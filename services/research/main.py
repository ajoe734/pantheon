from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import json as _json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("research-orchestrator")

from services.deployment.auth import AUTHENTICATED_SERVICE_ROLES, AuthenticatedTenant, AuthError, TenantBoundaryError, authenticate_tenant
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.registry.alpha_template import (
    ALPHA_TEMPLATE_METADATA_KEY,
    AlphaTemplateValidationError,
    alpha_template_registry_id,
    ensure_builtin_alpha_templates,
)
from services.registry.models import ArtifactType
from services.registry.split_api import RegistryService
from services.registry.strategy_artifact import (
    StrategyArtifactValidationError,
    mutate_strategy_artifact,
    strategy_artifact_checksum,
    validate_strategy_artifact,
)
from services.registry.storage import get_store as get_registry_store
from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.experiments import (
    ExperimentRegistryWritebackError,
    ExperimentRun,
    registry_entry_view_to_dict,
    write_experiment_run_artifact_to_registry,
)
from services.research.experiment_candidate_intake import (
    ExperimentCandidateIntakeError,
    intake_imitation_candidate,
)
from services.research.store import ResearchOrchestratorStore, build_research_orchestrator_store
from services.research.research_memory_outbox import (
    ResearchMemoryEligibilityError,
    ResearchMemoryOutboxRecord,
    ResearchMemoryOutboxStore,
    build_research_memory_outbox_store,
    get_outbox_store,
    validate_research_memory_writeback_eligibility,
    resolve_memory_sponsor,
)
from services.research.memory_writeback_worker import MemoryWritebackWorker
from services.research.retrieval_influence import (
    ResearchRetrievalInfluenceError,
    ResearchRetrievalInfluenceRecord,
    ResearchRetrievalInfluenceStore,
    build_research_retrieval_influence_store,
    get_influence_store,
    project_lineage_inspiration_edge,
)


PRODUCTION_ADAPTERS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}
PRODUCTION_MODES = {"production", "paper", "canary", "live"}
ALLOWED_STAGE_MODES = frozenset({"offline", "simulation", "fixture", "real", "stub", "handoff_only", "manual"})
STUB_ADAPTERS = {"stub", "handoff_only", "manual"}
ACTIVE_STATUSES = {"queued", "running", "dispatching"}
FAIL_CLOSED_SCOPE = "capability_metadata_read_only"
OFFLINE_DISPATCH_ENABLED_SCOPE = "offline_worker_dispatch_enabled"
_SHA256_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_RESOLVABLE_STORAGE_SCHEMES = (
    "$.",
    "file://",
    "memory://",
    "object://",
    "research-worker-gateway://",
    "s3://",
)
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



def _model_backtest_config(saved_model: Dict[str, Any], stage_params: Dict[str, Any], requested_by: str) -> Any:
    """Backtest config for a plan whose model was frozen from the owner Persona.

    The saved model was frozen by the BFF from the owner Persona through
    ``ArtifactLoader.load_exact``; here it is only checked against its own
    frozen checksum before use.  Stage parameters may only move governed mutation-surface controls, in which case
    the evaluated artifact is a Registry-derived candidate of the frozen base.
    """

    from services.research.vectorbt.adapter.vectorbt_adapter import BacktestConfig

    base = saved_model["strategy_artifact"]
    validate_strategy_artifact(base)
    if strategy_artifact_checksum(base) != saved_model["strategy_artifact_checksum"]:
        raise ValueError("saved model artifact does not match its frozen checksum")
    controls = {c["parameter_key"]: c for c in base["mutation_surface"]["controls"]}
    unknown = sorted(key for key in stage_params if key not in base["parameters"])
    if unknown:
        raise ValueError("stage parameters are not parameters of the frozen strategy artifact: " + ", ".join(unknown))
    updates = {key: value for key, value in stage_params.items() if value != base["parameters"][key]}
    candidate = base
    if updates:
        immutable = sorted(key for key in updates if key not in controls)
        if immutable:
            raise ValueError("stage parameters are immutable in the frozen strategy artifact: " + ", ".join(immutable))
        digest = hashlib.sha256(json.dumps({"base": saved_model["strategy_artifact_checksum"], "updates": updates}, sort_keys=True).encode()).hexdigest()[:12]
        major, minor, patch = (int(part) for part in str(base["version"]).split("."))
        candidate = mutate_strategy_artifact(
            base,
            new_artifact_id=f"{base['artifact_id']}-cand-{digest}",
            new_version=f"{major}.{minor}.{patch + 1}",
            parameter_updates=updates,
            source_run_ids=[f"research-candidate-{digest}"],
            parent_registry_id=base["artifact_id"],
        )
    return BacktestConfig(
        version="1.0.0",
        requested_by=requested_by,
        strategy_params=dict(candidate["parameters"]),
        strategy_artifact=candidate,
    )


