"""OODA-CANARY-003-V2: canary telemetry to evolution proposal e2e test.

Proves the canary Learn stage path:
canary TelemetryEvent -> IncidentCase -> Postmortem ->
EvolutionDecisionProposal -> closed CanaryOodaPacket.
"""
from __future__ import annotations

from typing import Any

from services.evolution.postmortem_bridge import on_postmortem_published
from services.incident.incident import (
    IncidentCase,
    IncidentStore,
    Postmortem,
    validate_incident_case,
    validate_postmortem,
)
from services.ooda.canary_packet_model import (
    CanaryActStage,
    CanaryAssertions,
    CanaryDecideStage,
    CanaryLearnStage,
    CanaryObserveStage,
    CanaryOodaPacket,
    CanaryOodaStages,
    CanaryOrientStage,
    CanaryPacketStatus,
)


CANARY_CAPITAL_SCALE_LIMIT_PCT = 5.0
CANARY_GROSS_SCALE_LIMIT_PCT = 25.0


def test_canary_telemetry_to_evolution_happy_path_closes_packet() -> None:
    telemetry = _canary_telemetry()

    incident, postmortem, proposal, validation_errors = _run_canary_learn_path(telemetry)
    packet = _build_closed_canary_packet(
        telemetry,
        incident=incident,
        postmortem=postmortem,
        proposal=proposal,
        validation_errors=validation_errors,
    )

    assert incident.deployment_stage == "canary"
    assert incident.binding_id == telemetry["binding_id"]
    assert telemetry["event_id"] in incident.telemetry_event_ids
    assert postmortem.incident_id == incident.incident_id

    assert proposal["proposed_action"] == "rollback"
    assert proposal["target_deployment_stage"] == "canary"
    assert proposal["created_by_role"] == "evolution_controller"
    assert proposal.get("governance_store_written", False) is False

    assert packet.status == CanaryPacketStatus.CLOSED
    assert packet.learn.incident_ref == f"incident://{incident.incident_id}"
    assert packet.learn.postmortem_ref == f"postmortem://{postmortem.postmortem_id}"
    assert packet.learn.evolution_proposal_ref == (
        f"evolution-proposal://{proposal['source_postmortem_id']}/rollback"
    )
    assert packet.assertions.live_capital_scope_limited is True
    assert packet.assertions.human_gate_valid is True
    assert packet.assertions.validation_errors_empty is True
    assert packet.validate() == []


def test_canary_packet_fails_closed_when_capital_scope_is_not_limited() -> None:
    telemetry = _canary_telemetry()
    telemetry["capital_scope"]["capital_scale_pct"] = 8.0

    incident, postmortem, proposal, validation_errors = _run_canary_learn_path(telemetry)
    packet = _build_closed_canary_packet(
        telemetry,
        incident=incident,
        postmortem=postmortem,
        proposal=proposal,
        validation_errors=validation_errors,
    )

    assert packet.assertions.live_capital_scope_limited is False
    assert (
        "assertions.live_capital_scope_limited must be true to close canary packet"
        in packet.validate()
    )


def test_canary_packet_fails_closed_when_operator_gate_is_missing() -> None:
    telemetry = _canary_telemetry()
    telemetry["human_gate"]["operator_approval_ref"] = ""

    incident, postmortem, proposal, validation_errors = _run_canary_learn_path(telemetry)
    packet = _build_closed_canary_packet(
        telemetry,
        incident=incident,
        postmortem=postmortem,
        proposal=proposal,
        validation_errors=validation_errors,
    )

    assert packet.assertions.human_gate_valid is False
    assert (
        "assertions.human_gate_valid must be true to close canary packet"
        in packet.validate()
    )


def _run_canary_learn_path(
    telemetry: dict[str, Any],
) -> tuple[IncidentCase, Postmortem, dict[str, Any], list[str]]:
    store = IncidentStore()
    incident = store.create_incident(_make_incident(telemetry))
    postmortem = store.create_postmortem(_make_postmortem(incident))
    proposal = on_postmortem_published(_postmortem_event(postmortem, incident, telemetry))

    assert proposal is not None
    validation_errors = _validation_errors(incident, postmortem, proposal)
    return incident, postmortem, proposal, validation_errors


