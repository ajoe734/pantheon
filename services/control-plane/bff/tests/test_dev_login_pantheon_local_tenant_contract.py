from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]

# The downstream Agora/policy-learning boundary owned by operator-bff is
# tenant-scoped to this canonical value; see AGORA_HANDOFF_SERVICE_TENANTS
# and POLICY_LEARNING_AGORA_TENANT_ID below. This test does not change that
# canonical downstream tenant -- it only asserts every dev-login identity
# that already has an explicit allowed-tenant default can also reach it.
CANONICAL_DOWNSTREAM_TENANT = "pantheon-local"

_IDENTITIES_WITH_EXPLICIT_DEFAULTS = (
    "PANTHEON_BFF_DEV_LOGIN_VIEWER_ALLOWED_TENANTS",
    "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_ALLOWED_TENANTS",
    "PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_ALLOWED_TENANTS",
)


def _compose_env() -> dict[str, str]:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["operator-bff"]["environment"]


def _default_value(raw: str) -> str:
    match = re.match(r"^\$\{[A-Z0-9_]+:-(.*)\}$", raw)
    assert match, f"expected a ${{VAR:-default}} placeholder, got {raw!r}"
    return match.group(1)


def test_dev_login_identity_defaults_authorize_the_downstream_tenant() -> None:
    """Every dev-login identity with an explicit allowed-tenant default must
    also authorize the canonical downstream pantheon-local tenant, or a real
    strict-auth token minted for that identity cannot reach the deployed
    Agora handoff / policy-learning boundary owned by this same service."""

    env = _compose_env()
    for var_name in _IDENTITIES_WITH_EXPLICIT_DEFAULTS:
        assert var_name in env, f"expected {var_name} to remain configured on operator-bff"
        default_tenants = _default_value(env[var_name]).split(",")
        assert CANONICAL_DOWNSTREAM_TENANT in default_tenants, (
            f"{var_name} default {default_tenants!r} does not authorize "
            f"{CANONICAL_DOWNSTREAM_TENANT!r}"
        )


def test_downstream_agora_tenant_boundary_is_unchanged() -> None:
    """This contract only widens dev-login identity defaults; it must not
    also change the canonical downstream tenant boundary those identities
    are being authorized to reach."""

    env = _compose_env()
    assert _default_value(env["AGORA_HANDOFF_SERVICE_TENANTS"]) == (
        "${POLICY_LEARNING_AGORA_TENANT_ID:-" + CANONICAL_DOWNSTREAM_TENANT + "}"
    )
