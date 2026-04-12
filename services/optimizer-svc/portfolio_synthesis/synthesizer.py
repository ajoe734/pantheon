"""
portfolio_synthesis.synthesizer
--------------------------------
PortfolioSynthesizer — multi-persona allocation aggregation engine.

Implements the arbitration pipeline defined in
MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md:

  Priority order (§9):
    1. pool risk policy          (hard veto)
    2. governance hard rules     (hard veto)
    3. committee override        (escalation gate)
    4. weighted fusion           (automated synthesis)
    5. persona suggestions       (input only)

Usage
-----
    synthesizer = PortfolioSynthesizer(
        pool_risk_policy=my_policy,
        committee_escalation_fn=my_escalation_fn,   # optional
    )
    result = synthesizer.synthesize(proposals, capital_pool_id, scope_ref)
    # result is AllocationPolicyArtifact or CommitteeReferral
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .models import (
    AllocationPolicyArtifact,
    CommitteeReferral,
    ConflictResolutionLog,
    PersonaAllocationProposal,
    SynthesisError,
    SynthesisMethod,
    VetoRecord,
    VetoReason,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# PoolRiskPolicy — injectable governance contract
# ---------------------------------------------------------------------------

class PoolRiskPolicy:
    """
    Holds the hard-veto rules for a capital pool.

    Callers may subclass or construct this directly.

    Parameters
    ----------
    forbidden_asset_classes     : set of strings to block (e.g. {'crypto'})
    forbidden_strategy_families : set of strings to block (e.g. {'leveraged_short'})
    max_single_weight           : maximum allowed weight for any single symbol (default 1.0)
    custom_veto_fn              : optional callable(proposal) -> Optional[str] returning
                                  a veto reason string, or None to allow.
    """

    def __init__(
        self,
        forbidden_asset_classes: Optional[set] = None,
        forbidden_strategy_families: Optional[set] = None,
        max_single_weight: float = 1.0,
        custom_veto_fn: Optional[Callable[[PersonaAllocationProposal], Optional[str]]] = None,
    ) -> None:
        self.forbidden_asset_classes = forbidden_asset_classes or set()
        self.forbidden_strategy_families = forbidden_strategy_families or set()
        self.max_single_weight = max_single_weight
        self.custom_veto_fn = custom_veto_fn

    def veto_reason(self, proposal: PersonaAllocationProposal) -> Optional[str]:
        """
        Return a VetoReason string if this proposal should be hard-vetoed,
        or None if it passes.
        """
        # Asset classes may be projected into metadata by upstream proposal normalization.
        asset_classes = proposal.metadata.get("asset_classes", [])
        for asset_class in asset_classes:
            if asset_class in self.forbidden_asset_classes:
                return VetoReason.FORBIDDEN_ASSET_CLASS.value
        # Check target_weights for single-position limit
        for sym, w in proposal.target_weights.items():
            if w > self.max_single_weight:
                return VetoReason.POOL_RISK_POLICY.value
        # Check forbidden strategy families in proposal metadata
        strat_family = proposal.metadata.get("strategy_family", "")
        if strat_family in self.forbidden_strategy_families:
            return VetoReason.FORBIDDEN_STRATEGY_FAMILY.value
        # Custom veto logic
        if self.custom_veto_fn is not None:
            return self.custom_veto_fn(proposal)
        return None


# ---------------------------------------------------------------------------
# Committee escalation conditions (§6.3)
# ---------------------------------------------------------------------------

def _needs_committee_escalation(
    proposals: List[PersonaAllocationProposal],
    is_high_importance_pool: bool = False,
) -> Tuple[bool, str]:
    """
    Evaluate whether the surviving proposals require committee escalation.

    Returns (should_escalate, trigger_reason).
    """
    if not proposals:
        return False, ""

    # Condition: long vs short direction conflict with both high conviction
    has_long = any("long" in p.directions for p in proposals)
    has_short = any("short" in p.directions for p in proposals)
    high_conviction_threshold = 0.7
    if has_long and has_short:
        long_max_conv = max(
            (p.conviction for p in proposals if "long" in p.directions), default=0.0
        )
        short_max_conv = max(
            (p.conviction for p in proposals if "short" in p.directions), default=0.0
        )
        if long_max_conv >= high_conviction_threshold and short_max_conv >= high_conviction_threshold:
            return True, "long_vs_short_high_conviction_conflict"

    # Condition: risk posture conflict — one wants to add leverage, another to reduce risk
    leveraged = any(p.metadata.get("wants_leverage") for p in proposals)
    de_risk = any(p.metadata.get("wants_de_risk") for p in proposals)
    if leveraged and de_risk:
        return True, "risk_posture_conflict"

    # Condition: first canary/live deployment of a high-risk strategy family
    high_risk_first_deployment = any(
        p.scope_ref in {"canary", "live"}
        and p.metadata.get("strategy_family_risk") == "high"
        and p.metadata.get("first_deployment_in_scope")
        for p in proposals
    )
    if high_risk_first_deployment:
        return True, "high_risk_first_deployment"

    # Condition: sponsor is ambiguous (no proposal clearly dominates)
    weights = [p.effective_weight for p in proposals]
    if len(weights) >= 2:
        max_w = max(weights)
        second_max = sorted(weights, reverse=True)[1]
        # If top two effective weights are within 5% of each other, sponsor is ambiguous
        if max_w > 0 and (max_w - second_max) / max_w < 0.05:
            return True, "sponsor_ambiguous"

    # Condition: high-importance pool
    if is_high_importance_pool:
        return True, "high_importance_pool"

    return False, ""


# ---------------------------------------------------------------------------
# PortfolioSynthesizer
# ---------------------------------------------------------------------------

class PortfolioSynthesizer:
    """
    Multi-persona allocation aggregator for a capital pool.

    This module lives inside optimizer-svc as an internal domain module (v1).
    It does NOT solve allocations — the optimizer layer does that. This module
    arbitrates between competing persona proposals and produces the single
    canonical AllocationPolicyArtifact that the optimizer then solves within.

    Pipeline (per canonical spec):
      1. Validate inputs
      2. Apply hard veto (pool risk policy + governance rules)
      3. Check committee escalation conditions
      4. Weighted fusion of surviving proposals
      5. Select sponsor (highest effective_weight post-fusion)
      6. Produce AllocationPolicyArtifact + ConflictResolutionLog

    Parameters
    ----------
    pool_risk_policy        : PoolRiskPolicy — governs hard veto rules.
    is_high_importance_pool : bool — triggers mandatory committee escalation.
    committee_escalation_fn : optional callable for committee referral side-effects.
                              Signature: (CommitteeReferral) -> None
    """

    def __init__(
        self,
        pool_risk_policy: Optional[PoolRiskPolicy] = None,
        is_high_importance_pool: bool = False,
        committee_escalation_fn: Optional[Callable[[CommitteeReferral], None]] = None,
    ) -> None:
        self._policy = pool_risk_policy or PoolRiskPolicy()
        self._is_high_importance = is_high_importance_pool
        self._escalation_fn = committee_escalation_fn
        self._last_conflict_resolution_log: Optional[ConflictResolutionLog] = None

    @property
    def last_conflict_resolution_log(self) -> Optional[ConflictResolutionLog]:
        return self._last_conflict_resolution_log

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def synthesize_with_log(
        self,
        proposals: List[PersonaAllocationProposal],
        capital_pool_id: str,
        scope_ref: str,
        constraints_bundle: Optional[Dict[str, Any]] = None,
        risk_budget: Optional[float] = None,
    ) -> Tuple[Union[AllocationPolicyArtifact, CommitteeReferral], ConflictResolutionLog]:
        """
        Aggregate proposals and return both the outcome and the generated
        ConflictResolutionLog.
        """
        result = self.synthesize(
            proposals=proposals,
            capital_pool_id=capital_pool_id,
            scope_ref=scope_ref,
            constraints_bundle=constraints_bundle,
            risk_budget=risk_budget,
        )
        if self._last_conflict_resolution_log is None:
            raise SynthesisError("ConflictResolutionLog was not recorded for this synthesis run")
        return result, self._last_conflict_resolution_log

    def synthesize(
        self,
        proposals: List[PersonaAllocationProposal],
        capital_pool_id: str,
        scope_ref: str,
        constraints_bundle: Optional[Dict[str, Any]] = None,
        risk_budget: Optional[float] = None,
    ) -> Union[AllocationPolicyArtifact, CommitteeReferral]:
        """
        Aggregate proposals into one canonical AllocationPolicyArtifact.

        Returns
        -------
        AllocationPolicyArtifact  if synthesis succeeds.
        CommitteeReferral         if proposals must be escalated to committee.

        Raises
        ------
        SynthesisError  if all proposals are vetoed and committee escalation
                        is not configured.
        """
        if not proposals:
            raise SynthesisError("synthesize() requires at least one proposal")

        self._last_conflict_resolution_log = None

        # Validate all proposals target the same pool and scope
        for p in proposals:
            if p.capital_pool_id != capital_pool_id:
                raise SynthesisError(
                    f"Proposal {p.proposal_id} targets pool {p.capital_pool_id!r}, "
                    f"expected {capital_pool_id!r}"
                )
            if p.scope_ref != scope_ref:
                raise SynthesisError(
                    f"Proposal {p.proposal_id} has scope_ref {p.scope_ref!r}, "
                    f"expected {scope_ref!r}"
                )

        timestamp = _utc_now()
        log_id = _new_id()

        # ------ Step 1: Hard veto ------
        vetoed: List[VetoRecord] = []
        surviving: List[PersonaAllocationProposal] = []
        for p in proposals:
            reason = self._policy.veto_reason(p)
            if reason is not None:
                vetoed.append(VetoRecord(
                    proposal_id=p.proposal_id,
                    persona_id=p.persona_id,
                    reason=reason,
                ))
            else:
                surviving.append(p)

        # ------ Step 2: Handle all-vetoed case ------
        if not surviving:
            log = self._build_log(
                log_id=log_id,
                capital_pool_id=capital_pool_id,
                scope_ref=scope_ref,
                timestamp=timestamp,
                all_proposals=proposals,
                vetoed=vetoed,
                surviving=[],
                raw_weights={},
                normalised_weights={},
                sponsor=None,
                synthesis_method=None,
                rejected_reason="all_proposals_vetoed",
            )
            self._last_conflict_resolution_log = log
            raise SynthesisError(
                f"All {len(vetoed)} proposal(s) were hard-vetoed for pool "
                f"{capital_pool_id!r} scope {scope_ref!r}. "
                f"ConflictResolutionLog id={log.log_id}. "
                f"Reasons: {[v.reason for v in vetoed]}"
            )

        # ------ Step 3: Committee escalation check ------
        should_escalate, escalation_reason = _needs_committee_escalation(
            surviving, is_high_importance_pool=self._is_high_importance
        )
        if should_escalate:
            referral = CommitteeReferral(
                referral_id=_new_id(),
                capital_pool_id=capital_pool_id,
                scope_ref=scope_ref,
                proposal_ids=[p.proposal_id for p in surviving],
                trigger_reason=escalation_reason,
                created_at=timestamp,
            )
            log = self._build_log(
                log_id=log_id,
                capital_pool_id=capital_pool_id,
                scope_ref=scope_ref,
                timestamp=timestamp,
                all_proposals=proposals,
                vetoed=vetoed,
                surviving=surviving,
                raw_weights={p.proposal_id: p.effective_weight for p in surviving},
                normalised_weights={},
                sponsor=None,
                synthesis_method=SynthesisMethod.COMMITTEE_OVERRIDE.value,
                committee_ref=referral.referral_id,
            )
            self._last_conflict_resolution_log = log
            if self._escalation_fn is not None:
                self._escalation_fn(referral)
            return referral

        # ------ Step 4: Weighted fusion ------
        raw_weights = {p.proposal_id: p.effective_weight for p in surviving}
        total_w = sum(raw_weights.values())

        if total_w == 0.0:
            # All surviving proposals have zero effective weight — treat as equal
            total_w = float(len(surviving))
            raw_weights = {p.proposal_id: 1.0 for p in surviving}

        normalised_weights = {pid: w / total_w for pid, w in raw_weights.items()}

        # Fuse target_weights: weighted average across proposals
        fused_weights: Dict[str, float] = {}
        for p in surviving:
            share = normalised_weights[p.proposal_id]
            for sym, w in p.target_weights.items():
                fused_weights[sym] = fused_weights.get(sym, 0.0) + w * share

        # Renormalise fused weights to [0,1] if needed
        total_fused = sum(fused_weights.values())
        if total_fused > 1.0 + 1e-9:
            fused_weights = {sym: w / total_fused for sym, w in fused_weights.items()}

        # ------ Step 5: Sponsor selection ------
        # Sponsor is the persona with the highest normalised fusion share
        sponsor_proposal = max(surviving, key=lambda p: normalised_weights[p.proposal_id])
        sponsor_persona_id = sponsor_proposal.persona_id

        synthesis_method = (
            SynthesisMethod.SINGLE_PROPOSAL.value
            if len(surviving) == 1
            else SynthesisMethod.WEIGHTED_FUSION.value
        )

        # ------ Step 6: Build log + artifact ------
        log = self._build_log(
            log_id=log_id,
            capital_pool_id=capital_pool_id,
            scope_ref=scope_ref,
            timestamp=timestamp,
            all_proposals=proposals,
            vetoed=vetoed,
            surviving=surviving,
            raw_weights=raw_weights,
            normalised_weights=normalised_weights,
            sponsor=sponsor_persona_id,
            synthesis_method=synthesis_method,
        )
        self._last_conflict_resolution_log = log

        artifact = AllocationPolicyArtifact(
            artifact_id=_new_id(),
            capital_pool_id=capital_pool_id,
            scope_ref=scope_ref,
            sponsor_persona_id=sponsor_persona_id,
            synthesis_method=synthesis_method,
            target_weights=fused_weights,
            created_at=timestamp,
            constraints_bundle=constraints_bundle or {},
            risk_budget=risk_budget,
            provenance_refs=[p.proposal_id for p in proposals],
            conflict_resolution_log_id=log.log_id,
        )

        return artifact

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_log(
        self,
        log_id: str,
        capital_pool_id: str,
        scope_ref: str,
        timestamp: str,
        all_proposals: List[PersonaAllocationProposal],
        vetoed: List[VetoRecord],
        surviving: List[PersonaAllocationProposal],
        raw_weights: Dict[str, float],
        normalised_weights: Dict[str, float],
        sponsor: Optional[str],
        synthesis_method: Optional[str],
        rejected_reason: Optional[str] = None,
        committee_ref: Optional[str] = None,
    ) -> ConflictResolutionLog:
        return ConflictResolutionLog(
            log_id=log_id,
            capital_pool_id=capital_pool_id,
            scope_ref=scope_ref,
            timestamp=timestamp,
            proposal_ids=[p.proposal_id for p in all_proposals],
            vetoed_proposals=vetoed,
            weighting_inputs=raw_weights,
            weighting_outputs=normalised_weights,
            committee_ref=committee_ref,
            sponsor_persona_id=sponsor,
            rejected_reason=rejected_reason,
            synthesis_method=synthesis_method,
        )
