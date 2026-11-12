"""K4: allowed bar_frequency set, §6.1 dataset table, minute-unit feature lag, provisioning guard."""

from __future__ import annotations

import pytest

from services.registry import factor_rule
from services.registry.strategy_artifact import (
    ALLOWED_BAR_FREQUENCIES,
    BAR_FREQUENCY_DATASETS,
    StrategyArtifactValidationError,
    dataset_for_bar_frequency,
    validate_execution_recipe,
)


def _recipe(freq):
    return {
        "kind": "close_to_close_momentum", "symbols": ["2330.TWSE"], "bar_frequency": freq,
        "data_source": "source-ingest:twse/daily-bars", "lookback_bars": 5, "momentum_threshold": 0.0,
        "positive_action": "BUY", "non_positive_action": "HOLD", "order_quantity": 1, "quantity_type": "SHARES",
    }


def test_table_matches_sd_6_1():
    assert dict(BAR_FREQUENCY_DATASETS) == {
        "1d": "tw_price_daily", "1m": "tw_price_1m", "5m": "tw_price_5m",
        "15m": "tw_price_15m", "30m": "tw_price_30m", "60m": "tw_price_60m",
    }
    assert ALLOWED_BAR_FREQUENCIES == ("1d", "1m", "5m", "15m", "30m", "60m")


@pytest.mark.parametrize("freq", ["1d", "1m", "5m", "15m", "30m", "60m"])
def test_allowed_frequencies_validate(freq):
    assert validate_execution_recipe(_recipe(freq))["bar_frequency"] == freq


@pytest.mark.parametrize("freq", ["2m", "10m", "1h", "daily", "d", "1D", "1day", "1w", "tick"])
def test_other_frequencies_rejected(freq):
    with pytest.raises(StrategyArtifactValidationError, match="not allowed"):
        validate_execution_recipe(_recipe(freq))
    with pytest.raises(StrategyArtifactValidationError):
        dataset_for_bar_frequency(freq)


def test_minute_datasets_are_governed_price_volume_with_minute_lag():
    for ds in ("tw_price_1m", "tw_price_5m", "tw_price_15m", "tw_price_30m", "tw_price_60m"):
        assert ds in factor_rule.PRICE_VOLUME_DATASETS
        d = factor_rule.get_feature_definition(ds, "close")
        assert d.availability_lag_unit == "minutes" and d.min_availability_lag == 0
        assert d.min_availability_lag_days == 0 and d.cross_session is False
    daily = factor_rule.get_feature_definition("tw_price_daily", "close")
    assert daily.availability_lag_unit == "days"
    rev = factor_rule.get_feature_definition("tw_monthly_revenue", "yoy")
    assert rev.availability_lag_unit == "days" and rev.min_availability_lag == 10 == rev.min_availability_lag_days


def test_feature_definition_lag_unit_validation():
    FD = factor_rule.FeatureDefinition
    assert FD("tw_price_1m", "close", availability_lag_unit="minutes", min_availability_lag=3).min_availability_lag == 3
    for kwargs in (
        {"availability_lag_unit": "hours"},
        {"availability_lag_unit": "minutes", "min_availability_lag_days": 1},
        {"min_availability_lag_days": 2, "min_availability_lag": 3},
        {"availability_lag_unit": "minutes", "min_availability_lag": -1},
    ):
        with pytest.raises(factor_rule.FactorRuleValidationError):
            FD("x", "y", **kwargs)


def _minute_logic(*, lag_minutes=None, window=2):
    item = {"name": "px", "dataset": "tw_price_1m", "field": "close", "availability_lag_days": 0}
    if lag_minutes is not None:
        item["availability_lag_minutes"] = lag_minutes
    return {
        "kind": "factor_rule_v1",
        "inputs": [item],
        "signal": {"op": "ts_mean", "args": [{"ref": "px"}, {"const_param": "w"}]},
        "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": "w"}]}, "short_when": None},
        "exit": {"max_holding_bars": None, "flat_when": {"op": "lt", "args": [{"ref": "signal"}, {"const_param": "w"}]}},
        "strength": {"op": "clip01", "args": [{"ref": "signal"}]},
    }


def _obs(event, value, avail=None):
    return {"value": value, "event_time": event, "available_time": avail or event}


@pytest.fixture
def _restore_catalogue():
    key = ("tw_price_1m", "close")
    original = factor_rule.get_feature_definition(*key)
    yield
    factor_rule.register_feature_definition(original)


