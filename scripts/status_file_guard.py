#!/usr/bin/env python3
"""Restore the live ai-status.json when a destructive git op wipes it.

ai-status.json is git-tracked but its working-tree copy is live orchestrator
state, so `git reset --hard` / `git clean` in the status root deletes or rewinds
it. The dashboard maps /ai-status.json straight at this file, so losing it takes
the whole board down until someone notices by hand.

This guard restores it from the freshest healthy snapshot (docs-site mirror or a
.bak sibling). It never touches a healthy live file, so it is safe to run on a
tight cron.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import (
    canonical_task_state_lock_file,
    durable_create_bytes,
    read_regular_file_bytes,
    read_regular_file_snapshot,
    restore_canonical_task_state_bytes,
)

LIVE_PATH = ROOT / "ai-status.json"
LOG_PATH = ROOT / ".orchestrator" / "logs" / "status-file-guard.log"
LIVE_MODE = 0o664


def snapshot_candidates(root: Path) -> list[Path]:
    """Restore sources, richest first: the docs-site mirror, then .bak siblings."""
    candidates = [root / "docs-site" / "ai-status.json", root / "ai-status.json.bak"]
    candidates.extend(sorted(root.glob("ai-status.json.bak-*")))
    candidates.extend(sorted(root.glob("ai-status.json.bak.*")))
    return candidates


def parse_status_bytes(raw: bytes) -> dict | None:
    """Parse one byte snapshot only when it is a usable dashboard board."""

    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("agents"):
        return None
    return payload


def read_status_record(path: Path) -> tuple[dict, bytes, float] | None:
    """Return a parsed board and its stable source bytes, or None."""

    try:
        raw, descriptor_stat = read_regular_file_snapshot(
            path,
            source="status snapshot",
        )
        if not raw:
            return None
        payload = parse_status_bytes(raw)
    except (OSError, RuntimeError):
        return None
    if payload is None:
        return None
    return payload, raw, descriptor_stat.st_mtime


def read_status(path: Path) -> dict | None:
    """Return parsed status only if the file is a usable board, else None."""

    record = read_status_record(path)
    return record[0] if record is not None else None


def status_generation(payload: dict, mtime: float) -> tuple[float, float]:
    """Sort key: board's own updated_at, with mtime as tiebreak for equal stamps."""
    stamp = payload.get("updated_at") or payload.get("last_updated") or ""
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        updated_at = parsed.timestamp()
    except ValueError:
        updated_at = 0.0
    return (updated_at, mtime)


def pick_source(root: Path) -> tuple[Path, dict, bytes] | None:
    """Freshest healthy snapshot to restore from, or None if every source is bad."""
    healthy = []
    for path in snapshot_candidates(root):
        record = read_status_record(path)
        if record is not None:
            payload, raw, mtime = record
            healthy.append((status_generation(payload, mtime), path, payload, raw))
    if not healthy:
        return None
    healthy.sort(key=lambda item: item[0], reverse=True)
    _, path, payload, raw = healthy[0]
    return path, payload, raw


def log_line(message: str, *, log_path: Path, echo: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{stamp}] {message}"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    if echo:
        print(line)


