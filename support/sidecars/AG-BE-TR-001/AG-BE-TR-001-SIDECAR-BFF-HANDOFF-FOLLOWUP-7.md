# AG-BE-TR-001 BFF and Frontend Handoff Packet - Followup 7

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` - Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Codex` |
| Reviewer | `Claude2` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Supersedes / builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md` |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. The parent owner decides whether and how to absorb
this material.

## Cumulative packet scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF` | BFF query gap matrix, operator journeys A-H, `tradingRoom.ts` method signatures, acceptance checks, open design notes. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Phased implementation sequence, backend module structure, D9 position event fields, Trading Room SSE contract, BFF degraded-response patterns, TypeScript types, safety wording, pending questions Q1-Q5. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Schema-derived TypeScript type corrections, Q1/Q2/Q4 resolutions, `additionalProperties` degradation-signalling clarification, idempotency implementation pattern, BFF test structure supplement, remaining open questions Q3/Q5/Q6/Q7. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Q3/Q5/Q6/Q7 resolutions, SSE channel catalog gap, router injection gap, `GovernedIntentHandoff` lifecycle types, additional acceptance checks. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | `CommandResponse`, `DetailEnvelope`, and `ListEnvelope` corrections; `TradingIntent` schema; `StrategyReadinessAssessment`; `allowedActions`; corrected TypeScript types. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Mutation HTTP statuses, required write headers, `ErrorEnvelope`, decision-event filters, no-body `withdraw`, full `GovernedIntentHandoff` create body, decision-to-state mapping, Q8-Q10. |
| **This packet (FOLLOWUP-7)** | Browser/CORS blocker for `If-Match` and `ETag`, runtime `_bff_error`/`ErrorCode` allowlist gap, write-role injection gap, missing Trading Room read-store datasets and command/object enum names, SSE replay-window correction (`500`, not `1000`) and resync-route header gap, and focused parent acceptance checks. |

## Sources read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates lifecycle; support packets do not override canonical architecture. |
| `.orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff_followup_7.md` | Sidecar is support-only: prepare BFF/frontend handoff material for `AG-BE-TR-001`; do not change canonical truth. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support doc changes must be committed with narrow scope and trailers. |
| `.orchestrator/skills/task-closeout-finalization.md` | Final lifecycle requires task commit, PR, review, merge, and `done`; this packet is owner handoff, not owner finalization. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Sidecar is `in_progress`, owner `Codex`, reviewer `Claude2`, helper parent `AG-BE-TR-001`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001` | Parent is `todo`; owner `Claude2`, reviewer `Codex`; depends on `AG-BE-CP-001` and `AG-XR-OPENAPI-004`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-CP-001` | Candidate pool task is currently `todo`; D8 candidate-to-decision-event promotion remains parent-coordinated dependency work. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001` | Frontend Trading Room task is `todo`; depends on `AG-BE-TR-001` and must bind via `tradingRoom.ts` live strict. |
| `services/control-plane/bff/main.py` | `_CORS_ALLOW_HEADERS` lacks `If-Match`; `_CORS_EXPOSE_HEADERS` lacks `ETag`; `_MAX_EVENTS` is `500`; `SSE_CHANNEL_CATALOG` lacks `trading_room`; `_bff_error` accepts `ErrorCode`, not arbitrary `TRADING_*` strings; `_require_operator_role` exists but is not injected into the Agora sub-router. |
| `services/control-plane/bff/agora/router.py` | `create_agora_router` passes only `extract_identity`, `require_read_role`, `bff_error`, and `utc_now` to `create_trading_room_router`; no write-role guard, command store, SSE closures, or idempotency store are forwarded. |
| `services/control-plane/bff/agora/trading_room/router.py` | Placeholder `APIRouter`; no Trading Room routes implemented. |
| `services/control-plane/bff/read_store.py` | `ReadSurfaceStore._LOCAL_DATA_KEYS` has many Agora datasets but no `agora_trading_room_*`, `agora_trading_decision_events`, `agora_trading_intents`, or governed-intent-handoff datasets/methods. |
| `services/control-plane/bff/models.py` | `CommandType` and `ObjectType` do not currently include Trading Room decision, Trading Intent, or Governed Intent Handoff command/target enum entries. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Trading Room mutations require `If-Match`, `Idempotency-Key`, and `X-Request-Id`; mutation success statuses remain `201`/`202`/`200`; `ErrorEnvelope` is simple and permits runtime-compatible extra fields unless `additionalProperties: false` is later added. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current state observed on 2026-06-22

| Surface | Observed state | Parent handoff meaning |
|---|---|---|
| Branch | `task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`, fast-forwarded to `origin/dev` before packet work. | Packet is based on current `dev` as of 2026-06-22. |
| `AG-BE-TR-001` | `todo`, owner `Claude2`, reviewer `Codex`. | Parent implementation has not started in this worktree. |
| `AG-BE-CP-001` | `todo`, owner `Codex`, reviewer `Claude2`. | Candidate-to-decision-event promotion remains dependent coordination; not solved by this packet. |
| `trading_room/router.py` | Placeholder only. | Parent must implement all Trading Room routes here. |
| `create_agora_router` injection | No write-role guard, command store, SSE closures, or idempotency store passed to Trading Room router. | Parent must extend injection before mutation/SSE routes can be implemented cleanly. |
| CORS headers | `If-Match` is not in `_CORS_ALLOW_HEADERS`; `ETag` is not in `_CORS_EXPOSE_HEADERS`. | Browser frontend cannot reliably perform the required write flow until this is fixed. |
| SSE replay window | `_MAX_EVENTS = 500`. | Corrects earlier sidecar wording that referenced `1000` events. |

## New findings

### Finding 1 - Browser CORS blocks the required `If-Match` / `ETag` flow

Followup-6 correctly documented that all three mutation endpoints require
`If-Match`, `Idempotency-Key`, and `X-Request-Id`. The current BFF CORS config
allows `Idempotency-Key` and `X-Request-Id`, but not `If-Match`:

```python
_CORS_ALLOW_HEADERS = [
    ...,
    "Idempotency-Key",
    "Last-Event-ID",
    "X-Correlation-Id",
    "X-Request-Id",
    ...
]
```

The current exposed response headers also do not include `ETag`:

```python
_CORS_EXPOSE_HEADERS = [
    "X-BFF-Api-Version",
    "X-Correlation-Id",
    "X-Request-Id",
]
```

**Impact**: a browser-based execute-plans client cannot safely complete the
OpenAPI-required optimistic concurrency loop:

1. `GET /decision-events/{id}` or `GET /trading-intents/{id}` needs to return an
   `ETag` response header.
2. The frontend needs to read that `ETag`.
3. The frontend needs to send `If-Match` on the subsequent POST.
4. Browser preflight must allow `If-Match`.

Without CORS support, the write request can fail before it reaches the route
handler, even if the BFF route implementation is correct.

**Parent implementation action**:

```python
_CORS_ALLOW_HEADERS = [
    ...,
    "If-Match",
    ...
]

