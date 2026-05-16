# IMT-003 Review — Claude

**Task:** IMT-003 imitation dataset builder skeleton
**Owner:** Claude2
**Reviewer:** Claude
**Date:** 2026-05-16

## Verdict: APPROVED

No blocking findings.

## Verification

```
cd services/research/imitation
python3 -m py_compile dataset_builder.py   # OK
python3 -m pytest test_dataset_builder.py -v  # 24 passed
python3 smoke_test.py                      # All smoke tests passed
```

## Assessment

### Governance constants
`ALLOWED_ACTOR_ROLES`, `ALLOWED_PROMOTION_STATES`, and `ELIGIBLE_DECISIONS` in
`dataset_builder.py` are identical to those in `services/learning/imitation/adapter.py`.
The research-to-learning boundary is consistent.

### Filtering logic
`ImitationDatasetBuilder._filter_reason()` correctly applies all four governance
checks in order: actor_role → decision → strategy_id match → promotion_state.
`DatasetBuilderError` is raised when all sessions are filtered.

### Decision aliasing
`_DECISION_ALIASES` correctly maps `approved → approve` and `edited → edit`.
`rejected → reject` is also mapped (normalised but then filtered by ELIGIBLE_DECISIONS).

### Schema correctness
`RawTrajectorySession.from_dict()` validates required fields, handles the optional
`event_type` fallback for the `decision` field, and requires a non-empty `steps`
sequence. `_parse_step()` validates numeric observations and non-empty action.

### Exports
`__init__.py` exports all 8 public names; `__all__` is consistent.

### Tests
24 unit tests cover: happy path, all three governance filter axes, strategy_id
mismatch, all-filtered error, decision aliases, paper promotion state, optional
reward, strict feedback_event_id config (both single and partial), build_id prefix,
built_at ISO format, and session order preservation.
Smoke test covers the end-to-end pipeline including the all-filtered error path.

## Non-blocking observations

1. `_DECISION_ALIASES` maps `"rejected" → "reject"` which is not in ELIGIBLE_DECISIONS
   and will always be filtered. This is harmless — the alias is accurate and the
   governance filter still rejects it. No change needed.

2. `require_strategy_id_match=True` is the safe default. Custom config can disable
   it for cross-strategy transfer datasets. Acceptable as-is.

## Conclusion

Implementation is clean, governance constants are consistent with the learning adapter,
all 24 tests pass, smoke test passes. Approved for finalization by owner.
