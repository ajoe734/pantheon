from __future__ import annotations

import os
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main
from session_lifecycle_store import SessionLifecycleStore
from services.runtime_auth_inbound import encode_jwt_hs256


JWT_SECRET = "pint-016-strict-browser-secret"
JWT_ISSUER = "https://identity.pantheon.test"
JWT_AUDIENCE = "pantheon-operator-bff"
FRONTEND_ORIGIN = "https://pantheon-frontend.test"


class _PersonaReadStore:
    def list_personas(self, **_kwargs):
        return [
            {
                "persona_id": "pint-016-persona",
                "tenant_id": "tenant-pint-016",
                "display_name": "PINT-016 Persona",
                "lifecycle_state": "active",
                "environment_ceiling": "paper",
            }
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {
            "snapshot_id": f"snapshot-{persona_id}",
            "persona_id": persona_id,
            "capabilities": ["persona_opinion"],
        }

    def list_approval_decisions(self):
        return []


def _token(role: str, *, subject: str | None = None) -> str:
    now = int(time.time())
    return encode_jwt_hs256(
        {
            "sub": subject or f"pint-016-{role}",
            "user_id": subject or f"pint-016-{role}",
            # Short-lived BFF/dev-login JWTs carry internal server-owned roles.
            "roles": [role],
            "tenant_id": "tenant-pint-016",
            "allowed_tenants": ["tenant-pint-016"],
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "sid": f"session-{subject or role}-{uuid.uuid4().hex}",
        },
        secret=JWT_SECRET,
    )


def _strict_env(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_ROLE_CLAIMS", "app_metadata.roles,roles")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP_MODE", "strict")
    monkeypatch.setenv(
        "PANTHEON_BFF_ROLE_MAP",
        "pantheon-operator=operator;pantheon-viewer=viewer",
    )
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-pint-016")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-pint-016")
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", FRONTEND_ORIGIN)
    monkeypatch.setenv("GIT_SHA", "1" * 40)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    original_store = bff_main.session_lifecycle_store
    original_read_store = bff_main.read_store
    bff_main.session_lifecycle_store = SessionLifecycleStore(
        str(tmp_path / "session-lifecycle.json")
    )
    monkeypatch.setattr(bff_main, "read_store", _PersonaReadStore())
    try:
        yield
    finally:
        bff_main.session_lifecycle_store = original_store
        bff_main.read_store = original_read_store


def _ready_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        bff_main,
        "_assistant_provider_readiness",
        lambda: {
            "provider": "openclaw",
            "ready": True,
            "status": "ready",
            "authStatus": "ready",
            "endpoint": "http://must-not-leak.internal",
            "credential": "must-not-leak",
        },
    )


