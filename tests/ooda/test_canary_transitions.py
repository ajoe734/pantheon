from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from services.ooda.canary_packet_model import (
    CANARY_ENVIRONMENT,
    CanaryActStage,
    CanaryAssertions,
    CanaryDecideStage,
    CanaryLearnStage,
    CanaryObserveStage,
    CanaryOodaPacket,
    CanaryOrientStage,
    CanaryPacketStatus,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
OODA_DIR = REPO_ROOT / "services" / "control-plane" / "ooda"
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from stage_transition import (  # noqa: E402
    OodaTransitionError,
    apply_stage_event,
    assert_complete_replay_path,
)


TransitionWriter = Callable[[CanaryOodaPacket], None]


def test_canary_stage_events_replay_to_closed_packet() -> None:
    packet = CanaryOodaPacket(packet_id="canary-ooda-transition-001")
    runtime_state = _runtime_state(packet.packet_id)
    transitions = []

    for step in _happy_path_steps():
        transition = apply_stage_event(
            runtime_state,
            step["event"],
            transitioned_at=step["transitioned_at"],
        )
        transitions.append(transition.to_dict())
        step["write"](packet)

        assert runtime_state["status"] == step["to_status"]
        assert packet.validate() == []

    close_transition = apply_stage_event(
        runtime_state,
        "close",
        transitioned_at="2026-05-19T15:05:00Z",
    )
    transitions.append(close_transition.to_dict())
    packet.status = CanaryPacketStatus.CLOSED
    packet.assertions = CanaryAssertions(
        live_capital_scope_limited=True,
        rollback_drill_completed=True,
        telemetry_ingested=True,
        human_gate_valid=True,
        validation_errors_empty=True,
    )

    assert [item["event"] for item in transitions] == [
        "observe",
        "orient",
        "decide",
        "act",
        "learn",
        "close",
    ]
    assert runtime_state["environment"] == CANARY_ENVIRONMENT
    assert runtime_state["status"] == "closed"
    assert runtime_state["closed_at"] == "2026-05-19T15:05:00Z"
    assert packet.learn.evolution_proposal_ref == "evolution-proposal://canary/postmortem"
    assert_complete_replay_path(transitions)
    assert packet.validate() == []


def test_canary_transition_rejects_skipped_stage() -> None:
    runtime_state = _runtime_state("canary-ooda-transition-skip")
    apply_stage_event(
        runtime_state,
        "observe",
        transitioned_at="2026-05-19T15:00:00Z",
    )

    with pytest.raises(OodaTransitionError, match="Forbidden OODA status transition: observing -> decided"):
        apply_stage_event(
            runtime_state,
            "decide",
            transitioned_at="2026-05-19T15:02:00Z",
        )

    assert runtime_state["status"] == "observing"
    assert runtime_state["closed_at"] is None


def test_canary_close_fails_without_human_gate_and_rollback_drill() -> None:
    packet = CanaryOodaPacket(packet_id="canary-ooda-transition-fail-closed")
    runtime_state = _runtime_state(packet.packet_id)

    for step in _happy_path_steps(include_human_gate=False, include_rollback_drill=False):
        apply_stage_event(
            runtime_state,
            step["event"],
            transitioned_at=step["transitioned_at"],
        )
        step["write"](packet)

    apply_stage_event(runtime_state, "close", transitioned_at="2026-05-19T15:05:00Z")
    packet.status = CanaryPacketStatus.CLOSED
    packet.assertions = CanaryAssertions(
        live_capital_scope_limited=True,
        rollback_drill_completed=False,
        telemetry_ingested=True,
        human_gate_valid=False,
        validation_errors_empty=True,
    )

    errors = packet.validate()

    assert runtime_state["status"] == "closed"
    assert "closed canary packet requires stages.decide.human_gate_ref" in errors
    assert "closed canary packet requires stages.act.rollback_drill_ref" in errors
    assert "assertions.rollback_drill_completed must be true to close canary packet" in errors
    assert "assertions.human_gate_valid must be true to close canary packet" in errors


def _runtime_state(packet_id: str) -> dict[str, object]:
    return {
        "packet_id": packet_id,
        "status": "open",
        "environment": CANARY_ENVIRONMENT,
        "updated_at": "2026-05-19T14:59:00Z",
        "closed_at": None,
        "act": {"live_capital_side_effects": False},
    }


def _happy_path_steps(
    *,
    include_human_gate: bool = True,
    include_rollback_drill: bool = True,
) -> list[dict[str, object]]:
    return [
        {
            "event": "observe",
            "to_status": "observing",
            "transitioned_at": "2026-05-19T15:00:00Z",
            "write": _write_observe,
        },
        {
            "event": "orient",
            "to_status": "oriented",
            "transitioned_at": "2026-05-19T15:01:00Z",
            "write": _write_orient,
        },
        {
            "event": "decide",
            "to_status": "decided",
            "transitioned_at": "2026-05-19T15:02:00Z",
            "write": _write_decide(include_human_gate=include_human_gate),
        },
        {
            "event": "act",
            "to_status": "acted",
            "transitioned_at": "2026-05-19T15:03:00Z",
            "write": _write_act(include_rollback_drill=include_rollback_drill),
        },
        {
            "event": "learn",
            "to_status": "evolving",
            "transitioned_at": "2026-05-19T15:04:00Z",
            "write": _write_learn,
        },
    ]


def _write_observe(packet: CanaryOodaPacket) -> None:
    packet.stages.observe = CanaryObserveStage(
        source_refs=["source://canary-alpha-note"],
        telemetry_refs=["telemetry://canary-runtime/heartbeat"],
    )


def _write_orient(packet: CanaryOodaPacket) -> None:
    packet.stages.orient = CanaryOrientStage(
        strategy_spec_ref="strategy-spec://canary-alpha@1",
        experiment_run_ref="experiment-run://canary-alpha-smoke",
        drift_report_ref="drift-report://canary-alpha/session-001",
    )


def _write_decide(*, include_human_gate: bool) -> TransitionWriter:
    def write(packet: CanaryOodaPacket) -> None:
        packet.stages.decide = CanaryDecideStage(
            approval_decision_ref="approval://canary-alpha",
            deployment_plan_ref="deployment-plan://canary-alpha",
            human_gate_ref=(
                "human-gate://risk-owner/operator/canary-alpha"
                if include_human_gate
                else ""
            ),
        )

    return write


def _write_act(*, include_rollback_drill: bool) -> TransitionWriter:
    def write(packet: CanaryOodaPacket) -> None:
        packet.stages.act = CanaryActStage(
            runtime_binding_ref="runtime-binding://canary-alpha",
            canary_runtime_ref="runtime://canary-alpha",
            rollback_drill_ref=(
                "rollback-drill://ep5-007/smoke"
                if include_rollback_drill
                else ""
            ),
        )

    return write


def _write_learn(packet: CanaryOodaPacket) -> None:
    packet.stages.learn = CanaryLearnStage(
        incident_ref="incident://canary-alpha/anomaly",
        postmortem_ref="postmortem://canary-alpha/anomaly",
        evolution_proposal_ref="evolution-proposal://canary/postmortem",
    )
