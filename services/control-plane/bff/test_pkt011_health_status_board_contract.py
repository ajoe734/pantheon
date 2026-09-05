from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt011_health_status_board_returns_contract_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: [
            {
                "id": "runtime-042",
                "runtime_id": "runtime-042",
                "deployment_stage": "paper",
                "status": "idle",
                "plan_id": "plan-F-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
            }
        ]
        store.get_telemetry_summary = lambda runtime_id: {
            "runtime_id": "runtime-042",
            "window": "1h",
            "pnl": -0.12,
            "drawdown": 0.125,
            "sharpe_ratio": -0.8,
            "fill_rate": 0.94,
            "avg_slippage_bps": 3.2,
            "total_trades": 47,
            "collected_at": "2026-04-10T15:00:00Z",
        } if runtime_id == "runtime-042" else None
        store.list_incidents = lambda **kwargs: [
            {
                "incident_id": "inc-001",
                "severity": "high",
                "status": "open",
            },
            {
                "incident_id": "inc-002",
                "severity": "low",
                "status": "resolved",
            },
        ]
        store.list_governance_review_queue_items = lambda **kwargs: [
            {"item_id": "gov-001", "risk_level": "medium"}
        ]
        store.list_approval_queue_items = lambda **kwargs: [
            {"decision_id": "appr-001", "risk_level": "high"}
        ]
        store.get_kill_switch_status = lambda: {
            "active": False,
            "status": "armed",
            "safe_mode_status": "off",
            "last_confirmed_at": "2026-04-18T06:00:00Z",
            "last_triggered_at": None,
            "active_commands": [],
            "secondary_path_available": True,
        }
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "canonical",
            "telemetry_summaries": "local_snapshot",
            "incidents": "service_store",
            "governance_review_queue_items": "local_snapshot",
            "approval_queue_items": "local_snapshot",
            "kill_switch": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/health-status",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["overall_status"] == "degraded"
            assert payload["headline"] == "Some services degraded"
            assert payload["group_counts"] == {"ok": 2, "degraded": 3, "unavailable": 0}
            assert payload["safe_mode_state"] == {
                "status": "off",
                "kill_switch_status": "armed",
                "active": False,
                "last_confirmed_at": "2026-04-18T06:00:00Z",
                "last_triggered_at": None,
                "secondary_path_available": True,
            }
            assert payload["secondary_control_path"]["mode"] == "advisory"
            assert payload["secondary_control_path"]["targets"][0]["command"] == "pantheon admin health"

            groups = {group["group_id"]: group for group in payload["groups"]}
            assert groups["runtime"]["status"] == "ok"
            assert groups["runtime"]["details"]["total_runtime_count"] == 1
            assert groups["runtime"]["target_refs"] == [
                {"label": "Runtime State Board", "href": "/operator/runtime-state"}
            ]
            assert groups["telemetry"]["status"] == "degraded"
            assert groups["telemetry"]["details"]["covered_runtime_count"] == 1
            assert groups["telemetry"]["target_refs"] == [
                {"label": "Runtime State Board", "href": "/operator/runtime-state"}
            ]
            assert groups["incident"]["details"]["active_incident_count"] == 1
            assert groups["incident"]["details"]["highest_severity"] == "sev1"
            assert groups["incident"]["target_refs"] == [
                {"label": "Incident Home", "href": "/operator/incidents"}
            ]
            assert groups["governance"]["details"]["total_pending_items"] == 2
            assert groups["governance"]["details"]["highest_risk_level"] == "high"
            assert groups["governance"]["target_refs"] == [
                {"label": "Governance Review Queue", "href": "/governance-review-queue"},
                {"label": "Governance Approval Queue", "href": "/governance-approval-queue"},
            ]
            assert groups["kill_switch"]["details"]["safe_mode_status"] == "off"
            assert groups["kill_switch"]["target_refs"] == [
                {"label": "Health Status Board", "href": "/operator/health-status"}
            ]

            assert payload["meta"]["surfaces"]["health_status"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["runtime"]["status"] == "ok"
            assert payload["meta"]["surfaces"]["telemetry"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["incident"]["status"] == "ok"
            assert payload["meta"]["surfaces"]["governance"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store


def test_pkt011_health_status_board_returns_unavailable_when_primary_surfaces_missing() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: []
        store.get_telemetry_summary = lambda runtime_id: None
        store.list_incidents = lambda **kwargs: []
        store.list_governance_review_queue_items = lambda **kwargs: []
        store.list_approval_queue_items = lambda **kwargs: []
        store.dataset_source = lambda dataset: "missing"
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/health-status",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["overall_status"] == "unavailable"
            assert payload["headline"] == "Control plane health unavailable"
            assert payload["group_counts"] == {"ok": 0, "degraded": 0, "unavailable": 5}
            assert payload["safe_mode_state"]["status"] is None
            assert payload["secondary_control_path"]["mode"] == "recommended"
            assert len(payload["secondary_control_path"]["targets"]) == 6
            assert all(group["status"] == "unavailable" for group in payload["groups"])
            assert payload["meta"]["surfaces"]["health_status"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store


def test_pkt011_health_status_board_escalates_secondary_path_when_safe_mode_active() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: [
            {
                "id": "runtime-042",
                "runtime_id": "runtime-042",
                "deployment_stage": "live",
                "status": "running",
            }
        ]
        store.get_telemetry_summary = lambda runtime_id: {
            "runtime_id": runtime_id,
            "window": "1h",
            "pnl": 0.15,
            "drawdown": 0.02,
            "sharpe_ratio": 2.0,
            "fill_rate": 0.99,
            "avg_slippage_bps": 1.0,
            "total_trades": 21,
            "collected_at": "2026-04-18T06:10:00Z",
        }
        store.list_incidents = lambda **kwargs: []
        store.list_governance_review_queue_items = lambda **kwargs: []
        store.list_approval_queue_items = lambda **kwargs: []
        store.get_kill_switch_status = lambda: {
            "active": True,
            "status": "triggered",
            "safe_mode_status": "soft",
            "last_confirmed_at": "2026-04-18T06:12:00Z",
            "last_triggered_at": "2026-04-18T06:11:00Z",
            "active_commands": ["safe-mode-001"],
            "secondary_path_available": True,
        }
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "canonical",
            "telemetry_summaries": "service_store",
            "incidents": "service_store",
            "governance_review_queue_items": "service_store",
            "approval_queue_items": "service_store",
            "kill_switch": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/health-status",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["headline"] == "Safe mode active"
            assert payload["safe_mode_state"]["status"] == "soft"
            assert payload["safe_mode_state"]["kill_switch_status"] == "triggered"
            assert payload["secondary_control_path"]["mode"] == "recommended"
            commands = [target["command"] for target in payload["secondary_control_path"]["targets"]]
            assert "pantheon admin runtime rollback --runtime={runtime_id} --target={version}" in commands
        finally:
            bff_main.read_store = original_store
