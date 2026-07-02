"""
BFF-B3-002: contract tests for GET /bff/management/persona-fleet.

The route is a read-only Management aggregate. It composes the existing B2
persona facade with persona-capital bindings, runtime bindings, telemetry,
trainer sessions, and evolution decisions.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3:operator"}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main._PERSONA_BFF_OVERLAY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app)


def test_persona_fleet_treats_deployed_lifecycle_as_operational() -> None:
    health = bff_main._project_persona_fleet_health(
        persona={"persona_id": "persona-deployed", "lifecycle_state": "deployed"},
        runtime_bindings=[{"runtime_id": "runtime-deployed", "status": "active"}],
        telemetry_summaries=[{"runtime_id": "runtime-deployed", "collected_at": "2026-06-03T08:00:00Z"}],
        active_incidents=[],
    )

    assert health["status"] == "healthy"
    assert "persona_lifecycle_not_active" not in health["reasons"]


def test_persona_fleet_composes_persona_bindings_telemetry_training_and_evolution() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/persona-fleet", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(json.dumps(body).encode("utf-8")) < 250_000
            assert set(body) == {"data", "page_info", "meta"}
            assert set(body["data"]) == {"items", "summary"}
            assert "items" not in body
            assert "summary" not in body
            assert "persona_fleet" not in body["data"]
            assert "persona_league" not in body["data"]
            assert "capital_pools" not in body["data"]
            assert "runtime_bindings" not in body["data"]
            assert "human_inbox" not in body["data"]
            assert body["data"]["summary"]["total_personas"] >= 1
            assert body["meta"]["surfaces"]["persona_fleet"]["source"] in {
                "bff_composed_slim_list",
                "service_store",
                "local_snapshot",
            }

            alpha = next(item for item in body["data"]["items"] if item["id"] == "persona-alpha")
            assert alpha["name"] == "Alpha Persona"
            assert alpha["capital_pool_id"] == "pool-main"
            assert alpha["health"] in {"healthy", "degraded", "critical"}
            assert alpha["governance_required"] is True
            assert "data_source_summary" in alpha
            assert "research_summary" in alpha
            assert "performance_summary" in alpha
            assert "dataSourceStatus" not in alpha
            assert "data_source_status" not in alpha
            assert "dataSources" not in alpha
            assert "data_sources" not in alpha
            assert "currentResearchProjects" not in alpha
            assert "current_research_projects" not in alpha
            assert body["data"]["summary"]["execution_boundary"] == {
                "approved_artifacts_only": True,
                "live_capital_side_effects": False,
                "human_gate_required_for_capital_changes": True,
            }
            assert body["meta"]["related"]["human_inbox"]["href"] == "/bff/management/human-inbox"
        finally:
            bff_main.read_store = original


def test_persona_fleet_supports_health_filter_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            all_resp = client.get(
                "/bff/management/persona-fleet?page_size=50",
                headers=OPERATOR_HEADERS,
            )
            assert all_resp.status_code == 200, all_resp.text
            existing_health = all_resp.json()["data"]["items"][0]["health"]
            resp = client.get(
                f"/bff/management/persona-fleet?health={existing_health}&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert len(body["data"]["items"]) == 1
            assert body["data"]["items"][0]["health"] == existing_health
            assert body["data"]["summary"]["total_personas"] >= 1
            assert "page_info" not in body["data"]

            existing_stage = all_resp.json()["data"]["items"][0]["deployment_stage"]
            stage_resp = client.get(
                f"/bff/management/persona-fleet?deployment_stage={existing_stage}&page_size=50",
                headers=OPERATOR_HEADERS,
            )
            assert stage_resp.status_code == 200, stage_resp.text
            stage_items = stage_resp.json()["data"]["items"]
            assert stage_items
            assert {item["deployment_stage"] for item in stage_items} == {existing_stage}
        finally:
            bff_main.read_store = original


def test_persona_fleet_requires_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/persona-fleet")

            assert resp.status_code == 401, resp.text
            body = resp.json()
            error = body.get("error") or (body.get("detail") or {}).get("error") or {}
            assert error["code"] in {"AUTH_REQUIRED", "AUTH_REQUIRED"}
        finally:
            bff_main.read_store = original


def test_legacy_management_fleet_alias_is_not_registered() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/fleet", headers=OPERATOR_HEADERS)

            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original
