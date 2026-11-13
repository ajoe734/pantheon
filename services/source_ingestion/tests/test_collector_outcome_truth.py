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
        "status": status, "last_run_at": "2026-11-10T02:05:00Z", "last_run_id": "run1",
        "paused_reason": "login_required" if status == "paused" else None,
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


@pytest.mark.parametrize("status", ["paused", "partial", "pending", "unavailable", "failed", "failing", "mystery", ""])
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


@pytest.mark.parametrize("host_status", ["ok", "complete"])
@pytest.mark.parametrize("change", ["no_bundle", "missing_status", "missing_source", "missing_last_run",
                                   "newer_run", "stale", "future_cursor", "partial_cursor", "unverified_cursor",
                                   "missing_raw_evidence", "mutated_raw_evidence", "mutated_boundary",
                                   "missing_ack", "mutated_ack"])
def test_empty_without_matching_complete_evidence_is_failure(tmp_path, monkeypatch, change, host_status):
    files, _, manifest, store, _ = setup_path(tmp_path, monkeypatch, status=host_status)
    consumed = change not in {"no_bundle", "unverified_cursor"}
    if consumed:
        assert run_scheduler(tmp_path, store).run.raw_count == 2
    cursor = CURSOR
    status = {"sources": {SOURCE: {"status": host_status, "last_run_at": "2026-11-10T02:05:00Z", "last_run_id": "run1"}}}
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
    elif change == "mutated_raw_evidence":
        next((tmp_path / "market_data_store" / "raw").rglob("*.html")).write_bytes(b"<html>post X</html>")
    elif change == "missing_ack":
        next((tmp_path / "market_data_store" / "raw").rglob("persisted_boundary.json")).unlink()
    elif change == "mutated_ack":
        path = next((tmp_path / "market_data_store" / "raw").rglob("persisted_boundary.json"))
        path.write_text(json.dumps({**json.loads(path.read_text()), "cursor": CURSOR + "-other"}))
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


@pytest.mark.parametrize("host_status", ["ok", "complete"])
@pytest.mark.parametrize("tamper", ["raw", "bundle", "malformed_status", "malformed_source",
                                   "contradictory_status", "contradictory_time", "auth"])
def test_verification_failure_keeps_last_watermark_and_clears_healthy_outcome(tmp_path, monkeypatch, tamper, host_status):
    files, entry, manifest, store, _ = setup_path(tmp_path, monkeypatch, status=host_status)
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


def setup_governed_pipeline(tmp_path, monkeypatch, kind, *, cap=100, governed_route=False, producer=False):
    from services.source_ingestion.runtime import create_runtime

    source = {"macro": "collector-tw-macro-ndc", "news": "collector-news-rss"}.get(kind, SOURCE)
    dataset = {"macro": "tw_macro_series", "social": "tw_community_daily"}.get(kind, "collector_bundle_items")
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
    if producer:
        from deploy.collector.collector_stack.bundle import BundleWriter, CollectedItem
        from deploy.collector.collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config

        policy = load_collector_config(DEFAULT_POLICY_PATH, env={}).get_source(source)
        items = [CollectedItem(
            item_id=row["id"], url=row["url"], fetched_at=row["fetched_at"],
            published_at=row["published_at"], text=row["text"], lang=row["lang"],
            hashed_author=row["hashed_author"], metrics=row["metrics"],
            extra_fields=row, raw_bytes=files[f"{base}/" + manifest_item["raw_path"]],
            raw_extension="html",
        ) for row, manifest_item in zip(rows, json.loads(files[f"{base}/manifest.json"])["items"])]
        root = tmp_path / "producer"
        BundleWriter(root).write_bundle(policy=policy, run_id="run1", items=items,
            started_at="2026-11-10T02:00:00Z", finished_at="2026-11-10T02:05:00Z")
        files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        import services.source_ingestion.connectors.collector_bundle as module
        real_observed = module.observed_from_collector_status
        monkeypatch.setattr(module, "observed_from_collector_status", lambda *a, **kw: real_observed(
            *a, **{**kw, "now": datetime(2026, 11, 10, 3, tzinfo=timezone.utc)}))
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEARCH_INGEST_NOTIFY_URL", "")
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setattr(CollectorBundleAdapter, "_instrument_authority",
                        lambda self: {"2330": ("2330.TWSE",)})
    runtime = create_runtime(data_dir=tmp_path)
    adapter = CollectorBundleAdapter(connector_id="conn-features", source_id=source,
        source_type=kind, platform={"macro": "ndc", "social": "ptt", "news": "rss"}[kind], host=HOST,
        raw_root=str(tmp_path / "market_data_store"),
        dataset=dataset if governed_route else "collector_bundle_items", license_scope="collector_restricted")
    connector_data = adapter.connector().to_dict()
    connector_data["metadata"]["tenant_id"] = "tenant-a"
    from services.source_ingestion.connectors import SourceConnector
    connector = SourceConnector.from_dict(connector_data)
    runtime.manager.register_connector(connector)
    runtime.connector_store.upsert_config(connector, {
        "mode": "provider_owned_adapter", "adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN,
        "adapter_config": {"source_id": source, "source_type": kind,
            "platform": adapter.platform, "host": HOST, "raw_root": adapter.raw_root,
            "dataset": adapter.dataset, "license_scope": adapter.license_scope},
        "max_records": cap,
    })
    return runtime, connector, files


