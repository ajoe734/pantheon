from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


BFF_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BFF_DIR))

from dev_auth_validation import (  # noqa: E402
    DevAuthValidationError,
    dev_login_environment_allowed,
    validate_dev_login_configuration,
    validate_login_response,
    validate_rollback_environment,
    validate_workshop_response,
)


JWT_SECRET = "validator-jwt-secret-value-2026-000000"
CI_SECRET = "validator-ci-secret-value-2026-0000000"
RISK_SECRET = "validator-risk-secret-value-2026-00000"


def _profiles() -> dict[str, dict]:
    return {
        "ci-agora": {
            "secret": CI_SECRET,
            "subject": "pantheon-dev-ci-agora",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "capabilities": [],
            "mfa_verified": False,
        },
        "risk-owner": {
            "secret": RISK_SECRET,
            "subject": "pantheon-dev-risk-owner",
            "roles": ["risk_owner"],
            "tenant_id": "tenant-risk",
            "allowed_tenants": ["tenant-risk"],
            "capabilities": ["risk.alert.read"],
            "mfa_verified": True,
        },
    }


def test_full_map_validator_accepts_exact_ci_and_risk_owner_profiles() -> None:
    profiles = validate_dev_login_configuration(
        json.dumps(_profiles()),
        JWT_SECRET,
        require_ci_profile=True,
        ci_client_id="ci-agora",
        ci_client_secret=CI_SECRET,
    )

    assert profiles["ci-agora"]["capabilities"] == []
    assert profiles["ci-agora"]["mfa_verified"] is False
    assert profiles["risk-owner"]["roles"] == ["risk_owner"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda profiles: profiles["risk-owner"].update({"secret": CI_SECRET}),
        lambda profiles: profiles["risk-owner"].update({"subject": "pantheon-dev-ci-agora"}),
        lambda profiles: profiles["risk-owner"].update({"secret": "short"}),
        lambda profiles: profiles["risk-owner"].update({"secret": f"{RISK_SECRET}\n"}),
        lambda profiles: profiles["risk-owner"].update({"tenant_id": " tenant-risk"}),
        lambda profiles: profiles["risk-owner"].update({"roles": ["risk-owner"]}),
        lambda profiles: profiles["risk-owner"].update({"extra": True}),
        lambda profiles: profiles["risk-owner"].pop("mfa_verified"),
    ],
)
def test_full_map_validator_rejects_ambiguity_controls_and_schema_drift(mutation) -> None:
    profiles = _profiles()
    mutation(profiles)

    with pytest.raises(DevAuthValidationError):
        validate_dev_login_configuration(json.dumps(profiles), JWT_SECRET)


@pytest.mark.parametrize("jwt_secret", ["short", f"{JWT_SECRET} ", f"{JWT_SECRET}\n"])
def test_full_map_validator_rejects_invalid_raw_jwt_secret(jwt_secret: str) -> None:
    with pytest.raises(DevAuthValidationError):
        validate_dev_login_configuration(json.dumps(_profiles()), jwt_secret)


def test_full_map_validator_rejects_duplicate_json_keys() -> None:
    raw = '{"ci-agora":{},"ci-agora":{}}'

    with pytest.raises(DevAuthValidationError, match="duplicate JSON key"):
        validate_dev_login_configuration(raw, JWT_SECRET)


@pytest.mark.parametrize("wrapper", [lambda raw: f" {raw}", lambda raw: f"{raw}\n"])
def test_full_map_validator_rejects_outer_whitespace(wrapper) -> None:
    with pytest.raises(DevAuthValidationError):
        validate_dev_login_configuration(wrapper(json.dumps(_profiles())), JWT_SECRET)


@pytest.mark.parametrize(
    ("environment", "deployment_stage", "allowed"),
    [
        ("dev", "dev", True),
        ("local", "", True),
        ("test", "testing", True),
        ("", "", False),
        ("qa", "qa", False),
        ("staging", "", False),
        ("unknown", "dev", False),
        ("dev", "production", False),
        (" DEV ", "dev", False),
        ("DEV", "dev", False),
    ],
)
def test_dev_login_environment_allowlist_is_explicit(
    environment: str, deployment_stage: str, allowed: bool
) -> None:
    assert dev_login_environment_allowed(environment, deployment_stage) is allowed


@pytest.mark.parametrize(
    "raw",
    [
        '{"token_type":" bearer","access_token":"aaa.bbb.ccc"}',
        '{"token_type":"bearer\\n","access_token":"aaa.bbb.ccc"}',
        '{"token_type":"bearer","access_token":"aaa.bbb.ccc\\n"}',
        '{"token_type":"bearer","access_token":"aaa.bbb.ccc extra"}',
        '{"token_type":"bearer","access_token":"aaa.bbb.ccc","access_token":"ddd.eee.fff"}',
    ],
)
def test_login_response_rejects_raw_whitespace_control_and_duplicate_fields(raw: str) -> None:
    with pytest.raises(DevAuthValidationError):
        validate_login_response(raw)


@pytest.mark.parametrize(
    "workshop_id",
    [
        " 123e4567-e89b-12d3-a456-426614174000",
        "123e4567-e89b-12d3-a456-426614174000 ",
        "123e4567-e89b-12d3-a456-426614174000\n",
        "NOT-A-UUID",
    ],
)
def test_workshop_response_rejects_noncanonical_raw_id(workshop_id: str) -> None:
    with pytest.raises(DevAuthValidationError):
        validate_workshop_response(json.dumps({"data": {"workshop_id": workshop_id}}))


def test_rollback_environment_compares_presence_and_exact_secret_digest() -> None:
    expected = json.dumps(["PANTHEON_BFF_JWT_SECRET=secret-a", "PANTHEON_BFF_JWKS_URI="])
    names = "PANTHEON_BFF_JWT_SECRET\nPANTHEON_BFF_JWKS_URI\n"

    validate_rollback_environment(expected, expected, names)
    with pytest.raises(DevAuthValidationError, match="changed value"):
        validate_rollback_environment(
            expected,
            json.dumps(["PANTHEON_BFF_JWT_SECRET=secret-b", "PANTHEON_BFF_JWKS_URI="]),
            names,
        )
    with pytest.raises(DevAuthValidationError, match="changed presence"):
        validate_rollback_environment(
            expected,
            json.dumps(["PANTHEON_BFF_JWT_SECRET=secret-a"]),
            names,
        )
