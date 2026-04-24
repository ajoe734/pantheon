#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import importlib
import json
import os
import random
import re
import signal
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
)
from coordination_file_watcher import sync_coordination_files
from github_bus import sync_github_bus
from provider_permissions import provider_capabilities as build_provider_capabilities, write_provider_capabilities
from runtime_state import load_approval_state, load_event_queue, load_runtime_state, prune_worker_records, queue_event_record, save_runtime_state
from runtime_state import enqueue_event
from task_archive import TaskResolver
from watch_events import queue_delivery_event, run_scan, trim_seen_events


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
    re.compile(r"^(?:you(?:'ve| have)\s+)?hit your limit\b", re.IGNORECASE),
    re.compile(r"^An unexpected critical error occurred", re.IGNORECASE),
    re.compile(r"^(?:Error|error|fatal):", re.IGNORECASE),
)
WORKER_FAILURE_FALSE_POSITIVE_PATTERNS = (
    re.compile(r"^(?:result|error|audit):\s+Optional\[Dict\[str,\s*Any\]\]\s*=\s*None$", re.IGNORECASE),
    re.compile(r"^error:\s+[A-Za-z_][A-Za-z0-9_<>{}\[\], :|?]+?\|\s*null$", re.IGNORECASE),
    re.compile(r"^[+-]?\s*console\.error\(", re.IGNORECASE),
)
SEARCH_RESULT_JSON_FIELD_PATTERN = re.compile(
    r"^(?:[^:\s][^:]*:)?\d+[:-]\s*\"[A-Za-z0-9_]+\"\s*:\s*",
    re.IGNORECASE,
)

LOCAL_TZ = ZoneInfo("Asia/Taipei")
SUPERVISOR_LOG_QUIET = False
GENERIC_WORKER_EXIT_REASON = "Worker exited before the task reached a terminal status."
PLANNING_STATE_FILE = THIS_DIR / "planning-state.json"
PLANNING_PHASE_DIR = THIS_DIR.parent / "docs" / "02-architecture" / "consensus" / "phase1"
_UNSET = object()


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


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


def iter_matching_supervisor_pids() -> list[int]:
    current_script = str(Path(__file__).resolve())
    current_script_name = str(Path(__file__).name)
    current_script_rel = ".orchestrator/supervisor.py"
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
        script_matches = any(
            part == current_script
            or part == current_script_rel
            or part.endswith(f"/{current_script_name}")
            for part in parts
        )
        if script_matches and proc_cwd == current_repo_root:
            matches.append(pid)
    return sorted(matches)


def terminate_older_supervisors(config: dict[str, Any]) -> None:
    current_pid = os.getpid()
    terminated: list[int] = []
    for pid in iter_matching_supervisor_pids():
        if pid >= current_pid:
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
    parser.add_argument("--poll-interval", type=float, default=None)
    parser.add_argument("--quiet", action="store_true", help="Suppress terminal heartbeat output.")
    parser.add_argument("--verbose", action="store_true", help="Print active worker and queue details each tick.")
    return parser.parse_args()


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
        ai_status.sync_docs_site()
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
    coordination = metadata.get("coordination")
    if isinstance(coordination, dict) and coordination:
        return "coordination"
    reason = str(event.get("reason") or "").strip()
    if reason.startswith("discussion_planning_"):
        return "planning"
    if reason.startswith("coordination:"):
        return "coordination"
    return "execution"


def worker_dispatch_mode(worker: dict[str, Any]) -> str:
    if worker_is_discussion_planning(worker):
        return "planning"
    if worker_is_coordination_dispatch(worker):
        return "coordination"
    return "execution"


