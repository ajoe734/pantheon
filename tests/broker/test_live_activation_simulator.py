from __future__ import annotations

import copy

import pytest

from services.broker.live_activation.simulator import (
    LiveActivationSimulationError,
    simulate_live_activation,
    simulate_live_activation_or_raise,
)
from services.broker.live_activation.validator import EXPECTED_HARD_FAIL_CONDITIONS


def test_simulator_walks_criteria_and_dual_gate_without_live_side_effects() -> None:
    payload = _activation_request()
    before = copy.deepcopy(payload)

    result = simulate_live_activation(payload)

    assert result.passed is True
    assert result.can_activate is True
    assert result.would_activate_live is True
    assert result.simulation_only is True
    assert result.production_flags_changed is False
    assert result.live_side_effects_attempted is False
    assert result.live_side_effects_executed is False
    assert payload == before
    assert [gate.id for gate in result.gates] == [
        "criteria",
        "side_effect_guard",
        "risk_owner",
        "operator",
        "activation_validation",
    ]
    assert {gate.status for gate in result.gates} == {"passed"}
    assert result.simulated_approvals["risk_owner"]["status"] == "approved"
    assert result.simulated_approvals["operator"]["status"] == "approved"
    assert result.simulated_approvals["operator"]["recorded"] is False
    assert result.to_dict()["production_flags"] == {
        "before": False,
        "after": False,
        "changed": False,
    }


def test_simulator_fails_closed_for_risk_owner_gate_blocker() -> None:
    payload = _activation_request()
    payload["risk_owner_review"]["open_conflicts"] = ["sponsor dispute"]

    result = simulate_live_activation(payload)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert gates["risk_owner"].status == "blocked"
    assert "conflict resolution log has open conflicts" in gates["risk_owner"].blocking_reasons
    assert "risk_owner" not in result.simulated_approvals
    assert "operator" not in result.simulated_approvals


def test_simulator_fails_closed_for_operator_gate_blocker() -> None:
    payload = _activation_request()
    payload["operator_review"]["target_stage"] = "canary"

    result = simulate_live_activation(payload)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert gates["risk_owner"].status == "passed"
    assert gates["operator"].status == "blocked"
    assert (
        "runtime binding target stage must be live"
        in gates["operator"].blocking_reasons
    )
    assert "risk_owner" in result.simulated_approvals
    assert "operator" not in result.simulated_approvals


def test_simulator_refuses_live_side_effect_or_production_flag_requests() -> None:
    payload = _activation_request()
    payload["operations"] = {
        "dispatch_runtime_manager_command": True,
        "dry_run": True,
    }

    result = simulate_live_activation(payload)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert result.live_side_effects_attempted is True
    assert result.live_side_effects_executed is False
    assert result.production_flags_changed is False
    assert gates["side_effect_guard"].status == "blocked"
    assert result.issues[0].code == "live_side_effect_requested"
    assert result.issues[0].path == "operations.dispatch_runtime_manager_command"


def test_simulator_does_not_override_explicit_operator_rejection() -> None:
    payload = _activation_request()
    payload["approvals"]["operator"] = {"status": "rejected"}

    result = simulate_live_activation(payload)

    gates = {gate.id: gate for gate in result.gates}
    assert result.passed is False
    assert gates["operator"].status == "blocked"
    assert (
        "cannot simulate over explicit operator approval status: rejected"
        in gates["operator"].blocking_reasons
    )
    assert "operator" not in result.simulated_approvals
    with pytest.raises(LiveActivationSimulationError, match="operator approval status"):
        simulate_live_activation_or_raise(payload)


def _activation_request() -> dict:
    return {
        "evidence": {
            "paper_run_days": 14,
            "canary_run_days": 7,
            "ep4_packet_ref": "support/evidence/EP4/proof-packet.json",
            "ep5_packet_ref": "support/evidence/EP5/proof-packet.json",
            "broker_sandbox_smoke_ref": "support/evidence/BROKER/sandbox-smoke.json",
            "broker_credential_scope_verified": True,
            "kill_switch_demo_ref": "support/evidence/KILL/demo.json",
            "rollback_drill_ref": "support/evidence/ROLLBACK/drill.json",
            "bff_ha_readiness_ref": "support/evidence/BFF/ha-readiness.json",
            "telemetry_readiness_ref": "support/evidence/TEL/readiness.json",
            "audit_retention_ref": "support/evidence/AUDIT/retention.json",
            "first_week_observation_window_ref": "support/evidence/BLA/first-week.json",
        },
        "risk_owner_review": {
            "strategy_artifact_lineage_ref": "lineage://candidate-artifact/live-ready-001",
            "risk_policy_matches_capital_pool_charter": True,
            "risk_policy_capital_pool_charter_ref": "policy://capital-pool/main/risk-charter",
            "risk_thresholds_within_policy": True,
            "risk_thresholds_ref": "support/evidence/RISK/thresholds.json",
            "rollback_target_ref": "artifact://rollback/live-ready-rollback-001",
            "postmortem_path_ref": "support/evidence/INCIDENT/postmortem-path.json",
            "sponsor_persona_responsibility_ref": "persona://sponsor/live-owner-001",
            "conflict_resolution_log_ref": "support/evidence/GOV/conflict-resolution.json",
            "open_conflicts": [],
        },
        "operator_review": {
            "operator_id": "operator://primary/live-operator",
            "operator_authority_ref": "rbac://operator/live-broker-activation",
            "runtime_binding_ref": "runtime-binding://live/shioaji-001",
            "runtime_binding_verified": True,
            "target_stage": "live",
            "deployment_plan_ref": "deployment-plan://live/shioaji-001",
            "deployment_plan_operator_reviewed": True,
            "capital_binding_ref": "capital-binding://live/main",
            "capital_binding_approved": True,
            "broker_live_session_ref": "shioaji://session/live-ready-001",
            "safe_mode_ready": True,
            "postmortem_path_ref": "support/evidence/INCIDENT/postmortem-path.json",
            "no_go_conditions_ref": "support/evidence/BLA/no-go-conditions.json",
            "open_operator_blockers": [],
        },
        "approvals": {
            "risk_owner": {"status": "pending"},
            "operator": {"status": "pending"},
        },
        "conditions": {condition: False for condition in EXPECTED_HARD_FAIL_CONDITIONS},
        "cooldown": {
            "short_term_drift_detected": True,
            "hours_since_short_term_drift": 24,
        },
    }
