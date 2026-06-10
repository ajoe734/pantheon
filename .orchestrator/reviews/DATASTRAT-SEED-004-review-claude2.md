# Review: DATASTRAT-SEED-004 — Strategy Seed Store and Materializer

Reviewer: Claude2
Date: 2026-06-09

## Scope

Reviewed the anchor commit `d7238d97` delivering:
- `services/source_ingestion/strategy_seed_store.py`
- `services/source_ingestion/seed_materializer.py`
- `services/source_ingestion/tests/test_strategy_seed_store.py`

## Verification

```
python3 -m pytest services/source_ingestion/tests/test_strategy_seed_store.py \
  services/source_ingestion/tests/test_strategy_seed_builder.py -v
# 19 passed in 2.06s
```

## Acceptance Criteria Status

| Criterion | Status |
|---|---|
| Idempotent materialization (same bundle+sources → same seed_id, no duplicate records) | PASS |
| Lineage refs persisted and retrievable | PASS |
| Rejected source records block seed creation | PASS |
| Seed metadata cannot request direct execution route | PASS (3 variants tested) |
| Status-based filtering | PASS |
| License / allowed_use stored as top-level queryable fields | PASS |
| No-direct-execution-route invariant in store | PASS |

## Notes

1. `StrategySpecSeedStore` is JSONL-backed with clear Postgres upgrade path; appropriate for first-segment delivery.
2. `SeedMaterializationService` correctly handles CREATE_IF_ABSENT idempotency and REFRESH upsert; FORCE_NEW_VERSION defers to REFRESH with documented comment — acceptable for this slice.
3. `_assert_no_direct_execution_route` guards metadata, backend_hint, and lineage fields — the triple-check is the right defense-in-depth approach.
4. Minor: `created_by` defaults to `"Claude"` in the materializer; not a blocker (caller-overridable).

## Decision

APPROVED — implementation meets the EvidenceBundle → StrategySpecSeed → Store first-segment acceptance criteria. Ready for closeout and PR.
