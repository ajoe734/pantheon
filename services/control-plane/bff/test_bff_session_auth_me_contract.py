"""Contract tests for BFF-LUV-GAP-009 `/bff/me` current-user DTO."""
from __future__ import annotations

import os
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from session_lifecycle_store import SessionLifecycleStore
from services.runtime_auth_inbound import encode_jwt_hs256


OPERATOR_TOKEN = "Bearer op-2:operator,reviewer:mfa"
DEV_GATE_TOKEN = "Bearer pantheon-dev-operator:operator,reviewer,approver:mfa"
PUBLIC_VIEWER_TOKEN = "Bearer pantheon-dev-browser:viewer"
JWT_SECRET = "test-bff-me-secret-32-bytes-minimum"
JWT_ISSUER = "pantheon-bff-me-test"
JWT_AUDIENCE = "bff-operators"
CI_PROFILE_SECRET = "ci-agora-secret-value-2026-00000000"
KERNEL_PROFILE_SECRET = "kernel-operator-secret-value-2026-0000"
OPERATOR_A_SECRET = "operator-a-secret-value-2026-000000000"
OPERATOR_B_SECRET = "operator-b-secret-value-2026-000000000"


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
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
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
        headers={"Authorization": "Bearer pantheon-dev-operator:admin,operator:mfa"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["session"]["mfa_verified"] is True
    assert set(data["roles"]) == {"admin", "operator"}
    assert "assistant.kernel.debug" in data["capabilities"]
    assert "assistant.kernel.repair" in data["capabilities"]


def test_bff_me_public_browser_subject_is_viewer_only(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_BFF_STUB_CAPABILITIES",
        "assistant.kernel.debug,assistant.kernel.repair",
    )
    privileged_token = _jwt_token(
        roles=["admin", "operator"],
        extra={"sub": "pantheon-dev-browser", "amr": ["mfa"]},
    )
    client = TestClient(bff_main.app)

    viewer = client.get(
        "/bff/me",
        headers={"Authorization": PUBLIC_VIEWER_TOKEN},
    )
    privileged = client.get(
        "/bff/me",
        headers={"Authorization": f"Bearer {privileged_token}"},
    )

    assert viewer.status_code == 200, viewer.text
    assert viewer.json()["data"]["roles"] == ["viewer"]
    assert viewer.json()["data"]["capabilities"] == []
    assert privileged.status_code == 403, privileged.text
    assert (
        privileged.json()["error"]["details"]["reason"]
        == "AUTH_PUBLIC_BROWSER_IDENTITY_PRIVILEGED"
    )


def test_bff_me_public_browser_strict_jwt_does_not_inherit_dev_capabilities(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_BFF_STUB_CAPABILITIES",
        "assistant.kernel.debug,assistant.kernel.repair",
    )
    token = _jwt_token(roles=["viewer"], extra={"sub": "pantheon-dev-browser"})

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["roles"] == ["viewer"]
    assert response.json()["data"]["capabilities"] == []


def test_bff_me_exact_public_browser_viewer_is_available_in_strict_mode(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_BFF_STUB_CAPABILITIES",
        "assistant.kernel.debug,assistant.kernel.repair",
    )

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": PUBLIC_VIEWER_TOKEN},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["roles"] == ["viewer"]
    assert data["capabilities"] == []
    assert data["session"]["auth_mode"] == "public"


@pytest.mark.parametrize(
    "token",
    [
        "pantheon-dev-browser:viewer:mfa",
        "pantheon-dev-browser:viewer,operator",
        "pantheon-dev-browser:operator",
        "another-dev-browser:viewer",
    ],
)
def test_bff_me_strict_mode_rejects_public_viewer_near_matches(
    monkeypatch,
    token,
) -> None:
    _strict_auth_env(monkeypatch)

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code in {401, 403}, response.text


@pytest.mark.parametrize("environment", ["staging-live", "production"])
def test_bff_me_public_viewer_exception_is_forbidden_outside_dev(
    monkeypatch,
    environment,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", environment)

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": PUBLIC_VIEWER_TOKEN},
    )

    assert response.status_code in {401, 403}, response.text


@pytest.mark.parametrize(
    ("environment", "deployment_stage"),
    [
        ("dev", ""),
        ("", "dev"),
        ("local", ""),
        ("", "local"),
        ("dev", "local"),
        (" DEV ", " LOCAL "),
    ],
)
@pytest.mark.parametrize("auth_mode", ["strict", " STRICT "])
def test_bff_me_public_viewer_allows_only_normalized_strict_dev_local(
    monkeypatch,
    environment,
    deployment_stage,
    auth_mode,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", environment)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", deployment_stage)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", auth_mode)

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": PUBLIC_VIEWER_TOKEN},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["session"]["auth_mode"] == "public"


@pytest.mark.parametrize(
    ("environment", "deployment_stage"),
    [
        ("", ""),
        ("staging", ""),
        ("staging-live", ""),
        ("production", ""),
        ("prod", ""),
        ("live", ""),
        ("canary", ""),
        ("paper", ""),
        ("sandbox", ""),
        ("development", ""),
        ("dev-preview", ""),
        ("local-dev", ""),
        ("dev", "staging"),
        ("local", "production"),
        ("production", "dev"),
    ],
)
@pytest.mark.parametrize(
    ("auth_mode", "auth_stub"),
    [
        ("strict", ""),
        ("permissive", ""),
        ("permissive", "true"),
        ("strict-preview", "true"),
    ],
)
def test_bff_me_public_viewer_rejects_forbidden_envs_without_parser_fallback(
    monkeypatch,
    environment,
    deployment_stage,
    auth_mode,
    auth_stub,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", environment)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", deployment_stage)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", auth_mode)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", auth_stub)

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": PUBLIC_VIEWER_TOKEN},
    )

    assert response.status_code == 403, response.text
    assert (
        response.json()["error"]["details"]["reason"]
        == "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"
    )


