from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer asst-bff-002:operator"}


class FakeProviderClient:
    def __init__(self, result: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self.result = result or {
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {"json_events": [{"final": "Provider grounded management answer."}]},
                "redaction": {"provider_invocation": {"redacted_fields": 0}},
            },
        }
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeHttpResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seeded_client(tmp_path: Path, monkeypatch) -> TestClient:
    read_surface_path = tmp_path / "read_surfaces.json"
    _write_json(
        read_surface_path,
        {
            "capital_pools": {
                "pool-alpha": {
                    "pool_id": "pool-alpha",
                    "name": "Alpha Pool",
                    "tenant_id": "tenant-alpha",
                    "status": "active",
                },
                "pool-beta": {
                    "pool_id": "pool-beta",
                    "name": "Beta Pool",
                    "tenant_id": "tenant-beta",
                    "status": "active",
                },
            },
            "runtime_bindings": {
                "rb-alpha": {
                    "binding_id": "rb-alpha",
                    "runtime_id": "rt-alpha",
                    "tenant_id": "tenant-alpha",
                    "capital_pool_id": "pool-alpha",
                    "deployment_stage": "paper",
                    "status": "running",
                },
                "rb-beta": {
                    "binding_id": "rb-beta",
                    "runtime_id": "rt-beta",
                    "tenant_id": "tenant-beta",
                    "capital_pool_id": "pool-beta",
                    "deployment_stage": "paper",
                    "status": "running",
                },
            },
            "telemetry_summaries": {
                "rt-alpha": {
                    "runtime_id": "rt-alpha",
                    "pnl": 2.5,
                    "fill_rate": 0.8,
                    "total_trades": 4,
                    "metrics": {"pnl": 2.5, "fill_rate": 0.8, "total_trades": 4},
                    "collected_at": "2026-06-02T00:00:00Z",
                },
                "rt-beta": {
                    "runtime_id": "rt-beta",
                    "pnl": 99.0,
                    "fill_rate": 0.5,
                    "total_trades": 100,
                    "metrics": {"pnl": 99.0, "fill_rate": 0.5, "total_trades": 100},
                    "collected_at": "2026-06-02T00:00:00Z",
                },
            },
            "agora_audit_events": {},
            "agora_sessions": {},
        },
    )
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_AI_AUDIT_PATH", str(tmp_path / "management-ai-audit.jsonl"))
    bff_main.read_store = ReadSurfaceStore(
        str(read_surface_path),
        allow_local_snapshot_fallback=True,
    )
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
    bff_main._MGMT_AI_AUDIT_EVENTS.clear()
    bff_main._sse_buffers["ask"].clear()
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _clear_provider_env(monkeypatch) -> None:
    for env_name in (
        "PANTHEON_ASSISTANT_ENABLED",
        "PANTHEON_ASSISTANT_PROVIDER",
        "PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS",
        "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED",
        "PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED",
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_openclaw_client_invokes_codex_provider_contract(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["headers"] = dict(request.header_items())
        recorded["body"] = json.loads(request.data.decode("utf-8"))
        recorded["timeout"] = timeout
        return FakeHttpResponse(
            {
                "status": "ok",
                "data": {
                    "provider": "codex_cli",
                    "status": "completed",
                    "output": {"json_events": [{"final": "ok"}]},
                },
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).invoke_assistant_provider(
            provider="codex_cli",
            mode="user",
            prompt="hello",
            context_pack={"context_pack_id": "ctx-test"},
            operator_id="operator-1",
            trace_id="trace-1",
            metadata={"tenant_id": "tenant-alpha"},
        )

    assert result["status"] == "ok"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/codex/invoke"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-1"
    assert recorded["headers"]["X-trace-id"] == "trace-1"
    assert recorded["body"] == {
        "mode": "user",
        "prompt": "hello",
        "context_pack": {"context_pack_id": "ctx-test"},
        "metadata": {"tenant_id": "tenant-alpha"},
    }
    assert recorded["timeout"] == 1.5


def test_openclaw_client_uses_assistant_provider_timeout_by_default(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    monkeypatch.setenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0")
    monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS", "75.0")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        return FakeHttpResponse(
            {
                "status": "ok",
                "data": {
                    "provider": "codex_cli",
                    "status": "completed",
                    "output": {"json_events": [{"final": "ok"}]},
                },
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        OpenClawOpsClient().invoke_assistant_provider(
            provider="codex_cli",
            mode="user",
            prompt="hello",
            context_pack={"context_pack_id": "ctx-test"},
            operator_id="operator-1",
        )

    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/codex/invoke"
    )
    assert recorded["timeout"] == 75.0


def test_provider_disabled_returns_deterministic_answer_and_context_pack(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-disabled"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"].startswith("Management summary for question:")
        assert body["data"]["providerStatus"]["status"] == "disabled"
        assert body["data"]["providerStatus"]["reason"] == "feature_disabled"
        assert body["data"]["providerStatus"]["used"] is False
        assert body["data"]["contextPack"]["mode"] == "user"
        assert body["data"]["contextPack"]["backend"]["management_nl"]["data"]["tenant_id"] == "tenant-alpha"
        assert fake.calls == []
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_enabled_invokes_openclaw_with_tenant_scoped_context(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-enabled"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"] == "Provider grounded management answer."
        assert body["data"]["providerStatus"]["status"] == "completed"
        assert body["data"]["providerStatus"]["used"] is True
        assert body["data"]["confidence"] in {"high", "partial", "unavailable"}
        assert body["data"]["sources"] == ["portfolio"]
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["provider"] == "codex_cli"
        assert call["mode"] == "user"
        assert call["operator_id"] == "asst-bff-002"
        assert call["metadata"]["tenant_id"] == "tenant-alpha"
        management_context = call["context_pack"]["backend"]["management_nl"]["data"]
        assert management_context["tenant_id"] == "tenant-alpha"
        assert management_context["summary_context"]["portfolio"]["total_pnl"] == 2.5
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_enabled_extracts_codex_item_completed_text(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        result={
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {
                    "json_events": [
                        {"type": "thread.started", "thread_id": "thread-test"},
                        {"type": "turn.started"},
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "item_0",
                                "type": "agent_message",
                                "text": "Codex transcript answer.",
                            },
                        },
                        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
                    ],
                },
            },
        }
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-codex-item"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"] == "Codex transcript answer."
        assert body["data"]["providerStatus"]["status"] == "completed"
        assert body["data"]["providerStatus"]["used"] is True
        assert "reason" not in body["data"]["providerStatus"]
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_audit_records_exchange_and_provider_trace(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        result={
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {
                    "returncode": 0,
                    "duration_ms": 1234,
                    "json_events": [
                        {"type": "thread.started", "thread_id": "thread-test"},
                        {"type": "turn.started"},
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "item_0",
                                "type": "agent_message",
                                "text": "Audited provider answer.",
                            },
                        },
                        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
                    ],
                },
                "redaction": {"provider_invocation": {"redacted_fields": 0}},
            },
        }
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "What is the scoped portfolio?",
                "focus": "portfolio",
                "session_id": "mgmt-audit-session",
                "trace_id": "mgmt-audit-trace",
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-audit"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["trace_id"] == "mgmt-audit-trace"
        assert body["data"]["audit_log"]["href"].endswith(
            "session_id=mgmt-audit-session&trace_id=mgmt-audit-trace"
        )
        assert fake.calls[0]["trace_id"] == "mgmt-audit-trace"
        assert fake.calls[0]["metadata"]["trace_id"] == "mgmt-audit-trace"

        session = bff_main.read_store.get_agora_session("mgmt-audit-session")
        assert session is not None
        roles = [message["role"] for message in session["messages"]]
        assert roles == ["user", "assistant"]
        assert session["messages"][1]["content"] == "Audited provider answer."

        audit_resp = client.get(
            "/bff/management/ai/audit?session_id=mgmt-audit-session&trace_id=mgmt-audit-trace",
            headers=OPERATOR_HEADERS,
        )
        assert audit_resp.status_code == 200, audit_resp.text
        events = audit_resp.json()["data"]
        event_types = [event["event_type"] for event in events]
        assert event_types == [
            "management_ai.exchange.accepted",
            "management_ai.provider.started",
            "management_ai.provider.completed",
            "management_ai.exchange.completed",
        ]
        completed = next(event for event in events if event["event_type"] == "management_ai.provider.completed")
        assert completed["output_summary"]["json_event_types"] == [
            "thread.started",
            "turn.started",
            "item.completed",
            "turn.completed",
        ]
        assert completed["output_summary"]["assistant_messages"] == ["Audited provider answer."]
        assert "Authorization" not in json.dumps(events)
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_degraded_falls_back_to_deterministic_answer(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        exc=OpenClawOpsClientError(
            "adapter unavailable",
            status_code=503,
            error_code="OPENCLAW_ADAPTER_UNREACHABLE",
        )
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-degraded"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"].startswith("Management summary for question:")
        provider_status = body["data"]["providerStatus"]
        assert provider_status["status"] == "degraded"
        assert provider_status["reason"] == "OPENCLAW_ADAPTER_UNREACHABLE"
        assert provider_status["fallback"] == "deterministic_synthesis"
        assert provider_status["used"] is False
        assert len(fake.calls) == 1
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_enabled_requires_read_role_before_invocation(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={"Idempotency-Key": "asst-bff-002-no-auth"},
        )

        assert resp.status_code == 401, resp.text
        assert fake.calls == []
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_high_risk_refusal_runs_before_provider_invocation(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "Please restart runtime rt-alpha", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-high-risk"},
        )

        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "high_risk_nl_policy"
        assert fake.calls == []
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()
