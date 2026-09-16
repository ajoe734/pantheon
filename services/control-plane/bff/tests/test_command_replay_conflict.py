from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import OperatorIdentity


HEADERS = {
    "Authorization": "Bearer op-bff-021:operator,approver,admin:mfa",
    "X-Trace-Id": "trace-bff-consol-021",
    "X-Correlation-Id": "corr-bff-consol-021",
    "X-Request-Id": "req-bff-consol-021",
}


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


_current_command_store: Optional[CommandStore] = None


class _StoreProxy:
    def _get_all_commands(self) -> list[dict[str, Any]]:
        if _current_command_store is None:
            return []
        return _current_command_store._get_all_commands()


command_store = _StoreProxy()


def _error_detail(response) -> dict:
    body = response.json()
    return body.get("detail") or body


@contextmanager
def _isolated_command_client() -> Iterator[TestClient]:
    global _current_command_store
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        _current_command_store = store
        svc = CommandAdapterService(
            command_store=store,
            read_surface=None,
            extract_identity=_test_extract_identity,
        )
        app = FastAPI()
        register_error_handlers(app)
        router = create_command_adapters_router(
            service=svc,
            submit_command_admission=svc.submit_command_admission,
        )
        app.include_router(router)
        try:
            yield TestClient(app)
        finally:
            _current_command_store = None


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


def _strategy_action_payload(
    reason: str,
    *,
    strategy_id: str = "stg-bff-021",
    action_id: str = "submit_review",
) -> dict:
    """Build the same StrategyAction command envelope the retired
    `/bff/actions/{type}/{id}/{action}` adapter route used to construct
    before forwarding into the shared `/bff/v1/commands` admission path.
    """
    audit_event = f"strategy.{action_id}"
    return {
        "command": "StrategyAction",
        "target": {"type": "Strategy", "id": strategy_id},
        "action": action_id,
        "params": {
            "reason": reason,
            "action_id": action_id,
            "actionId": action_id,
            "entity_type": "strategy",
            "entityType": "strategy",
            "entity_id": strategy_id,
            "entityId": strategy_id,
            "audit_event": audit_event,
        },
        "audit_context": {"reason": reason},
    }


def test_strategy_action_command_writes_command_receipt() -> None:
    with _isolated_command_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "bff-consol-021-action-dual"},
            json=_strategy_action_payload("submit strategy review through dual-write proof"),
        )

        assert response.status_code == 202, response.text
        assert "Deprecation" not in response.headers
        body = response.json()
        action_receipt_id = _receipt_id(body)
        assert body["data"]["command"] == "StrategyAction"
        assert "deprecated" not in body["data"]
        assert body["meta"]["durable"] is True
        assert body["meta"]["idempotency"]["idempotencyKey"] == "bff-consol-021-action-dual"
        assert body["meta"]["idempotency"]["replayed"] is False

        records = command_store._get_all_commands()
        assert len(records) == 1
        command_receipt = records[0]
        assert action_receipt_id == command_receipt["command_id"]
        assert command_receipt["type"] == "StrategyAction"
        assert command_receipt["target"] == {"type": "Strategy", "id": "stg-bff-021"}
        assert command_receipt["params"]["action_id"] == "submit_review"

        foundation = command_receipt["foundation"]
        assert foundation["admission_route"] == "POST /bff/v1/commands"
        assert foundation["idempotency_record"]["idempotency_key"] == "bff-consol-021-action-dual"
        assert command_receipt["audit"]["foundation"]["audit_action"]["metadata"]["route"] == (
            "POST /bff/v1/commands"
        )


def test_strategy_action_idempotency_replay_returns_same_receipt() -> None:
    with _isolated_command_client() as client:
        headers = {**HEADERS, "Idempotency-Key": "bff-consol-021-action-replay"}
        payload = _strategy_action_payload("same strategy action body replays")

        first = client.post("/bff/v1/commands", headers=headers, json=payload)
        second = client.post("/bff/v1/commands", headers=headers, json=payload)

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert "Deprecation" not in second.headers
        assert _receipt_id(second.json()) == _receipt_id(first.json())
        assert "deprecated" not in second.json()["data"]
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        assert len(command_store._get_all_commands()) == 1


def test_strategy_action_idempotency_conflict_returns_409() -> None:
    with _isolated_command_client() as client:
        headers = {**HEADERS, "Idempotency-Key": "bff-consol-021-action-conflict"}

        first = client.post(
            "/bff/v1/commands",
            headers=headers,
            json=_strategy_action_payload("initial action body"),
        )
        conflict = client.post(
            "/bff/v1/commands",
            headers=headers,
            json=_strategy_action_payload("changed action body"),
        )

        assert first.status_code == 202, first.text
        assert conflict.status_code == 409, conflict.text
        detail = _error_detail(conflict)
        assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["foundation_error"]["error_code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["audit_action"]["action_type"] == "bff.command.idempotency_conflict"
        assert len(command_store._get_all_commands()) == 1


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
        records = command_store._get_all_commands()
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
        assert len(command_store._get_all_commands()) == 1


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
        assert command_store._get_all_commands() == []


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
        assert command_store._get_all_commands() == []
