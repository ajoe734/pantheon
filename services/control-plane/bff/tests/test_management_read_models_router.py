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
    assert "system_kpis" in data["data"]
    assert "cards" in data["data"]

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
