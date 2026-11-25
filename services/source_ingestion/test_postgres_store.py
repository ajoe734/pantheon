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

def test_read_source_records_for_tenant_uses_read_only_scoped_postgres_query(monkeypatch, tmp_path):
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    source = SourceRecord(
        source_id="src-tenant-bound", connector_id="conn", source_type="paper",
        title="Persisted normalized record", content_ref="https://example.test/source",
        metadata={"tenant_id": "tenant-a"},
    )
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            statements.append((" ".join(sql.split()), params))
            if sql.startswith("SELECT"):
                assert params == ("source_record", "tenant-a")
                return _FakeCursor([(source.to_dict(),)])
            return _FakeCursor([])

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda _dsn: Connection()))
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "postgres")
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_DSN", "postgresql://readonly/test")

    records = read_source_records_for_tenant(jsonl_path=tmp_path / "absent.jsonl", tenant_id="tenant-a")

    assert [record.to_dict() for record in records] == [source.to_dict()]
    assert statements[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert "payload->'metadata'->>'tenant_id' = %s" in statements[1][0]
    assert "tenant-a" not in statements[1][0]


def test_read_source_records_for_tenant_rejects_mis_scoped_owner_result(monkeypatch, tmp_path):
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    foreign_payload = {
        "source_id": "src-foreign",
        "connector_id": "conn",
        "source_type": "paper",
        "title": "Foreign persisted source",
        "content_ref": "https://example.test/foreign",
        "status": "normalized",
        "metadata": {"tenant_id": "tenant-b"},
    }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _params=()):
            return _FakeCursor([(foreign_payload,)]) if sql.startswith("SELECT") else _FakeCursor([])

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda _dsn: Connection()))
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "postgres")
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_DSN", "postgresql://readonly/test")

    from services.knowledge.evidence.models import EvidenceValidationError
    with pytest.raises(EvidenceValidationError, match="outside the requested tenant"):
        read_source_records_for_tenant(jsonl_path=tmp_path / "absent.jsonl", tenant_id="tenant-a")


def test_read_source_records_for_tenant_rejects_unscoped_and_foreign(monkeypatch, tmp_path):
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    with pytest.raises(ValueError, match="explicit non-wildcard tenant_id"):
        read_source_records_for_tenant(jsonl_path=tmp_path / "absent.jsonl", tenant_id="")
    with pytest.raises(ValueError, match="explicit non-wildcard tenant_id"):
        read_source_records_for_tenant(jsonl_path=tmp_path / "absent.jsonl", tenant_id="*")


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


def test_scoped_record_id_collision_safe():
    from services.source_ingestion.pg_store import _scoped_record_id

    # Legacy records without @ prefix stay bare
    assert _scoped_record_id(None, "src-1") == "src-1"
    assert _scoped_record_id(None, "t1:a:x") == "t1:a:x"
    # Legacy records starting with @ are escaped
    assert _scoped_record_id(None, "@t1:a:x") == "@@t1:a:x"

    # Tenanted records have distinguished @t prefix
    assert _scoped_record_id("a", "x") == "@t1:a:x"
    assert _scoped_record_id("tenant-1", "src-1") == "@t8:tenant-1:src-1"

    # No collision between legacy t1:a:x and tenanted ('a', 'x')
    assert _scoped_record_id(None, "t1:a:x") != _scoped_record_id("a", "x")
    # No collision between escaped legacy @t1:a:x and tenanted ('a', 'x')
    assert _scoped_record_id(None, "@t1:a:x") != _scoped_record_id("a", "x")


def test_postgres_write_failure_rolls_back_in_memory_state():
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
    from services.source_ingestion.connectors.base import SourceRecord

    _FakeConnection.rows = []
    _FakeConnection.statements = []
    fake_psycopg, _conn = _make_fake_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        repo = PostgresSourceEvidenceRepository(dsn="postgresql://test@example/db", bootstrap=True)
        source = SourceRecord(
            source_id="fail-src",
            connector_id="conn-test",
            source_type="paper",
            title="Fail Paper",
            content_ref="ref://fail",
            metadata={"tenant_id": "tenant-test", "source_dedupe_key": "dk-fail"},
        )
        with mock.patch.object(repo, "_upsert", side_effect=RuntimeError("disk write failed")):
            with pytest.raises(RuntimeError, match="disk write failed"):
                repo.add_source_record(source)

        assert repo.get_source_record("fail-src", tenant_id="tenant-test") is None
        assert repo.get_source_record_by_dedupe_key("dk-fail", tenant_id="tenant-test") is None


