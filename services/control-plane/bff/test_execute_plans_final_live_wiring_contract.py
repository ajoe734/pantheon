from __future__ import annotations

import os
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
