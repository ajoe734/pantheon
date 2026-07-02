"""
BFF-B3-004: contract tests for Trading Pulse management aggregates.

The routes are read-only Management aggregates. They compose runtime binding
status with telemetry summaries, then expose cards and computed ranking blocks
without frontend fanout across lower-level runtime and telemetry surfaces.
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-trading:operator,reviewer"}


def _fresh_client(td: str) -> TestClient:
    store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
    store.list_runtime_bindings = lambda: [
        {
            "id": "binding-alpha",
            "binding_id": "binding-alpha",
            "runtime_id": "runtime-alpha",
            "deployment_stage": "paper",
            "status": "running",
            "plan_id": "plan-alpha",
            "artifact_id": "artifact-alpha",
            "artifact_version": "v1",
        },
        {
            "id": "binding-beta",
            "binding_id": "binding-beta",
            "runtime_id": "runtime-beta",
            "deployment_stage": "canary",
            "status": "paused",
            "plan_id": "plan-beta",
            "artifact_id": "artifact-beta",
            "artifact_version": "v2",
        },
    ]
    telemetry_summaries = {
        "runtime-alpha": {
            "runtime_id": "runtime-alpha",
            "runtime_binding_id": "binding-alpha",
            "deployment_stage": "paper",
            "state": "active",
            "window": "1h",
            "pnl": 0.42,
            "drawdown": 0.11,
            "sharpe_ratio": 1.7,
            "fill_rate": 0.9,
            "avg_slippage_bps": 4.8,
            "total_trades": 31,
            "collected_at": "2026-05-23T08:10:00Z",
            "last_heartbeat_at": "2026-05-23T08:10:00Z",
        },
        "runtime-beta": {
            "runtime_id": "runtime-beta",
            "runtime_binding_id": "binding-beta",
            "deployment_stage": "canary",
            "state": "paused",
            "window": "1h",
            "pnl": -0.12,
            "drawdown": 0.04,
            "sharpe_ratio": 0.8,
            "fill_rate": 0.88,
            "avg_slippage_bps": 3.1,
            "total_trades": 11,
            "collected_at": "2026-05-23T08:08:00Z",
            "last_heartbeat_at": "2026-05-23T08:08:00Z",
        },
    }
    drift_reports = {
        "runtime-alpha": {
            "runtime_id": "runtime-alpha",
            "artifact_id": "artifact-alpha",
            "paper_baseline": {
                "captured_at": "2026-05-23T07:00:00Z",
                "deployment_stage": "paper",
                "window": "1h",
                "metrics": {
                    "pnl": 0.25,
                    "drawdown": 0.08,
                    "fill_rate": 0.91,
                    "avg_slippage_bps": 4.0,
                },
            },
            "observed_state": {
                "deployment_stage": "paper",
                "runtime_status": "running",
                "observed_at": "2026-05-23T08:10:00Z",
                "metrics": {
                    "pnl": 0.42,
                    "drawdown": 0.11,
                    "fill_rate": 0.9,
                    "avg_slippage_bps": 4.8,
                },
            },
            "drift_groups": [
                {
                    "group_id": "performance",
                    "label": "Performance",
                    "status": "watch",
                    "metrics": [
                        {
                            "metric_id": "drawdown",
                            "baseline_value": 0.08,
                            "observed_value": 0.11,
                            "delta": 0.03,
                            "status": "watch",
                        }
                    ],
                }
            ],
            "threshold_evaluation": {
                "overall_status": "watch",
                "summary": "Drawdown drift is inside the watch band.",
                "breached_metric_ids": [],
            },
        },
        "runtime-beta": {
            "runtime_id": "runtime-beta",
            "artifact_id": "artifact-beta",
            "paper_baseline": {
                "captured_at": "2026-05-23T07:00:00Z",
                "deployment_stage": "paper",
                "window": "1h",
                "metrics": {
                    "pnl": 0.05,
                    "drawdown": 0.03,
                    "fill_rate": 0.9,
                    "avg_slippage_bps": 2.6,
                },
            },
            "observed_state": {
                "deployment_stage": "canary",
                "runtime_status": "paused",
                "observed_at": "2026-05-23T08:08:00Z",
                "metrics": {
                    "pnl": -0.12,
                    "drawdown": 0.04,
                    "fill_rate": 0.88,
                    "avg_slippage_bps": 3.1,
                },
            },
            "drift_groups": [
                {
                    "group_id": "execution",
                    "label": "Execution",
                    "status": "breached",
                    "metrics": [
                        {
                            "metric_id": "avg_slippage_bps",
                            "baseline_value": 2.6,
                            "observed_value": 3.1,
                            "delta": 0.5,
                            "status": "breached",
                        }
                    ],
                }
            ],
            "threshold_evaluation": {
                "overall_status": "breached",
                "summary": "Slippage drift breached the canary baseline.",
                "breached_metric_ids": ["avg_slippage_bps"],
            },
        },
    }
    store.get_telemetry_summary = lambda runtime_id: telemetry_summaries.get(runtime_id)
    store.get_paper_live_drift_report = lambda runtime_id: drift_reports.get(runtime_id)
    store.get_rollbacks = lambda runtime_id: []
    store.dataset_source = lambda dataset, **kwargs: {
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "paper_live_drift_reports": "service_store",
        "rollbacks": "service_store",
    }.get(dataset, "missing")
    bff_main.read_store = store
    return TestClient(bff_main.app)


def test_trading_pulse_returns_card_aggregate_and_runtime_rankings() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/trading-pulse", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            summary = data["summary"]
            assert data["id"] == "management-trading-pulse"
            assert "items" not in body
            assert "summary" not in body
            assert summary["runtime_count"] == 2
            assert summary["telemetry_coverage_count"] == 2
            assert summary["total_pnl"] == 0.3
            assert summary["worst_drawdown"] == 0.11
            assert summary["average_fill_rate"] == 0.89
            assert summary["worst_slippage_bps"] == 4.8
            assert summary["total_trades"] == 42
            assert summary["by_status"] == {"running": 1, "paused": 1}
            assert summary["by_stage"] == {"paper": 1, "canary": 1}
            assert summary["baseline_comparison_count"] == 2
            assert summary["baseline_breached_count"] == 1
            assert summary["baseline_watch_count"] == 1
            assert summary["by_baseline_status"] == {"watch": 1, "breached": 1}

            assert len(data["cards"]) == 5
            assert data["rankings"][0]["runtime_id"] == "runtime-alpha"
            assert data["rankings"][0]["rank"] == 1
            assert data["rankings"][0]["baseline_comparison_status"] == "watch"
            assert data["runtime_rows"][0]["telemetry_summary"]["metrics"]["pnl"] == 0.42
            assert (
                data["runtime_rows"][0]["baseline_comparison"]["paper_baseline"]["metrics"]["pnl"]
                == 0.25
            )
            assert data["baseline_comparisons"][1]["status"] == "breached"
            assert data["baseline_comparisons"][1]["paper_live_drift"]["available"] is True
            assert body["page_info"] == {
                "next_page_token": None,
                "total": 5,
                "page_size": 5,
            }
            assert body["meta"]["surfaces"]["management_trading_pulse"]["source"] == "bff_composed"
            assert body["meta"]["surfaces"]["runtime_roster"]["source"] == "canonical"
            assert body["meta"]["surfaces"]["telemetry_summary"]["source"] == "service_store"
            assert body["meta"]["surfaces"]["paper_live_drift"]["source"] == "service_store"
            assert body["meta"]["surfaces"]["baseline_comparison"]["source"] == "bff_composed"
        finally:
            bff_main.read_store = original_store


def test_trading_pulse_rankings_returns_computed_blocks_with_limit() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get(
                "/bff/management/trading-pulse/rankings?limit=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            summary = data["summary"]
            assert set(data) == {"id", "items", "summary"}
            assert "items" not in body
            assert "rankings" not in body
            assert summary["runtime_count"] == 2
            assert summary["ranking_block_count"] == 4
            assert summary["ranked_item_count"] == 4
            assert summary["limit"] == 1
            assert body["page_info"] == {
                "next_page_token": None,
                "total": 4,
                "page_size": 4,
            }

            blocks = {block["block_id"]: block for block in data["items"]}
            assert blocks["pnl-leaders"]["items"][0]["runtime_id"] == "runtime-alpha"
            assert blocks["pnl-leaders"]["items"][0]["ranking_metric"] == "pnl"
            assert blocks["drawdown-control"]["items"][0]["runtime_id"] == "runtime-beta"
            assert blocks["execution-quality"]["items"][0]["ranking_metric"] == "fill_rate"
            assert blocks["sharpe-leaders"]["items"][0]["runtime_id"] == "runtime-alpha"
            assert blocks["pnl-leaders"]["items"][0]["baseline_comparison_status"] == "watch"
            assert (
                body["meta"]["surfaces"]["management_trading_pulse_rankings"]["source"]
                == "bff_composed"
            )
            assert body["meta"]["surfaces"]["baseline_comparison"]["source"] == "bff_composed"
        finally:
            bff_main.read_store = original_store


def test_trading_pulse_routes_require_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            for path in (
                "/bff/management/trading-pulse",
                "/bff/management/trading-pulse/rankings",
            ):
                resp = client.get(path)

                assert resp.status_code == 401, resp.text
                assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            bff_main.read_store = original_store
