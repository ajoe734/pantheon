import unittest

from services.execution.lean_runtime import member_virtual_book as book
from services.execution.lean_runtime import taiwan_cash_costs as costs


class TaiwanCashCostsTests(unittest.TestCase):
    def test_buy_has_commission_only(self):
        fee, tax = costs.cash_share_costs("2330.TW", "buy", 1000, 1000.0)
        self.assertAlmostEqual(fee, 1425.0)
        self.assertEqual(tax, 0.0)

    def test_sell_stock_and_etf_tax(self):
        fee, tax = costs.cash_share_costs("2330.TW", "sell", 1000, 1000.0)
        self.assertAlmostEqual((fee, tax), (1425.0, 3000.0))
        _, etf_tax = costs.cash_share_costs("0050.TW", "sell", 1000, 100.0)
        self.assertAlmostEqual(etf_tax, 100.0)

    def test_buy_cash_required_includes_commission(self):
        self.assertAlmostEqual(costs.buy_cash_required("2330.TW", 1, 1000.0), 1001.425)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            costs.cash_share_costs("2330.TW", "hold", 1, 1.0)
        with self.assertRaises(ValueError):
            costs.cash_share_costs("2330.TW", "buy", float("nan"), 1.0)

    def test_member_book_uses_the_shared_rates(self):
        self.assertIs(book._COMMISSION_RATE, costs.COMMISSION_RATE)
        self.assertIs(book._EQUITY_TAX_RATE, costs.EQUITY_TAX_RATE)
        self.assertIs(book._ETF_TAX_RATE, costs.ETF_TAX_RATE)
        self.assertEqual(book._net_interval_costs("0050.TW", [{"exposure": 1.0}, {"exposure": 0.0}]),
                         [costs.COMMISSION_RATE, costs.COMMISSION_RATE + costs.ETF_TAX_RATE])


if __name__ == "__main__":
    unittest.main()
