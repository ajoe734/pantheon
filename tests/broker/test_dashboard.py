from __future__ import annotations

from services.broker.live_activation.dashboard import (
    DASHBOARD_SOURCE,
    build_broker_go_no_go_dashboard,
)
from services.broker.live_activation.validator import EXPECTED_HARD_FAIL_CONDITIONS


def test_dashboard_matches_2026_05_19_go_no_go_shape() -> None:
    dashboard = build_broker_go_no_go_dashboard(_activation_request())

    assert dashboard.version == "1.0"
    assert dashboard.source == DASHBOARD_SOURCE
    assert dashboard.readiness_state == "go"
    assert dashboard.can_activate is True
    assert [gate.id for gate in dashboard.gates] == [
        "activation_criteria",
        "risk_owner_checklist",
        "operator_checklist",
    ]
    assert dashboard.progress.ready_gates == 3
    assert dashboard.progress.total_gates == 3
    assert dashboard.progress.ready_items == 21
    assert dashboard.progress.total_items == 21

    payload = dashboard.to_dict()
    assert payload["passed"] is True
    assert payload["activation_criteria"]["can_activate"] is True
    assert payload["risk_owner_checklist"]["can_sign_off"] is True
    assert payload["operator_checklist"]["can_sign_off"] is True


def test_dashboard_happy_path_passes_without_live_side_effects() -> None:
    request = _activation_request()

    dashboard = build_broker_go_no_go_dashboard(request)

    assert dashboard.can_activate is True
    assert dashboard.blocking_reasons == ()
    assert {gate.status for gate in dashboard.gates} == {"ready"}
    assert request["approvals"]["operator"]["status"] == "approved"


def test_dashboard_fails_closed_without_operator_approval() -> None:
    payload = _activation_request()
    payload["approvals"]["operator"]["status"] = "pending"

    dashboard = build_broker_go_no_go_dashboard(payload)

    assert dashboard.can_activate is False
    assert dashboard.readiness_state == "no_go"
    criteria_gate = dashboard.gates[0]
    assert criteria_gate.id == "activation_criteria"
    assert criteria_gate.status == "blocked"
    assert "operator approval must be one of" in criteria_gate.blocking_reasons[0]
    assert "operator approval must be one of" in dashboard.blocking_reasons[0]


def test_dashboard_surfaces_checklist_progress_and_blocking_reasons() -> None:
    payload = _activation_request()
    payload["evidence"]["canary_run_days"] = 3
    payload["risk_owner_review"]["open_conflicts"] = ["sponsor dispute"]

    dashboard = build_broker_go_no_go_dashboard(payload)

    risk_owner_gate = dashboard.gates[1]
    assert risk_owner_gate.id == "risk_owner_checklist"
    assert risk_owner_gate.status == "blocked"
    assert risk_owner_gate.ready_items == 8
    assert risk_owner_gate.blocked_items == 2
    assert "canary_run_days must be >= 7" in risk_owner_gate.blocking_reasons
    assert "conflict resolution log has open conflicts" in risk_owner_gate.blocking_reasons
    assert dashboard.progress.blocked_gates == 2
    assert dashboard.progress.blocked_items == 3


def test_dashboard_fails_closed_for_active_hard_fail_condition() -> None:
    payload = _activation_request()
    payload["conditions"]["telemetry_unavailable"] = True

    dashboard = build_broker_go_no_go_dashboard(payload)

    assert dashboard.can_activate is False
    assert dashboard.readiness_state == "no_go"
    assert "hard fail condition active: telemetry_unavailable" in dashboard.blocking_reasons
    assert dashboard.gates[0].status == "blocked"
    assert dashboard.gates[1].status == "blocked"
    assert dashboard.gates[2].status == "blocked"


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
            "risk_owner": {
                "status": "approved",
                "approval_ref": "approval://risk-owner/live-001",
            },
            "operator": {
                "status": "approved",
                "approval_ref": "approval://operator/live-001",
            },
        },
        "conditions": {condition: False for condition in EXPECTED_HARD_FAIL_CONDITIONS},
        "cooldown": {
            "short_term_drift_detected": True,
            "hours_since_short_term_drift": 24,
        },
    }
