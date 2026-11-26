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


# --- Cadence units (REGIME-MEDIUM-CADENCE-UNIT-FIX-20261126) -------------------------------------

HOSTED_DECISION_TIME = "2035-01-01T00:00:00Z"


def test_hosted_shaped_medium_taiex_series_is_not_reported_history_short():
    """3,640 closes -> 3,639 daily returns -> 727 five-session rows (the hosted TAIEX shape).

    R11 asks for 2,450 sessions of training plus a 122-session refit: 490 + 25 five-session rows,
    not 2,572 rows (which would be 12,860 sessions, i.e. 52.49 years).
    """
    config = F.RegimeConfig()
    obs, _, _ = synthetic_observations(3640, seed=11, with_breadth=False, with_chip=False, with_macro=False)
    inputs = F.build_volatility_inputs(obs, decision_time=HOSTED_DECISION_TIME, horizon="medium", config=config)
    assert inputs.available and inputs.cadence_sessions == 5 and len(inputs.frame) == 727
    report = V.walk_forward_validate(inputs, V.make_fit_fn("volatility", "medium", config, cadence_sessions=5), config)
    assert report.status in ("validated", "failed"), report.reason
    assert not report.reason.startswith("history_short")
    p = report.protocol
    assert (p["cadence_sessions"], p["min_training_rows"], p["refit_rows"], p["signal_delay_rows"]) == (5, 490, 25, 1)
    assert (p["min_training_sessions"], p["refit_sessions"], p["signal_delay_sessions"]) == (2450, 122, 1)
    assert p["inference"] == "filtered_online_only"
    assert report.gates["history"] == {"value": 3635.0, "threshold": 2450.0, "passed": True}
    # Folds: 490-row expanding start, 25-row (125-session) refits; the last fold may be shorter.
    folds = report.folds
    assert folds[0]["train_rows"] == 490 and folds[0]["train_sessions"] == 2450
    assert all(f["test_rows"] == 25 and f["test_sessions"] == 125 for f in folds[:-1])
    assert sum(f["test_rows"] for f in folds) == 727 - 490 and len(folds) == 10
    m = report.metrics
    assert m["oos_rows"] == 727 - 490 - 1 and m["oos_sessions"] == (727 - 490 - 1) * 5
    assert m["switches_per_year"] == pytest.approx(m["switches"] / ((727 - 490 - 1) * 5 / 245))


def test_short_cadence_daily_series_still_requires_daily_session_history():
    config = F.RegimeConfig()
    inputs = F.build_volatility_inputs(OBS, decision_time=DECISION_TIME, horizon="short", config=config)
    assert inputs.cadence_sessions == 1
    report = V.walk_forward_validate(inputs, V.make_fit_fn("volatility", "short", config), config)
    assert report.status == "insufficient_history" and report.reason == f"history_short:{len(inputs.frame)}<2572"
    assert report.protocol["min_training_rows"] == 2450 and report.protocol["refit_rows"] == 122


class _ScriptedModel:
    """Deterministic stand-in: labels come from a fixed script, so rates/durations are exact."""

    method = "scripted"

    def __init__(self, script):
        self.script = script

    def infer(self, frame):
        return [_session(i % 28, self.script[i]) for i in range(len(frame))]

    def fingerprint(self):
        return "scripted"

    def expected_durations(self):
        return {"a": 4.0, "b": 2.0}


# 50 training rows, then 50 out-of-sample rows; after the one-row delay 49 labels remain with
# three switches and runs a=[10,10], b=[10,19].
_SCRIPT = list("a" * 50 + "a" * 10 + "b" * 10 + "a" * 10 + "b" * 19 + "b")


@pytest.mark.parametrize("cadence, training_sessions, refit_sessions, per_year, durations", [
    # 49 five-session rows = 245 sessions = one year: 3 switches -> 3.0 per year.
    (5, 250, 122, 3.0, {"a": 50.0, "b": 72.5}),
    # The same 49 rows at one session each = 0.2 years: 3 switches -> 15.0 per year.
    (1, 50, 25, 15.0, {"a": 10.0, "b": 14.5}),
])
def test_switch_rate_and_run_lengths_are_in_market_sessions(cadence, training_sessions, refit_sessions, per_year,
                                                            durations):
    config = F.RegimeConfig(min_training_years=training_sessions / 245, refit_sessions=refit_sessions,
                            max_switches_per_year=6.0, min_refits=2)
    assert config.min_training_sessions == training_sessions
    index = pd.bdate_range("2020-01-01", periods=100)
    frame = pd.DataFrame({"return_pct": np.random.default_rng(0).normal(0, 1, 100)}, index=index)
    inputs = F.AxisInputs("volatility", "medium", True, frame=frame, returns=frame["return_pct"],
                          sessions=100, cadence_sessions=cadence)
    model = _ScriptedModel(_SCRIPT)
    report = V.walk_forward_validate(inputs, lambda train, returns: model, config)
    assert report.protocol["min_training_rows"] == 50 and report.protocol["refit_rows"] == 25
    m = report.metrics
    assert m["switches"] == 3 and m["oos_rows"] == 49 and m["oos_sessions"] == 49 * cadence
    assert m["switches_per_year"] == pytest.approx(per_year)
    assert m["expected_durations_sessions"] == pytest.approx(durations)
    assert m["mean_run_length_sessions"] == pytest.approx(np.mean(list(durations.values())))
    assert report.gates["switches_per_year"]["passed"] is (per_year <= 6.0)
    assert report.gates["expected_duration_sessions"]["value"] == pytest.approx(min(durations.values()))
    assert report.folds[0]["model_expected_durations_sessions"] == {"a": 4.0 * cadence, "b": 2.0 * cadence}
    assert m["label_stability"] == 1.0 and m["refits"] == 2


def test_invalid_cadence_is_an_error_report():
    inputs = _vol_inputs()
    inputs.cadence_sessions = 0
    report = V.walk_forward_validate(inputs, V.make_fit_fn("volatility", "short", CONFIG), CONFIG)
    assert report.status == "error" and not report.passed and report.reason.startswith("cadence_invalid:")
