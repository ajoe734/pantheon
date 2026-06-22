# AG-BE-TR-001 BFF and Frontend Handoff Packet — Followup 6

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` — Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Supersedes / builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` |

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
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` (done) | `CommandResponse` shape correction vs Packet 2, `DetailEnvelope` and `ListEnvelope` wrapper clarifications, `TradingIntent` schema revealed (not in v4 bundle, distinct `no_order_route_proof`), `StrategyReadinessAssessment` gate structure and `readiness_state` derivation, `allowedActions` guidance for `DetailEnvelope`, and updated TypeScript types for all corrected surfaces. |
| **This packet (FOLLOWUP-6)** | HTTP status codes for all mutation endpoints (201/202/200), required write-operation headers (`If-Match`, `Idempotency-Key`, `X-Request-Id`), `ErrorEnvelope` schema, `listAgoraTradingDecisionEvents` filter parameters (`event_kind`, `state`), `POST /withdraw` has no request body, `GovernedIntentHandoff` frontend construction burden (12 required fields; frontend must generate `handoff_id`), `decision`→`decision_state` transition mapping, open question Q8 on state validation at handoff submission. |

## Current state observed

| Surface | Observed 2026-06-21 | Change since Packet 5 |
|---|---|---|
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. D8 promotion leg still gated. |
| `AG-FE-TR-001` | `todo`. | Unchanged. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. |

## New findings

### Finding 1 — HTTP status codes for mutation endpoints

Prior packets described mutation endpoints as returning `CommandResponse` but did not specify the HTTP response status code. The v1.3 OpenAPI defines three distinct codes:

| Endpoint | HTTP Status | Reason phrase |
|---|---|---|
| `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions` | **201 Created** | "Decision recorded; may create TradingIntent (request-only)" |
| `POST /bff/agora/trading-intents/{intent_id}/handoffs` | **202 Accepted** | "Request-only handoff submitted" |
| `POST /bff/agora/trading-intents/{intent_id}/withdraw` | **200 OK** | "Intent/handoff withdrawal recorded" |

**Frontend implication**: the TypeScript client must expect the correct HTTP status code and not assume all successful mutations return `200`. `response.ok` (status 200–299) is sufficient for branch checking, but explicit status handling improves error clarity:

```ts
// Correct status expectations (using fetch):
const decisionResp = await fetch(`/bff/agora/trading-room/decision-events/${id}/decisions`, { method: "POST", ... });
// decisionResp.status === 201 on success

const handoffResp = await fetch(`/bff/agora/trading-intents/${id}/handoffs`, { method: "POST", ... });
// handoffResp.status === 202 on success

const withdrawResp = await fetch(`/bff/agora/trading-intents/${id}/withdraw`, { method: "POST", ... });
// withdrawResp.status === 200 on success
```

**BFF implication**: FastAPI defaults to `status_code=200`. The BFF must explicitly set `status_code=201` on the decisions route and `status_code=202` on the handoffs route:

```python
@router.post(
    "/trading-room/decision-events/{decision_event_id}/decisions",
    status_code=201,
)
async def record_decision(...): ...

@router.post(
    "/trading-intents/{intent_id}/handoffs",
    status_code=202,
)
async def submit_handoff(...): ...

@router.post(
    "/trading-intents/{intent_id}/withdraw",
    status_code=200,   # FastAPI default; explicit for clarity
)
async def withdraw_intent(...): ...
```

### Finding 2 — Required write-operation headers (`If-Match`, `Idempotency-Key`, `X-Request-Id`)

All three mutation endpoints require three HTTP request headers. These are defined as `required: true` in `#/components/parameters` of the v1.3 OpenAPI spec and are referenced on every mutation operation's `parameters` list.

| Header | OpenAPI parameter name | Purpose |
|---|---|---|
| `If-Match` | `IfMatch` | Optimistic concurrency; value must match the current `ETag` of the resource being mutated. |
| `Idempotency-Key` | `IdempotencyKey` | Enables safe client retry; same key + same request body → same response without re-executing side effects. |
| `X-Request-Id` | `XRequestId` | Request trace correlation; echoed in logs and error responses for debugging. |

**Frontend construction** — the `WriteOptions` type referenced in Packet 4's method signatures must carry these headers:

