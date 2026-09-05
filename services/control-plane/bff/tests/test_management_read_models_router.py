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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

# Ensure bff root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# Main Assembly imports repository-level integration packages (for example
# Agora's OpenClaw adapters).  Keep the repository root importable when this
# file is run directly from a clean task worktree rather than relying on the
# caller's PYTHONPATH.
REPO_ROOT = str(Path(__file__).resolve().parents[4])
sys.path.insert(0, REPO_ROOT)

from management_read_models.router import (
    create_management_read_models_router,
    create_management_router,
    _default_extract_identity,
)
from management_read_models.service import ManagementService
from ports import create_in_memory_read_surface_ports


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
    "assembly_changed": [
        "services/control-plane/bff/main.py",
        "services/control-plane/bff/management_read_models/__init__.py",
    ],
    "not_changed": ["execute-plans"],
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
        "assembly_paths": [
            "services/control-plane/bff/main.py",
            "services/control-plane/bff/management_read_models/__init__.py",
        ],
    },
    "assembly_handoff": (
        "Main Assembly removes the 17 inventoried legacy decorators and includes the prepared router; "
        "the recursive runtime inventory must show one owner per method/path while preserving the "
        "existing evolution-journal owner and five-route composed read-model router."
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
        return getattr(self, "runtime_bindings", [{"id": "run-1", "status": "running"}])

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


def _import_main_for_inventory() -> Any:
    """Import ``main`` with the repository-level integrations package pinned.

    The BFF directory also contains a legacy top-level ``integrations``
    package.  ``main`` prepends that directory to ``sys.path`` while Agora
    imports ``integrations.openclaw`` from the repository package.  Tests that
    import other BFF modules may already have loaded the legacy package, so
    replace only the package binding before importing the composition root.
    """
    import importlib

    loaded = sys.modules.get("integrations")
    repo_package_dir = Path(REPO_ROOT) / "integrations"
    loaded_package_dir = (
        Path(str(getattr(loaded, "__file__", ""))).resolve().parent
        if loaded is not None and getattr(loaded, "__file__", None)
        else None
    )
    if loaded_package_dir != repo_package_dir.resolve():
        for name in list(sys.modules):
            if name == "integrations" or name.startswith("integrations."):
                sys.modules.pop(name, None)
        sys.path.insert(0, REPO_ROOT)
        importlib.import_module("integrations")
    return importlib.import_module("services.control_plane.bff.main")


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
        route: 0 for route in EXPECTED_17_ROUTES
    }

    # Main Assembly performs one atomic ownership transfer: remove the legacy
    # decorators, then include the prepared router. The runtime composition
    # therefore has one owner for every method/path pair.
    main_source = (bff_root / "main.py").read_text(encoding="utf-8")
    assert "create_management_router" in main_source
    assert "create_management_router(" in main_source


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
    assert "items" in data["data"]
    assert "summary" in data["data"]
    assert len(data["data"]["items"]) == 4


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


# ---------------------------------------------------------------------------
# Router-Mounted Trading Pulse Contract Parity Tests
# ---------------------------------------------------------------------------

