#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from common import (
    RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID as _LOCK_PROTOCOL_ID,
    RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION as _LOCK_PROTOCOL_VERSION,
    activity_audit_lock_file,
    append_jsonl,
    approval_tool_input_preview,
    approval_tool_input_signature,
    canonical_task_state_lock_file,
    config_path,
    load_json,
    load_jsonl,
    stable_sidecar_lock,
    summarize_failure_reason,
    utc_now,
    write_json,
)


RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION = _LOCK_PROTOCOL_VERSION
RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID = _LOCK_PROTOCOL_ID
RUNTIME_ADMISSION_SOURCE_IDS = (
    "runtime_state",
    "event_queue",
    "approval_queue",
)
RUNTIME_ADMISSION_CONFLICT_STATUSES = {
    "queued",
    "started",
    "running",
    "waiting_approval",
    "suspended_approval",
    "manual_pending",
    "retry_backoff",
    "stalled",
    "fallback",
    "admitted",
}
RUNTIME_ADMISSION_TERMINAL_WORKER_STATUSES = {
    "completed",
    "failed",
    "superseded",
    "reassigned",
    "retried",
    "done",
}


def default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "initialized_at": None,
        "last_scan_at": None,
        "tasks": {},
        "recent_terminal_tasks": [],
        "pending_handoff_keys": [],
        "seen_event_keys": {},
        "queue": {
            "events": {},
        },
        "workers": {},
        "worker_worktrees": {
            "leases": {},
        },
        "worker_worktree_cleanup": {
            "last_run": None,
        },
        "approvals": {
            "last_reconciled_at": None,
        },
        "underutilization": {
            "below_threshold_since": None,
            "last_sidecar_wave_at": None,
            "last_sidecar_wave_reason": None,
            "last_ratio": None,
        },
        "chair_rotation": {
            "current_index": 0,
            "last_chair_run_at": None,
            "last_chair_agent": None,
            "last_chair_reason": None,
            "last_review_path": None,
            "last_review_summary": None,
            "pending_review_path": None,
            "pending_review_agent": None,
            "sidecar_approved_until": None,
        },
        "provider_guardrails": {
            "dispatch_pauses": {},
            "task_failure_streaks": {},
        },
        "worker_runtime_metrics": {
            "version": 1,
            "updated_at": None,
            "totals": {},
            "last_measurements": {},
        },
        "watchdog": {
            "safe_mode_until": None,
            "safe_mode_reason": None,
            "safe_mode_started_at": None,
            "last_decision": None,
            "last_safe_mode_observed_until": None,
        },
        "coordination": {
            "last_scan_at": None,
            "files": {},
            "features": {},
        },
        "assistant_dev_bridge": {
            "last_drain_at": None,
            "last_result": None,
        },
        "supervisor": {
            "pid": None,
            "started_at": None,
            "last_heartbeat_at": None,
            "lifecycle": "idle",
            "last_successful_loop_at": None,
            "last_loop_started_at": None,
            "last_loop_finished_at": None,
            "last_loop_duration_ms": None,
            "last_loop_error": None,
            "focus_mode": None,
            "mode_status": "idle",
            "mode_switch_requested": None,
            "last_mode_switch_at": None,
            "mode_occupancy": {
                "planning": {"running": 0, "pending": 0, "queued": 0},
                "execution": {"running": 0, "pending": 0, "queued": 0},
                "coordination": {"running": 0, "pending": 0, "queued": 0},
                "chair_review": {"running": 0, "pending": 0, "queued": 0},
            },
        },
    }


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    state = deepcopy(default_state())
    if not raw:
        return state
    state.update({k: v for k, v in raw.items() if k in state or k in {"queue", "workers", "approvals", "supervisor", "coordination", "watchdog", "assistant_dev_bridge"}})
    state.setdefault("tasks", {})
    recent_terminal_tasks = state.get("recent_terminal_tasks")
    state["recent_terminal_tasks"] = recent_terminal_tasks if isinstance(recent_terminal_tasks, list) else []
    state.setdefault("pending_handoff_keys", [])
    state.setdefault("seen_event_keys", {})
    state.setdefault("queue", {})
    state["queue"].setdefault("events", {})
    state.setdefault("workers", {})
    state.setdefault("worker_worktrees", {})
    state["worker_worktrees"].setdefault("leases", {})
    state.setdefault("worker_worktree_cleanup", {})
    state["worker_worktree_cleanup"].setdefault("last_run", None)
    state.setdefault("approvals", {})
    state["approvals"].setdefault("last_reconciled_at", None)
    state.setdefault("underutilization", {})
    state["underutilization"].setdefault("below_threshold_since", None)
    state["underutilization"].setdefault("last_sidecar_wave_at", None)
    state["underutilization"].setdefault("last_sidecar_wave_reason", None)
    state["underutilization"].setdefault("last_ratio", None)
    state.setdefault("chair_rotation", {})
    state["chair_rotation"].setdefault("current_index", 0)
    state["chair_rotation"].setdefault("last_chair_run_at", None)
    state["chair_rotation"].setdefault("last_chair_agent", None)
    state["chair_rotation"].setdefault("last_chair_reason", None)
    state["chair_rotation"].setdefault("last_review_path", None)
    state["chair_rotation"].setdefault("last_review_summary", None)
    state["chair_rotation"].setdefault("pending_review_path", None)
    state["chair_rotation"].setdefault("pending_review_agent", None)
    state["chair_rotation"].setdefault("sidecar_approved_until", None)
    state.setdefault("provider_guardrails", {})
    state["provider_guardrails"].setdefault("dispatch_pauses", {})
    state["provider_guardrails"].setdefault("task_failure_streaks", {})
    state.setdefault("worker_runtime_metrics", {})
    state["worker_runtime_metrics"].setdefault("version", 1)
    state["worker_runtime_metrics"].setdefault("updated_at", None)
    state["worker_runtime_metrics"].setdefault("totals", {})
    state["worker_runtime_metrics"].setdefault("last_measurements", {})
    state.setdefault("watchdog", {})
    state["watchdog"].setdefault("safe_mode_until", None)
    state["watchdog"].setdefault("safe_mode_reason", None)
    state["watchdog"].setdefault("safe_mode_started_at", None)
    state["watchdog"].setdefault("last_decision", None)
    state["watchdog"].setdefault("last_safe_mode_observed_until", None)
    state.setdefault("coordination", {})
    state["coordination"].setdefault("last_scan_at", None)
    state["coordination"].setdefault("files", {})
    state["coordination"].setdefault("features", {})
    state.setdefault("assistant_dev_bridge", {})
    state["assistant_dev_bridge"].setdefault("last_drain_at", None)
    state["assistant_dev_bridge"].setdefault("last_result", None)
    state.setdefault("supervisor", {})
    state["supervisor"].setdefault("pid", None)
    state["supervisor"].setdefault("started_at", None)
    state["supervisor"].setdefault("last_heartbeat_at", None)
    state["supervisor"].setdefault("lifecycle", "idle")
    state["supervisor"].setdefault("last_successful_loop_at", None)
    state["supervisor"].setdefault("last_loop_started_at", None)
    state["supervisor"].setdefault("last_loop_finished_at", None)
    state["supervisor"].setdefault("last_loop_duration_ms", None)
    state["supervisor"].setdefault("last_loop_error", None)
    state["supervisor"].setdefault("focus_mode", None)
    state["supervisor"].setdefault("mode_status", "idle")
    state["supervisor"].setdefault("mode_switch_requested", None)
    state["supervisor"].setdefault("last_mode_switch_at", None)
    state["supervisor"].setdefault("mode_occupancy", {})
    for mode_name in ("planning", "execution", "coordination", "chair_review"):
        bucket = state["supervisor"]["mode_occupancy"].setdefault(mode_name, {})
        bucket.setdefault("running", 0)
        bucket.setdefault("pending", 0)
        bucket.setdefault("queued", 0)
    pauses = state.get("provider_guardrails", {}).get("dispatch_pauses", {}) or {}
    normalized_pauses: dict[str, Any] = {}
    for provider, entry in pauses.items():
        if not isinstance(entry, dict):
            continue
        summary = summarize_failure_reason(entry.get("reason"), provider)
        normalized = deepcopy(entry)
        normalized["summary"] = str(entry.get("summary") or summary.get("summary") or "").strip()
        normalized["detail"] = str(entry.get("detail") or summary.get("detail") or "").strip()
        normalized["failure_kind"] = str(entry.get("failure_kind") or summary.get("kind") or "").strip()
        normalized["reason"] = normalized["summary"]
        normalized_pauses[str(provider)] = normalized
    state["provider_guardrails"]["dispatch_pauses"] = normalized_pauses
    state["version"] = 2
    return state


