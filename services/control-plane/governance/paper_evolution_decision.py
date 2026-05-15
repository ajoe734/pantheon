"""
paper_evolution_decision - MGMT-PAPER-006

Factory and evidence writer for the paper-mode EvolutionDecision review packet
used in the Management Paper Loop Proof (Track E / EPIC-02).

Scope
-----
Turns the MGMT-PAPER-005 paper telemetry packet into a governed low-risk
EvolutionDecision:

    proposed -> reviewed -> approved -> executed

The executed action is a research-plane revalidation work item. It records
cooldown / observation windows and explicitly forbids runtime, broker, and
live-capital mutation.

Usage
-----
Run as a standalone script to generate the evidence artifact:

    python3 services/control-plane/governance/paper_evolution_decision.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

try:
    from approval_decision import (
        ActorRole,
        ApprovalDecision,
        DecisionOutcome,
        DecisionState,
        EvidenceRef,
        EvidenceRefType,
        RiskLevel,
        TargetType,
        validate_decision,
    )
    from evolution_decision import (
        ComparisonOperator,
        ExecutionPlane,
        ExecutionResult,
        ExecutionStatus,
        EvolutionActionType,
        EvolutionActorRole,
        EvolutionDecision,
        EvolutionDecisionState,
        EvolutionTargetType,
        ReviewStepType,
        ThresholdSignalType,
        ThresholdSnapshot,
        to_audit_event,
        validate_evolution_decision,
    )
except ImportError:
    from services.control_plane.governance.approval_decision import (  # type: ignore
        ActorRole,
        ApprovalDecision,
        DecisionOutcome,
        DecisionState,
        EvidenceRef,
        EvidenceRefType,
        RiskLevel,
        TargetType,
        validate_decision,
    )
    from services.control_plane.governance.evolution_decision import (  # type: ignore
        ComparisonOperator,
        ExecutionPlane,
        ExecutionResult,
        ExecutionStatus,
        EvolutionActionType,
        EvolutionActorRole,
        EvolutionDecision,
        EvolutionDecisionState,
        EvolutionTargetType,
        ReviewStepType,
        ThresholdSignalType,
        ThresholdSnapshot,
        to_audit_event,
        validate_evolution_decision,
    )


REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_ID = "MGMT-PAPER-006"
EPIC = "EPIC-02 Management Paper Loop Proof"
PAPER_ENVIRONMENT = "paper"
LOW_RISK_COOLDOWN_DAYS = 3
LOW_RISK_OBSERVATION_DAYS = 7

TELEMETRY_EVIDENCE_PATH = (
    REPO_ROOT / "support" / "evidence" / "MGMT-PAPER-005-paper-telemetry-packet.json"
)
EVIDENCE_PATH = (
    REPO_ROOT / "support" / "evidence" / "MGMT-PAPER-006-paper-evolution-decision.json"
)


@dataclass(frozen=True)
class PaperEvolutionDecisionContext:
    """Stable identifiers for the paper-loop EvolutionDecision packet."""

    decision_id: str = "evolution-paper-revalidate-001"
    approval_decision_id: str = "approval-paper-evolution-revalidate-001"
    strategy_spec_id: str = "strategy-spec-paper-qlib-lgbm-001"
    strategy_version: str = "1.0.0"
    capital_pool_id: str = "capital-pool-paper-001"
    sponsor_persona_id: str = "persona-quant-paper-01"
    source_ooda_packet_id: str = "ooda-paper-mgmt-loop-001"
    created_by_id: str = "evolution-controller-paper-01"
    reviewer_actor_id: str = "evolution-reviewer-paper-01"
    execution_ref_id: str = "revalidate-job-paper-001"
    created_at: str = "2026-05-15T15:05:30Z"
    reviewed_at: str = "2026-05-15T15:06:00Z"
    approved_at: str = "2026-05-15T15:06:05Z"
    executed_at: str = "2026-05-15T15:06:10Z"
    telemetry_packet_path: str = "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def load_telemetry_evidence(path: Path = TELEMETRY_EVIDENCE_PATH) -> Mapping[str, Any]:
    """Load the MGMT-PAPER-005 evidence packet used as review input."""
    if not path.exists():
        raise FileNotFoundError(f"Paper telemetry evidence not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def normalize_telemetry_evidence(
    telemetry_evidence: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Return telemetry packet, ingest validation, runtime summary, and safety assertions."""
    telemetry_packet = telemetry_evidence.get("telemetry_packet")
    if isinstance(telemetry_packet, Mapping):
        packet = telemetry_packet
        ingest_validation = telemetry_evidence.get("ingest_validation")
        runtime_summary = telemetry_evidence.get("runtime_summary_projection")
        safety_assertions = telemetry_evidence.get("safety_assertions")
    else:
        packet = telemetry_evidence
        ingest_validation = telemetry_evidence.get("ingest_validation")
        runtime_summary = telemetry_evidence.get("runtime_summary_projection")
        safety_assertions = telemetry_evidence.get("safety_assertions")

    return (
        packet if isinstance(packet, Mapping) else {},
        ingest_validation if isinstance(ingest_validation, Mapping) else {},
        runtime_summary if isinstance(runtime_summary, Mapping) else {},
        safety_assertions if isinstance(safety_assertions, Mapping) else {},
    )


