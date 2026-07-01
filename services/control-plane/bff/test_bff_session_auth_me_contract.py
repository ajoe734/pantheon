"""Contract tests for BFF-LUV-GAP-009 `/bff/me` current-user DTO."""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from session_lifecycle_store import SessionLifecycleStore
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


@pytest.fixture(autouse=True)
def isolated_session_lifecycle_store(tmp_path):
    original_store = bff_main.session_lifecycle_store
    bff_main.session_lifecycle_store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    try:
        yield
    finally:
        bff_main.session_lifecycle_store = original_store


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


def test_bff_me_permissive_structured_token_includes_dev_kernel_capabilities(monkeypatch) -> None:
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
        headers={"Authorization": "Bearer pantheon-dev-browser:admin,operator:mfa"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["session"]["mfa_verified"] is True
    assert set(data["roles"]) == {"admin", "operator"}
    assert "assistant.kernel.debug" in data["capabilities"]
    assert "assistant.kernel.repair" in data["capabilities"]


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


def test_bff_auth_refresh_replays_by_idempotency_alias(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(bff_main.app)
    headers = {
        "Authorization": OPERATOR_TOKEN,
        "X-Idempotency-Key": "refresh-alias-op-2",
    }
    first = client.post("/bff/auth/refresh", json={}, headers=headers)
    second = client.post("/bff/auth/refresh", json={}, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["meta"]["idempotency"]["idempotencyKey"] == "refresh-alias-op-2"
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
        headers={"Idempotency-Key": "refresh-cookie-op"},
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
        headers={"Idempotency-Key": "logout-cookie-op"},
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
    assert 300 <= payload["expires_in"] <= 3600
    assert payload["expires_in"] == 600
    assert payload["meta"]["contract"] == "FE-INT-GATE-OIDC-DEV-LOGIN"

    me = client.get("/bff/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["currentUser"]["id"] == "pantheon-dev-ci-client"
    assert data["session"]["session_kind"] == "bearer"
    assert data["tenant"]["id"] == "tenant-alpha"
    assert set(data["roles"]) == {"operator", "reviewer", "approver"}


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
    assert set(data["roles"]) == {"operator", "reviewer", "approver"}


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


def test_bff_dev_login_disabled_for_staging_live(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "staging-live")
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
