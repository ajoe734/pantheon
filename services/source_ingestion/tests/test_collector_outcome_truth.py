"""Collector outcomes through the real provider, configured fetcher and scheduler."""

import json
from datetime import datetime, timezone

import pytest

from services.foundation import DeadLetterQueue
from services.source_ingestion import IngestManager
from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors.collector_bundle import (
    COLLECTOR_BUNDLE_ADAPTER_TOKEN, CollectorBundleAdapter, HttpBundleTransport,
)
from services.source_ingestion.connectors import SourceEvidenceError
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter
from services.source_ingestion.scheduler import IngestionScheduler, JsonlIngestScheduleStore
from services.source_ingestion.tests.test_collector_bundle_connector import (
    FakeTransport, HOST, make_bundle,
)

SOURCE = "collector-tw-ptt-stock"
CURSOR = "2026-11-10T02:05:00Z#run1"


def setup_path(tmp_path, monkeypatch, *, status="ok", bundle_status="complete", cap=100):
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    files, entry, manifest = make_bundle(status=bundle_status)
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    files["status.json"] = json.dumps({"sources": {SOURCE: {
        "status": status, "last_run_at": "2026-11-10T02:05:00Z", "paused_reason": "login_required",
    }}}).encode()
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    # Freeze only the adapter health clock; scheduler timestamps remain real.
    import services.source_ingestion.connectors.collector_bundle as module
    real_observed = module.observed_from_collector_status
    monkeypatch.setattr(module, "observed_from_collector_status", lambda *a, **kw: real_observed(
        *a, **{**kw, "now": datetime(2026, 11, 10, 3, tzinfo=timezone.utc)}))
    adapter = CollectorBundleAdapter(connector_id="conn-truth", source_id=SOURCE,
                                     source_type="social", platform="ptt", host=HOST,
                                     raw_root=str(tmp_path / "raw"))
    store = JsonlConfiguredConnectorStore(tmp_path / "configured.jsonl")
    store.upsert_config(adapter.connector(), {
        "mode": "provider_owned_adapter", "adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN,
        "adapter_config": {"source_id": SOURCE, "source_type": "social", "platform": "ptt",
                           "host": HOST, "raw_root": str(tmp_path / "raw")},
        "max_records": cap,
    })
    return files, entry, manifest, store, adapter


def run_scheduler(tmp_path, store):
    # Reconstruct persisted stores and adapters each tick, as on worker restart.
    store = JsonlConfiguredConnectorStore(store.path)
    manager = IngestManager()
    manager.register_connector(store.get_config("conn-truth").connector)
    scheduler = IngestionScheduler(manager=manager,
        store=JsonlIngestScheduleStore(tmp_path / "schedule.jsonl"),
        dead_letter_queue=DeadLetterQueue(tmp_path / "dlq.jsonl"), max_attempts=1)
    return scheduler.run_once(connector_id="conn-truth", trace_id="trace-truth",
        fetch_batch=lambda watermark: ConfiguredConnectorFetcher(store).fetch_batch(
            "conn-truth", watermark, trace_id="trace-truth"))


def test_verified_ingest_then_consumed_cursor_has_zero_new_records(tmp_path, monkeypatch):
    _, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    first = run_scheduler(tmp_path, store)
    assert first.run.status.value == "completed"
    assert first.run.raw_count == first.run.normalized_count == 2
    assert first.watermark.value == CURSOR
    second = run_scheduler(tmp_path, store)
    assert second.run.status.value == "completed"
    assert second.run.raw_count == second.run.normalized_count == 0
    assert second.records == ()
    assert second.watermark.value == CURSOR
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["collector_outcome"]["no_new_data"] is True
    assert state["collector_outcome"]["observed"]["health_state"] == "healthy"


def test_paused_empty_does_not_complete_or_advance_watermark(tmp_path, monkeypatch):
    _, _, _, store, _ = setup_path(tmp_path, monkeypatch, status="paused", bundle_status="paused")
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "failed"
    assert result.run.raw_count == 0 and result.watermark is None
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["failed_attempts"] == 1
    assert "paused" in state["last_error"]
    assert state["collector_outcome"]["observed"]["health_state"] == "degraded"


def test_explicit_consumed_cursor_reaches_scheduler_as_proven_empty(tmp_path, monkeypatch):
    _, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    assert run_scheduler(tmp_path, store).run.raw_count == 2
    config = store.get_config("conn-truth")
    store.upsert_config(config.connector, {**config.fetch, "request": {"cursor": CURSOR}})
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "completed"
    assert result.run.raw_count == result.run.normalized_count == 0
    assert result.watermark.value == CURSOR


