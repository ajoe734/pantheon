"""Features are read point-in-time through the governed reader; missing input makes an axis unavailable."""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from services.research.regime import features as F
from services.research.tests.test_regime_research_route import DECISION_TIME, small_config, synthetic_observations

CONFIG = small_config()
OBS, _STATE, _RET = synthetic_observations(1500)


def _without(dataset, field=None):
    return [o for o in OBS if not (o["dataset"] == dataset and (field is None or o["field"] == field))]


@pytest.mark.parametrize("axis", ["volatility", "trend", "risk_appetite"])
@pytest.mark.parametrize("horizon", ["short", "medium"])
def test_axes_build_at_both_horizons(axis, horizon):
    inputs = F.BUILDERS[axis](OBS, decision_time=DECISION_TIME, horizon=horizon, config=CONFIG)
    assert inputs.available, inputs.reason
    assert inputs.frame is not None and not inputs.frame.isna().any().any()
    assert inputs.input_refs and all(r.startswith("feature://") for r in inputs.input_refs)


def test_inputs_are_listed_as_governed_dataset_refs():
    refs = F.build_risk_appetite_inputs(OBS, decision_time=DECISION_TIME, horizon="short", config=CONFIG).input_refs
    for dataset in ("tw_market_institutional_flows", "tw_market_margin_balances", "taifex_market_positions",
                    "taifex_market_put_call", "tw_market_breadth_daily", "tw_index_daily"):
        assert any(f"/{dataset}/" in r for r in refs), dataset


def test_read_goes_through_read_feature_series(monkeypatch):
    calls = []
    real = F.read_feature_series

    def spy(observations, dataset, field, **kw):
        calls.append((dataset, field))
        return real(observations, dataset, field, **kw)

    monkeypatch.setattr(F, "read_feature_series", spy)
    F.build_trend_inputs(OBS, decision_time=DECISION_TIME, horizon="short", config=CONFIG)
    assert ("tw_index_daily", "close") in calls and ("tw_market_breadth_daily", "whole_market_advancers") in calls


def test_observation_not_yet_available_is_invisible():
    late = copy.deepcopy(OBS)
    for o in late:
        if o["dataset"] == "tw_index_daily":
            o["available_time"] = "2031-01-01T00:00:00Z"  # after the decision time
    inputs = F.build_volatility_inputs(late, decision_time=DECISION_TIME, horizon="short", config=CONFIG)
    assert not inputs.available and inputs.reason == "tw_index_daily.close_missing"


@pytest.mark.parametrize("axis,drop,reason_part", [
    ("volatility", ("tw_index_daily", "close"), "tw_index_daily.close_missing"),
    ("trend", ("tw_market_breadth_daily", None), "tw_market_breadth_daily.history_missing"),
    ("risk_appetite", ("taifex_market_put_call", None), "taifex_market_put_call.open_interest_ratio"),
    ("risk_appetite", ("tw_market_margin_balances", None), "tw_market_margin_balances.margin_balance"),
])
def test_missing_input_makes_axis_unavailable_with_reason(axis, drop, reason_part):
    inputs = F.BUILDERS[axis](_without(*drop), decision_time=DECISION_TIME, horizon="short", config=CONFIG)
    assert not inputs.available and inputs.frame is None
    assert reason_part in inputs.reason


def test_short_history_is_unavailable_not_padded():
    inputs = F.build_volatility_inputs(OBS, decision_time=DECISION_TIME, horizon="short",
                                       config=small_config(min_input_sessions=2000))
    assert not inputs.available and inputs.reason == "tw_index_daily.close_history_short:1500<2000"


def test_non_finite_value_fails_the_input_closed():
    bad = copy.deepcopy(OBS)
    next(o for o in bad if o["dataset"] == "tw_index_daily")["value"] = float("nan")
    inputs = F.build_volatility_inputs(bad, decision_time=DECISION_TIME, horizon="short", config=CONFIG)
    assert not inputs.available


def test_features_are_causal():
    """The feature row at session t is unchanged when later observations are removed."""
    full = F.build_trend_inputs(OBS, decision_time=DECISION_TIME, horizon="medium", config=CONFIG).frame
    cut_day = full.index[900].date().isoformat()
    truncated_obs = [o for o in OBS if o["event_time"] <= cut_day]
    part = F.build_trend_inputs(truncated_obs, decision_time=DECISION_TIME, horizon="medium",
                                config=small_config(min_training_years=2.0)).frame
    pd.testing.assert_frame_equal(full.loc[:part.index[-1]], part, check_freq=False)


def test_medium_volatility_uses_non_overlapping_window_returns():
    short = F.build_volatility_inputs(OBS, decision_time=DECISION_TIME, horizon="short", config=CONFIG).frame
    medium = F.build_volatility_inputs(OBS, decision_time=DECISION_TIME, horizon="medium", config=CONFIG).frame
    assert len(short) == 1499 and len(medium) == 1499 // 5


def test_macro_rules_use_monitoring_score_and_leading_index_only():
    c = F.RegimeConfig()
    assert F.macro_state(30, 1.0, c) == "expansion"
    assert F.macro_state(30, -1.0, c) == "slowdown"
    assert F.macro_state(20, 0.0, c) == "slowdown"
    assert F.macro_state(12, 5.0, c) == "contraction"
    sessions, inputs = F.build_macro_inputs(OBS, decision_time=DECISION_TIME, config=c)
    assert inputs.available and {s.state for s in sessions} <= {"expansion", "slowdown", "contraction"}
    _, missing = F.build_macro_inputs(_without("tw_macro_ndc", "leading_index"), decision_time=DECISION_TIME, config=c)
    assert not missing.available and missing.reason == "tw_macro_ndc.history_missing"


def test_defaults_are_the_design_values_and_proposals_are_listed():
    c = F.RegimeConfig()
    assert dict(c.half_life_sessions) == {"short": 10, "medium": 60}
    assert c.min_training_years == 10.0 and c.refit_sessions == 122 and c.signal_delay_sessions == 1
    assert c.trend_states == 3 and c.risk_appetite_states == 2
    assert any("max_switches_per_year" in p for p in F.PROPOSALS)
    assert c.to_dict()["half_life_sessions"] == {"short": 10, "medium": 60}
