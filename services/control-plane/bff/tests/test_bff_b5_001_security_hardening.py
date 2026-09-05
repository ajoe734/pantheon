from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
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


async def _noop_process_command(_command_id: str) -> None:
    return None


@contextmanager
def _isolated_b5_security_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        store = create_in_memory_read_surface_ports()
        store.approval_decisions = {}
        store.get_approval_decision = lambda decision_id: store.approval_decisions.get(str(decision_id))
        store.list_approval_decisions = lambda **kw: list(store.approval_decisions.values())
        bff_main.read_store = store
        bff_main._process_command_stub = _noop_process_command
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_main._COMMAND_AUTH_CONTEXT.clear()
        bff_main._V5_INTERVENTIONS_STORE.clear()
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.command_store = original_command_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._COMMAND_AUTH_CONTEXT.clear()
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


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
    bff_main.read_store.approval_decisions[decision_id] = record


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
        record = bff_main.command_store.get_command(command_id)
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
        record = bff_main.command_store.get_command(command_id)
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
    from services.control_plane.bff import command_executor as command_executor
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
