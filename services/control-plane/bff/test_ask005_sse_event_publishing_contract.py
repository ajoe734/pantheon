"""ASK-005: approval / ask SSE event publishing contract tests.

Covers:
  - POST /bff/agora/ask/sessions       → publishes ask.session.started to ask channel
  - POST /bff/approvals/{id}/decide → publishes only after the Governance owner accepted the vote:
    approval.decided when the owner decided, approval.stage.changed while it stays under_review

Canonical basis:
  AI_COLLABORATION_GUIDE.md (ASK-005 scope)
  services/control-plane/bff/BFF_API_CONTRACT.md §11 (SSE channels)
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
import tempfile
import time
from typing import Any, Dict, Optional, Set
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.identity.router import create_identity_router
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.governance.router import create_governance_router

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
    def __init__(self, operator_id: str, roles: Set[str], tenant_id: str = "tenant-a") -> None:
        self.operator_id = operator_id
        self.roles = roles
        self.claims: Dict[str, Any] = {"tenant_id": tenant_id}
        self.is_authenticated = bool(operator_id != "anonymous")


def _extract_identity(auth_header: Optional[str]) -> _Identity:
    if not auth_header:
        return _Identity("anonymous", set())
    token = auth_header.removeprefix("Bearer ").strip()
    parts = token.split(":")
    actor_id = parts[0] if parts else "anonymous"
    roles_str = parts[1] if len(parts) > 1 else ""
    roles = {r.strip() for r in roles_str.split(",")} if roles_str else set()
    tenant_id = parts[2] if len(parts) > 2 else "tenant-a"
    return _Identity(actor_id, roles, tenant_id=tenant_id)


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
# approval events follow the Governance owner's result
# ---------------------------------------------------------------------------

def _vote(owner_state: str, monkeypatch, **body: Any):
    from services.control_plane.bff.governance import approval_owner

    monkeypatch.setattr(
        approval_owner, "decide",
        lambda authorization, decision_id, params, key: {"decision_id": decision_id, "decision_state": owner_state, "version": 2},
    )
    return TestClient(app).post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve", "memo": "reviewed", "expected_version": 1, **body},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )


def test_owner_decided_vote_publishes_approval_decided(monkeypatch) -> None:
    assert _vote("decided", monkeypatch).status_code == 202
    (_, event), = _sse_buffers["approval"]
    assert event["type"] == "approval.decided"
    assert event["data"] == {"approval_id": PENDING_APPROVAL_ID, "decision_state": "decided", "version": 2, "actor_id": "ask005-approver"}


def test_first_vote_publishes_stage_changed_not_decided(monkeypatch) -> None:
    assert _vote("under_review", monkeypatch).status_code == 202
    assert [event["type"] for _, event in _sse_buffers["approval"]] == ["approval.stage.changed"]


def test_unsupported_or_owner_rejected_votes_publish_nothing() -> None:
    client = TestClient(app)
    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "request_revision", "memo": "reviewed", "expected_version": 1},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 501
    assert len(_sse_buffers["approval"]) == 0
