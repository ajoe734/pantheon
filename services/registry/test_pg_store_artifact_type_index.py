"""ALPHA-TEMPLATE-READ-FAST-20261126: type-filtered registry read goes through an artifact_type index.

Unit tests run everywhere; the real-Postgres tests run when ``TEST_DATABASE_URL`` is set.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any, List

import pytest

from services.registry import pg_store
from services.registry.alpha_template import build_alpha_template_registry_payload, load_library_templates
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore


def test_artifact_type_index_is_additive_forward_only():
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
    store._ensure_artifact_type_index()
    type_idx = [s for s in executed if "entries_artifact_type_idx" in s]
    assert len(executed) == 1
    assert len(type_idx) == 1
    stmt = type_idx[0]
    assert stmt.upper().startswith("CREATE INDEX IF NOT EXISTS")
    assert "((payload ->> 'artifact_type'))" in stmt
    assert not any(w in stmt.upper() for w in ("ALTER ", "DROP ", "ADD COLUMN", "UNIQUE", "CONCURRENTLY"))


def test_type_filtered_sql_matches_index_expression_and_never_scans_unfiltered():
    sql = pg_store.artifact_type_select_sql('"registry"."entries"')
    # The object branch compares exactly the indexed expression, so the planner can use the index.
    assert f"WHERE {pg_store.ARTIFACT_TYPE_INDEX_SQL} = %s" in sql
    # The legacy JSON-string branch is restricted by the partial legacy index predicate.
    assert "WHERE jsonb_typeof(payload) = 'string'" in sql
    assert sql.count("%s") == 2
    # No whole-table unwrap (payload || '{}') over every row as in the generic visible_sql path.
    assert "|| '{}'" not in sql


def test_list_by_artifact_type_decodes_only_returned_rows():
    calls: list[tuple[str, Any]] = []
    entry = _alpha_entry_dicts()[0]

    class _Cursor:
        def fetchall(self):
            return [(json.dumps(entry),)]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            calls.append((sql, params))
            return _Cursor()

    store = pg_store.PostgresRegistryStore.__new__(pg_store.PostgresRegistryStore)
    store._entries = SimpleNamespace(table='"registry"."entries"', table_name="registry.entries", _connect=lambda: _Conn())
    out = store.list_by_artifact_type("alpha_template")
    assert [e.registry_id for e in out] == [entry["registry_id"]]
    assert calls == [(pg_store.artifact_type_select_sql('"registry"."entries"'), ("alpha_template", "alpha_template"))]


def _alpha_entry_dicts(n: int = 3) -> list[dict]:
    svc = RegistryService(RegistryStore())
    out = []
    for template in load_library_templates()[:n]:
        registry_id, payload = build_alpha_template_registry_payload(template)
        view, _ = svc.register_if_absent(payload, registry_id)
        out.append(view.entry.to_dict())
    return out


_DSN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not _DSN, reason="needs TEST_DATABASE_URL")


@pytest.fixture()
def pg_registry():
    psycopg = pytest.importorskip("psycopg")
    schema = f"atrf_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    store = pg_store.PostgresRegistryStore(
        dsn=_DSN, entries_table=f"{schema}.entries", receipts_table=f"{schema}.command_receipts",
    )
    try:
        yield store, schema
    finally:
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _seed(store, schema: str, *, others: int, pad: int) -> list[str]:
    import psycopg

    alphas = _alpha_entry_dicts(5)
    base = copy.deepcopy(alphas[0])
    with psycopg.connect(_DSN, autocommit=True) as conn:
        for i in range(others):
            row = copy.deepcopy(base)
            row.update(registry_id=f"spec-{i}", artifact_type="strategy_spec", strategy_id=f"s-{i}")
            row["metadata"] = {"blob": uuid.uuid4().hex * (pad // 32)}
            conn.execute(
                f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, %s::jsonb)',
                (row["registry_id"], json.dumps(row)),
            )
        for row in alphas[:4]:
            conn.execute(
                f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, %s::jsonb)',
                (row["registry_id"], json.dumps(row)),
            )
        # A legacy JSON-string payload of the wanted type is still returned.
        legacy = alphas[4]
        conn.execute(
            f'INSERT INTO "{schema}".entries (record_id, payload) VALUES (%s, to_jsonb(%s::text))',
            (legacy["registry_id"], json.dumps(legacy)),
        )
        conn.execute(f'ANALYZE "{schema}".entries')
    return [r["registry_id"] for r in alphas]


@needs_pg
def test_real_pg_type_filtered_read_uses_index_and_returns_only_that_type(pg_registry):
    import psycopg

    store, schema = pg_registry
    wanted_ids = _seed(store, schema, others=2000, pad=4096)
    got = store.list_by_artifact_type("alpha_template")
    assert [e.registry_id for e in got] == wanted_ids  # updated_at ascending, legacy string row unwrapped
    assert all(e.artifact_type.value == "alpha_template" for e in got)

    with psycopg.connect(_DSN) as conn:
        plan = "\n".join(
            r[0] for r in conn.execute(
                "EXPLAIN " + pg_store.artifact_type_select_sql(f"{schema}.entries"),
                ("alpha_template", "alpha_template"),
            ).fetchall()
        )
        idx = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = 'entries_artifact_type_idx'",
            (schema,),
        ).fetchone()
    assert idx is not None and "(payload ->> 'artifact_type'::text)" in idx[0]
    assert "entries_artifact_type_idx" in plan
    assert "Seq Scan" not in plan, plan


@needs_pg
def test_real_pg_previous_release_read_and_rebootstrap_keep_working(pg_registry):
    store, schema = pg_registry
    wanted_ids = _seed(store, schema, others=50, pad=256)
    # The previous release's type-filtered read (generic visible_sql path) still answers on the indexed table.
    rows = store._entries.list_all_selected(
        "(payload)::text", visible_sql="payload ->> 'artifact_type' = %s", keep_params=("alpha_template",),
    )
    assert [json.loads(r[0])["registry_id"] for r in rows] == wanted_ids
    # Bootstrapping again is a no-op (CREATE INDEX IF NOT EXISTS runs once).
    again = pg_store.PostgresRegistryStore(
        dsn=_DSN, entries_table=f"{schema}.entries", receipts_table=f"{schema}.command_receipts",
    )
    assert [e.registry_id for e in again.list_by_artifact_type("alpha_template")] == wanted_ids
