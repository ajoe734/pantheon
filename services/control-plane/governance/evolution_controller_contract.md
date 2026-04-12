# EvolutionController Contract

Last updated: 2026-04-11
Task: `EVO-004`
Status: canonical operational evolution boundary contract
Tier: L1-adjacent service contract derived from canonical evolution governance policy
Scope: EvolutionController orchestration boundary — freeze, rollback, retrain, redeploy ownership, threshold mapping, cooldown defaults, and execution plane routing
Conflict rule: this contract implements `EVOLUTION_REVIEW_AND_THRESHOLDS.md` and `ROLLBACK_AND_POSITION_SEMANTICS.md`; if this file and those L1 policy docs conflict, the L1 docs win and this contract must be updated

---

## 1. Purpose

`EvolutionController` is the single orchestration boundary that converts approved `EvolutionDecision` records into typed execution commands routed to the correct downstream plane.

It answers five questions that were previously unresolved:

- Which **execution plane** owns each action path (governance, runtime, research, deployment)?
- Which **actor role** may approve each action path before dispatch?
- Which **threshold section** in L1 policy triggers each action?
- What **cooldown and observation window** applies after execution?
- When does an approved decision also require a **companion runtime rollback signal**?

Without this boundary, `EvolutionDecision` approval and execution are decoupled by convention only. This contract makes the coupling explicit and machine-checkable.

---

## 2. Canonical Inputs

| Source | Why it matters |
|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Lifecycle, action classes, owner tiers, threshold defaults (§7) |
| `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` | Cooldown, observation window, loop-prevention rules |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Three rollback strategies, position treatment, telemetry cutover |
| `services/control-plane/governance/evolution_decision.py` | EvolutionDecision object, state machine, actor matrices |
| `services/execution/runtime-manager/rollback_action_matrix.md` | Rollback action type → runtime-manager execution mapping |

---

## 3. Boundary Rules

### 3.1 What EvolutionController owns

- **Propose**: create an `EvolutionDecision` in `proposed` state from a threshold signal or incident trigger
- **Route**: determine the correct execution plane and rollback signal for an approved decision
- **Record result**: call `EvolutionDecision.execute()` to transition to `executed` state, writing cooldown/observation timestamps

### 3.2 What EvolutionController does NOT own

- Actual freeze propagation (governance plane)
- Actual rollback execution — `RuntimeBinding` creation and position treatment (runtime plane / Rollback Controller)
- Retrain / revalidate execution (research plane)
- Deployment plan mutation (deployment plane)

### 3.3 Command boundary

- Downstream execution is triggered by `DispatchCommand` objects returned from `dispatch_approved()`
- `RollbackCommand` is a companion command emitted when the action mandates a concurrent runtime mitigation
- Execution commands are not executed in-process; the controller records a `SUBMITTED` result and returns the commands to the caller

---

## 4. Action Boundary Map

Each action path is declared with an `ActionBoundary`:

| Field | Meaning |
|---|---|
| `execution_plane` | Which plane handles actual execution (governance / runtime / research / deployment) |
| `threshold_policy_source` | Canonical section in L1 docs that defines trigger thresholds |
| `default_cooldown_days` | Minimum days before the same target may undergo another structural mutation |
| `default_observation_days` | Observation window after execution before the target is considered stable |
| `followthrough` | Whether dispatch also emits deployment or runtime follow-through commands |

### 4.1 Normal-path action boundaries

