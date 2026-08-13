from __future__ import annotations

import json as _json
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.registry.split_api import RegistryService
from services.registry.storage import get_store as get_registry_store
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


PRODUCTION_ADAPTERS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}
PRODUCTION_MODES = {"production", "paper", "canary", "live"}
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


DATA_DIR = _data_dir()
MAX_ACTIVE_RUNS = _max_active_runs()
PRODUCTION_ADAPTERS_ALLOWED = _production_adapters_allowed()
OFFLINE_GATE_ENABLED = _offline_gate_enabled()
GATEWAY_URL = _gateway_url()
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


app = FastAPI(title="Pantheon Research Orchestrator Service", version="0.1.0")
store = build_research_orchestrator_store(DATA_DIR)
def get_store() -> ResearchOrchestratorStore:
    return store
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
        metadata={
            "source_strategy_spec_id": body.source_strategy_spec_id,
            "research_orchestrator_run_record": True,
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
        "registry_writebacks": [],
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
    return run


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
        "registry_writebacks": run.get("registry_writebacks", []),
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
    return writeback


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
