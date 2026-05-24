from __future__ import annotations

import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from command_queue import CommandStore  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


FINAL_CONTRACT_METHOD_PATHS = {
    ("DELETE", "/bff/confirm-tokens/{tokenId}"),
    ("GET", "/bff/agora/alerts/triage"),
    ("GET", "/bff/agora/ask/sessions"),
    ("GET", "/bff/agora/evaluation-runs"),
    ("GET", "/bff/agora/evaluation-suites"),
    ("GET", "/bff/agora/inbox"),
    ("GET", "/bff/agora/journal"),
    ("GET", "/bff/agora/persona-lab/runs"),
    ("GET", "/bff/agora/postmortems"),
    ("GET", "/bff/agora/signals"),
    ("GET", "/bff/agora/signals/{id}"),
    ("GET", "/bff/agora/skill-coaching/sessions"),
    ("GET", "/bff/alerts"),
    ("GET", "/bff/alerts/{id}"),
    ("GET", "/bff/approvals"),
    ("GET", "/bff/approvals/{id}"),
    ("GET", "/bff/artifacts"),
    ("GET", "/bff/artifacts/{id}"),
    ("GET", "/bff/audit"),
    ("GET", "/bff/capabilities"),
    ("GET", "/bff/capital-pools"),
    ("GET", "/bff/capital-pools/{id}"),
    ("GET", "/bff/channels"),
    ("GET", "/bff/channels/{id}"),
    ("GET", "/bff/command-confirmations/{token}"),
    ("GET", "/bff/confirm-tokens/{tokenId}"),
    ("GET", "/bff/deployments"),
    ("GET", "/bff/deployments/{id}"),
    ("GET", "/bff/events/stream"),
    ("GET", "/bff/evolution-programs"),
    ("GET", "/bff/evolution-programs/{id}"),
    ("GET", "/bff/feature-flags"),
    ("GET", "/bff/healthz"),
    ("GET", "/bff/incidents"),
    ("GET", "/bff/incidents/{id}"),
    ("GET", "/bff/jobs"),
    ("GET", "/bff/jobs/{id}"),
    ("GET", "/bff/management/cockpit"),
    ("GET", "/bff/management/evidence"),
    ("GET", "/bff/management/evolution-journal"),
    ("GET", "/bff/management/persona-intent"),
    ("GET", "/bff/management/persona-league"),
    ("GET", "/bff/management/persona-league/heatmap"),
    ("GET", "/bff/management/persona-league/rankings"),
    ("GET", "/bff/management/persona-league/tiers"),
    ("GET", "/bff/management/quarterly-ranking"),
    ("GET", "/bff/management/quarterly-ranking/formula"),
    ("GET", "/bff/management/quarterly-ranking/recommendations"),
    ("GET", "/bff/management/performance-attribution"),
    ("GET", "/bff/management/performance-attribution/by-persona"),
    ("GET", "/bff/management/readiness/bff-ha"),
    ("GET", "/bff/management/readiness/broker-live"),
    ("GET", "/bff/management/readiness/capital-binding-live"),
    ("GET", "/bff/management/readiness/ep5"),
    ("GET", "/bff/management/readiness/strict-publish"),
    ("GET", "/bff/mcp-servers"),
    ("GET", "/bff/mcp-servers/{id}"),
    ("GET", "/bff/mcp-tools"),
    ("GET", "/bff/mcp-tools/{id}"),
    ("GET", "/bff/me"),
    ("GET", "/bff/personas"),
    ("GET", "/bff/personas/{id}"),
    ("GET", "/bff/ranking-formulas"),
    ("GET", "/bff/ranking-formulas/{id}"),
    ("GET", "/bff/readyz"),
    ("GET", "/bff/rebalances"),
    ("GET", "/bff/rebalances/{id}"),
    ("GET", "/bff/research-experiments"),
    ("GET", "/bff/research-experiments/{id}"),
    ("GET", "/bff/runtimes"),
    ("GET", "/bff/runtimes/{id}"),
    ("GET", "/bff/skills"),
    ("GET", "/bff/skills/{id}"),
    ("GET", "/bff/strategies"),
    ("GET", "/bff/strategies/{id}"),
    ("GET", "/bff/tools"),
    ("GET", "/bff/tools/{id}"),
    ("GET", "/bff/v5/control-room"),
    ("GET", "/bff/v5/execution/persona-health"),
    ("GET", "/bff/v5/execution/strategy-health"),
    ("GET", "/bff/v5/interventions"),
    ("GET", "/bff/v5/interventions/{id}"),
    ("GET", "/bff/v5/loop-runs"),
    ("GET", "/bff/v5/loop-runs/{id}"),
    ("GET", "/bff/v5/sentinel/findings"),
    ("GET", "/bff/v5/sentinel/findings/{id}"),
    ("PATCH", "/bff/agora/journal/{id}"),
    ("PATCH", "/bff/artifacts/{id}"),
    ("PATCH", "/bff/capital-pools/{id}"),
    ("PATCH", "/bff/deployments/{id}"),
    ("PATCH", "/bff/evolution-programs/{id}"),
    ("PATCH", "/bff/me/locale"),
    ("PATCH", "/bff/personas/{id}"),
    ("PATCH", "/bff/ranking-formulas/{id}"),
    ("PATCH", "/bff/rebalances/{id}"),
    ("PATCH", "/bff/research-experiments/{id}"),
    ("PATCH", "/bff/strategies/{id}"),
    ("POST", "/bff/actions/{entityType}/{entityId}/{actionId}"),
    ("POST", "/bff/agora/ask"),
    ("POST", "/bff/agora/signals/{id}/feedback"),
    ("POST", "/bff/alerts/{id}/acknowledge"),
    ("POST", "/bff/alerts/{id}/escalate-incident"),
    ("POST", "/bff/approvals/batch-decide"),
    ("POST", "/bff/approvals/{id}/decide"),
    ("POST", "/bff/artifacts"),
    ("POST", "/bff/audit/export"),
    ("POST", "/bff/auth/refresh"),
    ("POST", "/bff/capital-pools"),
    ("POST", "/bff/command-confirmations"),
    ("POST", "/bff/confirm-tokens"),
    ("POST", "/bff/confirm-tokens/{tokenId}/redeem"),
    ("POST", "/bff/deployments"),
    ("POST", "/bff/evolution-programs"),
    ("POST", "/bff/incidents/{id}/append-postmortem"),
    ("POST", "/bff/incidents/{id}/resolve"),
    ("POST", "/bff/incidents/{id}/rollback-deployment"),
    ("POST", "/bff/incidents/{id}/start-mitigation"),
    ("POST", "/bff/logout"),
    ("POST", "/bff/mcp-servers/{id}/import-tools"),
    ("POST", "/bff/personas"),
    ("POST", "/bff/ranking-formulas"),
    ("POST", "/bff/rebalances"),
    ("POST", "/bff/research-experiments"),
    ("POST", "/bff/strategies"),
    ("POST", "/bff/switch-tenant"),
    ("POST", "/bff/v5/interventions/{id}/claim"),
    ("POST", "/bff/v5/interventions/{id}/decide"),
    ("POST", "/bff/v5/interventions/{id}/escalate"),
    ("POST", "/bff/v5/interventions/{id}/release"),
    ("POST", "/bff/v5/interventions/{id}/two-man-sign"),
    ("POST", "/bff/v5/sentinel/findings/{id}/status"),
    ("POST", "/bff/v5/sentinel/remediation/build"),
    ("POST", "/bff/v5/sentinel/remediation/{actionId}/execute"),
}

