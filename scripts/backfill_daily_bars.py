#!/usr/bin/env python3
"""Backfill ``daily_bars`` from the existing normalized ``tw_price_daily`` run files.

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 sections 4.3 and 7 (step 4).

* writes daily bars only, only through ``DailyBarsStore``; never touches the run files;
* the trade date comes from the row (``trade_date``/``as_of_date``/``date``/``event_time``), never
  from the partition name (the write date) or the ingest day; a row without one is counted, not written;
* copies of one (symbol, trade date) collapse to the newest run (partition write date, file mtime,
  file name, then line order);
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
    DailyBarsBudgetExceeded,
    DailyBarsStore,
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


def collect_day(trade_date: str, files: Sequence[Path], state: ListingState) -> dict[str, Any]:
    """Pass 2 for one trade date: newest copy per symbol, classified. Holds one date in memory."""
    newest: dict[str, Mapping[str, Any]] = {}
    rows = 0
    for path in files:  # oldest first, so a later copy overwrites
        for row in _iter_rows(path):
            day, symbol = row_fields(row)
            if day != trade_date or symbol is None:
                continue
            rows += 1
            newest[symbol] = row
    classes = {"keep_listed": 0, "keep_delisted_share": 0, "review": 0, "delete": 0}
    carried: list[_Rec] = []
    for symbol, row in newest.items():
        kind = _classify(symbol, state)
        classes[kind] += 1
        if kind == "delete":
            continue  # expired warrant (or a code in no listing): not carried
        carried.append(_Rec(
            metadata=effective_metadata(row), source_id=str(row.get("source_id") or ""),
            is_rejected=False))
    return {
        "rows": rows, "unique": len(newest), "duplicates": rows - len(newest),
        "classes": classes, "records": carried,
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
) -> dict[str, Any]:
    root = Path(root)
    _check_listing(state, today)
    store = store or DailyBarsStore(root)
    plans, totals = index_run_files(root)
    swept = store.sweep_interrupted() if apply else {"temp_files": 0, "orphan_parts": 0}
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA, "root": root.as_posix(), "apply": apply,
        "listing_date": state.last_success_date, "source": totals, "swept": swept,
        "days": {}, "mismatches": [],
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
        if apply:
            changed = store.write_records(
                day["records"], ingest_run_id=f"backfill-{trade_date}", default_market="TW")
            entry["written"] = trade_date in changed["days_written"]
            manifest = store.read_manifest(trade_date) or {}
            verify = store.verify_day(trade_date)
            entry["manifest_rows"] = manifest.get("row_count")
            entry["manifest_symbols"] = manifest.get("symbol_count")
            entry["content_digest"] = manifest.get("content_digest")
            entry["manifest_bytes"] = manifest.get("bytes")
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
    args = ap.parse_args(argv)
    try:
        state = ListingState.load(args.listing_state)
        report = backfill(args.root, state, today=args.today, apply=args.apply, only_dates=args.date)
    except (BackfillRefused, BackfillMismatch, DailyBarsBudgetExceeded) as exc:
        print(f"backfill stopped: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
