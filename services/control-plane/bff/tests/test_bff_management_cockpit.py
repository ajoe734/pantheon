from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from management_read_models.service import ManagementService
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


@pytest.fixture
def _healthy_cockpit_context(monkeypatch):
    """MGMT-READ-001 seventh review: a healthy runtime/telemetry read with
    every alerts/human-inbox/anomalies payload's own meta.surfaces empty, so
    the only failure signal in each parametrized case below comes from the
    single contributing surface under test."""
    binding = dict(
        runtime_id="r1",
        tenant_id="tenant-a",
        owner="runtime-owner",
        status="ok",
        source_kind="live",
        source_version="rv1",
    )
    telemetry = dict(
        runtime_id="r1", owner="telemetry-owner", status="ok", source_kind="live", source_version="tv1"
    )
    store = SimpleNamespace(
        list_runtime_bindings=lambda: [binding],
        get_telemetry_summary=lambda _runtime_id: telemetry,
        get_paper_runtime_monitoring_session=lambda **_kwargs: None,
        get_rollbacks=lambda _runtime_id: [],
    )
    monkeypatch.setattr(bff_main, "read_store", store)
    monkeypatch.setattr(bff_main, "_management_ai_context_service", ManagementService(read_store=store))
    monkeypatch.setattr(bff_main, "_build_operator_alerts_payload", lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}})
    monkeypatch.setattr(bff_main, "_human_inbox_payload", lambda *args, **kwargs: {"data": {"items": []}, "meta": {"surfaces": {}}})
    monkeypatch.setattr(bff_main, "_build_management_anomalies_payload", lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}})


@pytest.mark.parametrize(
    "helper,rows_key,surface_key",
    [
        ("_build_operator_alerts_payload", "alerts", "incident_feed"),
        ("_human_inbox_payload", "data", "approval_queue"),
        ("_build_management_anomalies_payload", "items", "sentinel_findings"),
    ],
)
def test_cockpit_preserves_non_runtime_owner_unavailability(
    _healthy_cockpit_context, monkeypatch, helper, rows_key, surface_key
) -> None:
    """MGMT-READ-001 seventh review: management_cockpit previously merged
    only runtime/telemetry owner observations, so an unavailable
    incident_feed/approval_queue/sentinel_findings surface was reported as
    status=ok while its failed owner and reason were silently dropped."""
    failure = dict(
        status="unavailable",
        source="missing",
        owner=surface_key + "-owner",
        message=surface_key + " offline",
        source_version="v-failed",
    )
    payload = {
        rows_key: {"items": []} if rows_key == "data" else [],
        "meta": {"surfaces": {surface_key: failure}},
    }
    monkeypatch.setattr(bff_main, helper, lambda *args, **kwargs: payload)

    result = bff_main._mgmt_nl_collect_context("cockpit", "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"]["management_cockpit"]
    assert surface["status"] != "ok", surface
    assert surface_key + " offline" in json.dumps(surface), surface


@pytest.mark.parametrize(
    "focus,surface_key",
    [
        ("trading_pulse", "management_trading_pulse"),
        ("cockpit", "management_cockpit"),
    ],
)
def test_nonraising_unavailable_rollback_owner_is_preserved(
    _healthy_cockpit_context, monkeypatch, focus, surface_key
) -> None:
    """MGMT-READ-001 eighth review: get_context_rollbacks previously
    returned status=ok/source_kind=live after any non-raising read,
    discarding a rollback record that itself reported
    status=unavailable/owner=rollback-owner. Both trading_pulse and cockpit
    surfaces must surface that failed owner's full provenance instead of
    reporting ok."""
    row = dict(
        runtime_id="r1",
        id="rollback-1",
        owner="rollback-owner",
        status="unavailable",
        source_kind="unavailable",
        source_version="rollback-v7",
        observed_at="2026-09-08T18:00:00Z",
        correlation_id="rollback-correlation",
        degradation_reason="rollback owner offline",
    )
    monkeypatch.setattr(bff_main.read_store, "get_rollbacks", lambda _runtime_id: [row])

    result = bff_main._mgmt_nl_collect_context(focus, "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"][surface_key]
    assert surface["status"] == "unavailable", surface
    encoded = json.dumps(surface)
    for value in ("rollback-owner", "rollback-v7", "rollback-correlation", "rollback owner offline"):
        assert value in encoded, surface


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_version", "incident-v7"),
        ("observed_at", "2026-09-08T18:00:00Z"),
        ("correlation_id", "incident-correlation"),
    ],
)
def test_cockpit_surface_owner_observation_preserves_provenance_fields(
    _healthy_cockpit_context, monkeypatch, field, value
) -> None:
    """MGMT-READ-001 eighth review: _mgmt_nl_surface_owner_observation
    reconstructed a partial untyped dict that dropped source_version,
    observed_at and correlation_id from an incident/approval/sentinel
    surface observation. Every contributing owner observation must carry
    the full ManagementObservation provenance fields through cockpit
    conversion."""
    row = dict(
        status="unavailable",
        source="missing",
        owner="incident-owner",
        message="incident owner offline",
        source_version="incident-v7",
        observed_at="2026-09-08T18:00:00Z",
        correlation_id="incident-correlation",
    )
    payload = {"alerts": [], "meta": {"surfaces": {"incident_feed": row}}}
    monkeypatch.setattr(bff_main, "_build_operator_alerts_payload", lambda _snapshot_at: payload)

    result = bff_main._mgmt_nl_collect_context("cockpit", "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"]["management_cockpit"]
    observation = next(
        obs
        for obs in surface["owner_observation"]["contributing_observations"]
        if obs["subject_type"] == "incident_feed"
    )
    assert observation.get(field) == value, observation
