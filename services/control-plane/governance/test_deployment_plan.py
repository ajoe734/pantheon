"""
Unit tests for DeploymentPlan governance contract.

Run:
    python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deployment_plan import (
    DeploymentPlan,
    DeploymentPlanError,
    DeploymentPlanStore,
    DeploymentScale,
    DeploymentStage,
    PlanStatus,
    RollbackActionType,
    RollbackRef,
    RuntimeAction,
    ScheduleWindow,
    StagePlanner,
    TransitionType,
    validate_plan,
    validate_plan_json,
)

GOVERNANCE_DIR = Path(__file__).resolve().parent


def approved_registry_entry(stage: str = "none") -> dict:
    return {
        "registry_id": "reg-strat-001-1.2.0",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-001",
        "version": "1.2.0",
        "artifact_state": "approved",
        "checksum": "sha256:abc123def4567890",
        "approval_decision_id": "approval-001",
        "approved_at": "2026-04-09T12:00:00Z",
        "lineage": {"source_run_ids": ["replication-run-001"]},
        "deployment_summary": {"current_stage": stage},
    }


def approved_decision() -> dict:
    return {
        "decision_id": "approval-001",
        "target_id": "reg-strat-001-1.2.0",
        "target_version": "1.2.0",
        "decision_state": "decided",
        "decision": "approved",
        "capital_pool_id": "pool-001",
        "persona_id": "persona-ops",
    }


def rollback_ref(
    action_type: RollbackActionType = RollbackActionType.REPLACE,
) -> RollbackRef:
    return RollbackRef(
        target_artifact_id="reg-strat-001-1.1.0",
        target_version="1.1.0",
        action_type=action_type,
        reason="Previous approved baseline",
    )


class TestStagePlannerTransitions(unittest.TestCase):
    def setUp(self):
        self.planner = StagePlanner()

    def test_none_to_paper_is_activate(self):
        transition = self.planner.derive_transition_type(DeploymentStage.NONE, DeploymentStage.PAPER)
        self.assertEqual(transition, TransitionType.ACTIVATE)

    def test_paper_to_canary_is_promote(self):
        transition = self.planner.derive_transition_type(DeploymentStage.PAPER, DeploymentStage.CANARY)
        self.assertEqual(transition, TransitionType.PROMOTE)

    def test_live_to_paper_is_rollback(self):
        transition = self.planner.derive_transition_type(DeploymentStage.LIVE, DeploymentStage.PAPER)
        self.assertEqual(transition, TransitionType.ROLLBACK)

    def test_live_to_frozen_is_freeze(self):
        transition = self.planner.derive_transition_type(DeploymentStage.LIVE, DeploymentStage.FROZEN)
        self.assertEqual(transition, TransitionType.FREEZE)

    def test_frozen_to_live_is_resume(self):
        transition = self.planner.derive_transition_type(DeploymentStage.FROZEN, DeploymentStage.LIVE)
        self.assertEqual(transition, TransitionType.RESUME)

    def test_none_to_canary_is_forbidden(self):
        with self.assertRaises(DeploymentPlanError):
            self.planner.derive_transition_type(DeploymentStage.NONE, DeploymentStage.CANARY)

    def test_paper_to_live_skip_is_forbidden(self):
        with self.assertRaises(DeploymentPlanError):
            self.planner.derive_transition_type(DeploymentStage.PAPER, DeploymentStage.LIVE)


class TestDeploymentPlanCreation(unittest.TestCase):
    def setUp(self):
        self.planner = StagePlanner()

    def test_create_initial_paper_plan(self):
        plan = self.planner.create_plan(
            plan_id="plan-paper-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(),
        )

        self.assertEqual(plan.transition_type, TransitionType.ACTIVATE)
        self.assertEqual(plan.runtime_action, RuntimeAction.DEPLOY_NEW_BINDING)
        self.assertEqual(plan.scale.capital_scale_pct, 0.0)
        self.assertEqual(plan.scale.gross_scale_pct, 100.0)
        self.assertEqual(validate_plan(plan), [])

    def test_create_canary_plan_uses_policy_defaults(self):
        plan = self.planner.create_plan(
            plan_id="plan-canary-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="paper"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.CANARY,
            rollback=rollback_ref(),
        )

        self.assertEqual(plan.transition_type, TransitionType.PROMOTE)
        self.assertEqual(plan.runtime_action, RuntimeAction.REPLACE_BINDING)
        self.assertEqual(plan.scale.capital_scale_pct, 5.0)
        self.assertEqual(plan.scale.gross_scale_pct, 25.0)

    def test_risk_policy_rejection_blocks_plan_creation(self):
        with self.assertRaisesRegex(DeploymentPlanError, "RiskPolicy"):
            self.planner.create_plan(
                plan_id="plan-canary-risk-reject",
                approval_decision_id="approval-001",
                approval_decision=approved_decision(),
                registry_entry=approved_registry_entry(stage="paper"),
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-ops",
                target_stage=DeploymentStage.CANARY,
                rollback=rollback_ref(),
                risk_policy={
                    "risk_policy_id": "risk-main",
                    "max_canary_capital_scale_pct": 2.0,
                    "max_canary_gross_scale_pct": 10.0,
                },
            )

    def test_risk_policy_pass_is_recorded_in_plan_metadata(self):
        plan = self.planner.create_plan(
            plan_id="plan-canary-risk-pass",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="paper"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.CANARY,
            rollback=rollback_ref(),
            risk_policy={
                "risk_policy_id": "risk-main",
                "max_canary_capital_scale_pct": 5.0,
                "max_canary_gross_scale_pct": 25.0,
            },
        )

        assert plan.metadata is not None
        self.assertEqual(plan.metadata["risk_policy_evaluation"]["decision"], "allowed")

    def test_create_live_plan(self):
        plan = self.planner.create_plan(
            plan_id="plan-live-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="canary"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.LIVE,
            rollback=rollback_ref(),
        )

        self.assertEqual(plan.transition_type, TransitionType.PROMOTE)
        self.assertEqual(plan.runtime_action, RuntimeAction.REPLACE_BINDING)
        self.assertEqual(plan.scale.capital_scale_pct, 100.0)

    def test_create_frozen_plan(self):
        plan = self.planner.create_plan(
            plan_id="plan-freeze-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="live"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.FROZEN,
        )

        self.assertEqual(plan.transition_type, TransitionType.FREEZE)
        self.assertEqual(plan.runtime_action, RuntimeAction.FREEZE_BINDING)
        self.assertEqual(plan.scale.capital_scale_pct, 0.0)
        self.assertEqual(plan.scale.gross_scale_pct, 0.0)

    def test_create_rollback_plan(self):
        plan = self.planner.create_plan(
            plan_id="plan-rollback-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="live"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(RollbackActionType.PAUSE_THEN_REPLACE),
        )

        self.assertEqual(plan.transition_type, TransitionType.ROLLBACK)
        self.assertEqual(plan.runtime_action, RuntimeAction.PAUSE_THEN_REPLACE)
        self.assertEqual(plan.rollback.action_type, RollbackActionType.PAUSE_THEN_REPLACE.value)

    def test_replace_rollback_keeps_canonical_action_type(self):
        plan = self.planner.create_plan(
            plan_id="plan-rollback-replace-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="canary"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(RollbackActionType.REPLACE),
        )

        self.assertEqual(plan.transition_type, TransitionType.ROLLBACK)
        self.assertEqual(plan.runtime_action, RuntimeAction.REPLACE_BINDING)
        self.assertEqual(plan.rollback.action_type, RollbackActionType.REPLACE.value)

    def test_requires_approved_artifact_state(self):
        entry = approved_registry_entry()
        entry["artifact_state"] = "candidate"
        with self.assertRaises(DeploymentPlanError):
            self.planner.create_plan(
                plan_id="plan-invalid-001",
                approval_decision_id="approval-001",
                approval_decision=approved_decision(),
                registry_entry=entry,
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-ops",
                target_stage=DeploymentStage.PAPER,
                rollback=rollback_ref(),
            )

    def test_deployment_stage_values_are_not_artifact_state(self):
        for artifact_state in ("paper", "canary", "live"):
            with self.subTest(artifact_state=artifact_state):
                entry = approved_registry_entry()
                entry["artifact_state"] = artifact_state
                with self.assertRaisesRegex(
                    DeploymentPlanError,
                    "requires artifact_state=approved",
                ):
                    self.planner.create_plan(
                        plan_id=f"plan-invalid-artifact-state-{artifact_state}",
                        approval_decision_id="approval-001",
                        approval_decision=approved_decision(),
                        registry_entry=entry,
                        capital_pool_id="pool-001",
                        sponsor_persona_id="persona-ops",
                        target_stage=DeploymentStage.PAPER,
                        rollback=rollback_ref(),
                    )

    def test_deployment_plan_requires_approved_artifact_and_matching_approval(self):
        candidate = approved_registry_entry()
        candidate["artifact_state"] = "candidate"
        with self.assertRaisesRegex(DeploymentPlanError, "requires artifact_state=approved"):
            self.planner.create_plan(
                plan_id="plan-candidate-blocked",
                approval_decision_id="approval-001",
                approval_decision=approved_decision(),
                registry_entry=candidate,
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-ops",
                target_stage=DeploymentStage.PAPER,
                rollback=rollback_ref(),
            )

        mismatched_decision = approved_decision()
        mismatched_decision["target_id"] = "reg-other"
        with self.assertRaisesRegex(DeploymentPlanError, "target_id must match"):
            self.planner.create_plan(
                plan_id="plan-approval-mismatch",
                approval_decision_id="approval-001",
                approval_decision=mismatched_decision,
                registry_entry=approved_registry_entry(),
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-ops",
                target_stage=DeploymentStage.PAPER,
                rollback=rollback_ref(),
            )

    def test_requires_matching_approval_decision(self):
        decision = approved_decision()
        decision["target_version"] = "9.9.9"
        with self.assertRaises(DeploymentPlanError):
            self.planner.create_plan(
                plan_id="plan-invalid-approval",
                approval_decision_id="approval-001",
                approval_decision=decision,
                registry_entry=approved_registry_entry(),
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-ops",
                target_stage=DeploymentStage.PAPER,
                rollback=rollback_ref(),
            )

    def test_requires_explicit_rollback_for_active_target(self):
        with self.assertRaises(DeploymentPlanError):
            self.planner.create_plan(
                plan_id="plan-no-rollback",
                approval_decision_id="approval-001",
                approval_decision=approved_decision(),
                registry_entry=approved_registry_entry(),
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-ops",
                target_stage=DeploymentStage.PAPER,
            )

    def test_canary_scale_cannot_exceed_policy(self):
        plan = DeploymentPlan(
            plan_id="plan-canary-bad",
            approval_decision_id="approval-001",
            artifact_id="reg-strat-001-1.2.0",
            artifact_version="1.2.0",
            artifact_type="model_artifact",
            strategy_id="strat-001",
            capital_pool_id="pool-001",
            current_stage=DeploymentStage.PAPER,
            target_stage=DeploymentStage.CANARY,
            transition_type=TransitionType.PROMOTE,
            runtime_action=RuntimeAction.REPLACE_BINDING,
            status=PlanStatus.APPROVED,
            created_at="2026-04-09T12:30:00Z",
            scale=DeploymentScale(capital_scale_pct=10.0, gross_scale_pct=30.0),
            rollback=rollback_ref(),
        )

        errors = validate_plan(plan)
        self.assertTrue(any("capital_scale_pct" in error for error in errors))
        self.assertTrue(any("gross_scale_pct" in error for error in errors))

    def test_schedule_window_must_be_ordered(self):
        plan = self.planner.create_plan(
            plan_id="plan-window-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="paper"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.CANARY,
            rollback=rollback_ref(),
            schedule_window=ScheduleWindow(
                start_at="2026-04-10T09:00:00Z",
                end_at="2026-04-10T10:00:00Z",
            ),
        )

        self.assertEqual(validate_plan(plan), [])

        invalid_plan = DeploymentPlan.from_dict(plan.to_dict())
        invalid_plan.schedule_window = ScheduleWindow(
            start_at="2026-04-10T10:00:00Z",
            end_at="2026-04-10T09:00:00Z",
        )
        errors = validate_plan(invalid_plan)
        self.assertTrue(any("end_at must be after start_at" in error for error in errors))

    def test_executed_plan_allows_current_stage_to_match_target_stage(self):
        plan = self.planner.create_plan(
            plan_id="plan-paper-executed",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(),
        )
        plan.current_stage = DeploymentStage.PAPER
        plan.status = PlanStatus.EXECUTED

        self.assertEqual(validate_plan(plan), [])

        plan.status = PlanStatus.EXECUTING
        errors = validate_plan(plan)
        self.assertTrue(any("target_stage must differ" in error for error in errors))


class TestExecutionProjection(unittest.TestCase):
    def setUp(self):
        self.planner = StagePlanner()

    def test_projection_contains_canonical_fields(self):
        plan = self.planner.create_plan(
            plan_id="plan-paper-002",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(),
        )

        projection = self.planner.build_execution_projection(plan, approved_registry_entry())
        self.assertEqual(
            projection.metadata_key,
            "openclaw/registry/strat-001/1.2.0/metadata.json",
        )
        self.assertEqual(projection.metadata["artifact_state"], "approved")
        self.assertEqual(projection.metadata["deployment_stage"], "paper")
        self.assertEqual(projection.metadata["deployment_plan_id"], "plan-paper-002")
        self.assertEqual(projection.metadata["approval_decision_id"], "approval-001")
        self.assertEqual(projection.metadata["rollback"]["target_registry_id"], "reg-strat-001-1.1.0")
        self.assertEqual(projection.metadata["promotion_state"], "paper")

    def test_canary_projection_keeps_deployment_stage_without_legacy_alias(self):
        plan = self.planner.create_plan(
            plan_id="plan-canary-002",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="paper"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.CANARY,
            rollback=rollback_ref(),
        )

        projection = self.planner.build_execution_projection(plan, approved_registry_entry(stage="paper"))
        self.assertEqual(projection.metadata["deployment_stage"], "canary")
        self.assertNotIn("promotion_state", projection.metadata)


class TestDeploymentPlanSerialization(unittest.TestCase):
    def test_roundtrip_dict(self):
        planner = StagePlanner()
        plan = planner.create_plan(
            plan_id="plan-serialize-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(),
        )

        restored = DeploymentPlan.from_dict(plan.to_dict())
        self.assertEqual(restored.plan_id, plan.plan_id)
        self.assertEqual(restored.rollback.target_artifact_id, "reg-strat-001-1.1.0")
        self.assertEqual(restored.rollback.action_type, RollbackActionType.REPLACE.value)

    def test_json_validation_helper(self):
        planner = StagePlanner()
        plan = planner.create_plan(
            plan_id="plan-schema-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(stage="paper"),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.CANARY,
            rollback=rollback_ref(),
        )
        self.assertEqual(validate_plan_json(plan.to_dict()), [])

        invalid = plan.to_dict()
        invalid.pop("capital_pool_id")
        invalid["rollback"]["action_type"] = "restart"
        errors = validate_plan_json(invalid)
        self.assertTrue(any("capital_pool_id" in error for error in errors))
        self.assertTrue(any("rollback.action_type" in error for error in errors))

    def test_store_roundtrip(self):
        planner = StagePlanner()
        plan = planner.create_plan(
            plan_id="plan-store-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.PAPER,
            rollback=rollback_ref(),
        )

        with tempfile.NamedTemporaryFile(mode="w+", delete=True) as tmp:
            store = DeploymentPlanStore(tmp.name)
            store.put(plan)
            store2 = DeploymentPlanStore(tmp.name)
            restored = store2.get("plan-store-001")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.plan_id, "plan-store-001")
            self.assertEqual(restored.capital_pool_id, "pool-001")

    def test_schema_file_exists(self):
        schema_path = GOVERNANCE_DIR / "deployment_plan.schema.json"
        self.assertTrue(schema_path.exists(), "deployment_plan.schema.json must exist")


if __name__ == "__main__":
    unittest.main()
