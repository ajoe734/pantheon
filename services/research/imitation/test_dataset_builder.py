"""Unit tests for the imitation dataset builder (IMT-003)."""

from __future__ import annotations

import unittest

from dataset_builder import (
    DatasetBuildRequest,
    DatasetBuilderError,
    ImitationDatasetBuilder,
    ImitationDatasetBuilderConfig,
    RawTrajectorySession,
    TrajectoryStep,
    build_dataset,
)

_STEP_A = {"observation": [0.9, 0.1, -0.2], "action": "buy_small", "reward": 0.3, "feedback_event_id": "evt-001"}
_STEP_B = {"observation": [-0.5, 0.3, 0.1], "action": "reduce_risk", "reward": 0.1, "feedback_event_id": "evt-002"}

_TARGET_OK = {
    "registry_id": "reg-1",
    "strategy_id": "strat-a",
    "artifact_version": "1.0.0",
    "artifact_type": "strategy_spec",
    "promotion_state": "candidate",
}

_SESSION_OK = {
    "trajectory_id": "traj-001",
    "actor_id": "trader-01",
    "actor_role": "operator",
    "decision": "approve",
    "target": _TARGET_OK,
    "steps": [_STEP_A, _STEP_B],
}

_REQUEST_BASE = {
    "dataset_id": "ds-001",
    "strategy_id": "strat-a",
    "source_dataset_refs": ["dataset://fb/2026-05-16"],
    "sessions": [_SESSION_OK],
}


def _make_request(**overrides: object) -> DatasetBuildRequest:
    return DatasetBuildRequest.from_dict({**_REQUEST_BASE, **overrides})


class TestDatasetBuildRequest(unittest.TestCase):
    def test_from_dict_happy_path(self) -> None:
        req = DatasetBuildRequest.from_dict(_REQUEST_BASE)
        self.assertEqual(req.dataset_id, "ds-001")
        self.assertEqual(req.strategy_id, "strat-a")
        self.assertEqual(len(req.sessions), 1)
        self.assertEqual(len(req.sessions[0].steps), 2)

    def test_missing_dataset_id_raises(self) -> None:
        bad = dict(_REQUEST_BASE)
        del bad["dataset_id"]
        with self.assertRaises(DatasetBuilderError):
            DatasetBuildRequest.from_dict(bad)

    def test_missing_source_refs_raises(self) -> None:
        with self.assertRaises(DatasetBuilderError):
            DatasetBuildRequest.from_dict({**_REQUEST_BASE, "source_dataset_refs": []})

    def test_session_missing_target_raises(self) -> None:
        bad_session = {**_SESSION_OK, "target": None}
        with self.assertRaises(DatasetBuilderError):
            DatasetBuildRequest.from_dict({**_REQUEST_BASE, "sessions": [bad_session]})

    def test_step_non_numeric_observation_raises(self) -> None:
        bad_step = {"observation": ["a", "b"], "action": "hold"}
        bad_session = {**_SESSION_OK, "steps": [bad_step]}
        with self.assertRaises(DatasetBuilderError):
            DatasetBuildRequest.from_dict({**_REQUEST_BASE, "sessions": [bad_session]})

    def test_step_missing_action_raises(self) -> None:
        bad_step = {"observation": [0.1, 0.2]}
        bad_session = {**_SESSION_OK, "steps": [bad_step]}
        with self.assertRaises(DatasetBuilderError):
            DatasetBuildRequest.from_dict({**_REQUEST_BASE, "sessions": [bad_session]})


