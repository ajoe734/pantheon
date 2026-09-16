"""Synthetic issuance/verification tests; no hosted credentials are loaded."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.issue_dev_paper_principals import (
    PAPER_SCOPE, PAPER_SUBJECT, READERS, WRITERS, TTL_SECONDS, issue_environment, write_environment,
)
from services.runtime_auth_inbound import AuthError, _verify_jwt_hs256, validate_request_auth

NOW = 2_000_000_000
KEY = "synthetic-dev-principal-unit-key-" * 2


def configured():
    return {
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        "PANTHEON_DEV_BFF_JWT_SECRET": KEY,
        "PANTHEON_DEV_BFF_JWT_ISSUER": "isolated-issuer",
        "PANTHEON_DEV_BFF_JWT_AUDIENCE": "isolated-audience",
    }


def verify(token, **overrides):
    values = dict(secret=KEY, issuer="isolated-issuer", audience="isolated-audience", now=NOW + 1)
    values.update(overrides)
    return _verify_jwt_hs256(token, **values)


def test_fixed_product_profiles_are_tenant_scoped_short_lived_and_separate():
    values = issue_environment(configured(), now=NOW)
    ids = set()
    for variable, (subject, role) in READERS.items():
        claims = verify(values[variable])
        assert claims["sub"] == subject
        assert claims["roles"] == [role]
        assert claims["tenant_id"] == "tenant-dev"
        assert claims["allowed_tenants"] == ["tenant-dev"]
        assert claims["exp"] - claims["iat"] == TTL_SECONDS
        assert claims["scope"] == "pantheon:dev-owner-read"
        ids.add(claims["jti"])
    for variable, (subject, role, scope) in WRITERS.items():
        claims = verify(values[variable])
        assert claims["sub"] == subject
        assert claims["roles"] == [role]
        assert claims["tenant_id"] == "tenant-dev"
        assert claims["allowed_tenants"] == ["tenant-dev"]
        assert claims["exp"] - claims["iat"] == TTL_SECONDS
        assert claims["scope"] == scope
        assert not {"admin", "risk_owner", "operator", "approval_reader", "automated_gate", "runtime", "capital"}.intersection(claims["roles"])
        ids.add(claims["jti"])
    assert len(ids) == len(READERS) + len(WRITERS)
    writer = verify(values["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"])
    assert writer["sub"] == PAPER_SUBJECT
    assert writer["roles"] == ["automated_gate"]
    assert writer["scope"] == PAPER_SCOPE
    assert writer["tenant_id"] == "tenant-dev"
    assert not {"admin", "risk_owner", "operator"}.intersection(writer["roles"])


@pytest.mark.parametrize("field,value", [
    ("PANTHEON_ENV", "production"), ("PANTHEON_ENV", "staging"),
    ("PANTHEON_DEV_BFF_TENANT_ID", "other"),
    ("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED", "false"),
    ("PANTHEON_DEV_BFF_JWT_SECRET", "short"),
    ("PANTHEON_DEV_BFF_JWT_ISSUER", ""), ("PANTHEON_DEV_BFF_JWT_AUDIENCE", ""),
])
def test_missing_authority_or_wrong_environment_fails_closed(field, value):
    with pytest.raises(ValueError):
        issue_environment({**configured(), field: value}, now=NOW)


@pytest.mark.parametrize("overrides", [
    {"issuer": "foreign"}, {"audience": "foreign"}, {"secret": "wrong-key"},
    {"now": NOW + TTL_SECONDS + 1}, {"now": NOW - 100},
])
def test_real_verifier_rejects_wrong_or_expired_token(overrides):
    token = issue_environment(configured(), now=NOW)["DEPLOYMENT_REGISTRY_SERVICE_TOKEN"]
    with pytest.raises(AuthError):
        verify(token, **overrides)


@pytest.mark.parametrize("variable", READERS)
def test_readers_do_not_have_product_write_roles(variable):
    token = issue_environment(configured())[variable]
    env = {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict", "PANTHEON_RUNTIME_JWT_SECRET": KEY,
        "PANTHEON_RUNTIME_JWT_ISSUER": "isolated-issuer",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "isolated-audience",
    }
    context = validate_request_auth(authorization="Bearer " + token,
                                    required_roles=(READERS[variable][1],), env=env)
    assert context.actor_id == READERS[variable][0]
    with pytest.raises(AuthError) as rejected:
        validate_request_auth(authorization="Bearer " + token, mfa_header=None,
                              required_roles=("automated_gate", "admin", "operator"),
                              mfa_required=False, env=env)
    assert rejected.value.status_code == 403


@pytest.mark.parametrize("variable", WRITERS)
def test_writers_do_not_have_admin_operator_or_approval_roles(variable):
    token = issue_environment(configured())[variable]
    env = {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict", "PANTHEON_RUNTIME_JWT_SECRET": KEY,
        "PANTHEON_RUNTIME_JWT_ISSUER": "isolated-issuer",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "isolated-audience",
    }
    subject, role, scope = WRITERS[variable]
    context = validate_request_auth(authorization="Bearer " + token,
                                    required_roles=(role,), env=env)
    assert context.actor_id == subject
    with pytest.raises(AuthError) as rejected:
        validate_request_auth(authorization="Bearer " + token, mfa_header=None,
                              required_roles=("automated_gate", "admin", "operator", "approval_reader"),
                              mfa_required=False, env=env)
    assert rejected.value.status_code == 403


def test_private_file_never_overwrites_or_follows_existing_target(tmp_path):
    path = tmp_path / "principals.env"
    values = issue_environment(configured(), now=NOW)
    write_environment(path, values)
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_environment(path, values)
    assert path.read_bytes() == original
    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        write_environment(link, values)
    assert path.read_bytes() == original


def test_cli_outputs_no_secrets(tmp_path):
    path = tmp_path / "generated.env"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("issue_dev_paper_principals.py")),
         "--output-env", str(path)], env={**os.environ, **configured()},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert KEY not in result.stdout + result.stderr
    assert "eyJ" not in result.stdout + result.stderr
    assert "values withheld" in result.stdout
    assert path.stat().st_mode & 0o777 == 0o600


def test_deploy_and_compose_connect_authorized_environment_without_embedded_tokens():
    import yaml
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/deploy_nonprod_vm.sh").read_text()
    assert 'DEV_PAPER_PRINCIPALS_AUTHORIZED="${DEV_PAPER_PRINCIPALS_AUTHORIZED:-false}"' in script
    assert "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED=$(shell_quote" in script
    assert "PANTHEON_ENV=dev python3 scripts/issue_dev_paper_principals.py" in script
    root_case = script.split('case "${PANTHEON_DEPLOY_COMPONENT}" in', 1)[1].split("  bff)", 1)[0]
    assert root_case.index("prepare_deploy_worktree") < root_case.index("prepare_dev_paper_principals")
    assert root_case.index("prepare_dev_paper_principals") < root_case.index("docker-compose.yml build")
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    for service in ("governance", "deployment", "registry", "runtime-manager"):
        assert compose[service]["environment"]["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"
    assert "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN" in compose["operator-bff"]["environment"]
    assert "GOVERNANCE_REGISTRY_SERVICE_TOKEN" in compose["governance"]["environment"]
    assert "DISTILLATION_REGISTRY_SERVICE_TOKEN" in compose["strategy-distillation-worker"]["environment"]
    assert "DISTILLATION_REGISTRY_SERVICE_TOKEN_FILE" in compose["strategy-distillation-worker"]["environment"]
    assert "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN" in compose["alpha-replication-worker"]["environment"]
    assert "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN_FILE" in compose["alpha-replication-worker"]["environment"]
