# AG-BE-TR-002 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-002-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-002` — Governed TradingIntent / handoff |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It summarizes the BFF query gaps, operator journey,
and frontend handoff boundaries for `AG-BE-TR-002`; the parent owner decides
whether and how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, helper parent `AG-BE-TR-002`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002` | Parent is `todo`; owner `Codex`, reviewer `Claude2`; depends on `AG-BE-TR-001` (todo; gated on AG-BE-CP-001) and `AG-XR-OPENAPI-004` (**done** — archive `2026-06-21T13:30:08Z`). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DES-TR-001` | Status `done` (archive `2026-06-21T12:16:24Z`); v4 schemas `trading_room_aggregate.schema.json`, `trading_decision_event.schema.json`, `governed_intent_handoff.schema.json` verified and merged. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Status `done` (archive `2026-06-21T13:30:08Z`); v1.3 OpenAPI bundle, `capability_manifest_v1_3.json`, and `bundle_index.v1_3.json` are live. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001` | Status `todo`; owner `Claude2`, reviewer `Codex`; depends on `AG-BE-CP-001` (blocked) and `AG-XR-OPENAPI-004` (done). AG-BE-TR-001 must land before AG-BE-TR-002 can proceed. |
| `services/control-plane/specs/agora/trading_intent.schema.json` | `TradingIntent` v1 schema: required fields `spec_version`, `intent_id`, `operator_id`, `intent_type`, `direction`, `subject`, `expressed_at`, `no_order_route_proof`; `no_order_route_proof` must be `"agora_intent_record_only"`. Records operator intent for imitation learning only; never routes broker orders. `additionalProperties: false`. |
| `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | `GovernedIntentHandoff` v1.0 schema: required `spec_version`, `handoff_id`, `intent_id`, `requested_stage`, `handoff_type`, `state`, `strategy_id`, `strategy_spec_registry_id`, `requested_by`, `evidence_refs`, `no_order_route_proof`, `created_at`. `requested_stage: ["shadow", "paper", "canary", "live"]`; `handoff_type: ["shadow_start", "paper_validation_request", "promotion_review_request"]`; `no_order_route_proof: "agora_request_only_no_order_route"`. `additionalProperties: false`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` (lines 651–701) | Three trading-intent routes defined: `GET /bff/agora/trading-intents/{intent_id}`, `POST /bff/agora/trading-intents/{intent_id}/handoffs` (governed handoff; `202` response; requires `If-Match` + `Idempotency-Key`), `POST /bff/agora/trading-intents/{intent_id}/withdraw` (requires `If-Match` + `Idempotency-Key`). Handoff route description explicitly states: "request-only surface: no RuntimeBinding is written, no capital is bound, and no broker order is routed." |
| `services/control-plane/bff/agora/trading_room/router.py` | Placeholder only — returns empty `APIRouter`. All trading-room and trading-intent routes are absent. AG-BE-TR-001 must implement routes here first; AG-BE-TR-002 builds the governed handoff lifecycle on top. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md` | D1–D10: Trading Room boundary (D1), API surface (D2), aggregate (D3), decision event semantics (D4), lifecycle states (D5), trader decisions (D6), governed handoff stages and UI wording (D7), candidate-to-decision-event promotion gate (D8), position events (D9), safety error vocabulary (D10). |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/07_dispatch_unblock_matrix.md` | AG-BE-TR-002 remains gated until "governed intent/handoff contract merged" — satisfied by AG-DES-TR-001 (done) and AG-XR-OPENAPI-004 (done). AG-BE-TR-001 must also land first. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF is the sole frontend aggregation point; all intent and handoff endpoints must return typed degraded/blocked states when downstream services are unavailable. No synthetic fallback. |
| `execute-plans/src/lib/bff-v1/agora/` | Contains `dashboard.ts`, `types.ts`, `contract-snapshot.json`; `tradingRoom.ts` does not exist. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current BFF State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `GET /bff/agora/trading-intents/{intent_id}` | Not implemented. | AG-BE-TR-001 must create TradingIntent records first; AG-BE-TR-002 provides the read endpoint. |
| `POST /bff/agora/trading-intents/{intent_id}/handoffs` | Not implemented. | Core AG-BE-TR-002 deliverable: governed handoff submission with stage routing and no-order gate. |
| `POST /bff/agora/trading-intents/{intent_id}/withdraw` | Not implemented. | AG-BE-TR-002 must record intent/handoff withdrawal; idempotency and If-Match required. |
| `services/control-plane/bff/agora/trading_room/router.py` | Empty placeholder (`return APIRouter()`). All trading-room and trading-intent routes are absent. | AG-BE-TR-001 adds aggregate/decision-event/stream routes here; AG-BE-TR-002 adds governing handoff lifecycle on top. |
| `services/control-plane/bff/agora/trading_room.py` (task artifact) | File does not exist at this path; the router lives at `trading_room/router.py`. | AG-BE-TR-002 must either create a new module at the stated artifact path or confirm with reviewer that `trading_room/router.py` is the correct implementation location. This is an open design note (§1). |
| `services/control-plane/specs/agora/trading_intent.schema.json` | Valid JSON schema (v1); `no_order_route_proof: "agora_intent_record_only"`. This is the TradingIntent **record** schema (operator intent capture), not the governed handoff schema. | AG-BE-TR-002 must create TradingIntent records conformant to this schema when `approve`/`modify` decisions come through (via AG-BE-TR-001), and the governed handoff submission must reference intent IDs from these records. |
| `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | Valid JSON schema (v1.0); all required fields present; `no_order_route_proof: "agora_request_only_no_order_route"`. | AG-BE-TR-002 must validate every incoming `POST .../handoffs` body against this schema before accepting. |
| Idempotency enforcement | Not implemented anywhere in `trading_room/router.py`. | AG-BE-TR-002 must enforce `Idempotency-Key` header on all write operations. |
| No-order gate (`TRADING_INTENT_NOT_ALLOWED`) | Not implemented. | AG-BE-TR-002 must return this error code when any path would route a broker order or write a RuntimeBinding. |

