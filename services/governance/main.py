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
  GET    /api/governance/write-authority                    write-authority matrix
  GET    /api/governance/audit                              audit log read path
  GET    /health                                            liveness probe
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from services.foundation.health import register_fastapi_health_routes

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
        DecideRequest,
        ProposeApprovalRequest,
        RevokeRequest,
        WriteAuthorityEntry,
        WriteAuthorityResponse,
    )
    from .audit_log import append_audit_event
    from .write_authority import is_authorized_to_decide, matrix_as_list
except ImportError:
    from models import (  # type: ignore
        AcceptReviewRequest,
        ApprovalDecisionResponse,
        DecideRequest,
        ProposeApprovalRequest,
        RevokeRequest,
        WriteAuthorityEntry,
        WriteAuthorityResponse,
    )
    from audit_log import append_audit_event  # type: ignore
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

store: ApprovalDecisionStore = ApprovalDecisionStore(storage_path=STORE_PATH)

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
    metrics=lambda: {"approval_count": len(store.list_all())},
    details=lambda: {"data_dir": DATA_DIR, "store_path": STORE_PATH},
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
        append_audit_event(
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
            audit_log_path=AUDIT_LOG_PATH,
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)


def _get_or_404(decision_id: str) -> ApprovalDecision:
    decision = store.get(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")
    return decision

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
    events: List[Dict[str, Any]] = []
    try:
        audit_path = Path(AUDIT_LOG_PATH)
        if not audit_path.exists():
            return events
        with audit_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if decision_id and event.get("decision_id") != decision_id:
                        continue
                    events.append(event)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return events

    events.reverse()
    return events[:limit]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", summary="Liveness probe")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "governance"}
