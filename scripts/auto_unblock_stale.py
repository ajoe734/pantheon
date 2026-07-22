#!/usr/bin/env python3
"""Auto-unblock stale `blocked` tasks whose formal dependencies are all satisfied.

Guards: only status==blocked; ALL depends_on in done/archived; no unresolved
waiting_for target or open blocker; no live worker on it; blocked >=
MIN_AGE_SECONDS; loop cap MAX_AUTO_REOPENS (then leave for human). Reopen via
ai_status.py CLI impersonating the owner.
"""
from __future__ import annotations
import json, os, subprocess, sys, time, re
from pathlib import Path

ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT", "/home/lupin/pantheon"))
STATUS_FILE = ROOT / "ai-status.json"
ARCHIVE_DIR = ROOT / "ai-task-archive" / "tasks"
STATE_FILE = ROOT / ".orchestrator" / "auto-unblock-state.json"
AI_STATUS_CLI = ROOT / "scripts" / "ai_status.py"
DONE_STATUSES = {"done"}
MIN_AGE_SECONDS = 480
MAX_AUTO_REOPENS = 2
DRY_RUN = "--dry-run" in sys.argv


def _archived_ids() -> set[str]:
    try:
        return {f[:-5] for f in os.listdir(ARCHIVE_DIR) if f.endswith(".json")}
    except FileNotFoundError:
        return set()


def _parse_iso(ts: str) -> float:
    try:
        from datetime import datetime, timezone
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _running_task_ids() -> set[str]:
    ids: set[str] = set()
    try:
        out = subprocess.run(["pgrep", "-f", "worker_runner.py"], capture_output=True, text=True).stdout
        for pid in out.split():
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
            except OSError:
                continue
            for m in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", cmd):
                ids.add(m.upper())
    except FileNotFoundError:
        pass
    return ids


def _waiting_targets(task: dict) -> list[str]:
    value = task.get("waiting_for")
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _has_unresolved_waiting_target(task: dict, done: set[str]) -> bool:
    """Keep actor/external gates blocked; only completed task IDs are satisfied."""
    return any(target not in done for target in _waiting_targets(task))


def _open_blocker_task_ids(data: dict) -> set[str]:
    return {
        str(blocker.get("task_id") or "").strip()
        for blocker in data.get("blockers", []) or []
        if isinstance(blocker, dict)
        and str(blocker.get("status") or "open").strip().lower() != "resolved"
        and str(blocker.get("task_id") or "").strip()
    }


def main() -> int:
    data = json.loads(STATUS_FILE.read_text())
    tasks = data.get("tasks", [])
    done = {t["id"] for t in tasks if t.get("status") in DONE_STATUSES} | _archived_ids()
    open_blocker_task_ids = _open_blocker_task_ids(data)
    running = _running_task_ids()
    now = time.time()
    try:
        state = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    reopened, skipped_loop = [], []
    for t in tasks:
        if t.get("status") != "blocked":
            continue
        tid = t["id"]
        deps = t.get("depends_on") or []
        if [d for d in deps if d not in done]:
            continue
        if tid in open_blocker_task_ids or _has_unresolved_waiting_target(t, done):
            continue
        if now - _parse_iso(t.get("last_update", "")) < MIN_AGE_SECONDS:
            continue
        if tid in running:
            continue
        rec = state.get(tid, {"reopens": 0})
        if rec.get("reopens", 0) >= MAX_AUTO_REOPENS:
            skipped_loop.append(tid)
            continue
        owner = t.get("owner") or "Codex"
        msg = (f"[auto-unblock] All formal deps satisfied ({deps or 'none'}) but still blocked; "
               f"no live worker. Re-opening (auto-reopen #{rec.get('reopens', 0) + 1}/{MAX_AUTO_REOPENS}).")
        if DRY_RUN:
            print(f"WOULD reopen {tid} owner={owner}")
            reopened.append(tid)
            continue
        env = dict(os.environ, AI_NAME=owner, PANTHEON_STATUS_ROOT=str(ROOT))
        r = subprocess.run(["python3", str(AI_STATUS_CLI), "reopen", tid, msg], capture_output=True, text=True, env=env)
        if r.returncode == 0:
            reopened.append(tid)
            rec["reopens"] = rec.get("reopens", 0) + 1
            rec["last_reopen_at"] = int(now)
            state[tid] = rec
        else:
            print(f"reopen FAILED {tid}: {r.stderr.strip()[:160]}", file=sys.stderr)
    if not DRY_RUN:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    print(f"{stamp} auto-unblock: reopened={reopened or 'none'} loop-capped={skipped_loop or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
