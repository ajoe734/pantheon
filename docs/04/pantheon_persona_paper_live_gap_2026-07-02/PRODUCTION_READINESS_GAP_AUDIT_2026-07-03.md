# Persona Paper-First Production Readiness Gap Audit

Generated: 2026-07-03

Status: current-state audit and implementation gate

Owner: Codex

## 1. Decision

Pantheon persona creation is paper-first. A user-facing persona create flow is
not complete when it only creates identity, mandate, strategy direction, data
source scope, and risk preference. It is complete only when it reaches a paper
runtime state, or when it returns a repairable setup failure with evidence.

The management system must treat paper, canary, and live personas as one
competition:

```text
paper challenger -> human reviewed canary challenger -> human reviewed live incumbent
```

The system may recommend promotion, replacement, risk reduction, resume, or
retirement. The system must not approve canary, approve live, increase real
allocation, or execute a quarterly replacement without human approval.

Automatic action is reserved for protection only:

```text
pause_new_orders
reduce_exposure
risk_off
frozen
```

## 2. Current Merged State

The repo is not starting from zero. These pieces are already merged and should
be preserved:

| Area | Current evidence | Current status |
|---|---|---|
| Paper create orchestration | `POST /bff/management/personas/paper-launch`, `GET /bff/management/personas/{persona_id}/readiness`, `POST /bff/management/personas/{persona_id}/setup/retry` in `services/control-plane/bff/main.py` | Implemented for BFF local/dev store. Creates identity, paper pool, paper binding, paper deployment plan, paper-only approval, runtime binding, heartbeat session, and persona metadata. |
| Paper launch tests | `services/control-plane/bff/tests/test_pplg_paper_launch.py` | Covers happy path, idempotency replay/conflict, setup failure/retry, and rejection of live/canary pools during paper create. |
| Fleet state projection | `services/control-plane/bff/tests/test_bff_b3_persona_fleet.py` | Paper, canary, and live rows are projected into one fleet. Legacy `deployed` + active paper runtime now appears as `paper_running`. Startup wizard is hidden on runnable rows. |
| Human Inbox projection | `services/control-plane/bff/tests/test_bff_b3_human_inbox.py` | Paper-to-canary readiness blockers appear in Human Inbox as read-only review items. |
| Contract source | `services/control-plane/bff/BFF_API_CONTRACT.md` section 9.11 | Canonical PPLG routes and invariants are declared. |
| Product target spec | `GAP_AND_EXECUTION_PLAN.md` and `EXECUTION_TASKS.md` | Target lifecycle, scoring, review, quarterly, guardrail, and frontend work are specified. |

## 2.1 Branch Delta In This Change Set

This branch implements the first P0 blocker identified by the audit:

| Area | Branch implementation | Remaining boundary |
|---|---|---|
| Promotion review request | `POST /bff/management/personas/{persona_id}/promotion-reviews` creates a durable BFF local `HumanReviewRequest`-shaped record with idempotency. | Currently supports `promotion_to_canary`; canary-to-live should be added after paper-to-canary is stable. |
| Promotion review queue/detail | `GET /bff/management/promotion-reviews` and `GET /bff/management/promotion-reviews/{review_id}` expose pending/decided promotion reviews. | This is still BFF local/dev-store persistence until a governance service owns the canonical store. |
| Promotion review decision | `POST /bff/management/promotion-reviews/{review_id}/decisions` writes an ApprovalDecision-backed approve/reject envelope with idempotency. | Approval authorizes canary activation but does not yet start an external canary trading runtime/order path. |
| Human Inbox actionability | Human Inbox now shows submitted promotion reviews as actionable `promotion_review` items with approve/reject affordances; read-only readiness blockers are suppressed when a real review exists. | Frontend needs to render these new affordances and call the decision route. |

## 3. Non-Negotiable Product Rules

1. Create Persona means Create Paper Persona.
2. A completed create reaches `paper_running` or `paper_warming_up`.
3. A setup that cannot finish becomes `setup_failed` or `repair_required`; it is
   not a normal completed persona.
4. Existing legacy personas with active paper runtime must display as
   `paper_running`, not generic `deployed`.
5. Fleet and League default views compare paper challengers, canary challengers,
   and live incumbents together.
6. Mode/context selectors may affect command affordances and filters, but must
   not split the competition into separate hidden datasets.
7. Paper-to-canary requires human review.
8. Canary-to-live and live allocation increases require human review.
9. Quarterly ranking/replacement requires human review.
10. Automatic guardrails can reduce or stop risk; they cannot promote or
    increase allocation.
11. Every human decision must have an ApprovalDecision-backed audit record.
12. Every state-changing route must be idempotent and must emit traceable
    evidence.

## 4. Route Reality Audit

The PPLG contract declares the following routes. The implementation does not
yet match the whole contract.