## Parent Scope Boundary

`AG-BE-TR-002` owns:

- Governed TradingIntent handoff submission (`POST /bff/agora/trading-intents/{intent_id}/handoffs`): validates `GovernedIntentHandoff` against v4 schema, enforces `no_order_route_proof: "agora_request_only_no_order_route"`, routes to the correct governance queue (`shadow_research`, `management_governance`, `promotion_review`), enforces idempotency via `Idempotency-Key`, and enforces concurrency via `If-Match`.
- Canary/live gate enforcement: for `requested_stage: "canary"` or `"live"`, the BFF only records the request and routes to `promotion_review`; it never creates a capital binding, writes a RuntimeBinding, or routes a broker order. `TRADING_INTENT_NOT_ALLOWED` is returned when the gate would be violated.
- Trading Intent read (`GET /bff/agora/trading-intents/{intent_id}`): returns full intent detail including stage, state, and handoff chain.
- Intent and handoff withdrawal (`POST /bff/agora/trading-intents/{intent_id}/withdraw`): records withdrawal; idempotency and `If-Match` required.
- `TradingIntent` schema conformance: every TradingIntent record must satisfy `trading_intent.schema.json` with `no_order_route_proof: "agora_intent_record_only"`.
- The `TRADING_INTENT_NOT_ALLOWED`, `TRADING_INTENT_ALREADY_RECORDED`, `TRADING_INTENT_HANDOFF_NOT_ALLOWED`, and `APPROVAL_REQUIRED` error codes (D10) as applied to the intent/handoff lifecycle.

`AG-BE-TR-002` does **not** own:

- TradingIntent creation from operator `approve`/`modify` decisions — that is `AG-BE-TR-001` scope (D6); AG-BE-TR-002 only manages the post-creation governed handoff lifecycle.
- Trading Room aggregate read model, decision-event queue, and SSE stream (`AG-BE-TR-001` owns these).
- Trader decision recording (`POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions`) — `AG-BE-TR-001` owns this.
- Candidate pool management (`AG-BE-CP-001` owns the candidate state machine; Trading Room only reads references).
- `RuntimeBinding`, capital binding, broker order creation, paper/canary/live promotion approval (Management governance plane owns these; Agora is never their write owner).
- Frontend UI components (`AG-FE-TR-001` / `AG-FE-TR-002` own Trading Room page, tradingRoom.ts client, CandidateReviewDrawer, and TradeDecisionCard).

