# Task Brief: DATASTRAT-MARKETDATA-TW-TEJ-006

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Add TEJ paid historical gap-fill catalog and backfill pipeline
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Promote TEJ from implicit backup notes to explicit paid data-source entry and credential-aware backfill connector.

## Summary
TEJ should be used for historical backfill and research-grade supplement, especially broker/fundamental gaps. It must not replace MOPS official disclosure truth.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge
- DATASTRAT-MARKETDATA-TW-MOPS-005: todo · Official disclosure and fundamental baseline
- DATASTRAT-MARKETDATA-TW-FINMIND-004: todo · Low-cost FinMind layer

## Acceptance Criteria
- `financial_source_catalog.py` includes explicit `ds-tej-tw-research-backfill` entry.
- Config templates cover TEJ daily price/fundamentals and broker gap-fill candidates such as AMTOP1 and ABSR20 when licensed.
- `TEJ_API_KEY` is referenced only through secret ref.
- Without key, source health records credential unavailable.
- With key, table inventory and one small dataset smoke succeed.
- Backfill planner accepts dataset, date range, symbol universe, and entitlement metadata.
- TEJ raw and normalized rows include dataset code, table code, license scope, and point-in-time availability.

## Implementation Notes
- Existing adapter: `TejSourceIngestAdapter` in `services/source_ingestion/connectors/taiwan_market.py`.
- Existing client helpers: `fetch_tej_trial_table_catalog`, `fetch_tej_dataset`.
- Add purchased-table allowlist config so workers do not assume every TEJ table is available.
- Backfill should first fill gaps older than FinMind/Yahoo/public coverage, not run full-market forever.

## Relevant Canonical Files
- `services/source_ingestion/connectors/taiwan_market.py`
- `services/research/adapters/taiwan_market_client.py`
- `services/source_ingestion/financial_source_catalog.py`
- `services/data-plane/taiwan_reference.py`

## Working Rules
- No raw TEJ key in repo, logs, evidence, or docs.
- TEJ is research-grade and paid; track cost and entitlement.
- Do not overwrite official MOPS disclosure truth.
