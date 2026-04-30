"""
Pydantic request / response models for the Governance Service API.

These are wire-layer models only.  Business-logic enums and platform objects
live in services/control-plane/governance/approval_decision.py.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
    REGISTRY_ENTRY = "registry_entry"
    STRATEGY_SPEC = "strategy_spec"
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

class ProposeApprovalRequest(BaseModel):
    decision_id: Optional[str] = None   # auto-generated when omitted
    target_type: TargetType
    target_id: str
    target_version: str
    risk_level: RiskLevel = RiskLevel.LOW
    capital_pool_id: Optional[str] = None
    persona_id: Optional[str] = None


class AcceptReviewRequest(BaseModel):
    actor_role: ActorRole
    actor_id: str


class DecideRequest(BaseModel):
    actor_role: ActorRole
    outcome: DecisionOutcome
    rationale: str
    actor_id: str
    conditions: Optional[List[str]] = None
    evidence_refs: Optional[List[EvidenceRefBody]] = None


class RevokeRequest(BaseModel):
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
