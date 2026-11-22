"""
BFF-B3-004: contract tests for Trading Pulse management aggregates.

The routes are read-only Management aggregates. They compose runtime binding
status with telemetry summaries, then expose cards and computed ranking blocks
without frontend fanout across lower-level runtime and telemetry surfaces.
"""
from __future__ import annotations

import tempfile

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_read_role,
)
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models.router import (
    create_management_router,
)
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

@pytest.fixture(autouse=True)
def _tenant_scope():
    from services.control_plane.bff.core.owner_reads import selected_tenant
    token = selected_tenant.set("tenant-test")
    yield
    selected_tenant.reset(token)


OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-trading:operator,reviewer"}


def _fresh_client(
    td: str, *, include_gap: bool = False, drop_metric: str | None = None
) -> TestClient:
    store = create_in_memory_read_surface_ports()
    runtime_bindings = [
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
    if include_gap:
        runtime_bindings.append(
            {
                "id": "binding-gamma",
                "binding_id": "binding-gamma",
                "runtime_id": "runtime-gamma",
                "deployment_stage": "paper",
                "status": "running",
                "plan_id": "plan-gamma",
                "artifact_id": "artifact-gamma",
                "artifact_version": "v3",
            }
        )
    for binding in runtime_bindings:
        binding.update(persona_id="p-" + binding["runtime_id"], tenant_id="tenant-test")
    store.list_personas = lambda: [{"id": b["persona_id"], "name": b["persona_id"], "tenant_id": "tenant-test"} for b in runtime_bindings]
    store.list_runtime_bindings = lambda: list(runtime_bindings)
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
    if drop_metric:
        for summary in telemetry_summaries.values():
            summary[drop_metric] = None
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
    monitoring_sessions = {
        "runtime-alpha": {
            "session_id": "monitor-alpha",
            "binding_id": "binding-alpha",
            "runtime_binding_id": "binding-alpha",
            "runtime_id": "runtime-alpha",
            "deployment_stage": "paper",
            "status": "active",
            "active": True,
            "started_at": "2026-05-23T07:30:00Z",
            "last_heartbeat_at": "2026-05-23T08:10:00Z",
        }
    }
    store.get_telemetry_summary = lambda runtime_id: telemetry_summaries.get(runtime_id)
    for record in telemetry_summaries.values():
        record.update(drawdown_at="2026-05-23T08:00:00Z", sharpe_ratio_at="2026-05-23T08:00:00Z", fill_rate_at="2026-05-23T08:00:00Z", tenant_id="tenant-test", pnl_currency="TWD", pnl_period_start_at="2026-05-23T00:00:00Z", pnl_at="2026-05-23T08:00:00Z", pnl_definition="net_ledger_pnl")
    for runtime_id, record in drift_reports.items():
        record.update(tenant_id="tenant-test", binding_id="binding-" + runtime_id.removeprefix("runtime-"))
    for record in monitoring_sessions.values():
        record["tenant_id"] = "tenant-test"
    store.list_telemetry_summaries = lambda: list(telemetry_summaries.values())
    store.get_paper_live_drift_report = lambda runtime_id: drift_reports.get(runtime_id)
    store.list_paper_live_drift_reports = lambda: list(drift_reports.values())
    store.list_paper_runtime_monitoring_sessions = lambda: list(monitoring_sessions.values())
    store.get_paper_runtime_monitoring_session = (
        lambda runtime_id=None, binding_id=None: monitoring_sessions.get(runtime_id)
        or next(
            (
                session
                for session in monitoring_sessions.values()
                if session.get("binding_id") == binding_id
                or session.get("runtime_binding_id") == binding_id
            ),
            None,
        )
    )
    store.get_rollbacks = lambda runtime_id: []
    store.dataset_source = lambda dataset, **kwargs: {
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "paper_runtime_monitoring_sessions": "service_store",
        "paper_live_drift_reports": "service_store",
        "rollbacks": "service_store",
    }.get(dataset, "missing")
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )
    return TestClient(app)


def test_trading_pulse_returns_card_aggregate_and_runtime_rankings() -> None:
    with tempfile.TemporaryDirectory() as td:
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
        assert summary["total_pnl"] is None
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
        assert summary["row_health_degraded_count"] == 0
        assert summary["row_health_status_counts"] == {"ok": 2}
        assert summary["monitoring_coverage_count"] == 1
        assert summary["missing_monitoring_runtime_ids"] == []
        assert summary["coverage"]["metric_coverage"]["pnl"]["available_count"] == 2
        assert "rowHealthDegradedCount" not in summary
        assert "rowHealthStatusCounts" not in summary
        assert "monitoringCoverageCount" not in summary
        assert "metricCoverage" not in summary["coverage"]

        assert len(data["cards"]) == 6
        assert {card["card_id"] for card in data["cards"]} >= {"row-health"}
        assert data["rankings"][0]["runtime_id"] == "runtime-alpha"
        assert data["rankings"][0]["rank"] == 1
        assert data["rankings"][0]["baseline_comparison_status"] == "watch"
        assert "rowHealthStatus" not in data["rankings"][0]
        assert "rowHealthDegradedChecks" not in data["rankings"][0]
        rows_by_runtime = {row["runtime_id"]: row for row in data["runtime_rows"]}
        assert data["runtime_rows"][0]["runtime_id"] == "runtime-beta"
        assert rows_by_runtime["runtime-alpha"]["telemetry_summary"]["metrics"]["pnl"] == 0.42
        assert (
            rows_by_runtime["runtime-alpha"]["baseline_comparison"]["paper_baseline"]["metrics"]["pnl"]
            == 0.25
        )
        comparisons_by_runtime = {
            comparison["runtime_id"]: comparison
            for comparison in data["baseline_comparisons"]
        }
        assert comparisons_by_runtime["runtime-beta"]["status"] == "breached"
        assert comparisons_by_runtime["runtime-beta"]["paper_live_drift"]["available"] is True
        assert body["page_info"] == {
            "next_page_token": None,
            "total": 6,
            "page_size": 6,
        }
        assert body["meta"]["surfaces"]["management_trading_pulse"]["source"] == "bff_composed"
        assert body["meta"]["surfaces"]["runtime_roster"]["source"] == "canonical"
        assert body["meta"]["surfaces"]["telemetry_summary"]["source"] == "service_store"
        assert body["meta"]["surfaces"]["paper_runtime_monitoring"]["source"] == "service_store"
        assert body["meta"]["surfaces"]["paper_live_drift"]["source"] == "service_store"
        assert body["meta"]["surfaces"]["baseline_comparison"]["source"] == "bff_composed"
        assert body["meta"]["surfaces"]["runtime_row_health"]["status"] == "ok"


