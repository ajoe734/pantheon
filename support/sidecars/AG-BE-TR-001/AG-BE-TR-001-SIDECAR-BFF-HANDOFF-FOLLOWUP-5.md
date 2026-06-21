# AG-BE-TR-001 BFF and Frontend Handoff Packet — Followup 5

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` — Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Supersedes / builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. The parent owner decides whether and how to absorb
this material.

## Cumulative packet scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF` (done) | BFF query gap matrix, operator journeys A–H, `tradingRoom.ts` method signatures, acceptance checks, open design notes. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done) | Phased implementation sequence, backend module structure, D9 position event fields, Trading Room SSE contract, BFF degraded-response patterns, TypeScript types, safety wording, pending questions Q1–Q5. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (done) | Schema-derived TypeScript type corrections, Q1/Q2/Q4 resolutions, `additionalProperties` degradation-signalling clarification, idempotency implementation pattern, BFF test structure supplement, remaining open questions Q3/Q5/Q6/Q7. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` (done) | Q3/Q5/Q6/Q7 resolutions, SSE channel catalog gap, router injection gap (missing `get_read_store` and SSE hooks), `GovernedIntentHandoff` action_proposal and state lifecycle types, additional acceptance checks. |
| **This packet (FOLLOWUP-5)** | `CommandResponse` shape correction vs Packet 2, `DetailEnvelope` and `ListEnvelope` wrapper clarifications, `TradingIntent` schema revealed (not in v4 bundle, distinct `no_order_route_proof`), `StrategyReadinessAssessment` gate structure and `readiness_state` derivation, `allowedActions` guidance for `DetailEnvelope`, and updated TypeScript types for all corrected surfaces. |

## Current state observed

| Surface | Observed 2026-06-21 | Change since Packet 4 |
|---|---|---|
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. D8 promotion leg still gated. |
| `AG-FE-TR-001` | `todo`. | Unchanged. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. |
| `trading_intent.schema.json` | Present at `services/control-plane/specs/agora/trading_intent.schema.json`; NOT in v4 bundle. | **New finding** — see § TradingIntent schema below. |
| `strategy_readiness.schema.json` | Present at `services/control-plane/specs/agora/v4/strategy_readiness.schema.json`; in v1.3 bundle. | **New finding** — see § StrategyReadinessAssessment gate structure below. |

## New findings

### Finding 1 — `CommandResponse` shape mismatch

**Packet 2 published this TypeScript type:**

```ts
export interface CommandResponse {
  command_id: string;
  status: "accepted" | "created";
  resource_id?: string;
}
```

**The `agora_v1_3.openapi.yaml` component schema defines:**

```yaml
CommandResponse:
  type: object
  required: [status, data, meta]
  properties:
    status:
      type: string
      enum: [accepted, queued, completed]
    data: {}
    meta:
      type: object
      additionalProperties: true
```

**Corrections required:**

1. `command_id` and `resource_id` are **not in the v1.3 schema**. The Packet 2 type invented these fields.
2. The `status` enum is `"accepted" | "queued" | "completed"` — **not** `"accepted" | "created"`.
3. The response includes a `data` payload field (the schema uses an open `{}` type) and a `meta` object.
4. If the BFF needs to return a new resource ID (e.g. `intent_id` after `POST .../decisions`), the ID should be embedded in the `data` object, not in a top-level `resource_id` field.

**Corrected TypeScript type:**

```ts
export interface CommandResponse {
  status: "accepted" | "queued" | "completed";
  data: Record<string, unknown>;
  meta: Record<string, unknown>;
}

// Helpers for known data shapes (implementation-level, not schema-level):
export interface DecisionCommandData {
  intent_id?: string;             // present when decision is "approve" or "modify"
  decision_event_id: string;
  decision_state: string;         // new state after the decision
}

export interface HandoffCommandData {
  handoff_id: string;
  intent_id: string;
  target_queue: string;           // "shadow_research" | "management_governance" | "promotion_review"
  handoff_state: string;          // "submitted"
}