| Contract route | Implemented route status | Production readiness |
|---|---|---|
| `POST /bff/management/personas/paper-launch` | Exists | Production-shaped for paper create in BFF local/dev store. Needs frontend as primary create path. |
| `GET /bff/management/personas/{persona_id}/readiness` | Exists | Good projection route. Needs decision linkage once promotion reviews become real records. |
| `POST /bff/management/personas/{persona_id}/setup/retry` | Exists | Good repair route for paper launch failures. |
| `GET /bff/management/personas/evaluations` | Missing as declared | Blocker for a first-class paper evaluation list. |
| `GET /bff/management/personas/{persona_id}/evaluation` | Missing as declared | Blocker for per-persona promotion evidence. Existing `/bff/personas/{id}/evaluations` is not the PPLG management evaluation detail contract. |
| `GET /bff/management/personas/competition-standings` | Missing as declared | Partial capability exists through Persona Fleet and Persona League, but the contract route is not implemented. |
| `POST /bff/management/personas/{persona_id}/promotion-reviews` | Implemented in this branch | Creates paper-to-canary `HumanReviewRequest`-shaped records with idempotency. |
| `GET /bff/management/promotion-reviews` | Implemented in this branch | Lists pending and decided promotion reviews; branch also adds a detail route. |
| `POST /bff/management/promotion-reviews/{review_id}/decisions` | Implemented in this branch | Writes ApprovalDecision-backed decision envelopes and updates persona projection. |
| `GET /bff/management/quarterly-rankings` | Missing as declared | Partial PM-12 implementation exists under singular `/bff/management/quarterly-ranking*`, but not the PPLG plural contract. |
| `POST /bff/management/quarterly-rankings/{proposal_id}/decisions` | Missing | Blocker for governed quarterly replacement/allocation decisions. |
| `GET /bff/management/risk-guardrail-events` | Missing | Blocker for a first-class automatic protection review queue. Risk-off commands and projected risk states exist, but not the PPLG evidence route. |

## 5. Production Blockers

### P0-1. Promotion Review Was Only A Read-Only Projection

Audit-start behavior:

- Fleet can show `requiredHumanReview=promotion_to_canary`.
- Fleet action links to Human Inbox.
- Human Inbox can show a readiness blocker.
- The blocker explicitly has `canDecide=false`, `canApprove=false`, and
  `canReject=false`.

Why this is not production-ready:

- There is no `HumanReviewRequest` record.
- There is no promotion review queue endpoint.
- There is no ApprovalDecision-backed approve/reject mutation for
  paper-to-canary.
- There is no state transition after approval.

Branch fix:

1. Implement `POST /bff/management/personas/{persona_id}/promotion-reviews`.
2. Implement `GET /bff/management/promotion-reviews`.
3. Implement `POST /bff/management/promotion-reviews/{review_id}/decisions`.
4. Make Human Inbox persona review items point to the real review item once it
   exists.
5. On approval, write an ApprovalDecision-backed decision envelope and mark
   canary activation as `authorized_not_started` in persona metadata. On
   rejection, project `promotion_rejected` with rationale.

Acceptance:

- Paper recommendation alone cannot start canary.
- Approved review can produce the next canary deployment intent/binding path.
- Rejected review leaves the persona in paper competition with rejection
  rationale.
- Human Inbox detail can show approve/reject affordances for real promotion
  review items.

### P0-2. Frontend Create Must Call Paper Launch

Current behavior:

- Backend has the correct paper launch command.
- The frontend primary persona create flow still needs to be verified and wired
  so normal user creation cannot stop at identity-only persona.

Why this is not production-ready:

- Users can still reasonably ask whether created personas are complete.
- A visible persona that has no paper pool, no binding, no runtime, and no repair
  path is product-incomplete.

Required fix:

1. Primary create CTA becomes Create Paper Persona.
2. Submit uses `POST /bff/management/personas/paper-launch`.
3. The result screen handles `paper_running`, `paper_warming_up`,
   `paper_provisioning`, `setup_failed`, and `repair_required`.
4. Identity-only draft/persona screens are internal-only or clearly marked as
   incomplete setup artifacts.

Acceptance:

- Creating a persona from the management UI creates paper capital binding and
  runtime, or returns an actionable repair state.
- The UI does not show a startup wizard button for an already runnable persona.

### P0-3. Evaluation And Competition Routes Are Not First-Class

Current behavior:

- Persona Fleet includes `competitionStanding` style data.
- Persona League has PM-12 ranking surfaces.
- The PPLG management evaluation and competition routes are missing.

Why this is not production-ready:

- Promotion review cannot rely on a stable, reviewable evaluation detail route.
- Operators cannot cleanly answer why a paper challenger deserves canary.
- Frontend must compose or infer too much.

Required fix:

1. Implement `GET /bff/management/personas/evaluations`.
2. Implement `GET /bff/management/personas/{persona_id}/evaluation`.
3. Implement `GET /bff/management/personas/competition-standings`.
4. Reuse Fleet/League projections where possible, but expose the contract
   payloads directly.

