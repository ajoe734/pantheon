-- Migration: 002_drop_redundant_event_receipts_index.sql
-- Description: Drop the redundant second B-tree index on event_receipts(ingested_seq).
--              The UNIQUE constraint on ingested_seq (event_receipts_ingested_seq_key)
--              already indexes ingested_seq for cursor and contiguous-prefix lookups.
-- Target Schema: trade_journey_projection

-- Fresh/clean databases: no-op (IF EXISTS takes no table lock when the index is
-- absent). Legacy databases: one-time drop, bounded by the migration lock_timeout.
-- 001 no longer creates this index, so the drop never churns on later bootstraps.
DROP INDEX IF EXISTS trade_journey_projection.idx_event_receipts_ingested_seq;
