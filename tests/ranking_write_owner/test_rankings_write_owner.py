"""Write-then-fresh-read proof for the Rankings write-owner store.

Scope: services/rankings/store.py. Generation 2 narrows this store to a
single concrete backend, PostgresJsonOwnerStore, so these tests exercise it
against a fake psycopg connection (the same fake-connection pattern used by
the other Postgres owner-store suites in
services/foundation/tests/test_control_plane_postgres_owner_stores.py) --
every assertion reads through a *new* store instance pointed at the same
fake table, so none of it can pass off an in-process cache as durability.
"""
from __future__ import annotations

import json
import re
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from services.rankings.store import (
    RankingConflictError,
    RankingRecord,
    RankingWriteOwnerError,
    RankingWriteStore,
    build_rankings_store,
)


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
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            record_id = str(params[0])
            if "DO NOTHING" in normalized and record_id in table_rows:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)] if "RETURNING PAYLOAD" in normalized else [])
        if normalized.startswith("UPDATE"):
            payload = json.loads(params[0]) if isinstance(params[0], str) else params[0]
            record_id = str(params[1])
            expected = json.loads(params[2]) if isinstance(params[2], str) else params[2]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
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
        pass

    @staticmethod
    def _table_name(sql: str) -> str:
        match = re.search(
            r"(?:FROM|INTO|UPDATE)\s+((?:\"[^\"]+\"\.)?\"[^\"]+\")",
            sql,
            re.IGNORECASE,
        )
        return match.group(1) if match else "<unknown>"


def _fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn)


@pytest.fixture(autouse=True)
def _reset_fake_connection():
    _FakeConnection.rows = {}
    _FakeConnection.statements = []


@pytest.fixture
def store():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        yield RankingWriteStore(dsn="postgresql://owner@example/db")


def _record(ranking_id: str = "rk-001", **overrides) -> RankingRecord:
    payload = {
        "ranking_id": ranking_id,
        "title": "Persona League Weekly",
        "criteria": "sharpe_30d",
        "entries": [{"persona_id": "persona-a", "rank": 1, "score": 1.42}],
    }
    payload.update(overrides)
    return RankingRecord(**payload)


def test_create_then_fresh_read_from_new_store_instance(store) -> None:
    store.create_ranking(_record())

    # A brand-new store instance simulates a second process/reader: it has
    # no shared Python state with `store`, so this can only pass if the
    # write actually reached the durable owner table.
    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    fresh = reader.get_ranking("rk-001")

    assert fresh is not None
    assert fresh.title == "Persona League Weekly"
    assert fresh.entries == [{"persona_id": "persona-a", "rank": 1, "score": 1.42}]


def test_put_ranking_upsert_then_fresh_list(store) -> None:
    store.put_ranking(_record())
    store.put_ranking(_record(title="Persona League Weekly (revised)"))

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    listed = reader.list_rankings()

    assert len(listed) == 1
    assert listed[0].title == "Persona League Weekly (revised)"


def test_delete_ranking_then_fresh_read_returns_none(store) -> None:
    store.create_ranking(_record())
    deleted = store.delete_ranking("rk-001")
    assert deleted is True

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    assert reader.get_ranking("rk-001") is None
    assert reader.list_rankings() == []


def test_create_ranking_rejects_duplicate_id(store) -> None:
    store.create_ranking(_record())

    with pytest.raises(RankingConflictError):
        store.create_ranking(_record())


def test_put_ranking_rejects_missing_title(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.put_ranking(_record(title=""))


def test_writes_survive_across_separate_store_instances() -> None:
    """Simulates independent writer/reader processes sharing only the table."""
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        RankingWriteStore(dsn="postgresql://a@example/db").create_ranking(
            _record(ranking_id="rk-a")
        )
        RankingWriteStore(dsn="postgresql://b@example/db").create_ranking(
            _record(ranking_id="rk-b")
        )
        RankingWriteStore(dsn="postgresql://c@example/db").put_ranking(
            _record(ranking_id="rk-a", title="Persona League Weekly (v2)")
        )

        final_reader = RankingWriteStore(dsn="postgresql://d@example/db")
        ids = sorted(r.ranking_id for r in final_reader.list_rankings())
        assert ids == ["rk-a", "rk-b"]
        assert final_reader.get_ranking("rk-a").title == "Persona League Weekly (v2)"


def test_build_rankings_store_uses_postgres_json_owner_store(monkeypatch) -> None:
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://owner@example/db")
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store_instance = build_rankings_store()
    assert isinstance(store_instance, RankingWriteStore)


def test_build_rankings_store_requires_dsn(monkeypatch) -> None:
    monkeypatch.delenv("RANKING_STORE_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        build_rankings_store()


def test_no_read_store_import_and_no_local_overlay_fallback() -> None:
    """Guards the write-owner acceptance criteria at the source level.

    The write owner must not import the BFF's read_store.py, must not grow a
    local dict/overlay/cache/response-fallback path that would let a write
    appear durable without reaching the backing store, and (generation 2)
    must not reintroduce a second persistence backend alongside
    PostgresJsonOwnerStore.
    """
    import ast
    from pathlib import Path

    source = Path("services/rankings/store.py").read_text()
    tree = ast.parse(source)
    import_lines = [
        line
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert not any("read_store" in line for line in import_lines)
    assert not any("control_plane" in line or "control-plane" in line for line in import_lines)

    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    assert not any("overlay" in identifier.lower() for identifier in identifiers)
    assert "PostgresJsonOwnerStore" in identifiers
    assert not any("json" in identifier.lower() and "postgresjson" not in identifier.lower() for identifier in identifiers)
