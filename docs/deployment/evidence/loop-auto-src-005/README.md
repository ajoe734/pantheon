# LOOP-AUTO-SRC-005 Evidence

Task: Connect source completion to search index refresh truth.

## Delivered Behavior

- Source ingest completion now calls search via
  `POST /api/search/index/source-completions`.
- Search records an ingest-completion pipeline refresh, materializes an index
  snapshot, and exposes replay truth via
  `GET /api/search/index/source-completions/{ingest_run_id}`.
- Source-ingest persists a `SearchIndexRefreshObserved` run event and returns
  `source_search_refresh` in job responses.
- Compose makes `SEARCH_MATERIALIZE_STORE_PATH` explicit and keeps
  `SEARCH_INGEST_NOTIFY_URL` on the source-ingest service, so the event-driven
  path is independent of the optional `search-index-scheduler` profile.
- Existing BFF search ops can report this truth through search freshness,
  pipeline runs, and materialized index readback over `PANTHEON_SEARCH_API_URL`.

## Verification

Run on 2026-06-27 from `task/LOOP-AUTO-SRC-005`:

```bash
python3 -m py_compile services/search/main.py services/source_ingestion/main.py scripts/smoke_source_search_bounded.py
python3 -m pytest services/search/tests/test_search_refresh.py services/search/test_index_pipeline.py services/source_ingestion/test_service.py services/search/tests/test_service_activation_contract.py services/source_ingestion/test_compose_activation.py -q
POSTGRES_PORT=25432 SOURCE_INGEST_PORT=28097 SEARCH_PORT=28098 docker compose --profile source-search-bounded run --rm --build --use-aliases source-search-bounded-smoke
```

Results:

- py_compile passed.
- Focused pytest passed: `52 passed in 18.35s`.
- First compose smoke attempt with default ports built images but failed because
  host port `15432` was already allocated.
- Retried with alternate host ports (`POSTGRES_PORT=25432`,
  `SOURCE_INGEST_PORT=28097`, `SEARCH_PORT=28098`) and `--build`; bounded
  source/search smoke passed, including source-completion refresh/materialization
  truth replay.
