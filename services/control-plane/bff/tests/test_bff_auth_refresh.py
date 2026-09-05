from __future__ import annotations

import os
import sys
import time

from typing import Any
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import AuthDependencies, create_auth_handlers
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.runtime_auth_inbound import AuthError, encode_jwt_hs256, validate_request_auth


JWT_SECRET = "test-bff-auth-refresh-secret"
JWT_ISSUER = "pantheon-bff-auth-refresh-test"
JWT_AUDIENCE = "bff-operators"


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


def _make_client(tmp_path) -> TestClient:
    store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))

    def _bff_error(status_code: int, code: Any, message: str, reason: str, precondition_failed: str | None = None, **kwargs):
        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "code": str(getattr(code, "value", code)),
                    "message": message,
                    "details": {
                        "reason": reason,
                        "precondition_failed": precondition_failed,
                    },
                }
            },
        )

    def _extract_identity(
        authorization: str | None = None,
        mfa_token: str | None = None,
        session_cookie: str | None = None,
        **kwargs,
    ) -> OperatorIdentity:
        token_str = authorization
        is_cookie = False
        if not token_str and session_cookie:
            token_str = f"Bearer {session_cookie}"
            is_cookie = True
        if not token_str or not token_str.startswith("Bearer "):
            raise _bff_error(401, ErrorCode.AUTH_REQUIRED, "Token required", "AUTH_REQUIRED")

        bff_env = {
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_SECRET": JWT_SECRET,
            "PANTHEON_RUNTIME_JWT_ISSUER": JWT_ISSUER,
            "PANTHEON_RUNTIME_JWT_AUDIENCE": JWT_AUDIENCE,
            "PANTHEON_RUNTIME_DEFAULT_ROLE": "operator",
            "PANTHEON_RUNTIME_MFA_REQUIRED": "false",
            "PANTHEON_RUNTIME_ROLE_CLAIMS": "roles,role",
            "PANTHEON_RUNTIME_ROLE_MAP_MODE": "passthrough",
            "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": "false",
        }
        try:
            ctx = validate_request_auth(
                authorization=token_str,
                mfa_header=mfa_token or "",
                mfa_required=False,
                env=bff_env,
            )
        except AuthError as exc:
            raise _bff_error(exc.status_code, ErrorCode.AUTH_REQUIRED, exc.message, exc.code)

        claims = getattr(ctx, "claims", {}) or {}
        return OperatorIdentity(
            operator_id=getattr(ctx, "actor_id", "op-jwt"),
            roles=getattr(ctx, "roles", ["operator"]),
            token_kind="cookie" if is_cookie else "bearer",
            claims=claims,
        )

    deps = AuthDependencies(
        bff_error=_bff_error,
        dev_login_forbidden_environment=lambda: False,
        dev_login_identity_registry=lambda: {},
        extract_identity=_extract_identity,
        require_read_role=lambda id: None,
        raise_if_session_logged_out=lambda id: None,
        session_lifecycle_store=store,
        bff_me_tenant_payload=lambda identity, requested_tenant=None: {
            "id": requested_tenant or "pantheon-dev",
            "requested_id": requested_tenant,
            "default_id": "pantheon-dev",
            "allowed_ids": ["pantheon-dev"],
            "scope": "tenant",
        },
        capabilities_for_identity=lambda id: ["operator.write"],
        bff_auth_stub_enabled=lambda: False,
        bff_auth_mode=lambda: "strict",
        bff_source_commit=lambda: "HEAD",
        write_roles=frozenset({"operator", "admin"}),
        utc_now=lambda: "2026-09-05T00:00:00Z",
    )

    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)
    app = FastAPI()
    app.include_router(create_auth_router(service=service))

    @app.exception_handler(HTTPException)
    def _http_exc_handler(request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return TestClient(app, raise_server_exceptions=False)


def test_bff_auth_refresh_uses_bearer_refresh_credential(monkeypatch, tmp_path) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(extra={"sid": "session-bearer-refresh"})
    client = _make_client(tmp_path)

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


def test_bff_auth_refresh_uses_refresh_cookie_credential(monkeypatch, tmp_path) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(extra={"sid": "session-cookie-refresh"})
    client = _make_client(tmp_path)

    client.cookies.set("pantheon_refresh", token)
    response = client.post("/bff/auth/refresh", json={})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["operation"]["refreshCredential"]["source"] == "refresh_cookie"
    assert data["session"]["session_kind"] == "cookie"
    assert data["session"]["id"] == "session-cookie-refresh"
    assert data["session"]["last_refresh_credential_source"] == "refresh_cookie"


def test_bff_auth_refresh_missing_refresh_path_returns_typed_401(monkeypatch, tmp_path) -> None:
    _strict_auth_env(monkeypatch)
    client = _make_client(tmp_path)

    response = client.post(
        "/bff/auth/refresh",
        json={},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "AUTH_REQUIRED"
    assert payload["error"]["details"]["reason"] == "AUTH_REFRESH_CREDENTIAL_REQUIRED"
    assert payload["error"]["details"]["precondition_failed"] == "refresh_credential"
