"""Unit tests for the governed RLlib deferred-prep adapter."""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.rllib_adapter import (
    DeferredPrepGate,
    GovernedRLlibTrainEvalAdapter,
    RLlibDeferredPrepError,
    RLlibPPOBackend,
    RLlibTrainingConfig,
    StubRLlibBackend,
    run_rllib_workflow,
)
from smoke_test import main as smoke_main
from worker import main as worker_main


def build_dataset() -> dict:
    series = {
        "AAA": [100.0, 101.5, 102.1, 101.8, 103.4, 104.1, 103.6, 104.8, 105.2],
        "BBB": [50.0, 50.4, 50.9, 50.6, 51.3, 51.7, 51.1, 51.9, 52.2],
        "CCC": [75.0, 75.6, 76.1, 75.8, 76.9, 77.5, 77.2, 78.1, 78.7],
    }
    records: list[dict[str, float | str]] = []
    for instrument, closes in series.items():
        for idx, close in enumerate(closes, start=1):
            records.append(
                {
                    "instrument": instrument,
                    "date": f"2024-01-{idx:02d}",
                    "open": round(close * 0.995, 4),
                    "high": round(close * 1.01, 4),
                    "low": round(close * 0.99, 4),
                    "close": close,
                    "volume": 500000 + idx * 10000 + (0 if instrument == "AAA" else 5000),
                }
            )
    return {
        "dataset_id": "dataset:rllib-test-001",
        "strategy_id": "multi-agent-rebalance-test",
        "source_dataset_refs": ["dataset:test-source"],
        "source_strategy_spec_id": "strategy-spec:test-v1",
        "decision_focus": "rebalance",
        "data_frequency": "daily",
        "transaction_cost_pct": 0.001,
        "slippage_bps": 5,
        "max_position_size": 0.3,
        "records": records,
    }


MINIMAL_DATASET = build_dataset()


class TestGovernedRLlibTrainEvalAdapter(unittest.TestCase):
    def test_happy_path_produces_train_eval_rollouts(self) -> None:
        prepared = GovernedRLlibTrainEvalAdapter().prepare(MINIMAL_DATASET)
        self.assertEqual(prepared.strategy_id, "multi-agent-rebalance-test")
        self.assertEqual(len(prepared.instruments), 3)
        self.assertGreater(prepared.num_train_steps, 0)
        self.assertGreater(prepared.num_eval_steps, 0)
        self.assertEqual(prepared.observation_features, 8)

    def test_missing_dataset_refs_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["source_dataset_refs"]
        with self.assertRaises(RLlibDeferredPrepError):
            GovernedRLlibTrainEvalAdapter().prepare(bad)

    def test_invalid_decision_focus_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["decision_focus"] = "alpha_scoring"
        with self.assertRaises(RLlibDeferredPrepError):
            GovernedRLlibTrainEvalAdapter().prepare(bad)

    def test_single_instrument_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"] = [record for record in bad["records"] if record["instrument"] == "AAA"]
        with self.assertRaises(RLlibDeferredPrepError):
            GovernedRLlibTrainEvalAdapter().prepare(bad)


class TestStubRLlibBackend(unittest.TestCase):
    def test_stub_returns_rollout_schema(self) -> None:
        prepared = GovernedRLlibTrainEvalAdapter().prepare(MINIMAL_DATASET)
        result = StubRLlibBackend().train_and_evaluate(prepared, RLlibTrainingConfig())
        self.assertEqual(result.backend, "stub_rllib")
        self.assertIn("train", result.rollout_summary)
        self.assertEqual(result.train_eval_schema["search_space"]["strategy"], "pbt")
        self.assertEqual(result.train_eval_schema["split_steps"]["train"], prepared.num_train_steps)

    def test_run_id_is_unique(self) -> None:
        prepared = GovernedRLlibTrainEvalAdapter().prepare(MINIMAL_DATASET)
        r1 = StubRLlibBackend().train_and_evaluate(prepared, RLlibTrainingConfig())
        r2 = StubRLlibBackend().train_and_evaluate(prepared, RLlibTrainingConfig())
        self.assertNotEqual(r1.run_id, r2.run_id)


