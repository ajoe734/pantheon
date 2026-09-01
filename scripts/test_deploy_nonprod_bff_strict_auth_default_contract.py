import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


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


def test_product_deploy_does_not_carry_development_authority_keys() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    for value in (
        "BRIDGE_SIGNING_PRIVATE_KEY",
        "BRIDGE_SIGNING_KEY_ID",
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        "DEV_AUTHORITY_SIGNING_ENV_FILE",
        "STAGING_AUTHORITY_SIGNING_ENV_FILE",
        "PANTHEON_DEVELOPMENT_TOOLING_ROUTES_ENABLED",
        "provision_dev_supervisor_watchdog",
    ):
        assert value not in script
        assert value not in workflow


def test_payload_validation_is_product_only() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    validation = workflow[
        workflow.index("- name: Resolve and validate dev payload"):
        workflow.index("- name: Resolve exact execute-plans dev payload")
    ]
    assert "docker compose -f docker-compose.yml config --quiet" in validation
    assert "BRIDGE_SIGNING" not in validation


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


def test_openclaw_claude_oauth_token_has_no_fabricated_deploy_default() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert (
        'DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-}"'
        in script
    )
    assert "DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-sk-" not in script


def test_openclaw_claude_oauth_token_is_redacted_from_dry_run_output() -> None:
    secret = "sk-ant-oat01-must-not-appear-in-deploy-output"
    result = _run_deploy_script(
        {"DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN": secret},
        "--dry-run",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "dev_openclaw_claude_code_oauth_token_configured=true" in result.stdout


def test_openclaw_claude_oauth_token_defaults_to_unconfigured_in_dry_run() -> None:
    result = _run_deploy_script({}, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dev_openclaw_claude_code_oauth_token_configured=false" in result.stdout


def test_openclaw_claude_oauth_token_reaches_the_deploy_export() -> None:
    """The var has to reach both docker compose invocations (root and the
    narrower bff-only rebuild), keyed the same as the adapter service token
    docker-compose.yml already reads with a PANTHEON_ prefix."""

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    export_line = (
        'PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN}" \\'
    )
    assert script.count(export_line) >= 2

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "CLAUDE_CODE_OAUTH_TOKEN: ${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-}"
        in compose
    )


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
    # Assistant provider health is observability only and must never gate a
    # release; the BFF deliberately excludes it from ready/authReady.
    assert 'assert data.get("providerReady") is True' not in script
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
            "- name: Prepare pinned direct SSH transport"
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
        assert script.count(f"{compose_client_id}=") >= 2
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
            assert workflow.count(secret_ref) == 5
        else:
            assert secret_ref not in hosted_probe
            assert workflow.count(secret_ref) == 4


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


class _MockBffHandler(BaseHTTPRequestHandler):
    readiness_responses: list[dict[str, Any]] = []
    readiness_call_count: int = 0
    version_posture: dict[str, Any] = {"auth_stub": False, "auth_mode": "strict"}

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/bff/version":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"config_posture": self.version_posture}).encode("utf-8"))
            return

        if self.path == "/bff/me":
            auth = self.headers.get("Authorization", "")
            if "op-fixed:operator:mfa" in auth:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "unauthorized"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"meta": {"status": "ok"}}')
            return

        if self.path == "/bff/auth/readiness":
            type(self).readiness_call_count += 1
            idx = min(type(self).readiness_call_count - 1, len(self.readiness_responses) - 1)
            response = self.readiness_responses[idx] if self.readiness_responses else {}
            status_code = response.get("__status_code", 200)
            body = {k: v for k, v in response.items() if k != "__status_code"}
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/bff/auth/dev-login":
            import base64

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            client_id = body.get("client_id", "")

            if "viewer" in client_id:
                identity = "viewer"
                role = "viewer"
            elif "approver" in client_id:
                identity = "approver"
                role = "approver"
            elif "risk" in client_id:
                identity = "risk_owner"
                role = "risk_owner"
            elif "operator-b" in client_id:
                identity = "operator_b"
                role = "operator"
            else:
                identity = "operator_a"
                role = "operator"

            claims = {
                "sub": f"sub-{identity}",
                "roles": [role],
                "mfa_verified": True,
            }
            claims_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("utf-8").rstrip("=")
            token = f"eyJhbGciOiJIUzI1NiJ9.{claims_b64}.sig"

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "access_token": token,
                "meta": {"identity": identity},
            }).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def _run_auth_gate_against_server(
    base_url: str,
    *,
    expected_sha: str = _TEST_SHA,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.05,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    auth_gate_section = script_text[
        script_text.index("assert_dedicated_dev_login_identity() {") : script_text.index(
            "snapshot_remote_state()"
        )
    ]
    bash_code = f"""
set -euo pipefail
info() {{ echo "[nonprod-deploy] $*"; }}
error() {{ echo "[nonprod-deploy] ERROR: $*" >&2; exit 1; }}

{auth_gate_section}

assert_bff_auth_gate "{base_url}"
"""
    env = os.environ.copy()
    env["PANTHEON_DEPLOY_SHA"] = expected_sha
    env["PANTHEON_DEV_BFF_AUTH_MODE"] = "strict"
    env["PANTHEON_DEV_BFF_AUTH_STUB"] = "false"
    env["PANTHEON_DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS"] = str(timeout_seconds)
    env["PANTHEON_DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS"] = str(poll_interval_seconds)
    for identity in ("VIEWER", "APPROVER", "RISK_OWNER", "OPERATOR_A", "OPERATOR_B"):
        slug = identity.lower().replace("_", "-")
        env[f"PANTHEON_DEV_BFF_DEV_LOGIN_{identity}_CLIENT_ID"] = f"test-{slug}-client"
        env[f"PANTHEON_DEV_BFF_DEV_LOGIN_{identity}_CLIENT_SECRET"] = f"test-{slug}-secret"
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", "-c", bash_code],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def test_auth_gate_readiness_retry_succeeds_on_delayed_provider_readiness() -> None:
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": False,
                "authReady": True,
                "providerReady": False,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": False,
                    "status": "unavailable",
                    "reason": "OPENCLAW_GATEWAY_TIMEOUT",
                },
            }
        },
        {
            "data": {
                "ready": True,
                "authReady": True,
                "providerReady": True,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": True,
                    "status": "ready",
                },
            }
        },
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=3.0,
            poll_interval_seconds=0.05,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "authenticated dev-login and strict browser readiness round trip succeeded"
            in result.stdout
        )
        assert _MockBffHandler.readiness_call_count >= 2
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_readiness_retry_times_out_on_permanent_non_ready() -> None:
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": False,
                "authReady": True,
                "providerReady": False,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": False,
                    "status": "unavailable",
                    "reason": "OPENCLAW_GATEWAY_TIMEOUT",
                },
            }
        }
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=1,
            poll_interval_seconds=0.05,
        )
        assert result.returncode != 0, result.stdout
        assert "strict browser readiness contract is not satisfied within 1s timeout" in result.stderr
        # The blocking condition is the release-relevant ready flag, not the
        # assistant provider's health.
        assert "ready=False" in result.stderr
        assert "test-operator-a-secret" not in result.stdout
        assert "test-operator-a-secret" not in result.stderr
        assert "eyJhbGci" not in result.stdout
        assert "eyJhbGci" not in result.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_fails_fast_on_deployed_config_violation() -> None:
    """OPGAP-GATE-HARDENING-20260901 regression.

    Auth posture is decided when the container starts; polling cannot change it.
    Retrying a posture violation only turns a precise "this build came up
    permissive" into "contract not satisfied within Ns timeout", which sends the
    reader hunting for a slow dependency that does not exist. Fail immediately
    with the real reason, well inside the poll budget.
    """
    from http.server import ThreadingHTTPServer
    import threading
    import time as _time

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": True,
                "authReady": True,
                "providerReady": True,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    # Deployed permissive: no amount of polling makes this strict.
                    "mode": "permissive",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {"provider": "openclaw", "ready": True, "status": "ready"},
            }
        }
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = _time.monotonic()
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=30,
            poll_interval_seconds=0.05,
        )
        elapsed = _time.monotonic() - started

        assert result.returncode != 0, result.stdout
        assert "cannot be satisfied by retrying" in result.stderr, result.stderr
        assert "auth.mode=" in result.stderr, result.stderr
        assert "within 30s timeout" not in result.stderr, (
            "a deterministic posture violation must not be reported as a timeout"
        )
        assert elapsed < 15, (
            f"gate polled for {elapsed:.1f}s on a violation that retrying cannot fix"
        )
        assert _MockBffHandler.readiness_call_count == 1, (
            "a terminal violation must not be re-probed"
        )
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_readiness_passes_when_assistant_provider_is_down() -> None:
    """OPGAP-DEPLOY-PROVIDER-GATE-20260901 regression.

    Assistant provider health is observability only: the BFF deliberately
    excludes it from ready/authReady because "a provider outage or probe
    failure must never flip a validly authenticated strict session to
    not-ready". A deploy gate that blocks (and auto-rolls-back) an otherwise
    healthy release because an external LLM provider credential expired is
    strictly more conservative than the product itself, and recurs every time
    that credential rotates.
    """
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": True,
                "authReady": True,
                "providerReady": False,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": False,
                    "status": "degraded",
                    "reason": "OPENCLAW_GATEWAY_TIMEOUT",
                },
            }
        }
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=5,
            poll_interval_seconds=0.05,
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert "authenticated dev-login and strict browser readiness round trip succeeded" in result.stdout
        # Still surfaced for observability, just not as a gate.
        assert "advisory: assistant provider not ready" in result.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_readiness_fails_closed_on_sha_mismatch() -> None:
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": True,
                "authReady": True,
                "providerReady": True,
                "sourceCommitSha": "wrong" * 8,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": True,
                    "status": "ready",
                },
            }
        }
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=1,
            poll_interval_seconds=0.05,
        )
        assert result.returncode != 0, result.stdout
        assert "sourceCommitSha" in result.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_configures_bounded_readiness_timeout_and_plumbing() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS="${DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS:-120}"' in script
    assert 'DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS="${DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS:-2}"' in script
    assert 'command_prefix+=" PANTHEON_DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS=' in script
    assert 'command_prefix+=" PANTHEON_DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS=' in script


