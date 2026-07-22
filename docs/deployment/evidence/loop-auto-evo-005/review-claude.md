# Review: LOOP-AUTO-EVO-005 — Evolution Rollback Follow-Through

**Reviewer:** Claude
**Date:** 2026-06-27
**Owner:** Claude2
**Verdict:** APPROVED

---

## Verification Run

```
python3 -m pytest services/evolution/test_evo_005_rollback_followthrough.py -v
20 passed in 3.50s
```

All 20 tests re-verified in reviewer worktree. No regressions.

---

## Acceptance Criteria Assessment

### AC-1: Approved rollback command reaches runtime-manager or deployment ✅

`test_end_to_end_evolution_freeze_to_runtime_rollback` exercises the complete
chain in one test: freeze proposal → review → approve →
`/api/evolution/proposals/{id}/rollback-followthrough` →
`RuntimeManagerService.rollback()` → old binding retired → new binding active.
Three additional strategy tests (`replace`, `pause_then_replace`,
`liquidate_then_replace`) confirm the runtime-manager end of the chain handles
each rollback action type correctly. The test uses the real `RuntimeManagerService`
in-process — no seed fixtures, no mocks of the service layer.

### AC-2: BFF shows proposed/reviewed/approved/dispatched/executed stages ✅

`TestBffStageVisibility` (8 tests) individually verifies each stage transition:
- `proposed` via `decision_state == "proposed"` from `POST /api/evolution/proposals`
- `reviewed` via `decision_state == "reviewed"` + `review_chain` step
- `approved` via `decision_state == "approved"` + `review_chain` step
- `dispatched` via `execution_result.execution_ref_id` set after execute
- `executed` via `decision_state == "executed"` + `followthrough_refs`

`test_observation_report_shows_executed_decision` verifies all five stages are
present in the `GET /api/evolution/proposals/{id}/observation-report` BFF
endpoint. `test_boundary_query_shows_runtime_rollback_followthrough` confirms
the boundary endpoint correctly declares `runtime.rollback` follow-through for
freeze+active_runtime decisions.

### AC-3: Failure path records blocked reason and retry state ✅

`TestRollbackFollowthroughFailurePaths` (5 tests) covers:
- `proposed` state (not yet approved) → HTTP 422, "approved"/"state" in error
- `reviewed` state (not yet approved) → HTTP 422, "approved"/"state" in error
- missing `active_binding_id` → HTTP 422, "active_binding_id"/"binding" in error
- non-freeze `action_type` (retrain) → HTTP 422 or governance-only path (not runtime plane)
- duplicate execution (already `executed`) → HTTP 422

`TestRollbackFollowthroughRuntimeManagerIntegration` adds:
- terminal binding rollback → `RuntimeManagerError` with "terminal"/"retired" in message
- missing binding rollback → `RuntimeManagerError`/`RuntimeBindingError` with binding ID or "not found"

All failure cases surface a human-readable blocked reason. No silent failures.

---

## Quality Notes

- Tests use real service code paths — evolution `FastAPI` app via `TestClient`,
  `RuntimeManagerService` constructed fresh per-test. Not mocked, not seed fixtures.
- Autouse fixture resets store state between tests; no shared state leaks.
- The test file is self-contained: starts with `EVOLUTION_DATA_DIR` isolated to
  a `tempfile.mkdtemp()` directory so parallel CI runs do not collide.
- Three rollback strategy branches (replace / pause_then_replace /
  liquidate_then_replace) are individually tested, including the `start_paused`
  variant where position lineage correctly keeps the old binding as current
  owner until zero-position is confirmed.
- Evidence document (`README.md`) matches test output and architecture diagram
  is consistent with the implementation.

---

## Decision

Approved. All three acceptance criteria are met by running tests against the
real service code. Owner (Claude2) may finalize and close.
