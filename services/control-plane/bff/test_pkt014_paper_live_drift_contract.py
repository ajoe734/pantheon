from __future__ import annotations

from typing import Any
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.runtime.router import create_runtime_router


OPERATOR_TOKEN = "Bearer op-2:operator"


def _make_client(store: Any) -> TestClient:
    def _dataset_surface_status(dataset: str, snapshot_at: Any = None, has_data: Any = None, missing_message: Any = None) -> dict[str, Any]:
        src = store.dataset_source(dataset) if hasattr(store, "dataset_source") else "canonical"
        if src == "missing" or has_data is False:
            return {"status": "unavailable", "source": src}
        elif src == "local_snapshot":
            return {"status": "degraded", "source": "local_snapshot"}
        return {"status": "ok", "source": src}

    def _aggregate_group_surface(key: str, surfaces: list[dict[str, Any]], snapshot_at: Any = None, unavailable_message: Any = None, degraded_message: Any = None) -> dict[str, Any]:
        statuses = [s.get("status", "ok") for s in surfaces]
        if all(s == "ok" for s in statuses):
            return {"status": "ok", "source": "bff_composed"}
        if all(s == "unavailable" for s in statuses):
            return {"status": "unavailable", "source": "bff_composed", "message": unavailable_message}
        return {"status": "degraded", "source": "bff_composed", "message": degraded_message}

    deps = {
        "utc_now": lambda: "2026-04-18T06:10:00Z",
        "_extract_identity": lambda auth: {"roles": ["operator"]},
        "_require_read_role": lambda id: None,
        "_dataset_surface_status": _dataset_surface_status,
        "_aggregate_group_surface": _aggregate_group_surface,
        "_snapshot_meta": lambda s: {"snapshot_at": s},
        "_alert_target_ref": lambda surface_id, label, href, target_id=None: {
            "surface_id": surface_id, "label": label, "href": href, **({"target_id": target_id} if target_id else {})
        },
        "_deployment_review_href": lambda p: f"/operator/deployment-review?plan={p}",
        "_GOVERNANCE_APPROVAL_QUEUE_ROUTE": "/governance-approval-queue",
    }
    app = FastAPI()
    app.include_router(create_runtime_router(read_surface=store, dependencies=deps))
    return TestClient(app)


