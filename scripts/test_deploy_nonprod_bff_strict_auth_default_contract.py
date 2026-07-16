import json
import os
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml"

_TEST_SHA = "0" * 40
_TEST_LEASE_ID = "11111111-1111-4111-8111-111111111111"


def _valid_lease_env(sha: str = _TEST_SHA) -> dict:
    """Dev deploys sit behind the shared environment lease guard, which is the
    outermost dev gate; give these auth-contract tests a valid lease so they
    exercise the strict-auth gates behind it."""
    state = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(
        {
            "schemaVersion": 1,
            "repository": "ajoe734/execute-plans",
            "branch": "environment-coordination",
            "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "leaseId": _TEST_LEASE_ID,
            "expectedBackendSha": sha,
        },
        state,
    )
    state.close()
    return {
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": state.name,
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": _TEST_LEASE_ID,
    }


def test_nonprod_deploy_defaults_to_strict_bff_auth() -> None:
    """The dev deploy script always passes an explicit AUTH_STUB/AUTH_MODE
    value into the compose environment, which overrides docker-compose.yml's
    own strict default regardless of what that file says. Regression guard
    for LOOP-PROD-AUTH-001: the script's own default must also be strict, or
    every dev deploy silently re-forces stub/permissive auth."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"' in script
    assert 'DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"' in script
    assert 'DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-true}"' not in script
    assert 'DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-permissive}"' not in script


def test_workflow_rejects_refs_that_predate_the_strict_auth_contract() -> None:
    """The workflow definition comes from the dispatch ref, but checkout can
    replace the workspace with an older target ref before the deploy script is
    executed. Keep a workflow-level guard ahead of the remote deploy so an old
    script cannot silently restore permissive/stub auth."""
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    gate = workflow.index("- name: Enforce dev auth deployment floor")
    deploy = workflow.index("- name: Deploy dev VM stack under lease")
    assert gate < deploy

    for secret in (
        "secrets.DEV_BFF_JWT_SECRET",
        "secrets.DEV_BFF_OIDC_CLIENT_ID",
        "secrets.DEV_BFF_OIDC_CLIENT_SECRET",
    ):
        assert secret in workflow[gate:deploy]

    for marker in (
        'case "${DEV_AUTH_PROFILE}" in',
        'DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"',
        'DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"',
        "no governed verifier/dev-login credentials",
        "assert_bff_auth_gate",
    ):
        assert marker in workflow[gate:deploy]

    assert "refusing to run any target ref before strict auth can be verified" in workflow[gate:deploy]


def test_nonprod_workflow_has_bounded_dev_permissive_stub_profile() -> None:
    """Manual proof runs may deploy an older exact SHA, so the workflow must
    pass an atomic auth pair into that SHA's deploy script instead of relying
    on either the script's historical defaults or the remote .env file."""
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "dev_auth_profile:" in workflow
    assert "default: strict" in workflow
    assert "- permissive-stub" in workflow
    assert "export DEV_BFF_AUTH_STUB=false" in workflow
    assert "export DEV_BFF_AUTH_MODE=strict" in workflow
    assert "export DEV_BFF_AUTH_STUB=true" in workflow
    assert "export DEV_BFF_AUTH_MODE=permissive" in workflow
    # The auth profile is a dev-job concern only: the independent staging job
    # must not read it, so a permissive-stub selection can never leak into
    # staging deployments.
    staging = workflow[workflow.index("  deploy-staging-live:"):]
    assert "DEV_AUTH_PROFILE" not in staging


def _run_deploy_script(
    extra_env: dict,
    *extra_args: str,
) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("DEV_BFF_")
        and not k.startswith("DEV_OPENCLAW_ADAPTER_")
        and not k.startswith("PANTHEON_DEV_ENVIRONMENT_LEASE")
    }
    env.update(_valid_lease_env())
    env.update(extra_env)
    return subprocess.run(
        [
            "bash",
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            "0" * 40,
            "--project-id",
            "test-project",
            *extra_args,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_strict_cutover_refuses_to_deploy_without_verifier_credentials() -> None:
    """LOOP-PROD-AUTH-001 review: strict auth cutover must fail before
    touching the VM (not just fail post-hoc) when no governed dev-login
    verifier credentials are configured."""
    result = _run_deploy_script({})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "strict auth cutover requested" in result.stderr
    assert "no governed verifier/dev-login credentials" in result.stderr
    # Must fail before reaching the gcloud requirement check, i.e. this is a
    # dedicated preflight, not an accidental side effect of a later step.
    assert "gcloud is required" not in result.stderr


def test_strict_cutover_proceeds_past_credential_preflight_when_configured() -> None:
    result = _run_deploy_script(
        {
            "DEV_BFF_JWT_SECRET": "test-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "test-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "test-client-secret",
            "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": "test-service-token",
        }
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "strict auth cutover requested" not in result.stderr
    # With credentials satisfied, the script should proceed past our
    # dedicated preflight into actual gcloud usage (which then fails in this
    # sandbox for unrelated reasons — no live GCP credentials).
    assert "no governed verifier/dev-login credentials" not in result.stderr
    assert "gcloud" in result.stderr.lower()


def test_permissive_opt_out_does_not_require_verifier_credentials() -> None:
    result = _run_deploy_script(
        {
            "DEV_BFF_AUTH_MODE": "permissive",
            "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": "false",
        }
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "strict auth cutover requested" not in result.stderr
    assert "no governed verifier/dev-login credentials" not in result.stderr
    assert "gcloud" in result.stderr.lower()


def test_service_auth_refuses_deploy_without_human_provisioned_token() -> None:
    result = _run_deploy_script(
        {
            "DEV_BFF_JWT_SECRET": "test-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "test-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "test-client-secret",
        }
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "human-provisioned DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN" in result.stderr
    assert "empty or fabricated service credential" in result.stderr
    assert "gcloud is required" not in result.stderr


def test_service_auth_refuses_public_placeholder_token() -> None:
    result = _run_deploy_script(
        {
            "DEV_BFF_JWT_SECRET": "real-looking-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "real-looking-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "real-looking-client-secret",
            "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": (
                "replace-me-openclaw-adapter-service-token"
            ),
        }
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "empty or fabricated service credential" in result.stderr
    assert "gcloud is required" not in result.stderr


def test_service_auth_has_no_fabricated_deploy_default() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN="${DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}"' in script
    assert (
        'DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-true}"'
        in script
    )
    assert "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN:-pantheon" not in script


def test_service_token_is_redacted_from_dry_run_output() -> None:
    secret = "must-not-appear-in-deploy-output"
    result = _run_deploy_script(
        {"DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": secret},
        "--dry-run",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "dev_openclaw_adapter_service_token_configured=true" in result.stdout


def test_auth_gate_checks_hosted_posture_and_fixed_bearer_negative() -> None:
    """Regression guard: the post-deploy gate must assert both the hosted
    /bff/version auth posture and reject a fixed/arbitrary bearer token,
    not just check health/readyz/source SHA."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "assert_bff_auth_gate" in script
    assert 'posture = payload.get("config_posture")' in script
    assert 'posture.get("auth_stub") is False' in script
    assert 'posture.get("auth_mode") == "strict"' in script
    assert "posture = payload" in script
    assert "/bff/auth/dev-login" in script
    assert "Bearer op-fixed:operator:mfa" in script
    assert "accepted a fixed/arbitrary bearer token" in script
