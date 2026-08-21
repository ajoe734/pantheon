"""Authoritative Pantheon task lifecycle.

Every canonical task-status writer must ask :func:`transition` for the target
state.  Callers may add command-specific authorization and evidence gates, but
they must not maintain another status ladder or assign a status directly.

Dispatch eligibility is deliberately kept as a pure query over the same state
vocabulary.  It does not mutate tasks and it is not a second lifecycle.
"""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


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


_HEX = frozenset("0123456789abcdef")


def _is_hex(value: object, length: int) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == length and set(text) <= _HEX


def delivery_contract_payload(task: Mapping[str, object]) -> dict[str, object]:
    """Return the immutable artifact contract frozen by a review handoff.

    Deliberately excludes ``generation``: it is an assignment epoch that
    advances on owner/reviewer reassignment, not a signal that the reviewed
    artifacts/acceptance changed, so it must not affect contract identity.
    """

    def normalized_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return sorted({str(item).strip() for item in value if str(item).strip()})

    return {
        "task_id": str(task.get("id") or "").strip(),
        "repository_id": str(
            task.get("repository_id") or task.get("target_repo") or ""
        ).strip(),
        "artifacts": normalized_list(task.get("artifacts")),
        "required_artifacts": normalized_list(task.get("required_artifacts")),
        "acceptance": normalized_list(task.get("acceptance")),
    }


def _canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def delivery_binding_is_current(task: Mapping[str, object]) -> bool:
    """Return whether a review task still has its handoff-bound delivery.

    GitHub proof remains an approval/closeout concern.  Dispatch only needs to
    reject a review row that has no immutable subject to inspect.
    """

    binding = task.get("delivery_binding")
    if not isinstance(binding, Mapping):
        return False
    kind = str(binding.get("kind") or "").strip()
    if kind == "pull_request":
        raw_pr = str(binding.get("pr") or "").strip().lstrip("#")
        return (
            raw_pr.isdigit()
            and int(raw_pr) > 0
            and _is_hex(binding.get("head_sha"), 40)
            and bool(str(binding.get("head_branch") or "").strip())
            and bool(str(binding.get("base") or "").strip())
        )
    if kind == "artifact_contract":
        contract = delivery_contract_payload(task)
        return (
            str(binding.get("task_id") or "") == contract["task_id"]
            and _is_hex(binding.get("contract_sha256"), 64)
            and str(binding.get("contract_sha256")) == _canonical_json_sha256(contract)
        )
    return False


def delivery_binding_digest(task: Mapping[str, object]) -> str | None:
    """Return the immutable delivery identity used by dispatch keys."""

    if not delivery_binding_is_current(task):
        return None
    binding = task.get("delivery_binding")
    assert isinstance(binding, Mapping)
    return _canonical_json_sha256(dict(binding))


@dataclass(frozen=True)
class AssignmentTransition:
    old_owner: str
    old_reviewer: str
    new_owner: str
    new_reviewer: str
    actor: str
    reason: str


@dataclass(frozen=True)
class ValidatedAssignmentActivityEvent:
    """Normalized reassignment audit event accepted by the canonical contract."""

    event_id: str
    timestamp: str
    actor: str
    task_id: str
    old_owner: str
    new_owner: str
    old_reviewer: str
    new_reviewer: str
    old_generation: int | None
    generation: int | None
    message: str
    legacy_ungenerated: bool

    def as_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "event_id": self.event_id,
            "ts": self.timestamp,
            "agent": self.actor,
            "type": "task_reassigned",
            "task_id": self.task_id,
            "old_owner": self.old_owner,
            "new_owner": self.new_owner,
            "old_reviewer": self.old_reviewer,
            "new_reviewer": self.new_reviewer,
            "message": self.message,
        }
        if self.old_generation is not None and self.generation is not None:
            event["old_generation"] = self.old_generation
            event["generation"] = self.generation
        return event


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


