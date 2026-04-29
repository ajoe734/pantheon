# Source-Ingest and Search Postgres Store Pilot

Status: task-scoped pilot for `SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT`
Last updated: 2026-04-29

## Scope

This pilot keeps JSONL as the default single-VM baseline and adds an
optional Postgres-backed path for two cross-service stores:

1. **Source evidence** — written by `source-ingest`, read by `search`
2. **Search snapshots** — written by `search`

The pilot validates the write-ownership and read-only sharing boundary
defined in `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`.

## Ownership Map

| Store | Write owner | Postgres table | Read contract | Pilot state |
|---|---|---|---|---|
| Source evidence (source records, evidence items, bundles, knowledge objects) | `source-ingest-svc` | `source_ingest.source_evidence` | `search-svc` via read-only role or `source-ingest` API | Implemented behind env flag |
| Search index snapshots | `search-svc` | `search_svc.search_index_snapshots` | Internal to search | Implemented behind env flag |

The mapping follows `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`:
shared Postgres cluster is allowed; each table has exactly one write owner;
non-owner services may read through a read-only database role or the owner
service API, and must never write directly.

## Source-Ingest Activation

Default is JSONL:

```
SOURCE_INGEST_EVIDENCE_BACKEND=jsonl
```

Postgres pilot (evidence store only):

```
SOURCE_INGEST_EVIDENCE_BACKEND=postgres
SOURCE_INGEST_EVIDENCE_DSN=postgresql://source-ingest-writer@postgres/pantheon
```

Optional table and bootstrap controls:

```
SOURCE_INGEST_EVIDENCE_TABLE=source_ingest.source_evidence
SOURCE_INGEST_EVIDENCE_BOOTSTRAP=1
```

When enabled, source evidence records (source records, evidence items,
evidence bundles, knowledge objects) are persisted to Postgres.
The ingest schedule store and connector config store remain JSONL in
this pilot.

## Search Activation

Default is JSONL for both stores:

```
SEARCH_INDEX_STORE_BACKEND=jsonl
SEARCH_EVIDENCE_BACKEND=jsonl
```

Postgres pilot (index snapshots written to Postgres):

```
SEARCH_INDEX_STORE_BACKEND=postgres
SEARCH_INDEX_STORE_DSN=postgresql://search-writer@postgres/pantheon
```

Optional:

```
SEARCH_INDEX_STORE_TABLE=search_svc.search_index_snapshots
SEARCH_INDEX_STORE_BOOTSTRAP=1
```

Postgres pilot (evidence read from source-ingest's Postgres table):

```
SEARCH_EVIDENCE_BACKEND=postgres
SEARCH_EVIDENCE_DSN=postgresql://search-reader@postgres/pantheon
SEARCH_EVIDENCE_TABLE=source_ingest.source_evidence
```

The `SEARCH_EVIDENCE_BACKEND=postgres` path uses
`PostgresReadOnlyEvidenceRepository`, which raises `EvidenceValidationError`
on any write attempt.  This enforces the ownership boundary: search reads
evidence from the Postgres table but never writes to it.

## Bootstrap DDL

### source_ingest.source_evidence

```sql
CREATE SCHEMA IF NOT EXISTS source_ingest;

CREATE TABLE IF NOT EXISTS source_ingest.source_evidence (
    append_id   BIGSERIAL PRIMARY KEY,
    record_id   TEXT NOT NULL,
    record_type TEXT NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (record_type, record_id)
);
```

`record_type` is one of: `source_record`, `evidence_item`,
`evidence_bundle`, `knowledge_object`.  The `UNIQUE (record_type, record_id)`
constraint enables upsert semantics matching the JSONL log.

### search_svc.search_index_snapshots

```sql
CREATE SCHEMA IF NOT EXISTS search_svc;

CREATE TABLE IF NOT EXISTS search_svc.search_index_snapshots (
    append_id  BIGSERIAL PRIMARY KEY,
    request_id TEXT NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`append_id` preserves replay ordering.  Latest row per `request_id`
wins on reload, matching the JSONL append-then-replace-on-read semantics.

## Implementation Files

| File | Role |
|---|---|
| `services/source_ingestion/pg_store.py` | `PostgresSourceEvidenceRepository`, `build_source_evidence_repository` factory |
| `services/search/pg_store.py` | `PostgresReadOnlyEvidenceRepository`, `PostgresSearchIndexStore`, factory functions |
| `services/source_ingestion/main.py` | Uses `build_source_evidence_repository` instead of `JsonlEvidenceRepository` directly |
| `services/search/main.py` | Uses `build_search_index_store` and `build_search_evidence_repository` instead of JSONL stores directly |
| `services/source_ingestion/test_postgres_store.py` | Unit tests (fake DB; no psycopg required) |
| `services/search/test_postgres_store.py` | Unit tests (fake DB; no psycopg required) |
| `services/knowledge/evidence/repository.py` | Added no-op `reload()` to `InMemoryEvidenceRepository` |

## Compose Integration

The default compose stack is unchanged: both services start with JSONL stores.
To enable the Postgres pilot for a single-VM run:

```bash
SOURCE_INGEST_EVIDENCE_BACKEND=postgres \
SOURCE_INGEST_EVIDENCE_DSN="postgresql://source-ingest-writer@postgres:5432/pantheon" \
SEARCH_INDEX_STORE_BACKEND=postgres \
SEARCH_INDEX_STORE_DSN="postgresql://search-writer@postgres:5432/pantheon" \
SEARCH_EVIDENCE_BACKEND=postgres \
SEARCH_EVIDENCE_DSN="postgresql://search-reader@postgres:5432/pantheon" \
docker compose up source-ingest search-svc
```

The services must be able to reach the `postgres` container.  Add
`depends_on: [postgres]` when enabling Postgres mode in compose.
