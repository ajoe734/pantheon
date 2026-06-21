# AG-BE-TR-002 BFF and Frontend Handoff Packet — Followup 3

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-002` — Governed TradingIntent / handoff |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done, PR #2149, reviewed by Claude2) |
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
| **This packet (FOLLOWUP-3)** | Q1–Q4 resolution: `IdempotencyRecord` + `CommandStore` integration pattern (Q1), idempotency TTL and durability boundary (Q2), `required_gate_refs` population policy (Q3), `DetailEnvelope` concrete shape for `GET .../trading-intents/{id}` (Q4). `DetailEnvelope` TypeScript type, `allowedActions` mapping, `CommandStore.get_command_by_idempotency_key` lookup pattern, BFF test skeleton supplement, and operator journey J (post-governance state observation). |

---

## Current State Observed (2026-06-21)

| Surface | Observed state | Change since FOLLOWUP-2 |
|---|---|---|
| `AG-BE-TR-002` | `todo`; owner `Codex`, reviewer `Claude2`. | Unchanged. |
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. Still gated on `AG-BE-CP-001` (blocked). |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. Gated on `AG-FE-TR-001`. |
| `services/foundation/idempotency.py` | `IdempotencyRecord` class present. `reserve()`, `with_status()`, `matches_request()`, `to_dict()`, `from_dict()` methods confirmed. | New finding. See Q1 resolution below. |
| `services/control-plane/bff/command_queue.py` | `CommandStore.get_command_by_idempotency_key(idempotency_key, *, operator_id)` method confirmed. | New finding. See Q1 resolution below. |
| `DetailEnvelope` (OpenAPI `#/components/schemas/DetailEnvelope`) | Confirmed: `{object_ref, status, lifecycle_state?, allowedActions, meta, links, data}`. `additionalProperties: false`. | New finding. See Q4 resolution below. |

---

## Resolved Open Questions from FOLLOWUP-2

### Q1 — IdempotencyRecord.reserve() vs module-level dict

**Resolution: Use `IdempotencyRecord` + `CommandStore` — the existing foundation pattern.**

The `services/foundation/idempotency.py` module provides `IdempotencyRecord` with the following
interface:

```python
IdempotencyRecord.reserve(
    *,
    idempotency_key: str,
    operation_type: str,   # e.g. "trading_intent.handoff.submit"
    target_ref: str,       # e.g. intent_id
    request_payload: Any,  # hashed to detect conflicts
    trace_id: str,
    seen_at: datetime | str | None = None,
) -> IdempotencyRecord
```

The `CommandStore` in `services/control-plane/bff/command_queue.py` exposes:

```python
CommandStore.get_command_by_idempotency_key(
    idempotency_key: str,
    *,
    operator_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]
```

This method scans the command log and returns the first command whose `foundation.idempotency_record.idempotency_key` matches — scoped optionally to the same operator.

**Recommended integration pattern for `POST .../handoffs`:**

```python
# 1. Validate idempotency_key from header (reject if absent or blank).
resolved_key = _require_operator_command_idempotency_key(idempotency_key)

# 2. Look up existing command for this key + operator.
existing_cmd = command_store.get_command_by_idempotency_key(
    resolved_key,
    operator_id=identity.operator_id,
)

if existing_cmd is not None:
    existing_record = IdempotencyRecord.from_dict(
        existing_cmd["foundation"]["idempotency_record"]
    )
    # Conflict: same key, different body.
    if not existing_record.matches_request(request_payload):
        raise bff_error("IDEMPOTENCY_CONFLICT", status_code=409)
    # Replay: same key, same body — return cached result.
    return existing_cmd["result"]  # replay the original 202 response

# 3. Reserve an idempotency record.
idempotency_record = IdempotencyRecord.reserve(
    idempotency_key=resolved_key,
    operation_type="trading_intent.handoff.submit",
    target_ref=intent_id,
    request_payload=request_payload,
    trace_id=trace_context.trace_id,
)

# 4. Build and submit the command with foundation context embedding
#    the idempotency record.
foundation_context = {
    "idempotency_record": idempotency_record.to_dict(),
    "trace_context": trace_context.to_dict(),
}
command_store.submit_command(
    command_id=...,
    command_type=CommandType.SUBMIT_GOVERNED_HANDOFF,
    target=...,
    submitted_at=utc_now(),
    params={"handoff": handoff_dict},
    audit_context={"operator_id": identity.operator_id},
    foundation_context=foundation_context,
)

# 5. Return 202.
response = {"command_id": command_id, "handoff_id": handoff_id}
# Store response in the command result for replay:
command_store.update_command_result(command_id, response)
return JSONResponse(status_code=202, content=response)
```

