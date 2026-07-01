from unittest.mock import MagicMock
from streaming_quotes import StreamingQuoteManager


def _mgr():
    api = MagicMock()
    m = StreamingQuoteManager("k", "s", api=api, enable_keepalive=False)
    assert m.start() is True  # injected api -> registers callbacks, no login
    return m, api


def test_tick_updates_live_price():
    m, _ = _mgr()
    assert m.ensure_subscribed("2330.TW") is True
    tick = MagicMock(); tick.code = "2330"; tick.close = 2345.0
    m._on_tick("TSE", tick)
    assert m.live_price("2330") == 2345.0
    assert m.live_price("2330.TW") == 2345.0
    assert "2330" in m.quote_list


def test_bidask_midpoint_when_no_tick():
    m, _ = _mgr()
    m.ensure_subscribed("2317")
    ba = MagicMock(); ba.code = "2317"; ba.bid_price = [248.0]; ba.ask_price = [249.0]
    m._on_bidask("TSE", ba)
    assert m.live_price("2317") == 248.5


def test_quote_list_capped():
    api = MagicMock()
    m = StreamingQuoteManager("k", "s", api=api, max_subscriptions=1, enable_keepalive=False)
    m.start()
    assert m.ensure_subscribed("2330") is True
    assert m.ensure_subscribed("2317") is False  # cap reached
    assert m.quote_list == ["2330"]


def test_non_tw_not_subscribed():
    m, _ = _mgr()
    assert m.ensure_subscribed("AAPL.US") is False
    assert m.live_price("AAPL.US") is None


def test_subscribe_registers_callbacks():
    m, api = _mgr()
    assert api.set_on_tick_stk_v1_callback.called
    assert api.set_on_bidask_stk_v1_callback.called


def test_reconnect_resubscribes_quote_list():
    api = MagicMock()
    m = StreamingQuoteManager("k", "s", api=api, enable_keepalive=False)
    m.start()
    m.ensure_subscribed("2330")
    m.ensure_subscribed("2317")
    assert m.quote_list == ["2317", "2330"]
    m._on_session_down()            # broker drops the session
    assert m._session_ok is False
    m._reconnect()                  # keepalive would call this
    assert m._session_ok is True
    assert m.reconnect_count == 1
    assert m.quote_list == ["2317", "2330"]   # entire 報價列 re-subscribed
