from __future__ import annotations

from services.ooda.canary_packet_model import (
    CANARY_ENVIRONMENT,
    CANARY_LOOP_TYPE,
    CanaryActStage,
    CanaryAssertions,
    CanaryDecideStage,
    CanaryLearnStage,
    CanaryObserveStage,
    CanaryOodaPacket,
    CanaryOodaStages,
    CanaryOrientStage,
    CanaryPacketStatus,
    canary_ooda_packet_schema,
    validate_canary_packet,
)


def test_closed_canary_packet_matches_g2_shape_and_validates() -> None:
    packet = _closed_packet()

    payload = packet.to_dict()

    assert payload["loop_type"] == CANARY_LOOP_TYPE
    assert payload["environment"] == CANARY_ENVIRONMENT
    assert payload["status"] == "closed"
    assert set(payload["stages"]) == {"observe", "orient", "decide", "act", "learn"}
    assert payload["stages"]["observe"]["source_refs"] == ["source://canary-alpha-note"]
    assert payload["stages"]["orient"]["drift_report_ref"] is None
    assert payload["stages"]["act"]["rollback_drill_ref"] == "rollback-drill://ep5-007/smoke"
    assert packet.act.rollback_drill_ref == "rollback-drill://ep5-007/smoke"
    assert validate_canary_packet(payload) == []
    assert packet.validate() == []


def test_closed_packet_fails_closed_when_live_capital_scope_not_limited() -> None:
    packet = _closed_packet()
    packet.assertions.live_capital_scope_limited = False

    errors = packet.validate()

    assert "assertions.live_capital_scope_limited must be true to close canary packet" in errors


def test_closed_packet_requires_human_gate_and_rollback_drill_refs() -> None:
    packet = _closed_packet()
    packet.stages.decide.human_gate_ref = ""
    packet.stages.act.rollback_drill_ref = ""

    errors = packet.validate()

    assert "closed canary packet requires stages.decide.human_gate_ref" in errors
    assert "closed canary packet requires stages.act.rollback_drill_ref" in errors


def test_open_packet_can_be_incomplete_but_still_type_checked() -> None:
    packet = CanaryOodaPacket(packet_id="canary-ooda-open-001")

    assert packet.to_dict()["stages"]["observe"]["source_refs"] == []
    assert packet.validate() == []

    payload = packet.to_dict()
    payload["stages"]["observe"]["telemetry_refs"] = [123]

    assert "stages.observe.telemetry_refs[0] must be a string" in validate_canary_packet(payload)


def test_schema_constant_carries_g2_enums_and_required_assertions() -> None:
    schema = canary_ooda_packet_schema()

    assert schema["properties"]["loop_type"]["const"] == "canary_strategy"
    assert schema["properties"]["environment"]["const"] == "canary"
    assert schema["properties"]["status"]["enum"] == ["open", "closed", "failed"]
    assert set(schema["properties"]["assertions"]["required"]) == {
        "live_capital_scope_limited",
        "rollback_drill_completed",
        "telemetry_ingested",
        "human_gate_valid",
        "validation_errors_empty",
    }


def test_from_dict_round_trips_nested_stages() -> None:
    packet = CanaryOodaPacket.from_dict(_closed_packet().to_dict())

    assert packet.status == CanaryPacketStatus.CLOSED.value
    assert packet.observe.telemetry_refs == ["telemetry://canary-runtime/heartbeat"]
    assert packet.learn.evolution_proposal_ref == "evolution-proposal://canary/postmortem"
    assert packet.validate() == []


def _closed_packet() -> CanaryOodaPacket:
    return CanaryOodaPacket(
        packet_id="canary-ooda-001",
        status=CanaryPacketStatus.CLOSED,
        stages=CanaryOodaStages(
            observe=CanaryObserveStage(
                source_refs=["source://canary-alpha-note"],
                telemetry_refs=["telemetry://canary-runtime/heartbeat"],
            ),
            orient=CanaryOrientStage(
                strategy_spec_ref="strategy-spec://canary-alpha@1",
                experiment_run_ref="experiment-run://canary-alpha-smoke",
                drift_report_ref=None,
            ),
            decide=CanaryDecideStage(
                approval_decision_ref="approval://canary-alpha",
                deployment_plan_ref="deployment-plan://canary-alpha",
                human_gate_ref="human-gate://risk-owner/operator/canary-alpha",
            ),
            act=CanaryActStage(
                runtime_binding_ref="runtime-binding://canary-alpha",
                canary_runtime_ref="runtime://canary-alpha",
                rollback_drill_ref="rollback-drill://ep5-007/smoke",
            ),
            learn=CanaryLearnStage(
                incident_ref="incident://canary-alpha/anomaly",
                postmortem_ref="postmortem://canary-alpha/anomaly",
                evolution_proposal_ref="evolution-proposal://canary/postmortem",
            ),
        ),
        assertions=CanaryAssertions(
            live_capital_scope_limited=True,
            rollback_drill_completed=True,
            telemetry_ingested=True,
            human_gate_valid=True,
            validation_errors_empty=True,
        ),
    )
