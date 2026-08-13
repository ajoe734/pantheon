"""
Pydantic request/response models for the Evolution service HTTP API.

These models are shaped to the contract described in
EVOLUTION_REVIEW_AND_THRESHOLDS.md §14 and enforce only the fields that
the HTTP layer needs to validate at the boundary.  Deep domain invariants
(actor roles, risk levels, cooldown windows) are enforced by the platform
objects (EvolutionDecision, EvolutionController) in
services/control-plane/governance/.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared sub-objects
# ---------------------------------------------------------------------------

class ThresholdSnapshotIn(BaseModel):
    policy_source: str
    signal_type: str
    metric_name: str
    comparator: str
    observed_value: Any
    threshold_value: Any
    window: Optional[str] = None
    breached: bool = True
    note: Optional[str] = None


class EvidenceRefIn(BaseModel):
    ref_type: str
    ref_id: str
    storage_ref: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


class ReviewStepOut(BaseModel):
    step_type: str
    actor_role: str
    actor_id: str
    timestamp: str
    note: Optional[str] = None


class ExecutionReceiptIn(BaseModel):
    """Pointer to the downstream record that proves an action really ran.

    Deliberately carries no status field: the evolution service reads the
    downstream itself, so a caller cannot assert an outcome the downstream
    never reported.
    """

    downstream_kind: str
    downstream_ref_id: str


class ExecutionResultOut(BaseModel):
    status: str
    plane: str
    executed_at: str
    execution_ref_id: Optional[str] = None
    outcome_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------

class ProposeRequest(BaseModel):
    decision_id: str
    # Owning tenant.  Optional at the boundary so single-tenant callers keep
    # working; the governance object resolves an omitted value to the default
    # tenant and every later actor is checked against it.
    tenant_id: Optional[str] = None
    target_type: str
    target_id: str
    target_version: str
    action_type: str
    rationale: str
    created_by_id: str
    created_by_role: str = "evolution_controller"
    risk_level: Optional[str] = None
    target_stage: Optional[str] = None
    persona_id: Optional[str] = None
    capital_pool_id: Optional[str] = None
    linked_incident_id: Optional[str] = None
    linked_postmortem_id: Optional[str] = None
    evidence_refs: List[EvidenceRefIn] = Field(default_factory=list)
    threshold_snapshots: List[ThresholdSnapshotIn] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    # Optional durable-delivery envelope.  When present, the evolution service
    # validates a full foundation EventEnvelope for ``postmortem.published``
    # and uses its event/idempotency identity for inbox deduplication.  The
    # ordinary operator/controller proposal path leaves this unset.
    delivery_event: Optional[Dict[str, Any]] = None


class ProposeFromIncidentRequest(BaseModel):
    decision_id: str
    incident_id: str
    postmortem_id: Optional[str] = None
    created_by_id: str = "evolution-controller-incident-postmortem"
    created_by_role: str = "evolution_controller"
    action_type: Optional[str] = None
    target_type: str = "candidate_artifact"
    target_id: Optional[str] = None
    target_version: Optional[str] = None
    target_stage: Optional[str] = None
    rationale: Optional[str] = None
    has_active_runtime: bool = False
    metadata: Optional[Dict[str, Any]] = None


class ProposeFromPostmortemPublishedRequest(BaseModel):
    postmortem_id: str
    decision_id: Optional[str] = None
    publish_event_id: Optional[str] = None
    created_by_id: str = "postmortem-bridge"
    created_by_role: str = "evolution_controller"


class LearnFeedbackWritebackRequest(BaseModel):
    sponsor_persona_id: str
    contributing_persona_ids: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    contributor_feedback: List[Dict[str, Any]] = Field(default_factory=list)
    proposal_ids: List[str] = Field(default_factory=list)
    proposal_ids_by_persona: Dict[str, List[str]] = Field(default_factory=dict)


class LearnFeedbackWritebackResponse(BaseModel):
    source_event_type: str
    source_event_id: str
    write_authority: str
    sponsor_persona_id: str
    contributing_persona_ids: List[str]
    summary: str
    headline: str
    body: str
    evidence_refs: List[Dict[str, Any]]
    contributor_feedback: List[Dict[str, Any]]
    proposal_ids: List[str]
    proposal_ids_by_persona: Dict[str, List[str]]
    tags: List[str]



# ---------------------------------------------------------------------------
# Review / Approve / Reject / Execute / Cancel
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    actor_role: str
    actor_id: str
    approval_decision_id: str
    note: Optional[str] = None
    # The acting party's tenant. Omitted means the default tenant, which is
    # refused against a decision owned by any other tenant.
    tenant_id: Optional[str] = None


class ApproveRequest(BaseModel):
    actor_role: str
    actor_id: str
    approval_decision_id: Optional[str] = None
    note: Optional[str] = None
    tenant_id: Optional[str] = None


class RejectRequest(BaseModel):
    actor_role: str
    actor_id: str
    note: str
    approval_decision_id: Optional[str] = None
    tenant_id: Optional[str] = None


class ExecuteRequest(BaseModel):
    actor_role: str
    actor_id: str
    # Execution context for routing
    has_active_runtime: bool = False
    active_binding_id: Optional[str] = None
    freeze_mode: str = "governance_only"
    rollback_action_type: Optional[str] = None
    fallback_artifact_id: Optional[str] = None
    fallback_artifact_version: Optional[str] = None
    force_stage_freeze: bool = False
    note: Optional[str] = None
    tenant_id: Optional[str] = None
    # Reference to the downstream record whose terminal state authorises this
    # execution.  The service re-reads that record itself; a caller-asserted
    # status is not accepted as evidence.
    execution_receipt: Optional["ExecutionReceiptIn"] = None


class CancelRequest(BaseModel):
    actor_role: str
    actor_id: str
    note: str
    tenant_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------

class ThresholdEvalRequest(BaseModel):
    snapshot: ThresholdSnapshotIn
    context: Optional[Dict[str, Any]] = None


class ThresholdEvalResponse(BaseModel):
    proposed_action: str
    rationale: str
    requires_runtime_followthrough: bool
    committee_review_required: bool
    notes: List[str]


# ---------------------------------------------------------------------------
# Daily sweep
# ---------------------------------------------------------------------------

class DailySweepRequest(BaseModel):
    incident_ids: List[str] = Field(default_factory=list)
    include_closed: bool = False
    max_incidents: Optional[int] = None
    sweep_id: str = "daily"


class DailySweepItemResponse(BaseModel):
    incident_id: str
    status: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    decision_id: Optional[str] = None
    action_type: Optional[str] = None
    active_decision_id: Optional[str] = None
    reason: Optional[str] = None


class DailySweepResponse(BaseModel):
    sweep_id: str
    scanned_incidents: int
    created_decisions: int
    existing_decisions: int
    cooldown_blocked: int
    skipped_incidents: int
    items: List[DailySweepItemResponse]
    scheduler_attach: Dict[str, str]


# ---------------------------------------------------------------------------
# Decision response
# ---------------------------------------------------------------------------

class DecisionResponse(BaseModel):
    decision_id: str
    tenant_id: str
    target_type: str
    target_id: str
    target_version: str
    action_type: str
    decision_state: str
    risk_level: str
    created_at: str
    created_by_role: str
    created_by_id: str
    rationale: str
    approval_decision_id: Optional[str]
    target_stage: Optional[str]
    persona_id: Optional[str]
    capital_pool_id: Optional[str]
    linked_incident_id: Optional[str]
    linked_postmortem_id: Optional[str]
    cooldown_started_at: Optional[str]
    cooldown_ends_at: Optional[str]
    observation_window_started_at: Optional[str]
    observation_window_ends_at: Optional[str]
    superseded_by: Optional[str]
    supersedes_decision_id: Optional[str]
    evidence_refs: List[Dict[str, Any]]
    threshold_snapshots: List[Dict[str, Any]]
    review_chain: List[Dict[str, Any]]
    execution_result: Optional[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]
    is_active: bool


class ObservationWindowReportResponse(BaseModel):
    decision_id: str
    target_type: str
    target_id: str
    target_version: str
    target_stage: Optional[str]
    action_type: str
    risk_level: str
    decision_state: str
    report_generated_at: str
    observation_window_started_at: str
    observation_window_ends_at: str
    cooldown_started_at: str
    cooldown_ends_at: str
    observation_state: str
    cooldown_state: str
    active_until: str
    active_blocking: bool
    seconds_since_observation_start: int
    seconds_until_observation_end: int
    seconds_until_cooldown_end: int
    convergence_status: str
    approval_decision_id: Optional[str]
    linked_incident_id: Optional[str]
    linked_postmortem_id: Optional[str]
    execution: Dict[str, Any]
    followthrough_refs: List[Dict[str, Any]]
    evidence_refs: List[Dict[str, Any]]
    threshold_snapshots: List[Dict[str, Any]]
    review_chain: List[Dict[str, Any]]
    policy_refs: List[str]
    notes: List[str]


class BoundaryResponse(BaseModel):
    boundary_key: str
    execution_plane: str
    threshold_policy_source: str
    reviewed_owner_roles: List[str]
    approved_owner_roles: List[str]
    default_cooldown_days: int
    default_observation_days: int
    followthrough: List[str]
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Redeploy follow-through
# ---------------------------------------------------------------------------

class RedeployFollowthroughRequest(BaseModel):
    artifact_id: str
    artifact_version: str
    approval_decision_id: str
    target_stage: str
    requested_at: Optional[str] = None
    sponsor_persona_id: Optional[str] = None


class DispatchCommandResponse(BaseModel):
    command_id: str
    decision_id: str
    execution_plane: str
    action_type: str
    target_type: str
    target_id: str
    target_version: str
    target_stage: Optional[str]
    cooldown_ends_at: str
    observation_window_ends_at: str
    metadata: Dict[str, Any]


# ---------------------------------------------------------------------------
# Rollback follow-through
# ---------------------------------------------------------------------------

class RollbackFollowthroughRequest(BaseModel):
    actor_role: str
    actor_id: str
    tenant_id: Optional[str] = None
    execution_receipt: Optional["ExecutionReceiptIn"] = None
    active_binding_id: Optional[str] = None
    rollback_action_type: Optional[str] = None
    fallback_artifact_id: Optional[str] = None
    fallback_artifact_version: Optional[str] = None
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Action-paths routing matrix
# ---------------------------------------------------------------------------

class ActionPathEntry(BaseModel):
    path_key: str
    action_family: str
    trigger_source: str
    reviewed_owner: str
    approved_owner: str
    cooldown_days: int
    observation_days: int
    execution_plane: str
    followthrough: List[str]
    policy_source: str
    notes: Optional[str] = None


class ActionPathsResponse(BaseModel):
    policy_document: str
    paths: List[ActionPathEntry]


# ---------------------------------------------------------------------------
# Durable dispatch outbox / compensation
# ---------------------------------------------------------------------------

class DispatchOutboxRecordResponse(BaseModel):
    outbox_id: str
    tenant_id: str
    decision_id: str
    action_type: Optional[str] = None
    execution_plane: Optional[str] = None
    status: str
    delivery_ready: bool
    delivery_attempts: int
    redrive_count: int
    last_error: Optional[str] = None
    next_attempt_at: Optional[str] = None
    replay_available_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    published_at: Optional[str] = None


class DispatchOutboxListResponse(BaseModel):
    tenant_id: Optional[str] = None
    records: List[DispatchOutboxRecordResponse]


class DispatchReplayRequest(BaseModel):
    actor_id: str
    note: str
    tenant_id: Optional[str] = None


class CompensationResponse(BaseModel):
    compensation_id: str
    tenant_id: str
    decision_id: str
    outbox_id: str
    reason: str
    downstream_kind: Optional[str] = None
    downstream_ref_id: Optional[str] = None
    recorded_at: Optional[str] = None
    resolved: bool = False
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_note: Optional[str] = None


class CompensationListResponse(BaseModel):
    tenant_id: Optional[str] = None
    compensations: List[CompensationResponse]


class CompensationResolveRequest(BaseModel):
    actor_id: str
    note: str
    tenant_id: Optional[str] = None
