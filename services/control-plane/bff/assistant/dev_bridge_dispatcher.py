"""Dispatcher: verify signed task packet and materialise tasks via scripts/ai_status.py.

ASST-INTEG-006 — owned by Claude2.

Flow:
1. Verify packet signature (HMAC-SHA256 via dev_bridge_signer).
2. Reject duplicate packets via replay protection.
3. For each BridgeTask in the packet, call:
       python3 scripts/ai_status.py assign <task-id> <owner> <reviewer> [title]
   using subprocess with a structured TASK_METADATA_JSON envelope containing
   the exact task spec and packet/conversation/turn/document provenance.
4. Mark packet as seen so replays are rejected in subsequent calls.
5. Return BridgeDispatchResult with per-task records and audit refs.

The dispatcher never shells the VM for anything other than the ai_status.py
assign command.  Web API code must not call dispatcher functions directly —
they are invoked from a trusted internal service path or a repo-local script,
never from a raw HTTP request handler.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .dev_bridge_models import (
    BridgeConstraints,
    BridgeDispatchRequest,
    BridgeDispatchResult,
    BridgeTask,
    DevTaskPacket,
    TaskDispatchRecord,
)
from .dev_bridge_signer import (
    mark_packet_seen,
    packet_digest,
    packet_replay_lock,
    replay_record,
    verify_packet,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_repo_root(start: Optional[str] = None) -> str:
    """Walk up from *start* to find the Pantheon repo root."""
    if start:
        candidate = Path(start)
    else:
        env = os.environ.get("PANTHEON_STATUS_ROOT")
        candidate = Path(env) if env else Path(__file__).resolve()
    candidate = candidate if candidate.is_dir() else candidate.parent
    for _ in range(12):
        if (candidate / "ai-status.json").exists():
            return str(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return start or str(Path.cwd())


def _ai_status_py(repo_root: str) -> str:
    return str(Path(repo_root) / "scripts" / "ai_status.py")


def _configured_allowed_repos() -> set[str]:
    configured = str(
        os.environ.get("PANTHEON_ASSISTANT_DEV_BRIDGE_ALLOWED_REPOS") or "pantheon"
    )
    return {item.strip() for item in configured.split(",") if item.strip()}


# ---------------------------------------------------------------------------
# Constraint enforcement
# ---------------------------------------------------------------------------

def _check_constraints(packet: DevTaskPacket) -> List[str]:
    """Return a list of constraint violation messages (empty = OK)."""
    c: BridgeConstraints = packet.constraints
    violations: List[str] = []
    if not c.no_direct_shell_from_web:
        violations.append(
            "Packet constraint noDirectShellFromWeb is False — "
            "this dispatcher requires it to be True"
        )
    if not c.requires_branch_pr_merge:
        violations.append(
            "Packet constraint requiresBranchPrMerge is False — task branches and reviewed PR merge are required"
        )
    requested_repos = {
        str(item or "").strip()
        for item in c.allowed_repos
        if str(item or "").strip()
    }
    configured_repos = _configured_allowed_repos()
    if not requested_repos:
        violations.append("Packet constraint allowedRepos must not be empty")
    if "pantheon" not in requested_repos:
        violations.append(
            f"Packet constraint allowedRepos={c.allowed_repos!r} does not include 'pantheon'"
        )
    unconfigured = sorted(requested_repos - configured_repos)
    if unconfigured:
        violations.append(
            "Packet constraint allowedRepos contains unconfigured repositories: "
            + ", ".join(unconfigured)
        )
    return violations


def _task_spec(task: BridgeTask) -> Dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "owner": task.owner,
        "reviewer": task.reviewer,
        "phase": task.phase,
        "depends_on": list(task.depends_on),
        "artifacts": list(task.artifacts),
        "acceptance": list(task.acceptance),
        "summary": task.summary,
    }


def _task_spec_hash(task: BridgeTask) -> str:
    encoded = json.dumps(
        _task_spec(task),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_metadata(packet: DevTaskPacket, task: BridgeTask) -> Dict[str, object]:
    return {
        "dev_bridge": {
            "packet_id": packet.packet_id,
            "packet_digest": packet_digest(packet),
            "task_spec_hash": _task_spec_hash(task),
            "task_spec": _task_spec(task),
            "conversation_id": packet.source_conversation_id,
            "source_turn_ids": list(packet.source_turn_ids),
            "documents": [
                document.model_dump(mode="json", by_alias=True)
                for document in packet.documents
            ],
            "audit_conversation_href": packet.audit_conversation_href,
            "emitted_at": packet.emitted_at,
            "intent": packet.intent,
            "mode": packet.mode,
            "actor": packet.actor.model_dump(mode="json", by_alias=True),
        }
    }


def _audit_refs(packet: DevTaskPacket, dispatched_at: str) -> Dict[str, object]:
    return {
        "packetId": packet.packet_id,
        "packetDigest": packet_digest(packet),
        "conversationId": packet.source_conversation_id,
        "sourceTurnIds": packet.source_turn_ids,
        "documents": [d.path for d in packet.documents],
        "taskIds": [t.id for t in packet.tasks],
        "auditConversationHref": packet.audit_conversation_href,
        "dispatchedAt": dispatched_at,
    }


# ---------------------------------------------------------------------------
# Per-task dispatch
# ---------------------------------------------------------------------------

def _dispatch_task(
    task: BridgeTask,
    *,
    packet: DevTaskPacket,
    repo_root: str,
    actor_id: str,
    dry_run: bool,
) -> TaskDispatchRecord:
    """Call scripts/ai_status.py assign for a single task.

    Returns a TaskDispatchRecord indicating success or failure.
    Does NOT raise — errors are captured in the record.
    """
    record = TaskDispatchRecord(
        taskId=task.id,
        owner=task.owner,
        reviewer=task.reviewer,
        status="dry_run" if dry_run else "dispatched",
    )

    if dry_run:
        return record

    ai_status = _ai_status_py(repo_root)
    if not Path(ai_status).exists():
        record.status = "error"
        record.error = f"scripts/ai_status.py not found at {ai_status!r}"
        return record

    env = {**os.environ}
    env["AI_NAME"] = actor_id
    env["TASK_METADATA_JSON"] = json.dumps(
        _task_metadata(packet, task),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    cmd = [
        sys.executable,
        ai_status,
        "assign",
        task.id,
        task.owner,
        task.reviewer,
        task.title,
    ]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        if result.returncode != 0:
            record.status = "error"
            record.error = (result.stderr or result.stdout or "non-zero exit").strip()[:500]
    except subprocess.TimeoutExpired:
        record.status = "error"
        record.error = "ai_status.py assign timed out after 30s"
    except OSError as exc:
        record.status = "error"
        record.error = str(exc)

    return record


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def dispatch_task_packet(
    request: BridgeDispatchRequest,
    *,
    key_store: Optional[Dict[str, bytes]] = None,
) -> BridgeDispatchResult:
    """Verify, replay-check, and materialise all tasks in a signed DevTaskPacket.

    Returns BridgeDispatchResult.  Never raises for per-task failures — errors
    are captured in result.errors and per-task TaskDispatchRecord.error.

    Raises ValueError when:
    - Packet signature is invalid.
    - Packet constraints are violated.
    (Replay rejection is not raised; it returns a result with replay_rejected=True.)
    """
    packet = request.packet
    repo_root = request.repo_root or _find_repo_root()
    dry_run = request.dry_run
    dispatched_at = _now()

    # 1. Signature verification (raises on failure)
    verify_packet(packet, key_store=key_store)

    # 2. Constraint check (raises on violation)
    violations = _check_constraints(packet)
    if violations:
        raise ValueError("Packet constraint violation: " + "; ".join(violations))

    digest = packet_digest(packet)
    audit_refs = _audit_refs(packet, dispatched_at)

    # Replay check and successful terminal mark share one cross-process lock.
    # A failed/partial packet remains retryable; already successful tasks are
    # made no-ops by the bridge assignment metadata in scripts/ai_status.py.
    with packet_replay_lock(repo_root=repo_root):
        replay = replay_record(packet.packet_id, repo_root=repo_root, lock_held=True)
        if replay is not None:
            recorded_digest = str(replay.get("digest") or "").strip() or None
            if recorded_digest and recorded_digest != digest:
                raise ValueError(
                    f"Packet id {packet.packet_id!r} is already bound to a different payload"
                )
            return BridgeDispatchResult(
                packetId=packet.packet_id,
                dispatchedAt=dispatched_at,
                taskRecords=[
                    TaskDispatchRecord(
                        taskId=task.id,
                        owner=task.owner,
                        reviewer=task.reviewer,
                        status="already_dispatched",
                    )
                    for task in packet.tasks
                ],
                replayRejected=True,
                dryRun=dry_run,
                auditRefs=audit_refs,
            )

        task_records: List[TaskDispatchRecord] = []
        errors: List[str] = []
        actor_id = packet.actor.id

        for task in packet.tasks:
            rec = _dispatch_task(
                task,
                packet=packet,
                repo_root=repo_root,
                actor_id=actor_id,
                dry_run=dry_run,
            )
            task_records.append(rec)
            if rec.status == "error" and rec.error:
                errors.append(f"{task.id}: {rec.error}")

        if not dry_run and not errors:
            mark_packet_seen(
                packet.packet_id,
                repo_root=repo_root,
                digest=digest,
                lock_held=True,
            )

    return BridgeDispatchResult(
        packetId=packet.packet_id,
        dispatchedAt=dispatched_at,
        taskRecords=task_records,
        replayRejected=False,
        dryRun=dry_run,
        auditRefs=audit_refs,
        errors=errors,
    )
