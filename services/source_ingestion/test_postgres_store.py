"""Tests for optional Postgres evidence store pilot (source-ingest).

Uses fakes by default so the tests run without psycopg, plus an optional real
Postgres regression when a test DSN is configured.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Fake Postgres infrastructure
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    """Shared mutable state so tests can inspect DDL and data."""

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
            record_id, record_type, payload_json = params[0], params[1], params[2]
            payload = json.loads(payload_json)
            for existing in self.rows:
                if existing[0] == record_id and existing[1] == record_type:
                    existing[2] = payload
                    return _FakeCursor([])
            self.rows.append([record_id, record_type, payload])
            return _FakeCursor([])
        if normalized.startswith("SELECT"):
            return _FakeCursor([(row[1], row[2]) for row in self.rows])
        return _FakeCursor([])


def _make_fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn), conn


def _write_stable_knowledge_object_across_bundles(repo):
    from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem
    from services.source_ingestion.connectors.base import SourceRecord

    source = SourceRecord(
        source_id="src-stable",
        connector_id="conn-stable",
        source_type="paper",
        title="Stable source",
        content_ref="ref://stable",
    )
    item = EvidenceItem(
        evidence_item_id="evi-stable",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="ref://stable#chunk-1",
        citation_label="stable#chunk-1",
        body="Stable evidence receives a new run-scoped bundle.",
    )
    builder = EvidenceBundleBuilder(repo)
    first_bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="First bundle",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-first",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-stable",
        source_record=source,
        evidence_item=item,
        evidence_bundle=first_bundle,
        title=source.title,
        text=item.body,
    )
    later_bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Later bundle",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-later",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-stable",
        source_record=source,
        evidence_item=item,
        evidence_bundle=later_bundle,
        title=source.title,
        text=item.body,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_source_evidence_repository_jsonl_default():
    """JSONL remains default when env is empty."""
    from services.source_ingestion.pg_store import build_source_evidence_repository
    from services.knowledge.evidence import JsonlEvidenceRepository

    data_dir = tempfile.mkdtemp()
    path = Path(data_dir) / "source_evidence.jsonl"

    with mock.patch.dict("os.environ", {}, clear=True):
        repo = build_source_evidence_repository(path)

    assert isinstance(repo, JsonlEvidenceRepository)


def test_build_source_evidence_repository_postgres_env_gated():
    """Postgres path activates only when SOURCE_INGEST_EVIDENCE_BACKEND=postgres."""
    from services.source_ingestion.pg_store import (
        PostgresSourceEvidenceRepository,
        build_source_evidence_repository,
    )

    _FakeConnection.rows = []
    _FakeConnection.statements = []
    fake_psycopg, _conn = _make_fake_psycopg()

    path = Path(tempfile.mkdtemp()) / "source_evidence.jsonl"

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "SOURCE_INGEST_EVIDENCE_BACKEND": "postgres",
                "SOURCE_INGEST_EVIDENCE_DSN": "postgresql://source-ingest-writer@example/db",
            },
            clear=True,
        ),
    ):
        repo = build_source_evidence_repository(path)

    assert isinstance(repo, PostgresSourceEvidenceRepository)
    assert any("CREATE SCHEMA IF NOT EXISTS" in s for s in _FakeConnection.statements)
    assert any("CREATE TABLE IF NOT EXISTS" in s for s in _FakeConnection.statements)


def test_postgres_evidence_repository_write_and_reload():
    """Write operations upsert to Postgres; reload restores in-memory state."""
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
    from services.source_ingestion.connectors.base import SourceRecord

    _FakeConnection.rows = []
    _FakeConnection.statements = []
    fake_psycopg, _conn = _make_fake_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        repo = PostgresSourceEvidenceRepository(
            dsn="postgresql://test@example/db", bootstrap=True
        )
        source = SourceRecord(
            source_id="src-1",
            connector_id="conn-test",
            source_type="paper",
            title="Test Paper",
            content_ref="ref://test",
        )
        repo.add_source_record(source)

    # In-memory state is current.
    assert repo.get_source_record("src-1") is not None

    # Postgres row was written.
    insert_stmts = [s for s in _FakeConnection.statements if "INSERT INTO" in s.upper()]
    assert insert_stmts, "expected at least one INSERT"

    # Reload re-populates from Postgres rows.
    _FakeConnection.statements = []
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        repo.reload()

    assert repo.get_source_record("src-1") is not None


def test_postgres_reload_uses_dependency_topology_after_stable_object_upsert():
    """A stable KO may point to a bundle appended after the KO's original row."""
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    _FakeConnection.rows = []
    _FakeConnection.statements = []
    fake_psycopg, _conn = _make_fake_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        repo = PostgresSourceEvidenceRepository(
            dsn="postgresql://test@example/db", bootstrap=True
        )
        _write_stable_knowledge_object_across_bundles(repo)

        record_order = [[row[1], row[0]] for row in _FakeConnection.rows]
        assert ["knowledge_object", "ko-stable"] in record_order
        assert ["evidence_bundle", "evbundle-later"] in record_order
        assert record_order.index(["knowledge_object", "ko-stable"]) < record_order.index(
            ["evidence_bundle", "evbundle-later"]
        )

        restarted = PostgresSourceEvidenceRepository(
            dsn="postgresql://test@example/db", bootstrap=False
        )

    assert restarted.get_knowledge_object("ko-stable").evidence_bundle_id == "evbundle-later"


