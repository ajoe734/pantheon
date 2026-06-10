from __future__ import annotations

import pytest

from services.capital.binding_live.conflict_resolution_log import (
    CAPITAL_POOL_MISMATCH_BLOCKER,
    OPEN_CONFLICT_BLOCKER,
    PART_C3_PACKET_VERSION,
    SCHEMA_VERSION,
    ConflictResolutionLog,
    ConflictResolutionLogError,
    apply_conflict_resolution_gate,
    evaluate_conflict_resolution_gate,
    validate_conflict_resolution_gate,
    validate_conflict_resolution_log,
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


def _conflict_log_packet(**overrides):
    packet = {
        "log_id": "conflict-log-001",
        "capital_pool_id": "pool-main",
        "scope_ref": "strategy:alpha/live",
        "timestamp": "2026-05-19T00:00:00Z",
        "proposal_ids": ["proposal-alpha", "proposal-beta"],
        "vetoed_proposals": [
            {
                "proposal_id": "proposal-beta",
                "persona_id": "persona-beta",
                "reason": "pool_risk_policy_veto",
                "detail": "Proposal exceeds pool risk policy.",
            }
        ],
        "weighting_inputs": {
            "proposal-alpha": 0.8,
            "proposal-beta": 0.0,
        },
        "weighting_outputs": {
            "proposal-alpha": 1.0,
            "proposal-beta": 0.0,
        },
        "open_conflicts": [],
        "committee_ref": None,
        "sponsor_persona_id": "persona-alpha",
        "rejected_reason": None,
        "synthesis_method": "weighted_fusion",
    }
    packet.update(overrides)
    return packet


def test_c3_conflict_resolution_log_round_trips_schema_fields() -> None:
    log = ConflictResolutionLog.from_dict(_conflict_log_packet())

    assert log.schema_version == SCHEMA_VERSION
    assert log.packet_version == PART_C3_PACKET_VERSION
    assert log.proposal_ids == ("proposal-alpha", "proposal-beta")
    assert log.vetoed_proposals[0].proposal_id == "proposal-beta"
    assert log.weighting_inputs == {"proposal-alpha": 0.8, "proposal-beta": 0.0}
    assert log.weighting_outputs == {"proposal-alpha": 1.0, "proposal-beta": 0.0}
    assert log.sponsor_persona_id == "persona-alpha"
    assert log.open_conflict_ids == ()

    encoded = log.to_dict()
    assert encoded["open_conflicts"] == []
    assert encoded["vetoed_proposals"][0]["reason"] == "pool_risk_policy_veto"
    assert validate_conflict_resolution_log(encoded).log_id == "conflict-log-001"


def test_clean_conflict_log_allows_readiness_gate_to_pass() -> None:
    result = apply_conflict_resolution_gate(_readiness_packet(), _conflict_log_packet())
    gate = validate_conflict_resolution_gate(_readiness_packet(), _conflict_log_packet())

    assert result.can_bind_live is True
    assert result.blocking_reasons == ()
    assert gate.can_bind_live is True
    assert gate.blocking_reasons == ()


def test_open_conflicts_fail_closed_for_live_binding() -> None:
    log = _conflict_log_packet(
        open_conflicts=[
            {
                "conflict_id": "conflict-committee-001",
                "summary": "Committee has not resolved sponsor dispute.",
                "owner": "committee-chair",
                "evidence_ref": "support/evidence/CBL/conflict-committee-001.json",
            }
        ]
    )

    gate = evaluate_conflict_resolution_gate(log)
    result = apply_conflict_resolution_gate(_readiness_packet(), log)

    assert gate.can_bind_live is False
    assert gate.blocking_reasons == (OPEN_CONFLICT_BLOCKER,)
    assert gate.open_conflict_ids == ("conflict-committee-001",)
    assert result.can_bind_live is False
    assert result.blocking_reasons == (OPEN_CONFLICT_BLOCKER,)

    with pytest.raises(ConflictResolutionLogError, match="open conflicts: conflict-committee-001"):
        validate_conflict_resolution_gate(_readiness_packet(), log)


def test_closed_readiness_must_include_conflict_gate_blocker() -> None:
    readiness = _readiness_packet(
        result={
            "can_bind_live": False,
            "blocking_reasons": ["manual_hold"],
        },
    )
    log = _conflict_log_packet(open_conflicts=["conflict-unresolved-sponsor"])

    with pytest.raises(ConflictResolutionLogError, match=OPEN_CONFLICT_BLOCKER):
        validate_conflict_resolution_gate(readiness, log)


def test_readiness_and_conflict_log_must_target_same_capital_pool() -> None:
    log = _conflict_log_packet(capital_pool_id="pool-other")

    result = apply_conflict_resolution_gate(_readiness_packet(), log)

    assert result.can_bind_live is False
    assert result.blocking_reasons == (CAPITAL_POOL_MISMATCH_BLOCKER,)

    with pytest.raises(ConflictResolutionLogError, match=CAPITAL_POOL_MISMATCH_BLOCKER):
        validate_conflict_resolution_gate(_readiness_packet(), log)