def test_trading_pulse_exposes_operator_coverage_gaps_and_row_health() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, include_gap=True)
        resp = client.get("/bff/management/trading-pulse", headers=OPERATOR_HEADERS)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]
        summary = data["summary"]
        assert summary["runtime_count"] == 3
        assert summary["telemetry_coverage_count"] == 2
        assert summary["baseline_comparison_count"] == 2
        assert summary["monitoring_coverage_count"] == 1
        assert summary["row_health_degraded_count"] == 1
        assert summary["row_health_status_counts"] == {"degraded": 1, "ok": 2}
        assert summary["missing_telemetry_runtime_ids"] == ["runtime-gamma"]
        assert summary["missing_monitoring_runtime_ids"] == ["runtime-gamma"]
        assert summary["missing_baseline_runtime_ids"] == ["runtime-gamma"]
        assert summary["metric_coverage"]["pnl"]["missing_runtime_ids"] == ["runtime-gamma"]

        assert data["runtime_rows"][0]["runtime_id"] == "runtime-gamma"
        assert data["runtime_rows"][0]["row_health"]["status"] == "degraded"
        assert set(data["runtime_rows"][0]["row_health"]["degraded_checks"]) == {
            "telemetry_summary",
            "paper_runtime_monitoring",
        }
        assert data["runtime_rows"][0]["baseline_comparison"]["status"] == "unavailable"

        surfaces = body["meta"]["surfaces"]
        assert surfaces["management_trading_pulse"]["status"] == "degraded"
        assert surfaces["telemetry_summary"]["status"] == "degraded"
        assert surfaces["paper_runtime_monitoring"]["status"] == "degraded"
        assert surfaces["paper_live_drift"]["status"] == "degraded"
        assert surfaces["runtime_row_health"]["status"] == "degraded"
        assert body["meta"]["coverage"]["missing_baseline_runtime_ids"] == ["runtime-gamma"]


def test_trading_pulse_rankings_returns_computed_blocks_with_limit() -> None:
    with tempfile.TemporaryDirectory() as td:
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
        assert summary["eligible_item_count"] == 8
        assert summary["missing_metric_item_count"] == 0
        assert summary["limit"] == 1
        assert "eligibleItemCount" not in summary
        assert "missingMetricItemCount" not in summary
        assert body["page_info"] == {
            "next_page_token": None,
            "total": 4,
            "page_size": 4,
        }

        blocks = {block["block_id"]: block for block in data["items"]}
        assert blocks["pnl-leaders"]["eligible_item_count"] == 2
        assert blocks["pnl-leaders"]["missing_metric_count"] == 0
        assert "blockId" not in blocks["pnl-leaders"]
        assert "sortOrder" not in blocks["pnl-leaders"]
        assert "eligibleItemCount" not in blocks["pnl-leaders"]
        assert "missingMetricCount" not in blocks["pnl-leaders"]
        assert "missingMetricRuntimeIds" not in blocks["pnl-leaders"]
        assert blocks["pnl-leaders"]["items"][0]["runtime_id"] == "runtime-alpha"
        assert blocks["pnl-leaders"]["items"][0]["ranking_eligible"] is True
        assert blocks["pnl-leaders"]["items"][0]["ranking_metric"] == "pnl"
        assert blocks["drawdown-control"]["items"][0]["runtime_id"] == "runtime-beta"
        assert blocks["execution-quality"]["items"][0]["ranking_metric"] == "fill_rate"
        assert blocks["execution-quality"]["secondary_metric"] == "avg_slippage_bps"
        assert "secondaryMetric" not in blocks["execution-quality"]
        assert blocks["sharpe-leaders"]["items"][0]["runtime_id"] == "runtime-alpha"
        assert blocks["pnl-leaders"]["items"][0]["baseline_comparison_status"] == "watch"
        assert (
            body["meta"]["surfaces"]["management_trading_pulse_rankings"]["source"]
            == "bff_composed"
        )
        assert body["meta"]["surfaces"]["baseline_comparison"]["source"] == "bff_composed"


def test_trading_pulse_rankings_exclude_missing_metrics() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, include_gap=True)
        resp = client.get(
            "/bff/management/trading-pulse/rankings?limit=5",
            headers=OPERATOR_HEADERS,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]
        blocks = {block["block_id"]: block for block in data["items"]}
        assert blocks["pnl-leaders"]["eligible_item_count"] == 2
        assert blocks["pnl-leaders"]["missing_metric_count"] == 1
        assert blocks["pnl-leaders"]["missing_metric_runtime_ids"] == ["runtime-gamma"]
        assert [item["runtime_id"] for item in blocks["pnl-leaders"]["items"]] == [
            "runtime-alpha",
            "runtime-beta",
        ]
        assert data["summary"]["missing_metric_item_count"] == 4
        assert body["meta"]["surfaces"]["management_trading_pulse_rankings"]["status"] == "degraded"


