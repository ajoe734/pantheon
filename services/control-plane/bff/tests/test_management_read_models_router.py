"""Tests for Management System & Read Models Router (OPGAP-BE-MANAGEMENT-ROUTER-V2-20260830).

Verifies:
- All 17 Management system route decorators are registered with correct HTTP methods and paths
- Composed read model endpoints (formula-jobs, activity, paper-telemetry, postmortems) are preserved
- Route uniqueness and absence of collisions
- Full functional execution of all endpoints via TestClient
- Mock store integration, filtering, pagination, error handling, and degradation semantics
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

# Ensure bff root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from management_read_models.router import (
    create_management_read_models_router,
    create_management_router,
    _default_extract_identity,
)
from management_read_models.service import ManagementService


# ---------------------------------------------------------------------------
# Test Fixtures & Mock Stores
# ---------------------------------------------------------------------------

class MockManagementReadStore:
    """Mock store implementing reader interfaces across all Management surfaces."""

    def __init__(self) -> None:
        self.approval_records = [
            {"id": "app-1", "decision_id": "app-1", "title": "Deploy Strategy Alpha", "status": "pending", "priority": "high", "created_at": "2026-08-30T10:00:00Z"},
            {"id": "app-2", "decision_id": "app-2", "title": "Approve Capital Rebalance", "status": "in_review", "priority": "critical", "created_at": "2026-08-30T11:00:00Z"},
            {"id": "app-3", "decision_id": "app-3", "title": "Close Incident Postmortem", "status": "approved", "priority": "low", "created_at": "2026-08-30T09:00:00Z"},
        ]
        self.jobs = [
            {"id": "job-1", "job_id": "job-1", "formula_id": "form-1", "status": "running", "submitted_at": "2026-08-30T10:00:00Z"},
            {"id": "job-2", "job_id": "job-2", "formula_id": "form-2", "status": "completed", "submitted_at": "2026-08-30T08:00:00Z"},
        ]
        self.incident_alerts = [
            {"id": "alert-1", "status": "open", "severity": "sev1", "title": "Drawdown breach", "created_at": "2026-08-30T10:30:00Z"},
            {"id": "alert-2", "status": "resolved", "severity": "sev3", "title": "API latency spike", "created_at": "2026-08-30T07:00:00Z"},
        ]
        self.sentinel_findings = [
            {"id": "sent-1", "kind": "anomaly", "status": "active", "severity": "high", "summary": "Unusual fill slippage"},
            {"id": "sent-2", "kind": "drift", "status": "resolved", "severity": "low", "summary": "Weight drift within limits"},
        ]
        self.loop_executions = [
            {"id": "loop-1", "loop_type": "research", "status": "completed", "created_at": "2026-08-30T11:10:00Z"},
            {"id": "loop-2", "loop_type": "execution", "status": "running", "created_at": "2026-08-30T11:20:00Z"},
            {"id": "loop-3", "loop_type": "research", "status": "failed", "created_at": "2026-08-30T11:25:00Z"},
        ]
        self.risk_radar_rows = [
            {"persona_id": "persona-a", "strategy_id": "strat-1", "capital_pool_id": "pool-main", "risk_state": "normal", "var_95": 0.015},
            {"persona_id": "persona-b", "strategy_id": "strat-2", "capital_pool_id": "pool-main", "risk_state": "elevated", "var_95": 0.042},
        ]
        self.incident_records = [
            {"id": "inc-1", "status": "open", "severity": "critical", "title": "Execution Disconnect", "created_at": "2026-08-30T10:00:00Z"},
            {"id": "inc-2", "status": "resolved", "severity": "medium", "title": "Data Feed Delay", "created_at": "2026-08-30T06:00:00Z"},
        ]
        self.interventions = [
            {"id": "intv-1", "persona_id": "persona-a", "status": "open", "kind": "rebalance_override", "summary": "Manual weight override"},
            {"id": "intv-2", "persona_id": "persona-b", "status": "completed", "kind": "kill_switch_test", "summary": "Routine drill"},
        ]
        self.evidence_records = [
            {"id": "ev-1", "ref_id": "ref-alpha", "linked_entity_type": "strategy", "linked_entity_ref": "strat-1", "link_type": "backtest", "credibility_tier": "tier1", "verified": True},
            {"id": "ev-2", "ref_id": "ref-beta", "linked_entity_type": "persona", "linked_entity_ref": "persona-b", "link_type": "audit", "credibility_tier": "tier2", "verified": False},
        ]
        self.personas = [
            {"persona_id": "persona-a", "name": "Alpha Trend", "stage": "paper", "pnl": 15420.0, "pnl_pct": 0.154, "drawdown_pct": 0.032, "sharpe": 2.1, "rank": 1, "score": 95.0},
            {"persona_id": "persona-b", "name": "Beta Arbitrage", "stage": "canary", "pnl": 8950.0, "pnl_pct": 0.089, "drawdown_pct": 0.018, "sharpe": 1.8, "rank": 2, "score": 88.0},
        ]

    def list_approval_records(self) -> List[Dict[str, Any]]:
        return self.approval_records

    def list_records(self, dataset: str, **kwargs) -> Tuple[bool, List[Dict[str, Any]]]:
        if dataset == "jobs":
            return True, self.jobs
        if dataset == "incident_alerts":
            return True, self.incident_alerts
        return False, []

    def list_incident_alerts(self) -> List[Dict[str, Any]]:
        return self.incident_alerts

    def list_sentinel_findings(self) -> List[Dict[str, Any]]:
        return self.sentinel_findings

    def list_loop_executions(self) -> List[Dict[str, Any]]:
        return self.loop_executions

    def list_risk_radar_rows(self) -> List[Dict[str, Any]]:
        return self.risk_radar_rows

    def list_incident_records(self) -> List[Dict[str, Any]]:
        return self.incident_records

    def list_intervention_records(self) -> List[Dict[str, Any]]:
        return self.interventions

    def list_evidence_records(self) -> List[Dict[str, Any]]:
        return self.evidence_records

    def list_personas(self) -> List[Dict[str, Any]]:
        return self.personas

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        for p in self.personas:
            if p["persona_id"] == persona_id:
                return p
        return None

    def list_runtime_bindings(self) -> List[Dict[str, Any]]:
        return [{"id": "run-1", "status": "running"}]

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
        return [{"id": "tel-1", "summary": "active"}]

    def get_kill_switch_status(self) -> Dict[str, Any]:
        return {"status": "armed", "safe_mode_status": "off", "active": False}


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_management_router_registration_and_routes_count():
    """Verify all 17 required Management domain routes are registered on create_management_router."""
    router = create_management_router()
    routes = [(list(getattr(r, "methods", set())), getattr(r, "path", "")) for r in router.routes]

    # Exactly 17 management domain routes
    required_17_endpoints = [
        ("GET", "/bff/management/shell-summary"),
        ("GET", "/api/v1/operator/home"),
        ("GET", "/bff/management/cockpit"),
        ("GET", "/bff/management/trading-pulse"),
        ("GET", "/bff/management/trading-pulse/rankings"),
        ("GET", "/bff/management/sentinel-pulse"),
        ("GET", "/api/v1/operator/health-status"),
        ("GET", "/bff/management/loop-throughput"),
        ("GET", "/bff/management/risk-radar"),
        ("GET", "/bff/management/incident-timeline"),
        ("GET", "/bff/management/human-inbox"),
        ("GET", "/bff/management/human-inbox/{item_id}"),
        ("GET", "/bff/management/hiq-backlog"),
        ("GET", "/bff/management/intervention-stream"),
        ("GET", "/bff/management/evidence"),
        ("GET", "/bff/management/operations-read-model/{persona_id}"),
        ("GET", "/api/v1/operator/degraded-control-guidance"),
    ]

    for method, path in required_17_endpoints:
        matching = [r for r in routes if r[1] == path and method in r[0]]
        assert len(matching) == 1, f"Expected exactly 1 route for {method} {path}, found {len(matching)}"

    assert len(router.routes) == 17, f"Expected exactly 17 routes on create_management_router, got {len(router.routes)}"

    # Also verify create_management_read_models_router has the 5 composed endpoints
    composed_router = create_management_read_models_router()
    composed_routes = [(list(getattr(r, "methods", set())), getattr(r, "path", "")) for r in composed_router.routes]
    composed_endpoints = [
        ("GET", "/bff/management/formula-jobs"),
        ("GET", "/bff/management/activity"),
        ("GET", "/bff/management/paper-telemetry"),
        ("GET", "/bff/management/postmortems"),
        ("GET", "/bff/management/postmortems/{postmortem_id}"),
    ]
    for method, path in composed_endpoints:
        matching = [r for r in composed_routes if r[1] == path and method in r[0]]
        assert len(matching) == 1, f"Expected composed route for {method} {path}"

    assert len(composed_router.routes) == 5, f"Expected 5 routes on create_management_read_models_router, got {len(composed_router.routes)}"


def test_composed_read_models_router_count():
    """Verify create_management_read_models_router has 5 routes and create_management_router has 17 routes."""
    r1 = create_management_read_models_router()
    r2 = create_management_router()
    assert len(r1.routes) == 5
    assert len(r2.routes) == 17


def test_shell_summary_endpoint():
    """Test GET /bff/management/shell-summary."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    resp = client.get("/bff/management/shell-summary", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "meta" in data
    assert data["data"]["counts"]["pending_approvals"] == 2
    assert data["data"]["counts"]["running_jobs"] == 1
    assert data["data"]["counts"]["open_alerts"] == 1
    assert data["data"]["session"]["operator_id"] == "op-1"
    assert data["data"]["transport"]["bff_status"] == "ok"


