from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.capital.binding_live.evidence_collector import (
    CapitalBindingEvidenceCollectionError,
    collect_required_evidence,
)
from services.capital.binding_live.readiness_model import REQUIRED_EVIDENCE_FIELDS


def _write_json(root: Path, ref: str, payload: dict[str, object]) -> None:
    path = root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _readiness_packet(**overrides):
    required_evidence = {
        "persona_mandate_ref": "support/evidence/CBL/persona-mandate.json",
        "sponsor_responsibility_ref": "support/evidence/CBL/sponsor-responsibility.json",
        "conflict_resolution_log_ref": "support/evidence/CBL/conflict-resolution-log.json",
        "pool_risk_policy_ref": "support/evidence/CBL/pool-risk-policy.json",
        "runtime_compatibility_ref": "support/evidence/CBL/runtime-compatibility.json",
        "artifact_approval_ref": "support/evidence/CBL/artifact-approval.json",
        "deployment_plan_ref": "support/evidence/CBL/deployment-plan.json",
        "rollback_target_ref": "support/evidence/CBL/rollback-target.json",
        "telemetry_readiness_ref": "support/evidence/CBL/telemetry-readiness.json",
        "ep5_packet_ref": "support/evidence/EP5/proof-packet.json",
    }
    packet = {
        "readiness_id": "cbl-ready-001",
        "binding_id": "binding-live-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-main",
        "artifact_id": "artifact-reg-001",
        "runtime_id": "runtime-binding-001",
        "deployment_plan_id": "deployment-plan-canary-001",
        "risk_policy_id": "risk-policy-live-001",
        "roles": {
            "sponsor_persona": "persona-alpha",
            "live_owner": "ops-live-owner",
            "risk_owner": "risk-owner-1",
            "operator": "operator-1",
        },
        "required_evidence": required_evidence,
        "controls": {
            "max_budget_pct": 5,
            "ttl_hours": 24,
            "revocation_allowed": True,
            "auto_scale_allowed": False,
            "live_order_allowed": False,
        },
        "approval": {
            "risk_owner": "approved",
            "operator": "approved",
        },
        "result": {
            "can_bind_live": True,
            "blocking_reasons": [],
        },
    }
    packet.update(overrides)
    return packet


def _populate_all_required_evidence(root: Path, packet: dict[str, object]) -> None:
    required = packet["required_evidence"]
    assert isinstance(required, dict)
    for field in REQUIRED_EVIDENCE_FIELDS:
        _write_json(
            root,
            str(required[field]),
            {
                "field": field,
                "status": "present",
                "source_task": "CBL-006-V2",
            },
        )


def test_collect_required_evidence_resolves_all_schema_refs(tmp_path: Path) -> None:
    packet = _readiness_packet()
    _populate_all_required_evidence(tmp_path, packet)

    collection = collect_required_evidence(packet, evidence_root=tmp_path)

    assert collection.complete is True
    assert collection.blocking_reasons == ()
    assert tuple(item.field for item in collection.collected_evidence) == REQUIRED_EVIDENCE_FIELDS
    assert collection.collected_evidence[0].payload["field"] == "persona_mandate_ref"
    assert collection.collected_evidence[0].sha256.startswith("sha256:")
    assert collection.to_dict()["complete"] is True


def test_missing_required_evidence_ref_fails_closed(tmp_path: Path) -> None:
    packet = _readiness_packet()
    _populate_all_required_evidence(tmp_path, packet)
    missing_ref = packet["required_evidence"]["rollback_target_ref"]
    assert isinstance(missing_ref, str)
    (tmp_path / missing_ref).unlink()

    collection = collect_required_evidence(packet, evidence_root=tmp_path)

    assert collection.complete is False
    assert collection.missing_evidence[0].field == "rollback_target_ref"
    assert "rollback_target_ref:missing_evidence_ref" in collection.blocking_reasons
    with pytest.raises(CapitalBindingEvidenceCollectionError, match="failed closed"):
        collection.raise_for_incomplete()


def test_malformed_json_evidence_is_invalid_not_collected(tmp_path: Path) -> None:
    packet = _readiness_packet()
    _populate_all_required_evidence(tmp_path, packet)
    bad_ref = packet["required_evidence"]["telemetry_readiness_ref"]
    assert isinstance(bad_ref, str)
    (tmp_path / bad_ref).write_text("{not-json", encoding="utf-8")

    collection = collect_required_evidence(packet, evidence_root=tmp_path)

    assert collection.complete is False
    assert collection.invalid_evidence[0].field == "telemetry_readiness_ref"
    assert collection.invalid_evidence[0].code == "invalid_json_evidence"


def test_evidence_ref_outside_root_fails_closed(tmp_path: Path) -> None:
    packet = _readiness_packet()
    _populate_all_required_evidence(tmp_path, packet)
    required = dict(packet["required_evidence"])
    required["artifact_approval_ref"] = "../artifact-approval.json"
    packet["required_evidence"] = required

    collection = collect_required_evidence(packet, evidence_root=tmp_path)

    assert collection.complete is False
    assert collection.invalid_evidence[0].field == "artifact_approval_ref"
    assert collection.invalid_evidence[0].code == "evidence_ref_outside_root"
