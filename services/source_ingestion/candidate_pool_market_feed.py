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
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .market_data_storage import _iter_normalized_rows, _slug
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
    base = root / "normalized" / _slug(dataset)
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


def _capture(meta: Mapping[str, Any], nrow: Mapping[str, Any], *, dataset: str) -> tuple[tuple[str, str], dict[str, Any]] | None:
    """Evidence that a row came from a dated all-market official capture of one venue."""
    venue = str(meta.get("venue") or nrow.get("venue") or "")
    endpoint = str(meta.get("api_endpoint") or nrow.get("api_endpoint") or "")
    session = str(nrow.get("date") or meta.get("event_time") or "")[:10]
    if ALL_MARKET_BUNDLES.get(venue) != endpoint or len(session) != 10:
        return None
    source_dataset = str(meta.get("source_dataset") or nrow.get("source_dataset") or "")
    return (session, venue), {"ref": f"{endpoint}/{dataset}/{session}@{source_dataset}"}


def _read_bars(paths: Sequence[Path]) -> tuple[dict[str, list[dict[str, Any]]], set[str], dict[tuple[str, str], dict[str, Any]]]:
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    captures: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for row in _iter_normalized_rows(path):
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
            cap = _capture(meta, nrow, dataset=DAILY_BAR_DATASET)
            if cap is not None:
                captures.setdefault(*cap)
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


def _read_closed_days(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    """Official exchange closed days (dataset ``tw_trading_holidays``); none exist today."""
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in _iter_normalized_rows(path):
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
            day = str(nrow.get("date") or "")[:10]
            endpoint = str(meta.get("api_endpoint") or nrow.get("api_endpoint") or "")
            if nrow.get("closed") is True and len(day) == 10 and endpoint.startswith("https://www.twse.com.tw/"):
                out.setdefault(day, {"ref": f"{endpoint}/{HOLIDAY_DATASET}/{day}"})
    return out


def _read_suspension_captures(paths: Sequence[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    """Per-session suspended-security captures (dataset ``tw_trading_suspension``); none exist today."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for row in _iter_normalized_rows(path):
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
            if nrow.get("capture") != "all_market" or not isinstance(nrow.get("suspended_symbols"), list):
                continue
            cap = _capture(meta, nrow, dataset=SUSPENSION_DATASET)
            if cap is not None:
                out.setdefault(*cap)
    return out


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
    bar_paths = _partitions(root, DAILY_BAR_DATASET)
    if not bar_paths:
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
        str(root), tuple(declared), decision_day, _signature(bar_paths), _signature(suspension_paths), _signature(holiday_paths),
        tuple((d, _signature(p)) for d, p in sorted(paths_by_dataset.items())),
    )
    with _CACHE_LOCK:
        cached = _CACHE.get(signature)
    if cached is not None:
        return cached

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
        for p in bar_paths + [q for d in feature_datasets for q in paths_by_dataset[d]]
    ).encode()).hexdigest()
    window = ordered[-MAX_BARS_PER_SYMBOL:]  # the sessions the carried bars span
    attestation = attest_survivorship(window, price_captures, _read_suspension_captures(suspension_paths),
                                   _read_closed_days(holiday_paths))
    market = {
        "schema_version": SCHEMA_VERSION,
        "scope": SCOPE,
        "source_refs": [f"normalized/{DAILY_BAR_DATASET}@sha256:{digest}"]
        + [f"normalized/{d}" for d in feature_datasets],
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
