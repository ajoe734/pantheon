"""Typed immutable facts for Supervisor Authority V2 worker recovery."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from common import display_name_for, utc_now
from dispatch_policy import REASON_REVIEW_READY
from rewrite.task_identity import task_generation


WORKER_RECOVERY_TASK_KEY = "worker_recovery"
WORKER_RECOVERY_RECEIPTS_KEY = "worker_recovery_receipts"
LOST_LEASE_RECEIPT_SCHEMA_VERSION = 1


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
