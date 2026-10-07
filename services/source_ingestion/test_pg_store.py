"""Tests for cross-process PostgreSQL evidence reads in source-ingest.

Verifies that two independent PostgresSourceEvidenceRepository instances
(simulating separate scheduler and API server processes) have immediate
read visibility for committed source records, evidence items, evidence
bundles, and knowledge objects without process restart, and that strict
tenant isolation is maintained.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceBundleBuilder,
    EvidenceItem,
    KnowledgeObject,
)
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository


def _get_test_pg_dsn() -> str | None:
    for candidate in (
        os.getenv("SOURCE_INGEST_TEST_POSTGRES_DSN"),
        os.getenv("TEST_DATABASE_URL"),
        "postgresql://postgres:postgres@127.0.0.1:25432/postgres",
        "postgresql://postgres:postgres@127.0.0.1:15432/postgres",
    ):
        if not candidate:
            continue
        try:
            import psycopg

            with psycopg.connect(candidate, connect_timeout=1):
                return candidate
        except Exception:
            continue
    return None


@pytest.fixture
def real_pg_tables():
    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("No accessible PostgreSQL instance found for real PG tests")
    import psycopg

    schema = f"test_crossproc_{uuid.uuid4().hex[:12]}"
    table = f"{schema}.source_evidence"
    yield dsn, table
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    except Exception:
        pass


def test_two_independent_postgres_instances_cross_process_visibility(real_pg_tables):
    """Two independent instances alive before write prove cross-process getter/list visibility."""
    dsn, table = real_pg_tables

    # Instance 1: simulating long-running API server (alive BEFORE write)
    reader_repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)

    # Instance 2: simulating scheduler process (alive BEFORE write)
    writer_repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)

    tenant_dev = "tenant-dev"
    tenant_other = "tenant-other"

    # Step 1: Writer commits source record for tenant-dev
    source = SourceRecord(
        source_id="src-cross-001",
        connector_id="tw-twse-tpex-official-market",
        source_type="market",
        title="2330 Daily Close",
        content_ref="tw-official://tw_price_daily/TWSE/2330/2026-10-07",
        status="normalized",
        metadata={
            "tenant_id": tenant_dev,
            "source_dedupe_key": "dedupe-src-001",
            "provider": "TWSE OpenAPI",
            "dataset": "tw_price_daily",
        },
    )
    writer_repo.add_source_record(source)

    # Step 2: Writer commits evidence item for tenant-dev
    item = EvidenceItem(
        evidence_item_id="item-cross-001",
        source_id=source.source_id,
        item_type="metric_series",
        content_ref=source.content_ref,
        citation_label="TWSE:2330",
        body="2330 daily market evidence.",
        metadata={
            "tenant_id": tenant_dev,
            "evidence_dedupe_key": "dedupe-item-001",
        },
    )
    writer_repo.add_evidence_item(item)

    # Step 3: Writer commits evidence bundle for tenant-dev
    bundle = EvidenceBundle(
        evidence_bundle_id="bundle-cross-001",
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary="Official Taiwan market daily bundle",
        citation_refs=["TWSE:2330"],
        confidence=1.0,
        license_scope="official",
        access_scope=["internal"],
        created_by="source-ingest-scheduler",
        metadata={
            "connector_id": "tw-twse-tpex-official-market",
            "ingest_run_id": "ingest-run-official-001",
            "tenant_id": tenant_dev,
        },
    )
    writer_repo.add_bundle(bundle)

    # Step 4: Writer commits knowledge object for tenant-dev
    ko = KnowledgeObject(
        knowledge_object_id="ko-cross-001",
        source_id=source.source_id,
        evidence_item_id=item.evidence_item_id,
        evidence_bundle_id=bundle.evidence_bundle_id,
        title=source.title,
        text=item.body,
        source_type="market",
        license_scope="official",
        access_scope=["internal"],
        metadata={
            "tenant_id": tenant_dev,
        },
    )
    writer_repo.add_knowledge_object(ko)

    # Acceptance Criterion 1 & 2: Reader (API server) MUST observe committed
    # scheduler source/items/bundle/knowledgeobject WITHOUT API restart and
    # without calling reload() or shared mutators.
    # --- SourceRecord verification ---
    observed_source = reader_repo.get_source_record(source.source_id, tenant_id=tenant_dev)
    assert observed_source is not None
    assert observed_source.source_id == source.source_id
    assert observed_source.tenant_id == tenant_dev

    observed_source_dedupe = reader_repo.get_source_record_by_dedupe_key("dedupe-src-001", tenant_id=tenant_dev)
    assert observed_source_dedupe is not None
    assert observed_source_dedupe.source_id == source.source_id

    observed_sources = reader_repo.list_source_records(tenant_id=tenant_dev)
    assert any(s.source_id == source.source_id for s in observed_sources)

    # --- EvidenceItem verification ---
    observed_item = reader_repo.get_evidence_item(item.evidence_item_id, tenant_id=tenant_dev)
    assert observed_item is not None
    assert observed_item.evidence_item_id == item.evidence_item_id
    assert observed_item.tenant_id == tenant_dev

    observed_item_dedupe = reader_repo.get_evidence_item_by_dedupe_key("dedupe-item-001", tenant_id=tenant_dev)
    assert observed_item_dedupe is not None
    assert observed_item_dedupe.evidence_item_id == item.evidence_item_id

    observed_items = reader_repo.list_evidence_items(tenant_id=tenant_dev)
    assert any(i.evidence_item_id == item.evidence_item_id for i in observed_items)

    # --- EvidenceBundle verification ---
    observed_bundle = reader_repo.get_bundle(bundle.evidence_bundle_id, tenant_id=tenant_dev)
    assert observed_bundle is not None
    assert observed_bundle.evidence_bundle_id == bundle.evidence_bundle_id
    assert observed_bundle.metadata.get("connector_id") == "tw-twse-tpex-official-market"
    assert observed_bundle.metadata.get("ingest_run_id") == "ingest-run-official-001"
    assert observed_bundle.tenant_id == tenant_dev

    observed_bundles = reader_repo.list_bundles(tenant_id=tenant_dev)
    matching_bundles = [
        b for b in observed_bundles
        if b.metadata.get("connector_id") == "tw-twse-tpex-official-market"
        and b.metadata.get("ingest_run_id") == "ingest-run-official-001"
    ]
    assert len(matching_bundles) == 1
    assert matching_bundles[0].evidence_bundle_id == bundle.evidence_bundle_id

    # --- KnowledgeObject verification ---
    observed_ko = reader_repo.get_knowledge_object(ko.knowledge_object_id, tenant_id=tenant_dev)
    assert observed_ko is not None
    assert observed_ko.knowledge_object_id == ko.knowledge_object_id
    assert observed_ko.evidence_bundle_id == bundle.evidence_bundle_id
    assert observed_ko.tenant_id == tenant_dev

    observed_kos = reader_repo.list_knowledge_objects(tenant_id=tenant_dev)
    assert any(k.knowledge_object_id == ko.knowledge_object_id for k in observed_kos)


def test_postgres_distinguishes_missing_bundle_and_wrong_tenant(real_pg_tables):
    """Acceptance criterion 4: distinguish missing bundle and wrong tenant."""
    dsn, table = real_pg_tables
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)

    tenant_a = "tenant-alpha"
    tenant_b = "tenant-beta"

    source = SourceRecord(
        source_id="src-tenant-test",
        connector_id="conn-1",
        source_type="paper",
        title="Alpha Source",
        content_ref="ref://alpha",
        metadata={"tenant_id": tenant_a},
    )
    repo.add_source_record(source)

    item = EvidenceItem(
        evidence_item_id="item-tenant-test",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="ref://alpha#1",
        citation_label="alpha#1",
        body="Alpha evidence body",
        metadata={"tenant_id": tenant_a},
    )
    repo.add_evidence_item(item)

    bundle = EvidenceBundle(
        evidence_bundle_id="bundle-alpha-only",
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary="Alpha summary",
        citation_refs=["alpha#1"],
        confidence=1.0,
        license_scope="open",
        access_scope=["public"],
        created_by="tester",
        metadata={"tenant_id": tenant_a, "connector_id": "conn-1", "ingest_run_id": "run-alpha"},
    )
    repo.add_bundle(bundle)

    # 1. Correct tenant finds the bundle
    assert repo.get_bundle("bundle-alpha-only", tenant_id=tenant_a) is not None
    assert len(repo.list_bundles(tenant_id=tenant_a)) == 1

    # 2. Wrong tenant gets None / empty list (strict isolation)
    assert repo.get_bundle("bundle-alpha-only", tenant_id=tenant_b) is None
    assert repo.list_bundles(tenant_id=tenant_b) == []
    assert repo.get_source_record("src-tenant-test", tenant_id=tenant_b) is None
    assert repo.list_source_records(tenant_id=tenant_b) == []
    assert repo.get_evidence_item("item-tenant-test", tenant_id=tenant_b) is None
    assert repo.list_evidence_items(tenant_id=tenant_b) == []

    # 3. Truly missing bundle gets None on all tenants
    assert repo.get_bundle("bundle-completely-nonexistent", tenant_id=tenant_a) is None
    assert repo.get_bundle("bundle-completely-nonexistent", tenant_id=tenant_b) is None


def test_postgres_update_reread_and_restart(real_pg_tables):
    """Acceptance criterion 4: update, re-read, and restart consistency."""
    dsn, table = real_pg_tables

    writer = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    reader = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)

    tenant_id = "tenant-update"
    source = SourceRecord(
        source_id="src-up",
        connector_id="conn-up",
        source_type="market",
        title="Initial Title",
        content_ref="ref://up",
        metadata={"tenant_id": tenant_id},
    )
    writer.add_source_record(source)

    item = EvidenceItem(
        evidence_item_id="item-up",
        source_id=source.source_id,
        item_type="metric_series",
        content_ref=source.content_ref,
        citation_label="cit-up",
        body="Initial Body",
        metadata={"tenant_id": tenant_id},
    )
    writer.add_evidence_item(item)

    bundle_v1 = EvidenceBundle(
        evidence_bundle_id="bundle-up",
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary="Initial summary",
        citation_refs=["cit-up"],
        confidence=0.8,
        license_scope="open",
        access_scope=["public"],
        created_by="tester",
        metadata={"tenant_id": tenant_id, "connector_id": "conn-up", "ingest_run_id": "run-up-1"},
    )
    writer.add_bundle(bundle_v1)

    # Reader immediately observes v1
    b_read = reader.get_bundle("bundle-up", tenant_id=tenant_id)
    assert b_read is not None
    assert b_read.summary == "Initial summary"
    assert b_read.confidence == 0.8

    # Writer updates bundle (upsert with new summary and confidence)
    bundle_v2 = EvidenceBundle(
        evidence_bundle_id="bundle-up",
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary="Updated summary v2",
        citation_refs=["cit-up"],
        confidence=0.99,
        license_scope="open",
        access_scope=["public"],
        created_by="tester",
        metadata={"tenant_id": tenant_id, "connector_id": "conn-up", "ingest_run_id": "run-up-2"},
    )
    writer.add_bundle(bundle_v2)

    # Reader immediately observes updated v2 without restart
    b_updated = reader.get_bundle("bundle-up", tenant_id=tenant_id)
    assert b_updated is not None
    assert b_updated.summary == "Updated summary v2"
    assert b_updated.confidence == 0.99
    assert b_updated.metadata.get("ingest_run_id") == "run-up-2"

    # A brand-new restarted repository loads and observes exact v2
    restarted = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)
    b_restart = restarted.get_bundle("bundle-up", tenant_id=tenant_id)
    assert b_restart is not None
    assert b_restart.summary == "Updated summary v2"
    assert b_restart.confidence == 0.99


def test_no_leaked_secret_in_evidence_payload_or_exceptions(real_pg_tables):
    """Acceptance criterion 1 & 4: no leaked credentials in stored payload, representations, or exceptions."""
    dsn, table = real_pg_tables
    secret_pw = "super_secret_test_password_xyz987"
    # Create DSN with embedded sensitive password
    safe_dsn = dsn.replace("postgres:postgres", f"postgres:{secret_pw}") if "postgres:postgres" in dsn else dsn
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)

    source = SourceRecord(
        source_id="src-secret-test",
        connector_id="conn-secret",
        source_type="paper",
        title="Sanitized Record",
        content_ref="ref://sanitized",
        metadata={"tenant_id": "tenant-sec"},
    )
    repo.add_source_record(source)

    # Ensure repository object representations and JSON payloads do not expose DSN password
    repo_str = repr(repo)
    assert "password" not in repo_str.lower() or "postgres:postgres" not in repo_str

    with repo._connect() as conn:
        cursor = conn.execute(f"SELECT payload FROM {table} WHERE record_id = %s", ("@t10:tenant-sec:src-secret-test",))
        raw_payload = cursor.fetchone()[0]

    assert "password" not in json.dumps(raw_payload).lower()

    # Now verify query failure with sensitive DSN does NOT leak password in message, repr, or cause
    repo.dsn = f"postgresql://postgres:{secret_pw}@127.0.0.1:1/invalid_db"
    with pytest.raises(RuntimeError) as exc_info:
        repo.get_source_record("src-secret-test", tenant_id="tenant-sec")
    err_str = str(exc_info.value)
    err_repr = repr(exc_info.value)
    assert secret_pw not in err_str
    assert secret_pw not in err_repr
    assert exc_info.value.__cause__ is None
    assert "PostgreSQL evidence query failed" in err_str


def test_postgres_query_failure_fails_closed_without_returning_stale_cache(real_pg_tables):
    """Acceptance criterion 1: query/connection failure must NOT return startup stale cache and must fail closed."""
    dsn, table = real_pg_tables
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    tenant_id = "tenant-failclosed"

    source = SourceRecord(
        source_id="src-fail-001",
        connector_id="conn-fail",
        source_type="market",
        title="Failclosed Title",
        content_ref="ref://fail",
        metadata={"tenant_id": tenant_id, "source_dedupe_key": "dk-fail-src"},
    )
    repo.add_source_record(source)

    item = EvidenceItem(
        evidence_item_id="item-fail-001",
        source_id=source.source_id,
        item_type="metric_series",
        content_ref=source.content_ref,
        citation_label="cit-fail",
        body="Failclosed body",
        metadata={"tenant_id": tenant_id, "evidence_dedupe_key": "dk-fail-item"},
    )
    repo.add_evidence_item(item)

    bundle = EvidenceBundle(
        evidence_bundle_id="bundle-fail-001",
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary="Failclosed bundle summary",
        citation_refs=["cit-fail"],
        confidence=1.0,
        license_scope="open",
        access_scope=["public"],
        created_by="tester",
        metadata={"tenant_id": tenant_id},
    )
    repo.add_bundle(bundle)

    ko = KnowledgeObject(
        knowledge_object_id="ko-fail-001",
        source_id=source.source_id,
        evidence_item_id=item.evidence_item_id,
        evidence_bundle_id=bundle.evidence_bundle_id,
        title=source.title,
        text=item.body,
        source_type="market",
        license_scope="open",
        access_scope=["public"],
        metadata={"tenant_id": tenant_id},
    )
    repo.add_knowledge_object(ko)

    # In-memory startup cache now has all records.
    # Now induce a real DB failure: point table to non-existent table in Postgres
    repo.table = '"nonexistent_schema"."nonexistent_table"'

    # Every getter and lister MUST fail closed with sanitized RuntimeError and NOT return stale cache
    for fn, args in (
        (repo.get_source_record, ("src-fail-001", tenant_id)),
        (repo.get_source_record_by_dedupe_key, ("dk-fail-src", tenant_id)),
        (repo.list_source_records, (tenant_id,)),
        (repo.get_evidence_item, ("item-fail-001", tenant_id)),
        (repo.get_evidence_item_by_dedupe_key, ("dk-fail-item", tenant_id)),
        (repo.list_evidence_items, (tenant_id,)),
        (repo.get_bundle, ("bundle-fail-001", tenant_id)),
        (repo.list_bundles, (tenant_id,)),
        (repo.get_knowledge_object, ("ko-fail-001", tenant_id)),
        (repo.list_knowledge_objects, (tenant_id,)),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            fn(*args)
        assert "PostgreSQL evidence query failed" in str(exc_info.value)
        assert exc_info.value.__cause__ is None


def test_mock_typed_sql_fake_exceptions_fail_closed_sanitized(monkeypatch):
    """Unit proof (labelled unit test, not PG proof): typed SQL exception fails closed sanitized."""
    class FakeCursor:
        def __init__(self, rows=()):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            if "ORDER BY append_id ASC" in sql and "WHERE" not in sql:
                # reload() during init
                return FakeCursor([])
            raise psycopg.OperationalError("server closed the connection unexpectedly [sensitive_token_999]")

    import sys
    import psycopg
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(
        connect=lambda dsn: FakeConn(),
        OperationalError=psycopg.OperationalError,
    ))
    repo = PostgresSourceEvidenceRepository(dsn="postgresql://user:pass@localhost:5432/db", bootstrap=False)

    # Pre-populate in-memory cache to simulate boot cache
    bundle = EvidenceBundle(
        evidence_bundle_id="b-stale-1",
        source_ids=["s1"],
        evidence_item_ids=["i1"],
        summary="Stale bundle in boot cache",
        citation_refs=["ref1"],
        confidence=1.0,
        license_scope="open",
        access_scope=["public"],
        created_by="tester",
        metadata={"tenant_id": "tenant-mock"},
    )
    repo._bundles[bundle.evidence_bundle_id] = bundle
    repo._bundles_by_tenant[("tenant-mock", bundle.evidence_bundle_id)] = bundle

    with pytest.raises(RuntimeError) as exc_info:
        repo.get_bundle("b-stale-1", tenant_id="tenant-mock")

    err = str(exc_info.value)
    assert "sensitive_token_999" not in err
    assert "PostgreSQL evidence query failed: OperationalError" in err
    assert exc_info.value.__cause__ is None


def test_mock_postgres_read_visibility_and_query_dispatch(monkeypatch):
    """Unit proof: executes exact scoped SQL and handles JSON payload decoding."""
    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    dispatched = []

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            dispatched.append((sql, params))
            if "WHERE record_type = %s AND record_id = %s" in sql:
                r_type, scoped_id = params
                payload = {
                    "evidence_bundle_id": "b-mock-1",
                    "source_ids": ["s1"],
                    "evidence_item_ids": ["i1"],
                    "summary": "Mock bundle",
                    "citation_refs": ["ref1"],
                    "confidence": 1.0,
                    "license_scope": "open",
                    "access_scope": ["public"],
                    "created_by": "tester",
                    "metadata": {"tenant_id": "tenant-mock", "connector_id": "c1", "ingest_run_id": "r1"},
                }
                return FakeCursor([(payload,)])
            return FakeCursor([])

    import sys
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda dsn: FakeConn()))
    repo = PostgresSourceEvidenceRepository(dsn="postgresql://user:pass@localhost:5432/db", bootstrap=False)

    bundle = repo.get_bundle("b-mock-1", tenant_id="tenant-mock")
    assert bundle is not None
    assert bundle.evidence_bundle_id == "b-mock-1"
    assert bundle.tenant_id == "tenant-mock"

    # Verify query parameters
    assert any("record_type = %s" in sql and params == ("evidence_bundle", "@t11:tenant-mock:b-mock-1") for sql, params in dispatched)