export interface WithdrawCommandData {
  intent_id: string;
  withdrawal_state: string;       // "withdrawn"
}
```

**Test implication**: existing Packet 3 test case `test_no_order_route_proof_on_intent` reads `response.json().resource_id` — this must be changed to read `response.json().data.intent_id`.

### Finding 2 — `DetailEnvelope` and `ListEnvelope` wrappers not documented in prior packets

Two Trading Room routes return envelope types rather than raw schema objects. These envelopes were not documented in prior packets.

#### Routes that return `DetailEnvelope`

- `GET /bff/agora/trading-room/strategies/{strategy_id}`
- `GET /bff/agora/trading-intents/{intent_id}`

**`DetailEnvelope` v1.3 schema:**

```yaml
required: [object_ref, status, allowedActions, meta, links, data]
properties:
  object_ref:
    required: [type, id]
    properties:
      type: string
      id:   string
  status: string
  lifecycle_state: string          # optional
  allowedActions:
    additionalProperties: boolean  # {action_name: true|false}
  meta:
    additionalProperties: true
  links:
    additionalProperties: true
  data: {}                         # the wrapped payload (strategy or intent)
additionalProperties: false
```

**TypeScript type:**

```ts
export interface DetailEnvelope<T = Record<string, unknown>> {
  object_ref: { type: string; id: string };
  status: string;
  lifecycle_state?: string;
  allowedActions: Record<string, boolean>;
  meta: Record<string, unknown>;
  links: Record<string, unknown>;
  data: T;
}
```

**BFF construction guidance for `GET /bff/agora/trading-intents/{intent_id}`:**

```python
intent_data = trading_intent_store.get(intent_id)
if intent_data is None:
    raise bff_error("TRADING_INTENT_NOT_FOUND", status_code=404)

allowed = _compute_intent_allowed_actions(intent_data)

return {
    "object_ref": {"type": "trading_intent", "id": intent_id},
    "status": intent_data.get("state", "unknown"),
    "lifecycle_state": intent_data.get("state"),
    "allowedActions": allowed,
    "meta": {"intent_type": intent_data.get("intent_type")},
    "links": {},
    "data": intent_data,
}
```

**`allowedActions` for TradingIntent** — the BFF must populate this dict based on the intent's current `state`. Recommended matrix (for owner decision, not canonical):

| Intent state | `submit_handoff` | `withdraw` | `view` |
|---|---|---|---|
| `pending` (no handoff yet) | `true` | `true` | `true` |
| `handoff_submitted` | `false` | `true` | `true` |
| `withdrawn` | `false` | `false` | `true` |
| `completed` (handoff accepted/converted) | `false` | `false` | `true` |

#### Routes that return `ListEnvelope`

- `GET /bff/agora/trading-room/decision-events`

**`ListEnvelope` v1.3 schema:**

```yaml
required: [items, page_info, meta]
properties:
  items:
    type: array
    items: {}
  page_info:
    required: [next_page_token, page_size, has_more]
    properties:
      next_page_token: string | null
      page_size: integer
      has_more: boolean
      total: integer               # optional
  meta:
    additionalProperties: true
