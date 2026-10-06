"""Real read-port shapes; native composition is tested in its owning suite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.control_plane.bff.management_read_models.router import get_paper_telemetry_read_model
from services.control_plane.bff.management_read_models.service import (
    _build_trading_pulse_baseline_comparison,
    _project_operator_runtime_state_row,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.ports.persona_capital_runtime import RuntimePort


def test_owner_binding_metadata_survives_to_paper_and_trading_pulse():
    owner_row = {
        "binding_id": "binding-distinct", "runtime_id": "runtime-distinct",
        "deployment_mode": "paper", "status": "active",
        "metadata": {"strategy_id": "strategy-distinct", "persona_id": "persona-distinct"},
    }
    port = RuntimePort(runtime_bindings_provider=lambda: [owner_row])
    binding = port.get_runtime_binding("binding-distinct")
    assert binding["strategy_id"] == "strategy-distinct"
    assert binding["persona_id"] == "persona-distinct"
    assert "strategy_id" not in owner_row  # read adapter must not mutate owner state
    store = SimpleNamespace(list_runtime_bindings=port.list_runtime_bindings, list_telemetry_events=lambda: [])
    paper = get_paper_telemetry_read_model(store=store, strategy_id="strategy-distinct")
    assert [r["strategy_id"] for r in paper["items"]] == ["strategy-distinct"]
    assert paper["items"][0]["persona_id"] == "persona-distinct"
    row = _project_operator_runtime_state_row(None, binding, prefetched=True)
    comparison = _build_trading_pulse_baseline_comparison(None, row, prefetched=True)
    assert comparison["strategyId"] == comparison["strategy_id"] == "strategy-distinct"
    assert comparison["runtimeId"] == "runtime-distinct"
    assert comparison["runtimeBindingId"] == "binding-distinct"
    assert comparison["status"] == "unavailable"  # no invented drift evidence


@pytest.mark.parametrize("metadata", [None, [], {}, {"unrelated": "value"}])
def test_binding_id_is_not_promoted_to_strategy_identity(metadata):
    port = RuntimePort(runtime_bindings_provider=lambda: [{"binding_id": "binding-only", "metadata": metadata}])
    assert not port.list_runtime_bindings()[0].get("strategy_id")


def test_existing_explicit_identity_is_preserved():
    port = RuntimePort(runtime_bindings_provider=lambda: [{
        "binding_id": "b", "strategy_id": "explicit", "metadata": {"strategy_id": "legacy"},
    }])
    assert port.list_runtime_bindings()[0]["strategy_id"] == "explicit"


@pytest.mark.parametrize("sessions", [[], [{"runtime_id": "r", "binding_id": "b", "active": True}]])
def test_monitoring_owner_empty_and_nonempty_are_available(sessions):
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=lambda: sessions)
    assert store.list_paper_runtime_monitoring_sessions() == sessions
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "service"


@pytest.mark.parametrize("result", [None, {}, "invalid"])
def test_malformed_monitoring_owner_is_unavailable(result):
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=lambda: result)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "unavailable"


def test_failed_monitoring_owner_is_unavailable():
    def failed():
        raise TimeoutError("owner unavailable")
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=failed)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "unavailable"


def test_unconfigured_monitoring_owner_is_missing(monkeypatch):
    store = create_in_memory_read_surface_ports()
    # The in-memory factory deliberately supplies an empty provider; remove
    # that test owner to exercise the production unconfigured case.
    store._paper_runtime_monitoring_sessions_provider = None
    store._paper_fleet_reconciler_url = None
    monkeypatch.delenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", raising=False)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "missing"


@pytest.mark.parametrize("records", [[], [{"runtime_id": "r-1", "binding_id": "b-1", "deployment_stage": "live"}]])
def test_reconciliation_drift_owner_empty_and_nonempty_are_available(records):
    store = create_in_memory_read_surface_ports(reconciliation_records_provider=lambda **kw: records)
    assert store.dataset_source("paper_live_drift_reports") == "service"


def test_reconciliation_drift_unconfigured_is_unavailable(monkeypatch):
    monkeypatch.delenv("RECONCILIATION_DRIFT_URL", raising=False)
    monkeypatch.delenv("PANTHEON_RECONCILIATION_DRIFT_URL", raising=False)
    store = create_in_memory_read_surface_ports()
    store.reconciliation_drift_reads._records_provider = None
    store.reconciliation_drift_reads._base_url = ""
    assert store.dataset_source("paper_live_drift_reports") == "unavailable"


def test_reconciliation_drift_records_mapping_and_paper_filtering():
    records = [
        {
            "id": "rec-paper-1",
            "runtime_id": "rt-paper",
            "binding_id": "bind-paper",
            "deployment_stage": "paper",
            "generated_at": "2026-10-06T00:00:00Z",
            "delta_summary": {
                "baseline_metrics": {"sharpe": 1.5},
                "observed_metrics": {"sharpe": 1.4},
            },
        },
        {
            "id": "rec-live-1",
            "runtime_id": "rt-live",
            "binding_id": "bind-live",
            "deployment_stage": "live",
            "generated_at": "2026-10-06T01:00:00Z",
            "artifact_id": "art-1",
            "artifact_version": "v1.0.0",
            "plan_id": "plan-1",
            "delta_summary": {
                "baseline_metrics": {"drawdown": 0.05, "avg_slippage_bps": 2.0},
                "observed_metrics": {"drawdown": 0.08, "avg_slippage_bps": 3.5},
                "drift_checks": [
                    {
                        "metric": "drawdown",
                        "status": "warning",
                        "baseline": 0.05,
                        "observed": 0.08,
                        "relative_delta": 0.6,
                    },
                    {
                        "metric": "avg_slippage_bps",
                        "status": "breached",
                        "baseline": 2.0,
                        "observed": 3.5,
                        "relative_delta": 0.75,
                    },
                ],
            },
        },
    ]
    store = create_in_memory_read_surface_ports(reconciliation_records_provider=lambda **kw: records)
    # Paper-only runtime should return None (no live drift comparison)
    assert store.get_paper_live_drift_report("rt-paper") is None

    # Live runtime returns mapped report with baseline, observed, drift groups and threshold evaluation
    report = store.get_paper_live_drift_report("rt-live")
    assert report is not None
    assert report["runtime_id"] == "rt-live"
    assert report["binding_id"] == "bind-live"
    assert report["artifact_id"] == "art-1"
    assert report["artifact_version"] == "v1.0.0"
    assert report["plan_id"] == "plan-1"
    assert report["paper_baseline"]["deployment_stage"] == "paper"
    assert report["paper_baseline"]["metrics"]["drawdown"] == 0.05
    assert report["observed_state"]["deployment_stage"] == "live"
    assert report["observed_state"]["metrics"]["drawdown"] == 0.08
    assert report["threshold_evaluation"]["overall_status"] == "breached"
    assert "avg_slippage_bps" in report["threshold_evaluation"]["breached_metric_ids"]

    # list_paper_live_drift_reports only returns live reports
    reports = store.list_paper_live_drift_reports()
    assert len(reports) == 1
    assert reports[0]["runtime_id"] == "rt-live"
