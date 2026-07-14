"""
Governance Service — ApprovalDecision API

Deployable FastAPI service that exposes the canonical ApprovalDecision
governance API, write-authority matrix, and audit log read path.

Promotion, deployment, and evolution flows cite this service as the single
canonical approval surface instead of maintaining local fallbacks.

Depends on
----------
  services/control-plane/governance/approval_decision.py   (platform objects)

Routes
------
  POST   /api/governance/approvals                          propose
  GET    /api/governance/approvals                          list (filterable)
  GET    /api/governance/approvals/latest-approved          latest approved for a target
  GET    /api/governance/approvals/{decision_id}            get single decision
  POST   /api/governance/approvals/{decision_id}/review     accept review
  POST   /api/governance/approvals/{decision_id}/decide     record outcome
  POST   /api/governance/approvals/{decision_id}/revoke     revoke decided decision
  GET    /api/governance/freeze-orders                      list freeze orders
  GET    /api/governance/freeze-orders/{freeze_order_id}    get freeze order
  GET    /api/governance/rollbacks                          list rollback records
  GET    /api/governance/rollbacks/{rollback_id}            get rollback record
  GET    /api/governance/write-authority                    write-authority matrix
  GET    /api/governance/audit                              audit log read path
  GET    /health                                            liveness probe
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import threading
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, Body, Response
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture

# ---------------------------------------------------------------------------
# Platform objects — resolve relative to repo layout
# ---------------------------------------------------------------------------
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import (  # type: ignore
    ActorRole,
    ApprovalDecision,
    ApprovalDecisionStore,
    DecisionOutcome,
    DecisionState,
    EvidenceRef,
    OwnerMatrix,
    RiskLevel,
    TargetType,
)

# ---------------------------------------------------------------------------
# Local modules
# ---------------------------------------------------------------------------
try:
    from .models import (
        AcceptReviewRequest,
        ApprovalDecisionResponse,
        AuthzCheckRequest,
        AuthzCheckResponse,
        DecideRequest,
        ProposeApprovalRequest,
        RevokeRequest,
        WriteAuthorityEntry,
        WriteAuthorityResponse,
    )
    from .authz import evaluate_authz_request
    from .pg_store import build_approval_decision_store, build_governance_audit_store
    from .record_store import GovernanceRecordStore, build_governance_record_store
    from .write_authority import is_authorized_to_decide, matrix_as_list
except ImportError:
    from models import (  # type: ignore
        AcceptReviewRequest,
        ApprovalDecisionResponse,
        AuthzCheckRequest,
        AuthzCheckResponse,
        DecideRequest,
        ProposeApprovalRequest,
        RevokeRequest,
        WriteAuthorityEntry,
        WriteAuthorityResponse,
    )
    from authz import evaluate_authz_request  # type: ignore
    from pg_store import build_approval_decision_store, build_governance_audit_store  # type: ignore
    from record_store import GovernanceRecordStore, build_governance_record_store  # type: ignore
    from write_authority import is_authorized_to_decide, matrix_as_list  # type: ignore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.getenv("GOVERNANCE_DATA_DIR", "/tmp/pantheon/governance")
os.makedirs(DATA_DIR, exist_ok=True)

AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit.jsonl")
STORE_PATH     = os.path.join(DATA_DIR, "approval_decisions.json")
FREEZE_ORDER_STORE_PATH = os.path.join(DATA_DIR, "freeze_orders.json")
ROLLBACK_STORE_PATH = os.path.join(DATA_DIR, "rollbacks.json")

STORE_BACKEND = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("governance")
store = build_approval_decision_store(STORE_PATH)
audit_store = build_governance_audit_store(AUDIT_LOG_PATH)
freeze_order_store: GovernanceRecordStore = build_governance_record_store(
    FREEZE_ORDER_STORE_PATH,
    table=os.getenv("GOVERNANCE_FREEZE_ORDER_STORE_TABLE", "governance.freeze_orders"),
    id_fields=("freeze_order_id", "id"),
)
rollback_store: GovernanceRecordStore = build_governance_record_store(
    ROLLBACK_STORE_PATH,
    table=os.getenv("GOVERNANCE_ROLLBACK_STORE_TABLE", "governance.rollbacks"),
    id_fields=("rollback_id", "id"),
)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pantheon Governance Service",
    version="0.1.0",
    description=(
        "Canonical ApprovalDecision governance API.  "
        "Promotion, deployment, and evolution flows reference this service "
        "instead of local fallbacks."
    ),
)
register_fastapi_health_routes(
    app,
    "governance",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {
        "approval_count": len(store.list_all()),
        "freeze_order_count": len(freeze_order_store.list_all()),
        "rollback_count": len(rollback_store.list_all()),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_path": STORE_PATH,
        "freeze_order_store_path": FREEZE_ORDER_STORE_PATH,
        "rollback_store_path": ROLLBACK_STORE_PATH,
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_response(d: ApprovalDecision) -> ApprovalDecisionResponse:
    return ApprovalDecisionResponse(**d.to_dict())


def _emit(
    event_type: str,
    decision: ApprovalDecision,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        audit_store.append_event(
            event_type=event_type,
            decision_id=decision.decision_id,
            actor_id=decision.actor_id,
            actor_role=(
                decision.actor_role.value
                if isinstance(decision.actor_role, ActorRole)
                else decision.actor_role
            ),
            target_type=(
                decision.target_type.value
                if isinstance(decision.target_type, TargetType)
                else decision.target_type
            ),
            target_id=decision.target_id,
            detail=detail,
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)


def _get_or_404(decision_id: str) -> ApprovalDecision:
    decision = store.get(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")
    return decision


def _record_or_404(
    record_store: GovernanceRecordStore,
    record_id: str,
    *,
    label: str,
) -> Dict[str, Any]:
    record = record_store.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{label} '{record_id}' not found")
    return record


def _record_time(record: Dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# Routes — freeze-order and rollback read models
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/freeze-orders",
    response_model=List[Dict[str, Any]],
    summary="List canonical freeze orders",
)
def list_freeze_orders(
    status: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """List governance quarantine records, most-recent first."""
    records = freeze_order_store.list_all()
    if status:
        records = [record for record in records if record.get("status") == status]
    if scope:
        records = [record for record in records if record.get("scope") == scope]
    return sorted(
        records,
        key=lambda record: _record_time(record, "created_at", "issued_at", "updated_at"),
        reverse=True,
    )


@app.get(
    "/api/governance/freeze-orders/{freeze_order_id}",
    response_model=Dict[str, Any],
    summary="Get a canonical freeze order",
)
def get_freeze_order(freeze_order_id: str) -> Dict[str, Any]:
    return _record_or_404(freeze_order_store, freeze_order_id, label="Freeze order")


@app.get(
    "/api/governance/rollbacks",
    response_model=List[Dict[str, Any]],
    summary="List canonical rollback records",
)
def list_rollbacks(
    runtime_id: Optional[str] = Query(None),
    action_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """List rollback request/outcome read models, most-recent first."""
    records = rollback_store.list_all()
    if runtime_id:
        records = [record for record in records if record.get("runtime_id") == runtime_id]
    if action_type:
        records = [record for record in records if record.get("action_type") == action_type]
    if status:
        records = [record for record in records if record.get("status") == status]
    return sorted(
        records,
        key=lambda record: _record_time(record, "initiated_at", "requested_at", "created_at", "updated_at"),
        reverse=True,
    )


@app.get(
    "/api/governance/rollbacks/{rollback_id}",
    response_model=Dict[str, Any],
    summary="Get a canonical rollback record",
)
def get_rollback(rollback_id: str) -> Dict[str, Any]:
    return _record_or_404(rollback_store, rollback_id, label="Rollback")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Routes — freeze-order and rollback write models
# ---------------------------------------------------------------------------

_freeze_order_lock = threading.Lock()
_rollback_lock = threading.Lock()


@app.post(
    "/api/governance/freeze-orders",
    response_model=Dict[str, Any],
    status_code=201,
    summary="Record or update a canonical freeze order",
)
def record_freeze_order(body: Dict[str, Any] = Body(...), response: Response = None) -> Dict[str, Any]:
    """Create or update a FreezeOrder in the canonical store."""
    freeze_order_id = body.get("freeze_order_id") or body.get("id")
    if not freeze_order_id:
        freeze_order_id = f"freeze-{uuid.uuid4().hex[:12]}"
        body["freeze_order_id"] = freeze_order_id
        body["id"] = freeze_order_id

    if not body.get("created_at") and not body.get("issued_at"):
        body["created_at"] = _utc_now()
        body["issued_at"] = body["created_at"]

    with _freeze_order_lock:
        existing = freeze_order_store.get(freeze_order_id)
        is_transition = False
        if existing:
            is_transition = True
            if response:
                response.status_code = 200

            # Validate incoming transition audit fields
            inc_actor = body.get("transition_actor") or body.get("actor")
            inc_identity = body.get("transition_identity") or body.get("identity")
            inc_source_cmd = body.get("transition_source_command_id") or body.get("source_command_id")

            if not inc_actor or not inc_identity or not inc_source_cmd:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required transition audit fields (actor, identity, source_command_id)"
                )

            merged = dict(existing)
            for k, v in body.items():
                if v is not None:
                    merged[k] = v

            # Set transition audit fields explicitly
            merged["transition_actor"] = inc_actor
            merged["transition_identity"] = inc_identity
            merged["transition_source_command_id"] = inc_source_cmd

            # Preserve original fields and audit origin on transitions
            for field in ["scope", "target_id", "created_at", "issued_at", "actor", "identity", "source_command_id"]:
                if field in existing and existing[field] not in (None, ""):
                    merged[field] = existing[field]
            body = merged

        body["updated_at"] = _utc_now()

        # Enforce required audit fields on final payload
        for field in ["status", "actor", "identity", "source_command_id", "scope", "target_id"]:
            if not body.get(field):
                raise HTTPException(status_code=400, detail=f"Missing required audit field: {field}")

        # Enforce write authority on transitions
        if is_transition:
            new_status = body.get("status")
            if new_status in ("approved", "rejected", "active"):
                transition_actor_role = body.get("transition_actor")
                allowed_roles = {"governance_reviewer", "risk_owner", "governance_committee", "admin", "approver", "approver-role", "rejecter-role"}
                if transition_actor_role not in allowed_roles:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Role '{transition_actor_role}' is not authorized to transition freeze order to {new_status}."
                    )

        freeze_order_store.put(body)

    log.info("Recorded freeze order %s: %s", freeze_order_id, body)

    # Append audit event
    try:
        audit_store.append_event(
            event_type="freeze_order_state_changed" if is_transition else "freeze_order_created",
            decision_id=freeze_order_id,
            actor_id=body.get("transition_identity") or body.get("identity"),
            actor_role=body.get("transition_actor") or body.get("actor"),
            target_type="freeze_scope",
            target_id=body.get("scope"),
            detail={
                "status": body.get("status"),
                "target_id": body.get("target_id"),
                "source_command_id": body.get("source_command_id"),
                "transition_source_command_id": body.get("transition_source_command_id"),
            }
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)

    return body


@app.post(
    "/api/governance/rollbacks",
    response_model=Dict[str, Any],
    status_code=201,
    summary="Record or update a canonical rollback record",
)
def record_rollback(body: Dict[str, Any] = Body(...), response: Response = None) -> Dict[str, Any]:
    """Create or update a Rollback record in the canonical store."""
    rollback_id = body.get("rollback_id") or body.get("id")
    if not rollback_id:
        rollback_id = f"rollback-{uuid.uuid4().hex[:12]}"
        body["rollback_id"] = rollback_id
        body["id"] = rollback_id

    if not body.get("created_at") and not body.get("initiated_at"):
        body["created_at"] = _utc_now()
        body["initiated_at"] = body["created_at"]
        body["requested_at"] = body["created_at"]

    with _rollback_lock:
        existing = rollback_store.get(rollback_id)
        is_transition = False
        if existing:
            is_transition = True
            if response:
                response.status_code = 200

            # Validate incoming transition audit fields
            inc_actor = body.get("transition_actor") or body.get("actor")
            inc_identity = body.get("transition_identity") or body.get("identity")
            inc_source_cmd = body.get("transition_source_command_id") or body.get("source_command_id")

            if not inc_actor or not inc_identity or not inc_source_cmd:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required transition audit fields (actor, identity, source_command_id)"
                )

            merged = dict(existing)
            for k, v in body.items():
                if v is not None:
                    merged[k] = v

            # Set transition audit fields explicitly
            merged["transition_actor"] = inc_actor
            merged["transition_identity"] = inc_identity
            merged["transition_source_command_id"] = inc_source_cmd

            # Preserve original fields and audit origin on transitions
            for field in ["runtime_id", "runtime_binding_id", "action_type", "target_artifact_id", "created_at", "initiated_at", "requested_at", "actor", "identity", "source_command_id"]:
                if field in existing and existing[field] not in (None, ""):
                    merged[field] = existing[field]
            body = merged

        body["updated_at"] = _utc_now()

        # Enforce required audit fields on final payload
        for field in ["status", "actor", "identity", "source_command_id", "runtime_id", "action_type"]:
            if not body.get(field):
                raise HTTPException(status_code=400, detail=f"Missing required audit field: {field}")

        # Enforce write authority on transitions
        if is_transition:
            new_status = body.get("status")
            if new_status in ("approved", "rejected"):
                transition_actor_role = body.get("transition_actor")
                allowed_roles = {"governance_reviewer", "risk_owner", "governance_committee", "admin", "approver", "approver-role", "rejecter-role"}
                if transition_actor_role not in allowed_roles:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Role '{transition_actor_role}' is not authorized to transition rollback to {new_status}."
                    )

        rollback_store.put(body)

    log.info("Recorded rollback %s: %s", rollback_id, body)

    # Append audit event
    try:
        audit_store.append_event(
            event_type="rollback_state_changed" if is_transition else "rollback_created",
            decision_id=rollback_id,
            actor_id=body.get("transition_identity") or body.get("identity"),
            actor_role=body.get("transition_actor") or body.get("actor"),
            target_type="runtime_binding",
            target_id=body.get("runtime_binding_id") or body.get("runtime_id"),
            detail={
                "status": body.get("status"),
                "action_type": body.get("action_type"),
                "source_command_id": body.get("source_command_id"),
                "transition_source_command_id": body.get("transition_source_command_id"),
            }
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)

    return body


# ---------------------------------------------------------------------------
# Routes — proposals
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/approvals",
    response_model=ApprovalDecisionResponse,
    status_code=201,
    summary="Propose a new ApprovalDecision",
)
def propose_approval(body: ProposeApprovalRequest) -> ApprovalDecisionResponse:
    """Create a new ApprovalDecision in the *proposed* state.

    Called by: promotion plane, evolution controller, registry pipeline.
    """
    decision_id = body.decision_id or f"apv-{uuid.uuid4().hex[:12]}"
    if store.get(decision_id):
        raise HTTPException(
            status_code=409,
            detail=f"Decision '{decision_id}' already exists",
        )

    decision = ApprovalDecision.create_proposed(
        decision_id=decision_id,
        target_type=body.target_type.value,
        target_id=body.target_id,
        target_version=body.target_version,
        risk_level=body.risk_level.value,
        capital_pool_id=body.capital_pool_id,
        persona_id=body.persona_id,
    )

    errors = decision.validate()
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    store.put(decision)
    _emit("approval_decision_created", decision)
    log.info("Proposed %s for %s/%s", decision_id, body.target_type, body.target_id)
    return _to_response(decision)


# ---------------------------------------------------------------------------
# Routes — list / lookup
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/approvals/latest-approved",
    response_model=Optional[ApprovalDecisionResponse],
    summary="Latest approved decision for a target",
)
def get_latest_approved(
    target_type: str = Query(..., description="TargetType value"),
    target_id: str  = Query(..., description="Target artifact / object ID"),
) -> Optional[ApprovalDecisionResponse]:
    """Return the most recent *decided* + *approved* decision for a target.

    Returns null (HTTP 200 with null body) when no approved decision exists.

    Used by deployment planner, runtime manager, and evolution controller to
    verify canonical approval before proceeding.
    """
    decision = store.find_latest_approved(target_type, target_id)
    if not decision:
        return None
    return _to_response(decision)


@app.get(
    "/api/governance/approvals",
    response_model=List[ApprovalDecisionResponse],
    summary="List approval decisions",
)
def list_approvals(
    target_type:    Optional[str] = Query(None),
    target_id:      Optional[str] = Query(None),
    decision_state: Optional[str] = Query(None),
    risk_level:     Optional[str] = Query(None),
) -> List[ApprovalDecisionResponse]:
    """List all decisions with optional filters.  Most-recent first."""
    decisions = store.list_all()

    def _match_str(val, expected: str) -> bool:
        return val == expected or (hasattr(val, "value") and val.value == expected)

    if target_type:
        decisions = [d for d in decisions if _match_str(d.target_type, target_type)]
    if target_id:
        decisions = [d for d in decisions if d.target_id == target_id]
    if decision_state:
        decisions = [d for d in decisions if _match_str(d.decision_state, decision_state)]
    if risk_level:
        decisions = [d for d in decisions if _match_str(d.risk_level, risk_level)]

    decisions.sort(key=lambda d: d.created_at, reverse=True)
    return [_to_response(d) for d in decisions]


@app.get(
    "/api/governance/approvals/{decision_id}",
    response_model=ApprovalDecisionResponse,
    summary="Get a single approval decision",
)
def get_approval(decision_id: str) -> ApprovalDecisionResponse:
    return _to_response(_get_or_404(decision_id))


# ---------------------------------------------------------------------------
# Routes — lifecycle transitions
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/approvals/{decision_id}/review",
    response_model=ApprovalDecisionResponse,
    summary="Accept review (proposed → under_review)",
)
def accept_review(
    decision_id: str,
    body: AcceptReviewRequest,
) -> ApprovalDecisionResponse:
    """Transition a proposed decision to *under_review*.

    Authorization: role must be permitted for the decision's risk_level per
    the write-authority matrix.
    """
    decision = _get_or_404(decision_id)
    try:
        decision.accept_review(actor_role=body.actor_role.value, actor_id=body.actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store.put(decision)
    _emit("approval_decision_state_changed", decision, {"new_state": "under_review"})
    return _to_response(decision)


@app.post(
    "/api/governance/approvals/{decision_id}/decide",
    response_model=ApprovalDecisionResponse,
    summary="Record outcome (under_review → decided)",
)
def record_decision(
    decision_id: str,
    body: DecideRequest,
) -> ApprovalDecisionResponse:
    """Record the final outcome: *approved*, *rejected*, or *approved_with_conditions*.

    An *approved* decision here is what deployment planner and evolution
    controller cite when constructing a DeploymentPlan or executing a
    follow-through action.
    """
    decision = _get_or_404(decision_id)

    # Enforce write-authority matrix: caller must hold a role authorized to
    # decide at the decision's risk level.
    risk_level_str = (
        decision.risk_level.value
        if hasattr(decision.risk_level, "value")
        else decision.risk_level
    )
    if not is_authorized_to_decide(body.actor_role.value, risk_level_str):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Role '{body.actor_role.value}' is not authorized to decide "
                f"at risk level '{risk_level_str}'"
            ),
        )

    evidence_refs = None
    if body.evidence_refs:
        evidence_refs = [
            EvidenceRef(
                ref_type=e.ref_type,
                ref_id=e.ref_id,
                storage_ref=e.storage_ref,
            )
            for e in body.evidence_refs
        ]

    try:
        decision.decide(
            outcome=body.outcome.value,
            rationale=body.rationale,
            actor_role=body.actor_role.value,
            actor_id=body.actor_id,
            conditions=body.conditions,
            evidence_refs=evidence_refs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store.put(decision)
    _emit(
        "approval_decision_decided",
        decision,
        {"outcome": body.outcome.value, "rationale": body.rationale},
    )
    log.info("Decision %s → %s", decision_id, body.outcome)
    return _to_response(decision)


@app.post(
    "/api/governance/approvals/{decision_id}/revoke",
    response_model=ApprovalDecisionResponse,
    summary="Revoke a decided decision",
)
def revoke_decision(
    decision_id: str,
    body: RevokeRequest,
) -> ApprovalDecisionResponse:
    """Revoke a decided approval.  Requires risk_owner or governance_committee role."""
    decision = _get_or_404(decision_id)
    try:
        decision.revoke(actor_role=body.actor_role.value, actor_id=body.actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store.put(decision)
    _emit("approval_decision_revoked", decision)
    log.info("Decision %s revoked by %s (%s)", decision_id, body.actor_id, body.actor_role)
    return _to_response(decision)


# ---------------------------------------------------------------------------
# Routes — write-authority matrix
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/write-authority",
    response_model=WriteAuthorityResponse,
    summary="Write-authority matrix",
)
def get_write_authority() -> WriteAuthorityResponse:
    """Return the canonical write-authority matrix.

    Specifies which actor roles may write an ApprovalDecision at each risk
    level.  Callers can consult this before submitting a decision to verify
    they hold a permitted role.
    """
    return WriteAuthorityResponse(
        matrix=[WriteAuthorityEntry(**entry) for entry in matrix_as_list()],
        description=(
            "Risk-level → authorized_roles: only these roles may record "
            "an ApprovalDecision at the given risk level.  "
            "revoke_roles may revoke any decided decision."
        ),
    )


# ---------------------------------------------------------------------------
# Routes — AuthZ check
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/authz/check",
    response_model=AuthzCheckResponse,
    summary="Evaluate a narrow service authorization request",
)
def check_authz(body: AuthzCheckRequest) -> AuthzCheckResponse:
    """Return allow/deny for service-to-service policy checks.

    The first consumer is memory retrieval. Unsupported actions deny instead
    of falling back to implicit allow.
    """
    decision = evaluate_authz_request(
        action=body.action,
        actor_id=body.actor_id,
        actor_roles=body.actor_roles,
        resource=body.resource,
        context=body.context,
    )
    return AuthzCheckResponse(
        allowed=bool(decision.get("allowed")),
        reason=str(decision.get("reason") or "unknown"),
        policy_version="governance-authz.v1",
    )


# ---------------------------------------------------------------------------
# Routes — audit log read path
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/audit",
    summary="Audit log read path",
)
def get_audit_events(
    decision_id: Optional[str] = Query(None, description="Filter by decision_id"),
    limit:       int           = Query(100, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    """Return recent audit events from the governance audit log.

    Events are returned most-recent first.  Filter by decision_id to trace a
    single approval object through its lifecycle.
    """
    return audit_store.list_events(decision_id=decision_id, limit=limit)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", summary="Liveness probe")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "governance"}
