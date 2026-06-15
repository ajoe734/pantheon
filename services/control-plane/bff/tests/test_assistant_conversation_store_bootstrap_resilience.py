from __future__ import annotations

"""Unit tests: PostgresAssistantConversationStore.bootstrap() resilience.

Root cause: assistant_conversation_turns table is owned by pantheon_management_ai
but BFF connects as pantheon_app (MANAGEMENT_AI_DATABASE_URL empty → falls back to
shared DATABASE_URL).  CREATE INDEX fails with InsufficientPrivilege (sqlstate 42501)
→ BFF crash-loop.

These tests verify that:
- InsufficientPrivilege on CREATE INDEX is non-fatal (warning logged, BFF starts).
- InsufficientPrivilege on CREATE TABLE is non-fatal if the table already exists.
- InsufficientPrivilege on CREATE TABLE re-raises if the table does not exist
  (BFF truly cannot operate without the turns table).
- Non-privilege DDL errors always propagate.
"""

import logging
import os
import sys
from unittest import mock

import pytest

# conftest.py adds the BFF dir to sys.path for all tests in this tree,
# but we also add it explicitly for editors that run the file standalone.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant_conversation_store import PostgresAssistantConversationStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _InsufficientPrivilege(Exception):
    """Fake psycopg InsufficientPrivilege — canonical SQLSTATE 42501."""

    sqlstate = "42501"


class _OtherDbError(Exception):
    """Fake psycopg error with a different SQLSTATE (not a privilege error)."""

    sqlstate = "42P07"  # DuplicateTable — not 42501


class _BootstrapConn:
    """
    Fake psycopg-style connection for bootstrap() unit tests.

    Parameters
    ----------
    fail_create_table:
        Raise _InsufficientPrivilege when a CREATE TABLE statement is executed.
    fail_create_index:
        Raise _InsufficientPrivilege when a CREATE INDEX statement is executed.
    table_exists:
        Controls the response to information_schema.tables probes executed after
        a CREATE TABLE privilege failure.
    """

    def __init__(
        self,
        *,
        fail_create_table: bool = False,
        fail_create_index: bool = False,
        table_exists: bool = True,
    ) -> None:
        self._fail_create_table = fail_create_table
        self._fail_create_index = fail_create_index
        self._table_exists = table_exists
        self.rollback_count = 0
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("CREATE TABLE") and self._fail_create_table:
            raise _InsufficientPrivilege("permission denied for table")
        if normalized.startswith("CREATE INDEX") and self._fail_create_index:
            raise _InsufficientPrivilege("permission denied for index")
        if "INFORMATION_SCHEMA.TABLES" in normalized:
            return _FakeCursor([(1,)] if self._table_exists else [])
        if "INFORMATION_SCHEMA.SCHEMATA" in normalized:
            return _FakeCursor([(1,)])
        return _FakeCursor()

    def rollback(self):
        self.rollback_count += 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _BootstrapConnCustom(_BootstrapConn):
    """_BootstrapConn with an injected execute callable for specific DDL failures."""

    def __init__(self, *, custom_execute, **kwargs):
        super().__init__(**kwargs)
        self._custom_execute = custom_execute

    def execute(self, sql, params=None):
        result = self._custom_execute(sql, params)
        if result is not None:
            return result
        return super().execute(sql, params)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> PostgresAssistantConversationStore:
    """Construct the store with bootstrap=False — no real DB connection needed."""
    return PostgresAssistantConversationStore(
        dsn="postgresql://app@db/test",
        bootstrap=False,
    )


def _run_bootstrap(
    store: PostgresAssistantConversationStore,
    conn: _BootstrapConn,
) -> None:
    """Invoke store.bootstrap() with _connect() replaced by a stub returning conn."""
    with mock.patch.object(store, "_connect", return_value=conn):
        store.bootstrap()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bootstrap_succeeds_with_no_errors() -> None:
    """Happy path: no privilege errors — both CREATE TABLE and CREATE INDEX execute."""
    store = _make_store()
    conn = _BootstrapConn()
    _run_bootstrap(store, conn)

    assert conn.rollback_count == 0
    assert any("CREATE TABLE" in s.upper() for s in conn.statements)
    assert any("CREATE INDEX" in s.upper() for s in conn.statements)


