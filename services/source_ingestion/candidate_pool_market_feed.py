"""Source builder for ``candidate_pool_market.v1`` (scope ``all_governed_tw``).

Reads the governed normalized partitions that the Source writer already stores
(``<root>/normalized/<dataset>/date=*/<run>.jsonl[.gz]``) through the existing
storage reader, and returns the whole-market input a screened pool member needs:
dated daily bars (``available_time``, ``suspended``, ``traded_value``), the
trading calendar and the catalogued point-in-time observations the rule declares.

Nothing is imputed.  A bar without a volume carries no ``suspended`` flag and a
bar without a TWD value carries no ``traded_value``; the screen then excludes the
symbol with its own recorded reason.  A missing dataset or catalogued field is
``CandidatePoolMarketNeedsData`` naming the dataset.

The work is bounded per completed daily bar: results are memoised on the
partition signature (file count, size, newest mtime) so a 60 second tick re-reads
nothing until a new partition lands.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .market_data_storage import _iter_normalized_rows, _slug, daily_price_reader_mode, normalized_dataset_base
from .survivorship_attestation import ALL_MARKET_BUNDLES, HOLIDAY_DATASET, SUSPENSION_DATASET, attest_survivorship

SCHEMA_VERSION = "candidate_pool_market.v1"
SCOPE = "all_governed_tw"
DAILY_BAR_DATASET = "tw_price_daily"
MAX_BARS_PER_SYMBOL = 504

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_CACHE_LIMIT = 8


class CandidatePoolMarketNeedsData(RuntimeError):
    """A governed dataset (or field) the rule declares is not stored."""

    def __init__(self, dataset: str, detail: str) -> None:
        super().__init__(detail)
        self.dataset = dataset
        self.detail = detail


def _partitions(root: Path, dataset: str) -> list[Path]:
    base = normalized_dataset_base(root, _slug(dataset))
    return sorted(base.glob("date=*/*.jsonl*")) if base.is_dir() else []


def _signature(paths: Sequence[Path]) -> tuple[int, int, int]:
    count = size = newest = 0
    for path in paths:
        stat = path.stat()
        count += 1
        size += stat.st_size
        newest = max(newest, stat.st_mtime_ns)
    return count, size, newest


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _canonical(symbol: Any) -> str | None:
    from services.registry.candidate_pool import canonical_symbol

    try:
        return canonical_symbol(str(symbol))
    except Exception:  # noqa: BLE001 - non-equity rows are simply not in the screen universe
        return None


def _bar(meta: Mapping[str, Any], nrow: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    symbol = _canonical(nrow.get("symbol_canonical") or meta.get("symbol_canonical") or nrow.get("symbol"))
    event = str(nrow.get("date") or meta.get("event_time") or "")[:10]
    available = nrow.get("available_time") or meta.get("available_time")
    close = _number(nrow.get("close"))
    if symbol is None or len(event) != 10 or not available or close is None or close <= 0:
        return None
    bar: dict[str, Any] = {"event_time": f"{event}T13:30:00+08:00", "available_time": str(available), "close": close}
    for field in ("open", "high", "low", "volume"):
        value = _number(nrow.get(field))
        if value is not None:
            bar[field] = value
    volume = bar.get("volume")
    if volume is not None:
        bar["suspended"] = volume <= 0
    value = _number(nrow.get("value"))
    if value is None and _number(nrow.get("value_thousands")) is not None:
        value = float(nrow["value_thousands"]) * 1000
    if value is not None:
        bar["traded_value"] = value
    return symbol, bar


_EXCHANGE_VENUE = {"TWSE": "TWSE", "TPEX": "TPEx"}
# The dated all-market official pages the collector price-history spiders read.
_ALL_MARKET_UPSTREAM = {"TWSE": "MI_INDEX", "TPEx": "dailyQuotes"}


def _venue(meta: Mapping[str, Any], nrow: Mapping[str, Any]) -> str:
    return _EXCHANGE_VENUE.get(str(nrow.get("exchange") or meta.get("exchange") or "").upper(), "")


def _capture(meta: Mapping[str, Any], nrow: Mapping[str, Any], *, dataset: str) -> tuple[tuple[str, str], dict[str, Any]] | None:
    """Evidence that a row came from a dated all-market official capture of one venue.

    The identity is the collector bundle that relayed the page (``collector.source_id``) and the upstream
    page it names (``MI_INDEX`` / ``dailyQuotes``); a row from any other source or page is not a capture.
    """
    venue = _venue(meta, nrow)
    collector = meta.get("collector") if isinstance(meta.get("collector"), Mapping) else {}
    endpoint = f"bundle://{collector.get('source_id')}"
    upstream = str(nrow.get("upstream_endpoint") or meta.get("upstream_endpoint") or "")
    session = str(nrow.get("trade_date") or nrow.get("date") or "")[:10]
    if ALL_MARKET_BUNDLES.get(venue) != endpoint or upstream != _ALL_MARKET_UPSTREAM[venue] or len(session) != 10:
        return None
    return (session, venue), {"ref": f"{endpoint}/{dataset}/{session}@{upstream}#{str(collector.get('bundle_sha256') or '')[:12]}"}


def _read_bars(
    paths: Sequence[Path], *, captures_only: bool = False
) -> tuple[dict[str, list[dict[str, Any]]], set[str], dict[tuple[str, str], dict[str, Any]]]:
    """Bars, sessions and the dated all-market captures; a capture also lists the page's non-traded rows.

    A capture whose rows carry the collector's ``trading_status`` keeps the listed securities that have no
    close (suspended / not traded); ``non_traded`` is then the official list for that session and venue.
    An older capture without that field dropped those rows, so it is ``non_traded=None`` (unknown).
    """
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    captures: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for row in _iter_normalized_rows(path):
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
            cap = _capture(meta, nrow, dataset=DAILY_BAR_DATASET)
            if cap is not None:
                held = captures.setdefault(*cap)
                if nrow.get("trading_status") in ("traded", "not_traded"):
                    held.setdefault("non_traded", set())
                    if nrow["trading_status"] == "not_traded":
                        held["non_traded"].add(f"{nrow.get('symbol')}.{cap[0][1]}")
            if captures_only:
                continue
            parsed = _bar(meta, nrow)
            if parsed is None:
                continue
            symbol, bar = parsed
            key = (symbol, bar["event_time"])
            held = versions.get(key)
            if held is None or bar["available_time"] >= held["available_time"]:
                versions[key] = bar
    bars: dict[str, list[dict[str, Any]]] = {}
    for (symbol, _), bar in sorted(versions.items()):
        bars.setdefault(symbol, []).append(bar)
    sessions = {event[:10] for (_, event) in versions}
    return {s: rows[-MAX_BARS_PER_SYMBOL:] for s, rows in bars.items()}, sessions, captures


def _daily_bars_signature(root: Path) -> tuple[int, int, int]:
    manifest_dir = root / "daily_bars" / "_manifest"
    return _signature(sorted(manifest_dir.glob("date=*.json")) if manifest_dir.is_dir() else [])


def _daily_bars_digest(root: Path) -> str:
    lines = []
    for path in sorted((root / "daily_bars" / "_manifest").glob("date=*.json")):
        lines.append(f"{path.name[5:-5]}:{json.loads(path.read_text(encoding='utf-8')).get('content_digest')}")
    return hashlib.sha256("|".join(lines).encode()).hexdigest()


def _read_bars_from_daily_bars(root: Path) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Bars and sessions of the newest ``MAX_BARS_PER_SYMBOL`` stored trade dates through DailyBarsReader,
    paged by symbol chunks (reader cap) inside one date window (reader cap). ``available_time`` is the
    stored instant (UTC; a date only source was pinned to 14:30 +08:00 when it was written) and
    ``traded_value`` is the stored value, absent when the source gave none. A row with no stored available
    time is omitted, as the run file read omits a bar without one: 14:30 is never invented."""
    from .daily_bars_reader import MAX_RANGE_DAYS, MAX_SYMBOLS, PRICE_COLUMNS, DailyBarsReader
    from .daily_bars_store import DailyBarsError

    columns = [*PRICE_COLUMNS, "traded_value", "available_time"]
    reader = DailyBarsReader(root)
    dates = reader.trade_dates()[-min(MAX_BARS_PER_SYMBOL, MAX_RANGE_DAYS):]
    if not dates:
        return {}, set()
    try:
        names = sorted(reader.list_symbols(dates[0], dates[-1]))
        bars: dict[str, list[dict[str, Any]]] = {}
        for i in range(0, len(names), MAX_SYMBOLS):
            for raw_symbol, rows in reader.read_range(names[i:i + MAX_SYMBOLS], dates[0], dates[-1], columns=columns).items():
                symbol = _canonical(raw_symbol)
                if symbol is None:
                    continue
                for row in rows:
                    close = _number(row.get("close"))
                    available = row.get("available_time")
                    if close is None or close <= 0 or available is None:
                        continue
                    bar: dict[str, Any] = {
                        "event_time": f"{row['trade_date']}T13:30:00+08:00",
                        "available_time": available.isoformat().replace("+00:00", "Z"), "close": close,
                    }
                    value = _number(row.get("traded_value"))
                    if value is not None:
                        bar["traded_value"] = value
                    for field in ("open", "high", "low", "volume"):
                        value = _number(row.get(field))
                        if value is not None:
                            bar[field] = value
                    if "volume" in bar:
                        bar["suspended"] = bar["volume"] <= 0
                    bars.setdefault(symbol, []).append(bar)
    except DailyBarsError as exc:
        raise CandidatePoolMarketNeedsData(DAILY_BAR_DATASET, f"daily bars read refused: {exc}") from exc
    for rows in bars.values():
        rows.sort(key=lambda b: b["event_time"])
    sessions = {b["event_time"][:10] for rows in bars.values() for b in rows}
    return {s: rows[-MAX_BARS_PER_SYMBOL:] for s, rows in bars.items()}, sessions


