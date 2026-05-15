# Review: SVC-SOURCE-SEARCH-CRAWLER-INDEXER-WAVE2

Reviewer: Claude
Date: 2026-05-04
Status: approved

## Acceptance Criteria Review

### AC1: Connector registry supports bounded crawler adapters with allowlists and rate limits

**PASS**

- `services/source_ingestion/policy_registry.py`: `crawler_policy_for_connector()` projects each connector with `bounded: True` (line 75), `allowlist_enforced: fetch_mode == "external_feed"` (line 77), `allowed_url_hosts` extracted from allowed_url_prefixes (lines 44-51), and full rate/license guards at lines 86-97.
- `policy_registry_payload()` sets `bounded_crawler_adapters_only: True` at the registry level (line 128).
- `test_scheduled_connector.py` `test_policy_registry_reports_crawler_guards_and_rate_limits()` validates `adapter_type == "bounded_external_feed_crawler"`, `allowlist_enforced: True`, `allowed_url_hosts`, rate limit and license guards.

### AC2: Index refresh is scheduled and materialized without query-time request documents

**PASS**

- `services/search/scheduler_worker.py`: `run_tick()` POSTs to `/api/search/index/refresh` with `triggered_by: "scheduled_refresh"` and optionally to `/api/search/index/materialize`. Default `SEARCH_INDEX_SCHEDULER_MATERIALIZE=true`.
- `test_index_scheduler_worker.py` validates both endpoints are called in order and materialize returns `materialized_at`.
- `policy_registry.py` indexer block: `materialized_index_required: True`, `normal_search_path: "durable_index"`, `request_documents_compat: False` — confirms no query-time request documents in normal path.
- `test_service_activation_contract.py` line 33-37 confirms `search-index-scheduler` compose service wires `SEARCH_API_URL` and `SEARCH_INDEX_SCHEDULER_MATERIALIZE`.

### AC3: Search durable-only mode has staging/prod cutoff tests

**PASS**

- `services/search/tests/test_retrieval_rank_filter_cutoff_contract.py` has four `durable_index_only` tests: rejects request-documents even with compat flag, rejects compat route with documents, allows query without documents, health reports flag.
- `test_service_activation_contract.py` line 60: asserts `SEARCH_DURABLE_INDEX_ONLY=true` is present in `env/prod-control.env.example`.
- `test_search_refresh.py` validates `SEARCH_DURABLE_INDEX_ONLY must be true` for refresh activation.

### AC4: BFF exposes crawler/indexer health and freshness without direct documents compatibility as normal path

**PASS**

- `services/control-plane/bff/main.py`: `GET /api/v1/operator/source/ops` and `GET /api/v1/operator/search/ops` endpoints go through service clients only; comment at line 8708 states "The BFF never reads source volumes directly".
- `read_store.py` `get_source_ops_snapshot()` fetches `/api/source-ingest/registry` and extracts freshness, policy_registry summary, scheduled/due/degraded connector counts.
- `get_search_ops_snapshot()` fetches `/api/search/index/freshness`, `/api/search/index/pipeline-runs`, and `/api/search/index/materialize` — exposes freshness_ok / freshness_status without any request-documents path.

## Summary

All four acceptance criteria met. Implementation covers:
- Bounded policy registry with per-connector crawler/indexer projections
- Scheduled search index refresh/materialize worker in compose
- Durable-only search cutoff tested at staging and prod posture levels
- BFF freshness/health surfaces via service clients with no direct document reads

No blocking issues. Returning to Codex for closeout finalization.
