"""BFF-CONSOL-021: command receipt dual-write, replay, and precondition regressions."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BFF_DIR))
sys.path.insert(0, str(REPO_ROOT))

import main as bff_main
from command_queue import CommandStore


ADMIN_HEADERS = {"Authorization": "Bearer op-bff-consol-021:operator,reviewer,admin:mfa"}
APPROVER_HEADERS = {"Authorization": "Bearer op-bff-consol-021-approver:approver"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-bff-consol-021-operator:operator"}


async def _noop_process_command(_command_id: str) -> None:
    return None


@contextmanager
def _isolated_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()


def _assert_error_code(response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["detail"]["error"]["code"] == code


def test_action_receipt_and_command_receipt_dual_write_replay_and_conflict() -> None:
    with _isolated_bff() as client:
        headers = {**ADMIN_HEADERS, "Idempotency-Key": "bff-consol-021-action-replay"}
        body = {"reason": "dual-write replay proof"}

        first = client.post("/bff/actions/strategy/stg-bff-consol-021/submit", headers=headers, json=body)
        replay = client.post("/bff/actions/strategy/stg-bff-consol-021/submit", headers=headers, json=body)
        conflict = client.post(
            "/bff/actions/strategy/stg-bff-consol-021/submit",
            headers=headers,
            json={"reason": "different command body"},
        )

        assert first.status_code == 202, first.text
        assert replay.status_code == 202, replay.text
        _assert_error_code(conflict, status_code=409, code="IDEMPOTENCY_CONFLICT")

        first_data = first.json()["data"]
        replay_data = replay.json()["data"]
        assert replay_data["receipt_id"] == first_data["receipt_id"]
        assert replay.json()["meta"]["idempotency"]["replayed"] is True

        action_receipt = first_data["action_receipt"]
        command_receipt = first_data["command_receipt"]
        assert action_receipt["receipt_type"] == "action"
        assert command_receipt["receipt_type"] == "command"
        assert action_receipt["receipt_id"] == command_receipt["receipt_id"] == first_data["receipt_id"]
        assert command_receipt["command"] == "StrategyAction"

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        dual_write_log = records[0]["audit"]["receipt_dual_write"]
        assert dual_write_log["action_receipt"]["receipt_type"] == "action"
        assert dual_write_log["command_receipt"]["receipt_type"] == "command"
        assert dual_write_log["command_receipt"]["receipt_id"] == first_data["receipt_id"]


def test_bff_v1_commands_missing_confirm_token_returns_confirm_token_required() -> None:
    with _isolated_bff() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                **OPERATOR_HEADERS,
                "Idempotency-Key": "bff-consol-021-missing-confirm",
                "X-Correlation-Id": "corr-bff-consol-021-confirm",
            },
            json={
                "command": "PauseRuntime",
                "target": {"type": "Runtime", "id": "runtime-bff-consol-021"},
                "params": {
                    "runtime_binding_id": "rb-bff-consol-021",
                    "pause_action": "pause",
                },
                "audit_context": {"reason": "Pause runtime after operator review"},
            },
        )

        _assert_error_code(response, status_code=428, code="CONFIRM_TOKEN_REQUIRED")
        assert bff_main.command_store._get_all_commands() == []


def test_bff_v1_commands_missing_approval_returns_approval_required() -> None:
    with _isolated_bff() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                **APPROVER_HEADERS,
                "Idempotency-Key": "bff-consol-021-missing-approval",
                "X-Correlation-Id": "corr-bff-consol-021-approval",
            },
            json={
                "command": "ApproveDecision",
                "target": {"type": "ApprovalDecision", "id": "appr-bff-consol-021"},
                "params": {"decision_id": "appr-bff-consol-021"},
                "audit_context": {"reason": "Approve after evidence review"},
            },
        )

        _assert_error_code(response, status_code=409, code="APPROVAL_REQUIRED")
        assert bff_main.command_store._get_all_commands() == []
