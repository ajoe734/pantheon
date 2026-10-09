"""Fail-closed paper performance valuation from fills and real market marks.

The paper runtime historically valued every fill at the same in-process price
used to execute it.  Without an independent market-data update that makes
``cash + positions - initial_cash`` exactly zero, yet the runtime still emitted
that zero as authoritative PnL.  This module keeps the valuation boundary
explicit:

* source-ingest supplies price, source and observation time;
* every open position must have a finite positive mark;
* the oldest mark is the atomic valuation ``as_of`` boundary;
* incomplete marks produce diagnostics and no performance sample.

``drawdown_pct`` is represented as a fractional ratio (``0.18`` means 18%),
matching evolution threshold baselines.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), datetime_time.min, tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


_VENUE_SUFFIXES = frozenset(
    {
        "US",
        "TW",
        "TWSE",
        "TWO",
        "TPEX",
        "TAIFEX",
        "NYSE",
        "NASDAQ",
        "KRAKEN",
        "BINANCE",
        "COINBASE",
        "CRYPTO",
    }
)
_CRYPTO_QUOTES = ("USDT", "USDC", "USD", "BTC", "ETH", "BNB")


def _symbol_aliases(value: Any, quote_currency: str | None = None) -> frozenset[str]:
    text = str(value or "").strip().upper().replace(" ", "")
    if not text:
        return frozenset()
    aliases = {text}
    pair_candidates: set[str] = set()
    if "." in text:
        base, suffix = text.rsplit(".", 1)
        if suffix in _VENUE_SUFFIXES:
            if suffix == "CRYPTO" and quote_currency:
                pair_candidates.add(f"{base}/{str(quote_currency).strip().upper()}")
            else:
                aliases.add(base)
                pair_candidates.add(base)
    else:
        pair_candidates.add(text)
    for candidate in tuple(pair_candidates):
        normalized_pair = candidate.replace("-", "/")
        if "/" in normalized_pair:
            base, quote = normalized_pair.rsplit("/", 1)
            if base and quote in _CRYPTO_QUOTES:
                aliases.update({f"{base}/{quote}", f"{base}-{quote}", f"{base}{quote}"})
                continue
        for quote in _CRYPTO_QUOTES:
            if candidate.endswith(quote) and len(candidate) > len(quote):
                base = candidate[: -len(quote)]
                aliases.update({f"{base}/{quote}", f"{base}-{quote}", f"{base}{quote}"})
                break
    return frozenset(aliases)


def _same_instrument(requested: str, returned: str) -> bool:
    left = requested.strip().upper().replace(" ", "")
    right = returned.strip().upper().replace(" ", "")
    if left == right:
        return True
    # Two different venue-qualified symbols (AAPL.US vs AAPL.TW) share a base
    # alias but are different instruments.
    if "." in left and "." in right:
        return False
    return bool(_symbol_aliases(left) & _symbol_aliases(right))


def _nested_tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_nested_tuple(item) for item in value)
    return value


@dataclass(frozen=True)
class MarketMark:
    symbol: str
    price: float
    as_of: str
    source_ref: str
    quote_currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "price": self.price,
            "as_of": self.as_of,
            "source_ref": self.source_ref,
        }
        if self.quote_currency:
            payload["quote_currency"] = self.quote_currency
        return payload


@dataclass(frozen=True)
class PerformanceSample:
    pnl: float
    portfolio_value: float
    initial_cash: float
    cash: float
    as_of: str
    fill_count: int
    marks: tuple[MarketMark, ...]

    @property
    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.as_of,
            round(self.pnl, 10),
            round(self.portfolio_value, 10),
            self.fill_count,
            tuple(
                (
                    mark.symbol,
                    round(mark.price, 10),
                    mark.as_of,
                    mark.source_ref,
                    mark.quote_currency,
                )
                for mark in self.marks
            ),
        )


@dataclass(frozen=True)
class ValuationResult:
    status: str
    sample: PerformanceSample | None
    diagnostic: dict[str, Any]


class SourceIngestMarkProvider:
    """Resolve the latest market marks from the governed per-symbol snapshot.

    Each requested symbol is read from
    ``GET /api/source-ingest/snapshots/latest?symbol=`` (the read-only stored
    snapshot projection the paper fleet reconciler also reads).  The mark is
    the snapshot's latest close at its ``event_time`` with the snapshot's
    ``source_ref``.  Network and payload failures are reported as diagnostics;
    they never result in a fallback or previously cached price.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        max_mark_age_seconds: float | None = None,
        future_tolerance_seconds: float = 300.0,
        fetch_json: Callable[[str, float], Mapping[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        configured_url = (
            base_url
            if base_url is not None
            else os.getenv("PANTHEON_SOURCE_INGEST_URL")
            or os.getenv("PANTHEON_SOURCE_INGEST_API_URL")
            or ""
        )
        self._base_url = str(configured_url or "").strip().rstrip("/")
        self._timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PANTHEON_MARK_SOURCE_TIMEOUT_SECONDS", "2")
        )
        self._cache_ttl = max(
            float(
                cache_ttl_seconds
                if cache_ttl_seconds is not None
                else os.getenv("PANTHEON_MARK_CACHE_TTL_SECONDS", "15")
            ),
            0.0,
        )
        self._fetch_json = fetch_json or self._default_fetch_json
        self._max_mark_age = max(
            float(
                max_mark_age_seconds
                if max_mark_age_seconds is not None
                else os.getenv("PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS", "172800")
            ),
            0.0,
        )
        self._future_tolerance = max(float(future_tolerance_seconds), 0.0)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._marks_by_symbol: dict[str, MarketMark] = {}
        self._fetched_monotonic: dict[str, float] = {}
        self._symbol_errors: dict[str, str] = {}
        self._last_refresh_at: str | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    @staticmethod
    def _default_fetch_json(url: str, timeout: float) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("source-ingest response must be a JSON object")
        return payload

    def resolve(self, symbols: Sequence[str]) -> tuple[dict[str, MarketMark], dict[str, Any]]:
        requested = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]
        if not requested:
            return {}, self.snapshot(requested_symbols=[])
        if not self.enabled:
            self._last_error = "source_ingest_url_unconfigured"
        resolved: dict[str, MarketMark] = {}
        for symbol in dict.fromkeys(requested):
            if self.enabled:
                self._refresh_symbol_if_needed(symbol)
            mark = self._marks_by_symbol.get(symbol)
            if mark is not None and self._mark_is_fresh(mark):
                resolved[symbol] = mark
        diagnostic = self.snapshot(requested_symbols=requested)
        diagnostic["resolved_symbols"] = sorted(resolved)
        diagnostic["missing_symbols"] = sorted(set(requested) - set(resolved))
        return resolved, diagnostic

    def snapshot(self, *, requested_symbols: Sequence[str] | None = None) -> dict[str, Any]:
        return {
            "source": "source_ingest",
            "url": self._base_url or None,
            "enabled": self.enabled,
            "last_refresh_at": self._last_refresh_at,
            "last_error": self._last_error,
            "indexed_mark_count": len(self._marks_by_symbol),
            "max_mark_age_seconds": self._max_mark_age,
            "requested_symbols": list(requested_symbols or []),
        }

    def _mark_is_fresh(self, mark: MarketMark) -> bool:
        observed = _parse_rfc3339(mark.as_of)
        if observed is None:
            return False
        age = (self._now().astimezone(timezone.utc) - observed).total_seconds()
        return -self._future_tolerance <= age <= self._max_mark_age

    def _refresh_symbol_if_needed(self, symbol: str) -> None:
        now_monotonic = time.monotonic()
        fetched = self._fetched_monotonic.get(symbol)
        if fetched is not None and now_monotonic - fetched < self._cache_ttl:
            self._sync_last_error()
            return
        try:
            payload = self._fetch_json(
                f"{self._base_url}/api/source-ingest/snapshots/latest"
                f"?symbol={urllib.parse.quote(symbol, safe='')}",
                self._timeout,
            )
            mark = self._mark_from_snapshot(symbol, payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, urllib.error.URLError) as exc:
            # A failed refresh must not silently reuse a previously cached
            # price as though the canonical source were still available.
            self._marks_by_symbol.pop(symbol, None)
            self._symbol_errors[symbol] = f"{symbol}: {type(exc).__name__}: {exc}"
        else:
            self._marks_by_symbol[symbol] = mark
            self._symbol_errors.pop(symbol, None)
        self._fetched_monotonic[symbol] = now_monotonic
        self._last_refresh_at = utc_now_rfc3339()
        self._sync_last_error()

    def _sync_last_error(self) -> None:
        self._last_error = "; ".join(sorted(self._symbol_errors.values())) or None

    @staticmethod
    def _mark_from_snapshot(symbol: str, payload: Any) -> MarketMark:
        if not isinstance(payload, Mapping):
            raise ValueError("source-ingest snapshot must be a JSON object")
        snapshot_symbol = str(payload.get("symbol") or "").strip()
        if not snapshot_symbol or not _same_instrument(symbol, snapshot_symbol):
            raise ValueError(
                f"snapshot symbol {snapshot_symbol!r} does not match requested symbol"
            )
        closes = payload.get("closes")
        price = _finite(closes[-1]) if isinstance(closes, list) and closes else None
        if price is None or price <= 0:
            raise ValueError("snapshot has no finite positive latest close")
        observed = _parse_rfc3339(payload.get("event_time"))
        if observed is None:
            raise ValueError("snapshot event_time is missing or invalid")
        source_ref = str(payload.get("source_ref") or "").strip()
        if not source_ref:
            raise ValueError("snapshot source_ref is missing")
        return MarketMark(
            symbol=symbol,
            price=price,
            as_of=_iso(observed),
            source_ref=source_ref,
        )


