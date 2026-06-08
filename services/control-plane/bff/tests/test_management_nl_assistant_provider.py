from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from assistant.control_mode import ControlModeStore
from assistant.models import AssistantMode
from models import OperatorIdentity
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

    def get_assistant_readiness(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "provider": "codex_cli",
            "runtime": "openclaw_gateway_cli_mount",
            "ready": True,
            "status": "ready",
            "capabilities": {"read": True, "repairWrite": True},
        }

    def get_tool_policy(self) -> dict[str, Any]:
        return {
            "allowed_tools": ["assistant.command"],
            "allowed_workflows": [],
            "assistant_command_tool": "assistant.command",
            "default_posture": "deny_all",
            "always_blocked_tools": ["broker_order", "live_order", "paper_order"],
            "always_blocked_tool_prefixes": ["broker.", "live.", "paper.", "capital."],
            "always_blocked_workflow_prefixes": ["broker.", "live.", "paper.", "capital."],
        }

    def list_effective_tools(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "degraded",
            "upstream_status": "degraded",
            "agent_id": kwargs.get("agent_id") or "management-ai",
            "policy_allowed_tools": ["assistant.command"],
            "effective_tools": [],
        }


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
            "personas": {
                "persona-alpha": {
                    "persona_id": "persona-alpha",
                    "name": "Alpha Persona",
                    "tenant_id": "tenant-alpha",
                    "lifecycle_state": "active",
                    "created_at": "2026-06-01T00:00:00Z",
                },
                "persona-beta": {
                    "persona_id": "persona-beta",
                    "name": "Beta Persona",
                    "tenant_id": "tenant-beta",
                    "lifecycle_state": "active",
                    "created_at": "2026-06-01T00:00:00Z",
                },
            },
            "persona_bindings": {
                "binding-alpha": {
                    "binding_id": "binding-alpha",
                    "persona_id": "persona-alpha",
                    "tenant_id": "tenant-alpha",
                    "capital_pool_id": "pool-alpha",
                    "status": "active",
                },
                "binding-beta": {
                    "binding_id": "binding-beta",
                    "persona_id": "persona-beta",
                    "tenant_id": "tenant-beta",
                    "capital_pool_id": "pool-beta",
                    "status": "active",
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
    bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
        storage_path="off",
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
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


def _kernel_operator_identity(
    *,
    operator_id: str = "asst-bff-002",
    roles: list[str] | None = None,
    mfa_verified: bool = True,
    capabilities: list[str] | None = None,
) -> OperatorIdentity:
    resolved_roles = roles or ["operator"]
    resolved_capabilities = capabilities or ["assistant.kernel.debug"]
    return OperatorIdentity(
        operator_id=operator_id,
        roles=resolved_roles,
        mfa_verified=mfa_verified,
        claims={
            "sub": operator_id,
            "roles": resolved_roles,
            "capabilities": resolved_capabilities,
        },
        token_kind="stub",
    )


def test_management_ai_conversation_store_persists_sessions_and_turns_to_json(tmp_path: Path) -> None:
    store_path = str(tmp_path / "management-ai.json")
    store = bff_main.ManagementAiConversationStore(
        storage_path=store_path,
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
    store.upsert_session(
        session_id="mgmt-json-session",
        owner_id="asst-bff-002",
        tenant_id="tenant-alpha",
        now="2026-06-03T00:00:00Z",
        title="JSON persistence",
    )
    store.append_turn(
        turn_id="turn-user",
        session_id="mgmt-json-session",
        role="user",
        text="Persist me",
        created_at="2026-06-03T00:00:01Z",
        attachments=[{"id": "att-1", "kind": "image", "mimeType": "image/png", "filename": "x.png", "sizeBytes": 3, "storageUrl": "local://x"}],
        ui_snapshot={"route": "/management"},
    )

    reloaded = bff_main.ManagementAiConversationStore(
        storage_path=store_path,
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
    assert reloaded.get_session("mgmt-json-session")["ownerId"] == "asst-bff-002"
    turns = reloaded.list_turns("mgmt-json-session")
    assert len(turns) == 1
    assert turns[0]["text"] == "Persist me"
    assert turns[0]["attachments"][0]["storageUrl"] == "local://x"
    assert turns[0]["uiSnapshot"] == {"route": "/management"}
    sessions = reloaded.list_sessions(owner_id="asst-bff-002", tenant_id="tenant-alpha")
    assert [session["sessionId"] for session in sessions] == ["mgmt-json-session"]
    assert reloaded.list_sessions(owner_id="other-operator", tenant_id="tenant-other") == []


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


def test_openclaw_client_forwards_codex_multimodal_body(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aW1n"},
                    "attachmentId": "att-1",
                },
            ],
        }
    ]
    attachments = [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aW1n"},
            "attachmentId": "att-1",
        }
    ]

    def fake_urlopen(request, timeout):
        recorded["body"] = json.loads(request.data.decode("utf-8"))
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
        OpenClawOpsClient(timeout_seconds=1.5).invoke_assistant_provider(
            provider="codex_cli",
            mode="user",
            prompt="hello",
            context_pack={"context_pack_id": "ctx-test"},
            operator_id="operator-1",
            messages=messages,
            attachments=attachments,
        )

    assert recorded["body"]["messages"] == messages
    assert recorded["body"]["attachments"] == attachments


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


