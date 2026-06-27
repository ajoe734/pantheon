# LOOP-AUTO-RT-002: Managed Paper Runtime Fleet Reconciler — Evidence

**Task:** LOOP-AUTO-RT-002  
**Date:** 2026-06-27  
**Owner:** Claude2  
**Reviewer:** Claude

## Scope

Implemented the managed paper runtime fleet reconciler that maintains
exactly-one supervised worker subprocess per active paper `RuntimeBinding`.

## Key Changes

### `services/execution/runtime-manager/paper_fleet_reconciler.py`

- Added `_fetch_fleet_state()` method: calls the LOOP-AUTO-RT-001 canonical
  endpoint `GET /api/runtime-fleet/desired-state?stage=paper&include_excluded=true`
  and returns `(desired_bindings, excluded_binding_ids)`.
- Updated `reconcile_once()` to call `_fetch_fleet_state()` instead of the old
  `/api/runtime-bindings` endpoint, enabling explicit handling of excluded bindings.
- Excluded bindings (paused, retired, failed, draining) now trigger immediate
  worker termination via a dedicated stop path in the reconcile loop.
- `_fetch_active_paper_bindings()` retained as deprecated backward-compat wrapper.

### `services/execution/runtime-manager/test_paper_fleet_reconciler.py`

- Updated `_InstrumentedReconciler` to mock `_fetch_fleet_state` (includes both
  desired bindings and excluded IDs).
- Added `TestPaperFleetReconcilerExcludedBindings` (5 tests):
  paused stop, retired stop, dead+excluded not restarted, desired/excluded disjoint,
  fetch failure preserves excluded worker.
- Added `TestPaperFleetReconcilerAcceptanceCriteria` (4 tests):
  explicit coverage of all 3 acceptance criteria.

## Verification

```
python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -v
34 passed in 4.24s
```

## Acceptance Criteria Mapping

| Criterion | Test |
|---|---|
| Stack restart recreates workers for all active paper bindings | `test_stack_restart_recreates_workers_for_all_active_bindings` |
| Killing one worker restarts only that worker | `test_killing_one_worker_restarts_only_that_worker` |
| Paused or retired binding stops its worker | `test_paused_binding_stops_its_worker`, `test_retired_binding_stops_its_worker` |

## Integration

The `paper-fleet-reconciler` Docker service in `docker-compose.yml` is already
wired to the correct `runtime-manager` dependency and health check. The
`_fetch_fleet_state` method calls the fleet desired-state endpoint that
LOOP-AUTO-RT-001 added to the runtime-manager service.
