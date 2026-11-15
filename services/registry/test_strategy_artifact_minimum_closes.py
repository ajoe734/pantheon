import copy

import pytest

from services.registry.portfolio_envelope import build_portfolio_envelope
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    StrategyArtifactValidationError,
    load_strategy_artifact_registration,
    strategy_artifact_minimum_closes,
)


def _momentum():
    art = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])["strategy_artifact"]
    art["parameters"]["symbols"] = ["2330.TW"]
    return art


def _envelope(members):
    return build_portfolio_envelope(
        artifact_id="env-1", strategy_id="env-s", version="1.0.0", members=members)


def test_momentum_uses_lookback_parameter():
    art = _momentum()
    assert strategy_artifact_minimum_closes(art) == art["parameters"][art["strategy_logic"]["lookback_parameter"]]


def test_singleton_envelope_equals_member():
    art = _momentum()
    env = _envelope([{"member_id": "m1", "artifact": art, "risk_weight": 1}])
    assert strategy_artifact_minimum_closes(env) == strategy_artifact_minimum_closes(art)


def test_envelope_takes_member_maximum():
    a, b = _momentum(), copy.deepcopy(_momentum())
    b.update(artifact_id="other", strategy_id="other-s")
    key = b["strategy_logic"]["lookback_parameter"]
    b["parameters"][key] = a["parameters"][key] + 7
    for control in b["mutation_surface"]["controls"]:
        if control["parameter_key"] == key:
            control["current_value"] = b["parameters"][key]
    env = _envelope([{"member_id": "m1", "artifact": a, "risk_weight": 0.5},
                     {"member_id": "m2", "artifact": b, "risk_weight": 0.5}])
    assert strategy_artifact_minimum_closes(env) == b["parameters"][key]


def test_unknown_kind_fails_closed_with_named_error():
    art = _momentum()
    art["strategy_logic"] = {"kind": "mystery_v9"}
    with pytest.raises(StrategyArtifactValidationError, match="mystery_v9"):
        strategy_artifact_minimum_closes(art)


def test_factor_rule_uses_required_history_bars():
    from services.registry.factor_rule import required_history_bars
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    art = _sample_factor_rule_artifact()
    assert strategy_artifact_minimum_closes(art) == max(
        1, required_history_bars(art["strategy_logic"], art["parameters"]))