def test_bootstrap_create_index_insufficient_privilege_logs_warning_and_continues(
    caplog,
) -> None:
    """
    InsufficientPrivilege on CREATE INDEX → warning logged, bootstrap completes.
    BFF must start even without the performance index.
    """
    store = _make_store()
    conn = _BootstrapConn(fail_create_index=True)

    with caplog.at_level(logging.WARNING):
        _run_bootstrap(store, conn)  # must NOT raise

    assert conn.rollback_count == 1, "should rollback after privilege error"
    warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "InsufficientPrivilege" in m or "CREATE INDEX" in m.upper()
        for m in warning_texts
    ), f"Expected a warning about CREATE INDEX privilege failure; got: {warning_texts}"


def test_bootstrap_create_table_insufficient_privilege_table_exists_logs_warning(
    caplog,
) -> None:
    """
    InsufficientPrivilege on CREATE TABLE but the table already exists →
    warning logged, bootstrap completes.
    """
    store = _make_store()
    conn = _BootstrapConn(fail_create_table=True, table_exists=True)

    with caplog.at_level(logging.WARNING):
        _run_bootstrap(store, conn)  # must NOT raise

    assert conn.rollback_count >= 1
    warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "InsufficientPrivilege" in m or "CREATE TABLE" in m.upper()
        for m in warning_texts
    ), f"Expected a warning about CREATE TABLE privilege failure; got: {warning_texts}"
    # After recovering from the CREATE TABLE error, CREATE INDEX should still run
    assert any("CREATE INDEX" in s.upper() for s in conn.statements)


def test_bootstrap_create_table_insufficient_privilege_table_missing_reraises() -> None:
    """
    InsufficientPrivilege on CREATE TABLE and the table does not exist → re-raise.
    BFF cannot operate without the turns table.
    """
    store = _make_store()
    conn = _BootstrapConn(fail_create_table=True, table_exists=False)

    with pytest.raises(_InsufficientPrivilege):
        _run_bootstrap(store, conn)


def test_bootstrap_non_privilege_error_on_create_index_reraises() -> None:
    """Non-42501 error on CREATE INDEX propagates unchanged."""

    def _exec(sql, params):
        if "CREATE INDEX" in " ".join(sql.split()).upper():
            raise _OtherDbError("unexpected error")
        return None

    store = _make_store()
    conn = _BootstrapConnCustom(custom_execute=_exec)

    with pytest.raises(_OtherDbError):
        _run_bootstrap(store, conn)


def test_bootstrap_non_privilege_error_on_create_table_reraises() -> None:
    """Non-42501 error on CREATE TABLE propagates unchanged."""

    def _exec(sql, params):
        if "CREATE TABLE" in " ".join(sql.split()).upper():
            raise _OtherDbError("syntax error")
        return None

    store = _make_store()
    conn = _BootstrapConnCustom(custom_execute=_exec)

    with pytest.raises(_OtherDbError):
        _run_bootstrap(store, conn)


def test_bootstrap_both_ddl_fail_with_privilege_error_logs_two_warnings(caplog) -> None:
    """
    Both CREATE TABLE (table exists) and CREATE INDEX fail with 42501 →
    both degrade to warnings and bootstrap completes.
    """
    store = _make_store()
    conn = _BootstrapConn(fail_create_table=True, fail_create_index=True, table_exists=True)

    with caplog.at_level(logging.WARNING):
        _run_bootstrap(store, conn)  # must NOT raise

    assert conn.rollback_count == 2
    warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_texts) >= 2, (
        f"Expected at least two warnings (one per DDL failure); got: {warning_texts}"
    )
