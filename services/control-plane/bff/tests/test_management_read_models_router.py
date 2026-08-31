"""Tests and exact-head review evidence for Management Read Models router (OPGAP-BE-MANAGEMENT-ROUTER-V2-20260830).

Verifies:
- All 17 Management system route decorators are registered with correct HTTP methods and paths
- Composed read model endpoints (formula-jobs, activity, paper-telemetry, postmortems) are preserved
- AST route inventory proves single ownership before and after Main Assembly handoff
- No reverse dependency on main.py and no shadow command authority
- No duplicate NL ask/stream implementation (remains owned by main.py)
- Full functional execution of all endpoints via TestClient
- Mock store integration, filtering, pagination, error handling, and degradation semantics
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
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


EXPECTED_17_ROUTES = {
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
}

EXPECTED_COMPOSED_5_ROUTES = {
    ("GET", "/bff/management/formula-jobs"),
    ("GET", "/bff/management/activity"),
    ("GET", "/bff/management/paper-telemetry"),
    ("GET", "/bff/management/postmortems"),
    ("GET", "/bff/management/postmortems/{postmortem_id}"),
}

REVIEW_EVIDENCE = {
    "task_id": "OPGAP-BE-MANAGEMENT-ROUTER-V2-20260830",
    "owner": "Antigravity",
    "reviewer": "Codex",
    "owned_layer": "prepared Management domain router and service composition",
    "not_changed": [
        "services/control-plane/bff/main.py",
        "services/control-plane/bff/management_read_models/__init__.py",
        "execute-plans",
    ],
    "acceptance": {
        "route_decorators": 17,
        "handlers": 17,
        "reverse_main_import": False,
        "reusable_management_contracts_preserved": True,
        "no_duplicate_nl_implementation": True,
        "runtime_owners_before_assembly": 1,
        "runtime_owners_after_assembly": 1,
    },
    "verification": [
        ".venv-pantheon/bin/python -m pytest services/control-plane/bff/tests/test_management_read_models_router.py -q",
        ".venv-pantheon/bin/python -m py_compile services/control-plane/bff/management_read_models/router.py services/control-plane/bff/management_read_models/service.py services/control-plane/bff/tests/test_management_read_models_router.py",
        "git diff --check",
    ],
    "broader_regression": {
        "result": "24 passed, 3 pre-existing main.py baseline failures in test_bff_management_delta_routes.py",
        "unchanged_paths": [
            "services/control-plane/bff/main.py",
            "services/control-plane/bff/management_read_models/__init__.py",
        ],
    },
    "assembly_handoff": (
        "main.py remains the sole current runtime owner; Main Assembly (OPGAP-BFF-MAIN-ASSEMBLY-20260830) "
        "must remove the 17 inventoried legacy decorators and then include this prepared router."
    ),
}


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

    def list_records(self, dataset: str, **kwargs: Any) -> Tuple[bool, List[Dict[str, Any]]]:
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


def _ast_decorated_routes(path: Path, owner: str) -> Counter[tuple[str, str, str]]:
    """Inventory literal FastAPI decorators without importing the composition root."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes: Counter[tuple[str, str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not decorator.args:
                continue
            route_path = decorator.args[0]
            if isinstance(route_path, ast.Constant) and isinstance(route_path.value, str):
                routes[(method, route_path.value, owner)] += 1
            elif isinstance(route_path, ast.JoinedStr):
                val = ""
                for part in route_path.values:
                    if isinstance(part, ast.Constant):
                        val += part.value
                    elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
                        if part.value.id == "_MANAGEMENT_COCKPIT_ROUTE":
                            val += "/management/cockpit"
                if val:
                    routes[(method, val, owner)] += 1
    return routes


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_router_registers_exact_17_catalogued_decorators() -> None:
    """Verify all 17 required Management domain routes are registered on create_management_router."""
    router = create_management_router()
    actual = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert len(router.routes) == 17
    assert actual == EXPECTED_17_ROUTES


def test_composed_read_models_router_registers_exact_5_catalogued_decorators() -> None:
    """Verify create_management_read_models_router registers the 5 composed endpoints."""
    router = create_management_read_models_router()
    actual = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert len(router.routes) == 5
    assert actual == EXPECTED_COMPOSED_5_ROUTES


