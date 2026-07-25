"""Unit tests for the governed Ray Tune deferred-prep adapter."""
from __future__ import annotations

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
from ray_tune_smoke_test import main as ray_tune_smoke_main
from ray_tune_worker import main as ray_tune_worker_main


TEST_RAY_SECURITY_ENV = {
    "RAY_AUTH_MODE": "token",
    "RAY_AUTH_TOKEN": "test-only-ray-auth-token-00000000000000000000",
}


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
        fake_ray.__version__ = "2.55.1"
        fake_tune = types.ModuleType("ray.tune")
        with patch.dict(os.environ, TEST_RAY_SECURITY_ENV, clear=False):
            with patch.dict(sys.modules, {"ray": fake_ray, "ray.tune": fake_tune}, clear=False):
                result = run_ray_tune_workflow(SAMPLE_DATASET, backend=RayTuneImportBackend())
        self.assertEqual(result.search_result.backend, "ray_tune_search")
        self.assertTrue(result.search_result.summary["framework_import_ready"])
        self.assertEqual(result.search_result.search_space_schema["framework_import"], "ray.tune")
        self.assertLessEqual(len(result.search_result.trial_results), SAMPLE_DATASET["records"].__len__())
        self.assertEqual(result.search_result.summary["bounded_trials"], 12)

    def test_real_backend_does_not_delegate_to_stub(self) -> None:
        fake_ray = types.ModuleType("ray")
        fake_ray.__version__ = "2.55.1"
        fake_tune = types.ModuleType("ray.tune")
        with patch.dict(os.environ, TEST_RAY_SECURITY_ENV, clear=False):
            with patch.dict(sys.modules, {"ray": fake_ray, "ray.tune": fake_tune}, clear=False):
                with patch.object(
                    StubRayTuneBackend,
                    "run_search",
                    side_effect=AssertionError("stub called"),
                ):
                    result = run_ray_tune_workflow(SAMPLE_DATASET, backend=RayTuneImportBackend())
        self.assertEqual(result.search_result.backend, "ray_tune_search")


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


class TestRayTuneEntrypointGates(unittest.TestCase):
    def test_smoke_entrypoint_requires_cli_flag(self) -> None:
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(ray_tune_smoke_main([]), 2)

    def test_worker_entrypoint_requires_env_gate(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_RAYTUNE_PREP_ENABLED", None)
            with patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(ray_tune_worker_main(), 2)

    def test_enabled_worker_persists_optimizer_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "PANTHEON_RAYTUNE_PREP_ENABLED": "1",
                "PANTHEON_RAYTUNE_BACKEND": "stub",
                "RAYTUNE_OUTPUT_DIR": tmpdir,
            }
            with patch.dict(os.environ, env, clear=False):
                with patch("sys.stdout", new=io.StringIO()) as stdout:
                    self.assertEqual(ray_tune_worker_main(), 0)
            output = json.loads(stdout.getvalue())
            artifact_paths = output["artifact_paths"]
            self.assertTrue(Path(artifact_paths["artifact_bundle"]).exists())
            self.assertTrue(Path(artifact_paths["registry_entry"]).exists())
            self.assertTrue(Path(artifact_paths["candidate_packet"]).exists())
            persisted = json.loads(Path(artifact_paths["artifact_bundle"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["governance"]["gate_state"], "closed")
            self.assertEqual(persisted["registry_hints"]["artifact_state"], "draft")


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