additionalProperties: false
```

**TypeScript type:**

```ts
export interface ListEnvelope<T = Record<string, unknown>> {
  items: T[];
  page_info: {
    next_page_token: string | null;
    page_size: number;
    has_more: boolean;
    total?: number;
  };
  meta: Record<string, unknown>;
}
```

**Pagination query parameter**: the v1.3 OpenAPI does not define a `page_token` query parameter on `listAgoraTradingDecisionEvents`, but the `ListEnvelope` requires `next_page_token`. For the initial implementation, the BFF should:

1. Accept an optional `page_token` query parameter (not schema-validated on the server side).
2. Return `next_page_token: null` and `has_more: false` when all events fit in one page.
3. Set `page_size` to the number of items actually returned.
4. Optionally populate `total` with the total count across all pages.

This is a recommended approach; the parent owner should decide whether to add `page_token` to the OpenAPI spec as a follow-on.

**`GET /bff/agora/trading-room` still returns raw `TradingRoomAggregate`**, not an envelope. `GET /bff/agora/trading-room/decision-events/{id}` still returns raw `TradingDecisionEvent`. These were correctly documented in prior packets.

### Finding 3 — `TradingIntent` schema revealed

The `services/control-plane/specs/agora/trading_intent.schema.json` file defines `TradingIntent` v1.0. This schema is **not included in the v1.3 bundle** (`bundle_index.v1_3.json` does not list it). It is the non-v4 schema for the intent record created when a trader approves or modifies a decision event.

**Key schema details not in prior packets:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `spec_version` | `"1.0"` | Yes | Const enum. |
| `intent_id` | `string` | Yes | Unique intent ID. |
| `operator_id` | `string` | Yes | Operator who expressed the intent. |
| `intent_type` | enum | Yes | See below. |
| `direction` | enum | Yes | See below. |
| `subject` | `{symbol, asset_class?, venue?, strategy_ref?}` | Yes | `additionalProperties: false`. |
| `expressed_at` | ISO-8601 | Yes | |
| `no_order_route_proof` | `"agora_intent_record_only"` | Yes | **Different from `GovernedIntentHandoff`** — see below. |
| `rationale` | `string` | No | Free-text; valuable for imitation learning. |
| `size_hint` | enum | No | `"small" | "medium" | "large" | "full_position"`. |
| `timeframe_hint` | `string` | No | Approximate holding period (e.g. `"intraday"`, `"swing"`). |
| `confidence` | `0–1` | No | Operator's self-reported confidence. |
| `linked_event_ids` | `string[]` | No | Decision event IDs that triggered this intent. Default `[]`. |
| `learning_eligible` | `boolean` | No | Default `true`; whether this intent may be used for imitation learning. |
| `persona_id` | `string` | No | Persona that co-expressed the intent. |
| `session_id` | `string` | No | Agora session context. |
| `metadata` | `object` | No | `additionalProperties: true`. |
| (root) | | | `additionalProperties: false`. |

**`intent_type` enum values:**

```ts
type TradingIntentType =
  | "buy_interest"
  | "sell_interest"
  | "hold_decision"
  | "reduce_exposure"
  | "increase_exposure"
  | "hedge_intent"
  | "exit_intent"
  | "entry_interest";
