#!/usr/bin/env python3
"""Backfill ``daily_bars`` from the existing normalized ``tw_price_daily`` run files.

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 sections 4.3 and 7 (step 4).

* writes daily bars only, only through ``DailyBarsStore``; never touches the run files;
* the trade date comes from the row (``trade_date``/``as_of_date``/``date``/``event_time``), never
  from the partition name (the write date) or the ingest day; a row without one is counted, not written;
* copies of one (symbol, trade date) collapse by the rule of the evaluator read (design 4.1a item 3) when
  they agree on open, high, low and close: the copy with the largest volume supplies volume and traded
  value (a tie takes the newer run: partition write date, file mtime, file name, then line order) and the
  available time is the latest of all copies. Keys whose copies disagree are counted separately (price
  conflicts: open/high/low/close; volume only) and listed in the report, and a trade date that holds
  one is still NOT published by default (unchanged from the merged task; the volume only case is resolved
  by the rule above when the flag publishes the date).
  ``--newest-run-on-conflict`` is the one flag that publishes such a date with the newest copy;
* the three columns (traded value, available time, trading status) come from the run file line exactly as
  the store extracts them; a day written before them (manifest v1) is upgraded to v2 here and the report
  lists the v1 dates that no run file can upgrade (``upgrade``);
* expired warrants (listing of SOURCE-WARRANT-LISTING-EXPIRY-20261117) are not carried; listed
  warrants and delisted ordinary shares are; an unreadable or stale listing stops the run;
* resumable per trade date: a day whose content digest is unchanged is not rewritten, so a second
  run writes nothing; the residue of a killed run (one trade date at most) is swept at start;
* dry run by default (report only); ``--apply`` writes.

After the writes every date is compared with the deduplicated normalized rows; a mismatch fails the
run and names the dates.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.delete_expired_warrants import classify  # noqa: E402
from services.source_ingestion.daily_bars_store import (  # noqa: E402
    _DATE_KEYS,
    DAILY_BARS_DAY_ALARM_BYTES,
    DAILY_BARS_SCHEMA_VERSION,
    DailyBarsBudgetExceeded,
    DailyBarsStore,
    _available_time,
    _body_of,
    _number,
    _traded_value,
    _volume,
    validate_trade_date,
)
from services.source_ingestion.security_listing import ListingState, bare_symbol  # noqa: E402

REPORT_SCHEMA = "backfill_daily_bars_report.v1"
DATASET = "tw_price_daily"


class BackfillRefused(RuntimeError):
    """The run stopped before writing; the message is the readable reason."""


class BackfillMismatch(RuntimeError):
    """Daily bars of some dates do not equal the deduplicated normalized rows."""

    def __init__(self, dates: Sequence[str]) -> None:
        super().__init__(f"daily bars differ from the deduplicated normalized rows on: {', '.join(dates)}")
        self.dates = list(dates)


@dataclass
class _Rec:
    """Minimal record shape that ``DailyBarsStore.extract_rows`` reads."""

    metadata: Mapping[str, Any]
    source_id: str = ""
    is_rejected: bool = False


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def run_files(root: Path) -> list[Path]:
    """Normalized run files, oldest first: (partition write date, mtime, name). Later wins."""
    base = root / "normalized" / DATASET
    if not base.is_dir():
        return []
    files = [p for p in base.glob("date=*/*.jsonl*") if p.suffix in (".jsonl", ".gz")]
    return sorted(files, key=lambda p: (p.parent.name, p.stat().st_mtime_ns, p.name))


def _iter_rows(path: Path) -> Iterator[Mapping[str, Any]]:
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def effective_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata of a run-file line with ``normalized_row`` completed from the JSON ``body`` the hosted
    snapshot lines also carry (the evaluator reads that form), so both shapes extract the same bar."""
    meta = dict(row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {})
    body = meta.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    merged: dict[str, Any] = dict(body) if isinstance(body, Mapping) else {}
    if isinstance(meta.get("normalized_row"), Mapping):
        merged.update(meta["normalized_row"])
    if merged:
        meta["normalized_row"] = merged
    return meta