**Why prefer this over the module-level dict pattern:**

| Concern | Module-level dict | CommandStore + IdempotencyRecord |
|---|---|---|
| Survives process restart | No — lost on restart | Yes — persisted to `commands.jsonl` |
| Cross-pod safety | No — pod A and pod B have separate dicts | Partially (single file; durable when shared storage) |
| Aligns with governance BFF | No — diverges | Yes — same foundation layer as all other BFF routes |
| Operator scoping | Manual | Built-in via `operator_id` param |
| Conflict detection | SHA-256 hash in-dict | `IdempotencyRecord.matches_request()` — same primitive |
| TTL | None | None (file append log) — see Q2 below |

The module-level dict is acceptable as a temporary fallback for unit tests only, not for production
routing logic. The Trading Room router receives `command_store` from the factory caller (same as
other routers that accept it as an injected dependency).

**Factory signature change:** `create_trading_room_router` must accept `command_store`:

```python
def create_trading_room_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    command_store: "CommandStore",    # add this
    read_store: "ReadSurfaceStore",   # add this (for GET intent detail)
) -> APIRouter:
```

---

### Q2 — Idempotency key TTL and durability boundary

**Resolution: No explicit TTL in the current `CommandStore` implementation. Owner decision required for production posture.**

The `CommandStore` appends to a `.jsonl` file and never prunes entries. `get_command_by_idempotency_key` does a linear scan of all commands, so idempotency keys are effectively eternal for the life of the file.

**Recommended policy for the owner to decide before merging:**

| Option | Trade-off |
|---|---|
| No TTL (current behavior) | Simple. Key reuse is blocked permanently — a client cannot re-use a UUID for a different operation later (e.g., after a system reset). Acceptable if clients generate fresh UUIDs per action. |
| 24-hour TTL (conventional default) | Requires periodic log compaction or a separate TTL index. Not implemented in `CommandStore` today. |
| Session-scoped TTL | Keys expire when the operator session expires. Requires integration with session lifecycle (not currently wired in Trading Room context). |

**Immediate guidance (until owner decides):** The BFF should document in the response `meta` that the `Idempotency-Key` is valid for the duration of the command log. Clients must generate a fresh UUID for each user-initiated action and must not re-use UUIDs across different operations.

---

### Q3 — `required_gate_refs` population policy

**Resolution: Populate `required_gate_refs` server-side for `paper`, `canary`, and `live` stages. Leave absent for `shadow`.**

The `required_gate_refs` field (optional `string[]`) is defined in `governed_intent_handoff.schema.json`
to record which upstream gate approvals are required before this submission can proceed. The BFF
should populate it as follows:

| `requested_stage` | `required_gate_refs` policy |
|---|---|
| `"shadow"` | Omit — no prior approval gate required. |
| `"paper"` | Include the prior shadow handoff ID when one exists: `[shadow_handoff_id]`. If no prior shadow, omit (not all paper submissions require a prior shadow). |
| `"canary"` | Include the paper validation approval ref and prior paper handoff ID: `["paper_approval:<ref>", "paper_handoff:<handoff_id>"]`. If the refs cannot be resolved from the intent's handoff chain, return `APPROVAL_REQUIRED` with blocking reason before creating the record. |
| `"live"` | Include the canary approval ref and prior canary handoff ID: `["canary_approval:<ref>", "canary_handoff:<handoff_id>"]`. Same missing-ref policy as canary. |

This is a **server-owned field**: the BFF derives refs from the existing handoff chain on the intent
record. If the client supplies `required_gate_refs` in the request body, the BFF must reject with
`422` (`additionalProperties: false` violation — the BFF-populated field is not an accepted client
body field for the submission endpoint; the client only sends the schema-defined submission fields).

**Implementation note:** For the initial TR-002 implementation, the paper/canary/live `required_gate_refs`
check may be simplified to asserting that the handoff chain contains a prior handoff in `accepted` or
`converted` state for the preceding stage. The exact approval ref format (`paper_approval:<ref>`) is
a placeholder; owner should define the canonical ref format aligned with Management governance plane
handoff IDs.

