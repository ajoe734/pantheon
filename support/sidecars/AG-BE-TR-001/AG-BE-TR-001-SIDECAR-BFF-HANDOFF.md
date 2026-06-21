# AG-BE-TR-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` — Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It summarizes the BFF query gaps, operator journey,
and frontend handoff boundaries for `AG-BE-TR-001`; the parent owner decides
whether and how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, helper parent `AG-BE-TR-001`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001` | Parent is `todo`; owner `Claude2`, reviewer `Codex`; depends on `AG-BE-CP-001` (blocked) and `AG-XR-OPENAPI-004` (**done** — archive `2026-06-21T13:30:08Z`, v1.3 OpenAPI bundle and v4 schemas merged). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Status `done` (archive terminal); v1.3 bundle is live, all v4 schemas present. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001` | Status `blocked`; candidate pool BFF routes are not yet implemented; `AG-BE-CP-001` must land before Trading Room can consume candidate-decision references. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-001` | Status `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-SW-001`, `AG-BE-TR-001`, `AG-XR-OPENAPI-004`. Needs Trading Room BFF routes live before frontend can bind. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-002` | Status `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-TR-001`, `AG-BE-CP-001`, `AG-XR-OPENAPI-004`. Needs CandidateReviewDrawer and entry/position/exit queue cards. |
| `services/control-plane/bff/agora/trading_room/router.py` | Returns an empty `APIRouter`. No Trading Room routes are implemented; file is a placeholder (migration note says routes were in `main.py` for signals only; trading-room aggregate, decision-events, stream, and intent routes are absent). |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | No trading room routes; v1 OpenAPI does not define any `/bff/agora/trading-room/*` or `/bff/agora/trading-intents/*` paths. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | All trading room and trading intent routes are formally defined (lines 544–703): `GET /bff/agora/trading-room`, `GET /bff/agora/trading-room/strategies/{strategy_id}`, `GET /bff/agora/trading-room/decision-events`, `GET /bff/agora/trading-room/decision-events/{decision_event_id}`, `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions`, `GET /bff/agora/trading-room/stream`, `GET /bff/agora/trading-intents/{intent_id}`, `POST /bff/agora/trading-intents/{intent_id}/handoffs`, `POST /bff/agora/trading-intents/{intent_id}/withdraw`. |
| `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json` | `TradingRoomAggregate` v1.0 schema: required fields `spec_version`, `user_scope_ref`, `strategies`, `queue_summary`, `risk_summary`, `snapshot_at`, `data_cutoff`. Per-strategy required fields: `strategy_id`, `strategy_spec_registry_id`, `title`, `readiness_state`, `monitoring_state`, `pending_event_counts`. Queue summary covers entry/add/reduce/exit/review counts. `additionalProperties: false` at aggregate and strategy levels. |
| `services/control-plane/specs/agora/v4/trading_decision_event.schema.json` | `TradingDecisionEvent` v1.0 schema: required fields include `decision_event_id`, `event_kind`, `origin`, `strategy_id`, `strategy_spec_registry_id`, `subject`, `state`, `triggered_at`, `confidence`, `probability`, `expected_value`, `rationale`, `risk_notes`, `evidence_refs`, `invalidation`, `suggested_action`, `no_order_route_proof`. `additionalProperties: false`. |
| `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | `GovernedIntentHandoff` v1.0 schema: required `spec_version`, `handoff_id`, `intent_id`, `requested_stage`, `handoff_type`, `state`, `strategy_id`, `strategy_spec_registry_id`, `requested_by`, `evidence_refs`, `no_order_route_proof`, `created_at`. `no_order_route_proof` must be `"agora_request_only_no_order_route"`. |
| `services/control-plane/specs/agora/trading_event.schema.json` | `TradingEvent` v1.0 observation schema: required `spec_version`, `event_id`, `operator_id`, `event_type`, `observed_at`, `no_order_route_proof`. `no_order_route_proof` must be `"agora_observation_only"` or `"research_plane_only"`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md` | D1–D10 specifies Trading Room boundary, API routes, aggregate fields, decision event semantics (confidence vs probability, EV breakdown), event lifecycle states, trader decisions (approve/reject/defer/modify), governed handoff stages, candidate-to-decision-event promotion conditions, position event fields, and safety error codes. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF is the sole frontend aggregation point; trading room routes must return typed degraded/blocked states when downstream services are unavailable. BFF failure must not affect active runtimes. |
| `execute-plans/src/lib/bff-v1/agora/` | Contains `dashboard.ts`, `types.ts`, `contract-snapshot.json`; no `tradingRoom.ts` client module exists yet. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current BFF State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `GET /bff/agora/trading-room` | Not implemented (`trading_room/router.py` returns empty `APIRouter`). | AG-BE-TR-001 must implement the `TradingRoomAggregate` response per v4 schema. |
| `GET /bff/agora/trading-room/strategies/{strategy_id}` | Not implemented. | AG-BE-TR-001 must return strategy-level Trading Room detail. |
| `GET /bff/agora/trading-room/decision-events` | Not implemented. | AG-BE-TR-001 must return the decision-event queue filtered by `event_kind` and `state`. |
| `GET /bff/agora/trading-room/decision-events/{decision_event_id}` | Not implemented. | AG-BE-TR-001 must return a `TradingDecisionEvent` with all v4 required fields. |
| `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions` | Not implemented. | AG-BE-TR-001 must record trader decisions (approve/reject/defer/modify); `approve`/`modify` create a `TradingIntent` (request-only, no broker order). |
| `GET /bff/agora/trading-room/stream` | Not implemented. | AG-BE-TR-001 must provide a typed SSE stream for user-scoped Trading Room events (decision-event state changes, queue count updates, risk summary changes). |
| `GET /bff/agora/trading-intents/{intent_id}` | Not implemented. | AG-BE-TR-001 must return `TradingIntent` detail (or a `DetailEnvelope`). |
| `POST /bff/agora/trading-intents/{intent_id}/handoffs` | Not implemented. | AG-BE-TR-001 must accept a `GovernedIntentHandoff` body (`no_order_route_proof: "agora_request_only_no_order_route"`) and route it to the appropriate queue (`shadow_research`, `management_governance`, or `promotion_review`). No `RuntimeBinding` written. |
| `POST /bff/agora/trading-intents/{intent_id}/withdraw` | Not implemented. | AG-BE-TR-001 must record intent/handoff withdrawal. |
| `services/control-plane/bff/agora/trading_room/router.py` | Placeholder only; empty `APIRouter`, no route handlers. | All AG-BE-TR-001 routes must be added here, not to `main.py`. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | AG-FE-TR-001 must create this module; it is the only frontend-to-Trading-Room channel. |
| Candidate-decision reference consumption | Not implemented. | Trading Room reads candidate-decision references from AG-BE-CP-001 routes; it does not create a second candidate state machine. Gate: AG-BE-CP-001 must be unblocked first. |