def _canary_telemetry() -> dict[str, Any]:
    binding_id = "3d6ebfef-bc0a-489e-a87c-2c71aa74c003"
    event_id = "da918383-1743-4505-b26a-62c2e7ac3003"
    trace_id = "2cf25216-390d-485f-bd32-97716321c003"
    return {
        "event_id": event_id,
        "event_type": "drawdown_snapshot",
        "severity": "high",
        "created_at": "2026-05-19T15:50:00Z",
        "execution_mode": "live",
        "binding_id": binding_id,
        "runtime_binding_id": binding_id,
        "deployment_stage": "canary",
        "deployment_plan_id": "plan-canary-ooda-003-001",
        "plan_id": "plan-canary-ooda-003-001",
        "capital_pool_id": "pool-canary-ooda-003-001",
        "persona_capital_binding_id": "pcb-canary-ooda-003-001",
        "artifact_id": "reg-canary-strategy-001",
        "artifact_version": "2.0.0",
        "runtime_id": "rt-canary-ooda-003-001",
        "trace_id": trace_id,
        "target": {
            "registry_id": "reg-canary-strategy-001",
            "strategy_id": "strategy-canary-ooda-003",
            "artifact_version": "2.0.0",
            "artifact_type": "strategy_spec",
            "promotion_state": "live",
        },
        "metrics": {
            "max_drawdown_pct": 0.18,
            "anomaly_score": 0.92,
        },
        "human_gate": {
            "risk_owner_approval_ref": "approval://risk-owner/canary-ooda-003",
            "operator_approval_ref": "approval://operator/canary-ooda-003",
        },
        "capital_scope": {
            "allowed_deployment_scope": "canary",
            "capital_scale_pct": 2.5,
            "gross_scale_pct": 10.0,
            "production_live_enabled": False,
        },
        "description": "Canary runtime emitted a high-severity drawdown anomaly.",
    }


def _make_incident(telemetry: dict[str, Any]) -> IncidentCase:
    return IncidentCase(
        incident_id="inc-ooda-canary-003-001",
        title="Canary drawdown anomaly",
        status="open",
        severity=telemetry["severity"],
        created_at="2026-05-19T15:51:00Z",
        binding_id=telemetry["binding_id"],
        deployment_stage=telemetry["deployment_stage"],
        deployment_plan_id=telemetry["deployment_plan_id"],
        capital_pool_id=telemetry["capital_pool_id"],
        persona_capital_binding_id=telemetry["persona_capital_binding_id"],
        artifact_id=telemetry["artifact_id"],
        artifact_version=telemetry["artifact_version"],
        runtime_id=telemetry["runtime_id"],
        trace_id=telemetry["trace_id"],
        telemetry_event_ids=[telemetry["event_id"]],
        evidence_summary=telemetry["description"],
    )


def _make_postmortem(incident: IncidentCase) -> Postmortem:
    return Postmortem(
        postmortem_id="pm-ooda-canary-003-001",
        title=f"Postmortem: {incident.incident_id}",
        status="published",
        created_at="2026-05-19T15:52:00Z",
        incident_id=incident.incident_id,
        binding_id=incident.binding_id,
        deployment_stage=incident.deployment_stage,
        deployment_plan_id=incident.deployment_plan_id,
        capital_pool_id=incident.capital_pool_id,
        persona_capital_binding_id=incident.persona_capital_binding_id,
        artifact_id=incident.artifact_id,
        artifact_version=incident.artifact_version,
        runtime_id=incident.runtime_id,
        trace_id=incident.trace_id,
        root_cause="Canary strategy breached the drawdown anomaly threshold.",
        contributing_factors=["market_volatility", "canary_model_drift"],
        action_items=["rollback_to_paper", "review_canary_risk_parameters"],
        author_ids=["evolution-controller"],
        published_at="2026-05-19T15:53:00Z",
    )


def _postmortem_event(
    postmortem: Postmortem,
    incident: IncidentCase,
    telemetry: dict[str, Any],
) -> dict[str, Any]:
    payload = postmortem.to_dict()
    payload["severity"] = incident.severity
    payload["evidence_refs"] = [
        {"ref_type": "telemetry_event", "ref_id": telemetry["event_id"]},
        {"ref_type": "human_gate", "ref_id": _human_gate_ref(telemetry)},
    ]
    return payload


