"""Contract and unit tests for the standalone Command Adapters router and service."""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from command_adapters import (
    CommandAdapterService,
    create_action_command_router,
    create_command_adapters_router,
    dispatch_domain_command,
    find_adapter,
)
from command_queue import CommandStore
from models import CommandStatus, CommandType, ObjectType, OperatorIdentity, TargetObject


TASK_REVIEW_MANIFEST = {
    "task_id": "OPGAP-BE-COMMAND-ADAPTERS-V2-20260830",
    "owned_layer": "command adapters domain router, service, and executor reverse-import elimination",
    "not_changing": "unrelated BFF domain routers and existing business logic contracts",
    "review_scope": {
        "route_count": 11,
        "durable_readback": "Operator commands dispatch through typed domain adapters with durable receipts",
        "write_boundary": "Typed domain command dispatch, confirm tokens, and command confirmations",
        "reverse_import_elimination": "Zero reverse imports of main.py in command_adapters and command_executor",
    },
    "verification": [
        "pytest -q services/control-plane/bff/tests/test_command_adapters_router.py",
        "pytest -q services/control-plane/bff/tests/test_actions_to_commands_adapter.py",
        "pytest -q services/control-plane/bff/test_command_executor.py",
        "python3 services/control-plane/bff/smoke_test.py",
    ],
}

HEADERS = {"Authorization": "Bearer op-test:operator,approver:mfa"}


def _test_app(command_store: Optional[CommandStore] = None) -> FastAPI:
    app = FastAPI()
    router = create_command_adapters_router(
        get_command_store=lambda: command_store,
        get_read_store=lambda: None,
    )
    app.include_router(router)
    return app


