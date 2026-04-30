from __future__ import annotations

import json as _json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from store import ResearchOrchestratorStore, build_research_orchestrator_store


PRODUCTION_ADAPTERS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}
PRODUCTION_MODES = {"production", "paper", "canary", "live"}
STUB_ADAPTERS = {"stub", "handoff_only", "manual"}
ACTIVE_STATUSES = {"queued", "running", "dispatching"}
FAIL_CLOSED_SCOPE = "capability_metadata_read_only"
OFFLINE_DISPATCH_ENABLED_SCOPE = "offline_worker_dispatch_enabled"
# Adapters with declared gateway entrypoints that can be routed offline.
OFFLINE_ADAPTERS = {"qlib", "finrl", "rllib", "ray_tune", "trl"}
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
    return os.getenv("RESEARCH_ORCHESTRATOR_DATA_DIR", "/tmp/pantheon/research-orchestrator")


def _max_active_runs() -> int:
    return int(os.getenv("RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS", "8"))


def _production_adapters_allowed() -> bool:
    return os.getenv("RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS", "false").lower() == "true"


def _offline_gate_enabled() -> bool:
    return os.getenv("PANTHEON_OFFLINE_GATE_ENABLED", "false").lower() == "true"


def _gateway_url() -> str:
    return os.getenv("RESEARCH_WORKER_GATEWAY_URL", "http://research-worker-gateway-svc:8103")


DATA_DIR = _data_dir()
MAX_ACTIVE_RUNS = _max_active_runs()
PRODUCTION_ADAPTERS_ALLOWED = _production_adapters_allowed()
OFFLINE_GATE_ENABLED = _offline_gate_enabled()
GATEWAY_URL = _gateway_url()


def _route_to_gateway(adapter: str, task_id: str, run_id: str, objective: str, input_refs: List[Dict[str, Any]], parameters: Dict[str, Any], actor_id: str, timestamp: str) -> Optional[Dict[str, Any]]:
    """POST an offline-capable run to the research-worker-gateway."""
    request_body = {
        "worker": adapter,
        "requested_mode": "offline",
        "dispatch_mode": "offline",
        "objective": objective,
        "task_id": task_id,
        "run_id": run_id,
        "input_refs": input_refs,
        "parameters": parameters,
        "actor_id": actor_id,
        "idempotency_key": f"ro-{run_id}",
        "requested_at": timestamp,
    }
    payload = _json.dumps(request_body).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/research-worker-gateway/jobs",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _next_id(prefix: str, timestamp: str, existing: set[str]) -> str:
    date_prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"{prefix}-{date_prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"{prefix}-{date_prefix}-{index:03d}"
    return candidate


