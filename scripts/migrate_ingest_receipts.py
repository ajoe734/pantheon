#!/usr/bin/env python3
"""Rewrite an ingest schedule journal so receipts carry counts and references only.

SOURCE-RECEIPT-REFS-ONLY-20261117-R2 (T2b). Streams ``ingest_schedule.jsonl`` into a sibling
temp file, compacting every receipt row over 16 KB and dropping receipt rows past the
retention window (default 30 days) that no run, watermark, frontier item or lineage pin
still references. Every other row is copied byte for byte, so runs, watermarks and the
frontier are identical before and after.

Crash safe: the journal is only ever replaced by one atomic rename of a complete, fsynced
file; the old file stays readable until then and is kept as ``<journal>.pre-migrate.bak``
(``--no-backup`` removes it). Resumable: progress is checkpointed to
``<journal>.migrate.state.json``; a rerun after a crash continues from the last checkpoint
when the journal is unchanged, otherwise restarts. The writer may keep appending while the
bulk copy runs: the final tail catch-up and the rename happen under the store's own lock.

    python3 scripts/migrate_ingest_receipts.py JOURNAL [--retention-days 30] [--dry-run]
        [--no-backup] [--report report.json] [--measure-replay]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.source_ingestion.process_lock import exclusive_file_lock  # noqa: E402
from services.source_ingestion.scheduler import (  # noqa: E402
    DEFAULT_RECEIPT_RETENTION_DAYS,
    RECEIPT_LINE_BUDGET_BYTES,
    _line_identity,
    journal_backup_path,
    keep_journal_backup,
    discard_created_backup,
    rewrite_journal_line,
    scan_journal,
)

CHECKPOINT_BYTES = 64 * 1024 * 1024
STATE_VERSION = 1


class MigrationError(RuntimeError):
    pass


def _fsync_dir(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_state(state_path: Path, state: dict) -> None:
    tmp = state_path.with_name(state_path.name + ".w")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, state_path)


def _read_state(state_path: Path) -> dict | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("version") == STATE_VERSION else None


def journal_fingerprint(path: Path) -> dict:
    """Counts and a digest of every non-receipt row plus the set of receipt ids, streamed."""
    counts: dict[str, int] = {}
    digest = hashlib.sha256()
    receipt_ids: set[str] = set()
    max_receipt_line = 0
    with path.open("rb") as handle:
        while line := handle.readline():
            ident = _line_identity(line) if line.endswith(b"\n") else None
            record_type = ident[0] if ident else "unparsed"
            counts[record_type] = counts.get(record_type, 0) + 1
            if record_type == "ingest_receipt":
                receipt_ids.add(ident[1])
                max_receipt_line = max(max_receipt_line, len(line))
            else:
                digest.update(line)
    return {"rows": counts, "non_receipt_digest": digest.hexdigest(), "receipt_ids": receipt_ids,
            "max_receipt_line_bytes": max_receipt_line, "bytes": path.stat().st_size}


def _copy_range(src, dst, start: int, end: int | None, drop_ids: frozenset, stats: dict,
                on_checkpoint=None) -> int:
    """Copy complete rows of ``src`` from ``start`` (to ``end`` or EOF); returns the next offset."""
    src.seek(start)
    offset = start
    since_checkpoint = 0
    while end is None or offset < end:
        line = src.readline()
        if not line or not line.endswith(b"\n"):
            break  # EOF or a torn tail row: never copy a partial row
        out = rewrite_journal_line(line, drop_ids)
        if out is None:
            stats["expired_rows"] += 1
        else:
            if out is not line:
                stats["compacted_rows"] += 1
            dst.write(out)
        offset += len(line)
        stats["rows"] += 1
        since_checkpoint += len(line)
        if on_checkpoint is not None and since_checkpoint >= CHECKPOINT_BYTES:
            on_checkpoint(offset)
            since_checkpoint = 0
    return offset


def migrate(journal: Path, *, retention_days: int = DEFAULT_RECEIPT_RETENTION_DAYS,
            now: datetime | None = None, dry_run: bool = False, keep_backup: bool = True,
            stop_after_checkpoints: int | None = None) -> dict:
    """Run the migration. ``stop_after_checkpoints`` simulates a crash for tests."""
    journal = Path(journal)
    if not journal.exists():
        raise MigrationError(f"journal not found: {journal}")
    tmp = journal.with_name(journal.name + ".migrate.tmp")
    state_path = journal.with_name(journal.name + ".migrate.state.json")
    backup = journal_backup_path(journal)
    lock_path = journal.with_suffix(journal.suffix + ".lock")
    started = time.monotonic()
    ino = journal.stat().st_ino
    state = _read_state(state_path)
    resumed = False
    if state and state.get("ino") == ino and journal.stat().st_size >= state.get("src_offset", 0) \
            and tmp.exists() and tmp.stat().st_size >= state.get("tmp_size", -1) \
            and state.get("retention_days") == retention_days:
        resumed = True
        drop_ids = frozenset(state["drop_ids"])
        base_stats = state["stats"]
        src_offset, tmp_size = state["src_offset"], state["tmp_size"]
        bulk_end = state["bulk_end"]
        before = state["before"]
    else:
        for leftover in (tmp, state_path):
            try:
                leftover.unlink()
            except OSError:
                pass
        scan = scan_journal(journal, retention_days=retention_days, now=now)
        before = {"bytes": scan.bytes, "rows": scan.rows, "receipt_rows": scan.receipts,
                  "oversize_receipt_lines": scan.oversize_receipt_lines}
        drop_ids = scan.drop_ids
        base_stats = {"rows": 0, "compacted_rows": 0, "expired_rows": 0}
        src_offset = tmp_size = 0
        bulk_end = scan.bytes
        if not scan.needs_rewrite:
            return {"changed": False, "reason": "nothing over budget or past retention", "before": before,
                    "seconds": round(time.monotonic() - started, 2)}
    if dry_run:
        return {"changed": False, "dry_run": True, "would_drop_receipts": len(drop_ids),
                "oversize_receipt_lines": before["oversize_receipt_lines"], "before": before}
    stats = dict(base_stats)
    checkpoints = 0

    def checkpoint(offset: int) -> None:
        nonlocal checkpoints
        dst.flush()
        os.fsync(dst.fileno())
        _write_state(state_path, {
            "version": STATE_VERSION, "ino": ino, "src_offset": offset, "tmp_size": dst.tell(),
            "bulk_end": bulk_end, "retention_days": retention_days, "drop_ids": sorted(drop_ids),
            "stats": stats, "before": before})
        checkpoints += 1
        if stop_after_checkpoints is not None and checkpoints >= stop_after_checkpoints:
            raise KeyboardInterrupt("simulated crash")

    mode = "r+b" if resumed else "wb"
    with journal.open("rb") as src, tmp.open(mode) as dst:
        if resumed:
            dst.truncate(tmp_size)
            dst.seek(tmp_size)
        offset = _copy_range(src, dst, src_offset, bulk_end, drop_ids, stats, checkpoint)
        # Final catch-up under the store lock: rows appended while the bulk copy ran.
        with exclusive_file_lock(lock_path, threading.RLock()):
            offset = _copy_range(src, dst, offset, None, frozenset(), stats)
            if offset != journal.stat().st_size:
                raise MigrationError("journal ends with a torn row; refusing to replace it")
            dst.flush()
            os.fsync(dst.fileno())
            after = journal_fingerprint(tmp)
            current = journal_fingerprint(journal)
            _verify(current, after, drop_ids)
            created = False
            if keep_backup:
                # Same code path as the service: hard link, kept 7 days, never replaces an existing one.
                gate = keep_journal_backup(journal, bytes_needed=journal.stat().st_size, now=now)
                if not gate["ok"]:
                    raise MigrationError(gate["reason"])
                created = gate["created"]
            try:
                os.replace(tmp, journal)
            except OSError:
                if created:
                    discard_created_backup(journal)
                raise
            _fsync_dir(journal)
    try:
        state_path.unlink()
    except OSError:
        pass
    return {"changed": True, "resumed": resumed, "before": before, "after": {
                "bytes": after["bytes"], "rows": after["rows"], "max_receipt_line_bytes": after["max_receipt_line_bytes"]},
            "compacted_rows": stats["compacted_rows"], "expired_rows": stats["expired_rows"],
            "dropped_receipt_ids": len(drop_ids), "backup": str(backup) if keep_backup else None,
            "seconds": round(time.monotonic() - started, 2)}


def _verify(before: dict, after: dict, drop_ids: frozenset) -> None:
    """Receipt ids, runs, watermarks and frontier are identical (less the receipts that expired)."""
    for record_type in ("ingest_run", "source_watermark", "crawl_frontier_item"):
        if before["rows"].get(record_type, 0) != after["rows"].get(record_type, 0):
            raise MigrationError(f"{record_type} row count changed: {before['rows']} -> {after['rows']}")
    if before["non_receipt_digest"] != after["non_receipt_digest"]:
        raise MigrationError("non-receipt rows (runs, watermarks, frontier) differ after rewrite")
    expected = before["receipt_ids"] - set(drop_ids)
    if after["receipt_ids"] != expected:
        raise MigrationError("receipt ids differ from the expected set after rewrite")
    if after["max_receipt_line_bytes"] > RECEIPT_LINE_BUDGET_BYTES and before["max_receipt_line_bytes"] > 0:
        if after["max_receipt_line_bytes"] > before["max_receipt_line_bytes"]:
            raise MigrationError("a receipt row grew during rewrite")


def measure_replay(journal: Path) -> dict:
    """Cold replay of ``journal`` in a fresh process: wall seconds and peak resident memory.

    Replay only (the start-time rewrite is switched off) so the figure is the reader's cost.
    """
    code = (
        "import sys,time,resource\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        "from services.source_ingestion import scheduler as s\n"
        "s.JsonlIngestScheduleStore._slim_journal_unlocked=lambda self,**k:{}\n"
        "t=time.monotonic()\n"
        f"st=s.JsonlIngestScheduleStore({str(journal)!r})\n"
        "dt=time.monotonic()-t\n"
        "j=st.journal_stats()\n"
        "print(dt, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, j['receipt_count'], j['run_count'])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    return {"start_replay_seconds": round(float(out[0]), 2), "peak_rss_mb": round(int(out[1]) / 1024, 1),
            "receipts": int(out[2]), "runs": int(out[3])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("journal", type=Path)
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RECEIPT_RETENTION_DAYS,
                        help="drop unpinned receipts older than this (0 keeps all); default 30")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true", help="do not keep <journal>.pre-migrate.bak")
    parser.add_argument("--measure-replay", action="store_true",
                        help="record cold replay seconds and peak RSS before and after")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report: dict = {"journal_bytes_start": args.journal.stat().st_size if args.journal.exists() else None}
    try:
        if args.measure_replay:
            report["replay_before"] = measure_replay(args.journal)
        report["migration"] = migrate(args.journal, retention_days=args.retention_days,
                                      dry_run=args.dry_run, keep_backup=not args.no_backup)
        if args.measure_replay:
            report["replay_after"] = measure_replay(args.journal)
    except MigrationError as exc:
        print(f"migration refused: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
