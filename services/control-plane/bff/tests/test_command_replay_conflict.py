from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from command_queue import CommandStore


HEADERS = {
    "Authorization": "Bearer op-bff-021:operator,approver,admin:mfa",
    "X-Trace-Id": "trace-bff-consol-021",
    "X-Correlation-Id": "corr-bff-consol-021",
    "X-Request-Id": "req-bff-consol-021",
}


async def _noop_process_command(_command_id: str) -> None:
    return None


def _error_detail(response) -> dict:
    body = response.json()
    return body.get("detail") or body


@contextmanager
def _isolated_command_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            bff_main._process_command_stub = original_worker
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.clear()


def _receipt_id(payload: dict) -> str:
    assert payload["status"] == "accepted"
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["receipt"]["status"] == "accepted"
    return payload["data"]["receipt_id"]


def _pause_execution_payload(reason: str = "BFF-CONSOL-021 replay proof") -> dict:
    return {
        "command": "PauseExecution",
        "target": {"type": "Runtime", "id": "runtime-bff-consol-021"},
        "params": {"pause_new_entries": True, "cancel_open_orders": False},
        "audit_context": {"reason": reason},
    }


def test_legacy_action_dual_writes_action_and_command_receipts() -> None:
    with _isolated_command_client() as client:
        response = client.post(
            "/bff/actions/strategy/stg-bff-021/submit_review",
            headers={**HEADERS, "Idempotency-Key": "bff-consol-021-action-dual"},
            json={"reason": "submit strategy review through dual-write proof"},
        )

        assert response.status_code == 202, response.text
        assert response.headers["Deprecation"] == "true"
        assert response.headers["Sunset"] == "Mon, 15 Jun 2026 00:00:00 GMT"
        assert response.headers["X-Pantheon-Deprecated-Route"] == "/bff/actions/*"
        assert "/bff/v1/commands" in response.headers["Link"]
        body = response.json()
        action_receipt_id = _receipt_id(body)
        assert body["data"]["command"] == "StrategyAction"
        assert body["data"]["deprecated"] is True
        assert body["data"]["deprecation"]["deprecated_since"] == "2026-05-14"
        assert body["data"]["deprecation"]["replacement"] == "/bff/v1/commands"
        assert body["data"]["receipt"]["deprecated"] is True
        assert body["meta"]["deprecated"] is True
        assert body["meta"]["deprecation"]["sunset"] == "2026-06-15"
        assert body["meta"]["durable"] is True
        assert body["meta"]["idempotency"]["idempotencyKey"] == "bff-consol-021-action-dual"
        assert body["meta"]["idempotency"]["replayed"] is False

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        command_receipt = records[0]
        assert action_receipt_id == command_receipt["command_id"]
        assert command_receipt["type"] == "StrategyAction"
        assert command_receipt["target"] == {"type": "Strategy", "id": "stg-bff-021"}
        assert command_receipt["params"]["action_id"] == "submit_review"

        foundation = command_receipt["foundation"]
        assert foundation["admission_route"] == "POST /bff/v1/commands"
        assert foundation["source_route"] == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        assert foundation["idempotency_record"]["idempotency_key"] == "bff-consol-021-action-dual"
        assert foundation["command_envelope"]["payload"]["source_route"] == (
            "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        )
        assert command_receipt["audit"]["foundation"]["audit_action"]["metadata"]["route"] == (
            "POST /bff/v1/commands"
        )


def test_legacy_action_idempotency_replay_returns_same_receipt() -> None:
    with _isolated_command_client() as client:
        headers = {**HEADERS, "Idempotency-Key": "bff-consol-021-action-replay"}
        payload = {"reason": "same legacy action body replays"}

        first = client.post("/bff/actions/strategy/stg-bff-021/submit_review", headers=headers, json=payload)
        second = client.post("/bff/actions/strategy/stg-bff-021/submit_review", headers=headers, json=payload)

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert second.headers["Deprecation"] == "true"
        assert _receipt_id(second.json()) == _receipt_id(first.json())
        assert second.json()["data"]["deprecated"] is True
        assert second.json()["data"]["receipt"]["deprecated"] is True
        assert second.json()["meta"]["deprecation"]["replacement"] == "/bff/v1/commands"
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        assert len(bff_main.command_store._get_all_commands()) == 1


def test_legacy_action_idempotency_conflict_returns_409() -> None:
    with _isolated_command_client() as client:
        headers = {**HEADERS, "Idempotency-Key": "bff-consol-021-action-conflict"}

        first = client.post(
            "/bff/actions/strategy/stg-bff-021/submit_review",
            headers=headers,
            json={"reason": "initial action body"},
        )
        conflict = client.post(
            "/bff/actions/strategy/stg-bff-021/submit_review",
            headers=headers,
            json={"reason": "changed action body"},
        )

        assert first.status_code == 202, first.text
        assert conflict.status_code == 409, conflict.text
        detail = _error_detail(conflict)
        assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["foundation_error"]["error_code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["audit_action"]["action_type"] == "bff.command.idempotency_conflict"
        assert len(bff_main.command_store._get_all_commands()) == 1


def test_final_command_idempotency_replay_returns_same_receipt() -> None:
    with _isolated_command_client() as client:
        headers = {**HEADERS, "Idempotency-Key": "bff-consol-021-command-replay"}
        payload = _pause_execution_payload()

        first = client.post("/bff/v1/commands", headers=headers, json=payload)
        second = client.post("/bff/v1/commands", headers=headers, json=payload)

        assert first.status_code == 202, first.text
        assert "Deprecation" not in first.headers
        assert second.status_code == 202, second.text
        assert "Deprecation" not in second.headers
        assert _receipt_id(second.json()) == _receipt_id(first.json())
        assert "deprecated" not in first.json()["data"]
        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        assert records[0]["type"] == "PauseExecution"
        assert records[0]["foundation"]["admission_route"] == "POST /bff/v1/commands"


def test_final_command_idempotency_conflict_returns_409() -> None:
    with _isolated_command_client() as client:
        headers = {**HEADERS, "Idempotency-Key": "bff-consol-021-command-conflict"}

        first = client.post("/bff/v1/commands", headers=headers, json=_pause_execution_payload())
        conflict = client.post(
            "/bff/v1/commands",
            headers=headers,
            json=_pause_execution_payload(reason="changed final command body"),
        )

        assert first.status_code == 202, first.text
        assert conflict.status_code == 409, conflict.text
        detail = _error_detail(conflict)
        assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["foundation_error"]["error_code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["audit_action"]["action_type"] == "bff.command.idempotency_conflict"
        assert len(bff_main.command_store._get_all_commands()) == 1


def test_final_command_missing_confirm_token_returns_typed_error() -> None:
    with _isolated_command_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "bff-consol-021-missing-confirm"},
            json={
                "command": "PauseRuntime",
                "target": {"type": "Runtime", "id": "runtime-bff-consol-021-confirm"},
                "params": {
                    "runtime_binding_id": "rb-bff-consol-021",
                    "pause_action": "pause",
                },
                "audit_context": {"reason": "missing confirm token should block"},
            },
        )

        assert response.status_code == 428, response.text
        detail = _error_detail(response)
        assert detail["error"]["code"] == "CONFIRMATION_REQUIRED"
        assert detail["error"]["details"]["kind"] == "confirm_token"
        assert detail["foundation_error"]["error_code"] == "CONFIRMATION_REQUIRED"
        assert detail["audit_action"]["action_type"] == "bff.command.rejected"
        assert bff_main.command_store._get_all_commands() == []


def test_final_command_missing_approval_evidence_returns_typed_error() -> None:
    with _isolated_command_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "bff-consol-021-missing-approval"},
            json={
                "command": "ApproveDecision",
                "target": {"type": "ApprovalDecision", "id": "appr-bff-consol-021"},
                "params": {"decision_id": "appr-bff-consol-021"},
                "audit_context": {"reason": "missing approval evidence should block"},
            },
        )

        assert response.status_code == 409, response.text
        detail = _error_detail(response)
        assert detail["error"]["code"] == "HUMAN_GATE_PENDING"
        assert detail["error"]["details"]["kind"] == "approval"
        assert detail["foundation_error"]["error_code"] == "HUMAN_GATE_PENDING"
        assert detail["audit_action"]["action_type"] == "bff.command.rejected"
        assert bff_main.command_store._get_all_commands() == []