def telemetry_event_ids(telemetry_packet: Mapping[str, Any]) -> List[str]:
    event_ids = telemetry_packet.get("event_ids")
    if isinstance(event_ids, list):
        return [str(event_id) for event_id in event_ids if event_id]
    events = telemetry_packet.get("events")
    if isinstance(events, list):
        return [
            str(event.get("event_id"))
            for event in events
            if isinstance(event, Mapping) and event.get("event_id")
        ]
    return []


def build_review_evidence_refs(
    ctx: PaperEvolutionDecisionContext,
    telemetry_packet: Mapping[str, Any],
) -> List[EvidenceRef]:
    packet_id = str(telemetry_packet.get("packet_id") or "telemetry-packet-paper-mgmt-001")
    return [
        EvidenceRef(
            ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
            ref_id=packet_id,
            storage_ref={
                "backend": "support/evidence",
                "path": ctx.telemetry_packet_path,
                "packet_id": packet_id,
            },
            note="Paper telemetry summary accepted by ingest; no live capital exposure.",
        ),
        EvidenceRef(
            ref_type=EvidenceRefType.MANUAL_REVIEW_TICKET,
            ref_id="paper-evolution-review-ticket-001",
            storage_ref={
                "backend": "governance",
                "path": f"evolution-decision://{ctx.decision_id}/review",
            },
            note="Reviewer-on-duty approval for low-risk paper revalidation.",
        ),
    ]


def build_threshold_snapshot() -> ThresholdSnapshot:
    return ThresholdSnapshot(
        policy_source="EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md#5.2",
        signal_type=ThresholdSignalType.MANUAL_REVIEW,
        metric_name="paper_telemetry_ready_for_revalidation",
        comparator=ComparisonOperator.GTE,
        observed_value=1,
        threshold_value=1,
        window="paper-loop",
        breached=True,
        note="Paper telemetry completed and is ready for governed revalidation before any canary consideration.",
    )


def build_evolution_approval_decision(
    ctx: PaperEvolutionDecisionContext | None = None,
    *,
    evidence_refs: List[EvidenceRef] | None = None,
) -> ApprovalDecision:
    """Build the ApprovalDecision backing EvolutionDecision review authority."""
    ctx = ctx or PaperEvolutionDecisionContext()
    decision = ApprovalDecision.create_proposed(
        decision_id=ctx.approval_decision_id,
        target_type=TargetType.EVOLUTION_PROPOSAL,
        target_id=ctx.decision_id,
        target_version=ctx.strategy_version,
        risk_level=RiskLevel.LOW,
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.sponsor_persona_id,
    )
    decision.created_at = ctx.created_at
    decision.accept_review(ActorRole.GOVERNANCE_REVIEWER, ctx.reviewer_actor_id)
    decision.decide(
        outcome=DecisionOutcome.APPROVED,
        rationale=(
            "Low-risk paper revalidation approved from accepted paper telemetry. "
            "The approval authorizes a research work item only; runtime, broker, "
            "and live-capital mutation remain disabled."
        ),
        evidence_refs=list(evidence_refs or []),
    )
    decision.decided_at = ctx.approved_at
    return decision


