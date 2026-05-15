"""
paper_approval_decision — MGMT-PAPER-002

Factory and evidence writer for a paper-mode ApprovalDecision packet used in
the Management Paper Loop Proof (Track E / EPIC-02).

Scope
-----
Creates a concrete ApprovalDecision for a paper StrategySpec candidate and
walks it through the full lifecycle:

    proposed  →  under_review  →  decided (approved)

The resulting object is intended to be referenced by an OodaLoopPacket
decide.approval_decision_id field (MGMT-PAPER-007).

Usage
-----
Run as a standalone script to generate the evidence artifact:

    python3 services/control-plane/governance/paper_approval_decision.py

Or import the factory from another module:

    from paper_approval_decision import build_paper_approval_decision, PaperApprovalContext
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from approval_decision import (
        ActorRole,
        ApprovalDecision,
        ApprovalDecisionStore,
        DecisionOutcome,
        EvidenceRef,
        EvidenceRefType,
        RiskLevel,
        TargetType,
        to_audit_event,
        validate_decision,
    )
except ImportError:
    from services.control_plane.governance.approval_decision import (  # type: ignore
        ActorRole,
        ApprovalDecision,
        ApprovalDecisionStore,
        DecisionOutcome,
        EvidenceRef,
        EvidenceRefType,
        RiskLevel,
        TargetType,
        to_audit_event,
        validate_decision,
    )

# ---------------------------------------------------------------------------
# Paper loop context
# ---------------------------------------------------------------------------

PAPER_ENVIRONMENT = "paper"
PAPER_RISK_LEVEL = RiskLevel.MEDIUM  # paper deployment; no live capital side effects


@dataclass
class PaperApprovalContext:
    """Minimal context for a paper-loop ApprovalDecision."""

    decision_id: str
    strategy_spec_id: str
    strategy_version: str
    reviewer_actor_id: str = "governance-reviewer-paper-01"
    capital_pool_id: Optional[str] = None
    persona_id: Optional[str] = None
    evidence_refs: Optional[List[EvidenceRef]] = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_paper_approval_decision(ctx: PaperApprovalContext) -> ApprovalDecision:
    """Create and progress a paper-mode ApprovalDecision through full lifecycle.

    Lifecycle: proposed → under_review → decided (approved)

    The decision approves a StrategySpec artifact for paper-stage deployment.
    No live capital side effects are implied.
    """
    decision = ApprovalDecision.create_proposed(
        decision_id=ctx.decision_id,
        target_type=TargetType.STRATEGY_SPEC,
        target_id=ctx.strategy_spec_id,
        target_version=ctx.strategy_version,
        risk_level=PAPER_RISK_LEVEL,
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.persona_id,
    )

    decision.accept_review(ActorRole.RISK_OWNER, ctx.reviewer_actor_id)

    evidence = ctx.evidence_refs or [
        EvidenceRef(
            ref_type=EvidenceRefType.EVALUATOR_RESULT,
            ref_id=f"eval-{ctx.strategy_spec_id}-paper",
            storage_ref={
                "backend": "object_store",
                "path": f"/paper/eval/{ctx.strategy_spec_id}",
            },
            note="Paper-mode evaluator result — no live capital exposure",
        )
    ]

    decision.decide(
        outcome=DecisionOutcome.APPROVED,
        rationale=(
            f"StrategySpec {ctx.strategy_spec_id}@{ctx.strategy_version} "
            "reviewed and approved for paper-stage deployment. "
            "Live broker and capital-binding side effects remain fail-closed. "
            "Evidence: paper evaluator result attached."
        ),
        evidence_refs=evidence,
    )

    return decision


# ---------------------------------------------------------------------------
# Evidence packet writer
# ---------------------------------------------------------------------------

def write_evidence_packet(
    decision: ApprovalDecision,
    task_id: str,
    out_path: Path,
) -> Dict[str, Any]:
    """Serialize the decision + audit event into an evidence packet JSON file."""
    audit_event = to_audit_event(decision, "approval_decision_state_changed")
    errors = validate_decision(decision)

    packet: Dict[str, Any] = {
        "task_id": task_id,
        "epic": "EPIC-02 Management Paper Loop Proof",
        "environment": PAPER_ENVIRONMENT,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_capital_side_effects": False,
        "approval_decision": decision.to_dict(),
        "audit_event": audit_event,
        "validation_errors": errors,
        "ooda_decide_ref": {
            "approval_decision_id": decision.decision_id,
        },
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec",
            "MGMT-PAPER-002: ApprovalDecision packet  ← this artifact",
            "MGMT-PAPER-003: DeploymentPlan packet",
            "MGMT-PAPER-004: paper RuntimeBinding packet",
            "MGMT-PAPER-005: telemetry packet",
            "MGMT-PAPER-006: EvolutionDecision review packet",
            "MGMT-PAPER-007: complete OODA packet",
        ],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, indent=2))
    return packet


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3]
    / "support"
    / "evidence"
    / "MGMT-PAPER-002-paper-approval-decision.json"
)

_PAPER_CTX = PaperApprovalContext(
    decision_id="approval-paper-strategy-001",
    strategy_spec_id="strategy-spec-paper-qlib-lgbm-001",
    strategy_version="1.0.0",
    reviewer_actor_id="governance-reviewer-paper-01",
    capital_pool_id="capital-pool-paper-001",
    persona_id="persona-quant-paper-01",
)


def main() -> int:
    print("=== MGMT-PAPER-002: paper ApprovalDecision packet ===\n")

    decision = build_paper_approval_decision(_PAPER_CTX)
    errors = validate_decision(decision)

    if errors:
        print(f"FAIL: validation errors: {errors}")
        return 1

    packet = write_evidence_packet(decision, "MGMT-PAPER-002", _EVIDENCE_PATH)

    print(f"  decision_id   : {decision.decision_id}")
    print(f"  target_id     : {decision.target_id}@{decision.target_version}")
    print(f"  decision_state: {decision.decision_state}")
    print(f"  decision      : {decision.decision}")
    print(f"  actor_role    : {decision.actor_role}")
    print(f"  evidence_refs : {len(decision.evidence_refs)}")
    print(f"  validation    : {'PASS (no errors)' if not errors else 'FAIL'}")
    print(f"\n  evidence packet written to: {_EVIDENCE_PATH}")
    print(f"\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