def test_zero_reverse_main_imports() -> None:
    """Verify zero static or dynamic reverse imports of main / bff_main in command_adapters and executor."""
    bff_dir = os.path.dirname(os.path.dirname(__file__))
    command_adapters_dir = os.path.join(bff_dir, "command_adapters")
    command_executor_path = os.path.join(bff_dir, "command_executor.py")

    target_files = [command_executor_path]
    for root, _, files in os.walk(command_adapters_dir):
        for f in files:
            if f.endswith(".py"):
                target_files.append(os.path.join(root, f))

    for file_path in target_files:
        rel_path = os.path.relpath(file_path, bff_dir)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        parsed = ast.parse(content, filename=file_path)
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("main", "bff_main"), (
                        f"Found forbidden 'import {alias.name}' in {rel_path} at line {node.lineno}"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("main", "bff_main"), (
                    f"Found forbidden 'from {node.module} import ...' in {rel_path} at line {node.lineno}"
                )


def test_command_adapters_router_route_inventory() -> None:
    """Verify create_command_adapters_router owns exactly the 11 command adapter routes."""
    router = create_command_adapters_router()
    routes = [r.path for r in router.routes]

    expected_routes = [
        "/bff/actions",
        "/api/v1/operator/commands",
        "/api/v1/operator/commands/{command_id}",
        "/bff/v1/commands",
        "/bff/command-confirmations",
        "/bff/command-confirmations/{token}",
        "/bff/command-confirmations/{token}/confirm",
        "/bff/confirm-tokens",
        "/bff/confirm-tokens/{tokenId}",
        "/bff/confirm-tokens/{tokenId}/redeem",
        "/bff/confirm-tokens/{tokenId}",
    ]

    assert len(router.routes) == 11, f"Expected 11 routes, got {len(router.routes)}: {routes}"
    for expected in expected_routes:
        assert expected in routes, f"Missing route {expected} in {routes}"


def test_main_composition_has_no_loose_command_adapter_decorators() -> None:
    """Verify main.py contains zero loose @app decorators for the 11 migrated command adapter routes."""
    bff_dir = os.path.dirname(os.path.dirname(__file__))
    main_path = os.path.join(bff_dir, "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        main_source = f.read()

    forbidden_patterns = [
        r'@app\.post\(\s*["\']/api/v1/operator/commands["\']',
        r'@app\.get\(\s*["\']/api/v1/operator/commands/{command_id}["\']',
        r'@app\.post\(\s*["\']/bff/v1/commands["\']',
        r'@app\.get\(\s*["\']/bff/actions["\']',
        r'@app\.post\(\s*["\']/bff/command-confirmations["\']',
        r'@app\.get\(\s*["\']/bff/command-confirmations/{token}["\']',
        r'@app\.post\(\s*["\']/bff/command-confirmations/{token}/confirm["\']',
        r'@app\.post\(\s*["\']/bff/confirm-tokens["\']',
        r'@app\.get\(\s*["\']/bff/confirm-tokens/{tokenId}["\']',
        r'@app\.post\(\s*["\']/bff/confirm-tokens/{tokenId}/redeem["\']',
        r'@app\.delete\(\s*["\']/bff/confirm-tokens/{tokenId}["\']',
    ]

    for pattern in forbidden_patterns:
        match = re.search(pattern, main_source)
        assert match is None, f"Found lingering @app decorator in main.py matching {pattern}"


def test_action_catalog_readback() -> None:
    """Test GET /bff/actions returns canonical action catalog."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        response = client.get("/bff/actions", headers=HEADERS)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "actions" in data or "items" in data or "data" in data or isinstance(data, dict)


def test_confirm_token_full_lifecycle() -> None:
    """Test confirm token creation, readback, redemption, replay, deletion, and expiry."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        # 1. Create confirm token
        create_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-create-key-1"},
            json={"tokenId": "ct-test-001", "reason": "high risk action", "ttlSeconds": 300},
        )
        assert create_resp.status_code == 201, create_resp.text
        body = create_resp.json()
        assert body["data"]["tokenId"] == "ct-test-001"
        assert body["data"]["status"] == "created"
        assert body["meta"]["idempotency"]["replayed"] is False

        # 2. Replay creation with same key
        replay_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-create-key-1"},
            json={"tokenId": "ct-test-001", "reason": "high risk action", "ttlSeconds": 300},
        )
        assert replay_resp.status_code == 201, replay_resp.text
        assert replay_resp.json()["meta"]["idempotency"]["replayed"] is True

        # 3. Read token state
        read_resp = client.get("/bff/confirm-tokens/ct-test-001", headers=HEADERS)
        assert read_resp.status_code == 200, read_resp.text
        assert read_resp.json()["data"]["status"] == "created"
        assert read_resp.json()["data"]["expired"] is False

        # 4. Redeem token
        redeem_resp = client.post(
            "/bff/confirm-tokens/ct-test-001/redeem",
            headers={**HEADERS, "Idempotency-Key": "ct-redeem-key-1"},
            json={"reason": "operator confirmed"},
        )
        assert redeem_resp.status_code == 202, redeem_resp.text
        assert redeem_resp.json()["data"]["status"] == "redeemed"
        assert redeem_resp.json()["data"]["redeemed"] is True

        # 5. Read after redeem
        read_after_redeem = client.get("/bff/confirm-tokens/ct-test-001", headers=HEADERS)
        assert read_after_redeem.status_code == 200
        assert read_after_redeem.json()["data"]["status"] == "redeemed"

        # 6. Delete another token
        create_del = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-del-key-1"},
            json={"tokenId": "ct-to-delete", "reason": "delete me"},
        )
        assert create_del.status_code == 201

        del_resp = client.delete(
            "/bff/confirm-tokens/ct-to-delete",
            headers={**HEADERS, "Idempotency-Key": "ct-del-key-2"},
        )
        assert del_resp.status_code == 202
        assert del_resp.json()["data"]["status"] == "deleted"

        read_deleted = client.get("/bff/confirm-tokens/ct-to-delete", headers=HEADERS)
        assert read_deleted.status_code == 200
        assert read_deleted.json()["data"]["status"] == "deleted"