def _assignment_activity_event_digest(
    *,
    task_id: str,
    timestamp: str,
    old_owner: str,
    new_owner: str,
    old_reviewer: str,
    new_reviewer: str,
    old_generation: int | None,
    generation: int | None,
    message: str,
) -> str:
    generation_binding = ""
    if old_generation is not None and generation is not None:
        generation_binding = f"{old_generation}\0{generation}\0"
    payload = (
        f"{task_id}\0{timestamp}\0"
        f"{old_owner}\0{new_owner}\0"
        f"{old_reviewer}\0{new_reviewer}\0"
        f"{generation_binding}{message}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_assignment_activity_event(
    *,
    task_id: object,
    timestamp: object,
    assignment: AssignmentTransition,
    old_generation: object,
    new_generation: object,
) -> dict[str, object]:
    """Build the one canonical reassignment audit shape for both writers.

    Authorization differs between Human/Ops and the bounded Orchestrator
    reconciler, but neither should independently invent event fields or the
    generation-bound digest used by closeout/recovery readers.
    """

    normalized_task_id = str(task_id or "").strip()
    normalized_timestamp = str(timestamp or "").strip()
    normalized_message = assignment.reason
    if (
        assignment.actor not in {"Orchestrator", "Human/Ops"}
        or not assignment.new_owner
        or not assignment.new_reviewer
        or not normalized_message
    ):
        raise TransitionError("assignment event transition is invalid")
    if isinstance(old_generation, bool) or isinstance(new_generation, bool):
        raise TransitionError("assignment event generation is invalid")
    try:
        previous_generation = int(old_generation)
        current_generation = int(new_generation)
    except (TypeError, ValueError) as exc:
        raise TransitionError("assignment event generation is invalid") from exc
    if (
        not normalized_task_id
        or not normalized_timestamp
        or not normalized_message
        or previous_generation < 1
        or current_generation != previous_generation + 1
    ):
        raise TransitionError("assignment event is not generation-bound")
    digest = _assignment_activity_event_digest(
        task_id=normalized_task_id,
        timestamp=normalized_timestamp,
        old_owner=assignment.old_owner,
        new_owner=assignment.new_owner,
        old_reviewer=assignment.old_reviewer,
        new_reviewer=assignment.new_reviewer,
        old_generation=previous_generation,
        generation=current_generation,
        message=normalized_message,
    )
    prefix = (
        "supervisor-task-reassigned-"
        if assignment.actor == "Orchestrator"
        else "human-ops-task-reassigned-"
    )
    return {
        "event_id": prefix + digest,
        "ts": normalized_timestamp,
        "agent": assignment.actor,
        "type": "task_reassigned",
        "task_id": normalized_task_id,
        "old_owner": assignment.old_owner,
        "new_owner": assignment.new_owner,
        "old_reviewer": assignment.old_reviewer,
        "new_reviewer": assignment.new_reviewer,
        "old_generation": previous_generation,
        "generation": current_generation,
        "message": normalized_message,
    }


def validate_assignment_activity_event(
    event: Mapping[str, object],
) -> ValidatedAssignmentActivityEvent | None:
    """Return a normalized canonical reassignment event, or reject it.

    New Orchestrator and Human/Ops events are generation-bound and use their
    actor-specific canonical prefixes. Historical Orchestrator events without
    generation fields remain readable under both previously emitted
    supervisor prefixes; new writes never use that legacy form.
    """

    if str(event.get("type") or "") != "task_reassigned":
        return None
    actor = str(event.get("agent") or "")
    if actor not in {"Orchestrator", "Human/Ops"}:
        return None

    event_id = str(event.get("event_id") or "")
    timestamp = str(event.get("ts") or "")
    task_id = str(event.get("task_id") or "")
    old_owner = str(event.get("old_owner") or "")
    new_owner = str(event.get("new_owner") or "")
    old_reviewer = str(event.get("old_reviewer") or "")
    new_reviewer = str(event.get("new_reviewer") or "")
    message = str(event.get("message") or "")
    if not event_id or not timestamp or not task_id or not new_owner or not new_reviewer:
        return None
    if not message:
        return None

    raw_old_generation = event.get("old_generation")
    raw_generation = event.get("generation")
    legacy_ungenerated = raw_old_generation is None and raw_generation is None
    if legacy_ungenerated:
        if actor != "Orchestrator":
            return None
        previous_generation = None
        current_generation = None
    else:
        if (
            not isinstance(raw_old_generation, int)
            or isinstance(raw_old_generation, bool)
            or not isinstance(raw_generation, int)
            or isinstance(raw_generation, bool)
            or raw_old_generation < 1
            or raw_generation != raw_old_generation + 1
        ):
            return None
        previous_generation = raw_old_generation
        current_generation = raw_generation

    digest = _assignment_activity_event_digest(
        task_id=task_id,
        timestamp=timestamp,
        old_owner=old_owner,
        new_owner=new_owner,
        old_reviewer=old_reviewer,
        new_reviewer=new_reviewer,
        old_generation=previous_generation,
        generation=current_generation,
        message=message,
    )
    if actor == "Orchestrator":
        accepted_ids = {
            f"supervisor-task-reassigned-{digest}",
            f"supervisor-reassign-{digest}",
        }
    else:
        accepted_ids = {f"human-ops-task-reassigned-{digest}"}
    if event_id not in accepted_ids:
        return None

    return ValidatedAssignmentActivityEvent(
        event_id=event_id,
        timestamp=timestamp,
        actor=actor,
        task_id=task_id,
        old_owner=old_owner,
        new_owner=new_owner,
        old_reviewer=old_reviewer,
        new_reviewer=new_reviewer,
        old_generation=previous_generation,
        generation=current_generation,
        message=message,
        legacy_ungenerated=legacy_ungenerated,
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