## Parent Scope Boundary

`AG-BE-TR-001` owns:

- Trading Room aggregate read model (`GET /bff/agora/trading-room` and `GET /bff/agora/trading-room/strategies/{strategy_id}`), conforming to `trading_room_aggregate.schema.json` v1.0.
- Decision-event queue read model (`GET /bff/agora/trading-room/decision-events` and `GET /bff/agora/trading-room/decision-events/{decision_event_id}`), conforming to `trading_decision_event.schema.json` v1.0.
- Trader decision recording (`POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions`); `approve`/`modify` creates a `TradingIntent` (request-only); no broker order, no `RuntimeBinding`.
- Trading Room SSE stream (`GET /bff/agora/trading-room/stream`) delivering typed events for decision-event state changes, queue count deltas, and risk summary updates.
- Trading Intent read and governed handoff (`GET /bff/agora/trading-intents/{intent_id}`, `POST /bff/agora/trading-intents/{intent_id}/handoffs`, `POST /bff/agora/trading-intents/{intent_id}/withdraw`), conforming to `governed_intent_handoff.schema.json` v1.0.
- §17.4 endpoint (as referenced in the parent task acceptance criteria), which maps to the decision-events and trading-intents routes defined in `agora_v1_3.openapi.yaml`.

`AG-BE-TR-001` does **not** own:

- Candidate pool listing, scoring, or decision recording (`AG-BE-CP-001` owns this; Trading Room only consumes candidate-decision references).
- `RuntimeBinding`, capital binding, broker order, or live/paper governance promotion (Agora is never the write owner of these).
- `TradingIntent` creation from Management-side flows (`AG-BE-TR-002` owns Trading Intent creation initiated from Management decisions; AG-BE-TR-001 creates intents only as a result of operator `approve`/`modify` decisions).
- Research run dispatch or projection (`AG-BE-RS-001` / `AG-BE-RS-002` own this).
- Frontend UI components (`AG-FE-TR-001` / `AG-FE-TR-002` own the Trading Room page, multi-strategy switcher, CandidateReviewDrawer, and TradeDecisionCard).

