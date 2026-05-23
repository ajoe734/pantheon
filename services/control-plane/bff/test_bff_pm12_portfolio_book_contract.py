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
        },
        {
            "id": "pool-beta",
            "pool_id": "pool-beta",
            "name": "Beta Book",
            "status": "suspended",
            "risk_policy_ref": "risk-beta",
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
            "binding_ids": ["binding-alpha"],
        },
        {
            "id": "plan-beta",
            "plan_id": "plan-beta",
            "status": "draft",
            "target_stage": "paper",
            "capital_pool_id": "pool-beta",
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
        },
        "runtime-alpha-live": {
            "runtime_id": "runtime-alpha-live",
            "pnl": -2.0,
            "drawdown": 0.12,
            "fill_rate": 0.8,
            "total_trades": 4,
            "collected_at": "2026-05-23T08:05:00Z",
        },
    }

    store.list_capital_pools = lambda status=None, risk_policy_ref=None: capital_pools
    store.list_bindings = lambda persona_id=None, capital_pool_id=None, role=None, validity=None: bindings
    store.list_deployment_plans = lambda status=None, capital_pool_id=None: deployment_plans
    store.list_runtime_bindings = lambda deployment_mode=None, version=None: runtime_bindings
    store.get_telemetry_summary = lambda runtime_id: telemetry_records.get(runtime_id)

    def dataset_source(dataset: str, **_: Any) -> str:
        if dataset == "telemetry_summaries":
            return telemetry_source
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


def test_portfolio_book_is_registered_in_openapi() -> None:
    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()

    assert "/bff/management/portfolio-book" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/portfolio-book"]
