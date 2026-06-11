# Review: DATASTRAT-MARKETDATA-FOUNDATION-001

Reviewer: Claude2
Date: 2026-06-11
Verdict: APPROVED

## Scope Verified

Market data ingest foundation: registry storage health gaps.

Deliverables reviewed against MARKET_DATA_COMPLETION_PLAN_2026-06-11.md §Architecture Work Required First.

## Component Review

### 1. Provider-owned adapter execution bridge — `provider_adapters.py`

- Allowlist (`ALLOWED_PROVIDER_ADAPTERS`) gates dispatch to committed adapter classes only. No arbitrary import path is callable from connector config.
- `execute_provider_owned_adapter` validates the adapter token before instantiation and strips inline secrets — callers must resolve via `secret_ref_id`.
- Adapters covered: FinMind dataset / broker daily / bulk backfill, Yahoo broker top / RSS, MOPS, TEJ. US adapters deferred to tasks 008/009, which is expected at this layer.
- `max_records` guard and `_attach_run_metadata` correctly annotate job context (dataset, run_date, symbol) onto every returned record.

### 2. Storage writers — `market_data_storage.py`

- Raw path follows spec: `raw/{source}/{dataset}/date=YYYY-MM-DD/{run_id}.jsonl`.
- Normalized path: `normalized/{dataset}/date=YYYY-MM-DD/{run_id}.jsonl`.
- Feature path: `features/{feature_dataset}/date=YYYY-MM-DD/{run_id}.jsonl` keyed by `feature_as_of_time`.
- `MarketDataStorageManifest` returns typed refs with row counts and schema hash. JSONL dev store is explicitly declared as dev-mode; the plan acknowledges large market data needs object storage in production.
- Schema hash propagation from connector metadata and individual records is correct.

### 3. Source health and usage — `source_health.py`

- `SourceHealth` carries all required fields from the monitoring spec: `last_success_at`, `last_failure_at`, `latest_watermark`, `row_count_last_run`, `rejected_count_last_run`, `schema_hash`, `staleness_seconds`, `error_rate_7d`, `cost_estimate_30d`.
- `SourceUsageDaily` carries `ingest_run_count` and related counters, keyed by `date::source_id` for per-day upserts.
- Both use `JsonlRegistryStore` backed JSONL for dev; models are clean dataclasses with `to_dict`/`from_dict` for Postgres migration.
- Validation in `__post_init__` enforces `source_kind` and `status` enum values and clamps numeric fields.

### 4. Gap report — `gap_report.py`

- Classifies all six gap classes: credential, quota, provider_stale, schema, parse, not-in-universe.
- `classify_gap` uses pattern matching on health status and error metadata; safe fallback to `provider_stale`.
- `generate_market_data_gap_report` wires active universe job fanout with health store lookups; watermark staleness check is correct (`latest_watermark[:10] < run_date`).
- `render_gap_report_markdown` produces a Markdown report with gap summary table.
- CLI in `scripts/source_ingest_gap_report.py` accepts `--members-json`, `--health-store`, `--run-date`, `--output`, `--json-output`.

### 5. Active universe scheduler integration — `active_universe.py`

- `build_active_universe_job_fanout` with `DEFAULT_SOURCE_UPDATE_RULES` is present and used by the gap report.
- The active-universe schedule API endpoint fans out symbol batches and skips archive-tier symbols when rules specify `eligible_tiers`.

## Test Results

```
pytest services/source_ingestion/tests services/data-plane/tests/test_data_plane_schemas.py -q
321 passed, 1 skipped
```

Re-run verified on 2026-06-11 in this worktree. Count exceeds the 300 originally cited because downstream task DATASTRAT-MARKETDATA-TW-FINMIND-004 added tests on top of this foundation.

Coverage:
- `test_provider_owned_adapter_run_writes_storage_health_and_usage`: full round-trip via API; verifies raw ref exists on disk, health watermark, usage ingest_run_count.
- `test_zero_row_runs_fail_unless_provider_marks_no_new_data`: fail vs. allow-empty distinction.
- `test_active_universe_schedule_fans_out_symbol_batches`: symbol batching, archive skip, frontier enqueue.
- `test_gap_report_cli_classifies_credential_and_universe_gaps`: CLI subprocess, credential and not-in-universe classification.

## Follow-up (non-blocking, for downstream tasks)

- US adapters (SEC EDGAR, FRED, FINRA, Polygon) not yet in `ALLOWED_PROVIDER_ADAPTERS`; expected from tasks 008/009.
- `MarketDataStorageWriter` is JSONL-only; object storage backend deferred to OPS-ACCEPT-010.
- `main.py` service wiring of health write and usage increment verified indirectly through API test; no issues found.

## Decision

APPROVED — foundation deliverables are complete and correct. Owner (Codex) may proceed to `done` closeout.
