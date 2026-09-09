import time
import uuid
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
import services.memory.main as memory_main

JWT_SECRET = "memory-test-secret-key-12345"


def _make_jwt(
    *,
    sub: str | None = "memory-operator-1",
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


def _institutional_payload(**overrides) -> dict:
    payload = {
        "entry_id": f"mem-{uuid.uuid4()}",
        "knowledge_type": "research_finding",
        "content": {
            "headline": "Auth test finding",
            "body": "Auth test body content",
            "tags": ["auth", "test"],
        },
        "source_event_type": "research_task_completed",
        "source_event_id": "rt-auth-001",
        "written_at": "2026-04-20T04:00:00Z",
        "write_authority": "research-svc",
        "scope": "strategy_family",
        "scope_filter": "momentum",
        "contributing_persona_ids": ["persona-alpha"],
        "reuse_count": 0,
    }
    payload.update(overrides)
    return payload


def _persona_payload(**overrides) -> dict:
    payload = {
        "memory_id": f"pmem-{uuid.uuid4()}",
        "persona_id": "persona-alpha",
        "memory_type": "strategy_lesson",
        "content": {
            "summary": "Persona lesson for auth test",
            "tags": ["auth"],
        },
        "source_event_type": "postmortem_published",
        "source_event_id": "PM-AUTH-001",
        "written_at": "2026-06-09T01:00:00Z",
        "write_authority": "incident-svc",
        "relevance_scope": "persona_private",
        "reuse_count": 0,
    }
    payload.update(overrides)
    return payload


def _retrieve_params(**overrides) -> dict:
    params = {
        "query": "alpha",
        "scope": "institutional",
        "actor_id": "memory-operator-1",
        "actor_roles": ["operator"],
        "session_id": "sess-auth-001",
    }
    params.update(overrides)
    return params


@pytest.fixture
def memory_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_STORE_PATH", str(tmp_path / "memory-store.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE_PATH", str(tmp_path / "persona-store.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None
    return TestClient(memory_main.app)


def test_memory_auth_permissive_mode_unauthenticated_allowed(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "permissive")
    resp = memory_client.get("/api/memory/retrieve", params=_retrieve_params())
    assert resp.status_code == 200, resp.text


def test_memory_auth_strict_mode_missing_token_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    resp = memory_client.get("/api/memory/retrieve", params=_retrieve_params())
    assert resp.status_code == 401
    assert "missing Bearer token" in resp.text


def test_memory_auth_invalid_signature_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    bad_token = _make_jwt(secret="wrong-secret-999")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(),
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


def test_memory_auth_expired_token_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    expired_token = _make_jwt(exp_offset=-100)
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(),
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


def test_memory_auth_missing_required_claims_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)

    # Missing sub
    token = _make_jwt(sub=None)
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "sub" in resp.text

    # Missing tenant
    token = _make_jwt(tenant_id=None)
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "tenant" in resp.text


def test_memory_auth_whitespace_claim_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="   ")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "whitespace" in resp.text


def test_memory_auth_retrieve_actor_forgery_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="verified-operator-1")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(actor_id="forged-operator-2"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "actor_id does not match verified token identity" in resp.text


def test_memory_auth_retrieve_role_elevation_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=["researcher"], sub="memory-operator-1")
    resp = memory_client.get(
        "/api/memory/retrieve",
        params=_retrieve_params(actor_roles=["researcher", "admin"]),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "exceed verified token roles" in resp.text


def test_memory_auth_store_entry_tenant_forgery_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-alpha", roles=["operator"])
    payload = _institutional_payload(tenant_id="tenant-beta")
    resp = memory_client.post(
        "/api/memory/entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Payload tenant does not match verified token tenant" in resp.text


def test_memory_auth_store_entry_without_write_role_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    # Caller has non-writer role
    token = _make_jwt(tenant_id="tenant-alpha", roles=["guest_viewer"])
    payload = _institutional_payload(tenant_id="tenant-alpha")
    resp = memory_client.post(
        "/api/memory/entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_memory_auth_store_entry_auto_populates_tenant(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-gamma", roles=["operator"])
    payload = _institutional_payload()
    payload.pop("tenant_id", None)
    resp = memory_client.post(
        "/api/memory/entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    entry_id = resp.json()["entry_id"]

    # Verify stored entry has tenant_gamma
    get_resp = memory_client.get(
        f"/api/memory/entries/{entry_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["tenant_id"] == "tenant-gamma"


def test_memory_auth_persona_store_tenant_forgery_rejected(memory_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-alpha", roles=["operator"])
    payload = _persona_payload(tenant_id="tenant-beta")
    resp = memory_client.post(
        "/api/memory/persona-entries",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Payload tenant does not match verified token tenant" in resp.text
