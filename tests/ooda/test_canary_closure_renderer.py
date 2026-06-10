from __future__ import annotations

import json

import pytest

from services.ooda.canary_closure_renderer import (
    CanaryClosureRenderError,
    render_canary_closure,
    render_canary_closure_json,
    render_canary_closure_markdown,
    validate_closed_canary_packet,
    write_canary_closure_artifacts,
)
from services.ooda.canary_packet_model import (
    CANARY_OODA_PACKET_SCHEMA_VERSION,
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


def test_renders_closed_canary_packet_as_json_and_markdown() -> None:
    packet = _closed_packet()

    result = render_canary_closure(packet)

    assert result.packet_id == "canary-ooda-005-closure"
    assert result.validation_errors == ()
    assert result.json_payload == packet.to_dict()
    assert json.loads(result.json_text) == packet.to_dict()

    markdown = result.markdown_text
    assert markdown.startswith("# Canary OODA Closure\n")
    assert f"<code>{CANARY_OODA_PACKET_SCHEMA_VERSION}</code>" in markdown
    assert "| Packet ID | <code>canary-ooda-005-closure</code> |" in markdown
    assert (
        "| Act | <code>rollback_drill_ref</code> | "
        "<code>rollback-drill://ep5-007-v2/evidence-005</code> |"
    ) in markdown
    assert (
        "| Learn | <code>evolution_proposal_ref</code> | "
        "<code>evolution-proposal://canary-ooda-005/rollback</code> |"
    ) in markdown
    assert "| <code>human_gate_valid</code> | <code>true</code> |" in markdown
    assert "No validation errors." in markdown


def test_json_and_markdown_helpers_accept_packet_mappings() -> None:
    packet_payload = _closed_packet().to_dict()

    rendered_json = render_canary_closure_json(packet_payload)
    rendered_markdown = render_canary_closure_markdown(packet_payload)

    assert json.loads(rendered_json) == packet_payload
    assert "telemetry-event://canary-ooda-005/high-drawdown" in rendered_markdown
    assert "human-gate://risk-owner/operator/canary-ooda-005" in rendered_markdown


def test_write_canary_closure_artifacts_writes_json_and_markdown(tmp_path) -> None:
    packet = _closed_packet()
    json_path = tmp_path / "closed-canary-packet.json"
    markdown_path = tmp_path / "closed-canary-packet.md"

    result = write_canary_closure_artifacts(
        packet,
        json_path=json_path,
        markdown_path=markdown_path,
    )

    assert json.loads(json_path.read_text(encoding="utf-8")) == packet.to_dict()
    assert markdown_path.read_text(encoding="utf-8") == result.markdown_text


def test_renderer_fails_closed_when_packet_is_not_closed() -> None:
    packet = CanaryOodaPacket(packet_id="canary-ooda-005-open")

    errors = validate_closed_canary_packet(packet)

    assert "canary closure renderer requires status closed" in errors
    with pytest.raises(CanaryClosureRenderError, match="requires status closed"):
        render_canary_closure(packet)


def test_renderer_fails_closed_when_closed_packet_validation_fails() -> None:
    packet = _closed_packet()
    packet.stages.act.rollback_drill_ref = ""
    packet.assertions.rollback_drill_completed = False

    with pytest.raises(CanaryClosureRenderError) as exc_info:
        render_canary_closure(packet)

    assert (
        "closed canary packet requires stages.act.rollback_drill_ref"
        in exc_info.value.errors
    )
    assert (
        "assertions.rollback_drill_completed must be true to close canary packet"
        in exc_info.value.errors
    )


def test_renderer_validates_raw_mapping_before_normalizing() -> None:
    packet_payload = _closed_packet().to_dict()
    packet_payload["stages"]["observe"]["telemetry_refs"] = [123]

    with pytest.raises(CanaryClosureRenderError) as exc_info:
        render_canary_closure(packet_payload)

    assert "stages.observe.telemetry_refs[0] must be a string" in exc_info.value.errors


def _closed_packet() -> CanaryOodaPacket:
    return CanaryOodaPacket(
        packet_id="canary-ooda-005-closure",
        status=CanaryPacketStatus.CLOSED,
        stages=CanaryOodaStages(
            observe=CanaryObserveStage(
                source_refs=["source://canary-ooda-005/reviewer-note"],
                telemetry_refs=["telemetry-event://canary-ooda-005/high-drawdown"],
            ),
            orient=CanaryOrientStage(
                strategy_spec_ref="strategy-spec://canary-ooda-005@2.0.0",
                experiment_run_ref="experiment-run://canary-ooda-005/smoke",
                drift_report_ref="drift-report://canary-ooda-005/drawdown",
            ),
            decide=CanaryDecideStage(
                approval_decision_ref="approval://canary-ooda-005/risk-owner-operator",
                deployment_plan_ref="deployment-plan://canary-ooda-005",
                human_gate_ref="human-gate://risk-owner/operator/canary-ooda-005",
            ),
            act=CanaryActStage(
                runtime_binding_ref="runtime-binding://canary-ooda-005",
                canary_runtime_ref="runtime://canary-ooda-005",
                rollback_drill_ref="rollback-drill://ep5-007-v2/evidence-005",
            ),
            learn=CanaryLearnStage(
                incident_ref="incident://canary-ooda-005/drawdown",
                postmortem_ref="postmortem://canary-ooda-005/drawdown",
                evolution_proposal_ref="evolution-proposal://canary-ooda-005/rollback",
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
