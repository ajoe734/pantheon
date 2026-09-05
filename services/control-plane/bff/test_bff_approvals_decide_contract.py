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
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from typing import Any

from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports

APPROVER_HEADERS = {"Authorization": "Bearer op-app001:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-app001-admin:admin"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-app001-op:operator"}
ANON_HEADERS: dict = {}

# fixture id present in data/read_surfaces.json with state=under_review
PENDING_APPROVAL_ID = "appr-dec-c5a9f11e"
# fixture id with state=decided (already resolved, but endpoint still accepts commands)
DECIDED_APPROVAL_ID = "approval-042"
UNKNOWN_ID = "unknown-approval-xyz"


class ApprovalsDecideTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_fallback
        if data is not None:
            self._data = data
        elif allow_fallback:
            self._data = {
                "approval_decisions": {
                    PENDING_APPROVAL_ID: {
                        "id": PENDING_APPROVAL_ID,
                        "decision_id": PENDING_APPROVAL_ID,
                        "approval_id": PENDING_APPROVAL_ID,
                        "status": "pending",
                        "state": "under_review",
                        "scope": "strategy",
                        "target_id": "strat-001",
                    },
                    DECIDED_APPROVAL_ID: {
                        "id": DECIDED_APPROVAL_ID,
                        "decision_id": DECIDED_APPROVAL_ID,
                        "approval_id": DECIDED_APPROVAL_ID,
                        "status": "approved",
                        "state": "decided",
                        "scope": "strategy",
                        "target_id": "strat-002",
                    },
                }
            }
        else:
            self._data = {}

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot" if self._data else "missing"

    def get_approval_decision(self, decision_id: str | None) -> dict[str, Any] | None:
        return self._data.get("approval_decisions", {}).get(str(decision_id or ""))

    def list_approval_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("approval_decisions", {}).values())

    def get_approval(self, approval_id: str | None) -> dict[str, Any] | None:
        return self.get_approval_decision(approval_id)

    def list_approvals(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_approval_decisions(**kwargs)


@pytest.fixture(autouse=True)
def _isolated_command_admission():
    original_command_store = bff_main.command_store
    original_final_idem = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
    original_approval_buffer = list(bff_main._sse_buffers["approval"])
    original_approval_subscribers = list(bff_main._sse_subscribers["approval"])
    with tempfile.TemporaryDirectory() as td:
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_main._sse_buffers["approval"].clear()
        bff_main._sse_subscribers["approval"].clear()
        try:
            yield
        finally:
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idem)
            bff_main._sse_buffers["approval"].clear()
            bff_main._sse_buffers["approval"].extend(original_approval_buffer)
            bff_main._sse_subscribers["approval"].clear()
            bff_main._sse_subscribers["approval"].extend(original_approval_subscribers)


def _fresh_client(td: str, *, allow_fallback: bool = True) -> tuple[TestClient, ApprovalsDecideTestReadPorts]:
    store = ApprovalsDecideTestReadPorts(allow_fallback=allow_fallback)
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


def _error_payload(response) -> dict:
    body = response.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}


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


def test_bff_approvals_decide_second_operator_conflict_does_not_publish_sse() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idem = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        original_approval_buffer = list(bff_main._sse_buffers["approval"])
        original_approval_subscribers = list(bff_main._sse_subscribers["approval"])
        try:
            bff_main.read_store = ApprovalsDecideTestReadPorts(allow_fallback=True)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._sse_buffers["approval"].clear()
            bff_main._sse_subscribers["approval"].clear()
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            first = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers=_approver_headers("approval-race-first"),
            )
            assert first.status_code == 202, first.text
            assert len(bff_main.command_store._get_all_commands()) == 1
            assert len(bff_main._sse_buffers["approval"]) == 1

            second = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "reject", "rejection_reason": "second operator race"},
                headers=_admin_headers("approval-race-second"),
            )
            assert second.status_code == 409, second.text
            error = _error_payload(second)
            assert error["code"] == "RESOURCE_CONFLICT"
            assert error["details"]["precondition_failed"] == "concurrent_safety"
            assert len(bff_main.command_store._get_all_commands()) == 1
            assert len(bff_main._sse_buffers["approval"]) == 1
            assert bff_main._sse_buffers["approval"][0][1]["data"]["decided_by"] == "op-app001"
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idem)
            bff_main._sse_buffers["approval"].clear()
            bff_main._sse_buffers["approval"].extend(original_approval_buffer)
            bff_main._sse_subscribers["approval"].clear()
            bff_main._sse_subscribers["approval"].extend(original_approval_subscribers)


