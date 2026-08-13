"""
Evolution Service — EvolutionDecision lifecycle API

Canonical HTTP service for creating, reviewing, approving, executing,
cancelling, and querying EvolutionDecision records.

All cooldown, convergence, actor-role, and evidence-linkage rules are
enforced by the platform objects imported from
  services/control-plane/governance/evolution_decision.py
  services/control-plane/governance/evolution_controller.py

Routes
------
  POST   /api/evolution/proposals                         propose
  POST   /api/evolution/proposals/from-incident           propose from incident/postmortem
  GET    /api/evolution/proposals                         list / filter
  GET    /api/evolution/proposals/{decision_id}           get single
  POST   /api/evolution/proposals/{decision_id}/review    mark reviewed
  POST   /api/evolution/proposals/{decision_id}/approve   approve
  POST   /api/evolution/proposals/{decision_id}/reject    reject
  POST   /api/evolution/proposals/{decision_id}/cancel    cancel
  POST   /api/evolution/proposals/{decision_id}/execute   execute
  GET    /api/evolution/proposals/{decision_id}/boundary  routing boundary
  POST   /api/evolution/threshold-evaluate                evaluate a snapshot
  GET    /health                                           liveness probe

Policy references
-----------------
  EVOLUTION_REVIEW_AND_THRESHOLDS.md
  EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md
"""
from __future__ import annotations

import contextvars
import hmac
import json
import logging
import os
import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

try:  # pragma: no cover - Linux production and CI provide fcntl.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from services.foundation import (
    EventEnvelope,
    FoundationValidationError,
    InboxReceipt,
    InboxReceiptStatus,
    sha256_checksum,
)
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture

# ---------------------------------------------------------------------------
# Path bootstrap — platform objects live in control-plane/governance
# ---------------------------------------------------------------------------
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import EvidenceRef, EvidenceRefType  # type: ignore
from evolution_controller import (  # type: ignore
    EvolutionController,
    EvolutionControllerError,
    FreezeFollowthroughMode,
    ThresholdEvaluator,
)
from deployment_plan import DeploymentStage  # type: ignore
from evolution_decision import (  # type: ignore
    DEFAULT_TENANT_ID,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionError,
    EvolutionDecisionState,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ExecutionResult,
    ExecutionStatus,
    ThresholdSnapshot,
    normalize_tenant_id,
    utc_now,
    validate_evolution_decision,
)

# ---------------------------------------------------------------------------
# Local models
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import (  # type: ignore
    ActionPathEntry,
    ActionPathsResponse,
    ApproveRequest,
    BoundaryResponse,
    CancelRequest,
    CompensationListResponse,
    CompensationResolveRequest,
    CompensationResponse,
    DailySweepRequest,
    DailySweepResponse,
    DecisionResponse,
    DispatchCommandResponse,
    DispatchOutboxListResponse,
    DispatchOutboxRecordResponse,
    DispatchReplayRequest,
    ExecuteRequest,
    LearnFeedbackWritebackRequest,
    LearnFeedbackWritebackResponse,
    ObservationWindowReportResponse,
    ProposeFromIncidentRequest,
    ProposeFromPostmortemPublishedRequest,
    ProposeRequest,
    RejectRequest,
    RedeployFollowthroughRequest,
    ReviewRequest,
    RollbackFollowthroughRequest,
    ThresholdEvalRequest,
    ThresholdEvalResponse,
)
from sweep import run_daily_sweep  # type: ignore
from postmortem_bridge import (  # type: ignore
    PostmortemBridgeError,
    build_evolution_learn_feedback_writeback,
    build_published_postmortem_proposal_request,
)
from services.evolution.dispatch_outbox import (
    CompensationLedger,
    DispatchIntent,
    EvolutionDispatchError,
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
)
from services.evolution.dispatch_receipts import (
    DispatchReceiptError,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    build_adapter_registry,
    supported_planes,
    verify_terminal_receipt,
)
from services.evolution.threshold_sweep_worker import (
    assess_input_coverage,
    default_fetch_summaries,
    load_baselines,
    load_thresholds,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path bootstrap — incident objects live in services/incident
# ---------------------------------------------------------------------------
_INCIDENT_SVC = Path(__file__).resolve().parent.parent / "incident"
if str(_INCIDENT_SVC) not in sys.path:
    sys.path.insert(0, str(_INCIDENT_SVC))

from incident import IncidentCase, IncidentError, IncidentStore, Postmortem  # type: ignore

# ---------------------------------------------------------------------------
# App + storage
# ---------------------------------------------------------------------------
app = FastAPI(title="Pantheon Evolution Service", version="1.0.0")

EVOLUTION_DATA_DIR = os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution")
os.makedirs(EVOLUTION_DATA_DIR, exist_ok=True)
EVOLUTION_STORE_BACKEND = (
    os.getenv("EVOLUTION_STORE_BACKEND", "json").strip().lower() or "json"
)
EVOLUTION_STORE_DSN = (
    os.getenv("EVOLUTION_STORE_DSN") or os.getenv("DATABASE_URL")
)
PERSISTENCE_POSTURE = require_persistence_posture(
    "evolution",
    backend_env_vars={"EVOLUTION_STORE_BACKEND": "json"},
    require_object_store=False,
)

_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_TENANT_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "evolution_tenant_id",
    default=DEFAULT_TENANT_ID,
)


def _auth_mode() -> str:
    mode = os.getenv("EVOLUTION_AUTH_MODE", "disabled").strip().lower()
    if mode not in {"disabled", "token"}:
        raise HTTPException(
            status_code=503,
            detail="EVOLUTION_AUTH_MODE must be disabled or token",
        )
    return mode


def _configured_default_tenant() -> str:
    tenant_id = normalize_tenant_id(
        os.getenv("EVOLUTION_DEFAULT_TENANT_ID")
        or os.getenv("PANTHEON_TENANT_ID")
        or DEFAULT_TENANT_ID
    )
    if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise HTTPException(
            status_code=503,
            detail="configured Evolution tenant id is invalid",
        )
    return tenant_id


def _allowed_tenants() -> set[str]:
    return {
        item.strip()
        for item in os.getenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "").split(",")
        if item.strip()
    }


def _current_tenant() -> str:
    return _TENANT_CONTEXT.get()


def _authorized_request_tenant(tenant_id: Optional[str]) -> str:
    """Resolve body/query tenant identity against authenticated request scope."""

    if _auth_mode() == "disabled":
        return normalize_tenant_id(tenant_id) if tenant_id is not None else _current_tenant()
    supplied = normalize_tenant_id(tenant_id)
    current = _current_tenant()
    if tenant_id is not None and supplied != current:
        raise HTTPException(status_code=403, detail="tenant identity mismatch")
    return current


@app.middleware("http")
async def authenticate_tenant(request: Request, call_next):
    """Bind every Evolution API call to one authenticated tenant.

    Tests and explicitly local deployments may select ``disabled``.  Compose
    selects ``token`` and then both the bearer token and ``X-Tenant-Id`` are
    mandatory; missing secret/allowlist configuration fails closed before any
    durable state can be read or changed.
    """

    if not request.url.path.startswith("/api/evolution"):
        return await call_next(request)
    try:
        mode = _auth_mode()
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    supplied_tenant = str(request.headers.get("x-tenant-id") or "").strip()
    if supplied_tenant and not _TENANT_ID_PATTERN.fullmatch(supplied_tenant):
        return JSONResponse(status_code=400, content={"detail": "X-Tenant-Id is invalid"})

    if mode == "token":
        configured_token = os.getenv("EVOLUTION_AUTH_TOKEN", "").strip()
        allowed = _allowed_tenants()
        if not configured_token or not allowed:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Evolution tenant authentication requires "
                        "EVOLUTION_AUTH_TOKEN and EVOLUTION_AUTH_ALLOWED_TENANTS"
                    )
                },
            )
        authorization = request.headers.get("authorization") or ""
        scheme, separator, presented = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not presented
            or not hmac.compare_digest(presented, configured_token)
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid Evolution bearer token"},
            )
        if not supplied_tenant:
            return JSONResponse(
                status_code=400,
                content={"detail": "X-Tenant-Id is required"},
            )
        if "*" not in allowed and supplied_tenant not in allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "authenticated caller is not authorized for this tenant"},
            )

    try:
        tenant = supplied_tenant or _configured_default_tenant()
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    token = _TENANT_CONTEXT.set(tenant)
    try:
        response = await call_next(request)
    finally:
        _TENANT_CONTEXT.reset(token)
    response.headers["Vary"] = "Authorization, X-Tenant-Id"
    return response