_CORS_EXPOSE_HEADERS = [
    ...,
    "ETag",
]
```

The BFF should also set `ETag` on:

- `GET /bff/agora/trading-room/decision-events/{decision_event_id}`
- `GET /bff/agora/trading-intents/{intent_id}`

This directly supports Q9 from Followup-6. Recommended ETag source remains a
SHA-256 hash of the stable resource state plus its last modified timestamp.

### Finding 2 - Runtime `_bff_error` cannot emit arbitrary `TRADING_*` codes today

Followup-6 proposed Trading Room-specific `error.code` values such as
`TRADING_DECISION_EVENT_NOT_FOUND`, `ETAG_MISMATCH`, and
`HANDOFF_VALIDATION_FAILED`. The v1.3 OpenAPI `ErrorEnvelope` permits a string
code, but the current runtime helper is stricter:

```python
def _bff_error(status_code: int, code: ErrorCode, ...)
```

`ErrorCode` is an enum in `services/control-plane/bff/models.py`. It does not
currently include Trading Room-specific values. Passing a raw `TRADING_*` string
to `_bff_error` would not match the helper signature.

**Parent owner decision**:

| Option | Effect |
|---|---|
| Extend `ErrorCode` with Trading Room-specific codes | Lets BFF responses match the packet-specific `TRADING_*` vocabulary directly. This is a runtime/model change and should be reviewed as parent implementation scope. |
| Use existing canonical `ErrorCode` values and put Trading Room subcodes in `details` | Avoids expanding the enum in the first parent PR; frontend maps `details.trading_code` or `details.precondition_failed` to Trading Room UI messages. |

**Recommended narrow first implementation**: use existing `ErrorCode` values
unless the parent owner explicitly expands the enum. Suggested mapping:

| Scenario | HTTP | Existing `ErrorCode` | Trading detail field |
|---|---:|---|---|
| Decision event not found | 404 | `RESOURCE_NOT_FOUND` | `details.trading_code = "TRADING_DECISION_EVENT_NOT_FOUND"` |
| Intent not found | 404 | `RESOURCE_NOT_FOUND` | `details.trading_code = "TRADING_INTENT_NOT_FOUND"` |
| `If-Match` mismatch | 412 | `PRECONDITION_FAILED` | `details.trading_code = "ETAG_MISMATCH"` |
| Duplicate idempotency key with different body | 409 | `IDEMPOTENCY_CONFLICT` | `details.trading_code = "IDEMPOTENCY_KEY_CONFLICT"` |
| Decision already recorded | 409 | `RESOURCE_CONFLICT` | `details.trading_code = "DECISION_ALREADY_RECORDED"` |
| Handoff body invalid | 422 | `VALIDATION_FAILED` | `details.trading_code = "HANDOFF_VALIDATION_FAILED"` |
| Handoff not allowed | 422 or 403 | `OPERATION_NOT_ALLOWED` or `FORBIDDEN` | `details.trading_code = "TRADING_INTENT_HANDOFF_NOT_ALLOWED"` |

The current runtime `BffErrorEnvelope` includes extra fields such as `i18nKey`,
`retryable`, `userActionable`, and structured `details`. That remains compatible
with the current OpenAPI `ErrorEnvelope` because the component does not set
`additionalProperties: false` on `error`.

### Finding 3 - Mutation routes need a write-role guard, but the router only receives read-role today

`main.py` defines separate read and write guards:

```python
_READ_ROLES = {"viewer", "operator", "approver", "admin", "reviewer"}
_WRITE_ROLES = {"operator", "approver", "admin", "reviewer"}
```

`create_agora_router` currently forwards only `require_read_role` to the Trading
Room router. This is sufficient for GET routes but insufficient for:

- `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions`
- `POST /bff/agora/trading-intents/{intent_id}/handoffs`
- `POST /bff/agora/trading-intents/{intent_id}/withdraw`

**Parent implementation action**: extend the router factory injection with a
write guard, for example:

```python
def create_agora_router(
    *,
    extract_identity,
    require_read_role,
    require_operator_role,
    bff_error,
    utc_now,
    get_read_store,
    sync_servant_agent,
    ...
) -> APIRouter:
    ...
    router.include_router(create_trading_room_router(
        **_kw,
        require_operator_role=require_operator_role,
        ...
    ))
