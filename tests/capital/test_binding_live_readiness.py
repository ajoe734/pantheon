from __future__ import annotations

import pytest

from services.capital.binding_live.readiness_model import (
    PART_C2_PACKET_VERSION,
    REQUIRED_EVIDENCE_FIELDS,
    SCHEMA_VERSION,
    CapitalBindingLiveReadiness,
    CapitalBindingLiveReadinessError,
    validate_readiness,
)


def _readiness_packet(**overrides):
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
        "required_evidence": {
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
        },
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


def test_c2_readiness_packet_round_trips_schema_subtrees() -> None:
    packet = CapitalBindingLiveReadiness.from_dict(_readiness_packet())

    assert packet.schema_version == SCHEMA_VERSION
    assert packet.packet_version == PART_C2_PACKET_VERSION
    assert packet.roles.sponsor_persona == "persona-alpha"
    assert packet.controls.revocation_allowed is True
    assert packet.controls.auto_scale_allowed is False
    assert packet.controls.live_order_allowed is False
    assert packet.result.can_bind_live is True

    encoded = packet.to_dict()
    assert set(encoded) >= {
        "readiness_id",
        "binding_id",
        "roles",
        "required_evidence",
        "controls",
        "approval",
        "result",
    }
    assert tuple(encoded["required_evidence"]) == REQUIRED_EVIDENCE_FIELDS
    assert encoded["approval"] == {"risk_owner": "approved", "operator": "approved"}
    assert encoded["result"] == {"can_bind_live": True, "blocking_reasons": []}
    assert validate_readiness(encoded).readiness_id == "cbl-ready-001"


def test_pending_approval_round_trips_as_fail_closed_result() -> None:
    packet = _readiness_packet(
        approval={"risk_owner": "approved", "operator": "pending"},
        result={
            "can_bind_live": False,
            "blocking_reasons": ["operator_approval_pending"],
        },
    )

    readiness = validate_readiness(packet)

    assert readiness.result.can_bind_live is False
    assert readiness.approval.blocking_reasons() == ("operator_approval_pending",)


def test_can_bind_live_fails_closed_without_required_approval() -> None:
    packet = _readiness_packet(approval={"risk_owner": "approved", "operator": "pending"})

    with pytest.raises(CapitalBindingLiveReadinessError, match="approved risk_owner and operator"):
        CapitalBindingLiveReadiness.from_dict(packet)


def test_closed_result_must_name_pending_approval_blocker() -> None:
    packet = _readiness_packet(
        approval={"risk_owner": "approved", "operator": "pending"},
        result={
            "can_bind_live": False,
            "blocking_reasons": ["manual_hold"],
        },
    )

    with pytest.raises(CapitalBindingLiveReadinessError, match="approval blockers"):
        CapitalBindingLiveReadiness.from_dict(packet)


def test_can_bind_live_fails_closed_when_live_order_control_enabled() -> None:
    controls = dict(_readiness_packet()["controls"])
    controls["live_order_allowed"] = True

    with pytest.raises(CapitalBindingLiveReadinessError, match="fail-closed controls"):
        CapitalBindingLiveReadiness.from_dict(_readiness_packet(controls=controls))


def test_missing_required_evidence_ref_is_structural_error() -> None:
    required_evidence = dict(_readiness_packet()["required_evidence"])
    required_evidence["rollback_target_ref"] = ""

    with pytest.raises(CapitalBindingLiveReadinessError, match="rollback_target_ref"):
        CapitalBindingLiveReadiness.from_dict(_readiness_packet(required_evidence=required_evidence))