@pytest.mark.parametrize("kind", ["macro", "social", "news"])
def test_producer_complete_is_healthy_only_after_verified_delivery_and_committed_replay(tmp_path, monkeypatch, kind):
    from services.source_ingestion.api_models import RunScheduledRequest
    from services.source_ingestion.runtime import create_runtime

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind, producer=True)
    runtime.schedule_config_store.upsert_schedule(connector.connector_id, interval_seconds=60, enabled=True)
    request = RunScheduledRequest(exclusive_connector_ids=[connector.connector_id])
    first = runtime.pipeline.run_scheduled_connectors(request)
    assert not first["failed"], first
    assert first["ran"][0]["receipt"]["normalized_count"] == {"macro": 8, "social": 3, "news": 2}[kind]
    state = runtime.connector_store.get_fetch_state(connector.connector_id)["collector_outcome"]
    assert state["observed"]["health_state"] == "healthy"
    assert state["no_new_data"] is False
    runtime = create_runtime(data_dir=tmp_path)
    second = runtime.pipeline.run_scheduled_connectors(request)
    assert not second["failed"], second
    assert second["ran"][0]["receipt"]["raw_count"] == 0
    assert second["ran"][0]["receipt"]["normalized_count"] == 0
    state = runtime.connector_store.get_fetch_state(connector.connector_id)["collector_outcome"]
    assert state["no_new_data"] is True
    assert state["observed"]["health_state"] == "healthy"


@pytest.mark.parametrize("kind", ["macro", "social"])
@pytest.mark.parametrize("governed_route", [False, True])
def test_governed_features_persist_through_configured_pipeline(tmp_path, monkeypatch, kind, governed_route):
    """Regression for the retained NDC/PTT type failures, using local bundle fixtures.

    The read-only hosted proof records failed receipts, not raw provider payloads.
    Bundle bytes here are the existing local contract fixtures; no hosted data is
    fetched and no local result is claimed as hosted acceptance.
    """
    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind, governed_route=governed_route)
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
    assert all(r.metadata["license_scope"] == "collector_restricted" for r in result.records)
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

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind, cap=cap, producer=True)
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
        assert state["observed"]["health_state"] == ("healthy" if state["complete"] else "degraded")
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

    runtime, connector, _ = setup_governed_pipeline(tmp_path, monkeypatch, kind, cap=cap, producer=True)
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
    assert not list((tmp_path / "market_data_store" / "raw").rglob("persisted_boundary.json"))
    # Raw verification caches exist even after a failed persistence. A caller
    # supplying the full cursor cannot turn those uncommitted rows into empty.
    config = runtime.connector_store.get_config(connector.connector_id)
    runtime.connector_store.upsert_config(connector, {**config.fetch, "request": {"cursor": CURSOR}})
    with pytest.raises(SourceEvidenceError, match="not verified no-new-data"):
        runtime.pipeline.configured_fetch(connector.connector_id, trace_id="trace-uncommitted")(None)
    runtime.connector_store.upsert_config(connector, config.fetch)
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


def test_slow_reads_make_bounded_scheduler_progress_and_persist_partial_cursor(tmp_path, monkeypatch):
    """Delayed multi-item transport through the real configured fetcher and scheduler.

    Each raw read costs 20 fake seconds against the 15s whole-pull budget: the first scheduled
    run returns one verified item with a partial cursor (bounded, never claiming completion),
    the next resumes at the persisted cursor, and a final run proves no new data.
    """
    from types import SimpleNamespace

    import services.source_ingestion.connectors.collector_bundle as module

    files, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    reads: list[str] = []

    class Slow(FakeTransport):
        def read(self, relative_path, *, max_bytes):
            reads.append(relative_path)
            if "/raw/" in relative_path:
                clock[0] += 20.0
            return super().read(relative_path, max_bytes=max_bytes)

    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: Slow(files))
    first = run_scheduler(tmp_path, store)
    assert first.run.status.value == "completed"
    assert first.run.raw_count == 1
    assert first.watermark.value == CURSOR + "|items=1"
    assert len([r for r in reads if "/raw/" in r]) == 1
    second = run_scheduler(tmp_path, store)
    assert second.run.raw_count == 1 and second.watermark.value == CURSOR
    assert first.records[0].source_id != second.records[0].source_id
    third = run_scheduler(tmp_path, store)
    assert third.run.raw_count == 0 and third.watermark.value == CURSOR
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth")
    assert state["collector_outcome"]["no_new_data"] is True



