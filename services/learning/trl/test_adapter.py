"""Unit tests for the governed TRL DPO preference-learning adapter."""
from __future__ import annotations

import unittest

from services.learning.trl.adapter import (
    GovernedPreferencePairAdapter,
    PreferencePair,
    StubDPOBackend,
    TrainingConfig,
    TRLWorkflowError,
    run_trl_dpo_workflow,
)


# Minimal valid event fixtures
def _approve_event(idx: int = 1, family: str = "equity_cross_sectional") -> dict:
    return {
        "feedback_event_id": f"fb-{idx:03d}",
        "actor_role": "operator",
        "promotion_state": "candidate",
        "action": "approve",
        "strategy_family": family,
        "operator_id": f"op-{idx}",
        "artifact": {"artifact_id": f"art-{idx}", "sharpe": 1.0},
    }


def _reject_event(idx: int = 2, family: str = "stat_arb") -> dict:
    return {
        "feedback_event_id": f"fb-{idx:03d}",
        "actor_role": "approver",
        "promotion_state": "paper",
        "action": "reject",
        "strategy_family": family,
        "operator_id": f"op-{idx}",
        "artifact": {"artifact_id": f"art-{idx}", "sharpe": 0.3},
    }


def _edit_event(idx: int = 3, family: str = "equity_cross_sectional") -> dict:
    return {
        "feedback_event_id": f"fb-{idx:03d}",
        "actor_role": "operator",
        "promotion_state": "candidate",
        "action": "edit",
        "strategy_family": family,
        "operator_id": f"op-{idx}",
        "artifact": {"artifact_id": f"art-{idx}-orig", "sharpe": 0.9},
        "artifact_edited": {"artifact_id": f"art-{idx}-edit", "sharpe": 1.1},
    }


class TestGovernedPreferencePairAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = GovernedPreferencePairAdapter()

    def test_approve_event_constructs_null_rejected(self) -> None:
        ds = self.adapter.build_dataset(
            [_approve_event(1, "equity_cross_sectional"), _reject_event(2, "stat_arb")],
            dataset_id="ds-001",
            strategy_id="strat-001",
            source_dataset_refs=["ref-001"],
        )
        approve_pairs = [p for p in ds.pairs if p.action == "approve"]
        self.assertEqual(len(approve_pairs), 1)
        self.assertTrue(approve_pairs[0].rejected.get("is_null"))
        self.assertFalse(approve_pairs[0].chosen.get("is_null", False))

    def test_reject_event_constructs_null_chosen(self) -> None:
        ds = self.adapter.build_dataset(
            [_approve_event(1, "equity_cross_sectional"), _reject_event(2, "stat_arb")],
            dataset_id="ds-002",
            strategy_id="strat-001",
            source_dataset_refs=["ref-001"],
        )
        reject_pairs = [p for p in ds.pairs if p.action == "reject"]
        self.assertEqual(len(reject_pairs), 1)
        self.assertTrue(reject_pairs[0].chosen.get("is_null"))
        self.assertFalse(reject_pairs[0].rejected.get("is_null", False))

    def test_edit_event_constructs_edited_chosen(self) -> None:
        ds = self.adapter.build_dataset(
            [_approve_event(1, "equity_cross_sectional"), _edit_event(2, "stat_arb")],
            dataset_id="ds-003",
            strategy_id="strat-001",
            source_dataset_refs=["ref-001"],
        )
        edit_pairs = [p for p in ds.pairs if p.action == "edit"]
        self.assertEqual(len(edit_pairs), 1)
        self.assertEqual(edit_pairs[0].chosen["artifact_id"], "art-2-edit")
        self.assertEqual(edit_pairs[0].rejected["artifact_id"], "art-2-orig")

    def test_invalid_actor_role_raises(self) -> None:
        ev = _approve_event(1, "equity_cross_sectional")
        ev["actor_role"] = "trader"  # not in ALLOWED_ACTOR_ROLES
        with self.assertRaises(TRLWorkflowError):
            self.adapter.build_dataset(
                [ev, _reject_event(2, "stat_arb")],
                dataset_id="ds",
                strategy_id="strat",
                source_dataset_refs=["ref"],
            )

    def test_invalid_promotion_state_raises(self) -> None:
        ev = _reject_event(2, "stat_arb")
        ev["promotion_state"] = "live"  # not in ALLOWED_PROMOTION_STATES
        with self.assertRaises(TRLWorkflowError):
            self.adapter.build_dataset(
                [_approve_event(1, "equity_cross_sectional"), ev],
                dataset_id="ds",
                strategy_id="strat",
                source_dataset_refs=["ref"],
            )

    def test_missing_artifact_raises(self) -> None:
        ev = _approve_event(1, "equity_cross_sectional")
        del ev["artifact"]
        with self.assertRaises(TRLWorkflowError):
            self.adapter.build_dataset(
                [ev, _reject_event(2, "stat_arb")],
                dataset_id="ds",
                strategy_id="strat",
                source_dataset_refs=["ref"],
            )

    def test_strategy_families_collected(self) -> None:
        ds = self.adapter.build_dataset(
            [_approve_event(1, "equity_cross_sectional"), _reject_event(2, "stat_arb")],
            dataset_id="ds",
            strategy_id="strat",
            source_dataset_refs=["ref"],
        )
        self.assertIn("equity_cross_sectional", ds.strategy_families)
        self.assertIn("stat_arb", ds.strategy_families)

    def test_empty_events_raises(self) -> None:
        with self.assertRaises(TRLWorkflowError):
            self.adapter.build_dataset(
                [],
                dataset_id="ds",
                strategy_id="strat",
                source_dataset_refs=["ref"],
            )

    def test_edit_missing_artifact_edited_raises(self) -> None:
        ev = _edit_event(1, "equity_cross_sectional")
        del ev["artifact_edited"]
        with self.assertRaises(TRLWorkflowError):
            self.adapter.build_dataset(
                [ev, _reject_event(2, "stat_arb")],
                dataset_id="ds",
                strategy_id="strat",
                source_dataset_refs=["ref"],
            )


