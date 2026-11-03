-- Migration: 003_reclassify_stale_open_journeys.sql
-- Description: Partial index that keeps the bounded, resumable reclassification of
--   journeys stuck in 'open' (into the terminal statuses 'no_order' and
--   'unreconciled') cheap.
-- Target Schema: trade_journey_projection
--
-- This migration installs no routines (the runtime-role reconcile admits only the
-- projection tables and their indexes) and touches no rows, so it cannot block
-- projector readiness. Rows are moved by
-- projection_migration.reclassify_stale_open_journeys, which executes the bounded
-- UPDATE in RECLASSIFY_STALE_OPEN_SQL batch by batch.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_journeys_open_last_occurred
    ON trade_journey_projection.journeys (last_occurred_at, tenant_id, environment, journey_id)
    WHERE status = 'open';
