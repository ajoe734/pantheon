# Evidence: LOOP-AUTO-SRC-002 - Source Provisioning Reconciler

Task: `LOOP-AUTO-SRC-002`
Owner: Codex
Reviewer: Copilot
Date: 2026-07-01

Historical implementation owner/reviewer: Codex2 / Claude.

## Delivered Surface

- Added `SourceProvisioningReconciler` in `services/source_ingestion/persona_source_reconciler.py`.
- Added `POST /api/source-ingest/persona-source-provisioning/reconcile`.
- Added non-mutating fetch config normalization on `JsonlConfiguredConnectorStore`.
- Added focused unit and API tests in `services/source_ingestion/tests/test_persona_source_reconciler.py`.

## Controller Contract

- Desired-state input: persona objects or dicts with `required_data_sources`.
- Actual-state observation: configured source connector store and connector schedule store.
- Authoritative writes: `JsonlConfiguredConnectorStore` and `JsonlConnectorScheduleStore`.
- Idempotency key: `persona_id + market + dataset + cadence + source_class + connector_candidates`.
- Conflict behavior: existing connector configs are not overwritten when the connector or fetch contract differs from the built-in provider plan.
- Duplicate policy: repeated reconcile ticks verify existing connector/schedule state and do not append duplicate JSONL records.
- Drift repair: a missing connector config or missing schedule is recreated on the next reconcile tick.
- Non-goal: scheduler supervision, missed-tick recovery, SourceHealth projection, and loop maturity promotion remain follow-up work.

## Acceptance Evidence

1. Adding a `tw_price_daily` requirement creates or verifies connector and schedule:
   - `test_tw_price_daily_requirement_creates_connector_and_schedule`
   - API smoke test verifies `tw-finmind-datasets` connector and daily schedule through source-ingest routes.

2. Duplicate ticks do not create duplicate connector or schedule records:
   - `test_duplicate_reconcile_tick_does_not_append_duplicate_records`
   - The test records JSONL line counts after the first tick and verifies they are unchanged after the second tick.

3. Missing connector or schedule is repaired on the next reconciliation:
   - `test_missing_schedule_is_repaired_on_next_reconcile`
   - `test_missing_connector_is_repaired_on_next_reconcile`

4. Seed-only requirements are not treated as live source binding proof:
   - `test_seed_only_requirement_is_not_provisioned`

## Verification

```bash
pytest -q services/source_ingestion/tests/test_persona_source_reconciler.py
```

Result: `7 passed in 7.11s`.

```bash
pytest -q services/source_ingestion/tests/test_persona_source_reconciler.py services/source_ingestion/tests/test_scheduled_connector.py services/source_ingestion/tests/test_connector_framework.py services/source_ingestion/tests/test_financial_source_catalog.py services/control-plane/persona/test_persona_data_sources.py
```

Result: `54 passed in 39.75s`.

Closeout revalidation after refreshing with `origin/dev` at `438d5d93`:
`54 passed in 19.77s`.

Fresh Codex revalidation after fast-forwarding `task/LOOP-AUTO-SRC-002` to
`origin/dev` at `5160b79b2`:
`54 passed in 14.37s`.

Codex revalidation PR: https://github.com/ajoe734/pantheon/pull/2671

## Maturity Boundary

This task does not raise `source_ingestion` maturity above `api-only`.
The reconciler and drift-repair tests are present, but scheduled worker
supervision, missed-tick visibility, and SourceHealth/BFF truth projection are
owned by `LOOP-AUTO-SRC-003` and `LOOP-AUTO-SRC-004`.
