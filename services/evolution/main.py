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

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from services.foundation.health import register_fastapi_health_routes

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
    ActionPathEntry,
    ActionPathsResponse,
    ApproveRequest,
    BoundaryResponse,
    CancelRequest,
    DailySweepRequest,
    DailySweepResponse,
    DecisionResponse,
    DispatchCommandResponse,
    ExecuteRequest,
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
    build_published_postmortem_proposal_request,
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
register_fastapi_health_routes(
    app,
    "evolution",
    metrics=lambda: {"decision_count": len(store.list_all())},
    details=lambda: {
        "evolution_data_dir": EVOLUTION_DATA_DIR,
        "incident_data_dir": INCIDENT_DATA_DIR,
    },
)


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
) -> EvolutionDecision | None:
    if postmortem.linked_evolution_decision_id:
        linked = store.get(postmortem.linked_evolution_decision_id)
        if linked is not None:
            return linked
    direct = store.get(decision_id)
    if direct is not None:
        return direct
    for decision in store.list_all():
        metadata = decision.metadata or {}
        if metadata.get("postmortem_bridge_key") == bridge_key:
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


@app.post(
    "/api/evolution/proposals/from-incident",
    status_code=201,
    response_model=DecisionResponse,
)
def propose_from_incident(body: ProposeFromIncidentRequest):
    """
    Create a proposed EvolutionDecision from canonical IncidentCase/Postmortem evidence.

    This route derives target lineage from the incident snapshot, links the
    resulting decision back to the incident/postmortem chain, and then reuses
    the normal proposal path. It intentionally stops at ``proposed``: review,
    approval, runtime mitigation, broker orders, and capital-binding writes
    remain separate gated steps.
    """
    return propose(_proposal_request_from_incident(body))


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
    existing = _find_postmortem_bridge_decision(
        postmortem=postmortem,
        bridge_key=bridge_key,
        decision_id=decision_id,
    )
    if existing is not None:
        _link_postmortem_to_decision(postmortem.postmortem_id, existing.decision_id)
        response.status_code = 200
        return _decision_to_response(existing)

    return propose(ProposeRequest(**proposal_payload))


# --- Daily sweep -------------------------------------------------------------

@app.post("/api/evolution/daily-sweep", response_model=DailySweepResponse)
def daily_sweep(body: DailySweepRequest):
    """
    Run one scheduler-safe evolution sweep over incident threshold triggers.

    The sweep is proposal-only: it reads IncidentCase evidence, derives
    EvolutionDecision proposals, and relies on the existing single-active-rule
    to block repeated triggers while cooldown/observation is active.
    """
    try:
        result = run_daily_sweep(
            incident_store=incident_store,
            decision_store=store,
            incident_ids=body.incident_ids or None,
            include_closed=body.include_closed,
            max_incidents=body.max_incidents,
            sweep_id=body.sweep_id,
            evaluator=evaluator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


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
    try:
        freeze_mode = FreezeFollowthroughMode.ROLLBACK
        controller.execute_approved(
            decision,
            actor_role=body.actor_role,
            actor_id=body.actor_id,
            has_active_runtime=True,
            active_binding_id=body.active_binding_id,
            freeze_mode=freeze_mode,
            rollback_action_type=body.rollback_action_type,
            fallback_artifact_id=body.fallback_artifact_id,
            fallback_artifact_version=body.fallback_artifact_version,
        )
        store.put(decision)
    except (EvolutionDecisionError, EvolutionControllerError) as exc:
        raise _domain_error(exc) from exc
    log.info(
        "evolution.rollback_followthrough decision_id=%s actor=%s",
        decision_id,
        body.actor_id,
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
