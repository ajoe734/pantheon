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
    """Mock read store for testing fixtures and owner-backed catalog mutations."""

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

    def dataset_source(self, dataset: str) -> str:
        return "service_store" if dataset in self._data else "missing"

    def supports_catalog_metadata_mutation(self, operation: str) -> bool:
        return operation in {"create_tool", "update_tool", "create_skill", "update_skill"}

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._data["tools"])

    def list_skills(self) -> List[Dict[str, Any]]:
        return list(self._data["skills"])

    def create_tool(
        self,
        *,
        payload: Dict[str, Any],
        operator_id: str,
        tenant_id: str,
        idempotency_key: str,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        tool_id = f"tool-owner-{len(self._data['tools']) + 1}"
        record = {
            "id": tool_id,
            "tool_id": tool_id,
            "name": payload["name"],
            "status": "draft",
            "tool_class": payload.get("tool_class") or "generic",
            "description": payload.get("description") or "",
            "input_schema": payload.get("input_schema") or {},
            "output_schema": payload.get("output_schema") or {},
            "mcp_sourced": False,
            "created_at": snapshot_at,
            "updated_at": snapshot_at,
            "created_by": operator_id,
            "tenant_id": tenant_id,
        }
        self._data["tools"].append(record)
        return dict(record)

    def update_tool(
        self,
        *,
        tool_id: str,
        payload: Dict[str, Any],
        operator_id: str,
        tenant_id: str,
        idempotency_key: str,
        snapshot_at: str,
    ) -> Optional[Dict[str, Any]]:
        for record in self._data["tools"]:
            if record.get("tool_id") == tool_id or record.get("id") == tool_id:
                record.update(payload)
                record["updated_at"] = snapshot_at
                record["updated_by"] = operator_id
                return dict(record)
        return None

    def create_skill(
        self,
        *,
        payload: Dict[str, Any],
        operator_id: str,
        tenant_id: str,
        idempotency_key: str,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        skill_id = f"skill-owner-{len(self._data['skills']) + 1}"
        record = {
            "id": skill_id,
            "skill_id": skill_id,
            "name": payload["name"],
            "status": "draft",
            "description": payload.get("description") or "",
            "sandbox_enabled": bool(payload.get("sandbox_enabled", True)),
            "input_schema": payload.get("input_schema") or {},
            "output_schema": payload.get("output_schema") or {},
            "created_at": snapshot_at,
            "updated_at": snapshot_at,
            "created_by": operator_id,
            "tenant_id": tenant_id,
        }
        self._data["skills"].append(record)
        return dict(record)

    def update_skill(
        self,
        *,
        skill_id: str,
        payload: Dict[str, Any],
        operator_id: str,
        tenant_id: str,
        idempotency_key: str,
        snapshot_at: str,
    ) -> Optional[Dict[str, Any]]:
        for record in self._data["skills"]:
            if record.get("skill_id") == skill_id or record.get("id") == skill_id:
                record.update(payload)
                record["updated_at"] = snapshot_at
                record["updated_by"] = operator_id
                return dict(record)
        return None

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
        def __init__(self, op_id: str, roles: List[str], tenant_id: Optional[str] = None) -> None:
            self.operator_id = op_id
            self.id = op_id
            self.roles = roles
            self.tenant_id = tenant_id
            self.claims = {"tenant_id": tenant_id} if tenant_id else {}

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
        parts = token.split(":", 2)
        tid = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        return Identity(parts[0], [r.strip() for r in parts[1].split(",")], tid)
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
    from openclaw_ops_client import OpenClawOpsClient

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


def test_sanitized_counterexample_unconfigured_owner_fails_closed_and_viewer_denied():
    submissions: List[Any] = []
    unconfigured_service = IntegrationsService(
        read_store=None,
        submit_command_fn=lambda *args, **kwargs: submissions.append((args, kwargs)),
    )
    app = FastAPI()
    app.include_router(create_integrations_router(service=unconfigured_service))
    unconfigured_client = TestClient(app)

    # Exact sanitized counterexample: viewer on POST /bff/tools is rejected with 403 FORBIDDEN
    viewer_create = unconfigured_client.post(
        "/bff/tools",
        json={"name": "t1"},
        headers={"Authorization": "Bearer viewer:viewer", "Idempotency-Key": "k1"},
    )
    assert viewer_create.status_code == 403
    assert viewer_create.json()["detail"]["error"]["code"] == "FORBIDDEN"

    # Viewer on PATCH /bff/tools/{id} and PATCH /bff/skills/{id} is also rejected with 403
    viewer_patch_tool = unconfigured_client.patch(
        "/bff/tools/tool-1",
        json={"description": "viewer edit"},
        headers={"Authorization": "Bearer viewer:viewer", "Idempotency-Key": "k1-patch"},
    )
    assert viewer_patch_tool.status_code == 403
    viewer_patch_skill = unconfigured_client.patch(
        "/bff/skills/skill-1",
        json={"description": "viewer edit"},
        headers={"Authorization": "Bearer viewer:viewer", "Idempotency-Key": "k1-sk-patch"},
    )
    assert viewer_patch_skill.status_code == 403

    # Operator on POST/PATCH /bff/tools and /bff/skills without an owner fails closed with 503
    for method, path, payload, key, expected_precondition in [
        ("POST", "/bff/tools", {"name": "t1"}, "k-op-tool-create", "tool_catalog_owner_unavailable"),
        ("PATCH", "/bff/tools/tool-1", {"description": "d1"}, "k-op-tool-patch", "tool_catalog_owner_unavailable"),
        ("POST", "/bff/skills", {"name": "s1"}, "k-op-skill-create", "skill_catalog_owner_unavailable"),
        ("PATCH", "/bff/skills/skill-1", {"description": "d1"}, "k-op-skill-patch", "skill_catalog_owner_unavailable"),
    ]:
        resp = unconfigured_client.request(
            method,
            path,
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
        )
        assert resp.status_code == 503, f"{method} {path} returned {resp.status_code}: {resp.text}"
        err = resp.json()["detail"]["error"]
        assert err["code"] == "DEPENDENCY_UNAVAILABLE"
        assert err["precondition_failed"] == expected_precondition

    # Unconfigured GET /bff/tools and GET /bff/skills report unavailable/missing and never return memory-only writes
    tools_list = unconfigured_client.get("/bff/tools", headers=OPERATOR_HEADERS)
    assert tools_list.status_code == 200
    assert tools_list.json()["data"] == []
    assert tools_list.json()["meta"]["surfaces"]["tool_list"]["status"] == "unavailable"
    assert tools_list.json()["meta"]["surfaces"]["tool_list"]["source"] == "missing"

    skills_list = unconfigured_client.get("/bff/skills", headers=OPERATOR_HEADERS)
    assert skills_list.status_code == 200
    assert skills_list.json()["data"] == []
    assert skills_list.json()["meta"]["surfaces"]["skill_list"]["status"] == "unavailable"
    assert skills_list.json()["meta"]["surfaces"]["skill_list"]["source"] == "missing"

    assert len(submissions) == 0
    assert unconfigured_service.tool_registry == {}
    assert unconfigured_service.skill_registry == {}


def test_owner_backed_catalog_writes_and_fresh_instance_readback():
    shared_store = FakeReadStore()
    service_writer = IntegrationsService(read_store=shared_store)
    writer_app = FastAPI()
    writer_app.include_router(create_integrations_router(service=service_writer))
    writer_client = TestClient(writer_app)

    tool_resp = writer_client.post(
        "/bff/tools",
        json={"name": "Owner Durable Tool", "tool_class": "research"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-owner-tool-1"},
    )
    assert tool_resp.status_code == 201
    tool_id = tool_resp.json()["tool_id"]

    patch_tool_resp = writer_client.patch(
        f"/bff/tools/{tool_id}",
        json={"description": "Confirmed in owner store", "status": "active"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-owner-tool-patch-1"},
    )
    assert patch_tool_resp.status_code == 200
    assert patch_tool_resp.json()["description"] == "Confirmed in owner store"

    skill_resp = writer_client.post(
        "/bff/skills",
        json={"name": "Owner Durable Skill", "description": "Initial"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-owner-skill-1"},
    )
    assert skill_resp.status_code == 201
    skill_id = skill_resp.json()["skill_id"]

    patch_skill_resp = writer_client.patch(
        f"/bff/skills/{skill_id}",
        json={"description": "Updated in owner store", "status": "active"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-owner-skill-patch-1"},
    )
    assert patch_skill_resp.status_code == 200
    assert patch_skill_resp.json()["description"] == "Updated in owner store"

    # BFF process-local registries were NOT used to manufacture write acknowledgement
    assert service_writer.tool_registry == {}
    assert service_writer.skill_registry == {}

    # Fresh IntegrationsService instance (new process / zero local memory) reads back both records
    fresh_service = IntegrationsService(read_store=shared_store)
    reader_app = FastAPI()
    reader_app.include_router(create_integrations_router(service=fresh_service))
    reader_client = TestClient(reader_app)

    fresh_tool = reader_client.get(f"/bff/tools/{tool_id}", headers=VIEWER_HEADERS)
    assert fresh_tool.status_code == 200
    assert fresh_tool.json()["name"] == "Owner Durable Tool"
    assert fresh_tool.json()["description"] == "Confirmed in owner store"

    fresh_skill = reader_client.get(f"/bff/skills/{skill_id}", headers=VIEWER_HEADERS)
    assert fresh_skill.status_code == 200
    assert fresh_skill.json()["name"] == "Owner Durable Skill"
    assert fresh_skill.json()["description"] == "Updated in owner store"

    # Missing target IDs on PATCH return 404 from owner
    missing_tool = writer_client.patch(
        "/bff/tools/non-existent-tool",
        json={"description": "x"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-missing-tool"},
    )
    assert missing_tool.status_code == 404
    missing_skill = writer_client.patch(
        "/bff/skills/non-existent-skill",
        json={"description": "x"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-missing-skill"},
    )
    assert missing_skill.status_code == 404


def test_unconfirmed_owner_readback_fails_closed_503():
    class UnconfirmedOwnerStore(FakeReadStore):
        def create_tool(self, **kwargs: Any) -> Dict[str, Any]:
            return {"id": "unpersisted-tool", "tool_id": "unpersisted-tool", "name": "Ghost"}

        def create_skill(self, **kwargs: Any) -> Dict[str, Any]:
            return {"id": "unpersisted-skill", "skill_id": "unpersisted-skill", "name": "Ghost"}

        def update_tool(self, **kwargs: Any) -> Dict[str, Any]:
            return {"id": "tool-fixture-1", "tool_id": "tool-fixture-1"}

    app = FastAPI()
    app.include_router(
        create_integrations_router(service=IntegrationsService(read_store=UnconfirmedOwnerStore()))
    )
    unconfirmed_client = TestClient(app)

    t_resp = unconfirmed_client.post(
        "/bff/tools",
        json={"name": "Ghost"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-ghost-t"},
    )
    assert t_resp.status_code == 503
    assert t_resp.json()["detail"]["error"]["precondition_failed"] == "tool_readback_unconfirmed"

    tp_resp = unconfirmed_client.patch(
        "/bff/tools/tool-fixture-1",
        json={"description": "Never persisted"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-ghost-tp"},
    )
    assert tp_resp.status_code == 503
    assert tp_resp.json()["detail"]["error"]["precondition_failed"] == "tool_readback_unconfirmed"

    s_resp = unconfirmed_client.post(
        "/bff/skills",
        json={"name": "Ghost Skill"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-ghost-s"},
    )
    assert s_resp.status_code == 503
    assert s_resp.json()["detail"]["error"]["precondition_failed"] == "skill_readback_unconfirmed"


def test_tools_and_skills_idempotency_scoped_by_tenant_and_operator(client: TestClient):
    op_a_tenant_1 = {"Authorization": "Bearer op-a:operator:tenant-1", "Idempotency-Key": "shared-idem-key"}
    op_b_tenant_1 = {"Authorization": "Bearer op-b:operator:tenant-1", "Idempotency-Key": "shared-idem-key"}
    op_a_tenant_2 = {"Authorization": "Bearer op-a:operator:tenant-2", "Idempotency-Key": "shared-idem-key"}
    viewer_tenant_1 = {"Authorization": "Bearer viewer-a:viewer:tenant-1", "Idempotency-Key": "shared-idem-key"}

    r1 = client.post("/bff/tools", json={"name": "Tenant 1 Op A Tool"}, headers=op_a_tenant_1)
    assert r1.status_code == 201
    tool_a1_id = r1.json()["tool_id"]

    # Viewer cannot replay Operator A's idempotency key
    r_viewer = client.post("/bff/tools", json={"name": "Tenant 1 Op A Tool"}, headers=viewer_tenant_1)
    assert r_viewer.status_code == 403

    # Different operator in same tenant with same Idempotency-Key gets an isolated execution, not a borrowed replay or 409
    r2 = client.post("/bff/tools", json={"name": "Tenant 1 Op B Tool"}, headers=op_b_tenant_1)
    assert r2.status_code == 201
    assert r2.json()["tool_id"] != tool_a1_id
    assert r2.json()["name"] == "Tenant 1 Op B Tool"

    # Same operator in different tenant with same Idempotency-Key is isolated by tenant_id
    r3 = client.post("/bff/tools", json={"name": "Tenant 2 Op A Tool"}, headers=op_a_tenant_2)
    assert r3.status_code == 201
    assert r3.json()["tool_id"] != tool_a1_id

    # Same operator + same tenant + same key + same payload replays the confirmed record
    r1_replay = client.post("/bff/tools", json={"name": "Tenant 1 Op A Tool"}, headers=op_a_tenant_1)
    assert r1_replay.status_code == 201
    assert r1_replay.json()["tool_id"] == tool_a1_id

    # Same operator + same tenant + same key + different payload raises 409 IDEMPOTENCY_CONFLICT
    r1_conflict = client.post("/bff/tools", json={"name": "Mutated Payload"}, headers=op_a_tenant_1)
    assert r1_conflict.status_code == 409
    assert r1_conflict.json()["detail"]["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # Mismatched X-Tenant-Id header against token tenant claim is rejected with 403 FORBIDDEN
    r_mismatch = client.post(
        "/bff/tools",
        json={"name": "Cross Tenant Attempt"},
        headers={**op_a_tenant_1, "X-Tenant-Id": "tenant-2", "Idempotency-Key": "cross-tenant-key"},
    )
    assert r_mismatch.status_code == 403
    assert r_mismatch.json()["detail"]["error"]["precondition_failed"] == "tenant_scope"


