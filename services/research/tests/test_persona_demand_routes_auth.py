"""Demand routes through real token verification and tenant-partitioned stores.

All keys and tokens are local fixtures. No dependency overrides or hosted claims.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from scripts.issue_dev_paper_principals import issue_environment
from services.research.tests.test_research_demand import BODY
from services.research.tests.test_research_orchestrator_http_service import _load_service_module
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "persona-demand-auth-test-secret-0123456789"
ISSUER, AUDIENCE = "demand-test-issuer", "demand-test-audience"
TENANT, PRINCIPAL = "tenant-dev", "persona-pool-cycle"
BASE = "/api/research/persona-demands"


def _cycle_headers():
    # Prove the unchanged dev issuer's existing viewer principal is sufficient.
    issued = issue_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        "PANTHEON_DEV_BFF_JWT_SECRET": SECRET,
        "PANTHEON_DEV_BFF_JWT_ISSUER": ISSUER,
        "PANTHEON_DEV_BFF_JWT_AUDIENCE": AUDIENCE,
    })
    return {"Authorization": "Bearer " + issued["PERSONA_RESEARCH_SERVICE_TOKEN"], "X-Tenant-Id": TENANT}


def _headers(actor="unrelated-viewer", role="viewer", tenant=TENANT, *, secret=SECRET, **claims):
    payload = {"sub": actor, "roles": [role], "tenant_id": tenant,
               "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300, **claims}
    return {"Authorization": "Bearer " + encode_jwt_hs256(payload, secret=secret), "X-Tenant-Id": TENANT}


@pytest.fixture
def research(tmp_path, monkeypatch):
    for key, value in {"AUTH_MODE": "strict", "JWT_SECRET": SECRET,
                       "JWT_ISSUER": ISSUER, "JWT_AUDIENCE": AUDIENCE,
                       "MFA_REQUIRED": "false"}.items():
        monkeypatch.setenv("PANTHEON_RESEARCH_" + key, value)
    module = _load_service_module(data_dir=str(tmp_path))
    client = TestClient(module.app)
    created = client.post(BASE, headers=_cycle_headers(), json=BODY)
    assert created.status_code == 201, created.text
    return module, client, created.json()


def _mutation(route, demand):
    if route == "open":
        return BASE, dict(BODY, persona_id="p2")
    if route == "reconcile":
        return BASE + "/reconcile", {"persona_id": BODY["persona_id"], "members": [
            {"artifact_ref": "later-member", "joined_at": demand["expires_at"]}]}
    return BASE + "/" + demand["demand_id"] + "/actions", {
        "run_at": demand["created_at"], "matched": 1, "excluded": 0,
        "actions": [{"match_id": "candidate-1", "action": "consult", "status": "executed"}],
    }


@pytest.mark.parametrize("route", ["open", "reconcile", "actions"])
def test_issued_cycle_principal_can_write(research, route):
    module, client, demand = research
    url, body = _mutation(route, demand)
    response = client.post(url, headers=_cycle_headers(), json=body)
    assert response.status_code == (201 if route == "open" else 200), response.text
    rows = module.research_demand_store.list(TENANT)
    if route == "open":
        assert {r["persona_id"] for r in rows} == {"p1", "p2"}
    elif route == "reconcile":
        assert rows[0]["status"] == "satisfied" and rows[0]["satisfied_by"] == "later-member"
    else:
        assert rows[0]["discovery"]["actions"][0]["status"] == "executed"


@pytest.mark.parametrize("route", ["open", "reconcile", "actions"])
@pytest.mark.parametrize("identity", ["viewer", "operator", "admin", "cross-tenant", "spoofed"])
def test_other_principals_cannot_mutate_or_impersonate_cycle(research, route, identity):
    module, client, demand = research
    url, body = _mutation(route, demand)
    headers = (_headers(actor=PRINCIPAL, tenant="other-tenant") if identity == "cross-tenant"
               else _headers(role=identity if identity in {"operator", "admin"} else "viewer"))
    if identity == "spoofed":
        headers = _headers(service=PRINCIPAL, actor_id=PRINCIPAL)
        headers.update({"X-Actor-Id": PRINCIPAL, "X-Pantheon-Service": PRINCIPAL})
        body.update({"actor_id": PRINCIPAL, "service": PRINCIPAL})
    paths = [module.research_demand_store.path, module.query_plan_store.path]
    before = [p.read_bytes() if p.exists() else None for p in paths]
    response = client.post(url, headers=headers, json=body)
    assert response.status_code == 403, response.text
    assert [p.read_bytes() if p.exists() else None for p in paths] == before


@pytest.mark.parametrize("route", ["open", "reconcile", "actions"])
@pytest.mark.parametrize("bad_auth,expected", [
    ("missing", 401), ("bad-signature", 401), ("expired", 401), ("missing-tenant", 400),
])
def test_writes_require_real_verified_token_and_explicit_tenant(research, route, bad_auth, expected):
    module, client, demand = research
    headers = _headers(actor=PRINCIPAL)
    if bad_auth == "missing":
        headers.pop("Authorization")
    elif bad_auth == "bad-signature":
        headers = _headers(actor=PRINCIPAL, secret="wrong-test-secret")
    elif bad_auth == "expired":
        headers = _headers(actor=PRINCIPAL, exp=1)
    else:
        headers.pop("X-Tenant-Id")
    before = module.research_demand_store.path.read_bytes()
    url, body = _mutation(route, demand)
    response = client.post(url, headers=headers, json=body)
    assert response.status_code == expected, response.text
    assert module.research_demand_store.path.read_bytes() == before


@pytest.mark.parametrize("role", [
    "viewer", "view_only", "operator", "approver", "admin", "reviewer", "governance_reviewer",
    "risk_owner", "governance_committee", "automated_gate", "service", "deployment_consumer", "runtime_writer",
])
def test_listing_retains_existing_read_roles(research, role):
    _, client, demand = research
    response = client.get(BASE, headers=_headers(role=role), params={"persona_id": "p1", "status": "open"})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1 and response.json()["demands"][0]["demand_id"] == demand["demand_id"]


def test_get_tenant_boundary_and_foreign_demand_actions(research):
    module, client, demand = research
    cycle_read = client.get(BASE, headers=_cycle_headers())
    assert cycle_read.status_code == 200 and cycle_read.json()["count"] == 1
    assert client.get(BASE, headers=_headers(tenant="other-tenant")).status_code == 403
    assert client.get(BASE).status_code == 401
    own_headers = _headers(actor=PRINCIPAL, tenant="other-tenant")
    own_headers["X-Tenant-Id"] = "other-tenant"
    assert client.get(BASE, headers=own_headers).json() == {"demands": [], "count": 0}
    url, body = _mutation("actions", demand)
    before = module.research_demand_store.path.read_bytes()
    assert client.post(url, headers=own_headers, json=body).status_code == 404
    assert module.research_demand_store.path.read_bytes() == before


@pytest.mark.parametrize("route", ["open", "reconcile", "actions"])
def test_permissive_structured_tokens_preserve_actor_and_tenant_checks(research, monkeypatch, route):
    _, client, demand = research
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "permissive")
    url, body = _mutation(route, demand)
    for actor, tenant in [("unrelated-viewer", TENANT), (PRINCIPAL, "other-tenant")]:
        headers = {"Authorization": f"Bearer {actor}:viewer:{tenant}", "X-Tenant-Id": TENANT}
        assert client.post(url, headers=headers, json=body).status_code == 403
    headers = {"Authorization": f"Bearer {PRINCIPAL}:viewer:{TENANT}", "X-Tenant-Id": TENANT}
    assert client.post(url, headers=headers, json=body).status_code == (201 if route == "open" else 200)
