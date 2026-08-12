"""Authoritative Pantheon task lifecycle.

Every canonical task-status writer must ask :func:`transition` for the target
state.  Callers may add command-specific authorization and evidence gates, but
they must not maintain another status ladder or assign a status directly.

Dispatch eligibility is deliberately kept as a pure query over the same state
vocabulary.  It does not mutate tasks and it is not a second lifecycle.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class TaskState(enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    REVIEW_APPROVED = "review_approved"
    DONE = "done"
    BLOCKED = "blocked"


class TaskAction(enum.Enum):
    START = "start"
    PROGRESS = "progress"
    HANDOFF = "handoff"
    APPROVE = "approve"
    REOPEN = "reopen"
    DONE = "done"
    BLOCK = "block"
    SUPERSEDE = "supersede"
    RECONCILE_DONE = "reconcile_done"


class DispatchReason(enum.Enum):
    REVIEW_READY = 0
    OWNED_FINALIZE = 1
    OWNED_IN_PROGRESS = 2
    OWNED_READY = 3


class TransitionError(ValueError):
    """The requested action is not legal from the task's current state."""


@dataclass(frozen=True)
class AssignmentTransition:
    old_owner: str
    old_reviewer: str
    new_owner: str
    new_reviewer: str
    actor: str
    reason: str


def assignment_transition(
    current_owner: object,
    current_reviewer: object,
    new_owner: object,
    new_reviewer: object,
    *,
    actor: object,
    reason: object,
    expected_owner: object | None = None,
    expected_reviewer: object | None = None,
) -> AssignmentTransition:
    """Validate the sole current-assignment transition.

    Catalog provenance is intentionally absent: it fixes the original packet,
    scope, and evidence, not the current runtime owner/reviewer.
    """

    old_owner = str(current_owner or "").strip()
    old_reviewer = str(current_reviewer or "").strip()
    target_owner = str(new_owner or "").strip()
    target_reviewer = str(new_reviewer or "").strip()
    transition_actor = str(actor or "").strip()
    transition_reason = str(reason or "").strip()
    if transition_actor not in {"Human/Ops", "Orchestrator"}:
        raise TransitionError("assignment actor is not authorized")
    if not target_owner or not target_reviewer:
        raise TransitionError("assignment owner and reviewer are required")
    if target_owner.casefold() == target_reviewer.casefold():
        raise TransitionError("assignment owner and reviewer must be independent")
    if not transition_reason:
        raise TransitionError("assignment reason is required")
    if expected_owner is not None and old_owner != str(expected_owner).strip():
        raise TransitionError("assignment owner compare-and-swap mismatch")
    if expected_reviewer is not None and old_reviewer != str(expected_reviewer).strip():
        raise TransitionError("assignment reviewer compare-and-swap mismatch")
    return AssignmentTransition(
        old_owner=old_owner,
        old_reviewer=old_reviewer,
        new_owner=target_owner,
        new_reviewer=target_reviewer,
        actor=transition_actor,
        reason=transition_reason,
    )


# The one lifecycle table used by canonical commands.  Self-transitions are
# present only for commands that update progress without changing lifecycle.
_COMMAND_TRANSITIONS: dict[tuple[TaskState, TaskAction], TaskState] = {
    (TaskState.TODO, TaskAction.START): TaskState.IN_PROGRESS,
    (TaskState.IN_PROGRESS, TaskAction.PROGRESS): TaskState.IN_PROGRESS,
    (TaskState.IN_PROGRESS, TaskAction.HANDOFF): TaskState.REVIEW,
    (TaskState.REVIEW, TaskAction.APPROVE): TaskState.REVIEW_APPROVED,
    (TaskState.REVIEW, TaskAction.REOPEN): TaskState.IN_PROGRESS,
    (TaskState.REVIEW_APPROVED, TaskAction.REOPEN): TaskState.IN_PROGRESS,
    (TaskState.BLOCKED, TaskAction.REOPEN): TaskState.IN_PROGRESS,
    (TaskState.REVIEW_APPROVED, TaskAction.DONE): TaskState.DONE,
}

