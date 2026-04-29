from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from store import ResearchOrchestratorStore


PRODUCTION_ADAPTERS = {"qlib", "trl", "rl", "rllib", "finrl", "wandb"}
PRODUCTION_MODES = {"production", "paper", "canary", "live"}
STUB_ADAPTERS = {"stub", "handoff_only", "manual"}
ACTIVE_STATUSES = {"queued", "running", "dispatching"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_dir() -> str:
    return os.getenv("RESEARCH_ORCHESTRATOR_DATA_DIR", "/tmp/pantheon/research-orchestrator")


def _max_active_runs() -> int:
    return int(os.getenv("RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS", "8"))


def _production_adapters_allowed() -> bool:
    return os.getenv("RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS", "false").lower() == "true"


DATA_DIR = _data_dir()
MAX_ACTIVE_RUNS = _max_active_runs()
PRODUCTION_ADAPTERS_ALLOWED = _production_adapters_allowed()


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
store = ResearchOrchestratorStore(DATA_DIR)
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
    return {
        "service": "research-orchestrator",
        "default_dispatch_mode": "stub",
        "production_activation": "enabled" if PRODUCTION_ADAPTERS_ALLOWED else "disabled",
        "bounded_dispatch": {"max_active_runs": MAX_ACTIVE_RUNS},
        "capabilities": [
            {"adapter": adapter, "status": "available", "purpose": "lifecycle replay and handoff validation"}
            for adapter in sorted(STUB_ADAPTERS)
        ]
        + [
            {
                "adapter": adapter,
                "status": "deferred",
                "activation_gate": f"services/research/{adapter}/ACTIVATION_CRITERIA.md",
            }
            for adapter in sorted(PRODUCTION_ADAPTERS)
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
    if not PRODUCTION_ADAPTERS_ALLOWED and (adapter in PRODUCTION_ADAPTERS or requested_mode in PRODUCTION_MODES):
        rejected = True
        rejection = {
            "reason": "production_adapter_disabled",
            "detail": "Research orchestrator does not activate Qlib/TRL/RL production paths in this service boundary.",
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }
    if dispatch_mode not in STUB_ADAPTERS:
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
    events: List[Dict[str, Any]] = []
    status = "rejected" if rejected else "queued"
    summary = rejection["detail"] if rejection else "Stub research orchestration run queued for bounded dispatch."
    events.append(_event(timestamp, "run_rejected" if rejected else "run_queued", summary, body.actor_id, run_id, events))
    run = {
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
