"""Real PostgreSQL cost and exactness checks for ranking evidence reads."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from services.knowledge.evidence.models import EvidenceItem, summarize_evidence_items_collection
from services.knowledge.evidence.repository import InMemoryEvidenceRepository
from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository
from services.source_ingestion.tests.test_quarter_evidence_owner_read import (
    Q1, TENANT, _item, _put, client_for,
)

TYPES = ("artifact", "capital_pool", "capital_sleeve", "paper_ledger", "persona",
         "persona_binding", "persona_capital_binding", "runtime", "runtime_binding",
         "strategy", "strategy_spec")
VOLUME_TENANT = "pantheon-dev"


def test_summary_is_opt_out_and_legacy_callers_keep_the_population(client_for):
    class Counting(InMemoryEvidenceRepository):
        calls = 0

        def summarize_evidence_items(self, *a, **kw):
            self.calls += 1
            return super().summarize_evidence_items(*a, **kw)

    repo = Counting()
    _put(repo, _item("x", available=Q1[0]))
    client = client_for(repo)
    path = "/api/source-ingest/evidence/items"
    for flag in (None, True, False):
        before = repo.calls
        response = client.get(path, params={} if flag is None else {"include_summary": flag})
        assert response.status_code == 200
        body = response.json()
        assert repo.calls - before == (flag is not False)
        assert body["summary"] == (None if flag is False else repo.summarize_evidence_items(TENANT))
        if flag is not None:
            assert body["filters"]["include_summary"] is flag


@pytest.mark.parametrize("key", ["linked_object_summary", "linked_entity_type", "linked_decisions", "linked_entities", "related_entities"])
def test_owner_link_shapes_filter_and_echo_without_changing_summary(client_for, key):
    repo = InMemoryEvidenceRepository()
    link = {"entity_type": " PERSONA ", "entity_ref": "p-001"}
    metadata = {"tenant_id": TENANT, key: (
        " PERSONA " if key == "linked_entity_type" else link if key == "linked_object_summary" else [link]
    )}
    _put(repo, replace(_item("linked", available=Q1[0]), metadata=metadata))
    _put(repo, _item("raw", available=Q1[0]))
    result = client_for(repo).get("/api/source-ingest/evidence/items", params={
        "linked_entity_types": "Persona, persona", "include_summary": False,
    })
    assert result.status_code == 200
    body = result.json()
    assert body["total"] == 1 and body["items"][0]["evidence_item_id"] == "linked"
    assert body["filters"] == {"linked_entity_types": ["persona"], "include_summary": False}


@pytest.fixture(scope="module")
def volume_repo():
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn
    import psycopg

    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("real PostgreSQL required; set SOURCE_INGEST_TEST_POSTGRES_DSN")
    schema = f"evidence_cost_{uuid.uuid4().hex[:12]}"
    repo = PostgresSourceEvidenceRepository(dsn, table=f"{schema}.evidence", max_connections=16)
    rows = []
    for i in range(9000):
        metadata = {"tenant_id": VOLUME_TENANT, "source_class": "market" if i < 7200 else "internal",
                    "link_type": "supporting" if i % 2 else "context",
                    "raw_storage_refs": ["heavy-list-omitted-" * 400]}
        kind = "source_record" if i < 7200 else TYPES[i % len(TYPES)]
        link = {"entity_type": kind, "entity_ref": f"raw-{i}" if i < 7200 else "p-001"}
        key = ("linked_object_summary", "linked_decisions", "linked_entities", "related_entities", "linked_entity_type")[i % 5]
        metadata[key] = kind if key == "linked_entity_type" else link if key == "linked_object_summary" else [link]
        rows.append(replace(_item(f"ev-{i:05d}", available="2026-02-15T00:00:00Z"),
                            metadata=metadata, confidence=0.5 if i % 3 else 1.0, body="body-" * 400))
    # Seed persisted owner records without 9000 individual commit round-trips.
    with psycopg.connect(dsn) as conn:
        with conn.cursor().copy(f"COPY {repo.table} (record_type, record_id, payload) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(("evidence_item", row.evidence_item_id, json.dumps(row.to_dict())))
        conn.execute(f"ANALYZE {repo.table}")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"VACUUM ANALYZE {repo.table}")
    repo.seeded_rows = rows
    try:
        yield repo
    finally:
        repo.close()
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def record_measurement(key, value):
    """Optional task artifact sink; normal test runs never edit repository evidence."""
    output = os.getenv("SOURCE_EVIDENCE_COST_MEASUREMENTS")
    if output:
        with Path(output).open("a") as stream:
            stream.write(json.dumps({key: value}) + "\n")


def _plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(child)


def _assert_id_first_page_plan(explain, page_size=200):
    """Check bounded payload reads without prescribing PostgreSQL's index choice."""
    root = explain[0]["Plan"]
    nodes = list(_plan_nodes(root))
    pages = [node for node in nodes if node["Node Type"] == "Limit"
             and node.get("Output") == ["evidence.append_id"]]
    assert len(pages) == 1, "expected an id-only page Limit"
    page = pages[0]
    assert page["Actual Rows"] == page_size and page["Actual Loops"] == 1, page
    page_nodes = list(_plan_nodes(page))
    assert not any("Sort" in node["Node Type"] for node in page_nodes), page
    id_scans = [node for node in page_nodes if node.get("Relation Name") == "evidence"]
    assert id_scans, page
    for scan in id_scans:
        assert scan["Node Type"] in {"Index Scan", "Index Only Scan"}, scan
        assert scan["Output"] == ["evidence.append_id"], scan
    # An outer sort of the 200 selected rows is fine; scanning all payloads is not.
    payload_scans = [node for node in nodes
                     if node.get("Relation Name") == "evidence" and node.get("Alias") == "t"]
    assert payload_scans, root
    assert not any(node["Node Type"] == "Seq Scan" for node in nodes
                   if node.get("Relation Name") == "evidence"), root
    assert sum(node["Actual Rows"] * node["Actual Loops"]
               for node in payload_scans) == page_size, payload_scans
    assert root["Actual Rows"] == page_size and root["Actual Loops"] == 1, root