---

### Q4 — `DetailEnvelope` concrete shape for `GET .../trading-intents/{id}`

**Resolution: `DetailEnvelope` is defined in OpenAPI `#/components/schemas/DetailEnvelope` as a structured wrapper. `TradingIntentDetail` lives under the `data` field.**

Schema confirmed from `agora_v1_3.openapi.yaml` (lines 132–154):

```yaml
DetailEnvelope:
  type: object
  required: [object_ref, status, allowedActions, meta, links, data]
  properties:
    object_ref:
      type: object
      required: [type, id]
      properties:
        type: { type: string }
        id: { type: string }
    status: { type: string }
    lifecycle_state: { type: string }
    allowedActions:
      type: object
      additionalProperties: { type: boolean }
    meta:
      type: object
      additionalProperties: true
    links:
      type: object
      additionalProperties: true
    data: {}
  additionalProperties: false
```

**Concrete shape for `GET /bff/agora/trading-intents/{intent_id}`:**

```json
{
  "object_ref": { "type": "trading_intent", "id": "<intent_id>" },
  "status": "submitted",
  "lifecycle_state": "submitted",
  "allowedActions": {
    "submit_handoff": true,
    "withdraw": true
  },
  "meta": {
    "retrieved_at": "<UTC ISO-8601>",
    "schema_version": "1.0"
  },
  "links": {
    "self": "/bff/agora/trading-intents/<intent_id>",
    "handoffs": "/bff/agora/trading-intents/<intent_id>/handoffs"
  },
  "data": {
    "intent": { /* TradingIntent record */ },
    "handoffs": [ /* GovernedIntentHandoff[] */ ]
  }
}
```

**`allowedActions` mapping table (intent-state-driven):**

| Intent / handoff state | `submit_handoff` | `withdraw` | Notes |
|---|---|---|---|
| No handoff exists / last handoff `rejected` or `expired` | `true` | `false` (nothing to withdraw) | Operator may submit a new handoff. |
| Last handoff in `submitted` or `accepted` state | `false` | `true` | Handoff in flight; operator may withdraw. |
| Last handoff in `withdrawn` state | `true` | `false` | Prior handoff withdrawn; new submission allowed. |
| Last handoff in `converted` state | `false` | `false` | Governance plane has converted; no further operator action from Agora. |

These are Agora-layer allowed actions only. The BFF may not expose allowed actions that belong to the
Management governance plane (e.g., `approve`, `reject`, `bind_capital`).

**TypeScript type for `DetailEnvelope` (add to `tradingRoom.ts`):**

```ts
// DetailEnvelope wrapper — returned by GET .../trading-intents/{id}
export interface TradingIntentDetailEnvelope {
  object_ref: { type: "trading_intent"; id: string };
  status: string;
  lifecycle_state?: string;
  allowedActions: {
    submit_handoff?: boolean;
    withdraw?: boolean;
    [key: string]: boolean | undefined;
  };
  meta: Record<string, unknown>;
  links: Record<string, string>;
  data: TradingIntentDetail;
}

// Data payload inside the envelope
export interface TradingIntentDetail {
  intent: TradingIntent;
  handoffs: GovernedIntentHandoff[];
}
```

**Degraded response shape (when intent record unavailable):**

When the intent read store is unavailable, the BFF must return a typed blocked envelope, not a
`500` or fixture data:

```json
{
  "object_ref": { "type": "trading_intent", "id": "<intent_id>" },
  "status": "unavailable",
  "lifecycle_state": null,
  "allowedActions": {},
  "meta": { "degraded": true, "reason": "intent_store_unavailable" },
  "links": { "self": "/bff/agora/trading-intents/<intent_id>" },
  "data": null
}
```

The `data: null` here is technically outside the required schema (which requires `data` present), so
the BFF should use `data: {}` or emit the `ErrorEnvelope` schema instead. Owner should decide which
error shape to use at the degraded path; `ErrorEnvelope` is recommended for consistency with other
BFF routes.

---

## BFF Test Skeleton Supplement

This section provides a concrete test file starting point for the AG-BE-TR-002 acceptance tests.
It uses the same patterns as `test_cw03_committee_board_contract.py` and `conftest.py`.

