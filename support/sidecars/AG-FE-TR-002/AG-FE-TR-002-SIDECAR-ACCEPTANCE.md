# AG-FE-TR-002 Acceptance and Dependency Map (Sidecar)

**Parent task:** `AG-FE-TR-002` - Candidate review and entry/position/exit queues  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Parent status at packet time:** `in_progress`  
**Sidecar task:** `AG-FE-TR-002-SIDECAR-ACCEPTANCE`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Claude`  
**Helper kind:** `acceptance_packet`  
**Generated:** `2026-06-22`  
**Mutates canonical truth:** `no`

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance implementation, or execute-plans
frontend code. It packages the acceptance checklist and dependency map for the
parent owner to use while implementing `CandidateReviewDrawer` and
`TradeDecisionCard`.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## 1. Executive Summary

`AG-FE-TR-002` should implement the frontend review surfaces that sit on top of
already-merged Agora candidate, Trading Room, and governed intent contracts:

1. `CandidateReviewDrawer` must show the A2 score decomposition, not a single
   score.
2. Entry/add/reduce/exit/review queue cards must expose the full
   `TradingDecisionEvent` decision-support fields before any trader action is
   recorded.
3. Candidate review actions must call the canonical candidate-pool routes from
   the v1.4 contract.
4. Trading event decisions must go through the request/intent path from
   `AG-BE-TR-001` and governed handoff path from `AG-BE-TR-002`; the UI must
   never present order placement, capital binding, or RuntimeBinding writes.
5. Any field, enum, route, or layout not backed by the cited design/spec/source
   should be treated as a parent blocker, not filled in by invention.

## 2. Source References

| Source | Relevant finding |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-002-SIDECAR-ACCEPTANCE` | Sidecar is active, owner `Codex`, reviewer `Claude`, helper kind `acceptance_packet`, artifact path is this file. |
| `.orchestrator/task-briefs/ag_fe_tr_002_sidecar_acceptance.md` | Sidecar scope is support-only: prepare acceptance checklist, dependency map, and support packet; do not edit canonical truth. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-002` | Parent is `in_progress`; owner `Claude`, reviewer `Codex`; artifacts are `CandidateReviewDrawer.tsx` and `TradeDecisionCard.tsx`; hard constraints are no invented schema/route/enum and design-locked UI. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001` | Dependency is `done`; Trading Room page and `tradingRoom.ts` client are merged, including contract-backed `dashboard_recipe_id` recipe loading and expanded decision event fields. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-CP-001` | Dependency is `done`; candidate pool persistence/routes and A2 score components are merged in PR #2181. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Dependency is `done`; v1.3 OpenAPI/schema bundle is merged and frozen-file guardrails passed. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-CP-001` | Indirect contract dependency is `done`; v1.4 candidate-pool routes and v5 schemas are merged in PR #2179. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001` | Backend Trading Room aggregate/event queues are `done`; event schema alignment, no-order proof, and pagination fixes were reviewed. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-002` | Governed TradingIntent/handoff is `done`; canary/live remain request-only, idempotency headers are required, and no broker order route exists. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A2_candidate_scoring_recipe_spec.md` | A2 score display must include raw score, confidence, risk/penalty, effective score, component weights/contributions/evidence, missing/cap reasons, and recipe version. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md` | Trading Room may record decisions and create request-only intents, but must not write orders, RuntimeBinding, capital binding, or promotion approval. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Defines Trading Room decision-event routes and governed trading-intent handoff routes. |
| `services/control-plane/openapi/agora_v1_4.openapi.yaml` | Defines candidate pool/list/detail/score/member/review/discussion/monitoring routes. |
| `services/control-plane/specs/agora/v5/candidate_score_result.schema.json` | Required score fields are snake_case: `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`, plus `components`, `blockers`, `data_cutoff`, and `scored_at`. |
| `services/control-plane/specs/agora/v5/candidate_member_review.schema.json` | Valid review decisions are `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, and `reject`. |
| `services/control-plane/specs/agora/v4/trading_decision_event.schema.json` | Decision events require confidence, probability, expected value, rationale, risk notes, evidence refs, invalidation, suggested action, and `no_order_route_proof`. |
| `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | Handoffs require `no_order_route_proof: "agora_request_only_no_order_route"` and are request-only governance submissions. |
| `services/control-plane/specs/agora/trading_intent.schema.json` | TradingIntent records require `no_order_route_proof: "agora_intent_record_only"`. |

