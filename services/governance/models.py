"""
Pydantic request / response models for the Governance Service API.

These are wire-layer models only.  Business-logic enums and platform objects
live in services/control-plane/governance/approval_decision.py.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Enums (mirrored from the platform layer so callers need only import this module)
# ---------------------------------------------------------------------------

class DecisionOutcome(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"


class DecisionState(str, Enum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    DECIDED = "decided"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class ActorRole(str, Enum):
    GOVERNANCE_REVIEWER = "governance_reviewer"
    RISK_OWNER = "risk_owner"
    GOVERNANCE_COMMITTEE = "governance_committee"
    AUTOMATED_GATE = "automated_gate"


class TargetType(str, Enum):
    PERSONA_TRAINING_TARGET = "persona_training_target"
    REGISTRY_ENTRY = "registry_entry"
    STRATEGY_SPEC = "strategy_spec"
    STRATEGY_WORKSHOP = "strategy_workshop"
    MODEL_ARTIFACT = "model_artifact"
    ALLOCATION_POLICY = "allocation_policy"
    PERSONA_CAPITAL_BINDING = "persona_capital_binding"
    EVOLUTION_PROPOSAL = "evolution_proposal"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Shared sub-objects
# ---------------------------------------------------------------------------

class EvidenceRefBody(BaseModel):
    ref_type: str
    ref_id: str
    storage_ref: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class ApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


class ProposeApprovalRequest(ApprovalCommand):
    decision_id: Optional[str] = None   # auto-generated when omitted
    target_type: TargetType
    target_id: str
    target_version: str
    risk_level: RiskLevel = RiskLevel.LOW
    capital_pool_id: Optional[str] = None
    persona_id: Optional[str] = None
    tenant_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    proposal_id: Optional[str] = Field(default=None, min_length=1)
    proposal_revision: Optional[int] = Field(default=None, ge=1)
    proposal_content_digest: Optional[str] = Field(default=None, min_length=1)
    validation_result_digest: Optional[str] = Field(default=None, min_length=1)
    session_id: Optional[str] = None
    candidate_digest: Optional[str] = None
    proof_digest: Optional[str] = None
    expires_at: Optional[str] = None


class AcceptReviewRequest(ApprovalCommand):
    actor_role: ActorRole
    actor_id: str


class DecideRequest(ApprovalCommand):
    actor_role: ActorRole
    outcome: DecisionOutcome
    rationale: str
    actor_id: str
    conditions: Optional[List[str]] = None
    evidence_refs: Optional[List[EvidenceRefBody]] = None
    session_id: Optional[str] = None
    candidate_digest: Optional[str] = None
    proof_digest: Optional[str] = None
    expires_at: Optional[str] = None


class RevokeRequest(ApprovalCommand):
    actor_role: ActorRole
    actor_id: str


class AuthzCheckRequest(BaseModel):
    action: str
    actor_id: str
    actor_roles: List[str]
    resource: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)


class AuthzCheckResponse(BaseModel):
    allowed: bool
    reason: str
    policy_version: str


# ---------------------------------------------------------------------------
# Response bodies
# ---------------------------------------------------------------------------

class ApprovalDecisionResponse(BaseModel):
    decision_id: str
    target_type: str
    target_id: str
    target_version: str
    decision: Optional[str]
    decision_state: str
    actor_role: Optional[str]
    actor_id: Optional[str]
    rationale: Optional[str]
    created_at: str
    decided_at: Optional[str]
    conditions: List[str]
    risk_level: str
    evidence_refs: List[Dict[str, Any]]
    superseded_by: Optional[str]
    expires_at: Optional[str]
    capital_pool_id: Optional[str]
    persona_id: Optional[str]
    metadata: Optional[Dict[str, Any]]
    tenant_id: str
    owner_user_id: str
    proposal_id: Optional[str]
    proposal_revision: Optional[int]
    proposal_content_digest: Optional[str]
    validation_result_digest: Optional[str]
    revoked_at: Optional[str]
    session_id: Optional[str] = None
    candidate_digest: Optional[str] = None
    proof_digest: Optional[str] = None
    controller_record_ref: Optional[str] = None
    recorded_at: Optional[str] = None
    authority_status: Optional[str] = None
    version: int
    event_id: str


class WriteAuthorityEntry(BaseModel):
    risk_level: str
    authorized_roles: List[str]
    revoke_roles: List[str]


class WriteAuthorityResponse(BaseModel):
    matrix: List[WriteAuthorityEntry]
    description: str


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