class _EvolutionProposalInbox:
    """Concurrent-safe durable inbox ledger for proposal delivery events.

    Evolution decisions and inbox receipts intentionally remain separate owner
    records.  A path-scoped thread lock plus an advisory file lock serializes
    the entire check/create/receipt sequence across service instances, while
    immutable-decision comparison closes the recoverable window where the
    decision write succeeded but the receipt write did not.
    """

    _path_locks_guard = threading.Lock()
    _path_locks: Dict[str, threading.RLock] = {}

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        key = str(self.path.expanduser().resolve())
        with self._path_locks_guard:
            lock = self._path_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._path_locks[key] = lock
            self._thread_lock = lock

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._thread_lock:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read_unlocked(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("evolution proposal inbox must contain a JSON object")
        return {
            str(key): dict(value)
            for key, value in payload.items()
            if isinstance(value, Mapping)
        }

    def _write_unlocked(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                json.dump(records, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def matching_event_unlocked(self, event: EventEnvelope) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        for record in self._read_unlocked().values():
            receipt = record.get("receipt")
            if not isinstance(receipt, Mapping):
                continue
            if (
                str(receipt.get("event_id") or "") == event.event_id
                or str(receipt.get("idempotency_key") or "") == event.idempotency_key
            ):
                matches.append(record)
        return matches

    def for_decision_unlocked(self, decision_id: str) -> List[Dict[str, Any]]:
        return [
            record
            for record in self._read_unlocked().values()
            if str(record.get("decision_id") or "") == decision_id
        ]

    def record_applied_unlocked(
        self,
        *,
        event: EventEnvelope,
        decision_id: str,
        event_fingerprint: str,
        semantic_event_fingerprint: str,
        proposal_fingerprint: str,
        decision_fingerprint: str,
        notes: str,
    ) -> Dict[str, Any]:
        receipt = InboxReceipt.record(
            consumer_name="evolution-proposal-admission",
            event=event,
            status=InboxReceiptStatus.APPLIED,
            audit_action_ref=decision_id,
            notes=notes,
        )
        record = {
            "receipt": receipt.to_dict(),
            "decision_id": decision_id,
            "event_fingerprint": event_fingerprint,
            "semantic_event_fingerprint": semantic_event_fingerprint,
            "proposal_fingerprint": proposal_fingerprint,
            "decision_fingerprint": decision_fingerprint,
        }
        records = self._read_unlocked()
        records[event.event_id] = record
        self._write_unlocked(records)
        return record

    def clear(self) -> None:
        """Test/support hook that removes only this service-owned inbox file."""
        with self.locked():
            if self.path.exists():
                self.path.unlink()


proposal_inbox = _EvolutionProposalInbox(
    Path(EVOLUTION_DATA_DIR) / "proposal_delivery_inbox.json"
)

INCIDENT_DATA_DIR = os.getenv("INCIDENT_DATA_DIR", "/tmp/pantheon/incident")
os.makedirs(INCIDENT_DATA_DIR, exist_ok=True)

store = EvolutionDecisionStore(
    storage_path=os.path.join(EVOLUTION_DATA_DIR, "decisions.json"),
    backend=EVOLUTION_STORE_BACKEND,
    dsn=EVOLUTION_STORE_DSN,
)
try:
    from services.incident.pg_store import build_incident_store
except ImportError:
    from incident.pg_store import build_incident_store

incident_store = build_incident_store(Path(os.path.join(INCIDENT_DATA_DIR, "incidents.json")))
controller = EvolutionController()
evaluator = ThresholdEvaluator()

# ---------------------------------------------------------------------------
# Durable dispatch outbox — every supported approved action is made durable
# here before anything downstream is asked to do work, and a decision only
# reaches ``executed`` once the downstream reports a terminal receipt that this
# service re-reads for itself.
# ---------------------------------------------------------------------------
RESEARCH_API_URL = (
    os.getenv("EVOLUTION_RESEARCH_API_URL")
    or os.getenv("RESEARCH_ORCHESTRATOR_URL", "http://research-orchestrator-svc:8101")
)
TELEMETRY_API_URL = (
    os.getenv("EVOCHAIN_TELEMETRY_API_URL")
    or os.getenv("PANTHEON_TELEMETRY_API_URL")
    or os.getenv("PANTHEON_TELEMETRY_URL", "http://telemetry:8083")
)
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("EVOLUTION_DOWNSTREAM_TIMEOUT_SECONDS", "20"))

dispatch_outbox = EvolutionDispatchOutbox(
    build_dispatch_outbox_store(data_dir=EVOLUTION_DATA_DIR)
)
compensation_ledger = CompensationLedger(data_dir=EVOLUTION_DATA_DIR)
receipt_registry = build_adapter_registry(
    research_api_url=RESEARCH_API_URL, timeout=DOWNSTREAM_TIMEOUT_SECONDS
)

# ---------------------------------------------------------------------------
# Sweep state — updated on every POST /api/evolution/daily-sweep call.
# Exposed via GET /api/evolution/sweep-status and the /livez health metrics.
# ---------------------------------------------------------------------------
_sweep_state: Dict[str, Any] = {
    "last_success_at": None,
    "last_success_proposal_count": None,
    "last_failure_at": None,
    "last_failure_reason": None,
    "total_sweeps_run": 0,
    "total_proposals_created": 0,
}

register_fastapi_health_routes(
    app,
    "evolution",
    dependencies=lambda: {
        "persistence": PERSISTENCE_POSTURE.to_dict(),
    },
    metrics=lambda: {
        "decision_count": len(store.list_all()),
        "sweep_last_success_at": _sweep_state["last_success_at"],
        "sweep_last_failure_at": _sweep_state["last_failure_at"],
        "sweep_total_proposals_created": _sweep_state["total_proposals_created"],
    },
    details=lambda: {
        "evolution_data_dir": EVOLUTION_DATA_DIR,
        "evolution_store_backend": EVOLUTION_STORE_BACKEND,
        "incident_data_dir": INCIDENT_DATA_DIR,
        "auth_mode": os.getenv("EVOLUTION_AUTH_MODE", "disabled"),
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decision_to_response(decision: EvolutionDecision) -> DecisionResponse:
    d = decision.to_dict()
    return DecisionResponse(
        decision_id=d["decision_id"],
        tenant_id=d.get("tenant_id") or DEFAULT_TENANT_ID,
        target_type=d["target_type"],
        target_id=d["target_id"],
        target_version=d["target_version"],
        action_type=d["action_type"],
        decision_state=d["decision_state"],
        risk_level=d["risk_level"],
        created_at=d["created_at"],
        created_by_role=d["created_by_role"],
        created_by_id=d["created_by_id"],
        rationale=d["rationale"],
        approval_decision_id=d.get("approval_decision_id"),
        target_stage=d.get("target_stage"),
        persona_id=d.get("persona_id"),
        capital_pool_id=d.get("capital_pool_id"),
        linked_incident_id=d.get("linked_incident_id"),
        linked_postmortem_id=d.get("linked_postmortem_id"),
        cooldown_started_at=d.get("cooldown_started_at"),
        cooldown_ends_at=d.get("cooldown_ends_at"),
        observation_window_started_at=d.get("observation_window_started_at"),
        observation_window_ends_at=d.get("observation_window_ends_at"),
        superseded_by=d.get("superseded_by"),
        supersedes_decision_id=d.get("supersedes_decision_id"),
        evidence_refs=d.get("evidence_refs", []),
        threshold_snapshots=d.get("threshold_snapshots", []),
        review_chain=d.get("review_chain", []),
        execution_result=d.get("execution_result"),
        metadata=d.get("metadata"),
        is_active=decision.is_active(),
    )


def _not_found(decision_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"EvolutionDecision not found: {decision_id}")


def _tenant_error(exc: Exception) -> HTTPException:
    """A cross-tenant actor is an authority failure, not a validation failure."""
    return HTTPException(status_code=403, detail=str(exc))


def _guard_actor_tenant(decision: EvolutionDecision, tenant_id: Optional[str]) -> str:
    try:
        authoritative_tenant = _authorized_request_tenant(tenant_id)
        return decision.assert_actor_tenant(authoritative_tenant)
    except EvolutionDecisionError as exc:
        raise _tenant_error(exc) from exc


def _guard_read_tenant(decision: EvolutionDecision) -> str:
    """Refuse cross-tenant reads whenever request authentication is active."""

    if _auth_mode() == "disabled":
        return decision.tenant_id
    tenant = _current_tenant()
    if decision.tenant_id != tenant:
        # Do not disclose whether a foreign tenant owns the identifier.
        raise _not_found(decision.decision_id)
    return tenant


def _dispatch_intent_for(decision: EvolutionDecision) -> DispatchIntent | None:
    """Build the durable dispatch intent for an approved decision.

    Returns ``None`` when the controller cannot route the decision at all; an
    unroutable action has no dispatch to make durable.  Planes without a real
    receipt source still get an intent: the outbox is where the refusal to
    auto-execute them is recorded, rather than being silently skipped.
    """
    try:
        boundary = controller.boundary_for(decision, has_active_runtime=False)
    except EvolutionControllerError:
        return None
    plane = str(_enum_value(boundary.execution_plane))
    return DispatchIntent(
        tenant_id=decision.tenant_id,
        decision_id=decision.decision_id,
        action_type=str(_enum_value(decision.action_type)),
        execution_plane=plane,
        boundary_key=boundary.boundary_key,
        target_type=str(_enum_value(decision.target_type)),
        target_id=decision.target_id,
        target_version=decision.target_version,
        target_stage=decision.target_stage,
        approval_decision_id=decision.approval_decision_id,
        command_id=f"dispatch-{decision.decision_id}",
    )


def _outbox_record_response(record) -> DispatchOutboxRecordResponse:
    payload = dict(record.event.payload or {})
    replay_at = dispatch_outbox.replay_available_at(record)
    return DispatchOutboxRecordResponse(
        outbox_id=record.outbox_id,
        tenant_id=str(payload.get("tenant_id") or DEFAULT_TENANT_ID),
        decision_id=str(payload.get("decision_id") or ""),
        action_type=payload.get("action_type"),
        execution_plane=payload.get("execution_plane"),
        status=record.status.value,
        delivery_ready=record.delivery_ready,
        delivery_attempts=record.delivery_attempts,
        redrive_count=record.redrive_count,
        last_error=record.last_error,
        next_attempt_at=_iso_or_none(record.next_attempt_at),
        replay_available_at=_iso_or_none(replay_at),
        created_at=_iso_or_none(record.record.created_at),
        updated_at=_iso_or_none(record.record.updated_at),
        published_at=_iso_or_none(record.record.published_at),
    )


def _iso_or_none(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _domain_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _delivery_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _settle_terminal_dispatch_failure(
    decision: EvolutionDecision,
    *,
    downstream_kind: str,
    downstream_ref_id: str,
    downstream_status: str | None,
    detail: str | None,
) -> None:
    """Preserve approval while durably converging a real downstream failure."""
    intent = _dispatch_intent_for(decision)
    if intent is None:
        raise _delivery_conflict(
            f"failed downstream receipt for {decision.decision_id} has no dispatch boundary"
        )

    reason = (
        "downstream terminal failure: "
        f"{detail or f'{downstream_kind} {downstream_ref_id} status={downstream_status!r}'}"
    )
    try:
        record = dispatch_outbox.activate(dispatch_outbox.prepare(intent))
        # Compensation is written before the DLQ transition.  A crash between
        # the two leaves an idempotent obligation that a retry can finish;
        # reversing the order could strand a terminal DLQ without compensation.
        compensation_ledger.record(
            tenant_id=decision.tenant_id,
            decision_id=decision.decision_id,
            outbox_id=record.outbox_id,
            reason=reason,
            downstream_kind=downstream_kind,
            downstream_ref_id=downstream_ref_id,
        )
        _, completed = dispatch_outbox.dead_letter_terminal_failure(record, reason)
    except EvolutionDispatchError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"failed to durably settle downstream failure for "
                f"{decision.decision_id}: {exc}"
            ),
        ) from exc

    log.warning(
        "evolution.dispatch_failed decision_id=%s tenant=%s downstream=%s:%s "
        "status=%s outbox_id=%s outbox_status=%s",
        decision.decision_id,
        decision.tenant_id,
        downstream_kind,
        downstream_ref_id,
        downstream_status,
        record.outbox_id,
        completed.status.value,
    )
    raise _delivery_conflict(
        f"{downstream_kind} {downstream_ref_id} reported terminal "
        f"status={downstream_status!r}; decision remains approved, dispatch is "
        "dead-lettered, and compensation is required"
    )


def _proposal_request_payload(body: ProposeRequest) -> Dict[str, Any]:
    """Return a stable request payload without its recursive delivery wrapper."""
    if hasattr(body, "model_dump"):
        return body.model_dump(mode="json", exclude={"delivery_event"})
    return body.dict(exclude={"delivery_event"})  # pragma: no cover - pydantic v1


def _validated_delivery_event(body: ProposeRequest) -> Dict[str, Any] | None:
    raw_event = body.delivery_event
    if raw_event is None:
        return None
    try:
        event = EventEnvelope.from_dict(raw_event)
    except (FoundationValidationError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid delivery_event envelope: {exc}",
        ) from exc

    if event.event_type != "postmortem.published":
        raise HTTPException(
            status_code=422,
            detail="delivery_event.event_type must be 'postmortem.published'",
        )
    if event.aggregate_type != "postmortem":
        raise HTTPException(
            status_code=422,
            detail="delivery_event.aggregate_type must be 'postmortem'",
        )
    if event.producer_service != "postmortem-svc":
        raise HTTPException(
            status_code=422,
            detail="delivery_event.producer_service must be 'postmortem-svc'",
        )

    payload = dict(event.payload)
    embedded_proposal = payload.get("proposal")
    if not isinstance(embedded_proposal, Mapping):
        raise HTTPException(
            status_code=422,
            detail="delivery_event.payload.proposal must be an object",
        )
    if embedded_proposal.get("delivery_event") is not None:
        raise HTTPException(
            status_code=422,
            detail="delivery_event.payload.proposal cannot contain delivery_event",
        )
    try:
        embedded_request = ProposeRequest(**dict(embedded_proposal))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid delivery_event proposal snapshot: {exc}",
        ) from exc

    expected_proposal = _proposal_request_payload(body)
    embedded_payload = _proposal_request_payload(embedded_request)
    if sha256_checksum(embedded_payload) != sha256_checksum(expected_proposal):
        raise _delivery_conflict(
            "delivery_event proposal snapshot diverges from the submitted proposal"
        )

    linked_postmortem_id = str(body.linked_postmortem_id or "").strip()
    linked_incident_id = str(body.linked_incident_id or "").strip()
    if not linked_postmortem_id or not linked_incident_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "postmortem.published delivery requires linked_postmortem_id "
                "and linked_incident_id"
            ),
        )
    if event.aggregate_id != linked_postmortem_id:
        raise _delivery_conflict(
            "delivery_event.aggregate_id does not match linked_postmortem_id"
        )
    if str(payload.get("postmortem_id") or "") != linked_postmortem_id:
        raise _delivery_conflict(
            "delivery_event.payload.postmortem_id does not match linked_postmortem_id"
        )
    if str(payload.get("decision_id") or "") != body.decision_id:
        raise _delivery_conflict(
            "delivery_event.payload.decision_id does not match decision_id"
        )
    if payload.get("incident_id") is not None and str(payload["incident_id"]) != linked_incident_id:
        raise _delivery_conflict(
            "delivery_event.payload.incident_id does not match linked_incident_id"
        )

    postmortem_snapshot = payload.get("postmortem")
    if not isinstance(postmortem_snapshot, Mapping):
        raise HTTPException(
            status_code=422,
            detail="delivery_event.payload.postmortem snapshot is required",
        )
    if str(postmortem_snapshot.get("postmortem_id") or "") != linked_postmortem_id:
        raise _delivery_conflict(
            "delivery_event postmortem snapshot does not match linked_postmortem_id"
        )
    if str(postmortem_snapshot.get("incident_id") or "") != linked_incident_id:
        raise _delivery_conflict(
            "delivery_event postmortem snapshot does not match linked_incident_id"
        )
    if postmortem_snapshot.get("status") != "published":
        raise HTTPException(
            status_code=422,
            detail="delivery_event postmortem snapshot must be published",
        )
    if not postmortem_snapshot.get("published_at"):
        raise HTTPException(
            status_code=422,
            detail="delivery_event published postmortem snapshot requires published_at",
        )
    if str(postmortem_snapshot.get("published_event_id") or "") != event.event_id:
        raise _delivery_conflict(
            "delivery_event postmortem published_event_id does not match event_id"
        )

    incident_snapshot = payload.get("incident")
    if not isinstance(incident_snapshot, Mapping):
        raise HTTPException(
            status_code=422,
            detail="delivery_event.payload.incident snapshot is required",
        )
    if str(incident_snapshot.get("incident_id") or "") != linked_incident_id:
        raise _delivery_conflict(
            "delivery_event incident snapshot does not match linked_incident_id"
        )

    semantic_event = {
        "schema_version": event.schema_version,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "sequence_no": event.sequence_no,
        "idempotency_key": event.idempotency_key,
        "producer_service": event.producer_service,
        "schema_ref": event.schema_ref,
        "payload": payload,
    }
    return {
        "event": event,
        "event_fingerprint": sha256_checksum(event.to_dict()),
        "semantic_event_fingerprint": sha256_checksum(semantic_event),
        "proposal_fingerprint": sha256_checksum(expected_proposal),
    }


