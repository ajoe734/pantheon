# Persona Promotion Governance Gap Spec - 2026-07-05

Status: gap spec and execution source of truth
Owner: Codex
Scope: paper-to-canary, canary-to-live, quarterly live ranking, human approval,
recommendation submission, and emergency risk disposal for Persona Fleets.

## Why This Gap Exists

Pantheon already has the PM-12 quarterly ranking read model and advisory
recommendations:

- `GET /bff/management/quarterly-ranking`
- `GET /bff/management/quarterly-ranking/formula`
- `GET /bff/management/quarterly-ranking/recommendations`
- command catalog entry `QuarterlyRankingRecommendationSubmit`

That is not enough for production governance. The current surface can explain
what the model recommends, but the end-to-end management loop is incomplete:

1. Persona creation must enter a runnable `paper_running` state, not a passive
   deployed/draft state that cannot trade in simulation.
2. Ranking/recommendation rows must create persisted promotion reviews, not
   front-end-only local inbox ids.
3. Human approval must be available in Human Inbox / Human Gate with an auditable
   decision result.
4. Approval must not directly mutate live capital. It should authorize the next
   governed deployment/rebalance command.
5. Emergency risk actions must be able to interrupt paper/canary/live operation
   outside the quarterly cycle, but only for containment, not promotion.

## Current-State Audit

Current implemented behavior on the Pantheon BFF side:

- PM-12 quarterly ranking is implemented as a read-only governance advisory
  surface.
- Formula weights are exposed by BFF and traced to governance evidence.
- Recommendations mark `liveCapitalMutation=false` and are intended to route to
  `human_inbox`, `governance_queue`, and `human_gate_decision`.
- `QuarterlyRankingRecommendationSubmit` exists in the BFF v1 command catalog.

Current implemented behavior on the Execute Plans frontend side:

- Persona League and Quarterly Ranking have recommendation buttons.
- Promotion review detail/decision UI exists in Human Gate detail views.
- Frontend paths exist for
  `/bff/management/promotion-reviews/{review_id}/decisions`.

Known production gap:

- Recommendation submission from the frontend still has local-only behavior in
  places and must call a BFF write-gated adapter.
- Dedicated management promotion-review list/detail/decision BFF routes are not
  production-complete.
- There is no single UI path that clearly answers: "paper advanced to canary,
  canary advanced to live, or quarterly live ranking changed only after human
  approval."

## Target Operating Model

Every newly created trading persona starts as `paper_running` once creation is
accepted. It may trade only in paper simulation / broker paper-account mode:

```text
created -> paper_running -> canary_candidate -> canary_running -> live_candidate -> live_running
                         \-> frozen / suspended / retired
```

There is no direct paper-to-full-live path.

`paper_running`, `canary_running`, and `live_running` compete in the same
league/ranking model so the fleet can compare new candidates against active
capital owners. Promotion is gated by stage:

- `paper_running` can be recommended for `canary_candidate`.
- `canary_running` can be recommended for `live_candidate`.
- `live_running` can be recommended for capital increase/decrease, demotion,
  freeze, suspend, or retire.

Human approval is required for:

- paper-to-canary promotion;
- canary-to-live promotion;
- quarterly live capital rank changes;
- quarterly demotion/removal when it affects real capital;
- any override of the formula recommendation.

Emergency controls do not wait for quarterly review:

- S1/S2 incident, forced kill, binding mismatch, loader mismatch, unresolved
  reconciliation anomaly, hard risk breach, or drawdown threshold breach can
  trigger immediate containment.
- Containment actions include freeze, reduce capital, suspend, risk-off,
  liquidate, or rollback.
- Emergency actions never promote a persona or increase capital.

## Recommendation Mechanism

The PM-12 ranking formula remains the source for quarterly recommendations.
Current BFF weights are:

| Component | Weight |
|---|---:|
| PnL | 0.35 |
| Risk | 0.25 |
| Execution | 0.25 |
| Activity | 0.15 |

Recommendation mapping:

| Condition | Advisory action |
|---|---|
| overall >= 85, risk >= 70, execution >= 65 | `promote_to_canary_candidate`, plus research/tool budget recommendations |
| overall >= 70, risk >= 60 | increase research/tool access |
| risk < 55 | reduce capital access |
| execution < 55 or activity < 45 | require retraining |
| overall < 55 | require retraining and reduce capital access |
| overall < 45 | freeze persona |
| overall < 35 | suspend persona |
| overall < 25 | retire persona |

