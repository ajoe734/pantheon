#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import fcntl
import fnmatch
import hashlib
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
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import model_rotation
from approval_queue import prune_stale_approvals
from adapters import ADAPTERS, build_adapter
from adapters.base import DeliveryRequest
from common import (
    agent_config_for,
    bound_commit_subject,
    canonical_task_state_lock_file,
    config_path,
    display_name_for,
    load_config,
    load_json,
    load_status,
    new_runtime_id,
    first_symlink_component,
    normalize_agent_id,
    normalize_github_repo_slug,
    is_github_cli_auth_failure,
    resolved_coordinator_status_root,
    config_status_root,
    status_command_runtime_record_from_env,
    status_command_runtime_env,
    task_state_store_runtime_env,
    summarize_failure_reason,
    utc_now,
    WORKER_PROCESS_GENERATION_SCHEMA_VERSION,
    worker_process_generation_id,
    write_failure_evidence,
    write_json,
    write_status,
    write_activity_log as _write_activity_log_immediate,
    worker_runtime_paths,
)
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
from github_bus import GitHubBusError, resolve_gh_binary, run_gh_process, sync_github_bus
from multi_repo_registry import (
    artifact_repository_id,
    repository_configured_local_path,
    repository_local_path,
    repository_relative_artifact_path,
    repository_slug,
    resolve_repository,
    task_primary_repository_id,
)
from provider_permissions import probe_provider_auth
from rebase_helper import continue_or_skip_empty
from runtime_state import (
    load_approval_state,
    load_runtime_state,
    load_runtime_state_snapshot,
    queue_event_by_id,
    queue_event_record,
    queue_events,
    runtime_state_lock,
    runtime_state_update,
    save_runtime_state,
)
from task_archive import TaskResolver
from watch_events import (
    _queue_delivery_event_locked,
    trim_seen_events,
)

# Supervisor Authority V2 modules.
from rewrite import concurrency as rewrite_concurrency
from rewrite import dispatch_admission as rewrite_dispatch_admission
from rewrite import provider_health as rewrite_provider_health
from rewrite import task_machine as rewrite_task_machine
from rewrite import task_state_store as rewrite_task_state_store
from rewrite import worker_lifecycle as rewrite_worker_lifecycle


SIDECAR_READY_PRIORITY_OFFSET = 10


_DEFERRED_DISPATCH_STATUS_SYNCS: ContextVar[
    list[tuple[dict[str, Any], str | None, str | None]] | None
] = ContextVar("deferred_dispatch_status_syncs", default=None)
_DEFERRED_WORKER_TERMINATIONS: ContextVar[
    list[tuple[int, int]] | None
] = ContextVar(
    "deferred_worker_terminations",
    default=None,
)
_DEFERRED_AUTO_COMMIT_ARCHIVES: ContextVar[
    list[dict[str, Any]] | None
] = ContextVar(
    "deferred_auto_commit_archives",
    default=None,
)
_DEFERRED_ACTIVITY_EVENTS: ContextVar[
    list[tuple[dict[str, Any], dict[str, Any]]] | None
] = ContextVar(
    "deferred_supervisor_activity_events",
    default=None,
)
_CYCLE_METRICS: ContextVar[dict[str, Any] | None] = ContextVar(
    "supervisor_cycle_metrics",
    default=None,
)
_SCHEDULED_CYCLE_SAMPLE: ContextVar[dict[str, Any] | None] = ContextVar(
    "supervisor_scheduled_cycle_sample",
    default=None,
)
_RUNTIME_PHASE_RESERVATION: ContextVar[str | None] = ContextVar(
    "supervisor_runtime_phase_reservation",
    default=None,
)
_RUNTIME_PHASE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "supervisor_runtime_phase_context",
    default=None,
)


CYCLE_PHASE_METRICS_MAX = 64
CYCLE_BATCH_COUNT_MAX = 16
RUNTIME_PHASE_LAUNCH_INTENT_STALE_DEFAULT_SECONDS = 30.0
RUNTIME_PHASE_LAUNCH_INTENT_STALE_MAX_SECONDS = 300.0


def write_activity_log(config: dict[str, Any], entry: dict[str, Any]) -> None:
    """Defer audit filesystem I/O while runtime admission is exclusive.

    The entry is copied at the call boundary so later runtime-state mutation
    cannot change the audit payload.  Outside a supervisor transaction the
    canonical writer is used immediately, preserving direct-call behavior.
    """

    deferred = _DEFERRED_ACTIVITY_EVENTS.get()
    if deferred is not None:
        deferred.append((config, deepcopy(entry)))
        return
    _write_activity_log_immediate(config, entry)


def _record_cycle_phase_elapsed(name: str, elapsed_seconds: float) -> None:
    """Accumulate one bounded phase timing for the active cycle.

    Phase names are source-owned constants passed to ``_safe_phase``.  Keeping
    one aggregate row per name avoids placing task ids, provider output, or an
    ever-growing timing history in runtime state.
    """

    metrics = _CYCLE_METRICS.get()
    if not isinstance(metrics, dict):
        return
    phases = metrics.setdefault("phases", {})
    if name not in phases and len(phases) >= CYCLE_PHASE_METRICS_MAX - 1:
        name = "other"
    row = phases.setdefault(
        name,
        {"count": 0, "elapsed_seconds": 0.0, "max_seconds": 0.0},
    )
    elapsed = max(0.0, float(elapsed_seconds))
    row["count"] = int(row.get("count", 0)) + 1
    row["elapsed_seconds"] = float(row.get("elapsed_seconds", 0.0)) + elapsed
    row["max_seconds"] = max(float(row.get("max_seconds", 0.0)), elapsed)


def _record_cycle_batch_count(name: str, count: int) -> None:
    metrics = _CYCLE_METRICS.get()
    if not isinstance(metrics, dict):
        return
    batches = metrics.setdefault("batch_counts", {})
    if name not in batches and len(batches) >= CYCLE_BATCH_COUNT_MAX - 1:
        name = "other"
    batches[name] = int(batches.get(name, 0)) + max(0, int(count))


def _record_cycle_runtime_lock_hold(elapsed_seconds: float) -> None:
    """Retain the longest *transaction* hold, never slow reserved phase work."""

    metrics = _CYCLE_METRICS.get()
    if not isinstance(metrics, dict):
        return
    metrics["runtime_lock_hold_seconds"] = max(
        float(metrics.get("runtime_lock_hold_seconds", 0.0)),
        max(0.0, float(elapsed_seconds)),
    )


@contextmanager
def _measured_runtime_state_lock(config: dict[str, Any]):
    """Acquire runtime admission and measure only the exclusive hold.

    ``runtime_lock_hold_seconds`` is canary evidence about the time other
    writers are excluded.  Starting the clock before the blocking acquisition
    conflates contention with ownership and can report an arbitrarily long
    hold even when the critical section is bounded.
    """

    with runtime_state_lock(config, shared=False, nonblocking=False):
        acquired_at = time.monotonic()
        try:
            yield
        finally:
            _record_cycle_runtime_lock_hold(time.monotonic() - acquired_at)


def _bounded_cycle_metrics_snapshot(*, finished_monotonic: float) -> dict[str, Any] | None:
    metrics = _CYCLE_METRICS.get()
    if not isinstance(metrics, dict):
        return None
    started = float(metrics.get("started_monotonic", finished_monotonic))
    phases = {
        str(name)[:80]: {
            "count": int(row.get("count", 0)),
            "elapsed_seconds": round(float(row.get("elapsed_seconds", 0.0)), 3),
            "max_seconds": round(float(row.get("max_seconds", 0.0)), 3),
        }
        for name, row in list((metrics.get("phases") or {}).items())[
            :CYCLE_PHASE_METRICS_MAX
        ]
        if isinstance(row, dict)
    }
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "cycle_elapsed_seconds": round(max(0.0, finished_monotonic - started), 3),
        "phase_elapsed": phases,
        "batch_counts": {
            str(name)[:80]: max(0, int(count))
            for name, count in list((metrics.get("batch_counts") or {}).items())[
                :CYCLE_BATCH_COUNT_MAX
            ]
        },
    }
    cadence = metrics.get("cadence")
    if isinstance(cadence, dict):
        snapshot["cadence"] = {
            "scheduled_deadline": round(float(cadence.get("scheduled_deadline", 0.0)), 6),
            "start_overshoot_seconds": round(
                max(0.0, float(cadence.get("start_overshoot_seconds", 0.0))),
                3,
            ),
            "skipped_deadlines_before_start": max(
                0,
                int(cadence.get("skipped_deadlines_before_start", 0)),
            ),
        }
    if "runtime_lock_hold_seconds" in metrics:
        snapshot["runtime_lock_hold_seconds"] = round(
            max(0.0, float(metrics["runtime_lock_hold_seconds"])),
            3,
        )
    return snapshot


SESSION_ID_PATTERNS = [
    re.compile(r'"session_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"sessionId"\s*:\s*"([^"]+)"'),
]
URL_PATTERN = re.compile(r"https://github\.com/[^\s)]+")
RATE_LIMIT_EVENT_LINE_PATTERN = re.compile(r'"type"\s*:\s*"rate_limit_event"', re.IGNORECASE)
NONTHROTTLING_RATE_LIMIT_STATUSES = frozenset({"allowed", "allowed_warning"})
NONTHROTTLING_RATE_LIMIT_LINE_PATTERN = re.compile(
    r'"status"\s*:\s*"(?:allowed|allowed_warning)"',
    re.IGNORECASE,
)
RUNNER_FAILURE_STATUSES = frozenset({"error", "failed"})
PROVIDER_STREAM_FAILURE_STATUSES = frozenset(
    {"blocked", "denied", "error", "failed", "rate_limited", "rejected"}
)
PROVIDER_STREAM_FAILURE_TYPES = frozenset({"error", "failure"})
PROVIDER_STREAM_FAILURE_SUBTYPES = frozenset(
    {"error", "error_during_execution", "failed", "failure"}
)

LOCAL_TZ = ZoneInfo("Asia/Taipei")
SUPERVISOR_LOG_QUIET = False
GENERIC_WORKER_EXIT_REASON = "Worker exited before the task reached a terminal status."
_UNSET = object()


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


def supervisor_lock_path(config: dict[str, Any]) -> Path:
    coord_root = resolved_coordinator_status_root(config)
    return coord_root / ".orchestrator" / "supervisor.lock"


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


def check_status_root_consistency(config: dict[str, Any], allow_isolated: bool = False) -> None:
    env_val = os.environ.get("PANTHEON_STATUS_ROOT")
    if not env_val or not env_val.strip():
        # 完全清空的 env -> 不檢查
        return

    env_status_root = Path(os.path.expanduser(env_val.strip())).resolve()
    cfg_status_root = config_status_root(config)

    if env_status_root != cfg_status_root:
        if allow_isolated:
            return
        msg = (
            f"ERROR: PANTHEON_STATUS_ROOT consistency gate failed!\n"
            f"  Environment PANTHEON_STATUS_ROOT = {env_status_root}\n"
            f"  Config resolved status root     = {cfg_status_root}\n"
            f"Paths do not match. To run supervisor in this configuration, "
            f"either unset PANTHEON_STATUS_ROOT, align the config paths, or pass --allow-isolated-status-root."
        )
        print(msg, file=sys.stderr)
        sys.exit(1)


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
            with runtime_state_lock(config, shared=False, nonblocking=False):
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
    parser.add_argument(
        "--allow-isolated-status-root",
        action="store_true",
        help=(
            "Allow supervisor to start when environment PANTHEON_STATUS_ROOT "
            "does not match config resolved status root."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress terminal heartbeat output.")
    parser.add_argument("--verbose", action="store_true", help="Print active worker and queue details each tick.")
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
    active_statuses = {"running", "started", "waiting_approval", "suspended_approval", "retry_backoff", "stalled"}
    active_workers = [
        {
            "run_id": run_id,
            "task_id": worker.get("task_id"),
            "agent_id": worker.get("agent_id"),
            "provider": worker.get("provider"),
            "status": worker.get("status"),
            "lease_status_description": worker_lease_status_description({}, worker),
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


def assistant_dev_bridge_tooling_dirs(repo_root: Path) -> list[Path]:
    """Locate the local development-bridge package, never product BFF code."""

    code_tooling_dir = THIS_DIR
    repo_tooling_dir = repo_root / ".orchestrator"
    dirs: list[Path] = []
    for candidate in (code_tooling_dir, repo_tooling_dir):
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def drain_assistant_dev_packet_inbox(config: dict[str, Any], state: dict[str, Any]) -> bool:
    settings = config.get("assistant_dev_bridge") if isinstance(config.get("assistant_dev_bridge"), dict) else {}
    if settings.get("enabled") is False:
        return False

    try:
        repo_root = config_path(config, "status_file").parent
    except KeyError:
        repo_root = THIS_DIR.parent
    tooling_dirs = assistant_dev_bridge_tooling_dirs(repo_root)
    for tooling_dir in reversed(tooling_dirs):
        if str(tooling_dir) not in sys.path:
            sys.path.insert(0, str(tooling_dir))

    try:
        from development_bridge.dev_bridge_inbox import drain_task_packet_inbox
    except Exception as exc:
        write_activity_log(
            config,
            {
                "type": "assistant_dev_packet_drain_unavailable",
                "message": f"Assistant dev packet inbox drain unavailable: {type(exc).__name__}: {exc}",
                "searched_tooling_dirs": [str(path) for path in tooling_dirs],
            },
        )
        bridge_state = state.setdefault("assistant_dev_bridge", {})
        bridge_state["last_drain_at"] = utc_now()
        bridge_state["last_result"] = {
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }
        return True

    limit_value = settings.get("max_packets_per_tick", settings.get("limit", 4))
    try:
        limit = max(0, int(limit_value))
    except (TypeError, ValueError):
        limit = 4
    bridge_runtime_env = {
        "PANTHEON_STATUS_ROOT": str(repo_root.resolve()),
        "PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK": "1",
        **status_command_runtime_env(config),
    }
    result = drain_task_packet_inbox(
        repo_root=str(repo_root),
        inbox_dir=settings.get("inbox_path") or settings.get("inbox_dir"),
        limit=limit,
        dispatch_env=bridge_runtime_env,
    )
    processed_count = int(result.get("processedCount") or 0)
    error_count = int(result.get("errorCount") or 0)
    if processed_count == 0 and error_count == 0:
        return False

    bridge_state = state.setdefault("assistant_dev_bridge", {})
    bridge_state["last_drain_at"] = utc_now()
    bridge_state["last_result"] = result
    canonical_readbacks = [
        readback
        for item in result.get("packets", [])
        if isinstance(item, dict)
        for dispatch_result in [item.get("result")]
        if isinstance(dispatch_result, dict)
        for audit_refs in [dispatch_result.get("auditRefs")]
        if isinstance(audit_refs, dict)
        for readback in [audit_refs.get("materializationReadback")]
        if isinstance(readback, dict) and readback.get("status") == "verified"
    ]
    write_activity_log(
        config,
        {
            "type": "assistant_dev_packet_inbox_drained",
            "message": (
                "Drained assistant dev packet inbox: "
                f"processed={processed_count} errors={error_count}"
            ),
            "processed_count": processed_count,
            "error_count": error_count,
            "packet_ids": [
                item.get("packetId")
                for item in result.get("packets", [])
                if isinstance(item, dict) and item.get("packetId")
            ],
            "canonical_task_ids": sorted(
                {
                    str(task_id)
                    for readback in canonical_readbacks
                    for task_id in readback.get("taskIds", [])
                    if str(task_id or "").strip()
                }
            ),
            "canonical_readbacks": canonical_readbacks,
        },
    )
    return True


def safe_load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    try:
        return load_approval_state(config)
    except KeyError:
        return {"pending": [], "history": []}


def stamp_supervisor_runtime_state(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    heartbeat_at: str,
    lifecycle: str | None = None,
    loop_started_at: str | object = _UNSET,
    loop_finished_at: str | object = _UNSET,
    loop_error: str | None | object = _UNSET,
) -> None:
    supervisor_state = state.setdefault("supervisor", {})
    current_pid = os.getpid()
    previous_pid = supervisor_state.get("pid")

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

def bootstrap_supervisor_runtime_state(
    config: dict[str, Any],
    *,
    lifecycle: str = "starting",
) -> dict[str, Any]:
    """Initialize one supervisor process without discarding active leases.

    Every V2 process start restores the exact V2 runtime cache.  This retains
    each active lease across supervisor replacement and watchdog restart.
    """

    with runtime_state_update(config) as state:
        heartbeat_at = utc_now()
        stamp_supervisor_runtime_state(
            config,
            state,
            heartbeat_at=heartbeat_at,
            lifecycle=lifecycle,
        )
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
    mode = "once" if once else "tick"
    console_log(
        (
            f"supervisor {mode}: lifecycle={lifecycle} heartbeat={heartbeat_local} lag={lag_summary} changed={'yes' if changed else 'no'} "
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


def provider_config_for(config: dict[str, Any], provider: str | None) -> dict[str, Any]:
    providers = config.get("providers", {}) or {}
    raw = str(provider or "").strip()
    if not raw or not isinstance(providers.get(raw), dict):
        return {}
    return dict(providers[raw])


def validate_provider_accounts(config: dict[str, Any]) -> None:
    """Enforce the single authoritative account and capacity schema."""
    settings = ready_dispatch_settings(config)
    errors: list[str] = []
    retired_ready_keys = {
        "disabled_agents": "use agents.<id>.max_parallel=0",
        "max_tasks_per_agent": "use agents.<id>.max_parallel",
        "max_tasks_per_agent_by_agent": "use agents.<id>.max_parallel",
        "max_concurrent_per_quota_group": "use max_concurrent_per_account",
        "preferred_lane_order": "task assignment is owner/reviewer only",
        "preferredLaneOrder": "task assignment is owner/reviewer only",
    }
    for key, replacement in retired_ready_keys.items():
        if key in settings:
            errors.append(f"ready_dispatcher.{key} is retired; {replacement}")
    providers = config.get("providers", {}) or {}
    agents = config.get("agents", {}) or {}
    account_limits = settings.get("max_concurrent_per_account")
    if not isinstance(account_limits, dict):
        errors.append("ready_dispatcher.max_concurrent_per_account must be an object")
        account_limits = {}
    normalized_account_limits: dict[str, Any] = {}
    for raw_account, raw_limit in account_limits.items():
        account = normalize_agent_id(str(raw_account))
        if not account or account in normalized_account_limits:
            errors.append(
                "ready_dispatcher.max_concurrent_per_account has an invalid or duplicate account"
            )
            continue
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            errors.append(
                f"ready_dispatcher.max_concurrent_per_account.{raw_account} must be an integer"
            )
            continue
        if isinstance(raw_limit, bool) or limit < 0:
            errors.append(
                f"ready_dispatcher.max_concurrent_per_account.{raw_account} must be >= 0"
            )
            continue
        normalized_account_limits[account] = limit
    configured_accounts: set[str] = set()
    for provider, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            errors.append(f"providers.{provider} must be an object")
            continue
        account = normalize_agent_id(str(provider_cfg.get("account") or ""))
        if not account:
            errors.append(f"providers.{provider}.account is required")
        else:
            configured_accounts.add(account)
        for retired in ("file_inbox", "allow_inbox_fallback"):
            if retired in provider_cfg:
                errors.append(
                    f"providers.{provider}.{retired} is retired; delivery must fail closed"
                )
        aliases = [
            key
            for key in ("account_group", "quota_group", "dispatch_group")
            if str(provider_cfg.get(key) or "").strip()
        ]
        if aliases:
            errors.append(
                f"providers.{provider} uses deprecated account aliases: {', '.join(aliases)}"
            )
    for account in sorted(configured_accounts):
        if account not in normalized_account_limits:
            errors.append(
                f"ready_dispatcher.max_concurrent_per_account.{account} is required"
            )
    slots_by_parent: dict[str, set[str]] = {}
    for agent_id, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict):
            errors.append(f"agents.{agent_id} must be an object")
            continue
        if "file_inbox_path" in agent_cfg:
            errors.append(f"agents.{agent_id}.file_inbox_path is retired")
        adapter = str(agent_cfg.get("adapter") or "")
        if adapter not in ADAPTERS:
            errors.append(f"agents.{agent_id}.adapter is unsupported: {adapter or '(empty)'}")
        provider_id = str(agent_cfg.get("provider") or "").strip()
        if not provider_id or not provider_config_for(config, provider_id):
            errors.append(
                f"agents.{agent_id}.provider does not name a configured provider: "
                f"{provider_id or '(empty)'}"
            )
        if agent_is_dispatch_slot(agent_cfg):
            parent = normalize_agent_id(str(agent_cfg.get("dispatch_slot_for") or ""))
            normalized_slot = normalize_agent_id(agent_id)
            parent_cfg = agents.get(parent)
            if not parent or not isinstance(parent_cfg, dict) or agent_is_dispatch_slot(parent_cfg):
                errors.append(f"agents.{agent_id}.dispatch_slot_for has no logical parent")
            slots_by_parent.setdefault(parent, set()).add(normalized_slot)
            continue
        try:
            capacity = int(agent_cfg.get("max_parallel"))
        except (TypeError, ValueError):
            errors.append(f"agents.{agent_id}.max_parallel is required")
            continue
        if capacity < 0:
            errors.append(f"agents.{agent_id}.max_parallel must be >= 0")
    for agent_id, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict) or agent_is_dispatch_slot(agent_cfg):
            continue
        declared = {
            normalize_agent_id(str(slot))
            for slot in (agent_cfg.get("worker_slots") or [])
            if normalize_agent_id(str(slot))
        }
        actual = slots_by_parent.get(normalize_agent_id(agent_id), set())
        if declared != actual:
            errors.append(
                f"agents.{agent_id}.worker_slots must exactly match dispatch_slot_for children"
            )
    reassignment = config.get("worker_reassignment", {}) or {}
    if not isinstance(reassignment, dict):
        errors.append("worker_reassignment must be an object")
    else:
        known_reassignment_agents = {
            canonical_agent_name(config, name).casefold()
            for name in known_agent_display_names(config)
            if canonical_agent_name(config, name)
        }
        for mapping_name in ("owner_fallbacks", "reviewer_fallbacks"):
            mapping = reassignment.get(mapping_name, {})
            if not isinstance(mapping, dict):
                errors.append(f"worker_reassignment.{mapping_name} must be an object")
                continue
            for raw_root, raw_targets in mapping.items():
                root = canonical_agent_name(config, str(raw_root))
                if not root or root.casefold() not in known_reassignment_agents:
                    errors.append(
                        f"worker_reassignment.{mapping_name} has unknown root {raw_root!r}"
                    )
                if not isinstance(raw_targets, list):
                    errors.append(
                        f"worker_reassignment.{mapping_name}.{raw_root} must be a list"
                    )
                    continue
                for raw_target in raw_targets:
                    target = canonical_agent_name(config, str(raw_target))
                    if not target or target.casefold() not in known_reassignment_agents:
                        errors.append(
                            f"worker_reassignment.{mapping_name}.{raw_root} has unknown target {raw_target!r}"
                        )
                    elif target in sidecar_only_agent_names(config):
                        errors.append(
                            f"worker_reassignment.{mapping_name}.{raw_root} targets sidecar-only agent {target}"
                        )
    if errors:
        raise ValueError("invalid provider account configuration: " + "; ".join(errors))


def provider_account_id(config: dict[str, Any], provider: str | None) -> str:
    provider_cfg = provider_config_for(config, provider)
    return normalize_agent_id(str(provider_cfg.get("account") or ""))


def agent_provider_key(config: dict[str, Any], agent_id: str | None) -> str:
    """Return the exact configured provider key for an agent.

    Account/concurrency identities intentionally normalize punctuation, while
    provider configuration and capability reports retain keys such as
    ``codex1-1``.  Auth probes must use the latter or slotted providers silently
    miss their own runtime profile.
    """
    normalized_agent = normalize_agent_id(agent_id or "")
    if not normalized_agent:
        return ""
    agent = (config.get("agents", {}) or {}).get(normalized_agent, {}) or {}
    raw_provider = str(agent.get("provider") or "").strip()
    providers = config.get("providers", {}) or {}
    if raw_provider in providers:
        return raw_provider
    return ""


def agent_account_id(config: dict[str, Any], agent_id: str | None) -> str:
    # Provider keys retain punctuation (for example ``codex1-1``), while
    # account identities normalize it.  Resolve the exact provider key before
    # asking for its account or slotted lanes silently lose their account.
    provider_key = agent_provider_key(config, agent_id)
    return provider_account_id(config, provider_key)


def active_account_counts(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for worker in state.get("workers", {}).values():
        if worker.get("status") not in active_statuses:
            continue
        group_id = provider_account_id(
            config,
            str(worker.get("provider") or worker.get("agent_id") or ""),
        )
        if group_id:
            counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def queued_account_counts(
    config: dict[str, Any],
    state: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    task_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    queue_records = state.get("queue", {}).get("events", {})
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_queue_event_ids = {
        str(worker.get("queue_event_id") or "")
        for worker in state.get("workers", {}).values()
        if worker.get("status") in active_statuses and worker.get("queue_event_id")
    }
    if events is None:
        events = queue_events(state)
    queued_events = events
    for event in queued_events:
        if task_map is not None and stale_dispatch_skip_message(config, event, task_map):
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        if event_id in active_queue_event_ids:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
            continue
        group_id = agent_account_id(config, str(event.get("target_agent") or ""))
        if not group_id:
            continue
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def account_concurrency_limit(
    config: dict[str, Any],
    agent_id: str | None,
    settings: dict[str, Any] | None = None,
) -> int | None:
    settings = settings or ready_dispatch_settings(config)
    group_id = agent_account_id(config, agent_id)
    return rewrite_concurrency.account_limit(
        group_id,
        settings=settings,
    )


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
    return rewrite_concurrency.max_parallel(
        config,
        normalized,
        settings=settings,
        display_name=display_name_for(config, normalized),
    )


def delivery_health_settings(config: Mapping[str, Any]) -> dict[str, int]:
    """Return the single timing policy for cached delivery evidence.

    The old supervisor had separate 300s admission, 900s cache reuse, pause,
    recovery and hysteresis clocks.  V2 has one evidence TTL and one bounded
    refresh retry interval.  The values live in a dedicated section so no
    provider adapter or queue path can invent its own cadence.
    """

    raw = config.get("delivery_health")
    raw = raw if isinstance(raw, Mapping) else {}

    def positive(name: str, default: int) -> int:
        try:
            return max(1, int(raw.get(name, default)))
        except (TypeError, ValueError):
            return default

    return {
        "evidence_ttl_seconds": positive("evidence_ttl_seconds", 300),
        "retry_after_seconds": positive("retry_after_seconds", 60),
        "refresh_max_per_cycle": positive("refresh_max_per_cycle", 4),
    }


def runtime_delivery_health(state: Mapping[str, Any]) -> dict[str, Any]:
    """Read the one scheduler health authority from runtime state.

    ``provider_capabilities.json`` is intentionally not consulted here: it is
    adapter telemetry and may be stale.  All callers receive a detached,
    normalized snapshot so planning is pure.
    """

    return rewrite_provider_health.normalize_delivery_health(
        state.get("delivery_health") if isinstance(state, Mapping) else None
    )


def _delivery_endpoint_for_agent(
    config: dict[str, Any],
    endpoint_id: str,
    *,
    exclusive: bool,
) -> rewrite_dispatch_admission.DeliveryEndpoint:
    agent = (config.get("agents", {}) or {}).get(normalize_agent_id(endpoint_id))
    agent = agent if isinstance(agent, Mapping) else {}
    provider = agent_provider_key(config, endpoint_id)
    provider_config = provider_config_for(config, provider)
    configured = bool(agent and provider and provider_config)
    # Physical worker slots describe delivery topology, not independent
    # scheduling policy.  Their capacity is governed by the logical parent;
    # requiring agents.<slot>.max_parallel would disable every correctly
    # configured slot because startup validation intentionally requires that
    # field only on logical agents.
    capacity_agent_id = normalize_agent_id(
        str(agent.get("dispatch_slot_for") or endpoint_id)
    )
    # Config validation establishes that every live agent has a supported
    # adapter and provider.  This remaining static gate protects a stale
    # runtime event whose endpoint was removed after it was queued.
    enabled = configured and agent_dispatch_capacity(config, capacity_agent_id) > 0
    return rewrite_dispatch_admission.DeliveryEndpoint(
        endpoint_id=normalize_agent_id(endpoint_id),
        provider_id=provider,
        account_id=agent_account_id(config, endpoint_id),
        enabled=enabled,
        can_auto_deliver=enabled,
        exclusive=exclusive,
    )


def delivery_lane_for_agent(
    config: dict[str, Any],
    agent_id: str,
) -> rewrite_dispatch_admission.DispatchLane:
    """Project configured logical worker topology into pure admission input."""

    logical_id = normalize_agent_id(agent_id)
    configured_slots = logical_worker_slot_ids(config, logical_id)
    slots = configured_slots or [logical_id]
    return rewrite_dispatch_admission.DispatchLane(
        lane_id=logical_id,
        assignment_identity=display_name_for(config, logical_id) or logical_id,
        max_parallel=agent_dispatch_capacity(config, logical_id),
        endpoints=tuple(
            _delivery_endpoint_for_agent(
                config,
                slot,
                exclusive=bool(configured_slots),
            )
            for slot in slots
        ),
        enabled=logical_id in (config.get("agents", {}) or {}),
    )


def _admission_health_records(
    snapshot: Mapping[str, Any],
    bucket: str,
) -> dict[str, rewrite_dispatch_admission.HealthRecord]:
    records: dict[str, rewrite_dispatch_admission.HealthRecord] = {}
    values = snapshot.get(bucket)
    if not isinstance(values, Mapping):
        return records
    for identity, raw in values.items():
        if not isinstance(raw, Mapping):
            continue
        # Apply expiry while projecting the durable document.  The evaluator
        # deliberately has no schema or timestamp parser, but it must never
        # see an expired ``healthy`` entry as launch permission.
        entry = (
            rewrite_provider_health.endpoint_health_entry(snapshot, str(identity))
            if bucket == "endpoints"
            else rewrite_provider_health.account_health_entry(snapshot, str(identity))
        )
        state = str(entry.get("state") or "unknown")
        records[str(identity)] = rewrite_dispatch_admission.HealthRecord(
            state=state,
            retry_at=_parse_iso_utc(str(entry.get("retry_at") or "")),
            refresh_at=_parse_iso_utc(str(entry.get("retry_at") or "")),
        )
    return records


def build_delivery_admission_snapshot(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    active_task_ids: set[str],
    pending_task_ids: set[str],
    agent_loads: Mapping[str, list[int]],
    active_account_loads: Mapping[str, int],
    pending_account_loads: Mapping[str, int],
    live_total: int | None = None,
) -> rewrite_dispatch_admission.AdmissionSnapshot:
    """Build the shared immutable input used by plan and queue delivery."""

    settings = ready_dispatch_settings(config)
    active_statuses = normalized_status_set(settings.get("active_worker_statuses"), [])
    reserved_endpoints = {
        normalize_agent_id(str(worker.get("agent_id") or ""))
        for worker in (state.get("workers") or {}).values()
        if isinstance(worker, Mapping)
        and str(worker.get("status") or "") in active_statuses
        and normalize_agent_id(str(worker.get("agent_id") or ""))
    }
    for event_id, record in ((state.get("queue") or {}).get("events", {}) or {}).items():
        if not isinstance(record, Mapping) or str(record.get("status") or "") in {"completed", "failed"}:
            continue
        # Queue rows predate V2 endpoint binding.  Their logical target still
        # reserves capacity, but only new V2 events reserve an exact slot.
        endpoint = normalize_agent_id(str(record.get("delivery_endpoint_id") or ""))
        if endpoint:
            reserved_endpoints.add(endpoint)

    lanes: dict[str, int] = {}
    for logical_id in dispatch_loop_agent_ids(config):
        display = display_name_for(config, logical_id) or logical_id
        lanes[logical_id] = len(agent_loads.get(display, []))

    account_limits: dict[str, int] = {}
    for logical_id in dispatch_loop_agent_ids(config):
        account = agent_account_id(config, logical_id)
        if account:
            limit = account_concurrency_limit(config, logical_id, settings)
            account_limits[account] = 0 if limit is None else limit
    health = runtime_delivery_health(state)
    active_count = sum(
        1
        for worker in (state.get("workers") or {}).values()
        if isinstance(worker, Mapping) and str(worker.get("status") or "") in active_statuses
    )
    pending_count = max(0, len(pending_task_ids - active_task_ids))
    return rewrite_dispatch_admission.AdmissionSnapshot(
        now=datetime.now(timezone.utc),
        endpoint_health=_admission_health_records(health, "endpoints"),
        account_health=_admission_health_records(health, "accounts"),
        global_reserved=max(active_count, int(live_total or 0)) + pending_count,
        global_limit=ready_dispatch_max_concurrent_workers(config),
        lane_reserved=lanes,
        account_reserved={
            key: int(active_account_loads.get(key, 0)) + int(pending_account_loads.get(key, 0))
            for key in set(active_account_loads) | set(pending_account_loads)
        },
        account_limits=account_limits,
        reserved_endpoint_ids=frozenset(reserved_endpoints),
        leased_task_ids=frozenset(active_task_ids),
        pending_task_ids=frozenset(pending_task_ids),
    )


def evaluate_task_delivery_admission(
    config: dict[str, Any],
    state: dict[str, Any],
    task: Mapping[str, Any],
    target_agent: str,
    task_resolver: TaskResolver | dict[str, dict[str, Any]],
    *,
    active_task_ids: set[str],
    pending_task_ids: set[str],
    agent_loads: Mapping[str, list[int]],
    active_account_loads: Mapping[str, int],
    pending_account_loads: Mapping[str, int],
    live_total: int | None = None,
    requested_endpoint_id: str | None = None,
) -> rewrite_dispatch_admission.DispatchDecision:
    """Run the exact same task/health/capacity predicate in plan and delivery."""

    settings = ready_dispatch_settings(config)
    task_intent = rewrite_dispatch_admission.TaskIntent(
        task_id=str(task.get("id") or ""),
        status=str(task.get("status") or ""),
        owner=str(task.get((config.get("schema") or {}).get("assignee_field", "owner")) or ""),
        reviewer=str(task.get((config.get("schema") or {}).get("reviewer_field", "reviewer")) or ""),
        dependencies_satisfied=dependencies_satisfied(
            dict(task),
            task_resolver,
            normalized_status_set(settings.get("dependency_done_statuses"), ["done"]),
        ),
        human_ops_hold=bool(str(task.get("waiting_for") or "").strip()),
        review_binding_current=rewrite_task_machine.delivery_binding_is_current(task),
    )
    return rewrite_dispatch_admission.evaluate_dispatch_intent(
        task_intent,
        delivery_lane_for_agent(config, target_agent),
        build_delivery_admission_snapshot(
            config,
            state,
            active_task_ids=active_task_ids,
            pending_task_ids=pending_task_ids,
            agent_loads=agent_loads,
            active_account_loads=active_account_loads,
            pending_account_loads=pending_account_loads,
            live_total=live_total,
        ),
        requested_endpoint_id=requested_endpoint_id,
    )


def probe_demanded_delivery_health(
    config: dict[str, Any],
    demands: Iterable[Mapping[str, Any]],
    *,
    quiet: bool,
) -> list[dict[str, Any]]:
    """Observe only exact endpoints requested by the pure evaluator.

    This is deliberately post-delivery I/O.  A stale lane is fail-closed for
    the current cycle, but a fresh healthy lane never waits for another
    provider CLI subprocess before its queue intent is launched.  Account
    capacity is evidenced through the endpoint that made the request; there
    is no separate account probe or old pause-recovery scheduler.
    """

    max_refresh = delivery_health_settings(config)["refresh_max_per_cycle"]
    endpoint_ids: list[str] = []
    for raw in demands:
        if not isinstance(raw, Mapping) or str(raw.get("scope") or "") != "endpoint":
            continue
        endpoint_id = normalize_agent_id(str(raw.get("id") or ""))
        if endpoint_id and endpoint_id not in endpoint_ids:
            endpoint_ids.append(endpoint_id)
        if len(endpoint_ids) >= max_refresh:
            break

    observations: list[dict[str, Any]] = []
    for endpoint_id in endpoint_ids:
        provider_id = agent_provider_key(config, endpoint_id)
        account_id = agent_account_id(config, endpoint_id)
        if not provider_id or not account_id:
            continue
        probe = _safe_phase(
            f"probe_delivery_health:{endpoint_id}",
            probe_provider_auth,
            config,
            provider_id,
            force=True,
        )
        if not isinstance(probe, Mapping):
            probe = {
                "provider": provider_id,
                "ready": False,
                "status": "probe_error",
                "error": "delivery health probe returned no normalized observation",
                "checked_at": utc_now(),
                "source": "live",
            }
        observations.append(
            {
                "endpoint_id": endpoint_id,
                "account_id": account_id,
                "probe": dict(probe),
            }
        )
    return observations


def apply_delivery_health_observations(
    config: dict[str, Any],
    state: dict[str, Any],
    observations: Iterable[Mapping[str, Any]],
) -> bool:
    """Commit post-I/O endpoint observations to the one health authority."""

    settings = delivery_health_settings(config)
    snapshot = runtime_delivery_health(state)
    updated = snapshot
    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        endpoint_id = str(observation.get("endpoint_id") or "").strip()
        account_id = str(observation.get("account_id") or "").strip()
        probe = observation.get("probe")
        if not endpoint_id or not account_id or not isinstance(probe, Mapping):
            continue
        updated = rewrite_provider_health.apply_probe(
            updated,
            endpoint_id=endpoint_id,
            account_id=account_id,
            probe=probe,
            valid_for_seconds=settings["evidence_ttl_seconds"],
            retry_after_seconds=settings["retry_after_seconds"],
        )
    if updated == snapshot:
        return False
    state["delivery_health"] = updated
    return True


def record_delivery_health_failure(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    agent_id: str | None,
    failure_kind: str,
    detail: str | None = None,
    retry_at: object = None,
) -> bool:
    """Project a worker failure into the same admission evidence document."""

    endpoint_id = normalize_agent_id(str(agent_id or ""))
    account_id = agent_account_id(config, endpoint_id)
    if not endpoint_id or not account_id:
        return False
    settings = delivery_health_settings(config)
    before = runtime_delivery_health(state)
    after = rewrite_provider_health.apply_failure(
        before,
        endpoint_id=endpoint_id,
        account_id=account_id,
        failure_kind=failure_kind,
        retry_at=retry_at,
        valid_for_seconds=settings["evidence_ttl_seconds"],
        retry_after_seconds=settings["retry_after_seconds"],
        detail=detail,
    )
    if after == before:
        return False
    state["delivery_health"] = after
    return True


def worker_execution_context_files(task_id: str | None) -> list[str]:
    """Describe worker context without writing into the command checkout.

    The common context helper materializes a generated task brief beside the
    supervisor source.  That is appropriate for an interactive source
    checkout, but a mutable bootstrap incumbent must keep its tracked source
    tree byte-clean until promotion captures it.  Isolated worker preparation
    already copies a status-root brief when one exists and otherwise renders
    the brief inside the task worktree, so queue construction only needs the
    stable repository-relative destination here.
    """

    files = ["AI_COLLABORATION_GUIDE.md"]
    normalized_task_id = normalize_agent_id(task_id or "")
    if normalized_task_id:
        files.append(
            f".orchestrator/task-briefs/{normalized_task_id}.md"
        )
    for relative_path in (
        ".orchestrator/skills/worker-anchor-commit.md",
        ".orchestrator/skills/task-closeout-finalization.md",
    ):
        if (THIS_DIR.parent / relative_path).is_file():
            files.append(relative_path)
    files.append("ai-status.json")
    return files


def build_request(
    config: dict[str, Any],
    event: dict[str, Any],
    *,
    agent_id_override: str | None = None,
) -> DeliveryRequest:
    logical_agent = agent_config_for(config, event["target_agent"])
    agent = agent_config_for(config, agent_id_override or event["target_agent"])
    metadata = dict(event.get("metadata", {}) or {})
    dispatch_event_key = str(event.get("event_key") or "").strip()
    if dispatch_event_key:
        metadata.setdefault("dispatch_event_key", dispatch_event_key)
    if "task_generation" in event:
        metadata.setdefault("task_generation", event.get("task_generation"))
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
        context_files = worker_execution_context_files(event.get("task_id"))
    return DeliveryRequest(
        agent_id=agent["id"],
        provider=agent.get("provider", agent["id"]),
        delivery_mode=config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
            "delivery_mode", str(agent.get("adapter") or "")
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
        "task_generation": request.metadata.get("task_generation"),
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
]


def worker_worktree_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktrees")
    settings = raw if isinstance(raw, dict) else {}
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    return {
        "enabled": bool(settings.get("enabled", False)),
        "root": str(settings.get("root") or "/tmp/pantheon-worker-worktrees"),
        "source_root": str(settings.get("source_root") or settings.get("repo_root") or "").strip(),
        "base_ref": str(settings.get("base_ref") or f"origin/{branch_workflow.get('dev_branch') or 'dev'}"),
        "reuse_existing": bool(settings.get("reuse_existing", True)),
        "execution_reasons": list(settings.get("execution_reasons") or WORKER_WORKTREE_EXECUTION_REASONS),
    }


def worktree_cleanup_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktree_cleanup")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "cleanup_inactive_leases": bool(settings.get("cleanup_inactive_leases", True)),
        "archive_dirty_worktrees": bool(settings.get("archive_dirty_worktrees", True)),
        "force_remove_archived_dirty": bool(settings.get("force_remove_archived_dirty", True)),
        "archive_root": str(settings.get("archive_root") or "/tmp/pantheon-worker-worktree-archive"),
        "archive_max_file_bytes": int(settings.get("archive_max_file_bytes", 20 * 1024 * 1024) or 0),
        "max_removals_per_tick": int(settings.get("max_removals_per_tick", 25) or 0),
        "base_branches": [
            str(b).strip()
            for b in (settings.get("base_branches") or ["dev", "master", "main"])
            if str(b).strip()
        ],
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


def worker_worktree_source_root(
    config: dict[str, Any],
    settings: dict[str, Any] | None = None,
    *,
    repository_id: str = "pantheon",
) -> Path:
    """Return the writable git checkout used to create worker worktrees.

    The supervisor can run split-root: canonical status, activity, and queue
    files live in the shared status root, while the command checkout that owns
    ``.git/worktrees`` can be somewhere else.  Worktree creation must use the
    writable git source root; context materialization and status writes must
    continue to use the status root.
    """

    active_settings = settings or worker_worktree_settings(config)
    if repository_id != "pantheon":
        repository_root = repository_local_path(config, repository_id)
        if repository_root is None:
            raise RuntimeError(
                f"delivery repository {repository_id!r} has no registered local_path"
            )
        return repository_root.resolve()
    status_root = config_path(config, "status_file").parents[0]
    configured = str(active_settings.get("source_root") or "").strip()
    if not configured:
        return status_root.resolve()
    source_root = Path(os.path.expanduser(configured))
    if not source_root.is_absolute():
        source_root = status_root / source_root
    return source_root.resolve()


def worker_task_worktree_path(
    config: dict[str, Any],
    task_id: str | None,
    settings: dict[str, Any] | None = None,
    *,
    repository_id: str = "pantheon",
) -> Path:
    active_settings = settings or worker_worktree_settings(config)
    if repository_id == "pantheon":
        repository_name = config_path(config, "status_file").parents[0].name
    else:
        repository_name = str(
            resolve_repository(config, repository_id).get("display_name") or repository_id
        )
    repo_slug = re.sub(r"[^a-z0-9]+", "-", repository_name.lower()).strip("-") or "repo"
    return _worker_worktree_base_root(config, active_settings) / repo_slug / _task_id_slug(task_id)


def worker_request_repository_id(config: dict[str, Any], request: DeliveryRequest) -> str:
    task = request.metadata.get("task")
    task_payload = task if isinstance(task, dict) else {}
    repository_id = task_primary_repository_id(config, task_payload)
    if repository_id is None:
        raise RuntimeError(
            "task artifacts span multiple non-Pantheon delivery repositories"
        )
    declared = str(request.metadata.get("workspace_repository_id") or "").strip()
    if declared and declared != repository_id:
        raise RuntimeError(
            f"workspace repository mismatch: {declared} != {repository_id}"
        )
    return repository_id


def worker_repository_base_ref(
    config: dict[str, Any],
    settings: dict[str, Any],
    repository_id: str,
) -> str:
    if repository_id == "pantheon":
        return str(settings.get("base_ref") or "origin/dev")
    default_branch = str(
        resolve_repository(config, repository_id).get("default_branch") or ""
    ).strip()
    if not default_branch:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no default_branch"
        )
    return f"origin/{default_branch}"


def validate_worker_repository_source(
    config: dict[str, Any],
    repository_id: str,
    source_root: Path,
) -> None:
    configured_root = repository_configured_local_path(config, repository_id)
    if repository_id == "pantheon":
        source_setting = str(
            worker_worktree_settings(config).get("source_root") or ""
        ).strip()
        if source_setting:
            configured_root = Path(os.path.expanduser(source_setting))
            if not configured_root.is_absolute():
                configured_root = (
                    config_path(config, "status_file").parents[0]
                    / configured_root
                )
            configured_root = Path(os.path.abspath(configured_root))
    if configured_root is None:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no configured local_path"
        )
    configured_symlink = first_symlink_component(configured_root)
    if configured_symlink is not None:
        raise RuntimeError(
            "repository source root cannot include a symlink component: "
            f"{configured_symlink}"
        )
    if configured_root.resolve() != source_root:
        raise RuntimeError(
            f"repository source root does not match configured local_path: {source_root}"
        )
    if not source_root.is_absolute():
        raise RuntimeError(f"repository source root must be absolute: {source_root}")
    symlink_component = first_symlink_component(source_root)
    if symlink_component is not None:
        raise RuntimeError(
            f"repository source root cannot include a symlink component: {symlink_component}"
        )
    if not source_root.is_dir():
        raise RuntimeError(f"repository source root does not exist: {source_root}")
    top_proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if top_proc.returncode != 0 or Path(top_proc.stdout.strip()).resolve() != source_root:
        raise RuntimeError(f"repository source root is not a git root: {source_root}")
    expected_slug = normalize_github_repo_slug(repository_slug(config, repository_id))
    if not expected_slug:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no configured GitHub slug"
        )
    remote_proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    actual_slug = normalize_github_repo_slug(remote_proc.stdout.strip())
    if remote_proc.returncode != 0 or actual_slug != expected_slug:
        raise RuntimeError(
            f"repository source origin mismatch: {actual_slug or 'missing'} != {expected_slug}"
        )


def validate_worker_workspace_binding(
    source_root: Path,
    workspace_path: Path,
    *,
    expected_branch: str | None = None,
) -> None:
    top_proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if top_proc.returncode != 0 or Path(top_proc.stdout.strip()).resolve() != workspace_path:
        raise RuntimeError(
            f"workspace_path is not a git repository root: {workspace_path}"
        )

    def common_dir(root: Path) -> Path:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git common directory is unavailable for {root}")
        path = Path(proc.stdout.strip())
        if not path.is_absolute():
            path = root / path
        return path.resolve()

    if common_dir(workspace_path) != common_dir(source_root):
        raise RuntimeError(
            "workspace_path is not registered to the selected delivery repository"
        )
    records = {
        Path(record["worktree"]).resolve(): record
        for record in _git_worktree_records(source_root)
        if record.get("worktree")
    }
    record = records.get(workspace_path)
    if record is None:
        raise RuntimeError(
            "workspace_path is absent from the selected repository worktree registry"
        )
    branch = _worktree_record_branch(record)
    if expected_branch and branch != expected_branch:
        raise RuntimeError(
            f"workspace branch mismatch: {branch or 'detached'} != {expected_branch}"
        )


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


def worker_request_requires_isolated_worktree(request: DeliveryRequest) -> bool:
    metadata = getattr(request, "metadata", {})
    return bool(
        metadata.get("require_isolated_worktree")
        if isinstance(metadata, dict)
        else False
    )


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


def _fetch_worker_base_ref(
    repo_root: Path,
    base_ref: str,
    *,
    timeout_seconds: float | None = None,
) -> tuple[bool, str | None]:
    """Refresh the exact remote-tracking ref used to lease worker worktrees.

    ``git fetch origin dev`` updates ``FETCH_HEAD`` but does not necessarily
    update ``refs/remotes/origin/dev`` when the checkout's configured fetch
    refspec tracks only another branch (the live command checkout tracked only
    ``master``).  Worktree creation and freshness checks consume the remote-
    tracking ref, so fetch it with an explicit source and destination.

    ``timeout_seconds`` is available to standalone callers.  The supervisor
    cycle invokes this function only during its pre-admission phase; dispatch
    itself never performs a recovery fetch while holding runtime admission.
    """

    normalized = str(base_ref or "").strip()
    if normalized.startswith("origin/"):
        branch = normalized[len("origin/") :]
        refspec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    else:
        refspec = normalized
    if not refspec:
        return False, "missing_base_ref"

    try:
        proc = subprocess.run(
            ["git", "fetch", "origin", refspec, "--quiet"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"git fetch timed out after {timeout_seconds}s"
    if proc.returncode == 0:
        return True, None
    details = (proc.stderr or proc.stdout or "").strip()
    return False, details or "git fetch failed"


def _worker_base_ref_precondition(
    base_ref: str,
    repo_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Resolve or refresh a worker base during delivery preparation.

    Delivery preparation runs outside canonical locks.  A missing remote ref is
    fetched for this intent only; it never delays unrelated dispatch planning.
    """

    normalized = str(base_ref or "").strip()
    if not normalized:
        return False, "missing_base_ref"
    if repo_root is None:
        return False, f"missing_worker_source_root:{normalized}"

    if _git_ref_exists(repo_root, normalized):
        return True, None
    fetched, error = _fetch_worker_base_ref(repo_root, normalized, timeout_seconds=30)
    if fetched and _git_ref_exists(repo_root, normalized):
        return True, None
    return False, error or f"base_ref_unresolved:{normalized}"


def _quarantine_incomplete_worker_path(path: Path) -> Path | None:
    """Move an unregistered partial checkout aside so dispatch can recover.

    ``git worktree add`` can leave a populated directory without a ``.git``
    marker when checkout is interrupted (for example by ENOSPC).  These paths
    are not reusable worktrees, but refusing them forever wedges every later
    dispatch for the task.  Preserve the entire directory under the managed
    root and let the caller create a clean worktree at the canonical path.
    """
    if (
        not path.exists()
        or path.is_symlink()
        or not path.is_dir()
        or not any(path.iterdir())
        or (path / ".git").exists()
    ):
        return None

    quarantine_root = path.parent / ".incomplete-worktree-quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = quarantine_root / f"{path.name}-{stamp}-{os.getpid()}"
    try:
        path.replace(quarantine_path)
    except OSError:
        return None
    try:
        (quarantine_path / "ORCHESTRATOR_QUARANTINE.txt").write_text(
            "Incomplete worker checkout preserved before automatic redispatch.\n"
            f"original_path={path}\n"
            f"quarantined_at={utc_now()}\n",
            encoding="utf-8",
        )
    except OSError:
        # The recovery must still unblock a fresh checkout when the original
        # interruption was ENOSPC and even the small marker cannot be written.
        pass
    return quarantine_path


def _create_worker_worktree(repo_root: Path, path: Path, branch: str, base_ref: str) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        if _quarantine_incomplete_worker_path(path) is None:
            return False, f"Worker worktree path already exists and is not empty: {path}"

    base_ready, base_error = _worker_base_ref_precondition(base_ref, repo_root)
    if not base_ready:
        return False, f"Failed to refresh worker base {base_ref}: {base_error}"

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


def _staged_index_split_paths_matching_head(worktree_path: Path) -> list[str]:
    """Return staged paths whose worktree bytes already match HEAD.

    Worker worktrees can be left with a split index after a merge/review loop:
    the index stages a reverse patch while the working tree contains the branch
    HEAD content. In that case `git restore --staged` is safe because it only
    repairs the index. Real staged additions/renames or content changes must
    continue to block dispatch.
    """
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-status"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            return []
        status, path = parts[0], parts[-1]
        if status not in {"M", "D"}:
            return []
        candidate = worktree_path / path
        if not candidate.is_file():
            return []
        head_proc = subprocess.run(
            ["git", "rev-parse", f"HEAD:{path}"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        worktree_proc = subprocess.run(
            ["git", "hash-object", path],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if head_proc.returncode != 0 or worktree_proc.returncode != 0:
            return []
        if head_proc.stdout.strip() != worktree_proc.stdout.strip():
            return []
        paths.append(path)
    return paths


def _restore_reused_index_split(worktree_path: Path, paths: list[str]) -> bool:
    if not paths:
        return False
    proc = subprocess.run(
        ["git", "restore", "--staged", "--", *sorted(set(paths))],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _refresh_reused_worker_worktree(
    repo_root: Path,
    worktree_path: Path,
    base_ref: str,
    *,
    task_id: str | None = None,
    branch: str | None = None,
) -> tuple[bool, str]:
    """Fast-forward a reused worker worktree to the current base ref tip.

    Reused worktrees may carry the worker's per-task branch from days ago,
    which means their copy of `scripts/ai_status.py` / supervisor / skills can
    be older than the supervisor root. That stale snapshot has bypassed gates
    such as ORCH-CLOSEOUT-MERGE-GATE (require_merged_pr). Refresh on lease so
    the worker always sees current control-plane code.

    Strategy: fetch the exact remote-tracking ref + `git merge --ff-only
    origin/<base>`. Never auto-resolve a real merge — if the branch genuinely
    diverged, leave it for the worker to handle. Dirty reused worktrees are
    blocked before dispatch so workers cannot inherit unrelated staged or
    tracked changes.
    """
    base = base_ref.split("/", 1)[1] if base_ref.startswith("origin/") else base_ref
    base_ready, base_error = _worker_base_ref_precondition(base_ref, repo_root)
    if not base_ready:
        return False, f"fetch_failed: {base_error}"

    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    scratch_restored = False
    index_restored = False
    if status_proc.returncode == 0 and status_proc.stdout.strip():
        classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout)
        if classification == "real":
            index_split_paths = _staged_index_split_paths_matching_head(worktree_path)
            if index_split_paths and _restore_reused_index_split(worktree_path, index_split_paths):
                index_restored = True
            # No restorable staged index-split (or a failed restore) is NOT fatal:
            # fall through to re-classify and anchor genuine task WIP below instead
            # of hard-blocking dispatch forever. The previous early return here made
            # the auto-anchor unreachable for plain unstaged real dirt -- the common
            # case (a superseded run leaves modified-but-unstaged task files).
            status_proc = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if status_proc.returncode != 0:
                return False, "skipped_dirty_worktree"
            classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout)
            if classification == "real":
                # The supervisor owns leases, not source authorship.  Preserve
                # worker WIP and wait for the task's normal delivery path to
                # reconcile it; never synthesize a commit or reviewer identity.
                return False, "skipped_dirty_worktree"
            if classification == "clean":
                scratch_paths = []
        # Only orchestrator-managed scratch is dirty: restore it and reuse the
        # worktree instead of jamming dispatch on regenerable bookkeeping churn.
        if scratch_paths:
            _restore_reusable_scratch(worktree_path, scratch_paths)
            verify_untracked = "all" if index_restored else "no"
            verify_proc = subprocess.run(
                ["git", "status", "--porcelain", f"--untracked-files={verify_untracked}"],
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
        status_suffixes = []
        if scratch_restored:
            status_suffixes.append("scratch_restored")
        if index_restored:
            status_suffixes.append("index_restored")
        suffix = f"+{'+'.join(status_suffixes)}" if status_suffixes else ""
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


def _generated_worker_task_brief(
    config: dict[str, Any],
    task_id: str | None,
    *,
    finalization_context: bool = False,
) -> str:
    task = task_index_from_status(config, load_status(config)).get(str(task_id or ""))
    if not task:
        return "\n".join(
            [
                f"# Task Brief: {task_id or 'unknown-task'}",
                "",
                "Generated in the worker workspace because the supervisor root did not have a task brief file.",
                "",
                "## Coordination Root",
                "- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.",
                "- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.",
                "",
            ]
        )
    task_lines = [
        f"- Title: {task.get('title') or '-'}",
        f"- Owner: {task.get('owner') or '-'}",
        f"- Reviewer: {task.get('reviewer') or '-'}",
    ]
    if finalization_context:
        task_lines.extend(
            [
                "- Status: query the governed `ai-status.sh show` command; do not transcribe it into this file.",
                "- Next: close out only the already-reviewed delivery; do not commit this generated brief as an approval record.",
            ]
        )
    else:
        task_lines.extend(
            [
                f"- Status: {task.get('status') or '-'}",
                f"- Next: {task.get('next') or '-'}",
            ]
        )
    return "\n".join(
        [
            f"# Task Brief: {task.get('id') or task_id}",
            "",
            "Generated in the worker workspace because the supervisor root did not have a task brief file.",
            "",
            "## Task",
            *task_lines,
            "",
            "## Summary",
            str(task.get("summary_zh") or "-"),
            "",
            "## Coordination Root",
            "- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.",
            "- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.",
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
        if request.reason == "owned_finalize_dispatch":
            if destination.exists():
                # The branch already has the task-scoped context it was reviewed
                # against. Rewriting it from the live `review_approved` row would
                # manufacture a generated diff after approval, inviting an owner
                # to commit a redundant closeout record that moves the exact head.
                # Canonical status remains available through the governed `show`
                # command; preserve the reviewed branch bytes here.
                materialized.append(rel_value)
                continue
            # A fresh finalize worktree still needs the task context, but must
            # not receive a branch-local transcription of the just-recorded
            # approval. Render a stable closeout brief that directs the owner
            # to governed state instead of copying status/next into git.
            destination.write_text(
                _generated_worker_task_brief(
                    config,
                    request.task_id,
                    finalization_context=True,
                ),
                encoding="utf-8",
            )
            materialized.append(rel_value)
            continue
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


def bind_external_worker_context(
    config: dict[str, Any],
    request: DeliveryRequest,
    repository_id: str,
) -> list[str]:
    """Bind Pantheon-owned instructions without writing them into another repo."""

    if repository_id == "pantheon":
        return []
    status_root = config_path(config, "status_file").parents[0].resolve()
    command_root = THIS_DIR.parent.resolve()
    bound_context: list[str] = []
    for raw_path in request.context_files:
        raw_value = str(raw_path or "").strip()
        if not raw_value:
            continue
        candidate = Path(raw_value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            for root in (status_root, command_root):
                try:
                    resolved.relative_to(root)
                except ValueError:
                    continue
                if resolved.is_file():
                    bound_context.append(str(resolved))
                break
            continue
        for root in (status_root, command_root):
            resolved = (root / candidate).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if resolved.is_file():
                bound_context.append(str(resolved))
                break

    # Keep the delivery checkout pristine.  A generated Pantheon task brief in
    # an execute-plans worktree is neither product source nor valid evidence.
    request.context_files = list(dict.fromkeys(bound_context))
    delivery_targets = [
        str(repository_relative_artifact_path(config, artifact, repository_id))
        for artifact in request.target_files
        if artifact_repository_id(config, artifact) == repository_id
    ]
    request.metadata["workspace_target_files"] = delivery_targets
    context_lines = "\n".join(f"- {path}" for path in request.context_files) or "- (none; use governed task show)"
    target_lines = "\n".join(f"- {path}" for path in delivery_targets) or "- (none)"
    request.message += (
        "\n\nCross-repository delivery authority (this section overrides any relative "
        "Pantheon path above):\n"
        f"- Delivery repository id: {repository_id}\n"
        "- The current working directory is the supervisor-leased delivery worktree. "
        "Do not create another checkout or edit supervisor configuration.\n"
        "- The task branch is already provisioned. Do not run Pantheon's relative "
        "task_start.sh here; if the branch binding is wrong, report a blocker.\n"
        "- Run governed lifecycle commands only through "
        "`$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh`; relative Pantheon scripts "
        "are not part of this repository.\n"
        "- Read-only context files:\n"
        f"{context_lines}\n"
        "- Repository-relative delivery targets:\n"
        f"{target_lines}\n"
    )
    return []


def prepare_worker_workspace(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    target_agent: str | None,
) -> tuple[bool, str | None]:
    settings = worker_worktree_settings(config)
    requires_isolated = worker_request_requires_isolated_worktree(request)
    if not settings.get("enabled"):
        if requires_isolated:
            message = (
                f"Cannot dispatch explicit retry for {request.task_id or 'unknown task'}: "
                "isolated worker worktrees are disabled. Refusing shared-checkout fallback."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "refresh_status": "isolated_worktrees_disabled",
                },
            )
            return False, message
        return True, None
    if not requires_isolated and not worker_worktree_reason_enabled(request.reason, settings):
        return True, None
    workspace_task_id = worker_workspace_task_id(request)
    if not workspace_task_id:
        if requires_isolated:
            message = (
                "Cannot dispatch explicit retry without a task-scoped worktree identity. "
                "Refusing shared-checkout fallback."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "refresh_status": "missing_workspace_task_id",
                },
            )
            return False, message
        return True, None
    try:
        repository_id = worker_request_repository_id(config, request)
        source_root = worker_worktree_source_root(
            config,
            settings,
            repository_id=repository_id,
        )
        base_ref = worker_repository_base_ref(config, settings, repository_id)
        validate_worker_repository_source(config, repository_id, source_root)
    except RuntimeError as exc:
        message = (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: {exc}."
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
                "refresh_status": "delivery_repository_invalid",
            },
        )
        return False, message
    if request.metadata.get("workspace_path"):
        status_root = config_path(config, "status_file").parents[0].resolve()
        raw_workspace_path = Path(
            os.path.expanduser(str(request.metadata["workspace_path"]))
        )
        try:
            if not raw_workspace_path.is_absolute():
                raise RuntimeError("workspace_path must be absolute")
            workspace_symlink = first_symlink_component(raw_workspace_path)
            if workspace_symlink is not None:
                raise RuntimeError(
                    f"workspace_path contains a symlink component: {workspace_symlink}"
                )
            workspace_path = raw_workspace_path.resolve()
            if workspace_path in {status_root, source_root}:
                raise RuntimeError(
                    "workspace_path resolves to the shared supervisor or repository source checkout"
                )
            validate_worker_workspace_binding(
                source_root,
                workspace_path,
                expected_branch=str(
                    request.metadata.get("workspace_branch")
                    or worker_task_branch(config, workspace_task_id)
                ),
            )
        except RuntimeError as exc:
            message = (
                f"Cannot dispatch existing workspace for {workspace_task_id}: {exc}. "
                "Refusing unregistered checkout fallback."
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
                    "workspace_path": str(raw_workspace_path),
                    "refresh_status": "workspace_binding_rejected",
                },
            )
            return False, message
        request.metadata.update(
            {
                "workspace_path": str(workspace_path),
                "workspace_repository_id": repository_id,
                "workspace_source_root": str(source_root),
                "workspace_base_ref": base_ref,
            }
        )
        return True, None

    status_root = config_path(config, "status_file").parents[0].resolve()
    repo_root = source_root
    branch = worker_task_branch(config, workspace_task_id)
    worktree_path = worker_task_worktree_path(
        config,
        workspace_task_id,
        settings,
        repository_id=repository_id,
    )
    reused = False

    if not repo_root.exists():
        message = (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: "
            f"configured worker source root does not exist: {repo_root}."
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
                "status_root": str(status_root),
                "workspace_source_root": str(repo_root),
                "refresh_status": "source_root_missing",
            },
        )
        return False, message

    if settings.get("reuse_existing", True):
        existing = _existing_worktree_for_branch(repo_root, branch, exclude_root=True)
        if existing:
            worktree_path = existing
            reused = True
            refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                repo_root,
                worktree_path,
                base_ref,
                task_id=workspace_task_id,
                branch=branch,
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
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
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
                        "status_root": str(status_root),
                        "workspace_source_root": str(repo_root),
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
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
                },
            )
            return False, message
        created, error = _create_worker_worktree(
            repo_root,
            worktree_path,
            branch,
            base_ref,
        )
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
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
                },
            )
            return False, message
        # A local task branch can outlive its old worktree.  Creating a new
        # worktree from that branch without refreshing it resurrects stale
        # product/control-plane bytes (the live Agora candidate branch pointed
        # at an earlier Workshop merge).  Apply the same safe ff-only refresh
        # used for an existing worktree before the lease is published.
        refresh_ok, refresh_status = _refresh_reused_worker_worktree(
            repo_root,
            worktree_path,
            base_ref,
            task_id=workspace_task_id,
            branch=branch,
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
                "status_root": str(status_root),
                "workspace_source_root": str(repo_root),
                "workspace_repository_id": repository_id,
                "refresh_ok": refresh_ok,
                "refresh_status": refresh_status,
            },
        )
        if not refresh_ok and refresh_status == "skipped_dirty_worktree":
            message = (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"new worktree {worktree_path} could not be refreshed safely."
            )
            return False, message

    request.metadata.update(
        {
            "workspace_mode": "isolated_worktree",
            "workspace_path": str(worktree_path),
            "workspace_branch": branch,
            "status_root": str(status_root),
            "workspace_source_root": str(repo_root),
            "workspace_repository_id": repository_id,
            "workspace_base_ref": base_ref,
        }
    )
    if repository_id == "pantheon":
        materialized_context_files = materialize_worker_context_files(
            config, request, worktree_path
        )
    else:
        materialized_context_files = bind_external_worker_context(
            config, request, repository_id
        )
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    leases[workspace_task_id] = {
        "task_id": request.task_id,
        "workspace_task_id": workspace_task_id,
        "branch": branch,
        "path": str(worktree_path),
        "status_root": str(status_root),
        "source_root": str(repo_root),
        "repository_id": repository_id,
        "base_ref": base_ref,
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
            "status_root": str(status_root),
            "workspace_source_root": str(repo_root),
            "workspace_repository_id": repository_id,
            "workspace_base_ref": base_ref,
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


def isolated_workspace_commit_sha(
    workspace_mode: str | None,
    workspace_path: str | Path | None,
) -> str | None:
    """Read HEAD for a worker-owned worktree, never a shared checkout.

    A commit in a shared root cannot be attributed to one worker, so it must not
    renew that worker's lease. Isolated task worktrees provide the ownership
    boundary required for a real per-worker progress signal.
    """
    if str(workspace_mode or "").strip() != "isolated_worktree" or not workspace_path:
        return None
    try:
        path = Path(workspace_path).expanduser().resolve()
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = str(result.stdout or "").strip().lower()
    return sha if re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


def worker_commit_progress_snapshot(worker: dict[str, Any]) -> dict[str, Any]:
    sha = isolated_workspace_commit_sha(
        worker.get("workspace_mode"),
        worker.get("workspace_path"),
    )
    return {"commit_sha": sha} if sha else {}


def update_worker_commit_progress(
    worker: dict[str, Any],
    now: datetime,
) -> tuple[bool, bool]:
    """Observe an isolated worker's HEAD and record newly committed work.

    Returns ``(state_changed, progress_advanced)``. Merely learning a baseline
    snapshot changes state but does not manufacture progress for workers that
    predate this field.
    """
    current = worker_commit_progress_snapshot(worker)
    if not current:
        return False, False
    previous_raw = worker.get("work_progress_snapshot")
    previous = previous_raw if isinstance(previous_raw, dict) else {}
    if current == previous:
        return False, False
    baseline_missing = not previous
    advanced = (
        not baseline_missing
        and rewrite_worker_lifecycle.has_work_progress(previous, current)
    )
    worker["work_progress_snapshot"] = current
    if advanced:
        observed_at = _isoformat_utc(now)
        worker["last_commit_progress_at"] = observed_at
        worker["last_work_progress_at"] = observed_at
        worker["commit_progress_count"] = int(worker.get("commit_progress_count", 0)) + 1
    return True, advanced


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
    adapter_name = delivery_mode_override or str(agent.get("adapter") or "")
    # Admission already consumed the only dispatch health authority.  Adapters
    # receive no cached capability report, so inspection telemetry cannot reopen
    # the retired hysteresis/pause control plane during launch.
    adapter = build_adapter(adapter_name, config=config, provider_capabilities={})
    initial_work_progress_snapshot = worker_commit_progress_snapshot(
        {
            "workspace_mode": request.metadata.get("workspace_mode"),
            "workspace_path": request.metadata.get("workspace_path"),
        }
    )
    issued_command_env = status_command_runtime_env(config)
    issued_command_runtime = status_command_runtime_record_from_env(issued_command_env)
    request.metadata["status_command_runtime"] = issued_command_runtime
    _persist_runtime_phase_launch_intent(
        config,
        state,
        request=request,
        queue_event_id=queue_event_id,
        attempt_count=attempt_count,
        event_id_for_log=event_id_for_log,
        parent_run_id=parent_run_id,
        adapter_name=str(adapter_name),
        activity_type=activity_type,
        activity_message=activity_message,
    )
    result = adapter.deliver(request)
    if not result.ok:
        failure_run_id = (
            f"{event_id_for_log or queue_event_id}-attempt-{max(1, int(attempt_count))}"
        )
        failure_worker = {
            "provider": request.provider,
            "agent_id": request.agent_id,
            "task_id": request.task_id,
            "queue_event_id": event_id_for_log,
            "run_id": failure_run_id,
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
    result_pid = result.pid if isinstance(result.pid, int) and not isinstance(result.pid, bool) else None
    result_pid_start_ticks = worker_pid_start_ticks(result_pid)
    result_process_generation = (
        worker_process_generation_id(
            task_id=str(request.task_id or ""),
            worker_run_id=str(worker_run_id),
            queue_event_id=str(queue_event_id or ""),
            pid=result_pid,
            pid_start_ticks=result_pid_start_ticks,
        )
        if request.task_id
        and queue_event_id
        and result_pid is not None
        and result_pid_start_ticks is not None
        else None
    )
    now_dt = datetime.now(timezone.utc)
    now = _isoformat_utc(now_dt)
    result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
    worker_record = {
        "run_id": worker_run_id,
        "provider": request.provider,
        "agent_id": agent["id"],
        "logical_agent_id": logical_agent_id,
        "dispatch_slot_id": dispatch_slot_id or None,
        "dispatch_slot": request.metadata.get("dispatch_slot"),
        "account": provider_account_id(config, request.provider),
        "task_id": request.task_id,
        "task_generation": request.metadata.get("task_generation"),
        "session_id": result.session_id,
        "mode": result.mode,
        "status": "running",
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
        "workspace_repository_id": request.metadata.get("workspace_repository_id"),
        "workspace_source_root": request.metadata.get("workspace_source_root"),
        "workspace_base_ref": request.metadata.get("workspace_base_ref"),
        "work_progress_snapshot": initial_work_progress_snapshot,
        "last_commit_progress_at": None,
        "last_work_progress_at": None,
        "commit_progress_count": 0,
        "status_root": request.metadata.get("status_root"),
        "status_command_runtime": issued_command_runtime,
        "pid": result_pid,
        "pid_start_ticks": result_pid_start_ticks,
        "process_generation": result_process_generation,
        "heartbeat_path": result_metadata.get("heartbeat_path"),
        "runner_status_path": result_metadata.get("runner_status_path"),
        "notes": result.notes,
        "metadata": result_metadata,
        "request_snapshot": request_snapshot(request),
        "parent_run_id": parent_run_id,
        # A retry is represented by a new worker record.  Carry the attempt
        # number into that child instead of resetting its retry budget to zero;
        # otherwise a process that repeatedly disappears can retry forever.
        "retry_count": max(0, int(attempt_count) - 1),
        "next_retry_at": None,
        "last_error": None,
    }
    state.setdefault("workers", {})[worker_run_id] = worker_record
    if queue_event_id:
        q_record = queue_status(state, queue_event_id)
        w_status = str(worker_record.get("status") or "running")
        desired_status = "waiting_approval" if w_status == "waiting_approval" else "started"
        q_record["status"] = desired_status
        q_record["run_id"] = worker_run_id
        q_record["lease_owner"] = worker_run_id
        q_record["lease_acquired_at"] = worker_record.get("lease_acquired_at") or utc_now()
        q_record["lease_expires_at"] = worker_record.get("lease_expires_at") or queue_lease_expiry(config)
        q_record["processed_at"] = q_record.get("processed_at") or utc_now()
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
            "pid": result_pid,
            "pid_start_ticks": result_pid_start_ticks,
            "process_generation": result_process_generation,
            "task_id": request.task_id,
            "agent_id": agent["id"],
            "provider": request.provider,
            "lease_expires_at": state["workers"][worker_run_id].get("lease_expires_at"),
        },
        emit_activity=False,
    )
    # Direct callers retain the immediate whole-state save. Reserved slow
    # phases instead publish an exact-token launch receipt into their durable
    # reservation. A restart can adopt that receipt (or the runner marker tied
    # to the pre-launch intent) without bypassing the phase's whole-state CAS.
    if _RUNTIME_PHASE_RESERVATION.get() is None:
        save_runtime_state(config, state)
    else:
        _persist_runtime_phase_launch_receipt(config, state, worker_record)
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
            "pid": result_pid,
            "pid_start_ticks": result_pid_start_ticks,
            "process_generation": result_process_generation,
            "parent_run_id": parent_run_id,
            "command": result.command,
            "log_path": result.log_path,
            "payload_path": result.payload_path,
            "workspace_mode": request.metadata.get("workspace_mode"),
            "workspace_path": request.metadata.get("workspace_path"),
            "workspace_branch": request.metadata.get("workspace_branch"),
            "workspace_repository_id": request.metadata.get("workspace_repository_id"),
            "workspace_source_root": request.metadata.get("workspace_source_root"),
            "workspace_base_ref": request.metadata.get("workspace_base_ref"),
            "status_root": request.metadata.get("status_root"),
        },
    )
    return True, worker_run_id, result.as_dict()


def process_queue(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    delivery_outcome: dict[str, bool] | None = None,
    health_refresh_demand: list[dict[str, str]] | None = None,
) -> bool:
    """Reconcile queued intents and launch at most one worker process.

    A runtime-phase reservation carries one crash-recoverable launch intent and
    receipt.  Launching a second process in the same reservation would replace
    that evidence and orphan the first process after a hard crash.

    ``health_refresh_demand``, when supplied, collects the exact endpoints a
    pending intent is waiting on so the caller can fold them into the same
    cycle's live probe pass. Before this parameter existed, a pending intent
    only recorded ``last_wait_reason``/``last_health_refresh_requested_at`` as
    bookkeeping timestamps and dropped the actual endpoint identifiers on the
    floor -- nothing ever re-probed them, so an intent stuck waiting on a
    lane whose cached health had merely gone stale (not a durable failure)
    could wait forever. Diagnosed 2026-08-17 on AGORA-HOSTED-SERVICE-PROOF-20260815.
    """
    if delivery_outcome is not None:
        delivery_outcome["launched"] = False
    if not bool(ready_dispatch_settings(config).get("enabled", False)):
        return False
    changed = False
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    for event in queue_events(state):
        event_id = event.get("event_id")
        if not event_id:
            continue
        existing_record = state.get("queue", {}).get("events", {}).get(event_id, {})
        record = queue_status(state, event_id)
        event_key = str(event.get("event_key") or "")
        if event_key and record.get("event_key") != event_key:
            record["event_key"] = event_key
            changed = True
        if record.get("status") in {"completed", "failed"}:
            continue
        if record.get("status") in {"started", "waiting_approval"} and record.get("lease_owner") and record.get("run_id"):
            continue
        if record.get("status") == "retry_backoff":
            next_retry_at = _parse_iso_utc(str(record.get("next_retry_at") or ""))
            if next_retry_at is not None and next_retry_at > datetime.now(timezone.utc):
                continue
        if not is_execution_dispatch_reason(str(event.get("reason") or "")):
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "unsupported_dispatch_reason"
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": event.get("task_id"),
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "message": (
                        "Rejected a noncanonical delivery intent. Supervisor V2 "
                        "launches only intents emitted by the shared task planner."
                    ),
                    "queue_event_id": event_id,
                    "dispatch_reason": event.get("reason"),
                },
            )
            changed = True
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
            desired_status = "waiting_approval" if active_worker.get("status") == "waiting_approval" else "started"
            active_run_id = active_worker.get("run_id") or event_id
            if (
                record.get("status") != desired_status
                or record.get("run_id") != active_run_id
                or record.get("lease_owner") != active_run_id
            ):
                record["status"] = desired_status
                record["run_id"] = active_run_id
                record["lease_owner"] = active_run_id
                record["lease_acquired_at"] = record.get("lease_acquired_at") or active_worker.get("lease_acquired_at") or utc_now()
                record["lease_expires_at"] = active_worker.get("lease_expires_at") or queue_lease_expiry(config)
                record["processed_at"] = record.get("processed_at") or utc_now()
                sync_dispatched_task_status(
                    config,
                    event,
                    run_id=record["run_id"],
                    workspace_path=(
                        active_worker.get("workspace_path")
                        or (
                            (active_worker.get("request_snapshot") or {})
                            .get("metadata", {})
                            .get("workspace_path")
                        )
                        or config_path(config, "status_file").parent
                    ),
                )
                changed = True
            continue
        task_id = str(event.get("task_id") or "").strip()
        active_task_worker = next(
            (
                worker
                for worker in state.get("workers", {}).values()
                if task_id
                and str(worker.get("task_id") or "").strip() == task_id
                and worker.get("status") in active_statuses
            ),
            None,
        )
        if active_task_worker:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "task_already_active"
            record["active_run_id"] = active_task_worker.get("run_id")
            write_activity_log(
                config,
                {
                    "type": "wake_skipped",
                    "task_id": task_id,
                    "target_agent": event.get("target_display_name") or event.get("target_agent"),
                    "message": (
                        f"Skipped duplicate wake event for {task_id}: "
                        f"active worker {active_task_worker.get('run_id') or 'unknown'} "
                        "already owns the task lease."
                    ),
                    "queue_event_id": event_id,
                    "worker_run_id": active_task_worker.get("run_id"),
                },
            )
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
        endpoint_id = str(event.get("delivery_endpoint_id") or "").strip()
        if not endpoint_id:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "retired_intent_without_exact_endpoint"
            changed = True
            continue
        decision = evaluate_queued_delivery_admission(
            config,
            state,
            event,
            task_map,
            queue_events(state),
        )
        if decision is None:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "delivery_intent_schema_or_task_changed"
            changed = True
            continue
        if not decision.eligible:
            reason_code = decision.reason.value if decision.reason is not None else "delivery_eligibility_changed"
            if reason_code in {
                "task_not_dispatchable",
                "task_leased",
                "task_pending",
                "human_hold",
                "no_delivery_endpoint",
            }:
                record["status"] = "completed"
                record["processed_at"] = utc_now()
                record["skip_reason"] = reason_code
            else:
                record["status"] = "pending"
                record["last_wait_reason"] = reason_code
                if decision.needs_health_refresh:
                    record["last_health_refresh_requested_at"] = utc_now()
                    if health_refresh_demand is not None:
                        for target in decision.health_refresh_targets:
                            entry = {"scope": target.scope.value, "id": target.identifier}
                            if entry not in health_refresh_demand:
                                health_refresh_demand.append(entry)
            changed = True
            continue
        request = build_request(config, event, agent_id_override=endpoint_id)
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
        latest_task_map = task_index_from_status(config, load_status(config))
        late_skip_message = stale_dispatch_skip_message(
            config,
            event,
            latest_task_map,
        )
        if late_skip_message:
            record["status"] = "completed"
            record["processed_at"] = utc_now()
            record["skip_reason"] = "task_generation_changed_before_launch"
            record["error"] = late_skip_message
            changed = True
            continue
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["last_attempt_at"] = utc_now()
        ok, worker_outcome, delivery = start_worker_for_request(
            config,
            state,
            request,
            queue_event_id=event_id,
            attempt_count=record["attempt_count"],
            event_id_for_log=event_id,
        )
        if not ok:
            failure_run_id = (
                f"{event_id}-attempt-{max(1, int(record.get('attempt_count', 0)))}"
            )
            failure_worker = {
                "provider": request.provider,
                "agent_id": request.agent_id,
                "task_id": request.task_id,
                "queue_event_id": event_id,
                "run_id": failure_run_id,
                "retry_count": max(0, int(record.get("attempt_count", 0)) - 1),
                "request_snapshot": request_snapshot(request),
                "work_progress_snapshot": worker_commit_progress_snapshot(
                    {
                        "workspace_mode": request.metadata.get("workspace_mode"),
                        "workspace_path": request.metadata.get("workspace_path"),
                    }
                ),
            }
            failure_reason = str(worker_outcome or "")
            failure = classify_worker_failure(config, failure_worker, failure_reason)
            failure_summary = summarize_failure_reason(failure_reason, request.provider)
            raw_ref = write_failure_evidence(
                config,
                worker=failure_worker,
                reason=failure_reason,
                failure_kind=str(failure.get("kind") or ""),
            )
            failure_kind = str(failure.get("kind") or "")
            rotation_outcome = maybe_rotate_provider_model(
                config, state, request.provider, failure_kind, failure_reason
            )
            if rotation_outcome == "rotated":
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
                            f"Model rotation triggered for {request.provider} "
                            f"({failure.get('label')}); re-dispatching on the alternate model "
                            f"at {record.get('next_retry_at')}: "
                            f"{failure_summary.get('summary') or failure_reason}"
                        ),
                        "next_retry_at": record.get("next_retry_at"),
                        "raw_ref": raw_ref,
                    },
                )
                changed = True
                continue
            record_delivery_health_failure(
                config,
                state,
                agent_id=request.agent_id,
                failure_kind=failure_kind,
                detail=failure_summary.get("summary") or failure_reason,
            )
            if (
                failure_kind in {"transient", "capacity", "capacity_retryable"}
            ):
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
            record["status"] = "failed"
            record["error"] = failure_summary.get("summary") or worker_outcome
            if raw_ref:
                record["raw_ref"] = raw_ref
            record["processed_at"] = utc_now()
            changed = True
            continue

        worker_run_id = worker_outcome or event_id
        queue_started_at = datetime.now(timezone.utc)
        record["status"] = "started"
        record["run_id"] = worker_run_id
        record["lease_owner"] = worker_run_id
        record["lease_acquired_at"] = _isoformat_utc(queue_started_at)
        record["lease_expires_at"] = queue_lease_expiry(config, queue_started_at)
        record["processed_at"] = _isoformat_utc(queue_started_at)
        record.pop("last_wait_reason", None)
        sync_dispatched_task_status(
            config,
            event,
            run_id=worker_run_id,
            workspace_path=workspace_path or config_path(config, "status_file").parent,
        )
        changed = True
        if delivery_outcome is not None:
            delivery_outcome["launched"] = True
        break
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


def worker_pid_start_ticks(pid: int | None, proc_root: Path | None = None) -> int | None:
    """Return Linux's immutable process start-time token for PID reuse checks."""

    if not pid:
        return None
    root = proc_root if proc_root is not None else Path("/proc")
    try:
        raw_stat = (root / str(pid) / "stat").read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return None
    closing_paren = raw_stat.rfind(")")
    if closing_paren < 0:
        return None
    fields = raw_stat[closing_paren + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except (TypeError, ValueError):
        return None


def worker_process_identity(worker: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a validated immutable worker/process generation binding."""

    task_id = str(worker.get("task_id") or "").strip()
    worker_run_id = str(worker.get("run_id") or "").strip()
    queue_event_id = str(worker.get("queue_event_id") or "").strip()
    process_generation = str(worker.get("process_generation") or "").strip()
    pid = worker.get("pid")
    pid_start_ticks = worker.get("pid_start_ticks")
    if (
        not task_id
        or not worker_run_id
        or not queue_event_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(pid_start_ticks, int)
        or isinstance(pid_start_ticks, bool)
        or pid_start_ticks <= 0
    ):
        return None
    expected_generation = worker_process_generation_id(
        task_id=task_id,
        worker_run_id=worker_run_id,
        queue_event_id=queue_event_id,
        pid=pid,
        pid_start_ticks=pid_start_ticks,
    )
    if process_generation != expected_generation:
        return None
    return {
        "schema_version": WORKER_PROCESS_GENERATION_SCHEMA_VERSION,
        "task_id": task_id,
        "worker_run_id": worker_run_id,
        "queue_event_id": queue_event_id,
        "pid": pid,
        "pid_start_ticks": pid_start_ticks,
        "process_generation": process_generation,
    }


def _proc_activity_record(pid: int, proc_root: Path) -> dict[str, Any] | None:
    stat_path = proc_root / str(pid) / "stat"
    try:
        raw_stat = stat_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    closing_paren = raw_stat.rfind(")")
    if closing_paren < 0:
        return None
    fields = raw_stat[closing_paren + 2 :].split()
    if len(fields) < 20 or fields[0] == "Z":
        return None
    try:
        cpu_ticks = int(fields[11]) + int(fields[12])
        start_ticks = int(fields[19])
    except (TypeError, ValueError):
        return None

    io_bytes = 0
    try:
        for line in (proc_root / str(pid) / "io").read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, value = line.partition(":")
            if key in {"read_bytes", "write_bytes"}:
                io_bytes += int(value.strip())
    except (OSError, ValueError):
        pass

    try:
        command = (proc_root / str(pid) / "comm").read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        command = ""
    return {
        "pid_token": f"{pid}:{start_ticks}",
        "cpu_ticks": cpu_ticks,
        "io_bytes": io_bytes,
        "command": command,
    }


def worker_process_activity_snapshot(pid: int | None, proc_root: Path | None = None) -> dict[str, Any]:
    """Summarize measurable activity below the worker runner process."""
    if not pid:
        return {"processes": [], "cpu_ticks": 0, "io_bytes": 0, "commands": []}
    root = proc_root if proc_root is not None else Path("/proc")
    pending = [int(pid)]
    visited: set[int] = set()
    records: list[dict[str, Any]] = []
    while pending:
        parent = pending.pop()
        if parent in visited:
            continue
        visited.add(parent)
        children_path = root / str(parent) / "task" / str(parent) / "children"
        try:
            children = [int(value) for value in children_path.read_text(encoding="utf-8").split()]
        except (OSError, ValueError):
            children = []
        for child in children:
            if child in visited:
                continue
            pending.append(child)
            record = _proc_activity_record(child, root)
            if record is not None:
                records.append(record)
    return {
        "processes": sorted(record["pid_token"] for record in records),
        "cpu_ticks": sum(int(record["cpu_ticks"]) for record in records),
        "io_bytes": sum(int(record["io_bytes"]) for record in records),
        "commands": sorted({str(record["command"]) for record in records if record["command"]}),
    }


def worker_process_activity_advanced(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not previous or not current.get("processes"):
        return False
    return bool(
        int(current.get("cpu_ticks") or 0) > int(previous.get("cpu_ticks") or 0)
        or int(current.get("io_bytes") or 0) > int(previous.get("io_bytes") or 0)
        or list(current.get("processes") or []) != list(previous.get("processes") or [])
    )


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
        # Each worker run spawns ~3 processes carrying the same wakeword prompt
        # (worker_runner.py wrapper, the CLI shim, and the CLI binary). Only the
        # worker_runner.py wrapper is exactly one-per-worker, so count it alone;
        # otherwise the live worker count is ~3x inflated and max_concurrent_workers
        # freezes dispatch at ~1/3 of its configured value (OPS-DISPATCH-PIDCOUNT-001).
        if "worker_runner.py" not in cmdline:
            continue
        agent = match.group(1)
        result.setdefault(agent, []).append(pid)
    return result


def terminate_worker_pid(
    pid: int | None,
    *,
    expected_start_ticks: int | None = None,
) -> bool:
    if not pid:
        return False
    if (
        expected_start_ticks is not None
        and worker_pid_start_ticks(pid) != expected_start_ticks
    ):
        return False

    def identity_bound_is_alive(candidate_pid: int) -> bool:
        if not pid_is_alive(candidate_pid):
            return False
        return (
            expected_start_ticks is None
            or worker_pid_start_ticks(candidate_pid) == expected_start_ticks
        )

    deferred = _DEFERRED_WORKER_TERMINATIONS.get()
    if deferred is not None:
        # A terminal state must never be published until the process is
        # positively gone. Queue the identity-bound termination for immediately
        # after runtime admission instead of sending TERM and reporting success
        # while the worker can still mutate state.
        if any(item[0] == pid for item in deferred):
            return False
        if not identity_bound_is_alive(pid):
            return True
        start_ticks = expected_start_ticks or worker_pid_start_ticks(pid)
        if start_ticks is None:
            # Without Linux's immutable process-start token, a reused PID is
            # indistinguishable from the intended worker. Fail closed: do not
            # signal it and do not let the caller publish a terminal outcome.
            return False
        deferred.append((pid, start_ticks))
        return False
    # Confirmed termination is the sole lifecycle operation.  Do not publish a
    # terminal worker record until the exact PID generation is gone.
    try:
        return rewrite_worker_lifecycle.confirm_kill(
            pid,
            is_alive=identity_bound_is_alive,
            send_signal=os.kill,
            sleep=time.sleep,
            monotonic=time.monotonic,
        )
    except Exception:
        return False


def terminate_worker_process_generation(worker: Mapping[str, Any]) -> bool:
    """Signal only the exact process generation captured when this run started."""

    identity = worker_process_identity(worker)
    if identity is None:
        return False
    if worker_pid_start_ticks(identity["pid"]) != identity["pid_start_ticks"]:
        return False
    return terminate_worker_pid(
        identity["pid"],
        expected_start_ticks=identity["pid_start_ticks"],
    )


def worker_process_generation_is_current(worker: Mapping[str, Any]) -> bool:
    identity = worker_process_identity(worker)
    return bool(
        identity is not None
        and worker_pid_start_ticks(identity["pid"]) == identity["pid_start_ticks"]
    )


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


def canonical_agent_name(config: dict[str, Any], value: str | None) -> str:
    # scripts/ai_status.py has a separate, older canonical_agent_name(name)
    # that resolves against a hardcoded KNOWN_AGENTS/AGENT_ALIASES table
    # instead of live config -- it predates this config-driven version and
    # is missing worker-slot ids (e.g. codex1_1) present here. Investigated
    # 2026-08-17: no evidence in ai-status.json/activity-log/task-archive of
    # this ever causing a real mismatch (owner/reviewer values observed in
    # practice are always pre-normalized display names), so left as a known,
    # low-risk divergence rather than unified across ~60 call sites with no
    # existing test coverage.
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
                worker["pr_url_source"] = "result_payload"
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
                worker["pr_url_source"] = "log_scrape"
                break
    worker["pr_url"] = normalize_pr_url(config, worker.get("pr_url"))
    if not worker.get("session_url"):
        for url in URL_PATTERN.findall(content):
            if "/agent" in url or "/sessions/" in url:
                worker["session_url"] = url
                break


def worker_was_terminated_by_sigterm(worker: dict[str, Any]) -> bool:
    """Return True if worker was reaped/terminated by SIGTERM or SIGKILL signal/exit code."""
    sig = worker.get("runner_signal")
    if sig in {9, 15, "9", "15", "SIGKILL", "SIGTERM"}:
        return True
    try:
        exit_code = int(worker.get("exit_code", 0))
    except (TypeError, ValueError):
        exit_code = 0
    if exit_code in {137, 143}:
        return True
    return False


def worker_has_authoritative_runner_failure(worker: dict[str, Any]) -> bool:
    """Return whether worker_runner published a terminal failure marker.

    The provider transcript is mixed-trust content: prompts, tool output,
    source snippets, and provider stderr all share one log.  A regex match in
    that file is therefore not evidence that the provider failed.  The runner
    status file is the authority for plain-text CLIs and is copied onto the
    worker record by ``update_worker_runtime_markers``.
    """

    status = str(worker.get("runner_status") or "").strip().lower()
    if status not in RUNNER_FAILURE_STATUSES:
        return False
    return not worker_was_terminated_by_sigterm(worker)


def worker_uses_structured_provider_stream(worker: dict[str, Any]) -> bool:
    """Return whether top-level JSON lines are provenanced provider stream envelopes.

    Requires explicit stream_json command flag, provider stream configuration, or known
    structured-stream CLI modes when stream_json or structured_stream is explicitly enabled
    (or when command flags like --output-format stream-json or --json are present).
    """

    if worker.get("stream_json") is False or worker.get("structured_stream") is False:
        return False
    command = worker.get("command")
    if isinstance(command, list):
        normalized = [str(part).strip().lower() for part in command]
        if ("--output-format" in normalized and "stream-json" in normalized) or "--json" in normalized:
            return True
    if bool(worker.get("stream_json") or worker.get("structured_stream") or worker.get("provider_uses_stream_json")):
        return True
    mode = str(worker.get("mode") or "").strip().lower()
    if mode in {"claude_cli", "stream_json"}:
        return True
    return False


def is_authoritative_provider_failure_envelope(
    worker: dict[str, Any], payload: dict[str, Any]
) -> bool:
    """Recognize terminal control envelopes, never arbitrary JSON content."""

    if not worker_uses_structured_provider_stream(worker):
        return False
    message = payload.get("message")
    role = message.get("role") if isinstance(message, dict) else None
    if payload.get("type") == "user" or role == "user":
        return False

    info = rate_limit_info_payload(payload)
    if info is not None:
        status = str(info.get("status") or "").strip().lower()
        return status in PROVIDER_STREAM_FAILURE_STATUSES

    payload_type = str(payload.get("type") or "").strip().lower()
    payload_status = str(payload.get("status") or "").strip().lower()
    payload_subtype = str(payload.get("subtype") or "").strip().lower()
    if payload_type in PROVIDER_STREAM_FAILURE_TYPES:
        return True
    if payload_type != "result":
        return False
    return bool(
        payload.get("is_error") is True
        or payload.get("error") not in (None, "", {}, [])
        or payload_status in PROVIDER_STREAM_FAILURE_STATUSES
        or payload_subtype in PROVIDER_STREAM_FAILURE_SUBTYPES
    )


def is_runner_gated_provider_error_envelope(
    worker: dict[str, Any], payload: dict[str, Any]
) -> bool:
    """Recognize a retry/error control event after the runner failed.

    Claude and Qwen can emit a structured retry record followed by ordinary
    assistant text.  The assistant text is not authoritative, but the control
    record is once the runner independently reports terminal failure.
    """

    if not worker_uses_structured_provider_stream(worker):
        return False
    payload_type = str(payload.get("type") or "").strip().lower()
    payload_subtype = str(payload.get("subtype") or "").strip().lower()
    return bool(
        payload_type == "system"
        and payload_subtype in {"api_error", "api_retry"}
        and (
            payload.get("error") not in (None, "", {}, [])
            or payload.get("error_status") not in (None, "")
        )
    )


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

    runner_failed = worker_has_authoritative_runner_failure(worker)
    is_sigterm = worker_was_terminated_by_sigterm(worker)
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
            if is_authoritative_provider_failure_envelope(worker, stream_payload):
                return stripped
            if runner_failed and not is_sigterm and is_runner_gated_provider_error_envelope(worker, stream_payload):
                return stripped
            if is_captured_orchestrator_record(stream_payload):
                continue
            if is_allowed_rate_limit_event(stream_payload):
                continue
            message = stream_payload.get("message")
            role = message.get("role") if isinstance(message, dict) else None
            if stream_payload.get("type") == "user" or role == "user":
                continue
            # A top-level JSON object which is not one of the provider control
            # envelopes above is transcript content. Do not inspect its nested strings.
            continue
    return None


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


def rate_limit_info_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the rate-limit envelope carried by one worker stream record."""
    if not isinstance(payload, dict):
        return None
    candidates: list[dict[str, Any]] = [payload]
    message = payload.get("message")
    if isinstance(message, dict):
        candidates.append(message)
    for candidate in candidates:
        if str(candidate.get("type") or "") != "rate_limit_event":
            continue
        info = candidate.get("rate_limit_info")
        if isinstance(info, dict):
            return info
    return None


def is_allowed_rate_limit_event(payload: dict[str, Any]) -> bool:
    """True for a quota *notice* the CLI emits while still serving the request.

    The Claude CLI reports quota headroom as ``rate_limit_event`` records whose
    status is ``allowed`` (below threshold) or ``allowed_warning`` (past the
    warning threshold but still served); only ``rejected`` means the request was
    actually throttled. Accepting just ``allowed`` made a live
    ``allowed_warning`` notice match ``WORKER_FAILURE_PATTERNS``, so a healthy
    worker was recorded as failed, classified ``terminal``, and its task
    reassigned away from an owner that never failed.
    """
    info = rate_limit_info_payload(payload)
    if info is None:
        return False
    return str(info.get("status") or "").strip().lower() in NONTHROTTLING_RATE_LIMIT_STATUSES


def is_allowed_rate_limit_line(line: str) -> bool:
    """Raw-line fallback for a nonthrottling rate-limit notice.

    Worker logs can carry a truncated or wrapped stream record that no longer
    parses as JSON. The failure regexes still match its ``rate_limit_event``
    substring, so the same nonterminal notice must be recognised textually.
    """
    if not RATE_LIMIT_EVENT_LINE_PATTERN.search(line):
        return False
    return bool(NONTHROTTLING_RATE_LIMIT_LINE_PATTERN.search(line))


def classify_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> dict[str, Any] | None:
    if not reason:
        return None
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
        "quota_exceeded",
        "quota_reached",
        "exceeded your monthly quota",
        "individual quota reached",
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

    # A nonthrottling rate-limit notice can still reach a stored last_error via an
    # older worker record or an operator-supplied reason. It never denotes a
    # failed request, so it must stay nonterminal and redispatchable rather than
    # pausing the provider or reassigning the task.
    if is_allowed_rate_limit_line(str(reason or "")):
        return {"kind": "transient", "transient": True, "label": "allowed rate-limit notice"}
    if is_github_cli_auth_failure(reason):
        return {"kind": "tool_auth", "transient": False, "label": "tool auth"}
    if re.search(r"\b(?:you(?:'ve| have)\s+)?hit your(?:\s+\w+){0,3}\s+limit\b", normalized):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if any(marker in normalized for marker in terminal_quota_markers):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if any(marker in normalized for marker in retryable_capacity_markers):
        return {"kind": "capacity_retryable", "transient": True, "label": "capacity/429"}
    if any(marker in normalized for marker in auth_markers):
        return {"kind": "auth", "transient": False, "label": "auth"}
    if provider.startswith("gemini") and any(marker in normalized for marker in unknown_critical_markers):
        return {"kind": "unknown_critical", "transient": False, "label": "unknown critical error"}
    if any(pattern in normalized for pattern in transient_patterns):
        return {"kind": "transient", "transient": True, "label": "transient"}
    if any(marker in normalized for marker in unknown_critical_markers):
        return {"kind": "unknown_critical", "transient": False, "label": "unknown critical error"}
    return {"kind": "terminal", "transient": False, "label": "terminal"}


_parse_iso_utc = parse_runtime_timestamp


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
    settings.setdefault(
        "work_progress_stale_seconds",
        supervisor_settings.get(
            "work_progress_stale_seconds",
            int(settings.get("heartbeat_stale_seconds", 300))
            + int(settings.get("heartbeat_grace_seconds", 60)),
        ),
    )
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


def worker_lease_requires_work_progress(config: dict[str, Any]) -> bool:
    supervisor_settings = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    return bool(supervisor_settings.get("lease_requires_work_progress", True))


def worker_lease_progress_is_fresh(
    config: dict[str, Any],
    worker: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    settings = worker_runtime_settings(config)
    now_dt = now or datetime.now(timezone.utc)
    # A worker legitimately blocked on an unresolved tool-use approval has no
    # observable progress signal by design for as long as the approval stays
    # open (up to the provider's approval_wait_seconds, commonly much longer
    # than work_progress_stale_seconds). Reclaiming its lease as "stuck" here
    # kills a healthy, correctly-waiting process and surfaces as "Approval
    # state disappeared before the worker could resume" on the next
    # reconciliation tick. Heartbeat freshness (checked separately by the
    # caller) still catches a genuinely hung process in this state; only the
    # work-progress dimension is exempted.
    if str(worker.get("status") or "") in {"waiting_approval", "suspended_approval"}:
        return True
    progress_candidates = [
        dt
        for dt in (
            _parse_iso_utc(
                str(
                    worker.get("last_work_progress_at")
                    or worker.get("last_process_activity_at")
                    or ""
                )
            ),
            _parse_iso_utc(str(worker.get("last_event_at") or "")),
        )
        if dt is not None
    ]
    latest_progress = max(progress_candidates, default=None)
    return rewrite_worker_lifecycle.lease_progress_is_fresh(
        last_progress_epoch=(latest_progress.timestamp() if latest_progress else None),
        now_epoch=now_dt.timestamp(),
        stall_seconds=float(settings.get("work_progress_stale_seconds", 360)),
    )


def worker_lease_can_renew(
    config: dict[str, Any],
    worker: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    now_dt = now or datetime.now(timezone.utc)
    if worker_heartbeat_is_stale(config, worker, now_dt):
        return False
    return not worker_lease_requires_work_progress(config) or worker_lease_progress_is_fresh(
        config,
        worker,
        now_dt,
    )


def worker_lease_is_expired(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> bool:
    lease_expires_at = _parse_iso_utc(str(worker.get("lease_expires_at") or ""))
    if lease_expires_at is None:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return now_dt > lease_expires_at.astimezone(timezone.utc) and not worker_lease_can_renew(
        config,
        worker,
        now_dt,
    )


def worker_lease_status_description(config: dict[str, Any], worker: dict[str, Any], now: datetime | None = None) -> str:
    """Return explicit lease state description: healthy_long_finalize, healthy_running, stuck_lease, or expired."""
    now_dt = now or datetime.now(timezone.utc)
    req_snap = worker.get("request_snapshot")
    reason = str((req_snap if isinstance(req_snap, dict) else {}).get("reason") or "")
    is_finalize_or_review = reason in {"owned_finalize_dispatch", "review_ready_dispatch"}
    if worker_lease_is_expired(config, worker, now_dt):
        return "expired"
    if worker_heartbeat_is_stale(config, worker, now_dt) or (
        worker_lease_requires_work_progress(config) and not worker_lease_progress_is_fresh(config, worker, now_dt)
    ):
        return "stuck_lease"
    if is_finalize_or_review:
        return "healthy_long_finalize" if reason == "owned_finalize_dispatch" else "healthy_long_review"
    return "healthy_running"



def normalize_runtime_delivery_health(state: dict[str, Any]) -> bool:
    """Ensure the V2 delivery-health snapshot has its canonical shape."""

    changed = False
    normalized = runtime_delivery_health(state)
    if state.get("delivery_health") != normalized:
        state["delivery_health"] = normalized
        changed = True
    return changed




def is_terminal_quota_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "quota_terminal"


def maybe_rotate_provider_model(
    config: dict[str, Any],
    state: dict[str, Any],
    provider: str | None,
    failure_kind: str | None,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Rotate a model-rotation provider off its exhausted model instead of pausing.

    Returns one of:
      - ``"disabled"``   -> provider has no model rotation; use the normal path.
      - ``"ineligible"`` -> failure is not a model-exhaustion kind (e.g. auth); normal path.
      - ``"rotated"``    -> the exhausted model was cooled and another model is still
                            available; the caller should re-dispatch on the SAME provider
                            (the adapter will pick the alternate model) and skip the
                            dispatch pause / cross-provider reassignment.
      - ``"exhausted"``  -> every rotation model is now cooling; the caller should apply
                            the normal dispatch pause / reassignment.
    """
    provider_id = normalize_agent_id(provider or "")
    if not provider_id or not model_rotation.rotation_enabled(config, provider_id):
        return "disabled"
    if str(failure_kind or "").strip().lower() not in model_rotation.ROTATION_ELIGIBLE_FAILURE_KINDS:
        return "ineligible"
    now = now or datetime.now(timezone.utc)
    slot = model_rotation.active_slot(config, provider_id, now=now)
    if slot is None:
        return "exhausted"
    cooled_until = model_rotation.cool_slot(config, provider_id, slot, now=now)
    next_slot = model_rotation.active_slot(config, provider_id, now=now)
    outcome = "rotated" if next_slot is not None else "exhausted"
    write_activity_log(
        config,
        {
            "type": "provider_model_rotated" if outcome == "rotated" else "provider_model_exhausted",
            "provider": provider_id,
            "cooled_slot": slot,
            "cooled_until": cooled_until,
            "next_slot": next_slot,
            "message": (
                f"Rotated {provider_id} off its {slot} model until {cooled_until}; "
                f"switching to the {next_slot} model."
                if outcome == "rotated"
                else (
                    f"All rotation models for {provider_id} are cooling "
                    f"(last cooled {slot} until {cooled_until}); pausing dispatch."
                )
            ),
        },
    )
    return outcome


def record_delivery_health_for_reaped_worker(
    config: dict[str, Any], state: dict[str, Any], worker: dict[str, Any]
) -> str | None:
    """Preserve a detected terminal failure from a reaped worker log.

    Lease reaping is only evidence collection.  It feeds the same delivery
    health document as an ordinary worker exit; it does not own a second
    provider-pause or reassignment path.
    """
    if worker_was_terminated_by_sigterm(worker):
        return None
    detected_reason = detect_worker_failure(worker)
    if not detected_reason:
        return None
    pause_kind = str(
        classify_worker_failure(config, worker, detected_reason).get("kind") or ""
    )
    if pause_kind not in {"auth", "quota_terminal", "capacity", "capacity_retryable"}:
        return None
    write_failure_evidence(
        config, worker=worker, reason=detected_reason, failure_kind=pause_kind
    )
    record_delivery_health_failure(
        config,
        state,
        agent_id=str(worker.get("agent_id") or ""),
        failure_kind=pause_kind,
        detail=detected_reason,
    )
    return detected_reason


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
    return retry


def worker_reassignment_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("worker_reassignment", {}) or {})
    settings.setdefault("enabled", False)
    settings.setdefault("max_reassignments_per_cycle", 4)
    settings.setdefault("owner_fallbacks", {})
    settings.setdefault("reviewer_fallbacks", {})
    return settings


def load_balance_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only policy for saturated- or transiently-blocked-lane reassignment.

    Off by default. Every other automatic owner reassignment in this file
    fires only when the incumbent is durably unavailable (see
    ``assignment_terminal_unavailability``); it never fires because the
    incumbent's lane is simply full while healthy, or merely between health
    probes. That leaves either a full, busy, perfectly healthy lane, or one
    stuck on a stale/expired probe with zero active workers, owning
    not-yet-started work while a less-loaded fallback sits idle -- see
    ``assignment_saturated_recoverable`` and
    ``assignment_transiently_blocked_recoverable``, which this settles.
    ``min_saturated_seconds`` gates both signals identically: most transient
    blocks self-heal within one probe cycle, so acting on either signal too
    quickly would churn ownership for something that was about to recover.
    """

    raw = config.get("worker_reassignment")
    raw = raw.get("load_balance") if isinstance(raw, Mapping) else None
    raw = raw if isinstance(raw, Mapping) else {}

    def non_negative(name: str, default: int) -> int:
        try:
            return max(0, int(raw.get(name, default)))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(raw.get("enabled", False)),
        "min_saturated_seconds": non_negative("min_saturated_seconds", 900),
    }


def failure_loop_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only policy for the repeated-failure auto-governance loop.

    Off by default. When a task fails ``max_failures_in_window`` times
    within ``window_seconds`` under its current owner, it is auto-reassigned
    to the next configured ``owner_fallbacks`` candidate -- same governed
    pipeline as ``assignment_saturated_recoverable`` -- up to
    ``max_auto_reassignments`` times. If it keeps failing at that rate even
    after exhausting those attempts, it is put on an explicit Human/Ops hold
    instead of being bounced between agents forever: auto-remediation gets a
    bounded number of tries, then escalates rather than spinning silently.
    See ``reconcile_failure_loops``.
    """

    raw = config.get("worker_reassignment")
    raw = raw.get("failure_loop") if isinstance(raw, Mapping) else None
    raw = raw if isinstance(raw, Mapping) else {}

    def positive(name: str, default: int) -> int:
        try:
            return max(1, int(raw.get(name, default)))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(raw.get("enabled", False)),
        "max_failures_in_window": positive("max_failures_in_window", 3),
        "window_seconds": positive("window_seconds", 3600),
        "max_auto_reassignments": positive("max_auto_reassignments", 1),
    }


def normalized_mapping_values(mapping: dict[str, Any], key: str) -> list[str]:
    target = (key or "").strip().casefold()
    for candidate_key, values in mapping.items():
        if str(candidate_key).strip().casefold() != target:
            continue
        return [str(value).strip() for value in list(values or []) if str(value).strip()]
    return []


def known_agent_display_names(config: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        if agent_is_dispatch_slot(agent):
            continue
        display_name = str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        if not display_name or "legacy alias" in display_name.lower():
            continue
        names.add(display_name)
    return names



def sidecar_only_agent_names(config: dict[str, Any]) -> set[str]:
    return {
        str(agent_name).strip()
        for agent_name in ready_dispatch_settings(config).get("sidecar_only_agents", []) or []
        if str(agent_name).strip()
    }


def agent_is_known(config: dict[str, Any], agent_name: str | None) -> bool:
    """True if the name maps to an agent in the roster (display name or id).

    A task owner/reviewer that is not in the roster is a stale historical
    assignment. It can never run, so it must be treated as unable-to-take and
    reassigned.
    """
    name = str(agent_name or "").strip()
    if not name:
        return False
    if name in known_agent_display_names(config):
        return True
    agent_id = normalize_agent_id(name)
    return bool(agent_id and agent_id in (config.get("agents", {}) or {}))


def reassignment_candidate_order(
    config: dict[str, Any],
    mapping: dict[str, Any],
    *,
    roots: list[str],
    exclude: set[str] | None = None,
) -> list[str]:
    """Return only the configured, cycle-safe fallback allow-list.

    Assignment recovery may change responsibility but not task scope.  The
    configured graph therefore defines the complete set of permitted targets;
    a healthy but unrelated roster lane is not an implicit fallback.
    """

    excluded = {
        canonical_agent_name(config, name).casefold()
        for name in (exclude or set())
        if canonical_agent_name(config, name)
    }
    ordered: list[str] = []
    seen: set[str] = set(excluded)
    configured = bounded_fallback_candidates(config, mapping, roots=roots)
    for raw_name in configured:
        name = canonical_agent_name(config, raw_name)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


EXPLICIT_HUMAN_REVIEWER = "Human/Ops"


def reviewer_is_explicit_human_gate(reviewer: str | None) -> bool:
    """Return whether the canonical task explicitly requires Human/Ops review.

    Human/Ops is a governance actor, not an auto-worker lane. Availability
    planning must therefore preserve this reviewer even when ordinary worker
    eligibility checks correctly report that it cannot be dispatched.
    """

    return str(reviewer or "").strip().casefold() == EXPLICIT_HUMAN_REVIEWER.casefold()


def agent_can_take_task(
    config: dict[str, Any],
    agent_name: str | None,
    task: dict[str, Any] | None,
    *,
    state: dict[str, Any] | None = None,
) -> bool:
    name = str(agent_name or "").strip()
    if not name:
        return False
    if agent_dispatch_capacity(config, normalize_agent_id(name)) == 0:
        return False
    if not agent_is_known(config, name):
        return False
    if state is not None:
        health = runtime_delivery_health(state)
        lane = delivery_lane_for_agent(config, normalize_agent_id(name))
        healthy_endpoint = any(
            rewrite_provider_health.endpoint_health_entry(health, endpoint.endpoint_id).get("state")
            == rewrite_provider_health.DeliveryHealthState.HEALTHY.value
            and rewrite_provider_health.account_health_entry(health, endpoint.account_id).get("state")
            == rewrite_provider_health.DeliveryHealthState.HEALTHY.value
            for endpoint in lane.endpoints
        )
        if not healthy_endpoint:
            return False
    if not isinstance(task, dict) or task_is_sidecar(task):
        return True
    return name not in sidecar_only_agent_names(config)


def bounded_fallback_candidates(
    config: dict[str, Any],
    mapping: dict[str, Any],
    *,
    roots: list[str],
) -> list[str]:
    """Return a deterministic, cycle-safe breadth-first fallback order.

    Direct configured fallbacks retain their declared order and every discovered
    candidate may contribute its own configured fallbacks. A case-insensitive
    seen set bounds traversal by the finite configured/roster names even when
    mappings contain cycles such as Codex -> Codex2 -> Codex.
    """

    queue: list[str] = []
    seen: set[str] = set()

    def enqueue(raw_name: str | None) -> None:
        name = canonical_agent_name(config, raw_name)
        key = name.casefold()
        if not name or key in seen:
            return
        seen.add(key)
        queue.append(name)

    for root in roots:
        for candidate in normalized_mapping_values(mapping, root):
            enqueue(candidate)

    ordered: list[str] = []
    cursor = 0
    while cursor < len(queue):
        candidate = queue[cursor]
        cursor += 1
        ordered.append(candidate)
        for child in normalized_mapping_values(mapping, candidate):
            enqueue(child)
    return ordered


def reviewer_fallback_search_order(
    config: dict[str, Any],
    settings: dict[str, Any],
    *,
    reviewer: str,
    owner: str,
    candidate_owner: str,
) -> list[str]:
    """Reviewer fallback chain :func:`plan_task_assignment_pair` walks for an
    owner reassignment, past its fixed seed (the incumbent reviewer).

    Single definition shared with :func:`unavailable_assignment_fallback_refresh_targets`.
    A second, hand-maintained copy of this traversal previously covered only
    owner-side fallbacks when requesting live health refreshes, so a reviewer
    whose cached health had gone stale (no dispatch had touched it recently)
    was never re-probed and silently starved every owner reassignment attempt
    that needed it -- diagnosed 2026-08-17 on AGORA-HOSTED-SERVICE-PROOF-20260815
    after Codex2 hit a durable quota_terminal state: the planner correctly
    found Codex as a viable new owner but could never pair it with a reviewer
    because Claude's (and then Claude2's) health had never been refreshed.
    """

    reviewer_mapping = settings.get("reviewer_fallbacks", {}) or {}
    owner_mapping = settings.get("owner_fallbacks", {}) or {}
    order: list[str] = []
    order.extend(
        bounded_fallback_candidates(
            config,
            reviewer_mapping,
            roots=[name for name in (reviewer, owner, candidate_owner) if name],
        )
    )
    order.extend(
        bounded_fallback_candidates(
            config,
            owner_mapping,
            roots=[name for name in (owner, candidate_owner) if name],
        )
    )
    return order


def plan_task_assignment_pair(
    config: dict[str, Any],
    task: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    fixed_owner: str | None = None,
    owner_candidates: list[str] | None = None,
    preferred_reviewers: list[str] | None = None,
    allowed_reviewers: list[str] | None = None,
    excluded_reviewers: set[str] | None = None,
    preserve_in_progress_incumbents: bool = False,
    require_owner_ready: bool = True,
) -> tuple[str, str] | None:
    """Plan one viable, independent owner/reviewer pair before mutation.

    The search considers a candidate owner together with its reviewer options;
    it never rejects an otherwise viable owner merely because that agent is the
    incumbent reviewer. This is deliberately identity-only independence: it
    does not merge account/quota groups or prohibit Codex/Codex2 mutual review.
    """

    owner = canonical_agent_name(config, str(task.get("owner") or ""))
    reviewer = canonical_agent_name(config, str(task.get("reviewer") or ""))
    explicit_human_reviewer = reviewer_is_explicit_human_gate(reviewer)
    task_status = str(task.get("status") or "").strip().lower()
    allowed_reviewer_keys = (
        {
            canonical_agent_name(config, name).casefold()
            for name in allowed_reviewers
            if canonical_agent_name(config, name)
        }
        if allowed_reviewers is not None
        else None
    )
    excluded_reviewer_keys = {
        canonical_agent_name(config, name).casefold()
        for name in (excluded_reviewers or set())
        if canonical_agent_name(config, name)
    }

    settings = worker_reassignment_settings(config)
    owner_mapping = settings.get("owner_fallbacks", {}) or {}
    if fixed_owner is not None:
        owner_order = [canonical_agent_name(config, fixed_owner)]
    elif owner_candidates is not None:
        owner_order = [canonical_agent_name(config, name) for name in owner_candidates]
    else:
        owner_fallbacks = reassignment_candidate_order(
            config,
            owner_mapping,
            roots=[owner] if owner else [],
            exclude={owner} if owner else set(),
        )
        owner_order = ([owner] if owner else []) + owner_fallbacks

    seen_owners: set[str] = set()
    for candidate_owner in owner_order:
        candidate_owner = canonical_agent_name(config, candidate_owner)
        owner_key = candidate_owner.casefold()
        if not candidate_owner or owner_key in seen_owners:
            continue
        seen_owners.add(owner_key)
        incumbent_owner = fixed_owner is None and owner_key == owner.casefold()
        owner_state = (
            None
            if incumbent_owner and preserve_in_progress_incumbents and task_status == "in_progress"
            else state
        )
        if require_owner_ready and not agent_can_take_task(
            config, candidate_owner, task, state=owner_state
        ):
            continue

        if explicit_human_reviewer:
            # An explicit human gate is not a fallback candidate. Keep it as
            # the only reviewer while owner/helper recovery searches for a
            # viable auto-worker; incompatible constraints fail closed below.
            reviewer_order = [reviewer]
        else:
            reviewer_order = list(preferred_reviewers or ([reviewer] if reviewer else []))
            if preferred_reviewers is None and owner and owner not in reviewer_order:
                reviewer_order.append(owner)
            reviewer_order.extend(
                reviewer_fallback_search_order(
                    config,
                    settings,
                    reviewer=reviewer,
                    owner=owner,
                    candidate_owner=candidate_owner,
                )
            )
        seen_reviewers: set[str] = set()
        for candidate_reviewer in reviewer_order:
            candidate_reviewer = canonical_agent_name(config, candidate_reviewer)
            reviewer_key = candidate_reviewer.casefold()
            if (
                not candidate_reviewer
                or reviewer_key in seen_reviewers
                or reviewer_key == owner_key
                or reviewer_key in excluded_reviewer_keys
                or (
                    allowed_reviewer_keys is not None
                    and reviewer_key not in allowed_reviewer_keys
                )
            ):
                continue
            seen_reviewers.add(reviewer_key)
            incumbent_reviewer = reviewer_key == reviewer.casefold()
            reviewer_state = (
                None
                if incumbent_reviewer and preserve_in_progress_incumbents and task_status == "in_progress"
                else state
            )
            if (
                reviewer_is_explicit_human_gate(candidate_reviewer)
                or agent_can_take_task(
                    config,
                    candidate_reviewer,
                    task,
                    state=reviewer_state,
                )
            ):
                return candidate_owner, candidate_reviewer
    return None


def status_command_subprocess_context(
    config: dict[str, Any],
    *,
    workspace_path: str | Path | None = None,
) -> tuple[Path, dict[str, str]]:
    status_root = config_path(config, "status_file").parent
    issued_env = status_command_runtime_env(config)
    command_root = Path(str(issued_env["PANTHEON_COMMAND_ROOT"])).resolve()
    env = os.environ.copy()
    # A supervisor launched from an auto-worker shell must not accidentally
    # borrow that worker's lease identity for a different dispatch.
    for key in DISPATCH_STATUS_WORKER_ENV_NAMES:
        env.pop(key, None)
    env.update(issued_env)
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    # ``-B`` is local to one interpreter.  Status commands import additional
    # command-root modules and may themselves launch Python children, so bind
    # the inheritable form at this common subprocess boundary.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if workspace_path is not None and str(workspace_path).strip():
        workspace_root = Path(str(workspace_path)).expanduser().resolve()
        env["PANTHEON_WORKTREE_ROOT"] = str(workspace_root)
        env["ORCH_WORKSPACE_PATH"] = str(workspace_root)
    return command_root / "scripts" / "ai_status.py", env


DISPATCH_STATUS_WORKER_ENV_NAMES = (
    "ORCH_RUN_ID",
    "ORCH_TASK_ID",
    "PANTHEON_WORKTREE_ROOT",
    "ORCH_WORKSPACE_PATH",
    "ORCH_RUNNER_STATUS_PATH",
    "ORCH_HEARTBEAT_PATH",
)


def _worker_request_metadata(worker: dict[str, Any]) -> dict[str, Any]:
    snapshot = worker.get("request_snapshot")
    if not isinstance(snapshot, dict):
        return {}
    metadata = snapshot.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _resolve_dispatch_status_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve())


def _runtime_worker_record_for_status_sync(
    config: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    if not run_id or not (config.get("paths", {}) or {}).get("state_file"):
        return {}
    state = load_runtime_state(config)
    workers = state.get("workers")
    if not isinstance(workers, dict):
        return {}
    worker = workers.get(run_id)
    return worker if isinstance(worker, dict) else {}


def _apply_dispatch_status_worker_binding(
    config: dict[str, Any],
    env: dict[str, str],
    *,
    run_id: str,
    task_id: str,
    workspace_path: str | Path | None = None,
) -> None:
    for env_name in DISPATCH_STATUS_WORKER_ENV_NAMES:
        env.pop(env_name, None)

    lease_run_id = str(run_id or "").strip()
    if not lease_run_id:
        return

    env["ORCH_RUN_ID"] = lease_run_id
    env["ORCH_TASK_ID"] = task_id

    workspace_root = _resolve_dispatch_status_path(workspace_path)
    if workspace_root:
        env["PANTHEON_WORKTREE_ROOT"] = workspace_root
        env["ORCH_WORKSPACE_PATH"] = workspace_root

    worker = _runtime_worker_record_for_status_sync(config, lease_run_id)
    if not worker:
        return

    worker_task_id = str(worker.get("task_id") or task_id).strip()
    if worker_task_id:
        env["ORCH_TASK_ID"] = worker_task_id

    request_metadata = _worker_request_metadata(worker)
    if not workspace_root:
        workspace_root = _resolve_dispatch_status_path(
            worker.get("workspace_path") or request_metadata.get("workspace_path")
        )
    if workspace_root:
        env["PANTHEON_WORKTREE_ROOT"] = workspace_root
        env["ORCH_WORKSPACE_PATH"] = workspace_root

    status_root = _resolve_dispatch_status_path(
        worker.get("status_root") or request_metadata.get("status_root")
    )
    if status_root:
        env["PANTHEON_STATUS_ROOT"] = status_root

    worker_metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
    runner_status_path = _resolve_dispatch_status_path(
        worker.get("runner_status_path")
        or worker.get("status_path")
        or worker_metadata.get("runner_status_path")
    )
    if runner_status_path:
        env["ORCH_RUNNER_STATUS_PATH"] = runner_status_path

    heartbeat_path = _resolve_dispatch_status_path(
        worker.get("heartbeat_path")
        or worker_metadata.get("heartbeat_path")
    )
    if heartbeat_path:
        env["ORCH_HEARTBEAT_PATH"] = heartbeat_path


def sync_status_pipeline(config: dict[str, Any]) -> bool:
    script, env = status_command_subprocess_context(config)
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "task_reassignment_sync_failed",
                "message": f"Status recovery script not found at {script}.",
            },
        )
        return False
    result = subprocess.run(
        [sys.executable, str(script), "recover"],
        cwd=str(config_path(config, "status_file").parent),
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return True
    write_activity_log(
        config,
        {
            "type": "task_reassignment_sync_failed",
            "message": f"Status recovery failed after supervisor write: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}",
        },
    )
    return False


def _status_activity_outbox(events: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps(
        events,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "transaction_id": "ai-status-tx-" + hashlib.sha256(body).hexdigest(),
        "events": events,
    }


def _validated_status_activity_outbox_events(
    value: Any,
) -> list[dict[str, Any]] | None:
    """Return a safe copy of pending audit events, or fail closed."""

    if value in (None, {}, []):
        return []
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "transaction_id", "events"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("events"), list)
        or not value["events"]
        or any(
            not isinstance(event, dict)
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]
            or event["event_id"] != event["event_id"].strip()
            for event in value["events"]
        )
        or len({str(event["event_id"]) for event in value["events"]})
        != len(value["events"])
    ):
        return None
    expected = _status_activity_outbox(value["events"])
    if value.get("transaction_id") != expected["transaction_id"]:
        return None
    return deepcopy(value["events"])


def _compose_status_activity_outbox(
    pending: Any,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    """Append one event without discarding a pending status transaction."""

    events = _validated_status_activity_outbox_events(pending)
    if events is None:
        return None
    matches = [
        existing
        for existing in events
        if str(existing.get("event_id") or "") == str(event.get("event_id") or "")
    ]
    if matches and any(existing != event for existing in matches):
        return None
    if not matches:
        events.append(deepcopy(event))
    return _status_activity_outbox(events)


def sync_dispatched_task_status(
    config: dict[str, Any],
    event: dict[str, Any],
    run_id: str | None = None,
    workspace_path: str | Path | None = None,
) -> bool:
    mutation = prepare_dispatched_task_status_mutation(
        config,
        event,
        run_id=run_id,
        workspace_path=workspace_path,
    )
    if mutation is None:
        return False

    script, env = status_command_subprocess_context(
        config,
        workspace_path=mutation["workspace_path"],
    )
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "task_dispatch_sync_failed",
                "task_id": mutation["task_id"],
                "message": f"Dispatch status sync script not found at {script}.",
            },
        )
        return False

    env["AI_NAME"] = mutation["actor"]
    _apply_dispatch_status_worker_binding(
        config,
        env,
        run_id=mutation["run_id"],
        task_id=mutation["task_id"],
        workspace_path=mutation["workspace_path"],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            mutation["command"],
            mutation["task_id"],
            mutation["message"],
        ],
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
                "task_id": mutation["task_id"],
                "target_agent": mutation["actor"],
                "dispatch_reason": mutation["dispatch_reason"],
                "message": mutation["message"],
            },
        )
        return True

    write_activity_log(
        config,
        {
            "type": "task_dispatch_sync_failed",
            "task_id": mutation["task_id"],
            "target_agent": mutation["actor"],
            "dispatch_reason": mutation["dispatch_reason"],
            "message": (
                result.stderr.strip()
                or result.stdout.strip()
                or "Dispatch status sync failed."
            )[:1000],
        },
    )
    return False


def prepare_dispatched_task_status_mutation(
    config: dict[str, Any],
    event: Mapping[str, Any],
    *,
    run_id: str | None = None,
    workspace_path: str | Path | None = None,
    task_map: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build one lease-bound compare-and-set row without mutating state."""

    reason = str(event.get("reason") or "").strip()
    action = DISPATCH_STATUS_ACTIONS.get(reason)
    if action is None:
        return None
    if not config.get("paths", {}).get("status_file"):
        return None

    deferred = _DEFERRED_DISPATCH_STATUS_SYNCS.get()
    if deferred is not None:
        deferred.append(
            (
                dict(event),
                run_id,
                str(workspace_path) if workspace_path is not None else None,
            )
        )
        return None

    lease_run_id = str(run_id or "").strip()
    workspace_binding = str(workspace_path or "").strip()
    if lease_run_id and not workspace_binding:
        write_activity_log(
            config,
            {
                "type": "task_dispatch_sync_failed",
                "task_id": event.get("task_id"),
                "dispatch_reason": reason,
                "message": (
                    "Dispatch status sync refused an incomplete worker lease: "
                    f"ORCH_RUN_ID={lease_run_id} has no workspace binding."
                ),
            },
        )
        return None

    task_id = str(event.get("task_id") or "").strip()
    target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or ""))).strip()
    if not task_id or not target_agent:
        return None

    command_name, eligible_statuses = action
    tasks = task_map
    if tasks is None:
        tasks = task_index_from_status(config, load_status(config))
    task = tasks.get(task_id)
    if not task:
        return None
    if str(task.get("owner") or "").strip() != target_agent:
        return None
    if str(task.get("status") or "").lower() not in eligible_statuses:
        return None

    message = {
        REASON_OWNED_READY: f"Supervisor auto-started {task_id} after successful dispatch.",
        REASON_OWNED_FINALIZE: f"Supervisor resumed {task_id} for finalize after successful dispatch.",
        REASON_OWNED_IN_PROGRESS: f"Supervisor re-dispatched {task_id}; task remains in progress.",
    }[reason]
    return {
        "actor": target_agent,
        "command": command_name,
        "dispatch_reason": reason,
        "expected_statuses": sorted(eligible_statuses),
        "message": message,
        "run_id": lease_run_id,
        "task_id": task_id,
        "workspace_path": workspace_binding,
    }


def sync_dispatched_task_status_batch(
    config: dict[str, Any],
    deferred: list[tuple[dict[str, Any], str | None, str | None]],
) -> bool:
    """Commit lease-bound dispatch mutations through one ai-status process.

    Slow payload rendering and subprocess work happen after runtime admission is
    released.  ``ai_status.py`` revalidates every worker lease against one
    runtime snapshot, applies every owner/status CAS to one authoritative task
    snapshot, and publishes one activity outbox transaction.  One invalid row
    aborts the whole canonical batch.
    """

    if not deferred:
        return False
    try:
        status = load_status(config)
    except (KeyError, OSError, RuntimeError):
        return False
    task_map = task_index_from_status(config, status)
    mutations: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for event, run_id, workspace_path in deferred:
        mutation = prepare_dispatched_task_status_mutation(
            config,
            event,
            run_id=run_id,
            workspace_path=workspace_path,
            task_map=task_map,
        )
        if mutation is None:
            continue
        task_id = str(mutation["task_id"])
        if task_id in seen_task_ids:
            continue
        seen_task_ids.add(task_id)
        mutation.pop("dispatch_reason", None)
        mutations.append(mutation)
    if not mutations:
        return False

    script, env = status_command_subprocess_context(config)
    if not script.exists():
        write_activity_log(
            config,
            {
                "type": "task_dispatch_batch_sync_failed",
                "mutation_count": len(mutations),
                "message": f"Dispatch batch status script not found at {script}.",
            },
        )
        return False

    payload_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="pantheon-dispatch-status-batch-",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(
                {"schema_version": 1, "mutations": mutations},
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            payload_path = Path(handle.name)

        for env_name in DISPATCH_STATUS_WORKER_ENV_NAMES:
            env.pop(env_name, None)
        env["AI_NAME"] = "Human/Ops"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "supervisor-dispatch-batch",
                str(payload_path),
            ],
            cwd=str(config_path(config, "status_file").parent),
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        if payload_path is not None:
            try:
                payload_path.unlink()
            except FileNotFoundError:
                pass

    task_ids = [str(mutation["task_id"]) for mutation in mutations]
    if result.returncode == 0:
        write_activity_log(
            config,
            {
                "type": "task_dispatch_batch_synced",
                "mutation_count": len(mutations),
                "task_ids": task_ids,
                "message": (
                    f"Committed {len(mutations)} dispatch status mutations in one "
                    "authoritative snapshot transaction."
                ),
            },
        )
        return True

    write_activity_log(
        config,
        {
            "type": "task_dispatch_batch_sync_failed",
            "mutation_count": len(mutations),
            "task_ids": task_ids,
            "message": (
                result.stderr.strip()
                or result.stdout.strip()
                or "Dispatch status batch failed."
            )[:1000],
        },
    )
    return False


def _confirm_deferred_worker_terminations(
    deferred_terminations: list[tuple[int, int]],
) -> None:
    """Confirm exact process generations only after runtime admission is free."""

    for pid, expected_start_ticks in deferred_terminations:
        if worker_pid_start_ticks(pid) != expected_start_ticks:
            continue

        def deferred_worker_is_alive(candidate_pid: int) -> bool:
            if not pid_is_alive(candidate_pid):
                return False
            return worker_pid_start_ticks(candidate_pid) == expected_start_ticks

        rewrite_worker_lifecycle.confirm_kill(
            pid,
            is_alive=deferred_worker_is_alive,
            send_signal=os.kill,
            sleep=time.sleep,
            monotonic=time.monotonic,
        )


def _flush_deferred_runtime_side_effects(
    config: dict[str, Any],
    *,
    dispatch_status_syncs: list[tuple[dict[str, Any], str | None, str | None]],
    worker_terminations: list[tuple[int, int]],
    auto_commit_archives: list[dict[str, Any]],
    activity_events: list[tuple[dict[str, Any], dict[str, Any]]],
) -> bool:
    """Run all subprocess/audit side effects after the short state commit."""

    _confirm_deferred_worker_terminations(worker_terminations)
    for activity_config, activity_event in activity_events:
        _write_activity_log_immediate(activity_config, activity_event)
    _record_cycle_batch_count("runtime_activity_events", len(activity_events))

    archive_changed = False
    for action in auto_commit_archives:
        result = execute_auto_commit_archive(config, action)
        archive_changed = (
            apply_auto_commit_archive_result(config, action, result)
            or archive_changed
        )

    sync_changed = sync_dispatched_task_status_batch(
        config,
        dispatch_status_syncs,
    )
    return archive_changed or sync_changed


def _runtime_state_cas_digest(state: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_phase_reservation_record(
    state: Mapping[str, Any],
    phase_name: str,
    reservation_token: str,
) -> dict[str, Any] | None:
    supervisor_state = state.get("supervisor")
    if not isinstance(supervisor_state, Mapping):
        return None
    reservations = supervisor_state.get("runtime_phase_reservations")
    if not isinstance(reservations, Mapping):
        return None
    reservation = reservations.get(phase_name)
    if not isinstance(reservation, dict):
        return None
    if str(reservation.get("token") or "") != reservation_token:
        return None
    return reservation


def _persist_runtime_phase_launch_intent(
    config: dict[str, Any],
    scratch: dict[str, Any],
    *,
    request: DeliveryRequest,
    queue_event_id: str | None,
    attempt_count: int,
    event_id_for_log: str | None,
    parent_run_id: str | None,
    adapter_name: str,
    activity_type: str,
    activity_message: str | None,
) -> None:
    """Publish an exact-token intent before an adapter may create a process."""

    context = _RUNTIME_PHASE_CONTEXT.get()
    if not isinstance(context, dict):
        return
    phase_name = str(context.get("phase_name") or "")
    reservation_token = str(context.get("reservation_token") or "")
    expected_digest = str(context.get("expected_digest") or "")
    if not phase_name or not reservation_token or not expected_digest:
        raise RuntimeError("reserved runtime phase launch context is incomplete")

    prepared_at = utc_now()
    prepared_epoch_seconds = time.time()
    prepared_boottime_ticks = _runtime_launch_prepared_boottime_ticks()
    intent = {
        "schema_version": 1,
        "status": "prepared",
        "prepared_at": prepared_at,
        "prepared_epoch_seconds": prepared_epoch_seconds,
        "task_id": request.task_id,
        "task_generation": request.metadata.get("task_generation"),
        "queue_event_id": queue_event_id,
        "event_id_for_log": event_id_for_log,
        "agent_id": request.agent_id,
        "provider": request.provider,
        "attempt_count": max(1, int(attempt_count)),
        "parent_run_id": parent_run_id,
        "adapter_name": adapter_name,
        "activity_type": activity_type,
        "activity_message": activity_message,
        "request_snapshot": request_snapshot(request),
        "event": {
            "event_id": queue_event_id,
            "task_id": request.task_id,
            "task_generation": request.metadata.get("task_generation"),
            "target_agent": request.agent_id,
            "target_display_name": request.metadata.get("target_display_name")
            or display_name_for(config, request.agent_id),
            "provider": request.provider,
            "reason": request.reason,
        },
    }
    if prepared_boottime_ticks is not None:
        # Linux process start ticks and CLOCK_BOOTTIME share the same boot
        # epoch.  Persisting the pre-spawn boundary avoids the one-second
        # precision loss in /proc/stat's wall-clock ``btime`` value.
        intent["prepared_boottime_ticks"] = prepared_boottime_ticks

    with _measured_runtime_state_lock(config):
        current = load_runtime_state(config)
        reservation = _runtime_phase_reservation_record(
            current,
            phase_name,
            reservation_token,
        )
        if reservation is None or _runtime_state_cas_digest(current) != expected_digest:
            raise RuntimeError(
                "runtime phase changed before durable worker launch intent"
            )
        reservation["launch_intent"] = deepcopy(intent)
        save_runtime_state(config, current)
        context["expected_digest"] = _runtime_state_cas_digest(current)

    scratch_reservation = _runtime_phase_reservation_record(
        scratch,
        phase_name,
        reservation_token,
    )
    if scratch_reservation is None:
        raise RuntimeError("detached runtime phase lost its worker launch reservation")
    scratch_reservation["launch_intent"] = deepcopy(intent)


def _persist_runtime_phase_launch_receipt(
    config: dict[str, Any],
    scratch: dict[str, Any],
    worker: Mapping[str, Any],
) -> None:
    """Durably bind a launched worker to its reservation before phase CAS."""

    context = _RUNTIME_PHASE_CONTEXT.get()
    if not isinstance(context, dict):
        return
    phase_name = str(context.get("phase_name") or "")
    reservation_token = str(context.get("reservation_token") or "")
    expected_digest = str(context.get("expected_digest") or "")
    receipt = {
        "schema_version": 1,
        "recorded_at": utc_now(),
        "worker": deepcopy(dict(worker)),
    }
    conflict = False
    task_generation_conflict = False
    with _measured_runtime_state_lock(config):
        current = load_runtime_state(config)
        reservation = _runtime_phase_reservation_record(
            current,
            phase_name,
            reservation_token,
        )
        if reservation is None or _runtime_state_cas_digest(current) != expected_digest:
            conflict = True
        else:
            # Receipt authority is a two-plane CAS. Runtime bytes alone are
            # insufficient because assignment/lifecycle may change while git
            # and adapter delivery run outside locks.
            with canonical_task_state_lock_file(
                config_path(config, "status_file"),
                shared=True,
                nonblocking=False,
            ):
                status = load_status(config)
                current_tasks = task_index_from_status(config, status)
                task_generation_conflict = not worker_matches_current_assignment(
                    config,
                    dict(worker),
                    current_tasks,
                )
            if task_generation_conflict:
                conflict = True
            else:
                reservation["launch_receipt"] = deepcopy(receipt)
                save_runtime_state(config, current)
                context["expected_digest"] = _runtime_state_cas_digest(current)

    if conflict:
        # The detached snapshot no longer has commit authority. Termination is
        # deliberately outside runtime admission and is generation-bound.
        token = _DEFERRED_WORKER_TERMINATIONS.set(None)
        try:
            terminate_worker_process_generation(worker)
        finally:
            _DEFERRED_WORKER_TERMINATIONS.reset(token)
        raise RuntimeError(
            "canonical task generation changed before worker launch receipt"
            if task_generation_conflict
            else "runtime phase changed before durable worker launch receipt"
        )

    scratch_reservation = _runtime_phase_reservation_record(
        scratch,
        phase_name,
        reservation_token,
    )
    if scratch_reservation is None:
        token = _DEFERRED_WORKER_TERMINATIONS.set(None)
        try:
            terminate_worker_process_generation(worker)
        finally:
            _DEFERRED_WORKER_TERMINATIONS.reset(token)
        raise RuntimeError("detached runtime phase lost its worker launch reservation")
    scratch_reservation["launch_receipt"] = deepcopy(receipt)


def _runtime_launch_marker_candidates(
    config: dict[str, Any],
    intent: Mapping[str, Any],
) -> list[tuple[dict[str, Any], Path]]:
    """Find markers whose immutable start time proves post-intent order."""

    task_id = str(intent.get("task_id") or "")
    agent_ids = {
        normalize_agent_id(str(intent.get("agent_id") or "")),
        normalize_agent_id(str(intent.get("provider") or "")),
        normalize_agent_id(str(intent.get("adapter_name") or "")),
    }
    agent_ids.discard("")
    prepared_epoch = _runtime_launch_prepared_epoch_seconds(intent)
    if prepared_epoch is None:
        return []
    status_dir = worker_runtime_paths(config, "launch-recovery-probe")[
        "status_path"
    ].parent
    if not status_dir.is_dir():
        return []

    candidates: list[tuple[dict[str, Any], Path]] = []
    for path in sorted(status_dir.glob("*.json")):
        try:
            if path.is_symlink():
                continue
        except OSError:
            continue
        marker = _load_runtime_marker(path)
        if not isinstance(marker, dict):
            continue
        if str(marker.get("task_id") or "") != task_id:
            continue
        marker_agent = normalize_agent_id(str(marker.get("agent") or ""))
        if agent_ids and marker_agent and marker_agent not in agent_ids:
            continue
        if not str(marker.get("run_id") or ""):
            continue
        marker_started_at = _parse_iso_utc(str(marker.get("started_at") or ""))
        if marker_started_at is None:
            continue
        if marker_started_at.tzinfo is None:
            marker_started_at = marker_started_at.replace(tzinfo=timezone.utc)
        # worker_runner timestamps have one-second resolution.  A marker in
        # the same wall-clock second as a fractional intent boundary, or equal
        # to a legacy second-resolution boundary, cannot prove strictly
        # post-intent order and must fail closed.  A matching live process may
        # still be recovered through the boot-tick proof below.
        if marker_started_at.timestamp() <= prepared_epoch:
            continue
        candidates.append((marker, path))

    return candidates


def _runtime_launch_marker_is_terminal_or_dead(
    marker: Mapping[str, Any],
) -> bool:
    """Return whether marker-only recovery cannot claim a live generation."""

    marker_status = str(marker.get("status") or "").lower()
    if marker_status in {"completed", "failed", "terminated", "cancelled"}:
        return True
    raw_pid = marker.get("pid")
    pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
    return not pid_is_alive(pid)


def _runtime_launch_intent_stale_seconds(config: Mapping[str, Any]) -> float:
    supervisor_settings = config.get("supervisor")
    settings = supervisor_settings if isinstance(supervisor_settings, Mapping) else {}
    raw = settings.get(
        "runtime_phase_launch_intent_stale_seconds",
        RUNTIME_PHASE_LAUNCH_INTENT_STALE_DEFAULT_SECONDS,
    )
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = RUNTIME_PHASE_LAUNCH_INTENT_STALE_DEFAULT_SECONDS
    if not math.isfinite(seconds):
        seconds = RUNTIME_PHASE_LAUNCH_INTENT_STALE_DEFAULT_SECONDS
    return min(
        RUNTIME_PHASE_LAUNCH_INTENT_STALE_MAX_SECONDS,
        max(1.0, seconds),
    )


def _runtime_launch_prepared_epoch_seconds(
    intent: Mapping[str, Any],
) -> float | None:
    try:
        prepared_epoch = float(intent.get("prepared_epoch_seconds") or 0.0)
    except (TypeError, ValueError):
        prepared_epoch = 0.0
    if prepared_epoch > 0 and math.isfinite(prepared_epoch):
        return prepared_epoch
    prepared_at = _parse_iso_utc(str(intent.get("prepared_at") or ""))
    if prepared_at is None:
        return None
    if prepared_at.tzinfo is None:
        prepared_at = prepared_at.replace(tzinfo=timezone.utc)
    return prepared_at.timestamp()


def _linux_clock_ticks_per_second() -> float | None:
    try:
        ticks = float(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return ticks if ticks > 0 and math.isfinite(ticks) else None


def _runtime_launch_prepared_boottime_ticks() -> int | None:
    """Return the current Linux boot-relative tick boundary when available."""

    ticks_per_second = _linux_clock_ticks_per_second()
    clock_boottime = getattr(time, "CLOCK_BOOTTIME", None)
    if ticks_per_second is None or clock_boottime is None:
        return None
    try:
        boottime_seconds = float(time.clock_gettime(clock_boottime))
    except (OSError, TypeError, ValueError):
        return None
    if boottime_seconds < 0 or not math.isfinite(boottime_seconds):
        return None
    return math.floor(boottime_seconds * ticks_per_second)


def _proc_boot_epoch_seconds(proc_root: Path) -> float | None:
    """Read Linux's boot wall-clock epoch from one procfs root."""

    try:
        lines = (proc_root / "stat").read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) != 2 or fields[0] != "btime":
            continue
        try:
            boot_epoch = float(fields[1])
        except (TypeError, ValueError):
            return None
        return boot_epoch if boot_epoch > 0 and math.isfinite(boot_epoch) else None
    return None


def _proc_process_started_epoch_seconds(
    proc_root: Path,
    pid_start_ticks: int,
) -> float | None:
    boot_epoch = _proc_boot_epoch_seconds(proc_root)
    ticks_per_second = _linux_clock_ticks_per_second()
    if boot_epoch is None or ticks_per_second is None or pid_start_ticks < 0:
        return None
    return boot_epoch + (pid_start_ticks / ticks_per_second)


def _runtime_launch_intent_is_stale(
    config: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> bool:
    prepared_epoch = _runtime_launch_prepared_epoch_seconds(intent)
    if prepared_epoch is None:
        return True
    return max(0.0, time.time() - prepared_epoch) >= _runtime_launch_intent_stale_seconds(config)


def _proc_worker_runner_launch_marker(
    config: dict[str, Any],
    intent: Mapping[str, Any],
    entry: Path,
) -> tuple[dict[str, Any], Path] | None:
    """Return exact task/agent launch evidence for one live worker wrapper.

    Worker prompts are not an identity boundary.  The adapter binds the exact
    task, logical agent/provider, and run id in the wrapper environment before
    ``Popen``.  Reading those fields together with Linux PID start ticks gives
    recovery process-generation evidence even when the runner has not yet
    published its first atomic JSON marker.
    """

    raw_cmdline = (entry / "cmdline").read_bytes()
    if not raw_cmdline or b"worker_runner.py" not in raw_cmdline:
        return None
    raw_environ = (entry / "environ").read_bytes()
    env: dict[str, str] = {}
    for item in raw_environ.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", errors="ignore")] = value.decode(
            "utf-8",
            errors="ignore",
        )

    task_id = str(intent.get("task_id") or "")
    if not task_id or env.get("ORCH_TASK_ID") != task_id:
        return None
    intent_agents = {
        normalize_agent_id(str(intent.get("agent_id") or "")),
        normalize_agent_id(str(intent.get("provider") or "")),
    }
    intent_agents.discard("")
    process_agents = {
        normalize_agent_id(env.get("ORCH_AGENT_ID", "")),
        normalize_agent_id(env.get("ORCH_PROVIDER", "")),
    }
    process_agents.discard("")
    if not process_agents or (
        intent_agents and intent_agents.isdisjoint(process_agents)
    ):
        return None

    try:
        pid = int(entry.name)
    except ValueError:
        return None
    pid_start_ticks = worker_pid_start_ticks(pid, proc_root=entry.parent)
    if pid_start_ticks is None:
        raise RuntimeError("exact worker-runner PID generation is unreadable")
    process_started_epoch = _proc_process_started_epoch_seconds(
        entry.parent,
        pid_start_ticks,
    )
    prepared_epoch = _runtime_launch_prepared_epoch_seconds(intent)
    if process_started_epoch is None or prepared_epoch is None:
        raise RuntimeError("worker-runner launch epoch is unreadable")

    raw_prepared_boottime_ticks = intent.get("prepared_boottime_ticks")
    if raw_prepared_boottime_ticks is not None:
        try:
            prepared_boottime_ticks = int(raw_prepared_boottime_ticks)
        except (TypeError, ValueError):
            raise RuntimeError("worker launch boot-tick boundary is invalid")
        if prepared_boottime_ticks < 0:
            raise RuntimeError("worker launch boot-tick boundary is invalid")
        if pid_start_ticks < prepared_boottime_ticks:
            return None

    # ``btime`` is intentionally whole-second procfs data, so its reconstructed
    # wall epoch can lead real time by less than one second.  The persisted
    # boot-tick boundary above provides the exact check for newly written
    # intents; this fallback keeps older intents recoverable while rejecting
    # process generations that are definitively earlier than the intent.
    if process_started_epoch + 1.0 < prepared_epoch:
        return None
    argv = [
        value.decode("utf-8", errors="ignore")
        for value in raw_cmdline.split(b"\0")
        if value
    ]
    run_id = str(env.get("ORCH_RUN_ID") or "")
    if not run_id and "--run-id" in argv:
        index = argv.index("--run-id") + 1
        if index < len(argv):
            run_id = argv[index]
    if not run_id:
        raise RuntimeError("exact worker-runner run id is unreadable")
    status_path = worker_runtime_paths(config, run_id)["status_path"]
    return (
        {
            "run_id": run_id,
            "agent": env.get("ORCH_AGENT_ID") or env.get("ORCH_PROVIDER"),
            "task_id": task_id,
            "status": "running",
            "pid": pid,
            "pid_start_ticks": pid_start_ticks,
            "started_at": _isoformat_utc(
                datetime.fromtimestamp(process_started_epoch, tz=timezone.utc)
            ),
            "process_started_epoch_seconds": process_started_epoch,
            "command": [],
            "launch_recovered_from": "proc_environ",
        },
        status_path,
    )


def _runtime_launch_process_candidates(
    config: dict[str, Any],
    intent: Mapping[str, Any],
    *,
    proc_root: Path | None = None,
) -> tuple[list[tuple[dict[str, Any], Path]], bool]:
    """Return exact live launch processes and whether the scan was conclusive."""

    root = proc_root if proc_root is not None else Path("/proc")
    try:
        entries = list(root.iterdir())
    except OSError:
        return [], False
    candidates: list[tuple[dict[str, Any], Path]] = []
    conclusive = True
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            candidate = _proc_worker_runner_launch_marker(config, intent, entry)
        except PermissionError:
            # Workers are spawned under the supervisor uid. An unreadable
            # same-uid process might therefore be the missing wrapper; a
            # different-uid process cannot be this launch generation.
            try:
                same_uid = entry.stat().st_uid == os.geteuid()
            except OSError:
                same_uid = False
            if same_uid:
                conclusive = False
            continue
        except RuntimeError:
            conclusive = False
            continue
        except OSError:
            # A disappearing PID is proof that it is no longer live. Other
            # unreadable same-uid processes fail closed.
            try:
                same_uid = entry.exists() and entry.stat().st_uid == os.geteuid()
            except OSError:
                same_uid = False
            if same_uid:
                conclusive = False
            continue
        if candidate is not None:
            candidates.append(candidate)
    unique: dict[tuple[str, int], tuple[dict[str, Any], Path]] = {}
    for marker, path in candidates:
        unique[(str(marker.get("run_id") or ""), int(marker.get("pid") or 0))] = (
            marker,
            path,
        )
    return list(unique.values()), conclusive


def _worker_record_from_runtime_launch_marker(
    config: dict[str, Any],
    intent: Mapping[str, Any],
    marker: Mapping[str, Any],
    status_path: Path,
) -> dict[str, Any] | None:
    snapshot = intent.get("request_snapshot")
    if not isinstance(snapshot, dict):
        return None
    run_id = str(marker.get("run_id") or "")
    task_id = str(intent.get("task_id") or snapshot.get("task_id") or "")
    queue_event_id = str(intent.get("queue_event_id") or "") or None
    raw_pid = marker.get("pid")
    pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
    pid_start_ticks = worker_pid_start_ticks(pid) if pid_is_alive(pid) else None
    if pid_start_ticks is None:
        pid = None
    marker_status = str(marker.get("status") or "").lower()
    if marker_status == "completed":
        worker_status = "completed"
    elif marker_status in {"failed", "terminated", "cancelled"}:
        worker_status = "failed"
    elif pid is not None:
        worker_status = "running"
    else:
        worker_status = "failed"
    started_at = str(marker.get("started_at") or intent.get("prepared_at") or utc_now())
    started_dt = _parse_iso_utc(started_at) or datetime.now(timezone.utc)
    metadata = snapshot.get("metadata")
    metadata = deepcopy(metadata) if isinstance(metadata, dict) else {}
    runtime_paths = worker_runtime_paths(config, run_id)
    metadata.update(
        {
            "heartbeat_path": str(runtime_paths["heartbeat_path"]),
            "runner_status_path": str(status_path),
            "launch_recovered": True,
        }
    )
    provider = str(intent.get("provider") or snapshot.get("provider") or "")
    agent_id = str(intent.get("agent_id") or snapshot.get("agent_id") or provider)
    process_generation = (
        worker_process_generation_id(
            task_id=task_id,
            worker_run_id=run_id,
            queue_event_id=str(queue_event_id or ""),
            pid=pid,
            pid_start_ticks=pid_start_ticks,
        )
        if task_id and queue_event_id and pid is not None and pid_start_ticks is not None
        else None
    )
    return {
        "run_id": run_id,
        "provider": provider,
        "agent_id": agent_id,
        "logical_agent_id": str(metadata.get("logical_agent_id") or agent_id),
        "dispatch_slot_id": metadata.get("dispatch_slot_id"),
        "dispatch_slot": metadata.get("dispatch_slot"),
        "account": provider_account_id(config, provider),
        "task_id": task_id,
        "task_generation": snapshot.get("task_generation")
        or metadata.get("task_generation"),
        "session_id": None,
        "mode": snapshot.get("delivery_mode"),
        "status": worker_status,
        "last_event_at": utc_now(),
        "last_heartbeat_at": marker.get("last_heartbeat_at"),
        "lease_acquired_at": _isoformat_utc(started_dt),
        "lease_expires_at": worker_lease_expiry(config, started_dt),
        "deferred_action": None,
        "resume_token": None,
        "pr_url": None,
        "session_url": None,
        "attempt_count": max(1, int(intent.get("attempt_count") or 1)),
        "queue_event_id": queue_event_id,
        "command": deepcopy(marker.get("command") or []),
        "log_path": None,
        "payload_path": None,
        "workspace_mode": metadata.get("workspace_mode"),
        "workspace_path": metadata.get("workspace_path"),
        "workspace_branch": metadata.get("workspace_branch"),
        "workspace_repository_id": metadata.get("workspace_repository_id"),
        "workspace_source_root": metadata.get("workspace_source_root"),
        "workspace_base_ref": metadata.get("workspace_base_ref"),
        "work_progress_snapshot": {},
        "last_commit_progress_at": None,
        "last_work_progress_at": None,
        "commit_progress_count": 0,
        "status_root": metadata.get("status_root"),
        "status_command_runtime": metadata.get("status_command_runtime"),
        "pid": pid,
        "pid_start_ticks": pid_start_ticks,
        "process_generation": process_generation,
        "heartbeat_path": str(runtime_paths["heartbeat_path"]),
        "runner_status_path": str(status_path),
        "notes": "Recovered from a durable pre-launch reservation intent.",
        "metadata": metadata,
        "request_snapshot": deepcopy(snapshot),
        "parent_run_id": intent.get("parent_run_id"),
        "retry_count": max(0, int(intent.get("attempt_count") or 1) - 1),
        "next_retry_at": None,
        "last_error": (
            None
            if worker_status != "failed"
            else "Reserved launch marker was terminal or its exact process generation was no longer live."
        ),
    }


def _clear_stale_runtime_phase_launch_intent(
    config: dict[str, Any],
    *,
    phase_name: str,
    reservation_token: str,
    intent: Mapping[str, Any],
    marker_count: int,
) -> bool:
    """Clear an unchanged stale intent after a conclusive zero-process scan."""

    cleared = False
    with _measured_runtime_state_lock(config):
        current = load_runtime_state(config)
        reservation = _runtime_phase_reservation_record(
            current,
            phase_name,
            reservation_token,
        )
        if (
            reservation is None
            or isinstance(reservation.get("launch_receipt"), Mapping)
            or reservation.get("launch_intent") != dict(intent)
        ):
            return False
        reservations = current.setdefault("supervisor", {}).setdefault(
            "runtime_phase_reservations",
            {},
        )
        reservations.pop(phase_name, None)
        if not reservations:
            current["supervisor"].pop("runtime_phase_reservations", None)
        save_runtime_state(config, current)
        cleared = True

    if cleared:
        write_activity_log(
            config,
            {
                "type": "runtime_phase_launch_intent_expired",
                "phase": phase_name,
                "task_id": intent.get("task_id"),
                "queue_event_id": intent.get("queue_event_id"),
                "marker_count": max(0, int(marker_count)),
                "message": (
                    "Cleared a stale reserved-phase launch intent after an exact "
                    "task/agent /proc scan proved that no worker-runner process "
                    "remained live."
                ),
            },
        )
    return cleared


def _recover_runtime_phase_reservation(
    config: dict[str, Any],
    phase_name: str,
) -> bool | None:
    """Adopt a launched worker before allowing a reserved phase to repeat.

    ``None`` means there is no recoverable prior reservation and the caller may
    start a new phase. ``False`` retains an unresolved launch intent fail
    closed. ``True`` means the exact worker/queue lease was adopted.
    """

    cleared_legacy = False
    with _measured_runtime_state_lock(config):
        current = load_runtime_state(config)
        reservations = current.setdefault("supervisor", {}).setdefault(
            "runtime_phase_reservations",
            {},
        )
        reservation = reservations.get(phase_name)
        if not isinstance(reservation, dict):
            return None
        reservation_snapshot = deepcopy(reservation)
        intent = reservation_snapshot.get("launch_intent")
        if not isinstance(intent, Mapping):
            # Older/non-launch reservations have no external side effect to
            # adopt. A singleton restart may safely discard that stale token.
            reservations.pop(phase_name, None)
            if not reservations:
                current["supervisor"].pop("runtime_phase_reservations", None)
            save_runtime_state(config, current)
            cleared_legacy = True

    if cleared_legacy:
        return None

    receipt = reservation_snapshot.get("launch_receipt")
    receipt_worker = receipt.get("worker") if isinstance(receipt, Mapping) else None
    worker = deepcopy(receipt_worker) if isinstance(receipt_worker, Mapping) else None
    if worker is None:
        marker_candidates = _runtime_launch_marker_candidates(config, intent)
        process_candidates, process_scan_conclusive = (
            _runtime_launch_process_candidates(config, intent)
        )
        if not process_scan_conclusive:
            return False

        candidate: tuple[dict[str, Any], Path] | None = None
        if len(process_candidates) == 1:
            process_marker, process_status_path = process_candidates[0]
            process_run_id = str(process_marker.get("run_id") or "")
            matching_markers = [
                item
                for item in marker_candidates
                if str(item[0].get("run_id") or "") == process_run_id
            ]
            candidate = (
                matching_markers[0]
                if len(matching_markers) == 1
                else (process_marker, process_status_path)
            )
        elif len(process_candidates) > 1:
            # More than one exact task/agent worker wrapper is real ambiguity,
            # not merely stale marker debris. Preserve the reservation until
            # operators or process exit reduce it to one generation.
            return False
        elif len(marker_candidates) == 1 and _runtime_launch_marker_is_terminal_or_dead(
            marker_candidates[0][0]
        ):
            # A unique post-intent terminal/dead marker is sufficient to
            # recover the terminal outcome. A marker that still claims a live
            # worker must match an exact post-intent /proc generation above;
            # uniqueness alone never authorizes a new queue lease binding.
            candidate = marker_candidates[0]
        elif not _runtime_launch_intent_is_stale(config, intent):
            # The runner may not have written its first marker yet. The bounded
            # grace avoids a duplicate launch during ordinary spawn latency.
            return False
        else:
            reservation_token = str(reservation_snapshot.get("token") or "")
            if not _clear_stale_runtime_phase_launch_intent(
                config,
                phase_name=phase_name,
                reservation_token=reservation_token,
                intent=intent,
                marker_count=len(marker_candidates),
            ):
                return False
            return None

        if candidate is None:
            return False
        marker, status_path = candidate
        worker = _worker_record_from_runtime_launch_marker(
            config,
            intent,
            marker,
            status_path,
        )
        if worker is None:
            return False
    else:
        update_worker_runtime_markers(worker)

    run_id = str(worker.get("run_id") or "")
    event_id = str(worker.get("queue_event_id") or intent.get("queue_event_id") or "")
    if not run_id or not event_id:
        return False

    reservation_token = str(reservation_snapshot.get("token") or "")
    adopted = False
    with _measured_runtime_state_lock(config):
        current = load_runtime_state(config)
        current_reservation = _runtime_phase_reservation_record(
            current,
            phase_name,
            reservation_token,
        )
        if current_reservation is None:
            return False
        current.setdefault("workers", {}).setdefault(run_id, deepcopy(worker))
        persisted_worker = current["workers"][run_id]
        queue_record = queue_status(current, event_id)
        worker_status = str(persisted_worker.get("status") or "running")
        if worker_status == "waiting_approval":
            queue_record["status"] = "waiting_approval"
        elif worker_status == "completed":
            queue_record["status"] = "completed"
        elif worker_status in {"failed", "terminated", "cancelled"}:
            queue_record["status"] = "failed"
        else:
            queue_record["status"] = "started"
        queue_record["run_id"] = run_id
        queue_record["lease_owner"] = run_id
        queue_record["lease_acquired_at"] = persisted_worker.get("lease_acquired_at") or utc_now()
        queue_record["lease_expires_at"] = persisted_worker.get("lease_expires_at") or queue_lease_expiry(config)
        queue_record["processed_at"] = queue_record.get("processed_at") or utc_now()
        queue_record["attempt_count"] = max(
            int(queue_record.get("attempt_count", 0) or 0),
            int(intent.get("attempt_count", 1) or 1),
        )
        record_worker_runtime_measurement(
            config,
            current,
            "worker_launch_recovered",
            {"workers_started": 1, "queue_leases_started": 1},
            details={
                "worker_run_id": run_id,
                "queue_event_id": event_id,
                "task_id": persisted_worker.get("task_id"),
                "agent_id": persisted_worker.get("agent_id"),
                "provider": persisted_worker.get("provider"),
            },
            emit_activity=False,
        )
        reservations = current.setdefault("supervisor", {}).setdefault(
            "runtime_phase_reservations",
            {},
        )
        reservations.pop(phase_name, None)
        if not reservations:
            current["supervisor"].pop("runtime_phase_reservations", None)
        save_runtime_state(config, current)
        adopted = True

    if adopted:
        write_activity_log(
            config,
            {
                "type": "worker_launch_recovered",
                "task_id": worker.get("task_id"),
                "target_agent": display_name_for(config, str(worker.get("agent_id") or "")),
                "provider": worker.get("provider"),
                "message": (
                    "Adopted the exact worker process/queue lease from a durable "
                    "reserved-phase launch intent after supervisor interruption."
                ),
                "queue_event_id": event_id,
                "worker_run_id": run_id,
                "pid": worker.get("pid"),
                "pid_start_ticks": worker.get("pid_start_ticks"),
                "process_generation": worker.get("process_generation"),
            },
        )
        event = intent.get("event")
        if isinstance(event, dict):
            sync_dispatched_task_status(
                config,
                event,
                run_id=run_id,
                workspace_path=worker.get("workspace_path")
                or config_path(config, "status_file").parent,
            )
    return adopted


def _terminate_processes_started_by_failed_phase(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    """Fail closed if a slow phase launched a process but lost its state CAS."""

    prior_workers = before.get("workers") if isinstance(before, Mapping) else {}
    prior_workers = prior_workers if isinstance(prior_workers, Mapping) else {}
    next_workers = after.get("workers") if isinstance(after, Mapping) else {}
    next_workers = next_workers if isinstance(next_workers, Mapping) else {}
    for run_id, worker in next_workers.items():
        if not isinstance(worker, Mapping):
            continue
        prior = prior_workers.get(run_id)
        prior_generation = (
            str(prior.get("process_generation") or "")
            if isinstance(prior, Mapping)
            else ""
        )
        next_generation = str(worker.get("process_generation") or "")
        if next_generation and next_generation != prior_generation:
            terminate_worker_process_generation(worker)


def _run_reserved_runtime_phase(
    config: dict[str, Any],
    phase_name: str,
    operation: Any,
) -> bool:
    """Run slow runtime I/O between a token reservation and whole-state CAS.

    The singleton supervisor first publishes a bounded reservation in a short
    exclusive transaction. Slow process, worktree, git, marker, and prune I/O
    then mutates a detached snapshot with activity/status side effects deferred.
    A second short transaction commits that snapshot only when the reservation
    and every runtime byte still match. A concurrent writer therefore wins; a
    process launched by the losing phase is terminated by exact PID generation.
    """

    reservation_token = new_runtime_id(f"phase-{phase_name}")
    existing_reservation = False
    with _measured_runtime_state_lock(config):
        baseline = load_runtime_state(config)
        reservations = baseline.setdefault("supervisor", {}).setdefault(
            "runtime_phase_reservations",
            {},
        )
        existing_reservation = isinstance(reservations.get(phase_name), dict)
        if not existing_reservation:
            reserved = deepcopy(baseline)
            reserved_reservations = reserved.setdefault("supervisor", {}).setdefault(
                "runtime_phase_reservations",
                {},
            )
            reserved_reservations[phase_name] = {
                "token": reservation_token,
                "reserved_at": utc_now(),
            }
            save_runtime_state(config, reserved)
    if existing_reservation:
        recovery_result = _recover_runtime_phase_reservation(config, phase_name)
        if recovery_result is not None:
            return recovery_result
        # Recovery proved that the previous token had no external launch to
        # adopt. Reserve and run a fresh phase now so quota/auth fallback or
        # reassignment is not delayed for another supervisor cadence.
        return _run_reserved_runtime_phase(config, phase_name, operation)

    reserved_digest = _runtime_state_cas_digest(reserved)
    scratch = deepcopy(reserved)
    phase_context = {
        "phase_name": phase_name,
        "reservation_token": reservation_token,
        "expected_digest": reserved_digest,
    }

    deferred_dispatches: list[tuple[dict[str, Any], str | None, str | None]] = []
    deferred_terminations: list[tuple[int, int]] = []
    deferred_archives: list[dict[str, Any]] = []
    deferred_activity_events: list[tuple[dict[str, Any], dict[str, Any]]] = []
    dispatch_token = _DEFERRED_DISPATCH_STATUS_SYNCS.set(deferred_dispatches)
    termination_token = _DEFERRED_WORKER_TERMINATIONS.set(deferred_terminations)
    archive_token = _DEFERRED_AUTO_COMMIT_ARCHIVES.set(deferred_archives)
    activity_token = _DEFERRED_ACTIVITY_EVENTS.set(deferred_activity_events)
    phase_token = _RUNTIME_PHASE_RESERVATION.set(reservation_token)
    phase_context_token = _RUNTIME_PHASE_CONTEXT.set(phase_context)
    phase_error: BaseException | None = None
    changed = False
    try:
        changed = bool(operation(scratch))
    except BaseException as exc:  # the reservation must be cleared before isolation
        phase_error = exc
    finally:
        _RUNTIME_PHASE_CONTEXT.reset(phase_context_token)
        _RUNTIME_PHASE_RESERVATION.reset(phase_token)
        _DEFERRED_DISPATCH_STATUS_SYNCS.reset(dispatch_token)
        _DEFERRED_WORKER_TERMINATIONS.reset(termination_token)
        _DEFERRED_AUTO_COMMIT_ARCHIVES.reset(archive_token)
        _DEFERRED_ACTIVITY_EVENTS.reset(activity_token)

    committed = False
    launch_recovery_pending = False
    with _measured_runtime_state_lock(config):
        current = load_runtime_state(config)
        current_reservation = (
            ((current.get("supervisor") or {}).get("runtime_phase_reservations") or {})
            .get(phase_name, {})
        )
        cas_matches = (
            isinstance(current_reservation, Mapping)
            and current_reservation.get("token") == reservation_token
            and _runtime_state_cas_digest(current)
            == str(phase_context.get("expected_digest") or "")
        )
        if phase_error is None and cas_matches:
            phase_reservations = (
                scratch.setdefault("supervisor", {})
                .setdefault("runtime_phase_reservations", {})
            )
            phase_reservations.pop(phase_name, None)
            if not phase_reservations:
                scratch["supervisor"].pop("runtime_phase_reservations", None)
            save_runtime_state(config, scratch)
            committed = True
        elif (
            isinstance(current_reservation, Mapping)
            and current_reservation.get("token") == reservation_token
        ):
            launch_recovery_pending = bool(
                phase_error is not None
                and isinstance(current_reservation.get("launch_intent"), Mapping)
            )
            if not launch_recovery_pending:
                reservations = (
                    current.setdefault("supervisor", {})
                    .setdefault("runtime_phase_reservations", {})
                )
                reservations.pop(phase_name, None)
                if not reservations:
                    current["supervisor"].pop("runtime_phase_reservations", None)
                save_runtime_state(config, current)

    if not committed:
        if not launch_recovery_pending:
            _terminate_processes_started_by_failed_phase(reserved, scratch)
        if phase_error is not None:
            raise phase_error
        write_activity_log(
            config,
            {
                "type": "runtime_phase_cas_conflict",
                "phase": phase_name,
                "message": (
                    f"Discarded reserved runtime phase {phase_name}: runtime "
                    "state changed before its exact CAS commit."
                ),
            },
        )
        return False

    side_effect_changed = _flush_deferred_runtime_side_effects(
        config,
        dispatch_status_syncs=deferred_dispatches,
        worker_terminations=deferred_terminations,
        auto_commit_archives=deferred_archives,
        activity_events=deferred_activity_events,
    )
    return changed or side_effect_changed


def _run_dispatch_plan_transaction(
    config: dict[str, Any],
    operation: Any,
) -> bool:
    """Plan and reserve durable intents in one bounded runtime transaction.

    Planning has no network, provider-cache, process-scan, git, or subprocess
    I/O. Queue append and ``seen_event_keys`` commit share the runtime admission
    lock, so a queued intent cannot create the whole-state CAS conflict caused
    by the retired detached-phase implementation.
    """

    def transaction() -> bool:
        state = load_runtime_state(config)
        changed = bool(operation(state))
        if changed:
            save_runtime_state(config, state)
        return changed

    return _run_with_deferred_dispatch_status_syncs(config, transaction)


def _run_with_deferred_dispatch_status_syncs(
    config: dict[str, Any],
    operation: Any,
) -> bool:
    """Run one short supervisor mutation and flush slow side effects post-lock."""

    deferred: list[tuple[dict[str, Any], str | None, str | None]] = []
    deferred_terminations: list[tuple[int, int]] = []
    deferred_archives: list[dict[str, Any]] = []
    deferred_activity_events: list[tuple[dict[str, Any], dict[str, Any]]] = []
    token = _DEFERRED_DISPATCH_STATUS_SYNCS.set(deferred)
    termination_token = _DEFERRED_WORKER_TERMINATIONS.set(deferred_terminations)
    archive_token = _DEFERRED_AUTO_COMMIT_ARCHIVES.set(deferred_archives)
    activity_token = _DEFERRED_ACTIVITY_EVENTS.set(deferred_activity_events)
    changed = False
    operation_error: BaseException | None = None
    try:
        with _measured_runtime_state_lock(config):
            changed = bool(operation())
    except BaseException as exc:
        operation_error = exc
    finally:
        _DEFERRED_DISPATCH_STATUS_SYNCS.reset(token)
        _DEFERRED_WORKER_TERMINATIONS.reset(termination_token)
        _DEFERRED_AUTO_COMMIT_ARCHIVES.reset(archive_token)
        _DEFERRED_ACTIVITY_EVENTS.reset(activity_token)

    if operation_error is not None:
        # The mutation did not return successfully, so this wrapper has no
        # proof that its canonical/status, archive, or activity intents match a
        # committed runtime transition.  Exact process-generation termination
        # is the sole exception: it is compensating cleanup that must not be
        # skipped just because the mutation raised after scheduling it.
        _confirm_deferred_worker_terminations(deferred_terminations)
        raise operation_error

    side_effect_changed = _flush_deferred_runtime_side_effects(
        config,
        dispatch_status_syncs=deferred,
        worker_terminations=deferred_terminations,
        auto_commit_archives=deferred_archives,
        activity_events=deferred_activity_events,
    )
    return changed or side_effect_changed




MISSING_HANDOFF_EXIT_REASON = (
    "Owner worker exited cleanly after preparing a PR head but never advanced the "
    "task to review/handoff."
)

GOVERNANCE_ASSIGNMENT_EVENT_TYPES = frozenset({"assign", "task_reassigned"})
GOVERNANCE_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "assign",
        "start",
        "progress",
        "note",
        "reopen",
        "handoff",
        "review_approved",
        "blocker",
        "done",
        "supersede",
        "superseded",
        "cancel",
        "cancelled",
        "canceled",
        "task_reassigned",
    }
)
GOVERNANCE_TERMINAL_EVENT_TYPES = frozenset(
    {"done", "supersede", "superseded", "cancel", "cancelled", "canceled"}
)
GOVERNANCE_TERMINAL_TASK_STATUSES = frozenset(
    {"done", "superseded", "cancelled", "canceled"}
)


def _ai_status_activity_event_id_matches(event: Mapping[str, Any]) -> bool:
    event_id = str(event.get("event_id") or "").strip()
    if not event_id.startswith("ai-status-event-"):
        return False
    payload = dict(event)
    payload.pop("event_id", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return event_id == "ai-status-event-" + hashlib.sha256(encoded).hexdigest()


def _governance_event_after_worker_start(
    worker: Mapping[str, Any],
    event: Mapping[str, Any],
) -> bool:
    started_at = _parse_iso_utc(
        str(
            worker.get("lease_acquired_at")
            or worker.get("runner_started_at")
            or worker.get("started_at")
            or ""
        )
    )
    event_at = _parse_iso_utc(str(event.get("ts") or ""))
    return started_at is not None and event_at is not None and event_at >= started_at


def _latest_task_governance_event(
    worker: Mapping[str, Any],
    activity_events: list[dict[str, Any]] | None,
    *,
    event_types: frozenset[str],
) -> dict[str, Any] | None:
    task_id = str(worker.get("task_id") or "").strip()
    if not task_id:
        return None
    for event in reversed(activity_events or []):
        if (
            str(event.get("task_id") or "").strip() == task_id
            and str(event.get("type") or "") in event_types
            and _governance_event_after_worker_start(worker, event)
        ):
            return event
    return None


def status_event_matches_worker_process(
    event: Mapping[str, Any] | None,
    worker: Mapping[str, Any],
) -> bool:
    """Return whether a canonical status event was emitted by this exact run."""

    identity = worker_process_identity(worker)
    if identity is None or not isinstance(event, Mapping):
        return False
    command = event.get("status_command")
    lease = command.get("worker_lease") if isinstance(command, Mapping) else None
    return isinstance(lease, Mapping) and dict(lease) == identity


def active_worker_governance_lease_decision(
    config: dict[str, Any],
    worker: Mapping[str, Any],
    task: Mapping[str, Any] | None,
    *,
    activity_events: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Classify whether canonical governance truth may end this active lease.

    Absence, ambiguity, an ordinary ``assign``/``note``/review transition, or a
    concurrent task mutation all preserve the process. Only terminal lifecycle
    truth or the latest exact canonical ``task_reassigned`` event can authorize
    responsibility transfer here.
    """

    settings = ready_dispatch_settings(config)
    done_statuses = normalized_status_set(
        settings.get("dependency_done_statuses"),
        ["done"],
    ) | GOVERNANCE_TERMINAL_TASK_STATUSES
    if isinstance(task, Mapping) and str(task.get("status") or "").lower() in done_statuses:
        return {
            "action": "terminate",
            "reason_code": "terminal_task_truth",
            "source_event_id": None,
            "source_event_type": None,
        }

    latest_lifecycle = _latest_task_governance_event(
        worker,
        activity_events,
        event_types=GOVERNANCE_LIFECYCLE_EVENT_TYPES,
    )
    if status_event_matches_worker_process(latest_lifecycle, worker):
        return {
            "action": "terminate",
            "reason_code": "exact_worker_lifecycle_transition",
            "source_event_id": latest_lifecycle.get("event_id"),
            "source_event_type": latest_lifecycle.get("type"),
        }
    if task is None:
        if (
            latest_lifecycle is not None
            and str(latest_lifecycle.get("type") or "") in GOVERNANCE_TERMINAL_EVENT_TYPES
            and _ai_status_activity_event_id_matches(latest_lifecycle)
        ):
            return {
                "action": "terminate",
                "reason_code": "terminal_activity_truth",
                "source_event_id": latest_lifecycle.get("event_id"),
                "source_event_type": latest_lifecycle.get("type"),
            }
        return {
            "action": "preserve",
            "reason_code": "missing_or_ambiguous_task_truth",
            "source_event_id": latest_lifecycle.get("event_id") if latest_lifecycle else None,
            "source_event_type": latest_lifecycle.get("type") if latest_lifecycle else None,
            "producer_event_matches_process": status_event_matches_worker_process(
                latest_lifecycle,
                worker,
            ),
        }

    latest_assignment = _latest_task_governance_event(
        worker,
        activity_events,
        event_types=GOVERNANCE_ASSIGNMENT_EVENT_TYPES,
    )
    if latest_assignment is None or latest_assignment.get("type") != "task_reassigned":
        return {
            "action": "preserve",
            "reason_code": "governance_only_transition",
            "source_event_id": latest_lifecycle.get("event_id") if latest_lifecycle else None,
            "source_event_type": latest_lifecycle.get("type") if latest_lifecycle else None,
            "producer_event_matches_process": status_event_matches_worker_process(
                latest_lifecycle,
                worker,
            ),
        }
    validated_assignment = rewrite_task_machine.validate_assignment_activity_event(
        latest_assignment
    )
    if validated_assignment is None:
        return {
            "action": "preserve",
            "reason_code": "invalid_reassignment_evidence",
            "source_event_id": latest_assignment.get("event_id"),
            "source_event_type": latest_assignment.get("type"),
        }

    current_owner = canonical_agent_name(config, str(task.get("owner") or ""))
    current_reviewer = canonical_agent_name(config, str(task.get("reviewer") or ""))
    new_owner = canonical_agent_name(config, validated_assignment.new_owner)
    new_reviewer = canonical_agent_name(config, validated_assignment.new_reviewer)
    current_generation = task_generation(task)
    if (
        current_owner.casefold() != new_owner.casefold()
        or current_reviewer.casefold() != new_reviewer.casefold()
        or (
            validated_assignment.generation is not None
            and current_generation != validated_assignment.generation
        )
    ):
        return {
            "action": "preserve",
            "reason_code": "concurrent_assignment_mutation",
            "source_event_id": latest_assignment.get("event_id"),
            "source_event_type": latest_assignment.get("type"),
        }

    worker_actor = canonical_agent_name(
        config,
        display_name_for(
            config,
            str(
                worker.get("logical_agent_id")
                or worker.get("agent_id")
                or worker.get("provider")
                or ""
            ),
        ),
    )
    dispatch_reason = str((worker.get("request_snapshot") or {}).get("reason") or "")
    if dispatch_reason == REASON_REVIEW_READY:
        old_actor = canonical_agent_name(
            config,
            validated_assignment.old_reviewer,
        )
        new_actor = new_reviewer
        role = "reviewer"
    else:
        old_actor = canonical_agent_name(
            config,
            validated_assignment.old_owner,
        )
        new_actor = new_owner
        role = "owner"
    if (
        not worker_actor
        or worker_actor.casefold() != old_actor.casefold()
        or worker_actor.casefold() == new_actor.casefold()
    ):
        return {
            "action": "preserve",
            "reason_code": "reassignment_does_not_move_worker_role",
            "source_event_id": latest_assignment.get("event_id"),
            "source_event_type": latest_assignment.get("type"),
        }
    return {
        "action": "terminate",
        "reason_code": f"exact_{role}_reassignment",
        "source_event_id": latest_assignment.get("event_id"),
        "source_event_type": latest_assignment.get("type"),
    }


def record_worker_governance_lease_guard(
    config: dict[str, Any],
    worker: dict[str, Any],
    task: Mapping[str, Any] | None,
    decision: Mapping[str, Any],
) -> bool:
    """Record one deduplicated runtime/audit observation without ending work."""

    identity = worker_process_identity(worker)
    observed = {
        "action": str(decision.get("action") or "preserve"),
        "reason_code": str(decision.get("reason_code") or "governance_only_transition"),
        "task_status": str(task.get("status") or "missing") if isinstance(task, Mapping) else "missing",
        "task_last_update": str(task.get("last_update") or "") if isinstance(task, Mapping) else None,
        "source_event_id": decision.get("source_event_id"),
        "source_event_type": decision.get("source_event_type"),
        "producer_event_matches_process": bool(decision.get("producer_event_matches_process")),
        "process_generation": identity.get("process_generation") if identity else None,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            observed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    previous = worker.get("governance_lease_guard")
    if isinstance(previous, Mapping) and previous.get("fingerprint") == fingerprint:
        return False
    recorded_at = utc_now()
    worker["governance_lease_guard"] = {
        **observed,
        "fingerprint": fingerprint,
        "recorded_at": recorded_at,
    }
    termination_pending = observed["reason_code"].endswith(
        "termination_pending_confirmation"
    )
    write_activity_log(
        config,
        {
            "type": (
                "worker_governance_lease_termination_deferred"
                if termination_pending
                else "worker_governance_lease_preserved"
            ),
            "task_id": worker.get("task_id"),
            "provider": worker.get("provider"),
            "worker_run_id": worker.get("run_id"),
            "queue_event_id": worker.get("queue_event_id"),
            "pid": identity.get("pid") if identity else worker.get("pid"),
            "pid_start_ticks": identity.get("pid_start_ticks") if identity else worker.get("pid_start_ticks"),
            "process_generation": identity.get("process_generation") if identity else worker.get("process_generation"),
            "task_status": observed["task_status"],
            "source_event_id": observed["source_event_id"],
            "source_event_type": observed["source_event_type"],
            "producer_event_matches_process": observed["producer_event_matches_process"],
            "reason_code": observed["reason_code"],
            "message": (
                (
                    "Kept the worker nonterminal while its exact process-generation "
                    "termination waits for post-admission confirmation"
                    if termination_pending
                    else "Preserved the healthy active worker lease across a governance-only task transition"
                )
                + f" ({observed['reason_code']})."
            ),
        },
    )
    return True


def worker_prepared_review_head(worker: dict[str, Any]) -> bool:
    """True for an owner run that pushed a PR head and then exited cleanly.

    That shape is not a plain provider failure: the run did the work and only
    skipped the handoff transition. Redispatching the same owner reproduces the
    same clean exit forever, which is the observed token loop.

    A PR URL scraped from arbitrary provider prose is not proof that this worker
    prepared this task's head: task briefs, gh output, and evidence files can all
    mention PR URLs. Only a structured result payload (or a future explicit
    prepared-head flag) can trigger the missing-handoff blocker. Finalize
    dispatches are also excluded: a review-approved owner may cleanly exit while
    waiting for auto-merge, and that must not move the task to blocked.
    """
    if not str(worker.get("pr_url") or "").strip():
        return False
    if worker.get("prepared_review_head") is not True and str(
        worker.get("pr_url_source") or ""
    ) != "result_payload":
        return False
    dispatch_reason = str((worker.get("request_snapshot") or {}).get("reason") or "").strip()
    return dispatch_reason in {
        REASON_OWNED_READY,
        REASON_OWNED_IN_PROGRESS,
    }


def owner_worker_canonical_handoff_status(
    config: dict[str, Any],
    worker: dict[str, Any],
    task: dict[str, Any] | None,
) -> str | None:
    """Return the canonical outcome reached by this exact owner worker.

    Once an owned implementation run advances its task to review, reviewer
    approval, or done, that task transition is the worker's durable outcome.
    Failure-log scanning and assignment mismatch handling must not reinterpret
    the old owner process as a missing result after responsibility has moved to
    the reviewer.
    """

    if not isinstance(task, dict):
        return None
    dispatch_reason = str(
        (worker.get("request_snapshot") or {}).get("reason") or ""
    ).strip()
    if dispatch_reason not in {REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS}:
        return None
    worker_actor = display_name_for(
        config,
        str(worker.get("logical_agent_id") or worker.get("agent_id") or worker.get("provider") or ""),
    ).strip()
    owner = str(task.get("owner") or "").strip()
    if not worker_actor or worker_actor != owner:
        return None

    settings = ready_dispatch_settings(config)
    outcome_statuses = (
        normalized_status_set(settings.get("review_statuses"), ["review"])
        | normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
        | normalized_status_set(settings.get("dependency_done_statuses"), ["done"])
    )
    task_status = str(task.get("status") or "").strip().lower()
    return task_status if task_status in outcome_statuses else None


def _prepare_missing_handoff_blocker_locked(
    config: dict[str, Any],
    worker: dict[str, Any],
) -> dict[str, Any] | None:
    """Record an actionable missing-handoff blocker for a prepared-but-unhanded task."""
    if not config.get("paths", {}).get("status_file"):
        return None

    task_id = str(worker.get("task_id") or "").strip()
    if not task_id:
        return None
    owner_agent = display_name_for(config, str(worker.get("agent_id") or worker.get("provider") or "")).strip()
    if not owner_agent:
        return None

    status = load_status(config)
    if status.get("status_activity_outbox") not in (None, {}, []):
        return None
    task = task_index_from_status(config, status).get(task_id)
    if not task:
        return None
    if str(task.get("owner") or "").strip() != owner_agent:
        return None

    reviewer = str(task.get("reviewer") or "").strip()
    waiting_for = reviewer or owner_agent
    if any(
        str(blocker.get("task_id") or "") == task_id
        and str(blocker.get("status") or "") == "open"
        and str(blocker.get("blocker_kind") or "") == "missing_handoff"
        for blocker in (status.get("blockers") or [])
    ):
        return None

    pr_url = str(worker.get("pr_url") or "").strip()
    timestamp = utc_now()
    message = (
        f"{owner_agent} prepared {pr_url or 'a PR head'} for {task_id} and exited without "
        f"moving the task to review/handoff. Redispatch is suspended to stop an "
        f"owned_in_progress_dispatch loop: confirm the prepared head, then hand off to "
        f"{waiting_for} (or reopen the task) through scripts/ai-status.sh."
    )

    try:
        task["status"] = rewrite_task_machine.transition(
            task.get("status"),
            rewrite_task_machine.TaskAction.BLOCK,
        ).value
    except rewrite_task_machine.TransitionError:
        return None
    task["waiting_for"] = waiting_for
    task["last_update"] = timestamp
    task["next"] = message

    status.setdefault("blockers", []).append(
        {
            "task_id": task_id,
            "owner": owner_agent,
            "waiting_for": waiting_for,
            "message": message,
            "status": "open",
            "created_at": timestamp,
            "blocker_kind": "missing_handoff",
            "pr_url": pr_url or None,
            "worker_run_id": worker.get("run_id"),
        }
    )

    event = {
        "event_id": "supervisor-missing-handoff-"
        + hashlib.sha256(
            f"{task_id}\0{timestamp}\0{owner_agent}\0{pr_url}".encode("utf-8")
        ).hexdigest(),
        "ts": timestamp,
        "agent": "Orchestrator",
        "type": "task_missing_handoff_blocked",
        "task_id": task_id,
        "target_agent": owner_agent,
        "waiting_for": waiting_for,
        "pr_url": pr_url or None,
        "message": message,
    }
    status["status_activity_outbox"] = _status_activity_outbox([event])
    write_status(config, status, source="supervisor-missing-handoff")
    return event


def record_missing_handoff_blocker(config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any] | None:
    if not config.get("paths", {}).get("status_file"):
        return None
    status_path = config_path(config, "status_file")
    with canonical_task_state_lock_file(
        status_path,
        shared=False,
        nonblocking=False,
    ):
        event = _prepare_missing_handoff_blocker_locked(config, worker)
    if event is None:
        return None
    sync_status_pipeline(config)
    write_activity_log(
        config,
        {
            "type": "task_missing_handoff_blocked",
            "provider": worker.get("provider"),
            "task_id": event["task_id"],
            "message": event["message"],
            "worker_run_id": worker.get("run_id"),
            "pr_url": event.get("pr_url"),
        },
    )
    return event


def _prepare_failure_loop_blocker_locked(
    config: dict[str, Any],
    *,
    task_id: str,
    message: str,
) -> dict[str, Any] | None:
    """Record an actionable failure-loop hold for a task that keeps failing
    even after ``reconcile_failure_loops`` already tried reassigning it.

    Mirrors ``_prepare_missing_handoff_blocker_locked``'s shape: same
    outbox-clear precondition, same BLOCK transition + ``waiting_for`` +
    ``next`` fields, same open-blocker de-dup (by ``blocker_kind``) so a
    repeat call is a no-op instead of stacking duplicate blockers.
    """

    if not config.get("paths", {}).get("status_file"):
        return None

    status = load_status(config)
    if status.get("status_activity_outbox") not in (None, {}, []):
        return None
    task = task_index_from_status(config, status).get(task_id)
    if not task:
        return None
    if str(task.get("waiting_for") or "").strip():
        return None
    if any(
        str(blocker.get("task_id") or "") == task_id
        and str(blocker.get("status") or "") == "open"
        and str(blocker.get("blocker_kind") or "") == "failure_loop"
        for blocker in (status.get("blockers") or [])
    ):
        return None

    timestamp = utc_now()
    try:
        task["status"] = rewrite_task_machine.transition(
            task.get("status"),
            rewrite_task_machine.TaskAction.BLOCK,
        ).value
    except rewrite_task_machine.TransitionError:
        return None
    task["waiting_for"] = "Human/Ops"
    task["last_update"] = timestamp
    task["next"] = message

    status.setdefault("blockers", []).append(
        {
            "task_id": task_id,
            "owner": task.get("owner"),
            "waiting_for": "Human/Ops",
            "message": message,
            "status": "open",
            "created_at": timestamp,
            "blocker_kind": "failure_loop",
        }
    )

    event = {
        "event_id": "supervisor-failure-loop-"
        + hashlib.sha256(f"{task_id}\0{timestamp}".encode("utf-8")).hexdigest(),
        "ts": timestamp,
        "agent": "Orchestrator",
        "type": "task_failure_loop_blocked",
        "task_id": task_id,
        "waiting_for": "Human/Ops",
        "message": message,
    }
    status["status_activity_outbox"] = _status_activity_outbox([event])
    write_status(config, status, source="supervisor-failure-loop")
    return event


def record_failure_loop_blocker(
    config: dict[str, Any],
    *,
    task_id: str,
    message: str,
) -> dict[str, Any] | None:
    status_path = config_path(config, "status_file")
    with canonical_task_state_lock_file(
        status_path,
        shared=False,
        nonblocking=False,
    ):
        event = _prepare_failure_loop_blocker_locked(
            config, task_id=task_id, message=message
        )
    if event is None:
        return None
    sync_status_pipeline(config)
    write_activity_log(
        config,
        {
            "type": "task_failure_loop_blocked",
            "task_id": event["task_id"],
            "message": event["message"],
        },
    )
    return event


def ownerless_in_progress_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(ready_dispatch_settings(config).get("ownerless_in_progress", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault(
        "owner_dispatch_reasons",
        [REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS],
    )
    settings.setdefault("max_transitions_per_tick", 4)
    settings.setdefault("merge_search_limit", 200)
    return settings


def task_ids_with_active_workers(config: dict[str, Any], state: dict[str, Any]) -> set[str]:
    """Task ids a live worker still owns; reconciliation must never touch them."""
    active_statuses = {
        str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])
    }
    busy: set[str] = set()
    for worker in (state.get("workers", {}) or {}).values():
        if not isinstance(worker, dict):
            continue
        task_id = str(worker.get("task_id") or "").strip()
        if not task_id:
            continue
        if worker.get("status") in active_statuses or pid_is_alive(worker.get("pid")):
            busy.add(task_id)
    return busy


def task_ids_with_open_queue_records(state: dict[str, Any]) -> set[str]:
    """Task ids with an undelivered wake-up; a dispatch is still in flight."""
    open_statuses = {"queued", "pending", "started", "stalled", "retry_backoff"}
    pending: set[str] = set()
    for record in ((state.get("queue", {}) or {}).get("events", {}) or {}).values():
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or "").strip().lower() not in open_statuses:
            continue
        task_id = str(record.get("task_id") or "").strip()
        if task_id:
            pending.add(task_id)
    return pending


def latest_owner_worker_for_task(
    state: dict[str, Any],
    task_id: str,
    *,
    owner_reasons: set[str],
) -> dict[str, Any] | None:
    """Most recent worker record dispatched to implement ``task_id``."""
    candidates: list[dict[str, Any]] = []
    for worker in (state.get("workers", {}) or {}).values():
        if not isinstance(worker, dict):
            continue
        if str(worker.get("task_id") or "").strip() != task_id:
            continue
        snapshot = worker.get("request_snapshot")
        reason = str((snapshot or {}).get("reason") or "") if isinstance(snapshot, dict) else ""
        if reason not in owner_reasons:
            continue
        candidates.append(worker)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda worker: str(
            worker.get("last_event_at") or worker.get("runner_finished_at") or ""
        ),
    )


def _git_capture(repo_root: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_commit_is_ancestor(repo_root: Path, commit: str, ref: str) -> bool:
    """True only when git positively answers that ``commit`` is merged into ``ref``.

    ``merge-base --is-ancestor`` exits 1 for "not an ancestor" and 128 for an
    unknown object, so any non-zero exit and any transport failure is read as
    "not proven merged".
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", commit, ref],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def worker_delivery_head_commit(worker: dict[str, Any]) -> str | None:
    """The exact commit this worker's isolated worktree was last observed at.

    ``update_worker_commit_progress`` records the worker's own ``HEAD`` while it
    runs, so the final snapshot is the delivery head that worker produced. This
    is the only worker-side field that names a specific commit; ``pr_url`` is
    scraped from provider output and is not trustworthy (the live 2026-07-26
    state carried a malformed URL pointing at an unrelated PR).
    """
    snapshot = worker.get("work_progress_snapshot")
    if not isinstance(snapshot, dict):
        return None
    sha = str(snapshot.get("commit_sha") or "").strip().lower()
    return sha if re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


def worker_dispatch_started_at(worker: dict[str, Any]) -> datetime | None:
    """When this worker run began; the lower bound for work it can claim."""
    for field in ("lease_acquired_at", "runner_started_at", "started_at"):
        parsed = _parse_iso_utc(str(worker.get(field) or ""))
        if parsed is not None:
            return parsed
    return None


def worker_target_agent_display_name(config: dict[str, Any], worker: dict[str, Any]) -> str:
    """Canonical display name this worker was dispatched as, or ``""`` if unknown.

    ``display_name_for`` echoes an unregistered id back, so an id that the agent
    registry does not know is treated as unresolved rather than accepted as its
    own display name.
    """
    snapshot = worker.get("request_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    agents = config.get("agents", {}) or {}
    for raw in (
        worker.get("logical_agent_id"),
        snapshot.get("agent_id"),
        worker.get("agent_id"),
        worker.get("provider"),
    ):
        agent_id = normalize_agent_id(str(raw or ""))
        if not agent_id or agent_id not in agents:
            continue
        name = str(display_name_for(config, agent_id) or "").strip()
        if name:
            return name
    return ""


def merged_delivery_commits(
    config: dict[str, Any],
    task_id: str,
    *,
    delivery_head: str,
    since: str,
) -> dict[str, Any] | None:
    """Durable evidence that *this worker's* delivery already merged.

    ``task/<TASK-ID>`` branches are deleted by GitHub when their PR merges, so
    the branch ref is exactly what is missing in the merged case. The commit
    trailer enforced by ``.githooks/commit-msg`` survives the merge, but a
    trailer alone only proves the id was delivered at *some* point: a reopened
    or reassigned task still carries every commit from its earlier rounds.

    A governed delivery must preserve its reviewed head in the integration
    history.  Therefore only merge-commit/fast-forward ancestry is valid.
    Every git failure returns ``None``; absent linkage never reads as merged.
    """
    normalized_task_id = str(task_id or "").strip()
    head = str(delivery_head or "").strip().lower()
    since_value = str(since or "").strip()
    if not normalized_task_id or not since_value:
        return None
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        return None
    try:
        repo_root = config_path(config, "status_file").parent
    except (KeyError, TypeError):
        return None
    limit = str(int(ownerless_in_progress_settings(config).get("merge_search_limit", 200) or 200))

    bases: list[tuple[str, str]] = []
    for base in worktree_cleanup_settings(config).get("base_branches", []):
        for candidate in (f"origin/{base}", base):
            if _git_ref_exists(repo_root, candidate):
                bases.append((str(base), candidate))
                break

    for _base_name, candidate in bases:
        if not _git_commit_is_ancestor(repo_root, head, candidate):
            continue
        output = _git_capture(
            repo_root,
            [
                "log",
                "--format=%H",
                "-n",
                limit,
                "--fixed-strings",
                f"--grep=Task-ID: {normalized_task_id}",
                f"--since={since_value}",
                head,
            ],
        )
        if output is None:
            return None
        commits = [line.strip() for line in str(output).splitlines() if line.strip()]
        if not commits:
            continue
        return {
            "base_ref": candidate,
            "commits": commits[:10],
            "delivery_head": head,
            "merge_commit": _merge_commit_carrying_head(repo_root, head, candidate),
            "trailer_commits_since": since_value,
            "delivery_shape": "merge_ancestry",
        }

    return None


def _merge_commit_carrying_head(repo_root: Path, head: str, base_ref: str) -> str | None:
    """The oldest merge on the ancestry path from ``head`` into ``base_ref``.

    A PR merged with a merge commit yields the commit GitHub created; a
    fast-forward merge legitimately has none, so this is recorded for audit and
    is not itself a gate.
    """
    output = _git_capture(
        repo_root, ["rev-list", "--ancestry-path", "--merges", f"{head}..{base_ref}"]
    )
    commits = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    return commits[-1] if commits else None


def task_branch_has_unmerged_commits(
    config: dict[str, Any],
    task_id: str,
    base_ref: str = "",
    *,
    delivery_head: str | None = None,
) -> bool:
    """True when a surviving task branch carries work the delivery does not cover.

    A branch ahead of the integration base or delivery head still has unmerged
    work.  Git failure is read as unmerged so transport uncertainty never turns
    into a closeout claim.
    """
    branch = worker_task_branch(config, task_id)
    try:
        repo_root = config_path(config, "status_file").parent
    except (KeyError, TypeError):
        return True
    head = str(delivery_head or "").strip().lower()
    for ref in (branch, f"origin/{branch}"):
        if not _git_ref_exists(repo_root, ref):
            continue
        for start in [base_ref] + ([head] if head else []):
            if not str(start or "").strip():
                continue
            output = _git_capture(repo_root, ["rev-list", "--count", f"{start}..{ref}"])
            if output is None:
                return True
            try:
                if int(str(output).strip() or "0") > 0:
                    return True
            except ValueError:
                return True
    return False


def merged_owner_delivery_evidence(
    config: dict[str, Any],
    task_id: str,
    worker: dict[str, Any],
    *,
    owner: str,
) -> dict[str, Any] | None:
    """Evidence that *this* owner's *this* delivery merged and nothing remains.

    Fail-closed by construction: every gate below must be positively proven from
    worker state plus git history, and any missing linkage returns ``None`` so
    the task stays exactly where the existing ladders left it.
    """
    if str(worker.get("status") or "").strip().lower() != "completed":
        return None
    if not worker_runner_succeeded(worker):
        return None

    # Identity binding: the terminal worker must be the task's *current* owner.
    # A reassignment after dispatch leaves the latest owner-dispatch worker
    # pointing at the previous owner, which is not evidence about this owner.
    normalized_owner = str(owner or "").strip()
    target_agent = worker_target_agent_display_name(config, worker)
    if not normalized_owner or not target_agent or target_agent != normalized_owner:
        return None

    # Timestamp binding: without a dispatch time there is no window to attribute
    # merged commits to, so nothing can be claimed for this run.
    dispatched_at = worker_dispatch_started_at(worker)
    if dispatched_at is None:
        return None

    # Work binding: a rerun over an already-merged branch commits nothing. Only a
    # run that actually advanced its worktree can have produced this delivery.
    try:
        commit_progress_count = int(worker.get("commit_progress_count") or 0)
    except (TypeError, ValueError):
        return None
    last_commit_progress_at = _parse_iso_utc(str(worker.get("last_commit_progress_at") or ""))
    if commit_progress_count < 1 or last_commit_progress_at is None:
        return None

    delivery_head = worker_delivery_head_commit(worker)
    if not delivery_head:
        return None

    merged = merged_delivery_commits(
        config,
        task_id,
        delivery_head=delivery_head,
        since=_isoformat_utc(dispatched_at),
    )
    if not merged:
        return None
    if task_branch_has_unmerged_commits(
        config,
        task_id,
        str(merged.get("base_ref") or ""),
        delivery_head=delivery_head,
    ):
        return None
    return {
        "worker_run_id": worker.get("run_id"),
        "worker_status": worker.get("status"),
        "worker_target_agent": target_agent,
        "task_owner": normalized_owner,
        "dispatched_at": _isoformat_utc(dispatched_at),
        "commit_progress_count": commit_progress_count,
        "last_commit_progress_at": _isoformat_utc(last_commit_progress_at),
        "runner_finished_at": worker.get("runner_finished_at"),
        "delivery_head_commit": delivery_head,
        "delivery_shape": "merge_ancestry",
        "merged_base_ref": merged.get("base_ref"),
        "merge_commit": merged.get("merge_commit"),
        "merged_commits": merged.get("commits"),
        "trailer_commits_since": merged.get("trailer_commits_since"),
        # Recorded for the audit trail only. pr_url is scraped from provider
        # output and has been observed malformed and pointing at an unrelated
        # PR, so it is never a gate.
        "pr_url": worker.get("pr_url"),
        "pr_url_is_authoritative": False,
    }


def _prepare_ownerless_review_handoff_locked(
    config: dict[str, Any],
    *,
    task_id: str,
    owner: str,
    reviewer: str,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """Move a merged, ownerless ``in_progress`` task through the review handoff.

    Written with the same locked canonical transaction the lease recovery path uses,
    so the authoritative task-state journal receives the commit before the
    derived board projection is refreshed.
    """
    status = load_status(config)
    if status.get("status_activity_outbox") not in (None, {}, []):
        return None
    task = task_index_from_status(config, status).get(task_id)
    if not task:
        return None
    if str(task.get("status") or "").strip().lower() != "in_progress":
        return None
    if str(task.get("owner") or "").strip() != owner:
        return None
    if str(task.get("reviewer") or "").strip() != reviewer:
        return None

    timestamp = utc_now()
    try:
        task["status"] = rewrite_task_machine.transition(
            task.get("status"),
            rewrite_task_machine.TaskAction.HANDOFF,
        ).value
    except rewrite_task_machine.TransitionError:
        return None
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)

    handoffs_path = (config.get("schema", {}) or {}).get("handoffs_path", "handoffs")
    handoffs = status.setdefault(handoffs_path, [])
    if isinstance(handoffs, list):
        handoffs.append(
            {
                "task_id": task_id,
                "from": owner,
                "to": reviewer,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )

    event = {
        "event_id": "supervisor-ownerless-review-"
        + hashlib.sha256(f"{task_id}\0{timestamp}\0{message}".encode("utf-8")).hexdigest(),
        "ts": timestamp,
        "agent": "Orchestrator",
        "type": "task_ownerless_review_handoff",
        "task_id": task_id,
        "target_agent": reviewer,
        "owner": owner,
        "message": message,
        "evidence": evidence,
    }
    status["status_activity_outbox"] = _status_activity_outbox([event])
    write_status(config, status, source="supervisor-ownerless-review-handoff")
    return event


def reconcile_ownerless_in_progress_tasks(
    config: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """Resolve ``in_progress`` tasks whose owner worker already terminated.

    An owner worker that merges its delivery and exits leaves the task row at
    ``in_progress`` with no live worker. The ready dispatcher then reads that row
    as owned work and wakes the same owner again every cycle, forever, because
    there is nothing left for it to implement. This phase reads the terminal
    worker outcome plus durable git evidence and routes the task through the
    governed review handoff instead. Everything else -- live workers, in-flight
    dispatches, failed outcomes, and tasks without durable evidence -- is left
    exactly as it was for the existing ladders to own.

    The evidence is bound to one specific delivery by one specific owner: see
    ``merged_owner_delivery_evidence``. The latest owner-dispatch worker is the
    only candidate considered, and it must itself match the task's current owner,
    so a reopened or reassigned task cannot be moved to review on the strength of
    an earlier round's merged commits.
    """
    settings = ownerless_in_progress_settings(config)
    if not settings.get("enabled", True):
        return False
    if not config.get("paths", {}).get("status_file"):
        return False

    try:
        status = load_status(config)
    except (KeyError, RuntimeError, OSError):
        return False
    tasks = task_index_from_status(config, status)
    if not tasks:
        return False

    owner_reasons = {str(value) for value in settings.get("owner_dispatch_reasons", [])}
    live_task_ids = task_ids_with_active_workers(config, state)
    queued_task_ids = task_ids_with_open_queue_records(state)
    max_transitions = max(1, int(settings.get("max_transitions_per_tick", 4) or 1))

    counts = {"ownerless_in_progress_review_handoffs": 0}
    changed = False
    for task_id, task in tasks.items():
        if counts["ownerless_in_progress_review_handoffs"] >= max_transitions:
            break
        if str(task.get("status") or "").strip().lower() != "in_progress":
            continue
        if task_id in live_task_ids or task_id in queued_task_ids:
            continue
        owner = str(task.get("owner") or "").strip()
        reviewer = str(task.get("reviewer") or "").strip()
        if not owner or not reviewer or owner == reviewer:
            continue
        worker = latest_owner_worker_for_task(state, task_id, owner_reasons=owner_reasons)
        if worker is None:
            continue
        if str(worker.get("ownerless_reconciled_task_status") or "") == "review":
            continue
        evidence = merged_owner_delivery_evidence(
            config,
            task_id,
            worker,
            owner=owner,
        )
        if not evidence:
            continue

        message = (
            f"Supervisor reconciled {task_id} from the terminal worker outcome: {owner}'s delivery head "
            f"{str(evidence.get('delivery_head_commit') or '')[:12]} merged into "
            f"{evidence.get('merged_base_ref')} and no implementation remains, so the task "
            f"moves to review for {reviewer} instead of another owner redispatch."
        )
        status_path = config_path(config, "status_file")
        with canonical_task_state_lock_file(status_path, shared=False, nonblocking=False):
            event = _prepare_ownerless_review_handoff_locked(
                config,
                task_id=task_id,
                owner=owner,
                reviewer=reviewer,
                message=message,
                evidence=evidence,
            )
        if event is None:
            continue
        sync_status_pipeline(config)
        worker["ownerless_reconciled_task_status"] = "review"
        worker["ownerless_reconciled_at"] = event.get("ts")
        finalize_queue_event_record(config, state, worker, "completed")
        write_activity_log(
            config,
            {
                "type": "task_ownerless_review_handoff",
                "task_id": task_id,
                "target_agent": reviewer,
                "provider": worker.get("provider"),
                "worker_run_id": worker.get("run_id"),
                "message": message,
                "evidence": evidence,
            },
        )
        counts["ownerless_in_progress_review_handoffs"] += 1
        changed = True

    record_worker_runtime_measurement(
        config,
        state,
        "ownerless_in_progress_reconciliation",
        counts,
        emit_activity=bool(positive_runtime_counts(counts)),
    )
    return changed

def _persist_task_reassignment_locked(
    config: dict[str, Any],
    *,
    task_id: str,
    new_owner: str,
    new_reviewer: str,
    message: str,
    lifecycle_action: rewrite_task_machine.TaskAction | None = None,
    handoff_to: str | None = None,
    handoff_from: str | None = None,
    resolve_open_blockers: bool = False,
    resolve_open_handoffs: bool = False,
    expected_owner: str | None = None,
    expected_reviewer: str | None = None,
    expected_status: str | None = None,
    expected_generation: int | None = None,
) -> bool:
    status = load_status(config)
    if status.get("status_activity_outbox") not in (None, {}, []):
        return False
    tasks = status.get("tasks", []) or []
    timestamp = utc_now()
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        return False

    old_owner = str(task.get("owner") or "")
    old_reviewer = str(task.get("reviewer") or "")
    old_status = str(task.get("status") or "")
    old_generation = task_generation(task)
    if expected_owner is not None and old_owner != expected_owner:
        return False
    if expected_reviewer is not None and old_reviewer != expected_reviewer:
        return False
    if expected_status is not None and old_status != expected_status:
        return False
    if expected_generation is not None and old_generation != expected_generation:
        return False
    if (
        reviewer_is_explicit_human_gate(old_reviewer)
        and not reviewer_is_explicit_human_gate(new_reviewer)
    ):
        # Supervisor availability repair may move execution ownership, but it
        # cannot silently weaken an explicit human review gate. An intentional
        # operator reassignment remains available through governed ai-status.
        return False
    try:
        assignment = rewrite_task_machine.assignment_transition(
            old_owner,
            old_reviewer,
            new_owner,
            new_reviewer,
            actor="Orchestrator",
            reason=message,
            expected_owner=expected_owner,
            expected_reviewer=expected_reviewer,
        )
    except rewrite_task_machine.TransitionError:
        return False
    task["owner"] = assignment.new_owner
    task["reviewer"] = assignment.new_reviewer
    if lifecycle_action is not None:
        try:
            task["status"] = rewrite_task_machine.transition(
                old_status, lifecycle_action.value
            ).value
        except rewrite_task_machine.TransitionError:
            return False
        if task["status"] in {"todo", "in_progress"}:
            task.pop("waiting_for", None)
    task["last_update"] = timestamp
    task["generation"] = old_generation + 1
    task["next"] = message

    if resolve_open_blockers:
        for blocker in status.get("blockers", []) or []:
            if blocker.get("task_id") != task_id or blocker.get("status") == "resolved":
                continue
            blocker["status"] = "resolved"
            blocker["resolved_at"] = timestamp
            blocker["resolution_ref"] = f"recovery_reassignment:{task_id}"

    for handoff in status.get("handoffs", []) or []:
        if handoff.get("task_id") != task_id or handoff.get("status") == "done":
            continue
        if resolve_open_handoffs:
            handoff["status"] = "done"
            handoff["resolved_at"] = timestamp
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

    event = rewrite_task_machine.build_assignment_activity_event(
        task_id=task_id,
        timestamp=timestamp,
        assignment=assignment,
        old_generation=old_generation,
        new_generation=old_generation + 1,
    )
    status["status_activity_outbox"] = _status_activity_outbox([event])
    write_status(config, status, source="supervisor-reassignment")
    return True


def persist_task_reassignment(
    config: dict[str, Any],
    *,
    task_id: str,
    new_owner: str,
    new_reviewer: str,
    message: str,
    lifecycle_action: rewrite_task_machine.TaskAction | None = None,
    handoff_to: str | None = None,
    handoff_from: str | None = None,
    resolve_open_blockers: bool = False,
    resolve_open_handoffs: bool = False,
    expected_owner: str | None = None,
    expected_reviewer: str | None = None,
    expected_status: str | None = None,
    expected_generation: int | None = None,
) -> bool:
    status_path = config_path(config, "status_file")
    with canonical_task_state_lock_file(
        status_path,
        shared=False,
        nonblocking=False,
    ):
        applied = _persist_task_reassignment_locked(
            config,
            task_id=task_id,
            new_owner=new_owner,
            new_reviewer=new_reviewer,
            message=message,
            lifecycle_action=lifecycle_action,
            handoff_to=handoff_to,
            handoff_from=handoff_from,
            resolve_open_blockers=resolve_open_blockers,
            resolve_open_handoffs=resolve_open_handoffs,
            expected_owner=expected_owner,
            expected_reviewer=expected_reviewer,
            expected_status=expected_status,
            expected_generation=expected_generation,
        )
    if not applied:
        return False
    return sync_status_pipeline(config)


def assignment_terminal_unavailability(
    config: dict[str, Any],
    state: dict[str, Any],
    agent_name: str | None,
) -> str | None:
    """Return durable terminal unavailability, never a stale/unknown probe."""

    agent_id = normalize_agent_id(agent_name)
    if not agent_id or agent_id not in (config.get("agents", {}) or {}):
        return "unknown_agent"
    if agent_dispatch_capacity(config, agent_id) == 0:
        return "configured_zero_capacity"
    lane = delivery_lane_for_agent(config, agent_id)
    endpoints = [
        endpoint
        for endpoint in lane.endpoints
        if endpoint.endpoint_id
        and endpoint.provider_id
        and endpoint.account_id
        and endpoint.enabled
        and endpoint.can_auto_deliver
    ]
    if not endpoints:
        return "configured_no_delivery_endpoint"
    health = runtime_delivery_health(state)
    terminal_reasons: list[str] = []
    for delivery_endpoint in endpoints:
        endpoint = rewrite_provider_health.endpoint_health_entry(
            health, delivery_endpoint.endpoint_id
        )
        account_entry = rewrite_provider_health.account_health_entry(
            health, delivery_endpoint.account_id
        )
        if (
            account_entry.get("state")
            == rewrite_provider_health.DeliveryHealthState.RETRY_AFTER.value
            and str(account_entry.get("reason_kind") or "") == "quota_terminal"
        ):
            terminal_reasons.append(
                f"terminal_quota:{delivery_endpoint.provider_id}"
            )
            continue
        if (
            endpoint.get("state")
            == rewrite_provider_health.DeliveryHealthState.UNAVAILABLE.value
            and str(endpoint.get("reason_kind") or "") == "auth"
        ):
            terminal_reasons.append(
                f"terminal_auth:{delivery_endpoint.provider_id}"
            )
            continue
        # One physical endpoint that is healthy, stale, or retryable keeps the
        # logical lane recoverable in place.  Reassign only when every exact
        # endpoint is durably terminal.
        return None
    return terminal_reasons[0] if terminal_reasons else None


LOAD_BALANCE_REASON = "sustained_lane_saturation"


def assignment_saturated_recoverable(
    config: dict[str, Any],
    owner: str,
    *,
    agent_loads: Mapping[str, list[int]],
    fallback_candidates: list[str],
) -> str | None:
    """Return the load-balance reason when a fully-healthy owner lane is
    saturated and a configured fallback currently has spare capacity.

    This is deliberately narrow and never overlaps
    ``assignment_terminal_unavailability``: it only looks at current
    occupancy vs. ``agents.<id>.max_parallel``, never at health/auth/quota,
    and only returns non-None when a *configured* fallback (not an arbitrary
    roster agent) is presently under its own capacity. Callers must also
    hold this condition for a minimum duration before acting on it -- a
    momentarily full lane is normal dispatch noise, not a load-balance
    signal, and treating it as one would thrash ownership every cycle.
    """

    capacity = agent_dispatch_capacity(config, normalize_agent_id(owner))
    if capacity <= 0:
        return None
    if len(agent_loads.get(owner, [])) < capacity:
        return None
    for candidate in fallback_candidates:
        candidate_capacity = agent_dispatch_capacity(config, normalize_agent_id(candidate))
        if candidate_capacity <= 0:
            continue
        if len(agent_loads.get(candidate, [])) < candidate_capacity:
            return LOAD_BALANCE_REASON
    return None


LOAD_BALANCE_TRANSIENT_REASON = "owner_transiently_blocked"


def assignment_transiently_blocked_recoverable(
    config: dict[str, Any],
    task: dict[str, Any],
    owner: str,
    *,
    state: dict[str, Any],
    fallback_candidates: list[str],
) -> str | None:
    """Return the load-balance reason when the owner cannot take this task
    right now for ANY reason -- stale/unknown health cache, a short
    retry-after window, zero capacity -- while a configured fallback
    currently can.

    Unlike ``assignment_terminal_unavailability`` this does not require the
    block to be durable: ``agent_can_take_task`` already fails closed on
    anything short of a currently-healthy endpoint and account, which covers
    transient states that normally clear on their own within one probe
    cycle. Precisely because most of these self-heal quickly, callers MUST
    hold this condition for the same minimum duration as
    ``assignment_saturated_recoverable`` before acting -- reassigning away
    from an owner that was about to recover on its own just churns
    ownership for nothing.
    """

    if agent_can_take_task(config, owner, task, state=state):
        return None
    for candidate in fallback_candidates:
        if agent_can_take_task(config, candidate, task, state=state):
            return LOAD_BALANCE_TRANSIENT_REASON
    return None


def unavailable_assignment_fallback_refresh_targets(
    config: dict[str, Any],
    state: dict[str, Any],
    status: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Request bounded live evidence for possible reassignment targets.

    Recovery cannot safely bind a task to a fallback whose credential health
    is stale or unknown.  Without this demand path, however, only the broken
    incumbent is probed and every alternative can remain unknown forever.
    The normal observer applies the configured per-cycle bound.
    """

    settings = worker_reassignment_settings(config)
    if not settings.get("enabled", True):
        return []
    review_statuses = normalized_status_set(
        ready_dispatch_settings(config).get("review_statuses"), ["review"]
    )
    eligible_owner_statuses = {"todo", "in_progress", "review_approved", "blocked"}
    health = runtime_delivery_health(state)
    # Computed once: health_gate_for_endpoint is the same predicate plan/
    # delivery admission uses (rewrite_dispatch_admission), so a fallback
    # candidate's viability is judged by one rule instead of a second,
    # independently-maintained copy that can silently disagree (it did,
    # until OPS-HEALTH-GATE-ACCOUNT-PRECEDENCE-20260817 /
    # OPS-HEALTH-GATE-UNIFY-20260817).
    endpoint_records = _admission_health_records(health, "endpoints")
    account_records = _admission_health_records(health, "accounts")
    now = datetime.now(timezone.utc)
    targets: list[dict[str, str]] = []

    def demand_refresh(agent_name: str) -> None:
        lane = delivery_lane_for_agent(config, normalize_agent_id(agent_name))
        for endpoint in lane.endpoints:
            if (
                not endpoint.endpoint_id
                or not endpoint.provider_id
                or not endpoint.account_id
                or not endpoint.enabled
                or not endpoint.can_auto_deliver
            ):
                continue
            _reason, refresh_target = rewrite_dispatch_admission.health_gate_for_endpoint(
                endpoint_id=endpoint.endpoint_id,
                account_id=endpoint.account_id,
                endpoint_health=endpoint_records,
                account_health=account_records,
                now=now,
            )
            if refresh_target is None:
                continue
            target = {"scope": refresh_target.scope.value, "id": refresh_target.identifier}
            if target not in targets:
                targets.append(target)

    for task in status.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        task_status = str(task.get("status") or "").strip().lower()
        owner = canonical_agent_name(config, str(task.get("owner") or ""))
        reviewer = canonical_agent_name(config, str(task.get("reviewer") or ""))
        role = ""
        unavailable_actor = ""
        mapping: dict[str, Any] = {}
        excluded: set[str] = set()
        if task_status in review_statuses and not reviewer_is_explicit_human_gate(reviewer):
            if assignment_terminal_unavailability(config, state, reviewer):
                role = "reviewer"
                unavailable_actor = reviewer
                mapping = settings.get("reviewer_fallbacks", {}) or {}
                excluded = {owner, reviewer}
        elif task_status in eligible_owner_statuses:
            if assignment_terminal_unavailability(config, state, owner):
                role = "owner"
                unavailable_actor = owner
                mapping = settings.get("owner_fallbacks", {}) or {}
                excluded = {owner, reviewer}
        if not role:
            continue

        for candidate in reassignment_candidate_order(
            config,
            mapping,
            roots=[unavailable_actor],
            exclude=excluded,
        ):
            if role == "reviewer" and candidate.casefold() == owner.casefold():
                continue
            demand_refresh(candidate)
            if role != "owner" or reviewer_is_explicit_human_gate(reviewer):
                continue
            # plan_task_assignment_pair must also pair the reassigned owner
            # with a reviewer it can independently verify healthy. A stale
            # incumbent reviewer (nobody dispatched to it recently, not any
            # durable unavailability of its own) would otherwise never get
            # re-probed, and the planner would have a viable owner with no
            # viable reviewer to pair it with -- see reviewer_fallback_search_order.
            demand_refresh(reviewer)
            for reviewer_candidate in reviewer_fallback_search_order(
                config,
                settings,
                reviewer=reviewer,
                owner=owner,
                candidate_owner=candidate,
            ):
                if reviewer_candidate.casefold() == candidate.casefold():
                    continue
                demand_refresh(reviewer_candidate)
    return targets


def task_has_explicit_recovery_hold(
    status: Mapping[str, Any], task: Mapping[str, Any]
) -> bool:
    """Whether a blocked task carries an explicit non-provider wait.

    A durable quota/auth pause is enough to move a stranded execution
    assignment, but it must never erase a Human/Ops wait, an unresolved
    blocker, or a pending handoff.  Legacy rows with none of those markers are
    the only blocked rows eligible for the same recovery transition as a
    stranded todo/in-progress task.
    """

    if str(task.get("waiting_for") or "").strip():
        return True
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return True
    for blocker in status.get("blockers", []) or []:
        if not isinstance(blocker, Mapping) or str(blocker.get("task_id") or "") != task_id:
            continue
        if str(blocker.get("status") or "").strip().lower() not in {"resolved", "done", "closed"}:
            return True
    for handoff in status.get("handoffs", []) or []:
        if not isinstance(handoff, Mapping) or str(handoff.get("task_id") or "") != task_id:
            continue
        if str(handoff.get("status") or "").strip().lower() not in {"done", "resolved", "closed"}:
            return True
    return False


def reconcile_unavailable_assignments(
    config: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """Bounded recovery for assignments on durably unavailable lanes, plus
    optional load-balance recovery for saturated or transiently-blocked
    lanes.

    This is the sole automatic assignment mutation path.  The durable branch
    does not infer unavailability from a stale or missing probe.  The
    load-balance branch (gated by ``load_balance_settings``, off by default)
    is the only place occupancy vs. ``agents.<id>.max_parallel`` and a
    non-durable dispatch block both feed an assignment decision -- see
    ``assignment_saturated_recoverable`` and
    ``assignment_transiently_blocked_recoverable``.  Neither branch launches
    work; the normal planner consumes the committed assignment on a later
    pass.
    """

    settings = worker_reassignment_settings(config)
    if not settings.get("enabled", True):
        return False
    limit = max(0, int(settings.get("max_reassignments_per_cycle", 4) or 0))
    if limit == 0:
        return False
    load_balance = load_balance_settings(config)

    status = load_status(config)
    tasks = [task for task in status.get("tasks", []) if isinstance(task, dict)]
    active_statuses = normalized_status_set(
        ready_dispatch_settings(config).get("active_worker_statuses"), []
    )
    _active_agents, active_pairs = active_worker_indexes(state, active_statuses)
    task_map = {str(task.get("id") or ""): task for task in tasks}
    _pending_agents, pending_pairs, _pending_keys = outstanding_delivery_indexes(
        config, state, task_map=task_map
    )
    active_task_ids = {task_id for task_id, _agent in active_pairs if task_id}
    pending_agents_by_task: dict[str, set[str]] = {}
    for pending_task_id, pending_agent in pending_pairs:
        if not pending_task_id:
            continue
        canonical_pending_agent = canonical_agent_name(config, pending_agent)
        pending_agents_by_task.setdefault(pending_task_id, set()).add(
            canonical_pending_agent or str(pending_agent).strip()
        )
    eligible_owner_statuses = {"todo", "in_progress", "review_approved", "blocked"}
    # A load-balance move never touches a task that has already started under
    # its incumbent (in_progress) or that is on an explicit recovery hold
    # (blocked) -- only work that has not begun moves for capacity reasons.
    load_balance_eligible_statuses = {"todo", "review_approved"}
    review_statuses = normalized_status_set(
        ready_dispatch_settings(config).get("review_statuses"), ["review"]
    )
    agent_loads = agent_dispatch_loads(config, state, active_statuses, task_map=task_map)
    load_balance_watch = state.setdefault("load_balance_watch", {})
    for stale_task_id in [tid for tid in load_balance_watch if tid not in task_map]:
        load_balance_watch.pop(stale_task_id, None)
    changed = False
    actions: list[dict[str, Any]] = []

    for task in tasks:
        if len(actions) >= limit:
            break
        task_id = str(task.get("id") or "").strip()
        task_status = str(task.get("status") or "").strip().lower()
        # A live worker still owns the task lease even if its latest health
        # observation has become terminal.  Poll/reap that worker before
        # changing the canonical assignment.
        if not task_id or task_id in active_task_ids:
            continue
        if task_status == "blocked" and task_has_explicit_recovery_hold(status, task):
            continue
        owner = canonical_agent_name(config, str(task.get("owner") or ""))
        reviewer = canonical_agent_name(config, str(task.get("reviewer") or ""))

        role = ""
        unavailable_actor = ""
        unavailable_reason: str | None = None
        is_load_balance = False
        load_balance_owner_candidates: list[str] = []
        if task_status in review_statuses and not reviewer_is_explicit_human_gate(reviewer):
            unavailable_reason = assignment_terminal_unavailability(
                config, state, reviewer
            )
            if unavailable_reason:
                role = "reviewer"
                unavailable_actor = reviewer
        elif task_status in eligible_owner_statuses:
            unavailable_reason = assignment_terminal_unavailability(
                config, state, owner
            )
            if unavailable_reason:
                role = "owner"
                unavailable_actor = owner
            elif (
                load_balance["enabled"]
                and owner
                and task_status in load_balance_eligible_statuses
            ):
                fallback_candidates = reassignment_candidate_order(
                    config,
                    settings.get("owner_fallbacks", {}) or {},
                    roots=[owner],
                    exclude={owner, reviewer},
                )
                saturation_reason = assignment_saturated_recoverable(
                    config,
                    owner,
                    agent_loads=agent_loads,
                    fallback_candidates=fallback_candidates,
                ) or assignment_transiently_blocked_recoverable(
                    config,
                    task,
                    owner,
                    state=state,
                    fallback_candidates=fallback_candidates,
                )
                if saturation_reason is None:
                    load_balance_watch.pop(task_id, None)
                else:
                    watch_entry = load_balance_watch.get(task_id) or {}
                    first_seen_at = _parse_iso_utc(str(watch_entry.get("first_seen_at") or ""))
                    now_at = _parse_iso_utc(utc_now())
                    if first_seen_at is None:
                        load_balance_watch[task_id] = {
                            "first_seen_at": utc_now(),
                            "owner": owner,
                        }
                    elif (
                        now_at is not None
                        and (now_at - first_seen_at).total_seconds()
                        >= load_balance["min_saturated_seconds"]
                    ):
                        role = "owner"
                        unavailable_actor = owner
                        unavailable_reason = saturation_reason
                        is_load_balance = True
                        fallback_candidates.sort(
                            key=lambda name: (
                                agent_dispatch_capacity(config, normalize_agent_id(name))
                                - len(agent_loads.get(name, []))
                            ),
                            reverse=True,
                        )
                        load_balance_owner_candidates = fallback_candidates
        if not role or not unavailable_reason:
            continue

        # A delivery intent normally makes a task busy.  The exception is an
        # unleased intent targeting the exact actor that is now durably
        # unavailable: keeping that intent busy would prevent the sole
        # reassignment path from ever changing the assignment, while queue
        # admission would keep the same intent pending forever.  Once the
        # assignment is committed below, normal queue reconciliation retires
        # the old generation-bound intent as stale.  A pending intent for any
        # other actor still blocks recovery to avoid duplicate delivery.
        unavailable_actor_key = unavailable_actor.casefold()
        if any(
            pending_actor.casefold() != unavailable_actor_key
            for pending_actor in pending_agents_by_task.get(task_id, set())
            if pending_actor
        ):
            continue

        if role == "reviewer":
            candidates = reassignment_candidate_order(
                config,
                settings.get("reviewer_fallbacks", {}) or {},
                roots=[reviewer],
                exclude={owner, reviewer},
            )
            pair = plan_task_assignment_pair(
                config,
                task,
                state=state,
                fixed_owner=owner,
                preferred_reviewers=candidates,
                allowed_reviewers=candidates,
                excluded_reviewers={reviewer},
                require_owner_ready=False,
            )
        else:
            candidates = (
                load_balance_owner_candidates
                if is_load_balance
                else reassignment_candidate_order(
                    config,
                    settings.get("owner_fallbacks", {}) or {},
                    roots=[owner],
                    exclude={owner, reviewer},
                )
            )
            pair = plan_task_assignment_pair(
                config,
                task,
                state=state,
                owner_candidates=candidates,
                preferred_reviewers=[reviewer],
            )
        if pair is None:
            continue
        new_owner, new_reviewer = pair
        if is_load_balance:
            if unavailable_reason == LOAD_BALANCE_TRANSIENT_REASON:
                condition = "was blocked from auto-dispatch (unhealthy/stale probe/retry-after)"
            else:
                condition = "was full"
            message = (
                f"Load-balanced owner from {unavailable_actor} to {new_owner}: "
                f"lane {condition} for >= {load_balance['min_saturated_seconds']}s while "
                f"{new_owner} had spare capacity; planner will redispatch normally."
            )
        else:
            message = (
                f"Recovery reassigned {role} from {unavailable_actor} after "
                f"durable {unavailable_reason}; planner will redispatch normally."
            )
        if not persist_task_reassignment(
            config,
            task_id=task_id,
            new_owner=new_owner,
            new_reviewer=new_reviewer,
            message=message,
            lifecycle_action=(
                rewrite_task_machine.TaskAction.REOPEN
                if task_status == "blocked"
                else None
            ),
            handoff_from=unavailable_actor,
            handoff_to=new_reviewer if role == "reviewer" else None,
            expected_owner=owner,
            expected_reviewer=reviewer,
            expected_status=str(task.get("status") or ""),
            expected_generation=task_generation(task),
        ):
            continue
        load_balance_watch.pop(task_id, None)
        task["owner"] = new_owner
        task["reviewer"] = new_reviewer
        actions.append(
            {
                "task_id": task_id,
                "role": role,
                "from": unavailable_actor,
                "owner": new_owner,
                "reviewer": new_reviewer,
                "reason": unavailable_reason,
                "trigger": "load_balance" if is_load_balance else "durable_unavailable",
            }
        )
        changed = True

    return changed


def reconcile_failure_loops(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Bounded auto-governance for a task that keeps failing under its owner.

    Off by default (``worker_reassignment.failure_loop.enabled``).  Two-tier
    response, both reusing existing governed write paths so this adds no new
    mutation authority: the first ``max_auto_reassignments`` times a task
    hits the failure threshold within the window, it is hand-checked to the
    next configured ``owner_fallbacks`` candidate via the same
    ``plan_task_assignment_pair``/``persist_task_reassignment`` pipeline the
    load-balance mechanism uses -- on the theory the failure may be
    agent-specific.  If it keeps failing at that rate under a fresh owner
    too, further reassignment is unlikely to be the fix: the task is put on
    an explicit Human/Ops hold (``record_failure_loop_blocker``) instead of
    being bounced between agents forever, so it stops consuming capacity for
    nothing and surfaces for investigation rather than spinning silently.

    Never touches a task with a live active worker (that generation may yet
    succeed) or one already on any explicit hold.
    """

    settings = failure_loop_settings(config)
    if not settings["enabled"]:
        return False
    reassignment_settings = worker_reassignment_settings(config)
    if not reassignment_settings.get("enabled", True):
        return False

    status = load_status(config)
    tasks = [task for task in status.get("tasks", []) if isinstance(task, dict)]
    task_map = {str(task.get("id") or ""): task for task in tasks}
    active_statuses = normalized_status_set(
        ready_dispatch_settings(config).get("active_worker_statuses"), []
    )
    _active_agents, active_pairs = active_worker_indexes(state, active_statuses)
    active_task_ids = {task_id for task_id, _agent in active_pairs if task_id}
    eligible_statuses = {"todo", "in_progress"}

    watch = state.setdefault("failure_loop_watch", {})
    for stale_task_id in [tid for tid in watch if tid not in task_map]:
        watch.pop(stale_task_id, None)

    failure_counts = recent_task_failure_counts(
        config, window_seconds=settings["window_seconds"]
    )
    changed = False
    for task_id, count in failure_counts.items():
        if count < settings["max_failures_in_window"]:
            continue
        task = task_map.get(task_id)
        if not task:
            continue
        task_status = str(task.get("status") or "").strip().lower()
        if task_status not in eligible_statuses:
            continue
        if task_id in active_task_ids:
            continue
        if str(task.get("waiting_for") or "").strip():
            continue

        entry = watch.setdefault(task_id, {"auto_reassignments": 0})
        owner = canonical_agent_name(config, str(task.get("owner") or ""))
        reviewer = canonical_agent_name(config, str(task.get("reviewer") or ""))
        attempts_used = int(entry.get("auto_reassignments", 0))

        if attempts_used < settings["max_auto_reassignments"]:
            candidates = reassignment_candidate_order(
                config,
                reassignment_settings.get("owner_fallbacks", {}) or {},
                roots=[owner] if owner else [],
                exclude={owner, reviewer},
            )
            pair = plan_task_assignment_pair(
                config,
                task,
                state=state,
                owner_candidates=candidates,
                preferred_reviewers=[reviewer],
            )
            if pair is None:
                continue
            new_owner, new_reviewer = pair
            message = (
                f"Repeated failures ({count} in {settings['window_seconds']}s) under "
                f"{owner}; auto-reassigning to {new_owner} "
                f"(attempt {attempts_used + 1}/{settings['max_auto_reassignments']})."
            )
            if not persist_task_reassignment(
                config,
                task_id=task_id,
                new_owner=new_owner,
                new_reviewer=new_reviewer,
                message=message,
                handoff_from=owner,
                expected_owner=owner,
                expected_reviewer=reviewer,
                expected_status=str(task.get("status") or ""),
                expected_generation=task_generation(task),
            ):
                continue
            task["owner"] = new_owner
            task["reviewer"] = new_reviewer
            entry["auto_reassignments"] = attempts_used + 1
            changed = True
        else:
            message = (
                f"Repeated failures ({count} in {settings['window_seconds']}s) persisted "
                f"under {owner} even after {attempts_used} auto-reassignment(s); "
                f"holding for Human/Ops investigation."
            )
            if record_failure_loop_blocker(config, task_id=task_id, message=message) is not None:
                watch.pop(task_id, None)
                changed = True

    return changed






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


def request_for_worker(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
) -> DeliveryRequest | None:
    snapshot = worker.get("request_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("message"):
        return request_from_snapshot(snapshot)
    queue_event_id = worker.get("queue_event_id")
    if not queue_event_id:
        return None
    event = queue_event_by_id(state, str(queue_event_id))
    return build_request(config, event) if event is not None else None


def schedule_worker_retry(config: dict[str, Any], worker: dict[str, Any], reason: str) -> None:
    delay = retry_delay_seconds(config, worker)
    retry_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + delay, tz=timezone.utc)
    worker["status"] = "retry_backoff"
    worker["retry_count"] = int(worker.get("retry_count", 0)) + 1
    worker["next_retry_at"] = retry_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    worker["last_error"] = reason
    worker["last_event_at"] = utc_now()


def schedule_retry_from_worker_failure(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    reason: str,
) -> tuple[bool, bool]:
    retry = worker_retry_settings(config, worker.get("provider"))
    failure = classify_worker_failure(config, worker, reason)
    max_attempts = int(retry.get("max_attempts", 5))
    retry_count = int(worker.get("retry_count", 0))
    request = request_for_worker(config, state, worker)
    if request is None:
        return False, False
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

    return False, False


def retry_due_workers(
    config: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
) -> bool:
    changed = False
    for worker in list(state.get("workers", {}).values()):
        if worker.get("status") != "retry_backoff":
            continue
        next_retry_at = _parse_iso_utc(worker.get("next_retry_at"))
        if next_retry_at is None or next_retry_at > now:
            continue
        queue_event_id = str(worker.get("queue_event_id") or "")
        if not queue_event_id:
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Retry was due, but the original durable queue intent is missing.",
                    "worker_run_id": worker["run_id"],
                },
            )
            changed = True
            continue
        record = queue_status(state, queue_event_id)
        record["status"] = "queued"
        record.pop("lease_owner", None)
        record.pop("lease_expires_at", None)
        record.pop("run_id", None)
        record["retry_parent_run_id"] = worker.get("run_id")
        worker["status"] = "retry_queued"
        worker["last_event_at"] = utc_now()
        write_activity_log(
            config,
            {
                "type": "worker_retry_queued",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": "Retry returned to the durable delivery queue for normal launch revalidation.",
                "worker_run_id": worker.get("run_id"),
                "queue_event_id": queue_event_id,
            },
        )
        changed = True
    return changed


def poll_worker_observation_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    now: datetime,
    active_worker_statuses: set[str],
    poll_counts: dict[str, int],
) -> dict[str, Any]:
    """Observe one worker and apply lease/expiry effects as one Phase-4 stage.

    This is the first physical extraction from the historical ``poll_workers``
    god-function. The stage owns runtime markers, provider-log ingestion,
    per-worker commit/process progress, lease renewal, and expired-lease
    termination. The driver receives only the facts needed by later assignment,
    approval, stall, and completion stages.
    """
    changed = False
    marker_changed = update_worker_runtime_markers(worker)
    if marker_changed:
        poll_counts["marker_updates"] += 1
        changed = True
    update_from_log(config, worker)
    alive = pid_is_alive(worker.get("pid"))
    if (
        alive
        and worker.get("status") in active_worker_statuses
        and config.get("supervisor", {}).get("observe_worker_commit_progress", True)
    ):
        commit_state_changed, commit_progress_advanced = update_worker_commit_progress(
            worker,
            now,
        )
        if commit_state_changed:
            changed = True
        if commit_progress_advanced:
            poll_counts["commit_progress_updates"] += 1

    process_activity_advanced = False
    if alive and config.get("supervisor", {}).get("adaptive_stall_detection", True):
        previous_process_activity = worker.get("process_activity_snapshot")
        current_process_activity = worker_process_activity_snapshot(worker.get("pid"))
        process_activity_advanced = worker_process_activity_advanced(
            previous_process_activity if isinstance(previous_process_activity, dict) else None,
            current_process_activity,
        )
        if current_process_activity != previous_process_activity:
            worker["process_activity_snapshot"] = current_process_activity
            changed = True
        heartbeat_fresh = bool(worker.get("last_heartbeat_at")) and not worker_heartbeat_is_stale(
            config,
            worker,
            now,
        )
        if process_activity_advanced and heartbeat_fresh:
            progress_at = _isoformat_utc(now)
            worker["last_process_activity_at"] = progress_at
            worker["last_work_progress_at"] = progress_at
            changed = True
        else:
            process_activity_advanced = False

    if (
        alive
        and worker.get("status") in active_worker_statuses
        and worker.get("last_heartbeat_at")
        and worker_lease_can_renew(config, worker, now)
    ):
        refresh_worker_lease(config, worker, now)
        poll_counts["lease_refreshes"] += 1
        if worker.get("queue_event_id"):
            record = queue_status(state, worker["queue_event_id"])
            record["lease_owner"] = worker.get("run_id")
            record["lease_expires_at"] = queue_lease_expiry(config, now)

    stop = False
    if alive and worker.get("status") in active_worker_statuses and worker_lease_is_expired(config, worker, now):
        if not terminate_worker_pid(worker.get("pid")):
            # Deferred termination is confirmed after runtime admission. Keep
            # the worker nonterminal until a later cycle observes it gone.
            return {"changed": changed, "stop": True}
        worker["status"] = "failed"
        # Classify the expired signal before recording this failure event:
        # last_event_at is a fallback work-progress timestamp, so advancing it
        # first would relabel stale progress as a stale heartbeat.
        worker["last_error"] = (
            record_delivery_health_for_reaped_worker(config, state, worker)
            or (
                "Worker lease expired after observed work progress became stale."
                if worker_lease_requires_work_progress(config)
                and not worker_lease_progress_is_fresh(config, worker, now)
                else "Worker lease expired after heartbeat became stale."
            )
        )
        worker["last_event_at"] = utc_now()
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
        stop = True

    return {
        "changed": changed,
        "alive": alive,
        "process_activity_advanced": process_activity_advanced,
        "stop": stop,
    }


def poll_worker_approval_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    pending: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    alive: bool,
) -> dict[str, bool]:
    """Apply one worker's approval lifecycle and signal driver short-circuiting."""
    changed = False
    if pending:
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
                q_rec = queue_status(state, worker["queue_event_id"])
                q_rec["status"] = "waiting_approval"
                if worker.get("run_id"):
                    q_rec["run_id"] = worker.get("run_id")
                    q_rec["lease_owner"] = worker.get("run_id")
            changed = True
        return {"changed": changed, "stop": True}

    if worker.get("status") in {"waiting_approval", "suspended_approval"} and resolved:
        latest = resolved[-1]
        if latest.get("approval_id") != worker.get("last_approval_id"):
            worker["last_approval_id"] = latest.get("approval_id")
            if latest.get("decision") == "allow" and not alive:
                queue_event_id = str(worker.get("queue_event_id") or "").strip()
                queue_events = state.setdefault("queue", {}).setdefault("events", {})
                queue_record = queue_events.get(queue_event_id)
                if not queue_event_id or not isinstance(queue_record, dict):
                    worker["status"] = "failed"
                    worker["last_error"] = (
                        "Approved suspended worker cannot be relaunched because its durable queue intent is missing."
                    )
                    worker["last_event_at"] = utc_now()
                    return {"changed": True, "stop": True}
                queue_record["status"] = "queued"
                queue_record.pop("lease_owner", None)
                queue_record.pop("lease_expires_at", None)
                queue_record.pop("run_id", None)
                queue_record["approval_parent_run_id"] = worker.get("run_id")
                queue_record["approval_id"] = latest.get("approval_id")
                worker["status"] = "retry_queued"
                worker["deferred_action"] = None
                worker["deferred_tool_use"] = None
                worker["last_event_at"] = utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_approval_requeued",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": (
                            f"Returned approval {latest.get('approval_id')} to the durable queue "
                            "for normal launch revalidation."
                        ),
                        "worker_run_id": worker["run_id"],
                        "approval_id": latest.get("approval_id"),
                        "queue_event_id": queue_event_id,
                    },
                )
                return {"changed": True, "stop": True}
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
                finalize_queue_event_record(
                    config,
                    state,
                    worker,
                    "failed",
                    latest.get("note") or "Worker approval denied.",
                )
                return {"changed": True, "stop": True}
        # Preserve the incumbent dirty-state contract while a resolved worker
        # is normalized below, even when this approval was seen on a prior tick.
        changed = True

    current_status = worker.get("status")
    if current_status in {"waiting_approval", "suspended_approval"}:
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

    return {"changed": changed, "stop": False}


def poll_worker_stall_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    alive: bool,
    last_event_advanced: bool,
    process_activity_advanced: bool,
    now: datetime,
    stall_after: float,
) -> dict[str, bool]:
    """Handle recovery/stall transitions for a live worker as one stage."""
    if not alive:
        return {"changed": False, "stop": False}

    changed = False
    if worker.get("status") == "stalled" and (last_event_advanced or process_activity_advanced):
        worker["status"] = "running"
        worker["last_event_at"] = worker.get("last_event_at") or utc_now()
        write_activity_log(
            config,
            {
                "type": "worker_recovered",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": "Worker produced output or measurable process activity after being marked stalled; status restored to running.",
                "worker_run_id": worker["run_id"],
            },
        )
        console_log(
            f"worker recovered: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return {"changed": True, "stop": True}

    last_event_dt = _parse_iso_utc(str(worker.get("last_event_at") or ""))
    last_work_progress_dt = _parse_iso_utc(
        str(
            worker.get("last_work_progress_at")
            or worker.get("last_process_activity_at")
            or ""
        )
    )
    activity_timestamps = [
        value
        for value in (last_event_dt, last_work_progress_dt)
        if value is not None
    ]
    if process_activity_advanced and last_event_dt and (now - last_event_dt).total_seconds() >= stall_after:
        last_notice_dt = _parse_iso_utc(str(worker.get("last_stall_deferred_at") or ""))
        if last_notice_dt is None or (now - last_notice_dt).total_seconds() >= stall_after:
            worker["last_stall_deferred_at"] = _isoformat_utc(now)
            write_activity_log(
                config,
                {
                    "type": "worker_stall_deferred",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "No provider output, but fresh heartbeat and measurable process-tree activity were observed; stall action deferred.",
                    "worker_run_id": worker["run_id"],
                    "process_commands": (worker.get("process_activity_snapshot") or {}).get("commands", []),
                },
            )
            changed = True
    if activity_timestamps:
        stalled_for_seconds = (now - max(activity_timestamps)).total_seconds()
        if worker.get("status") == "stalled" and stalled_for_seconds >= stall_after * 2:
            if not terminate_worker_pid(worker.get("pid")):
                return {"changed": changed, "stop": True}
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = (
                "Worker had no output or measurable process activity for "
                f"{int(stalled_for_seconds)} seconds and was terminated for redispatch."
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
            console_log(
                f"worker terminated after extended stall: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            return {"changed": True, "stop": True}
        if stalled_for_seconds >= stall_after and worker.get("status") != "stalled":
            worker["status"] = "stalled"
            write_activity_log(
                config,
                {
                    "type": "worker_stalled",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": f"Worker appears stalled after {int(stall_after)} seconds without output or measurable process-tree activity.",
                    "worker_run_id": worker["run_id"],
                },
            )
            changed = True
    return {"changed": changed, "stop": True}


def poll_worker_failure_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
) -> dict[str, bool]:
    """Classify and apply one exited worker's provider failure response."""
    # A retry parent retains its original PID/log for evidence after the child
    # launches.  Never classify that stale log again: doing so can cool the
    # newly selected rotation slot and reassign the task while the retry child
    # is actively working.  The other statuses below are likewise inactive or
    # intentionally waiting outside the failure path.
    if worker.get("status") in {
        "completed",
        "failed",
        "waiting_approval",
        "suspended_approval",
        "reassigned",
        "retried",
        "retry_backoff",
        "superseded",
    }:
        return {"changed": False, "stop": True}
    failure_reason = None if worker_runner_succeeded(worker) else detect_worker_failure(worker)
    if not failure_reason:
        return {"changed": False, "stop": False}

    failure = classify_worker_failure(config, worker, failure_reason)
    failure_summary = summarize_failure_reason(
        failure_reason,
        str(worker.get("provider") or worker.get("agent_id") or ""),
    )
    summarized_reason = failure_summary.get("summary") or failure_reason
    failure_kind = str(failure.get("kind") or "")
    raw_ref = write_failure_evidence(
        config,
        worker=worker,
        reason=failure_reason,
        failure_kind=failure_kind,
    )
    console_log(
        f"worker failure: provider={worker.get('provider')} task={worker.get('task_id')} kind={failure.get('label')} transient={'yes' if failure.get('transient') else 'no'} reason={failure_reason}",
        quiet=SUPERVISOR_LOG_QUIET,
    )
    rotation_provider = str(worker.get("provider") or worker.get("agent_id") or "")
    rotation_retry = worker_retry_settings(config, rotation_provider)
    rotation_budget_left = int(worker.get("retry_count", 0)) < int(rotation_retry.get("max_attempts", 5))
    rotation_outcome = (
        maybe_rotate_provider_model(config, state, rotation_provider, failure_kind, failure_reason)
        if rotation_budget_left
        else "exhausted"
    )
    if rotation_outcome == "rotated":
        schedule_worker_retry(config, worker, summarized_reason)
        write_activity_log(
            config,
            {
                "type": "worker_retry_scheduled",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": (
                    f"Model rotation triggered for {rotation_provider} "
                    f"({failure.get('label')}); re-dispatching on the alternate model "
                    f"at {worker.get('next_retry_at')}: {summarized_reason}"
                ),
                "worker_run_id": worker["run_id"],
                "next_retry_at": worker.get("next_retry_at"),
                "raw_ref": raw_ref,
            },
        )
        return {"changed": True, "stop": True}

    record_delivery_health_failure(
        config,
        state,
        agent_id=str(worker.get("agent_id") or ""),
        failure_kind=failure_kind,
        detail=summarized_reason,
    )
    if (
        is_terminal_quota_failure_kind(failure_kind)
    ):
        worker["status"] = "failed"
        worker["last_error"] = summarized_reason
        worker["last_error_raw_ref"] = raw_ref
        worker["last_event_at"] = utc_now()
        write_activity_log(
            config,
            {
                "type": "worker_failed",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": summarized_reason,
                "worker_run_id": worker["run_id"],
                "pr_url": worker.get("pr_url"),
                "session_url": worker.get("session_url"),
                "raw_ref": raw_ref,
            },
        )
        # Preserve the incumbent raw failure reason on this terminal quota path.
        finalize_queue_event_record(config, state, worker, "failed", failure_reason)
        return {"changed": True, "stop": True}

    retry_exhausted = False
    if (
        failure_kind in {"transient", "capacity", "capacity_retryable"}
    ):
        handled, retry_changed = schedule_retry_from_worker_failure(
            config,
            state,
            worker,
            failure_reason,
        )
        if handled:
            return {"changed": bool(retry_changed), "stop": True}
        retry = worker_retry_settings(config, worker.get("provider"))
        retry_exhausted = worker_retry_attempt_index(worker) >= int(
            retry.get("max_attempts", 5)
        )

    worker["status"] = "failed"
    worker["last_error"] = summarized_reason
    worker["last_error_raw_ref"] = raw_ref
    worker["last_event_at"] = utc_now()
    write_activity_log(
        config,
        {
            "type": "worker_failed",
            "provider": worker.get("provider"),
            "task_id": worker.get("task_id"),
            "message": summarized_reason,
            "worker_run_id": worker["run_id"],
            "pr_url": worker.get("pr_url"),
            "session_url": worker.get("session_url"),
            "raw_ref": raw_ref,
        },
    )
    finalize_queue_event_record(config, state, worker, "failed", summarized_reason)
    if retry_exhausted:
        record_retry_exhausted_worker_terminal_outcome(
            config,
            worker,
            reason=summarized_reason,
        )
    return {"changed": True, "stop": True}


def poll_worker_completion_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    task_map: dict[str, dict[str, Any]],
    redispatch_statuses: set[str],
) -> dict[str, bool]:
    """Classify a cleanly exited worker and apply its terminal transition."""
    if worker.get("status") in {"completed", "failed", "waiting_approval", "suspended_approval"}:
        return {"changed": False, "stop": True}

    task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
    terminal_statuses = {
        str(value).lower()
        for value in ready_dispatch_settings(config).get(
            "worker_terminal_statuses",
            ["done", "review_approved"],
        )
    }
    if task_status in terminal_statuses:
        worker["status"] = "completed"
        worker["last_event_at"] = utc_now()
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
        return {"changed": True, "stop": True}

    if task_status in redispatch_statuses:
        if worker_prepared_review_head(worker):
            # A clean owner exit that already pushed the review head is a missing
            # handoff, not a provider failure. Reassigning or redispatching the
            # same owner reproduces the same clean exit every tick; surface the
            # concrete blocker instead and take the task out of owner dispatch.
            blocker = record_missing_handoff_blocker(config, worker)
            if blocker is not None:
                worker["status"] = "failed"
                worker["last_event_at"] = utc_now()
                worker["last_error"] = MISSING_HANDOFF_EXIT_REASON
                write_activity_log(
                    config,
                    {
                        "type": "worker_failed",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": MISSING_HANDOFF_EXIT_REASON,
                        "worker_run_id": worker["run_id"],
                        "pr_url": worker.get("pr_url"),
                        "session_url": worker.get("session_url"),
                    },
                )
                finalize_queue_event_record(
                    config, state, worker, "failed", MISSING_HANDOFF_EXIT_REASON
                )
                return {"changed": True, "stop": True}
        generic_failure_summary = summarize_failure_reason(
            GENERIC_WORKER_EXIT_REASON,
            str(worker.get("provider") or worker.get("agent_id") or ""),
        )
        raw_ref = write_failure_evidence(
            config,
            worker=worker,
            reason=GENERIC_WORKER_EXIT_REASON,
            failure_kind="generic_exit",
        )
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
        return {"changed": True, "stop": True}

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
    return {"changed": True, "stop": True}


def poll_worker_orphan_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    run_id: str,
    valid_queue_event_ids: set[str],
    task_map: dict[str, dict[str, Any]],
) -> dict[str, bool]:
    """Reap a dead worker whose queue record disappeared before observation."""
    queue_event_id = worker.get("queue_event_id")
    orphan_statuses = {"running", "waiting_approval", "retry_backoff", "stalled"}
    if (
        not queue_event_id
        or queue_event_id in valid_queue_event_ids
        or worker.get("status") not in orphan_statuses
        or pid_is_alive(worker.get("pid"))
    ):
        return {"changed": False, "stop": False}

    task_status = str(task_map.get(worker.get("task_id"), {}).get("status") or "").lower()
    state.setdefault("workers", {}).pop(run_id, None)
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
    return {"changed": True, "stop": True}


def poll_worker_assignment_stage(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    run_id: str,
    task_map: dict[str, dict[str, Any]],
    active_worker_statuses: set[str],
    alive: bool,
    activity_events: list[dict[str, Any]] | None = None,
    governance_activity_events: list[dict[str, Any]] | None = None,
) -> dict[str, bool]:
    """Apply redelivery and current-assignment lease rules."""
    changed = False
    task = task_map.get(str(worker.get("task_id") or ""))
    generation_fence_crossed = isinstance(
        task, Mapping
    ) and not worker_matches_current_task_generation(worker, task)
    handoff_status = owner_worker_canonical_handoff_status(config, worker, task)
    lease_guard_decision: dict[str, Any] | None = None
    if handoff_status is not None:
        if alive:
            terminal_statuses = normalized_status_set(
                ready_dispatch_settings(config).get("dependency_done_statuses"),
                ["done"],
            )
            if handoff_status in terminal_statuses:
                if not terminate_worker_process_generation(worker):
                    changed = record_worker_governance_lease_guard(
                        config,
                        worker,
                        task,
                        {
                            "action": "preserve",
                            "reason_code": (
                                "terminal_termination_pending_confirmation"
                                if worker_process_generation_is_current(worker)
                                else "terminal_process_identity_unproven"
                            ),
                            "source_event_id": None,
                            "source_event_type": None,
                        },
                    ) or changed
                    return {"changed": changed, "stop": True}
            elif not generation_fence_crossed:
                producer_event = _latest_task_governance_event(
                    worker,
                    governance_activity_events,
                    event_types=GOVERNANCE_LIFECYCLE_EVENT_TYPES,
                )
                producer_event_matches_process = status_event_matches_worker_process(
                    producer_event,
                    worker,
                )
                lease_guard_decision = {
                    "action": (
                        "terminate"
                        if producer_event_matches_process
                        else "preserve"
                    ),
                    "reason_code": (
                        "exact_worker_lifecycle_transition"
                        if producer_event_matches_process
                        else "canonical_review_handoff"
                    ),
                    "source_event_id": producer_event.get("event_id") if producer_event else None,
                    "source_event_type": producer_event.get("type") if producer_event else None,
                    "producer_event_matches_process": producer_event_matches_process,
                }
                if lease_guard_decision["action"] == "preserve":
                    changed = record_worker_governance_lease_guard(
                        config,
                        worker,
                        task,
                        lease_guard_decision,
                    ) or changed
        if not alive or handoff_status in normalized_status_set(
            ready_dispatch_settings(config).get("dependency_done_statuses"),
            ["done"],
        ):
            worker["status"] = "completed"
            worker["last_event_at"] = utc_now()
            worker.pop("last_error", None)
            finalize_queue_event_record(config, state, worker, "completed")
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": (
                        "Owner worker reached canonical task outcome "
                        f"{handoff_status}; review/finalize responsibility advanced."
                    ),
                    "worker_run_id": worker.get("run_id"),
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            return {"changed": True, "stop": True}

    if worker.get("queue_event_id") and not worker_matches_current_assignment(config, worker, task_map):
        if worker.get("status") == "superseded":
            return {"changed": False, "stop": True}
        decision = (
            {
                "action": "terminate",
                "reason_code": "task_generation_fence",
                "source_event_id": None,
                "source_event_type": None,
            }
            if generation_fence_crossed
            else lease_guard_decision
            or active_worker_governance_lease_decision(
                config,
                worker,
                task,
                activity_events=governance_activity_events,
            )
        )
        if alive and decision["action"] != "terminate":
            changed = record_worker_governance_lease_guard(
                config,
                worker,
                task,
                decision,
            ) or changed
        elif alive and not terminate_worker_process_generation(worker):
            changed = record_worker_governance_lease_guard(
                config,
                worker,
                task,
                {
                    **decision,
                    "action": "preserve",
                    "reason_code": (
                        "authorized_transition_termination_pending_confirmation"
                        if worker_process_generation_is_current(worker)
                        else "authorized_transition_process_identity_unproven"
                    ),
                },
            ) or changed
            return {"changed": changed, "stop": True}
        else:
            worker["status"] = "superseded"
            worker["last_event_at"] = utc_now()
            worker["last_error"] = "Worker superseded after exact task responsibility transition."
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
                    "queue_event_id": worker.get("queue_event_id"),
                    "process_generation": worker.get("process_generation"),
                    "governance_reason_code": decision.get("reason_code"),
                    "source_event_id": decision.get("source_event_id"),
                },
            )
            console_log(
                f"worker superseded: task={worker.get('task_id')} provider={worker.get('provider')} run={worker.get('run_id')}",
                quiet=SUPERVISOR_LOG_QUIET,
            )
            return {"changed": True, "stop": True}

    stale_assignment_statuses = {
        "retry_backoff",
        "stalled",
        "waiting_approval",
        "suspended_approval",
    }
    if (
        not alive
        and worker.get("queue_event_id")
        and worker.get("status") in stale_assignment_statuses
        and not worker_matches_current_assignment(config, worker, task_map)
    ):
        state.setdefault("workers", {}).pop(run_id, None)
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
        return {"changed": True, "stop": True}

    return {"changed": changed, "stop": False}


def poll_workers(
    config: dict[str, Any],
    state: dict[str, Any],
    activity_events: list[dict[str, Any]] | None = None,
    governance_activity_events: list[dict[str, Any]] | None = None,
) -> bool:
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
    changed = retry_due_workers(config, state, now) or changed
    poll_counts = {
        "marker_updates": 0,
        "commit_progress_updates": 0,
        "lease_refreshes": 0,
        "expired_lease_workers_failed": 0,
    }
    workers = state.setdefault("workers", {})
    for run_id, worker in list(workers.items()):
        previous_last_event_at = worker.get("last_event_at")
        orphan = poll_worker_orphan_stage(
            config,
            state,
            worker,
            run_id=run_id,
            valid_queue_event_ids=valid_queue_event_ids,
            task_map=task_map,
        )
        changed = bool(orphan["changed"]) or changed
        if orphan["stop"]:
            continue
        observation = poll_worker_observation_stage(
            config,
            state,
            worker,
            now=now,
            active_worker_statuses=active_worker_statuses,
            poll_counts=poll_counts,
        )
        changed = bool(observation["changed"]) or changed
        alive = bool(observation["alive"])
        process_activity_advanced = bool(observation["process_activity_advanced"])
        if observation["stop"]:
            continue
        assignment = poll_worker_assignment_stage(
            config,
            state,
            worker,
            run_id=run_id,
            task_map=task_map,
            active_worker_statuses=active_worker_statuses,
            alive=alive,
            activity_events=activity_events,
            governance_activity_events=governance_activity_events,
        )
        changed = bool(assignment["changed"]) or changed
        if assignment["stop"]:
            continue
        last_event_advanced = bool(
            previous_last_event_at
            and worker.get("last_event_at")
            and worker.get("last_event_at") > previous_last_event_at
        )
        approval = poll_worker_approval_stage(
            config,
            state,
            worker,
            pending=pending_by_run.get(worker["run_id"], []),
            resolved=resolved_by_run.get(worker["run_id"], []),
            alive=alive,
        )
        changed = bool(approval["changed"]) or changed
        if approval["stop"]:
            continue

        stall = poll_worker_stall_stage(
            config,
            state,
            worker,
            alive=alive,
            last_event_advanced=last_event_advanced,
            process_activity_advanced=process_activity_advanced,
            now=now,
            stall_after=stall_after,
        )
        changed = bool(stall["changed"]) or changed
        if stall["stop"]:
            continue

        failure = poll_worker_failure_stage(
            config,
            state,
            worker,
        )
        changed = bool(failure["changed"]) or changed
        if failure["stop"]:
            continue

        completion = poll_worker_completion_stage(
            config,
            state,
            worker,
            task_map=task_map,
            redispatch_statuses=redispatch_statuses,
        )
        changed = bool(completion["changed"]) or changed
    changed = cleanup_inactive_worker_worktrees(config, state) or changed
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


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    except OSError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left_resolved = left.resolve()
        right_resolved = right.resolve()
    except OSError:
        return False
    return _path_is_within(left_resolved, right_resolved) or _path_is_within(right_resolved, left_resolved)


def active_worker_workspace_roots(config: dict[str, Any], state: dict[str, Any]) -> set[Path]:
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_statuses.update(
        {"running", "started", "waiting_approval", "suspended_approval", "retry_backoff", "stalled"}
    )
    roots: set[Path] = set()
    for worker in state.get("workers", {}).values():
        if not isinstance(worker, dict):
            continue
        workspace_path = worker.get("workspace_path")
        if not workspace_path:
            continue
        status = str(worker.get("status") or "")
        if status not in active_statuses and not pid_is_alive(worker.get("pid")):
            continue
        try:
            roots.add(Path(str(workspace_path)).expanduser().resolve())
        except OSError:
            continue
    return roots


def _status_changed_paths(porcelain_status: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for line in porcelain_status.splitlines():
        if not line.strip():
            continue
        body = line[3:] if len(line) > 3 else line.strip()
        path = body.split(" -> ")[-1].strip().strip('"')
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _archive_dirty_worktree(
    worktree_path: Path,
    archive_root: Path,
    *,
    reason: str,
    max_file_bytes: int,
) -> Path | None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", worktree_path.name).strip("-") or "worktree"
    archive_dir = archive_root / f"{slug}-{timestamp}-{os.getpid()}"
    suffix = 1
    while archive_dir.exists():
        suffix += 1
        archive_dir = archive_root / f"{slug}-{timestamp}-{os.getpid()}-{suffix}"
    try:
        archive_dir.mkdir(parents=True)
    except OSError:
        return None

    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    status_proc = run_git(["status", "--porcelain", "--untracked-files=all"])
    diff_proc = run_git(["diff", "--binary"])
    staged_diff_proc = run_git(["diff", "--cached", "--binary"])
    untracked_proc = run_git(["ls-files", "--others", "--exclude-standard"])

    (archive_dir / "status.txt").write_text(status_proc.stdout or status_proc.stderr or "", encoding="utf-8")
    (archive_dir / "diff.patch").write_text(diff_proc.stdout or diff_proc.stderr or "", encoding="utf-8")
    (archive_dir / "diff-staged.patch").write_text(
        staged_diff_proc.stdout or staged_diff_proc.stderr or "",
        encoding="utf-8",
    )
    (archive_dir / "untracked-files.txt").write_text(
        untracked_proc.stdout or untracked_proc.stderr or "",
        encoding="utf-8",
    )

    copied: list[str] = []
    skipped: list[str] = []
    files_root = archive_dir / "files"
    for rel_path in _status_changed_paths(status_proc.stdout):
        source = worktree_path / rel_path
        if not source.exists() or not source.is_file():
            skipped.append(rel_path)
            continue
        try:
            size = source.stat().st_size
        except OSError:
            skipped.append(rel_path)
            continue
        if max_file_bytes > 0 and size > max_file_bytes:
            skipped.append(f"{rel_path}\ttoo_large:{size}")
            continue
        destination = files_root / rel_path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(rel_path)
        except OSError:
            skipped.append(rel_path)

    (archive_dir / "copied-files.txt").write_text("\n".join(copied) + ("\n" if copied else ""), encoding="utf-8")
    (archive_dir / "skipped-files.txt").write_text("\n".join(skipped) + ("\n" if skipped else ""), encoding="utf-8")
    manifest = {
        "archived_at": utc_now(),
        "worktree_path": str(worktree_path),
        "reason": reason,
        "status_returncode": status_proc.returncode,
        "copied_files": copied,
        "skipped_files": skipped,
    }
    (archive_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return archive_dir


def _merged_task_branches(repo_root: Path, base_branches: list[str]) -> set[str]:
    merged_branches: set[str] = set()
    for ref in base_branches:
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
    return merged_branches


def _remove_worker_worktree(
    repo_root: Path,
    worktree_path: Path,
    *,
    force: bool,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repo_root), "worktree", "remove"]
    if force:
        command.append("--force")
    command.append(str(worktree_path))
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _cleanup_registered_worker_worktrees(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    source: str,
    require_merged: bool,
    include_unregistered: bool = False,
    only_workspace_paths: set[Path] | None = None,
) -> bool:
    settings = worktree_cleanup_settings(config)
    if not settings["enabled"]:
        return False
    worktree_settings = worker_worktree_settings(config)
    if not worktree_settings.get("enabled", False):
        return False
    base_root = _worker_worktree_base_root(config, worktree_settings)
    if not base_root.exists():
        return False
    status_root = config_path(config, "status_file").parents[0]
    active_roots = active_worker_workspace_roots(config, state)
    live_paths = _scan_process_paths_in_root(base_root)
    max_removals = max(0, int(settings["max_removals_per_tick"]))
    archive_root = Path(os.path.expanduser(str(settings["archive_root"])))
    if not archive_root.is_absolute():
        archive_root = status_root / archive_root
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    if not isinstance(leases, dict):
        return False

    repository_roots: set[Path] = {
        worker_worktree_source_root(config, worktree_settings)
    }
    for lease in leases.values():
        if not isinstance(lease, dict) or not lease.get("source_root"):
            continue
        repository_roots.add(Path(str(lease["source_root"])).expanduser().resolve())
    if include_unregistered:
        for repository_id in ("execute_plans", "runtime_platform", "lean_engine"):
            repository_root = repository_local_path(config, repository_id)
            if repository_root is not None and repository_root.is_dir():
                repository_roots.add(repository_root.resolve())

    records_by_path: dict[Path, tuple[dict[str, str], Path]] = {}
    merged_by_root: dict[Path, set[str]] = {}
    for repository_root in repository_roots:
        if not repository_root.is_dir():
            continue
        if require_merged:
            merged_by_root[repository_root] = _merged_task_branches(
                repository_root, list(settings["base_branches"])
            )
        for record in _git_worktree_records(repository_root):
            wt_value = record.get("worktree")
            if not wt_value:
                continue
            try:
                wt_path = Path(wt_value).expanduser().resolve()
            except OSError:
                continue
            records_by_path[wt_path] = (record, repository_root)

    candidates: list[
        tuple[str | None, dict[str, Any], Path, str | None, Path]
    ] = []
    candidate_paths: set[Path] = set()
    normalized_only = {path.resolve() for path in only_workspace_paths} if only_workspace_paths else None
    for workspace_id, lease in list(leases.items()):
        if not isinstance(lease, dict):
            continue
        path_value = lease.get("path")
        if not path_value:
            continue
        try:
            wt_path = Path(str(path_value)).expanduser().resolve()
        except OSError:
            continue
        if not _path_is_within(wt_path, base_root):
            continue
        if normalized_only is not None and wt_path not in normalized_only:
            continue
        record_binding = records_by_path.get(wt_path)
        record = record_binding[0] if record_binding is not None else {}
        lease_source = Path(
            str(lease.get("source_root") or worker_worktree_source_root(config, worktree_settings))
        ).expanduser().resolve()
        repository_root = record_binding[1] if record_binding is not None else lease_source
        branch = str(lease.get("branch") or _worktree_record_branch(record) or "")
        candidates.append(
            (str(workspace_id), lease, wt_path, branch, repository_root)
        )
        candidate_paths.add(wt_path)

    if include_unregistered:
        for wt_path, (record, repository_root) in records_by_path.items():
            if wt_path in candidate_paths or not _path_is_within(wt_path, base_root):
                continue
            if normalized_only is not None and wt_path not in normalized_only:
                continue
            candidates.append(
                (None, {}, wt_path, _worktree_record_branch(record), repository_root)
            )

    summary: dict[str, Any] = {
        "at": utc_now(),
        "source": source,
        "status_root": str(status_root.resolve()),
        "workspace_source_roots": sorted(str(root) for root in repository_roots),
        "checked": 0,
        "removed": 0,
        "skipped": 0,
        "active": 0,
        "archived": 0,
        "failed": 0,
        "missing_leases": 0,
        "details": [],
    }
    changed = False
    removed_paths: list[str] = []
    for workspace_id, _lease, wt_path, branch, repository_root in candidates:
        if summary["removed"] >= max_removals and wt_path.exists():
            break
        summary["checked"] += 1
        if any(_paths_overlap(wt_path, active) for active in active_roots) or any(
            _paths_overlap(wt_path, live) for live in live_paths
        ):
            summary["active"] += 1
            continue
        if require_merged and (
            not branch or branch not in merged_by_root.get(repository_root, set())
        ):
            summary["skipped"] += 1
            continue
        if not wt_path.exists():
            if workspace_id is not None:
                leases.pop(workspace_id, None)
                summary["missing_leases"] += 1
                changed = True
            continue

        status_proc = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode != 0:
            summary["failed"] += 1
            summary["details"].append({"path": str(wt_path), "error": (status_proc.stderr or status_proc.stdout or "").strip()})
            continue

        force_remove = False
        if status_proc.stdout.strip():
            if not settings["archive_dirty_worktrees"]:
                summary["skipped"] += 1
                continue
            archive_dir = _archive_dirty_worktree(
                wt_path,
                archive_root,
                reason=source,
                max_file_bytes=int(settings["archive_max_file_bytes"]),
            )
            if archive_dir is None:
                summary["failed"] += 1
                summary["details"].append({"path": str(wt_path), "error": "archive_failed"})
                continue
            force_remove = bool(settings["force_remove_archived_dirty"])
            summary["archived"] += 1
            summary["details"].append({"path": str(wt_path), "archive": str(archive_dir)})
            if not force_remove:
                summary["skipped"] += 1
                continue

        remove_proc = _remove_worker_worktree(
            repository_root, wt_path, force=force_remove
        )
        if remove_proc.returncode != 0:
            summary["failed"] += 1
            summary["details"].append(
                {"path": str(wt_path), "error": (remove_proc.stderr or remove_proc.stdout or "").strip()}
            )
            continue
        if workspace_id is not None:
            leases.pop(workspace_id, None)
        summary["removed"] += 1
        removed_paths.append(str(wt_path))
        changed = True

    if changed or summary["checked"]:
        bucket = state.setdefault("worker_worktree_cleanup", {})
        bucket["last_run"] = summary
    if removed_paths:
        write_activity_log(
            config,
            {
                "type": "worktree_pruned",
                "message": f"Pruned {len(removed_paths)} worker worktree(s): {', '.join(removed_paths)}",
                "source": source,
                "archived": summary["archived"],
                "failed": summary["failed"],
            },
        )
    return changed


def cleanup_inactive_worker_worktrees(config: dict[str, Any], state: dict[str, Any]) -> bool:
    settings = worktree_cleanup_settings(config)
    if not settings["cleanup_inactive_leases"]:
        return False
    return _cleanup_registered_worker_worktrees(
        config,
        state,
        source="worker_lifecycle",
        require_merged=False,
        include_unregistered=False,
    )


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
    """Remove finished worker worktrees whose branches are merged."""
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
    return _cleanup_registered_worker_worktrees(
        config,
        state,
        source="worker_worktree_housekeeping",
        require_merged=True,
        include_unregistered=True,
    )




def auto_commit_archive_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("auto_commit_archive")
    settings = raw if isinstance(raw, dict) else {}
    timeout_seconds = int(settings.get("script_timeout_seconds", 180))
    return {
        "enabled": bool(settings.get("enabled", True)),
        "tick_interval_seconds": int(settings.get("tick_interval_seconds", 1800) or 0),
        "script_timeout_seconds": timeout_seconds,
        "pending_stale_seconds": int(
            settings.get(
                "pending_stale_seconds",
                max(300, timeout_seconds * 2),
            )
            or 0
        ),
    }


def maybe_auto_commit_archive(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Schedule archive publication without running a subprocess under lock.

    The caller owns the exclusive runtime-admission lock. The old implementation
    launched ``auto_commit_archive.py`` here and could hold admission for its
    full 180-second timeout while that script fetched, pushed, and opened a PR.
    This phase now records one tokenized intent only. The enclosing supervisor
    transaction executes it after releasing admission, then applies telemetry
    through ``apply_auto_commit_archive_result`` only if the token is still
    current.
    """
    settings = auto_commit_archive_settings(config)
    if not settings["enabled"]:
        return False

    deferred = _DEFERRED_AUTO_COMMIT_ARCHIVES.get()
    if deferred is None:
        # This phase is safe only inside the supervisor's deferred transaction.
        # A direct caller cannot silently regain the old locked subprocess path.
        return False

    interval = settings["tick_interval_seconds"]
    bucket = state.setdefault("auto_commit_archive", {})
    if str(bucket.get("pending_token") or "").strip():
        pending_since = _parse_iso_utc(str(bucket.get("pending_since") or ""))
        now = datetime.now(timezone.utc)
        pending_age = (
            (now - pending_since).total_seconds()
            if pending_since is not None
            else float("inf")
        )
        if pending_age < settings["pending_stale_seconds"]:
            return False
        bucket["last_error"] = "stale pending archive action expired"
        bucket["pending_token"] = None
        bucket["pending_since"] = None
    if interval > 0:
        last_at = bucket.get("last_run_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(timezone.utc)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False

    try:
        repo_root = config_path(config, "status_file").parents[0]
    except KeyError:
        bucket["last_error"] = "status_file path not configured"
        return True
    script = repo_root / ".orchestrator" / "auto_commit_archive.py"
    if not script.exists():
        bucket["last_error"] = "script missing"
        return True

    scheduled_at = utc_now()
    action = {
        "token": new_runtime_id("archive"),
        "scheduled_at": scheduled_at,
        "repo_root": str(repo_root),
        "script": str(script),
        "timeout_seconds": settings["script_timeout_seconds"],
    }
    bucket["pending_token"] = action["token"]
    bucket["pending_since"] = scheduled_at
    bucket["last_error"] = None
    deferred.append(action)
    return True


def execute_auto_commit_archive(
    config: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    """Run one previously scheduled archive action outside runtime admission."""

    del config  # the immutable action carries every execution input
    started_at = utc_now()
    try:
        proc = subprocess.run(
            ["python3", str(action["script"]), "--quiet"],
            cwd=str(action["repo_root"]),
            capture_output=True,
            text=True,
            timeout=int(action["timeout_seconds"]),
        )
    except subprocess.TimeoutExpired:
        return {
            "started_at": started_at,
            "finished_at": utc_now(),
            "last_error": "timeout",
        }
    except OSError as exc:
        return {
            "started_at": started_at,
            "finished_at": utc_now(),
            "last_error": f"spawn failed: {exc}",
        }

    stdout_tail = (proc.stdout or "").strip().splitlines()[-1:] if proc.stdout else []
    stderr_tail = (proc.stderr or "").strip().splitlines()[-1:] if proc.stderr else []
    return {
        "started_at": started_at,
        "finished_at": utc_now(),
        "last_error": None if proc.returncode == 0 else "nonzero exit",
        "last_exit": proc.returncode,
        "last_stdout": stdout_tail[0] if stdout_tail else "",
        "last_stderr": stderr_tail[0] if stderr_tail else "",
        # The script prints this marker only after it actually opens a PR.
        "opened_pr": proc.returncode == 0
        and "opened PR for" in (proc.stdout or ""),
    }


def apply_auto_commit_archive_result(
    config: dict[str, Any],
    action: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    """Apply post-lock archive telemetry iff the scheduling token is current."""

    with runtime_state_lock(config, shared=False, nonblocking=False):
        state = load_runtime_state(config)
        bucket = state.setdefault("auto_commit_archive", {})
        if str(bucket.get("pending_token") or "") != str(action.get("token") or ""):
            return False
        if str(bucket.get("pending_since") or "") != str(
            action.get("scheduled_at") or ""
        ):
            return False

        bucket.pop("pending_token", None)
        bucket.pop("pending_since", None)
        bucket["last_run_at"] = result.get("finished_at") or utc_now()
        for key in (
            "started_at",
            "finished_at",
            "last_error",
            "last_exit",
            "last_stdout",
            "last_stderr",
        ):
            if key in result:
                bucket[key] = result[key]
        save_runtime_state(config, state)
    refresh_dashboard_runtime_artifacts(config)
    return bool(result.get("opened_pr"))


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
            event_key = str(record.get("event_key") or "")
            if event_key:
                state.setdefault("seen_event_keys", {})[event_key] = record[
                    "processed_at"
                ]
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


def worker_retry_attempt_index(worker: dict[str, Any]) -> int:
    """Return retries already consumed across a parent/child worker chain."""

    return max(
        int(worker.get("retry_count", 0) or 0),
        max(0, int(worker.get("attempt_count", 0) or 0) - 1),
    )


def schedule_missing_process_retry(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    reason: str,
) -> bool:
    """Schedule a reconstructable missing-process retry within its total budget."""

    retry = worker_retry_settings(
        config,
        str(worker.get("provider") or worker.get("agent_id") or ""),
    )
    if not retry.get("enabled", True):
        return False
    consumed = worker_retry_attempt_index(worker)
    if consumed >= int(retry.get("max_attempts", 5)):
        return False
    try:
        request = request_for_worker(config, state, worker)
    except (KeyError, TypeError, ValueError):
        request = None
    if request is None:
        return False
    worker["retry_count"] = consumed
    schedule_worker_retry(config, worker, reason)
    return True


def _prepare_worker_terminal_outcome_locked(
    config: dict[str, Any],
    worker: dict[str, Any],
    *,
    reason: str,
    blocker_kind: str,
    activity_type: str,
) -> dict[str, Any] | None:
    """Move an open task to its one durable, operator-actionable outcome."""

    if not config.get("paths", {}).get("status_file"):
        return None
    task_id = str(worker.get("task_id") or "").strip()
    run_id = str(worker.get("run_id") or "").strip()
    provider = str(worker.get("provider") or worker.get("agent_id") or "").strip()
    if not task_id or not run_id or not provider:
        return None

    status = load_status(config)
    pending_outbox = status.get("status_activity_outbox")
    if _validated_status_activity_outbox_events(pending_outbox) is None:
        return None
    task = task_index_from_status(config, status).get(task_id)
    if not task:
        return None
    previous_status = str(task.get("status") or "").strip().lower()
    settings = ready_dispatch_settings(config)
    open_statuses = (
        normalized_status_set(settings.get("owned_statuses"), ["todo", "in_progress"])
        | normalized_status_set(settings.get("review_statuses"), ["review"])
        | normalized_status_set(settings.get("finalize_statuses"), ["review_approved"])
    )
    if previous_status not in open_statuses:
        return None

    dispatch_reason = str((worker.get("request_snapshot") or {}).get("reason") or "").strip()
    expected_actor = str(
        task.get("reviewer")
        if dispatch_reason == REASON_REVIEW_READY or previous_status == "review"
        else task.get("owner")
        or ""
    ).strip()
    worker_actor = display_name_for(
        config,
        str(worker.get("agent_id") or worker.get("provider") or ""),
    ).strip()
    if expected_actor and worker_actor and worker_actor != expected_actor:
        return None

    if any(
        str(blocker.get("task_id") or "") == task_id
        and str(blocker.get("status") or "") == "open"
        and str(blocker.get("blocker_kind") or "") == blocker_kind
        for blocker in (status.get("blockers") or [])
    ):
        return None

    timestamp = utc_now()
    waiting_for = expected_actor or worker_actor or provider
    message = (
        f"Supervisor recorded terminal worker outcome for task={task_id}, "
        f"run={run_id}, provider={provider}: {reason} Retry budget was exhausted "
        f"or the request could not be reconstructed; task moved from "
        f"{previous_status} to blocked. Confirm the failure, then reopen or "
        f"reassign through scripts/ai-status.sh."
    )
    event = {
        "event_id": f"supervisor-{blocker_kind}-"
        + hashlib.sha256(
            f"{task_id}\0{run_id}\0{provider}\0{reason}".encode("utf-8")
        ).hexdigest(),
        "ts": timestamp,
        "agent": "Orchestrator",
        "type": activity_type,
        "task_id": task_id,
        "target_agent": waiting_for,
        "provider": provider,
        "worker_run_id": run_id,
        "reason": reason,
        "outcome": "terminal_failure",
        "previous_status": previous_status,
        "message": message,
    }
    composed_outbox = _compose_status_activity_outbox(pending_outbox, event)
    if composed_outbox is None:
        return None
    try:
        task["status"] = rewrite_task_machine.transition(
            previous_status,
            rewrite_task_machine.TaskAction.BLOCK.value,
        ).value
    except rewrite_task_machine.TransitionError:
        return None
    task["waiting_for"] = waiting_for
    task["last_update"] = timestamp
    task["next"] = message
    status.setdefault("blockers", []).append(
        {
            "task_id": task_id,
            "owner": str(task.get("owner") or worker_actor or provider),
            "waiting_for": waiting_for,
            "message": message,
            "status": "open",
            "created_at": timestamp,
            "blocker_kind": blocker_kind,
            "previous_status": previous_status,
            "provider": provider,
            "worker_run_id": run_id,
            "failure_reason": reason,
            "dispatch_reason": dispatch_reason or None,
        }
    )
    status["status_activity_outbox"] = composed_outbox
    write_status(config, status, source="supervisor-worker-terminal-outcome")
    return event


def record_worker_terminal_outcome(
    config: dict[str, Any],
    worker: dict[str, Any],
    *,
    reason: str,
    blocker_kind: str,
    activity_type: str,
) -> dict[str, Any] | None:
    """Persist and publish one terminal worker outcome through task authority."""

    if not config.get("paths", {}).get("status_file"):
        return None
    status_path = config_path(config, "status_file")
    with canonical_task_state_lock_file(
        status_path,
        shared=False,
        nonblocking=False,
    ):
        event = _prepare_worker_terminal_outcome_locked(
            config,
            worker,
            reason=reason,
            blocker_kind=blocker_kind,
            activity_type=activity_type,
        )
    if event is None:
        return None
    sync_status_pipeline(config)
    write_activity_log(
        config,
        {
            "type": activity_type,
            "provider": event["provider"],
            "task_id": event["task_id"],
            "message": event["message"],
            "worker_run_id": event["worker_run_id"],
            "reason": event["reason"],
            "outcome": event["outcome"],
            "previous_status": event["previous_status"],
        },
    )
    return event


def record_missing_worker_terminal_outcome(
    config: dict[str, Any],
    worker: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any] | None:
    """Persist the existing terminal outcome for an unrecoverable worker."""

    return record_worker_terminal_outcome(
        config,
        worker,
        reason=reason,
        blocker_kind="missing_worker_terminal",
        activity_type="task_missing_worker_blocked",
    )


def record_retry_exhausted_worker_terminal_outcome(
    config: dict[str, Any],
    worker: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any] | None:
    """Stop a transient delivery loop once its configured retry budget is spent."""

    return record_worker_terminal_outcome(
        config,
        worker,
        reason=reason,
        blocker_kind="worker_retry_exhausted",
        activity_type="task_worker_retry_exhausted_blocked",
    )


def reconcile_runtime_on_boot(config: dict[str, Any], state: dict[str, Any]) -> bool:
    # Account topology is V2 configuration, not runtime state to migrate.
    # Starting from a persisted V2 cache therefore needs no account rewrite.
    changed = False
    now = datetime.now(timezone.utc)
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    redispatch_statuses = redispatch_candidate_statuses(config)
    counts = {
        "marker_updates": 0,
        "lease_refreshes": 0,
        "missing_process_workers_failed": 0,
        "missing_process_workers_retried": 0,
        "missing_process_workers_reassigned": 0,
        "missing_process_tasks_blocked": 0,
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
        if (
            alive
            and not expired_lease
            and worker.get("last_heartbeat_at")
            and worker_lease_can_renew(config, worker, now)
        ):
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
        if alive and not terminate_worker_pid(worker.get("pid")):
            # The post-lock confirmer owns the signal path. Do not classify the
            # still-live worker as terminal during this admission transaction.
            continue
        reason = (
            "Worker lease expired during supervisor boot reconciliation."
            if expired_lease
            else "Worker process missing during supervisor boot reconciliation."
        )
        task = task_map.get(str(worker.get("task_id") or ""))
        handoff_status = owner_worker_canonical_handoff_status(config, worker, task)
        if handoff_status is not None:
            worker["status"] = "completed"
            worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
            worker.pop("last_error", None)
            finalize_queue_event_record(config, state, worker, "completed")
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": (
                        "Owner worker reached canonical task outcome "
                        f"{handoff_status} during supervisor boot reconciliation."
                    ),
                    "worker_run_id": run_id,
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            changed = True
            continue

        runner_succeeded = worker_runner_succeeded(worker)

        task_status = str((task or {}).get("status") or "").lower()
        terminal_statuses = {
            str(value).lower()
            for value in ready_dispatch_settings(config).get("worker_terminal_statuses", ["done", "review_approved"])
        }
        dispatch_reason = str(
            (worker.get("request_snapshot") or {}).get("reason") or ""
        ).strip()
        finalize_statuses = normalized_status_set(
            ready_dispatch_settings(config).get("finalize_statuses"),
            ["review_approved"],
        )
        finalize_still_open = (
            dispatch_reason == REASON_OWNED_FINALIZE
            and task_status in finalize_statuses
        )
        if (
            runner_succeeded
            and task_status in terminal_statuses
            and not finalize_still_open
        ):
            worker["status"] = "completed"
            worker["last_event_at"] = worker.get("runner_finished_at") or utc_now()
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
        failure_kind = ""
        raw_ref: str | None = None
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
            failure_kind = str(failure.get("kind") or "")
            record_delivery_health_failure(
                config,
                state,
                agent_id=str(worker.get("agent_id") or ""),
                failure_kind=failure_kind,
                detail=failure_summary.get("summary") or detected_reason,
            )
            if failure_kind in {"transient", "capacity", "capacity_retryable"}:
                retry_reason = failure_summary.get("summary") or detected_reason
                if missing_process and schedule_missing_process_retry(
                    config,
                    state,
                    worker,
                    retry_reason,
                ):
                    worker["last_error_raw_ref"] = raw_ref
                    write_activity_log(
                        config,
                        {
                            "type": "worker_retry_scheduled",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": (
                                "Transient worker failure found during boot reconciliation; "
                                f"retry {worker.get('retry_count')} scheduled at "
                                f"{worker.get('next_retry_at')}: "
                                f"{failure_summary.get('summary') or detected_reason}"
                            ),
                            "worker_run_id": worker.get("run_id"),
                            "next_retry_at": worker.get("next_retry_at"),
                            "raw_ref": raw_ref,
                            "reason": retry_reason,
                            "outcome": "retry",
                        },
                    )
                    counts["missing_process_workers_retried"] += 1
                    changed = True
                    continue
            reason = failure_summary.get("summary") or detected_reason
            worker["last_error_raw_ref"] = raw_ref
        elif missing_process:
            failure_kind = "missing_process"
            raw_ref = write_failure_evidence(
                config,
                worker=worker,
                reason=reason,
                failure_kind=failure_kind,
            )

        if missing_process:
            if schedule_missing_process_retry(config, state, worker, reason):
                write_activity_log(
                    config,
                    {
                        "type": "worker_retry_scheduled",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": (
                            "Missing worker process found during boot reconciliation; "
                            f"retry {worker.get('retry_count')} scheduled at "
                            f"{worker.get('next_retry_at')}: {reason}"
                        ),
                        "worker_run_id": worker.get("run_id"),
                        "next_retry_at": worker.get("next_retry_at"),
                        "reason": reason,
                        "outcome": "retry",
                    },
                )
                counts["missing_process_workers_retried"] += 1
                changed = True
                continue

        worker["status"] = "failed"
        worker["last_event_at"] = utc_now()
        worker["last_error"] = reason
        finalize_queue_event_record(config, state, worker, "failed", reason)
        if expired_lease:
            counts["expired_lease_workers_failed"] += 1
        else:
            counts["missing_process_workers_failed"] += 1
            if record_missing_worker_terminal_outcome(
                config,
                worker,
                reason=reason,
            ):
                counts["missing_process_tasks_blocked"] += 1
        write_activity_log(
            config,
            {
                "type": "worker_failed",
                "provider": worker.get("provider"),
                "task_id": worker.get("task_id"),
                "message": reason,
                "worker_run_id": run_id,
                "reason": reason,
                "outcome": "terminal_failure",
            },
        )
        changed = True

    queue_records = state.setdefault("queue", {}).setdefault("events", {})
    queued_events = queue_events(state)
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
            "missing_process_workers_retried",
            "missing_process_workers_reassigned",
            "missing_process_tasks_blocked",
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





def task_is_sidecar(task: dict[str, Any]) -> bool:
    return str(task.get("task_class") or "").strip().lower() == "sidecar"








def redispatch_candidate_statuses(config: dict[str, Any]) -> set[str]:
    settings = ready_dispatch_settings(config)
    statuses = set(str(value).lower() for value in settings.get("review_statuses", []))
    statuses.update(str(value).lower() for value in settings.get("finalize_statuses", []))
    statuses.update(str(value).lower() for value in settings.get("owned_statuses", []))
    return statuses


def task_resolver_for_config(
    config: dict[str, Any],
    task_lookup: TaskResolver | dict[str, dict[str, Any]],
) -> TaskResolver:
    if isinstance(task_lookup, TaskResolver):
        return task_lookup
    # The archive is presentation/audit evidence only.  Scheduler dependency
    # decisions receive compact terminal facts through task_index_from_status.
    return TaskResolver(task_lookup, allow_archive_lookup=False)


def _task_resolver(task_lookup: TaskResolver | dict[str, dict[str, Any]]) -> TaskResolver:
    if isinstance(task_lookup, TaskResolver):
        return task_lookup
    return TaskResolver(task_lookup, allow_archive_lookup=False)


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


def outstanding_delivery_indexes(
    config: dict[str, Any],
    state: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    task_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    agents: set[str] = set()
    task_agents: set[tuple[str, str]] = set()
    event_keys: set[str] = set()
    queue_records = state.get("queue", {}).get("events", {})
    queued_events = queue_events(state) if events is None else events
    for event in queued_events:
        if task_map is not None and stale_dispatch_skip_message(config, event, task_map):
            continue
        event_id = event.get("event_id")
        if not event_id:
            continue
        record = queue_records.get(event_id, {})
        if record.get("status") in {"completed", "failed"}:
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
    event_key = str(record.get("event_key") or "")
    if event_key:
        state.setdefault("seen_event_keys", {})[event_key] = record["processed_at"]
    if worker.get("run_id"):
        record["lease_owner"] = worker.get("run_id")
    if error:
        record["error"] = error


def reconcile_queue_intents(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Reconcile leases against intents held in the same runtime snapshot.

    A queued intent without a worker is normal: it is the durable work waiting
    for delivery.  The retired JSONL queue treated that condition as an orphan
    after a timer and could delete it independently of the runtime-state CAS.
    """

    events = queue_events(state)
    task_map = task_index_from_status(config, load_status(config))
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    redispatch_statuses = redispatch_candidate_statuses(config)
    queue_records = state.setdefault("queue", {}).setdefault("events", {})
    workers = state.setdefault("workers", {})
    changed = False
    now = datetime.now(timezone.utc)

    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            continue

        record = queue_records.get(event_id, {})
        related_worker_items = [
            (run_id, worker)
            for run_id, worker in workers.items()
            if worker.get("queue_event_id") == event_id
        ]
        related_workers = [worker for _, worker in related_worker_items]
        has_active_worker = any(worker.get("status") in active_statuses for worker in related_workers)

        # ``retry_backoff`` is a scheduler hold, not a live process.  When its
        # process is gone, release only the lease; preserve the exact intent so
        # a later delivery uses the same generation-bound work item.
        stale_retry_workers = (
            record.get("status") in {"started", "retry_backoff"}
            and bool(related_worker_items)
            and all(
                str(worker.get("status") or "") == "retry_backoff"
                and (
                    ((next_retry_at := _parse_iso_utc(worker.get("next_retry_at"))) is not None and next_retry_at <= now)
                    or worker.get("runner_finished_at") is not None
                )
                and not worker_process_generation_is_current(worker)
                for _, worker in related_worker_items
            )
        )
        if stale_retry_workers:
            for run_id, worker in related_worker_items:
                workers.pop(run_id, None)
                write_activity_log(
                    config,
                    {
                        "type": "worker_reaped",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "worker_run_id": worker.get("run_id"),
                        "queue_event_id": event_id,
                        "message": (
                            "Dropped an overdue retry-backoff worker whose "
                            "recorded process generation is absent."
                        ),
                    },
                )
            record["status"] = "queued"
            record.pop("processed_at", None)
            record.pop("error", None)
            record.pop("lease_owner", None)
            record.pop("lease_acquired_at", None)
            record.pop("lease_expires_at", None)
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

        if not related_workers and record.get("status") in {"started", "waiting_approval", "retry_backoff", "stalled"}:
            record["status"] = "queued"
            record.pop("processed_at", None)
            record.pop("error", None)
            changed = True
            continue

        current_task = task_map.get(str(event.get("task_id") or ""))
        current_status = str(current_task.get("status") or "").lower() if current_task else ""

        if record.get("status") == "failed" and not has_active_worker and current_status in redispatch_statuses:
            record["status"] = "queued"
            record.pop("processed_at", None)
            record.pop("error", None)
            changed = True
            continue

    return changed


def task_index_from_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    tasks = {
        str(task.get(task_id_field)): task
        for task in status.get(tasks_path, [])
        if task.get(task_id_field)
    }
    terminal_facts = status.get("terminal_facts")
    if terminal_facts is None:
        terminal_facts = {}
    if not isinstance(terminal_facts, dict):
        raise RuntimeError("canonical terminal_facts must be an object")
    for raw_task_id, raw_fact in terminal_facts.items():
        task_id = str(raw_task_id or "").strip()
        if task_id in tasks:
            # A terminal row stays active only while the archive outbox is
            # recovering.  The row remains the current lifecycle record until
            # that transaction removes it.
            continue
        if not isinstance(raw_fact, dict):
            raise RuntimeError(f"canonical terminal fact is invalid: {task_id}")
        if (
            raw_fact.get("status") != "done"
            or str(raw_fact.get("terminal_outcome") or "")
            not in {"completed", "superseded"}
            or not isinstance(raw_fact.get("generation"), int)
            or int(raw_fact["generation"]) < 1
        ):
            raise RuntimeError(f"canonical terminal fact is invalid: {task_id}")
        tasks[task_id] = {
            "id": task_id,
            "status": "done",
            "terminal_outcome": raw_fact["terminal_outcome"],
            "generation": raw_fact["generation"],
        }
    return tasks


def current_dispatch_event_key(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return None

    task_id = str(event.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return None

    target_agent = str(event.get("target_display_name") or display_name_for(config, str(event.get("target_agent") or "")))
    resolver = task_resolver_for_config(config, task_map)
    candidate = task_execution_dispatch_candidate(
        config,
        task,
        target_agent,
        resolver,
    )
    if candidate is None or candidate[0] != reason:
        return None

    return str(build_dispatch_event(task, target_agent, reason, resolver).get("key") or "")



def task_execution_dispatch_candidate(
    config: dict[str, Any],
    task: dict[str, Any],
    agent_name: str,
    task_resolver: TaskResolver | dict[str, dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
) -> tuple[str, int] | None:
    """Return the canonical execution reason and priority for one assignment.

    Dispatch and diagnostics must use the same lifecycle ladder. A second,
    status-only copy previously let control paths disagree about whether a task
    could actually be queued.
    """

    dispatch_settings = settings or ready_dispatch_settings(config)
    review_statuses = normalized_status_set(
        dispatch_settings.get("review_statuses"),
        ["review"],
    )
    finalize_statuses = normalized_status_set(
        dispatch_settings.get("finalize_statuses"),
        ["review_approved"],
    )
    dependency_done_statuses = normalized_status_set(
        dispatch_settings.get("dependency_done_statuses"),
        ["done"],
    )
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    task_status = str(task.get("status") or "").lower()
    canonical_status = (
        "review"
        if task_status in review_statuses
        else "review_approved"
        if task_status in finalize_statuses
        else task_status
    )
    decision = rewrite_task_machine.dispatch_reason(
        canonical_status,
        is_owner=task.get(owner_field) == agent_name,
        is_reviewer=task.get(reviewer_field) == agent_name,
        deps_satisfied=dependencies_satisfied(
            task,
            task_resolver,
            dependency_done_statuses,
        ),
    )
    if decision is None:
        return None
    if (
        decision is rewrite_task_machine.DispatchReason.REVIEW_READY
        and not rewrite_task_machine.delivery_binding_is_current(task)
    ):
        # A reviewer must receive one frozen PR/artifact contract.  A missing
        # binding is a canonical recovery problem, not a reason to start a
        # worker that cannot complete the review.
        return None
    reasons = {
        rewrite_task_machine.DispatchReason.REVIEW_READY: REASON_REVIEW_READY,
        rewrite_task_machine.DispatchReason.OWNED_FINALIZE: REASON_OWNED_FINALIZE,
        rewrite_task_machine.DispatchReason.OWNED_IN_PROGRESS: REASON_OWNED_IN_PROGRESS,
        rewrite_task_machine.DispatchReason.OWNED_READY: REASON_OWNED_READY,
    }
    return reasons[decision], decision.value


def dispatch_event_is_in_unchanged_cooldown(
    seen_event_keys: dict[str, Any],
    event_key: str,
    *,
    cooldown_seconds: float,
    now: str | None = None,
) -> bool:
    """Suppress a recently served task signature until task truth changes."""

    if cooldown_seconds <= 0:
        return False
    seen_at = _parse_iso_utc(str(seen_event_keys.get(event_key) or ""))
    current_at = _parse_iso_utc(now or utc_now())
    if seen_at is None or current_at is None:
        return False
    elapsed_seconds = (current_at - seen_at).total_seconds()
    return 0 <= elapsed_seconds < cooldown_seconds


def recent_task_failure_counts(
    config: dict[str, Any],
    *,
    window_seconds: int,
    tail_bytes: int = 524288,
) -> dict[str, int]:
    """Count ``worker_failed`` activity-log events per task within the last
    ``window_seconds``, from a bounded read of the tail of the log.

    The activity log is append-only, periodically rotated, and can grow into
    the tens of MB -- nothing else in the supervisor reads it back, by
    design.  This reads only the last ``tail_bytes`` (default 512KiB, tens of
    thousands of events at typical entry sizes -- comfortably more than an
    hour of fleet activity) rather than the whole file, and holds no
    cross-cycle offset, so it is naturally correct across rotation: every
    call re-reads fresh from the current end of file instead of trusting a
    stored position that rotation could invalidate.
    """

    path_str = (config.get("paths") or {}).get("activity_log")
    if not path_str:
        return {}
    path = Path(path_str)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - tail_bytes))
            raw = handle.read()
    except OSError:
        return {}
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]  # first line may have been cut mid-record

    cutoff = _parse_iso_utc(utc_now())
    counts: dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "worker_failed":
            continue
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        observed_at = _parse_iso_utc(str(entry.get("ts") or ""))
        if cutoff is not None and observed_at is not None:
            if (cutoff - observed_at).total_seconds() > window_seconds:
                continue
        counts[task_id] = counts.get(task_id, 0) + 1
    return counts


def agent_dispatch_loads(
    config: dict[str, Any],
    state: dict[str, Any],
    active_statuses: set[str],
    events: list[dict[str, Any]] | None = None,
    task_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[int]]:
    loads: dict[str, list[int]] = {}
    represented_queue_event_ids: set[str] = set()

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
        queue_event_id = str(worker.get("queue_event_id") or "")
        if queue_event_id:
            represented_queue_event_ids.add(queue_event_id)

    queue_records = state.get("queue", {}).get("events", {})
    queued_events = queue_events(state) if events is None else events
    for event in queued_events:
        if task_map is not None and stale_dispatch_skip_message(config, event, task_map):
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        if event_id in represented_queue_event_ids:
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


def worker_matches_current_assignment(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> bool:
    task_id = str(worker.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return False

    if not worker_matches_current_task_generation(worker, task):
        return False
    agent_name = display_name_for(config, str(worker.get("agent_id") or ""))
    candidate = task_execution_dispatch_candidate(
        config,
        task,
        agent_name,
        task_resolver_for_config(config, task_map),
    )
    if candidate is None:
        return False
    current_reason = candidate[0]
    lease_reason = str((worker.get("request_snapshot") or {}).get("reason") or "")
    if not lease_reason:
        return True
    if lease_reason in {REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS}:
        return current_reason in {REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS}
    return current_reason == lease_reason


def worker_matches_current_task_generation(
    worker: Mapping[str, Any],
    task: Mapping[str, Any],
) -> bool:
    """Treat canonical task generation as the worker lease fencing token."""

    snapshot = worker.get("request_snapshot") or {}
    snapshot_metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else {}
    if not isinstance(snapshot_metadata, dict):
        snapshot_metadata = {}
    raw_bound_generations = (
        worker.get("task_generation"),
        snapshot.get("task_generation") if isinstance(snapshot, dict) else None,
        snapshot_metadata.get("task_generation"),
    )
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        for value in raw_bound_generations
    ):
        return False
    bound_generations = {int(value) for value in raw_bound_generations}
    return len(bound_generations) == 1 and task_generation(task) in bound_generations


def stale_dispatch_skip_message(config: dict[str, Any], event: dict[str, Any], task_map: dict[str, dict[str, Any]]) -> str | None:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return None

    expected_key = current_dispatch_event_key(config, event, task_map)
    task_id = str(event.get("task_id") or "unknown task")
    current_task = task_map.get(task_id)
    if current_task is not None and int(event.get("task_generation") or 0) != task_generation(current_task):
        return f"Skipped stale queued wake event for {task_id}: task generation changed after the wake-up was queued."
    if expected_key is None:
        return f"Skipped stale queued wake event for {task_id}: task is no longer eligible for {reason}."

    queued_key = str(event.get("event_key") or "")
    if queued_key and queued_key != expected_key:
        return f"Skipped stale queued wake event for {task_id}: task state changed after the wake-up was queued."

    return None


def task_generation(task: Mapping[str, Any] | None) -> int:
    try:
        generation = int((task or {}).get("generation", 1))
    except (TypeError, ValueError):
        return 0
    return generation if generation >= 1 else 0


def ready_dispatch_signature(task: dict[str, Any], reason: str, task_lookup: TaskResolver | dict[str, dict[str, Any]]) -> str:
    return json.dumps(
        {
            "task_id": task.get("id"),
            "task_generation": task_generation(task),
            "status": task.get("status"),
            "reason": reason,
            "owner": task.get("owner"),
            "reviewer": task.get("reviewer"),
            "depends_on": list(task.get("depends_on", []) or []),
            "dependency_signature": task_dependency_signature(task, task_lookup),
            "delivery_binding_digest": rewrite_task_machine.delivery_binding_digest(task),
        },
        sort_keys=True,
        ensure_ascii=True,
    )


def build_dispatch_event(
    task: dict[str, Any],
    target_agent: str,
    reason: str,
    task_lookup: TaskResolver | dict[str, dict[str, Any]],
) -> dict[str, Any]:
    task_payload = {
        "id": task.get("id"),
        "generation": task_generation(task),
        "owner": task.get("owner"),
        "reviewer": task.get("reviewer"),
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
        "delivery_binding",
    ):
        if key in task:
            task_payload[key] = task.get(key)
    signature = ready_dispatch_signature(task, reason, task_lookup)
    return {
        "key": f"dispatcher:{target_agent}:{task.get('id')}:{reason}:{signature}",
        "task_id": task.get("id"),
        "task_generation": task_generation(task),
        "target_agent": target_agent,
        "reason": reason,
        "task": task_payload,
    }


def evaluate_dispatch_candidate(
    config: dict[str, Any],
    state: dict[str, Any],
    status: dict[str, Any],
    task: dict[str, Any],
    target_agent: str,
    task_resolver: TaskResolver | dict[str, dict[str, Any]],
    *,
    settings: dict[str, Any],
    active_task_ids: set[str],
    pending_task_ids: set[str],
    pending_event_keys: set[str],
    agent_loads: dict[str, list[int]],
    active_account_loads: dict[str, int],
    pending_account_loads: dict[str, int],
    seen_event_keys: dict[str, Any],
    checked_at: str,
    cooldown_seconds: float,
    live_total: int | None = None,
) -> dict[str, Any]:
    """Pure candidate decision shared by planning and late delivery.

    Runtime ``delivery_health`` is the sole health authority and the pure
    admission evaluator receives every relevant fact as an immutable snapshot.
    """

    task_id = str(task.get("id") or "").strip()
    agent_id = normalize_agent_id(target_agent)

    def reject(gate: str, reason: str) -> dict[str, Any]:
        return {
            "eligible": False,
            "task_id": task_id,
            "target_agent": target_agent,
            "agent_id": agent_id,
            "first_blocking_gate": gate,
            "block_reason": reason,
        }

    admission = evaluate_task_delivery_admission(
        config,
        state,
        task,
        target_agent,
        task_resolver,
        active_task_ids=active_task_ids,
        pending_task_ids=pending_task_ids,
        agent_loads=agent_loads,
        active_account_loads=active_account_loads,
        pending_account_loads=pending_account_loads,
        live_total=live_total,
    )
    if not admission.eligible:
        reason_code = admission.reason.value if admission.reason is not None else "task_not_dispatchable"
        refresh_targets = [
            {"scope": target.scope.value, "id": target.identifier}
            for target in admission.health_refresh_targets
        ]
        return {
            **reject(reason_code, reason_code.replace("_", " ")),
            "health_refresh_targets": refresh_targets,
        }
    reason_map = {
        rewrite_task_machine.DispatchReason.REVIEW_READY: REASON_REVIEW_READY,
        rewrite_task_machine.DispatchReason.OWNED_FINALIZE: REASON_OWNED_FINALIZE,
        rewrite_task_machine.DispatchReason.OWNED_IN_PROGRESS: REASON_OWNED_IN_PROGRESS,
        rewrite_task_machine.DispatchReason.OWNED_READY: REASON_OWNED_READY,
    }
    reason = reason_map[admission.task_reason]
    priority = admission.task_reason.value
    if task_is_sidecar(task):
        priority += SIDECAR_READY_PRIORITY_OFFSET
    event = build_dispatch_event(task, target_agent, reason, task_resolver)
    event["delivery_endpoint_id"] = admission.endpoint_id
    event["provider"] = admission.provider_id
    if event["key"] in pending_event_keys:
        return reject("duplicate_event", "The exact delivery intent already exists")

    # A terminal (completed/failed) queue record with this exact key only
    # proves a delivery attempt was made, not that the reviewer recorded a
    # verdict: approve/reopen necessarily change task status and therefore
    # the signature, so a genuinely-resolved binding can never recompute the
    # same key again. Permanently rejecting on any past record (regardless
    # of outcome) strands a review binding forever whenever the one attempt
    # exits without acting (crash, timeout, silent no-op). In-flight
    # duplicates are already covered by the pending_event_keys check above,
    # so retry for a still-unresolved binding falls through to the same
    # unchanged-cooldown gate every other dispatch reason uses.
    if dispatch_event_is_in_unchanged_cooldown(
        seen_event_keys,
        event["key"],
        cooldown_seconds=cooldown_seconds,
        now=checked_at,
    ):
        return reject("unchanged_cooldown", "The exact task generation is cooling down")

    return {
        "eligible": True,
        "task_id": task_id,
        "target_agent": target_agent,
        "agent_id": agent_id,
        "reason": reason,
        "priority": priority,
        "event": event,
        "delivery_endpoint_id": admission.endpoint_id,
    }


def evaluate_queued_delivery_admission(
    config: dict[str, Any],
    state: dict[str, Any],
    event: Mapping[str, Any],
    task_map: dict[str, dict[str, Any]],
    queue_events: list[dict[str, Any]],
) -> rewrite_dispatch_admission.DispatchDecision | None:
    """Late revalidate one queue intent with the planner's exact predicate.

    The current event is removed from the occupancy snapshot because its own
    reservation is the capacity being consumed.  Any other active intent for
    the task remains a closed duplicate gate.
    """

    task_id = str(event.get("task_id") or "")
    task = task_map.get(task_id)
    endpoint_id = str(event.get("delivery_endpoint_id") or "").strip()
    target_agent = str(event.get("target_agent") or "").strip()
    event_id = str(event.get("event_id") or "")
    if task is None or not endpoint_id or not target_agent:
        return None
    other_events = [
        candidate
        for candidate in queue_events
        if str(candidate.get("event_id") or "") != event_id
    ]
    delivery_state = deepcopy(state)
    queue_records = ((delivery_state.get("queue") or {}).get("events") or {})
    if isinstance(queue_records, dict):
        queue_records.pop(event_id, None)
    settings = ready_dispatch_settings(config)
    active_statuses = normalized_status_set(settings.get("active_worker_statuses"), [])
    _active_agents, active_pairs = active_worker_indexes(delivery_state, active_statuses)
    _pending_agents, pending_pairs, _pending_keys = outstanding_delivery_indexes(
        config,
        delivery_state,
        other_events,
        task_map,
    )
    active_task_ids = {item for item, _agent in active_pairs if item}
    pending_task_ids = {item for item, _agent in pending_pairs if item}
    agent_loads = agent_dispatch_loads(
        config, delivery_state, active_statuses, other_events, task_map
    )
    active_accounts = active_account_counts(config, delivery_state, active_statuses)
    pending_accounts = queued_account_counts(
        config, delivery_state, other_events, task_map
    )
    resolver = task_resolver_for_config(config, task_map)
    return evaluate_task_delivery_admission(
        config,
        delivery_state,
        task,
        target_agent,
        resolver,
        active_task_ids=active_task_ids,
        pending_task_ids=pending_task_ids,
        agent_loads=agent_loads,
        active_account_loads=active_accounts,
        pending_account_loads=pending_accounts,
        requested_endpoint_id=endpoint_id,
    )


def dispatch_global_block_reason(
    config: dict[str, Any],
    settings: dict[str, Any],
    *,
    live_total: int,
    active_task_ids: set[str],
    pending_task_ids: set[str],
) -> str | None:
    if not settings.get("enabled", True):
        return "Dispatch planner is disabled"
    maximum = ready_dispatch_max_concurrent_workers(config)
    pending_only = len(pending_task_ids - active_task_ids)
    if maximum is not None and live_total + pending_only >= maximum:
        return (
            f"Global capacity is full ({live_total + pending_only}/{maximum}; "
            f"live={live_total}, pending={pending_only})"
        )
    return None




def dispatch_ready_tasks(
    config: dict[str, Any],
    state: dict[str, Any],
    agent_ids_override: list[str] | None = None,
    max_dispatches_override: int | None = None,
    activity_events: list[dict[str, Any]] | None = None,
    status_snapshot: dict[str, Any] | None = None,
    queue_events_snapshot: list[dict[str, Any]] | None = None,
    live_total_snapshot: int | None = None,
    event_sink: Any | None = None,
) -> bool:
    """Plan all currently executable canonical work up to hard capacities.

    This is the only routine scheduling path.  It never changes task ownership,
    waits for a second policy lane, or revalidates the packet that materialized an
    already-canonical task.  Recovery may change assignment in its own phase;
    the scheduler only consumes the resulting current board.
    The caller must supply an in-memory sink. Durable queue writes belong only
    to :func:`reserve_dispatch_plan`, inside the runtime admission transaction.
    """

    if event_sink is None:
        raise ValueError("dispatch planner requires an in-memory event sink")

    settings = ready_dispatch_settings(config)
    if not settings.get("enabled", True):
        return False
    # Zero is an explicit hard stop.  Return before reading the board or queue;
    # a disabled fleet must not depend on those stores being present.
    if ready_dispatch_max_concurrent_workers(config) == 0:
        return False

    status = status_snapshot if isinstance(status_snapshot, dict) else load_status(config)
    changed = False
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
    # The candidate list stays active-only, while dependency resolution also
    # consumes compact terminal facts from the same authoritative projection.
    # Building a second tasks-only map here made archived dependencies appear
    # missing even after their terminal facts were migrated.
    task_map = task_index_from_status(config, status)
    task_resolver = task_resolver_for_config(config, task_map)
    active_statuses = normalized_status_set(settings.get("active_worker_statuses"), [])
    _active_agents, active_task_agents = active_worker_indexes(state, active_statuses)
    _pending_agents, pending_task_agents, pending_event_keys = outstanding_delivery_indexes(
        config,
        state,
        queue_events_snapshot,
        task_map,
    )
    active_task_ids = {task_id for task_id, _agent_id in active_task_agents if task_id}
    pending_task_ids = {task_id for task_id, _agent_id in pending_task_agents if task_id}
    agent_loads = agent_dispatch_loads(
        config, state, active_statuses, queue_events_snapshot, task_map
    )
    active_quota_counts = active_account_counts(config, state, active_statuses)
    pending_quota_counts = queued_account_counts(
        config, state, queue_events_snapshot, task_map
    )
    seen = state.setdefault("seen_event_keys", {})
    try:
        unchanged_cooldown_seconds = max(
            0.0,
            float(settings.get("unchanged_task_cooldown_seconds", 900)),
        )
    except (TypeError, ValueError):
        unchanged_cooldown_seconds = 900.0
    dispatch_started_at = utc_now()

    configured_tick_limit = (
        settings.get("max_dispatches_per_tick", 4)
        if max_dispatches_override is None
        else max_dispatches_override
    )
    try:
        max_dispatches = max(0, int(configured_tick_limit))
    except (TypeError, ValueError):
        max_dispatches = 0
    if max_dispatches == 0:
        return changed

    max_concurrent = ready_dispatch_max_concurrent_workers(config)
    live_total = (
        live_total_snapshot
        if live_total_snapshot is not None
        else sum(len(pids) for pids in scan_live_worker_pids_by_agent().values())
    )
    pending_only_total = len(pending_task_ids - active_task_ids)
    if max_concurrent is not None:
        free_global_slots = max(0, max_concurrent - live_total - pending_only_total)
        if free_global_slots == 0:
            console_log(
                f"ready dispatch skipped: live worker count {live_total} >= "
                f"max_concurrent_workers {max_concurrent}"
                if pending_only_total == 0
                else (
                    f"ready dispatch skipped: reserved worker count "
                    f"{live_total + pending_only_total} >= max_concurrent_workers "
                    f"{max_concurrent} (live={live_total}, pending={pending_only_total})"
                ),
                quiet=SUPERVISOR_LOG_QUIET,
            )
            return changed
        max_dispatches = min(max_dispatches, free_global_slots)

    sequence = (
        [normalize_agent_id(agent_id) for agent_id in agent_ids_override if normalize_agent_id(agent_id)]
        if agent_ids_override
        else dispatch_loop_agent_ids(config)
    )
    dispatch_state = state.setdefault("ready_dispatcher", {})
    try:
        cursor = int(dispatch_state.get("dispatch_cursor", 0))
    except (TypeError, ValueError):
        cursor = 0
    if sequence:
        cursor %= len(sequence)
        agent_ids = sequence[cursor:] + sequence[:cursor]
    else:
        agent_ids = []
    considered = 0
    dispatches = 0
    refresh_demands = state.setdefault("delivery_health_refresh_demands", [])
    if not isinstance(refresh_demands, list):
        refresh_demands = []
        state["delivery_health_refresh_demands"] = refresh_demands

    for agent_id in agent_ids:
        if dispatches >= max_dispatches:
            break
        considered += 1
        target_agent = display_name_for(config, agent_id)
        if not target_agent:
            continue

        agent_capacity = agent_dispatch_capacity(config, agent_id, settings)
        current_load = len(agent_loads.get(target_agent, []))
        available_slots = max(0, agent_capacity - current_load)
        if available_slots == 0:
            continue

        quota_limit = account_concurrency_limit(config, agent_id, settings)
        quota_group = agent_account_id(config, agent_id)
        quota_used = active_quota_counts.get(quota_group, 0) + pending_quota_counts.get(
            quota_group,
            0,
        )
        if quota_limit is not None:
            available_slots = min(available_slots, max(0, quota_limit - quota_used))
        if available_slots == 0:
            continue

        candidates: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
        for index, task in enumerate(tasks):
            task_id = str(task.get(task_id_field) or "")
            if not task_id:
                continue
            decision = evaluate_dispatch_candidate(
                config,
                state,
                status,
                task,
                target_agent,
                task_resolver,
                settings=settings,
                active_task_ids=active_task_ids,
                pending_task_ids=pending_task_ids,
                pending_event_keys=pending_event_keys,
                agent_loads=agent_loads,
                active_account_loads=active_quota_counts,
                pending_account_loads=pending_quota_counts,
                seen_event_keys=seen,
                checked_at=dispatch_started_at,
                cooldown_seconds=unchanged_cooldown_seconds,
                live_total=live_total,
            )
            if not decision["eligible"]:
                for target in decision.get("health_refresh_targets", []) or []:
                    if not isinstance(target, Mapping):
                        continue
                    scope = str(target.get("scope") or "")
                    identity = str(target.get("id") or "")
                    demand = {"scope": scope, "id": identity}
                    if scope in {"endpoint", "account"} and identity and demand not in refresh_demands:
                        refresh_demands.append(demand)
                continue
            priority = int(decision["priority"])
            candidates.append(
                (
                    priority,
                    index,
                    task,
                    decision,
                )
            )

        candidates.sort(key=lambda item: item[:2])
        occurrence_limit = min(available_slots, max_dispatches - dispatches)
        for _, _, task, decision in candidates[:occurrence_limit]:
            task_id = str(task.get(task_id_field) or "")
            reason = str(decision["reason"])
            event = dict(decision["event"])
            queued = event_sink(config, event)
            if not queued:
                continue

            seen[event["key"]] = dispatch_started_at
            pending_event_keys.add(event["key"])
            pending_task_ids.add(task_id)
            pending_task_agents.add((task_id, agent_id))
            agent_loads.setdefault(target_agent, []).append(
                dispatch_reason_priority(reason) or 9
            )
            if quota_group:
                pending_quota_counts[quota_group] = pending_quota_counts.get(quota_group, 0) + 1
            changed = True
            dispatches += 1

    if sequence and considered and not agent_ids_override:
        dispatch_state["dispatch_cursor"] = (cursor + considered) % len(sequence)
    return changed


def build_dispatch_plan(
    config: dict[str, Any],
    runtime_snapshot: dict[str, Any],
    status_snapshot: dict[str, Any],
    queue_snapshot: list[dict[str, Any]],
    *,
    live_total: int,
) -> dict[str, Any]:
    """Run the sole planner without writing runtime or queue state."""

    scratch = deepcopy(runtime_snapshot)
    scratch.pop("delivery_health_refresh_demands", None)
    events: list[dict[str, Any]] = []

    def capture(_config: dict[str, Any], event: dict[str, Any]) -> bool:
        events.append(deepcopy(event))
        return True

    dispatch_ready_tasks(
        config,
        scratch,
        status_snapshot=status_snapshot,
        queue_events_snapshot=queue_snapshot,
        live_total_snapshot=live_total,
        event_sink=capture,
    )
    refresh_targets = list(scratch.get("delivery_health_refresh_demands") or [])
    for target in unavailable_assignment_fallback_refresh_targets(
        config, scratch, status_snapshot
    ):
        if target not in refresh_targets:
            refresh_targets.append(target)
    dispatcher_state = scratch.get("ready_dispatcher")
    cursor = (
        dispatcher_state.get("dispatch_cursor")
        if isinstance(dispatcher_state, dict)
        else None
    )
    return {
        "planned_at": utc_now(),
        "events": events,
        "dispatch_cursor": cursor,
        "live_total": max(0, int(live_total)),
        "health_refresh_targets": refresh_targets,
    }


def reserve_dispatch_plan(
    config: dict[str, Any],
    state: dict[str, Any],
    plan: Mapping[str, Any],
) -> bool:
    """CAS-like reservation of a precomputed plan under runtime admission.

    Canonical assignment and lifecycle are revalidated again by
    :func:`process_queue` immediately before launch. This transaction owns only
    duplicate intent and global/account/agent capacity reservation.
    """

    events = plan.get("events")
    if not isinstance(events, list) or not events:
        return False
    settings = ready_dispatch_settings(config)
    active_statuses = normalized_status_set(settings.get("active_worker_statuses"), [])
    queued_events = queue_events(state)
    task_map = task_index_from_status(config, load_status(config))
    _active_agents, active_pairs = active_worker_indexes(state, active_statuses)
    _pending_agents, pending_pairs, pending_keys = outstanding_delivery_indexes(
        config,
        state,
        queued_events,
        task_map,
    )
    active_task_ids = {task_id for task_id, _agent in active_pairs if task_id}
    pending_task_ids = {task_id for task_id, _agent in pending_pairs if task_id}
    agent_loads = agent_dispatch_loads(
        config, state, active_statuses, queued_events, task_map
    )
    active_accounts = active_account_counts(config, state, active_statuses)
    pending_accounts = queued_account_counts(config, state, queued_events, task_map)
    max_global = ready_dispatch_max_concurrent_workers(config)
    active_worker_count = sum(
        1
        for worker in (state.get("workers") or {}).values()
        if str(worker.get("status") or "") in active_statuses
    )
    live_total = max(active_worker_count, max(0, int(plan.get("live_total") or 0)))
    planned_at = str(plan.get("planned_at") or utc_now())
    changed = False

    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue
        event = deepcopy(raw_event)
        if stale_dispatch_skip_message(config, event, task_map):
            continue
        event_key = str(event.get("key") or "")
        task_id = str(event.get("task_id") or "")
        target_agent = str(event.get("target_agent") or "")
        agent_id = normalize_agent_id(target_agent)
        if (
            not event_key
            or not task_id
            or not agent_id
            or not is_execution_dispatch_reason(str(event.get("reason") or ""))
            or task_id in active_task_ids
            or task_id in pending_task_ids
            or event_key in pending_keys
        ):
            continue
        pending_only = len(pending_task_ids - active_task_ids)
        if max_global is not None and live_total + pending_only >= max_global:
            break
        account = agent_account_id(config, agent_id)
        account_limit = account_concurrency_limit(config, agent_id, settings)
        account_used = active_accounts.get(account, 0) + pending_accounts.get(account, 0)
        if account_limit is not None and account_used >= account_limit:
            continue
        capacity = agent_dispatch_capacity(config, agent_id, settings)
        if len(agent_loads.get(target_agent, [])) >= capacity:
            continue
        if not event.get("context_files"):
            event["context_files"] = worker_execution_context_files(task_id)
        if not _queue_delivery_event_locked(config, state, event):
            continue
        pending_keys.add(event_key)
        pending_task_ids.add(task_id)
        pending_pairs.add((task_id, agent_id))
        agent_loads.setdefault(target_agent, []).append(
            dispatch_reason_priority(str(event.get("reason") or "")) or 9
        )
        if account:
            pending_accounts[account] = pending_accounts.get(account, 0) + 1
        state.setdefault("seen_event_keys", {})[event_key] = planned_at
        changed = True

    cursor = plan.get("dispatch_cursor")
    if cursor is not None:
        dispatcher_state = state.setdefault("ready_dispatcher", {})
        if dispatcher_state.get("dispatch_cursor") != cursor:
            dispatcher_state["dispatch_cursor"] = cursor
            changed = True
    return changed


def ready_dispatch_max_concurrent_workers(config: dict[str, Any]) -> int | None:
    max_concurrent_setting = ready_dispatch_settings(config).get("max_concurrent_workers")
    try:
        max_concurrent = int(max_concurrent_setting) if max_concurrent_setting not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return max(0, max_concurrent) if max_concurrent is not None else None


def explain_dispatch_for_task(
    config: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    *,
    target_agent_filter: str | None = None,
    status: dict[str, Any] | None = None,
    live_total: int | None = None,
) -> dict[str, Any]:
    """Serialize the same candidate decisions consumed by the scheduler."""

    status = status if isinstance(status, dict) else load_status(config)
    settings = ready_dispatch_settings(config)
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")
    tasks = [task for task in status.get(tasks_path, []) if task.get(task_id_field)]
    task_map = task_index_from_status(config, status)
    task = task_map.get(task_id)
    if task is None:
        return {"task_id": task_id, "error": f"Task {task_id} not found", "agents": {}}
    task_resolver = task_resolver_for_config(config, task_map)
    active_statuses = normalized_status_set(settings.get("active_worker_statuses"), [])
    _active_agents, active_pairs = active_worker_indexes(state, active_statuses)
    _pending_agents, pending_pairs, pending_keys = outstanding_delivery_indexes(
        config, state, task_map=task_map
    )
    active_task_ids = {item for item, _agent in active_pairs if item}
    pending_task_ids = {item for item, _agent in pending_pairs if item}
    agent_loads = agent_dispatch_loads(
        config, state, active_statuses, task_map=task_map
    )
    active_accounts = active_account_counts(config, state, active_statuses)
    pending_accounts = queued_account_counts(config, state, task_map=task_map)
    seen = state.get("seen_event_keys")
    seen = seen if isinstance(seen, dict) else {}
    try:
        cooldown = max(0.0, float(settings.get("unchanged_task_cooldown_seconds", 900)))
    except (TypeError, ValueError):
        cooldown = 900.0
    checked_at = utc_now()
    if live_total is None:
        live_total = sum(len(pids) for pids in scan_live_worker_pids_by_agent().values())
    result: dict[str, Any] = {
        "task_id": task_id,
        "task_status": task.get("status"),
        "task_owner": task.get("owner"),
        "task_reviewer": task.get("reviewer"),
        "agents": {},
    }
    global_block = dispatch_global_block_reason(
        config,
        settings,
        live_total=live_total,
        active_task_ids=active_task_ids,
        pending_task_ids=pending_task_ids,
    )
    if global_block:
        result["global_block_reason"] = global_block

    agent_ids = list(dispatch_loop_agent_ids(config))
    for agent_id in (config.get("agents", {}) or {}):
        if agent_id not in agent_ids:
            agent_ids.append(agent_id)
    if target_agent_filter:
        wanted = normalize_agent_id(target_agent_filter)
        agent_ids = [
            agent_id
            for agent_id in agent_ids
            if agent_id == wanted
            or display_name_for(config, agent_id) == target_agent_filter
        ]
    for agent_id in agent_ids:
        target_agent = display_name_for(config, agent_id)
        if not target_agent:
            continue
        decision = evaluate_dispatch_candidate(
            config,
            state,
            status,
            task,
            target_agent,
            task_resolver,
            settings=settings,
            active_task_ids=active_task_ids,
            pending_task_ids=pending_task_ids,
            pending_event_keys=pending_keys,
            agent_loads=agent_loads,
            active_account_loads=active_accounts,
            pending_account_loads=pending_accounts,
            seen_event_keys=seen,
            checked_at=checked_at,
            cooldown_seconds=cooldown,
        )
        trace = {
            "display_name": target_agent,
            "blocked": not bool(decision["eligible"]),
            "first_blocking_gate": decision.get("first_blocking_gate"),
            "block_reason": decision.get("block_reason"),
        }
        if decision["eligible"]:
            trace.update(
                {
                    "candidate_reason": decision["reason"],
                    "candidate_priority": decision["priority"],
                    "verdict": "eligible for the sole delivery queue",
                }
            )
        result["agents"][target_agent] = trace
    return result




RUNTIME_LOCK_HOLD_WARN_DEFAULT_SECONDS = 30.0


def record_runtime_lock_hold(
    config: dict[str, Any],
    state: dict[str, Any],
    held_since: float,
    *,
    quiet: bool = False,
) -> float:
    """Publish how long this cycle owned the exclusive runtime-admission lock.

    Every status command an auto worker runs takes the same lock shared, so this
    number is the ceiling on how long ``approve``, ``assign``, and ``note`` can
    be made to wait. Leaving it unmeasured is how a 771s hold stayed invisible
    until reviewers noticed nine-minute stalls.
    """

    held_seconds = round(max(0.0, time.monotonic() - held_since), 3)
    _record_cycle_runtime_lock_hold(held_seconds)
    supervisor_state = state.setdefault("supervisor", {})
    supervisor_state["runtime_lock_hold_seconds"] = held_seconds
    peak = supervisor_state.get("runtime_lock_hold_peak_seconds")
    if not isinstance(peak, (int, float)) or held_seconds > float(peak):
        supervisor_state["runtime_lock_hold_peak_seconds"] = held_seconds
    warn_after = float(
        config.get("supervisor", {}).get(
            "runtime_lock_hold_warn_after_seconds",
            RUNTIME_LOCK_HOLD_WARN_DEFAULT_SECONDS,
        )
    )
    exceeded = warn_after > 0 and held_seconds > warn_after
    supervisor_state["runtime_lock_hold_exceeded"] = exceeded
    if exceeded:
        console_log(
            f"runtime-admission lock held {held_seconds}s (> {warn_after}s); "
            "concurrent approve/assign/note commands queued for that long",
            quiet=quiet,
        )
    return held_seconds


def publish_cycle_metrics_to_state(
    state: dict[str, Any],
    *,
    finished_monotonic: float | None = None,
) -> dict[str, Any] | None:
    """Replace the prior bounded cycle sample and retain only scalar peaks."""

    finished = time.monotonic() if finished_monotonic is None else finished_monotonic
    snapshot = _bounded_cycle_metrics_snapshot(finished_monotonic=finished)
    if snapshot is None:
        return None
    supervisor_state = state.setdefault("supervisor", {})
    supervisor_state["last_cycle_metrics"] = snapshot
    supervisor_state["cycle_elapsed_seconds"] = snapshot["cycle_elapsed_seconds"]
    supervisor_state["cycle_elapsed_peak_seconds"] = round(
        max(
            float(supervisor_state.get("cycle_elapsed_peak_seconds", 0.0)),
            float(snapshot["cycle_elapsed_seconds"]),
        ),
        3,
    )
    cadence = snapshot.get("cadence")
    if isinstance(cadence, dict):
        overshoot = float(cadence.get("start_overshoot_seconds", 0.0))
        supervisor_state["cadence_overshoot_seconds"] = overshoot
        supervisor_state["cadence_overshoot_peak_seconds"] = round(
            max(
                float(supervisor_state.get("cadence_overshoot_peak_seconds", 0.0)),
                overshoot,
            ),
            3,
        )
    return snapshot


def apply_post_dispatch_maintenance(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    delivery_health_observations: Iterable[Mapping[str, Any]],
    task_state_projection_snapshot: dict[str, Any] | None,
    assistant_dev_bridge_snapshot: dict[str, Any] | None,
    quiet: bool,
) -> bool:
    """Apply slow observations after launch; their effects feed the next plan."""

    changed = bool(reconcile_runtime_on_boot(config, state))
    changed = bool(
        apply_delivery_health_observations(
            config, state, delivery_health_observations
        )
    ) or changed
    changed = bool(reconcile_unavailable_assignments(config, state)) or changed
    changed = bool(reconcile_failure_loops(config, state)) or changed
    if isinstance(assistant_dev_bridge_snapshot, dict):
        bridge_state = assistant_dev_bridge_snapshot.get("state")
        if isinstance(bridge_state, dict):
            state["assistant_dev_bridge"] = deepcopy(bridge_state)
            changed = bool(assistant_dev_bridge_snapshot.get("changed")) or changed
    changed = bool(reconcile_queue_records(config, state)) or changed
    changed = bool(reconcile_queue_intents(config, state)) or changed
    changed = bool(
        reconcile_ownerless_in_progress_tasks(
            config,
            state,
        )
    ) or changed
    changed = bool(maybe_auto_commit_archive(config, state)) or changed
    if isinstance(task_state_projection_snapshot, dict):
        report = task_state_projection_snapshot.get("report")
        if isinstance(report, dict):
            state.setdefault("supervisor", {})["task_state_projection"] = deepcopy(report)
            changed = bool(task_state_projection_snapshot.get("changed")) or changed
    return changed


def run_once(
    config: dict[str, Any],
    *,
    quiet: bool = False,
    verbose: bool = False,
    once: bool = False,
) -> bool:
    cycle_metrics: dict[str, Any] = {
        "started_monotonic": time.monotonic(),
        "phases": {},
        "batch_counts": {},
        "critical_phase_errors": [],
    }
    scheduled_sample = _SCHEDULED_CYCLE_SAMPLE.get()
    if isinstance(scheduled_sample, dict):
        cycle_metrics["cadence"] = dict(scheduled_sample)
    cycle_metrics_token = _CYCLE_METRICS.set(cycle_metrics)
    try:
        initial_runtime_snapshot = load_runtime_state_snapshot(config)
    except Exception:
        initial_runtime_snapshot = {}
    try:
        write_supervisor_pid(config)
        # Stamp identity in one short transaction, then plan/queue outside the
        # exclusive lock against immutable local snapshots.
        changed = _run_with_deferred_dispatch_status_syncs(
            config,
            lambda: _run_once_locked(
                config,
                quiet=quiet,
            )
        )
        pruned_approvals = _safe_phase(
            "prune_stale_approvals",
            prune_stale_approvals,
            config,
            quiet=quiet,
        )
        changed = bool(pruned_approvals) or changed
        # Close the previous cycle's local worker/process observations before
        # taking planner snapshots.  A dead or completed worker must not keep
        # capacity occupied for another cycle.  This phase performs no slow
        # provider/GitHub/bridge I/O and never launches a process.
        pre_plan_poll_changed = bool(
            _safe_phase(
                "poll_workers_before_plan_reserved",
                _run_reserved_runtime_phase,
                config,
                "poll_workers_before_plan",
                lambda state: poll_workers(
                    config,
                    state,
                    activity_events=[],
                    governance_activity_events=[],
                ),
                quiet=quiet,
                critical=True,
            )
        )
        changed = pre_plan_poll_changed or changed

        dispatch_status_snapshot = _safe_phase(
            "load_dispatch_status_snapshot", load_status, config, quiet=quiet,
            critical=True,
        )
        if not isinstance(dispatch_status_snapshot, dict):
            dispatch_status_snapshot = {}
        live_pid_snapshot = _safe_phase(
            "scan_live_worker_pids", scan_live_worker_pids_by_agent, quiet=quiet,
            critical=True,
        )
        if not isinstance(live_pid_snapshot, dict):
            live_pid_snapshot = {}
        live_total_snapshot = sum(
            len(pids) for pids in live_pid_snapshot.values() if isinstance(pids, list)
        )
        planning_runtime_snapshot = _safe_phase(
            "load_planning_runtime_snapshot",
            load_runtime_state_snapshot,
            config,
            quiet=quiet,
            critical=True,
        )
        if not isinstance(planning_runtime_snapshot, dict):
            planning_runtime_snapshot = {}
        dispatch_queue_snapshot = queue_events(planning_runtime_snapshot)
        dispatch_plan = _safe_phase(
            "build_dispatch_plan",
            build_dispatch_plan,
            config,
            planning_runtime_snapshot,
            dispatch_status_snapshot,
            dispatch_queue_snapshot,
            live_total=live_total_snapshot,
            quiet=quiet,
            critical=True,
        )
        if not isinstance(dispatch_plan, dict):
            dispatch_plan = {"events": []}
        dispatch_changed = bool(
            _safe_phase(
                "dispatch_plan_transaction",
                _run_dispatch_plan_transaction,
                config,
                lambda state: (
                    False
                    if watchdog_safe_mode_active(state)
                    else reserve_dispatch_plan(config, state, dispatch_plan)
                ),
                quiet=quiet,
                critical=True,
            )
        )
        changed = dispatch_changed or changed
        process_changed = False
        max_delivery_launches = max(
            0,
            int(ready_dispatch_settings(config).get("max_dispatches_per_tick", 4) or 0),
        )
        queued_health_refresh_demand: list[dict[str, str]] = []
        for _delivery_index in range(max_delivery_launches):
            delivery_outcome: dict[str, bool] = {}
            delivery_changed = bool(
                _safe_phase(
                    "process_queue_reserved",
                    _run_reserved_runtime_phase,
                    config,
                    "process_queue",
                    lambda state: (
                        False
                        if watchdog_safe_mode_active(state)
                        else process_queue(
                            config,
                            state,
                            delivery_outcome=delivery_outcome,
                            health_refresh_demand=queued_health_refresh_demand,
                        )
                    ),
                    quiet=quiet,
                    critical=True,
                )
            )
            process_changed = delivery_changed or process_changed
            if not delivery_changed or not delivery_outcome.get("launched", False):
                break
        changed = process_changed or changed

        # Slow observation/maintenance follows launch and feeds the next cycle.
        maintenance_runtime_snapshot = _safe_phase(
            "load_maintenance_runtime_snapshot",
            load_runtime_state_snapshot,
            config,
            quiet=quiet,
        )
        if not isinstance(maintenance_runtime_snapshot, dict):
            maintenance_runtime_snapshot = {}
        bridge_runtime_scratch: dict[str, Any] = {}
        bridge_drain_changed = bool(
            _safe_phase(
                "drain_assistant_dev_packet_inbox",
                drain_assistant_dev_packet_inbox,
                config,
                bridge_runtime_scratch,
                quiet=quiet,
            )
        )
        bridge_state = bridge_runtime_scratch.get("assistant_dev_bridge")
        bridge_snapshot = (
            {"changed": bridge_drain_changed, "state": deepcopy(bridge_state)}
            if isinstance(bridge_state, dict)
            else None
        )
        # Bridge materialization is a canonical mutation even if its optional
        # runtime-observability snapshot loses a later CAS race.  Report the
        # cycle as changed so callers never treat a successfully drained packet
        # as an idle supervisor pass.
        changed = bridge_drain_changed or changed
        _safe_phase(
            "continue_or_skip_empty",
            continue_or_skip_empty,
            THIS_DIR.parent,
            quiet=quiet,
        )
        probe_targets = list(dispatch_plan.get("health_refresh_targets", []))
        for target in queued_health_refresh_demand:
            if target not in probe_targets:
                probe_targets.append(target)
        delivery_health_observations = probe_demanded_delivery_health(
            config,
            probe_targets,
            quiet=quiet,
        )
        task_state_projection_snapshot = _safe_phase(
            "prefetch_task_state_projection",
            prefetch_task_state_projection,
            config,
            maintenance_runtime_snapshot,
            quiet=quiet,
        )
        github_bus_changed = bool(
            _safe_phase(
                "sync_github_bus",
                sync_github_bus,
                config,
                maintenance_runtime_snapshot,
                quiet=quiet,
            )
        )
        maintenance_changed = bool(
            _safe_phase(
                "apply_post_dispatch_maintenance",
                _run_reserved_runtime_phase,
                config,
                "post_dispatch_maintenance",
                lambda state: apply_post_dispatch_maintenance(
                    config,
                    state,
                    delivery_health_observations=delivery_health_observations,
                    task_state_projection_snapshot=task_state_projection_snapshot,
                    assistant_dev_bridge_snapshot=bridge_snapshot,
                    quiet=quiet,
                ),
                quiet=quiet,
            )
        )
        changed = (
            maintenance_changed
            or github_bus_changed
            or changed
        )
        prune_changed = bool(
            _safe_phase(
                "prune_worktrees_reserved",
                _run_reserved_runtime_phase,
                config,
                "prune_worktrees",
                lambda state: _run_reserved_worktree_prunes(config, state),
                quiet=quiet,
            )
        )
        changed = prune_changed or changed
        finalized = bool(
            _safe_phase(
                "finalize_runtime_cycle",
                _run_with_deferred_dispatch_status_syncs,
                config,
                lambda: _finalize_runtime_cycle_locked(
                    config,
                    quiet=quiet,
                    critical_phase_errors=tuple(
                        cycle_metrics.get("critical_phase_errors", [])
                    ),
                ),
                quiet=quiet,
                critical=True,
            )
        )
        changed = finalized or changed
        postlock_state = _safe_phase(
            "load_postlock_runtime_snapshot",
            load_runtime_state_snapshot,
            config,
            quiet=quiet,
        )
        _safe_phase(
            "refresh_dashboard_runtime_artifacts",
            refresh_dashboard_runtime_artifacts,
            config,
            quiet=quiet,
        )
        if isinstance(postlock_state, dict):
            _safe_phase(
                "log_runtime_summary",
                lambda: log_runtime_summary(
                    postlock_state,
                    safe_load_approval_state(config),
                    changed=changed,
                    quiet=quiet,
                    verbose=verbose,
                    previous_heartbeat=(
                        (initial_runtime_snapshot.get("supervisor") or {}).get(
                            "last_heartbeat_at"
                        )
                        if isinstance(initial_runtime_snapshot, dict)
                        else None
                    ),
                    warn_after_seconds=float(
                        config.get("supervisor", {}).get(
                            "heartbeat_warn_after_seconds",
                            10.0,
                        )
                    ),
                    once=once,
                ),
                quiet=quiet,
            )
        _safe_phase(
            "persist_complete_cycle_metrics",
            persist_complete_cycle_metrics,
            config,
            quiet=quiet,
        )
        return changed
    finally:
        _CYCLE_METRICS.reset(cycle_metrics_token)


def sync_task_state_projection(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Reconcile the durable task journal with its derived board projection.

    The V2 journal is authoritative.  This phase verifies its derived JSON
    projection once and repairs only that projection; it never imports state
    from a retired format or a second authority.
    """

    runtime_env = task_state_store_runtime_env(config)
    raw_event_log = str(runtime_env.get("PANTHEON_TASK_STATE_EVENT_LOG") or "").strip()
    if not raw_event_log:
        return False

    checked_at = utc_now()
    event_log = Path(raw_event_log)
    status_file = config_path(config, "status_file", "ai-status.json")
    supervisor_state = state.setdefault("supervisor", {})
    previous = supervisor_state.get("task_state_projection")
    previous = previous if isinstance(previous, dict) else {}

    try:
        with canonical_task_state_lock_file(status_file, shared=False):
            file_state = load_json(status_file, default={})
            if not isinstance(file_state, dict):
                raise RuntimeError("task state projection must be a JSON object")
            snapshot = rewrite_task_state_store.load_snapshot(event_log)
            if not snapshot["event_count"]:
                raise RuntimeError("authoritative task-state journal is empty")
            canonical_state = snapshot["state"]
            repaired = (
                rewrite_task_state_store.sha256_json(file_state)
                != snapshot["state_sha256"]
            )
            if repaired:
                write_json(status_file, canonical_state)
                file_state = load_json(status_file, default={})
            report = rewrite_task_state_store.verify_snapshot(snapshot, file_state)
            caught_up = bool(report["ok"])
            if not caught_up:
                raise RuntimeError(
                    "task-state projection remains divergent after reconciliation"
                )

        supervisor_state["task_state_projection"] = {
            "mode": "authoritative",
            "ok": True,
            "event_log": str(event_log),
            "last_checked_at": checked_at,
            "last_success_at": checked_at,
            "last_error": None,
            # Parity after reconciliation, not "a write happened".
            "caught_up": caught_up,
            "repaired": repaired,
            **report,
        }
        return repaired
    except Exception as exc:  # per-phase isolation keeps the incumbent loop observable
        failure = {
            "mode": "authoritative",
            "ok": False,
            "event_log": str(event_log),
            "last_checked_at": checked_at,
            "last_error": f"{type(exc).__name__}: {exc}",
            # Parity was never established this cycle, and no repair is claimed.
            "caught_up": False,
            "repaired": False,
        }
        for key in (
            "event_count",
            "last_event_id",
            "projected_state_sha256",
            "expected_state_sha256",
            "last_success_at",
        ):
            if key in previous:
                failure[key] = previous[key]
        supervisor_state["task_state_projection"] = failure
        console_log(
            f"task-state projection reconciliation failed: {failure['last_error']}",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        return False


def prefetch_task_state_projection(
    config: dict[str, Any],
    runtime_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Reconcile the task projection before runtime admission.

    Authoritative journal validation takes the canonical task lock and may need
    to repair its derived JSON projection. Keeping that wait outside the
    exclusive runtime lock lets worker-lease commands continue validating
    admission while this independent state plane reaches parity.
    """

    previous = (
        (runtime_snapshot.get("supervisor") or {}).get("task_state_projection")
        if isinstance(runtime_snapshot, dict)
        else None
    )
    scratch = {
        "supervisor": {
            **(
                {"task_state_projection": deepcopy(previous)}
                if isinstance(previous, dict)
                else {}
            )
        }
    }
    changed = sync_task_state_projection(config, scratch)
    report = scratch["supervisor"].get("task_state_projection")
    if not isinstance(report, dict):
        return None
    return {
        "changed": bool(changed),
        "report": deepcopy(report),
    }


def _safe_phase(
    name: str,
    fn,
    *args,
    quiet: bool = False,
    critical: bool = False,
    **kwargs,
):
    """Run one supervisor cycle phase in isolation.

    Supervisor Authority V2 failure isolation: a
    failure in one phase must degrade only that phase, never abort the whole
    cycle. Historically the cycle body was a single flat ``try`` over ~30
    independent phases, so one raise (e.g. a missing activity-log archive)
    short-circuited dispatch/finalize/archive and crash-looped the supervisor
    for hours. Wrapping each phase turns that total outage into a one-line,
    self-describing degradation of a single subsystem.

    Returns the phase result, or ``None`` if the phase raised.
    """
    started = time.monotonic()
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberate per-phase isolation
        if critical:
            metrics = _CYCLE_METRICS.get()
            if isinstance(metrics, dict):
                errors = metrics.setdefault("critical_phase_errors", [])
                if isinstance(errors, list):
                    errors.append(f"{name}:{type(exc).__name__}:{exc}")
        console_log(
            f"cycle phase '{name}' failed: {type(exc).__name__}: {exc}; "
            "other phases continue",
            quiet=quiet,
        )
        return None
    finally:
        _record_cycle_phase_elapsed(name, time.monotonic() - started)


def _run_once_locked(
    config: dict[str, Any],
    *,
    quiet: bool = False,
) -> bool:
    lock_held_since = time.monotonic()
    loop_started_at = utc_now()
    state = load_runtime_state(config)
    stamp_supervisor_runtime_state(
        config,
        state,
        heartbeat_at=loop_started_at,
        lifecycle="running",
        loop_started_at=loop_started_at,
    )
    save_runtime_state(config, state)
    changed = False
    try:
        changed = normalize_runtime_delivery_health(state) or changed
        if watchdog_safe_mode_active(state):
            changed = _safe_phase("record_watchdog_safe_mode_observed", record_watchdog_safe_mode_observed, config, state, loop_started_at, quiet=quiet) or changed

        record_runtime_lock_hold(config, state, lock_held_since, quiet=quiet)
        _record_cycle_batch_count(
            "dispatch_status_mutations",
            len(_DEFERRED_DISPATCH_STATUS_SYNCS.get() or []),
        )
        _record_cycle_batch_count(
            "runtime_activity_events",
            len(_DEFERRED_ACTIVITY_EVENTS.get() or []),
        )
        save_runtime_state(config, state)
        return changed
    except Exception as exc:
        loop_finished_at = utc_now()
        stamp_supervisor_runtime_state(
            config,
            state,
            heartbeat_at=loop_finished_at,
            lifecycle="degraded",
            loop_finished_at=loop_finished_at,
            loop_error=f"{type(exc).__name__}: {exc}",
        )
        record_runtime_lock_hold(config, state, lock_held_since, quiet=quiet)
        _record_cycle_batch_count(
            "dispatch_status_mutations",
            len(_DEFERRED_DISPATCH_STATUS_SYNCS.get() or []),
        )
        _record_cycle_batch_count(
            "runtime_activity_events",
            len(_DEFERRED_ACTIVITY_EVENTS.get() or []),
        )
        publish_cycle_metrics_to_state(state)
        save_runtime_state(config, state)
        raise


def run_supervisor_cycle(
    config: dict[str, Any],
    *,
    quiet: bool = False,
    verbose: bool = False,
) -> bool:
    try:
        return run_once(config, quiet=quiet, verbose=verbose, once=False)
    except Exception as exc:
        console_log(
            f"supervisor cycle failed: {type(exc).__name__}: {exc}; continuing after next poll",
            quiet=quiet,
        )
        return False


def _run_reserved_worktree_prunes(
    config: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """Prune ordinary orphaned worker worktrees."""

    return prune_orphan_worktrees(config, state)


def _finalize_runtime_cycle_locked(
    config: dict[str, Any],
    *,
    quiet: bool,
    critical_phase_errors: tuple[str, ...] = (),
) -> bool:
    """Commit post-I/O queue reconciliation and the truthful cycle heartbeat."""

    state = load_runtime_state(config)
    changed = bool(
        _safe_phase(
            "reconcile_queue_records_post_io",
            reconcile_queue_records,
            config,
            state,
            quiet=quiet,
        )
    )
    changed = bool(
        _safe_phase(
            "reconcile_queue_intents_post_io",
            reconcile_queue_intents,
            config,
            state,
            quiet=quiet,
        )
    ) or changed
    _safe_phase(
        "trim_worker_history",
        trim_worker_history,
        state,
        int(config.get("supervisor", {}).get("max_worker_history", 200)),
        quiet=quiet,
    )
    _safe_phase(
        "trim_seen_events",
        trim_seen_events,
        state,
        int(ready_dispatch_settings(config).get("seen_event_history_limit", 2000)),
        quiet=quiet,
    )
    loop_finished_at = utc_now()
    loop_error = (
        "critical phase failures: " + "; ".join(critical_phase_errors)
        if critical_phase_errors
        else None
    )
    stamp_supervisor_runtime_state(
        config,
        state,
        heartbeat_at=loop_finished_at,
        lifecycle="degraded" if loop_error else "running",
        loop_finished_at=loop_finished_at,
        loop_error=loop_error,
    )
    save_runtime_state(config, state)
    return changed


def persist_complete_cycle_metrics(config: dict[str, Any]) -> bool:
    """Persist the complete post-lock cycle sample in one short transaction."""

    with _measured_runtime_state_lock(config):
        state = load_runtime_state(config)
        snapshot = publish_cycle_metrics_to_state(
            state,
            finished_monotonic=time.monotonic(),
        )
        if snapshot is None:
            return False
        supervisor_state = state.setdefault("supervisor", {})
        longest_hold = round(
            max(
                0.0,
                float(snapshot.get("runtime_lock_hold_seconds", 0.0)),
            ),
            3,
        )
        supervisor_state["runtime_lock_hold_seconds"] = longest_hold
        supervisor_state["runtime_lock_hold_peak_seconds"] = round(
            max(
                longest_hold,
                float(supervisor_state.get("runtime_lock_hold_peak_seconds", 0.0)),
            ),
            3,
        )
        save_runtime_state(config, state)
    return True


def run_deadline_scheduler(
    cycle: Any,
    poll_interval: float,
    *,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
    on_cycle_complete: Any | None = None,
    max_cycles: int | None = None,
) -> None:
    """Run cycles on an anchored monotonic deadline without catch-up spinning.

    The first cycle is immediate.  Every later target is derived from that
    monotonic schedule, so work consumes the interval instead of being followed
    by another full sleep.  If work spans one or more deadlines, those missed
    starts are skipped in one arithmetic step and the next cycle waits for the
    first future deadline.  This makes overrun behavior deterministic and
    prevents an overloaded supervisor from entering a zero-sleep busy loop.
    """

    interval = float(poll_interval)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("poll_interval must be a finite positive number")
    if max_cycles is not None and max_cycles < 0:
        raise ValueError("max_cycles cannot be negative")

    deadline = float(monotonic())
    skipped_before_start = 0
    completed = 0
    while max_cycles is None or completed < max_cycles:
        before_sleep = float(monotonic())
        sleep_seconds = max(0.0, deadline - before_sleep)
        if sleep_seconds > 0:
            sleep(sleep_seconds)
        started = float(monotonic())
        cadence_sample = {
            "scheduled_deadline": deadline,
            "start_overshoot_seconds": max(0.0, started - deadline),
            "skipped_deadlines_before_start": skipped_before_start,
        }
        token = _SCHEDULED_CYCLE_SAMPLE.set(cadence_sample)
        try:
            cycle()
        finally:
            _SCHEDULED_CYCLE_SAMPLE.reset(token)
        finished = float(monotonic())
        next_deadline = deadline + interval
        skipped = 0
        if next_deadline < finished:
            skipped = int(math.floor((finished - next_deadline) / interval)) + 1
            next_deadline += skipped * interval
        completion = {
            **cadence_sample,
            "cycle_elapsed_seconds": max(0.0, finished - started),
            "sleep_before_start_seconds": sleep_seconds,
            "skipped_deadlines_after_cycle": skipped,
            "next_deadline": next_deadline,
        }
        if on_cycle_complete is not None:
            try:
                on_cycle_complete(completion)
            except Exception as exc:  # telemetry must never terminate scheduling
                console_log(
                    "scheduler completion telemetry failed: "
                    f"{type(exc).__name__}: {exc}; future cycles continue",
                    quiet=SUPERVISOR_LOG_QUIET,
                )
        deadline = next_deadline
        skipped_before_start = skipped
        completed += 1


def publish_scheduler_cadence_completion(
    config: dict[str, Any],
    sample: Mapping[str, Any],
) -> None:
    """Persist one scalar scheduler completion sample in a short transaction."""

    with runtime_state_lock(config, shared=False, nonblocking=False):
        state = load_runtime_state(config)
        supervisor_state = state.setdefault("supervisor", {})
        elapsed = round(max(0.0, float(sample.get("cycle_elapsed_seconds", 0.0))), 3)
        supervisor_state["scheduler_cycle_elapsed_seconds"] = elapsed
        supervisor_state["scheduler_cycle_elapsed_peak_seconds"] = round(
            max(
                elapsed,
                float(supervisor_state.get("scheduler_cycle_elapsed_peak_seconds", 0.0)),
            ),
            3,
        )
        supervisor_state["cadence_skipped_deadlines"] = max(
            0,
            int(sample.get("skipped_deadlines_after_cycle", 0)),
        )
        supervisor_state["cadence_next_deadline_monotonic"] = round(
            float(sample.get("next_deadline", 0.0)),
            6,
        )
        save_runtime_state(config, state)


def main() -> int:
    global SUPERVISOR_LOG_QUIET
    args = parse_args()
    SUPERVISOR_LOG_QUIET = args.quiet
    config = load_config(args.config)
    validate_provider_accounts(config)
    check_status_root_consistency(config, allow_isolated=args.allow_isolated_status_root)
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
    bootstrap_supervisor_runtime_state(
        config,
        lifecycle="starting",
    )
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
            quiet=args.quiet,
            verbose=args.verbose,
            once=True,
        )
        return 0
    def scheduled_cycle() -> bool:
        return run_supervisor_cycle(
            config,
            quiet=args.quiet,
            verbose=args.verbose,
        )

    run_deadline_scheduler(
        scheduled_cycle,
        poll_interval,
        on_cycle_complete=lambda sample: publish_scheduler_cadence_completion(
            config,
            sample,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
