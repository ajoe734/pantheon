# Persona Paper-First Live Promotion Gap And Execution Plan

Generated: 2026-07-02

Status: implementation gap packet, ready for review and fleet dispatch

Owner: Codex

Reviewer: Human/Ops, then implementation reviewers per task

## 1. Executive Decision

Pantheon persona creation must be paper-first and complete by default.

A persona that only has identity, mandate, strategy family, source permissions,
and risk appetite is not a completed product object. It is an internal setup
artifact. The user-facing create path must finish at paper runtime or return a
repairable setup failure.

Required product invariant:

```text
Create Paper Persona
  -> Persona identity
  -> paper capital pool binding
  -> paper deployment plan
  -> paper approval
  -> paper runtime binding
  -> paper runtime active
```

Canary and live trading are separate governance decisions. System-generated
ranking may recommend them, but human approval is required before any real-money
state begins or changes allocation.

Automatic actions are allowed only for risk protection:

```text
pause_new_orders
reduce_exposure
risk_off
frozen
```

Automatic actions must not promote, increase live allocation, approve canary,
approve live, or execute quarterly replacement.

## 2. Current Gap

Existing architecture already has the correct primitives:

- Persona lifecycle and capability boundary in `docs/03/SD-02_persona_governance.md`.
- Capital pool and persona binding boundary in `docs/03/SD-06_capital_pool_governance.md`.
- Promotion, approval, deployment plan, and rollback boundary in `docs/03/SD-07_promotion_deployment.md`.
- A five-step onboarding flow in `docs/04/pantheon_persona_onboarding_wizard_2026-05-28/PERSONA_ONBOARDING_WIZARD_SPEC.md`.

The gap is product semantics and orchestration:

- The old create UX allows a normal persona object to stop before paper runtime.
- Persona Fleet does not clearly distinguish paper setup, paper evaluation,
  promotion review, canary, live, and risk-off states.
- There is no canonical evaluation score and cohort ranking contract for paper
  personas.
- There is no first-class human review workflow for canary promotion, live
  promotion, and quarterly rebalance.
- There is no unified automatic risk guardrail contract that can act immediately
  while still creating incident review evidence.
- Existing setup orchestration is documented as frontend-only. That keeps
  atomic records, but it does not satisfy the product requirement that a create
  action completes to paper runtime with idempotent retry and repair semantics.

## 3. Non-Negotiable Rules

1. A completed persona creation means paper runtime is active or explicitly
   warming up.
2. No persona starts canary or live without human approval.
3. Quarterly ranking and live allocation changes require human approval.
4. Loss, drawdown, policy, broker, data, and runtime protection may act
   automatically before human review.
5. Paper execution must simulate cost, slippage, liquidity, and broker
   constraints closely enough for evaluation.
6. Paper and live capital pools are separate. A persona must earn live access
   through evaluation and review.
7. All workflow writes require idempotency, trace IDs, audit events, and
   step-level evidence.
8. A failed setup is `setup_failed` or `repair_required`, not a normal draft
   persona state.
9. Persona authority never grants direct broker authority. Broker actions remain
   behind approved RuntimeBinding and risk policy.
10. UI labels must not imply that a persona is deployed/live when it is only
    provisioned, warming up, or pending review.

## 4. Target State Machine

Use one product lifecycle projection for the management console. It is a
projection over existing domain records, not necessarily a replacement for every
lower-level enum.

```text
paper_provisioning
paper_running
paper_warming_up
paper_ineligible
paper_eligible
promotion_review_pending
promotion_rejected
canary_running
live_review_pending
live_running
quarterly_review_pending
watchlist
auto_reduced
risk_off
frozen
retired
setup_failed
repair_required
```

State meanings:

