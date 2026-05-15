"""Allocation aggregation storage surface for optimizer-svc.

The synthesis engine currently lives in ``portfolio_synthesis``. This package
holds the v1 durable proposal store named by the Management Console OODA
supplemental SD while reusing the existing synthesis dataclasses.
"""

from portfolio_synthesis import PersonaAllocationProposal

from .proposal_store import (
    DEFAULT_PROPOSAL_STORE_PATH,
    PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION,
    PersonaAllocationProposalJsonlStore,
    PersonaAllocationProposalQuery,
    PersonaAllocationProposalStoreError,
    ingest_persona_proposal,
)
from .conflict_classifier import (
    AllocationConflictReport,
    classify_allocation_conflicts,
    explain_conflicts,
)
from .synthesis import (
    AllocationSynthesisResult,
    SUPPORTED_SYNTHESIS_METHODS,
    synthesize_allocation,
    synthesize_allocation_with_log,
)

__all__ = [
    "DEFAULT_PROPOSAL_STORE_PATH",
    "PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION",
    "AllocationSynthesisResult",
    "AllocationConflictReport",
    "PersonaAllocationProposal",
    "PersonaAllocationProposalJsonlStore",
    "PersonaAllocationProposalQuery",
    "PersonaAllocationProposalStoreError",
    "SUPPORTED_SYNTHESIS_METHODS",
    "classify_allocation_conflicts",
    "explain_conflicts",
    "ingest_persona_proposal",
    "synthesize_allocation",
    "synthesize_allocation_with_log",
]
