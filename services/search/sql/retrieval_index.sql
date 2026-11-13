-- Governed search and memory retrieval index schema with native FTS and pgvector
CREATE EXTENSION IF NOT EXISTS vector;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pantheon_retrieval') THEN
        CREATE ROLE pantheon_retrieval NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS search_retrieval_index (
    id VARCHAR(128) NOT NULL,
    record_kind VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    persona_id VARCHAR(64),
    workspace_id VARCHAR(64),
    environment_scope TEXT[] NOT NULL DEFAULT '{"paper"}',
    access_scope TEXT[] NOT NULL DEFAULT '{"public"}',
    license_scope VARCHAR(64) NOT NULL DEFAULT 'internal',
    role_scope TEXT[] NOT NULL DEFAULT '{}',
    sensitivity VARCHAR(64) NOT NULL DEFAULT 'internal',
    capital_pool_scope TEXT[] NOT NULL DEFAULT '{}',
    source_type VARCHAR(64) NOT NULL DEFAULT 'internal_note',
    asset_class TEXT[] NOT NULL DEFAULT '{}',
    strategy_id VARCHAR(64),
    title TEXT NOT NULL,
    search_text TEXT NOT NULL,
    content_ref TEXT,
    citation_label TEXT,
    evidence_bundle_id TEXT,
    evidence_item_id TEXT,
    event_time TIMESTAMPTZ,
    available_time TIMESTAMPTZ,
    relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    embedding vector(1024),
    lexical_text TEXT NOT NULL DEFAULT '',
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', lexical_text)) STORED,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, id)
);

-- This is a disposable projection. Old unigram indexes must be rebuilt from
-- owner stores, never retokenized in SQL with different segmentation rules.
ALTER TABLE search_retrieval_index ADD COLUMN IF NOT EXISTS lexical_text TEXT NOT NULL DEFAULT '';
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'search_retrieval_index' AND column_name = 'tsv'
          AND generation_expression LIKE '%regexp_replace%'
    ) THEN
        ALTER TABLE search_retrieval_index DROP COLUMN tsv;
        ALTER TABLE search_retrieval_index ADD COLUMN tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('simple', lexical_text)) STORED;
    END IF;
END $$;

-- Lexical Full-Text Search Index (GIN)
CREATE INDEX IF NOT EXISTS idx_search_retrieval_tsv
    ON search_retrieval_index USING gin (tsv);

-- Cosine Distance HNSW Vector Index
CREATE INDEX IF NOT EXISTS idx_search_retrieval_embedding
    ON search_retrieval_index USING hnsw (embedding vector_cosine_ops);

-- Partitioning / Filter Indices
CREATE INDEX IF NOT EXISTS idx_search_retrieval_kind_active
    ON search_retrieval_index (record_kind, is_active);

CREATE INDEX IF NOT EXISTS idx_search_retrieval_tenant
    ON search_retrieval_index (tenant_id);

CREATE INDEX IF NOT EXISTS idx_search_retrieval_persona_workspace
    ON search_retrieval_index (persona_id, workspace_id);

CREATE INDEX IF NOT EXISTS idx_search_retrieval_event_time
    ON search_retrieval_index (event_time);

CREATE INDEX IF NOT EXISTS idx_search_retrieval_available_time
    ON search_retrieval_index (available_time);

-- Fail-closed Row-Level Security (RLS)
ALTER TABLE search_retrieval_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_retrieval_index FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_policy ON search_retrieval_index;
CREATE POLICY tenant_isolation_policy ON search_retrieval_index
FOR ALL
USING (
    tenant_id = current_setting('pantheon.current_tenant', true)
    AND current_setting('pantheon.current_tenant', true) IS NOT NULL
    AND current_setting('pantheon.current_tenant', true) != ''
);

GRANT SELECT, INSERT, UPDATE, DELETE ON search_retrieval_index TO pantheon_retrieval;

-- Retrieval query log: one row per query served on the hybrid backend.
-- Written only by the search service request path; there is no seed or
-- synthetic writer. persona_private rows keep their persona scope under RLS.
CREATE TABLE IF NOT EXISTS retrieval_query_log (
    query_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    persona_id VARCHAR(64),
    workspace_id VARCHAR(64),
    persona_private BOOLEAN NOT NULL DEFAULT FALSE,
    request_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    caller_decision_type TEXT,
    source_event_id TEXT,
    attribution_missing_reason TEXT,
    retrieval_mode VARCHAR(32) NOT NULL,
    model_name TEXT NOT NULL,
    returned_ids TEXT[] NOT NULL DEFAULT '{}',
    returned_source_event_ids TEXT[] NOT NULL DEFAULT '{}',
    latency_ms DOUBLE PRECISION NOT NULL,
    origin VARCHAR(32) NOT NULL DEFAULT 'live_request' CHECK (origin = 'live_request'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_retrieval_query_log_recorded
    ON retrieval_query_log (tenant_id, recorded_at);

CREATE TABLE IF NOT EXISTS retrieval_query_labels (
    query_log_id UUID NOT NULL REFERENCES retrieval_query_log(query_log_id),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    source_event_id TEXT NOT NULL,
    labelled_by TEXT NOT NULL,
    labelled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (query_log_id, source_event_id)
);

ALTER TABLE retrieval_query_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE retrieval_query_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS query_log_scope_policy ON retrieval_query_log;
CREATE POLICY query_log_scope_policy ON retrieval_query_log
FOR ALL
USING (
    tenant_id = current_setting('pantheon.current_tenant', true)
    AND current_setting('pantheon.current_tenant', true) <> ''
    AND (persona_private = FALSE
         OR persona_id = NULLIF(current_setting('pantheon.current_persona', true), ''))
);
ALTER TABLE retrieval_query_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE retrieval_query_labels FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS query_label_scope_policy ON retrieval_query_labels;
CREATE POLICY query_label_scope_policy ON retrieval_query_labels
FOR ALL
USING (tenant_id = current_setting('pantheon.current_tenant', true)
       AND current_setting('pantheon.current_tenant', true) <> '');

GRANT SELECT, INSERT ON retrieval_query_log TO pantheon_retrieval;
GRANT SELECT, INSERT ON retrieval_query_labels TO pantheon_retrieval;
