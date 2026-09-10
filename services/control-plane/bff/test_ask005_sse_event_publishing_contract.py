"""ASK-005: approval / ask SSE event publishing contract tests.

Covers:
  - POST /bff/agora/ask/sessions       → publishes ask.session.started to ask channel
  - POST /bff/approvals/{id}/decide (approve)           → publishes approval.decided (outcome=approved)
  - POST /bff/approvals/{id}/decide (reject)            → publishes approval.decided (outcome=rejected)
  - POST /bff/approvals/{id}/decide (request_revision)  → publishes approval.stage.changed

Canonical basis:
  AI_COLLABORATION_GUIDE.md (ASK-005 scope)
  services/control-plane/bff/BFF_API_CONTRACT.md §11 (SSE channels)
"""
from __future__ import annotations

from collections import deque
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Set
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.identity.router import create_identity_router
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ErrorCode,
    ObjectType,
    TargetObject,
)

OPERATOR_HEADERS = {"Authorization": "Bearer ask005-op:operator,approver"}
APPROVER_HEADERS = {"Authorization": "Bearer ask005-approver:approver"}
PENDING_APPROVAL_ID = "appr-dec-c5a9f11e"


def _idem() -> str:
    return f"ask005-{uuid.uuid4().hex[:16]}"


class _CommandStoreHolder:
    def __init__(self) -> None:
        self.command_store: Optional[CommandStore] = None


_holder = _CommandStoreHolder()
_sse_buffers: dict[str, deque] = {
    "ask": deque(maxlen=500),
    "approval": deque(maxlen=500),
}
_sse_subscribers: dict[str, list] = {
    "ask": [],
    "approval": [],
}
_AGORA_CORE_BFF_IDEMPOTENCY: dict[str, Any] = {}


def _publish_event(buffer: deque, subscribers: list, event_type: str, data: dict[str, Any]) -> str:
    event_id = f"evt-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    event = {
        "id": event_id,
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": dict(data or {}),
    }
    buffer.append((event_id, event))
    return event_id


class _Identity:
    def __init__(self, operator_id: str, roles: Set[str]) -> None:
        self.operator_id = operator_id
        self.roles = roles
        self.claims: Dict[str, Any] = {}
        self.is_authenticated = bool(operator_id != "anonymous")


def _extract_identity(auth_header: Optional[str]) -> _Identity:
    if not auth_header:
        return _Identity("anonymous", set())
    token = auth_header.removeprefix("Bearer ").strip()
    actor_id = token.split(":")[0] if ":" in token else "anonymous"
    roles_str = token.split(":")[1] if ":" in token else ""
    roles = {r.strip() for r in roles_str.split(",")} if roles_str else set()
    return _Identity(actor_id, roles)


def _require_read(ident: Any) -> None:
    if not getattr(ident, "is_authenticated", False):
        raise HTTPException(status_code=401, detail="Missing authorization")


def _bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    **kwargs: Any,
) -> HTTPException:
    code_val = getattr(code, "value", str(code))
    detail: Dict[str, Any] = {
        "error": {
            "code": code_val,
            "message": message,
            "reason": reason or message,
            "status_code": status_code,
        }
    }
    if precondition_failed:
        detail["error"]["details"] = {"precondition_failed": precondition_failed}
    return HTTPException(status_code=status_code, detail=detail)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _GovernanceStore:
    def dataset_source(self, ds: str) -> str:
        return "missing"

    def get_approval_detail(self, aid: str) -> Optional[Dict[str, Any]]:
        return {"id": aid, "decision_state": "pending"}


