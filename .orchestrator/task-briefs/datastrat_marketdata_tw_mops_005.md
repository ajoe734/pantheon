# Task Brief: DATASTRAT-MARKETDATA-TW-MOPS-005

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Complete MOPS scheduling and fundamentals normalization
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T01:24:27Z
- Next: Implementing MOPS route-specific normalization and update strategy.

## Summary
MOPS is the official-reference source for Taiwan material events, monthly
revenue, financial statements, company master, and supported corporate actions.
TEJ remains a vendor research backfill and must not replace MOPS as disclosure
truth. This task completes route-specific normalization plus event and daily
update strategy metadata.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage,
  health, and scheduler bridge.

## Acceptance Criteria
- MOPS material events run for core, candidate, and archive symbols.
- MOPS monthly revenue and financial statements run for core symbols.
- Normalized outputs include `tw_material_event`, `tw_monthly_revenue`,
  `tw_financial_statement`, `tw_company_master`, and `tw_corporate_action`
  where supported by routes.
- Financial rows preserve fiscal year, quarter or month, announcement date,
  available time, company id, and raw route id.
- Restatement/correction routes are inventoried and represented in gap reports.
- Live public smoke for one material-event route passes through existing
  market-data/source-ingest smoke evidence.

## Implementation Notes
- Existing client: `services/research/adapters/taiwan_market_client.py`.
- Existing adapter: `services/source_ingestion/connectors/taiwan_market.py`.
- Add route-specific normalizers instead of treating all MOPS rows as generic
  `body` text.
- Add focused fixtures for monthly revenue and financial statement routes.

## Relevant Files
- `services/research/adapters/taiwan_market_client.py`
- `services/source_ingestion/connectors/taiwan_market.py`
- `services/source_ingestion/active_universe.py`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/tests/test_taiwan_market_connectors.py`
- `services/source_ingestion/tests/test_active_universe.py`
- `services/source_ingestion/tests/test_financial_source_catalog.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- MOPS is official reference truth for Taiwan disclosures.
- Keep polling courteous and bounded.
- Do not infer future availability dates.