def _validation_errors(
    incident: IncidentCase,
    postmortem: Postmortem,
    proposal: dict[str, Any],
) -> list[str]:
    errors = validate_incident_case(incident) + validate_postmortem(postmortem)
    if proposal.get("proposed_action") != "rollback":
        errors.append("proposal.proposed_action must be rollback for high canary incident")
    if proposal.get("target_deployment_stage") != "canary":
        errors.append("proposal.target_deployment_stage must remain canary")
    return errors


def _build_closed_canary_packet(
    telemetry: dict[str, Any],
    *,
    incident: IncidentCase,
    postmortem: Postmortem,
    proposal: dict[str, Any],
    validation_errors: list[str],
) -> CanaryOodaPacket:
    return CanaryOodaPacket(
        packet_id="canary-ooda-003-telemetry-evolution",
        status=CanaryPacketStatus.CLOSED,
        stages=CanaryOodaStages(
            observe=CanaryObserveStage(
                source_refs=["source://canary-ooda-003/synthetic-anomaly"],
                telemetry_refs=[f"telemetry-event://{telemetry['event_id']}"],
            ),
            orient=CanaryOrientStage(
                strategy_spec_ref=(
                    f"strategy-spec://{telemetry['target']['strategy_id']}@2.0.0"
                ),
                experiment_run_ref="experiment-run://canary-ooda-003-observation",
                drift_report_ref="drift-report://canary-ooda-003/drawdown",
            ),
            decide=CanaryDecideStage(
                approval_decision_ref="approval://canary-ooda-003/risk-owner-operator",
                deployment_plan_ref=f"deployment-plan://{telemetry['deployment_plan_id']}",
                human_gate_ref=_human_gate_ref(telemetry),
            ),
            act=CanaryActStage(
                runtime_binding_ref=f"runtime-binding://{telemetry['binding_id']}",
                canary_runtime_ref=f"runtime://{telemetry['runtime_id']}",
                rollback_drill_ref="rollback-drill://canary-ooda-003/completed",
            ),
            learn=CanaryLearnStage(
                incident_ref=f"incident://{incident.incident_id}",
                postmortem_ref=f"postmortem://{postmortem.postmortem_id}",
                evolution_proposal_ref=(
                    f"evolution-proposal://{proposal['source_postmortem_id']}/rollback"
                ),
            ),
        ),
        assertions=CanaryAssertions(
            live_capital_scope_limited=_live_capital_scope_limited(telemetry),
            rollback_drill_completed=True,
            telemetry_ingested=telemetry["event_id"] in incident.telemetry_event_ids,
            human_gate_valid=_human_gate_valid(telemetry),
            validation_errors_empty=validation_errors == [],
        ),
    )


def _human_gate_ref(telemetry: dict[str, Any]) -> str:
    human_gate = telemetry["human_gate"]
    return (
        "human-gate://"
        f"{human_gate.get('risk_owner_approval_ref', '')}/"
        f"{human_gate.get('operator_approval_ref', '')}"
    )


def _human_gate_valid(telemetry: dict[str, Any]) -> bool:
    human_gate = telemetry.get("human_gate") or {}
    return (
        telemetry.get("deployment_stage") == "canary"
        and bool(human_gate.get("risk_owner_approval_ref"))
        and bool(human_gate.get("operator_approval_ref"))
    )


def _live_capital_scope_limited(telemetry: dict[str, Any]) -> bool:
    scope = telemetry.get("capital_scope") or {}
    capital_scale_pct = float(scope.get("capital_scale_pct") or 0.0)
    gross_scale_pct = float(scope.get("gross_scale_pct") or 0.0)
    return (
        telemetry.get("deployment_stage") == "canary"
        and scope.get("allowed_deployment_scope") == "canary"
        and 0.0 < capital_scale_pct <= CANARY_CAPITAL_SCALE_LIMIT_PCT
        and 0.0 < gross_scale_pct <= CANARY_GROSS_SCALE_LIMIT_PCT
        and scope.get("production_live_enabled") is False
    )