@pytest.mark.parametrize(
    ("auth_mode", "auth_stub"),
    [
        ("permissive", ""),
        ("permissive", "true"),
        ("strict-preview", "true"),
    ],
)
@pytest.mark.parametrize("environment", ["dev", "local"])
def test_bff_me_public_viewer_rejects_non_strict_auth_in_allowed_env(
    monkeypatch,
    auth_mode,
    auth_stub,
    environment,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", environment)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", auth_mode)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", auth_stub)

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": PUBLIC_VIEWER_TOKEN},
    )

    assert response.status_code == 403, response.text
    assert (
        response.json()["error"]["details"]["reason"]
        == "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"
    )


@pytest.mark.parametrize(
    "authorization",
    [
        " Bearer pantheon-dev-browser:viewer",
        "Bearer pantheon-dev-browser:viewer ",
        " Bearer pantheon-dev-browser:viewer ",
        "Bearer  pantheon-dev-browser:viewer",
        "Bearer\tpantheon-dev-browser:viewer",
        "Bearer \tpantheon-dev-browser:viewer",
        "bearer pantheon-dev-browser:viewer",
        "Bearer Pantheon-dev-browser:viewer",
        "Bearer pantheon-dev-browser:Viewer",
        "Bearer pantheon-dev-browser :viewer",
        "Bearer pantheon-dev-browser\t:viewer",
        "Bearer pantheon-dev-browser:\tviewer",
        "Bearer pantheon-dev-browser:viewer:",
    ],
)
def test_bff_me_public_viewer_rejects_header_near_matches(
    monkeypatch,
    authorization,
) -> None:
    _strict_auth_env(monkeypatch)

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": authorization},
    )

    assert response.status_code == 401, response.text
    assert (
        response.json()["error"]["details"]["reason"]
        == "AUTH_PUBLIC_BROWSER_TOKEN_NEAR_MATCH"
    )


