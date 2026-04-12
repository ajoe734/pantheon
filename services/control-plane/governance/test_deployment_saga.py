"""
Unit tests for the DEP-002 deployment saga backbone.

Run:
    python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'
"""
from __future__ import annotations

import tempfile
import unittest

from deployment_plan import (
    DeploymentPlan,
    PlanStatus,
    RollbackActionType,
    RollbackRef,
    RuntimeAction,
    TransitionType,
)
from deployment_saga import (
    CompensationCommand,
    DeploymentSagaStore,
    ReceiptStatus,
    SagaStatus,
    SagaStep,
)


def build_plan(
    *,
    plan_id: str = "plan-paper-001",
    current_stage: str = "none",
    target_stage: str = "paper",
    transition_type: str = "activate",
    runtime_action: RuntimeAction = RuntimeAction.DEPLOY_NEW_BINDING,
    rollback_action: RollbackActionType | str = RollbackActionType.REPLACE,
) -> DeploymentPlan:
    return DeploymentPlan(
        plan_id=plan_id,
        approval_decision_id="approval-001",
        artifact_id="reg-strat-001-1.2.0",
        artifact_version="1.2.0",
        artifact_type="model_artifact",
        strategy_id="strat-001",
        capital_pool_id="pool-001",
        current_stage=current_stage,
        target_stage=target_stage,
        transition_type=transition_type,
        runtime_action=runtime_action,
        status=PlanStatus.APPROVED,
        created_at="2026-04-10T00:00:00Z",
        sponsor_persona_id="persona-ops",
        rollback=RollbackRef(
            target_artifact_id="reg-strat-001-1.1.0",
            target_version="1.1.0",
            action_type=rollback_action,
            reason="Previous approved baseline",
        ),
    )


class TestDeploymentSagaAtomicity(unittest.TestCase):
    def test_bootstrap_accepts_canonical_replace_rollback_action(self):
        store = DeploymentSagaStore()

        bootstrap = store.bootstrap_for_plan(build_plan(), trace_id="trace-canonical-001")

        self.assertEqual(bootstrap.saga.rollback_action_type, RollbackActionType.REPLACE.value)

    def test_bootstrap_persists_saga_and_first_outbox_event(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=True) as tmp:
            store = DeploymentSagaStore(tmp.name)
            bootstrap = store.bootstrap_for_plan(build_plan(), trace_id="trace-001")

            self.assertEqual(bootstrap.saga.status, SagaStatus.AWAITING_BINDING)
            self.assertEqual(bootstrap.saga.current_step, SagaStep.BINDING_REQUESTED)
            self.assertEqual(bootstrap.saga.last_sequence_no, 1)
            self.assertEqual(bootstrap.outbox_event.event.sequence_no, 1)
            self.assertEqual(bootstrap.outbox_event.event.event_type, "runtime.binding.requested")
            self.assertEqual(len(store.pending_outbox()), 1)

            reloaded = DeploymentSagaStore(tmp.name)
            persisted = reloaded.get(bootstrap.saga.saga_id)
            self.assertIsNotNone(persisted)
            self.assertEqual(len(reloaded.pending_outbox()), 1)

    def test_bootstrap_normalizes_legacy_replace_binding_to_canonical_replace(self):
        plan = build_plan(rollback_action=RuntimeAction.REPLACE_BINDING.value)
        store = DeploymentSagaStore()

        bootstrap = store.bootstrap_for_plan(plan, trace_id="trace-legacy-001")

        self.assertEqual(bootstrap.saga.rollback_action_type, RollbackActionType.REPLACE.value)

    def test_commit_failure_rolls_back_saga_and_outbox(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=True) as tmp:
            store = DeploymentSagaStore(tmp.name, before_commit=lambda _draft: (_ for _ in ()).throw(RuntimeError("boom")))

            with self.assertRaises(RuntimeError):
                store.bootstrap_for_plan(build_plan(), trace_id="trace-001")

            self.assertEqual(store.list_all(), [])
            self.assertEqual(store.pending_outbox(), [])
            reloaded = DeploymentSagaStore(tmp.name)
            self.assertEqual(reloaded.list_all(), [])
            self.assertEqual(reloaded.pending_outbox(), [])


