from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from ports import ReadSurfacePorts  # noqa: E402


HEADERS = {"Authorization": "Bearer op-pm12:operator"}
FOCUS_PERSONA_ID = "persona-20260528-04688755"


class PortfolioBookTestReadPorts(ReadSurfacePorts):
    def __init__(self) -> None:
        super().__init__()
        self._ranking_snapshots: dict[str, Any] = {}

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def put_ranking_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot_id = payload.get("id") or payload.get("ranking_snapshot_id") or "snap-1"
        self._ranking_snapshots[snapshot_id] = payload
        return payload

    def get_ranking_snapshot(self, snapshot_id: str | None) -> dict[str, Any] | None:
        return self._ranking_snapshots.get(str(snapshot_id or ""))


def _portfolio_store(
    monkeypatch,
    *,
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
    assert len(response.content) < 250_000
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"items", "summary"}
    assert "items" not in payload
    assert "pools" not in payload
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


def test_portfolio_book_requires_read_auth(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book")

    assert response.status_code == 401, response.text


def test_portfolio_book_exposure_composes_risk_budget_rollup(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)
    projected_pool_ids: list[str] = []
    original_projector = bff_main._management_portfolio_book_exposure_item

    def tracking_projector(entry: dict[str, Any]) -> dict[str, Any]:
        projected_pool_ids.append(str(entry.get("pool_id") or entry.get("id") or ""))
        return original_projector(entry)

    monkeypatch.setattr(bff_main, "_management_portfolio_book_exposure_item", tracking_projector)

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
    assert summary["returned_exposure_count"] == 1
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
    assert alpha["exposure"]["source"] == "capital_pool"
    assert alpha["source_refs"]["runtime_ids"] == ["runtime-alpha", "runtime-alpha-live"]
    assert "sourceRefs" not in alpha
    assert "runtimeIds" not in alpha["source_refs"]
    assert alpha["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert "capitalPool" not in alpha["links"]
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


def test_portfolio_book_holdings_composes_global_holdings_table(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)
    projected_runtime_ids: list[str] = []
    original_projector = bff_main._management_portfolio_holding_entry

    def tracking_projector(
        runtime: dict[str, Any],
        position: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        projected_runtime_ids.append(str(runtime.get("runtime_id") or runtime.get("id") or ""))
        return original_projector(runtime, position, **kwargs)

    monkeypatch.setattr(bff_main, "_management_portfolio_holding_entry", tracking_projector)

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
    assert summary["returned_holding_count"] == 1
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
    assert payload["data"]["summary"]["live_holding_count"] == 1
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
    assert summary["source_status_counts"] == {"stale": 1}
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
    assert row["identity"]["paper_ledger_ids"] == ["paper-ledger-focus"]
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
    client = _portfolio_store(monkeypatch)

    response = client.get("/bff/management/portfolio-book/positions", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"items", "summary"}
    assert "items" not in payload
    assert "positions" not in payload
    assert "summary" not in payload
    summary = payload["data"]["summary"]
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

    alpha = payload["data"]["items"][0]
    assert alpha["position_id"] == "runtime-alpha:pos-alpha-txf"
    assert "positionId" not in alpha
    assert alpha["holding_id"] == "runtime-alpha:pos-alpha-txf"
    assert alpha["runtime_id"] == "runtime-alpha"
    assert alpha["capital_pool_id"] == "pool-alpha"
    assert "capitalPoolId" not in alpha
    assert alpha["persona_id"] == "persona-alpha"
    assert alpha["strategy_id"] == "strategy-alpha"
    assert alpha["symbol"] == "TXF"
    assert alpha["quantity"] == 2
    assert alpha["mark_price"] == 15300
    assert alpha["market_value"] == 30600
    assert alpha["links"]["runtime"] == "/bff/runtimes/runtime-alpha"
    assert alpha["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert "capitalPool" not in alpha["links"]
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
    assert payload["data"]["summary"]["position_count"] == 1
    assert payload["data"]["summary"]["live_position_count"] == 1
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 1}
    assert payload["data"]["items"][0]["runtime_id"] == "runtime-alpha-live"


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
    assert payload["data"]["summary"]["period"] == "30d"
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
    assert txf["period"] == "30d"
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
    assert payload["data"]["summary"]["period"] == "30d"
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
    assert payload["data"]["summary"]["period"] == "30d"
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
    assert payload["data"]["summary"]["period"] == "30d"
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
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "items", "summary"}
    assert "items" not in payload
    assert "rows" not in payload
    assert "summary" not in payload
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 20}

    row = payload["data"]["items"][0]
    assert row["strategy_id"] == "strategy-alpha"
    assert row["strategy_label"] == "Alpha Carry"
    assert row["capital_pool_id"] == "pool-alpha"
    assert row["capital_pool_name"] == "Alpha Book"
    assert row["allocation_amount"] == 30600.0
    assert row["allocation"]["source"] == "position_snapshots"
    assert row["runtime_ids"] == ["runtime-alpha"]
    assert row["deployment_plan_ids"] == ["plan-alpha"]
    assert row["persona_ids"] == ["persona-alpha"]
    assert row["drift"]["status"] == "breached"
    assert row["drift"]["available_runtime_count"] == 1
    assert row["drift"]["breached_metric_count"] == 1
    assert row["links"]["strategy"] == "/bff/strategies/strategy-alpha"
    assert row["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert "strategyLabel" not in row
    assert "capitalPoolName" not in row
    assert "allocationAmount" not in row
    assert "runtimeIds" not in row
    assert "sourceRefs" not in row
    assert "paperLiveDrift" not in row
    assert "availableRuntimeCount" not in row["drift"]
    assert payload["data"]["summary"]["allocation_count"] == 1
    assert payload["data"]["summary"]["active_runtime_count"] == 1
    assert payload["data"]["summary"]["total_allocated_capital"] == 30600.0
    assert payload["data"]["summary"]["by_drift_status"] == {"breached": 1}
    assert "allocationCount" not in payload["data"]["summary"]
    assert payload["data"]["summary"]["basis"] == "active_runtime_strategy_pool_allocation"
    assert payload["meta"]["surfaces"]["strategy_allocation"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["paper_live_drift"]["source"] == "service_store"
    assert payload["meta"]["policy"] == "read_only_strategy_allocation"


def test_capital_flow_returns_read_only_capital_flow_projection(monkeypatch) -> None:
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
    assert payload["data"]["id"] == "management-capital-flow"
    assert set(payload) == {"data", "page_info", "meta"}
    assert set(payload["data"]) == {"id", "items", "summary"}
    assert "items" not in payload
    assert "rows" not in payload
    assert "flows" not in payload
    assert "summary" not in payload
    assert payload["page_info"] == {"next_page_token": None, "total": 1, "page_size": 20}

    row = payload["data"]["items"][0]
    assert row["capital_pool_id"] == "pool-alpha"
    assert row["capital_pool_name"] == "Alpha Book"
    assert row["persona_id"] == "persona-alpha"
    assert row["strategy_id"] == "strategy-alpha"
    assert row["strategy_label"] == "Alpha Carry"
    assert row["deployment_stage"] == "paper"
    assert row["direction"] == "inflow"
    assert row["net_capital_flow"] == 10.0
    assert row["inflow_amount"] == 10.0
    assert row["outflow_amount"] == 0.0
    assert row["allocated_capital"] == 30600.0
    assert row["runtime_ids"] == ["runtime-alpha"]
    assert row["deployment_plan_ids"] == ["plan-alpha"]
    assert row["persona_capital_binding_ids"] == ["binding-alpha"]
    assert row["links"]["capital_pool"] == "/bff/capital-pools/pool-alpha"
    assert row["links"]["persona"] == "/bff/personas/persona-alpha"
    assert row["links"]["strategy"] == "/bff/strategies/strategy-alpha"
    assert "capitalPoolName" not in row
    assert "netCapitalFlow" not in row
    assert "runtimeIds" not in row
    assert "sourceRefs" not in row
    assert payload["data"]["summary"]["flow_count"] == 1
    assert payload["data"]["summary"]["net_capital_flow"] == 10.0
    assert payload["data"]["summary"]["total_inflow"] == 10.0
    assert payload["data"]["summary"]["total_outflow"] == 0
    assert payload["data"]["summary"]["by_direction"] == {"inflow": 1}
    assert "flowCount" not in payload["data"]["summary"]
    assert payload["data"]["summary"]["basis"] == "runtime_capital_flow_projection_from_allocations_and_pnl"
    assert payload["meta"]["surfaces"]["capital_flow"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["source"] == "canonical"
    assert payload["meta"]["policy"] == "read_only_capital_flow"


def test_capital_flow_filters_outflows(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/capital-flow",
        headers=HEADERS,
        params={"direction": "outflow", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["flow_count"] == 1
    row = payload["data"]["items"][0]
    assert row["direction"] == "outflow"
    assert row["runtime_ids"] == ["runtime-alpha-live"]
    assert row["net_capital_flow"] == -2.0
    assert row["outflow_amount"] == 2.0
    assert "runtimeIds" not in row
    assert "netCapitalFlow" not in row


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
    assert set(payload["data"]) == {"id", "items", "summary"}
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
    assert row["metrics"]["worst_drawdown"] == 0.05
    assert row["metrics"]["total_exposure"] == 30600.0
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
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["source"] == "canonical"
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
    client = _portfolio_store(monkeypatch)

    response = client.get(
        "/bff/management/board-pack",
        headers=HEADERS,
        params={"period": "30d", "section_limit": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["id"] == "management-board-pack"
    assert set(payload) == {"data", "page_info", "meta"}
    assert "items" not in payload
    assert "sections" not in payload
    assert "summary" not in payload
    assert set(data) == {
        "id",
        "snapshot_at",
        "period",
        "section_limit",
        "items",
        "summary",
        "policy",
    }
    assert payload["page_info"] == {"next_page_token": None, "total": 8, "page_size": 8}
    assert data["summary"]["section_count"] == 8
    assert data["summary"]["period"] == "30d"
    assert data["summary"]["section_limit"] == 2
    assert data["summary"]["policy"] == "read_only_management_board_pack"
    for legacy_key in (
        "portfolioBook",
        "portfolio_book",
        "portfolioBookExposure",
        "portfolio_book_exposure",
        "portfolioBookPositions",
        "portfolio_book_positions",
        "strategyAllocation",
        "strategy_allocation",
        "personaLeague",
        "persona_league",
        "performanceAttribution",
        "performance_attribution",
    ):
        assert legacy_key not in data

    sections = data["items"]
    section_ids = {section["id"] for section in sections}
    assert {
        "portfolio_book",
        "portfolio_book_exposure",
        "portfolio_book_positions",
        "strategy_allocation",
        "persona_league",
        "persona_league_movers",
        "performance_attribution_by_persona",
        "performance_attribution_by_pool",
    }.issubset(section_ids)

    by_id = {section["id"]: section for section in sections}
    assert by_id["portfolio_book"]["href"] == "/bff/management/portfolio-book"
    assert by_id["portfolio_book"]["item_count"] >= by_id["portfolio_book"]["returned_item_count"]
    assert by_id["portfolio_book"]["summary"]["capital_pool_count"] == 2
    assert by_id["portfolio_book_exposure"]["summary"]["exposure_count"] >= 1
    assert by_id["portfolio_book_positions"]["summary"]["position_count"] == 3
    assert by_id["strategy_allocation"]["summary"]["allocation_count"] >= 1
    assert by_id["performance_attribution_by_persona"]["summary"]["dimensions"] == ["persona"]
    assert by_id["performance_attribution_by_pool"]["summary"]["dimensions"] == ["pool"]
    assert by_id["persona_league_movers"]["summary"]["mover_count"] >= 0
    assert all("itemCount" not in section and "returnedItemCount" not in section for section in sections)
    assert payload["meta"]["surfaces"]["management_board_pack"]["source"] == "bff_composed"
    assert payload["meta"]["surfaces"]["board_pack"] == payload["meta"]["surfaces"]["management_board_pack"]
    assert payload["meta"]["related"]["portfolio_book"]["href"] == "/bff/management/portfolio-book"
    assert payload["meta"]["related"]["persona_league"]["href"] == "/bff/management/persona-league"
    assert "GET /bff/management/strategy-allocation" in payload["meta"]["composition_sources"]


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
    assert alpha["pnl_summary"]["total_pnl"] == 8.0

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
    assert payload["data"]["summary"]["total_pnl"] is None
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book_holdings"]["status"] == "degraded"


def test_portfolio_book_positions_reports_degraded_telemetry(monkeypatch) -> None:
    client = _portfolio_store(monkeypatch, telemetry_source="missing", telemetry={})

    response = client.get("/bff/management/portfolio-book/positions", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["summary"]["position_count"] == 3
    assert payload["data"]["summary"]["telemetry_runtime_count"] == 0
    assert payload["data"]["summary"]["total_pnl"] is None
    assert payload["meta"]["surfaces"]["telemetry_summaries"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["portfolio_book_positions"]["status"] == "degraded"


def test_portfolio_book_is_registered_in_openapi() -> None:
    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()

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