def test_minute_lag_is_applied_in_minutes_by_validation_and_resolution(_restore_catalogue):
    factor_rule.register_feature_definition(
        factor_rule.FeatureDefinition(
            "tw_price_1m", "close", availability_lag_unit="minutes", min_availability_lag=3
        )
    )
    with pytest.raises(factor_rule.FactorRuleValidationError, match="below governed minimum 3 minutes"):
        factor_rule.validate_factor_rule_logic(_minute_logic(lag_minutes=2), {"w": 2})
    factor_rule.validate_factor_rule_logic(_minute_logic(lag_minutes=3), {"w": 2})
    series = [_obs("2026-11-10T09:01:00+08:00", 104.0)]
    early = "2026-11-10T09:02:00+08:00"
    with pytest.raises(factor_rule.FactorRuleValidationError, match="no visible observations"):
        factor_rule.resolve_point_in_time_inputs(_minute_logic(lag_minutes=3), None, features={"px": series}, decision_time=early)
    ok = factor_rule.resolve_point_in_time_inputs(
        _minute_logic(lag_minutes=3), None, features={"px": series}, decision_time="2026-11-10T09:04:00+08:00"
    )
    assert ok["px"] == (104.0,)  # exactly event + 3 minutes: visible, never earlier


@pytest.mark.parametrize("cross, expected", [(False, (200.0,)), (True, (100.0, 200.0))])
def test_cross_session_controls_window_reset(_restore_catalogue, cross, expected):
    factor_rule.register_feature_definition(
        factor_rule.FeatureDefinition("tw_price_1m", "close", availability_lag_unit="minutes", cross_session=cross)
    )
    series = [_obs("2026-11-09T13:30:00+08:00", 100.0), _obs("2026-11-10T09:00:00+08:00", 200.0)]
    env = factor_rule.resolve_point_in_time_inputs(
        _minute_logic(), None, features={"px": series}, decision_time="2026-11-10T09:05:00+08:00"
    )
    assert env["px"] == expected


def _art(lag=3):
    return {"strategy_logic": _minute_logic(lag_minutes=lag), "parameters": {"w": 2}}


DT = "2026-11-10T09:02:00+08:00"


def test_shared_evaluator_rejects_daily_close_fallback_for_minute_input(_restore_catalogue):
    factor_rule.register_feature_definition(
        factor_rule.FeatureDefinition("tw_price_1m", "close", availability_lag_unit="minutes", min_availability_lag=3)
    )
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(
            _art(), [100, 101], features={"tw_price_daily.close": [100, 101]}, decision_time=DT
        )
    generic = [_obs("2026-11-10T09:00:00+08:00", 100.0), _obs("2026-11-10T09:01:00+08:00", 101.0)]
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(_art(), generic, features={}, decision_time=DT)


@pytest.mark.parametrize(
    "series, decision_time",
    [
        ([100, 101], None),
        ([100, 101], DT),
        ([_obs("2026-11-10T09:00:00", 100.0), _obs("2026-11-10T09:01:00", 101.0)], DT),
        ([{"value": 100.0}, {"value": 101.0}], DT),
        ([_obs("2026-11-10T09:00:00+08:00", 100.0), _obs("2026-11-10T09:01:00+08:00", 101.0)], DT),
    ],
)
def test_shared_evaluator_rejects_raw_or_unstamped_minute_features(_restore_catalogue, series, decision_time):
    factor_rule.register_feature_definition(
        factor_rule.FeatureDefinition("tw_price_1m", "close", availability_lag_unit="minutes", min_availability_lag=3)
    )
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(
            _art(), None, features={"tw_price_1m.close": series}, decision_time=decision_time
        )


def _register_minute_close():
    factor_rule.register_feature_definition(
        factor_rule.FeatureDefinition("tw_price_1m", "close", availability_lag_unit="minutes", min_availability_lag=3)
    )


def _tagged(dataset, field="close"):
    return [
        {**_obs("2026-11-10T09:00:00+08:00", 100.0, "2026-11-10T09:00:00+08:00"), "dataset": dataset, "field": field},
        {**_obs("2026-11-10T09:01:00+08:00", 101.0, "2026-11-10T09:01:00+08:00"), "dataset": dataset, "field": field},
    ]


