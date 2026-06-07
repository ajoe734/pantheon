from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_dev_compose_enables_codex_assistant_provider_for_bff() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["operator-bff"]["environment"]

    assert env["PANTHEON_ASSISTANT_ENABLED"] == "${PANTHEON_ASSISTANT_ENABLED:-true}"
    assert (
        env["PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED"]
        == "${PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED:-true}"
    )
    assert env["MANAGEMENT_AI_STORE_BACKEND"] == "${MANAGEMENT_AI_STORE_BACKEND:-postgres}"
    assert env["PANTHEON_ASSISTANT_PROVIDER"] == "${PANTHEON_ASSISTANT_PROVIDER:-codex_cli}"
    assert (
        env["PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS"]
        == "${PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS:-75.0}"
    )
    assert env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] == "${PANTHEON_ASSISTANT_KERNEL_ENABLED:-false}"
    assert (
        env["PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"]
        == "${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
    )
    assert (
        env["PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS"]
        == "${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
    )
    assert env["PANTHEON_BFF_STUB_CAPABILITIES"] == "${PANTHEON_BFF_STUB_CAPABILITIES:-}"
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
    assert env["PANTHEON_LIVE_BROKER_ENABLED"] == "${PANTHEON_LIVE_BROKER_ENABLED:-false}"
