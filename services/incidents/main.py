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

POST  /api/incidents/consume-threshold
    Consume a telemetry threshold-breach payload and create an IncidentCase.
    Returns 201 on first write, 200 when the payload is idempotently replayed.

POST  /api/incidents/consume-drift-report
    Consume a reconciliation DriftReport threshold breach into a deduped
    IncidentCase. Returns 201 on first write, 200 when the payload updates an
    existing binding/runtime/cluster incident.

POST  /api/incidents/consume-infrastructure-health
    Consume a non-trading infrastructure-health incident payload and create a
    stable IncidentCase without caller-supplied RuntimeBinding evidence.
    Returns 201 on first write and 200 for exact idempotent replay.

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
    OutboxRecordStatus,
    TraceContext,
    EnvironmentScope,
    EnvironmentName,
)
from services.foundation.reliable_delivery import (
    ReliableOutboxRecord,
    ReliableOutboxStore,
    reconcile_prepared,
)
from services.foundation.serialization import sha256_checksum

# ---------------------------------------------------------------------------
# Bootstrap domain layer from sibling services/incident/ directory
# ---------------------------------------------------------------------------
_INC_DIR = Path(__file__).resolve().parent.parent / "incident"
if str(_INC_DIR) not in sys.path:
    sys.path.insert(0, str(_INC_DIR.parent))

try:
    from services.incident.incident import (  # type: ignore
        IncidentCase,
        IncidentConcurrencyError,
        IncidentError,
        IncidentStatus,
        IncidentStore,
        validate_incident_case,
    )
    from services.incident.pg_store import build_incident_store  # type: ignore
except ImportError:
    from incident.incident import (  # type: ignore
        IncidentCase,
        IncidentConcurrencyError,
        IncidentError,
        IncidentStatus,
        IncidentStore,
        validate_incident_case,
    )
    from incident.pg_store import build_incident_store  # type: ignore

try:
    from .models import (
        CreateIncidentRequest,
        IncidentResponse,
        OperatorIncidentPayload,
        UpdateIncidentStatusRequest,
    )
    from .consumer import (
        DriftReportIncidentConsumer,
        InfrastructureHealthIncidentConsumer,
        IncidentConsumerError,
        IncidentConsumerRetryableError,
        ThresholdTelemetryIncidentConsumer,
    )