def test_real_postgres_reload_uses_dependency_topology_after_stable_object_upsert():
    dsn = os.getenv("SOURCE_INGEST_TEST_POSTGRES_DSN") or os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SOURCE_INGEST_TEST_POSTGRES_DSN or TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    schema = f"source_ingest_reload_{uuid.uuid4().hex[:16]}"
    table = f"{schema}.source_evidence"
    try:
        repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
        _write_stable_knowledge_object_across_bundles(repo)
        with repo._connect() as conn:
            rows = conn.execute(
                f"SELECT record_type,record_id FROM {repo.table} ORDER BY append_id"
            ).fetchall()
        assert rows.index(("knowledge_object", "ko-stable")) < rows.index(
            ("evidence_bundle", "evbundle-later")
        )

        restarted = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)
        assert restarted.get_knowledge_object("ko-stable").evidence_bundle_id == "evbundle-later"
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_postgres_evidence_repository_reload_preserves_reference_validation():
    """Reload applies normal evidence invariants instead of trusting raw rows."""
    from services.knowledge.evidence.models import EvidenceValidationError
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    _FakeConnection.rows = [
        [
            "evi-orphan",
            "evidence_item",
            {
                "evidence_item_id": "evi-orphan",
                "source_id": "missing-source",
                "item_type": "text_chunk",
                "content_ref": "ref://orphan",
                "citation_label": "orphan",
                "body": "orphaned evidence",
            },
        ]
    ]
    _FakeConnection.statements = []
    fake_psycopg, _conn = _make_fake_psycopg()

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        pytest.raises(EvidenceValidationError, match="unknown source_id"),
    ):
        PostgresSourceEvidenceRepository(dsn="postgresql://test@example/db", bootstrap=False)


def test_postgres_evidence_repository_enforces_write_ownership():
    """Schema DDL includes the write-ownership table for source_ingest."""
    _FakeConnection.rows = []
    _FakeConnection.statements = []
    fake_psycopg, _conn = _make_fake_psycopg()

    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        PostgresSourceEvidenceRepository(
            dsn="postgresql://test@example/db",
            table="source_ingest.source_evidence",
            bootstrap=True,
        )

    ddl = " ".join(_FakeConnection.statements)
    assert "source_ingest" in ddl.lower()


def test_build_source_evidence_repository_invalid_backend():
    from services.source_ingestion.pg_store import build_source_evidence_repository

    path = Path(tempfile.mkdtemp()) / "source_evidence.jsonl"
    with mock.patch.dict("os.environ", {"SOURCE_INGEST_EVIDENCE_BACKEND": "redis"}, clear=True):
        with pytest.raises(ValueError, match="must be jsonl or postgres"):
            build_source_evidence_repository(path)
