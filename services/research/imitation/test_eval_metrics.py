"""Unit tests for imitation evaluation metrics (IMT-006)."""

from __future__ import annotations

import json
import math
import unittest

from eval_metrics import ImitationEvaluationError, evaluate


_TRAJECTORIES = {
    "dataset_id": "eval-ds-001",
    "strategy_id": "alpha-mean-reversion",
    "source_dataset_refs": ["dataset://feedback/eval/2026-05-16"],
    "sessions": [
        {
            "trajectory_id": "traj-001",
            "actor_id": "operator-01",
            "actor_role": "operator",
            "decision": "approve",
            "target": {
                "registry_id": "reg-alpha-v2",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "2.0.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "candidate",
            },
            "steps": [
                {
                    "step_id": "step-001",
                    "observation": [1.0, 0.0],
                    "action": "buy",
                    "reward": 1.0,
                    "feedback_event_id": "fb-001",
                },
                {
                    "step_id": "step-002",
                    "observation": [0.0, 1.0],
                    "action": "sell",
                    "reward": 1.0,
                    "feedback_event_id": "fb-002",
                },
                {
                    "step_id": "step-003",
                    "observation": [0.5, 0.5],
                    "action": "hold",
                    "reward": 1.0,
                    "feedback_event_id": "fb-003",
                },
            ],
        },
        {
            "trajectory_id": "traj-002",
            "actor_id": "operator-01",
            "actor_role": "operator",
            "decision": "edit",
            "target": {
                "registry_id": "reg-alpha-v2",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "2.0.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "paper",
            },
            "steps": [
                {
                    "step_id": "step-004",
                    "observation": [0.9, 0.1],
                    "action": "buy",
                    "reward": 1.0,
                    "feedback_event_id": "fb-004",
                },
                {
                    "step_id": "step-005",
                    "observation": [0.1, 0.9],
                    "action": "sell",
                    "reward": 1.0,
                    "feedback_event_id": "fb-005",
                },
                {
                    "step_id": "step-006",
                    "observation": [0.4, 0.6],
                    "action": "hold",
                    "reward": 1.0,
                    "feedback_event_id": "fb-006",
                },
            ],
        },
    ],
}


class TestImitationEvaluationMetrics(unittest.TestCase):
    def test_perfect_match_policy_returns_zero_gap_and_zero_kl(self) -> None:
        policy = {
            "registry_id": "reg-alpha-imitation-0.1.0",
            "artifact_type": "behavior_policy",
            "policy": {
                "action_labels": ["buy", "sell", "hold"],
                "predictions": {
                    "step-001": "buy",
                    "step-002": "sell",
                    "step-003": "hold",
                    "step-004": "buy",
                    "step-005": "sell",
                    "step-006": "hold",
                },
            },
        }

        result = evaluate(policy, _TRAJECTORIES)

        self.assertEqual(result["artifact_type"], "evaluation_result")
        self.assertEqual(result["target_artifact_type"], "behavior_policy")
        self.assertEqual(result["target_artifact_id"], "reg-alpha-imitation-0.1.0")
        self.assertEqual(result["action_match_rate"], 1.0)
        self.assertEqual(result["return_gap"], 0.0)
        self.assertEqual(result["kl_divergence"], 0.0)
        self.assertEqual(result["evaluation_summary"]["expert_return"], 6.0)
        self.assertEqual(result["evaluation_summary"]["policy_return"], 6.0)
        json.dumps(result, sort_keys=True)

    def test_random_uniform_policy_matches_one_over_action_count(self) -> None:
        policy = {
            "registry_id": "reg-alpha-random-baseline",
            "artifact_type": "behavior_policy",
            "policy": {
                "predictor": "uniform_random",
                "action_labels": ["buy", "sell", "hold"],
            },
        }

        result = evaluate(policy, _TRAJECTORIES)

        self.assertAlmostEqual(result["action_match_rate"], 1.0 / 3.0, places=12)
        self.assertAlmostEqual(result["return_gap"], 4.0, places=12)
        self.assertAlmostEqual(result["kl_divergence"], math.log(3.0), places=12)
        self.assertEqual(result["evaluation_summary"]["n_actions"], 3)
        self.assertEqual(result["registry_hints"]["artifact_type"], "evaluation_result")

    def test_counterfactual_rewards_feed_return_gap(self) -> None:
        trajectories = {
            "sessions": [
                {
                    "trajectory_id": "traj-counterfactual",
                    "steps": [
                        {
                            "step_id": "step-a",
                            "observation": [0.0],
                            "action": "buy",
                            "reward": 2.0,
                            "reward_by_action": {"buy": 2.0, "hold": 0.5},
                        }
                    ],
                }
            ]
        }
        policy = {
            "registry_id": "reg-alpha-hold",
            "policy": {
                "action_labels": ["buy", "hold"],
                "default_action_probabilities": {"buy": 0.0, "hold": 1.0},
            },
        }

        result = evaluate(policy, trajectories)

        self.assertEqual(result["action_match_rate"], 0.0)
        self.assertEqual(result["evaluation_summary"]["policy_return"], 0.5)
        self.assertEqual(result["return_gap"], 1.5)

    def test_missing_prediction_source_raises(self) -> None:
        with self.assertRaisesRegex(ImitationEvaluationError, "behavior policy must provide"):
            evaluate({"registry_id": "reg-alpha-empty", "policy": {"action_labels": ["buy"]}}, _TRAJECTORIES)


if __name__ == "__main__":
    unittest.main()
