#!/usr/bin/env python3
"""Delete the raw and normalized run files of ``tw_price_daily`` after daily bars are verified.

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 section 4.3 and 7 (step 8).

Dry run by default: the report lists, per write-date partition, each run file, its bytes and either
``eligible`` or the refusal reasons. A real run needs ``--execute`` and ``--report-digest`` equal to the
digest of a freshly computed report, so what is deleted is exactly what the operator read.

A run file is eligible only when ALL of these hold:

1. every trade date the file holds has a daily bars manifest that verifies (files, sizes, counts, digest)
   and whose content digest equals the one in the backfill report (``--backfill-report``);
2. the file holds no row without a trade date (such a row was never carried into daily bars);
3. the readers have been on daily bars for at least 5 trading days with zero mismatches
   (``--reader-evidence``: ``{"trading_days": n, "mismatches": 0}``);
4. its write-date partition is older than the verification window (``--window-days``, default 30);
5. none of its trade dates is pinned by a gate proof or paper evidence (``--pinned-dates``);
6. every reader in ``KNOWN_RUN_FILE_READERS`` is switched (otherwise nothing is deleted).

Only ``raw/<source>/tw_price_daily`` and ``normalized/tw_price_daily`` are touched; no other dataset.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.backfill_daily_bars import row_fields  # noqa: E402
from services.source_ingestion.daily_bars_store import DailyBarsStore  # noqa: E402

REPORT_SCHEMA = "cleanup_market_run_files_report.v1"
DATASET = "tw_price_daily"
MIN_READER_DAYS = 5
DEFAULT_WINDOW_DAYS = 30


# Readers of normalized ``tw_price_daily`` run files that exist in the repository. While any entry is not
# ``switched`` no run file may be deleted: that reader would stop working. Switching a reader means
# editing it AND setting ``switched`` here (``test_known_readers_list_matches_the_code`` fails when the
# list and the code disagree). ``marker`` is the text the unswitched reader still contains.
KNOWN_RUN_FILE_READERS: tuple[dict[str, Any], ...] = (
    {"file": "services/source_ingestion/intraday_reconcile.py", "lines": "255-290",
     "reads": "OfficialDaily.from_snapshot: rglob normalized/tw_price_daily/*.jsonl for official daily "
              "close/volume with line provenance (path, line, sha256) and available_time",
     "marker": '"normalized" / "tw_price_daily"', "switched": False},
    {"file": "services/source_ingestion/connectors/collector_bundle.py", "lines": "2124-2147",
     "reads": "stored_instrument_authority: symbol_canonical of normalized tw_price_daily rows, to resolve "
              "social symbols",
     "marker": "SOCIAL_SYMBOL_AUTHORITY_DATASET", "switched": False},
)


class CleanupRefused(RuntimeError):
    """The real run was refused; the message is the readable reason."""


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def run_files(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    norm = root / "normalized" / DATASET
    if norm.is_dir():
        out += [("normalized", p) for p in sorted(norm.glob("date=*/*.jsonl*")) if p.suffix in (".jsonl", ".gz")]
    raw = root / "raw"
    if raw.is_dir():
        out += [("raw", p) for p in sorted(raw.glob(f"*/{DATASET}/date=*/*.jsonl*")) if p.suffix in (".jsonl", ".gz")]
    return out


def _digest(report: Mapping[str, Any]) -> str:
    body = {k: v for k, v in report.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _file_dates(path: Path) -> tuple[set[str], int]:
    dates: set[str] = set()
    without = 0
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            trade_date, _symbol = row_fields(json.loads(line))
            if trade_date is None:
                without += 1
            else:
                dates.add(trade_date)
    return dates, without


def _reader_gate(evidence: Mapping[str, Any] | None) -> str | None:
    if not evidence:
        return "no_reader_evidence"
    if int(evidence.get("mismatches", 1)) != 0:
        return "reader_mismatches"
    if int(evidence.get("trading_days", 0)) < MIN_READER_DAYS:
        return "readers_on_daily_bars_under_5_trading_days"
    return None


def build_report(
    root: str | Path,
    *,
    today: str,
    backfill_report: Mapping[str, Any] | None,
    reader_evidence: Mapping[str, Any] | None = None,
    pinned_dates: Sequence[str] = (),
    window_days: int = DEFAULT_WINDOW_DAYS,
    store: DailyBarsStore | None = None,
    known_readers: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    root = Path(root)
    store = store or DailyBarsStore(root)
    readers = KNOWN_RUN_FILE_READERS if known_readers is None else known_readers
    unswitched = [str(r["file"]) for r in readers if not r.get("switched")]
    cutoff = (date.fromisoformat(today) - timedelta(days=window_days)).isoformat()
    pinned = set(pinned_dates)
    gate = _reader_gate(reader_evidence)
    days = (backfill_report or {}).get("days") or {}
    verified: dict[str, str | None] = {}  # trade date -> refusal reason (None = verified)

    def check(trade_date: str) -> str | None:
        if trade_date not in verified:
            claimed = (days.get(trade_date) or {}).get("content_digest")
            result = store.verify_day(trade_date)
            if not result["verified"]:
                verified[trade_date] = f"daily_bars_not_verified:{trade_date}:{result['reason']}"
            elif not claimed or claimed != result["content_digest"]:
                verified[trade_date] = f"backfill_report_digest_differs:{trade_date}"
            else:
                verified[trade_date] = None
        return verified[trade_date]

    partitions: dict[str, dict[str, Any]] = {}
    totals = {"files": 0, "bytes": 0, "eligible_files": 0, "eligible_bytes": 0}
    for kind, path in run_files(root):
        write_date = path.parent.name[5:]
        size = path.stat().st_size
        trade_dates, without = _file_dates(path)
        reasons: list[str] = [f"unswitched_reader:{f}" for f in unswitched]
        if gate:
            reasons.append(gate)
        if write_date > cutoff:
            reasons.append(f"younger_than_{window_days}_day_window")
        if without:
            reasons.append(f"rows_without_trade_date:{without}")
        if backfill_report is None:
            reasons.append("no_backfill_report")
        if not trade_dates:
            reasons.append("no_trade_dates")
        for trade_date in sorted(trade_dates):
            if trade_date in pinned:
                reasons.append(f"pinned:{trade_date}")
            elif backfill_report is not None:
                why = check(trade_date)
                if why:
                    reasons.append(why)
        entry = {
            "path": path.relative_to(root).as_posix(), "kind": kind, "bytes": size,
            "trade_dates": sorted(trade_dates), "eligible": not reasons, "refused": reasons,
        }
        partitions.setdefault(write_date, {"files": [], "bytes": 0, "eligible_bytes": 0})
        part = partitions[write_date]
        part["files"].append(entry)
        part["bytes"] += size
        totals["files"] += 1
        totals["bytes"] += size
        if not reasons:
            part["eligible_bytes"] += size
            totals["eligible_files"] += 1
            totals["eligible_bytes"] += size
    report = {
        "schema_version": REPORT_SCHEMA, "root": root.as_posix(), "today": today,
        "window_days": window_days, "cutoff_write_date": cutoff, "reader_gate": gate or "ok",
        "unswitched_readers": unswitched,
        "partitions": dict(sorted(partitions.items())), "totals": totals,
    }
    report["digest"] = _digest(report)
    return report


def execute(root: str | Path, report: Mapping[str, Any], *, expected_digest: str | None, log: Path | None = None) -> dict[str, Any]:
    """Delete the eligible files of a report. Refuses unless the digest is the report's own."""
    root = Path(root)
    if not expected_digest:
        raise CleanupRefused("a real run needs --report-digest of the dry run report")
    if expected_digest != report.get("digest") or _digest(report) != report.get("digest"):
        raise CleanupRefused("report digest does not match: read the current dry run report and pass its digest")
    deleted: list[str] = []
    for write_date, part in report["partitions"].items():
        for entry in part["files"]:
            if not entry["eligible"]:
                continue
            path = root / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["bytes"]:
                raise CleanupRefused(f"{entry['path']} changed since the report; deleted so far: {len(deleted)}")
            path.unlink()
            deleted.append(entry["path"])
            if log is not None:
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"deleted": entry["path"], "bytes": entry["bytes"], "write_date": write_date}) + "\n")
        part_dirs = {(root / e["path"]).parent for e in part["files"]}
        for directory in part_dirs:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    return {"deleted_files": len(deleted), "deleted": deleted}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--backfill-report", help="report JSON written by backfill_daily_bars.py --apply")
    ap.add_argument("--reader-evidence", help='JSON {"trading_days": n, "mismatches": 0}')
    ap.add_argument("--pinned-dates", help="JSON list of trade dates pinned by gate proofs / paper evidence")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--report", help="write the dry run report here")
    ap.add_argument("--execute", action="store_true", help="delete (default: dry run)")
    ap.add_argument("--report-digest", help="digest of the dry run report that was read")
    ap.add_argument("--log", help="append one line per deleted file here")
    args = ap.parse_args(argv)

    def load(path: str | None, default: Any = None) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else default

    report = build_report(
        args.root, today=args.today, backfill_report=load(args.backfill_report),
        reader_evidence=load(args.reader_evidence), pinned_dates=load(args.pinned_dates, []),
        window_days=args.window_days)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.execute:
        print(json.dumps({"dry_run": True, "digest": report["digest"], "totals": report["totals"],
                          "reader_gate": report["reader_gate"],
                          "unswitched_readers": report["unswitched_readers"]}, sort_keys=True))
        return 0
    try:
        result = execute(args.root, report, expected_digest=args.report_digest,
                         log=Path(args.log) if args.log else None)
    except CleanupRefused as exc:
        print(f"cleanup refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
