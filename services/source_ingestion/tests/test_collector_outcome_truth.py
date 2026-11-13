"""Collector outcomes through the real provider, configured fetcher and scheduler."""

from dataclasses import replace
import json
from datetime import datetime, timezone

import pytest

from services.foundation import DeadLetterQueue
from services.source_ingestion import IngestManager
from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors.collector_bundle import (
    COLLECTOR_BUNDLE_ADAPTER_TOKEN, CollectorBundleAdapter,
)
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
    config = store.get_config("conn-truth")
    store.upsert_config(config.connector, {**config.fetch, "request": {"cursor": CURSOR}})
    result = run_scheduler(tmp_path, store)
    assert result.run.status.value == "completed"
    assert result.run.raw_count == result.run.normalized_count == 0
    assert result.watermark.value == CURSOR