```

Then use:

- `require_read_role(identity)` for aggregate/detail/list/stream reads.
- `require_operator_role(identity)` for decisions, handoffs, and withdrawals.

**Frontend implication**: a `viewer` can open and inspect the Trading Room but
must receive `403` for write actions.

### Finding 4 - Read store and command model do not yet have Trading Room-specific storage surfaces

`ReadSurfaceStore._LOCAL_DATA_KEYS` currently has many Agora datasets
(`agora_signals`, `agora_handoffs`, `agora_sessions`, etc.) but no Trading Room
datasets for:

- Trading Room aggregate snapshots.
- Trading decision events.
- Trading intents.
- Governed intent handoffs.

The existing `CommandStore.submit_command()` also requires `CommandType` and
`ObjectType` enum values. `models.py` does not currently define Trading
Room-specific command or target types.

**Parent implementation action**:

1. Add explicit read-store datasets/methods instead of reading JSON files
   directly in `trading_room/router.py`.
2. Decide whether mutation command logging uses new enum values or another
   already-approved command envelope. If new enum values are required, define
   them in the parent PR rather than burying string literals in the router.
3. Keep no-order proof fields in the stored domain records:
   - `TradingDecisionEvent.no_order_route_proof = "agora_decision_support_only"`
   - `TradingIntent.no_order_route_proof = "agora_intent_record_only"`
   - `GovernedIntentHandoff.no_order_route_proof = "agora_request_only_no_order_route"`

Recommended read-store method shape for parent owner review:

```python
list_trading_decision_events(event_kind: str | None, state: str | None) -> list[dict]
get_trading_decision_event(decision_event_id: str) -> dict | None
update_trading_decision_event(decision_event_id: str, patch: dict) -> dict | None
get_trading_intent(intent_id: str) -> dict | None
create_trading_intent(record: dict) -> dict
create_governed_intent_handoff(record: dict) -> dict
withdraw_trading_intent(intent_id: str, actor_id: str, withdrawn_at: str) -> dict | None
```

The exact dataset names are a parent implementation decision, but they should
be explicit and testable.

### Finding 5 - SSE replay window is 500 events, and Trading Room resync routes are not registered

Followup-4 described the shared SSE helper and mentioned a replay window of
1000 events. Current `main.py` says:

```python
_MAX_EVENTS = 500
```

`SSE_CHANNEL_CATALOG` also still lacks `"trading_room"`, and
`_SSE_RESYNC_ROUTES` has entries for `"approval"` and `"ask"` only.

**Parent implementation action**:

```python
SSE_CHANNEL_CATALOG = (
    ...,
    "trading_room",
)

