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
import fcntl
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_PATH = ROOT / "ai-status.json"
LOCK_PATH = ROOT / ".orchestrator" / "status-file-guard.lock"
LOG_PATH = ROOT / ".orchestrator" / "logs" / "status-file-guard.log"
LIVE_MODE = 0o664


def snapshot_candidates(root: Path) -> list[Path]:
    """Restore sources, richest first: the docs-site mirror, then .bak siblings."""
    candidates = [root / "docs-site" / "ai-status.json", root / "ai-status.json.bak"]
    candidates.extend(sorted(root.glob("ai-status.json.bak-*")))
    candidates.extend(sorted(root.glob("ai-status.json.bak.*")))
    return [path for path in candidates if path.is_file()]


def read_status(path: Path) -> dict | None:
    """Return parsed status only if the file is a usable board, else None."""
    try:
        if path.stat().st_size == 0:
            return None
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("agents"):
        return None
    return payload


def status_generation(path: Path, payload: dict) -> tuple[float, float]:
    """Sort key: board's own updated_at, with mtime as tiebreak for equal stamps."""
    stamp = payload.get("updated_at") or payload.get("last_updated") or ""
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        updated_at = parsed.timestamp()
    except ValueError:
        updated_at = 0.0
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (updated_at, mtime)


def pick_source(root: Path) -> tuple[Path, dict] | None:
    """Freshest healthy snapshot to restore from, or None if every source is bad."""
    healthy = []
    for path in snapshot_candidates(root):
        payload = read_status(path)
        if payload is not None:
            healthy.append((status_generation(path, payload), path, payload))
    if not healthy:
        return None
    healthy.sort(key=lambda item: item[0], reverse=True)
    _, path, payload = healthy[0]
    return path, payload


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


def quarantine(live_path: Path) -> Path:
    """Move a corrupt live file aside so the restore never destroys evidence."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = live_path.with_name(f"{live_path.name}.corrupt-{stamp}")
    shutil.move(str(live_path), str(target))
    return target


def guard(root: Path, *, dry_run: bool, verbose: bool, log_path: Path) -> int:
    live_path = root / "ai-status.json"
    live_payload = read_status(live_path)
    if live_payload is not None:
        if verbose:
            log_line(
                f"healthy: {live_path.name} updated_at={live_payload.get('updated_at')}",
                log_path=log_path,
                echo=True,
            )
        return 0

    reason = "missing" if not live_path.exists() else "unreadable/empty"
    source = pick_source(root)
    if source is None:
        log_line(
            f"FAILED: live ai-status.json is {reason} and no healthy snapshot exists to restore from",
            log_path=log_path,
            echo=True,
        )
        return 2

    source_path, source_payload = source
    stamp = source_payload.get("updated_at")
    if dry_run:
        log_line(
            f"dry-run: would restore {reason} ai-status.json from {source_path.name} (updated_at={stamp})",
            log_path=log_path,
            echo=True,
        )
        return 1

    quarantined = None
    if live_path.exists():
        quarantined = quarantine(live_path)
    shutil.copyfile(source_path, live_path)
    live_path.chmod(LIVE_MODE)

    detail = f" (corrupt copy kept at {quarantined.name})" if quarantined else ""
    log_line(
        f"RESTORED: ai-status.json was {reason}; recovered from {source_path.name} "
        f"updated_at={stamp}{detail}",
        log_path=log_path,
        echo=True,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Repo root to guard.")
    parser.add_argument("--dry-run", action="store_true", help="Report without restoring.")
    parser.add_argument("--verbose", action="store_true", help="Also log the healthy case.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    lock_path = root / ".orchestrator" / "status-file-guard.lock"
    log_path = root / ".orchestrator" / "logs" / "status-file-guard.log"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return guard(root, dry_run=args.dry_run, verbose=args.verbose, log_path=log_path)


if __name__ == "__main__":
    sys.exit(main())
