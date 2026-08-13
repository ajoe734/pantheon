#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import stat
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


class RuntimeStateSchemaError(ValueError):
    """Raised when an ordinary V2 restart cannot safely read its cache."""


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
        "delivery_health": {
            "version": 1,
            "endpoints": {},
            "accounts": {},
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
            "runtime_phase_reservations": {},
        },
    }


def normalize_v2_runtime_cache(raw: Any) -> dict[str, Any]:
    """Load a structurally valid V2 cache.

    ``state.json`` is an ephemeral runtime cache, not canonical task
    authority.  V2 therefore does not upgrade or interpret a prior cache.
    A missing cache is a normal first-start condition.  Any other invalid
    shape is rejected on an ordinary restart: replacing it would erase active
    leases and make their durable intents launchable again.  The explicit
    direct-replacement path is the only caller allowed to rebuild a cache.
    """

    state = deepcopy(default_state())
    if raw is None:
        return state
    if not isinstance(raw, dict) or raw.get("version") != 2:
        raise RuntimeStateSchemaError("runtime cache is not a V2 object")
    if not isinstance(raw.get("workers"), dict):
        raise RuntimeStateSchemaError("runtime cache workers must be an object")
    queue = raw.get("queue")
    if not isinstance(queue, dict) or not isinstance(queue.get("events"), dict):
        raise RuntimeStateSchemaError("runtime cache queue.events must be an object")
    for key, value in raw.items():
        if key not in state:
            continue
        expected = state[key]
        if isinstance(expected, dict) and not isinstance(value, dict):
            continue
        if isinstance(expected, list) and not isinstance(value, list):
            continue
        state[key] = deepcopy(value)
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
    delivery_health = state.get("delivery_health")
    state["delivery_health"] = delivery_health if isinstance(delivery_health, dict) else {
        "version": 1,
        "endpoints": {},
        "accounts": {},
    }
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
    raw_supervisor = state.get("supervisor")
    allowed_supervisor_keys = set(default_state()["supervisor"])
    state["supervisor"] = {
        key: value
        for key, value in (raw_supervisor.items() if isinstance(raw_supervisor, dict) else ())
        if key in allowed_supervisor_keys
    }
    state["supervisor"].setdefault("pid", None)
    state["supervisor"].setdefault("started_at", None)
    state["supervisor"].setdefault("last_heartbeat_at", None)
    state["supervisor"].setdefault("lifecycle", "idle")
    state["supervisor"].setdefault("last_successful_loop_at", None)
    state["supervisor"].setdefault("last_loop_started_at", None)
    state["supervisor"].setdefault("last_loop_finished_at", None)
    state["supervisor"].setdefault("last_loop_duration_ms", None)
    state["supervisor"].setdefault("last_loop_error", None)
    reservations = state["supervisor"].get("runtime_phase_reservations")
    state["supervisor"]["runtime_phase_reservations"] = (
        reservations if isinstance(reservations, dict) else {}
    )
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
    state = normalize_v2_runtime_cache(
        load_json(config_path(config, "state_file"), default=None)
    )
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
        normalize_v2_runtime_cache(state),
        source_id="runtime_state",
    )


@contextmanager
def runtime_state_update(config: dict[str, Any]):
    """Yield one cache snapshot and persist it under one exclusive lock.

    This is deliberately the only read-modify-write primitive for bootstrap:
    A supervisor restart must not read an active lease, release the lock, and
    later overwrite a worker's concurrent progress update with that old copy.
    """

    with runtime_state_lock(config, shared=False):
        state = _load_runtime_state_unlocked(config)
        yield state
        _save_runtime_state_unlocked(config, state)


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
