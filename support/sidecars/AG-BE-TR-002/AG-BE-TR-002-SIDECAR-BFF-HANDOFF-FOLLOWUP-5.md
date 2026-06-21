# AG-BE-TR-002 BFF and Frontend Handoff Packet — Followup 5

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-002` — Governed TradingIntent / handoff |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` (done, archived 2026-06-21T22:09:41Z, reviewed by Claude2) |
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
| **This packet (FOLLOWUP-5)** | Q9–Q10 resolution: stage-sequence lock (Q9), initial handoff state draft vs submitted (Q10). New gaps Q11–Q14: ETag generation for `TradingIntent` GET response and `If-Match` validation (Q11), `requested_by` server-side population from BFF identity (Q12), Pydantic body model gap for `GovernedIntentHandoffBody` (Q13), `IdempotencyRecord.reserve()` parameter conventions for trading room commands (Q14). |

---

## Current State Observed (2026-06-21)

| Surface | Observed state | Change since FOLLOWUP-4 |
|---|---|---|
| `AG-BE-TR-002` | `todo`; owner `Codex`, reviewer `Claude2`. | Unchanged. |
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. Still gated on `AG-BE-CP-001` (blocked). |
| `AG-BE-CP-001` | `blocked`. | Unchanged. |
| `services/control-plane/bff/models.py` `CommandType` | No `SUBMIT_GOVERNED_HANDOFF` or `WITHDRAW_TRADING_INTENT`. | Unchanged. Additions still needed per FOLLOWUP-4 Q5. |
| `services/control-plane/bff/models.py` `ObjectType` | No `TRADING_INTENT` or `GOVERNED_HANDOFF`. | Unchanged. Additions still needed per FOLLOWUP-4 Q6. |
| `services/control-plane/bff/read_store.py` | No `trading_intents` or `governed_intent_handoffs` keys. No `get_trading_intent()`, `list_trading_intents()`, `get_governed_intent_handoffs_for_intent()`, `upsert_trading_intent()`, or `upsert_governed_intent_handoff()` methods. | Unchanged. Additions still needed per FOLLOWUP-4 Q7. |
| `services/control-plane/bff/agora/trading_room/router.py` | Empty placeholder: `create_trading_room_router()` returns `APIRouter(tags=["agora-trading"])` with no routes. | Unchanged. Routes pending AG-BE-TR-001/TR-002 implementation. |
| `services/control-plane/bff/agora/router.py` | Imports `create_trading_room_router` (line 31) and includes it at line 174. The inclusion point is wired; the placeholder router produces no routes. | Unchanged — wiring correct, implementation pending. |
| `GovernedIntentHandoff` Pydantic model | No Pydantic model for this type exists anywhere in `services/control-plane/bff/`. | **New finding.** See Q13 below. |
| ETag for `TradingIntent` records | `GET /bff/agora/trading-intents/{intent_id}` is not implemented; no ETag-generation logic exists for intent records. | **New finding.** See Q11 below. |
| `requested_by` population | No server-side population logic for `requested_by` in `GovernedIntentHandoff`. | **New finding.** See Q12 below. |
| `IdempotencyRecord.reserve()` conventions | No trading-room-specific `operation_type` or `target_ref` values documented. | **New finding.** See Q14 below. |

---

## Resolved Open Questions from FOLLOWUP-4

### Q9 — Stage-sequence lock: should the BFF enforce prior-stage completion?

**Resolution: The BFF must NOT enforce prior-stage completion. Stage-sequence ordering is a Management governance plane concern. The BFF's role is structural and schema validation only, plus terminal-state guard.**

Context from Q3 (FOLLOWUP-3): `required_gate_refs` is populated server-side by the BFF from the governance queue context — not from the client — and is forwarded to the Management governance plane with the queued command. The Management governance plane evaluates whether the gate references are satisfied.

**BFF-side checks that ARE required for stage-sequence:**