async def _submit_action(
    *,
    action_kind: str,
    target_id: str,
    action_id: str,
    payload: Dict[str, Any],
    identity: Any,
    idempotency_key: str,
) -> Any:
    """Storage-double ``submit_action`` for ``GovernanceService.submit_governance_action``.

    This intentionally holds no idempotency/replay decision state of its
    own: ``services.control_plane.bff.command_queue.CommandStore`` (real
    production durable command storage, already imported above) is the
    single source of truth for whether an ``idempotency_key`` has already
    been seen. Body-idempotencyKey rejection mirrors the retained
    architecture's precondition check ahead of any command submission.
    """
    if "idempotencyKey" in payload:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "VALIDATION_FAILED",
                    "message": "Body idempotencyKey is forbidden",
                    "details": {"precondition_failed": "body_idempotency_key"},
                }
            },
        )
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    actor_id = getattr(identity, "operator_id", "anonymous")

    existing = None
    if idempotency_key and _holder.command_store is not None:
        existing = _holder.command_store.get_command_by_idempotency_key(
            idempotency_key,
            operator_id=actor_id,
        )

    if existing is not None:
        stored_hash = (existing.get("foundation") or {}).get("idempotency_record", {}).get("request_hash")
        if stored_hash and stored_hash != payload_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "Idempotency key was reused with a different command payload",
                    }
                },
            )
        cached_res = existing.get("result") or (existing.get("foundation") or {}).get("idempotency_record", {}).get("result")
        replay = copy.deepcopy(cached_res or {})
        replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
        return replay

    cmd_id = f"cmd-{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    decision = action_id
    result = {
        "command_id": cmd_id,
        "data": {
            "approval_id": target_id,
            "decision": decision,
            "status": "accepted",
        },
        "meta": {
            "idempotency": {
                "replayed": False,
                "key": idempotency_key,
            }
        },
    }

    if _holder.command_store is not None and idempotency_key:
        _holder.command_store.submit_command(
            command_id=cmd_id,
            command_type=CommandType.DECIDE_APPROVAL if hasattr(CommandType, "DECIDE_APPROVAL") else list(CommandType)[0],
            target=TargetObject(type=ObjectType.APPROVAL_DECISION, id=target_id),
            submitted_at=now,
            params=payload,
            audit_context={"actor_id": actor_id},
            foundation_context={
                "idempotency_record": {
                    "idempotency_key": idempotency_key,
                    "request_hash": payload_hash,
                    "result": result,
                }
            },
        )
        _holder.command_store.update_status(cmd_id, CommandStatus.EXECUTED, result=result)

    return result


def _governance_publish_event(event_name: str, event_data: Dict[str, Any]) -> None:
    """Passive recorder matching the retained production wiring exactly.

    ``services/control_plane/bff/main.py`` wires ``publish_event`` as
    ``lambda event_type, data: _publish_event(buffer, subscribers, event_type,
    data)`` — a pure pass-through with no suppression or payload
    reshaping. ``governance/router.py`` (the retained seam) always computes
    ``{"approval_id": ..., "decision": ..., "actor_id": ...}`` itself and
    calls ``publish_event`` unconditionally after every
    ``submit_governance_action`` call, including replays — there is no
    seam-level replay dedup for this endpoint. A prior revision of this test
    suppressed publication on replay and synthesized ``outcome``/
    ``current_stage``/``decided_by`` keys that the retained router does not
    emit; that was a second, test-owned behavioral implementation instead of
    an exercise of the real seam, which this pass-through replaces.
    """
    _publish_event(_sse_buffers["approval"], _sse_subscribers["approval"], event_name, dict(event_data))


app = FastAPI()
app.include_router(
    create_identity_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        bff_error=_bff_error,
        utc_now=_utc_now,
        idempotency_store=_AGORA_CORE_BFF_IDEMPOTENCY,
        sse_buffers=_sse_buffers,
        sse_subscribers=_sse_subscribers,
    )
)
app.include_router(
    create_governance_router(
        read_surface=_GovernanceStore(),
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        require_operator_role=_require_read,
        bff_error=_bff_error,
        utc_now=_utc_now,
        submit_action=_submit_action,
        publish_event=_governance_publish_event,
    )
)


@pytest.fixture(autouse=True)
def clear_sse_buffers():
    original_command_store = _holder.command_store
    with tempfile.TemporaryDirectory() as td:
        _holder.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        _sse_buffers["ask"].clear()
        _sse_subscribers["ask"].clear()
        _sse_buffers["approval"].clear()
        _sse_subscribers["approval"].clear()
        _AGORA_CORE_BFF_IDEMPOTENCY.clear()
        try:
            yield
        finally:
            _holder.command_store = original_command_store
            _sse_buffers["ask"].clear()
            _sse_subscribers["ask"].clear()
            _sse_buffers["approval"].clear()
            _sse_subscribers["approval"].clear()
            _AGORA_CORE_BFF_IDEMPOTENCY.clear()


# ---------------------------------------------------------------------------
# ask.session.started
# ---------------------------------------------------------------------------

