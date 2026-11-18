#!/usr/bin/env python3
"""Delete the raw and normalized run files of ``tw_price_daily`` (default) or of one of the 13 keyed 6A
datasets (``--dataset``) after the keyed or daily bars store is verified.

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 section 4.3, 6A and 7 (step 8).

Dry run by default: the report lists, per write-date partition, each run file, its bytes and either
``eligible`` or the refusal reasons. A real run needs ``--execute`` and ``--report-digest`` equal to the
digest of a freshly computed report, so what is deleted is exactly what the operator read.

A run file is eligible only when ALL of these hold:

1. every trade date (``tw_price_daily``) or partition (keyed dataset) the file holds is verified in the
   store: daily bars manifests verify and equal the backfill report digest (``--backfill-report``); a keyed
   partition re-reads against its manifest and holds every key of the file;
2. the file holds no row without a trade date or key (such a row was never carried into the store). Rows
   without one are COUNTED in the report; a file that holds them is deleted only under the second explicit
   flag ``--delete-files-with-undated-rows``;
3. the readers have been on the store for at least 5 trading days with zero mismatches
   (``--reader-evidence``: ``{"trading_days": n, "mismatches": 0}``);
4. its write-date partition is older than the verification window (``--window-days``, default 30);
5. none of its dates is pinned by a gate proof or paper evidence (``--pinned-dates``);
6. every reader that blocks cleanup in ``KNOWN_RUN_FILE_READERS`` (``tw_price_daily``) or
   ``KNOWN_KEYED_READERS`` (keyed datasets) is switched (otherwise nothing is deleted).

Only ``raw/<source>/<dataset>`` and ``normalized/<dataset>`` of the one chosen dataset are touched.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.backfill_daily_bars import row_fields  # noqa: E402
from services.source_ingestion.daily_bars_store import (  # noqa: E402
    KEYED_DATASETS,
    DailyBarsStore,
    KeyedDatasetStore,
)

REPORT_SCHEMA = "cleanup_market_run_files_report.v2"
DATASET = "tw_price_daily"
MIN_READER_DAYS = 5
DEFAULT_WINDOW_DAYS = 30


# Every file under services/ and scripts/ that opens raw or normalized ``tw_price_daily`` run files. While an
# entry that ``blocks_cleanup`` is not ``switched`` no run file may be deleted: that reader would stop working.
# ``switched`` means: with ``PANTHEON_DAILY_PRICE_READER=daily_bars`` the reader reads daily bars ONLY (a part
# that still opens run files keeps its own entry unswitched, e.g. the survivorship attestation). The old read
# stays in the file for rollback, so ``marker`` (the old read) is present either way and ``switched_marker`` (the
# new read) is present exactly when ``switched``. ``test_known_readers_list_matches_the_code`` fails when the list
# and the code disagree and ``test_every_run_file_reader_in_the_code_is_listed`` when a file is missing here.
# ``kind`` "tool": maintenance of the run files themselves; nothing left to read after a cleanup.
KNOWN_RUN_FILE_READERS: tuple[dict[str, Any], ...] = (
    {"file": "services/source_ingestion/intraday_reconcile.py", "part": "OfficialDaily.from_snapshot",
     "reads": "official daily close/volume; daily bars mode derives available_time (14:30 +08:00) and the row "
              "provenance (manifest, row hash) instead of file, line, line sha256",
     "marker": '"normalized" / "tw_price_daily"', "switched_marker": "_from_daily_bars", "switched": True},
    {"file": "services/source_ingestion/connectors/collector_bundle.py", "part": "stored_instrument_authority",
     "reads": "venue qualified symbols with stored daily bars, to resolve social symbols",
     "marker": "SOCIAL_SYMBOL_AUTHORITY_DATASET", "switched_marker": "_daily_bars_symbols", "switched": True},
    {"file": "services/research/alpha_templates/evaluator.py", "part": "load_tw_price_daily_snapshot",
     "reads": "daily OHLCV of the governed snapshot", "marker": "_read_snapshot_rows",
     "switched_marker": "_read_daily_bars_rows", "switched": True},
    {"file": "services/source_ingestion/market_data_storage.py", "part": "measure_stored_history_depth",
     "reads": "depth of the daily price dataset (also the writer of the run files)",
     "marker": "_iter_normalized_rows(path)", "switched_marker": "_reader.list_symbols()", "switched": True},
    {"file": "services/source_ingestion/candidate_pool_market_feed.py", "part": "bars and sessions",
     "reads": "bars and sessions of the newest dates", "marker": "def _partitions(",
     "switched_marker": "_read_bars_from_daily_bars", "switched": True},
    {"file": "services/source_ingestion/candidate_pool_market_feed.py", "part": "survivorship attestation (G2)",
     "reads": "all-market capture rows (collector.source_id, upstream_endpoint, trading_status): in daily bars mode "
              "the status column, the status only files and the lineage entry of each run (design 5.2a)",
     "marker": "def _read_bars(", "switched_marker": "_read_captures_from_daily_bars", "switched": True},
    {"file": "services/source_ingestion/snapshot_universe.py", "part": "_partition_dates (seed)",
     "reads": "raw/<source>/tw_price_daily run files to seed a latest market snapshot. With the daily bars setting "
              "the newest 60 closes come from daily bars (_seed_from_daily_bars) but the run file read stays as the "
              "fallback (fewer than 60 stored dates, fewer closes than needed, unreadable store), so it is NOT "
              "switched: it stays unswitched until the default reads daily bars and the fallback is removed",
     "marker": "DAILY_PRICE_DATASET", "switched_marker": "", "switched": False},
    {"file": "services/source_ingestion/pin_source.py", "part": "_run_file_dates",
     "reads": "run file NAMES of tw_price_daily partitions to turn pinned run ids into pinned dates: after a "
              "cleanup a run id no longer maps to a date",
     "marker": '"tw_price_daily"', "switched_marker": "", "switched": False},
    {"file": "scripts/backfill_daily_bars.py", "part": "backfill source", "kind": "tool", "blocks_cleanup": False,
     "reads": "the run files are the backfill source; nothing to backfill after a cleanup",
     "marker": "normalized", "switched_marker": "", "switched": False},
    {"file": "scripts/cleanup_market_run_files.py", "part": "this tool", "kind": "tool", "blocks_cleanup": False,
     "reads": "lists the files it deletes", "marker": "normalized", "switched_marker": "", "switched": False},
    {"file": "scripts/delete_expired_warrants.py", "part": "one-off warrant delete", "kind": "tool",
     "blocks_cleanup": False, "reads": "rewrites run files; nothing to rewrite after a cleanup",
     "marker": "_store_files", "switched_marker": "", "switched": False},
)

# Readers of the 13 keyed datasets (6A). Each opens ``normalized/<dataset>`` through ``normalized_dataset_base``,
# which resolves to the keyed store when ``PANTHEON_KEYED_DATASET_READER=keyed``; they serve every dataset.
KNOWN_KEYED_READERS: tuple[dict[str, Any], ...] = (
    {"file": "services/research/regime/features.py", "part": "governed observations",
     "marker": "normalized_dataset_base(root, dataset)", "switched": True},
    {"file": "services/source_ingestion/candidate_pool_market_feed.py", "part": "feature datasets (_partitions)",
     "marker": "normalized_dataset_base(root, _slug(dataset))", "switched": True},
    {"file": "services/execution/lean_runtime/instrument_candidates.py", "part": "taifex_ssf_contracts rows",
     "marker": "normalized_dataset_base(root, NORMALIZED_DATASET_PATH", "switched": True},
)

# Files that look like readers to the scan below and are not: reason named.
SCAN_FALSE_POSITIVES: dict[str, str] = {
    "services/training-session/source_dataset_authority.py": "reads storage refs of run records, not run files",
    "services/source_ingestion/controller_worker.py": "status string 'normalized', not a path",
    "services/source_ingestion/finmind_broker_backfill.py": "documents where rows are written; reads none",
    "services/source_ingestion/intraday_store.py": "tw_price_1m store; never reads tw_price_daily",
    "services/source_ingestion/runtime.py": "status string 'normalized', not a path",
    "services/source_ingestion/scheduler.py": "compacts receipt storage_refs (raw_refs/normalized_refs lists); opens no run file",
    "scripts/check_market_data_budget.py": "measures budgets from the writer, opens no run file",
}

_SCAN_NAME = re.compile(r"tw_price_daily|DAILY_BAR_DATASET|DAILY_PRICE_DATASET|daily_bar_dataset|SOCIAL_SYMBOL_AUTHORITY_DATASET")
_SCAN_PATH = re.compile(r"""["']normalized["']|normalized/|["']raw["']|\braw/""")
_SCAN_OPEN = re.compile(r"\b(glob|rglob|iterdir|listdir|scandir|walk|open|read_text|read_bytes|_iter_normalized_rows|normalized_dataset_base)\(")