_SSE_RESYNC_ROUTES["trading_room"] = (
    "/bff/agora/trading-room",
    "/bff/agora/trading-room/decision-events",
    "/bff/agora/trading-room/decision-events/{decision_event_id}",
    "/bff/agora/trading-intents/{intent_id}",
)
```

The Trading Room stream route should pass `channel="trading_room"` to
`_handle_sse_stream` so response headers include:

- `X-SSE-Channel: trading_room`
- `X-SSE-Replay-Window-Events: 500`
- `X-SSE-Resync-Routes: ...`

Frontend reconnect logic must treat `409 SSE_REPLAY_HISTORY_MISSING` as a
signal to refetch the aggregate/detail routes and resubscribe. It should not
assume a 1000-event replay window.

### Finding 6 - Native browser `EventSource` cannot attach `Authorization`

The Trading Room stream is privileged Trading Room data, unlike the generic
`/bff/events/stream` liveness stream. Native browser `EventSource` does not let
the client attach an `Authorization` header. The existing generic BFF event
stream has an explicit comment that it emits only non-sensitive liveness events
until a cookie-backed SSE auth path exists.

**Parent/frontend coordination action**:

| Client approach | Requirement |
|---|---|
| Native `EventSource` | Use a cookie/session-backed auth path accepted by the BFF stream route. |
| Fetch-based SSE client | Attach `Authorization` and `Last-Event-ID` headers explicitly. |

The frontend `streamTradingRoom(onEvent)` implementation should choose one of
these intentionally. It should not silently subscribe to a privileged stream
without an auth strategy.

## Q8-Q10 recommended disposition

These recommendations are still support material; the parent owner decides the
final implementation.

| Question | Recommendation | Reason |
|---|---|---|
| Q8: `GovernedIntentHandoff.state` on POST | Reject `state != "submitted"` with `422 VALIDATION_FAILED` plus `details.trading_code = "HANDOFF_VALIDATION_FAILED"`. | Silent override hides client bugs and weakens auditability. |
| Q9: ETag derivation | Add `ETag` on event/intent detail GETs, derived from SHA-256 of stable JSON fields plus a last-modified timestamp; add CORS allow/expose entries. | Satisfies required `If-Match` mutation headers without inventing a body field. |
| Q10: requested stage / handoff type / target queue consistency | Enforce the mapping from Followup-6 and reject mismatches with `422`. | The schema allows enum combinations that are syntactically valid but semantically inconsistent. |

## Additional parent acceptance checks

These supplement prior packets.

| Check | Expected result |
|---|---|
| CORS allows `If-Match` | Browser preflight for Trading Room POST with `If-Match` succeeds; `_CORS_ALLOW_HEADERS` contains `If-Match`. |
| CORS exposes `ETag` | Frontend can read `ETag` from decision-event and intent detail GET responses; `_CORS_EXPOSE_HEADERS` contains `ETag`. |
| Detail GETs emit ETags | `GET /decision-events/{id}` and `GET /trading-intents/{id}` include deterministic `ETag` headers. |
| `If-Match` mismatch returns precondition failure | Mutation with stale `If-Match` returns `412` and an error body that frontend can map to refresh-required. |
| Viewer cannot mutate | Viewer/read-only role can GET Trading Room surfaces but receives `403` for decisions, handoffs, and withdrawals. |
| Write role can mutate | Operator/approver/admin/reviewer roles can submit valid writes, subject to state and schema checks. |
| Runtime error codes are valid | Routes either use existing `ErrorCode` enum values or parent PR explicitly extends the enum; no raw unsupported enum values are passed to `_bff_error`. |
| Trading-specific subcodes survive | Error details include a machine-readable Trading Room subcode when using generic `ErrorCode` values. |
| Read-store methods exist | Trading Room router calls `ReadSurfaceStore` methods for aggregate/events/intents/handoffs; it does not read or write ad hoc files. |
| Command/object typing is intentional | Mutation command logging has reviewed command and target object types; no unreviewed string literals are hidden in route handlers. |
| SSE channel registered | `trading_room` exists in `SSE_CHANNEL_CATALOG`; stream route uses `_handle_sse_stream(..., channel="trading_room")`. |
| SSE resync routes registered | Stream response includes `X-SSE-Resync-Routes` for Trading Room GET routes. |
| Replay window expectation corrected | Tests assert `X-SSE-Replay-Window-Events == "500"` unless parent intentionally changes `_MAX_EVENTS`. |
| SSE auth is explicit | Frontend stream client uses either cookie/session auth for native `EventSource` or a fetch-based SSE client that can attach `Authorization`. |

## Reviewer handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only support packet and task-scoped metadata are in scope; no canonical docs, schemas, OpenAPI, BFF runtime, or frontend source changed. |
| CORS finding | `_CORS_ALLOW_HEADERS` lacks `If-Match`; `_CORS_EXPOSE_HEADERS` lacks `ETag`. |
| Error-code finding | `_bff_error` takes `ErrorCode`; `models.py` lacks Trading Room-specific `ErrorCode` values. |
| Write-role finding | `_require_operator_role` exists in `main.py`, but `create_agora_router` does not inject it into `create_trading_room_router`. |
| Store/model finding | `ReadSurfaceStore._LOCAL_DATA_KEYS`, `CommandType`, and `ObjectType` do not currently define Trading Room-specific surfaces. |
| SSE correction | `_MAX_EVENTS` is `500`; `SSE_CHANNEL_CATALOG` lacks `trading_room`; `_SSE_RESYNC_ROUTES` lacks a `trading_room` entry. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md \
  REVIEW_NOTES_ZH="Followup-7 BFF/frontend handoff packet approved: documents browser/CORS blocker for OpenAPI-required If-Match and ETag flow; clarifies runtime _bff_error/ErrorCode allowlist gap versus packet-specific TRADING_* subcodes; identifies missing write-role injection for Trading Room mutation routes; identifies missing ReadSurfaceStore datasets/methods and CommandType/ObjectType entries for Trading Room events/intents/handoffs; corrects SSE replay window to 500 events and adds trading_room resync-route guidance; calls out native EventSource auth limitation. Support-only material; no canonical truth, OpenAPI, schema, runtime, registry/governance, or frontend code changed." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Support-only AG-BE-TR-001 BFF/frontend handoff followup-7 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

## Validation run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7

git merge --ff-only origin/dev
# Fast-forwarded from 00050d4f to 7b18c6c1 before packet edits.

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
# source: active; status: in_progress; owner: Codex; reviewer: Claude2

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001
# source: active; status: todo; owner: Claude2; reviewer: Codex

python3 -c 'import ast, pathlib; p=pathlib.Path("services/control-plane/bff/main.py"); tree=ast.parse(p.read_text()); vals={}; 
for n in tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name) and t.id in {"_CORS_ALLOW_HEADERS","_CORS_EXPOSE_HEADERS","SSE_CHANNEL_CATALOG"}:
                vals[t.id]=ast.literal_eval(n.value)
print("has If-Match allow", "If-Match" in vals.get("_CORS_ALLOW_HEADERS", []))
print("has ETag expose", "ETag" in vals.get("_CORS_EXPOSE_HEADERS", []))
print("has trading_room channel", "trading_room" in vals.get("SSE_CHANNEL_CATALOG", ()))'
# has If-Match allow False
# has ETag expose False
# has trading_room channel False

python3 -c 'import yaml; d=yaml.safe_load(open("services/control-plane/openapi/agora_v1_3.openapi.yaml")); paths=d["paths"]; keys=["/bff/agora/trading-room/decision-events/{decision_event_id}/decisions","/bff/agora/trading-intents/{intent_id}/handoffs","/bff/agora/trading-intents/{intent_id}/withdraw"]; 
for k in keys:
    op=paths[k]["post"]
    print(k, [p.get("$ref", p.get("name")) for p in op.get("parameters", [])], list(op.get("responses", {}).keys()), "requestBody" in op)'
# decisions: DecisionEventId, IfMatch, IdempotencyKey, XRequestId; responses ['201']; requestBody True
# handoffs: IntentId, IfMatch, IdempotencyKey, XRequestId; responses ['202']; requestBody True
# withdraw: IntentId, IfMatch, IdempotencyKey, XRequestId; responses ['200']; requestBody False

rg -n "def create_trading_room_router|require_operator_role|SSE_CHANNEL_CATALOG|_MAX_EVENTS|_CORS_ALLOW_HEADERS|_CORS_EXPOSE_HEADERS" \
  services/control-plane/bff/main.py \
  services/control-plane/bff/agora/router.py \
  services/control-plane/bff/agora/trading_room/router.py
# Confirms empty Trading Room router, no write-role injection, _MAX_EVENTS=500, no trading_room SSE catalog entry, and no If-Match/ETag CORS entries.
```
