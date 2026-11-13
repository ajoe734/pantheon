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


def setup_governed_pipeline(tmp_path, monkeypatch, kind, *, cap=100):
    from services.source_ingestion.runtime import create_runtime

    source = "collector-tw-macro-ndc" if kind == "macro" else SOURCE
    dataset = "tw_macro_series" if kind == "macro" else "tw_community_daily"
    files, entry, _ = make_bundle(source_id=source)
    base = f"bundles/{source}/2026-11-10/run1"
    rows = [json.loads(line) for line in files[f"{base}/items.jsonl"].splitlines()]
    for row, period in zip(rows, ("2026-09-30", "2026-08-31")):
        if kind == "macro":
            row.update(series_id="NDC:6099:MONITORING_SCORE", period_end=period,
                       value=28.0, metrics={"leading_index": 101.0, "coincident_index": 99.0})
        else:
            row["symbols"] = ["2330.TWSE"]
    files[f"{base}/items.jsonl"] = b"\n".join(json.dumps(row).encode() for row in rows)
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    files["status.json"] = json.dumps({"sources": {source: {
        "status": "ok", "last_run_at": "2026-11-10T02:05:00Z",
    }}}).encode()
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEARCH_INGEST_NOTIFY_URL", "")
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setattr(CollectorBundleAdapter, "_instrument_authority",
                        lambda self: {"2330": ("2330.TWSE",)})
    runtime = create_runtime(data_dir=tmp_path)
    adapter = CollectorBundleAdapter(connector_id="conn-features", source_id=source,
        source_type=kind, platform="ndc" if kind == "macro" else "ptt", host=HOST,
        raw_root=str(tmp_path / "market_data_store"))
    connector_data = adapter.connector().to_dict()
    connector_data["metadata"]["tenant_id"] = "tenant-a"
    from services.source_ingestion.connectors import SourceConnector
    connector = SourceConnector.from_dict(connector_data)
    runtime.manager.register_connector(connector)
    runtime.connector_store.upsert_config(connector, {
        "mode": "provider_owned_adapter", "adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN,
        "adapter_config": {"source_id": source, "source_type": kind,
            "platform": adapter.platform, "host": HOST, "raw_root": adapter.raw_root},
        "max_records": cap,
    })
    return runtime, connector, files


@pytest.mark.parametrize("kind", ["macro", "social"])
def test_governed_features_persist_through_configured_pipeline(tmp_path, monkeypatch, kind):
    """Regression for the retained NDC/PTT type failures, using local bundle fixtures.

    The read-only hosted proof records failed receipts, not raw provider payloads.
    Bundle bytes here are the existing local contract fixtures; no hosted data is
    fetched and no local result is claimed as hosted acceptance.
    """
    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind)
    dataset = "tw_macro_series" if kind == "macro" else "tw_community_daily"
    result, refs, _ = runtime.pipeline.run_job(connector=connector, trace_id="trace-features",
        trigger_type="scheduled", fetch_batch=runtime.pipeline.configured_fetch(
            connector.connector_id, trace_id="trace-features"))
    assert result.run.status.value == "completed", [event.message for event in result.run.events]
    raw = [r for r in result.records if r.source_type.value == kind]
    features = [r for r in result.records if r.source_type.value == "market"]
    assert len(raw) == 2
    assert len(features) == (6 if kind == "macro" else 1)
    assert all(r.metadata["dataset"] == "collector_bundle_items" for r in raw)
    assert all(r.metadata["dataset"] == dataset for r in features)
    assert {r.metadata["dataset"] for r in result.records} == {
        ref["dataset"] for ref in refs["storage_refs"]["normalized_refs"]}
    receipt = runtime.store.get_receipt(result.run.ingest_run_id)
    assert receipt.status == "completed"
    assert receipt.normalized_count == len(result.records)
    assert set(refs["source_ids"]) == {r.source_id for r in result.records}
    assert result.watermark.value == CURSOR