def looks_like_run_file_reader(text: str) -> bool:
    """True when a source text names the daily price dataset, builds a raw/normalized path and lists or opens files."""
    return bool(_SCAN_NAME.search(text) and _SCAN_PATH.search(text) and _SCAN_OPEN.search(text))


def scan_run_file_readers(repo_root: str | Path) -> list[str]:
    """Repository relative paths of every non-test source under services/ and scripts/ that looks like a
    reader of ``tw_price_daily`` run files (minus the named false positives)."""
    base = Path(repo_root)
    found: list[str] = []
    for top in ("services", "scripts"):
        for path in sorted((base / top).rglob("*.py")):
            parts = path.relative_to(base).parts
            if "tests" in parts or "__pycache__" in parts or path.name.startswith("test_") or path.name == "conftest.py":
                continue
            rel = path.relative_to(base).as_posix()
            if rel in SCAN_FALSE_POSITIVES:
                continue
            if looks_like_run_file_reader(path.read_text(encoding="utf-8", errors="ignore")):
                found.append(rel)
    return found


class CleanupRefused(RuntimeError):
    """The real run was refused; the message is the readable reason."""


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def run_files(root: Path, dataset: str = DATASET) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    norm = root / "normalized" / dataset
    if norm.is_dir():
        out += [("normalized", p) for p in sorted(norm.glob("date=*/*.jsonl*")) if p.suffix in (".jsonl", ".gz")]
    raw = root / "raw"
    if raw.is_dir():
        out += [("raw", p) for p in sorted(raw.glob(f"*/{dataset}/date=*/*.jsonl*")) if p.suffix in (".jsonl", ".gz")]
    return out


