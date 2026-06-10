#!/usr/bin/env bash
# Run all Pantheon DB schema migrations against the configured Postgres instance.
# Safe to re-run: all DDL uses IF NOT EXISTS / ON CONFLICT DO NOTHING.
#
# Usage (outside Docker):
#   TELEMETRY_DB_DSN=postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon \
#     bash scripts/db_migrate.sh
#
# Usage (inside the control-plane compose stack):
#   docker compose -f docker-compose.control.yml exec postgres \
#     psql -U pantheon_app -d pantheon -f /migrations/schema.sql
set -euo pipefail

: "${TELEMETRY_DB_DSN:=${DATABASE_URL:-postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon}}"

echo "==> Running Pantheon DB migrations"
echo "    DSN: ${TELEMETRY_DB_DSN//:*@/:***@}"

python3 - <<'PYEOF'
import asyncio
import os
import sys

DSN = os.environ.get("TELEMETRY_DB_DSN") or os.environ.get("DATABASE_URL", "")

MIGRATIONS = [
    # Telemetry ingest table (idempotent)
    """
    CREATE TABLE IF NOT EXISTS telemetry_events (
        event_id   TEXT        PRIMARY KEY,
        event_type TEXT        NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        payload    JSONB       NOT NULL
    );
    """,
    # Index for time-range queries
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_created_at
        ON telemetry_events (created_at DESC);
    """,
    # Index for type-filter queries
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_event_type
        ON telemetry_events (event_type);
    """,
    # Expression indexes for operational readback, replay triage, and lineage joins
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_binding_id
        ON telemetry_events ((payload->>'binding_id'));
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_runtime_id
        ON telemetry_events ((payload->>'runtime_id'));
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_deployment_stage
        ON telemetry_events ((payload->>'deployment_stage'));
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_payload_gin
        ON telemetry_events USING GIN (payload);
    """,
]

async def run():
    try:
        import asyncpg  # type: ignore
    except ImportError:
        print("ERROR: asyncpg not installed — install it or run migrations inside a service container", file=sys.stderr)
        sys.exit(1)

    if not DSN:
        print("ERROR: TELEMETRY_DB_DSN / DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(DSN)
    try:
        for i, sql in enumerate(MIGRATIONS, 1):
            await conn.execute(sql)
            print(f"    migration {i}/{len(MIGRATIONS)}: ok")
    finally:
        await conn.close()

asyncio.run(run())
PYEOF

echo "==> DB migrations complete."
