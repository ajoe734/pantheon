# DEVLOOP-PAPER-BINDING-RESTORE-001 Sidecar Acceptance Follow-Up 3

**Sidecar task:** `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-3`
**Parent task:** `DEVLOOP-PAPER-BINDING-RESTORE-001` - restore dev paper RuntimeBinding so the loop drains signals again
**Helper kind:** `acceptance_packet`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Parent owner:** `Claude`
**Prepared:** 2026-07-04

> Scope constraint: this is support material only. It does not change
> canonical truth, runtime contracts, RuntimeBinding write authority, fleet
> reconciliation, telemetry ingest, governance policy, supervisor cadence, or
> live paper-loop scripts. The parent owner decides whether to absorb this
> packet into the main repair.

---

## 1. Purpose

The original sidecar packet defines the full acceptance checklist and
dependency map. Follow-up 2 narrows that into a closeout matrix and false-close
signals. This follow-up gives the parent owner and reviewer a compact
handoff-ready packet: the dependency closure order, evidence bundle shape, and
review questions that should be answered before the parent repair moves toward
closeout.

This packet does not claim that the parent repair is implemented, reviewed, or
ready to close.

---

## 2. Packet Index

| Source packet | Use it for | Do not use it for |
|---|---|---|
| `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE.md` | Broad parent acceptance checklist, dependency map, evidence capture template, and rejection cases. | Claiming the dev paper loop has already been restored. |
| `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Closeout dependency chain and reviewer false-close matrix. | Replacing live before/after evidence from the parent repair. |
| This follow-up | Final support handoff packet for evidence ordering and reviewer questions. | Changing canonical policy or weakening RuntimeBinding-required behavior. |

---

## 3. Dependency Closure Order

The parent should prove these rows in order. Later rows should cite the same
identity values from earlier rows, especially `binding_id`, `strategy_id`,
runtime id, signal id, and queue key.

| Order | Dependency | Required closure evidence |
|---|---|---|
| C1 | Binding root cause | Before snapshot shows the active RuntimeBinding for `strategy-devloop-l0-001` was absent, inactive, mismatched, or otherwise unusable. |
| C2 | Runtime-manager-owned restore | After snapshot shows an active paper RuntimeBinding for `strategy-devloop-l0-001` with the final `binding_id` and deployment identity fields. |
| C3 | Producer and queue alignment | Signal producer feeds `pantheon:signals:pending:<binding_id>` for the restored binding, or the parent records a coordinated producer/consumer queue change. |
| C4 | Real worker alignment | The real dev paper runtime worker, not a deleted or orphan babysit target, starts with the same `binding_id` and binding-scoped queue key. |
| C5 | Drain movement | Queue depth drops and drain logs/state do not repeat `RuntimeBinding is required before paper execution can drain signals`. |
| C6 | Paper-only fill | New fill/order/readback carries the restored binding identity and `submitted_to_broker=false`. |
| C7 | Telemetry readback | Stored or service-read telemetry exists after the fix and matches the same binding/runtime/capital/plan identity where available. |
| C8 | Durability boundary | After the agreed restart/recreate boundary, the binding remains present or is reconstructed by the intended runtime-manager/bootstrap path, and drain still works. |
| C9 | Babysit truth | `ensure_worker.sh` targets the real managed worker or fails visibly; it must not silently babysit a deleted `paper-rt-test` container. |

If any row cannot be satisfied, the parent should record the missing row as a
blocker instead of substituting fixture-shaped or prose-only proof.

---

## 4. Evidence Bundle Shape

The parent can use any equivalent commands or paths, but the review packet
should make each evidence item inspectable without reconstructing the incident
from prose.

| Evidence file or section | Must include | Consumes dependency rows |
|---|---|---|
| `binding-before` | Timestamp, query/store path or endpoint, strategy id, and missing or mismatched binding state. | C1 |
| `binding-after` | Timestamp, final `binding_id`, status, stage/mode, runtime id, plan/artifact/capital identities if present. | C2 |
| `queue-before-after` | Redis key, before depth, after depth, and the final `binding_id`. | C3, C5 |
| `worker-state-after` | Container/service identity, runtime id, env-derived binding id and queue key, health/state output. | C4 |
| `drain-after` | Drain log or state movement with no RuntimeBinding-required error recurrence. | C5 |
| `paper-fill-after` | New signal/order/fill identity, strategy id, binding id, runtime id, and paper-only broker flags. | C6 |
| `telemetry-after` | Stored/service readback for a post-fix telemetry event tied to the same identity chain. | C7 |
| `recreate-after` | Binding, worker, queue, and drain proof after the promised restart/recreate boundary. | C8 |
| `ensure-worker-after` | Script target and output showing truthful worker ownership or visible failure. | C9 |

The reviewer should prefer concrete timestamps, ids, queue keys, container ids,
commit SHAs, and command outputs over narrative summaries.

---

## 5. Reviewer Questions

| Question | Expected answer before parent closeout |
|---|---|
| Does the fix preserve the RuntimeBinding-required fail-closed guard? | Yes; no guard weakening is used to drain signals. |
| Is the restored binding owned by runtime-manager or an equivalent runtime-manager-owned repair path? | Yes; no ad hoc env-only or hand-written bypass is the source of truth. |
| Do producer, binding store, worker env, drain result, fill, and telemetry all cite the same binding identity chain? | Yes; mismatched ids are explained or rejected. |
| Did the queue actually move after the repair? | Yes; before/after depth and drain state show consumption from the restored binding queue. |
| Is the paper fill real dev-loop output and paper-only? | Yes; it is not fixture/synthetic proof and does not submit to a live broker. |
| Did telemetry ingest/readback happen after the fix? | Yes; a stored or service-read event matches the restored runtime/binding identity. |
| Does the fix survive the promised restart/recreate boundary? | Yes; binding and drain still work after the boundary. |
| Is worker babysitting truthful? | Yes; `ensure_worker.sh` no longer silently targets a deleted/non-owned container. |

---

## 6. Non-Claims

This support packet does not:

- approve the parent repair;
- certify live dev runtime health;
- change RuntimeBinding ownership or queue semantics;
- authorize live broker or real-funds side effects;
- change supervisor cadence, dispatch policy, or canonical architecture;
- replace before/after evidence captured by the parent owner.

---

## 7. Handoff To Reviewer

**To:** `Claude`
**From:** `Codex`
**Requested review outcome:** approve this follow-up only if it is accurate and
useful as support material for reviewing parent
`DEVLOOP-PAPER-BINDING-RESTORE-001`.

Recommended reviewer use:

1. Treat Section 3 as the evidence closure order for the parent repair.
2. Treat Section 4 as the minimum parent evidence bundle shape.
3. Treat Section 5 as the parent closeout question set.
4. Do not treat this sidecar approval as parent repair approval.
5. Ask the parent owner to record a blocker for any missing binding, queue,
   worker, drain, fill, telemetry, recreate, or babysit evidence row.
