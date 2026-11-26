"""ARTIFACT-CATALOG-INDEXED-PAGING-20261115.

Proves, without a Postgres server (none is available to the worker; the SQL is asserted by shape):
1. Registry reads one bounded ordered page per request (LIMIT page+1, keyset predicate, separate exact count),
   with a stable keyset token, and rows read per page do not grow with the population.
2. Research exposes the same bounded ordered paging (and keeps the legacy bare list without paging params).
3. The /bff/artifacts merge has deterministic cross-owner page boundaries and yields the same order, totals and
   item set as the legacy full merge for the same data.
4. A population ten times larger keeps per-page owner rows and BFF latency flat.
5. Tenant scoping and visibility rules are unchanged.
6. Index DDL is additive (``IF NOT EXISTS``), so the previous release still reads the table.
"""
from __future__ import annotations

import bisect
import statistics
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from services.control_plane.bff.ports.research_knowledge_source import (
    ArtifactOwnerPageResult,
    ArtifactOwnerReadResult,
    DefaultResearchKnowledgeSourcePort,
    artifact_catalog_key,
    decode_artifact_owner_token,
    encode_artifact_owner_token,
)
from services.control_plane.bff.research.service import (
    ResearchRouterService,
    _naive_utc,
    _parse_rfc3339,
)
from services.registry import pg_store
from services.registry.models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from services.registry.storage import (
    RegistryStore,
    decode_strategy_artifact_page_token,
    page_strategy_artifact_rows,
)

HOSTED_REGISTRY = 251
HOSTED_RESEARCH = 40


def _ts(n: int) -> str:
    # Coarse buckets force equal timestamps across owners so the id tiebreak is exercised.
    return f"2026-11-{1 + (n // 6) % 28:02d}T{(n // 2) % 24:02d}:00:00Z"


def _registry_rows(count: int) -> List[Dict[str, Any]]:
    return [
        {
            "registry_id": f"reg-{i:06d}", "strategy_id": f"strat-{i % 7}", "version": "1.0.0",
            "artifact_state": "approved" if i % 3 else "candidate", "checksum": f"sha256:{i}",
            "created_at": _ts(i), "approver": None, "risk_level": "low",
        }
        for i in range(count)
    ]


def _research_rows(count: int) -> List[Dict[str, Any]]:
    return [
        {
            "artifact_id": f"res-{i:06d}", "title": f"out-{i}", "artifact_type": "run_output",
            "checksum": f"sha256:r{i}", "created_at": _ts(i + 1), "created_by": "op", "tenant_id": "t",
            "run": {"status": "completed"},
        }
        for i in range(count)
    ]


def _normalized_registry(rows):
    from services.control_plane.bff.ports.research_knowledge_source import normalize_registry_catalog_row

    return [normalize_registry_catalog_row(r) for r in rows]


def _normalized_research(rows):
    from services.control_plane.bff.ports.research_knowledge_source import normalize_research_catalog_row

    return [normalize_research_catalog_row(r) for r in rows]


class _IndexedOwner:
    """Models an ordered index: a page is found by bisect and costs ``rows_read`` = rows returned (+1 probe)."""

    def __init__(self, items: List[Dict[str, Any]]) -> None:
        self.items = sorted(items, key=artifact_catalog_key, reverse=True)
        self.keys = [artifact_catalog_key(r) for r in self.items]
        self.rows_read: List[int] = []

    def page(self, page_size: int, page_token: Optional[str]) -> ArtifactOwnerPageResult:
        anchor = decode_artifact_owner_token(page_token)
        # keys are descending; first index with key < anchor
        start = 0
        if anchor is not None:
            neg = [(-0, k) for k in self.keys]  # keep list untouched
            lo, hi = 0, len(self.keys)
            while lo < hi:
                mid = (lo + hi) // 2
                if self.keys[mid] < anchor:
                    hi = mid
                else:
                    lo = mid + 1
            start = lo
            del neg
        window = self.items[start:start + page_size + 1]
        self.rows_read.append(len(window))
        items = window[:page_size]
        more = len(window) > page_size
        return ArtifactOwnerPageResult(
            [dict(r) for r in items], "ok", "service_client", total=len(self.items),
            next_page_token=encode_artifact_owner_token(artifact_catalog_key(items[-1])) if more else None,
        )


