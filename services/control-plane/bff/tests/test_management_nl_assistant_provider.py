from __future__ import annotations

import base64
import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.tests.rebalance_authority_test_support import (
    clear_management_nl_sse_buffer,
    get_management_nl_module,
    get_management_nl_read_store,
    get_management_nl_sse_buffer,
    set_management_nl_read_store,
)

# BFF-PM12-FIXTURE-CLOSURE-001: `bff_main` is bound to the real
# `assistant.management_service` module (get_management_nl_module() no longer
# loads main.py -- see rebalance_authority_test_support.py). main.py's own
# module proxy already delegates every one of these attributes onto
# management_service (BFF-MGMT-NL-HELPER-EXTRACTION-001), so every
# `bff_main.<attr>` access below reads/writes the exact real seam object.
# BFF-MGMT-NL-B6-SEAM-MIGRATION-001 additionally extracted the persona-fleet
# projection and the management-ai route handlers (including
# `bff_assistant_provider_usage_summary`/`_assistant_provider_list`) onto
# this same module, so this file no longer needs a direct main.py import at
# all -- see `_seeded_client()` and `test_assistant_provider_usage_summary_
# aggregates_history_and_quota` below.
bff_main = get_management_nl_module()
from services.control_plane.bff.assistant.control_mode import ControlModeStore
from services.control_plane.bff.assistant.management_service import get_management_ai_context_service
from services.control_plane.bff.assistant.models import AssistantMode
from services.control_plane.bff.management_nl_command_idempotency import (
    ManagementNlCommandIdempotencyStore,
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandScope,
    ManagementNlCommandStorageError,
)
from services.control_plane.bff.management_ai_store import ManagementAiAttachmentStore
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    create_market_persona_projection_test_double,
)
from services.control_plane.bff import test_bff_promotion_review_governance as gov_test


@pytest.fixture(autouse=True)
def _management_nl_command_idempotency_default_path(monkeypatch, tmp_path):
    """BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: durable admission via
    ManagementNlCommandIdempotencyStore is unconditional for both nl/ask
    transports; give it a writable per-test default path since the module
    default (/data/bff/...) does not exist in the test sandbox."""
    if not os.environ.get("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH"):
        monkeypatch.setenv(
            "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
            str(tmp_path / "management-nl-command-idempotency.json"),
        )


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
        "personas": {},
        "persona_bindings": {},
        "agora_audit_events": {},
        "agora_sessions": {},
    }

    # BFF-PM12-FIXTURE-CLOSURE-001: reuse the canonical eligible-persona
    # builder (test_bff_promotion_review_governance.py::
    # build_pm12_eligible_persona_records, proven against the real
    # production pipeline by test_pm12_eligibility_fixture_contract.py)
    # instead of hand-seeding inert persona/binding fields. persona-alpha
    # (tenant-alpha) genuinely clears every PM12 league/eligibility gate;
    # persona-beta (tenant-beta) does too, so cross-tenant admission has a
    # real eligible record to exclude rather than an inert one.
    pm12_alpha = gov_test.build_pm12_eligible_persona_records(
        "persona-alpha",
        "runtime-asst-alpha",
        "binding-asst-alpha",
        tenant_id="tenant-alpha",
    )
    pm12_beta = gov_test.build_pm12_eligible_persona_records(
        "persona-beta",
        "runtime-asst-beta",
        "binding-asst-beta",
        tenant_id="tenant-beta",
    )
    seeded_surfaces["personas"] = {
        "persona-alpha": pm12_alpha["personas"]["persona-alpha"],
        "persona-beta": pm12_beta["personas"]["persona-beta"],
    }
    seeded_surfaces["persona_bindings"] = {
        "binding-asst-alpha": pm12_alpha["bindings"]["binding-asst-alpha"],
        "binding-asst-beta": pm12_beta["bindings"]["binding-asst-beta"],
    }
    # `personas.service._pm12_persona_league_rows` reads
    # `read_store.list_runtime_bindings()` with no tenant argument at all
    # (tenant admission for PM12 is enforced only on the persona record
    # itself), so these runtime bindings must stay visible there
    # unfiltered. But this file's own `/bff/management/nl/ask` "portfolio"
    # snippet passes every runtime binding through
    # `_mgmt_nl_filter_tenant_records`, and
    # test_provider_enabled_invokes_openclaw_with_tenant_scoped_context
    # hard-codes `total_pnl == 2.5`, computed only from rt-alpha/rt-beta.
    # Tag the PM12 fixture's own runtime bindings with a tenant id neither
    # test tenant ever requests, so that unrelated nl/ask tenant filter
    # (which has no bearing on PM12 eligibility) excludes them from that
    # aggregate while `_pm12_persona_league_rows` still sees them in full.
    _PM12_FIXTURE_ONLY_TENANT_ID = "tenant-pm12-fixture-internal"
    persona_runtime_bindings = [
        {**pm12_alpha["runtime_bindings"]["runtime-asst-alpha"], "tenant_id": _PM12_FIXTURE_ONLY_TENANT_ID},
        {**pm12_beta["runtime_bindings"]["runtime-asst-beta"], "tenant_id": _PM12_FIXTURE_ONLY_TENANT_ID},
    ]
    persona_telemetry_summaries = {
        **pm12_alpha["telemetry_summaries"],
        **pm12_beta["telemetry_summaries"],
    }
    persona_sessions_by_persona = {
        "persona-alpha": [pm12_alpha["sessions"]["sess-persona-alpha"]],
        "persona-beta": [pm12_beta["sessions"]["sess-persona-beta"]],
    }

    _write_json(read_surface_path, seeded_surfaces)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_AI_AUDIT_PATH", str(tmp_path / "management-ai-audit.jsonl"))
    all_telemetry_summaries = dict(seeded_surfaces["telemetry_summaries"])
    all_telemetry_summaries.update(persona_telemetry_summaries)
    store = create_market_persona_projection_test_double(
        persona_capital_runtime_kwargs={
            "capital_pools": list(seeded_surfaces["capital_pools"].values()),
            "runtime_bindings": list(seeded_surfaces["runtime_bindings"].values()) + persona_runtime_bindings,
            "personas": list(seeded_surfaces["personas"].values()),
            "bindings": list(seeded_surfaces["persona_bindings"].values()),
        },
        lifecycle_telemetry_governance_kwargs={
            "telemetry_summaries": all_telemetry_summaries,
        },
    )
    store._data = json.loads(json.dumps(seeded_surfaces))
    store.get_agora_session = lambda session_id: store._data["agora_sessions"].get(session_id)
    store.get_sessions_for_persona = lambda persona_id: json.loads(
        json.dumps(persona_sessions_by_persona.get(str(persona_id), []))
    )
    # create_market_persona_projection_test_double() re-wraps the composite
    # ports without carrying over create_in_memory_read_surface_ports()'s
    # default empty paper-fleet-monitoring provider, so an unconfigured
    # PANTHEON_PAPER_FLEET_RECONCILER_URL would otherwise raise instead of
    # falling back to the persona-session-store path PM12 eligibility uses.
    store.list_authoritative_paper_runtime_monitoring_sessions = lambda: []
    set_management_nl_read_store(store)
    bff_main._MGMT_AI_AUDIT_EVENTS.clear()
    bff_main._sse_buffers["ask"].clear()
    bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
        storage_path="off",
        attachment_store=ManagementAiAttachmentStore(storage_path="off"),
    )
    from services.control_plane.bff.tests.rebalance_authority_test_support import get_management_nl_app
    return TestClient(get_management_nl_app(), raise_server_exceptions=False)


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
    # BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: durable command admission is
    # unconditional now (no more REQUIRED bypass flag); this helper only
    # needs to point the store at a fast, isolated per-test config.
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
        attachment_store=ManagementAiAttachmentStore(storage_path="off"),
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
        attachment_store=ManagementAiAttachmentStore(storage_path="off"),
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
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


def test_management_nl_concurrent_exact_request_invokes_provider_once_and_replays_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
        bff_main._MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


