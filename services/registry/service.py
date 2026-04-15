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

import logging
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .models import (
    ArtifactState,
    DeploymentStage,
    DeploymentView,
    RegistryEntryCreate,
    RegistryEntryView,
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


# -- Error handling -------------------------------------------------------

@app.exception_handler(RegistryNotFoundError)
async def registry_not_found_handler(request: Request, exc: RegistryNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RegistryError)
async def registry_error_handler(request: Request, exc: RegistryError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


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


# -- Health ---------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-registry"}
