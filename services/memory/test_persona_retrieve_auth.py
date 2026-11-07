"""Authenticated, tenant-bound canonical persona memory reads (BFF-PERSONA-MEMORY-AUTH-20261107)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
main = importlib.import_module("services.memory.main")
from services.memory.persona_memory_store import PersonaMemoryEntry  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

SECRET = "memory-persona-retrieve-secret"


def _entry(memory_id: str, tenant_id):
    return {
        "memory_id": memory_id,
        "persona_id": "persona-x",
        "memory_type": "strategy_lesson",
        "content": {"summary": f"lesson {memory_id}", "structured_payload": {}, "tags": ["t"]},
        "source_event_type": "postmortem_published",
        "source_event_id": f"PM-{memory_id}",
        "written_at": "2026-06-09T01:00:00Z",
        "write_authority": "incident-svc",
        "tenant_id": tenant_id,
    }


def _token(tenant="tenant-alpha", roles=("operator",)):
    return encode_jwt_hs256({"sub": "op-1", "roles": list(roles), "tenant_id": tenant, "exp": 4102444800}, secret=SECRET)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "i.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", SECRET)
    for fn in (main._persona_store, main._store):
        getattr(fn, "cache_clear", lambda: None)()
    store = main._persona_store()
    for mid, tenant in (("pmem-a", "tenant-alpha"), ("pmem-b", "tenant-beta")):
        store.create(PersonaMemoryEntry.from_dict(_entry(mid, tenant)))
    return TestClient(main.app)


PARAMS = {"actor_id": "op-1", "actor_roles": "", "session_id": "s", "persona_id": "persona-x", "scope": "persona"}


def test_missing_authorization_is_401(client):
    assert client.get("/api/memory/retrieve", params=PARAMS).status_code == 401


def test_authenticated_read_is_tenant_scoped(client):
    resp = client.get("/api/memory/retrieve", params=PARAMS, headers={"Authorization": f"Bearer {_token()}"})
    assert resp.status_code == 200, resp.text
    assert [h["entry"]["memory_id"] for h in resp.json()["hits"]] == ["pmem-a"]
    other = client.get("/api/memory/retrieve", params=PARAMS, headers={"Authorization": f"Bearer {_token('tenant-beta')}"})
    assert [h["entry"]["memory_id"] for h in other.json()["hits"]] == ["pmem-b"]


def test_query_roles_cannot_exceed_verified_roles(client):
    resp = client.get(
        "/api/memory/retrieve",
        params={**PARAMS, "actor_roles": "admin"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 403