```python
# services/control-plane/bff/test_tr002_governed_handoff_contract.py
"""
AG-BE-TR-002 acceptance tests: Governed TradingIntent handoff contract.

Uses the same _seeded_client() pattern as test_cw03_committee_board_contract.py.
Stub tokens via PANTHEON_BFF_AUTH_STUB=true (set automatically by conftest.py).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore

OPERATOR_AUTH = "Bearer test-operator:operator"

# Minimal valid GovernedIntentHandoff submission body.
# Caller must supply intent_id, strategy_id, strategy_spec_registry_id.
def _handoff_body(intent_id: str, stage: str = "shadow") -> dict:
    stage_to_type = {
        "shadow": "shadow_start",
        "paper": "paper_validation_request",
        "canary": "promotion_review_request",
        "live": "promotion_review_request",
    }
    return {
        "spec_version": "1.0",
        "intent_id": intent_id,
        "requested_stage": stage,
        "handoff_type": stage_to_type[stage],
        "strategy_id": "strat-001",
        "strategy_spec_registry_id": "reg-001",
        "requested_by": {
            "actor_type": "trader",
            "actor_ref": "test-operator",
        },
        "evidence_refs": [
            {"ref_type": "evidence_bundle", "ref_id": "eb-001"}
        ],
        "no_order_route_proof": "agora_request_only_no_order_route",
    }


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
        # Seed a TradingIntent record into the read store if provided.
        if intent_fixture:
            bff_main.read_store.upsert(
                object_type="trading_intent",
                object_id=intent_fixture["intent_id"],
                data=intent_fixture,
            )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store


# --- Test: submit shadow handoff returns 202 with server-derived target_queue ---

def test_submit_handoff_shadow_returns_202() -> None:
    intent_id = "intent-test-shadow-001"
    with _seeded_client() as client:
        resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-shadow-001",
            },
            json=_handoff_body(intent_id, "shadow"),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "handoff_id" in body
        # target_queue must be server-derived, not in the 202 body directly.
        # Read the created record to verify target_queue.
        get_resp = client.get(
            f"/bff/agora/trading-intents/{intent_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert get_resp.status_code == 200
        handoffs = get_resp.json()["data"]["handoffs"]
        assert any(
            h.get("target_queue") == "shadow_research"
            and h.get("state") == "submitted"
            for h in handoffs
        ), handoffs


def test_submit_handoff_paper_routes_management_governance() -> None:
    intent_id = "intent-test-paper-001"
    with _seeded_client() as client:
        resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-paper-001",
            },
            json=_handoff_body(intent_id, "paper"),
        )
        assert resp.status_code == 202, resp.text
        get_resp = client.get(
            f"/bff/agora/trading-intents/{intent_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        handoffs = get_resp.json()["data"]["handoffs"]
        assert any(h.get("target_queue") == "management_governance" for h in handoffs)


def test_submit_handoff_missing_idempotency_key_returns_422() -> None:
    intent_id = "intent-test-missing-idem-001"
    with _seeded_client() as client:
        resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                # Intentionally omitting Idempotency-Key
            },
            json=_handoff_body(intent_id),
        )
        assert resp.status_code == 422, resp.text


def test_submit_handoff_idempotency_replay() -> None:
    intent_id = "intent-test-replay-001"
    with _seeded_client() as client:
        body = _handoff_body(intent_id)
        headers = {
            "Authorization": OPERATOR_AUTH,
            "If-Match": '"v0"',
            "Idempotency-Key": "idem-replay-001",
        }
        r1 = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers=headers,
            json=body,
        )
        assert r1.status_code == 202
        r2 = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers=headers,
            json=body,
        )
        assert r2.status_code == 202
        assert r1.json()["handoff_id"] == r2.json()["handoff_id"]


def test_submit_handoff_idempotency_conflict_returns_409() -> None:
    intent_id = "intent-test-conflict-001"
    with _seeded_client() as client:
        r1 = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-conflict-001",
            },
            json=_handoff_body(intent_id, "shadow"),
        )
        assert r1.status_code == 202
        # Same key, different body (different stage).
        r2 = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-conflict-001",
            },
            json=_handoff_body(intent_id, "paper"),   # body differs
        )
        assert r2.status_code == 409, r2.text


def test_submit_handoff_no_order_route_proof_wrong_value_returns_422() -> None:
    intent_id = "intent-test-proof-001"
    with _seeded_client() as client:
        body = _handoff_body(intent_id)
        body["no_order_route_proof"] = "WRONG_VALUE"
        resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-proof-001",
            },
            json=body,
        )
        assert resp.status_code == 422, resp.text


def test_submit_handoff_management_refs_absent_from_response() -> None:
    intent_id = "intent-test-mgmtrefs-001"
    with _seeded_client() as client:
        resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-mgmtrefs-001",
            },
            json=_handoff_body(intent_id),
        )
        assert resp.status_code == 202
        get_resp = client.get(
            f"/bff/agora/trading-intents/{intent_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        handoffs = get_resp.json()["data"]["handoffs"]
        for h in handoffs:
            assert "management_handoff_ref" not in h, h
            assert "deployment_plan_ref" not in h, h
            assert "runtime_binding_ref" not in h, h


def test_withdraw_sets_withdrawn_state() -> None:
    intent_id = "intent-test-withdraw-001"
    with _seeded_client() as client:
        client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v0"',
                "Idempotency-Key": "idem-withdraw-submit-001",
            },
            json=_handoff_body(intent_id),
        )
        w_resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/withdraw",
            headers={
                "Authorization": OPERATOR_AUTH,
                "If-Match": '"v1"',
                "Idempotency-Key": "idem-withdraw-001",
            },
        )
        assert w_resp.status_code == 200, w_resp.text
        get_resp = client.get(
            f"/bff/agora/trading-intents/{intent_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        handoffs = get_resp.json()["data"]["handoffs"]
        assert any(h.get("state") == "withdrawn" for h in handoffs), handoffs


def test_get_trading_intent_returns_detail_envelope() -> None:
    intent_id = "intent-test-get-001"
    with _seeded_client() as client:
        resp = client.get(
            f"/bff/agora/trading-intents/{intent_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["object_ref"]["type"] == "trading_intent"
        assert body["object_ref"]["id"] == intent_id
        assert "status" in body
        assert "allowedActions" in body
        assert "data" in body
```