```

**`direction` enum values:**

```ts
type TradingIntentDirection = "long" | "short" | "neutral" | "reduce" | "exit";
```

**Critical distinction — `no_order_route_proof` values:**

| Schema | `no_order_route_proof` value | Meaning |
|---|---|---|
| `TradingIntent` | `"agora_intent_record_only"` | The intent is a record of stated intent only; never routes orders. |
| `GovernedIntentHandoff` | `"agora_request_only_no_order_route"` | The handoff is a governance routing request only; never routes orders. |

These are **two different proof-claim values** on two different schemas. The BFF must set the correct value on each record type. The common string `"agora_decision_support_only"` on `TradingDecisionEvent` is a third distinct value.

**TradingIntent — mapping from trader decision to intent fields:**

When a `POST .../decisions` body with `{decision: "approve"}` arrives, the BFF creates a `TradingIntent` record. Recommended field mapping:

| `TradingIntent` field | Source |
|---|---|
| `intent_id` | Generate a new UUID. |
| `operator_id` | `identity.operator_id`. |
| `intent_type` | Derive from `decision_event.event_kind` (see mapping below). |
| `direction` | Derive from `decision_event.suggested_action` (see mapping below). |
| `subject` | Copy from `decision_event.subject`. |
| `expressed_at` | `utc_now()`. |
| `no_order_route_proof` | `"agora_intent_record_only"` (const). |
| `rationale` | `body.rationale` (optional). |
| `size_hint` | From `body.modifications.size_hint` if present and `decision == "modify"`. |
| `linked_event_ids` | `[decision_event_id]`. |
| `learning_eligible` | `true` (default). |
| `session_id` | From request context if available. |
| `confidence` | Omit (operator self-report, not available at BFF layer). |

**`intent_type` mapping from `event_kind`:**

| `event_kind` | Recommended `intent_type` |
|---|---|
| `entry` | `"entry_interest"` |
| `add` | `"increase_exposure"` |
| `reduce` | `"reduce_exposure"` |
| `exit` | `"exit_intent"` |
| `review` | `"hold_decision"` |

**`direction` mapping from `suggested_action`:**

| `suggested_action` | Recommended `direction` |
|---|---|
| `enter` | `"long"` (default; `"short"` if the event subject has a short-side indicator) |
| `add` | `"long"` or `"short"` (match position direction) |
| `reduce` | `"reduce"` |
| `exit` | `"exit"` |
| `review` | `"neutral"` |
| `no_action` | `"neutral"` |

**Owner note**: these mappings are recommendations, not canonical decisions. The parent owner should confirm or adjust the mapping logic.

**TradingIntent is not in the v4 bundle** — the `GET /bff/agora/trading-intents/{intent_id}` endpoint returns a `DetailEnvelope` wrapping the intent record. The BFF should store intent records using the `trading_intent.schema.json` shape and validate outgoing records against that schema in the test suite.

**TypeScript type for `TradingIntent` (full, corrected):**

```ts
export interface TradingIntent {
  spec_version: "1.0";
  intent_id: string;
  operator_id: string;
  intent_type:
    | "buy_interest" | "sell_interest" | "hold_decision"
    | "reduce_exposure" | "increase_exposure" | "hedge_intent"
    | "exit_intent" | "entry_interest";
  direction: "long" | "short" | "neutral" | "reduce" | "exit";
  subject: {
    symbol: string;
    asset_class?: string;
    venue?: string;
    strategy_ref?: string;
  };
  expressed_at: string;
  no_order_route_proof: "agora_intent_record_only";
  // Optional fields:
  persona_id?: string;
  session_id?: string;
  rationale?: string;
  size_hint?: "small" | "medium" | "large" | "full_position";
  timeframe_hint?: string;
  confidence?: number;
  linked_event_ids?: string[];
  learning_eligible?: boolean;
  metadata?: Record<string, unknown>;
}
```

### Finding 4 — `StrategyReadinessAssessment` gate structure and `readiness_state` derivation

The `services/control-plane/specs/agora/v4/strategy_readiness.schema.json` defines the `StrategyReadinessAssessment` object that drives the `readiness_state` field in `TradingRoomStrategy`.

**Key schema constraints:**

- `gates` array has `minItems: 3, maxItems: 3` — exactly 3 gates, always in order: `preliminary_research`, `full_validation`, `trading_room`.
- Each gate has a `state` from `"not_assessed" | "blocked" | "conditional" | "ready" | "stale"`.
- `highest_ready_gate` enum: `"preliminary_research" | "full_validation" | "trading_room" | null`.

**Gate definition:**

```ts
interface ReadinessGate {
  gate: "preliminary_research" | "full_validation" | "trading_room";
  state: "not_assessed" | "blocked" | "conditional" | "ready" | "stale";
  requirements: ReadinessRequirement[];
  blocking_requirement_ids?: string[];
  conditional_assumptions?: string[];
  evaluated_at?: string;
}
```

**How `readiness_state` in `TradingRoomStrategy` maps to `StrategyReadinessAssessment`:**

The `trading_room_aggregate.schema.json` defines `readiness_state` on each strategy as one of `"blocked" | "conditional" | "ready" | "stale"`. This maps to the `trading_room` gate `state` in the most recent `StrategyReadinessAssessment`:

| `StrategyReadinessAssessment.gates[trading_room].state` | `TradingRoomStrategy.readiness_state` |
|---|---|
| `"not_assessed"` | `"blocked"` |
| `"blocked"` | `"blocked"` |
| `"conditional"` | `"conditional"` |
| `"ready"` | `"ready"` |
| `"stale"` | `"stale"` |

**Recommendation for the BFF aggregate projection:** when building `TradingRoomAggregate.strategies[]`, the BFF should fetch the latest `StrategyReadinessAssessment` for each strategy and extract the `trading_room` gate state. The `valid_until` field on the assessment (optional ISO-8601) provides a freshness hint — if `valid_until < utc_now()`, the assessment is stale and `readiness_state` should be set to `"stale"` regardless of the gate state. The `staleness_reasons` array on the assessment should be copied to `TradingRoomStrategy.staleness_reasons`.

**Schema validation note:** `StrategyReadinessAssessment.assessment_id` must match the pattern `^ready_[A-Za-z0-9_-]+$`. The BFF should not generate or validate this ID; it is owned by the readiness evaluation service.

## Additional acceptance checks

These checks supplement the checks in Packets 1, 3, and 4:

| Check | Expected result |
|---|---|
| `CommandResponse` shape | `POST .../decisions`, `POST .../handoffs`, `POST .../withdraw` all return `{status, data, meta}`. No `command_id` or `resource_id` fields at the root. Status is one of `"accepted" | "queued" | "completed"`. |
| `CommandResponse.data` contains intent ID | After `POST .../decisions` with `{decision: "approve"}`, `response.data.intent_id` is present and non-null. |
| `GET .../decision-events` returns `ListEnvelope` | Response includes `items`, `page_info`, and `meta`. `page_info.next_page_token` is `string | null`. |
| `ListEnvelope.page_info` shape | `page_info.has_more: false` when all events returned in one page; `page_info.page_size` equals `items.length`. |
| `GET .../strategies/{id}` returns `DetailEnvelope` | Response includes `object_ref.type = "trading_room_strategy"`, `allowedActions`, `meta`, `links`, `data`. |
| `GET .../trading-intents/{id}` returns `DetailEnvelope` | Response includes `object_ref.type = "trading_intent"`, `allowedActions`, `data` contains a valid `TradingIntent` record. |
| `DetailEnvelope.allowedActions` present and accurate | For an intent in `pending` state: `submit_handoff: true`, `withdraw: true`. For a `withdrawn` intent: both `false`. |
| `TradingIntent.no_order_route_proof` value | Created `TradingIntent` records carry `no_order_route_proof: "agora_intent_record_only"` — NOT `"agora_request_only_no_order_route"`. |
| `TradingIntent.linked_event_ids` | Newly created intent records include `linked_event_ids: [<decision_event_id>]`. |
| `TradingIntent.learning_eligible` | Newly created intent records carry `learning_eligible: true` unless explicitly set to `false`. |
| `readiness_state` derivation | `TradingRoomStrategy.readiness_state` matches the `trading_room` gate state from the latest `StrategyReadinessAssessment`. If the assessment is expired (`valid_until < now`), `readiness_state` is `"stale"`. |
| `staleness_reasons` propagated | `TradingRoomStrategy.staleness_reasons` includes reasons from the `StrategyReadinessAssessment.staleness_reasons` array when the assessment is stale. |
| `TradingIntent.intent_type` valid | Created `TradingIntent` has `intent_type` from the 8-value enum. |
| `TradingIntent.direction` valid | Created `TradingIntent` has `direction` from the 5-value enum. |
| `TradingIntent` validates against schema | Created `TradingIntent` records validate against `services/control-plane/specs/agora/trading_intent.schema.json` via `jsonschema.validate`. |

## Updated TypeScript types summary

The following types from Packet 2 must be replaced with their corrected versions:

| Type | Correction source | Key correction |
|---|---|---|
| `CommandResponse` | `agora_v1_3.openapi.yaml` `#/components/schemas/CommandResponse` | Replace `{command_id, status: "accepted"/"created", resource_id?}` with `{status: "accepted"/"queued"/"completed", data, meta}`. |
| `TradingIntent` | `services/control-plane/specs/agora/trading_intent.schema.json` | Add full schema-derived type (new in this packet). |
| `DecisionEventList` → `ListEnvelope<TradingDecisionEvent>` | `agora_v1_3.openapi.yaml` | Decision-events list route returns `ListEnvelope`, not a raw array. |
| `TradingRoomStrategyDetail` → `DetailEnvelope<TradingRoomStrategy>` | `agora_v1_3.openapi.yaml` | Strategy detail route returns `DetailEnvelope`. |
| `TradingIntentDetail` → `DetailEnvelope<TradingIntent>` | `agora_v1_3.openapi.yaml` | Intent detail route returns `DetailEnvelope`. |

