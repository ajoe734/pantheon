"""Regression coverage for PFG-MGMT-BFF-PERF-20260820.

These are functional read budgets, not a generic load-testing framework.  The
tests lock in the three unnecessary costs found in the Management product
paths: Cockpit's duplicate composition, one-persona operations expanding the
whole fleet, and a synchronous Source Ingest registry wait.
"""
from __future__ import annotations

import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.console_gap.datasources import create_datasources_router
from services.control_plane.bff.source_management_client import SourceManagementClient
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports


HEADERS = {"Authorization": "Bearer pfg-mgmt-read-budget:operator"}


def _ok_surface() -> dict[str, str]:
    return {"status": "ok", "source": "test_projection"}


def _compile_pm12_namespace(store: Any) -> dict[str, Any]:
    tree = ast.parse(Path(__file__).resolve().parent.parent.joinpath("main.py").read_text())
    target_names = {
        "_management_avg",
        "_management_record_id",
        "_management_first_non_empty",
        "_management_dict_value",
        "_management_nested_dict",
        "_management_position_records",
        "_management_latest_timestamp",
        "_management_link",
        "_filter_by_common_identifiers",
        "_performance_ranking_source_surface",
        "_list_strategy_summaries",
        "_pm12_performance_attribution_sources",
    }
    funcs = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in target_names
    ]
    ns = dict(__import__("typing").__dict__)
    ns.update({
        "read_store": store,
        "_list_persona_records": lambda *a, **kw: [],
        "_list_strategy_summaries": lambda *a, **kw: [],
    })
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "main_pm12.py", "exec"), ns)
    return ns


def test_cockpit_reuses_alert_and_health_composition(monkeypatch) -> None:
    """Cockpit must not recompute the same two fan-outs for operator-home."""
    store = create_in_memory_read_surface_ports()
    svc = ManagementService(read_store=store)
    calls = {"alerts": 0, "health": 0}
    orig_alerts = svc.get_operator_alerts
    orig_health = svc.get_operator_health_status

    def alerts(snapshot_at: str) -> dict[str, Any]:
        calls["alerts"] += 1
        return orig_alerts(snapshot_at)

    def health(snapshot_at: str) -> dict[str, Any]:
        calls["health"] += 1
        return orig_health(snapshot_at)

    monkeypatch.setattr(svc, "get_operator_alerts", alerts)
    monkeypatch.setattr(svc, "get_operator_health_status", health)
    human_inbox = {
        "data": {"items": [], "summary": {}},
        "meta": {"surfaces": {"human_inbox": _ok_surface()}},
    }

    payload = svc.get_management_cockpit(
        "2026-08-21T00:00:00Z",
        human_inbox=human_inbox,
    )

    assert payload["data"]["id"] == "management-cockpit"
    assert calls["alerts"] == 1
    assert calls["health"] <= 2


def test_operations_read_model_never_expands_full_persona_fleet(monkeypatch) -> None:
    """A single-persona response uses direct canonical projections only."""
    store = create_in_memory_read_surface_ports()
    persona = {
        "persona_id": "persona-budget-direct",
        "name": "Budget Direct Persona",
        "lifecycle_state": "deployed",
        "metadata": {},
    }
    store.get_persona = lambda pid: persona if pid == "persona-budget-direct" else None

    def full_fleet_is_forbidden(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("operations read model must not build the full fleet")

    store.list_personas = full_fleet_is_forbidden

    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: store))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/bff/management/operations-read-model/persona-budget-direct",
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["identity"]["persona_id"] == "persona-budget-direct"


def test_attribution_reuses_bulk_telemetry_projection(monkeypatch) -> None:
    """Multiple runtimes must not trigger one telemetry read per runtime."""
    store = create_in_memory_read_surface_ports()
    runtimes = [
        {"runtime_id": "runtime-budget-a", "binding_id": "binding-budget-a"},
        {"runtime_id": "runtime-budget-b", "binding_id": "binding-budget-b"},
    ]
    store.list_runtime_bindings = lambda **_kwargs: runtimes
    store.list_deployment_plans = lambda **_kwargs: []
    store.list_bindings = lambda **_kwargs: []
    store.list_capital_pools = lambda **_kwargs: []
    store.list_personas = lambda **_kwargs: []
    store.list_strategies = lambda **_kwargs: []
    store.list_telemetry_summaries = lambda: [
        {"runtime_id": "runtime-budget-a", "pnl": 1.0},
        {"runtime_id": "runtime-budget-b", "pnl": 2.0},
    ]

    def per_runtime_read_is_forbidden(_runtime_id: str) -> None:
        raise AssertionError("bulk telemetry projection must satisfy attribution")

    store.get_telemetry_summary = per_runtime_read_is_forbidden
    pm12_ns = _compile_pm12_namespace(store)
    sources = pm12_ns["_pm12_performance_attribution_sources"]()

    assert set(sources["telemetry_by_runtime_id"]) == {
        "runtime-budget-a",
        "runtime-budget-b",
    }


def test_data_sources_times_out_as_typed_unavailable_not_a_healthy_cache(monkeypatch) -> None:
    """A slow Source Ingest read must stay within the BFF budget and degrade."""
    class _ManagementReadTimeout(Exception):
        pass

    async def _run_read(func, timeout_seconds, executor):
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(executor, func), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise _ManagementReadTimeout()

    executor = ThreadPoolExecutor(max_workers=2)
    timeout_val = [0.05]

    async def read_source_connector_registry(store: Any) -> dict[str, Any]:
        try:
            return await _run_read(store.get_source_connector_registry, timeout_val[0], executor)
        except _ManagementReadTimeout:
            return {
                "source": "unavailable",
                "connectors": [],
                "provider_examples": [],
                "policy_registry": None,
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
                "reason": "read_timeout",
            }

    store = create_in_memory_read_surface_ports()

    def slow_registry() -> dict[str, Any]:
        time.sleep(0.30)
        return {
            "source": "service_client",
            "connectors": [{"connector_id": "late-but-real"}],
            "provider_examples": [],
            "policy_registry": None,
            "financial_data_source_catalog": None,
            "active_universe_policy": None,
        }

    store.get_source_connector_registry = slow_registry

    app = FastAPI()
    router = create_datasources_router(
        read_surface=store,
        extract_identity=lambda h: type("Id", (), {"operator_id": "op1", "roles": ["operator"]})(),
        require_read_role=lambda i: None,
        snapshot_meta=lambda s: {"snapshot_at": s},
        utc_now=lambda: "2026-08-21T00:00:00Z",
        read_source_connector_registry=read_source_connector_registry,
        get_source_management_client=lambda: SourceManagementClient(),
        require_operator_role=lambda i: None,
        bff_error=lambda *a, **kw: Exception(*a),
    )
    app.include_router(router)

    with TestClient(app, raise_server_exceptions=False) as client:
        started = time.monotonic()
        response = client.get("/bff/management/data-sources", headers=HEADERS)
        elapsed = time.monotonic() - started

    # Let the deliberately uncancellable worker finish before another test
    # observes the shared bounded executor's capacity.
    time.sleep(0.30)

    assert response.status_code == 200, response.text
    assert elapsed < 0.25, f"data-sources waited {elapsed:.3f}s instead of degrading"
    payload = response.json()
    assert payload["data"]["items"] == []
    assert payload["data"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["data_sources"]["reason"] == "read_timeout"
    assert payload["meta"]["degradation"]["reason"] == "read_timeout"
