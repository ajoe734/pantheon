from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main


def test_bff_me_returns_session_bootstrap_payload(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-primary")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-primary,tenant-alt")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
    monkeypatch.setenv("PANTHEON_BFF_FEATURE_FLAGS", "plansLive=true,alpha=false")

    response = TestClient(bff_main.app).get(
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

    response = TestClient(bff_main.app).get(
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