@pytest.mark.parametrize(
    "claim",
    [
        {"capabilities": ["assistant.kernel.repair"]},
        {"capability": "assistant.kernel.repair"},
        {"permissions": ["assistant.kernel.repair"]},
        {"scp": "assistant.kernel.repair"},
        {"scope": "assistant.kernel.repair"},
    ],
)
def test_bff_me_public_browser_strict_jwt_rejects_capability_aliases(
    monkeypatch,
    claim,
) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(
        roles=["viewer"],
        extra={"sub": "pantheon-dev-browser", **claim},
    )

    response = TestClient(bff_main.app).get(
        "/bff/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403, response.text
    assert (
        response.json()["error"]["details"]["reason"]
        == "AUTH_PUBLIC_BROWSER_IDENTITY_PRIVILEGED"
    )


@pytest.mark.parametrize(
    ("method", "path", "payload", "extra_headers"),
    [
        ("POST", "/bff/logout", {}, {}),
        (
            "POST",
            "/bff/tools",
            {"name": "must-not-be-created"},
            {"Idempotency-Key": "public-viewer-tool-write"},
        ),
        (
            "POST",
            "/api/v1/internal/sse/publish?event_type=kill_switch.public_injection&channel=system",
            {"forged": True},
            {},
        ),
        ("PATCH", "/bff/me/locale", {"locale": "zh-TW"}, {}),
        ("PUT", "/bff/me", {}, {}),
        (
            "DELETE",
            "/bff/confirm-tokens/public-viewer-token",
            {},
            {"Idempotency-Key": "public-viewer-delete"},
        ),
    ],
)
@pytest.mark.parametrize(
    (
        "auth_mode",
        "auth_stub",
        "environment",
        "deployment_stage",
        "expected_reason",
    ),
    [
        ("strict", "", "dev", "", "AUTH_PUBLIC_BROWSER_READ_ONLY"),
        ("strict", "", "", "local", "AUTH_PUBLIC_BROWSER_READ_ONLY"),
        ("strict", "", "staging", "", "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"),
        ("strict", "", "production", "", "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"),
        ("permissive", "", "dev", "", "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"),
        ("permissive", "true", "local", "", "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"),
        ("strict-preview", "true", "dev", "", "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"),
        ("permissive", "true", "staging", "", "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"),
    ],
)
def test_public_viewer_token_rejects_every_mutating_http_method(
    monkeypatch,
    method,
    path,
    payload,
    extra_headers,
    auth_mode,
    auth_stub,
    environment,
    deployment_stage,
    expected_reason,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", auth_mode)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", auth_stub)
    monkeypatch.setenv("PANTHEON_ENV", environment)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", deployment_stage)
    headers = {"Authorization": PUBLIC_VIEWER_TOKEN, **extra_headers}

    response = TestClient(bff_main.app).request(
        method,
        path,
        headers=headers,
        json=payload,
    )

    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == expected_reason
    if expected_reason == "AUTH_PUBLIC_BROWSER_READ_ONLY":
        assert details["method"] == method
        assert details["allowed_methods"] == ["GET", "HEAD"]


def test_public_viewer_method_gate_allows_get_and_head(
    monkeypatch,
) -> None:
    _strict_auth_env(monkeypatch)
    client = TestClient(bff_main.app)
    headers = {"Authorization": PUBLIC_VIEWER_TOKEN}

    get_response = client.get("/bff/me", headers=headers)
    head_response = client.head("/bff/me", headers=headers)

    assert get_response.status_code == 200, get_response.text
    # FastAPI does not register HEAD automatically for this GET route, but the
    # public credential's method gate must not turn the router's 405 into 403.
    assert head_response.status_code == 405, head_response.text


@pytest.mark.parametrize(
    "credential_source",
    ["body", "header", "refresh_cookie", "session_cookie"],
)
def test_public_viewer_refresh_credentials_cannot_bypass_method_gate(
    monkeypatch,
    credential_source,
) -> None:
    _strict_auth_env(monkeypatch)
    client = TestClient(bff_main.app)
    headers = {}
    payload = {}
    if credential_source == "body":
        payload["refresh_token"] = "pantheon-dev-browser:viewer"
    elif credential_source == "header":
        headers["X-Refresh-Token"] = "pantheon-dev-browser:viewer"
    elif credential_source == "refresh_cookie":
        client.cookies.set("pantheon_refresh", "pantheon-dev-browser:viewer")
    else:
        client.cookies.set("pantheon_session", "pantheon-dev-browser:viewer")

    response = client.post(
        "/bff/auth/refresh",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "AUTH_PUBLIC_BROWSER_READ_ONLY"


@pytest.mark.parametrize(
    ("auth_mode", "auth_stub"),
    [("strict", ""), ("permissive", ""), ("permissive", "true")],
)
def test_public_viewer_raw_session_cookie_is_never_authenticated(
    monkeypatch,
    auth_mode,
    auth_stub,
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", auth_mode)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", auth_stub)
    client = TestClient(bff_main.app)
    client.cookies.set("pantheon_session", "pantheon-dev-browser:viewer")

    response = client.get("/bff/me")

    assert response.status_code == 403, response.text
    assert (
        response.json()["error"]["details"]["reason"]
        == "AUTH_PUBLIC_BROWSER_COOKIE_FORBIDDEN"
    )


def test_public_viewer_blocked_logout_cannot_poison_shared_session(
    monkeypatch,
) -> None:
    _strict_auth_env(monkeypatch)
    client = TestClient(bff_main.app)
    headers = {"Authorization": PUBLIC_VIEWER_TOKEN}

    before = client.get("/bff/me", headers=headers)
    logout = client.post("/bff/logout", headers=headers, json={})
    after = client.get("/bff/me", headers=headers)
    identity = bff_main._extract_identity(PUBLIC_VIEWER_TOKEN)

    assert before.status_code == 200, before.text
    assert logout.status_code == 403, logout.text
    assert after.status_code == 200, after.text
    assert bff_main._sem_session_state(identity) == {}


def test_public_viewer_blocked_routes_leave_tool_and_sse_state_unchanged(
    monkeypatch,
) -> None:
    _strict_auth_env(monkeypatch)
    client = TestClient(bff_main.app)
    headers = {"Authorization": PUBLIC_VIEWER_TOKEN}
    tool_ids_before = set(bff_main._TOOL_REGISTRY)
    system_events_before = list(bff_main._sse_buffers["system"])

    tool_response = client.post(
        "/bff/tools",
        headers={**headers, "Idempotency-Key": "public-viewer-tool-state"},
        json={"name": "must-not-be-created"},
    )
    sse_response = client.post(
        "/api/v1/internal/sse/publish?event_type=kill_switch.public_injection&channel=system",
        headers=headers,
        json={"forged": True},
    )

    assert tool_response.status_code == 403, tool_response.text
    assert sse_response.status_code == 403, sse_response.text
    assert set(bff_main._TOOL_REGISTRY) == tool_ids_before
    assert list(bff_main._sse_buffers["system"]) == system_events_before


@pytest.mark.parametrize("credential_kind", ["bearer", "cookie"])
def test_signed_jwt_and_cookie_logout_behavior_is_preserved(
    monkeypatch,
    credential_kind,
) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(roles=["operator"])
    client = TestClient(bff_main.app)
    headers = {}
    if credential_kind == "bearer":
        headers["Authorization"] = f"Bearer {token}"
    else:
        client.cookies.set("pantheon_session", token)

    response = client.post("/bff/logout", headers=headers, json={})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["session"]["state"] == "logged_out"


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
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", "600")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-agora",
            "client_secret": CI_PROFILE_SECRET,
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
    assert data["currentUser"]["id"] == "pantheon-dev-ci-agora"
    assert data["session"]["session_kind"] == "bearer"
    assert data["tenant"]["id"] == "tenant-dev"
    assert data["roles"] == ["operator"]
    assert data["capabilities"] == []
    assert data["session"]["mfa_verified"] is False


def test_bff_dev_login_does_not_accept_legacy_shared_oidc_client(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", "dev")
    monkeypatch.delenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "ci-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "legacy-shared-secret-value-000000")

    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={
            "grant_type": "client_credentials",
            "client_id": "ci-client",
            "client_secret": "legacy-shared-secret-value-000000",
        },
    )

    assert login.status_code == 403, login.text
    assert login.json()["error"]["details"]["precondition_failed"] == "dev_login"


def _governed_dev_login_profiles() -> dict[str, dict]:
    return {
        "ci-agora": {
            "secret": CI_PROFILE_SECRET,
            "subject": "pantheon-dev-ci-agora",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "capabilities": [],
            "mfa_verified": False,
        },
        "kernel-operator": {
            "secret": KERNEL_PROFILE_SECRET,
            "subject": "pantheon-dev-kernel-operator",
            "roles": ["admin", "operator"],
            "tenant_id": "tenant-kernel",
            "allowed_tenants": ["tenant-kernel"],
            "capabilities": ["assistant.kernel.debug", "assistant.kernel.repair"],
            "mfa_verified": True,
        },
        "operator-a": {
            "secret": OPERATOR_A_SECRET,
            "subject": "pantheon-dev-operator-a",
            "roles": ["operator"],
            "tenant_id": "tenant-operator-a",
            "allowed_tenants": ["tenant-operator-a"],
            "capabilities": ["approval.read"],
            "mfa_verified": True,
        },
        "operator-b": {
            "secret": OPERATOR_B_SECRET,
            "subject": "pantheon-dev-operator-b",
            "roles": ["risk_owner"],
            "tenant_id": "tenant-operator-b",
            "allowed_tenants": ["tenant-operator-b"],
            "capabilities": ["risk.alert.read"],
            "mfa_verified": False,
        },
    }


def test_bff_profiled_dev_login_issues_distinct_least_role_and_kernel_identities(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_ID", "legacy-shared-client")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_CLIENT_SECRET", "legacy-shared-secret")
    client = TestClient(bff_main.app)

    ci_login = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )
    kernel_login = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "kernel-operator", "client_secret": KERNEL_PROFILE_SECRET},
    )
    legacy_login = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "legacy-shared-client", "client_secret": "legacy-shared-secret"},
    )

    assert ci_login.status_code == 200, ci_login.text
    assert kernel_login.status_code == 200, kernel_login.text
    assert legacy_login.status_code == 401, legacy_login.text
    assert ci_login.json()["meta"]["identity_profile"] == "governed"
    ci_me = client.get(
        "/bff/me",
        headers={"Authorization": f"Bearer {ci_login.json()['access_token']}"},
    )
    kernel_me = client.get(
        "/bff/me",
        headers={"Authorization": f"Bearer {kernel_login.json()['access_token']}"},
    )
    assert ci_me.status_code == 200, ci_me.text
    assert kernel_me.status_code == 200, kernel_me.text
    ci_data = ci_me.json()["data"]
    kernel_data = kernel_me.json()["data"]
    assert ci_data["currentUser"]["id"] == "pantheon-dev-ci-agora"
    assert ci_data["roles"] == ["operator"]
    assert ci_data["tenant"]["id"] == "tenant-dev"
    assert ci_data["tenant"]["allowed_ids"] == ["tenant-dev"]
    assert ci_data["capabilities"] == []
    assert ci_data["session"]["mfa_verified"] is False
    assert kernel_data["currentUser"]["id"] == "pantheon-dev-kernel-operator"
    assert set(kernel_data["roles"]) == {"admin", "operator"}
    assert kernel_data["tenant"]["id"] == "tenant-kernel"
    assert kernel_data["session"]["mfa_verified"] is True
    assert {"assistant.kernel.debug", "assistant.kernel.repair"}.issubset(
        kernel_data["capabilities"]
    )


