"""Governed whole-market daily price dataset built from the source-owned ``daily_bars`` store.

DISCOVERY-GOVERNED-MARKET-DATASET-20261125 (RC2; amendment to SD_DISCOVERY_DEMAND_AND_EVOLUTION_TRIGGERS_2026-11-12).

Template search, the hypothesis-card gate and persona pool admission all need a price dataset. They used
to wait for a DatasetVersion that only exists after a persona candidate was evaluated. This module reads the
stored prices instead, through ``DailyBarsReader`` only (read-only; it opens no file for writing and never
calls the source API, so dead letters of unrelated connectors cannot block it).

* ``build_market_boundary`` returns the market boundary of SD_STRATEGY_SCREEN_ENTRY_EXIT section 2: every
  symbol with governed data, minus suspended, too-short and illiquid ones, each exclusion with its reason.
  No persona list, no policy ``required_instruments`` and no synthetic or imputed bar is used.
* ``dataset_for`` materialises bounded records (open, high, low, close, volume, traded_value,
  available_time) for symbols of the boundary. A bar counts only when its stored ``available_time`` is not
  after the as-of time.
* The snapshot id is a hash of the day manifests' ``content_digest`` list plus the policy, so a reader can
  re-verify it with ``verify_snapshot`` from the same store.
* Missing, stale, corrupt, incomplete or unverifiable store days fail closed with a readable reason.

The work reads parquet and must run in a worker thread or process, never on an API event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

STORE_ROOT_ENV = "GOVERNED_MARKET_STORE_ROOT"
DEFAULT_STORE_ROOT = "/data/source-ingest/market_data_store"
SUPPORTED_SCHEMAS = ("daily_bars_manifest.v1", "daily_bars_manifest.v2")
MAX_DATASET_SYMBOLS = 60  # records are Python objects (about 0.55 MiB per symbol over 483 days): one dataset must fit a 256 MiB service
_READ_CHUNK = 100  # symbols per read; DailyBarsReader allows 500
_LISTING_BATCH_DAYS = 8  # list_symbols caches one symbol set per day; clear it between batches


class GovernedMarketDatasetError(RuntimeError):
    """A readable fail-closed reason: ``<code>: <detail>``. ``code`` is the stable part."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class MarketPolicy:
    """Eligibility of the market boundary. Gate thresholds, survivorship and trial counting are elsewhere."""

    window_days: int = 483  # stored trading days the dataset covers (about two years)
    min_history_days: int = 60  # fewer stored bars than this in the window = too short
    liquidity_days: int = 20  # liquidity is the median traded value of the newest stored days
    min_liquidity_observations: int = 10  # fewer observed traded values than this = unverifiable liquidity
    min_median_traded_value: float = 5_000_000.0  # TWD per day; below it = illiquid
    max_stale_calendar_days: int = 7  # newest stored day older than this vs the as-of date = stale
    min_symbols: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketBoundary:
    snapshot_id: str
    as_of: str
    window_start: str
    window_end: str
    trade_days: int
    symbols: tuple[str, ...]
    exclusions: Mapping[str, str]
    policy: MarketPolicy
    store_symbol_count: int

    def summary(self) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for reason in self.exclusions.values():
            key = reason.split(":", 1)[0]
            reasons[key] = reasons.get(key, 0) + 1
        return {
            "snapshot_id": self.snapshot_id, "as_of": self.as_of, "window_start": self.window_start,
            "window_end": self.window_end, "trade_days": self.trade_days, "included": len(self.symbols),
            "excluded": len(self.exclusions), "store_symbol_count": self.store_symbol_count,
            "exclusion_reasons": dict(sorted(reasons.items())), "policy": self.policy.as_dict(),
        }


