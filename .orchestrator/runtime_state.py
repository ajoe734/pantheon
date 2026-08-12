#!/usr/bin/env python3
from __future__ import annotations

import base64
import ast
import hashlib
import json
import os
import stat
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from common import (
    RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID as _LOCK_PROTOCOL_ID,
    RUNTIME_TASK_AUDIT_LOCK_ORDER as _LOCK_ORDER,
    RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION as _LOCK_PROTOCOL_VERSION,
    activity_audit_lock_file,
    approval_tool_input_preview,
    approval_tool_input_signature,
    canonical_task_state_lock_file,
    config_path,
    durable_write_bytes,
    load_json,
    load_jsonl,
    stable_sidecar_lock,
    summarize_failure_reason,
    utc_now,
    write_json,
)


RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION = _LOCK_PROTOCOL_VERSION
RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID = _LOCK_PROTOCOL_ID
RUNTIME_TASK_AUDIT_LOCK_ORDER = _LOCK_ORDER
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
    "retry_backoff",
    "stalled",
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
RUNTIME_LOCK_REQUIRED_WRITER_PATHS = (
    ".orchestrator/runtime_state.py",
    ".orchestrator/supervisor.py",
    ".orchestrator/common.py",
    ".orchestrator/approval_queue.py",
    ".orchestrator/watch_events.py",
    ".orchestrator/supervisor_watchdog.py",
    "scripts/ai_status.py",
)
RUNTIME_LOCK_REQUIRED_API = (
    "tasks_runtime_admission_guard",
    "canonical_task_state_lock_file",
    "activity_audit_lock_file",
    "verify_runtime_lock_capability",
)
RUNTIME_LOCK_SOURCE_ROOTS = (".orchestrator", "scripts")
RUNTIME_LOCK_SOURCE_SUFFIXES = {".py", ".sh"}
RUNTIME_LOCK_WRITER_SCANNER_ID = "pantheon-canonical-writer-ast-v1"
_CANONICAL_PATH_LITERALS = {
    "ai-status.json",
    "ai-activity-log.jsonl",
    "state.json",
    "event-queue.jsonl",
    "approval-queue.json",
}
_DIRECT_SINK_ATTRIBUTES = {
    "write_text",
    "write_bytes",
    "replace",
    "rename",
    "unlink",
    "truncate",
}
_DIRECT_SINK_FUNCTIONS = {
    "write_json",
    "append_jsonl",
    "write_text",
    "copy_file",
    "copy2",
    "copyfile",
    "move",
    "replace",
    "rename",
}
_CANONICAL_GUARD_FUNCTIONS = {
    "assert_isolated_legacy_write_target",
    "assert_noncanonical_bundle_target",
}
_CANONICAL_LOCK_CONTEXT_FUNCTIONS = {
    "canonical_task_state_lock_file",
    "activity_audit_lock_file",
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
        "auto_commit_archive": {
            "pending_token": None,
            "pending_since": None,
            "last_run_at": None,
            "last_error": None,
        },
        "approvals": {
            "last_reconciled_at": None,
        },
        "provider_guardrails": {
            "dispatch_pauses": {},
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
                "execution": {"running": 0, "pending": 0, "queued": 0},
            },
        },
    }


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    state = deepcopy(default_state())
    if not raw:
        return state
    state.update({k: v for k, v in raw.items() if k in state or k in {"queue", "workers", "approvals", "supervisor", "watchdog", "assistant_dev_bridge"}})
    # V2 does not preserve obsolete control-plane buckets as dormant state.
    # Their presence previously allowed dashboard/recovery code to revive a
    # retired authority after a restart.
    for retired_key in ("underutilization", "chair_rotation", "coordination"):
        state.pop(retired_key, None)
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
    state.setdefault("auto_commit_archive", {})
    state["auto_commit_archive"].setdefault("pending_token", None)
    state["auto_commit_archive"].setdefault("pending_since", None)
    state["auto_commit_archive"].setdefault("last_run_at", None)
    state["auto_commit_archive"].setdefault("last_error", None)
    state.setdefault("approvals", {})
    state["approvals"].setdefault("last_reconciled_at", None)
    state.setdefault("provider_guardrails", {})
    state["provider_guardrails"].setdefault("dispatch_pauses", {})
    state["provider_guardrails"].pop("task_failure_streaks", None)
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
    raw_occupancy = state["supervisor"].get("mode_occupancy")
    raw_execution = (
        raw_occupancy.get("execution")
        if isinstance(raw_occupancy, dict)
        and isinstance(raw_occupancy.get("execution"), dict)
        else {}
    )
    execution_occupancy: dict[str, int] = {}
    for key in ("running", "pending", "queued"):
        try:
            execution_occupancy[key] = max(0, int(raw_execution.get(key) or 0))
        except (TypeError, ValueError):
            execution_occupancy[key] = 0
    state["supervisor"]["mode_occupancy"] = {
        "execution": execution_occupancy
    }
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


