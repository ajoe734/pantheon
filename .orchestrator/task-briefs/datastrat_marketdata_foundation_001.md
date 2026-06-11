# Task Brief: DATASTRAT-MARKETDATA-FOUNDATION-001

This file is task-scoped execution context. Treat `ai-status.json` as durable
task state only when explicitly updating state. Do not read `current-work.md` by
default for implementation context.

## Task
- Title: Build market-data ingest foundation for provider adapters, storage, health, and gap reports
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Phase: DATASTRAT market-data completion
- Last update: 2026-06-11
- Next: Review approved; owner is performing closeout finalization.

## Summary
Build the shared US/Taiwan market-data ingest foundation: allowlisted
provider-owned adapter dispatch, raw/normalized/features storage refs, source
health metrics, weekly gap report shell, and active-universe scheduler fanout.
This task does not implement source-specific live HTTP adapters or install
credentials; those compose through the follow-up market-data source tasks.

## Dependencies
- DATASTRAT-CATALOG-003: done - Financial data-source catalog and active-universe policy
- DATASTRAT-USAGE-007: done - Source health, usage, and retirement recommendations
- DATASTRAT-MARKETDATA-PLAN-001: done - Market data completion plan

## Acceptance Criteria
- `provider_owned_adapter` fetch configs are dispatchable through an allowlisted adapter registry, not arbitrary import strings.
- Connector runs can write raw storage references, normalized row counts, and feature output references.
- Successful jobs upsert `SourceHealth` with `last_success_at`, `latest_watermark`, row counts, rejected counts, schema hash, staleness, and quota/error metadata.
- Failed jobs update `last_failure_at` and do not report success with zero rows unless the provider explicitly has no new data.
- Scheduler can call `build_active_universe_update_plan` and fan out jobs by connector, dataset, date, and bounded symbol batches.
- Weekly gap report command exists and classifies gaps by credential, quota, provider stale, schema, parse, and not-in-universe.

## Implementation Notes
- Primary files: `services/source_ingestion/configured.py`, `services/source_ingestion/scheduler.py`, `services/source_ingestion/main.py`, `services/source_ingestion/source_health.py`.
- New foundation modules may live under `services/source_ingestion/` when they are shared by all market-data sources.
- Add tests in `services/source_ingestion/tests/` that exercise a fake provider-owned adapter end to end through scheduled or manual run paths.
- Keep bulk market data out of `source_evidence.jsonl`; use raw object references in evidence metadata.
- Reject inline API keys; only secret refs or runtime env resolution may be used.

## Relevant Canonical Files
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/active_universe.py`
- `services/source_ingestion/source_health.py`

## Review And Closeout
- Implementation PR: https://github.com/ajoe734/pantheon/pull/1300
- Merge commit: `34050b1f72786ce2c0af79d24e418968eaf8a6bc`
- Reviewer artifact: `docs/04/pantheon_data_strategy_source_design_2026-06-09/DATASTRAT-MARKETDATA-FOUNDATION-001-review.md`
- Reviewer decision: APPROVED by Claude2 on 2026-06-11.
- Reviewer verification: `pytest services/source_ingestion/tests services/data-plane/tests/test_data_plane_schemas.py -q` -> 321 passed, 1 skipped.

## Working Rules
- No live broker or capital side effects.
- Keep adapters read-only.
- Stage only intentional files and run focused tests before PR.
