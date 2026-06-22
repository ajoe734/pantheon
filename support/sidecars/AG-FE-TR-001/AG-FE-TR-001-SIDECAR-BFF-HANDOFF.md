# AG-FE-TR-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-TR-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-TR-001` — Trading Room tab + multi-strategy switcher |
| Parent owner / reviewer | `Claude` / `Codex` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It summarizes the BFF query gaps, operator journey,
and frontend handoff boundaries for `AG-FE-TR-001`; the parent owner decides
whether and how to absorb it into the main implementation.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_tr_001_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-TR-001` | Status `in_progress`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-SW-001` (**done**), `AG-BE-TR-001` (**done**), `AG-XR-OPENAPI-004` (**done**). All dependencies resolved; task is unblocked. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-SW-001` | Status `done` (archive). `TradingDeskLayout.tsx`, `StrategyWorkshopPage.tsx`, and `workshops.ts` merged. Canonical Agora IA: `/agora` → `/agora/trading-room` redirect live. TradingDeskLayout exposes three tabs: `trading-room`, `strategy-workshop`, `strategy-performance`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-TR-001` | Status `done` (archive). All Trading Room BFF routes implemented in `services/control-plane/bff/agora/trading_room/router.py`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001` | Status `done` (archive). Candidate pool BFF routes are live; Trading Room can consume candidate-decision references. |
| `services/control-plane/bff/agora/trading_room/router.py` | All nine Trading Room and Trading Intent routes are fully implemented. Safety invariants enforced: no order routing, no RuntimeBinding creation, no capital binding. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` lines 544–703 | All trading room and trading intent routes formally defined: `GET /bff/agora/trading-room`, `GET /bff/agora/trading-room/strategies/{strategy_id}`, `GET /bff/agora/trading-room/decision-events`, `GET /bff/agora/trading-room/decision-events/{decision_event_id}`, `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions`, `GET /bff/agora/trading-room/stream`, `GET /bff/agora/trading-intents/{intent_id}`, `POST /bff/agora/trading-intents/{intent_id}/handoffs`, `POST /bff/agora/trading-intents/{intent_id}/withdraw`. |
| `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json` | `TradingRoomAggregate` v1.0: required `spec_version`, `user_scope_ref`, `strategies[]`, `queue_summary`, `risk_summary`, `snapshot_at`, `data_cutoff`. Per-strategy required: `strategy_id`, `strategy_spec_registry_id`, `title`, `readiness_state` (blocked/conditional/ready/stale), `monitoring_state` (inactive/shadow/paper_requested/monitoring/paused), `pending_event_counts` (entry/add/reduce/exit/review). `additionalProperties: false` at both levels. |
| `services/control-plane/specs/agora/v4/trading_decision_event.schema.json` | `TradingDecisionEvent` v1.0: required `spec_version`, `decision_event_id`, `event_kind` (entry/add/reduce/exit/review), `origin`, `strategy_id`, `strategy_spec_registry_id`, `subject` (symbol required), `state` (approaching/triggered/pending_review/decided/expired/invalidated/superseded), `triggered_at`, `confidence` (value/basis/calibration_state), `probability` (target_outcome/horizon/value), `expected_value` (horizon/unit/gross/cost/net/downside), `rationale[]`, `risk_notes[]`, `evidence_refs[]`, `invalidation` (conditions/current_state), `suggested_action`, `no_order_route_proof` (`"agora_decision_support_only"`). `additionalProperties: false`. |
| `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | `GovernedIntentHandoff` v1.0: required `spec_version`, `handoff_id`, `intent_id`, `requested_stage` (shadow/paper/canary/live), `handoff_type` (shadow_start/paper_validation_request/promotion_review_request), `state` (draft/submitted/accepted/rejected/expired/withdrawn/converted), `strategy_id`, `strategy_spec_registry_id`, `requested_by` (actor), `evidence_refs[]`, `no_order_route_proof` (`"agora_request_only_no_order_route"`), `created_at`. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | TypeScript types file contains `TradingEvent` and `TradingIntent` (v1 observation-layer types). Does NOT contain `TradingRoomAggregate`, `TradingDecisionEvent`, or `GovernedIntentHandoff` — these v4 types are missing and must be added for `tradingRoom.ts` to type-check. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does NOT exist. Must be created by AG-FE-TR-001. This is the sole frontend-to-Trading-Room BFF channel. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Does NOT exist. Must be created by AG-FE-TR-001. |
| `execute-plans/src/agora/TradingDeskLayout.tsx` | Exists and is done. Defines `AgoraTab = "trading-room" | "strategy-workshop" | "strategy-performance"`. `trading-room` tab slot is present but receives no page component from AG-FE-TR-001 yet. |
| `execute-plans/src/lib/bff-v1/agora/workshops.ts` | Existing pattern: `resolvedBase()`, `recordFrom()`, `parseJson()`, typed fetch functions, typed normalisation helpers. `tradingRoom.ts` must follow the same live-strict pattern. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF is the sole frontend aggregation point. Trading Room routes must return typed degraded/blocked responses when downstream is unavailable. BFF failure must not affect active runtimes. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Current Frontend State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | AG-FE-TR-001 must create this module; it is the only frontend-to-Trading-Room channel. Must follow `workshops.ts` live-strict pattern. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Does not exist. | AG-FE-TR-001 must create this; it is the React page rendered when `activeTab === "trading-room"` in `TradingDeskLayout`. |
| `TradingRoomAggregate` TypeScript type in `types.ts` | Missing. | AG-FE-TR-001 must add `TradingRoomAggregate`, `TradingDecisionEvent`, and `GovernedIntentHandoff` interfaces to `types.ts` (or a peer file) mirroring the v4 schemas verbatim. No invented fields. |
| `execute-plans/src/agora/TradingDeskLayout.tsx` | Exists and is correct. `trading-room` tab is defined. | AG-FE-TR-001 renders `<TradingRoomPage>` into the `trading-room` tab slot. Layout is not modified. |
| `execute-plans/src/agora/pages/strategy-workshop/` | Exists. Pattern: page component + page-specific test file. | `trading-room/` directory must follow the same structure. |
| Backend BFF routes | All nine routes implemented (router.py, store.py). Live at v1.3 contract. | No backend work needed for AG-FE-TR-001. Frontend can bind immediately. |
| v4 schemas | All four relevant schemas present (trading_room_aggregate, trading_decision_event, governed_intent_handoff, trading_event). | TypeScript types in `types.ts` must match these schemas field-for-field. |
| v1.3 OpenAPI | All nine trading room / trading intent operationIds are present. | `tradingRoom.ts` function names must map to these operationIds (see table below). |

---

## BFF Route → Frontend Function Map

| BFF operationId (v1.3) | `tradingRoom.ts` function | Return type |
|---|---|---|
| `getAgoraTradingRoom` | `getTradingRoom(baseUrl?)` | `Promise<TradingRoomAggregate>` |
| `getAgoraTradingRoomStrategy` | `getTradingRoomStrategy(strategyId, baseUrl?)` | `Promise<DetailEnvelope>` |
| `listAgoraTradingDecisionEvents` | `listDecisionEvents(params?, baseUrl?)` | `Promise<TradingDecisionEvent[]>` |
| `getAgoraTradingDecisionEvent` | `getDecisionEvent(decisionEventId, baseUrl?)` | `Promise<TradingDecisionEvent>` |
| `decideAgoraTradingEvent` | `decideEvent(decisionEventId, body, headers?, baseUrl?)` | `Promise<CommandResponse>` |
| `streamAgoraTradingRoom` | `streamTradingRoom(baseUrl?)` | `EventSource` (or `URL`) |
| `getAgoraTradingIntent` | `getTradingIntent(intentId, baseUrl?)` | `Promise<DetailEnvelope>` |
| `submitAgoraTradingIntentHandoff` | `submitIntentHandoff(intentId, body, headers?, baseUrl?)` | `Promise<CommandResponse>` |
| `withdrawAgoraTradingIntent` | `withdrawIntent(intentId, headers?, baseUrl?)` | `Promise<CommandResponse>` |

**Query parameters for `listDecisionEvents`:**
- `event_kind`: `"entry" | "add" | "reduce" | "exit" | "review"` (optional filter)
- `state`: `string` (optional filter)

**Request body for `decideEvent`:**
```ts
interface DecideEventBody {
  decision: "approve" | "reject" | "defer" | "modify";
  rationale?: string;
  modifications?: Record<string, unknown>;
}
```

**Mutation headers (if-match, idempotency-key, x-request-id) for `decideEvent`, `submitIntentHandoff`, `withdrawIntent`:**
```ts
interface MutationHeaders {
  "If-Match"?: string;
  "Idempotency-Key"?: string;
  "X-Request-ID"?: string;
}
```

---

## TypeScript Types Required in `types.ts`

These three interfaces must be added. They must mirror the v4 schemas verbatim — no invented fields, no optional-ification of required fields.

### `TradingRoomAggregate`

```ts
export interface TradingRoomAggregate {
  spec_version: "1.0";
  user_scope_ref: string;
  strategies: Array<{
    strategy_id: string;
    strategy_spec_registry_id: string;
    title: string;
    readiness_state: "blocked" | "conditional" | "ready" | "stale";
    monitoring_state: "inactive" | "shadow" | "paper_requested" | "monitoring" | "paused";
    pending_event_counts: {
      entry?: number;
      add?: number;
      reduce?: number;
      exit?: number;
      review?: number;
    };
    dashboard_recipe_id?: string;
    candidate_count?: number;
    position_count?: number;
    shadow_status?: string;
    performance_summary?: Record<string, unknown>;
    staleness_reasons?: string[];
  }>;
  queue_summary: {
    entry: number;
    add: number;
    reduce: number;
    exit: number;
    review: number;
  };
  top_decision_events?: TradingDecisionEvent[];
  position_summaries?: Array<Record<string, unknown>>;
  risk_summary: {
    state: "normal" | "watch" | "warning" | "critical";
    summary?: string;
    alerts?: string[];
  };
  snapshot_at: string;
  data_cutoff: string;
}
```

### `TradingDecisionEvent`

```ts
export interface TradingDecisionEvent {
  spec_version: "1.0";
  decision_event_id: string;
  dedupe_key?: string;
  event_kind: "entry" | "add" | "reduce" | "exit" | "review";
  origin: "strategy_signal" | "risk_rule" | "position_rule" | "servant_analysis" | "trader_request";
  strategy_id: string;
  strategy_spec_registry_id: string;
  candidate_ref?: string;
  position_ref?: string;
  subject: { symbol: string; asset_class?: string; venue?: string };
  state: "approaching" | "triggered" | "pending_review" | "decided" | "expired" | "invalidated" | "superseded";
  trigger?: { rule_id?: string; summary?: string; current_value?: unknown; threshold?: unknown; distance_to_trigger?: number };
  triggered_at: string;
  expires_at?: string;
  confidence: { value: number; basis: "model" | "statistical" | "heuristic" | "mixed"; calibration_state: "calibrated" | "partially_calibrated" | "uncalibrated"; sample_size?: number; source_ref?: string };
  probability: { target_outcome: string; horizon: string; value: number; ci_lower?: number; ci_upper?: number; model_ref?: string; as_of?: string };
  expected_value: { horizon: string; unit: "pct_return" | "currency" | "risk_units"; gross: number; cost: number; net: number; downside: number; expected_shortfall?: number };
  rationale: Array<{ claim: string; confidence: number; evidence_refs?: EvidenceRef[] }>;
  risk_notes: Array<{ severity: "info" | "watch" | "warning" | "high" | "critical"; domain: string; summary: string; mitigation?: string }>;
  evidence_refs: EvidenceRef[];
  invalidation: { conditions: string[]; current_state: "valid" | "watch" | "invalidated"; last_checked_at?: string };
  suggested_action: "enter" | "add" | "reduce" | "exit" | "review" | "no_action";
  suggested_size?: { size_hint?: "small" | "medium" | "large" | "full_position"; portfolio_pct?: number; non_binding: true };
  position_snapshot?: Record<string, unknown>;
  decision_state?: "pending" | "approved_by_trader" | "rejected_by_trader" | "deferred" | "expired" | "handed_off" | "superseded";
  data_cutoff?: string;
  no_order_route_proof: "agora_decision_support_only";
}

