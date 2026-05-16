"""Unit tests for the TraderTrajectory contract (IMT-001)."""

from __future__ import annotations

import unittest

from jsonschema import Draft7Validator

from dataset_builder import DatasetBuildRequest, build_dataset
from trajectory_models import (
    TraderTrajectory,
    TraderTrajectoryValidationError,
    load_trader_trajectory_schema,
    validate_trader_trajectory_payload,
)


_TARGET = {
    "registry_id": "reg-alpha-v2",
    "strategy_id": "alpha-mean-reversion",
    "artifact_version": "2.0.0",
    "artifact_type": "strategy_spec",
    "promotion_state": "candidate",
}

_CHECKSUM = "sha256:" + ("a" * 64)


def _trajectory_payload(**overrides: object) -> dict:
    payload = {
        "schema_version": "1.0",
        "trajectory_id": "traj-001",
        "strategy_id": "alpha-mean-reversion",
        "actor_id": "operator-01",
        "actor_role": "operator",
        "decision": "approve",
        "target": _TARGET,
        "steps": [
            {
                "step_id": "step-001",
                "observation": [0.9, 0.1, -0.2],
                "action": "buy_small",
                "reward": 0.3,
                "feedback_event_id": "fb-001",
                "observed_at": "2026-05-16T09:00:00Z",
            }
        ],
        "lineage": {
            "source_trace_refs": ["teaching-session://ts-001/messages/1"],
            "source_feedback_event_ids": ["fb-001"],
            "source_dataset_refs": ["dataset://trainer/traces/2026-05-16"],
            "teaching_session_id": "ts-001",
            "task_ref": "IMT-001",
        },
        "storage_ref": {
            "backend": "object_store",
            "path": "research/imitation/trajectories/traj-001.json",
            "checksum": _CHECKSUM,
        },
        "governance": {
            "research_only": True,
            "direct_live_influence": False,
            "eligible_for_training": True,
        },
        "observed_at": "2026-05-16T09:00:00Z",
        "created_at": "2026-05-16T09:01:00Z",
        "context": {
            "surface": "trainer_workbench",
        },
        "metadata": {
            "notes": "sample trajectory",
        },
    }
    payload.update(overrides)
    return payload


class TestTraderTrajectorySchema(unittest.TestCase):
    def test_schema_is_valid_draft7(self) -> None:
        Draft7Validator.check_schema(load_trader_trajectory_schema())

    def test_trajectory_round_trips_through_model_and_schema(self) -> None:
        payload = _trajectory_payload()

        validate_trader_trajectory_payload(payload)
        trajectory = TraderTrajectory.from_dict(payload)

        self.assertEqual(trajectory.trajectory_id, "traj-001")
        self.assertEqual(trajectory.target.promotion_state, "candidate")
        self.assertEqual(trajectory.to_dict(), payload)
        validate_trader_trajectory_payload(trajectory.to_dict())

    def test_schema_rejects_system_actor_role(self) -> None:
        with self.assertRaisesRegex(TraderTrajectoryValidationError, "actor_role"):
            TraderTrajectory.from_dict(_trajectory_payload(actor_role="system"))

    def test_schema_rejects_live_target(self) -> None:
        target = {**_TARGET, "promotion_state": "live"}

        with self.assertRaisesRegex(TraderTrajectoryValidationError, "promotion_state"):
            TraderTrajectory.from_dict(_trajectory_payload(target=target))

    def test_model_rejects_target_strategy_mismatch(self) -> None:
        target = {**_TARGET, "strategy_id": "other-strategy"}

        with self.assertRaisesRegex(TraderTrajectoryValidationError, "target.strategy_id"):
            TraderTrajectory.from_dict(_trajectory_payload(target=target), validate_schema=False)

    def test_no_order_decision_requires_reason(self) -> None:
        with self.assertRaisesRegex(TraderTrajectoryValidationError, "no_order_reason"):
            TraderTrajectory.from_dict(_trajectory_payload(decision="no_order"))

    def test_no_order_step_requires_reason(self) -> None:
        step = {
            "observation": [0.1, 0.2],
            "action": "no_order",
            "feedback_event_id": "fb-no-order",
        }

        with self.assertRaisesRegex(TraderTrajectoryValidationError, "no_order_reason"):
            TraderTrajectory.from_dict(_trajectory_payload(decision="approve", steps=[step]))

    def test_governance_blocks_direct_live_influence(self) -> None:
        governance = {
            "research_only": True,
            "direct_live_influence": True,
        }

        with self.assertRaisesRegex(TraderTrajectoryValidationError, "direct_live_influence"):
            TraderTrajectory.from_dict(_trajectory_payload(governance=governance))

    def test_storage_ref_requires_sha256_checksum(self) -> None:
        storage_ref = {
            "backend": "object_store",
            "path": "research/imitation/trajectories/traj-001.json",
            "checksum": "sha256:not-a-checksum",
        }

        with self.assertRaisesRegex(TraderTrajectoryValidationError, "checksum"):
            TraderTrajectory.from_dict(_trajectory_payload(storage_ref=storage_ref))

    def test_trajectory_can_feed_dataset_builder(self) -> None:
        trajectory = TraderTrajectory.from_dict(_trajectory_payload())
        request = DatasetBuildRequest.from_dict(
            {
                "dataset_id": "imit-ds-001",
                "strategy_id": "alpha-mean-reversion",
                "source_dataset_refs": ["dataset://trainer/traces/2026-05-16"],
                "source_strategy_spec_id": "strat-alpha-mean-reversion-v2",
                "sessions": [trajectory.to_dataset_session()],
            }
        )

        result = build_dataset(request)

        self.assertEqual(result.num_included, 1)
        self.assertEqual(result.dataset_payload["sessions"][0]["trajectory_id"], "traj-001")
        self.assertEqual(result.dataset_payload["sessions"][0]["steps"][0]["feedback_event_id"], "fb-001")


if __name__ == "__main__":
    unittest.main()
