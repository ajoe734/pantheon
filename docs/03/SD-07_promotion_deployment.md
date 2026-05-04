# SD-07 — Governance & Promotion / Review Gate、Deployment Planner 與 Rollback 設計

版本：v0.1 Codex-ready draft  
適用範圍：Governance & Promotion Plane、Patch Validators、Review Gates、Approval Decision Store、Promotion Controller、Deployment Planner、Rollback Controller、Execution Loader Checks  
前置依賴：SD-00、SD-01、SD-04 Research Orchestrator、SD-05 Consultation / Red-Team、SD-06 Capital Pool Governance

---

## 1. Purpose

本文件定義 Pantheon 從 `CandidateArtifact` 到 `DeploymentPlan` 的治理與 promotion 設計。

Promotion Plane 的核心責任是防止研究 artifact 直接進入 execution：

```text
CandidateArtifact
→ validators
→ review gates
→ ApprovalDecision
→ DeploymentPlan
→ loader checks
→ RuntimeBinding request
```

此 plane 只產生 governance decision 與 deployment plan；真正 runtime 載入與 broker 行為由 SD-08 Execution Runtime Binding 負責。

---

## 2. Repo ownership

| Repo | Ownership |
|---|---|
| `pantheon` | Primary owner：validators、review gates、approval registry、promotion controller、deployment planner、rollback controller、loader checks。 |
| `front-ai-trading-system` | UI consumer：Review Queue、Approval Detail、Deployment Plan Viewer、Rollback Console。 |
| `pantheon-lean` | Consumes approved deployment plan only through SD-08 boundary。 |
| OpenClaw | May help prepare review context; cannot approve/deploy without Pantheon commands and policy checks。 |

---

## 3. Module paths

### `pantheon`

```text
services/governance/promotion/
  __init__.py
  models.py
  commands.py
  queries.py
  events.py
  validators.py
  gates.py
  approval_store.py
  promotion_controller.py
  deployment_planner.py
  rollback_controller.py
  loader_checks.py
  policies.py
  state_machine.py
  repository.py
  api.py
  tests/

docs/contracts/approval_decision.schema.json
docs/contracts/deployment_plan.schema.json
docs/contracts/loader_report.schema.json
docs/contracts/rollback_plan.schema.json
docs/sd/07_promotion_deployment.md
docs/codex/SD-07_task_packets.md
```

### `front-ai-trading-system`

```text
src/pages/governance/ReviewQueue.tsx
src/pages/governance/ApprovalDecisionDetail.tsx
src/pages/governance/DeploymentPlanDetail.tsx
src/pages/operator/RollbackConsole.tsx
src/types/promotion.ts
src/lib/promotionClient.ts
```

---

## 4. Domain model

### 4.1 `PromotionRequest`

```yaml
PromotionRequest:
  request_id: string
  target_type: enum[candidate_artifact, allocation_artifact, persona_policy_patch, runtime_replace]
  target_id: string
  requested_stage: enum[approval, paper, canary, live, rollback, retire]
  requested_by: actor_ref
  context_refs: string[]
  status: enum[draft, submitted, validating, gate_review, awaiting_approval, approved, rejected, planned, cancelled]
  policy_id: string
  trace_id: string
  created_at: datetime
```

### 4.2 `ValidatorRun`

```yaml
ValidatorRun:
  validator_run_id: string
  request_id: string
  validator_name: string
  target_id: string
  status: enum[passed, warning, failed]
  findings:
    - severity: enum[info, low, medium, high, critical]
      message: string
      evidence_refs: string[]
  blocking: boolean
  executed_at: datetime
```

### 4.3 `ReviewGateResult`

```yaml
ReviewGateResult:
  gate_result_id: string
  request_id: string
  gate_name: string
  required: boolean
  status: enum[pending, passed, failed, waived]
  source_refs: string[]
  waiver_ref: string | null
  evaluated_at: datetime
```

### 4.4 `ApprovalDecision`