| State | Meaning | Allowed automatic transition |
|---|---|---|
| `paper_provisioning` | Create workflow is executing identity, binding, plan, approval, and runtime startup. | To `paper_running`, `setup_failed`. |
| `paper_running` | Paper runtime is active and producing decisions/fills/telemetry. | To `paper_warming_up`, `risk_off`, `frozen`. |
| `paper_warming_up` | Evaluation window is accumulating enough evidence. | To `paper_eligible`, `paper_ineligible`, `risk_off`, `frozen`. |
| `paper_ineligible` | Minimum evidence or safety gates failed. | To `paper_warming_up` after more evidence or repair. |
| `paper_eligible` | Score and hard gates qualify for human promotion review. | To `promotion_review_pending` only when a reviewer opens/submits review. |
| `promotion_review_pending` | Human is reviewing canary recommendation. | No automatic approval. |
| `promotion_rejected` | Human rejected promotion, with reasons. | To `paper_warming_up` or `watchlist`. |
| `canary_running` | Approved small real-money run. | To `live_review_pending`, `auto_reduced`, `risk_off`, `frozen`. |
| `live_review_pending` | Human is reviewing full live promotion or allocation change. | No automatic approval. |
| `live_running` | Approved live allocation is active. | To `quarterly_review_pending`, `watchlist`, `auto_reduced`, `risk_off`, `frozen`. |
| `quarterly_review_pending` | Quarterly ranking/rebalance proposal awaits human decision. | No automatic rebalance. |
| `watchlist` | Human or policy flagged underperformance or elevated risk. | To `auto_reduced`, `risk_off`, `frozen` for risk triggers. |
| `auto_reduced` | System reduced exposure for risk protection. | Emits incident review evidence, then waits for human decision. |
| `risk_off` | Trading stopped by risk rule. | Requires human review to resume. |
| `frozen` | Critical policy, compliance, broker, or data incident. | Requires human review to resume or retire. |
| `retired` | Persona no longer participates. | None. |
| `setup_failed` | Create-to-paper setup failed before runtime. | Retry or repair only. |
| `repair_required` | Existing persona has inconsistent lower-level records. | Retry repair only. |

## 5. Create Paper Persona Workflow

The product create path should be a workflow command, not a bare persona insert.

Recommended endpoint:

```text
POST /bff/management/personas/paper-launch
```

Required headers:

```text
Idempotency-Key: <required>
X-Trace-Id: <required or generated>
```

Minimum request:

```yaml
name: string
mandate: string
strategy_family: string[]
market_scope: string[]
source_scope: string[]
risk_profile_id: string
paper_capital_pool:
  mode: enum[select_existing, create_from_template]
  capital_pool_id: string | null
  template_id: string | null
paper_budget: number
artifact_id: string
operator_note: string | null
```

Workflow steps:

1. Create persona identity and policy snapshots.
2. Ensure persona lifecycle permits paper ownership.
3. Select or create an active paper capital pool.
4. Create active `PersonaCapitalBinding` with `role=paper_owner` and
   `deployment_modes=[paper]`.
5. Create paper `DeploymentPlan` bound to the approved artifact and paper pool.
6. Record paper policy approval. Paper approval may be an automated governance
   decision when route, pool, artifact, and risk checks pass because it does not
   grant real-money authority. Human paper approval is required only when policy
   explicitly marks the paper setup as exceptional.
7. Create `RuntimeBinding`.
8. Start paper runtime.
9. Verify runtime appears in runtime-state and initial telemetry heartbeat exists.
10. Emit `PaperPersonaLaunchCompleted` or `PaperPersonaLaunchFailed`.

Workflow response:

```yaml
launch_id: string
status: enum[paper_running, paper_warming_up, setup_failed]
persona_id: string
capital_pool_id: string
binding_id: string
deployment_plan_id: string
approval_decision_id: string
runtime_binding_id: string
runtime_id: string
completed_steps: string[]
failed_step: string | null
retryable: boolean
repair_url: string | null
trace_id: string
```

Implementation rule:

- The endpoint may orchestrate multiple atomic writes, but it must preserve each
  underlying record, action, authorization check, and audit event.
- Replaying the same idempotency key with the same payload must return the same
  `launch_id` and current step state.