## 3. Dependency Map

| Dependency | State | What parent can rely on | Parent caution |
|---|---|---|---|
| `AG-FE-TR-001` | `done`, archived `2026-06-22T12:18:52Z` | Trading Room route/switcher and `tradingRoom.ts` base client exist in execute-plans; final review confirmed contract-backed dashboard recipe loading and expanded v4 decision event display. | Parent should extend existing Trading Room/client patterns rather than create a parallel route, client, or data model. |
| `AG-BE-CP-001` | `done`, archived `2026-06-22T02:35:20Z` | Candidate pool BFF implementation is available; A2 score computation/components and rejected negative-example retention were reviewed. | Use schema-backed v1.4 candidate routes. Do not use stale pre-v1.4 route guesses or camelCase score field names. |
| `AG-XR-OPENAPI-004` | `done`, archived `2026-06-21T13:30:08Z` | v1.3 Trading Room and governed intent routes/schemas are live, with frozen v1/v1.1/v1.2 intact. | v1.3 does not define candidate-pool routes; candidate review comes from the later v1.4 contract. |
| `AG-XR-CP-001` | `done`, archived `2026-06-22T02:13:08Z` | v1.4 candidate-pool OpenAPI and v5 schemas define pool, score, member review, discussion, and monitoring surfaces. | It is an indirect dependency through `AG-BE-CP-001`, but parent frontend must still consume its contract. |
| `AG-BE-TR-001` | `done`, archived `2026-06-22T03:22:30Z` | Backend Trading Room aggregate/event queues and decision-event routes are available; `approve`/`modify` decisions create TradingIntent records, not orders. | Parent UI must keep decisions inside the BFF client and should refresh backend state after writes. |
| `AG-BE-TR-002` | `done`, archived `2026-06-22T07:42:08Z` | Governed TradingIntent/handoff routes are available; canary/live are request-only and idempotency/If-Match headers are required. | Parent must not expose direct execution labels or any broker/RuntimeBinding/capital binding control. |

Downstream task to keep separate:

| Downstream | Boundary |
|---|---|
| `AG-E2E-TR-001` | E2E should prove the integrated Trading Room/candidate review/dashboard flow after parent implementation and review. This sidecar is not E2E proof. |

## 4. Parent Acceptance Checklist

