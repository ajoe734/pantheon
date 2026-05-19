from __future__ import annotations

import pytest

from services.broker.live_activation.credential_readiness import (
    READINESS_SOURCE,
    REQUIRED_SCHEMA_FIELDS,
    SCHEMA_VERSION,
    BrokerCredentialReadiness,
    BrokerCredentialReadinessError,
    validate_credential_readiness,
    validate_credential_readiness_or_raise,
)


def test_credential_readiness_schema_matches_part_b5_shape() -> None:
    readiness = BrokerCredentialReadiness.from_mapping(_readiness_payload())

    assert readiness.schema_version == SCHEMA_VERSION
    assert readiness.source == READINESS_SOURCE
    payload = readiness.to_dict()
    assert tuple(key for key in payload if key != "evidence_refs") == REQUIRED_SCHEMA_FIELDS
    assert payload["vault_secret_refs"] == [
        "secret://pantheon-prod-shioaji-api-key",
        "secret://pantheon-prod-shioaji-secret-key",
    ]


def test_credential_readiness_happy_path_passes_without_secret_dereference() -> None:
    result = validate_credential_readiness(_readiness_payload(), expected_stage="live")

    assert result.passed is True
    assert result.ready_for_stage_activation is True
    assert result.blocking_reasons == ()
    assert result.to_dict()["ready_for_stage_activation"] is True


def test_credential_readiness_to_dict_does_not_expose_raw_secret_values() -> None:
    readiness = BrokerCredentialReadiness.from_mapping(_readiness_payload())

    encoded = str(readiness.to_dict())

    assert "raw-secret-value" not in encoded
    assert "SHIOAJI_SECRET_KEY" not in encoded
    assert "secret://pantheon-prod-shioaji-secret-key" in encoded


def test_credential_readiness_rejects_raw_secret_material() -> None:
    payload = _readiness_payload()
    payload["raw_secret_present"] = True
    payload["operator_review"] = {"shioaji_secret_key": "raw-secret-value"}

    result = validate_credential_readiness(payload)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert codes == {"raw_secret_material_present"}
    with pytest.raises(BrokerCredentialReadinessError, match="raw broker secret"):
        validate_credential_readiness_or_raise(payload)


def test_credential_readiness_rejects_non_vault_secret_refs() -> None:
    payload = _readiness_payload()
    payload["vault_secret_refs"] = ["raw-secret-value"]

    result = validate_credential_readiness(payload)

    assert result.passed is False
    assert {issue.code for issue in result.errors} == {"invalid_vault_secret_ref"}


def test_credential_readiness_rejects_control_plane_injection() -> None:
    payload = _readiness_payload()
    payload["injection_target"] = "vm1_control_plane_bff"

    result = validate_credential_readiness(payload)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "invalid_injection_target" in codes
    assert "forbidden_credential_location" in codes


def test_credential_readiness_rejects_missing_stage_isolation() -> None:
    payload = _readiness_payload()
    payload["not_shared_with_stages"] = ["paper"]

    result = validate_credential_readiness(payload)

    assert result.passed is False
    assert {issue.code for issue in result.errors} == {"missing_stage_isolation"}
    assert "canary" in result.blocking_reasons[0]


def test_credential_readiness_rejects_stale_rotation_policy() -> None:
    payload = _readiness_payload()
    payload["rotation_interval_days"] = 120
    payload["next_rotation_due_at"] = "2026-08-30T00:00:00Z"

    result = validate_credential_readiness(payload)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "rotation_cadence_too_long" in codes
    assert "rotation_window_exceeds_policy" in codes


def test_credential_readiness_rejects_overbroad_or_incomplete_permissions() -> None:
    payload = _readiness_payload()
    payload["permission_scope"] = ["account_read", "market_data_read", "admin"]

    result = validate_credential_readiness(payload)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "overbroad_permission_scope" in codes
    assert "missing_required_permission_scope" in codes


def test_credential_readiness_rejects_stage_mismatch() -> None:
    result = validate_credential_readiness(_readiness_payload(), expected_stage="canary")

    assert result.passed is False
    assert {issue.code for issue in result.errors} == {"stage_mismatch"}


def _readiness_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": READINESS_SOURCE,
        "broker": "shioaji",
        "stage": "live",
        "account_ref": "broker-account://shioaji/live/main-subaccount",
        "venue_ref": "venue://twse-tpex/live-route",
        "vault_secret_refs": [
            "secret://pantheon-prod-shioaji-api-key",
            "secret://pantheon-prod-shioaji-secret-key",
        ],
        "injection_target": "vm2_execution_env",
        "permission_scope": [
            "account_read",
            "market_data_read",
            "order_submit",
            "order_cancel",
        ],
        "not_shared_with_stages": ["paper", "canary"],
        "rotation_interval_days": 90,
        "last_rotated_at": "2026-05-01T00:00:00Z",
        "next_rotation_due_at": "2026-07-30T00:00:00Z",
        "rotation_policy_ref": "runbook://broker-credential-rotation/live",
        "revocation_procedure_ref": "runbook://broker-credential-revoke/live",
        "operator_verification_ref": "approval://operator/broker-credential/live-001",
        "entitlement_evidence_ref": "support/evidence/BLA-006/live-entitlement.json",
        "sandbox_smoke_ref": "support/evidence/BROKER/sandbox-smoke.json",
        "status": "verified",
    }
