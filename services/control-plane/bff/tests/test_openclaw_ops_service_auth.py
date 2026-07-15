from __future__ import annotations

import json
from unittest import mock

import pytest

from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError


class _JsonResponse:
    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getcode(self) -> int:
        return 200

    def read(self) -> bytes:
        return json.dumps({"status": "ready", "ready": True}).encode("utf-8")


def _headers(request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def test_assistant_request_attaches_bff_service_token() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = _headers(request)
        captured["timeout"] = timeout
        return _JsonResponse()

    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        timeout_seconds=1.25,
        service_token="human-provisioned-token",
        service_auth_required=True,
    )
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = client.get_assistant_readiness("openclaw")

    assert result["ready"] is True
    assert captured["headers"]["x-pantheon-service-token"] == "human-provisioned-token"
    assert captured["timeout"] == 1.25


def test_assistant_request_reads_service_auth_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", "env-service-token")
    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED", "true")
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        del timeout
        captured["headers"] = _headers(request)
        return _JsonResponse()

    client = OpenClawOpsClient(base_url="http://openclaw-gateway-adapter:8104")
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        client.list_assistant_providers()

    assert captured["headers"]["x-pantheon-service-token"] == "env-service-token"


def test_assistant_request_fails_closed_before_network_when_token_missing() -> None:
    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        service_token="",
        service_auth_required=True,
    )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen") as urlopen:
        with pytest.raises(OpenClawOpsClientError) as captured:
            client.get_assistant_readiness("openclaw")

    assert captured.value.status_code == 503
    assert captured.value.error_code == "OPENCLAW_ADAPTER_SERVICE_AUTH_MISCONFIGURED"
    assert "token" in captured.value.message.lower()
    urlopen.assert_not_called()


def test_assistant_stream_attaches_bff_service_token() -> None:
    captured: dict[str, object] = {}

    class _StreamResponse:
        def __iter__(self):
            return iter([b'data: {"type":"done","text":"ok"}\n', b"data: [DONE]\n"])

        def close(self) -> None:
            return None

    def fake_urlopen(request, timeout):
        captured["headers"] = _headers(request)
        captured["timeout"] = timeout
        return _StreamResponse()

    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        timeout_seconds=2.5,
        service_token="stream-token",
        service_auth_required=True,
    )
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        events = list(
            client.stream_assistant_provider(
                mode="user",
                prompt="hello",
                context_pack={},
                operator_id="operator-1",
            )
        )

    assert events == [{"type": "done", "text": "ok"}]
    assert captured["headers"]["x-pantheon-service-token"] == "stream-token"
    assert captured["timeout"] == 2.5


def test_non_assistant_request_does_not_receive_or_require_assistant_token() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = _headers(request)
        return _JsonResponse()

    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        service_token="",
        service_auth_required=True,
    )
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        client.get_capabilities()

    assert "x-pantheon-service-token" not in captured["headers"]