def test_pkt014_paper_live_drift_returns_backend_owned_comparison_payload() -> None:
    store = create_in_memory_read_surface_ports()
    store.get_runtime_binding_by_runtime_id = lambda runtime_id: {
        "id": "runtime-042",
        "runtime_id": "runtime-042",
        "deployment_stage": "live",
        "status": "running",
        "plan_id": "plan-F-042",
        "artifact_id": "artifact-042",
        "artifact_version": "v2.1.0",
    } if runtime_id == "runtime-042" else None
    store.get_paper_live_drift_report = lambda runtime_id: {
        "runtime_id": "runtime-042",
        "artifact_id": "artifact-042",
        "artifact_version": "v2.1.0",
        "plan_id": "plan-F-042",
        "paper_baseline": {
            "captured_at": "2026-04-09T16:00:00Z",
            "deployment_stage": "paper",
            "window": "24h",
            "metrics": {
                "pnl": -0.04,
                "max_drawdown": 0.06,
                "fill_rate": 0.97,
                "avg_slippage_bps": 2.4,
                "turnover": 0.32,
            },
        },
        "observed_state": {
            "deployment_stage": "live",
            "runtime_status": "running",
            "observed_at": "2026-04-10T15:00:00Z",
            "metrics": {
                "pnl": -0.12,
                "drawdown": 0.125,
                "fill_rate": 0.94,
                "avg_slippage_bps": 3.2,
                "turnover": 0.41,
            },
        },
        "drift_groups": [
            {
                "group_id": "exposure",
                "label": "Exposure",
                "status": "breached",
                "metrics": [
                    {
                        "metric_id": "max_drawdown",
                        "label": "Max drawdown",
                        "baseline_value": 0.06,
                        "observed_value": 0.125,
                        "delta": 0.065,
                        "threshold": "<= 0.08",
                        "status": "breached",
                        "unit": "ratio",
                    }
                ],
            }
        ],
        "threshold_evaluation": {
            "overall_status": "breached",
            "summary": "Observed live metrics exceed the published paper baseline envelope; operator review is required.",
            "breached_metric_ids": ["max_drawdown"],
        },
        "evidence_refs": [
            {
                "ref_id": "approval-042",
                "type": "ApprovalDecision",
                "href": "/api/v1/approval-decisions/approval-042",
            }
        ],
        "recommended_actions": [
            {
                "action_id": "open-deployment-review",
                "label": "Open deployment review",
                "reason": "Re-verify promotion assumptions against observed live drift.",
                "target_ref": {
                    "surface_id": "PKT-001",
                    "label": "Open deployment review",
                    "href": "/api/v1/operator/deployment-review/plan-F-042",
                    "target_id": "plan-F-042",
                },
            }
        ],
    } if runtime_id == "runtime-042" else None
    store.get_deployment_plan = lambda plan_id: {
        "plan_id": "plan-F-042",
        "approval_decision_id": "approval-042",
    } if plan_id == "plan-F-042" else None
    store.get_approval_decision = lambda decision_id: {
        "id": "approval-042",
        "outcome": "approved",
    } if decision_id == "approval-042" else None
    store.get_telemetry_summary = lambda runtime_id: {
        "runtime_id": "runtime-042",
        "drawdown": 0.125,
        "fill_rate": 0.94,
        "avg_slippage_bps": 3.2,
    } if runtime_id == "runtime-042" else None
    store.get_telemetry_performance = lambda artifact_id: {
        "artifact_id": "artifact-042",
        "summary": {
            "max_drawdown": 0.06,
            "fill_rate": 0.97,
            "avg_slippage_bps": 2.4,
        },
    } if artifact_id == "artifact-042" else None
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-20260410-001",
            "runtime_id": "runtime-042",
            "status": "open",
        }
    ]
    store.get_evolution_decisions_by_incident = lambda incident_id: [
        {"decision_id": "evo-dec-001"}
    ] if incident_id == "inc-20260410-001" else []
    store.dataset_source = lambda dataset: {
        "paper_live_drift_reports": "local_snapshot",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "telemetry_performance": "service_store",
        "approval_decisions": "canonical",
        "incidents": "service_store",
        "evolution_decisions": "service_store",
    }.get(dataset, "missing")
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/paper-live-drift/runtime-042",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["runtime_id"] == "runtime-042"
    assert payload["artifact_ref"] == {
        "artifact_id": "artifact-042",
        "artifact_version": "v2.1.0",
    }
    assert payload["plan_ref"] == {
        "plan_id": "plan-F-042",
        "href": "/operator/deployment-plans/plan-F-042",
    }
    assert payload["paper_baseline"]["deployment_stage"] == "paper"
    assert payload["observed_state"]["deployment_stage"] == "live"
    assert payload["threshold_evaluation"] == {
        "overall_status": "breached",
        "summary": "Observed live metrics exceed the published paper baseline envelope; operator review is required.",
        "breached_metric_ids": ["max_drawdown"],
    }
    assert payload["recommended_actions"][0]["target_ref"]["surface_id"] == "PKT-001"
    assert payload["recommended_actions"][0]["target_ref"]["href"] == "/operator/deployment-review?plan=plan-F-042"
    assert payload["evidence_refs"][0]["href"] == "/governance-approval-queue"
    assert payload["meta"]["surfaces"]["paper_live_drift"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["drift_report"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["runtime_binding"]["status"] == "ok"
    assert payload["meta"]["supporting_counts"] == {
        "active_incident_count": 1,
        "evolution_decision_count": 1,
    }


def test_pkt014_paper_live_drift_returns_unavailable_payload_when_report_missing() -> None:
    store = create_in_memory_read_surface_ports()
    store.get_runtime_binding_by_runtime_id = lambda runtime_id: {
        "id": "runtime-042",
        "runtime_id": "runtime-042",
        "deployment_stage": "live",
        "status": "running",
        "plan_id": "plan-F-042",
    } if runtime_id == "runtime-042" else None
    store.get_paper_live_drift_report = lambda runtime_id: None
    store.get_deployment_plan = lambda plan_id: {
        "plan_id": "plan-F-042",
        "approval_decision_id": None,
    } if plan_id == "plan-F-042" else None
    store.get_approval_decision = lambda decision_id: None
    store.get_telemetry_summary = lambda runtime_id: None
    store.get_telemetry_performance = lambda artifact_id: None
    store.list_incidents = lambda **kwargs: []
    store.get_evolution_decisions_by_incident = lambda incident_id: []
    store.dataset_source = lambda dataset: {
        "paper_live_drift_reports": "missing",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "missing",
        "telemetry_performance": "missing",
        "approval_decisions": "missing",
        "incidents": "service_store",
        "evolution_decisions": "service_store",
    }.get(dataset, "missing")
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/paper-live-drift/runtime-042",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["paper_baseline"] is None
    assert payload["observed_state"] is None
    assert payload["drift_groups"] == []
    assert payload["threshold_evaluation"] == {
        "overall_status": "unavailable",
        "summary": "Paper/live drift report unavailable for this runtime.",
        "breached_metric_ids": [],
    }
    assert payload["meta"]["surfaces"]["paper_live_drift"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["drift_report"]["status"] == "unavailable"