def test_real_pg_summary_exact_and_updated_by_independent_writes(volume_repo):
    repo = volume_repo
    expected = summarize_evidence_items_collection(repo.seeded_rows)
    durations = []
    for _ in range(5):
        start = time.monotonic()
        assert repo.summarize_evidence_items(VOLUME_TENANT) == expected
        durations.append(time.monotonic() - start)
    record_measurement("summary_seconds", durations)
    with repo._get_connection() as conn:
        plan = conn.execute(
            f"EXPLAIN (ANALYZE, BUFFERS) SELECT evidence_source_type, evidence_link_type, "
            f"evidence_credibility_tier, evidence_verified, COUNT(*) FROM {repo.table} "
            "WHERE record_type = 'evidence_item' AND evidence_tenant_id = %s GROUP BY 1,2,3,4",
            (VOLUME_TENANT,),
        ).fetchall()
    plan_text = "\n".join(row[0] for row in plan)
    record_measurement("summary_query_plan", plan_text)
    # A second SQL writer updates the generated projections atomically; no cache.
    import psycopg
    # Verification is a recorded method + time, not a confidence: the writer adds one.
    record = json.dumps({"method": "manual_review", "verified_at": "2026-02-16T00:00:00Z"})
    with psycopg.connect(repo.dsn) as conn:
        conn.execute(
            f"UPDATE {repo.table} SET payload = jsonb_set(payload, '{{metadata,verification}}', %s::jsonb) WHERE record_id = 'ev-00000'",
            (record,),
        )
    try:
        changed = repo.summarize_evidence_items(VOLUME_TENANT)
        assert changed["total"] == 9000
        assert "verified_count" not in changed and "verified_evidence" not in changed
        tiers = changed["by_credibility_tier"]
        assert tiers.get("verified", 0) == expected["by_credibility_tier"].get("verified", 0) + 1
        assert tiers["unverified"] == expected["by_credibility_tier"]["unverified"] - 1
    finally:
        with psycopg.connect(repo.dsn) as conn:
            conn.execute(f"UPDATE {repo.table} SET payload = payload #- '{{metadata,verification}}' WHERE record_id = 'ev-00000'")
    assert repo.summarize_evidence_items(VOLUME_TENANT) == expected


