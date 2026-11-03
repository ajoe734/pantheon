-- Migration: 003_reclassify_stale_open_journeys.sql
-- Description: Bounded, resumable reclassification of journeys stuck in 'open'
--   into the explicit terminal statuses 'no_order' and 'unreconciled'.
-- Target Schema: trade_journey_projection
--
-- This migration only installs a function; it touches no rows, so it cannot
-- block projector readiness or hit the statement timeout. Rows are moved by
-- calling the function repeatedly (see projection_migration.reclassify_stale_open_journeys),
-- each call updating at most p_batch_size rows. Reclassified rows leave
-- status='open', so the work is naturally resumable and idempotent.
--
-- Rules (mirror JourneyMaterializer._rollup / deadline_status):
--   order_submission skipped                                  -> no_order
--   open, past deadline, reconciliation absent, no order stage -> no_order
--   open, past deadline, reconciliation absent, order stage    -> unreconciled
-- Journeys within the deadline stay open and keep the stalled flag behaviour.

CREATE OR REPLACE FUNCTION trade_journey_projection.reclassify_stale_open_journeys(
    p_as_of TIMESTAMPTZ,
    p_deadline_seconds BIGINT,
    p_batch_size INTEGER DEFAULT 1000
) RETURNS TABLE (new_status TEXT, reclassified BIGINT)
LANGUAGE sql
AS $$
    WITH candidates AS (
        SELECT tenant_id, environment, journey_id,
               CASE
                   WHEN stage_coverage -> 'order_submission' ->> 'status' = 'skipped' THEN 'no_order'
                   WHEN stage_coverage ? 'order_submission' THEN 'unreconciled'
                   ELSE 'no_order'
               END AS target_status
        FROM trade_journey_projection.journeys
        WHERE status = 'open'
          AND NOT (stage_coverage ? 'reconciliation')
          AND (
              stage_coverage -> 'order_submission' ->> 'status' = 'skipped'
              OR last_occurred_at < p_as_of - make_interval(secs => p_deadline_seconds)
          )
        ORDER BY tenant_id, environment, journey_id
        LIMIT GREATEST(1, LEAST(p_batch_size, 10000))
        FOR UPDATE SKIP LOCKED
    ), updated AS (
        UPDATE trade_journey_projection.journeys j
        SET status = c.target_status,
            is_terminal = TRUE,
            projection_revision = j.projection_revision + 1
        FROM candidates c
        WHERE j.tenant_id = c.tenant_id
          AND j.environment = c.environment
          AND j.journey_id = c.journey_id
        RETURNING c.target_status
    )
    SELECT target_status, COUNT(*)::BIGINT FROM updated GROUP BY target_status;
$$;
