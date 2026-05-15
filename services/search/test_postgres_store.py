"""Tests for optional Postgres store pilot (search service).

Uses fakes instead of a real DB so the tests run without psycopg installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


# ---------------------------------------------------------------------------
# Fake Postgres infrastructure
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSnapshotConnection:
    """Tracks search snapshot inserts."""

    rows: list = []
    statements: list = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("CREATE"):
            return _FakeCursor([])
        if normalized.startswith("INSERT INTO"):
            request_id, payload_json = params[0], params[1]
            payload = json.loads(payload_json)
            self.rows.append((request_id, payload))
            return _FakeCursor([])
        if normalized.startswith("SELECT"):
            return _FakeCursor([(row[0], row[1]) for row in self.rows])
        return _FakeCursor([])


class _FakeEvidenceConnection:
    """Tracks evidence record reads."""

    evidence_rows: list = []
    statements: list = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("SELECT"):
            return _FakeCursor([(row[0], row[1]) for row in self.evidence_rows])
        return _FakeCursor([])


def _fake_snapshot_psycopg():
    conn = _FakeSnapshotConnection()
    return SimpleNamespace(connect=lambda dsn: conn), conn


def _fake_evidence_psycopg():
    conn = _FakeEvidenceConnection()
    return SimpleNamespace(connect=lambda dsn: conn), conn


# ---------------------------------------------------------------------------
# Tests: JsonlSearchIndexStore default
# ---------------------------------------------------------------------------

def test_build_search_index_store_jsonl_default():
    """JSONL index store is the default when env is empty."""
    from services.search.pg_store import build_search_index_store
    from services.search.index_store import JsonlSearchIndexStore

    path = Path(tempfile.mkdtemp()) / "search-index.jsonl"
    with mock.patch.dict("os.environ", {}, clear=True):
        store = build_search_index_store(path)

    assert isinstance(store, JsonlSearchIndexStore)


# ---------------------------------------------------------------------------
# Tests: PostgresSearchIndexStore
# ---------------------------------------------------------------------------

def test_build_search_index_store_postgres_env_gated():
    """Postgres index store activates only when SEARCH_INDEX_STORE_BACKEND=postgres."""
    from services.search.pg_store import PostgresSearchIndexStore, build_search_index_store

    _FakeSnapshotConnection.rows = []
    _FakeSnapshotConnection.statements = []
    fake_psycopg, _conn = _fake_snapshot_psycopg()

    path = Path(tempfile.mkdtemp()) / "search-index.jsonl"
    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "SEARCH_INDEX_STORE_BACKEND": "postgres",
                "SEARCH_INDEX_STORE_DSN": "postgresql://search-writer@example/db",
            },
            clear=True,
        ),
    ):
        store = build_search_index_store(path)

    assert isinstance(store, PostgresSearchIndexStore)
    assert any("CREATE SCHEMA IF NOT EXISTS" in s for s in _FakeSnapshotConnection.statements)
    assert any("CREATE TABLE IF NOT EXISTS" in s for s in _FakeSnapshotConnection.statements)


def test_postgres_search_index_store_append_and_get():
    """append_snapshot persists to Postgres; get_snapshot returns from cache."""
    from services.search.pg_store import PostgresSearchIndexStore
    from services.search.index_store import SearchIndexSnapshot
    import datetime

    _FakeSnapshotConnection.rows = []
    _FakeSnapshotConnection.statements = []
    fake_psycopg, _conn = _fake_snapshot_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        pg_store = PostgresSearchIndexStore(
            dsn="postgresql://search-writer@example/db", bootstrap=True
        )
        snapshot = SearchIndexSnapshot(
            request_id="req-search-001",
            trace_id="trace-search-001",
            filters_applied={"source_types": ["paper"]},
            result_refs=[
                {
                    "result_id": "res-1",
                    "evidence_bundle_id": "evbundle-1",
                    "citations": ["cite-1"],
                    "matched_items": ["evi-1"],
                }
            ],
            created_at="2026-04-29T00:00:00Z",
        )
        returned = pg_store.append_snapshot(snapshot)

    assert returned.request_id == "req-search-001"
    assert pg_store.get_snapshot("req-search-001") is not None

    insert_stmts = [s for s in _FakeSnapshotConnection.statements if "INSERT INTO" in s.upper()]
    assert insert_stmts, "expected INSERT statement"


def test_postgres_search_index_store_reload():
    """reload() re-populates in-memory cache from Postgres rows."""
    from services.search.pg_store import PostgresSearchIndexStore
    from services.search.index_store import SearchIndexSnapshot

    snapshot_payload = {
        "schema_version": "governed_search_refs.v1",
        "request_id": "req-reload-001",
        "trace_id": "trace-reload-001",
        "filters_applied": {},
        "result_refs": [
            {
                "result_id": "res-reload",
                "evidence_bundle_id": "evbundle-reload",
                "citations": [],
                "matched_items": [],
            }
        ],
        "created_at": "2026-04-29T00:00:00Z",
    }

    _FakeSnapshotConnection.rows = [("req-reload-001", snapshot_payload)]
    _FakeSnapshotConnection.statements = []
    fake_psycopg, _conn = _fake_snapshot_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        pg_store = PostgresSearchIndexStore(
            dsn="postgresql://search-writer@example/db", bootstrap=False
        )
        pg_store.reload()

    assert pg_store.get_snapshot("req-reload-001") is not None


# ---------------------------------------------------------------------------
# Tests: PostgresReadOnlyEvidenceRepository
# ---------------------------------------------------------------------------

def test_build_search_evidence_repository_jsonl_default():
    """JSONL evidence repository is the default when env is empty."""
    from services.search.pg_store import build_search_evidence_repository
    from services.knowledge.evidence import JsonlEvidenceRepository

    path = Path(tempfile.mkdtemp()) / "source_evidence.jsonl"
    with mock.patch.dict("os.environ", {}, clear=True):
        repo = build_search_evidence_repository(path)

    assert isinstance(repo, JsonlEvidenceRepository)


def test_build_search_evidence_repository_postgres_env_gated():
    """Postgres evidence repository activates only when SEARCH_EVIDENCE_BACKEND=postgres."""
    from services.search.pg_store import PostgresReadOnlyEvidenceRepository, build_search_evidence_repository

    _FakeEvidenceConnection.evidence_rows = []
    _FakeEvidenceConnection.statements = []
    fake_psycopg, _conn = _fake_evidence_psycopg()

    path = Path(tempfile.mkdtemp()) / "source_evidence.jsonl"
    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "SEARCH_EVIDENCE_BACKEND": "postgres",
                "SEARCH_EVIDENCE_DSN": "postgresql://search-reader@example/db",
            },
            clear=True,
        ),
    ):
        repo = build_search_evidence_repository(path)

    assert isinstance(repo, PostgresReadOnlyEvidenceRepository)


def test_postgres_read_only_evidence_repository_enforces_write_boundary():
    """Write methods raise EvidenceValidationError — search must not write evidence."""
    from services.search.pg_store import PostgresReadOnlyEvidenceRepository
    from services.knowledge.evidence.models import EvidenceValidationError
    from services.source_ingestion.connectors.base import SourceRecord
    import pytest

    _FakeEvidenceConnection.evidence_rows = []
    _FakeEvidenceConnection.statements = []
    fake_psycopg, _conn = _fake_evidence_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        repo = PostgresReadOnlyEvidenceRepository(dsn="postgresql://search-reader@example/db")
        source = SourceRecord(
            source_id="src-forbidden",
            connector_id="conn-test",
            source_type="paper",
            title="Should Not Write",
            content_ref="ref://forbidden",
        )
        with pytest.raises(EvidenceValidationError, match="read-only"):
            repo.add_source_record(source)


def test_postgres_read_only_evidence_repository_reload_preserves_reference_validation():
    """Search read-only reload still enforces source evidence invariants."""
    from services.knowledge.evidence.models import EvidenceValidationError
    from services.search.pg_store import PostgresReadOnlyEvidenceRepository
    import pytest

    _FakeEvidenceConnection.evidence_rows = [
        (
            "evidence_item",
            {
                "evidence_item_id": "evi-orphan",
                "source_id": "missing-source",
                "item_type": "text_chunk",
                "content_ref": "ref://orphan",
                "citation_label": "orphan",
                "body": "orphaned evidence",
            },
        )
    ]
    _FakeEvidenceConnection.statements = []
    fake_psycopg, _conn = _fake_evidence_psycopg()

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        pytest.raises(EvidenceValidationError, match="unknown source_id"),
    ):
        PostgresReadOnlyEvidenceRepository(dsn="postgresql://search-reader@example/db")


def test_build_search_index_store_invalid_backend():
    import pytest
    from services.search.pg_store import build_search_index_store

    path = Path(tempfile.mkdtemp()) / "search-index.jsonl"
    with mock.patch.dict("os.environ", {"SEARCH_INDEX_STORE_BACKEND": "redis"}, clear=True):
        with pytest.raises(ValueError, match="must be jsonl or postgres"):
            build_search_index_store(path)


def test_build_search_evidence_repository_invalid_backend():
    import pytest
    from services.search.pg_store import build_search_evidence_repository

    path = Path(tempfile.mkdtemp()) / "source_evidence.jsonl"
    with mock.patch.dict("os.environ", {"SEARCH_EVIDENCE_BACKEND": "redis"}, clear=True):
        with pytest.raises(ValueError, match="must be jsonl or postgres"):
            build_search_evidence_repository(path)