def _read_captures_from_daily_bars(root: Path, sessions: Sequence[str]) -> dict[tuple[str, str], dict[str, Any]]:
    """The all-market captures of the sessions from daily bars, in the shape ``_read_bars`` returns them.

    The capture identity (venue, collector source id, upstream endpoint, bundle sha) is read from the lineage
    entry of each run (once per run), and the listed securities that did not trade from the status only files
    of the day (``trading_status = not_traded``). A capture whose rows carried a status is ``non_traded`` known;
    one without is unknown (``non_traded`` absent), as the run file read reports an older capture."""
    from .daily_bars_reader import DailyBarsReader
    from .daily_bars_store import DailyBarsError
    from .security_listing import bare_symbol

    reader = DailyBarsReader(root)
    captures: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        for session in sessions:
            runs = (reader.read_lineage(session).get("runs") or {})

            def valid(cap: Mapping[str, Any]) -> bool:
                venue = str(cap.get("venue") or "")
                return (venue in _ALL_MARKET_UPSTREAM and ALL_MARKET_BUNDLES.get(venue) == f"bundle://{cap.get('collector_source_id')}"
                        and str(cap.get("upstream_endpoint") or "") == _ALL_MARKET_UPSTREAM[venue])

            for run_id in sorted(runs):
                for cap in runs[run_id].get("captures", ()):
                    if not valid(cap):
                        continue
                    held = captures.setdefault((session, cap["venue"]), {"ref": (
                        f"bundle://{cap['collector_source_id']}/{DAILY_BAR_DATASET}/{session}@{cap['upstream_endpoint']}"
                        f"#{str(cap.get('bundle_sha12') or '')[:12]}")})
                    if cap.get("status_known"):
                        held.setdefault("non_traded", set())
            for row in reader.read_not_traded(session):
                venues = {cap["venue"] for cap in (runs.get(row["lineage_ref"]) or {}).get("captures", ())
                          if valid(cap) and cap.get("row_source_id") == row["source_id"]}
                if len(venues) == 1:  # a row that cannot be tied to one venue's capture is not counted
                    venue = next(iter(venues))
                    captures.setdefault((session, venue), {"ref": ""}).setdefault("non_traded", set()).add(
                        f"{bare_symbol(row['symbol'])}.{venue}")
    except DailyBarsError as exc:
        raise CandidatePoolMarketNeedsData(DAILY_BAR_DATASET, f"daily bars read refused: {exc}") from exc
    return captures


