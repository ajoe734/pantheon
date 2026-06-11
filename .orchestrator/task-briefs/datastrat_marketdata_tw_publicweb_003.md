# Task Brief: DATASTRAT-MARKETDATA-TW-PUBLICWEB-003

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Schedule Yahoo Taiwan RSS and broker top15 public-web connectors
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Admit existing Yahoo adapters into configured source-ingest schedules with active-universe fanout.

## Summary
The Yahoo Taiwan parsers already parse RSS metadata and broker top15 HTML. This task turns them into live scheduled connectors, scoped to active symbols, with health and gap-report evidence.

## Dependencies
- DATASTRAT-MARKETDATA-FOUNDATION-001: todo · Adapter dispatch, storage, health, and scheduler bridge

## Acceptance Criteria
- `tw-yahoo-stock-rss` is configured with a 10 to 30 minute cadence.
- `tw-yahoo-broker-top15` runs daily after close for core/candidate symbols only.
- RSS stores metadata and summary only by default; full text remains disabled unless an explicit license flag is added.
- Broker top15 rows land in `tw_broker_top` with 15 buy and 15 sell rows when available.
- Parse failures create degraded health and source errors.
- If FinMind broker data succeeds for the same symbol/date, Yahoo is marked as fallback/secondary source.
- Live smoke for `2330` broker page and RSS feed is recorded through source health.

## Implementation Notes
- Existing code: `services/source_ingestion/connectors/yahoo_taiwan.py`.
- Existing schema: `services/data-plane/schemas/tw_broker_top.schema.json`.
- Add scheduler tests that prove archive symbols are skipped for broker top and RSS detail.
- Deduplicate news by URL, title hash, timestamp, and provider.

## Relevant Canonical Files
- `services/source_ingestion/connectors/yahoo_taiwan.py`
- `services/source_ingestion/tests/test_yahoo_taiwan_connectors.py`
- `services/source_ingestion/active_universe.py`
- `services/data-plane/taiwan_reference.py`

## Working Rules
- Public-web access must be courteous and bounded.
- Yahoo is not official reference truth.
- Do not store raw credential material; no credential should be needed.
