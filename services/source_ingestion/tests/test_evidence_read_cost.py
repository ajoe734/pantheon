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


def test_real_pg_summary_exact_fast_and_updated_by_independent_writes(volume_repo):
    repo = volume_repo
    expected = summarize_evidence_items_collection(repo.seeded_rows)
    durations = []
    for _ in range(5):
        start = time.monotonic()
        assert repo.summarize_evidence_items(VOLUME_TENANT) == expected
        durations.append(time.monotonic() - start)
    assert max(durations) < 0.5, durations
    record_measurement("summary_seconds", durations)
    with repo._get_connection() as conn:
        plan = conn.execute(
            f"EXPLAIN (ANALYZE, BUFFERS) SELECT evidence_source_type, evidence_link_type, "
            f"evidence_credibility_tier, evidence_verified, COUNT(*) FROM {repo.table} "
            "WHERE record_type = 'evidence_item' AND evidence_tenant_id = %s GROUP BY 1,2,3,4",
            (VOLUME_TENANT,),
        ).fetchall()
    plan_text = "\n".join(row[0] for row in plan)
    assert "source_evidence_summary_idx" in plan_text, plan_text
    record_measurement("summary_query_plan", plan_text)
    # A second SQL writer updates the generated projections atomically; no cache.
    import psycopg
    with psycopg.connect(repo.dsn) as conn:
        conn.execute(f"UPDATE {repo.table} SET payload = jsonb_set(payload, '{{confidence}}', '0.1'::jsonb) WHERE record_id = 'ev-00000'")
    try:
        changed = repo.summarize_evidence_items(VOLUME_TENANT)
        assert changed["total"] == 9000
        assert changed["verified_count"] == expected["verified_count"] - 1
    finally:
        with psycopg.connect(repo.dsn) as conn:
            conn.execute(f"UPDATE {repo.table} SET payload = jsonb_set(payload, '{{confidence}}', '1.0'::jsonb) WHERE record_id = 'ev-00000'")
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