def row_fields(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(trade_date, symbol) of one run-file line, by the same rule as ``DailyBarsStore.extract_rows``."""
    meta = effective_metadata(row)
    normalized = meta.get("normalized_row")
    body = normalized if isinstance(normalized, Mapping) else meta
    trade_date = None
    for src in (body, meta):
        for key in _DATE_KEYS:
            if src.get(key) not in (None, ""):
                trade_date = validate_trade_date(src[key])
                break
        if trade_date:
            break
    symbol = body.get("symbol_canonical") or body.get("symbol") or meta.get("symbol_canonical") or meta.get("symbol")
    symbol = str(symbol or "").strip()
    return trade_date, (symbol or None)


@dataclass
class DayPlan:
    files: list[Path] = field(default_factory=list)


def index_run_files(root: Path) -> tuple[dict[str, DayPlan], dict[str, Any]]:
    """Pass 1: which run files hold which trade date, and how many rows carry none (kept in memory:
    dates and paths only)."""
    plans: dict[str, DayPlan] = {}
    totals = {"files": 0, "rows": 0, "rows_without_trade_date": 0, "rows_without_symbol": 0}
    for path in run_files(root):
        totals["files"] += 1
        dates: set[str] = set()
        for row in _iter_rows(path):
            totals["rows"] += 1
            trade_date, symbol = row_fields(row)
            if trade_date is None:
                totals["rows_without_trade_date"] += 1
            elif symbol is None:
                totals["rows_without_symbol"] += 1
            else:
                dates.add(trade_date)
        for trade_date in dates:
            plans.setdefault(trade_date, DayPlan()).files.append(path)
    return plans, totals


def _classify(symbol: str, state: ListingState) -> str:
    return classify(bare_symbol(symbol), state)


CONFLICT_LIST_LIMIT = 500  # keys listed per trade date; the counts are always complete
_PRICE_FIELDS = ("open", "high", "low", "close")


def _ohlcv(row: Mapping[str, Any]) -> tuple[float | None, ...]:
    meta = effective_metadata(row)
    normalized = meta.get("normalized_row")
    body = normalized if isinstance(normalized, Mapping) else meta
    prices = tuple(_number(body.get(k) if body.get(k) is not None else meta.get(k)) for k in _PRICE_FIELDS)
    sources = (body, _body_of(meta), meta)  # the collector names (volume_shares, turnover_twd) count as volume and value
    return (*prices, _volume(sources), _traded_value(sources))


def _avail(row: Mapping[str, Any]):
    meta = effective_metadata(row)
    normalized = meta.get("normalized_row")
    body = normalized if isinstance(normalized, Mapping) else meta
    return _available_time((body, _body_of(meta), meta))


def collect_day(trade_date: str, files: Sequence[Path], state: ListingState) -> dict[str, Any]:
    """Pass 2 for one trade date: one copy per symbol by the evaluator rule, classified. Holds one date in memory."""
    newest: dict[str, Mapping[str, Any]] = {}
    best: dict[str, tuple[Mapping[str, Any], float]] = {}  # copy with the largest volume (later wins a tie)
    latest: dict[str, Any] = {}  # latest available time of all copies
    first_seen: dict[str, tuple[float | None, ...]] = {}
    conflicts: dict[str, str] = {}  # symbol -> "price" | "volume"
    rows = 0
    for path in files:  # oldest first, so a later copy overwrites
        for row in _iter_rows(path):
            day, symbol = row_fields(row)
            if day != trade_date or symbol is None:
                continue
            rows += 1
            values = _ohlcv(row)
            held = first_seen.setdefault(symbol, values)
            if held != values:
                if held[:4] != values[:4]:
                    conflicts[symbol] = "price"
                elif conflicts.get(symbol) != "price":
                    conflicts[symbol] = "volume"
            newest[symbol] = row
            volume = values[4] if values[4] is not None else 0.0
            if symbol not in best or volume >= best[symbol][1]:
                best[symbol] = (row, volume)
            avail = _avail(row)
            if avail is not None and (symbol not in latest or avail > latest[symbol]):
                latest[symbol] = avail
    classes = {"keep_listed": 0, "keep_delisted_share": 0, "review": 0, "delete": 0}
    carried: list[_Rec] = []
    for symbol, row in newest.items():
        kind = _classify(symbol, state)
        classes[kind] += 1
        if kind == "delete":
            continue  # expired warrant (or a code in no listing): not carried
        if conflicts.get(symbol) != "price":
            row = best[symbol][0]  # same prices: the largest volume supplies volume and traded value
        meta = effective_metadata(row)
        if conflicts.get(symbol) != "price" and symbol in latest:
            meta["normalized_row"] = {**(meta.get("normalized_row") or meta), "available_time": latest[symbol].isoformat()}
        carried.append(_Rec(metadata=meta, source_id=str(row.get("source_id") or ""), is_rejected=False))
    conflicts = {k: v for k, v in conflicts.items() if _classify(k, state) != "delete"}
    return {
        "rows": rows, "unique": len(newest), "duplicates": rows - len(newest),
        "classes": classes, "records": carried, "conflicts": conflicts,
    }


def _check_listing(state: ListingState, today: str) -> None:
    status = state.status(today)
    if status != "fresh":
        raise BackfillRefused(
            f"listing is not fresh ({status}, last success {state.last_success_date}): expired warrants "
            "cannot be told apart, nothing was written")


def backfill(
    root: str | Path,
    state: ListingState,
    *,
    today: str,
    apply: bool = False,
    store: DailyBarsStore | None = None,
    only_dates: Sequence[str] | None = None,
    newest_run_on_conflict: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    _check_listing(state, today)
    store = store or DailyBarsStore(root)
    plans, totals = index_run_files(root)
    swept = store.sweep_interrupted() if apply else {"temp_files": 0, "orphan_parts": 0}
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA, "root": root.as_posix(), "apply": apply,
        "listing_date": state.last_success_date, "source": totals, "swept": swept,
        "days": {}, "mismatches": [], "newest_run_on_conflict": newest_run_on_conflict,
        "conflicts": {"price_keys": 0, "volume_only_keys": 0, "blocked_dates": [], "published_with_flag": []},
    }
    class_totals = {"keep_listed": 0, "keep_delisted_share": 0, "review": 0, "delete": 0}
    wanted = set(only_dates) if only_dates else None
    for trade_date in sorted(plans):
        if wanted is not None and trade_date not in wanted:
            continue
        day = collect_day(trade_date, plans[trade_date].files, state)
        for name, n in day["classes"].items():
            class_totals[name] += n
        entry: dict[str, Any] = {
            "rows": day["rows"], "unique": day["unique"], "duplicates": day["duplicates"],
            "classes": day["classes"], "run_files": len(plans[trade_date].files),
        }
        rows, rejected, _evidence = store.extract_rows(
            day["records"], ingest_run_id=f"backfill-{trade_date}", default_market="TW")
        entry["expected_rows"] = len(rows)
        entry["rejected"] = rejected
        found = day["conflicts"]
        price = sorted(k for k, v in found.items() if v == "price")
        volume = sorted(k for k, v in found.items() if v == "volume")
        entry["conflicts"] = {
            "price_keys": len(price), "volume_only_keys": len(volume),
            "price": price[:CONFLICT_LIST_LIMIT], "volume_only": volume[:CONFLICT_LIST_LIMIT],
            "listed_truncated": len(price) > CONFLICT_LIST_LIMIT or len(volume) > CONFLICT_LIST_LIMIT}
        report["conflicts"]["price_keys"] += len(price)
        report["conflicts"]["volume_only_keys"] += len(volume)
        blocked = bool(found) and not newest_run_on_conflict
        entry["published_blocked"] = blocked
        if found and newest_run_on_conflict:
            report["conflicts"]["published_with_flag"].append(trade_date)
        if blocked:
            report["conflicts"]["blocked_dates"].append(trade_date)
        if apply and blocked:
            entry["written"] = False
            entry["verified"] = None  # not published: nothing to verify, and the date stays unpublished
        elif apply:
            changed = store.write_records(
                day["records"], ingest_run_id=f"backfill-{trade_date}", default_market="TW")
            entry["written"] = trade_date in changed["days_written"]
            manifest = store.read_manifest(trade_date) or {}
            verify = store.verify_day(trade_date)
            entry["manifest_rows"] = manifest.get("row_count")
            entry["manifest_symbols"] = manifest.get("symbol_count")
            entry["content_digest"] = manifest.get("content_digest")
            entry["manifest_bytes"] = manifest.get("bytes")
            entry["schema_version"] = manifest.get("schema_version")
            entry["verified"] = bool(
                verify["verified"] and manifest.get("row_count") == len(rows)
                and manifest.get("symbol_count") == len({r["symbol"] for r in rows}))
            if not entry["verified"]:
                report["mismatches"].append(trade_date)
            if int(manifest.get("bytes") or 0) > DAILY_BARS_DAY_ALARM_BYTES:
                raise DailyBarsBudgetExceeded(f"daily bars of {trade_date} exceed the day limit")
        report["days"][trade_date] = entry
    report["classes"] = class_totals
    report["trade_dates"] = len(report["days"])
    v1 = sorted(d for d in store.read_manifest_dates()
                if (store.read_manifest(d) or {}).get("schema_version") != DAILY_BARS_SCHEMA_VERSION)
    report["upgrade"] = {  # v1 days (no traded value, available time, trading status) after this run
        "v1_dates": len(v1), "v1_dates_with_run_files": [d for d in v1 if d in plans],
        "v1_dates_without_run_files": [d for d in v1 if d not in plans],
        "v1_dates_without_run_files_count": sum(1 for d in v1 if d not in plans)}
    if apply:
        report["budget"] = store.enforce_budget()
        if report["mismatches"]:
            raise BackfillMismatch(report["mismatches"])
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="market_data_store root")
    ap.add_argument("--listing-state", required=True)
    ap.add_argument("--today", required=True, help="ISO date used for the listing freshness check")
    ap.add_argument("--apply", action="store_true", help="write daily bars (default: report only)")
    ap.add_argument("--report", help="write the report JSON here")
    ap.add_argument("--date", action="append", help="limit to this trade date (repeatable)")
    ap.add_argument("--newest-run-on-conflict", action="store_true",
                    help="publish a trade date whose copies of a key disagree, with the newest run "
                         "(default: such a date is not published and is listed in the report)")
    args = ap.parse_args(argv)
    try:
        state = ListingState.load(args.listing_state)
        report = backfill(args.root, state, today=args.today, apply=args.apply, only_dates=args.date,
                         newest_run_on_conflict=args.newest_run_on_conflict)
    except (BackfillRefused, BackfillMismatch, DailyBarsBudgetExceeded) as exc:
        print(f"backfill stopped: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    blocked = report["conflicts"]["blocked_dates"]
    if blocked:
        print(f"backfill: {len(blocked)} trade date(s) hold conflicting duplicates and were not published: "
              f"{', '.join(blocked[:10])}{' ...' if len(blocked) > 10 else ''}; see report conflicts", file=sys.stderr)
        return 3 if args.apply else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