| Path | `EvolutionDecision` representation | Plane | Threshold source | reviewed / approved owner | Cooldown / observation | Follow-through |
|---|---|---|---|---|---|---|
| Governance freeze on `paper` / `canary` | `action_type = "freeze"` + `target_stage in {"paper","canary"}` | `governance` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.3–§7.6 | medium-risk owner chain | 7d / 7d | none by default |
| Governance freeze on `live` with no active runtime | `action_type = "freeze"` + `target_stage = "live"` | `governance` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.5–§7.6 | high-risk committee chain | 14d / 14d | none; no active runtime exists to mitigate |
| Governance freeze on `live` with active runtime | `action_type = "freeze"` + `target_stage = "live"` | `governance` primary; may emit `deployment` and/or `runtime` follow-through | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.5–§7.6 | high-risk committee chain | 14d / 14d | `freeze_stage` deployment command when only stage quarantine is needed; `RollbackCommand` when artifact/runtime mitigation is required |
| Runtime rollback follow-through | no standalone evolution action; emitted from approved freeze / force_risk_off / incident-driven mitigation | `runtime` | `ROLLBACK_AND_POSITION_SEMANTICS.md` §4–§10 | inherits parent approval chain | inherits parent decision window | always uses `RollbackController -> RuntimeManager`; never direct evolution writes |
| Research retrain / revalidate | `action_type = "retrain"` or `"revalidate"` | `research` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.1, §7.3, §7.4 | low-risk owner chain | 3d / 7d | no runtime/deployment mutation |
| Redeploy follow-through | not a standalone `EvolutionDecision.action_type`; downstream deployment action after approved research / freeze-lift outcome | `deployment` | `PAPER_CANARY_LIVE_POLICY.md` §5–§7 | stage-specific deployment owner chain | no new evolution window; deployment stage policy applies | `DispatchCommand(action_type = "redeploy_followthrough")` creates a new `ApprovalDecision` + `DeploymentPlan` |

---

## 5. Follow-through Command Semantics

`dispatch_approved()` emits exactly one primary `DispatchCommand` for the plane that owns the approved action path.

It may additionally emit:

- a `DispatchCommand(action_type = "freeze_stage", execution_plane = deployment)` when a freeze decision needs `current_stage -> frozen`
- a companion `RollbackCommand` when an active runtime needs replacement / flattening

### 5.1 Rollback command defaults

| Trigger | Default rollback action | Escalation |
|---|---|---|
| `freeze` on `live` with unsafe active runtime | `pause_then_replace` | escalate to `liquidate_then_replace` when Risk Policy / Incident Classifier / Kill Switch requires zero exposure |
| `force_risk_off` | `liquidate_then_replace` | none; flatten is already mandatory |

The `RollbackCommand` must be consumed by the **Rollback Controller → Runtime Manager** path. The EvolutionController does not own position treatment; see `ROLLBACK_AND_POSITION_SEMANTICS.md §10`.

### 5.2 Redeploy bridge

`redeploy_followthrough` is intentionally **not** a new `EvolutionDecision.action_type`.

It is a deployment-plane command emitted only when:

1. the parent evolution outcome has already been accepted (`executed`)
2. a new artifact has its own valid `ApprovalDecision`
3. stage policy in `PAPER_CANARY_LIVE_POLICY.md` is satisfied

This keeps the canonical chain intact:

`EvolutionDecision evidence -> ApprovalDecision -> DeploymentPlan -> RuntimeBinding`

### 5.3 Worked incident handoff

Severity-1 incident on an active `live` binding:

1. Incident domain opens `IncidentCase` and later `Postmortem`, carrying binding / plan / artifact refs.
2. Evolution plane proposes and approves `EvolutionDecision(action_type = "freeze", target_stage = "live")` under the high-risk committee chain.
3. `EvolutionController.dispatch_approved()` emits:
   - primary governance `DispatchCommand(action_type = "freeze")`
   - either deployment `DispatchCommand(action_type = "freeze_stage")` when the book may stay in place
   - or companion `RollbackCommand(action_type = "pause_then_replace" | "liquidate_then_replace")` when runtime mitigation is required
4. `EvolutionDecision.executed` records the governance acceptance time and inherits the 14d/14d window.
5. `Rollback Controller` / `Runtime Manager` complete runtime mitigation, then incident / postmortem / audit layers record the downstream outcome without taking over runtime write authority.

---

## 6. Threshold → Proposed Decision Mapping

`ThresholdEvaluator` classifies incoming metric observations into a proposed action path using v1 global defaults.

