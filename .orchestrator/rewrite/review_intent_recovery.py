"""Typed immutable facts for review-decision-intent lost-lease recovery.

A pending ``review_decision_intent`` durably fences all task mutation and
dispatch until the exact same actor replays the exact same
command/message/nonce (``AI_COLLABORATION_GUIDE.md`` section 3). If the
reviewer worker that reserved the intent loses its lease -- crash, restart,
lease reap -- before replaying it, nothing would otherwise ever retry it
again: the fence becomes a permanent Human/Ops-only deadlock.

This module defines a narrow, generation-bound receipt that the supervisor
mints once it has independently confirmed the reserving worker's lease is
gone. The receipt authorizes exactly one thing: redispatching the *same*
actor to replay the *same* nonce. It never changes owner, reviewer, or task
generation, and its own task-row field is excluded from
``review_decision_task_digest`` so minting it can never invalidate the frozen
reservation it exists to unblock. A receipt that no longer matches the live
intent's nonce/actor/command/message, or the task's current generation and
digest, is not current and grants no authority -- this is the stale-generation
rejection the recovery lane must enforce.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from common import utc_now

REVIEW_INTENT_RECOVERY_TASK_KEY = "review_decision_intent_recovery"
REVIEW_INTENT_RECOVERY_SCHEMA_VERSION = 1

# Keys excluded from the digest so neither the pending intent itself nor this
# module's own recovery receipt can perturb the CAS binding the intent froze
# at reservation time.
_DIGEST_EXCLUDED_KEYS = frozenset(
    {
        "review_decision_intent",
        REVIEW_INTENT_RECOVERY_TASK_KEY,
        "status_write_pending",
        "status_write_pending_count",
    }
)


def review_decision_task_digest(task: Mapping[str, Any]) -> str:
    """Digest business task truth, excluding the intent and recovery markers.

    Mirrors ``scripts.ai_status.review_decision_task_digest`` exactly so a
    receipt minted here is judged identically by the canonical CLI's own
    finalize-time CAS check. Duplicated intentionally instead of importing
    across the supervisor/ai_status boundary for one hash function.
    """

    candidate = {
        key: value for key, value in task.items() if key not in _DIGEST_EXCLUDED_KEYS
    }
    encoded = json.dumps(
        candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_review_decision_intent_recovery_receipt(
    *,
    task_id: str,
    task_generation: int,
    task_digest: str,
    intent: Mapping[str, Any],
    detected_at: str | None = None,
) -> dict[str, Any]:
    """Build one typed, replay-stable receipt binding a lost review lease.

    Bound exactly to the pending intent's nonce/actor/command/message and to
    the task's current generation/digest, so a later stale intent, a
    different actor, or a changed task can never satisfy this receipt's
    currency check.
    """

    nonce = str(intent.get("nonce") or "")
    basis = {"task_id": task_id, "task_generation": task_generation, "nonce": nonce}
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": REVIEW_INTENT_RECOVERY_SCHEMA_VERSION,
        "receipt_id": f"review-intent-lease-{digest}",
        "task_id": task_id,
        "task_generation": task_generation,
        "task_digest": task_digest,
        "nonce": nonce,
        "actor": str(intent.get("actor") or ""),
        "command": str(intent.get("command") or ""),
        "message": str(intent.get("message") or ""),
        "detected_at": detected_at or utc_now(),
    }


def review_decision_intent_recovery_is_current(
    receipt: Any,
    *,
    task_id: str,
    task_generation: int,
    task_digest: str,
    intent: Mapping[str, Any] | None,
) -> bool:
    """Whether a recovery receipt still authorizes replaying this exact intent."""

    if not isinstance(receipt, Mapping) or not isinstance(intent, Mapping):
        return False
    if receipt.get("schema_version") != REVIEW_INTENT_RECOVERY_SCHEMA_VERSION:
        return False
    return (
        str(receipt.get("task_id") or "") == task_id
        and receipt.get("task_generation") == task_generation
        and str(receipt.get("task_digest") or "") == task_digest
        and str(receipt.get("nonce") or "") == str(intent.get("nonce") or "")
        and str(receipt.get("actor") or "") == str(intent.get("actor") or "")
        and str(receipt.get("command") or "") == str(intent.get("command") or "")
        and str(receipt.get("message") or "") == str(intent.get("message") or "")
    )
