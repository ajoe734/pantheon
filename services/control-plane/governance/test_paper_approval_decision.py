"""
Unit tests for paper_approval_decision (MGMT-PAPER-002).

Verifies:
- Paper-mode ApprovalDecision factory produces valid decided/approved decision
- Full lifecycle: proposed → under_review → decided
- Paper-specific invariants: no live capital side effects, paper risk level
- Evidence packet structure is complete and OODA-compatible
- Write-to-disk produces parseable JSON with required fields
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from approval_decision import (
    ActorRole,
    DecisionOutcome,
    DecisionState,
    EvidenceRef,
    EvidenceRefType,
    RiskLevel,
    TargetType,
    validate_decision,
)
from paper_approval_decision import (
    PAPER_RISK_LEVEL,
    PaperApprovalContext,
    build_paper_approval_decision,
    write_evidence_packet,
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
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def _default_ctx(**overrides) -> PaperApprovalContext:
    base = dict(
        decision_id="test-paper-approval-001",
        strategy_spec_id="spec-test-001",
        strategy_version="1.0.0",
    )
    base.update(overrides)
    return PaperApprovalContext(**base)


# ---------------------------------------------------------------------------
# 1. Factory lifecycle
# ---------------------------------------------------------------------------

def test_factory_lifecycle() -> None:
    print("[1] Factory lifecycle")
    ctx = _default_ctx()
    d = build_paper_approval_decision(ctx)

    check("decision_state is decided", d.decision_state == DecisionState.DECIDED)
    check("decision is approved", d.decision == DecisionOutcome.APPROVED)
    check("actor_role is risk_owner", d.actor_role == ActorRole.RISK_OWNER)
    check("target_type is strategy_spec", d.target_type == TargetType.STRATEGY_SPEC)
    check("target_id matches spec id", d.target_id == ctx.strategy_spec_id)
    check("target_version matches", d.target_version == ctx.strategy_version)
    check("decided_at is set", d.decided_at is not None)
    check("rationale is non-empty", bool(d.rationale))
    check("evidence_refs populated", len(d.evidence_refs) >= 1)


# ---------------------------------------------------------------------------
# 2. Paper invariants
# ---------------------------------------------------------------------------

def test_paper_invariants() -> None:
    print("\n[2] Paper invariants")
    ctx = _default_ctx(
        capital_pool_id="pool-paper-001",
        persona_id="persona-paper-01",
    )
    d = build_paper_approval_decision(ctx)

    check("risk_level is medium (paper default)", d.risk_level == PAPER_RISK_LEVEL)
    check("capital_pool_id preserved", d.capital_pool_id == "pool-paper-001")
    check("persona_id preserved", d.persona_id == "persona-paper-01")
    check("no conditions on plain approval", len(d.conditions) == 0)


# ---------------------------------------------------------------------------
# 3. Validation
# ---------------------------------------------------------------------------

def test_validation() -> None:
    print("\n[3] Validation")
    ctx = _default_ctx()
    d = build_paper_approval_decision(ctx)
    errors = validate_decision(d)
    check("no validation errors", len(errors) == 0, str(errors))


# ---------------------------------------------------------------------------
# 4. Custom evidence refs
# ---------------------------------------------------------------------------

def test_custom_evidence_refs() -> None:
    print("\n[4] Custom evidence refs")
    custom_refs = [
        EvidenceRef(
            ref_type=EvidenceRefType.EVALUATOR_RESULT,
            ref_id="eval-custom-001",
            note="Custom paper evaluator",
        ),
        EvidenceRef(
            ref_type=EvidenceRefType.DRIFT_REPORT,
            ref_id="drift-custom-001",
            note="No live drift in paper mode",
        ),
    ]
    ctx = _default_ctx(evidence_refs=custom_refs)
    d = build_paper_approval_decision(ctx)
    check("custom evidence_refs count", len(d.evidence_refs) == 2)
    check("first ref type matches", d.evidence_refs[0].ref_type == EvidenceRefType.EVALUATOR_RESULT)


# ---------------------------------------------------------------------------
# 5. Round-trip serialization
# ---------------------------------------------------------------------------

def test_serialization() -> None:
    print("\n[5] Serialization round-trip")
    from approval_decision import ApprovalDecision

    ctx = _default_ctx()
    d = build_paper_approval_decision(ctx)
    restored = ApprovalDecision.from_json(d.to_json())

    check("decision_id round-trips", restored.decision_id == d.decision_id)
    check("decision round-trips", restored.decision == d.decision)
    check("decided_at round-trips", restored.decided_at == d.decided_at)
    check("evidence_refs count preserved", len(restored.evidence_refs) == len(d.evidence_refs))


# ---------------------------------------------------------------------------
# 6. Evidence packet write
# ---------------------------------------------------------------------------

def test_evidence_packet_write() -> None:
    print("\n[6] Evidence packet write")
    ctx = _default_ctx(
        decision_id="test-paper-write-001",
        capital_pool_id="pool-001",
        persona_id="persona-001",
    )
    d = build_paper_approval_decision(ctx)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = Path(f.name)

    try:
        packet = write_evidence_packet(d, "MGMT-PAPER-002", tmp)
        raw = json.loads(tmp.read_text())

        check("task_id field present", raw.get("task_id") == "MGMT-PAPER-002")
        check("environment is paper", raw.get("environment") == "paper")
        check("live_capital_side_effects is false", raw.get("live_capital_side_effects") is False)
        check("approval_decision key present", "approval_decision" in raw)
        check("audit_event key present", "audit_event" in raw)
        check("ooda_decide_ref has approval_decision_id",
              raw.get("ooda_decide_ref", {}).get("approval_decision_id") == d.decision_id)
        check("validation_errors is empty list", raw.get("validation_errors") == [])
        check("paper_loop_chain present", "paper_loop_chain" in raw)
        check("generated_at present", "generated_at" in raw)
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# 7. Wrong state transition guard
# ---------------------------------------------------------------------------

def test_wrong_transition_guard() -> None:
    print("\n[7] Wrong state transition guard")
    from approval_decision import ApprovalDecision

    d = ApprovalDecision.create_proposed(
        decision_id="guard-test",
        target_type=TargetType.STRATEGY_SPEC,
        target_id="spec-guard",
        target_version="1.0.0",
        risk_level=RiskLevel.MEDIUM,
    )
    try:
        d.decide(DecisionOutcome.APPROVED, "skip review")
        check("decide without review should raise", False, "no exception raised")
    except ValueError:
        check("decide without review raises ValueError", True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    global PASS, FAIL
    print("=== MGMT-PAPER-002: paper ApprovalDecision unit tests ===\n")

    test_factory_lifecycle()
    test_paper_invariants()
    test_validation()
    test_custom_evidence_refs()
    test_serialization()
    test_evidence_packet_write()
    test_wrong_transition_guard()

    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
