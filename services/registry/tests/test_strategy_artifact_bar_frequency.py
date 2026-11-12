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