```yaml
ApprovalDecision:
  decision_id: string
  request_id: string
  target_type: string
  target_id: string
  decision: enum[approved, rejected, approved_with_conditions]
  approver: actor_ref
  approval_scope:
    allowed_stages: enum[paper, canary, live][]
    allowed_pool_ids: string[]
    expires_at: datetime | null
  risk_note: string
  committee_refs: string[]
  conditions: string[]
  rollback_target: string | null
  effective_at: datetime
  trace_id: string
```

### 4.5 `DeploymentPlan`

```yaml
DeploymentPlan:
  plan_id: string
  approval_decision_id: string
  artifact_id: string
  capital_pool_id: string
  target_mode: enum[paper, canary, live]
  runtime_action: enum[create, replace, restart, scale, rollback]
  runtime_config_ref: string
  rollback_target: string | null
  schedule_window: object | null
  pre_checks: string[]
  post_checks: string[]
  loader_report_id: string | null
  status: enum[draft, checks_pending, ready, submitted_to_execution, executed, failed, cancelled]
  trace_id: string
```

### 4.6 `LoaderReport`

```yaml
LoaderReport:
  loader_report_id: string
  plan_id: string
  artifact_id: string
  capital_pool_id: string
  schema_check: enum[passed, failed]
  lineage_check: enum[passed, failed]
  runtime_compatibility_check: enum[passed, failed]
  pool_policy_check: enum[passed, failed]
  broker_capability_check: enum[passed, failed]
  status: enum[passed, warning, failed]
  blocking_reasons: string[]
  generated_at: datetime
```

### 4.7 `RollbackPlan`

```yaml
RollbackPlan:
  rollback_plan_id: string
  source_plan_id: string
  runtime_binding_id: string
  rollback_target_artifact_id: string | null
  rollback_target_binding_id: string | null
  action: enum[pause, replace, restart, liquidate, safe_mode]
  reason: string
  approval_required: boolean
  status: enum[draft, approved, submitted, executed, failed, cancelled]
```

---

## 5. Commands

| Command | Input | Output | Notes |
|---|---|---|---|
| `SubmitPromotionRequest` | target + requested stage | request_id | Starts validation。 |
| `RunPatchValidators` | request_id | validator results | Schema/lineage/risk checks。 |
| `EvaluateReviewGates` | request_id | gate results | Includes consult, replication, risk, pool admissibility。 |
| `RecordApprovalDecision` | request_id + decision | decision_id | Requires approver RBAC。 |
| `CreateDeploymentPlan` | approval_decision + pool + target_mode | plan_id | Does not execute。 |
| `RunExecutionLoaderChecks` | plan_id | loader_report_id | Must pass before SD-08 execution submission。 |
| `SubmitDeploymentPlanToExecution` | plan_id | execution request ref | Only if ready。 |
| `CreateRollbackPlan` | runtime_binding + reason | rollback_plan_id | May be system-triggered。 |
| `ApproveRollbackPlan` | rollback_plan_id | status=approved | High-risk RBAC。 |
| `CancelPromotionRequest` | request_id | cancelled | Audit required。 |

---

## 6. Queries

| Query | Output |
|---|---|
| `GetPromotionRequest` | request + validators + gates |
| `ListReviewQueue` | pending review items |
| `GetApprovalDecision` | decision detail |
| `ListApprovalHistory` | decisions by target/strategy |
| `GetDeploymentPlan` | plan + loader report |
| `ListDeploymentPlans` | plans by status/pool/stage |
| `GetRollbackPlan` | rollback plan detail |
| `GetPromotionTimeline` | full target timeline |

---

## 7. Events

```yaml
PromotionRequestSubmitted:
  request_id: string
  target_type: string
  target_id: string

PatchValidatorsCompleted:
  request_id: string
  passed: boolean
  failed_validators: string[]

ReviewGatesEvaluated:
  request_id: string
  status: enum[passed, failed, pending]

ApprovalDecisionRecorded:
  decision_id: string
  request_id: string
  decision: string

DeploymentPlanCreated:
  plan_id: string
  artifact_id: string
  capital_pool_id: string
  target_mode: string

LoaderChecksCompleted:
  loader_report_id: string
  plan_id: string
  status: string

DeploymentPlanSubmittedToExecution:
  plan_id: string
  execution_request_ref: string

RollbackPlanCreated:
  rollback_plan_id: string
  runtime_binding_id: string
```

