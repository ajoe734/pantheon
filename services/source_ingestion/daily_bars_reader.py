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

from .daily_bars_store import (
    NEW_COLUMNS, STATUS_ONLY_KIND, DailyBarsCorrupt, DailyBarsError, _require_pyarrow, validate_trade_date)

MAX_WINDOW_CLOSES = 504  # design 5.4 E1
MAX_RANGE_DAYS = 800  # about 3 years of trading days
MAX_SYMBOLS = 500
_COLUMNS = ["market", "symbol", "trade_date", "open", "high", "low", "close", "volume", "adjusted", "content_hash"]
# Read only when asked for: traded_value, available_time, trading_status, and the row lineage (which run wrote the bar).
EXTRA_COLUMNS = [*NEW_COLUMNS, "source_id", "ingest_run_id", "lineage_ref"]
PRICE_COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close", "volume"]  # what the price readers use
_VENUE_SUFFIXES = ("TW", "TWSE", "TWO", "TPEX")


def symbol_forms(symbol: str) -> list[str]:
    """Every stored form of one Taiwan code: bare (``2330``) and the venue suffixed ones (``2330.TW``,
    ``2330.TWSE``, ``2330.TWO``, ``2330.TPEX``). A bare code and a canonical symbol of the same code give
    the same list (gap G4). Anything that is not a Taiwan code form is returned as it is."""
    text = str(symbol).strip()
    head, dot, tail = text.rpartition(".")
    bare = head if dot and tail.upper() in _VENUE_SUFFIXES else text
    if "." in bare:
        return [text]
    return [bare] + [f"{bare}.{suffix}" for suffix in _VENUE_SUFFIXES]


def _string_array(pa: Any, values: Sequence[str]) -> Any:
    """Arrow string array built from buffers. ``pa.array`` imports pandas on its first call when it is
    installed (about 0.19 s in a new process); a cold read must not pay that for a list of symbols."""
    import struct

    encoded = [v.encode("utf-8") for v in values]
    offsets = [0]
    for item in encoded:
        offsets.append(offsets[-1] + len(item))
    return pa.StringArray.from_buffers(
        len(encoded), pa.py_buffer(struct.pack(f"<{len(offsets)}i", *offsets)), pa.py_buffer(b"".join(encoded)))


class DailyBarsReadLimit(DailyBarsError):
    """A request exceeds a reader limit; nothing was read."""