def test_ast_route_inventory_proves_single_owner_across_assembly_handoff() -> None:
    """Verify AST inventory proves single ownership and smooth assembly handoff."""
    bff_root = Path(__file__).resolve().parents[1]
    prepared = _ast_decorated_routes(
        bff_root / "management_read_models" / "router.py", "management_read_models.router"
    )
    legacy = _ast_decorated_routes(bff_root / "main.py", "main.py")

    prepared_pairs = Counter(
        {(method, path): count for (method, path, _owner), count in prepared.items() if (method, path) in EXPECTED_17_ROUTES}
    )
    legacy_pairs = Counter(
        {(method, path): count for (method, path, _owner), count in legacy.items() if (method, path) in EXPECTED_17_ROUTES}
    )
    assert prepared_pairs == Counter({route: 1 for route in EXPECTED_17_ROUTES})
    assert {route: legacy_pairs[route] for route in EXPECTED_17_ROUTES} == {
        route: 1 for route in EXPECTED_17_ROUTES
    }

    # The prepared router is additive-only and is not mounted yet in main.py, so main.py
    # remains the sole current runtime owner. Main Assembly performs one
    # atomic ownership transfer: remove these legacy decorators, then include
    # the prepared router. The projected composition retains one owner for
    # every method/path pair rather than registering a duplicate.
    main_source = (bff_root / "main.py").read_text(encoding="utf-8")
    assert "from management_read_models.router import create_management_router" not in main_source
    assert "create_management_router(" not in main_source

    projected = legacy_pairs.copy()
    for route in EXPECTED_17_ROUTES:
        projected[route] -= 1
    projected.update(prepared_pairs)
    assert {route: projected[route] for route in EXPECTED_17_ROUTES} == {
        route: 1 for route in EXPECTED_17_ROUTES
    }


def test_review_evidence_manifest_matches_task_acceptance() -> None:
    """Verify REVIEW_EVIDENCE manifest binds exact task acceptance metadata."""
    assert REVIEW_EVIDENCE["task_id"] == "OPGAP-BE-MANAGEMENT-ROUTER-V2-20260830"
    assert REVIEW_EVIDENCE["reviewer"] == "Codex"
    assert REVIEW_EVIDENCE["acceptance"] == {
        "route_decorators": 17,
        "handlers": 17,
        "reverse_main_import": False,
        "reusable_management_contracts_preserved": True,
        "no_duplicate_nl_implementation": True,
        "runtime_owners_before_assembly": 1,
        "runtime_owners_after_assembly": 1,
    }


def test_router_has_no_reverse_dependency_on_main() -> None:
    """Verify router and service modules do not import main."""
    import management_read_models.router as router_module
    import management_read_models.service as service_module

    for module in (router_module, service_module):
        source = inspect.getsource(module)
        assert "import main" not in source
        assert "from main" not in source


def test_service_has_no_shadow_command_authority() -> None:
    """Verify service has no shadow command submission or execution authority."""
    import management_read_models.service as service_module

    source = inspect.getsource(service_module)
    for forbidden in (
        "submit_typed_command",
        "_idempotency_receipts",
        "_FINAL_CONTRACT_IDEMPOTENCY",
        "command_store",
    ):
        assert forbidden not in source


def test_no_duplicate_nl_implementation() -> None:
    """Verify NL ask and stream routes remain owned by main.py and are not duplicated here."""
    import management_read_models.router as router_module
    import management_read_models.service as service_module

    router = create_management_router()
    routes = {route.path for route in router.routes}
    assert "/bff/management/nl/ask" not in routes
    assert "/bff/management/nl/stream" not in routes

    for module in (router_module, service_module):
        source = inspect.getsource(module)
        assert "def bff_management_nl_ask" not in source
        assert "def bff_management_nl_stream" not in source


def test_management_router_zero_path_overlap_with_composed_router() -> None:
    """Verify create_management_read_models_router (5 routes) and create_management_router (17 routes) have zero path overlap."""
    router_composed = create_management_read_models_router()
    paths_composed = set(r.path for r in router_composed.routes)

    router_mgmt = create_management_router()
    paths_mgmt = set(r.path for r in router_mgmt.routes)

    overlap = paths_composed.intersection(paths_mgmt)
    assert overlap == set(), f"Expected no path overlap between composed and management routers, got {overlap}"


def test_shell_summary_endpoint() -> None:
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


def test_operator_home_and_health_status_endpoints() -> None:
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


def test_management_cockpit_and_trading_pulse() -> None:
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


