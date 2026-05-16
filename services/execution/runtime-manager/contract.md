# Runtime-Manager Contract

**Task:** RT-001
**Owner:** Claude
**Reviewer:** Claude2
**Status:** Review-ready — RuntimeBinding object, schema, authority boundary, and pytest suite (45 tests) locked for reviewer validation
**Tier:** L1 Execution Plane Contract
**Conflict rule:** This document defines the authoritative write boundary for the Execution Plane. It supplements `BINDING_AND_DEPLOYMENT_SEMANTICS.md` with operational detail for the Runtime Manager service.

---

## 1. Purpose

The Runtime Manager is the sole Execution Plane service authorised to create and mutate `RuntimeBinding` records.  Its job is to:

- consume an approved `DeploymentPlan` and translate it into a live `RuntimeBinding`
- enforce the single-runtime rule per capital pool
- manage the lifecycle of `RuntimeBinding` through status transitions
- preserve position lineage attribution during replace and rollback operations
- emit execution events with binding and stage context

This contract defines the authority boundary, pre-conditions, lifecycle rules, and failure semantics for these operations.

---

## 2. Authority Boundary

### 2.1 Write owner

| Object | Write Owner |
|---|---|
| `RuntimeBinding` (all fields) | Runtime Manager (Execution Plane) |
| `RuntimeBinding.status` | Runtime Manager only |
| `RuntimeBinding.retired_at` | Runtime Manager only |
| Position lineage `current_managed_by_binding_id` | Runtime Manager (on replace / rollback) |
| Execution events (`artifact_id`, `deployment_stage`, `plan_id`) | Runtime Manager |

No other service — not Governance Plane, not Capital Pool Plane, not BFF — may write to `RuntimeBinding`.

### 2.2 Read access

`RuntimeBinding` records are readable by any authorised service for query and audit purposes.  The Governance Plane may read bindings to verify admissibility; the BFF may read them to build operator views.

---

## 3. Pre-conditions for Creating a RuntimeBinding

Before the Runtime Manager may create a `RuntimeBinding`, all of the following must be satisfied:

1. **DeploymentPlan exists and is approved or executing**
   The plan's `plan_id` must resolve to a `DeploymentPlan` with `status ∈ {approved, executing}`.  A missing or rejected plan is a hard blocker.

2. **PersonaCapitalBinding exists and is active**
   The `persona_capital_binding_id` carried by the `DeploymentPlan` must resolve to a `PersonaCapitalBinding` with `status = active`.  The binding's `allowed_deployment_scope` must be `>=` the plan's `target_stage`.

3. **Single-runtime rule satisfied**
   If the backing `CapitalPool` has `single_runtime_enforced = True` (the default), the pool must have no existing `active` `RuntimeBinding`.  The Runtime Manager must retire the previous binding before activating the new one.

4. **Artifact loader checks passed**
   The execution loader must have validated the artifact's execution projection before the binding is created.
   Compatibility note: EX-001 still enforces legacy `promotion_state` only for `paper` / `live`, while `canary` / `frozen` continue to rely on canonical `artifact_state` + `deployment_stage` metadata until the loader migration is complete.

5. **Stage consistency**
   `RuntimeBinding.deployment_mode` must equal `DeploymentPlan.target_stage`.

6. **Canary/live activation gate present**
   `target_stage ∈ {canary, live}` requires explicit promotion-gate evidence at
   deploy time.  The Runtime Manager rejects forward activation unless the
   request carries a promotion gate with at least:
   `promotion_gate_decision_id`, `human_gate_packet_ref`,
   `broker_sandbox_smoke_ref`, `risk_owner_approval_ref`, and
   `operator_approval_ref`.  `live` additionally requires
   `canary_observation_ref`.  Canary activation also requires
   `0 < capital_scale_pct <= 5` and `0 < gross_scale_pct <= 25` in the gate.
   Rollback replacement creation may bypass this promotion gate internally,
   because rollback is a safety action rather than a forward activation.

---

## 4. RuntimeBinding Object References

Every `RuntimeBinding` must carry these three cross-object references (RUN-001 acceptance criteria):

| Reference | Field | Points to |
|---|---|---|
| Deployment plan | `plan_id` | `DeploymentPlan.plan_id` |
| Governance binding | `persona_capital_binding_id` | `PersonaCapitalBinding.binding_id` |
| Execution stage | `deployment_mode` | The actual stage: `paper` / `canary` / `live` / `frozen` |

These three references make the provenance chain auditable:
`PersonaCapitalBinding → DeploymentPlan → RuntimeBinding`

---

## 5. Status Lifecycle

```
pending_pause ──► paused ──► active ─► retired  (terminal)
     ▲               │
     │               └──────────────────► failed   (terminal)
     │
active ──────────────────────────────► failed      (terminal)
```

Full transition table:

