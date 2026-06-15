#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def normalize_command(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        raw = raw[1:]
    return raw


import re as _re


def derive_agent(run_id: str) -> str:
    """Agent label from a worker run-id, e.g. 'claude-1-2026...' -> 'claude-1'."""
    m = _re.match(r"([a-zA-Z][a-zA-Z0-9]*(?:-[0-9]+)?)-[0-9]{8}T", run_id or "")
    return m.group(1) if m else (run_id or "").split("-")[0]


def derive_task_id(cmd):
    """Parse the dispatched task id ('Task ID: <ID>') from the worker command."""
    m = _re.search(r"Task ID:\s*([A-Z][A-Z0-9_-]+)", " ".join(str(c) for c in cmd))
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an auto-worker command with heartbeat and terminal markers.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--heartbeat-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = normalize_command(list(args.command))
    if not command:
        print("worker_runner: missing command after --", file=sys.stderr)
        return 2

    agent = derive_agent(args.run_id)
    task_id = derive_task_id(command)

    heartbeat_path = Path(args.heartbeat_path)
    status_path = Path(args.status_path)
    interval = max(1.0, float(args.heartbeat_interval_seconds or 15.0))
    started_at = utc_now()
    child: subprocess.Popen[str] | None = None
    terminating_signal: int | None = None

    status: dict[str, Any] = {
        "run_id": args.run_id,
        "agent": agent,
        "task_id": task_id,
        "status": "starting",
        "pid": os.getpid(),
        "child_pid": None,
        "command": command,
        "started_at": started_at,
        "last_heartbeat_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "signal": None,
    }

    def publish(next_status: str) -> None:
        now = utc_now()
        status["status"] = next_status
        status["last_heartbeat_at"] = now
        write_json(heartbeat_path, {
            "run_id": args.run_id,
            "agent": agent,
            "task_id": task_id,
            "status": next_status,
            "pid": os.getpid(),
            "child_pid": status.get("child_pid"),
            "updated_at": now,
        })
        write_json(status_path, status)

    def forward_signal(signum: int, _frame: Any) -> None:
        nonlocal terminating_signal
        terminating_signal = signum
        status["signal"] = signum
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signum)
            except OSError:
                pass

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    try:
        publish("starting")
        child = subprocess.Popen(command, text=True)
        status["child_pid"] = child.pid
        publish("running")
        next_heartbeat = time.monotonic() + interval
        while True:
            exit_code = child.poll()
            if exit_code is not None:
                status["exit_code"] = exit_code
                status["finished_at"] = utc_now()
                publish("completed" if exit_code == 0 else "failed")
                if exit_code < 0:
                    return 128 + abs(exit_code)
                return exit_code
            if time.monotonic() >= next_heartbeat:
                publish("running")
                next_heartbeat = time.monotonic() + interval
            time.sleep(min(1.0, interval))
    except BaseException as exc:
        status["status"] = "failed"
        status["finished_at"] = utc_now()
        status["error"] = f"{type(exc).__name__}: {exc}"
        if terminating_signal is not None:
            status["signal"] = terminating_signal
        try:
            write_json(status_path, status)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