ACTIVE_QUEUE_STATUSES = {"running", "waiting_approval", "suspended_approval", "retry_backoff", "stalled", "started"}


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
            record["status"] = "waiting_approval" if any(worker.get("status") == "waiting_approval" for worker in related) else "started"
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
        if status in {"running", "started", "waiting_approval", "suspended_approval", "retry_backoff", "stalled"}:
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


def _assert_canonical_runtime_data_leaf(path: Path, *, source_id: str) -> None:
    """Reject an existing runtime data leaf without following a symlink.

    Missing leaves remain valid for the ordinary initialization helpers; the
    strict admission snapshot rejects them as ``runtime_source_invalid``.  An
    existing leaf, however, must already be a regular file before the stable
    sidecar is acquired.  ``lstat`` is deliberate: checking ``exists`` or
    resolving the complete path would follow an attacker-controlled target.
    """

    try:
        leaf_stat = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(
            f"canonical {source_id} data leaf cannot be inspected: {path}"
        ) from exc
    if stat.S_ISLNK(leaf_stat.st_mode):
        raise RuntimeError(
            f"canonical {source_id} data leaf cannot be a symlink: {path}"
        )
    if not stat.S_ISREG(leaf_stat.st_mode):
        raise RuntimeError(
            f"canonical {source_id} data leaf must be a regular file: {path}"
        )


def _runtime_source_layout(
    config: dict[str, Any],
    *,
    validate_data_leaves: bool = True,
) -> tuple[dict[str, Path], Path, Path]:
    """Return canonical source paths, status root, and shared lock directory.

    The three runtime sources may live directly in a status root (kept for
    small isolated deployments/tests) or in its canonical ``.orchestrator``
    child.  They may never be split across different resolved parents.  When
    a status file is configured it pins that root; otherwise a common source
    parent named ``.orchestrator`` pins its parent as the status root.
    """

    configured = (
        config.get("paths") if isinstance(config.get("paths"), dict) else {}
    )
    key_by_source = {
        "runtime_state": "state_file",
        "event_queue": "event_queue",
        "approval_queue": "approval_queue",
    }
    source_paths: dict[str, Path] = {}
    source_roots: dict[str, Path] = {}
    for source_id in RUNTIME_ADMISSION_SOURCE_IDS:
        key = key_by_source[source_id]
        if not configured.get(key):
            continue
        requested = config_path(config, key).expanduser()
        if validate_data_leaves:
            _assert_canonical_runtime_data_leaf(requested, source_id=source_id)
        try:
            source_root = requested.parent.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(
                f"canonical {source_id} parent is invalid: {requested.parent}"
            ) from exc
        if not source_root.is_dir():
            raise RuntimeError(
                f"canonical {source_id} parent is not a directory: {source_root}"
            )
        source_paths[source_id] = source_root / requested.name
        source_roots[source_id] = source_root

    distinct_source_roots = set(source_roots.values())
    if len(distinct_source_roots) > 1:
        details = ", ".join(
            f"{source_id}={source_roots[source_id]}"
            for source_id in RUNTIME_ADMISSION_SOURCE_IDS
            if source_id in source_roots
        )
        raise RuntimeError(f"canonical runtime sources use split roots: {details}")

    status_path: Path | None = None
    status_root: Path | None = None
    if configured.get("status_file"):
        status_path = config_path(config, "status_file").expanduser()
        try:
            status_root = status_path.parent.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(
                f"canonical status root is invalid: {status_path.parent}"
            ) from exc
        if not status_root.is_dir():
            raise RuntimeError(
                f"canonical status root is not a directory: {status_root}"
            )

    source_root = next(iter(distinct_source_roots), None)
    if status_root is not None and source_root is not None:
        allowed_source_roots = {status_root, status_root / ".orchestrator"}
        if source_root not in allowed_source_roots:
            raise RuntimeError(
                "canonical runtime source root does not belong to the status "
                f"root: source_root={source_root}, status_root={status_root}"
            )
    elif status_root is None and source_root is not None:
        status_root = (
            source_root.parent
            if source_root.name == ".orchestrator"
            else source_root
        )

    if status_root is None:
        raise KeyError(
            "Missing canonical runtime/status path for runtime admission lock"
        )
    if configured.get("activity_log"):
        activity_path = config_path(config, "activity_log").expanduser()
        try:
            activity_root = activity_path.parent.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(
                f"canonical activity audit root is invalid: {activity_path.parent}"
            ) from exc
        if activity_root != status_root:
            raise RuntimeError(
                "canonical activity audit root does not match the status root: "
                f"activity_root={activity_root}, status_root={status_root}"
            )
    lock_root = status_root / ".orchestrator"
    try:
        lock_root_stat = os.lstat(lock_root)
    except FileNotFoundError:
        lock_root_stat = None
    except OSError as exc:
        raise RuntimeError(
            f"canonical runtime lock root cannot be inspected: {lock_root}"
        ) from exc
    if lock_root_stat is not None and stat.S_ISLNK(lock_root_stat.st_mode):
        raise RuntimeError(
            f"canonical runtime lock root cannot be a symlink: {lock_root}"
        )
    if lock_root_stat is not None and not stat.S_ISDIR(lock_root_stat.st_mode):
        raise RuntimeError(
            f"canonical runtime lock root must be a directory: {lock_root}"
        )
    return source_paths, status_root, lock_root


