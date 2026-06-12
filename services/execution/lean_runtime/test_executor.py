import unittest

from services.execution.lean_runtime.executor import (
    BRACKET_ORDER_STATUS_LOGGED_ONLY,
    BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
    ExecutionError,
    _build_bracket_legs,
    execute,
)


class _Security:
    Price = 100.0


class _Holding:
    Quantity = 0.0


class _SpyAlgo:
    def __init__(self):
        self.Portfolio = {"AAPL": _Holding()}
        self.Securities = {"AAPL": _Security()}
        self.market_orders = []
        self.limit_orders = []
        self.stop_market_orders = []
        self.bracket_logs = []
        self.order_rejections = []
        self.signal_noops = []

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        self.market_orders.append((symbol, quantity))

    def LimitOrder(self, symbol, quantity, limit_price):  # noqa: N802
        self.limit_orders.append((symbol, quantity, limit_price))

    def StopMarketOrder(self, symbol, quantity, stop_price):  # noqa: N802
        self.stop_market_orders.append((symbol, quantity, stop_price))

    def RecordBracketOrderLogged(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        stop_loss_pct,
        take_profit_pct,
        broker_submission_status,
        submitted_to_broker,
        metadata=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        if metadata is not None:
            payload["metadata"] = metadata
        self.bracket_logs.append(payload)

    def RecordOrderRejected(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        reject_reason,
        requested_quantity,
        computed_quantity,
        quantity_type,
        order_type,
        broker_submission_status,
        submitted_to_broker,
        price=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "reject_reason": reject_reason,
            "requested_quantity": requested_quantity,
            "computed_quantity": computed_quantity,
            "quantity_type": quantity_type,
            "order_type": order_type,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        if price is not None:
            payload["price"] = price
        self.order_rejections.append(payload)

    def RecordSignalNoop(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        noop_reason,
        requested_quantity,
        quantity_type,
        order_type,
        broker_submission_status,
        submitted_to_broker,
        computed_quantity=None,
        price=None,
        metadata=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "noop_reason": noop_reason,
            "requested_quantity": requested_quantity,
            "computed_quantity": computed_quantity,
            "quantity_type": quantity_type,
            "order_type": order_type,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        if price is not None:
            payload["price"] = price
        if metadata is not None:
            payload["metadata"] = metadata
        self.signal_noops.append(payload)

    def SetHoldings(self, symbol, target_percent):  # noqa: N802
        raise AssertionError("SetHoldings should not be used by this signal")

    def Liquidate(self, symbol):  # noqa: N802
        raise AssertionError("Liquidate should not be used by this signal")


class _GuardedPaperAlgo(_SpyAlgo):
    DeploymentStage = "paper"
    BracketOrderExecutionEnabled = True

    def __init__(self):
        super().__init__()
        self.bracket_submissions = []

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        super().MarketOrder(symbol, quantity)
        self.Portfolio[symbol].Quantity += quantity

    def SubmitBracketOrder(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        legs,
        guard_stage,
        broker_submission_status,
        submitted_to_broker,
    ):
        submission = {
            "symbol": symbol,
            "signal_id": signal_id,
            "legs": legs,
            "guard_stage": guard_stage,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        self.bracket_submissions.append(submission)
        return {"submission_id": "bracket-001", "legs": legs}


class _GuardedPaperFallbackAlgo(_SpyAlgo):
    DeploymentStage = "paper"
    BracketOrderExecutionEnabled = True

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        super().MarketOrder(symbol, quantity)
        self.Portfolio[symbol].Quantity += quantity


class _MissingLimitPaperAlgo(_GuardedPaperFallbackAlgo):
    LimitOrder = None


class _LiveAlgo(_GuardedPaperAlgo):
    DeploymentStage = "live"


class _SimAlgo(_GuardedPaperAlgo):
    DeploymentStage = "sim"


class ExecutorBracketOrderTests(unittest.TestCase):
    def test_hold_signal_records_noop_feedback_without_order(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-hold-001",
                "symbol": "AAPL.US",
                "action": "HOLD",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["noop_reason"], "hold_signal")
        self.assertEqual(noop["requested_quantity"], 0.0)
        self.assertEqual(noop["quantity_type"], "SHARES")
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_noop")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["price"], 100.0)

    def test_exit_long_without_position_records_noop_feedback(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-exit-empty-long-001",
                "symbol": "AAPL.US",
                "action": "EXIT",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["noop_reason"], "exit_long_without_position")
        self.assertEqual(noop["requested_quantity"], 0.0)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["metadata"]["position_quantity"], 0.0)
        self.assertEqual(noop["metadata"]["exit_direction"], "LONG")
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_noop")
        self.assertFalse(noop["submitted_to_broker"])

    def test_zero_share_quantity_records_order_rejection(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-zero-shares",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 0.1,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.order_rejections), 1)
        rejection = algo.order_rejections[0]
        self.assertEqual(rejection["reject_reason"], "shares_quantity_rounded_to_zero")
        self.assertEqual(rejection["requested_quantity"], 0.1)
        self.assertEqual(rejection["computed_quantity"], 0.0)
        self.assertEqual(rejection["broker_submission_status"], "rejected_before_broker")
        self.assertFalse(rejection["submitted_to_broker"])

    def test_zero_cash_value_quantity_records_order_rejection(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-zero-cash",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10.0,
                "quantity_type": "CASH_VALUE",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(len(algo.order_rejections), 1)
        rejection = algo.order_rejections[0]
        self.assertEqual(rejection["reject_reason"], "cash_value_resolved_to_zero_shares")
        self.assertEqual(rejection["requested_quantity"], 10.0)
        self.assertEqual(rejection["computed_quantity"], 0.0)
        self.assertEqual(rejection["price"], 100.0)

    def test_limit_order_without_limit_price_raises_before_market_order(self):
        algo = _SpyAlgo()

        with self.assertRaisesRegex(ExecutionError, "limit_price is required"):
            execute(
                {
                    "signal_id": "sig-limit-missing-price",
                    "symbol": "AAPL.US",
                    "action": "BUY",
                    "direction": "LONG",
                    "quantity": 10,
                    "quantity_type": "SHARES",
                    "order_type": "LIMIT",
                },
                algo,
            )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])

    def test_bracket_order_is_logged_only_not_broker_submitted(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-bracket-001",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 10)])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(algo.stop_market_orders, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "unknown")

    def test_guarded_paper_bracket_order_submits_simulated_children(self):
        algo = _GuardedPaperAlgo()

        execute(
            {
                "signal_id": "sig-bracket-002",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 10)])
        self.assertEqual(algo.stop_market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.bracket_submissions), 1)
        legs = algo.bracket_submissions[0]["legs"]
        self.assertEqual(
            legs,
            [
                {
                    "leg_type": "stop_loss",
                    "order_type": "STOP_MARKET",
                    "quantity": -10.0,
                    "stop_price": 98.0,
                },
                {
                    "leg_type": "take_profit",
                    "order_type": "LIMIT",
                    "quantity": -10.0,
                    "limit_price": 105.0,
                },
            ],
        )
        self.assertEqual(len(algo.bracket_logs), 1)
        self.assertEqual(
            algo.bracket_logs[0]["broker_submission_status"],
            BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
        )
        self.assertTrue(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "paper")
        self.assertEqual(algo.bracket_logs[0]["metadata"]["legs"], legs)

    def test_guarded_paper_bracket_order_can_submit_with_lean_order_methods(self):
        algo = _GuardedPaperFallbackAlgo()

        execute(
            {
                "signal_id": "sig-bracket-fallback",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.stop_market_orders, [("AAPL", -10.0, 98.0)])
        self.assertEqual(algo.limit_orders, [("AAPL", -10.0, 105.0)])
        self.assertEqual(
            algo.bracket_logs[0]["broker_submission_status"],
            BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
        )
        self.assertTrue(algo.bracket_logs[0]["submitted_to_broker"])

    def test_paper_bracket_order_guard_disabled_remains_logged_only(self):
        algo = _GuardedPaperAlgo()
        algo.BracketOrderExecutionEnabled = False

        execute(
            {
                "signal_id": "sig-bracket-disabled",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])

    def test_missing_child_order_method_logs_only_without_partial_submission(self):
        algo = _MissingLimitPaperAlgo()

        execute(
            {
                "signal_id": "sig-bracket-missing-method",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.stop_market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["reason"], "bracket_submission_unavailable")

    def test_live_bracket_order_remains_logged_only_even_if_guard_flag_is_set(self):
        algo = _LiveAlgo()

        execute(
            {
                "signal_id": "sig-bracket-live",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 10)])
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "live")

    def test_guarded_sim_short_bracket_order_submits_inverse_child_legs(self):
        algo = _SimAlgo()

        execute(
            {
                "signal_id": "sig-bracket-sim-short",
                "symbol": "AAPL.US",
                "action": "SELL",
                "direction": "SHORT",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        legs = algo.bracket_submissions[0]["legs"]
        self.assertEqual(
            legs,
            [
                {
                    "leg_type": "stop_loss",
                    "order_type": "STOP_MARKET",
                    "quantity": 10.0,
                    "stop_price": 102.0,
                },
                {
                    "leg_type": "take_profit",
                    "order_type": "LIMIT",
                    "quantity": 10.0,
                    "limit_price": 95.0,
                },
            ],
        )
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER)
        self.assertTrue(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "sim")


class _CanaryAlgo(_GuardedPaperAlgo):
    DeploymentStage = "canary"


class _PaperNoFillAlgo(_GuardedPaperAlgo):
    """Paper algo where MarketOrder records the call but does NOT update portfolio holdings."""

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        _SpyAlgo.MarketOrder(self, symbol, quantity)


class _PaperWithLongAlgo(_GuardedPaperAlgo):
    """Paper algo that starts with an existing LONG position and tracks liquidations."""

    def __init__(self):
        super().__init__()
        self.liquidations: list = []
        holding = _Holding()
        holding.Quantity = 10.0
        self.Portfolio["AAPL"] = holding

    def Liquidate(self, symbol):  # noqa: N802
        self.liquidations.append(symbol)
        self.Portfolio[symbol].Quantity = 0.0


class BracketLegBuildTests(unittest.TestCase):
    """Direct unit tests for _build_bracket_legs deterministic price computation."""

    def test_buy_long_stop_and_take_profit_prices_are_deterministic(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(len(legs), 2)
        stop = next(l for l in legs if l["leg_type"] == "stop_loss")
        tp = next(l for l in legs if l["leg_type"] == "take_profit")
        self.assertEqual(stop["order_type"], "STOP_MARKET")
        self.assertEqual(stop["quantity"], -10.0)
        self.assertAlmostEqual(stop["stop_price"], 98.0)    # 100 × (1 − 0.02)
        self.assertEqual(tp["order_type"], "LIMIT")
        self.assertEqual(tp["quantity"], -10.0)
        self.assertAlmostEqual(tp["limit_price"], 105.0)    # 100 × (1 + 0.05)

    def test_sell_short_stop_and_take_profit_prices_are_inverse(self):
        legs = _build_bracket_legs(
            action="SELL", direction="SHORT",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(len(legs), 2)
        stop = next(l for l in legs if l["leg_type"] == "stop_loss")
        tp = next(l for l in legs if l["leg_type"] == "take_profit")
        self.assertEqual(stop["quantity"], 10.0)            # positive: cover the short
        self.assertAlmostEqual(stop["stop_price"], 102.0)   # 100 × (1 + 0.02)
        self.assertEqual(tp["quantity"], 10.0)
        self.assertAlmostEqual(tp["limit_price"], 95.0)     # 100 × (1 − 0.05)

    def test_zero_entry_quantity_returns_empty_legs(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=0, entry_price=100.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(legs, [])

    def test_zero_entry_price_returns_empty_legs(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=0.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(legs, [])

    def test_stop_loss_only_produces_single_stop_leg(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.03, take_profit_pct=0.0,
        )
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["leg_type"], "stop_loss")

    def test_take_profit_only_produces_single_limit_leg(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.0, take_profit_pct=0.04,
        )
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["leg_type"], "take_profit")


class ExecutorBracketGuardEdgeCaseTests(unittest.TestCase):

    def _bracket_signal(self, action="BUY", direction="LONG"):
        return {
            "signal_id": f"sig-edge-{action.lower()}-{direction.lower()}",
            "symbol": "AAPL.US",
            "action": action,
            "direction": direction,
            "quantity": 10,
            "quantity_type": "SHARES",
            "metadata": {
                "risk_parameters": {
                    "stop_loss_pct": 0.02,
                    "take_profit_pct": 0.05,
                }
            },
        }

    def test_canary_bracket_order_is_fail_closed(self):
        """Canary stage is outside allowed bracket execution stages → always logged_only."""
        algo = _CanaryAlgo()
        execute(self._bracket_signal(), algo)
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        log = algo.bracket_logs[0]
        self.assertEqual(log["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(log["submitted_to_broker"])
        self.assertEqual(log["metadata"]["guard_stage"], "canary")
        self.assertIn("paper/sim", log["metadata"]["guard_reason"])

    def test_invalid_bracket_zero_entry_fill_is_logged_only(self):
        """Guard passes but MarketOrder produces no portfolio delta → entry_qty=0 → invalid_bracket_quantity_or_price."""
        algo = _PaperNoFillAlgo()
        execute(self._bracket_signal(), algo)
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        log = algo.bracket_logs[0]
        self.assertEqual(log["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(log["submitted_to_broker"])
        self.assertEqual(log["metadata"]["reason"], "invalid_bracket_quantity_or_price")

    def test_non_entry_exit_signal_with_risk_params_is_logged_only(self):
        """EXIT+LONG is not a bracket entry combination → risk params present → logged_only with not_entry_signal."""
        algo = _PaperWithLongAlgo()
        execute(self._bracket_signal(action="EXIT", direction="LONG"), algo)
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        log = algo.bracket_logs[0]
        self.assertEqual(log["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(log["submitted_to_broker"])
        self.assertEqual(log["metadata"]["reason"], "not_entry_signal")


if __name__ == "__main__":
    unittest.main()
