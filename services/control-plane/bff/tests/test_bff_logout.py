from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from session_lifecycle_store import SessionLifecycleStore
from services.runtime_auth_inbound import encode_jwt_hs256


JWT_SECRET = "test-bff-b1-006-logout"
JWT_ISSUER = "pantheon-bff-b1-006"
JWT_AUDIENCE = "bff-operators"


def _jwt_token(*, sub: str = "op-cookie-logout", extra: dict | None = None) -> str:
    payload = {
        "sub": sub,
        "roles": ["operator"],
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    if extra:
        payload.update(extra)
    return encode_jwt_hs256(payload, secret=JWT_SECRET)


@pytest.fixture(autouse=True)
def isolated_session_lifecycle_store(tmp_path):
    original_store = bff_main.session_lifecycle_store
    bff_main.session_lifecycle_store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    try:
        yield
    finally:
        bff_main.session_lifecycle_store = original_store


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
    return TestClient(bff_main.app)


def _strict_cookie_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    return TestClient(bff_main.app)


def test_post_bff_logout_returns_200_with_logout_operation(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/bff/logout",
        headers={"Authorization": "Bearer op-logout-1:operator"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert data["operation"]["type"] == "logout"
    assert data["session"]["state"] == "logged_out"
    assert data["session"]["authenticated"] is False


def test_post_bff_logout_invalidates_session_for_subsequent_get_me(monkeypatch) -> None:
    client = _client(monkeypatch)
    auth = "Bearer op-logout-persist:operator"

    logout_resp = client.post("/bff/logout", headers={"Authorization": auth})
    assert logout_resp.status_code == 200, logout_resp.text

    me_resp = client.get("/bff/me", headers={"Authorization": auth})
    assert me_resp.status_code == 401
    detail = me_resp.json()["detail"]
    assert detail["error"]["code"] == "AUTH_REQUIRED"
    assert detail["error"]["details"]["reason"] == "SESSION_LOGGED_OUT"


def test_post_bff_logout_clears_cookie_and_followup_me_returns_401(monkeypatch) -> None:
    client = _strict_cookie_client(monkeypatch)
    token = _jwt_token(extra={"sid": "session-cookie-logout-b1-006"})
    client.cookies.set("pantheon_session", token)

    logout_resp = client.post("/bff/logout")
    assert logout_resp.status_code == 200, logout_resp.text
    set_cookie = logout_resp.headers.get("set-cookie", "")
    assert "pantheon_session=" in set_cookie
    assert "max-age=0" in set_cookie.lower()

    me_resp = client.get("/bff/me")
    assert me_resp.status_code == 401


def test_post_bff_logout_anonymous_returns_401(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post("/bff/logout")

    assert response.status_code == 401


def test_post_bff_logout_idempotency_replay(monkeypatch) -> None:
    client = _client(monkeypatch)
    auth = "Bearer op-logout-idem:operator"
    headers = {"Authorization": auth, "Idempotency-Key": "logout-key-42"}

    first = client.post("/bff/logout", headers=headers)
    assert first.status_code == 200, first.text

    second = client.post("/bff/logout", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["meta"]["idempotency"]["replayed"] is True


def test_post_bff_logout_idempotency_conflict_returns_409(monkeypatch) -> None:
    client = _client(monkeypatch)
    auth = "Bearer op-logout-conflict:operator"
    key = "logout-conflict-key"

    first = client.post(
        "/bff/logout",
        json={"reason": "first"},
        headers={"Authorization": auth, "Idempotency-Key": key},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/bff/logout",
        json={"reason": "different"},
        headers={"Authorization": auth, "Idempotency-Key": key},
    )
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_post_bff_logout_sets_logged_out_at(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/bff/logout",
        headers={"Authorization": "Bearer op-logout-ts:operator"},
    )

    assert response.status_code == 200, response.text
    session = response.json()["data"]["session"]
    assert session.get("logged_out_at") is not None
    assert session["fresh"] is False
