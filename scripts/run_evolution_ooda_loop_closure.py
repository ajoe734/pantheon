#!/usr/bin/env python3
"""MGMT-EVO-007 evolution OODA loop closure.

This script builds a complete, replayable evolution-type OodaLoopPacket that
closes the EPIC-06 Evolution Follow-Through chain.

The packet traces:
  Observe  — telemetry breach + incident signal (EVO-001)
  Orient   — incident/postmortem analysis + governance evidence (EVO-002)
  Decide   — freeze EvolutionDecision + mutation-review linkage (EVO-003/EVO-005)
  Act      — rollback + freeze follow-through commands (EVO-005)
  Learn    — observation window closed + follow-through refs (EVO-006)
  Closed   — OODA loop terminal

No live broker, runtime mutation, capital binding, or deployment side effects
are performed. All references are to existing local evidence or synthetic
replay identifiers.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
OODA_DIR = REPO_ROOT / "services" / "control-plane" / "ooda"
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"

for _path in (str(REPO_ROOT), str(OODA_DIR), str(GOVERNANCE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from approval_decision import EvidenceRef, EvidenceRefType  # noqa: E402
from evolution_decision import (  # noqa: E402
    ComparisonOperator,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
    validate_evolution_decision,
)
from jsonl_store import OodaJsonlAppendStore, OodaPacketQuery  # noqa: E402
from ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObservationWindow,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
    validate_packet,
)
from stage_transition import OodaTransitionError, assert_complete_replay_path  # noqa: E402


TASK_ID = "MGMT-EVO-007"
MILESTONE_ID = "MGMT-OODA-M6"
EPIC = "EPIC-06 Evolution Follow-Through"
PAPER_ENVIRONMENT = "paper"

TASK_EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / TASK_ID / "evolution-ooda-loop-closure.json"
MILESTONE_EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-OODA-M6-evolution-followthrough.json"

DEFAULT_GENERATED_AT = "2026-05-15T18:00:00Z"


@dataclass(frozen=True)
class EvoOodaClosureContext:
    """Stable identifiers for the evolution OODA loop closure packet."""

    packet_id: str = "ooda-evolution-mgmt-007-closure"
    strategy_spec_id: str = "strategy-spec-paper-qlib-lgbm-001"
    strategy_version: str = "1.0.0"
    capital_pool_id: str = "capital-pool-paper-001"
    sponsor_persona_id: str = "persona-quant-paper-01"
    persona_capital_binding_id: str = "persona-capital-binding-live-mgmt-evo-005"
    operator_actor_id: str = "operator-evolution-closure-01"
    freeze_decision_id: str = "evo-mgmt-005-freeze-stage"
    rollback_decision_id: str = "evo-mgmt-005-runtime-rollback"
    approval_decision_id: str = "approval-mgmt-evo-005-governance-freeze"
    deployment_plan_id: str = "deployment-plan-mgmt-evo-005-freeze"
    runtime_binding_id: str = "runtime-binding-live-mgmt-evo-005"
    incident_id: str = "incident-mgmt-evo-005"
    postmortem_id: str = "postmortem-mgmt-evo-005"
    created_at: str = "2026-05-15T17:55:00Z"
    observation_start_at: str = "2026-05-15T17:55:00Z"
    observation_end_at: str = "2026-05-15T18:00:00Z"


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def build_freeze_evolution_decision(ctx: EvoOodaClosureContext) -> EvolutionDecision:
    """Build the approved+executed freeze EvolutionDecision for the closure packet."""
    decision = EvolutionDecision.create_proposed(
        decision_id=ctx.freeze_decision_id,
        target_type=EvolutionTargetType.PERSONA_CAPITAL_BINDING,
        target_id=ctx.persona_capital_binding_id,
        target_version="v1",
        action_type=EvolutionActionType.FREEZE,
        rationale=(
            "Severity-1 execution drift requires governed freeze follow-through. "
            "Rollback remains a companion runtime mitigation, not the freeze object itself."
        ),
        created_by_id="evolution-controller-mgmt-evo-005",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        target_stage="live",
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.sponsor_persona_id,
        linked_incident_id=ctx.incident_id,
        linked_postmortem_id=ctx.postmortem_id,
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.MANUAL_REVIEW_TICKET,
                ref_id=ctx.incident_id,
                storage_ref={
                    "backend": "support/evidence",
                    "path": "support/evidence/MGMT-EVO-005/rollback-freeze-followthrough.json",
                },
                note="Synthetic local replay anchor for rollback/freeze follow-through.",
            )
        ],
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5 and section 11.1",
                signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
                metric_name="severity1_execution_drift",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="incident-followthrough",
                breached=True,
                note="High-risk live freeze path requires governance committee approval.",
            )
        ],
        metadata={
            "task_id": TASK_ID,
            "ooda_packet_id": ctx.packet_id,
            "source": "local_followthrough_replay",
            "live_mutation_allowed": False,
            "broker_order_allowed": False,
            "capital_binding_mutation_allowed": False,
        },
    )
    decision.created_at = ctx.created_at
    decision.mark_reviewed(
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
        "committee-mgmt-evo-005",
        "approval-mgmt-evo-005",
        note="Reviewed high-risk freeze follow-through packet.",
        reviewed_at=ctx.created_at,
    )
    decision.approve(
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
        "committee-mgmt-evo-005",
        note="Approved local replay of freeze and rollback follow-through.",
        approved_at=ctx.created_at,
    )
    decision.execute(
        EvolutionActorRole.EVOLUTION_CONTROLLER,
        "evolution-controller-mgmt-evo-005",
        ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plane=ExecutionPlane.DEPLOYMENT,
            executed_at=ctx.created_at,
            execution_ref_id="freeze-stage-command-mgmt-evo-005",
            outcome_summary="Freeze stage command emitted; no live capital side effects.",
        ),
        cooldown_ends_at="2026-05-16T17:55:00Z",
        observation_window_ends_at="2026-05-22T17:55:00Z",
        note="Evolution OODA loop closure follow-through recorded.",
    )
    return decision


def build_complete_ooda_packet(
    ctx: EvoOodaClosureContext,
    *,
    freeze_decision: EvolutionDecision,
) -> tuple[OodaLoopPacket, list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the full evolution OODA loop packet traversing all stages to closed."""
    packet = OodaLoopPacket(
        packet_id=ctx.packet_id,
        loop_type=LoopType.EVOLUTION,
        status=LoopStatus.OPEN,
        environment=LoopEnvironment.PAPER,
        created_at=ctx.created_at,
        updated_at=ctx.created_at,
        capital_pool_id=ctx.capital_pool_id,
        strategy_id=ctx.strategy_spec_id,
        persona_ids=[ctx.sponsor_persona_id],
        observe=ObserveBundle(),
        orient=OrientBundle(),
        decide=DecideBundle(),
        act=ActBundle(live_capital_side_effects=False),
        learn=LearnBundle(),
        audit_refs=[
            "audit://evolution-closure/incident-opened",
            "audit://evolution-closure/freeze-governance-approved",
            "audit://evolution-closure/no-live-side-effects",
            "audit://evolution-closure/observation-window-closed",
        ],
    )

    snapshots: list[dict[str, Any]] = [packet.to_dict()]
    transitions: list[dict[str, Any]] = []

    def _advance(stage: str, target: LoopStatus) -> None:
        from_status = _enum_value(packet.status)
        packet.advance(target)
        transitions.append({
            "packet_id": packet.packet_id,
            "stage": stage,
            "from_status": from_status,
            "to_status": _enum_value(packet.status),
            "actor_id": ctx.operator_actor_id,
            "transitioned_at": packet.updated_at,
        })
        snapshots.append(packet.to_dict())

    # --- Observe ---
    packet.observe.incident_refs.append(f"incident://{ctx.incident_id}")
    packet.observe.telemetry_refs.append(
        "telemetry-event://631f17dc-bbba-4593-9ad3-9ed24c5b3001"  # paper heartbeat
    )
    packet.observe.signal_refs.append("signal://severity1-execution-drift-breach")
    packet.observe.source_refs.extend([
        "support/evidence/MGMT-EVO-001-telemetry-evolution-link.json",
        f"strategy-spec://{ctx.strategy_spec_id}@{ctx.strategy_version}",
    ])
    _advance("observe", LoopStatus.OBSERVING)

    # --- Orient ---
    packet.orient.evidence_bundle_refs.extend([
        "support/evidence/MGMT-EVO-005/rollback-freeze-followthrough.json",
        "support/evidence/MGMT-EVO-006/summary.md",
    ])
    packet.orient.risk_adjudication_ref = (
        "risk-adjudication://governance-committee-freeze-approved-mgmt-evo-005"
    )
    packet.orient.persona_proposal_refs.append(f"persona://{ctx.sponsor_persona_id}")
    packet.orient.signal_inference_refs.append(
        "signal-inference://severity1-drift-freeze-path-confirmed"
    )
    _advance("orient", LoopStatus.ORIENTED)

    # --- Decide ---
    packet.decide.approval_decision_id = ctx.approval_decision_id
    packet.decide.deployment_plan_id = ctx.deployment_plan_id
    packet.decide.evolution_decision_id = freeze_decision.decision_id
    packet.decide.sponsor_persona_id = ctx.sponsor_persona_id
    packet.decide.decision_rationale_ref = (
        "rationale://governance-committee-approved-live-freeze-no-capital-side-effects"
    )
    packet.decide.policy_decision_refs.extend([
        "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
        "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md section 5.2",
        "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md section 3",
    ])
    _advance("decide", LoopStatus.DECIDED)

    # --- Act ---
    packet.act.runtime_binding_id = ctx.runtime_binding_id
    packet.act.rollback_refs.extend([
        f"evolution-decision://{ctx.rollback_decision_id}/runtime-rollback",
        "rollback://pause_then_replace/artifact-paper-safe-fallback-001",
    ])
    packet.act.command_receipt_refs.extend([
        "command-receipt://freeze-stage-mgmt-evo-005",
        "command-receipt://runtime-rollback-mgmt-evo-005",
    ])
    packet.act.safe_mode_refs.append("safe-mode://live-broker-disabled-throughout-freeze")
    packet.act.live_capital_side_effects = False
    _advance("act", LoopStatus.ACTED)

    # --- Learn ---
    packet.learn.telemetry_refs.extend([
        "telemetry-event://631f17dc-bbba-4593-9ad3-9ed24c5b3003",  # pnl snapshot
        "telemetry-event://631f17dc-bbba-4593-9ad3-9ed24c5b3004",  # bracket logged
    ])
    packet.learn.postmortem_refs.append(f"postmortem://{ctx.postmortem_id}")
    packet.learn.evolution_followthrough_refs.extend([
        f"evolution-decision://{ctx.freeze_decision_id}/freeze-stage-command-mgmt-evo-005",
        f"evolution-decision://{ctx.rollback_decision_id}/runtime-rollback-mgmt-evo-005",
        "support/evidence/MGMT-EVO-005/rollback-freeze-followthrough.json",
    ])
    packet.learn.observation_window = ObservationWindow(
        start_at=ctx.observation_start_at,
        end_at=ctx.observation_end_at,
        duration_hours=0.083,  # 5-minute observation window in the closure packet
    )
    _advance("learn", LoopStatus.EVOLVING)
    _advance("close", LoopStatus.CLOSED)

    return packet, transitions, snapshots


