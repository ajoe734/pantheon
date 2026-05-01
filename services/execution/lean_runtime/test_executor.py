import unittest

from services.execution.lean_runtime.executor import (
    BRACKET_ORDER_STATUS_LOGGED_ONLY,
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
    ):
        self.bracket_logs.append(
            {
                "symbol": symbol,
                "signal_id": signal_id,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "broker_submission_status": broker_submission_status,
                "submitted_to_broker": submitted_to_broker,
            }
        )

    def SetHoldings(self, symbol, target_percent):  # noqa: N802
        raise AssertionError("SetHoldings should not be used by this signal")

    def Liquidate(self, symbol):  # noqa: N802
        raise AssertionError("Liquidate should not be used by this signal")


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
        self.assertEqual(
            algo.bracket_logs,
            [
                {
                    "symbol": "AAPL",
                    "signal_id": "sig-bracket-001",
                    "stop_loss_pct": 0.02,
                    "take_profit_pct": 0.05,
                    "broker_submission_status": BRACKET_ORDER_STATUS_LOGGED_ONLY,
                    "submitted_to_broker": False,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
