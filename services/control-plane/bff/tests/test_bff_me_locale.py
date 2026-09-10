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


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
    assert _current_store is not None
    app = _create_app(_current_store)
    return TestClient(app)


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