except ImportError:
    from models import (  # type: ignore
        CreateIncidentRequest,
        IncidentResponse,
        OperatorIncidentPayload,
        UpdateIncidentStatusRequest,
    )
    from consumer import (  # type: ignore
        DriftReportIncidentConsumer,
        InfrastructureHealthIncidentConsumer,
        IncidentConsumerError,
        IncidentConsumerRetryableError,
        ThresholdTelemetryIncidentConsumer,
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
STORE_BACKEND = (os.getenv("INCIDENT_STORE_BACKEND") or os.getenv("POSTMORTEM_STORE_BACKEND", "json")).strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("incidents")

store: IncidentStore = build_incident_store(STORE_PATH)
reference_validator = CanonicalReferenceValidator()

OUTBOX_BACKEND = STORE_BACKEND
OUTBOX_DSN = os.getenv("INCIDENT_STORE_DSN") or os.getenv("DATABASE_URL")
outbox_store = ReliableOutboxStore(
    backend=OUTBOX_BACKEND,
    dsn=OUTBOX_DSN,
    table_name="incident.incidents_outbox",
    json_path=Path(DATA_DIR) / "incidents_outbox.json",
    owner_service="incident-svc",
)

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
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {"incident_count": len(store.list_incidents())},
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


def _to_response(inc: IncidentCase) -> IncidentResponse:
    d = inc.to_dict()
    d.setdefault("telemetry_event_ids", [])
    d.setdefault("reconciliation_ids", [])
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
            reconciliation_ids=body.reconciliation_ids,
            incident_cluster_id=body.incident_cluster_id,
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
    except IncidentConcurrencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IncidentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    log.info("Created IncidentCase %s severity=%s binding=%s", incident_id, body.severity, body.binding_id)
    return _to_response(inc)


@app.post(
    "/api/incidents/consume-threshold",
    response_model=IncidentResponse,
    status_code=201,
    summary="Consume a telemetry threshold breach into an IncidentCase",
)
def consume_threshold_incident(
    response: Response,
    body: Dict[str, Any] = Body(...),
) -> IncidentResponse:
    """Consume a threshold telemetry payload through the Incident domain writer."""
    consumer = ThresholdTelemetryIncidentConsumer(
        incident_store=store,
        reference_validator=reference_validator,
    )
    try:
        result = consumer.consume(body)
    except CanonicalReferenceError as exc:
        raise HTTPException(status_code=422, detail={"reference_errors": exc.errors})
    except IncidentConsumerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not result.created:
        response.status_code = 200

    log.info(
        "Consumed threshold telemetry into IncidentCase %s created=%s",
        result.incident.incident_id,
        result.created,
    )
    return _to_response(result.incident)


@app.post(
    "/api/incidents/consume-drift-report",
    response_model=IncidentResponse,
    status_code=201,
    summary="Consume a DriftReport threshold breach into a deduped IncidentCase",
)
def consume_drift_report_incident(
    response: Response,
    body: Dict[str, Any] = Body(...),
) -> IncidentResponse:
    """Consume a DriftReport through the Incident domain writer."""
    consumer = DriftReportIncidentConsumer(
        incident_store=store,
        reference_validator=reference_validator,
    )
    try:
        result = consumer.consume(body)
    except CanonicalReferenceError as exc:
        raise HTTPException(status_code=422, detail={"reference_errors": exc.errors})
    except IncidentConsumerRetryableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except IncidentConsumerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not result.created:
        response.status_code = 200

    log.info(
        "Consumed DriftReport into IncidentCase %s created=%s cluster=%s",
        result.incident.incident_id,
        result.created,
        result.incident_cluster_id,
    )
    return _to_response(result.incident)


@app.post(
    "/api/incidents/consume-infrastructure-health",
    response_model=IncidentResponse,
    status_code=201,
    summary="Consume a non-trading infrastructure-health event into an IncidentCase",
)
def consume_infrastructure_health_incident(
    response: Response,
    body: Dict[str, Any] = Body(...),
) -> IncidentResponse:
    """Consume BFF/downstream infrastructure health through Incident authority.

    This endpoint is deliberately separate from generic ``POST /api/incidents``:
    producers must send the strict infrastructure incident contract and must not
    provide fake RuntimeBinding fields to pass canonical runtime validation.
    """
    consumer = InfrastructureHealthIncidentConsumer(incident_store=store)
    try:
        result = consumer.consume(body)
    except IncidentConsumerError as exc:
        detail = str(exc)
        status_code = 409 if "conflicts with an existing infrastructure incident" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail)

    if not result.created:
        response.status_code = 200

    log.info(
        "Consumed infrastructure health into IncidentCase %s created=%s",
        result.incident.incident_id,
        result.created,
    )
    return _to_response(result.incident)


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

_TERMINAL_INCIDENT_STATUSES = {"resolved", "closed"}


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


