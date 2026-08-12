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


def _dedicated_dev_login_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for identity in ("VIEWER", "APPROVER", "RISK_OWNER", "OPERATOR_A", "OPERATOR_B"):
        slug = identity.lower().replace("_", "-")
        env[f"DEV_BFF_DEV_LOGIN_{identity}_CLIENT_ID"] = f"test-{slug}-client"
        env[f"DEV_BFF_DEV_LOGIN_{identity}_CLIENT_SECRET"] = f"test-{slug}-secret"
    return env


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


def test_authority_private_keys_use_per_environment_protected_files() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    command_prefix = script[
        script.index('command_prefix="PANTHEON_DEPLOY_ENV='):
        script.index('command_prefix+=" bash -s"')
    ]

    assert "DEV_AUTHORITY_SIGNING_ENV_FILE" in script
    assert "STAGING_AUTHORITY_SIGNING_ENV_FILE" in script
    assert '[[ "$mode" == "600" ]]' in script
    assert 'docker compose --env-file "$env_file" --env-file "$PANTHEON_AUTHORITY_SIGNING_ENV_FILE"' in script
    assert "STAGING_AUTHORITY_SIGNING_ENV_FILE" in workflow
    assert "BRIDGE_SIGNING_PRIVATE_KEY" not in command_prefix
    assert "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY" not in command_prefix
    assert "secrets.BRIDGE_SIGNING_PRIVATE_KEY" not in workflow
    assert "secrets.PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY" not in workflow


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
        "secrets.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET",
        "secrets.DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET",
        "secrets.DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET",
        "secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET",
        "secrets.DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET",
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
    env.update(
        {
            "BRIDGE_SIGNING_KEY_ID": "test-bridge-v1",
            "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": '{"test-bridge-v1":"AA"}',
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID": "test-operator-v1",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": '{"test-operator-v1":"AA"}',
        }
    )
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
    env = _dedicated_dev_login_env()
    env.update(
        {
            "DEV_BFF_JWT_SECRET": "test-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "test-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "test-client-secret",
            "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": "test-service-token",
        }
    )
    result = _run_deploy_script(
        env
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "strict auth cutover requested" not in result.stderr
    # With credentials satisfied, the script proceeds past our dedicated
    # preflight into the host's gcloud launcher. The exact launcher error is
    # environment-specific (an unconfigured CLI or snap confinement).
    assert "no governed verifier/dev-login credentials" not in result.stderr
    assert "requires dedicated" not in result.stderr
    assert result.stderr


def test_strict_cutover_refuses_shared_credential_without_distinct_actors() -> None:
    result = _run_deploy_script(
        {
            "DEV_BFF_JWT_SECRET": "test-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "test-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "test-client-secret",
            "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": "test-service-token",
        }
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "requires dedicated viewer dev-login credentials" in result.stderr
    assert "distinct governed proof actors" in result.stderr
    assert "gcloud is required" not in result.stderr


def test_dedicated_dev_login_secrets_are_redacted_from_dry_run_output() -> None:
    env = _dedicated_dev_login_env()
    env["DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED"] = "false"
    result = _run_deploy_script(env, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    for value in env.values():
        if value.endswith("-secret"):
            assert value not in result.stdout
            assert value not in result.stderr
    for identity in ("viewer", "approver", "risk_owner", "operator_a", "operator_b"):
        assert f"dev_bff_dev_login_{identity}_configured=true" in result.stdout


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
    assert "requires dedicated" not in result.stderr
    assert result.stderr


def test_service_auth_refuses_deploy_without_human_provisioned_token() -> None:
    env = _dedicated_dev_login_env()
    env.update(
        {
            "DEV_BFF_JWT_SECRET": "test-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "test-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "test-client-secret",
        }
    )
    result = _run_deploy_script(env)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "human-provisioned DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN" in result.stderr
    assert "empty or fabricated service credential" in result.stderr
    assert "gcloud is required" not in result.stderr


def test_service_auth_refuses_public_placeholder_token() -> None:
    env = _dedicated_dev_login_env()
    env.update(
        {
            "DEV_BFF_JWT_SECRET": "real-looking-secret",
            "DEV_BFF_OIDC_CLIENT_ID": "real-looking-client",
            "DEV_BFF_OIDC_CLIENT_SECRET": "real-looking-client-secret",
            "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": (
                "replace-me-openclaw-adapter-service-token"
            ),
        }
    )
    result = _run_deploy_script(env)

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
    assert 'auth_stub = posture.get("auth_stub")' in script
    assert 'auth_mode = posture.get("auth_mode")' in script
    assert 'assert auth_stub is False' in script
    assert 'assert auth_mode == "strict"' in script
    assert "posture = payload" in script
    assert "/bff/auth/dev-login" in script
    assert "/bff/auth/readiness" in script
    assert 'assert data.get("authReady") is True' in script
    assert 'assert data.get("providerReady") is True' in script
    assert 'assert data.get("ready") is True' in script
    assert 'assert data.get("sourceCommitSha") == expected_sha' in script
    assert "Bearer op-fixed:operator:mfa" in script
    assert "accepted a fixed/arbitrary bearer token" in script


def test_auth_gate_checks_all_dedicated_identities_and_distinct_subjects() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "assert_dedicated_dev_login_identity" in script
    assert "for identity in viewer approver risk_owner operator_a operator_b" in script
    assert 'assert set(claims.get("roles") or []) == {expected_role}' in script
    assert 'assert claims.get("mfa_verified") is True' in script
    assert "len(set(subjects)) == len(subjects)" in script
    assert (
        '"${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}" '
        '"${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}"'
        in script
    )
    assert (
        '"${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" '
        '"${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}"'
        not in script[script.index("assert_bff_auth_gate()") : script.index("snapshot_remote_state()")]
    )
    assert 'DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED:-true}"' in script
    assert "PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED=" in script
    assert 'PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED}"' in script
    assert (
        "DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED: "
        "${{ vars.DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED || 'true' }}"
        in workflow
    )
    auth_floor = workflow[
        workflow.index("- name: Enforce dev auth deployment floor") :
        workflow.index(
            "- name: Authenticate to Google Cloud via Workload Identity Federation"
        )
    ]
    deploy_step = workflow[
        workflow.index("- name: Deploy dev VM stack under lease") :
        workflow.index("- name: Ensure governed dev paper baseline under lease")
    ]
    hosted_probe = workflow[
        workflow.index("- name: Dev canonical paper lifecycle hosted probe") :
        workflow.index("- name: Upload canonical paper lifecycle hosted evidence")
    ]

    for identity in ("VIEWER", "APPROVER", "RISK_OWNER", "OPERATOR_A", "OPERATOR_B"):
        client_id = f"DEV_BFF_DEV_LOGIN_{identity}_CLIENT_ID"
        client_secret = f"DEV_BFF_DEV_LOGIN_{identity}_CLIENT_SECRET"
        compose_client_id = f"PANTHEON_{client_id.removeprefix('DEV_')}"
        assert f'{client_secret}="${{{client_secret}:-}}"' in script
        assert f"PANTHEON_{client_id}" in script
        assert script.count(f"{compose_client_id}=") == 2
        secret_ref = f"secrets.{client_secret}"
        assert auth_floor.count(secret_ref) == 1
        assert deploy_step.count(secret_ref) == 1
        if identity == "OPERATOR_A":
            assert hosted_probe.count(secret_ref) == 1
            assert (
                "DEV_BFF_OIDC_CLIENT_SECRET: "
                "${{ secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET }}"
                in hosted_probe
            )
            assert workflow.count(secret_ref) == 4
        else:
            assert secret_ref not in hosted_probe
            assert workflow.count(secret_ref) == 3


def test_dev_deploy_plumbs_product_oidc_and_fail_closed_role_mapping() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    for name in (
        "DEV_BFF_JWT_ISSUER",
        "DEV_BFF_JWT_AUDIENCE",
        "DEV_BFF_JWKS_URI",
        "DEV_BFF_OIDC_DISCOVERY_URL",
        "DEV_BFF_OIDC_ISSUER",
        "DEV_BFF_OIDC_AUDIENCE",
        "DEV_BFF_ROLE_CLAIMS",
        "DEV_BFF_ROLE_MAP",
        "DEV_BFF_ROLE_MAP_MODE",
        "DEV_BFF_DEFAULT_ROLE",
        "DEV_BFF_MFA_CLAIMS",
        "DEV_BFF_REQUIRE_EMAIL_VERIFIED",
    ):
        assert name in script
        assert name in workflow

    assert 'DEV_BFF_DEFAULT_ROLE="${DEV_BFF_DEFAULT_ROLE:-viewer}"' in script
    assert "DEV_BFF_ROLE_CLAIMS || 'roles,role'" in workflow
    assert "pantheon-operator=operator" in workflow
    assert "DEV_BFF_ROLE_MAP_MODE || 'strict'" in workflow
    assert "DEV_BFF_DEFAULT_ROLE || 'viewer'" in workflow
    assert "https://securetoken.google.com/pantheon-lupin-dev-20260719" in workflow
    assert "pantheon-lupin-dev-20260719" in workflow
    assert "securetoken@system.gserviceaccount.com" in workflow
    assert "firebase.sign_in_second_factor" in workflow
    assert "DEV_BFF_REQUIRE_EMAIL_VERIFIED || 'true'" in workflow
    assert "supabase.co/auth/v1" not in workflow
    assert "user_metadata.roles" not in workflow


def test_auth_gate_posture_assertion_is_valid_python() -> None:
    """Execute the exact inline verifier shipped to the VM.

    Escaped quotes inside an f-string expression are a Python syntax error,
    so static string checks alone do not protect the post-deploy gate.
    """
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index("  python3 -c '\n", script.index("assert_bff_auth_gate()"))
    start += len("  python3 -c '")
    end = script.index("\n' \"$version_payload\"", start)
    verifier = script[start:end]

    strict = subprocess.run(
        [
            "python3",
            "-c",
            verifier,
            json.dumps(
                {
                    "config_posture": {
                        "auth_stub": False,
                        "auth_mode": "strict",
                    }
                }
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert strict.returncode == 0, strict.stdout + strict.stderr

    permissive = subprocess.run(
        [
            "python3",
            "-c",
            verifier,
            json.dumps(
                {
                    "config_posture": {
                        "auth_stub": True,
                        "auth_mode": "permissive",
                    }
                }
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert permissive.returncode != 0
    assert "auth_stub=True, expected False" in permissive.stderr
