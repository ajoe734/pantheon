import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


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

    gate = workflow.index("- name: Enforce dev strict-auth deployment floor")
    deploy = workflow.index("- name: Deploy requested VM stack")
    assert gate < deploy

    for secret in (
        "secrets.DEV_BFF_JWT_SECRET",
        "secrets.DEV_BFF_OIDC_CLIENT_ID",
        "secrets.DEV_BFF_OIDC_CLIENT_SECRET",
    ):
        assert secret in workflow[gate:deploy]

    for marker in (
        'DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"',
        'DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"',
        "no governed verifier/dev-login credentials",
        "assert_bff_auth_gate",
    ):
        assert marker in workflow[gate:deploy]

    assert "refusing to run any target ref before strict auth can be verified" in workflow[gate:deploy]


def _run_deploy_script(extra_env: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEV_BFF_")}
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
    result = _run_deploy_script({"DEV_BFF_AUTH_MODE": "permissive"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "strict auth cutover requested" not in result.stderr
    assert "no governed verifier/dev-login credentials" not in result.stderr
    assert "gcloud" in result.stderr.lower()


def test_auth_gate_checks_hosted_posture_and_fixed_bearer_negative() -> None:
    """Regression guard: the post-deploy gate must assert both the hosted
    /bff/version auth posture and reject a fixed/arbitrary bearer token,
    not just check health/readyz/source SHA."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "assert_bff_auth_gate" in script
    assert 'payload.get("auth_stub") is False' in script
    assert 'payload.get("auth_mode") == "strict"' in script
    assert "/bff/auth/dev-login" in script
    assert "Bearer op-fixed:operator:mfa" in script
    assert "accepted a fixed/arbitrary bearer token" in script
