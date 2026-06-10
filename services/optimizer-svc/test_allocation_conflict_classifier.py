from __future__ import annotations

import sys
from pathlib import Path


OPTIMIZER_DIR = Path(__file__).resolve().parent
if str(OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(OPTIMIZER_DIR))

from allocation_aggregation import (  # noqa: E402
    PersonaAllocationProposalJsonlStore,
    explain_conflicts,
    ingest_persona_proposal,
)
from portfolio_synthesis import (  # noqa: E402
    AllocationConflictType,
    CommitteeReferral,
    PersonaAllocationProposal,
    PortfolioSynthesizer,
    classify_allocation_conflicts,
)


def make_proposal(**overrides) -> PersonaAllocationProposal:
    defaults = {
        "proposal_id": "pap-alpha",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-paper",
        "scope_ref": "paper",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"2330.TW": 0.08, "CASH": 0.92},
        "conviction": 0.8,
        "uncertainty": 0.1,
        "rationale_ref": "memo-alpha",
        "regime_ref": "regime-risk-on",
        "valid_from": "2026-05-15T14:00:00Z",
        "valid_to": "2026-05-22T14:00:00Z",
        "evidence_refs": ["evidence://pap-alpha"],
        "created_at": "2026-05-15T14:00:00Z",
        "reliability_score": 0.9,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {
            "holding_period": "swing",
            "risk_posture": "risk_on",
            "strategy_family": "tw_equity",
        },
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


def conflict_types(report) -> set[str]:
    return {conflict.conflict_type for conflict in report.conflicts}


def test_direction_conflict_with_high_conviction_requires_committee() -> None:
    report = classify_allocation_conflicts(
        [
            make_proposal(conviction=0.9, directions=["long"]),
            make_proposal(
                proposal_id="pap-beta",
                persona_id="persona-beta",
                directions=["short"],
                target_weights={"2330.TW": 0.01, "CASH": 0.99},
                conviction=0.82,
                regime_ref="regime-risk-on",
            ),
        ]
    )

    assert report.requires_committee is True
    assert report.committee_triggers[0] == "long_vs_short_high_conviction_conflict"
    assert AllocationConflictType.DIRECTION.value in conflict_types(report)
    direction_conflict = next(
        conflict
        for conflict in report.conflicts
        if conflict.conflict_type == AllocationConflictType.DIRECTION.value
    )
    assert direction_conflict.severity == "committee"
    assert direction_conflict.proposal_ids == ["pap-alpha", "pap-beta"]


def test_weight_horizon_and_regime_conflicts_are_explainable_without_committee() -> None:
    report = classify_allocation_conflicts(
        [
            make_proposal(),
            make_proposal(
                proposal_id="pap-beta",
                persona_id="persona-beta",
                target_weights={"2330.TW": 0.01, "CASH": 0.99},
                conviction=0.4,
                regime_ref="regime-risk-off",
                metadata={
                    "holding_period": "position",
                    "risk_posture": "risk_on",
                    "strategy_family": "tw_equity",
                },
            ),
        ]
    )

    assert report.requires_committee is False
    assert {
        AllocationConflictType.WEIGHT.value,
        AllocationConflictType.HORIZON.value,
        AllocationConflictType.REGIME.value,
    }.issubset(conflict_types(report))
    weight_conflict = next(
        conflict
        for conflict in report.conflicts
        if conflict.conflict_type == AllocationConflictType.WEIGHT.value
        and conflict.target_ref == "2330.TW"
    )
    assert weight_conflict.details["spread"] == 0.07


def test_low_correlation_duplicate_family_passes_without_committee() -> None:
    report = classify_allocation_conflicts(
        [
            make_proposal(
                target_weights={"2330.TW": 0.12, "CASH": 0.88},
                metadata={
                    "holding_period": "swing",
                    "risk_posture": "risk_on",
                    "strategy_family": "tw_equity",
                    "signal_correlations": {"pap-beta": 0.2},
                    "correlation_bucket": "low",
                },
            ),
            make_proposal(
                proposal_id="pap-beta",
                persona_id="persona-beta",
                conviction=0.5,
                target_weights={"0050.TW": 0.12, "CASH": 0.88},
                metadata={
                    "holding_period": "swing",
                    "risk_posture": "risk_on",
                    "strategy_family": "tw_equity",
                    "correlation_bucket": "low",
                },
            ),
        ]
    )

    assert report.requires_committee is False
    homogeneity_conflict = next(
        conflict
        for conflict in report.conflicts
        if conflict.conflict_type == AllocationConflictType.HOMOGENEITY.value
    )
    assert homogeneity_conflict.severity == "warning"
    assert homogeneity_conflict.details["max_target_overlap"] == 0.0
    assert homogeneity_conflict.details["max_signal_correlation"] == 0.2


def test_high_correlation_bucket_and_target_overlap_require_committee() -> None:
    report = classify_allocation_conflicts(
        [
            make_proposal(
                target_weights={"2330.TW": 0.1, "2454.TW": 0.08, "CASH": 0.82},
                metadata={
                    "holding_period": "swing",
                    "risk_posture": "risk_on",
                    "strategy_family": "tw_equity",
                    "signal_correlations": {"pap-beta": 0.91},
                    "correlation_bucket": "high",
                },
            ),
            make_proposal(
                proposal_id="pap-beta",
                persona_id="persona-beta",
                target_weights={"2330.TW": 0.1, "2454.TW": 0.08, "CASH": 0.82},
                metadata={
                    "holding_period": "swing",
                    "risk_posture": "risk_on",
                    "strategy_family": "tw_equity",
                    "correlation_bucket": "high",
                },
            ),
        ]
    )

    assert report.requires_committee is True
    assert report.committee_triggers[0] == "homogeneity_correlation_review"
    assert {
        AllocationConflictType.HOMOGENEITY.value,
        AllocationConflictType.CORRELATION.value,
    }.issubset(conflict_types(report))
    correlation_conflict = next(
        conflict
        for conflict in report.conflicts
        if conflict.conflict_type == AllocationConflictType.CORRELATION.value
    )
    assert correlation_conflict.severity == "committee"
    assert correlation_conflict.details["strategy_family_concentration"] == 1.0
    assert correlation_conflict.details["max_target_overlap"] == 1.0
    assert correlation_conflict.details["max_signal_correlation"] == 0.91


def test_store_backed_explain_conflicts_preserves_requested_proposal_order(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            directions=["short"],
            target_weights={"2330.TW": 0.01, "CASH": 0.99},
            regime_ref="regime-risk-on",
            created_at="2026-05-15T14:01:00Z",
        ),
        store=store,
    )

    report = explain_conflicts(["pap-beta", "pap-alpha"], store=store)

    assert report.proposal_ids == ["pap-beta", "pap-alpha"]
    assert report.capital_pool_id == "pool-paper"
    assert report.scope_ref == "paper"
    assert report.to_dict()["requires_committee"] is True


def test_synthesizer_uses_classifier_committee_trigger_for_risk_posture() -> None:
    synthesizer = PortfolioSynthesizer()

    result, log = synthesizer.synthesize_with_log(
        [
            make_proposal(metadata={"wants_leverage": True}),
            make_proposal(
                proposal_id="pap-beta",
                persona_id="persona-beta",
                directions=["de_risk"],
                target_weights={"CASH": 1.0},
                metadata={"wants_de_risk": True},
            ),
        ],
        capital_pool_id="pool-paper",
        scope_ref="paper",
    )

    assert isinstance(result, CommitteeReferral)
    assert result.trigger_reason == "risk_posture_conflict"
    assert log.committee_ref == result.referral_id
