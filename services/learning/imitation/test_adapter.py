import json
import sys
import unittest
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    GovernedTrajectoryAdapter,
    ImitationWorkflowError,
    StubBehaviorCloningBackend,
    TrainingConfig,
    run_imitation_workflow,
)


class TestImitationWorkflow(unittest.TestCase):
    def load_sample(self) -> dict:
        sample_path = SERVICE_DIR / "examples" / "trajectory_dataset_sample.json"
        return json.loads(sample_path.read_text(encoding="utf-8"))

    def test_adapter_filters_non_governed_sessions(self):
        dataset = self.load_sample()
        dataset["sessions"].append(
            {
                "trajectory_id": "traj-rejected",
                "actor_id": "trader-99",
                "actor_role": "operator",
                "decision": "reject",
                "target": {
                    "registry_id": "reg-alpha-2",
                    "strategy_id": "alpha-mean-reversion",
                    "artifact_version": "1.2.0",
                    "artifact_type": "strategy_spec",
                    "promotion_state": "candidate",
                },
                "steps": [
                    {
                        "observation": [0.1, 0.1, 0.1],
                        "action": "hold",
                        "feedback_event_id": "evt-reject-1",
                    }
                ],
            }
        )
        dataset["sessions"].append(
            {
                "trajectory_id": "traj-live",
                "actor_id": "trader-88",
                "actor_role": "operator",
                "decision": "approve",
                "target": {
                    "registry_id": "reg-alpha-3",
                    "strategy_id": "alpha-mean-reversion",
                    "artifact_version": "1.2.0",
                    "artifact_type": "strategy_spec",
                    "promotion_state": "live",
                },
                "steps": [
                    {
                        "observation": [0.2, 0.1, 0.0],
                        "action": "buy_small",
                        "feedback_event_id": "evt-live-1",
                    }
                ],
            }
        )

        prepared = GovernedTrajectoryAdapter().prepare(dataset)

        self.assertEqual(prepared.num_trajectories, 2)
        self.assertIn("traj-rejected", prepared.filtered_trajectories)
        self.assertIn("traj-live", prepared.filtered_trajectories)

    def test_stub_workflow_builds_registry_ready_artifact(self):
        result = run_imitation_workflow(
            self.load_sample(),
            backend=StubBehaviorCloningBackend(),
            config=TrainingConfig(version="0.2.0", requested_by="Codex", epochs=2),
        )

        self.assertEqual(result.registry_entry["artifact_type"], "model_artifact")
        self.assertEqual(result.registry_entry["lifecycle_state"], "draft")
        self.assertEqual(result.registry_entry["metadata"]["model_family"], "imitation_policy")
        self.assertEqual(
            result.registry_entry["lineage"]["source_dataset_refs"],
            ["dataset://feedback/approved/2026-04-06"],
        )
        self.assertTrue(result.registry_entry["checksum"].startswith("sha256:"))
        self.assertGreaterEqual(result.training_result.metrics["training_accuracy"], 0.5)

    def test_adapter_rejects_when_no_governed_sessions_remain(self):
        dataset = self.load_sample()
        dataset["sessions"] = [
            {
                "trajectory_id": "traj-bad",
                "actor_id": "sys-1",
                "actor_role": "system",
                "decision": "approve",
                "target": {
                    "registry_id": "reg-alpha-4",
                    "strategy_id": "alpha-mean-reversion",
                    "artifact_version": "1.2.0",
                    "artifact_type": "strategy_spec",
                    "promotion_state": "candidate",
                },
                "steps": [
                    {
                        "observation": [0.2, 0.2, 0.2],
                        "action": "hold",
                        "feedback_event_id": "evt-system-1",
                    }
                ],
            }
        ]

        with self.assertRaisesRegex(ImitationWorkflowError, "No governed trajectories remain"):
            GovernedTrajectoryAdapter().prepare(dataset)


if __name__ == "__main__":
    unittest.main()
