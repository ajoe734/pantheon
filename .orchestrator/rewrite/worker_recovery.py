"""Typed immutable facts for Supervisor Authority V2 worker recovery."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from common import display_name_for, utc_now
from dispatch_policy import REASON_REVIEW_READY
from rewrite.task_identity import task_generation


WORKER_RECOVERY_TASK_KEY = "worker_recovery"
WORKER_RECOVERY_RECEIPTS_KEY = "worker_recovery_receipts"
LOST_LEASE_RECEIPT_SCHEMA_VERSION = 1
MAX_WORKER_RECOVERY_RECEIPTS = 128


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
    if receipt.get("type") != "worker_lost_lease":
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
        pointer = task.get(WORKER_RECOVERY_TASK_KEY)
        if not isinstance(pointer, Mapping):
            continue
        receipt_id = str(pointer.get("receipt_id") or "")
        receipt = receipts.get(receipt_id)
        pointer_status = str(pointer.get("status") or "")
        if (
            receipt_id
            and pointer_status in {"pending", "reassigned"}
            and isinstance(receipt, Mapping)
            and str(receipt.get("status") or "") == pointer_status
        ):
            protected.add(receipt_id)
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


def task_has_pending_worker_recovery(task: Mapping[str, Any] | None) -> bool:
    pointer = (task or {}).get(WORKER_RECOVERY_TASK_KEY)
    if not isinstance(pointer, Mapping):
        return False
    try:
        fence_generation = int(pointer.get("fence_generation") or 0)
    except (TypeError, ValueError):
        return True
    return bool(
        str(pointer.get("receipt_id") or "")
        and str(pointer.get("status") or "") == "pending"
        and fence_generation == task_generation(task)
    )


def task_has_active_worker_recovery(task: Mapping[str, Any] | None) -> bool:
    """Whether typed recovery still uniquely owns assignment mutation."""

    pointer = (task or {}).get(WORKER_RECOVERY_TASK_KEY)
    if not isinstance(pointer, Mapping) or not str(
        pointer.get("receipt_id") or ""
    ):
        return False
    try:
        fence_generation = int(pointer.get("fence_generation") or 0)
        replacement_generation = int(pointer.get("replacement_generation") or 0)
    except (TypeError, ValueError):
        return True
    status = str(pointer.get("status") or "")
    generation = task_generation(task)
    return bool(
        (status == "pending" and fence_generation == generation)
        or (status == "reassigned" and replacement_generation == generation)
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