Dependencies:

- `AG-XR-OPENAPI-004`: **done** (archive `2026-06-21T13:30:08Z`). The v1.3 OpenAPI bundle and all v4 schemas are present. This gate is lifted.
- `AG-DES-TR-001`: **done** (archive `2026-06-21T12:16:24Z`). v4 schemas verified and merged. Gate is lifted.
- `AG-BE-TR-001`: **todo** (blocked on `AG-BE-CP-001`). AG-BE-TR-002 cannot proceed until AG-BE-TR-001 lands and TradingIntent creation from `approve`/`modify` decisions is live. The governed handoff routes (AG-BE-TR-002) require intent IDs produced by AG-BE-TR-001.
- `AG-BE-CP-001`: **blocked**. Unblocks AG-BE-TR-001; once AG-BE-TR-001 is unblocked, AG-BE-TR-002 becomes unblocked in turn.

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Parent disposition |
|---|---|---|
| Governed handoff submission is missing | `POST /bff/agora/trading-intents/{intent_id}/handoffs` accepting a `GovernedIntentHandoff` body. Must validate against v4 schema. Must enforce `no_order_route_proof: "agora_request_only_no_order_route"`. Must enforce `Idempotency-Key` header (required, not optional). Must enforce `If-Match` for optimistic concurrency. Returns `202` with `CommandResponse` on success. | `AG-BE-TR-002` primary. |
| Canary/live no-order gate is missing | For `requested_stage: "canary"` or `"live"`: BFF must record the request and route to `promotion_review` queue only; it must never write a `RuntimeBinding`, capital binding, or broker order under this code path. `TRADING_INTENT_NOT_ALLOWED` returned if any write-order path is reached. | `AG-BE-TR-002` primary. |
| Stage routing logic is missing | Stage→queue routing: `shadow` → `shadow_research`; `paper` → `management_governance`; `canary`/`live` → `promotion_review`. Queue target is derived from `requested_stage` and is not a free-form field accepted from the client. | `AG-BE-TR-002` primary. |
| TradingIntent read is missing | `GET /bff/agora/trading-intents/{intent_id}` returning `DetailEnvelope` (per v1.3 OpenAPI). Must include intent state, requested stage, handoff chain, and evidence refs. | `AG-BE-TR-002` primary (depends on AG-BE-TR-001 for intent record creation). |
| Intent/handoff withdrawal is missing | `POST /bff/agora/trading-intents/{intent_id}/withdraw` recording intent or handoff withdrawal. Must enforce `If-Match` and `Idempotency-Key`. Returns `200` with `CommandResponse`. | `AG-BE-TR-002` primary. |
| Idempotency enforcement is missing globally | All write endpoints (handoff submission, withdrawal) must accept an `Idempotency-Key` header and replay the prior response on duplicate keys rather than creating duplicate records or returning an error. | `AG-BE-TR-002` primary. |
| `TRADING_INTENT_NOT_ALLOWED` gate is missing | When any code path inside handoff submission would reach a broker-order-route or RuntimeBinding-write, BFF must return `TRADING_INTENT_NOT_ALLOWED` (D10). This is the safety gate, not just a validation error. | `AG-BE-TR-002` primary. |
| `APPROVAL_REQUIRED` error path is missing | When a governance gate (e.g. paper/canary/live review queue) rejects the request because prior approval has not been completed, BFF must return `APPROVAL_REQUIRED` with blocking reason and governance channel reference. | `AG-BE-TR-002` primary. |
| `TRADING_INTENT_HANDOFF_NOT_ALLOWED` error path is missing | When the intent state or stage transition rules prevent a handoff submission (e.g. already in terminal state, mismatched stage), BFF must return `TRADING_INTENT_HANDOFF_NOT_ALLOWED`. | `AG-BE-TR-002` primary. |
| TradingIntent schema conformance not enforced | Every TradingIntent record created (via AG-BE-TR-001 `approve`/`modify` or any other path) must satisfy `trading_intent.schema.json` v1 with `no_order_route_proof: "agora_intent_record_only"`. No record may have a broker-order or RuntimeBinding field. | `AG-BE-TR-002` acceptance gate; enforcement shared with AG-BE-TR-001. |
| `tradingRoom.ts` governed-handoff methods are missing | `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` (to be created by AG-FE-TR-001) must include `submitHandoff`, `withdrawHandoff`, and `getTradingIntent` methods that call the AG-BE-TR-002 endpoints. | `AG-FE-TR-001`; gate on `AG-BE-TR-002`. |

