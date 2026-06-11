# Task Brief: DATASTRAT-MARKETDATA-TW-OFFICIAL-002

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Implement TWSE/TPEx official market-data adapter and scheduler path
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Build `TaiwanOfficialMarketDatasetAdapter` and enable official daily price/chip summaries.

## Summary
Make TWSE/TPEx official public data a real source-ingest connector. Cover daily price baseline for all active-universe tiers and official chip summaries for core/candidate symbols.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge

## Acceptance Criteria
- `TaiwanOfficialMarketDatasetAdapter` exists under `services/source_ingestion/connectors`.
- Public TWSE daily price and TPEx daily close quote endpoints fetch through source-ingest.
- Official chip summary endpoints are inventoried and implemented where public APIs exist.
- Normalized rows emit `tw_price_daily`, `tw_institutional_flow`, `tw_margin_short_balance`, `tw_securities_lending`, and `tw_day_trading` as supported.
- Archive symbols receive daily price baseline only; core/candidate receive price plus chip summaries.
- Live read-only smoke passes for one TWSE symbol and one TPEx symbol.
- Source health and watermark are written for `tw-twse-tpex-official-market`.

## Implementation Notes
- Extend `services/source_ingestion/connectors/taiwan_market.py` or add `taiwan_official.py`.
- Reuse `services/research/adapters/taiwan_market_client.py` source specs when practical.
- Add data-plane helpers/schemas only for row types not yet represented.
- Do not model TWSE/TPEx purchased branch history in this task; that remains optional paid gap fill.

## Relevant Canonical Files
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/data-plane/taiwan_reference.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- Respect public endpoint rate limits.
- Store raw payload references and normalized row counts.
- No inline credentials are needed for official public endpoints.
