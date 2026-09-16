from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.control_plane.bff.command_adapters.receipts import project_final_command_response
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import (
    ActionCommandStatus,
    BffErrorEnvelope,
    BffErrorPayload,
    CommandResponse,
    CommandStatus,
    CommandType,
    ErrorCode,
    OperatorIdentity,
)


APPROVER_TOKEN = "Bearer op-6:approver"


def _test_extract_identity(
    authorization: Optional[str], mfa_token: Optional[str] = None
) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        return OperatorIdentity(operator_id="anonymous", roles=["viewer"], auth_mode="anonymous", has_mfa=False)
    token = authorization[len("Bearer ") :].strip()
    parts = token.split(":")
    actor = parts[0] if parts else "system"
    roles = [r.strip() for r in parts[1].split(",")] if len(parts) > 1 else ["operator"]
    return OperatorIdentity(
        operator_id=actor,
        roles=roles,
        auth_mode="bearer",
        has_mfa=len(parts) > 2 and parts[2] == "mfa",
        mfa_verified=len(parts) > 2 and parts[2] == "mfa",
    )


class _MockReadStore:
    def __init__(self, approval_decision: Optional[dict[str, Any]] = None) -> None:
        self._approval_decision = approval_decision

    def get_approval_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        if self._approval_decision and self._approval_decision.get("decision_id") == decision_id:
            return self._approval_decision
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
    response = project_final_command_response(
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
            project_final_command_response(
                command_id="cmd-final-failed",
                command=CommandType.APPROVE_DECISION,
                accepted_at="2026-05-07T00:00:00Z",
                status=failure_status,
                staleness_warning=None,
            )


def test_idempotency_conflict_uses_final_error_code(monkeypatch) -> None:
    del monkeypatch
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        decision_record = {
            "id": "approval-final-001",
            "decision_id": "approval-final-001",
            "outcome": "approved",
            "state": "approved",
            "command": "ApproveDecision",
            "target_type": "ApprovalDecision",
            "target_id": "appr-final-001",
            "reviewer": "governance",
            "risk_level": "medium",
        }
        read_store = _MockReadStore(approval_decision=decision_record)
        svc = CommandAdapterService(
            command_store=store,
            read_surface=read_store,
            extract_identity=_test_extract_identity,
        )
        app = FastAPI()
        register_error_handlers(app)
        router = create_command_adapters_router(
            service=svc,
            submit_command_admission=svc.submit_command_admission,
        )
        app.include_router(router)
        client = TestClient(app)

        headers = {
            "Authorization": APPROVER_TOKEN,
            "X-Trace-Id": "trace-final-idmp-conflict",
            "X-Idempotency-Key": "idmp-final-conflict",
        }
        body = {
            "command": "ApproveDecision",
            "target": {"type": "ApprovalDecision", "id": "appr-final-001"},
            "action": "approve",
            "params": {
                "decision_id": "appr-final-001",
                "approval_notes": "first",
                "approvalId": "approval-final-001",
            },
            "audit_context": {"reason": "Policy checks passed"},
        }
        changed_body = {
            **body,
            "params": {
                "decision_id": "appr-final-001",
                "approval_notes": "changed",
                "approvalId": "approval-final-001",
            },
        }

        first = client.post("/bff/v1/commands", headers=headers, json=body)
        second = client.post("/bff/v1/commands", headers=headers, json=changed_body)

        assert first.status_code == 202, first.text
        assert second.status_code == 409, second.text
        detail = second.json()
        assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["foundation_error"]["error_code"] == "IDEMPOTENCY_CONFLICT"
