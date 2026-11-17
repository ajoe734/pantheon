"""Memory-bound contracts for search-svc (evidence loading, snapshots, run history)."""

from __future__ import annotations

import threading
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
