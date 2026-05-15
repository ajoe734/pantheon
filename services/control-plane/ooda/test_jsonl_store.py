from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


OODA_DIR = Path(__file__).resolve().parent
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from jsonl_store import OodaJsonlAppendStore, OodaJsonlStoreError, OodaPacketQuery


def _packet(**overrides):
    packet = {
        "packet_id": "ooda-paper-001",
        "loop_type": "paper_strategy",
        "status": "open",
        "environment": "paper",
        "capital_pool_id": "pool-paper",
        "strategy_id": "strategy-rs-003",
        "persona_ids": ["persona-alpha", "persona-risk"],
        "observe": {
            "source_refs": ["source://note-001"],
            "telemetry_refs": [],
            "signal_refs": [],
            "market_refs": [],
            "incident_refs": [],
            "human_feedback_refs": [],
        },
        "orient": {
            "regime_state_ref": None,
            "universe_selection_ref": None,
            "signal_inference_refs": [],
            "allocation_proposal_refs": [],
            "risk_adjudication_ref": None,
            "persona_proposal_refs": [],
            "evidence_bundle_refs": [],
        },
        "decide": {
            "approval_decision_id": None,
            "deployment_plan_id": "deploy-paper-001",
            "evolution_decision_id": None,
            "sponsor_persona_id": None,
            "decision_rationale_ref": None,
            "policy_decision_refs": [],
        },
        "act": {
            "runtime_binding_id": "runtime-paper-001",
            "command_receipt_refs": [],
            "broker_evidence_refs": [],
            "rollback_refs": [],
            "safe_mode_refs": [],
            "live_capital_side_effects": False,
        },
        "learn": {
            "telemetry_refs": [],
            "postmortem_refs": [],
            "evolution_followthrough_refs": [],
            "trainer_refs": [],
            "retrain_refs": [],
            "observation_window": None,
        },
        "audit_refs": [],
        "created_at": "2026-05-15T14:00:00Z",
        "updated_at": "2026-05-15T14:00:00Z",
        "closed_at": None,
    }
    packet.update(overrides)
    return packet


def test_append_packet_replays_latest_snapshot(tmp_path):
    store_path = tmp_path / "ooda.jsonl"
    store = OodaJsonlAppendStore(store_path)

    envelope = store.append_packet(_packet())

    assert envelope["schema_version"] == "ooda_loop_packet_record.v1"
    assert envelope["record_type"] == "packet_snapshot"
    assert envelope["packet_id"] == "ooda-paper-001"
    assert store.get_packet("ooda-paper-001")["status"] == "open"

    replayed = OodaJsonlAppendStore(store_path)
    assert replayed.get_packet("ooda-paper-001")["strategy_id"] == "strategy-rs-003"
    assert len(list(replayed.iter_records())) == 1


def test_stage_transition_is_append_only_and_can_materialize_new_packet_state(tmp_path):
    store_path = tmp_path / "ooda.jsonl"
    store = OodaJsonlAppendStore(store_path)
    store.append_packet(_packet())

    decided_packet = _packet(
        status="decided",
        updated_at="2026-05-15T14:05:00Z",
        decide={
            **_packet()["decide"],
            "approval_decision_id": "approval-001",
        },
    )
    envelope = store.append_stage_transition(
        "ooda-paper-001",
        {
            "from_status": "open",
            "to_status": "decided",
            "stage": "decide",
            "actor_id": "operator-001",
            "transitioned_at": "2026-05-15T14:05:00Z",
        },
        packet=decided_packet,
    )

    assert envelope["record_type"] == "stage_transition"
    assert store.get_packet("ooda-paper-001")["status"] == "decided"
    assert store.get_stage_transitions("ooda-paper-001")[0]["to_status"] == "decided"

    lines = store_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["record_type"] for line in lines] == [
        "packet_snapshot",
        "stage_transition",
    ]


def test_list_packets_filters_by_linkage_and_latest_append_order(tmp_path):
    store = OodaJsonlAppendStore(tmp_path / "ooda.jsonl")
    store.append_packet(_packet(packet_id="ooda-paper-001", status="open", persona_ids=["persona-alpha"]))
    store.append_packet(
        _packet(
            packet_id="ooda-evo-001",
            loop_type="evolution",
            status="evolving",
            strategy_id="strategy-rs-004",
            persona_ids=["persona-evo"],
            decide={**_packet()["decide"], "evolution_decision_id": "evo-001"},
        )
    )

    assert [packet["packet_id"] for packet in store.list_packets(OodaPacketQuery(limit=1))] == ["ooda-evo-001"]
    assert [packet["packet_id"] for packet in store.list_packets(OodaPacketQuery(persona_id="persona-alpha"))] == [
        "ooda-paper-001"
    ]
    assert [packet["packet_id"] for packet in store.list_packets(OodaPacketQuery(evolution_decision_id="evo-001"))] == [
        "ooda-evo-001"
    ]


def test_replay_rejects_malformed_or_unsupported_records(tmp_path):
    store_path = tmp_path / "ooda.jsonl"
    store_path.write_text('{"schema_version":"wrong","record_type":"packet_snapshot"}\n', encoding="utf-8")

    with pytest.raises(OodaJsonlStoreError, match="schema_version"):
        OodaJsonlAppendStore(store_path)
