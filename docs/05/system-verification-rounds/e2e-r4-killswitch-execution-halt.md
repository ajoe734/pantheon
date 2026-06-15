# E2E-R4 — Kill-switch / pause execution-halt enforcement (SAFETY)

**Round:** E2E-R4 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r4-killswitch-halt
**Business flow:** operator pause / kill-switch / safe-mode → runtime-binding
status transitions to a halt state → the paper execution loop must STOP filling
orders (defense-in-depth).

## Plan

1. Inspect whether the paper execution worker gates signal execution on binding
   status / kill-switch / safe-mode.
2. Confirm e2e on the live fleet with a controlled, reversible binding pause.
3. Fix the gap in the worker; add a regression test; roll the fix into the image.

## Live test (controlled, reversible — single binding)

Binding `rb-016ccb04…` (runtime `rt-rescue-0260531-1715d8d2`) was transitioned
`active → pending_pause → paused` via the runtime-manager state machine, 3 signals
were pushed to its queue, and after one poll cycle the binding was restored to
`active`. Result:

```
current status: paused
queue depth: 3 -> 0   (worker drained the queue while PAUSED)
MarketOrders while PAUSED: 1  ([…] MarketOrder MSFT 3)
restore -> active (clean)
```

## Finding (real safety gap — now fixed)

**The paper execution worker continued consuming and filling orders while its
runtime-binding was `paused`.** `PaperRuntimeService.drain_once()` resolved the
binding but then executed unconditionally — it did not gate on binding status,
and nothing in the signal-consume → fill path checks kill-switch / safe-mode.
So an operator pause / kill-switch (which halts at the command/broker layer) did
**not** stop the paper execution loop — a defense-in-depth failure.

## Fix (code)

`services/execution/lean_runtime/paper_runtime.py`:
- New module constant `_HALT_BINDING_STATUSES = {paused, pending_pause, failed, retired}`.
- `drain_once()` now skips execution when the resolved binding's status is in that
  set, recording `last_skipped_status` and leaving signals on the queue so they
  replay when the binding returns to `active`.
- Surfaced `last_skipped_status` in the runtime snapshot for observability.

Regression tests (`test_paper_runtime.py`):
`test_drain_once_does_not_execute_when_binding_halted` (all four halt statuses)
and `test_drain_once_resumes_execution_when_binding_active`. Full suite: 20 passed.

## Disposition

- **Shipped (code/CI):** the safety gate + regression tests (gated by the existing
  `test_paper_runtime` suite in CI). This is a real code fix, not just a probe.
- **Rollout:** the worker image (`pantheon-lean-runtime`) is rebuilt so spawned
  paper-runtime workers pick up the gate; the live bug was proven before the fix
  and the gate is unit-proven after.

## Next round

E2E-R5: intervention / approval state-machine transition enforcement (v5
interventions: claim → decide → escalate → two-man-sign cannot skip states), or
deepen R4 by also gating on a kill-switch/safe-mode flag independent of binding
status.
