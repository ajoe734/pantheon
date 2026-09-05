from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
SNAPSHOT_DIR = BFF_DIR / "contract_snapshots"

from services.control_plane.bff import main as bff_main
from execute_plans_bff_contract import (  # noqa: E402
    app_route_index,
    entry_key,
    format_coverage_report,
    implemented_entry_is_live,
    load_registry,
    route_key,
)


VALID_STATUSES = {
    "implemented",
    "implemented_by_alias",
    "missing",
    "superseded_with_reason",
    "deferred_with_task",
}
GAP_TASK_IDS = {f"BFF-LUV-GAP-{index:03d}" for index in range(2, 13)}
OPERATOR_TOKEN = "Bearer op-execute-plans:operator"


def _registry() -> dict:
    return load_registry(SNAPSHOT_DIR / "execute_plans_bff_routes.json")


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


def test_execute_plans_registry_entries_are_well_formed() -> None:
    registry = _registry()
    entries = registry["entries"]
    assert len(entries) >= 140

    seen = set()
    for entry in entries:
        assert entry["status"] in VALID_STATUSES
        assert entry.get("family")
        assert entry.get("method")
        assert entry.get("path", "").startswith(("/bff", "/health"))
        key = entry_key(entry)
        assert key not in seen, f"duplicate route registry row: {key}"
        seen.add(key)

        if entry["status"] in {"missing", "deferred_with_task"}:
            assert entry.get("task_id") in GAP_TASK_IDS, entry
        if entry["status"] == "implemented_by_alias":
            assert entry.get("covered_by"), entry
            assert entry.get("proof"), entry
        if entry["status"] == "superseded_with_reason":
            assert entry.get("reason"), entry


def test_execute_plans_implemented_registry_rows_exist_in_fastapi_app() -> None:
    live_routes = app_route_index(bff_main.app)
    gaps = [
        entry
        for entry in _registry()["entries"]
        if entry["status"] in {"implemented", "implemented_by_alias"}
        and not implemented_entry_is_live(entry, live_routes)
    ]
    assert not gaps, gaps


def test_execute_plans_final_contract_routes_remain_registered() -> None:
    live_routes = app_route_index(bff_main.app)
    required = {
        route_key("GET", "/health"),
        route_key("GET", "/bff/actions"),
        route_key("GET", "/bff/approvals"),
        route_key("GET", "/bff/v5/interventions"),
        route_key("POST", "/bff/v5/interventions/{id}/remediate"),
    }
    assert required <= live_routes


def test_execute_plans_route_surface_report_is_renderable() -> None:
    report = format_coverage_report(_registry(), app_route_index(bff_main.app))
    assert "Execute-Plans BFF Route Coverage" in report
    assert "strategy-persona" in report
    assert "BFF-LUV-GAP-002" in report
    assert "sse-compatibility" in report
    assert "Implemented Rows Not Live" not in report


def test_execute_plans_mcp_tool_alias_actions_prove_concrete_verbs() -> None:
    bff_main._MCP_IMPORT_IDEMPOTENCY.clear()
    bff_main._MCP_TOOL_ACTION_IDEMPOTENCY.clear()
    bff_main._MCP_TOOL_REGISTRY.clear()
    client = TestClient(bff_main.app)

    imported = client.post(
        "/bff/mcp-servers/server-alpha/import-tools",
        headers={
            "Authorization": OPERATOR_TOKEN,
            "Idempotency-Key": "execute-plans-mcp-import",
        },
        json=_import_body(),
    )
    assert imported.status_code == 200, imported.text

    expected_status = {
        "grant": "granted",
        "revoke": "revoked",
        "disable": "disabled",
        "test": "tested",
    }
    for action, status in expected_status.items():
        response = client.post(
            f"/bff/mcp-tools/research.alpha/{action}",
            headers={
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": f"execute-plans-mcp-action-{action}",
            },
            json={
                "reason": f"Prove execute-plans alias action {action}",
                "scope": {"executionContext": "research"},
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["action"] == action
        assert data["status"] == status
        assert data["admitted"] is True

    assert bff_main._MCP_TOOL_REGISTRY["server-alpha:research.alpha"]["status"] == "tested"
