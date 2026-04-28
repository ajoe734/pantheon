"""Unit tests for the governed Ray Tune deferred-prep adapter."""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    RLlibTrainingConfig,
    GovernedRayTuneSearchAdapter,
    RayTuneDeferredPrepError,
    RayTuneDeferredPrepGate,
    RayTuneImportBackend,
    RayTuneSearchConfig,
    StubRayTuneBackend,
    run_ray_tune_workflow,
)


def load_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "train_eval_input_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


SAMPLE_DATASET = load_dataset()


class TestGovernedRayTuneSearchAdapter(unittest.TestCase):
    def test_happy_path_materializes_six_dimensional_search_space(self) -> None:
        prepared = GovernedRayTuneSearchAdapter().prepare(SAMPLE_DATASET)
        self.assertEqual(prepared.dataset.strategy_id, "multi-agent-portfolio-rebalance")
        self.assertEqual(prepared.search_strategy, "pbt")
        self.assertEqual(len(prepared.search_space_schema["parameters"]), 6)
        self.assertEqual(prepared.num_trials, 16)

    def test_invalid_search_strategy_raises(self) -> None:
        with self.assertRaises(RayTuneDeferredPrepError):
            GovernedRayTuneSearchAdapter().prepare(
                SAMPLE_DATASET,
                search_config=RayTuneSearchConfig(search_strategy="random"),
            )

    def test_top_k_cannot_exceed_num_trials(self) -> None:
        with self.assertRaises(RayTuneDeferredPrepError):
            GovernedRayTuneSearchAdapter().prepare(
                SAMPLE_DATASET,
                search_config=RayTuneSearchConfig(num_trials=8, top_k=9),
            )


class TestStubRayTuneBackend(unittest.TestCase):
    def test_stub_returns_ranked_trials(self) -> None:
        result = run_ray_tune_workflow(SAMPLE_DATASET)
        trials = result.search_result.trial_results
        self.assertEqual(len(trials), 16)
        self.assertEqual(trials[0].rank, 1)
        self.assertGreaterEqual(
            trials[0].metrics["validation_sharpe_proxy"],
            trials[-1].metrics["validation_sharpe_proxy"],
        )
        self.assertEqual(trials[0].candidate_artifact["artifact_state"], "draft")

    def test_summary_reports_top_candidates(self) -> None:
        result = run_ray_tune_workflow(SAMPLE_DATASET)
        summary = result.search_result.summary
        self.assertEqual(summary["num_trials"], 16)
        self.assertEqual(summary["top_k"], 3)
        self.assertEqual(len(summary["recommended_candidate_registry_ids"]), 3)


class TestRayTuneImportBackend(unittest.TestCase):
    def test_real_backend_uses_ray_tune_import_path(self) -> None:
        fake_ray = types.ModuleType("ray")
        fake_ray.__version__ = "2.9.3"
        fake_tune = types.ModuleType("ray.tune")
        with patch.dict(sys.modules, {"ray": fake_ray, "ray.tune": fake_tune}, clear=False):
            result = run_ray_tune_workflow(SAMPLE_DATASET, backend=RayTuneImportBackend())
        self.assertEqual(result.search_result.backend, "ray_tune_search")
        self.assertTrue(result.search_result.summary["framework_import_ready"])
        self.assertEqual(result.search_result.search_space_schema["framework_import"], "ray.tune")


class TestRayTuneDeferredPrepGate(unittest.TestCase):
    def test_cli_gate_requires_explicit_flag(self) -> None:
        with self.assertRaises(EnvironmentError):
            RayTuneDeferredPrepGate.require_cli_flag(False)

    def test_env_gate_requires_explicit_enable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_RAYTUNE_PREP_ENABLED", None)
            with self.assertRaises(EnvironmentError):
                RayTuneDeferredPrepGate.require_env()

    def test_env_gate_accepts_enabled_value(self) -> None:
        with patch.dict(os.environ, {"PANTHEON_RAYTUNE_PREP_ENABLED": "1"}, clear=False):
            RayTuneDeferredPrepGate.require_env()


class TestRunRayTuneWorkflow(unittest.TestCase):
    def test_registry_entry_starts_as_draft_optimizer_result(self) -> None:
        result = run_ray_tune_workflow(SAMPLE_DATASET)
        self.assertEqual(result.registry_entry["artifact_type"], "optimizer_result")
        self.assertEqual(result.registry_entry["artifact_state"], "draft")
        self.assertEqual(result.registry_entry["deployment_summary"]["current_stage"], "none")

    def test_candidate_packet_targets_candidate(self) -> None:
        result = run_ray_tune_workflow(SAMPLE_DATASET)
        packet = result.candidate_packet
        self.assertEqual(packet["current_artifact_state"], "draft")
        self.assertEqual(packet["requested_artifact_state"], "candidate")
        self.assertEqual(packet["candidate_registry_projection"]["artifact_state"], "candidate")

    def test_artifact_bundle_preserves_search_schema_and_governance(self) -> None:
        result = run_ray_tune_workflow(
            SAMPLE_DATASET,
            training_config=RLlibTrainingConfig(version="1.2.3", requested_by="tester"),
        )
        bundle = result.artifact_bundle
        self.assertEqual(bundle["artifact_type"], "optimizer_result")
        self.assertEqual(bundle["search_space_schema"]["strategy"], "pbt")
        self.assertEqual(len(bundle["search_space_schema"]["parameters"]), 6)
        self.assertEqual(bundle["base_training_config"]["version"], "1.2.3")
        self.assertFalse(bundle["governance"]["direct_live_influence"])
        self.assertEqual(len(bundle["output_artifacts"]), 3)


if __name__ == "__main__":
    unittest.main()
