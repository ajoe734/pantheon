# DEVLOOP-PAPER-BINDING-RESTORE-001 Sidecar Acceptance Packet

**Sidecar task:** `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE`
**Parent task:** `DEVLOOP-PAPER-BINDING-RESTORE-001` - Restore dev paper RuntimeBinding so the loop drains signals again
**Helper kind:** `acceptance_packet`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared:** 2026-07-03

> Scope constraint: this is a support artifact only. It does not modify
> canonical truth, L1 policy, core runtime, registry, governance, supervisor
> cadence, or live paper-loop scripts. The parent owner decides whether and how
> to absorb this packet into the main repair.

---

## 1. Purpose

This sidecar packages the acceptance checklist and dependency map for the
parent repair. The parent task is a dev runtime rescue: the paper loop is
fail-closed because the RuntimeBinding store is empty, signals are still being
fed to a binding-scoped Redis queue, and the paper runtime cannot drain without
a binding.

This packet is meant to help the parent owner and reviewer avoid three common
false closes:

1. Recreating a container without restoring the runtime-manager binding truth.
2. Moving signals or queues without proving the producer, RuntimeBinding, and
   worker all use the same `binding_id`.
3. Showing a local fill or telemetry-shaped record without proving it came from
   the real dev paper loop and survived the expected restart/recreate boundary.

---

## 2. Parent Task Snapshot

From the central `ai-status.json` task row, the parent acceptance criteria are:

| # | Parent acceptance | Review interpretation |
|---|---|---|
| A1 | `runtime_bindings.json` has at least one active binding for `strategy-devloop-l0-001` with `binding_id` equal to the fed queue, before/after shown | The binding must be restored through the regular runtime-manager path or an equivalent runtime-manager-owned state repair. Do not bypass the RuntimeBinding-required guard. |
| A2 | `pantheon-pantheon-paper-runtime-1` becomes healthy; drain no longer raises `RuntimeBinding is required before paper execution can drain signals`; pending queue depth drops | Health must be paired with queue movement and no recurrence of the fail-closed drain error. |
| A3 | End-to-end paper fill plus `TelemetryEvent` appears after the fix with a real fingerprint, not fixture or synthesized proof | Evidence must include fill/readback identity tied to the restored binding and runtime. |
| A4 | `ensure_worker.sh` no longer babysits a non-existent container and failures are visible | The current script checks `paper-rt-test`; parent must align it to the real managed worker/container or retire this babysit path with visible failure behavior. |
| A5 | Binding survives a container/volume recreate; no supervisor cadence change; existing tests green | The fix must be durable across the stated recreate boundary and must not change supervisor cadence as a workaround. |

Additional observed task facts:

- `paper_runtime.py` fails closed in `PaperRuntimeService.drain_once()` when no
  binding is resolved: `RuntimeBinding is required before paper execution can
  drain signals`.
- `/home/lupin/paper-loop/feed_signals.sh` currently pushes to
  `pantheon:signals:pending:rb-bf09c882005b4806a389b7d1d14f6469`.
- `/home/lupin/paper-loop/ensure_worker.sh` currently tries to start
  `paper-rt-test`, which the parent task summary says has been deleted.

---

## 3. Dependency Map

### 3.1 Canonical Policy Dependencies