Dependencies:

- `AG-XR-OPENAPI-004`: **done** (archive `2026-06-21T13:30:08Z`). The v1.3 OpenAPI bundle and all v4 schemas (`trading_room_aggregate.schema.json`, `trading_decision_event.schema.json`, `governed_intent_handoff.schema.json`) are present. This gate is lifted.
- `AG-BE-CP-001`: **blocked**. Candidate-decision references consumed by the Trading Room's decision-event promotion (D8) depend on `AG-BE-CP-001` routes. AG-BE-TR-001 implementation can proceed for aggregate/event-queue reads and trader-decision recording, but the candidate-to-decision-event promotion logic is gated on AG-BE-CP-001.

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Parent disposition |
|---|---|---|
| Trading Room aggregate is missing | `GET /bff/agora/trading-room` returning `TradingRoomAggregate` for the user scope: `strategies[]` (with `readiness_state`, `monitoring_state`, `pending_event_counts`), `queue_summary`, `top_decision_events`, `risk_summary`, `snapshot_at`, `data_cutoff`. | `AG-BE-TR-001` primary. |
| Strategy-level Trading Room detail is missing | `GET /bff/agora/trading-room/strategies/{strategy_id}` returning strategy detail including strategy-specific position summaries, shadow state, and performance summary. | `AG-BE-TR-001` primary. |
| Decision-event queue is missing | `GET /bff/agora/trading-room/decision-events` accepting filters `event_kind` (`entry`, `add`, `reduce`, `exit`, `review`) and `state`. Must return `TradingDecisionEvent` list with all required fields including `confidence`, `probability`, `expected_value`, `rationale`, `risk_notes`, `evidence_refs`, `invalidation`, `no_order_route_proof`. | `AG-BE-TR-001` primary. |
| Decision-event detail is missing | `GET /bff/agora/trading-room/decision-events/{decision_event_id}` returning full `TradingDecisionEvent` including `position_snapshot`, `suggested_size`, `dedupe_key`, `trigger`, and `decision_state`. | `AG-BE-TR-001` primary. |
| Trader decision recording is missing | `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions` accepting `{ decision: "approve" | "reject" | "defer" | "modify", rationale?, modifications? }`. `approve`/`modify` creates a `TradingIntent` (request-only). Must include `If-Match` header for optimistic concurrency and `Idempotency-Key` for duplicate protection. | `AG-BE-TR-001` primary. |
| Trading Room SSE stream is missing | `GET /bff/agora/trading-room/stream` delivering typed SSE events: decision-event state transitions, queue count changes, risk summary updates, strategy readiness changes. | `AG-BE-TR-001` primary. |
| Trading Intent read is missing | `GET /bff/agora/trading-intents/{intent_id}` returning intent detail (stage, state, handoff refs, evidence). | `AG-BE-TR-001` primary. |
| Governed handoff submission is missing | `POST /bff/agora/trading-intents/{intent_id}/handoffs` accepting a `GovernedIntentHandoff` body. Must enforce `no_order_route_proof: "agora_request_only_no_order_route"`. Routes to `shadow_research`, `management_governance`, or `promotion_review` queue depending on `requested_stage`. Must not write `RuntimeBinding` or capital binding. | `AG-BE-TR-001` primary. |
| Handoff withdrawal is missing | `POST /bff/agora/trading-intents/{intent_id}/withdraw` recording intent or handoff withdrawal. Must include `If-Match` and `Idempotency-Key`. | `AG-BE-TR-001` primary. |
| `tradingRoom.ts` frontend client is missing | `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` — typed client for all Trading Room and Trading Intent BFF routes. Pages must not call downstream services directly. | `AG-FE-TR-001`; gate on `AG-BE-TR-001`. |
| TradeDecisionCard and entry/add/reduce/exit queue binding is missing | Frontend cards for each event kind must show all decision-support fields: confidence + basis + calibration, probability + target_outcome + horizon, EV breakdown (gross/cost/net/downside + unit + horizon), rationale claims, risk notes, evidence refs, invalidation state, suggested action, suggested size (non-binding). | `AG-FE-TR-002`; gate on `AG-FE-TR-001` and `AG-BE-CP-001`. |

## Operator Journey

### Journey A: View The Trading Room Aggregate

