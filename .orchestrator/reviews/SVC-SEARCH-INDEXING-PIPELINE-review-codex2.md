# Review: SVC-SEARCH-INDEXING-PIPELINE

Reviewer: Codex2
Disposition: approved
Reviewed at: 2026-04-30

## Findings

No blocking findings.

## Acceptance Check

- Ingest completion can trigger incremental index refresh via `SEARCH_INGEST_NOTIFY_URL` and `/api/search/index/refresh`.
- Pipeline snapshots are schema-versioned as `index_pipeline_snapshot.v1` and retained through `JsonlIndexPipelineStore` pruning.
- Normal query path keeps request documents compatibility-gated and defaults to durable evidence-backed repository search.
- Freshness status is queryable via `/api/search/index/freshness`.
- Tests cover incremental additions, removed object tracking, freshness states, retention, HTTP endpoints, and source-ingest notification behavior.

## Verification

- `python3 -m pytest services/search/test_index_pipeline.py services/search/tests/test_http_service.py services/search/tests/test_materialized_index.py services/source_ingestion/tests/test_ingest_run.py`
- `python3 -m pytest services/source_ingestion/test_service.py services/source_ingestion/tests/test_scheduled_connector.py`
- `python3 -m py_compile services/search/index_pipeline.py services/search/main.py services/source_ingestion/main.py scripts/smoke_honest_stack.py`
- `docker compose config --quiet`
