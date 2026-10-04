"""BFF Capital read routes (real main app) against the real Capital inbound authority."""
import io
import time
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from services.capital.test_tenant_scoping import JWT_SECRET, _auth_headers, capital_test_env  # noqa: F401
from services.capital.conftest import _healthy_guard_collaborators  # noqa: F401
from services.runtime_auth_inbound import encode_jwt_hs256

ROLES = ["operator", "viewer", "capital-reader"]


def _bearer(*tenants, primary=None):
    claims = {"sub": "operator", "roles": ROLES, "allowed_tenants": list(tenants), "exp": int(time.time()) + 3600}
    if primary:
        claims["tenant_id"] = primary
    return {"Authorization": "Bearer " + encode_jwt_hs256(claims, secret=JWT_SECRET)}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def bff(capital_test_env, monkeypatch):  # noqa: F811
    capital, _module, _dir = capital_test_env
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital")
    monkeypatch.delenv("PANTHEON_BFF_TENANT_ID", raising=False)
    for key, val in {"PANTHEON_BFF_JWT_SECRET": JWT_SECRET, "PANTHEON_BFF_AUTH_MODE": "strict"}.items():
        monkeypatch.setenv(key, val)
    for tenant in ("tenant-a", "tenant-b"):
        res = capital.post("/api/capital-pools", headers=_auth_headers(tenant, actor_id="admin"), json={
            "actor_id": "admin", "actor_role": "capital.admin", "pool_id": f"pool-{tenant}", "name": tenant,
            "owner_id": "fund", "owner_type": "fund", "approval_decision_id": "approval", "risk_policy_ref": "risk-main",
        })
        assert res.status_code == 201, res.text

    def urlopen(req, timeout=None):
        res = capital.get(req.full_url.removeprefix("http://capital"), headers=dict(req.header_items()))
        if res.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, res.status_code, res.text, {}, io.BytesIO(res.content))
        return _Resp(res.content)

    res = capital.post("/api/bindings", headers=_auth_headers("tenant-a", actor_id="admin"), json={
        "actor_id": "admin", "actor_role": "persona.admin", "binding_id": "binding-a", "persona_id": "persona-a",
        "capital_pool_id": "pool-tenant-a", "capital_sleeve_id": "sleeve-1", "role": "live_owner",
        "allowed_deployment_scope": "paper",
    })
    assert res.status_code == 201, res.text
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    from services.control_plane.bff.main import app
    return TestClient(app)


def _fresh_bff():
    """A new client over a fresh app handle models a BFF restart: owner reads keep no state."""
    from services.control_plane.bff.main import app
    return TestClient(app)


@pytest.mark.parametrize("restart", [False, True])
def test_owner_records_readable_by_caller_tenant_on_bff_routes(bff, restart):
    client = _fresh_bff() if restart else bff
    headers = _bearer("tenant-a")
    pools = client.get("/bff/capital-pools", headers=headers)
    assert [p["pool_id"] for p in pools.json()["data"]] == ["pool-tenant-a"], pools.text
    detail = client.get("/bff/capital-pools/pool-tenant-a", headers=headers)
    assert detail.status_code == 200, detail.text
    bindings = client.get("/api/v1/bindings?persona_id=persona-a", headers=headers)
    assert [b["binding_id"] for b in bindings.json()["data"]] == ["binding-a"], bindings.text


def test_other_tenant_cannot_read_records_on_bff_routes(bff):
    headers = _bearer("tenant-b")
    assert [p["pool_id"] for p in bff.get("/bff/capital-pools", headers=headers).json()["data"]] == ["pool-tenant-b"]
    assert bff.get("/bff/capital-pools/pool-tenant-a", headers=headers).status_code == 404
    assert bff.get("/api/v1/bindings?persona_id=persona-a", headers=headers).json()["data"] == []


def test_owner_error_is_an_unavailable_surface_not_empty_success(bff):
    res = bff.get("/bff/capital-pools", headers=_bearer("tenant-a", "tenant-b"))
    assert res.json()["meta"]["surfaces"]["capital_pools"]["status"] == "unavailable", res.text