1. Operator opens the Trading Room in Agora.
2. Frontend calls `GET /bff/agora/trading-room` via the `tradingRoom.ts` BFF client.
3. BFF assembles a `TradingRoomAggregate` from the read model: strategies with `readiness_state` and `monitoring_state`, per-kind `pending_event_counts`, queue summary totals, top decision events, risk summary, and snapshot metadata.
4. UI renders the strategy switcher panel showing each strategy's readiness state, monitoring state, and pending queue counts by event kind (entry/add/reduce/exit/review).
5. UI renders the risk summary badge: `normal`, `watch`, `warning`, or `critical`.
6. If any strategy has `readiness_state: stale` or `staleness_reasons`, UI shows the staleness indicator and reason.

### Journey B: Browse And Filter The Decision-Event Queue

1. Operator selects an event-kind tab (e.g. "Entry" or "Exit") in the Trading Room.
2. Frontend calls `GET /bff/agora/trading-room/decision-events?event_kind=entry&state=pending_review` via the BFF client.
3. BFF returns a list of `TradingDecisionEvent` items matching the filter, including all required decision-support fields.
4. UI renders decision-event cards sorted by event urgency or `triggered_at`; each card shows:
   - Event kind, symbol, venue, strategy version identity.
   - `confidence.value` and `confidence.calibration_state` (must show basis, not just a number).
   - `probability.value`, `probability.target_outcome`, `probability.horizon` (distinct from confidence).
   - EV breakdown: `gross`, `cost`, `net`, `downside` with `unit` and `horizon`.
   - Top rationale claims and risk notes (severity badges).
   - `invalidation.current_state` — if `watch` or `invalidated`, show a warning banner.
   - `suggested_action` and `suggested_size` (non-binding label required on size).
   - `data_cutoff` timestamp.
5. UI must not hide stale or invalidated events; it must surface their state so the operator can decide.

### Journey C: Review A Decision Event In Detail

1. Operator clicks a decision-event card to open the detail view.
2. Frontend calls `GET /bff/agora/trading-room/decision-events/{decision_event_id}`.
3. BFF returns the full `TradingDecisionEvent` including:
   - `trigger` (rule, current value, threshold, distance to trigger).
   - `position_snapshot` for add/reduce/exit/review event kinds.
   - `evidence_refs` with `ref_type` and `summary`.
   - `probability.ci_lower` / `probability.ci_upper` confidence interval when available.
   - All `rationale` claims with per-claim confidence and evidence refs.
   - All `risk_notes` with severity, domain, summary, and mitigation.
4. UI renders the full decomposition; it must show confidence and probability as two distinct fields with their respective basis/calibration and target_outcome/horizon (per D4 semantics — confidence is not probability).

### Journey D: Record A Trader Decision

1. Operator selects a decision action (Approve / Reject / Defer / Modify) in the detail view.
2. Frontend calls `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions` with `{ decision, rationale?, modifications? }`, `If-Match`, and `Idempotency-Key`.
3. BFF validates the event state (must be `triggered` or `pending_review`; returns `TRADING_EVENT_STALE` or `TRADING_EVENT_INVALIDATED` if the event has expired or been invalidated).
4. For `approve` or `modify`: BFF creates a `TradingIntent` (request-only; `no_order_route_proof` required on the intent record). Returns `201` with a `CommandResponse` containing the new intent ID.
5. For `reject` or `defer`: BFF records the decision; event transitions to `decided`. `reject`/`defer` remain available to Shadow and Learn subject to consent.
6. UI transitions the card to its new state immediately after the BFF response; it must not pre-empt the response with a local mutation.
7. UI must not show "Place order" or "Execute trade" wording anywhere in the decision flow; allowed wording: "Approve intent", "Reject", "Defer", "Modify proposal".

### Journey E: Submit A Governed Handoff

1. Operator selects a governed action (Start shadow / Request paper validation / Submit canary review / Submit live review) from the intent detail view.
2. Frontend calls `POST /bff/agora/trading-intents/{intent_id}/handoffs` with a `GovernedIntentHandoff` body:
   - `requested_stage`: `shadow`, `paper`, `canary`, or `live`.
   - `handoff_type`: `shadow_start`, `paper_validation_request`, or `promotion_review_request`.
   - `no_order_route_proof`: `"agora_request_only_no_order_route"` (required).
   - `evidence_refs`: at least one evidence reference.
   - `action_proposal.non_binding: true` (required on any size proposal).