_IMMUTABLE_PROPOSAL_FIELDS = (
    "decision_id",
    "tenant_id",
    "target_type",
    "target_id",
    "target_version",
    "action_type",
    "risk_level",
    "created_by_role",
    "created_by_id",
    "rationale",
    "evidence_refs",
    "threshold_snapshots",
    "linked_postmortem_id",
    "linked_incident_id",
    "capital_pool_id",
    "persona_id",
    "target_stage",
    "metadata",
)


def _immutable_decision_fingerprint(decision: EvolutionDecision) -> str:
    payload = decision.to_dict()
    return sha256_checksum(
        {field: payload.get(field) for field in _IMMUTABLE_PROPOSAL_FIELDS}
    )


def _build_proposed_decision(
    body: ProposeRequest,
    *,
    require_local_postmortem: bool,
) -> EvolutionDecision:
    try:
        evidence_refs = [
            EvidenceRef(
                ref_type=EvidenceRefType(ref.ref_type),
                ref_id=ref.ref_id,
                storage_ref=ref.storage_ref or {},
                note=ref.note,
            )
            for ref in body.evidence_refs
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid evidence ref_type: {exc}") from exc
    threshold_snapshots = [
        ThresholdSnapshot(
            policy_source=snap.policy_source,
            signal_type=snap.signal_type,
            metric_name=snap.metric_name,
            comparator=snap.comparator,
            observed_value=snap.observed_value,
            threshold_value=snap.threshold_value,
            window=snap.window,
            breached=snap.breached,
            note=snap.note,
        )
        for snap in body.threshold_snapshots
    ]

    if require_local_postmortem and body.linked_postmortem_id:
        if incident_store.get_postmortem(body.linked_postmortem_id) is None:
            raise HTTPException(
                status_code=422,
                detail=f"linked_postmortem_id references unknown Postmortem: {body.linked_postmortem_id}",
            )
    try:
        decision = EvolutionDecision.create_proposed(
            decision_id=body.decision_id,
            target_type=body.target_type,
            target_id=body.target_id,
            target_version=body.target_version,
            action_type=body.action_type,
            rationale=body.rationale,
            created_by_id=body.created_by_id,
            created_by_role=body.created_by_role,
            risk_level=body.risk_level,
            evidence_refs=evidence_refs,
            threshold_snapshots=threshold_snapshots,
            linked_postmortem_id=body.linked_postmortem_id,
            linked_incident_id=body.linked_incident_id,
            capital_pool_id=body.capital_pool_id,
            persona_id=body.persona_id,
            target_stage=body.target_stage,
            metadata=body.metadata,
            tenant_id=_authorized_request_tenant(body.tenant_id),
        )
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    errors = validate_evolution_decision(decision)
    if errors:
        raise _domain_error(EvolutionDecisionError(f"Invalid EvolutionDecision: {errors}"))
    return decision


def _store_proposed_decision(
    decision: EvolutionDecision,
    *,
    link_local_postmortem: bool = True,
) -> None:
    try:
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    if link_local_postmortem and decision.linked_postmortem_id:
        try:
            incident_store.link_evolution_decision(
                decision.linked_postmortem_id,
                decision.decision_id,
            )
        except IncidentError as exc:
            log.warning(
                "evolution.propose: could not back-link postmortem %s → decision %s: %s",
                decision.linked_postmortem_id,
                decision.decision_id,
                exc,
            )
    log.info(
        "evolution.proposed decision_id=%s action=%s",
        decision.decision_id,
        decision.action_type,
    )


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _window_state(as_of: datetime, start: datetime, end: datetime) -> str:
    if as_of < start:
        return "not_started"
    if as_of <= end:
        return "open"
    return "elapsed"


def _seconds_until(as_of: datetime, end: datetime) -> int:
    return max(0, int((end - as_of).total_seconds()))


def _seconds_since(start: datetime, as_of: datetime) -> int:
    return max(0, int((as_of - start).total_seconds()))


def _convergence_status(observation_state: str, cooldown_state: str) -> str:
    if observation_state == "not_started":
        return "pending_observation"
    if observation_state == "open":
        return "collecting_observation_evidence"
    if cooldown_state in {"not_started", "open"}:
        return "observation_elapsed_cooldown_active"
    return "eligible_for_next_decision"


def _observation_report_notes(
    *,
    observation_state: str,
    cooldown_state: str,
    active_blocking: bool,
) -> List[str]:
    if observation_state == "not_started":
        return [
            "Execution has been accepted, but the observation clock has not reached the requested report time.",
            "Do not create another same-target structural mutation before the active window opens and closes.",
        ]
    if observation_state == "open":
        return [
            "Observation window is open; collect telemetry, drift, incident, and operator evidence.",
            "Single-active-rule still blocks another same-target structural mutation during the active window.",
        ]
    if active_blocking or cooldown_state == "open":
        return [
            "Observation window has elapsed, but cooldown still blocks another same-target structural mutation.",
            "Only severe escalation paths should bypass normal convergence wait semantics.",
        ]
    return [
        "Observation and cooldown windows have elapsed for this decision.",
        "The target is no longer blocked by this decision's active window.",
    ]


def _build_followthrough_refs(decision: EvolutionDecision) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if decision.execution_result and decision.execution_result.execution_ref_id:
        refs.append(
            {
                "ref_type": "dispatch_command",
                "ref_id": decision.execution_result.execution_ref_id,
                "plane": _enum_value(decision.execution_result.plane),
                "status": _enum_value(decision.execution_result.status),
                "note": decision.execution_result.outcome_summary,
            }
        )
    metadata = decision.metadata or {}
    for item in metadata.get("followthrough_refs", []):
        if isinstance(item, dict):
            refs.append(dict(item))
    return refs


def _incident_storage_ref(object_type: str) -> Dict[str, Any]:
    return {
        "backend": "incident_store",
        "path": os.path.join(INCIDENT_DATA_DIR, "incidents.json"),
        "object_type": object_type,
    }


def _default_action_from_incident(incident: IncidentCase) -> str:
    if incident.severity == "critical" and incident.deployment_stage in {"paper", "canary", "live"}:
        return EvolutionActionType.FREEZE.value
    return EvolutionActionType.FLAG_FOR_REVIEW.value


def _incident_threshold_snapshot(incident: IncidentCase) -> Dict[str, Any]:
    if incident.severity == "critical":
        return {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
            "signal_type": "governance_incident",
            "metric_name": "severity1_incident_count",
            "comparator": "gte",
            "observed_value": 1,
            "threshold_value": 1,
            "window": "active-incident",
            "breached": True,
            "note": "Critical incident opens the high-risk freeze proposal path.",
        }
    return {
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
        "signal_type": "manual_review",
        "metric_name": "incident_postmortem_followup_required",
        "comparator": "gte",
        "observed_value": 1,
        "threshold_value": 1,
        "window": "incident-followup",
        "breached": True,
        "note": "Incident/postmortem evidence requires explicit evolution review.",
    }


def _incident_evidence_refs(
    incident: IncidentCase,
    postmortem: Postmortem | None,
) -> List[Dict[str, Any]]:
    refs = [
        {
            "ref_type": EvidenceRefType.MANUAL_REVIEW_TICKET.value,
            "ref_id": incident.incident_id,
            "storage_ref": _incident_storage_ref("IncidentCase"),
            "note": "IncidentCase evidence snapshot for derived EvolutionDecision proposal.",
        }
    ]
    if postmortem is not None:
        refs.append(
            {
                "ref_type": EvidenceRefType.MANUAL_REVIEW_TICKET.value,
                "ref_id": postmortem.postmortem_id,
                "storage_ref": _incident_storage_ref("Postmortem"),
                "note": "Postmortem findings linked to the derived EvolutionDecision proposal.",
            }
        )
    return refs


def _default_incident_rationale(
    incident: IncidentCase,
    postmortem: Postmortem | None,
    action_type: str,
) -> str:
    source = (
        f"postmortem {postmortem.postmortem_id} for incident {incident.incident_id}"
        if postmortem is not None
        else f"incident {incident.incident_id}"
    )
    return (
        f"Create a governed {action_type} proposal from {source}. "
        "The proposal is review-gated and does not mutate runtime, broker, or capital-binding state."
    )


def _postmortem_for_incident_request(
    incident: IncidentCase,
    postmortem_id: str | None,
) -> Postmortem | None:
    if postmortem_id:
        postmortem = incident_store.get_postmortem(postmortem_id)
        if postmortem is None:
            raise HTTPException(
                status_code=404,
                detail=f"Postmortem not found: {postmortem_id}",
            )
        if postmortem.incident_id != incident.incident_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Postmortem {postmortem_id} belongs to incident "
                    f"{postmortem.incident_id}, not {incident.incident_id}"
                ),
            )
        return postmortem
    return incident_store.find_postmortem_for_incident(incident.incident_id)