def _event(timestamp: str, event_type: str, summary: str, actor: str, run_id: str | None, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    sequence_number = len(events) + 1
    return {
        "event_id": _next_id("revt", timestamp, {str(event.get("event_id") or "") for event in events}),
        "event_type": event_type,
        "summary": summary,
        "actor": actor,
        "run_id": run_id,
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


def _request_text(body: DispatchRunBody) -> str:
    parts = [body.adapter, body.requested_mode, body.dispatch_mode]
    parts.extend(str(ref.get("type") or "") for ref in body.input_refs)
    parts.extend(str(ref.get("id") or "") for ref in body.input_refs)
    parts.extend(str(key) for key in body.parameters.keys())
    parts.extend(str(value) for value in body.parameters.values())
    return " ".join(parts).lower()


class CreateTaskBody(BaseModel):
    title: str
    objective: str
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    created_at: Optional[str] = None


class DispatchRunBody(BaseModel):
    adapter: str = "stub"
    requested_mode: str = "stub"
    dispatch_mode: str = "stub"
    input_refs: List[Dict[str, Any]] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    requested_at: Optional[str] = None


class CompleteRunBody(BaseModel):
    status: str = "completed"
    summary: str = "Research orchestration run completed."
    actor_id: str = "operator"
    completed_at: Optional[str] = None


class ArtifactBody(BaseModel):
    artifact_type: str = "research_report"
    artifact_family: str = "research_orchestration"
    title: str
    storage_ref: str
    checksum: str = ""
    registry_hints: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    created_at: Optional[str] = None


class ProposalBody(BaseModel):
    proposal_type: str = "registry_candidate"
    target_ref: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    requested_state: str = "candidate"
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    proposed_at: Optional[str] = None


app = FastAPI(title="Pantheon Research Orchestrator Service", version="0.1.0")
store = build_research_orchestrator_store(DATA_DIR)
register_fastapi_health_routes(
    app,
    "research-orchestrator",
    metrics=lambda: {
        "run_count": len(store.list_runs()),
        "active_run_count": len([run for run in store.list_runs() if str(run.get("status") or "").lower() in ACTIVE_STATUSES]),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "max_active_runs": MAX_ACTIVE_RUNS,
        "production_adapters_enabled": PRODUCTION_ADAPTERS_ALLOWED,
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    active_count = len([run for run in store.list_runs() if str(run.get("status") or "").lower() in ACTIVE_STATUSES])
    return {
        "status": "ok",
        "service": "research-orchestrator",
        "data_dir": DATA_DIR,
        "task_count": len(store.list_tasks()),
        "run_count": len(store.list_runs()),
        "active_run_count": active_count,
        "max_active_runs": MAX_ACTIVE_RUNS,
        "production_adapters_enabled": PRODUCTION_ADAPTERS_ALLOWED,
    }


@app.get("/api/research-orchestrator/capabilities")
def capabilities() -> Dict[str, Any]:
    def _effective_metadata(adapter: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        if OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS:
            updated = dict(meta)
            updated["gate_state"] = "activation_ready"
            updated["allowed_scope"] = OFFLINE_DISPATCH_ENABLED_SCOPE
            updated["gateway_routing"] = "enabled"
            return updated
        return meta

    return {
        "service": "research-orchestrator",
        "default_dispatch_mode": "stub",
        "production_activation": "disabled",
        "offline_gate": "enabled" if OFFLINE_GATE_ENABLED else "disabled",
        "bounded_dispatch": {"max_active_runs": MAX_ACTIVE_RUNS},
        "safety_boundary": {
            "training_dispatch": "disabled",
            "registry_writes": "disabled",
            "governance_writes": "disabled",
            "paper_canary_live": "disabled",
        },
        "capabilities": [
            {"adapter": adapter, "status": "available", "purpose": "lifecycle replay and handoff validation"}
            for adapter in sorted(STUB_ADAPTERS)
        ]
        + [
            {"adapter": adapter, **_effective_metadata(adapter, metadata)}
            for adapter, metadata in sorted(CAPABILITY_REGISTRY.items())
        ],
    }


@app.get("/api/research-orchestrator/tasks")
def list_tasks(status: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    tasks = store.list_tasks()
    if status:
        tasks = [task for task in tasks if str(task.get("status") or "").lower() == status.lower()]
    return tasks


@app.post("/api/research-orchestrator/tasks", status_code=201)
def create_task(body: CreateTaskBody) -> Dict[str, Any]:
    existing = _idempotent_match(store.list_tasks(), body.idempotency_key)
    if existing:
        return existing
    timestamp = body.created_at or utc_now()
    task_id = _next_id("rtask", timestamp, {str(task.get("task_id") or "") for task in store.list_tasks()})
    task = {
        "id": task_id,
        "task_id": task_id,
        "title": body.title,
        "objective": body.objective,
        "status": "ready",
        "source_refs": body.source_refs,
        "constraints": body.constraints,
        "created_by": body.actor_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "idempotency_key": body.idempotency_key,
    }
    return store.put_task(task)


@app.get("/api/research-orchestrator/tasks/{task_id}")
def get_task(task_id: str) -> Dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="research task not found")
    return task


@app.post("/api/research-orchestrator/tasks/{task_id}/runs", status_code=201)
def dispatch_run(task_id: str, body: DispatchRunBody) -> Dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="research task not found")
    existing = _idempotent_match(store.list_runs(), body.idempotency_key)
    if existing:
        return existing

    timestamp = body.requested_at or utc_now()
    adapter = body.adapter.lower().strip()
    requested_mode = body.requested_mode.lower().strip()
    dispatch_mode = body.dispatch_mode.lower().strip()
    rejected = False
    rejection = None
    request_text = _request_text(body)
    if any(token in request_text for token in ("registry_write", "direct_registry_write", "promote_to_registry")):
        rejected = True
        rejection = {
            "reason": "registry_write_disabled",
            "detail": "Research orchestrator may emit draft handoff records only; canonical registry writes are not allowed.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }
    elif any(token in request_text for token in ("governance_write", "governance_stage", "approve_governance")):
        rejected = True
        rejection = {
            "reason": "governance_write_disabled",
            "detail": "Research orchestrator cannot approve governance decisions or change deployment stages.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }
    elif adapter not in STUB_ADAPTERS and adapter not in CAPABILITY_REGISTRY:
        rejected = True
        rejection = {
            "reason": "unknown_adapter",
            "detail": f"Adapter family '{adapter}' is not registered for research orchestration.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline" and dispatch_mode == "offline":
        # Offline gate path: route to gateway and record the dispatch.
        pass  # Handled below after run_id is assigned.
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode not in PRODUCTION_MODES:
        rejected = True
        rejection = {
            "reason": "offline_mode_required",
            "detail": "Offline-gated adapter dispatch requires requested_mode=offline and dispatch_mode=offline.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }
    elif adapter in PRODUCTION_ADAPTERS or requested_mode in PRODUCTION_MODES:
        rejected = True
        rejection = {
            "reason": "production_adapter_disabled",
            "detail": "Research orchestrator production adapters and paper/canary/live modes are fail-closed in this service boundary.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }
    if not rejected and dispatch_mode not in STUB_ADAPTERS and not (OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline" and dispatch_mode == "offline"):
        rejected = True
        rejection = {
            "reason": "dispatch_mode_disabled",
            "detail": "Only stub/handoff-only research orchestration is enabled.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }

    active_count = len([run for run in store.list_runs() if str(run.get("status") or "").lower() in ACTIVE_STATUSES])
    if not rejected and active_count >= MAX_ACTIVE_RUNS:
        raise HTTPException(status_code=429, detail=f"active research runs exceed RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS={MAX_ACTIVE_RUNS}")

    run_id = _next_id("rrun", timestamp, {str(run.get("run_id") or "") for run in store.list_runs()})

    # Offline gate: route to gateway before recording the run.
    is_offline_dispatch = not rejected and OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline" and dispatch_mode == "offline"
    gateway_ref: Optional[Dict[str, Any]] = None
    if is_offline_dispatch:
        gw_result = _route_to_gateway(
            adapter,
            task_id,
            run_id,
            str(task.get("objective") or ""),
            body.input_refs,
            body.parameters,
            body.actor_id,
            timestamp,
        )
        if gw_result:
            gateway_ref = {"gateway_job_id": gw_result.get("job_id"), "gateway": "research-worker-gateway"}
        else:
            gateway_ref = {"gateway_job_id": None, "error": "gateway_unavailable"}

    events: List[Dict[str, Any]] = []
    if rejected:
        status = "rejected"
        summary = rejection["detail"] if rejection else "Rejected."
        events.append(_event(timestamp, "run_rejected", summary, body.actor_id, run_id, events))
    elif is_offline_dispatch:
        status = "dispatched"
        summary = f"Offline-gated adapter '{adapter}' dispatched to research-worker-gateway (gateway_job_id={gateway_ref.get('gateway_job_id') if gateway_ref else None})."
        events.append(_event(timestamp, "run_dispatched", summary, body.actor_id, run_id, events))
    else:
        status = "queued"
        summary = "Stub research orchestration run queued for bounded dispatch."
        events.append(_event(timestamp, "run_queued", summary, body.actor_id, run_id, events))

    run: Dict[str, Any] = {
        "id": run_id,
        "run_id": run_id,
        "task_id": task_id,
        "adapter": adapter,
        "requested_mode": requested_mode,
        "dispatch_mode": dispatch_mode,
        "status": status,
        "production_activation": "disabled",
        "input_refs": body.input_refs,
        "parameters": body.parameters,
        "created_by": body.actor_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "idempotency_key": body.idempotency_key,
        "rejection": rejection,
        "events": events,
        "artifact_refs": [],
        "proposal_refs": [],
    }
    if gateway_ref is not None:
        run["gateway_ref"] = gateway_ref
    task["status"] = "rejected" if rejected else "running"
    task["updated_at"] = timestamp
    store.put_task(task)
    store.put_run(run)
    for event in events:
        store.append_event(event)
    return run


@app.get("/api/research-orchestrator/runs")
def list_runs(task_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    runs = store.list_runs()
    if task_id:
        runs = [run for run in runs if run.get("task_id") == task_id]
    if status:
        runs = [run for run in runs if str(run.get("status") or "").lower() == status.lower()]
    return runs


@app.get("/api/research-orchestrator/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="research run not found")
    return run


@app.get("/api/research-orchestrator/runs/{run_id}/status")
def get_run_status(run_id: str) -> Dict[str, Any]:
    run = get_run(run_id)
    return {
        "run_id": run["run_id"],
        "task_id": run["task_id"],
        "status": run["status"],
        "adapter": run["adapter"],
        "requested_mode": run["requested_mode"],
        "dispatch_mode": run["dispatch_mode"],
        "production_activation": run["production_activation"],
        "rejection": run.get("rejection"),
        "gateway_ref": run.get("gateway_ref"),
        "artifact_refs": run.get("artifact_refs", []),
        "proposal_refs": run.get("proposal_refs", []),
        "events": run.get("events", []),
        "updated_at": run.get("updated_at"),
    }


@app.post("/api/research-orchestrator/runs/{run_id}/complete")
def complete_run(run_id: str, body: CompleteRunBody) -> Dict[str, Any]:
    run = get_run(run_id)
    if str(run.get("status") or "").lower() == "rejected":
        raise HTTPException(status_code=409, detail="rejected research run cannot be completed")
    timestamp = body.completed_at or utc_now()
    events = list(run.get("events") or [])
    events.append(_event(timestamp, "run_completed", body.summary, body.actor_id, run_id, events))
    run["status"] = body.status
    run["updated_at"] = timestamp
    run["events"] = events
    task = store.get_task(run["task_id"])
    if task:
        task["status"] = "completed" if body.status == "completed" else body.status
        task["updated_at"] = timestamp
        store.put_task(task)
    store.put_run(run)
    store.append_event(events[-1])
    return run


@app.post("/api/research-orchestrator/runs/{run_id}/artifacts", status_code=201)
def handoff_artifact(run_id: str, body: ArtifactBody) -> Dict[str, Any]:
    run = get_run(run_id)
    existing = _idempotent_match(store.list_artifacts(), body.idempotency_key)
    if existing:
        return existing
    timestamp = body.created_at or utc_now()
    artifact_id = _next_id("rart", timestamp, {str(artifact.get("artifact_id") or "") for artifact in store.list_artifacts()})
    registry_projection = {
        "artifact_type": body.registry_hints.get("artifact_type", body.artifact_type),
        "artifact_state": body.registry_hints.get("artifact_state", "draft"),
        "deployment_stage": "none",
        "lineage": [{"type": "research_run", "id": run_id}],
        "storage_ref": body.storage_ref,
        "checksum": body.checksum,
    }
    artifact = {
        "id": artifact_id,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "task_id": run["task_id"],
        "artifact_type": body.artifact_type,
        "artifact_family": body.artifact_family,
        "title": body.title,
        "storage_ref": body.storage_ref,
        "checksum": body.checksum,
        "artifact_state": "draft",
        "deployment_stage": "none",
        "governance": {
            "direct_live_influence": False,
            "lean_consumption": "research_only_not_direct_action",
            "write_boundary": "research_plane_only",
        },
        "registry_projection": registry_projection,
        "metadata": body.metadata,
        "created_by": body.actor_id,
        "created_at": timestamp,
        "idempotency_key": body.idempotency_key,
    }
    refs = list(run.get("artifact_refs") or [])
    refs.append({"artifact_id": artifact_id, "artifact_type": body.artifact_type})
    run["artifact_refs"] = refs
    run["updated_at"] = timestamp
    store.put_run(run)
    return store.put_artifact(artifact)


@app.get("/api/research-orchestrator/runs/{run_id}/artifacts")
def list_run_artifacts(run_id: str) -> List[Dict[str, Any]]:
    get_run(run_id)
    return [artifact for artifact in store.list_artifacts() if artifact.get("run_id") == run_id]


@app.post("/api/research-orchestrator/runs/{run_id}/proposals", status_code=201)
def handoff_proposal(run_id: str, body: ProposalBody) -> Dict[str, Any]:
    run = get_run(run_id)
    existing = _idempotent_match(store.list_proposals(), body.idempotency_key)
    if existing:
        return existing
    timestamp = body.proposed_at or utc_now()
    proposal_id = _next_id("rprop", timestamp, {str(proposal.get("proposal_id") or "") for proposal in store.list_proposals()})
    proposal = {
        "id": proposal_id,
        "proposal_id": proposal_id,
        "run_id": run_id,
        "task_id": run["task_id"],
        "proposal_type": body.proposal_type,
        "target_ref": body.target_ref,
        "rationale": body.rationale,
        "requested_state": body.requested_state,
        "status": "proposed",
        "production_activation": "disabled",
        "evidence_refs": body.evidence_refs,
        "created_by": body.actor_id,
        "created_at": timestamp,
        "idempotency_key": body.idempotency_key,
    }
    refs = list(run.get("proposal_refs") or [])
    refs.append({"proposal_id": proposal_id, "proposal_type": body.proposal_type})
    run["proposal_refs"] = refs
    run["updated_at"] = timestamp
    store.put_run(run)
    return store.put_proposal(proposal)


@app.get("/api/research-orchestrator/runs/{run_id}/proposals")
def list_run_proposals(run_id: str) -> List[Dict[str, Any]]:
    get_run(run_id)
    return [proposal for proposal in store.list_proposals() if proposal.get("run_id") == run_id]