def _trickle_env(tmp_path, monkeypatch, step):
    from types import SimpleNamespace

    import services.source_ingestion.connectors.collector_bundle as module
    from services.source_ingestion.tests.test_collector_bundle_connector import _trickle_response

    files, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setenv(module.COLLECTOR_BUNDLE_TOKEN_ENV, "fixture-only-token")
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: HttpBundleTransport(host=HOST))
    opened: list[str] = []

    def fake_open(request, *, caller, timeout, **_kw):
        rel = request.full_url.split(HOST + "/", 1)[1]
        opened.append(rel)
        response, _raw = _trickle_response(files[rel], clock, step[0] if "/raw/" in rel else 0.0, chunk=65536)
        return response

    monkeypatch.setattr(module, "open_external_url", fake_open)
    return store, clock, opened


def test_stable_slow_raw_reads_persist_verified_prefix_and_progress_every_run(tmp_path, monkeypatch):
    """Two raw bodies cost 8 fake seconds each under the 15s budget, unchanged on every run.

    The second body is cut by the shared deadline: only its never-accepted bytes are dropped, the
    verified first row persists with a partial cursor, and the next run (same latency) reads the
    remainder, completes and advances the exact cursor. Then nothing is new.
    """
    store, clock, opened = _trickle_env(tmp_path, monkeypatch, [8.0])
    start = clock[0]
    first = run_scheduler(tmp_path, store)
    assert first.run.status.value == "completed"
    assert first.run.raw_count == 1 and first.watermark is not None and first.watermark.value != CURSOR
    assert clock[0] - start <= 16.0
    assert len([r for r in opened if "/raw/" in r]) == 2  # second body started, then cut
    second = run_scheduler(tmp_path, store)
    assert second.run.status.value == "completed"
    assert second.run.raw_count == 1 and second.watermark.value == CURSOR
    ids = [r.source_id for r in first.records] + [r.source_id for r in second.records]
    assert len(ids) == 2 and len(set(ids)) == 2
    third = run_scheduler(tmp_path, store)
    assert third.run.raw_count == 0 and third.watermark.value == CURSOR


def test_single_raw_body_exceeding_budget_fails_truthfully_then_retry_persists(tmp_path, monkeypatch):
    """A first (or only) body cut by the deadline is a truthful failure: no rows, no watermark."""
    step = [16.0]
    store, clock, _opened = _trickle_env(tmp_path, monkeypatch, step)
    failed = run_scheduler(tmp_path, store)
    assert failed.run.status.value != "completed"
    assert failed.run.raw_count == 0 and failed.watermark is None
    state = JsonlConfiguredConnectorStore(store.path).get_fetch_state("conn-truth") or {}
    assert not state.get("collector_outcome", {}).get("complete")
    step[0] = 0.0
    retry = run_scheduler(tmp_path, store)
    assert retry.run.status.value == "completed"
    assert retry.run.raw_count == 2 and retry.watermark.value == CURSOR
    assert len({r.source_id for r in retry.records}) == len(retry.records)
    again = run_scheduler(tmp_path, store)
    assert again.run.raw_count == 0 and again.watermark.value == CURSOR


