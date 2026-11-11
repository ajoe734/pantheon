"""Unit tests for services.registry.factor_rule (factor_rule_v1 grammar and evaluator)."""

from __future__ import annotations

import copy
import math
from datetime import date, timedelta
from typing import Any

import pytest

from services.registry.factor_rule import (
    CROSS_SECTIONAL_OPERATORS,
    DESIGN_9_2_OPERATORS,
    FactorRuleValidationError,
    FeatureDefinition,
    PLAN_3_4_TIME_SERIES_OPERATORS,
    TIME_SERIES_OPERATORS,
    evaluate_factor_expression,
    evaluate_factor_rule_action,
    evaluate_factor_rule_view,
    get_feature_definition,
    list_feature_definitions,
    parse_factor_expression,
    register_feature_definition,
    required_history_bars,
    resolve_point_in_time_inputs,
    validate_factor_rule_logic,
)
from services.registry.strategy_artifact import (
    StrategyArtifactValidationError,
    evaluate_strategy_action,
    evaluate_strategy_view,
    mutate_strategy_artifact,
    validate_strategy_artifact,
)


def _sample_factor_rule_artifact() -> dict[str, Any]:
    return {
        "artifact_schema_version": "1.0",
        "artifact_id": "strat-tw-rev-yoy-factor-v1-001",
        "strategy_id": "tw_rev_yoy_factor_v1",
        "version": "1.0.0",
        "algorithm_ref": {
            "engine": "lean",
            "repository": "ajoe734/pantheon-lean",
            "commit": "5ad0249432459c119f26718007e083808ef7995d",
            "path": "Algorithm.Python/pantheon_algo/base.py",
            "entrypoint": "pantheon_algo.base:PantheonAlgoBase",
            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
            "signal_schema_version": "1.0",
            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
        },
        "strategy_logic": {
            "kind": "factor_rule_v1",
            "inputs": [
                {
                    "name": "rev_yoy",
                    "dataset": "tw_monthly_revenue",
                    "field": "yoy",
                    "availability_lag_days": 10,
                }
            ],
            "signal": {
                "op": "ts_zscore",
                "args": [{"ref": "rev_yoy"}, {"const_param": "z_window"}],
            },
            "entry": {
                "long_when": {
                    "op": "gt",
                    "args": [{"ref": "signal"}, {"const_param": "entry_z"}],
                },
                "short_when": None,
            },
            "exit": {
                "max_holding_bars": {"const_param": "hold_bars"},
                "flat_when": {
                    "op": "lt",
                    "args": [{"ref": "signal"}, {"const_param": "exit_z"}],
                },
            },
            "strength": {
                "op": "clip01",
                "args": [
                    {
                        "op": "div",
                        "args": [
                            {"op": "abs", "args": [{"ref": "signal"}]},
                            {"const_param": "strength_scale"},
                        ],
                    }
                ],
            },
        },
        "parameters": {
            "market": "TW",
            "symbols": ["2330.TW", "2317.TW"],
            "bar_frequency": "1d",
            "data_source": "twse_openapi",
            "order_quantity": 1000,
            "quantity_type": "SHARES",
            "z_window": 4,
            "entry_z": 0.8,
            "exit_z": -0.5,
            "hold_bars": 3,
            "strength_scale": 2.0,
        },
        "mutation_surface": {
            "controls": [
                {
                    "parameter_key": "z_window",
                    "value_type": "integer",
                    "current_value": 4,
                    "allowed_range": {"min": 2, "max": 24},
                    "step": 1,
                },
                {
                    "parameter_key": "entry_z",
                    "value_type": "number",
                    "current_value": 0.8,
                    "allowed_range": {"min": 0.2, "max": 3.0},
                    "step": 0.1,
                },
                {
                    "parameter_key": "exit_z",
                    "value_type": "number",
                    "current_value": -0.5,
                    "allowed_range": {"min": -2.5, "max": 0.5},
                    "step": 0.1,
                },
                {
                    "parameter_key": "hold_bars",
                    "value_type": "integer",
                    "current_value": 3,
                    "allowed_range": {"min": 1, "max": 20},
                    "step": 1,
                },
                {
                    "parameter_key": "strength_scale",
                    "value_type": "number",
                    "current_value": 2.0,
                    "allowed_range": {"min": 0.5, "max": 5.0},
                    "step": 0.5,
                },
            ],
            "immutable_parameters": [
                "market",
                "symbols",
                "bar_frequency",
                "data_source",
                "order_quantity",
                "quantity_type",
            ],
        },
        "lineage": {
            "source_run_ids": ["run-factor-seed-001"],
            "source_dataset_refs": ["tw_monthly_revenue:2026-11"],
            "source_strategy_spec_id": "spec-tw-rev-yoy-v1",
        },
        "provenance_refs": ["evidence:tw-rev-yoy-001"],
    }


