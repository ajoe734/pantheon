-- Migration: 003_scope_twelve_loop_truth_receipts.sql
-- Description: Add tenant and environment scope columns, constraints, and indexes
-- to canonical loop receipts and observations for durable isolation.
-- Preserves existing legacy unscoped rows honestly without invented provenance.

-- 1. Add tenant_id and environment columns to loop_receipts
ALTER TABLE loop_truth_projection.loop_receipts
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS environment TEXT;

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

-- 3. If re-applying migration 003 after a 002 rollback, restore any backed-up scoped observations
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'loop_truth_projection'
          AND table_name = 'twelve_loop_observations_scoped_backup'
    ) THEN
        INSERT INTO loop_truth_projection.twelve_loop_observations
        SELECT * FROM loop_truth_projection.twelve_loop_observations_scoped_backup
        ON CONFLICT (tenant_id, environment, release_id, correlation_id, loop_id) DO NOTHING;

        DROP TABLE loop_truth_projection.twelve_loop_observations_scoped_backup;
    END IF;
END $$;
