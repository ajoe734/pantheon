from __future__ import annotations

import contextlib
import copy
import json as _json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from agora_dataset_authority import (
    AgoraAuthorityUnavailable,
    build_agora_dataset_authority,
    configured_tenant_id,
    configured_user_id,
)
from inbound_authority import (
    PolicyLearningAuthority,
    PolicyLearningAuthorityError,
    authority_configuration,
    bind_tenant,
    resolve_authority,
)
from store import (
    DEFAULT_CLAIM_BATCH_SIZE,
    DEFAULT_LEASE_SECONDS,
    CandidateStoreCorrupt,
    LeaseHeldError,
    LeaseLostError,
    STATUS_CLAIMED,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_PROCESSED,
    STATUS_PROPOSED,
    PolicyLearningStore,
    build_policy_learning_store,
    candidate_dedupe_key,
    candidate_id_for,
    candidate_tenant_id,
)
from services.research.imitation.agora_dataset_source import (
    AGORA_DATASET_AUTHORITY,
    AgoraDatasetError,
    attach_step_probabilities,
    build_dataset_lineage,
    build_dataset_payload,
)
from services.research.imitation.bc_trainer import train as train_bc
from services.research.imitation.eval_metrics import evaluate as evaluate_policy


PRODUCTION_ADAPTERS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}
STUB_ADAPTER = "stub"
FAIL_CLOSED_SCOPE = "capability_metadata_read_only"
OFFLINE_DISPATCH_ENABLED_SCOPE = "offline_worker_dispatch_enabled"
# Adapters with declared gateway entrypoints that can be routed offline.
OFFLINE_ADAPTERS = {"trl", "finrl", "rllib", "ray_tune", "qlib"}
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


def _offline_gate_enabled() -> bool:
    return os.getenv("PANTHEON_OFFLINE_GATE_ENABLED", "false").lower() == "true"


def _gateway_url() -> str:
    return os.getenv("RESEARCH_WORKER_GATEWAY_URL", "http://research-worker-gateway-svc:8103")


OFFLINE_GATE_ENABLED = _offline_gate_enabled()
GATEWAY_URL = _gateway_url()
STORE_BACKEND = os.getenv("POLICY_LEARNING_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("policy-learning")

# Dataset sourcing mode.  ``product`` is the default and has no seed, fixture,
# or synthesized fallback: when the tenant-scoped Agora dataset authority
# cannot answer, the tick and the candidate degrade truthfully.  ``seed`` is an
# explicitly non-product development mode and stamps every artifact it touches
# so seed-derived output can never be mistaken for authoritative output.
DATASET_MODE_ENV = "POLICY_LEARNING_DATASET_MODE"
PRODUCT_DATASET_MODE = "product"
SEED_DATASET_MODE = "seed"


def _dataset_mode() -> str:
    mode = (os.getenv(DATASET_MODE_ENV) or PRODUCT_DATASET_MODE).strip().lower()
    return SEED_DATASET_MODE if mode == SEED_DATASET_MODE else PRODUCT_DATASET_MODE


def _product_mode() -> bool:
    return _dataset_mode() == PRODUCT_DATASET_MODE


def _route_to_gateway(adapter: str, objective: str, source_refs: List[Dict[str, Any]], constraints: Dict[str, Any], actor_id: str, job_id: str, timestamp: str) -> Optional[Dict[str, Any]]:
    """POST an offline-capable adapter job to the research-worker-gateway."""
    request_body = {
        "worker": adapter,
        "requested_mode": "offline",
        "dispatch_mode": "offline",
        "objective": objective,
        "input_refs": source_refs,
        "parameters": constraints,
        "actor_id": actor_id,
        "idempotency_key": f"pl-{job_id}",
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


class ShadowEvalTickBody(BaseModel):
    tick_id: Optional[str] = None
    eval_type: str = "shadow"
    dataset_refs: List[Dict[str, Any]] = Field(default_factory=list)
    max_datasets: Optional[int] = None
    actor_id: str = "scheduler"
    ticked_at: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None


class AgoraDatasetHandoffBody(BaseModel):
    handoff_id: Optional[str] = Field(
        default=None,
        description="Optional handoff identifier. Derived if omitted.",
    )
    tick_id: Optional[str] = Field(
        default=None,
        description="Optional scheduler tick identifier associated with handoff.",
    )
    dataset_version: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Full tenant-scoped Agora DatasetVersion record mapping.",
    )
    dataset_ref: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Dataset reference containing dataset_version_id and tenant_id.",
    )
    eval_type: str = Field(
        default="shadow",
        description="Imitation evaluation type.",
    )
    actor_id: str = Field(
        default="agora-pipeline",
        description="Actor or pipeline component emitting the handoff.",
    )
    handed_off_at: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    process_immediately: bool = Field(
        default=False,
        description="Deprecated/legacy flag. Production semantics are admit-only; candidates are always proposed and leased by background workers.",
    )


