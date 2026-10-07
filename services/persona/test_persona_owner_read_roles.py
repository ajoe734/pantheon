"""Tests for Persona owner GET read roles (viewer, view_only, reviewer) and mutation role boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.persona.write_owner import (
    CreatePersonaRequest,
    PersistentCapabilitySnapshotOwner,
    PersistentPersonaOwner,
    create_app,
)
from services.runtime_auth_inbound import encode_jwt_hs256

_JWT_SECRET = "persona-test-read-roles-secret"


def _make_jwt_token(
    *,
    roles: list[str],
    tenant_id: str | None = "tenant-alpha",
    actor_id: str = "test-caller",
) -> str:
    claims: dict[str, Any] = {
        "sub": actor_id,
        "roles": list(roles),
        "exp": 4102444800,
    }
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
        claims["allowed_tenants"] = [tenant_id]
    return encode_jwt_hs256(claims, secret=_JWT_SECRET)


def _auth_header(
    *,
    roles: list[str],
    tenant_id: str | None = "tenant-alpha",
    actor_id: str = "test-caller",
) -> dict[str, str]:
    token = _make_jwt_token(roles=roles, tenant_id=tenant_id, actor_id=actor_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "test-svc-token")
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", "operator-bff")

    store = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    capability_store = PersistentCapabilitySnapshotOwner.from_json_path(
        tmp_path / "capabilities.json"
    )

    # Seed private persona for tenant-alpha
    store.create(
        CreatePersonaRequest(
            actor_id="operator-bff",
            persona_id="persona-alpha",
            name="Alpha Private Persona",
            mandate="Alpha thesis",
            strategy_family="alpha",
            tenant_id="tenant-alpha",
            metadata={"trade_reflections": [{"episode_id": "ep-alpha-1"}]},
        )
    )

    # Seed private persona for tenant-beta
    store.create(
        CreatePersonaRequest(
            actor_id="operator-bff",
            persona_id="persona-beta",
            name="Beta Private Persona",
            mandate="Beta thesis",
            strategy_family="beta",
            tenant_id="tenant-beta",
            metadata={"trade_reflections": [{"episode_id": "ep-beta-1"}]},
        )
    )

    app = create_app(store, capability_owner=capability_store)
    return TestClient(app, raise_server_exceptions=False), store


def test_viewer_can_list_and_get_own_tenant_persona_and_is_tenant_isolated(owner_app):
    client, _ = owner_app
    headers = _auth_header(roles=["viewer"], tenant_id="tenant-alpha", actor_id="viewer-alpha")

    # Viewer lists personas in tenant-alpha
    list_res = client.get("/api/personas", headers=headers)
    assert list_res.status_code == 200, list_res.text
    returned_ids = [p["persona_id"] for p in list_res.json()]
    assert "persona-alpha" in returned_ids
    assert "persona-beta" not in returned_ids

    # Viewer gets own tenant persona detail
    get_res = client.get("/api/personas/persona-alpha", headers=headers)
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["persona_id"] == "persona-alpha"
    assert get_res.json()["tenant_id"] == "tenant-alpha"

    # Viewer cannot get another tenant's persona
    cross_res = client.get("/api/personas/persona-beta", headers=headers)
    assert cross_res.status_code == 403, cross_res.text


@pytest.mark.parametrize(
    "role",
    [
        "viewer",
        "view_only",
        "reviewer",
        "operator",
        "approver",
        "admin",
        "persona.admin",
    ],
)
def test_all_read_roles_accepted_on_get_routes(owner_app, role: str):
    client, _ = owner_app
    headers = _auth_header(roles=[role], tenant_id="tenant-alpha", actor_id=f"caller-{role}")

    list_res = client.get("/api/personas", headers=headers)
    assert list_res.status_code == 200, f"Role {role} failed list_personas: {list_res.text}"
    returned_ids = [p["persona_id"] for p in list_res.json()]
    assert "persona-alpha" in returned_ids

    get_res = client.get("/api/personas/persona-alpha", headers=headers)
    assert get_res.status_code == 200, f"Role {role} failed get_persona: {get_res.text}"
    assert get_res.json()["persona_id"] == "persona-alpha"


def test_unauthenticated_and_unauthorized_role_rejected_on_get_routes(owner_app):
    client, _ = owner_app

    # Unauthenticated request fails with 401 when private personas exist
    assert client.get("/api/personas").status_code == 401
    assert client.get("/api/personas/persona-alpha").status_code == 401

    # Unauthorized role (not in _AUTHENTICATED_READ_ROLES) fails with 403
    unauth_headers = _auth_header(roles=["unauthorized_guest"], tenant_id="tenant-alpha")
    assert client.get("/api/personas", headers=unauth_headers).status_code == 403
    assert client.get("/api/personas/persona-alpha", headers=unauth_headers).status_code == 403


def test_viewer_role_rejected_on_all_mutation_routes(owner_app):
    client, _ = owner_app
    headers = _auth_header(roles=["viewer"], tenant_id="tenant-alpha", actor_id="viewer-alpha")

    # 1. Create persona rejected
    create_res = client.post(
        "/api/personas",
        headers=headers,
        json={
            "actor_id": "viewer-alpha",
            "persona_id": "persona-viewer-mutation",
            "name": "Viewer Created Persona",
            "mandate": "should fail",
            "strategy_family": "factor",
            "tenant_id": "tenant-alpha",
        },
    )
    assert create_res.status_code == 403, create_res.text

    # 2. Patch persona rejected
    patch_res = client.patch(
        "/api/personas/persona-alpha",
        headers=headers,
        json={
            "actor_id": "viewer-alpha",
            "name": "Viewer Renamed Persona",
        },
    )
    assert patch_res.status_code == 403, patch_res.text

    # 3. Patch lifecycle rejected
    lifecycle_res = client.patch(
        "/api/personas/persona-alpha/lifecycle",
        headers=headers,
        json={
            "actor_id": "viewer-alpha",
            "target_state": "research_only",
        },
    )
    assert lifecycle_res.status_code == 403, lifecycle_res.text

    # 4. Trade reflection retry mutation rejected
    reflect_res = client.post(
        "/api/personas/persona-alpha/trade-reflections/ep-1:retry",
        headers={**headers, "Idempotency-Key": "test-idem-key-1"},
        json={"actor_id": "viewer-alpha"},
    )
    assert reflect_res.status_code == 403, reflect_res.text
