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
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query, Response
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
import asyncio
from services.foundation import (
    EventEnvelope,
    OutboxRecord,
    TraceContext,
    EnvironmentScope,
    EnvironmentName,
)
from services.foundation.reliable_delivery import (
    ReliableInboxStore,
    ReliableOutboxRecord,
    ReliableOutboxStore,
    reconcile_prepared,
)
from services.foundation.serialization import sha256_checksum

# ---------------------------------------------------------------------------
# Bootstrap domain layer
# ---------------------------------------------------------------------------
_INC_DIR = Path(__file__).resolve().parent.parent / "incident"
if str(_INC_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_INC_DIR.parent))

try:
    from services.incident.incident import (  # type: ignore
        IncidentCase,
        IncidentConcurrencyError,
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
        IncidentConcurrencyError,
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
        PostmortemDraftConsumerRetryableError,
        ResolvedIncidentPostmortemDraftConsumer,
        postmortem_id_for_incident,
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
        PostmortemDraftConsumerRetryableError,
        ResolvedIncidentPostmortemDraftConsumer,
        postmortem_id_for_incident,
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

OUTBOX_BACKEND = (os.getenv("POSTMORTEM_STORE_BACKEND") or os.getenv("INCIDENT_STORE_BACKEND", "json")).strip().lower()
OUTBOX_DSN = os.getenv("POSTMORTEM_STORE_DSN") or os.getenv("INCIDENT_STORE_DSN") or os.getenv("DATABASE_URL")
outbox_store = ReliableOutboxStore(
    backend=OUTBOX_BACKEND,
    dsn=OUTBOX_DSN,
    table_name="incident.postmortems_outbox",
    json_path=Path(DATA_DIR) / "postmortems_outbox.json",
    owner_service="postmortem-svc",
)
inbox_store = ReliableInboxStore(
    backend=OUTBOX_BACKEND,
    dsn=OUTBOX_DSN,
    table_name="incident.postmortems_inbox",
    json_path=Path(DATA_DIR) / "postmortems_inbox.json",
    owner_service="postmortem-svc",
    consumer_name="postmortem-resolved-incident-consumer",
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
    except IncidentConcurrencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    event: EventEnvelope | None = None
    reservation: Dict[str, Any] | None = None
    raw_event = body.get("event")
    if raw_event is not None:
        try:
            event = EventEnvelope.from_dict(raw_event)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid incident delivery event: {exc}") from exc
        incident_id = str(body.get("incident_id") or "").strip()
        if (
            event.event_type != "incident.resolved"
            or event.aggregate_type != "incident"
            or event.aggregate_id != incident_id
            or str(event.payload.get("incident_id") or "") != incident_id
            or event.producer_service != "incident-svc"
            or str(event.payload.get("terminal_status") or "")
            not in {"resolved", "closed"}
        ):
            raise HTTPException(
                status_code=422,
                detail="incident delivery envelope does not match the requested incident_id",
            )
        existing_result = store.find_postmortem_for_incident(incident_id)
        result_ref = (
            existing_result.postmortem_id
            if existing_result is not None
            else postmortem_id_for_incident(incident_id)
        )
        inbox_state, reservation = inbox_store.reserve(
            event,
            result_ref=result_ref,
            lease_seconds=_positive_int_env("POSTMORTEMS_INBOX_CLAIM_SECONDS", 30),
        )
        if inbox_state == "conflict":
            raise HTTPException(status_code=409, detail="divergent incident delivery replay")
        if inbox_state == "in_progress":
            raise HTTPException(
                status_code=503,
                detail="incident delivery is already claimed and awaiting an applied receipt",
            )
        if inbox_state == "applied":
            applied_result_ref = str(reservation.get("result_ref") or "")
            existing = store.get_postmortem(applied_result_ref)
            if existing is None:
                raise HTTPException(
                    status_code=503,
                    detail="incident delivery receipt is applied but its postmortem result is not visible",
                )
            if existing.incident_id != incident_id:
                raise HTTPException(
                    status_code=409,
                    detail="incident delivery result identity is occupied by another incident",
                )
            response.status_code = 200
            return _to_response(existing)

    consumer = ResolvedIncidentPostmortemDraftConsumer(incident_store=store)
    try:
        result = consumer.consume(body)
    except PostmortemDraftConsumerRetryableError as exc:
        if event is not None and reservation is not None:
            inbox_store.release_reservation(event, reservation)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PostmortemDraftConsumerError as exc:
        if event is not None and reservation is not None:
            inbox_store.release_reservation(event, reservation)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        if event is not None and reservation is not None:
            inbox_store.release_reservation(event, reservation)
        raise

    if not result.created:
        response.status_code = 200

    if event is not None:
        try:
            inbox_store.record_applied(
                event,
                result_ref=result.postmortem.postmortem_id,
                notes=f"created={result.created}; updated={result.updated}",
                reservation=reservation,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("AUDIT: Postmortem inbox receipt write failed for %s", event.event_id)
            raise HTTPException(
                status_code=503,
                detail=f"postmortem applied; inbox receipt awaiting retry: {exc}",
            ) from exc

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

def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _nonnegative_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _postmortem_delivery_ids(
    postmortem_id: str,
    intent_checksum: str,
) -> tuple[str, str, str]:
    digest = sha256_checksum(
        {
            "postmortem_id": postmortem_id,
            "transition": "published",
            "intent_checksum": intent_checksum,
        }
    )[:24]
    return (
        f"evt-postmortem-published-{digest}",
        f"idmp-postmortem-published-{digest}",
        f"outbox-postmortem-published-{digest}",
    )


def _delivery_environment(deployment_stage: str | None) -> EnvironmentName:
    normalized = str(deployment_stage or "").strip().lower()
    if normalized == "frozen":
        normalized = EnvironmentName.LIVE.value
    try:
        return EnvironmentName(normalized)
    except ValueError:
        configured = os.getenv("PANTHEON_EVENT_ENVIRONMENT", EnvironmentName.DEV.value)
        try:
            return EnvironmentName(configured.strip().lower())
        except ValueError:
            return EnvironmentName.DEV


def _bridge_proposal(
    postmortem: Dict[str, Any],
    incident: Dict[str, Any],
    *,
    publish_event_id: str,
) -> Dict[str, Any] | None:
    from services.evolution.postmortem_bridge import (
        build_published_postmortem_proposal_request,
        on_postmortem_published,
    )

    bridge_input = {
        **postmortem,
        "severity": incident.get("severity"),
        "corrective_action_required": bool(postmortem.get("action_items")),
    }
    bridge_result = on_postmortem_published(bridge_input)
    if bridge_result is None:
        return None

    proposal = build_published_postmortem_proposal_request(
        postmortem,
        incident,
        publish_event_id=publish_event_id,
    )
    bridge_action = str(bridge_result["proposed_action"])
    stage = str(postmortem.get("deployment_stage") or incident.get("deployment_stage") or "").strip().lower()
    if bridge_action == "rollback":
        proposal["action_type"] = "flag_for_review"
    elif bridge_action == "freeze" and stage == "frozen":
        proposal["action_type"] = "flag_for_review"
    else:
        proposal["action_type"] = bridge_action
    proposal["rationale"] = str(bridge_result["rationale"])
    metadata = dict(proposal.get("metadata") or {})
    metadata.update(
        {
            "bridge_contract": "on_postmortem_published",
            "bridge_proposed_action": bridge_action,
            "bridge_cooldown_window_hours": bridge_result["cooldown_window_hours"],
            "bridge_action_normalized": proposal["action_type"] != bridge_action,
        }
    )
    proposal["metadata"] = metadata
    return proposal


def _prepare_evolution_delivery(
    postmortem: Dict[str, Any],
    incident: Dict[str, Any],
) -> ReliableOutboxRecord:
    postmortem_id = str(postmortem["postmortem_id"])
    intent_postmortem = dict(postmortem)
    intent_postmortem.pop("published_event_id", None)
    intent_checksum = sha256_checksum(
        {"postmortem": intent_postmortem, "incident": incident}
    )
    event_id, idempotency_key, outbox_id = _postmortem_delivery_ids(
        postmortem_id,
        intent_checksum,
    )
    published_snapshot = {**intent_postmortem, "published_event_id": event_id}
    proposal = _bridge_proposal(
        published_snapshot,
        incident,
        publish_event_id=event_id,
    )
    trace = TraceContext.new(
        environment=EnvironmentScope(
            name=_delivery_environment(str(incident.get("deployment_stage") or ""))
        ),
        source_system="postmortem-svc",
        correlation_id=str(postmortem.get("trace_id") or "") or None,
        idempotency_key=idempotency_key,
    )
    event = EventEnvelope(
        event_id=event_id,
        event_type="postmortem.published",
        aggregate_type="postmortem",
        aggregate_id=postmortem_id,
        sequence_no=1,
        trace=trace,
        payload={
            "postmortem_id": postmortem_id,
            "incident_id": incident["incident_id"],
            "decision_id": proposal.get("decision_id") if proposal else None,
            "proposal": proposal,
            "postmortem": published_snapshot,
            "incident": incident,
        },
        idempotency_key=idempotency_key,
        producer_service="postmortem-svc",
    )
    record = OutboxRecord(
        outbox_id=outbox_id,
        owner_service="postmortem-svc",
        event=event,
    )
    prepared = outbox_store.prepare(
        record=record,
        transition={
            "aggregate_type": "postmortem",
            "aggregate_id": postmortem_id,
            "expected_statuses": ["published"],
            "expected_snapshot_checksum": sha256_checksum(published_snapshot),
            "source_incident_checksum": sha256_checksum(incident),
            "published_event_id": event_id,
        },
    )
    log.info(
        "AUDIT: Prepared postmortem %s bridge event in outbox %s proposal=%s",
        postmortem_id,
        outbox_id,
        bool(proposal),
    )
    return prepared


def _postmortem_transition_applied(transition: Dict[str, Any]) -> bool:
    if transition.get("aggregate_type") != "postmortem":
        return False
    postmortem = store.get_postmortem(str(transition.get("aggregate_id") or ""))
    expected = set(transition.get("expected_statuses") or [])
    if postmortem is None or postmortem.status not in expected:
        return False
    expected_event_id = str(transition.get("published_event_id") or "")
    if expected_event_id and postmortem.published_event_id != expected_event_id:
        return False
    return True


def reconcile_postmortems_outbox() -> int:
    return reconcile_prepared(outbox_store, transition_applied=_postmortem_transition_applied)


def _publish_postmortem_to_evolution_if_needed(postmortem_id: str) -> None:
    """Compatibility helper for direct callers; status routes use prepare/activate."""
    postmortem = store.get_postmortem(postmortem_id)
    if postmortem is None or postmortem.status != "published":
        raise ValueError(f"published postmortem not found: {postmortem_id}")
    incident = store.get_incident(postmortem.incident_id)
    if incident is None:
        raise ValueError(f"parent incident not found: {postmortem.incident_id}")
    outbox_store.activate(_prepare_evolution_delivery(postmortem.to_dict(), incident.to_dict()))


# background delivery loop for postmortems
async def process_postmortems_outbox():
    reconcile_postmortems_outbox()
    records = outbox_store.claim_due(
        worker_id="postmortems-outbox-worker",
        lease_seconds=_positive_int_env("POSTMORTEMS_OUTBOX_CLAIM_SECONDS", 30),
    )
    if not records:
        return

    import httpx
    evolution_url = os.getenv("EVOLUTION_URL", "http://localhost:8093").strip()
    url = f"{evolution_url}/api/evolution/proposals"
    max_attempts = _positive_int_env("POSTMORTEMS_OUTBOX_MAX_ATTEMPTS", 8)
    base_delay = _nonnegative_float_env("POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS", 1.0)

    from services.evolution.client import (
        EvolutionAuthenticationError,
        EvolutionClient,
        EvolutionClientError,
        EvolutionReadbackError,
    )

    evo_client = EvolutionClient(
        base_url=evolution_url,
        auth_token=os.getenv("EVOLUTION_AUTH_TOKEN"),
        tenant_id=os.getenv("EVOLUTION_DEFAULT_TENANT_ID") or os.getenv("PANTHEON_TENANT_ID") or "pantheon-default",
    )

    async with httpx.AsyncClient(timeout=5.0) as http_client:
        evo_client._async_client = http_client
        for record in records:
            postmortem_id = record.event.payload.get("postmortem_id")
            log.info("AUDIT: Outbox worker attempting delivery of postmortem %s proposal to %s", postmortem_id, url)

            proposal_payload = record.event.payload.get("proposal")
            if proposal_payload is None:
                log.info("AUDIT: Bridge produced no proposal for postmortem %s", postmortem_id)
                applied, canonical = outbox_store.complete_published(record)
                if not applied:
                    log.warning(
                        "AUDIT: Ignored stale no-op completion for postmortem outbox %s; canonical status=%s",
                        record.outbox_id,
                        canonical.status,
                    )
                continue
            request_payload = {**dict(proposal_payload), "delivery_event": record.event.to_dict()}

            try:
                data, readback = await evo_client.submit_proposal(
                    request_payload,
                    idempotency_key=record.event.idempotency_key,
                    verify_readback=True,
                )
                log.info("AUDIT: Successfully delivered proposal for postmortem %s to evolution. Readback verified: %s", postmortem_id, bool(readback))
                applied, canonical = outbox_store.complete_published(record)
                if not applied:
                    log.warning(
                        "AUDIT: Ignored stale success completion for postmortem outbox %s; canonical status=%s",
                        record.outbox_id,
                        canonical.status,
                    )
            except EvolutionAuthenticationError as exc:
                err_msg = f"status_code={exc.status_code} body={exc.response_body}"
                log.warning("AUDIT: Outbox delivery attempt %d for postmortem %s auth failed: %s", record.delivery_attempts + 1, postmortem_id, err_msg)
                applied, canonical = outbox_store.complete_failed(
                    record,
                    err_msg,
                    max_attempts=max_attempts,
                    base_delay_seconds=base_delay,
                    permanent=(exc.status_code in {400, 401, 403, 409, 422}),
                )
            except (EvolutionClientError, EvolutionReadbackError) as exc:
                err_msg = f"status_code={exc.status_code} body={exc.response_body or str(exc)}"
                log.warning("AUDIT: Outbox delivery attempt %d for postmortem %s returned error: %s", record.delivery_attempts + 1, postmortem_id, err_msg)
                applied, canonical = outbox_store.complete_failed(
                    record,
                    err_msg,
                    max_attempts=max_attempts,
                    base_delay_seconds=base_delay,
                    permanent=(exc.status_code in {400, 409, 422}),
                )
            except Exception as exc:
                err_msg = str(exc)
                log.warning("AUDIT: Outbox delivery attempt %d for postmortem %s failed with exception: %s", record.delivery_attempts + 1, postmortem_id, err_msg)
                applied, canonical = outbox_store.complete_failed(
                    record,
                    err_msg,
                    max_attempts=max_attempts,
                    base_delay_seconds=base_delay,
                )
                if not applied:
                    log.warning(
                        "AUDIT: Ignored stale exception completion for postmortem outbox %s; canonical status=%s",
                        record.outbox_id,
                        canonical.status,
                    )

async def postmortems_outbox_loop():
    while True:
        try:
            await process_postmortems_outbox()
        except Exception as exc:
            log.exception("Error in postmortems outbox delivery loop: %s", exc)
        await asyncio.sleep(_nonnegative_float_env("POSTMORTEMS_OUTBOX_POLL_SECONDS", 2.0))

@app.on_event("startup")
def start_postmortems_outbox_worker():
    import sys
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return
    asyncio.create_task(postmortems_outbox_loop())


@app.post("/api/postmortems/outbox/{outbox_id}/redrive")
def redrive_postmortem_outbox(
    outbox_id: str,
    body: Dict[str, Any] = Body(...),
    redrive_token: Optional[str] = Header(None, alias="X-Pantheon-Outbox-Redrive-Token"),
) -> Dict[str, Any]:
    expected_token = os.getenv("POSTMORTEMS_OUTBOX_REDRIVE_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail="postmortem outbox redrive is not configured")
    if not redrive_token or not secrets.compare_digest(redrive_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid postmortem outbox redrive token")
    actor_id = str(body.get("actor_id") or "").strip()
    actor_role = str(body.get("actor_role") or "").strip()
    approval_ref = str(body.get("approval_ref") or "").strip()
    reason = str(body.get("reason") or "").strip()
    if actor_role not in {"operator", "risk_owner"} or not actor_id or not approval_ref or not reason:
        raise HTTPException(
            status_code=422,
            detail="actor_id, operator/risk_owner actor_role, approval_ref, and reason are required",
        )
    try:
        record = outbox_store.redrive(
            outbox_id,
            actor=f"{actor_role}:{actor_id}",
            note=f"approval_ref={approval_ref}; {reason}",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"outbox record not found: {outbox_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log.warning("AUDIT: Redrove postmortem outbox %s by %s:%s", outbox_id, actor_role, actor_id)
    return record.to_dict()


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

    crosses_published_boundary = pm.status != "published" and body.status == "published"
    prepared: ReliableOutboxRecord | None = None
    published_at = body.published_at or pm.published_at
    source_snapshot = pm.to_dict()
    if crosses_published_boundary:
        from services.evolution.postmortem_bridge import PostmortemBridgeError

        published_at = published_at or _utc_now()
        prospective = pm.to_dict()
        prospective["status"] = "published"
        prospective["published_at"] = published_at
        try:
            prepared = _prepare_evolution_delivery(prospective, incident.to_dict())
        except PostmortemBridgeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Bridge precondition validation failed: {exc}"
            ) from exc
        except Exception as exc:
            log.exception("AUDIT: Could not prepare postmortem outbox for %s", postmortem_id)
            raise HTTPException(
                status_code=503,
                detail=f"postmortem status not changed because delivery intent could not be persisted: {exc}",
            ) from exc

    try:
        updated = store.update_postmortem_status(
            postmortem_id,
            body.status,
            published_at=published_at,
            published_event_id=(prepared.event.event_id if prepared is not None else None),
            expected_snapshot=source_snapshot if prepared is not None else None,
            expected_incident_snapshot=(
                incident.to_dict() if prepared is not None else None
            ),
        )
    except IncidentError as exc:
        status_code = 409 if "changed concurrently" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))

    log.info("Postmortem %s → status=%s", postmortem_id, body.status)

    if prepared is not None:
        try:
            outbox_store.activate(prepared)
        except Exception as exc:
            log.exception(
                "AUDIT: Postmortem %s published but its durable prepared outbox could not be activated",
                postmortem_id,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "postmortem status changed and durable delivery intent is awaiting reconciliation: "
                    f"{exc}"
                ),
            ) from exc

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
