#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from common import (
    activity_audit_lock_file,
    approval_tool_input_preview,
    approval_tool_input_signature,
    canonical_task_state_lock_file,
    config_path,
    durable_write_bytes,
    load_json,
    stable_sidecar_lock,
    utc_now,
    write_json,
)


RUNTIME_ADMISSION_SOURCE_IDS = (
    "runtime_state",
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
class RuntimeStateSchemaError(ValueError):
    """Raised when an ordinary V2 restart cannot safely read its cache."""


def default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "initialized_at": None,
        "last_scan_at": None,
        "recent_terminal_tasks": [],
        "pending_handoff_keys": [],
        "seen_event_keys": {},
        "queue": {
            "version": 2,
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
            "task_state_projection": {},
            "last_cycle_metrics": {},
            "cycle_elapsed_seconds": None,
            "cycle_elapsed_peak_seconds": 0.0,
            "runtime_lock_hold_seconds": None,
            "runtime_lock_hold_peak_seconds": 0.0,
            "runtime_lock_hold_exceeded": False,
            "cadence_next_deadline_monotonic": None,
            "cadence_overshoot_seconds": None,
            "cadence_overshoot_peak_seconds": 0.0,
            "cadence_skipped_deadlines": 0,
            "scheduler_cycle_elapsed_seconds": None,
            "scheduler_cycle_elapsed_peak_seconds": 0.0,
            "runtime_phase_reservations": {},
        },
    }


def normalize_v2_runtime_cache(
    raw: Any,
    *,
    allow_legacy_queue_records: bool = False,
) -> dict[str, Any]:
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
    recent_terminal_tasks = state.get("recent_terminal_tasks")
    state["recent_terminal_tasks"] = recent_terminal_tasks if isinstance(recent_terminal_tasks, list) else []
    state.setdefault("pending_handoff_keys", [])
    state.setdefault("seen_event_keys", {})
    state.setdefault("queue", {})
    state["queue"].setdefault("version", 2)
    queue_events = state["queue"].get("events")
    if not isinstance(queue_events, dict):
        raise RuntimeStateSchemaError("runtime cache queue.events must be an object")
    normalized_queue_events: dict[str, dict[str, Any]] = {}
    for raw_event_id, raw_record in queue_events.items():
        event_id = str(raw_event_id or "").strip()
        if not event_id or not isinstance(raw_record, dict):
            continue
        record = deepcopy(raw_record)
        intent = record.get("intent")
        if intent is None:
            if not allow_legacy_queue_records:
                raise RuntimeStateSchemaError(
                    "runtime cache contains legacy queue records without embedded intents; "
                    "restore a valid V2 queue snapshot before starting the supervisor"
                )
        elif not isinstance(intent, dict):
            raise RuntimeStateSchemaError(
                f"runtime cache queue event {event_id} has a non-object intent"
            )
        else:
            intent = deepcopy(intent)
            intent["event_id"] = event_id
            record["intent"] = intent
        normalized_queue_events[event_id] = record
    state["queue"]["version"] = 2
    state["queue"]["events"] = normalized_queue_events
    state.setdefault("workers", {})
    for run_id, worker in state["workers"].items():
        if not isinstance(worker, dict):
            raise RuntimeStateSchemaError(f"runtime cache worker {run_id} is not an object")
        status = str(worker.get("status") or "").strip().lower()
        if status not in {
            "queued",
            "started",
            "running",
            "retry_backoff",
            "stalled",
            "admitted",
        }:
            continue
        event_id = str(worker.get("queue_event_id") or "").strip()
        if not event_id or event_id not in normalized_queue_events:
            raise RuntimeStateSchemaError(
                "runtime cache active worker has no matching state-owned queue intent: "
                f"worker={run_id} queue_event_id={event_id or '<missing>'}"
            )
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
    last_cycle_metrics = state["supervisor"].get("last_cycle_metrics")
    if isinstance(last_cycle_metrics, dict):
        # Queue dwell was retired as a process-health signal.  Drop a stale
        # sample on the next ordinary V2 cache write rather than carrying it
        # forward indefinitely after the source change.
        last_cycle_metrics.pop("queue_to_start", None)
    reservations = state["supervisor"].get("runtime_phase_reservations")
    state["supervisor"]["runtime_phase_reservations"] = (
        reservations if isinstance(reservations, dict) else {}
    )
    state["version"] = 2
    return state


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


def queue_event_record(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    queue = state.setdefault("queue", {})
    events = queue.setdefault("events", {})
    record = events.get(event_id)
    if not isinstance(record, dict) or not isinstance(record.get("intent"), dict):
        raise RuntimeStateSchemaError(
            f"runtime queue record is missing its immutable intent: {event_id}"
        )
    return record


def queue_events(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return immutable delivery-intent views from one runtime snapshot."""

    queue = state.get("queue") if isinstance(state, Mapping) else None
    records = queue.get("events") if isinstance(queue, Mapping) else None
    if not isinstance(records, Mapping):
        return []
    result: list[dict[str, Any]] = []
    for raw_event_id, raw_record in records.items():
        event_id = str(raw_event_id or "").strip()
        if not event_id or not isinstance(raw_record, Mapping):
            continue
        intent = raw_record.get("intent")
        if not isinstance(intent, Mapping):
            continue
        event = deepcopy(dict(intent))
        event["event_id"] = event_id
        result.append(event)
    return result


def queue_event_by_id(state: Mapping[str, Any], event_id: str | None) -> dict[str, Any] | None:
    normalized = str(event_id or "").strip()
    if not normalized:
        return None
    queue = state.get("queue") if isinstance(state, Mapping) else None
    records = queue.get("events") if isinstance(queue, Mapping) else None
    record = records.get(normalized) if isinstance(records, Mapping) else None
    intent = record.get("intent") if isinstance(record, Mapping) else None
    if not isinstance(intent, Mapping):
        return None
    event = deepcopy(dict(intent))
    event["event_id"] = normalized
    return event


def store_queue_event(state: dict[str, Any], event: Mapping[str, Any]) -> bool:
    """Store one immutable delivery intent with its mutable lease record.

    This function deliberately performs no file I/O.  Its caller owns the
    surrounding ``runtime_state_update``/CAS transaction, so an intent cannot
    survive if its matching worker/lease mutation loses that transaction.
    """

    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("queue intent requires event_id")
    events = state.setdefault("queue", {}).setdefault("events", {})
    existing = events.get(event_id)
    if isinstance(existing, dict):
        existing_intent = existing.get("intent")
        if isinstance(existing_intent, dict):
            return False
    payload = deepcopy(dict(event))
    payload["event_id"] = event_id
    record = {
        "intent": payload,
        "attempt_count": 0,
        "status": "queued",
    }
    event_key = str(payload.get("event_key") or "")
    if event_key:
        record["event_key"] = event_key
    events[event_id] = record
    return True


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
