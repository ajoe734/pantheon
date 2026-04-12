#!/usr/bin/env python3
"""
Smoke test for DeploymentPlan governance contract.

Run:
    python3 services/control-plane/governance/smoke_test_deployment_plan.py
"""
from __future__ import annotations

import json
import sys
import tempfile

from deployment_plan import (
    DeploymentPlanStore,
    DeploymentStage,
    RollbackActionType,
    RollbackRef,
    RuntimeAction,
    ScheduleWindow,
    StagePlanner,
    validate_plan,
    validate_plan_json,
)


def check(label: str, passed: bool) -> tuple[str, bool]:
    print(f"[{'PASS' if passed else 'FAIL'}] {label}")
    return label, passed


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


def main() -> int:
    print("=== DeploymentPlan Smoke Test ===\n")
    planner = StagePlanner()
    results: list[tuple[str, bool]] = []

    paper_plan = planner.create_plan(
        plan_id="plan-paper-001",
        approval_decision_id="approval-001",
        approval_decision=approved_decision(),
        registry_entry=approved_registry_entry(),
        capital_pool_id="pool-001",
        sponsor_persona_id="persona-ops",
        target_stage=DeploymentStage.PAPER,
        rollback=rollback_ref(),
    )
    print(json.dumps(paper_plan.to_dict(), indent=2))
    results.append(check("none -> paper create_plan", paper_plan.transition_type == "activate"))
    results.append(check("paper default scale uses zero capital", paper_plan.scale.capital_scale_pct == 0.0))

    canary_plan = planner.create_plan(
        plan_id="plan-canary-001",
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
    results.append(check("paper -> canary create_plan", canary_plan.transition_type == "promote"))
    results.append(check("canary scale <= 5/25", canary_plan.scale.capital_scale_pct == 5.0 and canary_plan.scale.gross_scale_pct == 25.0))
    results.append(check("schedule window validates", validate_plan(canary_plan) == []))

    live_plan = planner.create_plan(
        plan_id="plan-live-001",
        approval_decision_id="approval-001",
        approval_decision=approved_decision(),
        registry_entry=approved_registry_entry(stage="canary"),
        capital_pool_id="pool-001",
        sponsor_persona_id="persona-ops",
        target_stage=DeploymentStage.LIVE,
        rollback=rollback_ref(),
    )
    results.append(check("canary -> live create_plan", live_plan.transition_type == "promote"))

    freeze_plan = planner.create_plan(
        plan_id="plan-freeze-001",
        approval_decision_id="approval-001",
        approval_decision=approved_decision(),
        registry_entry=approved_registry_entry(stage="live"),
        capital_pool_id="pool-001",
        sponsor_persona_id="persona-ops",
        target_stage=DeploymentStage.FROZEN,
    )
    results.append(check("live -> frozen create_plan", freeze_plan.transition_type == "freeze"))
    results.append(check("freeze runtime action is freeze_binding", freeze_plan.runtime_action == "freeze_binding"))

    rollback_plan = planner.create_plan(
        plan_id="plan-rollback-001",
        approval_decision_id="approval-001",
        approval_decision=approved_decision(),
        registry_entry=approved_registry_entry(stage="live"),
        capital_pool_id="pool-001",
        sponsor_persona_id="persona-ops",
        target_stage=DeploymentStage.PAPER,
        rollback=rollback_ref(RollbackActionType.PAUSE_THEN_REPLACE),
    )
    results.append(check("live -> paper rollback transition", rollback_plan.transition_type == "rollback"))
    results.append(check("rollback action is explicit", rollback_plan.runtime_action == "pause_then_replace"))
    results.append(check("rollback action_type stays canonical", rollback_plan.rollback.action_type == "pause_then_replace"))

    projection = planner.build_execution_projection(paper_plan, approved_registry_entry())
    results.append(check("projection carries deployment_stage", projection.metadata["deployment_stage"] == "paper"))
    results.append(check("projection carries plan id", projection.metadata["deployment_plan_id"] == "plan-paper-001"))
    results.append(check("projection keeps rollback linkage", projection.metadata["rollback"]["target_registry_id"] == "reg-strat-001-1.1.0"))

    canary_projection = planner.build_execution_projection(canary_plan, approved_registry_entry(stage="paper"))
    results.append(check("canary projection omits legacy promotion_state", "promotion_state" not in canary_projection.metadata))

    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as tmp:
        store = DeploymentPlanStore(tmp.name)
        store.put(live_plan)
        store2 = DeploymentPlanStore(tmp.name)
        results.append(check("store roundtrip", store2.get("plan-live-001") is not None))

    results.append(check("plan JSON validation passes", validate_plan_json(live_plan.to_dict()) == []))

    try:
        planner.create_plan(
            plan_id="plan-invalid-001",
            approval_decision_id="approval-001",
            approval_decision=approved_decision(),
            registry_entry=approved_registry_entry(),
            capital_pool_id="pool-001",
            sponsor_persona_id="persona-ops",
            target_stage=DeploymentStage.CANARY,
            rollback=rollback_ref(),
        )
    except Exception:
        results.append(check("none -> canary is rejected", True))
    else:
        results.append(check("none -> canary is rejected", False))

    print("\nSUMMARY")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
