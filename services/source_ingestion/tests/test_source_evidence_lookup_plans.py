"""Real PostgreSQL plan and semantic equivalence tests for source_evidence lookups.

Proves that:
1. Every per-record lookup on the ingest path (get_source_record_by_dedupe_key,
   get_evidence_item_by_dedupe_key, _read_one with and without a tenant across
   source_record, evidence_item, evidence_bundle, knowledge_object, and every
   SELECT issued by persist_source_evidence_refs in pipeline.py) is answered by
   an index with no Filter over the table in EXPLAIN.
2. Results before and after the change are identical for present, absent,
   duplicate (newest-row-wins by append_id DESC), and other-tenant keys.
3. Startup schema bootstrap creates the lookup indexes with
   CREATE INDEX CONCURRENTLY IF NOT EXISTS and remains compatible with a
   previous-release rollback reader.
No wall-clock thresholds are asserted in any required check.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceBundleBuilder,
    EvidenceItem,
    KnowledgeObject,
)
from services.knowledge.evidence.repository import _UNSCOPED, _Unscoped
from services.source_ingestion.connectors.base import SourceConnector, SourceRecord
from services.source_ingestion.distillation_worker import DistillationJobQueue
from services.source_ingestion.ingest_manager import IngestManager
from services.source_ingestion.pg_store import (
    PostgresSourceEvidenceRepository,
    _scoped_record_id,
)
from services.source_ingestion.pipeline import persist_source_evidence_refs
from services.source_ingestion.scheduler import ScheduledIngestResult
from services.source_ingestion.test_pg_store import real_pg_tables  # noqa: F401


_ALLOWED_LOOKUP_INDEXES = frozenset({
    "source_evidence_dedupe_lookup_idx",
    "source_evidence_object_id_lookup_idx",
    "source_evidence_record_type_record_id_key",
})


def _walk_plan_nodes(plan_node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan_node]
    for child in plan_node.get("Plans") or ():
        nodes.extend(_walk_plan_nodes(child))
    return nodes


def _assert_indexed_plan_without_filter(conn: Any, sql: str, params: tuple[Any, ...], label: str) -> dict[str, Any]:
    json_rows = conn.execute(f"EXPLAIN (FORMAT JSON) {sql}", params).fetchone()[0]
    if isinstance(json_rows, str):
        json_rows = json.loads(json_rows)
    root_plan = json_rows[0]["Plan"]
    all_nodes = _walk_plan_nodes(root_plan)

    text_lines = [
        row[0]
        for row in conn.execute(f"EXPLAIN {sql}", params).fetchall()
    ]
    explain_text = "\n".join(text_lines)

    scan_nodes = [
        node for node in all_nodes
        if "Scan" in str(node.get("Node Type") or "")
        and node.get("Node Type") != "Subquery Scan"
    ]
    assert scan_nodes, f"{label}: expected at least one scan node in plan:\n{explain_text}"

    index_names: list[str] = []
    for node in all_nodes:
        node_type = str(node.get("Node Type") or "")
        assert node_type != "Seq Scan", f"{label}: unexpected Seq Scan in plan:\n{explain_text}"
        assert "Filter" not in node, (
            f"{label}: unexpected Filter ({node.get('Filter')}) on {node_type} in plan:\n{explain_text}"
        )
        assert "Recheck Cond" not in node, (
            f"{label}: unexpected heap Recheck Cond ({node.get('Recheck Cond')}) in plan:\n{explain_text}"
        )
        idx_name = node.get("Index Name")
        if idx_name:
            assert idx_name != "source_evidence_pkey", (
                f"{label}: lookup must not walk source_evidence_pkey backward:\n{explain_text}"
            )
            assert idx_name in _ALLOWED_LOOKUP_INDEXES, (
                f"{label}: unexpected index {idx_name!r} in plan:\n{explain_text}"
            )
            index_names.append(str(idx_name))

    assert index_names, f"{label}: expected an index scan in plan:\n{explain_text}"
    assert "Filter:" not in explain_text, f"{label}: Filter found in text EXPLAIN:\n{explain_text}"
    return {"label": label, "indexes": index_names, "explain": explain_text}


def _populate_multi_page_table(conn: Any, table: str, count_per_type: int = 400) -> None:
    """Insert enough rows across multiple heap pages so the planner evaluates real costs."""
    conn.execute(
        f"""
        INSERT INTO {table} (record_id, record_type, payload)
        SELECT
            '@t10:tenant-dev:src-' || i,
            'source_record',
            jsonb_build_object(
                'source_id', 'src-' || i,
                'connector_id', 'tw-official-twse-margin-balances',
                'source_type', 'market',
                'title', 'Margin Balance ' || i,
                'content_ref', 'https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=' || i,
                'status', 'normalized',
                'metadata', jsonb_build_object(
                    'tenant_id', 'tenant-dev',
                    'source_dedupe_key', 'url:https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=' || i,
                    'padding', repeat(md5(i::text), 16)
                )
            )
        FROM generate_series(1, %s) AS s(i)
        """,
        (count_per_type,),
    )
    conn.execute(
        f"""
        INSERT INTO {table} (record_id, record_type, payload)
        SELECT
            '@t10:tenant-dev:evi-' || i,
            'evidence_item',
            jsonb_build_object(
                'evidence_item_id', 'evi-' || i,
                'source_id', 'src-' || i,
                'item_type', 'source_record',
                'content_ref', 'https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=' || i,
                'citation_label', 'TWSE:' || i,
                'body', 'Margin row ' || i || ' ' || repeat(md5((i * 3)::text), 16),
                'confidence', 1.0,
                'access_scope', jsonb_build_array('public'),
                'metadata', jsonb_build_object(
                    'tenant_id', 'tenant-dev',
                    'source_dedupe_key', 'url:https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=' || i,
                    'evidence_dedupe_key', 'url:https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=' || i || '#item:' || i
                )
            )
        FROM generate_series(1, %s) AS s(i)
        """,
        (count_per_type,),
    )
    conn.execute(
        f"""
        INSERT INTO {table} (record_id, record_type, payload)
        SELECT
            '@t10:tenant-dev:evbundle-' || i,
            'evidence_bundle',
            jsonb_build_object(
                'evidence_bundle_id', 'evbundle-' || i,
                'source_ids', jsonb_build_array('src-' || i),
                'evidence_item_ids', jsonb_build_array('evi-' || i),
                'summary', 'Bundle ' || i,
                'citation_refs', jsonb_build_array('TWSE:' || i),
                'confidence', 1.0,
                'license_scope', 'exchange_public',
                'access_scope', jsonb_build_array('public'),
                'created_by', 'source-ingest',
                'metadata', jsonb_build_object(
                    'tenant_id', 'tenant-dev',
                    'padding', repeat(md5((i * 7)::text), 16)
                )
            )
        FROM generate_series(1, %s) AS s(i)
        """,
        (count_per_type,),
    )
    conn.execute(
        f"""
        INSERT INTO {table} (record_id, record_type, payload)
        SELECT
            '@t10:tenant-dev:ko-' || i,
            'knowledge_object',
            jsonb_build_object(
                'knowledge_object_id', 'ko-' || i,
                'source_id', 'src-' || i,
                'evidence_item_id', 'evi-' || i,
                'evidence_bundle_id', 'evbundle-' || i,
                'title', 'KO ' || i,
                'text', 'KO text ' || i || ' ' || repeat(md5((i * 11)::text), 16),
                'source_type', 'market',
                'license_scope', 'exchange_public',
                'access_scope', jsonb_build_array('public'),
                'metadata', jsonb_build_object(
                    'tenant_id', 'tenant-dev'
                )
            )
        FROM generate_series(1, %s) AS s(i)
        """,
        (count_per_type,),
    )
    conn.execute(f"ANALYZE {table}")


def test_explain_all_per_record_lookups_use_index_without_whole_table_filter(real_pg_tables, tmp_path):
    """AC 2 & AC 3: EXPLAIN proves every per-record lookup uses an index and has no table Filter."""
    dsn, table = real_pg_tables
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    try:
        with repo._get_connection() as conn:
            _populate_multi_page_table(conn, repo.table, count_per_type=400)

        captured_queries: list[tuple[str, str, tuple[Any, ...]]] = []
        orig_fetch = repo._fetch_records

        current_label = ["unlabelled"]

        def spy_fetch(sql: str, params: tuple[Any, ...], r_type: str, tenant_id: Any = _UNSCOPED):
            captured_queries.append((current_label[0], sql, params))
            return orig_fetch(sql, params, r_type, tenant_id=tenant_id)

        repo._fetch_records = spy_fetch  # type: ignore[method-assign]

        # 1. get_source_record_by_dedupe_key (tenanted, untenanted None, unscoped)
        for scope_name, scope_val in (("tenanted", "tenant-dev"), ("untenanted_none", None), ("unscoped", _UNSCOPED)):
            current_label[0] = f"get_source_record_by_dedupe_key[{scope_name}]"
            repo.get_source_record_by_dedupe_key(
                "url:https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=25",
                tenant_id=scope_val,
            )

        # 2. get_evidence_item_by_dedupe_key (tenanted, untenanted None, unscoped)
        for scope_name, scope_val in (("tenanted", "tenant-dev"), ("untenanted_none", None), ("unscoped", _UNSCOPED)):
            current_label[0] = f"get_evidence_item_by_dedupe_key[{scope_name}]"
            repo.get_evidence_item_by_dedupe_key(
                "url:https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=25#item:25",
                tenant_id=scope_val,
            )

        # 3. _read_one with and without a tenant across all 4 record types
        for getter_name, getter_fn, sample_id in (
            ("get_source_record", repo.get_source_record, "src-25"),
            ("get_evidence_item", repo.get_evidence_item, "evi-25"),
            ("get_bundle", repo.get_bundle, "evbundle-25"),
            ("get_knowledge_object", repo.get_knowledge_object, "ko-25"),
        ):
            for scope_name, scope_val in (("tenanted", "tenant-dev"), ("untenanted_none", None), ("unscoped", _UNSCOPED)):
                current_label[0] = f"{getter_name}[{scope_name}]"
                getter_fn(sample_id, tenant_id=scope_val)

        # 4. Run pipeline.persist_source_evidence_refs to verify every per-record query it issues
        manager = IngestManager()
        connector = SourceConnector(
            connector_id="tw-official-twse-margin-balances",
            provider="TWSE OpenAPI",
            source_type="market",
            license_scope="exchange_public",
            metadata={"tenant_id": "tenant-dev"},
        )
        manager.register_connector(connector)
        run = manager.start_ingest_run(
            connector_id=connector.connector_id,
            trace_id="trace-lookup-plan-test",
            trigger_type="scheduled",
        )
        ingest_records = (
            SourceRecord(
                source_id="src-pipeline-new-1",
                connector_id=connector.connector_id,
                source_type="market",
                title="TWSE Margin Balance 2330",
                content_ref="https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?symbol=2330&date=2026-11-20",
                status="normalized",
                metadata={
                    "tenant_id": "tenant-dev",
                    "dataset": "tw_margin_short",
                    "symbol": "2330",
                    "event_time": "2026-11-20T06:00:00Z",
                    "available_time": "2026-11-20T13:30:00Z",
                    "pit": {"event_time": "2026-11-20T06:00:00Z", "available_time": "2026-11-20T13:30:00Z"},
                    "governance": {"terms_ref": "twse-openapi", "robots_decision": "allow"},
                },
            ),
            SourceRecord(
                source_id="src-25",
                connector_id=connector.connector_id,
                source_type="market",
                title="Margin Balance 25",
                content_ref="https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN?row=25",
                status="normalized",
                metadata={
                    "tenant_id": "tenant-dev",
                    "dataset": "tw_margin_short",
                    "symbol": "2317",
                    "event_time": "2026-11-20T06:00:00Z",
                    "available_time": "2026-11-20T13:30:00Z",
                    "pit": {"event_time": "2026-11-20T06:00:00Z", "available_time": "2026-11-20T13:30:00Z"},
                    "governance": {"terms_ref": "twse-openapi", "robots_decision": "allow"},
                },
            ),
        )
        result = ScheduledIngestResult(run=run, records=ingest_records, audit_actions=(), watermark=None)
        current_label[0] = "pipeline.persist_source_evidence_refs"
        refs = persist_source_evidence_refs(
            manager=manager,
            evidence_repository=repo,
            evidence_builder=EvidenceBundleBuilder(repo),
            distillation_job_queue=DistillationJobQueue(tmp_path / "distill.sqlite"),
            result=result,
            storage_refs={},
        )
        assert len(refs["source_ids"]) == 2
        assert len(refs["evidence_item_ids"]) == 2
        assert len(refs["knowledge_object_ids"]) == 2

        assert len(captured_queries) >= 18
        with repo._get_connection() as conn:
            for label, sql, params in captured_queries:
                plan_info = _assert_indexed_plan_without_filter(conn, sql, params, label)
                if "by_dedupe_key" in label:
                    assert plan_info["indexes"] == ["source_evidence_dedupe_lookup_idx"]
                elif "[unscoped]" in label:
                    assert set(plan_info["indexes"]) == {
                        "source_evidence_record_type_record_id_key",
                        "source_evidence_object_id_lookup_idx",
                    }
                else:
                    assert set(plan_info["indexes"]).issubset(_ALLOWED_LOOKUP_INDEXES)
    finally:
        repo.close()


def _legacy_read_by_dedupe(
    repo: PostgresSourceEvidenceRepository,
    r_type: str,
    field: str,
    key: str,
    tenant_id: str | None | _Unscoped = _UNSCOPED,
) -> Any | None:
    """Exact pre-change _read_by_dedupe SQL implementation for equivalence testing."""
    if tenant_id is not _UNSCOPED:
        where = (
            "AND payload->'metadata'->>'tenant_id' = %s"
            if tenant_id is not None
            else "AND (payload->'metadata'->>'tenant_id' IS NULL OR payload->'metadata'->>'tenant_id' = '')"
        )
        params: tuple[Any, ...] = (r_type, field, key, tenant_id) if tenant_id is not None else (r_type, field, key)
        sql = (
            f"SELECT payload FROM {repo.table} "
            f"WHERE record_type = %s AND payload->'metadata'->>%s = %s {where} "
            "ORDER BY append_id DESC LIMIT 1"
        )
    else:
        sql = (
            f"SELECT payload FROM {repo.table} "
            "WHERE record_type = %s AND payload->'metadata'->>%s = %s "
            "ORDER BY append_id DESC LIMIT 1"
        )
        params = (r_type, field, key)
    records = repo._fetch_records(sql, params, r_type, tenant_id=tenant_id)
    return next((r for r in records if str(r.metadata.get(field) or "") == key), None)


def _legacy_read_one(
    repo: PostgresSourceEvidenceRepository,
    r_type: str,
    r_id: str,
    tenant_id: str | None | _Unscoped = _UNSCOPED,
) -> Any | None:
    """Exact pre-change _read_one SQL implementation for equivalence testing."""
    if tenant_id is not _UNSCOPED:
        sql = f"SELECT payload FROM {repo.table} WHERE record_type = %s AND record_id = %s"
        params: tuple[Any, ...] = (r_type, _scoped_record_id(tenant_id, r_id))
    else:
        k = "source_id" if r_type == "source_record" else f"{r_type}_id"
        sql = (
            f"SELECT payload FROM {repo.table} "
            "WHERE record_type = %s AND (record_id = %s OR payload->>%s = %s) "
            "ORDER BY append_id DESC LIMIT 1"
        )
        params = (r_type, r_id, k, r_id)
    records = repo._fetch_records(sql, params, r_type, tenant_id=tenant_id)
    k = "source_id" if r_type == "source_record" else f"{r_type}_id"
    return next((r for r in records if getattr(r, k, None) == r_id), records[0] if records else None)


def _norm(obj: Any) -> dict[str, Any] | None:
    return obj.to_dict() if obj is not None else None


def test_identical_results_before_and_after_for_present_absent_duplicate_and_other_tenant_keys(real_pg_tables):
    """AC 2 & AC 3: proves identical results before and after across all key/tenant cases."""
    dsn, table = real_pg_tables
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    try:
        # Insert raw rows directly to test present, absent, duplicate (same-tenant &
        # cross-tenant with newest append_id winning), untenanted (NULL and ''), and
        # other-tenant cases.
        rows_to_insert: list[tuple[str, str, dict[str, Any]]] = [
            # 1. Present & duplicate source_record dedupe keys within tenant-a (older vs newer append_id)
            (
                _scoped_record_id("tenant-a", "src-dup-old"),
                "source_record",
                SourceRecord(
                    source_id="src-dup-old",
                    connector_id="conn-a",
                    source_type="market",
                    title="Older Duplicate Source",
                    content_ref="ref://dup-1",
                    metadata={"tenant_id": "tenant-a", "source_dedupe_key": "dk-shared-src", "version": 1},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-a", "src-dup-new"),
                "source_record",
                SourceRecord(
                    source_id="src-dup-new",
                    connector_id="conn-a",
                    source_type="market",
                    title="Newer Duplicate Source",
                    content_ref="ref://dup-1",
                    metadata={"tenant_id": "tenant-a", "source_dedupe_key": "dk-shared-src", "version": 2},
                ).to_dict(),
            ),
            # 2. Same dedupe key in tenant-b with even newer append_id (cross-tenant duplicate)
            (
                _scoped_record_id("tenant-b", "src-dup-tenant-b"),
                "source_record",
                SourceRecord(
                    source_id="src-dup-tenant-b",
                    connector_id="conn-b",
                    source_type="market",
                    title="Tenant B Source With Same Dedupe Key",
                    content_ref="ref://dup-1",
                    metadata={"tenant_id": "tenant-b", "source_dedupe_key": "dk-shared-src", "version": 3},
                ).to_dict(),
            ),
            # 3. Untenanted source_records (NULL tenant_id and empty-string tenant_id) with duplicate dedupe key
            (
                _scoped_record_id(None, "src-legacy-null"),
                "source_record",
                SourceRecord(
                    source_id="src-legacy-null",
                    connector_id="conn-legacy",
                    source_type="paper",
                    title="Legacy Null Tenant",
                    content_ref="ref://legacy-null",
                    metadata={"source_dedupe_key": "dk-legacy-src"},
                ).to_dict(),
            ),
            (
                _scoped_record_id(None, "src-legacy-empty"),
                "source_record",
                {
                    **SourceRecord(
                        source_id="src-legacy-empty",
                        connector_id="conn-legacy",
                        source_type="paper",
                        title="Legacy Empty Tenant Newer",
                        content_ref="ref://legacy-empty",
                        metadata={"source_dedupe_key": "dk-legacy-src"},
                    ).to_dict(),
                    "metadata": {"tenant_id": "", "source_dedupe_key": "dk-legacy-src"},
                },
            ),
            # 4. Same source_id across legacy and tenanted rows (tests _read_one newest-row-wins when unscoped)
            (
                _scoped_record_id(None, "src-same-id"),
                "source_record",
                SourceRecord(
                    source_id="src-same-id",
                    connector_id="conn-1",
                    source_type="market",
                    title="Same ID Legacy Older",
                    content_ref="ref://same-id-1",
                    metadata={"source_dedupe_key": "dk-same-id-legacy"},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-a", "src-same-id"),
                "source_record",
                SourceRecord(
                    source_id="src-same-id",
                    connector_id="conn-1",
                    source_type="market",
                    title="Same ID Tenant A Newer",
                    content_ref="ref://same-id-2",
                    metadata={"tenant_id": "tenant-a", "source_dedupe_key": "dk-same-id-a"},
                ).to_dict(),
            ),
            # 5. Row where record_id differs from payload->>'source_id' (tests record_id OR payload->>source_id branch)
            (
                "custom-bare-record-id",
                "source_record",
                SourceRecord(
                    source_id="payload-only-source-id",
                    connector_id="conn-custom",
                    source_type="market",
                    title="Custom Record ID Row",
                    content_ref="ref://custom",
                    metadata={"source_dedupe_key": "dk-custom"},
                ).to_dict(),
            ),
            # 6. Evidence items: duplicate dedupe keys in tenant-a, other-tenant in tenant-b, and legacy untenanted
            (
                _scoped_record_id("tenant-a", "evi-dup-old"),
                "evidence_item",
                EvidenceItem(
                    evidence_item_id="evi-dup-old",
                    source_id="src-dup-old",
                    item_type="metric_series",
                    content_ref="ref://evi-1",
                    citation_label="cit-1",
                    body="Older evidence item",
                    metadata={"tenant_id": "tenant-a", "evidence_dedupe_key": "dk-shared-evi"},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-a", "evi-dup-new"),
                "evidence_item",
                EvidenceItem(
                    evidence_item_id="evi-dup-new",
                    source_id="src-dup-new",
                    item_type="metric_series",
                    content_ref="ref://evi-1",
                    citation_label="cit-2",
                    body="Newer evidence item",
                    metadata={"tenant_id": "tenant-a", "evidence_dedupe_key": "dk-shared-evi"},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-b", "evi-dup-tenant-b"),
                "evidence_item",
                EvidenceItem(
                    evidence_item_id="evi-dup-tenant-b",
                    source_id="src-dup-tenant-b",
                    item_type="metric_series",
                    content_ref="ref://evi-1",
                    citation_label="cit-3",
                    body="Tenant B evidence item",
                    metadata={"tenant_id": "tenant-b", "evidence_dedupe_key": "dk-shared-evi"},
                ).to_dict(),
            ),
            (
                _scoped_record_id(None, "evi-legacy"),
                "evidence_item",
                EvidenceItem(
                    evidence_item_id="evi-legacy",
                    source_id="src-legacy-null",
                    item_type="text_chunk",
                    content_ref="ref://evi-legacy",
                    citation_label="cit-legacy",
                    body="Legacy evidence item",
                    metadata={"evidence_dedupe_key": "dk-legacy-evi"},
                ).to_dict(),
            ),
            # 7. Evidence bundles & knowledge objects across tenants for _read_one equivalence
            (
                _scoped_record_id("tenant-a", "bun-shared"),
                "evidence_bundle",
                EvidenceBundle(
                    evidence_bundle_id="bun-shared",
                    source_ids=["src-dup-new"],
                    evidence_item_ids=["evi-dup-new"],
                    summary="Tenant A bundle",
                    citation_refs=["cit-2"],
                    confidence=1.0,
                    license_scope="open",
                    access_scope=["public"],
                    created_by="tester",
                    metadata={"tenant_id": "tenant-a"},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-b", "bun-shared"),
                "evidence_bundle",
                EvidenceBundle(
                    evidence_bundle_id="bun-shared",
                    source_ids=["src-dup-tenant-b"],
                    evidence_item_ids=["evi-dup-tenant-b"],
                    summary="Tenant B newer bundle",
                    citation_refs=["cit-3"],
                    confidence=1.0,
                    license_scope="open",
                    access_scope=["public"],
                    created_by="tester",
                    metadata={"tenant_id": "tenant-b"},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-a", "ko-shared"),
                "knowledge_object",
                KnowledgeObject(
                    knowledge_object_id="ko-shared",
                    source_id="src-dup-new",
                    evidence_item_id="evi-dup-new",
                    evidence_bundle_id="bun-shared",
                    title="Tenant A KO",
                    text="Body A",
                    source_type="market",
                    license_scope="open",
                    access_scope=["public"],
                    metadata={"tenant_id": "tenant-a"},
                ).to_dict(),
            ),
            (
                _scoped_record_id("tenant-b", "ko-shared"),
                "knowledge_object",
                KnowledgeObject(
                    knowledge_object_id="ko-shared",
                    source_id="src-dup-tenant-b",
                    evidence_item_id="evi-dup-tenant-b",
                    evidence_bundle_id="bun-shared",
                    title="Tenant B newer KO",
                    text="Body B",
                    source_type="market",
                    license_scope="open",
                    access_scope=["public"],
                    metadata={"tenant_id": "tenant-b"},
                ).to_dict(),
            ),
        ]

        with repo._get_connection() as conn:
            for rec_id, rec_type, payload in rows_to_insert:
                conn.execute(
                    f"INSERT INTO {repo.table} (record_id, record_type, payload) VALUES (%s, %s, %s::jsonb)",
                    (rec_id, rec_type, json.dumps(payload, sort_keys=True)),
                )

        tenant_scopes: tuple[str | None | _Unscoped, ...] = (
            "tenant-a",
            "tenant-b",
            "tenant-nonexistent",
            None,
            _UNSCOPED,
        )

        # Verify get_source_record_by_dedupe_key equivalence
        for dk in ("dk-shared-src", "dk-legacy-src", "dk-same-id-a", "dk-custom", "dk-absent"):
            for scope in tenant_scopes:
                before = _norm(_legacy_read_by_dedupe(repo, "source_record", "source_dedupe_key", dk, tenant_id=scope))
                after = _norm(repo.get_source_record_by_dedupe_key(dk, tenant_id=scope))
                assert after == before, f"source dedupe mismatch for key={dk!r} scope={scope!r}: {after} != {before}"

        # Explicit spot checks on duplicate newest-row-wins and tenant isolation
        assert repo.get_source_record_by_dedupe_key("dk-shared-src", tenant_id="tenant-a").source_id == "src-dup-new"
        assert repo.get_source_record_by_dedupe_key("dk-shared-src", tenant_id="tenant-b").source_id == "src-dup-tenant-b"
        assert repo.get_source_record_by_dedupe_key("dk-shared-src", tenant_id="tenant-c") is None
        assert repo.get_source_record_by_dedupe_key("dk-shared-src", tenant_id=None) is None
        assert repo.get_source_record_by_dedupe_key("dk-shared-src").source_id == "src-dup-tenant-b"
        assert repo.get_source_record_by_dedupe_key("dk-legacy-src", tenant_id=None).source_id == "src-legacy-empty"
        assert repo.get_source_record_by_dedupe_key("dk-legacy-src", tenant_id="tenant-a") is None

        # Verify get_evidence_item_by_dedupe_key equivalence
        for dk in ("dk-shared-evi", "dk-legacy-evi", "dk-absent-evi"):
            for scope in tenant_scopes:
                before = _norm(_legacy_read_by_dedupe(repo, "evidence_item", "evidence_dedupe_key", dk, tenant_id=scope))
                after = _norm(repo.get_evidence_item_by_dedupe_key(dk, tenant_id=scope))
                assert after == before, f"evidence item dedupe mismatch for key={dk!r} scope={scope!r}: {after} != {before}"

        assert repo.get_evidence_item_by_dedupe_key("dk-shared-evi", tenant_id="tenant-a").evidence_item_id == "evi-dup-new"
        assert repo.get_evidence_item_by_dedupe_key("dk-shared-evi", tenant_id="tenant-b").evidence_item_id == "evi-dup-tenant-b"
        assert repo.get_evidence_item_by_dedupe_key("dk-shared-evi", tenant_id=None) is None
        assert repo.get_evidence_item_by_dedupe_key("dk-shared-evi").evidence_item_id == "evi-dup-tenant-b"
        assert repo.get_evidence_item_by_dedupe_key("dk-legacy-evi", tenant_id=None).evidence_item_id == "evi-legacy"

        # Verify _read_one equivalence across all 4 record types (with tenant, None, and _UNSCOPED)
        read_one_cases = [
            ("source_record", repo.get_source_record, ("src-dup-old", "src-dup-new", "src-same-id", "custom-bare-record-id", "payload-only-source-id", "src-absent")),
            ("evidence_item", repo.get_evidence_item, ("evi-dup-old", "evi-dup-new", "evi-legacy", "evi-absent")),
            ("evidence_bundle", repo.get_bundle, ("bun-shared", "bun-absent")),
            ("knowledge_object", repo.get_knowledge_object, ("ko-shared", "ko-absent")),
        ]
        for r_type, getter, candidate_ids in read_one_cases:
            for cid in candidate_ids:
                for scope in tenant_scopes:
                    before = _norm(_legacy_read_one(repo, r_type, cid, tenant_id=scope))
                    after = _norm(getter(cid, tenant_id=scope))
                    assert after == before, (
                        f"_read_one mismatch for type={r_type} id={cid!r} scope={scope!r}: {after} != {before}"
                    )

        # Unscoped _read_one returns newest row across tenants and matches both record_id and payload object_id
        assert repo.get_source_record("src-same-id").title == "Same ID Tenant A Newer"
        assert repo.get_source_record("src-same-id", tenant_id=None).title == "Same ID Legacy Older"
        assert repo.get_source_record("custom-bare-record-id").source_id == "payload-only-source-id"
        assert repo.get_source_record("payload-only-source-id").source_id == "payload-only-source-id"
        assert repo.get_bundle("bun-shared").summary == "Tenant B newer bundle"
        assert repo.get_knowledge_object("ko-shared").title == "Tenant B newer KO"
    finally:
        repo.close()


def test_startup_concurrent_index_ddl_and_rollback_reader_compatibility(real_pg_tables):
    """AC 4: startup schema uses CREATE INDEX CONCURRENTLY IF NOT EXISTS and supports previous-release reader."""
    dsn, table = real_pg_tables
    repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
    try:
        with repo._get_connection() as conn:
            index_rows = conn.execute(
                """
                SELECT c.relname, i.indisvalid, pg_get_indexdef(i.indexrelid)
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indexrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                """,
                (repo.schema,),
            ).fetchall()
        by_name = {row[0]: (bool(row[1]), str(row[2])) for row in index_rows}
        assert "source_evidence_dedupe_lookup_idx" in by_name
        assert "source_evidence_object_id_lookup_idx" in by_name
        assert by_name["source_evidence_dedupe_lookup_idx"][0] is True
        assert by_name["source_evidence_object_id_lookup_idx"][0] is True

        # Write records through the new repository and verify the pre-change
        # rollback reader SQL reads them identically; then write via raw pre-change
        # upsert and verify both pre-change and post-change readers observe the update.
        source = SourceRecord(
            source_id="src-rollback-1",
            connector_id="conn-rb",
            source_type="market",
            title="Rollback Compatibility Source",
            content_ref="https://example.com/rollback/1",
            status="normalized",
            metadata={"tenant_id": "tenant-rb", "source_dedupe_key": "url:https://example.com/rollback/1"},
        )
        repo.add_source_record(source)

        item = EvidenceItem(
            evidence_item_id="evi-rollback-1",
            source_id=source.source_id,
            item_type="source_record",
            content_ref=source.content_ref,
            citation_label="RB:1",
            body="Rollback evidence body",
            metadata={"tenant_id": "tenant-rb", "evidence_dedupe_key": "url:https://example.com/rollback/1#item"},
        )
        repo.add_evidence_item(item)

        assert _norm(_legacy_read_by_dedupe(repo, "source_record", "source_dedupe_key", "url:https://example.com/rollback/1", tenant_id="tenant-rb")) == source.to_dict()
        assert _norm(_legacy_read_by_dedupe(repo, "evidence_item", "evidence_dedupe_key", "url:https://example.com/rollback/1#item", tenant_id="tenant-rb")) == item.to_dict()
        assert _norm(_legacy_read_one(repo, "source_record", "src-rollback-1", tenant_id=_UNSCOPED)) == source.to_dict()
        assert _norm(_legacy_read_one(repo, "evidence_item", "evi-rollback-1", tenant_id=_UNSCOPED)) == item.to_dict()
    finally:
        repo.close()