def _model_provenance(saved_model: Dict[str, Any], evaluated: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the frozen base checksum distinct from the evaluated (possibly derived) checksum."""

    return {
        "base_artifact_id": saved_model["artifact_id"],
        "base_artifact_version": saved_model["artifact_version"],
        "base_artifact_checksum": saved_model["strategy_artifact_checksum"],
        "persona_id": saved_model["persona_id"],
        "tenant_id": saved_model["tenant_id"],
        "strategy_id": saved_model["strategy_id"],
        "strategy_spec_registry_id": saved_model.get("strategy_spec_registry_id"),
        "symbols": list(saved_model["symbols"]),
        "evaluated_artifact_id": evaluated["artifact_id"],
        "evaluated_artifact_version": evaluated["version"],
        "evaluated_artifact_checksum": strategy_artifact_checksum(evaluated),
        "derived_candidate": evaluated["artifact_id"] != saved_model["artifact_id"],
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


def _alpha_replication_data_dir() -> str:
    return os.getenv("ALPHA_REPLICATION_DATA_DIR", "data/alpha-replication")


DATA_DIR = _data_dir()
MAX_ACTIVE_RUNS = _max_active_runs()
PRODUCTION_ADAPTERS_ALLOWED = _production_adapters_allowed()
OFFLINE_GATE_ENABLED = _offline_gate_enabled()
GATEWAY_URL = _gateway_url()
ALPHA_REPLICATION_DATA_DIR = _alpha_replication_data_dir()
STORE_BACKEND = os.getenv("RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND", "jsonl").strip().lower() or "jsonl"
PERSISTENCE_POSTURE = require_persistence_posture("research-orchestrator")


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


def _extract_dataset_refs(refs: Any) -> List[str]:
    items = refs if isinstance(refs, (list, tuple, set)) else [refs] if refs else []
    return [
        str(r.get("id") or r.get("dataset_id") if isinstance(r, dict) else r).strip()
        for r in items
        if (isinstance(r, str) and (r.startswith("dataset:") or r.startswith("ds-") or r.startswith("dataset-")))
        or (isinstance(r, dict) and r.get("type") == "dataset" and (r.get("id") or r.get("dataset_id")))
    ]


def _matches_dataset_ref(cand: Any, allowed_refs: List[str]) -> bool:
    if not cand or not isinstance(cand, dict) or not allowed_refs:
        return False
    cid = str(cand.get("dataset_id") or cand.get("id") or "").strip()
    clean_cid = cid.split(":", 1)[-1] if ":" in cid else cid
    return any(cid == ref or clean_cid == (ref.split(":", 1)[-1] if ":" in ref else ref) for ref in allowed_refs)


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


class CancelRunBody(BaseModel):
    reason: Optional[str] = "Research run canceled by operator."
    actor_id: str = "operator"
    canceled_at: Optional[str] = None


class RetryRunBody(BaseModel):
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    requested_at: Optional[str] = None


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


class RegistryWritebackBody(BaseModel):
    artifact_id: Optional[str] = None
    registry_id: Optional[str] = None
    artifact_type: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_spec_version: Optional[str] = None
    version: Optional[str] = None
    requested_artifact_state: str = "candidate"
    storage_ref: Optional[Any] = None
    checksum: Optional[str] = None
    source_strategy_spec_id: Optional[str] = None
    source_dataset_refs: List[str] = Field(default_factory=list)
    parent_registry_ids: List[str] = Field(default_factory=list)
    dataset_version_id: Optional[str] = None
    code_version: Optional[str] = None
    input_manifest_ref: Optional[str] = None
    output_manifest_ref: Optional[str] = None
    metric_bundle_id: Optional[str] = None
    runtime_env: str = "research"
    evaluation_summary: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    created_at: Optional[str] = None


class MemoryWritebackBody(BaseModel):
    artifact_id: Optional[str] = None
    sponsor_persona_id: Optional[str] = None
    summary: Optional[str] = None
    headline: Optional[str] = None
    confidence: float = 1.0
    evidence_refs: List[Any] = Field(default_factory=list)
    dataset_refs: List[str] = Field(default_factory=list)
    license_scope: Optional[str] = None
    allowed_use: List[str] = Field(default_factory=list)
    supersedes: List[str] = Field(default_factory=list)
    contradicts: List[str] = Field(default_factory=list)
    expires_at: Optional[str] = None
    trace_id: Optional[str] = None
    auto_deliver: bool = True
    actor_id: str = "operator"
    idempotency_key: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalInfluenceBody(BaseModel):
    task_id: Optional[str] = None
    persona_id: str = "persona-tw-equity"
    query_snapshot: Dict[str, Any] = Field(default_factory=dict)
    selected_memory_refs: List[str] = Field(default_factory=list)
    selected_evidence_refs: List[str] = Field(default_factory=list)
    counter_evidence_query: Optional[str] = None
    counter_evidence_results: List[Dict[str, Any]] = Field(default_factory=list)
    influence_assessment: str = ""
    influence_weight: Optional[float] = None
    influence_state: str = "influence_unknown"
    model_ranker_version: str = "v1.0"
    resulting_seed_ref: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Pantheon Research Orchestrator Service", version="0.1.0")


async def _drain_memory_outbox() -> None:
    interval = max(1, float(os.getenv("RESEARCH_MEMORY_POLL_SECONDS", "30")))
    while True:
        try:
            result = await asyncio.to_thread(MemoryWritebackWorker().drain)
            if result["total_processed"]:
                logger.info("Research memory deliveries: %s", result)
        except Exception:
            logger.exception("Research memory delivery tick failed")
        await asyncio.sleep(interval)


@app.on_event("startup")
async def start_memory_writeback_worker() -> None:
    if os.getenv("RESEARCH_MEMORY_WORKER_ENABLED", "false").lower() == "true":
        app.state.memory_writeback_task = asyncio.create_task(_drain_memory_outbox())


@app.on_event("shutdown")
async def stop_memory_writeback_worker() -> None:
    task = getattr(app.state, "memory_writeback_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _memory_sponsor(run: Dict[str, Any], requested: Optional[str] = None) -> str:
    """Resolve attribution from the owning demand/member carried by the run/task."""
    task = store.get_task(run["task_id"]) or {}
    params = run.get("parameters") or {}
    contexts = [run, params, params.get("metadata") or {}, task.get("constraints") or {}]
    exp = params.get("experiment_run") or {}
    contexts.extend([exp, exp.get("metadata") or {}])
    return resolve_memory_sponsor(*contexts, requested=requested)


store = build_research_orchestrator_store(DATA_DIR)
def get_store() -> ResearchOrchestratorStore:
    return store


_write_owner: Optional[Any] = None
_stage_workers_lock = threading.Lock()
_plan_progress_lock = threading.RLock()
_active_stage_workers: set[str] = set()
_stage_execution_lock = threading.RLock()
_stage_execution_cond = threading.Condition(_stage_execution_lock)


def _cancel_stage_claims(run_ids: Any, timestamp: str) -> None:
    exec_path = store.data_dir / "stage_executions.json"
    if exec_path.exists():
        with store._lock:
            claims, ch = store._read_map(exec_path), False
            for k, v in claims.items():
                if k.startswith("agora-stage-claim:") and isinstance(v, dict) and v.get("status") == "in_progress" and str(v.get("run_id") or "") in run_ids:
                    v["status"], v["updated_at"] = "canceled", timestamp
                    ch = True
            if ch:
                store._write_map(exec_path, claims)
    with _stage_execution_cond:
        _stage_execution_cond.notify_all()


def _format_run_completion_result(run_record: Dict[str, Any], stage_type: str) -> Dict[str, Any]:
    rcpt, arts = run_record.get("receipt") or {}, run_record.get("artifact_refs") or []
    first = arts[0] if arts else {}
    art_id = first.get("artifact_id") or ""
    digest = rcpt.get("artifact_digest") or first.get("digest") or ""
    run_id = str(run_record.get("run_id") or run_record.get("id") or "")
    return {
        "status": "succeeded", "outcome": "succeeded",
        "provenance": run_record.get("provenance") or rcpt.get("mode") or "real",
        "backend_reference": rcpt.get("backend_reference") or f"research-orchestrator://stages/{stage_type}/{run_id}",
        "artifact_id": art_id, "artifact_digest": digest, "artifact_refs": arts, "artifacts": arts,
        "checksums": {art_id: digest, f"artifact://{art_id}": digest} if art_id else {},
        "metrics": run_record.get("metrics") or [], "receipt": rcpt,
    }


def _sync_run_and_return_cached(run_id: str, cached_result: Dict[str, Any]) -> Dict[str, Any]:
    run_rec = store.get_run(run_id)
    if run_rec and isinstance(run_rec, dict):
        status_str = str(run_rec.get("status") or "").lower()
        if status_str in {"canceled", "cancelled", "rejected"}:
            raise HTTPException(status_code=409, detail=f"Research run '{run_id}' is in terminal status '{status_str}' and cannot return cached execution")
        if run_rec.get("status") != "completed":
            rcpt = cached_result.get("receipt") or {}
            run_rec.update({"status": "completed", "completed_at": rcpt.get("completed_at") or utc_now(), "metrics": cached_result.get("metrics") or [], "provenance": cached_result.get("provenance") or "real", "receipt": rcpt, "artifact_refs": cached_result.get("artifact_refs") or []})
            store.put_run(run_rec)
    return cached_result



@app.on_event("startup")
def resume_queued_plan_stages() -> None:
    """Resume durable queued stage work after an owner process restart."""
    exec_path = store.data_dir / "stage_executions.json"
    if exec_path.exists():
        with store._lock:
            claims, ch = store._read_map(exec_path), False
            for k, c in list(claims.items()):
                if k.startswith("agora-stage-claim:") and isinstance(c, dict) and c.get("status") == "in_progress":
                    r = store.get_run(str(c.get("run_id") or "")) if c.get("run_id") else None
                    if r and str(r.get("status") or "").lower() == "completed" and r.get("receipt"):
                        c["status"], c["updated_at"] = "succeeded", utc_now()
                    else:
                        claims.pop(k, None)
                    ch = True
            if ch:
                store._write_map(exec_path, claims)
    grouped: Dict[str, Dict[str, Any]] = {}
    for record in store.list_runs():
        params = record.get("parameters") or {}
        plan = params.get("plan")
        stage = params.get("stage")
        status = str(record.get("status") or "").lower()
        if status not in {"queued", "running", "completed", "succeeded"} or not isinstance(plan, dict) or not isinstance(stage, dict):
            continue
        if status in {"queued", "running"} and record.get("cancellation_fence"):
            record["status"] = "canceled"
            store.put_run(record)
            continue
        task = store.get_task(str(record.get("task_id") or "")) if record.get("task_id") else None
        if (task and (str(task.get("status") or "").lower() in {"canceled", "cancelled"} or task.get("cancellation_fence"))) or str(plan.get("status") or "").lower() in {"canceled", "cancelled"}:
            if status in {"queued", "running"}:
                record.update({"status": "canceled", "cancellation_fence": record.get("cancellation_fence") or utc_now()})
                store.put_run(record)
            continue
        if status == "running":
            record.update({"status": "queued", "updated_at": utc_now()})
            store.put_run(record)
        grouped[str(record.get("task_id") or "")] = (plan, record)
    for plan, record in grouped.values():
        _progress_plan_stages(plan, record, record.get("created_by"), store, str(record.get("updated_at") or utc_now()))


def get_write_owner() -> Any:
    global _write_owner
    if _write_owner is not None:
        return _write_owner
    try:
        from services.research.write_owner import build_research_write_owner

        _write_owner = build_research_write_owner()
        return _write_owner
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Research write owner unavailable: {exc}",
        ) from exc


def set_write_owner(owner: Optional[Any]) -> None:
    global _write_owner
    _write_owner = owner
alpha_replication_admission_store = ReplicationAdmissionStore(ALPHA_REPLICATION_DATA_DIR)
alpha_replication_queue = AlphaReplicationQueue(ALPHA_REPLICATION_DATA_DIR)
register_fastapi_health_routes(
    app,
    "research-orchestrator",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {
        "run_count": len(store.list_runs()),
        "active_run_count": len([run for run in store.list_runs() if str(run.get("status") or "").lower() in ACTIVE_STATUSES]),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_backend": STORE_BACKEND,
        "max_active_runs": MAX_ACTIVE_RUNS,
        "production_adapters_enabled": PRODUCTION_ADAPTERS_ALLOWED,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _first_text(*values: Any) -> Optional[str]:
    value = _first_value(*values)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_writeback_text(value: Optional[str], field_name: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail=f"{field_name} is required for registry writeback")
    return value


def _registry_hints(artifact: Dict[str, Any]) -> Dict[str, Any]:
    hints = artifact.get("registry_hints")
    if isinstance(hints, dict):
        return dict(hints)
    projection = artifact.get("registry_projection")
    return dict(projection) if isinstance(projection, dict) else {}


def _input_ref_id(run: Dict[str, Any], *types: str) -> Optional[str]:
    requested = {item.lower() for item in types}
    for ref in run.get("input_refs") or []:
        if not isinstance(ref, dict):
            continue
        ref_type = str(ref.get("type") or "").lower()
        if ref_type in requested:
            return _first_text(ref.get("id"), ref.get("ref"), ref.get("uri"))
    return None


def _resolve_writeback_artifact(run: Dict[str, Any], artifact_id: Optional[str]) -> Dict[str, Any]:
    target_id = artifact_id
    refs = [ref for ref in run.get("artifact_refs") or [] if isinstance(ref, dict)]
    if not target_id and len(refs) == 1:
        target_id = _first_text(refs[0].get("artifact_id"), refs[0].get("id"))
    if not target_id:
        raise HTTPException(status_code=400, detail="artifact_id is required when a run has zero or multiple artifacts")
    artifact = store.get_artifact(target_id)
    if not artifact or artifact.get("run_id") != run.get("run_id"):
        raise HTTPException(status_code=404, detail="research artifact not found for run")
    return artifact


def _checksum_status(checksum: Any) -> str:
    text = str(checksum or "").strip()
    if not text:
        return "missing"
    return "valid" if _SHA256_RE.fullmatch(text) else "invalid"


def _storage_status(storage_ref: Any) -> str:
    if isinstance(storage_ref, dict):
        backend = str(storage_ref.get("backend") or "").strip()
        path = str(storage_ref.get("path") or "").strip()
        return "resolvable" if backend and path else "missing"
    text = str(storage_ref or "").strip()
    if not text:
        return "missing"
    if text.startswith(("http://", "https://")):
        return "external"
    if text.startswith(_RESOLVABLE_STORAGE_SCHEMES):
        return "resolvable"
    return "unverified"


def _producer_mode(run: Dict[str, Any]) -> str:
    adapter = str(run.get("adapter") or "").strip().lower()
    requested_mode = str(run.get("requested_mode") or "").strip().lower()
    dispatch_mode = str(run.get("dispatch_mode") or "").strip().lower()
    if dispatch_mode == "offline" or requested_mode == "offline":
        return "offline"
    if adapter in STUB_ADAPTERS:
        return adapter
    if requested_mode in PRODUCTION_MODES or adapter in PRODUCTION_ADAPTERS:
        return "production"
    return dispatch_mode or requested_mode or adapter or "unknown"


def _artifact_origin(producer_mode: str) -> str:
    return {
        "stub": "dev_stub",
        "handoff_only": "manual_handoff",
        "manual": "manual_handoff",
        "offline": "offline_worker_output",
        "production": "production_adapter",
    }.get(producer_mode, "research_orchestrator_handoff")


def _evidence_source_refs(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                candidate = _first_text(
                    item.get("ref_id"),
                    item.get("evidence_item_id"),
                    item.get("evidence_bundle_id"),
                    item.get("source_ref"),
                    item.get("id"),
                )
            else:
                candidate = _first_text(item)
            if candidate and candidate not in refs:
                refs.append(candidate)
    return refs


def _artifact_quality(run: Dict[str, Any], body: ArtifactBody) -> Dict[str, Any]:
    metadata = _as_mapping(body.metadata)
    registry_hints = _as_mapping(body.registry_hints)
    producer_mode = _producer_mode(run)
    checksum_status = _checksum_status(body.checksum)
    storage_status = _storage_status(body.storage_ref)
    source_evidence_refs = _evidence_source_refs(
        metadata.get("source_evidence_refs"),
        metadata.get("evidence_refs"),
        registry_hints.get("source_evidence_refs"),
        registry_hints.get("evidence_refs"),
    )
    reasons: list[str] = []
    if producer_mode in STUB_ADAPTERS:
        reasons.append("producer_mode_not_evidence_grade")
    if checksum_status != "valid":
        reasons.append(f"checksum_{checksum_status}")
    if storage_status not in {"resolvable", "external"}:
        reasons.append(f"storage_{storage_status}")
    if not source_evidence_refs:
        reasons.append("missing_source_evidence_refs")
    return {
        "producer_mode": producer_mode,
        "artifact_origin": _artifact_origin(producer_mode),
        "storage_status": storage_status,
        "checksum_status": checksum_status,
        "source_evidence_refs": source_evidence_refs,
        "evidence_eligible": not reasons,
        "evidence_ineligibility_reasons": reasons,
    }


def _writeback_target_quality(
    run: Dict[str, Any],
    artifact: Dict[str, Any],
    body: RegistryWritebackBody,
) -> Dict[str, Any]:
    hints = _registry_hints(artifact)
    artifact_metadata = _as_mapping(artifact.get("metadata"))
    quality = _as_mapping(artifact.get("quality"))
    source_evidence_refs = _evidence_source_refs(
        quality.get("source_evidence_refs"),
        artifact.get("source_evidence_refs"),
        artifact_metadata.get("source_evidence_refs"),
        artifact_metadata.get("evidence_refs"),
        hints.get("source_evidence_refs"),
        hints.get("evidence_refs"),
        body.metadata.get("source_evidence_refs"),
        body.metadata.get("evidence_refs"),
    )
    source_strategy_spec_id = _first_text(
        body.source_strategy_spec_id,
        hints.get("source_strategy_spec_id"),
        hints.get("strategy_spec_id"),
        artifact.get("source_strategy_spec_id"),
        artifact_metadata.get("source_strategy_spec_id"),
    )
    source_dataset_refs = _evidence_source_refs(
        body.source_dataset_refs,
        hints.get("source_dataset_refs"),
        artifact.get("source_dataset_refs"),
        _input_ref_id(run, "dataset", "dataset_version"),
    )
    return {
        "producer_mode": quality.get("producer_mode") or _producer_mode(run),
        "storage_status": _storage_status(_first_value(body.storage_ref, hints.get("storage_ref"), artifact.get("storage_ref"))),
        "checksum_status": _checksum_status(_first_text(body.checksum, hints.get("checksum"), artifact.get("checksum"))),
        "source_strategy_spec_id": source_strategy_spec_id,
        "source_dataset_refs": source_dataset_refs,
        "source_evidence_refs": source_evidence_refs,
        "artifact_evidence_eligible": bool(artifact.get("evidence_eligible")),
    }


def _assert_registry_writeback_eligible(
    run: Dict[str, Any],
    artifact: Dict[str, Any],
    body: RegistryWritebackBody,
) -> Dict[str, Any]:
    quality = _writeback_target_quality(run, artifact, body)
    requested_state = str(_first_text(body.requested_artifact_state, _registry_hints(artifact).get("artifact_state"), "candidate")).lower()
    reasons: list[str] = []
    if quality["checksum_status"] != "valid":
        reasons.append(f"checksum_{quality['checksum_status']}")
    if quality["storage_status"] not in {"resolvable", "external"}:
        reasons.append(f"storage_{quality['storage_status']}")
    if not quality["source_strategy_spec_id"]:
        reasons.append("missing_source_strategy_spec_id")
    if not quality["source_dataset_refs"]:
        reasons.append("missing_source_dataset_refs")
    if requested_state == "candidate":
        if quality["producer_mode"] in STUB_ADAPTERS:
            reasons.append("producer_mode_not_candidate_grade")
        if not quality["source_evidence_refs"]:
            reasons.append("missing_source_evidence_refs")
        if not quality["artifact_evidence_eligible"]:
            reasons.append("artifact_not_evidence_eligible")
    if reasons:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "registry_writeback_not_eligible",
                "reasons": reasons,
                "quality": quality,
            },
        )
    return quality


def _experiment_run_for_writeback(
    run: Dict[str, Any],
    artifact: Dict[str, Any],
    body: RegistryWritebackBody,
    timestamp: str,
) -> ExperimentRun:
    hints = _registry_hints(artifact)
    params = _as_mapping(run.get("parameters"))
    artifact_metadata = _as_mapping(artifact.get("metadata"))
    strategy_id = _required_writeback_text(
        _first_text(body.strategy_id, hints.get("strategy_id"), artifact.get("strategy_id"), run.get("strategy_id"), params.get("strategy_id")),
        "strategy_id",
    )
    version = _required_writeback_text(
        _first_text(body.version, hints.get("version"), artifact.get("version"), params.get("version"), params.get("strategy_spec_version")),
        "version",
    )
    dataset_version_id = _required_writeback_text(
        _first_text(
            body.dataset_version_id,
            params.get("dataset_version_id"),
            params.get("dataset_ref"),
            artifact_metadata.get("dataset_version_id"),
            _input_ref_id(run, "dataset", "dataset_version"),
            body.source_dataset_refs[0] if body.source_dataset_refs else None,
        ),
        "dataset_version_id",
    )
    code_version = _required_writeback_text(
        _first_text(body.code_version, params.get("code_version"), artifact_metadata.get("code_version"), run.get("code_version")),
        "code_version",
    )
    output_manifest_ref = _required_writeback_text(
        _first_text(body.output_manifest_ref, run.get("output_manifest_ref"), artifact.get("storage_ref")),
        "output_manifest_ref",
    )
    strategy_spec_version = _required_writeback_text(
        _first_text(body.strategy_spec_version, hints.get("strategy_spec_version"), params.get("strategy_spec_version"), version),
        "strategy_spec_version",
    )
    return ExperimentRun(
        run_id=str(run["run_id"]),
        task_id=str(run["task_id"]),
        strategy_id=strategy_id,
        strategy_spec_version=strategy_spec_version,
        backend_id=str(_first_text(run.get("adapter"), params.get("backend_id"), "research-orchestrator")),
        runtime_env=body.runtime_env,
        status=str(run.get("status") or ""),
        started_at=str(_first_text(run.get("started_at"), run.get("created_at"), timestamp)),
        finished_at=str(_first_text(run.get("finished_at"), run.get("updated_at"), timestamp)),
        dataset_version_id=dataset_version_id,
        code_version=code_version,
        input_manifest_ref=str(_first_text(body.input_manifest_ref, run.get("input_manifest_ref"), f"research-run://{run['run_id']}/input")),
        output_manifest_ref=output_manifest_ref,
        metric_bundle_id=_first_text(body.metric_bundle_id, artifact_metadata.get("metric_bundle_id")),
        artifact_refs=[str(artifact["artifact_id"])],
        logs_ref=_first_text(run.get("logs_ref")),
        trace_id=str(_first_text(run.get("trace_id"), run.get("run_id"))),
        created_at=str(_first_text(run.get("created_at"), timestamp)),
        updated_at=str(_first_text(run.get("updated_at"), timestamp)),
        metadata={"source_strategy_spec_id": body.source_strategy_spec_id, "research_orchestrator_run_record": True},
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
            "registry_writes": "completed_run_draft_candidate_writeback_only",
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


@app.post("/api/research-orchestrator/intake/imitation-candidate", status_code=201)
def intake_imitation_candidate_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Intake an imitation candidate from policy-learning into Research as ExperimentTask and ExperimentRun."""
    try:
        receipt = intake_imitation_candidate(payload, store=store)
        return receipt.to_dict()
    except ExperimentCandidateIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/research-orchestrator/alpha-replication/admissions", status_code=201)
def create_alpha_replication_admission(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Admit a reviewed StrategySpec into the existing Alpha Replication controller.

    Delegates to the existing ReplicationAdmissionStore; the controller already
    reads admitted (tenant_id, strategy_spec_id) pairs from that store, so no
    second queue is introduced here. Replaying an identical, already-admitted
    payload returns the original admission instead of creating a duplicate.
    """
    try:
        return alpha_replication_admission_store.create_admission(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/research-orchestrator/alpha-replication/admissions/{tenant_id}/{strategy_spec_id}")
def get_alpha_replication_admission(tenant_id: str, strategy_spec_id: str) -> Dict[str, Any]:
    """Read back the exact admitted ReplicationAdmission identity, if any."""
    admission = alpha_replication_admission_store.get_admission(tenant_id, strategy_spec_id)
    if admission is None:
        raise HTTPException(status_code=404, detail="alpha replication admission not found")
    return admission


_ALPHA_REPLICATION_READ_ROLES = tuple(
    dict.fromkeys(("viewer", "view_only", "operator", "approver", "admin", "reviewer", *AUTHENTICATED_SERVICE_ROLES))
)


def _structured_token_tenants(authorization: Optional[str]) -> set[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return set()
    token = authorization.split(" ", 1)[1].strip()
    if not token or "." in token or ":" not in token:
        return set()
    parts = token.split(":")
    raw_tenants = ""
    if len(parts) == 3 and parts[2].strip().lower() != "mfa":
        raw_tenants = parts[2].strip()
    elif len(parts) >= 5 and parts[2].strip().lower() == "mfa":
        raw_tenants = parts[4].strip()
    if not raw_tenants:
        return set()
    return {t.strip() for t in raw_tenants.split(",") if t.strip()}


def _authenticated_alpha_replication_actor(
    authorization: Optional[str],
    x_tenant_id: Optional[str],
) -> AuthenticatedTenant:
    """Verified bearer identity bound to an explicit tenant; anonymous or cross-tenant is denied."""
    try:
        verified = authenticate_tenant(
            authorization=authorization,
            tenant_id=x_tenant_id,
            service_prefix="RESEARCH",
            required_roles=_ALPHA_REPLICATION_READ_ROLES,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except TenantBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    structured_tenants = _structured_token_tenants(authorization)
    if structured_tenants and "*" not in structured_tenants and verified.tenant_id not in structured_tenants:
        raise HTTPException(
            status_code=403,
            detail=f"Authenticated actor is not authorized for tenant {verified.tenant_id!r}.",
        )
    return verified


def _authenticated_alpha_replication_tenant(
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
) -> str:
    return _authenticated_alpha_replication_actor(authorization, x_tenant_id).tenant_id


def _authenticated_persona_demand_writer_tenant(
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
) -> str:
    actor = _authenticated_alpha_replication_actor(authorization, x_tenant_id)
    if actor.actor_id != "persona-pool-cycle":
        raise HTTPException(status_code=403, detail="Persona demand writes require the persona-pool-cycle principal.")
    return actor.tenant_id


def _build_alpha_replication_entry(
    admission: Dict[str, Any],
    queue_entry: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    q = queue_entry if isinstance(queue_entry, dict) else {}
    has_q = isinstance(queue_entry, dict)
    adm_status = admission.get("status")
    q_status = q.get("status") if has_q else None
    raw_exp_runs = q.get("experiment_run_ids") if has_q else None
    exp_run_ids = list(raw_exp_runs) if isinstance(raw_exp_runs, list) else None
    raw_auth_runs = q.get("authority_run_ids") if has_q else None
    auth_run_ids = list(raw_auth_runs) if isinstance(raw_auth_runs, list) else None
    return {
        "admission_id": admission.get("admission_id"),
        "tenant_id": admission.get("tenant_id"),
        "strategy_spec_id": admission.get("strategy_spec_id"),
        "strategy_id": admission.get("strategy_id") or (q.get("strategy_id") if has_q else None),
        "spec_version": admission.get("spec_version") or (q.get("spec_version") if has_q else None),
        "checksum": admission.get("checksum") or (q.get("checksum") if has_q else None),
        "approval_decision_id": admission.get("approval_decision_id") or (q.get("approval_decision_id") if has_q else None),
        "approver": admission.get("approver") if admission.get("approver") is not None else (q.get("approver") if has_q else None),
        "approved_at": admission.get("approved_at") if admission.get("approved_at") is not None else (q.get("approved_at") if has_q else None),
        "requested_by": admission.get("requested_by"),
        "review_ref": admission.get("review_ref"),
        "mode": admission.get("mode"),
        "created_at": admission.get("created_at"),
        "admission_status": adm_status,
        "status": adm_status,
        "queue_status": q_status,
        "enqueued_at": q.get("enqueued_at") if has_q else None,
        "enqueued_by": q.get("enqueued_by") if has_q else None,
        "attempt_count": q.get("attempt_count") if has_q else None,
        "max_attempts": q.get("max_attempts") if has_q else None,
        "experiment_task_id": q.get("experiment_task_id") if has_q else None,
        "experiment_run_ids": exp_run_ids,
        "authority_task_id": q.get("authority_task_id") if has_q else None,
        "authority_run_ids": auth_run_ids,
        "last_revalidation_at": q.get("last_revalidation_at") if has_q else None,
        "last_error": q.get("failure_reason") if has_q else None,
        "replay_count": q.get("replay_count") if has_q else None,
        "last_replayed_at": q.get("last_replayed_at") if has_q else None,
        "revalidation_count": q.get("revalidation_count") if has_q else None,
    }


@app.get("/api/research-orchestrator/alpha-replication/entries")
def list_alpha_replication_entries(
    tenant_id: str = Depends(_authenticated_alpha_replication_tenant),
) -> List[Dict[str, Any]]:
    """Return every replication admission of the authenticated tenant joined with its queue entry."""
    admissions = alpha_replication_admission_store.list_admissions(tenant_id=tenant_id)
    queue_store = (
        alpha_replication_queue
        if getattr(alpha_replication_queue, "_data_dir", None)
        == getattr(alpha_replication_admission_store, "_data_dir", None)
        else AlphaReplicationQueue(alpha_replication_admission_store._data_dir)
    )
    queue_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    for entry in queue_store.list_all():
        if not isinstance(entry, dict):
            continue
        entry_tenant = str(entry.get("tenant_id") or "").strip()
        entry_spec_id = str(entry.get("strategy_spec_id") or "").strip()
        if entry_tenant == tenant_id and entry_spec_id:
            queue_by_key[(entry_tenant, entry_spec_id)] = entry

    results: List[Dict[str, Any]] = []
    for admission in admissions:
        if not isinstance(admission, dict):
            continue
        adm_tenant = str(admission.get("tenant_id") or "").strip()
        if adm_tenant != tenant_id:
            continue
        spec_id = str(admission.get("strategy_spec_id") or "").strip()
        queue_entry = queue_by_key.get((adm_tenant, spec_id))
        results.append(_build_alpha_replication_entry(admission, queue_entry))
    return results


@app.get("/api/research-orchestrator/tasks")
def list_tasks(status: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    _expire_collector_query_plans()
    tasks = store.list_tasks() + query_plan_store.list_tasks()
    return [task for task in tasks if str(task.get("status") or "").lower() == status.lower()] if status else tasks


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
        "generation": 1,
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
    _expire_collector_query_plans()
    task = store.get_task(task_id) or query_plan_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="research task not found")
    return task


@app.post("/api/research-orchestrator/tasks/{task_id}/cancel")
def cancel_task(task_id: str, body: Optional[CancelRunBody] = None) -> Dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="research task not found")
    b = body or CancelRunBody()
    timestamp = b.canceled_at or utc_now()
    with _plan_progress_lock:
        task.update({"status": "canceled", "cancellation_fence": timestamp, "updated_at": timestamp})
        store.put_task(task)
        canceled_run_ids = set()
        for r in store.list_runs():
            if str(r.get("task_id")) == task_id and str(r.get("status") or "").lower() in ACTIVE_STATUSES:
                r.update({"status": "canceled", "cancellation_fence": timestamp, "completed_at": timestamp, "updated_at": timestamp})
                store.put_run(r)
                canceled_run_ids.add(str(r.get("run_id") or ""))
        _cancel_stage_claims(canceled_run_ids, timestamp)
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
    supported_stage_backends = {"vectorbt", "statsmodels", "quantlib", "openclaw_result_synthesis", "prototype_backtest", "econometric_validation", "derivatives_pricing_risk", "evidence_synthesis"}
    is_stage_backend = adapter in supported_stage_backends
    if is_stage_backend:
        backend_name = ALLOWLISTED_STAGE_BACKENDS.get(adapter, adapter)
        if (
            os.getenv(f"AGORA_RESEARCH_{backend_name.upper()}_UNAVAILABLE") == "1"
            or os.getenv(f"AGORA_RESEARCH_{adapter.upper()}_UNAVAILABLE") == "1"
            or (backend_name == "openclaw_result_synthesis" and os.getenv("PANTHEON_OPENCLAW_UNAVAILABLE") == "1")
        ):
            raise HTTPException(
                status_code=503,
                detail=f"Backend execution owner for adapter '{adapter}' ({backend_name}) is currently unavailable",
            )
        if backend_name not in {"vectorbt", "statsmodels", "quantlib", "openclaw_result_synthesis"} and requested_mode in ("real", "simulation"):
            raise HTTPException(
                status_code=503,
                detail=f"Backend execution owner for adapter '{adapter}' ({backend_name}) is absent or not configured",
            )
        if requested_mode == "real" or dispatch_mode == "real":
            env_var = "PANTHEON_OPENCLAW_BACKEND" if backend_name == "openclaw_result_synthesis" else f"PANTHEON_{backend_name.upper()}_BACKEND"
            if os.getenv(env_var, "stub").lower() != "real":
                raise HTTPException(
                    status_code=503,
                    detail=f"Backend execution owner for adapter '{adapter}' ({backend_name}) is unavailable in real mode ({env_var}!=real)",
                )

    def _reject(reason: str, detail: str) -> None:
        nonlocal rejected, rejection
        rejected = True
        rejection = {
            "reason": reason,
            "detail": detail,
            "rejected_at": timestamp,
            "rejected_by": "research-orchestrator-service",
        }

    if any(token in request_text for token in ("registry_write", "direct_registry_write", "promote_to_registry")):
        _reject(
            "registry_write_disabled",
            "Research orchestrator may emit draft handoff records only; canonical registry writes are not allowed.",
        )
    elif any(token in request_text for token in ("governance_write", "governance_stage", "approve_governance")):
        _reject(
            "governance_write_disabled",
            "Research orchestrator cannot approve governance decisions or change deployment stages.",
        )
    elif adapter not in STUB_ADAPTERS and adapter not in CAPABILITY_REGISTRY and not is_stage_backend:
        _reject(
            "unknown_adapter",
            f"Adapter family '{adapter}' is not registered for research orchestration.",
        )
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline" and dispatch_mode == "offline":
        # Offline gate path: route to gateway and record the dispatch.
        pass  # Handled below after run_id is assigned.
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode not in PRODUCTION_MODES:
        _reject(
            "offline_mode_required",
            "Offline-gated adapter dispatch requires requested_mode=offline and dispatch_mode=offline.",
        )
    elif (
        adapter in PRODUCTION_ADAPTERS
        or requested_mode in PRODUCTION_MODES
        or dispatch_mode in PRODUCTION_MODES
        or requested_mode in ("live", "canary")
        or dispatch_mode in ("live", "canary")
    ):
        _reject(
            "production_adapter_disabled",
            "Research orchestrator production adapters and paper/canary/live modes are fail-closed in this service boundary.",
        )
    elif is_stage_backend and (requested_mode not in ALLOWED_STAGE_MODES or dispatch_mode not in ALLOWED_STAGE_MODES):
        _reject(
            "dispatch_mode_disabled",
            f"Stage execution adapter '{adapter}' only supports allowed non-live modes: {sorted(ALLOWED_STAGE_MODES)}.",
        )
    if not rejected and not is_stage_backend and dispatch_mode not in STUB_ADAPTERS and not (OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline" and dispatch_mode == "offline"):
        _reject(
            "dispatch_mode_disabled",
            "Only stub/handoff-only research orchestration is enabled.",
        )

    stage_param = body.parameters.get("stage") if isinstance(body.parameters.get("stage"), dict) else {}
    stage_ds_refs = _extract_dataset_refs(
        (body.input_refs or []) + (stage_param.get("input_refs") or []) + (stage_param.get("approved_input_refs") or [])
    )
    expected_tid = str(body.parameters.get("tenant_id") or getattr(body, "tenant_id", None) or task.get("tenant_id") or "").strip()
    ds_val = body.parameters.get("dataset") or stage_param.get("dataset")
    if is_stage_backend and stage_ds_refs and not rejected:
        if not _matches_dataset_ref(ds_val, stage_ds_refs):
            _reject(
                "dataset_unavailable",
                f"Referenced governed research dataset '{stage_ds_refs}' is unavailable or mismatched",
            )
        elif ds_val and isinstance(ds_val, dict) and expected_tid:
            ds_tid = str(ds_val.get("tenant_id") or "").strip()
            if ds_tid and ds_tid != expected_tid:
                _reject(
                    "tenant_boundary_violation",
                    f"Unauthorized access to dataset across tenant boundary: '{ds_tid}' != '{expected_tid}'",
                )

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

    is_retrain_dispatch = not rejected and _is_retrain_action(body.parameters)
    if is_retrain_dispatch:
        # The executor runs real Training evaluations; the run is labelled by that
        # work, not by the payload's default stub identity.
        if _evolution_action(body.parameters) == "retrain":
            adapter, requested_mode, dispatch_mode = _RETRAIN_ADAPTER, _RETRAIN_MODE, _RETRAIN_MODE
        else:
            adapter, requested_mode, dispatch_mode = _EVOLUTION_GATE_ADAPTER, _EVOLUTION_GATE_MODE, _EVOLUTION_GATE_MODE

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
        if is_retrain_dispatch:
            summary = (
                "Retrain run queued for Training evaluation of the baseline and the candidate."
                if adapter == _RETRAIN_ADAPTER
                else f"{_evolution_action(body.parameters).capitalize()} run queued for the out-of-sample replication gate."
            )
        elif is_stage_backend:
            summary = f"Stage execution adapter '{adapter}' queued for authentic dispatch."
        else:
            summary = "Stub research orchestration run queued for bounded dispatch."
        events.append(_event(timestamp, "run_queued", summary, body.actor_id, run_id, events))
    stage_id_val = None
    if body.parameters.get("stage") and isinstance(body.parameters["stage"], dict):
        stage_id_val = body.parameters["stage"].get("stage_id")
    if not stage_id_val:
        for ref in body.input_refs:
            if isinstance(ref, dict) and ref.get("type") == "stage" and ref.get("id"):
                stage_id_val = str(ref["id"])
                break

    initial_artifact_refs: List[Dict[str, Any]] = []
    if (
        isinstance(body.parameters.get("experiment_run"), dict)
        and isinstance(body.parameters["experiment_run"].get("artifact_refs"), list)
    ):
        for ref in body.parameters["experiment_run"]["artifact_refs"]:
            if isinstance(ref, str) and ref.strip():
                initial_artifact_refs.append(
                    {"artifact_type": "backtest_run", "uri": ref.strip()}
                )
            elif isinstance(ref, dict):
                initial_artifact_refs.append(dict(ref))

    run: Dict[str, Any] = {
        "id": run_id, "run_id": run_id, "task_id": task_id, "stage_id": stage_id_val,
        "attempt_number": 1, "parent_run_id": None, "root_run_id": run_id,
        "adapter": adapter, "requested_mode": requested_mode, "dispatch_mode": dispatch_mode,
        "status": status, "production_activation": "disabled",
        "input_refs": body.input_refs, "parameters": body.parameters, "created_by": body.actor_id,
        "tenant_id": body.parameters.get("tenant_id") or getattr(body, "tenant_id", None) or task.get("tenant_id"),
        "user_id": body.parameters.get("user_id") or body.actor_id or getattr(body, "user_id", None) or task.get("user_id"),
        "created_at": timestamp, "updated_at": timestamp, "idempotency_key": body.idempotency_key,
        "rejection": rejection, "events": events, "artifact_refs": initial_artifact_refs,
        "proposal_refs": [], "registry_writebacks": [],
    }
    if gateway_ref is not None:
        run["gateway_ref"] = gateway_ref
    task["status"] = "rejected" if rejected else "running"
    task["updated_at"] = timestamp
    store.put_task(task)
    store.put_run(run)
    for event in events:
        store.append_event(event)
    if (
        not rejected
        and _is_retrain_action(body.parameters)
    ):
        _trigger_retrain_execution(run["run_id"], body.parameters)
    if not rejected and is_stage_backend and (body.parameters.get("stage") or (body.parameters.get("plan") and body.parameters["plan"].get("stages"))):
        _progress_plan_stages(body.parameters.get("plan") or {}, run, body.actor_id, store, timestamp)
    return run


def _progress_plan_stages(
    plan_payload: Dict[str, Any],
    parent_run: Dict[str, Any],
    actor_id: Optional[str],
    store: ResearchOrchestratorStore,
    timestamp: str,
) -> None:
    """Serialize stage reconciliation so concurrent completions cannot enqueue duplicates."""
    with _plan_progress_lock:
        _progress_plan_stages_locked(plan_payload, parent_run, actor_id, store, timestamp)


def _progress_plan_stages_locked(
    plan_payload: Dict[str, Any],
    parent_run: Dict[str, Any],
    actor_id: Optional[str],
    store: ResearchOrchestratorStore,
    timestamp: str,
) -> None:
    """Durably queue every newly ready stage and execute queued attempts off-request."""
    task_id = str(parent_run.get("task_id") or "")
    if (
        str(parent_run.get("status") or "").lower() in {"canceled", "cancelled"}
        or parent_run.get("cancellation_fence")
        or str(plan_payload.get("status") or "").lower() in {"canceled", "cancelled"}
    ):
        return
    task = store.get_task(task_id) if task_id else None
    if task and (str(task.get("status") or "").lower() in {"canceled", "cancelled"} or task.get("cancellation_fence")):
        return

    stages = plan_payload.get("stages") or []
    if not stages and parent_run.get("parameters", {}).get("stage"):
        stages = [parent_run["parameters"]["stage"]]
        plan_payload["stages"] = stages
    if not isinstance(stages, list) or not stages:
        return

    records = [r for r in store.list_runs() if str(r.get("task_id")) == task_id and r.get("stage_id")]
    latest: Dict[str, Dict[str, Any]] = {}
    for record in records:
        stage_id = str(record["stage_id"])
        old = latest.get(stage_id)
        rank = (int(record.get("attempt_number") or 1), str(record.get("created_at") or ""), str(record.get("run_id") or ""))
        old_rank = (int(old.get("attempt_number") or 1), str(old.get("created_at") or ""), str(old.get("run_id") or "")) if old else None
        if old is None or rank > old_rank:
            latest[stage_id] = record

    states = {key: str(value.get("status") or "").lower() for key, value in latest.items()}
    for stage in stages:
        if not isinstance(stage, dict) or not stage.get("stage_id"):
            continue
        stage_id = str(stage["stage_id"])
        if stage_id in latest:
            continue
        stage_status = str(stage.get("status") or "").lower()
        if stage_status in {"completed", "succeeded", "failed", "rejected", "canceled", "cancelled"}:
            canonical_status = "completed" if stage_status in {"completed", "succeeded"} else stage_status
            legacy_run_id = stage.get("run_id") or stage.get("latest_run_id") or stage.get("id")
            latest[stage_id] = {
                "stage_id": stage_id,
                "status": canonical_status,
                "run_id": legacy_run_id,
                "id": legacy_run_id,
                "attempt_number": 1,
            }
            states[stage_id] = canonical_status

    for stage in stages:
        if not isinstance(stage, dict) or not stage.get("stage_id"):
            continue
        stage_id = str(stage["stage_id"])
        if stage_id in latest:
            continue
        deps = stage.get("dependencies") or stage.get("depends_on") or []
        deps = [str(d) for d in (deps if isinstance(deps, (list, tuple, set)) else [deps]) if d]
        if deps and not all(states.get(dep) in {"completed", "succeeded"} for dep in deps):
            continue
        if any(states.get(dep) in {"failed", "canceled", "rejected"} for dep in deps):
            stage["status"] = "failed"
            continue
        if stage_id != str(parent_run.get("stage_id") or ""):
            backend = str(stage.get("stage_type") or parent_run.get("adapter") or "prototype_backtest")
            st_refs = (stage.get("input_refs") or []) + (stage.get("approved_input_refs") or [])
            st_ds_refs = _extract_dataset_refs(st_refs)
            exp_tid = str(parent_run.get("tenant_id") or plan_payload.get("tenant_id") or "").strip()
            ds = None
            if stage.get("dataset") and (not st_ds_refs or _matches_dataset_ref(stage["dataset"], st_ds_refs)):
                ds = stage["dataset"]
            elif plan_payload.get("datasets"):
                p_dss = plan_payload["datasets"]
                candidates = p_dss.items() if isinstance(p_dss, dict) else enumerate(p_dss if isinstance(p_dss, list) else [])
                for d_key, d_obj in candidates:
                    if _matches_dataset_ref(d_obj, st_ds_refs) or (isinstance(d_obj, dict) and str(d_key) in st_ds_refs):
                        ds = d_obj
                        break
            elif not st_ds_refs:
                ds = plan_payload.get("dataset") or (parent_run.get("parameters") or {}).get("dataset")

            ds_tid = str(ds.get("tenant_id") or "").strip() if (ds and isinstance(ds, dict) and exp_tid) else ""
            fail_reason = (
                f"Referenced governed research dataset '{st_ds_refs}' is unavailable" if (st_ds_refs and not ds)
                else (f"Unauthorized access to dataset across tenant boundary for stage '{stage_id}'" if (ds_tid and ds_tid != exp_tid)
                else (f"Missing required governed dataset for stage '{stage_id}'" if (backend != "evidence_synthesis" and not ds) else ""))
            )
            is_failed = bool(fail_reason)

            rid = _next_id("rrun", timestamp, {str(r.get("run_id") or "") for r in store.list_runs()})
            pred = next((r_id for d in reversed(deps) if d in latest for r_id in [latest[d].get("run_id") or latest[d].get("id")] if r_id), parent_run.get("run_id") or parent_run.get("id"))
            in_refs = [{"type": "stage", "id": stage_id}]
            if plan_payload.get("plan_id"):
                in_refs.insert(0, {"type": "research_plan", "id": plan_payload["plan_id"]})
            if ds and isinstance(ds, dict):
                ds_id_clean = str(ds.get("dataset_id") or ds.get("id") or "").strip()
                if ds_id_clean:
                    in_refs.append({"type": "dataset", "id": ds_id_clean})

            record = {
                "id": rid, "run_id": rid, "task_id": task_id, "stage_id": stage_id, "attempt_number": 1,
                "parent_run_id": pred if deps else None,
                "root_run_id": parent_run.get("root_run_id") or parent_run.get("run_id") or parent_run.get("id"),
                "adapter": backend, "requested_mode": parent_run.get("requested_mode", "stub"),
                "dispatch_mode": parent_run.get("dispatch_mode", "stub"),
                "status": "failed" if is_failed else "queued",
                "production_activation": "disabled",
                "input_refs": in_refs,
                "parameters": {**(parent_run.get("parameters") or {}), "stage": stage, "plan": plan_payload, "dataset": ds},
                "created_by": actor_id or parent_run.get("created_by"), "tenant_id": parent_run.get("tenant_id"),
                "user_id": parent_run.get("user_id"), "created_at": timestamp, "updated_at": timestamp,
                "idempotency_key": f"stage:{task_id}:{stage_id}", "events": [], "artifact_refs": [],
                "proposal_refs": [], "registry_writebacks": [],
            }
            if is_failed:
                record["error"] = fail_reason
                stage["status"] = "failed"
            store.put_run(record)
            latest[stage_id] = record
            states[stage_id] = "failed" if is_failed else "queued"

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("stage_id") or "")
        record = latest.get(stage_id)
        if not record or str(record.get("status") or "").lower() != "queued":
            continue
        if record.get("cancellation_fence") or str(record.get("status") or "").lower() in {"canceled", "cancelled"}:
            continue
        deps = stage.get("dependencies") or stage.get("depends_on") or []
        deps = [str(d) for d in (deps if isinstance(deps, (list, tuple, set)) else [deps]) if d]
        if any(states.get(dep) not in {"completed", "succeeded"} for dep in deps):
            continue
        run_id = str(record.get("run_id") or record.get("id"))
        with _stage_workers_lock:
            if run_id in _active_stage_workers:
                continue
            _active_stage_workers.add(run_id)
        threading.Thread(
            target=_execute_plan_stage,
            args=(dict(record), dict(stage), dict(plan_payload), store, actor_id),
            name=f"research-stage-{run_id}",
            daemon=True,
        ).start()


def _execute_plan_stage(
    run: Dict[str, Any], stage: Dict[str, Any], plan: Dict[str, Any],
    store: ResearchOrchestratorStore, actor_id: Optional[str],
) -> None:
    run_id = str(run.get("run_id") or run.get("id"))
    params = run.get("parameters") or {}
    ds = params.get("dataset") or plan.get("dataset")
    backend = str(stage.get("stage_type") or run.get("adapter") or "prototype_backtest")
    is_canc = lambda r, t: (
        str(r.get("status") or "").lower() in {"canceled", "cancelled", "rejected"}
        or r.get("cancellation_fence")
        or bool(t and (str(t.get("status") or "").lower() in {"canceled", "cancelled"} or t.get("cancellation_fence")))
    )
    try:
        with _plan_progress_lock:
            current = store.get_run(run_id) or run
            task = store.get_task(str(current.get("task_id") or "")) if current.get("task_id") else None
            if is_canc(current, task):
                return
            current["status"] = "running"
            current = store.put_run(current)
            if is_canc(current, task):
                return
        try:
            execute_research_stage(backend, {
                "stage": stage, "plan": plan, "dataset": ds, "run_id": run_id,
                "correlation_id": params.get("correlation_id") or f"corr-{run_id}",
                "downstream_key": f"stage:{backend}:{run_id}",
            })
        except Exception as exc:
            logger.warning("Research stage execution error for %s: %s", run_id, exc)
            with _plan_progress_lock:
                current = store.get_run(run_id) or current
                if not is_canc(current, None):
                    current["status"], current["error"] = "failed", str(exc)
                    store.put_run(current)
    finally:
        with _stage_workers_lock:
            _active_stage_workers.discard(run_id)
    with _plan_progress_lock:
        completed = store.get_run(run_id) or current
        fresh_task = store.get_task(str(completed.get("task_id") or "")) if completed.get("task_id") else None
        if not is_canc(completed, fresh_task):
            _progress_plan_stages_locked(plan, completed, actor_id, store, str(completed.get("updated_at") or utc_now()))


# ---------------------------------------------------------------------------
# Approved retrain executor
# ---------------------------------------------------------------------------
#
# An approved retrain decision becomes one bounded parameter change that is
# evaluated against the original through Persona Teaching (the real preview job
# route and real vectorbt evaluation).  At most one candidate artifact is
# registered, and only when the candidate passes the Teaching gate and is
# strictly better than the original.  The executor never completes the Teaching
# session, never commits or discards a replay, never fabricates a proof or an
# MFA claim, and writes no runtime binding, deployment plan or capital record.

_RETRAIN_ACTION_TYPE = "retrain"
# Identity of a retrain run: the Training evaluation the executor actually runs.
_RETRAIN_ADAPTER = "training_session_evaluation"
_RETRAIN_MODE = "training_evaluation"
_RETRAIN_SOURCE_SERVICE = "research-orchestrator"
_RETRAIN_OUTCOMES = frozenset({"candidate_created", "no_candidate", "evaluation_rejected"})


_EVOLUTION_EXEC_ACTIONS = frozenset({"retrain", "mutate", "revalidate"})
# Mutate / revalidate runs are labelled by the work they do: the §10 gate on the real OOS backtest.
_EVOLUTION_GATE_ADAPTER = "evolution_oos_gate"
_EVOLUTION_GATE_MODE = "evolution_oos_gate"


def _evolution_action(params: Dict[str, Any]) -> str:
    """The executable evolution action carried by run parameters, else ``""``."""
    if "decision_id" not in params or "target_artifact_id" not in params:
        return ""
    action = str(params.get("action_type") or "retrain").strip().lower()
    return action if action in _EVOLUTION_EXEC_ACTIONS else ""


def _is_retrain_action(params: Dict[str, Any]) -> bool:
    """Approved retrain / mutate / revalidate decisions run an executor.

    Any other action type stays queued: a stub-labelled run is never reported
    completed for work this service did not perform.
    """
    return bool(_evolution_action(params))


class _RetrainFailure(Exception):
    """Transport/auth/timeout failure: the run ends failed and names the step."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


class _RetrainFinished(Exception):
    """The run reached a stated terminal outcome and ends completed."""

    def __init__(self, outcome: str, reason: str) -> None:
        super().__init__(f"{outcome}: {reason}")
        self.outcome = outcome
        self.reason = reason


class _OwnerRejection(Exception):
    """An owner API refused the request with a business (4xx) response."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _retrain_env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _retrain_call(
    *,
    step: str,
    service: str,
    method: str,
    url: str,
    tenant_id: str,
    token_variable: str,
    body: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 15.0,
) -> Any:
    """One authenticated owner call.

    The service credential is read at call time from its mounted file, so a
    rotated token is picked up without a restart.  A missing credential, a
    transport error, a 401/403 or a 5xx fails the run at ``step``; any other
    4xx is the owner's rejection and is returned to the caller as such.
    """
    from services.service_token_file import configured_service_token

    try:
        token = configured_service_token(token_variable)
    except RuntimeError as exc:
        raise _RetrainFailure(step, f"{service} service credential unavailable ({token_variable}): {exc}") from None
    if not token:
        raise _RetrainFailure(step, f"{service} service credential is not configured ({token_variable})")
    headers = {
        "Accept": "application/json",
        "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Pantheon-Service": _RETRAIN_SOURCE_SERVICE,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
            raw_detail = parsed.get("detail") if isinstance(parsed, dict) else None
            if raw_detail is None and isinstance(parsed, dict):
                raw_detail = parsed.get("error")
            detail = raw_detail if isinstance(raw_detail, str) else json.dumps(raw_detail, sort_keys=True)
        except Exception:  # noqa: BLE001 - the status line is enough.
            detail = str(exc.reason or "")
        detail = (detail or f"HTTP {exc.code}")[:500]
        if exc.code in (401, 403) or exc.code >= 500 or exc.code in (408, 429):
            raise _RetrainFailure(step, f"{service} returned HTTP {exc.code}: {detail}") from None
        raise _OwnerRejection(exc.code, detail) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise _RetrainFailure(step, f"{service} request failed: {type(exc).__name__}: {exc}") from None


def _retrain_record(run_id: str, **fields: Any) -> Dict[str, Any]:
    """Persist retrain progress on the research run as it becomes known."""
    rstore = get_store()
    run = rstore.get_run(run_id) or {}
    run.update(fields)
    run["updated_at"] = utc_now()
    rstore.put_run(run)
    return run


def _retrain_prior_state(run_id: str, params: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """Progress already recorded for this decision by this or an earlier attempt.

    A retry is a new run for the same decision; it resumes from the recorded
    session and job ids and never opens a second session or registers a second
    child.  A startup resume re-runs the same run in place, so the run's own
    recorded fields are part of the state too (a run with no session yet
    recorded nothing and contributes nothing).
    """
    decision_id = str(params.get("decision_id") or "")
    keys = (
        "session_id", "baseline_job_id", "baseline_eval_id", "candidate_job_id", "candidate_eval_id",
        "baseline_metrics", "candidate_metrics", "persona_id", "parameter_updates",
        "candidate_artifact_id", "candidate_proof_ref", "outcome", "outcome_reason",
        "evolution_case_file", "evolution_child_context", "evolution_children",
    )
    related = []
    for other in get_store().list_runs():
        other_params = other.get("parameters") or {}
        if str(other_params.get("decision_id") or "") != decision_id:
            continue
        if str(other.get("tenant_id") or other_params.get("tenant_id") or "") != tenant_id:
            continue
        if not other.get("session_id"):
            continue
        related.append(other)
    related.sort(key=lambda item: str(item.get("created_at") or ""))
    state: Dict[str, Any] = {}
    for other in related:  # later attempts override earlier ones
        for key in keys:
            if other.get(key) not in (None, "", {}, []):
                state[key] = other[key]
    return state


def _retrain_finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in (float("inf"), float("-inf")) else None


# Governed in-sample dataset for the retrain search: the same admitted
# DatasetVersion authority Training evaluates against (canonical dataset +
# threshold policy, validated by ``load_evaluation_authority``).  A decision may
# also carry ``parameters["dataset"]``.  With neither, the search fails closed
# instead of falling back to a fixed toggle.
_RETRAIN_SEARCH_BUDGET_ENV = "RESEARCH_RETRAIN_SEARCH_BUDGET"
_RETRAIN_TRIAL_COUNTER_ENV = "RESEARCH_REPLICATION_TRIAL_COUNTER_PATH"


def _pool_owner(params):
    if not params.get("pool_member"):
        return None
    from services.evolution.auto_consumer_runtime import PoolOwner
    env = dict(os.environ, EVOLUTION_SCHEDULER_TENANT_ID=params["tenant_id"])
    return PoolOwner(env)


def _admit_evolution_child(params, child_id):
    owner = _pool_owner(params)
    if owner is not None:
        ctx = params["pool_member"]
        parent = next((m for m in owner.pool(ctx["persona_id"])["members"]
                       if m["artifact_ref"] == ctx["artifact_ref"]), None)
        if parent is None or parent["stage"] != "active" or any(
                parent.get(k) != ctx.get(k) for k in ("checksum", "joined_at")):
            raise _RetrainFailure("pool_trial", "approved parent membership changed")
        owner.admit_child(ctx, child_id, params["decision_id"])


def _evolution_member_case_context(run_id, parent, params):
    """Persist the owner-composed case file even when a child is refused."""
    if not params.get("pool_member"):
        return None, {}
    from services.research.alpha_replication import evolution_executor as executor

    try:
        case_file = executor.compose_member_case_file(
            parent, params=params, tenant_id=params["tenant_id"],
            decision_id=params["decision_id"], parent_registry_ref=params["target_artifact_id"],
        )
        _retrain_record(run_id, evolution_case_file=case_file)
        declaration = params.get("addresses_lessons", params["pool_member"].get("addresses_lessons"))
        context = executor.child_lesson_context(case_file, declaration)
    except executor.ExecutorRefusal as exc:
        _retrain_record(run_id, evolution_case_file_refusal={"reason": exc.reason})
        raise _RetrainFinished(exc.outcome, exc.reason) from None
    _retrain_record(run_id, evolution_child_context=context)
    return case_file, context


@app.get("/api/research/evolution/families/{family_id}")
def evolution_family_state(family_id: str, bar_frequency: Optional[str] = None,
                           tenant_id: str = Depends(_authenticated_alpha_replication_tenant)):
    from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore
    from services.research.replication.gate import family_trial_key
    # The same family counter used by the real gate is durable across workers.
    views = _retrain_call(step="family_state", service="registry", method="GET",
        token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN", tenant_id=tenant_id,
        url=os.getenv("REGISTRY_URL", "http://registry:8087").rstrip("/") +
            "/api/registry/strategies/" + urllib.parse.quote(family_id, safe="") + "/strategy-artifacts")
    if not any(((v or {}).get("entry") or {}).get("owner_tenant") == tenant_id for v in views or []):
        raise HTTPException(status_code=404, detail="family not found")
    # Frequency-scoped key (SD §9 K8); omitted/daily keeps the legacy bare family key.
    # `trials` stays frequency-isolated; the O-17 lifetime cap (500) is enforced on the
    # aggregate family total across every frequency bucket.
    store = SharedFamilyTrialCounterStore(_retrain_trial_counter_path())
    trials = store.get(family_trial_key(family_id, bar_frequency))
    total = store.family_total(family_id)
    return {"family_id": family_id, "bar_frequency": (bar_frequency or "").strip().lower() or None, "trials": trials,
            "family_total_trials": total, "status": "tried" if total >= 500 else "available"}


def _retrain_trial_counter_path() -> Path:
    """Authoritative family counter: the file OOS admission's gate also uses."""
    explicit = os.environ.get(_RETRAIN_TRIAL_COUNTER_ENV)
    return Path(explicit) if explicit else Path(ALPHA_REPLICATION_DATA_DIR) / "family_trial_counts.json"


def _default_retrain_dataset_loader(parent_artifact: Dict[str, Any], params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    dataset_path = os.environ.get("RESEARCH_RETRAIN_CANONICAL_DATASET_PATH") or os.environ.get("TRAINING_SESSION_CANONICAL_DATASET_PATH")
    policy_path = os.environ.get("RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH") or os.environ.get("TRAINING_SESSION_THRESHOLD_POLICY_PATH")
    if not dataset_path or not policy_path:
        return None
    training_dir = str(Path(__file__).resolve().parent.parent / "training-session")
    sys.path.insert(0, training_dir)
    try:
        import evaluation_authority as _authority  # type: ignore

        pinned = os.environ.get("RESEARCH_RETRAIN_TRUSTED_NOW")
        trusted_now = datetime.fromisoformat(pinned.replace("Z", "+00:00")) if pinned else datetime.now(timezone.utc)
        snapshot = _authority.load_evaluation_authority(
            dataset_path, policy_path, trusted_now=trusted_now,
            strategy_id=str(parent_artifact.get("strategy_id") or parent_artifact.get("artifact_id")),
        )
        return dict(snapshot.vectorbt_dataset)
    except Exception:  # governance rejection / unreadable authority: fail closed
        return None
    finally:
        if training_dir in sys.path:
            sys.path.remove(training_dir)


_RETRAIN_DATASET_LOADER: Optional[Any] = _default_retrain_dataset_loader


def _retrain_search_update(
    parent_artifact: Dict[str, Any], params: Dict[str, Any], run_id: str
) -> Dict[str, Any]:
    """Fixed-budget random search over the declared mutation ranges (design §12.2).

    Scored by walk-forward folds on the in-sample window only (the out-of-sample
    window is never loaded into the scorer) and counted per family.  Replaces the
    former lookback 2<->3 / threshold 0<->0.01 toggle.  Returns the best
    parameter update, or finishes the run ``no_candidate``.
    """
    from services.research.alpha_replication import evolution_search as search

    dataset = params.get("dataset") if isinstance(params.get("dataset"), dict) else None
    if dataset is None and callable(_RETRAIN_DATASET_LOADER):
        dataset = _RETRAIN_DATASET_LOADER(parent_artifact, params)
    if not isinstance(dataset, dict):
        raise _RetrainFinished("no_candidate", "in_sample_dataset_unavailable")
    budget = 50 if params.get("pool_member") else int(os.environ.get(_RETRAIN_SEARCH_BUDGET_ENV) or search.DEFAULT_SEARCH_BUDGET)
    family_id = str(parent_artifact.get("strategy_id") or parent_artifact.get("artifact_id"))
    if params.get("pool_member") and search.shared_family_trial_counter(_retrain_trial_counter_path()).family_total(family_id) >= 500:
        raise _RetrainFinished("no_candidate", "family_tried")
    from services.research.vectorbt.adapter.vectorbt_adapter import VectorbtBackend

    prev = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        scorer = search.make_in_sample_walk_forward_scorer(dataset, backend=VectorbtBackend())
        outcome = search.search_in_sample(
            parent_artifact,
            score_fn=scorer,
            decision_id=str(params.get("decision_id") or run_id),
            family_id=family_id,
            budget=budget,
            trial_counter=search.shared_family_trial_counter(_retrain_trial_counter_path()),
        )
    except search.EvolutionSearchError as exc:
        raise _RetrainFinished("no_candidate", f"search_unavailable: {exc}") from None
    finally:
        if prev is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = prev
    _retrain_record(run_id, search=outcome.to_dict())
    if outcome.best is None:
        raise _RetrainFinished("no_candidate", "search_found_no_in_sample_improvement")
    return dict(outcome.best.parameter_updates)


def _retrain_evaluate(
    *,
    run_id: str,
    step: str,
    training_url: str,
    tenant_id: str,
    session_id: str,
    decision_id: str,
    kind: str,
    recorded_job_id: Optional[str],
) -> Dict[str, Any]:
    """Queue (or resume) one evaluation job, run it, and wait for its terminal state.

    Returns the completed preview job.  A job the Training owner rejects ends the
    run ``evaluation_rejected``; transport, auth and wait-timeout failures raise
    ``_RetrainFailure`` naming ``step``.
    """
    mode = "baseline" if kind == "baseline" else "refresh"
    job_id = recorded_job_id
    job: Dict[str, Any] = {}
    if not job_id:
        try:
            job = _retrain_call(
                step=f"queue_{kind}_evaluation", service="training", method="POST", token_variable="RESEARCH_TRAINING_SERVICE_TOKEN",
                url=f"{training_url}/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/preview-jobs",
                tenant_id=tenant_id,
                body={"mode": mode, "requested_by": _RETRAIN_SOURCE_SERVICE, "terminalize_session": False, "purpose": "retrain"},
                extra_headers={"Idempotency-Key": f"retrain-{kind}:{decision_id}"},
            )
        except _OwnerRejection as exc:
            raise _RetrainFinished("evaluation_rejected", f"{kind} evaluation queue rejected: {exc.detail}") from None
        job_id = str(job.get("job_id") or "")
        if not job_id:
            raise _RetrainFailure(f"queue_{kind}_evaluation", "Training returned no job_id")
        _retrain_record(run_id, **{f"{kind}_job_id": job_id, f"{kind}_eval_id": job.get("eval_id")})
    job_url = f"{training_url}/api/training/preview-jobs/{urllib.parse.quote(job_id, safe='')}"
    run_url = job_url + "/run"
    deadline = time.monotonic() + _retrain_env_float("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS", 300.0)
    run_timeout = _retrain_env_float("RESEARCH_RETRAIN_EVAL_RUN_TIMEOUT_SECONDS", 120.0)
    poll = _retrain_env_float("RESEARCH_RETRAIN_EVAL_POLL_SECONDS", 1.0)
    while True:
        try:
            job = _retrain_call(
                step=f"wait_{kind}_evaluation", service="training", method="GET", url=job_url,
                tenant_id=tenant_id, token_variable="RESEARCH_TRAINING_SERVICE_TOKEN",
            )
        except _OwnerRejection as exc:
            raise _RetrainFinished("evaluation_rejected", f"{kind} evaluation job unreadable: {exc.detail}") from None
        status = str(job.get("status") or "").lower()
        if status == "completed":
            _retrain_record(run_id, **{f"{kind}_eval_id": job.get("eval_id")})
            return job
        if status == "failed":
            code = str(job.get("error_code") or "")
            reason = str(job.get("failure_reason") or code or "evaluation failed")
            attempts = int(job.get("attempt_count") or 0)
            if job.get("retryable") is True and attempts < int(job.get("max_attempts") or 1):
                pass  # the run route re-claims a retryable failed job below
            elif code in {"evaluation_admission_rejected", "training_session_not_found"}:
                raise _RetrainFinished("evaluation_rejected", f"{kind} evaluation rejected: {reason}")
            else:
                raise _RetrainFailure(f"{kind}_evaluation", f"Training evaluation failed ({code or 'unknown'}): {reason}")
        if time.monotonic() >= deadline:
            raise _RetrainFailure(f"wait_{kind}_evaluation", "bounded wait for the evaluation timed out")
        # queued / retryable-failed / running with an expired lease: drive it through the real run route.
        try:
            _retrain_call(
                step=f"run_{kind}_evaluation", service="training", method="POST", url=run_url,
                tenant_id=tenant_id, token_variable="RESEARCH_TRAINING_SERVICE_TOKEN", body={}, timeout=run_timeout,
            )
        except _OwnerRejection as exc:
            if exc.status != 409:  # 409: another worker holds the lease; keep waiting
                raise _RetrainFinished("evaluation_rejected", f"{kind} evaluation run rejected: {exc.detail}") from None
            time.sleep(poll)
        except _RetrainFailure as exc:
            # A timed-out run call may still be running at the owner: read its state back.
            if "request failed" not in exc.detail:
                raise
            time.sleep(poll)


def _retrain_proof(job: Dict[str, Any]) -> Dict[str, Any]:
    preview = job.get("preview") if isinstance(job.get("preview"), dict) else {}
    proof = preview.get("evaluation_proof") if isinstance(preview.get("evaluation_proof"), dict) else {}
    return proof


def _retrain_metrics(proof: Dict[str, Any]) -> Dict[str, Any]:
    metrics = dict(proof.get("metrics") or {})
    metrics["sharpe_defined"] = not any(
        str(error).startswith("sharpe_undefined_") for error in (proof.get("validation_errors") or [])
    ) and _retrain_finite(metrics.get("sharpe_ratio")) is not None
    return metrics


def _execute_retrain(run_id: str, params: Dict[str, Any]) -> None:
    decision_id = str(params["decision_id"])
    target_artifact_id = str(params["target_artifact_id"])
    work_item_id = str(params.get("work_item_id") or "")
    tenant_id = str(params.get("tenant_id") or "").strip()
    training_url = os.getenv("TRAINING_SESSION_URL", "http://training-session-svc:8099").rstrip("/")
    registry_url = os.getenv("REGISTRY_URL", "http://registry:8087").rstrip("/")

    _retrain_record(run_id, status="running")
    if not tenant_id:
        raise _RetrainFailure("resolve_tenant", "run parameters carry no decision tenant_id")
    state = _retrain_prior_state(run_id, params, tenant_id)
    if state.get("candidate_artifact_id") or state.get("outcome") in _RETRAIN_OUTCOMES:
        # The decision already reached its outcome: adopt it, change nothing.
        if params.get("pool_member") and state.get("candidate_artifact_id") and not state.get("evolution_child_context"):
            raise _RetrainFinished("no_candidate", "recorded_child_case_file_missing")
        carried = {k: v for k, v in state.items() if k in {
            "session_id", "baseline_job_id", "baseline_eval_id", "candidate_job_id", "candidate_eval_id",
            "baseline_metrics", "candidate_metrics", "candidate_artifact_id", "candidate_proof_ref", "persona_id",
            "evolution_case_file", "evolution_child_context", "evolution_children"}}
        _retrain_record(run_id, **carried)
        if state.get("candidate_artifact_id"):
            _admit_evolution_child(params, state["candidate_artifact_id"])
        raise _RetrainFinished(
            str(state.get("outcome") or "candidate_created"),
            str(state.get("outcome_reason") or "resumed_from_recorded_outcome"),
        )
    if state:
        _retrain_record(run_id, **{k: v for k, v in state.items() if k not in {"outcome", "outcome_reason"}})

    # 1. The target registry entry names its persona; nothing is assumed.
    try:
        artifact_view = _retrain_call(
            step="read_target_artifact", service="registry", method="GET", token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
            url=f"{registry_url}/api/registry/strategy-artifacts/{urllib.parse.quote(target_artifact_id, safe='')}",
            tenant_id=tenant_id,
        )
    except _OwnerRejection as exc:
        raise _RetrainFinished("evaluation_rejected", f"target artifact rejected by Registry: {exc.detail}") from None
    entry = artifact_view.get("entry") if isinstance(artifact_view, dict) else None
    metadata = (entry or {}).get("metadata") if isinstance(entry, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    parent_artifact = metadata.get("strategy_artifact")
    if not isinstance(parent_artifact, dict):
        raise _RetrainFailure("read_target_artifact", "Registry entry carries no strategy_artifact")
    persona_id = str(metadata.get("persona_id") or "").strip()
    if not persona_id:
        raise _RetrainFinished("evaluation_rejected", "target_has_no_persona")

    _case_file, child_context = _evolution_member_case_context(run_id, parent_artifact, params)

    # 2. One Teaching session for this decision (resumed, never duplicated).
    session_id = str(state.get("session_id") or "")
    session: Dict[str, Any] = {}
    if not session_id:
        try:
            session = _retrain_call(
                step="open_session", service="training", method="POST", token_variable="RESEARCH_TRAINING_SERVICE_TOKEN",
                url=f"{training_url}/api/training/sessions", tenant_id=tenant_id,
                body={
                    "persona_id": persona_id,
                    "objective": f"Retrain evaluation for EvolutionDecision {decision_id} on {target_artifact_id}",
                    "mode": "coaching",
                    "context_refs": [
                        {"type": "evolution_decision", "id": decision_id},
                        {"type": "research_run", "id": run_id},
                    ],
                    "actor_id": _RETRAIN_SOURCE_SERVICE,
                },
            )
        except _OwnerRejection as exc:
            raise _RetrainFinished("evaluation_rejected", f"session rejected by Training: {exc.detail}") from None
        session_id = str(session.get("session_id") or "")
        if not session_id:
            raise _RetrainFailure("open_session", "Training returned no session_id")
        _retrain_record(run_id, session_id=session_id, persona_id=persona_id)
    else:
        try:
            session = _retrain_call(
                step="resume_session", service="training", method="GET", token_variable="RESEARCH_TRAINING_SERVICE_TOKEN",
                url=f"{training_url}/api/training/sessions/{urllib.parse.quote(session_id, safe='')}", tenant_id=tenant_id,
            )
        except _OwnerRejection as exc:
            raise _RetrainFinished("evaluation_rejected", f"recorded session unavailable: {exc.detail}") from None
        if str(session.get("status") or "").lower() != "active":
            raise _RetrainFinished("evaluation_rejected", f"recorded session {session_id} is no longer active")
    model_authority = (session.get("metadata") or {}).get("model_authority")
    if isinstance(model_authority, dict) and str(model_authority.get("artifact_id") or "") not in ("", target_artifact_id):
        raise _RetrainFinished("evaluation_rejected", "persona_model_is_not_target_artifact")

    # 3. Baseline of the original, before any control change.
    controls_url = f"{training_url}/api/training/sessions/{urllib.parse.quote(session_id, safe='')}/controls"
    proposed = state.get("parameter_updates") or _retrain_search_update(parent_artifact, params, run_id)
    if not state.get("baseline_metrics"):
        baseline_job = _retrain_evaluate(
            run_id=run_id, step="baseline", training_url=training_url, tenant_id=tenant_id, session_id=session_id,
            decision_id=decision_id, kind="baseline", recorded_job_id=state.get("baseline_job_id"),
        )
        baseline_proof = _retrain_proof(baseline_job)
        _retrain_record(run_id, baseline_metrics=_retrain_metrics(baseline_proof), baseline_job_id=baseline_job.get("job_id"),
                        baseline_eval_id=baseline_job.get("eval_id"))
    baseline_metrics = (get_store().get_run(run_id) or {}).get("baseline_metrics") or state.get("baseline_metrics") or {}
    baseline_sharpe = _retrain_finite(baseline_metrics.get("sharpe_ratio"))
    if not baseline_metrics.get("sharpe_defined") or baseline_sharpe is None:
        raise _RetrainFinished("no_candidate", "baseline_unavailable")

    # 4. One bounded control change through the existing controls route.
    _retrain_record(run_id, parameter_updates=proposed)
    patches = [{"parameter_key": key, "proposed_value": value} for key, value in proposed.items()]
    try:
        current = _retrain_call(
            step="read_controls", service="training", method="GET", token_variable="RESEARCH_TRAINING_SERVICE_TOKEN",
            url=f"{training_url}/api/training/controls/{urllib.parse.quote(session_id, safe='')}", tenant_id=tenant_id,
        )
        current_values = {c.get("parameter_key"): c.get("current_value") for c in (current.get("controls") or [])}
        if any(current_values.get(p["parameter_key"]) != p["proposed_value"] for p in patches):
            patched = _retrain_call(
                step="apply_control_change", service="training", method="POST", token_variable="RESEARCH_TRAINING_SERVICE_TOKEN",
                url=controls_url, tenant_id=tenant_id, body={"patches": patches},
            )
            if str(patched.get("status") or "").lower() != "accepted":
                raise _RetrainFinished(
                    "evaluation_rejected",
                    "control change rejected by Training: " + json.dumps(patched.get("field_errors") or [], sort_keys=True),
                )
    except _OwnerRejection as exc:
        raise _RetrainFinished("evaluation_rejected", f"control change rejected by Training: {exc.detail}") from None

    # 5. Candidate evaluation.
    candidate_job = _retrain_evaluate(
        run_id=run_id, step="candidate", training_url=training_url, tenant_id=tenant_id, session_id=session_id,
        decision_id=decision_id, kind="candidate", recorded_job_id=state.get("candidate_job_id"),
    )
    candidate_proof = _retrain_proof(candidate_job)
    candidate_metrics = _retrain_metrics(candidate_proof)
    proof_ref = str(candidate_proof.get("proof_ref") or candidate_job.get("evaluation_proof_ref") or "")
    _retrain_record(run_id, candidate_metrics=candidate_metrics, candidate_job_id=candidate_job.get("job_id"),
                    candidate_eval_id=candidate_job.get("eval_id"), candidate_proof_ref=proof_ref)

    # 6. Decide.  A child exists only for a passed, strictly better candidate.
    gate_passed = (
        str(candidate_proof.get("status") or "").lower() == "passed"
        and str(candidate_proof.get("governance_gate_state") or "").lower() == "passed"
        and bool(proof_ref)
    )
    if not gate_passed:
        raise _RetrainFinished("no_candidate", "evaluation_gate_failed")
    candidate_sharpe = _retrain_finite(candidate_metrics.get("sharpe_ratio"))
    if not candidate_metrics.get("sharpe_defined") or candidate_sharpe is None or not candidate_sharpe > baseline_sharpe:
        raise _RetrainFinished("no_candidate", "not_better_than_baseline")

    # 7. Every retrain child is judged on the untouched out-of-sample window by the real
    #    §10 ReplicationGate (family trial counts accumulate, deflated Sharpe) BEFORE any
    #    Registry write.  Training's whole-dataset preview proof alone never registers a child.
    from services.research.alpha_replication import evolution_search as search

    suffix = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:10]
    new_artifact_id = f"{target_artifact_id}-retrain-{suffix}"
    child_url = f"{registry_url}/api/registry/strategy-artifacts/{urllib.parse.quote(new_artifact_id, safe='')}"
    family_id = str(parent_artifact.get("strategy_id") or parent_artifact.get("artifact_id"))

    def read_child() -> Optional[Dict[str, Any]]:
        try:
            return _retrain_call(
                step="register_candidate", service="registry", method="GET",
                token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN", url=child_url, tenant_id=tenant_id,
            )
        except _OwnerRejection:
            return None

    child_view = read_child()  # an earlier attempt of this decision already registered it: adopt, never duplicate
    if child_view is None:
        def registry_read(step: str, path: str) -> Any:
            return _retrain_call(
                step=step, service="registry", method="GET", token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
                url=f"{registry_url}{path}", tenant_id=tenant_id,
            )

        gate, factory = _evolution_oos_context(
            parent=parent_artifact, entry_meta=metadata, params=params, tenant_id=tenant_id,
            decision_id=decision_id, family_id=family_id, read=registry_read,
        )
        source_runs = [session_id, proof_ref]
        child_view = None
        for _attempt in range(3):
            new_version = search.allocate_child_version(
                parent_artifact.get("version") or "1.0.0",
                _registry_family_versions(registry_url, tenant_id, parent_artifact),
            )
            try:
                child, _descriptor = search.build_child_artifact(
                    parent_artifact, decision_id=decision_id, kind="retrain", parameter_updates=proposed,
                    source_run_ids=source_runs, child_version=new_version, parent_registry_ref=target_artifact_id,
                )
                if child_context:
                    child["artifact_id"] = new_artifact_id
            except (search.EvolutionSearchError, StrategyArtifactValidationError) as exc:
                raise _RetrainFinished("no_candidate", f"retrain_child_invalid: {exc}") from None
            if child_context:
                _retrain_record(run_id, evolution_children={new_artifact_id: child_context})
            prev_backend = os.environ.get("PANTHEON_VECTORBT_BACKEND")
            os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
            try:
                verdict = search.judge_child_out_of_sample(child, gate=gate, request_factory=factory, family_id=family_id)
            except search_errors() as exc:
                raise _RetrainFinished("evaluation_rejected", f"oos_evaluation_unavailable: {exc}") from None
            finally:
                if prev_backend is None:
                    os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
                else:
                    os.environ["PANTHEON_VECTORBT_BACKEND"] = prev_backend
            _retrain_record(run_id, out_of_sample_gate=verdict, family_trial_count=verdict.get("trial_count_after"))
            if not verdict["passed"]:
                raise _RetrainFinished("no_candidate", "children_failed_out_of_sample_gate")
            try:
                registration_url = f"{registry_url}/api/registry/strategy-artifacts/{urllib.parse.quote(target_artifact_id, safe='')}/mutate"
                registration_body = {
                    "new_artifact_id": new_artifact_id,
                    "new_version": new_version,
                    "parameter_updates": proposed,
                    "source_run_ids": [decision_id, *source_runs],
                }
                if child_context:
                    # The existing full registration API preserves proposal
                    # metadata; the parameter-only mutation API has no such field.
                    registration_url = f"{registry_url}/api/registry/strategy-artifacts"
                    registration_body = {
                        "registry_id": new_artifact_id, "artifact_state": "candidate",
                        "strategy_artifact": child,
                        **_producer_proof_registration(
                            data_dir=Path(ALPHA_REPLICATION_DATA_DIR), child=child, verdict=verdict,
                            decision_id=decision_id, run_id=run_id, gate=gate,
                        ),
                        "metadata": {"persona_id": persona_id, **child_context},
                    }
                child_view = _retrain_call(
                    step="register_candidate", service="registry", method="POST", token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
                    url=registration_url, tenant_id=tenant_id, body=registration_body,
                )
                break
            except _OwnerRejection as exc:
                if exc.status != 409:
                    raise _RetrainFinished("evaluation_rejected", f"candidate rejected by Registry: {exc.detail}") from None
                child_view = read_child()
                if child_view is not None:
                    break  # this decision's own child already exists: adopt
                # the revision was taken by another registration: allocate a fresh one and re-judge
        if child_view is None:
            raise _RetrainFinished("evaluation_rejected", "candidate_version_allocation_conflict")
    child_entry = child_view.get("entry") if isinstance(child_view, dict) else {}
    child_id = str((child_entry or {}).get("registry_id") or new_artifact_id)
    if str((child_entry or {}).get("artifact_state") or "") != "candidate":
        raise _RetrainFailure("register_candidate", "Registry did not report the child as a candidate")
    run = get_store().get_run(run_id) or {}
    writebacks = list(run.get("registry_writebacks") or [])
    writebacks.append({"registry_id": child_id, "artifact_state": "candidate", "created_at": utc_now()})
    _retrain_record(run_id, candidate_artifact_id=child_id, registry_writebacks=writebacks)
    _admit_evolution_child(params, child_id)
    raise _RetrainFinished("candidate_created", "candidate_passed_gate_and_beat_baseline")


def _registry_family_versions(registry_url: str, tenant_id: str, parent: Dict[str, Any]) -> List[str]:
    """Every version already registered for the parent's strategy family (read fresh per allocation)."""
    try:
        family = _retrain_call(
            step="register_candidate", service="registry", method="GET",
            token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN", tenant_id=tenant_id,
            url=f"{registry_url}/api/registry/strategies/{urllib.parse.quote(str(parent.get('strategy_id') or ''), safe='')}/strategy-artifacts",
        )
    except _OwnerRejection:
        return []
    return [
        str(((view or {}).get("entry") or {}).get("version") or "")
        for view in (family if isinstance(family, list) else [])
    ]


def _evolution_oos_context(
    *, parent: Dict[str, Any], entry_meta: Dict[str, Any], params: Dict[str, Any], tenant_id: str,
    decision_id: str, family_id: str, read: Any,
) -> tuple:
    """Real §10 ReplicationGate + request factory bound to the governed dataset (shared by every child kind)."""
    from services.research.alpha_replication import evolution_executor as executor
    from services.research.alpha_replication.shared_stores import SharedBacktestRunStore, SharedReplicationGate

    # The admitted StrategySpec the gate validates the candidate content against.
    spec_id = str((parent.get("lineage") or {}).get("source_strategy_spec_id") or entry_meta.get("strategy_spec_id") or "")
    if not spec_id:
        raise _RetrainFinished("evaluation_rejected", "strategy_spec_unavailable")
    try:
        spec_view = read("read_strategy_spec", f"/api/registry/strategy-specs/{urllib.parse.quote(spec_id, safe='')}")
    except _OwnerRejection as exc:
        raise _RetrainFinished("evaluation_rejected", f"strategy_spec_unavailable: {exc.detail}") from None
    spec_entry = (spec_view or {}).get("entry") or {}
    spec = (spec_entry.get("metadata") or {}).get("strategy_spec")
    if (
        spec_entry.get("artifact_state") != "approved"
        or str(spec_entry.get("owner_tenant") or (spec_entry.get("metadata") or {}).get("tenant_id") or "") != tenant_id
        or not isinstance(spec, dict)
    ):
        raise _RetrainFinished("evaluation_rejected", "strategy_spec_not_approved_for_tenant")

    dataset = params.get("dataset") if isinstance(params.get("dataset"), dict) else None
    if dataset is None and callable(_RETRAIN_DATASET_LOADER):
        dataset = _RETRAIN_DATASET_LOADER(parent, params)
    if not isinstance(dataset, dict):
        raise _RetrainFinished("evaluation_rejected", "governed_dataset_unavailable")

    if params.get("pool_member") and _evolution_action(params) == "revalidate":
        try:
            dataset = executor.post_admission_dataset(dataset, params["pool_member"]["joined_at"])
        except executor.ExecutorRefusal as exc:
            raise _RetrainFinished(exc.outcome, exc.reason) from None

    data_dir = Path(ALPHA_REPLICATION_DATA_DIR)
    backtests = SharedBacktestRunStore(data_dir / "backtest_runs.json")
    gate = _ProofCapturingGate(
        SharedReplicationGate(trial_counter_path=_retrain_trial_counter_path(), backtest_run_store=backtests)
    )

    def _record_owned(evaluation: Dict[str, Any]) -> Optional[str]:
        run_id = backtests.record(evaluation)
        if run_id:
            gate.evaluations[run_id] = copy.deepcopy(evaluation)  # the exact evaluation the gate judges
            _bind_backtest_run_owner(data_dir, run_id, tenant_id=tenant_id, decision_id=decision_id)
        return run_id

    factory = executor.build_gate_request_factory(
        spec=spec, dataset=dataset, decision_id=decision_id, tenant_id=tenant_id,
        record_backtest=_record_owned, family_id=family_id, timestamp=utc_now(),
    )

    return gate, factory


class _ProofCapturingGate:
    """Delegates to the real gate and keeps each response so registration can send the real proof.

    The proof/evaluation transport is scoped to this gate (one executing run); a rejected
    judgement is released immediately and everything else dies with the gate.
    """

    def __init__(self, gate: Any) -> None:
        self._gate = gate
        self.proofs: Dict[str, Dict[str, Any]] = {}
        self.evaluations: Dict[str, Dict[str, Any]] = {}

    def release(self, run_id: Any) -> None:
        self.proofs.pop(str(run_id or ""), None)
        self.evaluations.pop(str(run_id or ""), None)

    def get_family_trial_count(self, family_id: str) -> int:
        return self._gate.get_family_trial_count(family_id)

    def get_family_total_trial_count(self, family_id: str) -> int:
        # The real gate always provides the aggregate; minimal gate doubles fall back to the bare key.
        total = getattr(self._gate, "get_family_total_trial_count", None)
        return total(family_id) if callable(total) else self._gate.get_family_trial_count(family_id)

    def record_family_trial(self, family_id: str) -> int:
        record = getattr(self._gate, "record_family_trial", None)
        return record(family_id) if callable(record) else self._gate.get_family_trial_count(family_id)

    def evaluate_candidate(self, request: Any) -> Any:
        try:
            response = self._gate.evaluate_candidate(request)
        except BaseException:
            self.proofs.clear()
            self.evaluations.clear()
            raise
        for result in getattr(response, "results", []) or []:
            if getattr(result, "criterion_id", "") == "real_backtest_validation":
                run_id = str((getattr(result, "details", None) or {}).get("backtest_run_id") or "")
                if not run_id:
                    continue
                if getattr(response, "passed", False) and callable(getattr(response, "to_dict", None)):
                    self.proofs[run_id] = response.to_dict()
                else:
                    self.release(run_id)
        # Evaluations recorded for runs that never produced a passing proof are not retained.
        for run_id in [r for r in self.evaluations if r not in self.proofs]:
            self.evaluations.pop(run_id, None)
        return response


def _producer_proof_registration(
    *, data_dir: Path, child: Dict[str, Any], verdict: Dict[str, Any], decision_id: str, run_id: str,
    gate: Any = None,
) -> Dict[str, Any]:
    """Registration fields for a gate-passed child: the real proof, its run evaluation and lineage.

    Records the actual gate verdict (trial/mandate context + digest) in the server-owned run
    record so the Registry can authenticate it through the owner-binding readback.
    """
    from services.registry.promotion.gate import real_backtest_verdict_digest
    backtest_run_id = str(verdict.get("backtest_run_id") or "")
    proof = getattr(gate, "proofs", {}).pop(backtest_run_id, None)
    results = (proof or {}).get("results") if isinstance(proof, dict) else None
    details = next(
        (r.get("details") for r in results or [] if r.get("criterion_id") == "real_backtest_validation"), None
    )
    evaluation = getattr(gate, "evaluations", {}).pop(backtest_run_id, None)
    summary = {
        "out_of_sample_gate": dict(verdict),
        "decision_id": decision_id,
    }
    if not isinstance(details, dict) or not evaluation:
        return {"evaluation_summary": summary, "producer_run_id": run_id}
    wire = json.loads(json.dumps(proof, default=str))
    wire["replication_results"] = wire.pop("results")
    wire["backtest_run_id"] = backtest_run_id
    full = json.loads(json.dumps(evaluation, default=str))
    summary.update(
        backtest_run_id=backtest_run_id, out_of_sample_sharpe_ratio=details.get("out_of_sample_sharpe_ratio"),
        max_drawdown=abs(float(details.get("out_of_sample_max_drawdown"))),
        round_trips=details.get("out_of_sample_round_trips"),
    )
    wire["evaluation_summary"] = summary
    _bind_backtest_run_gate_verdict(
        data_dir, backtest_run_id,
        {"admission_status": wire.get("admission_status"), "trial_count": details.get("trial_count"),
         "mandate_max_drawdown": details.get("mandate_max_drawdown"), "family_id": details.get("family_id"),
         "verdict_digest": real_backtest_verdict_digest(json.loads(json.dumps(details, default=str)))},
    )
    # The artifact checksum is fixed before the run exists, so the exact backtest run is the producer run.
    return {
        "evaluation_summary": summary, "producer_run_id": backtest_run_id, "replication_proof": wire,
        "backtest_evaluation": full,
    }


def _execute_evolution_gate(run_id: str, params: Dict[str, Any]) -> None:
    """Approved ``mutate`` / ``revalidate`` decision -> real OOS gate -> lineage registration.

    Mutate builds structural children of a ``factor_rule_v1`` parent and judges
    each on the untouched out-of-sample window with the §10 ReplicationGate
    (real backtest, deflated Sharpe, family trial counts); only passing children
    are registered as candidates with lineage to the parent.  Revalidate judges
    the existing artifact the same way and registers nothing.  Every path ends
    in a terminal ``outcome`` on the run.
    """
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.alpha_replication import evolution_executor as executor
    from services.research.alpha_replication import evolution_search as search

    action = _evolution_action(params)
    decision_id = str(params["decision_id"])
    target_artifact_id = str(params["target_artifact_id"])
    tenant_id = str(params.get("tenant_id") or "").strip()
    registry_url = os.getenv("REGISTRY_URL", "http://registry:8087").rstrip("/")
    _retrain_record(run_id, status="running")
    if not tenant_id:
        raise _RetrainFailure("resolve_tenant", "run parameters carry no decision tenant_id")
    prior = [
        r for r in get_store().list_runs()
        if r.get("run_id") != run_id
        and str((r.get("parameters") or {}).get("decision_id") or "") == decision_id
        and str(r.get("tenant_id") or (r.get("parameters") or {}).get("tenant_id") or "") == tenant_id
        and r.get("outcome") in {"candidate_created", "no_candidate", "revalidation_passed", "revalidation_failed"}
    ]
    if prior:  # an earlier attempt of this decision already reached its terminal outcome: adopt it
        done = max(prior, key=lambda r: str(r.get("updated_at") or ""))
        if params.get("pool_member"):
            if done["outcome"] == "candidate_created" and not done.get("evolution_child_context"):
                raise _RetrainFinished("no_candidate", "recorded_child_case_file_missing")
            _retrain_record(run_id, **{k: done[k] for k in (
                "evolution_case_file", "evolution_child_context", "evolution_result", "evolution_case_file_refusal"
            ) if k in done})
        raise _RetrainFinished(str(done["outcome"]), str(done.get("outcome_reason") or "resumed_from_recorded_outcome"))

    def read(step: str, path: str) -> Any:
        return _retrain_call(
            step=step, service="registry", method="GET", token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
            url=f"{registry_url}{path}", tenant_id=tenant_id,
        )

    try:
        view = read("read_target_artifact", f"/api/registry/strategy-artifacts/{urllib.parse.quote(target_artifact_id, safe='')}")
    except _OwnerRejection as exc:
        raise _RetrainFinished("evaluation_rejected", f"target artifact rejected by Registry: {exc.detail}") from None
    entry = (view or {}).get("entry") if isinstance(view, dict) else None
    entry_meta = (entry or {}).get("metadata") if isinstance(entry, dict) else None
    parent = (entry_meta or {}).get("strategy_artifact") if isinstance(entry_meta, dict) else None
    if not isinstance(parent, dict):
        raise _RetrainFailure("read_target_artifact", "Registry entry carries no strategy_artifact")
    family_id = str(parent.get("strategy_id") or parent.get("artifact_id"))
    if params.get("pool_member") and action == "revalidate":
        from services.evolution.approved_baselines import resolve_member_baseline
        ctx = params["pool_member"]
        baseline = resolve_member_baseline(tenant_id=tenant_id, persona_id=ctx["persona_id"],
            artifact_ref=ctx["artifact_ref"], checksum=ctx["checksum"], entry=entry)
        dataset = _RETRAIN_DATASET_LOADER(parent, params) if callable(_RETRAIN_DATASET_LOADER) else None
        if not isinstance(dataset, dict):
            raise _RetrainFinished("evaluation_rejected", "governed_post_admission_dataset_unavailable")
        try:
            result = executor.revalidate_member(artifact=parent, dataset=dataset, member=ctx, baseline=baseline)
        except executor.ExecutorRefusal as exc:
            raise _RetrainFinished(exc.outcome, exc.reason) from None
        if result["outcome"] == "revalidation_failed":
            owner = _pool_owner(params)
            pool = owner.pool(ctx["persona_id"])
            member = next((m for m in pool["members"] if m["artifact_ref"] == ctx["artifact_ref"]
                           and m["checksum"] == ctx["checksum"] and m["joined_at"] == ctx["joined_at"]), None)
            if member is None:
                raise _RetrainFailure("pool_revalidation", "approved member identity changed")
            owner.remove_member(pool, member, reason="evolution:" + decision_id)
        _retrain_record(run_id, evolution_result=result["verdict"])
        raise _RetrainFinished(result["outcome"], result["reason"])

    case_file, child_context = _evolution_member_case_context(run_id, parent, params) if action == "mutate" else (None, {})
    gate, factory = _evolution_oos_context(
        parent=parent, entry_meta=entry_meta, params=params, tenant_id=tenant_id, decision_id=decision_id,
        family_id=family_id, read=read,
    )

    def register_child(child: Dict[str, Any], verdict: Dict[str, Any]) -> Any:
        child_id = str(child["artifact_id"])

        def read_existing(artifact_id: str) -> Any:
            try:
                return read("register_candidate", f"/api/registry/strategy-artifacts/{urllib.parse.quote(artifact_id, safe='')}")
            except _OwnerRejection:
                return None

        existing = read_existing(child_id)
        if existing is None:
            try:
                existing = _retrain_call(
                    step="register_candidate", service="registry", method="POST",
                    token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN", tenant_id=tenant_id,
                    url=f"{registry_url}/api/registry/strategy-artifacts",
                    body={
                        "registry_id": child_id,
                        "artifact_state": "candidate",
                        "strategy_artifact": child,
                        "producer_run_id": run_id,
                        **_producer_proof_registration(
                            data_dir=Path(ALPHA_REPLICATION_DATA_DIR), child=child, verdict=verdict,
                            decision_id=decision_id, run_id=run_id, gate=gate,
                        ),
                        "metadata": {**{k: v for k, v in entry_meta.items() if k == "persona_id"}, **child_context},
                    },
                )
            except _OwnerRejection as exc:
                if exc.status == 409:
                    again = read_existing(child_id)
                    if again is not None:  # an earlier attempt already registered this very child: adopt it
                        existing = again
                    else:  # immutable (strategy, version, type) taken by another registration: re-allocate
                        from services.research.alpha_replication.evolution_search import RegistryVersionConflict

                        raise RegistryVersionConflict(str(exc.detail)) from None
                else:
                    raise _RetrainFinished("evaluation_rejected", f"candidate rejected by Registry: {exc.detail}") from None
        getattr(gate, "release", lambda _run: None)(verdict.get("backtest_run_id"))  # adopted/registered: release
        if str(((existing or {}).get("entry") or {}).get("artifact_state") or "") not in (
                {"candidate", "approved"} if params.get("pool_member") else {"candidate"}):
            raise _RetrainFailure("register_candidate", "Registry did not report the child as a candidate")
        run = get_store().get_run(run_id) or {}
        writebacks = list(run.get("registry_writebacks") or [])
        writebacks.append({"registry_id": child_id, "artifact_state": "candidate", "created_at": utc_now()})
        _admit_evolution_child(params, child_id)
        _retrain_record(run_id, registry_writebacks=writebacks, candidate_artifact_id=child_id)
        return existing

    prev = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        if action == "mutate":
            result = executor.execute_mutate(
                parent, decision_id=decision_id, family_id=family_id, gate=gate,
                request_factory=factory, register_child=register_child,
                family_versions=lambda: _registry_family_versions(registry_url, tenant_id, parent),
                children=50 if params.get("pool_member") else 1,
                parent_registry_ref=target_artifact_id,
                case_file=case_file,
                declare_lessons=lambda _child, _case: child_context.get("addresses_lessons"),
            )
        else:
            result = executor.execute_revalidate(parent, family_id=family_id, gate=gate, request_factory=factory)
    except executor.ExecutorRefusal as refusal:
        raise _RetrainFinished(refusal.outcome, refusal.reason) from None
    except search_errors() as exc:  # noqa: B030 - bounded, named below
        raise _RetrainFinished("evaluation_rejected", f"oos_evaluation_unavailable: {exc}") from None
    finally:
        if prev is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = prev
    _retrain_record(
        run_id, evolution_result=result.get("detail") or result.get("verdict"),
        parent_artifact_checksum=strategy_artifact_checksum(parent),
        family_trial_count=gate.get_family_trial_count(search.artifact_trial_key(family_id, parent)),
    )
    raise _RetrainFinished(result["outcome"], result["reason"])


def search_errors() -> tuple:
    from services.research.alpha_replication.evolution_search import EvolutionSearchError

    return (EvolutionSearchError, ValueError)


_retrain_active_runs: set = set()
_retrain_active_lock = threading.Lock()


def _trigger_retrain_execution(run_id: str, params: dict) -> None:
    with _retrain_active_lock:
        if run_id in _retrain_active_runs:
            return  # this process already executes the run; never start a second executor for it
        _retrain_active_runs.add(run_id)

    def run_worker():
        rstore = get_store()
        template_run = _is_template_search_run(rstore.get_run(run_id) or {})
        label = "Template search" if template_run else "Retrain"
        try:
            if template_run:
                _execute_template_search(run_id, params)
            elif _evolution_action(params) == "retrain":
                _execute_retrain(run_id, params)
            else:
                _execute_evolution_gate(run_id, params)
        except _RetrainFinished as done:
            if template_run and (rstore.get_run(run_id) or {}).get("cancellation_fence"):
                return
            _retrain_record(run_id, outcome=done.outcome, outcome_reason=done.reason)
            _finish_run(
                run_id, "completed",
                f"{label} completed: outcome={done.outcome}, outcome_reason={done.reason}.",
                _RETRAIN_SOURCE_SERVICE,
            )
        except _RetrainFailure as failure:
            if template_run and (rstore.get_run(run_id) or {}).get("cancellation_fence"):
                return
            _retrain_record(
                run_id, failed_step=failure.step,
                rejection={"reason": "retrain_failed", "step": failure.step, "detail": failure.detail},
            )
            _finish_run(
                run_id, "failed", f"{label} failed at step {failure.step}: {failure.detail}",
                _RETRAIN_SOURCE_SERVICE,
            )
        except Exception as exc:  # noqa: BLE001 - every attempt must reach a terminal state.
            if template_run and (rstore.get_run(run_id) or {}).get("cancellation_fence"):
                return
            logger.exception("%s executor crashed for %s", label, run_id)
            detail = f"{type(exc).__name__}: {exc}"
            if rstore.get_run(run_id):
                _retrain_record(
                    run_id, failed_step="executor",
                    rejection={"reason": "retrain_failed", "step": "executor", "detail": detail},
                )
                _finish_run(run_id, "failed", f"{label} failed at step executor: {detail}", _RETRAIN_SOURCE_SERVICE)
        finally:
            with _retrain_active_lock:
                _retrain_active_runs.discard(run_id)

    threading.Thread(target=run_worker, name=f"retrain-executor-{run_id}").start()


# --- Persona-written research demands (research_demand.v1) ---
# The persona's daily pool cycle writes demands here; this owner keeps and serves
# them. Nothing in this service allocates capital or binds personas to discovery.
from services.research.research_demand import DemandError, ResearchDemandStore  # noqa: E402
from services.research.query_plan import QueryPlanError, QueryPlanStore  # noqa: E402

research_demand_store = ResearchDemandStore(store.data_dir / "research_demands.json")
query_plan_store = QueryPlanStore(store.data_dir / "collector_query_plans.json")


def _template_search_context(demand: Dict[str, Any]) -> Dict[str, Any]:
    """Read the mandate universe and registered templates from their owners."""
    tenant_id = demand["tenant_id"]
    persona_url = os.getenv("PERSONA_API_URL", "http://persona:8002").rstrip("/")
    persona = _retrain_call(
        step="template_mandate", service="persona", method="GET", tenant_id=tenant_id,
        token_variable="RESEARCH_PERSONA_SERVICE_TOKEN",
        url=f"{persona_url}/api/personas/{urllib.parse.quote(demand['persona_id'], safe='')}")
    if persona.get("tenant_id") != tenant_id or persona.get("persona_id") != demand["persona_id"]:
        raise ValueError("template_mandate_owner_mismatch")
    scope = persona.get("market_scope") or (persona.get("metadata") or {}).get("market_scope") or {}
    universe = scope.get("symbols")
    if not universe:
        raise ValueError("mandate_universe_missing")
    dataset = _RETRAIN_DATASET_LOADER({"strategy_id": demand["demand_id"]}, {}) if callable(_RETRAIN_DATASET_LOADER) else None
    if not dataset:
        raise ValueError("governed_market_data_missing")
    registry_url = os.getenv("PANTHEON_REGISTRY_URL", "http://registry:8087").rstrip("/")
    templates = _retrain_call(
        step="template_registry", service="registry", method="GET", tenant_id=tenant_id,
        token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN", url=f"{registry_url}/api/registry/alpha-templates?scope=time_series")
    return {"universe": universe, "dataset": dataset, "templates": templates}


def _is_template_search_run(run: Dict[str, Any]) -> bool:
    # This adapter is only recorded by authenticated demand reconciliation;
    # dispatch_run rejects it as unknown. Caller-supplied stub parameters do
    # not grant execution on startup or retry.
    return (run.get("adapter") == "template_demand_gate"
            and (run.get("parameters") or {}).get("action_type") == "template_search")


def _execute_template_search(run_id: str, params: Dict[str, Any]) -> None:
    run = get_run(run_id)
    if run.get("cancellation_fence"):
        return
    demand = next((row for row in research_demand_store.list(params["tenant_id"], params["persona_id"])
                   if row["demand_id"] == params["demand_id"]), None)
    if not demand:
        raise _RetrainFailure("template_search", "research_demand_missing")
    run.update(status="running", updated_at=utc_now())
    store.put_run(run)
    from services.research.template_demand_search import TemplateDemandSearch
    from services.research.alpha_replication.shared_stores import SharedBacktestRunStore, SharedReplicationGate

    data_dir = Path(ALPHA_REPLICATION_DATA_DIR)
    gate = SharedReplicationGate(trial_counter_path=_retrain_trial_counter_path(),
                                 backtest_run_store=SharedBacktestRunStore(data_dir / "backtest_runs.json"))
    search = TemplateDemandSearch(store=research_demand_store, gate=gate,
                                  context_loader=_template_search_context, lock_path=data_dir / "template_search.lock",
                                  run_budget=1)
    row = search.run(demand)
    result = (row.get("discovery") or {}).get("template_search") or {}
    run = get_run(run_id)
    if run.get("cancellation_fence"):
        return
    run["template_search"] = {"demand_ref": demand["demand_id"], **result}
    store.put_run(run)
    reason = result.get("reason", "demand_closed")
    if reason.startswith("template_search_unavailable:"):
        raise _RetrainFailure("template_search", reason)
    raise _RetrainFinished("template_search_completed", reason)


def _search_demand_templates(rows: List[Dict[str, Any]]) -> None:
    """Record normal Research runs; never perform owner I/O or backtests here."""
    for row in rows:
        if row["status"] != "open":
            continue
        # An unconfigured owner cannot execute a search. Record the same
        # unavailable outcome without creating Research tasks that can never
        # start; credential-file validation and all owner I/O stay in worker.
        missing = next((name for name in ("RESEARCH_PERSONA_SERVICE_TOKEN", "RESEARCH_REGISTRY_SERVICE_TOKEN")
                        if not (os.getenv(name, "").strip() or os.getenv(name + "_FILE", "").strip())), None)
        if missing:
            research_demand_store.record_template_search(
                row["tenant_id"], row["demand_id"],
                {"run_at": utc_now(), "attempts": {}, "paper_only": True,
                 "reason": "template_search_unavailable:owner_credential_unconfigured:" + missing})
            continue
        with _plan_progress_lock:
            runs = store.list_runs()
            matching = [r for r in runs if _is_template_search_run(r)
                        and (r.get("parameters") or {}).get("demand_id") == row["demand_id"]
                        and r.get("tenant_id") == row["tenant_id"]]
            active = next((r for r in matching if r.get("status") in ACTIVE_STATUSES), None)
            if active:
                run = active
            else:
                if sum(r.get("status") in ACTIVE_STATUSES for r in runs) >= MAX_ACTIVE_RUNS:
                    continue  # next normal persona cycle retries; no separate queue
                timestamp = utc_now()
                task = create_task(CreateTaskBody(
                    title="Search registered templates for " + row["demand_id"],
                    objective="Compile mandate-compatible templates through the replication gate",
                    actor_id="persona-pool-cycle",
                    constraints={"tenant_id": row["tenant_id"], "persona_id": row["persona_id"]},
                    idempotency_key="template-search:" + row["tenant_id"] + ":" + row["demand_id"]))
                if task.get("cancellation_fence"):
                    continue
                run_id = _next_id("rrun", timestamp, {r["run_id"] for r in runs})
                event = _event(timestamp, "run_queued", "Template search queued for the replication gate.",
                               "persona-pool-cycle", run_id, [])
                run = {"id": run_id, "run_id": run_id, "task_id": task["task_id"],
                       "adapter": "template_demand_gate", "requested_mode": "replication_gate",
                       "dispatch_mode": "replication_gate", "production_activation": "disabled",
                       "status": "queued", "tenant_id": row["tenant_id"], "created_by": "persona-pool-cycle",
                       "paper_only": True, "attempt_number": 1, "parent_run_id": None, "root_run_id": run_id,
                       "parameters": {"action_type": "template_search", "tenant_id": row["tenant_id"],
                                      "persona_id": row["persona_id"], "demand_id": row["demand_id"]},
                       "created_at": timestamp, "updated_at": timestamp, "events": [event],
                       "artifact_refs": [], "proposal_refs": [], "registry_writebacks": []}
                task.update(status="running", updated_at=timestamp)
                store.put_task(task)
                store.put_run(run)
                store.append_event(event)
        _trigger_retrain_execution(run["run_id"], run["parameters"])


def _refresh_collector_query_plan(tenant_id: str) -> None:
    try:
        query_plan_store.refresh(tenant_id, research_demand_store.list(tenant_id, status="open"))
    except QueryPlanError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _expire_collector_query_plans() -> None:
    # Demand expiry is time-based and has no write event: re-evaluate on read.
    for tenant_id in query_plan_store.tenants():
        try:
            _refresh_collector_query_plan(tenant_id)
        except HTTPException:
            pass


@app.post("/api/research/persona-demands", status_code=201)
def open_persona_research_demand(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(_authenticated_persona_demand_writer_tenant),
) -> Dict[str, Any]:
    try:
        result = research_demand_store.open(tenant_id, body)
        _refresh_collector_query_plan(tenant_id)
        return result
    except DemandError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/research/persona-demands")
def list_persona_research_demands(
    persona_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    tenant_id: str = Depends(_authenticated_alpha_replication_tenant),
) -> Dict[str, Any]:
    rows = research_demand_store.list(tenant_id, persona_id, status)
    return {"demands": rows, "count": len(rows)}


@app.post("/api/research/persona-demands/reconcile")
def reconcile_persona_research_demands(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(_authenticated_persona_demand_writer_tenant),
) -> Dict[str, Any]:
    persona_id = str(body.get("persona_id") or "")
    if not persona_id:
        raise HTTPException(status_code=422, detail="persona_id is required")
    rows = research_demand_store.reconcile(tenant_id, persona_id, list(body.get("members") or []))
    _search_demand_templates(rows)
    _refresh_collector_query_plan(tenant_id)
    return {"demands": rows, "count": len(rows)}


@app.post("/api/research/persona-demands/{demand_id}/actions")
def record_persona_research_demand_discovery(
    demand_id: str,
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(_authenticated_persona_demand_writer_tenant),
) -> Dict[str, Any]:
    try:
        return research_demand_store.record_discovery(tenant_id, demand_id, body)
    except DemandError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc


@app.on_event("startup")
def resume_retrain_runs() -> None:
    """Resume in place every retrain run a restart left queued or running.

    The run, its session and its evaluation job ids are the ones already
    recorded: the same executor continues from them, so no new run, second
    session or second child is created. Template searches use this same worker.
    """
    for record in get_store().list_runs():
        status = str(record.get("status") or "").lower()
        params = record.get("parameters") or {}
        if status not in {"queued", "running"} or not isinstance(params, dict) or not (_is_retrain_action(params) or _is_template_search_run(record)):
            continue
        if record.get("cancellation_fence"):
            continue
        _trigger_retrain_execution(str(record["run_id"]), params)


@app.on_event("startup")
def repair_retrain_tasks_left_running() -> None:
    """Bring a task left running by an earlier terminal retrain run to that status.

    Only the task is written: the run, its events and its session are untouched,
    and no run, session or child is created.  A task that still has an active
    run is left running.
    """
    runs = store.list_runs()
    active_tasks = {r.get("task_id") for r in runs if str(r.get("status") or "").lower() in ACTIVE_STATUSES}
    latest: Dict[Any, Dict[str, Any]] = {}
    for run in runs:
        params = run.get("parameters") or {}
        if (
            not isinstance(params, dict)
            or not (_is_retrain_action(params) or _is_template_search_run(run))
            or str(run.get("status") or "").lower() not in {"completed", "failed"}
        ):
            continue
        current = latest.get(run.get("task_id"))
        if current is None or str(run.get("updated_at") or "") >= str(current.get("updated_at") or ""):
            latest[run.get("task_id")] = run
    for task_id, run in latest.items():
        task = store.get_task(task_id) if task_id else None
        if task and task_id not in active_tasks and str(task.get("status") or "").lower() == "running":
            _set_task_terminal(task_id, str(run["status"]).lower(), utc_now())


@app.get("/api/research-orchestrator/runs")
def list_runs(task_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        r for r in store.list_runs()
        if (not task_id or r.get("task_id") == task_id)
        and (not status or str(r.get("status") or "").lower() == status.lower())
    ]


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
        "attempt_number": run.get("attempt_number", 1),
        "parent_run_id": run.get("parent_run_id"),
        "root_run_id": run.get("root_run_id"),
        "cancellation_fence": run.get("cancellation_fence"),
        "adapter": run["adapter"],
        "requested_mode": run["requested_mode"],
        "dispatch_mode": run["dispatch_mode"],
        "production_activation": run["production_activation"],
        "rejection": run.get("rejection"),
        "gateway_ref": run.get("gateway_ref"),
        "artifact_refs": run.get("artifact_refs", []),
        "proposal_refs": run.get("proposal_refs", []),
        "registry_writebacks": run.get("registry_writebacks", []),
        "events": run.get("events", []),
        "updated_at": run.get("updated_at"),
    }


def _set_task_terminal(task_id: Any, status: str, timestamp: str) -> None:
    task = store.get_task(task_id)
    if task:
        task["status"] = status
        task["updated_at"] = timestamp
        store.put_task(task)


def _finish_run(
    run_id: str, status: str, summary: str, actor_id: str, completed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """The one terminal transition of a research run.

    Appends ``run_completed``, sets the run's terminal status and brings its
    research task to the same status.  ``POST .../complete`` and the retrain
    executor both end a run through here.
    """
    run = get_run(run_id)
    timestamp = completed_at or utc_now()
    events = list(run.get("events") or [])
    events.append(_event(timestamp, "run_completed", summary, actor_id, run_id, events))
    run["status"] = status
    run["completed_at"] = timestamp
    run["updated_at"] = timestamp
    run["events"] = events
    _set_task_terminal(run["task_id"], status, timestamp)
    store.put_run(run)
    store.append_event(events[-1])
    params = run.get("parameters")
    if (
        status == "completed"
        and isinstance(params, dict)
        and params.get("record_type") == "ExperimentRun"
        and isinstance(params.get("experiment_run"), dict)
    ):
        exp_run = params["experiment_run"]
        exp_meta = exp_run.get("metadata") if isinstance(exp_run.get("metadata"), dict) else {}
        bt_summary = (
            exp_meta.get("backtest_summary")
            if isinstance(exp_meta.get("backtest_summary"), dict)
            else {}
        )
        if bt_summary:
            tx_costs = (
                bt_summary.get("transaction_costs")
                if isinstance(bt_summary.get("transaction_costs"), dict)
                else {}
            )
            try:
                if (exp_meta.get("replication_gate") or {}).get("passed") is not True:
                    raise ResearchMemoryEligibilityError("Replication gate has not passed; memory finding refused")
                outbox_store = get_outbox_store()
                outbox_id = _next_id(
                    "mout",
                    timestamp,
                    {r.outbox_id for r in outbox_store.list_records()},
                )
                outbox_record = ResearchMemoryOutboxRecord(
                    outbox_id=outbox_id,
                    run_id=str(exp_run.get("run_id") or run_id),
                    task_id=str(exp_run.get("task_id") or run["task_id"]),
                    artifact_id=str(bt_summary.get("backtest_run_id") or "") or None,
                    source_event_type="research_finding_published",
                    source_event_id=str(exp_run.get("run_id") or run_id),
                    sponsor_persona_id=_memory_sponsor(run),
                    summary=summary,
                    headline=f"Replicated Taiwan strategy {exp_run.get('strategy_id') or ''}",
                    confidence=1.0,
                    evidence_refs=list(run.get("artifact_refs") or run.get("input_refs") or []),
                    dataset_refs=[str(exp_run.get("dataset_version_id") or "")],
                    license_scope="governed_tw_market",
                    allowed_use=["research_memory", "replication_evidence"],
                    supersedes=[],
                    contradicts=[],
                    expires_at=None,
                    trace_id=str(exp_run.get("trace_id") or run_id),
                    status="pending",
                    created_at=timestamp,
                    updated_at=timestamp,
                    metadata={
                        "event_type": "research_finding_published",
                        "tenant_id": exp_run.get("tenant_id"),
                        "strategy_spec_id": exp_run.get("strategy_spec_id"),
                        "strategy_id": exp_run.get("strategy_id"),
                        "family_id": bt_summary.get("family_id"),
                        "trial_count": bt_summary.get("trial_count"),
                        "transaction_costs": dict(tx_costs),
                        "metrics": {
                            "sharpe_ratio": bt_summary.get("out_of_sample_sharpe_ratio"),
                            "in_sample_sharpe_ratio": bt_summary.get("in_sample_sharpe_ratio"),
                            "out_of_sample_sharpe_ratio": bt_summary.get("out_of_sample_sharpe_ratio"),
                            "deflated_sharpe_ratio": bt_summary.get("deflated_sharpe_ratio"),
                            "deflated_sharpe_threshold": bt_summary.get("deflated_sharpe_threshold"),
                            "trial_count": bt_summary.get("trial_count"),
                            "max_drawdown": bt_summary.get("out_of_sample_max_drawdown"),
                            "total_return": bt_summary.get("out_of_sample_total_return"),
                            "round_trips": bt_summary.get("out_of_sample_round_trips"),
                            "buy_and_hold_sharpe_ratio": bt_summary.get("buy_and_hold_sharpe_ratio"),
                            "tw_0050_sharpe_ratio": bt_summary.get("tw_0050_sharpe_ratio"),
                            "walk_forward_fold_sharpes": bt_summary.get("walk_forward_fold_sharpes"),
                            "commission_rate": tx_costs.get("commission_rate"),
                            "equity_transaction_tax_rate": tx_costs.get("equity_transaction_tax_rate"),
                            "etf_transaction_tax_rate": tx_costs.get("etf_transaction_tax_rate"),
                            "backtest_run_id": bt_summary.get("backtest_run_id"),
                        },
                        "artifact_refs": list(run.get("artifact_refs") or []),
                    },
                )
                outbox_store.create_record(outbox_record)
            except Exception as exc:
                run["memory_writeback_error"] = str(exc)
                store.put_run(run)
                logger.warning("Memory enqueue refused for %s: %s", run_id, exc)
    return run


@app.post("/api/research-orchestrator/runs/{run_id}/complete")
def complete_run(run_id: str, body: CompleteRunBody) -> Dict[str, Any]:
    run = get_run(run_id)
    if str(run.get("status") or "").lower() == "rejected":
        raise HTTPException(status_code=409, detail="rejected research run cannot be completed")
    run_status = str(run.get("status") or "").lower()
    if run_status == "canceled" or run.get("cancellation_fence"):
        timestamp = body.completed_at or utc_now()
        events = list(run.get("events") or [])
        events.append(
            _event(
                timestamp,
                "late_completion_discarded",
                f"Late completion rejected: run was already canceled. Summary: {body.summary}",
                body.actor_id,
                run_id,
                events,
            )
        )
        run["events"] = events
        discarded = list(run.get("discarded_completions") or [])
        discarded.append({"completed_at": timestamp, "summary": body.summary, "actor_id": body.actor_id})
        run["discarded_completions"] = discarded
        store.put_run(run)
        store.append_event(events[-1])
        raise HTTPException(
            status_code=409,
            detail="canceled research run cannot be completed; late completion fenced",
        )
    return _finish_run(run_id, body.status, body.summary, body.actor_id, body.completed_at)


@app.post("/api/research-orchestrator/runs/{run_id}/cancel")
def cancel_run(run_id: str, body: Optional[CancelRunBody] = None) -> Dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="research run not found")
    status = str(run.get("status") or "").lower()
    if status == "canceled":
        return run
    if status in ("completed", "rejected"):
        raise HTTPException(
            status_code=409,
            detail=f"terminal research run in status '{status}' cannot be canceled",
        )
    b = body or CancelRunBody()
    timestamp = b.canceled_at or utc_now()
    events = list(run.get("events") or [])
    events.append(
        _event(
            timestamp,
            "run_canceled",
            b.reason or "Run canceled",
            b.actor_id,
            run_id,
            events,
        )
    )
    with _plan_progress_lock:
        run.update({"status": "canceled", "completed_at": timestamp, "cancellation_fence": timestamp, "updated_at": timestamp, "events": events})
        task = store.get_task(run["task_id"])
        if task:
            sibling_runs = [r for r in store.list_runs() if r.get("task_id") == run["task_id"] and r.get("run_id") != run_id]
            if not any(str(r.get("status") or "").lower() in ACTIVE_STATUSES for r in sibling_runs):
                task.update({"status": "canceled", "cancellation_fence": task.get("cancellation_fence") or timestamp, "updated_at": timestamp})
                store.put_task(task)

        store.put_run(run)
        store.append_event(events[-1])
        _cancel_stage_claims({run_id}, timestamp)
    return run


@app.post("/api/research-orchestrator/runs/{run_id}/retry", status_code=201)
def retry_run(run_id: str, body: Optional[RetryRunBody] = None) -> Dict[str, Any]:
    with _plan_progress_lock:
        new_run, replayed = _create_retry_run(run_id, body)
    if replayed:
        return new_run
    b = body or RetryRunBody()
    timestamp = b.requested_at or utc_now()
    params = new_run.get("parameters") or {}
    plan_payload = params.get("plan") or {}
    stage_payload = params.get("stage") or {}
    if str(plan_payload.get("status") or "").lower() in {"canceled", "cancelled"}:
        plan_payload["status"] = "running"
    if isinstance(plan_payload.get("stages"), list):
        for stage in plan_payload["stages"]:
            if isinstance(stage, dict) and stage.get("stage_id") == stage_payload.get("stage_id"):
                if params.get("dataset"):
                    stage["dataset"] = params["dataset"]
                break
    adapter = new_run.get("adapter", "stub")
    supported_stage_backends = {"vectorbt", "statsmodels", "quantlib", "openclaw_result_synthesis", "prototype_backtest", "econometric_validation", "derivatives_pricing_risk", "evidence_synthesis"}
    is_stage_backend = adapter in supported_stage_backends or params.get("stage") is not None
    if _is_retrain_action(params) or _is_template_search_run(new_run):
        _trigger_retrain_execution(new_run["run_id"], params)
    elif is_stage_backend and (params.get("stage") or (params.get("plan") and params["plan"].get("stages"))):
        _progress_plan_stages(params.get("plan") or {}, new_run, b.actor_id, store, timestamp)
    return new_run


def _create_retry_run(run_id: str, body: Optional[RetryRunBody]) -> tuple[Dict[str, Any], bool]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="research run not found")
    status = str(run.get("status") or "").lower()
    eligible_statuses = {"failed", "canceled", "timeout"}
    if status not in eligible_statuses:
        raise HTTPException(
            status_code=409,
            detail=f"only runs in eligible terminal states ({', '.join(sorted(eligible_statuses))}) can be retried; run '{run_id}' is in status '{status}'",
        )
    b = body or RetryRunBody()
    if b.idempotency_key:
        scoped = [
            r for r in store.list_runs()
            if r.get("parent_run_id") == run_id
            and r.get("task_id") == run.get("task_id")
            and r.get("created_by") == (b.actor_id or run.get("created_by"))
        ]
        existing = _idempotent_match(scoped, b.idempotency_key)
        if existing:
            return existing, True

    timestamp = b.requested_at or utc_now()
    task_id = run["task_id"]
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="parent research task not found")

    attempt_number = int(run.get("attempt_number") or 1) + 1
    parent_run_id = run["run_id"]
    root_run_id = run.get("root_run_id") or run["run_id"]

    new_run_id = _next_id("rrun", timestamp, {str(r.get("run_id") or "") for r in store.list_runs()})

    events: List[Dict[str, Any]] = []
    events.append(
        _event(
            timestamp,
            "run_queued",
            f"Retry attempt #{attempt_number} queued (retrying parent run {parent_run_id}).",
            b.actor_id,
            new_run_id,
            events,
        )
    )

    new_run: Dict[str, Any] = {
        "id": new_run_id, "run_id": new_run_id, "task_id": task_id, "stage_id": run.get("stage_id"),
        "attempt_number": attempt_number, "parent_run_id": parent_run_id, "root_run_id": root_run_id,
        "adapter": run.get("adapter", "stub"), "requested_mode": run.get("requested_mode", "stub"),
        "dispatch_mode": run.get("dispatch_mode", "stub"), "status": "queued",
        "production_activation": "disabled", "input_refs": run.get("input_refs", []),
        "parameters": run.get("parameters", {}), "created_by": b.actor_id or run.get("created_by"),
        "tenant_id": run.get("tenant_id") or task.get("tenant_id"), "user_id": run.get("user_id") or task.get("user_id"),
        "created_at": timestamp, "updated_at": timestamp, "idempotency_key": b.idempotency_key,
        "events": events, "artifact_refs": [], "proposal_refs": [], "registry_writebacks": [],
    }

    old_events = list(run.get("events") or [])
    old_events.append(
        _event(
            timestamp,
            "run_retried",
            f"Run retried via new run {new_run_id} (attempt #{attempt_number}).",
            b.actor_id,
            run_id,
            old_events,
        )
    )
    run["events"] = old_events
    run["updated_at"] = timestamp
    store.put_run(run)
    store.append_event(old_events[-1])

    with _plan_progress_lock:
        task["status"] = "running"
        task["cancellation_fence"] = None
        task.pop("cancellation_fence", None)
        task["generation"] = int(task.get("generation") or 1) + 1
        task["updated_at"] = timestamp
        store.put_task(task)

        store.put_run(new_run)
        store.append_event(events[-1])

    return new_run, False


@app.post("/api/research-orchestrator/runs/{run_id}/artifacts", status_code=201)
def handoff_artifact(run_id: str, body: ArtifactBody) -> Dict[str, Any]:
    run = get_run(run_id)
    existing = _idempotent_match(store.list_artifacts(), body.idempotency_key)
    if existing:
        return existing
    timestamp = body.created_at or utc_now()
    artifact_id = _next_id("rart", timestamp, {str(artifact.get("artifact_id") or "") for artifact in store.list_artifacts()})
    quality = _artifact_quality(run, body)
    registry_projection = {
        "artifact_type": body.registry_hints.get("artifact_type", body.artifact_type),
        "artifact_state": body.registry_hints.get("artifact_state", "draft"),
        "deployment_stage": "none",
        "lineage": [{"type": "research_run", "id": run_id}],
        "storage_ref": body.storage_ref,
        "checksum": body.checksum,
        "quality": quality,
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
        "producer_mode": quality["producer_mode"],
        "artifact_origin": quality["artifact_origin"],
        "storage_status": quality["storage_status"],
        "checksum_status": quality["checksum_status"],
        "source_evidence_refs": quality["source_evidence_refs"],
        "evidence_eligible": quality["evidence_eligible"],
        "evidence_ineligibility_reasons": quality["evidence_ineligibility_reasons"],
        "quality": quality,
        "governance": {
            "direct_live_influence": False,
            "lean_consumption": "research_only_not_direct_action",
            "write_boundary": "research_plane_only",
        },
        "registry_hints": body.registry_hints,
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


@app.post("/api/research-orchestrator/runs/{run_id}/registry-writeback", status_code=201)
def writeback_run_artifact(run_id: str, body: RegistryWritebackBody) -> Dict[str, Any]:
    run = get_run(run_id)
    if str(run.get("status") or "").lower() != "completed":
        raise HTTPException(status_code=409, detail="only completed research runs can write artifacts to the registry")

    existing = _idempotent_match(list(run.get("registry_writebacks") or []), body.idempotency_key)
    if existing:
        return existing

    timestamp = body.created_at or utc_now()
    artifact = _resolve_writeback_artifact(run, body.artifact_id)
    writeback_quality = _assert_registry_writeback_eligible(run, artifact, body)
    registry_service = RegistryService(get_registry_store())
    try:
        experiment_run = _experiment_run_for_writeback(run, artifact, body, timestamp)
        view = write_experiment_run_artifact_to_registry(
            experiment_run,
            artifact,
            registry_service=registry_service,
            registry_id=body.registry_id,
            artifact_type=body.artifact_type,
            strategy_id=body.strategy_id,
            version=body.version,
            requested_artifact_state=body.requested_artifact_state,
            storage_ref=body.storage_ref,
            checksum=body.checksum,
            source_strategy_spec_id=body.source_strategy_spec_id,
            source_dataset_refs=body.source_dataset_refs,
            parent_registry_ids=body.parent_registry_ids,
            evaluation_summary=body.evaluation_summary,
            metadata={
                **body.metadata,
                "writeback_quality": writeback_quality,
                "writeback_actor": body.actor_id,
                "writeback_created_at": timestamp,
            },
        )
    except (ExperimentRegistryWritebackError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    registry_view = registry_entry_view_to_dict(view)
    entry = registry_view["entry"]
    writeback = {
        "id": entry["registry_id"],
        "registry_id": entry["registry_id"],
        "run_id": run_id,
        "task_id": run["task_id"],
        "artifact_id": artifact["artifact_id"],
        "artifact_type": entry["artifact_type"],
        "artifact_state": entry["artifact_state"],
        "deployment_stage": registry_view["deployment_stage"],
        "producer_run_id": entry.get("producer_run_id"),
        "lineage": entry.get("lineage", {}),
        "registry_view": registry_view,
        "created_by": body.actor_id,
        "created_at": timestamp,
        "idempotency_key": body.idempotency_key,
    }

    refs = list(run.get("registry_writebacks") or [])
    refs.append(writeback)
    events = list(run.get("events") or [])
    events.append(
        _event(
            timestamp,
            "registry_writeback_created",
            f"Registered run artifact {artifact['artifact_id']} as registry entry {entry['registry_id']}.",
            body.actor_id,
            run_id,
            events,
        )
    )
    run["registry_writebacks"] = refs
    run["events"] = events
    run["updated_at"] = timestamp
    artifact["registry_writeback"] = {
        "registry_id": entry["registry_id"],
        "artifact_state": entry["artifact_state"],
        "deployment_stage": registry_view["deployment_stage"],
        "created_at": timestamp,
        "quality": writeback_quality,
    }
    store.put_run(run)
    store.put_artifact(artifact)
    store.append_event(events[-1])

    # Auto-enqueue to memory writeback outbox if eligible
    try:
        if entry["artifact_state"] in {"candidate", "reviewed", "published"}:
            mem_eligibility = validate_research_memory_writeback_eligibility(
                run,
                artifact,
                {
                    "evidence_refs": writeback_quality.get("source_evidence_refs") or body.source_dataset_refs,
                    "dataset_refs": writeback_quality.get("source_dataset_refs") or body.source_dataset_refs,
                    "license_scope": body.metadata.get("license_scope"),
                    "allowed_use": body.metadata.get("allowed_use"),
                    "artifact_state": entry["artifact_state"],
                },
            )
            outbox_store = get_outbox_store()
            outbox_id = _next_id("mout", timestamp, {r.outbox_id for r in outbox_store.list_records()})
            outbox_record = ResearchMemoryOutboxRecord(
                outbox_id=outbox_id,
                run_id=run_id,
                task_id=run["task_id"],
                artifact_id=artifact["artifact_id"],
                source_event_type="research_finding_published",
                source_event_id=run_id,
                sponsor_persona_id=_memory_sponsor(run, body.metadata.get("sponsor_persona_id")),
                summary=_first_text(artifact.get("title"), f"Reviewed research finding from {run_id}") or f"Reviewed research finding from {run_id}",
                headline=_first_text(artifact.get("title"), f"Research Finding {artifact['artifact_id']}") or f"Research Finding {artifact['artifact_id']}",
                confidence=float(body.metadata.get("confidence") or 1.0),
                evidence_refs=mem_eligibility["evidence_refs"],
                dataset_refs=mem_eligibility["dataset_refs"],
                license_scope=mem_eligibility["license_scope"],
                allowed_use=mem_eligibility["allowed_use"],
                supersedes=body.metadata.get("supersedes") or [],
                contradicts=body.metadata.get("contradicts") or [],
                expires_at=body.metadata.get("expires_at"),
                trace_id=_first_text(run.get("trace_id"), run_id) or run_id,
                status="pending",
                created_at=timestamp,
                updated_at=timestamp,
                metadata={
                    "registry_id": entry["registry_id"],
                    "actor_id": body.actor_id,
                    "auto_enqueued": True,
                    "tenant_id": run.get("tenant_id"),
                },
            )
            outbox_store.create_record(outbox_record)
    except Exception as exc:
        run["memory_writeback_error"] = str(exc)
        store.put_run(run)
        logger.warning("Memory enqueue refused for %s: %s", run_id, exc)

    return writeback


@app.post("/api/research-orchestrator/runs/{run_id}/memory-writeback", status_code=201)
def writeback_run_memory(run_id: str, body: MemoryWritebackBody) -> Dict[str, Any]:
    run = get_run(run_id)
    if str(run.get("status") or "").lower() != "completed":
        raise HTTPException(status_code=409, detail="only completed research runs can write findings to memory")

    artifact = _resolve_writeback_artifact(run, body.artifact_id)
    try:
        eligibility = validate_research_memory_writeback_eligibility(
            run,
            artifact,
            body.model_dump(),
        )
    except ResearchMemoryEligibilityError as exc:
        raise HTTPException(status_code=400, detail={"reason": "memory_writeback_not_eligible", "detail": str(exc)})

    try:
        _memory_sponsor(run, body.sponsor_persona_id)
    except ResearchMemoryEligibilityError as exc:
        raise HTTPException(status_code=400, detail={"reason": "memory_attribution_missing_or_conflicting", "detail": str(exc)}) from exc
    outbox_store = get_outbox_store()
    timestamp = body.created_at or utc_now()
    existing = outbox_store.find_by_source_event("research_finding_published", run_id)
    if existing:
        if body.auto_deliver and existing.status in {"pending", "failed"}:
            worker = MemoryWritebackWorker(outbox_store=outbox_store)
            worker.deliver_record(existing.outbox_id)
            existing = outbox_store.get_record(existing.outbox_id) or existing
        return existing.to_dict()

    outbox_id = _next_id("mout", timestamp, {r.outbox_id for r in outbox_store.list_records()})
    record = ResearchMemoryOutboxRecord(
        outbox_id=outbox_id,
        run_id=run_id,
        task_id=run["task_id"],
        artifact_id=artifact.get("artifact_id"),
        source_event_type="research_finding_published",
        source_event_id=run_id,
        sponsor_persona_id=_memory_sponsor(run, body.sponsor_persona_id),
        summary=body.summary or str(artifact.get("title") or f"Reviewed finding for {run_id}"),
        headline=body.headline or str(artifact.get("title") or f"Finding {run_id}"),
        confidence=body.confidence,
        evidence_refs=eligibility["evidence_refs"],
        dataset_refs=eligibility["dataset_refs"],
        license_scope=eligibility["license_scope"],
        allowed_use=eligibility["allowed_use"],
        supersedes=body.supersedes,
        contradicts=body.contradicts,
        expires_at=body.expires_at,
        trace_id=body.trace_id or str(run.get("trace_id") or run_id),
        status="pending",
        created_at=timestamp,
        updated_at=timestamp,
        metadata={
            **body.metadata,
            "actor_id": body.actor_id,
            "idempotency_key": body.idempotency_key,
            "tenant_id": run.get("tenant_id"),
        },
    )
    saved = outbox_store.create_record(record)
    if body.auto_deliver:
        worker = MemoryWritebackWorker(outbox_store=outbox_store)
        worker.deliver_record(saved.outbox_id)
        saved = outbox_store.get_record(saved.outbox_id) or saved

    return saved.to_dict()


@app.get("/api/research-orchestrator/outbox/memory")
def list_memory_outbox(
    status: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
) -> List[Dict[str, Any]]:
    outbox_store = get_outbox_store()
    records = outbox_store.list_records(status=status, run_id=run_id)
    return [r.to_dict() for r in records]


@app.get("/api/research-orchestrator/outbox/memory/{outbox_id}")
def get_memory_outbox_record(outbox_id: str) -> Dict[str, Any]:
    outbox_store = get_outbox_store()
    record = outbox_store.get_record(outbox_id)
    if not record:
        raise HTTPException(status_code=404, detail="outbox record not found")
    return record.to_dict()


@app.post("/api/research-orchestrator/outbox/memory/{outbox_id}/retry")
def retry_memory_outbox_record(outbox_id: str) -> Dict[str, Any]:
    outbox_store = get_outbox_store()
    worker = MemoryWritebackWorker(outbox_store=outbox_store)
    res = worker.retry(outbox_id)
    if res.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="outbox record not found")
    return res


@app.post("/api/research-orchestrator/outbox/memory/drain")
def drain_memory_outbox(max_records: int = Query(default=50, ge=1, le=500)) -> Dict[str, Any]:
    outbox_store = get_outbox_store()
    worker = MemoryWritebackWorker(outbox_store=outbox_store)
    return worker.drain(max_records=max_records)


@app.post("/api/research-orchestrator/runs/{run_id}/retrieval-influence", status_code=201)
def record_run_retrieval_influence(run_id: str, body: RetrievalInfluenceBody) -> Dict[str, Any]:
    run = get_run(run_id)
    timestamp = body.created_at or utc_now()
    influence_store = get_influence_store()
    retrieval_id = _next_id("mret", timestamp, {r.retrieval_id for r in influence_store.list_records()})

    try:
        record = ResearchRetrievalInfluenceRecord(
            retrieval_id=retrieval_id,
            task_id=body.task_id or run["task_id"],
            run_id=run_id,
            persona_id=body.persona_id,
            query_snapshot=body.query_snapshot,
            selected_memory_refs=body.selected_memory_refs,
            selected_evidence_refs=body.selected_evidence_refs,
            counter_evidence_query=body.counter_evidence_query,
            counter_evidence_results=body.counter_evidence_results,
            influence_assessment=body.influence_assessment,
            influence_weight=body.influence_weight,
            influence_state=body.influence_state,
            model_ranker_version=body.model_ranker_version,
            resulting_seed_ref=body.resulting_seed_ref,
            created_at=timestamp,
            metadata=body.metadata,
        )
    except ResearchRetrievalInfluenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    saved = influence_store.create_record(record)
    return saved.to_dict()


@app.get("/api/research-orchestrator/runs/{run_id}/retrieval-influence")
def list_run_retrieval_influence(run_id: str) -> List[Dict[str, Any]]:
    get_run(run_id)
    influence_store = get_influence_store()
    records = influence_store.list_records(run_id=run_id)
    return [r.to_dict() for r in records]


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


from services.research.constants import ALLOWLISTED_STAGE_BACKENDS, ALLOWLISTED_STAGE_TYPES


def _first_present(*sources: Any) -> Any:
    for s in sources:
        if s is not None:
            return s
    return None


def _extract_artifact_ids(raw_refs: Any) -> List[str]:
    if not raw_refs:
        return []
    items = raw_refs if isinstance(raw_refs, (list, tuple, set)) else [raw_refs]
    out: List[str] = []
    for r in items:
        if isinstance(r, dict):
            rtype = str(r.get("type") or "").strip().lower()
            if rtype and rtype not in ("artifact", "evidence", "artifact_ref", "evidence_synthesis_artifact"):
                continue
            aid = str(r.get("artifact_id") or r.get("id") or "").strip()
            if aid:
                out.append(aid)
        elif isinstance(r, str) and r.strip():
            s = r.strip()
            out.append(s.split(":", 1)[-1] if s.startswith("artifact:") else s)
    return out


@app.post("/stages/{stage_type}/execute")
@app.post("/api/research-orchestrator/stages/{stage_type}/execute")
def execute_research_stage(
    stage_type: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
) -> Dict[str, Any]:
    """Execute an allowlisted research stage on the authentic research backend."""
    if not body or not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Missing required execution request body")

    stage = body.get("stage")
    plan = body.get("plan")
    context_map = body.get("context") if isinstance(body.get("context"), dict) else {}
    run_id = str(body.get("run_id") or context_map.get("run_id") or "").strip()
    correlation_id = str(
        body.get("correlation_id")
        or context_map.get("correlation_id")
        or (plan.get("correlation_id") if isinstance(plan, dict) else "")
        or ""
    ).strip()

    for field_name, val, is_dict in (("stage", stage, True), ("plan", plan, True), ("run_id", run_id, False), ("correlation_id", correlation_id, False)):
        if not val or (is_dict and not isinstance(val, dict)):
            detail = f"Missing or invalid required execution field: '{field_name}'" if is_dict else f"Missing required execution field: '{field_name}'"
            raise HTTPException(status_code=400, detail=detail)

    exec_storage_path = store.data_dir / "stage_executions.json"
    run_record = store.get_run(run_id)
    persisted_params = (run_record.get("parameters") or {}) if (run_record and isinstance(run_record.get("parameters"), dict)) else {}
    persisted_stage = persisted_params.get("stage") if isinstance(persisted_params.get("stage"), dict) else None
    persisted_plan = (persisted_params.get("plan") or {}) if isinstance(persisted_params.get("plan"), dict) else {}
    persisted_task_id = str((run_record or {}).get("task_id") or "").strip()

    rec_stage_id = str((run_record or {}).get("stage_id") or ((persisted_stage.get("stage_id") if persisted_stage else "") or "")).strip()
    if run_record and not rec_stage_id:
        for ref in (run_record.get("input_refs") or []):
            if isinstance(ref, dict) and ref.get("type") == "stage" and ref.get("id"):
                rec_stage_id = str(ref["id"]).strip()
                break
    caller_stage_id = str(stage.get("stage_id") or "").strip()
    if caller_stage_id and rec_stage_id and caller_stage_id != rec_stage_id:
        raise HTTPException(status_code=400, detail=f"Stage identity mismatch: run '{run_id}' stage '{rec_stage_id}' != '{caller_stage_id}'")
    stage_id = caller_stage_id or rec_stage_id
    if not stage_id:
        raise HTTPException(status_code=400, detail=f"Missing required stage identity: 'stage_id' must be specified in request or derived from stored run '{run_id}'")
    stage["stage_id"] = stage_id
    if persisted_stage is None and isinstance(persisted_plan.get("stages"), list):
        for s in persisted_plan["stages"]:
            if isinstance(s, dict) and str(s.get("stage_id") or "").strip() == stage_id:
                persisted_stage = s
                break
    if persisted_stage is None and "parameters" in persisted_params and isinstance(persisted_params.get("parameters"), dict):
        persisted_stage = {"parameters": persisted_params["parameters"]}

    if run_record:
        persisted_stage_type = str(
            (persisted_stage or {}).get("stage_type")
            or (persisted_stage or {}).get("type")
            or run_record.get("stage_type")
            or ""
        ).strip()
        if persisted_stage_type and stage_type != persisted_stage_type:
            raise HTTPException(
                status_code=400, detail=f"Stage type mismatch: run '{run_id}' stage type '{persisted_stage_type}' != '{stage_type}'"
            )
        caller_st_type = str(stage.get("stage_type") or stage.get("type") or "").strip()
        if caller_st_type and persisted_stage_type and caller_st_type != persisted_stage_type:
            raise HTTPException(
                status_code=400, detail=f"Stage type mismatch: run '{run_id}' caller stage type '{caller_st_type}' != '{persisted_stage_type}'"
            )

        caller_task_id = str(plan.get("task_id") or body.get("task_id") or "").strip()
        if caller_task_id and persisted_task_id and caller_task_id != persisted_task_id:
            raise HTTPException(
                status_code=400, detail=f"Task lineage mismatch: run '{run_id}' task '{persisted_task_id}' != caller '{caller_task_id}'"
            )

        persisted_plan_id = str(persisted_plan.get("plan_id") or persisted_plan.get("id") or "").strip()
        if not persisted_plan_id:
            for ref in (run_record.get("input_refs") or []):
                if isinstance(ref, dict) and ref.get("type") == "research_plan" and ref.get("id"):
                    persisted_plan_id = str(ref["id"]).strip()
                    break
        caller_plan_id = str(plan.get("plan_id") or plan.get("id") or body.get("plan_id") or "").strip()
        if caller_plan_id and persisted_plan_id and caller_plan_id != persisted_plan_id:
            raise HTTPException(
                status_code=400, detail=f"Plan lineage mismatch: run '{run_id}' plan '{persisted_plan_id}' != caller '{caller_plan_id}'"
            )

    effective_task_id = persisted_task_id or str(plan.get("task_id") or body.get("task_id") or f"task-{run_id}")
    effective_plan_id = (
        str(persisted_plan.get("plan_id") or persisted_plan.get("id") or "").strip()
        if run_record else ""
    ) or str(plan.get("plan_id") or plan.get("id") or body.get("plan_id") or "")

    stage_claim_key = f"agora-stage-claim:{run_id}:{stage_id}"
    claim_token = uuid.uuid4().hex

    def _persist_failure(exc: Any) -> None:
        try:
            with _stage_execution_cond:
                claim = store._get_record(exec_storage_path, stage_claim_key)
                if claim and claim.get("claim_token") == claim_token:
                    store._put_record(exec_storage_path, stage_claim_key, {"status": "failed", "error": str(exc), "updated_at": utc_now()})
                    rec = store.get_run(run_id)
                    if rec and isinstance(rec, dict) and str(rec.get("status") or "").lower() not in {"canceled", "cancelled"}:
                        rec["status"] = "failed"
                        rec["error"] = str(exc)
                        rec["updated_at"] = utc_now()
                        store.put_run(rec)
                _stage_execution_cond.notify_all()
        except Exception:
            pass

    def _fail_stage(detail: Any, status_code: int = 503, cause: Optional[Exception] = None) -> None:
        _persist_failure(detail)
        msg = getattr(detail, "detail", None) or getattr(detail, "message", None) or str(detail)
        raise HTTPException(status_code=status_code, detail=msg) from cause

    if stage_type not in ALLOWLISTED_STAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown or non-allowlisted research stage '{stage_type}'. Allowed: {sorted(ALLOWLISTED_STAGE_TYPES)}")

    backend_name = ALLOWLISTED_STAGE_BACKENDS.get(stage_type, stage_type)
    if os.getenv(f"AGORA_RESEARCH_{stage_type.upper()}_UNAVAILABLE") == "1" or os.getenv(f"AGORA_RESEARCH_{backend_name.upper()}_UNAVAILABLE") == "1":
        raise HTTPException(status_code=503, detail=f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable")

    SUPPORTED_EXECUTION_STAGES = {"prototype_backtest", "econometric_validation", "derivatives_pricing_risk", "evidence_synthesis"}
    if stage_type not in SUPPORTED_EXECUTION_STAGES and backend_name not in {"vectorbt", "statsmodels", "quantlib", "openclaw_result_synthesis"}:
        raise HTTPException(status_code=503, detail=f"Backend execution owner for stage '{stage_type}' ({backend_name}) is absent or not configured")

    persisted_refs = (
        ((persisted_stage or {}).get("input_refs") or [])
        + ((persisted_stage or {}).get("approved_input_refs") or [])
        + (run_record.get("input_refs") or [] if run_record else [])
    )
    persisted_ds_refs = _extract_dataset_refs(persisted_refs)
    caller_refs = (stage.get("input_refs") or []) + (stage.get("approved_input_refs") or [])
    caller_ds_refs = _extract_dataset_refs(caller_refs)
    if persisted_ds_refs and caller_ds_refs:
        clean_persisted = {(r.split(":", 1)[-1] if ":" in r else r) for r in persisted_ds_refs}
        for cr in caller_ds_refs:
            clean_cr = cr.split(":", 1)[-1] if ":" in cr else cr
            if clean_cr not in clean_persisted and cr not in persisted_ds_refs:
                raise HTTPException(
                    status_code=400,
                    detail=f"Conflicting input refs for stage '{stage_id}': caller supplied '{cr}' not in approved refs '{persisted_ds_refs}'",
                )
    st_ds_refs = persisted_ds_refs or caller_ds_refs

    persisted_dataset = (
        (persisted_params.get("dataset") if isinstance(persisted_params, dict) else None)
        or (persisted_stage.get("dataset") if isinstance(persisted_stage, dict) else None)
    )
    caller_dataset = stage.get("dataset") or body.get("dataset") or plan.get("dataset")
    if persisted_dataset and caller_dataset:
        p_ds_id = str((persisted_dataset or {}).get("dataset_id") or (persisted_dataset or {}).get("id") or "").strip()
        c_ds_id = str((caller_dataset or {}).get("dataset_id") or (caller_dataset or {}).get("id") or "").strip()
        clean_p = p_ds_id.split(":", 1)[-1] if ":" in p_ds_id else p_ds_id
        clean_c = c_ds_id.split(":", 1)[-1] if ":" in c_ds_id else c_ds_id
        if clean_p and clean_c and clean_p != clean_c:
            raise HTTPException(
                status_code=400,
                detail=f"Conflicting dataset hint for stage '{stage_id}': caller supplied '{c_ds_id}' but run is bound to persisted approved dataset '{p_ds_id}'",
            )
    dataset_input = persisted_dataset or caller_dataset

    has_persisted_stage = persisted_stage is not None
    persisted_stage_params = (persisted_stage.get("parameters") or {}) if (persisted_stage and isinstance(persisted_stage.get("parameters"), dict)) else {}
    caller_stage_params = (stage.get("parameters") or {}) if isinstance(stage, dict) else {}
    if has_persisted_stage and caller_stage_params:
        for k, v in caller_stage_params.items():
            if k not in persisted_stage_params:
                raise HTTPException(
                    status_code=400, detail=f"Unapproved stage parameter '{k}' for stage '{stage_id}': not in approved persisted parameters"
                )
            if persisted_stage_params[k] != v:
                raise HTTPException(
                    status_code=400, detail=f"Conflicting stage parameter '{k}' for stage '{stage_id}': caller supplied '{v}' != persisted '{persisted_stage_params[k]}'"
                )
    effective_stage_params = dict(persisted_stage_params) if has_persisted_stage else dict(caller_stage_params)
    stage["parameters"] = effective_stage_params

    persisted_art_candidates = _first_present(
        (persisted_stage.get("artifact_refs") if isinstance(persisted_stage, dict) and "artifact_refs" in persisted_stage else None),
        (persisted_stage.get("approved_artifact_refs") if isinstance(persisted_stage, dict) and "approved_artifact_refs" in persisted_stage else None),
        (persisted_params.get("artifact_refs") if isinstance(persisted_params, dict) and "artifact_refs" in persisted_params else None),
        (persisted_plan.get("artifact_refs") if isinstance(persisted_plan, dict) and "artifact_refs" in persisted_plan else None),
    )
    persisted_art_ids = _extract_artifact_ids(persisted_art_candidates)

    caller_art_candidates = _first_present(
        (stage.get("artifact_refs") if isinstance(stage, dict) and "artifact_refs" in stage else None),
        (stage.get("approved_artifact_refs") if isinstance(stage, dict) and "approved_artifact_refs" in stage else None),
        (body.get("artifact_refs") if isinstance(body, dict) and "artifact_refs" in body else None),
        (plan.get("artifact_refs") if isinstance(plan, dict) and "artifact_refs" in plan else None),
    )
    caller_art_ids = _extract_artifact_ids(caller_art_candidates)

    if has_persisted_stage:
        if persisted_art_candidates is not None or persisted_art_ids:
            if caller_art_ids:
                clean_p_arts = {(a.split(":", 1)[-1] if ":" in a else a) for a in persisted_art_ids}
                clean_c_arts = {(a.split(":", 1)[-1] if ":" in a else a) for a in caller_art_ids}
                if clean_c_arts != clean_p_arts:
                    raise HTTPException(
                        status_code=400, detail=f"Conflicting artifact refs for stage '{stage_id}': caller supplied '{caller_art_ids}' does not match persisted approved artifact refs '{persisted_art_ids}'"
                    )
            artifact_refs_input = list(persisted_art_ids)
        elif caller_art_ids:
            raise HTTPException(
                status_code=400, detail=f"Unapproved artifact refs for stage '{stage_id}': caller supplied '{caller_art_ids}' but stage has no approved artifact refs"
            )
        else:
            artifact_refs_input = []
    else:
        artifact_refs_input = list(caller_art_ids)

    stage["artifact_refs"] = list(artifact_refs_input)
    if st_ds_refs and not _matches_dataset_ref(dataset_input, st_ds_refs):
        ds_id_val = str((dataset_input or {}).get("dataset_id") or (dataset_input or {}).get("id") or "").strip()
        _fail_stage(f"Governed dataset mismatch for stage '{stage_id}': expected one of '{st_ds_refs}', got '{ds_id_val}'", 400)
    exp_tenant = str((run_record or {}).get("tenant_id") or plan.get("tenant_id") or body.get("tenant_id") or "").strip()
    ds_tenant = str((dataset_input or {}).get("tenant_id") or "").strip() if isinstance(dataset_input, dict) else ""
    if exp_tenant and ds_tenant and exp_tenant != ds_tenant:
        _fail_stage(f"Unauthorized access to dataset across tenant boundary: '{ds_tenant}' != '{exp_tenant}'", 403)
    if not dataset_input and stage_type != "evidence_synthesis":
        raise HTTPException(status_code=400, detail=f"Missing required governed dataset or input for stage '{stage_type}'")

    transport_keys = [str(k) for k in (body.get("downstream_key"), body.get("idempotency_key")) if isinstance(k, str) and k]

    def _record_transport_keys() -> None:
        key_payload = {"run_id": run_id, "stage_id": stage_id, "stage_type": stage_type}
        for tk in transport_keys:
            store._put_record(exec_storage_path, f"agora-transport-key:{tk}", key_payload)

    for tk in transport_keys:
        if isinstance(bound := store._get_record(exec_storage_path, f"agora-transport-key:{tk}"), dict):
            brun, bstage = str(bound.get("run_id") or ""), str(bound.get("stage_id") or "")
            if (brun and brun != run_id) or (bstage and stage_id and bstage != stage_id):
                raise HTTPException(status_code=409, detail=f"Transport key '{tk}' is already bound to run '{brun}'/stage '{bstage}', conflict with '{run_id}'/'{stage_id}'")

    wait_start = time.time()
    with _stage_execution_cond:
        while True:
            run_record = store.get_run(run_id)
            if run_record and isinstance(run_record, dict):
                status_str = str(run_record.get("status") or "").lower()
                if status_str in {"canceled", "cancelled", "rejected"}:
                    raise HTTPException(status_code=409, detail=f"Research run '{run_id}' is in terminal status '{status_str}' and cannot execute stages")
                rec_stage_id = run_record.get("stage_id")
                if not rec_stage_id:
                    for ref in run_record.get("input_refs") or []:
                        if isinstance(ref, dict) and ref.get("type") == "stage" and ref.get("id"):
                            rec_stage_id = str(ref["id"])
                            break
                if rec_stage_id and stage_id and rec_stage_id != stage_id:
                    raise HTTPException(status_code=400, detail=f"Stage identity mismatch: run '{run_id}' stage '{rec_stage_id}' != '{stage_id}'")
                if status_str == "completed" and run_record.get("receipt"):
                    _record_transport_keys()
                    return _format_run_completion_result(run_record, stage_type)

            if (cached_result := store._get_record(exec_storage_path, stage_claim_key)) is not None:
                if cached_result.get("status") == "succeeded":
                    _record_transport_keys()
                    return _sync_run_and_return_cached(run_id, cached_result)
                if cached_result.get("status") == "in_progress":
                    if time.time() - wait_start > 30.0:
                        raise HTTPException(status_code=504, detail=f"Timed out waiting for in-flight execution of stage '{stage_type}'")
                    _stage_execution_cond.wait(timeout=0.1)
                    continue

            claim_record = {
                "status": "in_progress", "claim_token": claim_token, "claimed_at": utc_now(),
                "run_id": run_id, "stage_type": stage_type, "stage_id": stage_id,
                "transport_keys": transport_keys,
            }
            store._put_record(exec_storage_path, stage_claim_key, claim_record)
            _record_transport_keys()
            if run_record and isinstance(run_record, dict):
                run_record["status"], run_record["claim_token"] = "running", claim_token
                store.put_run(run_record)
            break

    executor = str(
        context_map.get("executor")
        or body.get("executor")
        or f"{backend_name}_executor"
    )
    now_iso = utc_now()
    backend_ref = f"research-orchestrator://stages/{stage_type}/{run_id}"
    resolved_artifacts = []

    persisted_mode = str(
        (run_record.get("requested_mode") if run_record else "")
        or (run_record.get("dispatch_mode") if run_record else "")
        or ""
    ).lower().strip()
    caller_hints = [
        str(h).lower().strip()
        for h in (
            body.get("requested_mode"),
            body.get("dispatch_mode"),
            (stage.get("routing") or {}).get("backend_mode") if isinstance(stage, dict) else None,
        )
        if h
    ]
    if persisted_mode:
        for h in caller_hints:
            if h != persisted_mode and not (persisted_mode in {"stub", "simulation"} and h in {"stub", "simulation"}):
                _fail_stage(f"Conflicting execution mode hint '{h}' does not match persisted run mode '{persisted_mode}'", 409)
        req_mode = persisted_mode
    else:
        req_mode = caller_hints[0] if caller_hints else ""

    if req_mode and req_mode not in {"real", "simulation", "fixture", "stub", "offline"}:
        _fail_stage(f"Unknown research execution mode '{req_mode}'", 400)

    saved_model = persisted_plan.get("model_authority") if isinstance(persisted_plan, dict) else None
    caller_model = plan.get("model_authority") if isinstance(plan, dict) else None
    if caller_model and caller_model != saved_model:
        _fail_stage("model_authority must come from the saved research run, not the caller", 400)

    if backend_name == "vectorbt" or stage_type == "prototype_backtest":
        try:
            from services.research.vectorbt.adapter.vectorbt_adapter import (
                BacktestConfig,
                StubVectorbtBackend,
                VectorbtBackend,
                run_vectorbt_workflow,
                VectorbtWorkflowError,
            )
            use_real = os.environ.get("PANTHEON_VECTORBT_BACKEND", "stub").lower() == "real"
            if req_mode == "real" and not use_real:
                _fail_stage(f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable in real mode", 503)
            # The real owner checks its dependencies; never fall back to a stub
            # while retaining a real receipt label.
            backend_runner = VectorbtBackend() if use_real else StubVectorbtBackend()
            provenance = "real" if use_real else "simulation"
            if saved_model:
                vbt_config = _model_backtest_config(saved_model, stage.get("parameters") or {}, executor)
            else:
                vbt_config = BacktestConfig(
                    version="1.0.0",
                    requested_by=executor,
                    strategy_params=stage.get("parameters") or {},
                )
            vbt_dataset = dataset_input
            if isinstance(dataset_input, list):
                records = []
                for r in dataset_input:
                    if isinstance(r, dict):
                        rec = dict(r)
                        if "instrument" not in rec and "symbol" in rec:
                            rec["instrument"] = rec["symbol"]
                        if "date" not in rec and "timestamp" in rec:
                            rec["date"] = str(rec["timestamp"])[:10]
                        records.append(rec)
                ds_id = str(stage.get("dataset_id") or plan.get("dataset_id") or f"dataset:{run_id}")
                st_id = str(plan.get("strategy_id") or f"strat:{run_id}")
                vbt_dataset = {
                    "dataset_id": ds_id,
                    "strategy_id": st_id,
                    "source_dataset_refs": stage.get("source_dataset_refs") or plan.get("source_dataset_refs") or body.get("source_dataset_refs") or [],
                    "data_frequency": "daily",
                    "records": records,
                }
            workflow_res = run_vectorbt_workflow(vbt_dataset, backend=backend_runner, config=vbt_config)
            artifact_bundle = workflow_res.artifact_bundle
            if saved_model:
                artifact_bundle["model_authority"] = _model_provenance(saved_model, vbt_config.strategy_artifact)
            agg_m = workflow_res.backtest_result.aggregate_metrics
            metrics = [
                {"metric": "mean_total_return", "value": float(agg_m.get("mean_total_return", 0.0)), "provenance": provenance},
                {"metric": "mean_sharpe_ratio", "value": float(agg_m.get("mean_sharpe_ratio", 0.0)), "provenance": provenance},
                {"metric": "mean_max_drawdown", "value": float(agg_m.get("mean_max_drawdown", 0.0)), "provenance": provenance},
                {"metric": "total_trades", "value": float(agg_m.get("total_trades", 0)), "provenance": provenance},
            ]
        except (VectorbtWorkflowError, ValueError, KeyError) as exc:
            _fail_stage(f"Governed input validation error for {stage_type}: {exc}", 400, cause=exc)
        except Exception as exc:
            _fail_stage(f"Vectorbt execution owner failure: {exc}", 503, cause=exc)

    elif backend_name == "statsmodels" or stage_type == "econometric_validation":
        try:
            from services.research.statsmodels.adapter.statsmodels_adapter import (
                GovernedDataset,
                GovernedStatsmodelsInputAdapter,
                StatsmodelsBackend,
                StubStatsmodelsBackend,
                StatsmodelsWorkflowError,
            )
            if isinstance(dataset_input, dict) and not isinstance(dataset_input, GovernedDataset):
                dataset_obj = GovernedDataset(
                    price_series=dataset_input.get("price_series", {}),
                    factor_series=dataset_input.get("factor_series", {}),
                    metadata=dataset_input.get("metadata", {}),
                )
            else:
                dataset_obj = dataset_input

            adapter = GovernedStatsmodelsInputAdapter()
            validated_ds = adapter.validate(dataset_obj)

            use_real = os.environ.get("PANTHEON_STATSMODELS_BACKEND", "stub").lower() == "real"
            if req_mode == "real" and not use_real:
                _fail_stage(f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable in real mode", 503)
            backend_runner = StatsmodelsBackend() if use_real else StubStatsmodelsBackend()
            provenance = "real" if use_real else "simulation"

            coint_res = backend_runner.run_cointegration(validated_ds)
            var_res = backend_runner.run_var_vecm(validated_ds)
            artifact_bundle = {
                "schema_version": "1.0",
                "artifact_family": "regime_report",
                "framework": "statsmodels",
                "results": {"cointegration": coint_res, "var_vecm": var_res},
            }
            metrics = [
                {"metric": "cointegration_p_value", "value": float(coint_res.get("p_value", 0.02)), "provenance": provenance},
                {"metric": "var_aic", "value": float(var_res.get("aic", -100.0)), "provenance": provenance},
            ]
        except (StatsmodelsWorkflowError, ValueError, KeyError) as exc:
            _fail_stage(f"Governed input validation error for {stage_type}: {exc}", 400, cause=exc)
        except Exception as exc:
            _fail_stage(f"Statsmodels execution owner failure: {exc}", 503, cause=exc)

    elif backend_name == "quantlib" or stage_type == "derivatives_pricing_risk":
        try:
            from services.research.quantlib.adapter.quantlib_adapter import (
                GovernedMarketSnapshot,
                GovernedOptionSpec,
                GovernedBondSpec,
                GovernedQuantLibInputAdapter,
                QuantLibBackend,
                StubQuantLibBackend,
                run_quantlib_workflow,
                QuantLibWorkflowError,
            )
            if isinstance(dataset_input, dict) and not isinstance(dataset_input, GovernedMarketSnapshot):
                opt_specs = [
                    o if isinstance(o, GovernedOptionSpec) else GovernedOptionSpec(**o)
                    for o in (dataset_input.get("option_specs") or []) if isinstance(o, (GovernedOptionSpec, dict))
                ]
                bond_specs = [
                    b if isinstance(b, GovernedBondSpec) else GovernedBondSpec(**b)
                    for b in (dataset_input.get("bond_specs") or []) if isinstance(b, (GovernedBondSpec, dict))
                ]
                snapshot = GovernedMarketSnapshot(
                    dataset_id=str(dataset_input.get("dataset_id") or ""),
                    source_dataset_refs=tuple(dataset_input.get("source_dataset_refs") or ()),
                    valuation_date=str(dataset_input.get("valuation_date") or ""),
                    option_specs=tuple(opt_specs), bond_specs=tuple(bond_specs),
                    metadata=dataset_input.get("metadata") or {},
                )
            else:
                snapshot = dataset_input

            use_real = os.environ.get("PANTHEON_QUANTLIB_BACKEND", "stub").lower() == "real"
            if req_mode == "real" and not use_real:
                _fail_stage(f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable in real mode", 503)
            backend_runner = QuantLibBackend() if use_real else StubQuantLibBackend()
            provenance = "real" if use_real else "simulation"

            ql_bundle = run_quantlib_workflow(snapshot, backend=backend_runner)
            artifact_bundle = ql_bundle
            results_summary = ql_bundle.get("results_summary", {})
            opt_res = results_summary.get("options_pricing", {})
            fi_res = results_summary.get("fixed_income", {})

            metrics = [
                {"metric": f"option_{opt_id}_{k}", "value": float(m.get(k, 0.0)), "provenance": provenance}
                for opt_id, m in opt_res.items() for k in ("npv", "delta")
            ] + [
                {"metric": f"bond_{bond_id}_{k}", "value": float(b.get(k, 0.0)), "provenance": provenance}
                for bond_id, b in fi_res.items() for k in ("clean_price", "duration")
            ] or [
                {"metric": "derivatives_pricing_npv", "value": 0.0, "provenance": provenance},
                {"metric": "derivatives_pricing_delta", "value": 0.0, "provenance": provenance},
            ]
        except (QuantLibWorkflowError, ValueError, KeyError) as exc:
            _fail_stage(f"Governed input validation error for {stage_type}: {exc}", 400, cause=exc)
        except Exception as exc:
            _fail_stage(f"QuantLib execution owner failure: {exc}", 503, cause=exc)
    elif backend_name == "openclaw_result_synthesis" or stage_type == "evidence_synthesis":
        try:
            from services.control_plane.bff.openclaw_ops_client import (
                OpenClawOpsClient,
                OpenClawOpsClientError,
            )
            if os.getenv("PANTHEON_OPENCLAW_UNAVAILABLE") == "1":
                _fail_stage(f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable", 503)
            use_real = os.environ.get("PANTHEON_OPENCLAW_BACKEND", "stub").lower() == "real"
            client = OpenClawOpsClient()
            if req_mode == "real" and not (use_real and client.configured):
                _fail_stage(f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable in real mode", 503)
            expected_tenant = str((run_record.get("tenant_id") if run_record else None) or plan.get("tenant_id") or body.get("tenant_id") or "").strip()

            def _resolve_artifact_tenant(art_doc: Dict[str, Any]) -> Optional[str]:
                if not isinstance(art_doc, dict):
                    return None
                if tid := art_doc.get("tenant_id"):
                    return str(tid)
                if p_run_id := art_doc.get("run_id"):
                    if (p_run := store.get_run(str(p_run_id))) and isinstance(p_run, dict) and p_run.get("tenant_id"):
                        return str(p_run["tenant_id"])
                if p_task_id := art_doc.get("task_id"):
                    if (p_task := store.get_task(str(p_task_id))) and isinstance(p_task, dict) and p_task.get("tenant_id"):
                        return str(p_task["tenant_id"])
                return None

            def _validate_artifact_tenant(art_doc: Dict[str, Any], aid: str) -> None:
                art_tenant = _resolve_artifact_tenant(art_doc)
                if expected_tenant:
                    if not art_tenant:
                        raise HTTPException(status_code=403, detail=f"Unauthorized access to artifact '{aid}' with unknown tenant ownership")
                    if art_tenant != expected_tenant:
                        raise HTTPException(status_code=403, detail=f"Unauthorized access to artifact '{aid}' across tenant boundary: '{art_tenant}' != '{expected_tenant}'")
                elif art_tenant:
                    raise HTTPException(status_code=403, detail=f"Unauthorized access to tenant-owned artifact '{aid}' from tenantless execution")

            if artifact_refs_input:
                for ref in artifact_refs_input:
                    aid = ref.get("artifact_id") or ref.get("id") if isinstance(ref, dict) else str(ref)
                    if not aid:
                        continue
                    stored = store.get_artifact(aid)
                    if not stored:
                        raise HTTPException(status_code=400, detail=f"Required persisted artifact '{aid}' was not found in research store")
                    _validate_artifact_tenant(stored, aid)
                    resolved_artifacts.append(stored)
            else:
                task_id = effective_task_id
                persisted_deps_raw = _first_present(
                    (persisted_stage.get("dependencies") if isinstance(persisted_stage, dict) and "dependencies" in persisted_stage else None),
                    (persisted_stage.get("depends_on") if isinstance(persisted_stage, dict) and "depends_on" in persisted_stage else None),
                )
                caller_deps_raw = _first_present(
                    (stage.get("dependencies") if isinstance(stage, dict) and "dependencies" in stage else None),
                    (stage.get("depends_on") if isinstance(stage, dict) and "depends_on" in stage else None),
                )
                p_deps = [str(d).strip() for d in (persisted_deps_raw if isinstance(persisted_deps_raw, (list, tuple, set)) else [persisted_deps_raw]) if d] if persisted_deps_raw is not None else []
                c_deps = [str(d).strip() for d in (caller_deps_raw if isinstance(caller_deps_raw, (list, tuple, set)) else [caller_deps_raw]) if d] if caller_deps_raw is not None else []

                if has_persisted_stage and persisted_deps_raw is not None:
                    if c_deps and set(c_deps) != set(p_deps):
                        raise HTTPException(
                            status_code=400, detail=f"Conflicting dependency selection for stage '{stage_id}': caller supplied '{c_deps}' != persisted '{p_deps}'"
                        )
                    deps = set(p_deps)
                elif has_persisted_stage and persisted_deps_raw is None and c_deps:
                    raise HTTPException(
                        status_code=400, detail=f"Unapproved dependency selection for stage '{stage_id}': caller supplied '{c_deps}' but stage has no approved dependencies"
                    )
                else:
                    deps = set(c_deps) if not has_persisted_stage else set()

                if deps:
                    seen_ids = set()
                    c_runs = [r for r in store.list_runs() if str(r.get("task_id") or "") == task_id and str(r.get("run_id") or "") != str(run_id) and str(r.get("stage_id") or "") in deps]
                    for c_run in c_runs:
                        for aref in c_run.get("artifact_refs") or []:
                            aid = aref.get("artifact_id") if isinstance(aref, dict) else str(aref)
                            if aid and aid not in seen_ids and (stored := store.get_artifact(aid)):
                                _validate_artifact_tenant(stored, aid)
                                seen_ids.add(aid)
                                resolved_artifacts.append(stored)
                    if not resolved_artifacts and task_id:
                        for art in store.list_artifacts():
                            aid = art.get("artifact_id") or art.get("id")
                            if (
                                aid and aid not in seen_ids and str(art.get("task_id") or "") == task_id
                                and str(art.get("stage_id") or "") in deps and str(art.get("run_id") or "") != str(run_id)
                            ):
                                _validate_artifact_tenant(art, str(aid))
                                seen_ids.add(aid)
                                resolved_artifacts.append(art)

            if not resolved_artifacts:
                raise HTTPException(status_code=400, detail=f"Missing required persisted input artifacts for stage '{stage_type}'")

            if use_real and client.configured:
                res = client.invoke_structured_extraction(
                    prompt=f"Synthesize research evidence and produce an interpretation report for research run {run_id} across artifacts: {json.dumps(resolved_artifacts, default=str)}",
                    extraction_schema={"type": "object", "required": ["summary", "interpretation", "recommendation"], "properties": {"summary": {"type": "string"}, "interpretation": {"type": "string"}, "recommendation": {"type": "string"}, "confidence_score": {"type": "number"}}},
                    operator_id=executor or "operator", trace_id=correlation_id,
                )
                data = res.get("data") if isinstance(res, dict) else None
                report_data = (data.get("output") or {}).get("structured_data") if isinstance(data, dict) else None
                is_valid = (
                    isinstance(report_data, dict)
                    and isinstance(report_data.get("summary"), str) and bool(report_data["summary"].strip())
                    and isinstance(report_data.get("interpretation"), str) and bool(report_data["interpretation"].strip())
                    and isinstance(report_data.get("recommendation"), str) and bool(report_data["recommendation"].strip())
                    and ("confidence_score" not in report_data or isinstance(report_data["confidence_score"], (int, float)))
                )
                if not is_valid:
                    _fail_stage(f"Structured agent provider returned invalid extraction output schema: {res}", 502)
                provenance = "real"
            else:
                provenance = "simulation"
                art_count = len(resolved_artifacts)
                report_data = {
                    "summary": f"Evidence synthesis report for run {run_id} (stage {stage_id or 'report'})",
                    "interpretation": f"Analyzed {art_count} input artifact(s); deterministic synthesis indicates criteria met.",
                    "recommendation": "accept", "confidence_score": 0.95, "input_artifact_count": art_count,
                }

            artifact_bundle = {
                "artifact_family": "evidence_synthesis_artifact", "stage_id": stage_id,
                "report": report_data, "input_artifacts": resolved_artifacts, "synthesized_by": "openclaw_result_synthesis",
            }
            metrics = [
                {"metric": "evidence_completeness", "value": 1.0, "provenance": provenance},
                {"metric": "artifacts_analyzed", "value": float(len(resolved_artifacts)), "provenance": provenance},
                {"metric": "synthesis_confidence", "value": float(report_data.get("confidence_score", 0.9) if isinstance(report_data.get("confidence_score"), (int, float)) else 0.9), "provenance": provenance},
            ]
        except OpenClawOpsClientError as exc:
            _fail_stage(f"OpenClaw structured agent provider unavailable: {exc.message}", 503, cause=exc)
        except HTTPException as exc:
            _persist_failure(exc.detail)
            raise
        except Exception as exc:
            _fail_stage(f"Evidence synthesis provider failure: {exc}", 503, cause=exc)
    else:
        _fail_stage(f"Backend execution owner for stage '{stage_type}' ({backend_name}) is absent or not configured", 503)

    # A real engine does not turn explicitly simulated input into real evidence.
    # This only downgrades provenance: an input label can never promote a stub.
    inputs = [dataset_input] if dataset_input else []
    records = dataset_input.get("records", []) if isinstance(dataset_input, dict) else dataset_input
    if isinstance(records, list):
        inputs.extend(records)
    if resolved_artifacts:
        inputs.extend(resolved_artifacts)
    for value in inputs:
        if not isinstance(value, dict):
            continue
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        if any(item.get("provenance") == "simulation" or item.get("is_real") is False for item in (value, metadata)):
            provenance = "simulation"
            for metric in metrics:
                metric["provenance"] = provenance
            break

    def _abort_canceled() -> None:
        with _stage_execution_cond:
            store._put_record(exec_storage_path, stage_claim_key, {"status": "canceled", "updated_at": utc_now()})
            _stage_execution_cond.notify_all()
        raise HTTPException(status_code=409, detail=f"Research run '{run_id}' is in terminal status or canceled and cannot be marked completed")

    def _check_canceled() -> Dict[str, Any]:
        cur_run = store.get_run(run_id)
        cur_task = store.get_task(str(cur_run.get("task_id") or "")) if cur_run and cur_run.get("task_id") else None
        if cur_run and (
            str(cur_run.get("status") or "").lower() in {"canceled", "cancelled", "rejected"}
            or cur_run.get("cancellation_fence")
            or (cur_task and (str(cur_task.get("status") or "").lower() in {"canceled", "cancelled"} or cur_task.get("cancellation_fence")))
        ):
            _abort_canceled()
        return cur_run or {}

    latest_run = _check_canceled()

    target_tenant_id = (run_record.get("tenant_id") if run_record else None) or plan.get("tenant_id") or body.get("tenant_id")
    artifact_id = f"rart-{uuid.uuid4().hex[:12]}"
    artifact_record = {
        "id": artifact_id, "artifact_id": artifact_id, "run_id": run_id,
        "task_id": effective_task_id,
        "stage_id": stage_id, "stage_type": stage_type, "artifact_type": f"{stage_type}_result",
        "artifact_family": artifact_bundle.get("artifact_family") or f"{stage_type}_artifact",
        "title": f"Execution artifact for {stage_type} ({run_id})", "payload": artifact_bundle,
        "created_at": now_iso, "provenance": provenance,
    }
    if effective_plan_id:
        artifact_record["plan_id"] = effective_plan_id
    if target_tenant_id:
        artifact_record["tenant_id"] = str(target_tenant_id)
    try:
        persisted_art = store.put_artifact(artifact_record)
        digest = f"sha256:{hashlib.sha256(json.dumps(persisted_art, sort_keys=True, default=str).encode('utf-8')).hexdigest()}"
        persisted_art["checksum"] = digest
        store.put_artifact(persisted_art)
    except RuntimeError:
        _abort_canceled()

    latest_run = _check_canceled()

    receipt = {
        "receipt_id": f"rcpt-{uuid.uuid4().hex[:10]}", "run_id": run_id, "executor": executor,
        "mode": provenance, "correlation_id": correlation_id, "completed_at": now_iso,
        "backend_reference": backend_ref, "artifact_digest": digest, "spec_version": "1.0",
    }
    artifact_ref_entry = {"artifact_id": artifact_id, "ref": f"artifact://{artifact_id}", "digest": digest}

    if latest_run and isinstance(latest_run, dict):
        arts = list(latest_run.get("artifact_refs") or [])
        if not any(a.get("artifact_id") == artifact_id for a in arts if isinstance(a, dict)):
            arts.append(artifact_ref_entry)
        latest_run.update({"status": "completed", "completed_at": now_iso, "metrics": metrics, "provenance": provenance, "receipt": receipt, "artifact_refs": arts})
        if target_tenant_id and not latest_run.get("tenant_id"):
            latest_run["tenant_id"] = str(target_tenant_id)
        if dataset_input and isinstance(dataset_input, dict):
            latest_run.setdefault("parameters", {})["dataset"] = dataset_input
            ds_id = str(dataset_input.get("dataset_id") or dataset_input.get("id") or "").strip()
            if ds_id:
                cur_refs = list(latest_run.get("input_refs") or [])
                if not any((isinstance(r, dict) and r.get("type") == "dataset" and r.get("id") == ds_id) or r == ds_id for r in cur_refs):
                    cur_refs.append({"type": "dataset", "id": ds_id})
                    latest_run["input_refs"] = cur_refs
        saved_run = store.put_run(latest_run)
        if saved_run and str(saved_run.get("status") or "").lower() in {"canceled", "cancelled"}:
            _abort_canceled()

    result = {
        "status": "succeeded", "outcome": "succeeded", "provenance": provenance, "backend_reference": backend_ref,
        "artifact_id": artifact_id, "artifact_digest": digest, "artifact_refs": [artifact_ref_entry], "artifacts": [artifact_ref_entry],
        "checksums": {artifact_id: digest, f"artifact://{artifact_id}": digest}, "metrics": metrics, "receipt": receipt,
    }
    with _stage_execution_cond:
        store._put_record(exec_storage_path, stage_claim_key, result)
        _record_transport_keys()
        _stage_execution_cond.notify_all()
    return result


# -----------------------------------------------------------------------------
# Research Tickets (RW-01) Single Owner Endpoints
# -----------------------------------------------------------------------------


def _handle_experiment_action(experiment_id: str, action_fn: Any, body: Optional[Dict[str, Any]], err_desc: str) -> Dict[str, Any]:
    wo = get_write_owner()
    res = action_fn(wo, body or {})
    if res is None:
        if wo.get_research_experiment(experiment_id) is None:
            raise HTTPException(status_code=404, detail=f"Research experiment '{experiment_id}' not found")
        raise HTTPException(status_code=409, detail=f"Research experiment '{experiment_id}' {err_desc}")
    return res


# -----------------------------------------------------------------------------
# Saved run-output artifact reads (Management Artifact catalog)
# -----------------------------------------------------------------------------


def _saved_artifact_tenant(art: Dict[str, Any]) -> Optional[str]:
    """Owner tenant of a saved run output: its own stamp, else its run's, else its task's."""
    for source in (
        art,
        store.get_run(str(art.get("run_id"))) if art.get("run_id") else None,
        store.get_task(str(art.get("task_id"))) if art.get("task_id") else None,
    ):
        if isinstance(source, dict) and str(source.get("tenant_id") or "").strip():
            return str(source["tenant_id"]).strip()
    return None


def _saved_artifact_read_model(art: Dict[str, Any], run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Saved-output metadata only; no payload, and no approval/version/lineage the output never had."""
    run = run or {}
    receipt = run.get("receipt") if isinstance(run.get("receipt"), dict) else {}
    keys = ("artifact_id", "run_id", "task_id", "stage_id", "stage_type", "artifact_type", "artifact_family",
            "title", "storage_ref", "checksum", "provenance", "created_at", "created_by", "tenant_id")
    out = {k: art.get(k) for k in keys}
    out["tenant_id"] = _saved_artifact_tenant(art)
    out["run"] = {
        "run_id": art.get("run_id"), "status": run.get("status"), "provenance": run.get("provenance"),
        "completed_at": run.get("completed_at"), "receipt_id": receipt.get("receipt_id"),
        "artifact_digest": receipt.get("artifact_digest"),
    }
    return out


def _visible_saved_artifacts(tenant_id: str) -> List[Dict[str, Any]]:
    caller = str(tenant_id or "").strip()
    if not caller:
        return []
    runs = {str(r.get("run_id")): r for r in store.list_runs()}
    rows = [
        _saved_artifact_read_model(a, runs.get(str(a.get("run_id"))))
        for a in store.list_artifacts()
    ]
    # Fail closed: only the authenticated caller's own tenant; unresolved-tenant outputs are never visible.
    rows = [r for r in rows if r["tenant_id"] and r["tenant_id"] == caller]
    rows.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("artifact_id") or "")), reverse=True)
    return rows


# Artifact reads keep the existing BFF read-role contract (viewer included); service defaults stay admitted.
_ARTIFACT_READ_ROLES = tuple(dict.fromkeys(
    ("viewer", "view_only", "operator", "approver", "admin", "reviewer", *AUTHENTICATED_SERVICE_ROLES)
))


_BACKTEST_OWNER_READ_ROLES = tuple(dict.fromkeys(("registry-writer", *AUTHENTICATED_SERVICE_ROLES)))


def _backtest_owner_path(data_dir: Path) -> Path:
    return Path(data_dir) / "backtest_run_owners.json"


def _update_backtest_owner_file(data_dir: Path, mutate: Any) -> None:
    """Serialised reload/merge/atomic-replace of the authoritative owner file.

    An interprocess flock spans the whole cycle, the temp file is unique per writer, and a
    present-but-corrupt file fails closed (never reset to empty, which would drop other tenants).
    """
    import fcntl
    import tempfile

    path = _backtest_owner_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError(f"{path.name} is not a JSON object")
            else:
                loaded = {}
            if mutate(loaded) is False:
                return
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(loaded, indent=2, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _bind_backtest_run_owner(data_dir: Path, run_id: str, *, tenant_id: str, decision_id: str) -> None:
    """Server-side tenant/decision binding written by the code that ran the protocol."""
    def mutate(loaded: Dict[str, Any]) -> None:
        loaded[run_id] = {"tenant_id": tenant_id, "decision_id": decision_id}

    _update_backtest_owner_file(data_dir, mutate)


def _bind_backtest_run_gate_verdict(data_dir: Path, run_id: str, gate_verdict: Dict[str, Any]) -> None:
    """Persist the real gate decision context beside the run owner record (server-side only)."""
    def mutate(loaded: Dict[str, Any]) -> Optional[bool]:
        owner = loaded.get(run_id)
        if not isinstance(owner, dict):
            return False  # an unbound run never gains a verdict
        owner["gate_verdict"] = gate_verdict
        return None

    _update_backtest_owner_file(data_dir, mutate)


@app.get("/api/research-orchestrator/backtest-runs/{run_id}/owner-binding")
def read_backtest_run_owner_binding(
    run_id: str,
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Read-only producer proof of a recorded backtest run, bound to the authenticated tenant.

    The caller bearer is verified by the strict Research verifier (viewer roles are
    not admitted).  The run is returned only when the server-owned run owner record
    names the same tenant; foreign or unbound runs are 404, never disclosed.
    """
    try:
        verified = authenticate_tenant(
            authorization=authorization, tenant_id=x_tenant_id, service_prefix="RESEARCH",
            required_roles=_BACKTEST_OWNER_READ_ROLES,
        ).tenant_id
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except TenantBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    data_dir = Path(ALPHA_REPLICATION_DATA_DIR)
    from services.research.alpha_replication.shared_stores import SharedBacktestRunStore

    record = SharedBacktestRunStore(data_dir / "backtest_runs.json").get(run_id)
    try:
        owners = json.loads(_backtest_owner_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        owners = {}
    owner = owners.get(run_id) if isinstance(owners, dict) else None
    if not record or not isinstance(owner, dict) or owner.get("tenant_id") != verified:
        raise HTTPException(status_code=404, detail="backtest run not found for tenant")
    return {
        "backtest_run_id": run_id, "tenant_id": verified, "decision_id": owner.get("decision_id"),
        "status": record.get("status"), "artifact_checksum": record.get("artifact_checksum"),
        "dataset_id": record.get("dataset_id"), "source_dataset_refs": record.get("source_dataset_refs"),
        "artifact_ref": record.get("artifact_ref"), "evaluation_digest": record.get("evaluation_digest"),
        "gate_verdict": owner.get("gate_verdict"),
    }


def _authenticated_artifact_tenant(
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
) -> str:
    """Verified bearer identity bound to an explicit tenant (shared service boundary); anonymous is denied."""
    try:
        return authenticate_tenant(
            authorization=authorization, tenant_id=x_tenant_id, service_prefix="RESEARCH",
            required_roles=_ARTIFACT_READ_ROLES,
        ).tenant_id
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except TenantBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/research/artifacts")
def list_saved_research_artifacts(tenant_id: str = Depends(_authenticated_artifact_tenant)) -> List[Dict[str, Any]]:
    return _visible_saved_artifacts(tenant_id)


@app.get("/api/research/artifacts/{artifact_id}")
def get_saved_research_artifact(artifact_id: str, tenant_id: str = Depends(_authenticated_artifact_tenant)) -> Dict[str, Any]:
    for row in _visible_saved_artifacts(tenant_id):
        if row["artifact_id"] == artifact_id:
            return row
    raise HTTPException(status_code=404, detail=f"Research artifact '{artifact_id}' not found")


@app.post("/api/research/tickets")
def create_research_ticket(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return get_write_owner().create_research_ticket(
            title=str(body.get("title") or ""), description=str(body.get("description") or ""),
            priority=str(body.get("priority") or "medium"), owner=str(body.get("owner") or ""),
            actor_id=str(body.get("actor_id") or "operator"), created_at=body.get("created_at"), ticket_id=body.get("ticket_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/research/tickets/{ticket_id}")
def patch_research_ticket(ticket_id: str, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
    result = get_write_owner().patch_research_ticket(
        ticket_id, patch=patch, actor_id=str(body.get("actor_id") or patch.get("actor_id") or "operator"),
        updated_at=body.get("updated_at") or patch.get("updated_at"),
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Research ticket '{ticket_id}' not found")
    return result


@app.get("/api/research/tickets")
def list_research_tickets(status: Optional[str] = Query(default=None), owner: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    statuses = [item.strip() for item in status.split(",") if item.strip()] if status else None
    return get_write_owner().list_research_tickets(statuses=statuses, owner=owner)


@app.get("/api/research/tickets/{ticket_id}")
def get_research_ticket(ticket_id: str) -> Dict[str, Any]:
    result = get_write_owner().get_research_ticket(ticket_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Research ticket '{ticket_id}' not found")
    return result


@app.post("/api/research/experiments")
def create_research_experiment(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return get_write_owner().create_research_experiment(
            ticket_id=str(body.get("ticket_id") or ""), experiment_name=str(body.get("experiment_name") or ""),
            strategy_selector=body.get("strategy_selector") or {}, parameter_set=body.get("parameter_set") or {},
            run_config=body.get("run_config") or {}, launch_context=body.get("launch_context") or {},
            queued_at=body.get("queued_at"), experiment_id=body.get("experiment_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/research/experiments")
def list_research_experiments(ticket_id: Optional[str] = Query(default=None), status: Optional[str] = Query(default=None), include_archived: bool = Query(default=False)) -> List[Dict[str, Any]]:
    return get_write_owner().list_research_experiments(ticket_id=ticket_id, status=status, include_archived=include_archived)


@app.get("/api/research/experiments/{experiment_id}")
def get_research_experiment(experiment_id: str) -> Dict[str, Any]:
    result = get_write_owner().get_research_experiment(experiment_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Research experiment '{experiment_id}' not found")
    return result


@app.post("/api/research/experiments/{experiment_id}/cancel")
def cancel_research_experiment(experiment_id: str, body: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
    return _handle_experiment_action(experiment_id, lambda o, p: o.cancel_research_experiment(experiment_id, completed_at=p.get("completed_at"), reason=p.get("reason"), actor_id=p.get("actor_id")), body, "is not in a cancelable state")


@app.post("/api/research/experiments/{experiment_id}/retry")
def retry_research_experiment(experiment_id: str, body: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
    return _handle_experiment_action(experiment_id, lambda o, p: o.retry_research_experiment(experiment_id, actor_id=p.get("actor_id"), requested_at=p.get("requested_at"), idempotency_key=p.get("idempotency_key")), body, "is not in a retryable state")


@app.post("/api/research/experiments/{experiment_id}/archive")
def archive_research_experiment(experiment_id: str, body: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
    return _handle_experiment_action(experiment_id, lambda o, p: o.archive_research_experiment(experiment_id, actor_id=p.get("actor_id"), archived_at=p.get("archived_at")), body, "is not in an archivable state")


@app.post("/api/research/experiments/{experiment_id}/invalidate")
def invalidate_research_experiment(experiment_id: str, body: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
    return _handle_experiment_action(experiment_id, lambda o, p: o.invalidate_research_experiment(experiment_id, reason=p.get("reason"), actor_id=p.get("actor_id"), invalidated_at=p.get("invalidated_at")), body, "cannot be invalidated")


@app.post("/api/research/notes")
def create_research_note(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    result = get_write_owner().create_research_note(body)
    if result is None:
        raise HTTPException(status_code=400, detail="Invalid note payload")
    return result


@app.get("/api/research/notes")
def list_research_notes() -> List[Dict[str, Any]]:
    return get_write_owner().list_research_notes()


@app.get("/api/research/notes/{note_id}")
def get_research_note(note_id: str) -> Dict[str, Any]:
    result = get_write_owner().get_research_note(note_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Research note '{note_id}' not found")
    return result


class EvaluateAlphaTemplatesBody(BaseModel):
    template_ids: Optional[List[str]] = None
    market_data: Optional[Dict[str, Any]] = None
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    strict_stepwise: bool = False
    redundancy_threshold: float = 0.9


@app.post("/api/research-orchestrator/alpha-templates/evaluate", status_code=201)
@app.post("/api/research/alpha-templates/evaluate", status_code=201)
def evaluate_alpha_templates_endpoint(
    body: Optional[EvaluateAlphaTemplatesBody] = Body(default=None),
) -> Dict[str, Any]:
    # Lazy import: the evaluator needs numpy/pandas, which are not installed in
    # every environment that imports this module (e.g. evolution/policy-learning
    # test suites); only the evaluate endpoint requires them.
    from services.research.alpha_templates import run_alpha_template_evaluation

    req = body or EvaluateAlphaTemplatesBody()
    registry_service = RegistryService(get_registry_store())
    try:
        return run_alpha_template_evaluation(
            research_store=get_store(),
            registry_service=registry_service,
            template_ids=req.template_ids,
            universe=req.market_data,
            run_id=req.run_id,
            task_id=req.task_id,
            strict_stepwise=req.strict_stepwise,
            redundancy_threshold=req.redundancy_threshold,
        )
    except (AlphaTemplateValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/research-orchestrator/alpha-templates/evaluations")
@app.get("/api/research/alpha-templates/evaluations")
def list_alpha_template_evaluations_endpoint(
    family: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    registry_service = RegistryService(get_registry_store())
    ensure_builtin_alpha_templates(registry_service)
    entries = [
        v
        for v in registry_service.list_all_entries()
        if v.entry.artifact_type == ArtifactType.ALPHA_TEMPLATE
    ]
    items: List[Dict[str, Any]] = []
    for view in entries:
        meta = view.entry.metadata or {}
        tmpl = meta.get(ALPHA_TEMPLATE_METADATA_KEY)
        if not isinstance(tmpl, dict):
            continue
        if family and tmpl.get("family") != family:
            continue
        if status and tmpl.get("status") != status:
            continue
        eval_meta = meta.get("alpha_template_evaluation")
        items.append(
            {
                "registry_id": view.entry.registry_id,
                "template_id": tmpl.get("template_id"),
                "name": tmpl.get("name"),
                "family": tmpl.get("family"),
                "scope": tmpl.get("scope"),
                "status": tmpl.get("status"),
                "evaluation_ref": tmpl.get("evaluation_ref"),
                "duplicate_of": tmpl.get("duplicate_of"),
                "evaluation": eval_meta,
            }
        )
    return {"count": len(items), "items": items}


@app.get("/api/research-orchestrator/alpha-templates/evaluations/{template_id:path}")
@app.get("/api/research/alpha-templates/evaluations/{template_id:path}")
def get_alpha_template_evaluation_endpoint(template_id: str) -> Dict[str, Any]:
    registry_service = RegistryService(get_registry_store())
    ensure_builtin_alpha_templates(registry_service)
    reg_id = alpha_template_registry_id(template_id)
    entry = registry_service.store.get(reg_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"AlphaTemplate '{template_id}' not found")
    meta = entry.metadata or {}
    tmpl = meta.get(ALPHA_TEMPLATE_METADATA_KEY) or {}
    eval_ref = tmpl.get("evaluation_ref")
    run_record = get_store().get_run(str(eval_ref)) if eval_ref else None
    return {
        "registry_id": entry.registry_id,
        "template_id": tmpl.get("template_id"),
        "status": tmpl.get("status"),
        "evaluation_ref": eval_ref,
        "duplicate_of": tmpl.get("duplicate_of"),
        "template": tmpl,
        "evaluation": meta.get("alpha_template_evaluation"),
        "research_run": run_record,
    }
