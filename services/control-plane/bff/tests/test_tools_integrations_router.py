"""Characterization and contract tests for the extracted Tools & Integrations domain router.

Part of OPGAP-BE-TOOLS-INTEGRATIONS-V2-20260830.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BFF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from services.control_plane.bff.tools_integrations.router import create_integrations_router, router
from services.control_plane.bff.tools_integrations.service import IntegrationsService, default_bff_error

TASK_REVIEW_MANIFEST = {
    "task_id": "OPGAP-BE-TOOLS-INTEGRATIONS-V2-20260830",
    "owned_layer": "Tools, MCP servers, Skills, OpenClaw, and Channels domain router and service",
    "not_changing": "main.py composition root until later BFF assembly slice",
    "review_scope": {
        "route_count": 35,
        "durable_readback": "GET /bff/tools, GET /bff/skills, GET /bff/mcp-servers, GET /bff/mcp-tools, OpenClaw ops projections",
        "write_boundary": "MCP tool import, MCP tool action admission, Tools/Skills CRUD, OpenClaw session lifecycle",
    },
    "verification": [
        "pytest -q services/control-plane/bff/tests/test_tools_integrations_router.py",
        "python3 -m py_compile services/control-plane/bff/tools_integrations/service.py services/control-plane/bff/tools_integrations/router.py",
    ],
}

ADMIN_HEADERS = {"Authorization": "Bearer admin-user:admin,operator,viewer"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-user:operator,viewer"}
VIEWER_HEADERS = {"Authorization": "Bearer viewer-user:viewer"}


class FakeOpenClawClient:
    """Mock client for OpenClaw gateway adapter."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.canceled_sessions: List[str] = []

    def get_live_gate_status(self) -> Dict[str, Any]:
        return {
            "harness_enabled": True,
            "gate_checks": ["paper_drift", "risk_limits", "operator_concurrence"],
        }

    def list_live_gate_audit(
        self,
        operator_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return [
            {
                "id": "audit-gate-1",
                "operator_id": operator_id or "op-user",
                "capital_pool_id": capital_pool_id or "pool-1",
                "gate_outcome": "passed",
                "evaluated_at": "2026-08-30T12:00:00Z",
            }
        ]

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: str,
        context_bundle: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = f"openclaw-sess-{idempotency_key[:8]}"
        session = {
            "session_id": session_id,
            "agent_id": agent_id,
            "session_type": session_type,
            "operator_id": operator_id,
            "context_bundle": context_bundle or {},
            "status": "created",
        }
        self.sessions[session_id] = session
        return {"session": session, "status": "created", "replayed": False}

    def cancel_session(
        self,
        *,
        session_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        self.canceled_sessions.append(session_id)
        return {
            "session_id": session_id,
            "operator_id": operator_id,
            "status": "canceled",
        }


class FakeReadStore:
    """Mock read store for testing fixtures and projections."""

    def __init__(self) -> None:
        self._data = {
            "tools": [
                {"id": "tool-fixture-1", "tool_id": "tool-fixture-1", "name": "Fixture Tool 1", "status": "active", "tool_class": "generic"}
            ],
            "skills": [
                {"id": "skill-fixture-1", "skill_id": "skill-fixture-1", "name": "Fixture Skill 1", "status": "active"}
            ],
            "mcp_servers": [
                {"id": "mcp-srv-fixture-1", "server_id": "mcp-srv-fixture-1", "name": "Fixture Server 1", "status": "registered"}
            ],
            "mcp_tools": [
                {"id": "mcp-tool-fixture-1", "tool_id": "mcp-tool-fixture-1", "server_id": "mcp-srv-fixture-1", "name": "Fixture MCP Tool 1", "status": "imported", "tool_class": "research"}
            ],
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._data["tools"])

    def list_skills(self) -> List[Dict[str, Any]]:
        return list(self._data["skills"])

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        return list(self._data["mcp_servers"])

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        return list(self._data["mcp_tools"])

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]:
        return {
            "capabilities": {
                "sandbox": {"status": "available", "gate_reason": "sandbox_mode_enabled"},
                "paper": {"status": "available", "gate_reason": "paper_trading_permitted"},
                "canary": {"status": "gated", "gate_reason": "canary_gate_not_evaluated"},
                "live": {"status": "disabled", "gate_reason": "fail_closed_live_gate"},
            },
            "overall_status": "ok",
        }


@pytest.fixture
def fake_openclaw() -> FakeOpenClawClient:
    return FakeOpenClawClient()


@pytest.fixture
def fake_read_store() -> FakeReadStore:
    return FakeReadStore()


@pytest.fixture
def test_app(fake_openclaw: FakeOpenClawClient, fake_read_store: FakeReadStore) -> FastAPI:
    app = FastAPI(title="Tools & Integrations Test App")
    custom_router = create_integrations_router(
        get_read_store=lambda: fake_read_store,
        openclaw_client=fake_openclaw,
        extract_identity=lambda auth: _extract_identity(auth),
    )
    app.include_router(custom_router)
    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _extract_identity(authorization: Optional[str]) -> Any:
    class Identity:
        def __init__(self, op_id: str, roles: List[str]) -> None:
            self.operator_id = op_id
            self.id = op_id
            self.roles = roles

    if not authorization or not authorization.startswith("Bearer "):
        raise default_bff_error(
            status_code=401,
            code="AUTH_REQUIRED",
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
        )
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise default_bff_error(
            status_code=401,
            code="AUTH_REQUIRED",
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
        )
    if ":" in token:
        parts = token.split(":", 1)
        return Identity(parts[0], [r.strip() for r in parts[1].split(",")])
    return Identity(token, ["operator", "viewer", "admin"])


def test_zero_reverse_imports():
    """Verify that integrations module does NOT import main.py or bff_main."""
    integrations_dir = os.path.join(BFF_DIR, "integrations")
    for root, _, files in os.walk(integrations_dir):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    assert "import main" not in line and "from main" not in line, (
                        f"Forbidden reverse import in {path}:{line_idx}: {line}"
                    )
                    assert "import bff_main" not in line and "from bff_main" not in line, (
                        f"Forbidden reverse import in {path}:{line_idx}: {line}"
                    )


def test_route_inventory_count():
    """Verify that the domain router registers exactly 35 routes/decorators."""
    assert len(router.routes) == 35


# --------------------------------------------------------------------------- #
# Group 1 Tests: OpenClaw Routes
# --------------------------------------------------------------------------- #

def test_openclaw_ops_endpoints(client: TestClient):
    # 1. GET /api/v1/operator/openclaw/ops
    resp = client.get("/api/v1/operator/openclaw/ops", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "meta" in data
    assert "openclaw_ops" in data["meta"]["surfaces"]

    # 2. GET /api/v1/operator/openclaw/tool-workflow-bridge
    resp2 = client.get(
        "/api/v1/operator/openclaw/tool-workflow-bridge", headers=OPERATOR_HEADERS
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert "openclaw_tool_workflow_bridge" in data2["meta"]["surfaces"]


def test_openclaw_session_create_and_cancel(client: TestClient):
    headers = {**OPERATOR_HEADERS, "X-Idempotency-Key": "idem-openclaw-001"}
    create_payload = {
        "agent_id": "assistant-trader",
        "session_type": "agora_servant",
        "context_bundle": {"source": "test"},
    }
    resp = client.post(
        "/api/v1/operator/openclaw/sessions", json=create_payload, headers=headers
    )
    assert resp.status_code in (200, 202)
    res_data = resp.json()
    assert res_data["data"]["command"] == "OpenClawCreateSession"
    assert res_data["data"]["status"] == "accepted"
    session_id = res_data["data"]["session"]["session_id"]
    assert res_data["data"]["session"]["session_type"] == "agora_servant"

    # Cancel session
    cancel_headers = {**OPERATOR_HEADERS, "X-Idempotency-Key": "idem-openclaw-cancel-001"}
    cancel_resp = client.post(
        f"/api/v1/operator/openclaw/sessions/{session_id}/cancel",
        headers=cancel_headers,
    )
    assert cancel_resp.status_code == 202
    cancel_data = cancel_resp.json()
    assert cancel_data["data"]["command"] == "OpenClawCancelSession"


def test_openclaw_live_gate_and_broker_readiness(client: TestClient):
    # Live gate status
    resp = client.get(
        "/api/v1/operator/openclaw/live-gate/status", headers=OPERATOR_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["surface"] == "openclaw_live_gate_status"

    # Live gate audit
    resp_audit = client.get(
        "/api/v1/operator/openclaw/live-gate/audit", headers=ADMIN_HEADERS
    )
    assert resp_audit.status_code == 200
    assert resp_audit.json()["surface"] == "openclaw_live_gate_audit"

    # Broker adapter readiness (both legacy and modern operation endpoints)
    resp_legacy = client.get(
        "/api/v1/operator/openclaw/broker-adapter-readiness", headers=OPERATOR_HEADERS
    )
    assert resp_legacy.status_code == 200
    assert "capabilities" in resp_legacy.json()["data"]

    resp_modern = client.get(
        "/api/v1/operator/openclaw/broker/adapter-readiness", headers=OPERATOR_HEADERS
    )
    assert resp_modern.status_code == 200
    assert "capabilities" in resp_modern.json()["data"]


# --------------------------------------------------------------------------- #
# Group 2 Tests: MCP Server Tool Import & Action Admission
# --------------------------------------------------------------------------- #

def test_mcp_server_import_tools_lifecycle(client: TestClient):
    headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-import-100"}
    import_payload = {
        "serverName": "financial-data-server",
        "serverVersion": "1.0.0",
        "governance": {"approvedFlags": ["allow_standalone_create"]},
        "tools": [
            {
                "toolId": "tool-market-quote",
                "name": "Market Quote Tool",
                "description": "Fetch real-time quotes",
                "toolClass": "research",
                "inputSchema": {"symbol": "string"},
                "actions": [
                    {
                        "actionId": "fetch_quote",
                        "actionType": "invoke",
                        "allowStandaloneCreate": True,
                        "governanceFlag": "allow_standalone_create",
                    }
                ],
            },
            {
                "toolId": "tool-direct-lean",
                "name": "LEAN Direct Tool",
                "description": "Direct execution tool",
                "toolClass": "lean_direct",
                "actions": [
                    {
                        "actionId": "exec_signal",
                        "actionType": "invoke",
                    }
                ],
            },
        ],
    }

    # Import tools under server-1
    resp = client.post(
        "/bff/v1/mcp/servers/server-1/import-tools",
        json=import_payload,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("completed", "COMPLETED")
    assert len(data["data"]["importedTools"]) == 2

    # Test idempotency replay
    replay_resp = client.post(
        "/bff/v1/mcp/servers/server-1/import-tools",
        json=import_payload,
        headers=headers,
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["data"]["replayed"] is True

    # Test rejection when body has idempotencyKey
    bad_payload = dict(import_payload)
    bad_payload["idempotencyKey"] = "should-be-rejected"
    bad_resp = client.post(
        "/bff/v1/mcp/servers/server-1/import-tools",
        json=bad_payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-import-fail"},
    )
    assert bad_resp.status_code == 400


def test_mcp_tool_action_admission(client: TestClient):
    # First, import a tool
    headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-import-200"}
    import_payload = {
        "serverName": "order-management",
        "governance": {},
        "tools": [
            {
                "toolId": "tool-order-book",
                "name": "Order Book Tool",
                "toolClass": "status",
                "actions": [{"actionId": "read_book"}],
            },
            {
                "toolId": "tool-lean-live-test",
                "name": "Lean Live Tool",
                "toolClass": "lean_direct",
                "actions": [{"actionId": "lean_action"}],
            },
        ],
    }
    client.post(
        "/bff/v1/mcp/servers/server-2/import-tools",
        json=import_payload,
        headers=headers,
    )

    # 1. Admit action: GRANT
    action_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-action-grant-01"}
    action_payload = {
        "reason": "Granting order book read capability to operator",
        "scope": {"executionContext": "paper"},
        "dryRun": False,
    }
    resp = client.post(
        "/bff/v1/mcp/servers/server-2/tools/tool-order-book/actions/grant",
        json=action_payload,
        headers=action_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["status"] == "granted"
    assert data["data"]["admitted"] is True

    # 2. Test alias route: /bff/mcp-tools/{tool_id}/{action}
    alias_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-action-test-01"}
    alias_payload = {
        "reason": "Testing tool connectivity",
        "dryRun": False,
    }
    alias_resp = client.post(
        "/bff/mcp-tools/tool-order-book/test",
        json=alias_payload,
        headers=alias_headers,
    )
    assert alias_resp.status_code == 200
    assert alias_resp.json()["data"]["status"] == "tested"

    # 3. Test lean_direct live execution context block rule
    live_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-lean-live-01"}
    live_payload = {
        "reason": "Trying to grant live execution on lean_direct",
        "scope": {"executionContext": "live"},
    }
    live_resp = client.post(
        "/bff/v1/mcp/servers/server-2/tools/tool-lean-live-test/actions/grant",
        json=live_payload,
        headers=live_headers,
    )
    assert live_resp.status_code == 409
    assert live_resp.json()["detail"]["error"]["code"] == "PRECONDITION_FAILED"


# --------------------------------------------------------------------------- #
# Group 3 Tests: Tools, MCP Servers, Skills compatibility routes
# --------------------------------------------------------------------------- #

def test_tools_crud_and_deprecated_actions(client: TestClient):
    # List tools
    resp = client.get("/bff/tools", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    assert "data" in resp.json()

    # Create generic tool
    create_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-tool-create-1"}
    create_payload = {
        "name": "Custom Analytical Tool",
        "description": "Calculates custom risk metrics",
        "tool_class": "research",
    }
    c_resp = client.post("/bff/tools", json=create_payload, headers=create_headers)
    assert c_resp.status_code == 201
    tool_id = c_resp.json()["id"]

    # Get tool
    g_resp = client.get(f"/bff/tools/{tool_id}", headers=OPERATOR_HEADERS)
    assert g_resp.status_code == 200
    assert g_resp.json()["name"] == "Custom Analytical Tool"

    # Patch tool
    p_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-tool-patch-1"}
    p_resp = client.patch(
        f"/bff/tools/{tool_id}",
        json={"description": "Updated description"},
        headers=p_headers,
    )
    assert p_resp.status_code == 200
    assert p_resp.json()["description"] == "Updated description"

    # Deprecated tool action route (should return 410)
    act_resp = client.post(
        f"/bff/tools/{tool_id}/actions/restart",
        json={},
        headers=OPERATOR_HEADERS,
    )
    assert act_resp.status_code == 410
    assert act_resp.headers.get("X-Deprecated") == "true"


def test_mcp_servers_and_skills_crud(client: TestClient):
    # Create MCP Server
    srv_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-srv-create-1"}
    srv_payload = {
        "name": "Pricing Feeds Server",
        "endpoint": "http://pricing-service:8080",
    }
    srv_resp = client.post("/bff/mcp/servers", json=srv_payload, headers=srv_headers)
    assert srv_resp.status_code == 201
    server_id = srv_resp.json()["id"]

    # List MCP server tools
    tools_resp = client.get(
        f"/bff/mcp/servers/{server_id}/tools", headers=OPERATOR_HEADERS
    )
    assert tools_resp.status_code == 200

    # Deprecated /bff/mcp/servers route
    dep_resp = client.get("/bff/mcp/servers", headers=OPERATOR_HEADERS)
    assert dep_resp.status_code == 410

    # Skills: List
    sk_list = client.get("/bff/skills", headers=OPERATOR_HEADERS)
    assert sk_list.status_code == 200

    # Skills: Create
    sk_headers = {**OPERATOR_HEADERS, "Idempotency-Key": "idem-skill-create-1"}
    sk_payload = {
        "name": "Sentiment Parser Skill",
        "description": "Parses macro sentiment from news",
    }
    sk_resp = client.post("/bff/skills", json=sk_payload, headers=sk_headers)
    assert sk_resp.status_code == 201
    skill_id = sk_resp.json()["id"]

    # Skills: Get & Patch
    sk_get = client.get(f"/bff/skills/{skill_id}", headers=OPERATOR_HEADERS)
    assert sk_get.status_code == 200

    sk_patch = client.patch(
        f"/bff/skills/{skill_id}",
        json={"description": "Refined parser"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-skill-patch-1"},
    )
    assert sk_patch.status_code == 200
    assert sk_patch.json()["description"] == "Refined parser"

    # Skills: Sandbox Eval
    eval_resp = client.post(
        f"/bff/skills/{skill_id}/sandbox-eval",
        json={"inputs": {"text": "Bullish trend expected"}},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-skill-eval-1"},
    )
    assert eval_resp.status_code == 202
    eval_data = eval_resp.json()
    assert eval_data["status"] == "queued"
    assert eval_data["sandbox_mode"] is True
    assert eval_data["inputs"] == {"text": "Bullish trend expected"}
    assert eval_data["skill_id"] == skill_id
    assert "job_id" in eval_data
    assert "audit" in eval_data
    assert "meta" in eval_data


# --------------------------------------------------------------------------- #
# Group 4 Tests: Capabilities Facades and SSE Channels
# --------------------------------------------------------------------------- #

def test_capabilities_facades_and_channels(client: TestClient):
    # MCP Servers facade
    resp_srvs = client.get("/bff/mcp-servers", headers=OPERATOR_HEADERS)
    assert resp_srvs.status_code == 200
    assert "data" in resp_srvs.json()

    # MCP Tools facade
    resp_tools = client.get("/bff/mcp-tools", headers=OPERATOR_HEADERS)
    assert resp_tools.status_code == 200
    assert "data" in resp_tools.json()

    # Channels facade
    resp_channels = client.get("/bff/channels", headers=OPERATOR_HEADERS)
    assert resp_channels.status_code == 200
    data = resp_channels.json()["data"]
    channel_ids = [c["id"] for c in data]
    assert "approval" in channel_ids
    assert "tool" in channel_ids
    assert "mcp" in channel_ids

    # Channel detail
    resp_chan = client.get("/bff/channels/approval", headers=OPERATOR_HEADERS)
    assert resp_chan.status_code == 200
    assert resp_chan.json()["data"]["id"] == "approval"


# --------------------------------------------------------------------------- #
# Group 5 Tests: Error Handling, RBAC, and Edge Cases
# --------------------------------------------------------------------------- #

def test_openclaw_role_rejection(client: TestClient):
    # Non-operator viewer should be rejected with 403 on openclaw commands
    resp = client.post(
        "/api/v1/operator/openclaw/sessions",
        json={"agent_id": "test"},
        headers={**VIEWER_HEADERS, "X-Idempotency-Key": "idem-view-01"},
    )
    assert resp.status_code == 403

    resp_gate = client.get(
        "/api/v1/operator/openclaw/live-gate/status",
        headers=VIEWER_HEADERS,
    )
    assert resp_gate.status_code == 403


def test_openclaw_operator_id_filter_rejection(client: TestClient):
    # Operator filtering for another operator should get 403 if not admin
    resp = client.get(
        "/api/v1/operator/openclaw/ops?operator_id=different-op",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 403


def test_mcp_tool_action_unimported_404(client: TestClient):
    resp = client.post(
        "/bff/mcp-tools/non-existent-tool/grant",
        json={"reason": "Testing 404", "dryRun": False},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-404-01"},
    )
    assert resp.status_code == 404


def test_mcp_server_import_standalone_create_unauthorized_rejected(client: TestClient):
    # Governance flags lack allow_standalone_create
    payload = {
        "serverName": "restricted-server",
        "governance": {"approvedFlags": []},
        "tools": [
            {
                "toolId": "tool-standalone-fail",
                "name": "Standalone Tool",
                "toolClass": "research",
                "actions": [
                    {
                        "actionId": "create_item",
                        "actionType": "create",
                        "allowStandaloneCreate": True,
                    }
                ],
            }
        ],
    }
    resp = client.post(
        "/bff/v1/mcp/servers/server-restr/import-tools",
        json=payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-restr-01"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["rejectedTools"]) == 1
    assert data["data"]["rejectedTools"][0]["preconditionFailed"] == "standalone_tool_create"


def test_skills_dry_run_create(client: TestClient):
    # 1. Header-based dry run with X-Dry-Run: 1
    hdr_payload = {
        "name": "Header Dry Run Skill",
        "description": "Will not be saved via X-Dry-Run",
    }
    resp1 = client.post(
        "/bff/skills",
        json=hdr_payload,
        headers={**OPERATOR_HEADERS, "X-Dry-Run": "1", "Idempotency-Key": "idem-skill-dry-hdr-1"},
    )
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["meta"]["dryRun"] is True
    assert data1["meta"]["durable"] is False
    assert data1["meta"]["liveCapitalSideEffects"] is False
    assert data1["meta"]["evidenceKind"] == "skill.create"
    assert data1["meta"]["idempotency"]["key"] == "idem-skill-dry-hdr-1"
    assert "id" in data1["data"]
    created_id = data1["data"]["id"]

    # Verify non-persistence: GET by ID returns 404
    get_resp = client.get(f"/bff/skills/{created_id}", headers=OPERATOR_HEADERS)
    assert get_resp.status_code == 404

    # Verify list does not contain dry-run skill
    list_resp = client.get("/bff/skills", headers=OPERATOR_HEADERS)
    assert list_resp.status_code == 200
    assert all(s.get("id") != created_id for s in list_resp.json()["data"])

    # 2. Body-based dry run with dry_run: True
    body_payload = {
        "name": "Body Dry Run Skill",
        "description": "Will not be saved via dry_run in body",
        "dry_run": True,
    }
    resp2 = client.post(
        "/bff/skills",
        json=body_payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-skill-dry-body-1"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["meta"]["dryRun"] is True

    # 3. Body-based dry run with dryRun: True
    camel_payload = {
        "name": "Camel Dry Run Skill",
        "description": "Will not be saved via dryRun in body",
        "dryRun": True,
    }
    resp3 = client.post(
        "/bff/skills",
        json=camel_payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-skill-dry-camel-1"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["meta"]["dryRun"] is True

    # 4. Validation failure in dry-run mode still returns 422
    val_resp = client.post(
        "/bff/skills",
        json={"name": "   ", "description": "Empty name"},
        headers={**OPERATOR_HEADERS, "X-Dry-Run": "1", "Idempotency-Key": "idem-skill-dry-val-1"},
    )
    assert val_resp.status_code == 422
    assert val_resp.json()["detail"]["error"]["code"] == "VALIDATION_FAILED"


def test_tools_skills_channels_404(client: TestClient):
    # Tool 404
    resp_tool = client.get("/bff/tools/missing-tool-999", headers=OPERATOR_HEADERS)
    assert resp_tool.status_code == 404

    # Skill 404
    resp_skill = client.get("/bff/skills/missing-skill-999", headers=OPERATOR_HEADERS)
    assert resp_skill.status_code == 404

    # Channel 404
    resp_chan = client.get("/bff/channels/non-existent-channel", headers=OPERATOR_HEADERS)
    assert resp_chan.status_code == 404


def test_openclaw_session_validation_failures(client: TestClient):
    headers = {**OPERATOR_HEADERS, "X-Idempotency-Key": "idem-val-fail-001"}

    # Missing agent_id
    resp1 = client.post(
        "/api/v1/operator/openclaw/sessions",
        json={"session_type": "agora_servant"},
        headers=headers,
    )
    assert resp1.status_code == 422
    assert resp1.json()["detail"]["error"]["code"] == "VALIDATION_FAILED"

    # Missing session_type
    resp2 = client.post(
        "/api/v1/operator/openclaw/sessions",
        json={"agent_id": "assistant-1"},
        headers=headers,
    )
    assert resp2.status_code == 422
    assert resp2.json()["detail"]["error"]["code"] == "VALIDATION_FAILED"

    # Invalid context_bundle (not a dict)
    resp3 = client.post(
        "/api/v1/operator/openclaw/sessions",
        json={
            "agent_id": "assistant-1",
            "session_type": "agora_servant",
            "context_bundle": "invalid_string_not_dict",
        },
        headers=headers,
    )
    assert resp3.status_code == 422
    assert resp3.json()["detail"]["error"]["code"] == "VALIDATION_FAILED"


def test_openclaw_production_client_signature_contract():
    import inspect
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient

    sig_create = inspect.signature(OpenClawOpsClient.create_session)
    create_params = list(sig_create.parameters.keys())
    assert "agent_id" in create_params
    assert "session_type" in create_params
    assert "operator_id" in create_params
    assert "idempotency_key" in create_params
    assert "context_bundle" in create_params

    sig_cancel = inspect.signature(OpenClawOpsClient.cancel_session)
    cancel_params = list(sig_cancel.parameters.keys())
    assert "session_id" in cancel_params
    assert "operator_id" in cancel_params
    assert "idempotency_key" in cancel_params


def test_standalone_exported_router_unauthenticated_and_rbac():
    """Verify that standalone create_integrations_router() and exported router enforce 401 on unauthenticated requests."""
    standalone_app = FastAPI()
    standalone_app.include_router(router)
    standalone_client = TestClient(standalone_app)

    # 1. Unauthenticated requests should receive 401 AUTH_REQUIRED
    unauth_endpoints = [
        ("GET", "/bff/tools"),
        ("GET", "/bff/skills"),
        ("GET", "/bff/mcp-servers"),
        ("GET", "/bff/mcp-tools"),
        ("GET", "/bff/channels"),
        ("GET", "/api/v1/operator/openclaw/ops"),
        ("GET", "/api/v1/operator/openclaw/live-gate/status"),
        ("POST", "/api/v1/operator/openclaw/sessions"),
        ("POST", "/bff/tools"),
        ("POST", "/bff/skills"),
    ]
    for method, path in unauth_endpoints:
        if method == "GET":
            resp = standalone_client.get(path)
        else:
            resp = standalone_client.post(path, json={"name": "test"})
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code} instead of 401"
        data = resp.json()
        assert data["detail"]["error"]["code"] == "AUTH_REQUIRED"

    # 2. Invalid authorization token should receive 401
    inv_resp = standalone_client.get("/bff/tools", headers={"Authorization": "InvalidFormatToken"})
    assert inv_resp.status_code == 401
    assert inv_resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    # 3. Viewer token on OpenClaw session write should receive 403 FORBIDDEN
    viewer_headers = {"Authorization": "Bearer viewer-only:viewer", "X-Idempotency-Key": "key-1"}
    forb_resp = standalone_client.post(
        "/api/v1/operator/openclaw/sessions",
        json={"agent_id": "ag-1", "session_type": "agora_servant"},
        headers=viewer_headers,
    )
    assert forb_resp.status_code == 403
    assert forb_resp.json()["detail"]["error"]["code"] == "FORBIDDEN"

    # 4. Valid viewer token on read route should succeed (200)
    ok_resp = standalone_client.get("/bff/tools", headers=viewer_headers)
    assert ok_resp.status_code == 200


def test_skills_sandbox_eval_contract_and_idempotent_replay(client: TestClient):
    # 1. Create a skill first
    sk_resp = client.post(
        "/bff/skills",
        json={"name": "Eval Target Skill", "description": "Target for eval testing"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-eval-target-create"},
    )
    assert sk_resp.status_code == 201
    skill_id = sk_resp.json()["id"]

    # 2. Execute sandbox eval
    eval_payload = {
        "inputs": {
            "query": "Market regime classification",
            "parameters": {"lookback_days": 30, "threshold": 0.05},
        }
    }
    idem_key = "idem-skill-eval-replay-001"
    headers = {**OPERATOR_HEADERS, "Idempotency-Key": idem_key}

    eval_resp = client.post(
        f"/bff/skills/{skill_id}/sandbox-eval",
        json=eval_payload,
        headers=headers,
    )
    assert eval_resp.status_code == 202
    res = eval_resp.json()

    # Exact baseline contract assertions
    assert res["status"] == "queued"
    assert res["sandbox_mode"] is True
    assert res["skill_id"] == skill_id
    assert res["inputs"] == eval_payload["inputs"]
    assert res["job_id"].startswith(f"sandbox-eval-{skill_id}-")
    assert "submitted_at" in res
    assert res["submitted_by"] == "op-user"

    # Audit record verification
    audit = res["audit"]
    assert audit["operator_id"] == "op-user"
    assert audit["action_id"] == "sandbox-eval"
    assert audit["skill_id"] == skill_id
    assert audit["idempotency_key"] == idem_key
    assert "operator" in audit["roles_at_submission"]

    # Meta estimation verification
    assert res["meta"]["estimated_processing_time_ms"] == 3000
    assert res["meta"]["next_poll_after_ms"] == 500

    # 3. Idempotent replay with same key returns identical cached result
    replay_resp = client.post(
        f"/bff/skills/{skill_id}/sandbox-eval",
        json=eval_payload,
        headers=headers,
    )
    assert replay_resp.status_code == 202
    assert replay_resp.json() == res

    # 4. Validation failure on empty skill_id
    empty_id_resp = client.post(
        "/bff/skills/%20%20/sandbox-eval",
        json=eval_payload,
        headers=headers,
    )
    assert empty_id_resp.status_code == 422


def test_skills_rbac_rules(client: TestClient):
    # 1. Viewer trying to POST /bff/skills receives 403 FORBIDDEN
    resp_create = client.post(
        "/bff/skills",
        json={"name": "Unauthorized Skill"},
        headers={**VIEWER_HEADERS, "Idempotency-Key": "idem-unauth-skill"},
    )
    assert resp_create.status_code == 403
    assert resp_create.json()["detail"]["error"]["code"] == "FORBIDDEN"

    # 2. Viewer trying to POST /bff/skills/{id}/sandbox-eval succeeds (202) because sandbox-eval requires read role
    resp_eval = client.post(
        "/bff/skills/skill-any/sandbox-eval",
        json={"inputs": {"test": True}},
        headers={**VIEWER_HEADERS, "Idempotency-Key": "idem-viewer-eval"},
    )
    assert resp_eval.status_code == 202
    assert resp_eval.json()["status"] == "queued"


def test_dependency_injected_dry_run_resolver_and_context():
    from contextvars import ContextVar

    custom_dry_run_ctx: ContextVar[bool] = ContextVar("custom_dry_run_ctx", default=False)

    def custom_resolver(header_val: Optional[str]) -> bool:
        return header_val == "custom-dry-run-flag"

    custom_router = create_integrations_router(
        dry_run_resolver=custom_resolver,
        dry_run_context=custom_dry_run_ctx,
    )
    custom_app = FastAPI()
    custom_app.include_router(custom_router)
    custom_client = TestClient(custom_app)

    # 1. Custom resolver trigger
    resp1 = custom_client.post(
        "/bff/skills",
        json={"name": "Resolver Skill"},
        headers={**OPERATOR_HEADERS, "X-Dry-Run": "custom-dry-run-flag", "Idempotency-Key": "k1"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["meta"]["dryRun"] is True

    # 2. Custom ContextVar trigger
    token = custom_dry_run_ctx.set(True)
    try:
        resp2 = custom_client.post(
            "/bff/skills",
            json={"name": "ContextVar Skill"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "k2"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["meta"]["dryRun"] is True
    finally:
        custom_dry_run_ctx.reset(token)

