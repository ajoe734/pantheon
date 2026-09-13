"""Actual dev-login JWTs must interoperate with Governance's approval reader."""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.governance.write_authority import is_authorized_to_decide

from ..session_lifecycle_store import SessionLifecycleStore
from . import policy
from .handlers import create_auth_handlers
from .router import create_auth_router
from .service import AuthFacadeService


@pytest.fixture
def owners(tmp_path, monkeypatch):
    for name, value in {
        "PANTHEON_ENV": "dev", "PANTHEON_DEPLOYMENT_STAGE": "dev",
        "PANTHEON_PERSISTENCE_POSTURE": "dev",
        "PANTHEON_BFF_AUTH_MODE": "strict", "PANTHEON_BFF_AUTH_STUB": "false",
        "PANTHEON_BFF_JWT_SECRET": "synthetic-dev-approver-governance-test-secret",
        "PANTHEON_BFF_DEV_LOGIN_JWT_SECRET": "synthetic-dev-approver-governance-test-secret",
        "PANTHEON_BFF_JWT_ISSUER": "synthetic-dev-login",
        "PANTHEON_BFF_JWT_AUDIENCE": "synthetic-dev-owners",
        "GOVERNANCE_DATA_DIR": str(tmp_path / "governance"),
        "GOVERNANCE_STORE_BACKEND": "json", "GOVERNANCE_AUDIT_BACKEND": "jsonl",
    }.items():
        monkeypatch.setenv(name, value)
    # Verify the emitted roles themselves work: no configured role-map alias.
    for suffix in ("JWKS_URI", "OIDC_DISCOVERY_URL", "OIDC_ISSUER", "OIDC_AUDIENCE",
                   "ROLE_MAP", "ROLE_MAP_MODE", "ROLE_CLAIMS"):
        for owner in ("BFF", "GOVERNANCE"):
            monkeypatch.delenv(f"PANTHEON_{owner}_{suffix}", raising=False)
    for suffix in ("SECRET", "ISSUER", "AUDIENCE"):
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_JWT_{suffix}",
                          os.environ[f"PANTHEON_BFF_JWT_{suffix}"])
    for identity in ("viewer", "operator", "approver", "risk_owner", "operator_a", "operator_b"):
        prefix = "PANTHEON_BFF_DEV_LOGIN_" + identity.upper()
        monkeypatch.setenv(prefix + "_CLIENT_ID", "synthetic-" + identity)
        monkeypatch.setenv(prefix + "_CLIENT_SECRET", "password-" + identity)
        monkeypatch.setenv(prefix + "_TENANT_ID", "tenant-dev")
        monkeypatch.setenv(prefix + "_ALLOWED_TENANTS", "tenant-dev")
    deps = policy.create_auth_dependencies(
        session_lifecycle_store=SessionLifecycleStore(str(tmp_path / "sessions.json")))
    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)
    bff = FastAPI()
    bff.include_router(create_auth_router(service=service))

    from services.governance import main as governance
    # Only isolate persistence; JWT verification and owner authorization are real.
    from services.governance.pg_store import ApprovalDecisionStore
    monkeypatch.setattr(governance, "store", ApprovalDecisionStore(str(tmp_path / "decisions.json")))
    return TestClient(bff), TestClient(governance.app), governance


def login(client, identity, **extra):
    return client.post("/bff/auth/dev-login", json={
        "grant_type": "client_credentials", "client_id": "synthetic-" + identity,
        "client_secret": "password-" + identity, **extra})


def test_real_dev_approver_login_reaches_governance_without_mfa(owners):
    bff, governance_client, governance = owners
    response = login(bff, "approver")
    assert response.status_code == 200, response.text
    authorization = "Bearer " + response.json()["access_token"]
    principal = governance._approval_reader(authorization)
    assert principal.actor_id == "pantheon-dev-approver"
    assert principal.roles == frozenset({"approver", "governance_reviewer"})
    assert principal.claims["tenant_id"] == "tenant-dev"
    assert not principal.claims.get("mfa_verified")
    assert "amr" not in principal.claims
    assert is_authorized_to_decide("governance_reviewer", "low")
    assert not is_authorized_to_decide("governance_reviewer", "high")
    readback = governance_client.get("/api/governance/approvals", headers={"Authorization": authorization})
    assert readback.status_code == 200, readback.text
    assert readback.json() == []


@pytest.mark.parametrize("identity", ["viewer", "operator", "operator_a", "operator_b"])
def test_non_approver_credentials_do_not_gain_review_authority(owners, identity):
    bff, governance_client, governance = owners
    response = login(bff, identity)
    assert response.status_code == 200, response.text
    authorization = "Bearer " + response.json()["access_token"]
    principal = governance._approval_principal(authorization)
    assert principal.roles == frozenset({"viewer" if identity == "viewer" else "operator"})
    denied = governance_client.get("/api/governance/approvals", headers={"Authorization": authorization})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Approval read role required"
    assert login(bff, identity, roles=["governance_reviewer"]).status_code == 403


def test_risk_owner_remains_distinct(owners):
    bff, _, governance = owners
    response = login(bff, "risk_owner")
    assert response.status_code == 200, response.text
    principal = governance._approval_reader("Bearer " + response.json()["access_token"])
    assert principal.roles == frozenset({"risk_owner"})
    assert not is_authorized_to_decide("risk_owner", "low")


def test_approver_still_requires_own_password_and_cannot_cross_tenants(owners):
    bff, _, _ = owners
    assert login(bff, "approver", client_secret="wrong-password").status_code == 401
    assert login(bff, "approver", tenant_id="another-tenant").status_code == 403
    assert login(bff, "approver", roles=["risk_owner"]).status_code == 403


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_new_review_role_does_not_enable_nondev_login(owners, monkeypatch, environment):
    bff, _, _ = owners
    monkeypatch.setenv("PANTHEON_ENV", environment)
    response = login(bff, "approver")
    assert response.status_code == 403
    assert "access_token" not in response.json()
