# Task Brief: DATASTRAT-MARKETDATA-TW-FINMIND-004

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Enable FinMind Taiwan API and SponsorPro backfill pipeline
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Convert existing FinMind adapters from payload parsers to credential-aware scheduled fetchers.

## Summary
FinMind is the low-cost normalized Taiwan research layer. Implement real API fetch, token readiness, active-universe scheduling, broker top20 normalization, and bulk backfill manifests.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge
- DATASTRAT-MARKETDATA-TW-PUBLICWEB-003: todo · Yahoo fallback for broker top15

## Acceptance Criteria
- `FINMIND_API_TOKEN` is used only through `env://FINMIND_API_TOKEN` or equivalent secret ref.
- Without token, source health records `credential_unavailable` and scheduler does not silently pass.
- With token, a one-symbol smoke succeeds for `TaiwanStockPrice`, one chip dataset, `TaiwanStockNews`, and `TaiwanStockTradingDailyReport`.
- `TaiwanStockTradingDailyReport` normalizes to `tw_broker_top` top20 rows.
- Core/candidate symbols get FinMind detail; archive symbols do not get broker/news/detail fanout.
- SponsorPro storage-object backfill stores raw object manifests and repair jobs, not large payloads in source evidence.
- Quota/rate-limit metadata is captured when provider returns it.

## Implementation Notes
- Existing code: `services/source_ingestion/connectors/finmind_taiwan.py`.
- Add HTTP fetch methods for `/data`, `/taiwan_stock_trading_daily_report`, and `/storage_objects`.
- Use bounded date ranges and symbol batches.
- Add tests for credential-missing health, token redaction, and active-universe fanout.

## Relevant Canonical Files
- `services/source_ingestion/connectors/finmind_taiwan.py`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/data-plane/taiwan_reference.py`

## Working Rules
- No inline token in docs, tests, source evidence, or logs.
- Treat FinMind as research-grade, not official disclosure truth.
- Keep heavy broker detail active/candidate only.
