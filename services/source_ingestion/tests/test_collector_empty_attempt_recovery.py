"""Real collector files: an empty failed poll must not strand later complete polls."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.connectors.collector_bundle import (
    CollectorBundleAdapter, LocalDirectoryBundleTransport, deferred_collector_acknowledgement,
)

CAPTURE = Path(__file__).with_name("fixtures") / "collector_intake_20261128.json"
THREADS = "collector-tw-threads"
PTT = "collector-tw-ptt-stock"


def captured(tmp_path, source):
    doc = json.loads(CAPTURE.read_text())
    root = tmp_path / "collector"
    for name, content in doc["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
        path.chmod(0o600 if path.name == "manifest.json" else 0o644)
    entries = sorted((e for e in doc["entries"] if e["source_id"] == source),
                     key=lambda e: (e["finished_at"], e["run_id"]))
    (root / "bundles/index.json").write_text(json.dumps({"bundles": entries[::-1]}))
    adapter = CollectorBundleAdapter(
        connector_id=source, source_id=source, source_type="social",
        platform="threads" if source == THREADS else "ptt", dataset="collector_social_posts",
        raw_root=str(tmp_path / "store"), transport=LocalDirectoryBundleTransport(root),
        read_budget_seconds=None,
    )
    return adapter, root, entries


def key(entry):
    return entry["finished_at"] + "#" + entry["run_id"]


@pytest.mark.parametrize("source", [THREADS, PTT])
def test_real_failed_attempts_do_not_strand_complete_polls(tmp_path, source):
    adapter, root, entries = captured(tmp_path, source)
    cursor = key(entries[0]) + "|items=0"  # exact persisted hosted shape
    result = adapter.pull(cursor=cursor)
    assert result.records
    assert result.complete and result.next_cursor == key(entries[-1])
    assert not result.no_new_data
    raw = [r for r in result.records if r.metadata.get("collector")]
    expected = [e["run_id"] for e in entries if e["status"] == "complete"]
    actual = list(dict.fromkeys(r.metadata["collector"]["run_id"] for r in raw))
    assert actual == expected
    resolved = result.observed["resolved_partial_bundles"]
    assert len(resolved) == sum(e["status"] == "partial" for e in entries)
    assert all(r["resolution"] == "recovered_empty_attempt:" + expected[0] for r in resolved)


@pytest.mark.parametrize("variant", ["no_recovery", "empty_recovery", "paused", "unknown_reason",
                                    "nonempty_failure", "other_source", "other_type"])
def test_recovery_is_narrow_and_still_fail_closed(tmp_path, variant):
    adapter, root, entries = captured(tmp_path, PTT)
    first = entries[0]
    manifest_path = root / "bundles" / PTT / first["date"] / first["run_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if variant == "no_recovery":
        entries = entries[:2]
    elif variant == "empty_recovery":
        for entry in entries[2:]:
            base = root / "bundles" / PTT / entry["date"] / entry["run_id"]
            m = json.loads((base / "manifest.json").read_text())
            m["items"] = []
            m["bundle_sha256"] = manifest["bundle_sha256"]
            entry["bundle_sha256"] = m["bundle_sha256"]
            (base / "manifest.json").write_text(json.dumps(m))
            (base / "items.jsonl").write_text("")
    elif variant == "paused":
        first["status"] = manifest["status"] = "paused"
    elif variant == "unknown_reason":
        manifest["reason"] = "operator_hold"
    elif variant == "nonempty_failure":
        complete = entries[2]
        first = complete
        manifest_path = root / "bundles" / PTT / first["date"] / first["run_id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        first["status"] = manifest["status"] = "partial"
        manifest["reason"] = "fetch_failed"
        entries = entries[2:]
    elif variant == "other_source":
        adapter = replace(adapter, source_id="collector-youtube-finance")
        for entry in entries:
            entry["source_id"] = adapter.source_id
        (root / "bundles" / PTT).rename(root / "bundles" / adapter.source_id)
        for path in (root / "bundles" / adapter.source_id).rglob("manifest.json"):
            m = json.loads(path.read_text()); m["source_id"] = adapter.source_id
            path.write_text(json.dumps(m))
        manifest["source_id"] = adapter.source_id
        manifest_path = root / "bundles" / adapter.source_id / first["date"] / first["run_id"] / "manifest.json"
    elif variant == "other_type":
        adapter = replace(adapter, source_type="news")
    manifest_path.write_text(json.dumps(manifest))
    (root / "bundles/index.json").write_text(json.dumps({"bundles": entries}))
    result = adapter.pull(cursor=key(first) + "|items=0")
    assert not result.complete and not result.no_new_data
    assert result.next_cursor.startswith(key(first) + "|items=")
    assert "resolved_partial_bundles" not in result.observed


@pytest.mark.parametrize("tamper", ["manifest", "raw"])
def test_corrupt_recovery_never_acknowledges_cursor(tmp_path, tamper):
    adapter, root, entries = captured(tmp_path, PTT)
    entry = entries[2]
    base = root / "bundles" / PTT / entry["date"] / entry["run_id"]
    manifest = json.loads((base / "manifest.json").read_text())
    if tamper == "manifest":
        manifest["bundle_sha256"] = "0" * 64
        (base / "manifest.json").write_text(json.dumps(manifest))
    else:
        (base / manifest["items"][0]["raw_path"]).write_bytes(b"corrupt")
    with deferred_collector_acknowledgement():
        with pytest.raises(SourceEvidenceError):
            adapter.pull(cursor=key(entries[0]) + "|items=0")
    assert not (tmp_path / "store" / "state" / PTT / "resume_cursor.json").exists()


def test_instagram_control_consumes_complete_files_with_same_modes(tmp_path):
    adapter, root, entries = captured(tmp_path, "collector-tw-instagram-popular")
    result = adapter.pull()
    assert len(result.records) >= 12 and result.complete
    assert result.next_cursor == key(entries[-1])
    assert "resolved_partial_bundles" not in result.observed


@pytest.mark.parametrize("source", [THREADS, PTT])
def test_configured_scheduler_recovers_and_resumes_capped_delivery(tmp_path, monkeypatch, source):
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore
    from services.source_ingestion.connectors.collector_bundle import COLLECTOR_BUNDLE_ADAPTER_TOKEN
    from services.source_ingestion.tests.test_collector_outcome_truth import run_scheduler

    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    adapter, root, entries = captured(tmp_path, source)
    adapter = replace(adapter, connector_id="conn-truth")
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: LocalDirectoryBundleTransport(root))
    store = JsonlConfiguredConnectorStore(tmp_path / "configured.jsonl")
    fetch = {
        "mode": "provider_owned_adapter", "adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN,
        "adapter_config": {"source_id": source, "source_type": "social", "platform": adapter.platform,
                           "raw_root": adapter.raw_root, "dataset": adapter.dataset},
        "request": {"cursor": key(entries[0]) + "|items=0"}, "max_records": 3,
    }
    store.upsert_config(adapter.connector(), fetch)
    seen = []
    for _ in range(30):
        result = run_scheduler(tmp_path, store)
        assert result.run.status.value == "completed", store.get_fetch_state("conn-truth")
        # The same unchanged post can legitimately appear in two complete polls;
        # each (bundle, item) must be delivered once across capped continuation.
        seen.extend((r.metadata["collector"]["run_id"], r.source_id)
                    for r in result.records if r.metadata.get("collector"))
        if result.watermark.value == key(entries[-1]):
            break
        store.upsert_config(adapter.connector(), {**fetch, "request": {}})
    else:
        pytest.fail("capped recovery did not reach the newest complete bundle")
    expected = sum(len(json.loads((root / "bundles" / source / e["date"] / e["run_id"] /
                                   "manifest.json").read_text())["items"])
                   for e in entries if e["status"] == "complete")
    assert len(seen) == len(set(seen)) == expected
    outcome = store.get_fetch_state("conn-truth")["collector_outcome"]
    store.upsert_config(adapter.connector(), {**fetch, "request": {}})
    empty = run_scheduler(tmp_path, store)
    assert empty.run.raw_count == 0 and empty.watermark.value == key(entries[-1])
    state = store.get_fetch_state("conn-truth")
    if source == PTT:
        # The captured PTT post contains a year misread by the existing social
        # symbol mapper as code 2026. Recovery must not bypass that separate
        # governed-feature refusal to manufacture a verified empty boundary.
        assert any("symbol '2026'" in r["reason"] for r in outcome["feature_rejected"])
        assert empty.run.status.value == "failed"
        assert state["collector_outcome"]["no_new_data"] is False
        assert "collector_consumed_boundary_unverified" in state["last_error"]
    else:
        assert empty.run.status.value == "completed"
        assert state["collector_outcome"]["no_new_data"] is True


@pytest.mark.parametrize("missing_proof", [None, "old_ingest", "zero_raw", "stale_cursor", "failed_latest"])
def test_hosted_probe_requires_post_switch_positive_ingest_and_current_cursor(tmp_path, missing_proof):
    from services.source_ingestion.tests.collector_intake_acceptance import probe, utc

    rows = []
    for source in (THREADS, PTT):
        payload = {"connector_id": source, "ingest_run_id": "ingest-" + source,
                   "started_at": "2026-11-28T03:00:00Z", "finished_at": "2026-11-28T03:00:10Z",
                   "status": "completed", "raw_count": 18,
                   "watermark": "2026-11-28T00:16:28Z#run-20261128T001628Z"}
        if source == THREADS:
            if missing_proof == "old_ingest":
                payload["started_at"] = "2026-11-27T03:00:00Z"
            elif missing_proof == "zero_raw":
                payload["raw_count"] = 0
            elif missing_proof == "stale_cursor":
                payload["watermark"] = "2026-11-11T04:49:23Z#run-20261111T044923Z|items=0"
        rows.append({"record_type": "ingest_receipt", "payload": payload})
        if source == THREADS and missing_proof == "failed_latest":
            rows.append({"record_type": "ingest_receipt", "payload": {
                **payload, "ingest_run_id": "later-failure", "status": "failed", "raw_count": 0}})
    journal = tmp_path / "ingest_schedule.jsonl"
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    before = journal.read_bytes()
    result = probe(tmp_path, utc("2026-11-28T02:00:00Z"), now=utc("2026-11-28T04:00:00Z"))
    assert result["passed"] is (missing_proof is None)
    assert journal.read_bytes() == before
