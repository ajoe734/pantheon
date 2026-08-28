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
    old_auth = os.environ.get("PANTHEON_BFF_AUTH_MODE")
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            if old_auth is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = old_auth


def test_bff_actions_openapi_exposes_frontend_and_generic_action_templates() -> None:
    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()

    assert "/bff/actions/{type}/{id}/{action}" in schema["paths"]
    generic = schema["paths"]["/bff/actions/{type}/{id}/{action}"]["post"]
    generic_path_params = [param["name"] for param in generic["parameters"] if param.get("in") == "path"]
    assert generic_path_params == ["type", "id", "action"]
    assert generic["operationId"] == "submit_bff_action_generic"
    assert generic["deprecated"] is True


def test_bff_actions_adapter_records_final_command_foundation_context() -> None:
    with _isolated_action_adapter() as client:
        response = client.post(
            "/bff/actions/strategy/stg-bff-019/submit_review",
            headers=OPERATOR_HEADERS,
            json={"reason": "submit strategy review through action adapter"},
        )

        assert response.status_code == 202, response.text
        assert response.headers["Deprecation"] == "true"
        assert response.headers["Sunset"] == "Mon, 15 Jun 2026 00:00:00 GMT"
        assert response.headers["X-Pantheon-Deprecated-Route"] == "/bff/actions/*"
        body = response.json()
        assert body["status"] == "accepted"
        assert body["data"]["command"] == "StrategyAction"
        assert body["data"]["deprecated"] is True
        assert body["data"]["deprecation"]["route"] == "/bff/actions/{type}/{id}/{action}"
        assert body["data"]["receipt"]["deprecated"] is True
        assert body["meta"]["deprecation"]["replacement"] == "/bff/v1/commands"

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


def test_bff_actions_named_facade_accepts_x_idempotency_alias() -> None:
    with _isolated_action_adapter() as client:
        headers = {
            **{key: value for key, value in OPERATOR_HEADERS.items() if key != "Idempotency-Key"},
            "X-Idempotency-Key": "bff-b1-008-action-alias",
        }
        response = client.post(
            "/bff/actions/strategy/stg-bff-008/submit_review",
            headers=headers,
            json={"reason": "submit strategy review through named action facade"},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "accepted"
        assert body["data"]["command"] == "StrategyAction"
        assert body["meta"]["idempotency"]["idempotencyKey"] == "bff-b1-008-action-alias"

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        foundation = records[0]["foundation"]
        assert foundation["admission_route"] == "POST /bff/v1/commands"
        assert foundation["source_route"] == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        assert foundation["idempotency_record"]["idempotency_key"] == "bff-b1-008-action-alias"
        assert foundation["trace_context"]["correlation_id"] == "corr-bff-consol-019"


def test_bff_actions_named_facade_rejects_body_idempotency_key() -> None:
    with _isolated_action_adapter() as client:
        response = client.post(
            "/bff/actions/strategy/stg-bff-008/submit_review",
            headers=OPERATOR_HEADERS,
            json={
                "idempotencyKey": "body-key-must-not-be-used",
                "reason": "body idempotency key must fail closed",
            },
        )

        assert response.status_code == 400, response.text
        detail = response.json()
        assert detail["error"]["code"] == "VALIDATION_FAILED"
        assert detail["error"]["details"]["precondition_failed"] == "body_idempotency_key"
        assert bff_main.command_store._get_all_commands() == []


def test_bff_actions_adapter_requires_idempotency_key() -> None:
    with _isolated_action_adapter() as client:
        headers = {k: v for k, v in OPERATOR_HEADERS.items() if k != "Idempotency-Key"}
        response = client.post(
            "/bff/actions/strategy/stg-bff-019/submit_review",
            headers=headers,
            json={"reason": "missing key should fail admission"},
        )

        assert response.status_code == 400, response.text
        detail = response.json()
        assert detail["error"]["code"] == "VALIDATION_FAILED"
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
        detail = response.json()
        assert detail["foundation_error"]["error_kind"] == "policy_denial"
        assert detail["policy_decision"]["decision"] == "deny"
        assert detail["audit_action"]["metadata"]["route"] == "POST /bff/v1/commands"
        assert (
            detail["audit_action"]["metadata"]["source_route"]
            == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        )
        assert bff_main.command_store._get_all_commands() == []


def test_command_adapters_router_single_route_uniqueness() -> None:
    from command_adapters.router import create_action_command_router
    from fastapi import FastAPI

    router = create_action_command_router()
    app = FastAPI()
    app.include_router(router)

    schema = app.openapi()
    action_paths = [p for p in schema["paths"] if p.startswith("/bff/actions/")]
    assert action_paths == ["/bff/actions/{type}/{id}/{action}"]
    assert len(router.routes) == 1
    route = router.routes[0]
    assert route.path == "/bff/actions/{type}/{id}/{action}"
    assert "POST" in getattr(route, "methods", set())
    assert getattr(route, "operation_id", None) == "submit_bff_action_generic"


def test_command_adapters_router_standalone_execution() -> None:
    from command_adapters.router import create_action_command_router
    from fastapi import FastAPI

    with tempfile.TemporaryDirectory() as td:
        cstore = CommandStore(os.path.join(td, "commands.jsonl"))
        router = create_action_command_router(command_store=cstore)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            "/bff/actions/strategy/stg-bff-019/submit_review",
            headers=OPERATOR_HEADERS,
            json={"reason": "submit strategy review through standalone action router"},
        )
        assert resp.status_code == 202
        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["Sunset"] == "Mon, 15 Jun 2026 00:00:00 GMT"
        assert resp.headers["X-Pantheon-Deprecated-Route"] == "/bff/actions/*"
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["data"]["command"] == "StrategyAction"

        records = cstore._get_all_commands()
        assert len(records) == 1
        assert records[0]["type"] == "StrategyAction"
        assert records[0]["target"] == {"type": "Strategy", "id": "stg-bff-019"}


def test_command_adapters_router_validations() -> None:
    from command_adapters.router import create_action_command_router
    from fastapi import FastAPI

    router = create_action_command_router()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Reject body idempotency key
    resp = client.post(
        "/bff/actions/strategy/stg-01/submit_review",
        headers=OPERATOR_HEADERS,
        json={"idempotencyKey": "body-key", "reason": "test"},
    )
    assert resp.status_code == 400
    err = resp.json().get("detail", {}).get("error", {}) or resp.json().get("error", {})
    assert err.get("details", {}).get("precondition_failed") == "body_idempotency_key"

    # 2. Require header idempotency key
    resp = client.post(
        "/bff/actions/strategy/stg-01/submit_review",
        headers={"Authorization": "Bearer op-1:operator"},
        json={"reason": "test"},
    )
    assert resp.status_code == 400
    err = resp.json().get("detail", {}).get("error", {}) or resp.json().get("error", {})
    assert err.get("details", {}).get("precondition_failed") == "idempotency_key"

    # 3. Reject unsupported entity type
    resp = client.post(
        "/bff/actions/invalid_entity_type/stg-01/submit_review",
        headers=OPERATOR_HEADERS,
        json={"reason": "test"},
    )
    assert resp.status_code == 422
    err = resp.json().get("detail", {}).get("error", {}) or resp.json().get("error", {})
    assert err.get("details", {}).get("precondition_failed") == "entity_type"

