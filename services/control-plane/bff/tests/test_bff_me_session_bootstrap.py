from __future__ import annotations

import os
import sys

import tempfile
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import AuthDependencies, create_auth_handlers
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore


def _make_client() -> TestClient:
    td = tempfile.TemporaryDirectory()
    store = SessionLifecycleStore(f"{td.name}/sessions.json")

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

    def _extract_identity(authorization: str | None = None, **kwargs):
        if not authorization or not authorization.startswith("Bearer "):
            raise _bff_error(
                401,
                ErrorCode.AUTH_REQUIRED,
                "Missing or invalid Authorization header",
                "Token is absent or not a Bearer token",
            )
        token = authorization[len("Bearer "):].strip()
        parts = token.split(":")
        op = parts[0]
        roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
        return OperatorIdentity(operator_id=op, roles=roles, token_kind="stub", claims={})

    allowed = [t.strip() for t in os.getenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-primary,tenant-alt").split(",") if t.strip()]
    default_tenant = os.getenv("PANTHEON_BFF_TENANT_ID", "tenant-primary")

    deps = AuthDependencies(
        bff_error=_bff_error,
        dev_login_forbidden_environment=lambda: False,
        dev_login_identity_registry=lambda: {},
        extract_identity=_extract_identity,
        require_read_role=lambda id: None,
        raise_if_session_logged_out=lambda id: None,
        session_lifecycle_store=store,
        bff_me_tenant_payload=lambda identity, requested_tenant=None: {
            "id": requested_tenant or default_tenant,
            "requested_id": requested_tenant,
            "default_id": default_tenant,
            "allowed_ids": allowed,
            "scope": "tenant",
        },
        capabilities_for_identity=lambda id: ["runtime.read", "operator.write"],
        bff_auth_stub_enabled=lambda: True,
        bff_auth_mode=lambda: "permissive",
        bff_source_commit=lambda: "HEAD",
        write_roles=frozenset({"operator", "admin"}),
        utc_now=lambda: "2026-09-05T00:00:00Z",
    )

    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)
    app = FastAPI()
    app.include_router(create_auth_router(service=service))

    @app.exception_handler(HTTPException)
    def _http_exc_handler(request: Request, exc: HTTPException):
        corr = request.headers.get("X-Correlation-Id") or "corr-default"
        headers = {"X-Correlation-Id": corr}
        content = {
            "error": {
                "code": "AUTH_REQUIRED",
                "message": "Token is absent or not a Bearer token",
                "details": {"reason": "Token is absent or not a Bearer token"},
            },
            "meta": {"correlationId": corr},
        }
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            err = dict(exc.detail["error"])
            content["error"] = err
            content["meta"] = {"correlationId": corr}
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    client = TestClient(app)
    client._temp_dir = td
    return client


def test_bff_me_returns_session_bootstrap_payload(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-primary")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-primary,tenant-alt")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
    monkeypatch.setenv("PANTHEON_BFF_FEATURE_FLAGS", "plansLive=true,alpha=false")

    response = _make_client().get(
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


def test_bff_me_anonymous_returns_typed_401_with_correlation(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")

    response = _make_client().get(
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
