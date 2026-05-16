# portfolio_synthesis — allocation-aggregator domain module
#
# v1: internal module of optimizer-svc (not a standalone deployable service).
# Implements multi-persona proposal aggregation, conflict resolution, weighted fusion,
# sponsor selection, and canonical AllocationPolicyArtifact production.
#
# Public surface:
#   PersonaAllocationProposal  — input from one advisor persona
#   AllocationPolicyArtifact   — canonical synthesis output (one per scope)
#   ConflictResolutionLog      — governance evidence record
#   PortfolioSynthesizer       — main aggregator
#
# Canonical spec: MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md
from .models import (
    PersonaAllocationProposal,
    ProposalDirection,
    ProposalTargetType,
    AllocationPolicyArtifact,
    ConflictResolutionLog,
    SynthesisMethod,
    VetoReason,
    VetoRecord,
    CommitteeReferral,
    SynthesisError,
    validate_allocation_policy_artifact_json,
    validate_persona_allocation_proposal_json,
)
from .conflict_classifier import (
    AllocationConflict,
    AllocationConflictReport,
    AllocationConflictType,
    ConflictSeverity,
    classify_allocation_conflicts,
)
from .synthesizer import PoolRiskPolicy, PortfolioSynthesizer

__all__ = [
    "PersonaAllocationProposal",
    "ProposalDirection",
    "ProposalTargetType",
    "AllocationPolicyArtifact",
    "ConflictResolutionLog",
    "SynthesisMethod",
    "VetoReason",
    "VetoRecord",
    "CommitteeReferral",
    "SynthesisError",
    "AllocationConflict",
    "AllocationConflictReport",
    "AllocationConflictType",
    "ConflictSeverity",
    "PoolRiskPolicy",
    "PortfolioSynthesizer",
    "classify_allocation_conflicts",
    "validate_allocation_policy_artifact_json",
    "validate_persona_allocation_proposal_json",
]