class _PagedPort:
    def __init__(self, registry: List[Dict[str, Any]], research: List[Dict[str, Any]]) -> None:
        self.registry = _IndexedOwner(registry)
        self.research = _IndexedOwner(research)
        self._legacy_registry = list(registry)
        self._legacy_research = list(research)

    def read_registry_strategy_artifacts_page(self, *, page_size, page_token=None):
        return self.registry.page(page_size, page_token)

    def read_research_artifact_catalog_page(self, *, page_size, page_token=None):
        return self.research.page(page_size, page_token)

    def read_registry_strategy_artifacts(self):
        return ArtifactOwnerReadResult(list(self._legacy_registry), "ok", "service_client")

    def read_research_artifact_catalog_rows(self):
        return ArtifactOwnerReadResult(list(self._legacy_research), "ok", "service_client")


def _service(port: Any) -> ResearchRouterService:
    def page_slice(items, token, size):
        try:
            start = max(0, int(token or 0))
        except (TypeError, ValueError):
            start = 0
        end = start + size
        return list(items[start:end]), str(end) if end < len(items) else None

    svc = ResearchRouterService.__new__(ResearchRouterService)
    svc._port = lambda: port  # type: ignore[method-assign]
    svc.utc_now = lambda: "2026-11-15T00:00:00Z"  # type: ignore[method-assign]
    svc.snapshot_meta = lambda at: {"snapshot_at": at}  # type: ignore[method-assign]
    svc.page_slice = page_slice  # type: ignore[method-assign]
    svc._get_dataset_source = lambda name: "service_client"  # type: ignore[method-assign]
    return svc


def _walk(svc: ResearchRouterService, page_size: int) -> Tuple[List[Dict[str, Any]], List[Optional[int]]]:
    rows: List[Dict[str, Any]] = []
    totals: List[Optional[int]] = []
    token: Optional[str] = None
    for _ in range(10_000):
        body = svc.list_artifact_catalog(page_token=token, page_size=page_size)
        rows.extend(body["items"])
        totals.append(body["page_info"]["total"])
        token = body["page_info"]["next_page_token"]
        if not token:
            return rows, totals
    raise AssertionError("pagination did not terminate")


def _legacy_order(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (_naive_utc(_parse_rfc3339(r.get("updatedAt"))), str(r.get("id") or "")),
        reverse=True,
    )


# --- 1. Registry bounded page ---------------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, log, page_rows, total):
        self.log, self.page_rows, self.total = log, page_rows, total

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT COUNT"):
            return _FakeCursor([(self.total,)])
        if "jsonb_typeof(payload) = 'string'" in sql and "LIMIT 1" in sql:
            return _FakeCursor([])
        return _FakeCursor(self.page_rows)


def _pg_store_with(log, page_rows, total):
    class _Entries:
        table = '"registry"."entries"'

        def _use_conn(self, conn):
            import contextlib

            @contextlib.contextmanager
            def cm():
                yield _FakeConn(log, page_rows, total)

            return cm()

    store = pg_store.PostgresRegistryStore.__new__(pg_store.PostgresRegistryStore)
    store._entries = _Entries()
    return store


def _columns(row: Dict[str, Any]) -> tuple:
    import json

    names = list(pg_store.STRATEGY_ARTIFACT_ROW_KEYS) + list(pg_store.STRATEGY_ARTIFACT_EVAL_KEYS)
    return tuple(json.dumps(row.get(n)) if row.get(n) is not None else None for n in names)


