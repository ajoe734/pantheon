from __future__ import annotations

import json
from unittest import mock

import pytest

from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError


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


def test_servant_agent_ensure_uses_authenticated_adapter_contract() -> None:
    captured: dict[str, object] = {}
    persona_id = "agora-servant-da8656efa5da7a5fb8e0"
    persona = {
        "persona_id": persona_id,
        "archetype": "agora_servant",
        "_agent_sync_idempotency_key": "servant-ensure-request-1",
        "metadata": {
            "tenant_id": "pantheon-dev",
            "agora_user_id": "operator-a",
            "persona_class": "agora_servant",
            "execution_authority": "none",
            "interaction_capabilities": ["persona_opinion"],
            "capability_snapshot_id": "cap-servant-fa58dfa6442aa620a3e9",
        },
    }

    class _AgentResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getcode(self) -> int:
            return 201

        def read(self) -> bytes:
            return json.dumps(
                {
                    "status": "ok",
                    "agent": {
                        "status": "created",
                        "agent_id": persona_id,
                        "model_id": f"openclaw/{persona_id}",
                        "workspace_ref": f"/home/node/.openclaw/workspaces/{persona_id}",
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["headers"] = _headers(request)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _AgentResponse()

    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        timeout_seconds=1.25,
        service_token="adapter-secret",
        service_auth_required=True,
    )
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        agent = client.ensure_agora_servant_agent(persona)

    assert agent["agent_id"] == persona_id
    assert captured["headers"]["x-pantheon-service-token"] == "adapter-secret"
    assert captured["headers"]["idempotency-key"]
    assert captured["headers"]["x-request-id"]
    assert captured["body"] == {
        "persona_registry_ref": f"persona:{persona_id}",
        "workspace_ref": f"/home/node/.openclaw/workspaces/{persona_id}",
        "capability_snapshot": {
            "allowed_capabilities": ["persona_opinion"],
            "persona_class": "agora_servant",
        },
    }
    assert captured["timeout"] == 135.0


def test_servant_agent_ensure_fails_before_network_without_service_token() -> None:
    persona = {
        "persona_id": "agora-servant-da8656efa5da7a5fb8e0",
        "archetype": "agora_servant",
        "_agent_sync_idempotency_key": "servant-ensure-request-1",
        "metadata": {
            "tenant_id": "pantheon-dev",
            "agora_user_id": "operator-a",
            "persona_class": "agora_servant",
            "execution_authority": "none",
            "interaction_capabilities": ["persona_opinion"],
            "capability_snapshot_id": "cap-servant-fa58dfa6442aa620a3e9",
        },
    }
    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        service_token="",
        service_auth_required=True,
    )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen") as urlopen:
        with pytest.raises(OpenClawOpsClientError) as captured:
            client.ensure_agora_servant_agent(persona)

    assert captured.value.status_code == 503
    assert captured.value.error_code == "OPENCLAW_ADAPTER_SERVICE_AUTH_MISCONFIGURED"
    urlopen.assert_not_called()


def test_servant_agent_ensure_rejects_unbound_admission_before_network() -> None:
    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        service_token="adapter-secret",
        service_auth_required=True,
    )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen") as urlopen:
        with pytest.raises(OpenClawOpsClientError) as captured:
            client.ensure_agora_servant_agent(
                {
                    "persona_id": "agora-servant-da8656efa5da7a5fb8e0",
                    "_agent_sync_idempotency_key": "servant-ensure-request-1",
                    "metadata": {
                        "tenant_id": "pantheon-dev",
                        "agora_user_id": "operator-a",
                        "persona_class": "agora_servant",
                        "execution_authority": "none",
                        "interaction_capabilities": ["persona_opinion"],
                        "capability_snapshot_id": "cap-servant-wrong",
                    },
                }
            )

    assert captured.value.error_code == "OPENCLAW_AGENT_ADMISSION_INVALID"
    urlopen.assert_not_called()


def test_persona_opinion_ensure_and_invoke_send_exact_agent_admission() -> None:
    captured: list[dict[str, object]] = []
    admission = {
        "persona_id": "alpha",
        "tenant_id": "pantheon-dev",
        "persona_version": "alpha-v2",
        "agent_id": "persona-opinion-0123456789abcdef01234567",
        "workspace_ref": "/home/node/.openclaw/workspaces/persona-opinion-0123456789abcdef01234567",
        "capability_snapshot_id": "snapshot-alpha-v2",
        "allowed_capabilities": ["persona_opinion"],
        "environment_ceiling": "paper",
        "requested_environment": "paper",
        "execution_authority": "none",
        "display_name": "Alpha",
        "mandate": "trend",
        "archetype": "trend_follower",
        "strategy_family": "momentum",
        "traits": {"decision_style": "evidence-first"},
    }

    class _Response:
        def __init__(self, status, payload):
            self.status = status
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def getcode(self):
            return self.status
        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        captured.append({"url": request.full_url, "body": body, "headers": _headers(request), "timeout": timeout})
        if request.full_url.endswith("/agents/persona-opinion/ensure"):
            return _Response(201, {
                "status": "ok",
                "execution_authority": "none",
                "agent": {"agent_id": admission["agent_id"]},
            })
        return _Response(200, {"status": "ok", "data": {"status": "completed"}})

    client = OpenClawOpsClient(
        base_url="http://openclaw-gateway-adapter:8104",
        service_token="adapter-secret",
        service_auth_required=True,
    )
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        client.ensure_persona_opinion_agent(
            admission,
            persona_profile={"name": "Alpha", "mandate": "trend"},
        )
        client.invoke_assistant_provider(
            provider="openclaw",
            mode="user",
            prompt="opinion",
            context_pack={"participant": {"persona_id": "alpha"}},
            operator_id="operator-1",
            agent_id=admission["agent_id"],
            persona_admission=admission,
        )

    ensure_body = captured[0]["body"]
    invoke_body = captured[1]["body"]
    assert ensure_body["agent_id"] == admission["agent_id"]
    assert ensure_body["allowed_capabilities"] == ["persona_opinion"]
    assert invoke_body["agent_id"] == admission["agent_id"]
    assert invoke_body["persona_admission"] == admission
    assert captured[0]["headers"]["x-pantheon-service-token"] == "adapter-secret"
