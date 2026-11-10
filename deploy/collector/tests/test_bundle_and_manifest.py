"""Tests for manifest fields (SD §6.5), content/bundle hash tamper detection (AC3), pruning, and bundle server."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector_stack.bundle import (
    BundleIntegrityError,
    BundleWriter,
    CollectedItem,
    compute_bundle_sha256,
    prune_expired_bundles,
    recompute_bundle_hashes_from_disk,
    sha256_hex,
    verify_bundle_directory,
)
from collector_stack.bundle_server import ReadOnlyBundleServer
from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config


def test_manifest_writer_produces_section_6_5_fields_and_detects_changed_raw_file(
    tmp_path: Path,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    policy = config.get_source("collector-tw-macro-ndc")
    writer = BundleWriter(tmp_path, collector_version=config.collector_version)

    raw_a = b'{"date":"2026-09","monitoring_score":34}\n'
    raw_b = b'{"date":"2026-10","monitoring_score":32}\n'

    items = [
        CollectedItem(
            item_id="ndc-2026-09",
            url="https://ws.ndc.gov.tw/Download.ashx?icon=6099",
            fetched_at="2026-11-10T08:00:00Z",
            published_at="2026-10-28T08:00:00Z",
            text="NDC 2026-09 score 34",
            lang="zh-TW",
            hashed_author=None,
            metrics={"monitoring_score": 34},
            raw_bytes=raw_a,
            raw_extension="json",
            http_status=200,
        ),
        CollectedItem(
            item_id="ndc-2026-10",
            url="https://ws.ndc.gov.tw/Download.ashx?icon=6099",
            fetched_at="2026-11-10T08:00:00Z",
            published_at="2026-11-10T08:00:00Z",
            text="NDC 2026-10 score 32",
            lang="zh-TW",
            hashed_author=None,
            metrics={"monitoring_score": 32},
            raw_bytes=raw_b,
            raw_extension="json",
            http_status=200,
        ),
    ]

    written = writer.write_bundle(
        policy=policy,
        run_id="run-20261110-01",
        items=items,
        status="complete",
        started_at="2026-11-10T08:00:00Z",
        finished_at="2026-11-10T08:00:04Z",
    )

    manifest = verify_bundle_directory(written.bundle_dir)

    # Check every required field of Design §6.5
    assert manifest["schema_version"] == "collector_bundle.v1"
    assert manifest["source_id"] == "collector-tw-macro-ndc"
    assert manifest["run_id"] == "run-20261110-01"
    assert manifest["collector_version"] == "1.0.0"
    assert manifest["started_at"] == "2026-11-10T08:00:00Z"
    assert manifest["finished_at"] == "2026-11-10T08:00:04Z"
    assert manifest["fetch_policy_hash"] == policy.policy_hash
    assert manifest["robots_decision"] == "not_applicable_api"
    assert "https://data.gov.tw/license" in manifest["terms_ref"]
    assert "國家發展委員會" in manifest["attribution"]
    assert manifest["status"] == "complete"
    assert len(manifest["items"]) == 2

    orig_item_hash = manifest["items"][0]["content_sha256"]
    orig_bundle_hash = manifest["bundle_sha256"]
    assert orig_item_hash == sha256_hex(raw_a)
    assert orig_bundle_hash == compute_bundle_sha256([sha256_hex(raw_a), sha256_hex(raw_b)])

    # Mutate the first raw file on disk and prove both its content_sha256 and bundle_sha256 change
    raw_file_0 = written.bundle_dir / manifest["items"][0]["raw_path"]
    raw_file_0.write_bytes(b'{"date":"2026-09","monitoring_score":99}\n')

    recomputed_item_hashes, recomputed_bundle_hash = recompute_bundle_hashes_from_disk(
        written.bundle_dir
    )
    assert recomputed_item_hashes["ndc-2026-09"] != orig_item_hash
    assert recomputed_bundle_hash != orig_bundle_hash

    with pytest.raises(BundleIntegrityError, match="content_sha256 mismatch"):
        verify_bundle_directory(written.bundle_dir)


def test_bundle_pruner_and_read_only_bundle_server(tmp_path: Path) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    policy = config.get_source("collector-tw-macro-cbc")
    writer = BundleWriter(tmp_path)

    old_bundle = writer.write_bundle(
        policy=policy,
        run_id="run-old",
        items=[],
        started_at="2026-07-01T00:00:00Z",
        finished_at="2026-07-01T00:01:00Z",
        run_date="2026-07-01",
    )
    recent_bundle = writer.write_bundle(
        policy=policy,
        run_id="run-recent",
        items=[],
        started_at="2026-11-10T00:00:00Z",
        finished_at="2026-11-10T00:01:00Z",
        run_date="2026-11-10",
    )
    assert old_bundle.bundle_dir.exists()
    assert recent_bundle.bundle_dir.exists()

    removed = prune_expired_bundles(
        tmp_path,
        max_age_days=90,
        now=datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert len(removed) == 1
    assert not old_bundle.bundle_dir.exists()
    assert recent_bundle.bundle_dir.exists()

    token = "test-bearer-token-32-chars-minimum-length"
    server = ReadOnlyBundleServer(tmp_path, bearer_token=token)
    auth = {"Authorization": f"Bearer {token}"}

    # Unauthenticated or wrong token -> 401
    assert server.handle_request(method="GET", path="/status.json").status_code == 401
    assert (
        server.handle_request(
            method="GET", path="/status.json", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )

    # Write methods -> 405
    assert server.handle_request(method="POST", path="/status.json", headers=auth).status_code == 405
    assert (
        server.handle_request(method="DELETE", path="/bundles/index.json", headers=auth).status_code
        == 405
    )

    # Path traversal -> 403
    assert (
        server.handle_request(method="GET", path="/bundles/../../etc/passwd", headers=auth).status_code
        == 403
    )

    # Valid GET on /status.json and /bundles/index.json -> 200
    status_resp = server.handle_request(method="GET", path="/status.json", headers=auth)
    assert status_resp.status_code == 200
    assert json.loads(status_resp.body)["sources"]["collector-tw-macro-cbc"]["status"] == "complete"

    index_resp = server.handle_request(method="GET", path="/bundles/index.json", headers=auth)
    assert index_resp.status_code == 200
    assert len(json.loads(index_resp.body)["bundles"]) == 2
