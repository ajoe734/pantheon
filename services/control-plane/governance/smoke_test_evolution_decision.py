#!/usr/bin/env python3
"""
Smoke test for EvolutionDecision governance contract.

Run:
    python3 services/control-plane/governance/smoke_test_evolution_decision.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.incident.incident import IncidentCase, IncidentStore, Postmortem

from evolution_decision import (
    ComparisonOperator,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionError,
    EvolutionDecisionState,
    EvolutionDecisionStore,
    EvolutionTargetType,
    RiskLevel,
    ThresholdSignalType,
    ThresholdSnapshot,
    infer_risk_level,
    to_audit_event,
    validate_evolution_decision,
    validate_evolution_decision_json,
)
from approval_decision import EvidenceRef, EvidenceRefType

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


def threshold() -> ThresholdSnapshot:
    return ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.5",
        signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
        metric_name="severity1_incident_count",
        comparator=ComparisonOperator.GTE,
        observed_value=1,
        threshold_value=1,
        window="30d",
        note="Severity-1 incident triggered immediate governance review.",
    )


def evidence() -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.DRIFT_REPORT,
        ref_id="drift-001",
        storage_ref={"backend": "object_store", "path": "/drift/001"},
    )


def make_decision(decision_id: str = "evo-smoke-001") -> EvolutionDecision:
    return EvolutionDecision.create_proposed(
        decision_id=decision_id,
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-001",
        target_version="1.2.3",
        action_type=EvolutionActionType.FREEZE,
        rationale="Freeze live artifact after severity-1 incident.",
        created_by_id="evolution-controller-01",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[evidence()],
        threshold_snapshots=[threshold()],
        linked_postmortem_id="pm-001",
        linked_incident_id="inc-001",
        capital_pool_id="pool-001",
        persona_id="persona-ops",
        target_stage="live",
    )


def make_incident_store() -> IncidentStore:
    store = IncidentStore()
    store.create_incident(
        IncidentCase(
            incident_id="inc-001",
            title="Live strategy incident",
            status="open",
            severity="critical",
            created_at="2026-04-10T01:00:00Z",
            binding_id="binding-001",
            deployment_stage="live",
            deployment_plan_id="plan-001",
            capital_pool_id="pool-001",
            persona_capital_binding_id="pcb-001",
            artifact_id="artifact-001",
            artifact_version="1.2.3",
            runtime_id="runtime-001",
            trace_id="trace-001",
        )
    )
    store.create_postmortem(
        Postmortem(
            postmortem_id="pm-001",
            title="Postmortem",
            status="draft",
            created_at="2026-04-10T03:00:00Z",
            incident_id="inc-001",
            binding_id="binding-001",
            deployment_stage="live",
            deployment_plan_id="plan-001",
            capital_pool_id="pool-001",
            persona_capital_binding_id="pcb-001",
            artifact_id="artifact-001",
            artifact_version="1.2.3",
            runtime_id="runtime-001",
            trace_id="trace-001",
            root_cause="Mismatch between approval and loader binding.",
        )
    )
    return store


def main() -> int:
    global PASS, FAIL
    print("=== EvolutionDecision Smoke Test ===\n")

    print("[1] Risk Normalization")
    check(
        "freeze live -> high risk",
        infer_risk_level(EvolutionActionType.FREEZE, target_stage="live") == RiskLevel.HIGH,
    )
    check(
        "freeze canary -> medium risk",
        infer_risk_level(EvolutionActionType.FREEZE, target_stage="canary") == RiskLevel.MEDIUM,
    )
    check(
        "retrain -> low risk",
        infer_risk_level(EvolutionActionType.RETRAIN) == RiskLevel.LOW,
    )

    print("\n[2] Lifecycle")
    decision = make_decision()
    check("starts proposed", decision.decision_state == EvolutionDecisionState.PROPOSED)
    decision.mark_reviewed(
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
        "committee-01",
        "approval-001",
        note="Committee accepted the freeze case.",
    )
    check("reviewed transition", decision.decision_state == EvolutionDecisionState.REVIEWED)
    decision.approve(
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
        "committee-01",
        note="Freeze approved.",
    )
    check("approved transition", decision.decision_state == EvolutionDecisionState.APPROVED)
    decision.execute(
        EvolutionActorRole.EVOLUTION_CONTROLLER,
        "evolution-controller-01",
        ExecutionResult(
            status=ExecutionStatus.SUBMITTED,
            plane=ExecutionPlane.RUNTIME,
            executed_at="2026-04-10T05:00:00Z",
            execution_ref_id="freeze-order-001",
            outcome_summary="Freeze submitted to runtime manager.",
        ),
        cooldown_ends_at="2026-04-17T05:00:00Z",
        observation_window_ends_at="2026-04-24T05:00:00Z",
    )
    check("executed transition", decision.decision_state == EvolutionDecisionState.EXECUTED)
    check("active during observation", decision.is_active(as_of="2026-04-18T00:00:00Z"))
    check("inactive after observation", not decision.is_active(as_of="2026-04-25T00:00:00Z"))

    print("\n[3] Validation")
    errors = validate_evolution_decision(decision)
    check("executed decision validates", len(errors) == 0, str(errors))
    json_errors = validate_evolution_decision_json(decision.to_dict())
    check("json validation passes", len(json_errors) == 0, str(json_errors))

    print("\n[4] Incident Reverse Link")
    incident_store = make_incident_store()
    store = EvolutionDecisionStore(incident_store=incident_store)
    store.put(make_decision())
    postmortem = incident_store.require_postmortem("pm-001")
    check(
        "reverse link synced",
        postmortem.linked_evolution_decision_id == "evo-smoke-001",
    )

    print("\n[5] Single Active Rule")
    try:
        store.put(make_decision("evo-smoke-002"))
    except EvolutionDecisionError:
        blocked = True
    else:
        blocked = False
    check("parallel active decision blocked", blocked)

    print("\n[6] Persistence")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        tmp_path = handle.name
    try:
        persisted = EvolutionDecisionStore(tmp_path)
        persisted.put(make_decision("evo-smoke-003"))
        reloaded = EvolutionDecisionStore(tmp_path)
        check("persisted decision reloads", reloaded.get("evo-smoke-003") is not None)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    print("\n[7] Audit Event")
    event = to_audit_event(decision, "evolution_decision_executed")
    check("audit event carries decision id", event["decision_id"] == "evo-smoke-001")
    check("audit event carries latest actor", event["actor_id"] == "evolution-controller-01")

    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
