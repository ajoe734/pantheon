from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_product_bff_compose_has_no_development_bridge_or_status_root() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["operator-bff"]
    env = service["environment"]

    assert "PANTHEON_STATUS_ROOT" not in env
    assert "BRIDGE_SIGNING_PRIVATE_KEY" not in env
    assert "BRIDGE_SIGNING_KEY_ID" not in env
    assert "BRIDGE_SIGNING_PUBLIC_KEYS_JSON" not in env
    assert "PANTHEON_DEVELOPMENT_TOOLING_ROUTES_ENABLED" not in env
    assert env["PANTHEON_ASSISTANT_ENABLED"] == "${PANTHEON_ASSISTANT_ENABLED:-true}"
    assert (
        env["PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED"]
        == "${PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED:-true}"
    )
    assert env["MANAGEMENT_AI_STORE_BACKEND"] == "${MANAGEMENT_AI_STORE_BACKEND:-postgres}"
    assert env["PANTHEON_ASSISTANT_PROVIDER"] == "${PANTHEON_ASSISTANT_PROVIDER:-openclaw}"
    assert (
        env["PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS"]
        == "${PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS:-180.0}"
    )
    assert env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] == "${PANTHEON_ASSISTANT_KERNEL_ENABLED:-true}"
    assert (
        env["PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"]
        == "${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
    )
    assert (
        env["PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH"]
        == "${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH:-}"
    )
    assert (
        env["PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS"]
        == "${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
    )
    assert (
        env["PANTHEON_BFF_STUB_CAPABILITIES"]
        == "${PANTHEON_BFF_STUB_CAPABILITIES:-}"
    )
    assert env["PANTHEON_BFF_MFA_REQUIRED"] == "${PANTHEON_BFF_MFA_REQUIRED:-false}"
    assert (
        env["PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED"]
        == "${PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED:-false}"
    )
    assert (
        env["PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED"]
        == "${PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED:-false}"
    )
    assert (
        env["PANTHEON_MANAGEMENT_AI_STORE_PATH"]
        == "${PANTHEON_MANAGEMENT_AI_STORE_PATH:-/data/bff/management-ai-conversations.json}"
    )
    assert (
        env["PANTHEON_MANAGEMENT_AI_ATTACHMENT_STORE_PATH"]
        == "${PANTHEON_MANAGEMENT_AI_ATTACHMENT_STORE_PATH:-/data/bff/management-ai-attachments}"
    )
    assert env["PANTHEON_MGMT_AI_ATTACH_BUCKET"] == "${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
    assert (
        env["PANTHEON_MANAGEMENT_AI_AUDIT_PATH"]
        == "${PANTHEON_MANAGEMENT_AI_AUDIT_PATH:-/data/bff/management-ai-audit.jsonl}"
    )
    assert env["PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL"] == "http://openclaw-gateway-adapter:8104"
    assert (
        env["PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN"]
        == "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}"
    )
    assert (
        env["PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED"]
        == "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-true}"
    )
    assert (
        env["PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_REQUIRED"]
        == "${PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_REQUIRED:-true}"
    )
    assert (
        env["PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_STORE_PATH"]
        == "${PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_STORE_PATH:-/data/bff/assistant-command-idempotency.json}"
    )
    assert (
        env["PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS"]
        == "${PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS:-300}"
    )
    assert (
        env["PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED"]
        == "${PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED:-true}"
    )
    assert (
        env["PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH"]
        == "${PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH:-/data/bff/management-nl-command-idempotency.json}"
    )
    assert (
        env["PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS"]
        == "${PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS:-300}"
    )
    assert env["PANTHEON_LIVE_BROKER_ENABLED"] == "${PANTHEON_LIVE_BROKER_ENABLED:-false}"
    assert not any("PANTHEON_STATUS_ROOT_HOST" in volume for volume in service["volumes"])


def test_product_compose_defaults_do_not_require_development_tooling() -> None:
    for name in ("docker-compose.staging-full.yml", "docker-compose.control.yml"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        start = text.index("  operator-bff:")
        end = text.index("\n  ", start + len("  operator-bff:"))
        block = text[start:end]
        assert "BRIDGE_SIGNING" not in block
        assert "PANTHEON_DEVELOPMENT_TOOLING_ROUTES_ENABLED" not in block
        assert "PANTHEON_STATUS_ROOT" not in block
