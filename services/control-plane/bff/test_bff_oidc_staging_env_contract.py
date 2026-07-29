from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROL_COMPOSE = REPO_ROOT / "docker-compose.control.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.yml"
PROD_ENV_EXAMPLE = REPO_ROOT / "env" / "prod-control.env.example"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _operator_bff_block(path: Path = CONTROL_COMPOSE) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index("  operator-bff:")
    end = text.index("\n  persona:", start)
    return text[start:end]


def test_prod_control_env_requires_staging_oidc_idp_posture() -> None:
    env = _read_env_file(PROD_ENV_EXAMPLE)

    assert env["PANTHEON_ENV"] == "staging-live"
    assert env["PANTHEON_BFF_AUTH_STUB"] == "false"
    assert env["PANTHEON_BFF_AUTH_MODE"] == "strict"
    assert env["PANTHEON_BFF_JWT_SECRET"] == ""
    assert env["PANTHEON_BFF_JWT_ISSUER"] == ""
    assert env["PANTHEON_BFF_JWT_AUDIENCE"] == ""

    assert env["PANTHEON_BFF_OIDC_ISSUER"].startswith("https://")
    assert env["PANTHEON_BFF_OIDC_AUDIENCE"]
    assert env["PANTHEON_BFF_JWKS_URI"] or env["PANTHEON_BFF_OIDC_DISCOVERY_URL"]

    assert env["PANTHEON_BFF_ROLE_CLAIMS"] == "groups,roles"
    role_map = dict(
        item.split("=", 1)
        for item in env["PANTHEON_BFF_ROLE_MAP"].split(";")
        if "=" in item
    )
    assert role_map["pantheon-staging-admins"] == "admin"
    assert role_map["pantheon-staging-operators"] == "operator"
    assert role_map["pantheon-staging-reviewers"] == "reviewer"
    assert role_map["pantheon-staging-approvers"] == "approver"
    assert env["PANTHEON_BFF_ROLE_MAP_MODE"] == "strict"
    assert env["PANTHEON_BFF_DEFAULT_ROLE"] == "viewer"

    assert env["PANTHEON_BFF_MFA_REQUIRED"] == "true"
    assert set(env["PANTHEON_BFF_MFA_CLAIMS"].split(",")) >= {"amr", "acr", "mfa_verified"}
    assert set(env["PANTHEON_BFF_MFA_VALUES"].split(",")) >= {"mfa", "otp", "totp", "webauthn"}
    assert env["PANTHEON_BFF_STUB_CAPABILITIES"] == ""
    assert env["PANTHEON_STATUS_ROOT_HOST"]
    assert env["PANTHEON_STATUS_ROOT_CONTAINER"] == "/workspace/status-root"
    assert env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] == "false"
    assert env["PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"] == "/data/bff/assistant-control-mode.json"
    assert env["PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS"] == "300"


def test_control_compose_forwards_bff_idp_env_with_stub_disabled_by_default() -> None:
    block = _operator_bff_block()

    assert "PANTHEON_BFF_AUTH_STUB: ${PANTHEON_BFF_AUTH_STUB:-false}" in block
    assert "PANTHEON_BFF_AUTH_MODE: ${PANTHEON_BFF_AUTH_MODE:-strict}" in block
    assert "PANTHEON_BFF_JWT_SECRET: ${PANTHEON_BFF_JWT_SECRET:-}" in block
    assert "PANTHEON_STATUS_ROOT: ${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}" in block

    for key in (
        "PANTHEON_BFF_JWKS_URI",
        "PANTHEON_BFF_OIDC_DISCOVERY_URL",
        "PANTHEON_BFF_OIDC_ISSUER",
        "PANTHEON_BFF_OIDC_AUDIENCE",
        "PANTHEON_BFF_ROLE_CLAIMS",
        "PANTHEON_BFF_ROLE_MAP",
        "PANTHEON_BFF_ROLE_MAP_MODE",
        "PANTHEON_BFF_MFA_REQUIRED",
        "PANTHEON_BFF_MFA_CLAIMS",
        "PANTHEON_BFF_MFA_VALUES",
        "PANTHEON_BFF_STUB_CAPABILITIES",
        "PANTHEON_ASSISTANT_KERNEL_ENABLED",
        "PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH",
        "PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS",
    ):
        assert f"{key}: ${{{key}:-" in block
    assert "${PANTHEON_STATUS_ROOT_HOST:-.}:${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}:ro" in block


def test_dev_compose_forwards_bff_auth_env_with_dev_stub_default() -> None:
    block = _operator_bff_block(DEV_COMPOSE)

    assert "PANTHEON_BFF_AUTH_STUB: ${PANTHEON_BFF_AUTH_STUB:-false}" in block
    assert "PANTHEON_BFF_AUTH_MODE: ${PANTHEON_BFF_AUTH_MODE:-strict}" in block
    assert (
        "PANTHEON_BFF_JWT_SECRET: "
        "${PANTHEON_BFF_JWT_SECRET:-pantheon-local-bff-jwt-secret}"
    ) in block
    assert "PANTHEON_BFF_JWT_ISSUER: ${PANTHEON_BFF_JWT_ISSUER:-}" in block
    assert "PANTHEON_BFF_JWT_AUDIENCE: ${PANTHEON_BFF_JWT_AUDIENCE:-}" in block
    assert "PANTHEON_BFF_DEFAULT_ROLE: ${PANTHEON_BFF_DEFAULT_ROLE:-operator}" in block
    assert "PANTHEON_STATUS_ROOT: ${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}" in block

    for key in (
        "PANTHEON_BFF_JWKS_URI",
        "PANTHEON_BFF_OIDC_DISCOVERY_URL",
        "PANTHEON_BFF_OIDC_ISSUER",
        "PANTHEON_BFF_OIDC_AUDIENCE",
        "PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID",
        "PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET",
        "PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED",
        "PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_ID",
        "PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET",
        "PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID",
        "PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET",
        "PANTHEON_BFF_ROLE_CLAIMS",
        "PANTHEON_BFF_ROLE_MAP",
        "PANTHEON_BFF_ROLE_MAP_MODE",
        "PANTHEON_BFF_MFA_REQUIRED",
        "PANTHEON_BFF_MFA_CLAIMS",
        "PANTHEON_BFF_MFA_VALUES",
        "PANTHEON_BFF_STUB_CAPABILITIES",
        "PANTHEON_ASSISTANT_KERNEL_ENABLED",
        "PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH",
        "PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS",
    ):
        assert f"{key}: ${{{key}:-" in block
    assert "${PANTHEON_STATUS_ROOT_HOST:-.}:${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}:rw" in block
