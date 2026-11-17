"""PROJECTION-BOOTSTRAP-INDEX-CHURN-DEADLOCK-20261117.

Bootstrap must not create/drop the redundant event_receipts(ingested_seq) index
on every run, and must not deadlock with live projector writers.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.trade_journey.projection_store import ProjectionStore
from services.trade_journey.test_projection_store import (  # noqa: F401  (fixtures)
    _ephemeral_pg_cluster,
    postgres_dsn,
)

REDUNDANT = "idx_event_receipts_ingested_seq"


def _index_identity(dsn: str, schema: str) -> dict[str, tuple[int, int]]:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, c.oid::int, c.relfilenode::int FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind='i'",
            (schema,),
        )
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _insert_receipt(cur, schema: str, seq: int, suffix: str) -> None:
    cur.execute(
        f"INSERT INTO {schema}.event_receipts "
        "(tenant_id, environment, event_id, ingested_seq, fingerprint, source_event_type, created_at, disposition) "
        "VALUES ('t','paper',%s,%s,'fp','x',%s,'applied')",
        (f"ev-{suffix}-{seq}", seq, datetime.now(timezone.utc)),
    )


def _drop(dsn: str, schema: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_second_bootstrap_with_concurrent_writer_no_deadlock_no_index_churn(postgres_dsn: str) -> None:
    import psycopg

    schema = f"test_churn_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema, bootstrap=False)
    try:
        store.bootstrap_schema()
        before = _index_identity(postgres_dsn, schema)
        assert REDUNDANT not in before
        assert "event_receipts_ingested_seq_key" in before

        stop = threading.Event()
        errors: list[BaseException] = []
        counter = {"n": 0}

        def writer() -> None:
            try:
                with psycopg.connect(postgres_dsn, autocommit=True) as conn, conn.cursor() as cur:
                    while not stop.is_set():
                        counter["n"] += 1
                        _insert_receipt(cur, schema, counter["n"], "w")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=writer)
        t.start()
        try:
            time.sleep(0.3)
            store.bootstrap_schema(lock_timeout_seconds=5)
            store.bootstrap_schema(lock_timeout_seconds=5)
        finally:
            stop.set()
            t.join(timeout=10)

        assert not errors, errors
        assert counter["n"] > 0
        # Identical oid + relfilenode => no index was created, dropped or rebuilt.
        after = _index_identity(postgres_dsn, schema)
        assert after == before
    finally:
        _drop(postgres_dsn, schema)


def test_bootstrap_takes_no_blocking_lock_on_event_receipts_when_index_absent(postgres_dsn: str) -> None:
    """An in-flight writer transaction (RowExclusiveLock) must not stall a re-bootstrap."""
    import psycopg

    schema = f"test_churn_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema, bootstrap=False)
    try:
        store.bootstrap_schema()
        with psycopg.connect(postgres_dsn) as writer, writer.cursor() as cur:
            _insert_receipt(cur, schema, 1, "open")  # open txn, holds RowExclusiveLock
            started = time.monotonic()
            store.bootstrap_schema(lock_timeout_seconds=1)
            assert time.monotonic() - started < 5
            writer.rollback()
    finally:
        _drop(postgres_dsn, schema)


def test_legacy_database_with_redundant_index_loses_it_once(postgres_dsn: str) -> None:
    import psycopg

    schema = f"test_churn_{uuid4().hex[:8]}"
    store = ProjectionStore(postgres_dsn, schema=schema, bootstrap=False)
    try:
        store.bootstrap_schema()
        with psycopg.connect(postgres_dsn) as conn:
            conn.execute(f"CREATE INDEX {REDUNDANT} ON {schema}.event_receipts (ingested_seq)")
        store.bootstrap_schema(lock_timeout_seconds=5)
        after_first = _index_identity(postgres_dsn, schema)
        assert REDUNDANT not in after_first
        store.bootstrap_schema(lock_timeout_seconds=5)
        assert _index_identity(postgres_dsn, schema) == after_first
    finally:
        _drop(postgres_dsn, schema)
