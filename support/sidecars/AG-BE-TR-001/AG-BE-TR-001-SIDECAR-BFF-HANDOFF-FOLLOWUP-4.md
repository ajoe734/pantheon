# AG-BE-TR-001 BFF and Frontend Handoff Packet — Followup 4

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` — Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Supersedes / builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` (PR #2139) |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. The parent owner decides whether and how to absorb
this material.

## Cumulative packet scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF` (done) | BFF query gap matrix, operator journeys A–H, `tradingRoom.ts` method signatures, acceptance checks, open design notes. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done) | Phased implementation sequence, backend module structure, D9 position event fields, Trading Room SSE contract, BFF degraded-response patterns, TypeScript types, safety wording, pending questions Q1–Q5. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (review_approved) | Schema-derived TypeScript type corrections, Q1/Q2/Q4 resolutions, `additionalProperties` degradation-signalling clarification, idempotency implementation pattern, BFF test structure supplement, remaining open questions Q3/Q5/Q6/Q7. |
| **This packet (FOLLOWUP-4)** | Q3/Q5/Q6/Q7 resolutions, SSE channel catalog gap, router injection gap (missing `get_read_store` and SSE hooks), `GovernedIntentHandoff` action_proposal and state lifecycle types, additional acceptance checks, and updated TypeScript types. |

## Current state observed

| Surface | Observed 2026-06-21 | Change since Packet 3 |
|---|---|---|
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. D8 promotion leg still gated. |
| `AG-FE-TR-001` | `todo`. | Unchanged. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. |
| `SSE_CHANNEL_CATALOG` in `main.py` | Does **not** include `"trading_room"`. | **New finding** — see § SSE channel catalog gap below. |
| `create_trading_room_router` call site | Receives only `extract_identity`, `require_read_role`, `bff_error`, `utc_now`. | **New finding** — see § Router injection gap below. |

## New findings from codebase inspection

### SSE channel catalog gap

`main.py` line 41753 defines `SSE_CHANNEL_CATALOG` as a tuple of named channels. The
`"trading_room"` channel is **absent**. `_sse_buffers` and `_sse_subscribers` are
initialized from this catalog, so the Trading Room stream route cannot use
`_handle_sse_stream` or `_sse_stream` without a catalog entry.

**Action required by the parent owner before implementing the stream route:**

```python
# In main.py — add "trading_room" to SSE_CHANNEL_CATALOG before the dict initializations:
SSE_CHANNEL_CATALOG = (
    "approval",
    "ask",
    "artifact",
    "runtime",
    "mcp",
    "skill",
    "channel",
    "tool",
    "ranking",
    "rebalance",
    "evolution",
    "research",
    "signal",
    "inbox",
    "journal",
    "postmortem",
    "loop",
    "sentinel",
    "intervention",
    "audit",
    "system",
    "trading_room",  # <-- add this entry
)
```

After adding the channel, `_sse_buffers["trading_room"]` and
`_sse_subscribers["trading_room"]` will be automatically created by the existing
dict comprehension. The Trading Room SSE stream route can then call:

```python
return _handle_sse_stream(
    channel="trading_room",
    buffer=_sse_buffers["trading_room"],
    subscribers=_sse_subscribers["trading_room"],
    last_event_id=...,
)
```

Because `_sse_buffers` and `_sse_subscribers` are module-level globals in `main.py`,
the router factory needs to receive them via injection or closure. See the router
injection gap section for the recommended approach.

### Router injection gap

The `create_trading_room_router` factory is called from `agora/router.py` line 174:

```python
router.include_router(create_trading_room_router(**_kw))
```

`_kw` contains only `extract_identity`, `require_read_role`, `bff_error`, and `utc_now`.
This is insufficient for the Trading Room routes, which need:

- `get_read_store` — for reading decision events, trading intents, and the aggregate read model.
- `get_sse_buffer` / `get_sse_subscribers` — for publishing to and streaming from the `"trading_room"` SSE channel.
- `get_command_store` — for writing decisions and governed handoffs to the command log (same pattern as other action routes).
- `get_trading_room_idempotency` — for accessing the in-process idempotency dict.

**Recommended signature extension for `create_trading_room_router`:**

```python
def create_trading_room_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Callable[[], Any],
    get_command_store: Callable[[], Any],
    get_sse_buffer: Callable[[str], deque],
    get_sse_subscribers: Callable[[str], list[asyncio.Queue]],
    get_trading_room_idempotency: Callable[[], Dict[str, Dict[str, Any]]],
) -> APIRouter:
```

**Required change in `agora/router.py`** — extend the router factory to accept and
forward these parameters:

```python
def create_agora_router(
    *,
    extract_identity,
    require_read_role,
    bff_error,
    utc_now,
    get_read_store,
    sync_servant_agent,
    # add:
    get_command_store: Optional[Callable[[], Any]] = None,
    get_sse_buffer: Optional[Callable[[str], Any]] = None,
    get_sse_subscribers: Optional[Callable[[str], Any]] = None,
    get_trading_room_idempotency: Optional[Callable[[], Any]] = None,
) -> APIRouter:
```

And in `main.py`, pass closures when calling `create_agora_router`:

```python
app.include_router(
    _create_agora_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        get_read_store=lambda: read_store,
        sync_servant_agent=lambda persona: _ensure_agora_servant_openclaw_agent(dict(persona)),
        # add:
        get_command_store=lambda: command_store,
        get_sse_buffer=lambda ch: _sse_buffers[ch],
        get_sse_subscribers=lambda ch: _sse_subscribers[ch],
        get_trading_room_idempotency=lambda: _TRADING_ROOM_IDEMPOTENCY,
    )
)
```

This approach keeps the router factory stateless and testable: tests that need a
trading room router can pass a `ReadSurfaceStore` from a temp directory and a
`CommandStore` from a temp file, matching the `_isolated_bff()` pattern from Packet 3.

## Resolved open questions

### Q3 — Idempotency window (TTL)

**Resolution (recommendation, not canonical decision):** The existing
`_GOV_BFF_IDEMPOTENCY` dict in `main.py` (line 43921) is an in-process
`Dict[str, Dict[str, Any]]` with no explicit TTL. Keys live for the process
lifetime.

For AG-BE-TR-001:

- **Initial implementation**: follow the same in-process pattern by declaring
  `_TRADING_ROOM_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}` at module level
  in `main.py` (or accepted via closure in the router factory). No TTL needed
  for the first ship.
- **Production recommendation**: a 24-hour TTL with a durable store (Redis or a
  bounded JSONL file with periodic compaction) is conventional. Without TTL,
  pod restarts silently drop the idempotency record, so a client that retries
  across a restart could create duplicate decisions or handoffs.

**Owner decision required**: whether to add TTL in the initial implementation or
defer it to a follow-on task. The sidecar recommendation is to defer TTL to a
follow-on to keep the initial PR scope tight.

### Q5 — SSE initial snapshot event shape

**Resolution:** Send a full `TradingRoomAggregate` payload as the first SSE event
on each new connection, using the event type `"trading_room.snapshot"`. This
avoids requiring a separate `GET /bff/agora/trading-room` call on first connect or
reconnect.

The existing `_sse_stream` helper (lines 41981–42020) replays buffered events via
`_replay_from_channel` on connect, then streams new events. For the Trading Room:

1. On every new connection (including reconnect), the BFF should publish a
   `"trading_room.snapshot"` event to the buffer immediately before the subscriber
   queue is set up, so that the snapshot is the first event replayed.

   Alternatively, the stream route can emit the snapshot directly into the new
   subscriber's queue before entering the replay/live loop — similar to the workshop
   stream pattern.

2. Subsequent incremental events use specific `event_type` values (see § SSE event
   type enumeration below).

**Recommended SSE event shape** (mirrors the `WorkshopStreamEvent` structure):

```ts
interface TradingRoomStreamEvent {
  spec_version: "1.0";
  event_id: string;           // UUID
  event_type: TradingRoomEventType;
  aggregate_type: "trading_room";
  aggregate_id: string;       // user_scope_ref (tenant:user)
  sequence_no: number;
  event_time: string;         // ISO-8601
  emitted_at: string;
  trace_id: string;
  idempotency_key: string;
  payload: TradingRoomEventPayload;
}

type TradingRoomEventType =
  | "trading_room.snapshot"            // full TradingRoomAggregate
  | "trading_room.decision_event.state_changed"   // single TradingDecisionEvent with new state
  | "trading_room.decision_event.created"
  | "trading_room.decision_event.invalidated"
  | "trading_room.queue_summary.updated"           // queue count delta
  | "trading_room.risk_summary.updated"            // risk_summary object
  | "trading_room.strategy.readiness_changed"      // single strategy entry
  | "stream.heartbeat"                 // BFF keep-alive (no payload)
  | "stream.error";                    // BFF-side error notification
```

**`trading_room.snapshot` payload**: the full `TradingRoomAggregate` object (same
schema as `GET /bff/agora/trading-room`). The frontend should treat this as a full
state replacement, not a delta.

**Frontend reconnect**: if the SSE connection drops, the frontend should reconnect
with the `Last-Event-ID` header set to the last received `event_id`. The BFF's
`_sse_stream` helper replays from that point, so the frontend only misses events
newer than `_MAX_EVENTS` (currently 1000 per channel). If the gap is too large,
the replay path returns a `409 SSE_REPLAY_HISTORY_MISSING`; the frontend should
then fall back to a `GET /bff/agora/trading-room` call and re-subscribe without
`Last-Event-ID` to receive a fresh snapshot.

### Q6 — Who populates `position_snapshot` on add/reduce/exit/review events

**Resolution (recommendation):** `position_snapshot` should be stored with the
decision event record at the time the event is created by the projection, not
joined at query time. This is consistent with D9 ("the projection includes...") and
avoids a live position query on every decision-event read.

**Why stored-with-event, not joined at query time:**

- The position state at the time the event was triggered may differ from the current
  position at query time (trades settled, risk updated). Showing the trader the
  position as of trigger time is more useful than showing the current position.
- Query-time join requires the position projection to be available synchronously on
  every `GET /bff/agora/trading-room/decision-events/{id}` call — this creates a
  latency dependency and a new degraded-response path.
- The `position_snapshot` schema uses `"additionalProperties": true`, which is
  intentionally open-ended to allow the parent owner to evolve the shape.

**Recommended `position_snapshot` fields (based on D9):**

```ts
interface PositionSnapshot {
  position_ref: string;          // link to the position record
  symbol: string;
  asset_class?: string;
  venue?: string;
  direction: "long" | "short";
  quantity: number;              // current quantity at event trigger time
  quantity_unit: string;         // e.g. "shares", "contracts"
  average_cost?: number;
  unrealised_pnl?: number;
  unrealised_pnl_unit?: string;
  current_risk_exposure?: number;
  current_risk_unit?: string;
  original_thesis_ref?: string;  // ref to the research/proposal that originated the position
  thesis_status?: "active" | "invalidated" | "expired";
  alternative_action?: string;   // shadow/paper action that diverges from suggested_action
  as_of: string;                 // ISO-8601 timestamp at event creation
}
```

**Note on `"additionalProperties": true`:** the schema explicitly allows extra
fields. The parent owner may extend this shape without a schema version bump.
The frontend should use the fields it knows and ignore unknown keys.

### Q7 — `decision_state` update semantics

**Resolution (recommendation):** `decision_state` should be a live projection
field that updates as the intent/handoff lifecycle progresses, not a frozen
snapshot at the time of the trader decision.

**Rationale:**

- The decision-event card in the Trading Room UI should reflect the current state
  of any decision that has been made — showing `"approved_by_trader"` while the
  intent is being reviewed by governance, then `"handed_off"` once a governed
  handoff is submitted — without the operator needing to navigate to the intent
  detail screen.
- The alternative (snapshot at decision time) means `decision_state` always reads
  `"pending"` for new events and `"approved_by_trader"` / `"rejected_by_trader"` /
  `"deferred"` forever after the decision, regardless of what happened to the intent.
  This is misleading when governance rejects the handoff or the intent is superseded.

**Recommended projection update triggers:**

| Transition | `decision_state` value |
|---|---|
| Event first created, no decision yet | `"pending"` |
| `POST .../decisions` with `{decision: "approve"}` | `"approved_by_trader"` |
| `POST .../decisions` with `{decision: "reject"}` | `"rejected_by_trader"` |
| `POST .../decisions` with `{decision: "defer"}` | `"deferred"` |
| `POST .../decisions` with `{decision: "modify"}` | `"approved_by_trader"` (intent created with modifications) |
| `POST .../handoffs` submitted for the associated intent | `"handed_off"` |
| Decision event expires without a decision (`state: "expired"`) | `"expired"` |
| Decision event superseded by a newer event for the same strategy/symbol/kind | `"superseded"` |

**Implementation note**: the BFF read model (`ReadSurfaceStore`) stores the
decision event record, including `decision_state`. When a decision is recorded
via `POST .../decisions`, the BFF should update the stored event record's
`decision_state` field in addition to creating the `TradingIntent`. When a
governed handoff is submitted, the BFF should update `decision_state` to
`"handed_off"` on the associated event record. The SSE stream should then emit a
`"trading_room.decision_event.state_changed"` event.

## GovernedIntentHandoff type supplement

The prior packets described the handoff request body but did not fully enumerate the
server-side schema. The following additions are schema-derived and supplement Packet 3.

### State lifecycle

The `state` field of `GovernedIntentHandoff` follows this lifecycle:

```
draft → submitted → accepted → converted
                 ↘ rejected
                 ↘ expired
             submitted → withdrawn
```

When the BFF persists the handoff record on `POST .../handoffs`, it should set
`state: "submitted"`. Transitions to `accepted`, `rejected`, `expired`,
`converted`, or `withdrawn` are downstream governance decisions; the BFF does
not directly drive those transitions via the Agora surface.

### `target_queue` is schema-defined (not just inferred)

Packet 1 described the routing as `shadow → shadow_research`, `paper →
management_governance`, `canary`/`live` → `promotion_review`. The schema confirms
this: `target_queue` is an optional field with enum `["shadow_research",
"management_governance", "promotion_review"]`. The BFF should populate
`target_queue` at submission time based on `requested_stage`:

```python
_STAGE_TO_QUEUE = {
    "shadow": "shadow_research",
    "paper": "management_governance",
    "canary": "promotion_review",
    "live": "promotion_review",
}
target_queue = _STAGE_TO_QUEUE[body["requested_stage"]]
```

### `action_proposal` schema (schema-derived TypeScript type)

The prior packets described `action_proposal.non_binding: true` as a requirement
but did not enumerate the full shape. The schema defines:

```ts
interface ActionProposal {
  action?: "enter" | "add" | "reduce" | "exit" | "review";
  symbol?: string;
  direction?: string;
  size_hint?: string;           // qualitative band ("small" / "medium" / "large" etc.)
  portfolio_pct?: number;       // 0–1 fraction of portfolio
  non_binding: true;            // const; MUST be present if action_proposal is present
}
```

Note that `action_proposal` as a whole is optional on the handoff body, but
if it is present, `non_binding: true` is required. The BFF should reject any
`action_proposal` body that omits `non_binding` or sets it to `false`.

### `requested_by` actor shape

The `requested_by` field references the `actor` definition:

```ts
interface HandoffActor {
  actor_type: "trader" | "agora_servant" | "institutional_persona" | "system";
  actor_ref: string;            // minLength: 1
  session_id?: string;
  display_name?: string;
}
```

The BFF should populate `requested_by` from the authenticated identity
(`identity.operator_id` as `actor_ref`, `actor_type: "trader"`).

### `expires_at` on the handoff

The schema includes an optional `expires_at` on the handoff record (ISO-8601).
For the initial implementation, the parent owner may leave this blank. A
governance expiry window (e.g. 7 days for canary/live review requests) is a
governance-layer concern, not a BFF concern.

## Additional acceptance checks

These checks supplement the acceptance checks in Packets 1 and 3:

| Check | Expected result |
|---|---|
| `"trading_room"` in `SSE_CHANNEL_CATALOG` | `"trading_room"` is present in `SSE_CHANNEL_CATALOG` before the stream route is exercised; `_sse_buffers["trading_room"]` and `_sse_subscribers["trading_room"]` exist at app startup. |
| SSE first event is snapshot | On first connect (no `Last-Event-ID`), the stream emits a `trading_room.snapshot` event whose payload is a valid `TradingRoomAggregate`. |
| SSE reconnect replay | On reconnect with `Last-Event-ID: <event_id>`, the stream replays events newer than that id from the buffer. If gap exceeds buffer: `409 SSE_REPLAY_HISTORY_MISSING`. |
| SSE heartbeat | Stream emits `: heartbeat` SSE comment every 30 s when no events arrive. |
| `decision_state` updates | After `POST .../decisions` (approve), the `GET .../decision-events/{id}` response includes `decision_state: "approved_by_trader"`. After `POST .../handoffs`, `decision_state` updates to `"handed_off"`. |
| `position_snapshot` on add/reduce/exit/review | `position_snapshot` is present on decision events with `event_kind` in `["add", "reduce", "exit", "review"]`. It must include at least `position_ref`, `symbol`, `direction`, `quantity`, and `as_of`. |
| `target_queue` on handoff record | Persisted `GovernedIntentHandoff` record includes `target_queue` populated from `_STAGE_TO_QUEUE[requested_stage]`. |
| `action_proposal.non_binding` enforced | If `action_proposal` is present in handoff body but `non_binding` is missing or `false`, return `422`. |
| Handoff state on submit | Persisted `GovernedIntentHandoff` record has `state: "submitted"`. |
| `requested_by` populated from identity | `requested_by.actor_type == "trader"` and `requested_by.actor_ref == identity.operator_id`. |
| Router injection smoke test | `create_trading_room_router` called without `get_read_store` raises a `TypeError`; tests must pass `get_read_store` explicitly. |
| `_TRADING_ROOM_IDEMPOTENCY` cleared between tests | The `_isolated_bff()` context manager clears `_TRADING_ROOM_IDEMPOTENCY` on setup and teardown (per Packet 3 test fixture). |

## Reviewer handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files were changed. |
| SSE catalog gap accurate | `SSE_CHANNEL_CATALOG` in `main.py` line 41753 confirms "trading_room" is absent. |
| Router injection gap accurate | `create_trading_room_router` call in `agora/router.py` line 174 confirms only `_kw` is passed (no `get_read_store`, SSE hooks, or command store). |
| Q5 resolution consistent with `_sse_stream` | The `_sse_stream` helper (lines 41981–42020) confirms: replay-on-connect, heartbeat-on-timeout, unsubscribe-on-disconnect. Snapshot-on-connect recommendation is consistent with the pattern. |
| Q6 resolution consistent with D9 | D9 lists position fields that belong in the event projection. Stored-with-event is the stated approach. `additionalProperties: true` confirmed. |
| Q7 resolution | `decision_state` enum from schema confirmed (`pending`, `approved_by_trader`, `rejected_by_trader`, `deferred`, `expired`, `handed_off`, `superseded`). Live-projection recommendation is consistent with UI needs. |
| Q3 resolution | In-process pattern confirmed from `_GOV_BFF_IDEMPOTENCY` (line 43921). 24h production recommendation is conventional and not invented. |
| `GovernedIntentHandoff` additions | `action_proposal`, `state` enum, `target_queue`, `requested_by` actor — all confirmed from `governed_intent_handoff.schema.json`. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md \
  REVIEW_NOTES_ZH="Followup-4 BFF/frontend handoff packet approved: identifies SSE channel catalog gap (trading_room absent from SSE_CHANNEL_CATALOG) and router injection gap (get_read_store/SSE hooks/command store not passed to create_trading_room_router); resolves Q3 (in-process idempotency acceptable for initial ship, 24h TTL for production), Q5 (full TradingRoomAggregate snapshot as first SSE event on connect, TradingRoomStreamEvent type enumeration), Q6 (position_snapshot stored with event per D9, recommended PositionSnapshot fields), Q7 (decision_state is live projection updated as intent/handoff progresses); adds GovernedIntentHandoff type supplement (action_proposal shape, state lifecycle, target_queue mapping, requested_by actor, expires_at note) — all as support material without modifying canonical truth." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Support-only AG-BE-TR-001 BFF/frontend handoff followup-4 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

## Validation run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4

git status --short
# (clean worktree — all task files committed)

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
# review_approved; owner Claude; reviewer Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# blocked; owner Codex; reviewer Claude2

# SSE channel catalog gap confirmed:
grep -n "trading_room" services/control-plane/bff/main.py
# (no results in SSE_CHANNEL_CATALOG area; only trading_room/router.py import)

grep -n "SSE_CHANNEL_CATALOG" services/control-plane/bff/main.py
# 41753: SSE_CHANNEL_CATALOG = (
# Lists 21 channels; "trading_room" is absent.

# Router injection gap confirmed:
grep -n "create_trading_room_router" services/control-plane/bff/agora/router.py
# 31: from .trading_room.router import create_trading_room_router
# 174: router.include_router(create_trading_room_router(**_kw))
# _kw contains only extract_identity, require_read_role, bff_error, utc_now.

# GovernedIntentHandoff schema confirmed:
python3 -m json.tool services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json > /dev/null
# Valid JSON schema.

# action_proposal.non_binding const true confirmed:
python3 -c "
import json
with open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json') as f:
    s = json.load(f)
ap = s['properties']['action_proposal']['properties']['non_binding']
print(ap)
"
# {'type': 'boolean', 'const': True}

# state enum confirmed:
python3 -c "
import json
with open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json') as f:
    s = json.load(f)
print(s['properties']['state']['enum'])
"
# ['draft', 'submitted', 'accepted', 'rejected', 'expired', 'withdrawn', 'converted']

# target_queue enum confirmed:
python3 -c "
import json
with open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json') as f:
    s = json.load(f)
print(s['properties']['target_queue']['enum'])
"
# ['shadow_research', 'management_governance', 'promotion_review']

# position_snapshot additionalProperties: true confirmed:
python3 -c "
import json
with open('services/control-plane/specs/agora/v4/trading_decision_event.schema.json') as f:
    s = json.load(f)
print(s['properties']['position_snapshot'])
"
# {'type': 'object', 'additionalProperties': True}

# _GOV_BFF_IDEMPOTENCY in-process pattern confirmed (no TTL):
grep -n "_GOV_BFF_IDEMPOTENCY\s*:" services/control-plane/bff/main.py
# 41812: _sse_buffers: Dict[str, deque] = {
# (separate line near line 43921):
# _GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
```
