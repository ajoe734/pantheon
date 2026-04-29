"""
services/incidents — Incident Evidence Service (BP5-SVC-011)

Deployable FastAPI service that exposes the canonical IncidentCase CRUD and
evidence-linkage read path.  Uses the INC-001 backbone (services/incident/)
as its domain layer.

This service is the authoritative HTTP surface for incident records.  All
runtime, governance, and evolution flows that need to create or query incidents
must go through these endpoints instead of direct in-process store access.

Route summary
-------------
POST  /api/incidents
    Create a new IncidentCase.
    Body: CreateIncidentRequest.
    Returns 201 on success, 422 on validation failure.

GET   /api/incidents
    List incidents.  Optional query params:
      binding_id      — filter by RuntimeBinding
      capital_pool_id — filter by CapitalPool
      status          — filter by status (open | investigating | resolved | closed)
      severity        — filter by severity (critical | high | medium | low)
      open_only       — true → only open+investigating

GET   /api/incidents/{incident_id}
    Get a single IncidentCase.

POST  /api/incidents/{incident_id}/status
    Transition incident status.
    Body: UpdateIncidentStatusRequest.
    Returns 200 on success, 400 on invalid transition, 404 if not found.

GET   /api/incidents/{incident_id}/operator-payload
    Return the enriched OperatorIncidentPayload for this incident.  Includes
    postmortem linkage and evolution decision reference when present.

GET   /__health__
    Liveness probe.

Environment variables
---------------------
INCIDENTS_DATA_DIR
    Directory for the on-disk incident store.
    Defaults to /tmp/pantheon/incidents.

PORT
    HTTP listen port (default 8090).

Write authority (INC-001)
------------------------
Only this service (Incident domain) writes IncidentCase records.
Postmortem linkage (link_evolution_decision) is invoked by the
postmortem service via the dedicated route.
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
# Bootstrap domain layer from sibling services/incident/ directory
# ---------------------------------------------------------------------------
_INC_DIR = Path(__file__).resolve().parent.parent / "incident"
if str(_INC_DIR) not in sys.path:
    sys.path.insert(0, str(_INC_DIR.parent))

try:
    from services.incident.incident import (  # type: ignore
        IncidentCase,
        IncidentError,
        IncidentStatus,
        IncidentStore,
        validate_incident_case,
    )
except ImportError:
    from incident.incident import (  # type: ignore
        IncidentCase,
        IncidentError,
        IncidentStatus,
        IncidentStore,
        validate_incident_case,
    )

try:
    from .models import (
        CreateIncidentRequest,
        IncidentResponse,
        OperatorIncidentPayload,
        UpdateIncidentStatusRequest,
    )
except ImportError:
    from models import (  # type: ignore
        CreateIncidentRequest,
        IncidentResponse,
        OperatorIncidentPayload,
        UpdateIncidentStatusRequest,
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

DATA_DIR = os.getenv("INCIDENTS_DATA_DIR", "/tmp/pantheon/incidents")
os.makedirs(DATA_DIR, exist_ok=True)
STORE_PATH = Path(DATA_DIR) / "incidents.json"

store: IncidentStore = IncidentStore(path=STORE_PATH)
reference_validator = CanonicalReferenceValidator()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pantheon Incident Evidence Service",
    version="0.1.0",
    description=(
        "Canonical IncidentCase evidence service (BP5-SVC-011).  "
        "All runtime, governance, and evolution flows cite this service "
        "as the single incident evidence surface."
    ),
)
register_fastapi_health_routes(
    app,
    "incidents",
    metrics=lambda: {"incident_count": len(store.list_incidents())},
    details=lambda: {"data_dir": DATA_DIR, "store_path": str(STORE_PATH)},
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_response(inc: IncidentCase) -> IncidentResponse:
    d = inc.to_dict()
    d.setdefault("telemetry_event_ids", [])
    return IncidentResponse(**d)


def _get_or_404(incident_id: str) -> IncidentCase:
    inc = store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail=f"IncidentCase '{incident_id}' not found")
    return inc


# ---------------------------------------------------------------------------
# Routes — create
# ---------------------------------------------------------------------------

@app.post(
    "/api/incidents",
    response_model=IncidentResponse,
    status_code=201,
    summary="Create a new IncidentCase",
)
def create_incident(body: CreateIncidentRequest) -> IncidentResponse:
    """Create a new IncidentCase.

    Enforces:
    - Required evidence fields (binding_id, deployment_stage, deployment_plan_id, …)
    - Enum validation (severity, status, deployment_stage)
    - Uniqueness (duplicate incident_id rejected with 409)

    Write authority: Incident domain only (INC-001).
    """
    incident_id = body.incident_id or f"inc-{uuid.uuid4().hex[:12]}"
    if store.get_incident(incident_id) is not None:
        raise HTTPException(status_code=409, detail=f"IncidentCase '{incident_id}' already exists")

    try:
        inc = IncidentCase(
            incident_id=incident_id,
            title=body.title,
            status=body.status,
            severity=body.severity,
            created_at=_utc_now(),
            binding_id=body.binding_id,
            deployment_stage=body.deployment_stage,
            deployment_plan_id=body.deployment_plan_id,
            capital_pool_id=body.capital_pool_id,
            persona_capital_binding_id=body.persona_capital_binding_id,
            artifact_id=body.artifact_id,
            artifact_version=body.artifact_version,
            runtime_id=body.runtime_id,
            trace_id=body.trace_id,
            telemetry_event_ids=body.telemetry_event_ids,
            evidence_summary=body.evidence_summary,
            lineage_ref=body.lineage_ref,
        )
    except (IncidentError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    errors = validate_incident_case(inc)
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})
    try:
        reference_validator.validate_incident(inc)
    except CanonicalReferenceError as exc:
        raise HTTPException(status_code=422, detail={"reference_errors": exc.errors})

    try:
        store.create_incident(inc)
    except IncidentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    log.info("Created IncidentCase %s severity=%s binding=%s", incident_id, body.severity, body.binding_id)
    return _to_response(inc)


# ---------------------------------------------------------------------------
# Routes — list / lookup
# ---------------------------------------------------------------------------

@app.get(
    "/api/incidents",
    response_model=List[IncidentResponse],
    summary="List incidents",
)
def list_incidents(
    binding_id: Optional[str] = Query(None, description="Filter by RuntimeBinding ID"),
    capital_pool_id: Optional[str] = Query(None, description="Filter by CapitalPool ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    open_only: bool = Query(False, description="Return only open+investigating incidents"),
) -> List[IncidentResponse]:
    """List IncidentCases with optional filters.  Most-recent first."""
    incidents = store.list_incidents()

    if binding_id:
        incidents = [i for i in incidents if i.binding_id == binding_id]
    if capital_pool_id:
        incidents = [i for i in incidents if i.capital_pool_id == capital_pool_id]
    if status:
        incidents = [i for i in incidents if i.status == status]
    if severity:
        incidents = [i for i in incidents if i.severity == severity]
    if open_only:
        incidents = [i for i in incidents if i.is_open()]

    incidents.sort(key=lambda i: i.created_at, reverse=True)
    return [_to_response(i) for i in incidents]


@app.get(
    "/api/incidents/{incident_id}",
    response_model=IncidentResponse,
    summary="Get a single IncidentCase",
)
def get_incident(incident_id: str) -> IncidentResponse:
    return _to_response(_get_or_404(incident_id))


# ---------------------------------------------------------------------------
# Routes — status transitions
# ---------------------------------------------------------------------------

@app.post(
    "/api/incidents/{incident_id}/status",
    response_model=IncidentResponse,
    summary="Transition incident status",
)
def update_status(
    incident_id: str,
    body: UpdateIncidentStatusRequest,
) -> IncidentResponse:
    """Transition an IncidentCase to a new status.

    Lifecycle: open → investigating → resolved → closed

    resolved_at is auto-set when transitioning to resolved or closed.
    """
    _get_or_404(incident_id)
    try:
        updated = store.update_incident_status(
            incident_id,
            body.status,
            resolved_at=body.resolved_at,
        )
    except IncidentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log.info("IncidentCase %s → status=%s", incident_id, body.status)
    return _to_response(updated)


# ---------------------------------------------------------------------------
# Routes — operator payload
# ---------------------------------------------------------------------------

@app.get(
    "/api/incidents/{incident_id}/operator-payload",
    response_model=OperatorIncidentPayload,
    summary="Operator payload: enriched incident view with evidence and linkage",
)
def get_operator_payload(incident_id: str) -> OperatorIncidentPayload:
    """Return the enriched OperatorIncidentPayload for an incident.

    Includes all canonical evidence fields and, when available, postmortem
    linkage and evolution-decision reference so the operator console can render
    the full incident chain without cross-service joins.
    """
    inc = _get_or_404(incident_id)

    # Look up postmortem linkage in the same store
    pm = store.find_postmortem_for_incident(incident_id)
    postmortem_id: Optional[str] = pm.postmortem_id if pm else None
    linked_evolution_decision_id: Optional[str] = (
        pm.linked_evolution_decision_id if pm else None
    )

    inc_dict = inc.to_dict()
    inc_dict.setdefault("telemetry_event_ids", [])

    return OperatorIncidentPayload(
        **inc_dict,
        is_open=inc.is_open(),
        postmortem_id=postmortem_id,
        linked_evolution_decision_id=linked_evolution_decision_id,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/__health__", summary="Liveness probe")
def health():
    return {"status": "ok", "service": "incidents"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8090"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
