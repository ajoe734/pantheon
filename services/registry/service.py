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

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Pantheon Registry Service",
    description="Artifact-state and deployment-stage split API per BP5-SVC-002",
    version="0.1.0",
)


def get_registry_service() -> RegistryService:
    """Build the service against the current store singleton at request time."""
    return RegistryService(get_store())


# -- Request/Response wrappers --------------------------------------------

class AdvanceRequest(BaseModel):
    target_state: ArtifactState
    approver: Optional[str] = None


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
            registry_id, body.target_state, approver=body.approver
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
        return registry_service.register(create_payload, registry_id)
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
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- Health ---------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-registry"}
