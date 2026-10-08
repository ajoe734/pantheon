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


def test_concurrent_migrations_do_not_contain_dollar_quotes_or_do_blocks():
    """Verify that all migrations containing CONCURRENTLY do not contain $$ or DO blocks.

    ProjectionStore.bootstrap_schema splits concurrent migrations naively on ';' to execute
    statements individually outside transaction blocks. Semicolons inside $$ blocks or DO blocks
    would be silently broken into invalid statement fragments.
    """
    import re

    assert MIGRATIONS_DIR.is_dir()
    for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        content = migration_file.read_text(encoding="utf-8")
        if "CONCURRENTLY" in content.upper():
            assert "$$" not in content, (
                f"Migration {migration_file.name} contains CONCURRENTLY and '$$'; "
                "cannot safely split on ';' without a proper SQL parser."
            )
            assert not re.search(r"\bDO\b", content, re.IGNORECASE), (
                f"Migration {migration_file.name} contains CONCURRENTLY and a DO block; "
                "cannot safely split on ';' without a proper SQL parser."
            )


def test_concurrent_migration_safety_guard_rejects_synthetic_dollar_quotes_and_do():
    """Demonstrate that synthetic migrations with $$ or DO blocks violate the concurrent split guard."""
    import re

    def assert_concurrent_migration_safe(filename: str, content: str) -> None:
        if "CONCURRENTLY" in content.upper():
            assert "$$" not in content, (
                f"Migration {filename} contains CONCURRENTLY and '$$'; "
                "cannot safely split on ';' without a proper SQL parser."
            )
            assert not re.search(r"\bDO\b", content, re.IGNORECASE), (
                f"Migration {filename} contains CONCURRENTLY and a DO block; "
                "cannot safely split on ';' without a proper SQL parser."
            )

    # Valid concurrent statements pass
    assert_concurrent_migration_safe("test_ok.sql", "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx ON tbl (col);")

    # Migration with $$ fails
    with pytest.raises(AssertionError, match="contains CONCURRENTLY and '\\$\\$'"):
        assert_concurrent_migration_safe(
            "bad_dollar.sql",
            "CREATE INDEX CONCURRENTLY idx ON tbl (col);\nCREATE FUNCTION foo() RETURNS void AS $$ BEGIN NULL; END; $$ LANGUAGE plpgsql;",
        )

    # Migration with DO block fails
    with pytest.raises(AssertionError, match="contains CONCURRENTLY and a DO block"):
        assert_concurrent_migration_safe(
            "bad_do.sql",
            "CREATE INDEX CONCURRENTLY idx ON tbl (col);\nDO 'BEGIN NULL; END';",
        )


def test_bootstrap_schema_concurrent_failure_preserves_runtime_dml_and_usage_seven_tables():
    """Acceptance 1, 2, 4: Complete seven-table schema retains runtime SELECT, INSERT, UPDATE, DELETE

    and schema USAGE when concurrent index phase fails. Runtime DDL remains denied.
    """
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo, conninfo_to_dict
    from services.trade_journey.projection_store import (
        DEFAULT_PROJECTION_SCHEMA,
        PROJECTION_TABLES,
        INITIAL_MIGRATION_PATH,
    )
    import services.trade_journey.projection_store as ps_mod
    import tempfile
    from pathlib import Path

    schema = DEFAULT_PROJECTION_SCHEMA
    suffix = uuid4().hex[:8]
    runtime_role = f"unit_rt_{suffix}"
    migration_role = f"unit_mig_{suffix}"

    parsed = conninfo_to_dict(dsn)
    db = parsed.get("dbname") or "pantheon"

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        for r in (runtime_role, migration_role):
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'pw'").format(sql.Identifier(r)))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(runtime_role), sql.Identifier(migration_role)))
        admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(migration_role)))

        sql_001 = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8").replace(DEFAULT_PROJECTION_SCHEMA, schema)
        admin.execute(sql_001)

        admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))
        for tbl in PROJECTION_TABLES:
            admin.execute(sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(tbl), sql.Identifier(runtime_role)))

    mig_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=migration_role, password="pw")
    rt_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=runtime_role, password="pw")

    try:
        with tempfile.TemporaryDirectory(prefix="concurrent-fail-") as tmp:
            Path(tmp, "001_initial.sql").write_text(sql_001, encoding="utf-8")
            Path(tmp, "002_concurrent_fail.sql").write_text("-- CONCURRENTLY\nSELECT 1 / 0;\n", encoding="utf-8")
            orig_dir = ps_mod.MIGRATIONS_DIR
            try:
                ps_mod.MIGRATIONS_DIR = Path(tmp)
                store = ProjectionStore(mig_dsn, schema=schema)
                with pytest.raises(psycopg.errors.DivisionByZero):
                    store.bootstrap_schema(runtime_role=runtime_role, reconcile_runtime=True)
            finally:
                ps_mod.MIGRATIONS_DIR = orig_dir

        with psycopg.connect(rt_dsn, autocommit=True) as r_conn:
            has_usage = r_conn.execute("SELECT has_schema_privilege(%s, %s, %s)", (runtime_role, schema, "USAGE")).fetchone()[0]
            assert has_usage is True, "Runtime role must retain USAGE on schema after concurrent failure"

            for tbl in PROJECTION_TABLES:
                rows = r_conn.execute(sql.SQL("SELECT 1 FROM {}.{} LIMIT 0").format(sql.Identifier(schema), sql.Identifier(tbl))).fetchall()
                assert rows == [], f"Runtime role must be able to SELECT from {tbl} after concurrent failure"

            r_conn.execute(sql.SQL("INSERT INTO {}.controller (controller_id, tenant_scope, environment_scope) VALUES ('c1', 't1', 'paper')").format(sql.Identifier(schema)))
            r_conn.execute(sql.SQL("UPDATE {}.controller SET checkpoint_seq = 1 WHERE controller_id = 'c1'").format(sql.Identifier(schema)))
            r_conn.execute(sql.SQL("DELETE FROM {}.controller WHERE controller_id = 'c1'").format(sql.Identifier(schema)))

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r_conn.execute(sql.SQL("CREATE TABLE {}.forbidden (val int)").format(sql.Identifier(schema)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r_conn.execute(sql.SQL("ALTER TABLE {}.controller ADD COLUMN bad int").format(sql.Identifier(schema)))
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            for r in (migration_role, runtime_role):
                try:
                    admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(db), sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(r)))
                except Exception:
                    pass


