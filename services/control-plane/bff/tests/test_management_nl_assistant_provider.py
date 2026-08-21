from __future__ import annotations

import base64
import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from assistant.control_mode import ControlModeStore
from assistant.models import AssistantMode
from management_nl_command_idempotency import (
    ManagementNlCommandIdempotencyStore,
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandScope,
    ManagementNlCommandStorageError,
)
from models import OperatorIdentity
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer asst-bff-002:operator"}


class FakeProviderClient:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        exc: Exception | None = None,
        stream_events: list[dict[str, Any]] | None = None,
    ) -> None:
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
        self.stream_events = stream_events or [
            {"type": "delta", "text": "Streamed provider answer."},
            {
                "type": "done",
                "text": "Streamed provider answer.",
                "elapsed_ms": 12,
                "transport": "responses_http",
            },
        ]
        self.calls: list[dict[str, Any]] = []

    def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result

    def stream_assistant_provider(self, **kwargs: Any):
        self.calls.append({"stream": True, **kwargs})
        if self.exc is not None:
            raise self.exc
        yield from self.stream_events

    def get_assistant_readiness(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "provider": "codex_cli",
            "runtime": "openclaw_gateway_cli_mount",
            "ready": True,
            "status": "ready",
            "capabilities": {"read": True, "repairWrite": True},
        }

    def list_assistant_providers(self, *, auth_probe: bool = False) -> dict[str, Any]:
        return {
            "status": "ok",
            "data": [
                {
                    "provider": "codex_cli",
                    "provider_name": "Codex CLI",
                    "runtime": "openclaw_gateway_cli_mount",
                    "ready": True,
                    "status": "ready",
                    "auth_status": "ready" if auth_probe else "not_checked",
                    "usage": {
                        "status": "captured",
                        "source": "provider_snapshot",
                        "remaining": 12,
                        "remaining_percent": 24,
                        "limit": 50,
                        "used": 38,
                        "unit": "requests",
                        "reset_at": "2026-06-30T00:00:00Z",
                    },
                },
                {
                    "provider": "claude",
                    "provider_name": "Claude CLI",
                    "runtime": "openclaw_gateway_cli_mount",
                    "ready": False,
                    "status": "degraded",
                    "auth_status": "failed" if auth_probe else "not_checked",
                    "usage": {
                        "status": "unknown",
                        "source": "not_configured",
                        "reason": "provider_usage_source_not_configured",
                    },
                },
            ],
            "meta": {"auth_probe": auth_probe},
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


class BlockingProviderClient(FakeProviderClient):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._calls_lock = threading.Lock()

    def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
        with self._calls_lock:
            self.calls.append(kwargs)
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test provider release timed out")
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
    for env_name in (
        "PANTHEON_CAPITAL_API_URL",
        "PANTHEON_CAPITAL_SERVICE_URL",
        "PANTHEON_RUNTIME_MANAGER_URL",
        "PANTHEON_INTERNAL_API_URL",
        "PANTHEON_PAPER_FLEET_RECONCILER_URL",
        "PANTHEON_PAPER_RUNTIME_MONITORING_URL",
        "PANTHEON_TELEMETRY_API_URL",
        "PANTHEON_TELEMETRY_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    read_surface_path = tmp_path / "read_surfaces.json"
    seeded_surfaces = {
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
    }
    _write_json(read_surface_path, seeded_surfaces)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_AI_AUDIT_PATH", str(tmp_path / "management-ai-audit.jsonl"))
    store = ReadSurfaceStore(
        str(read_surface_path),
        allow_local_snapshot_fallback=True,
    )
    for dataset in (
        "capital_pools",
        "runtime_bindings",
        "telemetry_summaries",
        "personas",
        "persona_bindings",
    ):
        store._data[dataset] = json.loads(json.dumps(seeded_surfaces.get(dataset, {})))
    store._save()
    bff_main.read_store = store
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
        "PANTHEON_MANAGEMENT_NL_ASSISTANT_FALLBACK_PROVIDERS",
        "PANTHEON_MGMT_NL_ASSISTANT_FALLBACK_PROVIDERS",
        "PANTHEON_MANAGEMENT_NL_PROVIDER_DEADLINE_SECONDS",
    ):
        monkeypatch.delenv(env_name, raising=False)


def _enable_management_nl_command_idempotency(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED", "true")
    monkeypatch.setenv(
        "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
        str(tmp_path / "management-nl-command-idempotency.json"),
    )
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS", "30")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_WAIT_SECONDS", "5")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_POLL_SECONDS", "0.01")
    bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
    bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


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


def test_management_nl_idempotency_storage_is_scoped_by_actor_and_tenant() -> None:
    client_key = "same-browser-key"

    actor_a = bff_main._mgmt_nl_idempotency_storage_key(
        client_key,
        actor_id="operator-a",
        tenant_id="tenant-alpha",
    )
    actor_b = bff_main._mgmt_nl_idempotency_storage_key(
        client_key,
        actor_id="operator-b",
        tenant_id="tenant-alpha",
    )
    tenant_b = bff_main._mgmt_nl_idempotency_storage_key(
        client_key,
        actor_id="operator-a",
        tenant_id="tenant-beta",
    )

    assert len({actor_a, actor_b, tenant_b}) == 3
    assert all(client_key not in value for value in (actor_a, actor_b, tenant_b))


def test_management_nl_command_store_reloads_exact_result_and_scopes_every_boundary(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "management-nl-command-idempotency.json"
    store = ManagementNlCommandIdempotencyStore(str(store_path))
    request_hash = "a" * 64
    result = {
        "status": "accepted",
        "data": {
            "status": "completed",
            "message_id": "mnl-durable",
            "answer": "canonical replay remains byte-for-byte stable",
        },
        "meta": {"idempotency": {"idempotencyKey": "browser-key"}},
    }
    scope = ManagementNlCommandScope(
        actor_id="operator-a",
        tenant_id="tenant-a",
        route="POST /bff/management/nl/ask",
        idempotency_key="browser-key",
    )

    owner = store.admit(scope, request_hash=request_hash)
    assert owner.state == "owner"
    assert owner.reservation is not None
    store.complete(owner.reservation, result)

    reloaded = ManagementNlCommandIdempotencyStore(str(store_path))
    replay = reloaded.admit(scope, request_hash=request_hash)
    assert replay.state == "complete"
    assert replay.result == result
    assert store_path.stat().st_mode & 0o777 == 0o600
    persisted = json.loads(store_path.read_text(encoding="utf-8"))
    persisted_result = next(iter(persisted["records"].values()))["result"]
    assert persisted_result["meta"]["idempotency"]["idempotencyKey"] != "browser-key"

    short_scope = ManagementNlCommandScope(
        actor_id="operator-a",
        tenant_id="tenant-a",
        route="POST /bff/management/nl/ask",
        idempotency_key="a",
    )
    short_result = {
        "data": {"status": "completed", "answer": "alpha stays unchanged"},
        "meta": {"idempotency": {"idempotencyKey": "a"}},
    }
    short = reloaded.admit(short_scope, request_hash="c" * 64)
    assert short.reservation is not None
    reloaded.complete(short.reservation, short_result)
    assert reloaded.admit(short_scope, request_hash="c" * 64).result == short_result

    with pytest.raises(ManagementNlCommandPayloadConflict):
        reloaded.admit(scope, request_hash="b" * 64)

    scoped_variants = (
        ManagementNlCommandScope("operator-b", "tenant-a", scope.route, "browser-key"),
        ManagementNlCommandScope("operator-a", "tenant-b", scope.route, "browser-key"),
        ManagementNlCommandScope("operator-a", "tenant-a", "POST /different", "browser-key"),
        ManagementNlCommandScope("operator-a", "tenant-a", scope.route, "different-key"),
    )
    assert all(
        reloaded.admit(variant, request_hash=request_hash).state == "owner"
        for variant in scoped_variants
    )


def test_management_nl_command_store_reload_expires_to_uncertain_without_reexecution(
    tmp_path: Path,
) -> None:
    now = [100.0]
    scope = ManagementNlCommandScope(
        "operator-a",
        "tenant-a",
        "POST /bff/management/nl/ask",
        "crash-key",
    )
    request_hash = "c" * 64
    path = tmp_path / "management-nl-command-idempotency.json"
    first = ManagementNlCommandIdempotencyStore(
        str(path),
        recovery_seconds=5,
        clock=lambda: now[0],
    )
    assert first.admit(scope, request_hash=request_hash).state == "owner"

    now[0] = 106.0
    reloaded = ManagementNlCommandIdempotencyStore(
        str(path),
        recovery_seconds=5,
        clock=lambda: now[0],
    )
    with pytest.raises(ManagementNlCommandRecoveryRequired):
        reloaded.observe(scope, request_hash=request_hash)
    with pytest.raises(ManagementNlCommandRecoveryRequired):
        reloaded.admit(scope, request_hash=request_hash)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert list(document["records"].values())[0]["status"] == "uncertain"
    assert list(document["records"].values())[0]["reason"] == "reservation_expired"


def test_management_nl_command_store_size_retention_and_corruption_fail_closed(
    tmp_path: Path,
) -> None:
    now = [100.0]
    path = tmp_path / "bounded-management-nl-commands.json"
    store = ManagementNlCommandIdempotencyStore(
        str(path),
        retention_seconds=1,
        max_response_bytes=1024,
        clock=lambda: now[0],
    )
    first_scope = ManagementNlCommandScope(
        "operator-a", "tenant-a", "POST /bff/management/nl/ask", "first-key"
    )
    first = store.admit(first_scope, request_hash="d" * 64)
    assert first.reservation is not None
    store.complete(first.reservation, {"data": {"status": "completed"}})

    now[0] = 102.0
    second_scope = ManagementNlCommandScope(
        "operator-a", "tenant-a", "POST /bff/management/nl/ask", "second-key"
    )
    second = store.admit(second_scope, request_hash="e" * 64)
    assert second.reservation is not None
    document = json.loads(path.read_text(encoding="utf-8"))
    assert ManagementNlCommandIdempotencyStore.storage_key(first_scope) not in document["records"]
    assert ManagementNlCommandIdempotencyStore.storage_key(second_scope) in document["records"]

    with pytest.raises(ManagementNlCommandStorageError, match="size limit"):
        store.complete(
            second.reservation,
            {"data": {"status": "completed", "answer": "x" * 2048}},
        )
    still_reserved = store.observe(second_scope, request_hash="e" * 64)
    assert still_reserved.state == "wait"

    corrupt_path = tmp_path / "corrupt-management-nl-commands.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")
    corrupt = ManagementNlCommandIdempotencyStore(str(corrupt_path))
    with pytest.raises(ManagementNlCommandStorageError, match="unreadable"):
        corrupt.admit(first_scope, request_hash="f" * 64)


def test_management_nl_request_exception_marks_reservation_uncertain_immediately(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    try:
        _clear_provider_env(monkeypatch)
        _enable_management_nl_command_idempotency(tmp_path, monkeypatch)
        _seeded_client(tmp_path, monkeypatch)
        payload = {
            "question": "Exercise fail-closed command admission.",
            "focus": "portfolio",
            "sessionId": "mgmt-command-exception",
        }
        kwargs = {
            "payload": payload,
            "authorization": OPERATOR_HEADERS["Authorization"],
            "idempotency_key": "mgmt-command-exception-key",
            "x_idempotency_key": None,
            "x_tenant_id": None,
            "x_pantheon_tenant": None,
        }

        with mock.patch.object(
            bff_main,
            "_management_ai_ensure_session",
            side_effect=RuntimeError("injected session failure"),
        ) as ensure_session:
            with pytest.raises(RuntimeError, match="injected session failure"):
                asyncio.run(bff_main.bff_management_nl_ask(**kwargs))
            assert ensure_session.call_count == 1

        path = tmp_path / "management-nl-command-idempotency.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        record = next(iter(document["records"].values()))
        assert record["status"] == "uncertain"
        assert record["reason"] == "request_failed_before_terminal_commit"

        with pytest.raises(HTTPException) as retry:
            asyncio.run(bff_main.bff_management_nl_ask(**kwargs))
        assert retry.value.status_code == 409
        assert retry.value.detail["error"]["details"]["precondition_failed"] == (
            "idempotency_recovery_required"
        )
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


def test_management_nl_concurrent_exact_request_invokes_provider_once_and_replays_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    provider = BlockingProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        _enable_management_nl_command_idempotency(tmp_path, monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS", "8")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: provider)
        _seeded_client(tmp_path, monkeypatch)
        payload = {
            "question": "Prove concurrent exact command admission.",
            "focus": "portfolio",
            "sessionId": "mgmt-command-concurrent-exact",
        }
        key = "mgmt-command-concurrent-key"

        async def exercise() -> tuple[Any, Any]:
            kwargs = {
                "payload": payload,
                "authorization": OPERATOR_HEADERS["Authorization"],
                "idempotency_key": key,
                "x_idempotency_key": None,
                "x_tenant_id": None,
                "x_pantheon_tenant": None,
            }
            first = asyncio.create_task(bff_main.bff_management_nl_ask(**kwargs))
            assert await asyncio.to_thread(provider.entered.wait, 10)
            second = asyncio.create_task(bff_main.bff_management_nl_ask(**kwargs))
            await asyncio.sleep(0.1)
            assert not second.done(), "the exact contender should wait without invoking the provider"
            provider.release.set()
            return await asyncio.gather(first, second)

        first_response, second_response = asyncio.run(exercise())
        assert first_response.status_code == 202
        assert second_response.status_code == 202
        assert json.loads(second_response.body) == json.loads(first_response.body)
        assert len(provider.calls) == 1
        turns = bff_main._management_ai_conversation_store().list_turns(
            "mgmt-command-concurrent-exact"
        )
        assert [turn["role"] for turn in turns] == ["user", "assistant"]
    finally:
        provider.release.set()
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


def test_management_nl_concurrent_conflict_returns_409_before_second_side_effect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    provider = BlockingProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        _enable_management_nl_command_idempotency(tmp_path, monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS", "8")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: provider)
        _seeded_client(tmp_path, monkeypatch)
        key = "mgmt-command-conflict-key"
        first_payload = {
            "question": "First command owns this durable key.",
            "focus": "portfolio",
            "sessionId": "mgmt-command-conflict-owner",
        }
        conflict_payload = {
            "question": "Different command must fail before side effects.",
            "focus": "portfolio",
            "sessionId": "mgmt-command-conflict-contender",
        }

        async def exercise() -> Any:
            common = {
                "authorization": OPERATOR_HEADERS["Authorization"],
                "idempotency_key": key,
                "x_idempotency_key": None,
                "x_tenant_id": None,
                "x_pantheon_tenant": None,
            }
            first = asyncio.create_task(
                bff_main.bff_management_nl_ask(payload=first_payload, **common)
            )
            assert await asyncio.to_thread(provider.entered.wait, 10)
            with pytest.raises(HTTPException) as conflict:
                await bff_main.bff_management_nl_ask(payload=conflict_payload, **common)
            assert getattr(conflict.value, "status_code", None) == 409
            assert len(provider.calls) == 1
            assert (
                bff_main._management_ai_conversation_store().get_session(
                    "mgmt-command-conflict-contender"
                )
                is None
            )
            provider.release.set()
            return await first

        assert asyncio.run(exercise()).status_code == 202
    finally:
        provider.release.set()
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


def test_management_nl_provider_inline_wait_is_mode_independent(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS", "0.2")
    monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS", "12")
    monkeypatch.delenv("PANTHEON_MANAGEMENT_NL_REPAIR_INLINE_TIMEOUT_SECONDS", raising=False)

    assert bff_main._mgmt_nl_provider_inline_wait_seconds(
        {"active": True, "mode": "kernel_debug"}
    ) == 0.2


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


def test_openclaw_client_lists_assistant_providers_with_auth_probe(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        return FakeHttpResponse(
            {
                "status": "ok",
                "data": [
                    {"provider": "codex_cli", "ready": True, "auth_status": "ready"},
                    {"provider": "claude", "ready": False, "auth_status": "failed"},
                ],
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).list_assistant_providers(auth_probe=True)

    assert result["data"][0]["provider"] == "codex_cli"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers?auth_probe=true"
    )
    assert recorded["timeout"] == 1.5


def test_openclaw_client_provider_list_auth_probe_uses_assistant_timeout(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    monkeypatch.setenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0")
    monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS", "12.5")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        return FakeHttpResponse({"status": "ok", "data": []})

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient().list_assistant_providers(auth_probe=True)

    assert result["status"] == "ok"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers?auth_probe=true"
    )
    assert recorded["timeout"] == 12.5


def test_openclaw_client_starts_provider_reauth_device_flow(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    monkeypatch.setenv("PANTHEON_ASSISTANT_REAUTH_TIMEOUT_SECONDS", "4.0")
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
                    "reauth_session_id": "codex_reauth_1",
                    "status": "pending",
                    "verification_uri": "https://auth.openai.com/device",
                    "user_code": "ABCD-EFGH",
                },
            },
            status_code=202,
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).start_assistant_provider_reauth(
            provider="codex",
            payload={"reason": "expired"},
            operator_id="operator-1",
            trace_id="trace-reauth-1",
        )

    assert result["status"] == "ok"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/codex/reauth"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-1"
    assert recorded["headers"]["X-trace-id"] == "trace-reauth-1"
    assert recorded["body"] == {"reason": "expired", "provider": "codex"}
    assert recorded["timeout"] == 4.0


def test_openclaw_client_starts_claude_provider_reauth_device_flow(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    monkeypatch.setenv("PANTHEON_ASSISTANT_REAUTH_TIMEOUT_SECONDS", "4.0")
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
                    "reauth_session_id": "claude_reauth_1",
                    "provider": "claude",
                    "status": "pending",
                    "verification_uri": "https://claude.ai/login",
                    "user_code": None,
                },
            },
            status_code=202,
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).start_assistant_provider_reauth(
            provider="claude",
            payload={"reason": "expired"},
            operator_id="operator-1",
            trace_id="trace-claude-reauth-1",
        )

    assert result["status"] == "ok"
    assert result["data"]["reauth_session_id"] == "claude_reauth_1"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/claude/reauth"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-1"
    assert recorded["headers"]["X-trace-id"] == "trace-claude-reauth-1"
    assert recorded["body"] == {"reason": "expired", "provider": "claude"}
    assert recorded["timeout"] == 4.0


def test_openclaw_client_reads_provider_reauth_status(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["headers"] = dict(request.header_items())
        return FakeHttpResponse(
            {
                "status": "ok",
                "data": {
                    "reauth_session_id": "codex_reauth_1",
                    "status": "completed",
                    "readiness": {"ready": True},
                },
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).get_assistant_provider_reauth_status(
            provider="codex",
            session_id="codex_reauth_1",
            operator_id="operator-1",
        )

    assert result["data"]["status"] == "completed"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/codex/reauth/codex_reauth_1"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-1"


def test_openclaw_client_reads_claude_provider_reauth_status(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["headers"] = dict(request.header_items())
        return FakeHttpResponse(
            {
                "status": "ok",
                "data": {
                    "reauth_session_id": "claude_reauth_1",
                    "provider": "claude",
                    "status": "completed",
                    "readiness": {"ready": True},
                },
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).get_assistant_provider_reauth_status(
            provider="claude",
            session_id="claude_reauth_1",
            operator_id="operator-1",
        )

    assert result["data"]["status"] == "completed"
    assert result["data"]["provider"] == "claude"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/claude/reauth/claude_reauth_1"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-1"


def test_openclaw_client_submits_claude_provider_reauth_code(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    monkeypatch.setenv("PANTHEON_ASSISTANT_REAUTH_TIMEOUT_SECONDS", "4.0")
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
                    "reauth_session_id": "claude_reauth_1",
                    "provider": "claude",
                    "status": "code_submitted",
                    "code_submitted_at": "2026-07-01T00:00:00Z",
                },
            }
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).submit_assistant_provider_reauth_code(
            provider="claude",
            session_id="claude_reauth_1",
            code="claude-oauth-code-123",
            operator_id="operator-1",
            trace_id="trace-claude-code-1",
        )

    assert result["data"]["status"] == "code_submitted"
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers/claude"
        "/reauth/claude_reauth_1/code"
    )
    assert recorded["headers"]["X-operator-id"] == "operator-1"
    assert recorded["headers"]["X-trace-id"] == "trace-claude-code-1"
    assert recorded["headers"]["X-assistant-mode"] == "user"
    assert recorded["headers"]["X-operator-role"] == "operator"
    assert recorded["body"] == {
        "provider": "claude",
        "code": "claude-oauth-code-123",
        "mode": "user",
        "operator_role": "operator",
        "confirmed": True,
        "control_mode": {"active": False, "mode": "user", "activation_id": None},
    }
    assert recorded["timeout"] == 4.0


def test_openclaw_client_registers_assistant_provider_metadata(monkeypatch) -> None:
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
                    "provider": "gemini_cli",
                    "provider_name": "Gemini CLI",
                    "status": "registered",
                    "ready": False,
                },
            },
            status_code=201,
        )

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).register_assistant_provider(
            {
                "provider": "gemini_cli",
                "providerName": "Gemini CLI",
                "model": "gemini-2.5-pro",
                "mode": "kernel_debug",
                "operator_role": "operator",
            },
            operator_id="operator-1",
            trace_id="trace-provider-register-1",
        )

    assert result["status"] == "ok"
    assert recorded["url"] == "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/providers"
    assert recorded["headers"]["X-operator-id"] == "operator-1"
    assert recorded["headers"]["X-trace-id"] == "trace-provider-register-1"
    assert recorded["headers"]["X-operator-role"] == "operator"
    assert recorded["headers"]["X-assistant-mode"] == "kernel_debug"
    assert recorded["body"]["provider"] == "gemini_cli"
    assert recorded["body"]["model"] == "gemini-2.5-pro"


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
        assert "providerStatus" not in body["data"]
        assert body["data"]["provider_status"]["status"] == "disabled"
        assert body["data"]["provider_status"]["reason"] == "feature_disabled"
        assert body["data"]["provider_status"]["used"] is False
        assert "contextPack" not in body["data"]
        assert body["data"]["context_pack"]["mode"] == "user"
        assert body["data"]["context_pack"]["backend"]["management_nl"]["data"]["tenant_id"] == "tenant-alpha"
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
        assert body["data"]["provider_status"]["status"] == "completed"
        assert body["data"]["provider_status"]["used"] is True
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
        assert "sessionId" not in body["data"]
        assert body["data"]["session_id"] == "mgmt-multi-session"
        assert body["data"]["conversation"]["href"].endswith(
            "/bff/management/ai/conversations/mgmt-multi-session"
        )
        assert "trace_id=" not in body["data"]["conversation"]["href"]
        assert body["data"]["session"]["ttl_seconds"] >= 7 * 24 * 60 * 60
        assert "ttlSeconds" not in body["data"]["session"]

        management_context = fake.calls[0]["context_pack"]["backend"]["management_nl"]["data"]
        assert management_context["focus"] == "persona_fleet"
        assert management_context["conversation"]["source"] == "server"
        assert management_context["conversation"]["recent_turns"][0]["content"] == "Continue from the previous answer."
        assert management_context["conversation"]["client_hint"]["recent_turns"][0]["content"] == "What is unhealthy?"
        assert "recentTurns" not in management_context["conversation"]
        assert "clientHint" not in management_context["conversation"]
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
        assert body["data"]["control_mode"]["active"] is True
        assert body["data"]["control_mode"]["mode"] == "kernel_debug"
        assert "controlMode" not in body["data"]
        context_pack = body["data"]["context_pack"]
        assert context_pack["mode"] == "kernel_debug"
        assert "assistant.kernel.debug" in context_pack["actor"]["capabilities"]
        management_context = context_pack["backend"]["management_nl"]["data"]
        assert management_context["control_mode"]["active"] is True
        assert management_context["control_mode"]["mode"] == "kernel_debug"
        assert "controlMode" not in management_context
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_context_pack_excludes_development_orchestrator_status(
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
        assert "orchestrator_status" not in context_pack["backend"]
        source_ids = {
            source.get("sourceId") or source.get("source_id")
            for source in context_pack["sources"]
        }
        assert "orchestrator_status" not in source_ids
        assert "backend.orchestrator_status" not in fake.calls[0]["prompt"]
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
            lambda authorization=None, **_kwargs: _kernel_operator_identity(),
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
        assert body["data"]["control_mode"]["active"] is True
        assert body["data"]["provider_status"]["mode"] == "kernel_debug"
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
            lambda authorization=None, **_kwargs: _kernel_operator_identity(),
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
        assert body["data"]["status"] == "completed"
        assert body["data"]["lifecycle_status"] == "completed"
        assert "lifecycleStatus" not in body["data"]
        assert body["meta"]["status"] == "completed"
        assert body["data"]["question"] == "[CONTROL MODE COMMAND REDACTED]"
        assert body["data"]["control_mode"]["active"] is True
        assert body["data"]["control_mode"]["mode"] == "kernel_debug"
        assert body["data"]["control_command"] == "activate"
        assert body["data"]["context_pack"] is None
        provider_status = body["data"]["provider_status"]
        assert provider_status["provider"] == "pantheon_bff"
        assert provider_status["runtime"] == "management_nl_control_command_interceptor"
        assert provider_status["used"] is True
        assert fake.calls == []
        sse_events = [event for _, event in bff_main._sse_buffers["ask"]]
        event_types = [event.get("type") for event in sse_events]
        assert "ask.message.completed" in event_types
        assert "management.nl.ask.completed" in event_types
        domain_completed = next(
            event for event in sse_events if event.get("type") == "management.nl.ask.completed"
        )
        assert domain_completed["data"]["status"] == "completed"
        assert domain_completed["data"]["control_command"] == "activate"
        assert "controlCommand" not in domain_completed["data"]
        assert domain_completed["data"]["conversation"]["href"].endswith("/mgmt-chat-control-session")

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
            lambda authorization=None, **_kwargs: _kernel_operator_identity(),
        )
        client = _seeded_client(tmp_path, monkeypatch)

        activate_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": f"控制模式：{passphrase}", "sessionId": "mgmt-chat-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-prefix"},
        )
        assert activate_resp.status_code == 202, activate_resp.text
        assert activate_resp.json()["data"]["control_mode"]["active"] is True

        status_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "/control status", "sessionId": "mgmt-chat-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-status"},
        )
        assert status_resp.status_code == 202, status_resp.text
        assert status_resp.json()["data"]["control_mode"]["active"] is True
        assert status_resp.json()["data"]["control_command"] == "status"

        off_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "/control off", "sessionId": "mgmt-chat-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-control-off"},
        )
        assert off_resp.status_code == 202, off_resp.text
        assert off_resp.json()["data"]["control_mode"]["active"] is False
        assert off_resp.json()["data"]["control_command"] == "deactivate"
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



