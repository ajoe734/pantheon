# AG-BE-TR-002 BFF and Frontend Handoff Packet — Followup 6

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-002` — Governed TradingIntent / handoff |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Codex` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` (done, archived 2026-06-22T07:19:03Z) |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI, JSON schemas,
BFF runtime, registry/governance implementation, or frontend code. The parent owner (Codex) decides
whether and how to absorb this material into the main implementation.

---

## Cumulative Packet Scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF` (done, PR #2142) | BFF query gap matrix (10 gaps), operator journeys A–I, frontend `tradingRoom.ts` method signatures, backend acceptance checks, 7 open design notes, stage→queue routing table, `TradingIntent` vs `GovernedIntentHandoff` schema distinction. |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done, PR #2149) | Schema-derived corrections: `target_queue` derivation, `converted` state, `action_proposal` field constraints, management-plane-only fields, `additionalProperties: false` implication, corrected TypeScript interfaces, idempotency implementation pattern, backend module structure guidance, acceptance check addendum. Opened Q1–Q4. |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (done, PR #2150) | Q1–Q4 resolution: `IdempotencyRecord` + `CommandStore` integration pattern (Q1), idempotency TTL and durability boundary (Q2), `required_gate_refs` population policy (Q3), `DetailEnvelope` concrete shape (Q4). `DetailEnvelope` TypeScript type, `allowedActions` mapping, `CommandStore.get_command_by_idempotency_key` lookup pattern, BFF test skeleton supplement, operator journey J. |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` (done, archived 2026-06-21T22:09:41Z) | Q5–Q8 resolution: `CommandType` and `ObjectType` enum gaps confirmed and remediated (Q5/Q6), `ReadSurfaceStore` `trading_intents` dataset and method gaps identified with recommended additions (Q7), Management-plane-to-BFF state-push gap confirmed as unimplemented with interim guidance (Q8). Test skeleton correction (`update_command_result` → `update_status`). D10 error-code canonical mapping. Updated `_seeded_client` test pattern using `_ensure_local_overlay_records`. |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` (done, archived 2026-06-22T07:19:03Z) | Q9–Q10 resolution: stage-sequence lock (Q9), initial handoff state draft vs submitted (Q10). New gaps Q11–Q14: ETag generation for `TradingIntent` GET response and `If-Match` validation (Q11), `requested_by` server-side population from BFF identity (Q12), Pydantic body model gap for `GovernedIntentHandoffBody` (Q13), `IdempotencyRecord.reserve()` parameter conventions for trading room commands (Q14). |
| **This packet (FOLLOWUP-6)** | Q15–Q16 resolution: withdraw semantics for multiple handoffs (Q15), `X-Request-Id` required enforcement (Q16). Major new finding: `router.py` is no longer a placeholder — contains active route stubs for all Trading Room and TradingIntent routes. New implementation gaps Q17–Q22 identified from reading the current code. |

---

## Current State Observed (2026-06-22)

| Surface | Observed state | Change since FOLLOWUP-5 |
|---|---|---|
| `AG-BE-TR-002` | **`in_progress`**; owner `Codex`, reviewer `Claude2`. | **Changed from `todo` to `in_progress`**. Work has started. |
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. Still gated on `AG-BE-CP-001` (blocked). |
| `services/control-plane/bff/agora/trading_room/router.py` | **No longer a placeholder.** Contains active route stubs for `GET /bff/agora/trading-room`, `GET /bff/agora/trading-room/strategies/{strategy_id}`, `GET /bff/agora/trading-room/decision-events`, `GET /bff/agora/trading-room/decision-events/{decision_event_id}`, `POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions`, `GET /bff/agora/trading-room/stream`, `GET /bff/agora/trading-intents/{intent_id}`, `POST /bff/agora/trading-intents/{intent_id}/handoffs`, `POST /bff/agora/trading-intents/{intent_id}/withdraw`. Pydantic models for `TradingDecisionEvent`, `TradingRoomAggregate`, `TraderDecisionRequest`, `GovernedIntentHandoffRequest`, and supporting types are defined. | **Major change from FOLLOWUP-5**: was empty `APIRouter` placeholder. |
| `services/control-plane/bff/agora/trading_room/store.py` | **New file.** `TradingRoomStore` in-memory store: `_decision_events` and `_intents` dicts; `upsert_decision_event`, `get_decision_event`, `list_decision_events`, `record_trader_decision`, `upsert_intent`, `get_intent` methods. No `_handoffs` dict or handoff methods. | **New file** — not present in FOLLOWUP-5. |
| `services/control-plane/bff/agora/trading_room/test_trading_room.py` | **New file.** Unit tests for `TradingRoomStore`, Pydantic model alignment with v4 schemas, router smoke test, safety invariants, and pagination regression. | **New file** — not present in FOLLOWUP-5. |
| `services/control-plane/bff/models.py` `CommandType` | No `SUBMIT_GOVERNED_HANDOFF` or `WITHDRAW_TRADING_INTENT`. | Unchanged. Additions still needed per FOLLOWUP-4 Q5. |
| `services/control-plane/bff/models.py` `ObjectType` | No `TRADING_INTENT` or `GOVERNED_HANDOFF`. | Unchanged. Additions still needed per FOLLOWUP-4 Q6. |
| `GovernedIntentHandoffRequest` Pydantic model | **Exists in `router.py`**, but accepts server-derived fields (`handoff_id`, `state`, `requested_by`, `target_queue`, `created_at`) directly from client body. No `handoff_type`/`requested_stage` cross-validator. | **Partially addressed** vs FOLLOWUP-5 Q13; see Q17 below. |
| `submit_trading_intent_handoff` handler | Validates `no_order_route_proof` and `intent_id` path/body match only. Does not validate `If-Match`, `Idempotency-Key`, or `X-Request-Id` headers. Does not store handoff record. Returns `"queued"` status. | Gaps remain: see Q17–Q22 below. |
| `get_trading_intent` handler | Returns a `DetailEnvelope`-shaped response. Does NOT set `ETag` response header. | Q11 ETag gap remains unimplemented. |
| `withdraw_trading_intent` handler | Updates `intent["state"] = "withdrawn"` if intent exists. Does not look at handoffs. Does not return 404 if intent is not found (silently returns success). | Q15 partly informs needed behavior; Q22 is a new gap. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Resolved Open Questions from FOLLOWUP-5

### Q15 — Withdraw semantics: multiple handoffs in different states

**Context (from FOLLOWUP-5):** When `POST .../withdraw` is called and the intent has multiple handoffs (one `submitted`, one `rejected`), should the BFF withdraw only the active handoff, or mark the intent itself as withdrawn? FOLLOWUP-5 stated a default: "Withdraw the most recent non-terminal handoff (state `submitted` or `accepted`). If no such handoff exists, withdraw the intent record itself."

**Resolution confirmed and refined:**

The default from FOLLOWUP-5 is correct. The implementation should follow this sequence:

1. **Fetch the intent record.** If not found → `404 RESOURCE_NOT_FOUND`.
2. **Guard: intent already terminal.** If `intent.state in ("withdrawn", "expired")` → `409 OPERATION_NOT_ALLOWED` with `details.reason = "TRADING_INTENT_ALREADY_WITHDRAWN"`.
3. **Find the most recent non-terminal handoff** (state `"submitted"` or `"accepted"`). If such a handoff exists:
   - Set `handoff.state = "withdrawn"`.
   - Set `handoff.withdrawn_by = scope.user_id` and `handoff.withdrawn_at = utc_now()`.
4. **Mark the intent record itself as `"withdrawn"`** regardless of step 3 (intent-level withdrawal takes priority).
5. **Idempotency replay:** If a duplicate `Idempotency-Key` is detected before any mutation, replay the prior response without modifying any records.
6. **Return `200 CommandResponse`** with `intent_id`, `state: "withdrawn"`, `withdrawn_at`, and the `handoff_id` of the withdrawn handoff if one was found.

**Why intent-level withdrawal takes priority over handoff-level:**

A `TradingIntent` expresses the operator's complete intent to act on a strategy. Withdrawing the intent means the operator no longer intends to proceed with *any* governance path for that intent. Withdrawing only a handoff while leaving the intent open would allow a subsequent `POST .../handoffs` to resubmit. Since the operator's intent is `withdraw`, the intent itself must be closed. Both the handoff and the intent record carry `"withdrawn"` state to preserve the negative/preference evidence.

**Required `TradingRoomStore` additions for Q15:**

The current `TradingRoomStore` has no `_handoffs` dict. To implement Q15, the following must be added:

```python
class TradingRoomStore:
    def __init__(self) -> None:
        self._decision_events: Dict[str, Dict[str, Any]] = {}
        self._intents: Dict[str, Dict[str, Any]] = {}
        self._trader_decisions: Dict[str, List[Dict[str, Any]]] = {}
        self._handoffs: Dict[str, Dict[str, Any]] = {}          # new
        self._handoffs_by_intent: Dict[str, List[str]] = {}     # new: intent_id → [handoff_id, ...]

    def upsert_handoff(self, handoff: Dict[str, Any]) -> Dict[str, Any]:
        """Store a GovernedIntentHandoff record."""
        proof = handoff.get("no_order_route_proof")
        if proof != "agora_request_only_no_order_route":
            raise ValueError(
                f"D1 safety invariant: handoff no_order_route_proof must be "
                f"'agora_request_only_no_order_route', got {proof!r}"
            )
        handoff_id = handoff["handoff_id"]
        intent_id = handoff["intent_id"]
        self._handoffs[handoff_id] = handoff
        if intent_id not in self._handoffs_by_intent:
            self._handoffs_by_intent[intent_id] = []
        if handoff_id not in self._handoffs_by_intent[intent_id]:
            self._handoffs_by_intent[intent_id].append(handoff_id)
        return handoff

    def get_governed_intent_handoffs_for_intent(
        self, intent_id: str
    ) -> List[Dict[str, Any]]:
        """Return all GovernedIntentHandoff records for the given intent, ordered by created_at."""
        ids = self._handoffs_by_intent.get(intent_id, [])
        handoffs = [self._handoffs[h] for h in ids if h in self._handoffs]
        handoffs.sort(key=lambda h: h.get("created_at", ""))
        return handoffs

    def get_latest_active_handoff(self, intent_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recent non-terminal GovernedIntentHandoff for the intent."""
        handoffs = self.get_governed_intent_handoffs_for_intent(intent_id)
        active_states = {"submitted", "accepted"}
        # Return last in chronological order that is non-terminal
        for h in reversed(handoffs):
            if h.get("state") in active_states:
                return h
        return None
```

**Acceptance checks for Q15:**

| Check | Expected result |
|---|---|
| `POST .../withdraw` on non-existent intent → `404` | Missing intent returns `404 RESOURCE_NOT_FOUND`. |
| `POST .../withdraw` on already-withdrawn intent → `409` | Intent with `state: "withdrawn"` returns `409 OPERATION_NOT_ALLOWED` with `details.reason = "TRADING_INTENT_ALREADY_WITHDRAWN"`. |
| `POST .../withdraw` with active handoff | Most recent `"submitted"` or `"accepted"` handoff is marked `"withdrawn"`. Intent record also set to `"withdrawn"`. |
| `POST .../withdraw` with no active handoff | Only intent record is marked `"withdrawn"`. Response includes `handoff_id: null`. |
| `POST .../withdraw` idempotency | Same `Idempotency-Key` returns the prior response; no duplicate state mutation. |
| Withdrawal is non-destructive | Withdrawn intent and handoff records remain in the store with `state: "withdrawn"`; they are not deleted. |

---

### Q16 — `X-Request-Id` required enforcement on write routes

**Resolution confirmed:** The BFF must enforce `X-Request-Id` as a **required** header on `POST /bff/agora/trading-intents/{intent_id}/handoffs` and `POST /bff/agora/trading-intents/{intent_id}/withdraw`.

Basis: `services/control-plane/openapi/agora_v1_3.openapi.yaml` defines the `XRequestId` component parameter (lines 92–96) as `required: true`, and both write routes reference it via `$ref: "#/components/parameters/XRequestId"` (lines 675 and 696 for the trading-intent routes).

**Current state:** Both handlers accept `x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id")` but never validate its presence. The `x_request_id` value is received but unused.

**Required enforcement pattern:**

```python
if not x_request_id:
    raise bff_error(
        400, ErrorCode.VALIDATION_FAILED,
        "X-Request-Id header required",
        "missing_request_id",
    )
```

**Where `x_request_id` is used downstream:**

Per Q14 (FOLLOWUP-5), `X-Request-Id` is used as the `trace_id` for `IdempotencyRecord.reserve()`:

```python
idempotency_record = IdempotencyRecord.reserve(
    idempotency_key=idempotency_key,
    operation_type="bff.SubmitGovernedHandoff",
    target_ref=f"trading_intent:{intent_id}",
    request_payload=idempotency_payload,
    trace_id=x_request_id,   # Required, not a fallback
)
```

**Enforcement order (consistent with `If-Match` and `Idempotency-Key`):**

For `POST .../handoffs`:
1. Check `X-Request-Id` → `400` if absent.
2. Check `Idempotency-Key` → `400` if absent.
3. Check `If-Match` → `400` if absent.
4. Fetch intent; check terminal state → `409` if withdrawn/expired.
5. Check `If-Match` against computed ETag → `409 RESOURCE_CONFLICT` if stale.
6. Check idempotency duplicate → replay if same key+hash; `409` if same key+different hash.
7. Validate Pydantic body (`GovernedIntentHandoffBody` per Q13).
8. Build handoff record (server-populate `handoff_id`, `state`, `requested_by`, `target_queue`, `created_at`).
9. Store handoff and return `202 CommandResponse`.

For `POST .../withdraw`:
1. Check `X-Request-Id` → `400` if absent.
2. Check `Idempotency-Key` → `400` if absent.
3. Check `If-Match` → `400` if absent.
4. Fetch intent → `404` if not found.
5. Check `If-Match` against computed ETag → `409 RESOURCE_CONFLICT` if stale.
6. Check idempotency duplicate → replay if match.
7. Guard against already-withdrawn intent → `409 OPERATION_NOT_ALLOWED`.
8. Mark handoff(s) and intent as `"withdrawn"`.
9. Return `200 CommandResponse`.

**Acceptance checks for Q16:**

| Check | Expected result |
|---|---|
| `POST .../handoffs` without `X-Request-Id` → `400` | Missing `X-Request-Id` returns `400 VALIDATION_FAILED` with reason `missing_request_id`. |
| `POST .../withdraw` without `X-Request-Id` → `400` | Missing `X-Request-Id` returns `400 VALIDATION_FAILED` with reason `missing_request_id`. |
| `x_request_id` used as `trace_id` | `IdempotencyRecord.trace_id` equals the `X-Request-Id` value from the request. |

---

## New Gaps Identified in FOLLOWUP-6

### Q17 — `GovernedIntentHandoffRequest` accepts server-derived fields from the client body

**Finding:** The current `GovernedIntentHandoffRequest` Pydantic model in `router.py` exposes these fields as client-settable inputs:

| Field | Problem | Correct source |
|---|---|---|
| `handoff_id` | Client-supplied UUID can be spoofed or reused. | Server generates via `str(uuid.uuid4())`. |
| `state` | Allows client to set `"accepted"` or `"rejected"` directly. Should always be `"submitted"` at creation (Q10). | Server always sets `"submitted"`. |
| `requested_by` | Allows impersonation (Q12). | Server populates from `scope.user_id`; `actor_type = "trader"`. |
| `target_queue` | Allows client to route to any queue. Must be derived server-side from `requested_stage` (Q2 FOLLOWUP-2). | Server derives: `shadow→shadow_research`, `paper→management_governance`, `canary/live→promotion_review`. |
| `created_at` | Allows backdating. | Server generates via `utc_now()`. |
| `intent_id` | Accepted in body alongside the path parameter; creates a mismatch risk (currently validated by an explicit body vs path check, but should not be in the client body at all). | Path parameter `intent_id` is authoritative. |
| `management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref` | These are management-plane-only fields. Including them in the client body creates a write surface for fields the BFF must never accept. | Forbidden client inputs per FOLLOWUP-2 / schema `management_handoff_ref` etc. |

**Required fix (consistent with FOLLOWUP-5 Q13 recommendation):**

Replace `GovernedIntentHandoffRequest` with two types:

1. **`GovernedIntentHandoffBody`** — the client-facing Pydantic model (only client-settable fields):
   - `spec_version: Literal["1.0"] = "1.0"`
   - `strategy_id: str` (min length 1)
   - `strategy_spec_registry_id: str` (min length 1)
   - `requested_stage: RequestedStage` (shadow | paper | canary | live)
   - `handoff_type: HandoffType` (shadow_start | paper_validation_request | promotion_review_request)
   - `evidence_refs: List[EvidenceRef]` (min 1)
   - `decision_event_id: Optional[str]`
   - `action_proposal: Optional[ActionProposal]` (with `non_binding=True` validator)
   - `required_gate_refs: Optional[List[str]]`
   - **Cross-validator:** `handoff_type` must match `requested_stage` (per Q13 FOLLOWUP-5).
   - **Does NOT include:** `handoff_id`, `intent_id`, `state`, `requested_by`, `target_queue`, `created_at`, `management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref`.

2. **`GovernedIntentHandoffRecord`** — the stored/returned dict built by the server (internal; not a Pydantic route boundary model):
   - All `GovernedIntentHandoffBody` fields plus server-populated: `handoff_id`, `intent_id`, `state: "submitted"`, `requested_by`, `target_queue`, `no_order_route_proof: "agora_request_only_no_order_route"`, `created_at`.

**Acceptance checks for Q17:**

| Check | Expected result |
|---|---|
| `handoff_id` from client ignored | Submitting a body with `handoff_id: "my-id"` does not persist that value; the server-generated UUID is used. |
| `state: "accepted"` from client rejected | A body with `state: "accepted"` fails Pydantic validation at the route boundary (field not present in `GovernedIntentHandoffBody`). |
| `management_handoff_ref` from client rejected | A body with `management_handoff_ref: "some-ref"` fails validation (field not present in `GovernedIntentHandoffBody`). |
| `target_queue` server-derived | Body with `target_queue: "shadow_research"` and `requested_stage: "live"` does not persist `"shadow_research"`; the stored record has `target_queue: "promotion_review"`. |
| Stored record conforms to v4 schema | Every stored `GovernedIntentHandoff` record passes `jsonschema.Draft7Validator(GOVERNED_HANDOFF_SCHEMA).validate(record)`. |

---

### Q18 — `submit_trading_intent_handoff` returns `"queued"` status instead of `"submitted"`

**Finding:** The current handler returns:

```python
return {
    "status": "queued",
    "data": {
        ...
        "state": "submitted",    # correct for the handoff record
    },
    ...
}
```

The outer `"status": "queued"` conflicts with the command-response envelope convention. Per FOLLOWUP-3 Q4 `DetailEnvelope` and command-response pattern, the envelope `"status"` should reflect the command result. The correct `CommandResponse` shape per v1.3 OpenAPI is:

```json
{
  "command_id": "<uuid>",
  "status": "submitted",
  "result": {
    "handoff_id": "<uuid>",
    "intent_id": "<intent_id>",
    "requested_stage": "shadow",
    "state": "submitted"
  },
  "meta": { ... }
}
```

**Required fix:** The envelope `"status"` should be `"submitted"` (or `"accepted"` for synchronous commands), not `"queued"`. The `CommandResponse` schema from `bff/models.py` should be used as the return type.

**Acceptance checks for Q18:**

| Check | Expected result |
|---|---|
| `POST .../handoffs` `202` response envelope `status` | Response `status` is `"submitted"`, not `"queued"`. |
| Response contains `command_id` | A server-generated `command_id` UUID is present in the `202` response. |

---

### Q19 — `TradingRoomStore` has no handoff storage; `GovernedIntentHandoff` records are never persisted

**Finding:** `TradingRoomStore` tracks `_decision_events` and `_intents` only. There is no `_handoffs` dict, no `upsert_handoff()`, and no `get_governed_intent_handoffs_for_intent()` method. Consequences:

- `submit_trading_intent_handoff` constructs a response from the request body but never writes a `GovernedIntentHandoff` record anywhere.
- `get_trading_intent` returns `"data": intent` but cannot include a `handoff_chain` (the `DetailEnvelope` from FOLLOWUP-3 Q4 requires a `handoffs` array in the response).
- `withdraw_trading_intent` cannot find or update active handoffs for the intent.
- Idempotency replay via `command_store.get_command_by_idempotency_key()` (FOLLOWUP-5 Q14) has no backing store.

**Required additions to `TradingRoomStore`:** See the `upsert_handoff`, `get_governed_intent_handoffs_for_intent`, and `get_latest_active_handoff` methods listed under Q15 above.

**Additional store requirement — idempotency tracking:**

The `TradingRoomStore` should also track submitted command records for idempotency replay. FOLLOWUP-4 Q1 recommended `CommandStore.get_command_by_idempotency_key()`. If the trading room module does not use the shared BFF `CommandStore` (which is the pattern established for other routes via `main.py`), it needs its own equivalent. The choice between shared `CommandStore` and a module-local dict must be made explicit.

**Recommendation:** Use the shared `CommandStore` from `services/control-plane/bff/command_store.py` (consistent with the existing BFF pattern). Do not add a second idempotency dict to `TradingRoomStore`. The `TradingRoomStore` is for domain records; `CommandStore` is for command/idempotency lifecycle.

**Acceptance checks for Q19:**

| Check | Expected result |
|---|---|
| `submit_trading_intent_handoff` stores handoff record | After a successful `POST .../handoffs`, `store.get_governed_intent_handoffs_for_intent(intent_id)` returns the stored record. |
| `get_trading_intent` includes `handoffs` array | `GET .../trading-intents/{intent_id}` response includes a `handoffs` array (may be empty) in `data` or at the envelope level. |
| Idempotency check uses shared `CommandStore` | The module imports or receives `CommandStore` and calls `get_command_by_idempotency_key()` — not a local dict. |

---

### Q20 — Required headers not enforced: `If-Match`, `Idempotency-Key`, `X-Request-Id`

**Finding:** All three headers are declared as `Optional[str] = Header(default=None, alias="...")` in both `submit_trading_intent_handoff` and `withdraw_trading_intent`. None are validated for presence before use. Per v1.3 OpenAPI, all three are `required: true` on both routes (confirmed: `IfMatch`, `IdempotencyKey`, and `XRequestId` parameters all have `required: true`).

**Current behavior:**
- A request missing `If-Match` is accepted and processed.
- A request missing `Idempotency-Key` is accepted; no idempotency check occurs.
- A request missing `X-Request-Id` is accepted; the `x_request_id` variable is `None` but unused.

**Required enforcement:** See enforcement order listed under Q16 above (validated in order: `X-Request-Id`, then `Idempotency-Key`, then `If-Match`).

**Change the parameter declarations to retain the alias but enforce presence:**

```python
# Do NOT change to `required: true` via FastAPI's Header(default=...) alone;
# validation must be explicit because FastAPI does not raise 400 on a missing
# Optional header — it silently passes None.

if_match: Optional[str] = Header(default=None, alias="If-Match")
idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")
x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id")
# ... then immediately:
if not x_request_id:
    raise bff_error(400, ErrorCode.VALIDATION_FAILED, "X-Request-Id header required", "missing_request_id")
if not idempotency_key:
    raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Idempotency-Key header required", "missing_idempotency_key")
if not if_match:
    raise bff_error(400, ErrorCode.VALIDATION_FAILED, "If-Match header required", "missing_if_match")
```

**Acceptance checks for Q20:**

| Check | Expected result |
|---|---|
| `POST .../handoffs` without `If-Match` → `400` | `400 VALIDATION_FAILED`, reason `missing_if_match`. |
| `POST .../handoffs` without `Idempotency-Key` → `400` | `400 VALIDATION_FAILED`, reason `missing_idempotency_key`. |
| `POST .../handoffs` without `X-Request-Id` → `400` | `400 VALIDATION_FAILED`, reason `missing_request_id`. |
| Same three checks for `POST .../withdraw` | Same `400` responses for each missing header. |

---

### Q21 — `GET /bff/agora/trading-intents/{intent_id}` does not set `ETag` response header

**Finding:** The `get_trading_intent` handler returns a JSON response but does not call `response.headers["ETag"] = etag`. No `Response` parameter is declared in the handler signature. The `ETag` header is required for clients to populate the `If-Match` header on subsequent write requests.

**Current handler signature:**

```python
@router.get("/bff/agora/trading-intents/{intent_id}")
def get_trading_intent(
    intent_id: str,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
```

**Required fix (per FOLLOWUP-5 Q11):**

```python
import hashlib, json

def _make_intent_etag(intent_id: str, intent_record: dict) -> str:
    content_bytes = json.dumps(intent_record, sort_keys=True, default=str).encode()
    content_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
    return f'"intent:{intent_id}:{content_hash}"'

@router.get("/bff/agora/trading-intents/{intent_id}")
def get_trading_intent(
    intent_id: str,
    response: Response,                    # Add this parameter
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    identity = extract_identity(authorization)
    require_read_role(identity)

    intent = store.get_intent(intent_id)
    if intent is None:
        raise bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")

    etag = _make_intent_etag(intent_id, intent)
    response.headers["ETag"] = etag                # Set ETag header

    handoffs = store.get_governed_intent_handoffs_for_intent(intent_id)
    # Build DetailEnvelope with handoffs included
    ...
```

**Acceptance checks for Q21:**

| Check | Expected result |
|---|---|
| `GET .../trading-intents/{intent_id}` includes `ETag` header | Response includes `ETag: "intent:{intent_id}:{hash}"` for every non-404 response. |
| ETag is stable for unchanged record | Two successive GETs without intervening writes return the same `ETag` value. |
| ETag changes after withdrawal | After `POST .../withdraw` updates the intent record, a subsequent GET returns a different `ETag`. |
| `POST .../handoffs` stale `If-Match` rejects | `If-Match` value from before a state change is rejected with `409 RESOURCE_CONFLICT` including `current_etag`. |

---

### Q22 — `withdraw_trading_intent` silently succeeds when intent is not found

**Finding:** Current implementation:

```python
intent = store.get_intent(intent_id)
if intent is not None:
    intent["state"] = "withdrawn"

return {
    "status": "completed",
    "data": {
        "intent_id": intent_id,
        "state": "withdrawn",
        ...
    },
    ...
}
```

If `store.get_intent(intent_id)` returns `None` (intent does not exist), the handler silently returns `200 completed` with `state: "withdrawn"` — a false success. This violates the standard pattern where write operations on non-existent resources return `404`.

**Required fix:**

```python
intent = store.get_intent(intent_id)
if intent is None:
    raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND,
                    f"TradingIntent '{intent_id}' not found.", "intent_not_found")
```

This must be added before any state mutation and before the idempotency check (idempotency replay is only valid for a previously successful write on an existing intent; a 404 is not idempotency-replayable).

**Acceptance checks for Q22:**

| Check | Expected result |
|---|---|
| `POST .../withdraw` on non-existent intent → `404` | `404 RESOURCE_NOT_FOUND` with reason `intent_not_found`. |
| `POST .../withdraw` on existing intent → `200` | `200 CommandResponse` with `state: "withdrawn"`. |

---

## Acceptance Check Addendum (supplements all prior packets)

| Check | Expected result |
|---|---|
| Q15: Withdraw 404 | `POST .../withdraw` on missing intent → `404 RESOURCE_NOT_FOUND`. |
| Q15: Withdraw already-withdrawn | `POST .../withdraw` on withdrawn intent → `409 OPERATION_NOT_ALLOWED`, reason `TRADING_INTENT_ALREADY_WITHDRAWN`. |
| Q15: Withdraw with active handoff | Most recent `submitted`/`accepted` handoff marked `"withdrawn"`; intent also marked `"withdrawn"`. |
| Q15: Idempotency on withdraw | Same `Idempotency-Key` replay returns `200` with original response; no second mutation. |
| Q16: `X-Request-Id` required | `POST .../handoffs` and `POST .../withdraw` without `X-Request-Id` → `400 VALIDATION_FAILED`. |
| Q16: `x_request_id` as `trace_id` | `IdempotencyRecord.trace_id` equals the `X-Request-Id` request value. |
| Q17: Server-derived fields not accepted from client | `GovernedIntentHandoffBody` model rejects `handoff_id`, `state`, `requested_by`, `target_queue`, `created_at`, `management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref` as client-supplied fields. |
| Q17: Stored record conforms to v4 schema | `jsonschema.Draft7Validator(GOVERNED_HANDOFF_SCHEMA).validate(stored_record)` passes. |
| Q18: `202` envelope `status` is `"submitted"` | Not `"queued"`. Response includes `command_id`. |
| Q19: Handoff stored after submit | `store.get_governed_intent_handoffs_for_intent(intent_id)` returns the record after a successful `POST .../handoffs`. |
| Q19: Shared `CommandStore` used | Module does not introduce a second idempotency store; uses the shared BFF `CommandStore`. |
| Q20: Missing `If-Match` → `400` | Both write routes reject absent `If-Match` with `400 VALIDATION_FAILED`. |
| Q20: Missing `Idempotency-Key` → `400` | Both write routes reject absent `Idempotency-Key` with `400 VALIDATION_FAILED`. |
| Q21: `ETag` header in GET response | `GET .../trading-intents/{intent_id}` includes `ETag` header; stable across unchanged reads. |
| Q21: Stale `If-Match` rejected | `POST .../handoffs` with outdated `If-Match` returns `409 RESOURCE_CONFLICT` with `current_etag`. |
| Q22: Withdraw on missing intent → `404` | `POST .../withdraw` on non-existent `intent_id` returns `404`, not `200`. |

---

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed. |
| Q15 resolution accuracy | Q15 default confirmed: withdraw most recent non-terminal handoff + mark intent withdrawn. `TradingRoomStore` additions (`upsert_handoff`, `get_governed_intent_handoffs_for_intent`, `get_latest_active_handoff`) correctly specified. Idempotency replay and 409-already-withdrawn guard are correctly sequenced. |
| Q16 resolution accuracy | `X-Request-Id` marked `required: true` in v1.3 OpenAPI `XRequestId` component confirmed. Enforcement order (X-Request-Id → Idempotency-Key → If-Match → intent fetch → ETag check → idempotency duplicate → Pydantic → store) is consistent with prior packets and Q20 addendum. `x_request_id` correctly designated as `trace_id` for `IdempotencyRecord.reserve()`. |
| Q17 accuracy | `GovernedIntentHandoffRequest` model in current `router.py` confirmed to accept `handoff_id`, `state`, `requested_by`, `target_queue`, `created_at`, `management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref` from client. These are all correctly identified as server-derived fields. Recommended split into `GovernedIntentHandoffBody` (client-facing) + `GovernedIntentHandoffRecord` (server-built dict) is consistent with FOLLOWUP-5 Q13. |
| Q18 accuracy | Current handler returns `"status": "queued"` confirmed from source. `"submitted"` is correct per Q10 FOLLOWUP-5. `CommandResponse` envelope shape is consistent with `bff/models.py`. |
| Q19 accuracy | `TradingRoomStore` confirmed to have no `_handoffs` dict or related methods. `CommandStore` recommendation is consistent with existing BFF pattern from `main.py`. |
| Q20 accuracy | `If-Match`, `Idempotency-Key`, `X-Request-Id` all confirmed as `Optional[str] = Header(default=None, ...)` with no explicit presence validation in current code. All three are `required: true` in v1.3 OpenAPI. |
| Q21 accuracy | `get_trading_intent` handler confirmed to have no `Response` parameter and no `ETag` header set. `_make_intent_etag` pattern is consistent with FOLLOWUP-5 Q11 recommendation and adapted from `dashboard/router.py` `_make_etag` pattern. |
| Q22 accuracy | `withdraw_trading_intent` confirmed to return `200 completed` when `store.get_intent()` returns `None`. `404` guard required before mutation is correctly identified. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |
| Status accuracy | `AG-BE-TR-002` is `in_progress` (owner Codex); `AG-BE-TR-001` is `todo` (blocked on `AG-BE-CP-001`); FOLLOWUP-5 is `done` (archived 2026-06-22T07:19:03Z). |

**Recommended reviewer approval command:**

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md \
  REVIEW_NOTES_ZH="Followup-6 handoff packet approved: resolves Q15 (withdraw semantics: most recent non-terminal handoff + intent both set to withdrawn; 404 on missing intent; 409 on already-withdrawn; TradingRoomStore additions: upsert_handoff/get_governed_intent_handoffs_for_intent/get_latest_active_handoff), Q16 (X-Request-Id required: enforce 400 VALIDATION_FAILED; use as trace_id for IdempotencyRecord). New gaps Q17 (GovernedIntentHandoffRequest model accepts server-derived fields from client; needs GovernedIntentHandoffBody split), Q18 (submit returns status queued vs correct submitted), Q19 (TradingRoomStore has no handoff storage; use shared CommandStore for idempotency), Q20 (If-Match/Idempotency-Key/X-Request-Id not enforced as required; need explicit validation guards), Q21 (GET trading-intents missing ETag header; needs Response param and _make_intent_etag), Q22 (withdraw silently succeeds on missing intent; needs 404 guard). No canonical truth, schemas, OpenAPI, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Followup-6 BFF/frontend handoff packet approved for parent owner absorption."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Describe the factual error, scope issue, or missing context requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6

git status --short
# A  support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
# ?? .orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff_followup_6.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
# status: in_progress, owner: Claude, reviewer: Codex

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002
# status: in_progress, owner: Codex, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
# source: archive; terminal_status: done; archived_at 2026-06-22T07:19:03Z

# Confirmed router.py is no longer a placeholder:
# wc -l services/control-plane/bff/agora/trading_room/router.py
# → 420+ lines; all nine routes implemented

# Confirmed TradingRoomStore has no _handoffs dict:
# grep "_handoffs\|upsert_handoff\|get_governed" services/control-plane/bff/agora/trading_room/store.py
# (no output)

# Confirmed GovernedIntentHandoffRequest accepts server-derived fields:
# grep "handoff_id\|state:\|requested_by\|target_queue\|created_at" \
#   services/control-plane/bff/agora/trading_room/router.py | head -20
# → all present in GovernedIntentHandoffRequest model definition

# Confirmed no ETag in get_trading_intent:
# grep -n "ETag\|response.headers" services/control-plane/bff/agora/trading_room/router.py
# (no output — no ETag logic)

# Confirmed submit_trading_intent_handoff returns "queued":
# grep '"queued"' services/control-plane/bff/agora/trading_room/router.py
# → "status": "queued"

# Confirmed withdraw silently proceeds on missing intent:
# grep -A 5 "get_intent" services/control-plane/bff/agora/trading_room/router.py | tail -10
# → if intent is not None: intent["state"] = "withdrawn" (no else/404)

# Confirmed X-Request-Id, If-Match, Idempotency-Key not validated as required:
# grep "if not if_match\|if not idempotency\|if not x_request" \
#   services/control-plane/bff/agora/trading_room/router.py
# (no output)

# Confirmed CommandType/ObjectType still missing trading-room entries:
# grep "SUBMIT_GOVERNED\|WITHDRAW_TRADING\|TRADING_INTENT\|GOVERNED_HANDOFF" \
#   services/control-plane/bff/models.py
# (no output)

# Confirmed XRequestId required: true in OpenAPI:
# grep -A 4 "XRequestId:" services/control-plane/openapi/agora_v1_3.openapi.yaml
# → name: X-Request-Id; required: true

# Confirmed trading-intents write routes reference XRequestId:
# grep -n "trading.intents.*handoffs\|XRequestId" \
#   services/control-plane/openapi/agora_v1_3.openapi.yaml
# → lines 664, 675 (handoffs), 688, 696 (withdraw) all include XRequestId ref
```
