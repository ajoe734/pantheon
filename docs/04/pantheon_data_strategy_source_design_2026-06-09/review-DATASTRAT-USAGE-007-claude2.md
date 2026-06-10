# Review: DATASTRAT-USAGE-007
Reviewer: Claude2
Date: 2026-06-09
Commit: 4aef7a2493f6beef1e5660a438cd0733099ac44c

## Verdict: APPROVED

All acceptance criteria verified. 218 tests pass (31 new).

## Acceptance Criteria Checklist

- [x] SourceHealth model: last_success_at, last_failure_at, latest_watermark,
  row_count_last_run, rejected_count_last_run, schema_hash, staleness_seconds,
  error_rate_7d, cost_estimate_30d — all present and validated in __post_init__.
- [x] SourceUsageDaily: all 8 dimensions (ingest_run_count, query_count,
  search_hit_count, persona_match_count, strategy_seed_yield_count,
  strategy_promotion_count, experiment_dependency_count,
  active_strategy_dependency_count) + cost_estimate; composite key date::source_id.
- [x] Retirement rules (7): propose_disable, propose_replace, propose_retire,
  keep_increase_schedule, alert_backfill, keep_probation, no_action — all
  covered with correct preconditions in retirement_engine.py.
- [x] Critical invariant: active_strategy_dependency_count > 0 blocks
  propose_disable, propose_replace, and propose_retire in all code paths.
- [x] Observation window (30d default) enforced before propose_retire for
  disabled sources; remaining-days reason returned when window not elapsed.
- [x] BFF get_source_health_usage_snapshot returns enriched health+usage+rec
  composite with graceful fallback when service unavailable.
- [x] 6 new API endpoints: GET/PUT /health/{source_id}, GET /health,
  POST/GET /usage, GET /health/{source_id}/usage-aggregate,
  GET /retirement-recommendations, GET /health-usage-snapshot.

## Code Quality Notes

- source_health.py: frozen dataclasses with strict __post_init__ validation;
  error_rate_7d clamped to [0.0, 1.0]; negative count fields clamped to 0.
  JSONL stores delegate to JsonlRegistryStore correctly.
- retirement_engine.py: pure and deterministic; no side-effects; all branching
  paths return RetirementRecommendation with full evidence dict.
- main.py: health/usage stores initialized from env-configurable JSONL paths;
  retirement endpoint exposes threshold overrides as query params; snapshot
  endpoint avoids double-computation only for health store (usage aggregate is
  called twice per source — acceptable for current JSONL scale).
- bff/read_store.py: read-only delegation pattern; fallback returned on missing
  or unavailable service URL; json.loads(json.dumps(...)) copy is safe.

## Minor Observations (non-blocking)

1. Commit trailer says `LLM-Agent: Claude` — minor inconsistency with task
   ownership, but does not affect the delivered code.
2. `UpsertHealthRequest.metadata: dict[str, Any] = {}` uses a bare dict literal
   as Pydantic field default. Pydantic v2 handles this correctly; no bug.
3. get_health_usage_snapshot calls aggregate_for_source twice per source (once
   inside compute_recommendations lambda, once explicitly for the enriched
   list). At JSONL dev-store scale this is fine; worth noting for Postgres
   migration.

## Verification

```
python3 -m pytest services/source_ingestion/tests/ -v
218 passed in 34.26s
```

Reviewed independently of owner; tests observed to pass without modification.