class TestRLlibPPOBackend(unittest.TestCase):
    def test_real_backend_uses_ray_import_path(self) -> None:
        fake_ray = types.ModuleType("ray")
        fake_ray.__version__ = "2.9.3"
        fake_rllib = types.ModuleType("ray.rllib")
        prepared = GovernedRLlibTrainEvalAdapter().prepare(MINIMAL_DATASET)
        with patch.dict(sys.modules, {"ray": fake_ray, "ray.rllib": fake_rllib}, clear=False):
            result = RLlibPPOBackend().train_and_evaluate(prepared, RLlibTrainingConfig())
        self.assertEqual(result.backend, "rllib_ppo")
        self.assertTrue(result.metrics["framework_import_ready"])
        self.assertEqual(result.policy_payload["framework_import"], "ray.rllib")
        self.assertEqual(result.policy_payload["fit_mode"], "bounded_offline_rllib_adapter")

    def test_real_backend_does_not_delegate_to_stub(self) -> None:
        fake_ray = types.ModuleType("ray")
        fake_ray.__version__ = "2.9.3"
        fake_rllib = types.ModuleType("ray.rllib")
        prepared = GovernedRLlibTrainEvalAdapter().prepare(MINIMAL_DATASET)
        with patch.dict(sys.modules, {"ray": fake_ray, "ray.rllib": fake_rllib}, clear=False):
            with patch.object(
                StubRLlibBackend,
                "train_and_evaluate",
                side_effect=AssertionError("stub called"),
            ):
                result = RLlibPPOBackend().train_and_evaluate(prepared, RLlibTrainingConfig())
        self.assertEqual(result.backend, "rllib_ppo")


class TestDeferredPrepGate(unittest.TestCase):
    def test_cli_gate_requires_explicit_flag(self) -> None:
        with self.assertRaises(EnvironmentError):
            DeferredPrepGate.require_cli_flag(False)

    def test_env_gate_requires_explicit_enable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_RLLIB_PREP_ENABLED", None)
            with self.assertRaises(EnvironmentError):
                DeferredPrepGate.require_env()

    def test_env_gate_accepts_enabled_value(self) -> None:
        with patch.dict(os.environ, {"PANTHEON_RLLIB_PREP_ENABLED": "1"}, clear=False):
            DeferredPrepGate.require_env()


class TestRLlibEntrypointGates(unittest.TestCase):
    def test_smoke_entrypoint_requires_cli_flag(self) -> None:
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(smoke_main([]), 2)

    def test_worker_entrypoint_requires_env_gate(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_RLLIB_PREP_ENABLED", None)
            with patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(worker_main(), 2)

    def test_enabled_worker_persists_artifact_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "PANTHEON_RLLIB_PREP_ENABLED": "1",
                "PANTHEON_RLLIB_BACKEND": "stub",
                "RLLIB_OUTPUT_DIR": tmpdir,
            }
            with patch.dict(os.environ, env, clear=False):
                with patch("sys.stdout", new=io.StringIO()) as stdout:
                    self.assertEqual(worker_main(), 0)
            output = json.loads(stdout.getvalue())
            artifact_paths = output["artifact_paths"]
            self.assertTrue(Path(artifact_paths["artifact_bundle"]).exists())
            self.assertTrue(Path(artifact_paths["registry_entry"]).exists())
            self.assertTrue(Path(artifact_paths["candidate_packet"]).exists())
            persisted = json.loads(Path(artifact_paths["artifact_bundle"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["governance"]["gate_state"], "closed")
            self.assertEqual(persisted["registry_hints"]["artifact_state"], "draft")


class TestRunRLlibWorkflow(unittest.TestCase):
    def test_registry_entry_starts_as_draft(self) -> None:
        result = run_rllib_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["artifact_state"], "draft")
        self.assertEqual(result.registry_entry["deployment_summary"]["current_stage"], "none")

    def test_candidate_packet_targets_candidate(self) -> None:
        result = run_rllib_workflow(MINIMAL_DATASET)
        packet = result.candidate_packet
        self.assertEqual(packet["current_artifact_state"], "draft")
        self.assertEqual(packet["requested_artifact_state"], "candidate")
        self.assertEqual(packet["candidate_registry_projection"]["artifact_state"], "candidate")

    def test_rollout_schema_is_preserved_in_artifact_bundle(self) -> None:
        result = run_rllib_workflow(MINIMAL_DATASET)
        schema = result.artifact_bundle["train_eval_schema"]
        self.assertEqual(schema["optimizer_method"], "rllib_ppo")
        self.assertEqual(schema["observation_shape"][1], 8)
        self.assertGreater(schema["joint_action_cardinality"], 0)


if __name__ == "__main__":
    unittest.main()