| Check | Where enforced | Basis |
|---|---|---|
| Intent is not in a terminal state (`withdrawn`, `expired`) | BFF, before accepting the POST body | Cannot submit to an intent that is already closed; this is a state-machine constraint. |
| `handoff_type` matches `requested_stage` | BFF, schema validation layer | `"shadow"` → `"shadow_start"`, `"paper"` → `"paper_validation_request"`, `"canary"`/`"live"` → `"promotion_review_request"`. Mismatch → `422`. |
| `no_order_route_proof` = `"agora_request_only_no_order_route"` | BFF, before storing | Safety gate; not a stage-sequence check. |

**BFF-side checks that are NOT required:**

- Verifying that a prior `shadow` handoff exists in `accepted` state before accepting `paper`.
- Verifying that Management governance approved the previous stage.
- Checking `required_gate_refs` content beyond populating the array from the server context.

**Documentation requirement:** The `POST /bff/agora/trading-intents/{intent_id}/handoffs` response `meta` should note that stage-sequence ordering (e.g., shadow → paper → canary → live progression) is enforced by Management governance, not by the BFF. An `APPROVAL_REQUIRED` error from the governance plane will be returned asynchronously via the handoff state update mechanism (Q8, FOLLOWUP-4) if the prior stage has not been completed.

**Suggested BFF intent terminal-state guard (pre-schema validation):**

```python
intent = read_store.get_trading_intent(intent_id)
if intent is None:
    raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND,
                    f"TradingIntent '{intent_id}' not found.", "intent_not_found")

intent_state = intent.get("state") or intent.get("status")
if intent_state in ("withdrawn", "expired"):
    raise HTTPException(
        status_code=409,
        detail=BffErrorEnvelope(
            error=BFFError(
                code=ErrorCode.OPERATION_NOT_ALLOWED,
                i18nKey="trading_intent.handoff_not_allowed",
                message="This intent is in a terminal state and cannot accept a new handoff.",
                retryable=False,
                userActionable=True,
                details=ErrorDetail(
                    reason="TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                    suggestion="Review the intent status and open a new intent if needed.",
                ),
            )
        ).model_dump(),
    )
```

---

### Q10 — Initial handoff state: `"draft"` or `"submitted"`?

**Resolution (confirmed): Always create `GovernedIntentHandoff` records in `"submitted"` state. `"draft"` is reserved for a future save-before-submit UI flow not currently in scope.**

Basis:
- `POST /bff/agora/trading-intents/{intent_id}/handoffs` is a submit operation, not a save operation.
- The `202` response code confirms the handoff was accepted for routing; the record state must match.
- A `"draft"` initial state would mislead the governance queue consumer into treating a routed request as still in preparation.

**State transition on `POST .../handoffs`:**
```
(record created) → state: "submitted"
(governance processes) → state: "accepted" | "rejected" | "converted" | "expired"  [Management plane write]
(operator withdraws) → state: "withdrawn"  [BFF write via POST .../withdraw]
```

The BFF never writes `"accepted"`, `"rejected"`, `"converted"`, or `"expired"` — those states are written by the Management governance plane (Q8 gap, FOLLOWUP-4).

---

## New Gaps Identified in FOLLOWUP-5

### Q11 — ETag generation for `GET /bff/agora/trading-intents/{intent_id}` and `If-Match` validation

**Finding: No ETag generation pattern exists for `TradingIntent` records. The v1.3 OpenAPI marks `If-Match` as `required: true` on both `POST .../handoffs` and `POST .../withdraw`. The `GET` endpoint must return an `ETag` header so clients can use its value in subsequent write requests.**

Confirmed:
- `services/control-plane/openapi/agora_v1_3.openapi.yaml` `IfMatch` parameter definition (line 82–86): `required: true`.
- `services/control-plane/bff/agora/dashboard/router.py` uses `_make_etag(recipe_id, version, content_sha256)` for dashboard recipes, where `content_sha256` is derived from `hashlib.sha256(json.dumps(data).encode()).hexdigest()`.
- `TradingIntent` schema (`trading_intent.schema.json` v1) has no `version` integer field. The ETag must be derived from the content hash of the stored record.

**Recommended ETag generation pattern for `TradingIntent`:**

```python
import hashlib
import json

def _make_intent_etag(intent_id: str, intent_record: dict) -> str:
    """Derive a stable ETag from the TradingIntent record content."""
    content_bytes = json.dumps(intent_record, sort_keys=True, default=str).encode()
    content_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
    return f'"intent:{intent_id}:{content_hash}"'
```

