"""Event-log + board projection — Phase 6 (SUPERVISOR_REWRITE_PLAN.md §3.7,
anti-pattern K).

Live state (`ai-status.json`) is today both git-tracked and live-mutated, so a
worker's `git reset --hard`/`git clean` wipes the fleet's live state; and the
`next` field is overwritten by every event, keeping no history.

The plan's fix: the **source of truth is an append-only event log**, and the
board is a **projection** folded from it. `git clean` can never wipe a log that
lives outside the tree, and the `next`-overwrite problem disappears because every
update is a retained event, not an in-place mutation.

This module builds that model in isolation (no live storage cutover — that step
needs the fleet): a small event vocabulary, and a pure `project_board(events)`
that folds events into the current board. Task-status transitions are validated
against the ONE task state machine (`task_machine.TRANSITIONS`), so the projection
and the dispatcher share a single lifecycle definition.
"""
from __future__ import annotations

from typing import Any, Iterable

from task_machine import TRANSITIONS, TaskState, coerce_state

# Event vocabulary (append-only). Each event is a dict with at least
# {"type", "task_id"}; transition events also carry "action" (a TRANSITIONS verb).
EVENT_TASK_CREATED = "task_created"
EVENT_TASK_TRANSITION = "task_transition"
EVENT_OWNER_CHANGED = "owner_changed"
EVENT_REVIEWER_CHANGED = "reviewer_changed"
EVENT_NEXT_APPENDED = "next_appended"


class ProjectionError(ValueError):
    """An event could not be applied to the projection (illegal transition, etc.)."""


def _apply(board: dict[str, dict[str, Any]], event: dict[str, Any]) -> None:
    etype = str(event.get("type") or "")
    task_id = str(event.get("id") or event.get("task_id") or "")
    if not task_id:
        raise ProjectionError(f"event has no task id: {event!r}")

    if etype == EVENT_TASK_CREATED:
        if task_id in board:
            raise ProjectionError(f"task {task_id} created twice")
        board[task_id] = {
            "id": task_id,
            "status": str(event.get("status") or TaskState.TODO.value),
            "owner": event.get("owner"),
            "reviewer": event.get("reviewer"),
            "next_history": [],
            "bounce_count": 0,
        }
        return

    task = board.get(task_id)
    if task is None:
        raise ProjectionError(f"event for unknown task {task_id}: {etype}")

    if etype == EVENT_TASK_TRANSITION:
        action = str(event.get("action") or "")
        current = coerce_state(task.get("status"))
        if current is None:
            raise ProjectionError(f"task {task_id} has un-coercible status {task.get('status')!r}")
        nxt = TRANSITIONS.get((current, action))
        if nxt is None:
            raise ProjectionError(
                f"illegal transition for {task_id}: {current.value} --{action}-->"
            )
        task["status"] = nxt.value
        if action == "reject":
            task["bounce_count"] = int(task.get("bounce_count", 0)) + 1
    elif etype == EVENT_OWNER_CHANGED:
        task["owner"] = event.get("owner")
    elif etype == EVENT_REVIEWER_CHANGED:
        task["reviewer"] = event.get("reviewer")
    elif etype == EVENT_NEXT_APPENDED:
        # The anti-K fix: `next` is APPENDED (full history retained), not overwritten.
        task.setdefault("next_history", []).append(event.get("next"))
    else:
        raise ProjectionError(f"unknown event type {etype!r}")


def project_board(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fold an append-only event stream into the current board projection.

    Deterministic and replayable: projecting the same events always yields the
    same board, and truncating the stream yields the board as of that point —
    the property that makes the log the source of truth.
    """
    board: dict[str, dict[str, Any]] = {}
    for event in events:
        _apply(board, event)
    return board


def current_next(task: dict[str, Any]) -> Any:
    """The latest `next` for a task — the head of its retained history (vs the
    incumbent single overwritten field)."""
    history = task.get("next_history") or []
    return history[-1] if history else None