| Parent acceptance target | Evidence to gather during parent run | Pass condition |
|---|---|---|
| Candidate review uses canonical routes | Inspect `execute-plans/src/lib/bff-v1/agora/` and parent component calls. | Candidate list/detail/score/review/discussion/monitoring use `/bff/agora/candidate-pools*` v1.4 routes; no invented endpoint or local fixture fallback. |
| Score display is decomposed | Inspect `CandidateReviewDrawer.tsx` and tests. | UI shows `raw_score`, `penalty_score` or risk penalty, `evidence_confidence`, `effective_score`, recipe id/version, data cutoff, rank/band, and all component rows. |
| Component rows are complete | Inspect drawer rendering and test fixtures. | Each score component exposes category, raw/normalized value when available, transform, direction, weight, contribution, missing policy/reason, evidence refs, and explanation if present. |
| Missing/capped/suppressed score state is visible | Inspect treatment of `blockers`, `band`, null component values, and missing policies. | Critical missing data cannot look like a precise high score; suppressed/park/needs_research states are visible and reviewable. |
| Candidate review verbs match v1.4 schema | Inspect action controls and request body typing. | Only `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, and `reject` are sent to `reviewCandidatePoolMember`. |
| Rejected or parked candidates are preserved | Inspect UI copy/state refresh after review. | Reject/park is shown as a recorded review decision or negative example path, not as a hard delete or silent removal. |
| Queue cards cover all event kinds | Inspect `TradeDecisionCard.tsx` and queue filtering. | Entry, add, reduce, exit, and review cards render from `TradingDecisionEvent.event_kind` without a separate local event taxonomy. |
| Decision support fields are complete | Inspect cards/details and tests. | Cards or their expandable detail show confidence with basis/calibration, probability with target/horizon/interval, gross/cost/net/downside EV with unit/horizon, rationale, risk notes, evidence refs, invalidation, suggested action, suggested size, data cutoff, and no-order proof. |
| Decision actions stay request-only | Inspect decision buttons and `tradingRoom.ts` client calls. | `approve`, `reject`, `defer`, and `modify` call `decideAgoraTradingEvent`/client wrapper only; UI never calls broker, RuntimeBinding, or capital-binding paths. |
| Governed intent/handoff labels are safe | Inspect any handoff CTA text. | Shadow/paper/canary/live labels match "Start shadow", "Request paper validation", "Submit canary review request", and "Submit live review request"; no "execute", "place order", or "go live" copy. |
| Write headers are supplied | Inspect client wrapper tests for decision/review/handoff calls. | `If-Match`, `Idempotency-Key`, and `X-Request-Id` are passed where the OpenAPI requires them. |
| Live strict fallback posture is preserved | Inspect data-loading error states. | Typed BFF errors are surfaced; frontend does not synthesize successful candidate reviews, decision events, intents, or handoffs from fixtures when live BFF is unavailable. |
| Parent tests cover the contract shape | Inspect focused UI/client tests. | Tests exercise v1.4 candidate score/review fields and v1.3 TradingDecisionEvent fields using snake_case schema names and no-order proof literals. |

## 5. Route and Schema Guardrails

### 5.1 Candidate Pool Route Family

Parent frontend work should bind to the v1.4 candidate route family:

| Purpose | Route | OperationId |
|---|---|---|
| List pools | `GET /bff/agora/candidate-pools` | `listCandidatePools` |
| Create pool | `POST /bff/agora/candidate-pools` | `createCandidatePool` |
| Pool detail | `GET /bff/agora/candidate-pools/{pool_id}` | `getCandidatePool` |
| Score results | `GET /bff/agora/candidate-pools/{pool_id}/score` | `getCandidatePoolScore` |
| Trigger score | `POST /bff/agora/candidate-pools/{pool_id}/score` | `triggerCandidatePoolScore` |
| List members | `GET /bff/agora/candidate-pools/{pool_id}/members` | `listCandidatePoolMembers` |
| Member detail | `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}` | `getCandidatePoolMember` |
| Review member | `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review` | `reviewCandidatePoolMember` |
| Pool discussions | `GET/POST /bff/agora/candidate-pools/{pool_id}/discussions` | `listCandidatePoolDiscussions` / `createCandidatePoolDiscussion` |
| Member discussions | `GET/POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | `listCandidateMemberDiscussions` / `createCandidateMemberDiscussion` |
| Monitoring list | `GET /bff/agora/candidate-pools/{pool_id}/monitoring` | `listCandidatePoolMonitoring` |
| Add/remove monitoring | `POST/DELETE /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor` | `addCandidateToMonitoring` / `removeCandidateFromMonitoring` |

### 5.2 Score Fields

The frontend should use snake_case fields from `candidate_score_result.schema.json`:

```text
raw_score
penalty_score
evidence_confidence
effective_score
recipe_id
recipe_version
components
blockers
data_cutoff
scored_at
```

Do not use camelCase copies from descriptive OpenAPI text unless generated
types explicitly map them. The schema required fields are the acceptance source.

### 5.3 Candidate Review Decisions

The valid `CandidateMemberReview.decision` values are:

```text
approve_for_monitoring
send_to_shadow
needs_more_research
park
reject
```

Do not use the older round2 D8 verbs (`add_to_monitoring`, `remove`,
`request_research`, `start_shadow`, `create_entry_watch`) as request body enum
values. They are useful design intent, but v1.4 schema is the landed contract.

### 5.4 Trading Room Decision Events

The parent queue card/detail implementation should preserve the v1.3/v4 event
shape:

```text
event_kind: entry | add | reduce | exit | review
suggested_action: enter | add | reduce | exit | review | no_action
no_order_route_proof: agora_decision_support_only
```

Required support fields include `confidence`, `probability`, `expected_value`,
`rationale`, `risk_notes`, `evidence_refs`, `invalidation`, and
`suggested_size` when present.

### 5.5 Governed Intent and Handoff

TradingIntent and handoff proof literals are separate:

| Record | Proof literal |
|---|---|
| `TradingIntent` | `agora_intent_record_only` |
| `GovernedIntentHandoff` | `agora_request_only_no_order_route` |

