from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.control_plane.bff.tests.auth_session_app_support import build_auth_session_app
from services.runtime_auth_inbound import encode_jwt_hs256


JWT_SECRET = "test-bff-auth-refresh-secret"
JWT_ISSUER = "pantheon-bff-auth-refresh-test"
JWT_AUDIENCE = "bff-operators"


@pytest.fixture(autouse=True)
def app(tmp_path):
    store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    return build_auth_session_app(store)


def _strict_auth_env(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")


def _jwt_token(*, subject: str = "op-refresh", roles: list[str] | None = None, extra: dict | None = None) -> str:
    payload = {
        "sub": subject,
        "roles": roles or ["operator"],
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    if extra:
        payload.update(extra)
    return encode_jwt_hs256(payload, secret=JWT_SECRET)


def test_bff_auth_refresh_uses_bearer_refresh_credential(app, monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(extra={"sid": "session-bearer-refresh"})

    response = TestClient(app).post(
        "/bff/auth/refresh",
        json={},
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "bearer-refresh-1",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["operation"]["type"] == "refresh"
    assert data["operation"]["refreshCredential"]["source"] == "bearer"
    assert data["auth"]["refreshCredential"]["source"] == "bearer"
    assert data["session"]["session_kind"] == "bearer"
    assert data["session"]["state"] == "active"
    assert data["session"]["last_refreshed_at"]
    assert payload["meta"]["auth"]["refreshCredentialSource"] == "bearer"


def test_bff_auth_refresh_uses_refresh_cookie_credential(app, monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(extra={"sid": "session-cookie-refresh"})

    client = TestClient(app)
    client.cookies.set("pantheon_refresh", token)
    response = client.post("/bff/auth/refresh", json={})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["operation"]["refreshCredential"]["source"] == "refresh_cookie"
    assert data["session"]["session_kind"] == "cookie"
    assert data["session"]["id"] == "session-cookie-refresh"
    assert data["session"]["last_refresh_credential_source"] == "refresh_cookie"


def test_bff_auth_refresh_missing_refresh_path_returns_typed_401(app, monkeypatch) -> None:
    _strict_auth_env(monkeypatch)

    response = TestClient(app, raise_server_exceptions=False).post(
        "/bff/auth/refresh",
        json={},
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["details"]["reason"] == "AUTH_REFRESH_CREDENTIAL_REQUIRED"
    assert error["details"]["precondition_failed"] == "refresh_credential"
