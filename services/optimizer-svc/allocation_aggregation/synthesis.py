"""Store-backed allocation synthesis method surface for optimizer-svc.

MGMT-SYN-004 owns this narrow facade. The arbitration engine stays in
``portfolio_synthesis``; this module gives the Management Console OODA surface
the v1 function shape named by the supplemental SD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from portfolio_synthesis import (
    AllocationPolicyArtifact,
    CommitteeReferral,
    ConflictResolutionLog,
    PoolRiskPolicy,
    PortfolioSynthesizer,
    SynthesisError,
    SynthesisMethod,
)

from .proposal_store import PersonaAllocationProposalJsonlStore


WEIGHTED_COMMITTEE_METHOD = "weighted_committee"
RISK_FIRST_METHOD = "risk_first"
MANUAL_OVERRIDE_METHOD = "manual_override"

_METHOD_ALIASES = {
    SynthesisMethod.WEIGHTED_FUSION.value: SynthesisMethod.WEIGHTED_FUSION.value,
    WEIGHTED_COMMITTEE_METHOD: SynthesisMethod.WEIGHTED_FUSION.value,
    RISK_FIRST_METHOD: SynthesisMethod.WEIGHTED_FUSION.value,
    SynthesisMethod.SINGLE_PROPOSAL.value: SynthesisMethod.SINGLE_PROPOSAL.value,
}
_MANUAL_METHODS = {
    SynthesisMethod.COMMITTEE_OVERRIDE.value,
    MANUAL_OVERRIDE_METHOD,
}
SUPPORTED_SYNTHESIS_METHODS = tuple([*_METHOD_ALIASES, *_MANUAL_METHODS])


@dataclass(frozen=True)
class AllocationSynthesisResult:
    """Artifact plus the required conflict log from one synthesis run."""

    artifact: AllocationPolicyArtifact
    conflict_resolution_log: ConflictResolutionLog
    requested_method: str
    resolved_method: str
    risk_policy_ref: str
    committee_override_ref: str | None = None


def synthesize_allocation(
    capital_pool_id: str,
    proposal_ids: Sequence[str],
    method: str,
    risk_policy_ref: str,
    committee_override_ref: str | None = None,
    *,
    store: PersonaAllocationProposalJsonlStore | None = None,
    scope_ref: str | None = None,
    pool_risk_policy: PoolRiskPolicy | None = None,
    constraints_bundle: Mapping[str, Any] | None = None,
    risk_budget: float | None = None,
    is_high_importance_pool: bool = False,
) -> AllocationPolicyArtifact:
    """Replay proposals and produce the single v1 AllocationPolicyArtifact.

    This is the SD-compatible convenience function. Call
    ``synthesize_allocation_with_log`` when the caller also needs the generated
    ConflictResolutionLog object in-process.
    """

    return synthesize_allocation_with_log(
        capital_pool_id=capital_pool_id,
        proposal_ids=proposal_ids,
        method=method,
        risk_policy_ref=risk_policy_ref,
        committee_override_ref=committee_override_ref,
        store=store,
        scope_ref=scope_ref,
        pool_risk_policy=pool_risk_policy,
        constraints_bundle=constraints_bundle,
        risk_budget=risk_budget,
        is_high_importance_pool=is_high_importance_pool,
    ).artifact


def synthesize_allocation_with_log(
    capital_pool_id: str,
    proposal_ids: Sequence[str],
    method: str,
    risk_policy_ref: str,
    committee_override_ref: str | None = None,
    *,
    store: PersonaAllocationProposalJsonlStore | None = None,
    scope_ref: str | None = None,
    pool_risk_policy: PoolRiskPolicy | None = None,
    constraints_bundle: Mapping[str, Any] | None = None,
    risk_budget: float | None = None,
    is_high_importance_pool: bool = False,
) -> AllocationSynthesisResult:
    """Replay proposal snapshots and run the v1 risk-first synthesis method."""

    proposal_id_list = _require_proposal_ids(proposal_ids)
    normalized_method = _normalize_method(method, committee_override_ref)
    risk_policy_ref = _require_text(risk_policy_ref, "risk_policy_ref")

    if normalized_method == SynthesisMethod.SINGLE_PROPOSAL.value and len(proposal_id_list) != 1:
        raise SynthesisError("single_proposal synthesis requires exactly one proposal_id")

    target_store = store or PersonaAllocationProposalJsonlStore()
    proposals = target_store.require_proposals(proposal_id_list)
    synthesis_scope_ref = scope_ref or _infer_single_scope_ref(proposals)

    effective_constraints = dict(constraints_bundle or {})
    effective_constraints.setdefault("risk_policy_ref", risk_policy_ref)
    effective_constraints.setdefault("requested_synthesis_method", method)
    effective_constraints.setdefault("resolved_synthesis_method", normalized_method)
    if committee_override_ref is not None:
        effective_constraints.setdefault("committee_override_ref", committee_override_ref)

    synthesizer = PortfolioSynthesizer(
        pool_risk_policy=pool_risk_policy or PoolRiskPolicy(),
        is_high_importance_pool=is_high_importance_pool,
    )
    outcome, log = synthesizer.synthesize_with_log(
        proposals,
        capital_pool_id=capital_pool_id,
        scope_ref=synthesis_scope_ref,
        constraints_bundle=effective_constraints,
        risk_budget=risk_budget,
    )

    if isinstance(outcome, CommitteeReferral):
        raise SynthesisError(
            "synthesize_allocation() produced a committee referral instead of an "
            f"AllocationPolicyArtifact: {outcome.trigger_reason}. "
            "v1 requires a committee-selected target allocation before a manual "
            "override can emit an artifact."
        )

    if (
        normalized_method == SynthesisMethod.SINGLE_PROPOSAL.value
        and outcome.synthesis_method != SynthesisMethod.SINGLE_PROPOSAL.value
    ):
        raise SynthesisError(
            f"single_proposal synthesis resolved to unexpected method {outcome.synthesis_method!r}"
        )

    return AllocationSynthesisResult(
        artifact=outcome,
        conflict_resolution_log=log,
        requested_method=method,
        resolved_method=normalized_method,
        risk_policy_ref=risk_policy_ref,
        committee_override_ref=committee_override_ref,
    )


def _normalize_method(method: str, committee_override_ref: str | None) -> str:
    method = _require_text(method, "method")
    if method in _MANUAL_METHODS:
        if not committee_override_ref:
            raise SynthesisError(f"{method} requires committee_override_ref")
        raise SynthesisError(
            f"{method} requires committee-selected target weights; "
            "MGMT-SYN-004 v1 only supports risk-first weighted fusion and single proposal synthesis"
        )
    try:
        return _METHOD_ALIASES[method]
    except KeyError as exc:
        raise SynthesisError(
            f"Unsupported synthesis method {method!r}; expected one of {sorted(SUPPORTED_SYNTHESIS_METHODS)}"
        ) from exc


def _require_proposal_ids(proposal_ids: Sequence[str]) -> list[str]:
    if isinstance(proposal_ids, (str, bytes)) or not isinstance(proposal_ids, Sequence):
        raise SynthesisError("proposal_ids must be a non-empty sequence of proposal id strings")
    normalized = [_require_text(proposal_id, "proposal_id") for proposal_id in proposal_ids]
    if not normalized:
        raise SynthesisError("proposal_ids must contain at least one proposal id")
    if len(set(normalized)) != len(normalized):
        raise SynthesisError("proposal_ids entries must be unique")
    return normalized


def _infer_single_scope_ref(proposals: Sequence[Any]) -> str:
    scope_refs = {proposal.scope_ref for proposal in proposals}
    if len(scope_refs) != 1:
        raise SynthesisError(f"proposal_ids span multiple scope_ref values: {sorted(scope_refs)}")
    return next(iter(scope_refs))


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SynthesisError(f"{field_name} must be a non-empty string")
    return value.strip()


__all__ = [
    "AllocationSynthesisResult",
    "MANUAL_OVERRIDE_METHOD",
    "RISK_FIRST_METHOD",
    "SUPPORTED_SYNTHESIS_METHODS",
    "WEIGHTED_COMMITTEE_METHOD",
    "synthesize_allocation",
    "synthesize_allocation_with_log",
]
