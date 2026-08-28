from __future__ import annotations

import re
from typing import Any, Mapping

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

# A successful launch may advance a ``todo`` task to ``in_progress`` exactly
# once. Resume and finalize launches are already represented by the durable
# worker/queue receipt and must not write a lifecycle no-op back to task truth.
# Such writes update ``last_update``, which is part of the dispatch signature;
# the supervisor would otherwise invalidate its own event key and turn a short
# worker exit into an immediate orphan/re-dispatch loop.
DISPATCH_STATUS_ACTIONS = {
    REASON_OWNED_READY: ("start", {"todo"}),
}

DEFAULT_REVIEW_STATUSES = ["review"]
DEFAULT_FINALIZE_STATUSES = ["review_approved"]
DEFAULT_OWNED_STATUSES = ["in_progress", "todo"]
DEFAULT_SIDECAR_ONLY_AGENTS: list[str] = []
DEFAULT_DEPENDENCY_DONE_STATUSES = ["done"]
DEFAULT_WORKER_TERMINAL_STATUSES = ["review", "done", "review_approved"]
DEFAULT_ACTIVE_WORKER_STATUSES = [
    "running",
    "waiting_approval",
    "retry_backoff",
    "stalled",
]
DEFAULT_MAX_DISPATCHES_PER_TICK = 4
DEFAULT_MAX_CONCURRENT_WORKERS: int | None = None
DEFAULT_WORKER_OS_DUPLICATE_GUARD = True
DEFAULT_MAX_CONCURRENT_PER_ACCOUNT: dict[str, int] = {}
DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK = 1
DEFAULT_EXECUTION_RESOURCE_LIMITS: dict[str, int] = {"pantheon-dev": 1}
ALLOWLISTED_EXECUTION_RESOURCES: frozenset[str] = frozenset({"pantheon-dev"})
KNOWN_EXECUTION_RESOURCES: frozenset[str] = ALLOWLISTED_EXECUTION_RESOURCES
_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_OPERATOR_ACCEPTANCE_PROOF_PREFIX = "refs/tags/pantheon-review/operator-accept/"


def is_operator_exact_head_acceptance(task: Mapping[str, Any] | None) -> bool:
    """Return whether a task is in the non-worker Human/Ops integration lane.

    The authoritative validation and audit write live in ``ai_status.py``.
    The scheduler must not import that command runtime just to decide whether
    it should launch a worker, so it repeats only the immutable-shape checks
    needed to suppress owner-finalization dispatch. A malformed or partial
    record deliberately returns ``False`` and therefore cannot suppress the
    ordinary lifecycle.
    """

    if not isinstance(task, Mapping):
        return False
    if str(task.get("status") or "").strip().lower() != "review_approved":
        return False
    binding = task.get("review_binding")
    acceptance = task.get("operator_acceptance")
    if not isinstance(binding, Mapping) or not isinstance(acceptance, Mapping):
        return False
    head_sha = str(binding.get("head_sha") or "").strip().lower()
    if not _OID_RE.fullmatch(head_sha):
        return False
    try:
        binding_pr = int(binding.get("pr") or 0)
        acceptance_pr = int(acceptance.get("pr") or 0)
    except (TypeError, ValueError):
        return False
    if binding_pr <= 0 or acceptance_pr != binding_pr:
        return False
    if (
        str(acceptance.get("mode") or "").strip() != "operator_exact_head"
        or str(acceptance.get("decision") or "").strip() != "operator-accept"
        or str(acceptance.get("actor") or "").strip() != "Human/Ops"
        or str(acceptance.get("head_sha") or "").strip().lower() != head_sha
    ):
        return False
    for field in ("head_branch", "base"):
        if str(acceptance.get(field) or "").strip() != str(binding.get(field) or "").strip():
            return False
    return (
        str(acceptance.get("operator_acceptance_proof_ref") or "").strip()
        == f"{_OPERATOR_ACCEPTANCE_PROOF_PREFIX}{head_sha}"
    )


