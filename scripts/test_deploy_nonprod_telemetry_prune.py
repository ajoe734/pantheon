from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _prune_block(deploy: str) -> str:
    start = deploy.index("prune_dev_management_ai_telemetry_for_disk() {")
    end = deploy.index("dump_dev_root_failure_diagnostics() {", start)
    return deploy[start:end]


def _extract_sql_prune_block(deploy: str) -> str:
    match = re.search(
        r"-v mgmt_schema=\"\$\{MGMT_AI_SCHEMA\}\" <<'SQL'\n(.*?)\nSQL",
        deploy,
        re.DOTALL,
    )
    assert match is not None, "Could not find SQL block in prune_dev_management_ai_telemetry_for_disk"
    return match.group(1)


def test_telemetry_prune_scopes_truncate_to_management_ai_schema_only() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert "AND n.nspname = target_schema" in block
    assert "n.nspname IN (target_schema, 'public')" not in block
    assert "TRUNCATE TABLE %I.%I" in block


def test_telemetry_prune_refuses_when_schema_resolves_to_public() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert "IF target_schema_clean = 'public' THEN" in block
    assert "RAISE EXCEPTION 'refusing to prune telemetry_events: MANAGEMENT_AI_STORE_SCHEMA resolves to canonical public schema'" in block
    assert '[[ "${mgmt_schema,,}" == "public" ]]' in block
    assert (
        block.index("IF target_schema_clean = 'public' THEN")
        < block.index("FOR item IN")
    )


def test_telemetry_prune_rejects_invalid_or_empty_schema_identifiers() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert "target_schema !~ '^[a-zA-Z_][a-zA-Z0-9_]*$'" in block
    assert "! \"$mgmt_schema\" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$" in block
    assert "RAISE EXCEPTION" in block


def test_telemetry_prune_enforces_canonical_sentinel_preservation() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert "canonical_checksum_before" in block
    assert "canonical_checksum_after" in block
    assert "canonical_matched_count != canonical_count_before" in block
    assert "canonical_matched_checksum != canonical_checksum_before" in block
    assert "canonical_count_after < canonical_count_before" in block
    assert "canonical telemetry drift detected" in block
    assert "TELEMETRY_PRUNE_SENTINEL:" in block
    assert "'result', 'preserved'" in block


def test_telemetry_prune_still_gated_by_dev_root_and_postgres_backend() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert '[[ "${PANTHEON_DEPLOY_ENV}" != "dev" || "${PANTHEON_DEPLOY_COMPONENT}" != "root" ]]' in block
    assert '"${MANAGEMENT_AI_STORE_BACKEND:-}" != "postgres"' in block
    assert "PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE" in block


def test_configure_management_ai_dev_env_validates_schema() -> None:
    deploy = _read("scripts/deploy_nonprod_vm.sh")
    start = deploy.index("configure_management_ai_dev_env() {")
    end = deploy.index("configure_management_ai_dev_kernel_env() {", start)
    block = deploy[start:end]

    assert '! "$MANAGEMENT_AI_STORE_SCHEMA" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$' in block
    assert '[[ "${MANAGEMENT_AI_STORE_SCHEMA,,}" == "public" ]]' in block
    assert "MANAGEMENT_AI_STORE_SCHEMA cannot be 'public'" in block


# -----------------------------------------------------------------------------
# Behavioral Database Tests (against local / CI PostgreSQL container)
# -----------------------------------------------------------------------------

POSTGRES_HOST_DSN = os.environ.get(
    "PANTHEON_TEST_POSTGRES_DSN",
    "postgresql://postgres:postgres@localhost:15432/pantheon",
)
TEST_DB_NAME = "test_telemetry_prune_isolated"


async def _check_postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(POSTGRES_HOST_DSN, timeout=2.0)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def postgres_available() -> bool:
    return asyncio.run(_check_postgres_reachable())


