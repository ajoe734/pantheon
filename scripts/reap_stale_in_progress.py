#!/usr/bin/env python3
"""Reap `in_progress` tasks whose worker died, so they get re-dispatched.

Why this exists: `underutilization_dispatch` (the rewake path) is disabled in
the live config because it caused a bff_handoff FOLLOWUP-N sidecar storm under
chronic dep-gating. With it off, a task whose worker dies mid-run stays
`in_progress` forever -- the supervisor treats it as owned/running and never
re-dispatches it, so the task and everything downstream of it silently stall.
Observed 2026-07-15: 6 tasks stuck 3-4h with no worker, ready-frontier drained
to 0, fleet fell to 1 worker.

This is the narrow counterpart to scripts/auto_unblock_stale.py (which does the
same for `blocked`): reset only tasks that are provably abandoned, and cap the
number of automatic reaps so a genuinely broken task escalates to a human
instead of looping forever.

Guards: only status==in_progress; no live worker holding the task id; stale for
>= MIN_AGE_SECONDS; loop cap MAX_AUTO_REAPS per task.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT", "/home/lupin/code/pantheon"))
ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from runtime_state import canonical_task_state_lock_file  # noqa: E402
import sequencing_gate  # noqa: E402

STATUS_FILE = ROOT / "ai-status.json"
STATE_FILE = ROOT / ".orchestrator" / "reap-in-progress-state.json"
LOCK_FILE = ROOT / ".orchestrator" / "reap-in-progress.lock"

# A worker heartbeat is stale after 300s (worker_runtime.heartbeat_stale_seconds).
# 30 min without any live worker means the run is gone, not merely restarting.
MIN_AGE_SECONDS = 1800
MAX_AUTO_REAPS = 2
DRY_RUN = "--dry-run" in sys.argv


def _parse_iso(ts: str) -> float:
    try:
        from datetime import datetime, timezone

        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _running_task_ids() -> set[str]:
    """Task ids carried by a live worker_runner cmdline (upper-cased kebab tokens)."""
    ids: set[str] = set()
    try:
        out = subprocess.run(
            ["pgrep", "-f", "worker_runner.py"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        return ids
    for pid in out.split():
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        for match in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", cmd):
            ids.add(match.upper())
    return ids


def main() -> int:
    # Single-instance: a second cron tick must not race the first.
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = open(LOCK_FILE, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        print("reap: another run holds the lock; skipping")
        return 0

    # Hold the shared canonical task-state lock for the full read-decide-write
    # cycle so this reap pass cannot race ai_status.py / supervisor / dispatcher
    # task-state transactions and clobber newer task truth with a stale read.
    with canonical_task_state_lock_file(STATUS_FILE):
        raw = STATUS_FILE.read_text(encoding="utf-8")
        if not raw.strip():
            print("reap: refusing to act on empty status file", file=sys.stderr)
            return 1
        data = json.loads(raw)
        if data.get("program_activity_outbox") is not None and not DRY_RUN:
            print(
                "reap: task mutations paused while program_activity_outbox is "
                "pending or malformed"
            )
            return 0

        running = _running_task_ids()
        now = time.time()
        try:
            seen = json.loads(STATE_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            seen = {}

        reaped, loop_capped = [], []
        for task in data.get("tasks", []):
            if task.get("status") != "in_progress":
                continue
            if sequencing_gate.task_is_sequencing_parked(task, data):
                continue
            tid = task["id"]
            if tid in running:
                continue
            age = now - _parse_iso(task.get("last_update", ""))
            if age < MIN_AGE_SECONDS:
                continue
            record = seen.get(tid, {"reaps": 0})
            if record.get("reaps", 0) >= MAX_AUTO_REAPS:
                loop_capped.append(tid)
                continue
            if DRY_RUN:
                print(f"WOULD reap {tid} owner={task.get('owner')} stale={age/60:.0f}m")
                reaped.append(tid)
                continue
            task["status"] = "todo"
            task["last_update"] = _iso_now()
            task["next"] = (
                f"[auto-reap] in_progress {age/60:.0f}m with no live worker; run abandoned. "
                f"Reset to todo for re-dispatch (auto-reap #{record.get('reaps', 0) + 1}/{MAX_AUTO_REAPS})."
            )
            task.pop("waiting_for", None)
            record["reaps"] = record.get("reaps", 0) + 1
            record["last_reap_at"] = int(now)
            seen[tid] = record
            reaped.append(tid)

        if reaped and not DRY_RUN:
            # Atomic replace, matching scripts/ai_status.py save discipline.
            # This write must stay lexically inside the lock block above: the
            # canonical writer-inventory scanner recognizes a direct
            # `os.replace(..., STATUS_FILE)` as lock-protected only when it is
            # textually nested under a `with canonical_task_state_lock_file(...)`
            # statement in the same function scope.
            serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=STATUS_FILE.parent, delete=False
            ) as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, STATUS_FILE)
            STATE_FILE.write_text(json.dumps(seen, indent=2))

    print(
        f"{_iso_now()} reap-in-progress: reaped={reaped or 'none'} loop-capped={loop_capped or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