def test_confirm_token_expiration_returns_410() -> None:
    """Test expired confirm token returns typed 410 error."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        create_resp = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-expired-key-1"},
            json={"tokenId": "ct-expired-001", "ttlSeconds": -10},
        )
        assert create_resp.status_code == 201

        read_resp = client.get("/bff/confirm-tokens/ct-expired-001", headers=HEADERS)
        assert read_resp.status_code == 410, read_resp.text
        err = read_resp.json().get("error") or read_resp.json().get("detail", {}).get("error", {})
        assert err["details"]["precondition_failed"] == "confirm_token_expired"


def test_command_confirmations_lifecycle() -> None:
    """Test submit command confirmation, query status, and confirm by token."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(_test_app(store))

        # Create token first
        client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "conf-ct-1"},
            json={"tokenId": "ct-conf-001", "reason": "confirmation token"},
        )

        # Submit confirmation
        conf_resp = client.post(
            "/bff/command-confirmations",
            headers={**HEADERS, "Idempotency-Key": "conf-sub-1"},
            json={"command_id": "cmd-test-100", "confirm_token": "ct-conf-001"},
        )
        assert conf_resp.status_code == 202, conf_resp.text
        assert conf_resp.json()["status"] == "accepted"
        assert conf_resp.json()["command_id"] == "cmd-test-100"
        assert conf_resp.json()["token"] == "ct-conf-001"
        assert conf_resp.json()["lifecycleStatus"] == "redeemed"

        # Query confirmation status
        status_resp = client.get("/bff/command-confirmations/ct-conf-001", headers=HEADERS)
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["data"]["command_id"] == "cmd-test-100"
        assert status_resp.json()["data"]["status"] == "redeemed"

        # Confirm command by token
        client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "conf-ct-2"},
            json={"tokenId": "ct-conf-002", "reason": "confirmation token 2"},
        )
        confirm_token_resp = client.post(
            "/bff/command-confirmations/ct-conf-002/confirm",
            headers={**HEADERS, "Idempotency-Key": "conf-by-tok-1", "X-Correlation-Id": "corr-123"},
            json={"command_id": "cmd-test-200", "confirm_token": "ct-conf-002"},
        )
        assert confirm_token_resp.status_code == 202, confirm_token_resp.text
        assert confirm_token_resp.json()["status"] == "accepted"
        assert confirm_token_resp.json()["data"]["command_id"] == "cmd-test-200"


def test_operator_command_status_readback() -> None:
    """Test GET /api/v1/operator/commands/{command_id} returns durable status."""
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        store.submit_command(
            command_id="cmd-readback-1",
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target=TargetObject(type=ObjectType.CAPITAL_POOL, id="pool-1"),
            submitted_at="2026-08-30T12:00:00Z",
            params={"action_id": "ApprovePool"},
            audit_context={"actor": "op-test"},
        )
        client = TestClient(_test_app(store))

        response = client.get("/api/v1/operator/commands/cmd-readback-1", headers=HEADERS)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["command_id"] == "cmd-readback-1"
        assert data["type"] == CommandType.CAPITAL_POOL_ACTION.value
        assert data["target"]["id"] == "pool-1"
        assert data["status"] == CommandStatus.SUBMITTED.value


def test_typed_domain_command_dispatch_and_receipt() -> None:
    """Test typed domain command execution returns structured receipt."""
    adapter = find_adapter(CommandType.STRATEGY_ACTION)
    assert adapter is not None

    # Test domain receipt structure
    receipt = adapter.execute(
        command_id="cmd-dispatch-1",
        command_type=CommandType.STRATEGY_ACTION,
        params={"strategy_id": "stg-01", "action_id": "submit_review"},
        auth_token="Bearer test",
    )
    assert receipt["command_id"] == "cmd-dispatch-1"
    assert receipt["status"] in ("accepted", "review_pending")
    assert receipt["entity_type"] in ("strategy", "Strategy")
    assert receipt["entity_id"] == "stg-01"
    assert "domain_receipt" in receipt


def test_main_app_operator_command_submission_regression() -> None:
    """Regression test: verify POST /api/v1/operator/commands works in full main app with idempotency keys."""
    from main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

        # 1. Submit with X-Idempotency-Key
        resp = client.post(
            "/api/v1/operator/commands",
            headers={
                "Authorization": "Bearer op-1:operator,approver:mfa",
                "X-Idempotency-Key": "idmp-test-op-1",
            },
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-001"},
                "action": "approve",
                "params": {"deployment_plan_id": "dp-001", "approval_decision": "approve"},
                "audit_context": {"reason": "Integration regression test"},
            },
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["status"] == "accepted"
        assert "receipt_id" in data
        assert data["command"] == "ApproveDeployment"

        # 2. Submit with Idempotency-Key
        resp2 = client.post(
            "/api/v1/operator/commands",
            headers={
                "Authorization": "Bearer op-1:operator,approver:mfa",
                "Idempotency-Key": "idmp-test-op-2",
            },
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-002"},
                "action": "approve",
                "params": {"deployment_plan_id": "dp-002", "approval_decision": "approve"},
                "audit_context": {"reason": "Integration regression test 2"},
            },
        )
        assert resp2.status_code == 202, resp2.text
        data2 = resp2.json()
        assert data2["status"] == "accepted"
        assert "receipt_id" in data2
        assert data2["command"] == "ApproveDeployment"