class WorkerClaimBody(BaseModel):
    worker_id: str = "policy-learning-worker"
    batch_size: int = DEFAULT_CLAIM_BATCH_SIZE
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    tenant_id: Optional[str] = None


def _proposal_text(body: ProposalBody) -> str:
    parts = [body.adapter, body.requested_mode, body.objective]
    parts.extend(str(ref.get("type") or "") for ref in body.source_refs)
    parts.extend(str(ref.get("id") or "") for ref in body.source_refs)
    parts.extend(str(key) for key in body.constraints.keys())
    parts.extend(str(value) for value in body.constraints.values())
    return " ".join(parts).lower()


app = FastAPI(title="Pantheon Policy Learning Service", version="0.1.0")
store = build_policy_learning_store(_data_dir(), product_mode=_product_mode())


@app.exception_handler(PolicyLearningAuthorityError)
def _authority_error_handler(request: Request, exc: PolicyLearningAuthorityError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_detail()})


@app.exception_handler(CandidateStoreCorrupt)
def _corrupt_store_handler(request: Request, exc: CandidateStoreCorrupt) -> JSONResponse:
    """Surface a damaged backlog as an outage, never as an empty backlog."""

    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "CANDIDATE_STORE_CORRUPT",
                "message": "The policy-learning candidate backlog is unreadable",
                "path": exc.path,
                "reason": exc.detail,
                "candidate_authority": store.describe(),
            }
        },
    )


def require_authority(
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
    x_pantheon_tenant: Optional[str] = Header(default=None),
) -> PolicyLearningAuthority:
    """Authenticate one imitation-loop request and bind it to one tenant."""

    return resolve_authority(
        authorization=authorization,
        tenant_header=x_tenant_id,
        tenant_alias=x_pantheon_tenant,
    )


register_fastapi_health_routes(
    app,
    "policy-learning",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {"job_count": len(store.list_jobs())},
    details=lambda: {
        "data_dir": _data_dir(),
        "store_backend": STORE_BACKEND,
        "production_adapters_enabled": _production_adapters_allowed(),
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
        "dataset_mode": _dataset_mode(),
        "dataset_authority": dataset_authority().describe(),
        "candidate_authority": store.describe(),
        "inbound_authority": authority_configuration(),
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
        "dataset_mode": _dataset_mode(),
        "dataset_authority": dataset_authority().describe(),
        "candidate_authority": store.describe(),
        "inbound_authority": authority_configuration(),
    }


@app.get("/api/policy-learning/capabilities")
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
        "service": "policy-learning",
        "default_mode": "stub",
        "production_activation": "disabled",
        "offline_gate": "enabled" if OFFLINE_GATE_ENABLED else "disabled",
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
            {"adapter": adapter, **_effective_metadata(adapter, metadata)}
            for adapter, metadata in sorted(CAPABILITY_REGISTRY.items())
        ],
    }


