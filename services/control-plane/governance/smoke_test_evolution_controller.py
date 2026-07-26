#!/usr/bin/env python3
"""
Smoke test for EvolutionController operational routing.

Run:
    python3 services/control-plane/governance/smoke_test_evolution_controller.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from approval_decision import EvidenceRef, EvidenceRefType
from deployment_plan import DeploymentStage
from evolution_controller import EvolutionController, FreezeFollowthroughMode, ThresholdEvaluator
from evolution_decision import (
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    ExecutionResult,
    ExecutionStatus,
    ThresholdSignalType,
    ThresholdSnapshot,
)


def route_and_execute(controller, decision, *, actor_id, executed_at, **dispatch_kwargs):
    """Route an approved decision, then execute it on a terminal receipt.

    ``EvolutionDecision.execute`` refuses the ``submitted`` dispatch intent
    that ``EvolutionController.execute_approved`` composes: only a real
    downstream terminal readback may move a decision to ``executed``
    (L12-EVO-001).  This smoke test checks routing, so it dispatches and then
    supplies the terminal receipt the downstream plane would return.
    """
    outcome = controller.dispatch_approved(decision, executed_at=executed_at, **dispatch_kwargs)
    decision.execute(
        EvolutionActorRole.EVOLUTION_CONTROLLER,
        actor_id,
        ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plane=outcome.execution_result.plane,
            executed_at=executed_at,
            execution_ref_id=outcome.primary_command.command_id,
            outcome_summary=outcome.execution_result.outcome_summary,
        ),
        cooldown_ends_at=outcome.primary_command.cooldown_ends_at,
        observation_window_ends_at=outcome.primary_command.observation_window_ends_at,
    )
    return outcome

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} — {detail}")


def evidence() -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
        ref_id="telemetry-sum-001",
        storage_ref={"backend": "object_store", "path": "/telemetry/summary/001"},
    )


def threshold(
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
    decision_id: str,
    action_type: EvolutionActionType,
    target_stage: str | None,
) -> EvolutionDecision:
    decision = EvolutionDecision.create_proposed(
        decision_id=decision_id,
        target_type="candidate_artifact",
        target_id="artifact-001",
        target_version="1.2.3",
        action_type=action_type,
        rationale="Smoke routing validation.",
        created_by_id="evolution-controller-01",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[evidence()],
        threshold_snapshots=[threshold()],
        linked_incident_id="inc-001",
        linked_postmortem_id="pm-001",
        capital_pool_id="pool-001",
        persona_id="persona-001",
        target_stage=target_stage,
    )
    reviewer = (
        EvolutionActorRole.GOVERNANCE_COMMITTEE
        if decision.risk_level == "high"
        else EvolutionActorRole.REVIEWER_ON_DUTY
    )
    decision.mark_reviewed(reviewer, "reviewer-01", "approval-001")
    decision.approve(reviewer, "approver-01")
    return decision


def main() -> int:
    global PASS, FAIL
    controller = EvolutionController()
    evaluator = ThresholdEvaluator()

    print("=== EvolutionController Smoke Test ===\n")

    print("[1] Freeze live without active runtime")
    freeze_live = make_approved_decision(
        decision_id="evo-smoke-001",
        action_type=EvolutionActionType.FREEZE,
        target_stage="live",
    )
    outcome = route_and_execute(
        controller,
        freeze_live,
        actor_id="controller-01",
        executed_at="2026-04-11T10:00:00Z",
        has_active_runtime=False,
    )
    check("governance primary plane", outcome.primary_command.execution_plane == "governance")
    check("no rollback companion", outcome.rollback_command is None)
    check("high-risk window inherited", freeze_live.cooldown_ends_at == "2026-04-25T10:00:00Z")

    print("\n[2] Freeze live with stage-freeze follow-through")
    freeze_stage = make_approved_decision(
        decision_id="evo-smoke-002",
        action_type=EvolutionActionType.FREEZE,
        target_stage="live",
    )
    outcome = route_and_execute(
        controller,
        freeze_stage,
        actor_id="controller-01",
        executed_at="2026-04-11T10:00:00Z",
        has_active_runtime=True,
        freeze_mode=FreezeFollowthroughMode.FREEZE_STAGE,
    )
    check("deployment follow-through emitted", len(outcome.followthrough_commands) == 1)
    check("freeze_stage action preserved", outcome.followthrough_commands[0].action_type == "freeze_stage")

    print("\n[3] Freeze live with rollback follow-through")
    freeze_rollback = make_approved_decision(
        decision_id="evo-smoke-003",
        action_type=EvolutionActionType.FREEZE,
        target_stage="live",
    )
    outcome = route_and_execute(
        controller,
        freeze_rollback,
        actor_id="controller-01",
        executed_at="2026-04-11T10:00:00Z",
        has_active_runtime=True,
        active_binding_id="rb-live-001",
        freeze_mode=FreezeFollowthroughMode.ROLLBACK,
    )
    check("rollback companion emitted", outcome.rollback_command is not None)
    check(
        "default rollback action is pause_then_replace",
        outcome.rollback_command.rollback_action_type == "pause_then_replace",
    )

    print("\n[4] Retrain routes to research")
    retrain = make_approved_decision(
        decision_id="evo-smoke-004",
        action_type=EvolutionActionType.RETRAIN,
        target_stage=None,
    )
    outcome = route_and_execute(
        controller,
        retrain,
        actor_id="controller-01",
        executed_at="2026-04-11T10:00:00Z",
    )
    check("research plane selected", outcome.primary_command.execution_plane == "research")
    check("low-risk cooldown", retrain.cooldown_ends_at == "2026-04-14T10:00:00Z")

    print("\n[5] Redeploy follow-through stays in deployment plane")
    redeploy = controller.create_redeploy_followthrough(
        retrain,
        artifact_id="artifact-002",
        artifact_version="2.0.0",
        approval_decision_id="approval-002",
        target_stage=DeploymentStage.CANARY,
        requested_at="2026-04-12T10:00:00Z",
    )
    check("redeploy command uses deployment plane", redeploy.execution_plane == "deployment")
    check("redeploy does not invent a new action family", redeploy.action_type == "redeploy_followthrough")

    print("\n[6] Threshold evaluator")
    execution_drift = evaluator.classify(
        threshold(
            signal_type=ThresholdSignalType.EXECUTION_DRIFT,
            metric_name="slippage_drift_pct",
            observed_value=30.0,
            threshold_value=25.0,
        )
    )
    check("slippage drift maps to revalidate", execution_drift.proposed_action == "revalidate")
    incident = evaluator.classify(
        threshold(),
        context={"incident_severity": "critical", "has_active_runtime": True},
    )
    check("critical incident maps to freeze", incident.proposed_action == "freeze")
    check("critical incident requests runtime follow-through", incident.requires_runtime_followthrough)

    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
