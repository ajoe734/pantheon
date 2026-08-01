-- Migration: 001_create_trade_journey_projection_schema.sql
-- Description: Additive PostgreSQL schema, keys, constraints, and indexes for the Trade Journey relational projection store.
-- Target Schema: trade_journey_projection

CREATE SCHEMA IF NOT EXISTS trade_journey_projection;

-- 1. Controller table
CREATE TABLE IF NOT EXISTS trade_journey_projection.controller (
    controller_id TEXT NOT NULL,
    tenant_scope TEXT NOT NULL,
    environment_scope TEXT NOT NULL,
    checkpoint_seq BIGINT NOT NULL DEFAULT 0,
    source_high_watermark BIGINT NOT NULL DEFAULT 0,
    backlog_count BIGINT NOT NULL DEFAULT 0,
    projection_revision BIGINT NOT NULL DEFAULT 0,
    deployment_sha TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'live',
    status TEXT NOT NULL DEFAULT 'ok',
    accepted_live BOOLEAN NOT NULL DEFAULT FALSE,
    last_poll_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_live_success_at TIMESTAMPTZ,
    last_recovery_at TIMESTAMPTZ,
    last_backfill_at TIMESTAMPTZ,
    last_replay_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    last_error_message TEXT NOT NULL DEFAULT '',
    unresolved_quarantine_count BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (controller_id, tenant_scope, environment_scope)
);

-- 2. Event receipts table
CREATE TABLE IF NOT EXISTS trade_journey_projection.event_receipts (
    event_id TEXT PRIMARY KEY,
    ingested_seq BIGINT UNIQUE NOT NULL,
    fingerprint TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    journey_id TEXT NOT NULL DEFAULT '',
    loop_run_id TEXT NOT NULL DEFAULT '',
    source_event_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('applied', 'duplicate', 'ignored', 'quarantined')),
    projection_revision BIGINT NOT NULL DEFAULT 0,
    projected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_event_receipts_ingested_seq
    ON trade_journey_projection.event_receipts (ingested_seq);

CREATE INDEX IF NOT EXISTS idx_event_receipts_tenant_env_journey
    ON trade_journey_projection.event_receipts (tenant_id, environment, journey_id)
    WHERE journey_id != '';

-- 3. Identity links table
CREATE TABLE IF NOT EXISTS trade_journey_projection.identity_links (
    tenant_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    identifier_type TEXT NOT NULL CHECK (identifier_type IN (
        'journey_id', 'trade_id', 'order_id', 'fill_id', 'position_id',
        'signal_id', 'proposal_id', 'allocation_id', 'intent_id', 'loop_run_id'
    )),
    identifier_value TEXT NOT NULL,
    journey_id TEXT NOT NULL,
    first_ingested_seq BIGINT NOT NULL,
    last_ingested_seq BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, environment, identifier_type, identifier_value)
);

CREATE INDEX IF NOT EXISTS idx_identity_links_tenant_env_journey
    ON trade_journey_projection.identity_links (tenant_id, environment, journey_id);

-- 4. Journeys table
CREATE TABLE IF NOT EXISTS trade_journey_projection.journeys (
    tenant_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    journey_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    stage_coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_terminal BOOLEAN NOT NULL DEFAULT FALSE,
    first_occurred_at TIMESTAMPTZ NOT NULL,
    last_occurred_at TIMESTAMPTZ NOT NULL,
    first_ingested_seq BIGINT NOT NULL,
    last_ingested_seq BIGINT NOT NULL,
    current_identity_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    diagnostic_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    loop_run_id TEXT NOT NULL DEFAULT '',
    projection_revision BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, environment, journey_id)
);

CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_updated_journey
    ON trade_journey_projection.journeys (tenant_id, environment, updated_at DESC, journey_id DESC);

CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_created_journey
    ON trade_journey_projection.journeys (tenant_id, environment, created_at DESC, journey_id DESC);

CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_status_updated_journey
    ON trade_journey_projection.journeys (tenant_id, environment, status, updated_at DESC, journey_id DESC);

CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_loop_run
    ON trade_journey_projection.journeys (tenant_id, environment, loop_run_id)
    WHERE loop_run_id != '';

-- 5. Journey stages table
CREATE TABLE IF NOT EXISTS trade_journey_projection.journey_stages (
    tenant_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    journey_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_status TEXT NOT NULL DEFAULT 'completed',
    stage_ordinal INT NOT NULL,
    source_ingested_seq BIGINT NOT NULL,
    event_sequence BIGINT NOT NULL DEFAULT 0,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    contract_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_references JSONB NOT NULL DEFAULT '[]'::jsonb,
    projection_revision BIGINT NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, environment, journey_id, source_event_id, stage_name)
);

CREATE INDEX IF NOT EXISTS idx_journey_stages_timeline
    ON trade_journey_projection.journey_stages (tenant_id, environment, journey_id, stage_ordinal, event_sequence, occurred_at, source_ingested_seq, source_event_id);

-- 6. Loop runs table
CREATE TABLE IF NOT EXISTS trade_journey_projection.loop_runs (
    tenant_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    loop_run_id TEXT NOT NULL,
    journey_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    lifecycle_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    freshness_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
    contract_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    projection_revision BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, environment, loop_run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_loop_runs_tenant_env_journey
    ON trade_journey_projection.loop_runs (tenant_id, environment, journey_id)
    WHERE journey_id != '';

CREATE INDEX IF NOT EXISTS idx_loop_runs_tenant_env_updated_loop
    ON trade_journey_projection.loop_runs (tenant_id, environment, updated_at DESC, loop_run_id DESC);

-- 7. Quarantine table
CREATE TABLE IF NOT EXISTS trade_journey_projection.quarantine (
    event_id TEXT NOT NULL,
    ingested_seq BIGINT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT NOT NULL DEFAULT '',
    source_event_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '',
    journey_id TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    occurrence_count INT NOT NULL DEFAULT 1,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    resolution_audit_ref TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (event_id, ingested_seq)
);

CREATE INDEX IF NOT EXISTS idx_quarantine_status_first_seen
    ON trade_journey_projection.quarantine (resolution_status, first_seen_at);
