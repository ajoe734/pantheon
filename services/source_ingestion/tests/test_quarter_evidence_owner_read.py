"""BFF-QUARTERLY-RANKING-READ-LATENCY-20261114: owner quarter-window read.

The existing owner route GET /api/source-ingest/evidence/items accepts optional
``captured_from`` / ``captured_before``. Only items captured in the half-open
window are paged and ``total`` counts that population, so a caller's request
count follows the quarter population instead of the whole evidence count.
Unfiltered callers are unchanged. InMemory/JSONL and Postgres share semantics.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.knowledge.evidence.models import EvidenceItem
from services.knowledge.evidence.repository import (
    InMemoryEvidenceRepository,
    JsonlEvidenceRepository,
)
from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

Q1 = ("2026-01-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00")
TENANT = "tenant-a"


def _item(i: str, *, available: Any = None, event: Any = None, tenant: str | None = TENANT) -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id=i,
        source_id=f"src-{i}",
        item_type="filing",
        content_ref=f"ref://{i}",
        citation_label=i,
        available_time=available,
        event_time=event,
        metadata={"tenant_id": tenant} if tenant else {},
    )


def _put(repo: InMemoryEvidenceRepository, item: EvidenceItem) -> None:
    repo._evidence_items[item.evidence_item_id] = item
    repo._evidence_items_by_tenant[(item.tenant_id, item.evidence_item_id)] = item


def _repo(items: List[EvidenceItem]) -> InMemoryEvidenceRepository:
    repo = InMemoryEvidenceRepository()
    for it in items:
        _put(repo, it)
    return repo


def _ids(items: List[EvidenceItem]) -> List[str]:
    return [i.evidence_item_id for i in items]


def _mixed() -> List[EvidenceItem]:
    return [
        _item("before", available="2025-12-31T23:59:59Z"),
        _item("start-edge", available="2026-01-01T00:00:00Z"),
        _item("event-fallback", event="2026-02-01"),  # naive => UTC
        _item("offset", available="2026-03-31T23:30:00-01:00"),  # == 04-01T00:30Z: outside
        _item("inside-offset", available="2026-04-01T05:00:00+08:00"),  # == 03-31T21:00Z: inside
        _item("end-edge", available="2026-04-01T00:00:00Z"),  # exclusive
        _item("no-time"),
        _item("bad-time", available="not-a-date"),
        _item("other-tenant", available="2026-02-02T00:00:00Z", tenant="tenant-b"),
    ]


def test_window_semantics_in_memory_match_bff_rule() -> None:
    repo = _repo(_mixed())
    items, total = repo.list_evidence_items_page(TENANT, limit=50, captured_from=Q1[0], captured_before=Q1[1])
    assert sorted(_ids(items)) == ["event-fallback", "inside-offset", "start-edge"]
    assert total == 3
    # newest-first by append order, like the unfiltered read
    assert _ids(items) == ["inside-offset", "event-fallback", "start-edge"]


def test_unfiltered_page_is_unchanged() -> None:
    repo = _repo(_mixed())
    items, total = repo.list_evidence_items_page(TENANT, limit=3, offset=1)
    assert total == 8  # whole tenant population, filter-free
    assert len(items) == 3
    assert _ids(items) == _ids(list(reversed(repo.list_evidence_items(TENANT)))[1:4])


def test_total_counts_matching_population_and_pages_do_not_depend_on_other_rows() -> None:
    rows = [_item(f"new-{i}", available="2026-06-01T00:00:00Z") for i in range(1600)]
    rows += [_item(f"q-{i}", available="2026-02-15T00:00:00Z") for i in range(501)]
    rows += [_item(f"newer-{i}", available="2026-07-01T00:00:00Z") for i in range(300)]
    repo = _repo(rows)
    seen: List[str] = []
    offset, calls = 0, 0
    while True:
        page, total = repo.list_evidence_items_page(
            TENANT, limit=500, offset=offset, captured_from=Q1[0], captured_before=Q1[1]
        )
        calls += 1
        seen.extend(_ids(page))
        offset += len(page)
        if not page or offset >= total:
            break
    assert total == 501 and calls == 2  # ceil(501/500), not ceil(2401/500)
    assert sorted(seen) == sorted(f"q-{i}" for i in range(501))


def test_tenant_scope_is_never_widened_by_the_window() -> None:
    rows = [_item(f"x-{i}", available="2026-02-02T00:00:00Z", tenant="tenant-b") for i in range(500)]
    rows.append(_item("wanted", available="2026-02-03T00:00:00Z"))
    repo = _repo(rows)
    items, total = repo.list_evidence_items_page(TENANT, limit=500, captured_from=Q1[0], captured_before=Q1[1])
    assert _ids(items) == ["wanted"] and total == 1


def test_jsonl_repository_shares_the_semantics(tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    repo = JsonlEvidenceRepository(path)
    from services.source_ingestion.connectors.base import SourceRecord  # noqa: F401

    memory = _repo(_mixed())
    # JSONL inherits the InMemory page implementation: same predicate, same result.
    for it in _mixed():
        _put(repo, it)
    a = repo.list_evidence_items_page(TENANT, limit=50, captured_from=Q1[0], captured_before=Q1[1])
    b = memory.list_evidence_items_page(TENANT, limit=50, captured_from=Q1[0], captured_before=Q1[1])
    assert _ids(a[0]) == _ids(b[0]) and a[1] == b[1] == 3


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------
@pytest.fixture()
def client_for(monkeypatch: pytest.MonkeyPatch):
    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "q-secret")

    def build(repo: Any) -> TestClient:
        app = FastAPI()
        app.include_router(create_ingest_operations_router(SimpleNamespace(evidence_repository=repo)))
        token = encode_jwt_hs256(
            {"sub": "r", "roles": ["operator"], "tenant_id": TENANT, "exp": int(time.time()) + 600},
            secret="q-secret",
        )
        return TestClient(app, headers={"Authorization": f"Bearer {token}"})

    return build


def test_route_window_filters_and_echoes_filters(client_for) -> None:
    client = client_for(_repo(_mixed()))
    body = client.get(
        "/api/source-ingest/evidence/items",
        params={"page_size": 50, "captured_from": Q1[0], "captured_before": Q1[1]},
    ).json()
    assert body["total"] == 3
    assert sorted(i["evidence_item_id"] for i in body["items"]) == ["event-fallback", "inside-offset", "start-edge"]
    assert body["filters"] == {"captured_from": Q1[0], "captured_before": Q1[1]}
    assert body["next_page_token"] is None
    # the summary stays the whole tenant population summary, not the window's
    assert body["summary"]["total"] == 8


def test_route_without_window_is_the_legacy_response(client_for) -> None:
    client = client_for(_repo(_mixed()))
    body = client.get("/api/source-ingest/evidence/items", params={"page_size": 3}).json()
    assert "filters" not in body
    assert body["total"] == 8 and len(body["items"]) == 3 and body["next_page_token"] == "3"


def test_route_rejects_an_unparsable_window(client_for) -> None:
    client = client_for(_repo(_mixed()))
    resp = client.get("/api/source-ingest/evidence/items", params={"captured_from": "garbage"})
    assert resp.status_code == 400


def test_route_owner_failure_stays_unavailable_not_empty(client_for) -> None:
    class Broken(InMemoryEvidenceRepository):
        def list_evidence_items_page(self, *a: Any, **k: Any):
            raise RuntimeError("down")

    resp = client_for(Broken()).get(
        "/api/source-ingest/evidence/items", params={"captured_from": Q1[0], "captured_before": Q1[1]}
    )
    assert resp.status_code == 503


def test_route_window_on_an_old_repository_signature_is_not_silently_unfiltered(client_for) -> None:
    class Old(InMemoryEvidenceRepository):
        def list_evidence_items_page(self, tenant_id=None, *, limit, offset=0):  # no window support
            return super().list_evidence_items_page(tenant_id, limit=limit, offset=offset)

    resp = client_for(Old()).get(
        "/api/source-ingest/evidence/items", params={"captured_from": Q1[0], "captured_before": Q1[1]}
    )
    assert resp.status_code == 503  # honest failure; the BFF reads unavailable


# --------------------------------------------------------------------------
# Postgres
# --------------------------------------------------------------------------
class _FakeConn:
    def __init__(self, log: list, *, fail_window: bool):
        self.log, self.fail_window = log, fail_window

    def execute(self, sql: str, params: tuple = ()):
        self.log.append((sql, params))
        if self.fail_window and "timestamptz" in sql:
            raise RuntimeError("date/time field value out of range")
        return SimpleNamespace(fetchone=lambda: (0,), fetchall=lambda: [])


def _pg_stub(rows: List[EvidenceItem], log: list, *, fail_window: bool) -> PostgresSourceEvidenceRepository:
    repo = PostgresSourceEvidenceRepository.__new__(PostgresSourceEvidenceRepository)
    repo._replaying = False
    repo.table = "evidence_log"

    @contextmanager
    def conn():
        yield _FakeConn(log, fail_window=fail_window)

    repo._get_connection = conn  # type: ignore[method-assign]
    repo._fetch_records = lambda sql, params, r_type, tenant_id=None: []  # type: ignore[method-assign]
    repo._read_many = lambda r_type, tenant_id=None: [r for r in rows if r.tenant_id == tenant_id]  # type: ignore[method-assign]
    return repo


def test_postgres_window_filters_in_sql_with_a_strict_guard_and_bound_instants() -> None:
    log: list = []
    repo = _pg_stub([], log, fail_window=False)
    repo.list_evidence_items_page(TENANT, limit=500, offset=0, captured_from=Q1[0], captured_before=Q1[1])
    sql, params = log[0]
    assert "COUNT(*)" in sql and "timestamptz" in sql and "COALESCE(NULLIF(BTRIM(payload->>'available_time')" in sql
    assert ">= %s" in sql and "< %s" in sql
    assert params[0] == "evidence_item" and params[1] == TENANT
    assert [p.isoformat() for p in params[2:4]] == ["2026-01-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00"]
    assert Q1[0] not in sql  # values are bound, never interpolated


def test_postgres_cast_failure_falls_back_to_the_same_python_semantics() -> None:
    log: list = []
    repo = _pg_stub(_mixed(), log, fail_window=True)
    items, total = repo.list_evidence_items_page(TENANT, limit=50, captured_from=Q1[0], captured_before=Q1[1])
    assert total == 3
    assert _ids(items) == ["inside-offset", "event-fallback", "start-edge"]


def test_postgres_unfiltered_query_is_unchanged() -> None:
    log: list = []
    repo = _pg_stub([], log, fail_window=False)
    repo.list_evidence_items_page(TENANT, limit=200, offset=0)
    assert all("timestamptz" not in sql for sql, _ in log)


def _real_pg_dsn() -> str | None:
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn

    return _get_test_pg_dsn()


@pytest.mark.skipif(_real_pg_dsn() is None, reason="no Postgres test DSN")
def test_real_postgres_matches_in_memory_window_semantics() -> None:
    import uuid

    import psycopg

    dsn = _real_pg_dsn()
    schema = f"qwin_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        repo = PostgresSourceEvidenceRepository(dsn=dsn, table=f'"{schema}".evidence_log', bootstrap=True)
        from services.source_ingestion.connectors.base import SourceRecord  # noqa: F401

        rows = _mixed()
        memory = _repo(rows)
        for it in rows:
            repo.add_source_record(_source_for(it))
            repo.add_evidence_item(it)
        for tenant in (TENANT, "tenant-b"):
            a = repo.list_evidence_items_page(tenant, limit=50, captured_from=Q1[0], captured_before=Q1[1])
            b = memory.list_evidence_items_page(tenant, limit=50, captured_from=Q1[0], captured_before=Q1[1])
            assert _ids(a[0]) == _ids(b[0]) and a[1] == b[1]
        repo.close()
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _source_for(item: EvidenceItem):
    from services.source_ingestion.connectors.base import SourceRecord

    return SourceRecord(
        source_id=item.source_id,
        connector_id="c",
        source_type="filing",
        title=item.citation_label,
        content_ref=item.content_ref,
        metadata=dict(item.metadata),
    )