@pytest.fixture
def isolated_db(postgres_available: bool):
    if not postgres_available:
        pytest.skip("PostgreSQL is not reachable on " + POSTGRES_HOST_DSN)

    async def _setup():
        # Connect to default DB to create/drop test database
        admin_conn = await asyncpg.connect(POSTGRES_HOST_DSN)
        try:
            await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE);")
            await admin_conn.execute(f"CREATE DATABASE {TEST_DB_NAME};")
        finally:
            await admin_conn.close()

        # Connect to isolated test DB and setup schema
        test_dsn = POSTGRES_HOST_DSN.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"
        conn = await asyncpg.connect(test_dsn)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    ingested_seq BIGSERIAL UNIQUE,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                );
                """
            )
        finally:
            await conn.close()

        return test_dsn

    test_dsn = asyncio.run(_setup())
    yield test_dsn

    async def _teardown():
        admin_conn = await asyncpg.connect(POSTGRES_HOST_DSN)
        try:
            await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE);")
        finally:
            await admin_conn.close()

    asyncio.run(_teardown())


async def _run_sql_block(conn: asyncpg.Connection, schema_name: str) -> list[str]:
    notices: list[str] = []
    conn.add_log_listener(lambda _, msg: notices.append(msg.message))

    await conn.execute(f"SELECT set_config('pantheon.mgmt_ai_schema', '{schema_name}', false);")

    deploy = _read("scripts/deploy_nonprod_vm.sh")
    sql_text = _extract_sql_prune_block(deploy)

    # Extract the DO $prune$ ... $prune$; block
    do_block_match = re.search(r"DO \$prune\$.*?\$prune\$;", sql_text, re.DOTALL)
    assert do_block_match is not None, "DO $prune$ block not found in extracted SQL"
    do_block = do_block_match.group(0)

    await conn.execute(do_block)
    return notices


def test_db_behavior_prunes_derived_table_and_preserves_canonical_table(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            # Create derived schema and tables
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_derived_mgmt;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_derived_mgmt.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    ingested_seq BIGSERIAL UNIQUE,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                );
                """
            )

            # Seed canonical and derived data
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}'),
                ('evt-c-002', 'order.filled', '2026-08-22T06:00:00Z', '{"k": 2}'),
                ('evt-c-003', 'system.stop',  '2026-08-22T07:00:00Z', '{"k": 3}');

                INSERT INTO test_derived_mgmt.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}'),
                ('evt-d-002', 'mgmt.turn', '2026-08-22T09:00:00Z', '{"derived": true}');
                """
            )

            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 3
            assert await conn.fetchval("SELECT COUNT(*) FROM test_derived_mgmt.telemetry_events") == 2

            # Run SQL prune on derived schema
            notices = await _run_sql_block(conn, "test_derived_mgmt")

            # Verify derived table was truncated
            assert await conn.fetchval("SELECT COUNT(*) FROM test_derived_mgmt.telemetry_events") == 0

            # Verify canonical table was strictly preserved
            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 3
            min_ts = await conn.fetchval("SELECT MIN(created_at) FROM public.telemetry_events")
            assert "2026-08-22 05:00:00" in str(min_ts)

            # Verify sentinel notice was emitted
            sentinel_notices = [n for n in notices if "TELEMETRY_PRUNE_SENTINEL:" in n]
            assert len(sentinel_notices) == 1
            assert '"result": "preserved"' in sentinel_notices[0]
            assert '"derived_schema": "test_derived_mgmt"' in sentinel_notices[0]
            assert '"canonical_row_count_before": 3' in sentinel_notices[0]
            assert '"canonical_row_count_after": 3' in sentinel_notices[0]
            assert '"test_derived_mgmt.telemetry_events"' in sentinel_notices[0]
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_missing_derived_table_is_noop(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_derived_empty;")
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}');
                """
            )

            notices = await _run_sql_block(conn, "test_derived_empty")

            # Canonical table unchanged
            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 1

            sentinel_notices = [n for n in notices if "TELEMETRY_PRUNE_SENTINEL:" in n]
            assert len(sentinel_notices) == 1
            assert '"derived_tables_pruned": []' in sentinel_notices[0]
            assert '"result": "preserved"' in sentinel_notices[0]
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_refuses_public_schema_with_exception_and_preserves_data(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}'),
                ('evt-c-002', 'system.stop',  '2026-08-22T06:00:00Z', '{"k": 2}');
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="MANAGEMENT_AI_STORE_SCHEMA resolves to canonical public schema"):
                await _run_sql_block(conn, "public")

            with pytest.raises(asyncpg.PostgresError, match="MANAGEMENT_AI_STORE_SCHEMA resolves to canonical public schema"):
                await _run_sql_block(conn, "PUBLIC")

            # Verify public table was NOT truncated
            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 2
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_refuses_invalid_identifiers(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            with pytest.raises(asyncpg.PostgresError, match="is not a valid SQL identifier"):
                await _run_sql_block(conn, "bad;schema")

            with pytest.raises(asyncpg.PostgresError, match="is not a valid SQL identifier"):
                await _run_sql_block(conn, "123invalid")

            with pytest.raises(asyncpg.PostgresError, match="is empty"):
                await _run_sql_block(conn, "")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_concurrent_append_during_prune_preserves_both_and_succeeds(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_derived_mgmt;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_derived_mgmt.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    ingested_seq BIGSERIAL UNIQUE,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                );
                """
            )

            # Seed canonical and derived data
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}'),
                ('evt-c-002', 'order.filled', '2026-08-22T06:00:00Z', '{"k": 2}'),
                ('evt-c-003', 'system.stop',  '2026-08-22T07:00:00Z', '{"k": 3}');

                INSERT INTO test_derived_mgmt.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}'),
                ('evt-d-002', 'mgmt.turn', '2026-08-22T09:00:00Z', '{"derived": true}');
                """
            )

            # Attach a trigger to test_derived_mgmt.telemetry_events to simulate a concurrent append
            # to public.telemetry_events executing during the prune truncate step
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_concurrent_append() RETURNS trigger AS $$
                BEGIN
                  INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload)
                  VALUES ('evt-c-concurrent-004', 'order.placed', '2026-08-22T08:30:00Z', '{"concurrent": true}');
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_concurrent_append ON test_derived_mgmt.telemetry_events;
                CREATE TRIGGER trg_test_concurrent_append
                BEFORE TRUNCATE ON test_derived_mgmt.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_concurrent_append();
                """
            )

            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 3
            assert await conn.fetchval("SELECT COUNT(*) FROM test_derived_mgmt.telemetry_events") == 2

            # Run SQL prune on derived schema
            notices = await _run_sql_block(conn, "test_derived_mgmt")

            # Verify derived table was truncated
            assert await conn.fetchval("SELECT COUNT(*) FROM test_derived_mgmt.telemetry_events") == 0

            # Verify canonical table preserved the 3 pre-existing rows AND accepted the concurrent append
            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 4
            event_ids = await conn.fetch("SELECT event_id FROM public.telemetry_events ORDER BY created_at ASC")
            assert [r["event_id"] for r in event_ids] == [
                "evt-c-001",
                "evt-c-002",
                "evt-c-003",
                "evt-c-concurrent-004",
            ]

            # Verify sentinel notice was emitted with result: preserved
            sentinel_notices = [n for n in notices if "TELEMETRY_PRUNE_SENTINEL:" in n]
            assert len(sentinel_notices) == 1
            assert '"result": "preserved"' in sentinel_notices[0]
            assert '"canonical_row_count_before": 3' in sentinel_notices[0]
            assert '"canonical_row_count_after": 4' in sentinel_notices[0]
            assert '"canonical_matched_count": 3' in sentinel_notices[0]
            assert '"test_derived_mgmt.telemetry_events"' in sentinel_notices[0]
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_concurrent_append_with_early_and_late_timestamps(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_derived_mgmt;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_derived_mgmt.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )

            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-002', 'order.filled', '2026-08-22T06:00:00Z', '{"k": 2}');

                INSERT INTO test_derived_mgmt.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger inserts one out-of-order earlier event and one later event
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_multi_concurrent_append() RETURNS trigger AS $$
                BEGIN
                  INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload)
                  VALUES
                    ('evt-c-001', 'system.start', '2026-08-22T04:00:00Z', '{"early": true}'),
                    ('evt-c-003', 'system.stop',  '2026-08-22T09:00:00Z', '{"late": true}');
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_multi_concurrent ON test_derived_mgmt.telemetry_events;
                CREATE TRIGGER trg_test_multi_concurrent
                BEFORE TRUNCATE ON test_derived_mgmt.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_multi_concurrent_append();
                """
            )

            notices = await _run_sql_block(conn, "test_derived_mgmt")

            assert await conn.fetchval("SELECT COUNT(*) FROM test_derived_mgmt.telemetry_events") == 0
            assert await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 3

            sentinel_notices = [n for n in notices if "TELEMETRY_PRUNE_SENTINEL:" in n]
            assert len(sentinel_notices) == 1
            assert '"result": "preserved"' in sentinel_notices[0]
            assert '"canonical_row_count_before": 1' in sentinel_notices[0]
            assert '"canonical_row_count_after": 3' in sentinel_notices[0]
            assert '"canonical_matched_count": 1' in sentinel_notices[0]
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_drift_guard_catches_canonical_row_deletion(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_drift_schema.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}'),
                ('evt-c-002', 'order.filled', '2026-08-22T06:00:00Z', '{"k": 2}');

                INSERT INTO test_drift_schema.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger deletes a pre-existing canonical row during truncate
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_canonical_delete() RETURNS trigger AS $$
                BEGIN
                  DELETE FROM public.telemetry_events WHERE event_id = 'evt-c-001';
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_delete ON test_drift_schema.telemetry_events;
                CREATE TRIGGER trg_test_delete
                BEFORE TRUNCATE ON test_drift_schema.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_canonical_delete();
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await _run_sql_block(conn, "test_drift_schema")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_drift_guard_catches_canonical_row_update(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_drift_schema.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}');

                INSERT INTO test_drift_schema.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger mutates created_at of pre-existing canonical row during truncate
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_canonical_update() RETURNS trigger AS $$
                BEGIN
                  UPDATE public.telemetry_events
                  SET created_at = '2099-01-01T00:00:00Z'
                  WHERE event_id = 'evt-c-001';
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_update ON test_drift_schema.telemetry_events;
                CREATE TRIGGER trg_test_update
                BEFORE TRUNCATE ON test_drift_schema.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_canonical_update();
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await _run_sql_block(conn, "test_drift_schema")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_drift_guard_catches_deletion_masked_by_concurrent_append(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_drift_schema.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}'),
                ('evt-c-002', 'order.filled', '2026-08-22T06:00:00Z', '{"k": 2}');

                INSERT INTO test_drift_schema.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger deletes 1 row and inserts 1 row so total count remains 2
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_mask_deletion() RETURNS trigger AS $$
                BEGIN
                  DELETE FROM public.telemetry_events WHERE event_id = 'evt-c-001';
                  INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload)
                  VALUES ('evt-c-fake-003', 'fake.event', '2026-08-22T07:00:00Z', '{"fake": true}');
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_mask ON test_drift_schema.telemetry_events;
                CREATE TRIGGER trg_test_mask
                BEFORE TRUNCATE ON test_drift_schema.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_mask_deletion();
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await _run_sql_block(conn, "test_drift_schema")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_drift_guard_catches_canonical_truncate(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_drift_schema.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}');

                INSERT INTO test_drift_schema.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger truncates canonical table
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_canonical_truncate() RETURNS trigger AS $$
                BEGIN
                  TRUNCATE TABLE public.telemetry_events;
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_truncate ON test_drift_schema.telemetry_events;
                CREATE TRIGGER trg_test_truncate
                BEFORE TRUNCATE ON test_drift_schema.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_canonical_truncate();
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await _run_sql_block(conn, "test_drift_schema")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_drift_guard_catches_canonical_payload_update(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_drift_schema.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"original": true}');

                INSERT INTO test_drift_schema.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger mutates ONLY the payload JSON of pre-existing canonical row during truncate
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_payload_update() RETURNS trigger AS $$
                BEGIN
                  UPDATE public.telemetry_events
                  SET payload = '{"tampered": true}'::jsonb
                  WHERE event_id = 'evt-c-001';
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_payload_update ON test_drift_schema.telemetry_events;
                CREATE TRIGGER trg_test_payload_update
                BEFORE TRUNCATE ON test_drift_schema.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_payload_update();
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await _run_sql_block(conn, "test_drift_schema")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_drift_guard_catches_canonical_event_type_update(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_drift_schema.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"original": true}');

                INSERT INTO test_drift_schema.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Trigger mutates ONLY the event_type of pre-existing canonical row during truncate
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION test_simulate_event_type_update() RETURNS trigger AS $$
                BEGIN
                  UPDATE public.telemetry_events
                  SET event_type = 'tampered.type'
                  WHERE event_id = 'evt-c-001';
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_type_update ON test_drift_schema.telemetry_events;
                CREATE TRIGGER trg_test_type_update
                BEFORE TRUNCATE ON test_drift_schema.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_simulate_event_type_update();
                """
            )

            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await _run_sql_block(conn, "test_drift_schema")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_db_behavior_concurrent_append_from_independent_session(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn1 = await asyncpg.connect(isolated_db)
        conn2 = await asyncpg.connect(isolated_db)
        try:
            await conn1.execute("SET lock_timeout = '3000ms';")
            await conn2.execute("SET lock_timeout = '3000ms';")

            await conn1.execute("CREATE SCHEMA IF NOT EXISTS test_concurrent_session_mgmt;")
            await conn1.execute(
                """
                CREATE TABLE IF NOT EXISTS test_concurrent_session_mgmt.telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                """
            )

            await conn1.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}'),
                ('evt-c-002', 'order.filled', '2026-08-22T06:00:00Z', '{"k": 2}');

                INSERT INTO test_concurrent_session_mgmt.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-d-001', 'mgmt.turn', '2026-08-22T08:00:00Z', '{"derived": true}');
                """
            )

            # Deterministic cross-session barrier using PostgreSQL session-level advisory locks:
            # conn1 holds LOCK_BASELINE_CAPTURED (888001) until BEFORE TRUNCATE trigger fires.
            # conn2 holds LOCK_APPEND_COMPLETED (888002) until its concurrent INSERT commits.
            LOCK_BASELINE_CAPTURED = 888001
            LOCK_APPEND_COMPLETED = 888002

            await conn1.execute(f"SELECT pg_advisory_lock({LOCK_BASELINE_CAPTURED});")
            await conn2.execute(f"SELECT pg_advisory_lock({LOCK_APPEND_COMPLETED});")

            await conn1.execute(
                f"""
                CREATE OR REPLACE FUNCTION test_barrier_truncate_race() RETURNS trigger AS $$
                BEGIN
                  -- Signal conn2 that baseline snapshot has been captured and we entered BEFORE TRUNCATE
                  PERFORM pg_advisory_unlock({LOCK_BASELINE_CAPTURED});
                  -- Wait for conn2 to finish its concurrent append and release LOCK_APPEND_COMPLETED
                  PERFORM pg_advisory_lock({LOCK_APPEND_COMPLETED});
                  PERFORM pg_advisory_unlock({LOCK_APPEND_COMPLETED});
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                DROP TRIGGER IF EXISTS trg_test_barrier_truncate ON test_concurrent_session_mgmt.telemetry_events;
                CREATE TRIGGER trg_test_barrier_truncate
                BEFORE TRUNCATE ON test_concurrent_session_mgmt.telemetry_events
                FOR EACH STATEMENT EXECUTE FUNCTION test_barrier_truncate_race();
                """
            )

            async def _run_prune() -> list[str]:
                return await _run_sql_block(conn1, "test_concurrent_session_mgmt")

            async def _run_concurrent_append() -> None:
                # 1. Bounded wait until conn1 enters BEFORE TRUNCATE trigger (guaranteeing baseline was captured)
                await asyncio.wait_for(
                    conn2.execute(f"SELECT pg_advisory_lock({LOCK_BASELINE_CAPTURED});"),
                    timeout=3.0,
                )
                try:
                    # 2. Insert concurrent canonical row from independent connection
                    await conn2.execute(
                        """
                        INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload)
                        VALUES ('evt-c-session2-003', 'telemetry.append', '2026-08-22T07:00:00Z', '{"session2": true}');
                        """
                    )
                finally:
                    # 3. Unblock conn1 so prune can proceed to post-read verification
                    await conn2.execute(f"SELECT pg_advisory_unlock({LOCK_APPEND_COMPLETED});")
                    await conn2.execute(f"SELECT pg_advisory_unlock({LOCK_BASELINE_CAPTURED});")

            # Bounded execution: guarantee neither session can hang or deadlock the test suite
            notices, _ = await asyncio.wait_for(
                asyncio.gather(_run_prune(), _run_concurrent_append()),
                timeout=5.0,
            )

            assert await conn1.fetchval("SELECT COUNT(*) FROM test_concurrent_session_mgmt.telemetry_events") == 0
            assert await conn1.fetchval("SELECT COUNT(*) FROM public.telemetry_events") == 3

            sentinel_notices = [n for n in notices if "TELEMETRY_PRUNE_SENTINEL:" in n]
            assert len(sentinel_notices) == 1
            sentinel_data = json.loads(sentinel_notices[0].split("TELEMETRY_PRUNE_SENTINEL: ", 1)[1])
            assert sentinel_data["canonical_row_count_before"] == 2
            assert sentinel_data["canonical_matched_count"] == 2
            assert sentinel_data["canonical_row_count_after"] == 3
            assert sentinel_data["result"] == "preserved"
        finally:
            try:
                await conn1.execute("SELECT pg_advisory_unlock_all();")
            except Exception:
                pass
            try:
                await conn2.execute("SELECT pg_advisory_unlock_all();")
            except Exception:
                pass
            await conn1.close()
            await conn2.close()

    asyncio.run(_test())


