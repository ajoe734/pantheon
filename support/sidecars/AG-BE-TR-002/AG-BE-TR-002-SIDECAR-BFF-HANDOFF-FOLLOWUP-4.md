# AG-BE-TR-002 BFF and Frontend Handoff Packet — Followup 4

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-002` — Governed TradingIntent / handoff |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (done, PR #2150, reviewed by Claude2) |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI, JSON schemas,
BFF runtime, registry/governance implementation, or frontend code. The parent owner (Codex) decides
whether and how to absorb this material into the main implementation.

---

## Cumulative Packet Scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF` (done, PR #2142) | BFF query gap matrix (10 gaps), operator journeys A–I, frontend `tradingRoom.ts` method signatures, backend acceptance checks, 7 open design notes, routing table, `TradingIntent` vs `GovernedIntentHandoff` schema distinction. |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done, PR #2149) | Schema-derived corrections: `target_queue` derivation, `converted` state, `action_proposal` field constraints, management-plane-only fields, `additionalProperties: false` implication, corrected TypeScript interfaces, idempotency implementation pattern, backend module structure guidance, acceptance check addendum. Opened Q1–Q4. |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (done, PR #2150) | Q1–Q4 resolution: `IdempotencyRecord` + `CommandStore` integration pattern (Q1), idempotency TTL and durability boundary (Q2), `required_gate_refs` population policy (Q3), `DetailEnvelope` concrete shape (Q4). `DetailEnvelope` TypeScript type, `allowedActions` mapping, `CommandStore.get_command_by_idempotency_key` lookup pattern, BFF test skeleton supplement, operator journey J. |
| **This packet (FOLLOWUP-4)** | Q5–Q8 resolution: `CommandType` and `ObjectType` enum gaps confirmed and remediated (Q5/Q6), `ReadSurfaceStore` `trading_intents` dataset and method gaps identified with recommended additions (Q7), Management-plane-to-BFF state-push gap confirmed as unimplemented with interim guidance (Q8). Test skeleton correction (`update_command_result` → `update_status`). D10 error-code canonical mapping. Updated `_seeded_client` test pattern using `_ensure_local_overlay_records`. |

---

## Current State Observed (2026-06-21)

| Surface | Observed state | Change since FOLLOWUP-3 |
|---|---|---|
| `AG-BE-TR-002` | `todo`; owner `Codex`, reviewer `Claude2`. | Unchanged. |
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. Still gated on `AG-BE-CP-001` (blocked). |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. |
| `services/control-plane/bff/models.py` `CommandType` enum | Does not include `SUBMIT_GOVERNED_HANDOFF` or `WITHDRAW_TRADING_INTENT`. | New finding. See Q5 resolution below. |
| `services/control-plane/bff/models.py` `ObjectType` enum | Does not include `TRADING_INTENT` or `GOVERNED_HANDOFF`. | New finding. See Q6 resolution below. |
| `services/control-plane/bff/read_store.py` `ReadSurfaceStore._LOCAL_DATA_KEYS` | No `trading_intents` or `governed_intent_handoffs` keys. No `get_trading_intent()` or `list_trading_intents()` methods. The existing `agora_handoffs` dataset is a different construct. | New finding. See Q7 resolution below. |
| Management-plane-to-BFF push mechanism | Not implemented in BFF. `ReadSurfaceStore` loads from static JSON snapshots; no push-subscribe or poll-from-management-service wiring exists. | New finding. See Q8 resolution below. |
| FOLLOWUP-3 test skeleton `update_command_result` call | Method does not exist in `CommandStore`. Correct method is `update_status`. | Correction. See Test Skeleton Correction below. |
| D10 error codes in `models.py` `ErrorCode` | `TRADING_INTENT_NOT_ALLOWED`, `TRADING_INTENT_HANDOFF_NOT_ALLOWED`, `TRADING_INTENT_ALREADY_RECORDED` not in `ErrorCode`. `APPROVAL_REQUIRED` is a legacy alias (line 517 of `main.py`) that maps to `ErrorCode.HUMAN_GATE_PENDING`. | New finding. See D10 Error Code Mapping below. |

---

## Resolved Open Questions from FOLLOWUP-3

### Q5 — `CommandType` enum value for `submit_governed_handoff`

**Resolution: No trading-room `CommandType` values exist in `models.py`. Owner must add two new enum members.**

Confirmed from `services/control-plane/bff/models.py` (the `CommandType` class, lines 18–88):
the enum contains 60+ entries covering deployment, rollback, kill switch, evolution, capital, ranking,
Agora messaging, and lifecycle commands — but **no Trading Room command types**.

The existing enum follows a PascalCase `CamelCase` convention for enum values (e.g., `"ApproveDeployment"`,
`"IssueRiskOff"`, `"AgoraSignalFeedback"`). To maintain consistency:

**Recommended additions to `CommandType` in `models.py`:**

```python
# Trading Room — governed handoff lifecycle (AG-BE-TR-002)
SUBMIT_GOVERNED_HANDOFF = "SubmitGovernedHandoff"
WITHDRAW_TRADING_INTENT = "WithdrawTradingIntent"
```

The `CommandStore.submit_command()` signature (line 41) accepts `command_type: CommandType`, so
the typed enum value must exist before it can be used. Without these additions, calling
`CommandStore.submit_command(command_type=CommandType.SUBMIT_GOVERNED_HANDOFF, ...)` will raise an
`AttributeError`.

**No default fallback is acceptable here.** Using an existing catch-all type (e.g., `DEPLOYMENT_ACTION`)
would silently corrupt the command log and make telemetry unqueryable by command type.

---

### Q6 — `ObjectType` enum value and `TargetObject` type for `GovernedIntentHandoff` command target

**Resolution: No Trading Room object types exist in `models.py`. Owner must add two new enum members. The `TargetObject` for the submit-handoff command should target the `TradingIntent`, not the handoff itself.**

Confirmed from `services/control-plane/bff/models.py` (the `ObjectType` class, lines 91–127):
the enum contains 30+ entries covering deployment plans, runtime bindings, capital pools, personas,
Agora messaging objects, etc. — but **no `TradingIntent` or `GovernedHandoff` types**.

**Recommended additions to `ObjectType` in `models.py`:**

```python
# Trading Room — governed handoff lifecycle (AG-BE-TR-002)
TRADING_INTENT = "TradingIntent"
GOVERNED_HANDOFF = "GovernedHandoff"
```

**`TargetObject` guidance for each command type:**

| Command | `TargetObject.type` | `TargetObject.id` | Rationale |
|---|---|---|---|
| `SUBMIT_GOVERNED_HANDOFF` | `ObjectType.TRADING_INTENT` | `intent_id` | The command acts on a TradingIntent; the generated handoff ID is passed in `params` or returned in the command result. |
| `WITHDRAW_TRADING_INTENT` | `ObjectType.TRADING_INTENT` | `intent_id` | The withdrawal targets the intent, not an individual handoff; the most recent handoff is implicitly withdrawn. |

**Example `submit_governed_handoff` command call (using corrected types):**

```python
from models import CommandType, ObjectType, TargetObject
import uuid

command_id = str(uuid.uuid4())
handoff_id = str(uuid.uuid4())

command_store.submit_command(
    command_id=command_id,
    command_type=CommandType.SUBMIT_GOVERNED_HANDOFF,
    target=TargetObject(
        type=ObjectType.TRADING_INTENT,
        id=intent_id,
    ),
    submitted_at=utc_now(),
    params={
        "handoff": handoff_dict,
        "handoff_id": handoff_id,
        "target_queue": target_queue,       # server-derived, not from client
        "requested_stage": requested_stage,
    },
    audit_context={"operator_id": identity.operator_id},
    foundation_context={
        "idempotency_record": idempotency_record.to_dict(),
        "trace_context": trace_context.to_dict(),
    },
)
```

**`get_active_commands_for_target` usage:** `CommandStore.get_active_commands_for_target(target_type, target_id)` (line 131 of `command_queue.py`) can detect in-flight commands for an intent:

```python
active = command_store.get_active_commands_for_target(
    target_type=ObjectType.TRADING_INTENT.value,
    target_id=intent_id,
)
if active:
    raise bff_error("INVALID_STATE", status_code=409,
                    reason="An active command is already pending for this intent.")
```

---

### Q7 — `ReadSurfaceStore` method for `TradingIntent` and its handoff chain

**Resolution: `ReadSurfaceStore` has no `trading_intents` dataset, no `get_trading_intent()` method, and no `list_trading_intents()` method. The existing `agora_handoffs` dataset is a different construct. Owner must extend `ReadSurfaceStore`.**

Confirmed from `services/control-plane/bff/read_store.py` (class `ReadSurfaceStore`, `_LOCAL_DATA_KEYS`, lines 7001–7084):

- The key `"agora_handoffs"` already exists in `_LOCAL_DATA_KEYS` and is backed by
  `create_agora_handoff()` / `list_agora_handoffs()` methods (lines 10043–10106).
  **This is NOT the `GovernedIntentHandoff` record schema.** It is the generic Agora-to-Management
  routing handoff concept (a workflow routing record between Agora and Management; see the
  `create_agora_handoff()` body which includes `source`, `destination`, `priority`, `slaDueAt`,
  `canonicalWriteAuthority: "agora_handoff_service"`). This is a separate construct from the
  `GovernedIntentHandoff` schema in `services/control-plane/specs/agora/v4/`.
- There is no `trading_intents` key in `_LOCAL_DATA_KEYS`.
- `ReadSurfaceStore` has no generic `upsert(object_type, object_id, data)` method. Each dataset
  has its own typed methods.

**The FOLLOWUP-3 test skeleton's `bff_main.read_store.upsert(...)` call is incorrect.** See Test Skeleton Correction below.

**Recommended additions to `ReadSurfaceStore` (owner task for `read_store.py`):**

**Step 1 — Add to `_LOCAL_DATA_KEYS`:**

```python
"trading_intents": "trading_intents",
"governed_intent_handoffs": "governed_intent_handoffs",
```

**Step 2 — Add typed read methods:**

```python
def get_trading_intent(
    self, intent_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Return a TradingIntent record by intent_id, or None if not found."""
    if not intent_id:
        return None
    records = (self._data.get("trading_intents") or {})
    return json.loads(json.dumps(records.get(intent_id))) if intent_id in records else None

def list_trading_intents(self) -> List[Dict[str, Any]]:
    """Return all TradingIntent records, sorted newest-first."""
    items = list((self._data.get("trading_intents") or {}).values())
    items.sort(key=self._recent_sort_value, reverse=True)
    return json.loads(json.dumps(items))

def get_governed_intent_handoffs_for_intent(
    self, intent_id: str
) -> List[Dict[str, Any]]:
    """Return all GovernedIntentHandoff records for a given intent_id."""
    items = [
        v for v in (self._data.get("governed_intent_handoffs") or {}).values()
        if v.get("intent_id") == intent_id
    ]
    items.sort(key=self._recent_sort_value, reverse=True)
    return json.loads(json.dumps(items))
```

**Step 3 — Add local overlay write helpers (for BFF to record submitted handoffs and intent updates):**

```python
def upsert_trading_intent(
    self, intent_id: str, data: Dict[str, Any]
) -> Dict[str, Any]:
    """Write or overwrite a TradingIntent record in the local overlay store."""
    records = self._ensure_local_overlay_records("trading_intents")
    records[intent_id] = json.loads(json.dumps(data))
    self._save()
    return json.loads(json.dumps(records[intent_id]))

def upsert_governed_intent_handoff(
    self, handoff_id: str, data: Dict[str, Any]
) -> Dict[str, Any]:
    """Write or overwrite a GovernedIntentHandoff record in the local overlay store."""
    records = self._ensure_local_overlay_records("governed_intent_handoffs")
    records[handoff_id] = json.loads(json.dumps(data))
    self._save()
    return json.loads(json.dumps(records[handoff_id]))
```

**`DetailEnvelope` assembly pattern for `GET .../trading-intents/{intent_id}`:**

```python
def _build_detail_envelope(intent_id: str, read_store: ReadSurfaceStore) -> dict:
    intent = read_store.get_trading_intent(intent_id)
    handoffs = read_store.get_governed_intent_handoffs_for_intent(intent_id)
    last_handoff = handoffs[0] if handoffs else None
    last_state = (last_handoff or {}).get("state")

    allowed_submit = last_state in (None, "rejected", "expired", "withdrawn")
    allowed_withdraw = last_state in ("submitted", "accepted")

    if intent is None:
        # Degraded path — intent not yet visible in read store.
        # Use ErrorEnvelope rather than a skeleton DetailEnvelope.
        return _degraded_envelope(intent_id)

    return {
        "object_ref": {"type": "trading_intent", "id": intent_id},
        "status": last_state or intent.get("status", "unknown"),
        "lifecycle_state": last_state,
        "allowedActions": {
            "submit_handoff": allowed_submit,
            "withdraw": allowed_withdraw,
        },
        "meta": {
            "retrieved_at": utc_now(),
            "schema_version": "1.0",
        },
        "links": {
            "self": f"/bff/agora/trading-intents/{intent_id}",
            "handoffs": f"/bff/agora/trading-intents/{intent_id}/handoffs",
        },
        "data": {
            "intent": intent,
            "handoffs": handoffs,
        },
    }
```

---

### Q8 — Management-plane-to-BFF handoff state push mechanism

**Resolution: No push mechanism from Management governance plane to BFF read store is implemented. The BFF would show `state: "submitted"` indefinitely until this is built. This is a Management plane integration contract item; the owner must open a cross-service blocker or design item.**

Confirmed from `services/control-plane/bff/read_store.py`:

- `ReadSurfaceStore` uses two adapters:
  - `CanonicalSnapshotAdapter` — loads from a static JSON snapshot file
  - `ServiceBackedReadAdapter` — loads from a snapshot file with an optional service-backed live path

Neither adapter subscribes to or polls a Management governance plane endpoint. The BFF has no
SSE consumer, webhook receiver, or event-bus subscriber for governance-plane state changes.

The `_ensure_local_overlay_records()` mechanism writes BFF-local records into the JSON data store.
This is how `create_agora_handoff()` works for existing agora handoffs. For `GovernedIntentHandoff`
records, the BFF can write the initial `state: "submitted"` record via `upsert_governed_intent_handoff`
at submit time. But subsequent state transitions (`accepted`, `rejected`, `converted`, `expired`)
originate from the Management governance plane and have no wired-in delivery path to the BFF.

**Recommended interim guidance for owner:**

| Option | Trade-off |
|---|---|
| BFF poll (periodic HTTP GET to management service) | Simplest. BFF adds a background polling task (e.g., `asyncio.create_task`) that refreshes handoff states every N seconds from a Management service endpoint. Not implemented; needs Management service endpoint to be defined. |
| Management service push (POST webhook to BFF) | BFF exposes an internal `/internal/trading-intent-state-update` endpoint that the Management plane calls when handoff state changes. Requires authentication between planes. More reliable. Not implemented. |
| SSE from Management → BFF | BFF subscribes to a Management-plane SSE stream. Pattern exists in the codebase (`SseEventEnvelope` model in `models.py`) but no Management → BFF SSE subscription is wired. |
| Snapshot file refresh | Management plane writes the handoff state into the BFF's local snapshot file path. Works for single-instance dev; does not scale to multi-pod. |

**Default until owner decides:** The BFF should document in the `GET .../trading-intents/{intent_id}` response `meta` that the handoff state is BFF-local and may not reflect the latest governance-plane state. If the Management plane has already processed the handoff, the operator should refresh the page after the expected processing time.

**Owner action required:** Open a cross-service contract item with the Management plane team (or the `AG-BE-TR-001`/`AG-BE-TR-002` reviewer scope) to define the state push mechanism before AG-BE-TR-002 is accepted for production use. Mark this as a known gap in the AG-BE-TR-002 PR description.

---

## Test Skeleton Correction (FOLLOWUP-3)

The test skeleton in FOLLOWUP-3 contained one method call that does not exist in `CommandStore`:

```python
# FOLLOWUP-3 — INCORRECT:
command_store.update_command_result(command_id, response)

# CORRECT — use update_status() (line 103 of command_queue.py):
from models import CommandStatus
command_store.update_status(
    command_id=command_id,
    status=CommandStatus.EXECUTED,
    result=response,
)
```

`CommandStore` exposes exactly: `submit_command`, `get_command`, `get_command_by_idempotency_key`,
`update_status`, `get_active_commands_for_target`. There is no `update_command_result` method.

**Corrected `_seeded_client` context manager** (replaces the FOLLOWUP-3 version):

The FOLLOWUP-3 test skeleton used `bff_main.read_store.upsert(object_type="trading_intent", ...)`,
which does not exist. The corrected version uses `upsert_trading_intent()` (recommended new method
from Q7 resolution above), guarded against the case where the method is not yet implemented:

```python
@contextmanager
def _seeded_client(intent_fixture: dict | None = None):
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.command_store = CommandStore(
            os.path.join(td, "commands.jsonl")
        )
        # Seed a TradingIntent record if provided.
        # Requires upsert_trading_intent() to be added by the owner (Q7).
        if intent_fixture:
            upsert = getattr(bff_main.read_store, "upsert_trading_intent", None)
            if upsert:
                upsert(
                    intent_id=intent_fixture["intent_id"],
                    data=intent_fixture,
                )
            else:
                # Fallback: inject directly into _data for pre-implementation tests.
                bff_main.read_store._data.setdefault("trading_intents", {})[
                    intent_fixture["intent_id"]
                ] = intent_fixture
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
```

---

## D10 Error Code Canonical Mapping

The D10 error codes defined in the design docs are not individual `ErrorCode` enum members in
`models.py`. The canonical mapping (owner must use these, not invent new `ErrorCode` members):

| D10 error string | Canonical `ErrorCode` | HTTP status | Basis |
|---|---|---|---|
| `TRADING_INTENT_NOT_ALLOWED` | `ErrorCode.OPERATION_NOT_ALLOWED` | `409` | `main.py` line 507: `"INVALID_STATE" → OPERATION_NOT_ALLOWED`; this is the safety gate for broker-order or RuntimeBinding paths. |
| `TRADING_INTENT_HANDOFF_NOT_ALLOWED` | `ErrorCode.OPERATION_NOT_ALLOWED` | `409` | Same mapping — operation not allowed in the current intent state. |
| `TRADING_INTENT_ALREADY_RECORDED` | `ErrorCode.RESOURCE_CONFLICT` | `409` | `main.py` line 509–510: `"STATE_CONFLICT" / "CONCURRENT_MODIFICATION" → RESOURCE_CONFLICT`. Duplicate non-idempotent creation attempt. |
| `APPROVAL_REQUIRED` | `ErrorCode.HUMAN_GATE_PENDING` | `409` | Confirmed: `main.py` line 517: `"APPROVAL_REQUIRED": ErrorCode.HUMAN_GATE_PENDING.value`. |

The `BffErrorPayload.details.reason` field (the `ErrorDetail` model) should carry the domain-specific
sub-reason string (`"TRADING_INTENT_NOT_ALLOWED"`, etc.) so the frontend can render the correct
message without testing on the `code` alone:

```python
# Example: TRADING_INTENT_NOT_ALLOWED gate
raise HTTPException(
    status_code=409,
    detail=BffErrorEnvelope(
        error=BFFError(
            code=ErrorCode.OPERATION_NOT_ALLOWED,
            i18nKey="trading_intent.not_allowed",
            message="This intent cannot be submitted as a live action from Agora.",
            retryable=False,
            userActionable=True,
            details=ErrorDetail(
                reason="TRADING_INTENT_NOT_ALLOWED",
                suggestion="Submit a review request through the governance channel.",
            ),
        )
    ).model_dump(),
)
```

**Frontend action:** The frontend client in `tradingRoom.ts` should check
`error.details.reason === "TRADING_INTENT_NOT_ALLOWED"` (not `error.code`) to display the
D10-specific message. The `code` is the canonical BFF code; `details.reason` carries the
domain-specific sub-reason.

---

## Acceptance Check Addendum (to previous packets)

These checks supplement the acceptance checks from the original packet, FOLLOWUP-2, and FOLLOWUP-3.

| Check | Expected result |
|---|---|
| `CommandType.SUBMIT_GOVERNED_HANDOFF` exists | `"SubmitGovernedHandoff"` is a member of `CommandType` in `models.py`. `CommandStore.submit_command` call with this type does not raise `AttributeError`. |
| `CommandType.WITHDRAW_TRADING_INTENT` exists | `"WithdrawTradingIntent"` is a member of `CommandType` in `models.py`. |
| `ObjectType.TRADING_INTENT` exists | `"TradingIntent"` is a member of `ObjectType` in `models.py`. |
| `ObjectType.GOVERNED_HANDOFF` exists | `"GovernedHandoff"` is a member of `ObjectType` in `models.py`. |
| Submit-handoff command target | `TargetObject(type=ObjectType.TRADING_INTENT, id=intent_id)` — not `GOVERNED_HANDOFF` as the target. |
| `ReadSurfaceStore.get_trading_intent` exists | Method returns the `TradingIntent` record dict for a given `intent_id`, or `None`. |
| `ReadSurfaceStore.get_governed_intent_handoffs_for_intent` exists | Method returns a list of `GovernedIntentHandoff` records for a given `intent_id`. |
| `agora_handoffs` not used for `GovernedIntentHandoff` | The `agora_handoffs` dataset in `ReadSurfaceStore` is NOT used to store `GovernedIntentHandoff` records. Separate `governed_intent_handoffs` dataset used. |
| `TRADING_INTENT_NOT_ALLOWED` error shape | `BffErrorPayload.code = "OPERATION_NOT_ALLOWED"`, `details.reason = "TRADING_INTENT_NOT_ALLOWED"`. Not a custom `ErrorCode` enum member. |
| `APPROVAL_REQUIRED` error shape | `BffErrorPayload.code = "HUMAN_GATE_PENDING"`, `details.reason = "APPROVAL_REQUIRED"`. |
| `update_status` not `update_command_result` | BFF uses `CommandStore.update_status(command_id, CommandStatus.EXECUTED, result=...)` — not the non-existent `update_command_result`. |
| Management-plane state push gap documented | `GET .../trading-intents/{id}` PR description or inline doc notes that handoff state is BFF-local and may lag behind Management-plane governance decisions until the push mechanism is implemented. |

---

## Remaining Open Questions

| # | Question | Default if not resolved |
|---|---|---|
| Q9 | How does the BFF determine the `requested_stage` sequence lock — i.e., when a `paper` submission is made, does the BFF enforce that a prior `shadow` handoff exists in an `accepted` state, or is that a Management-plane concern? The `required_gate_refs` policy (Q3 resolution) deferred this to the owner. | The BFF should not enforce prior-stage completion unless a `required_gate_refs` check is explicitly specified in the AG-BE-TR-002 acceptance criteria. For the initial implementation, leave the stage-sequence guard to the Management governance plane and document this as a known limitation. |
| Q10 | The `GovernedIntentHandoff` schema (v4) specifies `state` enum values: `["draft", "submitted", "accepted", "rejected", "converted", "expired", "withdrawn"]`. Should the BFF create handoffs in `"draft"` state and transition to `"submitted"` after the command is queued, or always create in `"submitted"` state? | Create in `"submitted"` state. The `POST .../handoffs` endpoint is a submit operation, not a draft-save operation. `"draft"` is reserved for future use (e.g., a save-before-submit UI flow). |

---

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed. |
| Q5 resolution accuracy | `CommandType` in `models.py` lines 18–88 confirmed to have no `SUBMIT_GOVERNED_HANDOFF` or `WITHDRAW_TRADING_INTENT`. Recommended values `"SubmitGovernedHandoff"` and `"WithdrawTradingIntent"` follow the PascalCase enum convention. |
| Q6 resolution accuracy | `ObjectType` in `models.py` lines 91–127 confirmed to have no `TRADING_INTENT` or `GOVERNED_HANDOFF`. Target-type guidance (intent as command target, not handoff) is consistent with `TargetObject` semantics in other BFF routes. |
| Q7 resolution accuracy | `ReadSurfaceStore._LOCAL_DATA_KEYS` confirmed to have no `trading_intents` or `governed_intent_handoffs`. `agora_handoffs` confirmed as a different construct (routing handoff, not governed intent handoff). Recommended `get_trading_intent`, `list_trading_intents`, `get_governed_intent_handoffs_for_intent`, `upsert_trading_intent`, `upsert_governed_intent_handoff` methods are consistent with existing `ReadSurfaceStore` patterns. |
| Q8 resolution accuracy | No Management-plane push mechanism confirmed absent. Three options (poll, push webhook, SSE) are documented as not implemented. Guidance to document the gap in the PR description is appropriate. |
| Test skeleton correction accuracy | `CommandStore` (lines 1–139 of `command_queue.py`) has no `update_command_result` method. Correct method is `update_status(command_id, status, result=...)` (line 103). |
| D10 error code mapping accuracy | `main.py` line 517 confirms `"APPROVAL_REQUIRED" → HUMAN_GATE_PENDING`. `OPERATION_NOT_ALLOWED` and `RESOURCE_CONFLICT` are confirmed present in `models.py` `ErrorCode`. Mapping table is correct. |
| `agora_handoffs` vs `governed_intent_handoffs` distinction | `list_agora_handoffs()` (line 10043) and `create_agora_handoff()` (line 10059) in `read_store.py` use `"canonicalWriteAuthority": "agora_handoff_service"` — confirming these are a different service domain from `GovernedIntentHandoff`. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |
| Status accuracy | `AG-BE-TR-002` is `todo`; `AG-BE-TR-001` is `todo` (blocked on `AG-BE-CP-001`); FOLLOWUP-3 is `done` (PR #2150). |

**Recommended reviewer approval command:**

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md \
  REVIEW_NOTES_ZH="Followup-4 handoff packet approved: resolves Q5 (CommandType missing SUBMIT_GOVERNED_HANDOFF/WITHDRAW_TRADING_INTENT confirmed; PascalCase additions recommended), Q6 (ObjectType missing TRADING_INTENT/GOVERNED_HANDOFF confirmed; TargetObject targets TradingIntent not handoff), Q7 (ReadSurfaceStore has no trading_intents dataset or methods; agora_handoffs is a different construct; get_trading_intent/upsert pattern recommended), Q8 (no Management-plane push mechanism exists; three options documented; gap must be flagged in PR description). Includes test skeleton correction (update_command_result→update_status), D10 error-code canonical mapping (OPERATION_NOT_ALLOWED/HUMAN_GATE_PENDING/RESOURCE_CONFLICT), and updated _seeded_client pattern. No canonical truth, schemas, OpenAPI, BFF runtime, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Followup-4 BFF/frontend handoff packet approved for parent owner absorption."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Describe the factual error, scope issue, or missing context requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4

git status --short
# A  support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
# ?? .orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff_followup_4.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
# status: in_progress, owner: Claude, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002
# status: todo, owner: Codex, reviewer: Claude2

# Confirmed CommandType enum in models.py lines 18-88: no SUBMIT_GOVERNED_HANDOFF or WITHDRAW_TRADING_INTENT
# Confirmed ObjectType enum in models.py lines 91-127: no TRADING_INTENT or GOVERNED_HANDOFF
# Confirmed CommandStore methods in command_queue.py: submit_command, get_command,
#   get_command_by_idempotency_key, update_status, get_active_commands_for_target
#   NO update_command_result method

# Confirmed ReadSurfaceStore._LOCAL_DATA_KEYS in read_store.py lines 7001-7084:
#   no trading_intents, no governed_intent_handoffs
#   agora_handoffs key exists but is backed by create_agora_handoff() / list_agora_handoffs()
#   with canonicalWriteAuthority: "agora_handoff_service" — a different domain

# Confirmed APPROVAL_REQUIRED mapping in main.py line 517:
#   "APPROVAL_REQUIRED": ErrorCode.HUMAN_GATE_PENDING.value
# Confirmed OPERATION_NOT_ALLOWED in ErrorCode enum (models.py line 174)
# Confirmed RESOURCE_CONFLICT in ErrorCode enum (models.py line 175)

# Confirmed read_store.py has no trading_intent methods:
# grep -n "trading_intent\|TradingIntent\|governed_handoff\|GovernedHandoff" \
#   services/control-plane/bff/read_store.py
# (no output)

# Confirmed no Management-plane push receiver in BFF:
# grep -rn "management.*push\|event_store\|push.*handoff\|SSE.*management" \
#   services/control-plane/bff/ | grep -v ".pyc"
# (no relevant results for Management→BFF push)
```
