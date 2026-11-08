"""Contract tests for BFF-LUV-GAP-009 `/bff/me` current-user DTO."""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.control_plane.bff.tests.management_session_harness import ManagementSessionHarness
from services.runtime_auth_inbound import encode_jwt_hs256

OPERATOR_TOKEN = "Bearer op-2:operator,reviewer:mfa"
DEV_GATE_TOKEN = "Bearer pantheon-dev-browser:operator,reviewer,approver:mfa"
JWT_SECRET = "test-bff-me-secret"
JWT_ISSUER = "pantheon-bff-me-test"
JWT_AUDIENCE = "bff-operators"


def _jwt_token(*, roles: list[str], extra: dict | None = None) -> str:
    payload = {
        "sub": "op-jwt",
        "roles": roles,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    if extra:
        payload.update(extra)
    return encode_jwt_hs256(payload, secret=JWT_SECRET)


def _strict_auth_env(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://frontend.test")


class DynamicHarness(ManagementSessionHarness):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from services.control_plane.bff.core.errors import register_error_handlers
        register_error_handlers(self.app)

    @property
    def auth_stub(self) -> bool:
        val = os.environ.get("PANTHEON_BFF_AUTH_STUB", "")
        if not val:
            return False
        return val.lower() in ("true", "1", "yes")

    @auth_stub.setter
    def auth_stub(self, val):
        pass

    @property
    def auth_mode(self) -> str:
        return os.environ.get("PANTHEON_BFF_AUTH_MODE", "strict")

    @auth_mode.setter
    def auth_mode(self, val):
        pass

    @property
    def jwt_secret(self) -> str:
        return os.environ.get("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)

    @jwt_secret.setter
    def jwt_secret(self, val):
        pass

    @property
    def jwt_issuer(self) -> str:
        return os.environ.get("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)

    @jwt_issuer.setter
    def jwt_issuer(self, val):
        pass

    @property
    def jwt_audience(self) -> str:
        return os.environ.get("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)

    @jwt_audience.setter
    def jwt_audience(self, val):
        pass


_harness: DynamicHarness | None = None


class _AppProxy:
    def __getattr__(self, name):
        if _harness is None:
            raise RuntimeError("Harness not initialized")
        return getattr(_harness.app, name)

    async def __call__(self, scope, receive, send):
        if _harness is None:
            raise RuntimeError("Harness not initialized")
        return await _harness.app(scope, receive, send)


class _BffMainMock:
    app = _AppProxy()

    @property
    def session_lifecycle_store(self):
        return _harness.session_lifecycle_store if _harness else None

    @session_lifecycle_store.setter
    def session_lifecycle_store(self, val):
        if _harness is not None:
            _harness.session_lifecycle_store = val

    @property
    def auth_deps(self):
        return _harness.auth_deps if _harness else None

    @property
    def auth_facade_service(self):
        return _harness.auth_service if _harness else None


bff_main = _BffMainMock()


@pytest.fixture(autouse=True)
def isolated_session_lifecycle_store(tmp_path, monkeypatch):
    global _harness
    store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    _harness = DynamicHarness(
        store=store,
        jwt_secret=JWT_SECRET,
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
    )
    try:
        yield
    finally:
        if _harness is not None:
            _harness.close()
            _harness = None


def test_bff_me_stub_returns_frontend_ready_current_user_dto(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    monkeypatch.setenv("PANTHEON_ENV", "paper")
    monkeypatch.setenv("PANTHEON_REGION", "us-central1")
    monkeypatch.setenv("PANTHEON_TIMEZONE", "Asia/Taipei")
    monkeypatch.setenv("PANTHEON_BFF_FEATURE_FLAGS", "executePlansPanel=enabled")

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/me",
        headers={
            "Authorization": OPERATOR_TOKEN,
            "X-Tenant-Id": "tenant-beta",
            "X-Locale": "zh_TW",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert body["meta"]["contract"] == "BFF-LUV-GAP-009"
    assert data["user"]["operator_id"] == "op-2"
    assert data["currentUser"]["id"] == "op-2"
    assert data["roles"] == ["operator", "reviewer"]
    assert "runtime.read" in data["capabilities"]
    assert data["tenant"]["id"] == "tenant-beta"
    assert data["tenant"]["allowed_ids"] == ["tenant-alpha", "tenant-beta"]
    assert data["tenant_id"] == "tenant-beta"
    assert data["locale"]["resolved"] == "zh-TW"
    assert data["environment"]["name"] == "paper"
    assert data["environment"]["auth_mode"] == "stub"
    assert data["feature_flags"]["sessionAuthMe"] is True
    assert data["feature_flags"]["executePlansPanel"] is True
    assert data["session"]["authenticated"] is True
    assert data["session"]["fresh"] is True
    assert data["session"]["mfa_verified"] is True


def test_bff_me_permissive_operator_keeps_explicit_dev_kernel_capabilities(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "")
    monkeypatch.setenv("PANTHEON_BFF_STUB_CAPABILITIES", "")

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/me",
        headers={
            "Authorization": (
                "Bearer pantheon-dev-browser:admin,operator:mfa:"
                "assistant.kernel.debug,assistant.kernel.repair"
            )
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["session"]["mfa_verified"] is True
    assert set(data["roles"]) == {"admin", "operator"}
    assert "assistant.kernel.debug" in data["capabilities"]
    assert "assistant.kernel.repair" in data["capabilities"]


def test_bff_me_permissive_viewer_does_not_inherit_dev_kernel_capabilities(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "")
    monkeypatch.setenv(
        "PANTHEON_BFF_STUB_CAPABILITIES",
        "assistant.kernel.debug,assistant.kernel.repair",
    )

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/me",
        headers={"Authorization": "Bearer pantheon-dev-browser:viewer"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["roles"] == ["viewer"]
    assert set(data["capabilities"]) == {"metric.read", "strategy.view", "persona.view"}
    assert "assistant.kernel.debug" not in data["capabilities"]
    assert "assistant.kernel.repair" not in data["capabilities"]


def test_bff_me_permissive_rejects_plain_no_role_bearer(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "")

    client = TestClient(bff_main.app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer definitely-invalid-no-role-token"}

    me_response = client.get("/bff/me", headers=headers)
    fleet_response = client.get("/bff/management/persona-fleet", headers=headers)

    assert me_response.status_code in {401, 403}, me_response.text
    assert fleet_response.status_code in {401, 403}, fleet_response.text


def test_bff_me_stub_rejects_plain_no_role_bearer(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.delenv("PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS", raising=False)

    client = TestClient(bff_main.app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer definitely-invalid-no-role-token"}

    me_response = client.get("/bff/me", headers=headers)
    fleet_response = client.get("/bff/management/persona-fleet", headers=headers)

    assert me_response.status_code == 403, me_response.text
    assert fleet_response.status_code == 403, fleet_response.text


def test_session_lifecycle_routes_require_auth_by_default(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)

    client = TestClient(bff_main.app, raise_server_exceptions=False)
    cases = [
        ("POST", "/bff/auth/refresh", {}),
        ("POST", "/bff/logout", {}),
        ("POST", "/bff/switch-tenant", {"tenantId": "tenant-alpha"}),
        ("PATCH", "/bff/me/locale", {"locale": "en-US"}),
    ]

    for method, path, body in cases:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, (method, path, response.text)


def test_bff_auth_refresh_returns_session_dto_without_command_receipt(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")

    client = TestClient(bff_main.app)
    response = client.post(
        "/bff/auth/refresh",
        json={},
        headers={
            "Authorization": OPERATOR_TOKEN,
            "Idempotency-Key": "refresh-op-2",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert payload["meta"]["contract"] == "BFF-LUV-SEM-001"
    assert data["operation"]["type"] == "refresh"
    assert data["session"]["authenticated"] is True
    assert data["session"]["state"] == "active"
    assert data["currentUser"]["id"] == "op-2"
    assert "commandId" not in data
    assert "receipt" not in data


def test_bff_auth_refresh_replays_by_idempotency_key_and_ignores_retired_alias(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": OPERATOR_TOKEN,
        "Idempotency-Key": "refresh-canonical-op-2",
        "X-Idempotency-Key": "retired-alias-ignored",
    }
    first = client.post("/bff/auth/refresh", json={}, headers=headers)
    second = client.post("/bff/auth/refresh", json={}, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["meta"]["idempotency"]["idempotencyKey"] == "refresh-canonical-op-2"
    assert second_payload["meta"]["idempotency"]["replayed"] is True
    assert second_payload["data"]["operation"]["operation_id"] == first_payload["data"]["operation"]["operation_id"]
    assert second_payload["data"]["operation"]["performed_at"] == first_payload["data"]["operation"]["performed_at"]


def test_bff_auth_refresh_accepts_cookie_session_in_strict_mode(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(roles=["operator"], extra={"sid": "session-cookie-refresh"})

    client = TestClient(bff_main.app)
    client.cookies.set("pantheon_session", token)
    response = client.post(
        "/bff/auth/refresh",
        json={},
        headers={"Idempotency-Key": "refresh-cookie-op", "Origin": "https://frontend.test"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert payload["meta"]["contract"] == "BFF-LUV-SEM-001"
    assert data["operation"]["type"] == "refresh"
    assert data["currentUser"]["id"] == "op-jwt"
    assert data["session"]["authenticated"] is True
    assert data["session"]["state"] == "active"
    assert data["session"]["session_kind"] == "cookie"
    assert data["session"]["id"] == "session-cookie-refresh"


def test_bff_switch_tenant_persists_allowed_tenant_for_me(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")

    client = TestClient(bff_main.app)
    switched = client.post(
        "/bff/switch-tenant",
        json={"tenantId": "tenant-beta"},
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["data"]["tenant"]["id"] == "tenant-beta"
    assert switched.json()["data"]["tenant"]["source"] == "session"

    me = client.get("/bff/me", headers={"Authorization": OPERATOR_TOKEN})
    assert me.status_code == 200, me.text
    assert me.json()["data"]["tenant"]["id"] == "tenant-beta"
    assert me.json()["data"]["tenant"]["source"] == "session"


def test_bff_switch_tenant_rejects_scope_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(bff_main.app)
    response = client.post(
        "/bff/switch-tenant",
        json={"tenantId": "tenant-gamma"},
        headers={"Authorization": "Bearer op-switch:operator"},
    )

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["details"]["precondition_failed"] == "tenant_scope"
    assert error["details"]["tenantId"] == "tenant-gamma"


def test_bff_update_locale_normalizes_and_persists_for_me(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(bff_main.app)
    updated = client.patch(
        "/bff/me/locale",
        json={"locale": "zh_tw"},
        headers={"Authorization": OPERATOR_TOKEN},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["locale"]["resolved"] == "zh-TW"
    assert updated.json()["data"]["locale"]["source"] == "session"

    me = client.get("/bff/me", headers={"Authorization": OPERATOR_TOKEN})
    assert me.status_code == 200, me.text
    assert me.json()["data"]["locale"]["resolved"] == "zh-TW"
    assert me.json()["data"]["locale"]["source"] == "session"


def test_bff_logout_is_idempotent_session_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": OPERATOR_TOKEN,
        "Idempotency-Key": "logout-op-2",
    }
    first = client.post("/bff/logout", json={}, headers=headers)
    second = client.post("/bff/logout", json={}, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["data"]["session"]["authenticated"] is False
    assert first_payload["data"]["session"]["state"] == "logged_out"
    assert second_payload["meta"]["idempotency"]["replayed"] is True
    assert second_payload["data"]["operation"]["operation_id"] == first_payload["data"]["operation"]["operation_id"]
    assert second_payload["data"]["session"]["logged_out_at"] == first_payload["data"]["session"]["logged_out_at"]

    me = client.get("/bff/me", headers={"Authorization": OPERATOR_TOKEN})
    assert me.status_code == 401, me.text
    assert me.json()["error"]["details"]["reason"] == "SESSION_LOGGED_OUT"


def test_bff_logout_accepts_cookie_session_in_strict_mode(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(roles=["operator"], extra={"sid": "session-cookie-logout"})

    client = TestClient(bff_main.app)
    client.cookies.set("pantheon_session", token)
    response = client.post(
        "/bff/logout",
        json={},
        headers={"Idempotency-Key": "logout-cookie-op", "Origin": "https://frontend.test"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert payload["meta"]["contract"] == "BFF-LUV-SEM-001"
    assert data["operation"]["type"] == "logout"
    assert data["currentUser"]["id"] == "op-jwt"
    assert data["session"]["authenticated"] is False
    assert data["session"]["state"] == "logged_out"
    assert data["session"]["session_kind"] == "cookie"
    assert data["session"]["id"] == "session-cookie-logout"
    assert data["session"]["logged_out_at"]
    assert "max-age=0" in response.headers.get("set-cookie", "").lower()

    me = client.get("/bff/me")
    assert me.status_code == 401, me.text


def test_bff_session_lifecycle_routes_are_visible_in_openapi(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    response = client.get("/openapi.json")

    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    assert "post" in paths["/bff/auth/dev-login"]
    assert "post" in paths["/bff/auth/refresh"]
    assert "post" in paths["/bff/logout"]
    assert "post" in paths["/bff/switch-tenant"]
    assert "patch" in paths["/bff/me/locale"]


def test_bff_dev_login_issues_short_lived_jwt_for_me(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", "600")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    # Product OIDC and server-side dev-login coexist: the HS256 dev-login JWT
    # must keep using the BFF verifier even when browser ES256/JWKS discovery is
    # configured, and external strict role mapping must not erase its internal
    # server-bound operator role.
    monkeypatch.setenv(
        "PANTHEON_BFF_OIDC_DISCOVERY_URL",
        "https://identity.example.test/.well-known/openid-configuration",
    )
    monkeypatch.setenv("PANTHEON_BFF_OIDC_ISSUER", "https://identity.example.test")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_AUDIENCE", "authenticated")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_CLAIMS", "app_metadata.roles,roles")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP", "pantheon-operator=operator")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_ROLE", "viewer")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
            "roles": ["operator"],
            "tenant_id": "tenant-alpha",
        },
    )

    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["token_type"] == "bearer"
    assert 300 <= payload["expires_in"] <= 3600
    assert payload["expires_in"] == 600
    assert payload["meta"]["contract"] == "FE-INT-GATE-OIDC-DEV-LOGIN"
    assert payload["meta"]["identity"] == "operator"

    me = client.get("/bff/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["currentUser"]["id"] == "pantheon-dev-operator"
    assert data["session"]["session_kind"] == "bearer"
    assert data["tenant"]["id"] == "tenant-alpha"
    assert set(data["roles"]) == {"operator"}


def test_bff_dev_login_defaults_match_frontend_dev_gate_session(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
        },
    )

    assert login.status_code == 200, login.text
    me = client.get(
        "/bff/me",
        headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Tenant-Id": "tenant-dev",
        },
    )
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["tenant"]["id"] == "tenant-dev"
    assert data["tenant"]["allowed_ids"] == ["tenant-dev"]
    assert set(data["roles"]) == {"operator"}


def test_bff_dev_login_rejects_role_escalation_beyond_bound_identity(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
            "roles": ["admin"],
        },
    )
    assert login.status_code == 403, login.text
    error = login.json()["error"]
    assert error["details"]["reason"] == "AUTH_DEV_LOGIN_ESCALATION_DENIED"
    assert error["details"]["precondition_failed"] == "roles"


def test_bff_dev_login_rejects_cross_tenant_escalation(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
            "tenant_id": "tenant-unconfigured",
        },
    )
    assert login.status_code == 403, login.text
    error = login.json()["error"]
    assert error["details"]["reason"] == "AUTH_DEV_LOGIN_ESCALATION_DENIED"
    assert error["details"]["precondition_failed"] == "tenant_id"


def test_bff_dev_login_distinct_identities_have_distinct_subjects_and_roles(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", "viewer-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "viewer-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_ID", "approver-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET", "approver-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID", "risk-owner-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET", "risk-owner-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID", "operator-a-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET", "operator-a-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID", "operator-b-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET", "operator-b-secret")

    client = TestClient(bff_main.app)

    def _login(client_id, client_secret):
        resp = client.post(
            "/bff/auth/dev-login",
            json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    viewer = _login("viewer-client", "viewer-secret")
    approver = _login("approver-client", "approver-secret")
    risk_owner = _login("risk-owner-client", "risk-owner-secret")
    operator_a = _login("operator-a-client", "operator-a-secret")
    operator_b = _login("operator-b-client", "operator-b-secret")

    assert viewer["meta"]["identity"] == "viewer"
    assert approver["meta"]["identity"] == "approver"
    assert risk_owner["meta"]["identity"] == "risk_owner"

    def _jwt_claims(token):
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))

    def _me(token):
        resp = client.get("/bff/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    viewer_data = _me(viewer["access_token"])
    approver_data = _me(approver["access_token"])
    operator_a_data = _me(operator_a["access_token"])
    operator_b_data = _me(operator_b["access_token"])
    # risk_owner is not in the generic read-role set (_READ_ROLES), so /bff/me
    # correctly 403s for it; assert its claims directly off the issued JWT.
    risk_owner_claims = _jwt_claims(risk_owner["access_token"])

    for payload in (viewer, approver, risk_owner, operator_a, operator_b):
        assert "mfa_verified" not in _jwt_claims(payload["access_token"])

    for data in (viewer_data, approver_data, operator_a_data, operator_b_data):
        assert data["session"]["mfa_verified"] is False

    assert set(viewer_data["roles"]) == {"viewer"}
    assert set(approver_data["roles"]) == {"approver", "governance_reviewer"}
    assert set(risk_owner_claims["roles"]) == {"risk_owner"}
    assert set(operator_a_data["roles"]) == {"operator"}
    assert set(operator_b_data["roles"]) == {"operator"}

    # Operator A and B share a role but must never share a subject/actor id.
    assert operator_a_data["currentUser"]["id"] != operator_b_data["currentUser"]["id"]
    subjects = {
        viewer_data["currentUser"]["id"],
        approver_data["currentUser"]["id"],
        risk_owner_claims["sub"],
        operator_a_data["currentUser"]["id"],
        operator_b_data["currentUser"]["id"],
    }
    assert len(subjects) == 5


def test_bff_dev_login_unconfigured_identity_has_no_shared_fallback(monkeypatch) -> None:
    # viewer/approver/risk_owner/operator_a/operator_b must NOT fall back to
    # the legacy shared operator credential when their own dedicated
    # credential is not configured.
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={"grant_type": "client_credentials", "client_id": "viewer-client", "client_secret": "viewer-secret"},
    )
    assert login.status_code == 401, login.text
    assert login.json()["error"]["details"]["reason"] == "AUTH_DEV_LOGIN_CLIENT_CREDENTIALS"


def test_bff_dev_login_single_role_fallback_when_unspecified(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
        },
    )

    assert login.status_code == 200, login.text
    me = client.get(
        "/bff/me",
        headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
        },
    )
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["tenant"]["id"] == "tenant-dev"
    assert set(data["roles"]) == {"operator"}


def test_dev_gate_session_allows_me_and_management_reads(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev,pantheon-dev")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": DEV_GATE_TOKEN,
        "X-Tenant-Id": "tenant-dev",
    }
    paths = [
        "/bff/me",
        "/bff/strategies",
        "/bff/personas",
        "/bff/management/human-inbox",
        "/bff/management/evidence",
    ]

    for path in paths:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)

    data = client.get("/bff/me", headers=headers).json()["data"]
    assert data["tenant"]["id"] == "tenant-dev"
    assert set(data["roles"]) == {"operator", "reviewer", "approver"}


def test_management_reads_reject_tenant_scope_like_me(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": DEV_GATE_TOKEN,
        "X-Tenant-Id": "tenant-other",
    }

    for path in ["/bff/me", "/bff/strategies", "/bff/management/human-inbox"]:
        response = client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.text)
        error = response.json()["error"]
        assert error["details"]["precondition_failed"] == "tenant_scope"
        assert error["details"]["tenantId"] == "tenant-other"


def test_management_reads_reject_role_missing_like_me(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": "Bearer op-roleless:auditor:mfa",
        "X-Tenant-Id": "tenant-dev",
    }

    for path in ["/bff/me", "/bff/strategies", "/bff/management/human-inbox"]:
        response = client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.text)
        error = response.json()["error"]
        assert error["details"]["precondition_failed"] == "role_check"


def test_management_reads_reject_logged_out_session_like_me(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": DEV_GATE_TOKEN,
        "X-Tenant-Id": "tenant-dev",
    }

    logout = client.post("/bff/logout", headers=headers)
    assert logout.status_code == 200, logout.text

    for path in ["/bff/me", "/bff/strategies", "/bff/management/human-inbox"]:
        response = client.get(path, headers=headers)
        assert response.status_code == 401, (path, response.text)
        assert response.json()["error"]["details"]["reason"] == "SESSION_LOGGED_OUT"


def test_bff_dev_login_rejects_bad_client_secret(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")

    client = TestClient(bff_main.app)
    response = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-client", "client_secret": "wrong-secret"},
    )

    assert response.status_code == 401, response.text
    error = response.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["details"]["reason"] == "AUTH_DEV_LOGIN_CLIENT_CREDENTIALS"


@pytest.mark.parametrize("environment", ["staging", "staging-live", "prod", "production", "live", "canary"])
@pytest.mark.parametrize("selector", ["PANTHEON_ENV", "PANTHEON_DEPLOYMENT_STAGE"])
def test_bff_dev_login_disabled_outside_dev(monkeypatch, environment, selector) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv(selector, environment)
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")

    client = TestClient(bff_main.app)
    response = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-client", "client_secret": "ci-secret"},
    )

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "PRECONDITION_FAILED"
    assert error["details"]["precondition_failed"] == "dev_login"


@pytest.mark.parametrize("identity", ["operator", "viewer", "approver", "risk_owner", "operator_a", "operator_b"])
def test_dev_login_ignores_legacy_static_mfa_and_client_mfa_claims(monkeypatch, identity) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    prefix = f"PANTHEON_BFF_DEV_LOGIN_{identity.upper()}"
    monkeypatch.setenv(f"{prefix}_CLIENT_ID", f"{identity}-client")
    monkeypatch.setenv(f"{prefix}_CLIENT_SECRET", "credential-secret")
    # Existing machine-local configuration must not turn a password exchange
    # into a second-factor attestation, even if a caller makes the same claim.
    monkeypatch.setenv(f"{prefix}_MFA_VERIFIED", "true")
    client = TestClient(bff_main.app)
    login = client.post("/bff/auth/dev-login", json={
        "client_id": f"{identity}-client", "client_secret": "credential-secret",
        "mfa_verified": True, "amr": ["pwd", "mfa"],
    })
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    encoded = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert "mfa_verified" not in claims
    assert "amr" not in claims
    assert claims["identity"] == identity
    if identity != "risk_owner":
        me = client.get("/bff/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["data"]["session"]["authenticated"] is True
        assert me.json()["data"]["session"]["mfa_verified"] is False


def test_password_only_dev_login_cannot_satisfy_explicit_mfa_requirement(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "true")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_CLIENT_SECRET", "ci-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED", "true")
    client = TestClient(bff_main.app)
    login = client.post("/bff/auth/dev-login", json={
        "client_id": "ci-client", "client_secret": "ci-secret",
    })
    assert login.status_code == 200, login.text
    me = client.get("/bff/me", headers={
        "Authorization": f"Bearer {login.json()['access_token']}",
    })
    assert me.status_code == 401, me.text
    assert me.json()["error"]["details"]["reason"] == "MFA_REQUIRED"


def test_bff_me_propagates_accept_language_when_x_locale_absent(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/me",
        headers={
            "Authorization": "Bearer op-locale:operator",
            "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["locale"]["resolved"] == "fr-CA"


def test_bff_me_rejects_tenant_scope_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/me",
        headers={
            "Authorization": "Bearer op-tenant:operator",
            "X-Tenant-Id": "tenant-gamma",
        },
    )

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["details"]["precondition_failed"] == "tenant_scope"
    assert error["details"]["tenantId"] == "tenant-gamma"
    assert error["details"]["allowedTenantIds"] == ["tenant-alpha"]


def test_bff_me_strict_auth_requires_bearer_token(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)

    client = TestClient(bff_main.app)
    response = client.get("/bff/me")

    assert response.status_code == 401, response.text
    error = response.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["details"]["reason"]


def test_bff_me_strict_auth_allows_viewer_read_role(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(roles=["viewer"], extra={"tenant_id": "tenant-alpha"})

    client = TestClient(bff_main.app)
    response = client.get("/bff/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["roles"] == ["viewer"]
    assert data["tenant"]["id"] == "tenant-alpha"


def test_bff_dev_login_default_ttl_meets_proof_floor(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")
    monkeypatch.delenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", raising=False)

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
        },
    )

    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 1800
    assert payload["meta"]["contract"] == "FE-INT-GATE-OIDC-DEV-LOGIN"
    assert payload["meta"]["ttl_seconds"] == 1800
    assert payload["meta"]["identity"] == "operator"

    payload_b64 = payload["access_token"].split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    assert claims["exp"] - claims["iat"] == 1800

    me = client.get("/bff/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["currentUser"]["id"] == "pantheon-dev-operator"
    assert set(data["roles"]) == {"operator"}


@pytest.mark.parametrize(
    "raw_ttl,expected_ttl",
    [
        ("200", 300),          # below 300 floor clamped to 300
        ("300", 300),          # minimum bound
        ("600", 600),          # intermediate valid
        ("1200", 1200),        # 20-minute valid
        ("1800", 1800),        # 30-minute default (strictly above proof window floor)
        ("3600", 3600),        # maximum bound
        ("7200", 3600),        # above 3600 cap clamped to 3600
        ("invalid", 1800),     # unparseable falls back to default 1800
        ("", 1800),            # empty falls back to default 1800
        ("-500", 300),         # negative clamped to 300
    ],
)
def test_bff_dev_login_ttl_bounds_and_invalid_fallback(monkeypatch, raw_ttl: str, expected_ttl: int) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", raw_ttl)

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "ci-secret",
        },
    )

    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["expires_in"] == expected_ttl
    assert payload["meta"]["ttl_seconds"] == expected_ttl

    payload_b64 = payload["access_token"].split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    assert claims["exp"] - claims["iat"] == expected_ttl

    me = client.get("/bff/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200, me.text


def test_bff_dev_login_minted_credential_passes_proof_preflight_validator(monkeypatch) -> None:
    """Verify newly minted dev-login credentials strictly exceed the 1200-second proof preflight window."""
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-operator-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "ci-operator-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", "ci-viewer-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "ci-viewer-secret")
    monkeypatch.delenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", raising=False)

    client = TestClient(bff_main.app)

    # 1. Mint operator token
    op_login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-operator-client",
            "client_secret": "ci-operator-secret",
        },
    )
    assert op_login.status_code == 200, op_login.text
    op_token = op_login.json()["access_token"]

    # 2. Mint viewer token
    vw_login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-viewer-client",
            "client_secret": "ci-viewer-secret",
        },
    )
    assert vw_login.status_code == 200, vw_login.text
    vw_token = vw_login.json()["access_token"]

    # 3. Direct claims validation against proof floor requirement:
    # Proof validator rejects expiresAt <= nowSeconds + 1200 (HOSTED_PROOF_MIN_CREDENTIAL_TTL_SECONDS = 1200)
    now_seconds = int(time.time())
    proof_window_floor = 1200

    for token, label in [(op_token, "operator"), (vw_token, "viewer")]:
        parts = token.split(".")
        assert len(parts) == 3
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        expires_at = int(claims["exp"])
        assert expires_at > now_seconds + proof_window_floor, (
            f"{label} token exp={expires_at} must strictly exceed now + 1200 "
            f"({now_seconds + proof_window_floor}); remaining={expires_at - now_seconds}s"
        )


def test_bff_auth_login_test_admin_preserves_stable_subject_and_exposes_client_id_display_name(
    monkeypatch,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_ID", "admintest")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_SECRET", "test-admin-secret")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "admintest",
            "client_secret": "test-admin-secret",
        },
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["meta"]["identity"] == "test_admin"
    token = payload["access_token"]

    me = client.get("/bff/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["currentUser"]["id"] == "pantheon-dev-admin-test"
    assert data["user"]["operator_id"] == "pantheon-dev-admin-test"
    assert data["currentUser"]["display_name"] == "admintest"
    assert data["user"]["display_name"] == "admintest"
    assert set(data["roles"]) == {
        "admin",
        "operator",
        "approver",
        "governance_reviewer",
        "risk_owner",
        "reviewer_on_duty",
        "reviewer",
    }


def test_bff_persona_fleet_works_for_same_tenant_viewer_and_view_only_via_real_registry_read_contract(
    monkeypatch,
) -> None:
    """BFF-VIEWER-REGISTRY-READ-20261106:
    Prove that ``GET /bff/management/persona-fleet?purpose=trading&data_scope=regular&archived=exclude&page_size=20``
    works for same-tenant ``viewer`` (minted via ``/bff/auth/dev-login`` and tested via both
    ``Authorization: Bearer`` and browser ``Cookie: pantheon_session``) and ``view_only``
    principals through the real Registry read contract (``GET /api/registry/strategy-specs?view=persona_binding_counts``)
    in strict JWT mode with actual owner data and without empty fallback data or a
    substituted service or operator token, while preserving operator/admin reads and
    denying cross-tenant requests.
    """
    import urllib.error
    from urllib.parse import urlparse

    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
    from services.registry import service as reg_service
    from services.registry.split_api import RegistryService
    from services.registry.storage import RegistryStore

    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", "viewer-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "viewer-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID", "operator-a-client")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET", "operator-a-secret")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_ID", "admintest")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_SECRET", "test-admin-secret")

    # Configure real Registry service in strict JWT mode sharing the same JWT verifier settings
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.real.local")

    remote_store = RegistryStore()
    remote_svc = RegistryService(remote_store)
    monkeypatch.setattr(reg_service, "get_registry_service", lambda: remote_svc)
    monkeypatch.setattr(reg_service, "get_store", lambda: remote_store)
    reg_client = TestClient(reg_service.app)

    op_seed_token = _jwt_token(
        roles=["operator"],
        extra={"sub": "op-seed-dev", "tenant_id": "tenant-dev", "tenant": "tenant-dev"},
    )
    op_other_token = _jwt_token(
        roles=["operator"],
        extra={"sub": "op-seed-other", "tenant_id": "tenant-other", "tenant": "tenant-other"},
    )

    def _spec_payload(strategy_id: str, title: str) -> dict:
        return {
            "spec_version": "1.0",
            "strategy_id": strategy_id,
            "title": title,
            "hypothesis": "Orderflow mean-reverts over short horizons.",
            "objective": "Verify fleet routed-strategy counts against real strict Registry.",
            "market_scope": {"symbols": ["2330.TW"], "frequency": "1h"},
            "data_dependencies": [{"ref": "seed-fleet-1", "kind": "note"}],
            "execution_profile": {
                "signal_schema_version": "research-signal-v1",
                "quantity_type": "PERCENT_PORTFOLIO",
                "execution_mode_hint": "paper",
            },
            "evaluation_plan": {"metrics": ["sharpe_ratio"], "candidate_gate": "schema-valid"},
            "governance": {"approval_required": True, "risk_profile": "research"},
            "provenance": {
                "source_kind": "workflow",
                "created_at": "2026-11-06T00:00:00Z",
                "source_refs": ["seed-fleet-1"],
                "created_by": "op-seed-dev",
            },
        }

    for reg_id, strat_id, personas in (
        ("reg-fleet-spec-1", "strat-fleet-tw-1", ["persona-tw-alpha", "persona-us-core"]),
        ("reg-fleet-spec-2", "strat-fleet-tw-2", ["persona-tw-alpha"]),
    ):
        resp_seed = reg_client.post(
            "/api/registry/strategy-specs",
            json={
                "registry_id": reg_id,
                "strategy_id": strat_id,
                "version": "1.0.0",
                "artifact_state": "candidate",
                "lineage": {"source_run_ids": ["run-fleet-1"]},
                "metadata": {"persona_ids": personas, "title": f"Title {strat_id}"},
                "strategy_spec": _spec_payload(strat_id, f"Title {strat_id}"),
            },
            headers={"Authorization": f"Bearer {op_seed_token}"},
        )
        assert resp_seed.status_code == 200, resp_seed.text

    resp_other = reg_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-fleet-spec-other",
            "strategy_id": "strat-fleet-other",
            "version": "1.0.0",
            "artifact_state": "candidate",
            "lineage": {"source_run_ids": ["run-other-1"]},
            "metadata": {"persona_ids": ["persona-tw-alpha"], "title": "Foreign Tenant Strategy"},
            "strategy_spec": _spec_payload("strat-fleet-other", "Foreign Tenant Strategy"),
        },
        headers={"Authorization": f"Bearer {op_other_token}"},
    )
    assert resp_other.status_code == 200, resp_other.text

    forwarded_registry_requests: list[dict] = []

    def strict_registry_bridge(
        url: str,
        *,
        method: str = "GET",
        payload=None,
        auth_token=None,
        tenant_id=None,
        **kwargs,
    ):
        parsed = urlparse(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        forwarded_registry_requests.append(
            {
                "method": method,
                "path": path,
                "auth_token": auth_token,
                "tenant_id": tenant_id,
            }
        )
        hdrs = {}
        if auth_token:
            hdrs["Authorization"] = f"Bearer {auth_token}"
        if tenant_id:
            hdrs["X-Tenant-Id"] = tenant_id
        resp = reg_client.request(method, path, json=payload, headers=hdrs)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(url, resp.status_code, resp.text, hdrs=None, fp=None)
        return resp.json()

    monkeypatch.setattr(adapter_base, "http_request_json", strict_registry_bridge)

    from services.control_plane.bff.ports import create_in_memory_read_surface_ports

    assert _harness is not None
    _harness.default_tenant_id = "tenant-dev"
    _harness.allowed_tenants = ["tenant-dev"]
    _harness.app.add_middleware(OwnerReadContextMiddleware)

    fleet_read_store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "personas": [
                {
                    "id": "persona-tw-alpha",
                    "persona_id": "persona-tw-alpha",
                    "name": "TW Alpha Trading Persona",
                    "tenant_id": "tenant-dev",
                    "owner": "op-seed-dev",
                    "lifecycle_state": "paper_running",
                    "status": "paper_running",
                    "metadata": {
                        "tenant_id": "tenant-dev",
                        "market_scope": ["TW"],
                        "persona_class": "trading",
                        "persona_purpose": "trading",
                        "data_scope": "regular",
                    },
                },
                {
                    "id": "persona-us-core",
                    "persona_id": "persona-us-core",
                    "name": "US Core Trading Persona",
                    "tenant_id": "tenant-dev",
                    "owner": "op-seed-dev",
                    "lifecycle_state": "draft",
                    "status": "draft",
                    "metadata": {
                        "tenant_id": "tenant-dev",
                        "market_scope": ["US"],
                        "persona_class": "trading",
                        "persona_purpose": "trading",
                        "data_scope": "regular",
                    },
                },
            ]
        }
    )
    _harness.persona_service._read_store = fleet_read_store

    client = TestClient(bff_main.app)

    def _dev_login(client_id: str, client_secret: str) -> str:
        resp = client.post(
            "/bff/auth/dev-login",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    viewer_token = _dev_login("viewer-client", "viewer-secret")
    operator_token = _dev_login("operator-a-client", "operator-a-secret")
    admin_token = _dev_login("admintest", "test-admin-secret")
    view_only_token = _jwt_token(
        roles=["view_only"],
        extra={
            "sub": "pantheon-dev-view-only",
            "tenant_id": "tenant-dev",
            "tenant": "tenant-dev",
            "sid": "sess-view-only-1",
        },
    )

    fleet_path = "/bff/management/persona-fleet?purpose=trading&data_scope=regular&archived=exclude&page_size=20"

    # 1. Bearer GET /bff/management/persona-fleet succeeds for viewer, view_only, operator, and admin
    for label, token in (
        ("viewer", viewer_token),
        ("view_only", view_only_token),
        ("operator", operator_token),
        ("admin", admin_token),
    ):
        forwarded_registry_requests.clear()
        resp = client.get(
            fleet_path,
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"},
        )
        assert resp.status_code == 200, (label, resp.text)
        body = resp.json()
        items_by_id = {
            (item.get("persona_id") or item.get("id")): item
            for item in body["data"]["items"]
        }
        assert set(items_by_id) >= {"persona-tw-alpha", "persona-us-core"}, (label, set(items_by_id))
        tw_row = items_by_id["persona-tw-alpha"]
        us_row = items_by_id["persona-us-core"]
        assert tw_row.get("routed_strategies") == 2, (label, tw_row)
        assert us_row.get("routed_strategies") == 1, (label, us_row)
        assert any(
            req["path"] == "/api/registry/strategy-specs?view=persona_binding_counts"
            and req["auth_token"] == token
            and req["tenant_id"] == "tenant-dev"
            for req in forwarded_registry_requests
        ), (label, forwarded_registry_requests)

    # 2. Browser cookie-only GET /bff/management/persona-fleet (pantheon_session cookie, no Authorization header)
    for label, token in (("viewer_cookie", viewer_token), ("view_only_cookie", view_only_token)):
        forwarded_registry_requests.clear()
        cookie_client = TestClient(bff_main.app)
        cookie_client.cookies.set("pantheon_session", token)
        resp_cookie = cookie_client.get(fleet_path, headers={"X-Tenant-Id": "tenant-dev"})
        assert resp_cookie.status_code == 200, (label, resp_cookie.text)
        items_by_id = {
            (item.get("persona_id") or item.get("id")): item
            for item in resp_cookie.json()["data"]["items"]
        }
        assert items_by_id["persona-tw-alpha"].get("routed_strategies") == 2
        assert items_by_id["persona-us-core"].get("routed_strategies") == 1
        assert any(
            req["path"] == "/api/registry/strategy-specs?view=persona_binding_counts"
            and req["auth_token"] == token
            and req["tenant_id"] == "tenant-dev"
            for req in forwarded_registry_requests
        ), (label, forwarded_registry_requests)

    # 3. Cross-tenant selector on /bff/management/persona-fleet remains denied (403)
    cross_resp = client.get(
        fleet_path,
        headers={"Authorization": f"Bearer {viewer_token}", "X-Tenant-Id": "tenant-other"},
    )
    assert cross_resp.status_code == 403, cross_resp.text

