"""BFF owner reads against the real Capital inbound authority."""
import io
import json
import time
import urllib.error
import urllib.request

import pytest

from services.capital.test_tenant_scoping import JWT_SECRET, _auth_headers, capital_test_env  # noqa: F401
from services.capital.conftest import _healthy_guard_collaborators  # noqa: F401
from services.control_plane.bff.core import owner_reads
from services.runtime_auth_inbound import encode_jwt_hs256

ROLES = ["operator", "viewer", "capital-reader"]


def _user_jwt(*tenants):
    return encode_jwt_hs256(
        {"sub": "operator_a", "roles": ROLES, "allowed_tenants": list(tenants), "exp": int(time.time()) + 3600},
        secret=JWT_SECRET,
    )


@pytest.fixture()
def owner(capital_test_env, monkeypatch):  # noqa: F811
    client, _module, _dir = capital_test_env
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital")
    monkeypatch.delenv("PANTHEON_BFF_TENANT_ID", raising=False)
    for tenant in ("tenant-a", "tenant-b"):
        res = client.post("/api/capital-pools", headers=_auth_headers(tenant, actor_id="admin"), json={
            "actor_id": "admin", "actor_role": "capital.admin", "pool_id": f"pool-{tenant}",
            "name": tenant, "owner_id": "fund", "owner_type": "fund",
            "approval_decision_id": "approval", "risk_policy_ref": "risk-main",
        })
        assert res.status_code == 201, res.text

    def urlopen(req, timeout=None):
        res = client.get(req.full_url.removeprefix("http://capital"), headers=dict(req.header_items()))
        if res.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, res.status_code, res.text, {}, io.BytesIO(res.content))
        return io.BytesIO(res.content)

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp(urlopen(req)))
    return client


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, inner):
        super().__init__(inner.read())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _read(token, tenant=None):
    auth, sel = owner_reads.authorization.set(f"Bearer {token}"), owner_reads.selected_tenant.set(tenant)
    try:
        return owner_reads.read_records(owner_reads.capital_url, "/api/capital-pools")
    finally:
        owner_reads.selected_tenant.reset(sel)
        owner_reads.authorization.reset(auth)


def test_single_tenant_caller_reads_own_pool(owner):
    assert [p["pool_id"] for p in _read(_user_jwt("tenant-a"))] == ["pool-tenant-a"]


def test_multi_tenant_caller_without_selection_fails_closed(owner):
    with pytest.raises(Exception) as err:
        _read(_user_jwt("tenant-a", "tenant-b"))
    assert "TENANT_MISMATCH" in repr(getattr(err.value, "error_code", err.value))


def test_multi_tenant_caller_selects_one_tenant(owner):
    assert [p["pool_id"] for p in _read(_user_jwt("tenant-a", "tenant-b"), "tenant-b")] == ["pool-tenant-b"]


def test_configured_default_must_be_authorized(owner, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-a")
    assert [p["pool_id"] for p in _read(_user_jwt("tenant-a", "tenant-b"))] == ["pool-tenant-a"]
    with pytest.raises(Exception):
        _read(_user_jwt("tenant-b", "tenant-c"))


def test_cross_tenant_selection_is_denied(owner):
    with pytest.raises(Exception):
        _read(_user_jwt("tenant-a"), "tenant-b")
