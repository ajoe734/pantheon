import unittest

from services.execution.lean_runtime.symbol_parser import SymbolParseError, parse
from services.execution.shioaji_adapter import (
    ShioajiAdapter,
    ShioajiConfig,
    ShioajiOrderIntent,
    normalize_taiwan_symbol,
)


class TestShioajiAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = ShioajiAdapter(
            ShioajiConfig(
                api_key="key",
                secret_key="secret",
                simulation=True,
                account_name="ACC-001",
            )
        )

    def test_normalize_taiwan_symbol_supports_listing_suffixes(self):
        self.assertEqual(normalize_taiwan_symbol("2330.TW"), ("2330", "TSE"))
        self.assertEqual(normalize_taiwan_symbol("6488.TWO"), ("6488", "OTC"))
        self.assertEqual(normalize_taiwan_symbol("TXF202604.TAIFEX"), ("TXF202604", "TAIFEX"))

    def test_build_contract_infers_future_type_for_taifex(self):
        contract = self.adapter.build_contract("TXF202604.TAIFEX")
        self.assertEqual(contract.contract_type, "future")
        self.assertEqual(contract.venue, "TAIFEX")
        self.assertEqual(contract.delivery_month, "202604")

    def test_build_order_uses_native_symbol_and_account_default(self):
        payload = self.adapter.build_order(
            ShioajiOrderIntent(symbol="2330.TWSE", side="buy", quantity=2, price=950.0)
        )
        self.assertEqual(payload["symbol"], "2330")
        self.assertEqual(payload["account"], "ACC-001")
        self.assertEqual(payload["simulation"], True)
        self.assertEqual(payload["side"], "BUY")

    def test_build_quote_subscription_preserves_exchange_boundary(self):
        payload = self.adapter.build_quote_subscription("6488.TPEX", quote_type="bidask")
        self.assertEqual(payload["exchange"], "OTC")
        self.assertEqual(payload["symbol"], "6488")
        self.assertEqual(payload["quote_type"], "bidask")
        self.assertEqual(payload["bind"], True)

    def test_normalize_quote_maps_numeric_fields(self):
        quote = self.adapter.normalize_quote(
            {
                "ts": "2026-04-24T09:05:00+08:00",
                "close": "952.0",
                "bid_price": "951.0",
                "ask_price": "952.0",
                "bid_volume": "12",
                "ask_volume": 8,
                "reference": "948.0",
                "total_volume": "1024",
            },
            "2330.TWSE",
        )
        self.assertEqual(quote.symbol, "2330")
        self.assertEqual(quote.venue, "TSE")
        self.assertEqual(quote.close, 952.0)
        self.assertEqual(quote.bid_volume, 12)
        self.assertEqual(quote.day_volume, 1024)


class TestTaiwanLeanSymbolParsing(unittest.TestCase):
    def test_taiwan_suffixes_are_rejected_by_lean_symbol_parser(self):
        with self.assertRaises(SymbolParseError):
            parse("2330.TWSE")

        with self.assertRaises(SymbolParseError):
            parse("TXF202604.TAIFEX")


if __name__ == "__main__":
    unittest.main()