def build_paper_evolution_decision(
    ctx: PaperEvolutionDecisionContext | None = None,
    *,
    telemetry_evidence: Mapping[str, Any] | None = None,
) -> EvolutionDecision:
    """Create, review, approve, and execute the paper revalidation decision."""
    ctx = ctx or PaperEvolutionDecisionContext()
    telemetry_evidence = telemetry_evidence or load_telemetry_evidence()
    telemetry_packet, _, runtime_summary, _ = normalize_telemetry_evidence(telemetry_evidence)
    evidence_refs = build_review_evidence_refs(ctx, telemetry_packet)
    event_ids = telemetry_event_ids(telemetry_packet)
    execution_start = _parse_utc(ctx.executed_at)

    decision = EvolutionDecision.create_proposed(
        decision_id=ctx.decision_id,
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id=ctx.strategy_spec_id,
        target_version=ctx.strategy_version,
        action_type=EvolutionActionType.REVALIDATE,
        rationale=(
            "Revalidate the paper StrategySpec after paper telemetry confirms "
            "accepted ingest, paper-stage runtime context, and logged-only broker evidence."
        ),
        created_by_id=ctx.created_by_id,
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=evidence_refs,
        threshold_snapshots=[build_threshold_snapshot()],
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.sponsor_persona_id,
        metadata={
            "task_id": TASK_ID,
            "source_telemetry_packet_id": telemetry_packet.get("packet_id"),
            "source_event_ids": event_ids,
            "source_runtime_binding_id": runtime_summary.get("runtime_binding_id")
            or runtime_summary.get("binding_id"),
            "source_ooda_packet_id": ctx.source_ooda_packet_id,
            "review_packet": True,
            "live_mutation_allowed": False,
            "runtime_binding_mutation_allowed": False,
            "broker_order_allowed": False,
            "execution_plane_authority": ExecutionPlane.RESEARCH.value,
            "cooldown_days": LOW_RISK_COOLDOWN_DAYS,
            "observation_window_days": LOW_RISK_OBSERVATION_DAYS,
        },
    )
    decision.created_at = ctx.created_at
    decision.mark_reviewed(
        EvolutionActorRole.REVIEWER_ON_DUTY,
        ctx.reviewer_actor_id,
        ctx.approval_decision_id,
        note="Reviewer-on-duty accepted low-risk paper revalidation review.",
        reviewed_at=ctx.reviewed_at,
    )
    decision.approve(
        EvolutionActorRole.REVIEWER_ON_DUTY,
        ctx.reviewer_actor_id,
        note="Approved for research-plane revalidation only; no live mutation.",
        approved_at=ctx.approved_at,
    )
    decision.execute(
        EvolutionActorRole.EVOLUTION_CONTROLLER,
        ctx.created_by_id,
        ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plane=ExecutionPlane.RESEARCH,
            executed_at=ctx.executed_at,
            execution_ref_id=ctx.execution_ref_id,
            outcome_summary="Paper revalidation work item recorded without runtime, broker, or capital mutation.",
        ),
        cooldown_started_at=ctx.executed_at,
        cooldown_ends_at=_format_utc(execution_start + timedelta(days=LOW_RISK_COOLDOWN_DAYS)),
        observation_window_started_at=ctx.executed_at,
        observation_window_ends_at=_format_utc(execution_start + timedelta(days=LOW_RISK_OBSERVATION_DAYS)),
        note="Paper loop learn/evolve follow-through recorded.",
    )
    return decision


def _review_chain_contains(decision: EvolutionDecision, step_type: ReviewStepType) -> bool:
    return any(ReviewStepType(step.step_type) == step_type for step in decision.review_chain)


