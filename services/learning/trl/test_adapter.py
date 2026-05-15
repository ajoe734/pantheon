"""Unit tests for the governed TRL DPO preference-learning adapter."""
from __future__ import annotations

import unittest
import os
import subprocess
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

from services.feedback.models import (
    ActorRole,
    ArtifactType,
    Channel,
    EditItem,
    EditOperation,
    FeedbackEventType,
    GovernedLinkage,
    PromotionState,
    TraderFeedbackEvent,
)
from services.learning.trl.adapter import (
    ActivationReadyGate,
    GovernedPreferencePairAdapter,
    PreferencePair,
    StubDPOBackend,
    TrainingConfig,
    TRLWorkflowError,
    persist_trl_run_artifacts,
    run_trl_dpo_workflow,
    run_trl_dpo_workflow_from_feedback_store,
    validate_activation_ready_dataset,
)

SERVICE_DIR = Path(__file__).resolve().parent

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

    def test_build_dataset_from_fb002_dataclass_events(self) -> None:
        events = [
            TraderFeedbackEvent(
                event_id="fb002-001",
                event_type=FeedbackEventType.APPROVE,
                created_at="2026-04-30T04:00:00Z",
                actor_id="operator-1",
                actor_role=ActorRole.OPERATOR,
                channel=Channel.API,
                target=GovernedLinkage(
                    strategy_id="family-a",
                    registry_id="reg-a",
                    artifact_type=ArtifactType.MODEL_ARTIFACT,
                    promotion_state=PromotionState.CANDIDATE,
                ),
            ),
            TraderFeedbackEvent(
                event_id="fb002-002",
                event_type=FeedbackEventType.EDIT,
                created_at="2026-04-30T04:01:00Z",
                actor_id="operator-2",
                actor_role=ActorRole.APPROVER,
                channel=Channel.API,
                target=GovernedLinkage(
                    strategy_id="family-b",
                    registry_id="reg-b",
                    artifact_type=ArtifactType.MODEL_ARTIFACT,
                    promotion_state=PromotionState.PAPER,
                ),
                edits=[EditItem(path="/threshold", operation=EditOperation.REPLACE, value=0.8)],
            ),
        ]
        ds = self.adapter.build_dataset_from_feedback_events(
            events,
            dataset_id="fb002-ds",
            strategy_id="preference-strategy",
            source_dataset_refs=["feedback-store://fb002"],
        )
        self.assertEqual(ds.num_pairs, 2)
        self.assertEqual(ds.source_feedback_event_ids, ("fb002-001", "fb002-002"))
        self.assertEqual(ds.action_distribution["edit"], 1)
        edit_pair = [p for p in ds.pairs if p.action == "edit"][0]
        self.assertTrue(edit_pair.chosen["artifact_id"].endswith(":edited"))


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

    def test_candidate_packet_targets_candidate_without_deployment(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-candidate",
            strategy_id="strat-candidate",
            source_dataset_refs=["ref-candidate"],
        )
        packet = result.candidate_packet
        self.assertEqual(packet["current_artifact_state"], "draft")
        self.assertEqual(packet["requested_artifact_state"], "candidate")
        self.assertEqual(packet["deployment_stage"], "none")
        self.assertEqual(packet["candidate_registry_projection"]["artifact_state"], "candidate")
        self.assertEqual(packet["evaluator_handoff"]["minimum_artifact_state_for_scoring"], "approved")

    def test_evaluator_packet_references_model_artifact_without_routing(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-eval",
            strategy_id="strat-eval",
            source_dataset_refs=["ref-eval"],
        )
        packet = result.evaluator_packet
        self.assertEqual(packet["artifact_type"], "evaluation_result")
        self.assertEqual(packet["target_artifact_id"], result.registry_entry["registry_id"])
        self.assertEqual(packet["target_artifact_type"], "model_artifact")
        self.assertEqual(packet["target_promotion_state"], "draft")
        self.assertFalse(packet["handoff_boundary"]["direct_governance_write"])
        self.assertFalse(packet["handoff_boundary"]["order_routing_enabled"])

    def test_activation_ready_data_floors_reject_small_dataset(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-small",
            strategy_id="strat-small",
            source_dataset_refs=["ref-small"],
        )
        with self.assertRaises(TRLWorkflowError):
            validate_activation_ready_dataset(
                result.preference_dataset,
                source_event_count=len(self._events()),
            )

    def test_activation_ready_data_floors_accept_large_diverse_dataset(self) -> None:
        events = [
            _approve_event(i, "family-a" if i % 2 == 0 else "family-b")
            for i in range(1, 201)
        ]
        result = run_trl_dpo_workflow(
            events,
            dataset_id="ds-large",
            strategy_id="strat-large",
            source_dataset_refs=["ref-large"],
            enforce_activation_ready=True,
        )
        self.assertEqual(result.preference_dataset.num_pairs, 200)
        self.assertTrue(
            result.registry_entry["metadata"]["entry_criteria_satisfied"][
                "activation_ready_data_volume_met"
            ]
        )

    def test_run_workflow_from_feedback_store_events(self) -> None:
        events = [
            TraderFeedbackEvent(
                event_id=f"fb-store-{i}",
                event_type=FeedbackEventType.APPROVE,
                created_at="2026-04-30T04:00:00Z",
                actor_id=f"operator-{i}",
                actor_role=ActorRole.OPERATOR,
                channel=Channel.API,
                target=GovernedLinkage(
                    strategy_id="store-family-a" if i == 1 else "store-family-b",
                    registry_id=f"reg-store-{i}",
                    artifact_type=ArtifactType.MODEL_ARTIFACT,
                    promotion_state=PromotionState.CANDIDATE,
                ),
            )
            for i in (1, 2)
        ]
        result = run_trl_dpo_workflow_from_feedback_store(
            events,
            dataset_id="ds-store",
            strategy_id="strat-store",
            source_dataset_refs=["feedback-store://memory"],
        )
        self.assertEqual(result.preference_dataset.num_pairs, 2)
        self.assertEqual(result.training_result.backend, "stub_dpo")

    def test_persist_artifacts_writes_handoff_files(self) -> None:
        result = run_trl_dpo_workflow(
            self._events(),
            dataset_id="ds-persist",
            strategy_id="strat-persist",
            source_dataset_refs=["ref-persist"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifest = persist_trl_run_artifacts(result, tmp)
            self.assertIn("candidate_packet", manifest["files"])
            self.assertIn("evaluator_packet", manifest["files"])
            for path in manifest["files"].values():
                self.assertTrue(os.path.exists(path))


class TestActivationReadyGate(unittest.TestCase):
    def test_cli_gate_requires_explicit_flag(self) -> None:
        with self.assertRaises(EnvironmentError):
            ActivationReadyGate.require_cli_flag(False)

    def test_env_gate_requires_explicit_enable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_TRL_ACTIVATION_READY_ENABLED", None)
            with self.assertRaises(EnvironmentError):
                ActivationReadyGate.require_env()

    def test_env_gate_accepts_enabled_value(self) -> None:
        with patch.dict(os.environ, {"PANTHEON_TRL_ACTIVATION_READY_ENABLED": "1"}, clear=False):
            ActivationReadyGate.require_env()

    def test_worker_entrypoint_requires_env_gate(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SERVICE_DIR / "worker.py")],
            cwd=str(SERVICE_DIR),
            env={k: v for k, v in os.environ.items() if k != "PANTHEON_TRL_ACTIVATION_READY_ENABLED"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("disabled by default", proc.stderr)


if __name__ == "__main__":
    unittest.main()
