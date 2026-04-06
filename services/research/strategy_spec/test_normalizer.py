"""Unit tests for the RS-002 StrategySpec normalizer."""

import unittest

from normalizer import StrategySpecNormalizationError, StrategySpecNormalizer


def _paper_handoff() -> dict:
    return {
        "task_id": "RS-001",
        "source_type": "academic_paper",
        "source_metadata": {
            "api_endpoint": "https://api.openalex.org/works/W1234567890",
            "retrieved_at": "2026-04-06T10:00:00Z",
            "governance_context": "Approved structured source",
        },
        "normalized_findings": {
            "strategy_spec": {
                "name": "Momentum Mean Reversion",
                "description": "Cross-asset momentum entries mean-revert after short-term overshoots.",
                "source_paper": "W1234567890",
                "doi": "10.1000/example",
                "signals": ["momentum_score", "drawdown_indicator"],
                "parameters": {
                    "lookback_window": 20,
                    "mean_reversion_threshold": 1.5,
                },
                "asset_classes": ["equities"],
            },
            "replication_notes": "Daily bars, liquid large-cap universe, rebalance weekly.",
            "evaluation_hypotheses": "Sharpe ratio above 1.0 with max drawdown below 20%.",
        },
        "grok_processing_notes": {
            "normalization_confidence": "high",
            "governance_compliance": "verified",
            "downstream_readiness": "ready_for_replication",
        },
    }


def _repo_handoff() -> dict:
    return {
        "task_id": "RS-001",
        "source_type": "code_repository",
        "source_metadata": {
            "api_endpoint": "https://api.github.com/repos/acme/alpha-trader",
            "repository_url": "https://github.com/acme/alpha-trader",
            "retrieved_at": "2026-04-06T11:00:00Z",
            "governance_context": "Approved structured source",
        },
        "normalized_findings": {
            "strategy_spec": {
                "name": "Alpha Trader Repo",
                "description": "Repository for a crypto intraday strategy.",
                "source_repository": "acme/alpha-trader",
                "signals": ["orderflow_imbalance"],
                "parameters": {"rebalance_cadence": "1h"},
                "key_files": [{"path": "README.md"}, {"path": "strategy.py"}],
            },
            "replication_notes": "Intraday crypto strategy with hourly refresh.",
            "evaluation_hypotheses": "",
        },
        "grok_processing_notes": {
            "normalization_confidence": "low",
            "governance_compliance": "verified",
            "downstream_readiness": "needs_clarification",
        },
    }


class TestStrategySpecNormalizer(unittest.TestCase):
    def setUp(self) -> None:
        self.normalizer = StrategySpecNormalizer()

    def test_normalize_paper_handoff_to_canonical_outputs(self):
        result = self.normalizer.normalize(_paper_handoff())

        self.assertTrue(result.strategy_spec["strategy_id"].startswith("strat-"))
        self.assertEqual(result.strategy_spec["title"], "Momentum Mean Reversion")
        self.assertEqual(result.strategy_spec["market_scope"]["symbols"], ["RESEARCH_UNIVERSE"])
        self.assertEqual(result.strategy_spec["market_scope"]["asset_classes"], ["equities"])
        self.assertEqual(result.strategy_spec["execution_profile"]["quantity_type"], "PERCENT_PORTFOLIO")
        self.assertEqual(
            result.workflow_handoff["registry_hints"]["initial_lifecycle_state"],
            "candidate",
        )
        self.assertEqual(result.workflow_handoff["handoff_type"], "strategy_spec")
        self.assertEqual(result.workflow_handoff["governance_context"]["execution_context"], "research")
        self.assertEqual(result.replication_handoff["task_id"], "RS-002")
        self.assertEqual(
            result.replication_handoff["normalized_findings"]["strategy_spec"]["canonical_strategy_spec_id"],
            result.strategy_spec["strategy_id"],
        )

    def test_low_confidence_repo_material_stays_draft(self):
        result = self.normalizer.normalize(_repo_handoff())

        self.assertEqual(
            result.workflow_handoff["registry_hints"]["initial_lifecycle_state"],
            "draft",
        )
        self.assertEqual(result.strategy_spec["provenance"]["source_kind"], "repo")
        self.assertIn(
            {"ref": "acme/alpha-trader", "kind": "repo"},
            result.strategy_spec["data_dependencies"],
        )
        self.assertEqual(result.strategy_spec["market_scope"]["symbols"], ["CRYPTO_UNIVERSE"])

    def test_replication_payload_preserves_legacy_handoff_and_canonical_spec(self):
        result = self.normalizer.normalize(_paper_handoff())
        payload = result.to_replication_payload("cand-001", metadata={"test_case": True})

        self.assertEqual(payload["candidate_id"], "cand-001")
        self.assertEqual(payload["source_task_id"], "RS-002")
        self.assertEqual(payload["research_handoff"]["task_id"], "RS-002")
        self.assertEqual(
            payload["proposed_strategy_spec"]["strategy_id"],
            result.strategy_spec["strategy_id"],
        )
        self.assertTrue(payload["metadata"]["test_case"])

    def test_missing_governance_context_is_rejected(self):
        handoff = _paper_handoff()
        handoff["source_metadata"].pop("governance_context")

        with self.assertRaises(StrategySpecNormalizationError):
            self.normalizer.normalize(handoff)


if __name__ == "__main__":
    unittest.main()
