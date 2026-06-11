# Task Brief: DATASTRAT-MARKETDATA-US-PAID-BROKER-009

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Implement paid US data providers and broker quote readback boundaries
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Add Polygon/Massive and Alpha Vantage adapters, while keeping IBKR/Shioaji as read-only broker readback evidence.

## Summary
Paid/broker sources must be explicit, credential-safe, and bounded. Polygon/Massive is the preferred US research-grade paid OHLCV source. Alpha Vantage is optional low-throughput fallback. IBKR and Shioaji stay on the broker-execution readback boundary.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge
- DATASTRAT-MARKETDATA-US-PUBLIC-008: todo · Public US baseline sources

## Acceptance Criteria
- Catalog entries/templates exist for Polygon/Massive and optional Alpha Vantage.
- `PolygonUsEquityDailyAdapter` fetches daily aggregate OHLCV through secret ref only.
- Without key, source health records credential unavailable.
- With key, one-symbol daily aggregate smoke writes raw ref, normalized `us_price_daily`, and health.
- Alpha Vantage is disabled by default unless key and low-throughput schedule are configured.
- IBKR quote readback can be admitted as read-only evidence but never as default research history.
- Shioaji quote readback can be admitted as read-only Taiwan execution-sync evidence but not as research primary source.

## Implementation Notes
- Existing smoke script: `scripts/run_marketdata_credential_smoke.py`.
- Existing data-plane helper: `services/data-plane/us_equity_reference.py`.
- Add quote readback ingestion from `IBKR_QUOTE_READBACK_JSON` and `SHIOAJI_QUOTE_READBACK_JSON` if needed for health evidence.
- Capture quota/rate-limit headers when providers return them.

## Relevant Canonical Files
- `scripts/run_marketdata_credential_smoke.py`
- `services/data-plane/us_equity_reference.py`
- `services/broker/shioaji`
- `services/execution`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`

## Working Rules
- Never call order placement, cancellation, or capital mutation paths.
- No raw credential material in repo, source evidence, or logs.
- Paid providers must track entitlement, quota, and cost.
