"""Stored candidate-index column, write-trigger, bounded backfill, and stamp-validated
Phase 1 cache tests for ``GET /api/registry/strategy-specs?view=strategy_summary_page``
(REGISTRY-SUMMARY-PAGE-NO-RESCAN-20261127).

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
    SUMMARY_INDEX_BACKFILL_BATCH_SIZE,
    SUMMARY_INDEX_COLUMN,
    SUMMARY_REV_COLUMN,
    PostgresRegistryStore,
    strategy_spec_summary_index_delta_sql,
    strategy_spec_summary_index_stamp_sql,
    strategy_spec_summary_index_stored_select_sql,
    strategy_spec_summary_index_stored_tenant_select_sql,
)
from .split_api import RegistryService
from .storage import (
    RegistryStore,
    build_strategy_summary_index_payload,
    project_strategy_spec_summary_page,
    strategy_spec_summary_index_row,
    strategy_spec_summary_row,
)
from .test_strategy_summary_page_bounded import (
    STUB_RULE_TEST_PERSONA_IDS,
    STUB_RULE_VECTORS,
    _realistic_strategy_spec_payload,
)

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_pg = pytest.mark.skipif(not _DSN, reason="needs TEST_DATABASE_URL")


def test_phase1_stored_index_sql_never_evaluates_payload_json_expressions():
    """AC1: The Phase 1 SQL queries (cold stored-column select, cheap stamp check,
    incremental delta select, and uncached tenant select) read only stored
    ``summary_index``/``summary_rev`` columns and never reference or detoast ``payload``.
    """
    table = '"registry"."entries"'
    stamp_sql = strategy_spec_summary_index_stamp_sql(table)
    stored_sql = strategy_spec_summary_index_stored_select_sql(table)
    delta_sql = strategy_spec_summary_index_delta_sql(table)
    tenant_sql, tenant_params = strategy_spec_summary_index_stored_tenant_select_sql(
        table, frozenset({"tenant-dev", BUILTIN_TENANT})
    )

    for sql in (stamp_sql, stored_sql, delta_sql, tenant_sql):
        assert "payload" not in sql, f"Phase 1 SQL must not reference payload: {sql}"
        assert SUMMARY_INDEX_COLUMN in sql

    assert "count(*)" in stamp_sql.lower()
    assert f"max({SUMMARY_REV_COLUMN})" in stamp_sql.lower()
    assert f"sum({SUMMARY_REV_COLUMN})" in stamp_sql.lower()
    assert f"{SUMMARY_REV_COLUMN} > %s" in delta_sql
    assert tenant_params == (["__builtin__", "tenant-dev"],)


def test_ensure_strategy_summary_index_ddl_and_bounded_backfill_statements():
    """AC1 & AC4: Bootstrap DDL adds ``summary_index`` and ``summary_rev`` columns
    idempotently, creates the trigger and partial index, and runs a bounded batched
    backfill with ``FOR UPDATE SKIP LOCKED``.
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
    store._ensure_strategy_summary_index()

    sql_texts = [s for s, _ in executed]
    assert any("ADD COLUMN IF NOT EXISTS summary_index JSONB" in s for s in sql_texts)
    assert any("ADD COLUMN IF NOT EXISTS summary_rev BIGINT" in s for s in sql_texts)
    assert any("CREATE SEQUENCE IF NOT EXISTS" in s for s in sql_texts)
    assert any("CREATE OR REPLACE FUNCTION \"registry\".\"pantheon_registry_summary_index_build\"" in s for s in sql_texts)
    assert any("CREATE OR REPLACE TRIGGER \"entries_strategy_summary_index_tg\"" in s for s in sql_texts)
    assert any("CREATE INDEX IF NOT EXISTS \"entries_strategy_summary_idx\"" in s for s in sql_texts)
    backfill_calls = [(s, p) for s, p in executed if s.startswith("WITH todo AS (")]
    assert len(backfill_calls) == 1
    assert "FOR UPDATE SKIP LOCKED" in backfill_calls[0][0]
    assert backfill_calls[0][1] == (SUMMARY_INDEX_BACKFILL_BATCH_SIZE,)
    assert store.last_summary_index_backfill is not None
    assert store.last_summary_index_backfill["rows_updated"] == 0
    assert store.last_summary_index_backfill["batches_executed"] == 1


