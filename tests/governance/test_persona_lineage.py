"""Tests for MPO-004-V2 persona lineage bridge into EP5 proof packets."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.ep5_proof.packet_generator import (
    EVIDENCE_KEY_PERSONA_LINEAGE,
    PROOF_FLAG_PERSONA_LINEAGE_LINKED,
    EP5ProofGeneratorError,
    generate_ep5_proof_packet,
)
from services.governance.ep5_proof.persona_lineage import (
    PersonaLineageError,
    build_ep5_persona_lineage,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MPO_PACKET_REF = "support/evidence/MPO-003-V2/full_packet.json"
MPO_PACKET_PATH = REPO_ROOT / MPO_PACKET_REF


_VALID_CANARY_RUN = {
    "run_id": "canary-run-mpo-004",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-twse-canary-mpo-004",
    "environment": "canary",
    "deployment_stage": "canary",
    "runtime_started": True,
    "heartbeat_count": 5,
    "order_route_mode": "paper",
    "telemetry_event_count": 12,
    "result": "pass",
    "started_at": "2026-05-20T10:00:00Z",
    "finished_at": "2026-05-20T10:05:00Z",
}


def _mpo_packet() -> dict:
    return json.loads(MPO_PACKET_PATH.read_text(encoding="utf-8"))


def _conflict_resolution_log() -> dict:
    return _mpo_packet()["sponsor_resolution"]["conflict_resolution_log"]


def _run_with_lineage(**overrides):
    run = dict(_VALID_CANARY_RUN)
    run.update(
        {
            "multi_persona_ooda_packet": _mpo_packet(),
            "multi_persona_packet_ref": MPO_PACKET_REF,
        }
    )
    run.update(overrides)
    return run


def test_builds_ep5_lineage_refs_from_mpo_003_packet() -> None:
    lineage = build_ep5_persona_lineage(
        _mpo_packet(),
        source_packet_ref=MPO_PACKET_REF,
    )

    assert lineage.source_packet_id == "mpo-003-v2-full-e2e-packet"
    assert lineage.source_task_id == "MPO-003-V2"
    assert lineage.sponsor_persona_id == "persona-alpha"
    # support/evidence/MPO-003-V2/full_packet.json is regenerated with fresh
    # random ids by tests/e2e/test_multi_persona_ooda_packet.py, so the
    # expected id must be read from the same fixture rather than pinned.
    assert lineage.conflict_resolution_log_id == _conflict_resolution_log()["log_id"]
    assert lineage.conflict_log_ref == (
        "support/evidence/MPO-003-V2/full_packet.json"
        "#/sponsor_resolution/conflict_resolution_log"
    )
    assert lineage.synthesized_memo_refs == (
        "support/evidence/MPO-003-V2/full_packet.json#/governance_memo",
    )
    # classified_conflict_types is the sorted, deduplicated set of conflict_type
    # values across the fixture's classified_conflicts list; classified_conflict_refs
    # has one entry per item in that list (see persona_lineage.py). Derived rather
    # than pinned for the same reason as the ids above.
    conflicts = _conflict_resolution_log()["classified_conflicts"]
    assert lineage.classified_conflict_types == tuple(sorted({c["conflict_type"] for c in conflicts}))
    assert len(lineage.classified_conflict_refs) == len(conflicts)
    assert lineage.has_open_conflicts is False


def test_ep5_proof_packet_embeds_sponsor_lineage_when_mpo_packet_is_present() -> None:
    packet = generate_ep5_proof_packet(_run_with_lineage())

    assert packet.can_proceed is True
    assert packet.flags.values[PROOF_FLAG_PERSONA_LINEAGE_LINKED] is True
    assert packet.target.metadata["sponsor_persona_id"] == "persona-alpha"
    assert packet.target.metadata["multi_persona_artifact_id"] == (
        _conflict_resolution_log()["artifact_id"]
    )

    lineage = packet.extensions["persona_lineage"]
    assert lineage["sponsor_persona_id"] == "persona-alpha"
    assert lineage["conflict_log_ref"].endswith("#/sponsor_resolution/conflict_resolution_log")
    assert lineage["synthesized_memo_refs"] == [
        "support/evidence/MPO-003-V2/full_packet.json#/governance_memo"
    ]

    evidence = {item.key: item for item in packet.evidence.provided}
    assert evidence[EVIDENCE_KEY_PERSONA_LINEAGE].status == "pass"
    assert evidence[EVIDENCE_KEY_PERSONA_LINEAGE].path == MPO_PACKET_REF
    assert evidence[EVIDENCE_KEY_PERSONA_LINEAGE].metadata["conflict_resolution_log_id"] == (
        _conflict_resolution_log()["log_id"]
    )


def test_sponsor_mismatch_blocks_canary_readiness_fail_closed() -> None:
    packet = generate_ep5_proof_packet(_run_with_lineage(persona_id="persona-beta"))

    assert packet.can_proceed is False
    assert packet.flags.values[PROOF_FLAG_PERSONA_LINEAGE_LINKED] is False
    assert "SPONSOR_PERSONA_MISMATCH" in {reason.code for reason in packet.blocking_reasons}

    evidence = {item.key: item for item in packet.evidence.provided}
    assert evidence[EVIDENCE_KEY_PERSONA_LINEAGE].status == "fail"
    assert EVIDENCE_KEY_PERSONA_LINEAGE in packet.evidence.missing


def test_open_conflicts_in_lineage_block_canary_readiness() -> None:
    packet_data = _mpo_packet()
    conflict_log = packet_data["sponsor_resolution"]["conflict_resolution_log"]
    conflict_log["open_conflicts"] = [
        {
            "conflict_id": "committee-001",
            "summary": "Committee conflict must resolve before canary.",
            "owner": "governance.multi_persona.sponsor_resolver",
        }
    ]
    packet_data["sponsor_resolution"]["has_open_conflicts"] = True

    packet = generate_ep5_proof_packet(
        _run_with_lineage(multi_persona_ooda_packet=packet_data)
    )

    assert packet.can_proceed is False
    assert "PERSONA_LINEAGE_OPEN_CONFLICTS" in {reason.code for reason in packet.blocking_reasons}
    assert packet.extensions["persona_lineage"]["open_conflict_ids"] == ["committee-001"]


def test_missing_governance_memo_ref_is_rejected() -> None:
    packet_data = copy.deepcopy(_mpo_packet())
    packet_data.pop("governance_memo")

    with pytest.raises(PersonaLineageError, match="governance_memo"):
        build_ep5_persona_lineage(packet_data, source_packet_ref=MPO_PACKET_REF)


def test_invalid_lineage_source_in_generator_raises_ep5_error() -> None:
    with pytest.raises(EP5ProofGeneratorError, match="invalid persona lineage"):
        generate_ep5_proof_packet(
            {
                **_VALID_CANARY_RUN,
                "multi_persona_ooda_packet": {"packet_id": "bad-packet"},
            }
        )