_HOLIDAY_ENDPOINT = {"TWSE": "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?",
                     "TPEx": "https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate?"}
_SUSPENSION_ENDPOINT = {"TWSE": "https://www.twse.com.tw/rwd/zh/afterTrading/TWTAWU?",
                        "TPEx": "https://www.tpex.org.tw/www/zh-tw/bulletin/sprcHis?"}


def _official_rows(paths: Sequence[Path], dataset: str, endpoints: Mapping[str, str]):
    """Normalized rows of a governed dataset whose venue and official endpoint agree."""
    for path in paths:
        for row in _iter_normalized_rows(path):
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
            venue = str(nrow.get("venue") or "")
            endpoint = str(nrow.get("api_endpoint") or meta.get("api_endpoint") or "")
            if nrow.get("dataset") == dataset and venue in endpoints and endpoint.startswith(endpoints[venue]):
                yield venue, endpoint, nrow


def _read_closed_days(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    """Days both venues' official schedule records as closed (dataset ``tw_trading_holidays``).

    A day closed for one venue only is not accounted for: the other venue may have held a session.
    """
    per_day: dict[str, dict[str, str]] = {}
    for venue, endpoint, nrow in _official_rows(paths, HOLIDAY_DATASET, _HOLIDAY_ENDPOINT):
        day = str(nrow.get("date") or "")[:10]
        if nrow.get("closed") is True and len(day) == 10:
            per_day.setdefault(day, {}).setdefault(venue, f"{endpoint}#{HOLIDAY_DATASET}/{day}")
    return {day: {"ref": " + ".join(refs[v] for v in ("TWSE", "TPEx"))}
            for day, refs in per_day.items() if set(refs) == {"TWSE", "TPEx"}}


def _suspension_windows(paths: Sequence[Path]) -> tuple[dict[str, list[tuple[str, str, str]]], dict[str, list[tuple[str, str, str | None]]]]:
    """Per venue: the date ranges an official suspension list was fetched for, and its records.

    The range is the one the request named (TWSE ``startDate``..``endDate``; TPEx ``date=<year>``), so a
    session is only covered by a list that was actually requested for it.
    """
    windows: dict[str, set[tuple[str, str, str]]] = {}
    events: dict[str, list[tuple[str, str, str | None]]] = {}
    for venue, endpoint, nrow in _official_rows(paths, SUSPENSION_DATASET, _SUSPENSION_ENDPOINT):
        if venue == "TWSE":
            start, end = re.search(r"startDate=(\d{8})", endpoint), re.search(r"endDate=(\d{8})", endpoint)
            if not (start and end):
                continue
            first, last = (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" for m in (start, end))
        else:
            year = re.search(r"date=(\d{4})(?:&|$)", endpoint)
            if not year:
                continue
            first, last = f"{year.group(1)}-01-01", f"{year.group(1)}-12-31"
        windows.setdefault(venue, set()).add((first, last, endpoint))
        symbol, suspended = str(nrow.get("symbol") or ""), nrow.get("suspended_date")
        if symbol and suspended:
            events.setdefault(venue, []).append((symbol, str(suspended), str(nrow["resumed_date"]) if nrow.get("resumed_date") else None))
    return {v: sorted(w) for v, w in windows.items()}, events


def _suspension_captures(
    paths: Sequence[Path], window: Sequence[str], price_captures: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], str]]:
    """Sessions whose suspended / non-traded securities are known, per venue, with the unknown ones' cause.

    Known only when (1) the session's all-market page was stored with its no-close rows kept
    (``non_traded`` is not None) and (2) the official suspension list was fetched for a range that
    contains the session.  The list is the page's non-traded rows plus the listed suspension records in force.
    """
    windows, events = _suspension_windows(paths)
    known: dict[tuple[str, str], dict[str, Any]] = {}
    unknown: dict[tuple[str, str], str] = {}
    for session in window:
        for venue in ("TWSE", "TPEx"):
            capture = price_captures.get((session, venue))
            if capture is None:
                continue  # the price gap is reported by the attestation itself
            covering = [w for w in windows.get(venue, []) if w[0] <= session <= w[1]]
            if capture.get("non_traded") is None:
                unknown[(session, venue)] = (
                    f"{venue} all-market page was stored without its no-close rows (pre-intake format)")
            elif not covering:
                unknown[(session, venue)] = f"no official {SUSPENSION_DATASET} list fetched for {venue} covering {session}"
            else:
                listed = {f"{sym}.{venue}" for sym, start, end in events.get(venue, [])
                          if start <= session and (end is None or session < end)}
                known[(session, venue)] = {
                    "ref": f"{capture['ref']} + {covering[0][2]}",
                    "suspended_symbols": sorted(set(capture["non_traded"]) | listed)}
    return known, unknown


