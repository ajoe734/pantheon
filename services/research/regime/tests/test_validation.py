"""Walk-forward validation: filtered inference, one-session delay, stability, change-point audit."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.research.regime import features as F
from services.research.regime import validation as V
from services.research.regime.models import AxisSession
from services.research.tests.test_regime_research_route import DECISION_TIME, regime_path, small_config, synthetic_observations

CONFIG = small_config(max_switches_per_year=20.0)
OBS, STATE, RET = synthetic_observations(1600, seed=5)


def _vol_inputs(config=CONFIG, horizon="short"):
    return F.build_volatility_inputs(OBS, decision_time=DECISION_TIME, horizon=horizon, config=config)


def _session(i, state):
    return AxisSession(f"2020-01-{i + 1:02d}", state, {state: 1.0}, [[1.0]], {state: 1e9}, 1, None)


def test_signal_is_delayed_by_one_session():
    sessions = [_session(i, s) for i, s in enumerate("aabba")]
    assert V.delayed_signal(sessions, 1) == ["a", "a", "b", "b"]
    assert V.delayed_signal(sessions, 0) == list("aabba")
    assert V.switch_indices(list("aabba")) == [2, 4]
    assert V.run_lengths(list("aabba")) == {"a": [2, 1], "b": [2]}


def test_walk_forward_reports_required_metrics_and_stores_protocol():
    report = V.walk_forward_validate(_vol_inputs(), V.make_fit_fn("volatility", "short", CONFIG), CONFIG)
    assert report.status in ("validated", "failed"), report.reason
    m = report.metrics
    assert m["switches_per_year"] >= 0 and m["refits"] >= CONFIG.min_refits
    assert set(m["expected_durations_sessions"]) <= {"low", "high"} and m["label_stability"] is not None
    assert report.audit["change_points"]["status"] == "computed"
    assert report.protocol["inference"] == "filtered_online_only"
    assert report.protocol["signal_delay_sessions"] == 1
    assert set(report.gates) == {"history", "refits", "switches_per_year", "expected_duration_sessions", "label_stability"}
    assert report.to_dict()["folds"] and all("model_fingerprint" in f for f in report.to_dict()["folds"])


def test_out_of_sample_labels_do_not_change_when_future_data_is_removed():
    """Filtered inference: a prefix run gives the same labels as the full run on the shared rows."""
    inputs = _vol_inputs()
    fit = V.make_fit_fn("volatility", "short", CONFIG)
    frame = inputs.frame
    cut = CONFIG.min_training_sessions
    model = fit(frame.iloc[:cut], inputs.returns.reindex(frame.index[:cut]))
    full = model.infer(frame.iloc[: cut + 300])
    prefix = model.infer(frame.iloc[: cut + 100])
    assert [s.state for s in full[cut:cut + 100]] == [s.state for s in prefix[cut:cut + 100]]


def test_insufficient_history_runs_no_folds_and_cannot_pass():
    inputs = _vol_inputs()
    short = V.walk_forward_validate(inputs, V.make_fit_fn("volatility", "short", CONFIG),
                                    small_config(min_training_years=30.0))
    assert short.status == "insufficient_history" and not short.passed and short.folds == []
    assert short.reason.startswith("history_short:")


def test_unavailable_axis_is_an_error_report_not_a_pass():
    inputs = F.build_trend_inputs([], decision_time=DECISION_TIME, horizon="short", config=CONFIG)
    report = V.walk_forward_validate(inputs, V.make_fit_fn("trend", "short", CONFIG), CONFIG)
    assert report.status == "error" and not report.passed and report.reason.startswith("axis_unavailable:")


def test_gates_fail_when_switching_is_too_frequent():
    strict = small_config(max_switches_per_year=0.1)
    report = V.walk_forward_validate(_vol_inputs(strict), V.make_fit_fn("volatility", "short", strict), strict)
    assert report.status == "failed" and not report.passed and "switches_per_year" in report.reason


def test_changepoint_agreement_audit_with_ruptures():
    signal = np.concatenate([np.zeros(150), np.full(150, 5.0)]).reshape(-1, 1)
    out = V.changepoint_agreement(signal, [152], penalty=10.0, tolerance=10)
    assert out["status"] == "computed" and out["precision"] == 1.0 and out["recall"] == 1.0
    assert V.changepoint_agreement(signal[:30], [], penalty=10.0, tolerance=10)["status"] == "skipped"


def test_jump_axis_walk_forward_selects_penalty_from_grid():
    config = small_config(max_switches_per_year=30.0, jump_penalty_grid=(10.0, 100.0), refit_sessions=200)
    inputs = F.build_risk_appetite_inputs(OBS, decision_time=DECISION_TIME, horizon="short", config=config)
    model, report = V.fit_and_validate(inputs, config)
    assert model is not None and model.method == "jumpmodels-cjm" and model.jump_penalty in (10.0, 100.0)
    assert report.status in ("validated", "failed") and report.metrics["refits"] >= 2


def test_fit_and_validate_fits_candidate_model_when_history_is_short_but_never_passes():
    inputs = _vol_inputs()
    model, report = V.fit_and_validate(inputs, small_config(min_training_years=30.0))
    assert model is not None and report.status == "insufficient_history" and not report.passed