def test_operator_sets_match_design_9_2_and_plan_3_4() -> None:
    expected_design_9_2 = {
        "lag",
        "delta",
        "pct_change",
        "ts_mean",
        "ts_std",
        "ts_zscore",
        "ts_rank",
        "ts_min",
        "ts_max",
        "ratio",
        "add",
        "sub",
        "mul",
        "div",
        "abs",
        "sign",
        "clip01",
        "gt",
        "lt",
        "and",
        "or",
        "not",
    }
    expected_plan_3_4 = {
        "ts_sum",
        "ts_corr",
        "ts_cov",
        "ts_slope",
        "ts_rsquare",
        "ts_resi",
        "ts_quantile",
        "ts_argmax",
        "ts_argmin",
        "ts_decay_linear",
        "log",
        "signed_power",
        "max2",
        "min2",
    }
    expected_cross_sectional = {"cs_rank", "cs_scale", "industry_neutralize"}

    assert DESIGN_9_2_OPERATORS == expected_design_9_2
    assert PLAN_3_4_TIME_SERIES_OPERATORS == expected_plan_3_4
    assert TIME_SERIES_OPERATORS == expected_design_9_2 | expected_plan_3_4
    assert len(TIME_SERIES_OPERATORS) == 36
    assert CROSS_SECTIONAL_OPERATORS == expected_cross_sectional


