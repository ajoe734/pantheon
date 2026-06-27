"""Authoritative real-time quote pricing for paper fills.

The broker is the market boundary, so it owns the quote session. Market paper
orders are priced from a live quote instead of a placeholder:

  primary : Shioaji snapshot (broker-grade bid/ask/last, via the sandbox login)
  fallback: TWSE MIS public real-time endpoint (free, no SDK/session)

Read-only: this never places or matches orders. Returns None when no source is
available, so callers can fall back to their own placeholder.
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
    # Suffixed (2330.TW) or a bare TWSE/TPEx numeric ticker (e.g. 2330, 00878).
    return s.endswith(_TW_SUFFIXES) or (s.isdigit() and 4 <= len(s) <= 6)


class QuotePricer:
    """TTL-cached real-time price lookup: Shioaji primary, TWSE MIS fallback."""

    def __init__(self, *, shioaji_adapter: object = None, ttl_seconds: float = 5.0,
                 timeout_seconds: float = 4.0) -> None:
        self._adapter = shioaji_adapter
        self._ttl = float(ttl_seconds)
        self._timeout = float(timeout_seconds)
        self._cache: dict[str, tuple[float, Optional[float]]] = {}
        self._lock = threading.Lock()

    def market_price(self, symbol: str) -> Optional[float]:
        key = str(symbol or "").strip().upper()
        if not key:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
        price = self._shioaji_price(key)
        if price is None and _is_tw_symbol(key):
            price = self._twse_mis_price(key)
        with self._lock:
            self._cache[key] = (now, price)
        return price

    # --- primary: Shioaji snapshot ---
    def _shioaji_price(self, symbol: str) -> Optional[float]:
        adapter = self._adapter
        if adapter is None or not _is_tw_symbol(symbol):
            return None
        try:
            return adapter.snapshot_price(symbol)  # type: ignore[attr-defined]
        except Exception:
            return None

    # --- fallback: TWSE MIS ---
    def _twse_mis_price(self, symbol: str) -> Optional[float]:
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
