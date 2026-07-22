#!/usr/bin/env python3
"""Kill worker runs that are alive but making no progress.

reap_stale_in_progress.py only reclaims a task when NO live worker holds it, and
"live" means a worker_runner process carries the task id on its cmdline. A hung
provider CLI defeats that completely: worker_runner keeps heartbeating on its own
15s timer no matter what the child is doing, so a run that produced nothing for
six hours still looked healthy and held its slot the whole time. The heartbeat
proves the runner is alive, never that the work is moving.

This closes that gap with the one signal that was decisive in the incident: a
worker that has not written to its worktree in an hour is not working. Killing it
lets the existing reaper see no live worker and reclaim the task normally, so
this script only ever kills processes -- it does not touch the task board.

Thresholds are deliberately conservative. A run must be old AND silent before it
is touched, so a worker still reading or waiting on a slow call is left alone.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Both thresholds sit far outside observed healthy behavior on purpose. Live
# workers were measured reading for 36 minutes before their first worktree write,
# so a window anywhere near that would kill workers that are merely thinking.
# Killing a healthy run costs real work; missing a hung one costs a slot until
# the next sweep, so the bias is toward waiting.
#
# A run younger than this is never touched, however quiet it looks.
MIN_RUN_AGE_SECONDS = int(os.getenv("PANTHEON_HUNG_WORKER_MIN_AGE_SECONDS", "10800"))
# No worktree write in this long, on a run past MIN_RUN_AGE, means hung. A real
# provider call stalls for minutes; nothing legitimate goes quiet for two hours.
STALL_SECONDS = int(os.getenv("PANTHEON_HUNG_WORKER_STALL_SECONDS", "7200"))

RUN_ID_RE = re.compile(r"--run-id\s+(\S+)")
WORKTREE_RE = re.compile(r"-C\s+(\S+)")


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode(errors="ignore")


def _pid_age_seconds(pid: int) -> float | None:
    """Seconds since the process started, from its own stat file."""
    try:
        out = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        return float(out) if out else None
    except (FileNotFoundError, ValueError):
        return None


def worker_runner_pids() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "worker_runner.py"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        return []
    pids = []
    for token in out.split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return pids


def worktree_written_since(worktree: Path, seconds: int) -> bool:
    """True if any non-.git file under worktree was written within `seconds`.

    Uses find -quit so it stops at the first hit instead of walking the tree.
    """
    if not worktree.is_dir():
        # The worktree vanished (a /tmp wipe). Not progress; let the caller decide.
        return False
    try:
        out = subprocess.run(
            [
                "find", str(worktree),
                "-newermt", f"-{seconds} seconds",
                "-not", "-path", "*/.git/*",
                "-type", "f",
                "-print", "-quit",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Cannot tell -> assume progress, never kill on a broken probe.
        return True
    return bool(out)


def inspect(pid: int, *, min_age: int, stall: int) -> dict | None:
    """Return a hung-run record, or None when the run is healthy or unreadable."""
    cmd = _cmdline(pid)
    if "worker_runner.py" not in cmd:
        return None
    age = _pid_age_seconds(pid)
    if age is None or age < min_age:
        return None
    wt_match = WORKTREE_RE.search(cmd)
    if not wt_match:
        return None
    worktree = Path(wt_match.group(1))
    if not worktree.is_dir():
        return None
    if worktree_written_since(worktree, stall):
        return None
    run_match = RUN_ID_RE.search(cmd)
    return {
        "pid": pid,
        "run_id": run_match.group(1) if run_match else "?",
        "worktree": str(worktree),
        "age_seconds": age,
    }


def kill_run(pid: int, *, dry_run: bool) -> str:
    if dry_run:
        return "dry-run"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already-gone"
    except PermissionError:
        return "permission-denied"
    for _ in range(10):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "terminated"
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "terminated"
    return "killed-9"


def log_line(message: str, *, log_path: Path) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = f"[{stamp}] {message}"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Status root for logs.")
    parser.add_argument("--dry-run", action="store_true", help="Report without killing.")
    parser.add_argument(
        "--min-age-seconds", type=int, default=MIN_RUN_AGE_SECONDS,
        help="Never touch a run younger than this.",
    )
    parser.add_argument(
        "--stall-seconds", type=int, default=STALL_SECONDS,
        help="No worktree write in this long counts as hung.",
    )
    args = parser.parse_args(argv)

    log_path = args.root.resolve() / ".orchestrator" / "logs" / "hung-worker-reap.log"
    hung = []
    for pid in worker_runner_pids():
        record = inspect(pid, min_age=args.min_age_seconds, stall=args.stall_seconds)
        if record:
            hung.append(record)

    for record in hung:
        outcome = kill_run(record["pid"], dry_run=args.dry_run)
        log_line(
            f"HUNG run_id={record['run_id']} pid={record['pid']} "
            f"age={record['age_seconds'] / 3600:.1f}h no-writes-for>={args.stall_seconds}s "
            f"worktree={record['worktree']} -> {outcome}",
            log_path=log_path,
        )
    return 1 if hung else 0


if __name__ == "__main__":
    raise SystemExit(main())