| Signal Type | Policy Source | Condition | Proposed path |
|---|---|---|---|
| `performance_degradation` | §7.1 | Sharpe < 50% of baseline over 20d | `retrain` |
| `performance_degradation` | §7.1 | Rolling drawdown > 1.25× expected | `retrain` |
| `performance_degradation` | §7.1 | 3 consecutive evaluation windows below baseline | `flag_for_review` / `revalidate` |
| `execution_drift` | §7.2 | Slippage drift > 25% | `revalidate` |
| `execution_drift` | §7.2 | Order reject rate > 1.0% | `tighten_risk_policy`; if active incident opens, may upgrade to `freeze` |
| `execution_drift` | §7.2 | 3 consecutive days of partial fill / timeout anomaly | `flag_for_review` |
| `feature_drift` | §7.3 | PSI > 0.20 | `observe` |
| `feature_drift` | §7.3 | PSI > 0.30 | `revalidate` |
| `feature_drift` | §7.3 | Label mismatch rate > 0.5% | `retrain` |
| `human_correction` | §7.4 | > 3 major corrections in 5 trainer sessions | `retrain` |
| `human_correction` | §7.4 | Same strategy rejected ≥ 2 times in 14d | `flag_for_review` |
| `governance_incident` | §7.5 | Any Severity-1 incident | `freeze` + runtime mitigation |
| `governance_incident` | §7.5 | 2× Severity-2 in 30d for same artifact | stage-aware `freeze` |
| `governance_incident` | §7.5 | unresolved loader / binding / approval mismatch | `freeze` + runtime mitigation when active runtime exists |
| `governance_incident` | §7.6 | drift + drawdown simultaneously exceed threshold | auto-proposed `freeze` |
| `governance_incident` | §7.6 | rollback executed but problem persists | `freeze` + committee review + rollback escalation |

---

## 7. DispatchCommand

`DispatchCommand` is the execution order emitted by `dispatch_approved()` to a downstream plane.

```
decision_id        : str — the approved EvolutionDecision
execution_plane    : ExecutionPlane — governance / runtime / research / deployment
action_type        : str — target-plane verb; usually matches the parent decision action, but may be `freeze_stage` or `redeploy_followthrough` for follow-through commands
target_type        : str — governed target type
target_id          : str — governed target ID
target_version     : str — governed target version snapshot
target_stage       : str | None — paper / canary / live / frozen (when relevant)
cooldown_ends_at   : str — ISO-8601 timestamp
observation_window_ends_at : str — ISO-8601 timestamp
metadata           : dict — additional plane-specific payload
```

---

## 8. RollbackCommand

`RollbackCommand` is a companion command emitted alongside `DispatchCommand` when runtime mitigation is required.

```
decision_id        : str — parent EvolutionDecision
rollback_action_type : str — replace / pause_then_replace / liquidate_then_replace
target_binding_id  : str | None — active RuntimeBinding to mitigate (if known)
capital_pool_id    : str | None — pool in scope
persona_id         : str | None — persona in scope
```

The Rollback Controller consumes this command and routes it to the Runtime Manager per `ROLLBACK_AND_POSITION_SEMANTICS.md §10`.

---

## 9. API Draft

```
POST /api/evolution/proposals
    → creates a proposed EvolutionDecision from a threshold trigger

GET  /api/evolution/proposals/:id
POST /api/evolution/proposals/:id/review
POST /api/evolution/proposals/:id/approve
POST /api/evolution/proposals/:id/reject
POST /api/evolution/proposals/:id/execute
    → moves state and records execution result
```

Events emitted:

```
evolution.proposed
evolution.reviewed
evolution.approved
evolution.rejected
evolution.executed
evolution.rollback_requested
evolution.deployment_followthrough_requested
```

---

## 10. Acceptance Coverage

- [x] Each action path has declared execution plane (governance / runtime / research / deployment)
- [x] Each action path has declared approval owner (reviewer_on_duty / risk_owner / governance_committee)
- [x] Each action path has declared threshold policy source (§7.x section)
- [x] Each action path has declared cooldown and observation window defaults
- [x] Runtime rollback follow-through is declared for freeze-live and force_risk_off paths
- [x] ThresholdEvaluator maps metric signals to proposed action types
- [x] DispatchCommand and RollbackCommand boundary objects defined
- [x] Boundary rules (what controller owns vs. what downstream planes own) are explicit

## 11. Implementation Artifacts

| Artifact | Purpose |
|---|---|
| `services/control-plane/governance/evolution_controller.py` | Executable normal-path router for approved decisions, threshold mapping, follow-through command emission |
| `services/control-plane/governance/test_evolution_controller.py` | Unit coverage for freeze / rollback / retrain / redeploy routing invariants |
| `services/control-plane/governance/smoke_test_evolution_controller.py` | Scriptable smoke verification of the main operational handoff paths |