def test_bootstrap_schema_partial_grant_failure_rolls_back_authority_and_acl_seven_tables():
    """Acceptance 1, 2, 4: Partial grant failure in transactional phase rolls back authority

    reconciliation and ACL changes, preserving prior ownership and runtime operations.
    """
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo, conninfo_to_dict
    from services.trade_journey.projection_store import (
        DEFAULT_PROJECTION_SCHEMA,
        PROJECTION_TABLES,
        INITIAL_MIGRATION_PATH,
    )

    schema = DEFAULT_PROJECTION_SCHEMA
    suffix = uuid4().hex[:8]
    runtime_role = f"unit_rt_{suffix}"
    migration_role = f"unit_mig_{suffix}"

    parsed = conninfo_to_dict(dsn)
    db = parsed.get("dbname") or "pantheon"

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        for r in (runtime_role, migration_role):
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'pw'").format(sql.Identifier(r)))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(runtime_role), sql.Identifier(migration_role)))
        admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(migration_role)))

        sql_001 = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8").replace(DEFAULT_PROJECTION_SCHEMA, schema)
        admin.execute(sql_001)

        admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))
        for tbl in PROJECTION_TABLES:
            admin.execute(sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(tbl), sql.Identifier(runtime_role)))

    mig_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=migration_role, password="pw")
    rt_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=runtime_role, password="pw")

    def get_owners():
        with psycopg.connect(dsn, autocommit=True) as conn:
            return dict(conn.execute(
                "SELECT 'schema', r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname=%s "
                "UNION ALL SELECT c.relname, r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname=%s AND c.relkind='r'",
                (schema, schema),
            ).fetchall())

    owners_before = get_owners()
    assert owners_before["schema"] == runtime_role
    for tbl in PROJECTION_TABLES:
        assert owners_before[tbl] == runtime_role

    try:
        store = ProjectionStore(mig_dsn, schema=schema)

        class FailingCursor:
            def __init__(self, real_cur):
                self._real = real_cur
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self._real.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self._real, name)
            def execute(self, query, params=None):
                q_str = str(query)
                if "GRANT SELECT, INSERT" in q_str and "event_receipts" in q_str:
                    raise psycopg.errors.InternalError("Simulated partial grant failure on table event_receipts")
                return self._real.execute(query, params)

        class FailingConn:
            def __init__(self, real_conn):
                self._real = real_conn
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self._real.__exit__(*args)
            def cursor(self):
                return FailingCursor(self._real.cursor())
            def __getattr__(self, name):
                return getattr(self._real, name)

        orig_connect = store._connect_db
        def patched_connect(*args, **kwargs):
            c = orig_connect(*args, **kwargs)
            if not kwargs.get("autocommit", False):
                return FailingConn(c)
            return c

        store._connect_db = patched_connect

        with pytest.raises(psycopg.errors.InternalError):
            store.bootstrap_schema(runtime_role=runtime_role, reconcile_runtime=True)

        owners_after = get_owners()
        assert owners_after == owners_before, "Ownership must roll back on partial grant failure"

        with psycopg.connect(rt_dsn) as r_conn:
            for tbl in PROJECTION_TABLES:
                r_conn.execute(sql.SQL("SELECT 1 FROM {}.{} LIMIT 0").format(sql.Identifier(schema), sql.Identifier(tbl)))
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            for r in (migration_role, runtime_role):
                try:
                    admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(db), sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(r)))
                except Exception:
                    pass


