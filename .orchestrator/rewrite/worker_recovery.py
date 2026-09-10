"""Typed immutable facts for Supervisor Authority V2 worker recovery."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from common import display_name_for, utc_now
from dispatch_policy import REASON_REVIEW_READY
from rewrite.task_identity import task_generation
from task_archive import is_terminal_task


WORKER_RECOVERY_TASK_KEY = "worker_recovery"
WORKER_RECOVERY_RECEIPTS_KEY = "worker_recovery_receipts"
LOST_LEASE_RECEIPT_SCHEMA_VERSION = 1
MAX_WORKER_RECOVERY_RECEIPTS = 128


def _continuation_source(value: Any) -> dict[str, Any]:
    """Copy historical source identity, never delivery or approval authority."""

    if not isinstance(value, Mapping):
        return {}
    head = str(value.get("head_sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        return {}
    source: dict[str, Any] = {"head_sha": head}
    pr = str(value.get("pr") or "").strip().lstrip("#")
    if pr.isdigit() and int(pr) > 0:
        source["pr"] = int(pr)
    for key in ("head_branch", "base", "repository_id", "repository_slug"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            source[key] = item.strip()
    if "repository_slug" not in source and isinstance(value.get("repository"), str):
        source["repository_slug"] = value["repository"].strip()
    return source


def _continuation_rejection(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value[key].strip()
        for key in ("reason", "actor", "recorded_at")
        if isinstance(value.get(key), str) and value[key].strip()
    }


def worker_recovery_workspace_facts(value: Any) -> dict[str, str]:
    """Validate the allowlisted provenance of one quarantined worktree."""

    keys = (
        "repository_id", "workspace_path", "branch", "source_head",
        "archive_path", "preserved_branch_ref",
    )
    if not isinstance(value, Mapping) or any(
        not isinstance(value.get(key), str) or not value[key].strip() for key in keys
    ):
        return {}
    facts = {key: value[key].strip() for key in keys}
    if (
        not re.fullmatch(r"[0-9a-f]{40,64}", facts["source_head"])
        or not Path(facts["workspace_path"]).is_absolute()
        or not Path(facts["archive_path"]).is_absolute()
        or not facts["preserved_branch_ref"].startswith("refs/pantheon/recovery/")
    ):
        return {}
    return facts


def capture_worker_recovery_continuation(
    status: Mapping[str, Any], task: Mapping[str, Any]
) -> dict[str, Any]:
    """Freeze bounded diagnostic facts before recovery changes assignment state.

    The canonical receipt already owns lost-lease history. Carry its compact
    continuation forward so receipt pruning cannot sever a task's source and
    unresolved rejection context. Never copy a task/worker snapshot, a review
    binding, execution grant, or another recovery authority into this payload.
    """

    prior = _canonical_worker_recovery_receipt(status, task) or {}
    previous = prior.get("previous")
    inherited = previous.get("continuation") if isinstance(previous, Mapping) else {}
    inherited = inherited if isinstance(inherited, Mapping) else {}
    continuation: dict[str, Any] = {}
    next_step = task.get("next")
    if isinstance(next_step, str) and next_step.strip():
        continuation["next"] = next_step.strip()
    source = _continuation_source(inherited.get("source"))
    for key in ("delivery_binding", "github_review_bridge"):
        candidate = _continuation_source(task.get(key))
        if candidate:
            source = candidate
            break
    if not source:
        source = _continuation_source(task.get("source_ref"))
    if source:
        continuation["source"] = source
    workspace = worker_recovery_workspace_facts(prior.get("workspace")) or (
        worker_recovery_workspace_facts(inherited.get("workspace"))
    )
    if workspace:
        continuation["workspace"] = workspace

    # A new review handoff closes the earlier implementation rejection epoch.
    # Within the owner lane a newer operator note may change `next` while the
    # earlier reviewer requirements still need to accompany the continuation.
    if str(task.get("status") or "") not in {"review", "review_approved", "done", "superseded"}:
        rejection = _continuation_rejection(inherited.get("rejection"))
        requeue = task.get("review_requeue_intent")
        if (
            isinstance(requeue, Mapping)
            and requeue.get("task_id") == task.get("id")
            and requeue.get("task_generation") == task_generation(task)
        ):
            # Every new reopen supersedes the prior rejection epoch. A
            # Human/Ops or owner reopen contributes its current `next`, not
            # another reviewer's previously resolved requirements.
            rejection = (
                _continuation_rejection(
                    {
                        "reason": requeue.get("reason"),
                        "actor": requeue.get("reopened_by"),
                        "recorded_at": requeue.get("reopened_at"),
                    }
                )
                if requeue.get("reopened_by") == task.get("reviewer")
                else {}
            )
        elif not rejection:
            bridge = task.get("github_review_bridge")
            if isinstance(bridge, Mapping) and bridge.get("decision") in {
                "reopen", "changes_requested", "reject"
            }:
                actor = str(bridge.get("actor") or "")
                handoffs = [
                    item for item in (status.get("handoffs") or [])
                    if isinstance(item, Mapping)
                    and item.get("task_id") == task.get("id")
                    and actor and item.get("from") == actor
                    and isinstance(item.get("message"), str)
                ]
                if handoffs:
                    handoff = max(handoffs, key=lambda item: str(item.get("created_at") or ""))
                    rejection = _continuation_rejection(
                        {
                            "reason": handoff.get("message"),
                            "actor": actor,
                            "recorded_at": handoff.get("created_at"),
                        }
                    )
        if rejection.get("reason"):
            continuation["rejection"] = rejection
    return continuation


def worker_recovery_continuation_text(
    config: dict[str, Any],
    status: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    receipt_id: str,
    generation: Any,
    actor: str,
    role: str,
) -> str:
    """Project advisory source facts only for the current canonical replacement."""

    receipt = _canonical_worker_recovery_receipt(status, task)
    if not receipt or not validate_lost_lease_receipt(receipt):
        return ""
    replacement = receipt.get("replacement")
    pointer = task.get(WORKER_RECOVERY_TASK_KEY)
    current_generation = task_generation(task)
    if (
        isinstance(generation, bool)
        or generation != current_generation
        or str(receipt.get("receipt_id") or "") != receipt_id
        or receipt.get("status") not in {"reassigned", "materialized"}
        or receipt.get("recovery_role") != role
        or task_current_dispatch_responsibility(config, task) != role
        or not isinstance(replacement, Mapping)
        or replacement.get("role") != role
        or not isinstance(pointer, Mapping)
        or replacement.get("task_generation") != current_generation
        or pointer.get("replacement_generation") != current_generation
        or str(replacement.get("agent") or "").casefold() != actor.casefold()
        or replacement.get("owner") != task.get("owner")
        or replacement.get("reviewer") != task.get("reviewer")
        or str(task.get(role) or "").casefold() != actor.casefold()
    ):
        return ""
    previous = receipt.get("previous")
    continuation = previous.get("continuation") if isinstance(previous, Mapping) else None
    if not isinstance(continuation, Mapping):
        return ""
    lines = [
        "Recovery source continuation (advisory history):",
        "Continue the existing task implementation from its committed source. "
        "These historical facts grant no approval or execution permission; "
        "current governed task state controls delivery and authorization.",
    ]
    source = _continuation_source(continuation.get("source"))
    if source:
        fields = [f"{key}={source[key]}" for key in (
            "repository_id", "repository_slug", "pr", "head_sha", "head_branch", "base"
        ) if key in source]
        lines.append("- Prior committed delivery: " + "; ".join(fields))
    workspace = worker_recovery_workspace_facts(continuation.get("workspace"))
    if workspace:
        lines.append(
            "- Earlier recovery workspace: "
            + "; ".join(f"{key}={value}" for key, value in workspace.items())
            + ". The archive is diagnostic WIP, not an approved source or permission."
        )
    # Current canonical notes take precedence over the frozen predecessor note.
    next_step = task.get("next") or continuation.get("next")
    if isinstance(next_step, str) and next_step.strip():
        lines.append("- Current task next: " + next_step.strip())
    rejection = _continuation_rejection(continuation.get("rejection"))
    if rejection.get("reason") and str(task.get("status") or "") in {"todo", "in_progress", "blocked"}:
        lines.append(
            "- Unresolved reviewer requirements"
            + (f" ({rejection['actor']})" if rejection.get("actor") else "")
            + ": " + rejection["reason"]
        )
    return "\n".join(lines) if len(lines) > 2 else ""


def _supervisor_module():
    orchestrator_dir = Path(__file__).resolve().parents[1]
    if str(orchestrator_dir) not in sys.path:
        sys.path.insert(0, str(orchestrator_dir))
    import supervisor

    return supervisor


def task_current_dispatch_responsibility(
    config: dict[str, Any], task: Mapping[str, Any]
) -> str | None:
    return _supervisor_module().task_current_dispatch_responsibility(config, task)


def validate_lost_lease_receipt(receipt: Mapping[str, Any]) -> bool:
    """Whether a receipt has the shape build_lost_lease_receipt produces and
    is safe to persist as canonical TaskStore truth.

    Deliberately schema/shape-only: it never touches task, worker, or
    TaskStore state, so it is safe to call from any recovery transition
    (construction, adoption, or persistence) as a pure guard.
    """

    if not isinstance(receipt, Mapping):
        return False
    if receipt.get("schema_version") != LOST_LEASE_RECEIPT_SCHEMA_VERSION:
        return False
    if receipt.get("type") not in {"worker_lost_lease", "worker_promotion_drained"}:
        return False
    if receipt.get("type") == "worker_promotion_drained":
        import runtime_state
        drain = receipt.get("promotion_drain")
        if (receipt.get("reason_kind") != "promotion_drained" or not isinstance(drain, Mapping)
                or drain.get("digest") != runtime_state.promotion_receipt_digest(drain)
                or drain.get("status") != "drained"
                or drain.get("worker", {}).get("run_id") != receipt.get("worker_run_id")
                or drain.get("worker", {}).get("task_generation") != receipt.get("task_generation")):
            return False
    if str(receipt.get("status") or "") not in {
        "pending",
        "held",
        "resolved",
        "reassigned",
        "materialized",
    }:
        return False
    if not str(receipt.get("receipt_id") or "").strip():
        return False
    if not str(receipt.get("dedupe_key") or "").strip():
        return False
    if not str(receipt.get("task_id") or "").strip():
        return False
    if str(receipt.get("recovery_role") or "") not in {"owner", "reviewer"}:
        return False
    if not isinstance(receipt.get("worker"), Mapping):
        return False
    if not isinstance(receipt.get("lease"), Mapping):
        return False
    return True


def build_lost_lease_receipt(
    config: dict[str, Any],
    worker: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    reason_kind: str,
    reason: str,
    detected_at: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """Build one typed, replay-stable receipt for a lost worker lease."""

    task_id = str(task.get("id") or worker.get("task_id") or "").strip()
    run_id = str(worker.get("run_id") or "").strip()
    queue_event_id = str(worker.get("queue_event_id") or "").strip()
    process_generation = str(worker.get("process_generation") or "").strip()
    lease_acquired_at = str(worker.get("lease_acquired_at") or "").strip()
    lease_expires_at = str(worker.get("lease_expires_at") or "").strip()
    generation = task_generation(task)
    basis = {
        "task_id": task_id,
        "task_generation": generation,
        "worker_run_id": run_id,
        "queue_event_id": queue_event_id,
        "process_generation": process_generation,
        "lease_acquired_at": lease_acquired_at,
        "lease_expires_at": lease_expires_at,
        "reason_kind": reason_kind,
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    dispatch_reason = str((worker.get("request_snapshot") or {}).get("reason") or "")
    recovery_role = "reviewer" if dispatch_reason == REASON_REVIEW_READY else "owner"
    agent_id = str(worker.get("agent_id") or worker.get("provider") or "").strip()
    actor = display_name_for(config, agent_id)
    return {
        "schema_version": LOST_LEASE_RECEIPT_SCHEMA_VERSION,
        "type": "worker_lost_lease",
        "receipt_id": f"lost-lease-{digest}",
        "dedupe_key": f"worker-lost-lease:{digest}",
        "status": status,
        "task_id": task_id,
        "task_generation": generation,
        "worker_run_id": run_id,
        "queue_event_id": queue_event_id,
        "recovery_role": recovery_role,
        "worker": {
            "agent": actor,
            "agent_id": agent_id,
            "logical_agent_id": str(worker.get("logical_agent_id") or agent_id),
            "provider": str(worker.get("provider") or agent_id),
        },
        "lease": {
            "lease_id": ":".join(
                item for item in (queue_event_id, run_id, process_generation) if item
            ),
            "owner": str(worker.get("lease_owner") or run_id),
            "acquired_at": lease_acquired_at or None,
            "expires_at": lease_expires_at or None,
            "last_heartbeat_at": worker.get("last_heartbeat_at"),
            "pid": worker.get("pid"),
            "pid_start_ticks": worker.get("pid_start_ticks"),
            "process_generation": process_generation or None,
        },
        "reason_kind": reason_kind,
        "reason": reason,
        "detected_at": detected_at or utc_now(),
        "previous": {
            "owner": str(task.get("owner") or ""),
            "reviewer": str(task.get("reviewer") or ""),
            "status": str(task.get("status") or ""),
            "task_generation": generation,
            "agent": actor,
            "worker_run_id": run_id,
            "queue_event_id": queue_event_id,
        },
        "replacement": None,
        "attempt_count": 0,
        "last_attempt_at": None,
    }


def _worker_recovery_activity_event(
    receipt: Mapping[str, Any],
    *,
    event_type: str,
    timestamp: str,
    message: str,
    event_identity: str | None = None,
) -> dict[str, Any]:
    receipt_id = str(receipt.get("receipt_id") or "").strip()
    if receipt.get("type") == "worker_promotion_drained":
        event_type = event_type.replace("worker_lost_lease", "worker_promotion_continuation")
    identity_suffix = f"-{event_identity}" if event_identity else ""
    return {
        "event_id": f"supervisor-{event_type}-{receipt_id}{identity_suffix}",
        "ts": timestamp,
        "agent": "Orchestrator",
        "type": event_type,
        "task_id": receipt.get("task_id"),
        "target_agent": (receipt.get("replacement") or {}).get("agent"),
        "provider": (receipt.get("worker") or {}).get("provider"),
        "worker_run_id": receipt.get("worker_run_id"),
        "queue_event_id": receipt.get("queue_event_id"),
        "recovery_receipt_id": receipt_id,
        "worker_recovery_receipt": deepcopy(dict(receipt)),
        "message": message,
    }


def _worker_recovery_pointer(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Return the small task-local pointer to the canonical receipt history."""

    replacement = receipt.get("replacement")
    replacement = replacement if isinstance(replacement, Mapping) else {}
    return {
        "receipt_id": str(receipt.get("receipt_id") or ""),
        "status": str(receipt.get("status") or ""),
        "task_generation": int(receipt.get("task_generation") or 0),
        "fence_generation": int(receipt.get("fence_generation") or 0),
        "replacement_generation": replacement.get("task_generation"),
    }