def store_root(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    return Path(str(source.get(STORE_ROOT_ENV) or "").strip() or DEFAULT_STORE_ROOT)


def _require_worker() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise GovernedMarketDatasetError(
        "governed_dataset_must_run_in_worker", "parquet reads must not run on an API event loop")


def _as_of(now: Callable[[], datetime] | datetime | None) -> datetime:
    value = now() if callable(now) else (now or datetime.now(timezone.utc))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reader(root: Path):
    try:
        from services.source_ingestion.daily_bars_reader import DailyBarsReader
    except ImportError as exc:  # pyarrow absent: not an empty market
        raise GovernedMarketDatasetError("daily_bars_reader_unavailable", f"{type(exc).__name__}: {exc}") from exc
    return DailyBarsReader(root)


def _window_manifests(reader: Any, as_end: str | None, policy: MarketPolicy) -> tuple[list[str], list[tuple[str, str]]]:
    """Window dates and ``(date, content_digest)`` per day, after the cheap completeness checks."""
    from services.source_ingestion.daily_bars_store import DailyBarsError

    try:
        dates = reader.trade_dates()
    except OSError as exc:
        raise GovernedMarketDatasetError("daily_bars_store_unreadable", f"{type(exc).__name__}: {exc}") from exc
    if as_end:
        dates = [d for d in dates if d <= as_end]
    if not dates:
        raise GovernedMarketDatasetError("daily_bars_store_missing", f"no stored trade day under {reader.bars_dir}")
    window = dates[-policy.window_days:]
    digests: list[tuple[str, str]] = []
    for day in window:
        try:
            manifest = reader._manifest(day)
            if manifest.get("schema_version") not in SUPPORTED_SCHEMAS:
                raise GovernedMarketDatasetError("daily_bars_day_unverifiable", f"{day} has an unknown manifest schema")
            digest = manifest.get("content_digest")
            files = manifest.get("files")
            if not isinstance(digest, str) or not digest or not isinstance(files, list) or not files:
                raise GovernedMarketDatasetError("daily_bars_day_unverifiable", f"{day} has no digest or no files")
            if int(manifest.get("row_count") or 0) <= 0:
                raise GovernedMarketDatasetError("daily_bars_day_incomplete", f"{day} stores no rows")
            if sum(int(f.get("rows") or 0) for f in files) != int(manifest["row_count"]):
                raise GovernedMarketDatasetError("daily_bars_day_incomplete", f"{day} file rows differ from row_count")
            for entry in files:
                path = reader.bars_dir / entry["path"]
                if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
                    raise GovernedMarketDatasetError(
                        "daily_bars_day_incomplete", f"{day} file {entry.get('path')} is missing or has another size")
        except GovernedMarketDatasetError:
            raise
        except (DailyBarsError, OSError, ValueError, KeyError, TypeError) as exc:
            raise GovernedMarketDatasetError("daily_bars_day_corrupt", f"{day}: {type(exc).__name__}: {exc}") from exc
        digests.append((day, digest))
    return window, digests


def _snapshot_id(digests: Sequence[tuple[str, str]], policy: MarketPolicy) -> str:
    body = json.dumps({"days": [list(item) for item in digests], "policy": policy.as_dict()},
                      sort_keys=True, separators=(",", ":"))
    return f"gmd-{digests[-1][0]}-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:40]}"


def _chunks(items: Sequence[str], size: int):
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


def _list_stats(reader: Any, window: Sequence[str]) -> dict[str, list[Any]]:
    stats: dict[str, list[Any]] = {}
    for i in range(0, len(window), _LISTING_BATCH_DAYS):
        batch = window[i:i + _LISTING_BATCH_DAYS]
        for symbol, (days, first, last) in reader.list_symbols(batch[0], batch[-1]).items():
            held = stats.get(symbol)
            if held is None:
                stats[symbol] = [days, first, last]
            else:
                held[0] += days
                held[1], held[2] = min(held[1], first), max(held[2], last)
        reader._listing.clear()  # the cache is a per-call convenience here; keep the peak to one batch
    return stats


def build_market_boundary(
    root: str | Path | None = None, *, policy: MarketPolicy | None = None,
    now: Callable[[], datetime] | datetime | None = None, end_date: str | None = None,
    env: Mapping[str, str] | None = None,
) -> MarketBoundary:
    """The governed market boundary at the newest stored day (or ``end_date``), or a readable refusal."""
    _require_worker()
    policy = policy or MarketPolicy()
    as_of = _as_of(now)
    reader = _reader(Path(root) if root is not None else store_root(env))
    window, digests = _window_manifests(reader, end_date, policy)
    latest = window[-1]
    age = (as_of.date() - date.fromisoformat(latest)).days
    if end_date is None and age > policy.max_stale_calendar_days:
        raise GovernedMarketDatasetError(
            "daily_bars_store_stale", f"newest stored day {latest} is {age} days before {as_of.date().isoformat()}"
            f" (limit {policy.max_stale_calendar_days})")
    stats = _list_stats(reader, window)
    exclusions: dict[str, str] = {}
    survivors: list[str] = []
    for symbol in sorted(stats):
        days, _first, last = stats[symbol]
        if last != latest:
            exclusions[symbol] = f"suspended: last bar {last}, no bar on {latest}"
        elif days < policy.min_history_days:
            exclusions[symbol] = f"too_short_history: {days} bars, need {policy.min_history_days}"
        else:
            survivors.append(symbol)
    liquidity_window = window[-policy.liquidity_days:]
    included: list[str] = []
    for chunk in _chunks(survivors, _READ_CHUNK):
        rows = reader.read_range(chunk, liquidity_window[0], liquidity_window[-1],
                                 columns=["symbol", "trade_date", "traded_value"])
        for symbol in chunk:
            values = [r["traded_value"] for r in rows[symbol] if isinstance(r.get("traded_value"), (int, float))]
            if len(values) < policy.min_liquidity_observations:
                exclusions[symbol] = (f"illiquid: traded value observed on {len(values)} of the newest "
                                      f"{len(liquidity_window)} days, need {policy.min_liquidity_observations}")
            elif statistics.median(values) < policy.min_median_traded_value:
                exclusions[symbol] = (f"illiquid: median traded value {statistics.median(values):.0f} "
                                      f"< {policy.min_median_traded_value:.0f}")
            else:
                included.append(symbol)
    if len(included) < policy.min_symbols:
        raise GovernedMarketDatasetError(
            "market_boundary_empty", f"no symbol passes the boundary of {len(stats)} stored symbols")
    return MarketBoundary(
        snapshot_id=_snapshot_id(digests, policy), as_of=as_of.isoformat(), window_start=window[0], window_end=latest,
        trade_days=len(window), symbols=tuple(included), exclusions=exclusions, policy=policy,
        store_symbol_count=len(stats))


def verify_snapshot(snapshot_id: str, root: str | Path | None = None, *, policy: MarketPolicy | None = None,
                    env: Mapping[str, str] | None = None) -> bool:
    """Re-derive the id from the store's day manifests; ``True`` only when it is identical."""
    _require_worker()
    policy = policy or MarketPolicy()
    parts = str(snapshot_id).split("-")
    if len(parts) != 5 or parts[0] != "gmd":
        return False
    end = "-".join(parts[1:4])
    reader = _reader(Path(root) if root is not None else store_root(env))
    _, digests = _window_manifests(reader, end, policy)
    return digests[-1][0] == end and _snapshot_id(digests, policy) == snapshot_id


def _iso(value: Any) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else None


def dataset_for(
    boundary: MarketBoundary, symbols: Sequence[str] | None = None, *, root: str | Path | None = None,
    strategy_id: str = "governed-market-dataset", env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Records for ``symbols`` of the boundary (default: the first ``MAX_DATASET_SYMBOLS``), in the shape the
    gate and the template search read: ``dataset_id`` is the snapshot id; nothing is filled or imputed."""
    _require_worker()
    wanted = list(dict.fromkeys(symbols)) if symbols is not None else list(boundary.symbols[:MAX_DATASET_SYMBOLS])
    if not wanted:
        raise GovernedMarketDatasetError("dataset_symbols_missing", "no symbol requested")
    if len(wanted) > MAX_DATASET_SYMBOLS:
        raise GovernedMarketDatasetError(
            "dataset_symbols_over_limit", f"{len(wanted)} symbols requested, the limit is {MAX_DATASET_SYMBOLS}")
    outside = [s for s in wanted if s not in set(boundary.symbols)]
    if outside:
        detail = "; ".join(f"{s} ({boundary.exclusions.get(s, 'not in the store')})" for s in outside[:5])
        raise GovernedMarketDatasetError("symbols_outside_market_boundary", detail)
    reader = _reader(Path(root) if root is not None else store_root(env))
    cutoff = datetime.fromisoformat(boundary.as_of)
    records: list[dict[str, Any]] = []
    unavailable = 0
    for chunk in _chunks(wanted, _READ_CHUNK):
        rows = reader.read_range(
            chunk, boundary.window_start, boundary.window_end,
            columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "traded_value", "available_time"])
        for symbol in chunk:
            for r in rows[symbol]:
                available = r.get("available_time")
                if not isinstance(available, datetime) or available > cutoff:
                    unavailable += 1  # not point in time by its stored available_time: left out, never filled
                    continue
                records.append({
                    "instrument": symbol, "date": r["trade_date"], "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"],
                    "traded_value": r["traded_value"], "available_time": _iso(available)})
    if not records:
        raise GovernedMarketDatasetError("dataset_records_missing", f"no bar of {len(wanted)} symbols is available")
    records.sort(key=lambda r: (r["date"], r["instrument"]))
    return {
        "dataset_id": boundary.snapshot_id, "strategy_id": strategy_id,
        "source_dataset_refs": [f"daily_bars:{boundary.snapshot_id}"], "data_frequency": "daily",
        "records": records, "market_boundary": boundary.summary(), "bars_not_point_in_time": unavailable,
    }


def resolve_governed_dataset(
    *, symbols: Sequence[str] | None = None, snapshot_id: str | None = None, strategy_id: str = "governed-market-dataset",
    root: str | Path | None = None, now: Callable[[], datetime] | datetime | None = None,
    policy: MarketPolicy | None = None, env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One call for the three consumers. ``symbols`` are matched to stored forms (``2330`` finds ``2330.TW``).
    ``snapshot_id`` pins an earlier snapshot: the dataset is built at that snapshot's day and its id must
    re-derive identically, otherwise it fails closed (the store changed or the id is not ours)."""
    _require_worker()
    end = None
    if snapshot_id:
        parts = str(snapshot_id).split("-")
        if len(parts) != 5 or parts[0] != "gmd":
            raise GovernedMarketDatasetError("snapshot_id_unknown", f"{snapshot_id!r} is not a governed market snapshot id")
        end = "-".join(parts[1:4])
    boundary = build_market_boundary(root, policy=policy, now=now, end_date=end, env=env)
    if snapshot_id and boundary.snapshot_id != snapshot_id:
        raise GovernedMarketDatasetError("snapshot_not_reproducible", f"{snapshot_id} now derives as {boundary.snapshot_id}")
    chosen = None
    if symbols is not None:
        known = set(boundary.symbols) | set(boundary.exclusions)
        chosen, missing = [], []
        for requested in symbols:
            text = str(requested).strip()
            forms = [text] + [f"{text.split('.')[0]}.{s}" for s in ("TW", "TWSE", "TWO", "TPEX")]
            match = next((f for f in forms if f in known), None)
            (chosen if match else missing).append(match or text)
        if missing:
            raise GovernedMarketDatasetError("symbols_not_in_store", ", ".join(missing[:5]))
    return dataset_for(boundary, chosen, root=root, strategy_id=strategy_id, env=env)