```ts
export interface WriteOptions {
  ifMatch: string;       // ETag value from the resource's prior GET response
  idempotencyKey: string; // client-generated UUID per write attempt
  requestId?: string;    // trace ID; auto-generated if omitted
}
```

**BFF extraction** — in FastAPI, use `Header` dependency injection:

```python
from fastapi import Header

@router.post("/trading-room/decision-events/{decision_event_id}/decisions", status_code=201)
async def record_decision(
    decision_event_id: str,
    body: TraderDecisionBody,
    if_match: str = Header(..., alias="If-Match"),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    x_request_id: str = Header(..., alias="X-Request-Id"),
    identity=Depends(extract_identity),
    require_read_role=Depends(require_read_role),
): ...
```

**`If-Match` / ETag gap**: the v1.3 OpenAPI spec does not define `ETag` response headers on GET endpoints. This creates an ambiguity: the frontend must send `If-Match` on mutations, but the spec does not say how the frontend obtains the ETag value. Recommended interim pattern (for parent owner decision):

1. The BFF adds an `ETag` response header on `GET /bff/agora/trading-room/decision-events/{id}` and `GET /bff/agora/trading-intents/{intent_id}`. The ETag value can be derived from `decision_event.state` + `triggered_at` (decision events) or `intent.state` + `expressed_at` (intents), hashed with SHA-256 and hex-encoded.
2. The frontend stores the ETag from the GET response and sends it as `If-Match` on the subsequent POST.
3. The BFF validates `If-Match` against the current resource ETag; if mismatched, returns `412 Precondition Failed` with an `ErrorEnvelope`.

**Open question Q8** (see § Open questions below).

**Idempotency-Key** — the BFF should cache the `(idempotency_key, operator_id)` pair in a short-lived store (e.g., Redis TTL 10 min) and return the cached `CommandResponse` on replay. This was described abstractly in Packet 3 but is now confirmed as a mandatory header, not an optional feature.

### Finding 3 — `ErrorEnvelope` schema (not in prior packets)

All BFF error responses should return the `ErrorEnvelope` schema defined in `#/components/schemas/ErrorEnvelope`:

```yaml
ErrorEnvelope:
  type: object
  required: [error]
  properties:
    error:
      type: object
      required: [code, message]
      properties:
        code:
          type: string         # machine-readable error code
        message:
          type: string         # human-readable message
        details:
          type: object
          additionalProperties: true   # optional context
```

**TypeScript type:**

```ts
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
```

**Recommended BFF error codes for Trading Room routes:**

| HTTP Status | `error.code` | Trigger |
|---|---|---|
| `404` | `TRADING_DECISION_EVENT_NOT_FOUND` | `GET /decision-events/{id}` or `POST /decisions` when event ID not found |
| `404` | `TRADING_INTENT_NOT_FOUND` | `GET /trading-intents/{id}` or `POST /handoffs` / `POST /withdraw` when intent ID not found |
| `404` | `TRADING_ROOM_STRATEGY_NOT_FOUND` | `GET /strategies/{id}` when strategy not in aggregate |
| `409` | `DECISION_ALREADY_RECORDED` | `POST /decisions` when event `decision_state` is not `"pending"` |
| `409` | `INTENT_NOT_WITHDRAWABLE` | `POST /withdraw` when intent state is `"withdrawn"` or `"completed"` |
| `409` | `HANDOFF_ALREADY_SUBMITTED` | `POST /handoffs` when intent already has an active handoff in `"submitted"` or `"accepted"` state |
| `412` | `ETAG_MISMATCH` | `If-Match` header does not match current resource ETag |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` | Same `Idempotency-Key` used with a different request body |
| `422` | `HANDOFF_VALIDATION_FAILED` | `POST /handoffs` body fails `GovernedIntentHandoff` schema validation |
| `403` | `FORBIDDEN` | Operator lacks permission for the requested action |

The `bff_error()` helper from Packet 4's construction guidance should return `ErrorEnvelope`:

```python
def bff_error(code: str, message: str, status_code: int = 400, details=None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, **({"details": details} if details else {})}},
    )