def _read_observations(paths: Sequence[Path], dataset: str) -> dict[str, list[dict[str, Any]]]:
    from .feature_catalogue import FeatureCatalogueError, observations_from_row

    out: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        for row in _iter_normalized_rows(path):
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
            if not nrow:
                continue
            try:
                observations = observations_from_row(
                    dataset, nrow, ingest_time=str(meta.get("ingest_time") or meta.get("available_time") or "") or None,
                )
            except FeatureCatalogueError:
                continue
            for obs in observations:
                out.setdefault(f"{obs['field']}\x00{obs['entity']}", []).append(obs)
    return out


def _weekdays_between(after: date, before: date) -> list[date]:
    out, day = [], after + timedelta(days=1)
    while day < before:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def build_candidate_pool_market(
    root: str | Path, inputs: Sequence[Mapping[str, Any]], *, decision_day: str | None = None,
) -> dict[str, Any]:
    """Return ``candidate_pool_market.v1`` for the declared catalogue ``inputs``.

    With ``decision_day`` the calendar is declared to cover that day, which is only
    honest when no completed weekday between the last stored session and the
    decision day is unaccounted for (a weekday with no stored bars could be a
    holiday or a missing ingest and no governed holiday list is read here), so that
    case is ``needs_data`` naming ``tw_price_daily`` rather than a guessed calendar.

    ``inputs`` are the rule's ``{name, dataset, field}`` entries.  Raises
    ``CandidatePoolMarketNeedsData`` naming the dataset when anything is absent.
    """
    from .feature_catalogue import FeatureCatalogueError, get_dataset_spec, get_field

    root = Path(root)
    declared = sorted({(str(i["name"]), str(i["dataset"]), str(i["field"])) for i in inputs})
    from_bars = daily_price_reader_mode() == "daily_bars"
    bar_paths = _partitions(root, DAILY_BAR_DATASET)
    if from_bars:
        if _daily_bars_signature(root)[0] == 0:
            raise CandidatePoolMarketNeedsData(DAILY_BAR_DATASET, "daily_bars has no stored trade date")
    elif not bar_paths:
        raise CandidatePoolMarketNeedsData(DAILY_BAR_DATASET, f"governed dataset {DAILY_BAR_DATASET} has no stored partitions")
    feature_datasets = sorted({d for _, d, _ in declared if d != DAILY_BAR_DATASET and d not in _PRICE_LIKE})
    paths_by_dataset = {d: _partitions(root, d) for d in feature_datasets}
    for dataset in feature_datasets:
        try:
            spec = get_dataset_spec(dataset)
        except FeatureCatalogueError as exc:
            raise CandidatePoolMarketNeedsData(dataset, str(exc)) from exc
        if spec.key_kind == "market":
            raise CandidatePoolMarketNeedsData(
                dataset, f"{dataset} is a market-level chip dataset; the per-symbol feed does not serve it")
        if not paths_by_dataset[dataset]:
            raise CandidatePoolMarketNeedsData(dataset, f"governed dataset {dataset} has no stored partitions")
    for _, dataset, field in declared:
        if dataset in _PRICE_LIKE or dataset == DAILY_BAR_DATASET:
            continue
        try:
            get_field(dataset, field)
        except FeatureCatalogueError as exc:
            raise CandidatePoolMarketNeedsData(dataset, str(exc)) from exc

    suspension_paths = _partitions(root, SUSPENSION_DATASET)
    holiday_paths = _partitions(root, HOLIDAY_DATASET)
    signature = (
        str(root), tuple(declared), decision_day, from_bars,
        _daily_bars_signature(root) if from_bars else None, None if from_bars else _signature(bar_paths),
        _signature(suspension_paths), _signature(holiday_paths),
        tuple((d, _signature(p)) for d, p in sorted(paths_by_dataset.items())),
    )
    with _CACHE_LOCK:
        cached = _CACHE.get(signature)
    if cached is not None:
        return cached

    if from_bars:
        # Bars, sessions and the all-market captures (status and lineage) from daily_bars: no run file is read.
        bars, sessions = _read_bars_from_daily_bars(root)
        price_captures = _read_captures_from_daily_bars(root, sorted(sessions)[-MAX_BARS_PER_SYMBOL:])
    else:
        bars, sessions, price_captures = _read_bars(bar_paths)
    if not bars:
        raise CandidatePoolMarketNeedsData(DAILY_BAR_DATASET, f"{DAILY_BAR_DATASET} holds no usable daily bars")
    symbols: dict[str, dict[str, Any]] = {s: {"dataset": DAILY_BAR_DATASET, "bars": rows} for s, rows in bars.items()}
    for dataset in feature_datasets:
        observed = _read_observations(paths_by_dataset[dataset], dataset)
        fields = [(name, field) for name, d, field in declared if d == dataset]
        for name, field in fields:
            if not any(key.startswith(field + "\x00") for key in observed):
                raise CandidatePoolMarketNeedsData(
                    dataset, f"catalogued field {dataset}.{field} has no stored observations")
        for key, series in observed.items():
            field, _, entity = key.partition("\x00")
            symbol = _canonical(entity)
            if symbol not in symbols:
                continue
            for name, wanted in fields:
                if wanted == field:
                    symbols[symbol].setdefault("features", {}).setdefault(name, []).extend(
                        {"event_time": o["event_time"], "available_time": o["available_time"], "value": o["value"]}
                        for o in series)
    ordered = sorted(sessions)
    coverage = ordered[-1]
    if decision_day is not None:
        try:
            decision = date.fromisoformat(decision_day)
        except ValueError as exc:
            raise CandidatePoolMarketNeedsData(DAILY_BAR_DATASET, f"invalid decision_day {decision_day!r}") from exc
        last = date.fromisoformat(ordered[-1])
        if decision > last:
            gap = _weekdays_between(last, decision)
            if gap:
                raise CandidatePoolMarketNeedsData(
                    DAILY_BAR_DATASET,
                    f"{DAILY_BAR_DATASET} bars end {last}; weekdays {gap[0]}..{gap[-1]} before decision day "
                    f"{decision} have no stored bars and no governed holiday evidence")
            coverage = decision.isoformat()
    digest = hashlib.sha256("|".join(
        f"{p.relative_to(root).as_posix()}:{p.stat().st_size}"
        for p in ([] if from_bars else bar_paths) + [q for d in feature_datasets for q in paths_by_dataset[d]]
    ).encode()).hexdigest()
    bars_ref = (f"daily_bars@sha256:{_daily_bars_digest(root)}" if from_bars
                else f"normalized/{DAILY_BAR_DATASET}@sha256:{digest}")
    window = ordered[-MAX_BARS_PER_SYMBOL:]  # the sessions the carried bars span
    suspension_known, suspension_unknown = _suspension_captures(suspension_paths, window, price_captures)
    attestation = attest_survivorship(window, price_captures, suspension_known,
                                      _read_closed_days(holiday_paths), suspension_unknown=suspension_unknown)
    market = {
        "schema_version": SCHEMA_VERSION,
        "scope": SCOPE,
        "source_refs": [bars_ref] + [f"normalized/{d}" for d in feature_datasets],
        "trading_sessions": ordered,
        "calendar_coverage_through": coverage,
        "symbols": symbols,
        "includes_delisted": attestation["includes_delisted"],
        "includes_suspended": attestation["includes_suspended"],
        "survivorship_attestation": attestation,
    }
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[signature] = market
    return market


# Declared inputs that are derived from the daily bars themselves, never read as features.
_PRICE_LIKE: frozenset[str] = frozenset()


def _init_price_like() -> None:
    global _PRICE_LIKE
    from services.registry.factor_rule import INTRADAY_PRICE_VOLUME_DATASETS, PRICE_VOLUME_DATASETS

    _PRICE_LIKE = frozenset(PRICE_VOLUME_DATASETS - INTRADAY_PRICE_VOLUME_DATASETS)


_init_price_like()