def test_strict_operator_readiness_is_product_shaped_and_secret_free(monkeypatch) -> None:
    _strict_env(monkeypatch)
    _ready_provider(monkeypatch)
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get(
        "/bff/auth/readiness",
        headers={
            "Authorization": f"Bearer {_token('operator')}",
            "X-Tenant-Id": "tenant-pint-016",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert payload["meta"]["contract"] == "PINT-016-STRICT-BROWSER-READINESS"
    assert data["ready"] is True
    assert data["authReady"] is True
    assert data["providerReady"] is True
    assert data["auth"]["mode"] == "strict"
    assert data["auth"]["stub"] is False
    assert data["auth"]["sessionKind"] == "bearer"
    assert data["auth"]["operatorRoleReady"] is True
    assert data["auth"]["interactionCapabilityReady"] is True
    assert data["auth"]["verifier"]["issuerConfigured"] is True
    assert data["auth"]["verifier"]["audienceConfigured"] is True
    assert data["auth"]["verifier"]["roleClaimPaths"] == [
        "app_metadata.roles",
        "roles",
    ]
    assert data["auth"]["verifier"]["roleMapConfigured"] is True
    assert data["auth"]["verifier"]["roleMapMode"] == "strict"
    assert data["provider"] == {
        "provider": "openclaw",
        "ready": True,
        "status": "ready",
        "authStatus": "ready",
    }
    assert data["sourceCommitSha"] == "1" * 40
    assert data["authority"] == {
        "interaction": "advisory",
        "execution": "none",
        "broker": "none",
        "capital": "none",
    }
    serialized = response.text.lower()
    assert "must-not-leak" not in serialized
    assert JWT_SECRET not in response.text


def test_readiness_survives_provider_failure_for_valid_strict_session(monkeypatch) -> None:
    _strict_env(monkeypatch)

    def _raise_provider() -> dict:
        raise RuntimeError("openclaw provider unreachable")

    monkeypatch.setattr(bff_main, "_assistant_provider_readiness", _raise_provider)
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get(
        "/bff/auth/readiness",
        headers={
            "Authorization": f"Bearer {_token('operator')}",
            "X-Tenant-Id": "tenant-pint-016",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["authReady"] is True
    assert data["ready"] is True
    assert data["providerReady"] is False
    assert data["provider"]["ready"] is False


def test_readiness_route_is_published_in_openapi(monkeypatch) -> None:
    _strict_env(monkeypatch)
    schema = TestClient(bff_main.app).get("/openapi.json")

    assert schema.status_code == 200, schema.text
    assert "/bff/auth/readiness" in schema.json()["paths"]


def test_strict_viewer_can_read_readiness_but_is_not_write_ready(monkeypatch) -> None:
    _strict_env(monkeypatch)
    _ready_provider(monkeypatch)
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get(
        "/bff/auth/readiness",
        headers={"Authorization": f"Bearer {_token('viewer')}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ready"] is False
    assert data["authReady"] is False
    assert data["auth"]["operatorRoleReady"] is False
    assert data["auth"]["interactionCapabilityReady"] is False


def test_readiness_rejects_unauthenticated_and_stub_sessions(monkeypatch) -> None:
    _strict_env(monkeypatch)
    _ready_provider(monkeypatch)
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    unauthenticated = client.get("/bff/auth/readiness")
    assert unauthenticated.status_code == 401, unauthenticated.text

    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    stub = client.get(
        "/bff/auth/readiness",
        headers={"Authorization": "Bearer pint-016-stub:operator"},
    )
    assert stub.status_code == 403, stub.text
    assert stub.json()["error"]["details"]["reason"] == "AUTH_STUB_SESSION_REJECTED"


def test_strict_operator_interaction_mutation_succeeds_and_negative_roles_fail(monkeypatch) -> None:
    _strict_env(monkeypatch)
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    suffix = uuid.uuid4().hex
    operator = {
        "Authorization": f"Bearer {_token('operator', subject='pint-016-operator')}",
        "X-Tenant-Id": "tenant-pint-016",
    }
    context = {
        "environment": "paper",
        "context_refs": [
            {"type": "strategy", "id": f"strategy-{suffix}", "version_id": "v1"}
        ],
    }

    resolved = client.post(
        "/bff/agora/interactions/context:resolve",
        headers={**operator, "Idempotency-Key": f"context-{suffix}"},
        json=context,
    )
    assert resolved.status_code == 200, resolved.text
    workshop_id = resolved.json()["data"]["workshop_id"]

    interaction = client.post(
        "/bff/agora/interactions",
        headers={**operator, "Idempotency-Key": f"interaction-{suffix}"},
        json={
            "workshop_id": workshop_id,
            "mode": "challenge",
            "environment": "paper",
            "topic": "Challenge the current thesis without execution authority",
            "participant_persona_ids": ["pint-016-persona"],
            "context_refs": context["context_refs"],
        },
    )
    assert interaction.status_code == 202, interaction.text
    assert interaction.json()["data"]["execution_authority"] == "none"

    viewer = client.post(
        "/bff/agora/interactions/context:resolve",
        headers={
            "Authorization": f"Bearer {_token('viewer')}",
            "X-Tenant-Id": "tenant-pint-016",
            "Idempotency-Key": f"viewer-{suffix}",
        },
        json=context,
    )
    assert viewer.status_code == 403, viewer.text
    assert viewer.json()["error"]["details"]["precondition_failed"] == "role_check"

    unauthenticated = client.post(
        "/bff/agora/interactions/context:resolve",
        headers={"Idempotency-Key": f"unauth-{suffix}"},
        json=context,
    )
    assert unauthenticated.status_code == 401, unauthenticated.text


def test_cookie_mutation_requires_allowed_origin(monkeypatch) -> None:
    _strict_env(monkeypatch)
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    client.cookies.set("pantheon_session", _token("operator"))

    missing = client.post("/bff/auth/refresh", json={})
    assert missing.status_code == 403, missing.text
    assert missing.json()["error"]["details"]["reason"] == "COOKIE_SESSION_ORIGIN_DENIED"

    denied = client.post(
        "/bff/auth/refresh",
        json={},
        headers={"Origin": "https://attacker.example"},
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        "/bff/auth/refresh",
        json={},
        headers={"Origin": FRONTEND_ORIGIN},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["session"]["session_kind"] == "cookie"