def test_auth_gate_readiness_retry_succeeds_on_transient_http_error() -> None:
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {"__status_code": 503, "error": "service unavailable during restart"},
        {
            "data": {
                "ready": True,
                "authReady": True,
                "providerReady": True,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": True,
                    "status": "ready",
                },
            }
        },
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=3,
            poll_interval_seconds=0.05,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "authenticated dev-login and strict browser readiness round trip succeeded"
            in result.stdout
        )
        assert _MockBffHandler.readiness_call_count >= 2
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_readiness_fails_closed_on_auth_posture_mismatch() -> None:
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": True,
                "authReady": True,
                "providerReady": True,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "permissive",
                    "stub": True,
                    "sessionKind": "bearer",
                    "operatorRoleReady": True,
                    "interactionCapabilityReady": True,
                    "verifierReady": True,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": True,
                    "status": "ready",
                },
            }
        }
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=1,
            poll_interval_seconds=0.05,
        )
        assert result.returncode != 0, result.stdout
        assert "auth.mode" in result.stderr or "auth.stub" in result.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_auth_gate_readiness_fails_closed_on_auth_not_ready() -> None:
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBffHandler)
    port = server.server_port
    _MockBffHandler.readiness_call_count = 0
    _MockBffHandler.version_posture = {"auth_stub": False, "auth_mode": "strict"}
    _MockBffHandler.readiness_responses = [
        {
            "data": {
                "ready": False,
                "authReady": False,
                "providerReady": True,
                "sourceCommitSha": _TEST_SHA,
                "auth": {
                    "mode": "strict",
                    "stub": False,
                    "sessionKind": "bearer",
                    "operatorRoleReady": False,
                    "interactionCapabilityReady": False,
                    "verifierReady": False,
                },
                "provider": {
                    "provider": "openclaw",
                    "ready": True,
                    "status": "ready",
                },
            }
        }
    ]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_auth_gate_against_server(
            f"http://127.0.0.1:{port}",
            timeout_seconds=1,
            poll_interval_seconds=0.05,
        )
        assert result.returncode != 0, result.stdout
        assert "authReady" in result.stderr
    finally:
        server.shutdown()
        server.server_close()