def _digest(report: Mapping[str, Any]) -> str:
    body = {k: v for k, v in report.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _file_dates(path: Path, dataset: str = DATASET, store: KeyedDatasetStore | None = None
                ) -> tuple[set[str], int, set[tuple[str, tuple[str, ...]]]]:
    """(dates or partitions held, rows without one, (partition, key) pairs for a keyed dataset)."""
    dates: set[str] = set()
    keys: set[tuple[str, tuple[str, ...]]] = set()
    without = 0
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if store is None:
                trade_date, _symbol = row_fields(row)
            else:
                keyed = store.key_of(row)
                trade_date = keyed[1] if keyed else None
                if keyed:
                    keys.add((keyed[1], keyed[0]))
            if trade_date is None:
                without += 1
            else:
                dates.add(trade_date)
    return dates, without, keys


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
    backfill_report: Mapping[str, Any] | None = None,
    reader_evidence: Mapping[str, Any] | None = None,
    pinned_dates: Sequence[str] = (),
    window_days: int = DEFAULT_WINDOW_DAYS,
    store: DailyBarsStore | None = None,
    known_readers: Sequence[Mapping[str, Any]] | None = None,
    dataset: str = DATASET,
    delete_files_with_undated_rows: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    keyed = dataset != DATASET
    if keyed and dataset not in KEYED_DATASETS:
        raise CleanupRefused(f"dataset {dataset!r} is not tw_price_daily or one of the keyed datasets: "
                             f"{', '.join(sorted(KEYED_DATASETS))}")
    keyed_store = KeyedDatasetStore(root, KEYED_DATASETS[dataset]) if keyed else None
    store = store or (None if keyed else DailyBarsStore(root))
    if known_readers is None:
        known_readers = KNOWN_KEYED_READERS if keyed else KNOWN_RUN_FILE_READERS
    unswitched = sorted({f"{r['file']}::{r.get('part', '')}" if r.get("part") else str(r["file"])
                         for r in known_readers if r.get("blocks_cleanup", True) and not r.get("switched")})
    cutoff = (date.fromisoformat(today) - timedelta(days=window_days)).isoformat()
    pinned = set(pinned_dates)
    gate = _reader_gate(reader_evidence)
    days = (backfill_report or {}).get("days") or {}
    verified: dict[str, str | None] = {}  # trade date / partition -> refusal reason (None = verified)
    keyed_keys: dict[str, set[tuple[str, ...]]] = {}

    def check(trade_date: str) -> str | None:
        if trade_date not in verified:
            if keyed_store is not None:
                result = keyed_store.verify_partition(trade_date)
                keyed_keys[trade_date] = result["keys"]
                verified[trade_date] = (None if result["verified"]
                                        else f"keyed_partition_not_verified:{trade_date}:{result['reason']}")
                return verified[trade_date]
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
    undated = {"files": 0, "rows": 0}
    for kind, path in run_files(root, dataset):
        write_date = path.parent.name[5:]
        size = path.stat().st_size
        trade_dates, without, file_keys = _file_dates(path, dataset, keyed_store)
        reasons: list[str] = [f"unswitched_reader:{f}" for f in unswitched]
        if gate:
            reasons.append(gate)
        if write_date > cutoff:
            reasons.append(f"younger_than_{window_days}_day_window")
        if without:
            undated["files"] += 1
            undated["rows"] += without
            if not delete_files_with_undated_rows:
                reasons.append(f"rows_without_trade_date:{without}")
        if backfill_report is None and not keyed:
            reasons.append("no_backfill_report")
        if not trade_dates:
            reasons.append("no_trade_dates")
        for trade_date in sorted(trade_dates):
            if trade_date in pinned:
                reasons.append(f"pinned:{trade_date}")
            elif backfill_report is not None or keyed:
                why = check(trade_date)
                if why:
                    reasons.append(why)
        if keyed:
            missing = sum(1 for part, key in file_keys if verified.get(part) is None and key not in keyed_keys.get(part, ()))
            if missing:
                reasons.append(f"keys_not_in_keyed_store:{missing}")
        entry = {
            "path": path.relative_to(root).as_posix(), "kind": kind, "bytes": size,
            "trade_dates": sorted(trade_dates), "undated_rows": without, "eligible": not reasons, "refused": reasons,
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
        "schema_version": REPORT_SCHEMA, "dataset": dataset, "root": root.as_posix(), "today": today,
        "window_days": window_days, "cutoff_write_date": cutoff, "reader_gate": gate or "ok",
        "unswitched_readers": unswitched,
        "delete_files_with_undated_rows": bool(delete_files_with_undated_rows),
        "rows_without_trade_date": undated,
        "partitions": dict(sorted(partitions.items())), "totals": totals,
    }
    report["digest"] = _digest(report)
    return report


def _allowed_path(rel: str, dataset: str) -> bool:
    parts = rel.split("/")
    return (len(parts) == 4 and parts[0] == "normalized" and parts[1] == dataset and parts[2].startswith("date=")) or (
        len(parts) == 5 and parts[0] == "raw" and parts[2] == dataset and parts[3].startswith("date="))


def execute(root: str | Path, report: Mapping[str, Any], *, expected_digest: str | None, log: Path | None = None) -> dict[str, Any]:
    """Delete the eligible files of a report. Refuses unless the digest is the report's own."""
    root = Path(root)
    if not expected_digest:
        raise CleanupRefused("a real run needs --report-digest of the dry run report")
    if expected_digest != report.get("digest") or _digest(report) != report.get("digest"):
        raise CleanupRefused("report digest does not match: read the current dry run report and pass its digest")
    dataset = str(report.get("dataset") or DATASET)
    deleted: list[str] = []
    for write_date, part in report["partitions"].items():
        for entry in part["files"]:
            if not entry["eligible"]:
                continue
            if not _allowed_path(entry["path"], dataset):
                raise CleanupRefused(f"{entry['path']} is not a run file of {dataset}; nothing else is touched")
            path = root / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["bytes"]:
                raise CleanupRefused(f"{entry['path']} changed since the report; deleted so far: {len(deleted)}")
            path.unlink()
            deleted.append(entry["path"])
            if log is not None:
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"deleted": entry["path"], "bytes": entry["bytes"], "write_date": write_date,
                                             "undated_rows": entry.get("undated_rows", 0)}) + "\n")
        part_dirs = {(root / e["path"]).parent for e in part["files"]}
        for directory in part_dirs:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    return {"dataset": dataset, "deleted_files": len(deleted), "deleted": deleted}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--dataset", default=DATASET, help="tw_price_daily (default) or one of the 13 keyed datasets")
    ap.add_argument("--backfill-report", help="report JSON written by backfill_daily_bars.py --apply (tw_price_daily)")
    ap.add_argument("--reader-evidence", help='JSON {"trading_days": n, "mismatches": 0}')
    ap.add_argument("--pinned-dates", help="JSON list of dates pinned by gate proofs / paper evidence")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--report", help="write the dry run report here")
    ap.add_argument("--execute", action="store_true", help="delete (default: dry run)")
    ap.add_argument("--report-digest", help="digest of the dry run report that was read")
    ap.add_argument("--delete-files-with-undated-rows", action="store_true",
                    help="also delete a run file that holds rows without a trade date or key (never carried into "
                         "the store; the report lists their counts)")
    ap.add_argument("--log", help="append one line per deleted file here")
    args = ap.parse_args(argv)

    def load(path: str | None, default: Any = None) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else default

    try:
        report = build_report(
            args.root, today=args.today, backfill_report=load(args.backfill_report),
            reader_evidence=load(args.reader_evidence), pinned_dates=load(args.pinned_dates, []),
            window_days=args.window_days, dataset=args.dataset,
            delete_files_with_undated_rows=args.delete_files_with_undated_rows)
    except CleanupRefused as exc:
        print(f"cleanup refused: {exc}", file=sys.stderr)
        return 2
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.execute:
        print(json.dumps({"dry_run": True, "dataset": report["dataset"], "digest": report["digest"],
                          "totals": report["totals"], "reader_gate": report["reader_gate"],
                          "rows_without_trade_date": report["rows_without_trade_date"],
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
