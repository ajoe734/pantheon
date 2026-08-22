from __future__ import annotations

import asyncio
import os
import re
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
    assert "canonical_count_before != canonical_count_after" in block
    assert "canonical_min_created_before IS DISTINCT FROM canonical_min_created_after" in block
    assert "canonical_checksum_before != canonical_checksum_after" in block
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


def test_db_behavior_drift_guard_catches_canonical_mutation(
    isolated_db: str,
) -> None:
    async def _test() -> None:
        conn = await asyncpg.connect(isolated_db)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_drift_schema;")
            await conn.execute(
                """
                INSERT INTO public.telemetry_events (event_id, event_type, created_at, payload) VALUES
                ('evt-c-001', 'system.start', '2026-08-22T05:00:00Z', '{"k": 1}');
                """
            )

            # A custom DO block that simulates canonical table mutation during prune
            drift_sim_sql = """
            SELECT set_config('pantheon.mgmt_ai_schema', 'test_drift_schema', false);
            DO $prune$
            DECLARE
              target_schema text := current_setting('pantheon.mgmt_ai_schema');
              canonical_count_before bigint := 0;
              canonical_count_after bigint := 0;
              canonical_min_created_before timestamptz := null;
              canonical_min_created_after timestamptz := null;
              canonical_checksum_before text := 'none';
              canonical_checksum_after text := 'none';
            BEGIN
              SELECT COUNT(*), MIN(created_at), COALESCE(MD5(STRING_AGG(COALESCE(event_id::text, '') || ':' || COALESCE(created_at::text, ''), ',' ORDER BY created_at ASC, event_id ASC)), 'empty')
                INTO canonical_count_before, canonical_min_created_before, canonical_checksum_before
                FROM public.telemetry_events;

              -- Simulate accidental canonical deletion
              DELETE FROM public.telemetry_events;

              SELECT COUNT(*), MIN(created_at), COALESCE(MD5(STRING_AGG(COALESCE(event_id::text, '') || ':' || COALESCE(created_at::text, ''), ',' ORDER BY created_at ASC, event_id ASC)), 'empty')
                INTO canonical_count_after, canonical_min_created_after, canonical_checksum_after
                FROM public.telemetry_events;

              IF canonical_count_before != canonical_count_after
                 OR canonical_min_created_before IS DISTINCT FROM canonical_min_created_after
                 OR canonical_checksum_before != canonical_checksum_after THEN
                RAISE EXCEPTION 'canonical telemetry drift detected: count before=% after=%, min_created before=% after=%, checksum before=% after=%',
                  canonical_count_before, canonical_count_after, canonical_min_created_before, canonical_min_created_after, canonical_checksum_before, canonical_checksum_after;
              END IF;
            END
            $prune$;
            """
            with pytest.raises(asyncpg.PostgresError, match="canonical telemetry drift detected"):
                await conn.execute(drift_sim_sql)
        finally:
            await conn.close()

    asyncio.run(_test())
