"""MGMT-OODA-007 unit/integration coverage for OODA packet replay."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


OODA_DIR = Path(__file__).resolve().parent
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

jsonschema = pytest.importorskip("jsonschema")

from jsonl_store import OodaJsonlAppendStore, OodaJsonlStoreError, OodaPacketQuery  # noqa: E402
from ooda_loop_packet import (  # noqa: E402
    LoopEnvironment,
    LoopStatus,
    LoopType,
    OodaLoopPacket,
    OodaLoopStore,
    validate_packet,
)
from stage_transition import assert_complete_replay_path, validate_status_transition  # noqa: E402


SCHEMA_PATH = OODA_DIR / "ooda_loop_packet.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _record_transition(
    store: OodaJsonlAppendStore,
    packet: OodaLoopPacket,
    from_status: LoopStatus,
    to_status: LoopStatus,
    *,
    event: str,
) -> None:
    transition = validate_status_transition(from_status.value, to_status.value).to_dict()
    transition.update(
        {
            "packet_id": packet.packet_id,
            "event": event,
            "transitioned_at": packet.updated_at,
        }
    )
    store.append_stage_transition(packet.packet_id, transition, packet=packet.to_dict())


def test_full_paper_packet_round_trips_through_schema_store_and_replay(tmp_path):
    packet = OodaLoopPacket.create(
        loop_type=LoopType.PAPER_STRATEGY,
        environment=LoopEnvironment.PAPER,
        capital_pool_id="pool-paper-001",
        strategy_id="strategy-rs-003",
        persona_ids=["persona-alpha", "persona-risk"],
    )
    store = OodaJsonlAppendStore(tmp_path / "ooda_loop_packets.jsonl")

    store.append_packet(packet.to_dict())

    packet.add_observe_ref("source_refs", "source://search/rs-003")
    packet.observe.telemetry_refs.append("telemetry://paper/heartbeat-001")
    _record_transition(store, packet, LoopStatus.OPEN, LoopStatus.OBSERVING, event="observe")

    packet.advance(LoopStatus.ORIENTED)
    packet.orient.regime_state_ref = "regime://tw-market/range-bound"
    packet.orient.persona_proposal_refs.append("persona-proposal://alpha/001")
    _record_transition(store, packet, LoopStatus.OBSERVING, LoopStatus.ORIENTED, event="orient")

    packet.advance(LoopStatus.DECIDED)
    packet.decide.approval_decision_id = "approval-paper-001"
    packet.decide.deployment_plan_id = "deployment-plan-paper-001"
    packet.decide.policy_decision_refs.append("policy-decision://paper-gate/001")
    _record_transition(store, packet, LoopStatus.ORIENTED, LoopStatus.DECIDED, event="decide")

    packet.advance(LoopStatus.ACTED)
    packet.act.runtime_binding_id = "runtime-binding-paper-001"
    packet.act.command_receipt_refs.append("command-receipt://deploy/001")
    packet.act.broker_evidence_refs.append("broker-evidence://sandbox/readback-001")
    _record_transition(store, packet, LoopStatus.DECIDED, LoopStatus.ACTED, event="act")

    packet.advance(LoopStatus.EVOLVING)
    packet.learn.telemetry_refs.append("telemetry://paper/post-action-001")
    packet.learn.evolution_followthrough_refs.append("evolution-followthrough://review/001")
    _record_transition(store, packet, LoopStatus.ACTED, LoopStatus.EVOLVING, event="learn")

    packet.advance(LoopStatus.CLOSED)
    _record_transition(store, packet, LoopStatus.EVOLVING, LoopStatus.CLOSED, event="close")

    packet_payload = packet.to_dict()
    assert packet.validate() == []
    assert validate_packet(packet_payload) == []
    jsonschema.validate(packet_payload, _schema())

    replayed = OodaJsonlAppendStore(store.path)
    latest = replayed.get_packet(packet.packet_id)
    transitions = replayed.get_stage_transitions(packet.packet_id)

    assert latest is not None
    assert latest["status"] == "closed"
    assert latest["closed_at"] == packet.closed_at
    assert latest["observe"]["source_refs"] == ["source://search/rs-003"]
    assert latest["decide"]["deployment_plan_id"] == "deployment-plan-paper-001"
    assert latest["act"]["runtime_binding_id"] == "runtime-binding-paper-001"
    assert [item["event"] for item in transitions] == [
        "observe",
        "orient",
        "decide",
        "act",
        "learn",
        "close",
    ]
    assert_complete_replay_path(transitions)

    matched = replayed.list_packets(
        OodaPacketQuery(
            strategy_id="strategy-rs-003",
            persona_id="persona-alpha",
            runtime_binding_id="runtime-binding-paper-001",
            deployment_plan_id="deployment-plan-paper-001",
            limit=1,
        )
    )
    assert [item["packet_id"] for item in matched] == [packet.packet_id]
    assert len(store.path.read_text(encoding="utf-8").splitlines()) == 7


def test_non_live_capital_side_effect_guard_matches_model_and_json_schema():
    packet = OodaLoopPacket.create(
        loop_type=LoopType.PAPER_STRATEGY,
        environment=LoopEnvironment.PAPER,
        strategy_id="strategy-rs-003",
    )
    packet.advance(LoopStatus.OBSERVING)
    packet.advance(LoopStatus.ORIENTED)
    packet.advance(LoopStatus.DECIDED)
    packet.advance(LoopStatus.ACTED)
    packet.act.runtime_binding_id = "runtime-binding-paper-unsafe"
    packet.act.live_capital_side_effects = True

    expected = "act.live_capital_side_effects must be false in dev, paper, sandbox, and canary environments"
    assert expected in packet.validate()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet.to_dict(), _schema())


def test_legacy_packet_store_rejects_closed_packet_without_stage_evidence(tmp_path):
    packet = OodaLoopPacket.create(
        loop_type=LoopType.PAPER_STRATEGY,
        environment=LoopEnvironment.PAPER,
        strategy_id="strategy-rs-003",
    )
    for status in [
        LoopStatus.OBSERVING,
        LoopStatus.ORIENTED,
        LoopStatus.DECIDED,
        LoopStatus.ACTED,
        LoopStatus.CLOSED,
    ]:
        packet.advance(status)

    errors = validate_packet(packet.to_dict())
    assert any("observe bundle" in error for error in errors)
    assert any("orient bundle" in error for error in errors)
    assert any("decide bundle" in error for error in errors)
    assert any("act bundle" in error for error in errors)

    store = OodaLoopStore(str(tmp_path / "legacy_ooda_packets.jsonl"))
    with pytest.raises(ValueError, match="Invalid OodaLoopPacket"):
        store.add(packet)


def test_jsonl_store_rejects_transition_packet_id_mismatch(tmp_path):
    packet = OodaLoopPacket.create(
        loop_type=LoopType.PAPER_STRATEGY,
        environment=LoopEnvironment.PAPER,
        strategy_id="strategy-rs-003",
    )
    store = OodaJsonlAppendStore(tmp_path / "ooda_loop_packets.jsonl")
    store.append_packet(packet.to_dict())

    with pytest.raises(OodaJsonlStoreError, match="transition.packet_id must match packet_id"):
        store.append_stage_transition(
            packet.packet_id,
            {
                "packet_id": "ooda-different-packet",
                "from_status": "open",
                "to_status": "observing",
                "event": "observe",
            },
        )
