from __future__ import annotations

import copy

import pytest

from services.capital.binding_live.conflict_resolution_log import OPEN_CONFLICT_BLOCKER
from services.capital.binding_live.simulator import (
    BindingLiveActivationSimulationError,
    simulate_binding_live_activation,
    simulate_binding_live_activation_or_raise,
)


NOW = "2026-05-20T12:00:00Z"


def test_simulator_walks_dual_gate_without_live_side_effects() -> None:
    packet = _simulation_packet()
    before = copy.deepcopy(packet)

    result = simulate_binding_live_activation(packet, at=NOW)

    assert result.passed is True
    assert result.can_bind_live is True
    assert result.would_bind_live is True
    assert result.simulation_only is True
    assert result.production_flags_changed is False
    assert result.live_side_effects_attempted is False
    assert result.live_side_effects_executed is False
    assert packet == before
    assert [gate.id for gate in result.gates] == [
        "readiness_packet",
        "side_effect_guard",
        "risk_owner",
        "operator",
        "activation_preview",
    ]
    assert {gate.status for gate in result.gates} == {"passed"}
    assert result.simulated_approvals["risk_owner"]["status"] == "approved"
    assert result.simulated_approvals["risk_owner"]["recorded"] is False
    assert result.simulated_approvals["operator"]["status"] == "approved"
    assert result.simulated_approvals["operator"]["recorded"] is False
    assert result.readiness_preview["approval"] == {
        "risk_owner": "approved",
        "operator": "approved",
    }
    assert result.readiness_preview["result"] == {
        "can_bind_live": True,
        "blocking_reasons": [],
    }
    assert result.dashboard_preview["can_bind_live"] is True
    assert result.to_dict()["production_flags"] == {
        "before": False,
        "after": False,
        "changed": False,
    }


def test_simulator_fails_closed_for_risk_owner_rejection() -> None:
    packet = _simulation_packet(
        readiness=_readiness_packet(
            approval={"risk_owner": "rejected", "operator": "pending"},
            result={
                "can_bind_live": False,
                "blocking_reasons": [
                    "risk_owner_approval_rejected",
                    "operator_approval_pending",
                ],
            },
        )
    )

    result = simulate_binding_live_activation(packet, at=NOW)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert gates["risk_owner"].status == "blocked"
    assert (
        "cannot simulate over explicit risk_owner approval status: rejected"
        in gates["risk_owner"].blocking_reasons
    )
    assert "risk_owner" not in result.simulated_approvals
    assert "operator" not in result.simulated_approvals


def test_simulator_fails_closed_for_open_conflict_after_dual_gate() -> None:
    packet = _simulation_packet(
        conflict_log=_conflict_log_packet(
            open_conflicts=[
                {
                    "conflict_id": "conflict-committee-001",
                    "summary": "Committee has not resolved sponsor dispute.",
                    "owner": "committee-chair",
                    "evidence_ref": "support/evidence/CBL/conflict-committee-001.json",
                }
            ]
        )
    )

    result = simulate_binding_live_activation(packet, at=NOW)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert gates["risk_owner"].status == "passed"
    assert gates["operator"].status == "passed"
    assert gates["activation_preview"].status == "blocked"
    assert result.simulated_approvals["risk_owner"]["recorded"] is False
    assert result.simulated_approvals["operator"]["recorded"] is False
    assert OPEN_CONFLICT_BLOCKER in result.blocking_reasons
    assert result.readiness_preview["result"] == {
        "can_bind_live": False,
        "blocking_reasons": [OPEN_CONFLICT_BLOCKER],
    }
    assert result.dashboard_preview["conflict_log_status"]["open_conflict_ids"] == [
        "conflict-committee-001"
    ]


def test_simulator_refuses_live_side_effect_or_production_flag_requests() -> None:
    packet = _simulation_packet()
    packet["operations"] = {
        "dispatch_runtime_manager_command": True,
        "dry_run": True,
    }

    result = simulate_binding_live_activation(packet, at=NOW)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert result.live_side_effects_attempted is True
    assert result.live_side_effects_executed is False
    assert result.production_flags_changed is False
    assert gates["side_effect_guard"].status == "blocked"
    assert result.issues[0].code == "live_side_effect_requested"
    assert result.issues[0].path == "operations.dispatch_runtime_manager_command"
    assert "risk_owner" not in result.simulated_approvals
    assert "operator" not in result.simulated_approvals


def test_simulator_does_not_override_explicit_operator_rejection() -> None:
    packet = _simulation_packet(
        readiness=_readiness_packet(
            approval={"risk_owner": "pending", "operator": "rejected"},
            result={
                "can_bind_live": False,
                "blocking_reasons": [
                    "risk_owner_approval_pending",
                    "operator_approval_rejected",
                ],
            },
        )
    )

    result = simulate_binding_live_activation(packet, at=NOW)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert gates["operator"].status == "blocked"
    assert (
        "cannot simulate over explicit operator approval status: rejected"
        in gates["operator"].blocking_reasons
    )
    assert "risk_owner" in result.simulated_approvals
    assert "operator" not in result.simulated_approvals
    with pytest.raises(BindingLiveActivationSimulationError, match="operator approval status"):
        simulate_binding_live_activation_or_raise(packet, at=NOW)


def _simulation_packet(**overrides) -> dict:
    packet = {
        "readiness": _readiness_packet(),
        "sponsor_responsibility": _responsibility_packet(),
        "conflict_log": _conflict_log_packet(),
        "lifecycle": _lifecycle_packet(),
    }
    packet.update(overrides)
    return packet


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
            "risk_owner": "pending",
            "operator": "pending",
        },
        "result": {
            "can_bind_live": False,
            "blocking_reasons": [
                "risk_owner_approval_pending",
                "operator_approval_pending",
            ],
        },
    }
    packet.update(overrides)
    return packet


def _responsibility_packet(**overrides):
    packet = {
        "responsibility_id": "sponsor-resp-001",
        "sponsor_persona_id": "persona-alpha",
        "binding_id": "binding-live-001",
        "capital_pool_id": "pool-main",
        "live_owner": {
            "owner_id": "ops-live-owner",
            "role": "live_owner",
            "binding_id": "binding-live-001",
            "mandate_ref": "support/evidence/CBL/sponsor-mandate.json",
            "contact_ref": "ops://live-owner/on-call",
        },
        "escalation_chain": [
            {
                "level": 1,
                "owner_id": "risk-owner-1",
                "role": "risk_owner",
                "trigger": "risk_limit_breach",
                "action": "pause_and_review",
                "evidence_ref": "support/evidence/CBL/risk-escalation.json",
            },
            {
                "level": 2,
                "owner_id": "operator-1",
                "role": "operator",
                "trigger": "live_owner_unavailable",
                "action": "manual_intervention",
                "evidence_ref": "support/evidence/CBL/operator-escalation.json",
            },
        ],
        "policy_refs": [
            "BINDING_AND_DEPLOYMENT_SEMANTICS.md#3.4",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md#11-v1-decisions",
        ],
        "status": "active",
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


def _lifecycle_packet(**overrides):
    packet = {
        "binding_id": "binding-live-001",
        "status": "active",
        "ttl": {
            "issued_at": "2026-05-20T00:00:00Z",
            "ttl_hours": 24,
        },
        "revocation_policy": {
            "revocation_allowed": True,
            "allowed_revoker_roles": ["risk_owner", "operator"],
            "requires_reason": True,
        },
    }
    packet.update(overrides)
    return packet