def build_replay_validation(
    packet: OodaLoopPacket,
    transitions: list[Mapping[str, Any]],
    snapshots: list[Mapping[str, Any]],
) -> dict[str, Any]:
    assert_complete_replay_path(transitions)
    with tempfile.TemporaryDirectory(prefix="mgmt_evo_007_ooda_") as temp_dir:
        store_path = Path(temp_dir) / "ooda-evo-loop.jsonl"
        store = OodaJsonlAppendStore(store_path)
        store.append_packet(snapshots[0])
        for transition, snapshot in zip(transitions, snapshots[1:]):
            store.append_stage_transition(packet.packet_id, transition, packet=snapshot)

        latest = store.get_packet(packet.packet_id)
        by_evolution = store.list_packets(
            OodaPacketQuery(evolution_decision_id=packet.decide.evolution_decision_id)
        )
        by_runtime = store.list_packets(
            OodaPacketQuery(runtime_binding_id=packet.act.runtime_binding_id)
        )
        records = list(store.iter_records())

    return {
        "can_replay": bool(latest and latest.get("status") == LoopStatus.CLOSED.value),
        "record_count": len(records),
        "stage_transition_count": len(transitions),
        "latest_packet_id": latest.get("packet_id") if latest else None,
        "latest_status": latest.get("status") if latest else None,
        "query_matches": {
            "evolution_decision_id": [item["packet_id"] for item in by_evolution],
            "runtime_binding_id": [item["packet_id"] for item in by_runtime],
        },
    }


