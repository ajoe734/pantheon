from __future__ import annotations

import os
import sys
from unittest.mock import patch
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as router_main


class _FakePersonaResponse:
    def __init__(self, *, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", f"{router_main.PERSONA_URL}/invoke")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"Persona returned {self.status_code}",
                request=request,
                response=response,
            )

    def json(self) -> dict:
        return self._body


class _AsyncClientStub:
    def __init__(
        self,
        *,
        response: _FakePersonaResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or _FakePersonaResponse()
        self.error = error
        self.posts: list[dict] = []

    async def __aenter__(self) -> _AsyncClientStub:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, json: dict, timeout: int) -> _FakePersonaResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def test_health_reports_router_policy_constants() -> None:
    client = TestClient(router_main.app)

    response = client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ok",
        "service": "router",
        "persona_url": router_main.PERSONA_URL,
        "session_ttl_seconds": router_main.SESSION_TTL_SECONDS,
    }


def test_route_generates_session_id_and_uses_persona_response() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        response=_FakePersonaResponse(
            body={
                "response": "Research path accepted",
                "intent": "research.vectorbt",
                "skill": "research-summary",
            }
        )
    )

    with patch.object(router_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/route",
            json={
                "channel": "web",
                "user_id": "user-123",
                "message": "please run a qlib research backtest",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    UUID(payload["session_id"])
    assert payload["agent_id"] == "persona-default"
    assert payload["response"] == "Research path accepted"
    assert payload["intent"] == "research.vectorbt"
    assert payload["skill"] == "research-summary"
    assert payload["permission"] == "allow"

    assert len(async_client.posts) == 1
    outbound = async_client.posts[0]
    assert outbound["url"] == f"{router_main.PERSONA_URL}/invoke"
    assert outbound["timeout"] == 30
    assert outbound["json"] == {
        "session_id": payload["session_id"],
        "user_id": "user-123",
        "channel": "web",
        "message": "please run a qlib research backtest",
    }


def test_route_uses_local_classifier_and_default_response_when_persona_body_is_sparse() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(response=_FakePersonaResponse(body={}))

    with patch.object(router_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/route",
            json={
                "channel": "web",
                "user_id": "user-234",
                "message": "what is running right now?",
                "session_id": "sess-existing",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "session_id": "sess-existing",
        "agent_id": "persona-default",
        "response": "[no response]",
        "intent": "status",
        "skill": None,
        "permission": "allow",
    }
    assert async_client.posts[0]["json"]["session_id"] == "sess-existing"


def test_route_denies_non_operator_execution_signal_before_persona_invoke() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub()

    with patch.object(router_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/route",
            json={
                "channel": "web",
                "user_id": "user-345",
                "message": "buy this signal now",
            },
        )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Tool permission denied"
    assert async_client.posts == []


def test_route_allows_console_governance_with_approval() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        response=_FakePersonaResponse(
            body={
                "response": "Approval request queued",
                "skill": "governance-review",
            }
        )
    )

    with patch.object(router_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/route",
            json={
                "channel": "console",
                "user_id": "operator-1",
                "message": "approve rollback for the live artifact",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["intent"] == "governance.approve"
    assert payload["permission"] == "allow_with_approval"
    assert payload["response"] == "Approval request queued"
    assert payload["skill"] == "governance-review"
    assert len(async_client.posts) == 1


def test_route_returns_503_when_persona_returns_http_error() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(response=_FakePersonaResponse(status_code=502))

    with patch.object(router_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/route",
            json={
                "channel": "web",
                "user_id": "user-456",
                "message": "show me status",
            },
        )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "Persona Agent error"


def test_route_returns_503_when_persona_is_unreachable() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(error=httpx.ConnectError("dial failed"))

    with patch.object(router_main.httpx, "AsyncClient", return_value=async_client):
        response = client.post(
            "/route",
            json={
                "channel": "web",
                "user_id": "user-567",
                "message": "show me status",
            },
        )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "Persona Agent unavailable"
