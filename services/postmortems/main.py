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

POST  /api/postmortems/consume-resolved-incident
    Consume a resolved IncidentCase event and create or refresh an idempotent
    Postmortem draft. Returns 201 on first draft, 200 on duplicate/update.

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
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Response
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
import asyncio
import json
from services.foundation import (
    EventEnvelope,
    OutboxRecord,
    TraceContext,
    EnvironmentScope,
    EnvironmentName,
)
from services.foundation.postgres_json_store import PostgresJsonOwnerStore
from services.foundation.outbox import OutboxRecordStatus

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
    from .consumer import (
        PostmortemDraftConsumerError,
        ResolvedIncidentPostmortemDraftConsumer,
    )
except ImportError:
    from models import (  # type: ignore
        CreatePostmortemRequest,
        LinkEvolutionDecisionRequest,
        OperatorPostmortemPayload,
        PostmortemResponse,
        UpdatePostmortemStatusRequest,
    )
    from consumer import (  # type: ignore
        PostmortemDraftConsumerError,
        ResolvedIncidentPostmortemDraftConsumer,
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
PERSISTENCE_POSTURE = require_persistence_posture("postmortems")

# Shared IncidentStore — postmortem service uses the same backing store so that
# referential integrity (postmortem references incident) is enforced in-process.
# In production, both services connect to the shared Pantheon incidents DB schema.
store: IncidentStore = build_incident_store(STORE_PATH)

# ---------------------------------------------------------------------------
# Outbox Primitives & Store
# ---------------------------------------------------------------------------

class JsonOutboxStoreHelper:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write(self, data: dict):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def put(self, record_id: str, payload: dict) -> None:
        data = self._read()
        data[record_id] = payload
        self._write(data)

    def list_all(self) -> list[dict]:
        data = self._read()
        return list(data.values())

class UnifiedOutboxStore:
    def __init__(self, backend: str, dsn: str | None, table_name: str | None, json_path: Path, owner_service: str):
        self.backend = backend.strip().lower()
        if self.backend == "postgres":
            if not dsn:
                raise ValueError("DSN required for postgres outbox")
            self.impl = PostgresJsonOwnerStore(
                dsn=dsn,
                table=table_name,
                owner_service=owner_service,
                bootstrap=True
            )
        else:
            self.impl = JsonOutboxStoreHelper(json_path)

    def put(self, record: OutboxRecord) -> None:
        self.impl.put(record.outbox_id, record.to_dict())

    def list_pending_and_failed(self) -> list[OutboxRecord]:
        records = []
        for payload in self.impl.list_all():
            try:
                rec = OutboxRecord.from_dict(payload)
                if rec.status in {OutboxRecordStatus.PENDING, OutboxRecordStatus.FAILED}:
                    records.append(rec)
            except Exception as exc:
                log.warning("Failed to parse outbox record: %s", exc)
        return records

OUTBOX_BACKEND = (os.getenv("POSTMORTEM_STORE_BACKEND") or os.getenv("INCIDENT_STORE_BACKEND", "json")).strip().lower()
OUTBOX_DSN = os.getenv("POSTMORTEM_STORE_DSN") or os.getenv("INCIDENT_STORE_DSN") or os.getenv("DATABASE_URL")
outbox_store = UnifiedOutboxStore(
    backend=OUTBOX_BACKEND,
    dsn=OUTBOX_DSN,
    table_name="incident.postmortems_outbox",
    json_path=Path(DATA_DIR) / "postmortems_outbox.json",
    owner_service="postmortem-svc",
)
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
    dependencies=lambda: {
        "persistence": PERSISTENCE_POSTURE.to_dict(),
        "incidents": {"status": "ok", "store_path": str(STORE_PATH)},
    },
    metrics=lambda: {"postmortem_count": len(store.list_postmortems())},
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_path": str(STORE_PATH),
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_response(pm: Postmortem) -> PostmortemResponse:
    d = pm.to_dict()
    for list_field in (
        "contributing_factors",
        "timeline",
        "action_items",
        "author_ids",
        "telemetry_event_ids",
        "reconciliation_ids",
    ):
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
            telemetry_event_ids=body.telemetry_event_ids,
            reconciliation_ids=body.reconciliation_ids,
            incident_cluster_id=body.incident_cluster_id,
            incident_evidence_summary=body.incident_evidence_summary,
            lineage_ref=body.lineage_ref,
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


@app.post(
    "/api/postmortems/consume-resolved-incident",
    response_model=PostmortemResponse,
    status_code=201,
    summary="Consume a resolved IncidentCase into an idempotent Postmortem draft",
)
def consume_resolved_incident(
    response: Response,
    body: Dict[str, Any] = Body(...),
) -> PostmortemResponse:
    """Create or refresh a draft Postmortem from a resolved IncidentCase."""
    consumer = ResolvedIncidentPostmortemDraftConsumer(incident_store=store)
    try:
        result = consumer.consume(body)
    except PostmortemDraftConsumerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not result.created:
        response.status_code = 200

    log.info(
        "Consumed resolved incident into Postmortem %s created=%s updated=%s",
        result.postmortem.postmortem_id,
        result.created,
        result.updated,
    )
    return _to_response(result.postmortem)


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
    for list_field in (
        "contributing_factors",
        "timeline",
        "action_items",
        "author_ids",
        "telemetry_event_ids",
        "reconciliation_ids",
    ):
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

def _publish_postmortem_to_evolution_if_needed(postmortem_id: str) -> None:
    from datetime import datetime, timezone
    from services.evolution.postmortem_bridge import decision_id_for_published_postmortem

    pm = store.get_postmortem(postmortem_id)
    if pm is None:
        log.error("AUDIT: Failed to publish postmortem %s: postmortem not found in store", postmortem_id)
        return

    incident = store.get_incident(pm.incident_id)
    if incident is None:
        log.error("AUDIT: Failed to publish postmortem %s: parent incident %s not found in store", postmortem_id, pm.incident_id)
        return

    try:
        det_decision_id = decision_id_for_published_postmortem(pm, incident)
    except Exception as exc:
        log.error("AUDIT: Failed to compute decision_id for postmortem %s: %s", postmortem_id, exc)
        return

    trace = TraceContext.new(
        environment=EnvironmentScope(name=EnvironmentName.SANDBOX),
        source_system="postmortem-svc",
    )
    event = EventEnvelope.new(
        event_type="postmortem.published",
        aggregate_type="postmortem",
        aggregate_id=postmortem_id,
        sequence_no=1,
        trace=trace,
        payload={
            "postmortem_id": postmortem_id,
            "decision_id": det_decision_id,
        },
        producer_service="postmortem-svc",
    )
    record = OutboxRecord.new(owner_service="postmortem-svc", event=event)
    outbox_store.put(record)
    log.info("AUDIT: Enqueued postmortem %s proposal to outbox (decision_id: %s)", postmortem_id, det_decision_id)


# background delivery loop for postmortems
async def process_postmortems_outbox():
    records = outbox_store.list_pending_and_failed()
    if not records:
        return

    import httpx
    from datetime import datetime, timezone
    evolution_url = os.getenv("EVOLUTION_URL", "http://localhost:8093").strip()
    url = f"{evolution_url}/api/evolution/proposals/from-postmortem-published"

    async with httpx.AsyncClient(timeout=5.0) as client:
        for record in records:
            postmortem_id = record.event.payload.get("postmortem_id")
            det_decision_id = record.event.payload.get("decision_id")
            log.info("AUDIT: Outbox worker attempting delivery of postmortem %s proposal to %s", postmortem_id, url)

            proposal_payload = {
                "postmortem_id": postmortem_id,
                "decision_id": det_decision_id,
            }

            try:
                resp = await client.post(url, json=proposal_payload)
                if resp.status_code in {200, 201}:
                    log.info("AUDIT: Successfully delivered proposal for postmortem %s to evolution. Status: %d", postmortem_id, resp.status_code)
                    outbox_store.put(record.mark_published())
                elif resp.status_code == 409:
                    err_msg = f"status_code=409 conflict: {resp.text}"
                    log.error("AUDIT: Final failure for postmortem %s: %s", postmortem_id, err_msg)
                    outbox_store.put(record.mark_failed(err_msg, dead_lettered=True))
                else:
                    err_msg = f"status_code={resp.status_code} body={resp.text}"
                    dead_letter = record.delivery_attempts + 1 >= 3 or resp.status_code == 422
                    log.warning("AUDIT: Outbox delivery attempt %d for postmortem %s returned error: %s", record.delivery_attempts + 1, postmortem_id, err_msg)
                    outbox_store.put(record.mark_failed(err_msg, dead_lettered=dead_letter))
            except Exception as exc:
                err_msg = str(exc)
                dead_letter = record.delivery_attempts + 1 >= 3
                log.warning("AUDIT: Outbox delivery attempt %d for postmortem %s failed with exception: %s", record.delivery_attempts + 1, postmortem_id, err_msg)
                outbox_store.put(record.mark_failed(err_msg, dead_lettered=dead_letter))

async def postmortems_outbox_loop():
    while True:
        try:
            await process_postmortems_outbox()
        except Exception as exc:
            log.exception("Error in postmortems outbox delivery loop: %s", exc)
        await asyncio.sleep(2.0)

@app.on_event("startup")
def start_postmortems_outbox_worker():
    import sys
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return
    asyncio.create_task(postmortems_outbox_loop())


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
    pm = _get_or_404(postmortem_id)
    incident = store.get_incident(pm.incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404,
            detail=f"Parent IncidentCase '{pm.incident_id}' not found for postmortem '{postmortem_id}'"
        )

    # 進行 bridge validation (surface bridge precondition failures instead of returning 200)
    if body.status == "published":
        from services.evolution.postmortem_bridge import _validate_postmortem_incident_pair, PostmortemBridgeError
        try:
            test_pm_dict = pm.to_dict()
            test_pm_dict["status"] = "published"
            if not test_pm_dict.get("published_at"):
                test_pm_dict["published_at"] = _utc_now()
            _validate_postmortem_incident_pair(test_pm_dict, incident.to_dict(), require_published=True)
        except PostmortemBridgeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Bridge precondition validation failed: {exc}"
            )

    try:
        updated = store.update_postmortem_status(
            postmortem_id,
            body.status,
            published_at=body.published_at,
        )
    except IncidentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log.info("Postmortem %s → status=%s", postmortem_id, body.status)

    if body.status == "published":
        _publish_postmortem_to_evolution_if_needed(postmortem_id)

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