def _incident_delivery_ids(incident_id: str) -> tuple[str, str, str]:
    digest = sha256_checksum({"incident_id": incident_id, "transition": "terminal"})[:24]
    return (
        f"evt-incident-terminal-{digest}",
        f"idmp-incident-terminal-{digest}",
        f"outbox-incident-terminal-{digest}",
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


def _is_legacy_direct_close_intent(
    existing: ReliableOutboxRecord,
    *,
    incoming_record: OutboxRecord,
    transition: Dict[str, Any],
) -> bool:
    """Recognize the inert direct-close intent emitted before normalization.

    PR #3682 used the same terminal-boundary IDs but copied the requested
    direct-close status into the event payload. A crash or losing domain CAS
    could leave that exact intent prepared while the Incident stayed open.
    Adopt only that historical shape; every other divergent identity still
    fails closed through ``ReliableOutboxStore.prepare``.
    """

    expected_payload = {
        "incident_id": incoming_record.event.aggregate_id,
        "terminal_status": "closed",
    }
    incoming_payload = {
        "incident_id": incoming_record.event.aggregate_id,
        "terminal_status": "resolved",
    }
    stable_event_fields = (
        "event_id",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "sequence_no",
        "idempotency_key",
        "causal_parent_id",
        "producer_service",
        "schema_ref",
        "schema_version",
    )
    return (
        existing.status == OutboxRecordStatus.PENDING
        and not existing.delivery_ready
        and existing.claim_token is None
        and existing.delivery_attempts == 0
        and existing.last_error is None
        and existing.next_attempt_at is None
        and existing.redrive_count == 0
        and existing.owner_service == incoming_record.owner_service
        and existing.record.schema_version == incoming_record.schema_version
        and dict(existing.transition or {}) == transition
        and dict(existing.event.payload) == expected_payload
        and dict(incoming_record.event.payload) == incoming_payload
        and all(
            getattr(existing.event, field) == getattr(incoming_record.event, field)
            for field in stable_event_fields
        )
    )


def _prepare_postmortem_delivery(incident_id: str) -> ReliableOutboxRecord:
    incident = store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"incident not found while preparing delivery: {incident_id}")
    event_id, idempotency_key, outbox_id = _incident_delivery_ids(incident_id)
    trace = TraceContext.new(
        environment=EnvironmentScope(name=_delivery_environment(incident.deployment_stage)),
        source_system="incident-svc",
        idempotency_key=idempotency_key,
    )
    event = EventEnvelope(
        event_id=event_id,
        event_type="incident.resolved",
        aggregate_type="incident",
        aggregate_id=incident_id,
        sequence_no=1,
        trace=trace,
        # The deterministic event represents the first terminal boundary, not
        # a request attempt.  Keep the legacy-compatible ``resolved`` value
        # stable so failed resolve and later direct-close attempts can reuse
        # one durable intent without an identity collision.
        payload={"incident_id": incident_id, "terminal_status": "resolved"},
        idempotency_key=idempotency_key,
        producer_service="incident-svc",
    )
    record = OutboxRecord(
        outbox_id=outbox_id,
        owner_service="incident-svc",
        event=event,
    )
    transition = {
        "aggregate_type": "incident",
        "aggregate_id": incident_id,
        "expected_statuses": sorted(_TERMINAL_INCIDENT_STATUSES),
    }
    try:
        prepared = outbox_store.prepare(record=record, transition=transition)
    except ValueError:
        existing = outbox_store.get(outbox_id)
        if existing is None or not _is_legacy_direct_close_intent(
            existing,
            incoming_record=record,
            transition=transition,
        ):
            raise
        prepared = existing
        log.warning(
            "AUDIT: Adopted legacy prepared direct-close intent %s for incident %s",
            outbox_id,
            incident_id,
        )
    log.info("AUDIT: Prepared resolved incident %s in outbox %s", incident_id, outbox_id)
    return prepared


def _incident_transition_applied(transition: Dict[str, Any]) -> bool:
    if transition.get("aggregate_type") != "incident":
        return False
    incident_id = str(transition.get("aggregate_id") or "")
    incident = store.get_incident(incident_id)
    expected = set(transition.get("expected_statuses") or [])
    return incident is not None and incident.status in expected


def reconcile_incidents_outbox() -> int:
    return reconcile_prepared(outbox_store, transition_applied=_incident_transition_applied)


def _repair_prepared_after_transition_conflict(record: ReliableOutboxRecord) -> None:
    """Activate a winning terminal transition or preserve its future intent.

    A nonterminal conflict cannot safely delete the deterministic record: a
    concurrent terminal winner may already have reused the exact prepared
    snapshot and be between its domain CAS and activation.  The transition-
    neutral intent stays inert until a later terminal commit activates it.
    """

    try:
        if _incident_transition_applied(dict(record.transition or {})):
            outbox_store.activate(record)
        else:
            log.info(
                "AUDIT: Preserved prepared incident intent %s after nonterminal CAS conflict",
                record.outbox_id,
            )
    except Exception:
        # Preserve the original domain conflict for the caller.  The exact
        # compare/activate operation above cannot regress a concurrently
        # advanced outbox record.
        log.exception(
            "AUDIT: Could not reconcile losing prepared incident intent %s",
            record.outbox_id,
        )


