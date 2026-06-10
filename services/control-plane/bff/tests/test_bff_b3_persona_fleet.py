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
            assert body["data"] == body["items"]
            assert body["summary"]["total_personas"] >= 1
            assert body["meta"]["surfaces"]["persona_fleet"]["source"] == "bff_composed"

            alpha = next(item for item in body["items"] if item["id"] == "persona-alpha")
            assert alpha["persona"]["name"] == "Alpha Persona"
            assert alpha["bindings"][0]["capital_pool_id"] == "pool-main"
            assert alpha["capitalPools"][0]["id"] == "pool-main"
            assert alpha["runtimeBindings"][0]["runtime_id"] == "runtime-042"

            telemetry = alpha["telemetrySummary"]
            assert telemetry["runtime_count"] >= 1
            assert telemetry["covered_runtime_count"] >= 1
            assert telemetry["latest"]["runtime_id"] == "runtime-042"
            assert telemetry["latest"]["drawdown"] == 0.125

            training = alpha["training"]
            assert training["session_count"] >= 1
            assert training["latest_session"]["persona_id"] == "persona-alpha"

            evolution = alpha["evolution"]
            assert evolution["decision_count"] >= 1
            assert {decision["id"] for decision in evolution["decisions"]} >= {"evo-dec-001"}

            health = alpha["health"]
            assert health["status"] == "critical"
            assert "drawdown_threshold" in health["reasons"]
            assert health["active_incident_count"] >= 1
        finally:
            bff_main.read_store = original


def test_persona_fleet_supports_health_filter_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get(
                "/bff/management/persona-fleet?health=critical&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert len(body["items"]) == 1
            assert body["items"][0]["health"]["status"] == "critical"
            assert body["summary"]["critical_personas"] >= 1
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
