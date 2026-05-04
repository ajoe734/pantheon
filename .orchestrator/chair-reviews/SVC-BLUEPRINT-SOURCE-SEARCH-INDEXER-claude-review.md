# Review: SVC-BLUEPRINT-SOURCE-SEARCH-INDEXER

**Reviewer:** Claude
**Task:** Upgrade source/search into bounded autonomous connector and indexer platform
**Date:** 2026-05-04
**Decision:** APPROVED

---

## Acceptance Criteria Assessment

### 1. connector schedule/run/freshness 透過 API/BFF 可見 ✅

- `GET /api/source-ingest/registry` returns per-connector entries with `freshness`, `schedule`, and `fetch_policy` fields, schema-versioned as `source_connector_registry.v1`.
- Freshness computation in `_connector_freshness_summary()` correctly derives: `unscheduled`, `disabled`, `never_ingested`, `fresh`, `due`, `degraded` states from watermark + schedule config.
- `PUT/GET /api/source-ingest/connectors/{id}/schedule` enable schedule configuration and retrieval.
- BFF `get_source_ops_snapshot()` aggregates connector health, runs, DLQ, frontier, and audit from service HTTP API — no direct volume reads.
- BFF `get_search_ops_snapshot()` aggregates index freshness, pipeline runs, and materialized index from search service API.

### 2. durable index refresh 可重跑且有 evidence ✅

- `IncrementalIndexPipeline.run()` supports both incremental (new/updated objects since last run) and full rebuild; result recorded in `JsonlIndexPipelineStore`.
- `POST /api/search/index/refresh` triggers the pipeline; `GET /api/search/index/pipeline-runs` lists history with retention bounds.
- `POST /api/search/index/materialize` and `GET /api/search/index/materialize` persist and retrieve durable materialized index state.
- Freshness SLA is tracked and exposed through `GET /api/search/index/freshness`.

### 3. request-document compatibility 不再是 staging normal path ✅

- Normal `POST /api/search/query` rejects request-document usage unless `allow_request_documents_compat=true` is explicitly set.
- `SEARCH_DURABLE_INDEX_ONLY=true` blocks all request-document paths unconditionally.
- Separate explicit compat endpoint `/api/search/query/request-documents-compat` carries `Deprecation: true` response header.
- Production posture validation enforces `SEARCH_INDEX_STORE_BACKEND=postgres`, `SEARCH_DURABLE_INDEX_ONLY=true`.

### 4. tests 通過 ✅

- `test_scheduled_connector.py`: covers schedule CRUD, durability across reload, bounded concurrency, skipping disabled/non-due connectors, DLQ replay durability, frontier retry backoff, freshness reporting.
- `test_search_refresh.py`: covers pipeline snapshot recording, freshness SLA before/after refresh, production posture fail-closed check.
- `test_source_search_ops_bff.py`: covers BFF ops read surfaces (normal/degraded/unavailable/stale), auth guard, role check, idempotency key requirement, service forwarding.

---

## Code Quality Notes

- `_notify_search_index_refresh()` fire-and-forget is appropriate; swallowing errors is correct for eventual-consistency coupling.
- `JsonlIndexPipelineStore.prune()` is O(n) file rewrite, acceptable for bounded retention runs.
- `ReadSurfaceStore.get_source_ops_snapshot()` and `get_search_ops_snapshot()` have appropriate degradation semantics: missing URL → `"missing"`, all calls fail → `"unavailable"`.
- `source_search_ops_client.py` correctly enforces idempotency key on all write commands.
- Schema versions (`source_connector_registry.v1`, `source_connector_freshness.v1`, `index_pipeline_snapshot.v1`) make responses forward-compatible.

---

## Summary

The bounded autonomous connector and indexer platform is properly implemented. All acceptance criteria met. 56 focused tests pass. Implementation scope is appropriate — this is not an unrestricted crawler. Approved for finalization.
