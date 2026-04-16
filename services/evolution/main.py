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

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query

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
from evolution_decision import (  # type: ignore
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionError,
    EvolutionDecisionState,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ThresholdSnapshot,
    utc_now,
    validate_evolution_decision,
)

# ---------------------------------------------------------------------------
# Local models
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import (  # type: ignore
    ApproveRequest,
    BoundaryResponse,
    CancelRequest,
    DecisionResponse,
    ExecuteRequest,
    ProposeRequest,
    RejectRequest,
    ReviewRequest,
    ThresholdEvalRequest,
    ThresholdEvalResponse,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path bootstrap — incident objects live in services/incident
# ---------------------------------------------------------------------------
_INCIDENT_SVC = Path(__file__).resolve().parent.parent / "incident"
if str(_INCIDENT_SVC) not in sys.path:
    sys.path.insert(0, str(_INCIDENT_SVC))

from incident import IncidentError, IncidentStore  # type: ignore

# ---------------------------------------------------------------------------
# App + storage
# ---------------------------------------------------------------------------
app = FastAPI(title="Pantheon Evolution Service", version="1.0.0")

EVOLUTION_DATA_DIR = os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution")
os.makedirs(EVOLUTION_DATA_DIR, exist_ok=True)

INCIDENT_DATA_DIR = os.getenv("INCIDENT_DATA_DIR", "/tmp/pantheon/incident")
os.makedirs(INCIDENT_DATA_DIR, exist_ok=True)

store = EvolutionDecisionStore(
    storage_path=os.path.join(EVOLUTION_DATA_DIR, "decisions.json"),
)
incident_store = IncidentStore(
    path=Path(os.path.join(INCIDENT_DATA_DIR, "incidents.json")),
)
controller = EvolutionController()
evaluator = ThresholdEvaluator()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decision_to_response(decision: EvolutionDecision) -> DecisionResponse:
    d = decision.to_dict()
    return DecisionResponse(
        decision_id=d["decision_id"],
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


def _domain_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "evolution"}


# --- Propose -----------------------------------------------------------------

@app.post("/api/evolution/proposals", status_code=201, response_model=DecisionResponse)
def propose(body: ProposeRequest):
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
    # Validate postmortem reference before creating the decision so we don't
    # land in a partial-write state where the decision exists but the back-link
    # could not be written.
    if body.linked_postmortem_id:
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
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    # Wire the evolution → postmortem back-link (EVO-003 lineage edge).
    if decision.linked_postmortem_id:
        try:
            incident_store.link_evolution_decision(decision.linked_postmortem_id, decision.decision_id)
        except IncidentError as exc:
            log.warning(
                "evolution.propose: could not back-link postmortem %s → decision %s: %s",
                decision.linked_postmortem_id,
                decision.decision_id,
                exc,
            )
    log.info("evolution.proposed decision_id=%s action=%s", decision.decision_id, decision.action_type)
    return _decision_to_response(decision)


# --- List / filter -----------------------------------------------------------

@app.get("/api/evolution/proposals", response_model=List[DecisionResponse])
def list_proposals(
    target_id: Optional[str] = Query(default=None),
    target_type: Optional[str] = Query(default=None),
    decision_state: Optional[str] = Query(default=None),
    risk_level: Optional[str] = Query(default=None),
    active_only: bool = Query(default=False),
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
    decisions: List[EvolutionDecision] = store.list_all()
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
    return _decision_to_response(decision)


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
    try:
        decision.mark_reviewed(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            approval_decision_id=body.approval_decision_id,
            note=body.note,
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
    try:
        decision.approve(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            approval_decision_id=body.approval_decision_id,
            note=body.note,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    log.info("evolution.approved decision_id=%s actor=%s", decision_id, body.actor_id)
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
    try:
        decision.reject(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            approval_decision_id=body.approval_decision_id,
            note=body.note,
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
    try:
        decision.cancel(
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            note=body.note,
        )
        store.put(decision)
    except EvolutionDecisionError as exc:
        raise _domain_error(exc) from exc
    log.info("evolution.canceled decision_id=%s actor=%s", decision_id, body.actor_id)
    return _decision_to_response(decision)


# --- Execute -----------------------------------------------------------------

@app.post("/api/evolution/proposals/{decision_id}/execute", response_model=DecisionResponse)
def execute_proposal(decision_id: str, body: ExecuteRequest):
    """
    Execute an approved EvolutionDecision via the EvolutionController.

    This route:
    1. Calls ``EvolutionController.execute_approved()`` to determine the
       action boundary, compute cooldown/observation windows, and emit
       dispatch/rollback commands.
    2. Mutates the decision into ``executed`` state with full cooldown
       and observation-window metadata.

    Enforcement
    -----------
    - Decision must be in ``approved`` state.
    - actor_role must be in the execution-roles set
      (evolution_controller or operator).
    - Cooldown and observation-window timestamps are set automatically
      from the canonical policy; callers cannot override them.

    Response fields
    ---------------
    ``cooldown_ends_at`` and ``observation_window_ends_at`` in the
    response surface the canonical policy windows for runtime monitoring.
    """
    decision = store.get(decision_id)
    if decision is None:
        raise _not_found(decision_id)
    try:
        freeze_mode = FreezeFollowthroughMode(body.freeze_mode)
    except ValueError as exc:
        valid = [m.value for m in FreezeFollowthroughMode]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid freeze_mode {body.freeze_mode!r}. Must be one of {valid}",
        ) from exc
    try:
        controller.execute_approved(
            decision,
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            has_active_runtime=body.has_active_runtime,
            active_binding_id=body.active_binding_id,
            freeze_mode=freeze_mode,
            rollback_action_type=body.rollback_action_type,
            fallback_artifact_id=body.fallback_artifact_id,
            fallback_artifact_version=body.fallback_artifact_version,
            force_stage_freeze=body.force_stage_freeze,
        )
        store.put(decision)
    except (EvolutionDecisionError, EvolutionControllerError) as exc:
        raise _domain_error(exc) from exc
    log.info(
        "evolution.executed decision_id=%s actor=%s cooldown_ends=%s obs_ends=%s",
        decision_id,
        body.actor_id,
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
