from __future__ import annotations

import pytest

from services.governance.promotion_readiness.packet_model import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    PromotionReadinessPacket,
    PromotionReadinessPacketError,
    validate_packet,
)


def _structured_packet(**overrides):
    packet = {
        "packet_id": "prp-ep5-canary-001",
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-05-19T15:00:00Z",
        "generated_by": "Codex / EP5-001-V2",
        "source_task_id": "EP5-001-V2",
        "target": {
            "target_type": "deployment",
            "target_id": "m7-canary-deployment",
            "environment": "canary",
            "deployment_stage": "canary",
        },
        "depends_on_tasks": ["MGMT-BROKER-002", "MGMT-BROKER-006"],
        "evidence": {
            "required": ["broker_sandbox_smoke", "rollback_drill"],
            "provided": [
                {
                    "key": "broker_sandbox_smoke",
                    "status": "passed",
                    "source_task_id": "MGMT-BROKER-002",
                    "path": "support/evidence/MGMT-BROKER-002/summary.json",
                    "ref_type": "broker_sandbox_smoke_summary",
                },
                {
                    "key": "rollback_drill",
                    "status": "passed",
                    "source_task_id": "EP5-007-V2",
                    "path": "support/evidence/EP5-007-V2/rollback-drill.json",
                    "ref_type": "rollback_drill_evidence",
                },
            ],
            "missing": [],
            "gate_results": [
                {
                    "gate": "broker_sandbox_smoke",
                    "status": "passed",
                    "source_ref": "support/evidence/MGMT-BROKER-002/summary.json",
                },
                {
                    "gate": "rollback_drill",
                    "status": "passed",
                    "source_ref": "support/evidence/EP5-007-V2/rollback-drill.json",
                },
            ],
        },
        "approval": {
            "risk_owner": {
                "required": True,
                "recorded": True,
                "state": "recorded",
                "record_id": "risk-owner-signoff-001",
                "actor_id": "risk-owner",
                "evidence_hash": "sha256:risk-owner",
                "signed_at": "2026-05-19T15:01:00Z",
            },
            "operator": {
                "required": True,
                "recorded": True,
                "state": "recorded",
                "record_id": "operator-signoff-001",
                "actor_id": "operator",
                "evidence_hash": "sha256:operator",
                "signed_at": "2026-05-19T15:02:00Z",
            },
            "records": [
                {
                    "role": "risk_owner",
                    "record_id": "risk-owner-signoff-001",
                    "actor_id": "risk-owner",
                    "evidence_hash": "sha256:risk-owner",
                    "signed_at": "2026-05-19T15:01:00Z",
                },
                {
                    "role": "operator",
                    "record_id": "operator-signoff-001",
                    "actor_id": "operator",
                    "evidence_hash": "sha256:operator",
                    "signed_at": "2026-05-19T15:02:00Z",
                },
            ],
        },
        "flags": {
            "BROKER_PRODUCTION_LIVE_ENABLED": False,
            "CAPITAL_BINDING_LIVE_ENABLED": False,
            "live_capital_side_effects": False,
        },
        "blocking_reasons": [],
        "can_proceed": True,
        "reason": "All required evidence and human approvals are recorded; live/capital flags remain fail-closed.",
    }
    packet.update(overrides)
    return packet


def test_structured_packet_round_trips_a2_1_subtrees():
    packet = PromotionReadinessPacket.from_dict(_structured_packet())

    assert packet.schema_version == SCHEMA_VERSION
    assert packet.target.target_id == "m7-canary-deployment"
    assert packet.evidence.required == ("broker_sandbox_smoke", "rollback_drill")
    assert packet.approval.risk_owner.recorded is True
    assert packet.approval.operator.recorded is True
    assert packet.flags.unsafe_true_flags() == ()
    assert packet.can_proceed is True

    encoded = packet.to_dict()
    assert set(encoded) >= {"target", "evidence", "approval", "flags", "blocking_reasons"}
    assert encoded["evidence"]["provided"][0]["key"] == "broker_sandbox_smoke"
    assert encoded["approval"]["records"][1]["role"] == "operator"
    assert encoded["blocking_reasons"] == []
    validate_packet(packet)


def test_legacy_flat_packet_normalizes_to_subtrees():
    legacy = {
        "packet_id": "prp-m7-canary-closeout-20260517",
        "schema_version": LEGACY_SCHEMA_VERSION,
        "target_type": "deployment",
        "target_id": "m7-canary-deployment",
        "environment": "canary",
        "generated_at": "2026-05-17T10:44:16Z",
        "generated_by": "Claude / M7-CANARY-CLOSEOUT",
        "source_task_id": "M7-CANARY-CLOSEOUT",
        "depends_on_tasks": ["MGMT-BROKER-002"],
        "required_evidence": ["broker_sandbox_smoke_consumed"],
        "provided_evidence": [
            {
                "key": "broker_sandbox_smoke_consumed",
                "source_task_id": "MGMT-BROKER-002",
                "path": "support/evidence/MGMT-BROKER-002/summary.json",
                "ref_type": "broker_sandbox_smoke_summary",
                "status": "passed",
            }
        ],
        "missing_evidence": [],
        "gate_results": [
            {
                "gate": "risk_owner_approval",
                "status": "pending",
                "source_ref": "support/evidence/M7-CANARY-CLOSEOUT/risk_owner_approval_template.md",
            }
        ],
        "risk_owner_required": True,
        "risk_owner_approval_recorded": False,
        "operator_required": True,
        "operator_approval_recorded": False,
        "can_proceed": False,
        "reason": "Awaiting risk-owner and operator approvals.",
        "fail_closed_assertions": {
            "BROKER_PRODUCTION_LIVE_ENABLED": False,
            "CAPITAL_BINDING_LIVE_ENABLED": False,
        },
    }

    packet = PromotionReadinessPacket.from_dict(legacy)

    assert packet.target.target_type == "deployment"
    assert packet.evidence.provided[0].status == "passed"
    assert packet.approval.risk_owner.required is True
    assert packet.approval.risk_owner.recorded is False
    assert packet.approval.risk_owner.source_ref.endswith("risk_owner_approval_template.md")
    assert packet.flags.to_dict()["BROKER_PRODUCTION_LIVE_ENABLED"] is False
    assert packet.to_legacy_dict()["risk_owner_required"] is True


def test_fail_closed_when_can_proceed_without_required_approval():
    approval = dict(_structured_packet()["approval"])
    approval["risk_owner"] = {
        "required": True,
        "recorded": False,
        "state": "pending",
    }

    with pytest.raises(PromotionReadinessPacketError, match="required approvals"):
        PromotionReadinessPacket.from_dict(_structured_packet(approval=approval))


def test_fail_closed_when_provided_required_evidence_status_blocks():
    evidence = dict(_structured_packet()["evidence"])
    evidence["provided"] = [dict(item) for item in evidence["provided"]]
    evidence["provided"][0]["status"] = "failed"

    with pytest.raises(PromotionReadinessPacketError, match="provided evidence statuses"):
        PromotionReadinessPacket.from_dict(_structured_packet(evidence=evidence))


def test_fail_closed_when_live_or_capital_flag_is_enabled():
    flags = {
        "BROKER_PRODUCTION_LIVE_ENABLED": True,
        "CAPITAL_BINDING_LIVE_ENABLED": False,
        "live_capital_side_effects": False,
    }

    with pytest.raises(PromotionReadinessPacketError, match="fail-closed"):
        PromotionReadinessPacket.from_dict(_structured_packet(flags=flags))