def test_bff_approvals_decide_concurrent_operators_admit_only_one_command_and_one_sse() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idem = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        original_approval_buffer = list(bff_main._sse_buffers["approval"])
        original_approval_subscribers = list(bff_main._sse_subscribers["approval"])
        try:
            bff_main.read_store = ApprovalsDecideTestReadPorts(allow_fallback=True)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._sse_buffers["approval"].clear()
            bff_main._sse_subscribers["approval"].clear()

            def decide(index_and_headers: tuple[int, dict[str, str]]):
                index, headers = index_and_headers
                local_client = TestClient(bff_main.app, raise_server_exceptions=False)
                response = local_client.post(
                    f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                    json={"decision": "approve"},
                    headers={**headers, "Idempotency-Key": f"concurrent-approval-race-{index}"},
                )
                return response.status_code, response.json()

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(decide, enumerate((APPROVER_HEADERS, ADMIN_HEADERS))))

            statuses = sorted(status for status, _body in results)
            assert statuses == [202, 409]
            accepted = [body for status, body in results if status == 202]
            rejected = [body for status, body in results if status == 409]
            assert len(accepted) == 1
            assert len(rejected) == 1
            error = (rejected[0].get("detail") or rejected[0]).get("error")
            assert error["code"] == "RESOURCE_CONFLICT"
            assert error["details"]["precondition_failed"] == "concurrent_safety"

            commands = bff_main.command_store._get_all_commands()
            assert [command["type"] for command in commands] == ["ApproveDecision"]
            assert len(bff_main._sse_buffers["approval"]) == 1
            event = bff_main._sse_buffers["approval"][0][1]
            assert event["type"] == "approval.decided"
            assert event["data"]["decided_by"] in {"op-app001", "op-app001-admin"}
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idem)
            bff_main._sse_buffers["approval"].clear()
            bff_main._sse_buffers["approval"].extend(original_approval_buffer)
            bff_main._sse_subscribers["approval"].clear()
            bff_main._sse_subscribers["approval"].extend(original_approval_subscribers)


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


def test_bff_approvals_decide_request_changes_alias_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client, _ = _fresh_client(td)
            resp = client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "request_changes", "revision_notes": "Please attach more evidence"},
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
# Batch decide
# ---------------------------------------------------------------------------

def test_bff_approvals_batch_decide_accepts_list_and_records_commands() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        try:
            client, _ = _fresh_client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()

            resp = client.post(
                "/bff/approvals/batch-decide",
                json={
                    "decisions": [
                        {"id": PENDING_APPROVAL_ID, "decision": "approve"},
                        {
                            "id": DECIDED_APPROVAL_ID,
                            "decision": "request_changes",
                            "revision_notes": "Attach final operator evidence",
                        },
                    ]
                },
                headers=_approver_headers(_idem()),
            )

            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["summary"] == {"total": 2, "accepted": 2, "failed": 0}
            assert [item["status"] for item in body["results"]] == ["accepted", "accepted"]
            assert [item["id"] for item in body["results"]] == [PENDING_APPROVAL_ID, DECIDED_APPROVAL_ID]

            records = bff_main.command_store._get_all_commands()
            assert [record["type"] for record in records] == ["ApproveDecision", "RequestApprovalRevision"]
            assert [record["target"]["id"] for record in records] == [
                PENDING_APPROVAL_ID,
                DECIDED_APPROVAL_ID,
            ]
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()


def test_bff_approvals_batch_decide_partial_failure_returns_per_item_status() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        try:
            client, _ = _fresh_client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()

            resp = client.post(
                "/bff/approvals/batch-decide",
                json={
                    "decisions": [
                        {"id": PENDING_APPROVAL_ID, "decision": "approve"},
                        {"id": UNKNOWN_ID, "decision": "approve"},
                        {"id": DECIDED_APPROVAL_ID, "decision": "reject"},
                    ]
                },
                headers=_approver_headers(_idem()),
            )

            assert resp.status_code == 207, resp.text
            body = resp.json()
            assert body["status"] == "partial"
            assert body["summary"] == {"total": 3, "accepted": 1, "failed": 2}
            assert [item["status"] for item in body["results"]] == ["accepted", "failed", "failed"]
            assert body["results"][1]["error"]["code"] == "RESOURCE_NOT_FOUND"
            assert body["results"][2]["error"]["code"] == "VALIDATION_FAILED"
            assert bff_main.command_store._get_all_commands()[0]["target"]["id"] == PENDING_APPROVAL_ID
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()


def test_bff_approvals_batch_decide_rejects_body_idempotency_before_commands() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        try:
            client, _ = _fresh_client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()

            resp = client.post(
                "/bff/approvals/batch-decide",
                json={
                    "idempotencyKey": "body-key-must-not-be-used",
                    "decisions": [{"id": PENDING_APPROVAL_ID, "decision": "approve"}],
                },
                headers=_approver_headers(_idem()),
            )

            assert resp.status_code == 400, resp.text
            body = resp.json()
            error = body.get("error") or body.get("detail", {}).get("error")
            assert error["details"]["precondition_failed"] == "body_idempotency_key"
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()


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