LIVE_PROBE_CONCRETE_ROUTES = [
    ("GET", "/bff/approvals"),
    ("POST", "/bff/mcp-servers/server-alpha/import-tools"),
    ("GET", "/bff/v5/interventions"),
    ("GET", "/bff/me"),
    ("POST", "/bff/auth/refresh"),
    ("POST", "/bff/logout"),
    ("POST", "/bff/actions/strategy/stg_001/submit"),
    ("GET", "/bff/strategies"),
    ("GET", "/bff/strategies/stg_001"),
    ("POST", "/bff/strategies/stg_001/actions/submit"),
    ("GET", "/bff/personas"),
    ("GET", "/bff/personas/persona_001"),
    ("GET", "/bff/capital-pools"),
    ("GET", "/bff/capital-pools/pool_001"),
    ("GET", "/bff/rebalances"),
    ("GET", "/bff/deployments"),
    ("GET", "/bff/evolution-programs"),
    ("GET", "/bff/jobs"),
    ("GET", "/bff/management/cockpit"),
    ("GET", "/bff/management/evidence"),
    ("GET", "/bff/management/evolution-journal"),
    ("GET", "/bff/management/persona-intent"),
    ("GET", "/bff/management/readiness/ep5"),
    ("GET", "/bff/management/readiness/broker-live"),
    ("GET", "/bff/management/readiness/capital-binding-live"),
    ("GET", "/bff/management/readiness/bff-ha"),
    ("GET", "/bff/management/readiness/strict-publish"),
    ("POST", "/bff/approvals/apr_001/decide"),
    ("POST", "/bff/approvals/batch-decide"),
    ("GET", "/bff/alerts"),
    ("POST", "/bff/alerts/alert_001/acknowledge"),
    ("GET", "/bff/incidents"),
    ("GET", "/bff/audit"),
    ("GET", "/bff/artifacts"),
    ("GET", "/bff/management/persona-league"),
    ("GET", "/bff/management/persona-league/heatmap"),
    ("GET", "/bff/management/persona-league/rankings"),
    ("GET", "/bff/management/persona-league/tiers"),
    ("GET", "/bff/management/quarterly-ranking"),
    ("GET", "/bff/management/quarterly-ranking/formula"),
    ("GET", "/bff/management/quarterly-ranking/recommendations"),
    ("GET", "/bff/management/performance-attribution"),
    ("GET", "/bff/management/performance-attribution/by-persona"),
    ("GET", "/bff/runtimes"),
    ("GET", "/bff/mcp-servers"),
    ("GET", "/bff/mcp-tools"),
    ("GET", "/bff/skills"),
    ("GET", "/bff/channels"),
    ("GET", "/bff/tools"),
    ("GET", "/bff/ranking-formulas"),
    ("GET", "/bff/research-experiments"),
    ("GET", "/bff/agora/signals"),
    ("GET", "/bff/agora/inbox"),
    ("GET", "/bff/agora/journal"),
    ("GET", "/bff/agora/postmortems"),
    ("GET", "/bff/agora/ask/sessions"),
    ("GET", "/bff/v5/loop-runs"),
    ("GET", "/bff/v5/sentinel/findings"),
    ("POST", "/bff/v5/interventions/intv_001/decide"),
    ("GET", "/bff/v5/execution/persona-health"),
]