```

### Finding 4 — `listAgoraTradingDecisionEvents` filter parameters

The `GET /bff/agora/trading-room/decision-events` endpoint accepts two optional query parameters not documented in prior packets:

| Parameter | In | Type | Constraint | Notes |
|---|---|---|---|---|
| `event_kind` | query | string | enum `["entry", "add", "reduce", "exit", "review"]` | Filter to one event kind. |
| `state` | query | string | open (no enum in OpenAPI) | Filter by `TradingDecisionEvent.state`; expected set is the 7-value enum on `TradingDecisionEvent.state`. |

**Frontend `DecisionEventFilter` type** (referenced in Packet 5 method signature `listDecisionEvents(filter?: DecisionEventFilter)`):

```ts
export interface DecisionEventFilter {
  event_kind?: "entry" | "add" | "reduce" | "exit" | "review";
  state?: string;  // open string; TradingDecisionEvent.state enum is the natural set:
                   // "approaching" | "triggered" | "pending_review" | "decided" |
                   // "expired" | "invalidated" | "superseded"
}
```

**BFF query parameter extraction** — in FastAPI:

```python
from fastapi import Query
from typing import Optional

@router.get("/trading-room/decision-events")
async def list_decision_events(
    event_kind: Optional[str] = Query(None, enum=["entry", "add", "reduce", "exit", "review"]),
    state: Optional[str] = Query(None),
    identity=Depends(extract_identity),
    require_read_role=Depends(require_read_role),
) -> ListEnvelope:
    events = get_read_store().list_decision_events(event_kind=event_kind, state=state)
    ...