ACTIVE_QUEUE_STATUSES = {"running", "waiting_approval", "suspended_approval", "retry_backoff", "manual_pending", "stalled", "started", "fallback"}


def _rebuild_queue_records(state: dict[str, Any], queued_events: list[dict[str, Any]]) -> None:
    valid_event_ids = [event.get("event_id") for event in queued_events if event.get("event_id")]
    queue = state.setdefault("queue", {})
    existing_records = queue.setdefault("events", {})
    queue["events"] = {
        event_id: deepcopy(existing_records.get(event_id, {"attempt_count": 0, "status": "queued"}))
        for event_id in valid_event_ids
    }

    workers = state.setdefault("workers", {})
    for event_id, record in queue["events"].items():
        related = [worker for worker in workers.values() if worker.get("queue_event_id") == event_id]
        if not related:
            continue
        latest = sorted(related, key=lambda item: item.get("last_event_at") or "", reverse=True)[0]
        if any(worker.get("status") in ACTIVE_QUEUE_STATUSES for worker in related):
            record["status"] = "manual_pending" if any(worker.get("status") in {"manual_pending", "waiting_approval"} for worker in related) else "started"
            continue
        if any(worker.get("status") == "failed" for worker in related):
            record["status"] = "failed"
            record["processed_at"] = latest.get("last_event_at")
            if latest.get("last_error"):
                record["error"] = latest.get("last_error")
            continue
        record["status"] = "completed"
        record["processed_at"] = latest.get("last_event_at")




