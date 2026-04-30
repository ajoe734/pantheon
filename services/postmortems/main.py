"""
services/postmortems — Postmortem Evidence Service (BP5-SVC-011)

Deployable FastAPI service that exposes the canonical Postmortem CRUD and
evidence-linkage read path.  Uses the INC-001 backbone (services/incident/)
as its domain layer.

This service is the authoritative HTTP surface for postmortem records.  All
governance and evolution flows that need to create or query postmortems must
go through these endpoints.

Route summary
-------------
POST  /api/postmortems
    Create a new Postmortem for an IncidentCase.
    Body: CreatePostmortemRequest.
    Enforces referential integrity: the referenced incident_id must exist.
    Returns 201 on success, 422 on validation failure, 409 if already exists.

GET   /api/postmortems
    List postmortems.  Optional query params:
      incident_id  — filter by IncidentCase
      binding_id   — filter by RuntimeBinding
      status       — filter by status (draft | review | approved | published)

GET   /api/postmortems/{postmortem_id}
    Get a single Postmortem.

POST  /api/postmortems/{postmortem_id}/status
    Transition postmortem status.
    Body: UpdatePostmortemStatusRequest.
    published_at is auto-set when transitioning to published.

POST  /api/postmortems/{postmortem_id}/link-evolution-decision
    Set linked_evolution_decision_id (called by evolution controller, EVO-003).
    Body: LinkEvolutionDecisionRequest.

GET   /api/incidents/{incident_id}/postmortem
    Convenience read: find the postmortem for an incident (at most one).

GET   /__health__
    Liveness probe.

Environment variables
---------------------
POSTMORTEMS_DATA_DIR
    Directory for the shared on-disk incident+postmortem store.
    Defaults to /tmp/pantheon/incidents (shared with incident service in
    single-process mode; in production each service has its own Postgres schema).

PORT
    HTTP listen port (default 8091).

Referential integrity
---------------------
create_postmortem() requires the referenced incident_id to exist in the store.
All propagated evidence fields (binding_id, deployment_stage, …) must exactly
match the referenced IncidentCase.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from services.foundation.health import register_fastapi_health_routes

# ---------------------------------------------------------------------------
# Bootstrap domain layer
# ---------------------------------------------------------------------------
_INC_DIR = Path(__file__).resolve().parent.parent / "incident"
if str(_INC_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_INC_DIR.parent))

try:
    from services.incident.incident import (  # type: ignore
        IncidentCase,
        IncidentError,
        IncidentStore,
        Postmortem,
        PostmortemStatus,
        validate_postmortem,
    )
    from services.incident.pg_store import build_incident_store  # type: ignore
except ImportError:
    from incident.incident import (  # type: ignore
        IncidentCase,
        IncidentError,
        IncidentStore,
        Postmortem,
        PostmortemStatus,
        validate_postmortem,
    )
    from incident.pg_store import build_incident_store  # type: ignore

try:
    from .models import (
        CreatePostmortemRequest,
        LinkEvolutionDecisionRequest,
        OperatorPostmortemPayload,
        PostmortemResponse,
        UpdatePostmortemStatusRequest,
    )
except ImportError:
    from models import (  # type: ignore
        CreatePostmortemRequest,
        LinkEvolutionDecisionRequest,
        OperatorPostmortemPayload,
        PostmortemResponse,
        UpdatePostmortemStatusRequest,
    )

try:
    from services.incident.reference_validation import (  # type: ignore
        CanonicalReferenceError,
        CanonicalReferenceValidator,
    )
except ImportError:
    from incident.reference_validation import (  # type: ignore
        CanonicalReferenceError,
        CanonicalReferenceValidator,
    )

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration & store bootstrap
# ---------------------------------------------------------------------------

DATA_DIR = os.getenv("POSTMORTEMS_DATA_DIR", os.getenv("INCIDENTS_DATA_DIR", "/tmp/pantheon/incidents"))
os.makedirs(DATA_DIR, exist_ok=True)
STORE_PATH = Path(DATA_DIR) / "incidents.json"
STORE_BACKEND = (os.getenv("POSTMORTEM_STORE_BACKEND") or os.getenv("INCIDENT_STORE_BACKEND", "json")).strip().lower() or "json"

# Shared IncidentStore — postmortem service uses the same backing store so that
# referential integrity (postmortem references incident) is enforced in-process.
# In production, both services connect to the shared Pantheon incidents DB schema.
store: IncidentStore = build_incident_store(STORE_PATH)
reference_validator = CanonicalReferenceValidator()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pantheon Postmortem Evidence Service",
    version="0.1.0",
    description=(
        "Canonical Postmortem evidence service (BP5-SVC-011).  "
        "All governance and evolution flows cite this service as the single "
        "postmortem evidence surface.  Referential integrity against "
        "IncidentCase is enforced at write time."
    ),
)
register_fastapi_health_routes(
    app,
    "postmortems",
    dependencies=lambda: {"incidents": {"status": "ok", "store_path": str(STORE_PATH)}},
    metrics=lambda: {"postmortem_count": len(store.list_postmortems())},
    details=lambda: {"data_dir": DATA_DIR, "store_path": str(STORE_PATH), "store_backend": STORE_BACKEND},
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_response(pm: Postmortem) -> PostmortemResponse:
    d = pm.to_dict()
    for list_field in ("contributing_factors", "timeline", "action_items", "author_ids"):
        d.setdefault(list_field, [])
    return PostmortemResponse(**d)


def _get_or_404(postmortem_id: str) -> Postmortem:
    pm = store.get_postmortem(postmortem_id)
    if pm is None:
        raise HTTPException(status_code=404, detail=f"Postmortem '{postmortem_id}' not found")
    return pm


def _get_incident_for_postmortem_or_404(postmortem_id: str) -> IncidentCase:
    pm = _get_or_404(postmortem_id)
    incident = store.get_incident(pm.incident_id)
    if incident is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Postmortem '{postmortem_id}' points to IncidentCase "
                f"{pm.incident_id!r}, but the incident record is unavailable"
            ),
        )
    return incident


# ---------------------------------------------------------------------------
# Routes — create
# ---------------------------------------------------------------------------

@app.post(
    "/api/postmortems",
    response_model=PostmortemResponse,
    status_code=201,
    summary="Create a new Postmortem",
)
def create_postmortem(body: CreatePostmortemRequest) -> PostmortemResponse:
    """Create a new Postmortem for an IncidentCase.

    Referential integrity rules (INC-001):
    - incident_id must reference an existing IncidentCase in the store.
    - All propagated evidence fields (binding_id, deployment_stage, …) must
      exactly match the referenced IncidentCase.  This prevents forensically
      inconsistent postmortems.

    Write authority: Incident domain only.
    """
    postmortem_id = body.postmortem_id or f"pm-{uuid.uuid4().hex[:12]}"
    if store.get_postmortem(postmortem_id) is not None:
        raise HTTPException(status_code=409, detail=f"Postmortem '{postmortem_id}' already exists")

    try:
        pm = Postmortem(
            postmortem_id=postmortem_id,
            title=body.title,
            status=body.status,
            created_at=_utc_now(),
            incident_id=body.incident_id,
            binding_id=body.binding_id,
            deployment_stage=body.deployment_stage,
            deployment_plan_id=body.deployment_plan_id,
            capital_pool_id=body.capital_pool_id,
            persona_capital_binding_id=body.persona_capital_binding_id,
            artifact_id=body.artifact_id,
            artifact_version=body.artifact_version,
            runtime_id=body.runtime_id,
            trace_id=body.trace_id,
            root_cause=body.root_cause,
            contributing_factors=body.contributing_factors,
            timeline=body.timeline,
            action_items=body.action_items,
            author_ids=body.author_ids,
        )
    except (IncidentError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    errors = validate_postmortem(pm)
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})
    parent_incident = store.get_incident(body.incident_id)
    if parent_incident is not None:
        try:
            reference_validator.validate_incident(parent_incident)
        except CanonicalReferenceError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "reference_errors": exc.errors,
                    "message": (
                        f"Referenced IncidentCase {body.incident_id!r} failed canonical "
                        "reference validation"
                    ),
                },
            )

    try:
        store.create_postmortem(pm)
    except IncidentError as exc:
        # Covers: orphaned incident_id, evidence mismatch, duplicate
        raise HTTPException(status_code=422, detail=str(exc))

    log.info(
        "Created Postmortem %s for incident=%s binding=%s",
        postmortem_id, body.incident_id, body.binding_id,
    )
    return _to_response(pm)


# ---------------------------------------------------------------------------
# Routes — list / lookup
# ---------------------------------------------------------------------------

@app.get(
    "/api/postmortems",
    response_model=List[PostmortemResponse],
    summary="List postmortems",
)
def list_postmortems(
    incident_id: Optional[str] = Query(None, description="Filter by IncidentCase ID"),
    binding_id: Optional[str] = Query(None, description="Filter by RuntimeBinding ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
) -> List[PostmortemResponse]:
    """List Postmortems with optional filters.  Most-recent first."""
    postmortems = store.list_postmortems()

    if incident_id:
        postmortems = [p for p in postmortems if p.incident_id == incident_id]
    if binding_id:
        postmortems = [p for p in postmortems if p.binding_id == binding_id]
    if status:
        postmortems = [p for p in postmortems if p.status == status]

    postmortems.sort(key=lambda p: p.created_at, reverse=True)
    return [_to_response(p) for p in postmortems]


@app.get(
    "/api/postmortems/{postmortem_id}",
    response_model=PostmortemResponse,
    summary="Get a single Postmortem",
)
def get_postmortem(postmortem_id: str) -> PostmortemResponse:
    return _to_response(_get_or_404(postmortem_id))


@app.get(
    "/api/postmortems/{postmortem_id}/operator-payload",
    response_model=OperatorPostmortemPayload,
    summary="Operator payload: enriched postmortem view with incident context",
)
def get_operator_payload(postmortem_id: str) -> OperatorPostmortemPayload:
    """Return a postmortem payload shaped for operator post-incident review."""
    pm = _get_or_404(postmortem_id)
    incident = _get_incident_for_postmortem_or_404(postmortem_id)

    pm_dict = pm.to_dict()
    for list_field in ("contributing_factors", "timeline", "action_items", "author_ids"):
        pm_dict.setdefault(list_field, [])

    return OperatorPostmortemPayload(
        **pm_dict,
        incident_status=incident.status,
        incident_severity=incident.severity,
        incident_created_at=incident.created_at,
        incident_resolved_at=incident.resolved_at,
    )


# ---------------------------------------------------------------------------
# Routes — status transitions
# ---------------------------------------------------------------------------

@app.post(
    "/api/postmortems/{postmortem_id}/status",
    response_model=PostmortemResponse,
    summary="Transition postmortem status",
)
def update_status(
    postmortem_id: str,
    body: UpdatePostmortemStatusRequest,
) -> PostmortemResponse:
    """Transition a Postmortem to a new status.

    Lifecycle: draft → review → approved → published

    published_at is auto-set when transitioning to published.
    """
    _get_or_404(postmortem_id)
    try:
        updated = store.update_postmortem_status(
            postmortem_id,
            body.status,
            published_at=body.published_at,
        )
    except IncidentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log.info("Postmortem %s → status=%s", postmortem_id, body.status)
    return _to_response(updated)


# ---------------------------------------------------------------------------
# Routes — evolution linkage
# ---------------------------------------------------------------------------

@app.post(
    "/api/postmortems/{postmortem_id}/link-evolution-decision",
    response_model=PostmortemResponse,
    summary="Link an EvolutionDecision to a Postmortem (called by EVO-003)",
)
def link_evolution_decision(
    postmortem_id: str,
    body: LinkEvolutionDecisionRequest,
) -> PostmortemResponse:
    """Set the linked_evolution_decision_id on a Postmortem.

    Called by the evolution controller (EVO-003) after an EvolutionDecision
    is created that references this postmortem.  Establishes the reverse edge
    for the evolution_decision.postmortem lineage link.
    """
    _get_or_404(postmortem_id)
    try:
        updated = store.link_evolution_decision(postmortem_id, body.evolution_decision_id)
    except IncidentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log.info(
        "Postmortem %s linked to evolution decision %s",
        postmortem_id, body.evolution_decision_id,
    )
    return _to_response(updated)


# ---------------------------------------------------------------------------
# Routes — incident-scoped convenience endpoint
# ---------------------------------------------------------------------------

@app.get(
    "/api/incidents/{incident_id}/postmortem",
    response_model=Optional[PostmortemResponse],
    summary="Find the postmortem for an incident (at most one)",
)
def get_postmortem_for_incident(incident_id: str) -> Optional[PostmortemResponse]:
    """Return the Postmortem associated with an IncidentCase, or null.

    At most one Postmortem may exist per incident.  Returns HTTP 200 with
    null body when no postmortem exists yet.
    """
    pm = store.find_postmortem_for_incident(incident_id)
    if pm is None:
        return None
    return _to_response(pm)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/__health__", summary="Liveness probe")
def health():
    return {"status": "ok", "service": "postmortems"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8091"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
