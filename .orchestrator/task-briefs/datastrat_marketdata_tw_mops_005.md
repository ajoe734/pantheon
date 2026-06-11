# Task Brief: DATASTRAT-MARKETDATA-TW-MOPS-005

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Complete MOPS scheduling and fundamentals normalization
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Turn MOPS route inventory into scheduled material-event, monthly-revenue, and financial-statement ingestion.

## Summary
MOPS already has route inventory, client helpers, and a source-ingest adapter for payload conversion. This task completes event/fundamental scheduling and normalized table output.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge

## Acceptance Criteria
- MOPS material events run for core, candidate, and archive symbols.
- MOPS monthly revenue and financial statements run for core symbols.
- Normalized outputs include `tw_material_event`, `tw_monthly_revenue`, `tw_financial_statement`, `tw_company_master`, and `tw_corporate_action` where supported by routes.
- Financial rows preserve fiscal year, quarter or month, announcement date, available time, company id, and raw route id.
- Restatement/correction routes are inventoried and represented in gap reports.
- Live public smoke for one material-event route passes through source-ingest health.

## Implementation Notes
- Existing client: `services/research/adapters/taiwan_market_client.py`.
- Existing adapter: `services/source_ingestion/connectors/taiwan_market.py`.
- Add route-specific normalizers instead of treating all MOPS rows as generic `body` text.
- Add fixtures for monthly revenue and financial statement routes.

## Relevant Canonical Files
- `services/research/adapters/taiwan_market_client.py`
- `services/source_ingestion/connectors/taiwan_market.py`
- `services/source_ingestion/tests/test_taiwan_market_connectors.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- MOPS is official reference truth for Taiwan disclosures.
- Keep polling courteous and bounded.
- Do not infer future availability dates.