**Notes on the test skeleton:**

- The `_seeded_client()` context manager follows the same pattern as `test_cw03_committee_board_contract.py`: it replaces `bff_main.read_store` and `bff_main.command_store` with temp-dir instances.
- Tests that call `GET .../trading-intents/{id}` assume the BFF's read store is populated during the handoff submission. If the BFF uses a separate intent store (not the command store), the test must also seed that store with a stub `TradingIntent` record.
- The `_seeded_client(intent_fixture=...)` parameter covers the case where a `TradingIntent` must already exist before submitting a handoff. Adjust the `upsert()` call to match the actual `ReadSurfaceStore` API.
- Schema validation tests (against `governed_intent_handoff.schema.json`) should call `python3 -m json.tool` or `jsonschema.validate()` on the stored handoff object, not just on the BFF response body.

---

## Operator Journey J: Post-Governance State Observation

Journeys A–I were documented in the original packet. This journey covers the state the operator
sees after the Management governance plane processes a handoff.

### Journey J: Operator Observes Handoff State After Governance Action

1. Operator submitted a governed handoff (Journey A/B/C) and received `202`. The handoff is now
   in `state: "submitted"` with `target_queue` set.
2. The Management governance plane processes the handoff asynchronously and updates the record:
   - Accepted: sets `state: "accepted"` (governance approved the handoff for the requested stage).
   - Rejected: sets `state: "rejected"` with a rejection reason.
   - Converted: sets `state: "converted"` and populates `management_handoff_ref` (and optionally
     `deployment_plan_ref` / `runtime_binding_ref`) — these are governance-plane-only writes.
   - Expired: sets `state: "expired"` when the handoff TTL elapses without governance action.
3. Operator navigates back to the intent detail (Journey G).
4. Frontend calls `GET /bff/agora/trading-intents/{intent_id}`.
5. BFF returns the updated `DetailEnvelope` reflecting the governance-plane-written state:
   - `status` = new state string (`"accepted"`, `"rejected"`, `"converted"`, `"expired"`).
   - `allowedActions.submit_handoff` = `true` if last handoff is `rejected` or `expired`;
     `false` if `accepted` or `converted`.
   - `allowedActions.withdraw` = `false` (no pending submission to withdraw).
   - `data.handoffs[n].management_handoff_ref` may now be populated (written by governance plane).
