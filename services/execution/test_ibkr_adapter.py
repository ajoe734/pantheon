import unittest

from services.execution.ibkr_adapter import (
    IBKRAdapter,
    IBKRConfig,
    IBKROrderIntent,
    normalize_us_symbol,
)
from services.execution.lean_runtime.symbol_parser import parse


class TestIBKRAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = IBKRAdapter(
            IBKRConfig(
                host="127.0.0.1",
                port=7497,
                client_id=9,
                account_id="DU1234567",
                readonly_market_data=True,
                market_data_type=3,
            )
        )

    def test_normalize_us_symbol_supports_suffix_and_default_smart_route(self):
        self.assertEqual(normalize_us_symbol("AAPL.US"), ("AAPL", "SMART"))
        self.assertEqual(normalize_us_symbol("MSFT.NASDAQ"), ("MSFT", "NASDAQ"))
        self.assertEqual(normalize_us_symbol("SPY", venue="ARCA"), ("SPY", "ARCA"))

    def test_build_contract_uses_primary_exchange_for_direct_route(self):
        contract = self.adapter.build_contract("MSFT.NASDAQ")
        self.assertEqual(contract.symbol, "MSFT")
        self.assertEqual(contract.exchange, "NASDAQ")
        self.assertEqual(contract.primary_exchange, "NASDAQ")

    def test_build_order_uses_default_account_and_contract_payload(self):
        payload = self.adapter.build_order(
            IBKROrderIntent(symbol="AAPL.US", side="buy", quantity=5)
        )
        self.assertEqual(payload["contract"]["symbol"], "AAPL")
        self.assertEqual(payload["contract"]["exchange"], "SMART")
        self.assertEqual(payload["order"]["account"], "DU1234567")
        self.assertEqual(payload["order"]["action"], "BUY")

    def test_build_market_data_request_preserves_market_data_type(self):
        payload = self.adapter.build_market_data_request(
            "QQQ.NASDAQ",
            snapshot=True,
            generic_ticks="233",
        )
        self.assertEqual(payload["contract"]["symbol"], "QQQ")
        self.assertEqual(payload["contract"]["primaryExchange"], "NASDAQ")
        self.assertEqual(payload["marketDataType"], 3)
        self.assertTrue(payload["snapshot"])
        self.assertTrue(payload["readonly"])
        self.assertEqual(payload["provider"], "IBKR market data")
        self.assertEqual(payload["sourceClass"], "broker_execution")
        self.assertEqual(payload["boundaryRole"], "execution_sync_fallback")

    def test_normalize_quote_maps_numeric_fields(self):
        quote = self.adapter.normalize_quote(
            {
                "ts": "2026-04-24T14:30:00Z",
                "bidPrice": "209.11",
                "askPrice": "209.15",
                "lastPrice": "209.13",
                "bidSize": "4",
                "askSize": 7,
                "close": "208.02",
                "volume": "18000421",
                "contract": {"exchange": "NASDAQ"},
            },
            "AAPL.US",
        )
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.exchange, "NASDAQ")
        self.assertEqual(quote.last, 209.13)
        self.assertEqual(quote.bid_size, 4)
        self.assertEqual(quote.volume, 18000421)
        self.assertEqual(quote.provider, "IBKR market data")
        self.assertEqual(quote.source_class, "broker_execution")
        self.assertEqual(quote.boundary_role, "execution_sync_fallback")


class TestUSLeanSymbolParsing(unittest.TestCase):
    def test_us_equity_suffix_is_supported(self):
        us_equity = parse("AAPL.US")
        self.assertEqual(us_equity.ticker, "AAPL")
        self.assertEqual(us_equity.lean_security_type, "SecurityType.Equity")


if __name__ == "__main__":
    unittest.main()
