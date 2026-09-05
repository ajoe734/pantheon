from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_LOCALE", "en-US")
    return TestClient(bff_main.app)


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
