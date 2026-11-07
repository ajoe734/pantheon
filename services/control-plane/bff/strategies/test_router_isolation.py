"""Regression and contract test for decomposed Strategies router."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.strategies.router import create_strategies_router

AUTH_HEADERS = {"Authorization": "Bearer test-op:operator,admin"}


def test_strategies_router_decomposition_routes():
    router = create_strategies_router()
    actual_routes = {(r.path, frozenset(r.methods)) for r in router.routes}

    expected_routes = [
        ("/bff/strategies", frozenset({"GET"})),
        ("/bff/strategies", frozenset({"POST"})),
        ("/bff/strategies/{strategy_id}", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}", frozenset({"PATCH"})),
        ("/bff/strategies/{strategy_id}/specs", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/specs", frozenset({"POST"})),
        ("/bff/strategies/{strategy_id}/experiments", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/artifacts", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/lineage", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/audit", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/ooda", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/actions/{action_id}", frozenset({"POST"})),
        ("/bff/strategies/{strategy_id}/dry-run", frozenset({"POST"})),
        ("/bff/management/strategy-seeds", frozenset({"GET"})),
        ("/bff/management/strategy-seeds/{seed_id}", frozenset({"GET"})),
        ("/bff/management/strategy-seeds/{seed_id}/review", frozenset({"POST"})),
        ("/bff/management/strategy-seeds/{seed_id}/merge", frozenset({"POST"})),
        ("/bff/management/strategy-seeds/{seed_id}/submit-replication", frozenset({"POST"})),
    ]

    assert len(router.routes) == len(expected_routes)
    for path, methods in expected_routes:
        assert (path, methods) in actual_routes, f"Missing route {path} {methods}"


def test_strategies_router_client_execution():
    app = FastAPI()
    router = create_strategies_router(
        list_strategy_summaries=lambda: [
            {"strategy_id": "strat-1", "title": "Momentum Alpha", "lifecycle_state": "candidate"}
        ],
        read_surface=lambda: type(
            "FakeReadStore",
            (),
            {
                "get_strategy_spec": lambda self, sid: {"strategy_id": sid, "title": "Momentum Alpha"},
                "get_strategy_spec_detail": lambda self, sid, version_selector="current": {
                    "lifecycle_state": "candidate",
                    "governance": {"risk_level": "medium"},
                },
                "list_strategy_spec_versions": lambda self, sid: [{"version": "1.0.0"}],
            },
        )(),
    )
    app.include_router(router)
    client = TestClient(app)

    # List strategies
    resp = client.get("/bff/strategies", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "strat-1"

    # Get strategy
    resp = client.get("/bff/strategies/strat-1", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "strat-1"

    # List specs
    resp = client.get("/bff/strategies/strat-1/specs", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "service",
        "routes",
        "routes.common",
        "routes.collection",
        "routes.detail",
        "routes.seeds",
        "router",
    ],
)
def test_strategies_modules_cold_import_in_any_entry_order(module):
    """Each module must import first in a fresh interpreter (no service<->routes cycle)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import services.control_plane.bff.strategies.{module}"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 0, result.stderr


def test_strategies_service_has_no_routes_import_dependency():
    """service.py must import and execute get_strategy_rules without importing routes.*."""
    script = (
        "import sys; "
        "from types import SimpleNamespace; "
        "from services.control_plane.bff.strategies.service import StrategiesService; "
        "assert 'services.control_plane.bff.strategies.routes' not in sys.modules; "
        "svc = StrategiesService(read_surface=SimpleNamespace("
        "read_registry_strategy_artifacts_for_strategy=lambda sid: SimpleNamespace("
        "status='ok', items=[{'registry_id': 'art-1', 'artifact_type': 'execution_bundle', "
        "'artifact_state': 'approved', 'strategy_id': sid, 'version': '1.0.0'}]))); "
        "rules, status = svc.get_strategy_rules('strat-1'); "
        "assert status == 'ok' and len(rules) == 1 and rules[0]['registryId'] == 'art-1'; "
        "assert 'services.control_plane.bff.strategies.routes' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 0, result.stderr
