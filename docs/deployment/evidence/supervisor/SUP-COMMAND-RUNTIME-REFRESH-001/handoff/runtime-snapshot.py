#!/usr/bin/env python3
"""Read-only runtime snapshot for SUP-COMMAND-RUNTIME-REFRESH-001.

Captures the facts the refresh has to preserve: live config bytes, installed
roots, supervisor identity, worker leases, queue leases, and the authoritative
task-state projection report. Never writes into any governed path.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEPLOY = Path("/home/lupin/pantheon-ci-deploy")
LIVE_CONFIG = DEPLOY / "runtime" / "live-supervisor-mainroot-config.json"
STATUS_ROOT = Path("/home/lupin/pantheon")
STATE_FILE = STATUS_ROOT / ".orchestrator" / "state.json"
ADMISSION_LOCK = STATUS_ROOT / ".orchestrator" / "runtime-admission.lock"


def sh(args, cwd=None):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else f"ERR({proc.returncode}):{proc.stderr.strip()}"


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def proc_field(pid: int, name: str) -> str:
    try:
        return (Path("/proc") / str(pid) / name).read_text(errors="ignore").replace("\0", " ").strip()
    except OSError:
        return ""


def proc_argv(pid: int) -> list[str]:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode(errors="ignore") for part in raw.split(b"\0") if part]


def supervisor_processes() -> list[dict]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        argv = proc_argv(pid)
        if not argv or not Path(argv[0]).name.startswith("python"):
            continue
        if "--config" not in argv or not any(
            arg.endswith("/.orchestrator/supervisor.py") for arg in argv[1:]
        ):
            continue
        cmdline = " ".join(argv)
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = ""
        stat = sh(["ps", "-o", "lstart=,etime=,time=,stat=", "-p", str(pid)])
        found.append({"pid": pid, "cwd": cwd, "cmdline": cmdline, "ps": stat})
    return sorted(found, key=lambda item: item["pid"])


def lock_holders() -> dict:
    try:
        inode = str(ADMISSION_LOCK.stat().st_ino)
    except OSError:
        return {}
    rows = []
    for line in Path("/proc/locks").read_text().splitlines():
        if f":{inode} " in line + " ":
            rows.append(line.strip())
    return {"lock": str(ADMISSION_LOCK), "inode": inode, "proc_locks_rows": rows}


def roots() -> list[dict]:
    out = []
    for path in sorted(DEPLOY.glob("dev-root*")):
        if not (path / ".git").exists():
            continue
        out.append(
            {
                "root": str(path),
                "head": sh(["git", "rev-parse", "HEAD"], cwd=path),
                "head_tree": sh(["git", "rev-parse", "HEAD^{tree}"], cwd=path),
                "dirty_entries": len([ln for ln in sh(["git", "status", "--porcelain"], cwd=path).splitlines() if ln]),
                "remote": sh(["git", "remote", "get-url", "origin"], cwd=path),
            }
        )
    return out


def configured_supervisor_root(config: dict) -> str:
    command = config.get("watchdog", {}).get("supervisor_command") or []
    for arg in command:
        if str(arg).endswith("/.orchestrator/supervisor.py"):
            return str(Path(arg).parent.parent)
    return ""


def projection_report(event_log: str, configured_root: str) -> dict:
    """Verify the authoritative journal projection against the canonical board."""
    root = os.environ.get("SNAPSHOT_RUNTIME_ROOT") or configured_root
    if not root:
        return {"error": "live config does not identify a supervisor runtime root"}
    code = (
        "import json,sys;sys.path.insert(0,%r);"
        "from rewrite import task_state_store as s;"
        "events=s.load_events(%r);"
        "state=s.project_latest_state(events);"
        "board=json.load(open(%r));"
        "print(json.dumps({'event_count':len(events),"
        "'projected_sha256':s.sha256_json(state),'board_sha256':s.sha256_json(board),"
        "'verify':s.verify_projection(%r,board)}))"
        % (str(Path(root) / ".orchestrator"), event_log, str(STATUS_ROOT / "ai-status.json"), event_log)
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[-2000:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": proc.stdout[-2000:]}


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    config = json.loads(LIVE_CONFIG.read_text())
    state = json.loads(STATE_FILE.read_text())
    supervisor_state = state.get("supervisor", {})
    active = {
        "running",
        "started",
        "waiting_approval",
        "suspended_approval",
        "manual_pending",
        "retry_backoff",
        "stalled",
        "fallback",
    }
    workers = []
    for run_id, worker in (state.get("workers") or {}).items():
        if worker.get("status") not in active:
            continue
        runtime = worker.get("status_command_runtime") or {}
        workers.append(
            {
                "run_id": run_id,
                "provider": worker.get("provider"),
                "agent_id": worker.get("agent_id"),
                "task_id": worker.get("task_id"),
                "status": worker.get("status"),
                "pid": worker.get("pid"),
                "pid_alive": bool(worker.get("pid")) and Path(f"/proc/{worker.get('pid')}").exists(),
                "lease_acquired_at": worker.get("lease_acquired_at"),
                "lease_expires_at": worker.get("lease_expires_at"),
                "last_heartbeat_at": worker.get("last_heartbeat_at"),
                "queue_event_id": worker.get("queue_event_id"),
                "command_root": runtime.get("command_root"),
                "command_sha": runtime.get("source_sha"),
            }
        )
    queue = []
    for event_id, record in ((state.get("queue") or {}).get("events") or {}).items():
        queue.append(
            {
                "event_id": event_id,
                "status": record.get("status"),
                "task_id": record.get("task_id"),
                "lease_owner": record.get("lease_owner"),
                "lease_expires_at": record.get("lease_expires_at"),
            }
        )
    leases = []
    for key, lease in (state.get("worktree_leases") or {}).items():
        leases.append(
            {
                "key": key,
                "task_id": lease.get("task_id"),
                "path": lease.get("path"),
                "status_root": lease.get("status_root"),
                "run_id": lease.get("run_id") or lease.get("worker_run_id"),
                "released_at": lease.get("released_at"),
            }
        )
    payload = {
        "label": label,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_config": {
            "path": str(LIVE_CONFIG),
            "sha256": sha256(LIVE_CONFIG),
            "size_bytes": LIVE_CONFIG.stat().st_size,
            "mtime": datetime.fromtimestamp(LIVE_CONFIG.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "watchdog_supervisor_command": config.get("watchdog", {}).get("supervisor_command"),
            "task_state_store": config.get("task_state_store"),
            "worker_runtime": config.get("worker_runtime"),
        },
        "origin_dev": sh(["git", "rev-parse", "origin/dev"], cwd=STATUS_ROOT),
        "roots": roots(),
        "supervisor_processes": supervisor_processes(),
        "admission_lock": lock_holders(),
        "supervisor_state": {
            "pid": supervisor_state.get("pid"),
            "lifecycle": supervisor_state.get("lifecycle"),
            "last_heartbeat_at": supervisor_state.get("last_heartbeat_at"),
            "loop_started_at": supervisor_state.get("loop_started_at"),
            "mode_status": supervisor_state.get("mode_status"),
            "task_state_shadow": supervisor_state.get("task_state_shadow"),
        },
        "active_workers": sorted(workers, key=lambda item: item["run_id"]),
        "queue_records": sorted(queue, key=lambda item: item["event_id"]),
        "worktree_leases": [lease for lease in leases if not lease.get("released_at")],
        "projection": projection_report(
            str(DEPLOY / "runtime" / "task-state-events.jsonl"),
            configured_supervisor_root(config),
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
