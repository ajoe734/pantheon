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


OPERATOR_HEADERS = {
    "Authorization": "Bearer op-bff-019:operator,approver:mfa",
    "Idempotency-Key": "bff-consol-019-action",
    "X-Trace-Id": "trace-bff-consol-019",
    "X-Correlation-Id": "corr-bff-consol-019",
    "X-Request-Id": "req-bff-consol-019",
}


@contextmanager
def _isolated_action_adapter() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store


def test_bff_actions_adapter_records_final_command_foundation_context() -> None:
    with _isolated_action_adapter() as client:
        response = client.post(
            "/bff/actions/strategy/stg-bff-019/submit_review",
            headers=OPERATOR_HEADERS,
            json={"reason": "submit strategy review through action adapter"},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "accepted"
        assert body["data"]["command"] == "StrategyAction"

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        record = records[0]
        assert record["type"] == "StrategyAction"
        assert record["target"] == {"type": "Strategy", "id": "stg-bff-019"}
        assert record["params"]["action_id"] == "submit_review"
        assert record["params"]["entity_type"] == "strategy"
        assert record["params"]["audit_event"] == "strategy.submit_review"

        foundation = record["foundation"]
        assert foundation["admission_route"] == "POST /bff/v1/commands"
        assert foundation["source_route"] == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        assert foundation["trace_context"]["trace_id"] == "trace-bff-consol-019"
        assert foundation["trace_context"]["correlation_id"] == "corr-bff-consol-019"
        assert foundation["trace_context"]["request_id"] == "req-bff-consol-019"
        assert foundation["idempotency_record"]["idempotency_key"] == "bff-consol-019-action"
        assert foundation["policy_decision"]["decision"] == "allow"

        envelope = foundation["command_envelope"]
        assert envelope["actor_ref"]["actor_id"] == "op-bff-019"
        assert envelope["authority_scope"]["target_ref"] == "Strategy:stg-bff-019"
        assert envelope["payload"]["source_route"] == "POST /bff/actions/{entityType}/{entityId}/{actionId}"

        audit = record["audit"]
        assert audit["operator_id"] == "op-bff-019"
        assert audit["action_id"] == "submit_review"
        assert audit["audit_event"] == "strategy.submit_review"
        assert audit["foundation"]["policy_decision"]["decision"] == "allow"
        assert audit["foundation"]["audit_action"]["metadata"]["route"] == "POST /bff/v1/commands"
        assert (
            audit["foundation"]["audit_action"]["metadata"]["source_route"]
            == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        )


def test_bff_actions_adapter_requires_idempotency_key() -> None:
    with _isolated_action_adapter() as client:
        headers = {k: v for k, v in OPERATOR_HEADERS.items() if k != "Idempotency-Key"}
        response = client.post(
            "/bff/actions/strategy/stg-bff-019/submit_review",
            headers=headers,
            json={"reason": "missing key should fail admission"},
        )

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert detail["error"]["code"] == "INVALID_PARAMS"
        assert detail["error"]["details"]["precondition_failed"] == "idempotency_key"
        assert bff_main.command_store._get_all_commands() == []


def test_bff_actions_adapter_policy_denial_records_foundation_error() -> None:
    with _isolated_action_adapter() as client:
        response = client.post(
            "/bff/actions/strategy/stg-bff-019/submit_review",
            headers={
                **OPERATOR_HEADERS,
                "Authorization": "Bearer op-viewer:view_only",
                "Idempotency-Key": "bff-consol-019-denied",
            },
            json={"reason": "viewer should not submit command actions"},
        )

        assert response.status_code == 403, response.text
        detail = response.json()["detail"]
        assert detail["foundation_error"]["error_kind"] == "policy_denial"
        assert detail["policy_decision"]["decision"] == "deny"
        assert detail["audit_action"]["metadata"]["route"] == "POST /bff/v1/commands"
        assert (
            detail["audit_action"]["metadata"]["source_route"]
            == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        )
        assert bff_main.command_store._get_all_commands() == []