def test_public_evaluator_rejects_generic_or_daily_timestamped_minute_input(_restore_catalogue):
    _register_minute_close()
    late = "2026-11-10T16:05:00+08:00"
    # bare generic ``close`` key must not satisfy a declared tw_price_1m.close input
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(_art(), None, features={"close": _tagged("tw_price_daily")}, decision_time=late)
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(_art(), None, features={"close": _tagged("tw_price_1m")}, decision_time=late)
    # conflicting dataset metadata under the right key is rejected
    with pytest.raises(factor_rule.FactorRuleValidationError, match="cross-dataset"):
        factor_rule.evaluate_factor_rule_view(
            _art(), None, features={"px": _tagged("tw_price_daily")}, decision_time=late
        )
    # minute vwap must not be served by a generic vwap key or daily metadata
    vwap_logic = _minute_logic(lag_minutes=3)
    vwap_logic["inputs"][0].update(field="vwap")
    art = {"strategy_logic": vwap_logic, "parameters": {"w": 2}}
    factor_rule.register_feature_definition(
        factor_rule.FeatureDefinition("tw_price_1m", "vwap", availability_lag_unit="minutes", min_availability_lag=3)
    )
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(art, None, features={"vwap": _tagged("tw_price_daily", "vwap")}, decision_time=late)
    with pytest.raises(factor_rule.FactorRuleValidationError, match="cross-dataset"):
        factor_rule.evaluate_factor_rule_view(art, None, features={"px": _tagged("tw_price_daily", "vwap")}, decision_time=late)


@pytest.mark.parametrize("bad", ["2026-11-10T09:02:00", "2026-11-10", __import__("datetime").datetime(2026, 11, 10, 9, 2), None])
def test_minute_decision_time_must_be_timezone_aware(_restore_catalogue, bad):
    _register_minute_close()
    series = [
        _obs("2026-11-10T09:00:00+08:00", 100.0, "2026-11-10T16:00:00+08:00"),
        _obs("2026-11-10T09:01:00+08:00", 101.0, "2026-11-10T16:00:00+08:00"),
    ]
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(_art(), None, features={"px": series}, decision_time=bad)
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.resolve_point_in_time_inputs(_minute_logic(lag_minutes=3), None, features={"px": series}, decision_time=bad)
    with pytest.raises(factor_rule.FactorRuleValidationError, match="no visible"):
        factor_rule.evaluate_factor_rule_view(
            _art(), None, features={"px": series}, decision_time="2026-11-10T09:02:00+08:00"
        )


def _px_obs():
    return [
        _obs("2026-11-10T09:00:00+08:00", 100.0, "2026-11-10T09:00:00+08:00"),
        _obs("2026-11-10T09:01:00+08:00", 101.0, "2026-11-10T09:01:00+08:00"),
    ]


@pytest.mark.parametrize("field", ["close", "vwap", "open", "high", "low", "volume"])
@pytest.mark.parametrize("slot", ["signal", "long_when", "short_when", "flat_when", "strength"])
def test_minute_strategy_rejects_implicit_builtin_price_refs(_restore_catalogue, field, slot):
    _register_minute_close()
    logic = _minute_logic(lag_minutes=3)
    ref = {"op": "ts_mean", "args": [{"ref": field}, {"const_param": "w"}]}
    gt = {"op": "gt", "args": [ref, {"const_param": "w"}]}
    if slot == "signal":
        logic["signal"] = ref
    elif slot == "long_when":
        logic["entry"]["long_when"] = gt
    elif slot == "short_when":
        logic["entry"]["short_when"] = gt
    elif slot == "flat_when":
        logic["exit"]["flat_when"] = gt
    else:
        logic["strength"] = {"op": "clip01", "args": [ref]}
    with pytest.raises(factor_rule.FactorRuleValidationError, match="undeclared"):
        factor_rule.validate_factor_rule_logic(logic, {"w": 2})
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.evaluate_factor_rule_view(
            {"strategy_logic": logic, "parameters": {"w": 2}},
            [900.0, 1000.0],
            features={"px": _px_obs()},
            decision_time="2026-11-10T09:03:00+08:00",
        )


def test_minute_env_never_exposes_generic_closes(_restore_catalogue):
    _register_minute_close()
    env = factor_rule.resolve_point_in_time_inputs(
        _minute_logic(lag_minutes=3), [900.0, 1000.0], features={"px": _px_obs()},
        decision_time="2026-11-10T09:04:00+08:00",
    )
    assert set(env) == {"px"}