def test_openclaw_client_reads_assistant_provider_readiness_with_auth_probe(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        return FakeHttpResponse(
            {
                "provider": "codex_cli",
                "ready": True,
                "status": "ready",
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).get_assistant_readiness(
            provider="codex_cli",
            auth_probe=True,
        )

    assert result["status"] == "ready"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/readiness/codex_cli?auth_probe=true"
    )
    assert recorded["timeout"] == 1.5


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
        persona_health = call["context_pack"]["backend"]["persona_health"]
        persona_ids = {item["persona_id"] for item in persona_health["items"]}
        assert "persona-alpha" in persona_ids
        assert "persona-beta" not in persona_ids
        source_ids = {source["source_id"] for source in call["context_pack"]["sources"]}
        assert "persona_health" in source_ids
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_persona_fleet_summary_includes_health_items(tmp_path, monkeypatch) -> None:
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
            json={"question": "How is the persona fleet?", "focus": "persona_fleet"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-persona-health"},
        )

        assert resp.status_code == 202, resp.text
        management_context = fake.calls[0]["context_pack"]["backend"]["management_nl"]["data"]
        fleet_context = management_context["summary_context"]["persona_fleet"]
        assert fleet_context["summary"]["total_personas"] >= 1
        items_by_id = {item["persona_id"]: item for item in fleet_context["items"]}
        assert items_by_id["persona-alpha"]["health"]["status"] in {"healthy", "degraded", "critical"}
        assert isinstance(items_by_id["persona-alpha"]["health"]["reasons"], list)
        assert "persona-beta" not in json.dumps(fleet_context)
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_passes_conversation_and_ui_context_to_provider(tmp_path, monkeypatch) -> None:
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
            json={
                "question": "Continue from the previous answer.",
                "focus": "persona",
                "sessionId": "mgmt-multi-session",
                "conversation": {
                    "recentTurns": [
                        {"role": "user", "content": "What is unhealthy?"},
                        {"role": "assistant", "content": "Persona alpha is degraded."},
                    ],
                    "summary": "The operator is reviewing degraded personas.",
                },
                "ui": {
                    "currentRoute": "/management/personas",
                    "selectedEntity": {"kind": "persona", "id": "persona-alpha"},
                    "visiblePanels": ["AgentPanel", "PersonaFleet"],
                    "filters": {"severity": "high"},
                    "availableUiActions": [
                        {"kind": "navigate", "description": "Go to a route", "paramsSchema": "{ to: string }"},
                        {
                            "kind": "runBffAction",
                            "description": "Run a BFF action",
                            "paramsSchema": "{ endpoint: string, body?: object }",
                        },
                    ],
                },
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-multiturn-context"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["sessionId"] == "mgmt-multi-session"
        assert body["data"]["conversation"]["href"].endswith(
            "/bff/management/ai/conversations/mgmt-multi-session"
        )
        assert "trace_id=" not in body["data"]["conversation"]["href"]
        assert body["data"]["session"]["ttlSeconds"] >= 7 * 24 * 60 * 60

        management_context = fake.calls[0]["context_pack"]["backend"]["management_nl"]["data"]
        assert management_context["focus"] == "persona_fleet"
        assert management_context["conversation"]["source"] == "server"
        assert management_context["conversation"]["recentTurns"][0]["content"] == "Continue from the previous answer."
        assert management_context["conversation"]["clientHint"]["recentTurns"][0]["content"] == "What is unhealthy?"
        assert management_context["conversation"]["summary"] == "The operator is reviewing degraded personas."
        assert management_context["ui"]["currentRoute"] == "/management/personas"
        assert management_context["ui"]["selectedEntity"] == {"kind": "persona", "id": "persona-alpha"}
        assert management_context["ui"]["filters"] == {"severity": "high"}
        assert [item["kind"] for item in management_context["ui"]["availableUiActions"]] == [
            "navigate",
            "runBffAction",
        ]
        assert fake.calls[0]["context_pack"]["frontend"]["route"] == "/management/personas"
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_context_pack_reflects_active_control_mode(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    control_store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
    control_store.activate(
        actor_id="asst-bff-002",
        mode=AssistantMode.KERNEL_DEBUG,
        capabilities=["assistant.kernel.debug"],
        reason="debug management AI context",
        passphrase="control phrase ok",
        ttl_seconds=900,
        idle_ttl_seconds=120,
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "What mode is this management assistant using?",
                "sessionId": "mgmt-control-session",
                "ui": {"currentRoute": "/management/cockpit", "availableUiActions": []},
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-mode-context"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["controlMode"]["active"] is True
        assert body["data"]["controlMode"]["mode"] == "kernel_debug"
        context_pack = body["data"]["contextPack"]
        assert context_pack["mode"] == "kernel_debug"
        assert "assistant.kernel.debug" in context_pack["actor"]["capabilities"]
        management_context = context_pack["backend"]["management_nl"]["data"]
        assert management_context["controlMode"]["active"] is True
        assert management_context["controlMode"]["mode"] == "kernel_debug"
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_context_pack_includes_orchestrator_status_for_system_questions(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    status_root = tmp_path / "status-root"
    orchestrator_dir = status_root / ".orchestrator"
    orchestrator_dir.mkdir(parents=True)
    _write_json(
        status_root / "ai-status.json",
        {
            "project": "pantheon",
            "sprint": "status-context",
            "objective": "Expose supervisor state to Management AI",
            "tasks": [
                {
                    "id": "MGMT-AI-STATUS",
                    "title": "Expose orchestrator status",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                }
            ],
        },
    )
    _write_json(
        orchestrator_dir / "state.json",
        {
            "supervisor": {
                "pid": 4242,
                "lifecycle": "running",
                "last_heartbeat_at": "2026-06-07T09:00:00Z",
            },
            "workers": {},
            "queue": {"events": {}},
            "assistant_dev_bridge": {
                "last_drain_at": "2026-06-07T09:01:00Z",
                "last_result": {"status": "drained", "errorCount": 0},
            },
        },
    )
    _write_json(orchestrator_dir / "github-bus-state.json", {"tasks": {}})
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "Can you see the supervisor status?",
                "focus": "cockpit",
                "sessionId": "mgmt-orchestrator-status-context",
                "ui": {"currentRoute": "/management/cockpit", "availableUiActions": []},
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-orchestrator-context"},
        )

        assert resp.status_code == 202, resp.text
        context_pack = fake.calls[0]["context_pack"]
        orchestrator_context = context_pack["backend"]["orchestrator_status"]["data"]
        assert orchestrator_context["project"] == "pantheon"
        assert orchestrator_context["taskCount"] == 1
        assert orchestrator_context["supervisor"]["lifecycle"] == "running"
        assert orchestrator_context["supervisor"]["pid"] == 4242
        assert orchestrator_context["assistantDevBridge"]["lastDrainAt"] == "2026-06-07T09:01:00Z"
        assert orchestrator_context["providerReadiness"]["status"] == "ready"
        assert orchestrator_context["openclawToolPolicy"]["status"] == "ready"
        assert orchestrator_context["openclawToolPolicy"]["assistantCommandAllowed"] is True
        assert orchestrator_context["openclawToolPolicy"]["allowedTools"] == ["assistant.command"]
        assert orchestrator_context["openclawToolPolicy"]["effectiveStatus"] == "degraded"
        assert {ref["path"] for ref in orchestrator_context["sourceRefs"]} == {
            "ai-status.json",
            ".orchestrator/state.json",
            ".orchestrator/github-bus-state.json",
        }
        source_ids = {
            source.get("sourceId") or source.get("source_id")
            for source in context_pack["sources"]
        }
        assert "orchestrator_status" in source_ids
        assert (
            "Use backend.orchestrator_status.data for supervisor"
            in fake.calls[0]["prompt"]
        )
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_provider_uses_active_kernel_debug_mode(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    control_store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
    control_store.activate(
        actor_id="asst-bff-002",
        mode=AssistantMode.KERNEL_DEBUG,
        capabilities=["assistant.kernel.debug"],
        reason="debug management AI through OpenClaw",
        passphrase="control phrase ok",
        ttl_seconds=900,
        idle_ttl_seconds=120,
    )
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        monkeypatch.setattr(
            bff_main,
            "_extract_identity",
            lambda authorization: _kernel_operator_identity(),
        )
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "Use OpenClaw to inspect the current assistant debug context.",
                "sessionId": "mgmt-kernel-debug-provider",
                "ui": {"currentRoute": "/management/cockpit", "availableUiActions": []},
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-provider-kernel-debug"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["controlMode"]["active"] is True
        assert body["data"]["providerStatus"]["mode"] == "kernel_debug"
        call = fake.calls[0]
        assert call["mode"] == "kernel_debug"
        assert call["metadata"]["control_mode"]["active"] is True
        assert call["metadata"]["control_mode"]["mode"] == "kernel_debug"
        assert "You are operating in kernel_debug mode through OpenClaw/Codex." in call["prompt"]
        assert "read-only workspace" in call["prompt"]
        assert "You are operating in user mode." not in call["prompt"]
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_kernel_repair_passes_openclaw_task_metadata(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    control_store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
    control_store.activate(
        actor_id="asst-bff-002",
        mode=AssistantMode.KERNEL_REPAIR,
        capabilities=["assistant.kernel.repair"],
        reason="repair management AI through OpenClaw task worktree",
        passphrase="control phrase ok",
        ttl_seconds=900,
        idle_ttl_seconds=120,
    )
    fake = FakeProviderClient(
        result={
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {
                    "json_events": [{"final": "Repair worktree is ready for scoped file edits."}],
                    "sandbox": "workspace-write",
                    "workspace_class": "task_worktree",
                    "repair_workflow": {
                        "task_id": "ASST-REPAIR-123",
                        "branch": "task/ASST-REPAIR-123",
                        "merge_target": "dev",
                    },
                },
            },
        }
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        monkeypatch.setattr(
            bff_main,
            "_extract_identity",
            lambda authorization: _kernel_operator_identity(capabilities=["assistant.kernel.repair"]),
        )
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "Update the assistant integration files according to this repair task.",
                "sessionId": "mgmt-kernel-repair-provider",
                "openclaw": {
                    "repair": {
                        "taskId": "ASST-REPAIR-123",
                        "taskWorktree": "/srv/pantheon-assistant/worktrees/asst-repair-123",
                        "declaredScope": [
                            "services/control-plane/bff/main.py",
                            "services/control-plane/bff/tests/test_management_nl_assistant_provider.py",
                        ],
                        "expectedBranch": "task/ASST-REPAIR-123",
                        "mergeTarget": "dev",
                        "requireClean": True,
                        "repoKey": "pantheon",
                    }
                },
                "ui": {"currentRoute": "/management/cockpit", "availableUiActions": []},
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-provider-kernel-repair"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        provider_status = body["data"]["providerStatus"]
        assert provider_status["mode"] == "kernel_repair"
        assert provider_status["sandbox"] == "workspace-write"
        assert provider_status["workspaceClass"] == "task_worktree"
        assert provider_status["repairWorkflow"]["task_id"] == "ASST-REPAIR-123"
        call = fake.calls[0]
        assert call["mode"] == "kernel_repair"
        assert call["metadata"]["task_id"] == "ASST-REPAIR-123"
        assert call["metadata"]["task_worktree"] == "/srv/pantheon-assistant/worktrees/asst-repair-123"
        assert call["metadata"]["declared_scope"] == [
            "services/control-plane/bff/main.py",
            "services/control-plane/bff/tests/test_management_nl_assistant_provider.py",
        ]
        assert call["metadata"]["expected_branch"] == "task/ASST-REPAIR-123"
        assert call["metadata"]["merge_target"] == "dev"
        assert call["metadata"]["repo_key"] == "pantheon"
        assert call["metadata"]["require_clean"] is True
        assert call["metadata"]["repair_metadata_source"] == "management_nl_openclaw_payload"
        assert "You are operating in kernel_repair mode through OpenClaw/Codex." in call["prompt"]
        assert "workspace-write" in call["prompt"]
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_direct_passphrase_activates_control_mode_without_provider_or_secret_readback(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    passphrase = "九條好漢在一班"
    control_store = ControlModeStore(storage_path="off", initial_passphrase=passphrase)
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        monkeypatch.setattr(
            bff_main,
            "_extract_identity",
            lambda authorization: _kernel_operator_identity(),
        )
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": passphrase,
                "sessionId": "mgmt-chat-control-session",
                "focus": "cockpit",
                "ui": {"currentRoute": "/management/cockpit", "availableUiActions": []},
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-direct-passphrase"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["question"] == "[CONTROL MODE COMMAND REDACTED]"
        assert body["data"]["controlMode"]["active"] is True
        assert body["data"]["controlMode"]["mode"] == "kernel_debug"
        assert body["data"]["controlCommand"] == "activate"
        assert body["data"]["contextPack"] is None
        provider_status = body["data"]["providerStatus"]
        assert provider_status["provider"] == "pantheon_bff"
        assert provider_status["runtime"] == "management_nl_control_command_interceptor"
        assert provider_status["used"] is True
        assert fake.calls == []

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-chat-control-session",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        serialized_conversation = json.dumps(conversation_resp.json(), ensure_ascii=False)
        assert passphrase not in serialized_conversation
        assert "[CONTROL MODE COMMAND REDACTED]" in serialized_conversation
        assert "Control mode activated" in serialized_conversation

        serialized_audit = json.dumps(list(bff_main._MGMT_AI_AUDIT_EVENTS), ensure_ascii=False)
        assert passphrase not in serialized_audit
        assert "[CONTROL MODE COMMAND REDACTED]" in serialized_audit
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_explicit_control_status_and_off_are_redacted(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    passphrase = "九條好漢在一班"
    control_store = ControlModeStore(storage_path="off", initial_passphrase=passphrase)
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        monkeypatch.setattr(
            bff_main,
            "_extract_identity",
            lambda authorization: _kernel_operator_identity(),
        )
        client = _seeded_client(tmp_path, monkeypatch)

        activate_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": f"控制模式：{passphrase}", "sessionId": "mgmt-chat-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-prefix"},
        )
        assert activate_resp.status_code == 202, activate_resp.text
        assert activate_resp.json()["data"]["controlMode"]["active"] is True

        status_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "/control status", "sessionId": "mgmt-chat-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-status"},
        )
        assert status_resp.status_code == 202, status_resp.text
        assert status_resp.json()["data"]["controlMode"]["active"] is True
        assert status_resp.json()["data"]["controlCommand"] == "status"

        off_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "/control off", "sessionId": "mgmt-chat-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-off"},
        )
        assert off_resp.status_code == 202, off_resp.text
        assert off_resp.json()["data"]["controlMode"]["active"] is False
        assert off_resp.json()["data"]["controlCommand"] == "deactivate"
        assert fake.calls == []

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-chat-control-status",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        conversation = conversation_resp.json()["data"]
        serialized = json.dumps(conversation, ensure_ascii=False)
        assert passphrase not in serialized
        user_turns = [turn for turn in conversation["turns"] if turn["role"] == "user"]
        assert len(user_turns) == 3
        assert all(turn["text"] == "[CONTROL MODE COMMAND REDACTED]" for turn in user_turns)
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_chat_control_command_requires_authorized_operator(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    passphrase = "九條好漢在一班"
    control_store = ControlModeStore(storage_path="off", initial_passphrase=passphrase)
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        monkeypatch.setattr(
            bff_main,
            "_extract_identity",
            lambda authorization: _kernel_operator_identity(roles=["reviewer"]),
        )
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": f"/control {passphrase}", "sessionId": "mgmt-chat-control-denied"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-denied"},
        )

        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "control_mode_role"
        assert control_store.status_for_actor("asst-bff-002")["active"] is False
        assert fake.calls == []
        assert passphrase not in json.dumps(list(bff_main._MGMT_AI_AUDIT_EVENTS), ensure_ascii=False)
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_filters_provider_actions_to_ui_allowlist(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        result={
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {
                    "json_events": [
                        {
                            "final": {
                                "answer": "Action-aware provider answer.",
                                "actions": [
                                    {
                                        "id": "nav-ok",
                                        "kind": "navigate",
                                        "label": "Open cockpit",
                                        "rationale": "The user asked for cockpit context.",
                                        "params": {"to": "/management/cockpit"},
                                    },
                                    {
                                        "id": "write-ok",
                                        "kind": "runBffAction",
                                        "label": "Run refresh",
                                        "rationale": "Needs an explicit operator click.",
                                        "params": {"endpoint": "/bff/runtimes/rt-alpha/actions/Refresh", "body": {}},
                                        "requiresConfirmation": False,
                                    },
                                    {
                                        "id": "bad-kind",
                                        "kind": "openDrawer",
                                        "params": {"drawer": "personaDetail"},
                                    },
                                    {
                                        "id": "bad-params",
                                        "kind": "navigate",
                                        "params": {},
                                    },
                                ],
                            }
                        }
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
            json={
                "question": "Show me the cockpit and suggest the refresh.",
                "sessionId": "mgmt-action-session",
                "ui": {
                    "availableUiActions": [
                        {"kind": "navigate", "description": "Navigate", "paramsSchema": "{ to: string }"},
                        {
                            "kind": "runBffAction",
                            "description": "Run BFF action",
                            "paramsSchema": "{ endpoint: string, body?: object }",
                        },
                    ]
                },
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-actions"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"] == "Action-aware provider answer."
        actions = body["data"]["actions"]
        assert [action["id"] for action in actions] == ["nav-ok", "write-ok"]
        assert actions[0]["requiresConfirmation"] is False
        assert actions[1]["requiresConfirmation"] is True
        assert actions[1]["kind"] == "runBffAction"
        assert actions[1]["params"]["endpoint"].startswith("/bff/")

        completed = [
            event for event in bff_main._MGMT_AI_AUDIT_EVENTS
            if event.get("event_type") == "management_ai.exchange.completed"
        ][-1]
        assert completed["action_count"] == 2
        assert completed["actions"] == actions
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
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
        assert body["data"]["conversation"]["href"].endswith(
            "/bff/management/ai/conversations/mgmt-audit-session"
        )
        assert "trace_id=" not in body["data"]["conversation"]["href"]
        assert fake.calls[0]["trace_id"] == "mgmt-audit-trace"
        assert fake.calls[0]["metadata"]["trace_id"] == "mgmt-audit-trace"

        assert bff_main.read_store.get_agora_session("mgmt-audit-session") is None

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-audit-session?trace_id=mgmt-audit-trace",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        conversation = conversation_resp.json()["data"]
        assert conversation["sessionId"] == "mgmt-audit-session"
        assert conversation["session_id"] == "mgmt-audit-session"
        turns = conversation["turns"]
        assert [turn["role"] for turn in turns] == ["user", "assistant"]
        assert turns[0]["id"] == turns[0]["message_id"]
        assert turns[0]["text"] == "What is the scoped portfolio?"
        assert turns[0]["createdAt"]
        assert turns[0]["content"] == "What is the scoped portfolio?"
        assert turns[1]["text"] == "Audited provider answer."
        assert turns[1]["content"] == "Audited provider answer."
        assert turns[1]["providerStatus"]["used"] is True
        assert turns[1]["provider_status"]["used"] is True

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


def test_management_ai_conversation_reader_returns_full_session_and_ignores_trace_filter(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        result={
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {"json_events": [{"final": "Threaded provider answer."}]},
            },
        }
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        for turn_no, trace_id in enumerate(("trace-one", "trace-two"), start=1):
            resp = client.post(
                "/bff/management/nl/ask",
                json={
                    "question": f"Question {turn_no}?",
                    "focus": "portfolio",
                    "sessionId": "mgmt-full-session",
                    "traceId": trace_id,
                    "ui": {
                        "availableUiActions": [
                            {"kind": "navigate", "description": "Navigate", "paramsSchema": "{ to: string }"},
                        ]
                    },
                },
                headers={**OPERATOR_HEADERS, "Idempotency-Key": f"asst-bff-002-full-session-{turn_no}"},
            )
            assert resp.status_code == 202, resp.text

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-full-session?trace_id=trace-one",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        body = conversation_resp.json()
        assert body["data"]["sessionId"] == "mgmt-full-session"
        assert body["meta"]["filters"]["trace_id_ignored"] is True
        turns = body["data"]["turns"]
        assert [turn["role"] for turn in turns] == ["user", "assistant", "user", "assistant"]
        assert [turn["text"] for turn in turns] == [
            "Question 1?",
            "Threaded provider answer.",
            "Question 2?",
            "Threaded provider answer.",
        ]
        assert turns[1]["providerStatus"]["provider"] == "codex_cli"
        assert turns[1]["actions"] == []
        assert body["data"]["session"]["ttlSeconds"] >= 7 * 24 * 60 * 60

        list_resp = client.get("/bff/management/ai/conversations", headers=OPERATOR_HEADERS)
        assert list_resp.status_code == 200, list_resp.text
        sessions = list_resp.json()["items"]
        assert sessions[0]["sessionId"] == "mgmt-full-session"
        assert sessions[0]["href"] == "/bff/management/ai/conversations/mgmt-full-session"
        assert sessions[0]["turnCount"] == 4
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_persists_30_messages_as_60_ordered_turns(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        for turn_no in range(1, 31):
            resp = client.post(
                "/bff/management/nl/ask",
                json={
                    "question": f"Persistence question {turn_no}?",
                    "focus": "portfolio",
                    "sessionId": "mgmt-persist-30-session",
                    "conversation": {
                        "recentTurns": [
                            {"role": "user", "content": f"FE window only has turn {turn_no}."}
                        ],
                        "summary": "FE hint must not replace server-side history.",
                    },
                },
                headers={**OPERATOR_HEADERS, "Idempotency-Key": f"asst-bff-002-persist-30-{turn_no}"},
            )
            assert resp.status_code == 202, resp.text

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-persist-30-session",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        turns = conversation_resp.json()["data"]["turns"]
        assert len(turns) == 60
        assert [turn["role"] for turn in turns[:4]] == ["user", "assistant", "user", "assistant"]
        assert turns[0]["text"] == "Persistence question 1?"
        assert turns[-2]["text"] == "Persistence question 30?"
        assert turns[-1]["text"] == "Provider grounded management answer."

        last_management_context = fake.calls[-1]["context_pack"]["backend"]["management_nl"]["data"]
        assert last_management_context["conversation"]["source"] == "server"
        assert last_management_context["conversation"]["historySource"] == "management_ai_store"
        assert last_management_context["conversation"]["historyCharBudget"] > 32 * 1024
        assert len(last_management_context["conversation"]["recentTurns"]) == 59
        assert last_management_context["conversation"]["recentTurns"][0]["content"] == "Persistence question 1?"
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_uses_server_history_when_fe_recent_turns_are_truncated(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        for turn_no in range(1, 4):
            resp = client.post(
                "/bff/management/nl/ask",
                json={
                    "question": f"Server history question {turn_no}?",
                    "focus": "portfolio",
                    "sessionId": "mgmt-server-history-session",
                    "conversation": {
                        "recentTurns": [
                            {"role": "user", "content": "Only the latest FE-window hint remains."}
                        ],
                        "summary": "Short FE summary hint.",
                    },
                },
                headers={**OPERATOR_HEADERS, "Idempotency-Key": f"asst-bff-002-server-history-{turn_no}"},
            )
            assert resp.status_code == 202, resp.text

        management_context = fake.calls[-1]["context_pack"]["backend"]["management_nl"]["data"]
        conversation = management_context["conversation"]
        assert conversation["source"] == "server"
        assert [turn["content"] for turn in conversation["recentTurns"]] == [
            "Server history question 1?",
            "Provider grounded management answer.",
            "Server history question 2?",
            "Provider grounded management answer.",
            "Server history question 3?",
        ]
        assert conversation["historySource"] == "management_ai_store"
        assert conversation["storedTurnCount"] == 5
        assert conversation["historyTruncated"] is False
        assert conversation["historyCharBudget"] > 32 * 1024
        prompt = fake.calls[-1]["prompt"]
        assert "Server-side conversation history JSON" in prompt
        assert "Server history question 1?" in prompt
        assert "Only the latest FE-window hint remains." in prompt
        assert conversation["clientHint"]["recentTurns"] == [
            {
                "role": "user",
                "content": "Only the latest FE-window hint remains.",
                "text": "Only the latest FE-window hint remains.",
            }
        ]
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_provider_history_window_exceeds_fe_budget_and_truncates() -> None:
    turns = [
        {
            "id": f"turn-{idx:02d}",
            "role": "user" if idx % 2 == 0 else "assistant",
            "content": f"large stored turn {idx:02d} " + ("x" * 4096),
            "text": f"large stored turn {idx:02d} " + ("x" * 4096),
            "createdAt": f"2026-06-03T00:{idx:02d}:00Z",
            "created_at": f"2026-06-03T00:{idx:02d}:00Z",
        }
        for idx in range(40)
    ]

    windowed, budget = bff_main._management_ai_provider_history_window(turns)

    assert budget["historyCharBudget"] > 32 * 1024
    assert budget["historyTruncated"] is True
    assert budget["historyOmittedTurnCount"] > 0
    assert budget["historyEstimatedChars"] <= budget["historyCharBudget"]
    assert windowed[-1]["id"] == "turn-39"
    assert [turn["createdAt"] for turn in windowed] == sorted(turn["createdAt"] for turn in windowed)


def test_management_ai_idempotency_replay_does_not_duplicate_persisted_turns(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        payload = {
            "question": "Will idempotency duplicate turns?",
            "focus": "portfolio",
            "sessionId": "mgmt-idempotent-session",
        }
        headers = {**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-idempotent-persist"}
        first = client.post("/bff/management/nl/ask", json=payload, headers=headers)
        replay = client.post("/bff/management/nl/ask", json=payload, headers=headers)
        assert first.status_code == 202, first.text
        assert replay.status_code == 202, replay.text
        assert replay.json() == first.json()

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-idempotent-session",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        turns = conversation_resp.json()["data"]["turns"]
        assert len(turns) == 2
        assert [turn["role"] for turn in turns] == ["user", "assistant"]
        assert len(fake.calls) == 1
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_conversation_missing_session_returns_404(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    try:
        client = _seeded_client(tmp_path, monkeypatch)
        resp = client.get(
            "/bff/management/ai/conversations/mgmt-missing-session",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert body["error"]["details"]["precondition_failed"] == "management_ai_session"
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_conversation_get_enforces_owner_or_tenant_scope(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    try:
        client = _seeded_client(tmp_path, monkeypatch)
        store = bff_main._MGMT_AI_CONVERSATION_STORE
        store.upsert_session(
            session_id="mgmt-owned-session",
            owner_id="asst-bff-002",
            tenant_id="tenant-beta",
            now="2026-06-03T00:00:00Z",
            title="Owned session",
        )
        store.append_turn(
            turn_id="owned-turn",
            session_id="mgmt-owned-session",
            role="user",
            text="Owner-visible turn",
            created_at="2026-06-03T00:00:01Z",
        )
        store.upsert_session(
            session_id="mgmt-tenant-session",
            owner_id="other-operator",
            tenant_id="tenant-beta",
            now="2026-06-03T00:00:00Z",
            title="Tenant session",
        )
        store.append_turn(
            turn_id="tenant-turn",
            session_id="mgmt-tenant-session",
            role="assistant",
            text="Tenant-visible turn",
            created_at="2026-06-03T00:00:01Z",
        )

        owned_resp = client.get(
            "/bff/management/ai/conversations/mgmt-owned-session",
            headers=OPERATOR_HEADERS,
        )
        assert owned_resp.status_code == 200, owned_resp.text
        assert owned_resp.json()["data"]["turns"][0]["text"] == "Owner-visible turn"

        tenant_resp = client.get(
            "/bff/management/ai/conversations/mgmt-tenant-session",
            headers={**OPERATOR_HEADERS, "X-Tenant-Id": "tenant-beta"},
        )
        assert tenant_resp.status_code == 200, tenant_resp.text
        assert tenant_resp.json()["data"]["turns"][0]["text"] == "Tenant-visible turn"

        scoped_resp = client.get(
            "/bff/management/ai/conversations/mgmt-tenant-session",
            headers=OPERATOR_HEADERS,
        )
        assert scoped_resp.status_code == 404, scoped_resp.text
        assert scoped_resp.json()["error"]["details"]["precondition_failed"] == "management_ai_session"
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_inline_attachment_is_stored_and_read_back_as_proxy_url(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    image_bytes = b"not-a-real-png-but-stable"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "Please inspect this screenshot.",
                "focus": "cockpit",
                "sessionId": "mgmt-attachment-session",
                "attachments": [
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "screen.png",
                        "sizeBytes": len(image_bytes),
                        "dataBase64": encoded,
                    }
                ],
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-attachment"},
        )
        assert resp.status_code == 202, resp.text

        stored_turns = bff_main._MGMT_AI_CONVERSATION_STORE.list_turns("mgmt-attachment-session")
        stored_attachment = stored_turns[0]["attachments"][0]
        assert stored_attachment["storageUrl"].startswith("local://management-ai-attachments/")
        assert "dataBase64" not in stored_attachment
        assert encoded not in json.dumps(stored_turns, ensure_ascii=False)

        conversation_resp = client.get(
            "/bff/management/ai/conversations/mgmt-attachment-session",
            headers=OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        attachment = conversation_resp.json()["data"]["turns"][0]["attachments"][0]
        assert attachment == {
            "id": stored_attachment["id"],
            "attachmentId": stored_attachment["id"],
            "attachment_id": stored_attachment["id"],
            "kind": "image",
            "mimeType": "image/png",
            "mime_type": "image/png",
            "filename": "screen.png",
            "sizeBytes": len(image_bytes),
            "size_bytes": len(image_bytes),
            "url": f"/bff/management/ai/attachments/{stored_attachment['id']}",
        }
        assert "dataBase64" not in json.dumps(conversation_resp.json(), ensure_ascii=False)

        attachment_resp = client.get(attachment["url"], headers=OPERATOR_HEADERS)
        assert attachment_resp.status_code == 200, attachment_resp.text
        assert attachment_resp.content == image_bytes
        assert attachment_resp.headers["content-type"].startswith("image/png")

        metadata_attachments = fake.calls[0]["metadata"]["attachments"]
        assert metadata_attachments[0]["url"] == attachment["url"]
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


def test_codex_auth_unavailable_status_has_operator_notice(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        exc=OpenClawOpsClientError(
            "Codex service-user account session is unavailable or expired.",
            status_code=503,
            error_code="CODEX_AUTH_UNAVAILABLE",
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
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-codex-auth"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"].startswith("Management summary for question:")
        provider_status = body["data"]["providerStatus"]
        assert provider_status["status"] == "degraded"
        assert provider_status["reason"] == "CODEX_AUTH_UNAVAILABLE"
        assert provider_status["reasonCode"] == "CODEX_AUTH_UNAVAILABLE"
        assert provider_status["reason_code"] == "CODEX_AUTH_UNAVAILABLE"
        assert provider_status["severity"] == "warning"
        assert "Codex service-user session expired" in provider_status["displayMessage"]
        assert provider_status["display_message"] == provider_status["displayMessage"]
        assert provider_status["operatorAction"] == "reauth_codex_service_user"
        assert provider_status["operator_action"] == "reauth_codex_service_user"
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


def test_openclaw_client_routes_claude_provider_to_correct_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["headers"] = dict(request.header_items())
        recorded["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse(
            {
                "provider": "claude",
                "status": "ok",
                "text": "claude-smoke-ok",
                "config_dir": "claude_config",
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=2.0).invoke_assistant_provider(
            provider="claude_cli",
            mode="user",
            prompt="Reply with: smoke-ok",
            context_pack={"context_pack_id": "ctx-claude-test"},
            operator_id="operator-claude",
            trace_id="trace-claude-1",
        )

    assert result["status"] == "ok"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/claude/invoke"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-claude"
    assert recorded["headers"]["X-trace-id"] == "trace-claude-1"
    assert recorded["body"] == {
        "prompt": "Reply with: smoke-ok",
        "mode": "user",
        "context_pack": {"context_pack_id": "ctx-claude-test"},
    }


def test_openclaw_client_unsupported_provider_raises_error(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    client = OpenClawOpsClient(timeout_seconds=1.0)
    try:
        client.invoke_assistant_provider(
            provider="gpt-4o",
            mode="user",
            prompt="hello",
            context_pack={},
            operator_id="operator-test",
        )
        assert False, "Expected OpenClawOpsClientError"
    except OpenClawOpsClientError as exc:
        assert exc.error_code == "ASSISTANT_PROVIDER_NOT_SUPPORTED"
        assert exc.status_code == 400


def test_claude_provider_enabled_invokes_openclaw_claude_route(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    recorded_calls: list[dict[str, Any]] = []

    class FakeClaudeProviderClient:
        def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
            recorded_calls.append(kwargs)
            # Claude adapter returns a flat response with "text", not nested data.output.
            return {
                "provider": "claude",
                "status": "ok",
                "text": "Claude management answer.",
                "config_dir": "claude_config",
            }

    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "claude_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: FakeClaudeProviderClient())
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the portfolio status?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-claude-provider"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert len(recorded_calls) == 1
        call = recorded_calls[0]
        assert call["provider"] == "claude_cli"
        assert call["mode"] == "user"
        assert call["operator_id"] == "asst-bff-002"
        provider_status = body["data"]["providerStatus"]
        assert provider_status["used"] is True
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_claude_provider_degraded_falls_back_to_deterministic_answer(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient(
        exc=OpenClawOpsClientError(
            "claude binary not found",
            status_code=503,
            error_code="CLAUDE_BINARY_NOT_FOUND",
        )
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "claude_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the portfolio status?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-claude-degraded"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"].startswith("Management summary for question:")
        provider_status = body["data"]["providerStatus"]
        assert provider_status["status"] == "degraded"
        assert provider_status["reason"] == "CLAUDE_BINARY_NOT_FOUND"
        assert provider_status["fallback"] == "deterministic_synthesis"
        assert provider_status["used"] is False
        assert len(fake.calls) == 1
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()