def test_management_nl_concurrent_conflict_returns_409_before_second_side_effect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
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
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_provider_enabled_invokes_openclaw_with_tenant_scoped_context(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        # The Management AI context no longer carries a second, hardcoded
        # persona_health source: persona health reaches the provider only
        # through persona_fleet, which keeps the same foreign tenant exclusion.
        assert call["context_pack"]["backend"].get("persona_health") is None
        source_ids = {source["source_id"] for source in call["context_pack"]["sources"]}
        assert "persona_health" not in source_ids

        fleet_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "How is the persona fleet?", "focus": "persona_fleet"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "asst-bff-002-enabled-fleet"},
        )
        assert fleet_resp.status_code == 202, fleet_resp.text
        fleet_context = fake.calls[1]["context_pack"]["backend"]["management_nl"]["data"][
            "summary_context"
        ]["persona_fleet"]
        fleet_persona_ids = {item["persona_id"] for item in fleet_context["items"]}
        assert "persona-alpha" in fleet_persona_ids
        assert "persona-beta" not in fleet_persona_ids
        assert "persona-beta" not in json.dumps(fleet_context)
    finally:
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_persona_fleet_summary_includes_health_items(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_passes_conversation_and_ui_context_to_provider(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_context_pack_reflects_active_control_mode(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_context_pack_excludes_development_orchestrator_status(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_provider_uses_active_kernel_debug_mode(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_direct_passphrase_activates_control_mode_without_provider_or_secret_readback(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_explicit_control_status_and_off_are_redacted(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()



def test_management_nl_stream_control_status_uses_bff_interceptor(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
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
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


@pytest.mark.parametrize("wire_format", ["structured_done", "json_done", "json_deltas"])
def test_management_nl_stream_preserves_filtered_actions_after_durable_reload(
    tmp_path, monkeypatch, wire_format
) -> None:
    answer = "Review this paper-only proposal; it requires your confirmation."
    raw_actions = [
        {
            "id": "paper-pause", "kind": "runBffAction", "label": "Pause paper runtime",
            "requiresConfirmation": False,
            "params": {
                "actionId": "PausePaperRuntime", "entityType": "Runtime", "entityId": "rt-stream-test",
                "payload": {"runtime_id": "rt-stream-test", "reason": "operator_requested_pause"},
            },
        },
        {"id": "disallowed", "kind": "openDrawer", "params": {"drawer": "runtime"}},
        {"id": "invalid", "kind": "runBffAction", "params": {"endpoint": "https://example.invalid"}},
        {"id": "missing-runtime", "kind": "runBffAction", "params": {"entityType": "Runtime", "actionId": "PausePaperRuntime"}},
    ]
    structured = {"answer": answer, "actions": raw_actions}
    encoded = json.dumps(structured)
    if wire_format == "structured_done":
        events = [{"type": "delta", "text": "Partial answer"}, {"type": "done", "text": answer, "actions": raw_actions}]
    elif wire_format == "json_done":
        events = [{"type": "done", "text": encoded}]
    else:
        midpoint = len(encoded) // 2
        events = [{"type": "delta", "text": encoded[:midpoint]}, {"type": "delta", "text": encoded[midpoint:]},
                  {"type": "done", "text": encoded}]
    fake = FakeProviderClient(stream_events=events)
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
    client = _seeded_client(tmp_path, monkeypatch)
    store_path = str(tmp_path / "stream-conversation.json")
    def fresh_store():
        return bff_main.ManagementAiConversationStore(
            storage_path=store_path,
            attachment_store=ManagementAiAttachmentStore(storage_path="off"),
        )
    monkeypatch.setattr(bff_main, "_MGMT_AI_CONVERSATION_STORE", fresh_store())
    response = client.post(
        "/bff/management/nl/ask/stream",
        json={"question": "Explain this paper runtime proposal", "sessionId": "stream-actions",
              "ui": {"availableUiActions": [{"kind": "runBffAction"}]}},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": f"stream-actions-durable-reload-{wire_format}"},
    )
    assert response.status_code == 200, response.text
    frames = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    done = [event for event in frames if event.get("type") == "done"]
    assert len(done) == 1  # Never expose an unfiltered upstream terminal event.
    assert done[0]["text"] == answer
    actions = done[0]["ui_actions"]
    assert [action["id"] for action in actions] == ["paper-pause"]
    assert actions[0]["requiresConfirmation"] is True
    assert actions[0]["params"] == raw_actions[0]["params"]
    assert done[0]["provider_status"]["used"] is True

    monkeypatch.setattr(bff_main, "_MGMT_AI_CONVERSATION_STORE", fresh_store())
    readback = client.get("/bff/management/ai/conversations/stream-actions", headers=OPERATOR_HEADERS)
    assert readback.status_code == 200, readback.text
    turns = [turn for turn in readback.json()["data"]["turns"] if turn["role"] == "assistant"]
    assert len(turns) == 1
    assert turns[0]["text"] == answer
    assert turns[0]["ui_actions"] == actions


@pytest.mark.parametrize("failure", [False, True, "incomplete"])
def test_management_nl_stream_does_not_offer_unallowed_or_failed_actions(tmp_path, monkeypatch, failure) -> None:
    events = [{"type": "done", "text": "Provider proposal", "actions": [
        {"id": "proposal", "kind": "runBffAction", "params": {"endpoint": "/api/v1/operator/commands"}},
    ]}]
    if failure == "incomplete":
        events = [{"type": "delta", "text": json.dumps({"answer": "Incomplete proposal", "actions": events[0]["actions"]})}]
    elif failure:
        events.append({"type": "error", "message": "Provider stream failed", "error_code": "UPSTREAM_FAILED"})
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: FakeProviderClient(stream_events=events))
    client = _seeded_client(tmp_path, monkeypatch)
    response = client.post(
        "/bff/management/nl/ask/stream",
        json={"question": "Explain this proposal", "sessionId": "stream-no-action",
              "ui": {"availableUiActions": [{"kind": "runBffAction"}] if failure else []}},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": f"stream-no-action-{failure}"},
    )
    assert response.status_code == 200, response.text
    frames = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    done = [event for event in frames if event.get("type") == "done"]
    if failure:
        assert done == []
        assert not [turn for turn in bff_main._management_ai_conversation_store().list_turns("stream-no-action")
                    if turn["role"] == "assistant"]
    else:
        assert len(done) == 1
        assert done[0]["ui_actions"] == []


def test_management_nl_chat_control_command_requires_authorized_operator(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._ASSISTANT_CONTROL_MODE_STORE = original_control_store
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_filters_provider_actions_to_ui_allowlist(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_enabled_extracts_codex_item_completed_text(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_audit_records_exchange_and_provider_trace(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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

        assert get_management_nl_read_store().get_agora_session("mgmt-audit-session") is None

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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_assistant_provider_usage_summary_aggregates_history_and_quota(tmp_path, monkeypatch) -> None:
    """BFF-PM12-FIXTURE-CLOSURE-001: GET /bff/assistant/providers/usage-summary
    is now a live-wired default of ``compose_bff_app()`` (BFF-MGMT-NL-B6-SEAM-
    MIGRATION-001 moved the route handler and its ``_assistant_provider_list``
    collaborator onto ``assistant.management_service`` itself), so this test
    no longer needs to load main.py: the seam-composed app (``bff_main`` ==
    ``assistant.management_service``, reached via ``get_management_nl_app()``)
    reaches the exact same real implementation.
    """
    import datetime as _datetime

    from services.control_plane.bff.tests.rebalance_authority_test_support import (
        get_management_nl_app,
    )

    fake = FakeProviderClient()
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PANTHEON_MANAGEMENT_AI_AUDIT_PATH", str(tmp_path / "management-ai-audit.jsonl"))
    monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
    client = TestClient(get_management_nl_app(), raise_server_exceptions=False)
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
    now = _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0)
    started_at = (now - _datetime.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    completed_at = (now - _datetime.timedelta(minutes=4, seconds=57)).isoformat().replace("+00:00", "Z")
    failed_at = (now - _datetime.timedelta(minutes=4)).isoformat().replace("+00:00", "Z")

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
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_persists_30_messages_as_60_ordered_turns(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_uses_server_history_when_fe_recent_turns_are_truncated(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
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
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_idempotency_replay_survives_store_restart_without_duplicate_turns(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
            attachment_store=ManagementAiAttachmentStore(storage_path="off"),
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
        bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
            storage_path=conversation_path,
            attachment_store=ManagementAiAttachmentStore(storage_path="off"),
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_conversation_missing_session_returns_404(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_conversation_get_enforces_owner_or_tenant_scope(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_management_ai_inline_attachment_is_stored_and_read_back_as_proxy_url(
    tmp_path,
    monkeypatch,
) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_provider_degraded_falls_back_to_deterministic_answer(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
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

    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
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

    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_codex_auth_unavailable_status_has_operator_notice(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_provider_enabled_requires_read_role_before_invocation(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_high_risk_refusal_runs_before_provider_invocation(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
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
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._MGMT_AI_AUDIT_EVENTS.clear()
        bff_main._sse_buffers["ask"].clear()


def test_claude_provider_degraded_falls_back_to_deterministic_answer(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_provider_async_returns_processing_under_slow_provider(tmp_path, monkeypatch) -> None:
    import time as _time

    class SlowProviderClient(FakeProviderClient):
        def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
            _time.sleep(1.5)
            return super().invoke_assistant_provider(**kwargs)

    original_store = get_management_nl_read_store()
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
        set_management_nl_read_store(original_store)
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
            audit_log_href="/bff/audit/x",
            conversation_href="/bff/management/ai/conversations/x",
            base_result=base_result,
        )
    )

    turns = store.list_turns(session_id)
    assistant = [t for t in turns if t.get("turn_id") == assistant_turn_id]
    assert len(assistant) == 1, "finaliser must append the assistant turn exactly once"
    assert assistant[0]["text"] == "Async provider answer."
    # BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: the finaliser no longer dual-writes
    # to ManagementAiConversationStore's idempotency API -- durable command
    # completion (via command_reservation) is the sole persistence path, and
    # this call passed no reservation, so there is nothing further to assert
    # here beyond the assistant turn being appended exactly once.


def test_mgmt_nl_collect_context_all_bounds_telemetry_owner_reads_and_reuses_trading_pulse_snippet(
    tmp_path, monkeypatch
) -> None:
    original_store = get_management_nl_read_store()
    try:
        _clear_provider_env(monkeypatch)
        _seeded_client(tmp_path, monkeypatch)
        store = get_management_nl_read_store()

        base_bindings = store.list_runtime_bindings()
        extra_alpha_bindings = [
            {
                "binding_id": "rb-alpha-2",
                "runtime_id": "rt-alpha-2",
                "tenant_id": "tenant-alpha",
                "capital_pool_id": "pool-alpha",
                "deployment_stage": "paper",
                "status": "running",
            },
            {
                "binding_id": "rb-alpha-missing",
                "runtime_id": "rt-alpha-missing",
                "tenant_id": "tenant-alpha",
                "capital_pool_id": "pool-alpha",
                "deployment_stage": "paper",
                "status": "running",
            },
        ]
        base_telemetry = {
            str(row.get("runtime_id") or row.get("id")): dict(row)
            for row in store.list_telemetry_summaries()
            if isinstance(row, dict) and (row.get("runtime_id") or row.get("id"))
        }
        base_telemetry["rt-alpha-2"] = {
            "runtime_id": "rt-alpha-2",
            "tenant_id": "tenant-alpha",
            "pnl": 5.5,
            "fill_rate": 0.9,
            "total_trades": 6,
            "metrics": {"pnl": 5.5, "fill_rate": 0.9, "total_trades": 6},
            "collected_at": "2026-06-02T00:00:00Z",
        }
        base_telemetry["rt-alpha"] = {
            **base_telemetry["rt-alpha"],
            "tenant_id": "tenant-alpha",
        }
        base_telemetry["rt-beta"] = {
            **base_telemetry["rt-beta"],
            "tenant_id": "tenant-beta",
        }

        monitoring_sessions = [
            {
                "session_id": "mon-alpha-1",
                "runtime_id": "rt-alpha",
                "binding_id": "rb-alpha",
                "status": "running",
                "active": True,
                "started_at": "2026-06-01T23:00:00Z",
                "drift_detected": False,
                "owner": "monitoring-owner",
                "source_kind": "replayed",
                "source_version": "mon-v3",
                "observed_at": "2026-06-02T00:00:00Z",
                "correlation_id": "mon-corr-1",
            },
            {
                "session_id": "mon-alpha-2",
                "runtime_id": "rt-alpha-2",
                "binding_id": "rb-alpha-2",
                "status": "running",
                "active": True,
                "started_at": "2026-06-01T23:30:00Z",
                "drift_detected": False,
                "owner": "monitoring-owner",
                "source_kind": "replayed",
                "source_version": "mon-v3",
                "observed_at": "2026-06-02T00:00:00Z",
                "correlation_id": "mon-corr-2",
            },
        ]

        counts = {
            "list_telemetry_summaries": 0,
            "get_telemetry_summary": 0,
            "list_paper_runtime_monitoring_sessions": 0,
            "get_paper_runtime_monitoring_session": 0,
            "list_runtime_bindings": 0,
            "trading_pulse_snippet": 0,
        }
        projected_rows_batches: list[list[dict[str, Any]]] = []

        def counted_list_telemetry_summaries() -> list[dict[str, Any]]:
            counts["list_telemetry_summaries"] += 1
            return list(base_telemetry.values())

        def counted_get_telemetry_summary(runtime_id: str) -> dict[str, Any] | None:
            counts["get_telemetry_summary"] += 1
            return base_telemetry.get(runtime_id)

        def counted_list_monitoring_sessions() -> list[dict[str, Any]]:
            counts["list_paper_runtime_monitoring_sessions"] += 1
            return list(monitoring_sessions)

        def counted_get_monitoring_session(**kwargs: Any) -> dict[str, Any] | None:
            counts["get_paper_runtime_monitoring_session"] += 1
            return None

        def counted_list_runtime_bindings() -> list[dict[str, Any]]:
            counts["list_runtime_bindings"] += 1
            return list(base_bindings) + list(extra_alpha_bindings)

        orig_scoped_runtime_rows = bff_main._mgmt_nl_scoped_runtime_rows

        def SpyScopedRuntimeRows(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            rows = orig_scoped_runtime_rows(*args, **kwargs)
            projected_rows_batches.append(rows)
            return rows

        orig_trading_pulse_snippet = bff_main._mgmt_nl_trading_pulse_snippet

        def counted_trading_pulse_snippet(*args: Any, **kwargs: Any) -> dict[str, Any]:
            counts["trading_pulse_snippet"] += 1
            return orig_trading_pulse_snippet(*args, **kwargs)

        monkeypatch.setattr(store, "list_telemetry_summaries", counted_list_telemetry_summaries)
        monkeypatch.setattr(store, "get_telemetry_summary", counted_get_telemetry_summary)
        monkeypatch.setattr(
            store, "list_paper_runtime_monitoring_sessions", counted_list_monitoring_sessions
        )
        monkeypatch.setattr(
            store, "get_paper_runtime_monitoring_session", counted_get_monitoring_session
        )
        monkeypatch.setattr(store, "list_runtime_bindings", counted_list_runtime_bindings)
        monkeypatch.setattr(store, "get_rollbacks", lambda _runtime_id: [])
        monkeypatch.setattr(bff_main, "_mgmt_nl_scoped_runtime_rows", SpyScopedRuntimeRows)
        monkeypatch.setattr(bff_main, "_mgmt_nl_trading_pulse_snippet", counted_trading_pulse_snippet)
        monkeypatch.setattr(
            bff_main,
            "_build_operator_alerts_payload",
            lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}},
        )
        monkeypatch.setattr(
        get_management_ai_context_service(),
        "get_human_inbox",
            lambda *args, **kwargs: {"data": {"items": []}, "page_info": {"total": 0}, "meta": {"surfaces": {}}},
        )
        monkeypatch.setattr(
            bff_main,
            "_build_management_anomalies_payload",
            lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}},
        )

        context = bff_main._mgmt_nl_collect_context(
            "all", "2026-06-02T00:05:00Z", tenant_id="tenant-alpha"
        )

        assert counts["list_telemetry_summaries"] == 1
        assert counts["get_telemetry_summary"] == 0
        assert counts["list_paper_runtime_monitoring_sessions"] == 1
        assert counts["get_paper_runtime_monitoring_session"] == 0
        assert counts["list_runtime_bindings"] == 1
        assert counts["trading_pulse_snippet"] == 1

        assert len(projected_rows_batches) == 1
        rows_by_runtime = {row["runtime_id"]: row for row in projected_rows_batches[0]}
        assert rows_by_runtime["rt-alpha"]["paper_runtime_monitoring"] is not None
        assert (
            rows_by_runtime["rt-alpha"]["paper_runtime_monitoring"]["session_id"]
            == "mon-alpha-1"
        )
        assert rows_by_runtime["rt-alpha"]["monitoring_observation"]["owner"] == "monitoring-owner"
        assert (
            rows_by_runtime["rt-alpha"]["monitoring_observation"]["source_version"]
            == "mon-v3"
        )
        assert (
            rows_by_runtime["rt-alpha"]["monitoring_observation"]["correlation_id"]
            == "mon-corr-1"
        )
        assert (
            rows_by_runtime["rt-alpha-2"]["paper_runtime_monitoring"]["session_id"]
            == "mon-alpha-2"
        )

        assert (
            context["snippets"]["cockpit"]["trading_pulse_summary"]
            == context["snippets"]["trading_pulse"]["summary"]
        )
        assert context["snippets"]["portfolio"]["runtime_count"] == 3
        assert context["snippets"]["portfolio"]["total_pnl"] == 8.0

        # Missing telemetry on rt-alpha-missing must keep degraded status across surfaces
        assert context["surfaces"]["management_cockpit"]["status"] == "degraded"
        assert context["surfaces"]["management_trading_pulse"]["status"] == "degraded"
        assert context["surfaces"]["portfolio_book"]["status"] == "degraded"
        trading_pulse_obs = (
            context["surfaces"]["management_trading_pulse"]["owner_observation"][
                "contributing_observations"
            ]
        )
        mon_obs = [
            obs
            for obs in trading_pulse_obs
            if obs.get("subject_type") == "paper_runtime_monitoring"
            and obs.get("subject_id") == "rt-alpha"
        ]
        assert len(mon_obs) == 1
        assert mon_obs[0]["owner"] == "monitoring-owner"
        assert mon_obs[0]["source_version"] == "mon-v3"
        assert mon_obs[0]["correlation_id"] == "mon-corr-1"
        portfolio_obs = context["surfaces"]["portfolio_book"]["owner_observations"]
        missing_obs = [
            obs
            for obs in portfolio_obs
            if obs.get("subject_type") == "telemetry" and obs.get("subject_id") == "rt-alpha-missing"
        ]
        assert len(missing_obs) == 1
        assert missing_obs[0]["status"] == "degraded"
    finally:
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_mgmt_nl_collect_context_preserves_fail_closed_unavailable_when_telemetry_owner_raises(
    tmp_path, monkeypatch
) -> None:
    original_store = get_management_nl_read_store()
    try:
        _clear_provider_env(monkeypatch)
        _seeded_client(tmp_path, monkeypatch)
        store = get_management_nl_read_store()
        healthy_list_telemetry_summaries = store.list_telemetry_summaries

        calls = {
            "list_telemetry_summaries": 0,
            "list_paper_runtime_monitoring_sessions": 0,
        }

        def failing_list_telemetry_summaries() -> dict[str, Any]:
            calls["list_telemetry_summaries"] += 1
            raise RuntimeError("telemetry owner down")

        monkeypatch.setattr(store, "list_telemetry_summaries", failing_list_telemetry_summaries)
        monkeypatch.setattr(
            bff_main,
            "_build_operator_alerts_payload",
            lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}},
        )
        monkeypatch.setattr(
        get_management_ai_context_service(),
        "get_human_inbox",
            lambda *args, **kwargs: {"data": {"items": []}, "page_info": {"total": 0}, "meta": {"surfaces": {}}},
        )
        monkeypatch.setattr(
            bff_main,
            "_build_management_anomalies_payload",
            lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}},
        )

        context = bff_main._mgmt_nl_collect_context(
            "all", "2026-06-02T00:05:00Z", tenant_id="tenant-alpha"
        )

        assert calls["list_telemetry_summaries"] == 1
        assert context["surfaces"]["management_cockpit"]["status"] == "unavailable"
        assert context["surfaces"]["management_trading_pulse"]["status"] == "unavailable"
        assert context["surfaces"]["portfolio_book"]["status"] == "unavailable"
        assert context["surfaces"]["persona_fleet"]["status"] == "unavailable"

        def failing_list_monitoring_sessions() -> list[dict[str, Any]]:
            calls["list_paper_runtime_monitoring_sessions"] += 1
            raise RuntimeError("monitoring owner down")

        monkeypatch.setattr(
            store, "list_telemetry_summaries", healthy_list_telemetry_summaries
        )
        monkeypatch.setattr(
            store,
            "list_paper_runtime_monitoring_sessions",
            failing_list_monitoring_sessions,
        )
        monkeypatch.setattr(store, "get_rollbacks", lambda _runtime_id: [])

        monitoring_failed_context = bff_main._mgmt_nl_collect_context(
            "all", "2026-06-02T00:05:00Z", tenant_id="tenant-alpha"
        )

        assert calls["list_paper_runtime_monitoring_sessions"] == 1
        assert (
            monitoring_failed_context["surfaces"]["management_cockpit"]["status"]
            == "unavailable"
        )
        assert (
            monitoring_failed_context["surfaces"]["management_trading_pulse"]["status"]
            == "unavailable"
        )
        assert "paper runtime monitoring read failed: monitoring owner down" in json.dumps(
            monitoring_failed_context["surfaces"]["management_cockpit"]
        )
        assert "paper runtime monitoring read failed: monitoring owner down" in json.dumps(
            monitoring_failed_context["surfaces"]["management_trading_pulse"]
        )
    finally:
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()



def test_management_nl_large_fleet_context_stays_within_provider_bound(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
    fake = FakeProviderClient()
    provider_bound_chars = 200_000
    persona_count, runtime_count = 20, 40
    blob = {"candles": ["x" * 40] * 10}
    runtimes = [
        {"runtime_id": f"rt-{i}", "status": "running", "tenant_id": "tenant-alpha", "detail": blob}
        for i in range(runtime_count)
    ]
    ok_obs = [
        {"subject_type": "telemetry", "subject_id": f"rt-{i}", "status": "ok", "owner": "telemetry",
         "source_kind": "live", "contributing_observations": [{"subject_id": f"rt-{i}", "blob": blob}]}
        for i in range(runtime_count)
    ]
    bad_obs = {"subject_type": "bindings", "subject_id": "persona-7", "status": "unavailable",
               "owner": "runtime_manager", "source_kind": "unavailable", "degradation_reason": "owner down"}
    items = [
        {
            "id": f"persona-{i}", "persona_id": f"persona-{i}", "persona": {"name": f"P{i}", "traits": blob},
            "health": {"status": "healthy"}, "bindings": [],
            "runtimeBindings": runtimes, "runtime_bindings": runtimes,
            "telemetrySummary": {"runtime_count": runtime_count}, "telemetry_summary": {"runtime_count": runtime_count},
            "owner_observations": ok_obs,
        }
        for i in range(persona_count)
    ]
    snippets = {"persona_fleet": {"total": persona_count, "summary": {"total_personas": 500}, "items": items}}
    surfaces = {"persona_fleet": {"status": "unavailable", "source": "bff_composed", "owner_observations": [*ok_obs, bad_obs]}}
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)
        monkeypatch.setattr(
            bff_main, "_mgmt_nl_collect_context",
            lambda focus, now, tenant_id=None, **_kw: {"snippets": snippets, "surfaces": surfaces},
        )
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "hello", "focus": "persona_fleet"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "ctx-bound-large-fleet"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["data"]["provider_status"]["status"] == "completed"
        assert len(fake.calls) == 1
        pack = fake.calls[0]["context_pack"]
        assert len(json.dumps(snippets)) > provider_bound_chars // 2  # fixture is realistically large
        assert len(json.dumps(pack)) < provider_bound_chars
        assert pack["bff_reads"]["context"]["mirrors"] == "backend"
        assert "management_nl" in pack["bff_reads"]["context"]["source_keys"]
        data = pack["backend"]["management_nl"]["data"]
        fleet = data["summary_context"]["persona_fleet"]
        assert fleet["summary"]["total_personas"] == 500
        assert len(fleet["items"]) == persona_count
        scope = fleet["population_scope"]
        assert scope["partial"] is True
        assert scope["total_population"] == 500
        assert scope["returned_sample_count"] == persona_count
        assert scope["omitted_count"] == 500 - persona_count
        assert "healthy_personas" in scope["sample_scoped_summary_fields"]
        assert scope["whole_population_health_counts"] == "unavailable"
        assert fleet["items"][0]["runtime_bindings"]["count"] == runtime_count
        assert fleet["shared_rows"]["runtime_bindings"][0]["runtime_id"] == "rt-0"
        assert "runtimeBindings" not in fleet["items"][0]
        observations = data["surfaces"]["persona_fleet"]["owner_observations"]
        assert data["surfaces"]["persona_fleet"]["status"] == "unavailable"
        assert any(o["status"] == "unavailable" and o["degradation_reason"] == "owner down" for o in observations)
        grouped = [o for o in observations if o.get("count")]
        assert grouped and grouped[0]["count"] == runtime_count
    finally:
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_management_nl_residual_repeated_rows_and_merged_observations_stay_within_bound(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
    fake = FakeProviderClient()
    provider_bound_chars = 200_000
    persona_count, runtime_count = 20, 84

    def wide(prefix: str, i: int) -> dict:
        return {f"{prefix}_field_{n}": f"{prefix}-{i}-value-{n}-" + "v" * 24 for n in range(12)}

    runtimes = [
        {"runtime_id": f"rt-{i}", "status": "running", "tenant_id": "tenant-alpha", **wide("rt", i)}
        for i in range(runtime_count)
    ]
    summaries = [
        {"runtime_id": f"rt-{i}", "status": "ok", "tenant_id": "tenant-alpha", "metrics": {"pnl": i}, **wide("tm", i)}
        for i in range(runtime_count)
    ]
    decisions = [{"decision_id": f"dec-{i}", "status": "pending", **wide("dec", i)} for i in range(10)]
    reason = {"telemetry": "telemetry summary not found for this runtime.", "rollback": "rollback owner unavailable."}

    def real_obs(kind: str, i: int) -> dict:
        # Real ManagementService shape: owner defaults to the runtime id, so it
        # is unique per runtime; telemetry is degraded and rollback unavailable.
        status = {"telemetry": "degraded", "rollback": "unavailable"}.get(kind, "ok")
        return {
            "subject_type": kind, "subject_id": f"rt-{i}", "status": status, "owner": f"rt-{i}",
            "source_kind": "live" if status == "ok" else "unavailable", "source_version": None,
            "observed_at": f"2026-10-20T10:00:{i % 60:02d}Z", "freshness_seconds": None,
            "correlation_id": f"corr-{kind}-{i}", "degradation_reason": reason.get(kind),
            "contributing_observations": [],
        }

    ok_obs = [real_obs(kind, i) for i in range(runtime_count) for kind in ("telemetry", "monitoring", "rollback")]
    bad_obs = {"subject_type": "bindings", "subject_id": "persona-7", "status": "unavailable", "owner": "runtime_manager",
               "source_kind": "unavailable", "degradation_reason": "binding owner down", "contributing_observations": []}
    merged_obs = {
        "status": "unavailable", "owner": "rt-0", "source_kind": "unavailable",
        "degradation_reason": "; ".join(reason.values()), "contributing_observations": [*ok_obs, bad_obs],
    }
    items = [
        {
            "id": f"persona-{i}", "persona_id": f"persona-{i}", "persona": {"name": f"P{i}"},
            "health": {"status": "healthy"}, "bindings": [],
            "runtime_bindings": runtimes,
            "telemetry_summary": {"runtime_count": runtime_count, "latest": summaries[0], "summaries": summaries},
            "evolution": {"decision_count": len(decisions), "latest_decision": decisions[0], "decisions": decisions},
        }
        for i in range(persona_count)
    ]
    snippets = {"persona_fleet": {"total": persona_count, "summary": {"total_personas": persona_count}, "items": items}}
    surfaces = {
        "persona_fleet": {"status": "unavailable", "source": "bff_composed", "owner_observations": [*ok_obs, bad_obs]},
        "management_cockpit": {"status": "unavailable", "source": "bff_composed", "owner_observation": merged_obs},
        "management_trading_pulse": {"status": "unavailable", "source": "bff_composed", "owner_observation": merged_obs},
    }
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)
        monkeypatch.setattr(
            bff_main, "_mgmt_nl_collect_context",
            lambda focus, now, tenant_id=None, **_kw: {"snippets": snippets, "surfaces": surfaces},
        )
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "hello", "focus": "all"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "ctx-bound-residual-rows"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["data"]["provider_status"]["status"] == "completed"
        assert len(fake.calls) == 1
        pack = fake.calls[0]["context_pack"]
        assert len(json.dumps({"s": snippets, "u": surfaces})) > provider_bound_chars * 2
        assert len(json.dumps(pack)) < provider_bound_chars
        data = pack["backend"]["management_nl"]["data"]
        fleet = data["summary_context"]["persona_fleet"]
        assert len(fleet["items"]) == persona_count
        first = fleet["items"][0]
        shared = fleet["shared_rows"]
        assert first["runtime_bindings"]["shared_row_list"] == "runtime_bindings"
        assert first["runtime_bindings"]["count"] == runtime_count
        assert first["runtime_bindings"]["row_indexes"] == list(range(runtime_count))
        assert [r["runtime_id"] for r in shared["runtime_bindings"]] == [f"rt-{i}" for i in range(runtime_count)]
        assert all(r["tenant_id"] == "tenant-alpha" for r in shared["runtime_bindings"])
        assert first["telemetry_summary"]["summaries"]["count"] == runtime_count
        assert first["telemetry_summary"]["latest"] == {"shared_row_table": "telemetry_summary.latest", "shared_row": 0}
        assert first["telemetry_summary"]["runtime_count"] == runtime_count
        assert first["evolution"]["decisions"]["count"] == len(decisions)
        assert shared["evolution.decisions"][2]["decision_id"] == "dec-2"
        for name in ("management_cockpit", "management_trading_pulse"):
            merged = data["surfaces"][name]["owner_observation"]
            assert merged["status"] == "unavailable"
            assert "telemetry summary not found" in merged["degradation_reason"]
            assert merged["contributing_count"] == len(ok_obs) + 1
            contributing = merged["contributing_observations"]
            by_key = {(o["subject_type"], o["status"]): o for o in contributing}
            for kind, status in (("telemetry", "degraded"), ("rollback", "unavailable"), ("monitoring", "ok")):
                group = by_key[(kind, status)]
                assert group["count"] == runtime_count
                assert group["subject_ids"] == [f"rt-{i}" for i in range(runtime_count)]
                assert group["owners"] == group["subject_ids"]
            assert by_key[("telemetry", "degraded")]["degradation_reason"] == reason["telemetry"]
            assert by_key[("rollback", "unavailable")]["source_kind"] == "unavailable"
            assert by_key[("bindings", "unavailable")]["subject_id"] == "persona-7"
            assert sum(o.get("count", 1) for o in contributing) == len(ok_obs) + 1
        pf = data["surfaces"]["persona_fleet"]["owner_observations"]
        assert sum(o.get("count", 1) for o in pf) == len(ok_obs) + 1
        assert any(o["subject_type"] == "bindings" and o["degradation_reason"] == "binding owner down" for o in pf)
    finally:
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()


def test_mgmt_nl_ask_and_stream_deliver_loop_health_in_provider_prompt_summary_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both POST /bff/management/nl/ask and POST /bff/management/nl/ask/stream
    deliver the loop_health snippet to the provider inside
    backend.management_nl.data.summary_context and inside the provider prompt."""
    import importlib
    import sys
    from copy import deepcopy
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock
    from services.control_plane.bff import loop_inventory as loop_inventory_model

    original_store = get_management_nl_read_store()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    scoped_headers = {
        "Authorization": "Bearer asst-bff-002:operator:tenant-alpha",
        "X-Tenant-Id": "tenant-alpha",
    }

    registry = loop_inventory_model._load_registry()
    controller_rows: List[Dict[str, Any]] = []
    for idx, loop in enumerate(registry.get("loops") or []):
        loop_id = str(loop["loop_id"])
        contract = loop.get("controller_contract") or {}
        controller_name = str(contract.get("controller_name") or f"{loop_id}-controller")
        downstream_status = "ready" if idx % 2 == 0 else "ok"
        controller_rows.append(
            {
                "loop_id": loop_id,
                "tenant_id": "tenant-alpha",
                "environment": "dev",
                "controller_id": f"{loop_id}-controller-1",
                "controller_name": controller_name,
                "deployment_sha": "deadbeefcafe",
                "desired_state_query": f"desired state for {loop_id}",
                "actual_state_query": f"actual state for {loop_id}",
                "desired_state": {
                    "present": True,
                    "source": f"{loop_id}-desired-authority",
                    "checked_at": now,
                },
                "downstream_actual_state": {
                    "status": downstream_status,
                    "source": f"{loop_id}-terminal-store",
                    "summary": f"{loop_id} is {downstream_status}: " + ("漢" * 200),
                    "checked_at": now,
                },
                "last_heartbeat_at": now,
                "last_tick_at": now,
                "last_success_at": now,
                "last_failure_at": now - timedelta(minutes=5),
                "last_failure_reason": f"{loop_id} downstream timeout",
                "last_repair_at": now - timedelta(minutes=4),
                "last_repair_reason": f"{loop_id} drift repaired",
                "backlog": 0,
                "lag": 0,
                "dlq_count": 0,
                "evidence_refs": [
                    f"docs/deployment/evidence/twelve-loop-gap/L12-TRUTH-001/{loop_id}.json"
                ],
                "truth_level": "reconciled_live_proof",
                "lease_token": f"{loop_id}-fence-1",
                "lease_expires_at": now + timedelta(seconds=60),
                "payload": {"last_success_summary": f"{loop_id} reconciled desired and actual"},
            }
        )

    fake = FakeProviderClient(
        stream_events=[
            {"type": "delta", "text": "All 12 canonical control loops "},
            {"type": "done", "text": "All 12 canonical control loops are accepted and healthy."},
        ],
    )
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "openclaw")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)
        monkeypatch.setenv("PANTHEON_ENV", "dev")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://dummy_user:dummy_pass@localhost:5432/dummy_db",
        )
        monkeypatch.setitem(sys.modules, "asyncpg", MagicMock())
        loop_control = importlib.import_module("services.loop-control")

        async def mock_list_records(self, tenant_id: str, environment: str):
            return deepcopy(controller_rows)

        monkeypatch.setattr(loop_control.LoopControllerStore, "list_records", mock_list_records)

        ask_resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "Are the 12 loops healthy?", "focus": "all"},
            headers={**scoped_headers, "Idempotency-Key": "ik-provider-loop-health-ask"},
        )
        assert ask_resp.status_code == 202, ask_resp.text
        assert len(fake.calls) == 1
        ask_call = fake.calls[0]
        ask_pack = ask_call["context_pack"]
        ask_loop_snippet = ask_pack["backend"]["management_nl"]["data"]["summary_context"]["loop_health"]
        assert ask_loop_snippet["status"] == "ok"
        assert ask_loop_snippet["total"] == 12
        assert ask_loop_snippet["accepted"] == 12
        assert ask_loop_snippet["by_live_status"] == {"ready": 6, "ok": 6}
        assert len(ask_loop_snippet["items"]) == 12
        assert (
            len(json.dumps(ask_loop_snippet, sort_keys=True, ensure_ascii=True).encode("utf-8"))
            <= 8192
        )
        assert (
            len(json.dumps(ask_loop_snippet, ensure_ascii=False).encode("utf-8"))
            <= 8192
        )
        assert '"loop_health":' in ask_call["prompt"]
        assert '"source_ingestion"' in ask_call["prompt"]

        stream_resp = client.post(
            "/bff/management/nl/ask/stream",
            json={"question": "Are the 12 loops healthy?", "focus": "loop_health"},
            headers={**scoped_headers, "Idempotency-Key": "ik-provider-loop-health-stream"},
        )
        assert stream_resp.status_code == 200, stream_resp.text
        _ = stream_resp.text
        stream_calls = [c for c in fake.calls if c.get("stream")]
        assert len(stream_calls) == 1
        stream_call = stream_calls[0]
        stream_pack = stream_call["context_pack"]
        stream_loop_snippet = stream_pack["backend"]["management_nl"]["data"]["summary_context"]["loop_health"]
        assert stream_loop_snippet == ask_loop_snippet
        assert '"loop_health":' in stream_call["prompt"]
        assert '"source_ingestion"' in stream_call["prompt"]
    finally:
        set_management_nl_read_store(original_store)
        bff_main._sse_buffers["ask"].clear()



# ---------------------------------------------------------------------------
# BFF-AI-CURRENT-STATE-20261107: the Management AI context reports current
# state. Rows below use the real owner shapes read on 2026-11-07, not
# synthetic rows that carry fields the real rows lack.
# ---------------------------------------------------------------------------

_CS_NOW = "2026-11-07T00:00:00Z"
_CS_TENANT = "tenant-a"
_CS_INCIDENT_KEYS = (
    "artifact_id", "artifact_version", "binding_id", "capital_pool_id", "created_at",
    "deployment_plan_id", "deployment_stage", "evidence_summary", "incident_cluster_id",
    "incident_id", "lineage_ref", "persona_capital_binding_id", "reconciliation_ids",
    "resolved_at", "runtime_id", "severity", "status", "telemetry_event_ids", "title",
    "trace_id",
)


def _cs_incident(incident_id: str, status: str, **overrides: Any) -> dict[str, Any]:
    """An incident owner row: exactly the 20 keys, and no tenant key."""
    row: dict[str, Any] = {key: None for key in _CS_INCIDENT_KEYS}
    row.update(
        incident_id=incident_id,
        status=status,
        severity="critical" if status == "open" else "low",
        title=f"incident {incident_id}",
        created_at="2026-11-07T00:00:00Z",
        resolved_at=None if status == "open" else "2026-11-06T00:00:00Z",
        reconciliation_ids=[],
        telemetry_event_ids=[],
    )
    row.update(overrides)
    assert set(row) == set(_CS_INCIDENT_KEYS)
    return row


def _cs_alert(incident_id: str) -> dict[str, Any]:
    """An alert row as main.py builds it: no tenant key anywhere, including target_ref."""
    return {
        "alert_id": f"alert-{incident_id}",
        "severity": "critical",
        "category": "incident",
        "raised_at": "2026-11-07T00:00:00Z",
        "summary": f"incident {incident_id}",
        "target_ref": {
            "surface_id": "OC-02",
            "label": "Open incident",
            "href": f"/management/incidents/{incident_id}",
            "target_id": incident_id,
        },
    }


def _cs_anomaly(incident_id: str) -> dict[str, Any]:
    """An anomaly row built from an open incident, as _build_management_anomalies_payload does."""
    return {
        "id": incident_id,
        "kind": "incident",
        "severity": "critical",
        "status": "open",
        "summary": f"incident {incident_id}",
        "created_at": "2026-11-07T00:00:00Z",
        "triggered_at": None,
        "target_ref": {
            "label": "Open incident",
            "href": f"/management/incidents/{incident_id}",
            "target_id": incident_id,
        },
    }


def _cs_scenario(monkeypatch, *, runtime_count: int, current_runtime_count: int,
                 persona_count: int, current_persona_count: int, incidents=None):
    """Install a counting owner store. Returns (module, counts, store).

    The first `current_persona_count` personas are current; the rest are
    retired and are listed FIRST so the pre-fix 20 row cap would take them.
    Persona p-current-0 is paper_running with one active runtime and older
    retired runtimes. Persona p-current-1 is paper_running and only has
    retired runtimes.
    """
    from types import SimpleNamespace

    from services.control_plane.bff.assistant import management_service as ms
    from services.control_plane.bff.management_read_models.service import ManagementService

    counts: dict[str, int] = {}

    def counted(name, fn):
        def wrapper(*args, **kwargs):
            counts[name] = counts.get(name, 0) + 1
            return fn(*args, **kwargs)

        return wrapper

    states = ["paper_running", "paper_running", "draft", "research_only", "live_running"]
    current = [
        {"persona_id": f"p-current-{i}", "name": f"current {i}",
         "lifecycle_state": states[i % len(states)] if i > 1 else "paper_running",
         "tenant_id": _CS_TENANT}
        for i in range(current_persona_count)
    ]
    retired = [
        {"persona_id": f"p-retired-{i}", "name": f"retired {i}",
         "lifecycle_state": "retired", "tenant_id": _CS_TENANT}
        for i in range(persona_count - current_persona_count)
    ]
    personas = retired + current

    def runtime(runtime_id, binding, *, retired_runtime):
        return {
            "runtime_id": runtime_id, "binding_id": runtime_id,
            "persona_capital_binding_id": binding,
            "status": "retired" if retired_runtime else "active",
            "retired_at": "2026-10-13T14:02:00Z" if retired_runtime else None,
            "deployment_stage": "paper", "tenant_id": _CS_TENANT,
        }

    runtimes = [runtime(f"rt-live-{i}", f"b-p-current-{i}" if i != 1 else "b-none", retired_runtime=False)
                for i in range(current_runtime_count)]
    # p-current-1 has no active runtime: its only runtime is retired.
    runtimes[1:2] = [runtime("rt-live-1", "b-unbound", retired_runtime=False)] if current_runtime_count > 1 else []
    n_retired = runtime_count - len(runtimes)
    runtimes += [
        runtime(f"rt-old-{i}", "b-p-current-0" if i % 2 == 0 else "b-p-current-1", retired_runtime=True)
        for i in range(n_retired)
    ]
    incident_rows = list(incidents or [])

    def list_incidents(**kwargs):
        rows = list(incident_rows)
        if kwargs.get("open_only"):
            rows = [r for r in rows if r["status"] in {"open", "investigating"}]
        return rows

    def telemetry(runtime_id):
        if not runtime_id.startswith("rt-live"):
            return None
        return {"runtime_id": runtime_id, "pnl": 0.01, "drawdown": 0.0,
                "collected_at": _CS_NOW, "tenant_id": _CS_TENANT,
                "metrics": {"pnl": 0.01, "fill_rate": 0.9, "total_trades": 3}}

    def bindings_for(persona_id):
        return [{"id": f"b-{persona_id}", "binding_id": f"b-{persona_id}",
                 "capital_pool_id": f"pool-{persona_id}", "tenant_id": _CS_TENANT}]

    store = SimpleNamespace(
        list_runtime_bindings=counted("list_runtime_bindings", lambda: list(runtimes)),
        list_incidents=counted("list_incidents", list_incidents),
        list_evolution_decisions=counted("list_evolution_decisions", lambda: []),
        list_strategy_specs=counted("list_strategy_specs", lambda **kw: []),
        get_bindings_for_persona=counted("get_bindings_for_persona", bindings_for),
        get_sessions_for_persona=counted("get_sessions_for_persona", lambda _p: []),
        get_teaching_sessions_for_persona=counted("get_teaching_sessions_for_persona", lambda _p: []),
        get_capital_pool=counted("get_capital_pool", lambda pool_id: {"pool_id": pool_id, "tenant_id": _CS_TENANT}),
        get_persona_allowed_actions=counted("get_persona_allowed_actions", lambda _p: {"tenant_id": _CS_TENANT}),
        get_telemetry_summary=counted("get_telemetry_summary", telemetry),
        get_rollbacks=counted("get_rollbacks", lambda _rid: []),
    )
    service = ManagementService(read_store=store, utc_now=lambda: _CS_NOW)
    monkeypatch.setattr(ms, "_MANAGEMENT_AI_CONTEXT_SERVICE", service)
    monkeypatch.setattr(ms, "_list_persona_records", lambda *a, **k: list(personas))
    monkeypatch.setattr(ms, "_build_operator_alerts_payload",
                        lambda _s: {"alerts": [], "meta": {"surfaces": {}}})
    monkeypatch.setattr(
        get_management_ai_context_service(),
        "get_human_inbox",
                        lambda *a, **k: {"data": {"items": []}, "page_info": {"total": 0}, "meta": {"surfaces": {}}})
    monkeypatch.setattr(ms, "_build_management_anomalies_payload",
                        lambda _s: {"items": [], "meta": {"surfaces": {}}})
    return ms, counts, store


def test_ai_context_open_incident_without_tenant_key_reaches_every_summary(monkeypatch) -> None:
    open_incident = _cs_incident("inc-open-1", "open", persona_capital_binding_id="b-p-current-0",
                                 runtime_id="rt-live-0")
    history = [_cs_incident(f"inc-old-{i}", "resolved") for i in range(3)]
    ms, counts, _store = _cs_scenario(
        monkeypatch, runtime_count=7, current_runtime_count=2, persona_count=6,
        current_persona_count=3, incidents=[open_incident, *history],
    )
    monkeypatch.setattr(ms, "_build_operator_alerts_payload",
                        lambda _s: {"alerts": [_cs_alert("inc-open-1")], "meta": {"surfaces": {}}})
    monkeypatch.setattr(ms, "_build_management_anomalies_payload",
                        lambda _s: {"items": [_cs_anomaly("inc-open-1")], "meta": {"surfaces": {}}})

    cockpit = ms._mgmt_nl_collect_context("cockpit", _CS_NOW, tenant_id=_CS_TENANT)
    assert cockpit["snippets"]["cockpit"]["alerts_summary"]["total_active"] == 1
    assert cockpit["snippets"]["cockpit"]["anomalies_summary"]["total"] == 1

    fleet = ms._mgmt_nl_collect_context("persona_fleet", _CS_NOW, tenant_id=_CS_TENANT)
    items = {item["persona_id"]: item for item in fleet["snippets"]["persona_fleet"]["items"]}
    assert [i["incident_id"] for i in items["p-current-0"]["active_incidents"]] == ["inc-open-1"]
    assert items["p-current-0"]["health"]["status"] == "critical"
    # The incident read asks the owner for open rows only, so resolved history
    # adds no incident entity to the evidence entities.
    incident_entities = {ref for kind, ref in fleet["evidence_entities"] if kind == "incident"}
    assert incident_entities == {"inc-open-1"}

    # A resolved-only fleet reports no incident at all.
    ms2, _c2, _s2 = _cs_scenario(
        monkeypatch, runtime_count=7, current_runtime_count=2, persona_count=6,
        current_persona_count=3, incidents=history,
    )
    resolved_only = ms2._mgmt_nl_collect_context("persona_fleet", _CS_TENANT and _CS_NOW, tenant_id=_CS_TENANT)
    assert all(not i["active_incidents"] for i in resolved_only["snippets"]["persona_fleet"]["items"])
    assert not {r for k, r in resolved_only["evidence_entities"] if k == "incident"}


def test_ai_context_deployment_scope_keeps_foreign_tenant_and_no_tenant_scope_closed(monkeypatch) -> None:
    ms, _counts, _store = _cs_scenario(
        monkeypatch, runtime_count=3, current_runtime_count=2, persona_count=3,
        current_persona_count=3, incidents=[],
    )
    foreign_alert = {**_cs_alert("inc-foreign"), "tenant_id": "tenant-b"}
    own_alert = {**_cs_alert("inc-own"), "tenant_id": _CS_TENANT}
    keyless_alert = _cs_alert("inc-keyless")
    monkeypatch.setattr(
        ms, "_build_operator_alerts_payload",
        lambda _s: {"alerts": [foreign_alert, own_alert, keyless_alert], "meta": {"surfaces": {}}},
    )
    scoped = ms._mgmt_nl_collect_context("cockpit", _CS_NOW, tenant_id=_CS_TENANT)
    assert scoped["snippets"]["cockpit"]["alerts_summary"]["total_active"] == 2
    assert ("alert", "alert-inc-foreign") not in scoped["evidence_entities"]
    unscoped = ms._mgmt_nl_collect_context("cockpit", _CS_NOW, tenant_id=None)
    assert unscoped["snippets"]["cockpit"]["alerts_summary"]["total_active"] == 0
    # The global matcher is unchanged: it still rejects a row with no tenant key.
    assert ms._mgmt_nl_record_matches_tenant(keyless_alert, _CS_TENANT) is False


def test_ai_context_assistant_alerts_collector_keeps_deployment_scoped_rows() -> None:
    from services.control_plane.bff.assistant import source_collectors as sc

    class _Deps:
        def __init__(self):
            self.tenant_payload_fn = lambda identity, requested_tenant=None: {
                "id": _CS_TENANT, "allowed_ids": [_CS_TENANT], "scope": "tenant"}
            self.read_roles = frozenset({"viewer"})
            self.filter_tenant_records_fn = lambda rows, tenant: [
                r for r in rows if r.get("tenant_id") == tenant]
            self.filter_deployment_scoped_records_fn = None

    from services.control_plane.bff.assistant.management_service import (
        _mgmt_nl_filter_deployment_scoped_records,
    )

    deps = _Deps()
    deps.filter_deployment_scoped_records_fn = _mgmt_nl_filter_deployment_scoped_records
    identity = OperatorIdentity(operator_id="viewer-1", roles=["viewer"])
    rows = [_cs_alert("inc-keyless"), {**_cs_alert("inc-foreign"), "tenant_id": "tenant-b"}]
    payload = sc._assistant_filter_payload_tenant(
        {"alerts": rows}, identity, deps=deps, deployment_scoped=True)
    assert [r["alert_id"] for r in payload["alerts"]] == ["alert-inc-keyless"]
    # Any other source keeps the strict filter and drops the same keyless row.
    strict = sc._assistant_filter_payload_tenant({"items": rows}, identity, deps=deps)
    assert strict["items"] == []


def test_ai_context_current_state_excludes_retired_and_projects_healthy(monkeypatch) -> None:
    ms, counts, _store = _cs_scenario(
        monkeypatch, runtime_count=12, current_runtime_count=2, persona_count=30,
        current_persona_count=5, incidents=[],
    )
    context = ms._mgmt_nl_collect_context("all", _CS_NOW, tenant_id=_CS_TENANT)
    summary = context["snippets"]["trading_pulse"]["summary"]
    assert summary["runtime_count"] == 2 and summary["retired_runtime_count"] == 10
    assert context["snippets"]["portfolio"]["runtime_count"] == 2
    assert context["snippets"]["portfolio"]["retired_runtime_count"] == 10
    # Retired runtimes cause no telemetry or rollback read.
    assert counts["get_telemetry_summary"] <= 2 and counts.get("get_rollbacks", 0) <= 2

    fleet = context["snippets"]["persona_fleet"]
    assert fleet["summary"]["retired_persona_count"] == 25
    assert fleet["summary"]["retired_runtime_count"] == 10
    assert fleet["summary"]["total_personas"] == 5
    assert not [i for i in fleet["items"] if i["persona_id"].startswith("p-retired")]
    items = {i["persona_id"]: i for i in fleet["items"]}
    # paper_running persona, one active runtime, older retired runtimes, no incident.
    assert items["p-current-0"]["health"]["status"] == "healthy"
    assert items["p-current-0"]["health"]["reasons"] == []
    # paper_running persona whose only runtimes are retired.
    assert "no_runtime_binding" in items["p-current-1"]["health"]["reasons"]
    by_state = fleet["summary"]["by_lifecycle_state"]
    assert by_state["paper_running"] == 2 and by_state["draft"] == 1 and by_state["research_only"] == 1
    # draft and research_only personas are not counted as degraded.
    degraded_ids = {i["persona_id"] for i in fleet["items"] if i["health"]["status"] == "degraded"}
    assert degraded_ids == {"p-current-1", "p-current-2", "p-current-3", "p-current-4"}
    assert fleet["summary"]["degraded_personas"] == 2  # p-current-2 draft, p-current-3 research_only excluded


def test_ai_context_operational_personas_survive_the_row_cap(monkeypatch) -> None:
    ms, _counts, _store = _cs_scenario(
        monkeypatch, runtime_count=5, current_runtime_count=2, persona_count=60,
        current_persona_count=3, incidents=[],
    )
    fleet = ms._mgmt_nl_collect_context("persona_fleet", _CS_NOW, tenant_id=_CS_TENANT)["snippets"]["persona_fleet"]
    ids = [i["persona_id"] for i in fleet["items"]]
    assert ids[0].startswith("p-current-") and "p-current-0" in ids
    assert fleet["summary"]["retired_persona_count"] == 57
    provider = ms._mgmt_nl_provider_summary_context({"persona_fleet": fleet})["persona_fleet"]
    # population_scope describes the non-retired population, so it is not partial.
    assert provider["population_scope"]["total_population"] == 3
    assert provider["population_scope"]["partial"] is False


def test_ai_context_133_runtimes_143_personas_reads_and_size_drop(monkeypatch) -> None:
    import os

    ms, counts, _store = _cs_scenario(
        monkeypatch, runtime_count=133, current_runtime_count=5, persona_count=143,
        current_persona_count=12, incidents=[],
    )
    context = ms._mgmt_nl_collect_context("all", _CS_NOW, tenant_id=_CS_TENANT)
    provider_summary = ms._mgmt_nl_provider_summary_context(context["snippets"])
    size = len(json.dumps(provider_summary, default=list, ensure_ascii=True))
    owner_reads = sum(counts.values())
    path = os.environ.get("PANTHEON_AI_CURRENT_STATE_METRICS_PATH")
    if path:
        Path(path).write_text(json.dumps(
            {"provider_summary_bytes": size, "owner_reads": owner_reads, "counts": counts}, sort_keys=True))
    # Before this change all 133 runtimes and all 143 personas were read and
    # projected; the recorded "before" numbers are in the task evidence.
    assert counts["get_telemetry_summary"] <= 5
    assert counts["get_bindings_for_persona"] == 12
    assert context["snippets"]["persona_fleet"]["summary"]["retired_persona_count"] == 131
    assert context["snippets"]["trading_pulse"]["summary"]["runtime_count"] == 5
    assert context["snippets"]["trading_pulse"]["summary"]["retired_runtime_count"] == 128
    assert owner_reads < 133 + 143
    assert size < 34970  # provider summary size before this change, see evidence


def test_ai_context_current_runtime_rollback_unavailable_is_still_reported(monkeypatch) -> None:
    ms, counts, store = _cs_scenario(
        monkeypatch, runtime_count=4, current_runtime_count=2, persona_count=3,
        current_persona_count=3, incidents=[],
    )
    store.get_rollbacks = lambda _rid: [{
        "runtime_id": "rt-live-0", "id": "rb-1", "owner": "rollback-owner",
        "status": "unavailable", "source_kind": "unavailable",
        "degradation_reason": "rollback owner offline", "tenant_id": _CS_TENANT}]
    context = ms._mgmt_nl_collect_context("trading_pulse", _CS_NOW, tenant_id=_CS_TENANT)
    surface = context["surfaces"]["management_trading_pulse"]
    assert surface["status"] == "unavailable"
    assert "rollback owner offline" in json.dumps(surface)


# BFF-AI-FLEET-INBOX-20261107: the AI persona_fleet projection must run through
# the app-scoped PersonaService, and the AI inbox read must use the wired
# payload's data.items layer with the caller identity.
def _fleet_inbox_real_persona_service():
    from services.control_plane.bff.personas.service import (
        PersonaService,
        create_persona_registry_write_owner,
    )

    class _SourceHealthStore:
        def get_source_connector_registry(self):
            return {"connectors": []}

        def get_source_health_usage_snapshot(self):
            return {"sources": []}

    return PersonaService(
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=object(),
        read_store=_SourceHealthStore(),
        command_store=object(),
    )


def _fleet_inbox_source_health_persona(persona_id: str) -> dict:
    return {
        "persona_id": persona_id,
        "name": persona_id,
        "lifecycle_state": "paper_running",
        "tenant_id": _CS_TENANT,
        "required_data_sources": [{"provider_key": "shioaji"}],
    }


def _fleet_inbox_scenario(monkeypatch, personas):
    ms, _counts, _store = _cs_scenario(
        monkeypatch, runtime_count=1, current_runtime_count=1, persona_count=1,
        current_persona_count=1, incidents=[],
    )
    monkeypatch.setattr(ms, "_list_persona_records", lambda *a, **k: list(personas))
    return ms


def test_ai_persona_fleet_projects_source_health_personas_through_app_persona_service(monkeypatch) -> None:
    ms = _fleet_inbox_scenario(
        monkeypatch, [_fleet_inbox_source_health_persona("p-src-0")]
    )
    unbound = ms._mgmt_nl_collect_context("persona_fleet", _CS_NOW, tenant_id=_CS_TENANT)
    item = unbound["snippets"]["persona_fleet"]["items"][0]
    # Without the app-scoped service the projection raises; the persona is an
    # unavailable owner observation naming the exception class.
    assert item["health"]["status"] == "unavailable"
    assert any(
        "RuntimeError" in str(o.get("degradation_reason"))
        for o in unbound["surfaces"]["persona_fleet"]["owner_observations"]
    )

    from services.control_plane.bff.assistant.management_service import (
        bind_persona_service_projection as _bind_persona_service_projection,
    )

    monkeypatch.setattr(
        ms,
        "_PROJECT_PERSONA_FLEET_ITEM_FN",
        _bind_persona_service_projection(_fleet_inbox_real_persona_service()),
    )
    bound = ms._mgmt_nl_collect_context("persona_fleet", _CS_NOW, tenant_id=_CS_TENANT)
    bound_item = bound["snippets"]["persona_fleet"]["items"][0]
    assert bound_item["health"]["status"] != "unavailable"
    assert not any(
        "persona_projection_failed" in str(o.get("degradation_reason"))
        for o in bound["surfaces"]["persona_fleet"]["owner_observations"]
    )


def test_ai_persona_fleet_has_no_module_global_persona_service() -> None:
    from services.control_plane.bff.assistant import management_service as ms

    for name in ("_PERSONA_SERVICE", "set_persona_service", "get_persona_service", "reset_persona_service"):
        assert not hasattr(ms, name), name


def test_ai_persona_fleet_one_persona_failure_keeps_the_others_and_logs(monkeypatch, caplog) -> None:
    ms = _fleet_inbox_scenario(
        monkeypatch,
        [_fleet_inbox_source_health_persona("p-bad"), _fleet_inbox_source_health_persona("p-good")],
    )
    from services.control_plane.bff.assistant.management_service import (
        bind_persona_service_projection as _bind_persona_service_projection,
    )

    real_impl = _bind_persona_service_projection(_fleet_inbox_real_persona_service())

    def flaky(persona, **kwargs):
        if persona["persona_id"] == "p-bad":
            raise KeyError("boom")
        return real_impl(persona, **kwargs)

    monkeypatch.setattr(ms, "_PROJECT_PERSONA_FLEET_ITEM_FN", flaky)
    with caplog.at_level("WARNING"):
        context = ms._mgmt_nl_collect_context("persona_fleet", _CS_NOW, tenant_id=_CS_TENANT)
    items = {i["persona_id"]: i for i in context["snippets"]["persona_fleet"]["items"]}
    assert items["p-bad"]["health"]["status"] == "unavailable"
    assert items["p-good"]["health"]["status"] != "unavailable"
    bad_obs = [
        o for o in context["surfaces"]["persona_fleet"]["owner_observations"]
        if o.get("subject_id") == "p-bad"
    ]
    assert bad_obs and bad_obs[0]["status"] == "unavailable"
    assert "KeyError" in bad_obs[0]["degradation_reason"]
    assert any("KeyError" in r.getMessage() for r in caplog.records)


def test_ai_persona_fleet_error_branch_logs_exception_class(monkeypatch, caplog) -> None:
    ms = _fleet_inbox_scenario(monkeypatch, [_fleet_inbox_source_health_persona("p-x")])

    def boom(*_a, **_k):
        raise ValueError("owner list failed")

    monkeypatch.setattr(ms, "_mgmt_nl_persona_state", boom)
    with caplog.at_level("WARNING"):
        context = ms._mgmt_nl_collect_context("persona_fleet", _CS_NOW, tenant_id=_CS_TENANT)
    assert any("ValueError" in r.getMessage() for r in caplog.records)
    assert context["surfaces"]["persona_fleet"]["status"] == "unavailable"


def test_ai_cockpit_counts_wired_human_inbox_data_items_with_caller_identity(monkeypatch) -> None:
    ms, _counts, _store = _cs_scenario(
        monkeypatch, runtime_count=1, current_runtime_count=1, persona_count=1,
        current_persona_count=1, incidents=[],
    )
    seen: dict = {}
    incident_item = {
        "id": "agent-2e0cc89b4ea8-0001",
        "source_type": "incident",
        "status": "open",
        "title": "Control loop cannot fetch runtime summaries",
        "tenant_id": _CS_TENANT,
    }

    def wired_payload(snapshot_at, **kwargs):
        seen.update(kwargs)
        ok = {"status": "ok", "source": "service"}
        return {
            "data": {"id": "management-human-inbox", "items": [incident_item], "summary": {}},
            "page_info": {"next_page_token": None, "total": 1, "page_size": 1},
            "meta": {"surfaces": {
                "approval_queue": ok, "governance_review_queue": ok,
                "incidents": ok, "persona_readiness": ok,
            }},
        }

    monkeypatch.setattr(
        get_management_ai_context_service(),
        "get_human_inbox", lambda **kwargs: wired_payload(None, **kwargs)
    )
    caller = object()
    context = ms._mgmt_nl_collect_context(
        "cockpit", _CS_NOW, tenant_id=_CS_TENANT, identity=caller
    )
    assert context["snippets"]["cockpit"]["human_inbox_summary"]["total"] == 1
    assert seen["identity"] is caller
    observations = {
        o["subject_type"]: o
        for o in context["surfaces"]["management_cockpit"]["owner_observation"][
            "contributing_observations"
        ]
    }
    assert observations["approval_queue"]["status"] == "ok"