**`GET /bff/agora/trading-intents/{intent_id}` — ETag response:**

```python
@router.get("/bff/agora/trading-intents/{intent_id}")
def get_trading_intent(
    intent_id: str,
    response: Response,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    identity = extract_identity(authorization)
    require_read_role(identity)

    intent = read_store.get_trading_intent(intent_id)
    if intent is None:
        raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND,
                        f"TradingIntent '{intent_id}' not found.", "intent_not_found")

    etag = _make_intent_etag(intent_id, intent)
    response.headers["ETag"] = etag

    handoffs = read_store.get_governed_intent_handoffs_for_intent(intent_id)
    envelope = _build_detail_envelope(intent_id, intent, handoffs, etag, utc_now)
    return envelope
```

**`POST .../handoffs` — `If-Match` validation:**

```python
@router.post("/bff/agora/trading-intents/{intent_id}/handoffs", status_code=202)
def submit_governed_handoff(
    intent_id: str,
    body: GovernedIntentHandoffBody,
    response: Response,
    authorization: Optional[str] = Header(default=None),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> Dict[str, Any]:
    identity = extract_identity(authorization)
    require_read_role(identity)

    if not if_match:
        raise bff_error(400, ErrorCode.VALIDATION_FAILED,
                        "If-Match header required", "missing_if_match")
    if not idempotency_key:
        raise bff_error(400, ErrorCode.VALIDATION_FAILED,
                        "Idempotency-Key header required", "missing_idempotency_key")

    intent = read_store.get_trading_intent(intent_id)
    if intent is None:
        raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND,
                        f"TradingIntent '{intent_id}' not found.", "intent_not_found")

    current_etag = _make_intent_etag(intent_id, intent)
    if if_match != current_etag:
        raise bff_error(
            409, ErrorCode.RESOURCE_CONFLICT,
            "TradingIntent changed after the client snapshot.",
            "etag_mismatch",
            details_extra={
                "current_etag": current_etag,
                "latest_href": f"/bff/agora/trading-intents/{intent_id}",
            },
        )

    # ... intent terminal-state guard (Q9) ...
    # ... idempotency check ...
    # ... schema validation ...
    # ... stage routing ...
    # ... command submission ...
```

**Acceptance checks for Q11:**

| Check | Expected result |
|---|---|
| `GET .../trading-intents/{intent_id}` returns `ETag` header | Response includes `ETag: "intent:{intent_id}:{content_hash}"` header for every non-404 response. |
| ETag is stable for unchanged record | Two successive GETs without intervening writes return the same ETag value. |
| ETag changes after intent update | After a withdrawal or state update, the ETag computed from the updated record differs from the prior value. |
| `POST .../handoffs` without `If-Match` → `400` | Missing `If-Match` header returns `400 VALIDATION_FAILED` with reason `missing_if_match`. |
| `POST .../handoffs` with stale `If-Match` → `409` | Outdated ETag value returns `409 RESOURCE_CONFLICT` with `current_etag` in details. |

---

### Q12 — `requested_by` must be populated server-side from the authenticated identity

**Finding: `requested_by` is a required field in `governed_intent_handoff.schema.json` v4 (actor type `"trader"`, `"agora_servant"`, or `"institutional_persona"`). No server-side population logic for this field exists in the BFF. Accepting this field directly from the client body would allow impersonation.**

Confirmed from `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` definitions:
```json
"actor": {
  "type": "object",
  "required": ["actor_type", "actor_ref"],
  "properties": {
    "actor_type": { "enum": ["trader", "agora_servant", "institutional_persona"] },
    "actor_ref":  { "type": "string", "minLength": 1 },
    "session_id": { "type": "string" }
  }
}
```

For a human Trading Room operator, the correct mapping is:

| Field | Value | Source |
|---|---|---|
| `actor_type` | `"trader"` | Hard-coded for the Trading Room operator path. |
| `actor_ref` | `scope.user_id` | Resolved from `extract_identity(authorization)` + `resolve_agora_user_scope()`. |
| `session_id` | `scope.session_id` if present, else omit | Resolved from scope; optional. |