The UI may request shadow/paper/canary/live review, but it must not imply that
Agora has executed a trade, bound capital, or written a RuntimeBinding.

## 6. Suggested Parent Verification Commands

Run frontend commands from the execute-plans checkout or task worktree used by
the parent owner. Run Pantheon contract spot checks from this repo.

```bash
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-CP-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-002
```

From the execute-plans checkout:

```bash
rg -n "candidate-pools|reviewCandidatePoolMember|CandidateScoreResult|raw_score|effective_score" \
  src/lib/bff-v1/agora src/agora

rg -n "approve_for_monitoring|send_to_shadow|needs_more_research|negative_example_tags" \
  src/lib/bff-v1/agora src/agora

rg -n "TradingDecisionEvent|confidence|probability|expected_value|invalidation|no_order_route_proof" \
  src/agora

rg -n "placeOrder|broker|RuntimeBinding|capital binding|go live|execute trade|place order" \
  src/agora src/lib/bff-v1/agora
```

From this Pantheon repo:

```bash
jq '.required' services/control-plane/specs/agora/v5/candidate_score_result.schema.json
jq '.properties.decision.enum' services/control-plane/specs/agora/v5/candidate_member_review.schema.json
jq '.properties.no_order_route_proof.enum' services/control-plane/specs/agora/v4/trading_decision_event.schema.json
jq '.properties.no_order_route_proof.enum' services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json
```

Recommended focused frontend validation once parent implementation exists:

```bash
npm test -- src/agora
npm run build:agora
```

## 7. Review Guardrails

| Reviewer should reject | Reason |
|---|---|
| Candidate review shows only one score or one badge | A2 explicitly requires decomposition and says not to replace it with a single score/star. |
| Candidate review request body uses old D8 verbs | The landed v1.4 schema uses `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, `reject`. |
| Frontend invents candidate score fields or camelCase-only schema | Parent brief forbids invented fields; v5 schema required fields are snake_case. |
| Queue cards omit probability, EV breakdown, risk, evidence, or invalidation | Trading Room decision support requires these fields before trader decisions. |
| Any UI path routes directly to broker/order/capital/RuntimeBinding writes | Agora is decision support and request-only; backend tasks enforce no-order proof. |
| Canary/live CTA implies execution | D7 wording is review request only; canary/live are not direct actions from Agora. |
| Fixture fallback claims success in live strict mode | Parent must surface typed BFF unavailable/error states instead of synthetic success. |
| Parent broadens canonical docs/schemas/OpenAPI from this frontend task | This sidecar and parent frontend work must consume accepted contracts or block for clarification. |

## 8. Sidecar Acceptance Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates `support/sidecars/AG-FE-TR-002/AG-FE-TR-002-SIDECAR-ACCEPTANCE.md`. |
| No canonical truth edited | PASS | No L1 policy doc, OpenAPI, JSON schema, BFF runtime, registry/governance, or execute-plans frontend file is modified by this sidecar. |
| Dependencies mapped | PASS | Direct dependencies plus relevant landed backend/frontend surfaces are listed in Section 3. |
| Parent acceptance is concrete | PASS | Section 4 maps acceptance targets to evidence and pass conditions. |
| Route/schema guardrails are explicit | PASS | Section 5 names route families, enum values, required score fields, and no-order proof literals. |
| Handoff target identified | PASS | Assigned sidecar reviewer is `Claude`; parent owner decides whether to absorb this packet into mainline implementation/review. |

## 9. Handoff to Reviewer (`Claude`)

This sidecar is ready for support-only review.

Recommended reviewer stance:

1. Approve this sidecar if it accurately captures the parent acceptance surface
   and stays within support-only scope.
2. Keep `AG-FE-TR-002` responsible for the actual execute-plans implementation,
   UI tests, and final evidence.
3. Reject any attempt to treat this packet as implementation proof or as
   permission to invent fields, routes, widgets, or execution controls.

After approval, the parent owner can use this packet as a bounded checklist for
CandidateReviewDrawer, queue cards, and governed intent handoff UI review.

---

Generated by `Codex` as a sidecar `acceptance_packet` helper for
`AG-FE-TR-002`. This file is a support artifact and does not modify canonical
truth.