def value_portfolio(
    *,
    initial_cash: float,
    cash: float,
    positions: Sequence[Mapping[str, Any]],
    marks: Mapping[str, MarketMark],
    fill_count: int,
    last_fill_at: str | None,
    mark_diagnostic: Mapping[str, Any] | None = None,
    max_mark_age_seconds: float = 172800.0,
    future_tolerance_seconds: float = 300.0,
    now: datetime | None = None,
) -> ValuationResult:
    """Value a fill-derived paper book, refusing partial or fabricated marks."""
    attempted_at = utc_now_rfc3339()
    base_diagnostic: dict[str, Any] = {
        "attempted_at": attempted_at,
        "valuation_method": "fill_cash_ledger_mark_to_market",
        "fill_count": int(fill_count),
        "mark_source": dict(mark_diagnostic or {}),
    }
    if fill_count <= 0:
        return ValuationResult(
            status="no_fills",
            sample=None,
            diagnostic={**base_diagnostic, "code": "performance_no_fills"},
        )

    normalized_positions: list[tuple[str, float]] = []
    for position in positions:
        symbol = str(position.get("symbol") or "").strip()
        quantity = _finite(position.get("quantity"))
        if not symbol or quantity is None or abs(quantity) <= 1e-12:
            continue
        normalized_positions.append((symbol, quantity))

    ledger_as_of = _parse_rfc3339(last_fill_at)
    if normalized_positions and ledger_as_of is None:
        return ValuationResult(
            status="invalid_ledger",
            sample=None,
            diagnostic={**base_diagnostic, "code": "open_book_missing_fill_as_of"},
        )

    missing = sorted(symbol for symbol, _ in normalized_positions if symbol not in marks)
    if missing:
        return ValuationResult(
            status="marks_unavailable",
            sample=None,
            diagnostic={
                **base_diagnostic,
                "code": "missing_market_marks",
                "missing_symbols": missing,
            },
        )

    used_marks: list[MarketMark] = []
    portfolio_value = _finite(cash)
    initial = _finite(initial_cash)
    if portfolio_value is None or initial is None or initial <= 0:
        return ValuationResult(
            status="invalid_ledger",
            sample=None,
            diagnostic={**base_diagnostic, "code": "invalid_cash_ledger"},
        )
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stale: list[str] = []
    predating_ledger: list[str] = []
    for symbol, quantity in normalized_positions:
        mark = marks[symbol]
        observed = _parse_rfc3339(mark.as_of)
        if not math.isfinite(mark.price) or mark.price <= 0 or observed is None:
            return ValuationResult(
                status="marks_unavailable",
                sample=None,
                diagnostic={
                    **base_diagnostic,
                    "code": "invalid_market_mark",
                    "missing_symbols": [symbol],
                },
            )
        age_seconds = (reference_time - observed).total_seconds()
        if age_seconds < -max(float(future_tolerance_seconds), 0.0) or age_seconds > max(
            float(max_mark_age_seconds), 0.0
        ):
            stale.append(symbol)
            continue
        if ledger_as_of is not None and observed < ledger_as_of:
            predating_ledger.append(symbol)
            continue
        portfolio_value += quantity * mark.price
        used_marks.append(mark)

    if stale:
        return ValuationResult(
            status="marks_unavailable",
            sample=None,
            diagnostic={
                **base_diagnostic,
                "code": "stale_or_future_market_marks",
                "missing_symbols": sorted(stale),
                "max_mark_age_seconds": max(float(max_mark_age_seconds), 0.0),
            },
        )

    if predating_ledger:
        return ValuationResult(
            status="marks_unavailable",
            sample=None,
            diagnostic={
                **base_diagnostic,
                "code": "market_marks_predate_ledger",
                "missing_symbols": sorted(predating_ledger),
                "ledger_as_of": _iso(ledger_as_of) if ledger_as_of else None,
            },
        )

    if used_marks:
        as_of_dt = min(_parse_rfc3339(mark.as_of) for mark in used_marks)
        assert as_of_dt is not None
        as_of = _iso(as_of_dt)
    else:
        flat_as_of = _parse_rfc3339(last_fill_at)
        if flat_as_of is None:
            return ValuationResult(
                status="invalid_ledger",
                sample=None,
                diagnostic={**base_diagnostic, "code": "flat_book_missing_fill_as_of"},
            )
        as_of = _iso(flat_as_of)

    sample = PerformanceSample(
        pnl=float(portfolio_value - initial),
        portfolio_value=float(portfolio_value),
        initial_cash=float(initial),
        cash=float(cash),
        as_of=as_of,
        fill_count=int(fill_count),
        marks=tuple(sorted(used_marks, key=lambda mark: mark.symbol)),
    )
    return ValuationResult(
        status="valued",
        sample=sample,
        diagnostic={
            **base_diagnostic,
            "code": "performance_valued",
            "as_of": as_of,
            "position_count": len(normalized_positions),
            "mark_count": len(used_marks),
        },
    )


