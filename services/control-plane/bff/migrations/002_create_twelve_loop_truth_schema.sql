-- Migration: 002_create_twelve_loop_truth_schema.sql
-- Description: Additive PostgreSQL schema, keys, constraints, and indexes for durable receipt-derived twelve-loop truth.
-- Target Schema: loop_truth_projection

CREATE SCHEMA IF NOT EXISTS loop_truth_projection;

-- 1. Canonical Loop Receipts Store
-- Preserves immutable source receipts independently of read projections (supports rollback).
CREATE TABLE IF NOT EXISTS loop_truth_projection.loop_receipts (
    receipt_id TEXT PRIMARY KEY,
    receipt_type TEXT NOT NULL CHECK (receipt_type IN ('stimulus', 'terminal', 'next_consumer')),
    loop_id INT NOT NULL CHECK (loop_id BETWEEN 1 AND 12),
    correlation_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance IN ('live', 'replay', 'backfill')),
    status TEXT NOT NULL DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL,
    degradation_reason TEXT,
    causation_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_loop_receipts_key
    ON loop_truth_projection.loop_receipts (release_id, correlation_id, loop_id);

CREATE INDEX IF NOT EXISTS idx_loop_receipts_correlation
    ON loop_truth_projection.loop_receipts (correlation_id);

CREATE INDEX IF NOT EXISTS idx_loop_receipts_observed_at
    ON loop_truth_projection.loop_receipts (observed_at DESC);

-- 2. Projected Twelve-Loop Truth Observations
-- Durable read projection keyed by (release_id, correlation_id, loop_id).
-- Output is strictly receipt-derived: terminal plus next_consumer receipt required for completion.
CREATE TABLE IF NOT EXISTS loop_truth_projection.twelve_loop_observations (
    release_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    loop_id INT NOT NULL CHECK (loop_id BETWEEN 1 AND 12),
    owner TEXT NOT NULL,
    stimulus_id TEXT,
    stimulus_observed_at TIMESTAMPTZ,
    terminal_id TEXT,
    terminal_status TEXT,
    terminal_observed_at TIMESTAMPTZ,
    next_consumer_receipt_id TEXT,
    next_consumer_observed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('open', 'complete', 'failed', 'degraded', 'unobserved')),
    freshness_status TEXT NOT NULL CHECK (freshness_status IN ('fresh', 'stale', 'unavailable')),
    provenance TEXT NOT NULL CHECK (provenance IN ('live', 'replay', 'backfill')),
    observed_at TIMESTAMPTZ NOT NULL,
    degradation_reason TEXT,
    causation_id TEXT,
    receipt_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (release_id, correlation_id, loop_id)
);

CREATE INDEX IF NOT EXISTS idx_loop_obs_release_corr
    ON loop_truth_projection.twelve_loop_observations (release_id, correlation_id);

CREATE INDEX IF NOT EXISTS idx_loop_obs_updated
    ON loop_truth_projection.twelve_loop_observations (updated_at DESC);
