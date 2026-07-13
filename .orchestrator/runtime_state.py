#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import fcntl
import json
import re
from collections.abc import Iterable
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import local
from typing import Any

from common import (
    append_jsonl,
    approval_tool_input_preview,
    approval_tool_input_signature,
    config_path,
    load_json,
    load_jsonl,
    normalize_agent_id,
    summarize_failure_reason,
    utc_now,
    write_json,
)


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
            "task_failure_streak_schema_version": 2,
            "task_failure_streaks": {},
            "task_failure_streak_aliases": {},
            "task_failure_streak_history": [],
            "task_failure_streak_quarantine": [],
        },
        "execution_dispatch_guardrails": {
            "schema_version": 1,
            "records": {},
            "history": [],
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


def _legacy_failure_signature(record: dict[str, Any]) -> str:
    kind = normalize_agent_id(str(record.get("last_failure_kind") or "unknown")) or "unknown"
    reason = re.sub(r"\s+", " ", str(record.get("last_reason") or "").strip().lower())
    return f"legacy:{kind}:{hashlib.sha256(reason.encode('utf-8')).hexdigest()}"


def _logical_failure_lane(config: dict[str, Any] | None, provider: str) -> str:
    provider_id = normalize_agent_id(provider)
    agents = (config or {}).get("agents", {}) or {}
    for raw_agent_id, agent in agents.items():
        agent_id = normalize_agent_id(str(raw_agent_id))
        agent_provider = normalize_agent_id(str((agent or {}).get("provider") or agent_id))
        if provider_id not in {agent_id, agent_provider}:
            continue
        return normalize_agent_id(str((agent or {}).get("dispatch_slot_for") or agent_id)) or provider_id
    return provider_id


def _failure_record_time(record: dict[str, Any], *, latest: bool) -> str:
    keys = (
        ("last_failure_at", "last_at", "updated_at", "first_failure_at", "first_at")
        if latest
        else ("first_failure_at", "first_at", "last_failure_at", "last_at", "updated_at")
    )
    for key in keys:
        parsed = _parse_failure_record_time(record.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return ""


def _parse_failure_record_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _merge_legacy_failure_streaks(
    provider_guardrails: dict[str, Any],
    config: dict[str, Any] | None,
) -> None:
    raw_records = provider_guardrails.get("task_failure_streaks")
    records = raw_records if isinstance(raw_records, dict) else {}
    raw_aliases = provider_guardrails.get("task_failure_streak_aliases")
    aliases = dict(raw_aliases) if isinstance(raw_aliases, dict) else {}
    raw_quarantine = provider_guardrails.get("task_failure_streak_quarantine")
    quarantine = list(raw_quarantine) if isinstance(raw_quarantine, list) else []
    migrated: dict[str, dict[str, Any]] = {}

    def quarantine_record(old_key: Any, raw_record: Any, disposition: str) -> None:
        candidate = raw_record if isinstance(raw_record, dict) else {"raw_record": repr(raw_record)}
        quarantine.append(
            {
                **deepcopy(candidate),
                "record_key": str(old_key),
                "quarantined_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "quarantine_disposition": disposition,
            }
        )

    for old_key, raw_record in records.items():
        if not isinstance(raw_record, dict):
            quarantine_record(old_key, raw_record, "malformed_record")
            continue
        record = deepcopy(raw_record)
        schema_version = record.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            quarantine_record(
                old_key,
                record,
                "legacy_unbound" if not record.get("signature") else "missing_or_invalid_schema_version",
            )
            continue
        if schema_version > 2:
            quarantine_record(old_key, record, "future_schema_version")
            continue
        if schema_version != 2:
            quarantine_record(old_key, record, "unsupported_schema_version")
            continue
        task_id = str(record.get("task_id") or str(old_key).split(":", 1)[0] or "").strip()
        physical = str(
            record.get("logical_agent_id")
            or record.get("provider")
            or (str(old_key).split(":", 1)[1] if ":" in str(old_key) else "")
        ).strip()
        logical = _logical_failure_lane(config, physical)
        if not task_id or not logical:
            quarantine_record(old_key, record, "missing_task_or_logical_lane")
            continue
        signature = str(record.get("signature") or "").strip()
        scope = str(record.get("signature_scope") or "").strip()
        dispatch_signature = str(record.get("dispatch_task_signature") or "").strip()
        dispatch_event_key = str(record.get("dispatch_event_key") or "").strip()
        if scope != "exact_dispatch" or not signature or not dispatch_signature or not dispatch_event_key:
            quarantine_record(old_key, record, "unbound_dispatch_signature")
            continue
        count = record.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            quarantine_record(old_key, record, "invalid_failure_count")
            continue
        first_dt = _parse_failure_record_time(record.get("first_failure_at"))
        last_dt = _parse_failure_record_time(record.get("last_failure_at"))
        if first_dt is None or last_dt is None:
            quarantine_record(old_key, record, "missing_or_invalid_failure_time")
            continue
        now = datetime.now(timezone.utc)
        if first_dt > now or last_dt > now:
            quarantine_record(old_key, record, "future_failure_time")
            continue
        if last_dt < first_dt:
            quarantine_record(old_key, record, "failure_time_order_invalid")
            continue
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
        key = f"{task_id}|{logical}|{digest}"
        previous = migrated.get(key)
        first_at = _failure_record_time(record, latest=False)
        last_at = _failure_record_time(record, latest=True)
        evidence = [str(value) for value in (record.get("evidence_refs") or []) if str(value)]
        for field in ("raw_ref", "last_raw_ref", "last_error_raw_ref"):
            if record.get(field):
                evidence.append(str(record[field]))
        alias_values = [str(old_key), *[str(value) for value in (record.get("aliases") or []) if str(value)]]
        if previous is None:
            merged = record
            merged.update(
                {
                    "schema_version": 2,
                    "task_id": task_id,
                    "logical_agent_id": logical,
                    "provider": logical,
                    "signature": signature,
                    "signature_scope": scope,
                    "count": count,
                    "first_failure_at": first_at or None,
                    "last_failure_at": last_at or None,
                    "evidence_refs": list(dict.fromkeys(evidence))[-20:],
                    "aliases": list(dict.fromkeys(alias_values)),
                }
            )
            migrated[key] = merged
        else:
            previous["count"] = _safe_nonnegative_int(previous.get("count")) + _safe_nonnegative_int(
                record.get("count")
            )
            previous_first = str(previous.get("first_failure_at") or "")
            previous_first_dt = _parse_failure_record_time(previous_first)
            first_at_dt = _parse_failure_record_time(first_at)
            if first_at_dt is not None and (previous_first_dt is None or first_at_dt < previous_first_dt):
                previous["first_failure_at"] = first_at
            previous_last = str(previous.get("last_failure_at") or "")
            previous_last_dt = _parse_failure_record_time(previous_last)
            last_at_dt = _parse_failure_record_time(last_at)
            if last_at_dt is not None and (previous_last_dt is None or last_at_dt >= previous_last_dt):
                for field, value in record.items():
                    if field.startswith("last_") or field in {"worker_run_id", "provider", "logical_agent_id"}:
                        previous[field] = deepcopy(value)
                previous["last_failure_at"] = last_at
                previous["logical_agent_id"] = logical
                previous["provider"] = logical
            previous["evidence_refs"] = list(
                dict.fromkeys([*(previous.get("evidence_refs") or []), *evidence])
            )[-20:]
            previous["aliases"] = list(dict.fromkeys([*(previous.get("aliases") or []), *alias_values]))
        for alias in alias_values:
            aliases[alias] = key

    live_keys = set(migrated)
    aliases = {
        str(alias): str(target)
        for alias, target in aliases.items()
        if str(target) in live_keys and str(alias) != str(target)
    }
    provider_guardrails["task_failure_streak_schema_version"] = 2
    provider_guardrails["task_failure_streaks"] = migrated
    provider_guardrails["task_failure_streak_aliases"] = {
        alias: target
        for alias, target in aliases.items()
        if target in migrated
    }
    provider_guardrails["task_failure_streak_quarantine"] = quarantine[-200:]


def migrate_state(raw: dict[str, Any] | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
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
    state["provider_guardrails"].setdefault("task_failure_streak_aliases", {})
    failure_history = state["provider_guardrails"].get("task_failure_streak_history")
    state["provider_guardrails"]["task_failure_streak_history"] = (
        failure_history if isinstance(failure_history, list) else []
    )
    failure_quarantine = state["provider_guardrails"].get("task_failure_streak_quarantine")
    state["provider_guardrails"]["task_failure_streak_quarantine"] = (
        failure_quarantine if isinstance(failure_quarantine, list) else []
    )
    _merge_legacy_failure_streaks(state["provider_guardrails"], config)
    if not isinstance(state.get("execution_dispatch_guardrails"), dict):
        state["execution_dispatch_guardrails"] = {}
    state["execution_dispatch_guardrails"].setdefault("schema_version", 1)
    records = state["execution_dispatch_guardrails"].get("records")
    state["execution_dispatch_guardrails"]["records"] = records if isinstance(records, dict) else {}
    history = state["execution_dispatch_guardrails"].get("history")
    state["execution_dispatch_guardrails"]["history"] = history if isinstance(history, list) else []
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

def _load_runtime_state_unlocked(config: dict[str, Any]) -> dict[str, Any]:
    state = migrate_state(load_json(config_path(config, "state_file"), default=default_state()), config)
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
    write_json(config_path(config, "state_file"), migrate_state(state, config))


_RUNTIME_LOCK_LOCAL = local()


def runtime_state_lock_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").with_suffix(".lock")


@contextmanager
def runtime_state_lock(config: dict[str, Any]):
    """Serialize a complete runtime-state read/mutate/save transaction.

    The lock is process-local re-entrant because the supervisor calls watcher
    helpers inside its transaction.  Separate processes still contend on the
    same advisory flock, so a watcher or self-claim cannot overwrite a newer
    supervisor snapshot.
    """

    try:
        path = runtime_state_lock_path(config)
    except KeyError:
        # Lightweight pure-unit configurations intentionally replace all
        # runtime I/O with mocks. Production/self-claim/watcher configs always
        # provide state_file and therefore always take the shared flock.
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    locks = getattr(_RUNTIME_LOCK_LOCAL, "locks", None)
    if locks is None:
        locks = {}
        _RUNTIME_LOCK_LOCAL.locks = locks
    key = str(path.resolve())
    held = locks.get(key)
    if held is not None:
        held[1] += 1
        try:
            yield
        finally:
            held[1] -= 1
        return

    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    locks[key] = [handle, 1]
    try:
        yield
    finally:
        locks.pop(key, None)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def load_runtime_state(config: dict[str, Any]) -> dict[str, Any]:
    with runtime_state_lock(config):
        return _load_runtime_state_unlocked(config)


def save_runtime_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    with runtime_state_lock(config):
        _save_runtime_state_unlocked(config, state)


def load_runtime_state_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Read a projection-only snapshot without joining a mutation transaction.

    Runtime writes are atomic, so dashboards can safely tolerate this slightly
    stale view. Code making an admission or mutation decision must use
    ``load_runtime_state`` or ``task_runtime_admission_guard`` instead.
    """

    return _load_runtime_state_unlocked(config)


_TASK_STATE_LOCK_LOCAL = local()


def canonical_task_state_lock_path(status_file: str | Path) -> Path:
    return Path(status_file).expanduser().resolve().parent / ".orchestrator" / "task-state.lock"


@contextmanager
def canonical_task_state_lock_file(status_file: str | Path):
    """Lock the stable sidecar inode shared by every ai-status writer."""

    path = canonical_task_state_lock_path(status_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    locks = getattr(_TASK_STATE_LOCK_LOCAL, "locks", None)
    if locks is None:
        locks = {}
        _TASK_STATE_LOCK_LOCAL.locks = locks
    key = str(path)
    held = locks.get(key)
    if held is not None:
        held[1] += 1
        try:
            yield
        finally:
            held[1] -= 1
        return

    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    locks[key] = [handle, 1]
    try:
        yield
    finally:
        locks.pop(key, None)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


RUNTIME_TASK_TERMINAL_WORKER_STATUSES = {
    "completed",
    "failed",
    "superseded",
    "reassigned",
    "retried",
}
RUNTIME_TASK_TERMINAL_QUEUE_STATUSES = {"completed", "failed", "done"}


def inspect_task_runtime_admission(
    state: dict[str, Any],
    queued_events: list[dict[str, Any]],
    task_id: str,
    approval_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed task mutation decision from one locked snapshot."""

    task_value = str(task_id or "").strip()
    if not task_value:
        return {
            "allowed": False,
            "task_id": task_value,
            "reason": "task_id_missing",
            "queue_event_ids": [],
            "worker_run_ids": [],
            "admitted_run_ids": [],
            "pending_approval_ids": [],
        }
    approvals = approval_state if approval_state is not None else default_approval_state()
    runtime_shape_valid = bool(
        isinstance(state, dict)
        and isinstance(queued_events, list)
        and isinstance(state.get("queue"), dict)
        and isinstance((state.get("queue") or {}).get("events"), dict)
        and isinstance(state.get("workers"), dict)
        and all(isinstance(event, dict) for event in queued_events)
        and isinstance(approvals, dict)
        and isinstance(approvals.get("pending"), list)
        and all(isinstance(item, dict) for item in approvals.get("pending", []))
    )
    if not runtime_shape_valid:
        return {
            "allowed": False,
            "task_id": task_value,
            "reason": "runtime_snapshot_malformed",
            "queue_event_ids": [],
            "worker_run_ids": [],
            "admitted_run_ids": [],
            "pending_approval_ids": [],
        }

    queue_records = ((state.get("queue") or {}).get("events") or {}) if isinstance(state.get("queue"), dict) else {}
    queue_event_ids: list[str] = []
    for event in queued_events:
        if not isinstance(event, dict) or str(event.get("task_id") or "").strip() != task_value:
            continue
        event_id = str(event.get("event_id") or "").strip()
        record = queue_records.get(event_id, {}) if isinstance(queue_records, dict) and event_id else {}
        queue_status = str((record or {}).get("status") or "queued").strip().lower()
        if queue_status not in RUNTIME_TASK_TERMINAL_QUEUE_STATUSES:
            queue_event_ids.append(event_id or "<missing-event-id>")

    worker_run_ids: list[str] = []
    admitted_run_ids: list[str] = []
    workers = state.get("workers") if isinstance(state.get("workers"), dict) else {}
    for run_key, worker in workers.items():
        if not isinstance(worker, dict) or str(worker.get("task_id") or "").strip() != task_value:
            continue
        status = str(worker.get("status") or "").strip().lower()
        run_id = str(worker.get("run_id") or run_key or "<missing-run-id>")
        is_active = status in ACTIVE_QUEUE_STATUSES
        is_admitted = (
            isinstance(worker.get("execution_admission"), dict)
            and status not in RUNTIME_TASK_TERMINAL_WORKER_STATUSES
        )
        if is_active:
            worker_run_ids.append(run_id)
        if is_admitted:
            admitted_run_ids.append(run_id)

    queue_event_ids = sorted(set(queue_event_ids))
    worker_run_ids = sorted(set(worker_run_ids))
    admitted_run_ids = sorted(set(admitted_run_ids))
    pending_approval_ids: list[str] = []
    for item in approvals.get("pending", []):
        if str(item.get("task_id") or "").strip() != task_value:
            continue
        approval_id = str(
            item.get("approval_id")
            or item.get("request_id")
            or item.get("tool_use_id")
            or item.get("worker_run_id")
            or "<missing-approval-id>"
        ).strip()
        pending_approval_ids.append(approval_id or "<missing-approval-id>")
    pending_approval_ids = sorted(set(pending_approval_ids))
    allowed = not queue_event_ids and not worker_run_ids and not admitted_run_ids and not pending_approval_ids
    return {
        "allowed": allowed,
        "task_id": task_value,
        "reason": "runtime_clear" if allowed else "task_queued_running_admitted_or_pending_approval",
        "queue_event_ids": queue_event_ids,
        "worker_run_ids": worker_run_ids,
        "admitted_run_ids": admitted_run_ids,
        "pending_approval_ids": pending_approval_ids,
    }


def strict_runtime_admission_snapshot(
    config: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Read and validate one strict state/event/approval snapshot."""

    try:
        state_path = config_path(config, "state_file")
        event_path = config_path(config, "event_queue")
        approval_path = config_path(config, "approval_queue")
    except KeyError as exc:
        return f"path_missing:{exc.args[0]}", None, None, None
    bodies: dict[str, str] = {}
    for label, path in (
        ("state", state_path),
        ("event_queue", event_path),
        ("approval_queue", approval_path),
    ):
        if not path.exists() or not path.is_file():
            return f"{label}_file_missing", None, None, None
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"{label}_file_unreadable:{type(exc).__name__}", None, None, None
        if not body.strip():
            return f"{label}_file_empty", None, None, None
        bodies[label] = body
    try:
        raw_state = json.loads(bodies["state"])
    except json.JSONDecodeError as exc:
        return f"state_file_malformed:{type(exc).__name__}", None, None, None
    if (
        not isinstance(raw_state, dict)
        or raw_state.get("version") != 2
        or not isinstance(raw_state.get("queue"), dict)
        or not isinstance((raw_state.get("queue") or {}).get("events"), dict)
        or not isinstance(raw_state.get("workers"), dict)
    ):
        return "state_schema_invalid", None, None, None
    try:
        raw_events = [
            json.loads(line)
            for line in bodies["event_queue"].splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        return f"event_queue_file_malformed:{type(exc).__name__}", None, None, None
    if not raw_events:
        return "event_queue_file_empty", None, None, None
    if any(
        not isinstance(event, dict)
        or not isinstance(event.get("event_id"), str)
        or not event.get("event_id", "").strip()
        for event in raw_events
    ):
        return "event_queue_schema_invalid", None, None, None
    try:
        raw_approvals = json.loads(bodies["approval_queue"])
    except json.JSONDecodeError as exc:
        return f"approval_queue_file_malformed:{type(exc).__name__}", None, None, None
    if (
        not isinstance(raw_approvals, dict)
        or raw_approvals.get("version") != 2
        or not isinstance(raw_approvals.get("pending"), list)
        or not isinstance(raw_approvals.get("history"), list)
        or any(not isinstance(item, dict) for item in raw_approvals.get("pending", []))
        or any(not isinstance(item, dict) for item in raw_approvals.get("history", []))
    ):
        return "approval_queue_schema_invalid", None, None, None
    return None, raw_state, raw_events, raw_approvals


def strict_runtime_admission_error(config: dict[str, Any]) -> str | None:
    """Validate live runtime inputs without accepting loader defaults."""

    error, _state, _events, _approvals = strict_runtime_admission_snapshot(config)
    return error


def _blocked_task_runtime_decision(task_id: str, reason: str) -> dict[str, Any]:
    return {
        "allowed": False,
        "task_id": str(task_id or "").strip(),
        "reason": reason,
        "queue_event_ids": [],
        "worker_run_ids": [],
        "admitted_run_ids": [],
        "pending_approval_ids": [],
    }


def _normalize_runtime_admission_task_ids(
    task_ids: Iterable[str] | str | None,
) -> tuple[list[str], list[str], list[str]]:
    if isinstance(task_ids, str):
        raw_ids: list[Any] = [task_ids]
    elif task_ids is None:
        raw_ids = []
    else:
        try:
            raw_ids = list(task_ids)
        except TypeError:
            raw_ids = []
    normalized: list[str] = []
    empty_entries: list[str] = []
    duplicate_ids: list[str] = []
    seen: set[str] = set()
    for index, raw_id in enumerate(raw_ids):
        task_id = str(raw_id or "").strip()
        if not task_id:
            empty_entries.append(f"index:{index}")
            continue
        if task_id in seen:
            duplicate_ids.append(task_id)
            continue
        seen.add(task_id)
        normalized.append(task_id)
    return normalized, empty_entries, sorted(set(duplicate_ids))


@contextmanager
def tasks_runtime_admission_guard(
    config: dict[str, Any],
    task_ids: Iterable[str] | str | None,
    *,
    strict: bool | None = None,
):
    """Admit multiple task mutations from one locked runtime snapshot.

    The runtime lock remains held for the entire context.  A dispatcher may
    therefore enter ``canonical_task_state_lock_file`` inside this guard while
    preserving the global runtime→task lock order.  IDs must be non-empty and
    unique; invalid input poisons the aggregate instead of silently narrowing
    the mutation set.
    """

    normalized_ids, empty_entries, duplicate_ids = _normalize_runtime_admission_task_ids(task_ids)
    # ``strict`` is tri-state so fresh/unit configurations retain normalized
    # loader defaults while a live config can opt in centrally.
    strict_mode = (
        bool((config.get("supervisor", {}) or {}).get("strict_task_runtime_admission", False))
        if strict is None
        else bool(strict)
    )
    with runtime_state_lock(config):
        invalid_reason = ""
        if not normalized_ids:
            invalid_reason = "task_ids_empty"
        elif empty_entries:
            invalid_reason = "task_ids_contain_empty"
        elif duplicate_ids:
            invalid_reason = "task_ids_duplicate"
        if invalid_reason:
            decisions = {
                task_id: _blocked_task_runtime_decision(task_id, invalid_reason)
                for task_id in normalized_ids
            }
            yield {
                "allowed": False,
                "reason": invalid_reason,
                "task_ids": normalized_ids,
                "decisions": decisions,
                "busy_task_ids": normalized_ids,
                "empty_task_id_entries": empty_entries,
                "duplicate_task_ids": duplicate_ids,
                "strict": strict_mode,
            }
            return

        if strict_mode:
            strict_error, strict_state, strict_events, strict_approvals = strict_runtime_admission_snapshot(config)
            if strict_error:
                reason = f"strict_runtime_invalid:{strict_error}"
                decisions = {
                    task_id: _blocked_task_runtime_decision(task_id, reason)
                    for task_id in normalized_ids
                }
                yield {
                    "allowed": False,
                    "reason": reason,
                    "task_ids": normalized_ids,
                    "decisions": decisions,
                    "busy_task_ids": normalized_ids,
                    "empty_task_id_entries": [],
                    "duplicate_task_ids": [],
                    "strict": True,
                }
                return
        try:
            if strict_mode:
                state = migrate_state(strict_state, config)
                queued_events = strict_events or []
                approval_state = strict_approvals or default_approval_state()
            else:
                state = _load_runtime_state_unlocked(config)
                queued_events = load_jsonl(config_path(config, "event_queue"))
                try:
                    approval_state = load_approval_state(config)
                except KeyError:
                    approval_state = default_approval_state()
            decisions = {
                task_id: inspect_task_runtime_admission(
                    state,
                    queued_events,
                    task_id,
                    approval_state,
                )
                for task_id in normalized_ids
            }
        except Exception as exc:
            reason = f"runtime_state_unavailable:{type(exc).__name__}"
            decisions = {
                task_id: _blocked_task_runtime_decision(task_id, reason)
                for task_id in normalized_ids
            }
        busy_task_ids = [
            task_id
            for task_id, decision in decisions.items()
            if not decision.get("allowed")
        ]
        allowed = not busy_task_ids
        yield {
            "allowed": allowed,
            "reason": "runtime_clear" if allowed else "one_or_more_tasks_busy",
            "task_ids": normalized_ids,
            "decisions": decisions,
            "busy_task_ids": busy_task_ids,
            "empty_task_id_entries": [],
            "duplicate_task_ids": [],
            "strict": strict_mode,
        }


@contextmanager
def task_runtime_admission_guard(
    config: dict[str, Any],
    task_id: str,
    *,
    strict: bool | None = None,
):
    """Backward-compatible single-task view of the shared multi-task guard."""

    task_value = str(task_id or "").strip()
    with tasks_runtime_admission_guard(config, [task_id], strict=strict) as aggregate:
        decision = (aggregate.get("decisions") or {}).get(task_value)
        if not isinstance(decision, dict):
            decision = _blocked_task_runtime_decision(
                task_value,
                "task_id_missing"
                if not task_value
                else str(aggregate.get("reason") or "runtime_state_unavailable"),
            )
        yield decision


def load_event_queue(config: dict[str, Any]) -> list[dict[str, Any]]:
    return load_jsonl(config_path(config, "event_queue"))


def enqueue_event(config: dict[str, Any], event: dict[str, Any]) -> None:
    with runtime_state_lock(config):
        append_jsonl(config_path(config, "event_queue"), event)


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
    payload = deepcopy(state)
    payload["pending"] = [_normalize_approval_item(item) for item in payload.get("pending", []) if isinstance(item, dict)]
    payload["history"] = [_normalize_approval_item(item) for item in payload.get("history", []) if isinstance(item, dict)]
    payload["version"] = 2
    payload["updated_at"] = utc_now()
    write_json(config_path(config, "approval_queue"), payload)
