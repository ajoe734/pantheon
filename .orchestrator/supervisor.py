#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import fcntl
import fnmatch
import importlib
import json
import math
import os
import random
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from adapters import build_adapter
from approval_queue import prune_stale_approvals, resolve_approval
from adapters.base import DeliveryRequest
from common import (
    agent_config_for,
    command_exists,
    config_path,
    display_name_for,
    execution_context_files,
    load_config,
    load_json,
    load_status,
    new_runtime_id,
    normalize_agent_id,
    is_github_cli_auth_failure,
    preserve_github_cli_auth_env,
    relpath,
    selected_shared_files,
    shell_quote,
    snapshot_task,
    spawn_background_process,
    summarize_failure_reason,
    utc_now,
    write_failure_evidence,
    write_json,
    write_activity_log,
    worker_runtime_paths,
)
from coordination_file_watcher import sync_coordination_files
from dispatch_policy import (
    DISPATCH_STATUS_ACTIONS,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    normalized_status_set,
    ready_dispatch_settings,
)
from github_bus import sync_github_bus
from provider_permissions import (
    codex_config_health,
    provider_capabilities as build_provider_capabilities,
    write_provider_capabilities,
)
from rebase_helper import continue_or_skip_empty
from runtime_state import load_approval_state, load_event_queue, load_runtime_state, prune_worker_records, queue_event_record, save_runtime_state
from runtime_state import enqueue_event
from task_archive import TaskResolver
from watch_events import queue_delivery_event, run_scan, trim_seen_events


SIDECAR_READY_PRIORITY_OFFSET = 10
BLOCKED_OWNER_RESCUE_KEYWORDS = (
    "auth",
    "authentication",
    "credential",
    "credentials",
    "token",
    "permission",
    "quota",
    "rate limit",
    "push",
    "pr push",
)