def test_full_artifact_with_minute_lag_validates_and_registers(_restore_catalogue):
    import copy

    from services.registry.strategy_artifact import (
        build_strategy_artifact_registry_payload,
        validate_strategy_artifact,
    )
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    _register_minute_close()
    art = copy.deepcopy(_sample_factor_rule_artifact())
    logic = art["strategy_logic"]
    logic["inputs"][0].update(dataset="tw_price_1m", field="close", availability_lag_days=0)
    for lag in (3, 5):
        logic["inputs"][0]["availability_lag_minutes"] = lag
        validate_strategy_artifact(art)
        build_strategy_artifact_registry_payload(
            {"registry_id": art["artifact_id"], "artifact_state": "candidate", "strategy_artifact": art}
        )
    logic["inputs"][0]["availability_lag_minutes"] = 2  # below governed minimum
    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(art)
    logic["inputs"][0]["availability_lag_minutes"] = -1  # schema minimum
    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(art)


# --- declared bar_frequency enforced at the full-artifact boundary -----------
def _declared_art(freq, *, inputs, slot="signal", ref="close"):
    import copy

    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    art = copy.deepcopy(_sample_factor_rule_artifact())
    art["parameters"]["bar_frequency"] = freq
    logic = art["strategy_logic"]
    logic["inputs"] = inputs
    node = {"op": "ts_mean", "args": [{"ref": ref}, {"const_param": "z_window"}]}
    gt = {"op": "gt", "args": [node, {"const_param": "entry_z"}]}
    if slot == "signal":
        logic["signal"] = node
    elif slot == "entry":
        logic["entry"]["long_when"] = gt
    elif slot == "exit":
        logic["exit"]["flat_when"] = gt
    elif slot == "strength":
        logic["strength"] = {"op": "clip01", "args": [node]}
    return art


def _min_input(freq, name="px"):
    from services.registry.strategy_artifact import BAR_FREQUENCY_DATASETS

    return {"name": name, "dataset": BAR_FREQUENCY_DATASETS[freq], "field": "close", "availability_lag_days": 0}


@pytest.mark.parametrize("freq", ["1m", "5m", "15m", "30m", "60m"])
@pytest.mark.parametrize("inputs", ["empty", "daily"])
@pytest.mark.parametrize("slot", ["signal", "entry", "exit", "strength"])
def test_declared_minute_frequency_rejects_missing_minute_inputs(freq, inputs, slot):
    from services.registry.strategy_artifact import evaluate_strategy_view, validate_strategy_artifact

    ins = [] if inputs == "empty" else [
        {"name": "px", "dataset": "tw_price_daily", "field": "close", "availability_lag_days": 1}
    ]
    ref = "close" if inputs == "empty" else "px"
    art = _declared_art(freq, inputs=ins, slot=slot, ref=ref)
    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(art)
    with pytest.raises(StrategyArtifactValidationError):
        evaluate_strategy_view(art, [100.0, 100.5, 100.1, 120.0])


@pytest.mark.parametrize("freq", ["1m", "5m"])
def test_declared_minute_frequency_rejects_mismatched_dataset_and_implicit_refs(freq):
    from services.registry.strategy_artifact import validate_strategy_artifact

    other = "5m" if freq == "1m" else "1m"
    with pytest.raises(StrategyArtifactValidationError, match="bar_frequency"):
        validate_strategy_artifact(_declared_art(freq, inputs=[_min_input(other)], ref="px"))
    with pytest.raises(StrategyArtifactValidationError, match="contradicts"):
        validate_strategy_artifact(
            _declared_art(freq, inputs=[_min_input(freq), _min_input(other, "py")], ref="px")
        )
    for slot in ("signal", "entry", "exit", "strength"):
        with pytest.raises(StrategyArtifactValidationError, match="undeclared"):
            validate_strategy_artifact(
                _declared_art(freq, inputs=[_min_input(freq)], slot=slot, ref="close")
            )


@pytest.mark.parametrize("bad", ["2m", "1h", "", None, 1, "1M"])
def test_unknown_declared_frequency_fails_closed(bad):
    from services.registry.strategy_artifact import validate_strategy_artifact

    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(_declared_art(bad, inputs=[]))


def test_resolver_with_declared_minute_frequency_never_exposes_generic_closes(_restore_catalogue):
    _register_minute_close()
    logic = _minute_logic(lag_minutes=3)
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.resolve_point_in_time_inputs(
            {**logic, "inputs": []}, [900.0, 1000.0], decision_time=DT, bar_frequency="1m"
        )
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.resolve_point_in_time_inputs(
            logic, [900.0], features={"px": _px_obs()}, decision_time=DT, bar_frequency="5m"
        )
    env = factor_rule.resolve_point_in_time_inputs(
        logic, [900.0, 1000.0], features={"px": _px_obs()},
        decision_time="2026-11-10T09:04:00+08:00", bar_frequency="1m",
    )
    assert set(env) == {"px"}


