"""One-time, resumable price-history backfill over per-date all-stock exchange pages."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .fetchers import FetchAccessBlockedError, FetchBackendUnavailableError
from .runner import CollectorRunner, SourcePausedError
from .spiders import EXCHANGE_TRADING_DAYS_PER_YEAR, TW_EXCHANGE_HOLIDAYS, latest_published_trading_date

BACKFILL_SOURCES = frozenset(
    {
        "collector-tw-twse-price-history",
        "collector-tw-tpex-price-history",
        "collector-tw-twse-t86",
        "collector-tw-twse-index-breadth",
        "collector-tw-twse-taiex-history",
    }
)
TWO_YEARS_DAYS = 731
MONTHLY_SOURCE = "collector-tw-twse-taiex-history"


def resolve_months(start_month: str, end_month: str) -> list[str]:
    """Inclusive calendar months, one page each, with no holiday filtering."""
    for value in (start_month, end_month):
        if len(value) != 7 or datetime.strptime(value, "%Y-%m").strftime("%Y-%m") != value:
            raise ValueError("months must use YYYY-MM")
    if start_month < "2012-01" or start_month > end_month:
        raise ValueError("month range must start at or after 2012-01 and end after start")
    year, month = map(int, start_month.split("-"))
    months = []
    while f"{year:04d}-{month:02d}" <= end_month:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months

def load_extra_holidays(path: str | Path | None) -> frozenset[str]:
    """Extra closure dates from a JSON list file (typhoon days, newly announced holidays)."""
    if not path:
        return frozenset()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return frozenset(date.fromisoformat(str(v)).isoformat() for v in raw)


def trading_dates(
    start: str,
    end: str,
    *,
    holidays: Iterable[str] = TW_EXCHANGE_HOLIDAYS,
) -> list[str]:
    """Oldest-first weekdays in [start, end] that are not known exchange holidays."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    skip = set(holidays)
    out: list[str] = []
    day = first
    while day <= last:
        iso = day.isoformat()
        if day.weekday() < 5 and iso not in skip:
            out.append(iso)
        day += timedelta(days=1)
    return out


def last_n_trading_dates(end: str, count: int, *, holidays: Iterable[str] = TW_EXCHANGE_HOLIDAYS) -> list[str]:
    if count <= 0:
        raise ValueError("trading day count must be > 0")
    skip = set(holidays)
    out: list[str] = []
    day = date.fromisoformat(end)
    while len(out) < count:
        if day.weekday() < 5 and day.isoformat() not in skip:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(out)


def resolve_dates(
    *,
    start_date: str | None,
    end_date: str | None,
    trading_days: int | None,
    now_iso: str,
    holidays: Iterable[str] = TW_EXCHANGE_HOLIDAYS,
) -> list[str]:
    """Expand CLI options into the per-date request list (one request per trading day)."""
    end = end_date or latest_published_trading_date(now_iso)
    if trading_days is not None:
        if start_date:
            raise ValueError("use either --trading-days or --start-date, not both")
        return last_n_trading_dates(end, trading_days, holidays=holidays)
    if not start_date:
        raise ValueError("a date range needs --start-date (or --trading-days)")
    if start_date > end:
        raise ValueError(f"start date {start_date} is after end date {end}")
    return trading_dates(start_date, end, holidays=holidays)


def two_year_range(now_iso: str) -> tuple[str, str]:
    end = latest_published_trading_date(now_iso)
    start = (date.fromisoformat(end) - timedelta(days=TWO_YEARS_DAYS - 1)).isoformat()
    return start, end


def estimate(dates: Sequence[str], *, min_interval_seconds: float = 5.0) -> dict[str, Any]:
    return {
        "requests": len(dates),
        "min_interval_seconds": min_interval_seconds,
        "estimated_wall_seconds": len(dates) * min_interval_seconds,
        "estimated_wall_minutes": round(len(dates) * min_interval_seconds / 60, 1),
    }


class BackfillLedger:
    """Per-source record of completed dates so an interrupted backfill never refetches them."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.done: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            self.done = dict(json.loads(self.path.read_text(encoding="utf-8")).get("completed", {}))

    def is_done(self, trade_date: str) -> bool:
        return trade_date in self.done

    def mark(self, trade_date: str, *, outcome: str, run_id: str) -> None:
        self.done[trade_date] = {"outcome": outcome, "run_id": run_id}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"completed": self.done}, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


@dataclass
class BackfillResult:
    source_id: str
    requested: int = 0
    fetched: list[str] = field(default_factory=list)
    holidays_confirmed: list[str] = field(default_factory=list)
    skipped_already_done: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    stopped_at: str | None = None

    @property
    def complete(self) -> bool:
        return self.stopped_reason is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "dates_requested": self.requested,
            "fetched": len(self.fetched),
            "holidays_confirmed": self.holidays_confirmed,
            "skipped_already_done": len(self.skipped_already_done),
            "complete": self.complete,
            "stopped_reason": self.stopped_reason,
            "stopped_at": self.stopped_at,
        }


def run_backfill(
    runner: CollectorRunner,
    source_id: str,
    dates: Sequence[str],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] | None = None,
    ledger_path: Path | str | None = None,
) -> BackfillResult:
    """Fetch each date once (one request, one bundle), oldest first, resuming from the ledger."""
    if source_id not in BACKFILL_SOURCES:
        raise ValueError(f"{source_id!r} does not support backfill; use one of {sorted(BACKFILL_SOURCES)}")
    policy = runner.config.get_source(source_id)
    interval = float(policy.min_interval_seconds)
    ledger = BackfillLedger(ledger_path or runner.root_dir / "state" / "backfill" / f"{source_id}.json")
    if source_id == MONTHLY_SOURCE:
        dates = sorted(set(d[:7] for d in dates))
        for month in dates:
            resolve_months(month, month)
    result = BackfillResult(source_id=source_id, requested=len(dates))
    requested_any = False
    for trade_date in dates:
        if ledger.is_done(trade_date):
            result.skipped_already_done.append(trade_date)
            continue
        if requested_any and interval > 0:
            sleep_fn(interval)  # one connection, >= min_interval between requests across per-date runs
        requested_any = True
        run_id = f"backfill-{trade_date}"
        stamp = now_fn() if now_fn else None
        try:
            outcome = runner.run_source(
                source_id, run_id=run_id, started_at=stamp, finished_at=stamp,
                collect_kwargs={"dates": [trade_date]},
            )
        except (SourcePausedError, FetchAccessBlockedError, FetchBackendUnavailableError) as exc:
            result.stopped_reason, result.stopped_at = f"{type(exc).__name__}: {exc}", trade_date
            break
        reason = outcome.bundle.manifest.get("reason")
        if outcome.status == "complete":
            # The current month is still growing: refresh it on the next invocation.
            current_month = (stamp or datetime.now()).strftime("%Y-%m")
            if source_id != MONTHLY_SOURCE or trade_date < current_month:
                ledger.mark(trade_date, outcome="fetched", run_id=run_id)
            result.fetched.append(trade_date)
        elif outcome.status == "partial" and reason == "no_data_for_date":
            ledger.mark(trade_date, outcome="no_data", run_id=run_id)
            result.holidays_confirmed.append(trade_date)
        else:
            result.stopped_reason = f"{outcome.status}:{reason}"
            result.stopped_at = trade_date
            break
    return result
