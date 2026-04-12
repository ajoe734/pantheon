#!/usr/bin/env python3
"""
Smoke test for ApprovalDecision governance contract.

Verifies end-to-end lifecycle:
  proposed → under_review → decided (approved) → superseded

Run:
    python3 services/control-plane/governance/smoke_test_approval_decision.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from approval_decision import (
    ActorRole,
    ApprovalDecision,
    ApprovalDecisionStore,
    DecisionOutcome,
    DecisionState,
    EvidenceRef,
    EvidenceRefType,
    OwnerMatrix,
    RiskLevel,
    TargetType,
    to_audit_event,
    validate_decision,
    validate_decision_json,
)

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


def main() -> int:
    global PASS, FAIL
    print("=== ApprovalDecision Smoke Test ===\n")

    # 1. Owner matrix
    print("[1] Owner Matrix")
    check(
        "low risk → governance_reviewer authorized",
        OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_REVIEWER, RiskLevel.LOW),
    )
    check(
        "high risk → governance_reviewer NOT authorized",
        not OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_REVIEWER, RiskLevel.HIGH),
    )
    check(
        "critical → only committee",
        OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_COMMITTEE, RiskLevel.CRITICAL)
        and not OwnerMatrix.is_authorized(ActorRole.RISK_OWNER, RiskLevel.CRITICAL),
    )

    # 2. Lifecycle: proposed → under_review → decided
    print("\n[2] Lifecycle: proposed → under_review → decided")
    d = ApprovalDecision.create_proposed(
        decision_id="smoke-001",
        target_type=TargetType.REGISTRY_ENTRY,
        target_id="reg-001",
        target_version="1.0.0",
        risk_level=RiskLevel.MEDIUM,
    )
    check("starts as proposed", d.decision_state == DecisionState.PROPOSED)

    d.accept_review(ActorRole.RISK_OWNER, "risk-owner-01")
    check("accept_review → under_review", d.decision_state == DecisionState.UNDER_REVIEW)
    check("actor_role set", d.actor_role == ActorRole.RISK_OWNER)

    refs = [
        EvidenceRef(
            ref_type=EvidenceRefType.EVALUATOR_RESULT,
            ref_id="eval-001",
            storage_ref={"backend": "object_store", "path": "/eval/001"},
        )
    ]
    d.decide(
        DecisionOutcome.APPROVED,
        "Evaluator shows 95% accuracy, risk within bounds",
        evidence_refs=refs,
    )
    check("decide → decided", d.decision_state == DecisionState.DECIDED)
    check("decision == approved", d.decision == DecisionOutcome.APPROVED)
    check("evidence_refs preserved", len(d.evidence_refs) == 1)
    check("rationale set", len(d.rationale) > 0)

    # 3. Validation
    print("\n[3] Validation")
    errors = validate_decision(d)
    check("valid decided decision has no errors", len(errors) == 0, str(errors))

    # Test invalid: automated gate for high risk
    bad = ApprovalDecision(
        decision_id="bad-001",
        target_type=TargetType.REGISTRY_ENTRY,
        target_id="reg-001",
        target_version="1.0.0",
        decision=DecisionOutcome.APPROVED,
        decision_state=DecisionState.DECIDED,
        actor_role=ActorRole.AUTOMATED_GATE,
        actor_id="auto-01",
        rationale="Auto",
        created_at="2026-04-10T00:00:00Z",
        decided_at="2026-04-10T00:01:00Z",
        risk_level=RiskLevel.HIGH,
    )
    errors = validate_decision(bad)
    check("automated gate not authorized for high risk", len(errors) > 0)

    # 4. JSON validation
    print("\n[4] JSON Schema Validation")
    valid_data = {
        "decision_id": "smoke-002",
        "target_type": "registry_entry",
        "target_id": "reg-002",
        "target_version": "1.0.0",
        "decision": "approved",
        "decision_state": "decided",
        "actor_role": "governance_reviewer",
        "actor_id": "reviewer-01",
        "rationale": "Good",
        "created_at": "2026-04-10T00:00:00Z",
        "decided_at": "2026-04-10T00:01:00Z",
    }
    errors = validate_decision_json(valid_data)
    check("valid JSON decision has no errors", len(errors) == 0, str(errors))

    invalid_data = {"decision_id": "x"}
    errors = validate_decision_json(invalid_data)
    check("incomplete JSON has errors", len(errors) > 0)

    # 5. Serialization round-trip
    print("\n[5] Serialization")
    json_str = d.to_json()
    restored = ApprovalDecision.from_json(json_str)
    check("to_json/from_json round-trip", restored.decision_id == d.decision_id)
    check("rationale preserved", restored.rationale == d.rationale)
    check("evidence_refs preserved", len(restored.evidence_refs) == 1)

    # 6. Store persistence
    print("\n[6] Store Persistence")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    store = ApprovalDecisionStore(tmp_path)
    store.put(d)
    retrieved = store.get("smoke-001")
    check("store put/get", retrieved is not None and retrieved.decision_id == "smoke-001")

    store2 = ApprovalDecisionStore(tmp_path)
    reloaded = store2.get("smoke-001")
    check("store persistence across instances", reloaded is not None and reloaded.decision_id == "smoke-001")

    latest = store.find_latest_approved("registry_entry", "reg-001")
    check("find_latest_approved returns correct decision", latest is not None and latest.decision_id == "smoke-001")

    import os
    os.unlink(tmp_path)

    # 7. Audit event
    print("\n[7] Audit Event")
    event = to_audit_event(d, "approval_decision_state_changed")
    check("event has event_type", event["event_type"] == "approval_decision_state_changed")
    check("event has decision_id", event["decision_id"] == "smoke-001")
    check("event has target_type", event["target_type"] == "registry_entry")
    check("event has decision", event["decision"] == "approved")
    check("event has timestamp", "timestamp" in event)

    # 8. Supersede
    print("\n[8] Supersede")
    d.supersede("smoke-003")
    check("supersede changes state", d.decision_state == DecisionState.SUPERSEDED)
    check("supersede sets reference", d.superseded_by == "smoke-003")

    # 9. approved_with_conditions
    print("\n[9] Approved with Conditions")
    cond_d = ApprovalDecision.create_proposed(
        decision_id="smoke-cond",
        target_type=TargetType.REGISTRY_ENTRY,
        target_id="reg-003",
        target_version="1.0.0",
    )
    cond_d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
    cond_d.decide(
        DecisionOutcome.APPROVED_WITH_CONDITIONS,
        "Approved with conditions",
        conditions=["Reduce canary exposure to 5%", "Add monitoring alert"],
    )
    check("conditions stored", len(cond_d.conditions) == 2)
    errors = validate_decision(cond_d)
    check("conditions decision is valid", len(errors) == 0, str(errors))

    # Summary
    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
