from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-pm12:operator"}


def _portfolio_store(
    monkeypatch,
    *,
    telemetry_source: str = "canonical",
    telemetry: dict[str, dict[str, Any]] | None = None,
    drift_reports: dict[str, dict[str, Any]] | None = None,
    strategy_specs: list[dict[str, Any]] | None = None,
) -> TestClient:
    td = tempfile.TemporaryDirectory(prefix="bff_pm12_")
    monkeypatch.setattr(bff_main, "_BFF_PM12_TMPDIR", td, raising=False)
    store = ReadSurfaceStore(
        os.path.join(td.name, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
    capital_pools = [
        {
            "id": "pool-alpha",
            "pool_id": "pool-alpha",
            "name": "Alpha Book",
            "status": "active",
            "risk_policy_ref": "risk-alpha",
            "owner_id": "desk-alpha",
            "owner_type": "desk",
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
            "risk_budget": 50.0,
            "current_exposure": 20.0,
            "currency": "USD",
        },
    ]
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
    telemetry_records = telemetry if telemetry is not None else {
        "runtime-alpha": {
            "runtime_id": "runtime-alpha",
            "pnl": 10.0,
            "drawdown": 0.05,
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

    def list_capital_pools(status=None, risk_policy_ref=None):
        pools = list(capital_pools)
        if status:
            pools = [pool for pool in pools if pool.get("status") == status]
        if risk_policy_ref:
            pools = [pool for pool in pools if pool.get("risk_policy_ref") == risk_policy_ref]
        return pools

    store.list_capital_pools = list_capital_pools
    store.list_bindings = lambda persona_id=None, capital_pool_id=None, role=None, validity=None: bindings
    store.list_deployment_plans = lambda status=None, capital_pool_id=None: deployment_plans
    store.list_runtime_bindings = lambda deployment_mode=None, version=None: runtime_bindings
    store.get_telemetry_summary = lambda runtime_id: telemetry_records.get(runtime_id)
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
    monkeypatch.setattr(bff_main, "read_store", store)
    return TestClient(bff_main.app)


def test_portfolio_book_summary_composes_pool_runtime_and_telemetry(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    summary = payload["data"]["summary"]
    assert summary["capital_pool_count"] == 2
    assert summary["active_capital_pool_count"] == 1
    assert summary["binding_count"] == 2
    assert summary["active_binding_count"] == 1
    assert summary["deployment_count"] == 2
    assert summary["approved_deployment_count"] == 1
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

    alpha = payload["items"][0]
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
    assert payload["data"]["items"] == payload["items"]
    assert payload["data"]["pools"] == payload["items"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2}
    assert payload["meta"]["surfaces"]["portfolio_book"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["capital_pools"]["source"] == "canonical"


def test_portfolio_book_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book")

    assert response.status_code == 401, response.text


def test_portfolio_book_exposure_composes_risk_budget_rollup(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/exposure",
        headers=HEADERS,
        params={"page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    summary = payload["summary"]
    assert payload["data"]["id"] == "pm12-portfolio-book-exposure"
    assert payload["items"] == payload["exposures"] == payload["data"]["items"]
    assert payload["data"]["exposures"] == payload["items"]
    assert payload["data"]["summary"] == payload["summary"]
    assert summary["exposure_count"] == 2
    assert summary["returned_exposure_count"] == 2
    assert summary["risk_budget_total"] == 150.0
    assert summary["current_exposure_total"] == 60.0
    assert summary["available_budget_total"] == 90.0
    assert summary["risk_budget_utilization"] == 0.4
    assert summary["over_budget_count"] == 0
    assert summary["near_limit_count"] == 0
    assert summary["unknown_exposure_count"] == 0
    assert summary["telemetry_runtime_count"] == 2
    assert summary["total_pnl"] == 8.0
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 20}

    alpha = payload["items"][0]
    assert alpha["pool_id"] == "pool-alpha"
    assert alpha["capitalPoolId"] == "pool-alpha"
    assert alpha["risk_budget"] == 100.0
    assert alpha["current_exposure"] == 40.0
    assert alpha["risk_budget_utilization"] == 0.4
    assert alpha["risk_state"] == "within_budget"
    assert alpha["available_budget"] == 60.0
    assert alpha["exposure"]["source"] == "capital_pool"
    assert alpha["sourceRefs"]["runtimeIds"] == ["runtime-alpha", "runtime-alpha-live"]
    assert alpha["links"]["capitalPool"] == "/bff/capital-pools/pool-alpha"
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
    assert payload["summary"]["exposure_count"] == 1
    assert payload["summary"]["risk_budget_total"] == 50.0
    assert payload["summary"]["current_exposure_total"] == 20.0
    assert payload["summary"]["telemetry_runtime_count"] == 0
    assert payload["summary"]["total_pnl"] is None
    assert payload["items"][0]["pool_id"] == "pool-beta"


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


def test_portfolio_book_holdings_composes_global_holdings_table(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/holdings", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    summary = payload["summary"]
    assert summary["holding_count"] == 3
    assert summary["returned_holding_count"] == 3
    assert summary["active_holding_count"] == 2
    assert summary["paper_holding_count"] == 2
    assert summary["live_holding_count"] == 1
    assert summary["runtime_count"] == 3
    assert summary["telemetry_runtime_count"] == 2
    assert summary["total_notional"] == 30600
    assert summary["total_market_value"] == 30600
    assert summary["total_unrealized_pnl"] == 200
    assert summary["total_realized_pnl"] == 12
    assert summary["total_pnl"] == 8
    assert summary["latest_mark_at"] == "2026-05-23T08:05:00Z"

    alpha = payload["items"][0]
    assert alpha["holding_id"] == "runtime-alpha:pos-alpha-txf"
    assert alpha["runtime_id"] == "runtime-alpha"
    assert alpha["capital_pool_id"] == "pool-alpha"
    assert alpha["persona_id"] == "persona-alpha"
    assert alpha["strategy_id"] == "strategy-alpha"
    assert alpha["symbol"] == "TXF"
    assert alpha["quantity"] == 2
    assert alpha["mark_price"] == 15300
    assert alpha["market_value"] == 30600
    assert alpha["links"]["runtime"] == "/bff/runtimes/runtime-alpha"
    assert alpha["links"]["capitalPool"] == "/bff/capital-pools/pool-alpha"
    assert payload["data"]["items"] == payload["items"]
    assert payload["data"]["holdings"] == payload["items"]
    assert payload["data"]["summary"] == payload["summary"]
    assert payload["page_info"] == {"next_page_token": None, "total": 3}
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
    assert payload["summary"]["holding_count"] == 1
    assert payload["summary"]["live_holding_count"] == 1
    assert payload["items"][0]["runtime_id"] == "runtime-alpha-live"


def test_portfolio_book_holdings_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/holdings")

    assert response.status_code == 401, response.text


def test_portfolio_book_positions_composes_global_positions_table(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/positions", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    summary = payload["summary"]
    assert summary["position_count"] == 3
    assert summary["returned_position_count"] == 3
    assert summary["active_position_count"] == 2
    assert summary["paper_position_count"] == 2
    assert summary["live_position_count"] == 1
    assert summary["runtime_count"] == 3
    assert summary["telemetry_runtime_count"] == 2
    assert summary["total_notional"] == 30600
    assert summary["total_market_value"] == 30600
    assert summary["total_unrealized_pnl"] == 200
    assert summary["total_realized_pnl"] == 12
    assert summary["total_pnl"] == 8

    alpha = payload["items"][0]
    assert alpha["position_id"] == "runtime-alpha:pos-alpha-txf"
    assert alpha["positionId"] == "runtime-alpha:pos-alpha-txf"
    assert alpha["holding_id"] == "runtime-alpha:pos-alpha-txf"
    assert alpha["runtime_id"] == "runtime-alpha"
    assert alpha["capital_pool_id"] == "pool-alpha"
    assert alpha["persona_id"] == "persona-alpha"
    assert alpha["strategy_id"] == "strategy-alpha"
    assert alpha["symbol"] == "TXF"
    assert alpha["quantity"] == 2
    assert alpha["mark_price"] == 15300
    assert alpha["market_value"] == 30600
    assert alpha["links"]["runtime"] == "/bff/runtimes/runtime-alpha"
    assert alpha["links"]["capitalPool"] == "/bff/capital-pools/pool-alpha"
    assert payload["positions"] == payload["items"]
    assert payload["data"]["items"] == payload["items"]
    assert payload["data"]["positions"] == payload["items"]
    assert payload["data"]["summary"] == payload["summary"]
    assert payload["page_info"] == {"next_page_token": None, "total": 3, "page_size": 50}
    assert payload["meta"]["surfaces"]["portfolio_book_positions"]["source"] == "bff_composed"
    assert "portfolio_book_holdings" not in payload["meta"]["surfaces"]
    assert payload["meta"]["surfaces"]["runtime_bindings"]["source"] == "canonical"
    assert "GET /api/v1/telemetry/{runtime_id}/summary" in payload["meta"]["composition_sources"]


def test_portfolio_book_positions_filters_by_stage(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/portfolio-book/positions",
        headers=HEADERS,
        params={"deployment_stage": "live", "page_size": 1},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["position_count"] == 1
    assert payload["summary"]["live_position_count"] == 1
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 1}
    assert payload["items"][0]["runtime_id"] == "runtime-alpha-live"


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
    assert payload["items"] == payload["rows"] == payload["data"]["rows"]
    assert payload["data"]["items"] == payload["items"]
    assert payload["summary"]["period"] == "30d"
    assert payload["summary"]["dimensions"] == ["asset"]
    assert payload["summary"]["supportedDimensions"] == [
        "persona",
        "strategy",
        "pool",
        "asset",
        "broker",
        "runtime",
        "regime",
    ]
    assert payload["page_info"] == {"next_page_token": None, "total": 3, "page_size": 20}

    rows = {row["dimension_key"]: row for row in payload["items"]}
    txf = rows["TXF"]
    assert txf["dimension"] == "asset"
    assert txf["label"] == "TXF"
    assert txf["period"] == "30d"
    assert txf["metrics"]["totalPnl"] == 10.0
    assert txf["metrics"]["totalTrades"] == 12
    assert txf["metrics"]["runtimeCount"] == 1
    assert txf["sourceRefs"]["runtimeIds"] == ["runtime-alpha"]

    assert rows["NQ"]["metrics"]["totalPnl"] == -2.0
    assert payload["summary"]["totalPnl"] == 8.0
    assert payload["summary"]["telemetryRuntimeCount"] == 2
    assert payload["meta"]["policy"] == "read_only_performance_attribution"
    assert payload["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"
    assert "GET /api/v1/telemetry/{runtime_id}/summary" in payload["meta"]["composition_sources"]


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
    dimensions = {row["dimension"] for row in payload["items"]}
    assert dimensions == {"persona", "strategy", "pool", "asset", "broker", "runtime", "regime"}

    persona_alpha = next(
        row for row in payload["items"]
        if row["dimension"] == "persona" and row["dimension_key"] == "persona-alpha"
    )
    assert persona_alpha["label"] == "persona-alpha"
    assert persona_alpha["metrics"]["totalPnl"] == 10.0
    assert persona_alpha["links"]["persona"] == "/bff/personas/persona-alpha"

    pool_alpha = next(
        row for row in payload["items"]
        if row["dimension"] == "pool" and row["dimension_key"] == "pool-alpha"
    )
    assert pool_alpha["label"] == "Alpha Book"
    assert pool_alpha["metrics"]["totalPnl"] == 8.0

    broker_alpha = next(
        row for row in payload["items"]
        if row["dimension"] == "broker" and row["dimension_key"] == "broker-alpha"
    )
    assert broker_alpha["metrics"]["runtimeCount"] == 2
    assert broker_alpha["metrics"]["totalPnl"] == 8.0

    runtime_alpha = next(
        row for row in payload["items"]
        if row["dimension"] == "runtime" and row["dimension_key"] == "runtime-alpha"
    )
    assert runtime_alpha["links"]["runtime"] == "/bff/runtimes/runtime-alpha"

    regimes = {
        row["dimension_key"]
        for row in payload["items"]
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
    assert payload["items"] == payload["rows"] == payload["data"]["rows"]
    assert payload["data"]["items"] == payload["items"]
    assert payload["summary"]["period"] == "30d"
    assert payload["summary"]["dimensions"] == ["persona"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 20}
    assert {row["dimension"] for row in payload["items"]} == {"persona"}

    rows = {row["dimension_key"]: row for row in payload["items"]}
    assert rows["persona-alpha"]["label"] == "persona-alpha"
    assert rows["persona-alpha"]["metrics"]["totalPnl"] == 10.0
    assert rows["persona-alpha"]["links"]["persona"] == "/bff/personas/persona-alpha"
    assert rows["persona-alpha"]["sourceRefs"]["runtimeIds"] == ["runtime-alpha"]
    assert rows["unassigned"]["label"] == "Unassigned"
    assert rows["unassigned"]["metrics"]["totalPnl"] == -2.0
    assert payload["summary"]["totalPnl"] == 8.0
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
    assert payload["items"] == payload["rows"] == payload["data"]["rows"]
    assert payload["data"]["items"] == payload["items"]
    assert payload["summary"]["period"] == "30d"
    assert payload["summary"]["dimensions"] == ["pool"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 20}
    assert {row["dimension"] for row in payload["items"]} == {"pool"}

    rows = {row["dimension_key"]: row for row in payload["items"]}
    alpha = rows["pool-alpha"]
    assert alpha["label"] == "Alpha Book"
    assert alpha["metrics"]["totalPnl"] == 8.0
    assert alpha["metrics"]["runtimeCount"] == 2
    assert alpha["links"]["capitalPool"] == "/bff/capital-pools/pool-alpha"
    assert alpha["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert alpha["sourceRefs"]["runtimeIds"] == ["runtime-alpha", "runtime-alpha-live"]
    assert alpha["sourceRefs"]["capitalPoolIds"] == ["pool-alpha"]
    assert rows["pool-beta"]["label"] == "Beta Book"
    assert rows["pool-beta"]["metrics"]["totalPnl"] is None
    assert rows["pool-beta"]["metrics"]["telemetryRuntimeCount"] == 0
    assert payload["summary"]["totalPnl"] == 8.0
    assert payload["meta"]["surfaces"]["performance_attribution_by_pool"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"
    assert payload["meta"]["policy"] == "read_only_performance_attribution"


def test_strategy_allocation_returns_active_strategy_allocations_with_drift(monkeypatch) -> None:
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
    assert payload["data"]["id"] == "management-strategy-allocation"
    assert payload["items"] == payload["rows"] == payload["data"]["rows"]
    assert payload["data"]["items"] == payload["items"]
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 20}

    row = payload["items"][0]
    assert row["strategy_id"] == "strategy-alpha"
    assert row["strategyLabel"] == "Alpha Carry"
    assert row["capital_pool_id"] == "pool-alpha"
    assert row["capitalPoolName"] == "Alpha Book"
    assert row["allocationAmount"] == 30600.0
    assert row["allocation"]["source"] == "position_snapshots"
    assert row["runtimeIds"] == ["runtime-alpha"]
    assert row["deploymentPlanIds"] == ["plan-alpha"]
    assert row["personaIds"] == ["persona-alpha"]
    assert row["drift"]["status"] == "breached"
    assert row["drift"]["availableRuntimeCount"] == 1
    assert row["drift"]["breachedMetricCount"] == 1
    assert row["links"]["strategy"] == "/bff/strategies/strategy-alpha"
    assert row["links"]["capitalPool"] == "/bff/capital-pools/pool-alpha"
    assert payload["summary"]["allocationCount"] == 1
    assert payload["summary"]["activeRuntimeCount"] == 1
    assert payload["summary"]["totalAllocatedCapital"] == 30600.0
    assert payload["summary"]["byDriftStatus"] == {"breached": 1}
    assert payload["summary"]["basis"] == "active_runtime_strategy_pool_allocation"
    assert payload["meta"]["surfaces"]["strategy_allocation"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["paper_live_drift"]["source"] == "service_store"
    assert payload["meta"]["policy"] == "read_only_strategy_allocation"


def test_performance_attribution_rejects_invalid_dimension(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/performance-attribution",
        headers=HEADERS,
        params={"dimension": "desk"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "invalid_dimension"


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


def test_portfolio_book_reports_degraded_telemetry_without_hiding_core_book(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["capital_pool_count"] == 2
    assert payload["data"]["summary"]["telemetry_runtime_count"] == 0
    assert payload["data"]["summary"]["total_pnl"] is None
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book"]["status"] == "degraded"


def test_portfolio_book_pools_returns_pool_risk_exposure_and_pnl(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/pools", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"] == payload["items"]
    assert payload["pools"] == payload["items"]
    assert payload["page_info"] == {"next_page_token": None, "total": 2, "page_size": 50}

    alpha = payload["items"][0]
    assert alpha["pool_id"] == "pool-alpha"
    assert alpha["risk_budget"] == 100.0
    assert alpha["riskBudget"] == 100.0
    assert alpha["current_exposure"] == 40.0
    assert alpha["currentExposure"] == 40.0
    assert alpha["risk_budget_utilization"] == 0.4
    assert alpha["exposure"]["source"] == "capital_pool"
    assert alpha["pnl"] == 8.0
    assert alpha["pnl_summary"]["total_pnl"] == 8.0

    summary = payload["summary"]
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
    assert payload["items"][0]["pool_id"] == "pool-alpha"
    assert payload["summary"]["total_pools"] == 1


def test_portfolio_book_pools_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/pools")

    assert response.status_code == 401, response.text


def test_portfolio_book_holdings_reports_degraded_telemetry(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book/holdings", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["holding_count"] == 3
    assert payload["summary"]["telemetry_runtime_count"] == 0
    assert payload["summary"]["total_pnl"] is None
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book_holdings"]["status"] == "degraded"


def test_portfolio_book_positions_reports_degraded_telemetry(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book/positions", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["position_count"] == 3
    assert payload["summary"]["telemetry_runtime_count"] == 0
    assert payload["summary"]["total_pnl"] is None
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book_positions"]["status"] == "degraded"


def test_portfolio_book_is_registered_in_openapi() -> None:
    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()

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
    assert "/bff/management/performance-attribution/by-persona" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/performance-attribution/by-persona"]
    assert "/bff/management/performance-attribution/by-pool" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/performance-attribution/by-pool"]
    assert "/bff/management/strategy-allocation" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/strategy-allocation"]