---

## 8. State machines

### 8.1 PromotionRequest state

```text
draft → submitted → validating → gate_review → awaiting_approval → approved → planned
```

Alternative paths:

```text
validating / gate_review / awaiting_approval → rejected
submitted / validating / gate_review / awaiting_approval → cancelled
```

### 8.2 DeploymentPlan state

```text
draft → checks_pending → ready → submitted_to_execution → executed
```

Alternative paths:

```text
checks_pending → failed
ready → cancelled
submitted_to_execution → failed
```

### 8.3 Artifact state vs deployment stage

Do **not** collapse artifact maturity and runtime deployment into one enum.

```text
Artifact state:
draft → candidate → approved_template → deploy_candidate → archived

Deployment stage:
none → paper → canary → live → frozen → retired
```

---

## 9. Hard invariants

1. CandidateArtifact cannot create DeploymentPlan without approved `ApprovalDecision`.
2. DeploymentPlan must reference `capital_pool_id`.
3. DeploymentPlan must reference a target mode: paper, canary, or live.
4. Live DeploymentPlan requires rollback target unless policy grants emergency exception with approval.
5. Live DeploymentPlan requires passed loader checks.
6. Promotion cannot bypass required consult/red-team memo when policy requires it.
7. Pool admissibility failure is blocking unless explicitly waivered by governance override.
8. ApprovalDecision is immutable after recording; use superseding decision for changes.
9. Execution submission must call SD-08 boundary, not direct broker/LEAN command.
10. All waivers must store actor, reason, scope, and expiry.
11. Promotion state transitions must be idempotent.
12. OpenClaw/persona can draft review context but cannot directly record final ApprovalDecision unless explicitly assigned an allowed governance actor role; default deny.

---

## 10. Policy hooks

| Policy | Purpose |
|---|---|
| `promotion_policy` | Required gates by target/stage。 |
| `validator_policy` | Which validators run for artifact/persona/runtime target。 |
| `review_gate_policy` | Required consult memos, replication evidence, risk review。 |
| `approval_policy` | Who can approve which scope。 |
| `deployment_stage_policy` | Paper/canary/live transition rules。 |
| `rollback_policy` | Required rollback target/actions。 |
| `waiver_policy` | Who can waive which gate and for how long。 |
| `loader_check_policy` | Runtime/artifact/pool compatibility rules。 |

Example policy-as-data:

```yaml
promotion_policy:
  id: default_equity_v1
  candidate_to_approved:
    required_gates:
      - lineage_check
      - replication_check
      - redteam_memo
      - risk_review
  approved_to_paper:
    required_gates:
      - pool_admissibility
      - loader_check
  canary_to_live:
    required_gates:
      - canary_reconciliation
      - human_approval
      - rollback_target_check
    allow_waiver: false
```

---

## 11. Storage model

```text
promotion_requests
validator_runs
review_gate_results
approval_decisions
approval_conditions
deployment_plans
loader_reports
rollback_plans
promotion_waivers
promotion_events
promotion_audit_actions
```

---

## 12. API endpoints

```text
POST   /api/promotion/requests
GET    /api/promotion/requests
GET    /api/promotion/requests/{request_id}
POST   /api/promotion/requests/{request_id}/validate
POST   /api/promotion/requests/{request_id}/evaluate-gates
POST   /api/promotion/requests/{request_id}/approval-decisions
GET    /api/promotion/requests/{request_id}/timeline
GET    /api/approval-decisions/{decision_id}
POST   /api/deployment-plans
GET    /api/deployment-plans
GET    /api/deployment-plans/{plan_id}
POST   /api/deployment-plans/{plan_id}/loader-checks
POST   /api/deployment-plans/{plan_id}/submit-to-execution
POST   /api/rollback-plans
GET    /api/rollback-plans/{rollback_plan_id}
POST   /api/rollback-plans/{rollback_plan_id}/approve
POST   /api/rollback-plans/{rollback_plan_id}/submit
```