def test_management_nl_stream_control_status_uses_bff_interceptor(tmp_path, monkeypatch) -> None:
    original_store = bff_main.read_store
    original_control_store = bff_main._ASSISTANT_CONTROL_MODE_STORE
    control_store = ControlModeStore(storage_path="off", initial_passphrase="九條好漢在一班")
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "_ASSISTANT_CONTROL_MODE_STORE", control_store)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask/stream",
            json={"question": "/control status", "sessionId": "mgmt-chat-stream-control-status"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-stream-control-status"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.text
        assert '"type": "delta"' in body
        assert "Control mode is inactive for this Management AI session." in body
        assert '"type": "done"' in body
        assert "management_nl_control_command_interceptor" in body
        assert "data: [DONE]" in body

        audit_resp = client.get(
            "/bff/management/ai/audit?session_id=mgmt-chat-stream-control-status",
            headers=OPERATOR_HEADERS,
        )
        assert audit_resp.status_code == 200, audit_resp.text
        audit_body = audit_resp.json()
        assert "items" not in audit_body
        events = audit_body["data"]["items"]
        assert any(event.get("control_command") == "status" for event in events)
    finally:
        bff_main.read_store = original_store
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_stream_records_openclaw_provider_audit_and_usage(tmp_path, monkeypatch) -> None:
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()

        resp = client.post(
            "/bff/management/nl/ask/stream",
            json={
                "question": "Stream provider audit check",
                "sessionId": "mgmt-stream-provider-audit-session",
                "traceId": "mgmt-stream-provider-audit-trace",
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-stream-provider-audit"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.text
        assert "Streamed provider answer." in body
        assert '"provider": "openclaw"' in body
        assert fake.calls and fake.calls[0]["stream"] is True

        audit_resp = client.get(
            "/bff/management/ai/audit?session_id=mgmt-stream-provider-audit-session",
            headers=OPERATOR_HEADERS,
        )
        assert audit_resp.status_code == 200, audit_resp.text
        audit_body = audit_resp.json()
        assert "items" not in audit_body
        events = audit_body["data"]["items"]
        provider_events = [event for event in events if event["event_type"].startswith("management_ai.provider.")]
        assert [event["event_type"] for event in provider_events] == [
            "management_ai.provider.started",
            "management_ai.provider.completed",
        ]
        assert provider_events[0]["provider"] == "openclaw"
        assert provider_events[0]["route"] == "POST /api/openclaw-adapter/assistant/providers/openclaw/invoke/stream"
        assert provider_events[1]["provider"] == "openclaw"
        assert provider_events[1]["output_summary"]["model"] == "openclaw/main"

        usage_resp = client.get(
            "/bff/assistant/providers/usage-summary?auth_probe=false&limit=50&window_hours=24",
            headers=OPERATOR_HEADERS,
        )
        assert usage_resp.status_code == 200, usage_resp.text
        rows = usage_resp.json()["data"]["providers"]
        openclaw = next(row for row in rows if row["provider"] == "openclaw")
        assert openclaw["calls"] == 1
        assert openclaw["success_count"] == 1
        assert "successCount" not in openclaw
        assert openclaw["observed_usage"]["source"] == "management_ai_bff_audit"
        assert "observedUsage" not in openclaw
        assert openclaw["models"][0]["model"] == "openclaw/main"
    finally:
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_stream_records_done_only_openclaw_answer(tmp_path, monkeypatch) -> None:
    fake = FakeProviderClient(
        stream_events=[
            {
                "type": "done",
                "text": "Done-only provider answer.",
                "elapsed_ms": 12,
                "transport": "responses_http",
            }
        ]
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()

        resp = client.post(
            "/bff/management/nl/ask/stream",
            json={
                "question": "Done-only stream audit check",
                "sessionId": "mgmt-stream-provider-done-only-session",
                "traceId": "mgmt-stream-provider-done-only-trace",
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-stream-provider-done-only"},
        )

        assert resp.status_code == 200, resp.text
        assert "Done-only provider answer." in resp.text

        audit_resp = client.get(
            "/bff/management/ai/audit?session_id=mgmt-stream-provider-done-only-session",
            headers=OPERATOR_HEADERS,
        )
        assert audit_resp.status_code == 200, audit_resp.text
        audit_body = audit_resp.json()
        assert "items" not in audit_body
        provider_events = [
            event
            for event in audit_body["data"]["items"]
            if event["event_type"].startswith("management_ai.provider.")
        ]
        assert [event["event_type"] for event in provider_events] == [
            "management_ai.provider.started",
            "management_ai.provider.completed",
        ]
        assert provider_events[1]["output_summary"]["output_bytes"] == len("Done-only provider answer.".encode("utf-8"))
    finally:
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
            lambda authorization=None, **_kwargs: _kernel_operator_identity(roles=["reviewer"]),
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
        assert body["data"]["provider_status"]["status"] == "completed"
        assert body["data"]["provider_status"]["used"] is True
        assert "providerStatus" not in body["data"]
        assert "reason" not in body["data"]["provider_status"]
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
        assert conversation["session_id"] == "mgmt-audit-session"
        assert "sessionId" not in conversation
        turns = conversation["turns"]
        assert [turn["role"] for turn in turns] == ["user", "assistant"]
        assert turns[0]["id"] == turns[0]["message_id"]
        assert turns[0]["text"] == "What is the scoped portfolio?"
        assert turns[0]["created_at"]
        assert "createdAt" not in turns[0]
        assert turns[0]["content"] == "What is the scoped portfolio?"
        assert turns[1]["text"] == "Audited provider answer."
        assert turns[1]["content"] == "Audited provider answer."
        assert turns[1]["provider_status"]["used"] is True
        assert "providerStatus" not in turns[1]

        audit_resp = client.get(
            "/bff/management/ai/audit?session_id=mgmt-audit-session&trace_id=mgmt-audit-trace",
            headers=OPERATOR_HEADERS,
        )
        assert audit_resp.status_code == 200, audit_resp.text
        audit_body = audit_resp.json()
        assert "items" not in audit_body
        events = audit_body["data"]["items"]
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


def test_assistant_provider_usage_summary_aggregates_history_and_quota(tmp_path, monkeypatch) -> None:
    fake = FakeProviderClient()
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
    client = _seeded_client(tmp_path, monkeypatch)
    original_provider_list = bff_main._assistant_provider_list

    def provider_list_with_openclaw(*, auth_probe=False):
        payload = original_provider_list(auth_probe=auth_probe)
        payload["data"].append(
            {
                "provider": "openclaw",
                "provider_name": "OpenClaw",
                "runtime": "openclaw_gateway_agent_cli",
                "ready": True,
                "status": "ready",
                "auth_status": "ready",
            }
        )
        return payload

    monkeypatch.setattr(bff_main, "_assistant_provider_list", provider_list_with_openclaw)
    bff_main._MGMT_AI_AUDIT_EVENTS.clear()
    now = bff_main.datetime.now(bff_main.timezone.utc).replace(microsecond=0)
    started_at = (now - bff_main.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    completed_at = (now - bff_main.timedelta(minutes=4, seconds=57)).isoformat().replace("+00:00", "Z")
    failed_at = (now - bff_main.timedelta(minutes=4)).isoformat().replace("+00:00", "Z")

    bff_main._management_ai_record_event(
        {
            "event_type": "management_ai.provider.started",
            "recorded_at": started_at,
            "session_id": "usage-session",
            "message_id": "usage-message",
            "trace_id": "usage-trace",
            "provider_run_id": "usage-run-codex",
            "provider": "codex_cli",
            "mode": "user",
            "prompt_bytes": 1200,
        }
    )
    bff_main._management_ai_record_event(
        {
            "event_type": "management_ai.provider.completed",
            "recorded_at": completed_at,
            "session_id": "usage-session",
            "message_id": "usage-message",
            "trace_id": "usage-trace",
            "provider_run_id": "usage-run-codex",
            "provider": "codex_cli",
            "provider_state": "completed",
            "duration_ms": 2500,
            "output_summary": {
                "model": "gpt-5-codex",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                },
            },
        }
    )
    bff_main._management_ai_record_event(
        {
            "event_type": "management_ai.provider.failed",
            "recorded_at": failed_at,
            "session_id": "usage-session",
            "message_id": "usage-message-2",
            "trace_id": "usage-trace-2",
            "provider_run_id": "usage-run-claude",
            "provider": "claude",
            "duration_ms": 900,
            "error_code": "CLAUDE_AUTH_UNAVAILABLE",
        }
    )

    resp = client.get(
        "/bff/assistant/providers/usage-summary?auth_probe=true&limit=50&window_hours=24",
        headers=OPERATOR_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    data = body["data"]
    assert data["totals"]["calls"] == 2
    assert data["totals"]["success_count"] == 1
    assert data["totals"]["failed_count"] == 1
    assert "successCount" not in data["totals"]
    assert "failedCount" not in data["totals"]
    providers = {row["provider"]: row for row in data["providers"]}
    codex = providers["codex_cli"]
    assert codex["live_auth"] is True
    assert codex["provider_auth"]["authenticated"] is True
    assert codex["live_smoke"]["status"] == "not_checked"
    assert codex["readiness"]["mount_ready_is_sufficient"] is False
    assert codex["reauth"]["status"] == "not_started"
    assert codex["persona_dependencies"] == {
        "status": "unavailable",
        "count": None,
        "personas": [],
        "source": None,
        "reason": "persona_dependency_inventory_unavailable",
    }
    assert "liveAuth" not in codex
    assert codex["quota"]["source"] == "provider_snapshot"
    assert codex["quota"]["remaining"] == 12
    assert codex["quota"]["used"] == 38
    assert codex["observed_usage"]["source"] == "management_ai_bff_audit"
    assert codex["observed_usage"]["coverage"] == "bff_observed_management_ai_only"
    assert codex["observed_usage"]["stale"] is False
    assert codex["observed_usage"]["total_tokens"] == 14
    assert "observedUsage" not in codex
    assert codex["models"][0]["model"] == "gpt-5-codex"
    assert codex["models"][0]["input_tokens"] == 10
    claude = providers["claude"]
    assert claude["live_auth"] is False
    assert claude["provider_auth"]["authenticated"] is False
    assert claude["live_smoke"]["status"] == "not_checked"
    assert claude["readiness"]["mount_ready_is_sufficient"] is False
    assert claude["reauth"]["status"] == "not_started"
    assert claude["persona_dependencies"]["reason"] == "persona_dependency_inventory_unavailable"
    assert claude["failed_count"] == 1
    assert claude["quota"]["source"] == "not_configured"
    openclaw = providers["openclaw"]
    assert openclaw["provider_auth"]["status"] == "ready"
    assert openclaw["live_smoke"]["status"] == "not_checked"
    assert openclaw["readiness"]["mount_ready_is_sufficient"] is False
    assert openclaw["reauth"]["status"] == "not_started"
    assert openclaw["persona_dependencies"]["reason"] == "persona_dependency_inventory_unavailable"
    assert data["quota"]["missing_source_means"] == "quota remaining is unknown, not zero"
    assert data["usage"]["truth_policy"] == "observed_bff_events_only"
    assert "missingSourceMeans" not in data["quota"]
    assert "truthPolicy" not in data["usage"]


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
        assert body["data"]["session_id"] == "mgmt-full-session"
        assert "sessionId" not in body["data"]
        assert body["meta"]["filters"]["trace_id_ignored"] is True
        turns = body["data"]["turns"]
        assert [turn["role"] for turn in turns] == ["user", "assistant", "user", "assistant"]
        assert [turn["text"] for turn in turns] == [
            "Question 1?",
            "Threaded provider answer.",
            "Question 2?",
            "Threaded provider answer.",
        ]
        assert turns[1]["provider_status"]["provider"] == "codex_cli"
        assert turns[1]["actions"] == []
        assert body["data"]["session"]["ttl_seconds"] >= 7 * 24 * 60 * 60

        list_resp = client.get("/bff/management/ai/conversations", headers=OPERATOR_HEADERS)
        assert list_resp.status_code == 200, list_resp.text
        list_body = list_resp.json()
        assert "items" not in list_body
        sessions = list_body["data"]["items"]
        assert sessions[0]["session_id"] == "mgmt-full-session"
        assert sessions[0]["href"] == "/bff/management/ai/conversations/mgmt-full-session"
        assert sessions[0]["turn_count"] == 4
        assert "sessionId" not in sessions[0]
        assert "turnCount" not in sessions[0]
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
        assert last_management_context["conversation"]["history_source"] == "management_ai_store"
        assert last_management_context["conversation"]["history_char_budget"] > 32 * 1024
        assert len(last_management_context["conversation"]["recent_turns"]) == 59
        assert last_management_context["conversation"]["recent_turns"][0]["content"] == "Persistence question 1?"
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
        assert [turn["content"] for turn in conversation["recent_turns"]] == [
            "Server history question 1?",
            "Provider grounded management answer.",
            "Server history question 2?",
            "Provider grounded management answer.",
            "Server history question 3?",
        ]
        assert conversation["history_source"] == "management_ai_store"
        assert conversation["stored_turn_count"] == 5
        assert conversation["history_truncated"] is False
        assert conversation["history_char_budget"] > 32 * 1024
        prompt = fake.calls[-1]["prompt"]
        assert "Server-side conversation history JSON" in prompt
        assert "Server history question 1?" in prompt
        assert "Only the latest FE-window hint remains." in prompt
        assert conversation["client_hint"]["recent_turns"] == [
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

    assert budget["history_char_budget"] > 32 * 1024
    assert budget["history_truncated"] is True
    assert budget["history_omitted_turn_count"] > 0
    assert budget["history_estimated_chars"] <= budget["history_char_budget"]
    assert windowed[-1]["id"] == "turn-39"
    assert [turn["created_at"] for turn in windowed] == sorted(turn["created_at"] for turn in windowed)


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


def test_management_ai_idempotency_replay_survives_store_restart_without_duplicate_turns(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = bff_main.read_store
    fake = FakeProviderClient()
    conversation_path = str(tmp_path / "management-ai-conversations.json")
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)
        bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
            storage_path=conversation_path,
            attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
        )
        payload = {
            "question": "Will restart replay preserve one correlated assistant turn?",
            "focus": "portfolio",
            "sessionId": "mgmt-restart-replay",
            "traceId": "mnl-restart-correlation",
        }
        headers = {**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-restart-replay"}

        first = client.post("/bff/management/nl/ask", json=payload, headers=headers)
        assert first.status_code == 202, first.text

        # Reconstruct the durable store and clear only the process-local cache,
        # mirroring a BFF restart between the original request and its replay.
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
            storage_path=conversation_path,
            attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
        )
        replay = client.post("/bff/management/nl/ask", json=payload, headers=headers)

        assert replay.status_code == 202, replay.text
        assert replay.json() == first.json()
        turns = bff_main._management_ai_conversation_store().list_turns("mgmt-restart-replay")
        assert [turn["role"] for turn in turns] == ["user", "assistant"]
        assert [turn["trace_id"] for turn in turns] == [
            "mnl-restart-correlation",
            "mnl-restart-correlation",
        ]
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
            "attachment_id": stored_attachment["id"],
            "kind": "image",
            "mime_type": "image/png",
            "filename": "screen.png",
            "size_bytes": len(image_bytes),
            "url": f"/bff/management/ai/attachments/{stored_attachment['id']}",
        }
        assert "attachmentId" not in attachment
        assert "mimeType" not in attachment
        assert "sizeBytes" not in attachment
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
        provider_status = body["data"]["provider_status"]
        assert "providerStatus" not in body["data"]
        assert provider_status["status"] == "degraded"
        assert provider_status["reason"] == "OPENCLAW_ADAPTER_UNREACHABLE"
        assert provider_status["fallback"] == "deterministic_synthesis"
        assert provider_status["used"] is False
        assert len(fake.calls) == 1
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_inner_degraded_response_uses_configured_provider_failover(
    tmp_path,
    monkeypatch,
) -> None:
    class FailoverProviderClient(FakeProviderClient):
        def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if kwargs["provider"] == "openclaw":
                return {
                    "status": "ok",
                    "data": {
                        "provider": "openclaw",
                        "status": "degraded",
                        "output": {
                            "reason": "CLAUDE_AUTH_UNAVAILABLE",
                            "message": "Claude service-user session expired.",
                        },
                    },
                }
            return {
                "status": "ok",
                "data": {
                    "provider": "codex_cli",
                    "status": "completed",
                    "output": {"json_events": [{"final": "Fallback provider answer."}]},
                },
            }

    original_store = bff_main.read_store
    fake = FailoverProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "openclaw")
        monkeypatch.setenv(
            "PANTHEON_MANAGEMENT_NL_ASSISTANT_FALLBACK_PROVIDERS",
            "codex_cli",
        )
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_PROVIDER_DEADLINE_SECONDS", "7")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-provider-failover"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"] == "Fallback provider answer."
        status = body["data"]["provider_status"]
        assert status["provider"] == "codex_cli"
        assert status["used"] is True
        assert status["fallback"] == "provider_failover"
        assert status["fallback_from"] == "openclaw"
        assert status["fallback_reason"] == "CLAUDE_AUTH_UNAVAILABLE"
        assert [item["provider"] for item in status["attempted_providers"]] == [
            "openclaw",
            "codex_cli",
        ]
        assert status["deadline_seconds"] == 7.0
        assert [call["provider"] for call in fake.calls] == ["openclaw", "codex_cli"]
        assert all(0 < call["timeout_seconds"] <= 7 for call in fake.calls)
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_inner_degraded_response_is_typed_not_an_answer(tmp_path, monkeypatch) -> None:
    class DegradedProviderClient(FakeProviderClient):
        def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "status": "ok",
                "data": {
                    "provider": "openclaw",
                    "status": "degraded",
                    "output": {
                        "reason": "CLAUDE_AUTH_UNAVAILABLE",
                        "message": "Claude service-user session expired.",
                    },
                },
            }

    original_store = bff_main.read_store
    fake = DegradedProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "openclaw")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-inner-degraded"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["answer"].startswith("Management summary for question:")
        assert body["data"]["answer"] != "Claude service-user session expired."
        status = body["data"]["provider_status"]
        assert status["status"] == "degraded"
        assert status["reason"] == "CLAUDE_AUTH_UNAVAILABLE"
        assert status["used"] is False
        assert status["fallback"] == "deterministic_synthesis"
        assert status["attempted_providers"] == [
            {
                "provider": "openclaw",
                "status": "degraded",
                "used": False,
                "reason": "CLAUDE_AUTH_UNAVAILABLE",
                "run_id": status["run_id"],
            }
        ]
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
        provider_status = body["data"]["provider_status"]
        assert provider_status["status"] == "degraded"
        assert provider_status["reason"] == "CODEX_AUTH_UNAVAILABLE"
        assert provider_status["reason_code"] == "CODEX_AUTH_UNAVAILABLE"
        assert provider_status["severity"] == "warning"
        assert "reasonCode" not in provider_status
        assert "Codex service-user session expired" in provider_status["display_message"]
        assert "displayMessage" not in provider_status
        assert provider_status["operator_action"] == "reauth_codex_service_user"
        assert "operatorAction" not in provider_status
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
        provider_status = body["data"]["provider_status"]
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
        provider_status = body["data"]["provider_status"]
        assert provider_status["status"] == "degraded"
        assert provider_status["reason"] == "CLAUDE_BINARY_NOT_FOUND"
        assert provider_status["fallback"] == "deterministic_synthesis"
        assert provider_status["used"] is False
        assert len(fake.calls) == 1
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_async_returns_processing_under_slow_provider(tmp_path, monkeypatch) -> None:
    import time as _time

    class SlowProviderClient(FakeProviderClient):
        def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
            _time.sleep(1.5)
            return super().invoke_assistant_provider(**kwargs)

    original_store = bff_main.read_store
    fake = SlowProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        # Inline grace window well below the provider latency: the handler must
        # return 202 immediately with the deterministic answer instead of blocking
        # the event loop until the slow agent finishes.
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS", "0.2")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "What is the scoped portfolio?", "focus": "portfolio"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-async-001"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["status"] == "processing"
        assert body["data"]["lifecycle_status"] == "processing"
        assert "lifecycleStatus" not in body["data"]
        assert body["data"]["provider_status"]["status"] == "processing"
        assert body["data"]["provider_status"]["used"] is False
        assert "providerStatus" not in body["data"]
        # Deterministic answer is served immediately; the provider answer is
        # finalised asynchronously by the background task.
        assert body["data"]["answer"].startswith("Management summary for question:")
        assert body["meta"]["status"] == "processing"
    finally:
        bff_main.read_store = original_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()


def test_mgmt_nl_finalize_provider_turn_appends_assistant_turn_and_idempotency(tmp_path, monkeypatch) -> None:
    import asyncio as _asyncio

    _clear_provider_env(monkeypatch)
    client = _seeded_client(tmp_path, monkeypatch)  # seeds env + fresh conversation store
    store = bff_main._management_ai_conversation_store()
    session_id = "mgmt-nl-finalize-session"
    message_id = "mnl-finalize-001"
    assistant_turn_id = f"{message_id}-assistant"
    # Seed the session (the handler creates it before writing turns).
    store.upsert_session(
        session_id=session_id,
        owner_id="asst-bff-002",
        tenant_id="tenant-alpha",
        now=bff_main.utc_now(),
        title="finalize test",
    )
    store.append_turn(
        turn_id=f"{message_id}-user",
        session_id=session_id,
        role="user",
        text="What is the scoped portfolio?",
        created_at=bff_main.utc_now(),
    )
    base_result = {
        "status": "accepted",
        "data": {"status": "processing", "lifecycle_status": "processing", "answer": "deterministic"},
        "meta": {"status": "processing", "idempotency": {"idempotencyKey": "k-fin", "replayed": False}},
    }
    provider_status = bff_main._mgmt_nl_provider_status(
        provider="codex_cli", enabled=True, status="completed", used=True
    )

    async def _provider_result():
        return ("Async provider answer.", provider_status, [])

    _asyncio.run(
        bff_main._mgmt_nl_finalize_provider_turn(
            provider_task=_provider_result(),
            deterministic_answer="deterministic",
            session_id=session_id,
            message_id=message_id,
            assistant_turn_id=assistant_turn_id,
            trace_id="mnl-trace-fin",
            focus="portfolio",
            resolved_key="k-fin",
            request_hash="hash-fin",
            audit_log_href="/bff/audit/x",
            conversation_href="/bff/management/ai/conversations/x",
            base_result=base_result,
        )
    )

    turns = store.list_turns(session_id)
    assistant = [t for t in turns if t.get("turn_id") == assistant_turn_id]
    assert len(assistant) == 1, "finaliser must append the assistant turn exactly once"
    assert assistant[0]["text"] == "Async provider answer."
    cached = store.get_idempotency("k-fin")
    assert cached is not None
    assert cached["result"]["data"]["answer"] == "Async provider answer."
    assert cached["result"]["data"]["status"] == "completed"
    assert cached["result"]["data"]["lifecycle_status"] == "completed"
    assert "lifecycleStatus" not in cached["result"]["data"]
    assert "providerStatus" not in cached["result"]["data"]
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
