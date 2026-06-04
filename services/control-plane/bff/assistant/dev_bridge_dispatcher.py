"""Dispatcher: verify signed task packet and materialise tasks via scripts/ai_status.py.

ASST-INTEG-006 — owned by Claude2.

Flow:
1. Verify packet signature (HMAC-SHA256 via dev_bridge_signer).
2. Reject duplicate packets via replay protection.
3. For each BridgeTask in the packet, call:
       python3 scripts/ai_status.py assign <task-id> <owner> <reviewer> [title]
   using subprocess with the task's env vars for phase, artifacts, acceptance,
   and depends_on.
4. Mark packet as seen so replays are rejected in subsequent calls.
5. Return BridgeDispatchResult with per-task records and audit refs.

The dispatcher never shells the VM for anything other than the ai_status.py
assign command.  Web API code must not call dispatcher functions directly —
they are invoked from a trusted internal service path or a repo-local script,
never from a raw HTTP request handler.
"""
from __future__ import annotations

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
from .dev_bridge_signer import has_seen_packet, mark_packet_seen, verify_packet


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


def _csv(items: List[str]) -> str:
    return ";".join(i.strip() for i in items if i.strip())


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
    if "pantheon" not in c.allowed_repos:
        violations.append(
            f"Packet constraint allowedRepos={c.allowed_repos!r} does not include 'pantheon'"
        )
    return violations


# ---------------------------------------------------------------------------
# Per-task dispatch
# ---------------------------------------------------------------------------

def _dispatch_task(
    task: BridgeTask,
    *,
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
    if task.phase:
        env["TASK_PHASE"] = task.phase
    if task.artifacts:
        env["TASK_ARTIFACTS"] = _csv(task.artifacts)
    if task.acceptance:
        env["TASK_ACCEPTANCE"] = _csv(task.acceptance)
    if task.depends_on:
        env["TASK_DEPENDS_ON"] = _csv(task.depends_on)
    if task.summary:
        env["TASK_SUMMARY_ZH"] = task.summary[:200]

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

    # 3. Replay protection
    if has_seen_packet(packet.packet_id, repo_root=repo_root):
        return BridgeDispatchResult(
            packetId=packet.packet_id,
            dispatchedAt=dispatched_at,
            replayRejected=True,
            dryRun=dry_run,
        )

    # 4. Materialise each task
    task_records: List[TaskDispatchRecord] = []
    errors: List[str] = []
    actor_id = packet.actor.id

    for task in packet.tasks:
        rec = _dispatch_task(task, repo_root=repo_root, actor_id=actor_id, dry_run=dry_run)
        task_records.append(rec)
        if rec.status == "error" and rec.error:
            errors.append(f"{task.id}: {rec.error}")

    # 5. Mark packet as seen (even on partial failure — prevents partial replay)
    if not dry_run:
        mark_packet_seen(packet.packet_id, repo_root=repo_root)

    # 6. Build audit refs
    audit_refs = {
        "packetId": packet.packet_id,
        "conversationId": packet.source_conversation_id,
        "sourceTurnIds": packet.source_turn_ids,
        "documents": [d.path for d in packet.documents],
        "taskIds": [t.id for t in packet.tasks],
        "auditConversationHref": packet.audit_conversation_href,
        "dispatchedAt": dispatched_at,
    }

    return BridgeDispatchResult(
        packetId=packet.packet_id,
        dispatchedAt=dispatched_at,
        taskRecords=task_records,
        replayRejected=False,
        dryRun=dry_run,
        auditRefs=audit_refs,
        errors=errors,
    )