3. BFF validates `no_order_route_proof`, routes the request to the appropriate queue (`shadow_research`, `management_governance`, or `promotion_review`), and returns `202` with a `CommandResponse`.
4. BFF must not write a `RuntimeBinding`, create a capital binding, or route a broker order.
5. UI shows the wording defined in D7: "Start shadow", "Request paper validation", "Submit canary review request", "Submit live review request". Must not label these as "Execute" or "Place order".
6. If the governance gate rejects the request (`APPROVAL_REQUIRED` or `TRADING_INTENT_HANDOFF_NOT_ALLOWED`), UI shows the blocking reason and guides the operator to the correct approval channel.

### Journey F: Watch The Live SSE Stream

1. Operator keeps the Trading Room open; frontend subscribes to `GET /bff/agora/trading-room/stream`.
2. SSE stream delivers typed events as decision-event states change (e.g. `approaching → triggered → pending_review`), queue counts change, or the risk summary escalates.
3. UI updates the relevant cards and queue count badges in real time without full page reload.
4. If the SSE connection drops, UI reconnects with exponential backoff and shows a "reconnecting" indicator; it must not silently display stale data.

### Journey G: Candidate Becomes A Decision Event (D8 Gate)

1. Operator adds a candidate to monitoring via `AG-BE-CP-001` (candidate lifecycle: `add_to_monitoring`).
2. The Trading Room projection monitors the candidate; when the strategy version is Trading Room ready, the configured trigger is approaching or reached, freshness checks pass, and no invalidation condition is active — BFF creates a `TradingDecisionEvent` record linked to the candidate ref.
3. Operator sees the new decision event appear in the "Entry" queue.
4. The Trading Room does **not** re-create a candidate state machine; it only reads the candidate-decision reference produced by `AG-BE-CP-001`.

**Gate note:** This journey is blocked until `AG-BE-CP-001` is unblocked and its candidate routes land. The aggregate/queue/stream routes in journeys A–F can proceed independently.

### Journey H: Capability Not Ready

1. Operator attempts any Trading Room action while a downstream dependency (strategy registry, risk model, candidate pool, or event projection) is unavailable.
2. BFF returns a typed error from the D10 error vocabulary: `TRADING_ROOM_NOT_READY`, `TRADING_EVENT_STALE`, `TRADING_EVENT_INVALIDATED`, `TRADING_INTENT_ALREADY_RECORDED`, `TRADING_INTENT_HANDOFF_NOT_ALLOWED`, `APPROVAL_REQUIRED`, or `CAPABILITY_DENIED`.
3. BFF must not return a synthetic success or substitute fixture data.
4. UI shows the typed error with source and `blocking_reasons`; it must not silently ignore the failure.

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client module | Create `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`. All Trading Room and Trading Intent calls must go through this module; pages must not call downstream services directly. |
| Fallback posture | Live strict behavior (per BFF HA policy §5.1). Do not add local fixture fallback, synthetic decision events, or direct service fanout. |
| Trading Room aggregate | `getTradingRoom()` → bind `strategies[]` with `readiness_state`, `monitoring_state`, `pending_event_counts`, `candidate_count`, `position_count`, `staleness_reasons`. Bind `queue_summary` to event-kind tab badges. Bind `risk_summary.state` to the risk badge. Show `snapshot_at` and `data_cutoff` so operator knows data freshness. |
| Strategy detail | `getTradingRoomStrategy(strategyId)` → show strategy-specific detail, shadow state, and performance summary. |
| Decision-event list | `listDecisionEvents(filter?: { event_kind?, state? })` → render cards sorted by urgency. Each card must show `confidence` (value + basis + calibration) and `probability` (value + target_outcome + horizon) as distinct fields — do not merge or relabel them. |
| Decision-event detail | `getDecisionEvent(decisionEventId)` → bind full `TradingDecisionEvent` to the detail drawer. Show trigger rule + distance, full rationale claims, all risk notes, evidence refs, invalidation state, suggested action, and non-binding size hint. |
| Trader decision | `recordDecision(decisionEventId, body: TraderDecision, options: { ifMatch: string, idempotencyKey: string })` → optimistic UI transition on `201`; map `409` to refresh-required, `422` to governance/precondition failure. |
| Trading Room stream | `streamTradingRoom(onEvent)` → typed SSE subscription; reconnect with backoff on disconnect; show reconnecting indicator. |
| Trading Intent detail | `getTradingIntent(intentId)` → show intent state, requested stage, and handoff chain. |
| Governed handoff | `submitHandoff(intentId, body: GovernedIntentHandoff, options: { ifMatch, idempotencyKey })` → requires `no_order_route_proof: "agora_request_only_no_order_route"` and `action_proposal.non_binding: true` on any size proposal. Map `202` to "request submitted" — do not show "order placed". |
| Handoff withdrawal | `withdrawHandoff(intentId, options: { ifMatch, idempotencyKey })` → transition intent state to `withdrawn`. |
| EV display | Always show `gross`, `cost`, `net`, `downside`, `unit`, and `horizon` together. Do not show only `net`. Show a tooltip clarifying that `net EV = gross EV − estimated transaction cost/slippage`. |
| Confidence vs probability | Show `confidence.value` (evidence/model quality) and `probability.value + target_outcome + horizon` (outcome forecast) as separate fields with separate labels. Do not collapse to one number. |
| Non-binding size display | `suggested_size` must always carry a "non-binding" label; `non_binding: true` must be enforced. |
| Safety error display | `TRADING_ROOM_NOT_READY` → show "Trading Room is not ready; check strategy readiness." `TRADING_EVENT_STALE` → refresh and show stale banner. `TRADING_EVENT_INVALIDATED` → show invalidation reason and disable action buttons. `TRADING_INTENT_ALREADY_RECORDED` → show "Decision already recorded" and navigate to the existing intent. `APPROVAL_REQUIRED` → show approval-required banner with governance channel link. `CAPABILITY_DENIED` → show capability denied with scope details. |
| No-order guard | No Trading Room route or frontend action routes a broker order, writes a `RuntimeBinding`, or creates a capital binding. UI must never expose "Place order", "Execute", or "Bind capital" controls anywhere in the Trading Room surface. |
| Write actions | Decision and handoff POSTs must use `If-Match` + `Idempotency-Key`; map `409` to refresh-required, `422` to governance failure. |

