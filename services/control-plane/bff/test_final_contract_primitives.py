from __future__ import annotations

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import (
    ActionCommandStatus,
    BffErrorEnvelope,
    BffErrorPayload,
    CommandResponse,
    CommandStatus,
    CommandType,
    ErrorCode,
)


APPROVER_TOKEN = "Bearer op-6:approver"


async def _noop_process_command(_command_id: str) -> None:
    return None


def test_action_command_status_is_final_success_set_only() -> None:
    assert {status.value for status in ActionCommandStatus} == {
        "accepted",
        "queued",
        "completed",
    }

    for non_success_status in (
        "requires_approval",
        "requires_confirm_token",
        "requires_two_man",
    ):
        with pytest.raises(ValueError):
            ActionCommandStatus(non_success_status)


def test_command_response_requires_data() -> None:
    with pytest.raises(ValidationError) as exc:
        CommandResponse[dict[str, str]](status=ActionCommandStatus.ACCEPTED)

    assert "data" in str(exc.value)

    response = CommandResponse[dict[str, str]](
        status=ActionCommandStatus.ACCEPTED,
        data={"command_id": "cmd-final-001"},
    )
    assert response.data == {"command_id": "cmd-final-001"}


def test_final_error_envelope_and_codes_are_importable() -> None:
    assert ErrorCode.CONFIRMATION_REQUIRED.value == "CONFIRMATION_REQUIRED"
    assert ErrorCode.HUMAN_GATE_PENDING.value == "HUMAN_GATE_PENDING"
    assert ErrorCode.TWO_MAN_SIGNATURE_REQUIRED.value == "TWO_MAN_SIGNATURE_REQUIRED"
    assert ErrorCode.IDEMPOTENCY_CONFLICT.value == "IDEMPOTENCY_CONFLICT"
    assert ErrorCode.RESOURCE_CONFLICT.value == "RESOURCE_CONFLICT"

    envelope = BffErrorEnvelope(
        error=BffErrorPayload(
            code=ErrorCode.HUMAN_GATE_PENDING,
            i18nKey="errors.HUMAN_GATE_PENDING",
            message="Approval is required before this action can be accepted",
            retryable=False,
            userActionable=True,
        )
    )
    assert envelope.model_dump(mode="json") == {
        "error": {
            "code": "HUMAN_GATE_PENDING",
            "i18nKey": "errors.HUMAN_GATE_PENDING",
            "message": "Approval is required before this action can be accepted",
            "retryable": False,
            "userActionable": True,
            "details": None,
        }
    }


def test_final_command_response_adapter_excludes_failure_statuses() -> None:
    response = bff_main._project_final_command_response(
        command_id="cmd-final-001",
        command=CommandType.APPROVE_DECISION,
        accepted_at="2026-05-07T00:00:00Z",
        status=CommandStatus.EXECUTED,
        staleness_warning=None,
    )

    assert response.status == ActionCommandStatus.COMPLETED
    assert response.data["status"] == "completed"
    assert response.data["receipt"]["status"] == "completed"

    for failure_status in (CommandStatus.FAILED, CommandStatus.TIMEOUT):
        with pytest.raises(ValueError):
            bff_main._project_final_command_response(
                command_id="cmd-final-failed",
                command=CommandType.APPROVE_DECISION,
                accepted_at="2026-05-07T00:00:00Z",
                status=failure_status,
                staleness_warning=None,
            )


def test_idempotency_conflict_uses_final_error_code() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        headers = {
            "Authorization": APPROVER_TOKEN,
            "X-Trace-Id": "trace-final-idmp-conflict",
            "X-Idempotency-Key": "idmp-final-conflict",
        }
        body = {
            "command": "ApproveDecision",
            "target": {"type": "ApprovalDecision", "id": "appr-final-001"},
            "action": "approve",
            "params": {"decision_id": "appr-final-001", "approval_notes": "first"},
            "audit_context": {"reason": "Policy checks passed"},
        }
        changed_body = {
            **body,
            "params": {"decision_id": "appr-final-001", "approval_notes": "changed"},
        }

        try:
            first = client.post("/api/v1/operator/commands", headers=headers, json=body)
            second = client.post("/api/v1/operator/commands", headers=headers, json=changed_body)

            assert first.status_code == 202, first.text
            assert second.status_code == 409, second.text
            detail = second.json()
            assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
            assert detail["foundation_error"]["error_code"] == "IDEMPOTENCY_CONFLICT"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker
