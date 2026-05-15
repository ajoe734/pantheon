"""
MGMT-EVO-001 telemetry-to-evolution packet link.

This module builds the first explicit evidence packet that links post-action
paper telemetry to a governed EvolutionDecision proposal input. It does not
write an EvolutionDecision into the service store and it does not execute any
runtime, deployment, broker, or capital-binding mutation.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


OODA_DIR = Path(__file__).resolve().parent
REPO_ROOT = OODA_DIR.parents[2]
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"

for _path in (REPO_ROOT, OODA_DIR, GOVERNANCE_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from approval_decision import EvidenceRef, EvidenceRefType  # noqa: E402
from evolution_decision import (  # noqa: E402
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionState,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
    validate_evolution_decision,
)
from paper_loop_packet import PaperOodaLoopContext, build_telemetry_packet  # noqa: E402


TASK_ID = "MGMT-EVO-001"
MILESTONE_ID = "MGMT-OODA-M6"
EPIC = "EPIC-06 Evolution Follow-Through"
PAPER_ENVIRONMENT = "paper"

TASK_EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-EVO-001-telemetry-evolution-link.json"


@dataclass(frozen=True)
class TelemetryEvolutionLinkContext:
    """Stable identifiers for the telemetry-to-evolution link packet."""

    link_id: str = "telemetry-evolution-link-paper-001"
    decision_id: str = "evolution-telemetry-link-paper-001"
    source_ooda_packet_id: str = "ooda-paper-mgmt-loop-001"
    strategy_id: str = "strategy-spec-paper-qlib-lgbm-001"
    strategy_version: str = "1.0.0"
    capital_pool_id: str = "capital-pool-paper-001"
    sponsor_persona_id: str = "persona-quant-paper-01"
    created_at: str = "2026-05-15T15:08:00Z"
    telemetry_packet_path: str = "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json"
    paper_task_packet_path: str = "support/evidence/MGMT-PAPER-007-complete-paper-ooda-packet.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _normalize_telemetry_packet(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = packet.get("telemetry_packet")
    if isinstance(nested, Mapping):
        return nested
    return packet


def _telemetry_event_ids(telemetry_packet: Mapping[str, Any]) -> list[str]:
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


def _telemetry_refs(telemetry_packet: Mapping[str, Any]) -> list[str]:
    return [f"telemetry-event://{event_id}" for event_id in _telemetry_event_ids(telemetry_packet)]


def build_telemetry_evidence_ref(
    ctx: TelemetryEvolutionLinkContext,
    telemetry_packet: Mapping[str, Any],
) -> EvidenceRef:
    packet_id = str(telemetry_packet.get("packet_id") or "")
    return EvidenceRef(
        ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
        ref_id=packet_id,
        storage_ref={
            "backend": "support/evidence",
            "path": ctx.telemetry_packet_path,
            "packet_id": packet_id,
        },
        note="Post-action paper telemetry summary for evolution review input; proposal-only, no live mutation.",
    )


def build_threshold_snapshot() -> ThresholdSnapshot:
    return ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.3",
        signal_type=ThresholdSignalType.MANUAL_REVIEW,
        metric_name="telemetry_packet_ready_for_evolution_review",
        comparator=ComparisonOperator.GTE,
        observed_value=1,
        threshold_value=1,
        window="paper-loop",
        breached=True,
        note="Telemetry packet is complete enough to enter governed evolution review.",
    )


def build_evolution_proposal(
    ctx: TelemetryEvolutionLinkContext | None = None,
    *,
    telemetry_packet: Mapping[str, Any] | None = None,
) -> EvolutionDecision:
    ctx = ctx or TelemetryEvolutionLinkContext()
    telemetry_packet = _normalize_telemetry_packet(telemetry_packet or build_telemetry_packet(PaperOodaLoopContext()))
    evidence_ref = build_telemetry_evidence_ref(ctx, telemetry_packet)
    threshold_snapshot = build_threshold_snapshot()
    telemetry_refs = _telemetry_refs(telemetry_packet)

    decision = EvolutionDecision.create_proposed(
        decision_id=ctx.decision_id,
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id=ctx.strategy_id,
        target_version=ctx.strategy_version,
        action_type=EvolutionActionType.REVALIDATE,
        rationale=(
            "Link the paper telemetry packet into governed evolution review. "
            "This proposal remains review-gated and cannot mutate runtime state."
        ),
        created_by_id="evolution-controller-paper-telemetry-link",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[evidence_ref],
        threshold_snapshots=[threshold_snapshot],
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.sponsor_persona_id,
        metadata={
            "task_id": TASK_ID,
            "link_id": ctx.link_id,
            "source_ooda_packet_id": ctx.source_ooda_packet_id,
            "telemetry_packet_id": telemetry_packet.get("packet_id"),
            "telemetry_refs": telemetry_refs,
            "proposal_only": True,
            "live_mutation_allowed": False,
            "runtime_binding_mutation_allowed": False,
        },
    )
    decision.created_at = ctx.created_at
    return decision


def build_proposal_request(decision: EvolutionDecision) -> dict[str, Any]:
    payload = decision.to_dict()
    return {
        "decision_id": payload["decision_id"],
        "target_type": payload["target_type"],
        "target_id": payload["target_id"],
        "target_version": payload["target_version"],
        "action_type": payload["action_type"],
        "rationale": payload["rationale"],
        "created_by_id": payload["created_by_id"],
        "created_by_role": payload["created_by_role"],
        "risk_level": payload["risk_level"],
        "target_stage": payload.get("target_stage"),
        "persona_id": payload.get("persona_id"),
        "capital_pool_id": payload.get("capital_pool_id"),
        "linked_incident_id": payload.get("linked_incident_id"),
        "linked_postmortem_id": payload.get("linked_postmortem_id"),
        "evidence_refs": payload.get("evidence_refs", []),
        "threshold_snapshots": payload.get("threshold_snapshots", []),
        "metadata": payload.get("metadata"),
    }


def build_ooda_learn_patch(
    ctx: TelemetryEvolutionLinkContext,
    *,
    telemetry_packet: Mapping[str, Any],
    decision: EvolutionDecision,
) -> dict[str, Any]:
    telemetry_refs = _telemetry_refs(telemetry_packet)
    return {
        "packet_id": ctx.source_ooda_packet_id,
        "operation": "append_learn_refs",
        "expected_stage": "acted_or_evolving",
        "learn": {
            "telemetry_refs": telemetry_refs,
            "postmortem_refs": [],
            "evolution_followthrough_refs": [
                f"evolution-decision://{decision.decision_id}/proposal"
            ],
            "trainer_refs": [],
            "retrain_refs": [],
            "observation_window": None,
        },
        "status_after_patch": "evolving",
    }


def build_evidence_packet(
    ctx: TelemetryEvolutionLinkContext | None = None,
    *,
    telemetry_packet: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    ctx = ctx or TelemetryEvolutionLinkContext()
    telemetry_packet = _normalize_telemetry_packet(telemetry_packet or build_telemetry_packet(PaperOodaLoopContext()))
    decision = build_evolution_proposal(ctx, telemetry_packet=telemetry_packet)
    proposal_request = build_proposal_request(decision)
    ooda_learn_patch = build_ooda_learn_patch(ctx, telemetry_packet=telemetry_packet, decision=decision)
    event_ids = _telemetry_event_ids(telemetry_packet)

    packet: dict[str, Any] = {
        "task_id": TASK_ID,
        "milestone_id": MILESTONE_ID,
        "epic": EPIC,
        "environment": PAPER_ENVIRONMENT,
        "link_id": ctx.link_id,
        "link_type": "telemetry_to_evolution",
        "generated_at": generated_at or utc_now(),
        "source_packets": {
            "telemetry_packet": ctx.telemetry_packet_path,
            "paper_ooda_packet": ctx.paper_task_packet_path,
        },
        "telemetry_summary": {
            "packet_id": telemetry_packet.get("packet_id"),
            "event_count": telemetry_packet.get("event_count"),
            "event_ids": event_ids,
            "event_refs": _telemetry_refs(telemetry_packet),
            "runtime_summary_projection": telemetry_packet.get("runtime_summary_projection", {}),
            "safety_assertions": telemetry_packet.get("safety_assertions", {}),
        },
        "evidence_ref": proposal_request["evidence_refs"][0],
        "proposal_request": proposal_request,
        "evolution_decision": decision.to_dict(),
        "ooda_link": {
            "source_packet_id": ctx.source_ooda_packet_id,
            "learn_patch": ooda_learn_patch,
        },
        "policy_refs": [
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.3",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2-§5.3",
            "docs/04/pantheon_sa_supplemental_2026-05-15/SD_management_console_multi_persona_ooda.md §7.5",
        ],
        "safety_assertions": {
            "proposal_only": True,
            "evolution_decision_state_proposed": decision.decision_state == EvolutionDecisionState.PROPOSED,
            "no_live_capital_side_effects": True,
            "no_runtime_binding_mutation": True,
            "no_broker_order": True,
            "live_mutation_allowed_false": decision.metadata is not None
            and decision.metadata.get("live_mutation_allowed") is False,
        },
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_evidence_packet(packet)
    return packet


def validate_evidence_packet(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("task_id") != TASK_ID:
        errors.append("task_id must be MGMT-EVO-001")
    if packet.get("milestone_id") != MILESTONE_ID:
        errors.append("milestone_id must be MGMT-OODA-M6")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("link_type") != "telemetry_to_evolution":
        errors.append("link_type must be telemetry_to_evolution")

    telemetry_summary = packet.get("telemetry_summary")
    if not isinstance(telemetry_summary, Mapping):
        errors.append("telemetry_summary must be present")
        telemetry_summary = {}
    telemetry_packet_id = telemetry_summary.get("packet_id")
    event_ids = telemetry_summary.get("event_ids")
    event_refs = telemetry_summary.get("event_refs")
    if not telemetry_packet_id:
        errors.append("telemetry_summary.packet_id is required")
    if not isinstance(event_ids, list) or not event_ids:
        errors.append("telemetry_summary.event_ids must not be empty")
        event_ids = []
    if not isinstance(event_refs, list) or not event_refs:
        errors.append("telemetry_summary.event_refs must not be empty")
        event_refs = []

    evidence_ref = packet.get("evidence_ref")
    if not isinstance(evidence_ref, Mapping):
        errors.append("evidence_ref must be present")
        evidence_ref = {}
    else:
        if evidence_ref.get("ref_type") != EvidenceRefType.TELEMETRY_SUMMARY.value:
            errors.append("evidence_ref.ref_type must be telemetry_summary")
        if evidence_ref.get("ref_id") != telemetry_packet_id:
            errors.append("evidence_ref.ref_id must match telemetry_summary.packet_id")

    proposal_request = packet.get("proposal_request")
    if not isinstance(proposal_request, Mapping):
        errors.append("proposal_request must be present")
        proposal_request = {}
    else:
        proposal_refs = proposal_request.get("evidence_refs")
        if not isinstance(proposal_refs, list) or evidence_ref not in proposal_refs:
            errors.append("proposal_request must include the telemetry evidence_ref")
        metadata = proposal_request.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("proposal_only") is not True:
            errors.append("proposal_request.metadata.proposal_only must be true")
        if isinstance(metadata, Mapping) and metadata.get("runtime_binding_mutation_allowed") is not False:
            errors.append("proposal_request.metadata.runtime_binding_mutation_allowed must be false")

    evolution_decision = packet.get("evolution_decision")
    if not isinstance(evolution_decision, Mapping):
        errors.append("evolution_decision must be present")
        evolution_decision = {}
    else:
        if evolution_decision.get("decision_state") != EvolutionDecisionState.PROPOSED.value:
            errors.append("evolution_decision must remain proposed")
        try:
            decision_errors = validate_evolution_decision(EvolutionDecision.from_dict(evolution_decision))
            errors.extend(f"evolution_decision: {error}" for error in decision_errors)
        except Exception as exc:
            errors.append(f"evolution_decision parse failed: {exc}")

    ooda_link = packet.get("ooda_link")
    if not isinstance(ooda_link, Mapping):
        errors.append("ooda_link must be present")
        ooda_link = {}
    learn_patch = ooda_link.get("learn_patch") if isinstance(ooda_link, Mapping) else None
    if not isinstance(learn_patch, Mapping):
        errors.append("ooda_link.learn_patch must be present")
        learn_patch = {}
    learn = learn_patch.get("learn") if isinstance(learn_patch, Mapping) else None
    if not isinstance(learn, Mapping):
        errors.append("ooda_link.learn_patch.learn must be present")
        learn = {}
    else:
        learn_refs = learn.get("telemetry_refs")
        if not isinstance(learn_refs, list) or not learn_refs:
            errors.append("learn_patch.learn.telemetry_refs must not be empty")
            learn_refs = []
        missing = [ref for ref in learn_refs if ref not in event_refs]
        if missing:
            errors.append(f"learn_patch telemetry_refs not backed by telemetry_summary.event_refs: {missing}")
        evolution_refs = learn.get("evolution_followthrough_refs")
        if not isinstance(evolution_refs, list) or not evolution_refs:
            errors.append("learn_patch.learn.evolution_followthrough_refs must not be empty")

    safety = packet.get("safety_assertions")
    if not isinstance(safety, Mapping):
        errors.append("safety_assertions must be present")
    else:
        for key, value in safety.items():
            if value is not True:
                errors.append(f"safety assertion failed: {key}")

    return errors


def write_evidence_packet(
    packet: Mapping[str, Any],
    *,
    task_path: Path = TASK_EVIDENCE_PATH,
) -> dict[str, Any]:
    task_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_copy(packet)
    task_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    print("=== MGMT-EVO-001: telemetry-to-evolution packet link ===\n")
    packet = build_evidence_packet()
    write_evidence_packet(packet)
    errors = packet["validation_errors"]
    print(f"  link_id          : {packet['link_id']}")
    print(f"  telemetry_packet : {packet['telemetry_summary']['packet_id']}")
    print(f"  proposal         : {packet['proposal_request']['decision_id']}")
    print(f"  ooda_packet      : {packet['ooda_link']['source_packet_id']}")
    print(f"  validation       : {'PASS (no errors)' if not errors else errors}")
    print(f"\n  evidence packet written to: {TASK_EVIDENCE_PATH}")
    print("\n=== PASS ===" if not errors else "\n=== FAIL ===")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