for _state in (
    TaskState.TODO,
    TaskState.IN_PROGRESS,
    TaskState.REVIEW,
    TaskState.REVIEW_APPROVED,
    TaskState.BLOCKED,
):
    _COMMAND_TRANSITIONS[(_state, TaskAction.BLOCK)] = TaskState.BLOCKED

for _state in (
    TaskState.TODO,
    TaskState.IN_PROGRESS,
    TaskState.REVIEW,
    TaskState.REVIEW_APPROVED,
    TaskState.BLOCKED,
):
    _COMMAND_TRANSITIONS[(_state, TaskAction.SUPERSEDE)] = TaskState.DONE
    _COMMAND_TRANSITIONS[(_state, TaskAction.RECONCILE_DONE)] = TaskState.DONE


def coerce_state(status: object) -> TaskState | None:
    """Best-effort map a raw status string to a :class:`TaskState`."""

    try:
        return TaskState(str(status or "").strip().lower())
    except ValueError:
        return None


def coerce_action(action: object) -> TaskAction | None:
    """Best-effort map a raw action string to a :class:`TaskAction`."""

    try:
        return TaskAction(str(action or "").strip().lower())
    except ValueError:
        return None


def transition(status: object, action: object) -> TaskState:
    """Return the sole legal target for ``status --action-->``.

    Unknown states/actions and illegal pairs fail closed with one stable error
    type so CLI, event projection, and scheduler adapters do not grow their own
    transition tables.
    """

    current = coerce_state(status)
    requested = coerce_action(action)
    if current is None:
        raise TransitionError(f"unknown task state {status!r}")
    if requested is None:
        raise TransitionError(f"unknown task action {action!r}")
    target = _COMMAND_TRANSITIONS.get((current, requested))
    if target is None:
        raise TransitionError(
            f"illegal task transition: {current.value} --{requested.value}-->"
        )
    return target


# Append-only projection events used these verbs before command names became
# authoritative.  This is a read/replay compatibility view generated from the
# same lifecycle authority, not a fallback validator used by canonical writes.
TRANSITIONS: dict[tuple[TaskState, str], TaskState] = {
    (TaskState.TODO, "dispatch"): transition(TaskState.TODO.value, TaskAction.START.value),
    (TaskState.IN_PROGRESS, "submit"): transition(TaskState.IN_PROGRESS.value, TaskAction.HANDOFF.value),
    (TaskState.REVIEW, "approve"): transition(TaskState.REVIEW.value, TaskAction.APPROVE.value),
    (TaskState.REVIEW, "reject"): transition(TaskState.REVIEW.value, TaskAction.REOPEN.value),
    (TaskState.REVIEW_APPROVED, "finalize"): transition(TaskState.REVIEW_APPROVED.value, TaskAction.DONE.value),
    (TaskState.IN_PROGRESS, "block"): transition(TaskState.IN_PROGRESS.value, TaskAction.BLOCK.value),
    # Historical projections represented unblock as blocked -> todo.  Runtime
    # canonical reopen now resumes in_progress and never consults this adapter.
    (TaskState.BLOCKED, "unblock"): TaskState.TODO,
}


def dispatch_reason(
    status: object,
    *,
    is_owner: bool,
    is_reviewer: bool,
    deps_satisfied: bool,
) -> DispatchReason | None:
    """Return why this task is dispatchable to the supplied actor, if at all."""

    current = coerce_state(status)
    if current is TaskState.REVIEW and is_reviewer:
        return DispatchReason.REVIEW_READY
    if current is TaskState.REVIEW_APPROVED and is_owner:
        return DispatchReason.OWNED_FINALIZE
    if current is TaskState.IN_PROGRESS and is_owner and deps_satisfied:
        return DispatchReason.OWNED_IN_PROGRESS
    if current is TaskState.TODO and is_owner and deps_satisfied:
        return DispatchReason.OWNED_READY
    return None


def dispatch_priority(
    status: object,
    *,
    is_owner: bool,
    is_reviewer: bool,
    deps_satisfied: bool,
) -> int | None:
    reason = dispatch_reason(
        status,
        is_owner=is_owner,
        is_reviewer=is_reviewer,
        deps_satisfied=deps_satisfied,
    )
    return reason.value if reason is not None else None
