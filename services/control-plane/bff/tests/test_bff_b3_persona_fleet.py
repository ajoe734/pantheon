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
            boundary = body["data"]["execution_boundary"]
            assert boundary["approved_artifacts_only"] is True
            assert boundary["live_capital_side_effects"] is False
            assert boundary["human_gate_required_for_capital_changes"] is True
            assert boundary["competition_default"] == "unified_paper_canary_live_cohort"
            assert boundary["mode_selector"]["semantics"] == "command_safety_context_only"
            assert boundary["mode_selector"]["does_not_filter_competition_tracks"] is True
            assert alpha["readinessProjection"]["competition_track"] in {
                "paper_challenger",
                "canary_challenger",
                "live_incumbent",
                "watchlist_incumbent",
                "risk_off_excluded",
            }
            assert alpha["rowAction"]["startupWizardVisible"] is False
        finally:
            bff_main.read_store = original


def test_persona_fleet_default_unifies_paper_canary_and_live_tracks() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            store._data["personas"] = {
                "persona-paper-test": {
                    "id": "persona-paper-test",
                    "persona_id": "persona-paper-test",
                    "name": "Paper Challenger",
                    "lifecycle_state": "active",
                    "status": "active",
                    "created_at": "2026-07-02T00:00:00Z",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                    "metadata": {
                        "owner": "test",
                        "market_scope": ["US"],
                        "capital_pool_id": "pool-paper-test",
                        "deployment_stage": "paper",
                    },
                },
                "persona-canary-test": {
                    "id": "persona-canary-test",
                    "persona_id": "persona-canary-test",
                    "name": "Canary Challenger",
                    "lifecycle_state": "active",
                    "status": "active",
                    "created_at": "2026-07-02T00:00:00Z",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                    "metadata": {
                        "owner": "test",
                        "market_scope": ["US"],
                        "capital_pool_id": "pool-canary-test",
                        "deployment_stage": "canary",
                    },
                },
                "persona-live-test": {
                    "id": "persona-live-test",
                    "persona_id": "persona-live-test",
                    "name": "Live Incumbent",
                    "lifecycle_state": "active",
                    "status": "active",
                    "created_at": "2026-07-02T00:00:00Z",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                    "metadata": {
                        "owner": "test",
                        "market_scope": ["US"],
                        "capital_pool_id": "pool-live-test",
                        "deployment_stage": "live",
                    },
                },
            }
            store._data["bindings"] = {
                "binding-paper-test": {
                    "binding_id": "binding-paper-test",
                    "persona_id": "persona-paper-test",
                    "capital_pool_id": "pool-paper-test",
                    "status": "active",
                    "validity": "active",
                    "allowed_deployment_scope": "paper",
                },
                "binding-canary-test": {
                    "binding_id": "binding-canary-test",
                    "persona_id": "persona-canary-test",
                    "capital_pool_id": "pool-canary-test",
                    "status": "active",
                    "validity": "active",
                    "allowed_deployment_scope": "canary",
                },
                "binding-live-test": {
                    "binding_id": "binding-live-test",
                    "persona_id": "persona-live-test",
                    "capital_pool_id": "pool-live-test",
                    "status": "active",
                    "validity": "active",
                    "allowed_deployment_scope": "live",
                },
            }
            store._data["runtime_bindings"] = {
                "runtime-paper-test": {
                    "runtime_binding_id": "rb-paper-test",
                    "runtime_id": "runtime-paper-test",
                    "capital_pool_id": "pool-paper-test",
                    "deployment_stage": "paper",
                    "status": "active",
                },
                "runtime-canary-test": {
                    "runtime_binding_id": "rb-canary-test",
                    "runtime_id": "runtime-canary-test",
                    "capital_pool_id": "pool-canary-test",
                    "deployment_stage": "canary",
                    "status": "active",
                },
                "runtime-live-test": {
                    "runtime_binding_id": "rb-live-test",
                    "runtime_id": "runtime-live-test",
                    "capital_pool_id": "pool-live-test",
                    "deployment_stage": "live",
                    "status": "active",
                },
            }
            store._local_overlay_write_datasets.add("runtime_bindings")
            store._data["persona_league"] = {
                "persona-paper-test": {
                    "persona_id": "persona-paper-test",
                    "deployment_stage": "paper",
                    "capital_pool_id": "pool-paper-test",
                    "runtime_id": "runtime-paper-test",
                    "league_score": 82.0,
                    "league_rank": 3,
                    "governance_required": True,
                    "recommendation": "promote_canary_review",
                    "status": "paper_running",
                },
                "persona-canary-test": {
                    "persona_id": "persona-canary-test",
                    "deployment_stage": "canary",
                    "capital_pool_id": "pool-canary-test",
                    "runtime_id": "runtime-canary-test",
                    "league_score": 88.0,
                    "league_rank": 2,
                    "governance_required": True,
                    "recommendation": "canary_live_review",
                    "status": "canary_running",
                },
                "persona-live-test": {
                    "persona_id": "persona-live-test",
                    "deployment_stage": "live",
                    "capital_pool_id": "pool-live-test",
                    "runtime_id": "runtime-live-test",
                    "league_score": 91.0,
                    "league_rank": 1,
                    "governance_required": True,
                    "recommendation": "",
                    "status": "live_running",
                },
            }
            bff_main.read_store = store
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            response = client.get("/bff/management/persona-fleet", headers=OPERATOR_HEADERS)
            live_response = client.get(
                "/bff/management/persona-fleet?competition_track=live_incumbent",
                headers=OPERATOR_HEADERS,
            )

            assert response.status_code == 200, response.text
            rows = {item["persona_id"]: item for item in response.json()["items"]}
            assert rows["persona-paper-test"]["state"] == "paper_running"
            assert rows["persona-canary-test"]["state"] == "canary_running"
            assert rows["persona-live-test"]["state"] == "live_running"
            assert rows["persona-paper-test"]["competitionTrack"] == "paper_challenger"
            assert rows["persona-canary-test"]["competitionTrack"] == "canary_challenger"
            assert rows["persona-live-test"]["competitionTrack"] == "live_incumbent"
            assert rows["persona-paper-test"]["requiredHumanReview"] == "promotion_to_canary"
            assert rows["persona-paper-test"]["reviewStatus"] == "promotion_pending"
            assert rows["persona-paper-test"]["rowAction"]["actionId"] == "open_promotion_review"
            assert rows["persona-paper-test"]["rowAction"]["label"] == "開啟 Canary 審核"
            assert (
                rows["persona-paper-test"]["rowAction"]["href"]
                == "/management/human-inbox/readiness_blocker%3Apersona%3Apersona-paper-test"
            )
            assert rows["persona-paper-test"]["rowAction"]["startupWizardVisible"] is False
            assert rows["persona-live-test"]["rowAction"]["actionId"] == "monitor_live_runtime"
            assert response.json()["data"]["execution_boundary"]["separate_paper_live_datasets"] is False

            assert live_response.status_code == 200, live_response.text
            live_rows = {item["persona_id"] for item in live_response.json()["items"]}
            assert "persona-live-test" in live_rows
            assert "persona-paper-test" not in live_rows
            assert "persona-canary-test" not in live_rows
        finally:
            bff_main.read_store = original


