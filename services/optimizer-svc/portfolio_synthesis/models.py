"""
portfolio_synthesis.models
--------------------------
Data objects for multi-persona allocation aggregation.

Canonical spec: MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SynthesisMethod(str, Enum):
    WEIGHTED_FUSION = "weighted_fusion"
    COMMITTEE_OVERRIDE = "committee_override"
    SINGLE_PROPOSAL = "single_proposal"     # only one non-vetoed proposal


class VetoReason(str, Enum):
    POOL_RISK_POLICY = "pool_risk_policy"
    FORBIDDEN_ASSET_CLASS = "forbidden_asset_class"
    FORBIDDEN_STRATEGY_FAMILY = "forbidden_strategy_family"
    GOVERNANCE_PROHIBITION = "governance_prohibition"
    COMPLIANCE_BLOCK = "compliance_block"


class SynthesisError(ValueError):
    """Raised when synthesis cannot produce a valid artifact."""


# ---------------------------------------------------------------------------
# Input: PersonaAllocationProposal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PersonaAllocationProposal:
    """
    Standardised proposal from one advisor persona for a capital pool.

    Fields
    ------
    proposal_id     : unique identifier for this proposal
    persona_id      : proposing persona
    capital_pool_id : target pool
    scope_ref       : deployment scope reference (e.g. 'live', 'paper')
    target_type     : 'asset' | 'sleeve' | 'basket' | 'pool'
    directions      : list of direction hints (e.g. ['long', 'short'])
    target_weights  : mapping of symbol -> weight (must sum to ≤1.0)
    conviction      : [0.0, 1.0] confidence in this proposal
    uncertainty     : [0.0, 1.0] uncertainty estimate
    rationale_ref   : opaque reference to supporting rationale artifact
    regime_ref      : opaque reference to regime classification used
    valid_from      : ISO-8601 UTC start of validity
    valid_to        : ISO-8601 UTC end of validity (None = open)
    reliability_score   : recent track-record score [0.0, 1.0]
    regime_fit_score    : how well the proposal fits the current regime [0.0, 1.0]
    governance_multiplier : pool-level factor applied during fusion [0.0, 2.0]
    metadata        : arbitrary consumer metadata
    """
    proposal_id: str
    persona_id: str
    capital_pool_id: str
    scope_ref: str

    target_type: str = "pool"
    directions: List[str] = field(default_factory=list)
    target_weights: Dict[str, float] = field(default_factory=dict)

    conviction: float = 0.5
    uncertainty: float = 0.0
    rationale_ref: Optional[str] = None
    regime_ref: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None

    reliability_score: float = 1.0
    regime_fit_score: float = 1.0
    governance_multiplier: float = 1.0

    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for attr, lo, hi in [
            ("conviction", 0.0, 1.0),
            ("uncertainty", 0.0, 1.0),
            ("reliability_score", 0.0, 1.0),
            ("regime_fit_score", 0.0, 1.0),
            ("governance_multiplier", 0.0, 2.0),
        ]:
            v = getattr(self, attr)
            if not (lo <= v <= hi):
                raise SynthesisError(f"{attr} must be in [{lo}, {hi}], got {v}")
        total = sum(self.target_weights.values())
        if self.target_weights and total > 1.0 + 1e-9:
            raise SynthesisError(
                f"target_weights sum {total:.4f} > 1.0 for proposal {self.proposal_id}"
            )

    @property
    def effective_weight(self) -> float:
        """
        Compute the governance-adjusted fusion weight for this proposal.

        Formula (from canonical spec §6.2):
            reliability_score * regime_fit_score * conviction
            * (1 - uncertainty) * governance_multiplier
        """
        return (
            self.reliability_score
            * self.regime_fit_score
            * self.conviction
            * (1.0 - self.uncertainty)
            * self.governance_multiplier
        )


# ---------------------------------------------------------------------------
# Governance veto record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VetoRecord:
    """Records a single hard-veto decision against a proposal."""
    proposal_id: str
    persona_id: str
    reason: str        # VetoReason value or custom string
    detail: str = ""


# ---------------------------------------------------------------------------
# Committee referral
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommitteeReferral:
    """
    Produced when synthesis cannot resolve conflict autonomously.
    The synthesizer halts and returns this object instead of an artifact.

    Fields
    ------
    referral_id     : unique ID for this referral
    capital_pool_id : pool under arbitration
    scope_ref       : deployment scope
    proposal_ids    : proposals submitted to committee
    trigger_reason  : human-readable reason for escalation
    created_at      : ISO-8601 UTC timestamp
    """
    referral_id: str
    capital_pool_id: str
    scope_ref: str
    proposal_ids: List[str]
    trigger_reason: str
    created_at: str = field(default_factory=_utc_now)


# ---------------------------------------------------------------------------
# Output: ConflictResolutionLog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConflictResolutionLog:
    """
    Structured governance evidence record produced with every synthesis run.

    Required per canonical spec §10.
    """
    log_id: str
    capital_pool_id: str
    scope_ref: str
    timestamp: str

    proposal_ids: List[str] = field(default_factory=list)
    vetoed_proposals: List[VetoRecord] = field(default_factory=list)
    weighting_inputs: Dict[str, float] = field(default_factory=dict)   # proposal_id -> effective_weight
    weighting_outputs: Dict[str, float] = field(default_factory=dict)  # proposal_id -> normalised_share
    committee_ref: Optional[str] = None     # CommitteeReferral.referral_id if escalated
    sponsor_persona_id: Optional[str] = None
    rejected_reason: Optional[str] = None   # set when all proposals are vetoed/rejected
    synthesis_method: Optional[str] = None


# ---------------------------------------------------------------------------
# Output: AllocationPolicyArtifact
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AllocationPolicyArtifact:
    """
    Canonical synthesis output — one per capital pool per scope.

    Produced by PortfolioSynthesizer after weighted fusion or committee override.
    Must be the sole artifact sent into execution for this pool/scope.
    """
    artifact_id: str
    capital_pool_id: str
    scope_ref: str
    sponsor_persona_id: str
    synthesis_method: str
    target_weights: Dict[str, float]
    created_at: str

    constraints_bundle: Dict[str, Any] = field(default_factory=dict)
    risk_budget: Optional[float] = None
    provenance_refs: List[str] = field(default_factory=list)       # proposal_ids
    conflict_resolution_log_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
