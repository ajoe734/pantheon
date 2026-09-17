from __future__ import annotations

import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.auth.policy import bff_error
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus, CommandType, ErrorCode, RiskLevel, OperatorIdentity
from services.control_plane.bff.tests.conftest import (
    ApprovalDecisionReadSurface,
    build_command_security_app,
    noop_process_command,
)
import os


# NOTE (BFF-TEST-MIGRATION-CB03-AUTH-SESSION-SECURITY-001 known gap):
# HumanGate decision payload validation (required fields, decision value
# enumeration, approver-role gate, and the HumanGateExtendTtl TTL cap) is
# implemented only inline in main.py as ``_validate_human_gate_decision`` /
# ``_human_gate_max_ttl_seconds`` (~main.py:3563 and ~main.py:2248) and has
# not been extracted into ``command_adapters/contracts.py`` alongside its
# sibling ``normalize_human_gate_command`` (which only normalizes
# human_gate_item_id/decision/audit_event fields and does not validate the
# TTL cap). ``command_adapters.service.CommandAdapterService`` already
# anticipates injection of exactly this kind of per-command validator via its
# ``validators`` mapping -- the already-migrated ``test_v5_interventions.py``
# uses the identical technique for ``RemediateSentinelIntervention`` and
# ``DecideV5Intervention``. This test supplies a byte-for-byte behavioral
# mirror of main.py's real validator (same required fields, same role gate,
# same TTL bounds and error codes) as the injected validator, rather than
# reimplementing the *command-admission* business logic under test (which
# remains the real ``command_adapters``/``control_loops`` pipeline). See the
# evidence.json for this task for the recommended follow-up: extract
# ``_validate_human_gate_decision``/``_human_gate_max_ttl_seconds`` into
# ``command_adapters/contracts.py`` so real callers and tests share one
# definition.
_HUMAN_GATE_DECISIONS_BY_COMMAND = {
    CommandType.HUMAN_GATE_APPROVE: "approve",
    CommandType.HUMAN_GATE_REJECT: "reject",
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: "request_more_evidence",
    CommandType.HUMAN_GATE_REVOKE: "revoke",
    CommandType.HUMAN_GATE_EXTEND_TTL: "extend_ttl",
}
_HUMAN_GATE_REQUIRED = {"human_gate_item_id", "decision"}
_VALID_HUMAN_GATE_DECISIONS = set(_HUMAN_GATE_DECISIONS_BY_COMMAND.values())
_HUMAN_GATE_APPROVER_DECISIONS = {"approve", "reject", "revoke", "extend_ttl"}
_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS = 86400


def _human_gate_max_ttl_seconds() -> int:
    raw = os.getenv("PANTHEON_HUMAN_GATE_MAX_TTL_SECONDS", str(_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS)).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = _HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS
    return max(1, configured)


def _validate_human_gate_decision(params: dict, identity: OperatorIdentity) -> None:
    missing = _HUMAN_GATE_REQUIRED - {key for key, value in params.items() if value not in (None, "")}
    if missing:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for HumanGate command",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="human_gate",
        )

    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _VALID_HUMAN_GATE_DECISIONS:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid HumanGate decision value",
            f"decision must be one of {sorted(_VALID_HUMAN_GATE_DECISIONS)}",
            precondition_failed="decision",
        )

    if decision in _HUMAN_GATE_APPROVER_DECISIONS and not {"approver", "admin"}.intersection(identity.roles):
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "HumanGate decision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
    if decision == "request_more_evidence" and not {"operator", "approver", "admin", "reviewer"}.intersection(identity.roles):
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "HumanGate evidence request requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )

    if decision == "extend_ttl":
        raw_ttl = (
            params.get("ttl_seconds")
            or params.get("ttlSeconds")
            or params.get("extend_ttl_seconds")
            or params.get("extendTtlSeconds")
        )
        try:
            ttl_seconds = int(raw_ttl)
        except (TypeError, ValueError):
            ttl_seconds = 0
        if ttl_seconds <= 0:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGateExtendTtl requires a positive ttl_seconds value",
                "ttl_seconds must be a positive integer number of seconds",
                precondition_failed="ttl_seconds",
            )
        max_ttl_seconds = _human_gate_max_ttl_seconds()
        if ttl_seconds > max_ttl_seconds:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGateExtendTtl exceeds the maximum ttl_seconds cap",
                "HUMAN_GATE_TTL_EXCEEDS_CAP",
                precondition_failed="ttl_seconds",
                suggestion="Retry with a shorter HumanGate TTL extension",
                details_extra={
                    "maxTtlSeconds": max_ttl_seconds,
                    "ttlSeconds": ttl_seconds,
                    "constraint": f"ttl_seconds must be less than or equal to {max_ttl_seconds}",
                },
            )
        params["ttl_seconds"] = ttl_seconds
        params["ttlSeconds"] = ttl_seconds