**Implementation pattern:**

```python
from ..identity.scope import resolve_agora_user_scope

# Inside the route handler, after extract_identity:
scope = resolve_agora_user_scope(identity)

requested_by = {
    "actor_type": "trader",
    "actor_ref": scope.user_id,
}
if getattr(scope, "session_id", None):
    requested_by["session_id"] = scope.session_id

# Build the handoff record dict, overriding any client-supplied requested_by:
handoff_record = {
    **body.model_dump(exclude_none=True),
    "handoff_id": str(uuid.uuid4()),
    "intent_id": intent_id,
    "spec_version": "1.0",
    "state": "submitted",            # Always "submitted" per Q10.
    "target_queue": _derive_target_queue(body.requested_stage),  # Server-derived, not from client.
    "requested_by": requested_by,    # Override client-supplied value.
    "created_at": utc_now(),
    "no_order_route_proof": "agora_request_only_no_order_route",  # Enforce literal; reject body value.
}
```

**Key security invariants:**
- `requested_by` must always be populated from `scope.user_id`, never from the client body.
- `target_queue` must be derived server-side from `requested_stage`; never accepted from the client body.
- `no_order_route_proof` must always be the literal string `"agora_request_only_no_order_route"` in the stored record, regardless of what the client sends (though the client body validation should also require this value).

**Acceptance checks for Q12:**

| Check | Expected result |
|---|---|
| `requested_by.actor_type` = `"trader"` | Every stored `GovernedIntentHandoff` record has `requested_by.actor_type = "trader"`. Never `"operator"` (different from strategy_workshop convention). |
| `requested_by.actor_ref` = authenticated user | Submitting a handoff body with a different `requested_by.actor_ref` does not persist that value; the server-derived `scope.user_id` is used instead. |
| `target_queue` is server-derived | A client body supplying `target_queue: "shadow_research"` with `requested_stage: "live"` is rejected or the `target_queue` is overridden to `"promotion_review"` by the server. |

---

### Q13 — Pydantic body model gap for `GovernedIntentHandoffBody`

**Finding: No Pydantic model for `GovernedIntentHandoff` exists in `services/control-plane/bff/`. Existing BFF routes use Pydantic for request body validation (e.g., `ResearchPlanCreateRequest` in `agora/research/router.py`). The owner must define a Pydantic body model in the trading room module.**

Confirmed from codebase survey:
- `services/control-plane/bff/agora/research/router.py` lines 35, 125: uses `BaseModel` + `Field` from `pydantic` for all request bodies.
- `jsonschema.Draft7Validator` is used only in **tests** to validate stored records against the JSON Schema file (e.g., `bff/tests/test_agora_identity_scope.py` lines 113–164).
- No production route uses `jsonschema.validate()` inline; schema conformance is enforced via Pydantic at the route boundary and via `jsonschema` in tests.

**Recommended Pydantic model (in `services/control-plane/bff/agora/trading_room/router.py` or a new `trading_room/models.py`):**

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from enum import Enum

class RequestedStage(str, Enum):
    SHADOW = "shadow"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"

class HandoffType(str, Enum):
    SHADOW_START = "shadow_start"
    PAPER_VALIDATION_REQUEST = "paper_validation_request"
    PROMOTION_REVIEW_REQUEST = "promotion_review_request"

class EvidenceRef(BaseModel):
    ref_type: str
    ref_id: str
    description: Optional[str] = None

class ActionProposal(BaseModel):
    action: str  # "enter" | "add" | "reduce" | "exit" | "review"
    non_binding: bool = True

    @field_validator("non_binding")
    @classmethod
    def must_be_non_binding(cls, v: bool) -> bool:
        if not v:
            raise ValueError("action_proposal.non_binding must be true")
        return v

