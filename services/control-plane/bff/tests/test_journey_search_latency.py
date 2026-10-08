import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from services.control_plane.bff.trade_journey_projection_store import TradeJourneyProjectionStore
from services.trade_journey.projection_store import ProjectionStore


class _MockConn:
    def __init__(self, fetch_result=None):
        self.calls = []
        self.fetch_result = fetch_result if fetch_result is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.fetch_result

    description = [("journey_id",)]


def test_q_journey_only_exact_emits_equality():
    mock = _MockConn([{"journey_id": "tj-1"}])
    store = TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: mock)
    clauses, params = store._journey_where(
        tenant_id="t", environment="paper", filters={"q": "tj-1", "q_journey_only": True}
    )
    assert "journey_id = %s" in clauses
    assert params == ["t", "paper", "tj-1"]


def test_q_exact_journey_id_emits_equality():
    mock = _MockConn([{"journey_id": "tj-1"}])
    store = TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: mock)
    clauses, params = store._journey_where(
        tenant_id="t", environment="paper", filters={"q": "tj-1"}
    )
    assert "journey_id = %s" in clauses
    assert params == ["t", "paper", "tj-1"]


def test_q_exact_lookup_emits_index_backed_subquery():
    mock = _MockConn([])
    store = TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: mock)
    clauses, params = store._journey_where(
        tenant_id="t", environment="paper", filters={"q": "ord-1", "q_exact": True}
    )
    assert any("journey_id IN (" in c and "identity_links" in c for c in clauses)
    assert not any("jsonb_array_elements_text" in c for c in clauses)
    assert not any("jsonb_each" in c for c in clauses)


def test_q_substring_emits_trigram_backed_subquery():
    mock = _MockConn([])
    store = TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: mock)
    clauses, params = store._journey_where(
        tenant_id="t", environment="paper", filters={"q": "%ord-1%"}
    )
    assert any("journey_id IN (" in c and "ILIKE %s" in c for c in clauses)
    assert not any("jsonb_array_elements_text" in c for c in clauses)


def test_real_db_journey_search_exact_and_substring():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"test_search_{uuid4().hex[:8]}"
    ProjectionStore(dsn, schema=schema, bootstrap=True)

    now = datetime.now(timezone.utc)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            # Insert journeys
            for i in range(1, 11):
                jid = f"tj-search-{i:03d}"
                summary = {"identifiers": {"signal_id": [f"sig-search-{i:03d}"]}}
                cur.execute(
                    f"INSERT INTO {schema}.journeys (tenant_id, environment, journey_id, status, stage_coverage, is_terminal, first_occurred_at, last_occurred_at, first_ingested_seq, last_ingested_seq, current_identity_summary, projection_revision) VALUES ('t','paper',%s,'completed','{{}}',true,%s,%s,%s,%s,%s::jsonb,1)",
                    (jid, now, now, i, i, json.dumps(summary)),
                )
                cur.execute(
                    f"INSERT INTO {schema}.identity_links (tenant_id, environment, identifier_type, identifier_value, journey_id, first_ingested_seq, last_ingested_seq, first_occurred_at, last_occurred_at) VALUES ('t','paper','order_id',%s,%s,%s,%s,%s,%s)",
                    (f"ord-search-{i:03d}", jid, i, i, now, now),
                )
            cur.execute(f"VACUUM ANALYZE {schema}.journeys;")
            cur.execute(f"VACUUM ANALYZE {schema}.identity_links;")

        store = TradeJourneyProjectionStore(dsn, schema=schema, token_secret="s" * 16)

        # 1. Exact journey_id lookup
        res_jid = store.page_journeys(tenant_id="t", environment="paper", filters={"q": "tj-search-003"})
        assert res_jid.total == 1
        assert len(res_jid.items) == 1
        assert res_jid.items[0].journey_id == "tj-search-003"

        # 2. Exact identifier_value lookup (order_id)
        res_ord = store.page_journeys(tenant_id="t", environment="paper", filters={"q": "ord-search-005"})
        assert res_ord.total == 1
        assert res_ord.items[0].journey_id == "tj-search-005"

        # 3. Substring search matching multiple
        res_sub = store.page_journeys(tenant_id="t", environment="paper", filters={"q": "search-00"})
        assert res_sub.total == 9
        assert len(res_sub.items) == 9

        # 4. q_journey_only restricts to journey_id
        res_jo = store.page_journeys(
            tenant_id="t", environment="paper", filters={"q": "ord-search-005", "q_journey_only": True}
        )
        assert res_jo.total == 0

        # 5. Non-existent query
        res_none = store.page_journeys(tenant_id="t", environment="paper", filters={"q": "nonexistent-id"})
        assert res_none.total == 0
        assert len(res_none.items) == 0

        # 6. Pagination with q
        p1 = store.page_journeys(tenant_id="t", environment="paper", filters={"q": "search-0"}, page_size=4)
        assert p1.total == 10
        assert len(p1.items) == 4
        assert p1.next_page_token is not None

        p2 = store.page_journeys(
            tenant_id="t", environment="paper", filters={"q": "search-0"}, page_size=4, page_token=p1.next_page_token
        )
        assert p2.total == 10
        assert len(p2.items) == 4
        assert {item.journey_id for item in p1.items}.isdisjoint({item.journey_id for item in p2.items})
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