def _proposal_request_from_incident(body: ProposeFromIncidentRequest) -> ProposeRequest:
    incident = incident_store.get_incident(body.incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404,
            detail=f"IncidentCase not found: {body.incident_id}",
        )
    postmortem = _postmortem_for_incident_request(incident, body.postmortem_id)
    action_type = body.action_type or _default_action_from_incident(incident)
    target_stage = body.target_stage if body.target_stage is not None else incident.deployment_stage
    metadata = dict(body.metadata or {})
    metadata.update(
        {
            "task_id": "MGMT-EVO-002",
            "source": "incident_postmortem",
            "source_incident_id": incident.incident_id,
            "source_postmortem_id": postmortem.postmortem_id if postmortem else None,
            "source_trace_id": incident.trace_id,
            "binding_id": incident.binding_id,
            "deployment_plan_id": incident.deployment_plan_id,
            "persona_capital_binding_id": incident.persona_capital_binding_id,
            "runtime_id": incident.runtime_id,
            "deployment_stage_snapshot": incident.deployment_stage,
            "has_active_runtime": body.has_active_runtime,
            "proposal_only": True,
            "live_mutation_allowed": False,
            "runtime_binding_mutation_allowed": False,
            "broker_order_allowed": False,
            "capital_binding_mutation_allowed": False,
        }
    )
    return ProposeRequest(
        decision_id=body.decision_id,
        target_type=body.target_type,
        target_id=body.target_id or incident.artifact_id,
        target_version=body.target_version or incident.artifact_version,
        action_type=action_type,
        rationale=body.rationale or _default_incident_rationale(incident, postmortem, action_type),
        created_by_id=body.created_by_id,
        created_by_role=body.created_by_role,
        target_stage=target_stage,
        capital_pool_id=incident.capital_pool_id,
        linked_incident_id=incident.incident_id,
        linked_postmortem_id=postmortem.postmortem_id if postmortem else None,
        evidence_refs=_incident_evidence_refs(incident, postmortem),
        threshold_snapshots=[_incident_threshold_snapshot(incident)],
        metadata=metadata,
    )


def _find_postmortem_bridge_decision(
    *,
    postmortem: Postmortem,
    bridge_key: str,
    decision_id: str,
    incident: IncidentCase,
) -> EvolutionDecision | None:
    target_type = "candidate_artifact"
    target_id = postmortem.artifact_id
    from services.evolution.postmortem_bridge import _incident_cluster_key
    cluster = _incident_cluster_key(postmortem.to_dict(), incident.to_dict())

    def validate_decision(dec: EvolutionDecision) -> bool:
        meta = dec.metadata or {}
        if not (
            meta.get("postmortem_bridge_key") == bridge_key
            and dec.target_type == target_type
            and dec.target_id == target_id
            and meta.get("incident_cluster_id") == cluster
        ):
            return False

        if dec.linked_postmortem_id == postmortem.postmortem_id:
            return True

        if dec.linked_postmortem_id:
            other_pm = incident_store.get_postmortem(dec.linked_postmortem_id)
            if other_pm is not None:
                other_inc = incident_store.get_incident(other_pm.incident_id)
                if other_inc is not None:
                    other_cluster = _incident_cluster_key(other_pm.to_dict(), other_inc.to_dict())
                    if other_cluster == cluster:
                        return True
        return False

    if postmortem.linked_evolution_decision_id:
        linked = store.get(postmortem.linked_evolution_decision_id)
        if linked is not None and validate_decision(linked):
            return linked
    direct = store.get(decision_id)
    if direct is not None and validate_decision(direct):
        return direct
    for decision in store.list_all():
        if validate_decision(decision):
            return decision
    return None


def _link_postmortem_to_decision(postmortem_id: str, decision_id: str) -> None:
    try:
        current = incident_store.get_postmortem(postmortem_id)
        if current is not None and current.linked_evolution_decision_id != decision_id:
            incident_store.link_evolution_decision(postmortem_id, decision_id)
    except IncidentError as exc:
        log.warning(
            "evolution.postmortem_bridge: could not back-link postmortem %s -> decision %s: %s",
            postmortem_id,
            decision_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "evolution"}


# --- Propose -----------------------------------------------------------------

@app.post("/api/evolution/proposals", status_code=201, response_model=DecisionResponse)
def propose(body: ProposeRequest, response: Response):
    """
    Create a new EvolutionDecision in the ``proposed`` state.

    Enforcement
    -----------
    - ``created_by_role`` must be a valid proposer role
      (evolution_controller or operator).
    - At least one evidence link (evidence_refs, threshold_snapshots,
      linked_incident_id, or linked_postmortem_id) is required.
    - risk_level is inferred from action_type + target_stage if omitted;
      caller-supplied value must match the inferred value.
    - Single-active-rule: the target must not already have an active decision.
    """
    delivery = _validated_delivery_event(body)

    # Ordinary operator/controller proposals cannot claim an identity already
    # owned by a decision or durable inbox record.  This check shares the inbox
    # lock with delivery admission, so omitting delivery_event is not a bypass
    # around replay ownership or an opportunity to reset review state.
    if delivery is None:
        with proposal_inbox.locked():
            if proposal_inbox.for_decision_unlocked(body.decision_id):
                raise _delivery_conflict(
                    "evolution decision is bound to a durable delivery event"
                )
            if store.get(body.decision_id) is not None:
                raise _delivery_conflict(
                    "decision_id is already occupied by an evolution decision"
                )
            candidate = _build_proposed_decision(
                body,
                require_local_postmortem=True,
            )
            _store_proposed_decision(candidate)
        return _decision_to_response(candidate)

    candidate = _build_proposed_decision(
        body,
        require_local_postmortem=False,
    )

    event = delivery["event"]
    event_fingerprint = str(delivery["event_fingerprint"])
    semantic_event_fingerprint = str(delivery["semantic_event_fingerprint"])
    proposal_fingerprint = str(delivery["proposal_fingerprint"])
    decision_fingerprint = _immutable_decision_fingerprint(candidate)

    # Serialize inbox classification, decision admission, and receipt writes so
    # concurrent retries cannot both overwrite the canonical decision.
    with proposal_inbox.locked():
        matching_records = proposal_inbox.matching_event_unlocked(event)
        if matching_records:
            for record in matching_records:
                receipt = record.get("receipt")
                if not isinstance(receipt, Mapping):
                    raise _delivery_conflict(
                        "delivery inbox record is malformed for this event identity"
                    )
                same_event_id = str(receipt.get("event_id") or "") == event.event_id
                same_idempotency_key = (
                    str(receipt.get("idempotency_key") or "") == event.idempotency_key
                )
                if same_event_id and record.get("event_fingerprint") != event_fingerprint:
                    raise _delivery_conflict(
                        "delivery_event event_id was replayed with a divergent envelope"
                    )
                if (
                    same_idempotency_key
                    and record.get("semantic_event_fingerprint")
                    != semantic_event_fingerprint
                ):
                    raise _delivery_conflict(
                        "delivery_event idempotency_key was replayed with divergent semantics"
                    )
                if str(record.get("decision_id") or "") != body.decision_id:
                    raise _delivery_conflict(
                        "delivery event identity is already bound to another decision"
                    )
                if record.get("proposal_fingerprint") != proposal_fingerprint:
                    raise _delivery_conflict(
                        "delivery event identity is already bound to another proposal"
                    )
                if record.get("decision_fingerprint") != decision_fingerprint:
                    raise _delivery_conflict(
                        "delivery event identity is already bound to divergent decision fields"
                    )

            existing = store.get(body.decision_id)
            if existing is None:
                raise _delivery_conflict(
                    "delivery receipt exists but its evolution decision is unavailable"
                )
            if _immutable_decision_fingerprint(existing) != decision_fingerprint:
                raise _delivery_conflict(
                    "existing evolution decision diverges from the delivered proposal"
                )
            response.status_code = 200
            return _decision_to_response(existing)

        # Once a decision has an inbox identity, a newly generated unrelated
        # event cannot silently take ownership of that same decision ID.
        if proposal_inbox.for_decision_unlocked(body.decision_id):
            raise _delivery_conflict(
                "evolution decision is already bound to another delivery event"
            )

        existing = store.get(body.decision_id)
        if existing is not None:
            # Crash recovery: the decision write can precede the receipt write.
            # Accept only an immutable exact match and never replace its current
            # review/approval/execution state.
            if _immutable_decision_fingerprint(existing) != decision_fingerprint:
                raise _delivery_conflict(
                    "decision_id is already occupied by a divergent evolution decision"
                )
            admitted = existing
            response.status_code = 200
            receipt_note = "Recovered delivery receipt for an existing matching decision"
        else:
            _store_proposed_decision(
                candidate,
                link_local_postmortem=False,
            )
            admitted = candidate
            receipt_note = "Applied postmortem.published proposal delivery"

        proposal_inbox.record_applied_unlocked(
            event=event,
            decision_id=body.decision_id,
            event_fingerprint=event_fingerprint,
            semantic_event_fingerprint=semantic_event_fingerprint,
            proposal_fingerprint=proposal_fingerprint,
            decision_fingerprint=decision_fingerprint,
            notes=receipt_note,
        )
        return _decision_to_response(admitted)


