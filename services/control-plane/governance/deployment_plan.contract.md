# DeploymentPlan Governance Contract

Last updated: 2026-04-10  
Task: `DEP-001`  
Owner: Codex  
Reviewer: Qwen  
Status: APPROVED canonical contract

---

## 1. Purpose

`DeploymentPlan` is the canonical governed object that turns an already-approved
artifact into an explicit deployment-stage transition.

It answers:

- which approved artifact is being deployed
- for which capital pool
- from which current stage to which target stage
- with which runtime action
- with which rollback linkage

This object sits between:

`ApprovalDecision -> DeploymentPlan -> RuntimeBinding`

It is immutable after creation. If the operator wants a different target stage,
rollback target, or runtime action, they must abort the existing plan and create
a replacement plan.

---

## 2. Ownership

### Write owner
- Governance / promotion plane

### Consumers
- deployment orchestrator
- runtime-manager
- artifact-loader execution projection builder
- telemetry / lineage read models

### Source-of-truth split
- `ApprovalDecision` answers whether the artifact may proceed
- `DeploymentPlan` answers how stage transition should happen
- `RuntimeBinding` answers what is actually running

---

## 3. Canonical Fields

Machine-readable schema:

- `services/control-plane/governance/deployment_plan.schema.json`

Python implementation:

- `services/control-plane/governance/deployment_plan.py`

Minimum fields:

| Field | Required | Meaning |
|---|---|---|
| `plan_id` | yes | immutable deployment plan id |
| `approval_decision_id` | yes | governing approval object |
| `artifact_id` | yes | approved registry artifact id |
| `artifact_version` | yes | approved registry version |
| `artifact_type` | yes | deployable artifact class |
| `strategy_id` | yes | strategy family id |
| `capital_pool_id` | yes | pool receiving the deployment |
| `current_stage` | yes | current derived deployment stage |
| `target_stage` | yes | requested deployment stage |
| `transition_type` | yes | `activate`, `promote`, `rollback`, `freeze`, or `resume` |
| `runtime_action` | yes | runtime-manager action to execute |
| `rollback` | required for active targets | explicit fallback linkage |
| `scale` | yes in practice | capital / gross scaling envelope |
| `schedule_window` | no | bounded execution window |
| `pre_checks[]` | no | checks before orchestration |
| `post_checks[]` | no | checks after orchestration |
| `status` | yes | `draft`, `approved`, `executing`, `executed`, `aborted`, `rejected`, `failed` |

---

## 4. Stage Planner Rules

Allowed transitions:

| Current | Target | Transition Type | Default Runtime Action |
|---|---|---|---|
| `none` | `paper` | `activate` | `deploy_new_binding` |
| `paper` | `canary` | `promote` | `replace_binding` |
| `canary` | `live` | `promote` | `replace_binding` |
| `paper` | `frozen` | `freeze` | `freeze_binding` |
| `canary` | `frozen` | `freeze` | `freeze_binding` |
| `live` | `frozen` | `freeze` | `freeze_binding` |
| `frozen` | `paper` | `resume` | `resume_binding` |
| `frozen` | `canary` | `resume` | `resume_binding` |
| `frozen` | `live` | `resume` | `resume_binding` |
| `canary` | `paper` | `rollback` | explicit rollback action |
| `live` | `canary` | `rollback` | explicit rollback action |
| `live` | `paper` | `rollback` | explicit rollback action |

Forbidden examples:

- `none -> canary`
- `none -> live`
- `paper -> live`
- any no-op transition where `current_stage == target_stage`

---

## 5. Rollback Linkage

Rollback is not implied. It must be explicit in the plan.

`rollback` shape:

| Field | Required | Meaning |
|---|---|---|
| `target_artifact_id` | yes | approved fallback artifact |
| `target_version` | yes | fallback version |
| `action_type` | yes | `replace`, `pause_then_replace`, or `liquidate_then_replace` |
| `reason` | no | operator / policy reason |
| `verified_at` | no | when the fallback was last verified |

Rules:

- every plan targeting `paper`, `canary`, or `live` must carry rollback linkage
- rollback targets cannot point at the same artifact/version as the forward plan
- rollback transitions must use one of the three rollback-aware runtime actions from `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `rollback.action_type` is the position-treatment semantic (`replace`, `pause_then_replace`, `liquidate_then_replace`), while `runtime_action` remains the runtime-manager execution verb (`replace_binding`, `pause_then_replace`, `liquidate_then_replace`)

---

## 6. Policy Defaults

The stage planner encodes v1 defaults from `PAPER_CANARY_LIVE_POLICY.md`.

### Paper
- `capital_scale_pct = 0`
- `gross_scale_pct = 100`

### Canary
- `capital_scale_pct = 5`
- `gross_scale_pct = 25`

### Live
- `capital_scale_pct = 100`
- `gross_scale_pct = 100`

### Frozen
- both scale percentages = `0`

Hard guards:

- `canary.capital_scale_pct` must not exceed `5`
- `canary.gross_scale_pct` must not exceed `25`
- `paper.capital_scale_pct` must be `0`
- `frozen` must zero both percentages

---

## 7. ApprovalDecision Integration

Before a plan is created, the planner must verify:

1. `ApprovalDecision.decision_state == decided`
2. `ApprovalDecision.decision in {approved, approved_with_conditions}`
3. `ApprovalDecision.target_id == registry_entry.registry_id`
4. `ApprovalDecision.target_version == registry_entry.version`
5. `ApprovalDecision.capital_pool_id`, if set, matches `DeploymentPlan.capital_pool_id`
6. `ApprovalDecision.persona_id`, if set, matches `DeploymentPlan.sponsor_persona_id`
7. the registry entry resolves to canonical `artifact_state=approved`

This means deploy planning no longer depends on a loose `approver` string.

---

## 8. Execution Projection

The plan is also the canonical source for deployment-stage projection.

Projection fields emitted to execution metadata:

- `artifact_state`
- `deployment_stage`
- `approval_decision_id`
- `deployment_plan_id`
- `capital_pool_id`
- `runtime_action`
- `rollback`

Compatibility note:

- legacy `promotion_state` remains a temporary alias only for `paper` and `live`
- `canary` and `frozen` are canonical deployment stages without a legacy alias

---

## 9. Relationship to Other Canonical Docs

- `PAPER_CANARY_LIVE_POLICY.md` defines threshold and scale policy
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` defines object ownership and write authority
- `ROLLBACK_AND_POSITION_SEMANTICS.md` defines rollback action semantics
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` defines orchestration and compensation

---

## 10. Review Outcome

Reviewer approval was recorded in:

- `services/control-plane/governance/review_dep001_qwen_approved_zh.md`

Verified points:

- allowed stage transitions are strict enough for `paper` / `canary` / `live` / `frozen`
- rollback linkage is explicit enough for `DEP-002` saga compensation
- execution projection fields are sufficient for `EX-001` / runtime-manager migration
