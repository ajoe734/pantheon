"""Authoritative real-time quote pricing for paper fills.

The broker is the market boundary, so it owns the quote session. Market paper
orders are priced from a real-time quote instead of a placeholder:

  primary : Shioaji streaming tick (per-tick live price map; the symbol is added
            to the subscription list / 報價列 on first use). Read-only — quotes
            only, no order placement or matching.
  fallback: TWSE MIS public endpoint (~5s snapshot, free, no SDK/session).

Returns None when no source is available, so callers fall back to their own
placeholder. The streaming path is instant (in-memory map) and is not cached;
only the TWSE MIS HTTP path is TTL-cached.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_TW_SUFFIXES = (".TW", ".TWSE", ".TWO", ".TPEX")
_TW_OTC_SUFFIXES = (".TWO", ".TPEX")


def _to_float(value: object) -> Optional[float]:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _is_tw_symbol(symbol: str) -> bool:
    s = str(symbol or "").strip().upper()
    return s.endswith(_TW_SUFFIXES) or (s.isdigit() and 4 <= len(s) <= 6)


class QuotePricer:
    """Real-time price lookup: Shioaji streaming primary, TWSE MIS fallback."""

    def __init__(self, *, streaming_manager: object = None, ttl_seconds: float = 5.0,
                 timeout_seconds: float = 4.0) -> None:
        self._streaming = streaming_manager
        self._ttl = float(ttl_seconds)
        self._timeout = float(timeout_seconds)
        self._mis_cache: dict[str, tuple[float, Optional[float]]] = {}
        self._lock = threading.Lock()

    def market_price(self, symbol: str) -> Optional[float]:
        key = str(symbol or "").strip().upper()
        if not key:
            return None
        # 1. Shioaji streaming live tick (instant in-memory read; not cached)
        price = self._streaming_price(key)
        if price is not None:
            return price
        # 2. TWSE MIS fallback (TTL-cached HTTP snapshot)
        if _is_tw_symbol(key):
            return self._twse_mis_cached(key)
        return None

    # --- primary: Shioaji streaming tick ---
    def _streaming_price(self, symbol: str) -> Optional[float]:
        mgr = self._streaming
        if mgr is None or not _is_tw_symbol(symbol):
            return None
        try:
            mgr.ensure_subscribed(symbol)          # add to 報價列
            live = mgr.live_price(symbol)            # latest tick
            if live is not None:
                return live
            return mgr.snapshot_price(symbol)        # warm-up before first tick
        except Exception:
            return None

    # --- fallback: TWSE MIS (~5s), TTL-cached ---
    def _twse_mis_cached(self, symbol: str) -> Optional[float]:
        now = time.monotonic()
        with self._lock:
            cached = self._mis_cache.get(symbol)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
        price = self._twse_mis_fetch(symbol)
        with self._lock:
            self._mis_cache[symbol] = (now, price)
        return price

    def _twse_mis_fetch(self, symbol: str) -> Optional[float]:
        ticker = symbol.split(".", 1)[0]
        channel = "otc_" if symbol.endswith(_TW_OTC_SUFFIXES) else "tse_"
        ex_ch = f"{channel}{ticker}.tw"
        url = f"{_TWSE_MIS_URL}?ex_ch={urllib.parse.quote(ex_ch)}&json=1&delay=0"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None
        return self.price_from_mis_payload(payload)

    @staticmethod
    def price_from_mis_payload(payload: dict) -> Optional[float]:
        arr = payload.get("msgArray") if isinstance(payload, dict) else None
        if not arr:
            return None
        q = arr[0]
        last = _to_float(q.get("z"))
        if last:
            return last
        bid = _to_float(str(q.get("b") or "").split("_")[0])
        ask = _to_float(str(q.get("a") or "").split("_")[0])
        if bid and ask:
            return round((bid + ask) / 2.0, 4)
        return bid or ask or _to_float(q.get("y"))
