"""Stored lineage-index column, shared summary_rev write-trigger, bounded backfill,
and stamp-validated incremental adjacency cache tests for ``GET /api/registry/lineage``
(REGISTRY-LINEAGE-READ-NO-FULL-SCAN-20261127).

Unit tests run everywhere; real-PostgreSQL tests run when ``TEST_DATABASE_URL`` is set.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from . import pg_store
from .models import (
    BUILTIN_TENANT,
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from .pg_store import (
    LINEAGE_INDEX_BACKFILL_BATCH_SIZE,
    LINEAGE_INDEX_COLUMN,
    SUMMARY_REV_COLUMN,
    PostgresRegistryStore,
    lineage_index_delta_sql,
    lineage_index_stamp_sql,
    lineage_index_stored_select_sql,
    lineage_index_stored_tenant_select_sql,
)
from .split_api import RegistryService
from .storage import (
    RegistryStore,
    _CompiledLineageGraphIndex,
    _CompiledLineageRowList,
    build_lineage_graph,
    build_lineage_index_payload,
    lineage_row,
)
from .test_lineage_read import (
    FD,
    LIVE_ARTIFACT_ID,
    LIVE_PERSONA_ID,
    LIVE_POOL_ID,
    LIVE_RECORDED_ROOTS,
    LIVE_STRATEGY_ID,
    _edge_set,
    _entry,
    large_corpus,
    live_persona_family_entries,
    persona_family_entries,
)

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_pg = pytest.mark.skipif(not _DSN, reason="needs TEST_DATABASE_URL")


def test_lineage_stored_index_sql_never_evaluates_payload_json_expressions():
    """AC1: The lineage SQL queries (cold stored-column select, cheap stamp check,
    incremental delta select, and uncached tenant select) read only stored
    ``lineage_index``/``summary_rev`` columns and never reference or detoast ``payload``.
    """
    table = '"registry"."entries"'
    stamp_sql = lineage_index_stamp_sql(table)
    stored_sql = lineage_index_stored_select_sql(table)
    delta_sql = lineage_index_delta_sql(table)
    tenant_sql, tenant_params = lineage_index_stored_tenant_select_sql(
        table, frozenset({"tenant-dev", BUILTIN_TENANT})
    )

    for sql in (stamp_sql, stored_sql, delta_sql, tenant_sql):
        assert "payload" not in sql, f"Lineage SQL must not reference payload: {sql}"
        assert LINEAGE_INDEX_COLUMN in sql

    assert "count(*)" in stamp_sql.lower()
    assert f"max({SUMMARY_REV_COLUMN})" in stamp_sql.lower()
    assert f"sum({SUMMARY_REV_COLUMN})" in stamp_sql.lower()
    assert f"{SUMMARY_REV_COLUMN} > %s" in delta_sql
    assert tenant_params == (["__builtin__", "tenant-dev"],)


def test_ensure_lineage_index_ddl_reuses_summary_rev_trigger_and_runs_bounded_backfill():
    """AC1 & AC4: Bootstrap DDL adds ``lineage_index`` JSONB idempotently, reuses
    ``summary_rev`` BIGINT, ``pantheon_registry_summary_rev_seq``, and the shared
    ``entries_strategy_summary_index_tg`` trigger, creates ``entries_lineage_index_idx``,
    and runs a bounded batched backfill with ``FOR UPDATE SKIP LOCKED``.
    """
    executed: list[tuple[str, Any]] = []

    class _Cursor:
        rowcount = 0

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))
            return _Cursor()

    store = PostgresRegistryStore.__new__(PostgresRegistryStore)
    store._entries = SimpleNamespace(
        table='"registry"."entries"',
        table_name="registry.entries",
        schema="registry",
        _connect=lambda: _Conn(),
        advisory_xact_lock=lambda key, conn=None: executed.append((f"LOCK:{key}", None)),
    )
    store._ensure_lineage_index()

    sql_texts = [s for s, _ in executed]
    assert any("ADD COLUMN IF NOT EXISTS summary_index JSONB" in s for s in sql_texts)
    assert any("ADD COLUMN IF NOT EXISTS lineage_index JSONB" in s for s in sql_texts)
    assert any("ADD COLUMN IF NOT EXISTS summary_rev BIGINT" in s for s in sql_texts)
    assert any("CREATE SEQUENCE IF NOT EXISTS" in s for s in sql_texts)
    assert any(
        'CREATE OR REPLACE FUNCTION "registry"."pantheon_registry_lineage_index_build"' in s
        for s in sql_texts
    )
    assert any(
        'CREATE OR REPLACE TRIGGER "entries_strategy_summary_index_tg"' in s
        for s in sql_texts
    )
    assert any(
        'CREATE INDEX IF NOT EXISTS "entries_lineage_index_idx"' in s for s in sql_texts
    )
    backfill_calls = [(s, p) for s, p in executed if s.startswith("WITH lineage_todo AS (")]
    assert len(backfill_calls) == 1
    assert "FOR UPDATE SKIP LOCKED" in backfill_calls[0][0]
    assert backfill_calls[0][1] == (LINEAGE_INDEX_BACKFILL_BATCH_SIZE,)
    assert store.last_lineage_index_backfill is not None
    assert store.last_lineage_index_backfill["rows_updated"] == 0
    assert store.last_lineage_index_backfill["batches_executed"] == 1


def test_build_lineage_index_payload_and_compiled_graph_index_parity():
    """AC1 & AC2: ``build_lineage_index_payload`` and ``_CompiledLineageGraphIndex``
    match ``lineage_row`` and reference ``build_lineage_graph`` across all artifact
    types, legacy JSON-string payloads, root kinds, depths, limits, and large corpus.
    """
    entries = persona_family_entries() + live_persona_family_entries()
    entries.append(
        _entry(
            "reg-alpha-template-kmid-1",
            "alpha_template",
            "qlib_alpha158:KMID",
            lineage={"source_dataset_refs": ["qlib_alpha158:KMID"]},
            metadata={"risk_level": "low"},
            state="candidate",
            tenant=BUILTIN_TENANT,
        )
    )
    plain_rows = [lineage_row(e) for e in entries]
    compiled_rows = []
    for e in entries:
        built = build_lineage_index_payload(e.to_dict())
        assert built is not None
        # Legacy JSON-string payload unwraps identically
        assert build_lineage_index_payload(json.dumps(e.to_dict())) == built
        decoded = pg_store._decode_stored_lineage_index_row(json.dumps(built))
        assert dict(decoded) == lineage_row(e)
        compiled_rows.append(decoded)

    # Non-object payloads return None
    assert build_lineage_index_payload(json.dumps(["not-an-object"])) is None
    assert build_lineage_index_payload(None) is None

    graph_idx = _CompiledLineageGraphIndex.from_rows(compiled_rows)
    roots_to_check = (
        None,
        f"strategy-persona-{FD}",
        f"reg-strategy-spec-persona-{FD}",
        f"artifact-persona-paper-{FD}",
        f"persona-{FD}",
        f"pool-{FD}",
        *LIVE_RECORDED_ROOTS,
        "qlib_alpha158:KMID",
        "art_stg001_v320",
    )
    for root in roots_to_check:
        for depth in (1, 2, 3):
            for limit in (2, 5, 50, 200):
                expected = build_lineage_graph(plain_rows, root_id=root, depth=depth, limit=limit)
                actual = graph_idx.query(root_id=root, depth=depth, limit=limit)
                assert actual == expected, f"Mismatch for root={root} depth={depth} limit={limit}"

    # Verify large_corpus parity (5251 rows)
    corpus_plain = large_corpus()
    corpus_idx = _CompiledLineageGraphIndex.from_rows(corpus_plain)
    for root in (None, f"strategy-persona-{FD}", "strat-00010", "persona-5", "art_stg001_v320"):
        for limit in (10, 50, 200):
            assert corpus_idx.query(root_id=root, depth=3, limit=limit) == build_lineage_graph(
                corpus_plain, root_id=root, depth=3, limit=limit
            )


def test_postgres_lineage_cache_hit_and_incremental_delta_update_without_payload_rescan_or_full_adjacency_rebuild():
    """AC1 & AC2 (unit proof): ``PostgresRegistryStore.list_lineage_rows`` loads from
    ``lineage_index`` once on cold start (building adjacency once), uses the 1-row
    cheap stamp query on repeated reads (0 rows fetched, 0 adjacency rebuilds), and
    fetches only the delta ``summary_rev > prev_max_rev`` row after a write to update
    the cached adjacency incrementally without rescanning unchanged rows or touching ``payload``.
    """
    initial_entries = persona_family_entries("tenant-a")
    db_rows: dict[str, tuple[int, dict[str, Any]]] = {}
    rev_counter = 0
    for e in initial_entries:
        rev_counter += 1
        db_rows[e.registry_id] = (rev_counter, build_lineage_index_payload(e.to_dict()))

    executed_sql: list[tuple[str, Any]] = []

    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql: str, params: Any = None):
            executed_sql.append((sql, params))
            assert "payload" not in sql, f"Lineage query referenced payload: {sql}"
            if sql.startswith("SELECT count(*)"):
                cnt = len(db_rows)
                max_rev = max((rev for rev, _ in db_rows.values()), default=0)
                sum_rev = sum(rev for rev, _ in db_rows.values())
                return _FakeCursor([(cnt, max_rev, sum_rev)])
            if "summary_rev > %s" in sql:
                watermark = params[0]
                delta = [
                    (rid, rev, json.dumps(doc))
                    for rid, (rev, doc) in db_rows.items()
                    if rev > watermark
                ]
                return _FakeCursor(delta)
            if 'FROM "registry"."entries" WHERE lineage_index IS NOT NULL' in sql:
                all_rows = [
                    (rid, rev, json.dumps(doc))
                    for rid, (rev, doc) in db_rows.items()
                ]
                return _FakeCursor(all_rows)
            raise AssertionError(f"Unexpected SQL: {sql}")

    store = PostgresRegistryStore.__new__(PostgresRegistryStore)
    store._entries = SimpleNamespace(
        table='"registry"."entries"',
        table_name="registry.entries",
        schema="registry",
        _use_conn=lambda conn: _FakeConn(),
    )
    store._init_summary_index_cache_state()
    store._lineage_index_ready = True
    svc = RegistryService(store)
    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    # 1. Cold read: 1 SQL query fetching the 4 stored lineage_index rows, 1 adjacency build
    g1 = svc.lineage_graph(visible_tenants=visible, root_id=f"strategy-persona-{FD}", depth=3, limit=200)
    assert len(g1["nodes"]) > 0
    assert store.lineage_index_sql_reads == 1
    assert store.lineage_index_rows_fetched == 4
    assert store.lineage_index_cache_hits == 0
    assert store.lineage_payload_rescans == 0
    assert store.lineage_adjacency_builds == 1

    # 2. Warm read (unknown root & persona root): 1 cheap stamp query each, 0 rows fetched, 0 adjacency rebuilds
    g_unknown = svc.lineage_graph(visible_tenants=visible, root_id="art_stg001_v320", depth=3, limit=200)
    assert g_unknown["nodes"] == [] and g_unknown["edges"] == []
    assert store.lineage_index_sql_reads == 1
    assert store.lineage_index_cache_hits == 1
    assert store.lineage_index_rows_fetched == 0
    assert store.lineage_adjacency_builds == 1
    assert store.lineage_adjacency_cache_hits == 1

    # 3. Simulate a write (new version v1.0.2 + persona/pool change on existing artifact)
    rev_counter += 1
    new_v3 = _entry(
        f"reg-strategy-spec-persona-v3-{FD}",
        "strategy_spec",
        f"strategy-persona-{FD}",
        lineage={
            "source_run_ids": [f"persona-provisioning-forward-{FD}"],
            "parent_registry_ids": [f"reg-strategy-spec-persona-{FD}"],
        },
        metadata={"persona_id": f"persona-new-{FD}", "capital_pool_id": f"pool-new-{FD}"},
        state="retired",
        tenant="tenant-a",
        version="1.0.2",
        created_at="2026-11-01T04:00:00Z",
    )
    db_rows[new_v3.registry_id] = (rev_counter, build_lineage_index_payload(new_v3.to_dict()))

    g_after = svc.lineage_graph(visible_tenants=visible, root_id=f"strategy-persona-{FD}", depth=3, limit=200)
    assert store.lineage_index_sql_reads == 1
    assert store.lineage_index_delta_reads == 1
    assert store.lineage_index_rows_fetched == 1
    assert store.lineage_adjacency_builds == 1
    assert store.lineage_adjacency_delta_updates == 1
    nodes_by_id = {n["id"]: n for n in g_after["nodes"]}
    assert new_v3.registry_id in nodes_by_id
    assert nodes_by_id[new_v3.registry_id]["version"] == "1.0.2"
    assert nodes_by_id[new_v3.registry_id]["state"] == "retired"
    edges_after = _edge_set(g_after)
    assert (f"reg-strategy-spec-persona-{FD}", new_v3.registry_id, "parent") in edges_after
    assert (new_v3.registry_id, f"persona-new-{FD}", "persona") in edges_after
    assert (new_v3.registry_id, f"pool-new-{FD}", "capital_pool") in edges_after


@pytest.fixture()
def pg_lineage_registry():
    psycopg = pytest.importorskip("psycopg")
    schema = f"lin_idx_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    store = PostgresRegistryStore(
        dsn=_DSN,
        entries_table=f"{schema}.entries",
        receipts_table=f"{schema}.command_receipts",
    )
    try:
        yield store, schema
    finally:
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@needs_pg
def test_real_pg_writes_update_lineage_index_and_are_visible_in_next_read(pg_lineage_registry):
    """AC2: Against real PostgreSQL, proves that every write path on registry entries:
    1) new entry creation (strategy_spec, execution_bundle, alpha_template),
    2) new version creation with parent_registry_ids linkage,
    3) parent change on an entry,
    4) persona_id or capital_pool_id change via update_metadata CAS, and
    5) retire via advance_artifact_state (candidate -> retired)
    updates the stored ``lineage_index`` / ``summary_rev`` columns, performs an indexed
    delta update of the cached adjacency (without full table scan or full adjacency rebuild),
    and is immediately visible in the very next lineage read; and an unknown root_id
    (``art_stg001_v320``) returns zero nodes and zero edges.
    """
    store, _schema = pg_lineage_registry
    svc = RegistryService(store)
    actor = {"actor_id": "op-lin", "roles": ["operator"], "tenant": "tenant-a", "token_kind": "jwt"}
    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    for e in persona_family_entries("tenant-a"):
        store.put(e)

    # Initial cold read + unknown root check
    g_unknown = svc.lineage_graph(visible_tenants=visible, root_id="art_stg001_v320", depth=3, limit=200)
    assert g_unknown == {
        "root_id": "art_stg001_v320",
        "depth": 3,
        "limit": 200,
        "nodes": [],
        "edges": [],
        "has_more": False,
    }
    assert store.lineage_payload_rescans == 0
    assert store.lineage_index_sql_reads == 1
    assert store.lineage_adjacency_builds == 1

    # 1. New entry creation via svc.register -> visible in next read via 1-row delta
    svc.register(
        RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id="strat-pg-write",
            version="1.0.0",
            artifact_state=ArtifactState.CANDIDATE,
            lineage=Lineage(
                source_run_ids=["run-pg-w1"],
                source_dataset_refs=["ds-pg-w1"],
            ),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata"),
            checksum="sha256:pg-w1-v1",
            metadata={"persona_id": "persona-pg-1", "capital_pool_id": "pool-pg-1"},
        ),
        "reg-pg-w1-v1",
        actor=actor,
    )
    g1 = svc.lineage_graph(visible_tenants=visible, root_id="strat-pg-write", depth=2, limit=200)
    assert store.lineage_index_delta_reads == 1
    assert store.lineage_index_rows_fetched == 1
    assert store.lineage_adjacency_builds == 1
    assert store.lineage_adjacency_delta_updates == 1
    assert {n["id"] for n in g1["nodes"]} == {
        "reg-pg-w1-v1", "run-pg-w1", "ds-pg-w1", "persona-pg-1", "pool-pg-1",
    }

    # 2. New version creation via svc.register_strategy_spec_revision -> visible in next read
    svc.register_strategy_spec_revision(
        RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id="strat-pg-write",
            version="1.0.1",
            artifact_state=ArtifactState.CANDIDATE,
            lineage=Lineage(
                source_run_ids=["run-pg-w2"],
                parent_registry_ids=["reg-pg-w1-v1"],
            ),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata"),
            checksum="sha256:pg-w1-v2",
            metadata={"persona_id": "persona-pg-1", "capital_pool_id": "pool-pg-1"},
        ),
        "reg-pg-w1-v2",
        validate_lineage=lambda existing: None,
        actor=actor,
    )
    g2 = svc.lineage_graph(visible_tenants=visible, root_id="strat-pg-write", depth=2, limit=200)
    assert store.lineage_index_delta_reads == 2
    assert store.lineage_index_rows_fetched == 1
    assert store.lineage_adjacency_builds == 1
    assert ("reg-pg-w1-v1", "reg-pg-w1-v2", "parent") in _edge_set(g2)
    v2_node = next(n for n in g2["nodes"] if n["id"] == "reg-pg-w1-v2")
    assert v2_node["version"] == "1.0.1"

    # 3. Parent change write on an execution_bundle entry -> visible in next read
    bundle_v1 = _entry(
        "art-pg-bundle-v1",
        "execution_bundle",
        "strat-pg-write",
        lineage={
            "source_run_ids": ["run-bundle-1"],
            "parent_registry_ids": ["reg-pg-w1-v1"],
            "source_strategy_spec_id": "reg-pg-w1-v1",
        },
        metadata={"persona_id": "persona-pg-1", "capital_pool_id": "pool-pg-1"},
        state="candidate",
        tenant="tenant-a",
        version="1.0.0",
        created_at="2026-11-27T03:00:00Z",
    )
    store.put(bundle_v1)
    g3_before = svc.lineage_graph(visible_tenants=visible, root_id="art-pg-bundle-v1", depth=1, limit=200)
    assert ("reg-pg-w1-v1", "art-pg-bundle-v1", "parent") in _edge_set(g3_before)

    # Update parent_registry_ids and source_strategy_spec_id to point to reg-pg-w1-v2
    bundle_v1_reparented = copy.deepcopy(bundle_v1)
    bundle_v1_reparented.lineage = Lineage(
        source_run_ids=["run-bundle-1"],
        parent_registry_ids=["reg-pg-w1-v2"],
        source_strategy_spec_id="reg-pg-w1-v2",
    )
    store.update(bundle_v1_reparented, expected=bundle_v1.to_dict(), actor=actor)
    g3_after = svc.lineage_graph(visible_tenants=visible, root_id="art-pg-bundle-v1", depth=1, limit=200)
    edges_3 = _edge_set(g3_after)
    assert ("reg-pg-w1-v2", "art-pg-bundle-v1", "parent") in edges_3
    assert ("reg-pg-w1-v2", "art-pg-bundle-v1", "source_strategy_spec") in edges_3
    assert ("reg-pg-w1-v1", "art-pg-bundle-v1", "parent") not in edges_3
    assert ("reg-pg-w1-v1", "art-pg-bundle-v1", "source_strategy_spec") not in edges_3

    # 4. Persona and capital pool change via svc.update_metadata CAS -> visible in next read
    cur_bundle = svc.get("art-pg-bundle-v1").entry
    svc.update_metadata(
        "art-pg-bundle-v1",
        expected_metadata=cur_bundle.metadata,
        new_metadata={
            "persona_id": "persona-pg-2",
            "capital_pool_id": "pool-pg-2",
            "risk_level": "medium",
        },
        command_key="cmd-pg-persona-pool",
        actor=actor,
    )
    g4 = svc.lineage_graph(visible_tenants=visible, root_id="art-pg-bundle-v1", depth=1, limit=200)
    edges_4 = _edge_set(g4)
    assert ("art-pg-bundle-v1", "persona-pg-2", "persona") in edges_4
    assert ("art-pg-bundle-v1", "pool-pg-2", "capital_pool") in edges_4
    assert ("art-pg-bundle-v1", "persona-pg-1", "persona") not in edges_4
    assert ("art-pg-bundle-v1", "pool-pg-1", "capital_pool") not in edges_4
    b_node_4 = next(n for n in g4["nodes"] if n["id"] == "art-pg-bundle-v1")
    assert b_node_4["risk"] == "medium"

    # Root query by new persona_id and new capital_pool_id immediately finds art-pg-bundle-v1
    g_by_new_persona = svc.lineage_graph(visible_tenants=visible, root_id="persona-pg-2", depth=1, limit=200)
    assert ("art-pg-bundle-v1", "persona-pg-2", "persona") in _edge_set(g_by_new_persona)
    g_by_new_pool = svc.lineage_graph(visible_tenants=visible, root_id="pool-pg-2", depth=1, limit=200)
    assert ("art-pg-bundle-v1", "pool-pg-2", "capital_pool") in _edge_set(g_by_new_pool)

    # 5. Retire via svc.advance_artifact_state -> visible in next read
    cur_bundle_2 = svc.get("art-pg-bundle-v1").entry
    svc.advance_artifact_state(
        "art-pg-bundle-v1",
        ArtifactState.RETIRED,
        command_key="cmd-pg-retire-bundle",
        actor=actor,
        expected_artifact_state=cur_bundle_2.artifact_state,
        expected_version=cur_bundle_2.version,
        expected_updated_at=cur_bundle_2.updated_at,
    )
    g5 = svc.lineage_graph(visible_tenants=visible, root_id="art-pg-bundle-v1", depth=1, limit=200)
    b_node_5 = next(n for n in g5["nodes"] if n["id"] == "art-pg-bundle-v1")
    assert b_node_5["state"] == "retired"
    assert store.lineage_payload_rescans == 0
    assert store.lineage_adjacency_builds == 1


@needs_pg
def test_real_pg_idempotent_bounded_backfill_and_rollback_compatibility():
    """AC4: Existing rows written before the ``lineage_index`` column/trigger existed
    are populated by ``backfill_lineage_index`` in bounded batches without changing
    ``updated_at``, re-running the backfill is an idempotent 0-row no-op, and the
    previous release keeps reading and writing the table during a rollback.
    """
    import psycopg

    schema = f"lin_bf_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(
            f"""
            CREATE TABLE "{schema}".entries (
                record_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT '2026-10-01T00:00:00Z'
            )
            """
        )
        all_seed = persona_family_entries("tenant-dev") + live_persona_family_entries("tenant-dev")
        for idx, e in enumerate(all_seed):
            if idx == len(all_seed) - 1:
                # Legacy JSON-string payload encoding
                conn.execute(
                    f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, to_jsonb(%s::text))',
                    (e.registry_id, json.dumps(e.to_dict())),
                )
            else:
                conn.execute(
                    f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, %s::jsonb)',
                    (e.registry_id, json.dumps(e.to_dict())),
                )

    try:
        store = PostgresRegistryStore(
            dsn=_DSN,
            entries_table=f"{schema}.entries",
            receipts_table=f"{schema}.command_receipts",
            bootstrap=False,
        )
        store._ensure_artifact_catalog_indexes()
        store._ensure_artifact_type_index()
        store._ensure_lineage_index()

        first_report = store.last_lineage_index_backfill
        assert first_report is not None
        assert first_report["rows_updated"] == len(all_seed)

        # Idempotent second run updates 0 rows
        second_report = store.backfill_lineage_index(batch_size=4)
        assert second_report["rows_updated"] == 0
        assert second_report["batches_executed"] == 1

        # Verify updated_at was not modified and all rows have lineage_index and summary_rev
        with psycopg.connect(_DSN) as conn:
            non_null_cnt = conn.execute(
                f'SELECT count(*) FROM "{schema}".entries WHERE lineage_index IS NOT NULL AND summary_rev IS NOT NULL'
            ).fetchone()[0]
            distinct_ts = conn.execute(
                f'SELECT count(DISTINCT updated_at) FROM "{schema}".entries'
            ).fetchone()[0]
        assert non_null_cnt == len(all_seed)
        assert distinct_ts == 1

        # Simulate rollback to previous release: previous release replaces trigger fn
        # with the summary-only version (fn_lineage_build=None, which sets summary_rev=NULL
        # on non-strategy_spec writes and leaves lineage_index untouched) and reads/writes
        # (record_id, payload, updated_at) without referencing lineage_index.
        seq_ident, fn_build, fn_tg, _tg_name, _idx_name = store._summary_index_identifiers()
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute(
                pg_store._strategy_summary_index_trigger_function_sql(
                    fn_tg, fn_build, seq_ident, fn_lineage_build=None
                )
            )
            rollback_entry = _entry(
                "reg-during-rollback",
                "execution_bundle",
                LIVE_STRATEGY_ID,
                lineage={"source_run_ids": ["run-rollback"]},
                tenant="tenant-dev",
            )
            conn.execute(
                f"""
                INSERT INTO "{schema}".entries (record_id, payload, updated_at)
                VALUES (%s, %s::jsonb, now())
                ON CONFLICT (record_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                (rollback_entry.registry_id, json.dumps(rollback_entry.to_dict())),
            )
            # Also update an existing strategy_spec and an existing execution_bundle during rollback
            updated_spec = copy.deepcopy(all_seed[0])
            updated_spec.metadata = {**updated_spec.metadata, "persona_id": "persona-rollback-edited"}
            updated_bundle = copy.deepcopy(all_seed[2])
            updated_bundle.metadata = {**updated_bundle.metadata, "capital_pool_id": "pool-rollback-edited"}
            for mutated in (updated_spec, updated_bundle):
                conn.execute(
                    f'UPDATE "{schema}".entries SET payload = %s::jsonb WHERE record_id = %s',
                    (json.dumps(mutated.to_dict()), mutated.registry_id),
                )
            readback = conn.execute(
                f'SELECT payload FROM "{schema}".entries WHERE record_id = %s',
                (rollback_entry.registry_id,),
            ).fetchone()
            assert readback is not None

        # Simulate roll-forward: _ensure_strategy_summary_index + _ensure_lineage_index
        # restores the shared trigger and re-backfills any row with NULL or stale lineage_index / summary_rev
        store._summary_index_ready = False
        store._ensure_lineage_index()
        roll_forward_report = store.last_lineage_index_backfill
        assert roll_forward_report is not None
        assert roll_forward_report["rows_updated"] == 3
        svc = RegistryService(store)
        vis = frozenset({"tenant-dev", BUILTIN_TENANT})
        g_spec = svc.lineage_graph(visible_tenants=vis, root_id="persona-rollback-edited", depth=1, limit=200)
        assert (updated_spec.registry_id, "persona-rollback-edited", "persona") in _edge_set(g_spec)
        g_bundle = svc.lineage_graph(visible_tenants=vis, root_id="pool-rollback-edited", depth=1, limit=200)
        assert (updated_bundle.registry_id, "pool-rollback-edited", "capital_pool") in _edge_set(g_bundle)
        g_new = svc.lineage_graph(visible_tenants=vis, root_id="reg-during-rollback", depth=1, limit=200)
        assert ("run-rollback", "reg-during-rollback", "source_run") in _edge_set(g_new)
    finally:
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@needs_pg
def test_real_pg_parity_and_8_concurrent_cold_reads(pg_lineage_registry):
    """AC1, AC2 & AC3: Real PostgreSQL store with stored ``lineage_index`` column
    matches in-memory ``RegistryStore`` and reference ``build_lineage_graph`` across
    registry id, strategy id, persona id, capital pool id, unknown root, no root,
    depth, limit, has_more, and tenant visibility; and 8 concurrent cold reads on a
    fresh ``PostgresRegistryStore`` perform only 1 cold ``lineage_index`` load and
    1 adjacency build (0 ``payload`` rescans).
    """
    store, schema = pg_lineage_registry
    mem_store = RegistryStore()

    for e in persona_family_entries("tenant-a") + live_persona_family_entries("tenant-a"):
        store.put(e)
        mem_store.put(e)
    for e in persona_family_entries("tenant-b"):
        e_b = copy.deepcopy(e)
        e_b.registry_id = "b-" + e_b.registry_id
        store.put(e_b)
        mem_store.put(e_b)

    pg_svc = RegistryService(store)
    mem_svc = RegistryService(mem_store)

    visible_a = frozenset({"tenant-a", BUILTIN_TENANT})
    visible_b = frozenset({"tenant-b", BUILTIN_TENANT})

    for vis in (visible_a, visible_b, None):
        for root in (
            None,
            f"strategy-persona-{FD}",
            f"artifact-persona-paper-{FD}",
            f"persona-{FD}",
            f"pool-{FD}",
            *LIVE_RECORDED_ROOTS,
            f"b-artifact-persona-paper-{FD}",
            "art_stg001_v320",
        ):
            for depth in (1, 3):
                for limit in (2, 200):
                    pg_ans = pg_svc.lineage_graph(
                        visible_tenants=vis, root_id=root, depth=depth, limit=limit
                    )
                    mem_ans = mem_svc.lineage_graph(
                        visible_tenants=vis, root_id=root, depth=depth, limit=limit
                    )
                    assert pg_ans == mem_ans

    # 8 concurrent cold reads against a fresh PostgresRegistryStore instance
    fresh_store = PostgresRegistryStore(
        dsn=_DSN,
        entries_table=f"{schema}.entries",
        receipts_table=f"{schema}.command_receipts",
        bootstrap=False,
    )
    fresh_store._ensure_lineage_index()
    fresh_store.invalidate_lineage_index_cache(full=True)
    fresh_store.lineage_index_sql_reads = 0
    fresh_store.lineage_index_cache_hits = 0
    fresh_store.lineage_payload_rescans = 0
    fresh_store.lineage_adjacency_builds = 0
    fresh_store.lineage_adjacency_cache_hits = 0
    fresh_svc = RegistryService(fresh_store)

    barrier = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        futures = [
            barrier.submit(
                fresh_svc.lineage_graph,
                visible_tenants=visible_a,
                root_id=LIVE_STRATEGY_ID,
                depth=3,
                limit=200,
            )
            for _ in range(8)
        ]
        results = [f.result() for f in futures]
    finally:
        barrier.shutdown(wait=True)

    for res in results[1:]:
        assert res == results[0]
    assert fresh_store.lineage_payload_rescans == 0
    assert fresh_store.lineage_index_sql_reads == 1
    assert fresh_store.lineage_index_cache_hits == 7
    assert fresh_store.lineage_adjacency_builds == 1
    assert fresh_store.lineage_adjacency_cache_hits == 7
