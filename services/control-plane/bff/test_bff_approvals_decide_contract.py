"""
Contract tests for P0-APP-001: POST /bff/approvals/{id}/decide.

Verifies: role gate, decision routing (approve/reject/request_revision),
field validation, 404 when unknown id, idempotency replay, and 202 envelope.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore

APPROVER_HEADERS = {"Authorization": "Bearer op-app001:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-app001-admin:admin"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-app001-op:operator"}
ANON_HEADERS: dict = {}

# fixture id present in data/read_surfaces.json with state=under_review
PENDING_APPROVAL_ID = "appr-dec-c5a9f11e"
# fixture id with state=decided (already resolved, but endpoint still accepts commands)
DECIDED_APPROVAL_ID = "approval-042"
UNKNOWN_ID = "unknown-approval-xyz"


def _fresh_client(td: str, *, allow_fallback: bool = True) -> tuple[TestClient, ReadSurfaceStore]:
    store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=allow_fallback,
    )
    bff_main.read_store = store
    return TestClient(bff_main.app, raise_server_exceptions=False), store


def _idem() -> str:
    return f"test-app001-{uuid.uuid4().hex[:12]}"


def _approver_headers(idem_key: str | None = None) -> dict:
    h = dict(APPROVER_HEADERS)
    if idem_key:
        h["Idempotency-Key"] = idem_key
    return h


def _admin_headers(idem_key: str | None = None) -> dict:
    h = dict(ADMIN_HEADERS)
    if idem_key:
        h["Idempotency-Key"] = idem_key
    return h


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_approver_role_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_admin_role_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers=_admin_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_operator_role_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
            )
            assert resp.status_code == 403, resp.text
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_anonymous_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers=ANON_HEADERS,
            )
            assert resp.status_code in {401, 403}, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Decision routing — approve
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_approve_returns_202_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert "data" in body or "command_id" in body or "status" in body
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_empty_body_defaults_to_approve() -> None:
    """Missing decision field defaults to approve."""
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Decision routing — reject
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_reject_with_reason_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "reject", "rejection_reason": "Risk threshold exceeded"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_reject_without_reason_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "reject"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_reject_empty_reason_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "reject", "rejection_reason": ""},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Decision routing — request_revision
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_request_revision_with_notes_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "request_revision", "revision_notes": "Please add evidence"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
        finally:
            bff_main.read_store = original


def test_bff_approvals_decide_request_revision_without_notes_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "request_revision"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Escalate / freeze (pass-through pending dedicated command types)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_escalate_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "escalate"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 202, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Invalid decision value
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_invalid_decision_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "nonsense_value"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_unknown_id_returns_404_when_source_available() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td, allow_fallback=True)
            resp = client.post(
                f"/bff/approvals/{UNKNOWN_ID}/decide",
                json={"decision": "approve"},
                headers=_approver_headers(_idem()),
            )
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_idempotency_replay_returns_same_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            idem_key = f"test-idem-app001-{uuid.uuid4().hex[:12]}"
            headers = {**APPROVER_HEADERS, "Idempotency-Key": idem_key}
            payload = {"decision": "approve"}

            r1 = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json=payload,
                headers=headers,
            )
            r2 = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json=payload,
                headers=headers,
            )
            assert r1.status_code == 202, r1.text
            assert r2.status_code == 202, r2.text
            b1, b2 = r1.json(), r2.json()
            cmd_id_1 = (b1.get("data") or b1).get("command_id")
            cmd_id_2 = (b2.get("data") or b2).get("command_id")
            if cmd_id_1 and cmd_id_2:
                assert cmd_id_1 == cmd_id_2
        finally:
            bff_main.read_store = original
