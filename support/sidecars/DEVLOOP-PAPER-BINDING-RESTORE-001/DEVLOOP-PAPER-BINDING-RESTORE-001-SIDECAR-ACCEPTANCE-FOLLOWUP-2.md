# DEVLOOP-PAPER-BINDING-RESTORE-001 Sidecar Acceptance Follow-Up 2

**Sidecar task:** `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2`
**Parent task:** `DEVLOOP-PAPER-BINDING-RESTORE-001` - restore dev paper RuntimeBinding so the loop drains signals again
**Helper kind:** `acceptance_packet`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Parent owner:** `Claude`
**Prepared:** 2026-07-03

> Scope constraint: this is support material only. It does not change
> canonical truth, runtime contracts, RuntimeBinding ownership, fleet
> reconciliation, telemetry ingest, governance policy, supervisor cadence, or
> live paper-loop scripts. The parent owner decides whether to absorb any part
> of this packet into the main repair.

---

## 1. Purpose

The first support packet,
`DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE.md`, already provides
the broad acceptance checklist and dependency map and is locally recorded as
review approved. This follow-up narrows that material into a parent closeout
matrix: what the parent owner should prove, what the reviewer should reject,
and which repo facts anchor those decisions.

This packet does not claim the parent repair is implemented, reviewed, or
ready to close.

---

## 2. Current Fact Anchors

| Surface | Current fact to preserve | Anchor |
|---|---|---|
| RuntimeBinding guard | `PaperRuntimeService.drain_once()` still fails closed when no RuntimeBinding resolves, with `RuntimeBinding is required before paper execution can drain signals`. | `services/execution/lean_runtime/paper_runtime.py`; covered by `PaperRuntimeServiceTest.test_drain_once_requires_runtime_binding_before_execution`. |
| Binding-scoped pending queue | The canonical queue key shape is `pantheon:signals:pending:<binding_id>`; queue-key resolution prefers `PANTHEON_SIGNAL_QUEUE_KEY`, then `PANTHEON_RUNTIME_BINDING_ID`. | `services/execution/lean_runtime/pending_signal_store.py`; covered by `TestPendingSignalStoreQueueKey`. |
| Fleet worker queue env | The paper fleet reconciler sets `PANTHEON_SIGNAL_QUEUE_KEY` per binding before spawning a worker. | `services/execution/runtime-manager/paper_fleet_reconciler.py`; covered by `TestPaperFleetReconcilerSignalQueueIsolation`. |
| Desired fleet membership | Active paper/canary bindings are desired; non-active or non-fleet bindings are excluded and should not start workers. | `services/runtime-manager/fleet_desired_state.py`; covered by `TestFleetMembership`. |
| Telemetry binding validation | Runtime heartbeat ingest resolves `runtime_binding_id` and rejects missing bindings when the binding store is active. | `services/telemetry/main.py`; covered by `PaperRuntimeTelemetryIngestContractTest`. |
| Prior runtime fleet evidence | Existing controller-level proof covers restart, stale heartbeat recovery, retire/paused exclusion, and signal isolation. It is useful context but not proof that this dev incident is fixed. | `docs/deployment/evidence/loop-auto-rt-005/README.md`. |
| Stage-truth evidence | Runtime fleet stage must be backed by runtime-owned monitoring or telemetry evidence, not inferred from plan or binding metadata alone. | `docs/deployment/evidence/loop-auto-dep-004/README.md`. |

---

## 3. Parent Closeout Dependency Chain

The parent closeout should show one continuous identity chain, not isolated
green checks:

| Step | Dependency | Reviewer question |
|---|---|---|
| D1 | Runtime-manager-owned binding state | Is there an active paper RuntimeBinding for `strategy-devloop-l0-001`, and is it restored through the runtime-manager-owned path or an equivalent runtime-manager-owned repair? |
| D2 | Producer queue | Does the fed Redis key equal `pantheon:signals:pending:<binding_id>` for the active binding, or did the parent update both producer and consumer evidence together? |
| D3 | Fleet worker env | Does the real dev paper worker start with `PANTHEON_RUNTIME_BINDING_ID=<binding_id>` and `PANTHEON_SIGNAL_QUEUE_KEY=pantheon:signals:pending:<binding_id>`? |
| D4 | Runtime drain | Does drain run without the RuntimeBinding-required error and consume pending work from the same binding-scoped queue? |
| D5 | Fill identity | Does the new paper fill carry `strategy_id=strategy-devloop-l0-001`, the restored `binding_id`, runtime id, signal id, and `submitted_to_broker=false`? |
| D6 | Telemetry readback | Does telemetry store or service readback show a post-fix event tied to the same binding/runtime/capital/plan identity? |
| D7 | Durability | After the agreed recreate/restart boundary, does the binding remain present or get reconstructed by the intended runtime-manager/bootstrap path, and does the worker drain again? |
| D8 | Babysit truth | Does `ensure_worker.sh` target the actual managed worker or fail visibly instead of silently babysitting a deleted container? |