HEADERS = {"Authorization": "Bearer op-execute-plans:operator,reviewer,admin:mfa"}
FINAL_DETAIL_ID_KEYS = (
    "id",
    "strategy_id",
    "persona_id",
    "pool_id",
    "rebalance_id",
    "plan_id",
    "runtime_id",
    "program_id",
    "experiment_id",
    "artifact_id",
    "formula_id",
    "alert_id",
    "incident_id",
    "server_id",
    "tool_id",
    "skill_id",
    "channel_id",
    "intervention_id",
)


@contextmanager
def _isolated_final_read_models(*, fallback: bool = True) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_mcp_servers = dict(bff_main._MCP_SERVER_REGISTRY)
        original_mcp_tools = dict(bff_main._MCP_TOOL_REGISTRY)
        original_tools = dict(bff_main._TOOL_REGISTRY)
        original_skills = dict(bff_main._SKILL_REGISTRY)
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=fallback,
        )
        bff_main.read_store = store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._MCP_SERVER_REGISTRY.clear()
        bff_main._MCP_TOOL_REGISTRY.clear()
        bff_main._TOOL_REGISTRY.clear()
        bff_main._SKILL_REGISTRY.clear()
        bff_main._V5_INTERVENTIONS_STORE.clear()
        try:
            if fallback:
                store.create_ranking_formula(
                    name="Formula Alpha",
                    description="Seeded final detail matrix formula",
                    actor_id="op-execute-plans",
                )
                store.create_rebalance(
                    capital_pool_id="pool-main",
                    actor_id="op-execute-plans",
                    created_at="2026-05-09T00:00:00Z",
                    reason="Seed final detail matrix",
                )
                bff_main._MCP_SERVER_REGISTRY["server-alpha"] = {
                    "id": "server-alpha",
                    "server_id": "server-alpha",
                    "name": "Server Alpha",
                    "status": "registered",
                }
                bff_main._MCP_TOOL_REGISTRY["server-alpha:tool-alpha"] = {
                    "id": "tool-alpha",
                    "tool_id": "tool-alpha",
                    "server_id": "server-alpha",
                    "name": "Tool Alpha",
                    "status": "imported",
                }
                bff_main._TOOL_REGISTRY["tool-alpha"] = {
                    "id": "tool-alpha",
                    "tool_id": "tool-alpha",
                    "name": "Tool Alpha",
                    "status": "active",
                }
                bff_main._SKILL_REGISTRY["skill-alpha"] = {
                    "id": "skill-alpha",
                    "skill_id": "skill-alpha",
                    "name": "Skill Alpha",
                    "status": "active",
                }
                bff_main._V5_INTERVENTIONS_STORE.append(
                    {
                        "intervention_id": "intv_001",
                        "kind": "risk_breach",
                        "status": "pending",
                        "target_type": "Runtime",
                        "target_id": "runtime-042",
                        "triggered_at": "2026-05-09T00:00:00Z",
                        "triggered_by": "sentinel",
                        "description": "Seed final detail matrix intervention",
                    }
                )
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._MCP_SERVER_REGISTRY.clear()
            bff_main._MCP_SERVER_REGISTRY.update(original_mcp_servers)
            bff_main._MCP_TOOL_REGISTRY.clear()
            bff_main._MCP_TOOL_REGISTRY.update(original_mcp_tools)
            bff_main._TOOL_REGISTRY.clear()
            bff_main._TOOL_REGISTRY.update(original_tools)
            bff_main._SKILL_REGISTRY.clear()
            bff_main._SKILL_REGISTRY.update(original_skills)
            bff_main._V5_INTERVENTIONS_STORE[:] = original_interventions


