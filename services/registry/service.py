"""
BP5-SVC-002: FastAPI registry service exposing the split artifact_state / deployment_stage API.

Endpoints map to §8 operations in services/registry/contract.md:
- POST   /api/registry/entries                          → register()
- GET    /api/registry/entries/{registry_id}            → get()
- GET    /api/registry/strategies/{strategy_id}/entries → list_by_strategy()
- POST   /api/registry/entries/{registry_id}/advance    → advance_artifact_state()
- GET    /api/registry/strategies/{strategy_id}/latest-approved → resolve_latest_approved()
- GET    /api/registry/strategies/{strategy_id}/deployment-view → resolve_deployment_view()

Internal (deployment service calls):
- PUT    /api/registry/entries/{registry_id}/deployment-summary → update_deployment_summary()
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .models import (
    ArtifactType,
    ArtifactState,
    DeploymentStage,
    DeploymentView,
    Lineage,
    RegistryEntryCreate,
    RegistryEntryView,
    StorageBackend,
    StorageRef,
)
from .split_api import RegistryError, RegistryNotFoundError, RegistryService
from .storage import get_store
from .strategy_artifact import (
    build_strategy_artifact_registry_payload,
    ensure_builtin_strategy_artifacts,
    mutate_strategy_artifact,
    strategy_artifact_checksum,
    validate_strategy_artifact,
)

# Uvicorn attaches handlers only to its own loggers, so without this the root
# logger keeps its default WARNING level and no handler at all: every
# application INFO record is dropped before it reaches the container log.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def registry_lifespan(_: FastAPI):
    """Fail service startup if a checked-in built-in cannot be registered."""
    get_registry_service()
    yield


app = FastAPI(
    title="Pantheon Registry Service",
    description="Artifact-state and deployment-stage split API per BP5-SVC-002",
    version="0.1.0",
    lifespan=registry_lifespan,
)


def get_registry_service() -> RegistryService:
    """Build the service and idempotently expose checked-in built-in artifacts."""
    registry_service = RegistryService(get_store())
    ensure_builtin_strategy_artifacts(registry_service)
    return registry_service


# -- Request/Response wrappers --------------------------------------------

class AdvanceRequest(BaseModel):
    target_state: ArtifactState
    approver: Optional[str] = None
    approval_decision_id: Optional[str] = None


class DeploymentSummaryUpdate(BaseModel):
    current_stage: DeploymentStage
    deployment_plan_id: Optional[str] = None
    runtime_binding_id: Optional[str] = None


class StrategySpecRegisterRequest(BaseModel):
    strategy_id: str
    version: str
    artifact_state: ArtifactState = ArtifactState.DRAFT
    registry_id: Optional[str] = None
    source_seed_id: Optional[str] = None
    lineage: Optional[dict[str, Any]] = None
    storage_ref: Optional[dict[str, Any]] = None
    checksum: Optional[str] = None
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    rollback_target: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    strategy_spec: Optional[dict[str, Any]] = None


class StrategyArtifactRegisterRequest(BaseModel):
    registry_id: Optional[str] = None
    artifact_state: ArtifactState = ArtifactState.CANDIDATE
    strategy_artifact: dict[str, Any]
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    rollback_target: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class StrategyArtifactMutationRequest(BaseModel):
    new_artifact_id: str
    new_version: str
    parameter_updates: dict[str, Any]
    source_run_ids: list[str]


# -- Error handling -------------------------------------------------------

@app.exception_handler(RegistryNotFoundError)
async def registry_not_found_handler(request: Request, exc: RegistryNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RegistryError)
async def registry_error_handler(request: Request, exc: RegistryError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _strategy_spec_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _strategy_spec_register_payload(body: StrategySpecRegisterRequest) -> RegistryEntryCreate:
    strategy_id = body.strategy_id.strip()
    if not strategy_id:
        raise RegistryError("strategy_id is required")

    strategy_spec = body.strategy_spec
    if strategy_spec is not None:
        embedded_strategy_id = str(strategy_spec.get("strategy_id") or "").strip()
        if embedded_strategy_id and embedded_strategy_id != strategy_id:
            raise RegistryError(
                "Inline StrategySpec strategy_id must match the registry strategy_id."
            )

    lineage = Lineage.from_dict(body.lineage or {})
    source_seed_id = str(body.source_seed_id or "").strip()
    if source_seed_id:
        source_run_ids = list(lineage.source_run_ids or [])
        if source_seed_id not in source_run_ids:
            source_run_ids.append(source_seed_id)
        lineage.source_run_ids = source_run_ids

    if lineage.is_empty():
        raise RegistryError(
            "StrategySpec registry entries require lineage. "
            "Provide lineage or source_seed_id before registering the artifact."
        )

    if body.storage_ref:
        storage_ref = StorageRef.from_dict(body.storage_ref)
    elif strategy_spec is not None:
        storage_ref = StorageRef(
            backend=StorageBackend.INLINE,
            path="$.entry.metadata.strategy_spec",
        )
    else:
        raise RegistryError(
            "StrategySpec registry entries require storage_ref unless an inline strategy_spec is provided."
        )

    checksum = str(body.checksum or "").strip()
    if not checksum and strategy_spec is not None:
        checksum = _strategy_spec_checksum(strategy_spec)
    if not checksum:
        raise RegistryError(
            "StrategySpec registry entries require checksum unless an inline strategy_spec is provided."
        )

    metadata = dict(body.metadata or {})
    if source_seed_id:
        metadata.setdefault("source_seed_id", source_seed_id)
    if strategy_spec is not None:
        metadata.setdefault("strategy_spec", strategy_spec)

    return RegistryEntryCreate(
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=strategy_id,
        version=body.version,
        artifact_state=body.artifact_state,
        lineage=lineage,
        storage_ref=storage_ref,
        checksum=checksum,
        producer_run_id=body.producer_run_id or source_seed_id or None,
        evaluation_summary=body.evaluation_summary,
        rollback_target=body.rollback_target,
        metadata=metadata,
    )


def _ensure_strategy_spec_view(view: RegistryEntryView, registry_id: str) -> RegistryEntryView:
    if view.entry.artifact_type != ArtifactType.STRATEGY_SPEC:
        raise RegistryNotFoundError(f"StrategySpec registry entry not found: {registry_id}")
    return view


def _ensure_strategy_spec_registration_matches(
    view: RegistryEntryView,
    create_payload: RegistryEntryCreate,
    registry_id: str,
) -> RegistryEntryView:
    """Validate StrategySpec create-if-absent replay against existing content."""

    view = _ensure_strategy_spec_view(view, registry_id)
    entry = view.entry
    if (
        entry.strategy_id != create_payload.strategy_id
        or entry.version != create_payload.version
        or entry.lineage.to_dict() != create_payload.lineage.to_dict()
        or entry.storage_ref.to_dict() != create_payload.storage_ref.to_dict()
        or entry.checksum != create_payload.checksum
        or entry.producer_run_id != create_payload.producer_run_id
        or entry.evaluation_summary != create_payload.evaluation_summary
        or entry.rollback_target != create_payload.rollback_target
        or entry.metadata != create_payload.metadata
    ):
        raise RegistryError(
            f"StrategySpec registry_id already exists with different content: {registry_id}"
        )
    return view


def _ensure_strategy_artifact_view(
    view: RegistryEntryView,
    registry_id: str,
) -> RegistryEntryView:
    if not _is_strategy_artifact_view(view):
        raise RegistryNotFoundError(
            f"StrategyArtifact registry entry not found: {registry_id}"
        )
    return view


def _is_strategy_artifact_view(view: RegistryEntryView) -> bool:
    """Require a valid embedded payload and a fully consistent envelope."""
    entry = view.entry
    embedded = (entry.metadata or {}).get("strategy_artifact")
    if (
        entry.artifact_type != ArtifactType.EXECUTION_BUNDLE
        or not isinstance(embedded, dict)
    ):
        return False
    try:
        validate_strategy_artifact(embedded)
        checksum = strategy_artifact_checksum(embedded)
    except RegistryError:
        return False
    return bool(
        entry.registry_id == embedded["artifact_id"]
        and entry.strategy_id == embedded["strategy_id"]
        and entry.version == embedded["version"]
        and entry.lineage.to_dict() == embedded["lineage"]
        and entry.checksum == checksum
        and entry.storage_ref.backend == StorageBackend.INLINE
        and entry.storage_ref.path == "$.entry.metadata.strategy_artifact"
    )


def _strategy_artifact_registration(
    payload: StrategyArtifactRegisterRequest,
) -> dict[str, Any]:
    return {
        "registry_id": payload.registry_id,
        "artifact_state": payload.artifact_state.value,
        "strategy_artifact": payload.strategy_artifact,
        "producer_run_id": payload.producer_run_id,
        "evaluation_summary": payload.evaluation_summary,
        "rollback_target": payload.rollback_target,
        "metadata": payload.metadata,
    }


def _register_strategy_artifact(
    registry_service: RegistryService,
    registration: dict[str, Any],
) -> RegistryEntryView:
    registry_id, create_payload = build_strategy_artifact_registry_payload(registration)
    view, created = registry_service.register_if_absent(create_payload, registry_id)
    if created:
        return view
    view = _ensure_strategy_artifact_view(view, registry_id)
    expected_artifact = (create_payload.metadata or {}).get("strategy_artifact")
    existing_artifact = (view.entry.metadata or {}).get("strategy_artifact")
    if (
        view.entry.checksum != create_payload.checksum
        or existing_artifact != expected_artifact
        or view.entry.strategy_id != create_payload.strategy_id
        or view.entry.version != create_payload.version
        or view.entry.lineage.to_dict() != create_payload.lineage.to_dict()
        or view.entry.storage_ref.to_dict() != create_payload.storage_ref.to_dict()
        or view.entry.producer_run_id != create_payload.producer_run_id
        or view.entry.evaluation_summary != create_payload.evaluation_summary
        or view.entry.rollback_target != create_payload.rollback_target
        or view.entry.metadata != create_payload.metadata
    ):
        raise RegistryError(
            f"StrategyArtifact registry_id already exists with different content: {registry_id}"
        )
    return view


# -- Registry entry endpoints (§8 operations) -----------------------------

@app.post("/api/registry/entries", response_model=RegistryEntryView)
async def register_entry(payload: RegistryEntryCreate):
    """Create a new draft or candidate registry entry."""
    registry_id = f"reg-{payload.strategy_id}-{payload.version}-{uuid.uuid4().hex[:8]}"
    registry_service = get_registry_service()
    try:
        return registry_service.register(payload, registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/registry/entries/{registry_id}", response_model=RegistryEntryView)
async def get_entry(registry_id: str):
    """Read one registry entry with derived deployment_stage."""
    registry_service = get_registry_service()
    try:
        return registry_service.get(registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/api/registry/strategies/{strategy_id}/entries",
    response_model=list[RegistryEntryView],
)
async def list_entries(strategy_id: str):
    """Enumerate all versions within a strategy family."""
    return get_registry_service().list_by_strategy(strategy_id)


@app.post(
    "/api/registry/entries/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_state(registry_id: str, body: AdvanceRequest):
    """
    Advance artifact_state through governed transition.

    Does NOT modify deployment_stage — that is owned by DeploymentPlan.
    """
    registry_service = get_registry_service()
    try:
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/registry/strategies/{strategy_id}/latest-approved",
    response_model=RegistryEntryView,
)
async def latest_approved(strategy_id: str):
    """Return the newest approved entry for a strategy family."""
    result = get_registry_service().resolve_latest_approved(strategy_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No approved entry for strategy: {strategy_id}")
    return result


@app.get(
    "/api/registry/strategies/{strategy_id}/deployment-view",
    response_model=DeploymentView,
)
async def deployment_view(strategy_id: str):
    """Return the derived deployment-stage view for a strategy."""
    return get_registry_service().resolve_deployment_view(strategy_id)


# -- Internal: deployment summary projection ------------------------------

@app.put(
    "/api/registry/entries/{registry_id}/deployment-summary",
    response_model=RegistryEntryView,
)
async def update_deployment_summary(registry_id: str, body: DeploymentSummaryUpdate):
    """
    Update the derived deployment_summary on a registry entry.

    Called by the deployment service when a stage transition occurs.
    The registry does NOT own deployment stage truth — this is a read-model projection.
    """
    registry_service = get_registry_service()
    try:
        return registry_service.update_deployment_summary(
            registry_id,
            current_stage=body.current_stage,
            deployment_plan_id=body.deployment_plan_id,
            runtime_binding_id=body.runtime_binding_id,
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- StrategySpec registry facade ----------------------------------------

@app.post("/api/registry/strategy-specs", response_model=RegistryEntryView)
async def register_strategy_spec(payload: StrategySpecRegisterRequest):
    """
    Register a StrategySpec artifact through a StrategySpec-specific facade.

    This keeps the registry lifecycle generic while making the research-plane
    StrategySpec path explicit: artifact_type is forced to strategy_spec, lineage
    is required, and inline StrategySpec payloads get deterministic checksums.
    """
    registry_id = (
        payload.registry_id
        or f"reg-strategy-spec-{payload.strategy_id}-{payload.version}-{uuid.uuid4().hex[:8]}"
    )
    registry_service = get_registry_service()
    try:
        create_payload = _strategy_spec_register_payload(payload)
        view, created = registry_service.register_if_absent(
            create_payload,
            registry_id,
        )
        if created:
            return _ensure_strategy_spec_view(view, registry_id)
        return _ensure_strategy_spec_registration_matches(
            view,
            create_payload,
            registry_id,
        )
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/registry/strategy-specs/{registry_id}", response_model=RegistryEntryView)
async def get_strategy_spec_entry(registry_id: str):
    """Read one StrategySpec registry entry."""
    registry_service = get_registry_service()
    try:
        return _ensure_strategy_spec_view(registry_service.get(registry_id), registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/api/registry/strategies/{strategy_id}/strategy-specs",
    response_model=list[RegistryEntryView],
)
async def list_strategy_spec_entries(
    strategy_id: str,
    artifact_state: Optional[ArtifactState] = None,
):
    """List StrategySpec artifact versions for one strategy family."""
    views = [
        view
        for view in get_registry_service().list_by_strategy(strategy_id)
        if view.entry.artifact_type == ArtifactType.STRATEGY_SPEC
    ]
    if artifact_state is not None:
        views = [view for view in views if view.entry.artifact_state == artifact_state]
    return views


@app.post(
    "/api/registry/strategy-specs/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_strategy_spec_state(registry_id: str, body: AdvanceRequest):
    """Advance only StrategySpec registry entries through the governed artifact lifecycle."""
    registry_service = get_registry_service()
    try:
        _ensure_strategy_spec_view(registry_service.get(registry_id), registry_id)
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- Evolvable StrategyArtifact registry facade (EVOLOOP-003) ------------

@app.post("/api/registry/strategy-artifacts", response_model=RegistryEntryView)
async def register_strategy_artifact(payload: StrategyArtifactRegisterRequest):
    """Register a schema-valid StrategyArtifact as an execution_bundle."""
    registry_service = get_registry_service()
    try:
        return _register_strategy_artifact(
            registry_service,
            _strategy_artifact_registration(payload),
        )
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/registry/strategy-artifacts/{registry_id}",
    response_model=RegistryEntryView,
)
async def get_strategy_artifact_entry(registry_id: str):
    """Read one execution_bundle carrying a StrategyArtifact overlay."""
    registry_service = get_registry_service()
    try:
        return _ensure_strategy_artifact_view(
            registry_service.get(registry_id), registry_id
        )
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/api/registry/strategies/{strategy_id}/strategy-artifacts",
    response_model=list[RegistryEntryView],
)
async def list_strategy_artifact_entries(
    strategy_id: str,
    artifact_state: Optional[ArtifactState] = None,
):
    """List StrategyArtifact revisions for a strategy family."""
    views = [
        view
        for view in get_registry_service().list_by_strategy(strategy_id)
        if _is_strategy_artifact_view(view)
    ]
    if artifact_state is not None:
        views = [view for view in views if view.entry.artifact_state == artifact_state]
    return views


@app.post(
    "/api/registry/strategy-artifacts/{registry_id}/mutate",
    response_model=RegistryEntryView,
)
async def mutate_strategy_artifact_entry(
    registry_id: str,
    body: StrategyArtifactMutationRequest,
):
    """Create a candidate child revision from declared mutable parameters."""
    registry_service = get_registry_service()
    try:
        parent_view = _ensure_strategy_artifact_view(
            registry_service.get(registry_id), registry_id
        )
        parent_artifact = (parent_view.entry.metadata or {})["strategy_artifact"]
        child_artifact = mutate_strategy_artifact(
            parent_artifact,
            new_artifact_id=body.new_artifact_id,
            new_version=body.new_version,
            parameter_updates=body.parameter_updates,
            source_run_ids=body.source_run_ids,
            parent_registry_id=registry_id,
        )
        return _register_strategy_artifact(
            registry_service,
            {
                "registry_id": child_artifact["artifact_id"],
                "artifact_state": ArtifactState.CANDIDATE.value,
                "strategy_artifact": child_artifact,
                "producer_run_id": body.source_run_ids[-1]
                if body.source_run_ids
                else None,
            },
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/api/registry/strategy-artifacts/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_strategy_artifact_state(registry_id: str, body: AdvanceRequest):
    """Advance a StrategyArtifact through the generic governed lifecycle."""
    registry_service = get_registry_service()
    try:
        _ensure_strategy_artifact_view(registry_service.get(registry_id), registry_id)
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- AllocationPolicyArtifact registry facade (MPOS-P1-ART-001) ----------
#
# Registers an AllocationPolicyArtifact produced by optimizer-svc into the
# registry governance lifecycle.  The registry does not import optimizer-svc;
# the caller embeds the artifact payload inline and we extract the lineage
# fields (provenance_refs → source_run_ids, conflict_resolution_log_id →
# source_strategy_spec_id) to satisfy the registry lineage requirement.
#
# Key field mapping
# -----------------
#   artifact_type          = allocation_policy
#   strategy_id            = capital_pool_id (pool-scoped strategy identity)
#   lineage.source_run_ids = provenance_refs (PersonaAllocationProposal ids)
#   lineage.source_strategy_spec_id = conflict_resolution_log_id
#   evaluation_summary     = synthesis evidence: method, sponsor, risk, scope
#   metadata               = full AllocationPolicyArtifact dict
#   checksum               = sha256 of the artifact JSON (caller-supplied or computed)
#   producer_run_id        = artifact_id from the optimizer run

_ALLOC_POLICY_ARTIFACT_REQUIRED = [
    "artifact_id",
    "capital_pool_id",
    "scope_ref",
    "sponsor_persona_id",
    "synthesis_method",
    "target_weights",
    "created_at",
    "provenance_refs",
    "conflict_resolution_log_id",
]

_SYNTHESIS_METHODS = {"weighted_fusion", "committee_override", "single_proposal"}


class AllocationPolicyArtifactRegisterRequest(BaseModel):
    """
    Request body for POST /api/registry/allocation-policy-artifacts.

    The caller embeds the full AllocationPolicyArtifact dict in
    ``allocation_policy_artifact``.  ``version`` must be semver (e.g. "1.0.0").
    ``registry_id`` is optional; one is generated if omitted.
    ``checksum`` is optional; computed from the artifact JSON if omitted.
    """
    version: str
    allocation_policy_artifact: dict[str, Any]
    registry_id: Optional[str] = None
    checksum: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


def _validate_alloc_policy_artifact(artifact: dict[str, Any]) -> None:
    missing = [k for k in _ALLOC_POLICY_ARTIFACT_REQUIRED if not artifact.get(k)]
    if missing:
        raise RegistryError(
            "AllocationPolicyArtifact is missing required fields: " + ", ".join(missing)
        )
    if artifact.get("synthesis_method") not in _SYNTHESIS_METHODS:
        raise RegistryError(
            f"allocation_policy_artifact.synthesis_method must be one of "
            f"{sorted(_SYNTHESIS_METHODS)}, got {artifact.get('synthesis_method')!r}"
        )
    if not isinstance(artifact.get("provenance_refs"), list) or not artifact["provenance_refs"]:
        raise RegistryError(
            "allocation_policy_artifact.provenance_refs must be a non-empty list of proposal ids"
        )
    if not isinstance(artifact.get("target_weights"), dict) or not artifact["target_weights"]:
        raise RegistryError(
            "allocation_policy_artifact.target_weights must be a non-empty symbol-to-weight mapping"
        )


def _alloc_policy_register_payload(
    body: AllocationPolicyArtifactRegisterRequest,
) -> RegistryEntryCreate:
    artifact = body.allocation_policy_artifact

    _validate_alloc_policy_artifact(artifact)

    capital_pool_id = str(artifact["capital_pool_id"]).strip()
    if not capital_pool_id:
        raise RegistryError("allocation_policy_artifact.capital_pool_id is required")

    provenance_refs = list(artifact["provenance_refs"])
    conflict_log_id = str(artifact["conflict_resolution_log_id"]).strip()

    lineage = Lineage(
        source_run_ids=provenance_refs,
        source_strategy_spec_id=conflict_log_id or None,
    )

    artifact_json_bytes = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    checksum = str(body.checksum or "").strip()
    if not checksum:
        import hashlib
        checksum = f"sha256:{hashlib.sha256(artifact_json_bytes).hexdigest()}"

    evaluation_summary: dict[str, Any] = {
        "conflict_resolution_log_id": conflict_log_id,
        "synthesis_method": artifact.get("synthesis_method"),
        "sponsor_persona_id": artifact.get("sponsor_persona_id"),
        "scope_ref": artifact.get("scope_ref"),
    }
    risk_budget = artifact.get("risk_budget")
    if risk_budget is not None:
        evaluation_summary["risk_budget"] = risk_budget

    extra_metadata = dict(body.metadata or {})
    extra_metadata["allocation_policy_artifact"] = artifact

    storage_ref = StorageRef(
        backend=StorageBackend.INLINE,
        path="$.entry.metadata.allocation_policy_artifact",
    )

    return RegistryEntryCreate(
        artifact_type=ArtifactType.ALLOCATION_POLICY,
        strategy_id=capital_pool_id,
        version=body.version,
        artifact_state=ArtifactState.CANDIDATE,
        lineage=lineage,
        storage_ref=storage_ref,
        checksum=checksum,
        producer_run_id=str(artifact.get("artifact_id") or "").strip() or None,
        evaluation_summary=evaluation_summary,
        metadata=extra_metadata,
    )


def _ensure_alloc_policy_view(
    view: RegistryEntryView, registry_id: str
) -> RegistryEntryView:
    if view.entry.artifact_type != ArtifactType.ALLOCATION_POLICY:
        raise RegistryNotFoundError(
            f"AllocationPolicyArtifact registry entry not found: {registry_id}"
        )
    return view


@app.post(
    "/api/registry/allocation-policy-artifacts",
    response_model=RegistryEntryView,
)
async def register_allocation_policy_artifact(
    payload: AllocationPolicyArtifactRegisterRequest,
):
    """
    Register an AllocationPolicyArtifact produced by optimizer-svc.

    The artifact enters the registry at ``candidate`` state because it already
    carries a ConflictResolutionLog and provenance lineage from optimizer-svc;
    it does not require a separate replication run.  Governance can then advance
    it to ``approved`` via the standard advance endpoint before deployment planning.
    """
    artifact = payload.allocation_policy_artifact
    capital_pool_id = str(artifact.get("capital_pool_id") or "").strip()
    registry_id = (
        payload.registry_id
        or f"reg-alloc-policy-{capital_pool_id}-{payload.version}-{uuid.uuid4().hex[:8]}"
    )
    registry_service = get_registry_service()
    try:
        create_payload = _alloc_policy_register_payload(payload)
        return registry_service.register(create_payload, registry_id)
    except (RegistryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/registry/allocation-policy-artifacts/{registry_id}",
    response_model=RegistryEntryView,
)
async def get_allocation_policy_artifact_entry(registry_id: str):
    """Read one AllocationPolicyArtifact registry entry."""
    registry_service = get_registry_service()
    try:
        return _ensure_alloc_policy_view(
            registry_service.get(registry_id), registry_id
        )
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/api/registry/pools/{capital_pool_id}/allocation-policy-artifacts",
    response_model=list[RegistryEntryView],
)
async def list_allocation_policy_artifacts(
    capital_pool_id: str,
    artifact_state: Optional[ArtifactState] = None,
):
    """
    List AllocationPolicyArtifact registry entries for one capital pool.

    strategy_id == capital_pool_id for allocation-policy artifacts.
    """
    views = [
        view
        for view in get_registry_service().list_by_strategy(capital_pool_id)
        if view.entry.artifact_type == ArtifactType.ALLOCATION_POLICY
    ]
    if artifact_state is not None:
        views = [view for view in views if view.entry.artifact_state == artifact_state]
    return views


@app.post(
    "/api/registry/allocation-policy-artifacts/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_allocation_policy_artifact_state(
    registry_id: str, body: AdvanceRequest
):
    """
    Advance an AllocationPolicyArtifact entry through the governed artifact lifecycle.

    candidate -> approved makes the artifact eligible for DeploymentPlan creation.
    """
    registry_service = get_registry_service()
    try:
        _ensure_alloc_policy_view(registry_service.get(registry_id), registry_id)
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- Health ---------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-registry"}
