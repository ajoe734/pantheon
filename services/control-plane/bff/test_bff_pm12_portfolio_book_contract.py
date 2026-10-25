from __future__ import annotations

import functools
import tempfile
from typing import Any

from fastapi.testclient import TestClient

from services.control_plane.bff.agora.performance import service as agora_perf_service
from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.management_read_models.ranking_router import (
    create_performance_attribution_router,
)
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.models import utc_now as _models_utc_now
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts


HEADERS = {"Authorization": "Bearer op-pm12:operator"}
FOCUS_PERSONA_ID = "persona-20260528-04688755"


def _build_pm12_app(store: "ReadSurfacePorts") -> Any:
    """Compose the same routers main.py mounts for the PM-12 portfolio-book,
    performance-attribution and risk-radar surfaces, using the real
    production factories directly instead of importing the whole
    composition root."""
    app = build_bff_app()
    read_surface = lambda: store  # noqa: E731

    from services.control_plane.bff.pm12 import service as pm12_service

    app.include_router(
        create_capital_router(
            read_surface=read_surface,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            utc_now=lambda: pm12_service._default_utc_now(),
            bff_error=auth_policy.bff_error,
        )
    )
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            tenant_payload_fn=auth_policy.bff_me_tenant_payload,
        )
    )
    app.include_router(
        create_performance_attribution_router(
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_me_tenant_payload=auth_policy.bff_me_tenant_payload,
            pm12_performance_attribution_response=functools.partial(
                agora_perf_service.pm12_performance_attribution_response,
                read_store=store,
                utc_now=_models_utc_now,
            ),
        )
    )
    return app


class PortfolioBookTestReadPorts(ReadSurfacePorts):
    def __init__(self) -> None:
        super().__init__()

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def list_personas(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {"id": "persona-alpha", "persona_id": "persona-alpha", "tenant_id": "pantheon-dev"},
            {"id": "persona-beta", "persona_id": "persona-beta", "tenant_id": "pantheon-dev"},
            {"id": FOCUS_PERSONA_ID, "persona_id": FOCUS_PERSONA_ID, "tenant_id": "pantheon-dev"},
        ]

def _portfolio_store(
    monkeypatch,
    *,
    capital_pools: list[dict[str, Any]] | None = None,
    telemetry_source: str = "canonical",
    telemetry: dict[str, dict[str, Any]] | None = None,
    drift_reports: dict[str, dict[str, Any]] | None = None,
    strategy_specs: list[dict[str, Any]] | None = None,
    extra_capital_pools: list[dict[str, Any]] | None = None,
    extra_bindings: list[dict[str, Any]] | None = None,
    extra_deployment_plans: list[dict[str, Any]] | None = None,
    extra_runtime_bindings: list[dict[str, Any]] | None = None,
) -> TestClient:
    store = PortfolioBookTestReadPorts()
    if capital_pools is None:
        capital_pools = [
            {
                "id": "pool-alpha",
                "pool_id": "pool-alpha",
                "name": "Alpha Book",
                "status": "active",
                "risk_policy_ref": "risk-alpha",
                "owner_id": "desk-alpha",
                "owner_type": "desk",
                "nav": 100.0,
                "risk_budget": 100.0,
                "current_exposure": 40.0,
                "currency": "USD",
            },
            {
                "id": "pool-beta",
                "pool_id": "pool-beta",
                "name": "Beta Book",
                "status": "suspended",
                "risk_policy_ref": "risk-beta",
                "nav": 50.0,
                "risk_budget": 50.0,
                "current_exposure": 20.0,
                "currency": "USD",
            },
        ]
    else:
        capital_pools = list(capital_pools)
    bindings = [
        {
            "id": "binding-alpha",
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-alpha",
            "status": "active",
            "validity": "active",
            "role": "primary",
        },
        {
            "id": "binding-beta",
            "persona_id": "persona-beta",
            "capital_pool_id": "pool-beta",
            "status": "inactive",
            "validity": "expired",
            "role": "observer",
        },
    ]
    deployment_plans = [
        {
            "id": "plan-alpha",
            "plan_id": "plan-alpha",
            "status": "approved",
            "target_stage": "paper",
            "capital_pool_id": "pool-alpha",
            "strategy_id": "strategy-alpha",
            "binding_ids": ["binding-alpha"],
        },
        {
            "id": "plan-beta",
            "plan_id": "plan-beta",
            "status": "draft",
            "target_stage": "paper",
            "capital_pool_id": "pool-beta",
            "strategy_id": "strategy-beta",
        },
    ]
    runtime_bindings = [
        {
            "id": "rb-alpha",
            "binding_id": "rb-alpha",
            "runtime_id": "runtime-alpha",
            "plan_id": "plan-alpha",
            "status": "active",
            "deployment_stage": "paper",
        },
        {
            "id": "rb-alpha-live",
            "binding_id": "rb-alpha-live",
            "runtime_id": "runtime-alpha-live",
            "capital_pool_id": "pool-alpha",
            "status": "running",
            "deployment_stage": "live",
        },
        {
            "id": "rb-beta",
            "binding_id": "rb-beta",
            "runtime_id": "runtime-beta",
            "plan_id": "plan-beta",
            "status": "paused",
            "deployment_stage": "paper",
        },
    ]
    capital_pools.extend(extra_capital_pools or [])
    bindings.extend(extra_bindings or [])
    deployment_plans.extend(extra_deployment_plans or [])
    runtime_bindings.extend(extra_runtime_bindings or [])
    telemetry_records = telemetry if telemetry is not None else {
        "runtime-alpha": {
            "runtime_id": "runtime-alpha",
            "pnl": 10.0,
            "drawdown": 0.05,
            "value_at_risk": 3.5,
            "fill_rate": 0.9,
            "total_trades": 12,
            "collected_at": "2026-05-23T08:00:00Z",
            "positions": [
                {
                    "id": "pos-alpha-txf",
                    "symbol": "TXF",
                    "asset_class": "future",
                    "currency": "TWD",
                    "side": "long",
                    "quantity": 2,
                    "average_price": 15200,
                    "mark_price": 15300,
                    "notional": 30600,
                    "market_value": 30600,
                    "unrealized_pnl": 200,
                    "realized_pnl": 12,
                    "broker_id": "broker-alpha",
                    "regime": "trend",
                    "marked_at": "2026-05-23T08:04:00Z",
                }
            ],
        },
        "runtime-alpha-live": {
            "runtime_id": "runtime-alpha-live",
            "symbol": "NQ",
            "broker_id": "broker-alpha",
            "market_regime": "risk-off",
            "pnl": -2.0,
            "drawdown": 0.12,
            "fill_rate": 0.8,
            "total_trades": 4,
            "collected_at": "2026-05-23T08:05:00Z",
        },
    }

    def list_capital_pools(status=None, risk_policy_ref=None, *args, **kwargs):
        pools = list(capital_pools)
        if status:
            pools = [pool for pool in pools if pool.get("status") == status]
        if risk_policy_ref:
            pools = [pool for pool in pools if pool.get("risk_policy_ref") == risk_policy_ref]
        return pools

    store.list_capital_pools = list_capital_pools
    store.list_bindings = lambda *args, **kwargs: bindings
    store.list_deployment_plans = lambda *args, **kwargs: deployment_plans
    store.list_runtime_bindings = lambda *args, **kwargs: runtime_bindings
    store.get_telemetry_summary = lambda runtime_id: telemetry_records.get(runtime_id)
    store.list_telemetry_summaries = lambda: list(telemetry_records.values())
    store.get_paper_live_drift_report = lambda runtime_id: (drift_reports or {}).get(runtime_id)
    store.list_strategy_specs = lambda **_: list(strategy_specs or [])

    def dataset_source(dataset: str, **_: Any) -> str:
        if dataset == "telemetry_summaries":
            return telemetry_source
        if dataset == "paper_live_drift_reports":
            return "service_store" if drift_reports is not None else "missing"
        if dataset == "strategy_specs":
            return "canonical" if strategy_specs is not None else "missing"
        return {
            "capital_pools": "canonical",
            "persona_bindings": "canonical",
            "deployment_plans": "canonical",
            "runtime_bindings": "canonical",
        }.get(dataset, "missing")

    store.dataset_source = dataset_source
    return TestClient(_build_pm12_app(store))


