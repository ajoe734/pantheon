# Task Brief: DATASTRAT-MARKETDATA-OPS-ACCEPT-010

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Admit market-data connectors to runtime and prove end-to-end acceptance
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Configure schedules, credentials/readbacks, dashboard metrics, and final gap report after source-specific adapters merge.

## Summary
The source-specific tasks are not complete until the live runtime actually schedules them, writes source health, and produces a gap report. This task owns runtime admission and final acceptance evidence.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge
- DATASTRAT-MARKETDATA-TW-OFFICIAL-002: todo · TWSE/TPEx official market data
- DATASTRAT-MARKETDATA-TW-PUBLICWEB-003: todo · Yahoo RSS and broker top15
- DATASTRAT-MARKETDATA-TW-FINMIND-004: todo · FinMind API/backfill
- DATASTRAT-MARKETDATA-TW-MOPS-005: todo · MOPS disclosures/fundamentals
- DATASTRAT-MARKETDATA-TW-TEJ-006: todo · TEJ backfill
- DATASTRAT-MARKETDATA-TW-REMAINING-007: todo · TDCC, TAIFEX, Anue
- DATASTRAT-MARKETDATA-US-PUBLIC-008: todo · SEC, FRED, FINRA, public OHLCV
- DATASTRAT-MARKETDATA-US-PAID-BROKER-009: todo · Polygon/Massive, Alpha Vantage, broker readback

## Acceptance Criteria
- `/api/source-ingest/connectors` lists configured market-data connectors for every planned source, with enabled or explicitly disabled lifecycle.
- `/api/source-ingest/health` contains records for every enabled data source.
- Public sources have fresh read-ok runtime evidence.
- Paid sources without credentials have explicit credential-unavailable health.
- At least one Taiwan core symbol has scheduler-produced price, MOPS event, Yahoo broker top, Yahoo RSS/news metadata, and one chip/fundamental path.
- At least one US core symbol has scheduler-produced daily price, SEC filing, FRED macro context, and FINRA short-volume data.
- Heavy detail connectors skip archive symbols.
- Weekly gap report is generated and classifies missing data.
- Operator dashboard or BFF snapshot exposes connector health, usage, staleness, and gap summary.

## Implementation Notes
- Runtime currently has source-ingest on `127.0.0.1:18097`, but only bounded/internal smoke connectors are configured as of 2026-06-11.
- Use source-ingest APIs to configure connectors and schedules rather than editing runtime JSONL by hand.
- Store runtime evidence outside repo unless it is a bounded audit artifact.
- Add nonprod deployment notes only after PRs merge to `dev`.

## Relevant Canonical Files
- `services/source_ingestion/main.py`
- `services/source_ingestion/scheduler_worker.py`
- `services/source_ingestion/source_health.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- Runtime repair without a merged repo change is not completion.
- No raw secrets in evidence.
- Final closeout must report PRs, merge SHAs, tests, live health, and gap-report path.
