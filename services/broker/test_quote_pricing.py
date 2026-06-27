from unittest.mock import MagicMock

from quote_pricing import QuotePricer
from paper_simulation import simulate_paper_order
from shioaji.adapter import ShioajiBrokerAdapter


def test_mis_parse_uses_last_trade():
    payload = {"msgArray": [{"z": "2340.0000", "b": "2335.0000_", "a": "2340.0000_", "y": "2390.0000"}]}
    assert QuotePricer.price_from_mis_payload(payload) == 2340.0


def test_mis_parse_falls_back_to_bid_ask_mid():
    payload = {"msgArray": [{"z": "-", "b": "2335.0000_2330_", "a": "2345.0000_2350_", "y": "2390.0000"}]}
    assert QuotePricer.price_from_mis_payload(payload) == 2340.0


def test_mis_parse_empty():
    assert QuotePricer.price_from_mis_payload({"msgArray": []}) is None


def test_quote_pricer_prefers_shioaji():
    class _A:
        def snapshot_price(self, symbol):
            return 2400.0
    qp = QuotePricer(shioaji_adapter=_A())
    assert qp.market_price("2330.TW") == 2400.0  # shioaji primary, no network


def test_market_fill_uses_market_price():
    o = simulate_paper_order(capital_pool_id="p", strategy_id="s", symbol="2330",
                             qty=1, side="sell", order_type="market", market_price=2340.0)
    assert o.fill_price == 2340.0


def test_market_fill_defaults_when_no_price():
    o = simulate_paper_order(capital_pool_id="p", strategy_id="s", symbol="2330",
                             qty=1, side="buy", order_type="market")
    assert o.fill_price == 100.0


def test_limit_fill_unchanged_by_market_price():
    o = simulate_paper_order(capital_pool_id="p", strategy_id="s", symbol="2330",
                             qty=1, side="buy", order_type="limit", limit_price=2300.0, market_price=9999.0)
    assert o.fill_price == 2300.0


def test_adapter_snapshot_price_returns_last():
    api = MagicMock()
    snap = MagicMock(); snap.close = 2340.0; snap.buy_price = 2335.0; snap.sell_price = 2340.0
    api.snapshots.return_value = [snap]
    api.Contracts.Stocks.__getitem__.return_value = MagicMock()
    adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=api)
    assert adapter.snapshot_price("2330.TW") == 2340.0
