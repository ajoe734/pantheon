import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.trade_journey.materializer import SHARED_IDENTIFIER_TYPES, identity_summary
from services.trade_journey.projection_store import BatchProjectionMutation, JourneyRow, ProjectionStore
from services.control_plane.bff.trade_journey_projection_store import TradeJourneyProjectionStore


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return []

    description = [("journey_id",)]


def _store(calls):
    return TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: _Conn(calls))


def test_shared_dimension_filter_reads_journey_summary_and_legacy_links():
    clauses, params = _store([])._journey_where(
        tenant_id="t", environment="paper", filters={"persona_id": "p-1", "order_id": "o-1"}
    )
    shared, per_journey = clauses[2], clauses[3]
    assert "current_identity_summary -> 'identifiers', current_identity_summary) -> %s ? %s" in shared and "identity_links" in shared
    assert "current_identity_summary" not in per_journey
    assert params == ["t", "paper", "persona_id", "p-1", "persona_id", "p-1", "order_id", "o-1"]


def test_resolve_shared_dimension_unions_journey_rows_and_legacy_links():
    calls = []
    _store(calls).resolve(tenant_id="t", environment="paper", identifier_type="artifact_id", identifier_value="a-1")
    sql, params = calls[0]
    assert ".journeys" in sql and "identity_links" in sql
    assert params == ("t", "paper", "artifact_id", "a-1") * 2


def test_every_shared_dimension_filters_real_store_with_nested_and_flat_summaries():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg  # type: ignore[import]

    schema = f"test_bff_{uuid4().hex[:8]}"
    ProjectionStore(dsn, schema=schema).bootstrap_schema()
    now = datetime.now(timezone.utc)
    names = sorted(SHARED_IDENTIFIER_TYPES)
    ident = {name: f"{name}-v" for name in names}
    summaries = {"j-nested": identity_summary(ident, {}), "j-flat": {name: [f"{name}-v"] for name in names}}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for journey, summary in summaries.items():
            cur.execute(
                f"INSERT INTO {schema}.journeys (tenant_id, environment, journey_id, status, stage_coverage, is_terminal, first_occurred_at, last_occurred_at, first_ingested_seq, last_ingested_seq, current_identity_summary, projection_revision) VALUES ('t','paper',%s,'open','{{}}',false,%s,%s,1,1,%s::jsonb,1)",
                (journey, now, now, __import__("json").dumps(summary)),
            )
    store = TradeJourneyProjectionStore(dsn, schema=schema, token_secret="s" * 16)
    try:
        for name in names:
            hit = store.page_journeys(tenant_id="t", environment="paper", filters={name: f"{name}-v"})
            assert sorted(i.journey_id for i in hit.items) == ["j-flat", "j-nested"], name
            assert hit.total == 2
            assert store.page_journeys(tenant_id="t", environment="paper", filters={name: "absent"}).total == 0
            assert store.resolve(tenant_id="t", environment="paper", identifier_type=name, identifier_value=f"{name}-v") == ["j-flat", "j-nested"]
            assert store.resolve(tenant_id="t", environment="paper", identifier_type=name, identifier_value="absent") == []
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_projector_restart_and_backfill_expose_every_shared_dimension(tmp_path):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import json

    import psycopg  # type: ignore[import]

    from services.trade_journey.lifecycle_projector import RelationalLifecycleProjector
    from services.trade_journey.projection_migration import BackfillCoordinator
    from services.trade_journey.test_lifecycle_projector import IDENTITY, lifecycle_rows
    from services.trade_journey.test_projection_migration import _paged_fetch

    suffix = uuid4().hex[:8]
    live, migrated = f"test_live_{suffix}", f"test_mig_{suffix}"
    first = lifecycle_rows()[0]
    raw = json.dumps(first).replace(IDENTITY["trace_id"], "20000000-0000-0000-0000-000000000002")
    for token in ("tj-paper-001", "signal-paper-001", "decision-paper-001", "client-order-paper-001", "order-paper-001", "reconciliation-paper-001", "run-paper-001"):
        raw = raw.replace(token, token.replace("001", "002"))
    second = {**json.loads(raw.replace("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000099")), "ingested_seq": 2}
    try:
        store = ProjectionStore(dsn, schema=live, bootstrap=True)
        RelationalLifecycleProjector(store, deployment_sha="t", controller_id="c").project_records([first], mode="live", source_high_watermark=1)
        # Old code bound every shared dimension to the first journey and summarised none of them; restart must still advance, idempotently.
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for name in sorted(SHARED_IDENTIFIER_TYPES):
                cur.execute(f"INSERT INTO {live}.identity_links (tenant_id,environment,identifier_type,identifier_value,journey_id,first_ingested_seq,last_ingested_seq,first_occurred_at,last_occurred_at) VALUES ('tenant-a','paper',%s,%s,'tj-paper-001',1,1,now(),now())", (name, IDENTITY[name]))
            cur.execute(f"UPDATE {live}.journeys SET current_identity_summary = %s::jsonb WHERE journey_id='tj-paper-001'", (json.dumps({"identifiers": {"signal_id": ["signal-paper-001"]}}),))
        for _ in range(2):
            RelationalLifecycleProjector(ProjectionStore(dsn, schema=live), deployment_sha="t", controller_id="c").project_records([second], mode="live", source_high_watermark=2)
        BackfillCoordinator(ProjectionStore(dsn, schema=migrated, bootstrap=True), controller_id="m", tenant_scope="tenant-a", environment_scope="paper", fetch_batch=_paged_fetch(lifecycle_rows()), snapshot_path=tmp_path / "snap.json", batch_size=4).run()
        for schema, expected in ((live, 2), (migrated, 1)):
            reader = TradeJourneyProjectionStore(dsn, schema=schema, token_secret="s" * 16)
            for name in sorted(SHARED_IDENTIFIER_TYPES):
                assert reader.page_journeys(tenant_id="tenant-a", environment="paper", filters={name: IDENTITY[name]}).total == expected, (schema, name)
                assert len(reader.resolve(tenant_id="tenant-a", environment="paper", identifier_type=name, identifier_value=IDENTITY[name])) == expected
                assert reader.page_journeys(tenant_id="tenant-a", environment="paper", filters={name: "absent"}).total == 0
                assert reader.page_journeys(tenant_id="tenant-a", environment="paper", filters={"q": IDENTITY[name]}).total == expected, (schema, name, "q")
                assert reader.page_journeys(tenant_id="tenant-a", environment="paper", filters={"q": IDENTITY[name], "q_journey_only": True}).total == 0
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for schema in (live, migrated):
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