## Operator Journey

### Journey A: Submit A Governed Handoff — Shadow Path

1. Operator has reviewed a TradingIntent (created via `approve`/`modify` decision in AG-BE-TR-001) and wants to start a shadow evaluation.
2. Frontend calls `POST /bff/agora/trading-intents/{intent_id}/handoffs` with a `GovernedIntentHandoff` body:
   - `requested_stage: "shadow"`
   - `handoff_type: "shadow_start"`
   - `no_order_route_proof: "agora_request_only_no_order_route"` (required, exact string)
   - `evidence_refs`: at least one evidence reference
   - `action_proposal.non_binding: true` (required if `action_proposal` is present)
   - `If-Match: <current intent ETag>`
   - `Idempotency-Key: <client-generated UUID>`
3. BFF validates the request body against `governed_intent_handoff.schema.json` v4.
4. BFF confirms `no_order_route_proof = "agora_request_only_no_order_route"`.
5. BFF routes the request to the `shadow_research` queue.
6. BFF creates a `GovernedIntentHandoff` record with `state: "submitted"` and returns `202` with a `CommandResponse` containing the `handoff_id`.
7. BFF does not write a `RuntimeBinding`, create a capital binding, or route a broker order.
8. UI shows "Start shadow" confirmation message (D7 wording).

### Journey B: Submit A Governed Handoff — Paper Validation Request

1. Operator wants to request paper validation for an approved intent.
2. Frontend calls `POST /bff/agora/trading-intents/{intent_id}/handoffs` with:
   - `requested_stage: "paper"`
   - `handoff_type: "paper_validation_request"`
   - `no_order_route_proof: "agora_request_only_no_order_route"` (required)
   - `evidence_refs`: at least one evidence reference
   - `If-Match` and `Idempotency-Key` headers
3. BFF validates schema and `no_order_route_proof` constraint.
4. BFF routes to the `management_governance` queue.
5. BFF returns `202` with `CommandResponse`. BFF does not approve the paper stage; that remains with Management governance.
6. UI shows "Request paper validation" confirmation (D7 wording). Must not say "Start paper trading" or "Execute paper run".

### Journey C: Submit A Governed Handoff — Canary or Live Review Request

1. Operator wants to request canary or live promotion review.
2. Frontend calls `POST /bff/agora/trading-intents/{intent_id}/handoffs` with:
   - `requested_stage: "canary"` or `"live"`
   - `handoff_type: "promotion_review_request"`
   - `no_order_route_proof: "agora_request_only_no_order_route"` (required)
   - `evidence_refs` (required)
   - `If-Match` and `Idempotency-Key` headers
3. BFF validates schema and `no_order_route_proof` constraint.
4. BFF enforces the canary/live gate: this is a **request-only** submission. No capital binding, no RuntimeBinding, no broker order is created anywhere in this code path.
5. BFF routes to the `promotion_review` queue and returns `202` with `CommandResponse`.
6. If any downstream path would reach a broker order: BFF must return `TRADING_INTENT_NOT_ALLOWED` (D10 safety gate), not a synthetic success.
7. UI shows "Submit canary review request" or "Submit live review request" (D7 wording). Must never label this "Execute canary trade" or "Go live".

### Journey D: Governance Gate Rejects Handoff

1. Operator attempts a canary or live promotion review request, but the governance gate detects a missing prior approval (e.g. paper validation has not completed).
2. BFF returns `APPROVAL_REQUIRED` with a blocking reason and governance channel reference.
3. UI shows an approval-required banner with a link to the governance channel; it does not show "Permission denied".
4. If the intent state or stage transition rules prevent the submission (e.g. handoff already in terminal state): BFF returns `TRADING_INTENT_HANDOFF_NOT_ALLOWED`.