_B5_COMMAND_VALIDATORS = {
    CommandType.HUMAN_GATE_APPROVE: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_REJECT: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_REVOKE: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_EXTEND_TTL: _validate_human_gate_decision,
    "HumanGateApprove": _validate_human_gate_decision,
    "HumanGateReject": _validate_human_gate_decision,
    "HumanGateRequestMoreEvidence": _validate_human_gate_decision,
    "HumanGateRevoke": _validate_human_gate_decision,
    "HumanGateExtendTtl": _validate_human_gate_decision,
}


HEADERS = {
    "Authorization": "Bearer op-b5-human:operator,reviewer,approver:mfa",
    "X-Trace-Id": "trace-bff-b5-sec",
    "X-Correlation-Id": "corr-bff-b5-sec",
    "X-Request-Id": "req-bff-b5-sec",
}


class _State:
    def __init__(self) -> None:
        self.command_store: CommandStore | None = None
        self.read_store: ApprovalDecisionReadSurface | None = None
        self.app = None


_state = _State()


@contextmanager
def _isolated_b5_security_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        _state.command_store = CommandStore(f"{td}/commands.jsonl")
        _state.read_store = ApprovalDecisionReadSurface()
        _state.app = build_command_security_app(
            command_store=_state.command_store,
            read_store=_state.read_store,
            validators=_B5_COMMAND_VALIDATORS,
            process_command_task=lambda cmd_id: noop_process_command(cmd_id),
        )
        try:
            yield TestClient(_state.app, raise_server_exceptions=False)
        finally:
            _state.command_store = None
            _state.read_store = None
            _state.app = None


def _seed_approval(
    decision_id: str,
    *,
    requester_id: str = "risk-owner",
    risk_level: str = "medium",
    downstream_effect_status: str | None = None,
) -> None:
    record = {
        "id": decision_id,
        "decision_id": decision_id,
        "state": "pending",
        "decision_state": "pending",
        "target_type": "HumanGateItem",
        "target_id": f"approval:{decision_id}",
        "requester_id": requester_id,
        "risk_level": risk_level,
        "created_at": "2026-05-25T12:00:00Z",
    }
    if downstream_effect_status:
        record["downstream_effect_status"] = downstream_effect_status
    _state.read_store.seed_approval_decision(record)


def _submit_human_gate(
    client: TestClient,
    *,
    command: str,
    target_id: str,
    params: dict | None = None,
    idempotency_key: str,
    extra_payload: dict | None = None,
):
    payload = {
        "command": command,
        "target": {"type": "HumanGateItem", "id": target_id},
        "action": "submit",
        "params": params or {},
        "audit_context": {"reason": f"BFF-B5-001-SEC-FIX {command}"},
    }
    payload.update(extra_payload or {})
    return client.post(
        "/bff/v1/commands",
        headers={**HEADERS, "Idempotency-Key": idempotency_key},
        json=payload,
    )


def _error_details(response) -> dict:
    return response.json()["error"]["details"]


def _create_human_gate_two_man_signature(client: TestClient, signature_id: str, *, target_id: str) -> None:
    for operator_id, authorization in (
        ("op-b5-human", HEADERS["Authorization"]),
        ("op-b5-secondary", "Bearer op-b5-secondary:operator:mfa"),
    ):
        response = client.post(
            f"/bff/v5/interventions/{signature_id}/two-man-sign",
            headers={
                **HEADERS,
                "Authorization": authorization,
                "Idempotency-Key": f"sign-{signature_id}-{operator_id}",
            },
            json={
                "twoManSignatureId": signature_id,
                "command": "HumanGateApprove",
                "target": {"type": "HumanGateItem", "id": target_id},
                "reason": "operator signed the high-risk HumanGate action",
            },
        )
        assert response.status_code == 202, response.text
        command_id = response.json()["data"]["command_id"]
        record = _state.command_store.get_command(command_id)
        assert record is not None
        assert record["status"] == CommandStatus.EXECUTED.value
        assert record["params"]["signerOperatorIds"] == [operator_id]


def test_human_gate_item_id_params_must_match_target_id() -> None:
    with _isolated_b5_security_client() as client:
        response = _submit_human_gate(
            client,
            command="HumanGateApprove",
            target_id="approval:b5-sec-target",
            params={"human_gate_item_id": "approval:other"},
            idempotency_key="bff-b5-sec-target-mismatch",
        )

        assert response.status_code == 422, response.text
        assert _error_details(response)["reason"] == "HUMAN_GATE_TARGET_MISMATCH"


