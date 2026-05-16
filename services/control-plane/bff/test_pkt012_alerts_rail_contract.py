from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt012_alerts_rail_returns_backend_owned_alert_feed() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
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
                "status": "escalated",
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
                "/api/v1/operator/alerts",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["summary"] == {
                "total_active": 5,
                "highest_severity": "critical",
                "by_severity": {
                    "critical": 3,
                    "high": 2,
                    "medium": 0,
                    "low": 0,
                },
                "by_category": {
                    "incident": 1,
                    "kill_switch": 1,
                    "governance": 2,
                    "runtime": 1,
                },
            }
            assert payload["meta"]["acknowledgement_supported"] is False
            assert payload["meta"]["surfaces"]["alerts"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["incident_feed"]["status"] == "ok"
            assert payload["meta"]["surfaces"]["review_queue"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["runtime_roster"]["status"] == "ok"
            assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "degraded"

            alert_ids = [alert["alert_id"] for alert in payload["alerts"]]
            assert alert_ids == [
                "alert-runtime-runtime-042",
                "alert-kill-switch-state",
                "alert-incident-inc-001",
                "alert-approval-appr-001",
                "alert-governance-review-gov-review-001",
            ]
            assert payload["alerts"][0] == {
                "alert_id": "alert-runtime-runtime-042",
                "severity": "critical",
                "category": "runtime",
                "raised_at": "2026-04-18T06:10:00Z",
                "summary": "Runtime runtime-042 anomaly: drawdown is 0.125; fill rate dropped to 0.89.",
                "target_ref": {
                    "surface_id": "OC-04",
                    "label": "Open runtime state board",
                    "href": "/operator/runtime-state",
                    "target_id": "runtime-042",
                },
            }
            assert payload["alerts"][1]["target_ref"]["surface_id"] == "OC-03"
            assert payload["alerts"][1]["target_ref"]["href"] == "/operator/health-status"
            assert payload["alerts"][2]["target_ref"]["surface_id"] == "PKT-002"
            assert payload["alerts"][2]["target_ref"]["href"] == "/operator/incidents/inc-001"
            assert payload["alerts"][3]["target_ref"]["surface_id"] == "GV-02"
            assert payload["alerts"][3]["target_ref"]["href"] == "/governance-approval-queue"
            assert payload["alerts"][4]["target_ref"]["surface_id"] == "PKT-001"
            assert payload["alerts"][4]["target_ref"]["href"] == "/governance-review-queue"
        finally:
            bff_main.read_store = original_store


def test_pkt012_alerts_rail_returns_unavailable_when_all_sources_are_missing() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
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
                "/api/v1/operator/alerts",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["alerts"] == []
            assert payload["summary"]["total_active"] == 0
            assert payload["meta"]["surfaces"]["alerts"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["incident_feed"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["review_queue"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["runtime_roster"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store


def test_alt001_bff_alerts_endpoint_returns_operator_alert_projection_and_detail() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        store.list_incidents = lambda **kwargs: [
            {
                "incident_id": "inc-alt-001",
                "title": "Management alert live data",
                "severity": "high",
                "status": "open",
                "created_at": "2026-05-16T05:30:00Z",
            }
        ]
        store.list_governance_review_queue_items = lambda **kwargs: []
        store.list_approval_queue_items = lambda **kwargs: []
        store.get_kill_switch_status = lambda: {
            "active": False,
            "status": "released",
            "safe_mode_status": "off",
        }
        store.list_runtime_bindings = lambda: []
        store.get_telemetry_summary = lambda runtime_id: None
        store.dataset_source = lambda dataset: {
            "incidents": "service_store",
            "governance_review_queue_items": "service_store",
            "approval_queue_items": "service_store",
            "kill_switch": "service_store",
            "runtime_bindings": "canonical",
            "telemetry_summaries": "canonical",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/bff/alerts",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["summary"]["total_active"] == 1
            assert payload["summary"]["highest_severity"] == "critical"
            assert payload["meta"]["surfaces"]["alerts"]["status"] == "ok"
            alert = payload["alerts"][0]
            assert alert == {
                "alert_id": "alert-incident-inc-alt-001",
                "severity": "critical",
                "category": "incident",
                "raised_at": "2026-05-16T05:30:00Z",
                "summary": "Active incident: Management alert live data.",
                "target_ref": {
                    "surface_id": "PKT-002",
                    "label": "Open incident response",
                    "href": "/operator/incidents/inc-alt-001",
                    "target_id": "inc-alt-001",
                },
            }

            detail = client.get(
                "/bff/alerts/alert-incident-inc-alt-001",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert detail.status_code == 200, detail.text
            detail_payload = detail.json()
            assert detail_payload["data"] == alert
            assert detail_payload["meta"]["surfaces"]["alerts"]["status"] == "ok"
        finally:
            bff_main.read_store = original_store