def prune_worker_records(state: dict[str, Any], tasks_by_id: dict[str, str] | None = None) -> None:
    tasks_by_id = tasks_by_id or {}
    queue_events = state.setdefault("queue", {}).setdefault("events", {})
    workers = state.setdefault("workers", {})
    keep: dict[str, Any] = {}
    for run_id, worker in workers.items():
        status = str(worker.get("status") or "")
        task_id = str(worker.get("task_id") or "")
        event_id = worker.get("queue_event_id")
        task_status = str(tasks_by_id.get(task_id) or "")
        if status in {"running", "started", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "fallback", "stalled"}:
            keep[run_id] = worker
            continue
        if event_id and event_id in queue_events and queue_events[event_id].get("status") not in {"completed", "failed", "done"}:
            keep[run_id] = worker
            continue
        if task_status and task_status not in {"done", "review_approved"} and status == "completed":
            keep[run_id] = worker
            continue
        # Drop terminal workers once the queue event is settled, or the task itself is already terminal.
        if status in {"failed", "completed", "superseded", "reassigned"}:
            continue
        keep[run_id] = worker
    state["workers"] = keep

def runtime_admission_lock_path(config: dict[str, Any]) -> Path:
    try:
        return config_path(config, "state_file").parent / "runtime-admission.lock"
    except KeyError:
        status_path = config_path(config, "status_file")
        return status_path.parent / ".orchestrator" / "runtime-admission.lock"