@pytest.mark.parametrize("kind", ["macro", "social"])
@pytest.mark.parametrize("cap", [1, 2])
def test_actual_scheduled_frontier_persists_capped_features_and_restarts(tmp_path, monkeypatch, kind, cap):
    from services.source_ingestion.runtime import create_runtime
    from services.source_ingestion.api_models import RunScheduledRequest

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind, cap=cap)
    runtime.schedule_config_store.upsert_schedule(connector.connector_id, interval_seconds=60, enabled=True)
    raw_ids, revisions, community_volumes = set(), set(), []
    complete = False
    for _ in range(12):
        # A new runtime replays the same stores on every natural scheduled entry.
        runtime = create_runtime(data_dir=tmp_path)
        payload = runtime.pipeline.run_scheduled_connectors(RunScheduledRequest(
            exclusive_connector_ids=[connector.connector_id]))
        assert not payload["failed"], payload
        assert len(payload["ran"]) == 1
        item = payload["ran"][0]
        receipt = item["receipt"]
        assert receipt["status"] == "completed"
        assert receipt["raw_count"] <= cap
        for ref in receipt["storage_refs"]["normalized_refs"]:
            from pathlib import Path
            rows = [json.loads(line) for line in Path(ref["uri"]).read_text().splitlines()]
            for row in rows:
                if row["dataset"] == "collector_bundle_items":
                    raw_ids.add(row["source_id"])
                else:
                    meta = row["metadata"]
                    nrow = meta["normalized_row"]
                    revision = (meta["symbol"], meta["event_time"], meta["available_time"], nrow.get("field"))
                    assert revision not in revisions
                    revisions.add(revision)
                    if kind == "social":
                        community_volumes.append(nrow["post_volume"])
                    assert meta["access_scope"] == ["research"]
                    assert meta["redistribution_allowed"] is False
                    assert meta["tenant_id"] == "tenant-a"
                    assert meta["content_hash"].startswith("sha256:")
                    provenance = [nrow] if kind == "macro" else nrow["source_records"]
                    import hashlib
                    for origin in provenance:
                        raw_bytes = (tmp_path / "market_data_store" / origin["raw_uri"]).read_bytes()
                        assert origin["content_hash"] == "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        state = runtime.connector_store.get_fetch_state(connector.connector_id)["collector_outcome"]
        if state["complete"]:
            complete = True
            break
    assert complete
    assert len(raw_ids) == 2
    assert len(revisions) == (6 if kind == "macro" else 2)
    if kind == "social":
        assert max(community_volumes) == 2.0
    # A consumed boundary produces no synthetic rows after a restart.
    from services.source_ingestion.connectors import collector_bundle as module
    real_observed = module.observed_from_collector_status
    monkeypatch.setattr(module, "observed_from_collector_status", lambda *a, **kw: real_observed(
        *a, **{**kw, "now": datetime(2026, 11, 10, 3, tzinfo=timezone.utc)}))
    runtime = create_runtime(data_dir=tmp_path)
    empty = runtime.pipeline.run_scheduled_connectors(RunScheduledRequest(
        exclusive_connector_ids=[connector.connector_id]))
    assert not empty["failed"], empty
    assert empty["ran"][0]["receipt"]["normalized_count"] == 0


