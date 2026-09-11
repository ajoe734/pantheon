"""Real JWT login/cookie/reload/logout on an isolated existing session store."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from ..session_lifecycle_store import SessionLifecycleStore
from . import policy
from .browser_session import DevBrowserSessionMiddleware
from .handlers import create_auth_handlers
from .router import create_auth_router
from .service import AuthFacadeService

ORIGIN = "https://app.dev.mvl-cap.tw"
BASE = "https://api.dev.mvl-cap.tw"
CREDENTIALS = {"grant_type": "client_credentials", "client_id": "test-account", "client_secret": "test-password"}


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    for name, value in {
        "PANTHEON_ENV": "dev", "PANTHEON_DEPLOYMENT_STAGE": "dev",
        "PANTHEON_BFF_AUTH_MODE": "strict", "PANTHEON_BFF_AUTH_STUB": "false",
        "PANTHEON_BFF_MFA_REQUIRED": "false",
        "PANTHEON_BFF_JWT_SECRET": "synthetic-browser-test-signing-secret",
        "PANTHEON_BFF_DEV_LOGIN_JWT_SECRET": "synthetic-browser-test-signing-secret",
        "PANTHEON_BFF_JWT_ISSUER": "pantheon-dev",
        "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_CLIENT_ID": "test-account",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_CLIENT_SECRET": "test-password",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_TENANT_ID": "tenant-dev",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_ALLOWED_TENANTS": "tenant-dev",
    }.items():
        monkeypatch.setenv(name, value)

    def build():
        store = SessionLifecycleStore(str(tmp_path / "sessions.json"))
        deps = policy.create_auth_dependencies(session_lifecycle_store=store)
        handlers = create_auth_handlers(dependencies=deps)
        service = AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)
        app = FastAPI()
        allowed = lambda origin: origin == ORIGIN
        app.include_router(create_auth_router(service=service, browser_origin_allowed=allowed))
        app.add_middleware(
            DevBrowserSessionMiddleware, enabled=policy.dev_login_enabled,
            origin_allowed=allowed,
            validate_session=lambda token: deps.raise_if_session_logged_out(deps.extract_identity(f"Bearer {token}")),
        )
        app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

        @app.api_route("/bff/test-business", methods=["GET", "POST"])
        def business(authorization: str | None = Header(default=None)):
            identity = deps.extract_identity(authorization)
            deps.require_read_role(identity)
            return {"operator_id": identity.operator_id, "tenant_id": identity.claims["tenant_id"], "roles": identity.roles}

        return app

    return build


def login(client, **overrides):
    return client.post("/bff/auth/dev-login", headers={"Origin": ORIGIN}, json={**CREDENTIALS, "browser_session": True, **overrides})


def test_browser_login_reload_business_and_durable_logout(app_factory):
    with TestClient(app_factory(), base_url=BASE) as client:
        response = login(client)
        assert response.status_code == 200, response.text
        assert "access_token" not in response.json()
        header = response.headers["set-cookie"]
        assert all(value in header for value in ["HttpOnly", "Secure", "SameSite=lax", "Path=/bff", "Max-Age="])
        assert "Domain=" not in header
        token = client.cookies.get("pantheon_session")
        assert client.get("/bff/me").status_code == 200
        assert client.get("/bff/auth/readiness").status_code == 200
        business = client.get("/bff/test-business")
        assert business.status_code == 200
        assert business.json()["tenant_id"] == "tenant-dev"
        assert client.post("/bff/test-business", headers={"Origin": ORIGIN}).status_code == 200

    # A fresh application/client reads the persisted session using only cookie.
    with TestClient(app_factory(), base_url=BASE) as reloaded:
        reloaded.cookies.set("pantheon_session", token, domain="api.dev.mvl-cap.tw", path="/bff")
        assert reloaded.get("/bff/me").status_code == 200
        assert reloaded.get("/bff/test-business").status_code == 200
        logout = reloaded.post("/bff/logout", headers={"Origin": ORIGIN}, json={})
        assert logout.status_code == 200, logout.text
        assert reloaded.cookies.get("pantheon_session") is None
    with TestClient(app_factory(), base_url=BASE) as restarted:
        restarted.cookies.set("pantheon_session", token, domain="api.dev.mvl-cap.tw", path="/bff")
        assert restarted.get("/bff/me").status_code == 401
        assert restarted.get("/bff/test-business").status_code == 401


@pytest.mark.parametrize("origin", [None, "null", "https://attacker.example", ORIGIN + ".attacker.example"])
def test_cookie_mutations_and_login_require_exact_origin(app_factory, origin):
    with TestClient(app_factory(), base_url=BASE) as client:
        headers = {} if origin is None else {"Origin": origin}
        response = client.post("/bff/auth/dev-login", headers=headers, json={**CREDENTIALS, "browser_session": True})
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        assert login(client).status_code == 200
        assert client.post("/bff/test-business", headers=headers).status_code == 403
        assert client.post("/bff/logout", headers=headers).status_code == 403


def test_invalid_credentials_and_authorization_precedence(app_factory):
    with TestClient(app_factory(), base_url=BASE) as client:
        denied = login(client, client_secret="wrong-password")
        assert denied.status_code == 401
        assert "set-cookie" not in denied.headers
        assert login(client).status_code == 200
        assert client.get("/bff/test-business", headers={"Authorization": "Bearer invalid"}).status_code == 401
        client.cookies.clear()
        client.cookies.set("pantheon_session", "invalid", domain="api.dev.mvl-cap.tw", path="/bff")
        denied = client.get("/bff/test-business", headers={"Origin": ORIGIN})
        assert denied.status_code == 401
        assert denied.headers["access-control-allow-origin"] == ORIGIN
        assert login(client).status_code == 200


def test_cli_login_remains_bearer_and_does_not_require_origin(app_factory):
    with TestClient(app_factory(), base_url=BASE) as client:
        response = client.post("/bff/auth/dev-login", json=CREDENTIALS)
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        token = response.json()["access_token"]
        assert client.post("/bff/test-business", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_production_does_not_enable_dev_cookie_adapter(app_factory, monkeypatch):
    app = app_factory()
    with TestClient(app, base_url=BASE) as client:
        assert login(client).status_code == 200
        monkeypatch.setenv("PANTHEON_ENV", "production")
        assert login(client).status_code == 403
        assert client.get("/bff/test-business").status_code == 401
