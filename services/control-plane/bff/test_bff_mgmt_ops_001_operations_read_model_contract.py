"""MGMT-OPS-001: operations read model identity/source-confidence contract.

Covers the shared BFF read model (`operations_read_model.py`) that lets
Persona Fleet, Portfolio Book, Performance Attribution, Persona League, and
Human Review agree on identity, source status, and data confidence.

The focus persona `persona-20260528-04688755` from the 2026-07-07 management
console operations plan has no formal performance-attribution or holdings
match (see docs/04/pantheon_management_console_operations_workflow_2026-07-07/
MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md, "Performance Attribution").
This suite proves the BFF represents that gap as an explicit `fallback`
confidence with diagnostics, never as a dropped row or a `nan` metric.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

import json
from services.control_plane.bff import main as bff_main
from fastapi.testclient import TestClient  # noqa: E402
from services.control_plane.bff.ports import ReadSurfacePorts  # noqa: E402
from services.control_plane.bff.operations_read_model import (  # noqa: E402
    DataConfidence,
    SourceState,
    build_operations_identity,
    classify_confidence,
    dedupe_ids,
    sanitize_metric,
)

HEADERS = {"Authorization": "Bearer op-mgmt-ops-001:reader,operator,admin:mfa"}
FOCUS_PERSONA_ID = "persona-20260528-04688755"


def _load_fallback_data() -> dict[str, Any]:
    fallback_path = os.path.join(os.path.dirname(__file__), "data", "read_surfaces.json")
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class OperationsReadModelTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        if seed_data is not None:
            self._data: dict[str, Any] = seed_data
        elif allow_fallback:
            self._data = _load_fallback_data()
        else:
            self._data = {}
        self.allow_fallback = allow_fallback
        self._telemetries: dict[str, Any] = {}

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "source": "local_snapshot", "snapshot_at": snapshot_at}

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

    def get_capital_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("capital_pools", {})
        if isinstance(ds, dict):
            return ds.get(str(pool_id or ""))
        return next((p for p in ds if p.get("id") == pool_id or p.get("pool_id") == pool_id), None)

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        persona_id = kwargs.get("persona_id")
        if persona_id:
            items = [b for b in items if b.get("persona_id") == persona_id]
        return items

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("deployment_plans", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("deployment_plans", {})
        if isinstance(ds, dict):
            return ds.get(str(plan_id or ""))
        return next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_runtime_binding(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((r for r in ds if r.get("id") == runtime_id or r.get("runtime_id") == runtime_id), None)

    def get_telemetry_summary(self, runtime_id: str | None) -> dict[str, Any] | None:
        return self._telemetries.get(str(runtime_id or ""))


@contextmanager
def _client_with_store(store: OperationsReadModelTestReadPorts) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    bff_main.read_store = store
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store


def _fresh_store(*, allow_local_snapshot_fallback: bool) -> OperationsReadModelTestReadPorts:
    return OperationsReadModelTestReadPorts(allow_fallback=allow_local_snapshot_fallback)


def _get(client: TestClient, persona_id: str) -> Any:
    response = client.get(
        f"/bff/management/operations-read-model/{persona_id}",
        headers=HEADERS,
    )
    return response


def _response_schema_ref(schema: dict[str, Any], path: str) -> str:
    response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in response_schema:
        return response_schema["$ref"].rsplit("/", 1)[-1]
    if "allOf" in response_schema and response_schema["allOf"]:
        return response_schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    raise AssertionError(f"{path} does not publish a component response schema: {response_schema}")


def test_operations_read_model_publishes_typed_openapi_envelope() -> None:
    bff_main.app.openapi_schema = None
    schema = TestClient(bff_main.app).get("/openapi.json").json()
    path = "/bff/management/operations-read-model/{persona_id}"

    assert path in schema["paths"]
    assert _response_schema_ref(schema, path) == "OperationsReadModelEnvelope"
    components = schema["components"]["schemas"]
    for component_name in (
        "DataConfidence",
        "OperationsIdentity",
        "OperationsPerformance",
        "OperationsReadModelEntry",
        "OperationsReadModelEnvelope",
        "SourceDiagnostic",
        "SourceState",
        "SourceStatus",
    ):
        assert component_name in components


# ---------------------------------------------------------------------------
# Unit coverage for the pure contract module
# ---------------------------------------------------------------------------


def test_sanitize_metric_never_returns_nan_or_inf() -> None:
    assert sanitize_metric(float("nan")) is None
    assert sanitize_metric(float("inf")) is None
    assert sanitize_metric(float("-inf")) is None
    assert sanitize_metric(None) is None
    assert sanitize_metric("not-a-number") is None
    assert sanitize_metric("12.5") == 12.5
    assert sanitize_metric(0) == 0.0


def test_dedupe_ids_strips_blank_and_preserves_order() -> None:
    assert dedupe_ids(["a", "", None, "b", "a", "  ", "c"]) == ["a", "b", "c"]


def test_build_operations_identity_covers_required_fields() -> None:
    identity = build_operations_identity(
        persona_id="persona-x",
        persona_label="Persona X",
        stage="paper_running",
        runtime_ids=["runtime-1", "runtime-1", ""],
        paper_ledger_ids=["ledger-1"],
        capital_pool_ids=["pool-1", None],
        sleeve_ids=[],
        strategy_ids=["strat-1"],
        artifact_ids=[],
        broker_ids=["broker-1"],
        period="latest",
        as_of="2026-07-07T00:00:00Z",
    )
    assert identity.persona_id == "persona-x"
    assert identity.runtime_ids == ["runtime-1"]
    assert identity.capital_pool_ids == ["pool-1"]
    assert identity.sleeve_ids == []
    assert identity.period == "latest"
    assert identity.as_of == "2026-07-07T00:00:00Z"


def test_classify_confidence_ladder() -> None:
    assert classify_confidence(
        has_formal_match=True, has_partial_evidence=False, is_fallback=False,
        has_degraded_source=False, has_unavailable_source=False,
    ) == DataConfidence.FORMAL
    assert classify_confidence(
        has_formal_match=True, has_partial_evidence=False, is_fallback=False,
        has_degraded_source=True, has_unavailable_source=False,
    ) == DataConfidence.DEGRADED
    assert classify_confidence(
        has_formal_match=False, has_partial_evidence=True, is_fallback=False,
        has_degraded_source=False, has_unavailable_source=False,
    ) == DataConfidence.PARTIAL
    assert classify_confidence(
        has_formal_match=False, has_partial_evidence=False, is_fallback=True,
        has_degraded_source=False, has_unavailable_source=False,
    ) == DataConfidence.FALLBACK
    assert classify_confidence(
        has_formal_match=False, has_partial_evidence=False, is_fallback=False,
        has_degraded_source=False, has_unavailable_source=True,
    ) == DataConfidence.UNAVAILABLE
    assert classify_confidence(
        has_formal_match=False, has_partial_evidence=False, is_fallback=False,
        has_degraded_source=False, has_unavailable_source=False,
    ) == DataConfidence.UNAVAILABLE


# ---------------------------------------------------------------------------
# BFF contract: unknown persona
# ---------------------------------------------------------------------------


def test_unknown_persona_returns_404_not_a_fabricated_row() -> None:
    store = _fresh_store(allow_local_snapshot_fallback=False)
    with _client_with_store(store) as client:
        response = _get(client, "does-not-exist-xyz")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# BFF contract: formal — default fixture personas have real telemetry-backed
# attribution rows.
# ---------------------------------------------------------------------------


def test_formal_confidence_when_attribution_and_telemetry_match() -> None:
    store = _fresh_store(allow_local_snapshot_fallback=False)
    store.create_persona(
        persona_id="persona-mgmt-ops-formal",
        name="Formal Persona",
        actor_id="tester",
        lifecycle_state="deployed",
        metadata={},
    )
    _seed_isolated_sources(store)
    with _client_with_store(store) as client:
        response = _get(client, "persona-mgmt-ops-formal")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["data_confidence"] == "formal"
    assert data["identity"]["persona_id"] == "persona-mgmt-ops-formal"
    assert data["identity"]["runtime_ids"] == ["runtime-mgmt-ops-formal"]
    assert data["identity"]["capital_pool_ids"] == ["pool-formal"]
    assert data["performance"]["pnl"] is not None
    assert not math.isnan(data["performance"]["pnl"])
    source_by_name = {s["source_name"]: s for s in data["sources"]}
    assert source_by_name["performance_attribution"]["source_status"] == "ok"
    assert source_by_name["portfolio_holdings"]["source_status"] == "ok"
    assert source_by_name["capital_pools"]["source_status"] == "ok"
    assert data["diagnostics"] == []


# ---------------------------------------------------------------------------
# BFF contract: fallback — the 2026-07-07 focus persona has a persona-fleet
# row but no owned runtime, performance, formal attribution, or holdings rows.
# ---------------------------------------------------------------------------


def test_focus_persona_represents_missing_attribution_as_fallback_not_nan() -> None:
    store = _fresh_store(allow_local_snapshot_fallback=True)
    store.create_persona(
        persona_id=FOCUS_PERSONA_ID,
        name="Crypto-Alt-Hunter",
        actor_id="pantheon-dev-browser",
        created_at="2026-05-28T00:00:00Z",
        lifecycle_state="deployed",
        metadata={},
    )
    with _client_with_store(store) as client:
        response = _get(client, FOCUS_PERSONA_ID)

    assert response.status_code == 200, response.text
    assert "NaN" not in response.text
    data = response.json()["data"]

    assert data["identity"]["persona_id"] == FOCUS_PERSONA_ID
    assert data["identity"]["persona_label"] == "Crypto-Alt-Hunter"
    assert data["identity"]["runtime_ids"] == []
    assert data["identity"]["paper_ledger_ids"] == []

    assert data["data_confidence"] == "fallback"

    source_by_name = {s["source_name"]: s for s in data["sources"]}
    assert source_by_name["performance_attribution"]["source_status"] == "unavailable"
    assert source_by_name["performance_attribution"]["source_row_count"] == 0
    assert source_by_name["portfolio_holdings"]["source_status"] == "unavailable"
    assert source_by_name["persona_fleet_summary"]["source_status"] == "ok"

    diagnostic_codes = {d["code"] for d in data["diagnostics"]}
    assert "MISSING_ATTRIBUTION_MATCH" in diagnostic_codes
    assert "MISSING_HOLDINGS_MATCH" in diagnostic_codes
    assert "FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK" in diagnostic_codes

    # The Fleet row keeps fallback confidence/diagnostics, but missing
    # persona-owned evidence remains null instead of inheriting market seed
    # performance.
    assert data["performance"]["pnl"] is None
    assert data["performance"]["sharpe"] is None
    assert data["performance"]["drawdown_pct"] is None
    for value in data["performance"].values():
        if isinstance(value, float):
            assert not math.isnan(value)
            assert not math.isinf(value)


# ---------------------------------------------------------------------------
# BFF contract: partial, degraded, unavailable — constructed with isolated
# monkeypatched sources (same convention as test_bff_pm12_portfolio_book_contract.py)
# so each state is exercised in isolation from the default fixture data.
# ---------------------------------------------------------------------------


def _seed_isolated_sources(store: OperationsReadModelTestReadPorts) -> None:
    bindings = [
        {
            "id": "b-formal",
            "binding_id": "b-formal",
            "persona_id": "persona-mgmt-ops-formal",
            "capital_pool_id": "pool-formal",
            "status": "active",
            "validity": "active",
            "role": "primary",
        },
        {
            "id": "b-partial",
            "binding_id": "b-partial",
            "persona_id": "persona-mgmt-ops-partial",
            "capital_pool_id": "pool-partial",
            "status": "active",
            "validity": "active",
            "role": "primary",
        },
        {
            "id": "b-degraded",
            "binding_id": "b-degraded",
            "persona_id": "persona-mgmt-ops-degraded",
            "capital_pool_id": "pool-missing",
            "status": "active",
            "validity": "active",
            "role": "primary",
        },
    ]
    plans = [
        {
            "id": "plan-formal",
            "plan_id": "plan-formal",
            "status": "approved",
            "target_stage": "paper",
            "capital_pool_id": "pool-formal",
            "binding_ids": ["b-formal"],
        },
        {
            "id": "plan-partial",
            "plan_id": "plan-partial",
            "status": "approved",
            "target_stage": "paper",
            "capital_pool_id": "pool-partial",
            "binding_ids": ["b-partial"],
        },
        {
            "id": "plan-degraded",
            "plan_id": "plan-degraded",
            "status": "approved",
            "target_stage": "paper",
            "capital_pool_id": "pool-missing",
            "binding_ids": ["b-degraded"],
        },
    ]
    runtimes = [
        {
            "id": "rb-formal",
            "binding_id": "rb-formal",
            "runtime_id": "runtime-mgmt-ops-formal",
            "plan_id": "plan-formal",
            "status": "active",
            "deployment_stage": "paper",
        },
        {
            "id": "rb-partial",
            "binding_id": "rb-partial",
            "runtime_id": "runtime-mgmt-ops-partial",
            "plan_id": "plan-partial",
            "status": "active",
            "deployment_stage": "paper",
        },
        {
            "id": "rb-degraded",
            "binding_id": "rb-degraded",
            "runtime_id": "runtime-mgmt-ops-degraded",
            "plan_id": "plan-degraded",
            "status": "active",
            "deployment_stage": "paper",
        },
    ]
    pools = [
        {"id": "pool-formal", "pool_id": "pool-formal", "name": "Formal Pool", "status": "active"},
        {"id": "pool-partial", "pool_id": "pool-partial", "name": "Partial Pool", "status": "active"},
    ]
    telemetry = {
        "runtime-mgmt-ops-formal": {
            "runtime_id": "runtime-mgmt-ops-formal",
            "pnl": 1200.0,
            "drawdown": 0.03,
            "collected_at": "2026-07-01T00:00:00Z",
            "positions": [
                {
                    "id": "pos-formal-1",
                    "symbol": "BTC-USD",
                    "quantity": 1.0,
                    "mark_price": 1200.0,
                    "market_value": 1200.0,
                    "unrealized_pnl": 200.0,
                    "realized_pnl": 1000.0,
                    "broker_id": "broker-formal",
                }
            ],
        },
        "runtime-mgmt-ops-degraded": {
            "runtime_id": "runtime-mgmt-ops-degraded",
            "pnl": 500.0,
            "drawdown": 0.02,
            "collected_at": "2026-07-01T00:00:00Z",
        },
    }

    store.list_bindings = (
        lambda persona_id=None, capital_pool_id=None, role=None, validity=None, include_market_persona_defaults=False: [
            binding for binding in bindings if not persona_id or binding["persona_id"] == persona_id
        ]
    )
    store.list_deployment_plans = lambda **_: plans
    store.list_runtime_bindings = lambda **_: runtimes
    store.list_capital_pools = lambda **_: pools
    store.get_telemetry_summary = lambda runtime_id: telemetry.get(runtime_id)


def test_partial_confidence_when_attribution_row_exists_without_telemetry() -> None:
    store = _fresh_store(allow_local_snapshot_fallback=False)
    store.create_persona(
        persona_id="persona-mgmt-ops-partial",
        name="Partial Persona",
        actor_id="tester",
        lifecycle_state="deployed",
        metadata={},
    )
    _seed_isolated_sources(store)
    with _client_with_store(store) as client:
        response = _get(client, "persona-mgmt-ops-partial")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["data_confidence"] == "partial"
    source_by_name = {s["source_name"]: s for s in data["sources"]}
    assert source_by_name["performance_attribution"]["source_status"] == "partial"
    diagnostic_codes = {d["code"] for d in data["diagnostics"]}
    assert "MISSING_HOLDINGS_MATCH" in diagnostic_codes


def test_degraded_confidence_when_capital_pool_id_does_not_resolve() -> None:
    store = _fresh_store(allow_local_snapshot_fallback=False)
    store.create_persona(
        persona_id="persona-mgmt-ops-degraded",
        name="Degraded Persona",
        actor_id="tester",
        lifecycle_state="deployed",
        metadata={},
    )
    _seed_isolated_sources(store)
    with _client_with_store(store) as client:
        response = _get(client, "persona-mgmt-ops-degraded")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["data_confidence"] == "degraded"
    source_by_name = {s["source_name"]: s for s in data["sources"]}
    assert source_by_name["performance_attribution"]["source_status"] == "ok"
    assert source_by_name["capital_pools"]["source_status"] == "degraded"
    diagnostic_codes = {d["code"] for d in data["diagnostics"]}
    assert "CAPITAL_POOL_ID_UNRESOLVED" in diagnostic_codes
    # A degraded joined source must not be quietly dropped: the unresolved
    # pool id count is still visible instead of a silently empty list.
    assert source_by_name["capital_pools"]["source_row_count"] == 1


def test_unavailable_confidence_when_persona_has_no_bound_sources() -> None:
    store = _fresh_store(allow_local_snapshot_fallback=False)
    store.create_persona(
        persona_id="persona-mgmt-ops-nothing",
        name="Nothing Persona",
        actor_id="tester",
        lifecycle_state="draft",
        metadata={},
    )
    with _client_with_store(store) as client:
        response = _get(client, "persona-mgmt-ops-nothing")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["data_confidence"] == "unavailable"
    source_by_name = {s["source_name"]: s for s in data["sources"]}
    assert source_by_name["performance_attribution"]["source_status"] == "unavailable"
    assert source_by_name["portfolio_holdings"]["source_status"] == "unavailable"
    assert source_by_name["capital_pools"]["source_status"] == "unavailable"
    diagnostic_codes = {d["code"] for d in data["diagnostics"]}
    assert "MISSING_ATTRIBUTION_MATCH" in diagnostic_codes
    assert "MISSING_HOLDINGS_MATCH" in diagnostic_codes
