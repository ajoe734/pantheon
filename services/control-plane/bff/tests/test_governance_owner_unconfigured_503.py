"""Tests verifying that an unconfigured Governance approval owner yields 503 DEPENDENCY_UNAVAILABLE.

Acceptance Criteria:
  - An unconfigured Governance approval owner yields 503 DEPENDENCY_UNAVAILABLE retryable
    on the command route (POST /bff/v1/commands) and on the approval list read
    (GET /bff/approvals, GET /api/v1/approval-decisions).
  - Direct execution via command_executor also yields 503 DEPENDENCY_UNAVAILABLE retryable.
  - A configured owner that answers keeps its current behaviour.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import CommandStatus, CommandType, utc_now
from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports

OPERATOR_TOKEN = "Bearer op-1:operator:tenant-test"
APPROVER_TOKEN = "Bearer op-2:approver:tenant-test"


@pytest.fixture(autouse=True)
def _clear_governance_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
        "PANTHEON_GOVERNANCE_SERVICE_URL",
        "PANTHEON_GOVERNANCE_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _build_test_app(read_store: Any) -> FastAPI:
    commands_path = os.path.join(tempfile.gettempdir(), f"test-cmd-{os.getpid()}.jsonl")
    command_store = CommandStore(commands_path)
    service = CommandAdapterService(
        command_store=command_store,
        read_surface=read_store,
        extract_identity=lambda auth, mfa_token=None: extract_identity_stub(auth),
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
    )
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(create_command_adapters_router(service=service))
    app.include_router(
        create_governance_router(
            read_surface=read_store,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
        )
    )
    register_error_handlers(app)
    return app


def test_command_route_unconfigured_governance_owner_returns_503() -> None:
    """POST /bff/v1/commands carrying approval evidence returns 503 DEPENDENCY_UNAVAILABLE retryable."""
    read_store = create_read_surface_ports()
    app = _build_test_app(read_store)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": APPROVER_TOKEN,
            "Idempotency-Key": "test-unconfigured-cmd-1",
        },
        json={
            "command": "ApproveDeployment",
            "target": {"type": "DeploymentPlan", "id": "dp-001"},
            "action": "approve",
            "params": {
                "deployment_plan_id": "dp-001",
                "approval_decision": "approve",
                "approvalId": "appr-dp-001",
            },
            "audit_context": {"reason": "Test unconfigured 503"},
        },
    )

    assert response.status_code == 503
    payload = response.json()
    assert "error" in payload
    error = payload["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["retryable"] is True
    assert error["userActionable"] is True
    assert "meta" in payload
    assert "correlationId" in payload["meta"]


def test_approval_list_routes_unconfigured_governance_owner_return_503() -> None:
    """GET /bff/approvals and GET /api/v1/approval-decisions return 503 DEPENDENCY_UNAVAILABLE retryable."""
    read_store = create_read_surface_ports()
    app = _build_test_app(read_store)
    client = TestClient(app, raise_server_exceptions=False)

    resp_bff = client.get("/bff/approvals", headers={"Authorization": OPERATOR_TOKEN})
    assert resp_bff.status_code == 503
    payload_bff = resp_bff.json()
    assert payload_bff["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert payload_bff["error"]["retryable"] is True

    resp_api = client.get("/api/v1/approval-decisions", headers={"Authorization": OPERATOR_TOKEN})
    assert resp_api.status_code == 503
    payload_api = resp_api.json()
    assert payload_api["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert payload_api["error"]["retryable"] is True


def test_command_executor_unconfigured_governance_owner_returns_503() -> None:
    """Direct execution in command_executor returns FAILED with 503 DEPENDENCY_UNAVAILABLE retryable."""
    status, result, error = execute_command_with_status(
        "cmd-test-rollback-1",
        CommandType.HARD_ROLLBACK,
        {
            "target_id": "dep-001",
            "runtime_id": "rt-001",
            "reason": "Emergency rollback test",
        },
        auth_token=OPERATOR_TOKEN,
    )
    assert status == CommandStatus.FAILED
    assert result is None
    assert error is not None
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["downstream_status"] == 503
    assert error["retryable"] is True


def test_configured_governance_owner_answers_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Governance owner is configured and healthy, approval list and command route succeed."""
    pending_item = {
        "decision_id": "appr-pending-001",
        "decision": None,
        "decision_state": "proposed",
        "command": "ApproveDeployment",
        "target_type": "DeploymentPlan",
        "target_id": "dp-001",
    }
    approved_item = {
        "decision_id": "appr-dp-001",
        "decision": "approved",
        "decision_state": "approved",
        "command": "ApproveDeployment",
        "target_type": "DeploymentPlan",
        "target_id": "dp-001",
    }
    deployment_plan = {
        "id": "dp-001",
        "plan_id": "dp-001",
        "stage": "paper",
        "artifact_id": "artifact-dp-001",
    }

    class _MockGovernanceOwner(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            clean_path = self.path.split("?", 1)[0]
            if "deployment/plans" in clean_path:
                body = json.dumps([deployment_plan]).encode("utf-8")
            else:
                body = json.dumps([pending_item, approved_item]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), _MockGovernanceOwner)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    owner_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", owner_url)

    try:
        read_store = create_read_surface_ports()
        app = _build_test_app(read_store)
        client = TestClient(app, raise_server_exceptions=False)

        # 1. Approval list returns records normally
        resp_bff = client.get("/bff/approvals", headers={"Authorization": OPERATOR_TOKEN})
        assert resp_bff.status_code == 200
        data_bff = resp_bff.json()
        assert data_bff.get("count") == 1
        assert len(data_bff.get("items", [])) == 1

        # 2. Command route carrying approvalId successfully admits with 202
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": APPROVER_TOKEN,
                "Idempotency-Key": "test-configured-cmd-1",
            },
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-001"},
                "action": "approve",
                "params": {
                    "deployment_plan_id": "dp-001",
                    "approval_decision": "approve",
                    "approvalId": "appr-dp-001",
                },
                "audit_context": {"reason": "Test configured owner"},
            },
        )
        assert response.status_code == 202
        data = response.json()["data"]
        assert "receipt" in data
        assert data["receipt"]["command_id"]
    finally:
        server.shutdown()
        server.server_close()