def test_operator_home_and_health_status_endpoints():
    """Test GET /api/v1/operator/home and GET /api/v1/operator/health-status."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Operator home
    resp = client.get("/api/v1/operator/home", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "cards" in data
    assert len(data["cards"]) >= 4

    # 2. Health status
    resp = client.get("/api/v1/operator/health-status", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "ok"
    assert "groups" in data
    assert len(data["groups"]) == 5
    group_ids = [g["group_id"] for g in data["groups"]]
    assert set(group_ids) == {"runtime", "telemetry", "incident", "governance", "kill_switch"}


def test_management_cockpit_and_trading_pulse():
    """Test GET /bff/management/cockpit and /bff/management/trading-pulse (with rankings)."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Cockpit
    resp = client.get("/bff/management/cockpit", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-cockpit"
    assert "alerts" in data["data"]
    assert "anomalies" in data["data"]

    # 2. Trading pulse
    resp = client.get("/bff/management/trading-pulse", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-trading-pulse"
    assert "cards" in data["data"]
    assert "summary" in data["data"]

    # 3. Trading pulse rankings
    resp = client.get("/bff/management/trading-pulse/rankings?limit=10", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "ranking_blocks" in data["data"] or "blocks" in data["data"]


def test_sentinel_pulse_and_loop_throughput():
    """Test GET /bff/management/sentinel-pulse and /bff/management/loop-throughput."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Sentinel pulse
    resp = client.get("/bff/management/sentinel-pulse", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-sentinel-pulse"
    assert len(data["data"]["findings"]) == 2
    assert data["data"]["summary"]["total_items"] == 2
    assert data["data"]["summary"]["active_finding_count"] == 1

    # 2. Loop throughput
    resp = client.get("/bff/management/loop-throughput?window_minutes=60", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-loop-throughput"
    assert data["data"]["metrics"]["total_runs"] == 3
    assert data["data"]["metrics"]["completed_loop_count"] == 1
    assert data["data"]["metrics"]["failed_loop_count"] == 1


def test_risk_radar_and_incident_timeline():
    """Test GET /bff/management/risk-radar and /bff/management/incident-timeline."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Risk radar
    resp = client.get("/bff/management/risk-radar", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-risk-radar"
    assert len(data["data"]["rows"]) == 2
    assert data["data"]["summary"]["indicator_count"] == 2
    assert data["data"]["summary"]["by_risk_state"]["elevated"] == 1

    # 2. Incident timeline
    resp = client.get("/bff/management/incident-timeline", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-incident-timeline"
    assert len(data["data"]["items"]) == 2
    assert data["data"]["summary"]["total_incidents"] == 2
    assert data["data"]["summary"]["status_counts"]["open"] == 1


def test_human_inbox_and_details_and_hiq_backlog():
    """Test GET /bff/management/human-inbox, detail, and /bff/management/hiq-backlog."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Human inbox list
    resp = client.get("/bff/management/human-inbox", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["items"]) == 3
    assert data["data"]["summary"]["total_items"] == 3
    assert data["data"]["summary"]["approval_count"] == 3

    # 2. Human inbox item detail
    resp = client.get("/bff/management/human-inbox/app-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "app-1"
    assert data["data"]["title"] == "Deploy Strategy Alpha"

    # 3. HIQ backlog
    resp = client.get("/bff/management/hiq-backlog", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-hiq-backlog"
    assert len(data["data"]["items"]) == 3
    assert data["data"]["summary"]["total_items"] == 3


def test_intervention_stream_and_evidence():
    """Test GET /bff/management/intervention-stream and /bff/management/evidence."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Intervention stream
    resp = client.get("/bff/management/intervention-stream?window_hours=24", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-intervention-stream"
    assert len(data["data"]["items"]) == 2
    assert data["data"]["summary"]["total_items"] == 2

    # 2. Evidence
    resp = client.get("/bff/management/evidence", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["items"]) == 2
    assert data["data"]["summary"]["verified_count"] == 1


def test_operations_read_model_and_degraded_control_guidance():
    """Test GET /bff/management/operations-read-model/{persona_id} and /api/v1/operator/degraded-control-guidance."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Operations read model
    resp = client.get("/bff/management/operations-read-model/persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["identity"]["persona_id"] == "persona-a"
    assert data["data"]["identity"]["persona_label"] == "Alpha Trend"
    assert data["data"]["performance"]["sharpe"] == 2.1
    assert data["data"]["data_confidence"] == "formal"

    # 2. Degraded control guidance
    resp = client.get("/api/v1/operator/degraded-control-guidance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["current_state"] == "fresh"
    assert data["data"]["primary_path"]["status"] == "available"


def test_composed_read_models_via_router():
    """Verify composed read model endpoints (/bff/management/formula-jobs, activity, paper-telemetry, postmortems)."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. formula-jobs
    resp = client.get("/bff/management/formula-jobs", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data["data"]
    assert len(data["data"]["items"]) == 2

    # 2. activity
    resp = client.get("/bff/management/activity", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data["data"]

    # 3. paper-telemetry
    resp = client.get("/bff/management/paper-telemetry", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data["data"]


def test_tenant_payload_fn_production_shape():
    """Verify tenant_payload_fn receives and parses production shape {'id': 'tenant-proof'}."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(
        create_management_router(
            get_read_store=lambda: mock_store,
            tenant_payload_fn=lambda id_: {"id": "tenant-proof"},
        )
    )
    client = TestClient(app)

    # 1. Risk radar probe
    resp = client.get("/bff/management/risk-radar", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200

    # 2. Operations read model probe
    resp = client.get("/bff/management/operations-read-model/persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200


def test_unauthenticated_requests_rejected_with_401():
    """Verify default router fails closed on unauthenticated requests returning 401 AUTH_REQUIRED."""
    app = FastAPI()
    app.include_router(create_management_router())
    client = TestClient(app, raise_server_exceptions=False)

    # Cockpit requires read auth
    resp = client.get("/bff/management/cockpit")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    # Shell summary requires read auth
    resp = client.get("/bff/management/shell-summary")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    # Risk radar requires read auth
    resp = client.get("/bff/management/risk-radar")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    # Operator home requires read auth
    resp = client.get("/api/v1/operator/home")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"


def test_cockpit_composition_with_empty_store():
    """Verify cockpit reports unavailable surfaces and 0 counts without synthetic constants when store is missing."""
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: None))
    client = TestClient(app)

    resp = client.get("/bff/management/cockpit", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-cockpit"
    assert data["data"]["alerts"]["summary"]["total_active"] == 0
    assert data["data"]["anomalies"]["summary"]["total"] == 0
    assert "system_kpis" not in data["data"]
    assert "cards" not in data["data"]
    assert data["meta"]["surfaces"]["management_cockpit"]["status"] in {"unavailable", "degraded"}


def test_management_router_17_target_routes_inventory():
    """Verify create_management_router registers all 17 target GET routes."""
    from test_normalized_route_uniqueness import scan_fastapi_routes

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_management_router(get_read_store=lambda: MockManagementReadStore()))

    target_routes = [
        "/bff/management/shell-summary",
        "/api/v1/operator/home",
        "/bff/management/cockpit",
        "/bff/management/trading-pulse",
        "/bff/management/trading-pulse/rankings",
        "/bff/management/sentinel-pulse",
        "/api/v1/operator/health-status",
        "/bff/management/loop-throughput",
        "/bff/management/risk-radar",
        "/bff/management/incident-timeline",
        "/bff/management/human-inbox",
        "/bff/management/human-inbox/{item_id}",
        "/bff/management/hiq-backlog",
        "/bff/management/intervention-stream",
        "/bff/management/evidence",
        "/bff/management/operations-read-model/{persona_id}",
        "/api/v1/operator/degraded-control-guidance",
    ]

    scanned_entries = scan_fastapi_routes(app)
    for path in target_routes:
        matching = [
            entry for entry in scanned_entries
            if entry.raw_path == path and entry.method == "GET"
        ]
        assert len(matching) == 1, f"Expected exactly 1 registered route for GET {path}, found {len(matching)}"

    # Confirm exactly 17 routes registered
    assert len(scanned_entries) == 17, f"Expected 17 routes registered in app, got {len(scanned_entries)}"


def test_operator_health_status_fail_closed_on_missing_ports():
    """Verify health status reports degraded and fail closed if runtime or telemetry components are unreachable."""
    class FailingReadStore(MockManagementReadStore):
        def list_runtime_bindings(self) -> List[Dict[str, Any]]:
            raise ConnectionError("Runtime plane unreachable")

        def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
            raise TimeoutError("Telemetry store timeout")

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: FailingReadStore()))
    client = TestClient(app)

    resp = client.get("/api/v1/operator/health-status", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] in {"unavailable", "degraded"}
    groups = {g["group_id"]: g["status"] for g in data["groups"]}
    assert groups["runtime"] == "unavailable"
    assert groups["telemetry"] == "unavailable"


def test_degraded_control_guidance_contract():
    """Verify GET /api/v1/operator/degraded-control-guidance preserves 200/206 and {data, meta.staleness} envelope."""
    # 1. Fresh state with store present -> 200
    mock_store = MockManagementReadStore()
    app_fresh = FastAPI()
    app_fresh.include_router(create_management_router(get_read_store=lambda: mock_store))
    client_fresh = TestClient(app_fresh)

    resp_fresh = client_fresh.get("/api/v1/operator/degraded-control-guidance")
    assert resp_fresh.status_code == 200
    body_fresh = resp_fresh.json()
    assert "data" in body_fresh and "meta" in body_fresh
    assert "staleness" in body_fresh["meta"]
    guidance = body_fresh["data"]
    assert guidance["current_state"] == "fresh"
    assert guidance["primary_path"]["status"] == "available"
    assert "admin_cli" in guidance["secondary_path"]
    assert "protected_internal_api" in guidance["secondary_path"]
    assert guidance["critical_actions_bypass_mfa"] is True

    # 2. Degraded state with no store -> 206
    app_deg = FastAPI()
    app_deg.include_router(create_management_router(get_read_store=lambda: None))
    client_deg = TestClient(app_deg)

    resp_deg = client_deg.get("/api/v1/operator/degraded-control-guidance")
    assert resp_deg.status_code == 206
    body_deg = resp_deg.json()
    assert "data" in body_deg and "meta" in body_deg
    assert "staleness" in body_deg["meta"]
    assert body_deg["data"]["current_state"] == "degraded"


def test_management_nl_ask_service_execution():
    """Test ManagementService.ask_nl standard execution."""
    mock_store = MockManagementReadStore()
    svc = ManagementService(get_read_store=lambda: mock_store)
    identity = _default_extract_identity("Bearer op-1:operator")

    payload = {
        "question": "What is the status of open alerts and anomalies?",
        "focus": "risk",
        "sessionId": "test-session-123",
    }
    resp = svc.ask_nl(
        payload=payload,
        identity=identity,
        tenant_id="tenant-main",
    )
    assert resp["status_code"] == 202
    body = resp["payload"]
    assert "data" in body and "meta" in body
    data = body["data"]
    assert data["session_id"] == "test-session-123"
    assert "message_id" in data
    assert "trace_id" in data
    assert "answer" in data
    assert data["confidence"] == "high"
    assert len(data["sources"]) >= 1
    assert data["control_mode"]["active"] is False
    assert body["meta"]["status"] == "ok"


def test_management_nl_ask_dry_run_and_validation(tmp_path: Path):
    """Test ManagementService.ask_nl dry-run mode, header idempotency, and input validation."""
    from management_nl_command_idempotency import ManagementNlCommandIdempotencyStore
    idem_store = ManagementNlCommandIdempotencyStore(storage_path=str(tmp_path / "idem_dry_val.json"))
    mock_store = MockManagementReadStore()
    svc = ManagementService(
        get_read_store=lambda: mock_store,
        idempotency_store=idem_store,
    )
    identity = _default_extract_identity("Bearer op-1:operator")

    # 1. Dry run query returns 202 compact receipt
    payload_dry = {
        "question": "Simulate diagnostic inquiry",
        "dry_run": True,
    }
    resp_dry = svc.ask_nl(
        payload=payload_dry,
        identity=identity,
        tenant_id="tenant-main",
        idempotency_key="idem-dry-123",
    )
    assert resp_dry["status_code"] == 202
    body_dry = resp_dry["payload"]
    assert body_dry["data"]["confidence"] == "dry_run"
    assert body_dry["meta"]["dry_run_mode"] == "compact_receipt"
    assert body_dry["meta"]["idempotency"]["replayed"] is False

    # 2. Replay with same idempotency key returns exact same receipt
    resp_replay = svc.ask_nl(
        payload=payload_dry,
        identity=identity,
        tenant_id="tenant-main",
        idempotency_key="idem-dry-123",
    )
    assert resp_replay["status_code"] == 202
    body_replay = resp_replay["payload"]
    assert body_replay["data"]["message_id"] == body_dry["data"]["message_id"]
    assert body_replay["meta"]["idempotency"]["replayed"] is True


def test_management_nl_ask_high_risk_refusal():
    """Test ManagementService.classify_high_risk and ask_nl policy refusal for dangerous mutations."""
    mock_store = MockManagementReadStore()
    svc = ManagementService(get_read_store=lambda: mock_store)
    identity = _default_extract_identity("Bearer op-1:operator")

    high_risk_questions = [
        "delete all personas right now",
        "drop table personas immediately",
        "execute real trade buy AAPL 1000 shares",
        "delete from strategies where id=1",
        "disable kill switch right now",
    ]
    for q in high_risk_questions:
        risk = svc.classify_high_risk(q)
        assert risk is not None, f"Expected high-risk classification for: {q}"
        assert "matched_category" in risk
        assert "matched_pattern" in risk

        res = svc.ask_nl(
            payload={"question": q},
            identity=identity,
            tenant_id="tenant-main",
        )
        assert res.get("refused") is True
        assert res.get("matched_category") == risk["matched_category"]


def test_management_nl_ask_stream_endpoint():
    """Test ManagementService.ask_nl_stream SSE event generation."""
    mock_store = MockManagementReadStore()
    svc = ManagementService(get_read_store=lambda: mock_store)
    identity = _default_extract_identity("Bearer op-1:operator")

    payload = {
        "question": "Provide a comprehensive streaming analysis of the current portfolio",
        "focus": "all",
        "sessionId": "stream-session-999",
    }
    chunks = list(svc.ask_nl_stream(
        payload=payload,
        identity=identity,
        tenant_id="tenant-main",
    ))
    assert len(chunks) >= 3
    full_text = "".join(chunks)
    assert "event: chunk" in full_text
    assert "event: done" in full_text


def test_management_evidence_capability_redaction_and_facets():
    """Verify /bff/management/evidence enforces capability redaction, facets, and summary counts."""
    mock_store = MockManagementReadStore()
    mock_store.evidence_records.append({
        "id": "ev-metric-1",
        "ref_id": "evref-b3-metric-001",
        "evidence_type": "metric",
        "title": "Sharpe metric evidence",
        "display_label": "Sharpe Metric Audit",
        "source_type": "metric",
        "source_document": {
            "title": "Sharpe metric evidence",
            "source_type": "metric",
            "source_ref": "metric://runtime-alpha/sharpe",
        },
        "link_type": "supporting_evidence",
        "credibility_tier": "primary",
        "credibility": {"tier": "primary", "verified": True},
        "linked_entity_type": "artifact",
        "linked_entity_ref": "art-001",
        "linked_object_summary": {"entity_type": "artifact", "entity_ref": "art-001"},
        "artifact_manifest": {"secret_field": "confidential_manifest_content"},
        "criteria": {"threshold": 1.5},
        "verified": True,
    })

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Operator role lacks metric.read -> evref-b3-metric-001 is redacted
    resp_op = client.get(
        "/bff/management/evidence?ref_id=evref-b3-metric-001",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp_op.status_code == 200
    data_op = resp_op.json()
    item_op = data_op["data"]["items"][0]
    assert item_op["redacted"] is True
    assert item_op["required_capability"] == "metric.read"
    assert "artifact_manifest" not in item_op
    assert "criteria" not in item_op
    assert data_op["data"]["summary"]["redacted_evidence"] == 1
    assert data_op["meta"]["redacted_evidence_count"] == 1

    # 2. Admin role has all capabilities -> evref-b3-metric-001 has full artifact_manifest
    resp_adm = client.get(
        "/bff/management/evidence?ref_id=evref-b3-metric-001",
        headers={"Authorization": "Bearer op-admin:admin"},
    )
    assert resp_adm.status_code == 200
    data_adm = resp_adm.json()
    item_adm = data_adm["data"]["items"][0]
    assert item_adm["redacted"] is False
    assert "artifact_manifest" in item_adm
    assert item_adm["artifact_manifest"]["secret_field"] == "confidential_manifest_content"
    assert data_adm["data"]["summary"]["redacted_evidence"] == 0
    assert data_adm["meta"]["redacted_evidence_count"] == 0

    # 3. Facets returned
    facets = data_adm["data"]["facets"]
    assert "source_types" in facets
    assert "link_types" in facets
    assert "credibility_tiers" in facets


def test_management_evidence_validation_rules(tmp_path: Path):
    """Verify /bff/management/evidence rejects invalid query parameters with 400."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Invalid linked_entity_type -> 400
    resp = client.get(
        "/bff/management/evidence?linked_entity_type=invalid_type_xyz",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp.status_code == 400
    err = resp.json().get("detail", resp.json()).get("error", {})
    assert err.get("code") == "VALIDATION_FAILED"
    assert "linked_entity_type" in str(err.get("details", {}))

    # 2. linked_entity_ref without linked_entity_type -> 400
    resp_ref = client.get(
        "/bff/management/evidence?linked_entity_ref=ref-123",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp_ref.status_code == 400
    err_ref = resp_ref.json().get("detail", resp_ref.json()).get("error", {})
    assert err_ref.get("code") == "VALIDATION_FAILED"
    assert "linked_entity_ref" in str(err_ref.get("details", {}))

    # 3. Invalid link_type -> 400
    resp_lt = client.get(
        "/bff/management/evidence?link_type=invalid_link_type",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp_lt.status_code == 400

    # 4. Invalid credibility_tier -> 400
    resp_ct = client.get(
        "/bff/management/evidence?credibility_tier=invalid_tier",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp_ct.status_code == 400


def test_management_evidence_current_run_verifier_projection(tmp_path: Path, monkeypatch):
    """Verify BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json is projected into evidence explorer."""
    verify_json = tmp_path / "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json"
    verify_json.write_text(json.dumps({
        "overall": "pass",
        "generated_at": "2026-08-30T12:00:00Z",
        "artifact_dir": str(tmp_path),
        "artifact_manifest": {"bundle": "test-artifact.tar.gz"},
        "criteria": {"pass_rate": 1.0},
    }), encoding="utf-8")

    preflight_json = tmp_path / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight_json.write_text(json.dumps({
        "operator_remediation": {
            "github_environment": "production",
            "repository": "ajoe734/pantheon",
            "required_secret_names": ["SECRET_KEY"],
            "missing_secret_names": [],
            "workflow_dispatch": {"recommended_workflow": "verify.yml"},
        },
    }), encoding="utf-8")

    release_gate_json = tmp_path / "release-gate-summary.json"
    release_gate_json.write_text(json.dumps({
        "overall": "pass",
        "gates": {
            "security": [{"label": "mfa_check", "status": "pass"}],
        },
    }), encoding="utf-8")

    monkeypatch.setenv("PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON", str(verify_json))
    monkeypatch.setenv("PANTHEON_BFF_LIVE_EVIDENCE_PREFLIGHT_JSON", str(preflight_json))
    monkeypatch.setenv("PANTHEON_BFF_RELEASE_GATE_SUMMARY_JSON", str(release_gate_json))

    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    resp = client.get(
        "/bff/management/evidence?ref_id=BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["items"]) == 1
    item = data["data"]["items"][0]
    assert item["ref_id"] == "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY"
    assert item["operator_remediation"]["github_environment"] == "production"
    assert item["release_gate_summary"]["overall"] == "pass"


def test_management_nl_ask_crash_safe_idempotency_and_replay(tmp_path: Path):
    """Verify Idempotency-Key returns identical message_id on replay and 409 on payload conflict."""
    conv_store_path = tmp_path / "conversations.json"
    idem_store_path = tmp_path / "idempotency.json"

    from management_ai_store import ManagementAiConversationStore
    from management_nl_command_idempotency import ManagementNlCommandIdempotencyStore

    conv_store = ManagementAiConversationStore(storage_path=str(conv_store_path))
    idem_store = ManagementNlCommandIdempotencyStore(storage_path=str(idem_store_path))

    mock_store = MockManagementReadStore()
    svc = ManagementService(
        get_read_store=lambda: mock_store,
        conversation_store=conv_store,
        idempotency_store=idem_store,
    )
    identity = _default_extract_identity("Bearer op-1:operator")

    payload1 = {
        "question": "What is the trading pulse and risk status?",
        "focus": "all",
        "sessionId": "session-replay-1",
    }

    # First execution
    resp1 = svc.ask_nl(payload=payload1, identity=identity, tenant_id="tenant-test", idempotency_key="test-key-replay-999")
    assert resp1["status_code"] == 202
    body1 = resp1["payload"]
    msg_id1 = body1["data"]["message_id"]
    ans1 = body1["data"]["answer"]
    assert msg_id1.startswith("mnl-")

    # Second execution with exact same payload -> returns exact same message_id and replayed: True
    resp2 = svc.ask_nl(payload=payload1, identity=identity, tenant_id="tenant-test", idempotency_key="test-key-replay-999")
    assert resp2["status_code"] == 202
    body2 = resp2["payload"]
    assert body2["data"]["message_id"] == msg_id1
    assert body2["data"]["answer"] == ans1
    assert body2["meta"]["idempotency"]["replayed"] is True

    # Third execution with conflicting payload for same key -> raises payload conflict
    payload_conflict = {
        "question": "Different question for same key",
        "focus": "all",
    }
    with pytest.raises(Exception):
        svc.ask_nl(payload=payload_conflict, identity=identity, tenant_id="tenant-test", idempotency_key="test-key-replay-999")

    # Verify conversation store turns were persisted (1 user turn + 1 assistant turn)
    turns = conv_store.list_turns("session-replay-1")
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["text"] == payload1["question"]
    assert turns[1]["role"] == "assistant"
    assert turns[1]["text"] == ans1


def test_management_nl_ask_with_injected_provider_client(tmp_path: Path):
    """Verify provider_client is invoked when passed to service."""
    class FakeProvider:
        def __init__(self):
            self.invoked = False
            self.last_question = None

        def invoke_assistant_provider(self, **kwargs):
            self.invoked = True
            self.last_question = kwargs.get("question")
            return {
                "data": {
                    "answer": "Fake assistant provider answer for testing.",
                    "status": "completed",
                    "provider": "fake_codex",
                    "output": {
                        "actions": [
                            {
                                "kind": "navigate",
                                "label": "Go to Cockpit",
                                "params": {"to": "/management/cockpit"},
                            }
                        ]
                    }
                }
            }

    fake_provider = FakeProvider()
    mock_store = MockManagementReadStore()
    svc = ManagementService(
        get_read_store=lambda: mock_store,
        provider_client=fake_provider,
    )
    identity = _default_extract_identity("Bearer op-1:operator")

    resp = svc.ask_nl(
        payload={"question": "Analyze current risk posture"},
        identity=identity,
        tenant_id="tenant-main",
    )
    assert resp["status_code"] == 202
    body = resp["payload"]
    assert fake_provider.invoked is True
    assert fake_provider.last_question == "Analyze current risk posture"
    assert body["data"]["answer"] == "Fake assistant provider answer for testing."
    assert body["data"]["provider_status"]["provider"] == "fake_codex"


def test_management_nl_ask_dry_run_idempotency_replay_after_recovery_window(tmp_path: Path):
    """Isolated regression: dry-run idempotency completes terminally and replays cleanly after recovery expiry."""
    from management_nl_command_idempotency import ManagementNlCommandIdempotencyStore
    import time

    idem_store_path = tmp_path / "idem_dry_replay.json"
    idem_store = ManagementNlCommandIdempotencyStore(
        storage_path=str(idem_store_path),
        recovery_seconds=0.1,
    )
    mock_store = MockManagementReadStore()
    svc = ManagementService(
        get_read_store=lambda: mock_store,
        idempotency_store=idem_store,
    )
    identity = _default_extract_identity("Bearer op-1:operator")

    payload = {
        "question": "What is the trading pulse and risk status?",
        "dry_run": True,
        "sessionId": "session-dry-rec-1",
    }

    # 1. First dry-run execution
    resp1 = svc.ask_nl(payload=payload, identity=identity, tenant_id="tenant-test", idempotency_key="test-key-dry-recovery-001")
    assert resp1["status_code"] == 202
    body1 = resp1["payload"]
    assert body1["data"]["confidence"] == "dry_run"
    assert body1["meta"]["dry_run_mode"] == "compact_receipt"

    # 2. Wait past the recovery window (0.1s recovery_seconds)
    time.sleep(0.2)

    # 3. Replay exact same request with same key after recovery window
    resp2 = svc.ask_nl(payload=payload, identity=identity, tenant_id="tenant-test", idempotency_key="test-key-dry-recovery-001")
    assert resp2["status_code"] == 202
    body2 = resp2["payload"]
    assert body2["data"]["confidence"] == "dry_run"
    assert body2["meta"]["dry_run_mode"] == "compact_receipt"
    assert body2["meta"]["idempotency"]["replayed"] is True


def test_management_router_conditional_registration_crud_cutover():
    """Verify create_management_read_models_router (5 routes) and create_management_router (17 routes) have zero path overlap."""
    router_composed = create_management_read_models_router()
    assert len(router_composed.routes) == 5
    paths_composed = set(r.path for r in router_composed.routes)

    router_mgmt = create_management_router()
    assert len(router_mgmt.routes) == 17
    paths_mgmt = set(r.path for r in router_mgmt.routes)

    overlap = paths_composed.intersection(paths_mgmt)
    assert overlap == set(), f"Expected no path overlap between composed and management routers, got {overlap}"


def test_management_nl_ask_concurrent_wait_and_observe_completion():
    """Verify that when idempotency returns state='wait', worker polls observe() until completion without duplicate provider execution."""
    from dataclasses import dataclass

    @dataclass
    class MockAdmission:
        state: str
        reservation: Optional[Any] = None
        result: Optional[Dict[str, Any]] = None

    class MockWaitStore:
        def __init__(self):
            self.observe_calls = 0

        def admit(self, scope, request_hash=None):
            return MockAdmission(state="wait")

        def observe(self, scope, request_hash=None):
            self.observe_calls += 1
            if self.observe_calls >= 2:
                return MockAdmission(
                    state="complete",
                    result={
                        "data": {
                            "status": "completed",
                            "answer": "Concurrent observed answer",
                        },
                        "meta": {"idempotency": {"replayed": False}},
                    },
                )
            return MockAdmission(state="wait")

    class FakeProvider:
        def __init__(self):
            self.invoked = False

        def invoke_assistant_provider(self, **kwargs):
            self.invoked = True
            return {"data": {"answer": "Should not be called"}}

    idem_store = MockWaitStore()
    fake_provider = FakeProvider()
    svc = ManagementService(
        idempotency_store=idem_store,
        provider_client=fake_provider,
    )
    identity = _default_extract_identity("Bearer op-1:operator")

    resp = svc.ask_nl(
        payload={"question": "Analyze concurrency"},
        identity=identity,
        tenant_id="tenant-main",
        idempotency_key="wait-key-1",
    )
    assert resp["status_code"] == 202
    body = resp["payload"]
    assert body["data"]["answer"] == "Concurrent observed answer"
    assert body["meta"]["idempotency"]["replayed"] is True
    assert fake_provider.invoked is False
    assert idem_store.observe_calls >= 2


def test_management_nl_ask_concurrent_wait_timeout_conflict(monkeypatch):
    """Verify that when idempotency wait exceeds deadline, exception is raised."""
    from dataclasses import dataclass

    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_WAIT_SECONDS", "0.02")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_POLL_SECONDS", "0.005")

    @dataclass
    class MockAdmission:
        state: str = "wait"

    class MockStuckWaitStore:
        def admit(self, scope, request_hash=None):
            return MockAdmission(state="wait")

        def observe(self, scope, request_hash=None):
            return MockAdmission(state="wait")

    svc = ManagementService(
        idempotency_store=MockStuckWaitStore(),
    )
    identity = _default_extract_identity("Bearer op-1:operator")

    with pytest.raises(Exception) as exc_info:
        svc.ask_nl(
            payload={"question": "Analyze stuck concurrency"},
            identity=identity,
            tenant_id="tenant-main",
            idempotency_key="stuck-wait-key-1",
        )
    assert "conflict" in str(exc_info.value).lower() or "in progress" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower() or "uncertain" in str(exc_info.value).lower()


def test_management_nl_ask_idempotency_storage_error():
    """Verify that idempotency storage failures during admission or completion raise error."""
    from management_nl_command_idempotency import ManagementNlCommandStorageError
    from dataclasses import dataclass, field

    @dataclass
    class MockReservation:
        token: str = "tok-1"

    @dataclass
    class MockAdmission:
        state: str = "owner"
        reservation: Any = field(default_factory=MockReservation)

    class MockFailingStore:
        def __init__(self, fail_on="admit"):
            self.fail_on = fail_on

        def admit(self, scope, request_hash=None):
            if self.fail_on == "admit":
                raise ManagementNlCommandStorageError("Admission storage down")
            return MockAdmission()

        def complete(self, reservation, result_payload):
            if self.fail_on == "complete":
                raise ManagementNlCommandStorageError("Complete storage down")

    # 1. Failure during admit
    svc1 = ManagementService(idempotency_store=MockFailingStore(fail_on="admit"))
    identity = _default_extract_identity("Bearer op-1:operator")
    with pytest.raises(Exception):
        svc1.ask_nl(
            payload={"question": "Test admit failure"},
            identity=identity,
            tenant_id="tenant-main",
            idempotency_key="fail-key-1",
        )

    # 2. Failure during complete
    svc2 = ManagementService(idempotency_store=MockFailingStore(fail_on="complete"))
    with pytest.raises(Exception):
        svc2.ask_nl(
            payload={"question": "Test complete failure"},
            identity=identity,
            tenant_id="tenant-main",
            idempotency_key="fail-key-2",
        )


def test_management_nl_ask_control_mode_commands_and_reflection(tmp_path: Path):
    """Verify control mode status, activation, reflection in answers, and deactivation."""
    from assistant.control_mode import ControlModeStore

    store_path = tmp_path / "control_mode.json"
    ctrl_store = ControlModeStore(storage_path=str(store_path))
    ctrl_store.set_passphrase(new_passphrase="correct-horse-battery-staple", require_current=False)

    class AdminIdentity:
        operator_id = "admin-user"
        roles = {"admin", "operator"}
        is_authenticated = True
        mfa_verified = True
        session_kind = "bearer"
        allowed_tenants = {"*"}
        claims = {"capabilities": ["assistant.kernel"]}

    admin_ident = AdminIdentity()
    svc = ManagementService(
        control_mode_store=ctrl_store,
    )

    # 1. Query status -> inactive
    resp1 = svc.ask_nl(
        payload={"question": "/control-mode/status", "sessionId": "admin-session-1"},
        identity=admin_ident,
        tenant_id="tenant-main",
    )
    assert resp1["status_code"] == 202
    body1 = resp1["payload"]
    assert body1["data"]["provider_status"]["reason"] == "control_mode_status"
    assert body1["data"]["control_mode"]["active"] is False

    # 2. Activate control mode with passphrase
    resp2 = svc.ask_nl(
        payload={
            "question": "/control-mode/on correct-horse-battery-staple",
            "control_mode": {"mode": "kernel_debug"},
            "sessionId": "admin-session-1",
        },
        identity=admin_ident,
        tenant_id="tenant-main",
    )
    assert resp2["status_code"] == 202
    body2 = resp2["payload"]
    assert body2["data"]["provider_status"]["reason"] == "control_mode_activate"
    assert body2["data"]["control_mode"]["active"] is True
    assert body2["data"]["control_mode"]["mode"] == "kernel_debug"

    # 3. Regular question reflects active control mode
    resp3 = svc.ask_nl(
        payload={"question": "What is the system status?", "sessionId": "admin-session-1"},
        identity=admin_ident,
        tenant_id="tenant-main",
    )
    assert resp3["status_code"] == 202
    body3 = resp3["payload"]
    assert body3["data"]["control_mode"]["active"] is True
    assert body3["data"]["control_mode"]["mode"] == "kernel_debug"
    assert body3["meta"]["control_mode"]["active"] is True

    # 4. Deactivate control mode
    resp4 = svc.ask_nl(
        payload={"question": "/control-mode/deactivate", "sessionId": "admin-session-1"},
        identity=admin_ident,
        tenant_id="tenant-main",
    )
    assert resp4["status_code"] == 202
    body4 = resp4["payload"]
    assert body4["data"]["provider_status"]["reason"] == "control_mode_deactivate"
    assert body4["data"]["control_mode"]["active"] is False

    # 5. Subsequent regular question reflects inactive
    resp5 = svc.ask_nl(
        payload={"question": "What is the system status?", "sessionId": "admin-session-1"},
        identity=admin_ident,
        tenant_id="tenant-main",
    )
    assert resp5["status_code"] == 202
    body5 = resp5["payload"]
    assert body5["data"]["control_mode"]["active"] is False
