# LOOP-AUTO-EVO-004 Evidence: Evolution Dispatch Worker

**Task:** Dispatch approved evolution actions through gates
**Date:** 2026-06-27
**Owner:** Claude
**Branch:** task/LOOP-AUTO-EVO-004
**Commit:** 39918fbe

## Deliverables

- `services/evolution/dispatch_worker.py` — supervised dispatch worker
- `services/evolution/test_dispatch_worker.py` — 13 unit tests

## Acceptance Verification

| Criterion | Result |
|---|---|
| Approved action dispatches only through allowed gated path | PASS — worker calls `/execute` which enforces `ActionBoundary` via `EvolutionController.execute_approved()`; no bypass path exists |
| Production-affecting mutation requires correct approval gate | PASS — `reviewer`/`risk_owner`/`governance_committee` role required to review/approve before dispatch; wrong execution role returns HTTP 422 |
| Dispatch result visible in EvolutionDecision follow-through | PASS — `execution_result`, `cooldown_ends_at`, `observation_window_ends_at` set on execute; `/observation-report` exposes full follow-through |

## Test Run

```
python3 -m pytest services/evolution/test_dispatch_worker.py -v
13 passed in 2.67s
```

## Architecture Notes

The dispatch worker is safe-by-default:
- `has_active_runtime=False` unless the decision metadata explicitly sets it
- `freeze_mode=governance_only` prevents unintended runtime follow-through
- Already-executed decisions are not visible in the approved list (idempotent)
- Gate enforcement is in the domain layer (`evolution_controller.py`), not duplicated in the worker