| Source | Parent implication |
|---|---|
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Binding is not deployment. Real deployment follows `ApprovalDecision -> DeploymentPlan -> RuntimeBinding`; runtime-manager owns RuntimeBinding write authority. |
| `PAPER_CANARY_LIVE_POLICY.md` | Paper uses real market data and real runtime path with simulated execution. No live broker side effect or live funds are allowed for this repair. |
| `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | Local unit tests or a container health check alone do not prove a governed paper loop. The parent needs cross-surface evidence: binding, worker, queue, fill, telemetry, and durability. |
| `DELIVERY_CLOSURE_AND_LOOP_STATES.md` | A packet is not closed by a spec or isolated route. The current loop closes only when the returned runtime evidence is accepted or explicitly packetized for follow-up. |

### 3.2 Runtime And Queue Dependencies

| Dependency | Required alignment |
|---|---|
| RuntimeBinding store | Must contain an active paper binding for `strategy-devloop-l0-001`. The `binding_id` must match the signal queue key unless the parent intentionally updates both producer and worker queue derivation. |
| Desired-state query | Active paper bindings are the desired input for the fleet reconciler. Retired, paused, failed, or missing bindings must not be treated as runnable. |
| Paper fleet reconciler | The worker environment derives `PANTHEON_SIGNAL_QUEUE_KEY` from `binding_id` as `pantheon:signals:pending:<binding_id>`. If parent changes the binding id, it must prove the worker starts with the matching queue key. |
| Signal producer | The live feed script currently hardcodes the queue for `rb-bf09c882005b4806a389b7d1d14f6469`. Parent must either recreate that binding id or update and verify the producer. |
| Signal consumer | Binding/runtime/capital-pool mismatches should be rejected or DLQ'd. Do not "fix" the loop by allowing blind shared-queue consumption. |
| Paper runtime | `drain_once()` must resolve a non-halted binding before consuming signals and emitting heartbeat/fill telemetry. |
| Telemetry service | The final proof must show a real telemetry readback or stored event tied to the restored runtime/binding. A local JSON event shape is not enough. |
| Worker babysit | `ensure_worker.sh` must target the actual dev paper runtime ownership model. Restarting `paper-rt-test` is not acceptance unless that container is again the real worker by design. |

### 3.3 Completed Prior Work To Reuse

| Surface | Useful prior result |
|---|---|
| `LOOP-AUTO-RT-001` | Archived `done`; defines active paper/canary RuntimeBinding desired-state query and policy envelope. |
| `LOOP-AUTO-RT-002` | Archived `done`; implements active paper RuntimeBinding to exactly-one supervised worker behavior. |
| `LOOP-AUTO-TEL-001` | Archived `done`; validates telemetry readiness, writer metrics, DLQ, and replay behavior. |
| `docs/deployment/evidence/loop-auto-rt-005/README.md` | Consolidates controller-level runtime fleet evidence for restart, stale heartbeat, retire-binding stop, and signal isolation. Use as prior evidence, not as proof that this dev incident is fixed. |
| `docs/deployment/evidence/loop-auto-dep-004/README.md` | Defines stage-truth projection expectations: approval, plan, saga, binding, runtime fleet. Parent repair should keep those stages distinguishable in evidence. |

---

## 4. Parent Acceptance Checklist

| # | Gate | Required evidence |
|---|---|---|
| P1 | Binding absence/root cause recorded | Before snapshot shows RuntimeBinding count was `0` or otherwise missing for `strategy-devloop-l0-001`; parent notes why the store was empty if discoverable. |
| P2 | Binding restored by owned path | After snapshot shows active paper RuntimeBinding for `strategy-devloop-l0-001`, with `deployment_stage`/`deployment_mode=paper`, `status=active`, and all identity fields needed by telemetry. |
| P3 | Queue and binding id match | Evidence shows the fed Redis key `pantheon:signals:pending:<binding_id>` matches the active RuntimeBinding id, or shows coordinated updates to producer and worker queue derivation. |
| P4 | Runtime worker is the real consumer | `pantheon-pantheon-paper-runtime-1` or the fleet-managed replacement is healthy, resolves the restored binding, and uses the same queue key. |
| P5 | Drain consumes real pending work | Queue depth decreases after drain without `RuntimeBinding is required before paper execution can drain signals`; runtime state increments processed signal or execution/fill counts. |
| P6 | Paper fill is real dev-loop output | Fill/order evidence includes `strategy_id=strategy-devloop-l0-001`, restored `binding_id`, runtime id, signal id, and `submitted_to_broker=false`. |
| P7 | Telemetry readback exists | A stored or service-read telemetry event exists after the fix, tied to the same `binding_id`, runtime id, capital pool, artifact, and plan where available. |
| P8 | No live side effects | No broker live order, real funds, production credential use, or live/canary stage mutation is introduced. |
| P9 | `ensure_worker.sh` is truthful | Babysit target matches the actual runtime container/service or fails visibly with a non-silent error when the worker is absent. |
| P10 | Recreate durability proven | After the agreed container/volume recreate, the active binding is still present or automatically reconstructed through the intended runtime-manager/bootstrap path, and the worker drains again. |
| P11 | Regression coverage ran | Focused tests for touched repo files pass; if live scripts under `/home/lupin/paper-loop/` are changed, parent records an equivalent shellcheck/smoke or explicit manual validation. |

---

## 5. Evidence Capture Template

The parent can use equivalent commands, but the final evidence should answer
each row below with concrete output paths or log excerpts.

| Evidence item | Before | After | Reviewer note |
|---|---|---|---|
| RuntimeBinding store/query | Binding count and active entries for `strategy-devloop-l0-001` | Active paper binding with final `binding_id` | Required for P1/P2 |
| Redis queue depth | `LLEN pantheon:signals:pending:<binding_id>` | Depth lower after drain | Required for P3/P5 |
| Worker health | `docker ps`, runtime `/health`, runtime `/api/runtime/state` | Healthy and bound to same `binding_id` | Required for P4 |
| Drain result | Failing drain log or error | Drain completes without RuntimeBinding-required error | Required for P5 |
| Fill/readback | None or stale | New paper fill/order event with signal id | Required for P6 |
| Telemetry readback | None or stale | New telemetry event tied to binding/runtime | Required for P7 |
| Babysit behavior | `ensure_worker.sh` target and output | Correct target or visible failure | Required for P9 |
| Recreate test | State before recreate | Binding and drain still work after recreate | Required for P10 |

Suggested focused repo validation, adjusted to the actual files touched by the
parent:

```bash
python3 -m pytest \
  services/execution/lean_runtime/test_paper_runtime.py \
  services/execution/lean_runtime/test_signal_consumer.py \
  services/execution/lean_runtime/test_signal_isolation.py \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py \
  services/runtime-manager/test_fleet_desired_state.py \
  services/telemetry/test_paper_runtime_ingest_contract.py -q