### Journey E: Duplicate Handoff Submission (Idempotency)

1. Operator submits a handoff; the client retries with the same `Idempotency-Key` due to a network timeout.
2. BFF detects the duplicate key, does not create a second handoff record, and returns the response from the original submission.
3. UI receives the same `202` with the original `handoff_id`; no duplicate record appears.

### Journey F: Optimistic Concurrency Conflict

1. Operator submits a handoff with an `If-Match` value that no longer matches the current intent version (e.g. another operator or system updated the intent concurrently).
2. BFF returns `409` with a conflict response.
3. UI refreshes the intent detail and prompts the operator to review the updated state before resubmitting.

### Journey G: View Trading Intent Detail

1. Operator navigates to the intent detail from the Trading Room.
2. Frontend calls `GET /bff/agora/trading-intents/{intent_id}`.
3. BFF returns a `DetailEnvelope` containing the full intent: intent state, requested stage, handoff chain (each handoff's state and type), evidence refs, and `no_order_route_proof`.
4. UI shows the intent's progression through handoff stages (draft → submitted → accepted/rejected).

### Journey H: Withdraw An Intent Or Handoff

1. Operator decides to withdraw a pending handoff request (e.g. the strategy changed).
2. Frontend calls `POST /bff/agora/trading-intents/{intent_id}/withdraw` with `If-Match` and `Idempotency-Key`.
3. BFF records the withdrawal; the intent/handoff transitions to `state: "withdrawn"`.
4. BFF returns `200` with `CommandResponse`.
5. BFF does not delete any record; withdrawn state is retained as negative/preference evidence per D8.

### Journey I: Capability Not Ready

1. Operator attempts a handoff submission while a downstream governance service is unavailable.
2. BFF returns a typed degraded response; it does not substitute fixture data or return synthetic `202`.
3. UI shows the typed error with source and blocking reasons.

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client methods | `tradingRoom.ts` (to be created by `AG-FE-TR-001`) must include: `getTradingIntent(intentId)`, `submitHandoff(intentId, body, opts)`, `withdrawHandoff(intentId, opts)`. All three are part of AG-BE-TR-002 endpoint scope. |
| Fallback posture | Live strict behavior (BFF HA policy §5.1). No local fixture fallback, synthetic handoff responses, or direct governance-service fanout from the frontend. |
| Governed handoff body | `submitHandoff` must always include `no_order_route_proof: "agora_request_only_no_order_route"` and `action_proposal.non_binding: true` (if `action_proposal` is present). These are enforced server-side but the client must also set them. |
| Stage labels | Map `requested_stage` to display labels per D7: `"shadow"` → "Start shadow"; `"paper"` → "Request paper validation"; `"canary"` → "Submit canary review request"; `"live"` → "Submit live review request". Never use "Execute", "Place order", "Trade", or "Go live" labels. |
| Write options | All handoff and withdrawal calls must supply `{ ifMatch: string; idempotencyKey: string }` options. Generate a fresh UUID for each user-initiated action; reuse the same UUID for client retries of the same action. |
| `202` handling | Map `202` to "request submitted" confirmation. Do not show "order placed", "trade confirmed", or any wording implying execution. |
| `TRADING_INTENT_NOT_ALLOWED` | Show: "This intent cannot be routed as a live action from Agora. Submit a review request through the governance channel." |
| `APPROVAL_REQUIRED` | Show: "Prior approval is required before this stage can be requested. [Link to governance channel]." |
| `TRADING_INTENT_HANDOFF_NOT_ALLOWED` | Show: "Handoff submission is not allowed in the current intent state." Refresh the intent detail to show the current state. |
| `409` (concurrency conflict) | Show: "The intent was updated by another action. Please refresh and review before resubmitting." |
| No-order guard | No trading-intent endpoint or frontend action routes a broker order, writes a `RuntimeBinding`, or creates a capital binding. The frontend must never expose "Execute trade", "Bind capital", or "Place order" controls in any intent or handoff surface. |
| Intent detail | `getTradingIntent(intentId)` → show intent state, requested stage, handoff chain (each handoff's type, state, and evidence), `no_order_route_proof` field (display as "Request only — no order route"). |

Suggested frontend client method signatures (all in `tradingRoom.ts`):

```ts
getTradingIntent(intentId: string): Promise<TradingIntentDetail>
submitHandoff(intentId: string, body: GovernedIntentHandoff, opts: WriteOptions): Promise<CommandResponse>
withdrawHandoff(intentId: string, opts: WriteOptions): Promise<CommandResponse>
```

`WriteOptions`: `{ ifMatch: string; idempotencyKey: string }`

`GovernedIntentHandoff` body constraints:
- `no_order_route_proof` must always be `"agora_request_only_no_order_route"` (literal, not a variable)
- `action_proposal.non_binding` must be `true` when `action_proposal` is present
- `evidence_refs` must have at least one entry
- `requested_stage`: `"shadow"` | `"paper"` | `"canary"` | `"live"`
- `handoff_type` must match the requested stage:
  - `"shadow"` → `"shadow_start"`
  - `"paper"` → `"paper_validation_request"`
  - `"canary"` | `"live"` → `"promotion_review_request"`

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance — governed handoff body | Every `POST /bff/agora/trading-intents/{intent_id}/handoffs` request body validates against `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json`. |
| Schema conformance — TradingIntent record | Every TradingIntent record validates against `services/control-plane/specs/agora/trading_intent.schema.json` with `no_order_route_proof: "agora_intent_record_only"`. |
| Required governed handoff fields | `spec_version`, `handoff_id`, `intent_id`, `requested_stage`, `handoff_type`, `state`, `strategy_id`, `strategy_spec_registry_id`, `requested_by`, `evidence_refs`, `no_order_route_proof`, `created_at` all present. |
| `no_order_route_proof` on governed handoff | `governed_intent_handoff.no_order_route_proof = "agora_request_only_no_order_route"`. Reject any request with a different value. |
| `no_order_route_proof` on TradingIntent | `trading_intent.no_order_route_proof = "agora_intent_record_only"`. |
| Stage → queue routing | `shadow` → `shadow_research` queue; `paper` → `management_governance` queue; `canary`/`live` → `promotion_review` queue. Queue assignment is derived server-side; never accepted as a client-supplied parameter. |
| Canary/live no-order enforcement | `requested_stage: "canary"` or `"live"` must never cause a RuntimeBinding write, capital binding, or broker order under any code path. All paths must be covered by tests. |
| `TRADING_INTENT_NOT_ALLOWED` gate | A direct request for a broker order or RuntimeBinding returns `TRADING_INTENT_NOT_ALLOWED` (typed error). Tests must include a code path that reaches this gate and verify it is triggered. |
| `Idempotency-Key` required | `POST .../handoffs` and `POST .../withdraw` must reject requests without an `Idempotency-Key` header. |
| Idempotency replay | Duplicate `POST` with the same `Idempotency-Key` returns the original response; no duplicate record is created. Tests must include a duplicate-submission scenario. |
| `If-Match` required | `POST .../handoffs` and `POST .../withdraw` must require an `If-Match` header. `409` returned on version mismatch. |
| Handoff type validity | `handoff_type` must match `requested_stage` per schema enum; mismatched combinations → `422`. |
| State machine validity | Handoff state transitions follow v4 schema enum: `draft → submitted → accepted/rejected`; `submitted → withdrawn`; `submitted → expired`. Invalid transitions → `422`. |
| `TRADING_INTENT_HANDOFF_NOT_ALLOWED` | Returned when intent state or stage rules prevent submission. |
| `APPROVAL_REQUIRED` | Returned when a governance gate requires prior approval that has not been completed. |
| `TRADING_INTENT_ALREADY_RECORDED` | Returned when a duplicate (non-idempotent) intent creation attempt is detected. |
| Intent read — all fields present | `GET /bff/agora/trading-intents/{intent_id}` returns `DetailEnvelope` with full intent state, stage, handoff chain, and evidence. |
| Withdrawal recording | `POST /bff/agora/trading-intents/{intent_id}/withdraw` sets intent/handoff state to `withdrawn` and records the actor. Withdrawn records must not be deleted. |
| Action_proposal non-binding | If `action_proposal` is present, `non_binding` must be `true`. BFF must reject any `action_proposal` with `non_binding: false` or `non_binding` absent. |
| No-order guard end-to-end | No AG-BE-TR-002 endpoint creates a broker order, writes a RuntimeBinding, or creates a capital binding. Tests must cover the full intent → handoff → queue submission path and assert no order-routing side effects. |
| BFF degraded response | When the governance queue service is unavailable, BFF returns a typed blocked/degraded response; it must not substitute fixture data or return a synthetic `202`. |

## Open Design Notes

### 1. Artifact path discrepancy: `trading_room.py` vs `trading_room/router.py`

The AG-BE-TR-002 parent task artifact lists `services/control-plane/bff/agora/trading_room.py`, but the current repository has `services/control-plane/bff/agora/trading_room/` as a directory (containing `router.py`, `__init__.py`). The file `trading_room.py` does not exist at the artifact path.

Parent owner (Codex) and reviewer (Claude2) should clarify before implementation whether:
- AG-BE-TR-002 should implement routes inside `trading_room/router.py` (the existing placeholder), or
- A new `trading_room.py` module should be created alongside the `trading_room/` directory.

Recommendation: use `trading_room/router.py` (the file already includes the safety invariant comment and the `create_trading_room_router` function); adding a separate `trading_room.py` would create an ambiguous module layout.

### 2. AG-BE-TR-002 depends on AG-BE-TR-001 for TradingIntent record creation

The governed handoff routes in AG-BE-TR-002 operate on existing TradingIntent records. Those records are created by AG-BE-TR-001 when an operator makes an `approve`/`modify` decision on a decision event. AG-BE-TR-002 cannot be tested end-to-end until AG-BE-TR-001 is live.

However, AG-BE-TR-002 can implement the handoff submission, state routing, and gate enforcement logic using a stub intent store for unit tests, while integration tests gate on AG-BE-TR-001.

### 3. AG-BE-TR-001 is blocked on AG-BE-CP-001

AG-BE-TR-001 (which AG-BE-TR-002 depends on) is gated on AG-BE-CP-001 (blocked). The aggregate read and decision-event queue routes in AG-BE-TR-001 can be implemented without AG-BE-CP-001, but the candidate-to-decision-event promotion (D8, Journey G from AG-BE-TR-001 sidecar) is blocked. When AG-BE-CP-001 is unblocked, AG-BE-TR-001 unblocks, which then unblocks AG-BE-TR-002.

### 4. `trading_intent.schema.json` v1 vs `governed_intent_handoff.schema.json` v4

There are two schema files in scope for AG-BE-TR-002:
- `trading_intent.schema.json` (v1): Records an operator's expressed trading intent for imitation learning. Created by AG-BE-TR-001. `no_order_route_proof: "agora_intent_record_only"`. This is the **intent record** schema.
- `governed_intent_handoff.schema.json` (v4): Records a governed handoff submission on an existing intent. Created by AG-BE-TR-002. `no_order_route_proof: "agora_request_only_no_order_route"`. This is the **handoff request** schema.

These are distinct records, not versions of each other. The intent record captures what the operator wants to do; the handoff record captures the request to route that intent through a governance path.

### 5. Canary/live promotion never creates an order from Agora

Per D7 and the v1.3 OpenAPI route description: canary and live `requested_stage` values are **request-only**. The outcome of a promotion review request (if approved by Management governance) is a `DeploymentPlan` and `RuntimeBinding` created by the Management governance plane — not by Agora or the BFF. Agora only records the request and waits for the governance outcome. This must be stated clearly in the implementation comments and test evidence.

### 6. Idempotency key is not optional

The v1.3 OpenAPI marks `Idempotency-Key` as a parameter on both `POST .../handoffs` and `POST .../withdraw`. The AG-BE-TR-002 implementation must treat it as **required** (returning `422` if absent), not optional. This prevents duplicate records in the face of network retries.

### 7. Frontend task gates on AG-BE-TR-002

`AG-FE-TR-001` (Trading Room tab + `tradingRoom.ts` client) depends on `AG-BE-TR-001`. The governed-handoff methods (`submitHandoff`, `withdrawHandoff`, `getTradingIntent`) depend on `AG-BE-TR-002`. The frontend cannot bind to live governed-handoff data until AG-BE-TR-002 routes are testable.

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | `AG-BE-TR-002` is `todo` (owner `Codex`, reviewer `Claude2`); `AG-DES-TR-001` is **`done`** (archive `2026-06-21T12:16:24Z`); `AG-XR-OPENAPI-004` is **`done`** (archive `2026-06-21T13:30:08Z`); `AG-BE-TR-001` is `todo` (gated on `AG-BE-CP-001`); `trading_room/router.py` is a placeholder returning empty `APIRouter`. |
| Schema accuracy | `governed_intent_handoff.schema.json` v4 required fields, `requested_stage` enum values, `handoff_type` enum values, `state` enum values, and `no_order_route_proof` constraint accurately described. `trading_intent.schema.json` v1 `no_order_route_proof: "agora_intent_record_only"` correctly distinguished from v4 handoff proof. |
| Route accuracy | Three trading-intent routes from `agora_v1_3.openapi.yaml` (lines 651–701) accurately listed with correct HTTP methods and parameter requirements (`If-Match`, `Idempotency-Key`). |
| Stage routing accuracy | `shadow` → `shadow_research`; `paper` → `management_governance`; `canary`/`live` → `promotion_review` is correct per `governed_intent_handoff.schema.json` `target_queue` enum. |
| Open design note accuracy | Artifact path discrepancy (`trading_room.py` vs `trading_room/router.py`) is real and needs resolution. Schema distinction (intent record vs handoff record) is correctly drawn. Canary/live no-order claim is correctly grounded in D7 and v1.3 OpenAPI route description. Idempotency-Key required claim is correctly grounded in v1.3 OpenAPI parameters. |
| No-order guard | All journeys, acceptance checks, and open design notes correctly exclude broker orders, `RuntimeBinding`, and capital binding from AG-BE-TR-002 scope. |
| Boundary accuracy | AG-BE-TR-001 / AG-BE-TR-002 ownership split correctly stated: TR-001 creates TradingIntent from operator decisions; TR-002 manages post-creation governed handoff lifecycle. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: records governed TradingIntent/handoff BFF gaps (submit/read/withdraw), canary/live no-order gate enforcement (TRADING_INTENT_NOT_ALLOWED), idempotency/If-Match requirements, stage→queue routing (shadow→shadow_research, paper→management_governance, canary/live→promotion_review), TradingIntent vs GovernedIntentHandoff schema distinction, operator journeys A-I, frontend tradingRoom.ts method signatures, and AG-BE-TR-001/AG-BE-TR-002 ownership boundary, without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-TR-002-SIDECAR-BFF-HANDOFF \
  "Support-only AG-BE-TR-002 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-002-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-002-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF
# in_progress; owner Claude; reviewer Claude2; helper_parent AG-BE-TR-002

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002
# todo; owner Codex; reviewer Claude2; depends_on AG-BE-TR-001 (todo), AG-XR-OPENAPI-004 (done)

AI_NAME=Claude python3 scripts/ai_status.py show AG-DES-TR-001
# source: archive; terminal_status: done; archived_at 2026-06-21T12:16:24Z

AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-OPENAPI-004
# source: archive; terminal_status: done; archived_at 2026-06-21T13:30:08Z

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex; depends_on AG-BE-CP-001 (blocked), AG-XR-OPENAPI-004 (done)

cat services/control-plane/bff/agora/trading_room/router.py
# Empty APIRouter; placeholder only.

python3 -m json.tool services/control-plane/specs/agora/trading_intent.schema.json > /dev/null
# Valid JSON schema.

python3 -m json.tool services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json > /dev/null
# Valid JSON schema.

grep -n "trading.intent" services/control-plane/openapi/agora_v1_3.openapi.yaml
# Lines 651-701: GET /bff/agora/trading-intents/{intent_id},
# POST /bff/agora/trading-intents/{intent_id}/handoffs,
# POST /bff/agora/trading-intents/{intent_id}/withdraw

ls execute-plans/src/lib/bff-v1/agora/
# dashboard.ts  types.ts  contract-snapshot.json
# tradingRoom.ts does not exist.
```