@contextmanager
def runtime_state_lock(
    config: dict[str, Any],
    *,
    shared: bool = False,
    nonblocking: bool = False,
):
    """Serialize runtime state, event queue, and approval queue as one plane."""

    try:
        lock_path = runtime_admission_lock_path(config)
    except KeyError:
        # Pure unit tests replace every runtime I/O function and intentionally
        # pass no paths. A real configuration with a paths section remains
        # fail-closed if state_file is absent.
        if config.get("paths"):
            raise
        yield None
        return
    with stable_sidecar_lock(
        lock_path,
        plane="runtime_admission",
        shared=shared,
        nonblocking=nonblocking,
    ) as handle:
        yield handle


def _load_runtime_state_unlocked(config: dict[str, Any]) -> dict[str, Any]:
    state = migrate_state(load_json(config_path(config, "state_file"), default=default_state()))
    queued_events = load_jsonl(config_path(config, "event_queue"))
    _rebuild_queue_records(state, queued_events)

    valid_pending_event_ids = set(state.setdefault("queue", {}).setdefault("events", {}))
    workers = state.setdefault("workers", {})
    stale_manual_workers = [
        run_id
        for run_id, worker in workers.items()
        if worker.get("status") == "manual_pending" and worker.get("queue_event_id") not in valid_pending_event_ids
    ]
    for run_id in stale_manual_workers:
        workers.pop(run_id, None)

    try:
        pending_approval_runs = {
            str(item.get("worker_run_id") or "")
            for item in load_approval_state(config).get("pending", [])
            if item.get("worker_run_id")
        }
    except KeyError:
        pending_approval_runs = set()
    # Approval-gated workers without a surviving queue event or pending approval
    # are stale runtime leftovers. Once both coordination anchors are gone,
    # keeping them around only causes dashboards and health checks to report
    # ghost workers.
    stale_approval_workers = [
        run_id
        for run_id, worker in workers.items()
        if worker.get("status") in {"waiting_approval", "suspended_approval"}
        and worker.get("queue_event_id") not in valid_pending_event_ids
        and str(run_id) not in pending_approval_runs
    ]
    for run_id in stale_approval_workers:
        workers.pop(run_id, None)

    prune_worker_records(state)
    return state


def _save_runtime_state_unlocked(config: dict[str, Any], state: dict[str, Any]) -> None:
    write_json(config_path(config, "state_file"), migrate_state(state))


def load_runtime_state(config: dict[str, Any]) -> dict[str, Any]:
    with runtime_state_lock(config, shared=True):
        return _load_runtime_state_unlocked(config)


def load_runtime_state_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Return an atomic-file projection without joining a mutation lock.

    Dashboard code may call this while holding the later task-state lock. It
    must never be used for an admission or write decision.
    """

    return _load_runtime_state_unlocked(config)


def save_runtime_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    with runtime_state_lock(config, shared=False):
        _save_runtime_state_unlocked(config, state)


def load_event_queue(config: dict[str, Any]) -> list[dict[str, Any]]:
    with runtime_state_lock(config, shared=True):
        return load_jsonl(config_path(config, "event_queue"))


def enqueue_event(config: dict[str, Any], event: dict[str, Any]) -> None:
    with runtime_state_lock(config, shared=False):
        append_jsonl(config_path(config, "event_queue"), event)


def replace_event_queue(config: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Durably replace the queue while retaining the stable runtime sidecar."""

    with runtime_state_lock(config, shared=False):
        path = config_path(config, "event_queue")
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = "".join(
            json.dumps(event, ensure_ascii=False) + "\n" for event in events
        )
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError("event queue readback mismatch")


