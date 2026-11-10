"""Tests for automatic source pause on two consecutive HTTP 429, 403, or login redirects (AC5, SD §6.3)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from collector_stack.bundle import verify_bundle_directory
from collector_stack.config import (
    DEFAULT_POLICY_PATH,
    load_collector_config,
    validate_collector_config,
)
from collector_stack.fetchers import FetchResponse, FixtureTransport
from collector_stack.privacy import AuthorHasher
from collector_stack.runner import CollectorRunner, SourcePausedError


@pytest.mark.parametrize(
    ("first_resp", "second_resp", "expected_reason_substr"),
    [
        (
            FetchResponse(
                requested_url="https://www.threads.com/search?q=台股&serp_type=default",
                final_url="https://www.threads.com/search?q=台股&serp_type=default",
                http_status=429,
                body=b"Too Many Requests",
            ),
            FetchResponse(
                requested_url="https://www.threads.com/search?q=台股&serp_type=default",
                final_url="https://www.threads.com/search?q=台股&serp_type=default",
                http_status=429,
                body=b"Too Many Requests",
            ),
            "http_429",
        ),
        (
            FetchResponse(
                requested_url="https://www.threads.com/search?q=台股&serp_type=default",
                final_url="https://www.threads.com/search?q=台股&serp_type=default",
                http_status=403,
                body=b"Forbidden",
            ),
            FetchResponse(
                requested_url="https://www.threads.com/search?q=台股&serp_type=default",
                final_url="https://www.threads.com/search?q=台股&serp_type=default",
                http_status=403,
                body=b"Forbidden",
            ),
            "http_403",
        ),
        (
            FetchResponse(
                requested_url="https://www.threads.com/search?q=台股&serp_type=default",
                final_url="https://www.threads.com/accounts/login/?next=/search",
                http_status=200,
                body=b"<html>Login</html>",
            ),
            FetchResponse(
                requested_url="https://www.threads.com/search?q=台股&serp_type=default",
                final_url="https://www.threads.com/login",
                http_status=302,
                body=b"",
                headers={"location": "https://www.threads.com/login"},
            ),
            "login_redirect",
        ),
    ],
)
def test_two_consecutive_429_403_or_login_redirects_pause_source_and_write_paused_manifest(
    tmp_path: Path,
    test_hasher: AuthorHasher,
    first_resp: FetchResponse,
    second_resp: FetchResponse,
    expected_reason_substr: str,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    responses = [first_resp, second_resp]
    transport = FixtureTransport(
        {
            "https://www.threads.com/search": lambda _url: responses.pop(0),
        }
    )
    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        hasher=test_hasher,
        transport=transport,
    )

    # 1st block -> status is partial, consecutive_blocks=1, not paused yet
    outcome_1 = runner.run_source(
        "collector-tw-threads",
        run_id="run-block-1",
        started_at="2026-11-10T01:00:00Z",
        finished_at="2026-11-10T01:00:05Z",
    )
    assert outcome_1.status == "partial"
    assert outcome_1.state.paused is False
    assert outcome_1.state.consecutive_blocks == 1

    # 2nd consecutive block -> auto-paused, manifest has status="paused"
    outcome_2 = runner.run_source(
        "collector-tw-threads",
        run_id="run-block-2",
        started_at="2026-11-10T07:00:00Z",
        finished_at="2026-11-10T07:00:05Z",
    )
    assert outcome_2.status == "paused"
    assert outcome_2.state.paused is True
    assert outcome_2.state.consecutive_blocks == 2

    manifest = verify_bundle_directory(outcome_2.bundle.bundle_dir)
    assert manifest["status"] == "paused"
    assert expected_reason_substr in manifest["paused_reason"]
    assert manifest["items"] == []

    status_doc = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status_doc["sources"]["collector-tw-threads"]["status"] == "paused"
    assert expected_reason_substr in status_doc["sources"]["collector-tw-threads"]["paused_reason"]

    # 3rd run is refused while paused without making any request
    calls_before = len(transport.calls)
    with pytest.raises(SourcePausedError, match="auto-paused"):
        runner.run_source("collector-tw-threads", run_id="run-block-3")
    assert len(transport.calls) == calls_before


def test_single_block_followed_by_success_resets_counter_and_config_change_resumes_paused_source(
    tmp_path: Path,
    fixtures_dir: Path,
    test_hasher: AuthorHasher,
) -> None:
    raw = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    config = validate_collector_config(raw, env={})
    good_html = (fixtures_dir / "threads_search.html").read_bytes()

    url = "https://www.threads.com/search?q=台股&serp_type=default"
    seq = [
        FetchResponse(requested_url=url, final_url=url, http_status=429, body=b"rate limited"),
        FetchResponse(requested_url=url, final_url=url, http_status=200, body=good_html),
        FetchResponse(requested_url=url, final_url=url, http_status=403, body=b"forbidden 1"),
        FetchResponse(requested_url=url, final_url=url, http_status=429, body=b"rate limited 2"),
        FetchResponse(requested_url=url, final_url=url, http_status=200, body=good_html),
    ]
    transport = FixtureTransport({"https://www.threads.com/search": lambda _u: seq.pop(0)})
    runner = CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=transport)

    # 1 block then 1 success -> resets consecutive_blocks to 0
    r1 = runner.run_source("collector-tw-threads", run_id="r1")
    assert r1.state.consecutive_blocks == 1
    r2 = runner.run_source("collector-tw-threads", run_id="r2")
    assert r2.status == "complete"
    assert r2.state.consecutive_blocks == 0

    # Now 2 consecutive blocks (403 then 429) -> pauses
    runner.run_source("collector-tw-threads", run_id="r3")
    r4 = runner.run_source("collector-tw-threads", run_id="r4")
    assert r4.status == "paused"
    assert r4.state.paused is True

    # Operator updates fetch policy (e.g., increases min_interval_seconds) -> resumes source
    updated_raw = copy.deepcopy(raw)
    updated_raw["sources"]["collector-tw-threads"]["min_interval_seconds"] = 30.0
    updated_config = validate_collector_config(updated_raw, env={})
    resumed_runner = CollectorRunner(
        updated_config, root_dir=tmp_path, hasher=test_hasher, transport=transport
    )
    r5 = resumed_runner.run_source("collector-tw-threads", run_id="r5")
    assert r5.status == "complete"
    assert r5.state.paused is False
