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


# ---------------------------------------------------------------------------
# Review / Approve / Reject / Execute / Cancel
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    actor_role: str
    actor_id: str
    approval_decision_id: str
    note: Optional[str] = None


class ApproveRequest(BaseModel):
    actor_role: str
    actor_id: str
    approval_decision_id: Optional[str] = None
    note: Optional[str] = None


class RejectRequest(BaseModel):
    actor_role: str
    actor_id: str
    note: str
    approval_decision_id: Optional[str] = None


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


class CancelRequest(BaseModel):
    actor_role: str
    actor_id: str
    note: str


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
# Decision response
# ---------------------------------------------------------------------------

class DecisionResponse(BaseModel):
    decision_id: str
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
