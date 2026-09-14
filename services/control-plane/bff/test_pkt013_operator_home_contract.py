from __future__ import annotations

from typing import Any, Dict, Optional
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _make_client(store: Any) -> TestClient:
    app = FastAPI()
    app.include_router(create_management_router(read_surface=store))
    return TestClient(app)


def test_pkt013_operator_home_returns_backend_owned_summary_cards() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-001",
            "title": "Unexpected drawdown in persona-alpha",
            "severity": "high",
            "status": "open",
            "created_at": "2026-04-18T06:05:00Z",
        }
    ]
    store.list_governance_review_queue_items = lambda **kwargs: [
        {
            "item_id": "gov-review-001",
            "item_type": "DeploymentPlan",
            "risk_level": "medium",
            "status": "pending",
            "submitted_at": "2026-04-18T06:03:00Z",
        }
    ]
    store.list_approval_queue_items = lambda **kwargs: [
        {
            "decision_id": "appr-001",
            "decision_type": "DeploymentPlan",
            "risk_level": "high",
            "decision_state": "pending",
            "submitted_at": "2026-04-18T06:04:00Z",
        }
    ]
    store.get_kill_switch_status = lambda: {
        "active": True,
        "status": "triggered",
        "safe_mode_status": "soft",
        "last_confirmed_at": "2026-04-18T06:07:00Z",
        "last_triggered_at": "2026-04-18T06:08:00Z",
        "active_commands": ["safe-mode-001"],
        "secondary_path_available": True,
    }
    store.list_runtime_bindings = lambda: [
        {
            "id": "runtime-042",
            "runtime_id": "runtime-042",
            "deployment_stage": "live",
            "status": "running",
            "plan_id": "plan-F-042",
        }
    ]
    store.list_telemetry_summaries = lambda: {
        "runtime-042": {
            "runtime_id": "runtime-042",
            "window": "1h",
            "drawdown": 0.125,
            "fill_rate": 0.89,
            "avg_slippage_bps": 4.2,
            "collected_at": "2026-04-18T06:10:00Z",
        }
    }
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/home",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert [card["card_id"] for card in payload["cards"]] == [
        "alerts",
        "incidents",
        "governance",
        "runtime",
        "health",
    ]
    assert payload["cards"][0]["status"] == "degraded"
    assert payload["cards"][0]["details"]["open_incidents"] == 1
    assert payload["cards"][0]["target_refs"] == [
        {
            "surface_id": "OC-02",
            "label": "Open alerts rail",
            "href": "/bff/alerts",
        }
    ]
    assert payload["cards"][1]["details"]["open_incidents"] == 1
    assert payload["cards"][1]["target_refs"] == [
        {"surface_id": "OC-02", "label": "Incidents", "href": "/bff/alerts"}
    ]
    assert payload["cards"][2]["details"]["pending_items"] == 2
    assert payload["cards"][2]["target_refs"] == [
        {"surface_id": "OC-01", "label": "Approval Queue", "href": "/api/v1/operator/governance/approval-queue"}
    ]
    assert payload["cards"][3]["details"]["runtime"]["total_runtimes"] == 1
    assert payload["cards"][3]["details"]["runtime"]["active_runtimes"] == 1
    assert payload["cards"][3]["details"]["telemetry"]["telemetry_count"] == 1
    assert payload["cards"][3]["target_refs"] == [
        {
            "surface_id": "OC-04",
            "label": "Runtime State",
            "href": "/api/v1/operator/runtime-state",
        }
    ]
    assert payload["cards"][4]["details"]["safe_mode_state"]["status"] == "soft"
    assert payload["cards"][4]["details"]["safe_mode_state"]["kill_switch_status"] == "triggered"
    assert payload["cards"][4]["target_refs"] == [
        {
            "surface_id": "OC-03",
            "label": "Health Status",
            "href": "/api/v1/operator/health-status",
        }
    ]
    assert payload["meta"]["surfaces"]["operator_home"]["status"] == "ok"
    assert "snapshot_at" in payload["meta"]


def test_pkt013_operator_home_returns_unavailable_state_without_false_empty_dashboard() -> None:
    class UnavailableStore:
        def list_runtime_bindings(self) -> Any:
            raise RuntimeError("down")

        def list_telemetry_summaries(self) -> Any:
            raise RuntimeError("down")

        def list_incidents(self) -> Any:
            raise RuntimeError("down")

        def list_incident_alerts(self) -> Any:
            raise RuntimeError("down")

        def list_approval_queue_items(self) -> Any:
            raise RuntimeError("down")

        def list_governance_review_queue_items(self) -> Any:
            raise RuntimeError("down")

        def get_kill_switch_status(self) -> Any:
            raise RuntimeError("down")

    client = _make_client(UnavailableStore())

    response = client.get(
        "/api/v1/operator/home",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["meta"]["surfaces"]["operator_home"]["status"] == "unavailable"
    assert payload["cards"][0]["status"] == "unavailable"
    assert payload["cards"][4]["status"] == "unavailable"
    assert payload["cards"][4]["details"]["group_counts"]["unavailable"] == 5