Suggested frontend client method signatures (all in `tradingRoom.ts`):

```ts
getTradingRoom(): Promise<TradingRoomAggregate>
getTradingRoomStrategy(strategyId: string): Promise<TradingRoomStrategyDetail>
listDecisionEvents(filter?: DecisionEventFilter): Promise<DecisionEventList>
getDecisionEvent(decisionEventId: string): Promise<TradingDecisionEvent>
recordDecision(decisionEventId: string, body: TraderDecisionBody, opts: WriteOptions): Promise<CommandResponse>
streamTradingRoom(onEvent: (event: TradingRoomStreamEvent) => void): () => void
getTradingIntent(intentId: string): Promise<TradingIntentDetail>
submitHandoff(intentId: string, body: GovernedIntentHandoff, opts: WriteOptions): Promise<CommandResponse>
withdrawHandoff(intentId: string, opts: WriteOptions): Promise<CommandResponse>
```

`DecisionEventFilter`: `{ event_kind?: "entry" | "add" | "reduce" | "exit" | "review"; state?: string }`

`TraderDecisionBody`: `{ decision: "approve" | "reject" | "defer" | "modify"; rationale?: string; modifications?: Record<string, unknown> }`

`WriteOptions`: `{ ifMatch: string; idempotencyKey: string }`

