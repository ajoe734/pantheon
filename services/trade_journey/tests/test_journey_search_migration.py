import os
from uuid import uuid4

import pytest

from services.trade_journey.projection_store import MIGRATIONS_DIR, ProjectionStore


def test_migrations_dir_contains_002():
    mig_002 = MIGRATIONS_DIR / "002_add_trade_journey_search_indexes.sql"
    assert mig_002.is_file(), "002_add_trade_journey_search_indexes.sql must exist"


def test_migration_002_uses_concurrent_indexes():
    mig_002 = MIGRATIONS_DIR / "002_add_trade_journey_search_indexes.sql"
    content = mig_002.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm;" in content
    expected_indexes = (
        "idx_journeys_journey_id_trgm",
        "idx_identity_links_value_trgm",
        "idx_identity_links_tenant_env_value_journey",
        "idx_journeys_summary_gin",
    )
    for idx_name in expected_indexes:
        assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx_name}" in content


def test_bootstrap_schema_raises_timeout_only_for_migrations_mock():
    captured_calls = []

    class DummyCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class DummyConn:
        def __init__(self, autocommit=False):
            self.autocommit = autocommit

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return DummyCursor()

    def mock_connect(dsn, **kwargs):
        captured_calls.append(kwargs)
        return DummyConn(autocommit=kwargs.get("autocommit", False))

    store = ProjectionStore(
        "postgresql://unit:unit@127.0.0.1:5432/pantheon",
        connect=mock_connect,
        statement_timeout_seconds=5.0,
        lock_timeout_seconds=3.0,
        migration_statement_timeout_seconds=120.0,
        migration_lock_timeout_seconds=25.0,
    )

    # 1. Runtime query connection uses runtime statement timeout and lock timeout
    with store._connect_db() as conn:
        assert not conn.autocommit
    assert len(captured_calls) == 1
    assert captured_calls[0]["options"] == "-c statement_timeout=5000 -c lock_timeout=3000"
    assert "autocommit" not in captured_calls[0]

    # 2. bootstrap_schema raises timeout to migration statement/lock timeouts (transactional + concurrent phases)
    store.bootstrap_schema()
    assert len(captured_calls) >= 3
    tx_call = captured_calls[1]
    assert tx_call["options"] == "-c statement_timeout=120000 -c lock_timeout=25000"
    assert "autocommit" not in tx_call or not tx_call["autocommit"]
    concurrent_call = captured_calls[2]
    assert concurrent_call["options"] == "-c statement_timeout=120000 -c lock_timeout=25000"
    assert concurrent_call.get("autocommit") is True

    # 3. Subsequent runtime connection remains at runtime statement timeout
    with store._connect_db() as conn:
        assert not conn.autocommit
    assert captured_calls[-1]["options"] == "-c statement_timeout=5000 -c lock_timeout=3000"
    assert "autocommit" not in captured_calls[-1]


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


def test_bootstrap_schema_detects_and_rebuilds_invalid_index():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"test_mig_inv_{uuid4().hex[:8]}"
    store = ProjectionStore(dsn, schema=schema, bootstrap=True)

    try:
        # Intentionally invalidate one of the concurrent indexes
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE pg_index
                SET indisvalid = false
                WHERE indexrelid = '{schema}.idx_journeys_journey_id_trgm'::regclass
                """
            )
            cur.execute(
                f"""
                SELECT indisvalid FROM pg_index
                WHERE indexrelid = '{schema}.idx_journeys_journey_id_trgm'::regclass
                """
            )
            assert cur.fetchone()[0] is False, "index should be marked invalid"

        # Re-running bootstrap_schema must detect the invalid index and rebuild it
        store.bootstrap_schema()

        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT indisvalid FROM pg_index
                WHERE indexrelid = '{schema}.idx_journeys_journey_id_trgm'::regclass
                """
            )
            assert cur.fetchone()[0] is True, "invalid index must be rebuilt to valid"
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_bootstrap_schema_rollback_atomicity_on_failure():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg
    from psycopg import sql

    from services.trade_journey.projection_store import DEFAULT_PROJECTION_SCHEMA

    schema = DEFAULT_PROJECTION_SCHEMA
    runtime_role = f"unit_rt_{uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        cur_user = admin.execute("SELECT current_user").fetchone()[0]
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(runtime_role)))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(runtime_role), sql.Identifier(cur_user)))
        admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))
        admin.execute(sql.SQL("CREATE TABLE {}.controller (value integer)").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("ALTER TABLE {}.controller OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))

    try:
        def get_owners():
            with psycopg.connect(dsn, autocommit=True) as conn:
                return dict(conn.execute(
                    "SELECT 'schema', r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname=%s "
                    "UNION ALL SELECT 'controller', r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname=%s AND c.relname='controller'",
                    (schema, schema),
                ).fetchall())

        owners_before = get_owners()
        assert owners_before["schema"] == runtime_role
        assert owners_before["controller"] == runtime_role

        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="mig-fail-test-") as tmp:
            Path(tmp, "999_forced_failure.sql").write_text("SELECT 1 / 0;\n", encoding="utf-8")
            store = ProjectionStore(dsn, schema=schema)
            import services.trade_journey.projection_store as ps_mod
            orig_dir = ps_mod.MIGRATIONS_DIR
            try:
                ps_mod.MIGRATIONS_DIR = Path(tmp)
                with pytest.raises(psycopg.errors.DivisionByZero):
                    store.bootstrap_schema(runtime_role=runtime_role, reconcile_runtime=True)
            finally:
                ps_mod.MIGRATIONS_DIR = orig_dir

        owners_after = get_owners()
        assert owners_after == owners_before, "Ownership must be rolled back on migration failure"
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime_role)))


