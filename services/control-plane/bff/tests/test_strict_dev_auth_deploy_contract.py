from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"
PRIVILEGED_SCRIPTS = (
    REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh",
    REPO_ROOT / "scripts" / "smoke_management_ai_control_mode_queue.sh",
    REPO_ROOT / "scripts" / "smoke_management_ai_openclaw_repair_e2e.sh",
)
AUTH_OVERRIDE_KEYS = {
    "DEV_BFF_AUTH_STUB",
    "DEV_BFF_AUTH_MODE",
    "DEV_BFF_STUB_CAPABILITIES",
    "DEV_BFF_JWT_SECRET",
    "DEV_BFF_OIDC_CLIENT_ID",
    "DEV_BFF_OIDC_CLIENT_SECRET",
    "PANTHEON_BFF_STUB_CAPABILITIES",
}


def _deploy_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in AUTH_OVERRIDE_KEYS:
        env.pop(key, None)
    env.update(overrides)
    return env


def _dry_run(**overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--component",
            "bff",
            "--sha",
            "strict-auth-contract",
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        env=_deploy_env(**overrides),
        check=False,
        capture_output=True,
        text=True,
    )


def test_dev_compose_defaults_to_strict_capability_free_auth() -> None:
    compose_source = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_source)
    env = compose["services"]["operator-bff"]["environment"]

    assert env["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"
    assert env["PANTHEON_BFF_AUTH_STUB"] == "${PANTHEON_BFF_AUTH_STUB:-false}"
    assert env["PANTHEON_BFF_AUTH_MODE"] == "${PANTHEON_BFF_AUTH_MODE:-strict}"
    assert env["PANTHEON_BFF_STUB_CAPABILITIES"] == "${PANTHEON_BFF_STUB_CAPABILITIES:-}"
    assert env["PANTHEON_BFF_DEV_LOGIN_ROLES"] == "${PANTHEON_BFF_DEV_LOGIN_ROLES:-operator,reviewer,approver}"
    operator_bff_lines = compose_source.splitlines()
    operator_bff_start = operator_bff_lines.index("  operator-bff:") + 1
    operator_bff_end = next(
        index
        for index in range(operator_bff_start, len(operator_bff_lines))
        if operator_bff_lines[index].strip()
        and len(operator_bff_lines[index]) - len(operator_bff_lines[index].lstrip()) < 4
    )
    operator_bff_source = "\n".join(operator_bff_lines[operator_bff_start:operator_bff_end])
    assert operator_bff_source.count("\n      DATABASE_URL:") == 1


def test_dev_deploy_dry_run_proves_strict_auth_boundary() -> None:
    result = _dry_run()

    assert result.returncode == 0, result.stderr
    assert "dev_bff_auth_stub=false" in result.stdout
    assert "dev_bff_auth_mode=strict" in result.stdout
    assert "dev_bff_dev_login_configured=false" in result.stdout
    assert "dev_bff_stub_capabilities_configured=false" in result.stdout


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"DEV_BFF_AUTH_STUB": "true"}, "DEV_BFF_AUTH_STUB=false"),
        ({"DEV_BFF_AUTH_STUB": "false "}, "DEV_BFF_AUTH_STUB=false"),
        ({"DEV_BFF_AUTH_MODE": "permissive"}, "DEV_BFF_AUTH_MODE=strict"),
        ({"DEV_BFF_AUTH_MODE": "STRICT"}, "DEV_BFF_AUTH_MODE=strict"),
        (
            {"DEV_BFF_STUB_CAPABILITIES": "assistant.kernel.repair"},
            "DEV_BFF_STUB_CAPABILITIES to be empty",
        ),
        (
            {"PANTHEON_BFF_STUB_CAPABILITIES": "assistant.kernel.debug"},
            "PANTHEON_BFF_STUB_CAPABILITIES to be empty",
        ),
        (
            {"DEV_BFF_JWT_SECRET": "partial-secret"},
            "dev-login requires JWT secret, client id, and client secret together",
        ),
        (
            {"DEV_BFF_OIDC_CLIENT_ID": "partial-client"},
            "dev-login requires JWT secret, client id, and client secret together",
        ),
        (
            {"DEV_BFF_OIDC_CLIENT_SECRET": "partial-secret"},
            "dev-login requires JWT secret, client id, and client secret together",
        ),
        (
            {
                "DEV_BFF_JWT_SECRET": "   ",
                "DEV_BFF_OIDC_CLIENT_ID": "contract-client",
                "DEV_BFF_OIDC_CLIENT_SECRET": "contract-secret",
            },
            "DEV_BFF_JWT_SECRET must not be whitespace-only",
        ),
    ],
)
def test_dev_deploy_rejects_insecure_auth_overrides(
    override: dict[str, str], message: str
) -> None:
    result = _dry_run(**override)

    assert result.returncode == 1
    assert message in result.stderr
    assert "dry run" not in result.stdout


