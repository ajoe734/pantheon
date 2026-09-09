from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import create_auth_handlers
from services.control_plane.bff.auth.policy import create_auth_dependencies
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.runtime_auth_inbound import encode_jwt_hs256


JWT_SECRET = "test-bff-auth-refresh-secret"
JWT_ISSUER = "pantheon-bff-auth-refresh-test"
JWT_AUDIENCE = "bff-operators"


_current_store: SessionLifecycleStore | None = None


@pytest.fixture(autouse=True)
def isolated_session_lifecycle_store(tmp_path):
    global _current_store
    _current_store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    yield _current_store
    _current_store = None


def _create_app(store: SessionLifecycleStore) -> FastAPI:
    deps = create_auth_dependencies(session_lifecycle_store=store)
    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(
        local_readiness=handlers["bff_auth_readiness"],
        handlers=handlers,
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    app.include_router(create_auth_router(service=service))
    return app


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


def test_bff_auth_refresh_uses_bearer_refresh_credential(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(extra={"sid": "session-bearer-refresh"})

    assert _current_store is not None
    client = TestClient(_create_app(_current_store))
    response = client.post(
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


def test_bff_auth_refresh_uses_refresh_cookie_credential(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(extra={"sid": "session-cookie-refresh"})

    assert _current_store is not None
    client = TestClient(_create_app(_current_store))
    client.cookies.set("pantheon_refresh", token)
    response = client.post("/bff/auth/refresh", json={})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["operation"]["refreshCredential"]["source"] == "refresh_cookie"
    assert data["session"]["session_kind"] == "cookie"
    assert data["session"]["id"] == "session-cookie-refresh"
    assert data["session"]["last_refresh_credential_source"] == "refresh_cookie"


def test_bff_auth_refresh_missing_refresh_path_returns_typed_401(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)

    assert _current_store is not None
    client = TestClient(_create_app(_current_store), raise_server_exceptions=False)
    response = client.post(
        "/bff/auth/refresh",
        json={},
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["details"]["reason"] == "AUTH_REFRESH_CREDENTIAL_REQUIRED"
    assert error["details"]["precondition_failed"] == "refresh_credential"