def build_review_packet_summary(
    approval_decision: ApprovalDecision,
    evolution_decision: EvolutionDecision,
) -> Dict[str, Any]:
    return {
        "approval_decision_id": approval_decision.decision_id,
        "approval_target_type": _enum_value(approval_decision.target_type),
        "approval_target_id": approval_decision.target_id,
        "risk_level": _enum_value(evolution_decision.risk_level),
        "review_owner": EvolutionActorRole.REVIEWER_ON_DUTY.value,
        "approval_owner": EvolutionActorRole.REVIEWER_ON_DUTY.value,
        "review_chain_steps": [
            _enum_value(step.step_type) for step in evolution_decision.review_chain
        ],
        "authorized_by_policy": True,
        "policy_refs": [
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md#6.1",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md#5.2",
            "services/control-plane/governance/evolution_decision.contract.md#8",
        ],
    }


def build_evidence_packet(
    ctx: PaperEvolutionDecisionContext | None = None,
    *,
    telemetry_evidence: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    """Build the complete MGMT-PAPER-006 evidence packet."""
    ctx = ctx or PaperEvolutionDecisionContext()
    telemetry_evidence = telemetry_evidence or load_telemetry_evidence()
    telemetry_packet, ingest_validation, runtime_summary, telemetry_safety = normalize_telemetry_evidence(
        telemetry_evidence
    )
    evidence_refs = build_review_evidence_refs(ctx, telemetry_packet)
    approval_decision = build_evolution_approval_decision(ctx, evidence_refs=evidence_refs)
    evolution_decision = build_paper_evolution_decision(ctx, telemetry_evidence=telemetry_evidence)
    audit_event = to_audit_event(evolution_decision, "paper_evolution_decision_executed")
    event_ids = telemetry_event_ids(telemetry_packet)

    packet: Dict[str, Any] = {
        "task_id": TASK_ID,
        "epic": EPIC,
        "environment": PAPER_ENVIRONMENT,
        "generated_at": generated_at or utc_now(),
        "live_capital_side_effects": False,
        "source_packets": {
            "telemetry_packet": ctx.telemetry_packet_path,
        },
        "telemetry_review_input": {
            "packet_id": telemetry_packet.get("packet_id"),
            "event_count": telemetry_packet.get("event_count"),
            "event_ids": event_ids,
            "runtime_binding_id": runtime_summary.get("runtime_binding_id")
            or runtime_summary.get("binding_id"),
            "deployment_stage": runtime_summary.get("deployment_stage"),
            "ingest_validation": ingest_validation,
            "runtime_summary_projection": runtime_summary,
            "safety_assertions": telemetry_safety,
        },
        "approval_decision": approval_decision.to_dict(),
        "evolution_decision": evolution_decision.to_dict(),
        "review_packet": build_review_packet_summary(approval_decision, evolution_decision),
        "audit_event": audit_event,
        "ooda_decide_ref": {
            "approval_decision_id": approval_decision.decision_id,
            "evolution_decision_id": evolution_decision.decision_id,
        },
        "ooda_learn_ref": {
            "evolution_followthrough_refs": [
                f"evolution-decision://{evolution_decision.decision_id}/{ctx.execution_ref_id}"
            ],
            "observation_window": {
                "start_at": evolution_decision.observation_window_started_at,
                "end_at": evolution_decision.observation_window_ends_at,
            },
        },
        "safety_assertions": {
            "paper_environment": True,
            "low_risk_revalidate": evolution_decision.risk_level == RiskLevel.LOW
            and evolution_decision.action_type == EvolutionActionType.REVALIDATE,
            "research_execution_plane": evolution_decision.execution_result is not None
            and evolution_decision.execution_result.plane == ExecutionPlane.RESEARCH,
            "no_live_capital_side_effects": True,
            "no_runtime_binding_mutation": evolution_decision.metadata is not None
            and evolution_decision.metadata.get("runtime_binding_mutation_allowed") is False,
            "no_broker_order": evolution_decision.metadata is not None
            and evolution_decision.metadata.get("broker_order_allowed") is False,
            "live_mutation_allowed_false": evolution_decision.metadata is not None
            and evolution_decision.metadata.get("live_mutation_allowed") is False,
            "approval_decision_targets_evolution_proposal": approval_decision.target_type
            == TargetType.EVOLUTION_PROPOSAL
            and approval_decision.target_id == evolution_decision.decision_id,
            "cooldown_observation_recorded": bool(
                evolution_decision.cooldown_ends_at
                and evolution_decision.observation_window_ends_at
            ),
        },
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec",
            "MGMT-PAPER-002: ApprovalDecision packet",
            "MGMT-PAPER-003: DeploymentPlan packet",
            "MGMT-PAPER-004: paper RuntimeBinding packet",
            "MGMT-PAPER-005: telemetry packet",
            "MGMT-PAPER-006: EvolutionDecision review packet  <- this artifact",
            "MGMT-PAPER-007: complete OODA packet",
        ],
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_evidence_packet(packet)
    return packet


def validate_evidence_packet(packet: Mapping[str, Any]) -> List[str]:
    """Return packet validation errors. Empty list means pass."""
    errors: List[str] = []
    if packet.get("task_id") != TASK_ID:
        errors.append("task_id must be MGMT-PAPER-006")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    telemetry_input = packet.get("telemetry_review_input")
    if not isinstance(telemetry_input, Mapping):
        errors.append("telemetry_review_input must be present")
        telemetry_input = {}
    else:
        if not telemetry_input.get("packet_id"):
            errors.append("telemetry_review_input.packet_id is required")
        event_ids = telemetry_input.get("event_ids")
        if not isinstance(event_ids, list) or not event_ids:
            errors.append("telemetry_review_input.event_ids must not be empty")
        if telemetry_input.get("deployment_stage") != PAPER_ENVIRONMENT:
            errors.append("telemetry_review_input.deployment_stage must be paper")
        ingest = telemetry_input.get("ingest_validation")
        if not isinstance(ingest, Mapping):
            errors.append("telemetry_review_input.ingest_validation must be present")
            ingest = {}
        for key in (
            "heartbeat_first_accepted",
            "heartbeat_duplicate_accepted",
            "stage_mismatch_rejected",
            "missing_binding_rejected",
        ):
            if ingest.get(key) is not True:
                errors.append(f"telemetry ingest invariant failed: {key}")
        telemetry_safety = telemetry_input.get("safety_assertions")
        if not isinstance(telemetry_safety, Mapping):
            errors.append("telemetry_review_input.safety_assertions must be present")
        else:
            for key in (
                "paper_stage_only",
                "live_broker_telemetry_disabled",
                "capital_binding_live_disabled",
                "bracket_logged_only",
                "no_real_order",
                "no_real_capital",
            ):
                if telemetry_safety.get(key) is not True:
                    errors.append(f"telemetry safety assertion failed: {key}")

    approval_raw = packet.get("approval_decision")
    if not isinstance(approval_raw, Mapping):
        errors.append("approval_decision must be present")
        approval = None
    else:
        try:
            approval = ApprovalDecision.from_dict(dict(approval_raw))
            errors.extend(f"approval_decision: {error}" for error in validate_decision(approval))
        except Exception as exc:
            approval = None
            errors.append(f"approval_decision parse failed: {exc}")

    evolution_raw = packet.get("evolution_decision")
    if not isinstance(evolution_raw, Mapping):
        errors.append("evolution_decision must be present")
        evolution = None
    else:
        try:
            evolution = EvolutionDecision.from_dict(evolution_raw)
            errors.extend(
                f"evolution_decision: {error}"
                for error in validate_evolution_decision(evolution)
            )
        except Exception as exc:
            evolution = None
            errors.append(f"evolution_decision parse failed: {exc}")

    if approval is not None and evolution is not None:
        if approval.target_type != TargetType.EVOLUTION_PROPOSAL:
            errors.append("approval_decision.target_type must be evolution_proposal")
        if approval.target_id != evolution.decision_id:
            errors.append("approval_decision.target_id must match evolution decision_id")
        if approval.decision_state != DecisionState.DECIDED:
            errors.append("approval_decision must be decided")
        if approval.decision != DecisionOutcome.APPROVED:
            errors.append("approval_decision must be approved")
        if evolution.approval_decision_id != approval.decision_id:
            errors.append("evolution_decision.approval_decision_id mismatch")
        if evolution.decision_state != EvolutionDecisionState.EXECUTED:
            errors.append("evolution_decision must be executed")
        if evolution.action_type != EvolutionActionType.REVALIDATE:
            errors.append("evolution_decision.action_type must be revalidate")
        if evolution.risk_level != RiskLevel.LOW:
            errors.append("evolution_decision.risk_level must be low")
        if not _review_chain_contains(evolution, ReviewStepType.REVIEWED):
            errors.append("evolution review_chain must contain reviewed")
        if not _review_chain_contains(evolution, ReviewStepType.APPROVED):
            errors.append("evolution review_chain must contain approved")
        if not _review_chain_contains(evolution, ReviewStepType.EXECUTED):
            errors.append("evolution review_chain must contain executed")
        if evolution.execution_result is None:
            errors.append("evolution execution_result must be present")
        elif evolution.execution_result.plane != ExecutionPlane.RESEARCH:
            errors.append("evolution execution_result.plane must be research")
        metadata = evolution.metadata or {}
        if metadata.get("live_mutation_allowed") is not False:
            errors.append("evolution metadata.live_mutation_allowed must be false")
        if metadata.get("runtime_binding_mutation_allowed") is not False:
            errors.append("evolution metadata.runtime_binding_mutation_allowed must be false")
        if metadata.get("broker_order_allowed") is not False:
            errors.append("evolution metadata.broker_order_allowed must be false")

    ooda_decide_ref = packet.get("ooda_decide_ref")
    if not isinstance(ooda_decide_ref, Mapping):
        errors.append("ooda_decide_ref must be present")
    elif evolution is not None and approval is not None:
        if ooda_decide_ref.get("evolution_decision_id") != evolution.decision_id:
            errors.append("ooda_decide_ref.evolution_decision_id mismatch")
        if ooda_decide_ref.get("approval_decision_id") != approval.decision_id:
            errors.append("ooda_decide_ref.approval_decision_id mismatch")

    ooda_learn_ref = packet.get("ooda_learn_ref")
    if not isinstance(ooda_learn_ref, Mapping):
        errors.append("ooda_learn_ref must be present")
    else:
        refs = ooda_learn_ref.get("evolution_followthrough_refs")
        if not isinstance(refs, list) or not refs:
            errors.append("ooda_learn_ref.evolution_followthrough_refs must not be empty")
        window = ooda_learn_ref.get("observation_window")
        if not isinstance(window, Mapping) or not window.get("start_at") or not window.get("end_at"):
            errors.append("ooda_learn_ref.observation_window must include start_at/end_at")

    safety = packet.get("safety_assertions")
    if not isinstance(safety, Mapping):
        errors.append("safety_assertions must be present")
    else:
        for key, value in safety.items():
            if value is not True:
                errors.append(f"safety assertion failed: {key}")

    return errors


def write_evidence_packet(packet: Mapping[str, Any], out_path: Path = EVIDENCE_PATH) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_copy(packet)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    print("=== MGMT-PAPER-006: paper EvolutionDecision review packet ===\n")
    packet = build_evidence_packet()
    write_evidence_packet(packet, EVIDENCE_PATH)
    errors = packet["validation_errors"]
    decision = packet["evolution_decision"]

    print(f"  decision_id     : {decision['decision_id']}")
    print(f"  approval_id     : {decision['approval_decision_id']}")
    print(f"  action          : {decision['action_type']}")
    print(f"  state           : {decision['decision_state']}")
    print(f"  execution_plane : {decision['execution_result']['plane']}")
    print(f"  cooldown_ends   : {decision['cooldown_ends_at']}")
    print(f"  observation_end : {decision['observation_window_ends_at']}")
    print(f"  validation      : {'PASS (no errors)' if not errors else errors}")
    print(f"\n  evidence packet written to: {EVIDENCE_PATH}")
    print("\n=== PASS ===" if not errors else "\n=== FAIL ===")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
