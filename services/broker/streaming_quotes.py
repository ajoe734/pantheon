"""Shioaji streaming real-time quote manager (tick-level, subscription-based).

Unlike TWSE MIS (a ~5s public snapshot poll), Shioaji pushes per-tick updates
over a persistent session. You subscribe contracts (the "quote list" / 報價列,
capped at the broker's subscription limit) and receive tick/bidask callbacks
that maintain an in-memory live price map. Order pricing then reads the latest
tick instantly — no per-order request.

The quote list is the active TW execution universe (seed core symbols plus any
symbol the broker actually trades), bounded by MAX_SUBSCRIPTIONS.

Resilience: a session-down callback plus a background keepalive thread detect
drops during market hours and reconnect, re-subscribing the entire quote list so
the tick feed self-heals without manual intervention.

The local broker package was renamed away from ``shioaji`` so ``import shioaji``
resolves to the installed SDK, not the adapter package.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

MAX_SUBSCRIPTIONS = 500  # Shioaji per-session subscription limit

try:  # resolve real SDK enums once; string fallback keeps the module importable
    import shioaji as _sj  # installed SDK (post package rename, no shadow)
    _QUOTE_TYPE_TICK = _sj.constant.QuoteType.Tick
    _QUOTE_TYPE_BIDASK = _sj.constant.QuoteType.BidAsk
except Exception:  # pragma: no cover - SDK absent (e.g. unit tests)
    _QUOTE_TYPE_TICK = "tick"
    _QUOTE_TYPE_BIDASK = "bidask"


def _native_ticker(symbol: str) -> str:
    return str(symbol or "").strip().upper().split(".", 1)[0]


def _first(value) -> Optional[float]:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


class StreamingQuoteManager:
    """Persistent Shioaji session maintaining a subscribed live price map."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        seed_symbols=(),
        max_subscriptions: int = MAX_SUBSCRIPTIONS,
        reconnect_interval: float = 20.0,
        enable_keepalive: bool = True,
        api=None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._seed = [s for s in seed_symbols if s]
        self._max = int(max_subscriptions)
        self._reconnect_interval = float(reconnect_interval)
        self._enable_keepalive = bool(enable_keepalive)
        self._injected_api = api  # for tests; reused on reconnect
        self._api = None
        self._lock = threading.Lock()
        self._prices: dict[str, dict] = {}      # ticker -> {last,bid,ask,ts}
        self._subscribed: set[str] = set()        # the 報價列
        self._started = False
        self._session_ok = False
        self._login_failed_at: float = 0.0
        self._keepalive_thread: Optional[threading.Thread] = None
        self._reconnect_count = 0

    # ------------------------------------------------------------------ #
    # session lifecycle + reconnect
    # ------------------------------------------------------------------ #
    def _connect_locked(self) -> bool:
        """(Re)establish the session and register callbacks. Caller holds _lock."""
        try:
            api = self._injected_api
            if api is None:
                import shioaji as sj  # installed SDK
                api = sj.Shioaji(simulation=True)
                api.login(api_key=self._api_key, secret_key=self._secret_key)
            api.set_on_tick_stk_v1_callback(self._on_tick)
            api.set_on_bidask_stk_v1_callback(self._on_bidask)
            try:
                api.set_session_down_callback(self._on_session_down)
            except Exception:  # pragma: no cover - older SDK without the hook
                pass
            self._api = api
            self._session_ok = True
            self._login_failed_at = 0.0
            return True
        except Exception as exc:  # closed window, creds, SDK, network
            self._session_ok = False
            self._login_failed_at = time.monotonic()
            log.warning("shioaji session connect failed: %s", exc)
            return False

    def start(self) -> bool:
        with self._lock:
            if self._started:
                return self._session_ok
            if self._login_failed_at and (time.monotonic() - self._login_failed_at) < 30:
                return False
            if not self._connect_locked():
                return False
            self._started = True
        for sym in self._seed:
            self.ensure_subscribed(sym)
        self._ensure_keepalive()
        return True

    def _on_session_down(self, *_args, **_kwargs) -> None:
        log.warning("shioaji session down; keepalive will reconnect")
        self._session_ok = False

    def _ensure_keepalive(self) -> None:
        if not self._enable_keepalive:
            return
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        t = threading.Thread(target=self._keepalive_loop, name="shioaji-keepalive", daemon=True)
        self._keepalive_thread = t
        t.start()

    def _keepalive_loop(self) -> None:
        while True:
            time.sleep(self._reconnect_interval)
            if self._session_ok:
                continue
            self._reconnect()

    def _reconnect(self) -> None:
        with self._lock:
            if self._session_ok:
                return
            self._injected_api = self._injected_api  # tests reuse the mock
            if self._injected_api is None:
                self._api = None  # force a fresh login below
            ok = self._connect_locked()
            if not ok:
                return
            self._reconnect_count += 1
            symbols = sorted(self._subscribed)
            self._subscribed.clear()
        # re-subscribe the entire quote list on the restored session
        for sym in symbols:
            self.ensure_subscribed(sym)
        log.info("shioaji reconnected (#%d); re-subscribed %d symbols",
                 self._reconnect_count, len(symbols))

    # ------------------------------------------------------------------ #
    # callbacks -> live price map
    # ------------------------------------------------------------------ #
    def _on_tick(self, _exchange, tick) -> None:
        code = _native_ticker(getattr(tick, "code", "") or "")
        last = _first(getattr(tick, "close", None))
        if code and last:
            entry = self._prices.setdefault(code, {})
            entry["last"] = last
            entry["ts"] = time.time()

    def _on_bidask(self, _exchange, bidask) -> None:
        code = _native_ticker(getattr(bidask, "code", "") or "")
        if not code:
            return
        entry = self._prices.setdefault(code, {})
        bid = _first(getattr(bidask, "bid_price", None))
        ask = _first(getattr(bidask, "ask_price", None))
        if bid:
            entry["bid"] = bid
        if ask:
            entry["ask"] = ask
        entry["ts"] = time.time()

    # ------------------------------------------------------------------ #
    # subscription list (報價列) management
    # ------------------------------------------------------------------ #
    def ensure_subscribed(self, symbol: str) -> bool:
        code = _native_ticker(symbol)
        if not code or not code.isdigit():
            return False
        if code in self._subscribed:
            return True
        if not self._started and not self.start():
            return False
        if not self._session_ok:
            return False
        with self._lock:
            if code in self._subscribed:
                return True
            if len(self._subscribed) >= self._max:
                log.warning("quote list full (%d); not subscribing %s", self._max, code)
                return False
            try:
                contract = self._api.Contracts.Stocks[code]
                self._api.quote.subscribe(contract, quote_type=_QUOTE_TYPE_TICK)
                self._api.quote.subscribe(contract, quote_type=_QUOTE_TYPE_BIDASK)
                self._subscribed.add(code)
                return True
            except Exception as exc:
                log.warning("subscribe %s failed: %s", code, exc)
                return False

    def live_price(self, symbol: str) -> Optional[float]:
        entry = self._prices.get(_native_ticker(symbol))
        if not entry:
            return None
        if entry.get("last"):
            return entry["last"]
        bid, ask = entry.get("bid"), entry.get("ask")
        if bid and ask:
            return round((bid + ask) / 2.0, 4)
        return bid or ask

    def snapshot_price(self, symbol: str) -> Optional[float]:
        """One-off snapshot to warm a freshly-subscribed symbol before ticks arrive."""
        code = _native_ticker(symbol)
        if not code or not self._session_ok:
            return None
        try:
            contract = self._api.Contracts.Stocks[code]
            snaps = self._api.snapshots([contract])
            if not snaps:
                return None
            s = snaps[0]
            last = _first(getattr(s, "close", None))
            if last:
                return last
            bid = _first(getattr(s, "buy_price", None))
            ask = _first(getattr(s, "sell_price", None))
            if bid and ask:
                return round((bid + ask) / 2.0, 4)
            return bid or ask
        except Exception:
            return None

    @property
    def quote_list(self) -> list[str]:
        return sorted(self._subscribed)

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count