def quarantine(live_path: Path, payload: bytes) -> Path:
    """Copy corrupt bytes aside without making canonical state disappear."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(payload).hexdigest()
    target = live_path.with_name(
        f"{live_path.name}.corrupt-{stamp}-{digest[:16]}-{uuid.uuid4().hex}"
    )
    durable_create_bytes(target, payload)
    return target


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def restored_status_bytes(
    source_payload: dict,
    *,
    source_label: str,
    source_bytes: bytes,
    prior_bytes: bytes | None,
) -> bytes:
    """Bind the repair to a durable activity outbox event."""

    payload = dict(source_payload)
    pending = payload.get("status_activity_outbox")
    events: list[dict] = []
    if pending not in (None, {}, []):
        if (
            not isinstance(pending, dict)
            or set(pending) != {"schema_version", "transaction_id", "events"}
            or pending.get("schema_version") != 1
            or not isinstance(pending.get("events"), list)
            or not pending["events"]
            or any(
                not isinstance(event, dict)
                or not str(event.get("event_id") or "").strip()
                for event in pending["events"]
            )
            or len({str(event["event_id"]) for event in pending["events"]})
            != len(pending["events"])
            or pending.get("transaction_id")
            != "ai-status-tx-" + _canonical_json_sha256(pending["events"])
        ):
            raise RuntimeError("status snapshot activity outbox is invalid")
        events.extend(dict(event) for event in pending["events"])

    restored_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = {
        "ts": restored_at,
        "agent": "status-file-guard",
        "type": "status_file_restored",
        "message": f"Restored canonical ai-status.json from {source_label}",
        "source": source_label,
        "source_updated_at": source_payload.get("updated_at"),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "prior_sha256": (
            hashlib.sha256(prior_bytes).hexdigest()
            if prior_bytes is not None
            else None
        ),
    }
    event["event_id"] = "status-file-restored-" + _canonical_json_sha256(event)
    matches = [
        existing
        for existing in events
        if str(existing["event_id"]) == event["event_id"]
    ]
    if matches and any(existing != event for existing in matches):
        raise RuntimeError("status restore activity event id collision")
    if not matches:
        events.append(event)
    payload["status_activity_outbox"] = {
        "schema_version": 1,
        "transaction_id": "ai-status-tx-" + _canonical_json_sha256(events),
        "events": events,
    }
    payload["updated_at"] = restored_at
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _guard_unlocked(root: Path, *, dry_run: bool, verbose: bool, log_path: Path) -> int:
    live_path = root / "ai-status.json"
    live_bytes = None
    if live_path.exists():
        live_bytes = read_regular_file_bytes(live_path, source="canonical task-state")
    live_payload = parse_status_bytes(live_bytes or b"")
    if live_payload is not None:
        if verbose:
            log_line(
                f"healthy: {live_path.name} updated_at={live_payload.get('updated_at')}",
                log_path=log_path,
                echo=True,
            )
        return 0

    reason = "missing" if live_bytes is None else "unreadable/empty"
    source = pick_source(root)
    if source is None:
        log_line(
            f"FAILED: live ai-status.json is {reason} and no healthy snapshot exists to restore from",
            log_path=log_path,
            echo=True,
        )
        return 2

    source_path, source_payload, source_bytes = source
    stamp = source_payload.get("updated_at")
    if dry_run:
        log_line(
            f"dry-run: would restore {reason} ai-status.json from {source_path.name} (updated_at={stamp})",
            log_path=log_path,
            echo=True,
        )
        return 1

    try:
        source_label = str(source_path.relative_to(root))
    except ValueError:
        source_label = source_path.name
    restored_bytes = restored_status_bytes(
        source_payload,
        source_label=source_label,
        source_bytes=source_bytes,
        prior_bytes=live_bytes,
    )
    quarantined = None
    if live_bytes is not None:
        quarantined = quarantine(live_path, live_bytes)
    restore_canonical_task_state_bytes(live_path, restored_bytes, mode=LIVE_MODE)

    detail = f" (corrupt copy kept at {quarantined.name})" if quarantined else ""
    log_line(
        f"RESTORED: ai-status.json was {reason}; recovered from {source_path.name} "
        f"updated_at={stamp}{detail}",
        log_path=log_path,
        echo=True,
    )
    return 1


def guard(root: Path, *, dry_run: bool, verbose: bool, log_path: Path) -> int:
    """Inspect and, if necessary, restore state under the canonical task lock."""

    live_path = root / "ai-status.json"
    try:
        with canonical_task_state_lock_file(live_path, nonblocking=True):
            return _guard_unlocked(
                root,
                dry_run=dry_run,
                verbose=verbose,
                log_path=log_path,
            )
    except BlockingIOError:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Repo root to guard.")
    parser.add_argument("--dry-run", action="store_true", help="Report without restoring.")
    parser.add_argument("--verbose", action="store_true", help="Also log the healthy case.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    log_path = root / ".orchestrator" / "logs" / "status-file-guard.log"
    return guard(root, dry_run=args.dry_run, verbose=args.verbose, log_path=log_path)


if __name__ == "__main__":
    sys.exit(main())
