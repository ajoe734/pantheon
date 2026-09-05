from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from command_queue import CommandStore


OPERATOR_TOKEN = "Bearer op-2:operator"
APPROVER_TOKEN = "Bearer op-6:approver"
ADMIN_MFA_TOKEN = "Bearer op-admin:admin:mfa"


async def _noop_process_command(_command_id: str) -> None:
    return None


@contextmanager
def _isolated_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def _assert_precondition_error(
    response,
    *,
    status_code: int,
    code: str,
    action_id: str,
    entity_type: str,
    entity_id: str,
    kind: str,
    correlation_id: str,
) -> None:
    assert response.status_code == status_code, response.text
    detail = response.json()
    assert "detail" not in detail
    assert detail["meta"]["correlationId"] == correlation_id
    error = detail["error"]
    details = error["details"]
    assert error["code"] == code
    assert details["actionId"] == action_id
    assert details["entityType"] == entity_type
    assert details["entityId"] == entity_id
    assert details["kind"] == kind
    assert details["reason"]
    assert "correlationId" not in details
    assert detail["foundation_error"]["error_code"] == code
    assert detail["audit_action"]["action_type"] == "bff.command.rejected"
    assert response.headers["X-Correlation-Id"] == correlation_id
    assert bff_main.command_store._get_all_commands() == []


def test_bff_v1_commands_missing_confirm_token_returns_428_envelope() -> None:
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": "precondition-confirm-token-001",
                "X-Correlation-Id": "corr-precondition-confirm",
            },
            json={
                "command": "PauseRuntime",
                "target": {"type": "Runtime", "id": "runtime-final-precondition-001"},
                "params": {
                    "runtime_binding_id": "rb-final-precondition-001",
                    "pause_action": "pause",
                },
                "audit_context": {"reason": "Pause runtime after operator review"},
            },
        )

        _assert_precondition_error(
            response,
            status_code=428,
            code="CONFIRMATION_REQUIRED",
            action_id="PauseRuntime",
            entity_type="Runtime",
            entity_id="runtime-final-precondition-001",
            kind="confirm_token",
            correlation_id="corr-precondition-confirm",
        )


def test_bff_v1_commands_missing_approval_returns_409_envelope() -> None:
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": APPROVER_TOKEN,
                "Idempotency-Key": "precondition-approval-001",
                "X-Correlation-Id": "corr-precondition-approval",
            },
            json={
                "command": "ApproveDecision",
                "target": {"type": "ApprovalDecision", "id": "appr-final-precondition-001"},
                "params": {"decision_id": "appr-final-precondition-001"},
                "audit_context": {"reason": "Approve decision after evidence review"},
            },
        )

        _assert_precondition_error(
            response,
            status_code=409,
            code="APPROVAL_REQUIRED",
            action_id="ApproveDecision",
            entity_type="ApprovalDecision",
            entity_id="appr-final-precondition-001",
            kind="approval",
            correlation_id="corr-precondition-approval",
        )


def test_bff_v1_commands_missing_two_man_returns_409_envelope() -> None:
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": "precondition-two-man-001",
                "X-Confirm-Token": "confirm-token-001",
                "X-Correlation-Id": "corr-precondition-two-man",
            },
            json={
                "command": "ActivateKillSwitch",
                "target": {"type": "KillSwitchOrder", "id": "ks-final-precondition-001"},
                "approvalId": "approval-final-precondition-001",
                "params": {
                    "scope": "all",
                    "activate": True,
                    "severity": "critical",
                },
                "audit_context": {"reason": "Incident commander requested kill switch"},
            },
        )

        _assert_precondition_error(
            response,
            status_code=409,
            code="TWO_MAN_REQUIRED",
            action_id="ActivateKillSwitch",
            entity_type="KillSwitchOrder",
            entity_id="ks-final-precondition-001",
            kind="two_man",
            correlation_id="corr-precondition-two-man",
        )
