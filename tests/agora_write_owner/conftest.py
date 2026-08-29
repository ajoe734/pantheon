"""
Shared fixtures and paths setup for Agora write owner tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
from typing import Generator
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_paths() -> None:
    for subpath in [
        "",
        "services",
        "services/agora",
        "services/signal-store",
        "services/consultation",
        "services/source_ingestion",
    ]:
        p = str(REPO_ROOT / subpath) if subpath else str(REPO_ROOT)
        if p not in sys.path:
            sys.path.insert(0, p)


_ensure_paths()


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    rows: dict[str, dict[str, dict]] = {}
    statements: list[str] = []
    rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        table = self._table_name(sql)
        if normalized.startswith("CREATE"):
            return _FakeCursor([])
        if normalized.startswith("LOCK TABLE"):
            return _FakeCursor([])
        if normalized.startswith("INSERT INTO"):
            record_id = str(params[0])
            payload = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            if "DO NOTHING" in normalized and record_id in table_rows:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)] if "RETURNING PAYLOAD" in normalized else [])
        if normalized.startswith("UPDATE"):
            payload = json.loads(params[0]) if isinstance(params[0], str) else params[0]
            record_id = str(params[1])
            expected = json.loads(params[2]) if len(params) > 2 and isinstance(params[2], str) else (params[2] if len(params) > 2 else None)
            table_rows = self.rows.setdefault(table, {})
            if expected is not None and table_rows.get(record_id) != expected:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)])
        if normalized.startswith("DELETE FROM"):
            record_id = str(params[0])
            expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
                return _FakeCursor([])
            removed = table_rows.pop(record_id)
            return _FakeCursor([(removed,)])
        if normalized.startswith("SELECT 1 FROM INFORMATION_SCHEMA.SCHEMATA"):
            return _FakeCursor([(1,)])
        if normalized.startswith("SELECT PAYLOAD") and "WHERE RECORD_ID" in normalized:
            record = self.rows.get(table, {}).get(str(params[0]))
            if "AND PAYLOAD" in normalized and record is not None:
                expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
                if record != expected:
                    record = None
            return _FakeCursor([(record,)] if record is not None else [])
        if normalized.startswith("SELECT PAYLOAD"):
            return _FakeCursor([(payload,) for payload in self.rows.get(table, {}).values()])
        return _FakeCursor([])

    def rollback(self):
        type(self).rollback_count += 1

    @staticmethod
    def _table_name(sql: str) -> str:
        match = re.search(
            r'(?:FROM|INTO|UPDATE|TABLE)\s+((?:"[^"]+"\.)?"[^"]+")',
            sql,
            re.IGNORECASE,
        )
        return match.group(1) if match else "<unknown>"


def _fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn)


@pytest.fixture(autouse=True)
def setup_fake_postgres():
    """Ensure tests run against psycopg fake connection by default."""
    _ensure_paths()
    _FakeConnection.rows = {}
    _FakeConnection.statements = []
    _FakeConnection.rollback_count = 0
    fake = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake}):
        yield


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Provide an isolated temporary directory for test stores."""
    with tempfile.TemporaryDirectory(prefix="agora_write_owner_ws_") as tmp:
        yield Path(tmp)
