"""insert_if_absent(unique_fields=...) resolves identity inside Postgres
(REGISTRY-SEED-UNIQUE-SCAN-20261110).  Gated on TEST_DATABASE_URL."""
from __future__ import annotations

import json
import os
import threading
from uuid import uuid4

import pytest

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

FIELDS = ("strategy_id", "version", "artifact_type")


@pytest.fixture
def store():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres unique-field proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"uq_{uuid4().hex}"
    s = PostgresJsonOwnerStore(dsn=dsn, table=f"{schema}.entries", owner_service="test")
    try:
        yield s
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _row(**kw):
    base = {"strategy_id": "s", "version": "1", "artifact_type": "alpha_template", "n": 0}
    base.update(kw)
    return base


def _raw_insert(store, rid, payload, updated_at="2026-01-01T00:00:00Z", as_string=False):
    body = json.dumps(json.dumps(payload) if as_string else payload)
    with store._connect() as conn:
        conn.execute(
            f"INSERT INTO {store.table} (record_id, payload, updated_at) VALUES (%s, %s::jsonb, %s)",
            (rid, body, updated_at),
        )


def test_new_insert_id_hit_and_unique_hit(store):
    created, canon = store.insert_if_absent("a", _row(n=1), unique_fields=FIELDS)
    assert created is True and canon == _row(n=1)
    # id hit returns the stored payload even if the new payload differs
    created, canon = store.insert_if_absent("a", _row(n=2), unique_fields=FIELDS)
    assert (created, canon) == (False, _row(n=1))
    # unique hit under a different id returns the existing canonical payload
    created, canon = store.insert_if_absent("b", _row(n=3), unique_fields=FIELDS)
    assert (created, canon) == (False, _row(n=1))
    assert store.get("b") is None
    # different identity inserts
    created, _ = store.insert_if_absent("c", _row(version="2"), unique_fields=FIELDS)
    assert created is True


def test_no_unique_fields_only_id_identity(store):
    assert store.insert_if_absent("a", _row())[0] is True
    assert store.insert_if_absent("b", _row())[0] is True


def test_oldest_by_updated_at_wins_among_shared_identity(store):
    _raw_insert(store, "newer", _row(n=2), "2026-03-01T00:00:00Z")
    _raw_insert(store, "oldest", _row(n=1), "2026-01-01T00:00:00Z")
    _raw_insert(store, "middle", _row(n=3), "2026-02-01T00:00:00Z")
    created, canon = store.insert_if_absent("z", _row(n=9), unique_fields=FIELDS)
    assert (created, canon["n"]) == (False, 1)


def test_unique_lookup_never_returns_table_rows_to_python(store):
    for i in range(200):
        _raw_insert(store, f"r{i}", _row(strategy_id=f"s{i}"))
    fetched = []
    real_connect = store._connect

    class _Cur:
        def __init__(self, cur):
            self._cur = cur

        def fetchone(self):
            row = self._cur.fetchone()
            fetched.append(1 if row is not None else 0)
            return row

        def fetchall(self):
            rows = self._cur.fetchall()
            fetched.append(len(rows))
            return rows

    class _Conn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *a, **k):
            return _Cur(self._conn.execute(*a, **k))

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

    store._connect = lambda: _Conn(real_connect())
    created, canon = store.insert_if_absent("new", _row(strategy_id="s77"), unique_fields=FIELDS)
    assert (created, canon["strategy_id"]) == (False, "s77")
    assert sum(fetched) <= 2, fetched  # id probe (0) + the single colliding row
    fetched.clear()
    created, _ = store.insert_if_absent("brand", _row(strategy_id="nope"), unique_fields=FIELDS)
    assert created is True
    assert sum(fetched) == 0, fetched


def test_concurrent_callers_same_identity_different_ids_one_winner(store):
    results = []

    def run(i):
        results.append(store.insert_if_absent(f"id{i}", _row(n=i), unique_fields=FIELDS))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r[0]]
    assert len(winners) == 1
    assert {r[1]["n"] for r in results} == {winners[0][1]["n"]}
    assert len(store.list_all()) == 1


@pytest.mark.parametrize(
    "stored,probe,expected_hit",
    [
        ("k", "k", True),
        ("k", "K", False),
        (1, 1.0, True),  # numbers: 1 == 1.0
        (1.5, 1.5, True),
        (1, 2, False),
        (1, "1", False),
        (None, None, True),
        (True, 1, True),  # Python == semantics kept for bool/number
        (1, True, True),
        (False, 0, True),
        (True, 2, False),
        ({"a": 1}, {"a": 1}, True),
        ([1, 2], [2, 1], False),
    ],
)
def test_equality_matches_python_semantics(store, stored, probe, expected_hit):
    _raw_insert(store, "old", {"k": stored, "x": 1})
    created, _ = store.insert_if_absent("new", {"k": probe, "x": 1}, unique_fields=("k",))
    assert created is (not expected_hit)


def test_missing_stored_field_equals_null_probe(store):
    _raw_insert(store, "old", {"x": 1})
    created, canon = store.insert_if_absent("new", {"k": None, "x": 2}, unique_fields=("k",))
    assert (created, canon) == (False, {"x": 1})
    created, _ = store.insert_if_absent("new2", {"k": "v", "x": 2}, unique_fields=("k",))
    assert created is True


def test_legacy_string_payload_still_collides_and_oldest_wins(store):
    _raw_insert(store, "legacy", _row(n=1), "2026-01-01T00:00:00Z", as_string=True)
    _raw_insert(store, "obj", _row(n=2), "2026-02-01T00:00:00Z")
    created, canon = store.insert_if_absent("z", _row(n=9), unique_fields=FIELDS)
    assert (created, canon["n"]) == (False, 1)


def test_non_object_rows_ignored(store):
    _raw_insert(store, "arr", [1, 2])
    created, _ = store.insert_if_absent("z", _row(), unique_fields=FIELDS)
    assert created is True


def test_index_creation_failure_falls_back_to_correct_scan(store, monkeypatch):
    from services.foundation import postgres_json_store as mod

    monkeypatch.setattr(mod, "_UNIQUE_INDEX_READY", set())
    monkeypatch.setattr(
        PostgresJsonOwnerStore,
        "_ensure_unique_lookup_index",
        lambda self, conn, fields: None,
    )
    _raw_insert(store, "old", _row(n=1))
    created, canon = store.insert_if_absent("z", _row(n=2), unique_fields=FIELDS)
    assert (created, canon["n"]) == (False, 1)