def test_bff_profiled_dev_login_proves_distinct_operator_a_b_contracts(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-dev")
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )
    client = TestClient(bff_main.app)
    results = {}
    for client_id, secret in (("operator-a", OPERATOR_A_SECRET), ("operator-b", OPERATOR_B_SECRET)):
        login = client.post(
            "/bff/auth/dev-login",
            json={"client_id": client_id, "client_secret": secret},
        )
        assert login.status_code == 200, login.text
        me = client.get(
            "/bff/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.status_code == 200, me.text
        results[client_id] = me.json()["data"]

    assert results["operator-a"]["currentUser"]["id"] == "pantheon-dev-operator-a"
    assert results["operator-a"]["roles"] == ["operator"]
    assert results["operator-a"]["tenant"]["allowed_ids"] == ["tenant-operator-a"]
    assert results["operator-a"]["capabilities"] == ["approval.read"]
    assert results["operator-a"]["session"]["mfa_verified"] is True
    assert results["operator-b"]["currentUser"]["id"] == "pantheon-dev-operator-b"
    assert results["operator-b"]["roles"] == ["risk_owner"]
    assert results["operator-b"]["tenant"]["allowed_ids"] == ["tenant-operator-b"]
    assert results["operator-b"]["capabilities"] == ["risk.alert.read"]
    assert results["operator-b"]["session"]["mfa_verified"] is False


def test_risk_owner_is_readable_but_not_a_generic_operator_writer() -> None:
    identity = bff_main.OperatorIdentity(
        operator_id="risk-owner-a",
        roles=["risk_owner"],
        mfa_verified=True,
        claims={"sub": "risk-owner-a", "roles": ["risk_owner"]},
        token_kind="jwt",
    )

    bff_main._require_read_role(identity)
    with pytest.raises(Exception) as exc_info:
        bff_main._require_operator_role(identity)
    assert getattr(exc_info.value, "status_code", None) == 403


def test_bff_profiled_dev_login_rejects_duplicate_actor_subjects(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    profiles = _governed_dev_login_profiles()
    profiles["kernel-operator"]["subject"] = profiles["ci-agora"]["subject"]
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON", json.dumps(profiles))

    response = TestClient(bff_main.app).post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"]["reason"] == "AUTH_DEV_LOGIN_PROFILE_CONFIGURATION"


def test_bff_profiled_dev_login_rejects_duplicate_secrets(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    profiles = _governed_dev_login_profiles()
    profiles["operator-b"]["secret"] = profiles["operator-a"]["secret"]
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON", json.dumps(profiles))

    response = TestClient(bff_main.app).post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"]["reason"] == "AUTH_DEV_LOGIN_PROFILE_CONFIGURATION"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda profiles: profiles["operator-b"].update({"unexpected": True}),
        lambda profiles: profiles["operator-b"].pop("capabilities"),
        lambda profiles: profiles["operator-b"].update({"secret": "too-short"}),
        lambda profiles: profiles["operator-b"].update({"tenant_id": " tenant-operator-b"}),
    ],
)
def test_bff_profiled_dev_login_rejects_any_invalid_extra_profile(monkeypatch, mutation) -> None:
    _strict_auth_env(monkeypatch)
    profiles = _governed_dev_login_profiles()
    mutation(profiles)
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON", json.dumps(profiles))

    response = TestClient(bff_main.app).post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"]["reason"] == "AUTH_DEV_LOGIN_PROFILE_CONFIGURATION"


def test_bff_profiled_dev_login_rejects_cross_tenant_use(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )
    client = TestClient(bff_main.app)
    login = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    response = client.get(
        "/bff/me",
        headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Tenant-Id": "tenant-kernel",
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["precondition_failed"] == "tenant_scope"


@pytest.mark.parametrize(
    ("environment", "deployment_stage"),
    [
        ("", ""),
        ("staging", "staging"),
        ("staging-live", "staging-live"),
        ("qa", "qa"),
        ("unknown", "unknown"),
        ("dev", "qa"),
        ("production", "production"),
    ],
)
def test_bff_profiled_dev_login_is_disabled_outside_explicit_allowlist(
    monkeypatch, environment, deployment_stage
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", environment)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", deployment_stage)
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )

    response = TestClient(bff_main.app).post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["precondition_failed"] == "dev_login"


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
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )

    client = TestClient(bff_main.app)
    response = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": "wrong-secret"},
    )

    assert response.status_code == 401, response.text
    error = response.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["details"]["reason"] == "AUTH_DEV_LOGIN_CLIENT_CREDENTIALS"