def test_persona_fleet_projects_legacy_deployed_paper_runtime_as_paper_running() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            store._data["personas"] = {
                "persona-legacy-deployed-paper": {
                    "id": "persona-legacy-deployed-paper",
                    "persona_id": "persona-legacy-deployed-paper",
                    "name": "Legacy Paper Persona",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-06-07T00:00:00Z",
                    "updated_at": "2026-06-07T00:00:00Z",
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                    "metadata": {
                        "owner": "test",
                        "capital_pool_id": "pool-legacy-paper",
                    },
                },
            }
            store._data["persona_bindings"] = {
                "binding-legacy-paper": {
                    "binding_id": "binding-legacy-paper",
                    "persona_id": "persona-legacy-deployed-paper",
                    "capital_pool_id": "pool-legacy-paper",
                    "status": "active",
                    "validity": "active",
                    "allowed_deployment_scope": "paper",
                },
            }
            store._data["capital_pools"] = {
                "pool-legacy-paper": {
                    "id": "pool-legacy-paper",
                    "pool_id": "pool-legacy-paper",
                    "capital_pool_id": "pool-legacy-paper",
                    "capital_scope": "paper",
                    "live_capital_enabled": False,
                },
            }
            store._data["runtime_bindings"] = {
                "runtime-legacy-paper": {
                    "runtime_binding_id": "rb-legacy-paper",
                    "runtime_id": "runtime-legacy-paper",
                    "capital_pool_id": "pool-legacy-paper",
                    "deployment_stage": "paper",
                    "status": "active",
                },
            }
            store._local_overlay_write_datasets.add("persona_bindings")
            store._local_overlay_write_datasets.add("capital_pools")
            store._local_overlay_write_datasets.add("runtime_bindings")
            bff_main.read_store = store
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            response = client.get("/bff/management/persona-fleet", headers=OPERATOR_HEADERS)

            assert response.status_code == 200, response.text
            rows = {item["persona_id"]: item for item in response.json()["items"]}
            row = rows["persona-legacy-deployed-paper"]
            assert row["personaStatus"] == "deployed"
            assert row["state"] == "paper_running"
            assert row["deploymentStage"] == "paper"
            assert row["competitionTrack"] == "paper_challenger"
            assert row["capitalScope"] == "paper"
            assert row["readinessProjection"]["product_lifecycle_state"] == "paper_running"
            assert row["rowAction"]["actionId"] == "monitor_paper_runtime"
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
            assert error["code"] in {"AUTH_REQUIRED", "AUTH_REQUIRED"}
        finally:
            bff_main.read_store = original
