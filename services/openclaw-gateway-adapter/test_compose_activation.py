from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OPENCLAW_VERSION = "2026.7.1"


def test_compose_wires_openclaw_gateway_adapter_without_broker_activation() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    adapter = services["openclaw-gateway-adapter"]
    assert adapter["build"]["dockerfile"] == "services/openclaw-gateway-adapter/Dockerfile"
    assert adapter["environment"]["PORT"] == "8104"
    assert adapter["environment"]["OPENCLAW_GATEWAY_URL"] == "http://openclaw-gateway:18789"
    assert adapter["environment"]["OPENCLAW_UPSTREAM_RETRIES"] == "${OPENCLAW_UPSTREAM_RETRIES:-1}"
    assert adapter["environment"]["OPENCLAW_PRODUCTION_BROKER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_PAPER_ADAPTER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_LIVE_ADAPTER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_CANARY_ADAPTER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_CAPITAL_BINDING_ENABLED"] == "false"
    assert (
        adapter["environment"]["OPENCLAW_ALLOWED_TOOLS"]
        == "${OPENCLAW_ALLOWED_TOOLS:-assistant.command,assistant.provider.reauth,assistant.provider.register,assistant.openclaw.ask,assistant.control_mode.status,assistant.transcript.resync}"
    )
    assert adapter["ports"] == ["127.0.0.1:${OPENCLAW_GATEWAY_ADAPTER_PORT:-18104}:8104"]
    assert adapter["cap_add"] == ["SYS_ADMIN"]
    assert adapter["security_opt"] == ["seccomp=unconfined", "apparmor=unconfined"]

    healthcheck = " ".join(adapter["healthcheck"]["test"])
    assert "http://127.0.0.1:{port}/livez" in healthcheck
    assert "/readyz" not in healthcheck
    assert "/healthz" not in healthcheck

    init = services["openclaw-data-init"]
    assert init["profiles"] == ["openclaw"]
    assert "chown -R 1000:1000 /home/node/.openclaw" in " ".join(init["command"])

    upstream = services["openclaw-gateway"]
    assert upstream["profiles"] == ["openclaw"]
    assert upstream["image"] == f"pantheon-openclaw-gateway:{OPENCLAW_VERSION}"
    assert upstream["build"]["args"] == {
        "ANTHROPIC_CLAUDE_CODE_NPM_VERSION": "2.1.216",
        "GOOGLE_GEMINI_CLI_NPM_VERSION": "0.51.0",
    }
    upstream_command = " ".join(upstream["command"])
    assert "config set gateway.http.endpoints.responses.enabled true --json" in upstream_command
    assert 'exec node dist/index.js gateway --allow-unconfigured' in upstream_command
    assert '"$$OPENCLAW_GATEWAY_TOKEN"' in upstream_command
    assert upstream["depends_on"]["openclaw-data-init"]["condition"] == "service_completed_successfully"
    upstream_healthcheck = " ".join(upstream["healthcheck"]["test"])
    assert "/readyz" in upstream_healthcheck
    assert "/healthz" not in upstream_healthcheck
    assert "profiles" not in adapter
    assert adapter["environment"]["OPENCLAW_BROKER_SIDECAR_URL"] == "http://broker:8102"
    assert adapter["environment"]["OPENCLAW_RUNTIME_MANAGER_URL"] == "http://runtime-manager:8081"
    assert adapter["environment"]["PANTHEON_RUNTIME_MANAGER_TOKEN"] == "runtime-control-internal"
    assert adapter["environment"]["PANTHEON_ASSISTANT_CODEX_WORKSPACE"] == (
        "${PANTHEON_ASSISTANT_CODEX_WORKSPACE:-/workspace}"
    )
    assert (
        adapter["environment"]["PANTHEON_ASSISTANT_CODEX_HOST_HOME"]
        == "${PANTHEON_ASSISTANT_CODEX_HOST_HOME:-/srv/pantheon-assistant/.codex}"
    )
    assert (
        adapter["environment"]["PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME"]
        == "${PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME:-/home/pantheon-assistant/.codex}"
    )
    assert (
        adapter["environment"]["PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR"]
        == "${PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR:-/srv/pantheon-assistant/.claude}"
    )
    assert (
        adapter["environment"]["PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR"]
        == "${PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR:-/home/pantheon-assistant/.claude}"
    )
    assert adapter["environment"]["PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE"] == (
        "${PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE:-rw}"
    )

    codex_credential_volume = (
        "${PANTHEON_ASSISTANT_CODEX_HOST_HOME:-/srv/pantheon-assistant/.codex}:"
        "${PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME:-/home/pantheon-assistant/.codex}:"
        "${PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE:-rw}"
    )
    claude_credential_volume = (
        "${PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR:-/srv/pantheon-assistant/.claude}:"
        "${PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR:-/home/pantheon-assistant/.claude}:"
        "${PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE:-rw}"
    )
    assert codex_credential_volume in upstream["volumes"]
    assert claude_credential_volume in upstream["volumes"]
    assert codex_credential_volume in adapter["volumes"]
    assert claude_credential_volume in adapter["volumes"]
    assert "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT" not in adapter["environment"]
    assert all("PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT" not in volume for volume in upstream["volumes"])
    assert all("PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT" not in volume for volume in adapter["volumes"])

    broker = services["broker"]
    assert broker["build"]["dockerfile"] == "services/broker/Dockerfile"
    assert broker["environment"]["PORT"] == "8102"
    assert broker["environment"]["BROKER_PAPER_ENABLED"] == "${BROKER_PAPER_ENABLED:-false}"
    assert "profiles" not in broker

    smoke = services["smoke-stack"]
    assert smoke["environment"]["OPENCLAW_GATEWAY_ADAPTER_URL"] == "http://openclaw-gateway-adapter:8104"
    assert smoke["depends_on"]["openclaw-gateway-adapter"]["condition"] == "service_healthy"