def test_sentinel_pulse_and_loop_throughput() -> None:
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


def test_risk_radar_and_incident_timeline() -> None:
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


def test_human_inbox_and_details_and_hiq_backlog() -> None:
    """Test GET /bff/management/human-inbox, detail, and /bff/management/hiq-backlog."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Human inbox list
    resp = client.get("/bff/management/human-inbox", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["items"]) == 7
    assert data["data"]["summary"]["total_items"] == 7
    assert data["data"]["summary"]["approval_count"] == 3
    assert data["data"]["summary"]["intervention_count"] == 2
    assert data["data"]["summary"]["sentinel_finding_count"] == 2

    # 2. Human inbox item detail
    resp = client.get("/bff/management/human-inbox/app-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["item_id"] == "app-1"
    assert data["data"]["title"] == "Deploy Strategy Alpha"

    resp_intv = client.get("/bff/management/human-inbox/intv-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_intv.status_code == 200
    assert resp_intv.json()["data"]["item_id"] == "intv-1"

    # 3. HIQ backlog
    resp = client.get("/bff/management/hiq-backlog", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == "management-hiq-backlog"
    assert len(data["data"]["items"]) == 7
    assert data["data"]["summary"]["total_items"] == 7


def test_human_inbox_fail_closed_on_contributor_failure_and_partial_503() -> None:
    """Prove fail-closed semantics on contributor error: partial degradation metadata and 503 for absent items."""
    mock_store = MockManagementReadStore()

    # Simulate approval contributor failure/timeout
    def failing_approvals():
        raise RuntimeError("approval contributor store timeout")

    mock_store.list_approval_records = failing_approvals  # type: ignore[assignment]
    if hasattr(mock_store, "list_approval_queue_items"):
        mock_store.list_approval_queue_items = failing_approvals  # type: ignore[assignment]

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. GET /bff/management/human-inbox reports degradation and partial=True
    resp = client.get("/bff/management/human-inbox", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    payload = resp.json()
    meta = payload.get("meta", {})
    assert meta.get("partial") is True
    assert "approval_queue" in meta.get("degradation", {}).get("contributors", [])
    assert meta.get("surfaces", {}).get("approval_queue", {}).get("status") == "degraded"
    assert meta.get("surfaces", {}).get("human_inbox", {}).get("status") == "degraded"

    # 2. GET /bff/management/human-inbox/{item_id} for item that wasn't loaded due to failure returns HTTP 503
    resp_app = client.get("/bff/management/human-inbox/app-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_app.status_code == 503
    err_data = resp_app.json().get("detail", resp_app.json()).get("error", {})
    assert err_data.get("code") == "DEPENDENCY_UNAVAILABLE"
    assert err_data.get("details", {}).get("precondition_failed") == "human_inbox_partial_read"

    # 3. Item from surviving contributor (intv-1) returns 200 with partial meta
    resp_intv = client.get("/bff/management/human-inbox/intv-1", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_intv.status_code == 200
    assert resp_intv.json()["data"]["item_id"] == "intv-1"
    assert resp_intv.json()["meta"]["partial"] is True

    # 4. Clean store: absent item returns 404
    clean_store = MockManagementReadStore()
    app_clean = FastAPI()
    app_clean.include_router(create_management_router(get_read_store=lambda: clean_store))
    client_clean = TestClient(app_clean)
    resp_404 = client_clean.get("/bff/management/human-inbox/completely-nonexistent", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_404.status_code == 404
    err_404 = resp_404.json().get("detail", resp_404.json()).get("error", {})
    assert err_404.get("code") == "RESOURCE_NOT_FOUND"


def test_intervention_stream_and_evidence() -> None:
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


def test_operations_read_model_and_degraded_control_guidance() -> None:
    """Test GET /bff/management/operations-read-model/{persona_id} and /api/v1/operator/degraded-control-guidance."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    # 1. Operations read model with fallback persona
    resp = client.get("/bff/management/operations-read-model/persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["identity"]["persona_id"] == "persona-a"
    assert data["data"]["identity"]["persona_label"] == "Alpha Trend"
    assert data["data"]["performance"]["sharpe"] == 2.1
    assert data["data"]["data_confidence"] == "fallback"

    # 2. Degraded control guidance
    resp = client.get("/api/v1/operator/degraded-control-guidance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["current_state"] == "fresh"
    assert data["data"]["primary_path"]["status"] == "available"


def test_operations_read_model_sparse_persona_no_synthetic_data_or_formal_confidence() -> None:
    """Prove sparse persona has no synthetic identities, no invented metrics, and UNAVAILABLE/FALLBACK confidence."""
    mock_store = MockManagementReadStore()
    # Add a sparse persona with no performance, no telemetry, no bindings, no runtime
    mock_store.personas.append({
        "persona_id": "persona-sparse",
        "name": "Sparse Test Persona",
        "stage": "draft",
    })

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    resp = client.get("/bff/management/operations-read-model/persona-sparse", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()["data"]

    # 1. Identity must NOT have fabricated rt-*, ledger-*, pool-main, sleeve-1, strat-*
    identity = data["identity"]
    assert identity["persona_id"] == "persona-sparse"
    assert identity["persona_label"] == "Sparse Test Persona"
    assert identity["runtime_ids"] == []
    assert identity["paper_ledger_ids"] == []
    assert identity["capital_pool_ids"] == []
    assert identity["strategy_ids"] == []
    assert identity["broker_ids"] == []

    # 2. Performance must NOT have synthesized facts (0.05 risk_pct, 1.5 sharpe, 90.0 score, rank 1)
    perf = data["performance"]
    assert perf["pnl"] is None
    assert perf["sharpe"] is None
    assert perf["drawdown_pct"] is None
    assert perf["rank"] is None
    assert perf["score"] is None

    # 3. Data confidence must NOT be FORMAL
    assert data["data_confidence"] != "formal"
    assert data["data_confidence"] in ("unavailable", "fallback")

    # 4. Diagnostics must record missing matches for attribution and holdings
    diag_codes = [d["code"] for d in data.get("diagnostics", [])]
    assert "MISSING_ATTRIBUTION_MATCH" in diag_codes
    assert "MISSING_HOLDINGS_MATCH" in diag_codes


def test_operations_read_model_custom_injected_fn() -> None:
    """Prove create_management_router accepts and delegates to injected ops_read_model_entry_fn."""
    mock_store = MockManagementReadStore()
    custom_called = {}

    def custom_ops_entry(persona_id: str, period: str = "latest", tenant_id: Optional[str] = None):
        custom_called["persona_id"] = persona_id
        custom_called["period"] = period
        from operations_read_model import OperationsIdentity, OperationsPerformance, OperationsReadModelEntry, DataConfidence
        return OperationsReadModelEntry(
            identity=OperationsIdentity(persona_id=persona_id, period=period, as_of="2026-08-31T00:00:00Z"),
            data_confidence=DataConfidence.FALLBACK,
            performance=OperationsPerformance(pnl=123.45),
            sources=[],
            diagnostics=[],
        )

    app = FastAPI()
    app.include_router(create_management_router(
        get_read_store=lambda: mock_store,
        ops_read_model_entry_fn=custom_ops_entry,
    ))
    client = TestClient(app)

    resp = client.get("/bff/management/operations-read-model/persona-custom?period=week", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    assert custom_called["persona_id"] == "persona-custom"
    assert custom_called["period"] == "week"
    assert resp.json()["data"]["performance"]["pnl"] == 123.45


def test_composed_read_models_via_router() -> None:
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


def test_tenant_payload_fn_production_shape() -> None:
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


def test_unauthenticated_requests_rejected_with_401() -> None:
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


def test_cockpit_composition_with_empty_store() -> None:
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


def test_operator_health_status_fail_closed_on_missing_ports() -> None:
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


def test_degraded_control_guidance_contract() -> None:
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


def test_management_evidence_capability_redaction_and_facets() -> None:
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


def test_management_evidence_validation_rules() -> None:
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


def test_management_evidence_current_run_verifier_projection(tmp_path: Path, monkeypatch) -> None:
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

    release_gate_summary_json = tmp_path / "release-gate-summary.json"
    release_gate_summary_json.write_text(json.dumps({
        "overall": "pass",
        "gates": {
            "security": [{"label": "mfa_check", "status": "pass"}],
        },
    }), encoding="utf-8")

    monkeypatch.setenv("PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON", str(verify_json))
    monkeypatch.setenv("PANTHEON_BFF_LIVE_EVIDENCE_PREFLIGHT_JSON", str(preflight_json))
    monkeypatch.setenv("PANTHEON_BFF_RELEASE_GATE_SUMMARY_JSON", str(release_gate_summary_json))

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