@app.post(
    "/api/evolution/proposals/from-incident",
    status_code=201,
    response_model=DecisionResponse,
)
def propose_from_incident(body: ProposeFromIncidentRequest, response: Response):
    """
    Create a proposed EvolutionDecision from canonical IncidentCase/Postmortem evidence.

    This route derives target lineage from the incident snapshot, links the
    resulting decision back to the incident/postmortem chain, and then reuses
    the normal proposal path. It intentionally stops at ``proposed``: review,
    approval, runtime mitigation, broker orders, and capital-binding writes
    remain separate gated steps.
    """
    return propose(_proposal_request_from_incident(body), response)


@app.post(
    "/api/evolution/proposals/from-postmortem-published",
    status_code=201,
    response_model=DecisionResponse,
)
def propose_from_postmortem_published(
    body: ProposeFromPostmortemPublishedRequest,
    response: Response,
):
    """
    Admit a published Postmortem event as exactly one EvolutionDecision proposal.

    Idempotency is scoped to target type, target artifact id, and incident
    cluster. Duplicate publish events return the existing proposal with HTTP
    200. The created decision remains in ``proposed`` until normal review and
    approval gates advance it.
    """
    postmortem = incident_store.get_postmortem(body.postmortem_id)
    if postmortem is None:
        raise HTTPException(
            status_code=404,
            detail=f"Postmortem not found: {body.postmortem_id}",
        )
    incident = incident_store.get_incident(postmortem.incident_id)
    if incident is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Postmortem {body.postmortem_id!r} references unavailable "
                f"IncidentCase {postmortem.incident_id!r}"
            ),
        )
    try:
        proposal_payload = build_published_postmortem_proposal_request(
            postmortem,
            incident,
            decision_id=body.decision_id,
            publish_event_id=body.publish_event_id,
            created_by_id=body.created_by_id,
            created_by_role=body.created_by_role,
        )
    except PostmortemBridgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metadata = proposal_payload.get("metadata") or {}
    bridge_key = str(metadata.get("postmortem_bridge_key") or "")
    decision_id = str(proposal_payload["decision_id"])

    conflict = store.get(decision_id)
    existing = _find_postmortem_bridge_decision(
        postmortem=postmortem,
        bridge_key=bridge_key,
        decision_id=decision_id,
        incident=incident,
    )
    if existing is not None:
        _link_postmortem_to_decision(postmortem.postmortem_id, existing.decision_id)
        response.status_code = 200
        return _decision_to_response(existing)
    elif conflict is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Decision ID {decision_id} is already occupied by an unrelated decision",
        )

    return propose(ProposeRequest(**proposal_payload), response)


# --- Daily sweep -------------------------------------------------------------

