from __future__ import annotations

import os
import sys

import tempfile
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import AuthDependencies, create_auth_handlers
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")

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
            raise _bff_error(401, ErrorCode.AUTH_REQUIRED, "Token required", "AUTH_REQUIRED")
        token = authorization[len("Bearer "):].strip()
        parts = token.split(":")
        op = parts[0]
        roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
        return OperatorIdentity(operator_id=op, roles=roles, token_kind="stub", claims={})

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
    def _http_exc_handler(request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    client = TestClient(app)
    # keep td alive on client
    client._temp_dir = td
    return client


def test_patch_bff_me_locale_updates_locale(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.patch(
        "/bff/me/locale",
        json={"locale": "zh-TW"},
        headers={"Authorization": "Bearer op-locale:operator"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert data["locale"]["resolved"] == "zh-TW"
    assert data["locale"]["source"] == "session"
    assert data["operation"]["type"] == "update_locale"


def test_patch_bff_me_locale_normalises_case(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.patch(
        "/bff/me/locale",
        json={"locale": "ZH-tw"},
        headers={"Authorization": "Bearer op-locale:operator"},
    )

    assert response.status_code == 200, response.text
    resolved = response.json()["data"]["locale"]["resolved"]
    assert resolved == "zh-TW"


def test_patch_bff_me_locale_persists_to_session(monkeypatch) -> None:
    client = _client(monkeypatch)
    auth = "Bearer op-persist:operator"

    patch_resp = client.patch(
        "/bff/me/locale",
        json={"locale": "ja-JP"},
        headers={"Authorization": auth},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    get_resp = client.get("/bff/me", headers={"Authorization": auth})
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["data"]["locale"]["resolved"] == "ja-JP"
    assert get_resp.json()["data"]["locale"]["source"] == "session"


def test_patch_bff_me_locale_anonymous_returns_401(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.patch("/bff/me/locale", json={"locale": "en-US"})

    assert response.status_code == 401


def test_patch_bff_me_locale_missing_locale_returns_400(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.patch(
        "/bff/me/locale",
        json={},
        headers={"Authorization": "Bearer op-locale:operator"},
    )

    assert response.status_code == 400
    detail = response.json()
    assert detail["error"]["code"] == "VALIDATION_FAILED"
    assert detail["error"]["details"]["precondition_failed"] == "locale"


def test_patch_bff_me_locale_invalid_locale_tag_returns_400(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.patch(
        "/bff/me/locale",
        json={"locale": "not-a"},
        headers={"Authorization": "Bearer op-locale:operator"},
    )

    assert response.status_code == 400
    detail = response.json()
    assert detail["error"]["code"] == "VALIDATION_FAILED"
