"""Real partitions, controller, PostgreSQL and rollback reader acceptance."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc
import types
import uuid

import pytest

from services.knowledge.evidence import EvidenceBundleBuilder, InMemoryEvidenceRepository
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter, CollectorDelivery
from services.source_ingestion.distillation_worker import DistillationJobQueue, make_distillation_worker
from services.source_ingestion.ingest_manager import IngestManager
from services.source_ingestion.market_data_storage import MarketDataStorageWriter
from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
from services.source_ingestion.pipeline import persist_source_evidence_refs
from services.source_ingestion.scheduler import ScheduledIngestResult
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport, _intake_bundle
from services.source_ingestion.tests.test_scheduled_connector import client, admission_stack  # noqa: F401

SPECS = [("collector-tw-twse-price-history", 1375, "tw_price_daily", "TWSE"),
         ("collector-tw-tpex-price-history", 6000, "tw_price_daily", "TPEX"),
         ("collector-tw-twse-t86", 1349, "tw_institutional_flow", "TWSE")]


def bundle(tmp_path, spec, *, count=None, day="2024-11-12", cap=None):
    source, size, dataset, exchange = spec
    count = size if count is None else count
    files, entry = _intake_bundle(source, count, dataset, exchange, day)
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    return CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market", dataset=dataset,
        max_records=cap or count, transport=FakeTransport(files), raw_root=str(tmp_path / "raw")), files, entry


def ingest(adapter, records, root, repo, queue, *, verified=True):
    manager = IngestManager()
    manager.register_connector(adapter.connector())
    run = manager.start_ingest_run(connector_id=adapter.connector_id, trace_id="bound-test", trigger_type="manual")
    records = tuple(replace(r, status="normalized") for r in records)
    result = ScheduledIngestResult(run=run, records=records, audit_actions=(), watermark=None,
                          collector_delivery=CollectorDelivery(records, lambda: None) if verified else None)
    refs = MarketDataStorageWriter(root).write_run(result=result, connector=adapter.connector()).to_dict()
    return persist_source_evidence_refs(manager, repo, EvidenceBundleBuilder(repo), queue, result, refs), refs


@pytest.mark.parametrize("spec", SPECS, ids=[s[0] for s in SPECS])
def test_real_size_day_has_constant_evidence_and_no_distillation(tmp_path, spec):
    measurements = []
    for size in (1, spec[1]):
        root = tmp_path / str(size)
        adapter, _, _ = bundle(root, spec, count=size)
        records = adapter.pull().records
        assert len(records) == size
        repo = InMemoryEvidenceRepository()
        queue = DistillationJobQueue(root / "queue.jsonl")
        refs, stored = ingest(adapter, records, root / "partitions", repo, queue)
        summary = repo.list_source_records()[0]
        assert summary.metadata["collector_market_bundle"]["row_count"] == size
        assert summary.metadata["pit"]["validated"]
        assert summary.metadata["trade_date"] == "2024-11-12"
        assert len(summary.metadata["normalized_partition_refs"]) == 1
        assert all(Path(r.metadata["raw_uri"]).is_relative_to(summary.metadata["raw_uri"]) for r in records)
        assert "normalized_row" not in summary.metadata and "symbol" not in summary.metadata
        assert sum(r["row_count"] for r in stored["normalized_refs"]) == size
        actual = sum(len(Path(r["uri"]).read_text().splitlines()) for r in stored["normalized_refs"])
        assert actual == size
        assert len(refs["source_ids"]) == len(refs["evidence_item_ids"]) == len(refs["knowledge_object_ids"]) == 1
        worker = make_distillation_worker(job_queue=queue, seed_store_path=root / "seeds.jsonl")
        caught = worker.catch_up(repo.list_source_records())
        assert caught.enqueued == 0 and queue.count() == 0
        # Explicit/manual admission is also safe: no second evidence/seed projection.
        worker.enqueue_from_source_record(summary)
        assert worker.run_pending().skipped == 1
        assert worker._seed_store.list_all() == []
        measurements.append((len(repo.list_source_records()), len(repo.list_evidence_items()),
                             len(repo.list_knowledge_objects())))
    assert measurements == [(1, 1, 1), (1, 1, 1)]


def test_capped_delivery_retry_and_tenant_bounds(tmp_path):
    adapter, _, _ = bundle(tmp_path, SPECS[0], count=7, cap=2)
    all_rows, cursor = [], None
    repo, queue = InMemoryEvidenceRepository(), DistillationJobQueue(tmp_path / "queue.jsonl")
    for expected in (2, 4, 6, 7):
        pulled = adapter.pull(cursor=cursor)
        all_rows.extend(pulled.records)
        ingest(adapter, pulled.records, tmp_path / "partitions", repo, queue)
        assert len(repo.list_source_records()) == len(repo.list_bundles()) == 1
        assert repo.list_source_records()[0].metadata["collector_market_bundle"]["row_count"] == expected
        adapter.save_resume_cursor(pulled)
        cursor = pulled.next_cursor
    ingest(adapter, all_rows, tmp_path / "partitions", repo, queue)
    assert len(repo.list_source_records()) == len(repo.list_knowledge_objects()) == 1
    assert repo.list_source_records()[0].metadata["collector_market_bundle"]["row_count"] == 7
    foreign = [replace(r, metadata={**r.metadata, "tenant_id": "foreign"}) for r in all_rows]
    ingest(adapter, foreign, tmp_path / "partitions", repo, queue)
    assert len(repo.list_source_records()) == 2
    assert len(repo.list_source_records(tenant_id="foreign")) == 1
    assert queue.count() == 0


def test_corrected_bundle_keeps_both_hashes_and_exact_rows(tmp_path):
    from services.source_ingestion.tests.test_collector_newest_session_progress import recorded_run
    repo, queue = InMemoryEvidenceRepository(), DistillationJobQueue(tmp_path / "queue.jsonl")
    for corrected in (False, True):
        files, entry = recorded_run(count=7, correction=corrected)
        files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
        adapter = CollectorBundleAdapter(connector_id=SPECS[0][0], source_id=SPECS[0][0], source_type="market",
            dataset="tw_price_daily", transport=FakeTransport(files), raw_root=str(tmp_path / str(corrected)), max_records=7)
        ingest(adapter, adapter.pull().records, tmp_path / "partitions", repo, queue)
    records = repo.list_source_records()
    assert len(records) == len(repo.list_knowledge_objects()) == len(repo.list_bundles()) == 2
    assert {r.metadata["collector_market_bundle"]["row_count"] for r in records} == {7}
    assert len({r.metadata["collector_market_bundle"]["rows_sha256"] for r in records}) == 2
    assert len({r.metadata["collector_market_bundle"]["bundle_sha256"] for r in records}) == 2


def test_completed_receipt_reuses_rows_without_per_row_evidence(admission_stack, monkeypatch):
    from services.source_ingestion import controller_worker as cw
    http, module, reads, wanted, files, transport, tick = admission_stack
    source = SPECS[0][0]
    files["status.json"] = json.dumps({"sources": {s: {"status": "ok", "offered": s == source,
        "last_run_at": "2026-11-10T02:05:00Z", "cadence_seconds": 86400} for s in wanted}}).encode()
    assert tick(cw.COLLECTOR_STEADY_PULL_MODE)["status"] == "ok"
    receipt = module.runtime.store.read_freshness_snapshot()["receipts"][-1]
    rows = [json.loads(line) for ref in receipt.storage_refs["normalized_refs"]
            for line in Path(ref["uri"]).read_text().splitlines()]
    records = tuple(SourceRecord.from_dict({**row, "title": "replay", "source_type": "market", "status": "normalized"})
                    for row in rows)
    assert all(module.runtime.evidence_repository.get_source_record(r.source_id, tenant_id=r.tenant_id) is None for r in records)
    from services.source_ingestion.tests.test_scheduled_connector import _read_headers
    headers = _read_headers(records[0].tenant_id)
    response = http.get("/api/source-ingest/source-records", headers=headers)
    assert response.status_code == 200
    summaries = response.json()["source_records"]
    assert len(summaries) == len({r.metadata["trade_date"] for r in records})
    assert sum(r["metadata"]["collector_market_bundle"]["row_count"] for r in summaries) == len(records)
    assert http.get("/api/source-ingest/management/sources", headers=headers).status_code == 200
    seeds = http.get("/api/source-ingest/management/strategy-seeds", headers=headers)
    assert seeds.status_code == 200 and seeds.json()["items"] == []
    run = module.runtime.manager.start_ingest_run(connector_id=source, trace_id="replay", trigger_type="manual")
    result = ScheduledIngestResult(run=run, records=records, watermark=None,
                                   collector_delivery=CollectorDelivery(records, lambda: None))
    reused = module.runtime.pipeline._persist_collector_market_storage(result)
    assert reused["summary"]["new_normalized_row_count"] == 0
    assert reused["summary"]["reused_record_count"] == len(records)
    # An unrelated tenant cannot reuse a partition merely because its item ids match.
    foreign = tuple(replace(r, metadata={**r.metadata, "tenant_id": "foreign"}) for r in records)
    result = replace(result, records=foreign, collector_delivery=CollectorDelivery(foreign, lambda: None))
    assert module.runtime.pipeline._persist_collector_market_storage(result)["summary"]["new_normalized_row_count"] == len(records)


def test_legacy_rows_and_bee_reader_accept_additive_bundle(tmp_path):
    adapter, _, _ = bundle(tmp_path, SPECS[0], count=3)
    rows = adapter.pull().records
    repo, queue = InMemoryEvidenceRepository(), DistillationJobQueue(tmp_path / "queue.jsonl")
    legacy = replace(rows[0], status="normalized")
    repo.add_source_record(legacy)
    ingest(adapter, rows, tmp_path / "partitions", repo, queue)
    assert repo.get_source_record(legacy.source_id).to_dict() == legacy.to_dict()
    old = subprocess.run(["git", "show", "bee62bc29:services/source_ingestion/connectors/base.py"],
                         check=True, capture_output=True).stdout
    name = "services.source_ingestion.connectors._bee_bundle_codec"
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(old, name, "exec"), module.__dict__)
        for record in repo.list_source_records():
            assert module.SourceRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
    finally:
        sys.modules.pop(name, None)


def test_bundle_provenance_does_not_reach_embedding_engine(tmp_path):
    from services.search.index_pipeline import IncrementalIndexPipeline, JsonlIndexPipelineStore
    from unittest.mock import Mock
    adapter, _, _ = bundle(tmp_path, SPECS[0], count=4)
    repo, queue = InMemoryEvidenceRepository(), DistillationJobQueue(tmp_path / "queue.jsonl")
    ingest(adapter, adapter.pull().records, tmp_path / "partitions", repo, queue)
    backend = Mock()
    pipeline = IncrementalIndexPipeline(repo, JsonlIndexPipelineStore(tmp_path / "index.jsonl"), retrieval_backend=backend)
    assert pipeline.run(force_full=True).indexed_count == 0
    assert pipeline.run().incremental_count == 0
    backend.index_documents.assert_not_called()
    assert len(repo.list_knowledge_objects()) == 1


@pytest.fixture
def pg_repo():
    dsn = os.getenv("SOURCE_INGEST_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set SOURCE_INGEST_TEST_POSTGRES_DSN for real PostgreSQL scale proof")
    schema = "collector_bound_" + uuid.uuid4().hex[:10]
    repo = PostgresSourceEvidenceRepository(dsn, table=schema + ".source_evidence")
    try:
        yield repo
    finally:
        repo.close()
        import psycopg
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_pg_legacy_records_and_bee_reader(pg_repo, tmp_path):
    adapter, _, _ = bundle(tmp_path, SPECS[0], count=3)
    rows = adapter.pull().records
    queue = DistillationJobQueue(tmp_path / "queue.jsonl")
    ingest(adapter, rows[:1], tmp_path / "legacy", pg_repo, queue, verified=False)
    with pg_repo._get_connection() as conn:
        before = {(kind, key): payload for kind, key, payload in conn.execute(
            f"SELECT record_type,record_id,payload FROM {pg_repo.table}")}
    ingest(adapter, rows, tmp_path / "bounded", pg_repo, queue)
    with pg_repo._get_connection() as conn:
        after = {(kind, key): payload for kind, key, payload in conn.execute(
            f"SELECT record_type,record_id,payload FROM {pg_repo.table}")}
    assert len(before) == 4 and all(after[key] == payload for key, payload in before.items())
    assert len(after) == 8 and queue.count() == 1  # only the pre-existing row job
    raw = subprocess.run(["git", "show", "bee62bc29:services/source_ingestion/pg_store.py"],
                         check=True, capture_output=True).stdout
    name = "services.source_ingestion._bee_pg_reader"
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(raw, name, "exec"), module.__dict__)
        reader = module.PostgresSourceEvidenceRepository(pg_repo.dsn, table=pg_repo.table.replace('"', ''), bootstrap=False)
        try:
            loaded = reader.list_source_records()
            assert len(loaded) == 2 and len(reader.list_knowledge_objects()) == 2
            summary = next(r for r in loaded if r.metadata.get("collector_market_bundle"))
            assert summary.metadata["collector_market_bundle"]["row_count"] == 3
        finally:
            reader.close()
    finally:
        sys.modules.pop(name, None)


def test_pg_two_year_reload_budget_with_existing_population(pg_repo, tmp_path):
    """Model >=22701 existing rows plus all 5856 new two-year provenance rows."""
    adapter, _, _ = bundle(tmp_path, SPECS[0], count=2)
    ingest(adapter, adapter.pull().records, tmp_path / "partitions", pg_repo,
           DistillationJobQueue(tmp_path / "queue.jsonl"))
    with pg_repo._get_connection() as conn:
        templates = dict(conn.execute(f"SELECT record_type,payload FROM {pg_repo.table}"))
        conn.execute(f"TRUNCATE {pg_repo.table}")
        # One atomic SQL batch per object type avoids test setup fsync costs.
        for kind, template in templates.items():
            values = []
            for index in range(5676 + 1464):
                key = f"existing-{index}" if index < 5676 else f"two-year-{index}"
                payload = json.loads(json.dumps(template))
                id_key = {"source_record": "source_id", "evidence_item": "evidence_item_id",
                          "evidence_bundle": "evidence_bundle_id", "knowledge_object": "knowledge_object_id"}[kind]
                payload[id_key] = key
                if kind in {"evidence_item", "knowledge_object"}:
                    payload["source_id"] = key
                if kind == "evidence_bundle":
                    payload.update(source_ids=[key], evidence_item_ids=[key])
                if kind == "knowledge_object":
                    payload.update(evidence_item_id=key, evidence_bundle_id=key)
                for field in ("source_dedupe_key", "evidence_dedupe_key", "evidence_owner_id", "source_owner_id"):
                    if field in payload.get("metadata", {}):
                        payload["metadata"][field] = key
                if index < 5676:
                    payload.get("metadata", {}).pop("collector_market_bundle", None)
                    # Incompressible deterministic padding makes this model
                    # larger than the observed 67-MB legacy table plus new rows.
                    payload["metadata"]["legacy_payload_padding"] = hashlib.shake_256(
                        f"{kind}:{index}".encode()).hexdigest(1536)
                values.append({"record_id": key, "record_type": kind, "payload": payload})
            conn.execute(f"INSERT INTO {pg_repo.table}(record_id,record_type,payload) "
                "SELECT record_id,record_type,payload FROM jsonb_to_recordset(%s::jsonb) "
                "AS records(record_id text, record_type text, payload jsonb)", (json.dumps(values),))
        size = conn.execute(f"SELECT pg_total_relation_size('{pg_repo.table}')").fetchone()[0]
    del values, templates
    tracemalloc.start()
    start = time.perf_counter(); pg_repo.reload()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert len(pg_repo.list_source_records()) == 7140
    assert len(pg_repo.list_knowledge_objects()) == 7140
    assert size >= 80 * 1024 * 1024
    assert elapsed < 120 and peak < 1024 * 1024 * 1024
    print("PROVENANCE_STARTUP " + json.dumps({"existing_rows": 22704, "two_year_new_rows": 5856,
        "total_rows": 28560, "postgres_bytes": size, "reload_seconds": elapsed,
        "reload_peak_bytes": peak, "budget_seconds": 120}))


def test_real_pg_30_day_controller_backfill(admission_stack, monkeypatch, pg_repo, tmp_path):
    """Opt-in full-size 261720-row integration; no fake storage/queue/repository."""
    from services.source_ingestion import controller_worker as cw
    from services.source_ingestion.tests.test_scheduled_connector import _read_headers
    http, module, reads, wanted, files, transport, tick = admission_stack
    monkeypatch.setattr(module.runtime, "evidence_repository", pg_repo)
    monkeypatch.setattr(module.runtime, "evidence_builder", EvidenceBundleBuilder(pg_repo))
    # Scale the provenance store with a valid two-close projection window. The
    # default 60-close, held-symbol and intraday readers run in separate suites;
    # normalized partitions below retain every row of all thirty sessions.
    monkeypatch.setattr(module.runtime.latest_market_snapshot_store, "max_closes", 2)
    # The controller still uses its real desired-state/reconcile/frontier/receipt path.
    enabled = {s[0] for s in SPECS}
    from services.source_ingestion import persona_source_reconciler as reconciler
    definition_for = reconciler.collector_definition
    def scale_definition(source):
        definition = definition_for(source)
        if source not in enabled or definition is None:
            return definition
        return replace(definition, default_limits={**definition.default_limits, "max_records": 1000},
            metadata={**definition.metadata, "collector": {
                **definition.metadata["collector"], "cadence_seconds": 86400}})
    monkeypatch.setattr(reconciler, "collector_definition", scale_definition)
    files.clear()
    entries = []
    for source, count, dataset, exchange in SPECS:
        for offset in range(30):
            day = (date(2024, 11, 12) + timedelta(days=offset)).isoformat()
            added, entry = _intake_bundle(source, count, dataset, exchange, day)
            files.update(added); entries.append(entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {s: {"status": "ok", "offered": s in enabled,
        "last_run_at": "2026-11-11T12:00:00Z", "cadence_seconds": 86400} for s in wanted}}).encode()
    assert tick(cw.RECONCILE_ONLY_MODE)["status"] == "ok"
    # A larger test batch keeps the 30-day proof bounded in wall time. Production
    # caps, cadences and request concurrency are untouched; cap=2 is tested above.
    started = time.perf_counter()
    for i in range(400):
        outcome = tick(cw.COLLECTOR_STEADY_PULL_MODE)
        assert outcome["status"] == "ok", outcome
        # Test-only lossless journal compaction: keep each complete, checksummed
        # latest envelope byte-for-byte (including its entire retained series).
        # This measures source_evidence growth rather than repeated replay of
        # obsolete snapshot envelopes. No production code or projection is mocked.
        journal = module.runtime.latest_market_snapshot_store.path
        if journal.exists():
            latest_lines = {}
            with journal.open() as handle:
                for line in handle:
                    state = json.loads(line)["state"]
                    latest_lines[(state["symbol"], state.get("dataset", "tw_price_daily"))] = line
            journal.write_text("".join(latest_lines.values()))
        records = pg_repo.list_source_records()
        if len(records) == 90 and sum(r.metadata["collector_market_bundle"]["row_count"] for r in records) == 30 * 8724:
            break
    else:
        pytest.fail("controller did not drain 30 days per source in 400 ticks")
    elapsed = time.perf_counter() - started
    with pg_repo._get_connection() as conn:
        counts = dict(conn.execute(f"SELECT record_type,count(*) FROM {pg_repo.table} GROUP BY record_type").fetchall())
        size = conn.execute(f"SELECT pg_total_relation_size('{pg_repo.table}')").fetchone()[0]
    assert counts == {"source_record": 90, "evidence_item": 90, "knowledge_object": 90, "evidence_bundle": 90}
    assert module.runtime.distillation_job_queue.count() == 0
    stored_rows = sum(len(p.read_text().splitlines()) for p in module.runtime.market_data_storage_writer.root.glob("normalized/**/*.jsonl"))
    assert stored_rows == 30 * 8724
    # API readers, connector-level freshness, held symbols and normalized snapshots.
    api = http.get("/api/source-ingest/source-records", headers=_read_headers(records[0].tenant_id))
    assert api.status_code == 200 and len(api.json()["source_records"]) == 90
    assert set(enabled).issubset(module.runtime._latest_source_record_by_connector())
    snapshots = module.runtime.latest_market_snapshot_store.reload()
    assert len(snapshots) >= 6000
    source = pg_repo.list_source_records()[0]
    # Model all 488 days with the exact persisted objects, not serialized-byte arithmetic.
    templates = {}
    with pg_repo._get_connection() as conn:
        for kind, payload in conn.execute(f"SELECT DISTINCT ON (record_type) record_type,payload FROM {pg_repo.table}"):
            templates[kind] = payload
        for day in range(30, 488):
            for source_index in range(3):
                suffix = f"scale-{day}-{source_index}"
                for kind in ("source_record", "evidence_item", "evidence_bundle", "knowledge_object"):
                    payload = json.loads(json.dumps(templates[kind]))
                    id_key = {"source_record": "source_id", "evidence_item": "evidence_item_id",
                              "evidence_bundle": "evidence_bundle_id", "knowledge_object": "knowledge_object_id"}[kind]
                    payload[id_key] = suffix
                    if kind in {"evidence_item", "knowledge_object"}:
                        payload["source_id"] = suffix
                    if kind == "evidence_bundle":
                        payload.update(source_ids=[suffix], evidence_item_ids=[suffix])
                    if kind == "knowledge_object":
                        payload.update(evidence_item_id=suffix, evidence_bundle_id=suffix)
                    # Scope dedupe metadata to keep the model's rows independent.
                    for key in ("source_dedupe_key", "evidence_dedupe_key", "evidence_owner_id", "source_owner_id"):
                        if key in payload.get("metadata", {}):
                            payload["metadata"][key] = suffix
                    pg_repo._upsert(kind, suffix, payload, tenant_id=source.tenant_id)
    tracemalloc.start()
    start = time.perf_counter()
    pg_repo.reload()
    reload_seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert len(pg_repo.list_source_records()) == 488 * 3
    assert reload_seconds < 120 and peak < 256 * 1024 * 1024
    report = {"days_per_source": 30, "normalized_rows": stored_rows, "controller_ticks": i + 1,
        "wall_seconds": elapsed, "postgres_counts": counts, "postgres_bytes": size,
        "two_year_bundles": 488 * 3, "two_year_normalized_rows": 488 * 8724,
        "two_year_reload_seconds": reload_seconds, "two_year_reload_peak_bytes": peak,
        "new_embedding_calls": 0, "distillation_jobs": 0,
        "snapshot_harness": "valid two-close projection window; lossless latest-envelope journal compaction between ticks; all normalized history retained"}
    print("PROVENANCE_SCALE " + json.dumps(report, sort_keys=True))