def _rankings_for_dropped_metric(metric: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, drop_metric=metric)
        resp = client.get(
            "/bff/management/trading-pulse/rankings?limit=5",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()


def test_rankings_missing_sharpe_alone_does_not_degrade_surface() -> None:
    body = _rankings_for_dropped_metric("sharpe_ratio")
    blocks = {block["block_id"]: block for block in body["data"]["items"]}
    assert blocks["sharpe-leaders"]["missing_metric_count"] == 2
    assert blocks["sharpe-leaders"]["items"] == []
    assert body["data"]["summary"]["missing_metric_item_count"] == 2
    assert body["meta"]["surfaces"]["management_trading_pulse_rankings"]["status"] != "degraded"


@pytest.mark.parametrize("metric", ["pnl", "drawdown", "fill_rate"])
def test_rankings_missing_other_metric_still_degrades_surface(metric: str) -> None:
    body = _rankings_for_dropped_metric(metric)
    assert body["data"]["summary"]["missing_metric_item_count"] >= 2
    assert body["meta"]["surfaces"]["management_trading_pulse_rankings"]["status"] == "degraded"


def test_trading_pulse_routes_require_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        for path in (
            "/bff/management/trading-pulse",
            "/bff/management/trading-pulse/rankings",
        ):
            resp = client.get(path)

            assert resp.status_code == 401, resp.text
            assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


# --- BFF-TRADING-PULSE-CURRENT-FLEET-20261105 -------------------------------
from services.control_plane.bff.management_read_models.service import (  # noqa: E402
    ManagementService,
)


class _FleetStore:
    def __init__(self, bindings, sessions, telemetry=(), drift=()):
        self.bindings = [{**b, "persona_id": "p-" + b["runtime_id"], "tenant_id": "tenant-test"} for b in bindings]
        self.sessions = [{**r, "tenant_id": "tenant-test"} for r in sessions]
        self.telemetry = [{**r, "binding_id": "b-" + r["runtime_id"].removeprefix("r-"), "tenant_id": "tenant-test"} for r in telemetry]
        self.drift = [{**r, "binding_id": "b-" + r["runtime_id"].removeprefix("r-"), "tenant_id": "tenant-test"} for r in drift]
        self.drift_calls = 0

    def list_personas(self):
        return [{"id": b["persona_id"], "name": b["persona_id"], "tenant_id": "tenant-test"} for b in self.bindings]

    def list_runtime_bindings(self):
        return list(self.bindings)

    def list_telemetry_summaries(self):
        return list(self.telemetry)

    def list_paper_live_drift_reports(self):
        self.drift_calls += 1
        return list(self.drift)

    def list_paper_runtime_monitoring_sessions(self):
        return list(self.sessions)


def _binding(n, status, stage="paper", **extra):
    return {
        "id": f"b-{n}",
        "binding_id": f"b-{n}",
        "runtime_id": f"r-{n}",
        "deployment_stage": stage,
        "status": status,
        **extra,
    }


def _telemetry(n):
    return {
        "runtime_id": f"r-{n}",
        "window": "1h",
        "collected_at": "2026-11-05T00:00:00Z",
        "pnl": 1.0,
        "drawdown": 0.1,
        "fill_rate": 0.9,
        "avg_slippage_bps": 2.0,
        "total_trades": 3,
    }


def _hosted_shaped_store(sessions_order="ended_first"):
    bindings = (
        [_binding(i, "active") for i in range(4)]
        + [_binding(10 + i, "paused") for i in range(6)]
        + [_binding(20 + i, "failed") for i in range(3)]
        + [_binding(100 + i, "retired") for i in range(112)]
        + [_binding(300, "active", retired_at="2026-10-01T00:00:00Z")]
    )
    sessions = []
    for i in range(4):
        ended = {
            "session_id": f"e{i}", "runtime_id": f"r-{i}", "binding_id": f"b-{i}",
            "status": "ended", "active": False,
            "ended_at": "2026-11-04T00:00:00Z", "ended_reason": "market_input_missing",
        }
        running = {
            "session_id": f"s{i}", "runtime_id": f"r-{i}", "binding_id": f"b-{i}",
            "status": "running", "active": True, "started_at": "2026-11-05T00:00:00Z",
        }
        sessions += [ended, running] if sessions_order == "ended_first" else [running, ended]
    return _FleetStore(bindings, sessions, telemetry=[_telemetry(i) for i in range(4)])


def test_trading_pulse_excludes_retired_and_failed_from_operator_counts() -> None:
    store = _hosted_shaped_store()
    data = ManagementService(read_store=store).get_trading_pulse()["data"]
    summary = data["summary"]
    assert summary["runtime_count"] == 10
    assert summary["retired_runtime_count"] == 0
    assert len(data["runtime_rows"]) == 10
    assert len(data["rankings"]) == 10
    assert data["cards"][0]["value"] == 10
    assert summary["by_status"] == {"active": 4, "paused": 6}
    ids = {r["runtime_id"] for r in data["runtime_rows"]}
    assert not any(i in ids for i in ("r-20", "r-21", "r-22", "r-100", "r-300"))


def test_trading_pulse_prefers_non_ended_monitoring_session_in_either_order() -> None:
    for order in ("ended_first", "running_first"):
        store = _hosted_shaped_store(order)
        rows = {
            r["runtime_id"]: r
            for r in ManagementService(read_store=store).get_trading_pulse()["data"]["runtime_rows"]
        }
        for i in range(4):
            health = rows[f"r-{i}"]["row_health"]
            assert "paper_runtime_monitoring" not in (health.get("degraded_checks") or []), (order, health)
            assert health["status"] == "ok", (order, health)


def test_trading_pulse_all_ended_sessions_still_degraded_using_latest() -> None:
    store = _FleetStore(
        [_binding(1, "active")],
        [
            {"runtime_id": "r-1", "binding_id": "b-1", "status": "ended", "ended_at": "2026-11-03T00:00:00Z"},
            {"runtime_id": "r-1", "binding_id": "b-1", "status": "ended", "ended_at": "2026-11-04T00:00:00Z"},
        ],
        telemetry=[_telemetry(1)],
    )
    row = ManagementService(read_store=store).get_trading_pulse()["data"]["runtime_rows"][0]
    assert row["row_health"]["status"] != "ok"
    assert row["paper_runtime_monitoring"]["ended_at"] == "2026-11-04T00:00:00Z"


def test_trading_pulse_drift_read_only_when_live_or_canary_row_exists() -> None:
    paper_only = _hosted_shaped_store()
    body = ManagementService(read_store=paper_only).get_trading_pulse()
    assert paper_only.drift_calls == 0
    assert (
        body["meta"]["surfaces"]["paper_live_drift"]["message"]
        == "No live runtimes require paper/live baseline comparison."
    )

    with_canary = _FleetStore([_binding(1, "active", stage="canary")], [], telemetry=[_telemetry(1)])
    ManagementService(read_store=with_canary).get_trading_pulse()
    assert with_canary.drift_calls == 1


# --- BFF-PULSE-BASELINE-SCOPE-20261106 --------------------------------------
def _drift(n):
    return {
        "runtime_id": f"r-{n}",
        "threshold_evaluation": {"overall_status": "ok", "breached_metric_ids": []},
        "drift_groups": [],
    }


def _pulse(store):
    body = ManagementService(read_store=store).get_trading_pulse()
    data = body["data"]
    rows = {r["runtime_id"]: r for r in data["runtime_rows"]}
    return body, data, rows


def test_baseline_not_required_for_all_paper_fleet_without_reports() -> None:
    store = _hosted_shaped_store()
    body, data, rows = _pulse(store)
    assert store.drift_calls == 0
    summary = data["summary"]
    assert summary["missing_baseline_runtime_ids"] == []
    assert summary["baseline_required_count"] == 0
    assert summary["coverage"]["baseline_required_count"] == 0
    assert body["meta"]["coverage"]["baseline_required_count"] == 0
    card = next(c for c in data["cards"] if c["card_id"] == "baseline-comparison")
    assert card["details"]["baseline_required_count"] == 0
    assert card["details"]["missing_baseline_runtime_ids"] == []
    expected = {"not_required": len(rows)}
    assert len(rows) >= 4
    assert summary["by_baseline_status"] == expected
    assert card["details"]["by_baseline_status"] == expected
    for i in range(4):
        comparison = rows[f"r-{i}"]["baseline_comparison"]
        assert comparison["status"] == "not_required"
        assert comparison["threshold_evaluation"]["overall_status"] == "not_required"
    assert {
        r["runtime_id"]: r["baseline_comparison_status"] for r in data["rankings"]
    }["r-0"] == "not_required"
    assert body["meta"]["surfaces"]["paper_live_drift"]["status"] == "ok"
    assert rows["r-0"]["row_health"]["status"] == "ok"


def test_baseline_mixed_fleet_covered_live_and_uncovered_paper_not_required() -> None:
    store = _FleetStore(
        [_binding(1, "active", stage="live"), _binding(2, "active")],
        [],
        telemetry=[_telemetry(1), _telemetry(2)],
        drift=[_drift(1)],
    )
    body, data, rows = _pulse(store)
    assert data["summary"]["baseline_required_count"] == 1
    assert data["summary"]["missing_baseline_runtime_ids"] == []
    assert rows["r-1"]["baseline_comparison"]["status"] == "ok"
    assert rows["r-2"]["baseline_comparison"]["status"] == "not_required"
    assert body["meta"]["surfaces"]["paper_live_drift"]["status"] == "ok"
    assert data["summary"]["by_baseline_status"] == {"ok": 1, "not_required": 1}
    card = next(c for c in data["cards"] if c["card_id"] == "baseline-comparison")
    assert card["details"]["by_baseline_status"] == {"ok": 1, "not_required": 1}


def test_baseline_live_row_without_report_stays_missing_and_degraded() -> None:
    store = _FleetStore(
        [_binding(1, "active", stage="live"), _binding(2, "active")],
        [],
        telemetry=[_telemetry(1), _telemetry(2)],
        drift=[],
    )
    body, data, rows = _pulse(store)
    assert data["summary"]["baseline_required_count"] == 1
    assert data["summary"]["missing_baseline_runtime_ids"] == ["r-1"]
    assert rows["r-1"]["baseline_comparison"]["status"] == "unavailable"
    assert rows["r-2"]["baseline_comparison"]["status"] == "not_required"
    assert body["meta"]["surfaces"]["paper_live_drift"]["status"] == "degraded"
    assert data["summary"]["by_baseline_status"] == {"unavailable": 1, "not_required": 1}


def test_baseline_partial_paper_coverage_stays_degraded() -> None:
    store = _FleetStore(
        [_binding(1, "active", stage="live"), _binding(2, "active"), _binding(3, "active")],
        [],
        telemetry=[_telemetry(1), _telemetry(2), _telemetry(3)],
        drift=[_drift(1), _drift(2)],
    )
    body, data, rows = _pulse(store)
    assert data["summary"]["baseline_required_count"] == 3
    assert data["summary"]["missing_baseline_runtime_ids"] == ["r-3"]
    assert rows["r-3"]["baseline_comparison"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["paper_live_drift"]["status"] == "degraded"
    # not_required sorts after every status needing attention
    from services.control_plane.bff.management_read_models import service as svc

    order = svc._TRADING_PULSE_BASELINE_OPERATOR_ORDER
    assert all(order["not_required"] > v for k, v in order.items() if k != "not_required")


# Operator facts: exercise raw owner shapes, independent of wire DTO snapshots.
SNAP = "2026-11-22T12:00:00Z"


class _OperatorStore:
    def __init__(self):
        self.people = [
            {"id": "p1", "name": "青禾", "tenant_id": "tenant-test"},
            {"id": "p2", "name": "澄川", "tenant_id": "tenant-test"},
            {"id": "probe", "name": "release persona", "tenant_id": "tenant-test", "metadata": {"is_probe": True}},
            {"id": "foreign", "name": "private", "tenant_id": "other"},
        ]
        self.bindings = [{"id": "b-" + pid, "binding_id": "b-" + pid, "runtime_id": "r-" + pid,
            "persona_id": pid, "tenant_id": tenant, "deployment_stage": "paper", "status": "active"}
            for pid, tenant in (("p1", "tenant-test"), ("p2", "tenant-test"), ("probe", "tenant-test"), ("foreign", "other"))]
        self.records = [{"binding_id": b["binding_id"], "runtime_id": b["runtime_id"], "tenant_id": b["tenant_id"],
            "deployment_stage": "paper", "collected_at": SNAP, "last_event_at": SNAP,
            "pnl": -168.085 if b["persona_id"] == "p1" else None,
            "pnl_currency": "TWD", "pnl_period_start_at": "2026-11-01T00:00:00Z", "pnl_at": SNAP,
            "pnl_definition": "net_ledger_pnl", "ledger_fill_count": 1 if b["persona_id"] == "p1" else 0,
            "drawdown_at": SNAP, "sharpe_ratio_at": SNAP, "window": "1h",
            "avg_slippage_bps": 0, "avg_slippage": 99, "sharpe_ratio_reason": "insufficient_samples",
            "fill_rate_details": {"status": "known", "numerator": 0, "denominator": 452, "unit": "shares",
                                  "basis": "filled_quantity_over_submitted_quantity", "window": {"start_at": "2026-11-01T00:00:00Z", "end_exclusive_at": SNAP}}}
            for b in self.bindings]
        self.calls = 0
        self.reader = None

    def list_personas(self):
        self.calls += 1
        return self.people

    def list_runtime_bindings(self):
        return self.bindings

    def list_telemetry_summaries(self):
        return self.records

    def trade_journey_projection_reader(self):
        return self.reader


def _operator_pulse(store):
    return ManagementService(read_store=store).get_trading_pulse(snapshot_at=SNAP)


@pytest.mark.parametrize("surface", ["pulse", "rankings", "cockpit"])
@pytest.mark.parametrize("response", ["ok", "empty", "unavailable", "foreign", "wrong_id"])
def test_strategy_enrichment_uses_only_admitted_ids_and_bounded_owner_contract(surface, response, monkeypatch):
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core.owner_reads import authorization
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")
    store, calls = _OperatorStore(), []
    for binding in store.bindings:
        binding["strategy_id"] = "strategy-" + binding["persona_id"]
    store.bindings[1]["strategy_id"] = "strategy-p1"
    # Two admitted bindings share one strategy; artifacts without a strategy
    # identity, excluded personas and unverified bindings must not be probed.
    artifact_only = {**store.bindings[1], "id": "b-artifact", "binding_id": "b-artifact",
                     "runtime_id": "r-artifact", "artifact_id": "raw-artifact-id"}
    artifact_only.pop("strategy_id")
    store.bindings.extend([artifact_only,
        {**store.bindings[0], "status": "retired", "strategy_id": "retired-strategy"},
        {**store.bindings[0], "status": "failed", "strategy_id": "failed-strategy"},
        {**store.bindings[0], "tenant_id": None, "strategy_id": "unverified-strategy"}])

    def http_read(url, *, method="GET", tenant_id=None, **kwargs):
        calls.append((url, method, tenant_id))
        if response == "unavailable":
            raise RuntimeError("registry offline")
        if response == "empty":
            return []
        return [{"registry_id": "reg-current", "artifact_type": "strategy_spec",
                 "artifact_state": "approved", "version": "1.0.0",
                 "strategy_id": "wrong" if response == "wrong_id" else "strategy-p1",
                 "owner_tenant": "other" if response == "foreign" else "tenant-test",
                 "metadata": {"strategy_spec": {"title": "合格策略", "market_scope": {"symbols": ["2330.TWSE"]}}}}]

    monkeypatch.setattr(adapter_base, "http_request_json", http_read)
    ports = ReadSurfacePorts(research_knowledge_source=DefaultResearchKnowledgeSourcePort())
    store.get_strategy_spec_detail = ports.get_strategy_spec_detail
    # Record forbidden calls even though the production enrichment tolerates
    # owner exceptions; a swallowed AssertionError cannot make this test pass.
    store.list_strategies = lambda: calls.append(("FULL_LIST", None, None)) or []
    store.trade_journey_projection_reader = lambda: calls.append(("JOURNEY", None, None))
    service = ManagementService(read_store=store)
    if surface == "cockpit":
        for method in ("get_operator_health_status", "get_operator_home", "get_human_inbox",
                       "get_operator_alerts", "get_management_anomalies"):
            monkeypatch.setattr(service, method, lambda **kwargs: {"data": {}, "meta": {"surfaces": {}}})
    token = authorization.set("Bearer bounded:operator:tenant-test")
    try:
        for _ in range(2):  # A new snapshot retries even a failed prior read.
            if surface == "pulse":
                data = service.get_trading_pulse(snapshot_at=SNAP)["data"]
            elif surface == "rankings":
                data = service.get_trading_pulse_rankings(snapshot_at=SNAP)["data"]
            else:
                data = service.get_management_cockpit(snapshot_at=SNAP)["data"]["trading_pulse"]
            assert data["summary"]["runtime_count"] == 3
            if surface != "rankings":
                row = next(r for r in data["runtime_rows"] if r["runtime_id"] == "r-p1")
                assert row["strategy_name"] == ("合格策略" if response == "ok" else None)
                assert row["underlying_symbol"] == ("2330.TWSE" if response == "ok" else None)
                assert row["persona_name"] and row["links"]["strategy"].endswith("/strategy-p1")
                assert row["telemetry_summary"]["metrics"]["pnl"] == -168.085
            else:
                assert any(block["items"] for block in data["items"])
    finally:
        authorization.reset(token)
    strategy_calls = [c for c in calls if c[0] != "JOURNEY"]
    assert strategy_calls == [("http://registry.internal/api/registry/strategies/strategy-p1/strategy-specs", "GET", "tenant-test")] * 2
    assert sum(c[0] == "JOURNEY" for c in calls) == (2 if surface == "pulse" else 0)


@pytest.mark.parametrize("surface", ["rankings", "cockpit"])
def test_operator_summaries_do_not_open_or_hydrate_journeys(surface, monkeypatch):
    store = _OperatorStore()
    reads = []
    def reader():
        reads.append("opened")
        raise AssertionError("summary must not load journey counts or stages")
    store.trade_journey_projection_reader = reader
    service = ManagementService(read_store=store)
    if surface == "rankings":
        data = service.get_trading_pulse_rankings(snapshot_at=SNAP)["data"]
        assert data["summary"]["operator_scope"]["runtime_count"] == 2
        assert any(block["items"] for block in data["items"])
    else:
        # Isolate unrelated cockpit contributors; exercise its real pulse path.
        for method in ("get_operator_health_status", "get_operator_home", "get_human_inbox",
                       "get_operator_alerts", "get_management_anomalies"):
            monkeypatch.setattr(service, method, lambda **kwargs: {"data": {}, "meta": {"surfaces": {}}})
        data = service.get_management_cockpit(snapshot_at=SNAP)["data"]["trading_pulse"]
        assert data["summary"]["operator_scope"]["runtime_count"] == 2
        assert all(row["latest_outcome"]["status"] == "not_requested" for row in data["runtime_rows"])
    assert reads == []


def test_operator_scope_filters_every_output_and_counts_distinct_people():
    store = _OperatorStore()
    store.bindings.append({**store.bindings[0], "id": "b-extra", "binding_id": "b-extra", "runtime_id": "r-extra"})
    body = _operator_pulse(store)
    data = body["data"]
    scope = data["summary"]["operator_scope"]
    assert scope["status"] == "verified"
    assert scope["persona_count"] == 2 and scope["runtime_count"] == 3
    assert scope["by_stage"] == {"paper": {"persona_count": 2, "runtime_count": 3}}
    assert store.calls == 1
    assert {r["persona_id"] for r in data["runtime_rows"]} == {"p1", "p2"}
    assert "probe" not in str(body) and "foreign" not in str(body) and "private" not in str(body)
    ranked = ManagementService(read_store=store).get_trading_pulse_rankings(snapshot_at=SNAP)
    assert ranked["data"]["summary"]["operator_scope"] == scope
    assert "probe" not in str(ranked) and "foreign" not in str(ranked)


def test_operator_zero_fill_ledger_and_unknown_pnl_are_distinct():
    data = _operator_pulse(_OperatorStore())["data"]
    row = next(r for r in data["runtime_rows"] if r["persona_id"] == "p2")
    metrics = row["telemetry_summary"]["metrics"]
    assert metrics["total_trades"] == 0 and metrics["pnl"] is None
    assert metrics["fill_rate"] == 0 and metrics["avg_slippage_bps"] == 0
    ctx = row["performance_context"]
    assert ctx["ledger_fill_count"] == 0
    assert ctx["metric_reasons"]["pnl"] == "no_fills"
    assert ctx["metric_reasons"]["sharpe_ratio"] == "insufficient_samples"
    assert ctx["fill_rate_details"]["denominator"] == 452
    assert ctx["last_fill_at"] is None and ctx["latest_report_at"] == SNAP
    assert data["summary"]["pnl_groups"][0]["covered_persona_count"] == 1
    assert data["summary"]["pnl_groups"][0]["eligible_persona_count"] == 2


@pytest.mark.parametrize("location", ["metadata", "top_level", "both"])
def test_operator_binding_tenant_wire_locations_preserve_scoped_joins(location):
    import copy
    from services.control_plane.bff.ports.persona_capital_runtime import RuntimePort
    store = _OperatorStore()
    for binding in store.bindings:
        if location in {"metadata", "both"}:
            binding["metadata"] = {"tenant_id": binding["tenant_id"]}
        if location == "metadata":
            binding.pop("tenant_id")
            binding["deployment_mode"] = binding.pop("deployment_stage")
    original = copy.deepcopy(store.bindings)
    port = RuntimePort(runtime_bindings_provider=lambda: store.bindings)
    store.list_runtime_bindings = port.list_runtime_bindings
    data = _operator_pulse(store)["data"]
    scope = data["summary"]["operator_scope"]
    assert scope["status"] == "verified"
    assert scope["runtime_count"] == scope["persona_count"] == 2
    assert {r["persona_id"] for r in data["runtime_rows"]} == {"p1", "p2"}
    assert {r["tenant_id"] for r in data["runtime_rows"]} == {"tenant-test"}
    rows = {r["persona_id"]: r for r in data["runtime_rows"]}
    assert rows["p1"]["telemetry_summary"]["metrics"]["pnl"] == -168.085
    assert rows["p2"]["telemetry_summary"]["metrics"]["total_trades"] == 0
    assert rows["p2"]["telemetry_summary"]["metrics"]["fill_rate"] == 0
    ranked = ManagementService(read_store=store).get_trading_pulse_rankings(snapshot_at=SNAP)["data"]
    assert ranked["summary"]["operator_scope"] == scope
    assert "foreign" not in str(ranked) and "probe" not in str(ranked)
    assert store.bindings == original


@pytest.mark.parametrize("top,metadata", [("tenant-test", "other"), ("other", "tenant-test"), (None, None)])
def test_operator_binding_tenant_conflicts_never_fall_back_to_persona(top, metadata):
    store = _OperatorStore()
    store.bindings[0].update(tenant_id=top, metadata={"tenant_id": metadata})
    data = _operator_pulse(store)["data"]
    assert data["summary"]["operator_scope"]["status"] == "partial"
    assert data["summary"]["operator_scope"]["runtime_count"] is None
    assert {r["persona_id"] for r in data["runtime_rows"]} == {"p2"}
    assert "r-p1" not in str(data)


@pytest.mark.parametrize("inactive", [{"status": "retired"}, {"status": "FAILED"}, {"retired_at": SNAP}])
def test_operator_excludes_old_and_failed_bindings(inactive):
    store = _OperatorStore()
    store.bindings.append({**store.bindings[0], "id": "old", "binding_id": "old", "runtime_id": "r-old", **inactive})
    data = _operator_pulse(store)["data"]
    assert data["summary"]["operator_scope"]["status"] == "verified"
    assert data["summary"]["runtime_count"] == 2
    assert "r-old" not in str(data)


def test_no_intents_is_not_zero_percent_and_real_zero_pnl_is_preserved():
    store = _OperatorStore()
    store.records[0].update(pnl=0, drawdown=0, sharpe_ratio=0, sharpe_ratio_reason=None)
    store.records[1]["fill_rate_details"].update(numerator=0, denominator=0, reason="no_submitted_orders")
    rows = {r["persona_id"]: r for r in _operator_pulse(store)["data"]["runtime_rows"]}
    assert rows["p1"]["telemetry_summary"]["metrics"]["pnl"] == 0
    assert rows["p1"]["telemetry_summary"]["metrics"]["drawdown"] == 0
    assert rows["p2"]["telemetry_summary"]["metrics"]["fill_rate"] is None
    assert rows["p2"]["performance_context"]["metric_reasons"]["fill_rate"] == "no_submitted_orders"


@pytest.mark.parametrize("change", [{"binding_id": "old"}, {"tenant_id": "other"}, {"deployment_stage": "live"}, {"collected_at": "2027-01-01T00:00:00Z"}])
def test_operator_rejects_mismatched_telemetry(change):
    store = _OperatorStore()
    store.records[0].update(change)
    rows = {r["persona_id"]: r for r in _operator_pulse(store)["data"]["runtime_rows"]}
    assert rows["p1"]["telemetry_summary"] is None
    assert rows["p1"]["total_trades"] is None
    assert rows["p1"]["performance_context"]["source_status"] == "unavailable"


@pytest.mark.parametrize("failure", ["personas", "bindings"])
def test_roster_failure_is_unknown_and_never_leaks_identity(failure):
    store = _OperatorStore()
    def fail():
        raise RuntimeError("owner unavailable")
    if failure == "personas":
        store.list_personas = fail
    else:
        store.list_runtime_bindings = fail
    data = _operator_pulse(store)["data"]
    assert data["runtime_rows"] == [] and data["rankings"] == []
    assert data["summary"]["operator_scope"]["status"] == "unavailable"
    assert data["summary"]["operator_scope"]["persona_count"] is None
    assert data["summary"]["runtime_count"] is None
    assert data["cards"][0]["value"] is None


def test_partial_directory_does_not_expose_unverified_binding():
    store = _OperatorStore()
    store.people = store.people[1:]
    data = _operator_pulse(store)["data"]
    assert data["summary"]["operator_scope"]["status"] == "partial"
    assert data["summary"]["operator_scope"]["persona_count"] is None
    assert {r["persona_id"] for r in data["runtime_rows"]} == {"p2"}
    assert "r-p1" not in str(data)


@pytest.mark.parametrize("people", [[], [{"id": "probe", "tenant_id": "tenant-test", "metadata": {"is_probe": True}}]])
def test_verified_empty_and_all_probe_fleets(people):
    store = _OperatorStore()
    store.people = people
    store.bindings = [b for b in store.bindings if b["persona_id"] in {p["id"] for p in people}]
    data = _operator_pulse(store)["data"]
    assert data["summary"]["operator_scope"]["status"] == "verified"
    assert data["summary"]["runtime_count"] == 0
    assert data["summary"]["operator_scope"]["persona_count"] == 0
    assert data["runtime_rows"] == []


def test_mixed_currency_mode_and_period_are_never_summed_or_competed():
    store = _OperatorStore()
    store.records[1].update(pnl=5, pnl_currency="USD")
    body = _operator_pulse(store)
    assert body["data"]["summary"]["total_pnl"] is None
    assert len(body["data"]["summary"]["pnl_groups"]) == 2
    blocks = ManagementService(read_store=store).get_trading_pulse_rankings(snapshot_at=SNAP)["data"]["items"]
    pnl = next(b for b in blocks if b["metric"] == "pnl")
    assert [i["rank"] for i in pnl["items"]] == [1, 1]
    assert {i["ranking_unit"] for i in pnl["items"]} == {"USD", "TWD"}
    for change in ({"pnl_at": "2026-11-22T11:00:00Z"}, {"deployment_stage": "live"}):
        store.records[1].update(pnl_currency="TWD", **change)
        if "deployment_stage" in change:
            store.bindings[1].update(change)
        assert _operator_pulse(store)["data"]["summary"]["total_pnl"] is None


def test_stale_and_undefined_sharpe_preserve_source_reasons_and_skip_rankings():
    store = _OperatorStore()
    store.records[0].update(staleness={"age_seconds": 1000, "threshold_seconds": 180}, sharpe_ratio_reason="zero_variance")
    body = _operator_pulse(store)
    row = next(r for r in body["data"]["runtime_rows"] if r["persona_id"] == "p1")
    assert row["performance_context"]["source_status"] == "stale"
    assert row["performance_context"]["metric_reasons"]["sharpe_ratio"] == "zero_variance"
    blocks = ManagementService(read_store=store).get_trading_pulse_rankings(snapshot_at=SNAP)["data"]["items"]
    assert not next(b for b in blocks if b["metric"] == "pnl")["items"]


@pytest.fixture
def outcome_caller(request):
    from services.control_plane.bff.core.owner_reads import authorization
    stage, role, quantity = request.param
    token = authorization.set(f"Bearer outcome:{role}:tenant-test")
    yield stage, quantity
    authorization.reset(token)


@pytest.mark.parametrize("outcome_caller", [
    ("paper", "viewer", 452), ("live", "viewer", None), ("live", "operator", 452),
], indirect=True)
def test_actual_journey_contract_supplies_refusal_and_rejects_old_binding(outcome_caller):
    from types import SimpleNamespace
    from services.trade_journey.materializer import JourneyProjection
    event = {"event_id": "projected", "source_event_id": "immutable", "occurred_at": "2026-11-19T23:46:17Z",
        "stage": "risk_evaluation", "stage_status": "rejected", "reason_code": "insufficient_cash", "symbol": "2317.TWSE",
        "submitted_to_broker": False, "requested_quantity": 452,
        "reason_detail": {"required": 100034.3461, "available": 100000, "currency": "TWD", "requested_quantity": 452}}
    stage, quantity = outcome_caller
    projection = JourneyProjection("tj-refusal", "tenant-test", stage, [event], {
        "status": "failed", "stages": {"risk_evaluation": {"status": "rejected"}},
        "identifiers": {"runtime_id": ["r-p2"], "binding_id": ["b-p2"], "persona_id": ["p2"]},
        "updated_at": event["occurred_at"]})
    class Reader:
        def page_journeys(self, **kwargs):
            assert kwargs["page_size"] == 1 and kwargs["filters"]["date_to"] == SNAP
            assert kwargs["filters"]["binding_id"] in {"b-p1", "b-p2"}
            return SimpleNamespace(items=[projection] if kwargs["filters"]["binding_id"] == "b-p2" else [])
    store = _OperatorStore()
    for binding in store.bindings:
        binding["deployment_stage"] = stage
    store.reader = Reader()
    row = next(r for r in _operator_pulse(store)["data"]["runtime_rows"] if r["persona_id"] == "p2")
    outcome = row["latest_outcome"]
    assert outcome["reason_code"] == "insufficient_cash" and outcome["submitted_to_broker"] is False
    assert outcome["required"] == 100034.3461 and outcome["available"] == 100000
    assert outcome["requested_quantity"] == quantity and outcome["source_event_id"] == "immutable"
    assert outcome["currency"] == "TWD" and "tj-refusal" in row["links"]["journey"]
    assert row["underlying_symbol"] == "2317.TWSE"
    assert row["telemetry_summary"]["metrics"]["total_trades"] == 0
    projection.snapshot["identifiers"]["binding_id"] = ["old-binding"]
    row = next(r for r in _operator_pulse(store)["data"]["runtime_rows"] if r["persona_id"] == "p2")
    assert row["latest_outcome"]["reason_code"] is None and "journey" not in row["links"]
    assert row["underlying_symbol"] is None


def test_actual_owner_port_directory_failure_cannot_become_empty_fleet():
    from services.control_plane.bff.ports.persona_capital_runtime import PersonaFleetPort, PersonaCapitalRuntimeDomainPort
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
    def fail():
        raise RuntimeError("persona registry offline")
    port = PersonaFleetPort(records_provider=fail)
    store = ReadSurfacePorts(persona_capital_runtime=PersonaCapitalRuntimeDomainPort(persona_port=port))
    store.list_runtime_bindings = lambda: []
    scope = _operator_pulse(store)["data"]["summary"]["operator_scope"]
    assert scope["status"] == "unavailable" and scope["persona_count"] is None


def test_no_caller_scope_fails_closed_even_with_readable_owners():
    from services.control_plane.bff.core.owner_reads import selected_tenant
    token = selected_tenant.set(None)
    try:
        body = _operator_pulse(_OperatorStore())
        assert body["data"]["runtime_rows"] == []
        assert body["data"]["summary"]["operator_scope"]["reason"] == "tenant_scope_unverified"
    finally:
        selected_tenant.reset(token)


def test_metric_own_binding_and_nonfinite_values_cannot_supply_facts():
    store = _OperatorStore()
    store.records[0].update(pnl_binding_id="old-binding", drawdown=float("nan"), sharpe_ratio=float("inf"))
    row = next(r for r in _operator_pulse(store)["data"]["runtime_rows"] if r["persona_id"] == "p1")
    assert row["telemetry_summary"]["metrics"]["pnl"] is None
    assert row["telemetry_summary"]["metrics"]["drawdown"] is None
    assert row["telemetry_summary"]["metrics"]["sharpe_ratio"] is None
    assert row["performance_context"]["metric_reasons"]["pnl"] == "binding_mismatch"


def test_known_quarter_equity_provenance_can_name_pnl_definition():
    store = _OperatorStore()
    store.records[0].pop("pnl_definition")
    store.records[0]["quarter_equity_observations"] = [{"status": "known", "binding_id": "b-p1", "as_of": SNAP,
        "valuation_method": "fill_cash_ledger_mark_to_market"}]
    data = _operator_pulse(store)["data"]
    assert data["summary"]["pnl_groups"][0]["pnl_definition"] == "fill_cash_ledger_mark_to_market"
    store.records[0]["quarter_equity_observations"][0]["binding_id"] = "old"
    data = _operator_pulse(store)["data"]
    assert not any(g["value"] is not None for g in data["summary"]["pnl_groups"])
    assert not next(r for r in data["rankings"] if r["persona_id"] == "p1")["ranking_eligible"]


def test_observed_metric_with_unknown_period_is_not_comparable():
    store = _OperatorStore()
    store.records[0].pop("drawdown_at")
    store.records[0].update(drawdown=0.02)
    data = ManagementService(read_store=store).get_trading_pulse_rankings(snapshot_at=SNAP)["data"]
    block = next(b for b in data["items"] if b["metric"] == "drawdown")
    assert block["items"] == []
    assert block["metric_reasons"]["r-p1"] == "comparison_scope_unverified"


def test_reconciliation_mapping_preserves_tenant_for_scoped_baselines():
    from services.control_plane.bff.ports.reconciliation_drift_reads import map_reconciliation_record_to_drift_report
    source = {"tenant_id": "other", "runtime_id": "r-p1", "binding_id": "b-p1", "deployment_stage": "live", "generated_at": SNAP,
              "delta_summary": {"baseline_metrics": {"pnl": 1}, "observed_metrics": {"pnl": 2}}}
    mapped = map_reconciliation_record_to_drift_report(source)
    assert mapped["tenant_id"] == "other"
    store = _OperatorStore()
    store.bindings[0]["deployment_stage"] = "live"
    store.records[0]["deployment_stage"] = "live"
    store.list_paper_live_drift_reports = lambda: [mapped]
    body = _operator_pulse(store)
    assert body["data"]["summary"]["baseline_comparison_count"] == 0
    assert body["data"]["summary"]["missing_baseline_runtime_ids"] == ["r-p1"]
