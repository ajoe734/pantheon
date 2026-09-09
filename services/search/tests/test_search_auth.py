from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.search.main import create_app

JWT_SECRET = "search-test-secret-key-12345"


def _make_jwt(
    *,
    sub: str | None = "search-operator-1",
    roles: list[str] | None = None,
    tenant_id: str | None = "tenant-alpha",
    exp_offset: int = 3600,
    secret: str = JWT_SECRET,
    custom_claims: dict | None = None,
) -> str:
    claims: dict = {}
    if sub is not None:
        claims["sub"] = sub
    if roles is not None:
        claims["roles"] = roles
    elif roles is None and "roles" not in (custom_claims or {}):
        claims["roles"] = ["operator", "researcher"]
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if exp_offset is not None:
        claims["exp"] = int(time.time()) + exp_offset
    if custom_claims:
        claims.update(custom_claims)
    return encode_jwt_hs256(claims, secret=secret)


def _valid_query_body(**overrides) -> dict:
    body = {
        "request_id": "req-search-auth-001",
        "trace_id": "trace-search-auth-001",
        "query": "alpha",
        "documents": [],
        "persona_id": "persona-researcher",
        "workspace_id": "workspace-alpha",
        "access_context": {
            "persona_id": "persona-researcher",
            "workspace_id": "workspace-alpha",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }
    body.update(overrides)
    return body


@pytest.fixture
def search_client(tmp_path):
    index_path = tmp_path / "search-index.jsonl"
    return TestClient(create_app(index_path))


def test_search_auth_permissive_mode_unauthenticated_allowed(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "permissive")
    resp = search_client.post("/api/search/query", json=_valid_query_body())
    assert resp.status_code == 200, resp.text


def test_search_auth_strict_mode_missing_token_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    resp = search_client.post("/api/search/query", json=_valid_query_body())
    assert resp.status_code == 401
    assert "missing Bearer token" in resp.text


def test_search_auth_invalid_signature_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    bad_token = _make_jwt(secret="wrong-secret-key-999")
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


def test_search_auth_expired_token_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    expired_token = _make_jwt(exp_offset=-100)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


def test_search_auth_missing_sub_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub=None)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "sub" in resp.text


def test_search_auth_missing_exp_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(exp_offset=None)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "exp" in resp.text


def test_search_auth_missing_tenant_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id=None)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "tenant" in resp.text


def test_search_auth_missing_roles_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=[])
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "role" in resp.text.lower()


def test_search_auth_whitespace_claim_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="   ")
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "whitespace" in resp.text


def test_search_auth_forged_body_tenant_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-alpha")
    body = _valid_query_body()
    body["access_context"]["tenant_id"] = "tenant-beta"
    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "outside the verified caller scope" in resp.text


def test_search_auth_forged_body_actor_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="operator-alpha")
    body = _valid_query_body()
    body["actor_ref"] = "operator-intruder"
    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "actor_ref does not match verified token identity" in resp.text


def test_search_auth_role_elevation_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=["researcher"])
    body = _valid_query_body()
    body["role_refs"] = ["admin"]
    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "exceed verified token roles" in resp.text


def test_search_auth_insufficient_endpoint_role_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    # Caller has valid token but none of the required _SEARCH_READ_ROLES
    token = _make_jwt(roles=["guest_viewer"])
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_search_auth_valid_token_populates_context(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="researcher-carol", tenant_id="tenant-finance", roles=["researcher"])
    body = _valid_query_body()
    # Ensure body does not specify actor_ref or tenant_id
    body.pop("actor_ref", None)
    body["access_context"]["actor_ref"] = None
    body["access_context"]["tenant_id"] = None

    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


def test_search_admin_endpoints_role_enforcement(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)

    # 1. Researcher cannot call materialize or reload
    researcher_token = _make_jwt(roles=["researcher"])
    resp = search_client.post(
        "/api/search/index/materialize",
        headers={"Authorization": f"Bearer {researcher_token}"},
    )
    assert resp.status_code == 403

    resp = search_client.post(
        "/api/search/index/reload",
        headers={"Authorization": f"Bearer {researcher_token}"},
    )
    assert resp.status_code == 403

    # 2. Admin/operator can call reload
    admin_token = _make_jwt(roles=["admin"])
    resp = search_client.post(
        "/api/search/index/reload",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