```

**Acceptance check**: `GET /bff/agora/trading-room/decision-events?event_kind=entry` returns only items where `item.event_kind == "entry"`. `GET /bff/agora/trading-room/decision-events?event_kind=invalid_kind` returns HTTP 422.

### Finding 5 — `POST /withdraw` has no request body

The `POST /bff/agora/trading-intents/{intent_id}/withdraw` endpoint has **no `requestBody`** in the v1.3 OpenAPI spec. The only inputs are:
- Path parameter: `intent_id`
- Required headers: `If-Match`, `Idempotency-Key`, `X-Request-Id`

Prior packets referenced `withdrawHandoff(intentId, opts)` where `opts` was described as `WriteOptions`. The `opts` are the three headers, not a body object. The `writtenBy` actor is derived from the identity extracted from the session, not from the request body.

**Corrected TypeScript signature:**

```ts
// opts carries the required write headers only — no body needed
withdrawIntent(intentId: string, opts: WriteOptions): Promise<CommandResponse>
```

**BFF handler signature (no `body` parameter):**

```python
@router.post("/trading-intents/{intent_id}/withdraw", status_code=200)
async def withdraw_intent(
    intent_id: str,
    if_match: str = Header(..., alias="If-Match"),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    x_request_id: str = Header(..., alias="X-Request-Id"),
    identity=Depends(extract_identity),
): ...
```

### Finding 6 — `GovernedIntentHandoff` frontend construction burden

The `POST /bff/agora/trading-intents/{intent_id}/handoffs` endpoint requires the **full `GovernedIntentHandoff` object** as the request body — not a reduced "create request" DTO. The schema requires 12 fields:

```
required: [
  spec_version, handoff_id, intent_id, requested_stage, handoff_type,
  state, strategy_id, strategy_spec_registry_id, requested_by,
  evidence_refs, no_order_route_proof, created_at
]
```

This means the frontend must construct the full object. The BFF does not generate the `handoff_id` or supply required fields from server-side context. Key frontend responsibilities:

| Field | Who sets it | Value |
|---|---|---|
| `spec_version` | Frontend | `"1.0"` (const) |
| `handoff_id` | Frontend | Generate UUID (e.g., `crypto.randomUUID()`) |
| `intent_id` | Frontend | Must match the URL path `intent_id` |
| `requested_stage` | Frontend | One of `"shadow" | "paper" | "canary" | "live"` |
| `handoff_type` | Frontend | One of `"shadow_start" | "paper_validation_request" | "promotion_review_request"` |
| `state` | Frontend | Must be `"submitted"` on creation |
| `strategy_id` | Frontend | From the linked strategy context |
| `strategy_spec_registry_id` | Frontend | From the linked strategy context |
| `requested_by` | Frontend | `{actor_type: "trader", actor_ref: identity.operator_id}` |
| `evidence_refs` | Frontend | Array of evidence refs (may be `[]` — schema has no `minItems`) |
| `no_order_route_proof` | Frontend | Must be `"agora_request_only_no_order_route"` (const) |
| `created_at` | Frontend | Current timestamp ISO-8601 |

**BFF validation responsibility**: the BFF should validate the incoming body against `governed_intent_handoff.schema.json` using `jsonschema.validate` before storing. It should also assert that `body.intent_id == path intent_id` to prevent mismatched object references.

**`requestedStage` ↔ `handoffType` ↔ `targetQueue` consistency** (recommendation, not canon):

| `requested_stage` | `handoff_type` | `target_queue` |
|---|---|---|
| `"shadow"` | `"shadow_start"` | `"shadow_research"` |
| `"paper"` | `"paper_validation_request"` | `"management_governance"` |
| `"canary"` or `"live"` | `"promotion_review_request"` | `"promotion_review"` |

The BFF may enforce this consistency rule and reject mismatched combinations with `422 HANDOFF_VALIDATION_FAILED`. Parent owner decision required.

**Open question Q8** (see § Open questions below).

### Finding 7 — `decision` → `decision_state` transition mapping

The `POST /decisions` body `decision` field and the resulting `TradingDecisionEvent.decision_state` transition were not explicitly mapped in prior packets. Based on schema semantics:

| POST body `decision` | Resulting `decision_state` | Creates `TradingIntent`? | Notes |
|---|---|---|---|
| `"approve"` | `"approved_by_trader"` | Yes | Creates `TradingIntent` with `intent_type` and `direction` derived as per Packet 5. |
| `"modify"` | `"approved_by_trader"` | Yes | Creates `TradingIntent` with `size_hint` from `body.modifications.size_hint` if present. |
| `"reject"` | `"rejected_by_trader"` | No | No `TradingIntent` created. |
| `"defer"` | `"deferred"` | No | No `TradingIntent` created. |

States `"handed_off"`, `"expired"`, and `"superseded"` are not reachable via `POST /decisions`:
- `"handed_off"`: set by the BFF when a `GovernedIntentHandoff` is accepted by the governance queue.
- `"expired"`: set by a time-based job when `expires_at < utc_now()` and `decision_state` is still `"pending"`.
- `"superseded"`: set by the strategy system when a newer event supersedes this one.

**BFF state transition guard**: the BFF must reject `POST /decisions` if the event's current `decision_state` is not `"pending_review"` (the only state where a trader decision is valid). Return `409 DECISION_ALREADY_RECORDED` otherwise.

**`TradingDecisionEvent.state` vs `decision_state` distinction**:
- `state`: the overall event lifecycle — `"approaching" | "triggered" | "pending_review" | "decided" | "expired" | "invalidated" | "superseded"`. This field tracks whether the event is still valid for trader action.
- `decision_state`: the trader's decision disposition — `"pending" | "approved_by_trader" | ...`. Only meaningful when `state == "pending_review"` or `"decided"`.

The BFF should guard on `state == "pending_review"` (not just `decision_state == "pending"`) before accepting a decision.

## Updated TypeScript types

The following types supplement or refine those in prior packets:

```ts
// Corrected WriteOptions (carries required mutation headers)
export interface WriteOptions {
  ifMatch: string;
  idempotencyKey: string;
  requestId?: string;
}

// DecisionEventFilter (query params for list decision events)
export interface DecisionEventFilter {
  event_kind?: "entry" | "add" | "reduce" | "exit" | "review";
  state?: string;
}

// ErrorEnvelope (all BFF error responses)
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