6. UI renders the updated state:
   - `"accepted"` → "Handoff accepted — awaiting stage transition."
   - `"rejected"` → "Handoff rejected. [reason]. You may submit a new handoff."
   - `"converted"` → "Handoff converted — deployment plan in progress." (D7 wording)
   - `"expired"` → "Handoff expired. You may submit a new handoff."
7. The BFF does not write `management_handoff_ref`, `deployment_plan_ref`, or `runtime_binding_ref`.
   These fields in the handoff record are read-only from Agora's perspective; they are governance
   artifacts surfaced to the operator via the intent detail endpoint.

**BFF read concern:** The BFF's intent read store must be refreshed from the governance plane's
update stream to reflect the governance-plane-written state. The mechanism for this refresh
(push event, poll, or SSE from Management plane → BFF read store) is outside AG-BE-TR-002 scope
and belongs to the Management plane integration contract.

---

## Acceptance Check Addendum (to previous packets)

These checks supplement the acceptance checks from the original packet and FOLLOWUP-2.

| Check | Expected result |
|---|---|
| `IdempotencyRecord` integration | Trading Room router uses `IdempotencyRecord.reserve()` + `CommandStore` for idempotency — not a separate in-process dict. `CommandStore.get_command_by_idempotency_key()` is used for duplicate detection. |
| `DetailEnvelope` shape | `GET .../trading-intents/{id}` response validates against `#/components/schemas/DetailEnvelope`. Required fields: `object_ref`, `status`, `allowedActions`, `meta`, `links`, `data`. |
| `allowedActions` state-driven | `allowedActions.submit_handoff` and `allowedActions.withdraw` are driven by the current handoff state, not hardcoded. Tests cover each relevant state transition. |
| `required_gate_refs` server-derived | `required_gate_refs` is never accepted from the client request body. For `paper`/`canary`/`live` stages, the BFF derives refs from the intent's existing handoff chain. |
| `required_gate_refs` absent for shadow | Shadow handoff submissions have no `required_gate_refs` in the stored record (omitted, not `null`). |
| Governance-plane-only fields read-only | `management_handoff_ref`, `deployment_plan_ref`, and `runtime_binding_ref` are never written by the BFF. They appear in `GET` responses only when written by the Management governance plane. |
| `DetailEnvelope.data` typed as `TradingIntentDetail` | BFF `GET` response `data` field contains `{intent: TradingIntent, handoffs: GovernedIntentHandoff[]}`. Not a raw/flat object. |
| Journey J state surfaced correctly | After governance-plane state transition, `GET .../trading-intents/{id}` returns updated `status` and `allowedActions` reflecting the new handoff state. |
| No live routing in Journey J | Even after `state: "converted"`, the BFF never creates a broker order, writes a RuntimeBinding, or binds capital. The converted state is observed, not acted on. |

---

## Remaining Open Questions

| # | Question | Default if not resolved |
|---|---|---|
| Q5 | What `CommandType` enum value should be used for `submit_governed_handoff`? The existing `CommandType` enum in `models.py` may not include trading-room command types. Owner must add or map. | Add `SUBMIT_GOVERNED_HANDOFF = "submit_governed_handoff"` and `WITHDRAW_TRADING_INTENT = "withdraw_trading_intent"` to `CommandType` in `models.py` (owner task). |
| Q6 | What `ObjectType` enum value and `TargetObject` type are used for a `GovernedIntentHandoff` command target? | Add `TRADING_INTENT = "trading_intent"` and `GOVERNED_HANDOFF = "governed_handoff"` to `ObjectType`. Owner decision. |
| Q7 | Does the BFF's `ReadSurfaceStore` expose a method to look up `TradingIntent` records and their handoff chains by `intent_id`? The current `ReadSurfaceStore` interface (from `read_store.py`) must support intent + handoff read. | Owner must confirm `ReadSurfaceStore.get(object_type="trading_intent", object_id=intent_id)` returns the intent with its handoffs, or implement a separate intent-handoff read adapter. |
| Q8 | Is the Management governance plane expected to push handoff state updates back to the BFF's read store? If not, the BFF's `GET .../trading-intents/{id}` will always show `state: "submitted"` even after governance action. | This is a Management plane integration contract item. Owner should open a handoff blocker with the Management plane owner if the push mechanism is not yet defined. |