class RollingDrawdownTracker:
    """Compute the policy's 20-day rolling high-water drawdown."""

    def __init__(self, window_days: int = 20) -> None:
        self._window_days = max(int(window_days), 1)
        self._values: deque[tuple[datetime, float]] = deque()
        self._last_fingerprint: tuple[Any, ...] | None = None
        self._latest_as_of: datetime | None = None
        self._latest_sample_shared_with_seed = False

    def export_state(self) -> dict[str, Any]:
        return {
            "schema_version": "rolling_drawdown.v1",
            "window_days": self._window_days,
            "values": [
                {"as_of": _iso(as_of), "portfolio_value": value}
                for as_of, value in self._values
            ],
            "last_fingerprint": self._last_fingerprint,
            "latest_as_of": _iso(self._latest_as_of) if self._latest_as_of else None,
            "latest_sample_shared_with_seed": self._latest_sample_shared_with_seed,
        }

    def restore(self, payload: Mapping[str, Any] | None) -> None:
        self._values.clear()
        self._last_fingerprint = None
        self._latest_as_of = None
        self._latest_sample_shared_with_seed = False
        if not payload:
            return
        if payload.get("schema_version") != "rolling_drawdown.v1":
            raise ValueError("unsupported rolling drawdown state schema")
        values = payload.get("values")
        if not isinstance(values, list):
            raise ValueError("rolling drawdown values must be a list")
        previous: datetime | None = None
        restored: deque[tuple[datetime, float]] = deque()
        for item in values:
            if not isinstance(item, Mapping):
                raise ValueError("rolling drawdown value must be an object")
            as_of = _parse_rfc3339(item.get("as_of"))
            value = _finite(item.get("portfolio_value"))
            if as_of is None or value is None or (previous is not None and as_of < previous):
                raise ValueError("rolling drawdown state is invalid or out of order")
            restored.append((as_of, value))
            previous = as_of
        latest = _parse_rfc3339(payload.get("latest_as_of"))
        if latest is None and restored:
            latest = restored[-1][0]
        if latest is not None and not restored:
            raise ValueError("rolling drawdown latest timestamp has no window sample")
        if latest is not None and restored and latest != restored[-1][0]:
            raise ValueError("rolling drawdown latest timestamp must match its last sample")
        fingerprint = payload.get("last_fingerprint")
        self._values = restored
        self._last_fingerprint = (
            _nested_tuple(fingerprint) if isinstance(fingerprint, (list, tuple)) else None
        )
        self._latest_as_of = latest
        shared_with_seed = payload.get("latest_sample_shared_with_seed", False)
        if not isinstance(shared_with_seed, bool):
            raise ValueError("rolling drawdown seed-sharing flag must be boolean")
        self._latest_sample_shared_with_seed = shared_with_seed

    def observe(
        self,
        sample: PerformanceSample,
        *,
        initial_equity_as_of: str | None = None,
    ) -> dict[str, Any] | None:
        if sample.fingerprint == self._last_fingerprint:
            return None
        as_of = _parse_rfc3339(sample.as_of)
        if as_of is None or (self._latest_as_of is not None and as_of < self._latest_as_of):
            return None
        same_as_of_revision = self._latest_as_of is not None and as_of == self._latest_as_of
        if (
            same_as_of_revision
            and not self._latest_sample_shared_with_seed
            and self._values
            and self._values[-1][0] == as_of
        ):
            # A corrected value at the same observation boundary supersedes
            # the prior sample. Retaining both would let the obsolete value
            # remain a false high-water mark. A lone entry that also seeds
            # initial equity is preserved and the revision is added beside it.
            self._values.pop()
        cutoff = as_of - timedelta(days=self._window_days)
        seed_appended = False
        if not self._values:
            seed_as_of = _parse_rfc3339(initial_equity_as_of) or as_of
            seed_as_of = min(seed_as_of, as_of)
            if seed_as_of >= cutoff:
                self._values.append((seed_as_of, float(sample.initial_cash)))
                seed_appended = True
        sample_shared_with_seed = False
        if not (
            self._values
            and self._values[-1][0] == as_of
            and self._values[-1][1] == float(sample.portfolio_value)
        ):
            self._values.append((as_of, float(sample.portfolio_value)))
        elif seed_appended or (
            same_as_of_revision and self._latest_sample_shared_with_seed
        ):
            sample_shared_with_seed = True
        while self._values and self._values[0][0] < cutoff:
            self._values.popleft()
        peak = max(value for _, value in self._values)
        if not math.isfinite(peak) or peak <= 0:
            raise ValueError("rolling drawdown high-water mark must be finite and positive")
        drawdown = min(max((peak - sample.portfolio_value) / peak, 0.0), 1.0)
        self._last_fingerprint = sample.fingerprint
        self._latest_as_of = as_of
        self._latest_sample_shared_with_seed = sample_shared_with_seed
        return {
            "drawdown_pct": float(drawdown),
            "peak_portfolio_value": float(peak),
            "portfolio_value": float(sample.portfolio_value),
            "window_observations": len(self._values),
            "window_days": self._window_days,
            "drawdown_as_of": sample.as_of,
        }