@pytest.mark.parametrize("status", ["paused", "unavailable", "failed", "failing", "mystery", ""])
def test_consumed_cursor_cannot_hide_unhealthy_source(tmp_path, monkeypatch, status):
    files, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    first = run_scheduler(tmp_path, store)
    assert first.run.raw_count == 2
    files["status.json"] = json.dumps({"sources": {SOURCE: {
        "status": status, "last_run_at": "2026-11-10T02:05:00Z", "paused_reason": "http_429",
    }}}).encode()
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "failed"
    assert result.records == () and result.run.raw_count == 0
    assert result.watermark.value == CURSOR
    assert len(result.dlq_entries) == 1
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["failed_attempts"] == 1 and state["successful_attempts"] == 1
    assert state["collector_outcome"]["no_new_data"] is False
    assert state["collector_outcome"]["observed"]["health_state"] == "degraded"
    assert "collector_source_" in state["last_error"]
    if status in {"paused", "unavailable", "failed", "failing"}:
        assert "http_429" in state["last_error"]


@pytest.mark.parametrize("change", ["no_bundle", "missing_status", "missing_source", "missing_last_run",
                                   "newer_run", "stale", "future_cursor", "partial_cursor", "unverified_cursor",
                                   "missing_raw_evidence", "mutated_boundary"])
def test_empty_without_matching_complete_evidence_is_failure(tmp_path, monkeypatch, change):
    files, _, manifest, store, _ = setup_path(tmp_path, monkeypatch)
    consumed = change not in {"no_bundle", "unverified_cursor"}
    if consumed:
        assert run_scheduler(tmp_path, store).run.raw_count == 2
    cursor = CURSOR
    status = {"sources": {SOURCE: {"status": "ok", "last_run_at": "2026-11-10T02:05:00Z"}}}
    if change == "no_bundle":
        files["bundles/index.json"] = b'{"bundles": []}'
        cursor = None
    elif change == "missing_status":
        files.pop("status.json")
    elif change == "missing_source":
        status = {"sources": {}}
    elif change == "missing_last_run":
        status["sources"][SOURCE].pop("last_run_at")
    elif change == "newer_run":
        status["sources"][SOURCE]["last_run_at"] = "2026-11-10T02:06:00Z"
    elif change == "stale":
        status["sources"][SOURCE]["last_run_at"] = "2026-11-01T02:05:00Z"
    elif change == "future_cursor":
        cursor = "2026-11-11T02:05:00Z#unknown"
    elif change == "partial_cursor":
        cursor += "|items=2"
    elif change == "missing_raw_evidence":
        next((tmp_path / "market_data_store" / "raw").rglob("*.html")).unlink()
    elif change == "mutated_boundary":
        manifest["collector_version"] = "changed-after-consumption"
        files[f"bundles/{SOURCE}/2026-11-10/run1/manifest.json"] = json.dumps(manifest).encode()
    if change != "missing_status":
        files["status.json"] = json.dumps(status).encode()
    config = store.get_config("conn-truth")
    # Even an explicit legacy allow_empty cannot override Collector evidence.
    store.upsert_config(config.connector, {**config.fetch, "request": {"cursor": cursor},
        "allow_empty": True, "empty_reason": "static config is not proof"})
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "failed"
    assert result.records == () and result.run.raw_count == 0
    assert (result.watermark.value if result.watermark else None) == (CURSOR if consumed else None)
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["failed_attempts"] == 1 and state["successful_attempts"] == int(consumed)
    assert state["collector_outcome"]["no_new_data"] is False
    assert state["collector_outcome"]["observed"]["health_state"] == "degraded"


@pytest.mark.parametrize("tamper", ["raw", "bundle", "malformed_status", "malformed_source",
                                   "contradictory_status", "contradictory_time", "auth"])
def test_verification_failure_keeps_last_watermark_and_clears_healthy_outcome(tmp_path, monkeypatch, tamper):
    files, entry, manifest, store, _ = setup_path(tmp_path, monkeypatch)
    assert run_scheduler(tmp_path, store).run.raw_count == 2
    assert run_scheduler(tmp_path, store).run.raw_count == 0
    if tamper == "raw":
        # A new bundle must verify its actual raw bytes before advancing.
        new, newer, _ = make_bundle(run_id="run2", finished="2026-11-10T03:00:00Z")
        files.update(new)
        files["bundles/index.json"] = json.dumps({"bundles": [entry, newer]}).encode()
        files[next(k for k in new if "/raw/" in k)] = b"tampered"
    elif tamper == "bundle":
        manifest["bundle_sha256"] = "0" * 64
    elif tamper == "malformed_status":
        files["status.json"] = b"{"
    elif tamper == "malformed_source":
        files["status.json"] = json.dumps({"sources": {SOURCE: ["ok"]}}).encode()
    elif tamper == "contradictory_status":
        files["bundles/index.json"] = json.dumps({"bundles": [{**entry, "status": "paused"}]}).encode()
    elif tamper == "contradictory_time":
        manifest["finished_at"] = "2026-11-10T02:06:00Z"
    elif tamper == "auth":
        def denied(self):
            raise SourceEvidenceError("collector authentication denied (fixture)")
        monkeypatch.setattr(CollectorBundleAdapter, "_transport", denied)
    files[f"bundles/{SOURCE}/2026-11-10/run1/manifest.json"] = json.dumps(manifest).encode()
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "failed" and result.run.raw_count == 0
    assert result.watermark.value == CURSOR and result.records == ()
    assert len(result.dlq_entries) == 1
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["last_error"] and state["failed_attempts"] == 1
    assert state["collector_outcome"]["no_new_data"] is False
    assert state["collector_outcome"]["observed"]["health_state"] == "degraded"


