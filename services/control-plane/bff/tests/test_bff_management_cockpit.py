from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_in_memory_read_surface_ports


OPERATOR_HEADERS = {"Authorization": "Bearer op-b3:operator,reviewer"}


def _seeded_client(td: str) -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-b3-001",
            "title": "Runtime drawdown breach",
            "severity": "high",
            "status": "open",
            "created_at": "2026-05-23T08:01:00Z",
            "runtime_id": "runtime-b3-001",
        }
    ]
    store.list_governance_review_queue_items = lambda **kwargs: [
        {
            "item_id": "review-b3-001",
            "item_type": "DeploymentPlan",
            "risk_level": "medium",
            "status": "pending",
            "submitted_at": "2026-05-23T08:02:00Z",
        }
    ]
    store.list_approval_queue_items = lambda **kwargs: [
        {
            "decision_id": "approval-b3-001",
            "decision_type": "RuntimeBinding",
            "risk_level": "high",
            "decision_state": "pending",
            "submitted_at": "2026-05-23T08:03:00Z",
        }
    ]
    store.get_kill_switch_status = lambda: {
        "active": False,
        "status": "armed",
        "safe_mode_status": "off",
        "last_confirmed_at": "2026-05-23T08:00:00Z",
        "last_triggered_at": None,
        "active_commands": [],
        "secondary_path_available": True,
    }
    store.list_runtime_bindings = lambda **kwargs: [
        {
            "id": "binding-b3-001",
            "binding_id": "binding-b3-001",
            "runtime_id": "runtime-b3-001",
            "deployment_stage": "paper",
            "status": "running",
            "plan_id": "plan-b3-001",
            "artifact_id": "artifact-b3-001",
            "artifact_version": "v1",
        }
    ]
    telemetry_summary = {
        "runtime_id": "runtime-b3-001",
        "runtime_binding_id": "binding-b3-001",
        "deployment_stage": "paper",
        "state": "active",
        "window": "1h",
        "pnl": 0.42,
        "drawdown": 0.11,
        "sharpe_ratio": 1.7,
        "fill_rate": 0.88,
        "avg_slippage_bps": 4.8,
        "total_trades": 31,
        "collected_at": "2026-05-23T08:10:00Z",
        "last_heartbeat_at": "2026-05-23T08:10:00Z",
        "last_event_at": "2026-05-23T08:09:00Z",
    }
    store.get_telemetry_summary = lambda runtime_id: (
        telemetry_summary if runtime_id == "runtime-b3-001" else None
    )
    store.list_telemetry_summaries = lambda: [telemetry_summary]
    drift_report = {
        "runtime_id": "runtime-b3-001",
        "artifact_id": "artifact-b3-001",
        "paper_baseline": {
            "captured_at": "2026-05-23T07:00:00Z",
            "deployment_stage": "paper",
            "window": "1h",
            "metrics": {
                "pnl": 0.36,
                "drawdown": 0.09,
                "fill_rate": 0.9,
                "avg_slippage_bps": 4.1,
            },
        },
        "observed_state": {
            "deployment_stage": "paper",
            "runtime_status": "running",
            "observed_at": "2026-05-23T08:10:00Z",
            "metrics": {
                "pnl": 0.42,
                "drawdown": 0.11,
                "fill_rate": 0.88,
                "avg_slippage_bps": 4.8,
            },
        },
        "drift_groups": [
            {
                "group_id": "performance",
                "label": "Performance",
                "status": "watch",
                "metrics": [
                    {
                        "metric_id": "drawdown",
                        "baseline_value": 0.09,
                        "observed_value": 0.11,
                        "delta": 0.02,
                        "status": "watch",
                    }
                ],
            }
        ],
        "threshold_evaluation": {
            "overall_status": "watch",
            "summary": "Drawdown drift is inside the watch band.",
            "breached_metric_ids": [],
        },
    }
    store.get_paper_live_drift_report = lambda runtime_id: (
        drift_report if runtime_id == "runtime-b3-001" else None
    )
    store.list_paper_live_drift_reports = lambda: [drift_report]
    monitoring_session = {
        "session_id": "monitor-b3-001",
        "binding_id": "binding-b3-001",
        "runtime_binding_id": "binding-b3-001",
        "runtime_id": "runtime-b3-001",
        "deployment_stage": "paper",
        "status": "active",
        "active": True,
        "started_at": "2026-05-23T07:30:00Z",
        "last_heartbeat_at": "2026-05-23T08:10:00Z",
    }
    store.list_paper_runtime_monitoring_sessions = lambda: [monitoring_session]
    store.get_rollbacks = lambda runtime_id: []
    store.list_sentinel_findings = lambda **kwargs: (
        True,
        [
            {
                "id": "finding-b3-001",
                "kind": "risk_breach",
                "severity": "high",
                "status": "open",
                "title": "Runtime risk threshold breach",
                "runtime_id": "runtime-b3-001",
                "created_at": "2026-05-23T08:04:00Z",
            }
        ],
    )
    store.list_v5_interventions = lambda status=None, kind=None: [
        {
            "intervention_id": "intv-b3-001",
            "kind": "risk_breach",
            "status": "pending",
            "severity": "high",
            "target_type": "Runtime",
            "target_id": "runtime-b3-001",
            "triggered_at": "2026-05-23T08:05:00Z",
        }
    ]
    store.dataset_source = lambda dataset, **kwargs: {
        "incidents": "service_store",
        "governance_review_queue_items": "service_store",
        "approval_queue_items": "service_store",
        "kill_switch": "service_store",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "paper_runtime_monitoring_sessions": "service_store",
        "paper_live_drift_reports": "service_store",
        "rollbacks": "service_store",
        "v5_interventions": "service_store",
        "sentinel_findings": "service_store",
    }.get(dataset, "missing")
    bff_main.read_store = store
    return TestClient(bff_main.app)