def test_real_pg_filtered_pages_match_memory_and_never_transfer_source_records(volume_repo):
    repo = volume_repo
    memory = InMemoryEvidenceRepository()
    for row in repo.seeded_rows:
        _put(memory, row)
    started = time.monotonic()
    seen = []
    for offset in range(0, 1800, 500):
        kwargs = dict(limit=500, offset=offset, captured_from=Q1[0], captured_before=Q1[1], linked_entity_types=TYPES)
        page, total = repo.list_evidence_items_page(VOLUME_TENANT, **kwargs)
        expected, expected_total = memory.list_evidence_items_page(VOLUME_TENANT, **kwargs)
        assert total == expected_total == 1800
        assert [r.to_dict() for r in page] == [r.to_dict() for r in expected]
        seen.extend(page)
    elapsed = time.monotonic() - started
    assert len(seen) == len({r.evidence_item_id for r in seen}) == 1800
    assert all(int(r.evidence_item_id.split("-")[1]) >= 7200 for r in seen)
    # Includes Python reference comparisons; the BFF integration records owner-only timing.
    record_measurement("owner_pages_with_parity_seconds", elapsed)


def test_real_pg_existing_table_migrates_idempotently_and_link_updates_stay_exact(volume_repo):
    import psycopg
    from services.source_ingestion.tests.test_quarter_evidence_owner_read import _source_for

    schema = f"evidence_legacy_{uuid.uuid4().hex[:10]}"
    table = f'"{schema}".evidence'
    item = replace(_item("legacy", available=Q1[0]), metadata={
        "tenant_id": TENANT, "linked_decisions": [{"entity_type": " PERSONA ", "entity_ref": "p-001"}],
        "linked_entities": 123,
    })
    with psycopg.connect(volume_repo.dsn) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f"CREATE TABLE {table} (append_id BIGSERIAL PRIMARY KEY, record_id TEXT NOT NULL, record_type TEXT NOT NULL, payload JSONB NOT NULL, UNIQUE(record_type,record_id))")
        for kind, record_id, payload in (("source_record", item.source_id, _source_for(item).to_dict()),
                                         ("evidence_item", item.evidence_item_id, item.to_dict())):
            conn.execute(f"INSERT INTO {table} (record_type,record_id,payload) VALUES (%s,%s,%s::jsonb)",
                         (kind, record_id, json.dumps(payload)))
    repo = None
    try:
        repo = PostgresSourceEvidenceRepository(volume_repo.dsn, table=f"{schema}.evidence")
        repo._bootstrap()  # repeat startup against already-migrated data
        kwargs = dict(limit=500, linked_entity_types=("persona",), captured_from=Q1[0], captured_before=Q1[1])
        assert repo.list_evidence_items_page(TENANT, **kwargs)[1] == 1
        assert repo.summarize_evidence_items(TENANT)["total"] == 1
        with psycopg.connect(volume_repo.dsn) as conn:
            conn.execute(f"UPDATE {table} SET payload = jsonb_set(payload, '{{metadata,linked_decisions}}', '[]'::jsonb)")
        assert repo.list_evidence_items_page(TENANT, **kwargs)[1] == 0
        assert repo.summarize_evidence_items("other-tenant")["total"] == 0
    finally:
        if repo:
            repo.close()
        with psycopg.connect(volume_repo.dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_real_pg_deep_pages_are_exact_and_plan_is_id_first():
    """43000-row table: exact pages and bounded payload reads at deep offsets."""
    import psycopg
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn

    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("real PostgreSQL required; set SOURCE_INGEST_TEST_POSTGRES_DSN")
    schema = f"evidence_deep_{uuid.uuid4().hex[:12]}"
    repo = PostgresSourceEvidenceRepository(dsn, table=f"{schema}.evidence", max_connections=4)
    try:
        # Real row shape: confidence 1.0, source_class by_source_type mix, heavy body/raw refs.
        classes = ["official_reference"] * 20102 + ["source_record"] * 18503 + ["research_grade"] * 2770 + ["taiwan_chip"] * 1447 + ["internal"] * 178
        with psycopg.connect(dsn) as conn:
            with conn.cursor().copy(f"COPY {repo.table} (record_type, record_id, payload) FROM STDIN") as copy:
                for i, klass in enumerate(classes):
                    row = replace(
                        _item(f"evi-{i:06d}", available="2026-02-15T00:00:00Z"),
                        metadata={"tenant_id": VOLUME_TENANT, "source_class": klass, "raw_storage_refs": ["heavy-" * 300]},
                        confidence=1.0, body="body-" * 300,
                    )
                    copy.write_row(("evidence_item", row.evidence_item_id, json.dumps(row.to_dict())))
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f"VACUUM ANALYZE {repo.table}")
        timings = {}
        for offset in (0, 20000, 41000):
            best = []
            for _ in range(3):
                start = time.monotonic()
                items, total = repo.list_evidence_items_page(VOLUME_TENANT, limit=200, offset=offset)
                best.append(time.monotonic() - start)
            assert total == len(classes) == 43000
            assert len(items) == min(200, len(classes) - offset)
            assert [item.evidence_item_id for item in items] == [
                f"evi-{i:06d}" for i in range(len(classes) - 1 - offset,
                                             len(classes) - 1 - offset - 200, -1)
            ]
            timings[offset] = min(best)
        # CI load is not a read-cost invariant. Keep timings observational only.
        record_measurement("deep_page_seconds_by_offset", timings)
        id_first_sql = (
            f"SELECT payload FROM {repo.table} t JOIN ("
            f"SELECT append_id FROM {repo.table} WHERE record_type = 'evidence_item' AND evidence_tenant_id = %s "
            "ORDER BY append_id DESC LIMIT 200 OFFSET 41000) page USING (append_id) ORDER BY t.append_id DESC"
        )
        explain_sql = "EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) "
        with repo._get_connection() as conn:
            plan = conn.execute(explain_sql + id_first_sql, (VOLUME_TENANT,)).fetchone()[0]
            payload_first_plan = conn.execute(
                explain_sql + f"SELECT payload FROM {repo.table} "
                "WHERE record_type = 'evidence_item' AND evidence_tenant_id = %s "
                "ORDER BY append_id DESC LIMIT 200 OFFSET 41000",
                (VOLUME_TENANT,),
            ).fetchone()[0]
        _assert_id_first_page_plan(plan)
        # Negative control: 200 returned rows alone must not satisfy the guard.
        with pytest.raises(AssertionError, match="id-only page Limit"):
            _assert_id_first_page_plan(payload_first_plan)
        record_measurement("deep_page_query_plan", plan)
        record_measurement("rejected_payload_first_query_plan", payload_first_plan)
        with repo._get_connection() as conn:
            size = conn.execute(
                "SELECT pg_relation_size(%s::regclass), pg_total_relation_size(%s::regclass)",
                (f"{schema}.source_evidence_page_idx", f"{schema}.evidence"),
            ).fetchone()
        record_measurement("page_idx_bytes_and_table_total_bytes", list(size))
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP INDEX {schema}.source_evidence_page_idx")
            # The primary-key alternative seen in CI is equally valid. Exercise
            # it with the same assertion, without forcing any planner settings.
            alternative_plan = conn.execute(explain_sql + id_first_sql, (VOLUME_TENANT,)).fetchone()[0]
            _assert_id_first_page_plan(alternative_plan)
            record_measurement("id_first_plan_without_page_index", alternative_plan)
            start = time.monotonic()
            conn.execute(
                f"CREATE INDEX source_evidence_page_idx ON {repo.table} (evidence_tenant_id, append_id DESC) WHERE record_type = 'evidence_item'"
            )
        record_measurement("page_idx_build_seconds", time.monotonic() - start)
    finally:
        repo.close()
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_real_pg_tenant_item_under_bare_record_id_is_readable_by_detail_as_the_list_shows_it():
    import psycopg
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn

    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("real PostgreSQL required; set SOURCE_INGEST_TEST_POSTGRES_DSN")
    schema = f"evidence_bare_{uuid.uuid4().hex[:12]}"
    repo = PostgresSourceEvidenceRepository(dsn, table=f"{schema}.evidence")
    try:
        mine = replace(_item("evi-607b5b55229e8f72", available=Q1[0]), metadata={"tenant_id": VOLUME_TENANT})
        theirs = replace(_item("evi-foreign", available=Q1[0]), metadata={"tenant_id": "other-tenant"})
        with psycopg.connect(dsn) as conn:
            for row in (mine, theirs):  # stored under the bare id, not the tenant-scoped one
                conn.execute(
                    f"INSERT INTO {repo.table} (record_type, record_id, payload) VALUES ('evidence_item', %s, %s::jsonb)",
                    (row.evidence_item_id, json.dumps(row.to_dict())),
                )
        listed, total = repo.list_evidence_items_page(VOLUME_TENANT, limit=10)
        assert [i.evidence_item_id for i in listed] == ["evi-607b5b55229e8f72"] and total == 1
        assert repo.get_evidence_item("evi-607b5b55229e8f72", tenant_id=VOLUME_TENANT).evidence_item_id == "evi-607b5b55229e8f72"
        assert repo.get_evidence_item("evi-foreign", tenant_id=VOLUME_TENANT) is None
    finally:
        repo.close()
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
