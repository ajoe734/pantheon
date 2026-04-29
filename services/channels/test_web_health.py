from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "channels" / "web"))

import main as web_main


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body


class _AsyncClientStub:
    def __init__(self, *, gets: dict[str, _FakeResponse | Exception] | None = None) -> None:
        self.gets = gets or {}
        self.get_calls: list[dict] = []

    async def __aenter__(self) -> _AsyncClientStub:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str, timeout: float) -> _FakeResponse:
        self.get_calls.append({"url": url, "timeout": timeout})
        result = self.gets[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_web_channel_exposes_standard_health_endpoints_and_legacy_alias() -> None:
    client = TestClient(web_main.app)
    async_client = _AsyncClientStub(
        gets={
            f"{web_main.ROUTER_URL}/readyz": _FakeResponse(
                body={"status": "ok", "service": "router"}
            )
        }
    )

    with patch.object(web_main.httpx, "AsyncClient", return_value=async_client):
        for path in ("/healthz", "/health", "/readyz"):
            response = client.get(path)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["service"] == "web-channel"
            assert payload["status"] == "ok"
            assert payload["live"] is True
            assert payload["ready"] is True
            assert payload["dependencies"]["router"]["status"] == "ok"
            assert payload["dependencies"]["router"]["probe"] == "/readyz"
            assert payload["metrics"]["service_up"] == 1

    livez = client.get("/livez")
    assert livez.status_code == 200, livez.text
    assert livez.json()["dependencies"] == {}

    metrics = client.get("/metrics")
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["metrics"]["service_up"] == 1


def test_readyz_reports_router_degraded_without_crashing() -> None:
    client = TestClient(web_main.app)
    async_client = _AsyncClientStub(
        gets={
            f"{web_main.ROUTER_URL}/readyz": _FakeResponse(
                status_code=503,
                body={"status": "degraded", "service": "router"},
            )
        }
    )

    with patch.object(web_main.httpx, "AsyncClient", return_value=async_client):
        response = client.get("/readyz")

    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["ready"] is False
    assert payload["dependencies"]["router"]["status"] == "degraded"
    assert payload["dependencies"]["router"]["router_status"] == "degraded"


def test_readyz_reports_router_unavailable_without_crashing() -> None:
    client = TestClient(web_main.app)
    async_client = _AsyncClientStub(
        gets={
            f"{web_main.ROUTER_URL}/readyz": RuntimeError("connection refused"),
        }
    )

    with patch.object(web_main.httpx, "AsyncClient", return_value=async_client):
        response = client.get("/readyz")

    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["ready"] is False
    assert payload["dependencies"]["router"]["status"] == "unavailable"
    assert "connection refused" in payload["dependencies"]["router"]["reason"]


def test_readyz_falls_back_to_legacy_router_health_when_readyz_is_missing() -> None:
    client = TestClient(web_main.app)
    async_client = _AsyncClientStub(
        gets={
            f"{web_main.ROUTER_URL}/readyz": _FakeResponse(status_code=404),
            f"{web_main.ROUTER_URL}/health": _FakeResponse(
                body={"status": "ok", "service": "router"}
            ),
        }
    )

    with patch.object(web_main.httpx, "AsyncClient", return_value=async_client):
        response = client.get("/readyz")

    assert response.status_code == 200, response.text
    assert response.json()["dependencies"]["router"]["probe"] == "/health"


def test_web_channel_is_not_in_default_compose_services() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose.get("services", {})

    serialized_services = {
        service_name: yaml.safe_dump(service_config)
        for service_name, service_config in services.items()
    }

    assert "web-channel" not in services
    assert all("services/channels/web" not in payload for payload in serialized_services.values())


def test_bot_channels_are_scoped_out_of_http_health_contract() -> None:
    scope = (ROOT / "services" / "channels" / "README.md").read_text(encoding="utf-8")

    assert "Telegram" in scope
    assert "Discord" in scope
    assert "do not expose an HTTP process" in scope
