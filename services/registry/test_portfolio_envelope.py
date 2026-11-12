import copy
import unittest

from services.registry.portfolio_envelope import build_portfolio_envelope, evaluate_portfolio_envelope
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS, StrategyArtifactValidationError,
    build_strategy_artifact_registry_payload, evaluate_strategy_action,
    evaluate_strategy_view, load_strategy_artifact_registration,
    strategy_artifact_checksum, validate_strategy_artifact,
)


def member_artifact(name="long", *, symbols=None, bearish=False):
    artifact = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])["strategy_artifact"]
    artifact["artifact_id"] = "artifact-" + name
    artifact["strategy_id"] = "strategy-" + name
    artifact["parameters"]["symbols"] = symbols or ["2330.TW"]
    if bearish:
        artifact["strategy_logic"]["positive_action"] = "SELL"
        artifact["strategy_logic"]["non_positive_action"] = "BUY"
        artifact["parameters"]["zero_momentum_action"] = "BUY"
    return artifact


def envelope(members=None, **limits):
    return build_portfolio_envelope(
        artifact_id="artifact-persona-portfolio", strategy_id="persona-portfolio", version="1.0.0",
        members=members or [{"member_id": "long", "artifact": member_artifact(), "risk_weight": 1}],
        **limits,
    )


