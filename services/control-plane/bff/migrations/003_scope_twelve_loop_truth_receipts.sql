-- Migration: 003_scope_twelve_loop_truth_receipts.sql
-- Description: Add tenant and environment scope columns, constraints, and indexes
-- to canonical loop receipts and observations for durable isolation.
-- Preserves existing legacy unscoped rows honestly without invented provenance.

-- 1. Add tenant_id and environment columns to loop_receipts
ALTER TABLE loop_truth_projection.loop_receipts
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS environment TEXT;

-- Drop legacy unscoped primary key constraint to allow scoped receipts
-- and legacy unscoped receipts to coexist without collision or silent loss.
ALTER TABLE loop_truth_projection.loop_receipts
    DROP CONSTRAINT IF EXISTS loop_receipts_pkey;

-- Create scoped unique index with NULLS NOT DISTINCT (Postgres 15+)
-- to support both scoped records and legacy unscoped records with honest NULLs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_loop_receipts_scoped_key
    ON loop_truth_projection.loop_receipts (tenant_id, environment, receipt_id)
    NULLS NOT DISTINCT;

-- Index for scoped receipt lookups
CREATE INDEX IF NOT EXISTS idx_loop_receipts_scope_key
    ON loop_truth_projection.loop_receipts (tenant_id, environment, release_id, correlation_id, loop_id);

CREATE INDEX IF NOT EXISTS idx_loop_receipts_scope_correlation
    ON loop_truth_projection.loop_receipts (tenant_id, environment, correlation_id);

-- 2. Add tenant_id and environment columns to twelve_loop_observations
ALTER TABLE loop_truth_projection.twelve_loop_observations
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS environment TEXT;

-- Drop legacy unscoped primary key constraint to allow cross-tenant rows.
-- NOTE ON SOURCE COMPATIBILITY: Dropping this primary key breaks pre-003 store
-- code that relies on `ON CONFLICT (release_id, correlation_id, loop_id)`.
-- To run pre-003 store code, execute the tested non-lossy rollback procedure
-- (`rollback_to_002_schema_sync`) which archives scoped rows to
-- `twelve_loop_observations_scoped_backup` and restores this primary key constraint.
ALTER TABLE loop_truth_projection.twelve_loop_observations
    DROP CONSTRAINT IF EXISTS twelve_loop_observations_pkey;

-- Create scoped unique index with NULLS NOT DISTINCT (Postgres 15+)
-- to support both scoped records and legacy unscoped records with honest NULLs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_loop_obs_scoped_key
    ON loop_truth_projection.twelve_loop_observations (tenant_id, environment, release_id, correlation_id, loop_id)
    NULLS NOT DISTINCT;

CREATE INDEX IF NOT EXISTS idx_loop_obs_scope_release_corr
    ON loop_truth_projection.twelve_loop_observations (tenant_id, environment, release_id, correlation_id);

-- 3. If re-applying migration 003 after a 002 rollback, restore any backed-up scoped observations and receipts
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'loop_truth_projection'
          AND table_name = 'loop_receipts_scoped_backup'
    ) THEN
        -- Verify no conflicting content between active table and backup
        IF EXISTS (
            SELECT 1
            FROM loop_truth_projection.loop_receipts_scoped_backup b
            JOIN loop_truth_projection.loop_receipts r
              ON (r.tenant_id IS NOT DISTINCT FROM b.tenant_id)
             AND (r.environment IS NOT DISTINCT FROM b.environment)
             AND r.receipt_id = b.receipt_id
            WHERE (r.receipt_type, r.loop_id, r.correlation_id, r.release_id, r.owner, r.provenance, r.status, r.payload)
               IS DISTINCT FROM
                  (b.receipt_type, b.loop_id, b.correlation_id, b.release_id, b.owner, b.provenance, b.status, b.payload)
        ) THEN
            RAISE EXCEPTION 'Conflicting receipt content detected between loop_receipts and loop_receipts_scoped_backup';
        END IF;

        INSERT INTO loop_truth_projection.loop_receipts
        SELECT * FROM loop_truth_projection.loop_receipts_scoped_backup
        ON CONFLICT (tenant_id, environment, receipt_id) DO NOTHING;

        -- Ensure all backup rows are present in loop_receipts before dropping backup
        IF EXISTS (
            SELECT 1
            FROM loop_truth_projection.loop_receipts_scoped_backup b
            WHERE NOT EXISTS (
                SELECT 1 FROM loop_truth_projection.loop_receipts r
                WHERE (r.tenant_id IS NOT DISTINCT FROM b.tenant_id)
                  AND (r.environment IS NOT DISTINCT FROM b.environment)
                  AND r.receipt_id = b.receipt_id
            )
        ) THEN
            RAISE EXCEPTION 'Failed to restore all receipts from loop_receipts_scoped_backup; preserving backup table';
        END IF;

        DROP TABLE loop_truth_projection.loop_receipts_scoped_backup;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'loop_truth_projection'
          AND table_name = 'twelve_loop_observations_scoped_backup'
    ) THEN
        -- Verify no conflicting content between active table and backup
        IF EXISTS (
            SELECT 1
            FROM loop_truth_projection.twelve_loop_observations_scoped_backup b
            JOIN loop_truth_projection.twelve_loop_observations o
              ON (o.tenant_id IS NOT DISTINCT FROM b.tenant_id)
             AND (o.environment IS NOT DISTINCT FROM b.environment)
             AND o.release_id = b.release_id
             AND o.correlation_id = b.correlation_id
             AND o.loop_id = b.loop_id
            WHERE (o.owner, o.status, o.freshness_status, o.provenance, o.receipt_ids)
               IS DISTINCT FROM
                  (b.owner, b.status, b.freshness_status, b.provenance, b.receipt_ids)
        ) THEN
            RAISE EXCEPTION 'Conflicting observation content detected between twelve_loop_observations and twelve_loop_observations_scoped_backup';
        END IF;

        INSERT INTO loop_truth_projection.twelve_loop_observations
        SELECT * FROM loop_truth_projection.twelve_loop_observations_scoped_backup
        ON CONFLICT (tenant_id, environment, release_id, correlation_id, loop_id) DO NOTHING;

        -- Ensure all backup observations are present before dropping backup
        IF EXISTS (
            SELECT 1
            FROM loop_truth_projection.twelve_loop_observations_scoped_backup b
            WHERE NOT EXISTS (
                SELECT 1 FROM loop_truth_projection.twelve_loop_observations o
                WHERE (o.tenant_id IS NOT DISTINCT FROM b.tenant_id)
                  AND (o.environment IS NOT DISTINCT FROM b.environment)
                  AND o.release_id = b.release_id
                  AND o.correlation_id = b.correlation_id
                  AND o.loop_id = b.loop_id
            )
        ) THEN
            RAISE EXCEPTION 'Failed to restore all observations from twelve_loop_observations_scoped_backup; preserving backup table';
        END IF;

        DROP TABLE loop_truth_projection.twelve_loop_observations_scoped_backup;
    END IF;
END $$;
