from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEV_KERNEL_ENV = REPO_ROOT / "env" / "dev-management-ai-kernel.env.example"
PROD_CONTROL_ENV = REPO_ROOT / "env" / "prod-control.env.example"
ENABLE_SCRIPT = REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh"
NONPROD_DEPLOY = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_dev_management_ai_kernel_overlay_is_explicitly_dev_only() -> None:
    env = _read_env_file(DEV_KERNEL_ENV)

    assert env["PANTHEON_ENV"] == "dev"
    assert env["PANTHEON_ASSISTANT_ENABLED"] == "true"
    assert env["PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED"] == "true"
    assert env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] == "true"
    assert env["PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"] == "/data/bff/assistant-control-mode.json"
    assert env["PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS"] == "300"
    assert env["PANTHEON_STATUS_ROOT_HOST"] == "/home/lupin/code/pantheon"
    assert env["PANTHEON_STATUS_ROOT_CONTAINER"] == "/workspace/status-root"
    assert env["PANTHEON_ASSISTANT_REPAIR_REPO_URL"] == "/workspace/status-root"
    assert env["PANTHEON_ASSISTANT_REPAIR_REMOTE_URL"] == "https://github.com/ajoe734/pantheon.git"
    assert env["PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS"] == "https://github.com/ajoe734/execute-plans.git"
    assert env["PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS"] == "https://github.com/ajoe734/execute-plans.git"
    assert set(env["PANTHEON_BFF_STUB_CAPABILITIES"].split(",")) == {
        "assistant.kernel.debug",
        "assistant.kernel.repair",
    }

    prod_env = _read_env_file(PROD_CONTROL_ENV)
    assert prod_env["PANTHEON_ENV"] == "staging-live"
    assert prod_env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] == "false"
    assert prod_env["PANTHEON_BFF_STUB_CAPABILITIES"] == ""


def test_enable_management_ai_dev_kernel_script_targets_only_operator_bff() -> None:
    script = ENABLE_SCRIPT.read_text(encoding="utf-8")

    assert 'COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pantheon}"' in script
    assert 'COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"' in script
    assert 'BFF_AUTH_TOKEN="${BFF_AUTH_TOKEN:-pantheon-dev-browser:admin,operator:mfa:assistant.kernel.debug,assistant.kernel.repair}"' in script
    assert 'auth_args=(-H "Authorization: Bearer ${BFF_AUTH_TOKEN}")' in script
    assert "PANTHEON_SUPERVISOR_CONFIG" in script
    assert "resolve_status_root_host" in script
    assert 'PANTHEON_STATUS_ROOT_HOST="$(resolve_status_root_host)"' in script
    assert 'PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}"' in script
    assert 'PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED:-true}"' in script
    assert "--no-deps --force-recreate operator-bff" in script
    assert "/bff/assistant/mode" in script


def test_nonprod_dev_deploy_exports_management_ai_kernel_overlay() -> None:
    script = NONPROD_DEPLOY.read_text(encoding="utf-8")

    assert "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io" in script
    assert 'DEV_ASSISTANT_KERNEL_ENABLED="${DEV_ASSISTANT_KERNEL_ENABLED:-true}"' in script
    assert (
        'DEV_ASSISTANT_CONTROL_MODE_STORE_PATH="${DEV_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"'
        in script
    )
    assert 'DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"' in script
    assert 'DEV_ASSISTANT_REPAIR_REPO_URL="${DEV_ASSISTANT_REPAIR_REPO_URL:-/workspace/status-root}"' in script
    assert (
        'DEV_ASSISTANT_REPAIR_REMOTE_URL="${DEV_ASSISTANT_REPAIR_REMOTE_URL:-https://github.com/ajoe734/pantheon.git}"'
        in script
    )
    assert (
        'DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"'
        in script
    )
    assert (
        'DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"'
        in script
    )
    assert (
        'DEV_BFF_STUB_CAPABILITIES="${DEV_BFF_STUB_CAPABILITIES:-assistant.kernel.debug,assistant.kernel.repair}"'
        in script
    )
    assert 'DEV_STATUS_ROOT_CONTAINER="${DEV_STATUS_ROOT_CONTAINER:-/workspace/status-root}"' in script
    assert "configure_management_ai_dev_kernel_env" in script
    assert (
        'PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED:-$DEV_ASSISTANT_KERNEL_ENABLED}"'
        in script
    )
    assert (
        'PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-$DEV_ASSISTANT_REPAIR_REPO_URL}"'
        in script
    )
    assert (
        'PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-$DEV_ASSISTANT_REPAIR_REMOTE_URL}"'
        in script
    )
    assert (
        'PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-$DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}"'
        in script
    )
    assert (
        'PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-$DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}"'
        in script
    )
    assert 'PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST:-${DEV_STATUS_ROOT_HOST:-$DEV_REMOTE_DIR}}"' in script
    assert 'command_prefix+=" PANTHEON_ASSISTANT_KERNEL_ENABLED=' in script
    assert 'command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REPO_URL=' in script
    assert 'command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REMOTE_URL=' in script
    assert 'command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS=' in script
    assert 'command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS=' in script
    assert 'command_prefix+=" PANTHEON_STATUS_ROOT_HOST=' in script
    assert 'PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \\' in script
    assert 'PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL}" \\' in script
    assert 'PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL}" \\' in script
    assert 'PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}" \\' in script
    assert 'PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}" \\' in script
    assert 'PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST}" \\' in script