Stage-aware interpretation:

- For `paper_running`, `promote_to_canary_candidate` opens a
  paper-to-canary review.
- For `canary_running`, the same high-score eligibility opens a
  canary-to-live review only if canary sample and incident thresholds pass.
- For `live_running`, ranking recommendations produce capital reallocation,
  demotion, or retention reviews; they do not bypass human approval.

## Promotion Gates

Paper-to-canary follows `PAPER_CANARY_LIVE_POLICY.md`:

- at least 20 trading days, or 10 sessions plus 200 paper orders;
- no unresolved S1/S2 incident;
- loader/runtime integrity issues = 0;
- reconciliation mismatch rate < 1%;
- governance mismatch = 0;
- max drawdown <= 1.2x research expectation;
- slippage deviation < 25%;
- turnover <= 110% strategy limit;
- risk policy breach count = 0;
- rollback target exists;
- Risk Owner reviewed;
- Operator assigned.

Canary-to-live follows `PAPER_CANARY_LIVE_POLICY.md`:

- at least 10 trading days, or 50 real orders;
- no unresolved S1, forced kill, governance mismatch, loader mismatch, or
  binding mismatch;
- realized slippage degradation <= 20%;
- reject rate < 0.5%;
- fill rate >= 90% where applicable;
- exposure tracking error inside strategy tolerance;
- canary max drawdown < 50% of pool kill threshold;
- no hard risk breach;
- no unresolved reconciliation anomaly;
- Reviewer, Risk Owner, and Operator approvals.

## Approval Flow

Production flow:

```text
Quarterly Ranking / Persona League
  -> Recommendation row
  -> Submit recommendation
  -> BFF creates/returns Promotion Review
  -> Human Inbox / Human Gate detail
  -> approve | approve_with_conditions | reject
  -> audit event + idempotent decision receipt
  -> downstream governed deployment/rebalance command
```

Approval semantics:

- `approve`: reviewer accepts the recommendation as-is.
- `approve_with_conditions`: reviewer accepts with explicit constraints,
  expiry, or capital cap.
- `reject`: reviewer blocks the recommendation and must provide rationale.

Decision invariants:

- Submission and approval are auditable.
- Repeated idempotency keys return stable decisions.
- Approver/admin role is required for decisions.
- Operator may submit recommendations but cannot unilaterally approve live
  capital changes.
- Approval creates authorization for the next governed command; it does not
  directly place orders or mutate capital allocations.

## Required Product Surfaces

BFF:

- `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit`
  or equivalent BFF adapter to submit the recommendation into governance.
- `GET /bff/management/promotion-reviews`
- `GET /bff/management/promotion-reviews/{review_id}`
- `POST /bff/management/promotion-reviews/{review_id}/decisions`

Frontend:

- Persona League and Quarterly Ranking must call the BFF submit adapter.
- Successful submit must navigate or deep-link to the returned Human Inbox /
  Promotion Review item.
- Human Gate detail must show recommendation, gate evidence, stage target,
  required approvals, and decision history.
- Local-only deterministic inbox ids are allowed only when BFF real writes are
  disabled and must be visibly reported as disabled/local.

## Production Acceptance

This gap is complete only when:

1. BFF contract tests cover recommendation submit and promotion review
   list/detail/decision.
2. Frontend unit/component tests cover submit success, disabled write mode,
   decision approve, decision approve-with-conditions, and decision reject.
3. Hosted dev smoke proves a recommendation can be submitted and opened in
   Human Inbox / Human Gate.
4. Hosted dev smoke proves approval produces an auditable decision receipt.
5. No test or UI copy claims live capital changed at recommendation submit time.
6. Both Pantheon and Execute Plans PRs are merged to their dev targets.
7. The final closeout records PR numbers, merge SHAs, validation commands, and
   any residual risk.

## Execution Packet

Fleet execution tasks live at:

- `docs/bff/execution-tasks/2026-07-05-persona-promotion-governance-gap/INDEX.md`

Dispatch command:

```sh
python3 scripts/dispatch_persona_promotion_governance_2026-07-05.py
python3 scripts/ai_status.py sync
```
