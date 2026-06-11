# Task Brief: DATASTRAT-MARKETDATA-FOUNDATION-001

This file is generated for task-scoped execution context.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: Build market-data ingest foundation for provider adapters, storage, health, and gap reports
- Status: todo
- Owner: Auto Worker
- Reviewer: Codex
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11T00:00:00Z
- Next: Implement the cross-cutting runtime bridge before source-specific workers enable schedules.

## Summary
Turn provider-owned adapter templates into runnable source-ingest jobs. Add the bounded adapter dispatcher, storage writers, source health auto-updates, active-universe scheduler integration, and weekly gap report shell required by all US/TW market-data sources.

## Dependencies
- DATASTRAT-CATALOG-003: done · Financial data-source catalog and active-universe policy
- DATASTRAT-USAGE-007: done · Source health, usage, and retirement recommendations

## Acceptance Criteria
- `provider_owned_adapter` fetch configs are dispatchable through an allowlisted adapter registry, not through arbitrary import strings.
- Connector runs can write raw storage references, normalized row counts, and feature output references.
- Successful jobs upsert `SourceHealth` with `last_success_at`, `latest_watermark`, row counts, rejected counts, schema hash, staleness, and quota/error metadata.
- Failed jobs update `last_failure_at` and do not report success with zero rows unless the provider explicitly has no new data.
- Scheduler can call `build_active_universe_update_plan` and fan out jobs by connector, dataset, date, and bounded symbol batches.
- Weekly gap report command exists and classifies gaps by credential, quota, provider stale, schema, parse, and not-in-universe.

## Implementation Notes
- Primary files: `services/source_ingestion/configured.py`, `services/source_ingestion/scheduler.py`, `services/source_ingestion/main.py`, `services/source_ingestion/source_health.py`.
- Add tests in `services/source_ingestion/tests/` that exercise a fake provider-owned adapter end to end through scheduled run.
- Keep bulk market data out of `source_evidence.jsonl`; use raw object references in evidence metadata.
- Reject inline API keys; only secret refs or runtime env resolution may be used.

## Relevant Canonical Files
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/source_ingestion/source_health.py`

## Working Rules
- No live broker or capital side effects.
- Keep adapters read-only.
- Stage only intentional files and run focused tests before PR.
