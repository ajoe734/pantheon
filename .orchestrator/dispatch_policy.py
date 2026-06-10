from __future__ import annotations

from typing import Any

REASON_REVIEW_READY = "review_ready_dispatch"
REASON_OWNED_FINALIZE = "owned_finalize_dispatch"
REASON_OWNED_IN_PROGRESS = "owned_in_progress_dispatch"
REASON_OWNED_READY = "owned_ready_dispatch"

EXECUTION_DISPATCH_REASONS = {
    REASON_REVIEW_READY,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
}

DISPATCH_REASON_PRIORITIES = {
    REASON_REVIEW_READY: 0,
    REASON_OWNED_FINALIZE: 1,
    REASON_OWNED_IN_PROGRESS: 2,
    REASON_OWNED_READY: 3,
}

DISPATCH_STATUS_ACTIONS = {
    REASON_OWNED_READY: ("start", {"todo"}),
    REASON_OWNED_FINALIZE: ("note", {"review_approved"}),
    REASON_OWNED_IN_PROGRESS: ("progress", {"in_progress"}),
}

DEFAULT_REVIEW_STATUSES = ["review"]
DEFAULT_FINALIZE_STATUSES = ["review_approved"]
DEFAULT_OWNED_STATUSES = ["in_progress", "todo"]
DEFAULT_SIDECAR_ONLY_AGENTS: list[str] = []
DEFAULT_DISABLED_AGENTS: list[str] = []
DEFAULT_DEPENDENCY_DONE_STATUSES = ["done"]
DEFAULT_WORKER_TERMINAL_STATUSES = ["review", "done", "review_approved"]
DEFAULT_ACTIVE_WORKER_STATUSES = [
    "running",
    "waiting_approval",
    "retry_backoff",
    "manual_pending",
    "stalled",
]
DEFAULT_MAX_TASKS_PER_AGENT: int | None = None
DEFAULT_MAX_DISPATCHES_PER_TICK = 4
DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS = 300
DEFAULT_MAX_CONCURRENT_WORKERS: int | None = None
DEFAULT_WORKER_OS_DUPLICATE_GUARD = True
DEFAULT_MAX_CONCURRENT_PER_QUOTA_GROUP: dict[str, int] = {}
DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK = 1


def dispatch_reason_priority(reason: str | None) -> int | None:
    return DISPATCH_REASON_PRIORITIES.get(str(reason or ""))


def is_execution_dispatch_reason(reason: str | None) -> bool:
    return str(reason or "") in EXECUTION_DISPATCH_REASONS


def normalized_status_set(values: Any, default: list[str]) -> set[str]:
    if values is None:
        values = default
    if isinstance(values, str):
        values = [values]
    return {str(value).lower() for value in list(values or [])}


def ready_dispatch_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("ready_dispatcher", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("review_statuses", list(DEFAULT_REVIEW_STATUSES))
    settings.setdefault("finalize_statuses", list(DEFAULT_FINALIZE_STATUSES))
    settings.setdefault("owned_statuses", list(DEFAULT_OWNED_STATUSES))
    settings.setdefault("sidecar_only_agents", list(DEFAULT_SIDECAR_ONLY_AGENTS))
    settings.setdefault("disabled_agents", list(DEFAULT_DISABLED_AGENTS))
    legacy_done_statuses = settings.get("done_statuses", list(DEFAULT_WORKER_TERMINAL_STATUSES))
    settings.setdefault("dependency_done_statuses", list(DEFAULT_DEPENDENCY_DONE_STATUSES))
    settings.setdefault("worker_terminal_statuses", legacy_done_statuses)
    settings.setdefault("active_worker_statuses", list(DEFAULT_ACTIVE_WORKER_STATUSES))
    settings.setdefault("max_tasks_per_agent", DEFAULT_MAX_TASKS_PER_AGENT)
    settings.setdefault("max_tasks_per_agent_by_agent", {})
    settings.setdefault("max_dispatches_per_tick", DEFAULT_MAX_DISPATCHES_PER_TICK)
    settings.setdefault("orphaned_queue_event_grace_seconds", DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS)
    settings.setdefault("max_concurrent_workers", DEFAULT_MAX_CONCURRENT_WORKERS)
    settings.setdefault("worker_os_duplicate_guard", DEFAULT_WORKER_OS_DUPLICATE_GUARD)
    settings.setdefault("max_concurrent_per_quota_group", dict(DEFAULT_MAX_CONCURRENT_PER_QUOTA_GROUP))
    settings.setdefault("max_active_workers_per_task", DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK)
    return settings