def runtime_admission_lock_path(config: dict[str, Any]) -> Path:
    _source_paths, _status_root, lock_root = _runtime_source_layout(config)
    return lock_root / "runtime-admission.lock"


@contextmanager
def _runtime_state_sidecar_lock(
    config: dict[str, Any],
    *,
    shared: bool = False,
    nonblocking: bool = False,
    validate_data_leaves: bool,
):
    try:
        _source_paths, _status_root, lock_root = _runtime_source_layout(
            config,
            validate_data_leaves=validate_data_leaves,
        )
        lock_path = lock_root / "runtime-admission.lock"
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
        # Revalidate after acquiring the stable inode so a pre-acquisition
        # pathname swap cannot move a source outside the shared status root.
        _runtime_source_layout(
            config,
            validate_data_leaves=validate_data_leaves,
        )
        yield handle


@contextmanager
def runtime_state_lock(
    config: dict[str, Any],
    *,
    shared: bool = False,
    nonblocking: bool = False,
):
    """Serialize runtime state, event queue, and approval queue as one plane."""

    with _runtime_state_sidecar_lock(
        config,
        shared=shared,
        nonblocking=nonblocking,
        validate_data_leaves=True,
    ) as handle:
        yield handle


def _load_runtime_state_unlocked(config: dict[str, Any]) -> dict[str, Any]:
    state = migrate_state(load_json(config_path(config, "state_file"), default=default_state()))
    queued_events = load_jsonl(config_path(config, "event_queue"))
    _rebuild_queue_records(state, queued_events)

    valid_pending_event_ids = set(
        state.setdefault("queue", {}).setdefault("events", {})
    )
    workers = state.setdefault("workers", {})
    try:
        pending_approval_runs = {
            str(item.get("worker_run_id") or "")
            for item in _load_approval_state_unlocked(config).get("pending", [])
            if item.get("worker_run_id")
        }
    except KeyError:
        pending_approval_runs = set()
    # Approval-gated workers without a surviving queue event or pending approval
    # are stale runtime leftovers. Once both queue/approval anchors are gone,
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
    _write_runtime_json_unlocked(
        config_path(config, "state_file"),
        migrate_state(state),
        source_id="runtime_state",
    )


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


