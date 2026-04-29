# Review: SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT

**Reviewer:** Claude
**Date:** 2026-04-29
**Status:** approved

## Scope

Optional Postgres-backed store pilot for source-ingest and search.
JSONL remains default; Postgres activated by env.

## Acceptance Criteria Verification

### AC-1: JSONL remains default
- `build_source_evidence_repository` returns `JsonlEvidenceRepository` when `SOURCE_INGEST_EVIDENCE_BACKEND` is unset.
- `build_search_index_store` and `build_search_evidence_repository` return JSONL stores when their backend envs are unset.
- `docker-compose.yml` defaults to `jsonl` via `${SOURCE_INGEST_EVIDENCE_BACKEND:-jsonl}` etc.
- All 30 JSONL baseline tests pass without any Postgres env set.
- **PASS**

### AC-2: Postgres is explicit opt-in
- Backend selection is gated by `SOURCE_INGEST_EVIDENCE_BACKEND=postgres`, `SEARCH_INDEX_STORE_BACKEND=postgres`, `SEARCH_EVIDENCE_BACKEND=postgres`.
- Missing DSN fails closed in Postgres mode with a clear ValueError.
- Bootstrap DDL is documented in `POSTGRES_SOURCE_SEARCH_STORE_PILOT.md`.
- Rollback is env change back to unset/jsonl plus service restart.
- **PASS**

### AC-3: Source-ingest remains write owner
- `PostgresSourceEvidenceRepository` (source-ingest side) extends `InMemoryEvidenceRepository` and adds upsert on every write — write owner is source-ingest-svc.
- `PostgresReadOnlyEvidenceRepository` (search side) raises `EvidenceValidationError` on all four write methods: `add_source_record`, `add_evidence_item`, `add_bundle`, `add_knowledge_object`.
- Write boundary is enforced at the class level — not at the call site.
- Watermarks, DLQ, connector config, and audit log remain JSONL-only in this pilot; scope is clearly documented.
- **PASS**

### AC-4: Search reads durable evidence through owned boundary
- `SEARCH_EVIDENCE_TABLE` defaults to `source_ingest.source_evidence` (the source-ingest owned table).
- Search uses `PostgresReadOnlyEvidenceRepository` which only SELECTs.
- `PostgresSearchIndexStore` writes only `search_svc.search_index_snapshots` — no cross-writes into source evidence.
- **PASS**

### AC-5: Tests cover both paths
- 16 Postgres pilot tests (fake DB, no psycopg required) — all pass.
- 30 JSONL baseline tests — all pass.
- Tests cover: factory defaults, env-gated activation, write/reload round-trip, reload reference validation, read-only enforcement, invalid backend errors.
- **PASS**

## Additional Observations

- `_quote_pg` identifier validation prevents SQL injection in table names (both files).
- Fake-DB approach in tests is appropriate for a pilot — no live database dependency.
- `PostgresSearchIndexStore.path` and `PostgresReadOnlyEvidenceRepository.path` are set correctly so `str(store.path)` in health/status routes works.
- `store.reload()` and `durable_repository.reload()` are both implemented in the Postgres variants, preserving the interface contract with search/main.py.
- Schema ownership map in `POSTGRES_SOURCE_SEARCH_STORE_PILOT.md` aligns with `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`.

## Decision

**Approved.** All four acceptance criteria are met. Write boundary is enforced at the class level. JSONL default is unbroken. Returned to Codex2 for finalization.