`GovernedIntentHandoff`: must include `no_order_route_proof: "agora_request_only_no_order_route"` and `action_proposal.non_binding: true` (if `action_proposal` is present).

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance — aggregate | Every `GET /bff/agora/trading-room` response validates against `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json`. |
| Schema conformance — decision event | Every `TradingDecisionEvent` response validates against `services/control-plane/specs/agora/v4/trading_decision_event.schema.json`. |
| Schema conformance — governed handoff | Every `GovernedIntentHandoff` persisted validates against `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json`. |
| Required aggregate fields | `spec_version`, `user_scope_ref`, `strategies`, `queue_summary`, `risk_summary`, `snapshot_at`, `data_cutoff` all present. |
| Required decision-event fields | `decision_event_id`, `event_kind`, `origin`, `strategy_id`, `strategy_spec_registry_id`, `subject.symbol`, `state`, `triggered_at`, `confidence`, `probability`, `expected_value`, `rationale`, `risk_notes`, `evidence_refs`, `invalidation`, `suggested_action`, `no_order_route_proof` all present. |
| `no_order_route_proof` on events | `trading_decision_event.no_order_route_proof = "agora_decision_support_only"`. |
| `no_order_route_proof` on handoffs | `governed_intent_handoff.no_order_route_proof = "agora_request_only_no_order_route"`. |
| Confidence vs probability are distinct | `confidence.value` + `confidence.basis` + `confidence.calibration_state` populated; `probability.value` + `probability.target_outcome` + `probability.horizon` populated; never merged into one field. |
| EV breakdown | `expected_value.gross`, `expected_value.cost`, `expected_value.net`, `expected_value.downside`, `expected_value.unit`, `expected_value.horizon` all present. `net = gross − cost` (within tolerance). |
| Trader decision validity | Only `approve`, `reject`, `defer`, `modify` accepted; anything else → `422`. |
| Approve creates TradingIntent | `approve` decision creates a `TradingIntent` record; it does not create a broker order, `RuntimeBinding`, or capital binding. |
| Modify creates TradingIntent | `modify` decision with `modifications` creates a `TradingIntent`; same no-order guarantee. |
| Reject/defer retained | `reject`/`defer` decisions are recorded; event transitions to `decided`; event record is not deleted. |
| Idempotency | Duplicate POST to decisions or handoffs with the same idempotency key returns the current state rather than an error or duplicate record. |
| Optimistic concurrency | `If-Match` mismatch → `409`; stale aggregate version must prompt a refresh, not silently overwrite. |
| State machine validation | Decision-event state transitions follow D5 lifecycle (`approaching → triggered → pending_review → decided`; `approaching/triggered/pending_review → invalidated`; `pending_review → expired`; `pending_review → superseded`). Invalid transitions → `422`. |
| Dedupe key | Decision events for the same strategy version, symbol, event kind, and trigger window share a `dedupe_key`; duplicate events must not create duplicate cards. |
| Governed handoff routing | `shadow` → `shadow_research` queue; `paper` → `management_governance` queue; `canary`/`live` → `promotion_review` queue. |
| No RuntimeBinding write | No Trading Room endpoint writes a `RuntimeBinding`, capital binding, or broker order under any code path. |
| Safety errors | D10 error codes returned as typed responses: `TRADING_ROOM_NOT_READY`, `TRADING_EVENT_STALE`, `TRADING_EVENT_INVALIDATED`, `TRADING_INTENT_ALREADY_RECORDED`, `TRADING_INTENT_HANDOFF_NOT_ALLOWED`, `APPROVAL_REQUIRED`, `CAPABILITY_DENIED`. |
| SSE stream | `GET /bff/agora/trading-room/stream` delivers events on decision-event state changes, queue count changes, and risk-state changes; events are typed (not raw JSON blobs). |
| BFF degraded response | When any downstream is unavailable, BFF returns a typed blocked/degraded response; it must not substitute fixture data or return a synthetic `200`. |

## Open Design Notes

### 1. AG-BE-TR-001 depends on AG-BE-CP-001 for candidate-to-decision-event promotion (D8)

The D8 candidate-to-decision-event promotion logic (Journey G) requires `AG-BE-CP-001` candidate routes to be live so the Trading Room can read the `lifecycle_state` and `candidate_ref` of candidates in `add_to_monitoring` state. Until `AG-BE-CP-001` is unblocked, the Trading Room can implement:

- Aggregate read (`GET /bff/agora/trading-room`)
- Decision-event queue reads (`GET /bff/agora/trading-room/decision-events*`)
- Trader decision recording (`POST .../decisions`) for events not originating from candidates
- SSE stream
- Trading Intent and governed handoff routes

The candidate-promotion leg of D8 should be flagged as a conditional dependency in the parent task.

### 2. AG-XR-OPENAPI-004 is done — v1.3 bundle and v4 schemas are available

`AG-XR-OPENAPI-004` completed and was archived `2026-06-21T13:30:08Z`. All v4 schemas (`trading_room_aggregate.schema.json`, `trading_decision_event.schema.json`, `governed_intent_handoff.schema.json`) are present in `services/control-plane/specs/agora/v4/` and the `agora_v1_3.openapi.yaml` defines all Trading Room and Trading Intent routes. This gate is lifted; AG-BE-TR-001 can proceed against the v1.3 bundle.

### 3. Trading Room router is a placeholder

`services/control-plane/bff/agora/trading_room/router.py` returns an empty `APIRouter`. All AG-BE-TR-001 routes must be implemented in this module; they must not be added to `main.py` or another router file. The file's migration note mentions signals routes in `main.py` — those are for `AG-BE-SIG-*` scope, not Trading Room scope.

### 4. Confidence vs probability is a strict semantic distinction (D4)

The `TradingDecisionEvent` schema defines `confidence` (evidence/model quality) and `probability` (outcome forecast) as separate required objects with distinct fields. The BFF projection must preserve both; the frontend must never collapse them into a single composite metric. Mixing them would violate D4 and AG-BE-TR-001 acceptance criteria.