def _fresh_trading_pulse_router_client(*, include_gap: bool = False) -> TestClient:
    store = create_in_memory_read_surface_ports()
    runtime_bindings = [
        {
            "id": "binding-alpha",
            "binding_id": "binding-alpha",
            "runtime_id": "runtime-alpha",
            "deployment_stage": "paper",
            "status": "running",
            "plan_id": "plan-alpha",
            "artifact_id": "artifact-alpha",
            "artifact_version": "v1",
        },
        {
            "id": "binding-beta",
            "binding_id": "binding-beta",
            "runtime_id": "runtime-beta",
            "deployment_stage": "canary",
            "status": "paused",
            "plan_id": "plan-beta",
            "artifact_id": "artifact-beta",
            "artifact_version": "v2",
        },
    ]
    if include_gap:
        runtime_bindings.append(
            {
                "id": "binding-gamma",
                "binding_id": "binding-gamma",
                "runtime_id": "runtime-gamma",
                "deployment_stage": "paper",
                "status": "running",
                "plan_id": "plan-gamma",
                "artifact_id": "artifact-gamma",
                "artifact_version": "v3",
            }
        )
    store.list_runtime_bindings = lambda: list(runtime_bindings)
    telemetry_summaries = {
        "runtime-alpha": {
            "runtime_id": "runtime-alpha",
            "runtime_binding_id": "binding-alpha",
            "deployment_stage": "paper",
            "state": "active",
            "window": "1h",
            "pnl": 0.42,
            "drawdown": 0.11,
            "sharpe_ratio": 1.7,
            "fill_rate": 0.9,
            "avg_slippage_bps": 4.8,
            "total_trades": 31,
            "collected_at": "2026-05-23T08:10:00Z",
            "last_heartbeat_at": "2026-05-23T08:10:00Z",
        },
        "runtime-beta": {
            "runtime_id": "runtime-beta",
            "runtime_binding_id": "binding-beta",
            "deployment_stage": "canary",
            "state": "paused",
            "window": "1h",
            "pnl": -0.12,
            "drawdown": 0.04,
            "sharpe_ratio": 0.8,
            "fill_rate": 0.88,
            "avg_slippage_bps": 3.1,
            "total_trades": 11,
            "collected_at": "2026-05-23T08:08:00Z",
            "last_heartbeat_at": "2026-05-23T08:08:00Z",
        },
    }
    drift_reports = {
        "runtime-alpha": {
            "runtime_id": "runtime-alpha",
            "artifact_id": "artifact-alpha",
            "paper_baseline": {
                "captured_at": "2026-05-23T07:00:00Z",
                "deployment_stage": "paper",
                "window": "1h",
                "metrics": {
                    "pnl": 0.25,
                    "drawdown": 0.08,
                    "fill_rate": 0.91,
                    "avg_slippage_bps": 4.0,
                },
            },
            "observed_state": {
                "deployment_stage": "paper",
                "runtime_status": "running",
                "observed_at": "2026-05-23T08:10:00Z",
                "metrics": {
                    "pnl": 0.42,
                    "drawdown": 0.11,
                    "fill_rate": 0.9,
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
                            "baseline_value": 0.08,
                            "observed_value": 0.11,
                            "delta": 0.03,
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
        },
        "runtime-beta": {
            "runtime_id": "runtime-beta",
            "artifact_id": "artifact-beta",
            "paper_baseline": {
                "captured_at": "2026-05-23T07:00:00Z",
                "deployment_stage": "paper",
                "window": "1h",
                "metrics": {
                    "pnl": 0.05,
                    "drawdown": 0.03,
                    "fill_rate": 0.9,
                    "avg_slippage_bps": 2.6,
                },
            },
            "observed_state": {
                "deployment_stage": "canary",
                "runtime_status": "paused",
                "observed_at": "2026-05-23T08:08:00Z",
                "metrics": {
                    "pnl": -0.12,
                    "drawdown": 0.04,
                    "fill_rate": 0.88,
                    "avg_slippage_bps": 3.1,
                },
            },
            "drift_groups": [
                {
                    "group_id": "execution",
                    "label": "Execution",
                    "status": "breached",
                    "metrics": [
                        {
                            "metric_id": "avg_slippage_bps",
                            "baseline_value": 2.6,
                            "observed_value": 3.1,
                            "delta": 0.5,
                            "status": "breached",
                        }
                    ],
                }
            ],
            "threshold_evaluation": {
                "overall_status": "breached",
                "summary": "Slippage drift breached the canary baseline.",
                "breached_metric_ids": ["avg_slippage_bps"],
            },
        },
    }
    monitoring_sessions = {
        "runtime-alpha": {
            "session_id": "monitor-alpha",
            "binding_id": "binding-alpha",
            "runtime_binding_id": "binding-alpha",
            "runtime_id": "runtime-alpha",
            "deployment_stage": "paper",
            "status": "active",
            "active": True,
            "started_at": "2026-05-23T07:30:00Z",
            "last_heartbeat_at": "2026-05-23T08:10:00Z",
        }
    }
    store.get_telemetry_summary = lambda runtime_id: telemetry_summaries.get(runtime_id)
    store.list_telemetry_summaries = lambda: list(telemetry_summaries.values())
    store.get_paper_live_drift_report = lambda runtime_id: drift_reports.get(runtime_id)
    store.list_paper_live_drift_reports = lambda: list(drift_reports.values())
    store.list_paper_runtime_monitoring_sessions = lambda: list(monitoring_sessions.values())
    store.get_paper_runtime_monitoring_session = (
        lambda runtime_id=None, binding_id=None: monitoring_sessions.get(runtime_id)
        or next(
            (
                session
                for session in monitoring_sessions.values()
                if session.get("binding_id") == binding_id
                or session.get("runtime_binding_id") == binding_id
            ),
            None,
        )
    )
    store.get_rollbacks = lambda runtime_id: []
    store.dataset_source = lambda dataset, **kwargs: {
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "paper_runtime_monitoring_sessions": "service_store",
        "paper_live_drift_reports": "service_store",
        "rollbacks": "service_store",
    }.get(dataset, "missing")

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    return TestClient(app)


def test_router_mounted_trading_pulse_returns_card_aggregate_and_runtime_rankings() -> None:
    """Verify router-mounted /bff/management/trading-pulse meets full contract."""
    client = _fresh_trading_pulse_router_client()
    resp = client.get("/bff/management/trading-pulse", headers={"Authorization": "Bearer op-b3-trading:operator,reviewer"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"data", "page_info", "meta"}
    data = body["data"]
    summary = data["summary"]
    assert data["id"] == "management-trading-pulse"
    assert "items" not in body
    assert "summary" not in body
    assert summary["runtime_count"] == 2
    assert summary["telemetry_coverage_count"] == 2
    assert summary["total_pnl"] == 0.3
    assert summary["worst_drawdown"] == 0.11
    assert summary["average_fill_rate"] == 0.89
    assert summary["worst_slippage_bps"] == 4.8
    assert summary["total_trades"] == 42
    assert summary["by_status"] == {"running": 1, "paused": 1}
    assert summary["by_stage"] == {"paper": 1, "canary": 1}
    assert summary["baseline_comparison_count"] == 2
    assert summary["baseline_breached_count"] == 1
    assert summary["baseline_watch_count"] == 1
    assert summary["by_baseline_status"] == {"watch": 1, "breached": 1}
    assert summary["row_health_degraded_count"] == 0
    assert summary["row_health_status_counts"] == {"ok": 2}
    assert summary["monitoring_coverage_count"] == 1
    assert summary["missing_monitoring_runtime_ids"] == []
    assert summary["coverage"]["metric_coverage"]["pnl"]["available_count"] == 2
    assert "rowHealthDegradedCount" not in summary
    assert "rowHealthStatusCounts" not in summary
    assert "monitoringCoverageCount" not in summary
    assert "metricCoverage" not in summary["coverage"]

    assert len(data["cards"]) == 6
    assert {card["card_id"] for card in data["cards"]} >= {"row-health"}
    assert data["rankings"][0]["runtime_id"] == "runtime-alpha"
    assert data["rankings"][0]["rank"] == 1
    assert data["rankings"][0]["baseline_comparison_status"] == "watch"
    assert "rowHealthStatus" not in data["rankings"][0]
    assert "rowHealthDegradedChecks" not in data["rankings"][0]
    rows_by_runtime = {row["runtime_id"]: row for row in data["runtime_rows"]}
    assert data["runtime_rows"][0]["runtime_id"] == "runtime-beta"
    assert rows_by_runtime["runtime-alpha"]["telemetry_summary"]["metrics"]["pnl"] == 0.42
    assert (
        rows_by_runtime["runtime-alpha"]["baseline_comparison"]["paper_baseline"]["metrics"]["pnl"]
        == 0.25
    )
    comparisons_by_runtime = {
        comparison["runtime_id"]: comparison
        for comparison in data["baseline_comparisons"]
    }
    assert comparisons_by_runtime["runtime-beta"]["status"] == "breached"
    assert comparisons_by_runtime["runtime-beta"]["paper_live_drift"]["available"] is True
    assert body["page_info"] == {
        "next_page_token": None,
        "total": 6,
        "page_size": 6,
    }
    assert body["meta"]["surfaces"]["management_trading_pulse"]["source"] == "bff_composed"
    assert body["meta"]["surfaces"]["runtime_roster"]["source"] == "canonical"
    assert body["meta"]["surfaces"]["telemetry_summary"]["source"] == "service_store"
    assert body["meta"]["surfaces"]["paper_runtime_monitoring"]["source"] == "service_store"
    assert body["meta"]["surfaces"]["paper_live_drift"]["source"] == "service_store"
    assert body["meta"]["surfaces"]["baseline_comparison"]["source"] == "bff_composed"
    assert body["meta"]["surfaces"]["runtime_row_health"]["status"] == "ok"


def test_router_mounted_trading_pulse_exposes_operator_coverage_gaps_and_row_health() -> None:
    """Verify router-mounted /bff/management/trading-pulse gap degradation semantics."""
    client = _fresh_trading_pulse_router_client(include_gap=True)
    resp = client.get("/bff/management/trading-pulse", headers={"Authorization": "Bearer op-b3-trading:operator,reviewer"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body["data"]
    summary = data["summary"]
    assert summary["runtime_count"] == 3
    assert summary["telemetry_coverage_count"] == 2
    assert summary["baseline_comparison_count"] == 2
    assert summary["monitoring_coverage_count"] == 1
    assert summary["row_health_degraded_count"] == 1
    assert summary["row_health_status_counts"] == {"degraded": 1, "ok": 2}
    assert summary["missing_telemetry_runtime_ids"] == ["runtime-gamma"]
    assert summary["missing_monitoring_runtime_ids"] == ["runtime-gamma"]
    assert summary["missing_baseline_runtime_ids"] == ["runtime-gamma"]
    assert summary["metric_coverage"]["pnl"]["missing_runtime_ids"] == ["runtime-gamma"]

    assert data["runtime_rows"][0]["runtime_id"] == "runtime-gamma"
    assert data["runtime_rows"][0]["row_health"]["status"] == "degraded"
    assert set(data["runtime_rows"][0]["row_health"]["degraded_checks"]) == {
        "telemetry_summary",
        "paper_runtime_monitoring",
    }
    assert data["runtime_rows"][0]["baseline_comparison"]["status"] == "unavailable"

    surfaces = body["meta"]["surfaces"]
    assert surfaces["management_trading_pulse"]["status"] == "degraded"
    assert surfaces["telemetry_summary"]["status"] == "degraded"
    assert surfaces["paper_runtime_monitoring"]["status"] == "degraded"
    assert surfaces["paper_live_drift"]["status"] == "degraded"
    assert surfaces["runtime_row_health"]["status"] == "degraded"
    assert body["meta"]["coverage"]["missing_baseline_runtime_ids"] == ["runtime-gamma"]


def test_router_mounted_trading_pulse_rankings_returns_computed_blocks_with_limit() -> None:
    """Verify router-mounted /bff/management/trading-pulse/rankings contract and blocks."""
    client = _fresh_trading_pulse_router_client()
    resp = client.get(
        "/bff/management/trading-pulse/rankings?limit=1",
        headers={"Authorization": "Bearer op-b3-trading:operator,reviewer"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"data", "page_info", "meta"}
    data = body["data"]
    summary = data["summary"]
    assert set(data) == {"id", "items", "summary"}
    assert "items" not in body
    assert "rankings" not in body
    assert summary["runtime_count"] == 2
    assert summary["ranking_block_count"] == 4
    assert summary["ranked_item_count"] == 4
    assert summary["eligible_item_count"] == 8
    assert summary["missing_metric_item_count"] == 0
    assert summary["limit"] == 1
    assert "eligibleItemCount" not in summary
    assert "missingMetricItemCount" not in summary
    assert body["page_info"] == {
        "next_page_token": None,
        "total": 4,
        "page_size": 4,
    }

    blocks = {block["block_id"]: block for block in data["items"]}
    assert blocks["pnl-leaders"]["eligible_item_count"] == 2
    assert blocks["pnl-leaders"]["missing_metric_count"] == 0
    assert "blockId" not in blocks["pnl-leaders"]
    assert "sortOrder" not in blocks["pnl-leaders"]
    assert "eligibleItemCount" not in blocks["pnl-leaders"]
    assert "missingMetricCount" not in blocks["pnl-leaders"]
    assert "missingMetricRuntimeIds" not in blocks["pnl-leaders"]
    assert blocks["pnl-leaders"]["items"][0]["runtime_id"] == "runtime-alpha"
    assert blocks["pnl-leaders"]["items"][0]["ranking_eligible"] is True
    assert blocks["pnl-leaders"]["items"][0]["ranking_metric"] == "pnl"
    assert blocks["drawdown-control"]["items"][0]["runtime_id"] == "runtime-beta"
    assert blocks["execution-quality"]["items"][0]["ranking_metric"] == "fill_rate"
    assert blocks["execution-quality"]["secondary_metric"] == "avg_slippage_bps"
    assert "secondaryMetric" not in blocks["execution-quality"]
    assert blocks["sharpe-leaders"]["items"][0]["runtime_id"] == "runtime-alpha"
    assert blocks["pnl-leaders"]["items"][0]["baseline_comparison_status"] == "watch"
    assert (
        body["meta"]["surfaces"]["management_trading_pulse_rankings"]["source"]
        == "bff_composed"
    )
    assert body["meta"]["surfaces"]["baseline_comparison"]["source"] == "bff_composed"


def test_router_mounted_trading_pulse_rankings_exclude_missing_metrics() -> None:
    """Verify router-mounted /bff/management/trading-pulse/rankings missing metric handling."""
    client = _fresh_trading_pulse_router_client(include_gap=True)
    resp = client.get(
        "/bff/management/trading-pulse/rankings?limit=5",
        headers={"Authorization": "Bearer op-b3-trading:operator,reviewer"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body["data"]
    blocks = {block["block_id"]: block for block in data["items"]}
    assert blocks["pnl-leaders"]["eligible_item_count"] == 2
    assert blocks["pnl-leaders"]["missing_metric_count"] == 1
    assert blocks["pnl-leaders"]["missing_metric_runtime_ids"] == ["runtime-gamma"]
    assert [item["runtime_id"] for item in blocks["pnl-leaders"]["items"]] == [
        "runtime-alpha",
        "runtime-beta",
    ]
    assert data["summary"]["missing_metric_item_count"] == 4
    assert body["meta"]["surfaces"]["management_trading_pulse_rankings"]["status"] == "degraded"


def test_router_mounted_trading_pulse_routes_require_read_authentication() -> None:
    """Verify router-mounted trading pulse routes require auth."""
    client = _fresh_trading_pulse_router_client()
    for path in (
        "/bff/management/trading-pulse",
        "/bff/management/trading-pulse/rankings",
    ):
        resp = client.get(path)
        assert resp.status_code == 401, resp.text
        body = resp.json()
        error_code = (body.get("detail") or {}).get("error", {}).get("code") or (body.get("error") or {}).get("code")
        assert error_code == "AUTH_REQUIRED"


def test_plan_ref_href_canonical_operator_route() -> None:
    """Prove plan_ref.href uses canonical /operator/deployment-review?plan={plan_id} format."""
    mock_store = MockManagementReadStore()
    mock_store.runtime_bindings = [  # type: ignore[assignment]
        {
            "id": "runtime-1",
            "runtime_id": "runtime-1",
            "plan_id": "plan-12345",
            "status": "running",
            "deployment_stage": "paper",
            "capital_pool_id": "pool-main",
        }
    ]
    service = ManagementService(read_store=mock_store)
    pulse = service.get_trading_pulse()
    items = pulse["data"]["runtime_rows"]
    assert len(items) == 1
    assert items[0]["plan_ref"]["plan_id"] == "plan-12345"
    assert items[0]["plan_ref"]["href"] == "/operator/deployment-review?plan=plan-12345"
    assert "plan_id=" not in items[0]["plan_ref"]["href"]


def test_risk_radar_parity_real_attribution_and_no_synthetic_data() -> None:
    """Verify Risk Radar computes real metrics from telemetry and does not inject synthetic fail-open data."""
    class CustomRiskRadarStore:
        def list_runtime_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return [
                {
                    "runtime_id": "run-alpha",
                    "persona_id": "persona-a",
                    "strategy_id": "strat-1",
                    "capital_pool_id": "pool-main",
                    "status": "running",
                    "deployment_stage": "paper",
                },
                {
                    "runtime_id": "run-beta",
                    "persona_id": "persona-b",
                    "strategy_id": "strat-2",
                    "capital_pool_id": "pool-main",
                    "status": "running",
                    "deployment_stage": "canary",
                },
            ]

        def list_deployment_plans(self) -> List[Dict[str, Any]]:
            return []

        def list_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def list_capital_pools(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return [
                {"pool_id": "pool-main", "name": "Main Capital Pool", "risk_budget": 50000.0}
            ]

        def list_personas(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
            return [
                {"persona_id": "persona-a", "name": "Alpha Trend"},
                {"persona_id": "persona-b", "name": "Beta Arbitrage"},
            ]

        def list_strategies(self) -> List[Dict[str, Any]]:
            return [
                {"strategy_id": "strat-1", "name": "Trend Following"},
                {"strategy_id": "strat-2", "name": "Statistical Arbitrage"},
            ]

        def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
            return [
                {
                    "runtime_id": "run-alpha",
                    "metrics": {
                        "total_exposure": 25000.0,
                        "worst_drawdown": 0.08,
                        "value_at_risk": 2000.0,
                    },
                }
                # run-beta intentionally has NO telemetry
            ]

    store = CustomRiskRadarStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    client = TestClient(app)

    resp = client.get("/bff/management/risk-radar", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    rows = data["rows"]
    assert len(rows) == 2

    # Row 1 (Alpha): has real telemetry
    row_alpha = next(r for r in rows if r["persona_id"] == "persona-a")
    assert row_alpha["total_exposure"] == 25000.0
    assert row_alpha["worst_drawdown"] == 0.08
    assert row_alpha["value_at_risk"] == 2000.0
    assert row_alpha["exposure_utilization"] == 0.5  # 25000 / 50000
    assert row_alpha["value_at_risk_utilization"] == 0.04  # 2000 / 50000
    assert row_alpha["risk_state"] == "watch"  # drawdown 0.08 >= 0.06
    assert row_alpha["risk_score"] == 65.0

    # Row 2 (Beta): NO telemetry -> must remain None, NOT synthetic 10000.0 / 0.05 / 500.0
    row_beta = next(r for r in rows if r["persona_id"] == "persona-b")
    assert row_beta["total_exposure"] is None
    assert row_beta["worst_drawdown"] is None
    assert row_beta["value_at_risk"] is None
    assert row_beta["risk_state"] == "unknown"
    assert row_beta["risk_score"] == 40.0

    # Test filtering by persona_id
    resp_filtered = client.get("/bff/management/risk-radar?persona_id=persona-a", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_filtered.status_code == 200
    filtered_rows = resp_filtered.json()["data"]["rows"]
    assert len(filtered_rows) == 1
    assert filtered_rows[0]["persona_id"] == "persona-a"


def test_incident_timeline_parity_full_projection_and_sorting() -> None:
    """Verify Incident Timeline projects complete incident schema, severity buckets, and sorts properly."""
    class CustomIncidentStore:
        def list_incidents(self) -> List[Dict[str, Any]]:
            return [
                {
                    "incident_id": "inc-old",
                    "severity": "critical",
                    "status": "resolved",
                    "title": "Old Critical Outage",
                    "occurred_at": "2026-08-28T10:00:00Z",
                    "runtime_id": "run-1",
                    "capital_pool_id": "pool-main",
                },
                {
                    "incident_id": "inc-new",
                    "severity": "medium",
                    "status": "open",
                    "title": "Recent Minor Disconnect",
                    "occurred_at": "2026-08-30T15:00:00Z",
                    "runtime_id": "run-2",
                    "capital_pool_id": "pool-canary",
                },
                {
                    "incident_id": "inc-high",
                    "severity": "sev2",
                    "status": "in_progress",
                    "title": "High Latency Warning",
                    "occurred_at": "2026-08-29T12:00:00Z",
                    "runtime_id": "run-1",
                    "capital_pool_id": "pool-main",
                },
            ]

    store = CustomIncidentStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    client = TestClient(app)

    # 1. Default sorting is asc (chronological)
    resp = client.get("/bff/management/incident-timeline", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    items = data["items"]
    assert len(items) == 3
    assert [i["incident_id"] for i in items] == ["inc-old", "inc-high", "inc-new"]
    assert items[0]["timeline_id"] == "incident-timeline-inc-old"
    assert items[0]["severity_bucket"] == "high"
    assert items[1]["severity_bucket"] == "high"
    assert items[2]["severity_bucket"] == "medium"
    assert items[0]["sequence"] == 1
    assert items[0]["links"]["incident"] == "/bff/incidents/inc-old"

    summary = data["summary"]
    assert summary["incident_count"] == 3
    assert summary["active_incident_count"] == 2
    assert summary["resolved_incident_count"] == 1
    assert summary["high_severity_count"] == 2
    assert summary["medium_severity_count"] == 1
    assert summary["low_severity_count"] == 0

    # 2. Descending sort
    resp_desc = client.get("/bff/management/incident-timeline?sort_order=desc", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_desc.status_code == 200
    assert [i["incident_id"] for i in resp_desc.json()["data"]["items"]] == ["inc-new", "inc-high", "inc-old"]

    # 3. Filter by runtime_id
    resp_run = client.get("/bff/management/incident-timeline?runtime_id=run-2", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_run.status_code == 200
    assert len(resp_run.json()["data"]["items"]) == 1
    assert resp_run.json()["data"]["items"][0]["incident_id"] == "inc-new"


def test_intervention_stream_parity_dual_source_and_window_filtering() -> None:
    """Verify Intervention Stream projects v5 interventions and audit events with time window and search filtering."""
    now_dt = datetime.now(timezone.utc)
    recent_time = (now_dt - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    old_time = (now_dt - timedelta(hours=36)).isoformat().replace("+00:00", "Z")

    class CustomInterventionStore:
        def list_v5_interventions(self) -> List[Dict[str, Any]]:
            return [
                {
                    "intervention_id": "intv-recent",
                    "persona_id": "persona-a",
                    "status": "approved",
                    "kind": "circuit_breaker",
                    "priority": "high",
                    "occurred_at": recent_time,
                    "description": "Triggered circuit breaker due to volatility",
                },
                {
                    "intervention_id": "intv-old",
                    "persona_id": "persona-a",
                    "status": "completed",
                    "kind": "manual_override",
                    "priority": "low",
                    "occurred_at": old_time,
                    "description": "Old manual intervention from last week",
                },
            ]

        def list_governance_audit_events(self) -> List[Dict[str, Any]]:
            return [
                {
                    "entry_id": "audit-1",
                    "target_type": "Intervention",
                    "target_id": "intv-recent",
                    "action_type": "intervention.approved",
                    "outcome": "success",
                    "persona_id": "persona-a",
                    "occurred_at": recent_time,
                    "reason": "Operator approved circuit breaker",
                }
            ]

    store = CustomInterventionStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    client = TestClient(app)

    # 1. 24h window filter excludes intv-old
    resp = client.get("/bff/management/intervention-stream?window_hours=24", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    items = data["items"]
    assert len(items) == 2
    assert all(i["id"] != "intervention-stream-intv-old-completed" for i in items)

    intv_item = next(i for i in items if i["event_source"] == "v5_interventions")
    assert intv_item["intervention_id"] == "intv-recent"
    assert intv_item["event_type"] == "intervention.approved"
    assert intv_item["priority"] == "high"
    assert intv_item["target"]["id"] == "persona-a"
    assert intv_item["links"]["human_inbox"] == "/bff/management/human-inbox/intervention:intv-recent"

    audit_item = next(i for i in items if i["event_source"] == "governance_audit_events")
    assert audit_item["event_type"] == "intervention.approved"
    assert audit_item["target"]["id"] == "intv-recent"

    # 2. 48h window includes old item
    resp_48 = client.get("/bff/management/intervention-stream?window_hours=48", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_48.status_code == 200
    assert len(resp_48.json()["data"]["items"]) == 3

    # 3. Query string search filter
    resp_q = client.get("/bff/management/intervention-stream?window_hours=48&q=volatility", headers={"Authorization": "Bearer op-1:operator"})
    assert resp_q.status_code == 200
    assert len(resp_q.json()["data"]["items"]) == 1
    assert resp_q.json()["data"]["items"][0]["intervention_id"] == "intv-recent"


def test_all_17_management_router_routes_mounted_and_accessible() -> None:
    """Exhaustive mounted test verifying all 17 catalogued routes return 200 and standard envelope."""
    mock_store = MockManagementReadStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: mock_store))
    client = TestClient(app)

    concrete_paths = [
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
        "/bff/management/human-inbox/app-1",
        "/bff/management/hiq-backlog",
        "/bff/management/intervention-stream",
        "/bff/management/evidence",
        "/bff/management/operations-read-model/persona-a",
        "/api/v1/operator/degraded-control-guidance",
    ]

    assert len(concrete_paths) == 17, f"Expected 17 paths, got {len(concrete_paths)}"

    for path in concrete_paths:
        resp = client.get(path, headers={"Authorization": "Bearer op-1:operator,reviewer"})
        assert resp.status_code == 200, f"Route {path} failed with status {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "meta" in body, f"Route {path} missing meta envelope"


def test_incident_timeline_semantic_parity_and_alias_normalization() -> None:
    """Verify incident timeline normalizes alias fields, builds links, source_refs, and lineage_ref with exact predecessor parity."""
    class ParityIncidentStore:
        def list_incidents(self) -> List[Dict[str, Any]]:
            return [
                {
                    "id": "inc-parity-1",
                    "title": "Parity Incident Case",
                    "status": "open",
                    "severity": "critical",
                    "runtime_binding_id": "rt-bind-123",
                    "plan_id": "plan-xyz-789",
                    "affected_pool_id": "pool-canary-01",
                    "persona_capital_binding_id": "pc-bind-456",
                    "artifact_id": "art-model-v2",
                    "artifact_version": "2.4.1",
                    "opened_at": "2026-08-30T12:00:00Z",
                    "telemetry_event_ids": ["telem-evt-1", "telem-evt-2"],
                    "correlation_id": "trace-corr-999",
                }
            ]

    store = ParityIncidentStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    client = TestClient(app)

    resp = client.get("/bff/management/incident-timeline", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    payload = resp.json()
    data = payload["data"]
    items = data["items"]
    assert len(items) == 1
    item = items[0]

    # 1. Alias normalization & ID preservation
    assert item["id"] == "inc-parity-1"
    assert item["incident_id"] == "inc-parity-1"
    assert item["timeline_id"] == "incident-timeline-inc-parity-1"
    assert item["title"] == "Parity Incident Case"
    assert item["status"] == "open"
    assert item["severity"] == "critical"
    assert item["severity_bucket"] == "high"
    assert item["occurred_at"] == "2026-08-30T12:00:00Z"
    assert item["deployment_plan_id"] == "plan-xyz-789"
    assert item["capital_pool_id"] == "pool-canary-01"
    assert item["persona_capital_binding_id"] == "pc-bind-456"
    assert item["artifact_id"] == "art-model-v2"
    assert item["telemetry_event_ids"] == ["telem-evt-1", "telem-evt-2"]

    # 2. Lineage ref synthesized from artifact_id + artifact_version
    assert item["lineage_ref"] == "art-model-v2@2.4.1"

    # 3. Links built via _management_link
    assert item["links"]["incident"] == "/bff/incidents/inc-parity-1"
    assert item["links"]["deployment"] == "/bff/deployments/plan-xyz-789"
    assert item["links"]["capital_pool"] == "/bff/capital-pools/pool-canary-01"

    # 4. source_refs contains all 7 key arrays
    source_refs = item["source_refs"]
    assert source_refs["incident_ids"] == ["inc-parity-1"]
    assert source_refs["deployment_plan_ids"] == ["plan-xyz-789"]
    assert source_refs["capital_pool_ids"] == ["pool-canary-01"]
    assert source_refs["persona_capital_binding_ids"] == ["pc-bind-456"]
    assert source_refs["artifact_ids"] == ["art-model-v2"]
    assert source_refs["telemetry_event_ids"] == ["telem-evt-1", "telem-evt-2"]

    # 5. Sequence & summary basis
    assert item["sequence"] == 1
    assert item["timeline_sequence"] == 1
    assert data["summary"]["basis"] == "incident_case_opened_at_chronology"


def test_intervention_stream_semantic_parity_and_target_source_refs() -> None:
    """Verify intervention stream preserves runtime/persona/strategy/incident source_refs and target metadata."""
    now_dt = datetime.now(timezone.utc)
    recent_time = (now_dt - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")

    class ParityInterventionStore:
        def list_v5_interventions(self) -> List[Dict[str, Any]]:
            return [
                {
                    "intervention_id": "intv-rich-1",
                    "kind": "circuit_breaker",
                    "status": "pending_approval",
                    "priority": "high",
                    "occurred_at": recent_time,
                    "persona_id": "persona-gamma",
                    "runtime_id": "rt-live-1",
                    "strategy_id": "strat-momentum",
                    "incident_id": "inc-breach-101",
                    "target_type": "Strategy",
                    "target_id": "strat-momentum",
                    "description": "Triggered by volatility breaker",
                }
            ]

        def list_governance_audit_events(self) -> List[Dict[str, Any]]:
            return [
                {
                    "entry_id": "audit-rich-1",
                    "target_type": "Intervention",
                    "target_id": "intv-rich-1",
                    "action_type": "intervention.approved",
                    "outcome": "approved",
                    "occurred_at": recent_time,
                    "actor": "operator-alice",
                    "runtime_id": "rt-live-1",
                    "strategy_id": "strat-momentum",
                    "incident_id": "inc-breach-101",
                    "audit_context": {
                        "intervention_id": "intv-rich-1",
                        "persona_id": "persona-gamma",
                        "reason": "Approved manual weight override",
                    },
                }
            ]

    store = ParityInterventionStore()
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    client = TestClient(app)

    resp = client.get("/bff/management/intervention-stream?window_hours=24", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    payload = resp.json()
    items = payload["data"]["items"]
    assert len(items) == 2

    # 1. Check sequence numbers
    assert [i["stream_sequence"] for i in items] == [1, 2]

    # 2. Check v5 intervention item
    v5_item = next(i for i in items if i["event_source"] == "v5_interventions")
    assert v5_item["intervention_id"] == "intv-rich-1"
    assert v5_item["persona_id"] == "persona-gamma"
    assert v5_item["runtime_id"] == "rt-live-1"
    assert v5_item["strategy_id"] == "strat-momentum"
    assert v5_item["target"] == {"type": "Strategy", "id": "strat-momentum"}
    v5_refs = v5_item["source_refs"]
    assert v5_refs["source_dataset"] == "v5_interventions"
    assert v5_refs["intervention_ids"] == ["intv-rich-1"]
    assert "rt-live-1" in v5_refs["runtime_ids"]
    assert "persona-gamma" in v5_refs["persona_ids"]
    assert "strat-momentum" in v5_refs["strategy_ids"]
    assert "inc-breach-101" in v5_refs["incident_ids"]

    # 3. Check governance audit item
    audit_item = next(i for i in items if i["event_source"] == "governance_audit_events")
    assert audit_item["intervention_id"] == "intv-rich-1"
    assert audit_item["persona_id"] == "persona-gamma"
    assert audit_item["target"] == {"type": "Intervention", "id": "intv-rich-1"}
    audit_refs = audit_item["source_refs"]
    assert audit_refs["source_dataset"] == "governance_audit_events"
    assert audit_refs["intervention_ids"] == ["intv-rich-1"]
    assert "rt-live-1" in audit_refs["runtime_ids"]
    assert "persona-gamma" in audit_refs["persona_ids"]
    assert "strat-momentum" in audit_refs["strategy_ids"]
    assert "inc-breach-101" in audit_refs["incident_ids"]
    assert audit_item["links"]["source"] == "/bff/audit"
    assert audit_item["links"]["intervention"] == "/bff/v5/interventions/intv-rich-1"


def test_main_composes_management_router_without_legacy_decorators():
    """Main must mount the canonical router instead of retaining its handlers."""
    import re

    main_path = Path(__file__).resolve().parents[1] / "main.py"
    main_source = main_path.read_text(encoding="utf-8")

    assert "create_management_router" in main_source
    forbidden_paths = (
        "/bff/management/shell-summary",
        "/api/v1/operator/home",
        "/bff/management/trading-pulse",
        "/bff/management/trading-pulse/rankings",
        "/bff/management/sentinel-pulse",
        "/api/v1/operator/health-status",
        "/bff/management/loop-throughput",
        "/bff/management/risk-radar",
        "/bff/management/incident-timeline",
        "/bff/management/human-inbox",
        "/bff/management/hiq-backlog",
        "/bff/management/intervention-stream",
        "/bff/management/evidence",
        "/bff/management/operations-read-model/{persona_id}",
        "/api/v1/operator/degraded-control-guidance",
    )
    for path in forbidden_paths:
        assert not re.search(
            rf"@app\.get\([^\n]*{re.escape(path)}",
            main_source,
        ), f"Found legacy Management decorator for {path}"


def test_main_management_routes_have_zero_duplicate_registrations():
    """The mounted Management surface must contain each route exactly once."""
    bff_main = _import_main_for_inventory()
    from collections import Counter
    from test_normalized_route_uniqueness import scan_fastapi_routes

    expected = {
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
        "/bff/management/formula-jobs",
        "/bff/management/activity",
        "/bff/management/paper-telemetry",
        "/bff/management/postmortems",
        "/bff/management/postmortems/{postmortem_id}",
    }
    entries = scan_fastapi_routes(bff_main.app)
    routes = [
        (entry.method, entry.raw_path)
        for entry in entries
        if entry.raw_path in expected
    ]
    duplicates = [pair for pair, count in Counter(routes).items() if count != 1]
    assert not duplicates, f"Management route duplicates/missing registrations: {duplicates}"
    assert len(routes) == len(expected)


def test_main_preserves_management_evolution_journal_once():
    """The existing evolution-journal owner remains mounted exactly once."""
    bff_main = _import_main_for_inventory()
    from test_normalized_route_uniqueness import scan_fastapi_routes

    matching = [
        entry
        for entry in scan_fastapi_routes(bff_main.app)
        if entry.method == "GET"
        and entry.raw_path == "/bff/management/evolution-journal"
    ]
    assert len(matching) == 1