- Replaying the same idempotency key with a different payload must return a
  conflict error.

## 6. Paper Evaluation Eligibility

A paper persona is eligible for promotion review only after hard gates pass.

Default minimum gates:

| Gate | Default requirement |
|---|---|
| Evaluation time | At least 14 calendar days or 10 valid market days. |
| Decisions | At least 20 decision events. |
| Paper fills | At least 10 fills. Low-frequency profiles may use 45 calendar days and 5 fills. |
| Cost model | All returns must be after simulated fees, spread, slippage, and liquidity impact. |
| Return | Positive after-cost return or positive alpha vs benchmark. |
| Drawdown | Within persona and pool risk budget. |
| Runtime health | Uptime at least 95%. |
| Data health | Required source freshness at least 98%. |
| Traceability | Every fill traces to signal, artifact, decision rationale, and runtime binding. |
| Policy | No critical policy violation. |
| Broker realism | No fill assumption that violates configured broker/market capabilities. |

Profiles may override thresholds, but overrides must be explicit in
`evaluation_profile_id` and visible in the review packet.

## 7. Promotion Score

System scoring is advisory. It gates recommendation and ranking, but it does not
approve live capital.

Default score:

```text
promotion_score =
  30% performance
+ 20% risk_control
+ 15% consistency
+ 15% execution_realism
+ 10% operational_reliability
+ 10% governance_fit
- penalties
```

Components:

| Component | Inputs |
|---|---|
| `performance` | after-cost return, benchmark alpha, Sharpe, Sortino, hit rate by regime. |
| `risk_control` | max drawdown, downside volatility, loss streak, exposure discipline, tail loss. |
| `consistency` | rolling window alpha, regime stability, contribution breadth, avoiding one-trade dependence. |
| `execution_realism` | slippage delta, turnover cost, liquidity participation, capacity estimate. |
| `operational_reliability` | runtime uptime, data health, recovery behavior, missing heartbeat count. |
| `governance_fit` | mandate fit, source/tool authority fit, trace completeness, no policy override. |

Penalty examples:

- Continued trading after critical data degradation.
- High turnover without enough edge to cover costs.
- Alpha dominated by one extreme event.
- High correlation with existing live book.
- Trading behavior outside mandate.
- Manual override or missing rationale for material decisions.

## 8. Cohort Ranking

Ranking is inside cohorts, not a single global leaderboard.

Cohort key:

```text
market_scope
strategy_family
frequency_profile
risk_budget_profile
capital_pool_type
```

Default interpretation:

| Score/rank | Meaning |
|---|---|
| `< 70` | Not eligible. Stay paper or repair. |
| `>= 70` | Eligible for promotion queue recommendation. |
| `>= 80` and top 20% in cohort | Recommended for human canary review. |
| `>= 85` for two consecutive windows | Priority recommendation. |
| Challenger beats lowest live incumbent by at least 10 score points | Eligible for replacement proposal, still human approved. |

Tie breakers:

1. Lower drawdown.
2. Higher after-cost alpha.
3. Lower correlation to current live book.
4. Higher runtime reliability.
5. Lower turnover/cost burden.
6. Longer stable observation window.

## 9. Human Review Gates

Human review is mandatory for:

- Paper to canary.
- Canary allocation increase.
- Canary to full live.
- Live allocation increase.
- Quarterly rebalance.
- Replacing a live incumbent.
- Resuming from `risk_off` or `frozen`.

The system may generate a recommendation packet:

```yaml
review_id: string
review_type: enum[promotion_to_canary, canary_to_live, quarterly_rebalance, resume_after_incident, retire]
persona_id: string
cohort_id: string
recommendation: enum[approve, approve_with_conditions, reject, reduce, retire]
recommended_allocation: number | null
evidence_refs: string[]
risk_notes: string[]
blocking_findings: string[]
score_snapshot_id: string
created_at: datetime
expires_at: datetime
```

The human decision must record:

```yaml
decision: enum[approved, approved_with_conditions, rejected]
approver: actor_ref
approval_scope:
  allowed_stage: enum[canary, live]
  allowed_pool_ids: string[]
  max_allocation: number
  expires_at: datetime
conditions: string[]
risk_note: string
rollback_target: string | null
trace_id: string
```

## 10. Canary And Live Allocation

Canary is real-money and requires human approval.

Default canary limits:

- Initial canary allocation is at most 2% of the live pool.
- Initial canary allocation is at most 10% of the target full-live allocation.
- One persona cannot exceed 25% of the cohort canary budget.

Canary must observe at least:

- 7 calendar days or 5 valid market days.
- 5 live fills, unless approved as a low-frequency exception.
- Real slippage within approved tolerance.
- No canary risk budget breach.
- No order, broker, compliance, runtime, or data incident.

Full live default:

- First full-live approval maxes at 5% of the pool unless the human decision
  explicitly authorizes more.
- After two stable review windows, allocation may be increased to 10%.
- One strategy family should not exceed 30% of pool exposure without explicit
  committee approval.
- Highly correlated personas share one risk bucket.

## 11. Quarterly Review

Every quarter, the system computes rankings and proposals. It does not rebalance
without human approval.

Quarterly flow:

```text
quarter_end
  -> compute cohort rankings
  -> compare live incumbents and paper/canary challengers
  -> create rebalance proposal
  -> quarterly_review_pending
  -> human approval/rejection
  -> approved actions become deployment/allocation plans
```

Quarterly human decisions may:

- Keep allocation unchanged.
- Increase allocation.
- Reduce allocation.
- Move to watchlist.
- Replace an incumbent with a challenger.
- Return persona to paper.
- Retire persona.
- Keep risk-off state.

Replacement rule:

- A challenger must pass hard gates and beat the relevant live incumbent by at
  least 10 points, or the proposal must include a human-readable exception.
- Replacement is a proposal, not an automatic action.

## 12. Automatic Risk Guardrails

Automatic guardrails protect capital and platform integrity. They are not
promotion or allocation authorities.

Default triggers:

| Trigger | Automatic action | Follow-up |
|---|---|---|
| Daily loss exceeds daily risk budget | `pause_new_orders` | Incident review required. |
| Drawdown exceeds max drawdown risk-off threshold | `risk_off` | Human approval required to resume. |
| Exposure exceeds pool/persona limit | `reduce_exposure` | Incident review required. |
| Actual slippage materially exceeds paper/live assumption | `pause_new_orders` or `reduce_exposure` | Review execution realism. |
| Repeated order rejects or broker errors | `pause_new_orders` | Broker/runtime incident review. |
| Required data freshness below threshold | `pause_new_orders` for dependent strategy | Data incident review. |
| Runtime heartbeat lost | `pause_new_orders` | Runtime incident review. |
| Critical policy violation | `frozen` | Human governance review required. |
| Live book correlation spike over hard limit | `reduce_exposure` | Quarterly or emergency review. |

Each automatic action must emit:

```yaml
RiskGuardrailEvent:
  event_id: string
  persona_id: string
  runtime_binding_id: string
  capital_pool_id: string
  trigger_name: string
  observed_value: number | string
  threshold: number | string
  automatic_action: enum[pause_new_orders, reduce_exposure, risk_off, frozen]
  effective_at: datetime
  incident_id: string
  review_required: true
  trace_id: string
```

## 13. Management Console Requirements

Persona creation:

- Primary CTA: `建立 Paper Persona`.
- No normal user path should leave a persona as identity-only.
- Failed setup shows the failed step and a retry/repair action.

Persona Fleet columns:

- `Paper Runtime`: provisioning, running, warming up, failed, risk-off.
- `Evaluation`: warming up, ineligible, eligible, score, cohort percentile.
- `Review`: none, promotion pending, live pending, quarterly pending, rejected.
- `Capital Scope`: paper, canary, live, risk-off.
- `Live Status`: none, canary, live, watchlist, auto-reduced, frozen.

Row actions:

| Row state | Primary action |
|---|---|
| `setup_failed` | `修復 Paper 建立` |
| `paper_running` or `paper_warming_up` | `查看 Paper 評選` |
| `paper_eligible` | `送交實盤審核` |
| `promotion_review_pending` | `查看審核` |
| `canary_running` | `查看 Canary` |
| `live_running` | `查看 Live Runtime` |
| `quarterly_review_pending` | `查看季度重排` |
| `risk_off` or `frozen` | `查看事件審核` |

Do not show `啟動精靈` for already runnable personas. Use concrete state and
action labels.

## 14. Data And API Gaps

Required new or updated contracts:

- `PaperPersonaLaunch` workflow command and step state.
- `PersonaReadinessProjection` for create/setup status.
- `PaperEvaluationSnapshot`.
- `PromotionScoreSnapshot`.
- `CohortRankingSnapshot`.
- `HumanReviewRequest`.
- `QuarterlyRebalanceProposal`.
- `RiskGuardrailEvent`.
- Fleet summary DTO without duplicated payload fields.

Required endpoint families:

| Endpoint | Purpose |
|---|---|
| `POST /bff/management/personas/paper-launch` | User-facing create-to-paper workflow. |
| `GET /bff/management/personas/{id}/readiness` | Step status, repair info, paper runtime status. |
| `POST /bff/management/personas/{id}/setup/retry` | Idempotent retry from failed step. |
| `GET /bff/management/personas/evaluations` | Evaluation list by cohort/status. |
| `GET /bff/management/personas/{id}/evaluation` | Score, gates, ranking, evidence. |
| `POST /bff/management/personas/{id}/promotion-reviews` | Submit recommendation for human review. |
| `GET /bff/management/promotion-reviews` | Human review queue. |
| `POST /bff/management/promotion-reviews/{id}/decisions` | Human approval/rejection. |
| `GET /bff/management/quarterly-rankings` | Quarterly cohort rankings and proposals. |
| `POST /bff/management/quarterly-rankings/{id}/decisions` | Human quarterly approval/rejection. |
| `GET /bff/management/risk-guardrail-events` | Automatic action and review queue. |

## 15. Acceptance Gates

The implementation is not complete until these are true:

- Creating a persona through the primary UI results in `paper_running` or a
  visible `setup_failed` with retry information.
- No identity-only persona is shown as a completed normal object.
- A paper persona cannot enter canary or live without a human decision record.
- Quarterly ranking generates a proposal but cannot alter allocation without a
  human decision record.
- Automatic risk rules can pause/reduce/risk-off/freeze without waiting for
  human approval, and always create incident review evidence.
- Fleet list is clear and fast enough for operator use. It must not ship
  duplicate large payload branches for the same rows.
- Tests prove no canary/live route executes from paper recommendation alone.
- Tests prove risk-off triggers can interrupt live/canary runtime immediately.

## 16. Execution Task Set

The implementation is split into eight dispatchable tasks:

| Task | Lane | Purpose |
|---|---|---|
| `PPLG-001` | Architecture/contracts | Canonical state, schemas, and API contract alignment. |
| `PPLG-002` | Backend orchestration | Create-to-paper launch workflow and retry. |
| `PPLG-003` | BFF/read model | Fleet/readiness DTO and performance cleanup. |
| `PPLG-004` | Evaluation/ranking | Paper eligibility, score, and cohort ranking engine. |
| `PPLG-005` | Governance | Human promotion, live, and quarterly review workflows. |
| `PPLG-006` | Risk/runtime | Automatic guardrail actions and incident review evidence. |
| `PPLG-007` | Frontend | Paper persona create flow and Fleet state/action UX. |
| `PPLG-008` | Verification | End-to-end release gate and fleet closeout evidence. |

Detailed task packets live in:

- `docs/bff/execution-tasks/2026-07-02-persona-paper-live-gap/`
- `docs/04/pantheon_persona_paper_live_gap_2026-07-02/EXECUTION_TASKS.md`
