"""
Unit tests for EvolutionController operational boundary routing.

Run:
    python3 -m unittest services/control-plane/governance/test_evolution_controller.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from approval_decision import EvidenceRef, EvidenceRefType
from deployment_plan import DeploymentStage, RollbackActionType
from evolution_controller import (
    EvolutionController,
    EvolutionControllerError,
    FreezeFollowthroughMode,
    ThresholdEvaluator,
)
from evolution_decision import (
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionState,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
)


def make_evidence() -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
        ref_id="telemetry-sum-001",
        storage_ref={"backend": "object_store", "path": "/telemetry/summary/001"},
    )


def make_threshold(
    *,
    signal_type: ThresholdSignalType = ThresholdSignalType.GOVERNANCE_INCIDENT,
    metric_name: str = "severity1_incident_count",
    observed_value: int | float = 1,
    threshold_value: int | float = 1,
) -> ThresholdSnapshot:
    comparator = ComparisonOperator.GTE if observed_value >= threshold_value else ComparisonOperator.LT
    return ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.5",
        signal_type=signal_type,
        metric_name=metric_name,
        comparator=comparator,
        observed_value=observed_value,
        threshold_value=threshold_value,
        window="30d",
    )


def make_approved_decision(
    *,
    decision_id: str = "evo-ctrl-001",
    action_type: EvolutionActionType = EvolutionActionType.FREEZE,
    target_stage: str | None = "live",
    linked_incident_id: str | None = "inc-001",
    linked_postmortem_id: str | None = "pm-001",
) -> EvolutionDecision:
    decision = EvolutionDecision.create_proposed(
        decision_id=decision_id,
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-001",
        target_version="1.2.3",
        action_type=action_type,
        rationale="Normal-path operational evolution routing test.",
        created_by_id="evolution-controller-01",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[make_evidence()],
        threshold_snapshots=[make_threshold()],
        linked_incident_id=linked_incident_id,
        linked_postmortem_id=linked_postmortem_id,
        capital_pool_id="pool-001",
        persona_id="persona-001",
        target_stage=target_stage,
    )
    reviewer = (
        EvolutionActorRole.GOVERNANCE_COMMITTEE
        if decision.risk_level == "high"
        else EvolutionActorRole.REVIEWER_ON_DUTY
    )
    approver = reviewer
    decision.mark_reviewed(reviewer, "reviewer-01", "approval-001")
    decision.approve(approver, "approver-01")
    return decision


class TestEvolutionControllerDispatch(unittest.TestCase):
    def test_freeze_live_without_runtime_stays_governance_only(self):
        controller = EvolutionController()
        decision = make_approved_decision()

        outcome = controller.execute_approved(
            decision,
            actor_id="controller-01",
            executed_at="2026-04-11T10:00:00Z",
            has_active_runtime=False,
        )

        self.assertEqual(outcome.boundary.boundary_key, "freeze_live_no_active_runtime")
        self.assertEqual(outcome.primary_command.execution_plane, "governance")
        self.assertEqual(outcome.primary_command.action_type, "freeze")
        self.assertEqual(outcome.primary_command.target_type, "candidate_artifact")
        self.assertFalse(outcome.followthrough_commands)
        self.assertIsNone(outcome.rollback_command)
        self.assertEqual(decision.decision_state, EvolutionDecisionState.EXECUTED)
        self.assertEqual(decision.execution_result.plane, "governance")
        self.assertEqual(decision.cooldown_ends_at, "2026-04-25T10:00:00Z")

    def test_freeze_live_with_stage_freeze_emits_deployment_followthrough(self):
        controller = EvolutionController()
        decision = make_approved_decision(decision_id="evo-ctrl-002")

        outcome = controller.execute_approved(
            decision,
            actor_id="controller-01",
            executed_at="2026-04-11T10:00:00Z",
            has_active_runtime=True,
            freeze_mode=FreezeFollowthroughMode.FREEZE_STAGE,
        )

        self.assertEqual(outcome.boundary.boundary_key, "freeze_live_active_runtime")
        self.assertEqual(outcome.primary_command.execution_plane, "governance")
        self.assertEqual(len(outcome.followthrough_commands), 1)
        followthrough = outcome.followthrough_commands[0]
        self.assertEqual(followthrough.execution_plane, "deployment")
        self.assertEqual(followthrough.action_type, "freeze_stage")
        self.assertEqual(followthrough.target_stage, "frozen")
        self.assertIsNone(outcome.rollback_command)

    def test_freeze_live_with_runtime_rollback_emits_companion_request(self):
        controller = EvolutionController()
        decision = make_approved_decision(decision_id="evo-ctrl-003")

        outcome = controller.execute_approved(
            decision,
            actor_id="controller-01",
            executed_at="2026-04-11T10:00:00Z",
            has_active_runtime=True,
            active_binding_id="rb-live-001",
            freeze_mode=FreezeFollowthroughMode.ROLLBACK,
            fallback_artifact_id="artifact-000",
            fallback_artifact_version="1.2.2",
        )

        self.assertEqual(outcome.primary_command.execution_plane, "governance")
        self.assertIsNotNone(outcome.rollback_command)
        self.assertEqual(outcome.rollback_command.rollback_action_type, RollbackActionType.PAUSE_THEN_REPLACE)
        self.assertEqual(outcome.rollback_command.target_binding_id, "rb-live-001")
        self.assertEqual(outcome.rollback_command.fallback_artifact_id, "artifact-000")
        self.assertEqual(decision.execution_result.plane, "governance")

    def test_force_risk_off_defaults_to_liquidate_then_replace(self):
        controller = EvolutionController()
        decision = make_approved_decision(
            decision_id="evo-ctrl-004",
            action_type=EvolutionActionType.FORCE_RISK_OFF,
            target_stage=None,
        )

        outcome = controller.execute_approved(
            decision,
            actor_id="controller-01",
            executed_at="2026-04-11T10:00:00Z",
            has_active_runtime=True,
            active_binding_id="rb-live-002",
        )

        self.assertEqual(outcome.boundary.boundary_key, "force_risk_off_runtime")
        self.assertEqual(outcome.primary_command.execution_plane, "runtime")
        self.assertIsNotNone(outcome.rollback_command)
        self.assertEqual(
            outcome.rollback_command.rollback_action_type,
            RollbackActionType.LIQUIDATE_THEN_REPLACE,
        )
        self.assertEqual(decision.execution_result.plane, "runtime")

    def test_retrain_dispatches_to_research_with_low_risk_window(self):
        controller = EvolutionController()
        decision = make_approved_decision(
            decision_id="evo-ctrl-005",
            action_type=EvolutionActionType.RETRAIN,
            target_stage=None,
            linked_postmortem_id=None,
        )

        outcome = controller.execute_approved(
            decision,
            actor_id="controller-01",
            executed_at="2026-04-11T10:00:00Z",
        )

        self.assertEqual(outcome.boundary.boundary_key, "research_retrain")
        self.assertEqual(outcome.primary_command.execution_plane, "research")
        self.assertEqual(decision.cooldown_ends_at, "2026-04-14T10:00:00Z")
        self.assertEqual(decision.observation_window_ends_at, "2026-04-18T10:00:00Z")

    def test_redeploy_followthrough_requires_parent_in_observation(self):
        controller = EvolutionController()
        decision = make_approved_decision(
            decision_id="evo-ctrl-006",
            action_type=EvolutionActionType.RETRAIN,
            target_stage=None,
            linked_postmortem_id=None,
        )
        controller.execute_approved(
            decision,
            actor_id="controller-01",
            executed_at="2026-04-11T10:00:00Z",
        )

        command = controller.create_redeploy_followthrough(
            decision,
            artifact_id="artifact-002",
            artifact_version="2.0.0",
            approval_decision_id="approval-002",
            target_stage=DeploymentStage.CANARY,
            requested_at="2026-04-12T10:00:00Z",
        )

        self.assertEqual(command.execution_plane, "deployment")
        self.assertEqual(command.action_type, "redeploy_followthrough")
        self.assertEqual(command.target_type, "candidate_artifact")
        self.assertEqual(command.target_stage, "canary")
        self.assertEqual(command.metadata["parent_action_type"], "retrain")
        self.assertEqual(command.metadata["parent_decision_id"], "evo-ctrl-006")
        self.assertTrue(command.metadata["requires_new_deployment_plan"])

    def test_redeploy_followthrough_rejects_non_executed_parent(self):
        controller = EvolutionController()
        decision = make_approved_decision(decision_id="evo-ctrl-007")

        with self.assertRaises(EvolutionControllerError):
            controller.create_redeploy_followthrough(
                decision,
                artifact_id="artifact-002",
                artifact_version="2.0.0",
                approval_decision_id="approval-002",
                target_stage=DeploymentStage.CANARY,
            )


class TestThresholdEvaluator(unittest.TestCase):
    def test_slippage_drift_maps_to_revalidate(self):
        evaluator = ThresholdEvaluator()
        snapshot = make_threshold(
            signal_type=ThresholdSignalType.EXECUTION_DRIFT,
            metric_name="slippage_drift_pct",
            observed_value=30.0,
            threshold_value=25.0,
        )

        outcome = evaluator.classify(snapshot)

        self.assertEqual(outcome.proposed_action, EvolutionActionType.REVALIDATE)

    def test_critical_incident_maps_to_freeze_with_runtime_followthrough(self):
        evaluator = ThresholdEvaluator()
        snapshot = make_threshold()

        outcome = evaluator.classify(
            snapshot,
            context={"incident_severity": "critical", "has_active_runtime": True},
        )

        self.assertEqual(outcome.proposed_action, EvolutionActionType.FREEZE)
        self.assertTrue(outcome.requires_runtime_followthrough)

    def test_rollback_problem_persists_requires_committee_review(self):
        evaluator = ThresholdEvaluator()
        snapshot = make_threshold(
            metric_name="rollback_problem_persists",
            observed_value=1,
            threshold_value=1,
        )

        outcome = evaluator.classify(snapshot, context={"has_active_runtime": True})

        self.assertEqual(outcome.proposed_action, EvolutionActionType.FREEZE)
        self.assertTrue(outcome.committee_review_required)


if __name__ == "__main__":
    unittest.main()
