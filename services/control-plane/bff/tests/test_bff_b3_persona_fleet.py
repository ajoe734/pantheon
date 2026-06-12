"""
BFF-B3-002: contract tests for GET /bff/management/persona-fleet.

The route is a read-only Management aggregate. It composes the existing B2
persona facade with persona-capital bindings, runtime bindings, telemetry,
trainer sessions, and evolution decisions.
"""
from __future__ import annotations

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
            assert "data" in body and "items" in body and "meta" in body
            assert body["data"]["items"] == body["items"]
            assert body["data"]["persona_fleet"] == body["items"]
            assert body["summary"]["total_personas"] >= 1
            assert body["meta"]["surfaces"]["persona_fleet"]["source"] == "bff_composed"

            alpha = next(item for item in body["items"] if item["id"] == "persona-alpha")
            assert alpha["personaName"] == "Alpha Persona"
            assert alpha["capitalPoolId"] == "pool-main"
            assert alpha["health"] in {"healthy", "degraded", "critical"}
            assert alpha["governanceRequired"] is True
            assert alpha["drillDown"]["href"] == "/personas/persona-alpha"
            assert "metrics" in alpha
            assert "currentWork" in alpha
            assert body["data"]["execution_boundary"] == {
                "approved_artifacts_only": True,
                "live_capital_side_effects": False,
                "human_gate_required_for_capital_changes": True,
            }
        finally:
            bff_main.read_store = original


def test_persona_fleet_supports_health_filter_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get(
                "/bff/management/persona-fleet?health=healthy&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert len(body["items"]) == 1
            assert body["items"][0]["health"] == "healthy"
            assert body["summary"]["healthy_personas"] >= 1
            assert body["data"]["page_info"] == body["page_info"]
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
            assert error["code"] in {"AUTH_REQUIRED", "INVALID_TOKEN"}
        finally:
            bff_main.read_store = original
