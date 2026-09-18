"""
Tests for BFF common filters integration.
Covers Portfolio Book and Performance Attribution endpoints' parameter binding and filter correctness.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# RETAINED_COMPOSITION (see task BFF-TEST-MIGRATION-CB07-MANAGEMENT-CONSOLE-READS-OPS-001):
# Portfolio Book (`/bff/management/portfolio-book*`) is already extracted into
# `capital/router.py::create_capital_router`, and main.py mounts that same
# real router (no duplicate route). Performance Attribution
# (`/bff/management/performance-attribution*`) is already extracted into
# `management_read_models/ranking_router.py::create_performance_attribution_router`,
# which takes `pm12_performance_attribution_response` as an injected
# dependency; main.py supplies its own `_pm12_performance_attribution_response`
# closure for that dependency, and no equivalent standalone implementation of
# the sources/facts/grouping pipeline it wraps
# (`_pm12_performance_attribution_sources` / `_facts` / `_page_entries` /
# `_rows`) exists outside main.py. Building a B05-style standalone app for
# this file would therefore require either reimplementing that pipeline in
# the test (forbidden by the migration's rule against copying production
# logic into tests) or fabricating a fake response builder that no longer
# exercises the pipeline this suite is meant to cover, so this file keeps a
# package-qualified `from services.control_plane.bff import main as bff_main`
# import rather than a `sys.path` hack.
#
# Separately, this session's investigation of the file found that the real
# `capital/router.py` portfolio-book handlers do not read any of the
# `pool`/`strategyId`/`personaId`/`runtimeId` query filters this suite
# exercises (`CapitalService.portfolio_rows()` takes no filter arguments),
# so `test_portfolio_book_common_filters`,
# `test_portfolio_book_pools_common_filters`, and
# `test_portfolio_book_exposure_common_filters` already fail against the real
# production app on `dev`, independent of this migration. That is a
# pre-existing product/test gap in `capital/router.py`, which is outside this
# task's declared artifacts and receiver-module scope; it is left unchanged
# here to avoid silently weakening the suite's assertions or touching a file
# this task does not own.
from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import ReadSurfacePorts

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


class MgmtCommonFiltersTestReadPorts(ReadSurfacePorts):
    def __init__(self):
        super().__init__()
        # Seed test data for Portfolio Book
        self._pools = [
            {
                "pool_id": "pool-1",
                "id": "pool-1",
                "name": "Pool One",
                "status": "active",
                "risk_policy_ref": "rp-001",
                "persona_id": "persona-1",
                "runtime_id": "runtime-1",
                "strategy_id": "strategy-1",
                "sleeve_id": "sleeve-1",
                "artifact_id": "artifact-1",
                "broker_id": "broker-1",
                "stage": "stage-1",
                "period": "latest",
                "as_of": "2026-07-11T00:00:00Z",
            },
            {
                "pool_id": "pool-2",
                "id": "pool-2",
                "name": "Pool Two",
                "status": "active",
                "risk_policy_ref": "rp-002",
                "persona_id": "persona-2",
                "runtime_id": "runtime-2",
                "strategy_id": "strategy-2",
                "sleeve_id": "sleeve-2",
                "artifact_id": "artifact-2",
                "broker_id": "broker-2",
                "stage": "stage-2",
                "period": "latest",
                "as_of": "2026-07-11T00:00:00Z",
            }
        ]
        self._bindings = []
        self._deployment_plans = []
        self._runtime_bindings = []
        self._telemetries = {}

    def list_capital_pools(self, **kwargs):
        return self._pools

    def list_bindings(self, **kwargs):
        return self._bindings

    def list_deployment_plans(self, **kwargs):
        return self._deployment_plans

    def list_runtime_bindings(self, **kwargs):
        return self._runtime_bindings

    def get_telemetry_summary(self, runtime_id):
        return self._telemetries.get(runtime_id)

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot"


def test_portfolio_book_common_filters(monkeypatch) -> None:
    mock_store = MgmtCommonFiltersTestReadPorts()
    monkeypatch.setattr(bff_main, "read_store", mock_store)
    client = TestClient(bff_main.app)

    # 1. Test filtering by pool_id (alias pool)
    resp = client.get("/bff/management/portfolio-book?pool=pool-1", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pool_id"] == "pool-1"

    # 2. Test filtering by strategyId (alias strategy_id)
    resp = client.get("/bff/management/portfolio-book?strategyId=strategy-2", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pool_id"] == "pool-2"

    # 3. Test filtering by personaId (alias persona_id)
    resp = client.get("/bff/management/portfolio-book?personaId=persona-1", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pool_id"] == "pool-1"

    # 4. Test filtering by runtimeId (alias runtime_id)
    resp = client.get("/bff/management/portfolio-book?runtimeId=runtime-2", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pool_id"] == "pool-2"

    # 5. Test multiple filters that don't match anything
    resp = client.get("/bff/management/portfolio-book?pool=pool-1&strategyId=strategy-2", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 0


def test_portfolio_book_pools_common_filters(monkeypatch) -> None:
    mock_store = MgmtCommonFiltersTestReadPorts()
    monkeypatch.setattr(bff_main, "read_store", mock_store)
    client = TestClient(bff_main.app)

    # Test filtering pools by strategyId
    resp = client.get("/bff/management/portfolio-book/pools?strategyId=strategy-1", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pool_id"] == "pool-1"


def test_portfolio_book_exposure_common_filters(monkeypatch) -> None:
    mock_store = MgmtCommonFiltersTestReadPorts()
    monkeypatch.setattr(bff_main, "read_store", mock_store)
    client = TestClient(bff_main.app)

    # Test filtering exposure by strategyId
    resp = client.get("/bff/management/portfolio-book/exposure?strategyId=strategy-2", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pool_id"] == "pool-2"


def test_performance_attribution_common_filters(monkeypatch) -> None:
    # We mock _pm12_performance_attribution_sources to return dummy records
    dummy_sources = {
        "runtime_bindings": [],
        "telemetry_by_runtime_id": {},
        "persona_bindings": [],
        "deployment_plans": [],
        "capital_pools": [
            {
                "pool_id": "pool-1",
                "name": "Pool 1",
                "persona_id": "persona-1",
                "runtime_id": "runtime-1",
                "strategy_id": "strategy-1",
            },
            {
                "pool_id": "pool-2",
                "name": "Pool 2",
                "persona_id": "persona-2",
                "runtime_id": "runtime-2",
                "strategy_id": "strategy-2",
            }
        ],
        "bindings": [],
        "personas_by_id": {},
        "strategies_by_id": {},
        "pools_by_id": {},
        "plans_by_id": {},
        "bindings_by_id": {},
    }
    monkeypatch.setattr(bff_main, "_pm12_performance_attribution_sources", lambda: dummy_sources)

    # We mock _pm12_performance_attribution_facts to return facts containing filters
    dummy_facts = [
        {
            "persona_id": "persona-1",
            "runtime_id": "runtime-1",
            "strategy_id": "strategy-1",
            "capital_pool_id": "pool-1",
            "period": "latest",
            "pnl": 100.0,
            "volume": 1000.0,
            "dimensions": {
                "strategy": "strategy-1",
                "persona": "persona-1",
                "pool": "pool-1",
            }
        },
        {
            "persona_id": "persona-2",
            "runtime_id": "runtime-2",
            "strategy_id": "strategy-2",
            "capital_pool_id": "pool-2",
            "period": "latest",
            "pnl": 200.0,
            "volume": 2000.0,
            "dimensions": {
                "strategy": "strategy-2",
                "persona": "persona-2",
                "pool": "pool-2",
            }
        }
    ]
    monkeypatch.setattr(bff_main, "_pm12_performance_attribution_facts", lambda sources, period: dummy_facts)

    mock_store = MgmtCommonFiltersTestReadPorts()
    monkeypatch.setattr(bff_main, "read_store", mock_store)

    client = TestClient(bff_main.app)

    # Test /bff/management/performance-attribution with filters
    resp = client.get("/bff/management/performance-attribution?strategyId=strategy-1", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    # Aggregate summary facts when no dimensions are explicitly requested
    
    # Test grouping endpoints
    resp = client.get("/bff/management/performance-attribution/by-strategy?personaId=persona-2", headers=HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["dimension_key"] == "strategy-2"
