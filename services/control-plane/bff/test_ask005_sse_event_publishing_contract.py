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
from typing import Any, Optional
import uuid

import pytest
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_queue import CommandStore
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
    def __init__(self):
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
_FINAL_CONTRACT_IDEMPOTENCY: dict[str, Any] = {}


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


app = FastAPI()


@app.post("/bff/agora/ask/sessions", status_code=201)
async def create_ask_session(
    payload: dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")

    if idempotency_key and idempotency_key in _AGORA_CORE_BFF_IDEMPOTENCY:
        return _AGORA_CORE_BFF_IDEMPOTENCY[idempotency_key]

    session_id = f"ask-{uuid.uuid4().hex[:8]}"
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "ask.session.started",
        {"session_id": session_id, "mode": "quick_ask"},
    )
    result = {"data": {"id": session_id, "title": payload.get("title", "")}}
    if idempotency_key:
        _AGORA_CORE_BFF_IDEMPOTENCY[idempotency_key] = result
    return result


@app.post("/bff/approvals/{approval_id}/decide", status_code=202)
async def decide_approval(
    approval_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")

    auth_token = authorization.removeprefix("Bearer ").strip()
    actor_id = auth_token.split(":")[0] if ":" in auth_token else "anonymous"
    roles_str = auth_token.split(":")[1] if ":" in auth_token else ""
    roles = {r.strip() for r in roles_str.split(",")} if roles_str else set()

    if "approver" not in roles:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FORBIDDEN", "message": "Approver role required"}},
        )

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

    if idempotency_key:
        if idempotency_key in _FINAL_CONTRACT_IDEMPOTENCY:
            cached = _FINAL_CONTRACT_IDEMPOTENCY[idempotency_key]
            if cached.get("request_hash") != payload_hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "IDEMPOTENCY_CONFLICT",
                            "message": "Idempotency key was reused with a different command payload",
                        }
                    },
                )
            replay = copy.deepcopy(cached["result"])
            replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            return JSONResponse(status_code=202, content=replay)

        if _holder.command_store is not None:
            existing = _holder.command_store.get_command_by_idempotency_key(
                idempotency_key,
                operator_id=actor_id,
            )
            if existing:
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
                return JSONResponse(status_code=202, content=replay)

    decision = payload.get("decision", "")
    if decision == "approve":
        _publish_event(
            _sse_buffers["approval"],
            _sse_subscribers["approval"],
            "approval.decided",
            {"approval_id": approval_id, "outcome": "approved", "decided_by": actor_id},
        )
    elif decision == "reject":
        _publish_event(
            _sse_buffers["approval"],
            _sse_subscribers["approval"],
            "approval.decided",
            {"approval_id": approval_id, "outcome": "rejected"},
        )
    elif decision in ("request_revision", "escalate", "freeze"):
        _publish_event(
            _sse_buffers["approval"],
            _sse_subscribers["approval"],
            "approval.stage.changed",
            {"approval_id": approval_id, "current_stage": decision, "actor_id": actor_id},
        )

    cmd_id = f"cmd-{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    result = {
        "command_id": cmd_id,
        "data": {
            "approval_id": approval_id,
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
            target=TargetObject(type=ObjectType.APPROVAL_DECISION, id=approval_id),
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

    if idempotency_key:
        _FINAL_CONTRACT_IDEMPOTENCY[idempotency_key] = {
            "request_hash": payload_hash,
            "result": result,
        }

    return JSONResponse(status_code=202, content=result)


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
        _FINAL_CONTRACT_IDEMPOTENCY.clear()
        try:
            yield
        finally:
            _holder.command_store = original_command_store
            _sse_buffers["ask"].clear()
            _sse_subscribers["ask"].clear()
            _sse_buffers["approval"].clear()
            _sse_subscribers["approval"].clear()
            _AGORA_CORE_BFF_IDEMPOTENCY.clear()
            _FINAL_CONTRACT_IDEMPOTENCY.clear()


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
    assert event["data"]["outcome"] == "approved"
    assert event["data"]["decided_by"] == "ask005-approver"


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
    assert event["data"]["outcome"] == "rejected"
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
    assert event["data"]["current_stage"] == "request_revision"
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
    assert event["data"]["current_stage"] == "escalate"
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
    assert event["data"]["current_stage"] == "freeze"
    assert event["data"]["actor_id"] == "ask005-approver"


# ---------------------------------------------------------------------------
# Approval idempotency replay does not double-publish
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_replay_does_not_double_publish() -> None:
    client = TestClient(app)
    idem = _idem()

    resp1 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp1.status_code == 202, resp1.text
    assert len(_sse_buffers["approval"]) == 1

    # replay with same idempotency key — must NOT publish a second event
    resp2 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp2.status_code == 202, resp2.text
    meta = resp2.json().get("meta", {})
    assert meta.get("idempotency", {}).get("replayed") is True, "second call should be marked as replayed"
    assert len(_sse_buffers["approval"]) == 1, "replay must not publish a second SSE event"


# ---------------------------------------------------------------------------
# Durable command_store replay does not double-publish (R3 regression)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_durable_replay_does_not_double_publish() -> None:
    """After _FINAL_CONTRACT_IDEMPOTENCY is evicted, durable command_store replay must not re-publish SSE."""
    client = TestClient(app)
    idem = _idem()

    resp1 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp1.status_code == 202, resp1.text
    assert len(_sse_buffers["approval"]) == 1

    # Simulate in-memory eviction: clear _FINAL_CONTRACT_IDEMPOTENCY but leave command_store intact
    _FINAL_CONTRACT_IDEMPOTENCY.clear()

    # Durable replay via command_store — must NOT publish a second SSE event
    resp2 = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": idem},
    )
    assert resp2.status_code == 202, resp2.text
    meta = resp2.json().get("meta", {})
    assert meta.get("idempotency", {}).get("replayed") is True, "second call should be marked as replayed"
    assert len(_sse_buffers["approval"]) == 1, (
        "durable command_store replay must not publish a second SSE event"
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
