"""Allocation proposal conflict classification.

MGMT-SYN-003 owns the deterministic classifier used before synthesis to
explain why a set of PersonaAllocationProposal records is clean, conflicting,
or requires committee handling.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Mapping, Sequence

from .models import PersonaAllocationProposal, SynthesisError


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AllocationConflictType(str, Enum):
    DIRECTION = "direction_conflict"
    WEIGHT = "weight_conflict"
    HORIZON = "horizon_conflict"
    RISK_POSTURE = "risk_posture_conflict"
    REGIME = "regime_conflict"
    HOMOGENEITY = "homogeneity_conflict"
    CORRELATION = "correlation_conflict"
    SPONSOR_AMBIGUITY = "sponsor_ambiguity"


class ConflictSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    COMMITTEE = "committee"


@dataclass(frozen=True)
class AllocationConflict:
    conflict_id: str
    conflict_type: str
    severity: str
    proposal_ids: list[str]
    summary: str
    target_ref: str | None = None
    committee_trigger: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AllocationConflictReport:
    report_id: str
    capital_pool_id: str
    scope_ref: str
    generated_at: str
    proposal_ids: list[str]
    conflicts: list[AllocationConflict]
    committee_triggers: list[str]
    sponsor_candidates: list[dict[str, Any]]
    primary_sponsor_persona_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def requires_committee(self) -> bool:
        return bool(self.committee_triggers)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["requires_committee"] = self.requires_committee
        return payload


POSITIVE_DIRECTIONS = {"long", "buy", "increase"}
NEGATIVE_DIRECTIONS = {"short", "sell", "decrease", "flat"}
RISK_ON_DIRECTIONS = {"risk_on", "add_leverage"}
RISK_OFF_DIRECTIONS = {"risk_off", "de_risk", "flat"}
RISK_ON_LABELS = {"risk_on", "leverage", "levered", "aggressive", "add_leverage"}
RISK_OFF_LABELS = {"risk_off", "de_risk", "defensive", "reduce_risk", "flat"}
HORIZON_METADATA_KEYS = ("horizon", "time_horizon", "holding_period", "investment_horizon")
REGIME_METADATA_KEYS = ("regime_label", "regime", "market_regime", "regime_view")
STRATEGY_FAMILY_METADATA_KEYS = ("strategy_family", "strategy_family_ref", "strategy")
CORRELATION_BUCKET_METADATA_KEYS = (
    "correlation_bucket",
    "signal_correlation_bucket",
    "portfolio_correlation_bucket",
)
SIGNAL_CORRELATION_METADATA_KEYS = (
    "signal_correlation",
    "portfolio_correlation",
    "max_pairwise_correlation",
    "correlation",
)
CORRELATION_MAPPING_KEYS = (
    "signal_correlations",
    "proposal_correlations",
    "pairwise_correlations",
    "correlations",
)
HIGH_CORRELATION_BUCKETS = {"high", "very_high", "extreme", "highly_correlated"}
DEFAULT_EXCLUDED_OVERLAP_TARGETS = {"cash", "usd", "usdc", "usdt"}


def classify_allocation_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    capital_pool_id: str | None = None,
    scope_ref: str | None = None,
    weight_spread_threshold: float = 0.05,
    high_conviction_threshold: float = 0.7,
    sponsor_ambiguity_ratio: float = 0.05,
    strategy_family_concentration_threshold: float = 0.65,
    target_overlap_threshold: float = 0.8,
    high_correlation_threshold: float = 0.8,
    is_high_importance_pool: bool = False,
) -> AllocationConflictReport:
    """Classify conflicts among proposals for one capital pool and scope.

    The classifier records the L1 conflict categories and committee escalation
    triggers without producing an AllocationPolicyArtifact. Synthesis remains a
    separate step.
    """

    proposal_list = list(proposals)
    if not proposal_list:
        raise SynthesisError("classify_allocation_conflicts() requires at least one proposal")

    expected_pool = capital_pool_id or proposal_list[0].capital_pool_id
    expected_scope = scope_ref or proposal_list[0].scope_ref
    for proposal in proposal_list:
        if proposal.capital_pool_id != expected_pool:
            raise SynthesisError(
                f"Proposal {proposal.proposal_id} targets pool {proposal.capital_pool_id!r}, "
                f"expected {expected_pool!r}"
            )
        if proposal.scope_ref != expected_scope:
            raise SynthesisError(
                f"Proposal {proposal.proposal_id} has scope_ref {proposal.scope_ref!r}, "
                f"expected {expected_scope!r}"
            )

    conflicts: list[AllocationConflict] = []
    committee_triggers: list[str] = []

    def add_trigger(trigger: str) -> None:
        if trigger not in committee_triggers:
            committee_triggers.append(trigger)

    def add_conflict(
        conflict_type: AllocationConflictType,
        severity: ConflictSeverity,
        proposal_ids: Sequence[str],
        summary: str,
        *,
        target_ref: str | None = None,
        committee_trigger: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if committee_trigger:
            add_trigger(committee_trigger)
        conflicts.append(
            AllocationConflict(
                conflict_id=f"{conflict_type.value}-{len(conflicts) + 1:03d}",
                conflict_type=conflict_type.value,
                severity=severity.value,
                proposal_ids=_ordered_unique(proposal_ids),
                summary=summary,
                target_ref=target_ref,
                committee_trigger=committee_trigger,
                details=dict(details or {}),
            )
        )

    _classify_direction_conflicts(
        proposal_list,
        high_conviction_threshold=high_conviction_threshold,
        add_conflict=add_conflict,
    )
    _classify_weight_conflicts(
        proposal_list,
        weight_spread_threshold=weight_spread_threshold,
        add_conflict=add_conflict,
    )
    _classify_horizon_conflicts(proposal_list, add_conflict=add_conflict)
    _classify_risk_posture_conflicts(
        proposal_list,
        high_conviction_threshold=high_conviction_threshold,
        add_conflict=add_conflict,
    )
    _classify_regime_conflicts(proposal_list, add_conflict=add_conflict)
    _classify_homogeneity_correlation_conflicts(
        proposal_list,
        strategy_family_concentration_threshold=strategy_family_concentration_threshold,
        target_overlap_threshold=target_overlap_threshold,
        high_correlation_threshold=high_correlation_threshold,
        add_conflict=add_conflict,
    )

    sponsor_candidates = _sponsor_candidates(proposal_list)
    primary_sponsor_persona_id: str | None = None
    if sponsor_candidates:
        top = sponsor_candidates[0]
        primary_sponsor_persona_id = str(top["persona_id"]) if top["effective_weight"] > 0.0 else None
    if len(sponsor_candidates) >= 2:
        top = sponsor_candidates[0]
        second = sponsor_candidates[1]
        top_weight = float(top["effective_weight"])
        second_weight = float(second["effective_weight"])
        if top_weight > 0.0 and (top_weight - second_weight) / top_weight < sponsor_ambiguity_ratio:
            primary_sponsor_persona_id = None
            add_conflict(
                AllocationConflictType.SPONSOR_AMBIGUITY,
                ConflictSeverity.COMMITTEE,
                [str(top["proposal_id"]), str(second["proposal_id"])],
                "Top sponsor candidates are within the ambiguity threshold.",
                committee_trigger="sponsor_ambiguous",
                details={
                    "top_candidate": top,
                    "second_candidate": second,
                    "sponsor_ambiguity_ratio": sponsor_ambiguity_ratio,
                },
            )

    if any(_is_high_risk_first_deployment(proposal) for proposal in proposal_list):
        add_trigger("high_risk_first_deployment")
    if is_high_importance_pool:
        add_trigger("high_importance_pool")

    return AllocationConflictReport(
        report_id=f"allocation-conflict-report-{uuid.uuid4()}",
        capital_pool_id=expected_pool,
        scope_ref=expected_scope,
        generated_at=_utc_now(),
        proposal_ids=[proposal.proposal_id for proposal in proposal_list],
        conflicts=conflicts,
        committee_triggers=committee_triggers,
        sponsor_candidates=sponsor_candidates,
        primary_sponsor_persona_id=primary_sponsor_persona_id,
        metadata={
            "weight_spread_threshold": weight_spread_threshold,
            "high_conviction_threshold": high_conviction_threshold,
            "sponsor_ambiguity_ratio": sponsor_ambiguity_ratio,
            "strategy_family_concentration_threshold": strategy_family_concentration_threshold,
            "target_overlap_threshold": target_overlap_threshold,
            "high_correlation_threshold": high_correlation_threshold,
            "is_high_importance_pool": is_high_importance_pool,
        },
    )


def _classify_direction_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    high_conviction_threshold: float,
    add_conflict: Any,
) -> None:
    positive = [proposal for proposal in proposals if set(proposal.directions) & POSITIVE_DIRECTIONS]
    negative = [proposal for proposal in proposals if set(proposal.directions) & NEGATIVE_DIRECTIONS]
    if not positive or not negative:
        return

    positive_conviction = max(proposal.conviction for proposal in positive)
    negative_conviction = max(proposal.conviction for proposal in negative)
    committee_trigger = None
    severity = ConflictSeverity.WARNING
    if positive_conviction >= high_conviction_threshold and negative_conviction >= high_conviction_threshold:
        committee_trigger = "long_vs_short_high_conviction_conflict"
        severity = ConflictSeverity.COMMITTEE

    add_conflict(
        AllocationConflictType.DIRECTION,
        severity,
        [proposal.proposal_id for proposal in [*positive, *negative]],
        "Proposals contain opposing allocation directions.",
        committee_trigger=committee_trigger,
        details={
            "positive_directions": _directions_by_proposal(positive, POSITIVE_DIRECTIONS),
            "negative_directions": _directions_by_proposal(negative, NEGATIVE_DIRECTIONS),
            "positive_max_conviction": positive_conviction,
            "negative_max_conviction": negative_conviction,
        },
    )


def _classify_weight_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    weight_spread_threshold: float,
    add_conflict: Any,
) -> None:
    target_refs = sorted({target for proposal in proposals for target in proposal.target_weights})
    for target_ref in target_refs:
        weights = {
            proposal.proposal_id: float(proposal.target_weights.get(target_ref, 0.0))
            for proposal in proposals
        }
        max_weight = max(weights.values())
        min_weight = min(weights.values())
        spread = max_weight - min_weight
        if max_weight <= 0.0 or spread < weight_spread_threshold:
            continue
        add_conflict(
            AllocationConflictType.WEIGHT,
            ConflictSeverity.WARNING,
            list(weights),
            f"Target {target_ref} has materially different requested weights.",
            target_ref=target_ref,
            details={
                "weights_by_proposal": weights,
                "spread": spread,
                "weight_spread_threshold": weight_spread_threshold,
            },
        )


def _classify_horizon_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    add_conflict: Any,
) -> None:
    grouped = _group_by_metadata_value(proposals, HORIZON_METADATA_KEYS)
    if len(grouped) <= 1:
        return
    add_conflict(
        AllocationConflictType.HORIZON,
        ConflictSeverity.WARNING,
        [proposal_id for proposal_ids in grouped.values() for proposal_id in proposal_ids],
        "Proposals cite incompatible investment horizons.",
        details={"horizons_by_label": grouped},
    )


def _classify_risk_posture_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    high_conviction_threshold: float,
    add_conflict: Any,
) -> None:
    risk_on = [proposal for proposal in proposals if "risk_on" in _risk_postures(proposal)]
    risk_off = [proposal for proposal in proposals if "risk_off" in _risk_postures(proposal)]
    if not risk_on or not risk_off:
        return

    risk_on_conviction = max(proposal.conviction for proposal in risk_on)
    risk_off_conviction = max(proposal.conviction for proposal in risk_off)
    explicit_major = any(_truthy(proposal.metadata.get("wants_leverage")) for proposal in risk_on) and any(
        _truthy(proposal.metadata.get("wants_de_risk")) for proposal in risk_off
    )
    high_conviction = (
        risk_on_conviction >= high_conviction_threshold
        and risk_off_conviction >= high_conviction_threshold
    )
    committee_trigger = "risk_posture_conflict" if explicit_major or high_conviction else None
    severity = ConflictSeverity.COMMITTEE if committee_trigger else ConflictSeverity.WARNING

    add_conflict(
        AllocationConflictType.RISK_POSTURE,
        severity,
        [proposal.proposal_id for proposal in [*risk_on, *risk_off]],
        "Proposals disagree on risk posture.",
        committee_trigger=committee_trigger,
        details={
            "risk_on_proposals": [proposal.proposal_id for proposal in risk_on],
            "risk_off_proposals": [proposal.proposal_id for proposal in risk_off],
            "risk_on_max_conviction": risk_on_conviction,
            "risk_off_max_conviction": risk_off_conviction,
        },
    )


def _classify_regime_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    add_conflict: Any,
) -> None:
    grouped: dict[str, list[str]] = {}
    for proposal in proposals:
        label = _metadata_value(proposal.metadata, REGIME_METADATA_KEYS) or proposal.regime_ref
        normalized = _normalize_label(label)
        if normalized:
            grouped.setdefault(normalized, []).append(proposal.proposal_id)
    if len(grouped) <= 1:
        return
    add_conflict(
        AllocationConflictType.REGIME,
        ConflictSeverity.WARNING,
        [proposal_id for proposal_ids in grouped.values() for proposal_id in proposal_ids],
        "Proposals cite incompatible regime views.",
        details={"regimes_by_label": grouped},
    )


def _classify_homogeneity_correlation_conflicts(
    proposals: Sequence[PersonaAllocationProposal],
    *,
    strategy_family_concentration_threshold: float,
    target_overlap_threshold: float,
    high_correlation_threshold: float,
    add_conflict: Any,
) -> None:
    if len(proposals) < 2:
        return

    total_effective_weight = sum(float(proposal.effective_weight) for proposal in proposals)
    grouped: dict[str, list[PersonaAllocationProposal]] = {}
    for proposal in proposals:
        family = _strategy_family(proposal)
        if family:
            grouped.setdefault(family, []).append(proposal)

    for family, family_proposals in grouped.items():
        if len(family_proposals) < 2:
            continue

        concentration = _strategy_family_concentration(
            family_proposals,
            all_proposals=proposals,
            total_effective_weight=total_effective_weight,
        )
        pairwise = _pairwise_homogeneity_metrics(family_proposals)
        high_overlap = pairwise["max_target_overlap"] >= target_overlap_threshold
        high_numeric_correlation = (
            pairwise["max_signal_correlation"] is not None
            and pairwise["max_signal_correlation"] >= high_correlation_threshold
        )
        high_bucket = bool(pairwise["high_correlation_bucket"])
        concentrated = concentration >= strategy_family_concentration_threshold
        committee_trigger = (
            "homogeneity_correlation_review"
            if concentrated and (high_overlap or high_numeric_correlation or high_bucket)
            else None
        )
        severity = ConflictSeverity.COMMITTEE if committee_trigger else ConflictSeverity.WARNING
        proposal_ids = [proposal.proposal_id for proposal in family_proposals]
        base_details = {
            "strategy_family": family,
            "family_proposal_ids": proposal_ids,
            "strategy_family_concentration": concentration,
            "strategy_family_concentration_threshold": strategy_family_concentration_threshold,
            "max_target_overlap": pairwise["max_target_overlap"],
            "target_overlap_threshold": target_overlap_threshold,
            "max_signal_correlation": pairwise["max_signal_correlation"],
            "high_correlation_threshold": high_correlation_threshold,
            "correlation_buckets": pairwise["correlation_buckets"],
            "max_overlap_pair": pairwise["max_overlap_pair"],
            "max_correlation_pair": pairwise["max_correlation_pair"],
        }

        add_conflict(
            AllocationConflictType.HOMOGENEITY,
            severity,
            proposal_ids,
            "Proposals concentrate the capital pool in duplicate strategy-family exposure.",
            committee_trigger=committee_trigger if high_overlap else None,
            details=base_details,
        )

        if high_numeric_correlation or high_bucket:
            add_conflict(
                AllocationConflictType.CORRELATION,
                severity,
                proposal_ids,
                "Proposals cite highly correlated signals within the same strategy family.",
                committee_trigger=committee_trigger,
                details=base_details,
            )


def _sponsor_candidates(proposals: Sequence[PersonaAllocationProposal]) -> list[dict[str, Any]]:
    raw = {proposal.proposal_id: float(proposal.effective_weight) for proposal in proposals}
    total = sum(raw.values())
    if total > 0.0:
        shares = {proposal_id: weight / total for proposal_id, weight in raw.items()}
    else:
        equal_share = 1.0 / len(proposals)
        shares = {proposal.proposal_id: equal_share for proposal in proposals}

    candidates = [
        {
            "proposal_id": proposal.proposal_id,
            "persona_id": proposal.persona_id,
            "effective_weight": raw[proposal.proposal_id],
            "normalised_share": shares[proposal.proposal_id],
        }
        for proposal in proposals
    ]
    return sorted(
        candidates,
        key=lambda candidate: (-float(candidate["effective_weight"]), str(candidate["proposal_id"])),
    )


def _is_high_risk_first_deployment(proposal: PersonaAllocationProposal) -> bool:
    return (
        proposal.scope_ref in {"canary", "live"}
        and proposal.metadata.get("strategy_family_risk") == "high"
        and _truthy(proposal.metadata.get("first_deployment_in_scope"))
    )


def _directions_by_proposal(
    proposals: Sequence[PersonaAllocationProposal],
    direction_set: set[str],
) -> dict[str, list[str]]:
    return {
        proposal.proposal_id: sorted(set(proposal.directions) & direction_set)
        for proposal in proposals
    }


def _group_by_metadata_value(
    proposals: Sequence[PersonaAllocationProposal],
    keys: Sequence[str],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for proposal in proposals:
        value = _normalize_label(_metadata_value(proposal.metadata, keys))
        if value:
            grouped.setdefault(value, []).append(proposal.proposal_id)
    return grouped


def _strategy_family(proposal: PersonaAllocationProposal) -> str | None:
    return _metadata_value(proposal.metadata, STRATEGY_FAMILY_METADATA_KEYS)


def _strategy_family_concentration(
    family_proposals: Sequence[PersonaAllocationProposal],
    *,
    all_proposals: Sequence[PersonaAllocationProposal],
    total_effective_weight: float,
) -> float:
    if total_effective_weight > 0.0:
        return sum(float(proposal.effective_weight) for proposal in family_proposals) / total_effective_weight
    return len(family_proposals) / len(all_proposals)


def _pairwise_homogeneity_metrics(proposals: Sequence[PersonaAllocationProposal]) -> dict[str, Any]:
    max_target_overlap = 0.0
    max_signal_correlation: float | None = None
    max_overlap_pair: list[str] = []
    max_correlation_pair: list[str] = []
    correlation_buckets: dict[str, str] = {}
    high_correlation_bucket = False

    for index, left in enumerate(proposals):
        for right in proposals[index + 1 :]:
            pair = [left.proposal_id, right.proposal_id]
            overlap = _target_overlap(left, right)
            if overlap > max_target_overlap:
                max_target_overlap = overlap
                max_overlap_pair = pair

            correlation = _pairwise_signal_correlation(left, right)
            if correlation is not None and (
                max_signal_correlation is None or correlation > max_signal_correlation
            ):
                max_signal_correlation = correlation
                max_correlation_pair = pair

            bucket = _pairwise_correlation_bucket(left, right)
            if bucket:
                correlation_buckets["|".join(pair)] = bucket
                if bucket in HIGH_CORRELATION_BUCKETS:
                    high_correlation_bucket = True

    return {
        "max_target_overlap": max_target_overlap,
        "max_signal_correlation": max_signal_correlation,
        "max_overlap_pair": max_overlap_pair,
        "max_correlation_pair": max_correlation_pair,
        "correlation_buckets": correlation_buckets,
        "high_correlation_bucket": high_correlation_bucket,
    }


def _target_overlap(
    left: PersonaAllocationProposal,
    right: PersonaAllocationProposal,
) -> float:
    left_weights = _material_target_weights(left)
    right_weights = _material_target_weights(right)
    if not left_weights or not right_weights:
        return 0.0

    common = set(left_weights) & set(right_weights)
    if not common:
        return 0.0

    overlap = sum(min(left_weights[target], right_weights[target]) for target in common)
    denominator = max(sum(left_weights.values()), sum(right_weights.values()))
    if denominator <= 0.0:
        return 0.0
    return overlap / denominator


def _material_target_weights(proposal: PersonaAllocationProposal) -> dict[str, float]:
    excluded_targets = set(DEFAULT_EXCLUDED_OVERLAP_TARGETS)
    metadata_exclusions = proposal.metadata.get("homogeneity_excluded_targets")
    if isinstance(metadata_exclusions, str):
        excluded_targets.add(metadata_exclusions.strip().lower())
    elif isinstance(metadata_exclusions, (list, tuple, set)):
        excluded_targets.update(str(target).strip().lower() for target in metadata_exclusions)

    return {
        target_ref: float(weight)
        for target_ref, weight in proposal.target_weights.items()
        if float(weight) > 0.0 and target_ref.strip().lower() not in excluded_targets
    }


def _pairwise_signal_correlation(
    left: PersonaAllocationProposal,
    right: PersonaAllocationProposal,
) -> float | None:
    mapped = _mapped_correlation(left, right)
    if mapped is None:
        mapped = _mapped_correlation(right, left)
    if mapped is not None:
        return mapped

    scalar_values = [
        value
        for value in (
            _metadata_float(left.metadata, SIGNAL_CORRELATION_METADATA_KEYS),
            _metadata_float(right.metadata, SIGNAL_CORRELATION_METADATA_KEYS),
        )
        if value is not None
    ]
    return max(scalar_values) if scalar_values else None


def _mapped_correlation(
    source: PersonaAllocationProposal,
    target: PersonaAllocationProposal,
) -> float | None:
    for key in CORRELATION_MAPPING_KEYS:
        raw = source.metadata.get(key)
        if not isinstance(raw, Mapping):
            continue
        for target_key in (target.proposal_id, target.persona_id):
            value = _optional_float(raw.get(target_key))
            if value is not None:
                return value
    return None


def _pairwise_correlation_bucket(
    left: PersonaAllocationProposal,
    right: PersonaAllocationProposal,
) -> str | None:
    buckets = [
        bucket
        for bucket in (
            _metadata_value(left.metadata, CORRELATION_BUCKET_METADATA_KEYS),
            _metadata_value(right.metadata, CORRELATION_BUCKET_METADATA_KEYS),
        )
        if bucket
    ]
    if not buckets:
        return None
    for bucket in buckets:
        if bucket in HIGH_CORRELATION_BUCKETS:
            return bucket
    return buckets[0]


def _metadata_value(metadata: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        normalized = _normalize_label(value)
        if normalized:
            return normalized
    return None


def _metadata_float(metadata: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _optional_float(metadata.get(key))
        if value is not None:
            return value
    return None


def _risk_postures(proposal: PersonaAllocationProposal) -> set[str]:
    postures: set[str] = set()
    directions = set(proposal.directions)
    if directions & RISK_ON_DIRECTIONS or _truthy(proposal.metadata.get("wants_leverage")):
        postures.add("risk_on")
    if directions & RISK_OFF_DIRECTIONS or _truthy(proposal.metadata.get("wants_de_risk")):
        postures.add("risk_off")

    metadata_posture = _metadata_value(proposal.metadata, ("risk_posture", "desired_risk_posture", "risk_bias"))
    if metadata_posture in RISK_ON_LABELS:
        postures.add("risk_on")
    if metadata_posture in RISK_OFF_LABELS:
        postures.add("risk_off")
    return postures


def _normalize_label(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _ordered_unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