def queue_event_record(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    queue = state.setdefault("queue", {})
    events = queue.setdefault("events", {})
    record = events.setdefault(event_id, {"attempt_count": 0, "status": "queued"})
    return record


def default_approval_state() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": None,
        "pending": [],
        "history": [],
    }


def _normalize_approval_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(item)
    tool_input = normalized.get("tool_input")
    signature = str(normalized.get("tool_input_signature") or "").strip()
    preview = str(normalized.get("tool_input_preview") or "").strip()
    if not signature:
        signature = approval_tool_input_signature(tool_input if tool_input is not None else {})
    if not preview and tool_input is not None:
        preview = approval_tool_input_preview(tool_input)
    normalized["tool_input_signature"] = signature
    normalized["tool_input_preview"] = preview
    normalized.pop("tool_input", None)
    normalized.pop("request_payload", None)
    normalized.pop("broker_decision", None)
    normalized.pop("permission_payload", None)
    return normalized


def load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    with runtime_state_lock(config, shared=True):
        raw = load_json(config_path(config, "approval_queue"), default=default_approval_state())
        state = deepcopy(default_approval_state())
        if isinstance(raw, dict):
            state.update(raw)
        state.setdefault("pending", [])
        state.setdefault("history", [])
        state["pending"] = [_normalize_approval_item(item) for item in state["pending"] if isinstance(item, dict)]
        state["history"] = [_normalize_approval_item(item) for item in state["history"] if isinstance(item, dict)]
        state["version"] = 2
        return state


def save_approval_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    with runtime_state_lock(config, shared=False):
        payload = deepcopy(state)
        payload["pending"] = [_normalize_approval_item(item) for item in payload.get("pending", []) if isinstance(item, dict)]
        payload["history"] = [_normalize_approval_item(item) for item in payload.get("history", []) if isinstance(item, dict)]
        payload["version"] = 2
        payload["updated_at"] = utc_now()
        write_json(config_path(config, "approval_queue"), payload)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _strict_json_object(raw: bytes, *, source: str) -> dict[str, Any]:
    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key in {source}: {key}")
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=reject_duplicate_pairs,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{source} must be an object")
    return value


def _strict_runtime_sources(
    config: dict[str, Any],
) -> tuple[
    dict[str, bytes],
    dict[str, str],
    dict[str, Any] | None,
    list[dict[str, Any]] | None,
    dict[str, Any] | None,
    str | None,
]:
    paths = {
        "runtime_state": config_path(config, "state_file"),
        "event_queue": config_path(config, "event_queue"),
        "approval_queue": config_path(config, "approval_queue"),
    }
    bodies: dict[str, bytes] = {}
    source_sha256: dict[str, str] = {}
    source_error: str | None = None
    for source_id in RUNTIME_ADMISSION_SOURCE_IDS:
        path = paths[source_id]
        try:
            body = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            body = b""
            source_error = source_error or "runtime_source_invalid"
        bodies[source_id] = body
        source_sha256[source_id] = hashlib.sha256(body).hexdigest()
        if not body.strip():
            source_error = source_error or "runtime_source_invalid"

    if source_error:
        return bodies, source_sha256, None, None, None, source_error

    try:
        runtime = _strict_json_object(
            bodies["runtime_state"], source="runtime_state"
        )
        if (
            runtime.get("version") != 2
            or not isinstance(runtime.get("workers"), dict)
            or not isinstance(runtime.get("queue"), dict)
            or not isinstance((runtime.get("queue") or {}).get("events"), dict)
            or any(
                not isinstance(worker, dict)
                or not isinstance(worker.get("task_id"), str)
                or not str(worker.get("task_id") or "").strip()
                for worker in runtime["workers"].values()
            )
            or any(
                not isinstance(record, dict)
                for record in runtime["queue"]["events"].values()
            )
        ):
            raise ValueError("runtime state schema")

        events: list[dict[str, Any]] = []
        event_ids: set[str] = set()
        for line_number, line in enumerate(
            bodies["event_queue"].splitlines(), start=1
        ):
            if not line.strip():
                continue
            event = _strict_json_object(
                line, source=f"event_queue:{line_number}"
            )
            event_id = event.get("event_id")
            task_id = event.get("task_id")
            if (
                not isinstance(event_id, str)
                or not event_id.strip()
                or event_id in event_ids
                or not isinstance(task_id, str)
                or not task_id.strip()
            ):
                raise ValueError("event queue schema")
            event_ids.add(event_id)
            events.append(event)
        if not events:
            raise ValueError("event queue empty")

        approvals = _strict_json_object(
            bodies["approval_queue"], source="approval_queue"
        )
        if (
            approvals.get("version") != 2
            or not isinstance(approvals.get("pending"), list)
            or not isinstance(approvals.get("history"), list)
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("task_id"), str)
                or not str(item.get("task_id") or "").strip()
                for item in approvals["pending"]
            )
            or any(not isinstance(item, dict) for item in approvals["history"])
        ):
            raise ValueError("approval queue schema")
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return bodies, source_sha256, None, None, None, "runtime_source_invalid"
    return bodies, source_sha256, runtime, events, approvals, None