def test_dev_deploy_accepts_complete_dev_login_config_without_printing_secrets() -> None:
    configured = {
        "DEV_BFF_JWT_SECRET": "contract-jwt-secret",
        "DEV_BFF_OIDC_CLIENT_ID": "contract-client-id",
        "DEV_BFF_OIDC_CLIENT_SECRET": "contract-client-secret",
    }
    result = _dry_run(**configured)

    assert result.returncode == 0, result.stderr
    assert "dev_bff_dev_login_configured=true" in result.stdout
    for secret in configured.values():
        assert secret not in result.stdout
        assert secret not in result.stderr


@pytest.mark.parametrize("script", PRIVILEGED_SCRIPTS)
def test_privileged_management_scripts_require_explicit_auth_token(script: Path) -> None:
    env = os.environ.copy()
    env.pop("BFF_AUTH_TOKEN", None)
    result = subprocess.run(
        ["bash", str(script)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "explicit short-lived privileged BFF JWT" in result.stderr


@pytest.mark.parametrize("script", PRIVILEGED_SCRIPTS)
def test_privileged_management_scripts_reject_public_viewer(script: Path) -> None:
    result = subprocess.run(
        ["bash", str(script)],
        cwd=REPO_ROOT,
        env={**os.environ, "BFF_AUTH_TOKEN": "pantheon-dev-browser:viewer"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "public browser viewer token is read-only" in result.stderr


def test_kernel_enable_script_refuses_to_drop_runtime_auth_secrets() -> None:
    env = os.environ.copy()
    env["BFF_AUTH_TOKEN"] = "signed-token-placeholder"
    env["PANTHEON_BFF_AUTH_STUB"] = "false"
    env["PANTHEON_BFF_AUTH_MODE"] = "strict"
    env["PANTHEON_BFF_STUB_CAPABILITIES"] = ""
    for key in (
        "PANTHEON_BFF_JWT_SECRET",
        "PANTHEON_BFF_OIDC_CLIENT_ID",
        "PANTHEON_BFF_OIDC_CLIENT_SECRET",
    ):
        env.pop(key, None)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "requires governed JWT and dev-login client secrets" in result.stderr


def test_privileged_management_scripts_contain_no_fallback_bearer() -> None:
    for script in PRIVILEGED_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert 'BFF_AUTH_TOKEN="${BFF_AUTH_TOKEN:-}"' in source
        assert "pantheon-dev-browser:admin" not in source
        assert "pantheon-dev-browser:operator" not in source
        assert "assistant.kernel.debug,assistant.kernel.repair}" not in source


def test_nonprod_workflow_uses_dev_login_instead_of_a_tracked_privileged_token() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "agora-deploy-smoke:operator" not in workflow
    assert "secrets.DEV_BFF_JWT_SECRET" in workflow
    assert "secrets.DEV_BFF_OIDC_CLIENT_ID" in workflow
    assert "secrets.DEV_BFF_OIDC_CLIENT_SECRET" in workflow
    assert 'POST "${DEV_BFF_URL}/bff/auth/dev-login"' in workflow
    assert "bff_auth_token=\"$(jq -er '.access_token'" in workflow
    assert 'Authorization: Bearer ${bff_auth_token}' in workflow
    assert "Require dev short-lived auth qualification prerequisites" in workflow
    assert "Dev deployment is BLOCKED" in workflow
    assert "No deployment was attempted" in workflow
    assert "Privileged Agora restart smoke is BLOCKED" in workflow
    assert "Skipping privileged Agora restart smoke" not in workflow
    assert workflow.index("Require dev short-lived auth qualification prerequisites") < workflow.index(
        "Authenticate to Google Cloud via Workload Identity Federation"
    ) < workflow.index("Deploy requested VM stack")


def test_nonprod_workflow_missing_dev_login_is_a_hard_block(tmp_path: Path) -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(
            encoding="utf-8"
        )
    )
    preflight = next(
        step
        for step in workflow["jobs"]["deploy-manual"]["steps"]
        if step.get("name") == "Require dev short-lived auth qualification prerequisites"
    )
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "DEV_BFF_JWT_SECRET": "",
        "DEV_BFF_OIDC_CLIENT_ID": " ",
        "DEV_BFF_OIDC_CLIENT_SECRET": "",
        "GITHUB_STEP_SUMMARY": str(summary),
    }

    result = subprocess.run(
        ["bash", "-c", preflight["run"]],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Dev deployment is BLOCKED" in result.stdout
    summary_text = summary.read_text(encoding="utf-8")
    assert "Outcome: **BLOCKED**" in summary_text
    assert "No deployment was attempted" in summary_text


def test_strict_dev_still_exposes_only_the_exact_read_only_public_viewer() -> None:
    source = (REPO_ROOT / "services" / "control-plane" / "bff" / "main.py").read_text(
        encoding="utf-8"
    )

    assert '_PUBLIC_BROWSER_VIEWER_TOKEN = f"Bearer {_PUBLIC_BROWSER_OPERATOR_ID}:viewer"' in source
    assert '_PUBLIC_BROWSER_ALLOWED_ENVIRONMENTS = {"dev", "local"}' in source
    assert '_PUBLIC_BROWSER_READ_METHODS = {"GET", "HEAD"}' in source
    assert 'if str(authorization or "") != _PUBLIC_BROWSER_VIEWER_TOKEN:' in source
