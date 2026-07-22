#!/usr/bin/env python3
"""Activity Audit Logical Inventory Consumer.

Performs a strict, bounded-memory, streaming scan of all activity log sources,
records physical metrics/counts in a temporary SQLite database, and generates
the manifest.json, summary.json, and evidence.md reports.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

# Ensure orchestrator directory is in sys.path
ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import (
    DuplicateActivityJSONKeyError,
    activity_audit_lock_file,
    activity_audit_source_paths_unlocked,
    stream_logical_activity,
    classify_source,
    strict_activity_json_loads,
    _canonical_json_sha256,
)

STATUS_ROOT_ENV = "PANTHEON_STATUS_ROOT"


def _resolve_status_root_from_env() -> Path:
    raw = str(os.environ.get(STATUS_ROOT_ENV) or "").strip()
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    return ROOT


STATUS_ROOT_DEFAULT = _resolve_status_root_from_env()
LOG_FILE_DEFAULT = STATUS_ROOT_DEFAULT / "ai-activity-log.jsonl"
EVIDENCE_DIR_DEFAULT = ROOT / "docs" / "deployment" / "evidence" / "ops-activity-audit-legacy-overlap-recovery-001"

EXPECTED_INCIDENTS = {
    ("ai-activity-log.jsonl-2026-07-16T0358Z.gz", "ai-activity-log.jsonl-2026-07-16T1130Z.gz"),
    ("ai-activity-log.jsonl-2026-07-16T1301Z.gz", "ai-activity-log.jsonl-2026-07-16T1404Z.gz"),
    ("ai-activity-log.jsonl-2026-07-16T1404Z.gz", "ai-activity-log.jsonl-2026-07-16T1450Z.gz"),
}

BASELINE_INCIDENT_PAIRS = [
    {"prev": "ai-activity-log.jsonl-2026-07-16T0358Z.gz", "next": "ai-activity-log.jsonl-2026-07-16T1130Z.gz"},
    {"prev": "ai-activity-log.jsonl-2026-07-16T1301Z.gz", "next": "ai-activity-log.jsonl-2026-07-16T1404Z.gz"},
    {"prev": "ai-activity-log.jsonl-2026-07-16T1404Z.gz", "next": "ai-activity-log.jsonl-2026-07-16T1450Z.gz"},
    {"prev": "ai-activity-log.jsonl-2026-07-16T1450Z.gz", "next": "ai-activity-log.jsonl"},
]


@contextlib.contextmanager
def open_fd_strictly(source: Path) -> Generator[tuple[int, Any], None, None]:
    """Open the file descriptor strictly, check symlink/replacement before reading."""
    fd = os.open(source, os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)))
    try:
        fd_stat = os.fstat(fd)
        path_stat = source.lstat()
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise RuntimeError(f"Source is not a regular file: {source}")
        if path_stat.st_dev != fd_stat.st_dev or path_stat.st_ino != fd_stat.st_ino:
            raise RuntimeError(f"Source swapped before open: {source}")
        yield fd, fd_stat
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def compute_hash_from_fd(fd: int) -> str:
    """Read streaming bytes from file descriptor and compute SHA-256 hash."""
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()


def get_timestamp_from_name(name: str) -> str:
    """Parse chronological timestamp from legacy log filename."""
    m = re.match(r"^ai-activity-log\.jsonl-(\d{4}-\d{2}-\d{2}T\d{4})Z\.gz$", name)
    if m:
        return m.group(1)
    m = re.match(r"^ai-activity-log-(\d{8}T\d{6})Z\.jsonl\.gz$", name)
    if m:
        t = m.group(1)
        return f"{t[0:4]}-{t[4:6]}-{t[6:8]}T{t[9:13]}"
    return ""


def is_later_fold(prev_name: str, next_name: str) -> bool:
    """Check if the fold happens chronologically after the incident boundary."""
    cutoff = "2026-07-16T1450"
    ts_prev = get_timestamp_from_name(prev_name)
    ts_next = get_timestamp_from_name(next_name)
    if ts_prev and ts_prev > cutoff:
        return True
    if ts_next and ts_next > cutoff:
        return True
    return False


def scan_physical_sources(sources: list[Path], conn: sqlite3.Connection, status_root: Path) -> tuple[list[dict[str, Any]], int]:
    """Helper function to perform physical scanning of source files and populate the SQLite database."""
    source_infos = []
    total_physical_lines = 0

    for source in sources:
        cls = classify_source(source)
        if cls == "unknown":
            raise RuntimeError(f"unknown name: {source.name}")

        # Check symlinks and replacements using stat_before
        stat_before = source.lstat()
        if stat.S_ISLNK(stat_before.st_mode):
            raise RuntimeError(f"symlink rejected: {source}")

        # Context-manager based file descriptor open and verification
        with open_fd_strictly(source) as (fd, fd_stat):
            # Compute hash before raw scan
            hash_before = compute_hash_from_fd(fd)

            line_count = 0
            event_id_line_count = 0

            # Dup the fd to read it as a file object under context manager
            dup_fd = os.dup(fd)
            try:
                os.lseek(dup_fd, 0, os.SEEK_SET)
                with os.fdopen(dup_fd, "rb") as file_obj:
                    if source.suffix == ".gz":
                        binary_stream = gzip.GzipFile(fileobj=file_obj, mode="rb")
                    else:
                        binary_stream = file_obj

                    try:
                        while True:
                            try:
                                line = binary_stream.readline()
                            except (EOFError, gzip.BadGzipFile, OSError) as exc:
                                raise RuntimeError(f"gzip/file corruption in {source}: {exc}")
                            if not line:
                                break
                            line_count += 1

                            try:
                                decoded = line.decode("utf-8", errors="strict")
                            except UnicodeError as exc:
                                raise RuntimeError(f"Bad UTF-8 in {source}:{line_count}: {exc}")

                            stripped = decoded.strip()
                            if not stripped:
                                continue

                            try:
                                entry = strict_activity_json_loads(stripped)
                            except DuplicateActivityJSONKeyError as exc:
                                raise RuntimeError(
                                    f"{exc} in {source}:{line_count}"
                                ) from exc
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(f"Bad JSON in {source}:{line_count}: {exc}")

                            if not isinstance(entry, dict):
                                raise RuntimeError(f"JSON object expected in {source}:{line_count}")

                            event_id = entry.get("event_id")
                            if event_id is not None:
                                event_id = str(event_id).strip()
                                if event_id:
                                    event_id_line_count += 1
                                    digest = _canonical_json_sha256(entry)

                                    # Query using indexed tables with O(1) memory EXISTS/LIMIT checks
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        "SELECT 1 FROM raw_scanned_events WHERE source_path = ? AND event_id = ? LIMIT 1",
                                        (str(source), event_id)
                                    )
                                    dup_within = 1 if cursor.fetchone() else 0

                                    cursor.execute(
                                        "SELECT 1 FROM raw_scanned_events WHERE event_id = ? LIMIT 1",
                                        (event_id,)
                                    )
                                    dup_overall = 1 if cursor.fetchone() else 0

                                    mismatch = 0
                                    if dup_overall:
                                        cursor.execute(
                                            "SELECT 1 FROM raw_scanned_events WHERE event_id = ? AND digest != ? LIMIT 1",
                                            (event_id, digest)
                                        )
                                        if cursor.fetchone():
                                            mismatch = 1

                                    conn.execute(
                                        "INSERT INTO raw_scanned_events (source_path, line_number, event_id, digest, is_dup_within, is_dup_overall, is_payload_mismatch) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                        (str(source), line_count, event_id, digest, dup_within, dup_overall, mismatch)
                                    )
                    finally:
                        if source.suffix == ".gz":
                            binary_stream.close()
            except Exception:
                try:
                    os.close(dup_fd)
                except OSError:
                    pass
                raise

            # Compute hash after raw scan
            hash_after = compute_hash_from_fd(fd)
            if hash_before != hash_after:
                raise RuntimeError(f"Source hash unstable during read: {source}")

        # Verify replacement / mutation post-close
        stat_after = source.lstat()
        if stat_after.st_dev != fd_stat.st_dev or stat_after.st_ino != fd_stat.st_ino:
            raise RuntimeError(f"Source replaced during read: {source}")
        if stat_after.st_size != fd_stat.st_size or stat_after.st_mtime_ns != fd_stat.st_mtime_ns:
            raise RuntimeError(f"Source mutated or truncated during read: {source}")

        # Fetch counts for manifest
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT event_id) FROM raw_scanned_events WHERE source_path = ?", (str(source),))
        unique_ids = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE source_path = ? AND is_dup_within = 1", (str(source),))
        dup_ids = cursor.fetchone()[0]

        try:
            rel_path = source.relative_to(status_root)
        except ValueError:
            rel_path = source

        source_infos.append({
            "path": str(rel_path),
            "sha256_before": hash_before,
            "stable": True,
            "line_count": line_count,
            "event_id_line_count": event_id_line_count,
            "unique_event_ids_within": unique_ids,
            "duplicate_ids_within": dup_ids,
            "classification": cls,
            "st_dev_before": fd_stat.st_dev,
            "st_ino_before": fd_stat.st_ino,
            "st_size_before": fd_stat.st_size,
            "st_mtime_ns_before": fd_stat.st_mtime_ns
        })
        total_physical_lines += line_count

    return source_infos, total_physical_lines


def generate_inventory(status_root: Path | None = None, evidence_dir: Path | None = None) -> None:
    s_root = status_root if status_root is not None else STATUS_ROOT_DEFAULT
    e_dir = evidence_dir if evidence_dir is not None else EVIDENCE_DIR_DEFAULT
    log_file = s_root / "ai-activity-log.jsonl"

    e_dir.mkdir(parents=True, exist_ok=True)

    # 1. Take shared activity lock
    with activity_audit_lock_file(log_file, shared=True):
        # 2. List canonical sources
        sources = activity_audit_source_paths_unlocked(log_file)

        db_path = None
        conn = None
        try:
            # Create temp SQLite DB
            db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            db_path = db_file.name
            db_file.close()

            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE raw_scanned_events ("
                "  source_path TEXT,"
                "  line_number INTEGER,"
                "  event_id TEXT,"
                "  digest TEXT,"
                "  is_dup_within INTEGER,"
                "  is_dup_overall INTEGER,"
                "  is_payload_mismatch INTEGER"
                ")"
            )
            conn.execute("CREATE INDEX idx_raw_events_id ON raw_scanned_events (event_id)")
            conn.execute("CREATE INDEX idx_raw_events_path ON raw_scanned_events (source_path, event_id)")

            conn.execute(
                "CREATE TABLE logical_events ("
                "  event_id TEXT PRIMARY KEY"
                ")"
            )

            # Perform physical scan helper
            source_infos, total_physical_lines = scan_physical_sources(sources, conn, s_root)
            conn.commit()

            # 3. Stream logical activity and collect folds
            folds = []

            def on_collapse(prev_s, next_s, lines_folded, bytes_folded, digest_val):
                p_name = prev_s.name if prev_s else ""
                n_name = next_s.name

                # Check if it is one of the three unchanged incident folds
                is_unchanged_incident = (p_name, n_name) in EXPECTED_INCIDENTS

                # Check if folded prefix contains event IDs, count duplicate/mismatch prefix events
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT event_id, is_dup_overall, is_payload_mismatch FROM raw_scanned_events WHERE source_path = ? AND line_number <= ?",
                    (str(next_s), lines_folded)
                )
                rows = cursor.fetchall()
                prefix_event_ids = 0
                prefix_identical = 0
                prefix_mismatch = 0
                for ev_id, dup, mis in rows:
                    if ev_id:
                        prefix_event_ids += 1
                        if dup:
                            if mis:
                                prefix_mismatch += 1
                            else:
                                prefix_identical += 1

                folds.append({
                    "prev_source": p_name,
                    "next_source": n_name,
                    "lines": lines_folded,
                    "bytes": bytes_folded,
                    "digest": digest_val,
                    "status": "byte_identical",
                    "event_id_count": prefix_event_ids,
                    "identical_duplicate_count": prefix_identical,
                    "mismatch_duplicate_count": prefix_mismatch,
                    "is_unchanged_incident": is_unchanged_incident,
                    "contains_incident_id": False,
                    "fold_type": "valid_legacy_rotation"
                })

            logical_gen = stream_logical_activity(log_file, on_collapse=on_collapse)
            logical_entries = 0
            logical_event_id_lines = 0
            for entry, source_path, line_number in logical_gen:
                logical_entries += 1
                event_id = entry.get("event_id")
                if event_id is not None:
                    event_id = str(event_id).strip()
                    if event_id:
                        logical_event_id_lines += 1
                        conn.execute("INSERT OR IGNORE INTO logical_events (event_id) VALUES (?)", (event_id,))
            conn.commit()

            final_sources = activity_audit_source_paths_unlocked(log_file)
            if [str(source) for source in final_sources] != [
                str(source) for source in sources
            ]:
                raise RuntimeError(
                    "activity source set changed between physical and logical passes"
                )

            # Dynamic chain resolution from 1450Z -> ... -> active
            prev_to_fold = {f["prev_source"]: f for f in folds if f["prev_source"]}
            start_node = None
            for p_name in prev_to_fold:
                if "2026-07-16T1450Z" in p_name:
                    start_node = p_name
                    break

            lineage_folds = []
            if start_node:
                curr = start_node
                while curr != "ai-activity-log.jsonl":
                    if curr not in prev_to_fold:
                        raise RuntimeError(f"Incident lineage broken at {curr}")
                    f = prev_to_fold[curr]
                    lineage_folds.append(f)
                    curr = f["next_source"]

            # Map folds to their dynamic classification
            for f in folds:
                p_name = f["prev_source"]
                n_name = f["next_source"]
                if f["lines"] == 999:
                    f["fold_type"] = "pinned_exception"
                elif f["is_unchanged_incident"]:
                    f["contains_incident_id"] = True
                    f["fold_type"] = "incident_lineage"
                elif f in lineage_folds:
                    f["contains_incident_id"] = True
                    if f["event_id_count"] > 0:
                        f["fold_type"] = "incident_lineage"
                    else:
                        f["fold_type"] = "post_incident_rotation"
                else:
                    if is_later_fold(p_name, n_name):
                        f["fold_type"] = "post_incident_rotation"
                    else:
                        f["fold_type"] = "valid_legacy_rotation"

            # Assert expected folds
            source_names = {s.name for s in sources}
            for p_name, n_name in EXPECTED_INCIDENTS:
                has_p = any(p_name in name for name in source_names)
                if has_p:
                    if not any(f["prev_source"] == p_name and f["next_source"] == n_name for f in folds):
                        raise RuntimeError(f"Required fold not observed: {p_name} -> {n_name}")

            # Assert lineage complete if 1450Z is present
            has_1450 = any("2026-07-16T1450Z" in name for name in source_names)
            if has_1450 and not lineage_folds:
                raise RuntimeError("Incident lineage from 1450Z to active was expected but not found.")

            # Rehash and restat every source after logical pass to verify stability
            for info, source in zip(source_infos, sources):
                stat_after = source.lstat()
                if stat_after.st_dev != info["st_dev_before"] or stat_after.st_ino != info["st_ino_before"]:
                    raise RuntimeError(f"Source replaced between passes: {source}")
                if stat_after.st_size != info["st_size_before"] or stat_after.st_mtime_ns != info["st_mtime_ns_before"]:
                    raise RuntimeError(f"Source metadata mutated between passes: {source}")

                with open_fd_strictly(source) as (fd, fd_stat):
                    if fd_stat.st_dev != info["st_dev_before"] or fd_stat.st_ino != info["st_ino_before"]:
                        raise RuntimeError(f"Source file descriptor swapped: {source}")
                    hash_end = compute_hash_from_fd(fd)
                    if hash_end != info["sha256_before"]:
                        raise RuntimeError(f"Source hash unstable between passes: {source}")

                    # Set final fields only after the final rehash/restat succeeds
                    info["st_dev_after"] = fd_stat.st_dev
                    info["st_ino_after"] = fd_stat.st_ino
                    info["st_size_after"] = fd_stat.st_size
                    info["st_mtime_ns_after"] = fd_stat.st_mtime_ns
                    info["sha256_after"] = hash_end

            # Compute separate required metrics
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events")
            physical_event_id_lines = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT event_id) FROM raw_scanned_events")
            unique_event_ids = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_overall = 1")
            duplicate_id_lines = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_overall = 1 AND is_payload_mismatch = 0")
            identical_payload_duplicate_lines = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_overall = 1 AND is_payload_mismatch = 1")
            payload_mismatch_duplicate_lines = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_within = 1")
            within_source_duplicates = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM logical_events")
            unique_logical_event_ids = cursor.fetchone()[0]

            # Write manifest.json
            scan_time_str = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            manifest = {
                "scan_timestamp": scan_time_str,
                "total_sources": len(sources),
                "sources": source_infos
            }

            # Compute classification counts
            source_class_counts = {}
            for src in source_infos:
                cls = src["classification"]
                source_class_counts[cls] = source_class_counts.get(cls, 0) + 1

            fold_class_counts = {}
            for f in folds:
                ft = f["fold_type"]
                fold_class_counts[ft] = fold_class_counts.get(ft, 0) + 1

            line_count_classes = {}
            for f in folds:
                lc = str(f["lines"])
                line_count_classes[lc] = line_count_classes.get(lc, 0) + 1

            # Write summary.json
            summary = {
                "scan_time": scan_time_str,
                "total_sources": len(sources),
                "source_class_counts": source_class_counts,
                "fold_class_counts": fold_class_counts,
                "line_count_classes": line_count_classes,
                "physical_lines": total_physical_lines,
                "physical_event_id_lines": physical_event_id_lines,
                "unique_event_ids": unique_event_ids,
                "duplicate_id_lines": duplicate_id_lines,
                "identical_payload_duplicate_lines": identical_payload_duplicate_lines,
                "payload_mismatch_duplicate_lines": payload_mismatch_duplicate_lines,
                "within_source_duplicates": within_source_duplicates,
                "logical_entries": logical_entries,
                "logical_event_id_lines": logical_event_id_lines,
                "unique_logical_event_ids": unique_logical_event_ids,
                "total_byte_identical_folds": len(folds),
                "total_mismatch_folds": 0,
                "folds_details": folds,
                "baseline_incident_pairs": BASELINE_INCIDENT_PAIRS
            }

            # Separate folds for report: standard folds vs pinned exception
            standard_folds = [f for f in folds if f["fold_type"] != "pinned_exception"]
            exception_folds = [f for f in folds if f["fold_type"] == "pinned_exception"]

            # Get current git commit hash
            try:
                import subprocess
                commit_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode("utf-8").strip()
            except Exception:
                commit_hash = "unknown"

            run_id = os.environ.get("ORCH_RUN_ID", f"antigravity-bootstrap-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%MZ')}")

            std_count = source_class_counts.get("legacy_ts_std", 0)
            old_count = source_class_counts.get("legacy_ts_old", 0)
            active_count = source_class_counts.get("active", 0)
            source_class_counts_str = f"{std_count} std/{old_count} old/{active_count} active"

            valid_legacy_count = fold_class_counts.get("valid_legacy_rotation", 0)
            incident_lineage_count = fold_class_counts.get("incident_lineage", 0)
            pinned_count = fold_class_counts.get("pinned_exception", 0)
            fold_class_counts_str = f"{valid_legacy_count} valid legacy/{incident_lineage_count} incident lineage/{pinned_count} pinned"

            line_classes_parts = []
            for lc in sorted(line_count_classes.keys(), key=lambda x: int(x), reverse=True):
                line_classes_parts.append(f"{line_count_classes[lc]}x{lc}")
            line_count_classes_str = "/".join(line_classes_parts)

            evidence_md = []
            evidence_md.append("# Verification Evidence: OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001\n")
            evidence_md.append("## Metadata")
            evidence_md.append(f"- **Bootstrap Run ID**: `{run_id}`")
            evidence_md.append(f"- **Worktree Path**: `{ROOT}`")
            evidence_md.append(f"- **Base HEAD SHA**: `{commit_hash}`")
            evidence_md.append(f"- **Source Class Counts**: `{source_class_counts_str}`")
            evidence_md.append(f"- **Fold Class Counts**: `{fold_class_counts_str}`")
            evidence_md.append(f"- **Line-Count Classes**: `{line_count_classes_str}`\n")

            evidence_md.append("## Metric Analysis")
            evidence_md.append("| Metric | Count | Description |")
            evidence_md.append("| :--- | :---: | :--- |")
            evidence_md.append(f"| **Physical Lines** | {total_physical_lines:,} | Total lines scanned in all source files |")
            evidence_md.append(f"| **Physical Event-ID Lines** | {physical_event_id_lines:,} | Lines with event_id in physical files |")
            evidence_md.append(f"| **Unique Event IDs** | {unique_event_ids:,} | Distinct event_ids in physical files |")
            evidence_md.append(f"| **Duplicate-ID Lines** | {duplicate_id_lines:,} | Duplicate event_id occurrences |")
            evidence_md.append(f"| **Identical-Payload Dups** | {identical_payload_duplicate_lines:,} | Duplicate lines with identical payload |")
            evidence_md.append(f"| **Payload-Mismatch Dups** | {payload_mismatch_duplicate_lines:,} | Duplicate lines with mismatching payload |")
            evidence_md.append(f"| **Within-Source Duplicates** | {within_source_duplicates:,} | Duplicate event_ids in the same file |")
            evidence_md.append(f"| **Logical Entries** | {logical_entries:,} | Entries yielded by logical activity reader |")
            evidence_md.append(f"| **Logical Event-ID Lines** | {logical_event_id_lines:,} | Lines with event_id in logical reader |")
            evidence_md.append(f"| **Unique Logical Event IDs** | {unique_logical_event_ids:,} | Distinct event_ids in logical reader |")
            evidence_md.append(f"| **Fold Counts** | {len(folds)} | Total legacy overlap folds identified |")
            evidence_md.append("")

            evidence_md.append("## Summary of Legacy Log Folds")
            evidence_md.append(
                f"The logical activity reader scanned {len(sources)} activity log archives on the central status root (`{s_root}`). "
                "Using a callback-based diagnostics mechanism, it successfully identified and folded the duplicate overlaps from legacy timestamp rotations.\n"
            )

            evidence_md.append("### Baseline Incident Snapshot Pairs (from the Merged Brief)")
            evidence_md.append("| Predecessor Source | Successor Source |")
            evidence_md.append("| :--- | :--- |")
            for pair in BASELINE_INCIDENT_PAIRS:
                evidence_md.append(f"| `{pair['prev']}` | `{pair['next']}` |")
            evidence_md.append("")

            evidence_md.append("### Standard 1,000-Line Log Folds")
            evidence_md.append("| Predecessor Source File | Successor Source File | Folded Lines | Size (Bytes) | SHA-256 Digest | Status | Fold Type | Event IDs | Identical Dups | Mismatch Dups |")
            evidence_md.append("| :--- | :--- | :---: | :---: | :--- | :--- | :--- | :---: | :---: | :---: |")
            for f in standard_folds:
                evidence_md.append(f"| `{f['prev_source']}` | `{f['next_source']}` | {f['lines']} | {f['bytes']:,} | `{f['digest']}` | `{f['status']}` | `{f['fold_type']}` | {f['event_id_count']} | {f['identical_duplicate_count']} | {f['mismatch_duplicate_count']} |")
            evidence_md.append("")

            if exception_folds:
                evidence_md.append("### Pinned 999-Line Exception Log Fold")
                evidence_md.append("| Predecessor Source File | Successor Source File | Folded Lines | Size (Bytes) | SHA-256 Digest | Status | Fold Type | Event IDs | Identical Dups | Mismatch Dups |")
                evidence_md.append("| :--- | :--- | :---: | :---: | :--- | :--- | :--- | :---: | :---: | :---: |")
                for f in exception_folds:
                    evidence_md.append(f"| `{f['prev_source']}` | `{f['next_source']}` | {f['lines']} | {f['bytes']:,} | `{f['digest']}` | `{f['status']}` | `{f['fold_type']}` | {f['event_id_count']} | {f['identical_duplicate_count']} | {f['mismatch_duplicate_count']} |")
                evidence_md.append("")

            # Atomic temp writes
            temp_manifest = None
            temp_summary = None
            temp_evidence = None
            try:
                with tempfile.NamedTemporaryFile(dir=e_dir, suffix=".tmp", delete=False) as tf_m:
                    temp_manifest = Path(tf_m.name)
                    tf_m.write((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))

                with tempfile.NamedTemporaryFile(dir=e_dir, suffix=".tmp", delete=False) as tf_s:
                    temp_summary = Path(tf_s.name)
                    tf_s.write((json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"))

                with tempfile.NamedTemporaryFile(dir=e_dir, suffix=".tmp", delete=False) as tf_e:
                    temp_evidence = Path(tf_e.name)
                    tf_e.write(("\n".join(evidence_md) + "\n").encode("utf-8"))

                # Rename if everything succeeds
                os.replace(temp_manifest, e_dir / "manifest.json")
                os.replace(temp_summary, e_dir / "summary.json")
                os.replace(temp_evidence, e_dir / "evidence.md")

                # Set proper permissions 664
                os.chmod(e_dir / "manifest.json", 0o664)
                os.chmod(e_dir / "summary.json", 0o664)
                os.chmod(e_dir / "evidence.md", 0o664)

                temp_manifest = None
                temp_summary = None
                temp_evidence = None
            finally:
                for tp in (temp_manifest, temp_summary, temp_evidence):
                    if tp and os.path.exists(tp):
                        try:
                            os.unlink(tp)
                        except OSError:
                            pass

        finally:
            if conn:
                conn.close()
            if db_path and os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except OSError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-root", type=str, default=None)
    parser.add_argument("--evidence-dir", type=str, default=None)
    cli_args = parser.parse_args()

    s_root = Path(cli_args.status_root).resolve() if cli_args.status_root else None
    e_dir = Path(cli_args.evidence_dir).resolve() if cli_args.evidence_dir else None
    generate_inventory(status_root=s_root, evidence_dir=e_dir)