def normalize_execution_resources(
    raw: Any,
    *,
    task_id: str | None = None,
) -> list[str]:
    """Strictly validate and normalize an execution_resources list.

    Only allowlisted resources ('pantheon-dev') are accepted.
    Rejects explicit null (None), non-list, non-string elements, empty strings,
    unallowlisted resource names, and duplicate resources.
    Returns a normalized list of lowercased, stripped strings.
    """
    prefix = f"Task {task_id} " if task_id else "task "
    if raw is None:
        raise ValueError(f"{prefix}execution_resources must be a list, got null")
    if not isinstance(raw, list):
        raise ValueError(
            f"{prefix}execution_resources must be a list, got {type(raw).__name__}: {raw!r}"
        )
    res: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(
                f"{prefix}execution_resources elements must be strings, got {type(item).__name__}: {item!r}"
            )
        val = item.strip().lower()
        if not val:
            raise ValueError(f"{prefix}execution_resources element cannot be empty")
        if val not in ALLOWLISTED_EXECUTION_RESOURCES:
            raise ValueError(
                f"{prefix}execution_resources contains an unallowlisted resource: {item!r}; "
                f"allowlisted execution resources: {', '.join(sorted(ALLOWLISTED_EXECUTION_RESOURCES))}"
            )
        if val in res:
            raise ValueError(f"{prefix}execution_resources contains duplicate resource: {item!r}")
        res.append(val)
    return res


def task_execution_resources(task: Mapping[str, Any] | None) -> list[str]:
    """Extract and strictly normalize execution_resources from a task mapping.

    Preserves omitted => [] (when task is None or 'execution_resources' not in task),
    while explicit null, empty strings, duplicates, or unallowlisted values fail closed.
    """
    if not task:
        return []
    if "execution_resources" not in task:
        return []
    task_id = str(task.get("id") or "").strip() or None
    return normalize_execution_resources(task["execution_resources"], task_id=task_id)


def validate_execution_resource_limits(
    raw_limits: Any,
) -> dict[str, int]:
    """Validate execution resource limits strictly.

    Only 'pantheon-dev' is known, and only integer 1 is valid.
    Rejects bool, string, zero/negative, >1, and unknown keys.
    Missing config (None) or empty dict defaults to {'pantheon-dev': 1}.
    """
    if raw_limits is None:
        return dict(DEFAULT_EXECUTION_RESOURCE_LIMITS)
    if not isinstance(raw_limits, Mapping):
        raise ValueError(
            f"execution_resource_limits must be a dict or null, got {type(raw_limits).__name__}: {raw_limits!r}"
        )
    if not raw_limits:
        return dict(DEFAULT_EXECUTION_RESOURCE_LIMITS)
    normalized: dict[str, int] = {}
    for key, val in raw_limits.items():
        if not isinstance(key, str):
            raise ValueError(
                f"execution_resource_limits key must be a string, got {type(key).__name__}: {key!r}"
            )
        k = key.strip().lower()
        if k not in KNOWN_EXECUTION_RESOURCES:
            raise ValueError(
                f"Unknown execution resource limit key: {key!r}; known resources: {', '.join(sorted(KNOWN_EXECUTION_RESOURCES))}"
            )
        if isinstance(val, bool):
            raise ValueError(
                f"Invalid execution resource limit for {key!r}: boolean {val!r} is not allowed"
            )
        if not isinstance(val, int):
            raise ValueError(
                f"Invalid execution resource limit for {key!r}: expected int, got {type(val).__name__} ({val!r})"
            )
        if val != 1:
            raise ValueError(
                f"Invalid execution resource limit for {key!r}: value must be 1, got {val}"
            )
        normalized[k] = val
    return normalized


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
    settings.setdefault("dependency_done_statuses", list(DEFAULT_DEPENDENCY_DONE_STATUSES))
    settings.setdefault("worker_terminal_statuses", list(DEFAULT_WORKER_TERMINAL_STATUSES))
    settings.setdefault("active_worker_statuses", list(DEFAULT_ACTIVE_WORKER_STATUSES))
    settings.setdefault("max_dispatches_per_tick", DEFAULT_MAX_DISPATCHES_PER_TICK)
    settings.setdefault("max_concurrent_workers", DEFAULT_MAX_CONCURRENT_WORKERS)
    settings.setdefault("worker_os_duplicate_guard", DEFAULT_WORKER_OS_DUPLICATE_GUARD)
    if "max_concurrent_per_account" not in settings:
        settings["max_concurrent_per_account"] = dict(DEFAULT_MAX_CONCURRENT_PER_ACCOUNT)
    settings.setdefault("max_active_workers_per_task", DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK)
    settings["execution_resource_limits"] = validate_execution_resource_limits(
        settings.get("execution_resource_limits")
    )
    return settings