def build_evidence_packet(
    ctx: EvoOodaClosureContext | None = None,
    *,
    generated_at: str = DEFAULT_GENERATED_AT,
) -> dict[str, Any]:
    ctx = ctx or EvoOodaClosureContext()
    freeze_decision = build_freeze_evolution_decision(ctx)
    ooda_packet, transitions, snapshots = build_complete_ooda_packet(
        ctx, freeze_decision=freeze_decision
    )
    replay_validation = build_replay_validation(ooda_packet, transitions, snapshots)

    packet: dict[str, Any] = {
        "task_id": TASK_ID,
        "milestone_id": MILESTONE_ID,
        "epic": EPIC,
        "packet_id": ctx.packet_id,
        "generated_at": generated_at,
        "environment": PAPER_ENVIRONMENT,
        "live_capital_side_effects": False,
        "freeze_decision": freeze_decision.to_dict(),
        "ooda_packet": ooda_packet.to_dict(),
        "stage_transitions": transitions,
        "replay_validation": replay_validation,
        "evolution_chain": [
            "MGMT-EVO-001: telemetry-to-evolution packet link (observe refs)",
            "MGMT-EVO-002: proposal from incident/postmortem (orient evidence)",
            "MGMT-EVO-003: mutation-review UI linkage (decide refs)",
            "MGMT-EVO-004: retrain/revalidate dispatch (orient/learn refs)",
            "MGMT-EVO-005: rollback/freeze follow-through (act refs)",
            "MGMT-EVO-006: observation window report (learn window)",
            "MGMT-EVO-007: OODA loop closure (packet closed)",
        ],
        "safety_assertions": {
            "evolution_type_packet": ooda_packet.loop_type == LoopType.EVOLUTION,
            "paper_environment": ooda_packet.environment == LoopEnvironment.PAPER,
            "ooda_live_capital_side_effects_false": ooda_packet.act.live_capital_side_effects is False,
            "freeze_decision_executed": freeze_decision.decision_state == "executed",
            "no_live_broker_action": True,
            "no_real_capital_mutation": True,
            "no_deployment_side_effects": True,
            "replay_store_roundtrip": replay_validation["can_replay"],
        },
        "policy_refs": [
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 11.1",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md section 5.2",
            "ROLLBACK_AND_POSITION_SEMANTICS.md section 10",
            "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md section 3",
            "docs/04/pantheon_sa_supplemental_2026-05-15/SD_management_console_multi_persona_ooda.md section 7.5",
        ],
        "artifact_refs": {
            "task_evidence": str(TASK_EVIDENCE_PATH.relative_to(REPO_ROOT)),
            "milestone_evidence": str(MILESTONE_EVIDENCE_PATH.relative_to(REPO_ROOT)),
            "evo_005_followthrough": "support/evidence/MGMT-EVO-005/rollback-freeze-followthrough.json",
            "evo_006_observation": "support/evidence/MGMT-EVO-006/summary.md",
        },
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_evidence_packet(packet)
    return packet


def validate_evidence_packet(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    if packet.get("task_id") != TASK_ID:
        errors.append("task_id must be MGMT-EVO-007")
    if packet.get("milestone_id") != MILESTONE_ID:
        errors.append("milestone_id must be MGMT-OODA-M6")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    ooda_packet = packet.get("ooda_packet")
    if not isinstance(ooda_packet, Mapping):
        errors.append("ooda_packet must be present")
        ooda_packet = {}
    else:
        ooda_errors = validate_packet(dict(ooda_packet))
        errors.extend(f"ooda_packet: {error}" for error in ooda_errors)
        if ooda_packet.get("status") != LoopStatus.CLOSED.value:
            errors.append("ooda_packet status must be closed")
        if ooda_packet.get("loop_type") != LoopType.EVOLUTION.value:
            errors.append("ooda_packet loop_type must be evolution")
        if ooda_packet.get("environment") != PAPER_ENVIRONMENT:
            errors.append("ooda_packet environment must be paper")

        decide = ooda_packet.get("decide") or {}
        act = ooda_packet.get("act") or {}
        learn = ooda_packet.get("learn") or {}

        if not decide.get("evolution_decision_id"):
            errors.append("ooda_packet decide.evolution_decision_id must be set")
        if act.get("live_capital_side_effects") is not False:
            errors.append("ooda_packet act.live_capital_side_effects must be false")
        if not act.get("rollback_refs"):
            errors.append("ooda_packet act.rollback_refs must not be empty")
        if not learn.get("evolution_followthrough_refs"):
            errors.append("ooda_packet learn.evolution_followthrough_refs must not be empty")
        if not learn.get("postmortem_refs"):
            errors.append("ooda_packet learn.postmortem_refs must not be empty")
        if not learn.get("observation_window"):
            errors.append("ooda_packet learn.observation_window must be set")

    freeze_decision = packet.get("freeze_decision")
    if not isinstance(freeze_decision, Mapping):
        errors.append("freeze_decision must be present")
        freeze_decision = {}
    else:
        if freeze_decision.get("decision_state") != "executed":
            errors.append("freeze_decision must be executed")
        if freeze_decision.get("action_type") != "freeze":
            errors.append("freeze_decision action_type must be freeze")
        try:
            evo_errors = validate_evolution_decision(
                EvolutionDecision.from_dict(freeze_decision)
            )
            errors.extend(f"freeze_decision: {error}" for error in evo_errors)
        except Exception as exc:
            errors.append(f"freeze_decision parse failed: {exc}")

    transitions = packet.get("stage_transitions")
    if not isinstance(transitions, list):
        errors.append("stage_transitions must be present")
    else:
        try:
            assert_complete_replay_path(transitions)
        except OodaTransitionError as exc:
            errors.append(f"stage_transitions: {exc}")

    replay_validation = packet.get("replay_validation")
    if not isinstance(replay_validation, Mapping):
        errors.append("replay_validation must be present")
    else:
        if replay_validation.get("can_replay") is not True:
            errors.append("replay_validation.can_replay must be true")
        if replay_validation.get("latest_status") != LoopStatus.CLOSED.value:
            errors.append("replay_validation.latest_status must be closed")
        query_matches = replay_validation.get("query_matches") or {}
        packet_id = ooda_packet.get("packet_id") if isinstance(ooda_packet, Mapping) else None
        if packet_id not in query_matches.get("evolution_decision_id", []):
            errors.append("replay_validation query must match evolution_decision_id")

    safety = packet.get("safety_assertions") or {}
    if isinstance(safety, Mapping):
        for key, value in safety.items():
            if value is not True:
                errors.append(f"safety assertion failed: {key}")
    else:
        errors.append("safety_assertions must be present")

    return errors


def write_evidence_packet(
    packet: Mapping[str, Any],
    *,
    task_path: Path = TASK_EVIDENCE_PATH,
    milestone_path: Path = MILESTONE_EVIDENCE_PATH,
) -> dict[str, Any]:
    task_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_copy(packet)
    task_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if milestone_path != task_path:
        milestone_path.parent.mkdir(parents=True, exist_ok=True)
        milestone_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write the evidence packet to this JSON path (task path).",
    )
    parser.add_argument(
        "--milestone-out",
        type=Path,
        default=None,
        help="Write the evidence packet to this JSON path (milestone path).",
    )
    parser.add_argument(
        "--generated-at",
        default=DEFAULT_GENERATED_AT,
        help="RFC3339 timestamp to stamp into deterministic packet fields.",
    )
    args = parser.parse_args(argv)

    packet = build_evidence_packet(generated_at=args.generated_at)
    errors = packet["validation_errors"]
    ooda_packet = packet["ooda_packet"]

    task_path = args.json_out or TASK_EVIDENCE_PATH
    milestone_path = args.milestone_out or MILESTONE_EVIDENCE_PATH
    write_evidence_packet(packet, task_path=task_path, milestone_path=milestone_path)

    print(f"  packet_id           : {ooda_packet['packet_id']}")
    print(f"  status              : {ooda_packet['status']}")
    print(f"  loop_type           : {ooda_packet['loop_type']}")
    print(f"  evolution_decision  : {ooda_packet['decide']['evolution_decision_id']}")
    print(f"  rollback_refs       : {len(ooda_packet['act']['rollback_refs'])}")
    print(f"  followthrough_refs  : {len(ooda_packet['learn']['evolution_followthrough_refs'])}")
    print(f"  replay_records      : {packet['replay_validation']['record_count']}")
    print(f"  validation          : {'PASS (no errors)' if not errors else errors}")
    print(f"\n  evidence written to : {task_path}")
    print(f"  milestone written to: {milestone_path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