def empty_mode_occupancy() -> dict[str, dict[str, int]]:
    return {
        "planning": {"running": 0, "pending": 0, "queued": 0},
        "execution": {"running": 0, "pending": 0, "queued": 0},
        "coordination": {"running": 0, "pending": 0, "queued": 0},
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


def build_request(config: dict[str, Any], event: dict[str, Any]) -> DeliveryRequest:
    agent = agent_config_for(config, event["target_agent"])
    metadata = dict(event.get("metadata", {}) or {})
    model_preference = resolve_agent_model_preference(config, agent)
    if model_preference and "model_preference" not in metadata:
        metadata["model_preference"] = model_preference
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
    state.setdefault("workers", {})[worker_run_id] = {
        "run_id": worker_run_id,
        "provider": request.provider,
        "agent_id": agent["id"],
        "task_id": request.task_id,
        "session_id": result.session_id,
        "mode": result.mode,
        "status": "manual_pending" if result.manual_confirmation_required and not result.auto_delivered else "running",
        "last_event_at": utc_now(),
        "deferred_action": None,
        "resume_token": result.resume_token or result.session_id,
        "pr_url": normalize_pr_url(config, result.pr_url),
        "session_url": result.session_url,
        "attempt_count": attempt_count,
        "queue_event_id": queue_event_id,
        "command": result.command,
        "log_path": result.log_path,
        "payload_path": result.payload_path,
        "pid": result.pid,
        "notes": result.notes,
        "metadata": result.metadata,
        "request_snapshot": request_snapshot(request),
        "parent_run_id": parent_run_id,
        "retry_count": 0,
        "next_retry_at": None,
        "last_error": None,
    }
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
        pause_entry = current_provider_dispatch_pause(state, request_provider)
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
        record["status"] = "manual_pending" if delivery and delivery.get("manual_confirmation_required") and not delivery.get("auto_delivered") else "started"
        record["run_id"] = worker_run_id
        record["processed_at"] = utc_now()
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
    next_reviewer = str(planning_state.get("next_reviewer") or "Qwen")
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
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if '"ts":' in stripped and '"type":' in stripped:
            continue
        if SEARCH_RESULT_JSON_FIELD_PATTERN.search(stripped):
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


def classify_worker_failure(config: dict[str, Any], worker: dict[str, Any], reason: str | None) -> dict[str, Any]:
    provider = str(worker.get("provider") or worker.get("agent_id") or "").strip().lower()
    normalized = str(reason or "").lower()
    retry = worker_retry_settings(config, worker.get("provider"))
    transient_patterns = [str(pattern).lower() for pattern in retry.get("transient_error_patterns", [])]

    auth_markers = {
        "status: 401",
        "unauthorized",
        "authentication",
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
        "exhausted your capacity",
        "no quota",
        "you have no quota",
        "quota exceeded",
        "free daily quota has been reached",
        "free tier quota exceeded",
        "qwen oauth free tier was discontinued",
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

    if any(marker in normalized for marker in auth_markers):
        return {"kind": "auth", "transient": False, "label": "auth"}
    if any(marker in normalized for marker in terminal_quota_markers):
        return {"kind": "quota_terminal", "transient": False, "label": "quota terminal"}
    if any(marker in normalized for marker in retryable_capacity_markers):
        return {"kind": "capacity_retryable", "transient": True, "label": "capacity/429"}
    if provider == "gemini" and any(marker in normalized for marker in unknown_critical_markers):
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


def provider_guardrail_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("provider_guardrails", {}) or {})
    settings.setdefault("pause_on_capacity_failure", True)
    settings.setdefault("pause_on_auth_failure", True)
    settings.setdefault("capacity_pause_seconds", 900)
    settings.setdefault("auth_pause_seconds", int(settings.get("capacity_pause_seconds", 900)))
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


def current_provider_dispatch_pause(state: dict[str, Any], provider: str | None) -> dict[str, Any] | None:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return None
    bucket = _dispatch_pause_bucket(state)
    entry = bucket.get(provider_id)
    if not isinstance(entry, dict):
        return None
    blocked_until = _parse_iso_utc(str(entry.get("blocked_until") or ""))
    now = datetime.now(timezone.utc)
    if blocked_until is not None and blocked_until <= now:
        bucket.pop(provider_id, None)
        return None
    return entry


def provider_dispatch_paused(state: dict[str, Any], provider: str | None) -> bool:
    return current_provider_dispatch_pause(state, provider) is not None


def agent_dispatch_paused(config: dict[str, Any], state: dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id:
        return False
    agent = agent_config_for(config, agent_id)
    provider_id = str(agent.get("provider") or agent.get("id") or agent_id)
    return provider_dispatch_paused(state, provider_id)


def is_terminal_quota_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "quota_terminal"


def is_retryable_capacity_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() in {"capacity", "capacity_retryable"}


def is_auth_failure_kind(kind: str | None) -> bool:
    return str(kind or "").strip().lower() == "auth"


def should_pause_dispatch_for_failure_kind(kind: str | None) -> bool:
    return (
        is_terminal_quota_failure_kind(kind)
        or is_retryable_capacity_failure_kind(kind)
        or is_auth_failure_kind(kind)
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
    now = datetime.now(timezone.utc)
    effective_pause_kind = str(pause_kind or failure_kind or "").strip().lower()
    if effective_pause_kind == "auth":
        if not settings.get("pause_on_auth_failure", True):
            return False
        pause_seconds_key = "auth_pause_seconds"
    else:
        if not settings.get("pause_on_capacity_failure", True):
            return False
        pause_seconds_key = "quota_terminal_pause_seconds" if effective_pause_kind == "quota_terminal" else "capacity_pause_seconds"
    pause_seconds = max(60, int(settings.get(pause_seconds_key, 900)))
    blocked_until = (now + timedelta(seconds=pause_seconds)).replace(microsecond=0)
    blocked_until_iso = blocked_until.isoformat().replace("+00:00", "Z")
    bucket = _dispatch_pause_bucket(state)
    previous = bucket.get(provider_id)
    summary = summarize_failure_reason(reason, provider_id)
    changed = (
        not isinstance(previous, dict)
        or str(previous.get("blocked_until") or "") != blocked_until_iso
        or str(previous.get("summary") or "") != summary.get("summary")
        or str(previous.get("raw_ref") or "") != str(raw_ref or "")
    )
    bucket[provider_id] = {
        "provider": provider_id,
        "paused_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "blocked_until": blocked_until_iso,
        "reason": summary.get("summary"),
        "summary": summary.get("summary"),
        "detail": summary.get("detail"),
        "failure_kind": failure_kind or summary.get("kind"),
        "pause_kind": effective_pause_kind or failure_kind or summary.get("kind"),
        "reset_after_seconds": pause_seconds,
        "raw_ref": raw_ref,
        "task_id": task_id,
        "worker_run_id": worker_run_id,
    }
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
                "provider": provider_id,
                "task_id": task_id,
                "worker_run_id": worker_run_id,
                "message": (
                    f"Paused new dispatches for {provider_id} until {blocked_until_iso} after {pause_description}: "
                    f"{summary.get('summary')}"
                ),
                "raw_ref": raw_ref,
            },
        )
    return changed


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
        "Claude": ["Codex", "Qwen", "Grok", "Gemini"],
        "Gemini": ["Codex", "Qwen", "Claude", "Grok"],
        "Codex": ["Qwen", "Claude", "Grok", "Gemini"],
        "Qwen": ["Codex", "Claude", "Grok", "Gemini"],
        "Grok": ["Codex", "Qwen", "Claude", "Gemini"],
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


def agent_can_take_task(config: dict[str, Any], agent_name: str | None, task: dict[str, Any] | None) -> bool:
    name = str(agent_name or "").strip()
    if not name:
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
    action_by_reason = {
        "owned_ready_dispatch": ("start", {"todo"}),
        "owned_finalize_dispatch": ("note", {"review_approved"}),
        "owned_in_progress_dispatch": ("progress", {"in_progress"}),
    }
    action = action_by_reason.get(reason)
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
        "owned_ready_dispatch": f"Supervisor auto-started {task_id} after successful dispatch.",
        "owned_finalize_dispatch": f"Supervisor resumed {task_id} for finalize after successful dispatch.",
        "owned_in_progress_dispatch": f"Supervisor re-dispatched {task_id}; task remains in progress.",
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

    if dispatch_reason in {"owned_ready_dispatch", "owned_in_progress_dispatch"}:
        if task_status != "in_progress":
            return False
        task["status"] = "todo"
        message = (
            f"Supervisor preempted {task_id} to free {target_agent} for higher-priority review/finalize work; "
            "task returned to todo until a fresh run restarts it."
        )
    elif dispatch_reason == "owned_finalize_dispatch":
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
    task["last_update"] = timestamp
    task["next"] = message

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
    if current_provider_dispatch_pause(state, request.provider):
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
    env = dict(os.environ)
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    extra_env = runtime.get("env", {}) or {}
    for key, value in extra_env.items():
        if value is None:
            continue
        env[str(key)] = os.path.expanduser(str(value))
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
    env.update(
        {
            "ORCH_RUN_ID": worker["run_id"],
            "ORCH_TASK_ID": worker.get("task_id") or "",
            "ORCH_AGENT_ID": worker.get("agent_id") or "",
            "ORCH_PROVIDER": provider_id,
            "ORCH_SESSION_ID": str(session_id),
        }
    )
    process, _ = spawn_background_process(
        command,
        cwd=config_path(config, "status_file").parents[0],
        log_path=log_path,
        env=env,
    )
    previous_logs = list(worker.get("previous_log_paths") or [])
    if worker.get("log_path"):
        previous_logs.append(worker["log_path"])
    worker["previous_log_paths"] = previous_logs
    worker["pid"] = process.pid
    worker["status"] = "running"
    worker["deferred_action"] = None
    worker["last_event_at"] = utc_now()
    worker["log_path"] = str(log_path)
    worker["resume_count"] = int(worker.get("resume_count", 0)) + 1
    worker["last_resumed_session_id"] = str(session_id)
    worker["command"] = command
    worker.setdefault("metadata", {})["shell_command"] = shell_quote(command)
    worker["metadata"]["resume_permission_mode"] = resume_permission_mode if worker.get("last_approval_id") else None
    worker["metadata"]["resume_allowed_tools"] = allowed_tools
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
        update_from_log(config, worker)
        alive = pid_is_alive(worker.get("pid"))
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
            and higher_priority_ready_task_exists(config, worker, task_map)
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

        failure_reason = detect_worker_failure(worker)
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
    return changed


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



def ready_dispatch_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("ready_dispatcher", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("review_statuses", ["review"])
    settings.setdefault("finalize_statuses", ["review_approved"])
    settings.setdefault("owned_statuses", ["in_progress", "todo"])
    settings.setdefault("sidecar_only_agents", [])
    legacy_done_statuses = settings.get("done_statuses", ["done", "review_approved"])
    settings.setdefault("dependency_done_statuses", ["done"])
    settings.setdefault("worker_terminal_statuses", legacy_done_statuses)
    settings.setdefault("active_worker_statuses", ["running", "waiting_approval", "retry_backoff", "manual_pending", "stalled"])
    settings.setdefault("max_tasks_per_agent", 1)
    settings.setdefault("max_dispatches_per_tick", 4)
    settings.setdefault("orphaned_queue_event_grace_seconds", 300)
    return settings


def helper_claim_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(ready_dispatch_settings(config).get("helper_claim", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("task_statuses", ["todo"])
    settings.setdefault("paused_owner_task_statuses", ["in_progress"])
    settings.setdefault("require_owner_higher_priority_load", True)
    return settings


def underutilization_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("underutilization_dispatch", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("threshold_ratio", 0.5)
    settings.setdefault("continuous_window_seconds", 900)
    settings.setdefault("cooldown_seconds", 900)
    settings.setdefault("max_new_sidecars_per_wave", 2)
    settings.setdefault("max_active_sidecars_per_agent", 1)
    settings.setdefault(
        "productive_worker_statuses",
        ["running", "waiting_approval", "suspended_approval", "retry_backoff"],
    )
    return settings


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
        "review_packet": ["Qwen", "Codex", "Copilot", "Claude", "Gemini"],
        "acceptance_packet": ["Codex", "Qwen", "Copilot", "Claude", "Gemini"],
        "bff_handoff_packet": ["Copilot", "Codex", "Qwen", "Claude", "Gemini"],
    }
    return mapping.get(kind, ["Codex", "Qwen", "Copilot", "Claude", "Gemini"])


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


def eligible_idle_agents_for_sidecars(
    config: dict[str, Any],
    state: dict[str, Any],
    status: dict[str, Any],
    *,
    max_active_sidecars_per_agent: int,
) -> list[str]:
    settings = ready_dispatch_settings(config)
    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, _pending_task_agents, _pending_event_keys = outstanding_delivery_indexes(config, state)
    task_map = task_index_from_status(config, status)
    agents: list[str] = []
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        display_name = str(agent.get("display_name") or agent.get("name") or agent_id).strip()
        if "legacy alias" in display_name.lower():
            continue
        normalized = normalize_agent_id(agent_id)
        if agent_dispatch_paused(config, state, normalized):
            continue
        if normalized in active_agents or normalized in pending_agents:
            continue
        if count_open_sidecars_for_agent(status, display_name) >= max_active_sidecars_per_agent:
            continue
        if agent_has_dispatchable_primary_work(config, status, display_name, task_map):
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
    if reason not in {"review_ready_dispatch", "owned_finalize_dispatch", "owned_in_progress_dispatch", "owned_ready_dispatch"}:
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
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    task_status = str(task.get("status") or "").lower()

    eligible = False
    if reason == "review_ready_dispatch":
        eligible = task_status in review_statuses and task.get(reviewer_field) == target_agent
    elif reason == "owned_finalize_dispatch":
        eligible = task_status in finalize_statuses and task.get(owner_field) == target_agent
    elif reason == "owned_in_progress_dispatch":
        eligible = task_status == "in_progress" and task.get(owner_field) == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses)
    elif reason == "owned_ready_dispatch":
        eligible = task_status == "todo" and task.get(owner_field) == target_agent and dependencies_satisfied(task, task_map, dependency_done_statuses)

    if not eligible:
        return None

    return str(build_dispatch_event(task, target_agent, reason, task_map).get("key") or "")


def dispatch_reason_priority(reason: str | None) -> int | None:
    normalized = str(reason or "")
    priorities = {
        "review_ready_dispatch": 0,
        "owned_finalize_dispatch": 1,
        "owned_in_progress_dispatch": 2,
        "owned_ready_dispatch": 3,
    }
    return priorities.get(normalized)


def dispatch_priority_for_task(
    config: dict[str, Any],
    task: dict[str, Any],
    agent_name: str,
    *,
    dependencies_done_statuses: set[str] | None = None,
) -> int | None:
    settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    dependency_done_statuses = dependencies_done_statuses or {
        str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])
    }
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
    if owner_paused and task_status in paused_owner_statuses:
        return idle_agent_name in fallbacks
    owner_loads = agent_loads.get(owner_name, [])
    if helper_settings.get("require_owner_higher_priority_load", True):
        current_priority = dispatch_reason_priority("owned_ready_dispatch")
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


def higher_priority_ready_task_exists(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> bool:
    if worker_is_discussion_planning(worker) or worker_is_coordination_dispatch(worker):
        return False
    current_priority = dispatch_reason_priority(worker.get("request_snapshot", {}).get("reason"))
    if current_priority is None:
        return False

    agent_name = display_name_for(config, str(worker.get("agent_id") or ""))
    current_task_id = str(worker.get("task_id") or "")
    settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")
    current_task = task_map.get(current_task_id)

    for task_id, task in task_map.items():
        if task_id == current_task_id:
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
            return True

    return False


def worker_matches_current_assignment(
    config: dict[str, Any],
    worker: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
) -> bool:
    if worker_is_discussion_planning(worker):
        return True
    if worker_is_coordination_dispatch(worker):
        return True
    task_id = str(worker.get("task_id") or "")
    task = task_map.get(task_id)
    if not task:
        return False
    agent_name = display_name_for(config, str(worker.get("agent_id") or ""))
    settings = ready_dispatch_settings(config)
    review_statuses = {str(value).lower() for value in settings.get("review_statuses", ["review"])}
    finalize_statuses = {str(value).lower() for value in settings.get("finalize_statuses", ["review_approved"])}
    owned_statuses = {str(value).lower() for value in settings.get("owned_statuses", ["in_progress", "todo"])}
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}
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
    if reason not in {"review_ready_dispatch", "owned_finalize_dispatch", "owned_in_progress_dispatch", "owned_ready_dispatch"}:
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


def dispatch_discussion_planning(config: dict[str, Any], state: dict[str, Any], planning_state: dict[str, Any] | None = None) -> bool:
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
        if agent_dispatch_paused(config, state, agent_id):
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


def dispatch_ready_tasks(config: dict[str, Any], state: dict[str, Any]) -> bool:
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
    max_tasks_per_agent = max(1, int(settings.get("max_tasks_per_agent", 1)))
    max_dispatches_per_tick = max(1, int(settings.get("max_dispatches_per_tick", 4)))

    active_agents, active_task_agents = active_worker_indexes(state, active_statuses)
    pending_agents, pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    active_task_ids = {task_id for task_id, _agent_id in active_task_agents if task_id}
    pending_task_ids = {task_id for task_id, _agent_id in pending_task_agents if task_id}
    agent_loads = agent_dispatch_loads(config, state, active_statuses)
    helper_settings = helper_claim_settings(config)
    seen = state.setdefault("seen_event_keys", {})

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

    dispatches = 0
    agent_ids = list(config.get("agents", {}).keys())
    for agent_id in agent_ids:
        if dispatches >= max_dispatches_per_tick:
            break
        if agent_dispatch_paused(config, state, agent_id):
            continue
        if agent_id in active_agents or agent_id in pending_agents:
            continue

        target_agent = display_name_for(config, agent_id)
        candidates: list[tuple[int, int, dict[str, Any], str]] = []
        for index, task in enumerate(tasks):
            task_id = str(task.get(task_id_field) or "")
            if not task_id:
                continue
            task_status = str(task.get("status") or "").lower()
            task_owner = task.get(owner_field)
            task_reviewer = task.get(reviewer_field)
            owner_paused = agent_dispatch_paused(config, state, normalize_agent_id(str(task_owner or "")))

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

            helper_claim_candidate = (
                dependencies_satisfied(task, task_map, dependency_done_statuses)
                and task_id not in active_task_ids
                and task_id not in pending_task_ids
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
                helper_dispatch_reason = "owned_in_progress_dispatch" if task_status == "in_progress" else "owned_ready_dispatch"
                helper_message = (
                    f"Helper-claimed by {target_agent} while {task_owner} is dispatch-paused."
                    if owner_paused
                    else f"Helper-claimed by {target_agent} while {task_owner} completes higher-priority work."
                )
                if persist_task_reassignment(
                    config,
                    task_id=task_id,
                    new_owner=target_agent,
                    new_reviewer=str(task_owner or task_reviewer or ""),
                    message=helper_message,
                    handoff_to=target_agent,
                    handoff_from=str(task_owner or ""),
                ):
                    task[owner_field] = target_agent
                    task[reviewer_field] = str(task_owner or task_reviewer or "")
                    task["last_update"] = utc_now()
                    task["next"] = helper_message
                    event = build_dispatch_event(task, target_agent, helper_dispatch_reason, task_map)
                    if event["key"] not in pending_event_keys and queue_delivery_event(config, event):
                        seen[event["key"]] = utc_now()
                        pending_event_keys.add(event["key"])
                        pending_agents.add(agent_id)
                        active_task_ids.add(task_id)
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
                                "new_reviewer": str(task_owner or task_reviewer or ""),
                            },
                        )
                        console_log(
                            f"helper claim: task={task_id} from={task_owner} to={target_agent}",
                            quiet=SUPERVISOR_LOG_QUIET,
                        )
                        break

            if reason is None or priority is None:
                continue

            event = build_dispatch_event(task, target_agent, reason, task_map)
            if event["key"] in pending_event_keys:
                continue
            candidates.append((priority, index, task, reason))

        candidates.sort(key=lambda item: (item[0], item[1]))
        for _, _, task, reason in candidates[:max_tasks_per_agent]:
            event = build_dispatch_event(task, target_agent, reason, task_map)
            if queue_delivery_event(config, event):
                seen[event["key"]] = utc_now()
                pending_event_keys.add(event["key"])
                changed = True
                dispatches += 1
                if dispatches >= max_dispatches_per_tick:
                    break

    return changed


