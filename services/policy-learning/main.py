from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from store import PolicyLearningStore


PRODUCTION_ADAPTERS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}
STUB_ADAPTER = "stub"
FAIL_CLOSED_SCOPE = "capability_metadata_read_only"
CAPABILITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "openclaw": {
        "status": "deferred",
        "purpose": "OpenClaw agent runtime substrate",
        "activation_gate": "OPENCLAW_PRODUCTION_BROKER_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "qlib": {
        "status": "deferred",
        "activation_gate": "services/research/qlib/requirements.txt",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "trl": {
        "status": "deferred",
        "activation_gate": "services/learning/trl/ACTIVATION_CRITERIA.md",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "finrl": {
        "status": "deferred",
        "activation_gate": "PANTHEON_FINRL_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "rllib": {
        "status": "deferred",
        "activation_gate": "PANTHEON_RLLIB_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "ray_tune": {
        "status": "deferred",
        "activation_gate": "PANTHEON_RAYTUNE_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "wandb": {
        "status": "deferred",
        "activation_gate": "services/registry/experiments/WANDB_ACTIVATION.md",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_dir() -> str:
    return os.getenv("POLICY_LEARNING_DATA_DIR", "/tmp/pantheon/policy-learning")


def _production_adapters_allowed() -> bool:
    return os.getenv("POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS", "false").lower() == "true"


def _next_job_id(timestamp: str, existing: set[str]) -> str:
    prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"plj-{prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"plj-{prefix}-{index:03d}"
    return candidate


def _next_event_id(timestamp: str, events: List[Dict[str, Any]]) -> str:
    prefix = timestamp[:10].replace("-", "")
    next_sequence = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    return f"plevt-{prefix}-{next_sequence:03d}"


def _event(timestamp: str, event_type: str, summary: str, actor: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    sequence_number = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    return {
        "event_id": _next_event_id(timestamp, events),
        "event_type": event_type,
        "summary": summary,
        "actor": actor,
        "emitted_at": timestamp,
        "sequence_number": sequence_number,
    }


class ProposalBody(BaseModel):
    policy_id: str
    objective: str
    adapter: str = STUB_ADAPTER
    requested_mode: str = "stub"
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    proposed_at: Optional[str] = None


class RejectBody(BaseModel):
    reason: str
    actor_id: str = "operator"
    rejected_at: Optional[str] = None


def _proposal_text(body: ProposalBody) -> str:
    parts = [body.adapter, body.requested_mode, body.objective]
    parts.extend(str(ref.get("type") or "") for ref in body.source_refs)
    parts.extend(str(ref.get("id") or "") for ref in body.source_refs)
    parts.extend(str(key) for key in body.constraints.keys())
    parts.extend(str(value) for value in body.constraints.values())
    return " ".join(parts).lower()


app = FastAPI(title="Pantheon Policy Learning Service", version="0.1.0")
store = PolicyLearningStore(_data_dir())
register_fastapi_health_routes(
    app,
    "policy-learning",
    metrics=lambda: {"job_count": len(store.list_jobs())},
    details=lambda: {
        "data_dir": _data_dir(),
        "production_adapters_enabled": _production_adapters_allowed(),
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "policy-learning",
        "data_dir": _data_dir(),
        "job_count": len(store.list_jobs()),
        "production_adapters_enabled": _production_adapters_allowed(),
    }


@app.get("/api/policy-learning/capabilities")
def capabilities() -> Dict[str, Any]:
    return {
        "service": "policy-learning",
        "default_mode": "stub",
        "production_activation": "disabled",
        "safety_boundary": {
            "training_dispatch": "disabled",
            "registry_writes": "disabled",
            "governance_writes": "disabled",
            "paper_canary_live": "disabled",
        },
        "capabilities": [
            {
                "adapter": STUB_ADAPTER,
                "status": "available",
                "purpose": "non-production lifecycle replay and contract validation",
            }
        ]
        + [
            {"adapter": adapter, **metadata}
            for adapter, metadata in sorted(CAPABILITY_REGISTRY.items())
        ],
    }


@app.get("/api/policy-learning/jobs")
def list_jobs(status: Optional[str] = Query(default=None), policy_id: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    jobs = store.list_jobs()
    if status:
        jobs = [job for job in jobs if str(job.get("status") or "").lower() == status.lower()]
    if policy_id:
        jobs = [job for job in jobs if job.get("policy_id") == policy_id]
    return jobs


@app.post("/api/policy-learning/jobs", status_code=201)
def propose_job(body: ProposalBody) -> Dict[str, Any]:
    timestamp = body.proposed_at or utc_now()
    adapter = body.adapter.lower().strip()
    requested_mode = body.requested_mode.lower().strip()
    existing_ids = {str(job.get("job_id") or "") for job in store.list_jobs()}
    job_id = _next_job_id(timestamp, existing_ids)
    events: List[Dict[str, Any]] = []
    request_text = _proposal_text(body)
    rejection = None
    if any(token in request_text for token in ("registry_write", "direct_registry_write", "promote_to_registry")):
        status = "rejected"
        rejection = {
            "reason": "registry_write_disabled",
            "detail": "Policy-learning jobs may record proposals only; canonical registry writes are not allowed.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif any(token in request_text for token in ("governance_write", "governance_stage", "approve_governance")):
        status = "rejected"
        rejection = {
            "reason": "governance_write_disabled",
            "detail": "Policy-learning jobs cannot approve governance decisions or change deployment stages.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif adapter != STUB_ADAPTER and adapter not in CAPABILITY_REGISTRY:
        status = "rejected"
        rejection = {
            "reason": "unknown_adapter",
            "detail": f"Adapter family '{adapter}' is not registered for policy learning.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif adapter in PRODUCTION_ADAPTERS or requested_mode in {"production", "paper", "canary", "live"}:
        status = "rejected"
        rejection = {
            "reason": "production_adapter_disabled",
            "detail": "Policy-learning production adapters are not activated in this service boundary.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    else:
        status = "proposed"
        events.append(_event(timestamp, "proposal_recorded", "Stub policy-learning job proposal recorded.", body.actor_id, events))
    job = {
        "id": job_id,
        "job_id": job_id,
        "policy_id": body.policy_id,
        "objective": body.objective,
        "adapter": adapter,
        "requested_mode": requested_mode,
        "status": status,
        "production_activation": "disabled",
        "source_refs": body.source_refs,
        "constraints": body.constraints,
        "created_at": timestamp,
        "updated_at": timestamp,
        "created_by": body.actor_id,
        "rejection": rejection,
        "events": events,
    }
    store.put_job(job)
    return job


@app.get("/api/policy-learning/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="policy learning job not found")
    return job


@app.get("/api/policy-learning/jobs/{job_id}/status")
def get_job_status(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    return {
        "job_id": job["job_id"],
        "policy_id": job["policy_id"],
        "status": job["status"],
        "adapter": job["adapter"],
        "requested_mode": job["requested_mode"],
        "production_activation": job["production_activation"],
        "rejection": job.get("rejection"),
        "events": job.get("events", []),
        "updated_at": job.get("updated_at"),
    }


@app.post("/api/policy-learning/jobs/{job_id}/reject")
def reject_job(job_id: str, body: RejectBody) -> Dict[str, Any]:
    job = get_job(job_id)
    if str(job.get("status") or "").lower() == "rejected":
        return job
    timestamp = body.rejected_at or utc_now()
    events = list(job.get("events") or [])
    events.append(_event(timestamp, "job_rejected", body.reason, body.actor_id, events))
    job["status"] = "rejected"
    job["updated_at"] = timestamp
    job["rejection"] = {"reason": body.reason, "rejected_at": timestamp, "rejected_by": body.actor_id}
    job["events"] = events
    return store.put_job(job)
