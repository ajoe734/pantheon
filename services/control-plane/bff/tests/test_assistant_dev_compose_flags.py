from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_dev_compose_enables_codex_assistant_provider_for_bff() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["operator-bff"]
    env = service["environment"]

    assert env["PANTHEON_STATUS_ROOT"] == "${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}"
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
    assert (
        "${PANTHEON_STATUS_ROOT_HOST:-.}:${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}:rw"
        in service["volumes"]
    )


def test_openclaw_adapter_can_prepare_repair_worktrees_from_status_root() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["openclaw-gateway-adapter"]
    env = service["environment"]

    assert (
        env["PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT"]
        == "${PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT:-/srv/pantheon-assistant/worktrees}"
    )
    assert env["PANTHEON_STATUS_ROOT_CONTAINER"] == "${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}"
    assert env["PANTHEON_ASSISTANT_REPAIR_REPO_URL"] == "${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-/workspace/status-root}"
    assert (
        env["PANTHEON_ASSISTANT_REPAIR_REMOTE_URL"]
        == "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-https://github.com/ajoe734/pantheon.git}"
    )
    assert (
        env["PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS"]
        == "${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"
    )
    assert (
        env["PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS"]
        == "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"
    )
    assert (
        "${PANTHEON_STATUS_ROOT_HOST:-.}:${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}:ro"
        in service["volumes"]
    )
    assert (
        "${PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT:-/srv/pantheon-assistant/worktrees}:${PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT:-/srv/pantheon-assistant/worktrees}:rw"
        in service["volumes"]
    )
