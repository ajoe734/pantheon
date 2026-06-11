# Task Brief: DATASTRAT-MARKETDATA-US-PUBLIC-008

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Implement US public data sources: SEC EDGAR, FRED, FINRA, and public daily OHLCV fallback
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T03:59:30Z
- Next: Owner closeout evidence recorded; finalize to done after the task-scoped closeout commit is merged to dev.

## Summary
SEC EDGAR and FRED were catalog templates, while FINRA and public daily OHLCV
fallback were missing. Implement real adapters, schedules, normalized schemas,
and source health for public US research data.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: merged - Adapter dispatch, storage,
  health, and scheduler bridge.

## Acceptance Criteria
- `SecEdgarFilingAdapter` fetches submissions and company facts using
  configured User-Agent/contact identity.
- SEC symbol to CIK mapping is maintained and filings normalize into
  `sec_filing_event` and `sec_company_fact`.
- `FredMacroSeriesAdapter` supports optional API key and public CSV fallback for
  configured series.
- FRED watermarks and staleness thresholds are per series, not per symbol.
- `FinraShortSaleAdapter` fetches daily short-volume files and normalizes
  `us_short_volume_daily`.
- US daily OHLCV has at least one working public fallback or is explicitly
  marked disabled with a health reason if Stooq remains unavailable.
- Source health is written for SEC, FRED, FINRA, and the daily OHLCV provider.

## Review Approval
- Reviewer: Claude2
- Result: approved
- Review summary: SEC EDGAR, FRED, and FINRA adapters are implemented with
  appropriate rate limits, schema validation, and license scopes. Stooq remains
  explicitly disabled pending endpoint verification.
- Review verification: `python3 -m pytest services/source_ingestion/tests -q`
  passed with 273 tests and 1 skipped test; `python3 -m pytest
  services/data-plane/tests/test_data_plane_schemas.py -q` passed with 56
  tests.

## Owner Closeout Evidence
- Implementation PR #1304 merged into `dev` at
  `bf860bcb977a8caf98f2b1d7dac760631206b085`.
- Implementation commit
  `f019a754f433be2c9a01e43f3130c141207a728c` carries the required task
  trailers and original verification record.
- GitHub PR checks on #1304 passed: Commit trailers, Runtime mirror guard,
  Smoke acceptance, and Forward to orchestrator.
- Owner reran focused verification on 2026-06-11:
  - `python3 -m pytest services/source_ingestion/tests -q` passed with 273
    tests and 1 skipped test.
  - `python3 -m pytest services/data-plane/tests/test_data_plane_schemas.py -q`
    passed with 56 tests.

## Implementation Notes
- Added modules under `services/source_ingestion/connectors`.
- Added data-plane helpers/schemas for `us_price_daily`, `sec_filing_event`,
  `sec_company_fact`, `macro_fred_observation`, and `us_short_volume_daily`.
- Stooq smoke returned 404 from this VM on 2026-06-11; Stooq remains disabled
  until a working endpoint is verified.
- FINRA current-day files may publish with delay; expected publication windows
  are modeled before marking stale.

## Relevant Canonical Files
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/data-plane/us_equity_reference.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- SEC requests must include a real configured User-Agent/contact string.
- FRED is global macro context and should not fan out by symbol.
- Public endpoints must be read-only and rate-limited.
