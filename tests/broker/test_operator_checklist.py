from __future__ import annotations

from services.broker.live_activation.operator_checklist import (
    CHECKLIST_SOURCE,
    EXPECTED_OPERATOR_CHECKLIST_ITEMS,
    generate_operator_checklist,
)
from services.broker.live_activation.validator import EXPECTED_HARD_FAIL_CONDITIONS


def test_operator_checklist_matches_2026_05_19_b4_shape() -> None:
    checklist = generate_operator_checklist(_activation_request())

    assert checklist.version == "1.0"
    assert checklist.source == CHECKLIST_SOURCE
    assert tuple(item.text for item in checklist.items) == EXPECTED_OPERATOR_CHECKLIST_ITEMS
    assert [item.id for item in checklist.items] == [
        "operator_b4_01",
        "operator_b4_02",
        "operator_b4_03",
        "operator_b4_04",
        "operator_b4_05",
        "operator_b4_06",
        "operator_b4_07",
        "operator_b4_08",
        "operator_b4_09",
        "operator_b4_10",
    ]


def test_operator_checklist_happy_path_passes_without_live_side_effects() -> None:
    checklist = generate_operator_checklist(_activation_request())

    assert checklist.can_sign_off is True
    assert checklist.blocking_reasons == ()
    assert {item.status for item in checklist.items} == {"ready"}
    assert checklist.to_dict()["passed"] is True


def test_operator_checklist_fails_closed_without_risk_owner_approval() -> None:
    payload = _activation_request()
    payload["approvals"]["risk_owner"]["status"] = "pending"

    checklist = generate_operator_checklist(payload)

    assert checklist.can_sign_off is False
    assert checklist.items[0].status == "blocked"
    assert checklist.items[0].blocking_reasons == (
        "risk-owner approval must be recorded before operator sign-off",
    )
    assert (
        "risk-owner approval must be recorded before operator sign-off"
        in checklist.blocking_reasons
    )


def test_operator_checklist_fails_closed_for_non_live_runtime_binding() -> None:
    payload = _activation_request()
    payload["operator_review"]["target_stage"] = "canary"

    checklist = generate_operator_checklist(payload)

    assert checklist.can_sign_off is False
    assert checklist.items[3].status == "blocked"
    assert (
        "runtime binding target stage must be live"
        in checklist.items[3].blocking_reasons
    )


def test_operator_checklist_fails_closed_without_runtime_target_stage() -> None:
    payload = _activation_request()
    del payload["operator_review"]["target_stage"]

    checklist = generate_operator_checklist(payload)

    assert checklist.can_sign_off is False
    assert checklist.items[3].status == "blocked"
    assert (
        "runtime binding live-stage evidence is required"
        in checklist.items[3].blocking_reasons
    )


def test_operator_checklist_fails_closed_for_active_hard_fail_condition() -> None:
    payload = _activation_request()
    payload["conditions"]["kill_switch_unavailable"] = True

    checklist = generate_operator_checklist(payload)

    assert checklist.can_sign_off is False
    assert checklist.items[9].status == "blocked"
    assert (
        "hard fail condition active: kill_switch_unavailable"
        in checklist.items[9].blocking_reasons
    )


def test_operator_checklist_fails_closed_for_short_drift_cooldown() -> None:
    payload = _activation_request()
    payload["cooldown"]["hours_since_short_term_drift"] = 3

    checklist = generate_operator_checklist(payload)

    assert checklist.can_sign_off is False
    assert checklist.items[9].status == "blocked"
    assert (
        "short-term drift cooldown must be satisfied before operator sign-off"
        in checklist.items[9].blocking_reasons
    )


def _activation_request() -> dict:
    return {
        "evidence": {
            "broker_credential_scope_verified": True,
            "broker_sandbox_smoke_ref": "support/evidence/BROKER/sandbox-smoke.json",
            "kill_switch_demo_ref": "support/evidence/KILL/demo.json",
            "rollback_drill_ref": "support/evidence/ROLLBACK/drill.json",
            "bff_ha_readiness_ref": "support/evidence/BFF/ha-readiness.json",
            "telemetry_readiness_ref": "support/evidence/TEL/readiness.json",
            "audit_retention_ref": "support/evidence/AUDIT/retention.json",
            "first_week_observation_window_ref": "support/evidence/BLA/first-week.json",
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
            "operator": {"status": "pending"},
        },
        "conditions": {condition: False for condition in EXPECTED_HARD_FAIL_CONDITIONS},
        "cooldown": {
            "short_term_drift_detected": True,
            "hours_since_short_term_drift": 24,
        },
    }