def test_declared_minute_frequency_positive_and_daily_regression(_restore_catalogue):
    from services.registry.strategy_artifact import evaluate_strategy_view, validate_strategy_artifact

    _register_minute_close()
    art = _declared_art("1m", inputs=[{**_min_input("1m"), "availability_lag_minutes": 3}], ref="px")
    validate_strategy_artifact(art)
    # daily closes are never consumed by a minute strategy: missing minute obs fail closed
    with pytest.raises(StrategyArtifactValidationError):
        evaluate_strategy_view(art, [100.0, 100.5, 100.1, 120.0], decision_time="2026-11-10T09:10:00+08:00")
    # daily regression: unchanged behaviour, generic closes still accepted
    daily = _declared_art("1d", inputs=[])
    validate_strategy_artifact(daily)
    assert evaluate_strategy_view(daily, [100.0, 100.5, 100.1, 120.0, 121.0])["action"] in {"BUY", "SELL", "HOLD"}
    assert factor_rule.INTRADAY_FREQUENCY_DATASETS == {
        k: v for k, v in __import__("services.registry.strategy_artifact", fromlist=["x"]).BAR_FREQUENCY_DATASETS.items() if k != "1d"
    }


# --- review P1: placeholder minute input must not admit daily prices; momentum fails closed
def test_placeholder_minute_input_cannot_admit_daily_price_alias(_restore_catalogue):
    import copy

    from services.registry.strategy_artifact import evaluate_strategy_view, validate_strategy_artifact
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    _register_minute_close()
    art = copy.deepcopy(_sample_factor_rule_artifact())
    art["parameters"]["bar_frequency"] = "1m"
    art["strategy_logic"]["inputs"] = [
        {"name": "minute_guard", "dataset": "tw_price_1m", "field": "close", "availability_lag_days": 0},
        {"name": "rev_yoy", "dataset": "tw_price_daily", "field": "close", "availability_lag_days": 0},
    ]
    feats = {
        "minute_guard": [{"value": 10, "event_time": "2026-11-10T09:01:00+08:00",
                          "available_time": "2026-11-10T09:01:00+08:00"}],
        "rev_yoy": [100, 100.5, 100.1, 120],
    }
    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(art)
    with pytest.raises(StrategyArtifactValidationError):
        evaluate_strategy_view(art, None, features=feats, decision_time="2026-11-10T09:05:00+08:00")


@pytest.mark.parametrize("freq", ["1m", "5m", "15m", "30m", "60m"])
def test_close_to_close_momentum_minute_fails_closed(freq):
    import copy

    from services.registry.strategy_artifact import (
        evaluate_strategy_action,
        evaluate_strategy_view,
        validate_strategy_artifact,
    )
    from services.registry.test_strategy_artifact import _artifact

    art = copy.deepcopy(_artifact())
    art["parameters"]["bar_frequency"] = freq
    closes = [100.0] * 30 + [120.0]
    with pytest.raises(StrategyArtifactValidationError, match="fail closed"):
        validate_strategy_artifact(art)
    with pytest.raises(StrategyArtifactValidationError):
        evaluate_strategy_view(art, closes)
    with pytest.raises(StrategyArtifactValidationError):
        evaluate_strategy_action(art, closes)


@pytest.mark.parametrize("bad", ["2m", "1h", "daily", "1D", None, 5])
def test_unknown_frequency_rejected_for_momentum_artifact(bad):
    import copy

    from services.registry.strategy_artifact import validate_strategy_artifact
    from services.registry.test_strategy_artifact import _artifact

    art = copy.deepcopy(_artifact())
    art["parameters"]["bar_frequency"] = bad
    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(art)


def test_close_to_close_momentum_daily_unchanged():
    import copy

    from services.registry.strategy_artifact import evaluate_strategy_view
    from services.registry.test_strategy_artifact import _artifact

    art = copy.deepcopy(_artifact())
    base = evaluate_strategy_view(art, [100.0] * 30 + [120.0])
    art["parameters"]["bar_frequency"] = "1d"
    assert evaluate_strategy_view(art, [100.0] * 30 + [120.0]) == base
