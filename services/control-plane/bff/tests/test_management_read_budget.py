"""Regression coverage for PFG-MGMT-BFF-PERF-20260820.

These are functional read budgets, not a generic load-testing framework.  The
tests lock in the three unnecessary costs found in the Management product
paths: Cockpit's duplicate composition, one-persona operations expanding the
whole fleet, and a synchronous Source Ingest registry wait.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main  # noqa: E402
from ports import ReadSurfacePorts, create_in_memory_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer pfg-mgmt-read-budget:operator"}


@contextmanager
def _isolated_store(**factory_kwargs: Any) -> Iterator[ReadSurfacePorts]:
    with tempfile.TemporaryDirectory(prefix="pfg_mgmt_read_budget_") as td:
        original = bff_main.read_store
        del td
        store = create_in_memory_read_surface_ports(**factory_kwargs)
        bff_main.read_store = store
        try:
            yield store
        finally:
            bff_main.read_store = original


def _ok_surface() -> dict[str, str]:
    return {"status": "ok", "source": "test_projection"}


def test_cockpit_reuses_alert_and_health_composition(monkeypatch) -> None:
    """Cockpit must not recompute the same two fan-outs for operator-home."""
    original_alerts = bff_main._build_operator_alerts_payload
    original_health = bff_main._build_operator_health_status_payload
    calls = {"alerts": 0, "health": 0}

    def alerts(snapshot_at: str) -> dict[str, Any]:
        calls["alerts"] += 1
        return original_alerts(snapshot_at)

    def health(snapshot_at: str) -> dict[str, Any]:
        calls["health"] += 1
        return original_health(snapshot_at)

    monkeypatch.setattr(bff_main, "_build_operator_alerts_payload", alerts)
    monkeypatch.setattr(bff_main, "_build_operator_health_status_payload", health)
    human_inbox = {
        "data": {"items": [], "summary": {}},
        "meta": {"surfaces": {"human_inbox": _ok_surface()}},
    }

    payload = bff_main._build_management_cockpit_payload(
        "2026-08-21T00:00:00Z",
        human_inbox=human_inbox,
    )

    assert payload["data"]["id"] == "management-cockpit"
    assert calls == {"alerts": 1, "health": 1}


def test_operations_read_model_never_expands_full_persona_fleet(monkeypatch) -> None:
    """A single-persona response uses direct canonical projections only."""
    with _isolated_store(
        persona_capital_runtime_kwargs={
            "personas": [
                {
                    "persona_id": "persona-budget-direct",
                    "name": "Budget Direct Persona",
                    "lifecycle_state": "deployed",
                    "metadata": {},
                }
            ],
        },
    ) as store:

        def full_fleet_is_forbidden(**_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("operations read model must not build the full fleet")

        monkeypatch.setattr(
            bff_main,
            "_persona_fleet_slim_list_payload",
            full_fleet_is_forbidden,
        )
        with TestClient(bff_main.app, raise_server_exceptions=False) as client:
            response = client.get(
                "/bff/management/operations-read-model/persona-budget-direct",
                headers=HEADERS,
            )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["identity"]["persona_id"] == "persona-budget-direct"


def test_attribution_reuses_bulk_telemetry_projection(monkeypatch) -> None:
    """Multiple runtimes must not trigger one telemetry read per runtime."""
    with _isolated_store() as store:
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
        sources = bff_main._pm12_performance_attribution_sources()

    assert set(sources["telemetry_by_runtime_id"]) == {
        "runtime-budget-a",
        "runtime-budget-b",
    }


def test_data_sources_times_out_as_typed_unavailable_not_a_healthy_cache(monkeypatch) -> None:
    """A slow Source Ingest read must stay within the BFF budget and degrade."""
    with _isolated_store() as store:
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
        monkeypatch.setattr(
            bff_main,
            "_management_data_sources_read_timeout_seconds",
            lambda: 0.05,
        )
        with TestClient(bff_main.app, raise_server_exceptions=False) as client:
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
