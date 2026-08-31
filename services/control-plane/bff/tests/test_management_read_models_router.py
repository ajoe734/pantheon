"""Tests for Management System & Read Models Router (OPGAP-BE-MANAGEMENT-ROUTER-V2-20260830).

Verifies:
- All 17 Management system route decorators are registered with correct HTTP methods and paths
- Composed read model endpoints (formula-jobs, activity, paper-telemetry, postmortems) are preserved
- Route uniqueness and absence of collisions
- Full functional execution of all endpoints via TestClient
- Mock store integration, filtering, pagination, error handling, and degradation semantics
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

# Ensure bff root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from management_read_models.router import create_management_read_models_router, create_management_router
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
    """Verify all 17 required Management domain routes + composed routes are registered."""
    router = create_management_read_models_router()
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

    # Also verify the 5 composed endpoints
    composed_endpoints = [
        ("GET", "/bff/management/formula-jobs"),
        ("GET", "/bff/management/activity"),
        ("GET", "/bff/management/paper-telemetry"),
        ("GET", "/bff/management/postmortems"),
        ("GET", "/bff/management/postmortems/{postmortem_id}"),
    ]
    for method, path in composed_endpoints:
        matching = [r for r in routes if r[1] == path and method in r[0]]
        assert len(matching) == 1, f"Expected composed route for {method} {path}"

    assert len(router.routes) == 22, f"Expected 22 total routes (17 domain + 5 composed), got {len(router.routes)}"


def test_alias_factory_equivalence():
    """Verify create_management_router is equivalent alias to create_management_read_models_router."""
    r1 = create_management_read_models_router()
    r2 = create_management_router()
    assert len(r1.routes) == len(r2.routes)


def test_shell_summary_endpoint():
    """Test GET /bff/management/shell-summary."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
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
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
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
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Cockpit
    resp = client.get("/bff/management/cockpit", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-cockpit"
    assert "alerts" in data["data"]
    assert "anomalies" in data["data"]
    assert "human_inbox" in data["data"]
    assert "trading_pulse" in data["data"]
    assert data["meta"]["surfaces"]["management_cockpit"]["status"] in {"ok", "degraded"}

    # 2. Trading pulse
    resp = client.get("/bff/management/trading-pulse", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-trading-pulse"
    assert len(data["data"]["monitoring_cards"]) >= 3

    # 3. Trading pulse rankings
    resp = client.get("/bff/management/trading-pulse/rankings?limit=2", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["ranking_blocks"]["top_performers"]) == 2


def test_sentinel_pulse_and_loop_throughput():
    """Test GET /bff/management/sentinel-pulse and /bff/management/loop-throughput."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Sentinel pulse
    resp = client.get("/bff/management/sentinel-pulse", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-sentinel-pulse"
    assert len(data["data"]["items"]) == 2
    assert data["data"]["summary"]["active_findings"] == 1

    # Filter sentinel pulse
    resp = client.get("/bff/management/sentinel-pulse?kind=anomaly", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["items"]) == 1

    # 2. Loop throughput
    resp = client.get("/bff/management/loop-throughput?window_minutes=60", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-loop-throughput"
    assert data["data"]["summary"]["total_runs"] == 3
    assert data["data"]["summary"]["status_counts"]["completed"] == 1


def test_risk_radar_and_incident_timeline():
    """Test GET /bff/management/risk-radar and /bff/management/incident-timeline."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Risk radar
    resp = client.get("/bff/management/risk-radar", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-risk-radar"
    assert len(data["data"]["rows"]) == 2

    # Filter risk radar
    resp = client.get("/bff/management/risk-radar?persona_id=persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert len(resp.json()["data"]["rows"]) == 1

    # 2. Incident timeline
    resp = client.get("/bff/management/incident-timeline", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-incident-timeline"
    assert len(data["data"]["items"]) == 2
    assert data["data"]["summary"]["severity_counts"]["critical"] == 1


def test_human_inbox_and_details_and_hiq_backlog():
    """Test Human Inbox list, item detail, and HIQ backlog."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Human inbox list
    resp = client.get("/bff/management/human-inbox", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-human-inbox"
    assert len(data["data"]["items"]) == 3

    # 2. Human inbox detail - found
    resp = client.get("/bff/management/human-inbox/app-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["data"]["item_id"] == "app-1"

    # 3. Human inbox detail - not found
    resp = client.get("/bff/management/human-inbox/missing-item-999", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 404

    # 4. HIQ backlog
    resp = client.get("/bff/management/hiq-backlog", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-hiq-backlog"
    assert len(data["data"]["items"]) == 3


def test_intervention_stream_and_evidence():
    """Test GET /bff/management/intervention-stream and /bff/management/evidence."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Intervention stream
    resp = client.get("/bff/management/intervention-stream", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-intervention-stream"
    assert len(data["data"]["items"]) == 2

    # Filter intervention stream
    resp = client.get("/bff/management/intervention-stream?persona_id=persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) == 1

    # 2. Evidence explorer
    resp = client.get("/bff/management/evidence", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-evidence"
    assert len(data["data"]["items"]) == 2
    assert data["data"]["summary"]["verified_count"] == 1

    # Filter evidence by verification
    resp = client.get("/bff/management/evidence?verified=true", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) == 1


def test_operations_read_model_and_degraded_control_guidance():
    """Test Operations Read Model and Degraded Control Guidance."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Operations read model for persona-a
    resp = client.get("/bff/management/operations-read-model/persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["identity"]["persona_id"] == "persona-a"
    assert data["data"]["performance"]["pnl"] == 15420.0
    assert data["data"]["performance"]["sharpe"] == 2.1
    assert data["data"]["data_confidence"] == "formal"

    # Missing persona in store
    resp = client.get("/bff/management/operations-read-model/non-existent-persona", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 404

    # 2. Degraded control guidance
    resp = client.get("/api/v1/operator/degraded-control-guidance")
    assert resp.status_code in (200, 206)
    data = resp.json()
    assert "primary_path" in data["data"]
    assert "secondary_path" in data["data"]
    assert "admin_cli" in data["data"]["secondary_path"]
    assert "protected_internal_api" in data["data"]["secondary_path"]
    assert data["data"]["critical_actions_bypass_mfa"] is True


def test_composed_read_models_via_router():
    """Test composed read models formula-jobs, activity, paper-telemetry, postmortems."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # Formula jobs
    resp = client.get("/bff/management/formula-jobs", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "management-formula-jobs"

    # Activity
    resp = client.get("/bff/management/activity", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "management-activity"

    # Paper telemetry
    resp = client.get("/bff/management/paper-telemetry", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "management-paper-telemetry"

    # Postmortems list
    resp = client.get("/bff/management/postmortems", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "management-postmortems"


def test_tenant_payload_fn_production_shape():
    """Verify tenant_payload_fn receives and parses production shape {'id': 'tenant-proof'}."""
    captured_tenants: List[Optional[str]] = []

    def mock_risk_radar_builder(**kwargs):
        captured_tenants.append(kwargs.get("tenant_id"))
        return {"data": {"id": "management-risk-radar", "rows": [], "summary": {}}, "meta": {}}

    def mock_ops_builder(persona_id, **kwargs):
        captured_tenants.append(kwargs.get("tenant_id"))
        return {
            "identity": {
                "persona_id": persona_id,
                "persona_label": f"Persona {persona_id}",
                "stage": "paper",
                "runtime_ids": [f"rt-{persona_id}"],
                "paper_ledger_ids": [],
                "capital_pool_ids": [],
                "sleeve_ids": [],
                "strategy_ids": [],
                "artifact_ids": [],
                "broker_ids": [],
                "period": "latest",
                "as_of": "2026-08-30T10:00:00Z",
            },
            "performance": {
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "drawdown_pct": 0.0,
                "risk_pct": 0.0,
                "sharpe": 1.0,
                "rank": 1,
                "score": 90.0,
            },
            "data_confidence": "formal",
            "sources": [],
            "diagnostics": [],
        }

    app = FastAPI()
    app.include_router(
        create_management_read_models_router(
            risk_radar_builder=mock_risk_radar_builder,
            operations_read_model_builder=mock_ops_builder,
            tenant_payload_fn=lambda id_: {"id": "tenant-proof"},
        )
    )
    client = TestClient(app)

    # 1. Risk radar probe
    resp = client.get("/bff/management/risk-radar", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert captured_tenants[-1] == "tenant-proof"

    # 2. Operations read model probe
    resp = client.get("/bff/management/operations-read-model/persona-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert captured_tenants[-1] == "tenant-proof"

    # 3. Alternative payload shape with tenant_id key
    captured_tenants.clear()
    app2 = FastAPI()
    app2.include_router(
        create_management_read_models_router(
            risk_radar_builder=mock_risk_radar_builder,
            operations_read_model_builder=mock_ops_builder,
            tenant_payload_fn=lambda id_: {"tenant_id": "tenant-alt"},
        )
    )
    client2 = TestClient(app2)
    resp = client2.get("/bff/management/risk-radar", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert captured_tenants[-1] == "tenant-alt"


def test_unauthenticated_requests_rejected_with_401():
    """Verify default router fails closed on unauthenticated requests returning 401 AUTH_REQUIRED."""
    app = FastAPI()
    app.include_router(create_management_read_models_router())
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
    app.include_router(create_management_read_models_router(get_read_store=lambda: None))
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
    """Verify create_management_read_models_router registers all 17 target GET routes plus 5 composed read models."""
    from test_normalized_route_uniqueness import scan_fastapi_routes

    app = FastAPI()
    app.include_router(create_management_read_models_router(get_read_store=lambda: MockManagementReadStore()))

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
        assert len(matching) == 1, (
            f"Expected exactly 1 GET registration for {path}, found {len(matching)}: {matching}"
        )
        entry = matching[0]
        assert "management_read_models.router" in entry.owner_module, (
            f"Expected {path} to be owned by management_read_models.router, but owned by {entry.owner_module}"
        )

    composed_routes = [
        "/bff/management/formula-jobs",
        "/bff/management/activity",
        "/bff/management/paper-telemetry",
        "/bff/management/postmortems",
        "/bff/management/postmortems/{postmortem_id}",
    ]
    for path in composed_routes:
        matching = [
            entry for entry in scanned_entries
            if entry.raw_path == path and entry.method == "GET"
        ]
        assert len(matching) == 1, (
            f"Expected exactly 1 GET registration for composed read model {path}, found {len(matching)}: {matching}"
        )


def test_operator_health_status_fail_closed_on_missing_ports():
    """Verify ManagementService health projection fails closed (unavailable) when store exposes no reader ports."""
    # 1. Store is object() exposing zero reader ports -> overall_status=unavailable, all groups unavailable
    unsupported_service = ManagementService(get_read_store=lambda: object())
    health_unsupported = unsupported_service.get_operator_health_status()
    assert health_unsupported["overall_status"] == "unavailable"
    assert health_unsupported["group_counts"]["unavailable"] == 5
    assert health_unsupported["group_counts"]["ok"] == 0
    for group in health_unsupported["groups"]:
        assert group["status"] == "unavailable", f"Expected group {group['group_id']} status unavailable, got {group['status']}"
        assert health_unsupported["meta"]["surfaces"][group["group_id"]]["status"] == "unavailable"

    # 2. Store is None -> overall_status=unavailable, all groups unavailable
    none_service = ManagementService(get_read_store=lambda: None)
    health_none = none_service.get_operator_health_status()
    assert health_none["overall_status"] == "unavailable"
    assert health_none["group_counts"]["unavailable"] == 5
    assert health_none["group_counts"]["ok"] == 0
    for group in health_none["groups"]:
        assert group["status"] == "unavailable"

    # 3. Store is mock store with all reader ports -> all contributing group surfaces report ok
    full_service = ManagementService(get_read_store=lambda: MockManagementReadStore())
    health_full = full_service.get_operator_health_status()
    for gid in ("runtime", "telemetry", "incident", "governance", "kill_switch"):
        assert health_full["meta"]["surfaces"][gid]["status"] == "ok"

    # 4. Clean mock store with 0 pending approvals and 0 active incidents -> overall_status=ok, all 5 groups ok
    class CleanMockStore(MockManagementReadStore):
        def __init__(self):
            super().__init__()
            self.approval_records = []
            self.incident_alerts = []

    clean_service = ManagementService(get_read_store=lambda: CleanMockStore())
    health_clean = clean_service.get_operator_health_status()
    assert health_clean["overall_status"] == "ok"
    assert health_clean["group_counts"]["ok"] == 5
    assert health_clean["group_counts"]["unavailable"] == 0
    for group in health_clean["groups"]:
        assert group["status"] == "ok"


def test_degraded_control_guidance_contract():
    """Verify GET /api/v1/operator/degraded-control-guidance preserves 200/206 and {data, meta.staleness} envelope."""
    # 1. Fresh state via router builder injection
    def fresh_builder():
        return JSONResponse(
            status_code=200,
            content={
                "data": {
                    "current_state": "fresh",
                    "primary_path": {"status": "available"},
                    "secondary_path": ["admin_cli", "protected_internal_api"],
                    "critical_actions_bypass_mfa": True,
                },
                "meta": {
                    "snapshot_at": "2026-08-30T10:00:00Z",
                    "staleness": {"state": "fresh", "stale": False},
                },
            },
        )

    app_fresh = FastAPI()
    app_fresh.include_router(create_management_read_models_router(
        degraded_control_guidance_builder=fresh_builder,
    ))
    client_fresh = TestClient(app_fresh)
    resp = client_fresh.get("/api/v1/operator/degraded-control-guidance")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert "staleness" in body["meta"]
    assert body["data"]["current_state"] == "fresh"

    # 2. Degraded state via router builder injection
    def degraded_builder():
        return JSONResponse(
            status_code=206,
            content={
                "data": {
                    "current_state": "degraded",
                    "primary_path": {"status": "degraded"},
                    "secondary_path": ["admin_cli", "protected_internal_api"],
                    "critical_actions_bypass_mfa": True,
                },
                "meta": {
                    "snapshot_at": "2026-08-30T10:00:00Z",
                    "staleness": {"state": "degraded", "stale": True},
                },
            },
        )

    app_deg = FastAPI()
    app_deg.include_router(create_management_read_models_router(
        degraded_control_guidance_builder=degraded_builder,
    ))
    client_deg = TestClient(app_deg)
    resp_deg = client_deg.get("/api/v1/operator/degraded-control-guidance")
    assert resp_deg.status_code == 206
    body_deg = resp_deg.json()
    assert "data" in body_deg and "meta" in body_deg
    assert "staleness" in body_deg["meta"]
    assert body_deg["data"]["current_state"] == "degraded"

    # 3. Default ManagementService guidance fallback
    app_default = FastAPI()
    app_default.include_router(create_management_read_models_router(get_read_store=lambda: None))
    client_default = TestClient(app_default)
    resp_default = client_default.get("/api/v1/operator/degraded-control-guidance")
    assert resp_default.status_code in (200, 206)
    body_def = resp_default.json()
    assert "data" in body_def and "meta" in body_def
    assert "staleness" in body_def["meta"]


def test_real_bff_main_app_management_route_ownership():
    """Verify in the real main.app, each of the 17 target GET routes has exactly one registration owned by management_read_models.router."""
    import main as real_main
    from test_normalized_route_uniqueness import scan_fastapi_routes

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

    scanned_entries = scan_fastapi_routes(real_main.app)
    for path in target_routes:
        matching = [
            entry for entry in scanned_entries
            if entry.raw_path == path and entry.method == "GET"
        ]
        assert len(matching) == 1, (
            f"Expected exactly 1 GET registration for {path}, found {len(matching)}: {matching}"
        )
        entry = matching[0]
        assert "management_read_models.router" in entry.owner_module, (
            f"Expected {path} to be owned by management_read_models.router, but owned by {entry.owner_module}"
        )


def test_real_bff_main_app_degraded_control_guidance_contract(monkeypatch):
    """Verify GET /api/v1/operator/degraded-control-guidance on real main.app preserves 200/206 and {data, meta.staleness} envelope."""
    import main as real_main

    client = TestClient(real_main.app, raise_server_exceptions=False)

    # 1. Fresh state -> 200 with canonical {data, meta.staleness} envelope
    monkeypatch.setattr(real_main, "_read_surface_state", lambda: "fresh")
    resp = client.get("/api/v1/operator/degraded-control-guidance")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert "staleness" in body["meta"]
    guidance = body["data"]
    assert guidance["current_state"] == "fresh"
    assert guidance["primary_path"]["status"] == "available"
    assert "admin_cli" in guidance["secondary_path"]
    assert "protected_internal_api" in guidance["secondary_path"]
    assert guidance["critical_actions_bypass_mfa"] is True

    # 2. Degraded state -> 206 with canonical {data, meta.staleness} envelope
    monkeypatch.setattr(real_main, "_read_surface_state", lambda: "degraded")
    resp_degraded = client.get("/api/v1/operator/degraded-control-guidance")
    assert resp_degraded.status_code == 206
    body_degraded = resp_degraded.json()
    assert "data" in body_degraded and "meta" in body_degraded
    assert "staleness" in body_degraded["meta"]
    guidance_degraded = body_degraded["data"]
    assert guidance_degraded["current_state"] == "degraded"
    assert guidance_degraded["primary_path"]["status"] == "degraded"
    assert "admin_cli" in guidance_degraded["secondary_path"]
    assert "protected_internal_api" in guidance_degraded["secondary_path"]
    assert guidance_degraded["critical_actions_bypass_mfa"] is True
