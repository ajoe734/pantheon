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

import os
import sys
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main

OPERATOR_HEADERS = {"Authorization": "Bearer ask005-op:operator,approver"}
APPROVER_HEADERS = {"Authorization": "Bearer ask005-approver:approver"}
PENDING_APPROVAL_ID = "appr-dec-c5a9f11e"


def _idem() -> str:
    return f"ask005-{uuid.uuid4().hex[:16]}"


@pytest.fixture(autouse=True)
def clear_sse_buffers():
    bff_main._sse_buffers["ask"].clear()
    bff_main._sse_subscribers["ask"].clear()
    bff_main._sse_buffers["approval"].clear()
    bff_main._sse_subscribers["approval"].clear()
    bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
    bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
    yield
    bff_main._sse_buffers["ask"].clear()
    bff_main._sse_subscribers["ask"].clear()
    bff_main._sse_buffers["approval"].clear()
    bff_main._sse_subscribers["approval"].clear()


# ---------------------------------------------------------------------------
# ask.session.started
# ---------------------------------------------------------------------------

def test_create_ask_session_publishes_ask_session_started() -> None:
    client = TestClient(bff_main.app)
    assert len(bff_main._sse_buffers["ask"]) == 0

    resp = client.post(
        "/bff/agora/ask/sessions",
        json={"title": "Why did the signal fire?"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["data"]["id"]

    assert len(bff_main._sse_buffers["ask"]) == 1
    event_id, event = bff_main._sse_buffers["ask"][0]
    assert event["type"] == "ask.session.started"
    assert event["data"]["session_id"] == session_id
    assert event["data"]["mode"] == "quick_ask"


def test_create_ask_session_idempotency_replay_does_not_double_publish() -> None:
    client = TestClient(bff_main.app)
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

    assert len(bff_main._sse_buffers["ask"]) == 1


# ---------------------------------------------------------------------------
# approval.decided (approve)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_approve_publishes_approval_decided() -> None:
    client = TestClient(bff_main.app)
    assert len(bff_main._sse_buffers["approval"]) == 0

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(bff_main._sse_buffers["approval"]) == 1
    event_id, event = bff_main._sse_buffers["approval"][0]
    assert event["type"] == "approval.decided"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID
    assert event["data"]["outcome"] == "approved"
    assert event["data"]["decided_by"] == "ask005-approver"


# ---------------------------------------------------------------------------
# approval.decided (reject)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_reject_publishes_approval_decided_rejected() -> None:
    client = TestClient(bff_main.app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "reject", "rejection_reason": "Risk threshold exceeded"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(bff_main._sse_buffers["approval"]) == 1
    event_id, event = bff_main._sse_buffers["approval"][0]
    assert event["type"] == "approval.decided"
    assert event["data"]["outcome"] == "rejected"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID


# ---------------------------------------------------------------------------
# approval.stage.changed (request_revision)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_request_revision_publishes_stage_changed() -> None:
    client = TestClient(bff_main.app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "request_revision", "revision_notes": "Please attach more evidence"},
        headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
    )
    assert resp.status_code == 202, resp.text

    assert len(bff_main._sse_buffers["approval"]) == 1
    event_id, event = bff_main._sse_buffers["approval"][0]
    assert event["type"] == "approval.stage.changed"
    assert event["data"]["approval_id"] == PENDING_APPROVAL_ID
    assert event["data"]["current_stage"] == "request_revision"
    assert event["data"]["actor_id"] == "ask005-approver"


# ---------------------------------------------------------------------------
# No approval event on role gate failure
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_role_gate_failure_does_not_publish() -> None:
    client = TestClient(bff_main.app)

    resp = client.post(
        f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
        json={"decision": "approve"},
        headers={"Authorization": "Bearer ask005-op:operator"},
    )
    assert resp.status_code == 403, resp.text
    assert len(bff_main._sse_buffers["approval"]) == 0
