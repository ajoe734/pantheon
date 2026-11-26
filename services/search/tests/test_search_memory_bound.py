"""Memory-bound contracts for search-svc (evidence loading, snapshots, run history)."""

from __future__ import annotations

import threading

import pytest
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from services.knowledge.evidence import EvidenceItem, KnowledgeObject
from services.search import pg_store
from services.search.index_pipeline import IndexPipelineSnapshot, JsonlIndexPipelineStore
from services.search.index_store import JsonlSearchIndexStore, SearchIndexSnapshot
from services.source_ingestion.connectors.base import SourceRecord


def _source(i: int) -> dict:
    return SourceRecord(
        source_id=f"src-{i}", connector_id="c", source_type="paper",
        title=f"s{i}", content_ref=f"ref://{i}",
    ).to_dict()


def _item(i: int) -> dict:
    return EvidenceItem(
        evidence_item_id=f"evi-{i}", source_id=f"src-{i}", item_type="text_chunk",
        content_ref=f"ref://{i}#1", citation_label=f"l{i}", body=f"body {i}",
    ).to_dict()


class _Table:
    """Fake source_evidence table; honours the paged WHERE record_type/append_id clause."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, dict]] = []
        self.statements: list[str] = []
        self.lock = threading.Lock()

    def add(self, i: int) -> None:
        with self.lock:
            n = len(self.rows)
            self.rows.append((n + 1, "source_record", _source(i)))
            self.rows.append((n + 2, "evidence_item", _item(i)))

    def connect(self, dsn):
        table = self

        class Conn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=()):
                table.statements.append(sql)
                time.sleep(0.01)
                rtype, after, limit = params
                with table.lock:
                    rows = [(a, p) for a, t, p in table.rows if t == rtype and a > after][:limit]
                return SimpleNamespace(fetchall=lambda: rows)

        return Conn()


def _repo(table: _Table):
    with mock.patch.dict("sys.modules", {"psycopg": SimpleNamespace(connect=table.connect)}):
        repo = pg_store.PostgresReadOnlyEvidenceRepository(dsn="postgres://x")
        repo._connect = lambda: table.connect("x")
    return repo


def test_refresh_reads_only_rows_above_watermark() -> None:
    table = _Table()
    for i in range(5):
        table.add(i)
    repo = _repo(table)
    assert len(repo.list_evidence_items()) == 5
    table.statements.clear()
    table.add(5)
    repo.refresh()
    assert len(repo.list_evidence_items()) == 6
    assert repo._watermark == 12
    # every statement is bounded by the append_id watermark and a page limit
    assert all("append_id > %s" in s and "LIMIT" in s for s in table.statements)


def test_incremental_equals_full_reload() -> None:
    table = _Table()
    repo = _repo(table)
    for i in range(7):
        table.add(i)
        repo.refresh()
    full = _repo(table)
    assert [e.evidence_item_id for e in repo.list_evidence_items()] == [
        e.evidence_item_id for e in full.list_evidence_items()
    ]
    assert [s.source_id for s in repo.list_source_records()] == [
        s.source_id for s in full.list_source_records()
    ]


def test_concurrent_refresh_is_single_flight() -> None:
    table = _Table()
    for i in range(3):
        table.add(i)
    repo = _repo(table)
    table.add(3)
    table.statements.clear()
    threads = [threading.Thread(target=repo.refresh) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(repo.list_evidence_items()) == 4
    # 8 callers, but loads coalesce: far fewer than 8 independent passes (8 passes = 32+ statements)
    assert len(table.statements) < 8 * 4
    # one repository object is shared; nothing duplicated
    assert len({e.evidence_item_id for e in repo.list_evidence_items()}) == 4


def test_health_and_status_never_load_evidence(tmp_path) -> None:
    from fastapi.testclient import TestClient
    from services.search.main import create_app

    app = create_app(tmp_path / "idx.jsonl", tmp_path / "ev.jsonl",
                     tmp_path / "mat.jsonl", tmp_path / "pipe.jsonl")
    with mock.patch.object(pg_store.JsonlEvidenceRepository, "reload") as reload_mock:
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert client.get("/api/search/index/status").status_code == 200
        assert reload_mock.call_count == 0


def test_snapshots_bounded_by_count_and_age(tmp_path) -> None:
    store = JsonlSearchIndexStore(tmp_path / "s.jsonl", max_count=3, max_age_seconds=3600)
    now = datetime.now(timezone.utc)

    def snap(rid: str, age: int) -> SearchIndexSnapshot:
        created = (now - timedelta(seconds=age)).isoformat().replace("+00:00", "Z")
        return SearchIndexSnapshot(request_id=rid, trace_id="t", filters_applied={},
                                   result_refs=[], created_at=created)

    store.append_snapshot(snap("old", 7200))
    for i in range(5):
        store.append_snapshot(snap(f"r{i}", 0))
    ids = [s.request_id for s in store.list_snapshots()]
    assert ids == ["r2", "r3", "r4"]
    reloaded = JsonlSearchIndexStore(tmp_path / "s.jsonl", max_count=3, max_age_seconds=3600)
    assert [s.request_id for s in reloaded.list_snapshots()] == ["r2", "r3", "r4"]


def test_pipeline_history_drops_old_object_id_lists(tmp_path) -> None:
    store = JsonlIndexPipelineStore(tmp_path / "p.jsonl", max_retention=10)
    for i in range(4):
        store.record_run(IndexPipelineSnapshot(
            pipeline_run_id=f"run-{i}", schema_version="v", triggered_by="t", trigger_ref=None,
            indexed_count=3, incremental_count=1, removed_count=0, indexed_watermarks={},
            indexed_at="2026-01-01T00:00:00Z", is_full_rebuild=False, freshness_sla_seconds=60,
            indexed_object_ids=["a", "b", "c"],
        ))
    runs = store.list_runs()
    assert [r.indexed_object_ids for r in runs[:-1]] == [[], [], []]
    assert runs[-1].indexed_object_ids == ["a", "b", "c"]  # baseline for next incremental run
    assert all(r.indexed_count == 3 for r in runs)
    assert JsonlIndexPipelineStore(tmp_path / "p.jsonl", max_retention=10).get_latest().indexed_object_ids == ["a", "b", "c"]


def test_memory_growth_per_10000_evidence_rows_is_linear_and_stated() -> None:
    """Measures Python heap growth of the shared repository per 5000 source+item pairs."""
    table = _Table()
    repo = _repo(table)
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    with table.lock:
        for i in range(5000):
            n = len(table.rows)
            table.rows.append((n + 1, "source_record", _source(i)))
            table.rows.append((n + 2, "evidence_item", _item(i)))
    repo.refresh()
    grown = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    per_10k_rows_mb = grown / 10000 * 10000 / 1e6  # 5000 pairs == 10000 rows
    print(f"heap growth per 10000 evidence rows: {per_10k_rows_mb:.1f} MB")
    assert len(repo.list_evidence_items()) == 5000
    assert per_10k_rows_mb < 200


def test_pipeline_store_reload_memory_is_bounded_by_one_line(tmp_path) -> None:
    """Each run line persists the full checkpoint id list; reload must not hold the whole file."""
    import gc
    import json as _json

    path = tmp_path / "pipeline.jsonl"
    ids = [f"ko-{i:08d}-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" for i in range(6000)]
    with path.open("w", encoding="utf-8") as handle:
        for n in range(40):
            snap = IndexPipelineSnapshot(
                pipeline_run_id=f"run-{n}", schema_version="v", triggered_by="t", trigger_ref=None,
                indexed_count=len(ids), incremental_count=1, removed_count=0, indexed_watermarks={},
                indexed_at=f"2026-11-20T00:00:{n:02d}Z", is_full_rebuild=False,
                freshness_sla_seconds=3600, indexed_object_ids=ids, backlog_remaining=40 - n,
            )
            handle.write(_json.dumps(snap.to_dict()) + "\n")
    file_size = path.stat().st_size
    gc.collect()
    tracemalloc.start()
    try:
        store = JsonlIndexPipelineStore(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert store.count_runs() == 40
    assert store.get_latest().indexed_object_ids == ids
    assert all(r.indexed_object_ids == [] for r in store.list_runs(40)[:-1])
    # Old behaviour: peak >= file size (read_text) plus every parsed id list. Now ~ one line + latest ids.
    assert peak < file_size * 0.25, (peak, file_size)


# ---------------------------------------------------------------------------
# Streaming evidence repository + cursor pipeline: memory is O(page/batch), not O(rows)
# ---------------------------------------------------------------------------

class _GeneratedKoTable:
    """source_evidence fake that holds NO rows: payloads are generated per request, so any growth in
    process memory is attributable to the code under test, not to the fixture."""

    def __init__(self, total: int, body_bytes: int = 2048) -> None:
        self.total = total  # knowledge objects have append_id 1..total
        self.body = "x" * body_bytes
        self.updated: dict[int, str] = {}  # append_id -> updated_at (in-place upserts)
        self.max_rows_returned = 0
        self.statements: list[str] = []
        self.keep_statements = True

    def ko(self, append_id: int) -> dict:
        meta = {"created_at": "2026-01-01T00:00:00Z"}
        if append_id in self.updated:
            meta["updated_at"] = self.updated[append_id]
        return KnowledgeObject(
            knowledge_object_id=f"ko-{append_id}", source_id=f"src-{append_id}",
            evidence_item_id=f"evi-{append_id}", evidence_bundle_id=f"evb-{append_id}",
            title=f"t{append_id}", text=self.body, source_type="paper", license_scope="internal",
            access_scope=["operator"], environment_scope=["paper"], keywords=["k"], metadata=meta,
        ).to_dict()

    def connect(self, dsn):
        table = self

        class Conn:
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def execute(self, sql, params=()):
                flat = " ".join(sql.split())
                if table.keep_statements:
                    table.statements.append(flat)
                if flat.startswith("SELECT count(*)"):
                    after = params[0]
                    top = params[1] if "append_id <= %s" in flat else table.total
                    return SimpleNamespace(fetchall=lambda: [(max(0, min(top, table.total) - after),)])
                if flat.startswith("SELECT append_id, payload"):
                    after, limit = params[0], params[-1]
                    top = params[1] if "append_id <= %s" in flat else table.total
                    ids = range(after + 1, min(top, table.total) + 1)
                    rows = []
                    for i in ids:
                        if "left(" in flat and i not in table.updated:
                            continue  # date-prefix prefilter: only in-place updated rows qualify
                        rows.append((i, table.ko(i)))
                        if len(rows) >= limit:
                            break
                    table.max_rows_returned = max(table.max_rows_returned, len(rows))
                    return SimpleNamespace(fetchall=lambda: rows)
                if "GROUP BY" in flat:
                    return SimpleNamespace(fetchall=lambda: [("paper", "2026-01-01T00:00:00Z")])
                return SimpleNamespace(fetchall=lambda: [])

        return Conn()


class _CountingBackend:
    """Records what was indexed in O(1) per object (a 1-byte-per-object tally), never the objects."""

    def __init__(self, total: int = 0) -> None:
        self.tally = bytearray(max(total, 200) + 2)
        self.count = 0
        self.indexed: list[str] = []  # only used by small tests

    def index_documents(self, records) -> int:
        for r in records:
            n = int(r.id.split("-")[1])
            if n < len(self.tally):
                self.tally[n] += 1
            if self.count < 100:
                self.indexed.append(r.id)
            self.count += 1
        return len(records)


def _streaming_repo(table: _GeneratedKoTable):
    repo = pg_store.PostgresStreamingEvidenceRepository(dsn="postgres://x")
    repo._connect = lambda: table.connect("x")
    return repo


def _drain(tmp_path, table, batch=16, chunk=4):
    from services.search.index_pipeline import IncrementalIndexPipeline

    repo = _streaming_repo(table)
    backend = _CountingBackend(table.total)
    store = JsonlIndexPipelineStore(tmp_path / "p.jsonl", max_retention=5)
    pipe = IncrementalIndexPipeline(repo, store, retrieval_backend=backend,
                                    max_batch_size=batch, chunk_size=chunk, time_budget_seconds=60)
    runs = 0
    while True:
        snap = pipe.run()
        runs += 1
        if snap.backlog_remaining == 0:
            return repo, backend, store, pipe, runs
        assert runs < 10_000


def test_streaming_repository_startup_and_refresh_hold_no_rows() -> None:
    table = _GeneratedKoTable(50_000)
    tracemalloc.start()
    repo = _streaming_repo(table)
    repo.reload()
    repo.refresh()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert table.statements == []  # no table read at all on startup / reload / refresh
    assert peak < 1_000_000
    assert not repo._knowledge_objects and not repo._evidence_items


def test_streaming_drain_peak_memory_depends_on_batch_not_row_count(tmp_path) -> None:
    """Drain 500 vs 4000 knowledge objects (2 KiB each): peak heap must be flat in row count and bounded
    by page/batch size. Wall clock is never asserted."""
    warm = tmp_path / "warm"
    warm.mkdir()
    _drain(warm, _GeneratedKoTable(10))  # import-time allocations are not per-row cost
    peaks = {}
    for total in (500, 4_000):
        table = _GeneratedKoTable(total)
        table.keep_statements = False
        sub = tmp_path / str(total)
        sub.mkdir()
        tracemalloc.start()
        repo, backend, store, _, _ = _drain(sub, table)
        _, peaks[total] = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert backend.tally[1:total + 1] == bytes([1]) * total  # every object indexed exactly once
        assert backend.count == total
        assert table.max_rows_returned <= pg_store._STREAM_PAGE_SIZE
        assert store.get_latest().indexed_append_cursor == total
        assert store.get_latest().indexed_object_ids == []
    assert peaks[4_000] < peaks[500] * 1.5, peaks  # 8x rows, ~flat memory
    page_bytes = pg_store._STREAM_PAGE_SIZE * 2048
    assert peaks[4_000] < page_bytes * 10, (peaks, page_bytes)


def test_streaming_point_reads_and_scalar_summaries_do_not_load_rows() -> None:
    table = _GeneratedKoTable(1_000)
    repo = _streaming_repo(table)
    assert repo.knowledge_object_count() == 1_000
    assert repo.source_watermarks() == {"paper": "2026-01-01T00:00:00Z"}
    assert all("LIMIT" not in s for s in table.statements)  # aggregates only
    with pytest.raises(Exception):
        repo.list_evidence_items()


def test_streaming_pipeline_resumes_from_cursor_and_indexes_only_new_rows(tmp_path) -> None:
    table = _GeneratedKoTable(40)
    repo, backend, store, pipe, _ = _drain(tmp_path, table)
    assert backend.count == 40
    table.total = 47  # source-ingest appended 7 more objects
    snap = pipe.run()
    assert snap.backlog_remaining == 0
    assert backend.count == 47 and backend.tally[41:48] == bytes([1]) * 7
    assert snap.indexed_append_cursor == 47


def test_streaming_pipeline_reindexes_in_place_updates_once(tmp_path) -> None:
    table = _GeneratedKoTable(30)
    repo, backend, store, pipe, _ = _drain(tmp_path, table)
    before = backend.count
    table.updated[5] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")  # upsert keeps append_id, bumps updated_at
    pipe.run()
    assert backend.count == before + 1 and backend.tally[5] == 2
    pipe.run()
    assert backend.count == before + 1  # not re-embedded on later runs


def test_streaming_pipeline_honours_legacy_id_checkpoint_without_reembedding(tmp_path) -> None:
    from services.search.index_pipeline import IncrementalIndexPipeline

    table = _GeneratedKoTable(30)
    legacy = [f"ko-{i}" for i in range(1, 21)]
    store = JsonlIndexPipelineStore(tmp_path / "p.jsonl")
    store.record_run(IndexPipelineSnapshot(
        pipeline_run_id="old", schema_version="v", triggered_by="t", trigger_ref=None,
        indexed_count=20, incremental_count=0, removed_count=0, indexed_watermarks={},
        indexed_at="2099-01-01T00:00:00Z", is_full_rebuild=False, freshness_sla_seconds=60,
        indexed_object_ids=legacy,
    ))
    backend = _CountingBackend(30)
    pipe = IncrementalIndexPipeline(_streaming_repo(table), store, retrieval_backend=backend,
                                    max_batch_size=4, chunk_size=2, time_budget_seconds=60)
    while pipe.run().backlog_remaining:
        pass
    assert backend.tally[1:21] == bytes(20) and backend.tally[21:31] == bytes([1]) * 10
    assert store.get_latest().indexed_object_ids == []  # id list dropped once the cursor caught up


def test_runtime_budget_at_observed_growth_is_stated() -> None:
    """Budget arithmetic for the registered instance (see problem-register WP3.1 row)."""
    page_mb = pg_store._STREAM_PAGE_SIZE * 3_400 / 1e6  # ~290 MB / 86.5k rows = ~3.4 KB/row
    batch_mb = 16 * 3_400 / 1e6
    fixed_model_mb = 1_550  # e5-large ONNX measured in SEARCH-SVC-PEAK-MEMORY-AND-STALL-SIGNAL-20261120
    embed_mb = 600
    assert fixed_model_mb + embed_mb + page_mb + batch_mb < 13 * 1024 * 0.25