interface EvidenceRef {
  ref_type: "evidence_bundle" | "evidence_item" | "source_record" | "citation" | "experiment_artifact" | "registry_entry" | "consult_memo" | "research_run" | "telemetry_snapshot" | "market_context";
  ref_id: string;
  summary?: string;
  data_cutoff?: string;
}
```

### `GovernedIntentHandoff`

```ts
export interface GovernedIntentHandoff {
  spec_version: "1.0";
  handoff_id: string;
  intent_id: string;
  decision_event_id?: string;
  requested_stage: "shadow" | "paper" | "canary" | "live";
  handoff_type: "shadow_start" | "paper_validation_request" | "promotion_review_request";
  state: "draft" | "submitted" | "accepted" | "rejected" | "expired" | "withdrawn" | "converted";
  strategy_id: string;
  strategy_spec_registry_id: string;
  requested_by: { actor_type: "trader" | "agora_servant" | "institutional_persona" | "system"; actor_ref: string; session_id?: string; display_name?: string };
  target_queue?: "shadow_research" | "management_governance" | "promotion_review";
  required_gate_refs?: string[];
  action_proposal?: { action?: "enter" | "add" | "reduce" | "exit" | "review"; symbol?: string; direction?: string; size_hint?: string; portfolio_pct?: number; non_binding: true };
  rationale?: string;
  risk_summary?: string;
  evidence_refs: EvidenceRef[];
  management_handoff_ref?: string;
  deployment_plan_ref?: string;
  runtime_binding_ref?: string;
  no_order_route_proof: "agora_request_only_no_order_route";
  created_at: string;
  updated_at?: string;
  expires_at?: string;
}
```

`EvidenceRef` is shared between `TradingDecisionEvent` and `GovernedIntentHandoff`. Define it once as a local interface or export it.

### Envelope types needed

`tradingRoom.ts` also uses these envelope shapes already present in the codebase pattern:

```ts
interface DetailEnvelope { data?: Record<string, unknown>; [key: string]: unknown }
interface CommandResponse { [key: string]: unknown }
```

These do not need to be added to `types.ts` if they're defined locally in `tradingRoom.ts`.

---

## Operator Journey Through the Trading Room

This journey describes the primary flow an operator follows when the Trading Room tab is live. AG-FE-TR-001 must implement the data surface at each step; the UI shape follows SD §10.4/§12.1 strictly.

1. **Land on Trading Room tab** (`/agora/trading-room`)
   - Frontend calls `getTradingRoom()` → returns `TradingRoomAggregate`.
   - Renders the multi-strategy switcher: one row per `strategies[]` entry, showing `title`, `readiness_state`, `monitoring_state`, and `pending_event_counts` badge per kind.
   - Renders `queue_summary` total badge (entry/add/reduce/exit/review totals across all strategies).
   - Renders `risk_summary.state` indicator badge.

2. **Select a specific strategy** (`/agora/trading-room/:strategyId`)
   - Frontend calls `getTradingRoomStrategy(strategyId)` → returns strategy-level `DetailEnvelope`.
   - Renders strategy workspace with strategy-scoped decision event queue.

3. **Browse decision event queue** (within strategy workspace)
   - Frontend calls `listDecisionEvents({ event_kind })` to fetch filtered queues per kind.
   - Renders per-event card: `event_kind` label, `subject.symbol`, `state`, `confidence.value`, `probability.value`, `expected_value.net`, `suggested_action`, risk severity.
   - Queue filter tabs: entry | add | reduce | exit | review.

4. **Open decision event detail**
   - Frontend calls `getDecisionEvent(decision_event_id)` → full `TradingDecisionEvent`.
   - Renders: full `rationale[]` list, `risk_notes[]` list, `evidence_refs[]` list, `invalidation` status, `suggested_size` if present.
   - All fields rendered must use schema-defined keys. No invented display fields.

5. **Trader decision** (approve / reject / defer / modify)
   - Frontend calls `decideEvent(decision_event_id, { decision, rationale?, modifications? }, mutationHeaders)`.
   - `approve` or `modify` creates a `TradingIntent` server-side (request-only, no broker order).
   - On `201` response, refresh the event state.
   - Frontend must present `no_order_route_proof: "agora_decision_support_only"` semantics in the UI: decision is support-only, not an order instruction.

6. **View trading intent** (after approve/modify)
   - Frontend calls `getTradingIntent(intent_id)` → `DetailEnvelope`.
   - Renders intent detail if intent_id is available from `CommandResponse` after decision.

7. **Submit governed handoff** (optional — operator initiates governance routing)
   - Frontend calls `submitIntentHandoff(intent_id, GovernedIntentHandoff body, mutationHeaders)`.
   - `no_order_route_proof: "agora_request_only_no_order_route"` must be set by the frontend on the request body.
   - Backend routes to `target_queue` (shadow_research / management_governance / promotion_review).
   - No RuntimeBinding or capital binding created. Frontend must not present this as an order action.

8. **Withdraw intent / handoff**
   - Frontend calls `withdrawIntent(intent_id, mutationHeaders)`.
   - Refresh intent and event state.

9. **Live SSE stream** (`getTradingRoom`-scoped events)
   - Frontend opens `streamTradingRoom()` → typed SSE stream.
   - Receives: decision-event state changes, queue count deltas, risk summary changes.
   - Updates in-memory state without full re-fetch.

---

## Parent Scope Boundary

`AG-FE-TR-001` owns:

- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` — all nine BFF route bindings.
- `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` — the React page component.
- TypeScript interface additions to `types.ts` (or a peer `tradingRoomTypes.ts`): `TradingRoomAggregate`, `TradingDecisionEvent`, `GovernedIntentHandoff`.
- Route wiring: `/agora/trading-room` and `/agora/trading-room/:strategyId` mounted in `agora-main.tsx` (or equivalent router file) rendering `TradingRoomPage` inside `TradingDeskLayout`.
- Frontend tests: `TradingRoomPage.test.tsx` covering tab display, strategy switcher, decision event queue rendering, and decision submit.