def test_bff_management_cockpit_composes_required_sections() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _seeded_client(td)
            response = client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
            assert response.status_code == 200, response.text
            payload = response.json()
            data = payload["data"]

            assert data["id"] == "management-cockpit"
            assert set(payload) == {"data", "meta"}
            assert "operator_home" not in payload
            assert "runtime_health" not in payload
            assert "operatorHome" not in data
            assert "runtimeHealth" not in data
            assert data["alerts"]["summary"]["total_active"] >= 1
            inbox_summary = data["human_inbox"]["data"]["summary"]
            assert inbox_summary["total"] >= 4
            assert inbox_summary["governance_review_count"] == 1
            assert inbox_summary["approval_count"] == 1
            assert inbox_summary["intervention_count"] == 1
            assert inbox_summary["sentinel_finding_count"] == 1
            assert data["trading_pulse"]["summary"]["runtime_count"] == 1
            assert data["trading_pulse"]["summary"]["total_pnl"] == 0.42
            assert data["trading_pulse"]["summary"]["baseline_comparison_count"] == 1
            assert data["trading_pulse"]["rankings"][0]["runtime_id"] == "runtime-b3-001"
            assert (
                data["trading_pulse"]["baseline_comparisons"][0]["status"]
                == "watch"
            )
            assert data["anomalies"]["summary"]["total"] >= 2
            assert payload["meta"]["surfaces"]["management_cockpit"]["status"] in {
                "ok",
                "degraded",
            }
            assert "management_human_inbox" not in payload["meta"]["surfaces"]
            assert payload["meta"]["surfaces"]["human_inbox"]["status"] in {
                "ok",
                "degraded",
            }
            assert payload["meta"]["surfaces"]["trading_pulse"]["status"] == "ok"
        finally:
            bff_main.read_store = original_store


def test_bff_management_cockpit_requires_read_auth() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    response = client.get("/bff/management/cockpit")

    assert response.status_code == 401
