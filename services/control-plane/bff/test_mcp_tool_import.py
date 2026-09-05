"""
Tests for BFF-FINAL-006: MCP server tool import and v1 tool action admission.

Acceptance criteria verified:
  1. Server-scoped import-tools endpoint imports tool/action descriptors.
  2. Imports are idempotent: replay succeeds, changed payload conflicts.
  3. Standalone tool create is not exposed and is not implicitly enabled.
  4. v1 tool lifecycle actions are admitted only for imported tools.
  5. live lean_direct grants are denied by the tool permission boundary.
"""
from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main


OPERATOR_TOKEN = "Bearer op-2:operator"
VIEWER_TOKEN = "Bearer viewer-1:viewer"


def _client() -> TestClient:
    bff_main._MCP_IMPORT_IDEMPOTENCY.clear()
    bff_main._MCP_TOOL_ACTION_IDEMPOTENCY.clear()
    bff_main._MCP_TOOL_REGISTRY.clear()
    return TestClient(bff_main.app)


def _import_body(tool_id: str = "research.alpha") -> dict:
    return {
        "serverName": "research-mcp",
        "serverVersion": "1.0.0",
        "schemaUrl": "https://schemas.example.test/mcp/research.json",
        "tools": [
            {
                "toolId": tool_id,
                "name": "Research Alpha",
                "description": "Research-only feature probe",
                "toolClass": "research",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "actions": [
                    {
                        "actionId": "invoke.research.alpha",
                        "actionType": "invoke",
                        "riskLevel": "low",
                    }
                ],
            }
        ],
    }


def test_import_tools_endpoint_imports_server_descriptors() -> None:
    client = _client()

    response = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-001"},
        json=_import_body(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    data = payload["data"]
    assert data["serverId"] == "server-alpha"
    assert data["replayed"] is False
    assert len(data["importedTools"]) == 1
    imported = data["importedTools"][0]
    assert imported["toolId"] == "research.alpha"
    assert imported["actionCount"] == 1
    assert imported["standaloneCreateEnabled"] is False
    assert data["rejectedTools"] == []
    assert "server-alpha:research.alpha" in bff_main._MCP_TOOL_REGISTRY

    alias_response = client.post(
        "/bff/mcp-servers/server-beta/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-001-alias"},
        json=_import_body(tool_id="research.beta"),
    )
    assert alias_response.status_code == 200, alias_response.text
    assert alias_response.json()["data"]["serverId"] == "server-beta"
    assert "server-beta:research.beta" in bff_main._MCP_TOOL_REGISTRY


def test_import_tools_replays_same_idempotency_key_and_conflicts_on_changed_payload() -> None:
    client = _client()
    headers = {"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-replay"}

    first = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers=headers,
        json=_import_body(),
    )
    second = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers=headers,
        json=_import_body(),
    )
    conflict = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers=headers,
        json=_import_body(tool_id="research.beta"),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"]["importId"] == first.json()["data"]["importId"]
    assert second.json()["data"]["replayed"] is True
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_import_and_tool_actions_reject_body_key_and_missing_action_idempotency() -> None:
    client = _client()
    body_with_key = {**_import_body(), "idempotencyKey": "body-key"}
    import_response = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-body-key"},
        json=body_with_key,
    )
    assert import_response.status_code == 400, import_response.text
    assert import_response.json()["error"]["details"]["precondition_failed"] == "body_idempotency_key"

    body_with_unknown_field = {**_import_body(), "standaloneToolCreate": True}
    unknown_field = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-unknown-field"},
        json=body_with_unknown_field,
    )
    assert unknown_field.status_code == 422, unknown_field.text
    assert unknown_field.json()["error"]["details"]["precondition_failed"] == "payload_shape"

    imported = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-action-key-check"},
        json=_import_body(),
    )
    assert imported.status_code == 200, imported.text

    missing_key = client.post(
        "/bff/v1/mcp/servers/server-alpha/tools/research.alpha/actions/test",
        headers={"Authorization": OPERATOR_TOKEN},
        json={"reason": "Probe action admission", "scope": {"executionContext": "research"}},
    )
    assert missing_key.status_code == 400, missing_key.text
    assert missing_key.json()["error"]["details"]["precondition_failed"] == "idempotency_key"


def test_import_rejects_implicit_standalone_create_and_route_is_absent() -> None:
    client = _client()
    body = _import_body()
    body["tools"][0]["actions"][0]["allowStandaloneCreate"] = True

    response = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-standalone"},
        json=body,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["importedTools"] == []
    assert data["rejectedTools"][0]["preconditionFailed"] == "standalone_tool_create"
    assert "server-alpha:research.alpha" not in bff_main._MCP_TOOL_REGISTRY

    standalone_create_routes = [
        route
        for route in bff_main.app.routes
        if getattr(route, "path", "") in {"/bff/v1/mcp/tools", "/bff/v1/tools", "/bff/mcp-tools"}
        and "POST" in getattr(route, "methods", set())
    ]
    assert standalone_create_routes == []


def test_tool_action_admission_requires_imported_tool_and_updates_lifecycle() -> None:
    client = _client()
    imported = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-action"},
        json=_import_body(),
    )
    assert imported.status_code == 200, imported.text

    response = client.post(
        "/bff/v1/mcp/servers/server-alpha/tools/research.alpha/actions/grant",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-action-grant"},
        json={"reason": "Enable research session tool", "scope": {"executionContext": "research"}},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["data"] == {
        "toolId": "research.alpha",
        "serverId": "server-alpha",
        "action": "grant",
        "status": "granted",
        "admitted": True,
        "replayed": False,
    }
    assert bff_main._MCP_TOOL_REGISTRY["server-alpha:research.alpha"]["status"] == "granted"

    alias_response = client.post(
        "/bff/mcp-tools/research.alpha/disable",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-action-disable-alias"},
        json={"reason": "Disable after grant", "scope": {"executionContext": "research"}},
    )
    assert alias_response.status_code == 200, alias_response.text
    assert alias_response.json()["data"]["status"] == "disabled"
    assert bff_main._MCP_TOOL_REGISTRY["server-alpha:research.alpha"]["status"] == "disabled"


def test_tool_action_rejects_missing_tool_viewer_role_and_live_lean_direct_grant() -> None:
    client = _client()

    missing = client.post(
        "/bff/v1/mcp/servers/server-alpha/tools/research.alpha/actions/grant",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-action-missing"},
        json={"reason": "Missing import", "scope": {"executionContext": "research"}},
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    viewer = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": VIEWER_TOKEN, "Idempotency-Key": "mcp-import-viewer"},
        json=_import_body(),
    )
    assert viewer.status_code == 403, viewer.text

    body = _import_body(tool_id="lean.direct")
    body["tools"][0]["toolClass"] = "lean_direct"
    imported = client.post(
        "/bff/v1/mcp/servers/server-alpha/import-tools",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-import-lean"},
        json=body,
    )
    assert imported.status_code == 200, imported.text

    denied = client.post(
        "/bff/v1/mcp/servers/server-alpha/tools/lean.direct/actions/grant",
        headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "mcp-action-live-lean"},
        json={"reason": "Try live grant", "scope": {"executionContext": "live"}},
    )
    assert denied.status_code == 409, denied.text
    detail = denied.json()
    assert detail["error"]["code"] == "PRECONDITION_FAILED"
    assert detail["error"]["details"]["precondition_failed"] == "lean_direct_live"
