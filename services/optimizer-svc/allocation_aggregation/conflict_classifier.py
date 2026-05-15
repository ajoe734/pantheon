"""Store-backed conflict explanation surface for allocation aggregation."""

from __future__ import annotations

from typing import Sequence

from portfolio_synthesis import (
    AllocationConflictReport,
    PersonaAllocationProposal,
    classify_allocation_conflicts,
)

from .proposal_store import PersonaAllocationProposalJsonlStore


def explain_conflicts(
    proposal_ids: Sequence[str],
    *,
    store: PersonaAllocationProposalJsonlStore | None = None,
    weight_spread_threshold: float = 0.05,
    high_conviction_threshold: float = 0.7,
    sponsor_ambiguity_ratio: float = 0.05,
    is_high_importance_pool: bool = False,
) -> AllocationConflictReport:
    """Replay proposal snapshots and classify allocation conflicts.

    This is the v1 function named by the Management Console OODA supplemental
    design. It returns a structured report rather than mutating proposal state.
    """

    target_store = store or PersonaAllocationProposalJsonlStore()
    proposals = target_store.require_proposals(proposal_ids)
    return classify_allocation_conflicts(
        proposals,
        weight_spread_threshold=weight_spread_threshold,
        high_conviction_threshold=high_conviction_threshold,
        sponsor_ambiguity_ratio=sponsor_ambiguity_ratio,
        is_high_importance_pool=is_high_importance_pool,
    )


__all__ = [
    "AllocationConflictReport",
    "PersonaAllocationProposal",
    "classify_allocation_conflicts",
    "explain_conflicts",
]
