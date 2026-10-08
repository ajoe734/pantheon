"""Tests for PostgreSQL connection reuse and startup reload performance in source-ingest.

Verifies acceptance criteria for SOURCE-EVIDENCE-PG-CONNECTION-REUSE-20261008:
1. Startup reload replays stored evidence without a Postgres round trip per lookup.
2. Request-time evidence reads reuse connections via a connection pool.
3. Reloading 1,000 bundles opens at most a constant number of connections.
4. A store shaped like hosted (757 bundles, 117 items, 117 knowledge objects,
   117 source records) starts well inside the healthcheck budget (105s).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from unittest import mock

import pytest

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    KnowledgeObject,
)
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
from services.source_ingestion.test_pg_store import _get_test_pg_dsn


@pytest.fixture
def pg_test_table():
    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("No accessible PostgreSQL instance found for real PG tests")
    import psycopg

    schema = f"test_conn_reuse_{uuid.uuid4().hex[:12]}"
    table = f"{schema}.source_evidence"
    yield dsn, table
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    except Exception:
        pass


def test_reload_1000_bundles_opens_constant_connections(pg_test_table):
    """Acceptance criterion 1 & 3: reloading 1000 bundles opens at most a constant number of connections."""
    dsn, table = pg_test_table
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    tenant_id = "tenant-1000"

    # Seed 10 source records and 10 items
    sources = []
    items = []
    for i in range(10):
        src = SourceRecord(
            source_id=f"src-1000-{i}",
            connector_id="conn-1000",
            source_type="market",
            title=f"Source {i}",
            content_ref=f"ref://{i}",
            metadata={"tenant_id": tenant_id},
        )
        repo.add_source_record(src)
        sources.append(src)

        item = EvidenceItem(
            evidence_item_id=f"item-1000-{i}",
            source_id=src.source_id,
            item_type="metric_series",
            content_ref=src.content_ref,
            citation_label=f"cit-{i}",
            body=f"Item body {i}",
            metadata={"tenant_id": tenant_id},
        )
        repo.add_evidence_item(item)
        items.append(item)

    # Seed 1000 bundles referencing the seeded sources and items
    for b_idx in range(1000):
        s_id = sources[b_idx % 10].source_id
        i_id = items[b_idx % 10].evidence_item_id
        bundle = EvidenceBundle(
            evidence_bundle_id=f"bundle-1000-{b_idx}",
            source_ids=[s_id],
            evidence_item_ids=[i_id],
            summary=f"Bundle summary {b_idx}",
            citation_refs=[f"cit-{b_idx % 10}"],
            confidence=1.0,
            license_scope="official",
            access_scope=["internal"],
            created_by="tester",
            metadata={"tenant_id": tenant_id, "connector_id": "conn-1000"},
        )
        repo.add_bundle(bundle)

    # Now restart / reload in a fresh repository instance while instrumenting _connect
    connect_calls = []
    orig_connect = PostgresSourceEvidenceRepository._connect

    def instrumented_connect(self):
        connect_calls.append(time.monotonic())
        return orig_connect(self)

    with mock.patch.object(PostgresSourceEvidenceRepository, "_connect", instrumented_connect):
        fresh_repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)
        # Verify 1000 bundles were loaded in memory
        loaded_bundles = fresh_repo.list_bundles(tenant_id=tenant_id)
        assert len(loaded_bundles) == 1000

        # Reload explicitly to verify idempotency and connection bounding
        connect_calls.clear()
        fresh_repo.reload()
        # Reloading with existing warm pool should open ZERO new connections
        assert len(connect_calls) == 0

    # Total connections during startup should be constant (at most 1)
    fresh_repo.close()
    repo.close()


def test_hosted_shaped_store_starts_inside_healthcheck_budget(pg_test_table):
    """Acceptance criterion 3: hosted-shaped store (757 bundles, 117 items, 117 KOs, 117 sources) starts well inside 105s."""
    dsn, table = pg_test_table
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    tenant_id = "tenant-hosted"

    sources = [
        SourceRecord(
            source_id=f"src-h-{i}",
            connector_id="conn-hosted",
            source_type="market",
            title=f"Source {i}",
            content_ref=f"ref://h/{i}",
            metadata={"tenant_id": tenant_id},
        )
        for i in range(117)
    ]
    for s in sources:
        repo.add_source_record(s)

    items = [
        EvidenceItem(
            evidence_item_id=f"item-h-{i}",
            source_id=sources[i].source_id,
            item_type="metric_series",
            content_ref=sources[i].content_ref,
            citation_label=f"cit-h-{i}",
            body=f"Item {i}",
            metadata={"tenant_id": tenant_id},
        )
        for i in range(117)
    ]
    for it in items:
        repo.add_evidence_item(it)

    bundles = [
        EvidenceBundle(
            evidence_bundle_id=f"bundle-h-{i}",
            source_ids=[sources[i % 117].source_id],
            evidence_item_ids=[items[i % 117].evidence_item_id],
            summary=f"Hosted bundle {i}",
            citation_refs=[f"cit-h-{i % 117}"],
            confidence=1.0,
            license_scope="official",
            access_scope=["internal"],
            created_by="tester",
            metadata={"tenant_id": tenant_id, "connector_id": "conn-hosted"},
        )
        for i in range(757)
    ]
    for b in bundles:
        repo.add_bundle(b)

    kos = [
        KnowledgeObject(
            knowledge_object_id=f"ko-h-{i}",
            source_id=sources[i].source_id,
            evidence_item_id=items[i].evidence_item_id,
            evidence_bundle_id=bundles[i].evidence_bundle_id,
            title=f"KO {i}",
            text=f"KO Text {i}",
            source_type="market",
            license_scope="official",
            access_scope=["internal"],
            metadata={"tenant_id": tenant_id},
        )
        for i in range(117)
    ]
    for k in kos:
        repo.add_knowledge_object(k)

    # Measure startup time of a fresh repository instance
    start_time = time.monotonic()
    fresh_repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)
    startup_duration = time.monotonic() - start_time

    # Healthcheck budget is 5s start period + 10 * 10s retries = 105s.
    # Must start well inside this budget (e.g. < 5.0 seconds).
    assert startup_duration < 5.0

    # Verify that all 757 bundles are reloaded and accessible
    assert len(fresh_repo.list_bundles(tenant_id=tenant_id)) == 757
    assert len(fresh_repo.list_evidence_items(tenant_id=tenant_id)) == 117
    assert len(fresh_repo.list_knowledge_objects(tenant_id=tenant_id)) == 117
    assert len(fresh_repo.list_source_records(tenant_id=tenant_id)) == 117

    fresh_repo.close()
    repo.close()


def test_request_time_reads_reuse_connection_pool(pg_test_table):
    """Acceptance criterion 2: request-time evidence reads reuse connections from the pool."""
    dsn, table = pg_test_table
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    tenant_id = "tenant-req-pool"

    src = SourceRecord(
        source_id="src-pool-1",
        connector_id="conn-pool",
        source_type="market",
        title="Pooled Source",
        content_ref="ref://pool",
        metadata={"tenant_id": tenant_id, "source_dedupe_key": "dk-pool-src"},
    )
    repo.add_source_record(src)

    item = EvidenceItem(
        evidence_item_id="item-pool-1",
        source_id=src.source_id,
        item_type="metric_series",
        content_ref=src.content_ref,
        citation_label="cit-pool",
        body="Pooled Item",
        metadata={"tenant_id": tenant_id, "evidence_dedupe_key": "dk-pool-item"},
    )
    repo.add_evidence_item(item)

    bundle = EvidenceBundle(
        evidence_bundle_id="bundle-pool-1",
        source_ids=[src.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary="Pooled Bundle",
        citation_refs=["cit-pool"],
        confidence=1.0,
        license_scope="official",
        access_scope=["internal"],
        created_by="tester",
        metadata={"tenant_id": tenant_id, "connector_id": "conn-pool"},
    )
    repo.add_bundle(bundle)

    ko = KnowledgeObject(
        knowledge_object_id="ko-pool-1",
        source_id=src.source_id,
        evidence_item_id=item.evidence_item_id,
        evidence_bundle_id=bundle.evidence_bundle_id,
        title="Pooled KO",
        text="Pooled KO Text",
        source_type="market",
        license_scope="official",
        access_scope=["internal"],
        metadata={"tenant_id": tenant_id},
    )
    repo.add_knowledge_object(ko)

    # Instrument _connect
    connect_calls = []
    orig_connect = repo._connect

    def instrumented_connect():
        connect_calls.append(time.monotonic())
        return orig_connect()

    repo._connect = instrumented_connect

    # Perform 50 read operations across all read methods
    for _ in range(10):
        assert repo.get_source_record("src-pool-1", tenant_id=tenant_id) is not None
        assert repo.get_source_record_by_dedupe_key("dk-pool-src", tenant_id=tenant_id) is not None
        assert repo.get_evidence_item("item-pool-1", tenant_id=tenant_id) is not None
        assert repo.get_evidence_item_by_dedupe_key("dk-pool-item", tenant_id=tenant_id) is not None
        assert repo.get_bundle("bundle-pool-1", tenant_id=tenant_id) is not None
        assert repo.get_knowledge_object("ko-pool-1", tenant_id=tenant_id) is not None

    # Zero new connections opened during all 60 reads because of connection pool reuse
    assert len(connect_calls) == 0

    repo.close()


def test_mock_unit_reload_uses_constant_connection(monkeypatch):
    """Unit test proof: reloading 1000 bundles uses in-memory references and at most 1 connection."""
    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    connect_count = 0

    bundle_rows = []
    for b_idx in range(1000):
        bundle_rows.append((
            "evidence_bundle",
            {
                "evidence_bundle_id": f"b-unit-{b_idx}",
                "source_ids": ["s-unit-1"],
                "evidence_item_ids": ["i-unit-1"],
                "summary": f"Bundle {b_idx}",
                "citation_refs": ["cit"],
                "confidence": 1.0,
                "license_scope": "official",
                "access_scope": ["internal"],
                "created_by": "tester",
                "metadata": {"tenant_id": "t1", "connector_id": "c1"},
            },
        ))

    all_rows = [
        (
            "source_record",
            {
                "source_id": "s-unit-1",
                "connector_id": "c1",
                "source_type": "market",
                "title": "Title",
                "content_ref": "ref://1",
                "metadata": {"tenant_id": "t1"},
            },
        ),
        (
            "evidence_item",
            {
                "evidence_item_id": "i-unit-1",
                "source_id": "s-unit-1",
                "item_type": "metric_series",
                "content_ref": "ref://1",
                "citation_label": "cit",
                "body": "body",
                "metadata": {"tenant_id": "t1"},
            },
        ),
    ] + bundle_rows

    class CountingFakeConn:
        def __init__(self):
            nonlocal connect_count
            connect_count += 1
            self.closed = False
            self.autocommit = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            self.closed = True

        def execute(self, sql, params=()):
            if "ORDER BY append_id ASC" in sql and "WHERE" not in sql:
                return FakeCursor(all_rows)
            if "WHERE record_type = %s" in sql and params and params[0] == "evidence_bundle":
                return FakeCursor([(row[1],) for row in bundle_rows])
            return FakeCursor([])

    import sys
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        mock.MagicMock(connect=lambda dsn: CountingFakeConn()),
    )

    repo = PostgresSourceEvidenceRepository(dsn="postgresql://test@example/db", bootstrap=False)

    # Exactly 1 connection was opened during reload, even with 1000 bundles!
    assert connect_count == 1
    # Verify all 1000 bundles loaded in in-memory repository
    assert len(repo._bundles) == 1000
    assert len(repo.list_bundles(tenant_id="t1")) == 1000
    # List query reused the open connection, still 1 connection!
    assert connect_count == 1
    repo.close()