def test_every_design_9_2_and_plan_3_4_operator_evaluates_correctly() -> None:
    env = {
        "x": [10.0, 12.0, 15.0, 14.0, 20.0],
        "y": [2.0, 3.0, 5.0, 4.0, 8.0],
    }
    params = {
        "k1": 1,
        "k2": 2,
        "d3": 3,
        "d4": 4,
        "d5": 5,
        "c2": 2.0,
        "c_neg": -3.5,
        "c05": 0.5,
        "c0": 0.0,
        "c1": 1.0,
        "q50": 0.5,
    }

    # lag, delta, pct_change
    assert evaluate_factor_expression(
        {"op": "lag", "args": [{"ref": "x"}, {"const_param": "k2"}]}, env, params
    ) == pytest.approx(15.0)
    assert evaluate_factor_expression(
        {"op": "delta", "args": [{"ref": "x"}, {"const_param": "k2"}]}, env, params
    ) == pytest.approx(5.0)
    assert evaluate_factor_expression(
        {"op": "pct_change", "args": [{"ref": "x"}, {"const_param": "k1"}]}, env, params
    ) == pytest.approx((20.0 - 14.0) / 14.0)

    # ts_mean, ts_std, ts_zscore, ts_rank, ts_min, ts_max, ts_sum
    w4 = [12.0, 15.0, 14.0, 20.0]
    m4 = sum(w4) / 4.0
    s4 = math.sqrt(sum((v - m4) ** 2 for v in w4) / 4.0)
    assert evaluate_factor_expression(
        {"op": "ts_mean", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(m4)
    assert evaluate_factor_expression(
        {"op": "ts_std", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(s4)
    assert evaluate_factor_expression(
        {"op": "ts_zscore", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx((20.0 - m4) / s4)
    assert evaluate_factor_expression(
        {"op": "ts_rank", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(1.0)
    assert evaluate_factor_expression(
        {"op": "ts_min", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(12.0)
    assert evaluate_factor_expression(
        {"op": "ts_max", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(20.0)
    assert evaluate_factor_expression(
        {"op": "ts_sum", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(sum(w4))

    # ts_corr, ts_cov
    wy4 = [3.0, 5.0, 4.0, 8.0]
    my4 = sum(wy4) / 4.0
    sy4 = math.sqrt(sum((v - my4) ** 2 for v in wy4) / 4.0)
    cov4 = sum((w4[i] - m4) * (wy4[i] - my4) for i in range(4)) / 4.0
    assert evaluate_factor_expression(
        {"op": "ts_cov", "args": [{"ref": "x"}, {"ref": "y"}, {"const_param": "d4"}]},
        env,
        params,
    ) == pytest.approx(cov4)
    assert evaluate_factor_expression(
        {"op": "ts_corr", "args": [{"ref": "x"}, {"ref": "y"}, {"const_param": "d4"}]},
        env,
        params,
    ) == pytest.approx(cov4 / (s4 * sy4))

    # ts_slope, ts_rsquare, ts_resi on perfectly linear series [2, 4, 6, 8]
    lin_env = {"lin": [0.0, 2.0, 4.0, 6.0, 8.0]}
    assert evaluate_factor_expression(
        {"op": "ts_slope", "args": [{"ref": "lin"}, {"const_param": "d4"}]},
        lin_env,
        params,
    ) == pytest.approx(2.0)
    assert evaluate_factor_expression(
        {"op": "ts_rsquare", "args": [{"ref": "lin"}, {"const_param": "d4"}]},
        lin_env,
        params,
    ) == pytest.approx(1.0)
    assert evaluate_factor_expression(
        {"op": "ts_resi", "args": [{"ref": "lin"}, {"const_param": "d4"}]},
        lin_env,
        params,
    ) == pytest.approx(0.0, abs=1e-9)

    # ts_quantile, ts_argmax, ts_argmin, ts_decay_linear
    assert evaluate_factor_expression(
        {
            "op": "ts_quantile",
            "args": [{"ref": "x"}, {"const_param": "d4"}, {"const_param": "q50"}],
        },
        env,
        params,
    ) == pytest.approx(14.5)
    assert evaluate_factor_expression(
        {"op": "ts_argmax", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(3.0)
    assert evaluate_factor_expression(
        {"op": "ts_argmin", "args": [{"ref": "x"}, {"const_param": "d4"}]}, env, params
    ) == pytest.approx(0.0)
    expected_decay = (1 * 12.0 + 2 * 15.0 + 3 * 14.0 + 4 * 20.0) / 10.0
    assert evaluate_factor_expression(
        {"op": "ts_decay_linear", "args": [{"ref": "x"}, {"const_param": "d4"}]},
        env,
        params,
    ) == pytest.approx(expected_decay)

    # ratio, add, sub, mul, div, abs, sign, clip01, log, signed_power, max2, min2
    assert evaluate_factor_expression(
        {"op": "ratio", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(20.0 / 8.0)
    assert evaluate_factor_expression(
        {"op": "add", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(28.0)
    assert evaluate_factor_expression(
        {"op": "sub", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(12.0)
    assert evaluate_factor_expression(
        {"op": "mul", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(160.0)
    assert evaluate_factor_expression(
        {"op": "div", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(2.5)
    assert evaluate_factor_expression(
        {"op": "abs", "args": [{"const_param": "c_neg"}]}, env, params
    ) == pytest.approx(3.5)
    assert evaluate_factor_expression(
        {"op": "sign", "args": [{"const_param": "c_neg"}]}, env, params
    ) == pytest.approx(-1.0)
    assert evaluate_factor_expression(
        {"op": "clip01", "args": [{"const_param": "c2"}]}, env, params
    ) == pytest.approx(1.0)
    assert evaluate_factor_expression(
        {"op": "log", "args": [{"ref": "x"}]}, env, params
    ) == pytest.approx(math.log(20.0))
    assert evaluate_factor_expression(
        {
            "op": "signed_power",
            "args": [{"const_param": "c_neg"}, {"const_param": "c2"}],
        },
        env,
        params,
    ) == pytest.approx(-(3.5**2))
    assert evaluate_factor_expression(
        {"op": "max2", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(20.0)
    assert evaluate_factor_expression(
        {"op": "min2", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(8.0)

    # gt, lt, and, or, not
    assert evaluate_factor_expression(
        {"op": "gt", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(1.0)
    assert evaluate_factor_expression(
        {"op": "lt", "args": [{"ref": "x"}, {"ref": "y"}]}, env, params
    ) == pytest.approx(0.0)
    assert evaluate_factor_expression(
        {"op": "and", "args": [{"const_param": "c1"}, {"const_param": "c05"}]},
        env,
        params,
    ) == pytest.approx(1.0)
    assert evaluate_factor_expression(
        {"op": "or", "args": [{"const_param": "c0"}, {"const_param": "c05"}]},
        env,
        params,
    ) == pytest.approx(1.0)
    assert evaluate_factor_expression(
        {"op": "not", "args": [{"const_param": "c0"}]}, env, params
    ) == pytest.approx(1.0)


def test_unknown_and_cross_sectional_operators_rejected_by_evaluator() -> None:
    env = {"x": [1.0, 2.0, 3.0, 4.0]}
    params = {"w": 2}

    with pytest.raises(FactorRuleValidationError, match="unknown factor operator"):
        parse_factor_expression({"op": "ema_magic", "args": [{"ref": "x"}]})

    for cs_op in ("cs_rank", "cs_scale", "industry_neutralize"):
        # Parsed when allow_cross_sectional=True (e.g. for AlphaTemplate storage)
        parsed = parse_factor_expression(
            {"op": cs_op, "args": [{"ref": "x"}]},
            allow_cross_sectional=True,
        )
        assert parsed["op"] == cs_op

        # Rejected when allow_cross_sectional=False or evaluated per-symbol
        with pytest.raises(
            FactorRuleValidationError,
            match=f"cross-sectional operator '{cs_op}' is not allowed",
        ):
            parse_factor_expression({"op": cs_op, "args": [{"ref": "x"}]})

        with pytest.raises(
            FactorRuleValidationError,
            match=f"cross-sectional operator '{cs_op}' is not allowed",
        ):
            evaluate_factor_expression(
                {"op": cs_op, "args": [{"ref": "x"}]}, env, params
            )

        artifact = _sample_factor_rule_artifact()
        artifact["strategy_logic"]["signal"] = {"op": cs_op, "args": [{"ref": "rev_yoy"}]}
        with pytest.raises(
            StrategyArtifactValidationError,
            match=f"cross-sectional operator '{cs_op}' is not allowed",
        ):
            validate_strategy_artifact(artifact)


def test_point_in_time_inputs_hide_future_observations() -> None:
    artifact = _sample_factor_rule_artifact()
    closes = [500.0, 505.0, 510.0, 515.0, 520.0]

    base_observations = [
        {"event_time": "2026-01-31", "available_time": "2026-02-10T09:00:00Z", "value": 10.0},
        {"event_time": "2026-02-28", "available_time": "2026-03-10T09:00:00Z", "value": 12.0},
        {"event_time": "2026-03-31", "available_time": "2026-04-10T09:00:00Z", "value": 11.0},
        {"event_time": "2026-04-30", "available_time": "2026-05-10T09:00:00Z", "value": 18.0},
    ]
    # A future observation (May revenue available on 2026-06-10) with an extreme value
    # that would flip the signal from BUY to SELL if it were leaked early.
    with_future_outlier = [
        *base_observations,
        {"event_time": "2026-05-31", "available_time": "2026-06-10T09:00:00Z", "value": -500.0},
    ]

    decision_time_may = "2026-05-15T13:30:00Z"
    view_without_future = evaluate_strategy_view(
        artifact,
        closes,
        features={"rev_yoy": base_observations},
        decision_time=decision_time_may,
    )
    view_with_future = evaluate_strategy_view(
        artifact,
        closes,
        features={"rev_yoy": with_future_outlier},
        decision_time=decision_time_may,
    )

    assert view_without_future == view_with_future
    assert view_with_future["action"] == "BUY"
    assert view_with_future["direction"] == "LONG"

    # Once decision_time reaches 2026-06-10T09:00:00Z, the May observation becomes visible
    view_in_june = evaluate_strategy_view(
        artifact,
        closes,
        features={"rev_yoy": with_future_outlier},
        decision_time="2026-06-10T09:00:00Z",
    )
    assert view_in_june["action"] == "SELL"
    assert view_in_june["signal"] < -1.0


def test_availability_lag_days_enforced_even_when_available_time_is_earlier() -> None:
    artifact = _sample_factor_rule_artifact()
    closes = [100.0, 101.0, 102.0, 103.0]
    # 4th observation has event_time 2026-04-30; lag is 10 days -> effective available 2026-05-10
    obs = [
        {"event_time": "2026-01-31", "available_time": "2026-01-31", "value": 10.0},
        {"event_time": "2026-02-28", "available_time": "2026-02-28", "value": 12.0},
        {"event_time": "2026-03-31", "available_time": "2026-03-31", "value": 11.0},
        {"event_time": "2026-04-30", "available_time": "2026-05-02T00:00:00Z", "value": 20.0},
    ]
    # On 2026-05-05, only 3 observations are visible, which is less than z_window=4
    with pytest.raises(StrategyArtifactValidationError, match="requires sufficient"):
        evaluate_strategy_action(
            artifact,
            closes,
            features={"rev_yoy": obs},
            decision_time="2026-05-05T00:00:00Z",
        )

    # On 2026-05-10, the 4th observation becomes visible
    action = evaluate_strategy_action(
        artifact,
        closes,
        features={"rev_yoy": obs},
        decision_time="2026-05-10T00:00:00Z",
    )
    assert action == "BUY"


def test_numeric_constants_must_be_declared_mutable_controls_and_structure_is_immutable() -> None:
    artifact = _sample_factor_rule_artifact()
    validate_strategy_artifact(artifact)

    # 1. Bare numeric literal inside expression is rejected
    bad_literal = copy.deepcopy(artifact)
    bad_literal["strategy_logic"]["signal"] = {
        "op": "ts_zscore",
        "args": [{"ref": "rev_yoy"}, 4],
    }
    with pytest.raises(
        StrategyArtifactValidationError,
        match="numeric constants in factor_rule_v1 must be declared as",
    ):
        validate_strategy_artifact(bad_literal)

    # 2. const_param classified as immutable instead of mutable control is rejected
    bad_immutable = copy.deepcopy(artifact)
    bad_immutable["mutation_surface"]["controls"] = [
        c
        for c in bad_immutable["mutation_surface"]["controls"]
        if c["parameter_key"] != "entry_z"
    ]
    bad_immutable["mutation_surface"]["immutable_parameters"].append("entry_z")
    with pytest.raises(
        StrategyArtifactValidationError,
        match="must be a mutable control",
    ):
        validate_strategy_artifact(bad_immutable)

    # 3. Mutating a declared control succeeds and keeps strategy_logic structure identical
    child = mutate_strategy_artifact(
        artifact,
        new_artifact_id="strat-tw-rev-yoy-factor-v1-002",
        new_version="1.0.1",
        parameter_updates={"entry_z": 1.0, "z_window": 5},
        source_run_ids=["run-mutate-001"],
    )
    assert child["strategy_logic"] == artifact["strategy_logic"]
    assert child["parameters"]["entry_z"] == 1.0
    assert child["parameters"]["z_window"] == 5


def test_max_holding_bars_triggers_exit_after_hold_window() -> None:
    artifact = _sample_factor_rule_artifact()
    # hold_bars is 3; sequence of 7 observations where bar 3 (index 3) enters LONG,
    # bars 4 and 5 stay in the HOLD band (z-score between -0.5 and 0.8),
    # and bar 6 (3rd held bar) triggers max_holding_bars exit -> SELL.
    series = [10.0, 10.0, 10.0, 20.0, 15.0, 15.0, 15.0]
    closes = [100.0] * len(series)

    base = date(2026, 1, 1)
    observations = [
        {
            "event_time": (base + timedelta(days=30 * i)).isoformat(),
            "available_time": (base + timedelta(days=30 * i)).isoformat(),
            "value": v,
        }
        for i, v in enumerate(series)
    ]
    actions = [
        evaluate_factor_rule_action(
            artifact,
            closes[: i + 1],
            features={"rev_yoy": observations[: i + 1]},
            decision_time=(base + timedelta(days=30 * i + 15)).isoformat(),
        )
        for i in range(3, len(series))
    ]
    assert actions == ["BUY", "HOLD", "HOLD", "SELL"]


def test_vwap_derivation_and_feature_catalogue() -> None:
    register_feature_definition(
        FeatureDefinition(
            dataset="tw_custom_test_ds",
            field="score",
            min_availability_lag_days=2,
            description="Test custom feature",
        )
    )
    defn = get_feature_definition("tw_custom_test_ds", "score")
    assert defn is not None
    assert defn.min_availability_lag_days == 2
    assert any(d.dataset == "tw_monthly_revenue" for d in list_feature_definitions())

    logic = {
        "kind": "factor_rule_v1",
        "inputs": [],
        "signal": {"op": "ratio", "args": [{"ref": "close"}, {"ref": "vwap"}]},
        "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": "th"}]}, "short_when": None},
        "exit": {"max_holding_bars": None, "flat_when": {"op": "lt", "args": [{"ref": "signal"}, {"const_param": "th"}]}},
        "strength": {"op": "clip01", "args": [{"ref": "signal"}]},
    }
    env = resolve_point_in_time_inputs(
        logic,
        [100.0, 105.0],
        features={
            "TradeValue": [1000.0, 2000.0],
            "TradeVolume": [10.0, 20.0],
        },
    )
    assert env["vwap"] == (100.0, 100.0)
    assert required_history_bars(logic, {"th": 1.0}) == 1


def test_shared_evaluator_rejects_timestamp_free_catalogued_feature() -> None:
    artifact = _sample_factor_rule_artifact()
    with pytest.raises(FactorRuleValidationError, match="event_time and available_time"):
        evaluate_factor_rule_view(
            artifact,
            [100.0] * 4,
            features={"rev_yoy": [10.0, 12.0, 11.0, 19.0]},
            decision_time="2026-01-01",
        )


def test_shared_evaluator_rejects_uncatalogued_dataset() -> None:
    artifact = _sample_factor_rule_artifact()
    artifact["strategy_logic"]["inputs"][0]["dataset"] = "tw_not_in_catalogue"
    with pytest.raises(FactorRuleValidationError, match="governed feature catalogue"):
        evaluate_factor_rule_view(
            artifact,
            [100.0] * 4,
            features={"rev_yoy": [10.0, 12.0, 11.0, 19.0]},
            decision_time="2026-01-01",
        )