class GovernedIntentHandoffBody(BaseModel):
    """Client-facing body for POST /bff/agora/trading-intents/{intent_id}/handoffs.

    Note: requested_by, target_queue, state, handoff_id, created_at, and
    no_order_route_proof are NOT accepted from the client — they are populated
    server-side before storing.
    """
    spec_version: str = "1.0"
    strategy_id: str = Field(min_length=1)
    strategy_spec_registry_id: str = Field(min_length=1)
    requested_stage: RequestedStage
    handoff_type: HandoffType
    evidence_refs: List[EvidenceRef] = Field(min_length=1)
    decision_event_id: Optional[str] = None
    action_proposal: Optional[ActionProposal] = None
    required_gate_refs: Optional[List[str]] = None

    @field_validator("handoff_type")
    @classmethod
    def handoff_type_must_match_stage(
        cls, v: HandoffType, info: Any
    ) -> HandoffType:
        stage = (info.data or {}).get("requested_stage")
        expected = {
            RequestedStage.SHADOW: HandoffType.SHADOW_START,
            RequestedStage.PAPER: HandoffType.PAPER_VALIDATION_REQUEST,
            RequestedStage.CANARY: HandoffType.PROMOTION_REVIEW_REQUEST,
            RequestedStage.LIVE: HandoffType.PROMOTION_REVIEW_REQUEST,
        }
        if stage and expected.get(stage) != v:
            raise ValueError(
                f"handoff_type '{v}' is not valid for requested_stage '{stage}'. "
                f"Expected '{expected.get(stage)}'."
            )
        return v
```

**Why `requested_by` is excluded from `GovernedIntentHandoffBody`:**
- The field is required in the JSON Schema (schema-level), but it is populated server-side (see Q12).
- Accepting it from the client would require the validator to either ignore or override it, which is confusing and creates an impersonation surface.
- The Pydantic model represents what the client sends; the stored record has additional server-populated fields.

**Test validation (using `jsonschema`):**

```python
import json
import os
import jsonschema

GOVERNED_HANDOFF_SCHEMA = json.loads(
    (Path(__file__).parent.parent / "specs" / "agora" / "v4"
     / "governed_intent_handoff.schema.json").read_text()
)

def test_stored_handoff_record_conforms_to_schema(seeded_client_fixture):
    """Verify that stored GovernedIntentHandoff records satisfy the v4 JSON Schema."""
    client, read_store = seeded_client_fixture
    resp = client.post(
        f"/bff/agora/trading-intents/{FIXTURE_INTENT_ID}/handoffs",
        json=VALID_SHADOW_HANDOFF_BODY,
        headers={
            "Authorization": "Bearer test",
            "If-Match": FIXTURE_ETAG,
            "Idempotency-Key": "test-key-001",
        },
    )
    assert resp.status_code == 202
    handoffs = read_store.get_governed_intent_handoffs_for_intent(FIXTURE_INTENT_ID)
    assert len(handoffs) == 1
    # Validate the stored record against the JSON Schema (not the Pydantic model).
    jsonschema.Draft7Validator(GOVERNED_HANDOFF_SCHEMA).validate(handoffs[0])