class TestDeploymentSagaOrderingAndInbox(unittest.TestCase):
    def test_consumer_is_idempotent_and_preserves_per_aggregate_order(self):
        store = DeploymentSagaStore()
        bootstrap = store.bootstrap_for_plan(build_plan(), trace_id="trace-001")
        saga_id = bootstrap.saga.saga_id
        seq2 = store.record_binding_created(saga_id, binding_id="binding-001", runtime_id="runtime-001")
        seq3 = store.record_runtime_active(saga_id, binding_id="binding-001", runtime_id="runtime-001")

        applied_events: list[str] = []

        def apply(event):
            applied_events.append(event.event_id)

        receipt1 = store.consume_event("lineage-projector", bootstrap.outbox_event.event, apply_fn=apply)
        duplicate = store.consume_event("lineage-projector", bootstrap.outbox_event.event, apply_fn=apply)
        out_of_order = store.consume_event("lineage-projector", seq3.event, apply_fn=apply)
        receipt2 = store.consume_event("lineage-projector", seq2.event, apply_fn=apply)
        receipt3 = store.consume_event("lineage-projector", seq3.event, apply_fn=apply)

        self.assertEqual(receipt1.status, ReceiptStatus.APPLIED)
        self.assertEqual(duplicate.status, ReceiptStatus.DUPLICATE)
        self.assertEqual(out_of_order.status, ReceiptStatus.OUT_OF_ORDER)
        self.assertEqual(receipt2.status, ReceiptStatus.APPLIED)
        self.assertEqual(receipt3.status, ReceiptStatus.APPLIED)
        self.assertEqual(applied_events, [bootstrap.outbox_event.event.event_id, seq2.event.event_id, seq3.event.event_id])
        self.assertEqual(seq2.event.causal_parent_id, bootstrap.outbox_event.event.event_id)
        self.assertEqual(seq3.event.causal_parent_id, seq2.event.event_id)


class TestDeploymentSagaCompensation(unittest.TestCase):
    def test_binding_create_failure_maps_to_abort_plan(self):
        store = DeploymentSagaStore()
        bootstrap = store.bootstrap_for_plan(build_plan(), trace_id="trace-001")

        decision = store.record_failure(bootstrap.saga.saga_id, reason="runtime-manager rejected binding create")
        saga = store.get(bootstrap.saga.saga_id)

        self.assertEqual(decision.command_type, CompensationCommand.ABORT_PLAN)
        self.assertEqual(decision.owner_service, "governance-svc")
        self.assertEqual(decision.plan_status, "aborted")
        self.assertEqual(decision.binding_status, "not_created")
        self.assertEqual(saga.status, SagaStatus.COMPENSATING)
        self.assertEqual(saga.compensation.command_type, CompensationCommand.ABORT_PLAN)

        final_event = store.finalize_compensation(bootstrap.saga.saga_id)
        self.assertEqual(final_event.event.event_type, "deployment.saga.aborted")
        self.assertEqual(store.get(bootstrap.saga.saga_id).status, SagaStatus.ABORTED)

    def test_runtime_load_failure_marks_binding_failed_inactive(self):
        store = DeploymentSagaStore()
        bootstrap = store.bootstrap_for_plan(build_plan(), trace_id="trace-001")
        store.record_binding_created(bootstrap.saga.saga_id, binding_id="binding-001", runtime_id="runtime-001")

        decision = store.record_failure(bootstrap.saga.saga_id, reason="runtime load timed out")

        self.assertEqual(decision.command_type, CompensationCommand.MARK_BINDING_FAILED_INACTIVE)
        self.assertEqual(decision.owner_service, "runtime-manager-svc")
        self.assertEqual(decision.binding_status, "failed_inactive")

    def test_post_activation_failure_uses_plan_rollback_action(self):
        store = DeploymentSagaStore()
        bootstrap = store.bootstrap_for_plan(
            build_plan(
                plan_id="plan-live-001",
                current_stage="canary",
                target_stage="live",
                transition_type=TransitionType.PROMOTE,
                runtime_action=RuntimeAction.REPLACE_BINDING,
                rollback_action=RollbackActionType.PAUSE_THEN_REPLACE,
            ),
            trace_id="trace-live-001",
        )
        store.record_binding_created(bootstrap.saga.saga_id, binding_id="binding-live-001", runtime_id="runtime-live-001")
        store.record_runtime_active(bootstrap.saga.saga_id, binding_id="binding-live-001", runtime_id="runtime-live-001")

        decision = store.record_failure(
            bootstrap.saga.saga_id,
            reason="severe mismatch after cutover",
            failed_step=SagaStep.RUNTIME_ACTIVE,
        )

        self.assertEqual(decision.command_type, CompensationCommand.REQUEST_ROLLBACK)
        self.assertEqual(decision.owner_service, "rollback-controller")
        self.assertEqual(decision.runtime_action, RollbackActionType.PAUSE_THEN_REPLACE.value)

    def test_compensation_failure_escalates_to_safe_mode_incident(self):
        store = DeploymentSagaStore()
        bootstrap = store.bootstrap_for_plan(build_plan(), trace_id="trace-001")

        decision = store.record_failure(
            bootstrap.saga.saga_id,
            reason="rollback worker could not converge",
            failed_step=SagaStep.COMPENSATION_REQUESTED,
        )

        self.assertEqual(decision.command_type, CompensationCommand.ENTER_SAFE_MODE_AND_RAISE_INCIDENT)
        self.assertTrue(decision.safe_mode)
        self.assertTrue(decision.incident_required)


if __name__ == "__main__":
    unittest.main()
