from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_HEADERS = {"Authorization": "Bearer op-b3:operator,reviewer"}

_COCKPIT_FUNCS = None


def _get_cockpit_funcs():
    global _COCKPIT_FUNCS
    if _COCKPIT_FUNCS is None:
        tree = ast.parse(Path("services/control-plane/bff/main.py").read_text(encoding="utf-8"))
        target_names = {
            "_mgmt_nl_collect_context",
            "_mgmt_nl_filter_tenant_records",
            "_mgmt_nl_record_matches_tenant",
            "_mgmt_nl_record_tenant_ids",
            "_mgmt_nl_scope_values",
            "_mgmt_nl_trading_pulse_snippet",
            "_mgmt_nl_scoped_runtime_rows",
            "_project_operator_runtime_state_row",
            "_project_runtime_state_telemetry_summary",
            "_project_runtime_state_monitoring_session",
            "_runtime_state_monitoring_terminal_reason",
            "_project_runtime_state_latest_rollback",
            "_runtime_state_row_health_check",
            "_runtime_state_monitoring_health_check",
            "_derive_runtime_state_row_health",
            "_derive_runtime_state_last_updated_at",
            "_highest_ranked_value",
            "_max_alert_severity",
            "_deployment_review_href",
            "_management_number",
            "_management_avg",
            "_management_count_by",
            "_mgmt_nl_add_record_entities",
            "_mgmt_nl_add_entity",
            "_build_alert_summary",
            "_mgmt_nl_merge_owner_observations",
            "_mgmt_nl_payload_surface_observations",
            "_mgmt_nl_surface_owner_observation",
        }
        _COCKPIT_FUNCS = [
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in target_names
        ]
    return _COCKPIT_FUNCS


class _MockContextHost:
    def __init__(self):
        self.store = None
        self._management_ai_context_service = None
        self._build_operator_alerts_payload = lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}}
        self._human_inbox_payload = lambda *args, **kwargs: {"data": {"items": []}, "meta": {"surfaces": {}}}
        self._build_management_anomalies_payload = lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}}

    def _mgmt_nl_collect_context(self, focus, snapshot_at, tenant_id=None):
        funcs = _get_cockpit_funcs()
        ns = dict(__import__("typing").__dict__)
        ns.update({
            "re": re,
            "json": json,
            "read_store": self.store,
            "_ALERT_SEVERITY_ORDER": {"critical": 4, "high": 3, "medium": 2, "low": 1},
            "_ALERT_CATEGORY_ORDER": {"incident": 4, "kill_switch": 3, "governance": 2, "runtime": 1},
            "_management_ai_context_service": self._management_ai_context_service,
            "_build_operator_alerts_payload": self._build_operator_alerts_payload,
            "_human_inbox_payload": self._human_inbox_payload,
            "_build_management_anomalies_payload": self._build_management_anomalies_payload,
            "utc_now": lambda: snapshot_at,
        })
        mod = ast.Module(body=funcs, type_ignores=[])
        exec(compile(mod, "main_cockpit.py", "exec"), ns)
        return ns["_mgmt_nl_collect_context"](focus, snapshot_at, tenant_id)