| From | To | Trigger |
|---|---|---|
| `active` | `retired` | Successful replace / rollback / decommission |
| `active` | `failed` | Loader error / runtime crash / governance veto |
| `active` | `pending_pause` | `pause_then_replace` or `freeze` instruction |
| `pending_pause` | `paused` | All orders drained |
| `pending_pause` | `failed` | Drain timeout or error |
| `paused` | `active` | Resume instruction |
| `paused` | `retired` | Replace after pause completes |
| `paused` | `failed` | Governance veto or error |

Terminal states (`retired`, `failed`) have no further transitions.  Core fields of a terminal binding are immutable.

---

## 6. Single-Runtime Rule Enforcement

When `CapitalPool.single_runtime_enforced = True`:

1. `RuntimeBindingStore.create()` checks for any `active` binding for the pool.
2. If one exists, the create is rejected with a `RuntimeBindingError`.
3. The caller (Runtime Manager orchestration layer) is responsible for retiring the previous binding before creating the replacement.

This rule prevents split-brain scenarios where two artifacts simultaneously manage the same pool's positions.

---

## 7. Rollback Semantics

When executing a rollback `DeploymentPlan`:

| Field | Requirement |
|---|---|
| `rollback_parent` | Must point to the `binding_id` being replaced |
| `rollback_action_type` | Must match `DeploymentPlan.rollback.action_type` |

Rollback action execution follows the matrix in `rollback_action_matrix.md`:
- `replace` — hot-swap artifact, inherit existing book
- `pause_then_replace` — drain orders, then swap
- `liquidate_then_replace` — flatten all positions, then swap to fallback

In all cases, position lineage `current_managed_by_binding_id` is updated to the new binding atomically with the status transition.

---

## 8. Failure and Conflict Handling

- **Plan / binding mismatch:** If the resolved `DeploymentPlan` does not align with the `RuntimeBinding` being created, the Runtime Manager must abort and transition the plan to `failed`.
- **Concurrent plans for same pool:** Sequential execution enforced by `plan_id` / `created_at` ordering.  Only one plan may be `executing` per pool at a time.
- **Drain timeout (`pause_then_replace`, `liquidate_then_replace`):** If the stable state (zero orders / zero positions) is not reached within the configured `max_mitigation_window`, the Runtime Manager escalates to a Severity-1 incident and may trigger the kill-switch path (see `EVO-005`).

---

## 9. Implementation Artifacts

| Artifact | Purpose |
|---|---|
| `runtime_binding.py` | Python platform object — `RuntimeBinding`, `RuntimeBindingStore`, validation |
| `runtime_binding.schema.json` | Machine-readable JSON schema |
| `authority_matrix.md` | Write authority matrix (from RUN-001A support slice) |
| `rollback_action_matrix.md` | Rollback action execution matrix (from RUN-001A support slice) |
| `kill_switch_controller.py` | Emergency fast-path classifier and dispatcher for runtime-manager commands (EVO-005) |
| `test_kill_switch_controller.py` / `smoke_test_kill_switch_controller.py` | Fast-path invariants, audit coverage, and latency benchmark verification |

---

## 10. Downstream Consumers

| Consumer | What they read |
|---|---|
| `EX-002` (Gemini) | Rollback execution alignment against runtime-manager semantics |
| `TEL-001` (Gemini) | Telemetry schema gains `deployment_stage` and `runtime_binding_id` refs |
| `PER-001` (Claude) | Persona registry/session/runtime model references RuntimeBinding lifecycle |
| Operator BFF | Reads active binding per pool for deployment status surfaces |

---

## 11. Emergency Fast Path (EVO-005)

The kill-switch / safe-mode fast path supplements the normal deployment and rollback flows without changing the Runtime Manager write boundary:

1. `KillSwitchController` classifies an `EmergencyTrigger` as `soft` or `hard` according to `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`.
2. It emits a `KillSwitchCommand` with:
   - `dispatch_path = runtime_manager_fast_path`
   - `bypass_review_queue = true`
   - `priority = 1` for hard emergencies, `priority = 2` for soft emergencies
3. The command is forwarded to the Runtime Manager, which remains the sole writer for:
   - `RuntimeBinding.status`
   - safe operational state
   - position lineage cutover
4. The controller never mutates `RuntimeBinding` directly. Its responsibility is classification, safe-mode progression, and immutable audit-entry creation.

### 11.1 Replace-path requirements

If the selected action is `replace`, the command MUST carry both:

- `fallback_artifact_id`
- `fallback_artifact_version`

This keeps fallback identity explicit before the Runtime Manager accepts the fast-path command.

### 11.2 Audit guarantee

Every dispatch produces a `KillSwitchAuditEntry` before the caller acknowledges the command. Recovery-stage safe-mode transitions are also audited, so the incident timeline remains continuous across:

- initial kill-switch dispatch
- recovery testing
- restoration to normal

---

## 12. Fast-Path Benchmark Target

`smoke_test_kill_switch_controller.py` benchmarks the pure-Python hot path (`classify + dispatch`) with no blocking I/O.

v1 local verification target:

- `p95 dispatch latency <= 5.0 ms` over 1000 iterations

This is a smoke-level engineering target for regression detection, not a production SLA. The important invariant is that the emergency path bypasses the normal review queue while still routing through the Runtime Manager fast path.
