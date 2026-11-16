"""The only bounded window readers over the daily bars store.

``read_close_window(symbol, end_date, n)`` and ``read_range(symbols, start, end)`` resolve
files through the per-date manifest only (a reader sees the old or the new complete set of a
day), open only the partitions inside the window, and push the symbol filter into Parquet
row groups. Limits are enforced before any file is opened.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from .daily_bars_store import DailyBarsCorrupt, DailyBarsError, _require_pyarrow, validate_trade_date

MAX_WINDOW_CLOSES = 504  # design 5.4 E1
MAX_RANGE_DAYS = 800  # about 3 years of trading days
MAX_SYMBOLS = 500
_COLUMNS = ["market", "symbol", "trade_date", "open", "high", "low", "close", "volume", "adjusted", "content_hash"]


class DailyBarsReadLimit(DailyBarsError):
    """A request exceeds a reader limit; nothing was read."""


class DailyBarsReader:
    def __init__(self, root: str | Path, *, dataset: str = "daily_bars") -> None:
        self.bars_dir = Path(root) / dataset
        self.manifest_dir = self.bars_dir / "_manifest"
        self._manifest_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._dates_cache: tuple[int, list[str]] | None = None
        self._meta_cache: dict[Path, tuple[tuple[int, int], Any, list[Any]]] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="daily-bars-read")

    # -- manifests ---------------------------------------------------------------------------
    def trade_dates(self) -> list[str]:
        """Sorted trade dates that have a manifest (the trading calendar of stored days)."""
        try:
            mtime = self.manifest_dir.stat().st_mtime_ns
        except FileNotFoundError:
            return []
        if self._dates_cache is None or self._dates_cache[0] != mtime:
            dates = sorted(
                p.name[5:-5] for p in self.manifest_dir.iterdir()
                if p.name.startswith("date=") and p.name.endswith(".json"))
            self._dates_cache = (mtime, dates)
        return self._dates_cache[1]

    def _manifest(self, trade_date: str) -> dict[str, Any]:
        path = self.manifest_dir / f"date={trade_date}.json"
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._manifest_cache.get(trade_date)
        if hit and hit[0] == sig:
            return hit[1]
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DailyBarsCorrupt(f"manifest for {trade_date} is not valid JSON") from exc
        self._manifest_cache[trade_date] = (sig, manifest)
        return manifest

    # -- reads -------------------------------------------------------------------------------
    def _row_groups(self, path: Path, wanted: Sequence[str]) -> tuple[Any, list[int]]:
        """Open the file (metadata cached by size and mtime) and pick the row groups whose symbol
        statistics can contain a wanted symbol; the rest are never decoded."""
        _, pq = _require_pyarrow()
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._meta_cache.get(path)
        if hit is None or hit[0] != sig:
            if len(self._meta_cache) > 4096:
                self._meta_cache.clear()
            metadata = pq.read_metadata(path)
            col = metadata.schema.names.index("symbol")
            bounds = []
            for i in range(metadata.num_row_groups):
                stats = metadata.row_group(i).column(col).statistics
                bounds.append((stats.min, stats.max) if stats is not None and stats.has_min_max else None)
            hit = (sig, metadata, bounds)
            self._meta_cache[path] = hit
        _, metadata, bounds = hit
        picked = [
            i for i, b in enumerate(bounds)
            if b is None or any(b[0] <= w <= b[1] for w in wanted)
        ]
        return pq.ParquetFile(path, metadata=metadata), picked

    def _read_one_date(self, trade_date: str, wanted: list[str]) -> list[dict[str, Any]]:
        pa, _ = _require_pyarrow()
        import pyarrow.compute as pc

        value_set = pa.array(wanted, type=pa.string())
        for attempt in (0, 1):  # a writer may swap the day between manifest read and file open: retry once
            try:
                manifest = self._manifest(trade_date)
                out: list[dict[str, Any]] = []
                for entry in manifest["files"]:
                    if wanted[0] > entry["max_symbol"] or wanted[-1] < entry["min_symbol"]:
                        continue  # whole file outside the requested symbols
                    path = self.bars_dir / entry["path"]
                    if os.path.getsize(path) != int(entry["bytes"]):
                        raise DailyBarsCorrupt(f"daily bars file {entry['path']} has a different size than its manifest")
                    handle, groups = self._row_groups(path, wanted)
                    if groups:
                        table = handle.read_row_groups(groups, columns=_COLUMNS)
                        table = table.filter(pc.is_in(table["symbol"], value_set=value_set))  # before any Python objects
                        out.extend(table.to_pylist())
                return out
            except FileNotFoundError:
                self._manifest_cache.pop(trade_date, None)
                if attempt:
                    raise DailyBarsCorrupt(f"daily bars partition {trade_date} lists a file that does not exist")
        return []

    def _read_dates(self, dates: Sequence[str], symbols: Sequence[str]) -> list[dict[str, Any]]:
        wanted = sorted(set(symbols))
        if len(dates) <= 1:
            parts = [self._read_one_date(d, wanted) for d in dates]
        else:
            parts = list(self._pool.map(lambda d: self._read_one_date(d, wanted), dates))
        return [row for part in parts for row in part]

    @staticmethod
    def _symbols(symbols: str | Sequence[str]) -> list[str]:
        items = [symbols] if isinstance(symbols, str) else list(symbols)
        items = [str(s).strip() for s in items if str(s).strip()]
        if not items:
            raise DailyBarsReadLimit("at least one symbol is required")
        if len(set(items)) > MAX_SYMBOLS:
            raise DailyBarsReadLimit(f"{len(set(items))} symbols requested, the limit is {MAX_SYMBOLS} per call")
        return items

    def read_close_window(
        self, symbol: str | Sequence[str], end_date: str, n: int
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Last ``n`` stored trade-date bars with ``trade_date <= end_date``, oldest first.

        A single ``symbol`` returns a list; a sequence returns ``{symbol: [bars]}``. A symbol
        with no bar on a stored trading day (suspension) simply has fewer than ``n`` bars: the
        caller compares ``len`` with ``n``; nothing is filled.
        """
        symbols = self._symbols(symbol)
        end = validate_trade_date(end_date)
        if end is None:
            raise DailyBarsReadLimit(f"end_date {end_date!r} is not an ISO date")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_WINDOW_CLOSES:
            raise DailyBarsReadLimit(f"n must be an integer from 1 to {MAX_WINDOW_CLOSES}, got {n!r}")
        eligible = [d for d in self.trade_dates() if d <= end]
        rows = self._read_dates(eligible[-n:], symbols)
        return self._shape(rows, symbols, single=isinstance(symbol, str))

    def read_range(
        self, symbols: str | Sequence[str], start: str, end: str
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Bars with ``start <= trade_date <= end`` for the symbols, oldest first."""
        wanted = self._symbols(symbols)
        lo, hi = validate_trade_date(start), validate_trade_date(end)
        if lo is None or hi is None:
            raise DailyBarsReadLimit("start and end must be ISO dates")
        if lo > hi:
            raise DailyBarsReadLimit("start is after end")
        dates = [d for d in self.trade_dates() if lo <= d <= hi]
        if len(dates) > MAX_RANGE_DAYS:
            raise DailyBarsReadLimit(f"{len(dates)} trading days requested, the limit is {MAX_RANGE_DAYS} per call")
        rows = self._read_dates(dates, wanted)
        return self._shape(rows, wanted, single=isinstance(symbols, str))

    @staticmethod
    def _shape(rows: list[dict[str, Any]], symbols: Sequence[str], *, single: bool):
        by_symbol: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
        for row in rows:
            by_symbol[row["symbol"]].append(row)
        for items in by_symbol.values():
            items.sort(key=lambda r: r["trade_date"])
        return by_symbol[symbols[0]] if single else by_symbol