def dispatch_underutilization_sidecars(config: dict[str, Any], state: dict[str, Any]) -> bool:
    settings = underutilization_settings(config)
    tracking = state.setdefault("underutilization", {})
    productive_statuses = {str(value) for value in settings.get("productive_worker_statuses", [])}
    ratio = utilization_ratio_for_sidecars(config, state, productive_statuses)
    threshold = float(settings.get("threshold_ratio", 0.5))
    now = utc_now()
    tracking["last_ratio"] = round(ratio, 4)
    changed = False

    if not settings.get("enabled", True):
        tracking["below_threshold_since"] = None
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

    candidates.sort(key=lambda item: (int(item.get("priority", 9)), str(item.get("parent_task_id") or ""), str(item.get("kind") or "")))
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    _active_agents, _active_task_agents = active_worker_indexes(state, active_statuses)
    _pending_agents, _pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    seen = state.setdefault("seen_event_keys", {})
    per_agent_counts = {agent: count_open_sidecars_for_agent(status, agent) for agent in idle_agents}
    created = 0

    for candidate in candidates:
        if created >= int(settings.get("max_new_sidecars_per_wave", 2)):
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
        planning_state = load_discussion_planning_state()
        changed = auto_materialize_discussion_planning(config, planning_state) or changed
        planning_state = load_discussion_planning_state()
        if discussion_planning_is_active(planning_state):
            changed = dispatch_discussion_planning(config, state, planning_state) or changed
        else:
            changed = dispatch_ready_tasks(config, state) or changed
            changed = dispatch_underutilization_sidecars(config, state) or changed
        changed = process_queue(config, state, provider_report) or changed
        changed = poll_workers(config, state, provider_report=provider_report) or changed
        changed = reconcile_queue_records(config, state) or changed
        changed = prune_event_queue(config, state) or changed
        changed = sync_github_bus(config, state) or changed
        trim_worker_history(state, int(config.get("supervisor", {}).get("max_worker_history", 200)))
        trim_seen_events(state, int(config.get("watcher", {}).get("max_seen_events", 2000)))

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


def main() -> int:
    global SUPERVISOR_LOG_QUIET
    args = parse_args()
    SUPERVISOR_LOG_QUIET = args.quiet
    config = load_config(args.config)
    terminate_older_supervisors(config)
    atexit.register(clear_supervisor_pid, config)
    write_supervisor_pid(config)
    bootstrap_supervisor_runtime_state(config, lifecycle="starting")
    poll_interval = args.poll_interval or float(config.get("supervisor", {}).get("poll_interval_seconds", 2.0))
    console_log(
        f"starting supervisor pid={os.getpid()} poll_interval={poll_interval:.1f}s config={args.config}",
        quiet=args.quiet,
    )
    run_once(
        config,
        watch=not args.no_watch,
        replay=args.replay,
        quiet=args.quiet,
        verbose=args.verbose,
        once=args.once,
    )
    if args.once:
        return 0
    while True:
        time.sleep(poll_interval)
        run_once(
            config,
            watch=not args.no_watch,
            replay=False,
            quiet=args.quiet,
            verbose=args.verbose,
            once=False,
        )


if __name__ == "__main__":
    raise SystemExit(main())