@pytest.mark.parametrize("kind", ["macro", "social"])
@pytest.mark.parametrize("stage", ["market_storage", "source_evidence", "receipt_finalize"])
@pytest.mark.parametrize("cap", [2, 100])
def test_failed_persistence_never_acknowledges_features_or_cursor(tmp_path, monkeypatch, kind, stage, cap):
    import services.source_ingestion.pipeline as module
    from services.source_ingestion.runtime import create_runtime

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind, cap=cap)
    function = {"market_storage": "persist_market_data_storage_refs",
                "source_evidence": "persist_source_evidence_refs",
                "receipt_finalize": "persist_ingest_receipt"}[stage]
    original = getattr(module, function)

    def fail(*args, **kwargs):
        if stage != "receipt_finalize" or kwargs.get("status") is None:
            raise OSError("fixture persistence failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, function, fail)
    with pytest.raises(OSError, match="fixture persistence failure"):
        runtime.pipeline.run_job(connector=connector, trace_id="trace-failed", trigger_type="scheduled",
            fetch_batch=runtime.pipeline.configured_fetch(connector.connector_id, trace_id="trace-failed"))
    assert runtime.store.get_watermark(connector.connector_id) is None
    assert runtime.store.list_receipts()[-1].status == "failed"
    assert not list((tmp_path / "market_data_store" / "state").rglob("*emitted*"))
    assert not list((tmp_path / "market_data_store" / "state").rglob("*ledger*"))
    assert not list((tmp_path / "market_data_store" / "state").rglob("resume_cursor.json"))
    monkeypatch.setattr(module, function, original)
    runtime = create_runtime(data_dir=tmp_path)
    runtime._register_or_validate_connector(connector)
    result, refs, _ = runtime.pipeline.run_job(connector=connector, trace_id="trace-retry", trigger_type="scheduled",
        fetch_batch=runtime.pipeline.configured_fetch(connector.connector_id, trace_id="trace-retry"))
    assert result.run.status.value == "completed"
    assert any(r.source_type.value == "market" for r in result.records)
    assert refs["storage_refs"]["feature_refs"]
    assert runtime.store.get_receipt(result.run.ingest_run_id).status == "completed"


@pytest.mark.parametrize("target,change", [
    ("raw", "connector"), ("raw", "type"),
    ("derived", "connector"), ("derived", "type"), ("derived", "hash"),
    ("derived", "PIT"), ("derived", "schema"), ("derived", "scope"),
    ("derived", "forged"), ("derived", "coverage"), ("derived", "nested"),
])
def test_scheduled_handoff_rejects_unverified_or_mutated_records(tmp_path, monkeypatch, target, change):
    from dataclasses import replace

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, "macro")
    fetch = runtime.pipeline.configured_fetch(connector.connector_id, trace_id="trace-invalid")

    def tampered(watermark):
        batch = fetch(watermark)
        if change == "forged":
            return replace(batch, collector_delivery=None)
        if change == "coverage":
            return replace(batch, derived_records=batch.derived_records[:-1])
        field = "records" if target == "raw" else "derived_records"
        records = list(getattr(batch, field))
        if change == "nested":
            records[0].metadata["normalized_row"]["available_time"] = "1900-01-01T00:00:00Z"
            return batch
        record = records[0]
        if change == "connector":
            record = replace(record, connector_id="another-connector")
        elif change == "type":
            record = replace(record, source_type="news")
        else:
            key, value = {"hash": ("content_hash", "sha256:" + "0" * 64),
                          "PIT": ("available_time", "1900-01-01T00:00:00Z"),
                          "schema": ("normalized_row", {"dataset": "tw_macro_series"}),
                          "scope": ("access_scope", ["public"])}[change]
            record = replace(record, metadata={**record.metadata, key: value})
        records[0] = record
        return replace(batch, **{field: tuple(records)})

    result, _, _ = runtime.pipeline.run_job(connector=connector, trace_id="trace-invalid",
        trigger_type="scheduled", fetch_batch=tampered)
    assert result.run.status.value == "failed"
    assert runtime.store.get_watermark(connector.connector_id) is None
    assert runtime.store.get_receipt(result.run.ingest_run_id).status == "failed"
    assert not list((tmp_path / "market_data_store" / "state").rglob("*emitted*"))
    assert not list((tmp_path / "market_data_store" / "normalized").rglob("*.jsonl"))


