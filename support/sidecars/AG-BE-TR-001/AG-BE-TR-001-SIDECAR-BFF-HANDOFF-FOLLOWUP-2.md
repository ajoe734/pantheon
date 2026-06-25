# AG-BE-TR-001 BFF and Frontend Handoff Packet — Followup 2

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` — Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Supersedes / builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF.md` (done, PR #2128 merged) |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It supplements the first handoff packet with
implementation-sequence guidance, backend module structure, position-event detail,
Trading Room SSE contract specifics, BFF degraded-response patterns, and frontend
integration notes. The parent owner decides whether and how to absorb this material.

## Summary of the first handoff packet

The first sidecar (`AG-BE-TR-001-SIDECAR-BFF-HANDOFF`, done 2026-06-21T18:42:08Z) covered:

- **BFF query gap matrix**: all 9 routes absent from `trading_room/router.py` with their v4 schema bindings.
- **Operator journeys A–H**: view aggregate, browse/filter event queue, detail review, record trader decision, submit governed handoff, SSE stream subscription, candidate-to-decision-event promotion (D8), and capability-not-ready error path.
- **Frontend handoff**: `tradingRoom.ts` client method signatures, EV display rules, confidence vs probability semantics, and non-binding size labelling.
- **Suggested backend acceptance checks**: 30+ acceptance assertions covering schema conformance, state machine, idempotency, concurrency, and no-order guard.
- **Open design notes**: AG-BE-CP-001 gate, v1.3 bundle status, router placeholder, D4 semantics, missing `tradingRoom.ts`, and frontend dependency chain.

This followup does not repeat that material. It adds supplementary depth where the first packet left gaps.

## Current state observed

| Surface | Observed 2026-06-21 | Change since first sidecar |
|---|---|---|
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. |
| `AG-XR-OPENAPI-004` | `done` (archive 2026-06-21T13:30:08Z). | Gate lifted; no change. |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. D8 promotion leg still gated. |
| `AG-FE-TR-001` | `todo`. | Unchanged. Frontend still gated on AG-BE-TR-001. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. |

## Implementation sequence (phased by dependency)

Given that AG-BE-CP-001 is blocked, AG-BE-TR-001 should proceed in two phases to avoid being fully gated on a blocked dependency.

### Phase 1 — Independent routes (no AG-BE-CP-001 dependency)

These routes and behaviors can be implemented and tested before AG-BE-CP-001 lands:

| Route | Dependency |
|---|---|
| `GET /bff/agora/trading-room` | Strategy registry and read model only. Does not require AG-BE-CP-001 candidate refs if the `candidate_count` field is populated from a separate projection or returned as `0` with a `degraded` flag when AG-BE-CP-001 is unavailable. |
| `GET /bff/agora/trading-room/strategies/{strategy_id}` | Strategy registry, shadow/paper state. No candidate pool required. |
| `GET /bff/agora/trading-room/decision-events` + `/{decision_event_id}` | Decision events not originating from candidates (e.g. add/reduce/exit/review events for active paper/shadow positions) can exist without AG-BE-CP-001. |
| `POST .../decisions` (`reject` / `defer`) | Recording these decisions on non-candidate events has no AG-BE-CP-001 dependency. |
| `POST .../decisions` (`approve` / `modify`) | Creates a `TradingIntent`; no AG-BE-CP-001 dependency. |
| `GET /bff/agora/trading-room/stream` | SSE stream for all decision-event/queue/risk state changes; no AG-BE-CP-001 dependency. |
| `GET /bff/agora/trading-intents/{intent_id}` | Intent read; no AG-BE-CP-001 dependency. |
| `POST .../handoffs` | Governed handoff submission; no AG-BE-CP-001 dependency. |
| `POST .../withdraw` | Handoff withdrawal; no AG-BE-CP-001 dependency. |

**Recommended approach for `candidate_count`**: when AG-BE-CP-001 routes are absent or unavailable, return `"candidate_count": 0` with a `"degradation_notes"` entry (e.g. `"candidate_count_unavailable"`) in the aggregate. Do not return an error for the whole aggregate. The frontend should show "Candidates: —" when the field is 0 with a degradation note, rather than treating it as zero monitored candidates.

### Phase 2 — Candidate-gated route (requires AG-BE-CP-001)

| Route / behavior | Gate |
|---|---|
| D8 candidate-to-decision-event promotion: creating a `TradingDecisionEvent` whose `origin` is `candidate` and whose `candidate_ref` points to a `CandidatePool` member | Requires AG-BE-CP-001's `candidate_pool.schema.json` routes to be live so the Trading Room projection can read `lifecycle_state: "add_to_monitoring"` and the candidate decision reference. |

When implementing Phase 1, leave a TODO comment at the projection entry point (not in the route handler) marking where the candidate-promotion leg will be wired in during Phase 2.

## Backend module structure guidance

The empty `create_trading_room_router` function in `services/control-plane/bff/agora/trading_room/router.py` should be filled following this structure. This is supplementary guidance only — the canonical implementation authority is the parent owner (Claude2).

### Router factory signature (extend the existing pattern)

```python
def create_trading_room_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    trading_room_projection: TradingRoomProjection,       # Phase 1: aggregate + event queue read
    trading_intent_store: TradingIntentStore,             # create intent on approve/modify
    trading_room_stream_factory: TradingRoomStreamFactory, # SSE stream
    # Phase 2 (add when AG-BE-CP-001 lands):
    # candidate_projection: CandidateProjection,
) -> APIRouter:
```

### Route handler skeleton (illustrative, not canonical)

```python
router = APIRouter(tags=["agora-trading"])

@router.get("/bff/agora/trading-room")
async def get_trading_room(identity=Depends(extract_identity)):
    require_read_role(identity)
    aggregate = await trading_room_projection.get_aggregate(identity.user_scope_ref)
    if aggregate is None:
        raise bff_error("TRADING_ROOM_NOT_READY", status_code=503)
    return aggregate   # validated against trading_room_aggregate.schema.json v1.0

@router.get("/bff/agora/trading-room/decision-events")
async def list_decision_events(event_kind: str | None = None, state: str | None = None,
                                identity=Depends(extract_identity)):
    require_read_role(identity)
    return await trading_room_projection.list_events(
        user_scope_ref=identity.user_scope_ref,
        event_kind=event_kind,
        state=state,
    )

@router.post("/bff/agora/trading-room/decision-events/{decision_event_id}/decisions")
async def record_decision(decision_event_id: str, body: TraderDecisionBody,
                          if_match: str = Header(...),
                          idempotency_key: str = Header(...),
                          identity=Depends(extract_identity)):
    # validate state: must be triggered or pending_review
    # idempotency: if same key already processed, return current state
    # for approve/modify: create TradingIntent (no broker order, no RuntimeBinding)
    # return 201 CommandResponse with intent_id for approve/modify
    # return 200 for reject/defer
    ...

@router.get("/bff/agora/trading-room/stream")
async def trading_room_stream(identity=Depends(extract_identity)):
    require_read_role(identity)
    return StreamingResponse(
        trading_room_stream_factory.user_stream(identity.user_scope_ref),
        media_type="text/event-stream",
    )
```

### No-order-route invariant enforcement point

The existing router docstring states the invariant. The implementation should also assert it at the `TradingIntent` creation site:

```python
assert intent.no_order_route_proof == "agora_request_only_no_order_route", (
    "TradingIntent created by Trading Room must carry no_order_route_proof"
)
```

This is a belt-and-suspenders guard; the schema enforces it structurally, but a runtime assertion at intent creation prevents accidental future loosening.

### Schema validation

The router should validate outgoing aggregate and decision-event responses against the v4 schemas in CI at minimum, and optionally in-process in dev/test mode. The schema files are:

```
services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json
services/control-plane/specs/agora/v4/trading_decision_event.schema.json
services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json
```

Validation at the route level is optional in production (avoid latency); enforcing it in the test suite via `jsonschema.validate(response.json(), schema)` is required.

## Position event fields (D9 supplement)

The first sidecar listed position events as a gap but did not detail the required fields. For `add`/`reduce`/`exit`/`review` event kinds, the `TradingDecisionEvent` payload must also include a `position_snapshot` sub-object. Based on D9:

| Field | Required | Description |
|---|---|---|
| `position_snapshot.current_size` | Yes | Current position size (non-binding unit). |
| `position_snapshot.average_cost` | Yes | Average cost basis. |
| `position_snapshot.unrealized_pnl` | Yes | Current unrealized P&L. |
| `position_snapshot.current_risk_exposure` | Yes | Risk/exposure metric aligned with strategy risk model. |
| `position_snapshot.original_thesis_ref` | Yes | Reference to the original entry thesis or research run. |
| `position_snapshot.thesis_status` | Yes | `valid`, `weakened`, `invalidated`, or `superseded`. |
| `position_snapshot.triggered_rule` | Yes | The rule or condition that triggered this position event. |
| `position_snapshot.suggested_delta` | Yes | Non-binding suggested position change; must carry `non_binding: true`. |
| `position_snapshot.alternative_shadow_action` | No | Alternative action available from Shadow research. |

**Frontend binding note**: the `thesis_status` field is separate from `invalidation.current_state` on the event itself. A position event can have a `weakened` thesis but still be in `triggered` state; the UI should surface both independently.

## Trading Room SSE contract

The Trading Room SSE stream (`GET /bff/agora/trading-room/stream`) is not the same as the Workshop SSE stream (`GET /bff/agora/workshops/{workshop_id}/stream`). The Workshop SSE contract (document C) covers workshop dialogue and research events. The Trading Room stream is narrower.

### Trading Room stream event types

| Event type | Trigger |
|---|---|
| `trading_room.snapshot` | On connection or reconnect; delivers current aggregate state so client can initialize without a separate GET. |
| `trading_room.decision_event.state_changed` | Decision-event lifecycle transition (e.g. `approaching → triggered`, `triggered → pending_review`, `pending_review → decided`). |
| `trading_room.decision_event.invalidated` | Event transitioned to `invalidated`; includes `invalidation_reason` and `invalidated_at`. |
| `trading_room.decision_event.created` | New decision event entered the queue. |
| `trading_room.queue_counts.updated` | `pending_event_counts` changed for one or more strategies; includes updated per-kind counts. |
| `trading_room.risk_summary.changed` | `risk_summary.state` changed (e.g. `normal → watch`, `watch → warning`). |
| `trading_room.intent.state_changed` | A `TradingIntent` owned by this user scope changed state (e.g. after a governed handoff was accepted or rejected downstream). |
| `stream.heartbeat` | Keepalive; no payload. Interval: 15 seconds. |
| `stream.error` | Recoverable or terminal stream error; includes `code`, `message`, `retryable`. |

### Reconnect and replay

| Behavior | Specification |
|---|---|
| Reconnect trigger | Client sends `Last-Event-ID` header on reconnect. |
| Replay window | BFF replays undelivered events for this user scope from the last 30 minutes or the last 500 events, whichever limit is reached first. If replay is unavailable, return a `stream.error` with `code: "STREAM_REPLAY_UNAVAILABLE"` and set `retryable: true`. The client should fetch a fresh snapshot via `GET /bff/agora/trading-room` and reconnect. |
| Heartbeat | 15-second interval. |
| Degraded declaration | Client should declare the stream degraded after 45 seconds without an event or heartbeat and show a "Reconnecting" indicator. |
| Backoff | Exponential, capped at 30 seconds. |
| Deduplication | Events carry `event_id`; client drops duplicates by `event_id`. |
| Coalescing | `trading_room.queue_counts.updated` may be coalesced to at most 2 events/second; all state-change events are delivered without coalescing. |

### Contrast with Workshop SSE (doc C)

The Workshop SSE stream carries structured dialogue cards (servant response deltas, completeness updates, research progress, consult results) and is per-workshop. The Trading Room SSE stream is per-user-scope and carries decision-event lifecycle updates only. Do not conflate the two; they use different routes, different event catalogues, and have different reconnect semantics.

## BFF degraded-response patterns

Based on `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §5 (strict live behavior) and the D10 error vocabulary:

### When a downstream service is unavailable

| Downstream | Route affected | BFF response |
|---|---|---|
| Strategy registry unavailable | `GET /bff/agora/trading-room` | Return typed `503` with `code: "TRADING_ROOM_NOT_READY"`, `blocking_reasons: ["strategy_registry_unavailable"]`. Do not return partial data without a degradation note. |
| Decision-event projection unavailable | `GET /bff/agora/trading-room/decision-events` | Return typed `503` with `code: "TRADING_ROOM_NOT_READY"`, `blocking_reasons: ["event_projection_unavailable"]`. |
| Risk model unavailable | `GET /bff/agora/trading-room` | Return the aggregate with `risk_summary.state: "unavailable"` and a `degradation_notes` entry, not a `503`. The aggregate itself can be served; the risk summary is one component. |
| Intent store unavailable | `POST .../decisions` | Return typed `503` with `code: "TRADING_INTENT_HANDOFF_NOT_ALLOWED"`, `blocking_reasons: ["intent_store_unavailable"]`. Do not silently drop the decision. |
| SSE projection unavailable | `GET .../stream` | Emit a `stream.error` event with `code: "TRADING_ROOM_NOT_READY"`, `retryable: true`, then close the stream. Client reconnects with backoff. |

### Invariants

- BFF must not substitute fixture data for a missing downstream. The operator must see a typed error, not a synthetic result.
- BFF unavailability must not affect active paper/canary/live runtimes (BFF HA policy §2.2). The runtime execution plane does not depend on the Trading Room BFF.
- Return all D10 errors as structured JSON with `code`, `message`, `blocking_reasons`, and optional `source` fields.

## Frontend integration supplement

The first sidecar listed method signatures for `tradingRoom.ts`. This section adds structural guidance for how the module should integrate with the existing frontend BFF infrastructure.

### File location and pattern

Following the pattern in `execute-plans/src/lib/bff-v1/agora/dashboard.ts`:

- `tradingRoom.ts` should export typed functions (not classes).
- Use the `resolvedBase(baseUrl?)` pattern already present in `dashboard.ts` for URL construction.
- All write methods (`recordDecision`, `submitHandoff`, `withdrawHandoff`) must attach `If-Match` and `Idempotency-Key` as request headers.
- SSE subscription (`streamTradingRoom`) should return a cleanup function (`() => void`) that closes the `EventSource` on unmount.

### TypeScript types to define

These types are not in `types.ts` yet and should be added to `tradingRoom.ts` or a co-located `trading-room-types.ts`:

```ts
export interface TradingRoomAggregate {
  spec_version: "1.0";
  user_scope_ref: string;
  strategies: TradingRoomStrategy[];
  queue_summary: QueueSummary;
  risk_summary: RiskSummary;
  snapshot_at: string;
  data_cutoff: string;
  degradation_notes?: string[];
}

export interface TradingRoomStrategy {
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
  candidate_count?: number;
  position_count?: number;
  staleness_reasons?: string[];
  dashboard_recipe_id?: string;
}

export interface TradingDecisionEvent {
  decision_event_id: string;
  event_kind: "entry" | "add" | "reduce" | "exit" | "review";
  origin: string;
  strategy_id: string;
  strategy_spec_registry_id: string;
  subject: { symbol: string; asset_class?: string; venue?: string };
  state: "approaching" | "triggered" | "pending_review" | "decided" | "invalidated" | "expired" | "superseded";
  triggered_at: string;
  confidence: {
    value: number;
    basis: string;
    calibration_state: "calibrated" | "uncalibrated" | "degraded";
  };
  probability: {
    value: number;
    target_outcome: string;
    horizon: string;
    ci_lower?: number;
    ci_upper?: number;
  };
  expected_value: {
    gross: number;
    cost: number;
    net: number;
    downside: number;
    unit: string;
    horizon: string;
  };
  rationale: Array<{ claim: string; confidence: number; evidence_refs?: string[] }>;
  risk_notes: Array<{ severity: string; domain: string; summary: string; mitigation?: string }>;
  evidence_refs: Array<{ ref_type: string; ref_id: string; summary?: string }>;
  invalidation: { current_state: "none" | "watch" | "invalidated"; reason?: string };
  suggested_action: string;
  suggested_size?: { value: number; unit: string; non_binding: true };
  no_order_route_proof: "agora_decision_support_only";
  position_snapshot?: PositionSnapshot;
  dedupe_key?: string;
  data_cutoff: string;
}

export interface PositionSnapshot {
  current_size: number;
  average_cost: number;
  unrealized_pnl: number;
  current_risk_exposure: unknown;
  original_thesis_ref: string;
  thesis_status: "valid" | "weakened" | "invalidated" | "superseded";
  triggered_rule: string;
  suggested_delta: { value: number; unit: string; non_binding: true };
  alternative_shadow_action?: string;
}

export interface GovernedIntentHandoff {
  spec_version: "1.0";
  handoff_id: string;
  intent_id: string;
  requested_stage: "shadow" | "paper" | "canary" | "live";
  handoff_type: "shadow_start" | "paper_validation_request" | "promotion_review_request";
  state: string;
  strategy_id: string;
  strategy_spec_registry_id: string;
  requested_by: string;
  evidence_refs: Array<{ ref_type: string; ref_id: string; summary?: string }>;
  no_order_route_proof: "agora_request_only_no_order_route";
  action_proposal?: { non_binding: true; [key: string]: unknown };
  created_at: string;
}

export interface CommandResponse {
  command_id: string;
  status: "accepted" | "created";
  resource_id?: string;
}

export interface TraderDecisionBody {
  decision: "approve" | "reject" | "defer" | "modify";
  rationale?: string;
  modifications?: Record<string, unknown>;
}

export interface WriteOptions {
  ifMatch: string;
  idempotencyKey: string;
}

export interface TradingRoomStreamEvent {
  event_id: string;
  event_type: string;
  payload: unknown;
  emitted_at: string;
}

export interface DecisionEventFilter {
  event_kind?: "entry" | "add" | "reduce" | "exit" | "review";
  state?: string;
}
```

### Safety wording constraints (frontend)

The following UI copy is required; deviations are not permitted:

| Action | Required wording | Forbidden wording |
|---|---|---|
| `approve` decision | "Approve intent" | "Execute", "Place order", "Buy/Sell" |
| `modify` decision | "Modify proposal" | "Place modified order", "Execute modified" |
| `reject` decision | "Reject" | N/A |
| `defer` decision | "Defer" | N/A |
| Shadow handoff | "Start shadow" | "Shadow trade", "Shadow order" |
| Paper handoff | "Request paper validation" | "Paper trade", "Paper order" |
| Canary handoff | "Submit canary review request" | "Go canary", "Activate canary" |
| Live handoff | "Submit live review request" | "Go live", "Activate live" |

The `suggested_size` field must always be rendered with a visible "non-binding" label next to the value. It is never a quantity input or a pre-filled order size.

## Pending questions for AG-BE-TR-001 owner

These items require owner clarification before or during implementation. They are
recorded here so they surface during review rather than being silently resolved by
assumption.

| # | Question | Default if not resolved |
|---|---|---|
| Q1 | Should `candidate_count` in the aggregate return `0` with a `degradation_notes` entry while AG-BE-CP-001 is blocked, or should the whole route return `503`? | Return `0` with `degradation_notes: ["candidate_count_unavailable"]` to allow Phase 1 aggregate route to be testable. |
| Q2 | Does the `GET /bff/agora/trading-room` aggregate include `top_decision_events` as a sub-array (per D3), or is this only served via the decision-events list route? | The first sidecar described `top_decision_events` as part of the aggregate; this should be confirmed against the v4 schema (`trading_room_aggregate.schema.json` does not explicitly list it in required fields — it may be an optional array). |
| Q3 | What is the idempotency window for trader decisions? How long should a duplicate `Idempotency-Key` be honoured before expiry? | 24 hours (common default); confirm with owner. |
| Q4 | Should `POST .../handoffs` return `202` (async processing) or `201` (created immediately)? The first sidecar says `202`; the OpenAPI spec should be the authority. | Confirm against `agora_v1_3.openapi.yaml` response codes for `/trading-intents/{intent_id}/handoffs`. |
| Q5 | The Trading Room stream delivers a `trading_room.snapshot` event on connect. Should this snapshot be the full `TradingRoomAggregate` (same shape as `GET /bff/agora/trading-room`), or a lighter event-list-only snapshot? | Recommend full aggregate snapshot to eliminate need for a separate GET on SSE connect; confirm with owner. |

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files were changed by this sidecar. |
| Factual alignment | `AG-BE-TR-001` is `todo` (owner `Claude2`, reviewer `Codex`); `AG-XR-OPENAPI-004` is done; `AG-BE-CP-001` is blocked; `trading_room/router.py` is a placeholder. |
| Phase sequence accuracy | Phase 1 routes correctly exclude candidate-promotion logic; Phase 2 condition correctly names AG-BE-CP-001 as the gate. |
| Position event fields | D9 supplement accurately reflects the D9 specification; no fields invented beyond D9. |
| SSE event catalogue | Trading Room stream event types are grounded in D5 lifecycle states and the aggregate fields (D3) without importing Workshop SSE event types. |
| Frontend types | TypeScript types reflect v4 schema required fields without adding or removing required fields. |
| Pending questions | Questions are factual gaps, not assumptions; none have been resolved by this sidecar. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Followup BFF/frontend handoff packet approved: it adds phased implementation sequence (Phase 1 independent routes vs Phase 2 candidate-gated route), backend module structure and no-order-route invariant enforcement, D9 position event field detail, Trading Room SSE event catalogue and reconnect contract, BFF degraded-response patterns, TypeScript types, and safety wording constraints — all as support material without modifying canonical truth." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only AG-BE-TR-001 BFF/frontend handoff followup approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff_followup_2.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
# in_progress; owner Claude; reviewer Claude2; helper_parent AG-BE-TR-001; helper_kind bff_handoff_packet

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex; depends_on AG-BE-CP-001 (blocked), AG-XR-OPENAPI-004 (done)

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF
# source: archive; terminal_status: done; archived_at 2026-06-21T18:42:08Z

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# blocked; owner Codex; reviewer Claude2

cat services/control-plane/bff/agora/trading_room/router.py
# Placeholder returning empty APIRouter; migration note present; no route handlers.

python3 -m json.tool services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json > /dev/null
# Valid JSON schema.

python3 -m json.tool services/control-plane/specs/agora/v4/trading_decision_event.schema.json > /dev/null
# Valid JSON schema.

python3 -m json.tool services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json > /dev/null
# Valid JSON schema.

ls execute-plans/src/lib/bff-v1/agora/
# dashboard.ts  types.ts  contract-snapshot.json
# tradingRoom.ts confirmed absent.
```
