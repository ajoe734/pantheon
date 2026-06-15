"""
Seed helper for the Evolution service.

Populates an EvolutionDecisionStore with representative sample decisions
covering the three risk tiers and the full lifecycle path.  Used in tests
and local development smoke runs.
"""
from __future__ import annotations

import uuid
from pathlib import Path
import sys

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import EvidenceRef, EvidenceRefType  # type: ignore
from evolution_decision import (  # type: ignore
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
)


def _ev(ref_id: str) -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
        ref_id=ref_id,
        storage_ref={"backend": "object_store", "path": f"/telemetry/{ref_id}"},
    )


def _snap(signal: ThresholdSignalType, metric: str, observed: float, threshold: float) -> ThresholdSnapshot:
    return ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md",
        signal_type=signal,
        metric_name=metric,
        comparator=ComparisonOperator.LT,
        observed_value=observed,
        threshold_value=threshold,
        window="20d",
    )


def seed(store: EvolutionDecisionStore) -> None:
    """Insert representative seed decisions into *store*."""

    # EV-SEED-001: low-risk retrain, proposed
    d1 = EvolutionDecision.create_proposed(
        decision_id="ev-seed-001",
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id="strat-alpha-001",
        target_version="v3",
        action_type=EvolutionActionType.RETRAIN,
        rationale="Sharpe dropped below 50% of baseline over the last 20 days.",
        created_by_id="evolution-controller",
        threshold_snapshots=[
            _snap(
                ThresholdSignalType.PERFORMANCE_DEGRADATION,
                "sharpe_pct_of_baseline",
                0.41,
                0.50,
            )
        ],
        evidence_refs=[_ev("telemetry-sum-001")],
    )
    store.put(d1)

    # EV-SEED-002: medium-risk freeze (paper), proposed
    d2 = EvolutionDecision.create_proposed(
        decision_id="ev-seed-002",
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-beta-007",
        target_version="v1",
        action_type=EvolutionActionType.FREEZE,
        rationale="PSI exceeded hard revalidation threshold of 0.30 for the paper artifact.",
        created_by_id="evolution-controller",
        target_stage="paper",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3",
                signal_type=ThresholdSignalType.FEATURE_DRIFT,
                metric_name="population_stability_index",
                comparator=ComparisonOperator.GT,
                observed_value=0.33,
                threshold_value=0.30,
                window="14d",
            )
        ],
        evidence_refs=[_ev("telemetry-sum-002")],
    )
    store.put(d2)

    # EV-SEED-003: high-risk freeze (live), proposed (Severity-1 incident path)
    d3 = EvolutionDecision.create_proposed(
        decision_id="ev-seed-003",
        target_type=EvolutionTargetType.PERSONA,
        target_id="persona-gamma-live",
        target_version="v2",
        action_type=EvolutionActionType.FREEZE,
        rationale="Severity-1 incident opened the high-risk live-freeze path.",
        created_by_id="evolution-controller",
        target_stage="live",
        linked_incident_id="inc-sev1-2026-042",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
                signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
                metric_name="severity1_incident_count",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="1d",
            )
        ],
    )
    store.put(d3)

    # EV-SEED-004: low-risk observe, proposed (feature drift warning)
    d4 = EvolutionDecision.create_proposed(
        decision_id="ev-seed-004",
        target_type=EvolutionTargetType.ALPHA_TEMPLATE,
        target_id="alpha-tmpl-delta",
        target_version="v5",
        action_type=EvolutionActionType.OBSERVE,
        rationale="PSI crossed the 0.20 observation threshold; monitoring extended.",
        created_by_id="evolution-controller",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3",
                signal_type=ThresholdSignalType.FEATURE_DRIFT,
                metric_name="population_stability_index",
                comparator=ComparisonOperator.GT,
                observed_value=0.22,
                threshold_value=0.20,
                window="14d",
            )
        ],
        evidence_refs=[_ev("telemetry-sum-004")],
    )
    store.put(d4)

    # EV-SEED-005: medium-risk tighten_risk_policy, proposed (execution drift)
    d5 = EvolutionDecision.create_proposed(
        decision_id="ev-seed-005",
        target_type=EvolutionTargetType.ALLOCATION_POLICY_ARTIFACT,
        target_id="alloc-pol-epsilon",
        target_version="v2",
        action_type=EvolutionActionType.TIGHTEN_RISK_POLICY,
        rationale="Order reject rate exceeded 1.0% threshold without active incident yet.",
        created_by_id="evolution-controller",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                signal_type=ThresholdSignalType.EXECUTION_DRIFT,
                metric_name="order_reject_rate_pct",
                comparator=ComparisonOperator.GT,
                observed_value=1.4,
                threshold_value=1.0,
                window="5d",
            )
        ],
        evidence_refs=[_ev("telemetry-sum-005")],
    )
    store.put(d5)

    # EVO-VSLICE-001: vertical-slice proposal used to prove the evolution-programs
    # BFF surface population path (CONSOLE-DATA-EVOLUTION task).
    d_vslice = EvolutionDecision.create_proposed(
        decision_id="evo-vslice-1",
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-vslice-momentum-001",
        target_version="v1",
        action_type=EvolutionActionType.RETRAIN,
        rationale="Momentum strategy vertical-slice: sharpe_pct_of_baseline fell to 0.38, below the 0.50 governed threshold over 20 days.",
        created_by_id="evolution-controller",
        threshold_snapshots=[
            _snap(
                ThresholdSignalType.PERFORMANCE_DEGRADATION,
                "sharpe_pct_of_baseline",
                0.38,
                0.50,
            )
        ],
        evidence_refs=[_ev("telemetry-sum-vslice-001")],
    )
    store.put(d_vslice)