// GovernedIntentHandoff — frontend must construct this fully (excerpt of key fields)
export interface GovernedIntentHandoffCreate {
  spec_version: "1.0";
  handoff_id: string;               // frontend-generated UUID
  intent_id: string;                // must match URL path intent_id
  requested_stage: "shadow" | "paper" | "canary" | "live";
  handoff_type: "shadow_start" | "paper_validation_request" | "promotion_review_request";
  state: "submitted";               // always "submitted" on creation
  strategy_id: string;
  strategy_spec_registry_id: string;
  requested_by: {
    actor_type: "trader" | "agora_servant" | "institutional_persona" | "system";
    actor_ref: string;              // identity.operator_id for trader actor
    session_id?: string;
    display_name?: string;
  };
  evidence_refs: Array<{
    ref_type: "evidence_bundle" | "evidence_item" | "source_record" | "citation" |
              "experiment_artifact" | "registry_entry" | "consult_memo" |
              "research_run" | "telemetry_snapshot" | "market_context";
    ref_id: string;
    summary?: string;
    data_cutoff?: string;
  }>;
  no_order_route_proof: "agora_request_only_no_order_route";
  created_at: string;               // ISO-8601 UTC timestamp
  // Optional fields:
  decision_event_id?: string;
  target_queue?: "shadow_research" | "management_governance" | "promotion_review";
  required_gate_refs?: string[];
  action_proposal?: {
    action?: "enter" | "add" | "reduce" | "exit" | "review";
    symbol?: string;
    direction?: string;
    size_hint?: string;
    portfolio_pct?: number;
    non_binding?: true;
  };
  rationale?: string;
  risk_summary?: string;
  management_handoff_ref?: string;
  deployment_plan_ref?: string;
  runtime_binding_ref?: string;
  expires_at?: string;
  updated_at?: string;
}
```

**Corrected `tradingRoom.ts` method signatures** (building on Packet 5 with header and status corrections):

```ts
// All write methods now explicitly show WriteOptions carries the 3 required headers
listDecisionEvents(filter?: DecisionEventFilter): Promise<ListEnvelope<TradingDecisionEvent>>
getTradingRoomStrategy(strategyId: string): Promise<DetailEnvelope<TradingRoomStrategy>>
getTradingDecisionEvent(decisionEventId: string): Promise<TradingDecisionEvent>
getTradingIntent(intentId: string): Promise<DetailEnvelope<TradingIntent>>
recordDecision(
  decisionEventId: string,
  body: TraderDecisionBody,
  opts: WriteOptions          // If-Match, Idempotency-Key, X-Request-Id
): Promise<CommandResponse>   // HTTP 201
submitHandoff(
  intentId: string,
  body: GovernedIntentHandoffCreate,
  opts: WriteOptions
): Promise<CommandResponse>   // HTTP 202
withdrawIntent(
  intentId: string,
  opts: WriteOptions            // no body; headers only
): Promise<CommandResponse>   // HTTP 200
```

## Open questions

| Q | Question | Recommended approach | Status |
|---|---|---|---|
| Q8 (new) | Who validates `GovernedIntentHandoff.state == "submitted"` on `POST /handoffs`? Should the BFF override `state` to `"submitted"` regardless of what the frontend sends, or should it reject mismatched states with `422`? | Reject with `422 HANDOFF_VALIDATION_FAILED` if `state != "submitted"`. Do not silently override client data. | **Open — parent owner decision** |
| Q9 (new) | How should the BFF derive the `ETag` value for `GET /decision-events/{id}` and `GET /trading-intents/{id}` responses, given the v1.3 spec does not define `ETag` response headers? | Derive from SHA-256 of `(resource_state + last_modified_timestamp)`, hex-encoded. Include `ETag` in GET response headers. | **Open — parent owner decision** |
| Q10 (new) | Should the BFF enforce `requestedStage`/`handoffType`/`targetQueue` consistency (the 3-column mapping table above), or accept any valid enum combination regardless of logical consistency? | Enforce the 3-column mapping. Return `422` if inconsistent. | **Open — parent owner decision** |

(Q1–Q7 resolved in Packets 3, 4, and 5.)

## Additional acceptance checks

These checks supplement the checks in Packets 1, 3, 4, and 5:

| Check | Expected result |
|---|---|
| `POST /decisions` returns HTTP 201 | Response status code is `201`, not `200`. Body is `CommandResponse`. |
| `POST /handoffs` returns HTTP 202 | Response status code is `202`, not `200`. Body is `CommandResponse`. |
| `POST /withdraw` returns HTTP 200 | Response status code is `200`. Body is `CommandResponse`. |
| `POST /decisions` requires `If-Match` header | Missing `If-Match` returns `422`. |
| `POST /decisions` requires `Idempotency-Key` header | Missing `Idempotency-Key` returns `422`. |
| `POST /decisions` requires `X-Request-Id` header | Missing `X-Request-Id` returns `422`. |
| Same applies to `POST /handoffs` and `POST /withdraw` | Both require all three headers. |
| `POST /withdraw` accepts no request body | `POST /withdraw` with an empty body succeeds; no body parsing errors. |
| `POST /withdraw` rejects already-withdrawn intent | Returns `409` with `error.code == "INTENT_NOT_WITHDRAWABLE"`. |
| `POST /decisions` rejects non-pending event | `decision_state != "pending"` OR `state != "pending_review"` → `409 DECISION_ALREADY_RECORDED`. |
| Error responses use `ErrorEnvelope` shape | All `4xx`/`5xx` responses return `{"error": {"code": "...", "message": "..."}}`. |
| `GET /decision-events?event_kind=entry` filters correctly | All returned items have `event_kind == "entry"`. |
| `GET /decision-events?event_kind=invalid` returns `422` | Invalid enum value rejected. |
| `GET /decision-events?state=pending_review` filters correctly | All returned items have `state == "pending_review"`. |
| `POST /handoffs` body includes all 12 required fields | BFF validates body against `governed_intent_handoff.schema.json`; missing required field returns `422 HANDOFF_VALIDATION_FAILED`. |
| `POST /handoffs` rejects body where `intent_id != path intent_id` | Returns `422` with mismatch error. |
| `POST /handoffs` body `state != "submitted"` rejected | Returns `422 HANDOFF_VALIDATION_FAILED` (pending Q8 parent owner decision). |
| `GovernedIntentHandoff.no_order_route_proof` constant | Accepted handoff has `no_order_route_proof == "agora_request_only_no_order_route"`. |
| `TradingDecisionEvent.state` guard on `POST /decisions` | BFF checks `state == "pending_review"`, not just `decision_state == "pending"`. |
| `decision: "approve"` → `decision_state: "approved_by_trader"` | After approve decision, event `decision_state` is `"approved_by_trader"`. |
| `decision: "reject"` → `decision_state: "rejected_by_trader"` | After reject decision, event `decision_state` is `"rejected_by_trader"`. |
| `decision: "defer"` → `decision_state: "deferred"` | After defer decision, event `decision_state` is `"deferred"`. |
| No `TradingIntent` created for `reject` or `defer` | `POST /decisions` with `decision: "reject"` or `"defer"` does not create a `TradingIntent` record. |

## Reviewer handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files were changed. |
| HTTP 201 for POST /decisions | `agora_v1_3.openapi.yaml` path `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions` → `responses: {201: ...}` confirmed. |
| HTTP 202 for POST /handoffs | `agora_v1_3.openapi.yaml` path `POST /bff/agora/trading-intents/{intent_id}/handoffs` → `responses: {202: ...}` confirmed. |
| HTTP 200 for POST /withdraw | `agora_v1_3.openapi.yaml` path `POST /bff/agora/trading-intents/{intent_id}/withdraw` → `responses: {200: ...}` confirmed. |
| `If-Match`, `Idempotency-Key`, `X-Request-Id` are required on all mutations | `components/parameters` entries `IfMatch`, `IdempotencyKey`, `XRequestId` each have `required: true`; all appear on decisions, handoffs, and withdraw `parameters` lists. |
| `POST /withdraw` has no requestBody | No `requestBody` key present on the withdraw operation in the OpenAPI spec. |
| `ErrorEnvelope` schema in components | `components/schemas/ErrorEnvelope` exists with `required: [error]`; `error` has `required: [code, message]`. |
| `listAgoraTradingDecisionEvents` query params | `event_kind` (enum, optional) and `state` (string, optional) both present on the GET `/decision-events` operation. |
| `GovernedIntentHandoff` required fields | `governed_intent_handoff.schema.json` `required` array has 12 entries including `handoff_id`, `state`, `requested_by`, `evidence_refs`, `no_order_route_proof`. |
| `GovernedIntentHandoff.state` enum | Schema `state` enum is `[draft, submitted, accepted, rejected, expired, withdrawn, converted]` — "submitted" is the expected value on creation. |
| `target_queue` / `requested_stage` / `handoff_type` enums | All confirmed present as enum fields in `governed_intent_handoff.schema.json`. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md \
  REVIEW_NOTES_ZH="Followup-6 BFF/frontend handoff packet approved: documents HTTP status codes for mutations (POST /decisions=201, POST /handoffs=202, POST /withdraw=200, all previously undocumented); reveals 3 required write-op headers (If-Match, Idempotency-Key, X-Request-Id, all required:true in OpenAPI spec); documents ErrorEnvelope schema ({error:{code,message,details?}}); adds listAgoraTradingDecisionEvents filter params (event_kind enum, state string); corrects POST /withdraw to have no request body (headers only); documents GovernedIntentHandoff 12 required fields and frontend construction burden (handoff_id must be frontend-generated UUID); maps decision body field to decision_state transitions (approve→approved_by_trader, reject→rejected_by_trader, defer→deferred, modify→approved_by_trader); clarifies state vs decision_state guard for POST /decisions; opens Q8/Q9/Q10 for parent owner — all as support material without modifying canonical truth." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Support-only AG-BE-TR-001 BFF/frontend handoff followup-6 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

## Validation run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff_followup_6.md
# ?? support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
# in_progress; owner Claude; reviewer Claude2

# HTTP status codes confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
paths = doc['paths']
decisions_responses = list(paths['/bff/agora/trading-room/decision-events/{decision_event_id}/decisions']['post']['responses'].keys())
handoffs_responses = list(paths['/bff/agora/trading-intents/{intent_id}/handoffs']['post']['responses'].keys())
withdraw_responses = list(paths['/bff/agora/trading-intents/{intent_id}/withdraw']['post']['responses'].keys())
print('decisions:', decisions_responses)  # ['201']
print('handoffs:', handoffs_responses)    # ['202']
print('withdraw:', withdraw_responses)    # ['200']
"
# decisions: ['201']
# handoffs: ['202']
# withdraw: ['200']

# Required headers confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
params = doc['components']['parameters']
print('IfMatch required:', params['IfMatch']['required'])    # True
print('IdempotencyKey required:', params['IdempotencyKey']['required'])  # True
print('XRequestId required:', params['XRequestId']['required'])  # True
"
# IfMatch required: True
# IdempotencyKey required: True
# XRequestId required: True

# Withdraw has no requestBody confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
op = doc['paths']['/bff/agora/trading-intents/{intent_id}/withdraw']['post']
print('has requestBody:', 'requestBody' in op)  # False
"
# has requestBody: False

# ErrorEnvelope schema confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
ee = doc['components']['schemas']['ErrorEnvelope']
print('ErrorEnvelope required:', ee['required'])
print('error required:', ee['properties']['error']['required'])
"
# ErrorEnvelope required: ['error']
# error required: ['code', 'message']

# List decision-events query params confirmed:
python3 -c "
import yaml
with open('services/control-plane/openapi/agora_v1_3.openapi.yaml') as f:
    doc = yaml.safe_load(f)
op = doc['paths']['/bff/agora/trading-room/decision-events']['get']
params = op.get('parameters', [])
for p in params:
    print(p['name'], '->', p['schema'])
"
# event_kind -> {'type': 'string', 'enum': ['entry', 'add', 'reduce', 'exit', 'review']}
# state -> {'type': 'string'}

# GovernedIntentHandoff required fields confirmed:
python3 -c "
import json
with open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json') as f:
    s = json.load(f)
print('required count:', len(s['required']))  # 12
print('required:', s['required'])
print('state enum:', s['properties']['state']['enum'])
print('target_queue enum:', s['properties']['target_queue']['enum'])
"
# required count: 12
# required: ['spec_version', 'handoff_id', 'intent_id', 'requested_stage', 'handoff_type',
#            'state', 'strategy_id', 'strategy_spec_registry_id', 'requested_by',
#            'evidence_refs', 'no_order_route_proof', 'created_at']
# state enum: ['draft', 'submitted', 'accepted', 'rejected', 'expired', 'withdrawn', 'converted']
# target_queue enum: ['shadow_research', 'management_governance', 'promotion_review']
```