def test_real_postgres_legacy_and_tenanted_record_coexistence():
    dsn = os.getenv("SOURCE_INGEST_TEST_POSTGRES_DSN") or os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SOURCE_INGEST_TEST_POSTGRES_DSN or TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
    from services.source_ingestion.connectors.base import SourceRecord

    schema = f"coexist_{uuid.uuid4().hex[:16]}"
    table = f"{schema}.source_evidence"
    try:
        repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
        # Add legacy record whose ID could have collided with tenanted key
        legacy = SourceRecord(
            source_id="t1:a:x",
            connector_id="conn-test",
            source_type="internal_note",
            title="Legacy Note",
            content_ref="ref://legacy",
        )
        repo.add_source_record(legacy)

        # Add tenanted record
        tenanted = SourceRecord(
            source_id="x",
            connector_id="conn-test",
            source_type="internal_note",
            title="Tenanted Note",
            content_ref="ref://tenanted",
            metadata={"tenant_id": "a"},
        )
        repo.add_source_record(tenanted)

        # Reload from database and verify both records survive without collision
        reloaded = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)
        assert reloaded.get_source_record("t1:a:x", tenant_id=None) is not None
        assert reloaded.get_source_record("x", tenant_id="a") is not None
        assert reloaded.get_source_record("t1:a:x", tenant_id=None).title == "Legacy Note"
        assert reloaded.get_source_record("x", tenant_id="a").title == "Tenanted Note"
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture
def bounded_distillation_pg(monkeypatch):
    dsn = os.getenv("SOURCE_INGEST_TEST_POSTGRES_DSN") or os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SOURCE_INGEST_TEST_POSTGRES_DSN or TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    schema = f"distillation_{uuid.uuid4().hex[:16]}"
    table = f'"{schema}"."source_evidence"'
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "postgres")
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_DSN", dsn)
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_TABLE", f"{schema}.source_evidence")
    with psycopg.connect(dsn) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f"CREATE TABLE {table} (append_id bigserial PRIMARY KEY, record_type text, payload jsonb)")
    try:
        yield psycopg, dsn, table
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_bounded_distillation_real_pg_backlog_and_revisions(bounded_distillation_pg, tmp_path):
    """The incident's 27,803 rows must never become one Python payload list."""
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.distillation_worker import DistillationJobQueue, source_version_digest
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    psycopg, dsn, table = bounded_distillation_pg
    base = SourceRecord(source_id="eligible", connector_id="conn", source_type="paper",
                        title="Paper", content_ref="ref://paper", metadata={"tenant_id": "tenant-a"}).to_dict()
    with psycopg.connect(dsn) as conn:
        # Large market payloads precede the eligible rows, as on hosted.
        conn.execute(
            f"INSERT INTO {table} (record_type, payload) "
            "SELECT 'source_record', %s::jsonb || jsonb_build_object('source_id', 'market-' || n, "
            "'source_type', 'market', 'metadata', jsonb_build_object('tenant_id', 'tenant-a', "
            "'collector_market_bundle', true, 'body', repeat('m', 2048))) FROM generate_series(1, 27598) n",
            (json.dumps(base),),
        )
        conn.execute(
            f"INSERT INTO {table} (record_type, payload) "
            "SELECT 'source_record', %s::jsonb || jsonb_build_object('source_id', 'paper-' || n) "
            "FROM generate_series(1, 205) n", (json.dumps(base),),
        )
    queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
    summary = {}

    def read():
        return read_source_records_for_tenant(
            jsonl_path=tmp_path / "unused.jsonl", tenant_id="tenant-a", limit=100,
            already_admitted=lambda source: queue.get(source.source_id, source_version_digest(source)) is not None,
            summary=summary,
        )

    batches = []
    for expected_size in (100, 100, 5, 0):
        batch = read()
        assert len(batch) == expected_size
        assert summary["eligible_source_count"] == 205
        assert summary["source_summary_origin"] == "source-ingest.postgres.tenant_eligible_count"
        batches.extend(source.source_id for source in batch)
        for source in batch:
            job = queue.enqueue_source_record(source)
            if int(source.source_id.split("-")[1]) % 2:
                queue.mark_skipped(job.job_id, reason="completed", processed_at=1)
        queue.prune(now=1000, terminal_retention_seconds=10)
    assert batches == [f"paper-{i}" for i in range(1, 206)]
    # A revision retains its old append_id, so an enduring high-water mark
    # would lose it. Each catch-up starts a new read-only snapshot instead.
    with psycopg.connect(dsn) as conn:
        conn.execute(f"UPDATE {table} SET payload = jsonb_set(payload, '{{title}}', '\"Revised paper\"') "
                     "WHERE payload->>'source_id' = 'paper-1'")
    revised = read()
    assert [s.source_id for s in revised] == ["paper-1"]
    assert revised[0].title == "Revised paper"
    assert queue.version_count("paper-1") == 1
    queue.enqueue_source_record(revised[0])
    assert queue.version_count("paper-1") == 2