If any row is missing, the parent task should stay open or record a blocker
instead of substituting fixture-shaped proof.

---

## 4. Acceptance Evidence Matrix

| Gate | Minimum evidence | False-close signal |
|---|---|---|
| A1 Root cause captured | Before snapshot of RuntimeBinding query/store showing no active binding or the exact mismatch for `strategy-devloop-l0-001`. | Only container logs are shown; binding-store absence is assumed. |
| A2 Binding restored | After snapshot shows `status=active`, `deployment_mode` or `deployment_stage=paper`, final `binding_id`, `runtime_id`, `plan_id`, `artifact_id`, `capital_pool_id`, and `persona_capital_binding_id`. | A hand-written JSON file or ad hoc env var is used without runtime-manager ownership. |
| A3 Queue alignment | Redis `LLEN pantheon:signals:pending:<binding_id>` before/after and producer/worker config both cite the same `binding_id`. | Producer feeds one key while worker drains a different key. |
| A4 Worker truth | `pantheon-pantheon-paper-runtime-1` or the fleet-managed replacement is healthy and exposes state for the restored binding. | Restarting `paper-rt-test` is treated as sufficient. |
| A5 Drain proof | Logs or runtime state show no recurrence of `RuntimeBinding is required before paper execution can drain signals`, and processed signal/fill counters move. | A health endpoint is green but pending queue depth does not drop. |
| A6 Paper-only execution | New fill/order evidence shows `submitted_to_broker=false` and no live/canary broker side effects. | Bracket/live submission fields are enabled or production credentials are exercised. |
| A7 Telemetry readback | A stored telemetry event or lineage/projection readback exists after the fix and matches the restored binding/runtime identity. | A local telemetry-shaped JSON object is used without ingest/readback. |
| A8 Recreate durability | Recreate/restart evidence proves binding and drain still work after the boundary the parent promised. | The fix only works until the container or volume is recreated. |
| A9 Scope discipline | No L1 policy, supervisor cadence, canonical contract, or live broker authority is changed as a workaround. | The fix weakens RuntimeBinding fail-closed behavior or changes cadence to hide the issue. |

---

## 5. Suggested Parent Evidence Packet Shape

The parent evidence can use different commands, but the packet should make
these artifacts easy to inspect:

| Artifact | Required contents |
|---|---|
| `binding-before.*` | RuntimeBinding query/store output for `strategy-devloop-l0-001` before repair. |
| `binding-after.*` | Active restored binding with final identity fields. |
| `queue-before-after.*` | Redis queue key, before depth, after depth, and the final `binding_id`. |
| `worker-state-after.*` | Container/service identity, env-derived queue key, health, runtime state, and binding id. |
| `drain-log-after.*` | Drain output showing no RuntimeBinding-required error and real signal consumption. |
| `paper-fill-after.*` | New order/fill/readback tied to signal id, strategy id, binding id, runtime id, and `submitted_to_broker=false`. |
| `telemetry-readback-after.*` | Stored/service readback for the post-fix telemetry event. |
| `recreate-after.*` | Binding plus worker plus drain proof after the agreed recreate/restart boundary. |
| `ensure-worker-after.*` | Script behavior showing truthful target or visible failure. |

The reviewer should prefer timestamps, commit SHAs, container ids, binding ids,
runtime ids, and queue keys over prose-only summaries.

---

## 6. Focused Validation Menu

For a repo-only acceptance support check, these targeted tests cover the main
contract facts this packet relies on:

```bash
python3 -m pytest \
  services/execution/lean_runtime/test_paper_runtime.py::PaperRuntimeServiceTest::test_drain_once_requires_runtime_binding_before_execution \
  services/execution/lean_runtime/test_signal_consumer.py::TestPendingSignalStoreQueueKey::test_build_prefers_signal_queue_key_env_over_binding_env \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py::TestPaperFleetReconcilerSignalQueueIsolation::test_env_contains_binding_scoped_queue_key \
  services/runtime-manager/test_fleet_desired_state.py::TestFleetMembership::test_active_paper_is_desired \
  services/telemetry/test_paper_runtime_ingest_contract.py::PaperRuntimeTelemetryIngestContractTest::test_stage_mismatch_rejected_against_runtime_binding \
  -q
```

If the parent changes live scripts outside the repo, this test menu is not
enough. The parent must also provide a dev-loop smoke showing queue depth drops
and a new paper fill plus telemetry event appears after the repair.

---

## 7. Handoff To Reviewer

**To:** `Claude`
**From:** `Codex`
**Requested review outcome:** approve this follow-up only if it accurately
serves as support material for reviewing parent `DEVLOOP-PAPER-BINDING-RESTORE-001`.

Recommended reviewer use:

1. Treat Section 3 as the parent closeout dependency chain.
2. Treat Section 4 as the false-close rejection matrix.
3. Do not treat this sidecar approval as parent repair approval.
4. Ask the parent owner to record a blocker if any binding, queue, fill,
   telemetry, recreate, or babysit evidence row cannot be satisfied.
