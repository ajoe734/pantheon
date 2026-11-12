"""Same-day close capture: TWSE and TPEx all-stock daily price pages after the Taiwan close.

The OpenAPI daily feed only publishes the day's rows the next morning, so a timer fires this
command every ten minutes from 14:40 to 17:00 Taipei. Each source is fetched at most once per
trading day (a ledger marks completed dates); a failed or not-yet-published attempt is retried by
the next timer tick until the window closes. One connection, one request per source, >= the
policy ``min_interval_seconds`` between the two sources.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .fetchers import FetchAccessBlockedError, FetchBackendUnavailableError
from .runner import CollectorRunner, SourcePausedError
from .spiders import TW_EXCHANGE_HOLIDAYS

CLOSE_CAPTURE_SOURCES = (
    "collector-tw-twse-price-history",
    "collector-tw-tpex-price-history",
)
TAIPEI = timezone(timedelta(hours=8))
WINDOW_START = dtime(14, 40)
WINDOW_END = dtime(17, 0)


@dataclass
class CloseCaptureResult:
    trade_date: str
    state: str  # outside_window | not_trading_day | attempted
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def retry_needed(self) -> bool:
        return any(v not in {"captured", "already_captured"} for v in self.sources.values())

    def to_dict(self) -> dict[str, Any]:
        return {"trade_date": self.trade_date, "state": self.state, "sources": dict(self.sources)}


def _ledger_path(root: Path, source_id: str) -> Path:
    return root / "state" / "same-day-close" / f"{source_id}.json"


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")).get("completed", {})) if path.is_file() else {}


def _mark(path: Path, trade_date: str, run_id: str) -> None:
    done = _load(path)
    done[trade_date] = {"run_id": run_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"completed": done}, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def capture_same_day_close(
    runner: CollectorRunner,
    *,
    now: datetime,
    sources: Iterable[str] = CLOSE_CAPTURE_SOURCES,
    holidays: Iterable[str] = TW_EXCHANGE_HOLIDAYS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> CloseCaptureResult:
    local = now.astimezone(TAIPEI)
    trade_date = local.date().isoformat()
    if local.weekday() >= 5 or trade_date in set(holidays):
        return CloseCaptureResult(trade_date, "not_trading_day")
    if not WINDOW_START <= local.time().replace(tzinfo=None) <= WINDOW_END:
        return CloseCaptureResult(trade_date, "outside_window")

    result = CloseCaptureResult(trade_date, "attempted")
    requested = False
    for source_id in sources:
        ledger = _ledger_path(runner.root_dir, source_id)
        if trade_date in _load(ledger):
            result.sources[source_id] = "already_captured"
            continue
        interval = float(runner.config.get_source(source_id).min_interval_seconds)
        if requested and interval > 0:
            sleep_fn(interval)
        requested = True
        run_id = f"close-{trade_date}-{local.strftime('%H%M')}"
        try:
            outcome = runner.run_source(
                source_id, run_id=run_id, started_at=now, finished_at=now,
                collect_kwargs={"dates": [trade_date]},
            )
        except (SourcePausedError, FetchAccessBlockedError, FetchBackendUnavailableError) as exc:
            result.sources[source_id] = f"failed:{type(exc).__name__}"
            continue
        if outcome.status == "complete":
            _mark(ledger, trade_date, run_id)
            result.sources[source_id] = "captured"
        else:
            # Page not published yet / fetch failed: the next timer tick retries until 17:00.
            result.sources[source_id] = f"retry:{outcome.bundle.manifest.get('reason') or outcome.status}"
    return result
