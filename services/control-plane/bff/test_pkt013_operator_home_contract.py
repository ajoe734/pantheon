from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt013_operator_home_returns_backend_owned_summary_cards() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
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
        store.get_telemetry_summary = lambda runtime_id: {
            "runtime_id": "runtime-042",
            "window": "1h",
            "drawdown": 0.125,
            "fill_rate": 0.89,
            "avg_slippage_bps": 4.2,
            "collected_at": "2026-04-18T06:10:00Z",
        } if runtime_id == "runtime-042" else None
        store.dataset_source = lambda dataset: {
            "incidents": "service_store",
            "governance_review_queue_items": "local_snapshot",
            "approval_queue_items": "local_snapshot",
            "kill_switch": "local_snapshot",
            "runtime_bindings": "canonical",
            "telemetry_summaries": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/home",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["overall_status"] == "degraded"
            assert payload["headline"] == "Operator attention required"
            assert payload["message"] == "Safe mode or kill-switch activity requires immediate review."
            assert payload["safe_mode_state"]["status"] == "soft"
            assert payload["safe_mode_state"]["kill_switch_status"] == "triggered"
            assert [card["card_id"] for card in payload["cards"]] == [
                "alerts",
                "incidents",
                "governance",
                "runtime",
                "health",
            ]
            assert payload["cards"][0]["details"]["total_active"] == 5
            assert payload["cards"][0]["target_refs"] == [
                {
                    "surface_id": "OC-02",
                    "label": "Open alerts rail",
                    "href": "/alerts",
                }
            ]
            assert payload["cards"][1]["details"]["active_incident_count"] == 1
            assert payload["cards"][1]["target_refs"] == [
                {"label": "Incident Home", "href": "/operator/incidents"}
            ]
            assert payload["cards"][2]["details"]["total_pending_items"] == 2
            assert payload["cards"][2]["target_refs"] == [
                {"label": "Governance Review Queue", "href": "/governance-review-queue"},
                {"label": "Governance Approval Queue", "href": "/governance-approval-queue"},
            ]
            assert payload["cards"][3]["details"]["runtime"]["total_runtime_count"] == 1
            assert payload["cards"][3]["details"]["telemetry"]["covered_runtime_count"] == 1
            assert payload["cards"][3]["target_refs"] == [
                {
                    "surface_id": "OC-04",
                    "label": "Open runtime state board",
                    "href": "/operator/runtime-state",
                }
            ]
            assert payload["cards"][4]["details"]["headline"] == "Safe mode active"
            assert payload["cards"][4]["details"]["group_counts"] == {
                "ok": 2,
                "degraded": 3,
                "unavailable": 0,
            }
            assert payload["cards"][4]["target_refs"] == [
                {
                    "surface_id": "OC-03",
                    "label": "Open health status board",
                    "href": "/operator/health-status",
                }
            ]
            shortcut_ids = [shortcut["shortcut_id"] for shortcut in payload["escalation_shortcuts"]]
            assert shortcut_ids == [
                "open-alerts-rail",
                "open-incident-home",
                "open-health-status",
                "open-approval-queue",
                "open-runtime-state",
            ]
            assert [shortcut["href"] for shortcut in payload["escalation_shortcuts"]] == [
                "/alerts",
                "/operator/incidents",
                "/operator/health-status",
                "/governance-approval-queue",
                "/operator/runtime-state",
            ]
            assert payload["meta"]["surfaces"]["operator_home"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["alerts"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["health_status"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store


def test_pkt013_operator_home_returns_unavailable_state_without_false_empty_dashboard() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_incidents = lambda **kwargs: []
        store.list_governance_review_queue_items = lambda **kwargs: []
        store.list_approval_queue_items = lambda **kwargs: []
        store.get_kill_switch_status = lambda: {}
        store.list_runtime_bindings = lambda: []
        store.get_telemetry_summary = lambda runtime_id: None
        store.dataset_source = lambda dataset: "missing"
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/home",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["overall_status"] == "unavailable"
            assert payload["headline"] == "Operator home unavailable"
            assert payload["meta"]["surfaces"]["operator_home"]["status"] == "unavailable"
            assert payload["cards"][0]["status"] == "unavailable"
            assert payload["cards"][4]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store
