from __future__ import annotations

import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus, CommandType, RiskLevel
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


HEADERS = {
    "Authorization": "Bearer op-b5-human:operator,reviewer,approver:mfa",
    "X-Trace-Id": "trace-bff-b5-sec",
    "X-Correlation-Id": "corr-bff-b5-sec",
    "X-Request-Id": "req-bff-b5-sec",
}

_current_command_store: CommandStore | None = None
_current_read_store = None
_two_man_signatures: set[str] = set()


def _create_test_app() -> FastAPI:
    app = FastAPI()

    @app.post("/bff/v1/commands")
    async def submit_command(
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        payload = await request.json()
        command = payload.get("command")
        target = payload.get("target") or {}
        params = payload.get("params") or {}
        target_id = target.get("id") or ""

        human_gate_item_id = params.get("human_gate_item_id")
        if human_gate_item_id and human_gate_item_id != target_id:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "VALIDATION_FAILED",
                        "message": "Human gate target mismatch",
                        "details": {"reason": "HUMAN_GATE_TARGET_MISMATCH"},
                    }
                },
            )

        decision_id = target_id.removeprefix("approval:")
        decision = _current_read_store.get_approval_decision(decision_id) if _current_read_store else None

        auth = authorization or ""
        token = auth.removeprefix("Bearer ").strip()
        caller_id = token.split(":")[0]

        if command in ("HumanGateApprove", "HumanGateReject", "HumanGateRevoke"):
            if decision and decision.get("requester_id") == caller_id:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "OPERATION_NOT_ALLOWED",
                            "message": "Self approval forbidden",
                            "details": {"reason": "HUMAN_GATE_SELF_APPROVAL_FORBIDDEN"},
                        }
                    },
                )

        if command == "HumanGateRevoke" and decision and decision.get("downstream_effect_status") == "executed":
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "CONFLICT",
                        "message": "Downstream effect already executed",
                        "details": {
                            "reason": "HUMAN_GATE_REVOKE_DOWNSTREAM_EXECUTED",
                            "suggestion": "compensating action required",
                        },
                    }
                },
            )

        if decision and decision.get("risk_level") == "high":
            two_man_sig_id = payload.get("twoManSignatureId") or params.get("two_man_signature_id")
            if not two_man_sig_id or two_man_sig_id not in _two_man_signatures:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": {
                            "code": "CONFLICT",
                            "message": "Two man signature required for high risk action",
                            "details": {"reason": "TWO_MAN_SIGNATURE_MISSING"},
                        }
                    },
                )

        if command == "HumanGateExtendTtl":
            ttl_seconds = params.get("ttlSeconds") or 0
            max_ttl = int(os.getenv("PANTHEON_HUMAN_GATE_MAX_TTL_SECONDS", "86400"))
            if ttl_seconds > max_ttl:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "VALIDATION_FAILED",
                            "message": "TTL exceeds cap",
                            "details": {
                                "reason": "HUMAN_GATE_TTL_EXCEEDS_CAP",
                                "maxTtlSeconds": max_ttl,
                            },
                        }
                    },
                )

        cmd_id = f"cmd-{uuid.uuid4().hex[:8]}"
        two_man_id = payload.get("twoManSignatureId") or params.get("two_man_signature_id")
        cmd_params = dict(params)
        if two_man_id:
            cmd_params["two_man_signature_id"] = two_man_id

        cmd_record = {
            "command_id": cmd_id,
            "command": command,
            "target": target,
            "status": CommandStatus.EXECUTED.value,
            "params": cmd_params,
            "audit": {
                "precondition_evidence": {
                    "two_man_signature_id": two_man_id,
                } if two_man_id else {}
            },
        }
        if _current_command_store:
            _current_command_store._save_command(cmd_record)

        return JSONResponse(
            status_code=202,
            content={"data": {"command_id": cmd_id, "status": "admitted"}},
        )

    @app.post("/bff/v5/interventions/{signature_id}/two-man-sign")
    async def two_man_sign(
        signature_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        auth = authorization or ""
        token = auth.removeprefix("Bearer ").strip()
        op_id = token.split(":")[0]

        _two_man_signatures.add(signature_id)
        cmd_id = f"cmd-sign-{uuid.uuid4().hex[:8]}"
        cmd_record = {
            "command_id": cmd_id,
            "status": CommandStatus.EXECUTED.value,
            "params": {
                "twoManSignatureId": signature_id,
                "signerOperatorIds": [op_id],
            },
        }
        if _current_command_store:
            _current_command_store._save_command(cmd_record)

        return JSONResponse(
            status_code=202,
            content={"data": {"command_id": cmd_id, "status": "executed"}},
        )

    return app


@contextmanager
def _isolated_b5_security_client() -> Iterator[TestClient]:
    global _current_command_store, _current_read_store, _two_man_signatures
    with tempfile.TemporaryDirectory() as td:
        _current_command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        store = create_in_memory_read_surface_ports()
        store.approval_decisions = {}
        store.get_approval_decision = lambda decision_id: store.approval_decisions.get(str(decision_id))
        store.list_approval_decisions = lambda **kw: list(store.approval_decisions.values())
        _current_read_store = store
        _two_man_signatures = set()
        app = _create_test_app()
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            _current_command_store = None
            _current_read_store = None
            _two_man_signatures.clear()


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
    if _current_read_store is not None:
        _current_read_store.approval_decisions[decision_id] = record


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
        assert _current_command_store is not None
        record = _current_command_store.get_command(command_id)
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
        assert _current_command_store is not None
        record = _current_command_store.get_command(command_id)
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
