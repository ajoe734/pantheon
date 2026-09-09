from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import create_auth_handlers
from services.control_plane.bff.auth.policy import create_auth_dependencies
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore


from pathlib import Path


def _create_app(tmp_path: Path) -> FastAPI:
    store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    deps = create_auth_dependencies(session_lifecycle_store=store)
    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(
        local_readiness=handlers["bff_auth_readiness"],
        handlers=handlers,
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        headers = dict(exc.headers or {})
        correlation_id = request.headers.get("X-Correlation-Id")
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        if isinstance(exc.detail, dict):
            content = dict(exc.detail)
            if correlation_id:
                content["meta"] = {"correlationId": correlation_id}
            return JSONResponse(status_code=exc.status_code, content=content, headers=headers)
        content = {"detail": exc.detail}
        if correlation_id:
            content["meta"] = {"correlationId": correlation_id}
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    app.include_router(create_auth_router(service=service))
    return app


def test_bff_me_returns_session_bootstrap_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-primary")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-primary,tenant-alt")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
    monkeypatch.setenv("PANTHEON_BFF_FEATURE_FLAGS", "plansLive=true,alpha=false")

    app = _create_app(tmp_path)
    response = TestClient(app).get(
        "/bff/me?tenant_id=tenant-alt",
        headers={
            "Authorization": "Bearer op-bootstrap:operator,approver:mfa",
            "X-Correlation-Id": "corr-bff-b1-003",
            "X-Locale": "zh-TW",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-Correlation-Id"] == "corr-bff-b1-003"
    body = response.json()
    data = body["data"]

    assert data["operatorId"] == "op-bootstrap"
    assert data["operator_id"] == "op-bootstrap"
    assert data["roles"] == ["operator", "approver"]
    assert data["tenantId"] == "tenant-alt"
    assert data["tenant_id"] == "tenant-alt"
    assert data["allowedTenants"] == ["tenant-primary", "tenant-alt"]
    assert data["allowed_tenants"] == ["tenant-primary", "tenant-alt"]
    assert data["locale"]["resolved"] == "zh-TW"
    assert data["sessionKind"] == "stub"
    assert data["session_kind"] == "stub"
    assert data["session"]["authenticated"] is True
    assert data["session"]["session_kind"] == "stub"
    assert data["featureFlags"]["executePlansBff"] is True
    assert data["featureFlags"]["plansLive"] is True
    assert data["featureFlags"]["alpha"] is False
    assert "runtime.read" in data["capabilities"]
    assert body["meta"]["correlationId"] == "corr-bff-b1-003"


def test_bff_me_anonymous_returns_typed_401_with_correlation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")

    app = _create_app(tmp_path)
    response = TestClient(app).get(
        "/bff/me",
        headers={"X-Correlation-Id": "corr-anonymous-bff-b1-003"},
    )

    assert response.status_code == 401
    assert response.headers["X-Correlation-Id"] == "corr-anonymous-bff-b1-003"
    body = response.json()
    assert "detail" not in body
    assert body["meta"]["correlationId"] == "corr-anonymous-bff-b1-003"
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert body["error"]["details"]["reason"] == "Token is absent or not a Bearer token"
    assert "correlationId" not in body["error"]["details"]
