from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from store import ResearchWorkerGatewayStore, build_research_worker_gateway_store


SAFE_WORKERS = {"stub", "handoff_only", "manual"}
PRODUCTION_WORKERS = {"qlib", "finrl", "rllib", "ray_tune", "vectorbt", "statsmodels", "quantlib", "trl", "rl", "wandb", "openclaw"}
SAFE_DISPATCH_MODES = {"stub", "handoff_only", "manual"}
PRODUCTION_MODES = {"production", "paper", "canary", "live"}
ACTIVE_STATUSES = {"queued", "running", "cancel_requested"}
TERMINAL_STATUSES = {"completed", "failed", "canceled", "rejected"}
FAIL_CLOSED_SCOPE = "capability_metadata_read_only"

WORKER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "stub": {
        "status": "available",
        "purpose": "safe lifecycle replay and dispatch contract validation",
        "dispatch_modes": sorted(SAFE_DISPATCH_MODES),
    },
    "handoff_only": {
        "status": "available",
        "purpose": "record-only handoff validation without worker execution",
        "dispatch_modes": ["handoff_only"],
    },
    "manual": {
        "status": "available",
        "purpose": "operator-recorded research worker handoff",
        "dispatch_modes": ["manual"],
    },
    "qlib": {
        "status": "deferred",
        "entrypoint": "services/research/qlib/worker.py",
        "activation_gate": "services/research/qlib/requirements.txt",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "finrl": {
        "status": "deferred",
        "entrypoint": "services/research/finrl/worker.py",
        "activation_gate": "PANTHEON_FINRL_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "rllib": {
        "status": "deferred",
        "entrypoint": "services/research/rllib/worker.py",
        "activation_gate": "PANTHEON_RLLIB_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "ray_tune": {
        "status": "deferred",
        "entrypoint": "services/research/rllib/ray_tune_worker.py",
        "activation_gate": "PANTHEON_RAYTUNE_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "vectorbt": {
        "status": "deferred",
        "entrypoint": "services/research/vectorbt/worker.py",
        "activation_gate": "services/research/vectorbt/ACTIVATION_CRITERIA.md",
    },
    "statsmodels": {
        "status": "deferred",
        "entrypoint": "services/research/statsmodels/worker.py",
        "activation_gate": "services/research/statsmodels/ACTIVATION_CRITERIA.md",
    },
    "quantlib": {
        "status": "deferred",
        "entrypoint": "services/research/quantlib/worker.py",
        "activation_gate": "services/research/quantlib/ACTIVATION_CRITERIA.md",
    },
    "openclaw": {
        "status": "deferred",
        "purpose": "OpenClaw agent runtime substrate (consultation / teaching / workflow)",
        "activation_gate": "OPENCLAW_PRODUCTION_BROKER_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
        "note": (
            "OpenClaw is an agent runtime substrate, not a direct research worker. "
            "Dispatch through this gateway is deferred; capability metadata is readable "
            "in fail-closed mode via services/openclaw-gateway-adapter."
        ),
    },
    "trl": {
        "status": "deferred",
        "entrypoint": "services/learning/trl/worker.py",
        "activation_gate": "PANTHEON_TRL_ACTIVATION_READY_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
        "preflight": "services/learning/trl/preflight.py",
    },
    "wandb": {
        "status": "deferred",
        "purpose": "optional experiment registry backend",
        "activation_gate": "services/registry/experiments/WANDB_ACTIVATION.md",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_dir() -> str:
    return os.getenv("RESEARCH_WORKER_GATEWAY_DATA_DIR", "/tmp/pantheon/research-worker-gateway")


def _max_active_jobs() -> int:
    return int(os.getenv("RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS", "4"))


def _production_adapters_allowed() -> bool:
    return os.getenv("RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS", "false").lower() == "true"


def _orchestrator_url() -> str:
    return os.getenv("RESEARCH_ORCHESTRATOR_URL", "http://research-orchestrator-svc:8101")


DATA_DIR = _data_dir()
MAX_ACTIVE_JOBS = _max_active_jobs()
PRODUCTION_ADAPTERS_ALLOWED = _production_adapters_allowed()
ORCHESTRATOR_URL = _orchestrator_url()


def _next_id(prefix: str, timestamp: str, existing: set[str]) -> str:
    date_prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"{prefix}-{date_prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"{prefix}-{date_prefix}-{index:03d}"
    return candidate


def _event(timestamp: str, event_type: str, summary: str, actor: str, job_id: str | None, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    sequence_number = len(events) + 1
    return {
        "event_id": _next_id("wgevt", timestamp, {str(event.get("event_id") or "") for event in events}),
        "event_type": event_type,
        "summary": summary,
        "actor": actor,
        "job_id": job_id,
        "emitted_at": timestamp,
        "sequence_number": sequence_number,
    }


def _idempotent_match(records: List[Dict[str, Any]], key: str | None) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    for record in records:
        if record.get("idempotency_key") == key:
            return record
    return None


def _flatten_request_text(body: "DispatchJobBody") -> str:
    parts = [body.worker, body.requested_mode, body.dispatch_mode, body.objective]
    parts.extend(str(ref.get("type") or "") for ref in body.input_refs)
    parts.extend(str(ref.get("id") or "") for ref in body.input_refs)
    parts.extend(str(key) for key in body.parameters.keys())
    parts.extend(str(value) for value in body.parameters.values())
    return " ".join(parts).lower()


def _rejection_for(body: "DispatchJobBody", worker: str, requested_mode: str, dispatch_mode: str, timestamp: str) -> Optional[Dict[str, Any]]:
    request_text = _flatten_request_text(body)
    if "ep5" in request_text or "production_training" in request_text or "production-learning" in request_text:
        return {
            "reason": "learning_activation_disabled",
            "detail": "EP5 and production learning activation are outside the research worker gateway boundary.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if any(token in request_text for token in ("lean", "signalstore", "signal_store", "live_trading", "execution-plane")):
        return {
            "reason": "execution_path_disabled",
            "detail": "LEAN, SignalStore, and live execution paths are disabled for research worker gateway jobs.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if any(token in request_text for token in ("registry_write", "direct_registry_write", "promote_to_registry")):
        return {
            "reason": "registry_write_disabled",
            "detail": "The gateway may emit output refs only; canonical registry writes are not allowed.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if any(token in request_text for token in ("governance_write", "governance_stage", "approve_governance")):
        return {
            "reason": "governance_write_disabled",
            "detail": "The gateway cannot approve governance decisions or change deployment stages.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if worker in PRODUCTION_WORKERS or requested_mode in PRODUCTION_MODES:
        return {
            "reason": "production_adapter_disabled",
            "detail": "Production research adapters and paper/canary/live modes are fail-closed in this service boundary.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if worker not in WORKER_REGISTRY:
        return {
            "reason": "unknown_worker",
            "detail": f"Worker family '{worker}' is not registered for gateway dispatch.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if dispatch_mode not in SAFE_DISPATCH_MODES:
        return {
            "reason": "dispatch_mode_disabled",
            "detail": "Only stub, handoff_only, and manual dispatch modes are enabled.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    if worker not in SAFE_WORKERS:
        return {
            "reason": "production_adapter_disabled",
            "detail": "Registered research worker is deferred until a separate activation task approves it.",
            "rejected_at": timestamp,
            "rejected_by": "research-worker-gateway",
        }
    return None


class DispatchJobBody(BaseModel):
    worker: str = "stub"
    requested_mode: str = "stub"
    dispatch_mode: str = "stub"
    objective: str = "Record safe research worker dispatch."
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    input_refs: List[Dict[str, Any]] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    requested_at: Optional[str] = None


class CancelJobBody(BaseModel):
    reason: str = "operator_cancel_requested"
    actor_id: str = "operator"
    canceled_at: Optional[str] = None


app = FastAPI(title="Pantheon Research Worker Gateway", version="0.1.0")
store = build_research_worker_gateway_store(DATA_DIR)
register_fastapi_health_routes(
    app,
    "research-worker-gateway",
    dependencies=lambda: {
        "research_orchestrator": {"status": "ok" if ORCHESTRATOR_URL else "degraded", "url": ORCHESTRATOR_URL}
    },
    metrics=lambda: {
        "job_count": len(store.list_jobs()),
        "active_job_count": len([job for job in store.list_jobs() if str(job.get("status") or "").lower() in ACTIVE_STATUSES]),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "max_active_jobs": MAX_ACTIVE_JOBS,
        "production_adapters_enabled": PRODUCTION_ADAPTERS_ALLOWED,
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    active_count = len([job for job in store.list_jobs() if str(job.get("status") or "").lower() in ACTIVE_STATUSES])
    return {
        "status": "ok",
        "service": "research-worker-gateway",
        "data_dir": DATA_DIR,
        "job_count": len(store.list_jobs()),
        "active_job_count": active_count,
        "max_active_jobs": MAX_ACTIVE_JOBS,
        "production_adapters_enabled": PRODUCTION_ADAPTERS_ALLOWED,
        "dependencies": {
            "research_orchestrator": {
                "url": ORCHESTRATOR_URL,
                "required_for_boot": False,
            }
        },
    }


@app.get("/api/research-worker-gateway/capabilities")
def capabilities() -> Dict[str, Any]:
    return {
        "service": "research-worker-gateway",
        "default_dispatch_mode": "stub",
        "production_activation": "disabled",
        "bounded_dispatch": {"max_active_jobs": MAX_ACTIVE_JOBS},
        "safety_boundary": {
            "registry_writes": "disabled",
            "governance_writes": "disabled",
            "lean_execution": "disabled",
            "ep5_activation": "disabled",
        },
        "capabilities": [
            {"worker": worker, **metadata}
            for worker, metadata in sorted(WORKER_REGISTRY.items())
        ],
    }


@app.post("/api/research-worker-gateway/jobs", status_code=201)
def dispatch_job(body: DispatchJobBody) -> Dict[str, Any]:
    existing = _idempotent_match(store.list_jobs(), body.idempotency_key)
    if existing:
        return existing

    timestamp = body.requested_at or utc_now()
    worker = body.worker.lower().strip()
    requested_mode = body.requested_mode.lower().strip()
    dispatch_mode = body.dispatch_mode.lower().strip()
    rejection = _rejection_for(body, worker, requested_mode, dispatch_mode, timestamp)

    active_count = len([job for job in store.list_jobs() if str(job.get("status") or "").lower() in ACTIVE_STATUSES])
    if rejection is None and active_count >= MAX_ACTIVE_JOBS:
        raise HTTPException(status_code=429, detail=f"active research worker jobs exceed RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS={MAX_ACTIVE_JOBS}")

    job_id = _next_id("wjob", timestamp, {str(job.get("job_id") or "") for job in store.list_jobs()})
    events: List[Dict[str, Any]] = []
    status = "rejected" if rejection else "queued"
    summary = rejection["detail"] if rejection else "Safe research worker gateway job queued for stub dispatch."
    events.append(_event(timestamp, "job_rejected" if rejection else "job_queued", summary, body.actor_id, job_id, events))

    output_refs: List[Dict[str, Any]] = []
    if rejection is None:
        output_id = _next_id("wgout", timestamp, {str(output.get("output_id") or "") for output in store.list_outputs()})
        output = store.put_output(
            {
                "id": output_id,
                "output_id": output_id,
                "job_id": job_id,
                "output_type": "dispatch_stub",
                "storage_ref": f"research-worker-gateway://jobs/{job_id}",
                "summary": "Queued safe stub dispatch; no production worker process was started.",
                "created_at": timestamp,
            }
        )
        output_refs.append({"output_id": output["output_id"], "output_type": output["output_type"]})

    job = {
        "id": job_id,
        "job_id": job_id,
        "worker": worker,
        "task_id": body.task_id,
        "run_id": body.run_id,
        "objective": body.objective,
        "requested_mode": requested_mode,
        "dispatch_mode": dispatch_mode,
        "status": status,
        "production_activation": "disabled",
        "input_refs": body.input_refs,
        "parameters": body.parameters,
        "output_refs": output_refs,
        "exit_code": None,
        "created_by": body.actor_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "idempotency_key": body.idempotency_key,
        "rejection": rejection,
        "cancel_reason": None,
        "events": events,
    }
    store.put_job(job)
    for event in events:
        store.append_event(event)
    return job


@app.get("/api/research-worker-gateway/jobs")
def list_jobs(
    worker: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
) -> List[Dict[str, Any]]:
    jobs = store.list_jobs()
    if worker:
        jobs = [job for job in jobs if str(job.get("worker") or "").lower() == worker.lower()]
    if status:
        jobs = [job for job in jobs if str(job.get("status") or "").lower() == status.lower()]
    if task_id:
        jobs = [job for job in jobs if job.get("task_id") == task_id]
    if run_id:
        jobs = [job for job in jobs if job.get("run_id") == run_id]
    return jobs


@app.get("/api/research-worker-gateway/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="research worker gateway job not found")
    return job


@app.get("/api/research-worker-gateway/jobs/{job_id}/status")
def get_job_status(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    return {
        "job_id": job["job_id"],
        "worker": job["worker"],
        "task_id": job.get("task_id"),
        "run_id": job.get("run_id"),
        "status": job["status"],
        "requested_mode": job["requested_mode"],
        "dispatch_mode": job["dispatch_mode"],
        "production_activation": job["production_activation"],
        "rejection": job.get("rejection"),
        "cancel_reason": job.get("cancel_reason"),
        "output_refs": job.get("output_refs", []),
        "exit_code": job.get("exit_code"),
        "events": job.get("events", []),
        "updated_at": job.get("updated_at"),
    }


@app.post("/api/research-worker-gateway/jobs/{job_id}/cancel")
def cancel_job(job_id: str, body: CancelJobBody) -> Dict[str, Any]:
    job = get_job(job_id)
    status = str(job.get("status") or "").lower()
    if status in TERMINAL_STATUSES:
        return job
    timestamp = body.canceled_at or utc_now()
    events = list(job.get("events") or [])
    events.append(_event(timestamp, "job_canceled", body.reason, body.actor_id, job_id, events))
    job["status"] = "canceled"
    job["updated_at"] = timestamp
    job["cancel_reason"] = body.reason
    job["events"] = events
    store.put_job(job)
    store.append_event(events[-1])
    return job