def test_db_behavior_concurrent_append_barrier_unblocks_cleanly_on_prune_failure(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn1 = await asyncpg.connect(isolated_db)
        conn2 = await asyncpg.connect(isolated_db)
        try:
            await conn1.execute("SET lock_timeout = '2000ms';")
            await conn2.execute("SET lock_timeout = '2000ms';")

            LOCK_BASELINE_CAPTURED = 888003
            LOCK_APPEND_COMPLETED = 888004

            await conn1.execute(f"SELECT pg_advisory_lock({LOCK_BASELINE_CAPTURED});")
            await conn2.execute(f"SELECT pg_advisory_lock({LOCK_APPEND_COMPLETED});")

            # Pruning an invalid/forbidden schema (e.g. public) fails before the trigger
            async def _run_failing_prune() -> list[str]:
                return await _run_sql_block(conn1, "public")

            async def _run_concurrent_append() -> None:
                try:
                    await asyncio.wait_for(
                        conn2.execute(f"SELECT pg_advisory_lock({LOCK_BASELINE_CAPTURED});"),
                        timeout=1.0,
                    )
                except (asyncio.TimeoutError, asyncpg.PostgresError):
                    # Expected to time out cleanly since conn1 fails before reaching trigger
                    pass
                finally:
                    try:
                        await conn2.execute(f"SELECT pg_advisory_unlock({LOCK_APPEND_COMPLETED});")
                    except Exception:
                        pass
                    try:
                        await conn2.execute(f"SELECT pg_advisory_unlock({LOCK_BASELINE_CAPTURED});")
                    except Exception:
                        pass

            with pytest.raises(asyncpg.PostgresError, match="refusing to prune telemetry_events: MANAGEMENT_AI_STORE_SCHEMA resolves to canonical public schema"):
                await asyncio.wait_for(
                    asyncio.gather(_run_failing_prune(), _run_concurrent_append()),
                    timeout=3.0,
                )
        finally:
            try:
                await conn1.execute("SELECT pg_advisory_unlock_all();")
            except Exception:
                pass
            try:
                await conn2.execute("SELECT pg_advisory_unlock_all();")
            except Exception:
                pass
            await conn1.close()
            await conn2.close()

    asyncio.run(_test())


# -----------------------------------------------------------------------------
# Deploy Script CLI Dry-Run Tests for Schema Resolution & Rejection
# -----------------------------------------------------------------------------

def _run_deploy_dry_run(env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    cmd = [
        str(DEPLOY_SCRIPT),
        "--environment", "dev",
        "--sha", "95a1455e3dc1a275b8d541fd2c432c3971013308",
        "--project-id", "pantheon-lupin-dev-20260719",
        "--dry-run",
    ]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


def test_deploy_script_defaults_to_management_ai_when_unset() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("MANAGEMENT_AI_STORE_SCHEMA", "DEV_MANAGEMENT_AI_STORE_SCHEMA")}
    res = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "dev",
            "--sha", "95a1455e3dc1a275b8d541fd2c432c3971013308",
            "--project-id", "pantheon-lupin-dev-20260719",
            "--dry-run",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "management_ai_store_schema=management_ai" in res.stdout


def test_deploy_script_rejects_empty_schema_in_env() -> None:
    res = _run_deploy_dry_run({"MANAGEMENT_AI_STORE_SCHEMA": ""})
    assert res.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: ''" in res.stderr


def test_deploy_script_rejects_empty_dev_schema_in_env() -> None:
    env_overrides = {"DEV_MANAGEMENT_AI_STORE_SCHEMA": ""}
    # Remove MANAGEMENT_AI_STORE_SCHEMA so it inherits from DEV_MANAGEMENT_AI_STORE_SCHEMA
    env = {k: v for k, v in os.environ.items() if k != "MANAGEMENT_AI_STORE_SCHEMA"}
    env.update(env_overrides)
    res = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "dev",
            "--sha", "95a1455e3dc1a275b8d541fd2c432c3971013308",
            "--project-id", "pantheon-lupin-dev-20260719",
            "--dry-run",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: ''" in res.stderr


def test_deploy_script_rejects_both_empty_schemas_in_env() -> None:
    res = _run_deploy_dry_run({
        "MANAGEMENT_AI_STORE_SCHEMA": "",
        "DEV_MANAGEMENT_AI_STORE_SCHEMA": "",
    })
    assert res.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: ''" in res.stderr


def test_deploy_script_rejects_public_schema_in_env() -> None:
    res = _run_deploy_dry_run({"MANAGEMENT_AI_STORE_SCHEMA": "public"})
    assert res.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA cannot be 'public'" in res.stderr

    res_upper = _run_deploy_dry_run({"MANAGEMENT_AI_STORE_SCHEMA": "PUBLIC"})
    assert res_upper.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA cannot be 'public'" in res_upper.stderr


def test_deploy_script_rejects_invalid_identifier_in_env() -> None:
    res = _run_deploy_dry_run({"MANAGEMENT_AI_STORE_SCHEMA": "bad;identifier"})
    assert res.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: 'bad;identifier'" in res.stderr

    res_num = _run_deploy_dry_run({"MANAGEMENT_AI_STORE_SCHEMA": "123invalid"})
    assert res_num.returncode != 0
    assert "MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: '123invalid'" in res_num.stderr


def test_deploy_script_accepts_valid_custom_schema_in_env() -> None:
    res = _run_deploy_dry_run({"MANAGEMENT_AI_STORE_SCHEMA": "custom_management_v2"})
    assert res.returncode == 0
    assert "management_ai_store_schema=custom_management_v2" in res.stdout