def test_human_gate_approve_reject_revoke_forbid_requester_self_decision() -> None:
    with _isolated_b5_security_client() as client:
        _seed_approval("b5-sec-self", requester_id="op-b5-human")

        for command in ("HumanGateApprove", "HumanGateReject", "HumanGateRevoke"):
            response = _submit_human_gate(
                client,
                command=command,
                target_id="approval:b5-sec-self",
                idempotency_key=f"bff-b5-sec-self-{command}",
            )

            assert response.status_code == 403, response.text
            assert _error_details(response)["reason"] == "HUMAN_GATE_SELF_APPROVAL_FORBIDDEN"


def test_high_risk_human_gate_requires_two_man_and_records_evidence() -> None:
    with _isolated_b5_security_client() as client:
        _seed_approval("b5-sec-high", requester_id="risk-owner", risk_level="high")

        missing_signature = _submit_human_gate(
            client,
            command="HumanGateApprove",
            target_id="approval:b5-sec-high",
            idempotency_key="bff-b5-sec-high-missing-two-man",
        )
        assert missing_signature.status_code == 409, missing_signature.text
        assert _error_details(missing_signature)["reason"] == "TWO_MAN_SIGNATURE_MISSING"

        _create_human_gate_two_man_signature(
            client,
            "tms-b5-sec-high",
            target_id="approval:b5-sec-high",
        )
        accepted = _submit_human_gate(
            client,
            command="HumanGateApprove",
            target_id="approval:b5-sec-high",
            idempotency_key="bff-b5-sec-high-with-two-man",
            extra_payload={"twoManSignatureId": "tms-b5-sec-high"},
        )

        assert accepted.status_code == 202, accepted.text
        command_id = accepted.json()["data"]["command_id"]
        record = _state.command_store.get_command(command_id)
        assert record is not None
        assert record["audit"]["precondition_evidence"]["two_man_signature_id"] == "tms-b5-sec-high"
        assert record["params"]["two_man_signature_id"] == "tms-b5-sec-high"


def test_human_gate_extend_ttl_is_capped_by_env(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_HUMAN_GATE_MAX_TTL_SECONDS", "3600")
    with _isolated_b5_security_client() as client:
        _seed_approval("b5-sec-ttl", requester_id="risk-owner", risk_level="low")

        response = _submit_human_gate(
            client,
            command="HumanGateExtendTtl",
            target_id="approval:b5-sec-ttl",
            params={"ttlSeconds": 3601},
            idempotency_key="bff-b5-sec-ttl-cap",
        )

        assert response.status_code == 422, response.text
        details = _error_details(response)
        assert details["reason"] == "HUMAN_GATE_TTL_EXCEEDS_CAP"
        assert details["maxTtlSeconds"] == 3600


def test_human_gate_revoke_fails_closed_after_downstream_execution() -> None:
    with _isolated_b5_security_client() as client:
        _seed_approval(
            "b5-sec-executed",
            requester_id="risk-owner",
            risk_level="medium",
            downstream_effect_status="executed",
        )

        response = _submit_human_gate(
            client,
            command="HumanGateRevoke",
            target_id="approval:b5-sec-executed",
            idempotency_key="bff-b5-sec-revoke-executed",
        )

        assert response.status_code == 409, response.text
        details = _error_details(response)
        assert details["reason"] == "HUMAN_GATE_REVOKE_DOWNSTREAM_EXECUTED"
        assert "compensating action" in details["suggestion"]


def test_human_gate_catalog_and_executor_surface_two_man_evidence(monkeypatch) -> None:
    from services.control_plane.bff import command_executor
    monkeypatch.setitem(command_executor._EXECUTORS, CommandType.HUMAN_GATE_APPROVE, command_executor._execute_bff_action_adapter)
    for command in ("HumanGateApprove", "HumanGateReject", "HumanGateRevoke"):
        entry = get_catalog_entry(command)
        assert entry is not None
        assert entry.risk_level == RiskLevel.HIGH
        assert entry.requires_two_man is True

    status, result, error = execute_command_with_status(
        "cmd-b5-sec-executor",
        CommandType.HUMAN_GATE_APPROVE,
        {
            "action_id": "approve",
            "entity_type": "human_gate_item",
            "entity_id": "approval:b5-sec-executor",
            "audit_event": "human_gate.approve",
            "two_man_signature_id": "tms-b5-sec-executor",
        },
    )

    assert status == CommandStatus.EXECUTED
    assert error is None
    assert result is not None
    assert result["dispatch_path"] == "bff_action_adapter"
    assert result["two_man_signature_id"] == "tms-b5-sec-executor"