Acceptance:

- A promotion review has a stable evaluation evidence URL.
- Ranking is one cohort by default across paper/canary/live.
- Filters can narrow by lifecycle, market scope, strategy family, and cohort
  without changing the competition model.

### P0-4. Quarterly Ranking Has Advisory Surfaces But No Decision Closure

Current behavior:

- PM-12 singular routes exist:
  `/bff/management/quarterly-ranking`,
  `/bff/management/quarterly-ranking/formula`, and
  `/bff/management/quarterly-ranking/recommendations`.
- PPLG plural contract routes and decision mutation are missing.

Why this is not production-ready:

- Quarterly recommendation is advisory only.
- Replacement/allocation decisions do not have a PPLG approval decision route.

Required fix:

1. Add PPLG-compatible plural read route as an alias or canonical successor:
   `GET /bff/management/quarterly-rankings`.
2. Add `POST /bff/management/quarterly-rankings/{proposal_id}/decisions`.
3. Require ApprovalDecision-backed approval/rejection before replacement or
   allocation changes.

Acceptance:

- Quarterly ranking can propose but cannot execute by itself.
- Approved/rejected quarterly decisions are auditable and visible in Human
  Inbox/Governance Queue.

### P0-5. Risk Guardrail Events Are Not A First-Class Review Queue

Current behavior:

- Risk-off command validation and Fleet risk projections exist.
- PPLG `RiskGuardrailEvent` schema exists.
- The route `GET /bff/management/risk-guardrail-events` is missing.

Why this is not production-ready:

- Automatic protection actions need a visible evidence trail.
- Operators need a queue of auto-reduced/risk-off/frozen events that can be
  reviewed for resume, retire, or repair.

Required fix:

1. Implement `GET /bff/management/risk-guardrail-events`.
2. Emit or project guardrail events for `pause_new_orders`, `reduce_exposure`,
   `risk_off`, and `frozen`.
3. Ensure every event has `review_required=true`, `may_promote=false`,
   `may_increase_allocation=false`, `incident_id`, and `trace_id`.

Acceptance:

- Guardrail can interrupt immediately.
- Guardrail cannot promote or increase allocation.
- Human review is required before resume after incident.

### P1-1. Fleet Load Time Needs A Slim Projection

Current behavior:

- `GET /bff/management/persona-fleet` composes rows by loading personas,
  persona league, capital pools, runtime bindings, and per-row projection data.
- Human Inbox persona review projection also calls `_build_persona_health_items`.

Why this is risky:

- The Fleet page can feel slow because the BFF builds heavy composed payloads and
  returns nested Fleet, League, capital, runtime, human inbox, OODA, summary, and
  surface metadata in one response.
- Page slicing happens after composition, so the server still does broad work
  before returning page one.

Required fix:

1. Add a slim Fleet list mode for the first viewport.
2. Compute page/filter selection before expensive nested expansions wherever
   possible.
3. Move expensive review/evidence details behind detail routes.
4. Add latency and payload-size regression tests.

Acceptance:

- First page can load without composing all detail payloads.
- Fleet list still exposes state, rank, review status, row action, and critical
  health fields.

## 6. Implementation Order

The correct order is:

1. Close promotion review workflow first.
2. Wire frontend create to paper launch and Human Inbox review actions.
3. Add evaluation and competition standings routes.
4. Add quarterly PPLG decision route.
5. Add risk guardrail event route.
6. Optimize Fleet list payload and latency.
7. Add end-to-end release gate proving:
   - create reaches paper runtime or repair state;
   - paper recommendation cannot start canary without approval;
   - approval produces the next canary-governed transition;
   - quarterly replacement cannot execute without approval;
   - risk guardrail can interrupt without human approval but cannot promote.

## 7. Immediate Code Change Scope

The first code change after this audit should be P0-1:

```text
Implement real promotion review request, queue, and decision routes.
```

That is the highest-value fix because it resolves the user's most visible
confusion:

- "Why is an existing persona still deployed instead of paper_running?"
- "Where do I approve paper to canary?"
- "Why does Human Inbox show a review-looking item that cannot decide?"

Fleet projection already improved the label problem. The remaining production
hole is that review is not yet an executable governance workflow.

## 8. Definition Of Done

This packet is production-ready only when all of the following are true:

1. Primary create flow reaches paper runtime or repair state.
2. Existing legacy paper personas display as `paper_running`.
3. Startup wizard is not shown for runnable personas.
4. Paper/canary/live competition is unified by default.
5. Promotion review request, queue, and decision routes exist.
6. ApprovalDecision is the authority for canary/live/quarterly decisions.
7. Promotion rejection, canary approval, live approval, quarterly approval, and
   guardrail interruption are visible in Human Inbox/Governance surfaces.
8. Evaluation details provide the evidence for promotion and rejection.
9. Guardrail events prove automatic protection is protection-only.
10. Fleet first-page latency and payload size have regression tests.