# background delivery loop for incidents
async def process_incidents_outbox():
    reconcile_incidents_outbox()
    records = outbox_store.claim_due(
        worker_id="incidents-outbox-worker",
        lease_seconds=_positive_int_env("INCIDENTS_OUTBOX_CLAIM_SECONDS", 30),
    )
    if not records:
        return

    import httpx
    postmortems_url = os.getenv("POSTMORTEMS_URL", "http://localhost:8091").strip()
    url = f"{postmortems_url}/api/postmortems/consume-resolved-incident"
    max_attempts = _positive_int_env("INCIDENTS_OUTBOX_MAX_ATTEMPTS", 8)
    base_delay = _nonnegative_float_env("INCIDENTS_OUTBOX_BACKOFF_BASE_SECONDS", 1.0)

    async with httpx.AsyncClient(timeout=5.0) as client:
        for record in records:
            incident_id = record.event.payload.get("incident_id")
            log.info("AUDIT: Outbox worker attempting delivery of resolved incident %s to %s", incident_id, url)

            try:
                resp = await client.post(
                    url,
                    json={"incident_id": incident_id, "event": record.event.to_dict()},
                )
                if resp.status_code in {200, 201}:
                    log.info("AUDIT: Successfully delivered resolved incident %s to postmortems via outbox. Status: %d", incident_id, resp.status_code)
                    applied, canonical = outbox_store.complete_published(record)
                    if not applied:
                        log.warning(
                            "AUDIT: Ignored stale success completion for incident outbox %s; canonical status=%s",
                            record.outbox_id,
                            canonical.status,
                        )
                else:
                    err_msg = f"status_code={resp.status_code} body={resp.text}"
                    log.warning("AUDIT: Outbox delivery attempt %d for resolved incident %s returned error: %s", record.delivery_attempts + 1, incident_id, err_msg)
                    applied, canonical = outbox_store.complete_failed(
                        record,
                        err_msg,
                        max_attempts=max_attempts,
                        base_delay_seconds=base_delay,
                        permanent=resp.status_code in {400, 409, 422},
                    )
                    if not applied:
                        log.warning(
                            "AUDIT: Ignored stale failure completion for incident outbox %s; canonical status=%s",
                            record.outbox_id,
                            canonical.status,
                        )
            except Exception as exc:
                err_msg = str(exc)
                log.warning("AUDIT: Outbox delivery attempt %d for resolved incident %s failed with exception: %s", record.delivery_attempts + 1, incident_id, err_msg)
                applied, canonical = outbox_store.complete_failed(
                    record,
                    err_msg,
                    max_attempts=max_attempts,
                    base_delay_seconds=base_delay,
                )
                if not applied:
                    log.warning(
                        "AUDIT: Ignored stale exception completion for incident outbox %s; canonical status=%s",
                        record.outbox_id,
                        canonical.status,
                    )

async def incidents_outbox_loop():
    while True:
        try:
            await process_incidents_outbox()
        except Exception as exc:
            log.exception("Error in incidents outbox delivery loop: %s", exc)
        await asyncio.sleep(_nonnegative_float_env("INCIDENTS_OUTBOX_POLL_SECONDS", 2.0))

@app.on_event("startup")
def start_incidents_outbox_worker():
    import sys
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return
    asyncio.create_task(incidents_outbox_loop())


@app.post("/api/incidents/outbox/{outbox_id}/redrive")
def redrive_incident_outbox(
    outbox_id: str,
    body: Dict[str, Any] = Body(...),
    redrive_token: Optional[str] = Header(None, alias="X-Pantheon-Outbox-Redrive-Token"),
) -> Dict[str, Any]:
    expected_token = os.getenv("INCIDENTS_OUTBOX_REDRIVE_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail="incident outbox redrive is not configured")
    if not redrive_token or not secrets.compare_digest(redrive_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid incident outbox redrive token")
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
    log.warning("AUDIT: Redrove incident outbox %s by %s:%s", outbox_id, actor_role, actor_id)
    return record.to_dict()


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
    previous = _get_or_404(incident_id)
    crosses_terminal_boundary = (
        previous.status not in _TERMINAL_INCIDENT_STATUSES
        and body.status in _TERMINAL_INCIDENT_STATUSES
    )
    prepared: ReliableOutboxRecord | None = None
    if crosses_terminal_boundary:
        try:
            prepared = _prepare_postmortem_delivery(incident_id)
        except Exception as exc:
            log.exception("AUDIT: Could not prepare incident outbox for %s", incident_id)
            raise HTTPException(
                status_code=503,
                detail=f"incident status not changed because delivery intent could not be persisted: {exc}",
            ) from exc
    try:
        updated = store.update_incident_status(
            incident_id,
            body.status,
            resolved_at=body.resolved_at or previous.resolved_at,
            expected_snapshot=previous.to_dict(),
        )
    except IncidentError as exc:
        if prepared is not None:
            _repair_prepared_after_transition_conflict(prepared)
        status_code = 409 if "changed concurrently" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))

    log.info("IncidentCase %s → status=%s", incident_id, body.status)

    if prepared is not None:
        try:
            outbox_store.activate(prepared)
        except Exception as exc:
            log.exception(
                "AUDIT: Incident %s transitioned but its durable prepared outbox could not be activated",
                incident_id,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "incident status changed and durable delivery intent is awaiting reconciliation: "
                    f"{exc}"
                ),
            ) from exc

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
    inc_dict.setdefault("reconciliation_ids", [])

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
