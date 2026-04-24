import unittest

from services.execution.kraken_adapter import (
    KrakenAdapter,
    KrakenConfig,
    KrakenOrderIntent,
    normalize_kraken_symbol,
)
from services.execution.lean_runtime.symbol_parser import parse


class TestKrakenAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = KrakenAdapter(
            KrakenConfig(
                api_key="key",
                api_secret="secret",
                validate_only=True,
            )
        )

    def test_normalize_kraken_symbol_supports_compact_and_slashed_pairs(self):
        self.assertEqual(normalize_kraken_symbol("BTCUSD"), ("BTC/USD", "BTCUSD", "KRAKEN"))
        self.assertEqual(normalize_kraken_symbol("ETH/USDT.KRAKEN"), ("ETH/USDT", "ETHUSDT", "KRAKEN"))
        self.assertEqual(normalize_kraken_symbol("MATICGBP"), ("MATIC/GBP", "MATICGBP", "KRAKEN"))
        self.assertEqual(normalize_kraken_symbol("MATIC/GBP.KRAKEN"), ("MATIC/GBP", "MATICGBP", "KRAKEN"))

    def test_build_contract_preserves_venue_pair_boundary(self):
        contract = self.adapter.build_contract("BTCUSD.KRAKEN")
        self.assertEqual(contract.pair, "BTC/USD")
        self.assertEqual(contract.altname, "BTCUSD")
        self.assertEqual(contract.venue, "KRAKEN")

    def test_build_order_uses_validate_default_and_string_volume(self):
        payload = self.adapter.build_order(
            KrakenOrderIntent(symbol="ETH/USDT", side="buy", quantity=0.75)
        )
        self.assertEqual(payload["pair"], "ETH/USDT")
        self.assertEqual(payload["type"], "buy")
        self.assertEqual(payload["volume"], "0.75")
        self.assertEqual(payload["provider"], "Kraken")
        self.assertTrue(payload["validate"])

    def test_build_market_data_request_marks_kraken_as_execution_truth(self):
        payload = self.adapter.build_market_data_request("BTCUSD", interval=5, since=1713916800)
        self.assertEqual(payload["pair"], "BTC/USD")
        self.assertEqual(payload["interval"], 5)
        self.assertEqual(payload["sourceClass"], "broker_execution")
        self.assertEqual(payload["boundaryRole"], "venue_canonical")

    def test_build_websocket_subscription_marks_transport_and_boundary(self):
        payload = self.adapter.build_websocket_subscription(
            "BTCUSD",
            channel="ticker",
            snapshot=False,
            req_id=42,
        )
        self.assertEqual(payload["event"], "subscribe")
        self.assertEqual(payload["pair"], ["BTC/USD"])
        self.assertEqual(payload["subscription"]["name"], "ticker")
        self.assertFalse(payload["subscription"]["snapshot"])
        self.assertEqual(payload["reqid"], 42)
        self.assertEqual(payload["transport"], "websocket")
        self.assertEqual(payload["boundaryRole"], "venue_canonical")

    def test_normalize_quote_maps_numeric_fields(self):
        quote = self.adapter.normalize_quote(
            {
                "ts": "2026-04-24T16:00:00Z",
                "bid": "64320.1",
                "ask": "64321.0",
                "close": "64320.4",
                "vwap": "64290.8",
                "volume": "128.55",
                "funding_rate": "0.0001",
            },
            "BTCUSD.KRAKEN",
        )
        self.assertEqual(quote.symbol, "BTCUSD")
        self.assertEqual(quote.base_asset, "BTC")
        self.assertEqual(quote.quote_asset, "USD")
        self.assertEqual(quote.last, 64320.4)
        self.assertEqual(quote.close, 64320.4)
        self.assertEqual(quote.volume, 128.55)
        self.assertEqual(quote.source_class, "broker_execution")

    def test_normalize_quote_preserves_distinct_last_and_close(self):
        quote = self.adapter.normalize_quote(
            {
                "ts": "2026-04-24T16:00:00Z",
                "last": "64321.1",
                "close": "64320.4",
                "bid": "64320.1",
                "ask": "64321.0",
            },
            "BTCUSD.KRAKEN",
        )
        self.assertEqual(quote.last, 64321.1)
        self.assertEqual(quote.close, 64320.4)

    def test_normalize_websocket_ticker_maps_kraken_array_shape(self):
        quote = self.adapter.normalize_websocket_ticker(
            {
                "channel": "ticker",
                "ts": "2026-04-24T16:00:03Z",
                "a": ["64321.0", "1", "1.0"],
                "b": ["64320.1", "2", "2.0"],
                "c": ["64320.9", "0.3"],
                "v": ["120.0", "128.55"],
                "p": ["64288.0", "64290.8"],
            },
            "BTCUSD.KRAKEN",
        )
        self.assertEqual(quote.symbol, "BTCUSD")
        self.assertEqual(quote.transport, "websocket")
        self.assertEqual(quote.channel, "ticker")
        self.assertEqual(quote.replay_source, "websocket_ticker")
        self.assertTrue(quote.is_replayable)
        self.assertEqual(quote.last, 64320.9)
        self.assertEqual(quote.bid, 64320.1)
        self.assertEqual(quote.ask, 64321.0)
        self.assertEqual(quote.volume, 128.55)

    def test_reconcile_execution_sync_prefers_ws_realtime_and_rest_close(self):
        state = self.adapter.reconcile_execution_sync(
            symbol="BTCUSD.KRAKEN",
            rest_payload={
                "ts": "2026-04-24T16:00:00Z",
                "close": "64320.4",
                "bid": "64319.9",
                "ask": "64320.5",
                "volume": "120.0",
            },
            websocket_payload={
                "channel": "ticker",
                "ts": "2026-04-24T16:00:03Z",
                "a": ["64321.0", "1", "1.0"],
                "b": ["64320.1", "2", "2.0"],
                "c": ["64320.9", "0.3"],
                "v": ["120.0", "128.55"],
            },
        )
        self.assertEqual(state.symbol, "BTCUSD")
        self.assertEqual(state.rest_ts, "2026-04-24T16:00:00Z")
        self.assertEqual(state.websocket_ts, "2026-04-24T16:00:03Z")
        self.assertEqual(state.ts, "2026-04-24T16:00:03Z")
        self.assertEqual(state.last, 64320.9)
        self.assertEqual(state.close, 64320.4)
        self.assertEqual(state.bid, 64320.1)
        self.assertEqual(state.ask, 64321.0)
        self.assertEqual(state.volume, 128.55)
        self.assertEqual(state.replay_source, "websocket_backed_sync")
        self.assertTrue(state.is_replayable)


class TestKrakenLeanSymbolParsing(unittest.TestCase):
    def test_kraken_crypto_symbols_are_supported(self):
        kraken_pair = parse("BTCUSD.KRAKEN")
        self.assertEqual(kraken_pair.ticker, "BTCUSD")
        self.assertEqual(kraken_pair.lean_market, "Market.Kraken")
        self.assertEqual(kraken_pair.lean_security_type, "SecurityType.Crypto")

        heuristic_pair = parse("ETHUSDT")
        self.assertEqual(heuristic_pair.lean_market, "Market.Kraken")


if __name__ == "__main__":
    unittest.main()
