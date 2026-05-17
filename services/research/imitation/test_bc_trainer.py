"""Unit tests for the IMT-005 behavior cloning baseline trainer."""

from __future__ import annotations

import unittest

from bc_trainer import BCTrainerError, train, verify_artifact_checksum
from dataset_builder import DatasetBuildRequest, build_dataset


_ACTIONS = ("buy_small", "sell_small", "hold", "reduce_risk")


def _synthetic_step(index: int) -> dict[str, object]:
    action_index = index % len(_ACTIONS)
    observation = [0.0 for _ in _ACTIONS]
    observation[action_index] = 1.0
    return {
        "observation": observation,
        "action": _ACTIONS[action_index],
        "reward": 1.0,
        "feedback_event_id": f"evt-synth-{index:03d}",
    }


def _synthetic_builder_request() -> DatasetBuildRequest:
    return DatasetBuildRequest.from_dict(
        {
            "dataset_id": "imt-005-synthetic-dataset",
            "strategy_id": "imt-005-strategy",
            "source_dataset_refs": ["dataset://feedback/imt-005/synthetic"],
            "source_strategy_spec_id": "strategy-spec://imt-005/base",
            "sessions": [
                {
                    "trajectory_id": "traj-imt-005-synth",
                    "actor_id": "operator-001",
                    "actor_role": "operator",
                    "decision": "approve",
                    "target": {
                        "registry_id": "reg-imt-005-base",
                        "strategy_id": "imt-005-strategy",
                        "artifact_version": "1.0.0",
                        "artifact_type": "strategy_spec",
                        "promotion_state": "candidate",
                    },
                    "steps": [_synthetic_step(i) for i in range(50)],
                }
            ],
        }
    )


class TestBehaviorCloningTrainer(unittest.TestCase):
    def test_train_returns_behavior_policy_with_decreasing_loss(self) -> None:
        dataset_payload = build_dataset(_synthetic_builder_request()).dataset_payload

        artifact = train(
            dataset_payload,
            {
                "version": "0.1.0",
                "epochs": 30,
                "learning_rate": 0.8,
                "seed": 13,
                "requested_by": "Codex",
            },
        )

        loss_history = artifact["training"]["loss_history"]
        self.assertEqual(artifact["artifact_type"], "behavior_policy")
        self.assertEqual(artifact["model_family"], "imitation_policy")
        self.assertEqual(artifact["algorithm"], "behavior_cloning")
        self.assertEqual(artifact["trainer"], "softmax_regression_cpu")
        self.assertEqual(artifact["training"]["device"], "cpu")
        self.assertEqual(artifact["policy"]["action_labels"], list(_ACTIONS))
        self.assertEqual(artifact["training"]["num_transitions"], 50)
        self.assertGreater(loss_history[0], loss_history[-1])
        self.assertTrue(artifact["training"]["loss_decreased"])
        self.assertGreaterEqual(artifact["training"]["training_accuracy"], 0.95)
        self.assertTrue(artifact["checksum"].startswith("sha256:"))
        self.assertTrue(verify_artifact_checksum(artifact))

    def test_artifact_lineage_references_dataset_ref(self) -> None:
        dataset_payload = build_dataset(_synthetic_builder_request()).dataset_payload

        artifact = train(dataset_payload, {"epochs": 5, "learning_rate": 0.5})

        lineage = artifact["lineage"]
        self.assertEqual(lineage["source_dataset_ref"], "imt-005-synthetic-dataset")
        self.assertIn("imt-005-synthetic-dataset", lineage["source_dataset_refs"])
        self.assertIn("dataset://feedback/imt-005/synthetic", lineage["source_dataset_refs"])
        self.assertEqual(lineage["source_strategy_spec_id"], "strategy-spec://imt-005/base")
        self.assertEqual(
            artifact["registry_hints"]["source_dataset_ref"],
            "imt-005-synthetic-dataset",
        )

    def test_rejects_dataset_without_source_refs(self) -> None:
        dataset_payload = build_dataset(_synthetic_builder_request()).dataset_payload
        dataset_payload = dict(dataset_payload)
        dataset_payload["source_dataset_refs"] = []

        with self.assertRaisesRegex(BCTrainerError, "source_dataset_refs"):
            train(dataset_payload, {"epochs": 1})


if __name__ == "__main__":
    unittest.main()