@app.post("/api/evolution/daily-sweep", response_model=DailySweepResponse)
def daily_sweep(body: DailySweepRequest):
    """
    Run one scheduler-safe evolution sweep over incident threshold triggers.

    The sweep is proposal-only: it reads IncidentCase evidence, derives
    EvolutionDecision proposals, and relies on the existing single-active-rule
    to block repeated triggers while cooldown/observation is active.

    Sweep outcomes are recorded in the module-level ``_sweep_state`` so
    ``GET /api/evolution/sweep-status`` and the health metrics can surface
    last success, last failure, and cumulative proposal count without a
    persistent store.
    """
    now_str = _format_rfc3339(datetime.now(timezone.utc))
    try:
        result = run_daily_sweep(
            incident_store=incident_store,
            decision_store=store,
            incident_ids=body.incident_ids or None,
            include_closed=body.include_closed,
            max_incidents=body.max_incidents,
            sweep_id=body.sweep_id,
            evaluator=evaluator,
            tenant_id=_authorized_request_tenant(None),
        )
    except ValueError as exc:
        _sweep_state["last_failure_at"] = now_str
        _sweep_state["last_failure_reason"] = str(exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _sweep_state["last_success_at"] = now_str
    _sweep_state["last_success_proposal_count"] = result.created_decisions
    _sweep_state["total_sweeps_run"] = _sweep_state["total_sweeps_run"] + 1
    _sweep_state["total_proposals_created"] = (
        _sweep_state["total_proposals_created"] + result.created_decisions
    )
    log.info(
        "evolution.daily_sweep sweep_id=%s created=%d blocked=%d scanned=%d",
        body.sweep_id,
        result.created_decisions,
        result.cooldown_blocked,
        result.scanned_incidents,
    )
    return result.to_dict()


@app.get("/api/evolution/sweep-status")
def sweep_status():
    """
    Return the current daily-sweep worker status.

    Reports last success timestamp, last failure timestamp, last success
    proposal count, total sweeps run this process lifetime, and total
    cumulative proposals created.  Values are in-memory only and reset on
    service restart; they are sufficient for operator liveness checks.
    """
    return {
        "last_success_at": _sweep_state["last_success_at"],
        "last_success_proposal_count": _sweep_state["last_success_proposal_count"],
        "last_failure_at": _sweep_state["last_failure_at"],
        "last_failure_reason": _sweep_state["last_failure_reason"],
        "total_sweeps_run": _sweep_state["total_sweeps_run"],
        "total_proposals_created": _sweep_state["total_proposals_created"],
        "scheduler_attach": {
            "route": "POST /api/evolution/daily-sweep",
            "worker_module": "services.evolution.scheduler_worker",
            "compose_service": "evolution-daily-sweep-scheduler",
            "compose_profile": "evolution-daily-sweep-scheduler",
        },
    }


# --- List / filter -----------------------------------------------------------

@app.get("/api/evolution/proposals", response_model=List[DecisionResponse])
def list_proposals(
    target_id: Optional[str] = Query(default=None),
    target_type: Optional[str] = Query(default=None),
    decision_state: Optional[str] = Query(default=None),
    risk_level: Optional[str] = Query(default=None),
    active_only: bool = Query(default=False),
    tenant_id: Optional[str] = Query(default=None),
):
    """
    List EvolutionDecision records with optional filtering.

    Parameters
    ----------
    target_id       : filter by target object ID
    target_type     : filter by target type enum value
    decision_state  : filter by state (proposed, reviewed, approved, …)
    risk_level      : filter by risk level (low, medium, high)
    active_only     : when true, only return decisions whose is_active() == True
    """
    scope = (
        _authorized_request_tenant(tenant_id)
        if tenant_id is not None or _auth_mode() == "token"
        else None
    )
    decisions: List[EvolutionDecision] = [
        decision
        for decision in store.list_all()
        if scope is None or decision.tenant_id == scope
    ]
    if target_id:
        decisions = [d for d in decisions if d.target_id == target_id]
    if target_type:
        decisions = [d for d in decisions if _enum_value(d.target_type) == target_type]
    if decision_state:
        decisions = [d for d in decisions if _enum_value(d.decision_state) == decision_state]
    if risk_level:
        decisions = [d for d in decisions if _enum_value(d.risk_level) == risk_level]
    if active_only:
        decisions = [d for d in decisions if d.is_active()]
    return [_decision_to_response(d) for d in decisions]


# --- Get single --------------------------------------------------------------

@app.get("/api/evolution/proposals/{decision_id}", response_model=DecisionResponse)
def get_proposal(decision_id: str):
    """Retrieve a single EvolutionDecision by its ID."""
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    _guard_read_tenant(decision)
    return _decision_to_response(decision)


# --- Observation report ------------------------------------------------------

@app.get(
    "/api/evolution/proposals/{decision_id}/observation-report",
    response_model=ObservationWindowReportResponse,
)
def get_observation_report(
    decision_id: str,
    as_of: Optional[str] = Query(default=None),
):
    """
    Produce the post-execution observation-window report for one decision.

    The report is read-only evidence for the Learn/Evolve leg: it makes the
    parent decision's observation window, cooldown window, active blocking
    status, execution reference, and evidence links inspectable without opening
    a new EvolutionDecision or follow-through command.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    _guard_read_tenant(decision)
    if EvolutionDecisionState(decision.decision_state) != EvolutionDecisionState.EXECUTED:
        raise HTTPException(
            status_code=422,
            detail="Observation window report requires an executed EvolutionDecision.",
        )

    report_time = _parse_rfc3339(as_of) if as_of else datetime.now(timezone.utc)
    if report_time is None:
        raise HTTPException(status_code=400, detail=f"Invalid as_of timestamp: {as_of!r}")

    cooldown_start = _parse_rfc3339(decision.cooldown_started_at)
    cooldown_end = _parse_rfc3339(decision.cooldown_ends_at)
    observation_start = _parse_rfc3339(decision.observation_window_started_at)
    observation_end = _parse_rfc3339(decision.observation_window_ends_at)
    if not all((cooldown_start, cooldown_end, observation_start, observation_end)):
        raise HTTPException(
            status_code=422,
            detail="Executed EvolutionDecision is missing cooldown or observation window timestamps.",
        )

    assert cooldown_start is not None
    assert cooldown_end is not None
    assert observation_start is not None
    assert observation_end is not None
    active_until = max(cooldown_end, observation_end)
    observation_state = _window_state(report_time, observation_start, observation_end)
    cooldown_state = _window_state(report_time, cooldown_start, cooldown_end)
    active_blocking = report_time <= active_until

    execution = (
        decision.execution_result.to_dict()
        if decision.execution_result is not None
        else {}
    )
    return ObservationWindowReportResponse(
        decision_id=decision.decision_id,
        target_type=str(_enum_value(decision.target_type)),
        target_id=decision.target_id,
        target_version=decision.target_version,
        target_stage=decision.target_stage,
        action_type=str(_enum_value(decision.action_type)),
        risk_level=str(_enum_value(decision.risk_level)),
        decision_state=str(_enum_value(decision.decision_state)),
        report_generated_at=_format_rfc3339(report_time),
        observation_window_started_at=decision.observation_window_started_at or "",
        observation_window_ends_at=decision.observation_window_ends_at or "",
        cooldown_started_at=decision.cooldown_started_at or "",
        cooldown_ends_at=decision.cooldown_ends_at or "",
        observation_state=observation_state,
        cooldown_state=cooldown_state,
        active_until=_format_rfc3339(active_until),
        active_blocking=active_blocking,
        seconds_since_observation_start=_seconds_since(observation_start, report_time),
        seconds_until_observation_end=_seconds_until(report_time, observation_end),
        seconds_until_cooldown_end=_seconds_until(report_time, cooldown_end),
        convergence_status=_convergence_status(observation_state, cooldown_state),
        approval_decision_id=decision.approval_decision_id,
        linked_incident_id=decision.linked_incident_id,
        linked_postmortem_id=decision.linked_postmortem_id,
        execution=execution,
        followthrough_refs=_build_followthrough_refs(decision),
        evidence_refs=[
            ref.to_dict() if hasattr(ref, "to_dict") else ref
            for ref in decision.evidence_refs
        ],
        threshold_snapshots=[
            snap.to_dict() if hasattr(snap, "to_dict") else snap
            for snap in decision.threshold_snapshots
        ],
        review_chain=[
            step.to_dict() if hasattr(step, "to_dict") else step
            for step in decision.review_chain
        ],
        policy_refs=[
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2-§5.4",
            "services/control-plane/governance/evolution_decision.contract.md §10",
            "docs/04/pantheon_sa_supplemental_2026-05-15/SA_management_console_multi_persona_ooda.md §6.5",
        ],
        notes=_observation_report_notes(
            observation_state=observation_state,
            cooldown_state=cooldown_state,
            active_blocking=active_blocking,
        ),
    )


@app.post(
    "/api/evolution/proposals/{decision_id}/learn-feedback",
    response_model=LearnFeedbackWritebackResponse,
)
def get_learn_feedback_writeback(
    decision_id: str,
    body: LearnFeedbackWritebackRequest,
):
    """
    Produce the memory-service Learn feedback writeback payload for an executed or approved decision.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    _guard_read_tenant(decision)
    allowed_states = {
        EvolutionDecisionState.APPROVED,
        EvolutionDecisionState.EXECUTED,
    }
    if EvolutionDecisionState(decision.decision_state) not in allowed_states:
        raise HTTPException(
            status_code=422,
            detail="Learn feedback writeback requires an approved or executed EvolutionDecision.",
        )
    try:
        payload = build_evolution_learn_feedback_writeback(
            decision.to_dict(),
            sponsor_persona_id=body.sponsor_persona_id,
            contributing_persona_ids=body.contributing_persona_ids,
            summary=body.summary,
            contributor_feedback=body.contributor_feedback,
            proposal_ids=body.proposal_ids,
            proposal_ids_by_persona=body.proposal_ids_by_persona,
        )
    except PostmortemBridgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LearnFeedbackWritebackResponse(**payload)


# --- Review ------------------------------------------------------------------


@app.post("/api/evolution/proposals/{decision_id}/review", response_model=DecisionResponse)
def review_proposal(decision_id: str, body: ReviewRequest):
    """
    Advance an EvolutionDecision from ``proposed`` to ``reviewed``.

    Enforcement
    -----------
    - actor_role must be in the review-owner matrix for the decision's risk level.
    - Decision must currently be in ``proposed`` state.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    try:
        decision.mark_reviewed(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            approval_decision_id=body.approval_decision_id,
            note=body.note,
            actor_tenant_id=tenant,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    log.info("evolution.reviewed decision_id=%s actor=%s", decision_id, body.actor_id)
    return _decision_to_response(decision)


# --- Approve -----------------------------------------------------------------

@app.post("/api/evolution/proposals/{decision_id}/approve", response_model=DecisionResponse)
def approve_proposal(decision_id: str, body: ApproveRequest):
    """
    Advance an EvolutionDecision from ``reviewed`` to ``approved``.

    Enforcement
    -----------
    - actor_role must be in the approval-owner matrix for the decision's risk level.
    - Decision must currently be in ``reviewed`` state.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    try:
        decision.approve(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            approval_decision_id=body.approval_decision_id,
            note=body.note,
            actor_tenant_id=tenant,
        )
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc

    # Prepare the dispatch intent *before* the approval is durable, then
    # activate it after.  A crash between the two leaves an inert prepared
    # record that the dispatch worker's reconcile pass activates, so an
    # approved decision can never end up with no dispatch on record.  Preparing
    # after the commit would leave exactly that hole.
    intent = _dispatch_intent_for(decision)
    prepared = None
    if intent is not None:
        try:
            prepared = dispatch_outbox.prepare(intent)
        except (EvolutionDispatchError, ValueError) as exc:
            raise _delivery_conflict(
                f"could not prepare a durable dispatch intent for {decision_id}: {exc}"
            ) from exc

    try:
        store.put(decision)
    except EvolutionDecisionError as exc:
        # The approval did not commit, so the intent must not become
        # deliverable.  Discard it only while it is still the exact inert
        # snapshot we wrote; anything else means a concurrent winner owns it.
        if prepared is not None:
            try:
                dispatch_outbox.store.discard_prepared(prepared)
            except Exception:  # noqa: BLE001
                log.exception(
                    "AUDIT: could not discard prepared dispatch intent %s after a failed approval",
                    prepared.outbox_id,
                )
        raise _domain_error(exc) from exc

    if prepared is not None:
        try:
            dispatch_outbox.activate(prepared)
        except Exception:  # noqa: BLE001
            # The approval is durable and the intent is durable; only the
            # activation is missing. Reconcile picks it up on the next tick.
            log.exception(
                "AUDIT: prepared dispatch intent %s awaits reconcile activation",
                prepared.outbox_id,
            )

    log.info(
        "evolution.approved decision_id=%s tenant=%s actor=%s dispatch_intent=%s",
        decision_id,
        decision.tenant_id,
        body.actor_id,
        prepared.outbox_id if prepared is not None else None,
    )
    return _decision_to_response(decision)


# --- Reject ------------------------------------------------------------------

@app.post("/api/evolution/proposals/{decision_id}/reject", response_model=DecisionResponse)
def reject_proposal(decision_id: str, body: RejectRequest):
    """
    Advance an EvolutionDecision from ``reviewed`` to ``rejected``.

    Enforcement
    -----------
    - actor_role must be in the review or approval matrix for the risk level.
    - Decision must currently be in ``reviewed`` state.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    try:
        decision.reject(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            approval_decision_id=body.approval_decision_id,
            note=body.note,
            actor_tenant_id=tenant,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    log.info("evolution.rejected decision_id=%s actor=%s", decision_id, body.actor_id)
    return _decision_to_response(decision)


# --- Cancel ------------------------------------------------------------------

@app.post("/api/evolution/proposals/{decision_id}/cancel", response_model=DecisionResponse)
def cancel_proposal(decision_id: str, body: CancelRequest):
    """
    Cancel an EvolutionDecision from ``proposed``, ``reviewed``, or ``approved``.

    Enforcement
    -----------
    - actor_role must be in the cancel-roles set (operator, risk_owner, governance_committee).
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    try:
        decision.cancel(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            note=body.note,
            actor_tenant_id=tenant,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    log.info("evolution.canceled decision_id=%s actor=%s", decision_id, body.actor_id)
    return _decision_to_response(decision)


# The former ``_trigger_research_retrain`` fire-and-forget thread was removed
# here. It POSTed a research task/run from inside the execute request and
# ignored the outcome, so the decision was already recorded as executed while
# the research run's real result was never read back. That work now goes
# through the durable dispatch outbox
# (``services/evolution/dispatch_outbox.py``) and only converges the decision
# on a terminal receipt read back from the orchestrator.


# --- Execute -----------------------------------------------------------------

@app.post("/api/evolution/proposals/{decision_id}/execute", response_model=DecisionResponse)
def execute_proposal(decision_id: str, body: ExecuteRequest):
    """
    Execute an approved EvolutionDecision on a real downstream terminal receipt.

    This route:
    1. Requires an ``execution_receipt`` naming the downstream record that did
       the work, and **re-reads that record itself**.  A caller-asserted status
       is not evidence; the readback is.
    2. Calls ``EvolutionController.dispatch_approved()`` for the action
       boundary, cooldown/observation windows, and follow-through commands.
    3. Moves the decision to ``executed`` carrying the terminal downstream
       status and the downstream reference it was read back from.

    Enforcement
    -----------
    - Decision must be in ``approved`` state.
    - Actor tenant must match the decision's tenant.
    - actor_role must be in the execution-roles set.
    - The receipt's plane must have a real receipt source; planes that have no
      automatic downstream are refused rather than approximated.
    - The downstream must report a terminal state.  A ``submitted`` dispatch
      intent is a request, not an outcome, and is rejected by the governance
      object itself.
    - Cooldown and observation-window timestamps come from canonical policy;
      callers cannot override them.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    try:
        freeze_mode = FreezeFollowthroughMode(body.freeze_mode)
    except ValueError as exc:
        valid = [m.value for m in FreezeFollowthroughMode]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid freeze_mode {body.freeze_mode!r}. Must be one of {valid}",
        ) from exc

    if body.execution_receipt is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "execution_receipt is required: an EvolutionDecision may only be executed "
                "on a downstream terminal receipt this service can read back. Dispatch the "
                "decision through the durable outbox instead of asserting execution."
            ),
        )

    try:
        outcome = controller.dispatch_approved(
            decision,
            has_active_runtime=body.has_active_runtime,
            active_binding_id=body.active_binding_id,
            freeze_mode=freeze_mode,
            rollback_action_type=body.rollback_action_type,
            fallback_artifact_id=body.fallback_artifact_id,
            fallback_artifact_version=body.fallback_artifact_version,
            force_stage_freeze=body.force_stage_freeze,
        )
    except (EvolutionDecisionError, EvolutionControllerError) as exc:
        raise _domain_error(exc) from exc

    execution_plane = str(_enum_value(outcome.execution_result.plane))
    try:
        receipt = verify_terminal_receipt(
            receipt_registry,
            execution_plane=execution_plane,
            downstream_kind=body.execution_receipt.downstream_kind,
            downstream_ref_id=body.execution_receipt.downstream_ref_id,
            tenant_id=tenant,
            decision_id=decision.decision_id,
        )
    except DispatchReceiptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if receipt.outcome == OUTCOME_FAILED:
        _settle_terminal_dispatch_failure(
            decision,
            downstream_kind=receipt.downstream_kind,
            downstream_ref_id=str(receipt.downstream_ref_id or ""),
            downstream_status=receipt.downstream_status,
            detail=receipt.detail,
        )

    execution_result = ExecutionResult(
        status=(
            ExecutionStatus.SUCCEEDED
            if receipt.outcome == OUTCOME_SUCCEEDED
            else ExecutionStatus.FAILED
        ),
        plane=execution_plane,
        executed_at=utc_now(),
        execution_ref_id=receipt.downstream_ref_id,
        outcome_summary=(
            f"{receipt.downstream_kind} {receipt.downstream_ref_id} reported terminal "
            f"status={receipt.downstream_status!r}"
        ),
    )

    try:
        decision.execute(
            body.actor_role,
            body.actor_id,
            execution_result,
            cooldown_ends_at=outcome.primary_command.cooldown_ends_at,
            observation_window_ends_at=outcome.primary_command.observation_window_ends_at,
            note=body.note or execution_result.outcome_summary,
            actor_tenant_id=tenant,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc

    log.info(
        "evolution.executed decision_id=%s tenant=%s actor=%s downstream=%s:%s status=%s "
        "cooldown_ends=%s obs_ends=%s",
        decision_id,
        decision.tenant_id,
        body.actor_id,
        receipt.downstream_kind,
        receipt.downstream_ref_id,
        receipt.downstream_status,
        decision.cooldown_ends_at,
        decision.observation_window_ends_at,
    )
    return _decision_to_response(decision)


# --- Boundary query ----------------------------------------------------------

@app.get("/api/evolution/proposals/{decision_id}/boundary", response_model=BoundaryResponse)
def get_boundary(
    decision_id: str,
    has_active_runtime: bool = Query(default=False),
):
    """
    Return the ActionBoundary that governs how this decision should be
    dispatched.  Useful for UI pre-flight checks and audit reporting.

    The boundary describes the execution plane, reviewed/approved owner roles,
    default cooldown window, and any required follow-through commands.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    _guard_read_tenant(decision)
    try:
        boundary = controller.boundary_for(decision, has_active_runtime=has_active_runtime)
    except EvolutionControllerError as exc:
        raise _domain_error(exc) from exc
    return BoundaryResponse(
        boundary_key=boundary.boundary_key,
        execution_plane=str(boundary.execution_plane.value if hasattr(boundary.execution_plane, "value") else boundary.execution_plane),
        threshold_policy_source=boundary.threshold_policy_source,
        reviewed_owner_roles=list(boundary.reviewed_owner_roles),
        approved_owner_roles=list(boundary.approved_owner_roles),
        default_cooldown_days=boundary.default_cooldown_days,
        default_observation_days=boundary.default_observation_days,
        followthrough=list(boundary.followthrough),
        notes=boundary.notes,
    )


# --- Rollback follow-through -------------------------------------------------

@app.post(
    "/api/evolution/proposals/{decision_id}/rollback-followthrough",
    response_model=DecisionResponse,
)
def rollback_followthrough(decision_id: str, body: RollbackFollowthroughRequest):
    """
    Execute the rollback operational follow-through on an approved EvolutionDecision.

    This is a convenience wrapper over the generic execute endpoint that fixes
    ``freeze_mode=rollback`` so callers do not need to know the internal enum.

    The decision must already carry a ``freeze`` action type and be in
    ``approved`` state.  A ``RollbackCommand`` is emitted to the runtime plane;
    the Evolution Decision moves to ``executed``.

    Cooldown / observation windows
    ------------------------------
    Rollback follow-through does NOT open a new evolution cooldown window.
    The parent EvolutionDecision's existing cooldown and observation clocks are
    reused (per ``EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2``).

    Owner / threshold
    -----------------
    - Reviewed owner: inherits from the triggering parent review chain.
    - Approved owner: same parent approval chain; no parallel chain is created.
    - Cooldown: parent decision window (high-risk freeze: 14 days).
    - Execution plane: ``runtime`` (Rollback Controller → Runtime Manager).
    - Policy source: ``ROLLBACK_AND_POSITION_SEMANTICS.md §10`` and
      ``EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1``.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    if not body.active_binding_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "rollback-followthrough requires an active_binding_id; "
                "freeze decisions without an active runtime or binding must use "
                "the governance-only or freeze-stage path instead."
            ),
        )
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    if body.execution_receipt is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "rollback-followthrough requires an execution_receipt naming the runtime "
                "record that carried out the mitigation. Runtime mitigation is executed by "
                "the Rollback Controller / Runtime Manager; this route records its terminal "
                "outcome and must not synthesise one."
            ),
        )
    try:
        outcome = controller.dispatch_approved(
            decision,
            has_active_runtime=True,
            active_binding_id=body.active_binding_id,
            freeze_mode=FreezeFollowthroughMode.ROLLBACK,
            rollback_action_type=body.rollback_action_type,
            fallback_artifact_id=body.fallback_artifact_id,
            fallback_artifact_version=body.fallback_artifact_version,
        )
    except (EvolutionDecisionError, EvolutionControllerError) as exc:
        raise _domain_error(exc) from exc

    execution_plane = str(_enum_value(outcome.execution_result.plane))
    try:
        receipt = verify_terminal_receipt(
            receipt_registry,
            execution_plane=execution_plane,
            downstream_kind=body.execution_receipt.downstream_kind,
            downstream_ref_id=body.execution_receipt.downstream_ref_id,
            tenant_id=tenant,
            decision_id=decision.decision_id,
        )
    except DispatchReceiptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if receipt.outcome == OUTCOME_FAILED:
        _settle_terminal_dispatch_failure(
            decision,
            downstream_kind=receipt.downstream_kind,
            downstream_ref_id=str(receipt.downstream_ref_id or ""),
            downstream_status=receipt.downstream_status,
            detail=receipt.detail,
        )

    try:
        decision.execute(
            body.actor_role,
            body.actor_id,
            ExecutionResult(
                status=(
                    ExecutionStatus.SUCCEEDED
                    if receipt.outcome == OUTCOME_SUCCEEDED
                    else ExecutionStatus.FAILED
                ),
                plane=execution_plane,
                executed_at=utc_now(),
                execution_ref_id=receipt.downstream_ref_id,
                outcome_summary=(
                    f"{receipt.downstream_kind} {receipt.downstream_ref_id} reported terminal "
                    f"status={receipt.downstream_status!r}"
                ),
            ),
            cooldown_ends_at=outcome.primary_command.cooldown_ends_at,
            observation_window_ends_at=outcome.primary_command.observation_window_ends_at,
            note=body.note,
            actor_tenant_id=tenant,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    log.info(
        "evolution.rollback_followthrough decision_id=%s tenant=%s actor=%s downstream=%s:%s",
        decision_id,
        decision.tenant_id,
        body.actor_id,
        receipt.downstream_kind,
        receipt.downstream_ref_id,
    )
    return _decision_to_response(decision)


# --- Redeploy follow-through -------------------------------------------------

@app.post(
    "/api/evolution/proposals/{decision_id}/redeploy-followthrough",
    response_model=DispatchCommandResponse,
)
def redeploy_followthrough(decision_id: str, body: RedeployFollowthroughRequest):
    """
    Request a redeploy follow-through command for an executed EvolutionDecision.

    Redeploy is not an independent ``EvolutionDecision`` — it is the deployment
    follow-through after a retrain / revalidate / revive / freeze-lift has
    already been executed.  This endpoint validates that the parent decision is
    in the ``executed`` state and that the request falls within the active
    observation window, then returns a ``DispatchCommand`` that the deployment
    plane can consume to create a new ``ApprovalDecision`` and ``DeploymentPlan``.

    Owner / threshold
    -----------------
    - ``paper`` stage: ``reviewer_on_duty`` or ``automated_gate``.
    - ``canary`` / ``live`` stages: ``reviewer``, ``risk_owner``, ``operator``.
    - Cooldown: no new evolution cooldown; parent observation window still governs.
    - Execution plane: ``deployment`` (Governance / Promotion plane creates the plan).
    - Policy source: ``PAPER_CANARY_LIVE_POLICY.md §5–§8`` and
      ``EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1``.

    The returned ``DispatchCommand`` carries ``metadata.reviewed_owner_roles``,
    ``metadata.approved_owner_roles``, ``cooldown_ends_at``, and
    ``observation_window_ends_at`` so the deployment plane can enforce the
    appropriate governance gate without reading the policy document directly.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    _guard_read_tenant(decision)
    parent_action = _enum_value(decision.action_type)
    if parent_action not in _REDEPLOY_ELIGIBLE_ACTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Redeploy follow-through is not allowed after a '{parent_action}' decision. "
                f"Valid parent action families: {sorted(_REDEPLOY_ELIGIBLE_ACTION_TYPES)}."
            ),
        )
    try:
        command = controller.create_redeploy_followthrough(
            decision,
            artifact_id=body.artifact_id,
            artifact_version=body.artifact_version,
            approval_decision_id=body.approval_decision_id,
            target_stage=body.target_stage,
            requested_at=body.requested_at,
            sponsor_persona_id=body.sponsor_persona_id,
        )
    except EvolutionControllerError as exc:
        raise _domain_error(exc) from exc
    log.info(
        "evolution.redeploy_followthrough decision_id=%s artifact=%s@%s stage=%s",
        decision_id,
        body.artifact_id,
        body.artifact_version,
        body.target_stage,
    )
    d = command.to_dict()
    return DispatchCommandResponse(
        command_id=d["command_id"],
        decision_id=d["decision_id"],
        execution_plane=d["execution_plane"],
        action_type=d["action_type"],
        target_type=d["target_type"],
        target_id=d["target_id"],
        target_version=d["target_version"],
        target_stage=d.get("target_stage"),
        cooldown_ends_at=d["cooldown_ends_at"],
        observation_window_ends_at=d["observation_window_ends_at"],
        metadata=d.get("metadata", {}),
    )


