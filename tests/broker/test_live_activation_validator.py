from __future__ import annotations

import pytest

from services.broker.live_activation.validator import (
    EXPECTED_HARD_FAIL_CONDITIONS,
    EXPECTED_REQUIRED_APPROVALS,
    EXPECTED_REQUIRED_EVIDENCE,
    BrokerLiveActivationValidationError,
    load_default_criteria,
    validate_activation_request,
    validate_activation_request_or_raise,
    validate_criteria_shape,
)


def test_criteria_json_matches_2026_05_19_b2_shape() -> None:
    criteria = load_default_criteria()

    assert criteria["version"] == "1.0"
    assert criteria["required_evidence"] == EXPECTED_REQUIRED_EVIDENCE
    assert tuple(criteria["required_approvals"]) == EXPECTED_REQUIRED_APPROVALS
    assert tuple(criteria["hard_fail_conditions"]) == EXPECTED_HARD_FAIL_CONDITIONS
    assert criteria["cooldown_policy"] == {
        "min_hours_after_short_term_drift_before_live_change": 24
    }
    assert validate_criteria_shape(criteria).passed is True


def test_activation_request_happy_path_passes_without_live_side_effects() -> None:
    result = validate_activation_request(_activation_request())

    assert result.passed is True
    assert result.to_dict()["can_activate"] is True
    assert result.blocking_reasons == ()


def test_activation_request_fails_closed_for_missing_evidence_and_hard_fail() -> None:
    payload = _activation_request()
    del payload["evidence"]["rollback_drill_ref"]
    payload["conditions"]["telemetry_unavailable"] = True

    result = validate_activation_request(payload)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "missing_required_evidence" in codes
    assert "hard_fail_condition_active" in codes
    assert any("telemetry_unavailable" in reason for reason in result.blocking_reasons)


def test_activation_request_fails_closed_without_operator_approval() -> None:
    payload = _activation_request()
    payload["approvals"]["operator"] = {"status": "pending"}

    result = validate_activation_request(payload)

    assert result.passed is False
    assert {issue.code for issue in result.errors} == {"required_approval_missing"}
    with pytest.raises(BrokerLiveActivationValidationError, match="operator approval"):
        validate_activation_request_or_raise(payload)


def test_activation_request_enforces_short_term_drift_cooldown() -> None:
    payload = _activation_request()
    payload["cooldown"] = {
        "short_term_drift_detected": True,
        "hours_since_short_term_drift": 3,
    }

    result = validate_activation_request(payload)

    assert result.passed is False
    assert {issue.code for issue in result.errors} == {"cooldown_not_satisfied"}


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
        "approvals": {
            "risk_owner": {"status": "approved"},
            "operator": {"status": "approved"},
        },
        "conditions": {condition: False for condition in EXPECTED_HARD_FAIL_CONDITIONS},
        "cooldown": {
            "short_term_drift_detected": True,
            "hours_since_short_term_drift": 24,
        },
    }
