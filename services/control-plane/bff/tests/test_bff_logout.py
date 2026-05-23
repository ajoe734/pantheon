from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
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


def test_post_bff_logout_session_state_reflected_in_subsequent_get_me(monkeypatch) -> None:
    client = _client(monkeypatch)
    auth = "Bearer op-logout-persist:operator"

    logout_resp = client.post("/bff/logout", headers={"Authorization": auth})
    assert logout_resp.status_code == 200, logout_resp.text

    me_resp = client.get("/bff/me", headers={"Authorization": auth})
    assert me_resp.status_code == 200, me_resp.text
    session = me_resp.json()["data"]["session"]
    assert session["state"] == "logged_out"
    assert session["authenticated"] is False
    assert "logged_out_at" in session


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
