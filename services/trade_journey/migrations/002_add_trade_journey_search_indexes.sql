-- Migration: 002_add_trade_journey_search_indexes.sql
-- Description: Add trigram and composite B-tree indexes for Trade Journey list search.
-- Target Schema: trade_journey_projection

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_journeys_journey_id_trgm
    ON trade_journey_projection.journeys USING gin (journey_id gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_identity_links_value_trgm
    ON trade_journey_projection.identity_links USING gin (identifier_value gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_identity_links_tenant_env_value_journey
    ON trade_journey_projection.identity_links (tenant_id, environment, identifier_value, journey_id);

CREATE INDEX IF NOT EXISTS idx_journeys_summary_gin
    ON trade_journey_projection.journeys USING gin (
        (COALESCE(current_identity_summary -> 'identifiers', current_identity_summary))
    );
