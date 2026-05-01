import unittest

from services.execution.lean_runtime.executor import (
    BRACKET_ORDER_STATUS_LOGGED_ONLY,
    BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
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


if __name__ == "__main__":
    unittest.main()