**Frontend `tradingRoom.ts` method return type corrections:**

```ts
// Corrected method signatures:
listDecisionEvents(filter?: DecisionEventFilter): Promise<ListEnvelope<TradingDecisionEvent>>
getTradingRoomStrategy(strategyId: string): Promise<DetailEnvelope<TradingRoomStrategy>>
getTradingIntent(intentId: string): Promise<DetailEnvelope<TradingIntent>>
recordDecision(decisionEventId: string, body: TraderDecisionBody, opts: WriteOptions): Promise<CommandResponse>
submitHandoff(intentId: string, body: GovernedIntentHandoff, opts: WriteOptions): Promise<CommandResponse>
withdrawHandoff(intentId: string, opts: WriteOptions): Promise<CommandResponse>
```

After receiving `CommandResponse` for a decision:

```ts
// Get intent ID from the envelope:
const intentId = (response.data as DecisionCommandData).intent_id;
```

## Reviewer handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files were changed. |
| `CommandResponse` correction accurate | `agora_v1_3.openapi.yaml` `#/components/schemas/CommandResponse` confirms `required: [status, data, meta]`, `status` enum `[accepted, queued, completed]`; no `command_id` or `resource_id` present. |
| `DetailEnvelope` and `ListEnvelope` confirmed | Both schemas present in `agora_v1_3.openapi.yaml` components; routes `GET .../strategies/{id}` and `GET .../trading-intents/{id}` confirmed to use `$ref: DetailEnvelope`; `GET .../decision-events` confirmed to use `$ref: ListEnvelope`. |
| `TradingIntent` schema location | `services/control-plane/specs/agora/trading_intent.schema.json` exists; NOT in `v4/` directory; NOT referenced by `bundle_index.v1_3.json`. |
| `TradingIntent.no_order_route_proof` value | Schema confirms `enum: ["agora_intent_record_only"]` — distinct from `GovernedIntentHandoff`'s `"agora_request_only_no_order_route"`. |
| `TradingIntent.intent_type` enum | Schema confirms 8 values: `buy_interest, sell_interest, hold_decision, reduce_exposure, increase_exposure, hedge_intent, exit_intent, entry_interest`. |
| `TradingIntent.direction` enum | Schema confirms 5 values: `long, short, neutral, reduce, exit`. |
| `StrategyReadinessAssessment` gate structure | `strategy_readiness.schema.json` confirms `gates` array with `minItems: 3, maxItems: 3`; `gate` field enum `[preliminary_research, full_validation, trading_room]`; gate `state` enum `[not_assessed, blocked, conditional, ready, stale]`. |
| `highest_ready_gate` enum | Schema confirms `["preliminary_research", "full_validation", "trading_room", null]`. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Followup-5 BFF/frontend handoff packet approved: corrects CommandResponse shape (Packet 2 had wrong fields; v1.3 schema uses {status: accepted/queued/completed, data, meta} not {command_id, status: accepted/created, resource_id}); documents DetailEnvelope/ListEnvelope wrappers for strategies/{id}, decision-events list, and trading-intents/{id} routes; reveals TradingIntent schema (not in v4 bundle, no_order_route_proof='agora_intent_record_only' distinct from GovernedIntentHandoff's 'agora_request_only_no_order_route', 8-value intent_type enum, 5-value direction enum, linked_event_ids, learning_eligible); documents StrategyReadinessAssessment gate structure (3 named gates, state enum, highest_ready_gate) and how it drives readiness_state in TradingRoomStrategy; adds intent_type/direction field mapping recommendations and allowedActions guidance for DetailEnvelope — all as support material without modifying canonical truth." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only AG-BE-TR-001 BFF/frontend handoff followup-5 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

## Validation run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff_followup_5.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
# in_progress; owner Claude; reviewer Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# blocked; owner Codex; reviewer Claude2

# CommandResponse correction confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
cr = doc['components']['schemas']['CommandResponse']
print(cr)
"
# {'type': 'object', 'required': ['status', 'data', 'meta'], 'properties': {'status': ...
# enum: ['accepted', 'queued', 'completed'] — NO command_id, NO resource_id

# DetailEnvelope and ListEnvelope confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
de = doc['components']['schemas']['DetailEnvelope']
le = doc['components']['schemas']['ListEnvelope']
print('DetailEnvelope required:', de['required'])
print('ListEnvelope required:', le['required'])
"
# DetailEnvelope required: ['object_ref', 'status', 'allowedActions', 'meta', 'links', 'data']
# ListEnvelope required: ['items', 'page_info', 'meta']

# Routes confirmed to use DetailEnvelope and ListEnvelope:
grep -n "DetailEnvelope\|ListEnvelope" services/control-plane/openapi/agora_v1_3.openapi.yaml
# line ~557: getAgoraTradingRoomStrategy -> DetailEnvelope
# line ~588: listAgoraTradingDecisionEvents -> ListEnvelope
# line ~662: getAgoraTradingIntent -> DetailEnvelope

# TradingIntent schema confirmed NOT in v4 bundle:
python3 -c "
import json
with open('services/control-plane/specs/agora/bundle_index.v1_3.json') as f:
    b = json.load(f)
print('trading_intent in bundle files:', any('trading_intent' in k for k in b['files']))
"
# False

# TradingIntent schema at root agora dir:
python3 -c "
import json
with open('services/control-plane/specs/agora/trading_intent.schema.json') as f:
    s = json.load(f)
print('required:', s['required'])
print('no_order_route_proof:', s['properties']['no_order_route_proof']['enum'])
print('intent_type enum:', s['properties']['intent_type']['enum'])
print('direction enum:', s['properties']['direction']['enum'])
print('additionalProperties:', s.get('additionalProperties'))
"
# required: ['spec_version', 'intent_id', 'operator_id', 'intent_type', 'direction', 'subject', 'expressed_at', 'no_order_route_proof']
# no_order_route_proof: ['agora_intent_record_only']
# intent_type enum: ['buy_interest', 'sell_interest', 'hold_decision', 'reduce_exposure', 'increase_exposure', 'hedge_intent', 'exit_intent', 'entry_interest']
# direction enum: ['long', 'short', 'neutral', 'reduce', 'exit']
# additionalProperties: False

# StrategyReadinessAssessment gate structure confirmed:
python3 -c "
import json
with open('services/control-plane/specs/agora/v4/strategy_readiness.schema.json') as f:
    s = json.load(f)
g = s['definitions']['gate']
print('gate required:', g['required'])
print('gate name enum:', g['properties']['gate']['enum'])
print('gate state enum:', g['properties']['state']['enum'])
print('highest_ready_gate enum:', s['properties']['highest_ready_gate']['enum'])
print('gates minItems:', s['properties']['gates']['minItems'])
print('gates maxItems:', s['properties']['gates']['maxItems'])
"
# gate required: ['gate', 'state', 'requirements']
# gate name enum: ['preliminary_research', 'full_validation', 'trading_room']
# gate state enum: ['not_assessed', 'blocked', 'conditional', 'ready', 'stale']
# highest_ready_gate enum: ['preliminary_research', 'full_validation', 'trading_room', None]
# gates minItems: 3
# gates maxItems: 3

# Schema validation:
python3 -m json.tool services/control-plane/specs/agora/trading_intent.schema.json > /dev/null
# Valid JSON schema.
python3 -m json.tool services/control-plane/specs/agora/v4/strategy_readiness.schema.json > /dev/null
# Valid JSON schema.
```