class TestStubDPOBackend(unittest.TestCase):
    def _make_dataset(self) -> object:
        adapter = GovernedPreferencePairAdapter()
        return adapter.build_dataset(
            [_approve_event(1, "equity_cross_sectional"), _reject_event(2, "stat_arb")],
            dataset_id="ds-stub",
            strategy_id="strat-stub",
            source_dataset_refs=["ref-stub"],
        )

    def test_stub_returns_training_result(self) -> None:
        ds = self._make_dataset()
        result = StubDPOBackend().train(ds, TrainingConfig())
        self.assertEqual(result.backend, "stub_dpo")
        self.assertIn("accuracy", result.metrics)
        self.assertIn("auc_roc", result.metrics)

    def test_stub_accuracy_between_0_and_1(self) -> None:
        ds = self._make_dataset()
        result = StubDPOBackend().train(ds, TrainingConfig())
        self.assertGreaterEqual(result.metrics["accuracy"], 0.0)
        self.assertLessEqual(result.metrics["accuracy"], 1.0)


class TestRunTRLDPOWorkflow(unittest.TestCase):
    def _events(self) -> list:
        return [
            _approve_event(1, "equity_cross_sectional"),
            _reject_event(2, "stat_arb"),
            _edit_event(3, "equity_cross_sectional"),
        ]

    def test_full_workflow_returns_run_result(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-wf",
            strategy_id="strat-wf",
            source_dataset_refs=["ref-wf"],
        )
        self.assertEqual(result.registry_entry["artifact_state"], "draft")
        self.assertEqual(
            result.registry_entry["deployment_summary"]["current_stage"], "none"
        )

    def test_registry_entry_has_required_keys(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-keys",
            strategy_id="strat-keys",
            source_dataset_refs=["ref-keys"],
        )
        reg = result.registry_entry
        for key in ("registry_id", "artifact_type", "checksum", "lineage", "storage_ref"):
            self.assertIn(key, reg, f"missing key: {key}")

    def test_checksum_is_sha256(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-cs",
            strategy_id="strat-cs",
            source_dataset_refs=["ref-cs"],
        )
        self.assertTrue(result.registry_entry["checksum"].startswith("sha256:"))

    def test_governance_no_live_influence(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-gov",
            strategy_id="strat-gov",
            source_dataset_refs=["ref-gov"],
        )
        gov = result.artifact_bundle["governance"]
        self.assertFalse(gov["direct_live_influence"])
        self.assertEqual(gov["execution_stage"], "none")

    def test_lineage_carries_feedback_event_ids(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-lin",
            strategy_id="strat-lin",
            source_dataset_refs=["ref-lin"],
        )
        self.assertTrue(result.registry_entry["lineage"]["source_feedback_event_ids"])


if __name__ == "__main__":
    unittest.main()