def test_registry_postgres_page_is_one_bounded_ordered_keyset_read_with_exact_total():
    population = sorted(_registry_rows(500), key=lambda r: (r["created_at"], r["registry_id"]), reverse=True)
    log: list = []
    store = _pg_store_with(log, [_columns(r) for r in population[:11]], total=500)
    items, total, token = store.list_strategy_artifact_page(
        visible_tenants=frozenset({"tenant-a", None}), limit=10, cursor=None,
    )
    assert (len(items), total) == (10, 500)
    assert [r["registry_id"] for r in items] == [r["registry_id"] for r in population[:10]]
    assert decode_strategy_artifact_page_token(token) == ("after", (population[9]["created_at"], population[9]["registry_id"]))

    page_sql = next(sql for sql, _ in log if not sql.lstrip().upper().startswith(("SELECT COUNT", "SELECT 1")))
    assert "LIMIT 11" in page_sql
    assert 'ORDER BY (COALESCE(payload ->> \'created_at\', \'\') COLLATE "C") DESC' in page_sql
    assert "OFFSET" not in page_sql and "payload ->> 'artifact_type' = 'execution_bundle'" in page_sql
    count_sqls = [sql for sql, _ in log if sql.lstrip().upper().startswith("SELECT COUNT")]
    assert len(count_sqls) == 1 and "LIMIT" not in count_sqls[0]

    # Second page anchors on the last served key (keyset, not offset).
    log.clear()
    store.list_strategy_artifact_page(limit=10, cursor=decode_strategy_artifact_page_token(token))
    page_sql = next(sql for sql, _ in log if not sql.lstrip().upper().startswith(("SELECT COUNT", "SELECT 1")))
    assert "< (%s::text, %s::text)" in page_sql and "OFFSET" not in page_sql


@pytest.mark.parametrize("population", [HOSTED_REGISTRY, HOSTED_REGISTRY * 10])
def test_registry_sql_row_limit_does_not_depend_on_population(population):
    log: list = []
    store = _pg_store_with(log, [], total=population)
    store.list_strategy_artifact_page(limit=100, cursor=None)
    page_sql = next(sql for sql, _ in log if not sql.lstrip().upper().startswith(("SELECT COUNT", "SELECT 1")))
    assert "LIMIT 101" in page_sql  # bounded by the page, whatever the population


def test_registry_in_memory_page_matches_legacy_listing_and_token_is_stable_across_inserts():
    rows = _registry_rows(60)
    legacy = sorted(rows, key=lambda r: (r["created_at"], r["registry_id"]), reverse=True)
    seen: List[str] = []
    cursor = None
    while True:
        items, total, token = page_strategy_artifact_rows(rows, limit=7, cursor=cursor)
        assert total == 60
        seen.extend(r["registry_id"] for r in items)
        if token is None:
            break
        cursor = decode_strategy_artifact_page_token(token)
    assert seen == [r["registry_id"] for r in legacy]

    # A newer row inserted between pages does not shift the next page (offset tokens would repeat a row).
    items, _total, token = page_strategy_artifact_rows(rows, limit=7)
    rows.append({**rows[0], "registry_id": "reg-new", "created_at": "2099-01-01T00:00:00Z"})
    nxt, new_total, _ = page_strategy_artifact_rows(rows, limit=7, cursor=decode_strategy_artifact_page_token(token))
    assert new_total == 61
    assert [r["registry_id"] for r in nxt] == [r["registry_id"] for r in legacy[7:14]]


def test_registry_legacy_offset_token_and_filters_still_work():
    rows = _registry_rows(30)
    items, total, _ = page_strategy_artifact_rows(
        rows, artifact_state="candidate", strategy_id="strat-0", limit=50,
    )
    assert total == len(items) and all(r["artifact_state"] == "candidate" and r["strategy_id"] == "strat-0" for r in items)
    assert decode_strategy_artifact_page_token("5") == ("offset", (5,))
    for bad in ("-1", "abc", "k1:@@@"):
        with pytest.raises(ValueError):
            decode_strategy_artifact_page_token(bad)
    legacy_page, _, _ = page_strategy_artifact_rows(rows, limit=4, cursor=("offset", (4,)))
    full, _, _ = page_strategy_artifact_rows(rows, limit=30)
    assert legacy_page == full[4:8]


# --- 5. Tenant scoping ----------------------------------------------------------------------------------------