def test_bff_dev_login_disabled_for_staging_live(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", "staging-live")
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )

    client = TestClient(bff_main.app)
    response = client.post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "PRECONDITION_FAILED"
    assert error["details"]["precondition_failed"] == "dev_login"


@pytest.mark.parametrize("environment", ["dev", "local", "test", "testing"])
def test_bff_profiled_dev_login_allows_only_deliberate_dev_test_values(
    monkeypatch, environment
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_ENV", environment)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_STAGE", environment)
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )

    response = TestClient(bff_main.app).post(
        "/bff/auth/dev-login",
        json={"client_id": "ci-agora", "client_secret": CI_PROFILE_SECRET},
    )

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("client_id", "client_secret", "grant_type"),
    [
        (" ci-agora", CI_PROFILE_SECRET, "client_credentials"),
        ("ci-agora ", CI_PROFILE_SECRET, "client_credentials"),
        ("ci-agora", f"{CI_PROFILE_SECRET} ", "client_credentials"),
        ("ci-agora", f"\t{CI_PROFILE_SECRET}", "client_credentials"),
        ("ci-agora", CI_PROFILE_SECRET, " client_credentials"),
    ],
)
def test_bff_dev_login_rejects_raw_credential_whitespace_without_stripping(
    monkeypatch, client_id, client_secret, grant_type
) -> None:
    _strict_auth_env(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON",
        json.dumps(_governed_dev_login_profiles()),
    )

    response = TestClient(bff_main.app).post(
        "/bff/auth/dev-login",
        json={
            "grant_type": grant_type,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )

    assert response.status_code in {400, 401}, response.text


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
