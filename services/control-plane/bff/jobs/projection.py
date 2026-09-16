"""Unified Job Read and Action Projection Service.

Implements the normalized Management Job projection and per-source action
affordance calculation established in
``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md`` §2–§3:
- Preserves domain aggregate separation: ResearchTicket != Experiment != OrchestratorRun != Job
- Maps native records from the six qualified sources into unified Job schema
- Computes truthful allowedActions per source and lifecycle state:
  * research_orchestrator: canCancel (queued/dispatched/running), canRetry (failed/canceled/timeout)
  * other sources: all false until follow-up domain tasks complete (GW-STOP-FENCE-001, etc.)
  * openclaw: all false (permanently read-only diagnostics)
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def normalize_job_status(raw: Any) -> str:
    """Normalize raw source status or state string to standard job lifecycle status."""
    return str(raw or "pending").strip().lower()


def calculate_job_allowed_actions(
    source: str,
    status: str,
    record: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """Calculate truthful operator action affordances for a job based on source and state."""
    clean_source = str(source or "").strip().lower()
    clean_status = normalize_job_status(status)

    if clean_source in ("research_orchestrator", "orchestrator"):
        # U10B implements real cancel and retry execution closure for research orchestrator runs.
        # Archive and Promote remain disabled pending explicit Operator Decisions (D-JOBS §1/§3).
        can_cancel = clean_status in ("queued", "dispatched", "running", "active")
        can_retry = clean_status in ("failed", "canceled", "timeout")
        return {
            "canCancel": can_cancel,
            "canRetry": can_retry,
            "canArchive": False,
            "canPromote": False,
        }

    # All other sources: worker-gateway, training-session, source-ingest, policy-learning, openclaw
    # do not have verified backend actions in U10B scope. Actions fail closed via JobCommandAdapter.
    return {
        "canCancel": False,
        "canRetry": False,
        "canArchive": False,
        "canPromote": False,
    }


def project_job(spec: Any, native_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw domain record into the canonical Management Job model."""
    prefix = getattr(spec, "prefix", "")
    source_name = getattr(spec, "name", "unknown")
    job_id = f"{prefix}{native_id}"
    status = normalize_job_status(record.get("status") or record.get("state"))
    allowed_actions = calculate_job_allowed_actions(source_name, status, record)

    return {
        "job_id": job_id,
        "id": job_id,
        "native_id": native_id,
        "source": source_name,
        "job_type": source_name,
        "status": status,
        "attempt_number": record.get("attempt_number", 1),
        "parent_run_id": record.get("parent_run_id") or record.get("parent_job_id"),
        "root_run_id": record.get("root_run_id") or record.get("root_job_id"),
        "cancellation_fence": record.get("cancellation_fence"),
        "created_at": record.get("created_at") or record.get("queued_at") or record.get("dispatched_at"),
        "updated_at": record.get("updated_at") or record.get("completed_at"),
        "started_at": record.get("started_at"),
        "completed_at": record.get("completed_at"),
        "progress": record.get("progress"),
        "detail": record,
        "logs": record.get("logs") if isinstance(record.get("logs"), list) else None,
        "allowedActions": allowed_actions,
    }