# --- Action-paths routing matrix ---------------------------------------------

# Valid parent action types for a redeploy follow-through per §11.1.
# Freeze is NOT eligible — a freeze governance decision has no deployment
# follow-through unless it was lifted (revive) or replaced by a retrain.
_REDEPLOY_ELIGIBLE_ACTION_TYPES: frozenset[str] = frozenset({
    "retrain",
    "revalidate",
    "revive",
    "observe",
    "require_more_data",
    "flag_for_review",
})

_ACTION_PATHS = [
    {
        "path_key": "freeze_non_live",
        "action_family": "freeze",
        "trigger_source": "§7.3–§7.6: PSI > 0.30, performance degradation, execution drift escalated, or Severity-2",
        "reviewed_owner": "Reviewer, Risk Owner",
        "approved_owner": "Risk Owner",
        "cooldown_days": 7,
        "observation_days": 7,
        "execution_plane": "governance",
        "followthrough": ["deployment.freeze_stage (optional when active paper/canary runtime)"],
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1",
        "notes": "Governance quarantine only; no automatic runtime rollback unless reviewer explicitly requires it.",
    },
    {
        "path_key": "freeze_live_no_active_runtime",
        "action_family": "freeze",
        "trigger_source": "§7.5–§7.6: Severity-1, repeated Severity-2 (30d), unresolved binding/approval mismatch",
        "reviewed_owner": "Governance Committee",
        "approved_owner": "Governance Committee",
        "cooldown_days": 14,
        "observation_days": 14,
        "execution_plane": "governance",
        "followthrough": [],
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1",
        "notes": "High-risk governance freeze; no companion deployment/runtime follow-through because no active runtime exists.",
    },
    {
        "path_key": "freeze_live_active_runtime",
        "action_family": "freeze",
        "trigger_source": "§7.5–§7.6: Severity-1, repeated Severity-2, rollback executed but problem persists, unresolved mismatch",
        "reviewed_owner": "Governance Committee",
        "approved_owner": "Governance Committee",
        "cooldown_days": 14,
        "observation_days": 14,
        "execution_plane": "governance",
        "followthrough": ["deployment.freeze_stage", "runtime.rollback"],
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1",
        "notes": "Freeze is the governance decision. Companion operational path (frozen DeploymentPlan or rollback-followthrough) decided by reviewer/risk-owner based on incident severity.",
    },
    {
        "path_key": "rollback_operational_followthrough",
        "action_family": "rollback",
        "trigger_source": "Approved EvolutionDecision with freeze action + active runtime; incident-driven or postmortem follow-up",
        "reviewed_owner": "Parent review chain (no parallel approval chain created)",
        "approved_owner": "Parent approval chain",
        "cooldown_days": 0,
        "observation_days": 0,
        "execution_plane": "runtime",
        "followthrough": ["runtime.rollback → Runtime Manager creates replacement RuntimeBinding"],
        "policy_source": "ROLLBACK_AND_POSITION_SEMANTICS.md §10; EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1",
        "notes": "Rollback does not open a new evolution cooldown. Uses parent decision cooldown/observation. Action types: replace | pause_then_replace | liquidate_then_replace.",
    },
    {
        "path_key": "research_retrain",
        "action_family": "retrain",
        "trigger_source": "§7.1–§7.4: Sharpe < 50% baseline, drawdown > 1.25x, 3 underperforming windows, PSI breach, label drift, human correction",
        "reviewed_owner": "Reviewer on Duty",
        "approved_owner": "Reviewer on Duty (or automated gate if policy allows)",
        "cooldown_days": 3,
        "observation_days": 7,
        "execution_plane": "research",
        "followthrough": [],
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1; EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2",
        "notes": "Executed means a governed research work item or job was accepted — not that the artifact is redeployed. Redeploy requires a separate deployment follow-through.",
    },
    {
        "path_key": "research_revalidate",
        "action_family": "retrain",
        "trigger_source": "§7.1–§7.4: Model drift or PSI breach requiring revalidation without full retrain",
        "reviewed_owner": "Reviewer on Duty",
        "approved_owner": "Reviewer on Duty (or automated gate if policy allows)",
        "cooldown_days": 3,
        "observation_days": 7,
        "execution_plane": "research",
        "followthrough": [],
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1; EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2",
        "notes": "Revalidation: validates artifact fitness without retraining; same research-plane execution as retrain.",
    },
    {
        "path_key": "redeploy_followthrough",
        "action_family": "redeploy",
        "trigger_source": "Executed retrain/revalidate/revive/freeze-lift; new artifact already passed approval and stage gate",
        "reviewed_owner": "paper: reviewer_on_duty or automated_gate; canary/live: reviewer + risk_owner + operator",
        "approved_owner": "paper: reviewer_on_duty; canary/live: reviewer + risk_owner + operator",
        "cooldown_days": 0,
        "observation_days": 0,
        "execution_plane": "deployment",
        "followthrough": ["deployment: new ApprovalDecision + DeploymentPlan created by Governance/Promotion plane"],
        "policy_source": "PAPER_CANARY_LIVE_POLICY.md §5–§8; EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1",
        "notes": "Redeploy is not an independent EvolutionDecision. It must occur within the parent executed decision's observation window and still pass the stage deployment gate.",
    },
]