def test_verified_bundle_prefix_survives_next_bundle_deadline(tmp_path, monkeypatch):
    """Two singleton bundles, constant per-raw-read latency over the shared budget.

    Bundle 1 verifies and persists; bundle 2 is cut by the deadline at its first raw read
    and stays pending. Retries resume without duplicates; the backlog must progress.
    """
    from types import SimpleNamespace
    from services.source_ingestion.connectors import collector_bundle as cb
    from services.source_ingestion.tests.test_collector_bundle_connector import _trickle_response

    files, _, _, store, _ = setup_path(tmp_path, monkeypatch)
    entries = []
    for num in (1, 2):
        f, entry, manifest = make_bundle(run_id=f"run{num}", finished=f"2026-11-10T02:0{num + 4}:00Z")
        base = f"bundles/{SOURCE}/2026-11-10/run{num}"
        manifest["items"] = manifest["items"][:1]
        manifest["bundle_sha256"] = cb.compute_bundle_sha256([manifest["items"][0]["content_sha256"]])
        f[base + "/manifest.json"] = json.dumps(manifest).encode()
        f[base + "/items.jsonl"] = f[base + "/items.jsonl"].splitlines()[0]
        files.update(f)
        entries.append(entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {SOURCE: {
        "status": "ok", "last_run_at": "2026-11-10T02:06:00Z", "last_run_id": "run2"}}}).encode()
    clock = [0.0]
    monkeypatch.setattr(cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "fixture-only")
    monkeypatch.setattr(cb.CollectorBundleAdapter, "_transport", lambda self: cb.HttpBundleTransport(host=HOST))

    def fake_open(request, **kwargs):
        rel = request.full_url.split(HOST + "/", 1)[1]
        response, _ = _trickle_response(files[rel], clock, 8.0 if "/raw/" in rel else 0.0, chunk=65536)
        return response

    monkeypatch.setattr(cb, "open_external_url", fake_open)
    first = run_scheduler(tmp_path, store)
    assert first.run.status.value == "completed" and first.run.raw_count == 1
    assert first.watermark.value == "2026-11-10T02:05:00Z#run1"  # run2 stays pending
    second = run_scheduler(tmp_path, store)
    assert second.run.status.value == "completed" and second.run.raw_count == 1
    assert second.watermark.value == "2026-11-10T02:06:00Z#run2"
    assert len(first.records) == len(second.records) == 1  # one bundle per attempt, none replayed
    third = run_scheduler(tmp_path, store)
    assert third.run.raw_count == 0 and third.watermark.value == "2026-11-10T02:06:00Z#run2"
    assert third.run.status.value == "completed"


from types import SimpleNamespace

from services.source_ingestion.connectors import collector_bundle as _cb
from services.source_ingestion.tests.test_collector_bundle_connector import _day_bundle, _trickle_response


def test_priority_verified_prefix_survives_ordered_deadline(tmp_path, monkeypatch):
    source_id = "collector-tw-twse-price-history"
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(_cb.COLLECTOR_BUNDLE_TOKEN_ENV, "fixture-only")
    clock = [0.0]
    monkeypatch.setattr(_cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    files, entries, opened = {}, [], []
    for num in (1, 2):
        f, entry = _day_bundle(source_id, num, 1)
        files.update(f)
        entries.append(entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {source_id: {
        "status": "ok", "last_run_at": entries[-1]["finished_at"], "last_run_id": "run2"
    }}}).encode()
    real_observed = _cb.observed_from_collector_status
    monkeypatch.setattr(_cb, "observed_from_collector_status", lambda *a, **kw: real_observed(
        *a, **{**kw, "now": datetime(2026, 11, 10, 3, tzinfo=timezone.utc)}))
    monkeypatch.setattr(_cb.CollectorBundleAdapter, "_transport", lambda self: _cb.HttpBundleTransport(host=HOST))

    def fake_open(request, **kwargs):
        rel = request.full_url.split(HOST + "/", 1)[1]
        opened.append(rel)
        response, _ = _trickle_response(files[rel], clock, 8.0 if "/raw/" in rel else 0.0, chunk=65536)
        return response

    monkeypatch.setattr(_cb, "open_external_url", fake_open)
    adapter = _cb.CollectorBundleAdapter(connector_id="conn-truth", source_id=source_id,
        source_type="market", dataset="tw_price_daily", host=HOST, cadence_seconds=1800)
    store = JsonlConfiguredConnectorStore(tmp_path / "configured.jsonl")
    store.upsert_config(adapter.connector(), {
        "mode": "provider_owned_adapter", "adapter": _cb.COLLECTOR_BUNDLE_ADAPTER_TOKEN,
        "adapter_config": {"source_id": source_id, "source_type": "market", "host": HOST,
                           "dataset": "tw_price_daily", "cadence_seconds": 1800},
        "max_records": 500,
    })
    results = []
    for _ in range(3):
        result = run_scheduler(tmp_path, store)
        results.append((result.run.status.value, result.run.raw_count,
                        result.watermark.value if result.watermark else None))
    # constant-latency priority progress must be persisted, never discarded by the ordered sweep timeout
    assert [r[0] for r in results] == ["completed"] * 3, results
    assert results[0][1] == 1, results  # priority row persisted on first attempt
    assert [r[1] for r in results] == [1, 1, 1], results  # bounded progress per attempt; priority replay is idempotent
    assert results[-1][2] == "2026-11-10T02:02:00Z#run2", results
