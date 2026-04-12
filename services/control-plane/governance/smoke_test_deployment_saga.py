#!/usr/bin/env python3
"""
Smoke test for the DEP-002 deployment saga backbone.

Run:
    python3 services/control-plane/governance/smoke_test_deployment_saga.py
"""
from __future__ import annotations

import json
import sys
import tempfile

from deployment_plan import DeploymentPlan, PlanStatus, RollbackActionType, RollbackRef, RuntimeAction
from deployment_saga import (
    CompensationCommand,
    DeploymentSagaStore,
    ReceiptStatus,
    SagaStatus,
)


def check(label: str, passed: bool) -> tuple[str, bool]:
    print(f"[{'PASS' if passed else 'FAIL'}] {label}")
    return label, passed


def build_plan() -> DeploymentPlan:
    return DeploymentPlan(
        plan_id="plan-paper-001",
        approval_decision_id="approval-001",
        artifact_id="reg-strat-001-1.2.0",
        artifact_version="1.2.0",
        artifact_type="model_artifact",
        strategy_id="strat-001",
        capital_pool_id="pool-001",
        current_stage="none",
        target_stage="paper",
        transition_type="activate",
        runtime_action="deploy_new_binding",
        status=PlanStatus.APPROVED,
        created_at="2026-04-10T00:00:00Z",
        rollback=RollbackRef(
            target_artifact_id="reg-strat-001-1.1.0",
            target_version="1.1.0",
            action_type=RollbackActionType.PAUSE_THEN_REPLACE,
            reason="Previous approved baseline",
        ),
    )


def main() -> int:
    print("=== DeploymentSaga Smoke Test ===\n")
    results: list[tuple[str, bool]] = []
    plan = build_plan()
    results.append(check("canonical rollback action stays replace/pause/liquidate vocabulary", plan.rollback.action_type == RollbackActionType.PAUSE_THEN_REPLACE.value))

    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as tmp:
        store = DeploymentSagaStore(tmp.name)
        bootstrap = store.bootstrap_for_plan(plan, trace_id="trace-001")
        print(json.dumps(bootstrap.to_dict(), indent=2))
        results.append(check("bootstrap emits sequence 1 outbox event", bootstrap.outbox_event.event.sequence_no == 1))
        results.append(check("bootstrap status is awaiting_binding", bootstrap.saga.status == SagaStatus.AWAITING_BINDING))

        seq2 = store.record_binding_created(bootstrap.saga.saga_id, binding_id="binding-001", runtime_id="runtime-001")
        results.append(check("binding create emits sequence 2", seq2.event.sequence_no == 2))
        results.append(check("causal parent links sequence 2 to sequence 1", seq2.event.causal_parent_id == bootstrap.outbox_event.event.event_id))

        side_effects: list[str] = []
        consume = lambda event: side_effects.append(event.event_id)
        first = store.consume_event("runtime-projector", bootstrap.outbox_event.event, apply_fn=consume)
        duplicate = store.consume_event("runtime-projector", bootstrap.outbox_event.event, apply_fn=consume)
        results.append(check("first delivery applies", first.status == ReceiptStatus.APPLIED))
        results.append(check("duplicate delivery is skipped", duplicate.status == ReceiptStatus.DUPLICATE and len(side_effects) == 1))

        seq3 = store.record_runtime_active(bootstrap.saga.saga_id, binding_id="binding-001", runtime_id="runtime-001")
        out_of_order = store.consume_event("runtime-projector", seq3.event, apply_fn=consume)
        second = store.consume_event("runtime-projector", seq2.event, apply_fn=consume)
        third = store.consume_event("runtime-projector", seq3.event, apply_fn=consume)
        results.append(check("out-of-order delivery is rejected", out_of_order.status == ReceiptStatus.OUT_OF_ORDER))
        results.append(check("sequence 2 applies after sequence 1", second.status == ReceiptStatus.APPLIED))
        results.append(check("sequence 3 applies after the gap closes", third.status == ReceiptStatus.APPLIED and len(side_effects) == 3))
        results.append(check("runtime active completes the saga", store.get(bootstrap.saga.saga_id).status == SagaStatus.COMPLETED))

    rollback_store = DeploymentSagaStore()
    rollback_bootstrap = rollback_store.bootstrap_for_plan(build_plan(), trace_id="trace-rollback-001")
    rollback_store.record_binding_created(rollback_bootstrap.saga.saga_id, binding_id="binding-rollback-001")
    decision = rollback_store.record_failure(
        rollback_bootstrap.saga.saga_id,
        reason="runtime load failed repeatedly",
    )
    results.append(check("runtime load failure chooses inactive-binding compensation", decision.command_type == CompensationCommand.MARK_BINDING_FAILED_INACTIVE))

    active_store = DeploymentSagaStore()
    active_bootstrap = active_store.bootstrap_for_plan(build_plan(), trace_id="trace-active-001")
    active_store.record_binding_created(active_bootstrap.saga.saga_id, binding_id="binding-active-001")
    active_store.record_runtime_active(active_bootstrap.saga.saga_id, binding_id="binding-active-001")
    active_decision = active_store.record_failure(
        active_bootstrap.saga.saga_id,
        reason="post-activation mismatch",
        failed_step="runtime_active",
    )
    results.append(check("post-activation failure uses rollback compensation", active_decision.runtime_action == RollbackActionType.PAUSE_THEN_REPLACE.value))

    print("\nSUMMARY")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
