# Review: MGMT-EVO-006 — evolution observation window report

Reviewer: Claude
Task: MGMT-EVO-006
Owner: Codex2
Date: 2026-05-15

## Verdict: Approved

## Scope Verified

Read-only `GET /api/evolution/proposals/{decision_id}/observation-report` endpoint
added to `services/evolution/main.py`. New `ObservationWindowReportResponse` Pydantic
model added to `services/evolution/models.py`. Three new tests in
`services/evolution/test_evolution_service.py`.

## Verification

```
python3 -m pytest services/evolution/test_evolution_service.py -q
53 passed in 43.92s

python3 -m pytest services/evolution/test_evolution_service.py -k "observation" -v
3 passed
```

## Implementation Checks

- **404 path**: missing decision → `_not_found()` → 404. ✓
- **422 pre-execute guard**: non-executed decision returns 422 with "executed" in detail. ✓
- **400 as_of guard**: invalid as_of timestamp returns 400. ✓
- **422 missing timestamps**: executed decision missing cooldown/observation timestamps returns 422. ✓
- **`_window_state()`**: `not_started / open / elapsed` boundary conditions correct; boundary `as_of == end` → "open". ✓
- **`active_until`**: `max(cooldown_end, observation_end)` — correctly handles 3-day cooldown + 7-day observation (observation governs). ✓
- **`active_blocking`**: `report_time <= active_until` — correctly flips to False when both windows elapsed. ✓
- **`convergence_status`**: four transitions correctly mapped; elapsed observation + elapsed cooldown → `eligible_for_next_decision`. ✓
- **`seconds_since_observation_start`**: 86400 for 1-day as_of offset — correct. ✓
- **`seconds_until_*`**: clamped to 0 when elapsed — correct. ✓
- **`followthrough_refs`**: execution_ref_id surfaces as dispatch_command ref. ✓
- **`policy_refs`**: includes `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2-§5.4` and SA supplemental. ✓
- **Read-only contract**: no state mutations; no new EvolutionDecision, cooldown window, or dispatch command created. ✓

## Non-Blocking Notes

- No test for `observation_state == "not_started"` (as_of before observation starts). Edge case for audit replay only; not blocking.
- `assert` type-narrowing stubs after the `if not all(...)` guard (lines 461-464) are acceptable mypy hints.

## Summary

MGMT-EVO-006 scope is correctly and completely implemented. The endpoint is read-only,
enforces the executed precondition, correctly derives window state and convergence status,
and surfaces all required evidence/followthrough refs. Tests pass deterministically.
Returned to Codex2 for closeout finalization.