class DailyBarsReader:
    def __init__(self, root: str | Path, *, dataset: str = "daily_bars") -> None:
        self.bars_dir = Path(root) / dataset
        self.manifest_dir = self.bars_dir / "_manifest"
        self._manifest_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._dates_cache: tuple[int, list[str]] | None = None
        self._meta_cache: dict[Path, tuple[tuple[int, int], Any, list[Any]]] = {}
        self._presence: dict[str, tuple[tuple[int, int], dict[str, bool]]] = {}
        self._listing: dict[str, tuple[tuple[int, int], frozenset[str]]] = {}
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
        """Open the file once (metadata cached by size and mtime) and pick the row groups whose symbol
        statistics can contain a wanted symbol; the rest are never decoded. A cold read parses each footer
        once: the handle that parsed it is the one that reads."""
        _, pq = _require_pyarrow()
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._meta_cache.get(path)
        handle = None
        if hit is None or hit[0] != sig:
            if len(self._meta_cache) > 4096:
                self._meta_cache.clear()
            handle = pq.ParquetFile(path)
            metadata = handle.metadata
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
        return (handle or pq.ParquetFile(path, metadata=metadata)), picked

    def _read_one_date(self, trade_date: str, wanted: list[str], columns: list[str] | None = None) -> list[dict[str, Any]]:
        pa, _ = _require_pyarrow()
        import pyarrow.compute as pc

        value_set = _string_array(pa, wanted)
        for attempt in (0, 1):  # a writer may swap the day between manifest read and file open: retry once
            try:
                manifest = self._manifest(trade_date)
                out: list[dict[str, Any]] = []
                with_status = bool(columns) and "trading_status" in columns  # then the no-close rows are wanted too
                for entry in manifest["files"]:
                    if entry.get("kind") == STATUS_ONLY_KIND and not with_status:
                        continue  # a security that did not trade has no bar
                    if wanted[0] > entry["max_symbol"] or wanted[-1] < entry["min_symbol"]:
                        continue  # whole file outside the requested symbols
                    path = self.bars_dir / entry["path"]
                    if os.path.getsize(path) != int(entry["bytes"]):
                        raise DailyBarsCorrupt(f"daily bars file {entry['path']} has a different size than its manifest")
                    handle, groups = self._row_groups(path, wanted)
                    if groups:
                        asked = columns or _COLUMNS
                        names = set(handle.metadata.schema.names)
                        table = handle.read_row_groups(  # a v1 partition has no new columns: they read as null
                            groups, columns=[c for c in asked if c in names], use_threads=False)  # the date pool is the parallelism
                        table = table.filter(pc.is_in(table["symbol"], value_set=value_set))  # before any Python objects
                        rows = table.to_pylist()
                        absent = [c for c in asked if c not in names]
                        if absent:
                            for r in rows:
                                for c in absent:
                                    r[c] = None
                        out.extend(rows)
                return out
            except FileNotFoundError:
                self._manifest_cache.pop(trade_date, None)
                if attempt:
                    raise DailyBarsCorrupt(f"daily bars partition {trade_date} lists a file that does not exist")
        return []

    def _read_dates(self, dates: Sequence[str], symbols: Sequence[str], columns: list[str] | None = None) -> list[dict[str, Any]]:
        wanted = sorted(set(symbols))
        if len(dates) <= 1:
            parts = [self._read_one_date(d, wanted, columns) for d in dates]
        else:
            parts = list(self._pool.map(lambda d: self._read_one_date(d, wanted, columns), dates))
        return [row for part in parts for row in part]

    def _present_one_date(self, trade_date: str, wanted: list[str]) -> set[str]:
        """Which of ``wanted`` (sorted) have a bar on the date: symbol column only, row groups pruned."""
        pa, _ = _require_pyarrow()
        import pyarrow.compute as pc

        value_set = _string_array(pa, wanted)
        for attempt in (0, 1):
            try:
                manifest = self._manifest(trade_date)
                found: set[str] = set()
                for entry in manifest["files"]:
                    if entry.get("kind") == STATUS_ONLY_KIND:
                        continue
                    if wanted[0] > entry["max_symbol"] or wanted[-1] < entry["min_symbol"]:
                        continue
                    path = self.bars_dir / entry["path"]
                    handle, groups = self._row_groups(path, wanted)
                    if groups:
                        column = handle.read_row_groups(groups, columns=["symbol"])["symbol"]
                        hits = column.filter(pc.is_in(column, value_set=value_set))
                        found.update(hits.to_pylist())
                return found
            except FileNotFoundError:
                self._manifest_cache.pop(trade_date, None)
                self._presence.pop(trade_date, None)
                if attempt:
                    raise DailyBarsCorrupt(f"daily bars partition {trade_date} lists a file that does not exist")
        return set()

    def availability(self, symbols: str | Sequence[str]) -> dict[str, dict[str, Any]]:
        """``{symbol: {first_date, last_date, count}}`` over the stored trading calendar.

        Resolved through the per-date manifests (symbol range per file) and the symbol column of the
        row groups that can hold the symbol; no other column is decoded and nothing is written. Per
        date presence is cached against the manifest signature, so a repeat call opens no file.
        A symbol with no stored bar reports ``count`` 0 and null dates.
        """
        wanted = sorted(set(self._symbols(symbols)))
        dates = self.trade_dates()
        sigs: dict[str, tuple[int, int]] = {}
        todo: list[str] = []
        for d in dates:
            st = (self.manifest_dir / f"date={d}.json").stat()
            sigs[d] = (st.st_mtime_ns, st.st_size)
            hit = self._presence.get(d)
            if hit is None or hit[0] != sigs[d]:
                self._presence[d] = hit = (sigs[d], {})
            if any(s not in hit[1] for s in wanted):
                todo.append(d)

        def fill(d: str) -> None:
            known = self._presence[d][1]
            missing = [s for s in wanted if s not in known]
            found = self._present_one_date(d, missing)
            for s in missing:
                known[s] = s in found

        if len(todo) <= 1:
            for d in todo:
                fill(d)
        else:
            list(self._pool.map(fill, todo))
        out: dict[str, dict[str, Any]] = {}
        for s in wanted:
            have = [d for d in dates if self._presence[d][1].get(s)]
            out[s] = {"first_date": have[0] if have else None, "last_date": have[-1] if have else None,
                      "count": len(have)}
        if len(self._presence) > 2000:
            self._presence.clear()
        return out

    @staticmethod
    def _symbols(symbols: str | Sequence[str]) -> list[str]:
        items = [symbols] if isinstance(symbols, str) else list(symbols)
        items = [str(s).strip() for s in items if str(s).strip()]
        if not items:
            raise DailyBarsReadLimit("at least one symbol is required")
        if len(set(items)) > MAX_SYMBOLS:
            raise DailyBarsReadLimit(f"{len(set(items))} symbols requested, the limit is {MAX_SYMBOLS} per call")
        return items

    @staticmethod
    def _columns(columns: Sequence[str] | None) -> list[str] | None:
        if columns is None:
            return None
        picked = list(dict.fromkeys(columns))
        unknown = [c for c in picked if c not in _COLUMNS and c not in EXTRA_COLUMNS]
        if unknown or "symbol" not in picked or "trade_date" not in picked:
            raise DailyBarsReadLimit(f"columns must be stored columns and include symbol and trade_date, got {list(columns)!r}")
        return picked

    def read_close_window(
        self, symbol: str | Sequence[str], end_date: str, n: int, *,
        any_form: bool = False, columns: Sequence[str] | None = None,
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Last ``n`` stored trade-date bars with ``trade_date <= end_date``, oldest first.

        A single ``symbol`` returns a list; a sequence returns ``{symbol: [bars]}``. A symbol
        with no bar on a stored trading day (suspension) simply has fewer than ``n`` bars: the
        caller compares ``len`` with ``n``; nothing is filled. ``any_form`` makes a bare code and a
        canonical symbol of the same code find the same rows (see ``read_range``).
        """
        symbols = self._symbols(symbol)
        end = validate_trade_date(end_date)
        if end is None:
            raise DailyBarsReadLimit(f"end_date {end_date!r} is not an ISO date")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_WINDOW_CLOSES:
            raise DailyBarsReadLimit(f"n must be an integer from 1 to {MAX_WINDOW_CLOSES}, got {n!r}")
        eligible = [d for d in self.trade_dates() if d <= end]
        return self._read_shaped(eligible[-n:], symbols, single=isinstance(symbol, str), any_form=any_form,
                                 columns=columns)

    def read_range(
        self, symbols: str | Sequence[str], start: str, end: str, *,
        any_form: bool = False, columns: Sequence[str] | None = None,
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Bars with ``start <= trade_date <= end`` for the symbols, oldest first.

        ``any_form=True``: each requested symbol also finds the bars stored under the other forms of its
        code (``2330`` and ``2330.TW`` / ``.TWSE`` / ``.TWO`` / ``.TPEX``); the result is keyed by the
        symbol as requested and each bar keeps the symbol it is stored under. Two stored forms of one code
        with a bar on the same trade date are ambiguous and fail closed. ``columns`` limits what is decoded.
        """
        wanted = self._symbols(symbols)
        lo, hi = validate_trade_date(start), validate_trade_date(end)
        if lo is None or hi is None:
            raise DailyBarsReadLimit("start and end must be ISO dates")
        if lo > hi:
            raise DailyBarsReadLimit("start is after end")
        dates = [d for d in self.trade_dates() if lo <= d <= hi]
        if len(dates) > MAX_RANGE_DAYS:
            raise DailyBarsReadLimit(f"{len(dates)} trading days requested, the limit is {MAX_RANGE_DAYS} per call")
        return self._read_shaped(dates, wanted, single=isinstance(symbols, str), any_form=any_form,
                                 columns=columns)

    def _read_shaped(self, dates, requested, *, single, any_form, columns):
        cols = self._columns(columns)
        if not any_form:
            return self._shape(self._read_dates(dates, requested, cols), requested, single=single)
        forms = {r: symbol_forms(r) for r in requested}
        rows = self._read_dates(dates, [f for fs in forms.values() for f in fs], cols)
        owners: dict[str, list[str]] = {}
        for r, fs in forms.items():
            for f in fs:
                owners.setdefault(f, []).append(r)
        by_request: dict[str, list[dict[str, Any]]] = {r: [] for r in requested}
        for row in rows:
            for r in owners.get(row["symbol"], ()):
                by_request[r].append(row)
        for r, items in by_request.items():
            seen: dict[str, str] = {}
            for row in items:
                other = seen.setdefault(row["trade_date"], row["symbol"])
                if other != row["symbol"]:
                    raise DailyBarsCorrupt(
                        f"{r}: both {other} and {row['symbol']} have a bar on {row['trade_date']}; the form is ambiguous")
            items.sort(key=lambda x: x["trade_date"])
        return by_request[requested[0]] if single else by_request

    @staticmethod
    def _shape(rows: list[dict[str, Any]], symbols: Sequence[str], *, single: bool):
        by_symbol: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
        for row in rows:
            by_symbol[row["symbol"]].append(row)
        for items in by_symbol.values():
            items.sort(key=lambda r: r["trade_date"])
        return by_symbol[symbols[0]] if single else by_symbol

    # -- survivorship evidence (status and lineage) ----------------------------------------------
    def read_lineage(self, trade_date: str) -> dict[str, Any]:
        """The day's lineage document (calendar evidence once per day, run refs once per run); ``{}`` when the
        day has none. Reads one small file."""
        path = self.bars_dir.parent / "lineage" / f"date={trade_date}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as exc:
            raise DailyBarsCorrupt(f"lineage for {trade_date} is not valid JSON") from exc

    def read_not_traded(self, trade_date: str) -> list[dict[str, Any]]:
        """Rows with ``trading_status = not_traded`` of one day: symbol, source_id, lineage_ref only. Reads the
        status only files of the day, never a price file."""
        _, pq = _require_pyarrow()
        for attempt in (0, 1):
            try:
                out: list[dict[str, Any]] = []
                for entry in self._manifest(trade_date)["files"]:
                    if entry.get("kind") != STATUS_ONLY_KIND:
                        continue
                    path = self.bars_dir / entry["path"]
                    if os.path.getsize(path) != int(entry["bytes"]):
                        raise DailyBarsCorrupt(f"daily bars file {entry['path']} has a different size than its manifest")
                    table = pq.read_table(path, columns=["symbol", "source_id", "lineage_ref", "trading_status"])
                    out.extend(r for r in table.to_pylist() if r["trading_status"] == "not_traded")
                return out
            except FileNotFoundError:
                self._manifest_cache.pop(trade_date, None)
                if attempt:
                    raise DailyBarsCorrupt(f"daily bars partition {trade_date} lists a file that does not exist")
        return []

    # -- symbol listing (reader side, symbol column only) --------------------------------------
    def list_symbols(self, start: str | None = None, end: str | None = None) -> dict[str, tuple[int, str, str]]:
        """``{symbol: (days, first_date, last_date)}`` over the stored trade dates inside ``[start, end]``.

        Resolved through the per-date manifests and the symbol column only; per-date symbol sets are cached
        against the manifest signature, so a repeat call opens no file. Writes nothing.
        """
        lo = validate_trade_date(start) if start else None
        hi = validate_trade_date(end) if end else None
        if (start and lo is None) or (end and hi is None):
            raise DailyBarsReadLimit("start and end must be ISO dates")
        if lo and hi and lo > hi:
            raise DailyBarsReadLimit("start is after end")
        dates = [d for d in self.trade_dates() if (lo is None or d >= lo) and (hi is None or d <= hi)]
        sigs: dict[str, tuple[int, int]] = {}
        todo = []
        for d in dates:
            st = (self.manifest_dir / f"date={d}.json").stat()
            sigs[d] = (st.st_mtime_ns, st.st_size)
            hit = self._listing.get(d)
            if hit is None or hit[0] != sigs[d]:
                todo.append(d)
        if len(self._listing) > 2000:
            self._listing.clear()
            todo = list(dates)

        def load(d: str) -> None:
            _, pq = _require_pyarrow()
            for attempt in (0, 1):
                try:
                    seen: set[str] = set()
                    for entry in self._manifest(d)["files"]:
                        if entry.get("kind") == STATUS_ONLY_KIND:
                            continue
                        seen.update(pq.read_table(self.bars_dir / entry["path"], columns=["symbol"])["symbol"].to_pylist())
                    self._listing[d] = (sigs[d], frozenset(seen))
                    return
                except FileNotFoundError:
                    self._manifest_cache.pop(d, None)
                    if attempt:
                        raise DailyBarsCorrupt(f"daily bars partition {d} lists a file that does not exist")

        if len(todo) <= 1:
            for d in todo:
                load(d)
        else:
            list(self._pool.map(load, todo))
        stats: dict[str, tuple[int, str, str]] = {}
        for d in dates:
            for symbol in self._listing[d][1]:
                days, first, last = stats.get(symbol, (0, d, d))
                stats[symbol] = (days + 1, min(first, d), max(last, d))
        return stats


# -- SOURCE-SNAPSHOT-MIN-CLOSES-WINDOW-20261120: snapshot (newest 60) + daily bars (older) ---------
SNAPSHOT_CLOSES = 60  # the stored current record keeps 60 closes; it is never enlarged (design 5.4 E1)


def join_snapshot_with_daily_bars(
    reader: "DailyBarsReader | None", symbol: str, points: Sequence[tuple[str, float]], min_closes: int
) -> dict[str, Any]:
    """Return exactly ``min_closes`` closes: the record's points plus the older part from daily bars.

    ``points`` are the record's ``(trade_date, close)`` oldest first. Result keys: ``closes`` (list or
    None), ``window_source`` and, when the history is not there, ``reason='history_insufficient'`` with
    ``requested``, ``available``, ``first_available_date`` and ``detail``. Nothing is filled or
    carried forward; the older part is joined on trade date and must be contiguous in the stored
    trading calendar. Writes nothing.
    """
    own = list(points)[-min_closes:]
    need = min_closes - len(own)
    source = {"snapshot": len(own), "daily_bars": 0}
    first_own = own[0][0] if own else None

    def short(available: list[tuple[str, float]], detail: str) -> dict[str, Any]:
        return {
            "closes": None, "reason": "history_insufficient", "requested": min_closes,
            "available": len(available), "first_available_date": available[0][0] if available else None,
            "detail": detail, "window_source": {"snapshot": len(own), "daily_bars": len(available) - len(own)},
        }

    if need <= 0:
        return {"closes": [c for _, c in own], "window_source": source}
    if reader is None or first_own is None:
        return short(own, "daily bars store is not available")
    try:
        calendar = reader.trade_dates()
        older_dates = [d for d in calendar if d < first_own]
        if first_own not in calendar:
            return short(own, f"snapshot start {first_own} is not a stored trading day; the join cannot be verified")
        if not older_dates:
            return short(own, "daily bars hold no trading day before the snapshot window")
        rows = reader.read_close_window(symbol, older_dates[-1], min(need, MAX_WINDOW_CLOSES))
    except DailyBarsError as exc:
        return short(own, f"daily bars read refused: {exc}")
    older = [(r["trade_date"], float(r["close"])) for r in rows]
    joined = older + own
    # contiguity against the trading calendar: the joined dates must be one unbroken calendar slice
    index = {d: i for i, d in enumerate(calendar)}
    positions = [index.get(d) for d, _ in joined]
    if None in positions or any(b - a != 1 for a, b in zip(positions, positions[1:])):
        gap = next((f"{a[0]}..{b[0]}" for a, b, pa, pb in zip(joined, joined[1:], positions, positions[1:])
                    if pa is None or pb is None or pb - pa != 1), "unknown")
        # the longest unbroken tail is what is really available
        tail = [joined[-1]]
        for i in range(len(joined) - 2, -1, -1):
            if positions[i] is not None and positions[i + 1] is not None and positions[i + 1] - positions[i] == 1:
                tail.insert(0, joined[i])
            else:
                break
        return short(tail, f"daily bars and snapshot are not contiguous in the trading calendar at {gap}; not filled")
    if len(joined) < min_closes:
        return short(joined, f"only {len(joined)} contiguous closes are stored")
    source["daily_bars"] = len(older)
    return {"closes": [c for _, c in joined], "window_source": source}