```

**Acceptance checks for Q13:**

| Check | Expected result |
|---|---|
| `GovernedIntentHandoffBody` Pydantic model exists | `from agora.trading_room.router import GovernedIntentHandoffBody` (or similar import) succeeds without error. |
| `handoff_type` / `requested_stage` validator fires | Submitting `requested_stage: "shadow"` with `handoff_type: "paper_validation_request"` returns `422`. |
| `action_proposal.non_binding: false` rejected | Body with `action_proposal: { "action": "enter", "non_binding": false }` returns `422`. |
| `evidence_refs` min length | Body with empty `evidence_refs: []` returns `422`. |
| Stored record passes `jsonschema.Draft7Validator` | Every stored `GovernedIntentHandoff` record passes `Draft7Validator(GOVERNED_HANDOFF_SCHEMA).validate(record)` in the test suite. |

---

### Q14 — `IdempotencyRecord.reserve()` parameter conventions for trading room commands

**Finding: No trading-room-specific `operation_type` or `target_ref` values have been documented. The existing pattern in `main.py` (line 1387–1393) and `services/foundation/idempotency.py` is clear, but the exact values for the trading room routes need to be specified to ensure idempotency records are queryable and consistent.**

Confirmed from `services/foundation/idempotency.py`:
- `IdempotencyRecord.reserve()` signature: `idempotency_key`, `operation_type`, `target_ref`, `request_payload`, `trace_id`.
- `operation_type` convention from `main.py` line 1389: `f"bff.{cmd.command.value}"` → e.g., `"bff.ApproveDeployment"`.
- `target_ref` convention from `main.py` line 1390: `authority_scope.target_ref` → a string referencing the command target.

**Recommended values for trading room commands:**

| Command | `operation_type` | `target_ref` | `request_payload` exclusions |
|---|---|---|---|
| `SUBMIT_GOVERNED_HANDOFF` | `"bff.SubmitGovernedHandoff"` | `f"trading_intent:{intent_id}"` | Exclude `requested_by` and `created_at` (server-derived; should not affect idempotency hash for duplicate detection). |
| `WITHDRAW_TRADING_INTENT` | `"bff.WithdrawTradingIntent"` | `f"trading_intent:{intent_id}"` | Exclude `actor` and `withdrawn_at` (server-derived). |

**Why exclude server-derived fields from `request_payload` for idempotency hashing:**

The `IdempotencyRecord.request_hash` (computed by `sha256_checksum(request_payload)`) is used to detect duplicate requests with the same `Idempotency-Key` but a different body. If the payload includes `requested_by` (which is server-derived from the session identity), two requests with the same client body but from different sessions would compute different hashes and be treated as non-duplicate, even though the Idempotency-Key matches. Excluding server-derived fields from the hash payload makes the idempotency check robust to session identity variance.

**Recommended `request_payload` construction:**

```python
# For submit_governed_handoff:
idempotency_payload = {
    "spec_version": body.spec_version,
    "strategy_id": body.strategy_id,
    "strategy_spec_registry_id": body.strategy_spec_registry_id,
    "requested_stage": body.requested_stage,
    "handoff_type": body.handoff_type,
    "evidence_refs": [ref.model_dump() for ref in body.evidence_refs],
    "action_proposal": body.action_proposal.model_dump() if body.action_proposal else None,
    "decision_event_id": body.decision_event_id,
    # Intentionally excludes: requested_by, target_queue, state, handoff_id, created_at
}

idempotency_record = IdempotencyRecord.reserve(
    idempotency_key=idempotency_key,
    operation_type="bff.SubmitGovernedHandoff",
    target_ref=f"trading_intent:{intent_id}",
    request_payload=idempotency_payload,
    trace_id=x_request_id or str(uuid.uuid4()),
)
```

**Idempotency replay check pattern (consistent with FOLLOWUP-4 Q1):**

```python
existing = command_store.get_command_by_idempotency_key(idempotency_key)
if existing:
    existing_hash = (
        (existing.get("foundation") or {})
        .get("idempotency_record", {})
        .get("request_hash")
    )
    if existing_hash != idempotency_record.request_hash:
        raise bff_error(
            409, ErrorCode.RESOURCE_CONFLICT,
            "Idempotency-Key reuse with different request body.",
            "idempotency_conflict",
        )
    # Same key + same hash → replay the prior response.
    prior_result = existing.get("result") or {}
    return _make_command_response(
        command_id=existing["command_id"],
        handoff_id=prior_result.get("handoff_id"),
        status="submitted",
        meta={"replayed": True, "original_at": existing.get("submitted_at")},
    )
