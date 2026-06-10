"""
Unit tests for the CAP-002 portfolio synthesis module.

Run:
    python3 -m unittest discover -s services/optimizer-svc -p 'test_*.py'
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_synthesis import (  # noqa: E402
    CommitteeReferral,
    PoolRiskPolicy,
    PortfolioSynthesizer,
    SynthesisError,
    SynthesisMethod,
    VetoReason,
    PersonaAllocationProposal,
)


def make_proposal(**overrides) -> PersonaAllocationProposal:
    defaults = {
        "proposal_id": "proposal-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "scope_ref": "live",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"AAPL": 0.6, "MSFT": 0.4},
        "conviction": 0.8,
        "uncertainty": 0.0,
        "evidence_refs": ["evidence://proposal-001"],
        "created_at": "2026-05-15T14:00:00Z",
        "reliability_score": 1.0,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {},
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


class TestPortfolioSynthesis(unittest.TestCase):
    def test_weighted_fusion_produces_single_artifact_and_log(self) -> None:
        synthesizer = PortfolioSynthesizer()
        result, log = synthesizer.synthesize_with_log(
            [
                make_proposal(),
                make_proposal(
                    proposal_id="proposal-002",
                    persona_id="persona-beta",
                    target_weights={"AAPL": 0.1, "GOOG": 0.9},
                    conviction=0.2,
                ),
            ],
            capital_pool_id="pool-001",
            scope_ref="live",
            constraints_bundle={"max_names": 3},
            risk_budget=0.35,
        )

        self.assertEqual(result.sponsor_persona_id, "persona-alpha")
        self.assertEqual(result.synthesis_method, SynthesisMethod.WEIGHTED_FUSION.value)
        self.assertEqual(result.conflict_resolution_log_id, log.log_id)
        self.assertEqual(result.provenance_refs, ["proposal-001", "proposal-002"])
        self.assertEqual(result.constraints_bundle, {"max_names": 3})
        self.assertEqual(result.risk_budget, 0.35)
        self.assertTrue(math.isclose(result.target_weights["AAPL"], 0.5, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(result.target_weights["MSFT"], 0.32, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(result.target_weights["GOOG"], 0.18, rel_tol=0, abs_tol=1e-9))
        self.assertEqual(log.sponsor_persona_id, "persona-alpha")
        self.assertEqual(log.synthesis_method, SynthesisMethod.WEIGHTED_FUSION.value)
        self.assertEqual(log.proposal_ids, ["proposal-001", "proposal-002"])
        self.assertTrue(math.isclose(log.weighting_inputs["proposal-001"], 0.8, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(log.weighting_outputs["proposal-001"], 0.8, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(log.weighting_outputs["proposal-002"], 0.2, rel_tol=0, abs_tol=1e-9))
        self.assertIs(synthesizer.last_conflict_resolution_log, log)

    def test_single_surviving_proposal_records_vetoed_inputs(self) -> None:
        policy = PoolRiskPolicy(max_single_weight=0.65)
        synthesizer = PortfolioSynthesizer(pool_risk_policy=policy)
        result, log = synthesizer.synthesize_with_log(
            [
                make_proposal(target_weights={"AAPL": 0.6, "MSFT": 0.4}),
                make_proposal(
                    proposal_id="proposal-002",
                    persona_id="persona-beta",
                    target_weights={"TSLA": 0.7, "CASH": 0.2},
                ),
            ],
            capital_pool_id="pool-001",
            scope_ref="live",
        )

        self.assertEqual(result.synthesis_method, SynthesisMethod.SINGLE_PROPOSAL.value)
        self.assertEqual(result.sponsor_persona_id, "persona-alpha")
        self.assertEqual(result.target_weights, {"AAPL": 0.6, "MSFT": 0.4})
        self.assertEqual(len(log.vetoed_proposals), 1)
        self.assertEqual(log.vetoed_proposals[0].proposal_id, "proposal-002")
        self.assertEqual(log.vetoed_proposals[0].reason, VetoReason.POOL_RISK_POLICY.value)

    def test_all_vetoed_raises_and_retains_log(self) -> None:
        policy = PoolRiskPolicy(
            forbidden_asset_classes={"crypto"},
            max_single_weight=0.5,
        )
        synthesizer = PortfolioSynthesizer(pool_risk_policy=policy)

        with self.assertRaises(SynthesisError):
            synthesizer.synthesize(
                [
                    make_proposal(
                        directions=["long"],
                        target_weights={"BTC": 0.8},
                        metadata={"asset_classes": ["crypto"]},
                    ),
                    make_proposal(
                        proposal_id="proposal-002",
                        persona_id="persona-beta",
                        target_weights={"NVDA": 0.7},
                    ),
                ],
                capital_pool_id="pool-001",
                scope_ref="live",
            )

        log = synthesizer.last_conflict_resolution_log
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.rejected_reason, "all_proposals_vetoed")
        self.assertEqual(len(log.vetoed_proposals), 2)
        self.assertEqual(log.sponsor_persona_id, None)

    def test_first_class_risk_policy_payload_vetoes_optimizer_proposal(self) -> None:
        policy = PoolRiskPolicy(
            risk_policy={
                "risk_policy_id": "risk-main",
                "forbidden_asset_classes": ["crypto"],
                "max_single_name_weight": 0.5,
            }
        )
        synthesizer = PortfolioSynthesizer(pool_risk_policy=policy)

        with self.assertRaises(SynthesisError):
            synthesizer.synthesize(
                [
                    make_proposal(
                        metadata={"asset_classes": ["crypto"]},
                        target_weights={"BTC": 0.8},
                    )
                ],
                capital_pool_id="pool-001",
                scope_ref="live",
            )

        log = synthesizer.last_conflict_resolution_log
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.vetoed_proposals[0].reason, VetoReason.FORBIDDEN_ASSET_CLASS.value)

    def test_risk_veto_precedes_committee_escalation(self) -> None:
        referrals: list[CommitteeReferral] = []
        policy = PoolRiskPolicy(risk_policy={"risk_policy_id": "risk-main", "forbidden_asset_classes": ["crypto"]})
        synthesizer = PortfolioSynthesizer(
            pool_risk_policy=policy,
            committee_escalation_fn=referrals.append,
        )

        with self.assertRaises(SynthesisError):
            synthesizer.synthesize(
                [
                    make_proposal(
                        conviction=0.95,
                        metadata={"asset_classes": ["crypto"]},
                    ),
                    make_proposal(
                        proposal_id="proposal-002",
                        persona_id="persona-beta",
                        conviction=0.96,
                        directions=["short"],
                        target_weights={"ETH": 0.8},
                        metadata={"asset_classes": ["crypto"]},
                    ),
                ],
                capital_pool_id="pool-001",
                scope_ref="live",
            )

        self.assertEqual(referrals, [])
        log = synthesizer.last_conflict_resolution_log
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.rejected_reason, "all_proposals_vetoed")

    def test_committee_escalation_returns_referral_and_log(self) -> None:
        referrals: list[CommitteeReferral] = []
        synthesizer = PortfolioSynthesizer(
            committee_escalation_fn=referrals.append,
        )

        result, log = synthesizer.synthesize_with_log(
            [
                make_proposal(conviction=0.9, directions=["long"]),
                make_proposal(
                    proposal_id="proposal-002",
                    persona_id="persona-beta",
                    conviction=0.92,
                    directions=["short"],
                    target_weights={"AAPL": 0.1, "GOOG": 0.9},
                ),
            ],
            capital_pool_id="pool-001",
            scope_ref="live",
        )

        self.assertIsInstance(result, CommitteeReferral)
        self.assertEqual(result.trigger_reason, "long_vs_short_high_conviction_conflict")
        self.assertEqual(referrals, [result])
        self.assertEqual(log.committee_ref, result.referral_id)
        self.assertEqual(log.synthesis_method, SynthesisMethod.COMMITTEE_OVERRIDE.value)
        self.assertEqual(log.sponsor_persona_id, None)

    def test_homogeneity_correlation_review_escalates_to_committee(self) -> None:
        referrals: list[CommitteeReferral] = []
        synthesizer = PortfolioSynthesizer(
            committee_escalation_fn=referrals.append,
        )

        result, log = synthesizer.synthesize_with_log(
            [
                make_proposal(
                    metadata={
                        "strategy_family": "mega_cap_momentum",
                        "signal_correlations": {"proposal-002": 0.92},
                        "correlation_bucket": "high",
                    },
                ),
                make_proposal(
                    proposal_id="proposal-002",
                    persona_id="persona-beta",
                    target_weights={"AAPL": 0.6, "MSFT": 0.4},
                    metadata={
                        "strategy_family": "mega_cap_momentum",
                        "correlation_bucket": "high",
                    },
                ),
            ],
            capital_pool_id="pool-001",
            scope_ref="live",
        )

        self.assertIsInstance(result, CommitteeReferral)
        self.assertEqual(result.trigger_reason, "homogeneity_correlation_review")
        self.assertEqual(referrals, [result])
        self.assertEqual(log.committee_ref, result.referral_id)
        self.assertEqual(log.synthesis_method, SynthesisMethod.COMMITTEE_OVERRIDE.value)

    def test_risk_policy_correlation_veto_precedes_homogeneity_committee(self) -> None:
        referrals: list[CommitteeReferral] = []
        policy = PoolRiskPolicy(
            risk_policy={
                "risk_policy_id": "risk-main",
                "max_signal_correlation": 0.9,
            }
        )
        synthesizer = PortfolioSynthesizer(
            pool_risk_policy=policy,
            committee_escalation_fn=referrals.append,
        )

        with self.assertRaises(SynthesisError):
            synthesizer.synthesize(
                [
                    make_proposal(
                        conviction=0.95,
                        metadata={
                            "strategy_family": "mega_cap_momentum",
                            "signal_correlation": 0.95,
                            "correlation_bucket": "high",
                        },
                    ),
                    make_proposal(
                        proposal_id="proposal-002",
                        persona_id="persona-beta",
                        conviction=0.96,
                        directions=["short"],
                        target_weights={"AAPL": 0.6, "MSFT": 0.4},
                        metadata={
                            "strategy_family": "mega_cap_momentum",
                            "signal_correlation": 0.95,
                            "correlation_bucket": "high",
                        },
                    ),
                ],
                capital_pool_id="pool-001",
                scope_ref="live",
            )

        self.assertEqual(referrals, [])
        log = synthesizer.last_conflict_resolution_log
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.rejected_reason, "all_proposals_vetoed")
        self.assertEqual(
            [record.reason for record in log.vetoed_proposals],
            [VetoReason.POOL_RISK_POLICY.value, VetoReason.POOL_RISK_POLICY.value],
        )

    def test_high_risk_first_canary_deployment_escalates(self) -> None:
        synthesizer = PortfolioSynthesizer()

        result = synthesizer.synthesize(
            [
                make_proposal(
                    scope_ref="canary",
                    metadata={
                        "strategy_family_risk": "high",
                        "first_deployment_in_scope": True,
                    },
                )
            ],
            capital_pool_id="pool-001",
            scope_ref="canary",
        )

        self.assertIsInstance(result, CommitteeReferral)
        log = synthesizer.last_conflict_resolution_log
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.committee_ref, result.referral_id)

    def test_zero_effective_weight_falls_back_to_equal_shares(self) -> None:
        synthesizer = PortfolioSynthesizer()
        result, log = synthesizer.synthesize_with_log(
            [
                make_proposal(
                    conviction=0.0,
                    target_weights={"AAPL": 1.0},
                ),
                make_proposal(
                    proposal_id="proposal-002",
                    persona_id="persona-beta",
                    conviction=0.0,
                    target_weights={"MSFT": 1.0},
                ),
            ],
            capital_pool_id="pool-001",
            scope_ref="live",
        )

        self.assertTrue(math.isclose(result.target_weights["AAPL"], 0.5, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(result.target_weights["MSFT"], 0.5, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(log.weighting_outputs["proposal-001"], 0.5, rel_tol=0, abs_tol=1e-9))
        self.assertTrue(math.isclose(log.weighting_outputs["proposal-002"], 0.5, rel_tol=0, abs_tol=1e-9))

    def test_pool_scope_mismatch_rejected_before_synthesis(self) -> None:
        synthesizer = PortfolioSynthesizer()

        with self.assertRaises(SynthesisError):
            synthesizer.synthesize(
                [
                    make_proposal(capital_pool_id="pool-999"),
                ],
                capital_pool_id="pool-001",
                scope_ref="live",
            )


if __name__ == "__main__":
    unittest.main()
