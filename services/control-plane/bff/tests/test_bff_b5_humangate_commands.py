from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, Header, Query, Request
from fastapi.testclient import TestClient

from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.command_adapters import (
    CommandAdapterService,
    create_command_adapters_router,
)
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    OperatorIdentity,
)


HEADERS = {
    "Authorization": "Bearer op-b5-human:operator,reviewer,approver:mfa",
    "X-Trace-Id": "trace-bff-b5-001",
    "X-Correlation-Id": "corr-bff-b5-001",
    "X-Request-Id": "req-bff-b5-001",
}


def _test_extract_identity(
    authorization: Optional[str] = None, mfa_token: Optional[str] = None
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
    )


class MockReadStore:
    def __init__(self, approvals: list[dict[str, Any]]):
        self.approvals = approvals

    def get_approval_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        for item in self.approvals:
            if item.get("decision_id") == decision_id:
                return item
        return None

    def get_ranking_snapshot(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        from datetime import datetime, timezone
        from services.control_plane.bff.pm12.service import _stable_json_hash, _PM12_LEAGUE_FORMULA_VERSION
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "surface": "quarterly",
            "period": "2026-Q1",
            "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "items": [
                {
                    "persona_id": "p-1",
                    "score": 90.0,
                    "components": {"risk_score": 80.0, "execution_score": 75.0},
                }
            ],
        }
        return {
            "snapshot_id": snapshot_id,
            "created_at": now,
            **payload,
            "content_digest": _stable_json_hash(payload),
        }


command_store: Optional[CommandStore] = None
read_store: Optional[MockReadStore] = None
_GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


def _build_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    service = CommandAdapterService(
        command_store=lambda: command_store,
        read_surface=lambda: read_store,
        extract_identity=_test_extract_identity,
        gov_bff_idempotency=_GOV_BFF_IDEMPOTENCY,
    )

    cmd_router = create_command_adapters_router(service=service)
    app.include_router(cmd_router)

    @app.get("/bff/management/human-inbox")
    async def _human_inbox(
        source_type: Optional[str] = Query(default=None),
        page_size: int = Query(default=20),
    ):
        return {
            "data": {
                "items": [
                    {
                        "id": "approval:b5-human-001",
                        "source_id": "b5-human-001",
                        "source_type": "approval",
                        "status": "pending",
                        "title": "B5 HumanGate approval fixture.",
                    }
                ]
            }
        }

    @app.get("/bff/management/quarterly-ranking/recommendations")
    async def _quarterly_ranking_recommendations(
        quarter: Optional[str] = Query(default=None),
        page_size: int = Query(default=20),
    ):
        q = quarter or "2026-Q1"
        return {
            "data": {
                "items": [
                    {
                        "quarter": q,
                        "recommendation_id": f"pm12-{q.lower()}-p-1-promote_to_canary_candidate",
                        "action_id": "promote_to_canary_candidate",
                        "persona_id": "p-1",
                        "ranking_snapshot_id": "snap-b5-001",
                        "live_capital_mutation": False,
                    }
                ]
            }
        }

    return app


@contextmanager
def _isolated_b5_client() -> Iterator[TestClient]:
    global command_store, read_store
    with tempfile.TemporaryDirectory() as td:
        command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        read_store = MockReadStore(
            approvals=[
                {"decision_id": "b5-human-001", "status": "pending", "requested_by": "governance-queue"},
                {"decision_id": "b5-revoke", "status": "pending", "requested_by": "governance-queue"},
            ]
        )
        _GOV_BFF_IDEMPOTENCY.clear()
        app = _build_test_app()
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            command_store = None
            read_store = None
            _GOV_BFF_IDEMPOTENCY.clear()


def _submit_command(
    client: TestClient,
    *,
    command: str,
    target_type: str,
    target_id: str,
    params: dict | None = None,
    idempotency_key: str,
):
    return client.post(
        "/bff/v1/commands",
        headers={**HEADERS, "Idempotency-Key": idempotency_key},
        json={
            "command": command,
            "target": {"type": target_type, "id": target_id},
            "action": "submit",
            "params": params or {},
            "audit_context": {"reason": f"BFF-B5-001 {command} contract test"},
        },
    )


def _accepted_command_id(payload: dict) -> str:
    assert payload["status"] == "accepted"
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["trackingUrl"].startswith("/api/v1/operator/commands/")
    assert payload["data"]["receipt"]["status"] == "accepted"
    assert payload["meta"]["durable"] is True
    assert payload["meta"]["liveCapitalSideEffects"] is False
    return payload["data"]["command_id"]


def test_humangate_command_names_are_admitted_through_bff_v1_commands() -> None:
    cases = [
        ("HumanGateRevoke", "approval:b5-revoke", {"revoke_reason": "stale approval"}),
    ]

    with _isolated_b5_client() as client:
        for command, target_id, params in cases:
            response = _submit_command(
                client,
                command=command,
                target_type="HumanGateItem",
                target_id=target_id,
                params=params,
                idempotency_key=f"bff-b5-{command}",
            )

            assert response.status_code == 202, response.text
            payload = response.json()
            command_id = _accepted_command_id(payload)
            assert payload["data"]["command"] == command
            assert payload["meta"]["idempotency"]["idempotencyKey"] == f"bff-b5-{command}"

            assert command_store is not None
            record = command_store.get_command(command_id)
            assert record is not None
            assert record["type"] == command
            assert record["target"] == {"type": "HumanGateItem", "id": target_id}
            assert record["params"]["human_gate_item_id"] == target_id
            assert record["params"]["itemId"] == target_id
            assert record["params"]["decision"] == "revoke"
            assert record["params"]["audit_event"].startswith("human_gate.")
            assert record["foundation"]["admission_route"] == "POST /bff/v1/commands"


def test_b5_commands_are_in_action_catalog() -> None:
    expected = {
        "HumanGateRevoke": "HumanGateItem",
    }

    for command, entity_type in expected.items():
        entry = get_catalog_entry(command)
        assert entry is not None
        assert entry.entity_type == entity_type
        assert entry.endpoint == "/bff/v1/commands"
