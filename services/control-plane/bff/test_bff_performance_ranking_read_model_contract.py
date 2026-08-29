"""
Contract and schema tests for the Performance and Ranking Read Model.
Locks the BFF query envelope and source-confidence contract needed by all three
canonical centers (Performance Attribution, Persona League Rankings, Quarterly Ranking).
"""
from __future__ import annotations

import os
import sys
import tempfile
import math
from contextlib import contextmanager
from typing import Iterator, Any, Dict, List

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

import json
import main as bff_main
from ports import ReadSurfacePorts
from operations_read_model import (
    DataConfidence,
    SourceState,
    sanitize_metric,
)

HEADERS = {"Authorization": "Bearer op-perf-ranking:reader,operator,admin:mfa"}


def _load_fallback_data() -> dict[str, Any]:
    fallback_path = os.path.join(os.path.dirname(__file__), "data", "read_surfaces.json")
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class PerformanceRankingReadModelTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        if seed_data is not None:
            self._data: dict[str, Any] = seed_data
        elif allow_fallback:
            self._data = _load_fallback_data()
        else:
            self._data = {}
        self.allow_fallback = allow_fallback

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "source": "local_snapshot",
            "snapshot_at": snapshot_at,
            "freshness": "fresh",
            "observed_time": snapshot_at,
            "coverage": 1.0,
            "missing_bindings": False,
        }

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, [])

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        persona_id = kwargs.get("persona_id") or kwargs.get("id") or "p-new"
        persona = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": kwargs.get("name") or persona_id,
            "lifecycle_state": kwargs.get("lifecycle_state") or "active",
            "metadata": kwargs.get("metadata") or {},
        }
        ds = self._data.setdefault("personas", {})
        if isinstance(ds, dict):
            ds[persona_id] = persona
        elif isinstance(ds, list):
            ds.append(persona)
        return persona

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("personas", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("personas", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("capital_pools", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("deployment_plans", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_rebalances(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("rebalances", [])
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_ranking_formulas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("ranking_formulas", [])
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_quarterly_ranking_recommendations(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("quarterly_ranking_recommendations", [])
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def put_ranking_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot_id = payload.get("id") or payload.get("ranking_snapshot_id") or "snap-1"
        ds = self._data.setdefault("ranking_snapshots", {})
        if isinstance(ds, dict):
            ds[snapshot_id] = payload
        elif isinstance(ds, list):
            ds.append(payload)
        return payload

    def get_ranking_snapshot(self, snapshot_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("ranking_snapshots", {})
        if isinstance(ds, dict):
            return ds.get(str(snapshot_id or ""))
        return next((s for s in ds if s.get("id") == snapshot_id or s.get("ranking_snapshot_id") == snapshot_id), None)


@contextmanager
def _client_with_store(store: PerformanceRankingReadModelTestReadPorts) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    bff_main.read_store = store
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store


def _fresh_store(*, allow_local_snapshot_fallback: bool) -> PerformanceRankingReadModelTestReadPorts:
    return PerformanceRankingReadModelTestReadPorts(allow_fallback=allow_local_snapshot_fallback)


def test_performance_attribution_filters_and_normalization() -> None:
    """Verify Performance Attribution endpoint supports all normalized common filters."""
    store = _fresh_store(allow_local_snapshot_fallback=True)
    # Seed a persona to make sure it filters and matches
    store.create_persona(
        persona_id="persona-test-1",
        name="Test Persona 1",
        actor_id="tester",
        lifecycle_state="deployed",
        metadata={},
    )
    with _client_with_store(store) as client:
        # Request with all common filters
        response = client.get(
            "/bff/management/performance-attribution",
            headers=HEADERS,
            params={
                "personaId": "persona-test-1",
                "runtimeId": "runtime-test-1",
                "strategyId": "strategy-test-1",
                "capitalPoolId": "pool-test-1",
                "sleeveId": "sleeve-test-1",
                "artifactId": "artifact-test-1",
                "brokerId": "broker-test-1",
                "stage": "deployed",
                "period": "latest",
                "asOf": "2026-07-11T00:00:00Z",
            }
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "data" in body
        assert "items" in body["data"]
        # Schema vocabulary check
        assert body["data"]["id"] == "pm12-performance-attribution"
        assert body["data"]["period"] == "latest"


def test_persona_league_rankings_filters() -> None:
    """Verify Persona League Rankings endpoint supports all normalized common filters."""
    store = _fresh_store(allow_local_snapshot_fallback=True)
    with _client_with_store(store) as client:
        response = client.get(
            "/bff/management/persona-league/rankings",
            headers=HEADERS,
            params={
                "personaId": "persona-test-1",
                "runtimeId": "runtime-test-1",
                "strategyId": "strategy-test-1",
                "capitalPoolId": "pool-test-1",
                "sleeveId": "sleeve-test-1",
                "artifactId": "artifact-test-1",
                "brokerId": "broker-test-1",
                "stage": "deployed",
                "period": "latest",
                "asOf": "2026-07-11T00:00:00Z",
            }
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "data" in body
        assert "items" in body["data"]
        assert "summary" in body["data"]


def test_quarterly_ranking_filters() -> None:
    """Verify Quarterly Ranking endpoint supports all normalized common filters."""
    store = _fresh_store(allow_local_snapshot_fallback=True)
    with _client_with_store(store) as client:
        response = client.get(
            "/bff/management/quarterly-ranking",
            headers=HEADERS,
            params={
                "quarter": "2026-Q1",
                "personaId": "persona-test-1",
                "runtimeId": "runtime-test-1",
                "strategyId": "strategy-test-1",
                "capitalPoolId": "pool-test-1",
                "sleeveId": "sleeve-test-1",
                "artifactId": "artifact-test-1",
                "brokerId": "broker-test-1",
                "stage": "deployed",
                "period": "latest",
                "asOf": "2026-07-11T00:00:00Z",
            }
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "data" in body
        assert "items" in body["data"]
        assert body["data"]["quarter"] == "2026-Q1"


def test_quarterly_ranking_recommendations_filters() -> None:
    """Verify Quarterly Recommendations endpoint supports all normalized common filters."""
    store = _fresh_store(allow_local_snapshot_fallback=True)
    with _client_with_store(store) as client:
        response = client.get(
            "/bff/management/quarterly-ranking/recommendations",
            headers=HEADERS,
            params={
                "quarter": "2026-Q1",
                "personaId": "persona-test-1",
                "runtimeId": "runtime-test-1",
                "strategyId": "strategy-test-1",
                "capitalPoolId": "pool-test-1",
                "sleeveId": "sleeve-test-1",
                "artifactId": "artifact-test-1",
                "brokerId": "broker-test-1",
                "stage": "deployed",
                "period": "latest",
                "asOf": "2026-07-11T00:00:00Z",
            }
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "data" in body
        assert "items" in body["data"]
        assert body["data"]["quarter"] == "2026-Q1"


def test_recommendation_evidence_and_governance_contract() -> None:
    """Verify that recommendations reference immutable ranking evidence and human review state."""
    store = _fresh_store(allow_local_snapshot_fallback=True)
    with _client_with_store(store) as client:
        response = client.get(
            "/bff/management/quarterly-ranking/recommendations",
            headers=HEADERS,
            params={"quarter": "2026-Q1"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        items = body["data"]["items"]
        assert len(items) >= 1
        for item in items:
            # Check immutable ranking evidence reference
            assert item["ranking_evidence_ref"].startswith("ranking-evidence:2026-q1-") or item["ranking_evidence_ref"].startswith("ranking-snapshot:ranking-quarterly-2026-q1-")

            # Check Human Review state structure
            assert "human_review_state" in item
            hr_state = item["human_review_state"]
            assert "status" in hr_state
            assert "decision_status" in hr_state
            assert "submitted" in hr_state

            # Check Governance structure
            assert "governance" in item
            gov = item["governance"]
            assert gov["requires_human_gate_decision"] is True
            assert gov["live_capital_mutation"] is False

            # Verify ranking evidence cannot be mistaken for approval or application
            assert item["live_capital_mutation"] is False
            assert item["requires_human_gate_decision"] is True


def test_zero_rebalance_and_formula_rows() -> None:
    """Verify empty/zero formula collections respond gracefully without NaN or errors."""
    store = _fresh_store(allow_local_snapshot_fallback=False)
    # We clear formula / rebalances by keeping them empty
    with _client_with_store(store) as client:
        response = client.get("/bff/rebalances", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["data"] == []

        response_formulas = client.get("/bff/ranking-formulas", headers=HEADERS)
        assert response_formulas.status_code == 200, response_formulas.text
        assert response_formulas.json()["data"] == []


def test_explicit_source_states_and_freshness() -> None:
    """Verify metadata carries explicit source states, freshness, coverage, and observed time."""
    store = _fresh_store(allow_local_snapshot_fallback=True)

    # Seed a persona to make sure performance-attribution endpoint doesn't fail
    store.create_persona(
        persona_id="persona-test-1",
        name="Test Persona 1",
        actor_id="tester",
        lifecycle_state="deployed",
        metadata={},
    )

    endpoints = [
        ("/bff/management/quarterly-ranking", {"quarter": "2026-Q1"}),
        ("/bff/management/performance-attribution", {}),
        ("/bff/management/persona-league/rankings", {}),
    ]

    with _client_with_store(store) as client:
        for path, params in endpoints:
            response = client.get(
                path,
                headers=HEADERS,
                params=params,
            )
            assert response.status_code == 200, f"Failed on {path}: {response.text}"
            body = response.json()
            meta = body["meta"]
            assert "snapshot_at" in meta
            assert "surfaces" in meta

            surfaces = meta["surfaces"]
            assert len(surfaces) > 0, f"No surfaces returned for {path}"

            # Verify freshness, coverage, missing_bindings, and observed_time contract
            for name, surface in surfaces.items():
                assert "status" in surface, f"Missing status in {name} of {path}"
                assert "observed_time" in surface, f"Missing observed_time in {name} of {path}"
                assert "freshness" in surface, f"Missing freshness in {name} of {path}"
                assert "coverage" in surface, f"Missing coverage in {name} of {path}"
                assert "missing_bindings" in surface, f"Missing missing_bindings in {name} of {path}"
                assert isinstance(surface["coverage"], float), f"coverage in {name} of {path} is not float"
                assert isinstance(surface["missing_bindings"], bool), f"missing_bindings in {name} of {path} is not bool"
