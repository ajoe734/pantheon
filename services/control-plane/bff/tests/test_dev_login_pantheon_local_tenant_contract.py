from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]

# Without an explicit policy or BFF tenant, local Compose retains this
# downstream tenant. Hosted deployments select their existing BFF tenant.
# Every dev-login identity with an explicit allowed-tenant default must still
# authorize the unchanged local fallback.
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
    """Local login defaults retain access to the unchanged local fallback.

    Hosted downstream selection follows explicit policy/BFF configuration;
    it does not change existing local login allowlists.
    """

    env = _compose_env()
    for var_name in _IDENTITIES_WITH_EXPLICIT_DEFAULTS:
        assert var_name in env, f"expected {var_name} to remain configured on operator-bff"
        default_tenants = _default_value(env[var_name]).split(",")
        assert CANONICAL_DOWNSTREAM_TENANT in default_tenants, (
            f"{var_name} default {default_tenants!r} does not authorize "
            f"{CANONICAL_DOWNSTREAM_TENANT!r}"
        )


def test_downstream_agora_tenant_falls_back_to_bff_then_local() -> None:
    """Explicit handoff/policy scopes win; otherwise follow the deployed BFF."""

    env = _compose_env()
    assert _default_value(env["AGORA_HANDOFF_SERVICE_TENANTS"]) == (
        "${POLICY_LEARNING_AGORA_TENANT_ID:-${PANTHEON_BFF_TENANT_ID:-"
        + CANONICAL_DOWNSTREAM_TENANT + "}}"
    )