def test_bounded_distillation_real_pg_eligibility_and_scope(bounded_distillation_pg, tmp_path):
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    psycopg, dsn, table = bounded_distillation_pg
    markers = [None, False, 0, "", [], {}, True, "false", [1], {"bundle": "b"}]
    payloads = [SourceRecord(source_id=f"market-{i}", connector_id="conn", source_type="market",
                             title="Market", content_ref="ref://market",
                             metadata={"tenant_id": "tenant-a", "collector_market_bundle": marker}).to_dict()
                for i, marker in enumerate(markers)]
    payloads.extend([
        {**payloads[0], "source_id": "rejected", "status": "rejected"},
        {**payloads[0], "source_id": "foreign", "metadata": {"tenant_id": "tenant-b"}},
        {**payloads[0], "source_id": "unowned", "metadata": {}},
        {**payloads[0], "source_id": "paper", "source_type": "paper", "metadata": {"tenant_id": "tenant-a", "collector_market_bundle": True}},
    ])
    legacy = {**payloads[0], "source_id": "missing-status"}
    legacy.pop("status")
    payloads.append(legacy)
    with psycopg.connect(dsn) as conn:
        for payload in payloads:
            conn.execute(f"INSERT INTO {table} (record_type, payload) VALUES ('source_record', %s::jsonb)",
                         (json.dumps(payload),))
    summary = {}
    records = read_source_records_for_tenant(jsonl_path=tmp_path / "unused", tenant_id="tenant-a",
                                             limit=100, summary=summary)
    assert [s.source_id for s in records] == [*[f"market-{i}" for i in range(6)], "paper", "missing-status"]
    assert summary["eligible_source_count"] == 8


@pytest.mark.parametrize("dict_rows", [False, True])
def test_bounded_distillation_pg_limits_every_payload_query(monkeypatch, tmp_path, dict_rows):
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    statements = []
    payload = SourceRecord(source_id="s", connector_id="conn", source_type="paper", title="Paper",
                           content_ref="ref://paper", metadata={"tenant_id": "tenant-a"}).to_dict()

    class Cursor(_FakeCursor):
        def fetchone(self):
            return {"count": 250} if dict_rows else (250,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=()):
            statements.append((sql, params))
            if not sql.startswith("SELECT payload"):
                return Cursor([])
            assert "ORDER BY append_id ASC LIMIT %s" in sql
            assert "collector_market_bundle" in sql and "'normalized'" in sql
            assert params[:2] == ("source_record", "tenant-a")
            after, take = params[-2:]
            assert take == 100
            rows = []
            for i in range(max(1, after + 1), min(251, max(1, after + 1) + take)):
                value = {**payload, "source_id": f"s-{i}"}
                rows.append({"payload": value, "append_id": i} if dict_rows else (value, i))
            return Cursor(rows)

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda _: Connection()))
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "postgres")
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_DSN", "postgresql://unused/test")
    summary = {}
    records = read_source_records_for_tenant(
        jsonl_path=tmp_path / "unused", tenant_id="tenant-a", limit=100, summary=summary,
        already_admitted=lambda s: int(s.source_id.split("-")[1]) <= 150,
    )
    assert [s.source_id for s in records] == [f"s-{i}" for i in range(151, 251)]
    assert summary["eligible_source_count"] == 250
    assert len([sql for sql, _ in statements if sql.startswith("SELECT payload")]) == 3
    assert statements[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"


def test_bounded_distillation_pg_uses_one_server_cursor(monkeypatch, tmp_path):
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import read_source_records_for_tenant

    payload = SourceRecord(source_id="s", connector_id="conn", source_type="paper", title="Paper",
                           content_ref="ref://paper", metadata={"tenant_id": "tenant-a"}).to_dict()
    executed, fetch_sizes, names = [], [], []

    class Cursor:
        def __init__(self):
            self._rows = [(({**payload, "source_id": f"s-{i}"}), i) for i in range(1, 251)]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=()):
            executed.append((sql, params))

        def fetchmany(self, size):
            fetch_sizes.append(size)
            batch, self._rows = self._rows[:size], self._rows[size:]
            return batch

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self, name=None):
            names.append(name)
            return Cursor()

        def execute(self, sql, params=()):
            return SimpleNamespace(fetchone=lambda: (250,))

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda _: Connection()))
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "postgres")
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_DSN", "postgresql://unused/test")
    records = read_source_records_for_tenant(
        jsonl_path=tmp_path / "unused", tenant_id="tenant-a", limit=100, summary={},
        already_admitted=lambda s: int(s.source_id.split("-")[1]) <= 150,
    )
    assert [s.source_id for s in records] == [f"s-{i}" for i in range(151, 251)]
    assert len(names) == 1 and len(executed) == 1
    assert "ORDER BY append_id ASC" in executed[0][0] and "append_id >" not in executed[0][0]
    assert set(fetch_sizes) == {100} and len(fetch_sizes) == 3