```

If the parent changes only live scripts outside the repo, the validation must
still include a live dev-loop smoke that proves queue depth decreases and a new
paper fill plus telemetry event appears.

---

## 6. Non-Claims And Rejection Cases

This packet does not claim the parent is fixed, reviewed, or ready to close.

The reviewer should reject parent closeout if any of these are true:

- The fix deletes or weakens the RuntimeBinding-required fail-closed guard.
- The worker is made to consume a shared or wrong queue instead of restoring
  binding-scoped alignment.
- The evidence uses fixture/synthetic fills or hand-written telemetry as the
  primary proof.
- The only change is restarting or recreating `paper-rt-test`.
- `ensure_worker.sh` still silently succeeds while targeting a non-existent
  container.
- The binding disappears after the required recreate check.
- Supervisor cadence is changed to mask the runtime/binding issue.
- Live/canary broker or capital side effects are introduced.

---

## 7. Handoff To Reviewer

**To:** `Claude`
**From:** `Codex`
**Requested review outcome:** approve this sidecar if it is accurate and useful
as support material for the parent repair. This approval should not be treated
as approval of the parent implementation.

Recommended parent-owner next steps:

1. Use the checklist in Section 4 as the review matrix while repairing
   `DEVLOOP-PAPER-BINDING-RESTORE-001`.
2. Attach concrete before/after evidence for the binding store, Redis queue,
   worker health, drain result, fill, telemetry readback, and recreate test.
3. Keep runtime-manager as the RuntimeBinding owner and keep paper/live
   boundaries fail-closed.
4. If any acceptance item cannot be satisfied in the current turn, record a
   blocker in `ai-status.json` rather than substituting weaker evidence.