def replace_event_queue(config: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Durably replace the queue while retaining the stable runtime sidecar."""

    with runtime_state_lock(config, shared=False):
        path = config_path(config, "event_queue")
        serialized = "".join(
            json.dumps(event, ensure_ascii=False) + "\n" for event in events
        ).encode("utf-8")
        _write_runtime_bytes_unlocked(
            path,
            serialized,
            source_id="event_queue",
        )


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


def _load_approval_state_unlocked(config: dict[str, Any]) -> dict[str, Any]:
    raw = load_json(
        config_path(config, "approval_queue"),
        default=default_approval_state(),
    )
    state = deepcopy(default_approval_state())
    if isinstance(raw, dict):
        state.update(raw)
    state.setdefault("pending", [])
    state.setdefault("history", [])
    state["pending"] = [
        _normalize_approval_item(item)
        for item in state["pending"]
        if isinstance(item, dict)
    ]
    state["history"] = [
        _normalize_approval_item(item)
        for item in state["history"]
        if isinstance(item, dict)
    ]
    state["version"] = 2
    return state


def load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    with runtime_state_lock(config, shared=True):
        return _load_approval_state_unlocked(config)


def save_approval_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    with runtime_state_lock(config, shared=False):
        payload = deepcopy(state)
        payload["pending"] = [_normalize_approval_item(item) for item in payload.get("pending", []) if isinstance(item, dict)]
        payload["history"] = [_normalize_approval_item(item) for item in payload.get("history", []) if isinstance(item, dict)]
        payload["version"] = 2
        payload["updated_at"] = utc_now()
        _write_runtime_json_unlocked(
            config_path(config, "approval_queue"),
            payload,
            source_id="approval_queue",
        )


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


def _read_canonical_runtime_source(path: Path, *, source_id: str) -> bytes:
    """Read one regular runtime leaf without ever following a leaf symlink."""

    _assert_canonical_runtime_data_leaf(path, source_id=source_id)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(path)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or path_stat.st_dev != descriptor_stat.st_dev
            or path_stat.st_ino != descriptor_stat.st_ino
        ):
            raise RuntimeError(
                f"canonical {source_id} data leaf changed during read: {path}"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_stat = os.lstat(path)
        if (
            stat.S_ISLNK(after_stat.st_mode)
            or after_stat.st_dev != descriptor_stat.st_dev
            or after_stat.st_ino != descriptor_stat.st_ino
        ):
            raise RuntimeError(
                f"canonical {source_id} data leaf changed during read: {path}"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_runtime_bytes_unlocked(
    path: Path,
    payload: bytes,
    *,
    source_id: str,
) -> None:
    _assert_canonical_runtime_data_leaf(path, source_id=source_id)
    durable_write_bytes(path, payload)
    if _read_canonical_runtime_source(path, source_id=source_id) != payload:
        raise RuntimeError(f"canonical {source_id} readback mismatch: {path}")


def _write_runtime_json_unlocked(
    path: Path,
    payload: dict[str, Any],
    *,
    source_id: str,
) -> None:
    serialized = (
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _write_runtime_bytes_unlocked(path, serialized, source_id=source_id)


def _pread_exact(descriptor: int, size: int, offset: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.pread(descriptor, remaining, offset)
        if not chunk:
            break
        chunk = chunk[:remaining]
        chunks.append(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _append_runtime_jsonl_unlocked(
    path: Path,
    payload: dict[str, Any],
    *,
    source_id: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_canonical_runtime_data_leaf(path, source_id=source_id)
    serialized = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(path)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise RuntimeError(
                f"canonical {source_id} data leaf changed during append: {path}"
            )
        offset = os.lseek(descriptor, 0, os.SEEK_END)
        if offset and os.pread(descriptor, 1, offset - 1) != b"\n":
            raise RuntimeError(
                f"canonical {source_id} is not newline terminated: {path}"
            )
        view = memoryview(serialized)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"canonical {source_id} append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        after_stat = os.lstat(path)
        if (
            stat.S_ISLNK(after_stat.st_mode)
            or (after_stat.st_dev, after_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
            or _pread_exact(descriptor, len(serialized), offset) != serialized
        ):
            raise RuntimeError(
                f"canonical {source_id} append readback mismatch: {path}"
            )
    finally:
        os.close(descriptor)
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


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
    key_by_source = {
        "runtime_state": "state_file",
        "event_queue": "event_queue",
        "approval_queue": "approval_queue",
    }
    paths = {
        source_id: config_path(config, key_by_source[source_id])
        for source_id in RUNTIME_ADMISSION_SOURCE_IDS
    }
    bodies: dict[str, bytes] = {}
    source_sha256: dict[str, str] = {}
    source_error: str | None = None
    for source_id in RUNTIME_ADMISSION_SOURCE_IDS:
        path = paths[source_id]
        try:
            body = _read_canonical_runtime_source(path, source_id=source_id)
        except (
            FileNotFoundError,
            IsADirectoryError,
            PermissionError,
            OSError,
            RuntimeError,
        ):
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
    # Source leaves are parsed through O_NOFOLLOW below.  Deferring their
    # content/leaf verdict until after the stable sidecar is held preserves the
    # admission decision contract (``runtime_source_invalid``) while ordinary
    # runtime writers continue to reject such leaves before entering.
    with _runtime_state_sidecar_lock(
        config,
        shared=shared,
        nonblocking=nonblocking,
        validate_data_leaves=False,
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


def _lower_hex(value: Any, length: int) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _capability_repo_path(root: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is invalid")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or str(relative) != value:
        raise ValueError(f"{label} is not normalized")
    resolved = (root / relative).resolve()
    if root not in resolved.parents:
        raise ValueError(f"{label} escapes repository")
    return resolved


def _source_inventory_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for source_root in RUNTIME_LOCK_SOURCE_ROOTS:
        base = root / source_root
        if not base.is_dir():
            raise ValueError(f"runtime lock source root is missing: {source_root}")
        for path in sorted(base.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in RUNTIME_LOCK_SOURCE_SUFFIXES
                or "__pycache__" in path.parts
            ):
                continue
            if path.is_symlink():
                raise ValueError(f"runtime lock source symlink is forbidden: {path}")
            relative = path.relative_to(root).as_posix()
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _ast_symbols(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name)
    }


def _ast_has_canonical_literal(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        value = child.value.strip()
        if Path(value).name in _CANONICAL_PATH_LITERALS or value in {
            "status_file",
            "activity_log",
            "state_file",
            "event_queue",
            "approval_queue",
        }:
            return True
    return False


def _assignment_taint(
    tree: ast.AST,
    inherited: set[str] | None = None,
) -> set[str]:
    tainted = set(inherited or set())
    assignments: list[tuple[list[str], ast.AST | None]] = []
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        nodes.append(node)
        stack.extend(ast.iter_child_nodes(node))
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets = [
                name.id
                for target in node.targets
                for name in ast.walk(target)
                if isinstance(name, ast.Name)
            ]
            assignments.append((targets, node.value))
        elif isinstance(node, ast.AnnAssign):
            targets = [
                name.id
                for name in ast.walk(node.target)
                if isinstance(name, ast.Name)
            ]
            assignments.append((targets, node.value))
    changed = True
    while changed:
        changed = False
        for targets, value in assignments:
            if not targets:
                continue
            if _ast_has_canonical_literal(value) or _ast_symbols(value) & tainted:
                for target in targets:
                    if target not in tainted:
                        tainted.add(target)
                        changed = True
    return tainted


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _write_mode(call: ast.Call, *, builtin_open: bool) -> bool:
    mode_node: ast.AST | None = None
    if builtin_open and len(call.args) >= 2:
        mode_node = call.args[1]
    elif not builtin_open and call.args:
        mode_node = call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if mode_node is None:
        return False
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return True
    return any(marker in mode_node.value for marker in ("w", "a", "x", "+"))


def _sink_target(call: ast.Call, tainted: set[str]) -> tuple[ast.AST | None, str] | None:
    function = call.func
    name = _call_name(call)
    if isinstance(function, ast.Attribute):
        receiver = function.value
        if name == "open" and _write_mode(call, builtin_open=False):
            if _ast_has_canonical_literal(receiver) or _ast_symbols(receiver) & tainted:
                return receiver, "path.open(write)"
        # `os.replace(src, dst)` / `os.rename(src, dst)` are module-level
        # functions reached through attribute access (`os.replace`), not
        # `Path.replace`/`Path.rename` instance methods. Treating every
        # `<name>.replace(...)` receiver the same conflates the two shapes:
        # the receiver-name heuristic below exists to avoid false positives
        # on unrelated `.replace()` calls (e.g. `str.replace`), but for a
        # known module receiver there is no such ambiguity and the call must
        # fall through to the destination-argument check instead of
        # returning early.
        receiver_is_module_call = isinstance(receiver, ast.Name) and receiver.id in {
            "os",
            "shutil",
        }
        if name in _DIRECT_SINK_ATTRIBUTES and not receiver_is_module_call:
            if name in {"replace", "rename"}:
                receiver_symbols = _ast_symbols(receiver)
                if isinstance(receiver, ast.Call) or not any(
                    marker in symbol.lower()
                    for symbol in receiver_symbols
                    for marker in ("path", "file", "status", "log", "queue", "state", "archive")
                ):
                    return None
            if _ast_has_canonical_literal(receiver) or _ast_symbols(receiver) & tainted:
                return receiver, f"path.{name}"
        if name in {"replace", "rename", "copy", "copy2", "copyfile", "move"} and call.args:
            destination = call.args[-1]
            if _ast_has_canonical_literal(destination) or _ast_symbols(destination) & tainted:
                return destination, name
    if isinstance(function, ast.Name):
        if name == "open" and call.args and _write_mode(call, builtin_open=True):
            target = call.args[0]
            if _ast_has_canonical_literal(target) or _ast_symbols(target) & tainted:
                return target, "open(write)"
        if name in _DIRECT_SINK_FUNCTIONS and call.args:
            target = call.args[0]
            if _ast_has_canonical_literal(target) or _ast_symbols(target) & tainted:
                return target, name
    return None


def _python_writer_violations(root: Path, path: Path) -> list[dict[str, Any]]:
    relative = path.relative_to(root).as_posix()
    if (
        relative in RUNTIME_LOCK_REQUIRED_WRITER_PATHS
        or path.name.startswith("test_")
        or "tests" in path.parts
    ):
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [
            {
                "path": relative,
                "line": 0,
                "sink": "source_parse",
                "reason_id": f"writer_source_unreadable:{type(exc).__name__}",
            }
        ]
    module_taint = _assignment_taint(tree)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    scope_calls: dict[ast.AST, list[ast.Call]] = {tree: []}
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        cursor: ast.AST | None = call
        scope: ast.AST = tree
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = cursor
                break
        scope_calls.setdefault(scope, []).append(call)
    violations: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for scope, calls in scope_calls.items():
        tainted = _assignment_taint(scope, module_taint)
        guard_calls = [
            call
            for call in calls
            if _call_name(call) in _CANONICAL_GUARD_FUNCTIONS and call.args
        ]
        # A write lexically inside `with canonical_task_state_lock_file(...):`
        # (or the activity-audit equivalent) already shares the same stable
        # lock every registered writer takes, so it is not an "unregistered"
        # direct writer even though it lives outside the fixed core-protocol
        # file list above.
        lock_ranges = [
            (int(getattr(node, "lineno", 0)), int(getattr(node, "end_lineno", getattr(node, "lineno", 0))))
            for node in ast.walk(scope)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and _call_name(item.context_expr) in _CANONICAL_LOCK_CONTEXT_FUNCTIONS
                for item in node.items
            )
        ]
        for call in calls:
            sink = _sink_target(call, tainted)
            if sink is None:
                continue
            target, sink_name = sink
            key = (int(getattr(call, "lineno", 0)), sink_name)
            if key in seen:
                continue
            seen.add(key)
            target_symbols = _ast_symbols(target)
            guarded = any(
                int(getattr(guard, "lineno", 0)) < key[0]
                and (
                    bool(target_symbols & _ast_symbols(guard.args[0]))
                    or (
                        _ast_has_canonical_literal(target)
                        and _ast_has_canonical_literal(guard.args[0])
                    )
                )
                for guard in guard_calls
            )
            if guarded:
                continue
            if any(start <= key[0] <= end for start, end in lock_ranges):
                continue
            violations.append(
                {
                    "path": relative,
                    "line": key[0],
                    "sink": sink_name,
                    "reason_id": "unregistered_direct_canonical_write",
                }
            )
    return sorted(
        violations,
        key=lambda item: (item["path"], item["line"], item["sink"]),
    )


def runtime_lock_source_inventory(root: str | Path) -> dict[str, Any]:
    repository_root = Path(root).expanduser().resolve()
    files = _source_inventory_files(repository_root)
    violations: list[dict[str, Any]] = []
    for relative in files:
        path = repository_root / relative
        if path.suffix == ".py":
            violations.extend(_python_writer_violations(repository_root, path))
        elif path.suffix == ".sh":
            text = path.read_text(encoding="utf-8", errors="strict")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if (
                    any(name in line for name in _CANONICAL_PATH_LITERALS)
                    and any(
                        marker in line
                        for marker in (">", "tee ", "cp ", "mv ", "install ", "rsync ")
                    )
                ):
                    violations.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "sink": "shell_write",
                            "reason_id": "unregistered_direct_canonical_write",
                        }
                    )
    return {
        "algorithm": "sha256(canonical-json(path-to-sha256))",
        "roots": list(RUNTIME_LOCK_SOURCE_ROOTS),
        "files": files,
        "sha256": _canonical_sha256(files),
        "writer_scanner_id": RUNTIME_LOCK_WRITER_SCANNER_ID,
        "unregistered_direct_writers": sorted(
            violations,
            key=lambda item: (item["path"], item["line"], item["sink"]),
        ),
    }


def _protected_verifier_policy(root: Path) -> dict[str, Any]:
    configured = str(
        os.environ.get("PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY") or ""
    ).strip()
    if not configured:
        raise ValueError("protected verifier policy path is not configured")
    configured_path = Path(configured).expanduser()
    if not configured_path.is_absolute():
        raise ValueError("protected verifier policy path must be absolute")
    try:
        path = configured_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("protected verifier policy is missing") from exc
    if path != configured_path or path == root or root in path.parents:
        raise ValueError("protected verifier policy must be outside the repository")

    parent = path.parent
    while True:
        parent_stat = parent.stat()
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != 0
            or parent_stat.st_mode & 0o022
        ):
            raise ValueError("protected verifier policy parent is unsafe")
        if parent == parent.parent:
            break
        parent = parent.parent

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("protected verifier policy is missing")
    try:
        stat_result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(stat_result.st_mode)
            or stat_result.st_uid != 0
            or stat_result.st_mode & 0o022
        ):
            raise ValueError("protected verifier policy permissions are unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            body = handle.read()
    finally:
        os.close(descriptor)
    policy = _strict_json_object(body, source=str(path))
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


def _protected_checks_evidence(
    *,
    root: Path,
    evidence_path: Path,
    completion_evidence: dict[str, Any],
    merge_sha: str,
) -> dict[str, Any]:
    checks_path = evidence_path.with_name("checks.json")
    checks_body = checks_path.read_bytes()
    if hashlib.sha256(checks_body).hexdigest() != completion_evidence.get(
        "checks_sha256"
    ):
        raise ValueError("protected checks digest mismatch")
    checks = _strict_json_object(checks_body, source=str(checks_path))
    if set(checks) != {
        "schema_version",
        "protocol_id",
        "task_id",
        "source_inventory",
        "writer_surface_verdict",
        "validation_commands",
    }:
        raise ValueError("protected checks schema is not exact")
    inventory = runtime_lock_source_inventory(root)
    commands = checks.get("validation_commands")
    if (
        checks.get("schema_version") != 1
        or checks.get("protocol_id") != RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID
        or checks.get("task_id") != completion_evidence.get("task_id")
        or checks.get("source_inventory") != inventory
        or inventory.get("unregistered_direct_writers") != []
        or checks.get("writer_surface_verdict") != "passed"
        or not isinstance(commands, list)
        or not commands
        or any(
            not isinstance(command, dict)
            or set(command) != {"command", "conclusion", "result"}
            or command.get("conclusion") != "passed"
            or not str(command.get("command") or "").strip()
            or not str(command.get("result") or "").strip()
            for command in commands
        )
    ):
        raise ValueError("protected checks contract mismatch")

    def git_output(*arguments: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise ValueError("protected checks git verification failed")
        return result.stdout

    head_sha = git_output("rev-parse", "HEAD").decode("ascii").strip()
    if head_sha != merge_sha:
        raise ValueError("protected checks require the exact merged checkout")
    relative_checks = checks_path.relative_to(root).as_posix()
    committed_checks = git_output("show", f"{merge_sha}:{relative_checks}")
    if hashlib.sha256(committed_checks).hexdigest() != hashlib.sha256(
        checks_body
    ).hexdigest():
        raise ValueError("protected checks merged blob mismatch")
    return checks


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
        required_manifest_fields = {
            "schema_version",
            "protocol_id",
            "module_path",
            "lock_order",
            "stable_lock_paths",
            "shared_read_supported",
            "api",
            "writers",
            "writer_registry_path",
            "writer_registry_sha256",
            "bootstrap_task_id",
            "bootstrap_task_contract_sha256",
            "bootstrap_completion_evidence_path",
            "bootstrap_completion_evidence_sha256",
            "merged_commit_sha",
        }
        required_evidence_fields = {
            "schema_version",
            "task_id",
            "task_contract_sha256",
            "conclusion",
            "worker_runtime_identity",
            "reviewer_runtime_identity",
            "checks_sha256",
            "verdict_id",
            "verifier_capability_sha256",
            "signature_algorithm",
            "key_id",
            "policy_version",
            "signature",
            "revocation_checked_at",
            "ledger_entry_id",
        }
        writers = manifest.get("writers")
        if (
            set(manifest) != required_manifest_fields
            or manifest.get("schema_version") != 1
            or manifest.get("protocol_id")
            != RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID
            or manifest.get("module_path") != ".orchestrator/runtime_state.py"
            or manifest.get("lock_order")
            != list(RUNTIME_TASK_AUDIT_LOCK_ORDER)
            or manifest.get("stable_lock_paths")
            != [
                ".orchestrator/runtime-admission.lock",
                ".orchestrator/task-state.lock",
                ".orchestrator/activity-audit.lock",
            ]
            or manifest.get("shared_read_supported") is not True
            or manifest.get("api") != list(RUNTIME_LOCK_REQUIRED_API)
            or not isinstance(writers, dict)
            or set(writers) != set(RUNTIME_LOCK_REQUIRED_WRITER_PATHS)
            or not _lower_hex(manifest_sha256, 64)
            or not _lower_hex(merge_sha, 40)
            or not _lower_hex(registry_digest, 64)
            or not _lower_hex(evidence_digest, 64)
            or set(completion_evidence) != required_evidence_fields
        ):
            raise ValueError("capability manifest schema mismatch")

        for relative_path, expected_digest in writers.items():
            writer_path = _capability_repo_path(
                root,
                relative_path,
                label="runtime lock writer path",
            )
            if (
                not _lower_hex(expected_digest, 64)
                or hashlib.sha256(writer_path.read_bytes()).hexdigest()
                != expected_digest
            ):
                raise ValueError("runtime lock writer binding mismatch")
        registry_path = _capability_repo_path(
            root,
            manifest["writer_registry_path"],
            label="writer registry path",
        )
        evidence_path = _capability_repo_path(
            root,
            manifest["bootstrap_completion_evidence_path"],
            label="bootstrap completion evidence path",
        )
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
            or completion_evidence.get("task_id")
            != manifest.get("bootstrap_task_id")
            or completion_evidence.get("task_contract_sha256")
            != manifest.get("bootstrap_task_contract_sha256")
            or completion_evidence.get("worker_runtime_identity")
            == completion_evidence.get("reviewer_runtime_identity")
            or not _lower_hex(completion_evidence.get("checks_sha256"), 64)
        ):
            raise ValueError("capability evidence mismatch")

        _protected_checks_evidence(
            root=root,
            evidence_path=evidence_path,
            completion_evidence=completion_evidence,
            merge_sha=merge_sha,
        )

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
