from __future__ import annotations

import hashlib
import json
import json as _json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("research-orchestrator")

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.registry.split_api import RegistryService
from services.registry.storage import get_store as get_registry_store
from services.research.alpha_replication.admission import ReplicationAdmissionStore
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
    sponsor_persona_id: str = "persona-tw-equity"
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


@app.get("/api/research-orchestrator/tasks")
def list_tasks(status: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    tasks = store.list_tasks()
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
        summary = (
            f"Stage execution adapter '{adapter}' queued for authentic dispatch."
            if is_stage_backend
            else "Stub research orchestration run queued for bounded dispatch."
        )
        events.append(_event(timestamp, "run_queued", summary, body.actor_id, run_id, events))
    stage_id_val = None
    if body.parameters.get("stage") and isinstance(body.parameters["stage"], dict):
        stage_id_val = body.parameters["stage"].get("stage_id")
    if not stage_id_val:
        for ref in body.input_refs:
            if isinstance(ref, dict) and ref.get("type") == "stage" and ref.get("id"):
                stage_id_val = str(ref["id"])
                break

    run: Dict[str, Any] = {
        "id": run_id, "run_id": run_id, "task_id": task_id, "stage_id": stage_id_val,
        "attempt_number": 1, "parent_run_id": None, "root_run_id": run_id,
        "adapter": adapter, "requested_mode": requested_mode, "dispatch_mode": dispatch_mode,
        "status": status, "production_activation": "disabled",
        "input_refs": body.input_refs, "parameters": body.parameters, "created_by": body.actor_id,
        "tenant_id": body.parameters.get("tenant_id") or getattr(body, "tenant_id", None) or task.get("tenant_id"),
        "user_id": body.parameters.get("user_id") or body.actor_id or getattr(body, "user_id", None) or task.get("user_id"),
        "created_at": timestamp, "updated_at": timestamp, "idempotency_key": body.idempotency_key,
        "rejection": rejection, "events": events, "artifact_refs": [],
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
    if not rejected and "decision_id" in body.parameters and "target_artifact_id" in body.parameters:
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


def _trigger_retrain_execution(run_id: str, params: dict) -> None:
    def run_worker():
        try:
            import urllib.request
            import json
            import os
            
            decision_id = params["decision_id"]
            target_artifact_id = params["target_artifact_id"]
            work_item_id = params["work_item_id"]
            
            training_session_url = os.getenv("TRAINING_SESSION_URL", "http://training-session-svc:8099")
            registry_url = os.getenv("REGISTRY_URL", "http://registry:8087")
            research_url = os.getenv("RESEARCH_ORCHESTRATOR_URL", "http://research-orchestrator-svc:8101")

            # Update research run status to running
            from services.research.main import get_store as get_research_store
            rstore = get_research_store()
            r_run = rstore.get_run(run_id)
            if r_run:
                r_run["status"] = "running"
                rstore.put_run(r_run)

            # 1. Create a training session in training-session-svc
            session_body = {
                "persona_id": "persona-tw-equity",
                "objective": f"Evolutionary parameter mutation for {target_artifact_id}",
                "mode": "coaching",
                "context_refs": [
                    {"type": "evolution_decision", "id": decision_id},
                    {"type": "research_run", "id": run_id}
                ],
                "actor_id": "research-orchestrator"
            }
            session_req = urllib.request.Request(
                f"{training_session_url}/api/training/sessions",
                data=json.dumps(session_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(session_req, timeout=10) as resp:
                session_data = json.loads(resp.read().decode("utf-8"))
            
            session_id = session_data["session_id"]
            
            # 2. Fetch the current artifact (v1) from the registry to see its parameters
            get_req = urllib.request.Request(
                f"{registry_url}/api/registry/strategy-artifacts/{target_artifact_id}",
                method="GET"
            )
            with urllib.request.urlopen(get_req, timeout=10) as resp:
                artifact_view = json.loads(resp.read().decode("utf-8"))
            
            parent_artifact = artifact_view["entry"]["metadata"]["strategy_artifact"]
            
            # 3. Determine the parameter update.
            parameter_updates = {}
            current_params = parent_artifact.get("parameters") or {}
            
            if "lookback_bars" in current_params:
                current_lookback = int(current_params["lookback_bars"])
                new_lookback = 3 if current_lookback == 2 else 2
                parameter_updates["lookback_bars"] = new_lookback
            elif "momentum_threshold" in current_params:
                current_threshold = float(current_params["momentum_threshold"])
                new_threshold = 0.01 if current_threshold == 0.0 else 0.0
                parameter_updates["momentum_threshold"] = new_threshold
            else:
                parameter_updates["momentum_threshold"] = 0.01

            # 4. Mutate the artifact v1 to produce v2 using registry /mutate endpoint!
            new_artifact_id = target_artifact_id
            if "-v1" in target_artifact_id:
                new_artifact_id = target_artifact_id.replace("-v1", "-v2")
            else:
                new_artifact_id = target_artifact_id + "-v2"
                
            new_version = "1.1.0"
            if parent_artifact.get("version") == "1.1.0":
                new_version = "1.2.0"
                
            mutate_body = {
                "new_artifact_id": new_artifact_id,
                "new_version": new_version,
                "parameter_updates": parameter_updates,
                "source_run_ids": [
                    decision_id,
                    work_item_id,
                    session_id
                ]
            }
            
            mutate_req = urllib.request.Request(
                f"{registry_url}/api/registry/strategy-artifacts/{target_artifact_id}/mutate",
                data=json.dumps(mutate_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(mutate_req, timeout=10) as resp:
                mutate_data = json.loads(resp.read().decode("utf-8"))
            
            # 5. Complete the training session
            complete_req = urllib.request.Request(
                f"{training_session_url}/api/training/sessions/{session_id}/complete",
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(complete_req, timeout=10) as resp:
                json.loads(resp.read().decode("utf-8"))
                
            # 6. Complete the research run in research-orchestrator!
            r_run = rstore.get_run(run_id)
            if r_run:
                r_run["status"] = "completed"
                r_run["registry_writebacks"] = r_run.get("registry_writebacks") or []
                r_run["registry_writebacks"].append({
                    "registry_id": new_artifact_id,
                    "artifact_state": "candidate",
                    "created_at": utc_now()
                })
                rstore.put_run(r_run)
                
        except Exception as e:
            from services.research.main import get_store as get_research_store
            rstore = get_research_store()
            r_run = rstore.get_run(run_id)
            if r_run:
                r_run["status"] = "failed"
                r_run["rejection"] = {"reason": "retrain_failed", "detail": str(e)}
                rstore.put_run(r_run)

    import threading
    threading.Thread(target=run_worker, name=f"retrain-executor-{run_id}").start()


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
    timestamp = body.completed_at or utc_now()
    events = list(run.get("events") or [])
    events.append(_event(timestamp, "run_completed", body.summary, body.actor_id, run_id, events))
    run["status"] = body.status
    run["completed_at"] = timestamp
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
        existing = _idempotent_match(store.list_runs(), b.idempotency_key)
        if existing:
            return existing

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
    if "decision_id" in params and "target_artifact_id" in params:
        _trigger_retrain_execution(new_run["run_id"], params)
    elif is_stage_backend and (params.get("stage") or (params.get("plan") and params["plan"].get("stages"))):
        _progress_plan_stages(params.get("plan") or {}, new_run, b.actor_id, store, timestamp)
    return new_run


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
                sponsor_persona_id=_first_text(body.metadata.get("sponsor_persona_id"), run.get("sponsor_persona_id"), "persona-tw-equity") or "persona-tw-equity",
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
                },
            )
            outbox_store.create_record(outbox_record)
    except Exception:
        pass

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
        sponsor_persona_id=body.sponsor_persona_id,
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
        raise HTTPException(
            status_code=400,
            detail="Missing required execution request body",
        )

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
        raise HTTPException(
            status_code=400, detail=f"Unknown or non-allowlisted research stage '{stage_type}'. Allowed: {sorted(ALLOWLISTED_STAGE_TYPES)}"
        )

    backend_name = ALLOWLISTED_STAGE_BACKENDS.get(stage_type, stage_type)
    if os.getenv(f"AGORA_RESEARCH_{stage_type.upper()}_UNAVAILABLE") == "1" or os.getenv(f"AGORA_RESEARCH_{backend_name.upper()}_UNAVAILABLE") == "1":
        raise HTTPException(
            status_code=503, detail=f"Backend execution owner for stage '{stage_type}' ({backend_name}) is currently unavailable"
        )

    SUPPORTED_EXECUTION_STAGES = {"prototype_backtest", "econometric_validation", "derivatives_pricing_risk", "evidence_synthesis"}
    if stage_type not in SUPPORTED_EXECUTION_STAGES and backend_name not in {"vectorbt", "statsmodels", "quantlib", "openclaw_result_synthesis"}:
        raise HTTPException(
            status_code=503, detail=f"Backend execution owner for stage '{stage_type}' ({backend_name}) is absent or not configured"
        )

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

    persisted_art_candidates = (
        ((persisted_stage or {}).get("artifact_refs") if isinstance(persisted_stage, dict) else None)
        or ((persisted_stage or {}).get("approved_artifact_refs") if isinstance(persisted_stage, dict) else None)
        or (persisted_params.get("artifact_refs") if isinstance(persisted_params, dict) else None)
        or (persisted_plan.get("artifact_refs") if isinstance(persisted_plan, dict) else None)
    )
    persisted_art_ids = _extract_artifact_ids(persisted_art_candidates)

    caller_art_candidates = (
        (stage.get("artifact_refs") if isinstance(stage, dict) else None)
        or (stage.get("approved_artifact_refs") if isinstance(stage, dict) else None)
        or body.get("artifact_refs")
        or (plan.get("artifact_refs") if isinstance(plan, dict) else None)
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
        raise HTTPException(
            status_code=400,
            detail=f"Missing required governed dataset or input for stage '{stage_type}'",
        )

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
                persisted_deps_raw = (
                    ((persisted_stage or {}).get("dependencies") if isinstance(persisted_stage, dict) else None)
                    or ((persisted_stage or {}).get("depends_on") if isinstance(persisted_stage, dict) else None)
                )
                caller_deps_raw = (
                    (stage.get("dependencies") if isinstance(stage, dict) else None)
                    or (stage.get("depends_on") if isinstance(stage, dict) else None)
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
                    deps = set(c_deps)

                c_runs = [r for r in store.list_runs() if str(r.get("task_id") or "") == task_id and str(r.get("run_id") or "") != str(run_id)]
                if deps:
                    c_runs = [r for r in c_runs if str(r.get("stage_id") or "") in deps]
                seen_ids = set()
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
                            and (not deps or str(art.get("stage_id") or "") in deps)
                            and str(art.get("run_id") or "") != str(run_id)
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
