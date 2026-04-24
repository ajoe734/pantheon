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
    def __init__(self, routes: dict[str, _FakePersonaResponse | Exception]) -> None:
        self.routes = routes
        self.posts: list[dict] = []

    async def __aenter__(self) -> _AsyncClientStub:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, json: dict, timeout: int) -> _FakePersonaResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        result = self.routes[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_health_reports_router_policy_constants() -> None:
    client = TestClient(router_main.app)

    response = client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ok",
        "service": "router",
        "persona_url": router_main.PERSONA_URL,
        "session_ttl_seconds": router_main.SESSION_TTL_SECONDS,
        "classification_owner": "persona",
        "fallback_classifier_mode": "degraded_only",
    }


def test_route_uses_persona_classify_before_invoke() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        routes={
            f"{router_main.PERSONA_URL}/classify": _FakePersonaResponse(
                body={"intent": "research", "skill": "research-summary"}
            ),
            f"{router_main.PERSONA_URL}/invoke": _FakePersonaResponse(
                body={
                    "response": "Research path accepted",
                    "intent": "research.vectorbt",
                    "skill": "research-summary",
                    "session_status": "active",
                    "runtime": {"mode": "openclaw"},
                    "agent_id": "main",
                }
            ),
        }
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
    assert payload == {
        "session_id": payload["session_id"],
        "agent_id": "main",
        "response": "Research path accepted",
        "intent": "research.vectorbt",
        "skill": "research-summary",
        "permission": "allow",
        "intent_source": "persona",
        "routing_mode": "openclaw",
        "session_status": "active",
    }

    assert [entry["url"] for entry in async_client.posts] == [
        f"{router_main.PERSONA_URL}/classify",
        f"{router_main.PERSONA_URL}/invoke",
    ]
    assert async_client.posts[1]["json"]["intent_hint"] == "research"


def test_route_uses_router_fallback_classifier_only_in_degraded_mode() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        routes={
            f"{router_main.PERSONA_URL}/classify": httpx.ConnectError("classify unavailable"),
            f"{router_main.PERSONA_URL}/invoke": _FakePersonaResponse(
                body={
                    "response": "Fallback response",
                    "session_status": "degraded",
                    "runtime": {"mode": "degraded_surrogate"},
                }
            ),
        }
    )

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
        "response": "Fallback response",
        "intent": "status",
        "skill": "status-summary",
        "permission": "allow",
        "intent_source": "router.degraded_fallback",
        "routing_mode": "degraded_surrogate",
        "session_status": "degraded",
    }
    assert async_client.posts[1]["json"]["intent_hint"] == "status"


def test_route_denies_non_operator_execution_signal_before_persona_invoke() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        routes={
            f"{router_main.PERSONA_URL}/classify": _FakePersonaResponse(
                body={"intent": "execution.signal", "skill": "execution-signal"}
            ),
            f"{router_main.PERSONA_URL}/invoke": _FakePersonaResponse(),
        }
    )

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
    assert [entry["url"] for entry in async_client.posts] == [f"{router_main.PERSONA_URL}/classify"]


def test_route_allows_console_governance_with_approval() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        routes={
            f"{router_main.PERSONA_URL}/classify": _FakePersonaResponse(
                body={"intent": "governance.approve", "skill": "governance-review"}
            ),
            f"{router_main.PERSONA_URL}/invoke": _FakePersonaResponse(
                body={
                    "response": "Approval request queued",
                    "skill": "governance-review",
                    "session_status": "active",
                    "runtime": {"mode": "openclaw"},
                }
            ),
        }
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


def test_route_returns_503_when_persona_returns_http_error() -> None:
    client = TestClient(router_main.app)
    async_client = _AsyncClientStub(
        routes={
            f"{router_main.PERSONA_URL}/classify": _FakePersonaResponse(
                body={"intent": "status", "skill": "status-summary"}
            ),
            f"{router_main.PERSONA_URL}/invoke": _FakePersonaResponse(status_code=502),
        }
    )

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
    async_client = _AsyncClientStub(
        routes={
            f"{router_main.PERSONA_URL}/classify": _FakePersonaResponse(
                body={"intent": "status", "skill": "status-summary"}
            ),
            f"{router_main.PERSONA_URL}/invoke": httpx.ConnectError("dial failed"),
        }
    )

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
