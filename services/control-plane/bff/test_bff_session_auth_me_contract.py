"""Contract tests for BFF-LUV-GAP-009 `/bff/me` current-user DTO."""
from __future__ import annotations

import os
import sys
import time

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from services.runtime_auth_inbound import encode_jwt_hs256


OPERATOR_TOKEN = "Bearer op-2:operator,reviewer:mfa"
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
    error = response.json()["detail"]["error"]
    assert error["code"] == "INSUFFICIENT_ROLE"
    assert error["details"]["precondition_failed"] == "tenant_scope"
    assert error["details"]["tenantId"] == "tenant-gamma"
    assert error["details"]["allowedTenantIds"] == ["tenant-alpha"]


def test_bff_me_strict_auth_requires_bearer_token(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)

    client = TestClient(bff_main.app)
    response = client.get("/bff/me")

    assert response.status_code == 401, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "INVALID_TOKEN"
    assert error["details"]["reason"]


def test_bff_me_strict_auth_rejects_viewer_without_read_role(monkeypatch) -> None:
    _strict_auth_env(monkeypatch)
    token = _jwt_token(roles=["viewer"], extra={"tenant_id": "tenant-alpha"})

    client = TestClient(bff_main.app)
    response = client.get("/bff/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "INSUFFICIENT_ROLE"
    assert error["details"]["precondition_failed"] == "role_check"