---

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed. |
| Q1 resolution accuracy | `IdempotencyRecord.reserve()` interface matches `services/foundation/idempotency.py`; `CommandStore.get_command_by_idempotency_key()` interface matches `services/control-plane/bff/command_queue.py`. Factory signature change (`command_store`, `read_store`) is additive only. |
| Q2 resolution accuracy | `CommandStore` has no built-in TTL; keys are eternal for the file's lifetime. The three options (no TTL, 24h TTL, session-scoped) are correctly characterized. |
| Q3 resolution accuracy | `required_gate_refs` is correctly identified as a server-derived field; population policy for each `requested_stage` is a reasonable default that the owner may refine. |
| Q4 resolution accuracy | `DetailEnvelope` required fields (`object_ref`, `status`, `allowedActions`, `meta`, `links`, `data`) confirmed against `agora_v1_3.openapi.yaml` lines 132–154. `data` field is freeform — `TradingIntentDetail` mapped correctly. |
| `allowedActions` mapping | The `submit_handoff` / `withdraw` mapping table covers the defined `state` enum values (`submitted`, `accepted`, `rejected`, `expired`, `withdrawn`, `converted`). No invented states. |
| Test skeleton patterns | Test file uses the same `_seeded_client()` context manager and `conftest.py` stub-auth pattern as existing BFF tests. No new test infrastructure invented. |
| Journey J accuracy | Journey J does not add any Agora-side write path. Governance-plane state transitions are read-only from the BFF perspective. No broker orders, RuntimeBindings, or capital bindings are created in Journey J. |
| Status accuracy | `AG-BE-TR-002` is `todo`; `AG-BE-TR-001` is `todo` (blocked on `AG-BE-CP-001`); FOLLOWUP-2 is `done` (archived `2026-06-21T21:32:12Z`). |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

**Recommended reviewer approval command:**

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Followup-3 handoff packet approved: resolves Q1 (IdempotencyRecord.reserve()+CommandStore integration pattern confirmed from services/foundation/idempotency.py and command_queue.py), Q2 (no built-in TTL in CommandStore; three options documented), Q3 (required_gate_refs server-derived per stage; never client-supplied), Q4 (DetailEnvelope shape confirmed from agora_v1_3.openapi.yaml lines 132-154; TradingIntentDetail under data field). Adds allowedActions state mapping, DetailEnvelope TypeScript type, BFF test skeleton supplement using existing conftest.py patterns, Journey J (post-governance state observation, no new write paths). No canonical truth, schemas, OpenAPI, BFF runtime, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Followup-3 BFF/frontend handoff packet approved for parent owner absorption."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual error, scope issue, or missing context requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

git status --short
# A  support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
# ?? .orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff_followup_3.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
# status: in_progress, owner: Claude, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002
# status: todo, owner: Codex, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# status: todo, owner: Claude2, reviewer: Codex; depends_on AG-BE-CP-001 (blocked)

# Confirmed IdempotencyRecord class at services/foundation/idempotency.py:28
# Confirmed IdempotencyRecord.reserve() signature (idempotency_key, operation_type, target_ref, request_payload, trace_id)
# Confirmed IdempotencyRecord.matches_request() for conflict detection

# Confirmed CommandStore.get_command_by_idempotency_key() at command_queue.py
# Confirmed DetailEnvelope schema at agora_v1_3.openapi.yaml lines 132-154
# Required: [object_ref, status, allowedActions, meta, links, data]; additionalProperties: false

# Confirmed governed_intent_handoff.schema.json properties:
# python3 -c "import json; s=json.load(open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json')); print(list(s['properties'].keys()))"
# ['spec_version', 'handoff_id', 'intent_id', 'decision_event_id', 'requested_stage',
#  'handoff_type', 'state', 'strategy_id', 'strategy_spec_registry_id', 'requested_by',
#  'target_queue', 'required_gate_refs', 'action_proposal', 'rationale', 'risk_summary',
#  'evidence_refs', 'management_handoff_ref', 'deployment_plan_ref', 'runtime_binding_ref',
#  'no_order_route_proof', 'created_at', 'updated_at', 'expires_at']

# Confirmed required_gate_refs type: {type: array, items: {type: string}} (no additionalProperties restriction)
# Confirmed trading_room/router.py is still placeholder (returns empty APIRouter)
```