`AG-FE-TR-001` does **not** own:

- BFF backend routes (done by `AG-BE-TR-001`).
- `TradingDeskLayout.tsx` layout or tab definitions (done by `AG-FE-SW-001`; do not modify).
- `workshops.ts` or `StrategyWorkshopPage.tsx` (done by `AG-FE-SW-001`).
- `types.ts` types for `TradingEvent` and `TradingIntent` v1 observation layer (existing; do not remove or rename).
- OpenAPI schemas, JSON schemas, or BFF Python code.
- `RuntimeBinding`, capital binding, broker order, or live/paper governance promotion — these are never touched by Agora frontend.
- Candidate pool listing, scoring, or candidate-decision recording (owned by AG-BE-CP-001 backend and related frontend tasks).

---

## Safety Constraints

These must be enforced in `tradingRoom.ts` and never weakened:

| Constraint | Enforcement |
|---|---|
| No order routing | `decideEvent` result is a `CommandResponse` (decision record), not a broker instruction. Frontend must not label it as an order. |
| No RuntimeBinding creation | `submitIntentHandoff` sets `no_order_route_proof: "agora_request_only_no_order_route"` in the request body; frontend must not omit this field. |
| No capital binding | Neither `tradingRoom.ts` nor `TradingRoomPage.tsx` writes to capital or runtime endpoints. |
| `TradingDecisionEvent.no_order_route_proof` must be rendered | If the frontend renders the field, it must show `"agora_decision_support_only"` and never present it as an actionable order. |
| Typed degraded response | Per `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`: if `getTradingRoom` returns a degraded/blocked envelope, `TradingRoomPage` must render a typed degraded state, not throw. |
| `additionalProperties: false` on v4 schemas | `TradingRoomAggregate` and `TradingDecisionEvent` use `additionalProperties: false`. TypeScript interfaces must not add fields. |
| No self-created intents | `TradingIntent` objects are created server-side by `decideEvent` when decision is `approve`/`modify`. Frontend does not POST to a `/trading-intents` creation endpoint. |