def test_capped_scheduler_resumes_across_restarts_then_reports_proven_empty(tmp_path, monkeypatch):
    _, _, _, store, _ = setup_path(tmp_path, monkeypatch, cap=1)
    first = run_scheduler(tmp_path, store)
    second = run_scheduler(tmp_path, store)
    third = run_scheduler(tmp_path, store)
    assert [r.run.raw_count for r in (first, second, third)] == [1, 1, 0]
    assert all(r.run.status.value == "completed" for r in (first, second, third))
    assert first.watermark.value == CURSOR + "|items=1"
    assert second.watermark.value == third.watermark.value == CURSOR
    assert first.records[0].source_id != second.records[0].source_id
    assert {r.metadata["event_time"] for r in (*first.records, *second.records)} == {"2026-11-10T01:00:00Z"}


@pytest.mark.parametrize("bundle_status", ["paused", "partial"])
def test_cursor_stops_at_unresolved_material_before_later_bundle(tmp_path, monkeypatch, bundle_status):
    files, entry, _, store, _ = setup_path(tmp_path, monkeypatch)
    bad, barrier, _ = make_bundle(run_id="barrier", finished="2026-11-10T03:00:00Z", status=bundle_status)
    later, newest, _ = make_bundle(run_id="later", finished="2026-11-10T04:00:00Z")
    files.update({**bad, **later})
    files["bundles/index.json"] = json.dumps({"bundles": [entry, barrier, newest]}).encode()
    first = run_scheduler(tmp_path, store)
    assert first.run.status.value == "completed"
    if bundle_status == "paused":
        assert first.run.raw_count == 2 and first.watermark.value == CURSOR
    else:
        assert first.run.raw_count == 4
        assert first.watermark.value == "2026-11-10T03:00:00Z#barrier|items=2"
    second = run_scheduler(tmp_path, store)
    assert second.run.status.value == "failed" and second.run.raw_count == 0
    assert second.watermark.value == first.watermark.value
    assert not any(":later:" in r.source_id for r in first.records)


def test_public_tuple_contract_and_idempotent_replay_unchanged(tmp_path, monkeypatch):
    _, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    config = store.get_config("conn-truth")
    first = execute_provider_owned_adapter(connector=config.connector, fetch=config.fetch, trace_id="t1")
    replay = execute_provider_owned_adapter(connector=config.connector, fetch=config.fetch, trace_id="t2")
    assert isinstance(first, tuple) and isinstance(replay, tuple)
    assert len(first) == len(replay) == 2
    assert [r.source_id for r in first] == [r.source_id for r in replay]
    assert all(r.metadata["pit"]["validated"] for r in replay)


@pytest.mark.parametrize("missing_token", [True, False])
def test_real_http_reader_denial_never_reuses_prior_no_new_data(tmp_path, monkeypatch, missing_token):
    from urllib.error import HTTPError
    import services.source_ingestion.connectors.collector_bundle as module

    _, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    assert run_scheduler(tmp_path, store).run.raw_count == 2
    assert run_scheduler(tmp_path, store).run.raw_count == 0
    calls = []

    def denied(request, **kwargs):
        calls.append(request.full_url)
        assert request.get_header("Authorization") == "Bearer fixture-only-token"
        raise HTTPError(request.full_url, 401, "fixture denial", {}, None)

    monkeypatch.setattr(module, "open_external_url", denied)
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: HttpBundleTransport(
        host=HOST, token_env="COLLECTOR_TEST_TOKEN"))
    if missing_token:
        monkeypatch.delenv("COLLECTOR_TEST_TOKEN", raising=False)
    else:
        monkeypatch.setenv("COLLECTOR_TEST_TOKEN", "fixture-only-token")
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "failed" and result.run.raw_count == 0
    assert result.watermark.value == CURSOR
    assert calls == ([] if missing_token else [f"https://{HOST}/bundles/index.json"])
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["collector_outcome"]["no_new_data"] is False
    assert state["collector_outcome"]["observed"]["health_state"] == "degraded"