@app.get("/api/policy-learning/jobs")
def list_jobs(status: Optional[str] = None, policy_id: Optional[str] = None) -> List[Dict[str, Any]]:
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
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline":
        # Offline gate open: route activation-ready adapters to the research-worker-gateway.
        gateway_result = _route_to_gateway(adapter, body.objective, body.source_refs, body.constraints, body.actor_id, job_id, timestamp)
        gateway_ref: Dict[str, Any]
        if gateway_result:
            gateway_ref = {"gateway_job_id": gateway_result.get("job_id"), "gateway": "research-worker-gateway"}
            status = "dispatched"
            events.append(_event(timestamp, "proposal_dispatched", f"Offline-gated adapter '{adapter}' routed to research-worker-gateway (gateway_job_id={gateway_result.get('job_id')}).", body.actor_id, events))
        else:
            gateway_ref = {"gateway_job_id": None, "error": "gateway_unavailable"}
            status = "dispatched"
            events.append(_event(timestamp, "proposal_dispatched", f"Offline-gated adapter '{adapter}' dispatch attempted; gateway unavailable.", body.actor_id, events))
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
            "rejection": None,
            "gateway_ref": gateway_ref,
            "events": events,
        }
        store.put_job(job)
        return job
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode not in {"production", "paper", "canary", "live"}:
        status = "rejected"
        rejection = {
            "reason": "offline_mode_required",
            "detail": "Offline-gated policy-learning dispatch requires requested_mode=offline.",
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
        "gateway_ref": job.get("gateway_ref"),
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


DATASET_AUTHORITY = build_agora_dataset_authority()


def dataset_authority() -> Any:
    """Process-wide Agora dataset authority handle."""

    return DATASET_AUTHORITY


def _resolve_tenant_scope(
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve the tenant/user scope a tick or resolution runs under."""

    return (
        (tenant_id or configured_tenant_id() or "").strip(),
        (user_id or configured_user_id() or "").strip(),
    )


def _degradation(reason: str, detail: str, timestamp: str, **extra: Any) -> Dict[str, Any]:
    """Truthful degradation record.

    ``seed_fallback_used`` is recorded as an explicit ``False`` in product mode
    so an auditor can see that the failure produced no substituted data rather
    than having to infer it from an absence.
    """

    record = {
        "status": "degraded",
        "reason": reason,
        "detail": detail,
        "dataset_mode": _dataset_mode(),
        "seed_fallback_used": False,
        "authority": dataset_authority().describe(),
        "degraded_at": timestamp,
    }
    record.update(extra)
    return record


class DatasetResolutionError(RuntimeError):
    """A candidate's dataset could not be resolved from the Agora authority."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def discover_eligible_datasets(
    *,
    tenant_id: str = "",
    user_id: str = "",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Discover tenant-scoped learning-eligible Agora DatasetVersions.

    Discovery is independent of ``POLICY_LEARNING_STORE_BACKEND``: the Agora
    dataset authority is a separate owner with its own connection settings, so
    the default JSON candidate store still sees real data.

    Raises:
        AgoraAuthorityUnavailable: the authority is unresolvable or unreachable.
            Callers must degrade; there is no seed substitution here.
    """

    versions = dataset_authority().list_dataset_versions(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    return [version.to_dataset_ref() for version in versions]


# Non-product development fixture.  Only reachable when
# POLICY_LEARNING_DATASET_MODE=seed is set explicitly; the product path never
# reads it, and anything derived from it is stamped seed_fallback_used=true.
SEED_DATASET = {
    "dataset_id": "traj-smoke-2026-05-16",
    "strategy_id": "alpha-mean-reversion",
    "source_dataset_refs": ["dataset://feedback/approved/2026-05-16"],
    "source_strategy_spec_id": "strat-alpha-mean-reversion-v2",
    "sessions": [
        {
            "trajectory_id": "traj-001",
            "actor_id": "trader-01",
            "actor_role": "operator",
            "decision": "approve",
            "target": {
                "registry_id": "reg-alpha-1",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "candidate",
            },
            "steps": [
                {"observation": [0.9, 0.1, -0.2], "action": "buy_small", "reward": 0.3, "feedback_event_id": "evt-001"},
                {"observation": [0.8, 0.2, -0.1], "action": "buy_small", "reward": 0.2, "feedback_event_id": "evt-002"},
            ],
        },
        {
            "trajectory_id": "traj-002",
            "actor_id": "trader-02",
            "actor_role": "approver",
            "decision": "edit",
            "target": {
                "registry_id": "reg-alpha-1",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "paper",
            },
            "steps": [
                {"observation": [-0.85, -0.2, 0.55], "action": "reduce_risk", "reward": 0.15, "feedback_event_id": "evt-003"},
            ],
        },
    ],
}


def _seed_dataset_payload(dataset_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    payload = copy.deepcopy(SEED_DATASET)
    payload["dataset_id"] = dataset_id
    lineage = {
        "authority": "policy_learning_seed_fixture",
        "dataset_id": dataset_id,
        "dataset_version_ids": [],
        "tenant_id": "",
        "user_id": "",
        "source_dataset_refs": list(payload["source_dataset_refs"]),
        "seed_fallback_used": True,
        "authoritative": False,
        "product_mode": False,
        "resolved_at": utc_now(),
    }
    return payload, lineage


def resolve_candidate_dataset(candidate: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Resolve a candidate's real dataset payload and its lineage.

    Product mode reads the tenant-scoped Agora DatasetVersion the candidate
    cites and fails closed when it cannot.  No branch of this function returns
    seed data while ``POLICY_LEARNING_DATASET_MODE`` is ``product``.

    Returns:
        ``(dataset_payload, dataset_lineage)``.

    Raises:
        DatasetResolutionError: the authority is unavailable, the version is
            invisible in this tenant, or the content is not learning-eligible.
    """

    dataset_ref = candidate.get("dataset_ref") or {}
    dataset_version_id = str(
        dataset_ref.get("dataset_version_id") or dataset_ref.get("id") or dataset_ref.get("dataset_id") or ""
    ).strip()

    if not _product_mode():
        return _seed_dataset_payload(dataset_version_id or "ds-seed")

    tenant_id, user_id = _resolve_tenant_scope(
        str(dataset_ref.get("tenant_id") or ""),
        str(dataset_ref.get("user_id") or ""),
    )
    if isinstance(dataset_ref.get("record"), dict):
        with contextlib.suppress(Exception):
            dataset_authority().register_version(dataset_ref["record"])
    try:
        versions = dataset_authority().get_dataset_versions(
            dataset_version_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        payload = build_dataset_payload(versions)
        lineage = build_dataset_lineage(versions, payload)
    except AgoraAuthorityUnavailable as exc:
        raise DatasetResolutionError(exc.reason, exc.detail) from exc
    except AgoraDatasetError as exc:
        raise DatasetResolutionError(getattr(exc, "reason", "agora_dataset_error"), str(exc)) from exc

    lineage["authoritative"] = True
    lineage["product_mode"] = True
    return payload, lineage


def _train_and_evaluate(payload: Dict[str, Any], lineage: Dict[str, Any]) -> Dict[str, Any]:
    """Run the CPU behavior-cloning baseline and its shadow evaluation.

    The returned block keeps the dataset lineage attached to both the trained
    artifact and the evaluation so a candidate can never be read back without
    knowing which tenant-scoped DatasetVersion produced it.
    """

    artifact = train_bc(payload)
    attach_step_probabilities(artifact, payload)
    evaluation = evaluate_policy(artifact, payload)

    artifact_lineage = dict(artifact.get("lineage") or {})
    artifact_lineage["dataset_lineage"] = lineage
    artifact["lineage"] = artifact_lineage

    return {
        "artifact": artifact,
        "evaluation": evaluation,
        "training_evaluation": {
            "evaluator_id": evaluation.get("evaluator_id"),
            "evaluation_timestamp": evaluation.get("evaluation_timestamp"),
            "action_match_rate": evaluation.get("action_match_rate"),
            "return_gap": evaluation.get("return_gap"),
            "kl_divergence": evaluation.get("kl_divergence"),
            "artifact_checksum": artifact.get("checksum"),
            "dataset_lineage": lineage,
        },
    }


def _apply_processed_result(
    candidate: Dict[str, Any],
    result: Dict[str, Any],
    lineage: Dict[str, Any],
    timestamp: str,
) -> Dict[str, Any]:
    evaluation = result["evaluation"]
    artifact = result["artifact"]
    candidate["status"] = STATUS_PROCESSED
    candidate["metrics"] = evaluation.get("metrics", {})
    candidate["evaluation_summary"] = {
        "action_match_rate": evaluation.get("action_match_rate"),
        "return_gap": evaluation.get("return_gap"),
        "kl_divergence": evaluation.get("kl_divergence"),
        "evaluator_id": evaluation.get("evaluator_id"),
        "evaluation_timestamp": evaluation.get("evaluation_timestamp"),
    }
    candidate["policy_weights"] = artifact.get("policy", {})
    candidate["lineage"] = artifact.get("lineage", {})
    candidate["dataset_lineage"] = lineage
    candidate["training_evaluation"] = result["training_evaluation"]
    candidate["artifact_checksum"] = artifact.get("checksum")
    candidate["dataset_mode"] = _dataset_mode()
    candidate["seed_fallback_used"] = bool(lineage.get("seed_fallback_used"))
    candidate["authoritative"] = bool(lineage.get("authoritative"))
    candidate.pop("error_message", None)
    candidate.pop("degradation", None)
    candidate["updated_at"] = timestamp
    return candidate


def process_claimed_candidate(claim: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve, train, evaluate, and settle one leased candidate.

    A dataset-authority failure lands the candidate in ``degraded`` — visibly
    separate from ``failed`` — because the work was never attempted against
    real data, and the alternative (training on a fixture) is exactly what this
    task forbids.  A trainer or evaluator failure lands in ``failed`` / DLQ.
    """

    timestamp = utc_now()
    candidate = copy.deepcopy(claim)
    lease_token = str(candidate.get("lease_token") or "")
    try:
        payload, lineage = resolve_candidate_dataset(candidate)
    except DatasetResolutionError as exc:
        print(f"DEBUG RESOLVE EXCEPTION: {exc.reason} - {exc.detail}")
        candidate["status"] = STATUS_DEGRADED
        candidate["degradation"] = _degradation(exc.reason, exc.detail, timestamp)
        candidate["seed_fallback_used"] = False
        candidate["authoritative"] = False
        candidate["updated_at"] = timestamp
    else:
        try:
            result = _train_and_evaluate(payload, lineage)
        except Exception as exc:  # trainer / evaluator failure -> DLQ
            candidate["status"] = STATUS_FAILED
            candidate["error_message"] = str(exc)
            candidate["dataset_lineage"] = lineage
            candidate["updated_at"] = timestamp
        else:
            _apply_processed_result(candidate, result, lineage, timestamp)

    try:
        return store.settle_candidate(candidate, lease_token=lease_token)
    except LeaseLostError:
        # Another worker reclaimed the expired lease and is authoritative for
        # this candidate; discard this result instead of overwriting theirs.
        candidate["status"] = "lease_lost"
        return candidate


def run_worker_cycle(
    *,
    worker_id: str,
    batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    tenant_id: str = "",
) -> Dict[str, Any]:
    """Claim a batch of backlog work and process it under lease."""

    claims = store.claim_candidates(
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        tenant_id=tenant_id,
    )
    settled = [process_claimed_candidate(claim) for claim in claims]
    counts: Dict[str, int] = {}
    for record in settled:
        key = str(record.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return {
        "worker_id": worker_id,
        "claimed_count": len(claims),
        "processed_count": counts.get(STATUS_PROCESSED, 0),
        "degraded_count": counts.get(STATUS_DEGRADED, 0),
        "failed_count": counts.get(STATUS_FAILED, 0),
        "lease_lost_count": counts.get("lease_lost", 0),
        "status_counts": counts,
        "candidate_ids": [str(record.get("candidate_id") or "") for record in settled],
    }


def _dataset_version_id(dataset_ref: Dict[str, Any]) -> str:
    if not isinstance(dataset_ref, dict):
        return ""
    return str(
        dataset_ref.get("dataset_version_id") or dataset_ref.get("id") or dataset_ref.get("dataset_id") or ""
    ).strip()


@app.post("/api/policy-learning/shadow-eval-tick", status_code=201)
def shadow_eval_tick(
    body: ShadowEvalTickBody,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Schedule a shadow / imitation evaluation tick over real trace datasets.

    The tick runs inside the authenticated caller's tenant.  A ``tenant_id`` in
    the body is only accepted when it repeats that tenant, so the route cannot
    be used to schedule work, or to probe for datasets, in someone else's
    scope.

    With no explicit ``dataset_refs`` the tick discovers tenant-scoped Agora
    DatasetVersions through the dataset authority.  If that authority cannot
    answer, the tick fails closed with HTTP 503 and a truthful degradation
    record; it never falls back to seed data in product mode.

    Produces gated ShadowImitationCandidate records in proposed state.
    Production training and artifact mutation remain fail-closed until a
    separate experiment approval gate is explicitly activated.
    """
    timestamp = body.ticked_at or utc_now()
    tick_id = body.tick_id or f"shadow-tick-{timestamp[:10].replace('-', '')}"
    eval_type = body.eval_type.strip().lower() if body.eval_type else "shadow"
    tenant_id = bind_tenant(authority, body.tenant_id)
    _, user_id = _resolve_tenant_scope(tenant_id, body.user_id)

    dataset_refs = list(body.dataset_refs)
    dataset_source = "explicit_refs"
    if not dataset_refs:
        dataset_source = "agora_dataset_version" if _product_mode() else "seed_mode"
        try:
            dataset_refs = discover_eligible_datasets(
                tenant_id=tenant_id,
                user_id=user_id,
                limit=body.max_datasets,
            )
        except AgoraAuthorityUnavailable as exc:
            degradation = _degradation(
                exc.reason,
                exc.detail,
                timestamp,
                tick_id=tick_id,
                eval_type=eval_type,
                tenant_id=tenant_id,
                candidate_count=0,
                production_training="fail_closed",
            )
            raise HTTPException(status_code=503, detail=degradation)

    if body.max_datasets is not None and len(dataset_refs) > body.max_datasets:
        dataset_refs = dataset_refs[: body.max_datasets]

    created_ids: List[str] = []
    skipped_ids: List[str] = []
    rejected: List[Dict[str, Any]] = []

    for ref in dataset_refs:
        version_id = _dataset_version_id(ref)
        if not version_id:
            rejected.append({"reason": "dataset_version_id_missing", "dataset_ref": ref})
            continue
        ref_tenant = str(ref.get("tenant_id") or "").strip()
        if ref_tenant and ref_tenant != tenant_id:
            # An explicit ref naming another tenant is refused rather than
            # re-stamped: re-stamping would attach this tenant's candidate to a
            # dataset version it has no authority over.
            rejected.append({"reason": "dataset_ref_tenant_mismatch", "dataset_ref": ref})
            continue

        bound_ref = dict(ref)
        bound_ref["tenant_id"] = tenant_id
        dedupe_key = candidate_dedupe_key(tenant_id, tick_id, version_id)
        candidate_id = candidate_id_for(tenant_id, tick_id, version_id)
        candidate = {
            "id": candidate_id,
            "candidate_id": candidate_id,
            "dedupe_key": dedupe_key,
            "tick_id": tick_id,
            "eval_type": eval_type,
            "dataset_ref": bound_ref,
            "dataset_version_id": version_id,
            "dataset_source": dataset_source,
            "dataset_mode": _dataset_mode(),
            "tenant_id": tenant_id,
            "status": STATUS_PROPOSED,
            "attempt_count": 0,
            "production_training": "fail_closed",
            "experiment_approval_gate": "required",
            "runtime_effect": "none",
            "gate_note": "Candidate requires experiment approval and deployment gate before any production training.",
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": body.actor_id,
            "scheduled_by": authority.actor_id,
        }
        # The store owns duplicate suppression.  A read-then-write check here
        # would still let two concurrent ticks both observe "not present".
        _, created = store.create_candidate_if_absent(candidate)
        if created:
            created_ids.append(candidate_id)
        else:
            skipped_ids.append(candidate_id)

    return {
        "status": "ok",
        "tick_id": tick_id,
        "eval_type": eval_type,
        "dataset_source": dataset_source,
        "dataset_mode": _dataset_mode(),
        "tenant_id": tenant_id,
        "actor_id": authority.actor_id,
        "seed_fallback_used": not _product_mode(),
        "candidate_count": len(created_ids),
        "skipped_count": len(skipped_ids),
        "skipped_ids": skipped_ids,
        "rejected_refs": rejected,
        "candidate_ids": created_ids,
        "production_training": "fail_closed",
        "ticked_at": timestamp,
    }


@app.post("/api/policy-learning/agora-handoff", status_code=201)
@app.post("/api/policy-learning/handoff", status_code=201)
def agora_dataset_handoff(
    body: AgoraDatasetHandoffBody,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Consume a durable Agora DatasetVersion handoff through the production consumer boundary.

    Validates tenant scope, ingests the dataset version record into the dataset
    authority, durably stores the proposed/processed shadow candidate, and only
    acknowledges after durable ingestion succeeds.

    Idempotent: duplicate submissions return the existing candidate and
    acknowledged status without duplicating backlog state or mutating
    authoritative lineage.
    """
    timestamp = body.handed_off_at or utc_now()
    tenant_id = bind_tenant(authority, body.tenant_id)
    _, user_id = _resolve_tenant_scope(tenant_id, body.user_id)

    record = body.dataset_version or {}
    ref = body.dataset_ref or {}

    if record:
        rec_tenant = str(record.get("tenant_id") or "").strip()
        if rec_tenant and rec_tenant != tenant_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dataset_ref_tenant_mismatch",
                    "message": f"DatasetVersion tenant {rec_tenant!r} does not match caller tenant {tenant_id!r}",
                },
            )
        try:
            version_obj = dataset_authority().register_version(record)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dataset_version_invalid",
                    "message": f"Invalid Agora DatasetVersion record: {exc}",
                },
            ) from exc
        dataset_version_id = version_obj.dataset_version_id
        bound_ref = version_obj.to_dataset_ref()
        bound_ref["record"] = dict(record)
    elif ref:
        dataset_version_id = _dataset_version_id(ref)
        if not dataset_version_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dataset_version_id_missing",
                    "message": "A dataset_version_id is required for a DatasetVersion handoff",
                },
            )
        ref_tenant = str(ref.get("tenant_id") or "").strip()
        if ref_tenant and ref_tenant != tenant_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dataset_ref_tenant_mismatch",
                    "message": f"Dataset ref tenant {ref_tenant!r} does not match caller tenant {tenant_id!r}",
                },
            )
        bound_ref = dict(ref)
        bound_ref["tenant_id"] = tenant_id
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "dataset_version_missing",
                "message": "Either dataset_version or dataset_ref is required for handoff",
            },
        )

    tick_id = body.tick_id or body.handoff_id or f"handoff-tick-{dataset_version_id}"
    eval_type = body.eval_type.strip().lower() if body.eval_type else "shadow"
    dedupe_key = candidate_dedupe_key(tenant_id, tick_id, dataset_version_id)
    candidate_id = candidate_id_for(tenant_id, tick_id, dataset_version_id)

    candidate = {
        "id": candidate_id,
        "candidate_id": candidate_id,
        "handoff_id": body.handoff_id or candidate_id,
        "dedupe_key": dedupe_key,
        "tick_id": tick_id,
        "eval_type": eval_type,
        "dataset_ref": bound_ref,
        "dataset_version_id": dataset_version_id,
        "dataset_source": "agora_dataset_version_handoff",
        "dataset_mode": _dataset_mode(),
        "tenant_id": tenant_id,
        "status": STATUS_PROPOSED,
        "attempt_count": 0,
        "production_training": "fail_closed",
        "experiment_approval_gate": "required",
        "runtime_effect": "none",
        "gate_note": "Candidate requires experiment approval and deployment gate before any production training.",
        "created_at": timestamp,
        "updated_at": timestamp,
        "created_by": body.actor_id,
        "scheduled_by": authority.actor_id,
    }

    # Atomically write to candidate store - durable ingestion acknowledgment gate
    stored_candidate, created = store.create_candidate_if_absent(candidate)

    return {
        "status": "acknowledged",
        "handoff_id": body.handoff_id or candidate_id,
        "candidate_id": candidate_id,
        "dedupe_key": dedupe_key,
        "dataset_version_id": dataset_version_id,
        "tenant_id": tenant_id,
        "created": created,
        "candidate_status": stored_candidate.get("status", STATUS_PROPOSED),
        "metrics": stored_candidate.get("metrics", {}),
        "evaluation_summary": stored_candidate.get("evaluation_summary", {}),
        "dataset_lineage": stored_candidate.get("dataset_lineage", {}),
        "seed_fallback_used": bool(stored_candidate.get("seed_fallback_used", False)),
        "authoritative": bool(stored_candidate.get("authoritative", True)),
        "production_training": "fail_closed",
        "acknowledged_at": timestamp,
    }


def _tenant_candidates(
    authority: PolicyLearningAuthority,
    *,
    tick_id: Optional[str] = None,
    eval_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    candidates = [
        candidate
        for candidate in store.list_candidates()
        if candidate_tenant_id(candidate) == authority.tenant_id
    ]
    if tick_id:
        candidates = [c for c in candidates if c.get("tick_id") == tick_id]
    if eval_type:
        candidates = [c for c in candidates if str(c.get("eval_type") or "").lower() == eval_type.lower()]
    if status:
        candidates = [c for c in candidates if str(c.get("status") or "").lower() == status.lower()]
    return candidates


def _tenant_candidate(candidate_id: str, authority: PolicyLearningAuthority) -> Dict[str, Any]:
    """Load one candidate inside the authenticated tenant.

    A candidate owned by another tenant answers 404 rather than 403 so the
    route cannot be used to enumerate which candidate ids exist elsewhere.
    """

    candidate = store.get_candidate(candidate_id)
    if not candidate or candidate_tenant_id(candidate) != authority.tenant_id:
        raise HTTPException(status_code=404, detail="shadow imitation candidate not found")
    return candidate


@app.get("/api/policy-learning/candidates")
def list_candidates(
    tick_id: Optional[str] = None,
    eval_type: Optional[str] = None,
    status: Optional[str] = None,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> List[Dict[str, Any]]:
    return _tenant_candidates(
        authority,
        tick_id=tick_id,
        eval_type=eval_type,
        status=status,
    )


@app.get("/api/policy-learning/candidates/{candidate_id}")
def get_candidate(
    candidate_id: str,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    return _tenant_candidate(candidate_id, authority)


@app.get("/api/policy-learning/worker/backlog")
def get_worker_backlog(
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> List[Dict[str, Any]]:
    """Return the list of unclaimed pending items in this tenant's backlog."""
    return _tenant_candidates(authority, status=STATUS_PROPOSED)


@app.get("/api/policy-learning/worker/claims")
def get_worker_claims(
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> List[Dict[str, Any]]:
    """Return this tenant's candidates currently leased by a worker."""
    return _tenant_candidates(authority, status=STATUS_CLAIMED)


@app.get("/api/policy-learning/worker/degraded")
def get_worker_degraded(
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> List[Dict[str, Any]]:
    """Return candidates whose dataset authority lookup degraded.

    These are separate from the DLQ: no training was attempted, and no seed
    data was substituted for the missing tenant-scoped dataset.
    """
    return _tenant_candidates(authority, status=STATUS_DEGRADED)


@app.get("/api/policy-learning/worker/dlq")
def get_worker_dlq(
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> List[Dict[str, Any]]:
    """Return the list of failed items in this tenant's DLQ."""
    return _tenant_candidates(authority, status=STATUS_FAILED)


def _requeue(candidate_id: str) -> Dict[str, Any]:
    """Return a candidate to the backlog unless a worker still owns it."""

    try:
        return store.requeue_candidate(candidate_id)
    except LeaseHeldError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "CANDIDATE_LEASE_HELD", "message": str(exc)},
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="shadow imitation candidate not found") from exc


@app.post("/api/policy-learning/worker/dlq/{candidate_id}/replay")
def replay_dlq_item(
    candidate_id: str,
    body: Optional[WorkerClaimBody] = None,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Return a failed or degraded candidate to the backlog and re-run it."""
    candidate = _tenant_candidate(candidate_id, authority)
    if candidate.get("status") not in (STATUS_FAILED, STATUS_DEGRADED):
        raise HTTPException(status_code=400, detail="candidate is not in DLQ")
    claim = body or WorkerClaimBody()
    bind_tenant(authority, claim.tenant_id)
    _requeue(candidate_id)
    run_worker_cycle(
        worker_id=f"{claim.worker_id}-replay",
        batch_size=claim.batch_size,
        lease_seconds=claim.lease_seconds,
        tenant_id=authority.tenant_id,
    )
    return _tenant_candidate(candidate_id, authority)


@app.post("/api/policy-learning/worker/retry/{candidate_id}")
def retry_candidate(
    candidate_id: str,
    body: Optional[WorkerClaimBody] = None,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Retry processing a candidate this tenant owns."""
    _tenant_candidate(candidate_id, authority)
    claim = body or WorkerClaimBody()
    bind_tenant(authority, claim.tenant_id)
    _requeue(candidate_id)
    run_worker_cycle(
        worker_id=f"{claim.worker_id}-retry",
        batch_size=claim.batch_size,
        lease_seconds=claim.lease_seconds,
        tenant_id=authority.tenant_id,
    )
    return _tenant_candidate(candidate_id, authority)


@app.post("/api/policy-learning/worker/claim")
def claim_backlog(
    body: Optional[WorkerClaimBody] = None,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Take an exclusive expiring lease on this tenant's backlog work.

    Two workers calling this concurrently receive disjoint candidate sets, and
    the claim never reaches outside the authenticated tenant.
    """
    claim = body or WorkerClaimBody()
    bind_tenant(authority, claim.tenant_id)
    claims = store.claim_candidates(
        worker_id=claim.worker_id,
        batch_size=claim.batch_size,
        lease_seconds=claim.lease_seconds,
        tenant_id=authority.tenant_id,
    )
    return {
        "status": "ok",
        "worker_id": claim.worker_id,
        "tenant_id": authority.tenant_id,
        "claimed_count": len(claims),
        "claims": [
            {
                "candidate_id": record.get("candidate_id"),
                "lease_token": record.get("lease_token"),
                "lease_expires_at": record.get("lease_expires_at"),
                "attempt_count": record.get("attempt_count"),
            }
            for record in claims
        ],
    }


@app.post("/api/policy-learning/worker/process")
def trigger_backlog_processing(
    body: Optional[WorkerClaimBody] = None,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Claim and process a batch of this tenant's backlog work under lease."""
    claim = body or WorkerClaimBody()
    bind_tenant(authority, claim.tenant_id)
    result = run_worker_cycle(
        worker_id=claim.worker_id,
        batch_size=claim.batch_size,
        lease_seconds=claim.lease_seconds,
        tenant_id=authority.tenant_id,
    )
    return {"status": "ok", "tenant_id": authority.tenant_id, **result}


@app.post("/api/policy-learning/worker/restart")
def restart_worker(
    body: Optional[WorkerClaimBody] = None,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Recover backlog work orphaned by a crashed or restarted worker.

    Only claims whose lease has expired return to the backlog; a candidate held
    by a live worker keeps its owner, so a restart of one worker cannot steal
    in-flight work from its peer.  Terminal candidates are left alone, and the
    recovery sweep stays inside the authenticated tenant.
    """
    claim = body or WorkerClaimBody()
    bind_tenant(authority, claim.tenant_id)
    released = store.release_expired_leases(tenant_id=authority.tenant_id)
    result = run_worker_cycle(
        worker_id=claim.worker_id,
        batch_size=claim.batch_size,
        lease_seconds=claim.lease_seconds,
        tenant_id=authority.tenant_id,
    )
    return {
        "status": "ok",
        "tenant_id": authority.tenant_id,
        "released_count": len(released),
        "released_candidate_ids": released,
        **result,
    }


@app.get("/api/policy-learning/candidates/{candidate_id}/governance")
def candidate_governance(
    candidate_id: str,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Report the gates standing between a candidate and any runtime effect."""
    return _gate_evaluation(_tenant_candidate(candidate_id, authority))


@app.post("/api/policy-learning/candidates/{candidate_id}/promote", status_code=409)
def promote_candidate(
    candidate_id: str,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Fail closed: shadow candidates cannot be promoted from this service.

    A shadow imitation candidate is research output.  Promotion requires an
    experiment approval and a deployment gate that this service boundary does
    not hold, so this endpoint always refuses and never mutates the candidate,
    a registry artifact, or a RuntimeBinding.
    """
    candidate = _tenant_candidate(candidate_id, authority)
    raise HTTPException(status_code=409, detail=_gate_evaluation(candidate))


def _gate_evaluation(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "status": candidate.get("status"),
        "promotion_allowed": False,
        "runtime_effect": "none",
        "production_training": "fail_closed",
        "gates": {
            "experiment_approval": {
                "required": True,
                "satisfied": False,
                "owner": "experiment approval authority",
            },
            "deployment_gate": {
                "required": True,
                "satisfied": False,
                "owner": "deployment and RuntimeBinding authority",
            },
        },
        "denied_authorities": [
            "registry_write",
            "runtime_binding_mutation",
            "deployment_stage_change",
            "capital_binding",
            "order_routing",
        ],
        "detail": (
            "Shadow imitation candidates are research output. Promotion requires "
            "experiment approval and a deployment gate held outside policy-learning."
        ),
        "dataset_lineage": candidate.get("dataset_lineage"),
        "evaluated_at": utc_now(),
    }


@app.get("/api/policy-learning/worker/readback/{candidate_id}")
def readback_candidate_target(
    candidate_id: str,
    authority: PolicyLearningAuthority = Depends(require_authority),
) -> Dict[str, Any]:
    """Target readback for a candidate inside the authenticated tenant.

    Readback returns the full record, including the dataset lineage and the
    trained policy weights, so it is tenant-bound like every other route here.
    """
    return _tenant_candidate(candidate_id, authority)