def test_build_strategy_summary_index_payload_matches_index_row_and_filters_non_strategy_spec():
    """AC1 & AC2: ``build_strategy_summary_index_payload`` extracts the exact candidate-index
    fields for ``strategy_spec`` entries (including legacy JSON-string payloads) and returns
    ``None`` for other artifact types.
    """
    spec = _realistic_strategy_spec_payload(
        "strat-idx-unit",
        version="1.2.0",
        lifecycle_state="approved",
        persona_ids=["p-alpha", "p-beta"],
    )
    spec["metadata"]["data_scope"] = "production"
    entry = RegistryEntry(
        registry_id="reg-strat-idx-unit-v2",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="strat-idx-unit",
        version="1.2.0",
        artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=["run-idx"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum="sha256:reg-strat-idx-unit-v2",
        metadata={
            "strategy_spec": spec,
            "title": spec["title"],
            "persona_ids": ["p-alpha", "p-beta"],
            "data_scope": "production",
        },
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-02T00:00:00Z",
        owner_tenant="tenant-dev",
    )

    built = build_strategy_summary_index_payload(entry.to_dict())
    assert built is not None
    assert built["owner_tenant"] == "tenant-dev"

    # Legacy JSON-string payload unwraps identically
    built_from_str = build_strategy_summary_index_payload(json.dumps(entry.to_dict()))
    assert built_from_str == built

    # Decoded row matches strategy_spec_summary_index_row across include_title/include_scope combinations
    decoded = pg_store._decode_stored_summary_index_row(json.dumps(built))
    for inc_title in (False, True):
        for inc_scope in (False, True):
            projected = pg_store.project_cached_summary_index_row(
                decoded, include_title=inc_title, include_scope=inc_scope
            )
            expected = strategy_spec_summary_index_row(
                entry, include_title=inc_title, include_scope=inc_scope
            )
            assert dict(projected) == expected

    # Non-strategy_spec entries return None
    non_spec_dict = entry.to_dict()
    non_spec_dict["artifact_type"] = "execution_bundle"
    assert build_strategy_summary_index_payload(non_spec_dict) is None


def test_postgres_phase1_cache_hit_and_incremental_delta_update_without_payload_rescan():
    """AC1 & AC2 (unit proof): ``PostgresRegistryStore.list_strategy_spec_summary_index_rows``
    loads from ``summary_index`` once on cold start, uses the 1-row cheap stamp query on
    repeated reads (0 rows fetched), and fetches only the delta ``summary_rev > prev_max_rev``
    row after a write without rescanning unchanged rows or touching ``payload``.
    """
    e1 = RegistryEntry(
        registry_id="reg-s1-v1",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="strat-s1",
        version="1.0.0",
        artifact_state=ArtifactState.DRAFT,
        lineage=Lineage(source_run_ids=["r1"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum="sha256:s1-v1",
        metadata={
            "strategy_spec": _realistic_strategy_spec_payload(
                "strat-s1", version="1.0.0", lifecycle_state="draft", persona_ids=["p-live"]
            )
        },
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T01:00:00Z",
        owner_tenant="tenant-a",
    )
    e2 = RegistryEntry(
        registry_id="reg-s2-v1",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="strat-s2",
        version="1.0.0",
        artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=["r2"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum="sha256:s2-v1",
        metadata={
            "strategy_spec": _realistic_strategy_spec_payload(
                "strat-s2", version="1.0.0", lifecycle_state="approved", persona_ids=["p-live"]
            )
        },
        created_at="2026-10-02T00:00:00Z",
        updated_at="2026-10-02T01:00:00Z",
        owner_tenant="tenant-a",
    )

    db_rows: dict[str, tuple[int, dict[str, Any]]] = {
        e1.registry_id: (1, build_strategy_summary_index_payload(e1.to_dict())),
        e2.registry_id: (2, build_strategy_summary_index_payload(e2.to_dict())),
    }
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
            assert "payload" not in sql, f"Phase 1 query referenced payload: {sql}"
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
            if "FROM \"registry\".\"entries\" WHERE summary_index IS NOT NULL" in sql:
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
    store._summary_index_ready = True

    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    # 1. Cold read: 1 SQL query fetching the 2 stored summary_index rows
    rows_1 = store.list_strategy_spec_summary_index_rows(
        visible_tenants=visible, include_title=True, include_scope=True
    )
    assert len(rows_1) == 2
    assert store.phase1_index_sql_reads == 1
    assert store.phase1_index_rows_fetched == 2
    assert store.phase1_index_cache_hits == 0
    assert store.phase1_payload_rescans == 0

    # 2. Warm read: only 1 cheap stamp query, 0 rows fetched
    rows_2 = store.list_strategy_spec_summary_index_rows(
        visible_tenants=visible, include_title=True, include_scope=True
    )
    assert len(rows_2) == 2
    assert store.phase1_index_sql_reads == 1
    assert store.phase1_index_cache_hits == 1
    assert store.phase1_index_rows_fetched == 0

    # 3. Simulate a write (e1 advances from draft to approved -> summary_rev bumped to 3)
    e1_approved = copy.deepcopy(e1)
    e1_approved.artifact_state = ArtifactState.APPROVED
    e1_approved.metadata["strategy_spec"]["lifecycle_state"] = "approved"
    e1_approved.updated_at = "2026-10-03T00:00:00Z"
    db_rows[e1.registry_id] = (3, build_strategy_summary_index_payload(e1_approved.to_dict()))

    rows_3 = store.list_strategy_spec_summary_index_rows(
        visible_tenants=visible, include_title=True, include_scope=True
    )
    assert len(rows_3) == 2
    assert store.phase1_index_sql_reads == 1
    assert store.phase1_index_delta_reads == 1
    assert store.phase1_index_rows_fetched == 1
    page = project_strategy_spec_summary_page(rows_3, state="approved", page_size=20)
    assert page["total"] == 2
    assert page["state_counts"]["approved"] == 2
    assert page["state_counts"]["draft"] == 0


@pytest.fixture()
def pg_summary_registry():
    psycopg = pytest.importorskip("psycopg")
    schema = f"ssp_idx_{uuid.uuid4().hex[:10]}"
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
def test_real_pg_writes_update_summary_index_and_are_visible_in_next_read(pg_summary_registry):
    """AC2: Against real PostgreSQL, proves that every write path on ``strategy_spec`` entries:
    1) registering a new version,
    2) advancing artifact_state (draft -> candidate -> approved),
    3) retiring a strategy,
    4) changing persona binding via metadata CAS, and
    5) changing ``data_scope`` via metadata CAS (causing ``hide_stubs=true`` classification to flip)
    updates the stored ``summary_index`` / ``summary_rev`` columns and is immediately visible in
    the very next ``strategy_summary_page`` read.
    """
    store, _schema = pg_summary_registry
    svc = RegistryService(store)
    actor = {"actor_id": "op-1", "roles": ["operator"], "tenant": "tenant-a", "token_kind": "jwt"}
    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    def _make_create(
        sid: str,
        version: str,
        *,
        state: ArtifactState = ArtifactState.DRAFT,
        personas: list[str] | None = None,
        data_scope: str | None = None,
        parent_ids: list[str] | None = None,
    ) -> RegistryEntryCreate:
        spec = _realistic_strategy_spec_payload(
            sid, version=version, lifecycle_state=state.value, persona_ids=personas or ["p-live-1"]
        )
        meta: dict[str, Any] = {
            "strategy_spec": spec,
            "title": spec["title"],
            "persona_ids": list(personas or ["p-live-1"]),
        }
        if data_scope is not None:
            meta["data_scope"] = data_scope
            spec["metadata"]["data_scope"] = data_scope
        return RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version=version,
            artifact_state=state,
            lineage=Lineage(
                source_run_ids=[f"run-{sid}-{version}"],
                parent_registry_ids=parent_ids,
            ),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{sid}:{version}",
            metadata=meta,
        )

    # Seed initial v1.0.0 draft strategy
    svc.register(_make_create("strat-write-1", "1.0.0", personas=["p-live-1"]), "reg-w1-v1", actor=actor)
    svc.register(_make_create("strat-write-2", "1.0.0", state=ArtifactState.CANDIDATE, personas=["p-live-1"]), "reg-w2-v1", actor=actor)

    page0 = svc.strategy_spec_summary_page(
        visible_tenants=visible, page_size=20, hide_stubs=True, hide_persona_ids=["p-test-hidden"]
    )
    assert page0["total"] == 2
    assert page0["state_counts"]["draft"] == 1
    assert page0["state_counts"]["review"] == 1
    by_sid0 = {item["strategy_id"]: item for item in page0["items"]}
    assert by_sid0["strat-write-1"]["current_spec_version"] == "1.0.0"
    assert by_sid0["strat-write-1"]["lifecycle_state"] == "draft"
    assert store.phase1_payload_rescans == 0

    # 1. Write new version v1.1.0 for strat-write-1 (candidate state) -> visible in next read
    svc.register_strategy_spec_revision(
        _make_create(
            "strat-write-1",
            "1.1.0",
            state=ArtifactState.CANDIDATE,
            personas=["p-live-1"],
            parent_ids=["reg-w1-v1"],
        ),
        "reg-w1-v2",
        validate_lineage=lambda existing: None,
        actor=actor,
    )
    page1 = svc.strategy_spec_summary_page(
        visible_tenants=visible, page_size=20, hide_stubs=True, hide_persona_ids=["p-test-hidden"]
    )
    assert store.phase1_index_delta_reads >= 1
    assert store.phase1_index_rows_fetched == 1
    by_sid1 = {item["strategy_id"]: item for item in page1["items"]}
    assert by_sid1["strat-write-1"]["current_spec_version"] == "1.1.0"
    assert by_sid1["strat-write-1"]["current_spec_version_id"] == "reg-w1-v2"
    assert by_sid1["strat-write-1"]["version_count"] == 2
    assert by_sid1["strat-write-1"]["lifecycle_state"] == "candidate"
    assert page1["state_counts"]["review"] == 2
    assert page1["state_counts"]["draft"] == 0

    # 2. Persona binding change via update_metadata CAS -> visible in next read
    cur_v2 = svc.get("reg-w1-v2").entry
    new_meta_persona = copy.deepcopy(cur_v2.metadata)
    new_meta_persona["persona_ids"] = ["p-live-2"]
    new_meta_persona["personaIds"] = ["p-live-2"]
    store.commit_metadata_cas(
        registry_id="reg-w1-v2",
        base_snapshot=cur_v2.to_dict(),
        new_metadata=new_meta_persona,
        command_key="cmd-persona-change",
        actor=actor,
    )
    # Note: strategy_spec.persona_ids takes precedence over metadata.persona_ids if present;
    # test a version or metadata where persona_ids changes to p-test-hidden:
    svc.register_strategy_spec_revision(
        _make_create(
            "strat-write-1",
            "1.2.0",
            state=ArtifactState.CANDIDATE,
            personas=["p-test-hidden"],
            parent_ids=["reg-w1-v2"],
        ),
        "reg-w1-v3",
        validate_lineage=lambda existing: None,
        actor=actor,
    )
    page_persona_hidden = svc.strategy_spec_summary_page(
        visible_tenants=visible, page_size=20, hide_stubs=True, hide_persona_ids=["p-test-hidden"]
    )
    assert page_persona_hidden["total"] == 1
    assert [i["strategy_id"] for i in page_persona_hidden["items"]] == ["strat-write-2"]
    assert page_persona_hidden["hidden"]["by_reason"]["test_persona_probe"] == 1

    # Rebind strat-write-1 to p-live-2 in v1.3.0 -> becomes visible again under persona_id=p-live-2
    svc.register_strategy_spec_revision(
        _make_create(
            "strat-write-1",
            "1.3.0",
            state=ArtifactState.CANDIDATE,
            personas=["p-live-2"],
            parent_ids=["reg-w1-v3"],
        ),
        "reg-w1-v4",
        validate_lineage=lambda existing: None,
        actor=actor,
    )
    page_p2 = svc.strategy_spec_summary_page(
        visible_tenants=visible,
        persona_id="p-live-2",
        page_size=20,
        hide_stubs=True,
        hide_persona_ids=["p-test-hidden"],
    )
    assert page_p2["total"] == 1
    assert page_p2["items"][0]["strategy_id"] == "strat-write-1"
    assert page_p2["items"][0]["persona_ids"] == ["p-live-2"]

    # 3. data_scope change via update_metadata CAS (setting data_scope="test") -> hidden in next read
    cur_v4 = svc.get("reg-w1-v4").entry
    meta_scope_test = copy.deepcopy(cur_v4.metadata)
    meta_scope_test["data_scope"] = "test"
    svc.update_metadata(
        "reg-w1-v4",
        expected_metadata=cur_v4.metadata,
        new_metadata=meta_scope_test,
        command_key="cmd-scope-test",
        actor=actor,
    )
    page_scope_hidden = svc.strategy_spec_summary_page(
        visible_tenants=visible, page_size=20, hide_stubs=True, hide_persona_ids=["p-test-hidden"]
    )
    assert page_scope_hidden["total"] == 1
    assert [i["strategy_id"] for i in page_scope_hidden["items"]] == ["strat-write-2"]
    assert page_scope_hidden["hidden"]["by_reason"]["test_persona_probe"] == 1

    # Change data_scope back to "production" via update_metadata CAS -> visible again in next read
    cur_v4_test = svc.get("reg-w1-v4").entry
    meta_scope_prod = copy.deepcopy(cur_v4_test.metadata)
    meta_scope_prod["data_scope"] = "production"
    svc.update_metadata(
        "reg-w1-v4",
        expected_metadata=cur_v4_test.metadata,
        new_metadata=meta_scope_prod,
        command_key="cmd-scope-prod",
        actor=actor,
    )
    page_scope_prod = svc.strategy_spec_summary_page(
        visible_tenants=visible, page_size=20, hide_stubs=True, hide_persona_ids=["p-test-hidden"]
    )
    assert page_scope_prod["total"] == 2

    # 4. State change & retire via advance_artifact_state (candidate -> retired) -> visible in next read
    cur_w2 = svc.get("reg-w2-v1").entry
    svc.advance_artifact_state(
        "reg-w2-v1",
        ArtifactState.RETIRED,
        command_key="cmd-retire-w2",
        actor=actor,
        expected_artifact_state=cur_w2.artifact_state,
        expected_version=cur_w2.version,
        expected_updated_at=cur_w2.updated_at,
    )
    page_after_retire = svc.strategy_spec_summary_page(
        visible_tenants=visible, page_size=20, hide_stubs=True, hide_persona_ids=["p-test-hidden"]
    )
    assert page_after_retire["total"] == 1
    assert [i["strategy_id"] for i in page_after_retire["items"]] == ["strat-write-1"]
    assert page_after_retire["state_counts"]["retired"] == 1
    assert page_after_retire["visible_state_counts"]["retired"] == 1

    page_retired_only = svc.strategy_spec_summary_page(
        visible_tenants=visible, state="retired", page_size=20
    )
    assert page_retired_only["total"] == 1
    assert page_retired_only["items"][0]["strategy_id"] == "strat-write-2"
    assert store.phase1_payload_rescans == 0


@needs_pg
def test_real_pg_idempotent_bounded_backfill_populates_existing_rows_without_touching_updated_at():
    """AC4: Existing rows written before the ``summary_index`` column/trigger existed
    are populated by ``backfill_strategy_summary_index`` in bounded batches without
    changing ``updated_at``, and re-running the backfill is an idempotent 0-row no-op.
    """
    import psycopg

    schema = f"ssp_bf_{uuid.uuid4().hex[:10]}"
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
        # Seed 25 strategy_spec rows (including 1 legacy JSON-string payload) + 5 model_artifact rows
        for i in range(25):
            sid = f"strat-bf-{i:03d}"
            rid = f"reg-{sid}-v1"
            spec = _realistic_strategy_spec_payload(sid, version="1.0.0", lifecycle_state="draft", persona_ids=["p-live"])
            entry = RegistryEntry(
                registry_id=rid,
                artifact_type=ArtifactType.STRATEGY_SPEC,
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.DRAFT,
                lineage=Lineage(source_run_ids=["run-bf"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                checksum=f"sha256:{rid}",
                metadata={"strategy_spec": spec},
                created_at="2026-10-01T00:00:00Z",
                updated_at="2026-10-01T00:00:00Z",
                owner_tenant="tenant-dev",
            )
            if i == 24:
                conn.execute(
                    f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, to_jsonb(%s::text))',
                    (rid, json.dumps(entry.to_dict())),
                )
            else:
                conn.execute(
                    f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, %s::jsonb)',
                    (rid, json.dumps(entry.to_dict())),
                )
        for j in range(5):
            rid = f"model-bf-{j}"
            conn.execute(
                f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, %s::jsonb)',
                (rid, json.dumps({"registry_id": rid, "artifact_type": "model_artifact", "strategy_id": "m", "version": "1.0.0", "artifact_state": "approved"})),
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
        # Run DDL without default 1000-batch backfill by testing explicit small batch_size=10
        store._ensure_strategy_summary_index()
        first_report = store.last_summary_index_backfill
        assert first_report is not None
        assert first_report["rows_updated"] == 25

        # Idempotent second run updates 0 rows
        second_report = store.backfill_strategy_summary_index(batch_size=10)
        assert second_report["rows_updated"] == 0
        assert second_report["batches_executed"] == 1

        # Verify updated_at was not modified and non-strategy_spec rows remain summary_index IS NULL
        with psycopg.connect(_DSN) as conn:
            non_null_cnt = conn.execute(
                f'SELECT count(*) FROM "{schema}".entries WHERE summary_index IS NOT NULL'
            ).fetchone()[0]
            null_cnt = conn.execute(
                f'SELECT count(*) FROM "{schema}".entries WHERE summary_index IS NULL'
            ).fetchone()[0]
            distinct_ts = conn.execute(
                f'SELECT count(DISTINCT updated_at) FROM "{schema}".entries'
            ).fetchone()[0]
        assert non_null_cnt == 25
        assert null_cnt == 5
        assert distinct_ts == 1

        svc = RegistryService(store)
        page = svc.strategy_spec_summary_page(
            visible_tenants=frozenset({"tenant-dev", BUILTIN_TENANT}),
            state="draft",
            page_size=10,
        )
        assert page["total"] == 25
        assert len(page["items"]) == 10
        assert store.full_summary_rows_extracted == 10
    finally:
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@needs_pg
def test_real_pg_parity_across_stub_vectors_and_8_concurrent_cold_reads(pg_summary_registry):
    """AC1, AC2 & AC3: Real PostgreSQL store with stored ``summary_index`` column
    matches in-memory and legacy projection across all 17 ``STUB_RULE_VECTORS``,
    and 8 concurrent cold reads on a fresh ``PostgresRegistryStore`` perform
    only 1 cold ``summary_index`` load (0 ``payload`` rescans) and return identical answers.
    """
    store, schema = pg_summary_registry
    mem_store = RegistryStore()
    actor = {"actor_id": "op-parity", "roles": ["operator"], "tenant": "tenant-dev", "token_kind": "jwt"}

    for idx, vec in enumerate(STUB_RULE_VECTORS):
        sid = f"{vec['strategy_id']}-v{idx:02d}" if not vec["strategy_id"].startswith("strat-tw-official") else f"{vec['strategy_id']}-{idx:02d}"
        rid = f"reg-vec-{idx:02d}"
        spec = _realistic_strategy_spec_payload(
            sid,
            version="1.0.0",
            lifecycle_state="draft",
            persona_ids=list(vec["persona_ids"]),
        )
        spec["title"] = vec["title"]
        spec["name"] = vec["title"]
        meta: dict[str, Any] = {
            "strategy_spec": spec,
            "title": vec["title"],
            "name": vec["title"],
            "persona_ids": list(vec["persona_ids"]),
        }
        if vec["data_scope"]:
            meta["data_scope"] = vec["data_scope"]
            spec["metadata"]["data_scope"] = vec["data_scope"]
        create_payload = RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            lineage=Lineage(source_run_ids=[f"run-vec-{idx}"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid}",
            metadata=meta,
        )
        created_entry = store.create(create_payload, rid, actor=actor)
        mem_store.put(created_entry)

    visible = frozenset({"tenant-dev", BUILTIN_TENANT})
    pg_svc = RegistryService(store)
    mem_svc = RegistryService(mem_store)

    for kwargs in (
        {"visible_tenants": visible, "state": "draft", "page_size": 50},
        {
            "visible_tenants": visible,
            "state": "draft",
            "page_size": 50,
            "hide_stubs": True,
            "hide_persona_ids": list(STUB_RULE_TEST_PERSONA_IDS),
        },
        {
            "visible_tenants": visible,
            "state": "draft",
            "page_size": 50,
            "hide_stubs": True,
            "hide_persona_ids": [],
        },
    ):
        pg_ans = pg_svc.strategy_spec_summary_page(**kwargs)
        mem_ans = mem_svc.strategy_spec_summary_page(**kwargs)
        assert pg_ans == mem_ans

    # Test 8 concurrent cold reads against a fresh PostgresRegistryStore instance
    fresh_store = PostgresRegistryStore(
        dsn=_DSN,
        entries_table=f"{schema}.entries",
        receipts_table=f"{schema}.command_receipts",
        bootstrap=False,
    )
    fresh_Store_ready = fresh_store._ensure_strategy_summary_index()  # noqa: F841
    fresh_store.invalidate_strategy_summary_index_cache(full=True)
    fresh_store.phase1_index_sql_reads = 0
    fresh_store.phase1_index_cache_hits = 0
    fresh_store.phase1_payload_rescans = 0
    fresh_svc = RegistryService(fresh_store)

    barrier = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        futures = [
            barrier.submit(
                fresh_svc.strategy_spec_summary_page,
                visible_tenants=visible,
                state="draft",
                page_size=50,
                hide_stubs=True,
                hide_persona_ids=list(STUB_RULE_TEST_PERSONA_IDS),
            )
            for _ in range(8)
        ]
        results = [f.result() for f in futures]
    finally:
        barrier.shutdown(wait=True)

    for res in results[1:]:
        assert res == results[0]
    assert fresh_store.phase1_payload_rescans == 0
    assert fresh_store.phase1_index_sql_reads == 1
    assert fresh_store.phase1_index_cache_hits == 7

