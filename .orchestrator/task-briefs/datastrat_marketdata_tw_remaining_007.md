# Task Brief: DATASTRAT-MARKETDATA-TW-REMAINING-007

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Implement remaining Taiwan public sources: TDCC, TAIFEX, and Anue RSS
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Add missing Taiwan chip/news public-source adapters and schemas.

## Summary
Close the Taiwan 0% gaps: TDCC weekly shareholding distribution, TAIFEX futures/options chip data, and Anue RSS metadata fallback.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge

## Acceptance Criteria
- Catalog entries and config templates exist for TDCC, TAIFEX, and Anue RSS.
- TDCC adapter fetches one symbol/week and normalizes holder level, people count, shares, and percentage.
- TAIFEX adapter fetches daily futures/options chip data and normalizes contract, date, participant group, volume, and open interest.
- Anue RSS parser stores metadata/summary only by default and dedupes with Yahoo/MOPS.
- TDCC staleness thresholds are weekly; TAIFEX is daily after publication; Anue is 10 to 30 minutes.
- Source health records are written for all enabled connectors.

## Implementation Notes
- Add connector modules under `services/source_ingestion/connectors`.
- Add data-plane helper/schemas for `tdcc_shareholding_distribution`, `taifex_futures_chip`, `taifex_options_chip`, and Anue news metadata if existing news schema is insufficient.
- Use active-universe tiers: TDCC core/candidate, TAIFEX global contract context, Anue core/candidate symbol/news context.

## Relevant Canonical Files
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/data-plane/taiwan_reference.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- Public sources must be read-only and courteous.
- Do not scrape full articles unless licensing is explicitly configured.
- No broker or order side effects.