---

## Contract Snapshot Gap

`execute-plans/src/lib/bff-v1/agora/types.ts` was generated from the v1.1 contract snapshot (`AG-XR-001`). The v1.3 extension bundle (`agora_v1_3.openapi.yaml`) adds the nine trading room routes and the three new v4 schemas. These are not in `AGORA_V1_CONTRACT_SNAPSHOT` yet.

**Implication for AG-FE-TR-001:**
- Add the three v4 type interfaces manually (see § TypeScript Types Required above).
- After `tradingRoom.ts` is done, a follow-on task should update the contract snapshot to include the v1.3 trading room routes and schemas.
- Do NOT update the generated `types.ts` GENERATED FILE header comment unless the full regeneration script is run (see `node scripts/generate-agora-types.mjs`).

---

## Dependency Resolution Summary

| Dependency | Status | Meaning for AG-FE-TR-001 |
|---|---|---|
| `AG-FE-SW-001` — TradingDeskLayout + tabs | **done** | `trading-room` tab slot is ready to receive `<TradingRoomPage>`. No changes needed to layout. |
| `AG-BE-TR-001` — Backend BFF routes | **done** | All nine v1.3 routes live at runtime. Frontend can call immediately. |
| `AG-BE-CP-001` — Candidate pool BFF | **done** | Trading Room can reference candidate-decision data. Frontend does not need to implement candidate-pool calls; references arrive via `TradingDecisionEvent.candidate_ref`. |
| `AG-XR-OPENAPI-004` — v1.3 OpenAPI bundle | **done** | Authoritative contract is at `agora_v1_3.openapi.yaml`. Use as source-of-truth for paths and request/response shapes. |

**AG-FE-TR-001 is fully unblocked as of 2026-06-22.**

---

## What Must Not Happen

- Do not invent schema fields or create new enums beyond what the v4 schemas define.
- Do not modify `TradingDeskLayout.tsx` beyond wiring `<TradingRoomPage>` into the `trading-room` tab slot.
- Do not add Trading Room capability to `AGORA_V1_CAPABILITIES` or `types.ts` contract snapshot entries — the contract snapshot covers v1.1; the v1.3 extension is handled separately.
- Do not create a direct `fetch` call inside `TradingRoomPage` — all BFF access must go through `tradingRoom.ts`.
- Do not write a `RuntimeBinding`, capital binding, or governance promotion from any frontend code.
- Do not rename or remove existing `TradingEvent` or `TradingIntent` v1 types from `types.ts`.
- If any design spec question is unclear (SD §10.4/§12.1/§23, design-closure), stop and open a blocker. Do not self-implement unclear UI.