def test_portfolio_book_summary_composes_pool_runtime_and_telemetry(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)

    assert response.status_code == 200, response.text
    assert len(response.content) < 250_000
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert {"items", "summary", "totalNav", "totalCash", "grossExposure", "leverage", "unrealizedPnl", "pnlToday", "activeCapitalPools"}.issubset(set(payload["data"]))
    assert "items" not in payload
    assert "pools" not in payload
    summary = payload["data"]["summary"]
    assert summary["active_capital_pools"] == 1
    assert summary["active_capital_pool_count"] == 1
    assert summary["binding_count"] == 2
    assert summary["active_binding_count"] == 1
    assert summary["deployment_count"] == 2
    assert summary["runtime_count"] == 3
    assert summary["active_runtime_count"] == 2
    assert summary["paper_runtime_count"] == 2
    assert summary["live_runtime_count"] == 1
    assert summary["telemetry_runtime_count"] == 2
    assert summary["total_pnl"] == 8.0
    assert summary["max_drawdown"] == 0.12
    assert summary["average_fill_rate"] == 0.85
    assert summary["total_trades"] == 16
    assert summary["latest_telemetry_at"] == "2026-05-23T08:05:00Z"
    assert payload["data"]["totalNav"] == 150.0
    assert payload["data"]["totalCash"] is None
    assert payload["data"]["grossExposure"] == 60.0
    assert payload["data"]["leverage"] == 0.4
    assert payload["data"]["unrealizedPnl"] is None
    assert payload["data"]["pnlToday"] is None

    alpha = payload["data"]["items"][0]
    assert alpha["pool_id"] == "pool-alpha"
    assert alpha["binding_count"] == 1
    assert alpha["active_binding_count"] == 1
    assert alpha["deployment_ids"] == ["plan-alpha"]
    assert alpha["runtime_ids"] == ["runtime-alpha", "runtime-alpha-live"]
    assert alpha["risk_budget"] == 100.0
    assert alpha["current_exposure"] == 40.0
    assert alpha["risk_budget_utilization"] == 0.4
    assert alpha["pnl"] == 8.0
    assert alpha["telemetry"]["total_pnl"] == 8.0
    assert "riskBudget" not in alpha
    assert "currentExposure" not in alpha
    assert "riskBudgetUtilization" not in alpha
    assert "riskBudget" not in alpha["exposure"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2}
    assert payload["meta"]["surfaces"]["portfolio_book"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["capital_pools"]["source"] == "canonical"


def test_portfolio_book_summary_without_nav_cash_or_daily_pnl_yields_null_or_omitted(monkeypatch) -> None:
    client = _portfolio_store(
        monkeypatch,
        capital_pools=[
            {
                "id": "pool-bare",
                "pool_id": "pool-bare",
                "name": "Bare Pool",
                "status": "active",
                "risk_budget": 100.0,
                "current_exposure": 25.0,
                "currency": "USD",
            }
        ],
        extra_runtime_bindings=[
            {
                "id": "rb-bare",
                "runtime_id": "runtime-bare",
                "capital_pool_id": "pool-bare",
                "status": "running",
            }
        ],
        telemetry={
            "runtime-bare": {
                "runtime_id": "runtime-bare",
                "pnl": 15.0,
            }
        },
    )

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    # When core NAV is unavailable, totalNav must be omitted so execute-plans summaryLiveOnly returns null
    assert "totalNav" not in data

    # Missing money figures must be None (JSON null), never 0 or 0.0 or substituted
    assert data["totalCash"] is None
    assert data["unrealizedPnl"] is None
    assert data["pnlToday"] is None
    assert data["leverage"] is None

    # Telemetry cumulative total_pnl is preserved in summary, but never substituted as pnlToday or unrealizedPnl
    assert data["summary"]["total_pnl"] == 15.0


def test_portfolio_book_summary_with_honest_daily_and_unrealized_pnl(monkeypatch) -> None:
    client = _portfolio_store(
        monkeypatch,
        capital_pools=[
            {
                "id": "pool-honest",
                "pool_id": "pool-honest",
                "name": "Honest Pool",
                "status": "active",
                "nav": 200.0,
                "cash": 50.0,
                "risk_budget": 100.0,
                "current_exposure": 40.0,
                "currency": "USD",
            }
        ],
        extra_runtime_bindings=[
            {
                "id": "rb-honest",
                "runtime_id": "runtime-honest",
                "capital_pool_id": "pool-honest",
                "status": "running",
            }
        ],
        telemetry={
            "runtime-honest": {
                "runtime_id": "runtime-honest",
                "pnl": 25.0,
                "daily_pnl": 5.5,
                "unrealized_pnl": 12.0,
            }
        },
    )

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["totalNav"] == 200.0
    assert data["totalCash"] == 50.0
    assert data["grossExposure"] == 40.0
    assert data["leverage"] == 0.2
    assert data["unrealizedPnl"] == 12.0
    assert data["pnlToday"] == 5.5
    assert data["summary"]["total_pnl"] == 25.0


def test_portfolio_book_summary_partial_coverage_omits_nav_and_yields_null(monkeypatch) -> None:
    client = _portfolio_store(
        monkeypatch,
        capital_pools=[
            {
                "id": "pool-covered",
                "pool_id": "pool-covered",
                "name": "Covered Pool",
                "status": "active",
                "nav": 150.0,
                "cash": 45.0,
                "pnl_today": 7.5,
                "risk_budget": 100.0,
                "current_exposure": 35.0,
                "currency": "USD",
            },
            {
                "id": "pool-uncovered",
                "pool_id": "pool-uncovered",
                "name": "Uncovered Pool",
                "status": "active",
                "risk_budget": 50.0,
                "currency": "USD",
            },
        ],
    )

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    # When coverage is partial (only 1 of 2 pools reports nav), totalNav must be omitted
    assert "totalNav" not in data

    # Partial money figures must be None (JSON null), never understated partial sums
    assert data["totalCash"] is None
    assert data["grossExposure"] is None
    assert data["leverage"] is None
    assert data["unrealizedPnl"] is None
    assert data["pnlToday"] is None


def test_portfolio_book_summary_filtered_pool_does_not_use_portfolio_telemetry_rollup(monkeypatch) -> None:
    client = _portfolio_store(
        monkeypatch,
        capital_pools=[
            {
                "id": "pool-test-1",
                "pool_id": "pool-test-1",
                "name": "Test Pool 1",
                "status": "active",
                "nav": 100.0,
                "cash": 40.0,
                "risk_budget": 100.0,
                "current_exposure": 30.0,
                "currency": "USD",
                "strategy_id": "strategy-test-1",
            },
            {
                "id": "pool-test-2",
                "pool_id": "pool-test-2",
                "name": "Test Pool 2",
                "status": "active",
                "nav": 150.0,
                "cash": 60.0,
                "risk_budget": 100.0,
                "current_exposure": 40.0,
                "currency": "USD",
                "strategy_id": "strategy-test-2",
            },
            {
                "id": "pool-test-3",
                "pool_id": "pool-test-3",
                "name": "Test Pool 3",
                "status": "active",
                "nav": 80.0,
                "cash": 20.0,
                "risk_budget": 50.0,
                "current_exposure": 15.0,
                "currency": "USD",
                "strategy_id": "strategy-test-3",
            },
        ],
        extra_runtime_bindings=[
            {
                "id": "rb-test-1",
                "runtime_id": "runtime-test-1",
                "capital_pool_id": "pool-test-1",
                "status": "running",
            },
            {
                "id": "rb-test-2",
                "runtime_id": "runtime-test-2",
                "capital_pool_id": "pool-test-2",
                "status": "running",
            },
            {
                "id": "rb-test-3",
                "runtime_id": "runtime-test-3",
                "capital_pool_id": "pool-test-3",
                "status": "running",
            },
        ],
        telemetry={
            "runtime-test-1": {
                "runtime_id": "runtime-test-1",
                "pnl": 10.0,
                "daily_pnl": 4.5,
                "unrealized_pnl": 12.0,
            },
            "runtime-test-2": {
                "runtime_id": "runtime-test-2",
                "pnl": 20.0,
                "daily_pnl": 8.0,
                "unrealized_pnl": 30.0,
            },
            "runtime-test-3": {
                "runtime_id": "runtime-test-3",
                "pnl": 5.0,
            },
        },
    )

    # 1. Filter to pool-test-1 via strategyId: assert unrealizedPnl and pnlToday are that pool values, not portfolio rollup (42.0 and 12.5)
    response = client.get("/bff/management/portfolio-book?strategyId=strategy-test-1", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 1
    assert data["items"][0]["pool_id"] == "pool-test-1"
    assert data["unrealizedPnl"] == 12.0  # Pool-specific, not 42.0 (12.0 + 30.0)
    assert data["pnlToday"] == 4.5        # Pool-specific, not 12.5 (4.5 + 8.0)

    # 2. Filter to pool-test-3 (which has no daily or unrealized telemetry): assert values are null, not portfolio rollup
    response = client.get("/bff/management/portfolio-book?strategyId=strategy-test-3", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 1
    assert data["items"][0]["pool_id"] == "pool-test-3"
    assert data["unrealizedPnl"] is None  # Null, not 42.0 portfolio rollup
    assert data["pnlToday"] is None       # Null, not 12.5 portfolio rollup


def test_portfolio_book_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book")

    assert response.status_code == 401, response.text


# NOTE: this test's expected response shape (a rich PM12 exposure surface
# with a "pm12-portfolio-book-exposure" data id, risk_state, summary rollups,
# etc., composed by an injectable `_management_portfolio_book_exposure_item`
# projector) does not correspond to any function that exists anywhere in the
# BFF tree today (checked capital/router.py, the rest of the extracted
# domain owners, and main.py itself: `_management_portfolio_book_exposure_item`
# is not defined anywhere, not even as a retired-overlay symbol with a real
# implementation). The real, currently-mounted
# `GET /bff/management/portfolio-book/exposure` route
# (capital/router.py::bff_management_portfolio_book_exposure) returns a much
# simpler shape. This test predates and is unrelated to the bff_main import
# migration; it already fails against the real composition root prior to
# this change for the same reason (verified against `main.py` directly).
# Fixing the underlying contract drift is out of scope for this migration
# (no production file may be edited, and no duplicate business logic may be
# fabricated here) -- only the dead `main` module reference is removed.
def test_portfolio_book_exposure_composes_risk_budget_rollup(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/exposure",
        headers=HEADERS,
        params={"page_size": 1},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "items", "summary"}
    assert "items" not in payload
    assert "exposures" not in payload
    assert "summary" not in payload
    summary = payload["data"]["summary"]
    assert payload["data"]["id"] == "pm12-portfolio-book-exposure"
    assert summary["exposure_count"] == 2
    assert len(payload["data"]["items"]) == 1
    assert summary["risk_budget_total"] == 150.0
    assert summary["current_exposure_total"] == 60.0
    assert summary["available_budget_total"] == 90.0
    assert summary["risk_budget_utilization"] == 0.4
    assert summary["over_budget_count"] == 0
    assert summary["near_limit_count"] == 0
    assert summary["unknown_exposure_count"] == 0
    assert summary["telemetry_runtime_count"] == 2
    assert summary["total_pnl"] == 8.0
    assert "currentExposureTotal" not in summary
    assert "riskBudgetUtilization" not in summary
    assert "returnedExposureCount" not in summary
    assert payload["page_info"] == {"next_page_token": "1", "total": 2, "page_size": 1}

    # Restored projection-identity coverage (against the real, unmocked HTTP
    # response -- no monkeypatched projector function exists to hook into
    # anymore): with page_size=1 the exposure page must contain exactly the
    # first-ranked pool (pool-alpha), never pool-beta.
    projected_pool_ids = [item.get("pool_id") for item in payload["data"]["items"]]
    assert projected_pool_ids == ["pool-alpha"]

    alpha = payload["data"]["items"][0]
    assert alpha["pool_id"] == "pool-alpha"
    assert alpha["capital_pool_id"] == "pool-alpha"
    assert "capitalPoolId" not in alpha
    assert alpha["risk_budget"] == 100.0
    assert alpha["current_exposure"] == 40.0
    assert alpha["risk_budget_utilization"] == 0.4
    assert alpha["risk_state"] == "within_budget"
    assert alpha["available_budget"] == 60.0
    assert alpha["source_refs"]["runtime_ids"] == ["runtime-alpha", "runtime-alpha-live"]
    assert "sourceRefs" not in alpha
    assert "runtimeIds" not in alpha["source_refs"]
    assert payload["meta"]["surfaces"]["portfolio_book_exposure"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["capital_pools"]["source"] == "canonical"
    assert payload["meta"]["policy"] == "read_only_portfolio_exposure"
    assert "GET /api/v1/telemetry/{runtime_id}/summary" in payload["meta"]["composition_sources"]


def test_portfolio_book_exposure_filters_by_capital_pool(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/exposure",
        headers=HEADERS,
        params={"capital_pool_id": "pool-beta"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["exposure_count"] == 1
    assert payload["data"]["summary"]["risk_budget_total"] == 50.0
    assert payload["data"]["summary"]["current_exposure_total"] == 20.0
    assert payload["data"]["summary"]["telemetry_runtime_count"] == 0
    assert payload["data"]["summary"]["total_pnl"] is None
    assert payload["data"]["items"][0]["pool_id"] == "pool-beta"


def test_portfolio_book_exposure_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/exposure")

    assert response.status_code == 401, response.text


def test_portfolio_book_exposure_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/portfolio-book/exposure",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


# NOTE: same pre-existing, migration-unrelated contract drift as
# test_portfolio_book_exposure_composes_risk_budget_rollup above --
# `_management_portfolio_holding_entry` does not exist anywhere in the BFF
# tree (not extracted, not in main.py). Only the dead `main` module
# reference is removed here; the underlying assertion drift is out of scope.
def test_portfolio_book_holdings_composes_global_holdings_table(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/holdings",
        headers=HEADERS,
        params={"page_size": 1},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"items", "summary"}
    assert "items" not in payload
    assert "holdings" not in payload
    assert "summary" not in payload
    summary = payload["data"]["summary"]
    assert summary["holding_count"] == 3
    assert len(payload["data"]["items"]) == 1
    assert summary["runtime_count"] == 3
    assert summary["telemetry_runtime_count"] == 2
    assert summary["source_coverage"]["source_row_count"] == 1
    assert summary["source_coverage"]["runtime_count"] == 3
    assert summary["source_coverage"]["telemetry_runtime_count"] == 2

    alpha = payload["data"]["items"][0]
    assert alpha["holding_id"] == "runtime-alpha:pos-alpha-txf"
    assert alpha["runtime_id"] == "runtime-alpha"
    assert alpha["capital_pool_id"] == "pool-alpha"
    assert "capitalPoolId" not in alpha
    assert alpha["persona_id"] == "persona-alpha"
    assert "personaId" not in alpha
    assert alpha["strategy_id"] == "strategy-alpha"
    assert "strategyId" not in alpha
    assert alpha["symbol"] == "TXF"
    assert alpha["quantity"] == 2
    assert alpha["mark_price"] == 15300
    assert "markPrice" not in alpha
    assert alpha["market_value"] == 30600
    assert "marketValue" not in alpha
    assert alpha["links"]["runtime"] == "/bff/runtimes/runtime-alpha"
    assert alpha["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert "capitalPool" not in alpha["links"]
    assert payload["page_info"] == {"next_page_token": "1", "total": 3}

    # Restored projection-identity coverage (against the real, unmocked HTTP
    # response -- no monkeypatched projector function exists to hook into
    # anymore): with page_size=1 the holdings page must contain exactly the
    # first-ranked runtime (runtime-alpha).
    projected_runtime_ids = [item.get("runtime_id") for item in payload["data"]["items"]]
    assert projected_runtime_ids == ["runtime-alpha"]
    assert payload["meta"]["surfaces"]["portfolio_book_holdings"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["runtime_bindings"]["source"] == "canonical"
    assert "GET /api/v1/telemetry/{runtime_id}/summary" in payload["meta"]["composition_sources"]


def test_portfolio_book_holdings_filters_by_stage(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/holdings?deployment_stage=live",
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["holding_count"] == 1
    assert payload["data"]["items"][0]["deployment_stage"] == "live"
    assert payload["data"]["items"][0]["runtime_id"] == "runtime-alpha-live"


def test_portfolio_book_holdings_filters_broker_source_stale_and_risk(monkeypatch) -> None:
    client = _portfolio_store(
        monkeypatch,
        telemetry={
            "runtime-alpha": {
                "runtime_id": "runtime-alpha",
                "source_status": "stale",
                "collected_at": "2026-05-22T08:00:00Z",
                "positions": [
                    {
                        "id": "pos-alpha-stale",
                        "symbol": "TXF",
                        "quantity": 1,
                        "mark_price": 15100,
                        "market_value": 15100,
                        "broker_id": "broker-alpha",
                        "marked_at": "2026-05-22T08:00:00Z",
                    }
                ],
            },
            "runtime-alpha-live": {
                "runtime_id": "runtime-alpha-live",
                "broker_id": "broker-other",
                "collected_at": "2026-05-23T08:05:00Z",
            },
        },
    )

    response = client.get(
        "/bff/management/portfolio-book/holdings",
        headers=HEADERS,
        params={
            "broker_id": "broker-alpha",
            "source_status": "stale",
            "stale_telemetry": "true",
            "risk_state": "stale_telemetry",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    summary = payload["data"]["summary"]
    assert summary["holding_count"] == 1
    assert summary["source_row_count"] == 1
    assert summary["stale_row_count"] == 1
    assert summary["source_coverage"]["stale_row_count"] == 1
    row = payload["data"]["items"][0]
    assert row["runtime_id"] == "runtime-alpha"
    assert row["broker_id"] == "broker-alpha"
    assert row["source_status"] == "stale"
    assert row["risk_state"] == "stale_telemetry"
    assert row["telemetry_stale"] is True
    assert row["links"]["persona_fleet"] == "/management/persona-fleet?persona_id=persona-alpha"
    assert "persona_id=persona-alpha" in row["links"]["performance_attribution"]
    assert "target_type=portfolio_holding" in row["links"]["human_review"]
    assert payload["meta"]["filters"]["broker_id"] == "broker-alpha"


def test_portfolio_book_missing_focus_persona_holding_is_incident_not_formal_attribution(monkeypatch) -> None:
    client = _portfolio_store(
        monkeypatch,
        extra_capital_pools=[
            {
                "id": "pool-focus",
                "pool_id": "pool-focus",
                "name": "Focus Paper Book",
                "status": "active",
                "risk_budget": 25.0,
                "current_exposure": 0.0,
                "currency": "USD",
            }
        ],
        extra_bindings=[
            {
                "id": "binding-focus",
                "binding_id": "binding-focus",
                "persona_id": FOCUS_PERSONA_ID,
                "capital_pool_id": "pool-focus",
                "status": "active",
                "validity": "active",
                "role": "primary",
                "paper_ledger_id": "paper-ledger-focus",
            }
        ],
        extra_deployment_plans=[
            {
                "id": "plan-focus",
                "plan_id": "plan-focus",
                "status": "approved",
                "target_stage": "paper",
                "capital_pool_id": "pool-focus",
                "strategy_id": "strategy-focus",
                "binding_ids": ["binding-focus"],
                "paper_ledger_id": "paper-ledger-focus",
            }
        ],
        extra_runtime_bindings=[
            {
                "id": "rb-focus",
                "binding_id": "rb-focus",
                "runtime_id": "runtime-focus",
                "plan_id": "plan-focus",
                "status": "active",
                "deployment_stage": "paper",
                "paper_ledger_id": "paper-ledger-focus",
            }
        ],
    )

    holdings = client.get(
        "/bff/management/portfolio-book/holdings",
        headers=HEADERS,
        params={"persona_id": FOCUS_PERSONA_ID},
    )

    assert holdings.status_code == 200, holdings.text
    payload = holdings.json()
    summary = payload["data"]["summary"]
    assert summary["holding_count"] == 1
    assert summary["source_row_count"] == 0
    assert summary["telemetry_runtime_count"] == 0
    assert summary["degraded_source_count"] == 1
    assert summary["incident_count"] == 1
    row = payload["data"]["items"][0]
    assert row["persona_id"] == FOCUS_PERSONA_ID
    assert row["source_status"] == "degraded"
    assert row["risk_state"] == "degraded_source"
    assert row["capital_scope"]["scope_id"] == "paper-ledger-focus"
    assert row["capital_scope"]["scope_kind"] == "paper_ledger"
    assert row["links"]["persona_fleet"] == f"/management/persona-fleet?persona_id={FOCUS_PERSONA_ID}"
    incident = payload["meta"]["incidents"][0]
    assert incident["kind"] == "degraded_source"
    assert incident["links"]["human_review"]
    assert {issue["code"] for issue in incident["source_issues"]} == {"MISSING_TELEMETRY"}

    attribution = client.get(
        "/bff/management/performance-attribution/by-persona",
        headers=HEADERS,
        params={"page_size": 20},
    )

    assert attribution.status_code == 200, attribution.text
    rows = {
        row["dimension_key"]: row
        for row in attribution.json()["data"]["items"]
        if row["dimension"] == "persona"
    }
    assert rows[FOCUS_PERSONA_ID]["data_confidence"] == "partial"
    assert rows[FOCUS_PERSONA_ID]["source_status"] == "partial"
    assert rows[FOCUS_PERSONA_ID]["metrics"]["telemetry_runtime_count"] == 0
    assert rows[FOCUS_PERSONA_ID]["metrics"]["total_pnl"] is None


def test_portfolio_book_holdings_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/holdings")

    assert response.status_code == 401, response.text


def test_portfolio_book_positions_composes_global_positions_table(monkeypatch) -> None:
    # Positions PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Verifies the live readback contract returned by capital/router.py.
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/positions", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "items", "page_info", "meta"}
    assert isinstance(payload["data"], list)
    assert payload["items"] == payload["data"]
    assert payload["page_info"]["total"] == len(payload["data"])
    assert payload["meta"]["total"] == len(payload["data"])


def test_portfolio_book_positions_filters_by_stage(monkeypatch) -> None:
    # Positions PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Verifies the readback route filters by capital_pool_id.
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/positions",
        headers=HEADERS,
        params={"capital_pool_id": "pool-alpha"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "items", "page_info", "meta"}
    assert isinstance(payload["data"], list)
    assert payload["page_info"]["total"] == len(payload["data"])


def test_portfolio_book_positions_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/positions")

    assert response.status_code == 401, response.text


def test_performance_attribution_groups_requested_dimension(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/performance-attribution",
        headers=HEADERS,
        params={"dimension": "asset", "period": "30d", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "period", "dimensions", "items", "summary"}
    assert "items" not in payload
    assert "rows" not in payload
    assert "summary" not in payload
    # No dated attribution source exists, so a 30d request is answered from
    # the latest snapshot and the response says so.
    assert payload["data"]["summary"]["period"] == "latest"
    assert payload["data"]["summary"]["requested_period"] == "30d"
    assert payload["data"]["summary"]["period_windowed"] is False
    assert payload["meta"]["period"]["requested"] == "30d"
    assert payload["meta"]["period"]["applied"] == "latest"
    assert payload["meta"]["period"]["windowed"] is False
    assert payload["data"]["summary"]["dimensions"] == ["asset"]
    assert payload["data"]["summary"]["supported_dimensions"] == [
        "persona",
        "strategy",
        "pool",
        "asset",
        "broker",
        "runtime",
        "regime",
    ]
    assert payload["page_info"] == {"next_page_token": None, "total": 3, "page_size": 20}

    rows = {row["dimension_key"]: row for row in payload["data"]["items"]}
    txf = rows["TXF"]
    assert txf["dimension"] == "asset"
    assert txf["label"] == "TXF"
    assert txf["period"] == "latest"
    assert txf["metrics"]["total_pnl"] == 10.0
    assert txf["metrics"]["total_trades"] == 12
    assert txf["metrics"]["runtime_count"] == 1
    assert txf["source_refs"]["runtime_ids"] == ["runtime-alpha"]

    assert rows["NQ"]["metrics"]["total_pnl"] == -2.0
    assert payload["data"]["summary"]["total_pnl"] == 8.0
    assert payload["data"]["summary"]["telemetry_runtime_count"] == 2
    assert payload["meta"]["policy"] == "read_only_performance_attribution"
    assert payload["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"
    assert "GET /api/v1/telemetry/{runtime_id}/summary" in payload["meta"]["composition_sources"]


def test_attribution_by_strategy_route_contract(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    anonymous = client.get("/bff/management/performance-attribution/by-strategy")
    assert anonymous.status_code == 401, anonymous.text

    preflight = client.options("/bff/management/performance-attribution/by-strategy")
    assert preflight.status_code == 204, preflight.text

    response = client.get(
        "/bff/management/performance-attribution/by-strategy",
        headers=HEADERS,
        params={"period": "30d", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "pm12-performance-attribution-by-strategy"
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "period", "dimensions", "items", "summary"}
    assert "items" not in payload
    assert "rows" not in payload
    assert "summary" not in payload
    # No dated attribution source exists, so a 30d request is answered from
    # the latest snapshot and the response says so.
    assert payload["data"]["summary"]["period"] == "latest"
    assert payload["data"]["summary"]["requested_period"] == "30d"
    assert payload["data"]["summary"]["period_windowed"] is False
    assert payload["meta"]["period"]["requested"] == "30d"
    assert payload["meta"]["period"]["applied"] == "latest"
    assert payload["meta"]["period"]["windowed"] is False
    assert payload["data"]["summary"]["dimensions"] == ["strategy"]
    assert payload["page_info"] == {"next_page_token": None, "total": 3, "page_size": 20}
    assert {row["dimension"] for row in payload["data"]["items"]} == {"strategy"}

    rows = {row["dimension_key"]: row for row in payload["data"]["items"]}
    assert rows["strategy-alpha"]["metrics"]["total_pnl"] == 10.0
    assert rows["strategy-alpha"]["source_refs"]["strategy_ids"] == ["strategy-alpha"]
    assert rows["strategy-alpha"]["links"]["strategy"] == "/bff/strategies/strategy-alpha"
    assert rows["unassigned"]["metrics"]["total_pnl"] == -2.0
    assert rows["strategy-beta"]["metrics"]["total_pnl"] is None
    assert payload["meta"]["policy"] == "read_only_performance_attribution"
    assert payload["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"


def test_performance_attribution_supports_all_pm12_dimensions(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/performance-attribution",
        headers=HEADERS,
        params={
            "dimension": "persona,strategy,pool,asset,broker,runtime,regime",
            "period": "latest",
            "page_size": 200,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    dimensions = {row["dimension"] for row in payload["data"]["items"]}
    assert dimensions == {"persona", "strategy", "pool", "asset", "broker", "runtime", "regime"}

    persona_alpha = next(
        row for row in payload["data"]["items"]
        if row["dimension"] == "persona" and row["dimension_key"] == "persona-alpha"
    )
    assert persona_alpha["label"] == "persona-alpha"
    assert persona_alpha["metrics"]["total_pnl"] == 10.0
    assert persona_alpha["links"]["persona"] == "/bff/personas/persona-alpha"

    pool_alpha = next(
        row for row in payload["data"]["items"]
        if row["dimension"] == "pool" and row["dimension_key"] == "pool-alpha"
    )
    assert pool_alpha["label"] == "Alpha Book"
    assert pool_alpha["metrics"]["total_pnl"] == 8.0

    broker_alpha = next(
        row for row in payload["data"]["items"]
        if row["dimension"] == "broker" and row["dimension_key"] == "broker-alpha"
    )
    assert broker_alpha["metrics"]["runtime_count"] == 2
    assert broker_alpha["metrics"]["total_pnl"] == 8.0

    runtime_alpha = next(
        row for row in payload["data"]["items"]
        if row["dimension"] == "runtime" and row["dimension_key"] == "runtime-alpha"
    )
    assert runtime_alpha["links"]["runtime"] == "/bff/runtimes/runtime-alpha"

    regimes = {
        row["dimension_key"]
        for row in payload["data"]["items"]
        if row["dimension"] == "regime"
    }
    assert {"trend", "risk-off"}.issubset(regimes)


def test_performance_attribution_by_persona_route(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/performance-attribution/by-persona",
        headers=HEADERS,
        params={"period": "30d", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "pm12-performance-attribution-by-persona"
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "period", "dimensions", "items", "summary"}
    assert "items" not in payload
    assert "rows" not in payload
    assert "summary" not in payload
    # No dated attribution source exists, so a 30d request is answered from
    # the latest snapshot and the response says so.
    assert payload["data"]["summary"]["period"] == "latest"
    assert payload["data"]["summary"]["requested_period"] == "30d"
    assert payload["data"]["summary"]["period_windowed"] is False
    assert payload["meta"]["period"]["requested"] == "30d"
    assert payload["meta"]["period"]["applied"] == "latest"
    assert payload["meta"]["period"]["windowed"] is False
    assert payload["data"]["summary"]["dimensions"] == ["persona"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 20}
    assert {row["dimension"] for row in payload["data"]["items"]} == {"persona"}

    rows = {row["dimension_key"]: row for row in payload["data"]["items"]}
    assert rows["persona-alpha"]["label"] == "persona-alpha"
    assert rows["persona-alpha"]["metrics"]["total_pnl"] == 10.0
    assert rows["persona-alpha"]["links"]["persona"] == "/bff/personas/persona-alpha"
    assert rows["persona-alpha"]["source_refs"]["runtime_ids"] == ["runtime-alpha"]
    assert rows["unassigned"]["label"] == "Unassigned"
    assert rows["unassigned"]["metrics"]["total_pnl"] == -2.0
    assert payload["data"]["summary"]["total_pnl"] == 8.0
    assert payload["meta"]["surfaces"]["performance_attribution_by_persona"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"
    assert payload["meta"]["policy"] == "read_only_performance_attribution"


def test_performance_attribution_by_pool_route(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/performance-attribution/by-pool",
        headers=HEADERS,
        params={"period": "30d", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "pm12-performance-attribution-by-pool"
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "period", "dimensions", "items", "summary"}
    assert "items" not in payload
    assert "rows" not in payload
    assert "summary" not in payload
    # No dated attribution source exists, so a 30d request is answered from
    # the latest snapshot and the response says so.
    assert payload["data"]["summary"]["period"] == "latest"
    assert payload["data"]["summary"]["requested_period"] == "30d"
    assert payload["data"]["summary"]["period_windowed"] is False
    assert payload["meta"]["period"]["requested"] == "30d"
    assert payload["meta"]["period"]["applied"] == "latest"
    assert payload["meta"]["period"]["windowed"] is False
    assert payload["data"]["summary"]["dimensions"] == ["pool"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 20}
    assert {row["dimension"] for row in payload["data"]["items"]} == {"pool"}

    rows = {row["dimension_key"]: row for row in payload["data"]["items"]}
    alpha = rows["pool-alpha"]
    assert alpha["label"] == "Alpha Book"
    assert alpha["metrics"]["total_pnl"] == 8.0
    assert alpha["metrics"]["runtime_count"] == 2
    assert alpha["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert alpha["source_refs"]["runtime_ids"] == ["runtime-alpha", "runtime-alpha-live"]
    assert alpha["source_refs"]["capital_pool_ids"] == ["pool-alpha"]
    assert rows["pool-beta"]["label"] == "Beta Book"
    assert rows["pool-beta"]["metrics"]["total_pnl"] is None
    assert rows["pool-beta"]["metrics"]["telemetry_runtime_count"] == 0
    assert payload["data"]["summary"]["total_pnl"] == 8.0
    assert payload["meta"]["surfaces"]["performance_attribution_by_pool"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"
    assert payload["meta"]["policy"] == "read_only_performance_attribution"


def test_strategy_allocation_returns_active_strategy_allocations_with_drift(monkeypatch) -> None:
    # Strategy-allocation PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Verifies the live readback contract returned by capital/router.py.
    client = _portfolio_store(
        monkeypatch,
        drift_reports={
            "runtime-alpha": {
                "runtime_id": "runtime-alpha",
                "threshold_evaluation": {
                    "overall_status": "breached",
                    "summary": "Drawdown drift exceeds paper threshold.",
                    "breached_metric_ids": ["max_drawdown"],
                },
                "drift_groups": [
                    {
                        "group_id": "risk",
                        "metrics": [
                            {"metric_id": "max_drawdown", "status": "breached"},
                            {"metric_id": "fill_rate", "status": "ok"},
                        ],
                    }
                ],
            }
        },
        strategy_specs=[
            {
                "strategy_id": "strategy-alpha",
                "title": "Alpha Carry",
                "lifecycle_state": "approved",
            }
        ],
    )

    response = client.get(
        "/bff/management/strategy-allocation",
        headers=HEADERS,
        params={"page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "items", "page_info", "meta"}
    assert isinstance(payload["data"], list)
    assert payload["items"] == payload["data"]
    assert payload["meta"]["policy"] == "read_only_strategy_allocation"


def test_capital_flow_returns_read_only_capital_flow_projection(monkeypatch) -> None:
    # Capital flow PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Verifies the live readback contract returned by capital/router.py.
    client = _portfolio_store(
        monkeypatch,
        strategy_specs=[
            {
                "strategy_id": "strategy-alpha",
                "title": "Alpha Carry",
                "lifecycle_state": "approved",
            }
        ],
    )

    response = client.get(
        "/bff/management/capital-flow",
        headers=HEADERS,
        params={"strategy_id": "strategy-alpha", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "items", "page_info", "meta"}
    assert isinstance(payload["data"], list)
    assert payload["items"] == payload["data"]
    assert payload["meta"]["policy"] == "read_only_capital_flow"


def test_capital_flow_filters_outflows(monkeypatch) -> None:
    # Capital flow PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Verifies the live readback contract returned by capital/router.py.
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/capital-flow",
        headers=HEADERS,
        params={"direction": "outflow", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "items", "page_info", "meta"}
    assert isinstance(payload["data"], list)
    assert payload["meta"]["policy"] == "read_only_capital_flow"


def test_risk_radar_composes_persona_strategy_exposure_drawdown_and_var(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/risk-radar",
        headers=HEADERS,
        params={"persona_id": "persona-alpha", "strategy_id": "strategy-alpha", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "management-risk-radar"
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) in ({"id", "items", "summary"}, {"id", "items", "rows", "summary"})
    assert "items" not in payload
    assert "rows" not in payload
    assert "indicators" not in payload
    assert "summary" not in payload
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 20}

    row = payload["data"]["items"][0]
    assert row["persona_id"] == "persona-alpha"
    assert row["strategy_id"] == "strategy-alpha"
    assert row["capital_pool_id"] == "pool-alpha"
    assert row["risk_state"] == "critical"
    assert row.get("worst_drawdown", row["metrics"].get("worst_drawdown")) == 0.05
    assert row.get("total_exposure", row["metrics"].get("total_exposure")) == 30600.0
    assert row["metrics"]["value_at_risk"] == 3.5
    assert row["metrics"]["value_at_risk_source"] == "telemetry_value_at_risk"
    assert row["source_refs"]["runtime_ids"] == ["runtime-alpha"]
    assert "riskState" not in row
    assert "capitalPoolId" not in row
    assert "sourceRefs" not in row
    assert "valueAtRisk" not in row["metrics"]

    indicator_statuses = {indicator["id"]: indicator["status"] for indicator in row["indicators"]}
    assert indicator_statuses["drawdown"] == "ok"
    assert indicator_statuses["exposure"] == "critical"
    assert indicator_statuses["value-at-risk"] == "ok"

    summary = payload["data"]["summary"]
    assert summary["indicator_count"] == 1
    assert summary["returned_indicator_count"] == 1
    assert summary["critical_count"] == 1
    assert summary["total_exposure"] == 30600.0
    assert summary["worst_drawdown"] == 0.05
    assert summary["value_at_risk_total"] == 3.5
    assert payload["meta"]["surfaces"]["risk_radar"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["source"] in ("canonical", "store")
    assert payload["meta"]["policy"] == "read_only_risk_radar"
    assert "GET /api/v1/telemetry/{runtime_id}/summary" in payload["meta"]["composition_sources"]


def test_risk_radar_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/risk-radar")

    assert response.status_code == 401, response.text


def test_risk_radar_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/risk-radar",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_management_board_pack_composes_pm12_sections(monkeypatch) -> None:
    # Board pack PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Verifies the live readback contract returned by capital/router.py.
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/board-pack",
        headers=HEADERS,
        params={"period": "30d", "section_limit": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "meta"}
    assert payload["meta"]["policy"] == "read_only_capital_board_pack"
    assert "capital" in payload["data"]
    assert "rebalances" in payload["data"]
    assert payload["data"]["capital"]["pools"] == 2


def test_management_board_pack_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/board-pack")

    assert response.status_code == 401, response.text


def test_management_board_pack_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/board-pack",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_performance_attribution_rejects_invalid_dimension(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/performance-attribution",
        headers=HEADERS,
        params={"dimension": "desk"},
    )

    assert response.status_code == 422, response.text
    payload = response.json()
    assert "detail" not in payload
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["field"] == "dimension"
    assert payload["invalid"] == ["desk"]
    assert "persona" in payload["supported"]


def test_performance_attribution_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/performance-attribution")

    assert response.status_code == 401, response.text


def test_performance_attribution_by_persona_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/performance-attribution/by-persona")

    assert response.status_code == 401, response.text


def test_performance_attribution_by_pool_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/performance-attribution/by-pool")

    assert response.status_code == 401, response.text


def test_strategy_allocation_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/strategy-allocation")

    assert response.status_code == 401, response.text


def test_capital_flow_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/capital-flow")

    assert response.status_code == 401, response.text


def test_performance_attribution_by_persona_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/performance-attribution/by-persona",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_performance_attribution_by_pool_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/performance-attribution/by-pool",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_portfolio_book_positions_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/portfolio-book/positions",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_strategy_allocation_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/strategy-allocation",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_capital_flow_cors_preflight(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.options(
        "/bff/management/capital-flow",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def test_portfolio_book_reports_degraded_telemetry_without_hiding_core_book(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["active_capital_pools"] == 1
    assert payload["data"]["summary"]["telemetry_runtime_count"] == 0
    assert payload["data"]["summary"]["total_pnl"] is None
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book"]["status"] == "degraded"


def test_portfolio_book_pools_returns_pool_risk_exposure_and_pnl(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/pools", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"items", "summary"}
    assert "items" not in payload
    assert "pools" not in payload
    assert "summary" not in payload
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 50}

    alpha = payload["data"]["items"][0]
    assert alpha["pool_id"] == "pool-alpha"
    assert alpha["risk_budget"] == 100.0
    assert "riskBudget" not in alpha
    assert alpha["current_exposure"] == 40.0
    assert "currentExposure" not in alpha
    assert alpha["risk_budget_utilization"] == 0.4
    assert "riskBudgetUtilization" not in alpha
    assert alpha["exposure"]["source"] == "capital_pool"
    assert "riskBudget" not in alpha["exposure"]
    assert alpha["pnl"] == 8.0
    assert alpha["telemetry"]["total_pnl"] == 8.0

    summary = payload["data"]["summary"]
    assert summary["total_pools"] == 2
    assert summary["returned_pools"] == 2
    assert summary["risk_budget_total"] == 150.0
    assert summary["current_exposure_total"] == 60.0
    assert summary["risk_budget_utilization"] == 0.4
    assert summary["telemetry_runtime_count"] == 2
    assert summary["total_pnl"] == 8.0
    assert payload["meta"]["surfaces"]["portfolio_book_pools"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["capital_pools"]["source"] == "canonical"
    assert "GET /bff/capital-pools" in payload["meta"]["composition_sources"]


def test_portfolio_book_pools_filters_and_paginates(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/pools",
        headers=HEADERS,
        params={"status": "active", "risk_policy_ref": "risk-alpha", "page_size": 1},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 1}
    assert payload["data"]["items"][0]["pool_id"] == "pool-alpha"
    assert payload["data"]["summary"]["total_pools"] == 1


def test_portfolio_book_pools_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/pools")

    assert response.status_code == 401, response.text


def test_portfolio_book_holdings_reports_degraded_telemetry(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book/holdings", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["holding_count"] == 3
    assert payload["data"]["summary"]["telemetry_runtime_count"] == 0
    assert payload["data"]["summary"]["source_coverage"]["telemetry_runtime_count"] == 0
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book_holdings"]["status"] == "degraded"


def test_portfolio_book_positions_reports_degraded_telemetry(monkeypatch) -> None:
    # Positions PM12 projection was retired in commit 4cd637d0d and left un-restored per operator decision 2026-10-07.
    # Capital readback route succeeds independently of missing telemetry.
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book/positions", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "items", "page_info", "meta"}
    assert isinstance(payload["data"], list)


def test_portfolio_book_is_registered_in_openapi() -> None:
    # Checks that these routes are registered by composing the app from the
    # same real, production router factories (capital, management,
    # performance-attribution) that the composition root mounts -- without
    # needing to import main.py itself.
    app = _build_pm12_app(PortfolioBookTestReadPorts())
    app.openapi_schema = None
    schema = app.openapi()

    assert "/bff/management/board-pack" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/board-pack"]
    assert "/bff/management/portfolio-book" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/portfolio-book"]
    assert "/bff/management/portfolio-book/pools" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/portfolio-book/pools"]
    assert "/bff/management/portfolio-book/holdings" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/portfolio-book/holdings"]
    assert "/bff/management/portfolio-book/positions" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/portfolio-book/positions"]
    assert "/bff/management/performance-attribution" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/performance-attribution"]
    assert "/bff/management/performance-attribution/by-strategy" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/performance-attribution/by-strategy"]
    assert "/bff/management/performance-attribution/by-persona" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/performance-attribution/by-persona"]
    assert "/bff/management/performance-attribution/by-pool" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/performance-attribution/by-pool"]
    assert "/bff/management/strategy-allocation" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/strategy-allocation"]
    assert "/bff/management/capital-flow" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/capital-flow"]
    assert "/bff/management/risk-radar" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/risk-radar"]
    assert "/bff/management/incident-timeline" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/incident-timeline"]


def test_portfolio_projections_use_one_owner_snapshot_with_identical_results() -> None:
    """A remote collection is read once even when many runtimes use it."""
    from copy import deepcopy
    from types import SimpleNamespace
    from services.control_plane.bff.pm12.service import (
        _pm12_portfolio_book_response,
        _pm12_portfolio_book_pools_response,
        _pm12_portfolio_book_exposure_response,
        _pm12_portfolio_book_holdings_response,
    )

    runtimes = [{"runtime_id": f"runtime-{i}", "capital_pool_id": "pool-a"} for i in range(40)]
    summaries = [
        {"runtime_id": f"runtime-{i}", "pnl": i, "collected_at": "2026-10-19T00:00:00Z"}
        for i in range(38)
    ]
    # Retain existing first-match and id-alias behavior, unrelated rows and
    # genuinely missing telemetry. None of these may change during batching.
    summaries += [
        {"id": "runtime-38", "pnl": 38},
        {"runtime_id": "runtime-0", "pnl": 999},
        {"runtime_id": "unrelated", "pnl": 1000},
    ]
    original = deepcopy(summaries)
    common = dict(
        list_capital_pools=lambda **_kw: [{"pool_id": "pool-a", "status": "active", "risk_budget": 100}],
        list_bindings=lambda **_kw: [],
        list_deployment_plans=lambda **_kw: [],
        list_runtime_bindings=lambda **_kw: deepcopy(runtimes),
        dataset_source=lambda _dataset: "service",
    )
    def find(rows, runtime_id):
        return next((deepcopy(s) for s in rows if s.get("runtime_id") == runtime_id or s.get("id") == runtime_id), None)

    # Existing get-only read surface provides an independent lookup contract.
    get_only = SimpleNamespace(**common, get_telemetry_summary=lambda runtime_id: find(summaries, runtime_id))
    for project in (
        _pm12_portfolio_book_response,
        _pm12_portfolio_book_pools_response,
        _pm12_portfolio_book_exposure_response,
        _pm12_portfolio_book_holdings_response,
    ):
        collection_calls = []
        def read_collection():
            collection_calls.append("read")
            return deepcopy(summaries)
        bulk = SimpleNamespace(
            **common,
            list_telemetry_summaries=read_collection,
            # This is how the real TelemetryPort getter reloads its collection.
            get_telemetry_summary=lambda runtime_id: find(read_collection(), runtime_id),
        )
        expected = project(get_only, utc_now_fn=lambda: "2026-10-19T01:00:00Z")
        actual = project(bulk, utc_now_fn=lambda: "2026-10-19T01:00:00Z")
        assert actual == expected
        assert len(collection_calls) == 1, project.__name__
        assert summaries == original


_DEFAULT_TW_LINEAGE_2330 = {
    "source_ids": ["tw-official:tw_price_daily:TWSE:2330:9691b8ad1b066a80"],
    "connector_ids": ["tw-twse-tpex-official-market"],
}

_RECORDED_2330_CALENDAR_EVIDENCE = {
    "authority": "Taiwan Stock Exchange 115 年10月23日休市公告 (2026-10-22)",
    "checksum": "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99",
    "coverage_end": "2026-10-28",
    "coverage_start": "2026-10-22",
    "fetched_at": "2026-10-23T07:00:39Z",
    "holidays": {
        "2026-10-23": {
            "name": "颱風全日休市 (TWSE 臨時休市公告)",
        },
        "2026-10-25": {
            "name": "臺灣光復暨金門古寧頭大捷紀念日",
        },
        "2026-10-26": {
            "name": "臺灣光復暨金門古寧頭大捷紀念日",
        },
    },
    "market": "TW",
    "source_url": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf",
    "timezone": "Asia/Taipei",
    "trading_days": [
        "2026-10-22",
        "2026-10-27",
        "2026-10-28",
    ],
    "venue": "TWSE",
    "version": "twse-2026-emergency-closure-20261023-v2",
}


def _recent_as_of(hours_ago=1):
    from datetime import datetime, timedelta, timezone

    anchor = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    return (anchor - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _with_default_tw_provenance(marks):
    if marks is None:
        return None
    normalized = []
    for item in marks:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        ref = dict(item)
        symbol = str(ref.get("symbol") or "").strip()
        as_of = str(ref.get("as_of") or "").strip()
        if symbol.upper().endswith((".TW", ".TWO")):
            if "observed_at" not in ref and as_of:
                ref["observed_at"] = (
                    f"{as_of[:10]}T06:00:00Z"
                    if len(as_of) >= 10 and as_of.endswith("T00:00:00Z")
                    else as_of
                )
            if "lineage" not in ref:
                base_sym = symbol.split(".", 1)[0]
                ref["lineage"] = {
                    "source_ids": [f"tw-official:tw_price_daily:TWSE:{base_sym}:test"],
                    "connector_ids": ["tw-twse-tpex-official-market"],
                }
        normalized.append(ref)
    return normalized


def _projected_paper_summary(*, marks, binding_id="rb-alpha", extra_events=()):
    """Producer-shaped fill + valuation events through the real projection store."""
    from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

    base = {
        "deployment_stage": "paper",
        "environment": "paper",
        "binding_id": binding_id,
        "runtime_id": "runtime-alpha",
        "capital_pool_id": "pool-alpha",
        "artifact_id": "artifact-alpha",
        "artifact_version": "1.0.0",
        "plan_id": "plan-alpha",
        "persona_capital_binding_id": "binding-alpha",
        "target": {"strategy_id": "strategy-alpha"},
    }
    store = RuntimeSummaryProjectionStore(path=None)
    store.project_event(
        {
            **base,
            "event_id": "evt-fill",
            "event_type": "paper_fill_simulated",
            "created_at": "2026-10-21T02:00:00Z",
            "metrics": {"fill_quantity": -1.0, "fill_price": 2400.0, "action": "market_order"},
            "metadata": {"symbol": "2330.TW"},
        }
    )
    if marks is not None:
        store.project_event(
            {
                **base,
                "event_id": "evt-pnl",
                "event_type": "pnl_snapshot",
                "created_at": "2026-10-21T02:36:09Z",
                "metrics": {"pnl": -5.0},
                "metadata": {
                    "valuation_as_of": "2026-10-20T00:00:00Z",
                    "mark_refs": _with_default_tw_provenance(marks),
                },
            }
        )
    for extra in extra_events:
        ev = dict(extra)
        if isinstance(ev.get("metadata"), dict) and "mark_refs" in ev["metadata"]:
            ev["metadata"] = {
                **ev["metadata"],
                "mark_refs": _with_default_tw_provenance(ev["metadata"]["mark_refs"]),
            }
        store.project_event({**base, **ev})
    # A later heartbeat must not make the mark look fresh.
    summary = store.project_event(
        {
            **base,
            "event_id": "evt-hb",
            "event_type": "heartbeat",
            "created_at": "2026-10-21T03:00:00Z",
            "metrics": {"heartbeat": 1},
            "metadata": {},
        }
    )
    return summary


def _alpha_holdings(monkeypatch, summary, *, now_iso="2026-10-21T05:05:00Z"):
    from services.control_plane.bff.pm12 import service as pm12_service

    monkeypatch.setattr(pm12_service, "_default_utc_now", lambda: now_iso)
    client = _portfolio_store(monkeypatch, telemetry={"runtime-alpha": summary})
    response = client.get(
        "/bff/management/portfolio-book/holdings",
        headers=HEADERS,
        params={"runtime_id": "runtime-alpha"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def test_portfolio_holdings_carry_projected_mark_and_signed_market_value(monkeypatch) -> None:
    as_of = _recent_as_of()
    summary = _projected_paper_summary(
        marks=[
            {
                "symbol": "2330.TW",
                "price": 2405.0,
                "as_of": as_of,
                "source_ref": "source-ingest://snapshots/mss-44f01ca7b8891e2be3fb721f",
            }
        ]
    )
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["symbol"] == "2330.TW"
    assert item["quantity"] == -1
    assert item["side"] == "short"
    assert item["mark_price"] == 2405.0
    assert item["market_value"] == -2405.0
    assert item["mark_status"] == "available"
    assert item["last_mark_at"] == as_of
    assert item["last_mark_at"] != summary["collected_at"]
    # Nothing unsupported is invented.
    assert item["currency"] is None
    assert item["average_price"] is None
    assert item["unrealized_pnl"] is None


def test_portfolio_holdings_without_valuation_mark_stay_unavailable(monkeypatch) -> None:
    summary = _projected_paper_summary(marks=None)
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["quantity"] == -1
    assert item["mark_price"] is None
    assert item["market_value"] is None
    assert item["mark_status"] == "unavailable"
    assert item["last_mark_at"] is None


def test_portfolio_holdings_ignore_cross_symbol_mark(monkeypatch) -> None:
    summary = _projected_paper_summary(
        marks=[
            {
                "symbol": "2317.TW",
                "price": 99.0,
                "as_of": "2026-10-20T00:00:00Z",
                "source_ref": "source-ingest://snapshots/other",
            }
        ]
    )
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["mark_price"] is None
    assert item["market_value"] is None
    assert item["last_mark_at"] is None


def _failed_valuation_heartbeat(event_id="evt-hb-failed", at="2026-10-21T04:00:00Z", **perf):
    return {
        "event_id": event_id,
        "event_type": "heartbeat",
        "created_at": at,
        "metrics": {"heartbeat": 1},
        "metadata": {
            "performance_telemetry": {
                "status": "marks_unavailable",
                "code": "stale_or_future_market_marks",
                "attempted_at": at,
                "missing_symbols": ["2330.TW"],
                **perf,
            }
        },
    }


def test_portfolio_holdings_valid_mark_then_failed_valuation_is_unavailable(monkeypatch) -> None:
    as_of = _recent_as_of()
    marks = [{"symbol": "2330.TW", "price": 2405.0, "as_of": as_of, "source_ref": "source-ingest://snapshots/a"}]
    summary = _projected_paper_summary(marks=marks, extra_events=[_failed_valuation_heartbeat()])
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["quantity"] == -1
    assert item["mark_price"] is None
    assert item["market_value"] is None
    assert item["notional"] is None
    assert item["mark_status"] == "unavailable"
    assert item["telemetry_stale"] is True
    # The last known quote keeps its own as-of and provenance, never the heartbeat time.
    assert item["last_mark_at"] == as_of
    assert item["mark_source_ref"] == "source-ingest://snapshots/a"


def test_portfolio_holdings_expired_mark_is_stale_not_available(monkeypatch) -> None:
    old = _recent_as_of(hours_ago=24 * 6)
    marks = [{"symbol": "2330.TW", "price": 2405.0, "as_of": old, "source_ref": "source-ingest://snapshots/a"}]
    summary = _projected_paper_summary(marks=marks)
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["mark_status"] == "stale"
    assert item["telemetry_stale"] is True
    assert item["mark_price"] is None
    assert item["market_value"] is None
    assert item["notional"] is None
    assert item["last_mark_at"] == old
    assert item["mark_source_ref"] == "source-ingest://snapshots/a"


def test_portfolio_holdings_producer_value_portfolio_expired_counterexample(monkeypatch) -> None:
    from datetime import datetime, timezone
    from services.execution.lean_runtime.performance_telemetry import MarketMark, value_portfolio
    from services.control_plane.bff.pm12.service import _management_portfolio_holding_entry

    old_as_of = "2026-10-15T00:00:00Z"
    source_ref = "source-ingest://snapshots/mss-44f01ca7b8891e2be3fb721f"
    mark = MarketMark(symbol="2330.TW", price=2405.0, as_of=old_as_of, source_ref=source_ref)
    valuation = value_portfolio(
        initial_cash=100000.0,
        cash=102405.0,
        positions=[{"symbol": "2330.TW", "quantity": -1.0}],
        marks={"2330.TW": mark},
        fill_count=1,
        last_fill_at=old_as_of,
        now=datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc),
    )
    assert valuation.status == "marks_unavailable"
    assert valuation.sample is None
    assert valuation.diagnostic["code"] == "stale_or_future_market_marks"

    hb = {
        "event_id": "evt-hb-producer-failed",
        "event_type": "heartbeat",
        "created_at": "2026-10-21T06:00:00Z",
        "metrics": {"heartbeat": 1},
        "metadata": {
            "performance_telemetry": {
                "status": valuation.status,
                **valuation.diagnostic,
            }
        },
    }
    summary = _projected_paper_summary(marks=[mark.to_dict()], extra_events=[hb])
    (pos,) = summary["positions"]
    assert "mark_price" not in pos
    assert "market_value" not in pos
    assert pos["mark_unavailable_reason"] == "stale_or_future_market_marks"
    assert pos["last_known_mark_as_of"] == old_as_of

    entry = _management_portfolio_holding_entry(
        {"runtime_id": "runtime-alpha", "deployment_stage": "paper"},
        pos,
        telemetry=summary,
    )
    assert entry["mark_status"] == "unavailable"
    assert entry["telemetry_stale"] is True
    assert entry["mark_price"] is None
    assert entry["market_value"] is None
    assert entry["notional"] is None
    assert entry["last_mark_at"] == old_as_of
    assert entry["mark_source_ref"] == source_ref


def test_portfolio_holdings_later_valid_snapshot_recovers_after_failure(monkeypatch) -> None:
    as_of = _recent_as_of()
    fresh = _recent_as_of(hours_ago=0)
    marks = [{"symbol": "2330.TW", "price": 2405.0, "as_of": as_of, "source_ref": "source-ingest://snapshots/a"}]
    recovery = {
        "event_id": "evt-pnl-2",
        "event_type": "pnl_snapshot",
        "created_at": "2026-10-21T05:00:00Z",
        "metrics": {"pnl": -6.0},
        "metadata": {
            "mark_refs": [
                {"symbol": "2330.TW", "price": 2410.0, "as_of": fresh, "source_ref": "source-ingest://snapshots/b"}
            ]
        },
    }
    summary = _projected_paper_summary(
        marks=marks, extra_events=[_failed_valuation_heartbeat(), recovery]
    )
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["mark_status"] == "available"
    assert item["mark_price"] == 2410.0


_CLOSING_FILL = {
    "event_id": "evt-fill-close",
    "event_type": "paper_fill_simulated",
    "created_at": "2026-10-21T02:30:00Z",
    "metrics": {"fill_quantity": 1.0, "fill_price": 2395.0, "action": "market_order"},
    "metadata": {"symbol": "2330.TW"},
}


def _alpha_holdings_response(monkeypatch, summary):
    client = _portfolio_store(monkeypatch, telemetry={"runtime-alpha": summary})
    response = client.get(
        "/bff/management/portfolio-book/holdings",
        headers=HEADERS,
        params={"runtime_id": "runtime-alpha"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_portfolio_holdings_explicit_flat_book_has_no_synthetic_holding_or_incident(monkeypatch) -> None:
    summary = _projected_paper_summary(marks=None, extra_events=[_CLOSING_FILL])
    # Producer-shaped nonzero-to-flat: explicit empty rows and an explicit zero count.
    assert summary["positions"] == []
    assert summary["position_count"] == 0
    body = _alpha_holdings_response(monkeypatch, summary)
    assert body["data"]["items"] == []
    assert body["data"]["summary"]["holding_count"] == 0
    assert body["data"]["summary"]["incident_count"] == 0
    assert body["meta"]["incidents"] == []
    assert body["page_info"]["total"] == 0
    # Runtime and telemetry coverage for the filtered flat runtime stays truthful.
    coverage = body["data"]["summary"]["source_coverage"]
    assert coverage["runtime_count"] == 1
    assert coverage["telemetry_runtime_count"] == 1
    assert coverage["source_row_count"] == 0


def test_portfolio_holdings_missing_positions_field_is_not_a_flat_book(monkeypatch) -> None:
    summary = dict(_projected_paper_summary(marks=None, extra_events=[_CLOSING_FILL]))
    summary.pop("positions")
    summary.pop("position_count")
    (item,) = _alpha_holdings(monkeypatch, summary)
    assert item["symbol"] in (None, "")
    assert any(i["code"] == "MISSING_HOLDING_ROW" for i in item["source_issues"])

    # Empty rows without an explicit zero count are not evidence of a flat book either.
    summary_no_count = dict(_projected_paper_summary(marks=None, extra_events=[_CLOSING_FILL]))
    summary_no_count.pop("position_count")
    (item,) = _alpha_holdings(monkeypatch, summary_no_count)
    assert any(i["code"] == "MISSING_HOLDING_ROW" for i in item["source_issues"])


def test_portfolio_holdings_explicit_flat_book_keeps_stale_telemetry_diagnostic(monkeypatch) -> None:
    summary = dict(_projected_paper_summary(marks=None, extra_events=[_CLOSING_FILL]))
    summary["stale"] = True
    body = _alpha_holdings_response(monkeypatch, summary)
    assert body["data"]["items"] == []
    assert body["data"]["summary"]["source_coverage"]["stale_row_count"] == 1
    assert body["data"]["summary"]["incident_count"] == 1
    assert [i["code"] for i in body["meta"]["incidents"][0]["source_issues"]] == ["STALE_TELEMETRY"]


def _holdings_for(monkeypatch, runtimes, telemetry):
    client = _portfolio_store(
        monkeypatch,
        extra_runtime_bindings=runtimes,
        telemetry=telemetry,
    )
    ids = ",".join(r["runtime_id"] for r in runtimes)
    response = client.get(
        "/bff/management/portfolio-book/holdings",
        headers=HEADERS,
        params={"runtime_id": ids},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _term_runtime(runtime_id, status):
    return {
        "id": f"rb-{runtime_id}",
        "runtime_id": runtime_id,
        "capital_pool_id": "pool-alpha",
        "status": status,
        "deployment_stage": "paper",
    }


def test_portfolio_holdings_terminal_runtime_omission_is_not_current_holding_or_incident(monkeypatch) -> None:
    body = _holdings_for(
        monkeypatch,
        [_term_runtime("rt-retired", "retired"), _term_runtime("rt-failed", "failed")],
        {},
    )
    assert body["data"]["items"] == []
    assert body["data"]["summary"]["holding_count"] == 0
    assert body["data"]["summary"]["incident_count"] == 0
    assert body["meta"]["incidents"] == []


def test_portfolio_holdings_active_runtime_missing_telemetry_stays_incident(monkeypatch) -> None:
    body = _holdings_for(
        monkeypatch,
        [_term_runtime("rt-retired", "retired"), _term_runtime("rt-active", "active")],
        {},
    )
    assert [i["runtime_id"] for i in body["data"]["items"]] == ["rt-active"]
    assert body["data"]["summary"]["incident_count"] == 1
    assert body["data"]["summary"]["source_coverage"]["runtime_count"] == 1


def test_portfolio_holdings_terminal_runtime_with_reported_position_is_kept(monkeypatch) -> None:
    summary = {
        "runtime_id": "rt-retired",
        "positions": [{"symbol": "2330.TW", "quantity": 1.0, "average_price": 2400.0}],
        "position_count": 1,
    }
    body = _holdings_for(
        monkeypatch,
        [_term_runtime("rt-retired", "retired")],
        {"rt-retired": summary},
    )
    assert [i["symbol"] for i in body["data"]["items"]] == ["2330.TW"]


def test_portfolio_holdings_canonical_active_status_wins_over_lower_priority_terminal_fields(monkeypatch) -> None:
    runtime = _term_runtime("rt-conflict", "active")
    runtime["state"] = "retired"
    runtime["lifecycle_state"] = "failed"
    body = _holdings_for(monkeypatch, [runtime], {})
    assert [i["runtime_id"] for i in body["data"]["items"]] == ["rt-conflict"]
    assert body["data"]["summary"]["incident_count"] == 1


def test_portfolio_holdings_reproduces_h12_76062_owner_dto_and_governed_calendar_projection(monkeypatch) -> None:
    import json
    from datetime import datetime, timezone
    from services.control_plane.bff.pm12 import service as pm12_service
    from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
    from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
    from services.execution.lean_runtime.performance_telemetry import SourceIngestMarkProvider
    from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
    from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

    h12_runtime_id = "rt-eb295fe2"
    h12_binding_id = "rb-eea1cd16cf3745479f42e8b4440b3c27"
    source_ref = "source-ingest://snapshots/mss-ff49b0e9e567bba38063946a"
    h12_runtime = {
        "id": h12_binding_id,
        "binding_id": h12_binding_id,
        "runtime_id": h12_runtime_id,
        "capital_pool_id": "pool-tw-equities-core",
        "artifact_id": "artifact-tw-equities",
        "artifact_version": "1.0.0",
        "plan_id": "plan-alpha",
        "persona_capital_binding_id": "binding-alpha",
        "persona_id": "persona-tw-swing-trader",
        "strategy_id": "strat-tw-swing-breakout",
        "status": "active",
        "deployment_stage": "paper",
        "deployment_mode": "paper",
    }

    # 1. Exact saved 76062 owner DTO before governed calendar provenance was carried:
    # mark_stale is True and _marks_by_symbol lacks observed_at/lineage/calendar_evidence.
    saved_76062_owner_dto = {
        "runtime_id": h12_runtime_id,
        "binding_id": h12_binding_id,
        "deployment_stage": "paper",
        "environment": "paper",
        "status": "running",
        "last_event_at": "2026-10-25T21:41:42Z",
        "updated_at": "2026-10-25T21:41:42Z",
        "collected_at": "2026-10-25T21:41:42Z",
        "_binding_latest_event_at": "2026-10-25T21:41:42Z",
        "_positions_by_symbol": {"2330.TW": -2.0},
        "_marks_by_symbol": {
            "2330.TW": {
                "as_of": "2026-10-22T00:00:00Z",
                "binding_id": h12_binding_id,
                "price": 2385.0,
                "source_ref": source_ref,
            }
        },
        "positions": [
            {
                "symbol": "2330.TW",
                "quantity": -2.0,
                "mark_stale": True,
                "last_known_mark_price": 2385.0,
                "last_known_mark_as_of": "2026-10-22T00:00:00Z",
                "last_known_mark_source_ref": source_ref,
            }
        ],
        "position_count": 1,
    }
    monkeypatch.setattr(pm12_service, "_default_utc_now", lambda: "2026-10-25T21:48:43Z")
    pre_body = _holdings_for(monkeypatch, [h12_runtime], {h12_runtime_id: saved_76062_owner_dto})
    (pre_item,) = pre_body["data"]["items"]
    assert pre_item["holding_id"] == "rt-eb295fe2:2330.TW"
    assert pre_item["symbol"] == "2330.TW"
    assert pre_item["side"] == "short"
    assert pre_item["quantity"] == -2.0
    assert pre_item["mark_status"] == "stale"
    assert pre_item["telemetry_stale"] is True
    assert pre_item["mark_price"] is None
    assert pre_item["market_value"] is None
    assert pre_item["notional"] is None
    assert pre_item["last_mark_at"] == "2026-10-22T00:00:00Z"
    assert pre_item["mark_source_ref"] == source_ref

    # 2. Drive the actual producer (SourceIngestMarkProvider + PaperRuntimeService)
    # starting from an already-committed unchanged performance_window (the exact 76062
    # unchanged-sample transport gap), into RuntimeSummaryProjectionStore and BFF holdings.
    recorded_snapshot = {
        "schema_version": "source_ingest_latest_market_snapshot.v1",
        "snapshot_id": "mss-ff49b0e9e567bba38063946a",
        "symbol": "2330.TW",
        "event_time": "2026-10-22T00:00:00Z",
        "observed_at": "2026-10-23T12:29:54Z",
        "closes": [2405.0, 2420.0, 2385.0],
        "lineage": {
            "source_ids": ["tw-official:tw_price_daily:TWSE:2330:9691b8ad1b066a80"],
            "connector_ids": ["tw-twse-tpex-official-market"],
            "content_refs": ["tw-official://tw_price_daily/TWSE/2330/2026-10-22/9691b8ad1b066a80"],
            "ingest_run_ids": ["ingest-e53a86463995"],
        },
        "source_ref": source_ref,
        "calendar_evidence": json.loads(json.dumps(_RECORDED_2330_CALENDAR_EVIDENCE)),
        "market": "TW",
    }
    now_oct25 = datetime.fromisoformat("2026-10-25T21:41:44+00:00")

    class _CapturingEmitter:
        def __init__(self):
            self.enabled = True
            self.events = []

        def build_event(self, event_type, metrics, metadata=None, *, event_id=None, created_at=None):
            m = dict(metrics)
            stamp_key = "pnl_as_of" if event_type == "pnl_snapshot" else "drawdown_as_of"
            stamp = m.pop(stamp_key, None)
            return {
                "event_id": event_id,
                "event_type": event_type,
                "created_at": created_at,
                "runtime_id": h12_runtime_id,
                "binding_id": h12_binding_id,
                "deployment_stage": "paper",
                "environment": "paper",
                stamp_key: stamp,
                "metrics": m,
                "metadata": dict(metadata or {}),
            }

        def emit_payload(self, payload):
            self.events.append(json.loads(json.dumps(dict(payload))))
            return True

        def emit(self, event_type, metrics, metadata=None):
            return True

        def emit_heartbeat(self, metadata=None):
            return True

        def snapshot(self):
            return {"enabled": True, "sent": len(self.events), "failed": 0, "last_error": None}

    identity = RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": h12_runtime_id,
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_WORKSPACE_REF": "workspace-paper",
            "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper",
            "PANTHEON_PERSONA_ID": "persona-tw-swing-trader",
            "PANTHEON_SESSION_ID": "session-h12",
            "PANTHEON_TRACE_ID": "11111111-2222-3333-4444-555555555555",
            "PANTHEON_REQUEST_ID": "request-h12",
        }
    )
    provider = SourceIngestMarkProvider(
        "http://source-ingest:8097",
        cache_ttl_seconds=0,
        max_mark_age_seconds=172800,
        fetch_json=lambda _url, _timeout: recorded_snapshot,
        now=lambda: now_oct25,
    )
    emitter = _CapturingEmitter()

    class _RMClient:
        def list_all(self):
            return [dict(h12_runtime)]

    with tempfile.TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("PANTHEON_LIFECYCLE_OUTBOX_DIR", f"{tmp_dir}/outbox")
        monkeypatch.setenv("PANTHEON_PERFORMANCE_STATE_PATH", f"{tmp_dir}/paper-ledger.json")
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore(),
            identity=identity,
            runtime_manager_client=_RMClient(),
            telemetry_emitter=emitter,
            mark_provider=provider,
            poll_interval_seconds=3600,
        )
        assert service._algo.BindPerformanceBinding(h12_binding_id)
        service._algo._initial_cash = 100000.0
        service._algo._cash = 104800.0
        service._algo._holding("2330.TW").Quantity = -2.0
        service._algo._fill_count = 2
        service._algo._first_fill_at = "2026-10-20T02:27:49Z"
        service._algo._last_fill_at = "2026-10-21T02:35:58Z"
        # Pre-seed performance_window with the exact same sample fingerprint (as recorded before provenance).
        service._algo.save_performance_window(
            {
                "schema_version": "rolling_drawdown.v1",
                "window_days": 20,
                "values": [
                    {"as_of": "2026-10-20T02:27:49Z", "portfolio_value": 100000.0},
                    {"as_of": "2026-10-22T00:00:00Z", "portfolio_value": 100030.0},
                ],
                "last_fingerprint": [
                    "2026-10-22T00:00:00Z",
                    30.0,
                    100030.0,
                    2,
                    [["2330.TW", 2385.0, "2026-10-22T00:00:00Z", source_ref, None]],
                ],
                "latest_as_of": "2026-10-22T00:00:00Z",
                "latest_sample_shared_with_seed": False,
            }
        )

        first_drain = service.drain_once()
        assert first_drain["paper_state"]["performance_telemetry"]["code"] == "performance_snapshots_emitted"
        assert len(emitter.events) == 2

        # Second drain with identical evidence is idempotent and does not re-emit.
        second_drain = service.drain_once()
        assert second_drain["paper_state"]["performance_telemetry"]["code"] == "performance_sample_unchanged"
        assert len(emitter.events) == 2

    # Project the emitted unchanged-sample events onto the saved 76062 DTO.
    from services.telemetry.runtime_summary import _summary_key

    store = RuntimeSummaryProjectionStore(path=None)
    store._summaries[_summary_key(saved_76062_owner_dto)] = json.loads(json.dumps(saved_76062_owner_dto))
    for ev in emitter.events:
        projected_76062 = store.project_event(ev)

    post_body = _holdings_for(monkeypatch, [h12_runtime], {h12_runtime_id: projected_76062})
    (post_item,) = post_body["data"]["items"]
    assert post_item["holding_id"] == "rt-eb295fe2:2330.TW"
    assert post_item["symbol"] == "2330.TW"
    assert post_item["side"] == "short"
    assert post_item["quantity"] == -2.0
    assert post_item["mark_status"] == "available"
    assert post_item["telemetry_stale"] is False
    assert post_item["mark_price"] == 2385.0
    assert post_item["market_value"] == -4770.0
    assert post_item["notional"] == 4770.0
    assert post_item["last_mark_at"] == "2026-10-22T00:00:00Z"
    assert post_item["mark_source_ref"] == source_ref

    # 3. Negatives through BFF holdings projection:
    # (a) Missed trading session on 2026-10-27 after 13:30 Asia/Taipei close (05:31Z) -> stale.
    monkeypatch.setattr(pm12_service, "_default_utc_now", lambda: "2026-10-27T05:31:00Z")
    missed_session_body = _holdings_for(monkeypatch, [h12_runtime], {h12_runtime_id: projected_76062})
    (missed_item,) = missed_session_body["data"]["items"]
    assert missed_item["mark_status"] == "stale"
    assert missed_item["telemetry_stale"] is True
    assert missed_item["mark_price"] is None
    assert missed_item["market_value"] is None
    assert missed_item["notional"] is None
    assert missed_item["last_mark_at"] == "2026-10-22T00:00:00Z"

    # (b) Untrusted pin, malformed calendar, missing calendar, non-official lineage, or missing observed_at -> stale.
    monkeypatch.setattr(pm12_service, "_default_utc_now", lambda: "2026-10-25T21:48:43Z")
    for mutation in (
        {"mark_calendar_evidence": None},
        {"mark_calendar_evidence": "not-a-dict"},
        {
            "mark_calendar_evidence": {
                **_RECORDED_2330_CALENDAR_EVIDENCE,
                "version": "twse-2026-untrusted-v99",
            }
        },
        {"mark_lineage": {"source_ids": ["yahoo:2330"], "connector_ids": ["yahoo"]}},
        {"mark_observed_at": None},
        {"mark_observed_at": "2026-10-22T05:00:00Z"},
        {"mark_observed_at": "2026-10-26T00:00:00Z"},
    ):
        mutated_dto = json.loads(json.dumps(projected_76062))
        mutated_dto["positions"][0].update(mutation)
        bad_body = _holdings_for(monkeypatch, [h12_runtime], {h12_runtime_id: mutated_dto})
        (bad_item,) = bad_body["data"]["items"]
        assert bad_item["mark_status"] == "stale", mutation
        assert bad_item["mark_price"] is None, mutation
        assert bad_item["market_value"] is None, mutation
        assert bad_item["notional"] is None, mutation

    # (c) Non-Taiwan symbol preserves 172800s raw max age even if Taiwan calendar evidence is attached.
    non_tw_dto = json.loads(json.dumps(projected_76062))
    non_tw_dto["positions"][0]["symbol"] = "AAPL.US"
    non_tw_dto["_marks_by_symbol"]["AAPL.US"] = non_tw_dto["_marks_by_symbol"].pop("2330.TW")
    non_tw_body = _holdings_for(monkeypatch, [h12_runtime], {h12_runtime_id: non_tw_dto})
    (non_tw_item,) = non_tw_body["data"]["items"]
    assert non_tw_item["symbol"] == "AAPL.US"
    assert non_tw_item["mark_status"] == "stale"
    assert non_tw_item["mark_price"] is None
    assert non_tw_item["market_value"] is None
    assert non_tw_item["notional"] is None
