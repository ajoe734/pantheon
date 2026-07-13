from __future__ import annotations

import os
import json
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
    "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
    "PANTHEON_BFF_STUB_CAPABILITIES",
    "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
}
DEV_JWT_SECRET = "contract-jwt-secret-value-2026-000000"
CI_PROFILE_SECRET = "ci-agora-secret-value-2026-00000000"
KERNEL_PROFILE_SECRET = "kernel-profile-secret-value-2026-000000"


def _governed_profiles_json() -> str:
    return json.dumps(
        {
            "ci-agora": {
                "secret": CI_PROFILE_SECRET,
                "subject": "pantheon-dev-ci-agora",
                "roles": ["operator"],
                "tenant_id": "tenant-dev",
                "allowed_tenants": ["tenant-dev"],
                "capabilities": [],
                "mfa_verified": False,
            },
            "kernel": {
                "secret": KERNEL_PROFILE_SECRET,
                "subject": "kernel-operator",
                "roles": ["admin", "operator"],
                "tenant_id": "old-tenant",
                "allowed_tenants": ["old-tenant"],
                "capabilities": ["assistant.kernel.repair"],
                "mfa_verified": True,
            },
        },
        separators=(",", ":"),
    )


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


def _install_fake_gcloud(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    outputs = {
        "args": tmp_path / "gcloud.args",
        "env": tmp_path / "gcloud.env",
        "stdin": tmp_path / "gcloud.stdin",
    }
    fake = bin_dir / "gcloud"
    fake.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" > "${FAKE_GCLOUD_ARGS}"
env | sort > "${FAKE_GCLOUD_ENV}"
cat > "${FAKE_GCLOUD_STDIN}"
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir, outputs


def _non_dry_run(
    *,
    environment: str,
    component: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(DEPLOY_SCRIPT),
            "--environment",
            environment,
            "--component",
            component,
            "--sha",
            "strict-auth-contract",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _install_fake_kernel_runtime(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "kernel-bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker-up.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *" ps -q operator-bff"* ]]; then
  printf 'fake-container-id\\n'
elif [[ "$1" == "inspect" ]]; then
  inspect_json="${FAKE_INSPECT_ENV_JSON}"
  if [[ -n "${FAKE_ROLLBACK_INSPECT_ENV_JSON:-}" && -f "${FAKE_DOCKER_LOG}" ]] \
    && [[ "$(wc -l <"${FAKE_DOCKER_LOG}")" -ge 2 ]]; then
    inspect_json="${FAKE_ROLLBACK_INSPECT_ENV_JSON}"
  fi
  printf '%s\\n' "${inspect_json}"
elif [[ "$*" == *" up "* ]]; then
  printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\\n' \
    "${PANTHEON_ASSISTANT_KERNEL_ENABLED-unset}" \
    "${PANTHEON_BFF_JWT_ISSUER-unset}" \
    "${PANTHEON_BFF_TENANT_ID-unset}" \
    "${PANTHEON_BFF_MFA_REQUIRED-unset}" \
    "${PANTHEON_BFF_JWKS_URI-unset}" \
    "${PANTHEON_BFF_OIDC_DISCOVERY_URL-unset}" \
    "${PANTHEON_BFF_OIDC_ISSUER-unset}" \
    "${PANTHEON_BFF_OIDC_AUDIENCE-unset}" \
    "${PANTHEON_BFF_ROLE_MAP-unset}" \
    "${PANTHEON_BFF_DEFAULT_ROLE-unset}" >> "${FAKE_DOCKER_LOG}"
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
url="${!#}"
if [[ "$url" == */bff/me ]]; then
  if [[ "${FAKE_ME_INVALID:-false}" == "true" ]]; then
    exit 22
  fi
  if [[ "${FAKE_ROLLBACK_ME_INVALID:-false}" == "true" && -f "${FAKE_DOCKER_LOG}" ]] \
    && [[ "$(wc -l <"${FAKE_DOCKER_LOG}")" -ge 2 ]]; then
    exit 22
  fi
  printf '%s\\n' '{"data":{"roles":["admin","operator"],"currentUser":{"id":"kernel-operator","mfa_verified":true},"session":{"mfa_verified":true},"capabilities":["assistant.kernel.repair"],"tenant":{"id":"old-tenant","allowed_ids":["old-tenant"]}}}'
elif [[ "$url" == */bff/assistant/mode ]]; then
  count=0
  [[ -f "${FAKE_MODE_COUNT}" ]] && count="$(cat "${FAKE_MODE_COUNT}")"
  count=$((count + 1))
  printf '%s' "$count" > "${FAKE_MODE_COUNT}"
  if [[ "$count" -gt 1 && "${FAKE_POST_MODE_GOOD:-false}" == "true" ]]; then
    printf '%s\\n' '{"data":{"kernel_enabled":true,"control_mode":{"configured":true,"active":false,"state":"inactive"}}}'
  else
    printf '%s\\n' '{"data":{"kernel_enabled":false,"control_mode":{"configured":true,"active":false,"state":"inactive"}}}'
  fi
else
  exit 22
fi
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    return bin_dir, docker_log


def _kernel_runtime_env(tmp_path: Path, bin_dir: Path, docker_log: Path, **overrides: str) -> dict[str, str]:
    previous_environment = [
        "PANTHEON_ENV=dev",
        "PANTHEON_DEPLOYMENT_STAGE=dev",
        "PANTHEON_ASSISTANT_KERNEL_ENABLED=false",
        "PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH=/data/bff/assistant-control-mode.json",
        "PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS=300",
        "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH=pbkdf2_sha256$260000$abcd$efgh",
        "PANTHEON_BFF_AUTH_STUB=false",
        "PANTHEON_BFF_AUTH_MODE=strict",
        "PANTHEON_BFF_STUB_CAPABILITIES=",
        "PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS=",
        f"PANTHEON_BFF_JWT_SECRET={DEV_JWT_SECRET}",
        "PANTHEON_BFF_JWT_ISSUER=old-issuer",
        "PANTHEON_BFF_JWT_AUDIENCE=old-audience",
        "PANTHEON_BFF_JWKS_URI=https://idp.invalid/jwks",
        "PANTHEON_BFF_OIDC_DISCOVERY_URL=https://idp.invalid/.well-known/openid-configuration",
        "PANTHEON_BFF_OIDC_ISSUER=https://idp.invalid/",
        "PANTHEON_BFF_OIDC_AUDIENCE=pantheon-dev",
        f"PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON={_governed_profiles_json()}",
        "PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS=900",
        "PANTHEON_BFF_TENANT_ID=old-tenant",
        "PANTHEON_BFF_ALLOWED_TENANTS=old-tenant",
        "PANTHEON_BFF_ROLE_CLAIMS=roles,role",
        "PANTHEON_BFF_ROLE_MAP=external-risk=risk_owner",
        "PANTHEON_BFF_ROLE_MAP_MODE=passthrough",
        "PANTHEON_BFF_DEFAULT_ROLE=viewer",
        "PANTHEON_BFF_MFA_REQUIRED=false",
        "PANTHEON_BFF_MFA_CLAIMS=amr,acr,mfa,mfa_verified",
        "PANTHEON_BFF_MFA_VALUES=true,1,yes,mfa,otp,totp,webauthn",
        f"PANTHEON_STATUS_ROOT_HOST={tmp_path}",
        "PANTHEON_STATUS_ROOT_CONTAINER=/workspace/status-root",
    ]
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(docker_log),
        "FAKE_MODE_COUNT": str(tmp_path / "mode-count"),
        "BFF_AUTH_TOKEN": "signed-kernel-token",
        "PANTHEON_STATUS_ROOT_HOST": str(tmp_path),
        "PANTHEON_BFF_AUTH_STUB": "false",
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_STUB_CAPABILITIES": "",
        "FAKE_INSPECT_ENV_JSON": json.dumps(previous_environment, separators=(",", ":")),
    }
    for name in (
        "PANTHEON_ASSISTANT_KERNEL_ENABLED",
        "PANTHEON_BFF_JWT_SECRET",
        "PANTHEON_BFF_JWT_ISSUER",
        "PANTHEON_BFF_JWT_AUDIENCE",
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        "PANTHEON_BFF_TENANT_ID",
        "PANTHEON_BFF_ALLOWED_TENANTS",
    ):
        env.pop(name, None)
    env["PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON"] = _governed_profiles_json()
    env.update(overrides)
    return env


def test_dev_compose_defaults_to_strict_capability_free_auth() -> None:
    compose_source = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_source)
    env = compose["services"]["operator-bff"]["environment"]

    assert env["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"
    assert env["PANTHEON_DEPLOYMENT_STAGE"] == "${PANTHEON_DEPLOYMENT_STAGE:-dev}"
    assert env["PANTHEON_BFF_AUTH_STUB"] == "${PANTHEON_BFF_AUTH_STUB:-false}"
    assert env["PANTHEON_BFF_AUTH_MODE"] == "${PANTHEON_BFF_AUTH_MODE:-strict}"
    assert env["PANTHEON_BFF_STUB_CAPABILITIES"] == "${PANTHEON_BFF_STUB_CAPABILITIES:-}"
    assert env["PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS"] == "${PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS:-}"
    assert env["PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON"] == "${PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON:-}"
    assert env["PANTHEON_BFF_DEFAULT_ROLE"] == "${PANTHEON_BFF_DEFAULT_ROLE:-viewer}"
    for legacy_name in (
        "PANTHEON_BFF_OIDC_CLIENT_ID",
        "PANTHEON_BFF_OIDC_CLIENT_SECRET",
        "PANTHEON_BFF_DEV_LOGIN_ROLES",
        "PANTHEON_BFF_DEV_LOGIN_SUBJECT",
        "PANTHEON_BFF_DEV_LOGIN_MFA_VERIFIED",
    ):
        assert legacy_name not in env
    assert env["PANTHEON_BFF_TENANT_ID"] == "${PANTHEON_BFF_TENANT_ID:-tenant-dev}"
    assert env["PANTHEON_BFF_ALLOWED_TENANTS"] == "${PANTHEON_BFF_ALLOWED_TENANTS:-tenant-dev}"
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
    assert "dev_bff_profiled_login_configured=false" in result.stdout
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
            "requires both DEV_BFF_JWT_SECRET and governed client profiles",
        ),
        (
            {
                "DEV_BFF_JWT_SECRET": "   ",
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


def test_dev_deploy_rejects_legacy_shared_dev_login_config_without_printing_secrets() -> None:
    configured = {
        "DEV_BFF_JWT_SECRET": DEV_JWT_SECRET,
        "DEV_BFF_OIDC_CLIENT_ID": "contract-client-id",
        "DEV_BFF_OIDC_CLIENT_SECRET": "contract-client-secret",
    }
    result = _dry_run(**configured)

    assert result.returncode == 1
    assert "requires both DEV_BFF_JWT_SECRET and governed client profiles" in result.stderr
    for secret in configured.values():
        assert secret not in result.stdout
        assert secret not in result.stderr


def test_dev_deploy_accepts_governed_profile_config_without_printing_secrets() -> None:
    configured = {
        "DEV_BFF_JWT_SECRET": DEV_JWT_SECRET,
        "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON": _governed_profiles_json(),
    }
    result = _dry_run(**configured)

    assert result.returncode == 0, result.stderr
    assert "dev_bff_profiled_login_configured=true" in result.stdout
    for secret in configured.values():
        assert secret not in result.stdout
        assert secret not in result.stderr


@pytest.mark.parametrize("invalid_kind", ["short-jwt", "invalid-extra-profile", "duplicate-secret"])
def test_dev_deploy_canonical_validator_rejects_entire_invalid_map(
    invalid_kind: str,
) -> None:
    profiles = json.loads(_governed_profiles_json())
    jwt_secret = DEV_JWT_SECRET
    if invalid_kind == "short-jwt":
        jwt_secret = "too-short"
    elif invalid_kind == "invalid-extra-profile":
        profiles["invalid-extra"] = {
            **profiles["kernel"],
            "secret": "invalid-extra-secret-value-2026-000000",
            "subject": "invalid-extra",
            "unknown_field": True,
        }
    else:
        profiles["kernel"]["secret"] = profiles["ci-agora"]["secret"]

    result = _dry_run(
        DEV_BFF_JWT_SECRET=jwt_secret,
        DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON=json.dumps(profiles),
    )

    assert result.returncode == 1
    assert "failed canonical validation" in result.stderr
    assert jwt_secret not in result.stderr


def test_invalid_dev_auth_config_fails_before_gcloud_auth_or_ssh(tmp_path: Path) -> None:
    bin_dir, outputs = _install_fake_gcloud(tmp_path)
    profiles = json.loads(_governed_profiles_json())
    profiles["invalid-extra"] = {**profiles["kernel"], "unexpected": "rejected"}
    env = _deploy_env(
        DEV_BFF_JWT_SECRET=DEV_JWT_SECRET,
        DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON=json.dumps(profiles),
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_GCLOUD_ARGS=str(outputs["args"]),
        FAKE_GCLOUD_ENV=str(outputs["env"]),
        FAKE_GCLOUD_STDIN=str(outputs["stdin"]),
    )

    result = _non_dry_run(environment="dev", component="bff", env=env)

    assert result.returncode == 1
    assert "failed canonical validation" in result.stderr
    assert not outputs["args"].exists()


@pytest.mark.parametrize("component", ["auto", "root", "bff"])
def test_non_dry_run_dev_without_credentials_fails_before_gcloud(
    tmp_path: Path, component: str
) -> None:
    bin_dir, outputs = _install_fake_gcloud(tmp_path)
    env = _deploy_env(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_GCLOUD_ARGS=str(outputs["args"]),
        FAKE_GCLOUD_ENV=str(outputs["env"]),
        FAKE_GCLOUD_STDIN=str(outputs["stdin"]),
    )

    result = _non_dry_run(environment="dev", component=component, env=env)

    assert result.returncode == 1
    assert "dev deployment is blocked until JWT secret" in result.stderr
    assert not outputs["args"].exists()


def test_dev_credentials_use_ssh_stdin_and_never_gcloud_argv_or_environment(
    tmp_path: Path,
) -> None:
    bin_dir, outputs = _install_fake_gcloud(tmp_path)
    secrets = {
        "DEV_BFF_JWT_SECRET": "sentinel-jwt-secret-value-2026-00000000",
        "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON": _governed_profiles_json().replace(
            CI_PROFILE_SECRET, "sentinel-profile-secret-value-2026-000000"
        ),
        "GITHUB_TOKEN": "sentinel-github-token",
        "DEV_MANAGEMENT_AI_DB_PASSWORD": "sentinel-database-password",
    }
    ambient_secrets = {
        "PANTHEON_BFF_JWT_SECRET": "sentinel-ambient-jwt",
        "PANTHEON_BFF_OIDC_CLIENT_ID": "sentinel-ambient-client",
        "PANTHEON_BFF_OIDC_CLIENT_SECRET": "sentinel-ambient-client-secret",
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON": "sentinel-ambient-profile",
        "PANTHEON_MANAGEMENT_AI_DB_PASSWORD": "sentinel-ambient-db-password",
    }
    env = _deploy_env(
        **secrets,
        **ambient_secrets,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_GCLOUD_ARGS=str(outputs["args"]),
        FAKE_GCLOUD_ENV=str(outputs["env"]),
        FAKE_GCLOUD_STDIN=str(outputs["stdin"]),
    )

    result = _non_dry_run(environment="dev", component="bff", env=env)

    assert result.returncode == 0, result.stderr
    args = outputs["args"].read_text(encoding="utf-8")
    child_env = outputs["env"].read_text(encoding="utf-8")
    stdin = outputs["stdin"].read_text(encoding="utf-8")
    assert "--command=bash -s" in args
    for secret in secrets.values():
        assert secret not in args
        assert secret not in child_env
        if secret.startswith("{"):
            assert "sentinel-profile-secret-value-2026-000000" in stdin
        else:
            assert secret in stdin
    for secret in ambient_secrets.values():
        assert secret not in args
        assert secret not in child_env
        assert secret not in stdin


@pytest.mark.parametrize("component", ["auto", "control", "exec", "all"])
def test_staging_never_receives_exported_dev_credentials(
    tmp_path: Path, component: str
) -> None:
    bin_dir, outputs = _install_fake_gcloud(tmp_path)
    secrets = {
        "DEV_BFF_JWT_SECRET": "staging-must-not-see-jwt",
        "DEV_BFF_OIDC_CLIENT_ID": "staging-must-not-see-client",
        "DEV_BFF_OIDC_CLIENT_SECRET": "staging-must-not-see-secret",
        "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON": '{"ci":{"secret":"staging-must-not-see-profile"}}',
        "DEV_MANAGEMENT_AI_DB_PASSWORD": "staging-must-not-see-db-password",
        "DEV_MANAGEMENT_AI_DATABASE_URL": "staging-must-not-see-database-url",
        "PANTHEON_BFF_JWT_SECRET": "staging-must-not-see-ambient-jwt",
        "PANTHEON_BFF_OIDC_CLIENT_SECRET": "staging-must-not-see-ambient-client-secret",
        "PANTHEON_MANAGEMENT_AI_DB_PASSWORD": "staging-must-not-see-ambient-db-password",
    }
    env = _deploy_env(
        **secrets,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_GCLOUD_ARGS=str(outputs["args"]),
        FAKE_GCLOUD_ENV=str(outputs["env"]),
        FAKE_GCLOUD_STDIN=str(outputs["stdin"]),
    )

    result = _non_dry_run(environment="staging-live", component=component, env=env)

    assert result.returncode == 0, result.stderr
    captured = "".join(
        outputs[name].read_text(encoding="utf-8") for name in ("args", "env", "stdin")
    )
    for secret in secrets.values():
        assert secret not in captured


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


def test_kernel_enable_script_refuses_to_drop_runtime_auth_secrets(tmp_path: Path) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    env = _kernel_runtime_env(
        tmp_path,
        bin_dir,
        docker_log,
    )
    captured = [
        item
        for item in json.loads(env["FAKE_INSPECT_ENV_JSON"])
        if not item.startswith("PANTHEON_BFF_JWT_SECRET=")
    ]
    env["FAKE_INSPECT_ENV_JSON"] = json.dumps(captured, separators=(",", ":"))

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "lacks required trust snapshot key PANTHEON_BFF_JWT_SECRET" in result.stderr


def test_kernel_enable_validates_privileged_identity_before_container_mutation() -> None:
    source = (REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh").read_text(
        encoding="utf-8"
    )

    assert source.index('"${BFF_BASE_URL}/bff/me"') < source.index("--force-recreate")
    assert 'assistant.kernel.debug" or . == "assistant.kernel.repair' in source
    assert '-H "@${auth_header}"' in source
    assert '-H "Authorization: Bearer ${BFF_AUTH_TOKEN}"' not in source


def test_kernel_enable_rejects_explicit_disable_without_mutation(tmp_path: Path) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=_kernel_runtime_env(
            tmp_path,
            bin_dir,
            docker_log,
            PANTHEON_ASSISTANT_KERNEL_ENABLED="false",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must be exactly true" in result.stderr
    assert not docker_log.exists()


def test_kernel_enable_rejects_privileged_default_role_without_mutation(
    tmp_path: Path,
) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    env = _kernel_runtime_env(tmp_path, bin_dir, docker_log)
    captured = [
        "PANTHEON_BFF_DEFAULT_ROLE=operator"
        if item.startswith("PANTHEON_BFF_DEFAULT_ROLE=")
        else item
        for item in json.loads(env["FAKE_INSPECT_ENV_JSON"])
    ]
    env["FAKE_INSPECT_ENV_JSON"] = json.dumps(captured, separators=(",", ":"))
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "requires PANTHEON_BFF_DEFAULT_ROLE=viewer" in result.stderr
    assert not docker_log.exists()


def test_kernel_enable_rolls_back_captured_policy_when_postcondition_fails(tmp_path: Path) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=_kernel_runtime_env(
            tmp_path,
            bin_dir,
            docker_log,
            PANTHEON_ASSISTANT_KERNEL_ENABLED="true",
            FAKE_POST_MODE_GOOD="false",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "rolling operator-bff back" in result.stderr
    assert "policy rollback completed" in result.stderr
    mutations = docker_log.read_text(encoding="utf-8").splitlines()
    preserved_trust = (
        "old-issuer|old-tenant|false|https://idp.invalid/jwks|"
        "https://idp.invalid/.well-known/openid-configuration|https://idp.invalid/|"
        "pantheon-dev|external-risk=risk_owner|viewer"
    )
    assert mutations[0] == f"true|{preserved_trust}"
    assert mutations[1] == f"false|{preserved_trust}"


@pytest.mark.parametrize("proof_failure", ["environment", "previous_credential"])
def test_kernel_enable_fails_when_authoritative_rollback_proof_fails(
    tmp_path: Path, proof_failure: str
) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    env = _kernel_runtime_env(
        tmp_path,
        bin_dir,
        docker_log,
        PANTHEON_ASSISTANT_KERNEL_ENABLED="true",
        FAKE_POST_MODE_GOOD="false",
    )
    if proof_failure == "environment":
        rollback_environment = json.loads(env["FAKE_INSPECT_ENV_JSON"])
        rollback_environment = [
            "PANTHEON_BFF_JWT_ISSUER=wrong-rollback-issuer"
            if item.startswith("PANTHEON_BFF_JWT_ISSUER=")
            else item
            for item in rollback_environment
        ]
        env["FAKE_ROLLBACK_INSPECT_ENV_JSON"] = json.dumps(
            rollback_environment, separators=(",", ":")
        )
    else:
        env["FAKE_ROLLBACK_ME_INVALID"] = "true"

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "rollback container environment or previous-credential" in result.stderr
    assert "operator-bff policy rollback failed" in result.stderr
    assert "policy rollback completed" not in result.stderr


def test_kernel_enable_accepts_only_exact_enabled_configured_inactive_postcondition(tmp_path: Path) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=_kernel_runtime_env(
            tmp_path,
            bin_dir,
            docker_log,
            FAKE_POST_MODE_GOOD="true",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"kernel_enabled": true' in result.stdout
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        "true|old-issuer|old-tenant|false|https://idp.invalid/jwks|"
        "https://idp.invalid/.well-known/openid-configuration|https://idp.invalid/|"
        "pantheon-dev|external-risk=risk_owner|viewer"
    ]


def test_kernel_enable_rejects_invalid_identity_before_docker(tmp_path: Path) -> None:
    bin_dir, docker_log = _install_fake_kernel_runtime(tmp_path)
    env = _kernel_runtime_env(
        tmp_path,
        bin_dir,
        docker_log,
        BFF_AUTH_TOKEN="invalid-near-viewer-token",
        FAKE_ME_INVALID="true",
    )

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "enable_management_ai_dev_kernel.sh")],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "rejected before operator-bff restart" in result.stderr
    assert not docker_log.exists()


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
    assert "secrets.DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" in workflow
    assert "secrets.DEV_BFF_CI_CLIENT_ID" in workflow
    assert "secrets.DEV_BFF_CI_CLIENT_SECRET" in workflow
    assert 'POST "${DEV_BFF_URL}/bff/auth/dev-login"' in workflow
    assert "dev_auth_validation.py profiles" in workflow
    assert "dev_auth_validation.py login-response" in workflow
    assert "dev_auth_validation.py workshop-response" in workflow
    assert "bff_auth_token=\"$(" not in workflow
    assert "workshop_id=\"$(" not in workflow
    assert "cat \"${bff_auth_token_file}\"" in workflow
    assert '-H "@${auth_header}"' in workflow
    assert '--arg client_secret "${DEV_BFF_CI_CLIENT_SECRET}"' not in workflow
    assert "invalid token_type or malformed compact JWT" in workflow
    assert "invalid workshop id" in workflow
    assert "pantheon-dev-ci-agora" in workflow
    assert ".data.capabilities == []" in workflow
    assert ".data.session.mfa_verified == false" in workflow
    assert "env.TARGET_ENV == 'dev'" in workflow
    parsed = yaml.safe_load(workflow)
    deploy_step = next(
        step
        for step in parsed["jobs"]["deploy-manual"]["steps"]
        if step.get("name") == "Deploy requested VM stack"
    )
    for name in (
        "DEV_BFF_JWT_SECRET",
        "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        "DEV_MANAGEMENT_AI_DB_PASSWORD",
        "DEV_MANAGEMENT_AI_DATABASE_URL",
    ):
        assert "env.TARGET_ENV == 'dev'" in deploy_step["env"][name]
    assert "Require dev short-lived auth qualification prerequisites" in workflow
    assert "Dev deployment is BLOCKED" in workflow
    assert "No deployment was attempted" in workflow
    assert "Agora restart smoke is BLOCKED" in workflow
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
        "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON": " ",
        "DEV_BFF_CI_CLIENT_ID": " ",
        "DEV_BFF_CI_CLIENT_SECRET": "",
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


def test_nonprod_workflow_full_map_validator_rejects_invalid_unselected_profile(
    tmp_path: Path,
) -> None:
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
    profiles = json.loads(_governed_profiles_json())
    profiles["kernel"]["unexpected"] = True
    summary = tmp_path / "summary.md"

    result = subprocess.run(
        ["bash", "-c", preflight["run"]],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DEV_BFF_JWT_SECRET": DEV_JWT_SECRET,
            "DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON": json.dumps(profiles),
            "DEV_BFF_CI_CLIENT_ID": "ci-agora",
            "DEV_BFF_CI_CLIENT_SECRET": CI_PROFILE_SECRET,
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "canonical full-map/JWT/CI profile validation failed" in summary.read_text(
        encoding="utf-8"
    )
    assert CI_PROFILE_SECRET not in result.stdout
    assert CI_PROFILE_SECRET not in result.stderr


def test_nonprod_workflow_rejects_control_character_token_before_remote_restart(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(
            encoding="utf-8"
        )
    )
    smoke = next(
        step
        for step in workflow["jobs"]["deploy-manual"]["steps"]
        if step.get("name") == "Dev Agora workshop restart persistence smoke"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud_marker = tmp_path / "gcloud-called"
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"/bff/auth/dev-login"* ]]; then
  printf '%s\\n' '{"token_type":"bearer","access_token":"abc.def.ghi\\n::set-env name=PWNED::yes"}'
else
  printf '%s\\n' '{}'
fi
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(
        "#!/usr/bin/env bash\nprintf called > \"${GCLOUD_MARKER}\"\n",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-c", smoke["run"]],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DEV_BFF_URL": "https://bff.invalid",
            "DEV_BFF_CI_CLIENT_ID": "ci-agora",
            "DEV_BFF_CI_CLIENT_SECRET": "ci-secret-value",
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_STEP_SUMMARY": str(summary),
            "GCLOUD_MARKER": str(gcloud_marker),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "malformed compact JWT" in result.stdout
    assert "::set-env name=PWNED" not in result.stdout
    assert "::set-env name=PWNED" not in result.stderr
    assert not gcloud_marker.exists()


def test_kernel_runbook_manual_equivalent_preserves_strict_auth_secrets() -> None:
    runbook = (
        REPO_ROOT / "docs" / "deployment" / "management-ai-dev-kernel-control-mode.md"
    ).read_text(encoding="utf-8")
    manual = runbook.split("Manual equivalent:", 1)[1].split("```bash", 1)[1].split(
        "```", 1
    )[0]

    assert "export PANTHEON_BFF_JWT_SECRET=" in manual
    assert "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" in runbook
    assert "calls `/bff/me` before any container mutation" in runbook
    assert "EXIT cleanup requires HTTP `202`" in runbook
    assert "authoritative inactive" in runbook


def test_strict_dev_still_exposes_only_the_exact_read_only_public_viewer() -> None:
    source = (REPO_ROOT / "services" / "control-plane" / "bff" / "main.py").read_text(
        encoding="utf-8"
    )

    assert '_PUBLIC_BROWSER_VIEWER_TOKEN = f"Bearer {_PUBLIC_BROWSER_OPERATOR_ID}:viewer"' in source
    assert '_PUBLIC_BROWSER_ALLOWED_ENVIRONMENTS = {"dev", "local"}' in source
    assert '_PUBLIC_BROWSER_READ_METHODS = {"GET", "HEAD"}' in source
    assert 'if str(authorization or "") != _PUBLIC_BROWSER_VIEWER_TOKEN:' in source
