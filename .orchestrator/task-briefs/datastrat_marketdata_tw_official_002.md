# Task Brief: DATASTRAT-MARKETDATA-TW-OFFICIAL-002

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Implement TWSE/TPEx official market-data adapter and scheduler path
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T01:37:39Z
- Next: Review approved; owner closeout pending.

## Summary
Make TWSE/TPEx official public data a real source-ingest connector. Cover daily
price baseline for all active-universe tiers and official chip summaries for
core/candidate symbols.

The worker dispatch summary mentioned TDCC and TAIFEX as Taiwan official public
sources. The canonical market-data completion plan keeps TDCC holdings and
TAIFEX futures/options chip in `DATASTRAT-MARKETDATA-TW-REMAINING-007`; this
task inventories those follow-up surfaces but does not claim their fetch path.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: foundation scheduler and adapter bridge available.

## Acceptance Criteria
- `TaiwanOfficialMarketDatasetAdapter` exists under `services/source_ingestion/connectors`.
- Public TWSE daily price and TPEx daily close quote endpoints fetch through source-ingest.
- Official chip summary endpoints are inventoried and implemented where public APIs exist.
- Normalized rows emit `tw_price_daily`, `tw_institutional_flow`, `tw_margin_short_balance`, `tw_securities_lending`, and `tw_day_trading` as supported.
- Archive symbols receive daily price baseline only; core/candidate receive price plus chip summaries.
- Live read-only smoke is available for one TWSE symbol and one TPEx symbol, gated by `PANTHEON_TW_OFFICIAL_LIVE_SMOKE=1`.
- Source health and watermark are written for `tw-twse-tpex-official-market`.

## Implementation Notes
- Implemented as `services/source_ingestion/connectors/taiwan_official.py`.
- Registered in `services/source_ingestion/connectors/__init__.py` and connector examples.
- Reuses source-ingestion adapter conventions for rows, watermarks, and health.
- Does not model TWSE/TPEx purchased branch history; that remains optional paid gap fill.

## Relevant Canonical Files
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/data-plane/taiwan_reference.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- Respect public endpoint rate limits.
- Store raw payload references and normalized row counts.
- No inline credentials are needed for official public endpoints.

## Review And Closeout Evidence
- Implementation PR: https://github.com/ajoe734/pantheon/pull/1299
- Merge commit: `a792347d3ab149bc180c61652cc7a654f2b205cd`
- Task commit: `26a970c5`
- Reviewer: Claude2
- Review artifact: `.orchestrator/task-reviews/datastrat_marketdata_tw_official_002_review.md`
- Reviewer verdict: approved; all implemented datasets pass, watermark and tier policy verified.
- Follow-up scope: TDCC weekly holdings and TAIFEX futures/options chip remain deferred to `DATASTRAT-MARKETDATA-TW-REMAINING-007`.
