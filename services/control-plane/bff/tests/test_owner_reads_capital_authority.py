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


def test_configured_default_does_not_select_tenant_for_ambiguous_caller(owner, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-a")
    with pytest.raises(Exception) as err:
        _read(_user_jwt("tenant-a", "tenant-b"))
    assert "TENANT_MISMATCH" in repr(getattr(err.value, "error_code", err.value))


def test_configured_default_does_not_override_primary_tenant(owner, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-a")
    token = encode_jwt_hs256(
        {"sub": "operator_b", "roles": ROLES, "tenant_id": "tenant-b",
         "allowed_tenants": ["tenant-a", "tenant-b"], "exp": int(time.time()) + 3600},
        secret=JWT_SECRET,
    )
    assert [p["pool_id"] for p in _read(token)] == ["pool-tenant-b"]


def test_cross_tenant_selection_is_denied(owner):
    with pytest.raises(Exception):
        _read(_user_jwt("tenant-a"), "tenant-b")


def test_evaluator_viewer_token_reads_pools_through_bff(owner):
    """
    Original evaluator viewer token with service='pantheon-dev-persona-evaluator-bff-reader'
    and role='viewer' successfully reads pools through BFF owner_reads.
    """
    evaluator_token = encode_jwt_hs256(
        {
            "sub": "pantheon-dev-persona-evaluator-bff-reader",
            "service": "pantheon-dev-persona-evaluator-bff-reader",
            "roles": ["viewer"],
            "allowed_tenants": ["tenant-a"],
            "tenant_id": "tenant-a",
            "scope": "pantheon:dev-owner-read",
            "exp": int(time.time()) + 3600,
        },
        secret=JWT_SECRET,
    )
    pools = _read(evaluator_token)
    assert [p["pool_id"] for p in pools] == ["pool-tenant-a"]


def test_evaluator_viewer_token_mutation_is_denied(owner):
    """
    Original evaluator viewer token attempting mutation against Capital
    is denied (service mismatch when forwarded with control-plane-bff and viewer lack of write privilege).
    """
    client = owner
    evaluator_token = encode_jwt_hs256(
        {
            "sub": "pantheon-dev-persona-evaluator-bff-reader",
            "service": "pantheon-dev-persona-evaluator-bff-reader",
            "roles": ["viewer"],
            "allowed_tenants": ["tenant-a"],
            "tenant_id": "tenant-a",
            "scope": "pantheon:dev-owner-read",
            "exp": int(time.time()) + 3600,
        },
        secret=JWT_SECRET,
    )
    res = client.post(
        "/api/capital-pools",
        headers={
            "Authorization": f"Bearer {evaluator_token}",
            "X-Pantheon-Service": "control-plane-bff",
            "X-Tenant-Id": "tenant-a",
        },
        json={
            "actor_id": "pantheon-dev-persona-evaluator-bff-reader",
            "actor_role": "viewer",
            "pool_id": "pool-evaluator-denied",
            "name": "Denied",
            "owner_id": "fund",
            "owner_type": "fund",
            "approval_decision_id": "app",
        },
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACTOR_SERVICE_MISMATCH"


def test_forwarded_user_jwt_mutation_service_mismatch_is_denied(owner):
    """
    Validates that:
    1. An arbitrary user JWT without service claim matching control-plane-bff is denied
       for mutations through control-plane-bff with ACTOR_SERVICE_MISMATCH.
    2. A token with an explicit mismatched service claim is denied with ACTOR_SERVICE_MISMATCH.
    """
    client = owner
    user_token = encode_jwt_hs256(
        {
            "sub": "operator-bob",
            "roles": ["operator", "capital.admin"],
            "allowed_tenants": ["tenant-a"],
            "tenant_id": "tenant-a",
            "exp": int(time.time()) + 3600,
        },
        secret=JWT_SECRET,
    )
    res_ok = client.post(
        "/api/capital-pools",
        headers={
            "Authorization": f"Bearer {user_token}",
            "X-Pantheon-Service": "control-plane-bff",
            "X-Tenant-Id": "tenant-a",
        },
        json={
            "actor_id": "operator-bob",
            "actor_role": "capital.admin",
            "pool_id": "pool-bob-created",
            "name": "Bob Pool",
            "owner_id": "fund",
            "owner_type": "fund",
            "approval_decision_id": "appr-bob",
            "risk_policy_ref": "risk-main",
        },
    )
    assert res_ok.status_code == 403
    assert res_ok.json()["error"]["code"] == "ACTOR_SERVICE_MISMATCH"

    mismatched_token = encode_jwt_hs256(
        {
            "sub": "attacker-service",
            "service": "attacker-service",
            "roles": ["operator", "capital.admin"],
            "allowed_tenants": ["tenant-a"],
            "tenant_id": "tenant-a",
            "exp": int(time.time()) + 3600,
        },
        secret=JWT_SECRET,
    )
    res_bad = client.post(
        "/api/capital-pools",
        headers={
            "Authorization": f"Bearer {mismatched_token}",
            "X-Pantheon-Service": "control-plane-bff",
            "X-Tenant-Id": "tenant-a",
        },
        json={
            "actor_id": "attacker-service",
            "actor_role": "capital.admin",
            "pool_id": "pool-attacker",
            "name": "Attacker Pool",
            "owner_id": "fund",
            "owner_type": "fund",
            "approval_decision_id": "appr-bad",
        },
    )
    assert res_bad.status_code == 403
    assert res_bad.json()["error"]["code"] == "ACTOR_SERVICE_MISMATCH"