class TestImitationDatasetBuilder(unittest.TestCase):
    def _build(self, sessions: list[dict], **req_overrides: object) -> object:
        return build_dataset(DatasetBuildRequest.from_dict({**_REQUEST_BASE, "sessions": sessions, **req_overrides}))

    def test_happy_path_single_session(self) -> None:
        result = self._build([_SESSION_OK])
        self.assertEqual(result.num_included, 1)
        self.assertEqual(result.num_filtered, 0)
        payload = result.dataset_payload
        self.assertEqual(payload["dataset_id"], "ds-001")
        self.assertEqual(len(payload["sessions"]), 1)

    def test_filters_disallowed_actor_role(self) -> None:
        bad = {**_SESSION_OK, "trajectory_id": "traj-bad", "actor_role": "external"}
        result = self._build([_SESSION_OK, bad])
        self.assertEqual(result.num_included, 1)
        self.assertIn("traj-bad", result.filtered_trajectory_ids)

    def test_filters_ineligible_decision(self) -> None:
        bad = {**_SESSION_OK, "trajectory_id": "traj-rej", "decision": "reject"}
        result = self._build([_SESSION_OK, bad])
        self.assertIn("traj-rej", result.filtered_trajectory_ids)

    def test_filters_disallowed_promotion_state(self) -> None:
        bad_target = {**_TARGET_OK, "promotion_state": "live"}
        bad = {**_SESSION_OK, "trajectory_id": "traj-live", "target": bad_target}
        result = self._build([_SESSION_OK, bad])
        self.assertIn("traj-live", result.filtered_trajectory_ids)

    def test_filters_strategy_id_mismatch(self) -> None:
        bad_target = {**_TARGET_OK, "strategy_id": "other-strat"}
        bad = {**_SESSION_OK, "trajectory_id": "traj-mismatch", "target": bad_target}
        result = self._build([_SESSION_OK, bad])
        self.assertIn("traj-mismatch", result.filtered_trajectory_ids)

    def test_all_filtered_raises(self) -> None:
        bad = {**_SESSION_OK, "actor_role": "external"}
        with self.assertRaises(DatasetBuilderError):
            self._build([bad])

    def test_decision_alias_approved(self) -> None:
        aliased = {**_SESSION_OK, "decision": "approved"}
        result = self._build([aliased])
        self.assertEqual(result.num_included, 1)

    def test_decision_alias_edited(self) -> None:
        aliased = {**_SESSION_OK, "decision": "edited"}
        result = self._build([aliased])
        self.assertEqual(result.num_included, 1)

    def test_paper_promotion_state_allowed(self) -> None:
        paper_target = {**_TARGET_OK, "promotion_state": "paper"}
        paper_session = {**_SESSION_OK, "trajectory_id": "traj-paper", "target": paper_target}
        result = self._build([paper_session])
        self.assertEqual(result.num_included, 1)

    def test_source_strategy_spec_id_propagated(self) -> None:
        result = self._build([_SESSION_OK], source_strategy_spec_id="strat-a-v2")
        self.assertEqual(result.dataset_payload.get("source_strategy_spec_id"), "strat-a-v2")

    def test_no_source_strategy_spec_id_omitted(self) -> None:
        result = self._build([_SESSION_OK])
        self.assertNotIn("source_strategy_spec_id", result.dataset_payload)

    def test_target_block_in_payload(self) -> None:
        result = self._build([_SESSION_OK])
        session = result.dataset_payload["sessions"][0]
        self.assertIn("target", session)
        self.assertEqual(session["target"]["promotion_state"], "candidate")

    def test_reward_optional(self) -> None:
        step_no_reward = {"observation": [0.1, 0.2], "action": "hold", "feedback_event_id": "evt-x"}
        session = {**_SESSION_OK, "steps": [step_no_reward]}
        result = self._build([session])
        self.assertEqual(result.num_included, 1)
        step_out = result.dataset_payload["sessions"][0]["steps"][0]
        self.assertNotIn("reward", step_out)

    def test_require_feedback_event_ids_strict(self) -> None:
        step_no_evt = {"observation": [0.5, 0.5], "action": "hold"}
        session = {**_SESSION_OK, "trajectory_id": "traj-strict", "steps": [step_no_evt]}
        config = ImitationDatasetBuilderConfig(require_feedback_event_ids=True)
        req = DatasetBuildRequest.from_dict({**_REQUEST_BASE, "sessions": [session]})
        # single session is filtered for missing feedback_event_id -> all-filtered -> raises
        with self.assertRaises(DatasetBuilderError) as ctx:
            ImitationDatasetBuilder(config).build(req)
        self.assertIn("feedback_event_id", str(ctx.exception))

    def test_require_feedback_event_ids_strict_partial(self) -> None:
        step_no_evt = {"observation": [0.5, 0.5], "action": "hold"}
        bad_session = {**_SESSION_OK, "trajectory_id": "traj-strict", "steps": [step_no_evt]}
        config = ImitationDatasetBuilderConfig(require_feedback_event_ids=True)
        req = DatasetBuildRequest.from_dict({**_REQUEST_BASE, "sessions": [_SESSION_OK, bad_session]})
        result = ImitationDatasetBuilder(config).build(req)
        self.assertEqual(result.num_included, 1)
        self.assertIn("traj-strict", result.filtered_trajectory_ids)

    def test_build_id_prefix(self) -> None:
        result = self._build([_SESSION_OK])
        self.assertTrue(result.build_id.startswith("dsb-"))

    def test_built_at_is_iso(self) -> None:
        result = self._build([_SESSION_OK])
        self.assertIn("T", result.built_at)
        self.assertTrue(result.built_at.endswith("Z"))

    def test_multiple_sessions_preserved_order(self) -> None:
        s2 = {**_SESSION_OK, "trajectory_id": "traj-002", "target": {**_TARGET_OK, "promotion_state": "paper"}}
        result = self._build([_SESSION_OK, s2])
        ids = [s["trajectory_id"] for s in result.dataset_payload["sessions"]]
        self.assertEqual(ids, ["traj-001", "traj-002"])


if __name__ == "__main__":
    unittest.main()