def test_honest_stack_smoke_checks_openclaw_adapter_degraded_boundary() -> None:
    smoke = (ROOT / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")

    assert 'OPENCLAW_GATEWAY_ADAPTER_URL = os.getenv("OPENCLAW_GATEWAY_ADAPTER_URL", "http://127.0.0.1:8104")' in smoke
    assert '_wait_for_health("openclaw-gateway-adapter", f"{OPENCLAW_GATEWAY_ADAPTER_URL}/livez")' in smoke
    assert "/api/openclaw-adapter/capabilities" in smoke
    assert "/api/openclaw-adapter/sessions" in smoke
    assert "CAPABILITY_DENIED" in smoke


def test_openclaw_pin_and_shared_model_pool_stay_in_lockstep() -> None:
    upstream_image = f"ghcr.io/openclaw/openclaw:{OPENCLAW_VERSION}"
    gateway_dockerfile = (ROOT / "integrations/openclaw/gateway/Dockerfile").read_text(
        encoding="utf-8"
    )
    adapter_dockerfile = (ROOT / "services/openclaw-gateway-adapter/Dockerfile").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "integrations/openclaw/adapter/gateway_runtime.py").read_text(
        encoding="utf-8"
    )
    smoke = (ROOT / "scripts/openclaw-smoke-test.sh").read_text(encoding="utf-8")
    model_pool = (ROOT / "scripts/openclaw-configure-shared-model-pool.sh").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")

    assert f"FROM {upstream_image}" in gateway_dockerfile
    assert f"FROM {upstream_image} AS openclaw_cli" in adapter_dockerfile
    assert f'image_ref: str = "{upstream_image}"' in runtime
    assert f'IMAGE="{upstream_image}"' in smoke

    expected_models = {
        "openai/gpt-5.6-sol",
        "openai/gpt-5.5",
        "anthropic/claude-opus-4-8",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-3.1-pro-preview",
    }
    assert all(model_ref in model_pool for model_ref in expected_models)
    assert "plugins.entries.codex.enabled" in model_pool
    assert "plugins.entries.google.enabled" in model_pool
    assert '"path":"gateway.http.endpoints.responses.enabled","value":true' in model_pool
    assert (
        '{"path":"agents.defaults.model.primary","value":"anthropic/claude-opus-4-8"}'
        in model_pool
    )
    assert 'openclaw config get agents.defaults.model.primary --json' in model_pool
    assert "openclaw-configure-shared-model-pool.sh" in deploy
