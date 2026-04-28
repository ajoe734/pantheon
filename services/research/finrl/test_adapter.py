"""Unit tests for the governed FinRL deferred-prep adapter."""
from __future__ import annotations

import copy
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.finrl_adapter import (
    DeferredPrepGate,
    FinRLDeferredPrepError,
    FinRLPPOBackend,
    GovernedFinRLPolicyAdapter,
    PolicyTrainingConfig,
    StubFinRLBackend,
    run_finrl_workflow,
)

MINIMAL_DATASET = {
    "dataset_id": "dataset:finrl-test-001",
    "strategy_id": "portfolio-exit-test",
    "source_dataset_refs": ["dataset:test-source"],
    "source_strategy_spec_id": "strategy-spec:test-v1",
    "decision_focus": "exit_timing",
    "data_frequency": "daily",
    "position_ratio": 0.2,
    "cash_ratio": 0.8,
    "records": [
        {"instrument": "AAA", "date": "2024-01-01", "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0, "volume": 1000000},
        {"instrument": "AAA", "date": "2024-01-02", "open": 101.0, "high": 102.4, "low": 100.4, "close": 102.1, "volume": 1010000},
        {"instrument": "AAA", "date": "2024-01-03", "open": 102.1, "high": 102.9, "low": 101.0, "close": 101.7, "volume": 980000},
        {"instrument": "AAA", "date": "2024-01-04", "open": 101.7, "high": 103.7, "low": 101.2, "close": 103.0, "volume": 1075000},
        {"instrument": "AAA", "date": "2024-01-05", "open": 103.0, "high": 104.0, "low": 102.7, "close": 103.6, "volume": 1090000},
        {"instrument": "AAA", "date": "2024-01-06", "open": 103.6, "high": 104.8, "low": 103.0, "close": 104.2, "volume": 1110000},
        {"instrument": "AAA", "date": "2024-01-07", "open": 104.2, "high": 104.4, "low": 103.4, "close": 103.8, "volume": 1080000},
        {"instrument": "BBB", "date": "2024-01-01", "open": 50.0, "high": 50.8, "low": 49.5, "close": 50.2, "volume": 600000},
        {"instrument": "BBB", "date": "2024-01-02", "open": 50.2, "high": 51.0, "low": 49.9, "close": 50.6, "volume": 615000},
        {"instrument": "BBB", "date": "2024-01-03", "open": 50.6, "high": 50.9, "low": 50.1, "close": 50.4, "volume": 610000},
        {"instrument": "BBB", "date": "2024-01-04", "open": 50.4, "high": 51.3, "low": 50.0, "close": 51.0, "volume": 635000},
        {"instrument": "BBB", "date": "2024-01-05", "open": 51.0, "high": 51.8, "low": 50.6, "close": 51.4, "volume": 640000},
        {"instrument": "BBB", "date": "2024-01-06", "open": 51.4, "high": 52.0, "low": 51.0, "close": 51.7, "volume": 650000},
        {"instrument": "BBB", "date": "2024-01-07", "open": 51.7, "high": 51.9, "low": 50.8, "close": 51.1, "volume": 638000}
    ],
}


class TestGovernedFinRLPolicyAdapter(unittest.TestCase):
    def test_happy_path_produces_observations(self) -> None:
        prepared = GovernedFinRLPolicyAdapter().prepare(MINIMAL_DATASET)
        self.assertEqual(prepared.strategy_id, "portfolio-exit-test")
        self.assertEqual(len(prepared.instruments), 2)
        self.assertGreater(prepared.num_steps, 0)
        self.assertEqual(prepared.observation_dim, 7)

    def test_missing_dataset_refs_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["source_dataset_refs"]
        with self.assertRaises(FinRLDeferredPrepError):
            GovernedFinRLPolicyAdapter().prepare(bad)

    def test_invalid_decision_focus_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["decision_focus"] = "alpha_scoring"
        with self.assertRaises(FinRLDeferredPrepError):
            GovernedFinRLPolicyAdapter().prepare(bad)

    def test_single_instrument_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"] = [record for record in bad["records"] if record["instrument"] == "AAA"]
        with self.assertRaises(FinRLDeferredPrepError):
            GovernedFinRLPolicyAdapter().prepare(bad)


class TestStubFinRLBackend(unittest.TestCase):
    def test_stub_returns_policy_priors(self) -> None:
        prepared = GovernedFinRLPolicyAdapter().prepare(MINIMAL_DATASET)
        result = StubFinRLBackend().train(prepared, PolicyTrainingConfig())
        self.assertEqual(result.backend, "stub_finrl")
        self.assertIn("action_priors", result.policy_payload)
        self.assertIn("mean_reward_proxy", result.metrics)

    def test_run_id_is_unique(self) -> None:
        prepared = GovernedFinRLPolicyAdapter().prepare(MINIMAL_DATASET)
        r1 = StubFinRLBackend().train(prepared, PolicyTrainingConfig())
        r2 = StubFinRLBackend().train(prepared, PolicyTrainingConfig())
        self.assertNotEqual(r1.run_id, r2.run_id)


class TestFinRLPPOBackend(unittest.TestCase):
    def test_real_backend_uses_finrl_import_path(self) -> None:
        fake_finrl = types.SimpleNamespace(__name__="finrl", __version__="0.3.6")
        prepared = GovernedFinRLPolicyAdapter().prepare(MINIMAL_DATASET)
        with patch.dict(sys.modules, {"finrl": fake_finrl}, clear=False):
            result = FinRLPPOBackend().train(prepared, PolicyTrainingConfig())
        self.assertEqual(result.backend, "finrl_ppo")
        self.assertTrue(result.metrics["framework_import_ready"])
        self.assertEqual(result.policy_payload["framework_import"], "finrl")


class TestDeferredPrepGate(unittest.TestCase):
    def test_cli_gate_requires_explicit_flag(self) -> None:
        with self.assertRaises(EnvironmentError):
            DeferredPrepGate.require_cli_flag(False)

    def test_env_gate_requires_explicit_enable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_FINRL_PREP_ENABLED", None)
            with self.assertRaises(EnvironmentError):
                DeferredPrepGate.require_env()

    def test_env_gate_accepts_enabled_value(self) -> None:
        with patch.dict(os.environ, {"PANTHEON_FINRL_PREP_ENABLED": "1"}, clear=False):
            DeferredPrepGate.require_env()


class TestRunFinRLWorkflow(unittest.TestCase):
    def test_registry_entry_starts_as_draft(self) -> None:
        result = run_finrl_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["artifact_state"], "draft")
        self.assertEqual(result.registry_entry["deployment_summary"]["current_stage"], "none")

    def test_candidate_packet_targets_candidate(self) -> None:
        result = run_finrl_workflow(MINIMAL_DATASET)
        packet = result.candidate_packet
        self.assertEqual(packet["current_artifact_state"], "draft")
        self.assertEqual(packet["requested_artifact_state"], "candidate")
        self.assertEqual(packet["candidate_registry_projection"]["artifact_state"], "candidate")

    def test_governance_remains_offline_only(self) -> None:
        result = run_finrl_workflow(MINIMAL_DATASET)
        governance = result.artifact_bundle["governance"]
        self.assertFalse(governance["direct_live_influence"])
        self.assertEqual(governance["gate_state"], "closed")
        self.assertEqual(governance["lean_consumption"], "scoring_only_not_direct_action")

    def test_checksum_is_sha256_prefixed(self) -> None:
        result = run_finrl_workflow(MINIMAL_DATASET)
        self.assertTrue(result.registry_entry["checksum"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
