from __future__ import annotations

import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as web_main


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _AsyncClientStub:
    def __init__(self, *, posts: dict[str, _FakeResponse] | None = None, gets: dict[str, _FakeResponse] | None = None) -> None:
        self.posts = posts or {}
        self.gets = gets or {}
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []

    async def __aenter__(self) -> _AsyncClientStub:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, json: dict, timeout: int) -> _FakeResponse:
        self.post_calls.append({"url": url, "json": json, "timeout": timeout})
        return self.posts[url]

    async def get(self, url: str, timeout: int) -> _FakeResponse:
        self.get_calls.append({"url": url, "timeout": timeout})
        return self.gets[url]


def test_health_returns_ok() -> None:
    client = TestClient(web_main.app)

    response = client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "service": "web-channel"}


def test_chat_forwards_session_id_and_returns_router_metadata() -> None:
    client = TestClient(web_main.app)
    async_client = _AsyncClientStub(
        posts={
            f"{web_main.ROUTER_URL}/route": _FakeResponse(
                body={
                    "session_id": "sess-existing",
                    "response": "hello from router",
                    "intent": "status",
                    "skill": "status-summary",
                    "permission": "allow",
                    "intent_source": "persona",
                    "routing_mode": "openclaw",
                    "session_status": "active",
                }
            )
        }
    )

    with patch.object(web_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/chat",
            json={"user_id": "user-1", "message": "show status", "session_id": "sess-existing"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "session_id": "sess-existing",
        "response": "hello from router",
        "intent": "status",
        "skill": "status-summary",
        "permission": "allow",
        "intent_source": "persona",
        "routing_mode": "openclaw",
        "session_status": "active",
    }
    assert async_client.post_calls[0]["json"]["session_id"] == "sess-existing"


def test_stream_emits_truthful_router_health_events() -> None:
    client = TestClient(web_main.app)
    async_client = _AsyncClientStub(
        gets={
            f"{web_main.ROUTER_URL}/health": _FakeResponse(
                body={"status": "ok", "service": "router", "classification_owner": "persona"}
            )
        }
    )

    with patch.object(web_main.httpx, "AsyncClient", return_value=async_client):
        response = client.get("/stream/sess-001")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: session" in response.text
    assert "event: router_health" in response.text
    assert '"classification_owner":"persona"' in response.text
    assert '"streaming":"disabled"' in response.text
    assert "[stream not yet implemented" not in response.text
