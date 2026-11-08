from __future__ import annotations

import unittest

from services.broker.paper_simulation import (
    PaperSimulationStore,
    SimulationError,
    simulate_paper_order,
)


class PaperSimulationTests(unittest.TestCase):
    def test_cash_share_simulation_defaults(self) -> None:
        order = simulate_paper_order(
            capital_pool_id="pool-paper",
            strategy_id="strat-1",
            symbol="2330",
            qty=1000,
            side="buy",
            market_price=2370.0,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.instrument_id, "eq:2330")
        self.assertEqual(order.kind, "cash_share")
        self.assertEqual(order.multiplier, 1.0)
        self.assertIsNone(order.contract_month)
        self.assertEqual(order.fill_price, 2370.0)
        self.assertEqual(order.fill_qty, 1000.0)

    def test_stock_future_simulation_carries_contract_and_price_basis_metadata(self) -> None:
        order = simulate_paper_order(
            capital_pool_id="pool-paper",
            strategy_id="strat-1",
            symbol="2330",
            qty=1,
            side="buy",
            market_price=2247.0,
            instrument_id="ssf:QFF:202611",
            kind="stock_future",
            multiplier=100.0,
            contract_month="202611",
            price_basis="futures_daily_settlement_prev_session",
            fee=5.0,
            tax=4.494,
            initial_margin_reserved=30334.5,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.instrument_id, "ssf:QFF:202611")
        self.assertEqual(order.kind, "stock_future")
        self.assertEqual(order.multiplier, 100.0)
        self.assertEqual(order.contract_month, "202611")
        self.assertEqual(order.price_basis, "futures_daily_settlement_prev_session")
        self.assertAlmostEqual(order.fee, 5.0)
        self.assertAlmostEqual(order.tax, 4.494)
        self.assertAlmostEqual(order.initial_margin_reserved, 30334.5)

    def test_stock_future_rejects_non_settlement_price_basis(self) -> None:
        with self.assertRaises(SimulationError) as ctx:
            simulate_paper_order(
                capital_pool_id="pool-paper",
                strategy_id="strat-1",
                symbol="2330",
                qty=1,
                side="buy",
                market_price=2247.0,
                instrument_id="ssf:QFF:202611",
                kind="stock_future",
                multiplier=100.0,
                contract_month="202611",
                price_basis="stock_close",
            )
        self.assertEqual(ctx.exception.error_code, "INVALID_FUTURES_PRICE_BASIS")

    def test_stock_future_rejects_missing_market_price(self) -> None:
        with self.assertRaises(SimulationError) as ctx:
            simulate_paper_order(
                capital_pool_id="pool-paper",
                strategy_id="strat-1",
                symbol="2330",
                qty=1,
                side="buy",
                market_price=None,
                instrument_id="ssf:QFF:202611",
                kind="stock_future",
                multiplier=100.0,
                contract_month="202611",
            )
        self.assertEqual(ctx.exception.error_code, "MARKET_PRICE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
