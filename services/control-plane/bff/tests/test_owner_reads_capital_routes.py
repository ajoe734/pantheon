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
    def urlopen(req, timeout=None):
        res = capital.get(req.full_url.removeprefix("http://capital"), headers=dict(req.header_items()))
        if res.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, res.status_code, res.text, {}, io.BytesIO(res.content))
        return _Resp(res.content)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    _onboard(capital, "tenant-a")
    return _fresh_bff


def _fresh_bff():
    """A newly composed BFF instance (the supported composition seam) models a BFF restart."""
    from services.control_plane.bff.tests.bff_compose_stand_ins import resolve_with_stand_ins
    from services.control_plane.bff.core.app_factory import compose_bff_app
    return TestClient(compose_bff_app(dependency_resolver=resolve_with_stand_ins))


def _onboard(capital, tenant):
    """Paper onboarding: the real provisioning coordinator drives the real Capital owner."""
    from services.control_plane.bff.test_persona_provisioning_coordinator import (
        FakeOwnerTransport, _coordinator, _record_and_store, _schedule_receipt,
    )
    from services.control_plane.bff.tests.test_persona_paper_onboarding_capital_integration import (
        _CapitalOwnerBackedTransport,
    )

    class _Tenant:
        def __getattr__(self, verb):
            return lambda path, **kw: getattr(capital, verb)(path, headers=_auth_headers(tenant, actor_id="pantheon-persona-provisioner"), **kw)

    store, record = _record_and_store()
    transport = _CapitalOwnerBackedTransport(_Tenant(), FakeOwnerTransport())
    result = _coordinator(store, transport, _schedule_receipt).coordinate(record)
    assert result.current_step == "schedule_registered", result.error


def _ids():
    from services.control_plane.bff.persona_provisioning_coordinator import deterministic_provisioning_ids
    from services.control_plane.bff.test_persona_provisioning_coordinator import _record_and_store
    ids = deterministic_provisioning_ids(_record_and_store()[1])
    return ids.capital_pool_id, ids.persona_capital_binding_id


@pytest.mark.parametrize("restart", [False, True])
def test_onboarded_records_readable_by_caller_tenant_on_bff_routes(bff, restart):
    pool_id, binding_id = _ids()
    client = bff()
    if restart:
        client = bff()  # a second, freshly composed BFF: nothing is carried over
    headers = _bearer("tenant-a")
    pools = client.get("/bff/capital-pools", headers=headers)
    assert [p["pool_id"] for p in pools.json()["data"]] == [pool_id], pools.text
    assert client.get(f"/bff/capital-pools/{pool_id}", headers=headers).status_code == 200
    bindings = client.get("/api/v1/bindings?persona_id=persona-a", headers=headers)
    assert [b["binding_id"] for b in bindings.json()["data"]] == [binding_id], bindings.text


def test_other_tenant_cannot_read_records_on_bff_routes(bff):
    pool_id, _ = _ids()
    client, headers = bff(), _bearer("tenant-b")
    assert client.get("/bff/capital-pools", headers=headers).json()["data"] == []
    assert client.get(f"/bff/capital-pools/{pool_id}", headers=headers).status_code == 404
    assert client.get("/api/v1/bindings?persona_id=persona-a", headers=headers).json()["data"] == []


def _unavailable(res):
    return res.json()["meta"]["surfaces"]["capital_pools"]["status"] == "unavailable"


def test_ambiguous_tenant_is_an_unavailable_surface_not_empty_success(bff):
    res = bff().get("/bff/capital-pools", headers=_bearer("tenant-a", "tenant-b"))
    assert _unavailable(res), res.text


def test_downstream_capital_failure_is_an_unavailable_surface_not_empty_success(bff, monkeypatch):
    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "capital down", {}, io.BytesIO(b"{}"))

    client = bff()
    monkeypatch.setattr(urllib.request, "urlopen", refused)
    res = client.get("/bff/capital-pools", headers=_bearer("tenant-a"))
    assert _unavailable(res), res.text