def _payload_records(payload: dict) -> list[dict]:
    for key in ("data", "items", "alerts", "artifacts", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _record_id(record: dict, preferred_key: str | None = None) -> str:
    if preferred_key:
        value = record.get(preferred_key)
        if value not in (None, ""):
            return str(value)
    for key in FINAL_DETAIL_ID_KEYS:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    raise AssertionError(f"record has no supported id key: {record}")


def _detail_data(payload: dict) -> dict:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _canonical_route_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


def _route_index() -> set[tuple[str, str]]:
    return {
        (method, _canonical_route_path(getattr(route, "path", "")))
        for route in bff_main.app.routes
        for method in (getattr(route, "methods", set()) or set())
        if method in {"DELETE", "GET", "PATCH", "POST", "PUT"}
    }


def test_execute_plans_final_contract_paths_are_registered() -> None:
    expected = {(method, _canonical_route_path(path)) for method, path in FINAL_CONTRACT_METHOD_PATHS}
    missing = expected - _route_index()
    assert not missing


def test_execute_plans_final_openapi_json_is_route_discoverable() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    response = client.get("/openapi.json")

    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    openapi_paths = {_canonical_route_path(path) for path in paths}
    missing = [path for _, path in FINAL_CONTRACT_METHOD_PATHS if _canonical_route_path(path) not in openapi_paths]
    assert not missing


def test_execute_plans_live_probe_catalog_no_longer_404s_anonymously() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    failures = []
    for method, path in LIVE_PROBE_CONCRETE_ROUTES:
        body = {} if method in {"PATCH", "POST", "PUT"} else None
        response = client.request(method, path, json=body)
        if response.status_code in {404, 500}:
            failures.append((method, path, response.status_code, response.text[:240]))

    assert not failures
    assert ("GET", "/bff/events/stream") in _route_index()


def test_execute_plans_final_stub_auth_smoke_avoids_server_errors(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    with _isolated_final_read_models() as client:
        for path in [
            "/bff/agora/signals/sig_001",
            "/bff/artifacts",
            "/bff/artifacts/art_001",
            "/bff/capital-pools/pool-main",
            "/bff/v5/execution/strategy-health",
        ]:
            response = client.get(path, headers=HEADERS)
            assert response.status_code < 500, response.text


def test_execute_plans_final_seeded_detail_paths_use_read_model_dtos(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    matrix = [
        ("/bff/strategies", "/bff/strategies/{id}", "id"),
        ("/bff/personas", "/bff/personas/{id}", "id"),
        ("/bff/capital-pools", "/bff/capital-pools/{id}", "id"),
        ("/bff/rebalances", "/bff/rebalances/{id}", "rebalance_id"),
        ("/bff/deployments", "/bff/deployments/{id}", "id"),
        ("/bff/runtimes", "/bff/runtimes/{id}", "runtime_id"),
        ("/bff/research-experiments", "/bff/research-experiments/{id}", "experiment_id"),
        ("/bff/artifacts", "/bff/artifacts/{id}", "artifact_id"),
        ("/bff/ranking-formulas", "/bff/ranking-formulas/{id}", "formula_id"),
        ("/bff/mcp-servers", "/bff/mcp-servers/{id}", "server_id"),
        ("/bff/mcp-tools", "/bff/mcp-tools/{id}", "tool_id"),
        ("/bff/skills", "/bff/skills/{id}", "skill_id"),
        ("/bff/channels", "/bff/channels/{id}", "channel_id"),
        ("/bff/tools", "/bff/tools/{id}", "tool_id"),
        ("/bff/incidents", "/bff/incidents/{id}", "incident_id"),
        ("/bff/alerts", "/bff/alerts/{id}", "alert_id"),
        ("/bff/v5/interventions", "/bff/v5/interventions/{id}", "intervention_id"),
    ]

    with _isolated_final_read_models() as client:
        failures = []
        for list_path, detail_template, preferred_key in matrix:
            list_response = client.get(list_path, headers=HEADERS)
            if list_response.status_code != 200:
                failures.append((list_path, list_response.status_code, list_response.text[:240]))
                continue
            records = _payload_records(list_response.json())
            if not records:
                failures.append((list_path, "empty", list_response.text[:240]))
                continue
            entity_id = _record_id(records[0], preferred_key)
            detail_response = client.get(detail_template.format(id=entity_id), headers=HEADERS)
            if detail_response.status_code != 200:
                failures.append((detail_template, detail_response.status_code, detail_response.text[:240]))
                continue
            data = _detail_data(detail_response.json())
            if set(data.keys()) == {"id"} or data.get("status") == "degraded":
                failures.append((detail_template, "generic_or_degraded", detail_response.text[:240]))
                continue
            if not any(str(data.get(key) or "") == entity_id for key in FINAL_DETAIL_ID_KEYS):
                failures.append((detail_template, "id_mismatch", detail_response.text[:240]))

        assert not failures


def test_execute_plans_final_detail_unknown_ids_are_404_when_source_exists(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _isolated_final_read_models() as client:
        for path in [
            "/bff/ranking-formulas/not-a-formula",
            "/bff/research-experiments/not-an-experiment",
            "/bff/artifacts/not-an-artifact",
            "/bff/mcp-servers/not-a-server",
            "/bff/mcp-tools/not-a-tool",
            "/bff/channels/not-a-channel",
        ]:
            response = client.get(path, headers=HEADERS)
            assert response.status_code == 404, response.text


def test_execute_plans_final_detail_missing_source_returns_degraded_dto(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _isolated_final_read_models(fallback=False) as client:
        response = client.get("/bff/ranking-formulas/formula-unavailable", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "formula-unavailable"
    assert payload["data"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["ranking_formula_detail"]["status"] == "unavailable"