```

**Acceptance checks for Q14:**

| Check | Expected result |
|---|---|
| `operation_type` = `"bff.SubmitGovernedHandoff"` | `IdempotencyRecord.operation_type` for submit-handoff commands is `"bff.SubmitGovernedHandoff"`, not a generic or mismatched type. |
| `target_ref` = `"trading_intent:{intent_id}"` | `IdempotencyRecord.target_ref` for all trading room commands is the intent-scoped ref, not a blank string or session ref. |
| Server-derived fields excluded from hash | `IdempotencyRecord.request_hash` does not change when the same client body is submitted from two different user sessions with different `requested_by` identities. |
| Idempotency conflict returns `409 RESOURCE_CONFLICT` | Same `Idempotency-Key` with a different body content returns `409` with `"idempotency_conflict"` reason. |
| Idempotency replay returns prior response | Same `Idempotency-Key` with the same body content returns `202` with the original `handoff_id` and `meta.replayed: true`. |

---

## Remaining Open Questions

| # | Question | Default if not resolved |
|---|---|---|
| Q15 | When `POST .../withdraw` is called and the intent has multiple handoffs (one `submitted`, one `rejected`), should the BFF withdraw only the active (`submitted`) handoff, or mark the intent itself as withdrawn? The schema allows both the `GovernedIntentHandoff` record and the `TradingIntent` record to have a `"withdrawn"` state. | Withdraw the most recent non-terminal handoff (state `"submitted"` or `"accepted"`). If no such handoff exists, withdraw the intent record itself. Record both the handoff-level and intent-level state change in the stored data. |
| Q16 | Should the BFF enforce `X-Request-Id` as a required header on write routes (per v1.3 OpenAPI `XRequestId` parameter marked `required: true`)? Currently no validation check for this header exists in the codebase pattern documented so far. | Yes. The BFF should require `X-Request-Id` on `POST .../handoffs` and `POST .../withdraw`. If absent, return `400 VALIDATION_FAILED` with reason `missing_request_id`. Use `X-Request-Id` as the `trace_id` for `IdempotencyRecord.reserve()` when `Idempotency-Key` is also present. Fall back to `str(uuid.uuid4())` if absent and the validation is not yet enforced. |

---

## Acceptance Check Addendum (to all prior packets)

These checks supplement the acceptance checks from packets 1–4.

| Check | Expected result |
|---|---|
| Stage-sequence not enforced by BFF | A `POST .../handoffs` with `requested_stage: "live"` succeeds at the BFF level (202) even if no prior shadow or paper handoff exists. Management governance returns `APPROVAL_REQUIRED` asynchronously. |
| Intent terminal-state guard | A `POST .../handoffs` on an intent with `state: "withdrawn"` returns `409 OPERATION_NOT_ALLOWED` with `details.reason = "TRADING_INTENT_HANDOFF_NOT_ALLOWED"`. |
| `GET` returns `ETag` header | `GET /bff/agora/trading-intents/{intent_id}` response includes `ETag: "intent:{intent_id}:{hash}"` header. |
| ETag stability | Successive GETs without intervening writes return identical `ETag` header values. |
| Stale `If-Match` rejected | `POST .../handoffs` with wrong `If-Match` value returns `409 RESOURCE_CONFLICT` including `current_etag` in `details_extra`. |
| Missing `If-Match` rejected | `POST .../handoffs` without `If-Match` header returns `400 VALIDATION_FAILED`. |
| `requested_by.actor_type = "trader"` | All stored `GovernedIntentHandoff` records have `requested_by.actor_type = "trader"`, not `"operator"`. |
| `requested_by` not accepted from client | A body with `requested_by: {"actor_type": "agora_servant", "actor_ref": "fake-ref"}` does not persist that value; the server-derived identity is used. |
| `GovernedIntentHandoffBody` Pydantic model exists | `from agora.trading_room.router import GovernedIntentHandoffBody` succeeds. |
| Stage/type mismatch returns `422` | `requested_stage: "paper"` + `handoff_type: "shadow_start"` returns `422`. |
| `operation_type` for idempotency record | `"bff.SubmitGovernedHandoff"` and `"bff.WithdrawTradingIntent"` are the exact `operation_type` values used. |
| `target_ref` format | `f"trading_intent:{intent_id}"` is the `target_ref` for all trading room idempotency records. |
| Stage-sequence note in response meta | `POST .../handoffs` `202` response `meta` includes a note that stage ordering is enforced by Management governance, not the BFF. |

---

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed. |
| Q9 resolution accuracy | Stage-sequence lock is correctly left to Management governance. Intent terminal-state guard (withdrawn/expired → `409 TRADING_INTENT_HANDOFF_NOT_ALLOWED`) is correctly added as a BFF-side check. `handoff_type`/`requested_stage` mismatch check is correctly identified as BFF-layer validation. |
| Q10 resolution accuracy | `"submitted"` initial state is confirmed. `"draft"` is correctly reserved for a not-yet-in-scope save-before-submit flow. BFF never writes `"accepted"`, `"rejected"`, `"converted"`, or `"expired"`. |
| Q11 accuracy | ETag generation using content hash of the intent record is correct (no `version` field in `TradingIntent` schema v1 to use instead). `If-Match: required: true` confirmed in v1.3 OpenAPI `IfMatch` parameter definition. Dashboard router pattern (`_make_etag`, `hashlib.sha256`) correctly adapted. |
| Q12 accuracy | `actor_type: "trader"` for human Trading Room operators is correct per `governed_intent_handoff.schema.json` actor enum (`"trader"`, `"agora_servant"`, `"institutional_persona"`). Strategy Workshop router uses `"operator"` which is NOT in this enum. BFF must use `"trader"` for human operators. |
| Q13 accuracy | Pydantic model approach is consistent with `ResearchPlanCreateRequest` pattern. `jsonschema.Draft7Validator` is correctly noted as test-only. `requested_by` is correctly excluded from the client-facing Pydantic body model. |
| Q14 accuracy | `operation_type` convention `f"bff.{cmd.command.value}"` confirmed from `main.py` line 1389. `target_ref` convention confirmed from same. `request_payload` exclusion of server-derived fields is consistent with idempotency hash intent. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |
| Status accuracy | `AG-BE-TR-002` is `todo`; `AG-BE-TR-001` is `todo` (blocked on `AG-BE-CP-001`); FOLLOWUP-4 is `done` (archived 2026-06-21T22:09:41Z). |

**Recommended reviewer approval command:**

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Followup-5 handoff packet approved: resolves Q9 (stage-sequence lock deferred to Management governance; BFF enforces terminal-state guard and handoff_type/stage mismatch only), Q10 (submitted initial state confirmed; draft reserved for future save-before-submit flow). New gaps Q11 (ETag generation via content hash; If-Match required validation pattern), Q12 (requested_by server-side population from scope.user_id; actor_type trader not operator), Q13 (Pydantic GovernedIntentHandoffBody model needed; jsonschema for tests only; requested_by excluded from client model), Q14 (operation_type bff.SubmitGovernedHandoff; target_ref trading_intent:{intent_id}; server-derived fields excluded from idempotency hash). No canonical truth, schemas, OpenAPI, BFF runtime, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Followup-5 BFF/frontend handoff packet approved for parent owner absorption."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual error, scope issue, or missing context requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5

git status --short
# A  support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
# ?? .orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff_followup_5.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
# status: in_progress, owner: Claude, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002
# status: todo, owner: Codex, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
# source: archive; terminal_status: done; archived_at 2026-06-21T22:09:41Z

# Confirmed v1.3 OpenAPI IfMatch parameter (line 82-86): required: true
# grep -n "IfMatch\|If-Match" services/control-plane/openapi/agora_v1_3.openapi.yaml
# → IfMatch: name: If-Match, required: true

# Confirmed GovernedIntentHandoff schema actor enum:
# cat services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json | python3 -m json.tool
# → "actor_type": {"enum": ["trader", "agora_servant", "institutional_persona"]}
# (NOT "operator" — differs from strategy_workshop convention)

# Confirmed no Pydantic model for GovernedIntentHandoff in BFF:
# grep -rn "GovernedIntentHandoff\|TradingIntentHandoff" services/control-plane/bff/ --include="*.py"
# (no output)

# Confirmed trading_room router is wired into agora router:
# grep -n "create_trading_room_router" services/control-plane/bff/agora/router.py
# → line 31: import; line 174: include_router call

# Confirmed TradingIntent schema has no version field:
# grep -n "\"version\"\|\"content_sha256\"" services/control-plane/specs/agora/trading_intent.schema.json
# (no output — content-hash based ETag is the correct approach)

# Confirmed IdempotencyRecord.reserve() signature from services/foundation/idempotency.py:
# operation_type: str, target_ref: str, request_payload: Any, trace_id: str
# Confirmed operation_type convention from main.py line 1389: f"bff.{cmd.command.value}"
# Confirmed target_ref convention from main.py line 1390: authority_scope.target_ref
```