---

## 13. Integration points

| Integration | Direction | Contract |
|---|---|---|
| SD-01 Registry | read/write | CandidateArtifact, ApprovalDecision, lineage。 |
| SD-04 Research | read | experiment metrics, artifact manifest。 |
| SD-05 Consultation | read | required/published consult memos。 |
| SD-06 Capital Pool | read | admissibility reports, risk policy, broker capability。 |
| SD-08 Execution | command | submit ready DeploymentPlan only。 |
| SD-09 Telemetry | read | paper/canary evidence, reconciliation result。 |
| Console | read/command | review queue, approval, plan, rollback UI。 |

---

## 14. Tests

### Unit tests

- unapproved artifact cannot create deployment plan.
- deployment plan without pool is rejected.
- live plan without rollback target is rejected.
- required consult memo missing causes gate failure.
- waiver requires allowed role and expiry.
- ApprovalDecision immutable after recorded.

### Integration tests

- CandidateArtifact → validation → gates → approval → paper plan → loader checks.
- Pool admissibility failure blocks plan.
- Published red-team memo satisfies required gate.
- Ready deployment plan submits to execution boundary and emits event.

### Regression tests

- artifact_state and deployment_stage remain separate.
- legacy `candidate -> paper -> live` direct transition is rejected.

---

## 15. Definition of Done

1. Promotion request lifecycle is implemented and persisted.
2. Validator and gate results are separate from ApprovalDecision.
3. DeploymentPlan is created only from approved target and pool admissibility.
4. Loader checks are mandatory before execution submission.
5. RollbackPlan exists and is separate from DeploymentPlan.
6. Policy-as-data controls gates and waivers.
7. Tests cover direct-live rejection and artifact/deployment state separation.

---

## 16. Codex task packets

### PTH-SD07-001 — Implement promotion domain models

```text
Repo: ajoe734/pantheon
Target paths:
  services/governance/promotion/models.py
  docs/contracts/approval_decision.schema.json
  docs/contracts/deployment_plan.schema.json
Goal:
  Define PromotionRequest, ValidatorRun, ReviewGateResult, ApprovalDecision, DeploymentPlan, LoaderReport, RollbackPlan.
Acceptance tests:
  - DeploymentPlan requires capital_pool_id
  - ApprovalDecision is immutable after status recorded
  - live plan requires rollback_target by default
```

### PTH-SD07-002 — Implement validators and review gates

```text
Repo: ajoe734/pantheon
Target paths:
  services/governance/promotion/validators.py
  services/governance/promotion/gates.py
  services/governance/promotion/tests/test_gates.py
Goal:
  Run lineage, replication, risk, consult memo, and pool admissibility gates.
Acceptance tests:
  - missing consult memo fails gate
  - failed pool admissibility blocks gate
  - all required gates pass when evidence exists
```

### PTH-SD07-003 — Implement deployment planner

```text
Repo: ajoe734/pantheon
Target paths:
  services/governance/promotion/deployment_planner.py
  services/governance/promotion/tests/test_deployment_planner.py
Goal:
  Create DeploymentPlan from ApprovalDecision and CapitalPool.
Acceptance tests:
  - approved paper plan created
  - unapproved decision rejected
  - live plan without rollback target rejected
  - plan emits DeploymentPlanCreated
```

### PTH-SD07-004 — Implement loader checks and execution submission boundary

```text
Repo: ajoe734/pantheon
Target paths:
  services/governance/promotion/loader_checks.py
  services/governance/promotion/api.py
  services/governance/promotion/tests/test_loader_checks.py
Goal:
  Validate schema, lineage, pool policy, runtime compatibility, and broker capability before execution submission.
Acceptance tests:
  - failed loader check blocks submit-to-execution
  - passed loader check allows status ready
  - submit-to-execution calls execution boundary, not direct broker
```