def _runtime_conflicts(
    runtime: dict[str, Any],
    events: list[dict[str, Any]],
    approvals: dict[str, Any],
    task_ids: set[str],
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []

    def add(source_id: str, task_id: str, status: str, record_id: str) -> None:
        normalized = str(status or "").strip().lower()
        if normalized not in RUNTIME_ADMISSION_CONFLICT_STATUSES:
            return
        conflicts.append(
            {
                "source_id": source_id,
                "task_id": task_id,
                "status": normalized,
                "record_id": record_id,
            }
        )

    for run_key, worker in runtime["workers"].items():
        task_id = str(worker.get("task_id") or "").strip()
        if task_id not in task_ids:
            continue
        run_id = str(worker.get("run_id") or run_key or "<missing-run-id>")
        status = str(worker.get("status") or "").strip().lower()
        add("runtime_state", task_id, status, run_id)
        if (
            isinstance(worker.get("execution_admission"), dict)
            and status not in RUNTIME_ADMISSION_TERMINAL_WORKER_STATUSES
        ):
            add("runtime_state", task_id, "admitted", run_id)

    queue_records = runtime["queue"]["events"]
    for event in events:
        task_id = str(event["task_id"]).strip()
        if task_id not in task_ids:
            continue
        event_id = str(event["event_id"]).strip()
        record = queue_records.get(event_id)
        status = str(event.get("status") or "").strip().lower()
        if not status and isinstance(record, dict):
            status = str(record.get("status") or "").strip().lower()
        add("event_queue", task_id, status or "queued", event_id)

    for index, item in enumerate(approvals["pending"]):
        task_id = str(item.get("task_id") or "").strip()
        if task_id not in task_ids:
            continue
        approval_id = str(
            item.get("approval_id")
            or item.get("request_id")
            or item.get("tool_use_id")
            or item.get("worker_run_id")
            or f"pending:{index}"
        )
        status = str(item.get("status") or "waiting_approval").strip().lower()
        if status not in RUNTIME_ADMISSION_CONFLICT_STATUSES:
            status = "waiting_approval"
        add("approval_queue", task_id, status, approval_id)

    unique = {
        (item["source_id"], item["task_id"], item["status"], item["record_id"]): item
        for item in conflicts
    }
    return [unique[key] for key in sorted(unique)]


def _ordered_task_ids(
    task_ids: Iterable[str] | str | None,
) -> tuple[list[str], str | None]:
    if isinstance(task_ids, str):
        raw: list[Any] = [task_ids]
    elif task_ids is None:
        raw = []
    else:
        try:
            raw = list(task_ids)
        except TypeError:
            raw = []
    if not raw:
        return [], "task_ids_empty"
    if any(not isinstance(value, str) or not value.strip() for value in raw):
        return [str(value or "").strip() for value in raw], "task_ids_invalid"
    ordered = [value.strip() for value in raw]
    if len(set(ordered)) != len(ordered):
        return ordered, "task_ids_duplicate"
    return ordered, None


@contextmanager
def tasks_runtime_admission_guard(
    config: dict[str, Any],
    task_ids: Iterable[str] | str | None,
    *,
    strict: bool,
    shared: bool,
    nonblocking: bool,
):
    """Hold one strict runtime snapshot while a nested task transaction acts."""

    ordered_ids, input_error = _ordered_task_ids(task_ids)
    with runtime_state_lock(
        config,
        shared=shared,
        nonblocking=nonblocking,
    ):
        (
            _bodies,
            source_sha256,
            runtime,
            events,
            approvals,
            source_error,
        ) = _strict_runtime_sources(config)
        conflicts: list[dict[str, str]] = []
        if source_error is None and runtime is not None and events is not None and approvals is not None:
            conflicts = _runtime_conflicts(
                runtime,
                events,
                approvals,
                set(ordered_ids),
            )
        reason_id = input_error or source_error or "clear"
        if reason_id == "clear" and strict is not True:
            reason_id = "strict_required"
        if reason_id == "clear" and conflicts:
            reason_id = "target_has_runtime_admission"
        decision = {
            "schema_version": 1,
            "protocol_id": RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
            "strict": strict,
            "lock_mode": "shared" if shared else "exclusive",
            "task_ids": ordered_ids,
            "source_sha256": source_sha256,
            "conflicts": conflicts,
            "allowed": reason_id == "clear" and not conflicts,
            "reason_id": reason_id,
            "snapshot_sha256": _canonical_sha256(source_sha256),
        }
        yield decision


def task_runtime_admission_guard(
    config: dict[str, Any],
    task_id: str,
    *,
    strict: bool = True,
    shared: bool = False,
    nonblocking: bool = False,
):
    return tasks_runtime_admission_guard(
        config,
        [task_id],
        strict=strict,
        shared=shared,
        nonblocking=nonblocking,
    )


def runtime_capability_signature_payload(
    manifest: dict[str, Any],
    completion_evidence: dict[str, Any],
) -> bytes:
    evidence = {
        key: deepcopy(value)
        for key, value in completion_evidence.items()
        if key != "signature"
    }
    return _canonical_json_bytes(
        {
            "schema_version": 1,
            "protocol_id": RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
            "writer_registry_sha256": manifest.get("writer_registry_sha256"),
            "verifier_capability_sha256": (
                (manifest.get("writers") or {}).get(manifest.get("module_path"))
            ),
            "completion_evidence": evidence,
        }
    )


def _protected_verifier_policy(root: Path) -> dict[str, Any]:
    configured = str(
        os.environ.get("PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY") or ""
    ).strip()
    path = (
        Path(configured).expanduser().resolve()
        if configured
        else root / ".orchestrator" / "runtime-lock-verifier-policy.json"
    )
    if path.is_symlink() or not path.is_file():
        raise ValueError("protected verifier policy is missing")
    stat_result = path.stat()
    if stat_result.st_mode & 0o022 or stat_result.st_uid not in {0, os.getuid()}:
        raise ValueError("protected verifier policy permissions are unsafe")
    policy = _strict_json_object(path.read_bytes(), source=str(path))
    if set(policy) != {
        "schema_version",
        "protocol_id",
        "policy_version",
        "key_id",
        "public_key_base64",
        "revoked_key_ids",
        "ledger_entries",
    }:
        raise ValueError("protected verifier policy schema is not exact")
    if (
        policy.get("schema_version") != 1
        or policy.get("protocol_id") != RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID
        or not isinstance(policy.get("revoked_key_ids"), list)
        or not isinstance(policy.get("ledger_entries"), list)
    ):
        raise ValueError("protected verifier policy contract mismatch")
    return policy


def verify_runtime_lock_capability(
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    writer_registry: dict[str, Any],
    completion_evidence: dict[str, Any],
    repository_root: str | Path,
) -> dict[str, Any]:
    """Verify a reviewer signature plus a protected post-merge ledger entry."""

    root = Path(repository_root).expanduser().resolve()
    registry_digest = str(manifest.get("writer_registry_sha256") or "")
    evidence_digest = str(
        manifest.get("bootstrap_completion_evidence_sha256") or ""
    )
    merge_sha = str(manifest.get("merged_commit_sha") or "")
    allowed = False
    reason_id = "protected_evidence_invalid"
    try:
        registry_path = root / str(manifest["writer_registry_path"])
        evidence_path = root / str(manifest["bootstrap_completion_evidence_path"])
        actual_registry_digest = hashlib.sha256(registry_path.read_bytes()).hexdigest()
        actual_evidence_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if actual_registry_digest != registry_digest or actual_evidence_digest != evidence_digest:
            raise ValueError("content binding mismatch")
        if (
            writer_registry.get("schema_version") != 1
            or writer_registry.get("protocol_id")
            != RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID
            or writer_registry.get("transaction_scope")
            != "complete_read_validate_mutate_replace"
            or writer_registry.get("direct_canonical_writes_forbidden") is not True
            or writer_registry.get("writers") != manifest.get("writers")
            or completion_evidence.get("schema_version") != 1
            or completion_evidence.get("conclusion") != "passed"
            or completion_evidence.get("signature_algorithm") != "ed25519"
            or completion_evidence.get("verifier_capability_sha256")
            != (manifest.get("writers") or {}).get(manifest.get("module_path"))
        ):
            raise ValueError("capability evidence mismatch")

        policy = _protected_verifier_policy(root)
        if (
            completion_evidence.get("policy_version") != policy.get("policy_version")
            or completion_evidence.get("key_id") != policy.get("key_id")
            or policy.get("key_id") in policy.get("revoked_key_ids", [])
        ):
            raise ValueError("review key is not active")
        public_key_bytes = base64.b64decode(
            str(policy["public_key_base64"]), validate=True
        )
        signature = base64.b64decode(
            str(completion_evidence["signature"]), validate=True
        )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature,
            runtime_capability_signature_payload(manifest, completion_evidence),
        )

        expected_entry = {
            "ledger_entry_id": completion_evidence.get("ledger_entry_id"),
            "verdict_id": completion_evidence.get("verdict_id"),
            "task_id": completion_evidence.get("task_id"),
            "reviewer_runtime_identity": completion_evidence.get(
                "reviewer_runtime_identity"
            ),
            "merged_commit_sha": merge_sha,
            "manifest_sha256": manifest_sha256,
            "writer_registry_sha256": registry_digest,
            "completion_evidence_sha256": evidence_digest,
            "revocation_checked_at": completion_evidence.get(
                "revocation_checked_at"
            ),
            "status": "accepted",
        }
        if set(expected_entry) != {
            "ledger_entry_id",
            "verdict_id",
            "task_id",
            "reviewer_runtime_identity",
            "merged_commit_sha",
            "manifest_sha256",
            "writer_registry_sha256",
            "completion_evidence_sha256",
            "revocation_checked_at",
            "status",
        } or expected_entry not in policy["ledger_entries"]:
            raise ValueError("protected ledger entry is missing")
        allowed = True
        reason_id = "verified"
    except Exception:
        allowed = False
        reason_id = "protected_evidence_invalid"

    return {
        "schema_version": 1,
        "protocol_id": RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
        "allowed": allowed,
        "reason_id": reason_id,
        "manifest_sha256": manifest_sha256,
        "writer_registry_sha256": registry_digest,
        "completion_evidence_sha256": evidence_digest,
        "merged_commit_sha": merge_sha,
    }
