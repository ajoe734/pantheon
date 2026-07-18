"""Explicit task state machine — Phase 3 (SUPERVISOR_REWRITE_PLAN.md, anti-pattern C).

The incumbent has no task state machine: the lifecycle
``todo → in_progress → review → review_approved → done`` is re-derived by hand in
five separate places (dispatch_ready_tasks, higher_priority_ready_task_exists,
dispatch_priority_for_task, current_dispatch_event_key, worker_matches_current_
assignment), and owner/reviewer/status is mutated from three subsystems, so tasks
thrash between review and in_progress and never close.

This module makes the lifecycle a single explicit machine:

    TODO ──dispatch──▶ IN_PROGRESS ──submit──▶ REVIEW ──approve──▶ REVIEW_APPROVED
                          ▲                       │                      │
                          └────────reject─────────┘                 finalize
                                                                         ▼
                                                                       DONE
    (reject bumps bounce_count; N bounces ⇒ BLOCKED + escalation, ending thrash)

Its first job is to own **dispatch eligibility** — the "who may act on this task
next, and why" decision that the five ladders duplicate. `dispatch_reason()` is a
pure function that reproduces the incumbent `dispatch_priority_for_task()` exactly
(shadow-proven on the live board) so every consumer can query it instead of
re-deriving the ladder. It takes already-resolved booleans (is_owner, is_reviewer,
deps_satisfied) so the machine stays pure and independently testable; the caller
resolves those from the task/config once.
"""
from __future__ import annotations

import enum


class TaskState(enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    REVIEW_APPROVED = "review_approved"
    DONE = "done"
    BLOCKED = "blocked"


class DispatchReason(enum.Enum):
    """Why a task is dispatchable to an agent, and its priority (lower = more
    urgent), matching the incumbent ready-dispatcher priorities exactly."""
    REVIEW_READY = 0        # status REVIEW, agent is the reviewer
    OWNED_FINALIZE = 1      # status REVIEW_APPROVED, agent is the owner
    OWNED_IN_PROGRESS = 2   # status IN_PROGRESS, agent is owner, deps satisfied
    OWNED_READY = 3         # status TODO, agent is owner, deps satisfied


# Canonical lifecycle transitions. The ONLY sanctioned way status/owner/reviewer
# change once the machine owns them; helper-claim, chair-review, and mainline
# normalization become callers of `transition`, not independent mutators.
TRANSITIONS: dict[tuple[TaskState, str], TaskState] = {
    (TaskState.TODO, "dispatch"): TaskState.IN_PROGRESS,
    (TaskState.IN_PROGRESS, "submit"): TaskState.REVIEW,
    (TaskState.REVIEW, "approve"): TaskState.REVIEW_APPROVED,
    (TaskState.REVIEW, "reject"): TaskState.IN_PROGRESS,   # bumps bounce_count
    (TaskState.REVIEW_APPROVED, "finalize"): TaskState.DONE,
    (TaskState.IN_PROGRESS, "block"): TaskState.BLOCKED,
    (TaskState.BLOCKED, "unblock"): TaskState.TODO,
}


def coerce_state(status: object) -> TaskState | None:
    """Best-effort map a raw status string to a TaskState, else None."""
    try:
        return TaskState(str(status or "").lower())
    except ValueError:
        return None


def dispatch_reason(
    status: object,
    *,
    is_owner: bool,
    is_reviewer: bool,
    deps_satisfied: bool,
) -> DispatchReason | None:
    """Why (if at all) this task is dispatchable to the agent for whom the
    is_owner/is_reviewer/deps_satisfied booleans were resolved.

    Reproduces `dispatch_priority_for_task` exactly: review→reviewer,
    review_approved→owner, in_progress→owner+deps, todo→owner+deps.
    """
    st = str(status or "").lower()
    if st == TaskState.REVIEW.value and is_reviewer:
        return DispatchReason.REVIEW_READY
    if st == TaskState.REVIEW_APPROVED.value and is_owner:
        return DispatchReason.OWNED_FINALIZE
    if st == TaskState.IN_PROGRESS.value and is_owner and deps_satisfied:
        return DispatchReason.OWNED_IN_PROGRESS
    if st == TaskState.TODO.value and is_owner and deps_satisfied:
        return DispatchReason.OWNED_READY
    return None


def dispatch_priority(
    status: object,
    *,
    is_owner: bool,
    is_reviewer: bool,
    deps_satisfied: bool,
) -> int | None:
    """The incumbent's integer priority (0..3) or None — a thin adapter over
    dispatch_reason for shadow comparison against dispatch_priority_for_task."""
    reason = dispatch_reason(
        status, is_owner=is_owner, is_reviewer=is_reviewer, deps_satisfied=deps_satisfied
    )
    return reason.value if reason is not None else None