def test_bootstrap_schema_concurrent_failure_minimal_controller_preserves_runtime():
    """Acceptance 1, 2, 4: Minimal controller fixture remains fail-safe under concurrent failure."""
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo, conninfo_to_dict
    from services.trade_journey.projection_store import DEFAULT_PROJECTION_SCHEMA
    import services.trade_journey.projection_store as ps_mod
    import tempfile
    from pathlib import Path

    schema = DEFAULT_PROJECTION_SCHEMA
    suffix = uuid4().hex[:8]
    runtime_role = f"unit_rt_{suffix}"
    migration_role = f"unit_mig_{suffix}"

    parsed = conninfo_to_dict(dsn)
    db = parsed.get("dbname") or "pantheon"

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        for r in (runtime_role, migration_role):
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'pw'").format(sql.Identifier(r)))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(runtime_role), sql.Identifier(migration_role)))
        admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(migration_role)))
        admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))
        admin.execute(sql.SQL("CREATE TABLE {}.controller (value integer)").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("ALTER TABLE {}.controller OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))

    mig_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=migration_role, password="pw")
    rt_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=runtime_role, password="pw")

    try:
        with tempfile.TemporaryDirectory(prefix="concurrent-min-fail-") as tmp:
            Path(tmp, "999_unit_forced_failure.sql").write_text("-- CONCURRENTLY phase failure injection\nSELECT 1 / 0;\n", encoding="utf-8")
            orig_dir = ps_mod.MIGRATIONS_DIR
            try:
                ps_mod.MIGRATIONS_DIR = Path(tmp)
                store = ProjectionStore(mig_dsn, schema=schema)
                with pytest.raises(psycopg.errors.DivisionByZero):
                    store.bootstrap_schema(runtime_role=runtime_role, reconcile_runtime=True)
            finally:
                ps_mod.MIGRATIONS_DIR = orig_dir

        with psycopg.connect(rt_dsn) as r_conn:
            rows = r_conn.execute(sql.SQL("SELECT value FROM {}.controller").format(sql.Identifier(schema))).fetchall()
            assert rows == []
            has_usage = r_conn.execute("SELECT has_schema_privilege(%s, %s, %s)", (runtime_role, schema, "USAGE")).fetchone()[0]
            assert has_usage is True
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            for r in (migration_role, runtime_role):
                try:
                    admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(db), sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(r)))
                except Exception:
                    pass


def test_bootstrap_schema_complete_schema_idempotence_and_denied_ddl():
    """Acceptance 1, 3, 4: Re-running bootstrap_schema on complete seven-table schema is idempotent;

    runtime role retains DML and is denied DDL authority.
    """
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo, conninfo_to_dict
    from services.trade_journey.projection_store import (
        DEFAULT_PROJECTION_SCHEMA,
        PROJECTION_TABLES,
        INITIAL_MIGRATION_PATH,
    )

    schema = DEFAULT_PROJECTION_SCHEMA
    suffix = uuid4().hex[:8]
    runtime_role = f"unit_rt_{suffix}"
    migration_role = f"unit_mig_{suffix}"

    parsed = conninfo_to_dict(dsn)
    db = parsed.get("dbname") or "pantheon"

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        for r in (runtime_role, migration_role):
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'pw'").format(sql.Identifier(r)))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(runtime_role), sql.Identifier(migration_role)))
        admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(migration_role)))

        sql_001 = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8").replace(DEFAULT_PROJECTION_SCHEMA, schema)
        admin.execute(sql_001)

        admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(runtime_role)))
        for tbl in PROJECTION_TABLES:
            admin.execute(sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(tbl), sql.Identifier(runtime_role)))

    mig_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=migration_role, password="pw")
    rt_dsn = make_conninfo(host=parsed.get("host", "127.0.0.1"), port=parsed.get("port", "5432"), dbname=db, user=runtime_role, password="pw")

    try:
        store = ProjectionStore(mig_dsn, schema=schema)
        # First execution: applies migrations and reconciles runtime
        store.bootstrap_schema(runtime_role=runtime_role, reconcile_runtime=True)
        # Second execution: idempotent no-op
        store.bootstrap_schema(runtime_role=runtime_role, reconcile_runtime=True)

        with psycopg.connect(rt_dsn) as r_conn:
            for tbl in PROJECTION_TABLES:
                r_conn.execute(sql.SQL("SELECT 1 FROM {}.{} LIMIT 0").format(sql.Identifier(schema), sql.Identifier(tbl)))

            r_conn.execute(sql.SQL("INSERT INTO {}.controller (controller_id, tenant_scope, environment_scope) VALUES ('c1', 't1', 'paper')").format(sql.Identifier(schema)))
            r_conn.execute(sql.SQL("UPDATE {}.controller SET checkpoint_seq = 2 WHERE controller_id = 'c1'").format(sql.Identifier(schema)))
            r_conn.execute(sql.SQL("DELETE FROM {}.controller WHERE controller_id = 'c1'").format(sql.Identifier(schema)))

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r_conn.execute(sql.SQL("CREATE TABLE {}.forbidden (val int)").format(sql.Identifier(schema)))
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            for r in (migration_role, runtime_role):
                try:
                    admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(db), sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(r)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(r)))
                except Exception:
                    pass


