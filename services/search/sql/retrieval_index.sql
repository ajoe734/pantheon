-- Governed search and memory retrieval index schema with native FTS and pgvector
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS search_retrieval_index (
    id VARCHAR(128) PRIMARY KEY,
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
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', regexp_replace(coalesce(title, '') || ' ' || coalesce(search_text, ''), '([\u4e00-\u9fff])', ' \1 ', 'g'))) STORED,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- Row-Level Security (RLS)
ALTER TABLE search_retrieval_index ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'search_retrieval_index' AND policyname = 'tenant_isolation_policy'
    ) THEN
        CREATE POLICY tenant_isolation_policy ON search_retrieval_index
        FOR ALL
        USING (
            tenant_id = current_setting('pantheon.current_tenant', true)
            OR current_setting('pantheon.current_tenant', true) IS NULL
            OR current_setting('pantheon.current_tenant', true) = ''
            OR 'public' = ANY(access_scope)
        );
    END IF;
END $$;
