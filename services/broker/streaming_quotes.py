"""Shioaji streaming real-time quote manager (tick-level, subscription-based).

Unlike TWSE MIS (a ~5s public snapshot poll), Shioaji pushes per-tick updates
over a persistent session. You subscribe contracts (the "quote list" / 報價列,
capped at the broker's subscription limit) and receive tick/bidask callbacks
that maintain an in-memory live price map. Order pricing then reads the latest
tick instantly — no per-order request.

The quote list is the active TW execution universe (seed core symbols plus any
symbol the broker actually trades), bounded by MAX_SUBSCRIPTIONS. Symbols beyond
the cap, non-TW symbols, or a closed/unavailable session fall back to the
caller's other sources (e.g. TWSE MIS).

The local broker package was renamed away from ``shioaji`` so ``import shioaji``
here resolves to the installed SDK, not the adapter package.
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
    # Shioaji bidask price fields are lists (price ladder); take the best.
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
        api=None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._seed = [s for s in seed_symbols if s]
        self._max = int(max_subscriptions)
        self._api = api  # injectable for tests
        self._lock = threading.Lock()
        self._prices: dict[str, dict] = {}      # ticker -> {last,bid,ask,ts}
        self._subscribed: set[str] = set()        # the 報價列
        self._started = False
        self._login_failed_at: float = 0.0

    # ------------------------------------------------------------------ #
    # session lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        with self._lock:
            if self._started:
                return True
            # back off briefly after a failed login (closed window / outage)
            if self._login_failed_at and (time.monotonic() - self._login_failed_at) < 60:
                return False
            try:
                api = self._api
                if api is None:
                    import shioaji as sj  # installed SDK (post-rename, no shadow)
                    api = sj.Shioaji(simulation=True)
                    api.login(api_key=self._api_key, secret_key=self._secret_key)  # noqa
                api.set_on_tick_stk_v1_callback(self._on_tick)
                api.set_on_bidask_stk_v1_callback(self._on_bidask)
                self._api = api
                self._started = True
                self._login_failed_at = 0.0
            except Exception as exc:  # closed window, creds, SDK, network
                self._login_failed_at = time.monotonic()
                log.warning("shioaji streaming session start failed: %s", exc)
                return False
        for sym in self._seed:
            self.ensure_subscribed(sym)
        return True

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
        if not code or not self._started:
            return None
        try:
            contract = self._api.Contracts.Stocks[code]
            snaps = self._api.snapshots([contract])
            if not snaps:
                return None
            s = snaps[0]
            return _first(getattr(s, "close", None)) or (
                round((_first(s.buy_price) + _first(s.sell_price)) / 2.0, 4)
                if _first(getattr(s, "buy_price", None)) and _first(getattr(s, "sell_price", None))
                else None
            )
        except Exception:
            return None

    @property
    def quote_list(self) -> list[str]:
        return sorted(self._subscribed)