class PortfolioEnvelopeTests(unittest.TestCase):
    def test_singleton_preserves_exact_view_and_actions(self):
        artifact = member_artifact()
        portfolio = envelope([{"member_id": "long", "artifact": artifact, "risk_weight": 1}])
        for closes in ([100, 105], [105, 100], [100, 100]):
            self.assertEqual(evaluate_strategy_view(portfolio, closes), evaluate_strategy_view(artifact, closes))
            self.assertEqual(evaluate_strategy_action(portfolio, closes), evaluate_strategy_action(artifact, closes))
        self.assertEqual(artifact, portfolio["strategy_logic"]["members"][0]["artifact"])

    def test_netting_clipping_and_virtual_only(self):
        long, short = member_artifact(), member_artifact("short", bearish=True)
        portfolio = envelope([
            {"member_id": "long", "artifact": long, "risk_weight": .5},
            {"member_id": "short", "artifact": short, "risk_weight": .5},
            {"member_id": "trial", "artifact": short, "risk_weight": 0, "virtual_only": True},
        ])
        result = evaluate_portfolio_envelope(portfolio, [100, 105])
        self.assertEqual(result["view"]["direction"], "FLAT")
        self.assertEqual(result["view"]["action"], "HOLD")
        self.assertEqual(len(result["member_views"]), 3)
        portfolio = envelope([
            {"member_id": "long", "artifact": long, "risk_weight": .8},
            {"member_id": "short", "artifact": short, "risk_weight": .2},
        ], max_net_strength=.4)
        self.assertEqual(evaluate_strategy_view(portfolio, [100, 105])["strength"], .4)

    def test_pin_validation_and_limits_fail_closed(self):
        original = envelope()
        mutations = [
            lambda e: e["strategy_logic"]["members"][0]["artifact"]["parameters"].update(order_quantity=99),
            lambda e: e["strategy_logic"]["members"][0].update(version="9.0.0"),
            lambda e: e["strategy_logic"]["members"][0].update(virtual_only=True),
            lambda e: e["strategy_logic"]["risk_limits"].update(gross_weight_limit=.5),
            lambda e: e["strategy_logic"]["risk_limits"].update(max_net_strength=float("nan")),
            lambda e: e["lineage"].update(parent_registry_ids=["unrelated"]),
            lambda e: e["mutation_surface"]["controls"].append(
                copy.deepcopy(e["strategy_logic"]["members"][0]["artifact"]["mutation_surface"]["controls"][0])),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                bad = copy.deepcopy(original)
                mutate(bad)
                with self.assertRaises(StrategyArtifactValidationError):
                    evaluate_strategy_view(bad, [100, 101])
        self.assertEqual(strategy_artifact_checksum(original), strategy_artifact_checksum(envelope()))

    def test_registry_approval_boundary_and_version_identity(self):
        portfolio = envelope()
        _, payload = build_strategy_artifact_registry_payload({"strategy_artifact": portfolio})
        self.assertEqual(payload.artifact_type.value, "execution_bundle")
        self.assertEqual(payload.artifact_state.value, "candidate")
        self.assertEqual(payload.checksum, strategy_artifact_checksum(portfolio))
        with self.assertRaises(StrategyArtifactValidationError):
            build_strategy_artifact_registry_payload({"strategy_artifact": portfolio, "artifact_state": "approved"})
        changed = copy.deepcopy(portfolio)
        changed["strategy_logic"]["members"][0]["risk_weight"] = .5
        self.assertNotEqual(strategy_artifact_checksum(changed), payload.checksum)

    def test_decimal_weights_cancel_without_a_residual_order(self):
        portfolio = envelope([
            {"member_id": "a", "artifact": member_artifact("a"), "risk_weight": .1},
            {"member_id": "b", "artifact": member_artifact("b"), "risk_weight": .2},
            {"member_id": "c", "artifact": member_artifact("c", bearish=True), "risk_weight": .3},
        ])
        self.assertEqual(evaluate_strategy_view(portfolio, [100, 101])["action"], "HOLD")

    def test_member_symbol_aliases_share_one_net_underlying(self):
        portfolio = envelope([
            {"member_id": "a", "artifact": member_artifact("a"), "risk_weight": .5},
            {"member_id": "b", "artifact": member_artifact("b", bearish=True, symbols=["2330.TWSE"]), "risk_weight": .5},
        ])
        self.assertEqual(len(portfolio["parameters"]["symbols"]), 1)
        self.assertEqual(evaluate_strategy_view(portfolio, [100, 101])["action"], "HOLD")

    def test_multi_underlying_selects_only_members_of_that_underlying(self):
        portfolio = envelope([
            {"member_id": "long", "artifact": member_artifact(), "risk_weight": .6},
            {"member_id": "short", "artifact": member_artifact("short", symbols=["2317.TW"], bearish=True), "risk_weight": .4},
        ])
        self.assertEqual(evaluate_strategy_view(portfolio, [100, 101], symbol="2330.TWSE")["strength"], .6)
        self.assertEqual(evaluate_strategy_view(portfolio, [100, 101], symbol="2317.TW")["direction"], "SHORT")
        with self.assertRaises(StrategyArtifactValidationError):
            evaluate_strategy_view(portfolio, [100, 101])

    def test_singleton_expression_strength_is_identical(self):
        from services.registry.test_strategy_artifact import _artifact_with_expression_policy

        artifact = _artifact_with_expression_policy()
        portfolio = envelope([{"member_id": "expression", "artifact": artifact, "risk_weight": 1}])
        for closes in ([100, 100.1], [100, 99.9], [100, 102]):
            self.assertEqual(evaluate_strategy_view(portfolio, closes, symbol="2330.TW"),
                             evaluate_strategy_view(artifact, closes))

    def test_factor_members_use_independent_history_and_fail_on_missing_features(self):
        from services.registry.test_factor_rule import _sample_factor_rule_artifact

        artifact = _sample_factor_rule_artifact()
        artifact["strategy_logic"]["inputs"][0].update(
            name="close", dataset="tw_price_daily", field="close", availability_lag_days=0)
        artifact["strategy_logic"]["signal"]["args"][0] = {"ref": "close"}
        portfolio = envelope([{"member_id": "factor", "artifact": artifact, "risk_weight": 1}])
        closes = [10, 10, 10, 20, 15, 15, 15]
        for index in range(4, len(closes) + 1):
            self.assertEqual(evaluate_strategy_view(portfolio, closes[:index], symbol="2330.TW"),
                             evaluate_strategy_view(artifact, closes[:index]))
        missing = _sample_factor_rule_artifact()
        portfolio = envelope([{"member_id": "fundamental", "artifact": missing, "risk_weight": 1}])
        with self.assertRaises(ValueError):
            evaluate_strategy_view(portfolio, closes, symbol="2330.TW", decision_time="2026-11-12")