def _canonical_worker_recovery_receipt(
    status: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    pointer = task.get(WORKER_RECOVERY_TASK_KEY)
    receipts = status.get(WORKER_RECOVERY_RECEIPTS_KEY)
    if not isinstance(pointer, Mapping) or not isinstance(receipts, Mapping):
        return None
    receipt_id = str(pointer.get("receipt_id") or "").strip()
    receipt = receipts.get(receipt_id)
    if not receipt_id or not isinstance(receipt, Mapping):
        return None
    if (
        str(receipt.get("receipt_id") or "") != receipt_id
        or str(receipt.get("task_id") or "") != str(task.get("id") or "")
        or str(pointer.get("status") or "") != str(receipt.get("status") or "")
    ):
        return None
    return deepcopy(dict(receipt))


def _prune_worker_recovery_receipts(
    status: dict[str, Any],
    *,
    current_receipt_id: str,
) -> None:
    receipts = status.get(WORKER_RECOVERY_RECEIPTS_KEY)
    if not isinstance(receipts, dict) or len(receipts) <= MAX_WORKER_RECOVERY_RECEIPTS:
        return
    protected = {current_receipt_id} if current_receipt_id in receipts else set()
    for task in status.get("tasks", []) or []:
        if not isinstance(task, dict) or is_terminal_task(task):
            continue
        # Materialization ends assignment fencing, not source continuity.
        # Keep each live task's current canonical context even while held or
        # after a lane handoff; never retain its entire previous-receipt chain.
        receipt = _canonical_worker_recovery_receipt(status, task)
        if receipt is not None:
            protected.add(receipt["receipt_id"])
    prunable = sorted(
        (receipt_id for receipt_id in receipts if receipt_id not in protected),
        key=lambda receipt_id: (
            str(
                (receipts.get(receipt_id) or {}).get("detected_at") or ""
                if isinstance(receipts.get(receipt_id), Mapping)
                else ""
            ),
            receipt_id,
        ),
    )
    while len(receipts) > MAX_WORKER_RECOVERY_RECEIPTS and prunable:
        removed = prunable.pop(0)
        receipts.pop(removed, None)
        for task in status.get("tasks", []) or []:
            pointer = task.get(WORKER_RECOVERY_TASK_KEY)
            if isinstance(pointer, Mapping) and str(
                pointer.get("receipt_id") or ""
            ) == removed:
                task.pop(WORKER_RECOVERY_TASK_KEY, None)


def worker_recovery_responsibility_is_obsolete(
    config: dict[str, Any],
    task: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> bool:
    """Whether a pending recovery no longer owns the task's current lane."""

    recovery_role = str(receipt.get("recovery_role") or "").strip()
    if recovery_role not in {"owner", "reviewer"}:
        return False
    return task_current_dispatch_responsibility(config, task) != recovery_role


def _task_worker_recovery_fence_matches(
    task: Mapping[str, Any] | None, *, statuses: frozenset[str]
) -> bool:
    """One pointer policy for dispatch and Human/Ops assignment admission.

    Only an active lifecycle fences assignment. Its relevant authority epoch
    must be a positive canonical integer; malformed active authority fails
    closed, while unrelated historical epochs cannot reactivate a resolved
    receipt. Full receipt validation remains the transition owner's job.
    """

    if not isinstance(task, Mapping):
        return False
    pointer = task.get(WORKER_RECOVERY_TASK_KEY)
    if not isinstance(pointer, Mapping) or not str(
        pointer.get("receipt_id") or ""
    ).strip():
        return False
    status = str(pointer.get("status") or "").strip()
    if status not in statuses:
        return False
    authority_key = "fence_generation" if status == "pending" else "replacement_generation"
    authority_generation = pointer.get(authority_key)
    generation = task.get("generation", 1)
    if any(type(value) is not int or value < 1 for value in (authority_generation, generation)):
        return True
    return authority_generation == generation


def task_has_pending_worker_recovery(task: Mapping[str, Any] | None) -> bool:
    return _task_worker_recovery_fence_matches(task, statuses=frozenset({"pending"}))


def task_has_active_worker_recovery(task: Mapping[str, Any] | None) -> bool:
    """Whether typed recovery still uniquely owns assignment mutation."""

    return _task_worker_recovery_fence_matches(
        task, statuses=frozenset({"pending", "reassigned"})
    )


def count_lost_worker_recovery_outcome(
    counts: dict[str, int],
    state: Mapping[str, Any],
    worker: Mapping[str, Any],
    *,
    reason_kind: str,
) -> None:
    """Count one typed recovery by detector and canonical receipt outcome."""

    prefix = (
        "expired_lease"
        if reason_kind == "worker_lease_expired"
        else "missing_process"
    )
    counts[f"{prefix}_workers_reconciled"] += 1
    receipt_id = str(worker.get("lost_lease_receipt_id") or "")
    runtime_receipt = (
        (state.get(WORKER_RECOVERY_RECEIPTS_KEY) or {}).get(receipt_id)
        if receipt_id
        else None
    )
    recovery_status = (
        str(runtime_receipt.get("status") or "")
        if isinstance(runtime_receipt, Mapping)
        else ""
    )
    if recovery_status in {"reassigned", "materialized"}:
        counts[f"{prefix}_workers_reassigned"] += 1
    elif recovery_status == "pending":
        counts[f"{prefix}_recoveries_pending"] += 1
    elif recovery_status == "held":
        counts[f"{prefix}_tasks_held"] += 1
    else:
        counts[f"{prefix}_workers_superseded"] += 1
