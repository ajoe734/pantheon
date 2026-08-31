"""Dispatch reason/status vocabulary, execution-resource validation, and
(DTG-CLEAN-M6) the pure dispatch-candidate/admission-snapshot functions
shared by supervisor.py's live dispatch loop and scripts/explain_dispatch.py.

Kept separate from rewrite/dispatch_admission.py, which owns only the
hermetic execution-admission predicate and deliberately has no supervisor,
filesystem, subprocess, clock, or runtime-state dependencies: the M6
functions here read a live, already-loaded ``state`` snapshot and build
dispatch events, which is a different (still pure, still no I/O) tier.
A handful of symbols supervisor.py still owns are resolved lazily via
_supervisor_module() so importing this module -- from supervisor.py's own
top level, and independently from development_bridge/dev_bridge_models.py
for the execution-resource helpers below -- never triggers a circular
import back into supervisor.py itself. M6 did add real (non-lazy) module
weight to every consumer, including dev_bridge_models.py: common,
rewrite.dispatch_admission, rewrite.task_machine, and task_archive are
now required at import time, not just supervisor.py. Any isolated-copy
test fixture that copies this file must also copy those four.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from common import display_name_for, normalize_agent_id, utc_now
from rewrite import dispatch_admission as rewrite_dispatch_admission
from rewrite import task_machine as rewrite_task_machine
from task_archive import TaskResolver


def _supervisor_module():
    orchestrator_dir = Path(__file__).resolve().parent
    if str(orchestrator_dir) not in sys.path:
        sys.path.insert(0, str(orchestrator_dir))
    import supervisor

    return supervisor


def _admission_health_records(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module()._admission_health_records(*args, **kwargs)


def _parse_iso_utc(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().parse_runtime_timestamp(*args, **kwargs)


def account_concurrency_limit(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().account_concurrency_limit(*args, **kwargs)


def agent_account_id(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().agent_account_id(*args, **kwargs)


def build_dispatch_event(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().build_dispatch_event(*args, **kwargs)


def delivery_lane_for_agent(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().delivery_lane_for_agent(*args, **kwargs)


def dependencies_satisfied(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().dependencies_satisfied(*args, **kwargs)


def dispatch_loop_agent_ids(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().dispatch_loop_agent_ids(*args, **kwargs)


def ready_dispatch_max_concurrent_workers(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().ready_dispatch_max_concurrent_workers(*args, **kwargs)


def review_decision_intent_replay_eligible(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().review_decision_intent_replay_eligible(*args, **kwargs)


def runtime_delivery_health(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().runtime_delivery_health(*args, **kwargs)


def task_review_requeue_intent(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().task_review_requeue_intent(*args, **kwargs)


def task_review_requeue_record(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().task_review_requeue_record(*args, **kwargs)


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
    settings.setdefault("dependency_done_statuses", list(DEFAULT_DEPENDENCY_DONE_STATUSES))
    settings.setdefault("worker_terminal_statuses", list(DEFAULT_WORKER_TERMINAL_STATUSES))
    settings.setdefault("active_worker_statuses", list(DEFAULT_ACTIVE_WORKER_STATUSES))
    settings.setdefault("max_dispatches_per_tick", DEFAULT_MAX_DISPATCHES_PER_TICK)
    settings.setdefault("max_concurrent_workers", DEFAULT_MAX_CONCURRENT_WORKERS)
    settings.setdefault("worker_os_duplicate_guard", DEFAULT_WORKER_OS_DUPLICATE_GUARD)
    if "max_concurrent_per_account" not in settings:
        settings["max_concurrent_per_account"] = dict(DEFAULT_MAX_CONCURRENT_PER_ACCOUNT)
    settings.setdefault("max_active_workers_per_task", DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK)
    settings.setdefault("terminal_queue_history_limit", 200)
    settings["execution_resource_limits"] = validate_execution_resource_limits(
        settings.get("execution_resource_limits")
    )
    return settings


def build_delivery_admission_snapshot(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    active_task_ids: set[str],
    pending_task_ids: set[str],
    agent_loads: Mapping[str, list[int]],
    active_account_loads: Mapping[str, int],
    pending_account_loads: Mapping[str, int],
    active_resource_loads: Mapping[str, int] | None = None,
    pending_resource_loads: Mapping[str, int] | None = None,
    resource_limits: Mapping[str, int] | None = None,
    live_total: int | None = None,
    provisional_reserved_endpoint_ids: set[str] | None = None,
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
    # Planning uses an in-memory event sink and deliberately does not write
    # queue rows. Preserve physical-slot choices already made in this plan so
    # the next candidate cannot select the same exclusive endpoint again.
    for endpoint_id in provisional_reserved_endpoint_ids or set():
        endpoint = normalize_agent_id(str(endpoint_id or ""))
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
    active_res = active_resource_loads if isinstance(active_resource_loads, Mapping) else {}
    pending_res = pending_resource_loads if isinstance(pending_resource_loads, Mapping) else {}
    resource_reserved = {
        key: int(active_res.get(key, 0)) + int(pending_res.get(key, 0))
        for key in set(active_res) | set(pending_res)
    }
    raw_limits = resource_limits if resource_limits is not None else settings.get("execution_resource_limits")
    limits = validate_execution_resource_limits(raw_limits)
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
        resource_reserved=resource_reserved,
        resource_limits=limits,
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
    active_resource_loads: Mapping[str, int] | None = None,
    pending_resource_loads: Mapping[str, int] | None = None,
    resource_limits: Mapping[str, int] | None = None,
    live_total: int | None = None,
    requested_endpoint_id: str | None = None,
    provisional_reserved_endpoint_ids: set[str] | None = None,
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
        human_ops_hold=bool(
            str(task.get("waiting_for") or "").strip()
            or (
                task.get("review_decision_intent") not in (None, {}, [])
                and not review_decision_intent_replay_eligible(
                    config, task, target_agent
                )
            )
        ),
        review_binding_current=rewrite_task_machine.delivery_binding_is_current(task),
        execution_resources=tuple(task_execution_resources(task)),
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
            active_resource_loads=active_resource_loads,
            pending_resource_loads=pending_resource_loads,
            resource_limits=resource_limits,
            live_total=live_total,
            provisional_reserved_endpoint_ids=provisional_reserved_endpoint_ids,
        ),
        requested_endpoint_id=requested_endpoint_id,
    )


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


def task_review_requeue_is_materialized(
    task: Mapping[str, Any] | None,
) -> bool:
    record = task_review_requeue_record(task)
    return bool(record is not None and record.get("status") == "materialized")


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
    active_resource_loads: dict[str, int] | None = None,
    pending_resource_loads: dict[str, int] | None = None,
    resource_limits: dict[str, int] | None = None,
    seen_event_keys: dict[str, Any],
    checked_at: str,
    cooldown_seconds: float,
    live_total: int | None = None,
    activity_events: list[dict[str, Any]] | None = None,
    provisional_reserved_endpoint_ids: set[str] | None = None,
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

    if task_review_requeue_is_materialized(task):
        # The canonical acknowledgement is the durable planner offset. The
        # already-reserved queue row is late-revalidated by the lower-level
        # admission predicate, but planning must never append another row.
        return reject(
            "review_requeue_already_materialized",
            "The canonical review-requeue intent was already materialized",
        )

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
        active_resource_loads=active_resource_loads,
        pending_resource_loads=pending_resource_loads,
        resource_limits=resource_limits,
        live_total=live_total,
        provisional_reserved_endpoint_ids=provisional_reserved_endpoint_ids,
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
    event = build_dispatch_event(
        task,
        target_agent,
        reason,
        task_resolver,
        activity_events=activity_events,
        config=config,
    )
    event["delivery_endpoint_id"] = admission.endpoint_id
    event["provider"] = admission.provider_id
    if event["key"] in pending_event_keys:
        return reject("duplicate_event", "The exact delivery intent already exists")

    if (
        task_review_requeue_intent(task) is not None
        and event["key"] in seen_event_keys
    ):
        # Reopen is a canonical transactional-outbox row, not an ordinary
        # unchanged-task poll.  Queue append and this consumer offset share
        # one runtime transaction, so a crash/restart must never materialize
        # the same reopen intent twice even when the general cooldown is zero.
        return reject(
            "review_requeue_already_materialized",
            "The canonical review-requeue intent was already materialized",
        )

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