def _create(store: RegistryStore, idx: int, tenant: str, created_at: str) -> None:
    entry = store.create(
        RegistryEntryCreate(
            artifact_type=ArtifactType.EXECUTION_BUNDLE, strategy_id=f"strat-{idx}", version=f"1.{idx}.0",
            artifact_state=ArtifactState.APPROVED, lineage=Lineage(source_run_ids=[f"run-{idx}"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_artifact"),
            checksum=f"sha256:b{idx}", evaluation_summary={"risk_level": "low"},
            metadata={"strategy_artifact": {"artifact_id": f"b-{idx}"}},
        ),
        f"b-{idx}", actor={"tenant": tenant},
    )
    entry.created_at = created_at
    store.put(entry)


def test_tenant_scope_is_applied_before_paging_and_totals():
    store = RegistryStore()
    for i in range(6):
        _create(store, i, "tenant-a" if i % 2 == 0 else "tenant-b", f"2026-11-0{i + 1}T00:00:00Z")
    items, total, token = store.list_strategy_artifact_page(visible_tenants=frozenset({"tenant-a"}), limit=2)
    assert total == 3 and [r["registry_id"] for r in items] == ["b-4", "b-2"]
    nxt, _, last = store.list_strategy_artifact_page(
        visible_tenants=frozenset({"tenant-a"}), limit=2, cursor=decode_strategy_artifact_page_token(token),
    )
    assert [r["registry_id"] for r in nxt] == ["b-0"] and last is None
    assert store.list_strategy_artifact_page(visible_tenants=frozenset({"tenant-z"}), limit=5)[:2] == ([], 0)


def test_research_page_keeps_caller_tenant_visibility_and_legacy_list(monkeypatch):
    research_main = pytest.importorskip("services.research.main")
    rows = [
        {"artifact_id": f"a-{i}", "created_at": _ts(i), "tenant_id": "mine"} for i in range(25)
    ]
    monkeypatch.setattr(research_main, "_visible_saved_artifacts", lambda tenant: sorted(
        [r for r in rows if r["tenant_id"] == tenant], key=research_main._artifact_page_key, reverse=True))
    legacy = research_main.list_saved_research_artifacts(page_size=None, page_token=None, tenant_id="mine")
    assert isinstance(legacy, list) and len(legacy) == 25
    assert research_main.list_saved_research_artifacts(page_size=None, page_token=None, tenant_id="other") == []

    seen: List[str] = []
    token = None
    while True:
        body = research_main.list_saved_research_artifacts(page_size=8, page_token=token, tenant_id="mine")
        assert body["total"] == 25 and len(body["items"]) <= 8
        seen.extend(r["artifact_id"] for r in body["items"])
        token = body["next_page_token"]
        if token is None:
            break
    assert seen == [r["artifact_id"] for r in legacy]
    assert research_main.list_saved_research_artifacts(page_size=8, page_token=None, tenant_id="other")["total"] == 0
    with pytest.raises(Exception):
        research_main.list_saved_research_artifacts(page_size=8, page_token="9", tenant_id="mine")


def test_port_pages_injected_registry_and_research_stores_with_owner_order():
    port = DefaultResearchKnowledgeSourcePort(
        registry_strategy_artifacts_store={r["registry_id"]: r for r in _registry_rows(30)},
        research_artifacts_store={r["artifact_id"]: r for r in _research_rows(12)},
    )
    reg = port.read_registry_strategy_artifacts_page(page_size=10)
    res = port.read_research_artifact_catalog_page(page_size=5)
    assert (len(reg.items), reg.total, len(res.items), res.total) == (10, 30, 5, 12)
    assert reg.next_page_token and res.next_page_token
    keys = [artifact_catalog_key(r) for r in reg.items]
    assert keys == sorted(keys, reverse=True)


# --- 3. Cross-owner merge -------------------------------------------------------------------------------------


@pytest.mark.parametrize("page_size", [1, 3, 7, 20, 100])
def test_bff_merge_matches_legacy_order_totals_and_item_set(page_size):
    reg = _normalized_registry(_registry_rows(90))
    res = _normalized_research(_research_rows(37))
    paged_rows, totals = _walk(_service(_PagedPort(reg, res)), page_size)

    legacy = _legacy_order(reg + res)
    assert [r["id"] for r in paged_rows] == [r["id"] for r in legacy]
    assert paged_rows == legacy
    assert set(totals) == {len(legacy)}
    assert len({r["id"] for r in paged_rows}) == len(paged_rows)


def test_bff_merge_page_boundaries_are_deterministic_and_resumable():
    reg, res = _normalized_registry(_registry_rows(40)), _normalized_research(_research_rows(40))
    svc = _service(_PagedPort(reg, res))
    first = svc.list_artifact_catalog(page_size=9)
    again = svc.list_artifact_catalog(page_size=9)
    assert first["items"] == again["items"]
    assert first["page_info"]["next_page_token"] == again["page_info"]["next_page_token"]
    second = svc.list_artifact_catalog(page_token=first["page_info"]["next_page_token"], page_size=9)
    legacy = _legacy_order(reg + res)
    assert first["items"] + second["items"] == legacy[:18]


def test_bff_merge_one_owner_empty_and_one_owner_failed():
    reg = _normalized_registry(_registry_rows(12))
    rows, totals = _walk(_service(_PagedPort(reg, [])), 5)
    assert [r["id"] for r in rows] == [r["id"] for r in _legacy_order(reg)] and set(totals) == {12}

    port = _PagedPort(reg, _normalized_research(_research_rows(5)))
    port.read_research_artifact_catalog_page = lambda **_k: ArtifactOwnerPageResult([], "failed", "unavailable", "boom")
    body = _service(port).list_artifact_catalog(page_size=5)
    assert body["meta"]["surfaces"]["artifact_list"]["status"] == "degraded"
    assert body["page_info"]["total"] is None and len(body["items"]) == 5


def test_bff_falls_back_to_legacy_merge_for_ports_without_owner_pages_and_offset_tokens():
    reg, res = _normalized_registry(_registry_rows(15)), _normalized_research(_research_rows(6))

    class _Legacy:
        def read_registry_strategy_artifacts(self):
            return ArtifactOwnerReadResult(list(reg), "ok", "service_client")

        def read_research_artifact_catalog_rows(self):
            return ArtifactOwnerReadResult(list(res), "ok", "service_client")

    rows, totals = _walk(_service(_Legacy()), 4)
    assert rows == _legacy_order(reg + res) and set(totals) == {21}
    # An in-flight legacy offset token on a page-capable port keeps working.
    body = _service(_PagedPort(reg, res)).list_artifact_catalog(page_token="4", page_size=4)
    assert body["items"] == _legacy_order(reg + res)[4:8]


# --- 4. Ten-times population -----------------------------------------------------------------------------------


def _measure(registry_n: int, research_n: int, page_size: int = 20) -> Dict[str, Any]:
    port = _PagedPort(_normalized_registry(_registry_rows(registry_n)), _normalized_research(_research_rows(research_n)))
    svc = _service(port)
    svc.list_artifact_catalog(page_size=page_size)  # warm
    port.registry.rows_read.clear()
    port.research.rows_read.clear()
    timings = []
    for _ in range(31):
        t0 = time.perf_counter()
        body = svc.list_artifact_catalog(page_size=page_size)
        timings.append((time.perf_counter() - t0) * 1000.0)
    assert body["page_info"]["total"] == registry_n + research_n
    return {
        "population": registry_n + research_n,
        "registry_rows_read_per_page": max(port.registry.rows_read),
        "research_rows_read_per_page": max(port.research.rows_read),
        "median_ms": statistics.median(timings),
    }


def test_ten_times_population_keeps_per_page_rows_and_latency_flat():
    hosted = _measure(HOSTED_REGISTRY, HOSTED_RESEARCH)
    tenfold = _measure(HOSTED_REGISTRY * 10, HOSTED_RESEARCH * 10)
    assert tenfold["population"] == hosted["population"] * 10
    assert tenfold["registry_rows_read_per_page"] <= hosted["registry_rows_read_per_page"] * 1.2
    assert tenfold["research_rows_read_per_page"] <= hosted["research_rows_read_per_page"] * 1.2
    assert tenfold["registry_rows_read_per_page"] <= 21 and tenfold["research_rows_read_per_page"] <= 21
    # Latency: 20% budget plus a 1 ms floor so timer noise on a sub-millisecond merge cannot fail the test.
    assert tenfold["median_ms"] <= hosted["median_ms"] * 1.2 + 1.0


# --- 6. Additive migration --------------------------------------------------------------------------------------


def test_index_migration_is_additive_and_forward_only():
    executed: List[str] = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            executed.append(sql)

    store = pg_store.PostgresRegistryStore.__new__(pg_store.PostgresRegistryStore)
    store._entries = SimpleNamespace(table='"registry"."entries"', table_name="registry.entries", _connect=lambda: _Conn())
    store._ensure_artifact_catalog_indexes()
    assert len(executed) == 3  # + ALPHA-TEMPLATE-READ-FAST-20261126 artifact_type expression index
    for sql in executed:
        upper = sql.upper()
        assert upper.startswith("CREATE INDEX IF NOT EXISTS")
        assert not any(word in upper for word in ("ALTER ", "DROP ", "ADD COLUMN", "UNIQUE"))
