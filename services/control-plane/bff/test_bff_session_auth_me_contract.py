"""Contract tests for BFF-LUV-GAP-009 `/bff/me` current-user DTO."""
from __future__ import annotations

import base64
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
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
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://frontend.test")


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
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "true")
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
    for identity in ("VIEWER", "APPROVER", "RISK_OWNER", "OPERATOR_A", "OPERATOR_B"):
        monkeypatch.setenv(f"PANTHEON_BFF_DEV_LOGIN_{identity}_MFA_VERIFIED", "true")

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
        assert _jwt_claims(payload["access_token"])["mfa_verified"] is True

    assert set(viewer_data["roles"]) == {"viewer"}
    assert set(approver_data["roles"]) == {"approver"}
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