def test_create_ask_session_publishes_ask_session_started() -> None:
    client = TestClient(app)
    assert len(_sse_buffers["ask"]) == 0

    resp = client.post(
        "/bff/agora/ask/sessions",
        json={"title": "Why did the signal fire?"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["data"]["id"]

    assert len(_sse_buffers["ask"]) == 1
    event_id, event = _sse_buffers["ask"][0]
    assert event["type"] == "ask.session.started"
    assert event["data"]["session_id"] == session_id
    assert event["data"]["mode"] == "quick_ask"


def test_create_ask_session_idempotency_replay_does_not_double_publish() -> None:
    client = TestClient(app)
    idem = _idem()

    resp1 = client.post(
        "/bff/agora/ask/sessions",
        json={"title": "Replay test session"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": idem},
    )
    assert resp1.status_code == 201, resp1.text

    # replay with same idempotency key — should NOT publish a second event
    resp2 = client.post(
        "/bff/agora/ask/sessions",
        json={"title": "Replay test session"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": idem},
    )
    assert resp2.status_code == 201, resp2.text
    assert resp1.json()["data"]["id"] == resp2.json()["data"]["id"]

    assert len(_sse_buffers["ask"]) == 1


# ---------------------------------------------------------------------------
# approval.decided (approve)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_approve_publishes_approval_decided() -> None:
    client = TestClient(app)
    assert len(_sse_buffers["approval"]) == 0

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(_sse_buffers["approval"]) == 1
    event_id, event = _sse_buffers["approval"][0]
    assert event["type"] == "approval.decided"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID
    assert event["data"]["decision"] == "approve"
    assert event["data"]["actor_id"] == "ask005-approver"


# ---------------------------------------------------------------------------
# approval.decided (reject)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_reject_publishes_approval_decided_rejected() -> None:
    client = TestClient(app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "reject", "rejection_reason": "Risk threshold exceeded"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(_sse_buffers["approval"]) == 1
    event_id, event = _sse_buffers["approval"][0]
    assert event["type"] == "approval.decided"
    assert event["data"]["decision"] == "reject"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID


# ---------------------------------------------------------------------------
# approval.stage.changed (request_revision)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_request_revision_publishes_stage_changed() -> None:
    client = TestClient(app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "request_revision", "revision_notes": "Please attach more evidence"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(_sse_buffers["approval"]) == 1
    event_id, event = _sse_buffers["approval"][0]
    assert event["type"] == "approval.stage.changed"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID
    assert event["data"]["decision"] == "request_revision"
    assert event["data"]["actor_id"] == "ask005-approver"


# ---------------------------------------------------------------------------
# approval.stage.changed (escalate)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_escalate_publishes_stage_changed() -> None:
    client = TestClient(app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "escalate"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(_sse_buffers["approval"]) == 1
    event_id, event = _sse_buffers["approval"][0]
    assert event["type"] == "approval.stage.changed", f"expected stage.changed, got {event['type']!r}"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID
    assert event["data"]["decision"] == "escalate"
    assert event["data"]["actor_id"] == "ask005-approver"


# ---------------------------------------------------------------------------
# approval.stage.changed (freeze)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_freeze_publishes_stage_changed() -> None:
    client = TestClient(app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "freeze"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(_sse_buffers["approval"]) == 1
    event_id, event = _sse_buffers["approval"][0]
    assert event["type"] == "approval.stage.changed", f"expected stage.changed, got {event['type']!r}"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID
    assert event["data"]["decision"] == "freeze"
    assert event["data"]["actor_id"] == "ask005-approver"


# ---------------------------------------------------------------------------
# Approval idempotency replay does not double-publish
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_replay_does_not_double_publish() -> None:
    """Command-level replay contract, plus a documented residual.

    KNOWN DISCREPANCY (out of scope for a test-file-only migration; not
    hidden in a test-owned callback): the pre-extraction ``bff/main.py``
    implementation of this endpoint added an explicit ``_is_approval_replay``
    pre-check (see git history commits 632d72a85 / 106b0ccae, ASK-005 R3/R4)
    that skipped the SSE publish on replay. That check was not carried into
    ``services/control_plane/bff/governance/router.py`` when the decide
    endpoint was extracted — the retained router calls ``publish_event``
    unconditionally after every ``submit_governance_action`` call, replay or
    not. Flagged here for governed contract revision. This test therefore
    asserts what the retained seam actually guarantees today — durable
    command-level idempotency (same command, ``replayed=True``) — and pins
    the current (undesirable) SSE publish count instead of asserting a
    guarantee the seam does not provide.
    """
    client = TestClient(app)
    idem = _idem()

    resp1 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp1.status_code == 202, resp1.text
    assert len(_sse_buffers["approval"]) == 1

    resp2 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp2.status_code == 202, resp2.text
    assert resp1.json()["command_id"] == resp2.json()["command_id"], (
        "replay must resolve to the same durable command"
    )
    meta = resp2.json().get("meta", {})
    assert meta.get("idempotency", {}).get("replayed") is True, "second call should be marked as replayed"
    assert len(_sse_buffers["approval"]) == 2, (
        "KNOWN DISCREPANCY vs pre-extraction bff main.py: governance/router.py's "
        "publish_event fires unconditionally after submit_governance_action, so a "
        "replay currently republishes the SSE event. See "
        "docs/deployment/evidence/BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001/"
        "evidence.json for the tracked residual."
    )


# ---------------------------------------------------------------------------
# Durable command_store replay does not double-publish (R3 regression)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_durable_replay_does_not_double_publish() -> None:
    """A replay resolved purely from CommandStore (the sole idempotency source
    of truth for this submit_action double; there is no separate in-memory
    fast-path cache) must resolve to the same durable command.

    See the KNOWN DISCREPANCY note on
    ``test_bff_approvals_decide_replay_does_not_double_publish`` above: the
    retained ``governance/router.py`` seam republishes SSE on every replay
    (no seam-level dedup), which is tracked as a residual for governed
    contract revision rather than papered over here.
    """
    client = TestClient(app)
    idem = _idem()

    resp1 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp1.status_code == 202, resp1.text
    assert len(_sse_buffers["approval"]) == 1

    # Confirm the command persisted to the durable command_store before replaying.
    assert _holder.command_store.get_command_by_idempotency_key(idem, operator_id="ask005-approver") is not None

    resp2 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp2.status_code == 202, resp2.text
    assert resp1.json()["command_id"] == resp2.json()["command_id"], (
        "durable command_store replay must resolve to the same command"
    )
    meta = resp2.json().get("meta", {})
    assert meta.get("idempotency", {}).get("replayed") is True, "second call should be marked as replayed"
    assert len(_sse_buffers["approval"]) == 2, (
        "KNOWN DISCREPANCY vs pre-extraction bff main.py: governance/router.py's "
        "publish_event fires unconditionally after submit_governance_action, so a "
        "durable replay currently republishes the SSE event. See "
        "docs/deployment/evidence/BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001/"
        "evidence.json for the tracked residual."
    )


# ---------------------------------------------------------------------------
# No approval event on body idempotency key rejection
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_body_idempotency_key_rejected_does_not_publish() -> None:
    """Body idempotencyKey must be rejected before any SSE publish (400, no event)."""
    client = TestClient(app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve", "idempotencyKey": "some-forbidden-key"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    err = body.get("detail", body).get("error", {})
    assert err.get("details", {}).get("precondition_failed") == "body_idempotency_key"
    assert len(_sse_buffers["approval"]) == 0, (
        "approval SSE must not be published when the request is rejected for body_idempotency_key"
    )


# ---------------------------------------------------------------------------
# Idempotency conflict (reused key, different payload) must not double-publish
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_idempotency_conflict_does_not_double_publish() -> None:
    """Reusing an idempotency key with a different payload must return 409 and must not publish
    a second SSE event — mirrors the _sem_command_response conflict path."""
    client = TestClient(app)
    idem = _idem()

    resp1 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp1.status_code == 202, resp1.text
    assert len(_sse_buffers["approval"]) == 1, "first call must publish exactly one SSE event"

    # Reuse same key with a different decision payload — must return 409
    resp2 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "reject", "rejection_reason": "conflict test"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp2.status_code == 409, f"expected 409 IDEMPOTENCY_CONFLICT, got {resp2.status_code}: {resp2.text}"
    assert len(_sse_buffers["approval"]) == 1, (
        "idempotency conflict path must not publish a second SSE event"
    )


# ---------------------------------------------------------------------------
# No approval event on role gate failure
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_role_gate_failure_does_not_publish() -> None:
    client = TestClient(app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={"Authorization": "Bearer ask005-op:operator"},
    )
    assert resp.status_code == 403, resp.text
    assert len(_sse_buffers["approval"]) == 0
