"""Governed minute-bar aggregation and the fail-closed frequency loader.

SD_INTRADAY_MINUTE_BARS §6.1–§6.3 / §9 K4.

* ``1m`` reads reconciled ``tw_price_1m`` rows; ``5m/15m/30m/60m`` are derived here
  from those same reconciled rows.  ``1d`` is *not* served by this module: a daily
  request is rejected instead of being answered from another dataset.
* Buckets align to the 09:00 session start (never wall-clock hours).  The last
  bucket is truncated at the end of the session and flagged ``half_bucket``.
* A bucket with a missing minute is ``partial``; partial buckets are never imputed
  and are skipped by default.
* Strict point in time: a bar is returned only when ``bar_availability_ts`` (plus the
  declared minute lag) is ``<= decision_time``.  A bucket is available only once its
  *whole* window has closed and every member bar is obtainable.
* Anything that is not reconciled raises ``InsufficientCleanHistory``; there is no
  fallback to daily or synthetic bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from services.registry.strategy_artifact import (
    BAR_FREQUENCY_DATASETS,
    StrategyArtifactValidationError,
    dataset_for_bar_frequency,
)

TAIPEI = timezone(timedelta(hours=8))
SESSION_OPEN_MINUTE = 9 * 60  # 09:00 Asia/Taipei
SESSION_CLOSE_MINUTE = 13 * 60 + 30  # regular session ends 13:30 (SD §6.3); a 13:30 closing-auction bar folds into the last bucket
SESSION_BARS = SESSION_CLOSE_MINUTE - SESSION_OPEN_MINUTE  # 270 regular minutes (09:00..13:29)
MINUTE_DATASET = "tw_price_1m"
FREQUENCY_MINUTES: Mapping[str, int] = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


class IntradayBarsError(RuntimeError):
    """The request cannot be served from governed minute data."""


class InsufficientCleanHistory(IntradayBarsError):
    """A requested session has no reconciled minute rows (never imputed or substituted)."""

    def __init__(self, symbol: str, missing_days: Sequence[str]) -> None:
        self.symbol = symbol
        self.missing_days = tuple(missing_days)
        super().__init__(
            f"insufficient_clean_history: {symbol} has no reconciled tw_price_1m rows for "
            + ", ".join(self.missing_days)
        )


@dataclass(frozen=True)
class Bar:
    dataset: str
    bar_frequency: str
    symbol: str
    session_date: str
    bar_start: str
    bar_end: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_count: int
    expected_bars: int
    partial: bool
    half_bucket: bool
    bar_availability_ts: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def minutes_for_frequency(bar_frequency: Any) -> int:
    """Validate against the §6.1 table; daily and unknown values fail closed."""
    dataset_for_bar_frequency(bar_frequency)  # rejects anything outside the table
    if bar_frequency not in FREQUENCY_MINUTES:
        raise IntradayBarsError(
            f"bar_frequency {bar_frequency!r} ({BAR_FREQUENCY_DATASETS[bar_frequency]}) "
            "is not a minute frequency; this loader never serves or substitutes daily bars"
        )
    return FREQUENCY_MINUTES[bar_frequency]


def _ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo is not None else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def _out(value: Decimal) -> float:
    return float(value)


def aggregate_session(
    rows: Sequence[Mapping[str, Any]],
    bar_frequency: str,
    *,
    symbol: str,
    session_date: str,
    session_close_minute: int = SESSION_CLOSE_MINUTE,
) -> list[Bar]:
    """Aggregate one reconciled session of 1-minute rows to ``bar_frequency`` bars.

    Rows must carry ``bar_start``/``bar_end``/OHLCV and ``bar_availability_ts``.  A row
    that is malformed, outside the regular session, off the minute grid, duplicated, or
    lacks an availability stamp raises: a reconciled day must not contain such rows and
    nothing is silently dropped or repaired.
    """
    width = minutes_for_frequency(bar_frequency)
    if isinstance(session_close_minute, bool) or not isinstance(session_close_minute, int) or not (
        SESSION_OPEN_MINUTE < session_close_minute <= SESSION_CLOSE_MINUTE
    ):
        raise IntradayBarsError("session_close_minute must be a minute-of-day within (09:00, 13:30]")
    session_bars = session_close_minute - SESSION_OPEN_MINUTE  # early close shortens the session
    buckets: dict[int, dict[int, tuple[Mapping[str, Any], datetime, datetime, datetime]]] = {}
    for row in rows:
        start, end, avail = _ts(row.get("bar_start")), _ts(row.get("bar_end")), _ts(row.get("bar_availability_ts"))
        if start is None or end is None or avail is None:
            raise IntradayBarsError("minute row lacks tz-aware bar_start/bar_end/bar_availability_ts")
        local = start.astimezone(TAIPEI)
        if local.date().isoformat() != session_date:
            raise IntradayBarsError(f"minute row {row.get('bar_start')!r} is outside session {session_date}")
        offset = local.hour * 60 + local.minute - SESSION_OPEN_MINUTE
        if local.second or local.microsecond or not 0 <= offset <= session_bars:
            raise IntradayBarsError(f"minute row {row.get('bar_start')!r} is outside the regular session grid")
        # The close-minute bar (13:30 auction) is a real source bar: keep it in the last bucket.
        slot = buckets.setdefault((min(offset, session_bars - 1) if width > 1 else offset) // width, {})
        if offset in slot:
            raise IntradayBarsError(f"duplicate minute row at {row.get('bar_start')!r}")
        slot[offset] = (row, start, end, avail)

    out: list[Bar] = []
    for idx in sorted(buckets):
        first_offset = idx * width
        expected = min(width, session_bars - first_offset) if width > 1 else 1
        members = [buckets[idx][k] for k in sorted(buckets[idx])]
        values: list[list[Decimal]] = []
        for row, *_ in members:
            parsed = [_num(row.get(k)) for k in ("open", "high", "low", "close", "volume")]
            if any(v is None for v in parsed):
                raise IntradayBarsError(f"malformed OHLCV in minute row {row.get('bar_start')!r}")
            values.append(parsed)  # type: ignore[arg-type]
        bucket_start = datetime.combine(
            date.fromisoformat(session_date), datetime.min.time(), tzinfo=TAIPEI
        ) + timedelta(minutes=SESSION_OPEN_MINUTE + first_offset)
        bucket_end = bucket_start + timedelta(minutes=expected)
        # The bucket is obtainable only when its window closed and every member is.
        avail = max([bucket_end.astimezone(timezone.utc), *(m[3] for m in members)])
        out.append(
            Bar(
                dataset=BAR_FREQUENCY_DATASETS[bar_frequency],
                bar_frequency=bar_frequency,
                symbol=symbol,
                session_date=session_date,
                bar_start=bucket_start.isoformat(),
                bar_end=bucket_end.isoformat(),
                open=_out(values[0][0]),
                high=_out(max(v[1] for v in values)),
                low=_out(min(v[2] for v in values)),
                close=_out(values[-1][3]),
                volume=_out(sum((v[4] for v in values), Decimal(0))),
                bar_count=len(members),
                expected_bars=expected,
                partial=width > 1 and sum(1 for k in buckets[idx] if k < session_bars) < expected,
                half_bucket=expected < width,
                bar_availability_ts=_iso(avail),
            )
        )
    return out


def load_bars(
    store: Any,
    symbol: str,
    session_dates: Iterable[str],
    bar_frequency: str,
    *,
    decision_time: datetime,
    availability_lag_minutes: int = 0,
    include_partial: bool = False,
    session_close_minute: int = SESSION_CLOSE_MINUTE,
) -> list[Bar]:
    """Frequency→dataset loader over reconciled ``tw_price_1m`` days only.

    ``decision_time`` is mandatory and tz-aware.  Raises ``InsufficientCleanHistory``
    if any requested session has no reconciled rows (quarantined, pending, missing or
    waived), and ``IntradayBarsError`` for daily/unknown frequencies.
    """
    from services.source_ingestion.intraday_reconcile import read_reconciled_rows  # lazy: avoids import cycle

    minutes_for_frequency(bar_frequency)
    if not isinstance(decision_time, datetime) or decision_time.tzinfo is None:
        raise IntradayBarsError("decision_time must be a tz-aware datetime (strict point in time)")
    if isinstance(availability_lag_minutes, bool) or not isinstance(availability_lag_minutes, int) or availability_lag_minutes < 0:
        raise IntradayBarsError("availability_lag_minutes must be a non-negative integer")
    lag = timedelta(minutes=availability_lag_minutes)
    days = sorted(set(session_dates))
    missing: list[str] = []
    bars: list[Bar] = []
    for day in days:
        try:
            rows = read_reconciled_rows(store, symbol, day)
        except (ValueError, StrategyArtifactValidationError) as exc:
            raise IntradayBarsError(str(exc)) from exc
        if not rows:
            missing.append(day)
            continue
        for bar in aggregate_session(rows, bar_frequency, symbol=symbol, session_date=day, session_close_minute=session_close_minute):
            if bar.partial and not include_partial:
                continue
            if _ts(bar.bar_availability_ts) + lag <= decision_time:  # type: ignore[operator]
                bars.append(bar)
    if missing:
        raise InsufficientCleanHistory(symbol, missing)
    return bars


__all__ = [
    "Bar",
    "InsufficientCleanHistory",
    "IntradayBarsError",
    "aggregate_session",
    "load_bars",
    "minutes_for_frequency",
]
