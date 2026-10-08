import os
from uuid import uuid4

import pytest

from services.trade_journey.projection_store import MIGRATIONS_DIR, ProjectionStore


def test_migrations_dir_contains_002():
    mig_002 = MIGRATIONS_DIR / "002_add_trade_journey_search_indexes.sql"
    assert mig_002.is_file(), "002_add_trade_journey_search_indexes.sql must exist"


def test_bootstrap_schema_applies_002_and_is_idempotent():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"test_mig002_{uuid4().hex[:8]}"
    store = ProjectionStore(dsn, schema=schema, bootstrap=True)

    try:
        # Re-run bootstrap_schema to verify idempotence through real bootstrap path
        store.bootstrap_schema()

        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
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

