import os
from pathlib import Path
from uuid import uuid4

import pytest

from services.trade_journey.projection_store import ProjectionStore


def test_migration_002_creates_search_indexes():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"test_mig002_{uuid4().hex[:8]}"
    store = ProjectionStore(dsn, schema=schema, bootstrap=True)

    migration_file = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "002_add_trade_journey_search_indexes.sql"
    )
    assert migration_file.is_file(), "002 migration file must exist"
    migration_sql = migration_file.read_text(encoding="utf-8").replace(
        "trade_journey_projection", schema
    )

    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            # Apply migration twice to verify idempotence
            cur.execute(migration_sql)
            cur.execute(migration_sql)

            cur.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = %s
                """,
                (schema,),
            )
            indexes = {row[0] for row in cur.fetchall()}
            assert "idx_journeys_journey_id_trgm" in indexes
            assert "idx_identity_links_value_trgm" in indexes
            assert "idx_identity_links_tenant_env_value_journey" in indexes
            assert "idx_journeys_summary_gin" in indexes
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
