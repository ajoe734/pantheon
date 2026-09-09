"""Synthetic rotation and wiring proof, never hosted credentials or approvals."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts.issue_dev_paper_principals import (
    CONSUMER_FILES, REFRESH_SECONDS, TTL_SECONDS, healthy_files, refresh_files, revoke_files,
)
from scripts.test_issue_dev_paper_principals import NOW, configured, verify
from services.service_token_file import configured_dev_paper_grant_enabled, configured_service_token


def test_atomic_rotation_keeps_every_consumer_alive_beyond_original_expiry(tmp_path):
    env = configured()
    refresh_files(tmp_path, env, now=NOW)
    previous = {}
    for consumer, variables in CONSUMER_FILES.items():
        for variable in variables:
            path = tmp_path / consumer / variable
            source = {variable + "_FILE": str(path), variable: "stale-env-must-not-win"}
            previous[variable] = configured_service_token(variable, source)
            assert path.stat().st_mode & 0o777 == 0o600
    assert healthy_files(tmp_path, env, now=NOW + 1)
    assert not healthy_files(tmp_path, env, now=NOW + TTL_SECONDS - REFRESH_SECONDS)
    renewed_at = NOW + TTL_SECONDS - REFRESH_SECONDS
    refresh_files(tmp_path, env, now=renewed_at)
    assert healthy_files(tmp_path, env, now=NOW + TTL_SECONDS + 1)
    for consumer, variables in CONSUMER_FILES.items():
        for variable in variables:
            value = configured_service_token(variable, {variable + "_FILE": str(tmp_path / consumer / variable)})
            assert value != previous[variable]
            claims = verify(value, now=NOW + TTL_SECONDS + 1)
            assert claims["exp"] == renewed_at + TTL_SECONDS
    # Refresh does not read/write business ApprovalDecisions or owner DBs.
    assert {p.name for p in tmp_path.iterdir()} == set(CONSUMER_FILES)


def test_unauthorized_renewal_preserves_still_valid_existing_tokens(tmp_path):
    env = configured()
    refresh_files(tmp_path, env, now=NOW)
    old = {p: p.read_bytes() for p in tmp_path.rglob("*_SERVICE_TOKEN")}
    with pytest.raises(ValueError):
        refresh_files(tmp_path, {**env, "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"}, now=NOW + 1)
    assert all(p.read_bytes() == data for p, data in old.items())


def test_explicit_withdrawal_denies_captured_principal_and_removes_only_fixed_files(tmp_path):
    from types import SimpleNamespace
    from services.governance.paper_approval_scope import resolve_dev_paper_grant, PaperApprovalDenied
    env = configured()
    refresh_files(tmp_path, env, now=NOW)
    grant_env = {"GOVERNANCE_DEV_PAPER_GRANT_FILE": str(tmp_path / "governance/grant")}
    assert configured_dev_paper_grant_enabled(grant_env)
    unrelated = tmp_path / "governance/unrelated-audit-note"
    unrelated.write_text("keep")
    captured = (tmp_path / "operator-bff/PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN").read_text()
    claims = verify(captured)
    context = SimpleNamespace(actor_id=claims["sub"], claims=claims,
                              roles=frozenset(claims["roles"]), token_kind="jwt")
    owner_env = {**env, **grant_env, "GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "true"}
    assert resolve_dev_paper_grant(context, env=owner_env) is not None
    revoked_env = {**env, "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "false"}
    revoke_files(tmp_path, revoked_env)
    assert verify(captured)["sub"] == "pantheon-dev-paper-provisioner"  # JWT expiry alone is insufficient.
    assert not configured_dev_paper_grant_enabled(grant_env)
    with pytest.raises(PaperApprovalDenied):
        resolve_dev_paper_grant(context, env=owner_env)
    assert not healthy_files(tmp_path, env, now=NOW + 1)
    assert not list(tmp_path.rglob("*_SERVICE_TOKEN"))
    assert unrelated.read_text() == "keep"
    revoke_files(tmp_path, revoked_env)  # safe replay
    with pytest.raises(ValueError):
        refresh_files(tmp_path, revoked_env, now=NOW + 2)
    assert not configured_dev_paper_grant_enabled(grant_env)


@pytest.mark.parametrize("domain", ["deployment", "registry", "runtime_manager", "paper_registry"])
def test_cached_owner_reader_reads_rotated_file_at_every_get(tmp_path, monkeypatch, domain):
    from types import SimpleNamespace
    from urllib.error import URLError
    from services.governance.approval_authority import configured_approval_reader, ApprovalUnavailable
    from services.governance.paper_approval_scope import configured_paper_registry_reader, PaperCandidateUnavailable
    variable = "GOVERNANCE_REGISTRY_SERVICE_TOKEN" if domain == "paper_registry" else domain.upper() + "_GOVERNANCE_SERVICE_TOKEN"
    consumer = {"deployment": "deployment", "registry": "registry", "runtime_manager": "runtime-manager", "paper_registry": "governance"}[domain]
    env = configured()
    refresh_files(tmp_path, env, now=NOW)
    monkeypatch.setenv(variable + "_FILE", str(tmp_path / consumer / variable))
    monkeypatch.setenv(variable, "old-env-must-not-win")
    if domain == "paper_registry":
        monkeypatch.setenv("GOVERNANCE_REGISTRY_BASE_URL", "http://isolated-owner")
        reader = configured_paper_registry_reader()
        get = reader.get_entry_view
    else:
        reader = configured_approval_reader(domain, base_url="http://isolated-owner")
        get = reader.get
    observed = []

    def disconnected(request, **kwargs):
        observed.append(request.get_header("Authorization"))
        raise URLError("isolated transport deliberately disconnected")

    reader._opener = SimpleNamespace(open=disconnected)
    for issued in [NOW, NOW + REFRESH_SECONDS]:
        refresh_files(tmp_path, env, now=issued)
        with pytest.raises((ApprovalUnavailable, PaperCandidateUnavailable)):
            get("isolated-record")
    assert len(set(observed)) == 2
    assert all("old-env" not in value for value in observed)
    (tmp_path / consumer / variable).unlink()
    with pytest.raises((ApprovalUnavailable, PaperCandidateUnavailable)):
        get("isolated-record")
    assert len(observed) == 2  # missing file prevented any attempted owner request.


@pytest.mark.parametrize("failure", ["missing", "empty", "world-readable", "symlink", "directory", "malformed"])
def test_configured_file_failure_never_uses_environment_fallback(tmp_path, failure):
    path = tmp_path / "credential"
    if failure not in ("missing", "directory"):
        path.write_text("" if failure == "empty" else "invalid token" if failure == "malformed" else "synthetic")
        path.chmod(0o644 if failure == "world-readable" else 0o600)
    if failure == "directory":
        path.mkdir()
    if failure == "symlink":
        path = tmp_path / "link"
        path.symlink_to(tmp_path / "credential")
    with pytest.raises(RuntimeError, match="credential unavailable"):
        configured_service_token("TEST_SERVICE_TOKEN", {"TEST_SERVICE_TOKEN_FILE": str(path), "TEST_SERVICE_TOKEN": "old"})


def test_compose_limits_issuer_and_mounts_each_reader_read_only():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    services = compose["services"]
    issuer = services["dev-paper-principal-issuer"]
    assert issuer["network_mode"] == "none"
    assert issuer["read_only"] is True and issuer["cap_drop"] == ["ALL"]
    assert issuer["profiles"] == ["dev-paper-principals"]
    assert not issuer.get("privileged") and not issuer.get("ports")
    source_volumes = {v.split(":")[1].split("/")[-1]: v.split(":")[0] for v in issuer["volumes"]}
    for consumer, variables in CONSUMER_FILES.items():
        mounts = services[consumer]["volumes"]
        assert source_volumes[consumer] + ":/run/pantheon-principals:ro" in mounts
        for variable in variables:
            assert variable + "_FILE" in services[consumer]["environment"]
    outbox = services["deployment-outbox-consumer"]
    assert source_volumes["deployment"] + ":/run/pantheon-principals:ro" in outbox["volumes"]
    assert outbox["environment"]["RUNTIME_MANAGER_REGISTRY_SERVICE_TOKEN_FILE"] == "${DEPLOYMENT_REGISTRY_SERVICE_TOKEN_FILE:-}"
    assert outbox["environment"]["RUNTIME_MANAGER_GOVERNANCE_SERVICE_TOKEN_FILE"] == "${DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN_FILE:-}"
    assert all("sock" not in v for v in issuer["volumes"])


def test_exact_predecessor_without_issuer_keeps_restore_compatibility(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/deploy_nonprod_vm.sh").read_text()
    function = source.split("prepare_dev_paper_principals() {", 1)[1].split("\nstart_dev_paper_principal_issuer()", 1)[0]
    script = "info() { :; }\nprepare_dev_paper_principals() {" + function
    script += "\nprepare_dev_paper_principals\n[[ $DEV_PAPER_PRINCIPALS_SUPPORTED == false ]]\n"
    result = subprocess.run(["bash", "-eu", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("component,adopted,expected", [("root", True, 1), ("root", False, 0), ("bff", True, 0)])
def test_old_full_owner_downgrade_is_blocked_but_exact_bff_restore_is_allowed(tmp_path, component, adopted, expected):
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/deploy_nonprod_vm.sh").read_text()
    function = source.split("prepare_dev_paper_principals() {", 1)[1].split("\nstart_dev_paper_principal_issuer()", 1)[0]
    # Stub only the read-only fixed-volume existence check, never actual Docker.
    script = f"info() {{ :; }}\ndocker() {{ return {0 if adopted else 1}; }}\n"
    script += "prepare_dev_paper_principals() {" + function + "\nprepare_dev_paper_principals\n"
    result = subprocess.run(["bash", "-eu", "-c", script], cwd=tmp_path,
                            env={**os.environ, "PANTHEON_DEPLOY_COMPONENT": component},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stderr