mock_context_host = _MockContextHost()


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
            "priority": 2,
            "sla_expires_at": "2026-05-23T12:00:00Z",
            "submitted_at": "2026-05-23T08:00:00Z",
            "summary": "Review pending deployment plan",
            "actions": ["approve", "reject"],
            "stage": "paper",
        }
    ]
    store.list_approval_queue_items = lambda **kwargs: [
        {
            "item_id": "approval-b3-001",
            "decision_id": "approval-b3-001",
            "id": "approval-b3-001",
            "item_type": "Action",
            "risk_level": "high",
            "status": "pending",
            "priority": 1,
            "sla_expires_at": "2026-05-23T10:00:00Z",
            "submitted_at": "2026-05-23T07:30:00Z",
            "summary": "Approve risk threshold expansion",
            "actions": ["approve", "reject"],
        }
    ]
    store.get_kill_switch = lambda **kwargs: {
        "state": "active",
        "engaged": False,
        "mode": "normal",
        "actor": "system",
        "reason": "nominal",
        "updated_at": "2026-05-23T08:00:00Z",
    }
    store.list_runtime_bindings = lambda **kwargs: [
        {
            "binding_id": "binding-b3-001",
            "runtime_id": "runtime-b3-001",
            "persona_id": "persona-b3-001",
            "stage": "paper",
            "deployment_stage": "paper",
            "status": "active",
            "capital_pool_id": "pool-b3-001",
            "capital_allocated": 50000.0,
            "leverage_limit": 2.0,
            "created_at": "2026-05-23T08:00:00Z",
        }
    ]
    store.get_telemetry_summary = lambda runtime_id: {
        "runtime_id": runtime_id,
        "window": "24h",
        "collected_at": "2026-05-23T08:05:00Z",
        "pnl": 0.42,
        "drawdown": 0.11,
        "sharpe_ratio": 1.85,
        "fill_rate": 0.98,
        "avg_slippage_bps": 2.1,
        "total_trades": 120,
        "metrics": {
            "pnl": 0.42,
            "drawdown": 0.11,
            "sharpe_ratio": 1.85,
            "fill_rate": 0.98,
            "avg_slippage_bps": 2.1,
            "total_trades": 120,
        },
    }
    store.list_telemetry_summaries = lambda: [
        store.get_telemetry_summary("runtime-b3-001")
    ]
    store.get_paper_runtime_monitoring_session = lambda **kwargs: {
        "session_id": "session-b3-001",
        "runtime_id": "runtime-b3-001",
        "binding_id": "binding-b3-001",
        "status": "running",
        "active": True,
        "started_at": "2026-05-23T08:00:00Z",
        "drift_detected": False,
    }
    store.list_paper_runtime_monitoring_sessions = lambda: [
        store.get_paper_runtime_monitoring_session()
    ]
    store.get_paper_live_drift_report = lambda **kwargs: {
        "report_id": "drift-b3-001",
        "runtime_id": "runtime-b3-001",
        "status": "watch",
        "drift_detected": True,
        "generated_at": "2026-05-23T08:05:00Z",
        "paper_live_drift": {"available": True},
        "threshold_evaluation": {"overall_status": "watch"},
    }
    store.list_paper_live_drift_reports = lambda: [
        store.get_paper_live_drift_report()
    ]
    store.get_rollbacks = lambda runtime_id: [
        {
            "rollback_id": "rb-b3-001",
            "runtime_id": runtime_id,
            "status": "completed",
            "initiated_at": "2026-05-23T07:45:00Z",
            "completed_at": "2026-05-23T07:50:00Z",
        }
    ]
    store.list_v5_interventions = lambda **kwargs: [
        {
            "id": "intervention-b3-001",
            "title": "Manual circuit trip",
            "status": "open",
            "severity": "medium",
            "created_at": "2026-05-23T08:03:00Z",
            "runtime_id": "runtime-b3-001",
        }
    ]
    store.list_sentinel_findings = lambda **kwargs: [
        {
            "id": "sentinel-b3-001",
            "title": "Telemetry heartbeats delayed",
            "severity": "medium",
            "state": "active",
            "detected_at": "2026-05-23T08:02:00Z",
            "runtime_id": "runtime-b3-001",
        }
    ]
    store.dataset_source = lambda dataset: {
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

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    return TestClient(app)


def test_bff_management_cockpit_composes_required_sections() -> None:
    with tempfile.TemporaryDirectory() as td:
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


def test_bff_management_cockpit_requires_read_auth() -> None:
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: create_in_memory_read_surface_ports()))
    client = TestClient(app, raise_server_exceptions=False)
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
    monkeypatch.setattr(mock_context_host, "store", store)
    monkeypatch.setattr(mock_context_host, "_management_ai_context_service", ManagementService(read_store=store))
    monkeypatch.setattr(mock_context_host, "_build_operator_alerts_payload", lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}})
    monkeypatch.setattr(mock_context_host, "_human_inbox_payload", lambda *args, **kwargs: {"data": {"items": []}, "meta": {"surfaces": {}}})
    monkeypatch.setattr(mock_context_host, "_build_management_anomalies_payload", lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}})


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
    monkeypatch.setattr(mock_context_host, helper, lambda *args, **kwargs: payload)

    result = mock_context_host._mgmt_nl_collect_context("cockpit", "2026-09-08T18:00:00Z", "tenant-a")

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
    monkeypatch.setattr(mock_context_host.store, "get_rollbacks", lambda _runtime_id: [row])

    result = mock_context_host._mgmt_nl_collect_context(focus, "2026-09-08T18:00:00Z", "tenant-a")

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
    monkeypatch.setattr(mock_context_host, "_build_operator_alerts_payload", lambda _snapshot_at: payload)

    result = mock_context_host._mgmt_nl_collect_context("cockpit", "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"]["management_cockpit"]
    observation = next(
        obs
        for obs in surface["owner_observation"]["contributing_observations"]
        if obs.get("owner") == "incident-owner"
    )
    assert observation[field] == value

