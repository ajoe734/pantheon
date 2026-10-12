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

    # Seed tenant-bound persona without reflections for tenant-alpha
    store.create(
        CreatePersonaRequest(
            actor_id="operator-bff",
            persona_id="persona-alpha",
            name="Alpha Private Persona",
            mandate="Alpha thesis",
            strategy_family="alpha",
            tenant_id="tenant-alpha",
            metadata={"paper_ledger": {"cash": 100000}},
        )
    )

    # Seed tenant-bound persona without reflections for tenant-beta
    store.create(
        CreatePersonaRequest(
            actor_id="operator-bff",
            persona_id="persona-beta",
            name="Beta Private Persona",
            mandate="Beta thesis",
            strategy_family="beta",
            tenant_id="tenant-beta",
            metadata={"paper_ledger": {"cash": 50000}},
        )
    )

    # Seed unbound public legacy persona without reflections
    store.create(
        CreatePersonaRequest(
            actor_id="operator-bff",
            persona_id="persona-public",
            name="Public Legacy Persona",
            mandate="Public thesis",
            strategy_family="public",
            tenant_id=None,
            metadata={"description": "legacy public record"},
        )
    )

    # Seed unbound persona with sensitive reflections
    store.create(
        CreatePersonaRequest(
            actor_id="operator-bff",
            persona_id="persona-unbound-private",
            name="Unbound Private Persona",
            mandate="Sensitive reflection thesis",
            strategy_family="private-unbound",
            tenant_id=None,
            metadata={"trade_reflections": [{"episode_id": "ep-unbound-1"}]},
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
    assert "persona-unbound-private" not in returned_ids

    # Viewer gets own tenant persona detail
    get_res = client.get("/api/personas/persona-alpha", headers=headers)
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["persona_id"] == "persona-alpha"
    assert get_res.json()["tenant_id"] == "tenant-alpha"

    # Viewer cannot get another tenant's persona
    cross_res = client.get("/api/personas/persona-beta", headers=headers)
    assert cross_res.status_code == 403, cross_res.text


def test_operator_valid_reads_and_foreign_tenant_isolation(owner_app):
    client, _ = owner_app
    same_headers = _auth_header(roles=["operator"], tenant_id="tenant-alpha", actor_id="operator-alpha")
    foreign_headers = _auth_header(roles=["operator"], tenant_id="tenant-beta", actor_id="operator-beta")

    # Same-tenant operator can list and get tenant-alpha persona without reflections
    list_res = client.get("/api/personas", headers=same_headers)
    assert list_res.status_code == 200, list_res.text
    returned_ids = [p["persona_id"] for p in list_res.json()]
    assert "persona-alpha" in returned_ids
    assert "persona-beta" not in returned_ids

    get_res = client.get("/api/personas/persona-alpha", headers=same_headers)
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["persona_id"] == "persona-alpha"
    assert get_res.json()["tenant_id"] == "tenant-alpha"

    # Foreign-tenant operator is excluded from list and detail access is denied (403)
    foreign_list_res = client.get("/api/personas", headers=foreign_headers)
    assert foreign_list_res.status_code == 200, foreign_list_res.text
    foreign_returned_ids = [p["persona_id"] for p in foreign_list_res.json()]
    assert "persona-alpha" not in foreign_returned_ids
    assert "persona-beta" in foreign_returned_ids

    foreign_get_res = client.get("/api/personas/persona-alpha", headers=foreign_headers)
    assert foreign_get_res.status_code == 403, foreign_get_res.text


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

    # Invalid / tampered bearer token rejected with 401
    bad_sig_token = encode_jwt_hs256(
        {"sub": "bad-caller", "roles": ["viewer"], "tenant_id": "tenant-alpha", "exp": 4102444800},
        secret="wrong-secret",
    )
    tampered_headers = {"Authorization": f"Bearer {bad_sig_token}"}
    assert client.get("/api/personas/persona-alpha", headers=tampered_headers).status_code == 401
    malformed_headers = {"Authorization": "Bearer not-a-valid-token"}
    assert client.get("/api/personas/persona-alpha", headers=malformed_headers).status_code == 401

    # Unauthorized role (not in _AUTHENTICATED_READ_ROLES) fails with 403
    unauth_headers = _auth_header(roles=["unauthorized_guest"], tenant_id="tenant-alpha")
    assert client.get("/api/personas", headers=unauth_headers).status_code == 403
    assert client.get("/api/personas/persona-alpha", headers=unauth_headers).status_code == 403


def test_unbound_public_legacy_records_preserve_public_read_behavior(owner_app):
    client, _ = owner_app
    alpha_headers = _auth_header(roles=["viewer"], tenant_id="tenant-alpha", actor_id="viewer-alpha")
    beta_headers = _auth_header(roles=["viewer"], tenant_id="tenant-beta", actor_id="viewer-beta")

    # Unbound public record (without sensitive reflections) allows anonymous read
    anon_res = client.get("/api/personas/persona-public")
    assert anon_res.status_code == 200, anon_res.text
    assert anon_res.json()["persona_id"] == "persona-public"
    assert anon_res.json()["tenant_id"] is None

    # Authenticated same- and foreign-tenant readers can also read public record
    assert client.get("/api/personas/persona-public", headers=alpha_headers).status_code == 200
    assert client.get("/api/personas/persona-public", headers=beta_headers).status_code == 200

    # Public record is included in tenant lists
    alpha_list = client.get("/api/personas", headers=alpha_headers).json()
    assert "persona-public" in [p["persona_id"] for p in alpha_list]


def test_unbound_sensitive_reflections_remain_private(owner_app):
    client, _ = owner_app
    alpha_headers = _auth_header(roles=["viewer"], tenant_id="tenant-alpha", actor_id="viewer-alpha")

    # Unbound persona with reflections fails closed: 401 unauthenticated
    assert client.get("/api/personas/persona-unbound-private").status_code == 401

    # Fails closed for authenticated caller because no tenant binding can authorize it
    auth_res = client.get("/api/personas/persona-unbound-private", headers=alpha_headers)
    assert auth_res.status_code == 403, auth_res.text

    # Excluded from tenant list
    alpha_list = client.get("/api/personas", headers=alpha_headers).json()
    assert "persona-unbound-private" not in [p["persona_id"] for p in alpha_list]


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
