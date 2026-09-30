"""Mounted-route coverage for the caller-scoped servant profile read."""
from __future__ import annotations

from services.control_plane.bff.tests.test_agora_router import (
    _OPERATOR_AUTH,
    _client,
    _create_test_agora_store,
    _install_agora_store,
    _runtime,
)


def _provision(client):
    return client.post(
        "/bff/agora/servant/ensure",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": "servant-status-test",
            "X-Request-Id": "req-servant-status-test",
        },
    )


def test_servant_status_returns_profile_to_viewer(monkeypatch):
    store = _create_test_agora_store(allow_fallback=True)
    _install_agora_store(monkeypatch, store)
    monkeypatch.setattr(_runtime, "_ensure_agora_servant_openclaw_agent", lambda persona: {})
    client = _client(monkeypatch)
    provisioned = _provision(client)
    assert provisioned.status_code == 200, provisioned.text

    response = client.get(
        "/bff/agora/servant",
        headers={"Authorization": "Bearer agora-test-user:viewer"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["persona_id"] == provisioned.json()["data"]["persona_id"]
    assert body["meta"]["capability"] == "agora.servant.v1"
    assert body["meta"]["audience"] == "tenant:pantheon-dev:user:agora-test-user"


def test_servant_status_returns_not_found_when_unprovisioned(monkeypatch):
    _install_agora_store(monkeypatch, _create_test_agora_store(allow_fallback=False))
    response = _client(monkeypatch).get(
        "/bff/agora/servant",
        headers={"Authorization": "Bearer agora-test-user:viewer"},
    )
    assert response.status_code == 404, response.text


def test_servant_status_does_not_expose_another_users_profile(monkeypatch):
    store = _create_test_agora_store(allow_fallback=True)
    _install_agora_store(monkeypatch, store)
    monkeypatch.setattr(_runtime, "_ensure_agora_servant_openclaw_agent", lambda persona: {})
    client = _client(monkeypatch)
    assert _provision(client).status_code == 200

    response = client.get(
        "/bff/agora/servant",
        headers={"Authorization": "Bearer foreign-user:viewer"},
    )
    assert response.status_code == 404, response.text