SESSION_ID_PATTERNS = [
    re.compile(r'"session_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"sessionId"\s*:\s*"([^"]+)"'),
]
URL_PATTERN = re.compile(r"https://github\.com/[^\s)]+")
WORKER_FAILURE_PATTERNS = (
    re.compile(r"^Error when talking to gemini api\b", re.IGNORECASE),
    re.compile(r'"error"\s*:\s*"rate_limit"', re.IGNORECASE),
    re.compile(r'"type"\s*:\s*"rate_limit_event"', re.IGNORECASE),
    re.compile(r'"error"\s*:\s*"authentication_failed"', re.IGNORECASE),
    re.compile(r"quota exceeded", re.IGNORECASE),
    re.compile(r"free daily quota has been reached", re.IGNORECASE),
    re.compile(r"you have no quota", re.IGNORECASE),
    re.compile(r"^Failed to authenticate\b", re.IGNORECASE),
    re.compile(r"\bnot authenticated\b", re.IGNORECASE),
    re.compile(r"invalid authentication credentials", re.IGNORECASE),
    re.compile(
        r"^reason:\s*.*\b("
        r"terminalquotaerror|retryablequotaerror|quota_exhausted|resource_exhausted|"
        r"you have exhausted your capacity|no capacity available for model|"
        r"timed out|etimedout|econnreset|unauthorized"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(r"^status:\s*(401|429)\b", re.IGNORECASE),
    re.compile(r"^(?:you(?:'ve| have)\s+)?hit your(?:\s+\w+)?\s+limit\b", re.IGNORECASE),
    re.compile(r"^Error loading config\.toml\b", re.IGNORECASE),
    re.compile(r"^An unexpected critical error occurred", re.IGNORECASE),
    re.compile(r"^(?:Error|error|fatal):", re.IGNORECASE),
)
WORKER_FAILURE_FALSE_POSITIVE_PATTERNS = (
    re.compile(r"^(?:result|error|audit):\s+Optional\[Dict\[str,\s*Any\]\]\s*=\s*None,?$", re.IGNORECASE),
    re.compile(r"^error:\s+BFF?[A-Za-z0-9_]*Error[A-Za-z0-9_]*,?$", re.IGNORECASE),
    re.compile(r"^error:\s+[A-Za-z_][A-Za-z0-9_<>{}\[\], :|?]+?\|\s*null$", re.IGNORECASE),
    re.compile(r"^[+-]?\s*console\.error\(", re.IGNORECASE),
    re.compile(r"^[+-]\s*[A-Za-z_][A-Za-z0-9_.]*\s*=\s*", re.IGNORECASE),
    re.compile(r"^-\s+\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+·\s+", re.IGNORECASE),
    re.compile(r"\bauto-reassigned\b.*\bafter repeated\b.*\bquota\b", re.IGNORECASE),
)
SEARCH_RESULT_JSON_FIELD_PATTERN = re.compile(
    r"^(?:[^:\s][^:]*:)?\d+[:-]\s*\"[A-Za-z0-9_]+\"\s*:\s*",
    re.IGNORECASE,
)
JSON_FIELD_LINE_PATTERN = re.compile(
    r"^\"[A-Za-z0-9_]+\"\s*:\s*",
    re.IGNORECASE,
)
SEARCH_RESULT_LOG_JSON_PATTERN = re.compile(
    r"^[^\s:]+\.log:\d+[:-]\s*\{",
    re.IGNORECASE,
)
COMMAND_OUTPUT_EXIT_LINE_PATTERN = re.compile(r"^exited\s+\d+\s+in\s+\S+:", re.IGNORECASE)

LOCAL_TZ = ZoneInfo("Asia/Taipei")
SUPERVISOR_LOG_QUIET = False
GENERIC_WORKER_EXIT_REASON = "Worker exited before the task reached a terminal status."
PLANNING_STATE_FILE = THIS_DIR / "planning-state.json"
PLANNING_PHASE_DIR = THIS_DIR.parent / "docs" / "02-architecture" / "consensus" / "phase1"
_UNSET = object()


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


def supervisor_lock_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.lock"


# Held open for the lifetime of the winning supervisor process. The advisory
# flock is released automatically by the kernel when the process exits (or is
# killed), so a crashed supervisor never leaves the lock stuck.
_SINGLETON_LOCK_HANDLE: Any = None


def acquire_singleton_lock(config: dict[str, Any]) -> bool:
    """Acquire the exclusive supervisor singleton lock.

    Returns True if this process is now the sole supervisor, False if another
    live supervisor already holds the lock (in which case the caller should
    exit WITHOUT touching the shared pid file or runtime state). This is the
    race-proof single-instance guard that covers every launch path
    (cron/tmux/run-supervisor.sh and the watchdog's direct spawn), replacing
    the PID-ordering heuristic that broke under PID wraparound.
    """
    global _SINGLETON_LOCK_HANDLE
    path = supervisor_lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _SINGLETON_LOCK_HANDLE = handle
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except OSError:
        pass
    return True


def write_supervisor_pid(config: dict[str, Any]) -> None:
    path = supervisor_pid_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")


def clear_supervisor_pid(config: dict[str, Any]) -> None:
    path = supervisor_pid_path(config)
    if not path.exists():
        return
    try:
        current = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if current == str(os.getpid()):
        try:
            state = load_runtime_state(config)
            supervisor_state = state.setdefault("supervisor", {})
            supervisor_state["pid"] = os.getpid()
            supervisor_state["lifecycle"] = "stopping"
            supervisor_state["last_heartbeat_at"] = utc_now()
            save_runtime_state(config, state)
        except Exception:
            pass
        path.unlink(missing_ok=True)


def cmdline_is_supervisor_process(parts: list[str]) -> bool:
    current_script = str(Path(__file__).resolve())
    current_script_name = str(Path(__file__).name)
    current_script_rel = ".orchestrator/supervisor.py"
    if not parts:
        return False
    executable = Path(parts[0]).name
    if parts[0] in {current_script, current_script_rel}:
        return True
    if not executable.startswith("python"):
        return False
    return any(
        part == current_script
        or part == current_script_rel
        or part.endswith(f"/{current_script_name}")
        for part in parts[1:]
    )


def iter_matching_supervisor_pids() -> list[int]:
    current_repo_root = str(THIS_DIR.parent.resolve())
    matches: list[int] = []
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        cmdline_path = proc_dir / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        parts = [part.decode("utf-8", errors="ignore") for part in raw.split(b"\x00") if part]
        try:
            proc_cwd = str((proc_dir / "cwd").resolve())
        except OSError:
            proc_cwd = ""
        if cmdline_is_supervisor_process(parts) and proc_cwd == current_repo_root:
            matches.append(pid)
    return sorted(matches)


def terminate_other_supervisors(config: dict[str, Any]) -> None:
    """Terminate every other matching supervisor process except this one.

    Called only by the process that just won the singleton flock, so killing all
    other matches (rather than only lower-PID "older" ones) is safe and clears
    any lock-less straggler from an earlier code version. The previous
    pid < current_pid heuristic silently failed under PID wraparound, which let a
    later-started supervisor with a smaller PID coexist with an earlier one.
    """
    current_pid = os.getpid()
    terminated: list[int] = []
    for pid in iter_matching_supervisor_pids():
        if pid == current_pid:
            continue
        if not pid_is_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        deadline = time.time() + 2.0
        while time.time() < deadline and pid_is_alive(pid):
            time.sleep(0.1)
        if pid_is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            deadline = time.time() + 1.0
            while time.time() < deadline and pid_is_alive(pid):
                time.sleep(0.05)
        terminated.append(pid)
    for pid in terminated:
        write_activity_log(
            config,
            {
                "type": "supervisor_replaced",
                "message": f"Terminated older supervisor process {pid} while starting {current_pid}.",
                "old_pid": pid,
                "new_pid": current_pid,
            },
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local orchestrator supervisor loop.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-watch", action="store_true", help="Process the event queue without running watch_events first.")
    parser.add_argument("--replay", action="store_true", help="Pass replay through to watch_events for the first scan.")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help=(
            "Override supervisor poll interval in seconds. Values below "
            "config.supervisor.poll_interval_seconds require --allow-fast-poll."
        ),
    )
    parser.add_argument(
        "--allow-fast-poll",
        action="store_true",
        help=(
            "Authorize --poll-interval below the configured value. Reserved for "
            "ad-hoc incident debugging; do not use for steady-state runs."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress terminal heartbeat output.")
    parser.add_argument("--verbose", action="store_true", help="Print active worker and queue details each tick.")
    parser.add_argument("--claim-agent", default=None, help="Let one idle agent claim and start one ready task.")
    parser.add_argument("--release-task", default=None, help="Release this agent's completed worker slot before claiming more work.")
    parser.add_argument("--clear-provider-pause", default=None, help="Manually clear one provider dispatch pause.")
    return parser.parse_args()


CONFIG_DEFAULT_POLL_INTERVAL_SECONDS = 300.0


class FastPollNotAllowedError(SystemExit):
    """Raised when --poll-interval is below config without --allow-fast-poll."""


def resolve_poll_interval(
    config: dict[str, Any],
    *,
    cli_value: float | None,
    allow_fast_poll: bool,
) -> tuple[float, str]:
    configured = float(
        config.get("supervisor", {}).get(
            "poll_interval_seconds", CONFIG_DEFAULT_POLL_INTERVAL_SECONDS
        )
    )
    if cli_value is None:
        return configured, "config"
    if cli_value <= 0:
        raise FastPollNotAllowedError(
            f"--poll-interval must be positive (got {cli_value})."
        )
    if cli_value < configured and not allow_fast_poll:
        raise FastPollNotAllowedError(
            f"--poll-interval={cli_value}s is below config.supervisor.poll_interval_seconds={configured}s. "
            "Pass --allow-fast-poll to authorize an ad-hoc fast cadence, or update config.json "
            "if this is a steady-state change."
        )
    return cli_value, "cli"


def console_log(message: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    timestamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def parse_runtime_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def heartbeat_lag_seconds(previous_heartbeat: str | None, current_heartbeat: str | None) -> float | None:
    previous_dt = parse_runtime_timestamp(previous_heartbeat)
    current_dt = parse_runtime_timestamp(current_heartbeat)
    if previous_dt is None or current_dt is None:
        return None
    return max(0.0, (current_dt - previous_dt).total_seconds())


def watchdog_safe_mode_active(state: dict[str, Any], now: datetime | None = None) -> bool:
    watchdog = state.get("watchdog", {}) if isinstance(state.get("watchdog"), dict) else {}
    safe_mode_until = parse_runtime_timestamp(str(watchdog.get("safe_mode_until") or ""))
    if safe_mode_until is None:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return now_dt.astimezone(timezone.utc) < safe_mode_until.astimezone(timezone.utc)


def record_watchdog_safe_mode_observed(config: dict[str, Any], state: dict[str, Any], now: str) -> bool:
    watchdog = state.setdefault("watchdog", {})
    safe_mode_until = str(watchdog.get("safe_mode_until") or "").strip()
    if not safe_mode_until:
        return False
    if watchdog.get("last_safe_mode_observed_until") == safe_mode_until:
        return False
    watchdog["last_safe_mode_observed_until"] = safe_mode_until
    write_activity_log(
        config,
        {
            "type": "watchdog_safe_mode_dispatch_suppressed",
            "message": f"Watchdog safe mode suppresses new supervisor dispatch until {safe_mode_until}.",
            "safe_mode_until": safe_mode_until,
            "reason": watchdog.get("safe_mode_reason"),
        },
    )
    return True


def format_runtime_timestamp_local(ts: str | None) -> str:
    dt = parse_runtime_timestamp(ts)
    if dt is None:
        return "-"
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def summarize_runtime(state: dict[str, Any], approval_state: dict[str, Any]) -> dict[str, Any]:
    workers = state.get("workers", {}) or {}
    queue_events = state.get("queue", {}).get("events", {}) or {}
    pending_approvals = approval_state.get("pending", []) or []
    active_statuses = {"running", "started", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled", "fallback"}
    active_workers = [
        {
            "run_id": run_id,
            "task_id": worker.get("task_id"),
            "agent_id": worker.get("agent_id"),
            "provider": worker.get("provider"),
            "status": worker.get("status"),
        }
        for run_id, worker in workers.items()
        if worker.get("status") in active_statuses
    ]
    queue_items = [
        {
            "event_id": event_id,
            "status": record.get("status"),
            "run_id": record.get("run_id"),
            "error": record.get("error"),
        }
        for event_id, record in queue_events.items()
        if str(record.get("status") or "") not in {"completed", "done"}
    ]
    return {
        "active_worker_count": len(active_workers),
        "queue_count": len(queue_items),
        "pending_approval_count": len(pending_approvals),
        "active_workers": active_workers,
        "queue_items": queue_items,
    }


def refresh_dashboard_runtime_artifacts(config: dict[str, Any]) -> None:
    try:
        repo_root = config_path(config, "status_file").parent
    except KeyError:
        repo_root = THIS_DIR.parent
    scripts_dir = repo_root / "scripts"
    if not scripts_dir.exists():
        return
    scripts_path = str(scripts_dir)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    try:
        ai_status = importlib.import_module("ai_status")
        status_state = ai_status.load_state()
        ai_status.write_dashboard_bundle(status_state)
        ai_status.sync_docs_site(status_state)
    except Exception as exc:
        console_log(
            f"dashboard bundle refresh failed: {type(exc).__name__}: {exc}",
            quiet=SUPERVISOR_LOG_QUIET,
        )


def safe_load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    try:
        return load_approval_state(config)
    except KeyError:
        return {"pending": [], "history": []}


def event_dispatch_mode(event: dict[str, Any]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    planning = metadata.get("planning")
    if isinstance(planning, dict) and planning:
        return "planning"
    chair = metadata.get("chair")
    if isinstance(chair, dict) and chair:
        return "chair_review"
    coordination = metadata.get("coordination")
    if isinstance(coordination, dict) and coordination:
        return "coordination"
    reason = str(event.get("reason") or "").strip()
    if reason.startswith("discussion_planning_"):
        return "planning"
    if reason.startswith("chair_review:"):
        return "chair_review"
    if reason.startswith("coordination:"):
        return "coordination"
    return "execution"


def worker_dispatch_mode(worker: dict[str, Any]) -> str:
    if worker_is_discussion_planning(worker):
        return "planning"
    if worker_is_chair_review(worker):
        return "chair_review"
    if worker_is_coordination_dispatch(worker):
        return "coordination"
    return "execution"


def empty_mode_occupancy() -> dict[str, dict[str, int]]:
    return {
        "planning": {"running": 0, "pending": 0, "queued": 0},
        "execution": {"running": 0, "pending": 0, "queued": 0},
        "coordination": {"running": 0, "pending": 0, "queued": 0},
        "chair_review": {"running": 0, "pending": 0, "queued": 0},
    }


def mode_has_activity(bucket: dict[str, Any] | None) -> bool:
    if not isinstance(bucket, dict):
        return False
    return any(int(bucket.get(key) or 0) > 0 for key in ("running", "pending", "queued"))


def compute_mode_occupancy(config: dict[str, Any], state: dict[str, Any]) -> dict[str, dict[str, int]]:
    occupancy = empty_mode_occupancy()
    settings = ready_dispatch_settings(config)
    active_worker_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    active_worker_statuses.update({"started", "suspended_approval", "fallback"})
    pending_worker_statuses = {"waiting_approval", "manual_pending", "suspended_approval", "retry_backoff"}
    active_event_ids: set[str] = set()

    for worker in state.get("workers", {}).values():
        status = str(worker.get("status") or "")
        if status not in active_worker_statuses:
            continue
        mode = worker_dispatch_mode(worker)
        bucket = occupancy.setdefault(mode, {"running": 0, "pending": 0, "queued": 0})
        if status in pending_worker_statuses:
            bucket["pending"] += 1
        else:
            bucket["running"] += 1
        event_id = str(worker.get("queue_event_id") or "").strip()
        if event_id:
            active_event_ids.add(event_id)

    queue_records = state.get("queue", {}).get("events", {}) or {}
    pending_queue_statuses = {"started", "manual_pending", "waiting_approval", "suspended_approval", "retry_backoff", "stalled", "fallback"}
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []

    for event in queued_events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        record = queue_records.get(event_id, {})
        record_status = str(record.get("status") or "queued")
        if record_status in {"completed", "failed", "done"}:
            continue
        if event_id in active_event_ids:
            continue
        mode = event_dispatch_mode(event)
        bucket = occupancy.setdefault(mode, {"running": 0, "pending": 0, "queued": 0})
        if record_status in pending_queue_statuses:
            bucket["pending"] += 1
        else:
            bucket["queued"] += 1

    return occupancy


def stamp_supervisor_runtime_state(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    planning_state: dict[str, Any] | None,
    heartbeat_at: str,
    lifecycle: str | None = None,
    loop_started_at: str | object = _UNSET,
    loop_finished_at: str | object = _UNSET,
    loop_error: str | None | object = _UNSET,
) -> None:
    supervisor_state = state.setdefault("supervisor", {})
    current_pid = os.getpid()
    previous_pid = supervisor_state.get("pid")
    previous_focus = str(supervisor_state.get("focus_mode") or "").strip()

    supervisor_state["pid"] = current_pid
    supervisor_state["last_heartbeat_at"] = heartbeat_at
    if not supervisor_state.get("started_at") or previous_pid != current_pid:
        supervisor_state["started_at"] = heartbeat_at
        supervisor_state["last_successful_loop_at"] = None
        supervisor_state["last_loop_started_at"] = None
        supervisor_state["last_loop_finished_at"] = None
        supervisor_state["last_loop_duration_ms"] = None
        supervisor_state["last_loop_error"] = None

    if lifecycle is not None:
        supervisor_state["lifecycle"] = lifecycle
    if loop_started_at is not _UNSET:
        supervisor_state["last_loop_started_at"] = loop_started_at
    if loop_finished_at is not _UNSET:
        supervisor_state["last_loop_finished_at"] = loop_finished_at
    if loop_error is not _UNSET:
        supervisor_state["last_loop_error"] = loop_error
    effective_loop_started_at = (
        loop_started_at
        if isinstance(loop_started_at, str)
        else supervisor_state.get("last_loop_started_at")
    )
    if (
        loop_finished_at is not _UNSET
        and isinstance(effective_loop_started_at, str)
        and isinstance(loop_finished_at, str)
    ):
        started_dt = parse_runtime_timestamp(effective_loop_started_at)
        finished_dt = parse_runtime_timestamp(loop_finished_at)
        if started_dt is not None and finished_dt is not None:
            supervisor_state["last_loop_duration_ms"] = max(0, int((finished_dt - started_dt).total_seconds() * 1000))
    if (
        loop_finished_at is not _UNSET
        and loop_finished_at is not None
        and loop_error is not _UNSET
        and loop_error is None
    ):
        supervisor_state["last_successful_loop_at"] = loop_finished_at

    occupancy = compute_mode_occupancy(config, state)
    supervisor_state["mode_occupancy"] = occupancy

    desired_focus = "planning" if discussion_planning_is_active(planning_state) else "execution"
    previous_focus_valid = previous_focus in {"planning", "execution"}
    # Discussion planning is additive: keep the visible focus on planning even if
    # execution still has inflight work that should continue to drain in parallel.
    if desired_focus == "planning":
        supervisor_state["focus_mode"] = "planning"
        supervisor_state["mode_status"] = "active" if mode_has_activity(occupancy.get("planning")) else "idle"
        supervisor_state["mode_switch_requested"] = None
        if previous_focus_valid and previous_focus != "planning":
            supervisor_state["last_mode_switch_at"] = heartbeat_at
    elif previous_focus_valid and previous_focus != desired_focus and mode_has_activity(occupancy.get(previous_focus)):
        supervisor_state["focus_mode"] = previous_focus
        supervisor_state["mode_status"] = "draining"
        supervisor_state["mode_switch_requested"] = desired_focus
    else:
        supervisor_state["focus_mode"] = desired_focus
        supervisor_state["mode_status"] = "active" if mode_has_activity(occupancy.get(desired_focus)) else "idle"
        supervisor_state["mode_switch_requested"] = None
        if previous_focus_valid and previous_focus != desired_focus:
            supervisor_state["last_mode_switch_at"] = heartbeat_at


def bootstrap_supervisor_runtime_state(config: dict[str, Any], *, lifecycle: str = "starting") -> dict[str, Any]:
    heartbeat_at = utc_now()
    state = load_runtime_state(config)
    stamp_supervisor_runtime_state(
        config,
        state,
        planning_state=load_discussion_planning_state(),
        heartbeat_at=heartbeat_at,
        lifecycle=lifecycle,
    )
    save_runtime_state(config, state)
    return state


def log_runtime_summary(
    state: dict[str, Any],
    approval_state: dict[str, Any],
    *,
    changed: bool,
    quiet: bool,
    verbose: bool,
    previous_heartbeat: str | None = None,
    warn_after_seconds: float = 10.0,
    once: bool = False,
) -> None:
    summary = summarize_runtime(state, approval_state)
    supervisor_state = state.get("supervisor", {}) or {}
    heartbeat = supervisor_state.get("last_heartbeat_at") or "-"
    heartbeat_local = format_runtime_timestamp_local(heartbeat if heartbeat != "-" else None)
    lag_seconds = heartbeat_lag_seconds(previous_heartbeat, heartbeat)
    lag_summary = f"{lag_seconds:.1f}s" if lag_seconds is not None else "-"
    lifecycle = str(supervisor_state.get("lifecycle") or "idle")
    mode_status = str(supervisor_state.get("mode_status") or "idle")
    mode = "once" if once else "tick"
    console_log(
        (
            f"supervisor {mode}: lifecycle={lifecycle} heartbeat={heartbeat_local} lag={lag_summary} changed={'yes' if changed else 'no'} "
            f"mode={mode_status} "
            f"queue={summary['queue_count']} "
            f"approvals={summary['pending_approval_count']} "
            f"active_workers={summary['active_worker_count']}"
        ),
        quiet=quiet,
    )
    if lag_seconds is not None and lag_seconds > warn_after_seconds:
        console_log(
            f"WARNING heartbeat lag exceeded threshold: {lag_seconds:.1f}s > {warn_after_seconds:.1f}s",
            quiet=quiet,
        )
    if not verbose or quiet:
        return
    console_log(f"heartbeat: {heartbeat_local} (utc={heartbeat}, lag={lag_summary})", quiet=quiet)
    if summary["active_workers"]:
        details = ", ".join(
            f"{item['agent_id'] or item['provider']}:{item['task_id']}({item['status']})"
            for item in summary["active_workers"]
        )
        console_log(f"active workers: {details}", quiet=quiet)
    else:
        console_log("active workers: none", quiet=quiet)
    if summary["queue_items"]:
        details = ", ".join(
            f"{item['event_id']}({item['status']})"
            for item in summary["queue_items"]
        )
        console_log(f"queue: {details}", quiet=quiet)
    else:
        console_log("queue: empty", quiet=quiet)


def load_provider_report(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("supervisor", {}).get("auto_refresh_provider_capabilities", True):
        report = build_provider_capabilities(config)
        write_provider_capabilities(config, report=report)
        return report
    return load_json(config_path(config, "provider_capabilities"), default={}) or {}


def resolve_agent_model_preference(config: dict[str, Any], agent: dict[str, Any]) -> str | None:
    explicit = str(agent.get("model_preference") or "").strip()
    if explicit:
        return explicit

    provider_id = str(agent.get("provider") or agent.get("id") or "").strip()
    provider = config.get("providers", {}).get(provider_id, {})
    model_preference = provider.get("model_preference", {})
    if not isinstance(model_preference, dict):
        return None

    agent_id = str(agent.get("id") or "").strip()
    direct = str(model_preference.get(agent_id) or "").strip()
    if direct:
        return direct

    if agent_id == provider_id:
        default = str(model_preference.get("default") or "").strip()
        if default:
            return default
    return None


def provider_config_entry_for(config: dict[str, Any], provider: str | None) -> tuple[str, dict[str, Any]]:
    providers = config.get("providers", {}) or {}
    raw = str(provider or "").strip()
    if not raw:
        return "", {}
    normalized = normalize_agent_id(raw)
    candidates = [raw, normalized, raw.replace("_", "-"), raw.replace("-", "_")]
    for candidate in candidates:
        if candidate in providers and isinstance(providers[candidate], dict):
            return candidate, providers[candidate]
    return normalized, {}


def provider_config_for(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    return provider_config_entry_for(config, provider)[1]


def provider_runtime_config_block_reason(config: dict[str, Any], provider: str | None) -> str | None:
    provider_key, provider_cfg = provider_config_entry_for(config, provider)
    if str(provider_cfg.get("delivery_mode") or "").strip().lower() != "codex":
        return None
    health = codex_config_health(config, provider_key or str(provider or "codex"))
    if health.get("valid", True):
        return None
    return str(health.get("error") or f"{provider_key or provider} provider config is invalid.")


def provider_dispatch_group_id(config: dict[str, Any], provider: str | None) -> str:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return ""
    provider_cfg = provider_config_for(config, provider)
    group = (
        provider_cfg.get("quota_group")
        or provider_cfg.get("dispatch_group")
        or provider_cfg.get("account_group")
    )
    return normalize_agent_id(str(group or provider_id))


def agent_provider_id(config: dict[str, Any], agent_id: str | None) -> str:
    normalized = normalize_agent_id(agent_id or "")
    if not normalized:
        return ""
    agent = (config.get("agents", {}) or {}).get(normalized, {}) or {}
    return normalize_agent_id(str(agent.get("provider") or normalized))


def agent_quota_group_id(config: dict[str, Any], agent_id: str | None) -> str:
    provider_id = agent_provider_id(config, agent_id)
    return provider_dispatch_group_id(config, provider_id or agent_id)


def active_quota_group_counts(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
        group_id = normalize_agent_id(str(worker.get("quota_group") or ""))
        if not group_id:
            group_id = provider_dispatch_group_id(config, str(worker.get("provider") or worker.get("agent_id") or ""))
        if not group_id:
            continue
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def queued_quota_group_counts(config: dict[str, Any], state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    queue_records = state.get("queue", {}).get("events", {})
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_queue_event_ids = {
        str(worker.get("queue_event_id") or "")
        for worker in state.get("workers", {}).values()
        if worker.get("status") in active_statuses and worker.get("queue_event_id")
    }
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []
    for event in queued_events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        if event_id in active_queue_event_ids:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        group_id = agent_quota_group_id(config, str(event.get("target_agent") or ""))
        if not group_id:
            continue
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def quota_group_concurrency_limit(
    config: dict[str, Any],
    agent_id: str | None,
    settings: dict[str, Any] | None = None,
) -> int | None:
    settings = settings or ready_dispatch_settings(config)
    raw = settings.get("max_concurrent_per_quota_group")
    group_id = agent_quota_group_id(config, agent_id)
    if isinstance(raw, dict):
        provider_id = agent_provider_id(config, agent_id)
        display_name = display_name_for(config, normalize_agent_id(agent_id or ""))
        for key in (group_id, provider_id, normalize_agent_id(agent_id or ""), display_name):
            if key in raw:
                try:
                    return max(0, int(raw[key]))
                except (TypeError, ValueError):
                    return None
        return None
    if raw in (None, ""):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def agent_is_dispatch_slot(agent: dict[str, Any] | None) -> bool:
    return bool(isinstance(agent, dict) and str(agent.get("dispatch_slot_for") or "").strip())


def logical_worker_slot_ids(config: dict[str, Any], agent_id: str | None) -> list[str]:
    normalized = normalize_agent_id(agent_id or "")
    if not normalized:
        return []
    agents = config.get("agents", {}) or {}
    logical_agent = agents.get(normalized) or {}
    slot_ids: list[str] = []
    seen: set[str] = set()
    for raw_slot in logical_agent.get("worker_slots", []) or []:
        slot_id = normalize_agent_id(str(raw_slot or ""))
        if slot_id and slot_id in agents and slot_id not in seen:
            seen.add(slot_id)
            slot_ids.append(slot_id)
    for slot_id, slot_agent in agents.items():
        if normalize_agent_id(str((slot_agent or {}).get("dispatch_slot_for") or "")) != normalized:
            continue
        normalized_slot = normalize_agent_id(slot_id)
        if normalized_slot and normalized_slot not in seen:
            seen.add(normalized_slot)
            slot_ids.append(normalized_slot)
    return slot_ids


def dispatch_loop_agent_ids(config: dict[str, Any]) -> list[str]:
    return [
        normalize_agent_id(agent_id)
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if normalize_agent_id(agent_id) and not agent_is_dispatch_slot(agent)
    ]


def agent_dispatch_capacity(config: dict[str, Any], agent_id: str | None, settings: dict[str, Any] | None = None) -> int:
    normalized = normalize_agent_id(agent_id or "")
    settings = settings or ready_dispatch_settings(config)
    default_capacity: int | None = None
    raw_default_capacity = settings.get("max_tasks_per_agent")
    if raw_default_capacity not in (None, ""):
        try:
            default_capacity = max(1, int(raw_default_capacity))
        except (TypeError, ValueError):
            default_capacity = None
    display_name = display_name_for(config, normalized)
    overrides = settings.get("max_tasks_per_agent_by_agent", {}) or {}
    for key in (normalized, display_name):
        if key in overrides:
            try:
                return max(1, int(overrides[key]))
            except (TypeError, ValueError):
                pass
    slot_count = len(logical_worker_slot_ids(config, normalized))
    if slot_count:
        return max(default_capacity or 0, slot_count)
    return default_capacity or 1


def dispatch_weight_mapping(settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = settings or {}
    mapping = settings.get("target_workload") or settings.get("agent_workload_weights") or {}
    return mapping if isinstance(mapping, dict) else {}


def dispatch_weight_for_agent(config: dict[str, Any], agent_id: str | None, settings: dict[str, Any] | None = None) -> int:
    mapping = dispatch_weight_mapping(settings)
    if not mapping:
        return 1
    normalized = normalize_agent_id(agent_id or "")
    display_name = display_name_for(config, normalized)
    for key in (display_name, normalized):
        if key in mapping:
            try:
                return max(0, int(mapping[key]))
            except (TypeError, ValueError):
                return 0
    return 0


def weighted_dispatch_agent_ids(config: dict[str, Any], settings: dict[str, Any] | None = None) -> list[str]:
    settings = settings or ready_dispatch_settings(config)
    base_agent_ids = dispatch_loop_agent_ids(config)
    if not dispatch_weight_mapping(settings):
        return base_agent_ids

    weighted = [
        (agent_id, dispatch_weight_for_agent(config, agent_id, settings))
        for agent_id in base_agent_ids
    ]
    weighted = [(agent_id, weight) for agent_id, weight in weighted if weight > 0]
    if not weighted:
        return base_agent_ids

    divisor = 0
    for _agent_id, weight in weighted:
        divisor = weight if divisor == 0 else math.gcd(divisor, weight)
    normalized = [(agent_id, max(1, weight // max(1, divisor))) for agent_id, weight in weighted]
    total = sum(weight for _agent_id, weight in normalized)
    current = {agent_id: 0 for agent_id, _weight in normalized}
    sequence: list[str] = []
    for _ in range(total):
        for agent_id, weight in normalized:
            current[agent_id] += weight
        selected = max(
            normalized,
            key=lambda item: (current[item[0]], item[1], -base_agent_ids.index(item[0])),
        )[0]
        sequence.append(selected)
        current[selected] -= total
    return sequence


def select_dispatch_agent_id(
    config: dict[str, Any],
    state: dict[str, Any],
    agent_id: str | None,
    active_statuses: set[str],
    provider_report: dict[str, Any] | None = None,
) -> str | None:
    normalized = normalize_agent_id(agent_id or "")
    settings = ready_dispatch_settings(config)
    slot_ids = logical_worker_slot_ids(config, normalized)
    if not slot_ids:
        return normalized
    active_slots = {
        normalize_agent_id(str(worker.get("agent_id") or ""))
        for worker in state.get("workers", {}).values()
        if worker.get("status") in active_statuses
    }
    for slot_id in slot_ids:
        if slot_id in active_slots:
            continue
        quota_limit = quota_group_concurrency_limit(config, slot_id, settings)
        quota_group = agent_quota_group_id(config, slot_id)
        if quota_limit and quota_group:
            quota_counts = active_quota_group_counts(config, state, active_statuses)
            if quota_counts.get(quota_group, 0) >= quota_limit:
                continue
        if agent_auto_dispatch_block_reason(config, state, slot_id, provider_report):
            continue
        return slot_id
    return None


def build_request(
    config: dict[str, Any],
    event: dict[str, Any],
    *,
    agent_id_override: str | None = None,
) -> DeliveryRequest:
    logical_agent = agent_config_for(config, event["target_agent"])
    agent = agent_config_for(config, agent_id_override or event["target_agent"])
    metadata = dict(event.get("metadata", {}) or {})
    model_preference = resolve_agent_model_preference(config, agent)
    if model_preference and "model_preference" not in metadata:
        metadata["model_preference"] = model_preference
    logical_agent_id = normalize_agent_id(str(logical_agent.get("id") or event.get("target_agent") or ""))
    if logical_agent_id and "logical_agent_id" not in metadata:
        metadata["logical_agent_id"] = logical_agent_id
    if agent_id_override:
        metadata["dispatch_slot_id"] = agent["id"]
        metadata["dispatch_slot"] = agent.get("slot_id") or agent["id"]
        metadata["target_display_name"] = event.get("target_display_name") or display_name_for(config, logical_agent_id)
    context_files = event.get("context_files")
    if context_files is None:
        context_files = execution_context_files(config, event.get("task_id"))
    return DeliveryRequest(
        agent_id=agent["id"],
        provider=agent.get("provider", agent["id"]),
        delivery_mode=config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
            "delivery_mode", agent.get("adapter", "file_inbox")
        ),
        message=event["message"],
        task_id=event.get("task_id"),
        reason=event.get("reason"),
        context_files=context_files,
        target_files=event.get("target_files", []),
        metadata=metadata,
    )


def queue_status(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    return queue_event_record(state, event_id)


def request_snapshot(request: DeliveryRequest) -> dict[str, Any]:
    return {
        "agent_id": request.agent_id,
        "provider": request.provider,
        "delivery_mode": request.delivery_mode,
        "message": request.message,
        "task_id": request.task_id,
        "reason": request.reason,
        "context_files": list(request.context_files),
        "target_files": list(request.target_files),
        "metadata": dict(request.metadata),
    }


def request_from_snapshot(snapshot: dict[str, Any]) -> DeliveryRequest:
    return DeliveryRequest(
        agent_id=snapshot["agent_id"],
        provider=snapshot["provider"],
        delivery_mode=snapshot["delivery_mode"],
        message=snapshot["message"],
        task_id=snapshot.get("task_id"),
        reason=snapshot.get("reason"),
        context_files=list(snapshot.get("context_files", []) or []),
        target_files=list(snapshot.get("target_files", []) or []),
        metadata=dict(snapshot.get("metadata", {}) or {}),
    )


WORKER_WORKTREE_EXECUTION_REASONS = [
    REASON_OWNED_READY,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_FINALIZE,
    REASON_REVIEW_READY,
    "chair_review:*",
]


def worker_worktree_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktrees")
    settings = raw if isinstance(raw, dict) else {}
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    return {
        "enabled": bool(settings.get("enabled", False)),
        "root": str(settings.get("root") or "/tmp/pantheon-worker-worktrees"),
        "base_ref": str(settings.get("base_ref") or f"origin/{branch_workflow.get('dev_branch') or 'dev'}"),
        "reuse_existing": bool(settings.get("reuse_existing", True)),
        "execution_reasons": list(settings.get("execution_reasons") or WORKER_WORKTREE_EXECUTION_REASONS),
    }


def _task_id_slug(task_id: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(task_id or "").lower()).strip("-")
    return slug or "unknown-task"


def worker_task_branch(config: dict[str, Any], task_id: str | None) -> str:
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    normalized_task_id = str(task_id or "").strip()
    return f"{prefix}{normalized_task_id}" if normalized_task_id else f"{prefix}unknown-task"


def _worker_worktree_base_root(config: dict[str, Any], settings: dict[str, Any]) -> Path:
    repo_root = config_path(config, "status_file").parents[0]
    configured = Path(os.path.expanduser(str(settings.get("root") or "")))
    if not configured.is_absolute():
        configured = repo_root / configured
    return configured.resolve()


def worker_task_worktree_path(config: dict[str, Any], task_id: str | None, settings: dict[str, Any] | None = None) -> Path:
    active_settings = settings or worker_worktree_settings(config)
    repo_root = config_path(config, "status_file").parents[0]
    repo_slug = re.sub(r"[^a-z0-9]+", "-", repo_root.name.lower()).strip("-") or "repo"
    return _worker_worktree_base_root(config, active_settings) / repo_slug / _task_id_slug(task_id)


def worker_worktree_reason_enabled(reason: str | None, settings: dict[str, Any]) -> bool:
    normalized_reason = str(reason or "")
    for pattern in settings.get("execution_reasons", []):
        if fnmatch.fnmatchcase(normalized_reason, str(pattern)):
            return True
    return False


def worker_workspace_task_id(request: DeliveryRequest) -> str | None:
    metadata_task_id = str(request.metadata.get("workspace_task_id") or "").strip()
    task_id = metadata_task_id or str(request.task_id or "").strip()
    return task_id or None


def _git_worktree_records(repo_root: Path) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    if current:
        records.append(current)
    return records


def _worktree_record_branch(record: dict[str, str]) -> str:
    branch = str(record.get("branch") or "").strip()
    if branch.startswith("refs/heads/"):
        return branch[len("refs/heads/") :]
    return branch


def _existing_worktree_for_branch(repo_root: Path, branch: str, *, exclude_root: bool) -> Path | None:
    resolved_repo_root = repo_root.resolve()
    for record in _git_worktree_records(repo_root):
        if _worktree_record_branch(record) != branch:
            continue
        path_value = record.get("worktree")
        if not path_value:
            continue
        path = Path(path_value).resolve()
        if exclude_root and path == resolved_repo_root:
            continue
        return path
    return None


def _branch_checked_out_in_root(repo_root: Path, branch: str) -> bool:
    for record in _git_worktree_records(repo_root):
        path_value = record.get("worktree")
        if not path_value:
            continue
        if Path(path_value).resolve() == repo_root.resolve():
            return _worktree_record_branch(record) == branch
    return False


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def _create_worker_worktree(repo_root: Path, path: Path, branch: str, base_ref: str) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        return False, f"Worker worktree path already exists and is not empty: {path}"

    remote_ref = f"refs/remotes/origin/{branch}"
    if _git_ref_exists(repo_root, f"refs/heads/{branch}"):
        command = ["git", "worktree", "add", str(path), branch]
    elif _git_ref_exists(repo_root, remote_ref):
        command = ["git", "worktree", "add", "-b", branch, str(path), f"origin/{branch}"]
    else:
        command = ["git", "worktree", "add", "-b", branch, str(path), base_ref]

    proc = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        return False, f"Failed to create worker worktree {path} for {branch}: {details}"
    return True, None


# Orchestrator-managed per-task scratch that a worker routinely dirties inside its
# own worktree (the task brief gets annotated, the review artifact rewritten). The
# supervisor regenerates these on dispatch, so a reused worktree whose ONLY dirt is
# here is safe to restore-and-reuse. Blocking dispatch on this churn is what jams
# the whole fleet once worktrees are reused (every tick re-blocks, nothing runs).
_REUSABLE_DIRTY_PREFIXES = (
    ".orchestrator/task-briefs/",
    ".orchestrator/reviews/",
)


def _classify_worktree_dirt(porcelain_status: str) -> tuple[str, list[str]]:
    """Classify reused-worktree dirtiness from `git status --porcelain` output.

    Returns (classification, paths):
      'clean'        - no tracked/staged changes; paths is []
      'scratch_only' - every change is orchestrator-managed scratch
                       (see _REUSABLE_DIRTY_PREFIXES); paths lists them
      'real'         - at least one change outside scratch -> must block dispatch
    """
    lines = [ln for ln in porcelain_status.splitlines() if ln.strip()]
    if not lines:
        return "clean", []
    paths: list[str] = []
    for ln in lines:
        body = ln[3:] if len(ln) > 3 else ln.strip()
        # rename/copy lines render as "old -> new"; the new path is what exists.
        path = body.split(" -> ")[-1].strip().strip('"')
        if path:
            paths.append(path)
    if any(not p.startswith(_REUSABLE_DIRTY_PREFIXES) for p in paths):
        return "real", []
    return "scratch_only", paths


def _restore_reusable_scratch(worktree_path: Path, paths: list[str]) -> None:
    """Restore orchestrator scratch paths to HEAD and drop untracked scratch."""
    if paths:
        subprocess.run(
            ["git", "checkout", "-q", "HEAD", "--", *sorted(set(paths))],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    subprocess.run(
        ["git", "clean", "-fq", "--", *_REUSABLE_DIRTY_PREFIXES],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _refresh_reused_worker_worktree(repo_root: Path, worktree_path: Path, base_ref: str) -> tuple[bool, str]:
    """Fast-forward a reused worker worktree to the current base ref tip.

    Reused worktrees may carry the worker's per-task branch from days ago,
    which means their copy of `scripts/ai_status.py` / supervisor / skills can
    be older than the supervisor root. That stale snapshot has bypassed gates
    such as ORCH-CLOSEOUT-MERGE-GATE (require_merged_pr). Refresh on lease so
    the worker always sees current control-plane code.

    Strategy: fetch + `git merge --ff-only origin/<base>`. Never auto-resolve
    a real merge — if the branch genuinely diverged, leave it for the worker
    to handle. Dirty reused worktrees are blocked before dispatch so workers
    cannot inherit unrelated staged or tracked changes.
    """
    base = base_ref.split("/", 1)[1] if base_ref.startswith("origin/") else base_ref
    fetch_proc = subprocess.run(
        ["git", "fetch", "origin", base, "--quiet"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if fetch_proc.returncode != 0:
        details = (fetch_proc.stderr or fetch_proc.stdout or "").strip()
        return False, f"fetch_failed: {details}"

    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    scratch_restored = False
    if status_proc.returncode == 0 and status_proc.stdout.strip():
        classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout)
        if classification == "real":
            return False, "skipped_dirty_worktree"
        # Only orchestrator-managed scratch is dirty: restore it and reuse the
        # worktree instead of jamming dispatch on regenerable bookkeeping churn.
        _restore_reusable_scratch(worktree_path, scratch_paths)
        verify_proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if verify_proc.returncode == 0 and verify_proc.stdout.strip():
            return False, "skipped_dirty_worktree"
        scratch_restored = True

    merge_proc = subprocess.run(
        ["git", "merge", "--ff-only", f"origin/{base}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge_proc.returncode == 0:
        head_proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        head = (head_proc.stdout or "").strip()
        suffix = "+scratch_restored" if scratch_restored else ""
        return True, (f"ff_to_{head}{suffix}" if head else f"ff_ok{suffix}")
    details = (merge_proc.stderr or merge_proc.stdout or "").strip().splitlines()[0] if (merge_proc.stderr or merge_proc.stdout) else "unknown"
    return False, f"non_fast_forward: {details}"


def _task_brief_context_candidates(task_id: str | None, rel_context_path: str) -> list[str]:
    normalized = rel_context_path.replace("\\", "/").strip()
    candidates = [normalized]
    if ".orchestrator/task-briefs/" in normalized and task_id:
        hyphen_slug = _task_id_slug(task_id)
        underscore_slug = hyphen_slug.replace("-", "_")
        for slug in (underscore_slug, hyphen_slug, normalize_agent_id(task_id)):
            if slug:
                candidates.append(f".orchestrator/task-briefs/{slug}.md")
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _generated_worker_task_brief(config: dict[str, Any], task_id: str | None) -> str:
    task = task_index_from_status(config, load_status(config)).get(str(task_id or ""))
    if not task:
        return "\n".join(
            [
                f"# Task Brief: {task_id or 'unknown-task'}",
                "",
                "Generated in the worker workspace because the supervisor root did not have a task brief file.",
                "",
            ]
        )
    return "\n".join(
        [
            f"# Task Brief: {task.get('id') or task_id}",
            "",
            "Generated in the worker workspace because the supervisor root did not have a task brief file.",
            "",
            "## Task",
            f"- Title: {task.get('title') or '-'}",
            f"- Status: {task.get('status') or '-'}",
            f"- Owner: {task.get('owner') or '-'}",
            f"- Reviewer: {task.get('reviewer') or '-'}",
            f"- Next: {task.get('next') or '-'}",
            "",
            "## Summary",
            str(task.get("summary_zh") or "-"),
            "",
        ]
    )


def materialize_worker_context_files(
    config: dict[str, Any],
    request: DeliveryRequest,
    workspace_path: Path,
) -> list[str]:
    """Copy generated task briefs into isolated worktrees before worker launch."""
    if not request.context_files:
        return []
    status_root = config_path(config, "status_file").parents[0].resolve()
    materialized: list[str] = []
    for rel_context_path in request.context_files:
        rel_value = str(rel_context_path or "").strip().replace("\\", "/")
        if not rel_value or Path(rel_value).is_absolute():
            continue
        if ".orchestrator/task-briefs/" not in rel_value:
            continue
        destination = workspace_path / rel_value
        destination.parent.mkdir(parents=True, exist_ok=True)
        copied = False
        for candidate in _task_brief_context_candidates(request.task_id, rel_value):
            source = status_root / candidate
            if not source.exists() or not source.is_file():
                continue
            shutil.copy2(source, destination)
            copied = True
            break
        if not copied:
            destination.write_text(_generated_worker_task_brief(config, request.task_id), encoding="utf-8")
        materialized.append(rel_value)
    if materialized:
        request.metadata["materialized_context_files"] = materialized
    return materialized


def prepare_worker_workspace(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    target_agent: str | None,
) -> tuple[bool, str | None]:
    settings = worker_worktree_settings(config)
    if not settings.get("enabled"):
        return True, None
    if not worker_worktree_reason_enabled(request.reason, settings):
        return True, None
    workspace_task_id = worker_workspace_task_id(request)
    if not workspace_task_id:
        return True, None
    if request.metadata.get("workspace_path"):
        return True, None

    repo_root = config_path(config, "status_file").parents[0].resolve()
    branch = worker_task_branch(config, workspace_task_id)
    worktree_path = worker_task_worktree_path(config, workspace_task_id, settings)
    reused = False

    if settings.get("reuse_existing", True):
        existing = _existing_worktree_for_branch(repo_root, branch, exclude_root=True)
        if existing:
            worktree_path = existing
            reused = True
            refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                repo_root, worktree_path, str(settings.get("base_ref") or "origin/dev")
            )
            write_activity_log(
                config,
                {
                    "type": "worker_worktree_refreshed",
                    "task_id": request.task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "refresh_ok": refresh_ok,
                    "refresh_status": refresh_status,
                },
            )
            if not refresh_ok and refresh_status == "skipped_dirty_worktree":
                message = (
                    f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                    f"reused worktree {worktree_path} has dirty tracked or staged changes. "
                    "Clean or remove that worktree before dispatch."
                )
                write_activity_log(
                    config,
                    {
                        "type": "dispatch_blocked_worktree_lease",
                        "task_id": request.task_id,
                        "workspace_task_id": workspace_task_id,
                        "target_agent": target_agent,
                        "queue_event_id": queue_event_id,
                        "message": message,
                        "workspace_branch": branch,
                        "workspace_path": str(worktree_path),
                        "refresh_status": refresh_status,
                    },
                )
                return False, message

    if not reused:
        if _branch_checked_out_in_root(repo_root, branch):
            message = (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"branch {branch} is currently checked out in supervisor root {repo_root}. "
                "Move the supervisor root back to dev or finish that root task branch first."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                },
            )
            return False, message
        created, error = _create_worker_worktree(repo_root, worktree_path, branch, str(settings.get("base_ref") or "origin/dev"))
        if not created:
            message = error or f"Failed to create worker worktree for {workspace_task_id}."
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                },
            )
            return False, message

    request.metadata.update(
        {
            "workspace_mode": "isolated_worktree",
            "workspace_path": str(worktree_path),
            "workspace_branch": branch,
            "status_root": str(repo_root),
        }
    )
    materialized_context_files = materialize_worker_context_files(config, request, worktree_path)
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    leases[workspace_task_id] = {
        "task_id": request.task_id,
        "workspace_task_id": workspace_task_id,
        "branch": branch,
        "path": str(worktree_path),
        "status_root": str(repo_root),
        "last_queue_event_id": queue_event_id,
        "last_target_agent": target_agent,
        "last_used_at": utc_now(),
        "materialized_context_files": materialized_context_files,
    }
    write_activity_log(
        config,
        {
            "type": "worker_worktree_reused" if reused else "worker_worktree_allocated",
            "task_id": request.task_id,
            "workspace_task_id": workspace_task_id,
            "target_agent": target_agent,
            "queue_event_id": queue_event_id,
            "workspace_branch": branch,
            "workspace_path": str(worktree_path),
            "status_root": str(repo_root),
        },
    )
    return True, None


def worker_tree_guard_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_tree_guard")
    settings = raw if isinstance(raw, dict) else {}
    blocking_globs = settings.get("blocking_globs")
    auto_restore_globs = settings.get("auto_restore_globs")
    return {
        "enabled": bool(settings.get("enabled", False)),
        "mode": str(settings.get("mode") or "warn").strip().lower(),
        "blocking_globs": list(blocking_globs)
        if isinstance(blocking_globs, list)
        else [
            ".orchestrator/supervisor.py",
            "supervisor.py",
            ".orchestrator/skills/**",
            "branch-strategy.md",
            "docs/conventions/GIT_WORKFLOW.md",
            "config*.json",
            ".orchestrator/config*.json",
            "docs/**",
        ],
        "auto_restore_globs": list(auto_restore_globs)
        if isinstance(auto_restore_globs, list)
        else [
            "ai-activity-log.jsonl",
            "ai-status.json",
            "current-work.md",
            "dashboard-bundle.json",
            "docs-site/**",
        ],
        "auto_restore_enabled": bool(settings.get("auto_restore_enabled", False)),
    }


def _git_dirty_entries(cwd: Path | None = None) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=cwd or THIS_DIR.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    parts = proc.stdout.split("\0")
    index = 0
    while index < len(parts):
        raw = parts[index]
        index += 1
        if not raw:
            continue
        status = raw[:2]
        path = raw[3:] if len(raw) > 3 else ""
        if not path:
            continue
        entries.append({"status": status, "path": path.replace("\\", "/")})
        if status[:1] in {"R", "C"} and index < len(parts):
            index += 1
    return entries


def _path_matches_any_glob(path: str, patterns: list[Any]) -> bool:
    normalized = path.replace("\\", "/")
    basename = Path(normalized).name
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip().replace("\\", "/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        if "/" not in pattern and fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


def check_worker_tree_clean(
    config: dict[str, Any],
    *,
    run_id: str | None,
    task_id: str | None,
    target_agent: str | None,
    queue_event_id: str | None,
    cwd: Path | None = None,
) -> tuple[bool, str | None]:
    settings = worker_tree_guard_settings(config)
    if not settings.get("enabled"):
        return True, None
    mode = str(settings.get("mode") or "warn").lower()
    if mode in {"off", "disabled", "false"}:
        return True, None

    dirty_entries = _git_dirty_entries(cwd)
    if not dirty_entries:
        return True, None

    blocking_globs = settings.get("blocking_globs") or []
    blocking_entries = [
        entry
        for entry in dirty_entries
        if _path_matches_any_glob(entry["path"], blocking_globs)
    ]
    if not blocking_entries:
        return True, None

    display_entries = [f"{entry['status']} {entry['path']}" for entry in blocking_entries[:20]]
    remaining = max(0, len(blocking_entries) - len(display_entries))
    suffix = f" (+{remaining} more)" if remaining else ""
    message = (
        "Worker tree guard found dirty high-fragility files before dispatch; "
        "anchor or close out the existing task-owned diff before yielding: "
        + "; ".join(display_entries)
        + suffix
    )
    activity_type = "dispatch_blocked_dirty_tree" if mode == "block" else "dispatch_dirty_tree_warning"
    write_activity_log(
        config,
        {
            "type": activity_type,
            "task_id": task_id,
            "target_agent": target_agent,
            "message": message,
            "queue_event_id": queue_event_id,
            "worker_run_id": run_id,
            "blocking_paths": [entry["path"] for entry in blocking_entries],
            "mode": mode,
            "workspace_path": str(cwd) if cwd else None,
        },
    )
    return mode != "block", message


def start_worker_for_request(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    attempt_count: int,
    event_id_for_log: str | None,
    parent_run_id: str | None = None,
    delivery_mode_override: str | None = None,
    activity_type: str = "worker_started",
    activity_message: str | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    agent = agent_config_for(config, request.agent_id)
    adapter_name = delivery_mode_override or agent.get("adapter", "file_inbox")
    adapter = build_adapter(adapter_name, config=config, provider_capabilities=provider_report)
    result = adapter.deliver(request)
    if not result.ok:
        failure_worker = {
            "provider": request.provider,
            "agent_id": request.agent_id,
            "task_id": request.task_id,
            "queue_event_id": event_id_for_log,
            "run_id": None,
            "log_path": result.log_path,
        }
        failure_summary = summarize_failure_reason(result.error or result.notes or "Worker delivery failed.", request.provider)
        raw_ref = write_failure_evidence(
            config,
            worker=failure_worker,
            reason=result.error or result.notes or "Worker delivery failed.",
            failure_kind=failure_summary.get("kind"),
        )
        write_activity_log(
            config,
            {
                "type": "worker_failed",
                "task_id": request.task_id,
                "target_agent": display_name_for(config, agent["id"]),
                "delivery_mode": result.mode,
                "message": failure_summary.get("summary") or "Worker delivery failed.",
                "queue_event_id": event_id_for_log,
                "parent_run_id": parent_run_id,
                "raw_ref": raw_ref,
            },
        )
        return False, failure_summary.get("summary") or result.error or result.notes or "Worker delivery failed.", None

    worker_run_id = result.run_id or new_runtime_id(request.provider)
    logical_agent_id = str(request.metadata.get("logical_agent_id") or agent["id"])
    dispatch_slot_id = str(request.metadata.get("dispatch_slot_id") or "")
    now_dt = datetime.now(timezone.utc)
    now = _isoformat_utc(now_dt)
    result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
    state.setdefault("workers", {})[worker_run_id] = {
        "run_id": worker_run_id,
        "provider": request.provider,
        "agent_id": agent["id"],
        "logical_agent_id": logical_agent_id,
        "dispatch_slot_id": dispatch_slot_id or None,
        "dispatch_slot": request.metadata.get("dispatch_slot"),
        "quota_group": provider_dispatch_group_id(config, request.provider),
        "task_id": request.task_id,
        "session_id": result.session_id,
        "mode": result.mode,
        "status": "manual_pending" if result.manual_confirmation_required and not result.auto_delivered else "running",
        "last_event_at": now,
        "last_heartbeat_at": None,
        "lease_acquired_at": now,
        "lease_expires_at": worker_lease_expiry(config, now_dt),
        "deferred_action": None,
        "resume_token": result.resume_token or result.session_id,
        "pr_url": normalize_pr_url(config, result.pr_url),
        "session_url": result.session_url,
        "attempt_count": attempt_count,
        "queue_event_id": queue_event_id,
        "command": result.command,
        "log_path": result.log_path,
        "payload_path": result.payload_path,
        "workspace_mode": request.metadata.get("workspace_mode"),
        "workspace_path": request.metadata.get("workspace_path"),
        "workspace_branch": request.metadata.get("workspace_branch"),
        "status_root": request.metadata.get("status_root"),
        "pid": result.pid,
        "heartbeat_path": result_metadata.get("heartbeat_path"),
        "runner_status_path": result_metadata.get("runner_status_path"),
        "notes": result.notes,
        "metadata": result_metadata,
        "request_snapshot": request_snapshot(request),
        "parent_run_id": parent_run_id,
        "retry_count": 0,
        "next_retry_at": None,
        "last_error": None,
    }
    record_worker_runtime_measurement(
        config,
        state,
        "worker_started",
        {
            "workers_started": 1,
            "queue_leases_started": 1 if queue_event_id else 0,
        },
        details={
            "worker_run_id": worker_run_id,
            "queue_event_id": queue_event_id,
            "task_id": request.task_id,
            "agent_id": agent["id"],
            "provider": request.provider,
            "lease_expires_at": state["workers"][worker_run_id].get("lease_expires_at"),
        },
        emit_activity=False,
    )
    # Persist immediately after launch so a supervisor crash cannot orphan
    # a live worker before the end-of-tick state save.
    save_runtime_state(config, state)
    write_activity_log(
        config,
        {
            "type": activity_type,
            "task_id": request.task_id,
            "target_agent": display_name_for(config, agent["id"]),
            "provider": request.provider,
            "delivery_mode": result.mode,
            "message": activity_message or f"Worker started via {result.adapter}: {request.reason}",
            "queue_event_id": event_id_for_log,
            "worker_run_id": worker_run_id,
            "parent_run_id": parent_run_id,
            "command": result.command,
            "log_path": result.log_path,
            "payload_path": result.payload_path,
            "workspace_mode": request.metadata.get("workspace_mode"),
            "workspace_path": request.metadata.get("workspace_path"),
            "workspace_branch": request.metadata.get("workspace_branch"),
            "status_root": request.metadata.get("status_root"),
        },
    )
    return True, worker_run_id, result.as_dict()


def process_queue(config: dict[str, Any], state: dict[str, Any], provider_report: dict[str, Any]) -> bool:
    changed = False
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for event in load_event_queue(config):
        event_id = event.get("event_id")
        if not event_id:
            continue
        existing_record = state.get("queue", {}).get("events", {}).get(event_id, {})
        related_workers = [
            worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id
        ]
        if queue_event_is_orphaned(config, event, existing_record, related_workers):
            continue
        record = queue_status(state, event_id)
        if record.get("status") in {"started", "manual_pending", "completed", "failed"}:
            continue
        if record.get("status") == "retry_backoff":
            next_retry_at = _parse_iso_utc(str(record.get("next_retry_at") or ""))
            if next_retry_at is not None and next_retry_at > datetime.now(timezone.utc):
                continue
        active_worker = next(
            (
                worker
                for worker in state.get("workers", {}).values()
                if worker.get("queue_event_id") == event_id and worker.get("status") in active_statuses
            ),
            None,
        )
        if active_worker:
            desired_status = "manual_pending" if active_worker.get("status") in {"manual_pending", "waiting_approval"} else "started"
            if record.get("status") != desired_status or record.get("run_id") != active_worker.get("run_id"):
                record["status"] = desired_status
                record["run_id"] = active_worker.get("run_id") or event_id
                record["lease_owner"] = active_worker.get("run_id") or event_id
                record["lease_acquired_at"] = record.get("lease_acquired_at") or active_worker.get("lease_acquired_at") or utc_now()
                record["lease_expires_at"] = active_worker.get("lease_expires_at") or queue_lease_expiry(config)
                record["processed_at"] = record.get("processed_at") or utc_now()
                sync_dispatched_task_status(config, event)
                changed = True
            continue
        skip_message = stale_dispatch_skip_message(config, event, task_map)
        if skip_message:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "stale_dispatch_event"
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "message": skip_message,
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue
        request = build_request(config, event)
        request_provider = getattr(request, "provider", event.get("provider"))
        pause_entry = current_provider_dispatch_pause(state, request_provider, config)
        if pause_entry:
            pause_summary = str(pause_entry.get("summary") or pause_entry.get("reason") or "capacity guardrail active.")
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = (
                f"Dispatch paused for provider {request_provider} until {pause_entry.get('blocked_until')}: "
                f"{pause_summary}"
            )
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": request_provider,
                    "message": record["error"],
                    "queue_event_id": event_id,
                    "raw_ref": pause_entry.get("raw_ref"),
                },
            )
            changed = True
            continue
        request_agent_id = str(getattr(request, "agent_id", event.get("target_agent")) or "")
        auto_block_reason = agent_auto_dispatch_block_reason(config, state, request_agent_id, provider_report)
        if auto_block_reason:
            if auto_dispatch_block_is_temporary_capacity(auto_block_reason):
                record["status"] = "pending"
                record["last_wait_reason"] = f"Auto dispatch waiting for {request_agent_id}: {auto_block_reason}"
                record["capacity_wait_count"] = int(record.get("capacity_wait_count", 0) or 0) + 1
                record["last_capacity_wait_at"] = utc_now()
                reason_changed = record.get("last_capacity_wait_reason") != auto_block_reason
                record["last_capacity_wait_reason"] = auto_block_reason
                record_worker_runtime_measurement(
                    config,
                    state,
                    "dispatch_capacity_wait",
                    {"capacity_pending_queue_events": 1},
                    details={
                        "queue_event_id": event_id,
                        "task_id": event.get("task_id"),
                        "agent_id": request_agent_id,
                        "reason": auto_block_reason,
                        "capacity_wait_count": record["capacity_wait_count"],
                    },
                    emit_activity=reason_changed,
                )
                changed = True
                continue
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = f"Auto dispatch unavailable for {request_agent_id}: {auto_block_reason}"
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "provider": request.provider,
                    "message": record["error"],
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue
        dispatch_agent_id = select_dispatch_agent_id(config, state, request_agent_id, active_statuses, provider_report)
        if dispatch_agent_id is None:
            record["status"] = "pending"
            record["last_wait_reason"] = f"All worker slots for {request_agent_id} are busy or dispatch-paused."
            changed = True
            continue
        if dispatch_agent_id != request_agent_id:
            request = build_request(config, event, agent_id_override=dispatch_agent_id)
        workspace_ok, workspace_message = prepare_worker_workspace(
            config,
            state,
            request,
            queue_event_id=str(event_id or ""),
            target_agent=str(event.get("target_display_name") or event.get("target_agent") or ""),
        )
        if not workspace_ok:
            record["status"] = "pending"
            record["last_wait_reason"] = workspace_message
            record["worktree_lease_blocked_at"] = utc_now()
            changed = True
            continue
        request_metadata = getattr(request, "metadata", {}) if hasattr(request, "metadata") else {}
        workspace_path = request_metadata.get("workspace_path") if isinstance(request_metadata, dict) else None
        guard_ok, guard_message = check_worker_tree_clean(
            config,
            run_id=str(event_id or ""),
            task_id=str(event.get("task_id") or ""),
            target_agent=str(event.get("target_display_name") or event.get("target_agent") or ""),
            queue_event_id=str(event_id or ""),
            cwd=Path(str(workspace_path)) if workspace_path else None,
        )
        if not guard_ok:
            record["status"] = "pending"
            record["last_wait_reason"] = guard_message
            record["dirty_tree_guard_at"] = utc_now()
            changed = True
            continue
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["last_attempt_at"] = utc_now()
        ok, outcome, delivery = start_worker_for_request(
            config,
            state,
            provider_report,
            request,
            queue_event_id=event_id,
            attempt_count=record["attempt_count"],
            event_id_for_log=event_id,
        )
        if not ok:
            failure_worker = {
                "provider": request.provider,
                "agent_id": request.agent_id,
                "task_id": request.task_id,
                "queue_event_id": event_id,
                "run_id": record.get("run_id"),
                "retry_count": max(0, int(record.get("attempt_count", 0)) - 1),
            }
            failure_reason = str(outcome or "")
            failure = classify_worker_failure(config, failure_worker, failure_reason)
            failure_summary = summarize_failure_reason(failure_reason, request.provider)
            raw_ref = write_failure_evidence(
                config,
                worker=failure_worker,
                reason=failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_count = record_task_failure_streak(
                state,
                failure_worker,
                failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_kind = str(failure.get("kind") or "")
            if should_pause_dispatch_for_failure_kind(failure_kind):
                mark_provider_dispatch_paused(
                    config,
                    state,
                    request.provider,
                    failure_reason,
                    task_id=str(request.task_id or ""),
                    failure_kind=str(failure.get("kind") or ""),
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                )
            if is_retryable_capacity_failure_kind(failure_kind):
                retry = worker_retry_settings(config, request.provider)
                retry_count = int(record.get("retry_count", 0))
                max_attempts = int(retry.get("max_attempts", 5))
                if retry_count < max_attempts:
                    schedule_queue_event_retry(
                        config,
                        record,
                        provider=request.provider,
                        reason=failure_summary.get("summary") or failure_reason,
                    )
                    write_activity_log(
                        config,
                        {
                            "type": "dispatch_retry_scheduled",
                            "provider": request.provider,
                            "task_id": request.task_id,
                            "queue_event_id": event_id,
                            "message": (
                                f"Transient dispatch failure detected ({failure.get('label')}); "
                                f"retry {record.get('retry_count')} scheduled at {record.get('next_retry_at')}: "
                                f"{failure_summary.get('summary') or failure_reason}"
                            ),
                            "next_retry_at": record.get("next_retry_at"),
                            "raw_ref": raw_ref,
                        },
                    )
                    changed = True
                    continue
            reassigned_to = maybe_reassign_task_after_worker_failure(
                config,
                state,
                failure_worker,
                failure_summary.get("summary") or failure_reason,
                terminal=True,
                force=is_terminal_quota_failure_kind(failure_kind),
                failure_count=failure_count,
            )
            if reassigned_to:
                record["status"] = "completed"
                record["processed_at"] = utc_now()
                record["error"] = failure_summary.get("summary") or ""
                if raw_ref:
                    record["raw_ref"] = raw_ref
                changed = True
                continue
            record["status"] = "failed"
            record["error"] = failure_summary.get("summary") or outcome
            if raw_ref:
                record["raw_ref"] = raw_ref
            record["processed_at"] = utc_now()
            changed = True
            continue

        worker_run_id = outcome or event_id
        queue_started_at = datetime.now(timezone.utc)
        record["status"] = "manual_pending" if delivery and delivery.get("manual_confirmation_required") and not delivery.get("auto_delivered") else "started"
        record["run_id"] = worker_run_id
        record["lease_owner"] = worker_run_id
        record["lease_acquired_at"] = _isoformat_utc(queue_started_at)
        record["lease_expires_at"] = queue_lease_expiry(config, queue_started_at)
        record["processed_at"] = _isoformat_utc(queue_started_at)
        record.pop("last_wait_reason", None)
        sync_dispatched_task_status(config, event)
        changed = True
    return changed


def pid_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return False
    except ChildProcessError:
        pass
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            parts = proc_stat.read_text(encoding="utf-8", errors="ignore").split()
        except OSError:
            parts = []
        if len(parts) >= 3 and parts[2] == "Z":
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# Worker wakeup template always embeds `auto worker 身分是：<DisplayName>` in argv;
# scan /proc to recover the truth when state["workers"] bookkeeping drifts.
WORKER_AGENT_CMDLINE_MARKER = re.compile(r"auto worker 身分是：([A-Za-z][A-Za-z0-9_]*)")


def scan_live_worker_pids_by_agent(proc_root: Path | None = None) -> dict[str, list[int]]:
    """Return live worker PIDs grouped by agent display name parsed from /proc/*/cmdline."""
    root = proc_root if proc_root is not None else Path("/proc")
    result: dict[str, list[int]] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return result
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == self_pid:
            continue
        cmdline_path = entry / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        match = WORKER_AGENT_CMDLINE_MARKER.search(cmdline)
        if not match:
            continue
        agent = match.group(1)
        result.setdefault(agent, []).append(pid)
    return result


def active_worker_refs_for_agent_id(
    state: dict[str, Any],
    agent_id: str | None,
    active_statuses: set[str],
) -> list[str]:
    normalized_agent = normalize_agent_id(agent_id or "")
    if not normalized_agent:
        return []
    normalized_statuses = {str(status or "").strip().lower() for status in active_statuses}
    refs: list[str] = []
    for worker in (state.get("workers", {}) or {}).values():
        worker_agent_id = normalize_agent_id(str(worker.get("agent_id") or ""))
        if worker_agent_id != normalized_agent:
            continue
        worker_status = str(worker.get("status") or "").strip().lower()
        if worker_status not in normalized_statuses:
            continue
        pid = worker.get("pid")
        if pid:
            refs.append(str(pid))
            continue
        run_id = str(worker.get("run_id") or "").strip()
        if run_id:
            refs.append(run_id)
    return sorted(set(refs))


def terminate_worker_pid(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def normalize_pr_url(config: dict[str, Any], url: str | None) -> str | None:
    if not url:
        return None
    repo = (((config.get("github_bus") or {}).get("repo")) or "").strip()
    if not repo:
        return url
    expected = f"github.com/{repo}/"
    if "github.com/" in url and expected not in url:
        return None
    return url


def load_discussion_planning_state() -> dict[str, Any] | None:
    payload = load_json(PLANNING_STATE_FILE, default={}) or {}
    if not isinstance(payload, dict):
        return None
    if str(payload.get("planning_mode") or "").strip() != "discussion_planning":
        return None
    return payload


def discussion_planning_is_active(planning_state: dict[str, Any] | None) -> bool:
    if not planning_state:
        return False
    return str(planning_state.get("status") or "").strip() in {"active", "human_required"}


def discussion_planning_needs_materialization(config: dict[str, Any], planning_state: dict[str, Any] | None) -> bool:
    if not planning_state:
        return False
    if str(planning_state.get("status") or "").strip() != "accepted":
        return False
    if str(planning_state.get("human_gate_status") or "").strip() != "approved":
        return False
    if str(planning_state.get("materialized_at") or "").strip():
        return False

    proposed = [payload for payload in list(planning_state.get("proposed_execution_tasks") or []) if isinstance(payload, dict)]
    if not proposed:
        return False

    status = load_json(config_path(config, "status_file", "ai-status.json"), default={}) or {}
    schema = config.get("schema", {})
    tasks_path = str(schema.get("tasks_path", "tasks"))
    task_id_field = str(schema.get("task_id_field", "id"))
    task_map = {
        str(task.get(task_id_field) or "").strip(): task
        for task in list(status.get(tasks_path) or [])
        if isinstance(task, dict) and str(task.get(task_id_field) or "").strip()
    }
    resolver = TaskResolver(task_map)
    session_id = str(planning_state.get("session_id") or "").strip()

    for payload in proposed:
        task_id = str(payload.get("id") or "").strip()
        if not task_id:
            continue
        current = task_map.get(task_id)
        if not isinstance(current, dict):
            if resolver.snapshot(task_id) is not None:
                continue
            return True
        if str(current.get("source_plane") or "").strip().lower() != "planning":
            return True
        source_ref = current.get("source_ref") if isinstance(current.get("source_ref"), dict) else {}
        if session_id and str(source_ref.get("session_id") or "").strip() != session_id:
            return True

    return False


def auto_materialize_discussion_planning(config: dict[str, Any], planning_state: dict[str, Any] | None) -> bool:
    if not discussion_planning_needs_materialization(config, planning_state):
        return False

    status_root = config_path(config, "status_file", "ai-status.json").parent
    script = status_root / "scripts" / "planning_state.py"
    session_id = str((planning_state or {}).get("session_id") or "").strip()
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "planning_materialization_failed",
                "session_id": session_id,
                "message": f"Planning materialization script not found at {script}.",
            },
        )
        return False

    result = subprocess.run(
        [sys.executable, str(script), "materialize"],
        cwd=str(status_root),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        write_activity_log(
            config,
            {
                "type": "planning_tasks_materialized_auto",
                "session_id": session_id,
                "message": result.stdout.strip() or "Accepted planning session auto-materialized into ai-status.json.",
            },
        )
        return True

    write_activity_log(
        config,
        {
            "type": "planning_materialization_failed",
            "session_id": session_id,
            "message": result.stderr.strip() or result.stdout.strip() or "Planning materialization failed.",
        },
    )
    return False


def discussion_planning_dir(planning_state: dict[str, Any]) -> str:
    planning_dir = str(planning_state.get("planning_dir") or "").strip()
    if planning_dir:
        return planning_dir
    return "docs/02-architecture/consensus/phase1"


def discussion_planning_artifact_path(planning_state: dict[str, Any], artifact_key: str, default_name: str) -> str:
    artifacts = planning_state.get("artifacts") if isinstance(planning_state.get("artifacts"), dict) else {}
    artifact = artifacts.get(artifact_key) if isinstance(artifacts.get(artifact_key), dict) else {}
    path = str(artifact.get("path") or "").strip()
    if path:
        return path
    return f"{discussion_planning_dir(planning_state)}/{default_name}"


def discussion_planning_readout_path(planning_state: dict[str, Any], agent_name: str) -> str:
    readouts = planning_state.get("readouts") if isinstance(planning_state.get("readouts"), dict) else {}
    readout = readouts.get(agent_name) if isinstance(readouts.get(agent_name), dict) else {}
    path = str(readout.get("path") or "").strip()
    if path:
        return path
    return f"{discussion_planning_dir(planning_state)}/{agent_name.lower()}-readout.md"


def discussion_planning_target_files(planning_state: dict[str, Any], agent_name: str) -> list[str]:
    target_files = [
        discussion_planning_artifact_path(planning_state, "planning_readme", "README.md"),
        str(planning_state.get("session_file") or "").strip() or f"{discussion_planning_dir(planning_state)}/planning-session.json",
        *[str(path).strip() for path in list(planning_state.get("brief_files") or []) if str(path).strip()],
        discussion_planning_artifact_path(planning_state, "starter_draft", "starter-draft.md"),
        discussion_planning_artifact_path(planning_state, "consensus_packet", "consensus-packet.md"),
        discussion_planning_readout_path(planning_state, agent_name),
    ]
    for output in list(planning_state.get("expected_outputs") or []):
        if not isinstance(output, dict):
            continue
        if str(output.get("owner") or "").strip() != agent_name:
            continue
        output_path = str(output.get("path") or "").strip()
        if output_path:
            target_files.append(output_path)
    ordered: list[str] = []
    seen: set[str] = set()
    for path in target_files:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def build_discussion_planning_message(planning_state: dict[str, Any], agent_name: str, target_files: list[str]) -> str:
    session_id = str(planning_state.get("session_id") or "phase1")
    summary = str(planning_state.get("summary") or "").strip()
    objective = str(planning_state.get("objective") or "").strip()
    baton_owner = str(planning_state.get("baton_owner") or "Codex")
    next_reviewer = str(planning_state.get("next_reviewer") or "Codex2")
    current_round = int(planning_state.get("current_round") or 0)
    consensus_status = str(planning_state.get("consensus_status") or "not_started")
    readout_path = discussion_planning_readout_path(planning_state, agent_name)
    role_lines = [
        f"- 先寫你自己的 lane readout：`{readout_path}`",
        "- 只用 cited observations；不要直接改別人的 readout。",
        "- 如果你不是 baton owner，不要直接重寫 `starter-draft.md`。",
        f"- 完成 readout 後，請用 `./scripts/planning-state.sh readout {agent_name} submitted \"{agent_name} readout ready\"` 更新 planning state。",
    ]
    if agent_name == baton_owner:
        role_lines.append("- 你目前是 baton owner，除了自己的 readout，也要把 `starter-draft.md` seed 成可供 cross-review 的共享草稿。")
    if agent_name == "Claude":
        role_lines.append("- 你同時是 facilitator；目前先聚焦 readout 與 cited review，不要提早定稿 consensus packet，除非所有 readout 已齊。")
    return (
        "你被喚醒進入 discussion planning mode。\n\n"
        f"Session: {session_id}\n"
        f"Summary: {summary or 'Align architecture, delivery order, and execution slicing before implementation.'}\n"
        f"Baton owner: {baton_owner}\n"
        f"Next reviewer: {next_reviewer}\n"
        f"Current round: {current_round}\n"
        f"Consensus status: {consensus_status}\n\n"
        "請先閱讀這些 planning canonical files，並以它們作為本輪討論唯一共同真相：\n"
        + "\n".join(f"- {path}" for path in target_files)
        + "\n\n"
        + f"本輪目標：{objective or 'Align architecture, delivery order, and execution slicing before implementation.'}\n\n"
        + "\n".join(role_lines)
        + "\n"
    )


def worker_is_discussion_planning(worker: dict[str, Any]) -> bool:
    request_snapshot = worker.get("request_snapshot", {}) or {}
    metadata = request_snapshot.get("metadata", {}) or {}
    planning = metadata.get("planning")
    if isinstance(planning, dict) and planning:
        return True
    reason = str(request_snapshot.get("reason") or worker.get("reason") or "").strip()
    return reason.startswith("discussion_planning_")


def worker_is_coordination_dispatch(worker: dict[str, Any]) -> bool:
    request_snapshot = worker.get("request_snapshot", {}) or {}
    metadata = request_snapshot.get("metadata", {}) or {}
    coordination = metadata.get("coordination")
    if isinstance(coordination, dict) and coordination:
        return True
    reason = str(request_snapshot.get("reason") or worker.get("reason") or "").strip()
    return reason.startswith("coordination:")


def worker_is_chair_review(worker: dict[str, Any]) -> bool:
    request_snapshot = worker.get("request_snapshot", {}) or {}
    metadata = request_snapshot.get("metadata", {}) or {}
    chair = metadata.get("chair")
    if isinstance(chair, dict) and chair:
        return True
    reason = str(request_snapshot.get("reason") or worker.get("reason") or "").strip()
    return reason.startswith("chair_review:")


def chair_review_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("chair_review", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("cooldown_seconds", 1800)
    settings.setdefault("candidates", ["Codex", "Codex2", "Claude", "Claude2"])
    settings.setdefault("task_id", "OPS-CHAIR-REVIEW")
    settings.setdefault("output_dir", ".orchestrator/chair-reviews")
    settings.setdefault("skill_path", ".orchestrator/skills/chairman-review.md")
    settings.setdefault("recent_summary_lines", 6)
    settings.setdefault("decision_schema_version", 1)
    settings.setdefault("approval_ttl_minutes", 45)
    settings.setdefault("min_approval_ttl_minutes", 5)
    settings.setdefault("max_approval_ttl_minutes", 120)
    settings.setdefault("approval_actions_enabled", True)
    settings.setdefault("max_pending_approvals_in_prompt", 6)
    settings.setdefault("bypass_cooldown_for_pending_approvals", True)
    settings.setdefault("bypass_primary_work_for_pending_approvals", True)
    settings.setdefault("reassignment_actions_enabled", True)
    settings.setdefault("max_reassignment_actions", 4)
    settings.setdefault("failure_loop_reassignment_threshold", int(worker_reassignment_settings(config).get("after_attempts", 2)))
    settings.setdefault("max_failure_loops_in_prompt", 6)
    settings.setdefault("bypass_cooldown_for_failure_loops", True)
    return settings


def chair_review_base_dir(config: dict[str, Any]) -> Path:
    try:
        return config_path(config, "state_file").parent.parent
    except KeyError:
        status_file = ((config.get("paths", {}) or {}).get("status_file") or "").strip()
        return Path(status_file).resolve().parent if status_file else THIS_DIR.parent


def chair_review_output_dir(config: dict[str, Any]) -> Path:
    raw_path = str(chair_review_settings(config).get("output_dir") or ".orchestrator/chair-reviews").strip()
    path = Path(raw_path)
    if not path.is_absolute():
        path = chair_review_base_dir(config) / path
    return path


def chair_review_skill_path(config: dict[str, Any]) -> Path | None:
    raw_path = str(chair_review_settings(config).get("skill_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = chair_review_base_dir(config) / path
    return path


def chair_review_context_files(config: dict[str, Any]) -> list[str]:
    paths = [relpath(path) for path in selected_shared_files(config)]
    skill_path = chair_review_skill_path(config)
    if skill_path and skill_path.exists():
        skill_relpath = relpath(skill_path)
        if skill_relpath not in paths:
            paths.append(skill_relpath)
    return paths


def chair_rotation_state(state: dict[str, Any]) -> dict[str, Any]:
    rotation = state.setdefault("chair_rotation", {})
    rotation.setdefault("current_index", 0)
    rotation.setdefault("last_chair_run_at", None)
    rotation.setdefault("last_chair_agent", None)
    rotation.setdefault("last_chair_reason", None)
    rotation.setdefault("last_review_path", None)
    rotation.setdefault("last_review_summary", None)
    rotation.setdefault("pending_review_path", None)
    rotation.setdefault("pending_decision_path", None)
    rotation.setdefault("pending_review_event_id", None)
    rotation.setdefault("pending_review_agent", None)
    rotation.setdefault("sidecar_approved_until", None)
    rotation.setdefault("sidecar_approval_max_sidecars", None)
    return rotation


def chair_review_summary_lines(path: Path, *, max_lines: int) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return lines[: max(1, max_lines)]


def chair_review_decision_path(review_path: Path) -> Path:
    return review_path.with_suffix(".json")


def chair_review_state_path(value: str | None) -> Path | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return THIS_DIR.parent / path


def chair_review_worker_path(worker: dict[str, Any]) -> str:
    snapshot = worker.get("request_snapshot", {}) or {}
    metadata = snapshot.get("metadata", {}) or {}
    chair = metadata.get("chair") if isinstance(metadata, dict) else None
    if isinstance(chair, dict):
        return str(chair.get("review_path") or "")
    metadata = worker.get("metadata", {}) or {}
    chair = metadata.get("chair") if isinstance(metadata, dict) else None
    if isinstance(chair, dict):
        return str(chair.get("review_path") or "")
    return ""


def pending_chair_review_active(state: dict[str, Any], pending_review_path: str) -> bool:
    active_statuses = {
        "running",
        "started",
        "waiting_approval",
        "manual_pending",
        "retry_backoff",
        "suspended_approval",
        "stalled",
        "fallback",
    }
    for worker in state.get("workers", {}).values():
        if not worker_is_chair_review(worker):
            continue
        if chair_review_worker_path(worker) != pending_review_path:
            continue
        if str(worker.get("status") or "") in active_statuses:
            return True

    rotation = chair_rotation_state(state)
    pending_event_id = str(rotation.get("pending_review_event_id") or "").strip()
    if pending_event_id:
        record = (state.get("queue", {}) or {}).get("events", {}).get(pending_event_id, {}) or {}
        if record and str(record.get("status") or "") not in {"completed", "failed"}:
            return True
    return False


def chair_review_worker_workspace_path(worker: dict[str, Any]) -> Path | None:
    raw_path = str(worker.get("workspace_path") or "").strip()
    if not raw_path:
        snapshot = worker.get("request_snapshot", {}) or {}
        metadata = snapshot.get("metadata", {}) or {}
        if isinstance(metadata, dict):
            raw_path = str(metadata.get("workspace_path") or "").strip()
    if not raw_path:
        metadata = worker.get("metadata", {}) or {}
        if isinstance(metadata, dict):
            raw_path = str(metadata.get("workspace_path") or "").strip()
    return Path(raw_path) if raw_path else None


def chair_review_workspace_artifact_path(config: dict[str, Any], workspace_path: Path, artifact_path: Path) -> Path | None:
    if not artifact_path.is_absolute():
        return workspace_path / artifact_path

    base_candidates: list[Path] = []
    try:
        base_candidates.append(config_path(config, "status_file").parent)
    except KeyError:
        pass
    base_candidates.append(chair_review_base_dir(config))

    for base in base_candidates:
        try:
            relative_path = artifact_path.resolve().relative_to(base.resolve())
        except ValueError:
            continue
        return workspace_path / relative_path
    return None


def sync_chair_review_artifacts_from_worker_workspace(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    pending_review_relpath: str,
    review_path: Path,
    decision_path: Path,
) -> bool:
    """Copy completed chair-review artifacts out of an isolated worker workspace."""
    copied = False
    for worker in state.get("workers", {}).values():
        if not worker_is_chair_review(worker):
            continue
        if chair_review_worker_path(worker) != pending_review_relpath:
            continue

        workspace_path = chair_review_worker_workspace_path(worker)
        if workspace_path is None:
            continue
        source_review_path = chair_review_workspace_artifact_path(config, workspace_path, review_path)
        source_decision_path = chair_review_workspace_artifact_path(config, workspace_path, decision_path)
        if source_review_path is None or not source_review_path.exists():
            continue

        review_path.parent.mkdir(parents=True, exist_ok=True)
        if not review_path.exists() or source_review_path.stat().st_mtime_ns > review_path.stat().st_mtime_ns:
            shutil.copy2(source_review_path, review_path)
            copied = True

        if source_decision_path is not None and source_decision_path.exists():
            decision_path.parent.mkdir(parents=True, exist_ok=True)
            if not decision_path.exists() or source_decision_path.stat().st_mtime_ns > decision_path.stat().st_mtime_ns:
                shutil.copy2(source_decision_path, decision_path)
                copied = True

        if copied:
            write_activity_log(
                config,
                {
                    "type": "chair_review_artifact_synced_from_worktree",
                    "task_id": chair_review_settings(config).get("task_id"),
                    "message": f"Copied chair review artifacts from worker workspace {workspace_path}.",
                    "review_path": relpath(review_path),
                    "decision_path": relpath(decision_path),
                    "workspace_path": str(workspace_path),
                    "source_review_path": str(source_review_path),
                    "source_decision_path": str(source_decision_path) if source_decision_path is not None else None,
                },
            )
        return copied
    return copied


def normalize_chair_review_decision(
    config: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    settings = chair_review_settings(config)
    if not isinstance(payload, dict):
        return None, "decision JSON must be an object"

    expected_version = int(settings.get("decision_schema_version", 1))
    try:
        version = int(payload.get("version", expected_version))
    except (TypeError, ValueError):
        return None, "version must be an integer"
    if version != expected_version:
        return None, f"unsupported decision schema version {version}"

    decision = str(payload.get("decision") or "").strip().lower()
    approved_value = payload.get("sidecar_approved")
    if isinstance(approved_value, bool):
        sidecar_approved = approved_value
    elif decision in {"approve_sidecars", "approve", "approved"}:
        sidecar_approved = True
    elif decision in {"deny_sidecars", "deny", "denied", "hold"}:
        sidecar_approved = False
    else:
        return None, "sidecar_approved must be boolean or decision must approve/deny sidecars"

    if not decision:
        decision = "approve_sidecars" if sidecar_approved else "deny_sidecars"

    try:
        ttl_minutes = int(payload.get("approval_ttl_minutes", settings.get("approval_ttl_minutes", 45)))
    except (TypeError, ValueError):
        return None, "approval_ttl_minutes must be an integer"
    if sidecar_approved:
        min_ttl = int(settings.get("min_approval_ttl_minutes", 5))
        max_ttl = int(settings.get("max_approval_ttl_minutes", 120))
        ttl_minutes = max(min_ttl, min(max_ttl, ttl_minutes))
    else:
        ttl_minutes = 0

    blocked_by = payload.get("blocked_by") or []
    if not isinstance(blocked_by, list):
        return None, "blocked_by must be a list"
    blocked_sidecar_parents = payload.get("blocked_sidecar_parents") or []
    if not isinstance(blocked_sidecar_parents, list):
        return None, "blocked_sidecar_parents must be a list"
    recommended_focus = payload.get("recommended_focus") or []
    if not isinstance(recommended_focus, list):
        return None, "recommended_focus must be a list"
    approval_actions = payload.get("approval_actions") or []
    if not isinstance(approval_actions, list):
        return None, "approval_actions must be a list"
    normalized_approval_actions: list[dict[str, Any]] = []
    for index, action in enumerate(approval_actions):
        if not isinstance(action, dict):
            return None, f"approval_actions[{index}] must be an object"
        approval_id = str(action.get("approval_id") or "").strip()
        if not approval_id:
            return None, f"approval_actions[{index}].approval_id is required"
        action_decision = str(action.get("decision") or "").strip().lower()
        if action_decision not in {"allow", "deny"}:
            return None, f"approval_actions[{index}].decision must be allow or deny"
        action_reason = str(action.get("reason") or "").strip()
        if not action_reason:
            return None, f"approval_actions[{index}].reason is required"
        normalized_approval_actions.append(
            {
                "approval_id": approval_id,
                "decision": action_decision,
                "reason": action_reason,
                "remember": bool(action.get("remember", False)),
            }
        )

    reassignment_actions = payload.get("reassignment_actions") or []
    if not isinstance(reassignment_actions, list):
        return None, "reassignment_actions must be a list"
    normalized_reassignment_actions: list[dict[str, Any]] = []
    max_reassignment_actions = max(0, int(settings.get("max_reassignment_actions", 4)))
    for index, action in enumerate(reassignment_actions[:max_reassignment_actions]):
        if not isinstance(action, dict):
            return None, f"reassignment_actions[{index}] must be an object"
        task_id = str(action.get("task_id") or "").strip()
        if not task_id:
            return None, f"reassignment_actions[{index}].task_id is required"
        role = str(action.get("role") or "").strip().lower()
        if role not in {"owner", "reviewer"}:
            return None, f"reassignment_actions[{index}].role must be owner or reviewer"
        to_agent = str(action.get("to") or action.get("to_agent") or "").strip()
        if not to_agent:
            return None, f"reassignment_actions[{index}].to is required"
        action_reason = str(action.get("reason") or "").strip()
        if not action_reason:
            return None, f"reassignment_actions[{index}].reason is required"
        normalized_reassignment_actions.append(
            {
                "task_id": task_id,
                "role": role,
                "from": str(action.get("from") or action.get("from_agent") or "").strip(),
                "to": to_agent,
                "reason": action_reason,
            }
        )

    max_sidecars = payload.get("max_sidecars")
    normalized_max_sidecars = None
    if max_sidecars is not None:
        try:
            normalized_max_sidecars = max(0, int(max_sidecars))
        except (TypeError, ValueError):
            return None, "max_sidecars must be an integer when present"

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        reason = "Chair review approved sidecar dispatch." if sidecar_approved else "Chair review denied sidecar dispatch."

    return (
        {
            "version": version,
            "decision": decision,
            "sidecar_approved": sidecar_approved,
            "approval_ttl_minutes": ttl_minutes,
            "max_sidecars": normalized_max_sidecars,
            "reason": reason,
            "blocked_by": [str(item) for item in blocked_by if str(item).strip()],
            "blocked_sidecar_parents": [str(item) for item in blocked_sidecar_parents if str(item).strip()],
            "recommended_focus": [str(item) for item in recommended_focus if str(item).strip()],
            "approval_actions": normalized_approval_actions,
            "reassignment_actions": normalized_reassignment_actions,
        },
        None,
    )


def mark_chair_review_problem(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    problem_type: str,
    message: str,
    review_path: Path | None = None,
    decision_path: Path | None = None,
) -> bool:
    rotation = chair_rotation_state(state)
    now = utc_now()
    rotation["last_chair_problem"] = problem_type
    rotation["last_chair_problem_at"] = now
    rotation["last_chair_problem_message"] = message
    rotation["last_review_valid"] = False
    if review_path is not None and review_path.exists():
        rotation["last_review_path"] = relpath(review_path)
        rotation["last_review_summary"] = chair_review_summary_lines(
            review_path,
            max_lines=int(chair_review_settings(config).get("recent_summary_lines", 6)),
        )
    if decision_path is not None:
        rotation["last_review_decision_path"] = relpath(decision_path)
    rotation["pending_review_path"] = None
    rotation["pending_decision_path"] = None
    rotation["pending_review_event_id"] = None
    rotation["pending_review_agent"] = None
    rotation["last_chair_run_at"] = None
    write_activity_log(
        config,
        {
            "type": problem_type,
            "task_id": chair_review_settings(config).get("task_id"),
            "message": message,
            "review_path": relpath(review_path) if review_path is not None else None,
            "decision_path": relpath(decision_path) if decision_path is not None else None,
        },
    )
    return True


def apply_chair_review_decision(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    review_path: Path,
    decision_path: Path,
    decision: dict[str, Any],
) -> bool:
    rotation = chair_rotation_state(state)
    summary_lines = chair_review_summary_lines(
        review_path,
        max_lines=int(chair_review_settings(config).get("recent_summary_lines", 6)),
    )
    if not summary_lines:
        summary_lines = [str(decision.get("reason") or "Chair review decision recorded.")]

    now = utc_now()
    current_dt = _parse_iso_utc(now) or datetime.now(timezone.utc)
    approved = bool(decision.get("sidecar_approved"))
    if approved:
        approval_until = current_dt + timedelta(minutes=int(decision.get("approval_ttl_minutes") or 0))
        rotation["sidecar_approved_until"] = approval_until.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rotation["sidecar_approval_max_sidecars"] = decision.get("max_sidecars")
    else:
        rotation["sidecar_approved_until"] = None
        rotation["sidecar_approval_max_sidecars"] = None

    rotation["last_review_path"] = relpath(review_path)
    rotation["last_review_decision_path"] = relpath(decision_path)
    rotation["last_review_summary"] = summary_lines
    rotation["last_review_decision"] = decision.get("decision")
    rotation["last_review_valid"] = True
    rotation["last_review_sidecar_approved"] = approved
    rotation["last_review_reason"] = decision.get("reason")
    rotation["last_review_blocked_by"] = decision.get("blocked_by", [])
    rotation["last_review_blocked_sidecar_parents"] = decision.get("blocked_sidecar_parents", [])
    rotation["last_review_recommended_focus"] = decision.get("recommended_focus", [])
    rotation["last_review_approval_actions"] = decision.get("approval_actions", [])
    rotation["last_review_reassignment_actions"] = decision.get("reassignment_actions", [])
    rotation["last_review_at"] = now
    rotation["last_chair_problem"] = None
    rotation["last_chair_problem_message"] = None
    rotation["sidecar_blocked_parents"] = decision.get("blocked_sidecar_parents", [])

    if rotation.get("pending_review_path") == relpath(review_path):
        rotation["pending_review_path"] = None
        rotation["pending_decision_path"] = None
        rotation["pending_review_event_id"] = None
        rotation["pending_review_agent"] = None

    write_activity_log(
        config,
        {
            "type": "chair_review_approved_sidecars" if approved else "chair_review_denied_sidecars",
            "task_id": chair_review_settings(config).get("task_id"),
            "message": str(decision.get("reason") or ""),
            "review_path": relpath(review_path),
            "decision_path": relpath(decision_path),
            "sidecar_approved_until": rotation.get("sidecar_approved_until"),
            "max_sidecars": decision.get("max_sidecars"),
            "blocked_by": decision.get("blocked_by", []),
            "blocked_sidecar_parents": decision.get("blocked_sidecar_parents", []),
            "approval_actions": decision.get("approval_actions", []),
            "reassignment_actions": decision.get("reassignment_actions", []),
        },
    )
    apply_chair_review_reassignment_actions(config, state, decision.get("reassignment_actions", []), review_path=review_path)
    apply_chair_review_approval_actions(config, decision.get("approval_actions", []), review_path=review_path)
    return True


def canonical_agent_name(config: dict[str, Any], value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    agent_id = normalize_agent_id(raw)
    if agent_id and agent_id in config.get("agents", {}):
        return display_name_for(config, agent_id)
    for known in known_agent_display_names(config):
        if known.casefold() == raw.casefold():
            return known
    return raw


def log_chair_reassignment_skip(
    config: dict[str, Any],
    *,
    review_path: Path,
    action: dict[str, Any],
    message: str,
) -> None:
    write_activity_log(
        config,
        {
            "type": "chair_review_reassignment_skipped",
            "task_id": action.get("task_id"),
            "message": message,
            "review_path": relpath(review_path),
            "action": action,
        },
    )


def apply_chair_review_reassignment_actions(
    config: dict[str, Any],
    state: dict[str, Any],
    reassignment_actions: list[dict[str, Any]],
    *,
    review_path: Path,
) -> None:
    if not reassignment_actions:
        return
    if not chair_review_settings(config).get("reassignment_actions_enabled", True):
        write_activity_log(
            config,
            {
                "type": "chair_review_reassignment_actions_skipped",
                "task_id": chair_review_settings(config).get("task_id"),
                "message": "Chair review included reassignment actions, but reassignment action execution is disabled.",
                "review_path": relpath(review_path),
                "count": len(reassignment_actions),
            },
        )
        return

    dispatch_settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in dispatch_settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in dispatch_settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = {str(value).lower() for value in dispatch_settings.get("owned_statuses", ["in_progress", "todo"])}

    for action in reassignment_actions:
        task_id = str(action.get("task_id") or "").strip()
        role = str(action.get("role") or "").strip().lower()
        to_agent = canonical_agent_name(config, str(action.get("to") or ""))
        from_agent = canonical_agent_name(config, str(action.get("from") or ""))
        reason = str(action.get("reason") or "Chair review reassignment.").strip()

        status = load_status(config)
        task = task_index_from_status(config, status).get(task_id)
        if not task:
            log_chair_reassignment_skip(
                config,
                review_path=review_path,
                action=action,
                message=f"Chair reassignment skipped because task {task_id} no longer exists.",
            )
            continue
        if to_agent not in known_agent_display_names(config):
            log_chair_reassignment_skip(
                config,
                review_path=review_path,
                action=action,
                message=f"Chair reassignment skipped because target agent {to_agent} is not configured.",
            )
            continue
        if agent_dispatch_paused(config, state, to_agent):
            log_chair_reassignment_skip(
                config,
                review_path=review_path,
                action=action,
                message=f"Chair reassignment skipped because target agent {to_agent} is dispatch-paused.",
            )
            continue
        if not agent_can_take_task(config, to_agent, task):
            log_chair_reassignment_skip(
                config,
                review_path=review_path,
                action=action,
                message=f"Chair reassignment skipped because {to_agent} is not eligible for task {task_id}.",
            )
            continue

        task_status = str(task.get("status") or "").lower()
        owner = str(task.get("owner") or "").strip()
        reviewer = str(task.get("reviewer") or "").strip()
        applied = False
        message = ""

        if role == "reviewer":
            if task_status not in review_statuses:
                log_chair_reassignment_skip(
                    config,
                    review_path=review_path,
                    action=action,
                    message=f"Chair reviewer reassignment skipped because task {task_id} is status={task_status}.",
                )
                continue
            if from_agent and reviewer != from_agent:
                log_chair_reassignment_skip(
                    config,
                    review_path=review_path,
                    action=action,
                    message=f"Chair reviewer reassignment skipped because reviewer moved from {from_agent} to {reviewer}.",
                )
                continue
            if to_agent in {owner, reviewer}:
                log_chair_reassignment_skip(
                    config,
                    review_path=review_path,
                    action=action,
                    message=f"Chair reviewer reassignment skipped because target {to_agent} would duplicate owner or reviewer.",
                )
                continue
            message = f"Chair reassigned review from {reviewer} to {to_agent}: {reason}"
            applied = persist_task_reassignment(
                config,
                task_id=task_id,
                new_owner=owner,
                new_reviewer=to_agent,
                message=message,
                handoff_to=to_agent,
                handoff_from=reviewer,
            )
        elif role == "owner":
            blocked_owner_rescue = chair_blocked_owner_rescue_allowed(task)
            allowed_owner_statuses = owned_statuses | finalize_statuses
            if blocked_owner_rescue:
                allowed_owner_statuses.add("blocked")
            if task_status not in allowed_owner_statuses:
                log_chair_reassignment_skip(
                    config,
                    review_path=review_path,
                    action=action,
                    message=f"Chair owner reassignment skipped because task {task_id} is status={task_status}.",
                )
                continue
            if from_agent and owner != from_agent:
                log_chair_reassignment_skip(
                    config,
                    review_path=review_path,
                    action=action,
                    message=f"Chair owner reassignment skipped because owner moved from {from_agent} to {owner}.",
                )
                continue
            if to_agent == reviewer:
                log_chair_reassignment_skip(
                    config,
                    review_path=review_path,
                    action=action,
                    message=f"Chair owner reassignment skipped because target {to_agent} is already reviewer.",
                )
                continue
            requeue_for_fresh_dispatch = (
                (task_status in owned_statuses or blocked_owner_rescue)
                and task_status not in finalize_statuses
            )
            message = f"Chair reassigned owner from {owner} to {to_agent}: {reason}"
            if requeue_for_fresh_dispatch:
                suffix = "Task returned to todo for a blocked-owner rescue dispatch." if blocked_owner_rescue else "Task returned to todo for a fresh run."
                message = f"{message.rstrip('.')}. {suffix}"
            applied = persist_task_reassignment(
                config,
                task_id=task_id,
                new_owner=to_agent,
                new_reviewer=reviewer,
                message=message,
                new_status="todo" if requeue_for_fresh_dispatch else None,
                handoff_to=to_agent,
                handoff_from=owner,
                resolve_open_blockers=blocked_owner_rescue,
            )

        if not applied:
            log_chair_reassignment_skip(
                config,
                review_path=review_path,
                action=action,
                message=f"Chair reassignment for {task_id} could not be persisted.",
            )
            continue

        clear_task_failure_streaks_for_task(state, task_id)
        write_activity_log(
            config,
            {
                "type": "chair_review_reassignment_applied",
                "task_id": task_id,
                "message": message,
                "role": role,
                "from_agent": from_agent or (reviewer if role == "reviewer" else owner),
                "to_agent": to_agent,
                "review_path": relpath(review_path),
            },
        )


def apply_chair_review_approval_actions(
    config: dict[str, Any],
    approval_actions: list[dict[str, Any]],
    *,
    review_path: Path,
) -> None:
    if not approval_actions:
        return
    if not chair_review_settings(config).get("approval_actions_enabled", True):
        write_activity_log(
            config,
            {
                "type": "chair_review_approval_actions_skipped",
                "task_id": chair_review_settings(config).get("task_id"),
                "message": "Chair review included approval actions, but approval action execution is disabled.",
                "review_path": relpath(review_path),
                "count": len(approval_actions),
            },
        )
        return

    pending_by_id = {
        str(item.get("approval_id") or ""): item
        for item in safe_load_approval_state(config).get("pending", []) or []
        if item.get("approval_id")
    }
    for action in approval_actions:
        approval_id = str(action.get("approval_id") or "").strip()
        action_decision = str(action.get("decision") or "").strip().lower()
        if approval_id not in pending_by_id:
            write_activity_log(
                config,
                {
                    "type": "chair_review_approval_action_skipped",
                    "task_id": chair_review_settings(config).get("task_id"),
                    "message": f"Chair review approval action skipped because {approval_id} is no longer pending.",
                    "approval_id": approval_id,
                    "decision": action_decision,
                    "review_path": relpath(review_path),
                },
            )
            continue
        try:
            resolve_approval(
                config,
                approval_id,
                decision=action_decision,
                note=f"Chair review {relpath(review_path)}: {action.get('reason')}",
                remember=bool(action.get("remember", False)),
            )
        except KeyError:
            write_activity_log(
                config,
                {
                    "type": "chair_review_approval_action_skipped",
                    "task_id": chair_review_settings(config).get("task_id"),
                    "message": f"Chair review approval action skipped because {approval_id} disappeared during resolution.",
                    "approval_id": approval_id,
                    "decision": action_decision,
                    "review_path": relpath(review_path),
                },
            )


def refresh_chair_review_artifact(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    review_path: Path,
    decision_path: Path,
) -> bool:
    if not decision_path.exists():
        return mark_chair_review_problem(
            config,
            state,
            problem_type="chair_review_invalid_schema",
            message=f"Chair review {relpath(review_path)} did not produce required decision JSON {relpath(decision_path)}.",
            review_path=review_path,
            decision_path=decision_path,
        )
    try:
        payload = load_json(decision_path, default={})
    except (OSError, json.JSONDecodeError) as exc:
        return mark_chair_review_problem(
            config,
            state,
            problem_type="chair_review_invalid_schema",
            message=f"Chair review decision JSON could not be parsed: {exc}",
            review_path=review_path,
            decision_path=decision_path,
        )

    decision, error = normalize_chair_review_decision(config, payload)
    if decision is None:
        return mark_chair_review_problem(
            config,
            state,
            problem_type="chair_review_invalid_schema",
            message=f"Chair review decision JSON failed validation: {error}",
            review_path=review_path,
            decision_path=decision_path,
        )
    return apply_chair_review_decision(config, state, review_path=review_path, decision_path=decision_path, decision=decision)


def refresh_chair_review_state(config: dict[str, Any], state: dict[str, Any]) -> bool:
    rotation = chair_rotation_state(state)
    output_dir = chair_review_output_dir(config)
    pending_review_relpath = str(rotation.get("pending_review_path") or "").strip()
    if pending_review_relpath:
        pending_review_path = chair_review_state_path(pending_review_relpath)
        pending_decision_path = chair_review_state_path(str(rotation.get("pending_decision_path") or "")) if rotation.get("pending_decision_path") else None
        if pending_review_path is not None:
            pending_decision_path = pending_decision_path or chair_review_decision_path(pending_review_path)
            pending_active = pending_chair_review_active(state, pending_review_relpath)
            sync_chair_review_artifacts_from_worker_workspace(
                config,
                state,
                pending_review_relpath=pending_review_relpath,
                review_path=pending_review_path,
                decision_path=pending_decision_path,
            )
            if pending_decision_path.exists():
                return refresh_chair_review_artifact(
                    config,
                    state,
                    review_path=pending_review_path,
                    decision_path=pending_decision_path,
                )
            if pending_review_path.exists() and not pending_active:
                return refresh_chair_review_artifact(
                    config,
                    state,
                    review_path=pending_review_path,
                    decision_path=pending_decision_path,
                )
            if not pending_active:
                return mark_chair_review_problem(
                    config,
                    state,
                    problem_type="chair_review_missing_report",
                    message=f"Chair review worker finished without producing {pending_review_relpath}.",
                    review_path=pending_review_path,
                    decision_path=pending_decision_path,
                )

    if not output_dir.exists():
        return False
    review_files = sorted(output_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not review_files:
        return False
    latest = review_files[0]
    latest_relpath = relpath(latest)
    if rotation.get("last_review_path") == latest_relpath:
        return False
    decision_path = chair_review_decision_path(latest)
    if decision_path.exists():
        return refresh_chair_review_artifact(config, state, review_path=latest, decision_path=decision_path)

    summary_lines = chair_review_summary_lines(latest, max_lines=int(chair_review_settings(config).get("recent_summary_lines", 6)))
    rotation["last_review_path"] = latest_relpath
    rotation["last_review_summary"] = summary_lines
    rotation["last_review_valid"] = False
    rotation["last_chair_problem"] = "chair_review_invalid_schema"
    rotation["last_chair_problem_message"] = f"Chair review {latest_relpath} has no decision JSON."
    return True


def chair_review_candidates(config: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for item in chair_review_settings(config).get("candidates", []):
        agent_name = str(item or "").strip()
        if not agent_name:
            continue
        agent_id = normalize_agent_id(agent_name)
        if agent_id and agent_id in config.get("agents", {}):
            candidates.append(display_name_for(config, agent_id))
    return candidates


def chair_review_cooldown_active(config: dict[str, Any], state: dict[str, Any], *, now: str) -> bool:
    last_run = _parse_iso_utc(str(chair_rotation_state(state).get("last_chair_run_at") or ""))
    current_dt = _parse_iso_utc(now)
    if last_run is None or current_dt is None:
        return False
    return (current_dt - last_run).total_seconds() < float(chair_review_settings(config).get("cooldown_seconds", 1800))


def chair_review_active(state: dict[str, Any]) -> bool:
    for worker in state.get("workers", {}).values():
        if str(worker.get("status") or "") in {"running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"} and worker_is_chair_review(worker):
            return True
    return False


def chair_review_worker_artifacts_applied(state: dict[str, Any], worker: dict[str, Any]) -> bool:
    if not worker_is_chair_review(worker):
        return False
    review_relpath = chair_review_worker_path(worker)
    if not review_relpath:
        return False

    rotation = chair_rotation_state(state)
    if str(rotation.get("last_review_path") or "") != review_relpath:
        return False
    if not rotation.get("last_review_valid"):
        return False

    review_path = chair_review_state_path(review_relpath)
    decision_path = chair_review_state_path(str(rotation.get("last_review_decision_path") or ""))
    return bool(review_path and review_path.exists() and decision_path and decision_path.exists())


def chair_review_report_path(config: dict[str, Any], agent_name: str, *, issued_at: str) -> Path:
    stamp = issued_at.replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    filename = f"{stamp}-{normalize_agent_id(agent_name) or agent_name.lower()}.md"
    return chair_review_output_dir(config) / filename


def chair_review_failure_loop_details(config: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    settings = chair_review_settings(config)
    if not settings.get("reassignment_actions_enabled", True):
        return []
    threshold = max(1, int(settings.get("failure_loop_reassignment_threshold", 2)))
    try:
        status = load_status(config)
    except KeyError:
        return []
    task_map = task_index_from_status(config, status)
    dispatch_settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in dispatch_settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in dispatch_settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = {str(value).lower() for value in dispatch_settings.get("owned_statuses", ["in_progress", "todo"])}
    eligible_statuses = {str(value).lower() for value in worker_reassignment_settings(config).get("eligible_statuses", [])}
    max_items = max(1, int(settings.get("max_failure_loops_in_prompt", 6)))
    loops: list[dict[str, Any]] = []

    for key, record in ((state.get("provider_guardrails", {}) or {}).get("task_failure_streaks", {}) or {}).items():
        if not isinstance(record, dict):
            continue
        try:
            count = int(record.get("count", 0))
        except (TypeError, ValueError):
            continue
        if count < threshold:
            continue
        task_id = str(record.get("task_id") or str(key).rsplit(":", 1)[0] or "").strip()
        provider = normalize_agent_id(str(record.get("provider") or str(key).rsplit(":", 1)[-1] or ""))
        task = task_map.get(task_id)
        if not task or not provider:
            continue
        task_status = str(task.get("status") or "").lower()
        if eligible_statuses and task_status not in eligible_statuses:
            continue
        agent_name = display_name_for(config, provider)
        owner = str(task.get("owner") or "").strip()
        reviewer = str(task.get("reviewer") or "").strip()
        role = ""
        exclude: set[str] = set()
        candidates: list[str] = []
        if task_status in review_statuses and reviewer == agent_name:
            role = "reviewer"
            exclude = {owner, reviewer}
            candidates = normalized_mapping_values(worker_reassignment_settings(config).get("reviewer_fallbacks", {}), agent_name)
        elif task_status in owned_statuses | finalize_statuses and owner == agent_name:
            role = "owner"
            exclude = {owner, reviewer}
            candidates = normalized_mapping_values(worker_reassignment_settings(config).get("owner_fallbacks", {}), agent_name)
        if not role:
            continue
        viable_candidates = [
            candidate
            for candidate in candidates
            if first_viable_agent(config, [candidate], exclude=exclude, state=state, task=task) == candidate
        ]
        loops.append(
            {
                "task_id": task_id,
                "status": task_status,
                "role": role,
                "agent": agent_name,
                "count": count,
                "last_failure_kind": record.get("last_failure_kind"),
                "last_failure_at": record.get("last_failure_at"),
                "last_reason": record.get("last_reason"),
                "owner": owner,
                "reviewer": reviewer,
                "viable_reassignment_targets": viable_candidates,
            }
        )

    loops.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("task_id") or "")))
    return loops[:max_items]


def chair_review_failure_loop_lines(config: dict[str, Any], state: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in chair_review_failure_loop_details(config, state):
        reason = str(item.get("last_reason") or "").replace("\n", " ").strip()
        if len(reason) > 220:
            reason = reason[:217] + "..."
        lines.append(
            "- "
            f"task={item.get('task_id')} "
            f"status={item.get('status')} "
            f"role={item.get('role')} "
            f"agent={item.get('agent')} "
            f"failures={item.get('count')} "
            f"targets={json.dumps(item.get('viable_reassignment_targets') or [], ensure_ascii=False)} "
            f"last_reason={json.dumps(reason, ensure_ascii=False)}"
        )
    return lines


def chair_review_blocked_owner_rescue_details(config: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    settings = chair_review_settings(config)
    if not settings.get("reassignment_actions_enabled", True):
        return []
    try:
        status = load_status(config)
    except KeyError:
        return []
    max_items = max(1, int(settings.get("max_blocked_owner_rescues_in_prompt", 6)))
    details: list[dict[str, Any]] = []
    owner_fallbacks = worker_reassignment_settings(config).get("owner_fallbacks", {})
    for task in status.get("tasks", []) or []:
        if not isinstance(task, dict) or not chair_blocked_owner_rescue_allowed(task):
            continue
        task_id = str(task.get("id") or "").strip()
        owner = str(task.get("owner") or "").strip()
        reviewer = str(task.get("reviewer") or "").strip()
        candidates = normalized_mapping_values(owner_fallbacks, owner)
        viable_candidates = [
            candidate
            for candidate in candidates
            if first_viable_agent(config, [candidate], exclude={owner, reviewer}, state=state, task=task) == candidate
        ]
        details.append(
            {
                "task_id": task_id,
                "status": str(task.get("status") or "").lower(),
                "owner": owner,
                "reviewer": reviewer,
                "waiting_for": str(task.get("waiting_for") or "").strip(),
                "next": str(task.get("next") or "").replace("\n", " ").strip(),
                "viable_reassignment_targets": viable_candidates,
            }
        )
    return details[:max_items]


def chair_review_blocked_owner_rescue_lines(config: dict[str, Any], state: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in chair_review_blocked_owner_rescue_details(config, state):
        note = str(item.get("next") or "")
        if len(note) > 220:
            note = note[:217] + "..."
        lines.append(
            "- "
            f"task={item.get('task_id')} "
            f"status={item.get('status')} "
            f"owner={item.get('owner')} "
            f"reviewer={item.get('reviewer')} "
            f"waiting_for={json.dumps(item.get('waiting_for') or '', ensure_ascii=False)} "
            f"targets={json.dumps(item.get('viable_reassignment_targets') or [], ensure_ascii=False)} "
            f"next={json.dumps(note, ensure_ascii=False)}"
        )
    return lines


def chair_reassignment_triage_needed_for_task(
    config: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    agent_name: str,
) -> bool:
    settings = chair_review_settings(config)
    if not settings.get("reassignment_actions_enabled", True):
        return False
    threshold = max(1, int(settings.get("failure_loop_reassignment_threshold", 2)))
    provider_id = normalize_agent_id(agent_name)
    if not task_id or not provider_id:
        return False
    record = ((state.get("provider_guardrails", {}) or {}).get("task_failure_streaks", {}) or {}).get(
        _failure_streak_key(task_id, provider_id)
    )
    if not isinstance(record, dict):
        return False
    try:
        return int(record.get("count", 0)) >= threshold
    except (TypeError, ValueError):
        return False


def failure_loop_task_agents_for_task_map(
    config: dict[str, Any],
    state: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    settings = chair_review_settings(config)
    if not settings.get("reassignment_actions_enabled", True):
        return set()
    threshold = max(1, int(settings.get("failure_loop_reassignment_threshold", 2)))
    dispatch_settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in dispatch_settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in dispatch_settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = {str(value).lower() for value in dispatch_settings.get("owned_statuses", ["in_progress", "todo"])}
    task_agents: set[tuple[str, str]] = set()
    for key, record in ((state.get("provider_guardrails", {}) or {}).get("task_failure_streaks", {}) or {}).items():
        if not isinstance(record, dict):
            continue
        try:
            count = int(record.get("count", 0))
        except (TypeError, ValueError):
            continue
        if count < threshold:
            continue
        task_id = str(record.get("task_id") or str(key).rsplit(":", 1)[0] or "").strip()
        provider = normalize_agent_id(str(record.get("provider") or str(key).rsplit(":", 1)[-1] or ""))
        task = task_map.get(task_id)
        if not task or not provider:
            continue
        agent_name = display_name_for(config, provider)
        task_status = str(task.get("status") or "").lower()
        if task_status in review_statuses and str(task.get("reviewer") or "").strip() == agent_name:
            task_agents.add((task_id, agent_name))
        elif task_status in owned_statuses | finalize_statuses and str(task.get("owner") or "").strip() == agent_name:
            task_agents.add((task_id, agent_name))
    return task_agents


def failure_loop_agents_for_task_map(
    config: dict[str, Any],
    state: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> set[str]:
    agents: set[str] = set()
    for _task_id, agent_name in failure_loop_task_agents_for_task_map(config, state, task_map):
        agents.add(agent_name)
    return agents


def build_chair_review_message(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    agent_name: str,
    review_path: Path,
) -> str:
    approval_state = safe_load_approval_state(config)
    paused_lanes = sorted((state.get("provider_guardrails", {}) or {}).get("dispatch_pauses", {}).keys())
    underutilization = state.get("underutilization", {}) or {}
    occupancy = (state.get("supervisor", {}) or {}).get("mode_occupancy", {}) or {}
    queue_depth = len(load_event_queue(config))
    decision_path = chair_review_decision_path(review_path)
    skill_path = chair_review_skill_path(config)
    skill_line = f"- Skill Reference: {relpath(skill_path)}\n" if skill_path and skill_path.exists() else ""
    pending_approval_lines = chair_review_pending_approval_lines(config, approval_state)
    pending_approvals_block = "\n".join(pending_approval_lines) if pending_approval_lines else "- none"
    failure_loop_lines = chair_review_failure_loop_lines(config, state)
    failure_loops_block = "\n".join(failure_loop_lines) if failure_loop_lines else "- none"
    blocked_owner_rescue_lines = chair_review_blocked_owner_rescue_lines(config, state)
    blocked_owner_rescues_block = "\n".join(blocked_owner_rescue_lines) if blocked_owner_rescue_lines else "- none"
    return (
        "你是本輪輪值主席，請做一次 operational review，不接主線實作。\n\n"
        f"- Chair Agent: {agent_name}\n"
        f"- Markdown Review Output: {relpath(review_path)}\n"
        f"- Required Decision JSON Output: {relpath(decision_path)}\n"
        f"{skill_line}"
        f"- Queue Depth: {queue_depth}\n"
        f"- Pending Approvals: {len(approval_state.get('pending') or [])}\n"
        f"- Paused Lanes: {', '.join(paused_lanes) if paused_lanes else 'none'}\n"
        f"- Underutilization Ratio: {underutilization.get('last_ratio') if underutilization.get('last_ratio') is not None else 'unknown'}\n"
        f"- Mode Occupancy: {json.dumps(occupancy, ensure_ascii=False)}\n\n"
        "Pending Approval Details:\n"
        f"{pending_approvals_block}\n\n"
        "Repeated Failure Details:\n"
        f"{failure_loops_block}\n\n"
        "Blocked Owner Rescue Candidates:\n"
        f"{blocked_owner_rescues_block}\n\n"
        "請檢查以下事項：\n"
        "1. task board 是否有假的 in_progress（沒有 live worker）。\n"
        "2. worker 是否跑錯 owner/reviewer 或 queue event 對不上。\n"
        "3. dispatch queue / approval queue 是否有卡住太久的項目。\n"
        "4. provider guardrail 是否讓主線無法推進。\n"
        "5. review / review_approved 是否有長時間滯留。\n"
        "6. sidecar 是否過多、重複、或缺少明確 parent support need。\n"
        "7. 已 closeout 的工作是否仍停在 push_status/ahead；有安全的 normal push approval 時主席要處理。\n\n"
        "請一定要產生兩個檔案：\n"
        f"1. markdown 人類報告：{relpath(review_path)}，格式建議包含 Summary、Findings、Suggested Repairs、Sidecar Recommendation。\n"
        f"2. JSON 決策檔：{relpath(decision_path)}，必須符合以下 schema。\n\n"
        "JSON schema:\n"
        "{\n"
        '  "version": 1,\n'
        '  "decision": "approve_sidecars | deny_sidecars",\n'
        '  "sidecar_approved": true,\n'
        '  "approval_ttl_minutes": 45,\n'
        '  "max_sidecars": null,\n'
        '  "reason": "one concise operational reason",\n'
        '  "blocked_by": [],\n'
        '  "blocked_sidecar_parents": [],\n'
        '  "approval_actions": [\n'
        "    {\n"
        '      "approval_id": "apr-...",\n'
        '      "decision": "allow | deny",\n'
        '      "reason": "why this approval is safe or should be denied",\n'
        '      "remember": false\n'
        "    }\n"
        "  ],\n"
        '  "reassignment_actions": [\n'
        "    {\n"
        '      "task_id": "SVC-...",\n'
        '      "role": "owner | reviewer",\n'
        '      "from": "Codex2",\n'
        '      "to": "Claude",\n'
        '      "reason": "why this reassignment is the right repair"\n'
        "    }\n"
        "  ],\n"
        '  "recommended_focus": []\n'
        "}\n\n"
        "如果目前有 idle auto worker、execution backlog 有可安全平行化的工作、且沒有 global blocker，預設應 approve_sidecars。\n"
        "不要為 sidecar wave 設定數量上限；max_sidecars 請填 null，除非存在具體安全風險需要暫時 cap。\n"
        "如果 deny_sidecars，blocked_by 必須列出具體 blocker；如果只有特定 parent 不應產生 sidecar，請放進 blocked_sidecar_parents。\n"
        "如果 Pending Approval Details 裡有你能判斷的低風險 approval，請在 approval_actions 裡 allow 或 deny；不能判斷就不要列入。\n"
        "如果 Repeated Failure Details 顯示同一 agent 在同一 task 壞循環，請用 reassignment_actions 指定是否改派；不需要就留空。\n"
        "如果 Blocked Owner Rescue Candidates 有可用 targets，且不是 human gate，請用 role=owner 的 reassignment_actions 改派給健康 target；supervisor 會把該 task 退回 todo 重新 dispatch。\n"
        "你可以提出 repair commands 或建立 OPS-/SUP- follow-up task 建議；不要直接手改 task board，也不要直接把 task 標成 done。\n"
    )


def chair_review_pending_approval_lines(config: dict[str, Any], approval_state: dict[str, Any]) -> list[str]:
    max_items = max(1, int(chair_review_settings(config).get("max_pending_approvals_in_prompt", 6)))
    lines: list[str] = []
    for item in (approval_state.get("pending", []) or [])[:max_items]:
        preview = str(item.get("tool_input_preview") or "").replace("\n", " ").strip()
        if len(preview) > 240:
            preview = preview[:237] + "..."
        lines.append(
            "- "
            f"approval_id={item.get('approval_id')} "
            f"provider={item.get('provider')} "
            f"task={item.get('task_id')} "
            f"worker={item.get('worker_run_id')} "
            f"tool={item.get('tool_name')} "
            f"risk={item.get('risk_class')} "
            f"created_at={item.get('created_at')} "
            f"preview={json.dumps(preview, ensure_ascii=False)}"
        )
    if len(approval_state.get("pending", []) or []) > max_items:
        lines.append(f"- ... {len(approval_state.get('pending', []) or []) - max_items} more pending approvals omitted")
    return lines


def queue_chair_review_event(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    agent_name: str,
    reason: str,
    issued_at: str,
) -> str:
    agent = agent_config_for(config, agent_name)
    review_path = chair_review_report_path(config, agent_name, issued_at=issued_at)
    decision_path = chair_review_decision_path(review_path)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    queue_payload = {
        "event_id": new_runtime_id("evt"),
        "created_at": issued_at,
        "event_key": f"chair:{normalize_agent_id(agent_name)}:{reason}:{issued_at}",
        "task_id": None,
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        "reason": reason,
        "message": build_chair_review_message(config, state, agent_name=agent_name, review_path=review_path),
        "context_files": chair_review_context_files(config),
        "target_files": [relpath(review_path), relpath(decision_path)],
        "metadata": {
            "chair": {
                "mode": "chair_review",
                "agent": agent_name,
                "review_path": relpath(review_path),
                "decision_path": relpath(decision_path),
            },
            "workspace_task_id": f"chair-review-{review_path.stem}",
        },
    }
    enqueue_event(config, queue_payload)
    rotation = chair_rotation_state(state)
    rotation["last_chair_run_at"] = issued_at
    rotation["last_chair_agent"] = agent_name
    rotation["last_chair_reason"] = reason
    rotation["pending_review_path"] = relpath(review_path)
    rotation["pending_decision_path"] = relpath(decision_path)
    rotation["pending_review_event_id"] = queue_payload["event_id"]
    rotation["pending_review_agent"] = agent_name
    write_activity_log(
        config,
        {
            "type": "chair_review_queued",
            "task_id": chair_review_settings(config).get("task_id"),
            "target_agent": display_name_for(config, agent["id"]),
            "delivery_mode": config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
                "delivery_mode", agent.get("adapter", "file_inbox")
            ),
            "message": f"Chair review queued for {agent_name}: {reason}",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return queue_payload["event_key"]


def queue_discussion_planning_event(
    config: dict[str, Any],
    planning_state: dict[str, Any],
    *,
    agent_name: str,
    reason: str,
) -> str:
    agent = agent_config_for(config, agent_name)
    target_files = discussion_planning_target_files(planning_state, agent_name)
    queue_payload = {
        "event_id": new_runtime_id("evt"),
        "created_at": utc_now(),
        "event_key": (
            f"discussion:{planning_state.get('session_id')}:{agent_name}:{reason}:"
            f"round-{planning_state.get('current_round', 0)}:{planning_state.get('consensus_status', 'not_started')}"
        ),
        "task_id": str(planning_state.get("session_id") or "phase1"),
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        "reason": reason,
        "message": build_discussion_planning_message(planning_state, agent_name, target_files),
        "context_files": [relpath(path) for path in selected_shared_files(config)],
        "target_files": target_files,
        "metadata": {
            "planning": {
                "session_id": planning_state.get("session_id"),
                "mode": planning_state.get("planning_mode"),
                "baton_owner": planning_state.get("baton_owner"),
            }
        },
    }
    enqueue_event(config, queue_payload)
    write_activity_log(
        config,
        {
            "type": "planning_wake_queued",
            "task_id": queue_payload["task_id"],
            "target_agent": display_name_for(config, agent["id"]),
            "delivery_mode": config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
                "delivery_mode", agent.get("adapter", "file_inbox")
            ),
            "message": f"Discussion planning wake-up queued for {agent_name}: {reason}",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return queue_payload["event_key"]


def file_iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def update_from_log(config: dict[str, Any], worker: dict[str, Any]) -> None:
    log_path_value = worker.get("log_path")
    if not log_path_value:
        return
    log_path = Path(log_path_value)
    if not log_path.exists():
        return
    mtime = file_iso_mtime(log_path)
    if mtime and (not worker.get("last_event_at") or mtime > worker.get("last_event_at", "")):
        worker["last_event_at"] = mtime
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not worker.get("session_id") and payload.get("session_id"):
            worker["session_id"] = payload.get("session_id")
            worker.setdefault("resume_token", worker["session_id"])
        if payload.get("type") == "result":
            if payload.get("stop_reason") == "tool_deferred":
                worker["status"] = "waiting_approval"
                worker["deferred_tool_use"] = payload.get("deferred_tool_use")
            if payload.get("pr_url") and not worker.get("pr_url"):
                worker["pr_url"] = normalize_pr_url(config, payload.get("pr_url"))
            if payload.get("session_url") and not worker.get("session_url"):
                worker["session_url"] = payload.get("session_url")
    if not worker.get("session_id"):
        for pattern in SESSION_ID_PATTERNS:
            match = pattern.search(content)
            if match:
                worker["session_id"] = match.group(1)
                worker.setdefault("resume_token", worker["session_id"])
                break
    if not worker.get("pr_url"):
        for url in URL_PATTERN.findall(content):
            if "/pull/" in url:
                worker["pr_url"] = normalize_pr_url(config, url)
                break
    worker["pr_url"] = normalize_pr_url(config, worker.get("pr_url"))
    if not worker.get("session_url"):
        for url in URL_PATTERN.findall(content):
            if "/agent" in url or "/sessions/" in url:
                worker["session_url"] = url
                break


def detect_worker_failure(worker: dict[str, Any]) -> str | None:
    log_path_value = worker.get("log_path")
    if not log_path_value:
        return None
    log_path = Path(log_path_value)
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None

    fallback: str | None = None
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            continue
        if '"ts":' in stripped and '"type":' in stripped:
            continue
        try:
            stream_payload = json.loads(stripped)
        except json.JSONDecodeError:
            stream_payload = None
        if isinstance(stream_payload, dict):
            if is_captured_orchestrator_record(stream_payload):
                continue
            if is_allowed_rate_limit_event(stream_payload):
                continue
            message = stream_payload.get("message")
            role = message.get("role") if isinstance(message, dict) else None
            if stream_payload.get("type") == "user" or role == "user":
                continue
        if SEARCH_RESULT_JSON_FIELD_PATTERN.search(stripped):
            continue
        if JSON_FIELD_LINE_PATTERN.search(stripped):
            continue
        if SEARCH_RESULT_LOG_JSON_PATTERN.search(stripped):
            continue
        if is_tool_command_output_failure_line(lines, idx):
            continue
        if any(pattern.search(stripped) for pattern in WORKER_FAILURE_FALSE_POSITIVE_PATTERNS):
            continue
        if any(pattern.search(stripped) for pattern in WORKER_FAILURE_PATTERNS):
            normalized = stripped.lower()
            if (
                "an unexpected critical error occurred" in normalized
                or "[object object]" in normalized
                or normalized.startswith("reason:")
                or normalized.startswith("retrydelayms:")
            ):
                fallback = fallback or stripped
                continue
            return stripped
    return fallback


def is_captured_orchestrator_record(payload: dict[str, Any]) -> bool:
    if payload.get("event_id") or payload.get("event_key"):
        return True
    if payload.get("queue_event_id") or payload.get("worker_run_id"):
        return True
    if payload.get("target_agent") or payload.get("target_display_name"):
        return True
    if isinstance(payload.get("metadata"), dict) and isinstance(payload.get("context_files"), list):
        return True
    return False


def is_allowed_rate_limit_event(payload: dict[str, Any]) -> bool:
    if payload.get("type") != "rate_limit_event":
        return False
    info = payload.get("rate_limit_info")
    if not isinstance(info, dict):
        return False
    return str(info.get("status") or "").strip().lower() == "allowed"


def is_tool_command_output_failure_line(lines: list[str], idx: int) -> bool:
    for prev_idx in range(idx - 1, max(idx - 5, -1), -1):
        previous = lines[prev_idx].strip()
        if not previous:
            continue
        return bool(COMMAND_OUTPUT_EXIT_LINE_PATTERN.search(previous))
    return False


def classify_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> dict[str, Any]:
    provider = str(worker.get("provider") or worker.get("agent_id") or "").strip().lower()
    normalized = str(reason or "").lower()
    retry = worker_retry_settings(config, worker.get("provider"))
    transient_patterns = [str(pattern).lower() for pattern in retry.get("transient_error_patterns", [])]

    auth_markers = {
        "status: 401",
        "unauthorized",
        "authentication",
        "not authenticated",
        "auth failed",
        "invalid api key",
        "forbidden",
        "permission denied",
    }
    terminal_quota_markers = {
        "status: 402",
        "credit balance is too low",
        "billing_error",
        "hit your limit",
        "hit your usage limit",
        "exhausted your capacity",
        "no quota",
        "you have no quota",
        "quota exceeded",
        "free daily quota has been reached",
        "free tier quota exceeded",
        "quota will reset after",
        "terminalquotaerror",
    }
    retryable_capacity_markers = {
        "status: 429",
        "retryablequotaerror",
        "quota_exhausted",
        "resource_exhausted",
        "rate limit",
        "rate limited",
        "no capacity available",
    }
    unknown_critical_markers = {
        "an unexpected critical error occurred",
        "[object object]",
    }
    provider_config_markers = {
        "error loading config.toml",
        "config.toml cannot be parsed",
        "unsupported service_tier",
        "unknown variant",
        "service_tier",
    }

    if is_github_cli_auth_failure(reason):
        return {"kind": "tool_auth", "transient": False, "label": "tool auth"}
    if "config.toml" in normalized and any(marker in normalized for marker in provider_config_markers):
        return {"kind": "provider_config", "transient": False, "label": "provider config"}
    if any(marker in normalized for marker in auth_markers):
        return {"kind": "auth", "transient": False, "label": "auth"}
    if any(marker in normalized for marker in terminal_quota_markers):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if any(marker in normalized for marker in retryable_capacity_markers):
        return {"kind": "capacity_retryable", "transient": True, "label": "capacity/429"}
    if provider.startswith("gemini") and any(marker in normalized for marker in unknown_critical_markers):
        return {"kind": "unknown_critical", "transient": False, "label": "unknown critical error"}
    if any(pattern in normalized for pattern in transient_patterns):
        return {"kind": "transient", "transient": True, "label": "transient"}
    if any(marker in normalized for marker in unknown_critical_markers):
        return {"kind": "unknown_critical", "transient": False, "label": "unknown critical error"}
    return {"kind": "terminal", "transient": False, "label": "terminal"}


def _parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def worker_runtime_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_runtime")
    settings = dict(raw if isinstance(raw, dict) else {})
    supervisor_settings = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    settings.setdefault("worker_lease_seconds", supervisor_settings.get("worker_lease_seconds", 1800))
    settings.setdefault("queue_lease_seconds", supervisor_settings.get("queue_lease_seconds", 1800))
    settings.setdefault("heartbeat_stale_seconds", supervisor_settings.get("heartbeat_stale_seconds", 300))
    settings.setdefault("heartbeat_grace_seconds", supervisor_settings.get("heartbeat_grace_seconds", 60))
    settings.setdefault("runner_heartbeat_interval_seconds", 15)
    return settings


WORKER_RUNTIME_METRIC_COUNTERS = (
    "workers_started",
    "queue_leases_started",
    "marker_updates",
    "lease_refreshes",
    "missing_process_workers_failed",
    "expired_lease_workers_failed",
    "started_queue_records_requeued",
    "started_queue_records_failed",
    "stale_queue_records_completed",
    "capacity_pending_queue_events",
)


def worker_runtime_metrics_bucket(state: dict[str, Any]) -> dict[str, Any]:
    bucket = state.setdefault("worker_runtime_metrics", {})
    bucket.setdefault("version", 1)
    bucket.setdefault("updated_at", None)
    totals = bucket.setdefault("totals", {})
    for key in WORKER_RUNTIME_METRIC_COUNTERS:
        totals.setdefault(key, 0)
    bucket.setdefault("last_measurements", {})
    return bucket


def positive_runtime_counts(counts: dict[str, Any]) -> dict[str, int]:
    positive: dict[str, int] = {}
    for key, value in counts.items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            positive[key] = amount
    return positive


def record_worker_runtime_measurement(
    config: dict[str, Any],
    state: dict[str, Any],
    measurement: str,
    counts: dict[str, Any],
    *,
    details: dict[str, Any] | None = None,
    emit_activity: bool = True,
) -> bool:
    positive = positive_runtime_counts(counts)
    if not positive and not details:
        return False
    now = utc_now()
    bucket = worker_runtime_metrics_bucket(state)
    totals = bucket.setdefault("totals", {})
    for key, amount in positive.items():
        totals[key] = int(totals.get(key, 0) or 0) + amount
    bucket["updated_at"] = now
    bucket.setdefault("last_measurements", {})[measurement] = {
        "at": now,
        "counts": positive,
        "details": details or {},
    }
    if emit_activity and positive:
        try:
            write_activity_log(
                config,
                {
                    "type": "worker_runtime_metrics",
                    "measurement": measurement,
                    "message": f"Worker runtime measurement {measurement}: {positive}",
                    "counts": positive,
                    "details": details or {},
                },
            )
        except KeyError:
            pass
    return True


def worker_lease_expiry(config: dict[str, Any], now: datetime | None = None) -> str:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(timezone.utc)
    return _isoformat_utc(now_dt + timedelta(seconds=max(60, int(settings.get("worker_lease_seconds", 1800)))))


def queue_lease_expiry(config: dict[str, Any], now: datetime | None = None) -> str:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(timezone.utc)
    return _isoformat_utc(now_dt + timedelta(seconds=max(60, int(settings.get("queue_lease_seconds", 1800)))))


def refresh_worker_lease(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> None:
    now_dt = now or datetime.now(timezone.utc)
    worker.setdefault("lease_acquired_at", _isoformat_utc(now_dt))
    worker["lease_expires_at"] = worker_lease_expiry(config, now_dt)


def _load_runtime_marker(path_value: Any) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists():
        return None
    try:
        payload = load_json(path, default={}) or {}
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def update_worker_runtime_markers(worker: dict[str, Any]) -> bool:
    metadata = worker.setdefault("metadata", {}) if isinstance(worker.get("metadata"), dict) else {}
    heartbeat_path = worker.get("heartbeat_path") or metadata.get("heartbeat_path")
    status_path = worker.get("runner_status_path") or metadata.get("runner_status_path")
    changed = False
    status_payload = _load_runtime_marker(status_path)
    heartbeat_payload = _load_runtime_marker(heartbeat_path)
    for payload in (status_payload, heartbeat_payload):
        if not payload:
            continue
        heartbeat_at = str(payload.get("last_heartbeat_at") or payload.get("updated_at") or "").strip()
        if heartbeat_at and heartbeat_at > str(worker.get("last_heartbeat_at") or ""):
            worker["last_heartbeat_at"] = heartbeat_at
            changed = True
        child_pid = payload.get("child_pid")
        if child_pid and worker.get("child_pid") != child_pid:
            worker["child_pid"] = child_pid
            changed = True
    if status_payload:
        runner_status = str(status_payload.get("status") or "").strip()
        if runner_status and worker.get("runner_status") != runner_status:
            worker["runner_status"] = runner_status
            changed = True
        if status_payload.get("finished_at") and worker.get("runner_finished_at") != status_payload.get("finished_at"):
            worker["runner_finished_at"] = status_payload.get("finished_at")
            changed = True
        if "exit_code" in status_payload and worker.get("exit_code") != status_payload.get("exit_code"):
            worker["exit_code"] = status_payload.get("exit_code")
            changed = True
        if status_payload.get("signal") and worker.get("runner_signal") != status_payload.get("signal"):
            worker["runner_signal"] = status_payload.get("signal")
            changed = True
    return changed


def worker_runner_succeeded(worker: dict[str, Any]) -> bool:
    runner_status = str(worker.get("runner_status") or "").strip().lower()
    if runner_status not in {"completed", "success", "succeeded"}:
        return False
    try:
        exit_code = int(worker.get("exit_code", 0))
    except (TypeError, ValueError):
        return False
    return exit_code == 0 and not worker.get("runner_signal")


def worker_heartbeat_is_stale(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    settings = worker_runtime_settings(config)
    heartbeat_dt = _parse_iso_utc(str(worker.get("last_heartbeat_at") or ""))
    if heartbeat_dt is None:
        return True
    now_dt = now or datetime.now(timezone.utc)
    stale_after = int(settings.get("heartbeat_stale_seconds", 300)) + int(settings.get("heartbeat_grace_seconds", 60))
    return (now_dt - heartbeat_dt.astimezone(timezone.utc)).total_seconds() > max(60, stale_after)


def worker_lease_is_expired(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    lease_expires_at = _parse_iso_utc(str(worker.get("lease_expires_at") or ""))
    if lease_expires_at is None:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return now_dt > lease_expires_at.astimezone(timezone.utc) and worker_heartbeat_is_stale(config, worker, now_dt)


_QUOTA_RETRY_AT_PATTERN = re.compile(
    r"\btry again at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?",
    re.IGNORECASE,
)
_QUOTA_RETRY_AT_DATE_PATTERN = re.compile(
    r"\btry again at\s+"
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)"
    r"(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?",
    re.IGNORECASE,
)
_QUOTA_RESETS_AT_PATTERN = re.compile(
    r"\bresets\s+(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?",
    re.IGNORECASE,
)
_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_quota_retry_hint(reason: str | None, *, now: datetime | None = None) -> datetime | None:
    """Return the next wall-clock time at which a quota error says it will reset.

    Both Codex ("try again at 7:00 PM") and Claude ("resets 1pm (Asia/Taipei)")
    emit reset times. Bare times are interpreted in LOCAL_TZ, while explicit UTC
    hints are interpreted in UTC. Returns a UTC-aware datetime, or None if no
    hint is found.
    """
    if not reason:
        return None
    hint_tz = timezone.utc if re.search(r"\(\s*UTC\s*\)|\bUTC\b", reason, re.IGNORECASE) else LOCAL_TZ
    date_match = _QUOTA_RETRY_AT_DATE_PATTERN.search(reason)
    if date_match:
        month = _MONTH_NAME_TO_NUMBER.get(date_match.group("month").lower())
        if not month:
            return None
        hour = int(date_match.group("hour"))
        minute = int(date_match.group("minute") or 0)
        meridiem = (date_match.group("meridiem") or "").replace(".", "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        try:
            return datetime(
                int(date_match.group("year")),
                month,
                int(date_match.group("day")),
                hour,
                minute,
                tzinfo=hint_tz,
            ).astimezone(timezone.utc)
        except ValueError:
            return None

    match = _QUOTA_RETRY_AT_PATTERN.search(reason) or _QUOTA_RESETS_AT_PATTERN.search(reason)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").replace(".", "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    base = (now.astimezone(hint_tz) if now else datetime.now(hint_tz))
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def provider_guardrail_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("provider_guardrails", {}) or {})
    settings.setdefault("pause_on_capacity_failure", True)
    settings.setdefault("pause_on_auth_failure", True)
    settings.setdefault("capacity_pause_seconds", 900)
    settings.setdefault("auth_pause_seconds", int(settings.get("capacity_pause_seconds", 900)))
    settings.setdefault("provider_config_pause_seconds", int(settings.get("auth_pause_seconds", 900)))
    settings.setdefault("quota_terminal_pause_seconds", int(settings.get("capacity_pause_seconds", 900)))
    settings.setdefault("generic_exit_reassign_after", int(worker_reassignment_settings(config).get("after_attempts", 2)))
    return settings


def _provider_guardrail_bucket(state: dict[str, Any]) -> dict[str, Any]:
    bucket = state.setdefault("provider_guardrails", {})
    bucket.setdefault("dispatch_pauses", {})
    bucket.setdefault("task_failure_streaks", {})
    return bucket


def _dispatch_pause_bucket(state: dict[str, Any]) -> dict[str, Any]:
    return _provider_guardrail_bucket(state).setdefault("dispatch_pauses", {})


def _task_failure_streak_bucket(state: dict[str, Any]) -> dict[str, Any]:
    return _provider_guardrail_bucket(state).setdefault("task_failure_streaks", {})


def _failure_streak_key(task_id: str, provider: str) -> str:
    return f"{task_id}:{provider}"


def current_provider_dispatch_pause(
    state: dict[str, Any],
    provider: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return None
    bucket = _dispatch_pause_bucket(state)
    group_id = provider_dispatch_group_id(config, provider) if config is not None else provider_id
    for pause_id in dict.fromkeys([group_id, provider_id]):
        entry = bucket.get(pause_id)
        if not isinstance(entry, dict):
            continue
        blocked_until = _parse_iso_utc(str(entry.get("blocked_until") or ""))
        now = datetime.now(timezone.utc)
        if blocked_until is not None and blocked_until <= now:
            bucket.pop(pause_id, None)
            continue
        return entry
    return None


def provider_dispatch_paused(config: dict[str, Any], state: dict[str, Any], provider: str | None) -> bool:
    return current_provider_dispatch_pause(state, provider, config) is not None


def agent_dispatch_paused(config: dict[str, Any], state: dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id:
        return False
    if agent_dispatch_disabled(config, agent_id):
        return True
    agent = agent_config_for(config, agent_id)
    provider_id = str(agent.get("provider") or agent.get("id") or agent_id)
    return provider_dispatch_paused(config, state, provider_id)


def is_terminal_quota_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "quota_terminal"


def is_retryable_capacity_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() in {"capacity", "capacity_retryable"}


def is_auth_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "auth"


def is_provider_config_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "provider_config"


def should_pause_dispatch_for_failure_kind(kind: str | None) -> bool:
    return (
        is_terminal_quota_failure_kind(kind)
        or is_retryable_capacity_failure_kind(kind)
        or is_auth_failure_kind(kind)
        or is_provider_config_failure_kind(kind)
    )


def mark_provider_dispatch_paused(
    config: dict[str, Any],
    state: dict[str, Any],
    provider: str | None,
    reason: str,
    *,
    task_id: str | None = None,
    worker_run_id: str | None = None,
    failure_kind: str | None = None,
    pause_kind: str | None = None,
    raw_ref: str | None = None,
) -> bool:
    settings = provider_guardrail_settings(config)
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return False
    pause_provider_id = provider_dispatch_group_id(config, provider) or provider_id
    now = datetime.now(timezone.utc)
    effective_pause_kind = str(pause_kind or failure_kind or "").strip().lower()
    if effective_pause_kind in {"auth", "provider_config"}:
        if not settings.get("pause_on_auth_failure", True):
            return False
        pause_seconds_key = (
            "provider_config_pause_seconds"
            if effective_pause_kind == "provider_config"
            else "auth_pause_seconds"
        )
    else:
        if not settings.get("pause_on_capacity_failure", True):
            return False
        pause_seconds_key = "quota_terminal_pause_seconds" if effective_pause_kind == "quota_terminal" else "capacity_pause_seconds"
    pause_seconds = max(60, int(settings.get(pause_seconds_key, 900)))
    blocked_until = (now + timedelta(seconds=pause_seconds)).replace(microsecond=0)
    hinted_blocked_until: str | None = None
    hint_capped = False
    if effective_pause_kind == "quota_terminal":
        hinted = parse_quota_retry_hint(reason, now=now)
        if hinted is not None and hinted > blocked_until:
            hinted = hinted.replace(microsecond=0)
            hinted_blocked_until = hinted.isoformat().replace("+00:00", "Z")
            hint_max_seconds = int(settings.get("quota_terminal_hint_max_seconds", 0) or 0)
            if hint_max_seconds > 0:
                hint_cap = (now + timedelta(seconds=hint_max_seconds)).replace(microsecond=0)
                if hinted > hint_cap:
                    blocked_until = hint_cap
                    hint_capped = True
                else:
                    blocked_until = hinted
            else:
                blocked_until = hinted
    blocked_until_iso = blocked_until.isoformat().replace("+00:00", "Z")
    actual_pause_seconds = max(1, int((blocked_until - now).total_seconds()))
    bucket = _dispatch_pause_bucket(state)
    previous = bucket.get(pause_provider_id)
    summary = summarize_failure_reason(reason, pause_provider_id)
    changed = (
        not isinstance(previous, dict)
        or str(previous.get("blocked_until") or "") != blocked_until_iso
        or str(previous.get("summary") or "") != summary.get("summary")
        or str(previous.get("raw_ref") or "") != str(raw_ref or "")
    )
    bucket[pause_provider_id] = {
        "provider": pause_provider_id,
        "trigger_provider": provider_id,
        "paused_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "blocked_until": blocked_until_iso,
        "reason": summary.get("summary"),
        "summary": summary.get("summary"),
        "detail": summary.get("detail"),
        "failure_kind": failure_kind or summary.get("kind"),
        "pause_kind": effective_pause_kind or failure_kind or summary.get("kind"),
        "reset_after_seconds": actual_pause_seconds,
        "raw_ref": raw_ref,
        "task_id": task_id,
        "worker_run_id": worker_run_id,
    }
    if hinted_blocked_until:
        bucket[pause_provider_id]["hint_blocked_until"] = hinted_blocked_until
        bucket[pause_provider_id]["hint_capped"] = hint_capped
    if changed:
        if effective_pause_kind == "quota_terminal":
            pause_description = "terminal quota failure"
        elif effective_pause_kind == "auth":
            pause_description = "authentication failure"
        else:
            pause_description = "capacity failure"
        write_activity_log(
            config,
            {
                "type": "provider_dispatch_paused",
                "provider": pause_provider_id,
                "trigger_provider": provider_id,
                "task_id": task_id,
                "worker_run_id": worker_run_id,
                "message": (
                    f"Paused new dispatches for {pause_provider_id} until {blocked_until_iso} after {pause_description}: "
                    f"{summary.get('summary')}"
                ),
                "raw_ref": raw_ref,
            },
        )
    return changed


def clear_provider_dispatch_pause(config: dict[str, Any], state: dict[str, Any], provider: str | None) -> bool:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return False
    pause_provider_id = provider_dispatch_group_id(config, provider_id) or provider_id
    bucket = _dispatch_pause_bucket(state)
    removed: list[tuple[str, dict[str, Any]]] = []
    for pause_id in dict.fromkeys([pause_provider_id, provider_id]):
        entry = bucket.pop(pause_id, None)
        if isinstance(entry, dict):
            removed.append((pause_id, entry))
    for pause_id, entry in removed:
        write_activity_log(
            config,
            {
                "type": "provider_dispatch_resumed",
                "provider": pause_id,
                "task_id": entry.get("task_id"),
                "worker_run_id": entry.get("worker_run_id"),
                "message": f"Manually cleared dispatch pause for {pause_id}; dispatch is enabled again.",
                "raw_ref": entry.get("raw_ref"),
                "cleared_pause": entry,
            },
        )
    return bool(removed)


def expire_provider_dispatch_pauses(config: dict[str, Any], state: dict[str, Any]) -> bool:
    bucket = _dispatch_pause_bucket(state)
    if not bucket:
        return False
    now = datetime.now(timezone.utc)
    expired: list[tuple[str, dict[str, Any]]] = []
    for provider_id, entry in list(bucket.items()):
        if not isinstance(entry, dict):
            continue
        blocked_until = _parse_iso_utc(str(entry.get("blocked_until") or ""))
        if blocked_until is None or blocked_until > now:
            continue
        expired.append((provider_id, dict(entry)))
        bucket.pop(provider_id, None)

    for provider_id, entry in expired:
        write_activity_log(
            config,
            {
                "type": "provider_dispatch_resumed",
                "provider": provider_id,
                "task_id": entry.get("task_id"),
                "worker_run_id": entry.get("worker_run_id"),
                "message": f"Dispatch pause for {provider_id} expired at {entry.get('blocked_until')}; dispatch is enabled again.",
                "raw_ref": entry.get("raw_ref"),
            },
        )
    return bool(expired)


def record_task_failure_streak(
    state: dict[str, Any],
    worker: dict[str, Any],
    reason: str,
    *,
    failure_kind: str | None = None,
) -> int:
    task_id = str(worker.get("task_id") or "").strip()
    provider_id = normalize_agent_id(str(worker.get("provider") or worker.get("agent_id") or ""))
    if not task_id or not provider_id:
        return 0
    bucket = _task_failure_streak_bucket(state)
    key = _failure_streak_key(task_id, provider_id)
    record = dict(bucket.get(key) or {})
    count = int(record.get("count", 0)) + 1
    record.update(
        {
            "task_id": task_id,
            "provider": provider_id,
            "count": count,
            "last_reason": reason,
            "last_failure_at": utc_now(),
            "last_failure_kind": failure_kind or str(record.get("last_failure_kind") or ""),
        }
    )
    bucket[key] = record
    return count


def clear_task_failure_streak(
    state: dict[str, Any],
    *,
    task_id: str | None = None,
    provider: str | None = None,
    worker: dict[str, Any] | None = None,
) -> None:
    if worker is not None:
        task_id = str(worker.get("task_id") or task_id or "")
        provider = str(worker.get("provider") or worker.get("agent_id") or provider or "")
    task_id = str(task_id or "").strip()
    provider_id = normalize_agent_id(provider or "")
    if not task_id or not provider_id:
        return
    _task_failure_streak_bucket(state).pop(_failure_streak_key(task_id, provider_id), None)


def clear_task_failure_streaks_for_task(state: dict[str, Any], task_id: str | None) -> None:
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    bucket = _task_failure_streak_bucket(state)
    for key in [item for item in bucket if item.startswith(f"{task_id}:")]:
        bucket.pop(key, None)


def worker_retry_settings(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    retry = dict(config.get("worker_retry", {}) or {})
    if provider:
        retry.update((config.get("providers", {}).get(provider, {}).get("retry", {}) or {}))
    retry.setdefault("enabled", True)
    retry.setdefault("max_attempts", 5)
    retry.setdefault("backoff_schedule_seconds", [5, 15, 30, 60, 120])
    retry.setdefault("jitter_seconds", 3)
    retry.setdefault(
        "transient_error_patterns",
        [
            "429",
            "resource_exhausted",
            "rate limit",
            "rate limited",
            "timed out",
            "etimedout",
            "econnreset",
            "temporarily unavailable",
            "try again later",
            "server overloaded",
            "deadline exceeded",
        ],
    )
    retry.setdefault("fallback_mode", "file_inbox")
    return retry


def worker_reassignment_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("worker_reassignment", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("after_attempts", 2)
    settings.setdefault("reassign_on_terminal_failure", True)
    default_eligible_statuses: list[str] = []
    ready_settings = ready_dispatch_settings(config)
    for key in ("owned_statuses", "review_statuses", "finalize_statuses"):
        for value in ready_settings.get(key, []) or []:
            normalized = str(value).strip().lower()
            if normalized and normalized not in default_eligible_statuses:
                default_eligible_statuses.append(normalized)
    settings.setdefault("eligible_statuses", default_eligible_statuses or ["todo", "in_progress", "review", "review_approved"])
    default_fallbacks = {
        "Claude": ["Codex", "Codex2"],
        "Claude2": ["Codex", "Codex2", "Claude"],
        "Gemini": ["Codex", "Codex2", "Claude"],
        "Gemini2": ["Codex", "Codex2", "Claude"],
        "Codex": ["Codex2", "Claude", "Claude2"],
        "Codex2": ["Codex", "Claude", "Claude2"],
        "Copilot": ["Codex", "Codex2", "Claude"],
        "Grok": ["Codex", "Codex2", "Claude"],
    }
    settings.setdefault("owner_fallbacks", default_fallbacks)
    settings.setdefault("reviewer_fallbacks", default_fallbacks)
    return settings


def normalized_mapping_values(mapping: dict[str, Any], key: str) -> list[str]:
    target = (key or "").strip().casefold()
    for candidate_key, values in mapping.items():
        if str(candidate_key).strip().casefold() != target:
            continue
        return [str(value).strip() for value in list(values or []) if str(value).strip()]
    return []


def known_agent_display_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if str(agent.get("display_name") or agent.get("name") or agent_id).strip()
    }


def sidecar_only_agent_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent_name).strip()
        for agent_name in ready_dispatch_settings(config).get("sidecar_only_agents", []) or []
        if str(agent_name).strip()
    }


def disabled_dispatch_agent_keys(config: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    agents = config.get("agents", {}) or {}
    for raw_value in ready_dispatch_settings(config).get("disabled_agents", []) or []:
        raw = str(raw_value or "").strip()
        if not raw:
            continue
        keys.add(raw.casefold())
        normalized = normalize_agent_id(raw)
        if normalized:
            keys.add(normalized.casefold())
        agent = agents.get(normalized) if normalized else None
        if not isinstance(agent, dict):
            continue
        display = str(agent.get("display_name") or agent.get("name") or normalized).strip()
        provider = str(agent.get("provider") or "").strip()
        if display:
            keys.add(display.casefold())
        if provider:
            keys.add(provider.casefold())
            provider_id = normalize_agent_id(provider)
            if provider_id:
                keys.add(provider_id.casefold())
    return keys


def agent_dispatch_disabled(config: dict[str, Any], agent_name: str | None) -> bool:
    name = str(agent_name or "").strip()
    if not name:
        return False
    keys = disabled_dispatch_agent_keys(config)
    if name.casefold() in keys:
        return True
    agent_id = normalize_agent_id(name)
    if agent_id and agent_id.casefold() in keys:
        return True
    agent = (config.get("agents", {}) or {}).get(agent_id)
    if isinstance(agent, dict):
        display = str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        provider = str(agent.get("provider") or "").strip()
        return bool(
            (display and display.casefold() in keys)
            or (provider and provider.casefold() in keys)
            or (provider and normalize_agent_id(provider).casefold() in keys)
        )
    return False


def agent_can_take_task(config: dict[str, Any], agent_name: str | None, task: dict[str, Any] | None) -> bool:
    name = str(agent_name or "").strip()
    if not name:
        return False
    if agent_dispatch_disabled(config, name):
        return False
    if not isinstance(task, dict) or task_is_sidecar(task):
        return True
    return name not in sidecar_only_agent_names(config)


def first_viable_agent(
    config: dict[str, Any],
    preferred: list[str],
    exclude: set[str],
    *,
    state: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
) -> str | None:
    known = known_agent_display_names(config)
    seen: set[str] = set()
    for candidate in preferred:
        name = str(candidate or "").strip()
        if not name or name in seen or name in exclude:
            continue
        seen.add(name)
        if name in known:
            if state is not None and agent_dispatch_paused(config, state, name):
                continue
            if task is not None and not agent_can_take_task(config, name, task):
                continue
            return name
    return None


def agent_auto_dispatch_block_reason(
    config: dict[str, Any],
    state: dict[str, Any],
    agent_id: str | None,
    provider_report: dict[str, Any] | None = None,
) -> str | None:
    """Return a human-readable reason when an agent must not receive auto dispatch."""
    normalized_agent = normalize_agent_id(agent_id or "")
    if not normalized_agent:
        return "missing target agent"
    if agent_dispatch_paused(config, state, normalized_agent):
        return f"dispatch is paused or disabled for {display_name_for(config, normalized_agent) or normalized_agent}"
    settings = ready_dispatch_settings(config)
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    quota_limit = quota_group_concurrency_limit(config, normalized_agent, settings)
    quota_group = agent_quota_group_id(config, normalized_agent)
    if quota_limit and quota_group:
        active_quota_counts = active_quota_group_counts(config, state, active_statuses)
        active_count = active_quota_counts.get(quota_group, 0)
        if active_count >= quota_limit:
            return (
                f"quota group {quota_group} already has {active_count}/{quota_limit} "
                "active worker(s)"
            )
    agent = (config.get("agents", {}) or {}).get(normalized_agent)
    provider_key = str((agent or {}).get("provider") or normalized_agent)
    config_block_reason = provider_runtime_config_block_reason(config, provider_key)
    if config_block_reason:
        return config_block_reason
    if not provider_report:
        return None

    provider_id = normalize_agent_id(
        provider_key
    )
    agent_capability = ((provider_report.get("agent_adapters") or {}).get(normalized_agent) or {})
    provider_capability = (
        ((provider_report.get("providers") or {}).get(provider_key) or {})
        or ((provider_report.get("providers") or {}).get(provider_id) or {})
    )

    if agent_capability:
        if not agent_capability.get("supported", True):
            notes = str(agent_capability.get("notes") or "").strip()
            return notes or f"{normalized_agent} adapter is not supported"
        if agent_capability.get("can_auto_deliver") is False:
            notes = str(agent_capability.get("notes") or "").strip()
            return notes or f"{normalized_agent} cannot auto-deliver in the current workspace"

    if provider_capability:
        if provider_capability.get("local_cli_worker_supported") is False:
            return f"{provider_id} local CLI worker is not ready"
        if provider_capability.get("supports_auto_approve") is False:
            return f"{provider_id} does not currently support auto-approved dispatch"
        if provider_capability.get("config_valid") is False:
            return str(provider_capability.get("config_error") or f"{provider_id} provider config is invalid")
        if provider_capability.get("auth_ready") is False:
            return f"{provider_id} authentication is not ready"

    if settings.get("worker_os_duplicate_guard", True):
        slot_ids = logical_worker_slot_ids(config, normalized_agent)
        if slot_ids:
            occupied_slots = {
                slot_id: refs
                for slot_id in slot_ids
                if (refs := active_worker_refs_for_agent_id(state, slot_id, active_statuses))
            }
            if len(occupied_slots) >= len(slot_ids):
                slot_summary = ", ".join(
                    f"{slot_id}=PID:{'/'.join(refs)}" for slot_id, refs in sorted(occupied_slots.items())
                )
                display_name = display_name_for(config, normalized_agent) or normalized_agent
                return (
                    f"{display_name} all dispatch slots already have live worker process(es) "
                    f"{slot_summary}; skipping dispatch to avoid duplicate workers"
                )
            return None

        if agent and agent_is_dispatch_slot(agent):
            slot_refs = active_worker_refs_for_agent_id(state, normalized_agent, active_statuses)
            if slot_refs:
                display_name = display_name_for(config, normalized_agent) or normalized_agent
                return (
                    f"{display_name} slot {normalized_agent} already has live worker process(es) "
                    f"PID={','.join(slot_refs)}; skipping dispatch to avoid duplicate workers"
                )
            return None

        display_name = display_name_for(config, normalized_agent) or normalized_agent
        live_pids = scan_live_worker_pids_by_agent().get(display_name, [])
        if live_pids:
            return (
                f"{display_name} already has live worker process(es) "
                f"PID={','.join(str(p) for p in sorted(set(live_pids)))}; "
                "skipping dispatch to avoid duplicate workers"
            )

    return None


def auto_dispatch_block_is_temporary_capacity(reason: str | None) -> bool:
    normalized = str(reason or "").lower()
    return any(
        marker in normalized
        for marker in (
            "quota group",
            "already has live worker",
            "all dispatch slots",
            "slot",
        )
    )


def sync_status_pipeline(config: dict[str, Any]) -> bool:
    script = config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "task_reassignment_sync_failed",
                "message": f"Status sync script not found at {script}.",
            },
        )
        return False
    result = subprocess.run(
        [sys.executable, str(script), "sync"],
        cwd=str(config_path(config, "status_file").parent),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    write_activity_log(
        config,
        {
            "type": "task_reassignment_sync_failed",
            "message": f"Status sync failed after reassignment: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}",
        },
    )
    return False


def sync_dispatched_task_status(config: dict[str, Any], event: dict[str, Any]) -> bool:
    reason = str(event.get("reason") or "").strip()
    action = DISPATCH_STATUS_ACTIONS.get(reason)
    if action is None:
        return False
    if not config.get("paths", {}).get("status_file"):
        return False

    script = config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "task_dispatch_sync_failed",
                "task_id": event.get("task_id"),
                "message": f"Dispatch status sync script not found at {script}.",
            },
        )
        return False

    task_id = str(event.get("task_id") or "").strip()
    target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or ""))).strip()
    if not task_id or not target_agent:
        return False

    command_name, eligible_statuses = action
    task = task_index_from_status(config, load_status(config)).get(task_id)
    if not task:
        return False
    if str(task.get("owner") or "").strip() != target_agent:
        return False
    if str(task.get("status") or "").lower() not in eligible_statuses:
        return False

    message = {
        REASON_OWNED_READY: f"Supervisor auto-started {task_id} after successful dispatch.",
        REASON_OWNED_FINALIZE: f"Supervisor resumed {task_id} for finalize after successful dispatch.",
        REASON_OWNED_IN_PROGRESS: f"Supervisor re-dispatched {task_id}; task remains in progress.",
    }[reason]
    env = os.environ.copy()
    env["AI_NAME"] = target_agent
    result = subprocess.run(
        [sys.executable, str(script), command_name, task_id, message],
        cwd=str(config_path(config, "status_file").parent),
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        write_activity_log(
            config,
            {
                "type": "task_dispatch_synced",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": reason,
                "message": message,
            },
        )
        return True

    write_activity_log(
        config,
        {
            "type": "task_dispatch_sync_failed",
            "task_id": task_id,
            "target_agent": target_agent,
            "dispatch_reason": reason,
            "message": result.stderr.strip() or result.stdout.strip() or "Dispatch status sync failed.",
        },
    )
    return False


def sync_preempted_task_status(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    """Keep task truth aligned when a worker is superseded for higher-priority work."""
    if not config.get("paths", {}).get("status_file"):
        return False

    dispatch_reason = str(worker.get("request_snapshot", {}).get("reason") or "").strip()
    task_id = str(worker.get("task_id") or "").strip()
    target_agent = display_name_for(config, str(worker.get("agent_id") or worker.get("provider") or "")).strip()
    if not task_id or not target_agent:
        return False

    status = load_status(config)
    task = task_index_from_status(config, status).get(task_id)
    if not task:
        return False
    if str(task.get("owner") or "").strip() != target_agent:
        return False

    task_status = str(task.get("status") or "").lower()
    timestamp = utc_now()
    message = ""

    if dispatch_reason in {REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS}:
        if task_status != "in_progress":
            return False
        task["status"] = "todo"
        message = (
            f"Supervisor preempted {task_id} to free {target_agent} for higher-priority review/finalize work; "
            "task returned to todo until a fresh run restarts it."
        )
    elif dispatch_reason == REASON_OWNED_FINALIZE:
        if task_status != "review_approved":
            return False
        message = (
            f"Supervisor paused finalize on {task_id} to free {target_agent} for higher-priority review work; "
            "task remains review_approved."
        )
    else:
        return False

    task["last_update"] = timestamp
    task["next"] = message
    write_json(config_path(config, "status_file"), status)
    synced = sync_status_pipeline(config)
    if synced:
        write_activity_log(
            config,
            {
                "type": "task_preempted_synced",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": dispatch_reason,
                "message": message,
            },
        )
    else:
        write_activity_log(
            config,
            {
                "type": "task_preempt_sync_failed",
                "task_id": task_id,
                "target_agent": target_agent,
                "dispatch_reason": dispatch_reason,
                "message": f"Failed to persist preempted task truth for {task_id}.",
            },
        )
    return synced


def persist_task_reassignment(
    config: dict[str, Any],
    *,
    task_id: str,
    new_owner: str,
    new_reviewer: str,
    message: str,
    new_status: str | None = None,
    handoff_to: str | None = None,
    handoff_from: str | None = None,
    resolve_open_blockers: bool = False,
) -> bool:
    status_path = config_path(config, "status_file")
    status = load_status(config)
    tasks = status.get("tasks", []) or []
    timestamp = utc_now()
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        return False

    old_owner = str(task.get("owner") or "")
    old_reviewer = str(task.get("reviewer") or "")
    task["owner"] = new_owner
    task["reviewer"] = new_reviewer
    if new_status:
        task["status"] = new_status
        if str(new_status).lower() == "todo":
            task.pop("waiting_for", None)
    task["last_update"] = timestamp
    task["next"] = message

    if resolve_open_blockers:
        for blocker in status.get("blockers", []) or []:
            if blocker.get("task_id") != task_id or blocker.get("status") == "resolved":
                continue
            blocker["status"] = "resolved"
            blocker["resolved_at"] = timestamp
            blocker["resolution_ref"] = f"chair_reassignment:{task_id}"

    for handoff in status.get("handoffs", []) or []:
        if handoff.get("task_id") != task_id or handoff.get("status") == "done":
            continue
        target = str(handoff.get("to") or "")
        if target in {old_owner, old_reviewer} and target not in {new_owner, new_reviewer}:
            handoff["status"] = "done"
            handoff["resolved_at"] = timestamp

    if handoff_to:
        status.setdefault("handoffs", []).append(
            {
                "task_id": task_id,
                "from": handoff_from or old_owner or old_reviewer or new_owner,
                "to": handoff_to,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )

    write_json(status_path, status)
    return sync_status_pipeline(config)


def maybe_reassign_task_after_worker_failure(
    config: dict[str, Any],
    state_or_worker: dict[str, Any],
    worker_or_reason: dict[str, Any] | str | None = None,
    reason: str | None = None,
    *,
    terminal: bool = False,
    force: bool = False,
    failure_count: int | None = None,
    respect_threshold: bool = False,
) -> str | None:
    if isinstance(worker_or_reason, dict):
        state = state_or_worker
        worker = worker_or_reason
    else:
        state = {}
        worker = state_or_worker
        reason = str(worker_or_reason or reason or "")
    settings = worker_reassignment_settings(config)
    if not settings.get("enabled", True):
        return None

    attempt_number = failure_count if failure_count is not None else int(worker.get("retry_count", 0)) + 1
    if not force and (not terminal or respect_threshold) and attempt_number < int(settings.get("after_attempts", 2)):
        return None
    if terminal and not settings.get("reassign_on_terminal_failure", True):
        return None

    task_id = str(worker.get("task_id") or "")
    if not task_id:
        return None
    status = load_status(config)
    task = next((item for item in status.get("tasks", []) if item.get("id") == task_id), None)
    if task is None:
        return None

    task_status = str(task.get("status") or "").lower()
    if task_status not in {str(value).lower() for value in settings.get("eligible_statuses", [])}:
        return None

    dispatch_settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in dispatch_settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in dispatch_settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = {str(value).lower() for value in dispatch_settings.get("owned_statuses", ["in_progress", "todo"])}

    failing_agent = display_name_for(config, str(worker.get("agent_id") or worker.get("provider") or ""))
    failure = classify_worker_failure(config, worker, reason)
    failure_label = failure.get("label", "provider failure")
    failure_summary = summarize_failure_reason(reason, failing_agent).get("summary") or failure_label
    owner = str(task.get("owner") or "")
    reviewer = str(task.get("reviewer") or "")

    if task_status in review_statuses and reviewer == failing_agent:
        candidates = normalized_mapping_values(settings.get("reviewer_fallbacks", {}), failing_agent)
        new_reviewer = first_viable_agent(config, candidates, exclude={owner, reviewer}, state=state, task=task)
        if not new_reviewer:
            return None
        message = (
            f"Auto-reassigned review from {reviewer} to {new_reviewer} after repeated {failing_agent} {failure_label}: {failure_summary}"
        )
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=owner,
            new_reviewer=new_reviewer,
            message=message,
            handoff_to=new_reviewer,
            handoff_from=reviewer,
        ):
            return None
        write_activity_log(
            config,
            {
                "type": "task_reassigned",
                "task_id": task_id,
                "message": message,
                "from_reviewer": reviewer,
                "to_reviewer": new_reviewer,
                "worker_run_id": worker.get("run_id"),
            },
        )
        clear_task_failure_streaks_for_task(state, task_id)
        console_log(
            f"reassigned review: task={task_id} from={reviewer} to={new_reviewer} kind={failure_label}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return new_reviewer

    if task_status in owned_statuses | finalize_statuses and owner == failing_agent:
        candidates = normalized_mapping_values(settings.get("owner_fallbacks", {}), failing_agent)
        new_owner = first_viable_agent(config, candidates, exclude={owner, reviewer}, state=state, task=task)
        if not new_owner:
            return None
        reviewer_candidates = [reviewer]
        reviewer_candidates.extend(normalized_mapping_values(settings.get("reviewer_fallbacks", {}), failing_agent))
        reviewer_candidates.extend(normalized_mapping_values(settings.get("owner_fallbacks", {}), failing_agent))
        new_reviewer = first_viable_agent(config, reviewer_candidates, exclude={new_owner}, state=state, task=task)
        if not new_reviewer:
            return None
        requeue_for_fresh_dispatch = task_status in owned_statuses and task_status not in finalize_statuses
        message = (
            f"Auto-reassigned ownership from {owner} to {new_owner} after repeated {failing_agent} {failure_label}: {failure_summary}"
        )
        if requeue_for_fresh_dispatch:
            message = f"{message}. Task returned to todo until {new_owner} starts a fresh run."
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=new_owner,
            new_reviewer=new_reviewer,
            message=message,
            new_status="todo" if requeue_for_fresh_dispatch else None,
            handoff_from=owner,
        ):
            return None
        write_activity_log(
            config,
            {
                "type": "task_reassigned",
                "task_id": task_id,
                "message": message,
                "from_owner": owner,
                "to_owner": new_owner,
                "from_reviewer": reviewer,
                "to_reviewer": new_reviewer,
                "worker_run_id": worker.get("run_id"),
            },
        )
        clear_task_failure_streaks_for_task(state, task_id)
        console_log(
            f"reassigned owner: task={task_id} from={owner} to={new_owner} kind={failure_label}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return new_owner

    return None


def is_transient_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> bool:
    if not reason:
        return False
    if not worker_retry_settings(config, worker.get("provider")).get("enabled", True):
        return False
    return bool(classify_worker_failure(config, worker, reason).get("transient"))


def retry_delay_seconds(config: dict[str, Any], worker: dict[str, Any]) -> float:
    retry = worker_retry_settings(config, worker.get("provider"))
    retry_count = int(worker.get("retry_count", 0))
    schedule = list(retry.get("backoff_schedule_seconds", []) or [5, 15, 30, 60, 120])
    index = min(retry_count, len(schedule) - 1)
    base_delay = float(schedule[index])
    jitter = float(retry.get("jitter_seconds", 0) or 0)
    return base_delay + (random.uniform(0, jitter) if jitter > 0 else 0)


def schedule_queue_event_retry(config: dict[str, Any], record: dict[str, Any], *, provider: str | None, reason: str) -> None:
    delay = retry_delay_seconds(
        config,
        {
            "provider": provider,
            "retry_count": int(record.get("retry_count", 0)),
        },
    )
    retry_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + delay, tz=timezone.utc)
    record["status"] = "retry_backoff"
    record["retry_count"] = int(record.get("retry_count", 0)) + 1
    record["next_retry_at"] = retry_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record["error"] = reason
    record["processed_at"] = utc_now()


def request_for_worker(config: dict[str, Any], worker: dict[str, Any]) -> DeliveryRequest | None:
    snapshot = worker.get("request_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("message"):
        return request_from_snapshot(snapshot)
    queue_event_id = worker.get("queue_event_id")
    if not queue_event_id:
        return None
    for event in load_event_queue(config):
        if event.get("event_id") == queue_event_id:
            return build_request(config, event)
    return None


def manual_pending_inbox_can_auto_redeliver(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    worker: dict[str, Any],
) -> bool:
    if worker.get("status") != "manual_pending":
        return False
    if worker.get("mode") != "file_inbox":
        return False
    if pid_is_alive(worker.get("pid")):
        return False
    request = request_for_worker(config, worker)
    if request is None:
        return False
    if current_provider_dispatch_pause(state, request.provider, config):
        return False
    agent_capability = (provider_report or {}).get("agent_adapters", {}).get(str(request.agent_id) or "", {}) or {}
    if not agent_capability.get("can_auto_deliver"):
        return False
    return str(agent_capability.get("delivery_mode") or "") != "file_inbox"


def requeue_stale_manual_pending_worker(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    reason: str,
) -> bool:
    run_id = str(worker.get("run_id") or "").strip()
    if not run_id:
        return False
    queue_event_id = str(worker.get("queue_event_id") or "").strip()
    state.setdefault("workers", {}).pop(run_id, None)
    if queue_event_id:
        record = queue_status(state, queue_event_id)
        record["status"] = "queued"
        record.pop("processed_at", None)
        record.pop("error", None)
        record.pop("run_id", None)
    write_activity_log(
        config,
        {
            "type": "worker_requeued",
            "provider": worker.get("provider"),
            "task_id": worker.get("task_id"),
            "worker_run_id": run_id,
            "queue_event_id": queue_event_id or None,
            "message": reason,
        },
    )
    console_log(
        f"requeued stale manual_pending worker: provider={worker.get('provider')} task={worker.get('task_id')} run={run_id}",
        quiet=SUPERVISOR_LOG_QUIET,
    )
    return True


def schedule_worker_retry(config: dict[str, Any], worker: dict[str, Any], reason: str) -> None:
    delay = retry_delay_seconds(config, worker)
    retry_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + delay, tz=timezone.utc)
    worker["status"] = "retry_backoff"
    worker["retry_count"] = int(worker.get("retry_count", 0)) + 1
    worker["next_retry_at"] = retry_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    worker["last_error"] = reason
    worker["last_event_at"] = utc_now()


def existing_file_inbox_fallback_run_id(state: dict[str, Any], queue_event_id: str | None, exclude_run_id: str | None = None) -> str | None:
    if not queue_event_id:
        return None
    fallback_statuses = {"manual_pending", "waiting_approval", "running", "retry_backoff", "fallback", "completed"}
    for candidate in state.get("workers", {}).values():
        if candidate.get("run_id") == exclude_run_id:
            continue
        if candidate.get("queue_event_id") != queue_event_id:
            continue
        if candidate.get("mode") != "file_inbox":
            continue
        if candidate.get("status") not in fallback_statuses:
            continue
        run_id = candidate.get("run_id")
        if run_id:
            return str(run_id)
    return None


def maybe_trigger_retry_or_fallback(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    worker: dict[str, Any],
    reason: str,
) -> tuple[bool, bool]:
    retry = worker_retry_settings(config, worker.get("provider"))
    failure = classify_worker_failure(config, worker, reason)
    max_attempts = int(retry.get("max_attempts", 5))
    retry_count = int(worker.get("retry_count", 0))
    request = request_for_worker(config, worker)
    if request is None:
        return False, False
    reassigned_to = maybe_reassign_task_after_worker_failure(config, state, worker, reason)
    if reassigned_to:
        worker["status"] = "reassigned"
        worker["reassigned_to"] = reassigned_to
        worker["last_error"] = reason
        worker["last_event_at"] = utc_now()
        finalize_queue_event_record(config, state, worker, "completed")
        return True, True
    if retry_count < max_attempts:
        schedule_worker_retry(config, worker, reason)
        write_activity_log(
            config,
            {
                "type": "worker_retry_scheduled",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": f"Transient worker failure detected ({failure.get('label')}); retry {worker.get('retry_count')} scheduled at {worker.get('next_retry_at')}: {reason}",
                "worker_run_id": worker["run_id"],
                "next_retry_at": worker.get("next_retry_at"),
            },
        )
        console_log(
            f"retry scheduled: provider={worker.get('provider')} task={worker.get('task_id')} kind={failure.get('label')} next={worker.get('next_retry_at')}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return True, True

    if retry.get("fallback_mode") == "file_inbox":
        existing_fallback = existing_file_inbox_fallback_run_id(
            state,
            worker.get("queue_event_id"),
            exclude_run_id=worker.get("run_id"),
        )
        if existing_fallback:
            worker["status"] = "fallback"
            worker["fallback_run_id"] = existing_fallback
            worker["last_event_at"] = utc_now()
            return True, True
        if not worker.get("fallback_run_id"):
            ok, outcome, _ = start_worker_for_request(
                config,
                state,
                provider_report,
                request,
                queue_event_id=worker.get("queue_event_id"),
                attempt_count=int(worker.get("attempt_count", 0)) + 1,
                event_id_for_log=worker.get("queue_event_id"),
                parent_run_id=worker["run_id"],
                delivery_mode_override="file_inbox",
                activity_type="worker_fallback_started",
                activity_message=f"Worker fell back to file inbox after transient failures: {reason}",
            )
            if ok:
                worker["status"] = "fallback"
                worker["fallback_run_id"] = outcome
                worker["last_event_at"] = utc_now()
                return True, True
    return False, False


def retry_due_workers(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any],
    now: datetime,
) -> bool:
    changed = False
    for worker in list(state.get("workers", {}).values()):
        if worker.get("status") != "retry_backoff":
            continue
        next_retry_at = _parse_iso_utc(worker.get("next_retry_at"))
        if next_retry_at is None or next_retry_at > now:
            continue
        request = request_for_worker(config, worker)
        if request is None:
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Retry was due, but the original request could not be reconstructed.",
                    "worker_run_id": worker["run_id"],
                },
            )
            changed = True
            continue
        ok, outcome, _ = start_worker_for_request(
            config,
            state,
            provider_report,
            request,
            queue_event_id=worker.get("queue_event_id"),
            attempt_count=int(worker.get("attempt_count", 0)) + 1,
            event_id_for_log=worker.get("queue_event_id"),
            parent_run_id=worker["run_id"],
            activity_type="worker_retried",
            activity_message=f"Worker retry launched after backoff from {worker['run_id']}",
        )
        if ok:
            worker["status"] = "retried"
            worker["superseded_by_run_id"] = outcome
            worker["last_event_at"] = utc_now()
        else:
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = outcome
        changed = True
    return changed


def _claude_resume_allowed_tools(approval: dict[str, Any] | None) -> list[str]:
    if not approval:
        return []
    candidates: list[str] = []
    for value in (
        approval.get("resume_override_rule"),
        approval.get("suggested_rule"),
        approval.get("tool_name"),
    ):
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _provider_uses_claude_cli(config: dict[str, Any], provider_id: str | None) -> bool:
    normalized = normalize_agent_id(provider_id or "")
    if not normalized:
        return False
    provider = (config.get("providers", {}) or {}).get(normalized, {}) or {}
    delivery_mode = str(provider.get("delivery_mode") or "").strip()
    if delivery_mode:
        return delivery_mode == "claude_cli"
    return normalized.startswith("claude")


def _claude_runtime_env(config: dict[str, Any], provider_id: str | None) -> dict[str, str]:
    provider = (config.get("providers", {}) or {}).get(normalize_agent_id(provider_id or ""), {}) or {}
    runtime = provider.get("runtime", {}) or {}
    base_env = dict(os.environ)
    env = dict(base_env)
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    extra_env = runtime.get("env", {}) or {}
    for key, value in extra_env.items():
        if value is None:
            continue
        env[str(key)] = os.path.expanduser(str(value))
    preserve_github_cli_auth_env(env, base_env)
    return env


def worker_supports_approval_resume(config: dict[str, Any], worker: dict[str, Any]) -> bool:
    return bool(
        _provider_uses_claude_cli(config, worker.get("provider"))
        and (worker.get("session_id") or worker.get("resume_token"))
    )


def resume_claude_worker(
    config: dict[str, Any],
    worker: dict[str, Any],
    provider_report: dict[str, Any],
    *,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    session_id = worker.get("session_id") or worker.get("resume_token")
    if not session_id:
        return None
    provider_id = normalize_agent_id(worker.get("provider") or "claude")
    provider = (config.get("providers", {}) or {}).get(provider_id) or config.get("providers", {}).get("claude", {}) or {}
    runtime = provider.get("runtime", {})
    cli = command_exists(runtime.get("cli") or "claude")
    if not cli:
        return None
    command = [
        runtime.get("cli") or cli,
        "--resume",
        str(session_id),
        "--output-format",
        runtime.get("output_format", "stream-json"),
    ]
    if runtime.get("output_format", "stream-json") == "stream-json":
        command.append("--verbose")
    if runtime.get("include_hook_events", True):
        command.append("--include-hook-events")
    allowed_tools = (
        _claude_resume_allowed_tools(approval)
        if runtime.get("resume_use_allowed_tools_from_approval", True)
        else []
    )
    if allowed_tools:
        command.extend(["--allowedTools", *allowed_tools])
    provider_info = (
        (provider_report or {}).get("providers", {}).get(provider_id)
        or (provider_report or {}).get("providers", {}).get("claude", {})
    )
    resume_permission_mode = runtime.get("resume_permission_mode_after_approval", "bypassPermissions")
    if worker.get("last_approval_id"):
        command.extend(["--permission-mode", resume_permission_mode])
    elif runtime.get("enable_auto_mode_if_supported", True) and provider_info.get("supports_auto_approve"):
        command.extend(["--permission-mode", runtime.get("auto_permission_mode", "auto")])
    else:
        command.extend(["--permission-mode", runtime.get("permission_mode", "acceptEdits")])
    mcp_config = runtime.get("mcp_config")
    if mcp_config:
        command.extend(["--mcp-config", str(config_path(config, "claude_mcp_config"))])
    log_path = config_path(config, "state_file").parent / "logs" / f"{new_runtime_id(f'{provider_id}-resume')}.log"
    env = _claude_runtime_env(config, provider_id)
    repo_root = config_path(config, "status_file").parents[0]
    request_metadata = (worker.get("request_snapshot") or {}).get("metadata", {}) if isinstance(worker.get("request_snapshot"), dict) else {}
    workspace_root = Path(str(worker.get("workspace_path") or request_metadata.get("workspace_path") or repo_root)).expanduser().resolve()
    status_root = Path(str(worker.get("status_root") or request_metadata.get("status_root") or repo_root)).expanduser().resolve()
    env.update(
        {
            "ORCH_RUN_ID": worker["run_id"],
            "ORCH_TASK_ID": worker.get("task_id") or "",
            "ORCH_AGENT_ID": worker.get("agent_id") or "",
            "ORCH_PROVIDER": provider_id,
            "ORCH_SESSION_ID": str(session_id),
            "PANTHEON_WORKTREE_ROOT": str(workspace_root),
            "PANTHEON_STATUS_ROOT": str(status_root),
            "ORCH_WORKSPACE_PATH": str(workspace_root),
        }
    )
    runtime_paths = worker_runtime_paths(config, worker["run_id"])
    process, _ = spawn_background_process(
        command,
        cwd=workspace_root,
        log_path=log_path,
        env=env,
        run_id=worker["run_id"],
        heartbeat_path=runtime_paths["heartbeat_path"],
        status_path=runtime_paths["status_path"],
    )
    previous_logs = list(worker.get("previous_log_paths") or [])
    if worker.get("log_path"):
        previous_logs.append(worker["log_path"])
    now_dt = datetime.now(timezone.utc)
    worker["previous_log_paths"] = previous_logs
    worker["pid"] = process.pid
    worker["status"] = "running"
    worker["deferred_action"] = None
    worker["last_event_at"] = _isoformat_utc(now_dt)
    worker["last_heartbeat_at"] = None
    worker["lease_acquired_at"] = _isoformat_utc(now_dt)
    worker["lease_expires_at"] = worker_lease_expiry(config, now_dt)
    worker["heartbeat_path"] = str(runtime_paths["heartbeat_path"])
    worker["runner_status_path"] = str(runtime_paths["status_path"])
    worker["log_path"] = str(log_path)
    worker["resume_count"] = int(worker.get("resume_count", 0)) + 1
    worker["last_resumed_session_id"] = str(session_id)
    worker["command"] = command
    worker.setdefault("metadata", {})["shell_command"] = shell_quote(command)
    worker["metadata"]["resume_permission_mode"] = resume_permission_mode if worker.get("last_approval_id") else None
    worker["metadata"]["resume_allowed_tools"] = allowed_tools
    worker["metadata"]["heartbeat_path"] = str(runtime_paths["heartbeat_path"])
    worker["metadata"]["runner_status_path"] = str(runtime_paths["status_path"])
    return {
        "command": command,
        "log_path": str(log_path),
        "pid": process.pid,
        "allowed_tools": allowed_tools,
    }


def poll_workers(config: dict[str, Any], state: dict[str, Any], provider_report: dict[str, Any] | None = None) -> bool:
    changed = False
    approval_state = load_approval_state(config)
    task_map = task_index_from_status(config, load_status(config))
    valid_queue_event_ids = set(state.get("queue", {}).get("events", {}))
    redispatch_statuses = redispatch_candidate_statuses(config)
    active_worker_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    pending_by_run: dict[str, list[dict[str, Any]]] = {}
    resolved_by_run: dict[str, list[dict[str, Any]]] = {}
    for item in approval_state.get("pending", []):
        run_id = item.get("worker_run_id")
        if run_id:
            pending_by_run.setdefault(run_id, []).append(item)
    for item in approval_state.get("history", []):
        run_id = item.get("worker_run_id")
        if run_id:
            resolved_by_run.setdefault(run_id, []).append(item)

    stall_after = float(config.get("supervisor", {}).get("stall_after_seconds", 300))
    now = datetime.now(timezone.utc)
    if provider_report is None:
        provider_report = load_provider_report(config)
    changed = retry_due_workers(config, state, provider_report, now) or changed
    poll_counts = {
        "marker_updates": 0,
        "lease_refreshes": 0,
        "expired_lease_workers_failed": 0,
    }
    workers = state.setdefault("workers", {})
    for run_id, worker in list(workers.items()):
        previous_last_event_at = worker.get("last_event_at")
        if worker.get("queue_event_id") and worker.get("queue_event_id") not in valid_queue_event_ids:
            if worker.get("status") in {"running", "waiting_approval", "retry_backoff", "manual_pending", "stalled"} and not pid_is_alive(worker.get("pid")):
                task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
                workers.pop(run_id, None)
                write_activity_log(
                    config,
                    {
                        "type": "worker_reaped",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": (
                            "Dropped orphaned worker after its queue event disappeared; open tasks will be redispatched."
                            if task_status in {"todo", "in_progress", "review", "blocked"}
                            else "Dropped orphaned worker after its queue event disappeared."
                        ),
                        "worker_run_id": worker.get("run_id"),
                    },
                )
                changed = True
                continue
        marker_changed = update_worker_runtime_markers(worker)
        if marker_changed:
            poll_counts["marker_updates"] += 1
            changed = True
        update_from_log(config, worker)
        alive = pid_is_alive(worker.get("pid"))
        if alive and worker.get("status") in active_worker_statuses and worker.get("last_heartbeat_at"):
            if not worker_heartbeat_is_stale(config, worker, now):
                refresh_worker_lease(config, worker, now)
                poll_counts["lease_refreshes"] += 1
                if worker.get("queue_event_id"):
                    record = queue_status(state, worker["queue_event_id"])
                    record["lease_owner"] = worker.get("run_id")
                    record["lease_expires_at"] = queue_lease_expiry(config, now)
        if alive and worker.get("status") in active_worker_statuses and worker_lease_is_expired(config, worker, now):
            terminate_worker_pid(worker.get("pid"))
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker lease expired after heartbeat became stale."
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": worker["last_error"],
                    "worker_run_id": worker.get("run_id"),
                },
            )
            finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            poll_counts["expired_lease_workers_failed"] += 1
            changed = True
            continue
        if (
            alive
            and worker.get("status") in active_worker_statuses
            and chair_review_worker_artifacts_applied(state, worker)
        ):
            terminate_worker_pid(worker.get("pid"))
            worker["status"] = "completed"
            worker["last_event_at"] = utc_now()
            clear_task_failure_streak(state, worker=worker)
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Chair review artifacts were accepted; terminated lingering control runner.",
                    "worker_run_id": worker["run_id"],
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            finalize_queue_event_record(config, state, worker, "completed")
            changed = True
            continue
        last_event_advanced = bool(
            previous_last_event_at
            and worker.get("last_event_at")
            and worker.get("last_event_at") > previous_last_event_at
        )
        if manual_pending_inbox_can_auto_redeliver(config, state, provider_report, worker):
            changed = (
                requeue_stale_manual_pending_worker(
                    config,
                    state,
                    worker,
                    reason=(
                        "Cleared stale file_inbox/manual_pending worker after provider auto-delivery became available; "
                        "queue event returned to queued for redispatch."
                    ),
                )
                or changed
            )
            continue
        if (
            worker.get("queue_event_id")
            and not worker_matches_current_assignment(config, worker, task_map)
        ):
            if worker.get("status") == "superseded":
                continue
            if alive:
                terminate_worker_pid(worker.get("pid"))
            worker["status"] = "superseded"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker superseded after task responsibility moved to another agent."
            finalize_queue_event_record(
                config,
                state,
                worker,
                "completed",
                worker["last_error"],
            )
            write_activity_log(
                config,
                {
                    "type": "worker_superseded",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": worker["last_error"],
                    "worker_run_id": worker.get("run_id"),
                },
            )
            console_log(
                f"worker superseded: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            changed = True
            continue
        if (
            worker.get("queue_event_id")
            and worker.get("status") in active_worker_statuses
            and higher_priority_ready_task_exists(config, worker, task_map, state)
        ):
            if alive:
                terminate_worker_pid(worker.get("pid"))
            worker["status"] = "superseded"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker superseded to prioritize higher-priority review/finalize work."
            finalize_queue_event_record(
                config,
                state,
                worker,
                "completed",
                worker["last_error"],
            )
            sync_preempted_task_status(config, worker)
            write_activity_log(
                config,
                {
                    "type": "worker_superseded",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": worker["last_error"],
                    "worker_run_id": worker.get("run_id"),
                },
            )
            console_log(
                f"worker superseded for priority escalation: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            changed = True
            continue
        if (
            not alive
            and worker.get("queue_event_id")
            and worker.get("status") in {"fallback", "manual_pending", "retry_backoff", "stalled", "waiting_approval", "suspended_approval"}
            and not worker_matches_current_assignment(config, worker, task_map)
        ):
            workers.pop(run_id, None)
            finalize_queue_event_record(
                config,
                state,
                worker,
                "completed",
                "Dropped stale worker after task ownership/review assignment moved to another agent.",
            )
            write_activity_log(
                config,
                {
                    "type": "worker_reaped",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Dropped stale worker after task responsibility moved to another agent.",
                    "worker_run_id": worker.get("run_id"),
                },
            )
            changed = True
            continue
        pending = pending_by_run.get(worker["run_id"], [])
        resolved = resolved_by_run.get(worker["run_id"], [])
        if pending:
            if not alive and not worker_supports_approval_resume(config, worker):
                worker["status"] = "failed"
                worker["deferred_action"] = None
                worker["deferred_tool_use"] = None
                worker["last_event_at"] = utc_now()
                worker["last_error"] = "Worker exited while waiting for approval."
                for approval in pending:
                    approval_id = approval.get("approval_id")
                    if not approval_id:
                        continue
                    try:
                        resolve_approval(
                            config,
                            approval_id,
                            decision="deny",
                            note="Auto-denied because the worker exited before approval could be applied.",
                            remember=False,
                        )
                    except KeyError:
                        pass
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
                changed = True
                continue
            approval = pending[0]
            next_status = "waiting_approval" if pid_is_alive(worker.get("pid")) else "suspended_approval"
            if worker.get("status") != next_status:
                worker["status"] = next_status
                worker["deferred_action"] = approval.get("approval_id")
                worker["last_event_at"] = approval.get("created_at") or worker.get("last_event_at") or utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_waiting_approval",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": (
                            f"Worker suspended for approval {approval.get('approval_id')}"
                            if next_status == "suspended_approval"
                            else f"Worker waiting on approval {approval.get('approval_id')}"
                        ),
                        "worker_run_id": worker["run_id"],
                        "approval_id": approval.get("approval_id"),
                    },
                )
                if worker.get("queue_event_id"):
                    queue_status(state, worker["queue_event_id"])["status"] = "manual_pending"
                changed = True
            continue

        if worker.get("status") in {"waiting_approval", "suspended_approval"} and resolved:
            latest = resolved[-1]
            if latest.get("approval_id") != worker.get("last_approval_id"):
                worker["last_approval_id"] = latest.get("approval_id")
                if latest.get("decision") == "allow" and _provider_uses_claude_cli(config, worker.get("provider")):
                    resumed = resume_claude_worker(config, worker, provider_report, approval=latest)
                    write_activity_log(
                        config,
                        {
                            "type": "worker_resumed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Resumed worker after approval {latest.get('approval_id')}",
                            "worker_run_id": worker["run_id"],
                            "approval_id": latest.get("approval_id"),
                            "command": resumed.get("command") if resumed else None,
                            "log_path": resumed.get("log_path") if resumed else None,
                            "allowed_tools": resumed.get("allowed_tools") if resumed else None,
                        },
                    )
                    changed = True
                    if resumed:
                        continue
                if latest.get("decision") == "deny":
                    worker["status"] = "failed"
                    worker["last_event_at"] = utc_now()
                    write_activity_log(
                        config,
                        {
                            "type": "worker_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": latest.get("note") or "Worker approval denied.",
                            "worker_run_id": worker["run_id"],
                            "approval_id": latest.get("approval_id"),
                        },
                    )
                    finalize_queue_event_record(config, state, worker, "failed", latest.get("note") or "Worker approval denied.")
                    changed = True
                    continue
            changed = True

        current_status = worker.get("status")
        if current_status in {"waiting_approval", "suspended_approval"} and not pending:
            worker["deferred_action"] = None
            worker["deferred_tool_use"] = None
            if not resolved:
                worker["last_approval_id"] = None
            if alive:
                worker["status"] = "running"
                worker["last_event_at"] = utc_now()
            else:
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = (
                    "Approval state disappeared before the worker could resume."
                    if current_status == "waiting_approval"
                    else "Approval state disappeared before the suspended worker could resume."
                )
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            changed = True

        if alive:
            if worker.get("status") == "stalled" and last_event_advanced:
                worker["status"] = "running"
                worker["last_event_at"] = worker.get("last_event_at") or utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_recovered",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Worker produced new output after being marked stalled; status restored to running.",
                        "worker_run_id": worker["run_id"],
                    },
                )
                console_log(
                    f"worker recovered: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                    quiet=SUPERVISOR_LOG_QUIET,
                )
                changed = True
                continue
            last_event = worker.get("last_event_at")
            if last_event:
                last_dt = datetime.fromisoformat(last_event.replace("Z", "+00:00"))
                stalled_for_seconds = (now - last_dt).total_seconds()
                if worker.get("status") == "stalled" and stalled_for_seconds >= stall_after * 2:
                    terminate_worker_pid(worker.get("pid"))
                    worker["status"] = "failed"
                    worker["last_event_at"] = utc_now()
                    worker["last_error"] = f"Worker remained stalled for {int(stalled_for_seconds)} seconds and was terminated for redispatch."
                    write_activity_log(
                        config,
                        {
                            "type": "worker_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": worker["last_error"],
                            "worker_run_id": worker["run_id"],
                        },
                    )
                    finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
                    console_log(
                        f"worker terminated after extended stall: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                        quiet=SUPERVISOR_LOG_QUIET,
                    )
                    changed = True
                    continue
                if (now - last_dt).total_seconds() >= stall_after and worker.get("status") != "stalled":
                    worker["status"] = "stalled"
                    write_activity_log(
                        config,
                        {
                            "type": "worker_stalled",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Worker appears stalled after {int(stall_after)} seconds.",
                            "worker_run_id": worker["run_id"],
                        },
                    )
                    changed = True
            continue

        failure_reason = None if worker_runner_succeeded(worker) else detect_worker_failure(worker)
        if failure_reason and worker.get("status") != "failed":
            failure = classify_worker_failure(config, worker, failure_reason)
            failure_summary = summarize_failure_reason(failure_reason, str(worker.get("provider") or worker.get("agent_id") or ""))
            raw_ref = write_failure_evidence(
                config,
                worker=worker,
                reason=failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_count = record_task_failure_streak(
                state,
                worker,
                failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            console_log(
                f"worker failure: provider={worker.get('provider')} task={worker.get('task_id')} kind={failure.get('label')} transient={'yes' if failure.get('transient') else 'no'} reason={failure_reason}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            failure_kind = str(failure.get("kind") or "")
            if should_pause_dispatch_for_failure_kind(failure_kind):
                mark_provider_dispatch_paused(
                    config,
                    state,
                    str(worker.get("provider") or worker.get("agent_id") or ""),
                    failure_reason,
                    task_id=str(worker.get("task_id") or ""),
                    worker_run_id=str(worker.get("run_id") or ""),
                    failure_kind=str(failure.get("kind") or ""),
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                )
            if is_terminal_quota_failure_kind(failure_kind):
                reassigned_to = maybe_reassign_task_after_worker_failure(
                    config,
                    state,
                    worker,
                    failure_summary.get("summary") or failure_reason,
                    terminal=True,
                    force=True,
                    failure_count=failure_count,
                )
                if reassigned_to:
                    worker["status"] = "reassigned"
                    worker["reassigned_to"] = reassigned_to
                    worker["last_error"] = failure_summary.get("summary") or failure_reason
                    worker["last_error_raw_ref"] = raw_ref
                    worker["last_event_at"] = utc_now()
                    finalize_queue_event_record(config, state, worker, "completed")
                    changed = True
                    continue
                worker["status"] = "failed"
                worker["last_error"] = failure_summary.get("summary") or failure_reason
                worker["last_error_raw_ref"] = raw_ref
                worker["last_event_at"] = utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": failure_summary.get("summary") or failure_reason,
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                        "raw_ref": raw_ref,
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", failure_reason)
                changed = True
                continue
            if is_transient_worker_failure(config, worker, failure_reason):
                handled, retry_changed = maybe_trigger_retry_or_fallback(config, state, provider_report, worker, failure_reason)
                if handled:
                    changed = changed or retry_changed
                    continue
            reassigned_to = maybe_reassign_task_after_worker_failure(
                config,
                state,
                worker,
                failure_summary.get("summary") or failure_reason,
                terminal=True,
                failure_count=failure_count,
            )
            if reassigned_to:
                worker["status"] = "reassigned"
                worker["reassigned_to"] = reassigned_to
                worker["last_error"] = failure_summary.get("summary") or failure_reason
                worker["last_error_raw_ref"] = raw_ref
                worker["last_event_at"] = utc_now()
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            worker["status"] = "failed"
            worker["last_error"] = failure_summary.get("summary") or failure_reason
            worker["last_error_raw_ref"] = raw_ref
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": failure_summary.get("summary") or failure_reason,
                    "worker_run_id": worker["run_id"],
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                    "raw_ref": raw_ref,
                },
            )
            finalize_queue_event_record(config, state, worker, "failed", failure_summary.get("summary") or failure_reason)
            changed = True
            continue

        if worker.get("status") not in {"completed", "failed", "manual_pending"}:
            if worker_is_discussion_planning(worker):
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                clear_task_failure_streak(state, worker=worker)
                write_activity_log(
                    config,
                    {
                        "type": "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Discussion planning worker exited.",
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            if worker_is_coordination_dispatch(worker):
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                clear_task_failure_streak(state, worker=worker)
                write_activity_log(
                    config,
                    {
                        "type": "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Coordination worker exited after completing its handoff step.",
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            if worker_is_chair_review(worker):
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                clear_task_failure_streak(state, worker=worker)
                write_activity_log(
                    config,
                    {
                        "type": "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Chair review worker exited; supervisor will validate the review artifacts.",
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "completed")
                changed = True
                continue
            task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
            terminal_statuses = {
                str(value).lower()
                for value in ready_dispatch_settings(config).get("worker_terminal_statuses", ["done", "review_approved"])
            }
            if task_status in redispatch_statuses:
                failure_count = record_task_failure_streak(
                    state,
                    worker,
                    GENERIC_WORKER_EXIT_REASON,
                    failure_kind="generic_exit",
                )
                generic_threshold = max(1, int(provider_guardrail_settings(config).get("generic_exit_reassign_after", 2)))
                reassigned_to = None
                if failure_count >= generic_threshold:
                    reassigned_to = maybe_reassign_task_after_worker_failure(
                        config,
                        state,
                        worker,
                        GENERIC_WORKER_EXIT_REASON,
                        terminal=True,
                        force=True,
                        failure_count=failure_count,
                    )
                if reassigned_to:
                    worker["status"] = "reassigned"
                    worker["reassigned_to"] = reassigned_to
                    worker["last_error"] = GENERIC_WORKER_EXIT_REASON
                    worker["last_event_at"] = utc_now()
                    finalize_queue_event_record(config, state, worker, "completed")
                    changed = True
                    continue
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = GENERIC_WORKER_EXIT_REASON
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            elif task_status in terminal_statuses:
                worker["status"] = "completed"
                worker["last_event_at"] = utc_now()
                clear_task_failure_streak(state, worker=worker)
                write_activity_log(
                    config,
                    {
                        "type": "worker_completed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": "Background worker process exited.",
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "completed")
            else:
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = GENERIC_WORKER_EXIT_REASON
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": worker["last_error"],
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(config, state, worker, "failed", worker["last_error"])
            changed = True
    record_worker_runtime_measurement(
        config,
        state,
        "poll_workers",
        poll_counts,
        emit_activity=bool(poll_counts["expired_lease_workers_failed"]),
    )
    return changed


def worker_worktree_housekeeping_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktree_housekeeping")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "tick_interval_seconds": int(settings.get("tick_interval_seconds", 600) or 0),
        "base_branches": [str(b).strip() for b in (settings.get("base_branches") or ["dev", "master", "main"]) if str(b).strip()],
        "max_removals_per_tick": int(settings.get("max_removals_per_tick", 5)),
    }


def _scan_process_paths_in_root(base_root: Path) -> set[Path]:
    """Return resolved paths under base_root mentioned in any live process cmdline."""
    base_str = str(base_root)
    referenced: set[Path] = set()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return referenced
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        if int(name) == self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if base_str not in cmdline:
            continue
        for tok in cmdline.split(" "):
            if tok.startswith(base_str):
                try:
                    referenced.add(Path(tok).resolve())
                except OSError:
                    pass
    return referenced


def prune_orphan_worktrees(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Remove finished worker worktrees whose branches are merged and tree is clean."""
    settings = worker_worktree_housekeeping_settings(config)
    if not settings["enabled"]:
        return False

    interval = settings["tick_interval_seconds"]
    bucket = state.setdefault("worker_worktree_housekeeping", {})
    if interval > 0:
        last_at = bucket.get("last_run_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(timezone.utc)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False
    bucket["last_run_at"] = utc_now()

    worktree_settings = worker_worktree_settings(config)
    if not worktree_settings.get("enabled", False):
        return False
    base_root = _worker_worktree_base_root(config, worktree_settings)
    if not base_root.exists():
        return False
    repo_root = config_path(config, "status_file").parents[0]

    claimed_paths: set[Path] = set()
    for worker in state.get("workers", {}).values():
        wp = worker.get("workspace_path")
        if not wp:
            continue
        try:
            claimed_paths.add(Path(str(wp)).resolve())
        except OSError:
            continue

    live_paths = _scan_process_paths_in_root(base_root)

    merged_branches: set[str] = set()
    for ref in settings["base_branches"]:
        for candidate in (f"origin/{ref}", ref):
            if not _git_ref_exists(repo_root, candidate):
                continue
            proc = subprocess.run(
                ["git", "branch", "--merged", candidate, "--list", "task/*"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                continue
            for line in proc.stdout.splitlines():
                name = line.strip().lstrip("*").strip()
                if name:
                    merged_branches.add(name)
    if not merged_branches:
        return False

    max_removals = max(0, settings["max_removals_per_tick"])
    base_root_str = str(base_root)
    removed: list[str] = []
    for record in _git_worktree_records(repo_root):
        if len(removed) >= max_removals:
            break
        wt_value = record.get("worktree")
        if not wt_value or not wt_value.startswith(base_root_str):
            continue
        try:
            wt_path = Path(wt_value).resolve()
        except OSError:
            continue
        if wt_path in claimed_paths:
            continue
        if any(str(live).startswith(str(wt_path)) or str(wt_path).startswith(str(live)) for live in live_paths):
            continue
        branch = _worktree_record_branch(record)
        if not branch or branch not in merged_branches:
            continue
        status_proc = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode != 0 or status_proc.stdout.strip():
            continue
        remove_proc = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "remove", str(wt_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if remove_proc.returncode == 0:
            removed.append(str(wt_path))

    if removed:
        write_activity_log(
            config,
            {
                "type": "worktree_pruned",
                "message": f"Pruned {len(removed)} orphan worker worktree(s): {', '.join(removed)}",
            },
        )
        return True
    return False


def auto_commit_archive_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("auto_commit_archive")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "tick_interval_seconds": int(settings.get("tick_interval_seconds", 1800) or 0),
        "script_timeout_seconds": int(settings.get("script_timeout_seconds", 180)),
    }


def maybe_auto_commit_archive(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Periodically run .orchestrator/auto_commit_archive.py so supervisor-side
    archive metadata + task briefs are not stranded as untracked files in the
    main worktree. Returns True iff the script ran AND produced a PR (so the
    caller can mark state as changed and refresh runtime artifacts)."""
    settings = auto_commit_archive_settings(config)
    if not settings["enabled"]:
        return False

    interval = settings["tick_interval_seconds"]
    bucket = state.setdefault("auto_commit_archive", {})
    if interval > 0:
        last_at = bucket.get("last_run_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(timezone.utc)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False
    bucket["last_run_at"] = utc_now()

    try:
        repo_root = config_path(config, "status_file").parents[0]
    except KeyError:
        bucket["last_error"] = "status_file path not configured"
        return False
    script = repo_root / ".orchestrator" / "auto_commit_archive.py"
    if not script.exists():
        bucket["last_error"] = "script missing"
        return False

    try:
        proc = subprocess.run(
            ["python3", str(script), "--quiet"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=settings["script_timeout_seconds"],
        )
    except subprocess.TimeoutExpired:
        bucket["last_error"] = "timeout"
        return False
    except OSError as exc:
        bucket["last_error"] = f"spawn failed: {exc}"
        return False

    bucket["last_exit"] = proc.returncode
    stdout_tail = (proc.stdout or "").strip().splitlines()[-1:] if proc.stdout else []
    stderr_tail = (proc.stderr or "").strip().splitlines()[-1:] if proc.stderr else []
    bucket["last_stdout"] = stdout_tail[0] if stdout_tail else ""
    bucket["last_stderr"] = stderr_tail[0] if stderr_tail else ""
    # Script prints "auto_commit_archive: opened PR for ..." when it actually opens one.
    return proc.returncode == 0 and "opened PR for" in (proc.stdout or "")


def trim_worker_history(state: dict[str, Any], max_entries: int) -> None:
    workers = state.get("workers", {})
    if len(workers) <= max_entries:
        return
    ordered = sorted(workers.items(), key=lambda item: item[1].get("last_event_at") or "")
    state["workers"] = dict(ordered[-max_entries:])


def reconcile_queue_records(config: dict[str, Any], state: dict[str, Any]) -> bool:
    changed = False
    queue_events = state.get("queue", {}).get("events", {})
    if not queue_events:
        return False
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for event_id, record in queue_events.items():
        workers = [worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id]
        if not workers:
            continue
        if any(worker.get("status") in active_statuses for worker in workers):
            continue
        latest = sorted(workers, key=lambda item: item.get("last_event_at") or "", reverse=True)[0]
        next_status = "failed" if any(worker.get("status") == "failed" for worker in workers) else "completed"
        if record.get("status") != next_status:
            record["status"] = next_status
            record["processed_at"] = latest.get("last_event_at") or utc_now()
            if next_status == "failed" and latest.get("last_error"):
                record["error"] = latest.get("last_error")
            changed = True
    return changed


def _reset_queue_record_for_redispatch(record: dict[str, Any], *, reason: str) -> None:
    record["status"] = "queued"
    record["requeued_at"] = utc_now()
    record["requeue_reason"] = reason
    for key in (
        "processed_at",
        "error",
        "lease_owner",
        "lease_acquired_at",
        "lease_expires_at",
        "lease_released_at",
        "last_wait_reason",
    ):
        record.pop(key, None)


def reconcile_runtime_on_boot(config: dict[str, Any], state: dict[str, Any]) -> bool:
    changed = False
    now = datetime.now(timezone.utc)
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    redispatch_statuses = redispatch_candidate_statuses(config)
    counts = {
        "marker_updates": 0,
        "lease_refreshes": 0,
        "missing_process_workers_failed": 0,
        "expired_lease_workers_failed": 0,
        "started_queue_records_requeued": 0,
        "started_queue_records_failed": 0,
        "stale_queue_records_completed": 0,
    }
    try:
        task_map = task_index_from_status(config, load_status(config))
    except KeyError:
        task_map = {}
    workers = state.setdefault("workers", {})

    for run_id, worker in list(workers.items()):
        if worker.get("status") not in active_statuses:
            continue
        marker_changed = update_worker_runtime_markers(worker)
        if marker_changed:
            counts["marker_updates"] += 1
            changed = True
        alive = pid_is_alive(worker.get("pid"))
        missing_process = worker.get("status") in {"running", "stalled"} and not alive
        expired_lease = alive and worker_lease_is_expired(config, worker, now)
        if alive and not expired_lease and worker.get("last_heartbeat_at") and not worker_heartbeat_is_stale(config, worker, now):
            refresh_worker_lease(config, worker, now)
            counts["lease_refreshes"] += 1
            if worker.get("queue_event_id"):
                record = queue_status(state, worker["queue_event_id"])
                record["lease_owner"] = worker.get("run_id")
                record["lease_expires_at"] = queue_lease_expiry(config, now)
            changed = True
            continue
        if not missing_process and not expired_lease:
            continue
        if alive:
            terminate_worker_pid(worker.get("pid"))
        reason = (
            "Worker lease expired during supervisor boot reconciliation."
            if expired_lease
            else "Worker process missing during supervisor boot reconciliation."
        )
        runner_succeeded = worker_runner_succeeded(worker)
        if runner_succeeded and (
            worker_is_chair_review(worker) or worker_is_discussion_planning(worker) or worker_is_coordination_dispatch(worker)
        ):
            worker["status"] = "completed"
            worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
            clear_task_failure_streak(state, worker=worker)
            finalize_queue_event_record(config, state, worker, "completed")
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Control worker exited successfully during supervisor boot reconciliation.",
                    "worker_run_id": run_id,
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            changed = True
            continue

        task_status = str(task_map.get(str(worker.get("task_id") or ""), {}).get("status") or "").lower()
        terminal_statuses = {
            str(value).lower()
            for value in ready_dispatch_settings(config).get("worker_terminal_statuses", ["done", "review_approved"])
        }
        if runner_succeeded and task_status in terminal_statuses:
            worker["status"] = "completed"
            worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
            clear_task_failure_streak(state, worker=worker)
            finalize_queue_event_record(config, state, worker, "completed")
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Worker exited successfully during supervisor boot reconciliation.",
                    "worker_run_id": run_id,
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            changed = True
            continue

        if runner_succeeded:
            reason = GENERIC_WORKER_EXIT_REASON

        detected_reason = None if runner_succeeded else detect_worker_failure(worker)
        if detected_reason:
            failure = classify_worker_failure(config, worker, detected_reason)
            failure_summary = summarize_failure_reason(
                detected_reason,
                str(worker.get("provider") or worker.get("agent_id") or ""),
            )
            raw_ref = write_failure_evidence(
                config,
                worker=worker,
                reason=detected_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_count = record_task_failure_streak(
                state,
                worker,
                detected_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_kind = str(failure.get("kind") or "")
            if should_pause_dispatch_for_failure_kind(failure_kind):
                mark_provider_dispatch_paused(
                    config,
                    state,
                    str(worker.get("provider") or worker.get("agent_id") or ""),
                    detected_reason,
                    task_id=str(worker.get("task_id") or ""),
                    worker_run_id=str(worker.get("run_id") or ""),
                    failure_kind=failure_kind,
                    pause_kind=failure_kind,
                    raw_ref=raw_ref,
                )
            if is_terminal_quota_failure_kind(failure_kind):
                reassigned_to = maybe_reassign_task_after_worker_failure(
                    config,
                    state,
                    worker,
                    failure_summary.get("summary") or detected_reason,
                    terminal=True,
                    force=True,
                    failure_count=failure_count,
                )
                if reassigned_to:
                    worker["status"] = "reassigned"
                    worker["reassigned_to"] = reassigned_to
                    worker["last_event_at"] = utc_now()
                    worker["last_error"] = failure_summary.get("summary") or detected_reason
                    worker["last_error_raw_ref"] = raw_ref
                    finalize_queue_event_record(config, state, worker, "completed")
                    if expired_lease:
                        counts["expired_lease_workers_failed"] += 1
                    else:
                        counts["missing_process_workers_failed"] += 1
                    changed = True
                    continue
            reason = failure_summary.get("summary") or detected_reason
            worker["last_error_raw_ref"] = raw_ref
        worker["status"] = "failed"
        worker["last_event_at"] = utc_now()
        worker["last_error"] = reason
        finalize_queue_event_record(config, state, worker, "failed", reason)
        if expired_lease:
            counts["expired_lease_workers_failed"] += 1
        else:
            counts["missing_process_workers_failed"] += 1
        write_activity_log(
            config,
            {
                "type": "worker_failed",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": reason,
                "worker_run_id": run_id,
            },
        )
        changed = True

    queue_records = state.setdefault("queue", {}).setdefault("events", {})
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []
    for event in queued_events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        record = queue_records.get(event_id)
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or "") not in {"started", "stalled"}:
            continue
        related_active = [
            worker
            for worker in workers.values()
            if worker.get("queue_event_id") == event_id and worker.get("status") in active_statuses
        ]
        if related_active:
            continue
        skip_message = stale_dispatch_skip_message(config, event, task_map)
        if skip_message:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "stale_dispatch_event"
            record["requeue_reason"] = "started event became stale while supervisor was offline"
            counts["stale_queue_records_completed"] += 1
            changed = True
            continue
        task_status = str(task_map.get(str(event.get("task_id") or ""), {}).get("status") or "").lower()
        if task_status in redispatch_statuses:
            _reset_queue_record_for_redispatch(
                record,
                reason="started queue record had no active worker during supervisor boot reconciliation",
            )
            counts["started_queue_records_requeued"] += 1
        else:
            record["status"] = "failed"
            record["processed_at"] = utc_now()
            record["error"] = "Started queue record had no active worker and task is no longer redispatchable."
            counts["started_queue_records_failed"] += 1
        changed = True
    corrective_counts = {
        key: counts[key]
        for key in (
            "missing_process_workers_failed",
            "expired_lease_workers_failed",
            "started_queue_records_requeued",
            "started_queue_records_failed",
            "stale_queue_records_completed",
        )
    }
    record_worker_runtime_measurement(
        config,
        state,
        "boot_reconciliation",
        counts,
        emit_activity=bool(positive_runtime_counts(corrective_counts)),
    )
    return changed

def helper_claim_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(ready_dispatch_settings(config).get("helper_claim", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("task_statuses", ["todo", "in_progress"])
    settings.setdefault("paused_owner_task_statuses", ["in_progress"])
    settings.setdefault("require_owner_higher_priority_load", True)
    settings.setdefault("claim_idle_work", False)
    settings.setdefault("claim_sidecars_when_idle", False)
    settings.setdefault("disable_when_failure_loops", True)
    return settings


def worker_self_claim_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(ready_dispatch_settings(config).get("worker_self_claim", {}) or {})
    settings.setdefault("enabled", False)
    settings.setdefault("release_task_statuses", ["review", "review_approved", "done", "blocked"])
    return settings


def release_completed_worker_for_claim(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    agent_name: str,
    task_id: str | None,
) -> bool:
    if not task_id:
        return False
    settings = worker_self_claim_settings(config)
    allowed_statuses = {str(value).lower() for value in settings.get("release_task_statuses", [])}
    if not allowed_statuses:
        return False
    status = load_status(config)
    task = task_index_from_status(config, status).get(task_id)
    if not task or str(task.get("status") or "").lower() not in allowed_statuses:
        return False

    normalized_agent = normalize_agent_id(agent_name)
    display_agent = display_name_for(config, normalized_agent)
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    now = utc_now()
    changed = False
    for worker in state.get("workers", {}).values():
        worker_agent = str(worker.get("logical_agent_id") or worker.get("agent_id") or "").strip()
        if worker.get("task_id") != task_id:
            continue
        if display_name_for(config, normalize_agent_id(worker_agent)) != display_agent:
            continue
        if worker.get("status") not in active_statuses:
            continue
        worker["status"] = "completed"
        worker["completed_at"] = now
        worker["last_event_at"] = now
        worker["last_error"] = None
        finalize_queue_event_record(config, state, worker, "completed")
        changed = True
        write_activity_log(
            config,
            {
                "type": "worker_self_claim_released",
                "task_id": task_id,
                "message": f"{display_agent} released completed worker slot before self-claim.",
                "worker_run_id": worker.get("run_id"),
                "queue_event_id": worker.get("queue_event_id"),
            },
        )
    return changed


def underutilization_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("underutilization_dispatch", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("require_recent_chair_signal", True)
    settings.setdefault("threshold_ratio", 0.5)
    settings.setdefault("continuous_window_seconds", 900)
    settings.setdefault("cooldown_seconds", 900)
    settings.setdefault("max_new_sidecars_per_wave", None)
    settings.setdefault("max_active_sidecars_per_agent", 1)
    settings.setdefault("respect_chair_max_sidecars", False)
    settings.setdefault(
        "productive_worker_statuses",
        ["running", "waiting_approval", "suspended_approval", "retry_backoff"],
    )
    return settings


def sidecar_wave_limit(raw_value: Any) -> int | None:
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, str) and raw_value.strip().lower() in {"none", "null", "unlimited", "false"}:
        return None
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return None


def load_sidecar_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    path_value = config.get("sidecar_catalog_path") or config.get("paths", {}).get("sidecar_catalog")
    if not path_value:
        return []
    payload = load_json(config_path(config, "sidecar_catalog") if "sidecar_catalog" in config.get("paths", {}) else Path(path_value), default={})
    if isinstance(payload, dict):
        templates = payload.get("templates", [])
        if isinstance(templates, list):
            return [dict(item) for item in templates if isinstance(item, dict)]
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    return []


def configured_worker_lane_ids(config: dict[str, Any]) -> list[str]:
    lanes: list[str] = []
    seen: set[str] = set()
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        if agent_is_dispatch_slot(agent):
            continue
        display_name = str(agent.get("display_name") or agent.get("name") or agent_id)
        if "legacy alias" in display_name.lower():
            continue
        lane_id = normalize_agent_id(agent.get("provider") or agent_id)
        if not lane_id or lane_id in seen:
            continue
        seen.add(lane_id)
        lanes.append(lane_id)
    return lanes


def productive_worker_lane_ids(config: dict[str, Any], state: dict[str, Any], productive_statuses: set[str]) -> set[str]:
    lanes: set[str] = set()
    for worker in state.get("workers", {}).values():
        if str(worker.get("status") or "") not in productive_statuses:
            continue
        lane_id = normalize_agent_id(worker.get("provider") or worker.get("agent_id") or "")
        if lane_id:
            lanes.add(lane_id)
    return lanes


def utilization_ratio_for_sidecars(config: dict[str, Any], state: dict[str, Any], productive_statuses: set[str]) -> float:
    lanes = configured_worker_lane_ids(config)
    if not lanes:
        return 1.0
    productive = productive_worker_lane_ids(config, state, productive_statuses)
    return len(productive) / len(lanes)


def task_is_sidecar(task: dict[str, Any]) -> bool:
    return str(task.get("task_class") or "").strip().lower() == "sidecar"


def task_is_human_gate(task: dict[str, Any]) -> bool:
    task_class = str(task.get("task_class") or "").strip().lower()
    gate_status = str(task.get("gate_status") or "").strip().lower()
    return (
        task_class == "human_gate"
        or bool(task.get("human_required_roles"))
        or gate_status.startswith("pending_human")
    )


def chair_blocked_owner_rescue_allowed(task: dict[str, Any]) -> bool:
    if str(task.get("status") or "").strip().lower() != "blocked":
        return False
    if task_is_human_gate(task) or task_is_sidecar(task) or bool(task.get("non_dispatchable")):
        return False
    context = " ".join(
        str(task.get(key) or "")
        for key in (
            "next",
            "waiting_for",
            "blocker",
            "blocked_by",
            "failure_reason",
            "last_failure_reason",
            "push_status",
        )
    ).casefold()
    return any(keyword in context for keyword in BLOCKED_OWNER_RESCUE_KEYWORDS)


def sidecar_statuses() -> set[str]:
    return {"todo", "in_progress", "review", "review_approved", "blocked", "done"}


def existing_sidecar_signatures(status: dict[str, Any]) -> set[str]:
    signatures: set[str] = set()
    for task in status.get("tasks", []) or []:
        if not task_is_sidecar(task):
            continue
        parent = str(task.get("helper_parent") or "").strip()
        kind = str(task.get("helper_kind") or "").strip()
        if parent and kind:
            signatures.add(f"{parent}:{kind}")
    return signatures


def sidecar_task_id(parent_task_id: str, kind: str) -> str:
    slug = kind
    if slug.endswith("_packet"):
        slug = slug[: -len("_packet")]
    return f"{parent_task_id}-SIDECAR-{slug.replace('_', '-').upper()}"


def render_sidecar_template(value: str, variables: dict[str, str]) -> str:
    rendered = str(value)
    for key, item in variables.items():
        rendered = rendered.replace("{{" + key + "}}", item)
    return rendered


def task_phase_priority(task: dict[str, Any], task_map: dict[str, dict[str, Any]], dependency_done_statuses: set[str]) -> int:
    status = str(task.get("status") or "").lower()
    if status == "in_progress":
        return 0
    if status == "review":
        return 1
    if status == "review_approved":
        return 2
    if status == "todo" and dependencies_satisfied(task, task_map, dependency_done_statuses):
        return 3
    if status == "todo":
        return 4
    if status == "blocked":
        return 5
    return 9


def dynamic_sidecar_kind(task: dict[str, Any]) -> str | None:
    phase = str(task.get("phase") or "").lower()
    title = str(task.get("title") or "").lower()
    artifacts = " ".join(str(item).lower() for item in (task.get("artifacts") or []))
    if "persona and application surfaces" in phase or "bff" in title or "surface" in title or "bff" in artifacts:
        return "bff_handoff_packet"
    if str(task.get("status") or "").lower() in {"review", "review_approved"}:
        return "review_packet"
    return "acceptance_packet"


def preferred_agents_for_sidecar(kind: str) -> list[str]:
    mapping = {
        "review_packet": ["Codex2", "Codex", "Claude"],
        "acceptance_packet": ["Codex", "Codex2", "Claude"],
        "bff_handoff_packet": ["Claude", "Codex", "Codex2"],
    }
    return mapping.get(kind, ["Codex", "Codex2", "Claude"])


def normalize_mainline_task_assignment(config: dict[str, Any], task: dict[str, Any]) -> bool:
    if task_is_sidecar(task):
        return False
    settings = worker_reassignment_settings(config)
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return False
    task_status = str(task.get("status") or "").lower()
    eligible_statuses = {str(value).lower() for value in settings.get("eligible_statuses", [])}
    eligible_statuses.add("blocked")
    if task_status not in eligible_statuses:
        return False

    owner = str(task.get("owner") or "").strip()
    reviewer = str(task.get("reviewer") or "").strip()
    owner_allowed = agent_can_take_task(config, owner, task)
    reviewer_allowed = agent_can_take_task(config, reviewer, task)
    if owner_allowed and reviewer_allowed:
        return False

    new_owner = owner
    new_reviewer = reviewer
    changed_fields: list[str] = []

    if owner and not owner_allowed:
        owner_candidates = normalized_mapping_values(settings.get("owner_fallbacks", {}), owner)
        replacement_owner = first_viable_agent(config, owner_candidates, exclude={owner, reviewer}, task=task)
        if not replacement_owner:
            return False
        new_owner = replacement_owner
        changed_fields.append(f"owner {owner} -> {new_owner}")

    if not reviewer or not reviewer_allowed or reviewer == new_owner:
        reviewer_candidates: list[str] = []
        if reviewer:
            reviewer_candidates.append(reviewer)
            reviewer_candidates.extend(normalized_mapping_values(settings.get("reviewer_fallbacks", {}), reviewer))
        if owner:
            reviewer_candidates.extend(normalized_mapping_values(settings.get("reviewer_fallbacks", {}), owner))
            reviewer_candidates.extend(normalized_mapping_values(settings.get("owner_fallbacks", {}), owner))
        replacement_reviewer = first_viable_agent(config, reviewer_candidates, exclude={new_owner}, task=task)
        if not replacement_reviewer:
            return False
        new_reviewer = replacement_reviewer
        if replacement_reviewer != reviewer:
            changed_fields.append(f"reviewer {reviewer or '(unset)'} -> {new_reviewer}")

    if new_owner == owner and new_reviewer == reviewer:
        return False

    blocked_agents = [
        agent_name
        for agent_name in (owner, reviewer)
        if agent_name and not agent_can_take_task(config, agent_name, task)
    ]
    blocked_summary = ", ".join(dict.fromkeys(blocked_agents)) or "disallowed lane"
    message = (
        f"Auto-reassigned {task_id} away from sidecar-only lane {blocked_summary}; "
        f"{', '.join(changed_fields)}. Reserved sidecar-only agents no longer hold mainline tasks."
    )
    if not persist_task_reassignment(
        config,
        task_id=task_id,
        new_owner=new_owner,
        new_reviewer=new_reviewer,
        message=message,
        handoff_to=new_owner if new_owner != owner else new_reviewer,
        handoff_from=owner if new_owner != owner else reviewer,
    ):
        return False
    write_activity_log(
        config,
        {
            "type": "task_reassigned",
            "task_id": task_id,
            "message": message,
            "from_owner": owner,
            "to_owner": new_owner,
            "from_reviewer": reviewer,
            "to_reviewer": new_reviewer,
            "policy": "sidecar_only_agent_mainline_guard",
        },
    )
    console_log(
        f"policy reassignment: task={task_id} owner={owner}->{new_owner} reviewer={reviewer}->{new_reviewer}",
        quiet=SUPERVISOR_LOG_QUIET,
    )
    return True


def agent_has_dispatchable_primary_work(
    config: dict[str, Any],
    status: dict[str, Any],
    agent_name: str,
    task_map: dict[str, dict[str, Any]],
) -> bool:
    settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    for task in status.get("tasks", []) or []:
        if task_is_sidecar(task):
            continue
        if not agent_can_take_task(config, agent_name, task):
            continue
        task_status = str(task.get("status") or "").lower()
        if task_status in review_statuses and task.get("reviewer") == agent_name:
            return True
        if task_status in finalize_statuses and task.get("owner") == agent_name:
            return True
        if task.get("owner") != agent_name:
            continue
        if task_status == "in_progress" and dependencies_satisfied(task, task_map, dependency_done_statuses):
            return True
        if task_status == "todo" and dependencies_satisfied(task, task_map, dependency_done_statuses):
            return True
    return False


def count_open_sidecars_for_agent(status: dict[str, Any], agent_name: str) -> int:
    count = 0
    for task in status.get("tasks", []) or []:
        if task.get("owner") != agent_name:
            continue
        if not task_is_sidecar(task):
            continue
        if str(task.get("status") or "").lower() == "done":
            continue
        count += 1
    return count


def workload_targets(status: dict[str, Any]) -> dict[str, float]:
    raw = status.get("workload")
    if not isinstance(raw, dict):
        return {}
    targets: dict[str, float] = {}
    for name, value in raw.items():
        try:
            targets[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    return targets


def open_owner_counts(status: dict[str, Any], owner_field: str = "owner") -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    total = 0
    for task in status.get("tasks", []) or []:
        task_status = str(task.get("status") or "").lower()
        if task_status in {"done", "superseded"}:
            continue
        owner = str(task.get(owner_field) or "").strip()
        if not owner:
            continue
        counts[owner] = counts.get(owner, 0) + 1
        total += 1
    return counts, total


def agent_within_target_workload_for_assignment(
    status: dict[str, Any],
    agent_name: str,
    *,
    owner_field: str = "owner",
    previous_owner: str | None = None,
    creates_new_task: bool = False,
) -> bool:
    targets = workload_targets(status)
    target = targets.get(agent_name)
    if target is None:
        return True

    counts, total = open_owner_counts(status, owner_field)
    current_count = counts.get(agent_name, 0)
    if previous_owner and previous_owner != agent_name:
        counts[previous_owner] = max(0, counts.get(previous_owner, 0) - 1)
        counts[agent_name] = current_count + 1
    elif creates_new_task:
        total += 1
        counts[agent_name] = current_count + 1
    else:
        counts[agent_name] = current_count + 1

    if current_count <= 0:
        return True
    if total <= 0:
        return True
    projected_share = (counts.get(agent_name, 0) / total) * 100
    return projected_share <= target


def eligible_idle_agents_for_sidecars(
    config: dict[str, Any],
    state: dict[str, Any],
    status: dict[str, Any],
    *,
    max_active_sidecars_per_agent: int,
    provider_report: dict[str, Any] | None = None,
) -> list[str]:
    settings = ready_dispatch_settings(config)
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, _pending_task_agents, _pending_event_keys = outstanding_delivery_indexes(config, state)
    task_map = task_index_from_status(config, status)
    owner_field = config.get("schema", {}).get("assignee_field", "owner")
    agents: list[str] = []
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        if agent_is_dispatch_slot(agent):
            continue
        display_name = str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        if "legacy alias" in display_name.lower():
            continue
        normalized = normalize_agent_id(agent_id)
        if agent_auto_dispatch_block_reason(config, state, normalized, provider_report):
            continue
        if normalized in active_agents or normalized in pending_agents:
            continue
        if count_open_sidecars_for_agent(status, display_name) >= max_active_sidecars_per_agent:
            continue
        if agent_has_dispatchable_primary_work(config, status, display_name, task_map):
            continue
        if not agent_within_target_workload_for_assignment(
            status,
            display_name,
            owner_field=owner_field,
            creates_new_task=True,
        ):
            continue
        agents.append(display_name)
    return agents


def sidecar_support_artifact(parent_task_id: str, sidecar_id: str) -> str:
    return f"support/sidecars/{parent_task_id}/{sidecar_id}.md"


def build_catalog_sidecar_candidates(
    config: dict[str, Any],
    status: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    existing_signatures: set[str],
) -> list[dict[str, Any]]:
    settings = ready_dispatch_settings(config)
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    resolver = TaskResolver(task_map)
    templates = load_sidecar_catalog(config)
    candidates: list[dict[str, Any]] = []
    for template in templates:
        kind = str(template.get("kind") or "").strip()
        if not kind:
            continue
        parent_ids = [str(item).strip() for item in template.get("parent_task_ids", []) if str(item).strip()]
        phase_match = str(template.get("parent_phase_match") or "").strip()
        activation_dependencies = [str(item).strip() for item in template.get("activation_dependencies", []) if str(item).strip()]
        for parent in status.get("tasks", []) or []:
            if task_is_sidecar(parent):
                continue
            parent_id = str(parent.get("id") or "").strip()
            if not parent_id:
                continue
            if parent_ids and parent_id not in parent_ids:
                continue
            if not parent_ids and phase_match and str(parent.get("phase") or "") != phase_match:
                continue
            if not parent_ids and not phase_match:
                continue
            if str(parent.get("status") or "").lower() == "done":
                continue
            if any(resolver.dependency_status(dep) not in dependency_done_statuses or not resolver.dependency_satisfied(dep) for dep in activation_dependencies):
                continue
            signature = f"{parent_id}:{kind}"
            if signature in existing_signatures:
                continue
            reviewer = str(parent.get("owner") or "").strip()
            if not reviewer:
                continue
            sidecar_id = sidecar_task_id(parent_id, kind)
            variables = {
                "parent_task_id": parent_id,
                "parent_title": str(parent.get("title") or ""),
                "parent_phase": str(parent.get("phase") or ""),
                "sidecar_task_id": sidecar_id,
                "kind": kind,
                "kind_slug": kind.replace("_", "-"),
            }
            artifact_targets = [
                render_sidecar_template(str(item), variables)
                for item in (template.get("artifact_targets") or [])
                if str(item).strip()
            ] or [sidecar_support_artifact(parent_id, sidecar_id)]
            candidates.append(
                {
                    "template_id": str(template.get("template_id") or sidecar_id),
                    "kind": kind,
                    "parent_task_id": parent_id,
                    "parent_task": parent,
                    "sidecar_id": sidecar_id,
                    "title": render_sidecar_template(str(template.get("title_template") or sidecar_id), variables),
                    "summary_zh": render_sidecar_template(str(template.get("summary_zh_template") or ""), variables),
                    "phase": str(parent.get("phase") or "Support"),
                    "depends_on": activation_dependencies,
                    "artifacts": artifact_targets,
                    "reviewer": reviewer,
                    "mutates_canonical": bool(template.get("mutates_canonical", False)),
                    "priority": task_phase_priority(parent, task_map, dependency_done_statuses),
                }
            )
    return candidates


def build_dynamic_sidecar_candidates(
    config: dict[str, Any],
    status: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    existing_signatures: set[str],
) -> list[dict[str, Any]]:
    settings = ready_dispatch_settings(config)
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    resolver = TaskResolver(task_map)
    candidates: list[dict[str, Any]] = []
    for parent in status.get("tasks", []) or []:
        if task_is_sidecar(parent):
            continue
        parent_id = str(parent.get("id") or "").strip()
        if not parent_id or str(parent.get("status") or "").lower() == "done":
            continue
        kind = dynamic_sidecar_kind(parent)
        if kind not in {"review_packet", "acceptance_packet", "bff_handoff_packet"}:
            continue
        signature = f"{parent_id}:{kind}"
        if signature in existing_signatures:
            continue
        parent_status = str(parent.get("status") or "").lower()
        if kind in {"review_packet", "acceptance_packet"} and parent_status == "todo" and not dependencies_satisfied(parent, task_map, dependency_done_statuses):
            continue
        activation_dependencies = [
            dep_id
            for dep_id in (parent.get("depends_on") or [])
            if resolver.dependency_status(dep_id) in dependency_done_statuses and resolver.dependency_satisfied(dep_id)
        ]
        if kind == "bff_handoff_packet" and parent.get("depends_on") and not activation_dependencies:
            continue
        reviewer = str(parent.get("owner") or "").strip()
        if not reviewer:
            continue
        sidecar_id = sidecar_task_id(parent_id, kind)
        title_by_kind = {
            "review_packet": f"Prepare {parent_id} review packet and evidence summary",
            "acceptance_packet": f"Prepare {parent_id} acceptance packet and dependency map",
            "bff_handoff_packet": f"Prepare {parent_id} BFF and frontend handoff packet",
        }
        summary_by_kind = {
            "review_packet": f"平行支援 {parent_id}，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。",
            "acceptance_packet": f"平行支援 {parent_id}，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。",
            "bff_handoff_packet": f"平行支援 {parent_id}，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。",
        }
        candidates.append(
            {
                "template_id": f"dynamic:{kind}",
                "kind": kind,
                "parent_task_id": parent_id,
                "parent_task": parent,
                "sidecar_id": sidecar_id,
                "title": title_by_kind[kind],
                "summary_zh": summary_by_kind[kind],
                "phase": str(parent.get("phase") or "Support"),
                "depends_on": activation_dependencies,
                "artifacts": [sidecar_support_artifact(parent_id, sidecar_id)],
                "reviewer": reviewer,
                "mutates_canonical": False,
                "priority": task_phase_priority(parent, task_map, dependency_done_statuses),
            }
        )
    return candidates


def create_sidecar_task(
    config: dict[str, Any],
    *,
    sidecar_id: str,
    owner: str,
    reviewer: str,
    phase: str,
    title: str,
    summary_zh: str,
    depends_on: list[str],
    artifacts: list[str],
    helper_parent: str,
    helper_kind: str,
    mutates_canonical: bool,
) -> tuple[bool, str]:
    script = config_path(config, "status_file").parent / "scripts" / "ai_status.py"
    metadata = {
        "task_class": "sidecar",
        "auto_generated": True,
        "helper_parent": helper_parent,
        "helper_kind": helper_kind,
        "mutates_canonical": mutates_canonical,
        "auto_created_by": "supervisor-underutilization",
    }
    env = os.environ.copy()
    env.update(
        {
            "AI_NAME": "Codex",
            "TASK_PHASE": phase,
            "TASK_TITLE": title,
            "TASK_SUMMARY_ZH": summary_zh,
            "TASK_DEPENDS_ON": ",".join(depends_on),
            "TASK_ARTIFACTS": ",".join(artifacts),
            "TASK_ACCEPTANCE": ",".join(
                [
                    "Create support artifacts only",
                    "Do not edit canonical truth",
                    "Hand off the packet to the assigned reviewer",
                ]
            ),
            "TASK_METADATA_JSON": json.dumps(metadata, ensure_ascii=False),
        }
    )
    result = subprocess.run(
        [sys.executable, str(script), "assign", sidecar_id, owner, reviewer],
        cwd=str(config_path(config, "status_file").parent),
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or "unknown error"
    return True, ""


def redispatch_candidate_statuses(config: dict[str, Any]) -> set[str]:
    settings = ready_dispatch_settings(config)
    statuses = set(str(value).lower() for value in settings.get("review_statuses", []))
    statuses.update(str(value).lower() for value in settings.get("finalize_statuses", []))
    statuses.update(str(value).lower() for value in settings.get("owned_statuses", []))
    return statuses


def _task_resolver(task_lookup: TaskResolver | dict[str, dict[str, Any]]) -> TaskResolver:
    if isinstance(task_lookup, TaskResolver):
        return task_lookup
    return TaskResolver(task_lookup)


def dependencies_satisfied(task: dict[str, Any], task_lookup: TaskResolver | dict[str, dict[str, Any]], done_statuses: set[str]) -> bool:
    resolver = _task_resolver(task_lookup)
    for dep_id in task.get("depends_on", []) or []:
        dep_status = resolver.dependency_status(dep_id)
        if dep_status not in done_statuses or not resolver.dependency_satisfied(dep_id):
            return False
    return True


def task_dependency_signature(task: dict[str, Any], task_lookup: TaskResolver | dict[str, dict[str, Any]]) -> str:
    resolver = _task_resolver(task_lookup)
    parts: list[str] = []
    for dep_id in task.get("depends_on", []) or []:
        dep_status = resolver.dependency_status(dep_id)
        parts.append(f"{dep_id}:{dep_status}")
    return "|".join(parts)


def active_worker_indexes(state: dict[str, Any], active_statuses: set[str]) -> tuple[set[str], set[tuple[str, str]]]:
    agents: set[str] = set()
    task_agents: set[tuple[str, str]] = set()
    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
        agent_id = str(worker.get("agent_id") or "")
        task_id = str(worker.get("task_id") or "")
        if agent_id:
            agents.add(agent_id)
        if task_id and agent_id:
            task_agents.add((task_id, agent_id))
    return agents, task_agents


def orphaned_queue_event_grace_seconds(config: dict[str, Any]) -> int:
    value = ready_dispatch_settings(config).get("orphaned_queue_event_grace_seconds", 300)
    try:
        return max(30, int(value))
    except (TypeError, ValueError):
        return 300


def queue_event_age_seconds(event: dict[str, Any]) -> float | None:
    created_at = _parse_iso_utc(str(event.get("created_at") or ""))
    if created_at is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds())


def queue_event_is_orphaned(
    config: dict[str, Any],
    event: dict[str, Any],
    record: dict[str, Any],
    related_workers: list[dict[str, Any]],
) -> bool:
    if related_workers:
        return False
    status = str(record.get("status") or "").lower()
    if status in {"completed", "failed"}:
        return False
    age_seconds = queue_event_age_seconds(event)
    if age_seconds is None:
        return False
    return age_seconds > orphaned_queue_event_grace_seconds(config)


def outstanding_delivery_indexes(config: dict[str, Any], state: dict[str, Any]) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    agents: set[str] = set()
    task_agents: set[tuple[str, str]] = set()
    event_keys: set[str] = set()
    queue_records = state.get("queue", {}).get("events", {})
    for event in load_event_queue(config):
        event_id = event.get("event_id")
        if not event_id:
            continue
        record = queue_records.get(event_id, {})
        related_workers = [
            worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id
        ]
        if record.get("status") in {"completed", "failed"}:
            continue
        if queue_event_is_orphaned(config, event, record, related_workers):
            continue
        event_key = str(event.get("event_key") or "")
        if event_key:
            event_keys.add(event_key)
        agent_id = str(event.get("target_agent") or "")
        task_id = str(event.get("task_id") or "")
        if agent_id:
            agents.add(agent_id)
        if task_id and agent_id:
            task_agents.add((task_id, agent_id))
    return agents, task_agents, event_keys


def finalize_queue_event_record(config: dict[str, Any], state: dict[str, Any], worker: dict[str, Any], status: str, error: str | None = None) -> None:
    queue_event_id = worker.get("queue_event_id")
    if not queue_event_id:
        return
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for item in state.get("workers", {}).values():
        if item.get("run_id") == worker.get("run_id"):
            continue
        if item.get("queue_event_id") == queue_event_id and item.get("status") in active_statuses:
            return
    record = queue_status(state, queue_event_id)
    record["status"] = status
    record["processed_at"] = utc_now()
    record["lease_released_at"] = record["processed_at"]
    if worker.get("run_id"):
        record["lease_owner"] = worker.get("run_id")
    if error:
        record["error"] = error



def save_event_queue(config: dict[str, Any], events: list[dict[str, Any]]) -> None:
    path = config_path(config, "event_queue")
    payload = "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in events)
    path.write_text(payload, encoding="utf-8")


def prune_event_queue(config: dict[str, Any], state: dict[str, Any]) -> bool:
    events = load_event_queue(config)
    if not events:
        return False
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    redispatch_statuses = redispatch_candidate_statuses(config)
    queue_events = state.setdefault("queue", {}).setdefault("events", {})
    kept: list[dict[str, Any]] = []
    kept_ids: set[str] = set()
    changed = False

    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            changed = True
            continue

        record = queue_events.get(event_id, {})
        related_workers = [worker for worker in state.get("workers", {}).values() if worker.get("queue_event_id") == event_id]
        has_active_worker = any(worker.get("status") in active_statuses for worker in related_workers)
        if queue_event_is_orphaned(config, event, record, related_workers):
            age_seconds = queue_event_age_seconds(event)
            write_activity_log(
                config,
                {
                    "type": "queue_event_pruned",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "queue_event_id": event_id,
                    "message": (
                        f"Pruned orphaned queue event after {age_seconds:.1f}s without a live worker or queue record."
                        if age_seconds is not None
                        else "Pruned orphaned queue event without a live worker or queue record."
                    ),
                },
            )
            changed = True
            continue
        skip_message = stale_dispatch_skip_message(config, event, task_map)

        if skip_message and not has_active_worker:
            completed = queue_status(state, event_id)
            completed["status"] = "completed"
            completed["processed_at"] = completed.get("processed_at") or utc_now()
            completed["skip_reason"] = "stale_dispatch_event"
            changed = True
            continue

        if not related_workers and record.get("status") in {"started", "manual_pending", "retry_backoff", "stalled"}:
            record["status"] = "queued"
            record.pop("processed_at", None)
            record.pop("error", None)
            changed = True
            kept.append(event)
            kept_ids.add(event_id)
            continue

        current_task = task_map.get(str(event.get("task_id") or ""))
        current_status = str(current_task.get("status") or "").lower() if current_task else ""

        if record.get("status") == "failed" and not has_active_worker and current_status in redispatch_statuses:
            changed = True
            continue

        if record.get("status") in {"completed", "failed"} and not has_active_worker:
            changed = True
            continue

        kept.append(event)
        kept_ids.add(event_id)

    if not changed:
        return False

    state.setdefault("queue", {}).setdefault("events", {})
    state["queue"]["events"] = {event_id: record for event_id, record in queue_events.items() if event_id in kept_ids}
    save_event_queue(config, kept)
    return True


def task_status_map(status: dict[str, Any]) -> dict[str, str]:
    return {str(task.get("id")): str(task.get("status") or "") for task in status.get("tasks", []) if task.get("id")}


def task_index_from_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    return {
        str(task.get(task_id_field)): task
        for task in status.get(tasks_path, [])
        if task.get(task_id_field)
    }


def current_dispatch_event_key(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return None

    task_id = str(event.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return None

    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
    settings = ready_dispatch_settings(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    dependency_done_statuses = normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    task_status = str(task.get("status") or "").lower()

    eligible = False
    if reason == REASON_REVIEW_READY:
        eligible = task_status in review_statuses and task.get(reviewer_field) == target_agent
    elif reason == REASON_OWNED_FINALIZE:
        eligible = task_status in finalize_statuses and task.get(owner_field) == target_agent
    elif reason == REASON_OWNED_IN_PROGRESS:
        eligible = task_status == "in_progress" and task.get(owner_field) == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses)
    elif reason == REASON_OWNED_READY:
        eligible = task_status == "todo" and task.get(owner_field) == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses)

    if not eligible:
        return None

    return str(build_dispatch_event(task, target_agent, reason, task_map).get("key") or "")

def dispatch_priority_for_task(
    config: dict[str, Any],
    task: dict[str, Any],
    agent_name: str,
    *,
    dependencies_done_statuses: set[str] | None = None,
) -> int | None:
    settings = ready_dispatch_settings(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    dependency_done_statuses = dependencies_done_statuses or normalized_status_set(
        settings.get("dependency_done_statuses"),
        ["done"],
    )
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    task_status = str(task.get("status") or "").lower()
    if task_status in review_statuses and task.get(reviewer_field) == agent_name:
        return 0
    if task_status in finalize_statuses and task.get(owner_field) == agent_name:
        return 1
    if (
        task_status == "in_progress"
        and task.get(owner_field) == agent_name
        and dependencies_satisfied(task, {str(task.get("id") or ""): task}, dependency_done_statuses)
    ):
        return 2
    if (
        task_status == "todo"
        and task.get(owner_field) == agent_name
        and dependencies_satisfied(task, {str(task.get("id") or ""): task}, dependency_done_statuses)
    ):
        return 3
    return None


def agent_dispatch_loads(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
) -> dict[str, list[int]]:
    loads: dict[str, list[int]] = {}

    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
        reason = str(worker.get("request_snapshot", {}).get("reason") or "")
        priority = dispatch_reason_priority(reason)
        if priority is None:
            continue
        agent_name = display_name_for(config, str(worker.get("agent_id") or ""))
        if not agent_name:
            continue
        loads.setdefault(agent_name, []).append(priority)

    queue_records = state.get("queue", {}).get("events", {})
    for event in load_event_queue(config):
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        reason = str(event.get("reason") or "")
        priority = dispatch_reason_priority(reason)
        if priority is None:
            continue
        agent_name = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
        if not agent_name:
            continue
        loads.setdefault(agent_name, []).append(priority)

    return loads


def choose_helper_claim_agent(
    config: dict[str, Any],
    *,
    task: dict[str, Any],
    owner_name: str,
    reviewer_name: str,
    idle_agent_name: str,
    agent_loads: dict[str, list[int]],
    helper_settings: dict[str, Any],
    owner_paused: bool = False,
) -> bool:
    if not helper_settings.get("enabled", True):
        return False
    if not agent_can_take_task(config, idle_agent_name, task):
        return False
    task_status = str(task.get("status") or "").lower()
    allowed_statuses = {str(value).lower() for value in helper_settings.get("task_statuses", ["todo"])}
    paused_owner_statuses = {
        str(value).lower() for value in helper_settings.get("paused_owner_task_statuses", ["in_progress"])
    }
    if task_status not in allowed_statuses and not (owner_paused and task_status in paused_owner_statuses):
        return False
    if not owner_name or owner_name == idle_agent_name:
        return False
    fallbacks = normalized_mapping_values(worker_reassignment_settings(config).get("owner_fallbacks", {}), owner_name)
    if not fallbacks:
        return False
    if owner_paused:
        return idle_agent_name in fallbacks
    if helper_settings.get("claim_idle_work", False):
        return idle_agent_name in fallbacks
    owner_loads = agent_loads.get(owner_name, [])
    if helper_settings.get("require_owner_higher_priority_load", True):
        dispatch_reason_for_status = {
            "in_progress": REASON_OWNED_IN_PROGRESS,
            "todo": REASON_OWNED_READY,
        }.get(task_status, REASON_OWNED_READY)
        current_priority = dispatch_reason_priority(dispatch_reason_for_status)
        if current_priority is None or not any(priority < current_priority for priority in owner_loads):
            return False
    return idle_agent_name in fallbacks


def is_sidecar_review_of_current_parent(
    candidate_task: dict[str, Any],
    current_task: dict[str, Any] | None,
    *,
    agent_name: str,
    review_statuses: set[str],
    owner_field: str,
    reviewer_field: str,
) -> bool:
    if not current_task:
        return False
    candidate_status = str(candidate_task.get("status") or "").lower()
    if candidate_status not in review_statuses:
        return False
    if candidate_task.get(reviewer_field) != agent_name:
        return False
    if current_task.get(owner_field) != agent_name:
        return False
    current_task_id = str(current_task.get("id") or "")
    helper_parent = str(candidate_task.get("helper_parent") or "").strip()
    if not current_task_id or helper_parent != current_task_id:
        return False
    task_class = str(candidate_task.get("task_class") or "").lower()
    return task_class == "sidecar" or bool(candidate_task.get("helper_kind"))


def worker_logical_dispatch_agent_id(config: dict[str, Any], worker: dict[str, Any]) -> str:
    explicit = normalize_agent_id(str(worker.get("logical_agent_id") or ""))
    if explicit:
        return explicit
    agent_id = normalize_agent_id(str(worker.get("agent_id") or worker.get("provider") or ""))
    agent = config.get("agents", {}).get(agent_id, {}) or {}
    return normalize_agent_id(str(agent.get("dispatch_slot_for") or agent_id))


def higher_priority_ready_task_exists(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> bool:
    if worker_is_discussion_planning(worker) or worker_is_coordination_dispatch(worker):
        return False
    current_priority = dispatch_reason_priority(worker.get("request_snapshot", {}).get("reason"))
    if current_priority is None:
        return False

    logical_agent_id = worker_logical_dispatch_agent_id(config, worker)
    agent_name = display_name_for(config, logical_agent_id)
    current_task_id = str(worker.get("task_id") or "")
    settings = ready_dispatch_settings(config)
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    dependency_done_statuses = normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    current_task = task_map.get(current_task_id)
    higher_priority_task_ids: set[str] = set()
    slot_count = len(logical_worker_slot_ids(config, logical_agent_id))
    urgent_priority_cutoff = dispatch_reason_priority(REASON_OWNED_FINALIZE)

    for task_id, task in task_map.items():
        if task_id == current_task_id:
            continue
        if task_is_sidecar(task) and not task_is_sidecar(current_task or {}):
            continue
        task_status = str(task.get("status") or "").lower()
        candidate_priority = None
        if task_status in review_statuses and task.get(reviewer_field) == agent_name:
            if is_sidecar_review_of_current_parent(
                task,
                current_task,
                agent_name=agent_name,
                review_statuses=review_statuses,
                owner_field=owner_field,
                reviewer_field=reviewer_field,
            ):
                continue
            candidate_priority = 0
        elif task_status in finalize_statuses and task.get(owner_field) == agent_name:
            candidate_priority = 1
        elif (
            task_status == "in_progress"
            and task.get(owner_field) == agent_name
            and dependencies_satisfied(task, task_map, dependency_done_statuses)
        ):
            candidate_priority = 2
        elif (
            task_status == "todo"
            and task.get(owner_field) == agent_name
            and dependencies_satisfied(task, task_map, dependency_done_statuses)
        ):
            candidate_priority = 3

        if candidate_priority is not None and candidate_priority < current_priority:
            if (
                slot_count
                and urgent_priority_cutoff is not None
                and candidate_priority > urgent_priority_cutoff
            ):
                continue
            higher_priority_task_ids.add(str(task_id))

    if not higher_priority_task_ids:
        return False

    effective_state = state or {
        "workers": {str(worker.get("run_id") or "__current__"): worker},
        "queue": {"events": {}},
    }
    occupied_count = 0
    served_higher_priority_task_ids: set[str] = set()
    active_event_ids: set[str] = set()
    current_run_id = str(worker.get("run_id") or "")

    for run_id, other in (effective_state.get("workers", {}) or {}).items():
        if other.get("status") not in active_statuses:
            continue
        other_agent_id = worker_logical_dispatch_agent_id(config, other)
        if display_name_for(config, other_agent_id) != agent_name:
            continue
        occupied_count += 1
        event_id = str(other.get("queue_event_id") or "")
        if event_id:
            active_event_ids.add(event_id)
        other_priority = dispatch_reason_priority(other.get("request_snapshot", {}).get("reason"))
        other_task_id = str(other.get("task_id") or "")
        if str(run_id) != current_run_id and other_priority is not None and other_priority < current_priority and other_task_id:
            served_higher_priority_task_ids.add(other_task_id)

    queue_records = (effective_state.get("queue", {}) or {}).get("events", {}) or {}
    try:
        queued_events = load_event_queue(config)
    except KeyError:
        queued_events = []
    for event in queued_events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in active_event_ids:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
        if target_agent != agent_name:
            continue
        occupied_count += 1
        event_priority = dispatch_reason_priority(str(event.get("reason") or ""))
        event_task_id = str(event.get("task_id") or "")
        if event_priority is not None and event_priority < current_priority and event_task_id:
            served_higher_priority_task_ids.add(event_task_id)

    agent_capacity = agent_dispatch_capacity(config, logical_agent_id, settings)
    free_slots = max(0, agent_capacity - occupied_count)
    unserved_higher_priority = higher_priority_task_ids - served_higher_priority_task_ids
    return len(unserved_higher_priority) > free_slots


def worker_matches_current_assignment(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> bool:
    if worker_is_discussion_planning(worker):
        return True
    if worker_is_coordination_dispatch(worker):
        return True
    if worker_is_chair_review(worker):
        return True
    task_id = str(worker.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return False
    agent_name = display_name_for(config, str(worker.get("agent_id") or ""))
    settings = ready_dispatch_settings(config)
    review_statuses = normalized_status_set(settings.get("review_statuses"), ["review"])
    finalize_statuses = normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    owned_statuses = normalized_status_set(settings.get("owned_statuses"), ["in_progress", "todo"])
    dependency_done_statuses = normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    task_status = str(task.get("status") or "").lower()
    if task_status in dependency_done_statuses:
        return False
    if task_status in review_statuses:
        return task.get(reviewer_field) == agent_name
    if task_status in finalize_statuses:
        return task.get(owner_field) == agent_name
    if task_status in owned_statuses:
        return task.get(owner_field) == agent_name
    return False


def stale_dispatch_skip_message(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return None

    expected_key = current_dispatch_event_key(config, event, task_map)
    task_id = str(event.get("task_id") or "unknown task")
    if expected_key is None:
        return f"Skipped stale queued wake event for {task_id}: task is no longer eligible for {reason}."

    queued_key = str(event.get("event_key") or "")
    if queued_key and queued_key != expected_key:
        return f"Skipped stale queued wake event for {task_id}: task state changed after the wake-up was queued."

    return None


def ready_dispatch_signature(task: dict[str, Any], reason: str, task_map: dict[str, dict[str, Any]]) -> str:
    return json.dumps(
        {
            "task_id": task.get("id"),
            "status": task.get("status"),
            "reason": reason,
            "owner": task.get("owner"),
            "reviewer": task.get("reviewer"),
            "last_update": task.get("last_update"),
            "depends_on": list(task.get("depends_on", []) or []),
            "dependency_signature": task_dependency_signature(task, task_map),
        },
        sort_keys=True,
        ensure_ascii=True,
    )


def build_dispatch_event(task: dict[str, Any], target_agent: str, reason: str, task_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    task_payload = {
        "id": task.get("id"),
        "artifacts": list(task.get("artifacts", []) or []),
        "next": task.get("next"),
    }
    for key in (
        "task_class",
        "auto_generated",
        "helper_parent",
        "helper_kind",
        "mutates_canonical",
        "auto_created_by",
    ):
        if key in task:
            task_payload[key] = task.get(key)
    signature = ready_dispatch_signature(task, reason, task_map)
    return {
        "key": f"dispatcher:{target_agent}:{task.get('id')}:{reason}:{signature}",
        "task_id": task.get("id"),
        "target_agent": target_agent,
        "reason": reason,
        "task": task_payload,
    }


def dispatch_discussion_planning(
    config: dict[str, Any],
    state: dict[str, Any],
    planning_state: dict[str, Any] | None = None,
    provider_report: dict[str, Any] | None = None,
) -> bool:
    planning_state = planning_state or load_discussion_planning_state()
    if not discussion_planning_is_active(planning_state):
        return False
    paths = config.get("paths", {}) or {}
    if not paths.get("event_queue") or not paths.get("activity_log"):
        return False

    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, _pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    seen = state.setdefault("seen_event_keys", {})
    changed = False

    for agent_name, readout in (planning_state.get("readouts", {}) or {}).items():
        agent_id = normalize_agent_id(agent_name)
        if not agent_id or agent_id not in config.get("agents", {}):
            continue
        if agent_auto_dispatch_block_reason(config, state, agent_id, provider_report):
            continue
        readout_status = str((readout or {}).get("status") or "").lower()
        if readout_status in {"submitted", "accepted"}:
            continue
        if agent_id in active_agents or agent_id in pending_agents:
            continue
        reason = "discussion_planning_baton_dispatch" if str(planning_state.get("baton_owner") or "") == agent_name else "discussion_planning_readout_dispatch"
        event_key = (
            f"discussion:{planning_state.get('session_id')}:{agent_name}:{reason}:"
            f"round-{planning_state.get('current_round', 0)}:{planning_state.get('consensus_status', 'not_started')}"
        )
        if event_key in pending_event_keys:
            continue
        queued_event_key = queue_discussion_planning_event(config, planning_state, agent_name=agent_name, reason=reason)
        seen[queued_event_key] = utc_now()
        pending_event_keys.add(queued_event_key)
        changed = True

    return changed


def dispatch_ready_tasks(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any] | None = None,
    agent_ids_override: list[str] | None = None,
    max_dispatches_override: int | None = None,
) -> bool:
    settings = ready_dispatch_settings(config)
    if not settings.get("enabled", True):
        return False

    status = load_status(config)
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")

    tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
    task_map = {task.get(task_id_field): task for task in tasks}
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = [str(value).lower() for value in settings.get("owned_statuses", ["in_progress", "todo"])]
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    max_dispatches_per_tick = max(1, int(max_dispatches_override or settings.get("max_dispatches_per_tick", 4)))

    _active_agents, active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    active_task_ids = {task_id for task_id, _agent_id in active_task_agents if task_id}
    pending_task_ids = {task_id for task_id, _agent_id in pending_task_agents if task_id}
    agent_loads = agent_dispatch_loads(config, state, active_statuses)
    helper_settings = helper_claim_settings(config)
    active_quota_counts = active_quota_group_counts(config, state, active_statuses)
    pending_quota_counts = queued_quota_group_counts(config, state)
    seen = state.setdefault("seen_event_keys", {})
    failure_loop_task_agents = failure_loop_task_agents_for_task_map(config, state, task_map)
    failure_loop_task_ids = {task_id for task_id, _agent_name in failure_loop_task_agents}
    disable_helper_claims_for_failure_loops = bool(helper_settings.get("disable_when_failure_loops", True))

    changed = False
    normalized = False
    for task in tasks:
        task_id = str(task.get(task_id_field) or "")
        if not task_id or task_id in active_task_ids or task_id in pending_task_ids:
            continue
        normalized = normalize_mainline_task_assignment(config, task) or normalized

    if normalized:
        changed = True
        status = load_status(config)
        tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
        task_map = {task.get(task_id_field): task for task in tasks}
        failure_loop_task_agents = failure_loop_task_agents_for_task_map(config, state, task_map)
        failure_loop_task_ids = {task_id for task_id, _agent_name in failure_loop_task_agents}

    dispatches = 0
    weighted_dispatch_enabled = bool(dispatch_weight_mapping(settings)) and not agent_ids_override
    agent_sequence = (
        [normalize_agent_id(agent_id) for agent_id in agent_ids_override if normalize_agent_id(agent_id)]
        if agent_ids_override
        else weighted_dispatch_agent_ids(config, settings)
    )
    dispatch_state = state.setdefault("ready_dispatcher", {})
    try:
        dispatch_cursor = int(dispatch_state.get("weighted_cursor", 0))
    except (TypeError, ValueError):
        dispatch_cursor = 0
    if agent_sequence:
        dispatch_cursor %= len(agent_sequence)
        agent_ids = agent_sequence[dispatch_cursor:] + agent_sequence[:dispatch_cursor]
    else:
        agent_ids = []
    max_concurrent = ready_dispatch_max_concurrent_workers(config)
    if max_concurrent is not None and max_concurrent > 0:
        live_total = sum(len(pids) for pids in scan_live_worker_pids_by_agent().values())
        if live_total >= max_concurrent:
            return changed
    considered_agents = 0
    for agent_id in agent_ids:
        if dispatches >= max_dispatches_per_tick:
            break
        considered_agents += 1
        target_agent = display_name_for(config, agent_id)
        if agent_auto_dispatch_block_reason(config, state, agent_id, provider_report):
            continue
        quota_limit = quota_group_concurrency_limit(config, agent_id, settings)
        quota_group = agent_quota_group_id(config, agent_id)
        quota_used = active_quota_counts.get(quota_group, 0) + pending_quota_counts.get(quota_group, 0)
        if quota_limit and quota_group and quota_used >= quota_limit:
            continue
        agent_capacity = agent_dispatch_capacity(config, agent_id, settings)
        current_agent_load = len(agent_loads.get(target_agent, []))
        if current_agent_load >= agent_capacity:
            continue
        available_agent_slots = agent_capacity - current_agent_load
        if quota_limit and quota_group:
            available_agent_slots = min(available_agent_slots, max(0, quota_limit - quota_used))
            if available_agent_slots <= 0:
                continue
        target_has_primary_work = agent_has_dispatchable_primary_work(config, status, target_agent, task_map)

        candidates: list[tuple[int, int, dict[str, Any], str]] = []
        helper_candidates: list[tuple[int, int, dict[str, Any], str, str, str, bool]] = []
        for index, task in enumerate(tasks):
            task_id = str(task.get(task_id_field) or "")
            if not task_id:
                continue
            if task_id in active_task_ids or task_id in pending_task_ids:
                continue
            is_sidecar_task = task_is_sidecar(task)
            task_status = str(task.get("status") or "").lower()
            task_owner = task.get(owner_field)
            task_reviewer = task.get(reviewer_field)
            owner_paused = bool(
                agent_auto_dispatch_block_reason(
                    config,
                    state,
                    normalize_agent_id(str(task_owner or "")),
                    provider_report,
                )
            )

            if (task_id, agent_id) in active_task_agents or (task_id, agent_id) in pending_task_agents:
                continue

            reason = None
            priority = None
            if task_status in review_statuses and task_reviewer == target_agent:
                reason = "review_ready_dispatch"
                priority = 0
            elif task_status in finalize_statuses and task_owner == target_agent:
                reason = "owned_finalize_dispatch"
                priority = 1
            elif task_status == "in_progress" and task_owner == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses):
                reason = "owned_in_progress_dispatch"
                priority = 2
            elif task_status == "todo" and task_owner == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses):
                reason = "owned_ready_dispatch"
                priority = 3

            if reason is not None and not agent_can_take_task(config, target_agent, task):
                continue
            if reason is not None and (task_id, target_agent) in failure_loop_task_agents:
                continue
            if reason is not None and chair_reassignment_triage_needed_for_task(config, state, task_id, target_agent):
                continue

            sidecar_claim_allowed = (
                not task_is_sidecar(task)
                or owner_paused
                or bool(helper_settings.get("claim_sidecars_when_idle", False))
            )
            helper_claim_candidate = (
                (not disable_helper_claims_for_failure_loops or task_id not in failure_loop_task_ids)
                and dependencies_satisfied(task, task_map, dependency_done_statuses)
                and task_id not in active_task_ids
                and task_id not in pending_task_ids
                and sidecar_claim_allowed
                and agent_within_target_workload_for_assignment(
                    status,
                    target_agent,
                    owner_field=owner_field,
                    previous_owner=str(task_owner or ""),
                )
                and choose_helper_claim_agent(
                    config,
                    task=task,
                    owner_name=str(task_owner or ""),
                    reviewer_name=str(task_reviewer or ""),
                    idle_agent_name=target_agent,
                    agent_loads=agent_loads,
                    helper_settings=helper_settings,
                    owner_paused=owner_paused,
                )
            )

            if helper_claim_candidate:
                helper_dispatch_reason = (
                    "owned_in_progress_dispatch"
                    if task_status == "in_progress"
                    else "owned_ready_dispatch"
                )
                helper_priority = 4 if task_status == "in_progress" else 5
                if owner_paused:
                    helper_priority -= 2
                if task_is_sidecar(task):
                    helper_priority += 2
                helper_candidates.append(
                    (
                        helper_priority,
                        index,
                        task,
                        helper_dispatch_reason,
                        str(task_owner or ""),
                        str(task_reviewer or ""),
                        owner_paused,
                    )
                )

            if reason is None or priority is None:
                continue

            if is_sidecar_task and target_has_primary_work:
                priority += SIDECAR_READY_PRIORITY_OFFSET

            event = build_dispatch_event(task, target_agent, reason, task_map)
            if event["key"] in pending_event_keys:
                continue
            candidates.append((priority, index, task, reason))

        candidates.sort(key=lambda item: (item[0], item[1]))
        per_occurrence_limit = 1 if weighted_dispatch_enabled else available_agent_slots
        queued_for_agent = 0
        for _, _, task, reason in candidates[:per_occurrence_limit]:
            event = build_dispatch_event(task, target_agent, reason, task_map)
            if queue_delivery_event(config, event):
                seen[event["key"]] = utc_now()
                pending_event_keys.add(event["key"])
                pending_agents.add(agent_id)
                pending_task_ids.add(str(task.get(task_id_field) or ""))
                pending_task_agents.add((str(task.get(task_id_field) or ""), agent_id))
                agent_loads.setdefault(target_agent, []).append(dispatch_reason_priority(reason) or 9)
                if quota_group:
                    pending_quota_counts[quota_group] = pending_quota_counts.get(quota_group, 0) + 1
                changed = True
                dispatches += 1
                queued_for_agent += 1
                if dispatches >= max_dispatches_per_tick:
                    break

        if dispatches >= max_dispatches_per_tick:
            break

        remaining_occurrence_slots = max(0, per_occurrence_limit - queued_for_agent)
        helper_candidates.sort(key=lambda item: (item[0], item[1]))
        for (
            _,
            _,
            task,
            helper_dispatch_reason,
            task_owner,
            task_reviewer,
            owner_paused,
        ) in helper_candidates[:remaining_occurrence_slots]:
            task_id = str(task.get(task_id_field) or "")
            if not task_id or task_id in active_task_ids or task_id in pending_task_ids:
                continue
            helper_message = (
                f"Helper-claimed by {target_agent} while {task_owner} is dispatch-paused."
                if owner_paused
                else (
                    f"Helper-claimed by idle {target_agent}; previous owner {task_owner} becomes reviewer."
                    if helper_settings.get("claim_idle_work", False)
                    else f"Helper-claimed by {target_agent} while {task_owner} completes higher-priority work."
                )
            )
            new_reviewer = str(task_owner or task_reviewer or "")
            if not persist_task_reassignment(
                config,
                task_id=task_id,
                new_owner=target_agent,
                new_reviewer=new_reviewer,
                message=helper_message,
                handoff_to=target_agent,
                handoff_from=str(task_owner or ""),
            ):
                continue

            task[owner_field] = target_agent
            task[reviewer_field] = new_reviewer
            task["next"] = helper_message

            # Re-read the persisted task before signing the dispatch event. The
            # status writer owns last_update; using a separate utc_now() here
            # makes the queued event immediately look stale.
            persisted_status = load_status(config)
            persisted_task_map = task_index_from_status(config, persisted_status)
            persisted_task = persisted_task_map.get(task_id)
            if (
                persisted_task
                and persisted_task.get(owner_field) == target_agent
                and persisted_task.get(reviewer_field) == new_reviewer
            ):
                task.update(persisted_task)
                task_map = dict(task_map)
                task_map[task_id] = task
            else:
                task["last_update"] = utc_now()

            event = build_dispatch_event(task, target_agent, helper_dispatch_reason, task_map)
            if event["key"] not in pending_event_keys and queue_delivery_event(config, event):
                seen[event["key"]] = utc_now()
                pending_event_keys.add(event["key"])
                pending_agents.add(agent_id)
                agent_loads.setdefault(target_agent, []).append(
                    dispatch_reason_priority(helper_dispatch_reason) or 9
                )
                active_task_ids.add(task_id)
                pending_task_ids.add(task_id)
                if quota_group:
                    pending_quota_counts[quota_group] = pending_quota_counts.get(quota_group, 0) + 1
                changed = True
                dispatches += 1
                write_activity_log(
                    config,
                    {
                        "type": "task_helper_claimed",
                        "task_id": task_id,
                        "message": helper_message,
                        "from_owner": task_owner,
                        "to_owner": target_agent,
                        "new_reviewer": new_reviewer,
                    },
                )
                console_log(
                    f"helper claim: task={task_id} from={task_owner} to={target_agent}",
                    quiet=SUPERVISOR_LOG_QUIET,
                )
                if dispatches >= max_dispatches_per_tick:
                    break

    if agent_sequence and considered_agents and not agent_ids_override:
        dispatch_state["weighted_cursor"] = (dispatch_cursor + considered_agents) % len(agent_sequence)
    return changed


def ready_dispatch_max_concurrent_workers(config: dict[str, Any]) -> int | None:
    max_concurrent_setting = ready_dispatch_settings(config).get("max_concurrent_workers")
    try:
        max_concurrent = int(max_concurrent_setting) if max_concurrent_setting not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if max_concurrent is not None and max_concurrent <= 0:
        return None
    return max_concurrent


def dispatch_chair_review(
    config: dict[str, Any],
    state: dict[str, Any],
    planning_state: dict[str, Any] | None = None,
    provider_report: dict[str, Any] | None = None,
) -> bool:
    settings = chair_review_settings(config)
    if not settings.get("enabled", True):
        return False
    if discussion_planning_is_active(planning_state):
        return False
    if chair_review_active(state):
        return False
    now = utc_now()
    pending_approval_count = len(safe_load_approval_state(config).get("pending", []) or [])
    approval_triage_requested = bool(
        pending_approval_count
        and settings.get("approval_actions_enabled", True)
    )
    failure_loop_details = chair_review_failure_loop_details(config, state)
    failure_loop_count = len(failure_loop_details)
    failure_loop_agents = {
        str(item.get("agent") or "").strip()
        for item in failure_loop_details
        if str(item.get("agent") or "").strip()
    }
    bypass_cooldown = bool(
        (
            approval_triage_requested
            and settings.get("approval_actions_enabled", True)
            and settings.get("bypass_cooldown_for_pending_approvals", True)
        )
        or (
            failure_loop_count
            and settings.get("reassignment_actions_enabled", True)
            and settings.get("bypass_cooldown_for_failure_loops", True)
        )
    )
    if chair_review_cooldown_active(config, state, now=now) and not bypass_cooldown:
        return False

    candidates = chair_review_candidates(config)
    if not candidates:
        return False

    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, _pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    max_concurrent = ready_dispatch_max_concurrent_workers(config)
    if max_concurrent is not None:
        live_total = sum(len(pids) for pids in scan_live_worker_pids_by_agent().values())
        reserved_total = len(set(active_agents) | set(pending_agents))
        if max(live_total, reserved_total) >= max_concurrent:
            return False
    seen = state.setdefault("seen_event_keys", {})
    status = load_status(config)
    task_map = task_index_from_status(config, status)
    rotation = chair_rotation_state(state)
    start_index = int(rotation.get("current_index") or 0) % len(candidates)

    for offset in range(len(candidates)):
        agent_name = candidates[(start_index + offset) % len(candidates)]
        agent_id = normalize_agent_id(agent_name)
        if not agent_id or agent_id not in config.get("agents", {}):
            continue
        if agent_auto_dispatch_block_reason(config, state, agent_id, provider_report):
            continue
        if agent_name in failure_loop_agents:
            continue
        if agent_id in active_agents or agent_id in pending_agents:
            continue
        if (
            not (
                approval_triage_requested
                and settings.get("bypass_primary_work_for_pending_approvals", True)
            )
            and agent_has_dispatchable_primary_work(config, status, agent_name, task_map)
        ):
            continue
        if failure_loop_count:
            reason = "chair_review:reassignment_triage"
        elif pending_approval_count:
            reason = "chair_review:approval_triage"
        else:
            reason = "chair_review:operational_review"
        event_key = f"chair:{agent_id}:{reason}:{now}"
        if event_key in pending_event_keys:
            continue
        queued_event_key = queue_chair_review_event(config, state, agent_name=agent_name, reason=reason, issued_at=now)
        seen[queued_event_key] = now
        pending_event_keys.add(queued_event_key)
        rotation["current_index"] = (start_index + offset + 1) % len(candidates)
        return True
    return False


def dispatch_underutilization_sidecars(
    config: dict[str, Any],
    state: dict[str, Any],
    provider_report: dict[str, Any] | None = None,
) -> bool:
    settings = underutilization_settings(config)
    tracking = state.setdefault("underutilization", {})
    rotation = chair_rotation_state(state)
    productive_statuses = {str(value) for value in settings.get("productive_worker_statuses", [])}
    ratio = utilization_ratio_for_sidecars(config, state, productive_statuses)
    threshold = float(settings.get("threshold_ratio", 0.5))
    now = utc_now()
    tracking["last_ratio"] = round(ratio, 4)
    changed = False

    if not settings.get("enabled", True):
        tracking["below_threshold_since"] = None
        return changed

    if settings.get("require_recent_chair_signal", True):
        approval_until = _parse_iso_utc(str(rotation.get("sidecar_approved_until") or ""))
        current_dt = _parse_iso_utc(now)
        if approval_until is None or current_dt is None or current_dt > approval_until:
            tracking["last_sidecar_wave_reason"] = "awaiting chair review approval before creating sidecars"
            return changed

    if ratio >= threshold:
        if tracking.get("below_threshold_since") is not None:
            tracking["below_threshold_since"] = None
            changed = True
        return changed

    if not tracking.get("below_threshold_since"):
        tracking["below_threshold_since"] = now
        return True

    below_since = _parse_iso_utc(tracking.get("below_threshold_since"))
    current_dt = _parse_iso_utc(now)
    if below_since is None or current_dt is None:
        tracking["below_threshold_since"] = now
        return True

    if (current_dt - below_since).total_seconds() < float(settings.get("continuous_window_seconds", 900)):
        return changed

    last_wave_at = _parse_iso_utc(tracking.get("last_sidecar_wave_at"))
    if last_wave_at is not None and (current_dt - last_wave_at).total_seconds() < float(settings.get("cooldown_seconds", 900)):
        return changed

    status = load_status(config)
    task_map = task_index_from_status(config, status)
    idle_agents = eligible_idle_agents_for_sidecars(
        config,
        state,
        status,
        max_active_sidecars_per_agent=int(settings.get("max_active_sidecars_per_agent", 1)),
        provider_report=provider_report,
    )
    if not idle_agents:
        tracking["last_sidecar_wave_at"] = now
        tracking["last_sidecar_wave_reason"] = "underutilized but no idle agents were eligible for sidecar work"
        write_activity_log(
            config,
            {
                "type": "sidecar_wave_skipped",
                "message": tracking["last_sidecar_wave_reason"],
                "ratio": ratio,
            },
        )
        return True

    existing_signatures = existing_sidecar_signatures(status)
    candidates = build_catalog_sidecar_candidates(config, status, task_map, existing_signatures)
    if not candidates:
        candidates = build_dynamic_sidecar_candidates(config, status, task_map, existing_signatures)
    blocked_sidecar_parents = {str(item) for item in rotation.get("sidecar_blocked_parents", []) or [] if str(item).strip()}
    if blocked_sidecar_parents:
        candidates = [candidate for candidate in candidates if str(candidate.get("parent_task_id") or "") not in blocked_sidecar_parents]
    if not candidates:
        tracking["last_sidecar_wave_at"] = now
        tracking["last_sidecar_wave_reason"] = "underutilized but no sidecar candidates matched the catalog or dynamic fallback"
        write_activity_log(
            config,
            {
                "type": "sidecar_wave_skipped",
                "message": tracking["last_sidecar_wave_reason"],
                "ratio": ratio,
            },
        )
        return True

    recommended_focus = {
        str(item)
        for item in rotation.get("last_review_recommended_focus", []) or []
        if str(item).strip()
    }
    candidates.sort(
        key=lambda item: (
            0
            if not recommended_focus
            or str(item.get("parent_task_id") or "") in recommended_focus
            or str(item.get("sidecar_id") or "") in recommended_focus
            else 1,
            int(item.get("priority", 9)),
            str(item.get("parent_task_id") or ""),
            str(item.get("kind") or ""),
        )
    )
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    _active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
    _pending_agents, _pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    seen = state.setdefault("seen_event_keys", {})
    per_agent_counts = {agent: count_open_sidecars_for_agent(status, agent) for agent in idle_agents}
    max_new_sidecars = sidecar_wave_limit(settings.get("max_new_sidecars_per_wave"))
    if settings.get("respect_chair_max_sidecars", False):
        chair_limit = sidecar_wave_limit(rotation.get("sidecar_approval_max_sidecars"))
        if chair_limit is not None:
            max_new_sidecars = chair_limit if max_new_sidecars is None else min(max_new_sidecars, chair_limit)
    created = 0

    for candidate in candidates:
        if max_new_sidecars is not None and created >= max_new_sidecars:
            break

        parent_owner = str(candidate.get("reviewer") or "").strip()
        preferred_agents = preferred_agents_for_sidecar(str(candidate.get("kind") or ""))
        selected_owner = next(
            (
                agent
                for agent in preferred_agents
                if agent in idle_agents
                and agent != parent_owner
                and per_agent_counts.get(agent, 0) < int(settings.get("max_active_sidecars_per_agent", 1))
            ),
            None,
        )
        if not selected_owner:
            selected_owner = next(
                (
                    agent
                    for agent in idle_agents
                    if agent != parent_owner
                    and per_agent_counts.get(agent, 0) < int(settings.get("max_active_sidecars_per_agent", 1))
                ),
                None,
            )
        if not selected_owner:
            continue

        ok, error = create_sidecar_task(
            config,
            sidecar_id=str(candidate["sidecar_id"]),
            owner=selected_owner,
            reviewer=parent_owner,
            phase=str(candidate["phase"]),
            title=str(candidate["title"]),
            summary_zh=str(candidate["summary_zh"]),
            depends_on=list(candidate.get("depends_on", []) or []),
            artifacts=list(candidate.get("artifacts", []) or []),
            helper_parent=str(candidate["parent_task_id"]),
            helper_kind=str(candidate["kind"]),
            mutates_canonical=bool(candidate.get("mutates_canonical", False)),
        )
        if not ok:
            write_activity_log(
                config,
                {
                    "type": "sidecar_task_create_failed",
                    "task_id": candidate["sidecar_id"],
                    "message": f"Failed to create sidecar for {candidate['parent_task_id']}: {error}",
                },
            )
            continue

        status = load_status(config)
        task_map = task_index_from_status(config, status)
        sidecar_task = next((task for task in status.get("tasks", []) if task.get("id") == candidate["sidecar_id"]), None)
        if not sidecar_task:
            continue

        state.setdefault("tasks", {})[candidate["sidecar_id"]] = snapshot_task(sidecar_task, config.get("schema", {}))

        event = build_dispatch_event(sidecar_task, selected_owner, "owned_ready_dispatch", task_map)
        if event["key"] in pending_event_keys:
            continue
        if queue_delivery_event(config, event):
            seen[event["key"]] = utc_now()
            pending_event_keys.add(event["key"])

        per_agent_counts[selected_owner] = per_agent_counts.get(selected_owner, 0) + 1
        existing_signatures.add(f"{candidate['parent_task_id']}:{candidate['kind']}")
        created += 1
        changed = True
        write_activity_log(
            config,
            {
                "type": "sidecar_task_created",
                "task_id": candidate["sidecar_id"],
                "message": (
                    f"Auto-created sidecar {candidate['sidecar_id']} for {candidate['parent_task_id']} "
                    f"({candidate['kind']}) while utilization remained below threshold."
                ),
                "parent_task_id": candidate["parent_task_id"],
                "target_agent": selected_owner,
            },
        )

    tracking["last_sidecar_wave_at"] = now
    if created:
        tracking["last_sidecar_wave_reason"] = (
            f"utilization {ratio:.2f} stayed below threshold {threshold:.2f}; created {created} visible sidecar task(s)"
        )
        write_activity_log(
            config,
            {
                "type": "sidecar_wave_started",
                "message": tracking["last_sidecar_wave_reason"],
                "ratio": ratio,
                "created": created,
            },
        )
        return True

    tracking["last_sidecar_wave_reason"] = "underutilized but no sidecar candidate could be assigned safely"
    write_activity_log(
        config,
        {
            "type": "sidecar_wave_skipped",
            "message": tracking["last_sidecar_wave_reason"],
            "ratio": ratio,
        },
    )
    return True


def run_once(
    config: dict[str, Any],
    *,
    watch: bool,
    replay: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    once: bool = False,
) -> bool:
    write_supervisor_pid(config)
    loop_started_at = utc_now()
    state = load_runtime_state(config)
    previous_heartbeat = state.get("supervisor", {}).get("last_heartbeat_at")
    planning_state = load_discussion_planning_state()
    stamp_supervisor_runtime_state(
        config,
        state,
        planning_state=planning_state,
        heartbeat_at=loop_started_at,
        lifecycle="running",
        loop_started_at=loop_started_at,
    )
    save_runtime_state(config, state)
    changed = False
    try:
        changed = reconcile_runtime_on_boot(config, state) or changed
        if changed:
            save_runtime_state(config, state)
        continue_or_skip_empty(THIS_DIR.parent)
        changed = expire_provider_dispatch_pauses(config, state) or changed
        pruned = prune_stale_approvals(config)
        if pruned:
            changed = True
        provider_report = load_provider_report(config)
        if watch:
            changed = run_scan(config, state, replay=replay, provider_capabilities=provider_report) or changed
            state = load_runtime_state(config)
            stamp_supervisor_runtime_state(
                config,
                state,
                planning_state=planning_state,
                heartbeat_at=loop_started_at,
                lifecycle="running",
                loop_started_at=loop_started_at,
            )
        changed = sync_coordination_files(config, state) or changed
        changed = poll_workers(config, state, provider_report=provider_report) or changed
        changed = reconcile_queue_records(config, state) or changed
        changed = prune_event_queue(config, state) or changed
        changed = refresh_chair_review_state(config, state) or changed
        planning_state = load_discussion_planning_state()
        changed = auto_materialize_discussion_planning(config, planning_state) or changed
        planning_state = load_discussion_planning_state()
        dispatch_suppressed_by_watchdog = watchdog_safe_mode_active(state)
        if dispatch_suppressed_by_watchdog:
            changed = record_watchdog_safe_mode_observed(config, state, loop_started_at) or changed
        elif discussion_planning_is_active(planning_state):
            changed = dispatch_discussion_planning(config, state, planning_state, provider_report=provider_report) or changed
        else:
            if chair_review_failure_loop_details(config, state):
                chair_dispatched = dispatch_chair_review(config, state, planning_state, provider_report=provider_report)
                changed = chair_dispatched or changed
                changed = dispatch_ready_tasks(config, state, provider_report=provider_report) or changed
            else:
                changed = dispatch_ready_tasks(config, state, provider_report=provider_report) or changed
                changed = dispatch_chair_review(config, state, planning_state, provider_report=provider_report) or changed
            changed = dispatch_underutilization_sidecars(config, state, provider_report=provider_report) or changed
        if not dispatch_suppressed_by_watchdog:
            changed = process_queue(config, state, provider_report) or changed
        changed = poll_workers(config, state, provider_report=provider_report) or changed
        changed = reconcile_queue_records(config, state) or changed
        changed = prune_event_queue(config, state) or changed
        changed = sync_github_bus(config, state) or changed
        trim_worker_history(state, int(config.get("supervisor", {}).get("max_worker_history", 200)))
        trim_seen_events(state, int(config.get("watcher", {}).get("max_seen_events", 2000)))
        changed = prune_orphan_worktrees(config, state) or changed
        changed = maybe_auto_commit_archive(config, state) or changed

        loop_finished_at = utc_now()
        stamp_supervisor_runtime_state(
            config,
            state,
            planning_state=planning_state,
            heartbeat_at=loop_finished_at,
            lifecycle="running",
            loop_finished_at=loop_finished_at,
            loop_error=None,
        )
        save_runtime_state(config, state)
        refresh_dashboard_runtime_artifacts(config)
        log_runtime_summary(
            state,
            safe_load_approval_state(config),
            changed=changed,
            quiet=quiet,
            verbose=verbose,
            previous_heartbeat=previous_heartbeat,
            warn_after_seconds=float(config.get("supervisor", {}).get("heartbeat_warn_after_seconds", 10.0)),
            once=once,
        )
        return changed
    except Exception as exc:
        loop_finished_at = utc_now()
        stamp_supervisor_runtime_state(
            config,
            state,
            planning_state=planning_state,
            heartbeat_at=loop_finished_at,
            lifecycle="degraded",
            loop_finished_at=loop_finished_at,
            loop_error=f"{type(exc).__name__}: {exc}",
        )
        save_runtime_state(config, state)
        refresh_dashboard_runtime_artifacts(config)
        raise


def run_supervisor_cycle(
    config: dict[str, Any],
    *,
    watch: bool,
    replay: bool = False,
    quiet: bool = False,
    verbose: bool = False,
) -> bool:
    try:
        return run_once(config, watch=watch, replay=replay, quiet=quiet, verbose=verbose, once=False)
    except Exception as exc:
        console_log(
            f"supervisor cycle failed: {type(exc).__name__}: {exc}; continuing after next poll",
            quiet=quiet,
        )
        return False


def claim_next_task_for_agent(
    config: dict[str, Any],
    *,
    agent_name: str,
    release_task_id: str | None = None,
    quiet: bool = False,
) -> bool:
    settings = worker_self_claim_settings(config)
    if not settings.get("enabled", False):
        console_log("worker self-claim disabled", quiet=quiet)
        return False
    agent_id = normalize_agent_id(agent_name)
    if not agent_id or agent_id not in config.get("agents", {}):
        console_log(f"worker self-claim skipped: unknown agent {agent_name}", quiet=quiet)
        return False

    state = load_runtime_state(config)
    planning_state = load_discussion_planning_state()
    changed = release_completed_worker_for_claim(
        config,
        state,
        agent_name=display_name_for(config, agent_id),
        task_id=release_task_id,
    )
    provider_report = load_provider_report(config)
    changed = expire_provider_dispatch_pauses(config, state) or changed
    changed = reconcile_queue_records(config, state) or changed
    changed = prune_event_queue(config, state) or changed
    if not discussion_planning_is_active(planning_state):
        changed = dispatch_ready_tasks(
            config,
            state,
            provider_report=provider_report,
            agent_ids_override=[agent_id],
            max_dispatches_override=1,
        ) or changed
        changed = process_queue(config, state, provider_report) or changed
    supervisor_state = state.setdefault("supervisor", {})
    occupancy = compute_mode_occupancy(config, state)
    supervisor_state["mode_occupancy"] = occupancy
    focus_mode = str(supervisor_state.get("focus_mode") or "execution")
    supervisor_state["mode_status"] = "active" if mode_has_activity(occupancy.get(focus_mode)) else "idle"
    save_runtime_state(config, state)
    refresh_dashboard_runtime_artifacts(config)
    return changed


def main() -> int:
    global SUPERVISOR_LOG_QUIET
    args = parse_args()
    SUPERVISOR_LOG_QUIET = args.quiet
    config = load_config(args.config)
    if args.clear_provider_pause:
        state = load_runtime_state(config)
        changed = clear_provider_dispatch_pause(config, state, args.clear_provider_pause)
        if changed:
            save_runtime_state(config, state)
            console_log(f"cleared provider dispatch pause: {args.clear_provider_pause}", quiet=args.quiet)
        else:
            console_log(f"no provider dispatch pause found for: {args.clear_provider_pause}", quiet=args.quiet)
        return 0
    if args.claim_agent:
        claim_next_task_for_agent(
            config,
            agent_name=args.claim_agent,
            release_task_id=args.release_task,
            quiet=args.quiet,
        )
        return 0
    if not acquire_singleton_lock(config):
        console_log(
            "another supervisor already holds the singleton lock; exiting without "
            "touching shared state",
            quiet=args.quiet,
        )
        return 0
    terminate_other_supervisors(config)
    atexit.register(clear_supervisor_pid, config)
    write_supervisor_pid(config)
    bootstrap_supervisor_runtime_state(config, lifecycle="starting")
    poll_interval, poll_source = resolve_poll_interval(
        config,
        cli_value=args.poll_interval,
        allow_fast_poll=args.allow_fast_poll,
    )
    console_log(
        f"starting supervisor pid={os.getpid()} poll_interval={poll_interval:.1f}s "
        f"source={poll_source} config={args.config}",
        quiet=args.quiet,
    )
    if args.once:
        run_once(
            config,
            watch=not args.no_watch,
            replay=args.replay,
            quiet=args.quiet,
            verbose=args.verbose,
            once=True,
        )
        return 0
    run_supervisor_cycle(
        config,
        watch=not args.no_watch,
        replay=args.replay,
        quiet=args.quiet,
        verbose=args.verbose,
    )
    while True:
        time.sleep(poll_interval)
        run_supervisor_cycle(
            config,
            watch=not args.no_watch,
            replay=False,
            quiet=args.quiet,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    raise SystemExit(main())
