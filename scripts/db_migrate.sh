#!/usr/bin/env bash
# Run all Pantheon DB schema migrations against the configured Postgres instance.
# Safe to re-run: every migration is idempotent and backfills only missing
# database-owned values.
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
    # Explicit sequence keeps the upgrade path idempotent even when the base
    # telemetry_events table predates the ingestion cursor columns.
    """
    CREATE SEQUENCE IF NOT EXISTS telemetry_events_ingested_seq_seq AS BIGINT;
    """,
    # Telemetry ingest table (idempotent)
    """
    CREATE TABLE IF NOT EXISTS telemetry_events (
        event_id     TEXT        PRIMARY KEY,
        event_type   TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL,
        payload      JSONB       NOT NULL,
        ingested_seq BIGINT      NOT NULL DEFAULT nextval('telemetry_events_ingested_seq_seq'),
        ingested_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    """,
    # Upgrade existing telemetry tables with a database-owned replay cursor and
    # commit timestamp. nextval backfills existing rows with unique sequence
    # values and supplies the default for every future canonical append.
    """
    ALTER TABLE telemetry_events
        ADD COLUMN IF NOT EXISTS ingested_seq BIGINT;
    ALTER TABLE telemetry_events
        ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
    ALTER TABLE telemetry_events
        ALTER COLUMN ingested_seq SET DEFAULT nextval('telemetry_events_ingested_seq_seq');
    UPDATE telemetry_events
        SET ingested_seq = nextval('telemetry_events_ingested_seq_seq')
        WHERE ingested_seq IS NULL;
    ALTER TABLE telemetry_events
        ALTER COLUMN ingested_seq SET NOT NULL;
    ALTER TABLE telemetry_events
        ALTER COLUMN ingested_at SET DEFAULT clock_timestamp();
    UPDATE telemetry_events
        SET ingested_at = clock_timestamp()
        WHERE ingested_at IS NULL;
    ALTER TABLE telemetry_events
        ALTER COLUMN ingested_at SET NOT NULL;
    ALTER SEQUENCE telemetry_events_ingested_seq_seq
        OWNED BY telemetry_events.ingested_seq;
    """,
    # Stable cursor for commit-order projector catch-up.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_ingested_seq
        ON telemetry_events (ingested_seq);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_ingested_at
        ON telemetry_events (ingested_at DESC);
    """,
    # Projector hot-path cursor lookup; migrations own this index because the
    # long-lived projector must never perform DDL at startup.
    """
    CREATE INDEX IF NOT EXISTS idx_telemetry_events_event_type_ingested_seq
        ON telemetry_events (event_type, ingested_seq);
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
    # Loop controller records table for durable controller truth substrate
    """
    CREATE TABLE IF NOT EXISTS loop_controller_records (
        loop_id             TEXT        NOT NULL,
        tenant_id           TEXT        NOT NULL,
        environment         TEXT        NOT NULL,
        controller_id       TEXT        NOT NULL,
        controller_name     TEXT        NOT NULL,
        deployment_sha      TEXT        NOT NULL,
        desired_state_query TEXT,
        actual_state_query  TEXT,
        last_heartbeat_at   TIMESTAMPTZ,
        last_tick_at        TIMESTAMPTZ,
        last_success_at     TIMESTAMPTZ,
        last_failure_at     TIMESTAMPTZ,
        last_failure_reason TEXT,
        last_repair_at      TIMESTAMPTZ,
        last_repair_reason  TEXT,
        backlog             INTEGER,
        lag                 INTEGER,
        dlq_count           INTEGER,
        evidence_refs       JSONB       NOT NULL DEFAULT '[]'::jsonb,
        truth_level         TEXT        NOT NULL,
        lease_expires_at    TIMESTAMPTZ,
        payload             JSONB       NOT NULL DEFAULT '{}'::jsonb,
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (loop_id, tenant_id, environment)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_loop_controller_records_updated_at
        ON loop_controller_records (updated_at DESC);
    """,
    # Source Ingestion source management tables (SD-SRCM-01, SD-SRCM-02)
    """
    CREATE SCHEMA IF NOT EXISTS source_ingest;
    """,
    """
    CREATE TABLE IF NOT EXISTS source_ingest.data_source_instances (
        source_instance_id TEXT PRIMARY KEY,
        source_kind        TEXT NOT NULL,
        definition_id      TEXT NOT NULL,
        connector_id       TEXT NOT NULL UNIQUE,
        current_revision   INTEGER NOT NULL DEFAULT 1,
        lifecycle_state    TEXT NOT NULL,
        payload            JSONB NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_data_source_instances_definition
        ON source_ingest.data_source_instances (definition_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_data_source_instances_lifecycle
        ON source_ingest.data_source_instances (lifecycle_state);
    """,
    """
    CREATE TABLE IF NOT EXISTS source_ingest.source_desired_states (
        source_instance_id        TEXT NOT NULL,
        revision                  INTEGER NOT NULL,
        desired_lifecycle         TEXT NOT NULL,
        definition_id             TEXT NOT NULL,
        definition_deployment_sha TEXT NOT NULL,
        connector_config          JSONB NOT NULL,
        schedule                  JSONB NOT NULL,
        limits                    JSONB NOT NULL,
        allowed_hosts             JSONB NOT NULL,
        last_command_receipt_id   TEXT,
        payload                   JSONB NOT NULL,
        created_at                TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (source_instance_id, revision)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_desired_states_def
        ON source_ingest.source_desired_states (definition_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS source_ingest.source_command_receipts (
        receipt_id             TEXT PRIMARY KEY,
        command_id             TEXT NOT NULL UNIQUE,
        idempotency_key_hash   TEXT NOT NULL UNIQUE,
        source_instance_id     TEXT NOT NULL,
        command_type           TEXT NOT NULL,
        status                 TEXT NOT NULL,
        before_revision        INTEGER NOT NULL,
        after_revision         INTEGER NOT NULL,
        payload                JSONB NOT NULL,
        created_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        completed_at           TIMESTAMPTZ
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_command_receipts_instance
        ON source_ingest.source_command_receipts (source_instance_id, created_at DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS source_ingest.source_canary_results (
        canary_id          TEXT PRIMARY KEY,
        source_instance_id TEXT NOT NULL,
        definition_id      TEXT NOT NULL,
        status             TEXT NOT NULL,
        payload            JSONB NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        completed_at       TIMESTAMPTZ
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_canary_results_instance
        ON source_ingest.source_canary_results (source_instance_id, created_at DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS source_ingest.source_observed_snapshots (
        source_instance_id    TEXT NOT NULL,
        observed_revision     INTEGER NOT NULL,
        desired_revision      INTEGER NOT NULL,
        reconciliation_status TEXT NOT NULL,
        effective_lifecycle   TEXT NOT NULL,
        payload               JSONB NOT NULL,
        observed_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (source_instance_id, observed_revision)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_observed_snapshots_instance
        ON source_ingest.source_observed_snapshots (source_instance_id, observed_at DESC);
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