@pytest.mark.parametrize("change", ["connector", "type", "derived"])
def test_ordinary_batches_cannot_use_collector_derived_exception(tmp_path, monkeypatch, change):
    from dataclasses import replace
    from services.source_ingestion.scheduler import IngestBatch

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, "macro")
    batch = runtime.pipeline.configured_fetch(connector.connector_id, trace_id="trace-ordinary")(None)
    raw = batch.records[0]
    if change == "connector":
        ordinary = IngestBatch(records=(replace(raw, connector_id="forged"),))
    elif change == "type":
        ordinary = IngestBatch(records=(replace(raw, source_type="market"),))
    else:
        ordinary = IngestBatch(records=batch.records, derived_records=batch.derived_records,
                              metadata={"fetch_mode": "provider_owned_adapter", "collector_outcome": {"complete": True}})
    result, _, _ = runtime.pipeline.run_job(connector=connector, trace_id="trace-ordinary",
        trigger_type="scheduled", fetch_batch=lambda watermark: ordinary)
    assert result.run.status.value == "failed"
    expected = {"connector": "connector_id must match", "type": "source_type must match",
                "derived": "require a verified Collector delivery"}[change]
    assert any(expected in (event.message or "") for event in result.run.events)
    assert runtime.store.get_watermark(connector.connector_id) is None


def test_capped_derived_rows_never_consume_unverified_raw_hashes(tmp_path, monkeypatch):
    from services.source_ingestion.api_models import RunScheduledRequest

    runtime, connector, files = setup_governed_pipeline(tmp_path, monkeypatch, "macro", cap=1)
    raw_paths = sorted(path for path in files if "/raw/" in path)
    # Identify the second item in maintained bundle order, not hash filename order.
    manifest_path = next(path for path in files if path.endswith("/manifest.json"))
    manifest = json.loads(files[manifest_path])
    base = manifest_path.rsplit("/", 1)[0]
    bad_path = base + "/" + manifest["items"][1]["raw_path"]
    assert bad_path in raw_paths
    files[bad_path] = b"corrupted fixture raw bytes"
    runtime.schedule_config_store.upsert_schedule(connector.connector_id, interval_seconds=60, enabled=True)
    request = RunScheduledRequest(exclusive_connector_ids=[connector.connector_id])
    # First verified raw item yields its three fields, followed by its raw record.
    for _ in range(4):
        payload = runtime.pipeline.run_scheduled_connectors(request)
        assert not payload["failed"], payload
    previous = runtime.store.get_watermark(connector.connector_id)
    assert previous.value == CURSOR + "|items=1"
    payload = runtime.pipeline.run_scheduled_connectors(request)
    assert payload["failed"] and not payload["ran"]
    assert "raw hash mismatch" in payload["failed"][0]["error"]
    assert runtime.store.get_watermark(connector.connector_id) == previous


@pytest.mark.parametrize("kind", ["macro", "social"])
def test_failed_legacy_pull_markers_and_cursor_cannot_acknowledge_unstored_features(tmp_path, monkeypatch, kind):
    from dataclasses import replace

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind)
    config = runtime.connector_store.get_config(connector.connector_id)
    # This is exactly the old eager provider contract: it can leave markers and
    # a resume file even though the scheduler/persistence owner never accepted it.
    legacy = execute_provider_owned_adapter(connector=connector, fetch=config.fetch,
        trace_id="trace-legacy", preserve_collector_outcome=True)
    assert any(r.source_type.value == "market" for r in legacy.records)
    adapter = CollectorBundleAdapter(connector_id=connector.connector_id,
        source_id=config.fetch["adapter_config"]["source_id"], source_type=kind)
    adapter.save_resume_cursor(replace(legacy, complete=False))
    assert adapter.load_resume_cursor() == CURSOR
    assert runtime.store.get_watermark(connector.connector_id) is None
    result, refs, _ = runtime.pipeline.run_job(connector=connector, trace_id="trace-recovered",
        trigger_type="scheduled", fetch_batch=runtime.pipeline.configured_fetch(
            connector.connector_id, trace_id="trace-recovered"))
    assert result.run.status.value == "completed"
    assert len([r for r in result.records if r.source_type.value == kind]) == 2
    assert len([r for r in result.records if r.source_type.value == "market"]) == (6 if kind == "macro" else 1)
    assert refs["storage_refs"]["feature_refs"]
    assert runtime.store.get_watermark(connector.connector_id).value == CURSOR