### 5. `tradingRoom.ts` frontend client module does not exist

`execute-plans/src/lib/bff-v1/agora/` currently contains `dashboard.ts`, `types.ts`, and `contract-snapshot.json` only. AG-FE-TR-001 must create `tradingRoom.ts` before any Trading Room page can call BFF routes.

### 6. Frontend tasks gate on AG-BE-TR-001

`AG-FE-TR-001` (Trading Room tab + multi-strategy switcher) depends on `AG-BE-TR-001`. `AG-FE-TR-002` (CandidateReviewDrawer + entry/exit queue cards) depends on `AG-FE-TR-001` and `AG-BE-CP-001`. Until AG-BE-TR-001 lands and its routes are testable, frontend tasks cannot bind to live data.

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | `AG-BE-TR-001` is `todo` (owner `Claude2`, reviewer `Codex`); `AG-XR-OPENAPI-004` is **`done`** (archive `2026-06-21T13:30:08Z`); `AG-BE-CP-001` is `blocked`; `AG-FE-TR-001` and `AG-FE-TR-002` are `todo`; `trading_room/router.py` is a placeholder returning empty `APIRouter`. |
| Schema accuracy | `trading_room_aggregate.schema.json`, `trading_decision_event.schema.json`, and `governed_intent_handoff.schema.json` accurately described from v4 directory; required fields and `additionalProperties: false` constraints correctly reflected. |
| Route accuracy | All 9 Trading Room and Trading Intent routes from `agora_v1_3.openapi.yaml` (lines 544–703) accurately listed; none are invented or missing. |
| Open design note accuracy | AG-BE-CP-001 gate is genuine (D8 candidate-promotion requires it); XR-OPENAPI-004 gate is lifted (done); trading room router is a placeholder (confirmed). |
| Candidate pool isolation | Correct that `AG-BE-CP-001` is the sole owner of candidate state; Trading Room only reads candidate-decision references without creating a duplicate state machine. |
| No-order guard | All journeys and acceptance checks correctly exclude broker orders, `RuntimeBinding`, and capital binding. |
| Confidence vs probability | Packet correctly records D4 semantics: confidence and probability are distinct required fields that must never be merged or relabeled in BFF or UI. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: it records the Trading Room aggregate/event-queue/stream/intent BFF gap surfaces, decision-support field semantics (confidence vs probability, EV breakdown), governed-handoff no-order-route guardrails, operator journeys, frontend tradingRoom.ts client boundaries, AG-BE-CP-001 candidate-promotion gate, and AG-BE-TR-001 versus AG-BE-CP-001/AG-FE-TR-001/AG-FE-TR-002 ownership boundaries without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF \
  "Support-only AG-BE-TR-001 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF
# in_progress; owner Claude; reviewer Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex; depends_on AG-BE-CP-001 (blocked), AG-XR-OPENAPI-004 (done)

AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-OPENAPI-004
# source: archive; terminal_status: done; archived_at 2026-06-21T13:30:08Z

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# blocked; owner Codex; reviewer Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-001
# todo; owner Claude; reviewer Codex; depends_on AG-FE-SW-001, AG-BE-TR-001, AG-XR-OPENAPI-004

AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-002
# todo; owner Claude; reviewer Codex; depends_on AG-FE-TR-001, AG-BE-CP-001, AG-XR-OPENAPI-004

cat services/control-plane/bff/agora/trading_room/router.py
# Empty APIRouter; placeholder only.

grep -n "trading.room\|trading-room\|trading.intent" services/control-plane/openapi/agora_v1.openapi.yaml
# (no results) — v1 OpenAPI has no Trading Room routes.

grep -n "trading.room\|trading-room\|trading.intent" services/control-plane/openapi/agora_v1_3.openapi.yaml
# Confirms 9 routes: trading-room aggregate, strategy, decision-events list/detail/decisions, stream, trading-intents detail/handoffs/withdraw.

python3 -m json.tool services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json > /dev/null
# Valid JSON schema.

python3 -m json.tool services/control-plane/specs/agora/v4/trading_decision_event.schema.json > /dev/null
# Valid JSON schema.

python3 -m json.tool services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json > /dev/null
# Valid JSON schema.

ls execute-plans/src/lib/bff-v1/agora/
# dashboard.ts  types.ts  contract-snapshot.json
# tradingRoom.ts does not exist.
```