# --- Durable dispatch outbox -------------------------------------------------

@app.post(
    "/api/evolution/proposals/{decision_id}/dispatch",
    response_model=DispatchOutboxRecordResponse,
)
def dispatch_proposal(decision_id: str, tenant_id: Optional[str] = Query(default=None)):
    """Ensure an approved decision has a durable, deliverable dispatch intent.

    Idempotent by construction: the intent's id is derived from
    ``(tenant, decision)``, so a duplicate trigger returns the existing record
    rather than dispatching the approved action a second time.  This is also the
    manual recovery hand-crank for a decision approved before the outbox
    existed, or whose activation was lost.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    _guard_actor_tenant(decision, tenant_id)

    state = str(_enum_value(decision.decision_state))
    if state not in {"approved", "executed"}:
        raise HTTPException(
            status_code=409,
            detail=f"only an approved EvolutionDecision may be dispatched; {decision_id} is {state}",
        )

    intent = _dispatch_intent_for(decision)
    if intent is None:
        raise HTTPException(
            status_code=422,
            detail=f"no action boundary routes {decision_id}; there is no dispatch to make durable",
        )
    try:
        prepared = dispatch_outbox.prepare(intent)
        record = dispatch_outbox.activate(prepared)
    except (EvolutionDispatchError, ValueError) as exc:
        raise _delivery_conflict(str(exc)) from exc
    return _outbox_record_response(record)


@app.get("/api/evolution/dispatch-outbox", response_model=DispatchOutboxListResponse)
def list_dispatch_outbox(
    tenant_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    """List durable dispatch records, tenant-scoped when a tenant is supplied."""
    scope = (
        _authorized_request_tenant(tenant_id)
        if tenant_id is not None or _auth_mode() == "token"
        else None
    )
    records = dispatch_outbox.list_all(tenant_id=scope)
    if status:
        wanted = status.strip().lower()
        records = [item for item in records if item.status.value == wanted]
    return DispatchOutboxListResponse(
        tenant_id=scope,
        records=[_outbox_record_response(item) for item in records],
    )


@app.post(
    "/api/evolution/dispatch-outbox/{outbox_id}/replay",
    response_model=DispatchOutboxRecordResponse,
)
def replay_dispatch(outbox_id: str, body: DispatchReplayRequest):
    """Replay a dead-lettered dispatch once its replay cooldown has elapsed.

    The cooldown is derived from the record's own durable timestamps, so a
    restart or a duplicate replay trigger cannot shorten it.
    """
    record = dispatch_outbox.get_by_id(outbox_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"dispatch record not found: {outbox_id}")

    record_tenant = str(dict(record.event.payload or {}).get("tenant_id") or DEFAULT_TENANT_ID)
    actor_tenant = _authorized_request_tenant(body.tenant_id)
    if actor_tenant != record_tenant:
        raise HTTPException(
            status_code=403,
            detail=(
                f"actor tenant '{actor_tenant}' is not authorized to "
                f"replay a dispatch owned by tenant '{record_tenant}'"
            ),
        )
    try:
        replayed = dispatch_outbox.replay(outbox_id, actor=body.actor_id, note=body.note)
    except EvolutionDispatchError as exc:
        raise _delivery_conflict(str(exc)) from exc
    log.info(
        "evolution.dispatch_replayed outbox_id=%s tenant=%s actor=%s",
        outbox_id,
        record_tenant,
        body.actor_id,
    )
    return _outbox_record_response(replayed)


@app.get("/api/evolution/compensations", response_model=CompensationListResponse)
def list_compensations(tenant_id: Optional[str] = Query(default=None)):
    """List durable compensation obligations left by failed dispatches."""
    scope = (
        _authorized_request_tenant(tenant_id)
        if tenant_id is not None or _auth_mode() == "token"
        else None
    )
    return CompensationListResponse(
        tenant_id=scope,
        compensations=[
            CompensationResponse(**item)
            for item in compensation_ledger.list_all(tenant_id=scope)
        ],
    )


@app.post(
    "/api/evolution/compensations/{decision_id}/resolve",
    response_model=CompensationResponse,
)
def resolve_compensation(decision_id: str, body: CompensationResolveRequest):
    """Record that a compensation obligation has been discharged."""
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    tenant = _guard_actor_tenant(decision, body.tenant_id)
    try:
        resolved = compensation_ledger.resolve(
            tenant_id=tenant,
            decision_id=decision_id,
            actor_id=body.actor_id,
            note=body.note,
        )
    except EvolutionDispatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CompensationResponse(**resolved)


# --- Evolution input coverage ------------------------------------------------

@app.get("/api/evolution/input-coverage")
def get_input_coverage():
    """Report whether every monitored artifact has usable Evolution inputs.

    Reads the same live threshold and baseline config the sweep worker uses and
    the same telemetry runtime summaries, so this answer cannot drift from what
    the sweep would actually do.  A telemetry read failure is reported as an
    unavailable coverage answer, never as complete coverage.
    """
    thresholds = load_thresholds()
    baselines = load_baselines()
    try:
        summaries = default_fetch_summaries(
            TELEMETRY_API_URL, timeout=DOWNSTREAM_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"telemetry runtime summaries are unavailable; coverage is unknown: {exc}",
        ) from exc

    coverage = assess_input_coverage(summaries, thresholds, baselines=baselines)
    coverage["thresholds_loaded"] = len(thresholds)
    coverage["approved_baseline_artifacts"] = sorted(baselines)
    coverage["supported_dispatch_planes"] = supported_planes(receipt_registry)
    return coverage


@app.get("/api/evolution/action-paths", response_model=ActionPathsResponse)
def list_action_paths():
    """
    Return the canonical operational evolution routing matrix.

    Each entry documents the trigger source, review/approval owner, cooldown,
    observation window, and execution plane for one action path.  This is the
    machine-readable form of ``EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1``.

    Action paths
    ------------
    - ``freeze_non_live``: medium-risk governance quarantine on paper/canary stage.
    - ``freeze_live_no_active_runtime``: high-risk freeze without an active runtime.
    - ``freeze_live_active_runtime``: high-risk freeze + companion operational path.
    - ``rollback_operational_followthrough``: runtime mitigation via Rollback Controller.
    - ``research_retrain``: low-risk retrain research work item creation.
    - ``research_revalidate``: low-risk revalidation research work item creation.
    - ``redeploy_followthrough``: deployment follow-through after research/revive completes.
    """
    return ActionPathsResponse(
        policy_document="EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1",
        paths=[ActionPathEntry(**p) for p in _ACTION_PATHS],
    )


# --- Threshold evaluation ----------------------------------------------------

@app.post("/api/evolution/threshold-evaluate", response_model=ThresholdEvalResponse)
def threshold_evaluate(body: ThresholdEvalRequest):
    """
    Evaluate a ThresholdSnapshot against the canonical policy and return
    the recommended ``proposed_action``.

    Used by automated detectors (drift monitors, incident classifiers) to
    determine which action type to request before calling ``POST /proposals``.
    """
    snap = ThresholdSnapshot(
        policy_source=body.snapshot.policy_source,
        signal_type=body.snapshot.signal_type,
        metric_name=body.snapshot.metric_name,
        comparator=body.snapshot.comparator,
        observed_value=body.snapshot.observed_value,
        threshold_value=body.snapshot.threshold_value,
        window=body.snapshot.window,
        breached=body.snapshot.breached,
        note=body.snapshot.note,
    )
    try:
        result = evaluator.classify(snap, context=body.context)
    except EvolutionControllerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ThresholdEvalResponse(
        proposed_action=str(result.proposed_action.value if hasattr(result.proposed_action, "value") else result.proposed_action),
        rationale=result.rationale,
        requires_runtime_followthrough=result.requires_runtime_followthrough,
        committee_review_required=result.committee_review_required,
        notes=list(result.notes),
    )
