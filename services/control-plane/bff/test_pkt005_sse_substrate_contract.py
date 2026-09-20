from __future__ import annotations

import ast
import asyncio
from collections import deque
import json
import os
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.auth.policy import bff_error as _bff_error
from services.control_plane.bff.events.router import create_events_router
from services.control_plane.bff.events.service import (
    DEFAULT_SSE_CHANNEL_CATALOG,
    EventStreamService,
    SseReplayUnavailableError,
)
from services.control_plane.bff.models import (
    ApprovalCreatedPayload,
    ApprovalDecidedPayload,
    ApprovalSlaEscalatedPayload,
    ApprovalStageChangedPayload,
    AskMessageCompletedPayload,
    AskMessageDeltaPayload,
    AskSessionCompletedPayload,
    AskSessionFailedPayload,
    AskSessionStartedPayload,
    AskToolCalledPayload,
    ErrorCode,
    ObjectType,
    SseEventEnvelope,
)
from services.control_plane.bff.tools_integrations.service import SSE_CHANNEL_CATALOG


AUTH = "Bearer test-operator:operator,admin"
FINAL_CHANNEL_CATALOG = (
    "approval",
    "ask",
    "artifact",
    "runtime",
    "mcp",
    "skill",
    "channel",
    "tool",
    "ranking",
    "rebalance",
    "evolution",
    "research",
    "signal",
    "inbox",
    "journal",
    "postmortem",
    "loop",
    "sentinel",
    "intervention",
    "audit",
    "system",
)
SSE_CHANNELS = set(FINAL_CHANNEL_CATALOG)


def _load_main_cors_allow_headers() -> tuple[str, ...]:
    main_path = os.path.join(os.path.dirname(__file__), "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="main.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_CORS_ALLOW_HEADERS":
                    if isinstance(node.value, (ast.Tuple, ast.List)):
                        return tuple(
                            elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
                        )
    try:
        from services.control_plane.bff.core.http_security import _CORS_ALLOW_HEADERS as _imported_headers
        return tuple(_imported_headers)
    except ImportError:
        return ()


_CORS_ALLOW_HEADERS = _load_main_cors_allow_headers()


def _error_code_value(code: Any) -> Any:
    return getattr(code, "value", code)


def _response_error(response: Any) -> dict:
    payload = response.json()
    return payload.get("detail", payload)["error"]


sse_service = EventStreamService(channels=FINAL_CHANNEL_CATALOG)
_events_router = create_events_router(
    event_stream_service=sse_service,
    bff_error=_bff_error,
    include_domain_sse_aliases=True,
)
app = FastAPI()
app.include_router(_events_router)


def _handle_sse_stream(
    channel: str,
    buffer: deque,
    subscribers: list,
    last_event_id: Optional[str],
    extra_headers: Optional[dict[str, str]] = None,
) -> Any:
    sse_service.buffers.setdefault(channel, deque(maxlen=sse_service.max_events))
    sse_service.subscribers.setdefault(channel, [])
    return sse_service.stream_response(
        channel,
        last_event_id,
        bff_error=_bff_error,
        conflict_code=ErrorCode.RESOURCE_CONFLICT,
        extra_headers=extra_headers,
    )


_agora_router = create_agora_router(
    read_surface=lambda: None,
    sync_servant_agent=lambda payload: payload,
    extract_identity=lambda auth=None: None,
    require_read_role=lambda ident: None,
    require_write_role=lambda ident: None,
    bff_error=_bff_error,
    utc_now=lambda: "2026-05-23T00:00:00Z",
    sse_buffers=sse_service.buffers,
    sse_subscribers=sse_service.subscribers,
    handle_sse_stream=_handle_sse_stream,
)
app.include_router(_agora_router)

_publish_event = sse_service.publish
_replay_from = sse_service.replay
_sse_format = sse_service.format_event
_sse_buffers = sse_service.buffers
_sse_subscribers = sse_service.subscribers

stream_generic_events = next(r.endpoint for r in _events_router.routes if r.path == "/api/v1/stream/{channel}")
stream_bff_events = next(r.endpoint for r in _events_router.routes if r.path == "/bff/events/stream")
bff_events_stream_alias = lambda channel="system", last_event_id=None, authorization=None: stream_generic_events(channel, last_event_id, authorization)
bff_sse_notifications_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/notifications")
bff_sse_cc_kpi_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/command-center/kpi")
bff_sse_cc_events_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/command-center/events")
bff_sse_job_progress_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/jobs/{jobId}/progress")
bff_sse_alerts_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/alerts")
bff_sse_incident_timeline_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/incidents/{incidentId}/timeline")
bff_sse_deployment_events_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/deployment/events")
bff_sse_review_updates_alias = next(r.endpoint for r in _events_router.routes if r.path == "/bff/sse/review/updates")

bff_sse_agora_signals_alias = next(
    r.endpoint for r in _agora_router.routes if getattr(r, "path", None) == "/bff/sse/agora/signals"
)
bff_sse_agora_session_alias = next(
    r.endpoint for r in _agora_router.routes if getattr(r, "path", None) == "/bff/sse/agora/sessions/{sessionId}"
)


async def stream_approval_events(last_event_id: Optional[str] = None, authorization: Optional[str] = None):
    return await stream_generic_events("approval", last_event_id, authorization)


async def stream_ask_events(last_event_id: Optional[str] = None, authorization: Optional[str] = None):
    return await stream_generic_events("ask", last_event_id, authorization)


@pytest.fixture(autouse=True)
def clean_sse_buffers():
    for buffer in _sse_buffers.values():
        buffer.clear()
    for subscribers in _sse_subscribers.values():
        subscribers.clear()
    sse_service.incident_buffer.clear()
    sse_service.incident_subscribers.clear()
    yield
    for buffer in _sse_buffers.values():
        buffer.clear()
    for subscribers in _sse_subscribers.values():
        subscribers.clear()
    sse_service.incident_buffer.clear()
    sse_service.incident_subscribers.clear()


def test_final_sse_channel_catalog_contains_approval_and_ask() -> None:
    assert SSE_CHANNEL_CATALOG == FINAL_CHANNEL_CATALOG
    assert "approval" in SSE_CHANNELS
    assert "ask" in SSE_CHANNELS
    assert "incident" not in SSE_CHANNELS


def test_sse_event_envelope_and_payload_models_are_importable() -> None:
    event = SseEventEnvelope[dict[str, str]](
        id="evt-final-sse-001",
        type="approval.created",
        data={"approval_id": "appr-final-sse-001"},
    )
    assert event.model_dump(mode="json")["type"] == "approval.created"

    approval_payloads = [
        ApprovalCreatedPayload(
            approval_id="appr-final-sse-001",
            target_type=ObjectType.APPROVAL_DECISION,
            target_id="decision-final-sse-001",
            requester_id="operator-1",
        ),
        ApprovalStageChangedPayload(
            approval_id="appr-final-sse-001",
            previous_stage="requested",
            current_stage="reviewing",
            actor_id="operator-2",
        ),
        ApprovalDecidedPayload(
            approval_id="appr-final-sse-001",
            outcome="approved",
            decided_by="operator-3",
        ),
        ApprovalSlaEscalatedPayload(
            approval_id="appr-final-sse-001",
            severity="high",
            message="Approval breached SLA threshold",
        ),
    ]
    ask_payloads = [
        AskSessionStartedPayload(session_id="ask-final-sse-001", persona_id="persona-1"),
        AskMessageDeltaPayload(
            session_id="ask-final-sse-001",
            message_id="msg-1",
            delta="partial",
        ),
        AskToolCalledPayload(
            session_id="ask-final-sse-001",
            tool_name="search",
            call_id="call-1",
        ),
        AskMessageCompletedPayload(
            session_id="ask-final-sse-001",
            message_id="msg-1",
            full_content="complete",
        ),
        AskSessionCompletedPayload(session_id="ask-final-sse-001"),
        AskSessionFailedPayload(
            session_id="ask-final-sse-002",
            error_code="ASK_FAILED",
            error_message="Ask session failed",
        ),
    ]

    assert len(approval_payloads) == 4
    assert len(ask_payloads) == 6


def test_replay_success_returns_events_after_last_event_id() -> None:
    channel = "approval"
    first_id = _publish_event(
        _sse_buffers[channel],
        _sse_subscribers[channel],
        "approval.created",
        {"approval_id": "appr-final-sse-001"},
    )
    second_id = _publish_event(
        _sse_buffers[channel],
        _sse_subscribers[channel],
        "approval.decided",
        {"approval_id": "appr-final-sse-001", "outcome": "approved"},
    )

    replayed = _replay_from(channel, _sse_buffers[channel], first_id)

    assert [event["id"] for event in replayed] == [second_id]
    assert replayed[0]["type"] == "approval.decided"
    assert replayed[0]["data"]["outcome"] == "approved"
    assert "event: approval.decided" in _sse_format(replayed[0])


def test_replay_unavailable_uses_final_error_envelope_with_resync_metadata() -> None:
    client = TestClient(app)

    response = client.get(
        "/api/v1/stream/approval?last_event_id=evt-final-sse-missing",
        headers={"Authorization": AUTH},
    )

    assert response.status_code == 409, response.text
    error = _response_error(response)
    assert _error_code_value(error["code"]) == "RESOURCE_CONFLICT"
    assert error["details"]["reason"] == "SSE_REPLAY_HISTORY_MISSING"
    assert error["details"]["channel"] == "approval"
    assert error["details"]["lastEventId"] == "evt-final-sse-missing"
    assert error["details"]["replaySupported"] is True
    assert error["details"]["replayWindowEvents"] == 500
    assert error["details"]["replayStore"] == "in-memory"
    assert error["details"]["resyncRoutes"] == ["/bff/approvals", "/bff/v5/interventions"]


def test_approval_and_ask_stream_routes_publish_replay_metadata_headers() -> None:
    for route, channel, resync in [
        (stream_approval_events, "approval", "/bff/approvals,/bff/v5/interventions"),
        (
            stream_ask_events,
            "ask",
            (
                "/bff/management/ai/conversations,"
                "/bff/management/ai/conversations/{id},"
                "/bff/agora/ask/sessions/{id},"
                "/bff/agora/committee/sessions/{id}"
            ),
        ),
    ]:
        response = asyncio.run(route(last_event_id=None, authorization=AUTH))
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == channel
        assert response.headers["X-SSE-Replay-Supported"] == "true"
        assert response.headers["X-SSE-Replay-Window-Events"] == "500"
        assert response.headers["X-SSE-Replay-Store"] == "in-memory"
        assert response.headers["X-SSE-Resync-Routes"] == resync


def test_execute_plans_sse_compatibility_routes_are_registered() -> None:
    # OpenAPI is the compiled client-visible routing surface.
    registered_paths = set(app.openapi()["paths"])

    assert {
        "/bff/events/stream",
        "/bff/sse/notifications",
        "/bff/sse/command-center/kpi",
        "/bff/sse/command-center/events",
        "/bff/sse/jobs/{jobId}/progress",
        "/bff/sse/alerts",
        "/bff/sse/incidents/{incidentId}/timeline",
        "/bff/sse/deployment/events",
        "/bff/sse/review/updates",
        "/bff/sse/agora/signals",
        "/bff/sse/agora/sessions/{sessionId}",
    }.issubset(registered_paths)


def test_execute_plans_sse_compatibility_aliases_share_replay_headers() -> None:
    route_factories = [
        (
            lambda: bff_events_stream_alias(
                channel="system", last_event_id=None, authorization=AUTH,
            ),
            "system",
        ),
        (lambda: bff_sse_notifications_alias(last_event_id=None, authorization=AUTH), "inbox"),
        (lambda: bff_sse_cc_kpi_alias(last_event_id=None, authorization=AUTH), "ranking"),
        (lambda: bff_sse_cc_events_alias(last_event_id=None, authorization=AUTH), "loop"),
        (
            lambda: bff_sse_job_progress_alias(
                jobId="job-final-sse-001", last_event_id=None, authorization=AUTH,
            ),
            "tool",
        ),
        (lambda: bff_sse_alerts_alias(last_event_id=None, authorization=AUTH), "sentinel"),
        (
            lambda: bff_sse_incident_timeline_alias(
                incidentId="inc-final-sse-001", last_event_id=None, authorization=AUTH,
            ),
            "journal",
        ),
        (lambda: bff_sse_deployment_events_alias(last_event_id=None, authorization=AUTH), "artifact"),
        (lambda: bff_sse_review_updates_alias(last_event_id=None, authorization=AUTH), "approval"),
    ]

    for response_factory, expected_channel in route_factories:
        response = asyncio.run(response_factory())
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == expected_channel
        assert response.headers["X-SSE-Replay-Supported"] == "true"
        assert response.headers["X-SSE-Replay-Window-Events"] == "500"
        assert response.headers["X-SSE-Replay-Store"] == "in-memory"

    # Agora's signal/session SSE aliases are synchronous route handlers (not
    # coroutines), unlike every other alias above, and resolve Last-Event-ID
    # through a FastAPI Header() dependency default that only FastAPI's own
    # request dependency-injection resolves to None; call them directly with
    # that header dependency explicitly supplied rather than via asyncio.run.
    for sync_factory, expected_channel in [
        (
            lambda: bff_sse_agora_signals_alias(
                last_event_id=None, authorization=AUTH, last_event_id_header=None,
            ),
            "signal",
        ),
        (
            lambda: bff_sse_agora_session_alias(
                sessionId="ask-final-sse-001", last_event_id=None, authorization=AUTH,
                last_event_id_header=None,
            ),
            "session:ask-final-sse-001",
        ),
    ]:
        response = sync_factory()
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == expected_channel
        assert response.headers["X-SSE-Replay-Supported"] == "true"
        assert response.headers["X-SSE-Replay-Window-Events"] == "500"
        assert response.headers["X-SSE-Replay-Store"] == "in-memory"


async def _first_sse_payload(response: Any) -> dict:
    iterator = response.body_iterator
    try:
        chunk = await anext(iterator)
    finally:
        if hasattr(iterator, "aclose"):
            await iterator.aclose()
    if isinstance(chunk, bytes):
        chunk = chunk.decode()
    data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


def test_execute_plans_sse_alias_uses_same_envelope_shape_as_generic_stream() -> None:
    _publish_event(
        _sse_buffers["inbox"],
        _sse_subscribers["inbox"],
        "inbox.notification.created",
        {"notification_id": "note-final-sse-001"},
    )

    async def compare_alias_to_generic() -> tuple[dict, dict]:
        generic_response = await stream_generic_events(
            channel="inbox", last_event_id=None, authorization=AUTH,
        )
        alias_response = await bff_sse_notifications_alias(
            last_event_id=None, authorization=AUTH,
        )
        return (
            await _first_sse_payload(generic_response),
            await _first_sse_payload(alias_response),
        )

    generic_payload, alias_payload = asyncio.run(compare_alias_to_generic())

    assert alias_payload == generic_payload
    assert set(alias_payload) == {"id", "type", "timestamp", "data"}
    assert alias_payload["type"] == "inbox.notification.created"
    assert alias_payload["data"] == {"notification_id": "note-final-sse-001"}


def test_execute_plans_sse_aliases_return_replay_unavailable_envelope() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            bff_sse_notifications_alias(
                last_event_id="evt-final-sse-missing",
                authorization=AUTH,
            )
        )

    assert exc_info.value.status_code == 409
    error = exc_info.value.detail["error"]
    assert _error_code_value(error["code"]) == "RESOURCE_CONFLICT"
    assert error["details"]["reason"] == "SSE_REPLAY_HISTORY_MISSING"
    assert error["details"]["channel"] == "inbox"
    assert error["details"]["lastEventId"] == "evt-final-sse-missing"


def test_bff_events_stream_matches_lovable_shell_schema_without_auth() -> None:
    response = asyncio.run(
        stream_bff_events(
            channels="system,loop",
            last_event_id=None,
            last_event_id_camel=None,
        )
    )

    assert response.media_type == "text/event-stream"
    assert response.headers["X-SSE-Channel"] == "bff"
    assert response.headers["X-SSE-Replay-Supported"] == "false"

    first_chunk = asyncio.run(anext(response.body_iterator))
    assert "event:" not in first_chunk
    data_line = next(line for line in first_chunk.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["schemaVersion"] == 1
    assert payload["channel"] == "system"
    assert payload["type"] == "system.connected"
    assert payload["payload"]["channels"] == ["system", "loop"]


def test_cors_allow_headers_include_lovable_bff_client_headers() -> None:
    assert "Accept-Language" in _CORS_ALLOW_HEADERS
    assert "X-BFF-Api-Version" in _CORS_ALLOW_HEADERS
    assert "X-Locale" in _CORS_ALLOW_HEADERS
    assert "X-Request-Id" in _CORS_ALLOW_HEADERS
    assert "X-Tenant-Id" in _CORS_ALLOW_HEADERS


def test_internal_publish_infers_approval_and_ask_channels() -> None:
    client = TestClient(app)

    approval_response = client.post(
        "/api/v1/internal/sse/publish?event_type=approval.created",
        json={"approval_id": "appr-final-sse-001"},
        headers={"Authorization": AUTH},
    )
    ask_response = client.post(
        "/api/v1/internal/sse/publish?event_type=ask.tool.called",
        json={"session_id": "ask-final-sse-001", "tool_name": "search", "call_id": "call-1"},
        headers={"Authorization": AUTH},
    )

    assert approval_response.status_code == 200, approval_response.text
    assert ask_response.status_code == 200, ask_response.text
    approval_event = _sse_buffers["approval"][0][1]
    ask_event = _sse_buffers["ask"][0][1]
    assert approval_event["id"] == approval_response.json()["event_id"]
    assert approval_event["type"] == "approval.created"
    assert approval_event["data"]["approval_id"] == "appr-final-sse-001"
    assert ask_event["id"] == ask_response.json()["event_id"]
    assert ask_event["type"] == "ask.tool.called"
    assert ask_event["data"]["tool_name"] == "search"


def test_invalid_generic_channel_returns_catalog_validation_error() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/stream/not-a-channel", headers={"Authorization": AUTH})

    assert response.status_code == 400, response.text
    error = _response_error(response)
    assert _error_code_value(error["code"]) == "VALIDATION_FAILED"
    assert "approval" in error["details"]["reason"]
    assert "ask" in error["details"]["reason"]


def test_ask_replay_payload_is_json_serializable_sse_data() -> None:
    channel = "ask"
    event_id = _publish_event(
        _sse_buffers[channel],
        _sse_subscribers[channel],
        "ask.message.delta",
        {"session_id": "ask-final-sse-001", "message_id": "msg-1", "delta": "hello"},
    )
    event = _sse_buffers[channel][0][1]

    assert event["id"] == event_id
    formatted = _sse_format(event)
    data_line = next(line for line in formatted.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["data"]["delta"] == "hello"


def test_sse_multiline_data_framing() -> None:
    multiline_text = "line1: start\nline2: middle\nline3: end"
    formatted = sse_service.format_event({
        "id": "evt-multiline-001",
        "type": "log.stream",
        "data": {"output": multiline_text},
    })
    assert "event: log.stream" in formatted
    assert "id: evt-multiline-001" in formatted
    lines = formatted.splitlines()
    data_lines = [l for l in lines if l.startswith("data: ")]
    assert len(data_lines) >= 1
    full_data = "\n".join(l.removeprefix("data: ") for l in data_lines)
    parsed = json.loads(full_data)
    assert parsed["data"]["output"] == multiline_text

    # Also verify raw multiline text with ServerSentEvent
    from fastapi.sse import ServerSentEvent
    raw_sse = ServerSentEvent(raw_data="alpha\nbeta\ngamma", event="chunk", id="evt-raw-001")
    raw_formatted = sse_service.format_event(raw_sse)
    assert "event: chunk\n" in raw_formatted
    assert "data: alpha\ndata: beta\ndata: gamma\n" in raw_formatted
    assert "id: evt-raw-001\n" in raw_formatted


def test_sse_exact_event_ids_preserved_and_replayed() -> None:
    channel = "approval"
    exact_id_1 = "evt-exact-uuid-12345678-abcd-ef01-2345-6789abcdef01"
    exact_id_2 = "evt-exact-custom-id-99999"

    event1 = {
        "id": exact_id_1,
        "type": "approval.created",
        "timestamp": "2026-09-19T12:00:00Z",
        "data": {"approval_id": "appr-001"},
    }
    event2 = {
        "id": exact_id_2,
        "type": "approval.decided",
        "timestamp": "2026-09-19T12:01:00Z",
        "data": {"approval_id": "appr-001", "outcome": "approved"},
    }
    _sse_buffers[channel].append((exact_id_1, event1))
    _sse_buffers[channel].append((exact_id_2, event2))

    replayed = _replay_from(channel, _sse_buffers[channel], exact_id_1)
    assert len(replayed) == 1
    assert replayed[0]["id"] == exact_id_2
    assert replayed[0]["id"] == "evt-exact-custom-id-99999"

    formatted = _sse_format(event1)
    assert f"id: {exact_id_1}\n" in formatted


def test_sse_disconnect_cancellation_cleans_subscribers() -> None:
    channel = "approval"
    _sse_subscribers[channel].clear()
    assert len(_sse_subscribers[channel]) == 0

    async def _test():
        gen = sse_service.stream(channel, _sse_buffers[channel], _sse_subscribers[channel], None)
        task = asyncio.create_task(anext(gen))
        await asyncio.sleep(0.01)
        # Entering stream registers subscriber queue
        assert len(_sse_subscribers[channel]) == 1
        # Disconnect client: cancel task then close generator
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        await gen.aclose()
        # Subscriber queue must be cleaned up on disconnect
        assert len(_sse_subscribers[channel]) == 0

    asyncio.run(_test())


def test_sse_persisted_date_enum_null_and_fingerprint_serialization(tmp_path: Any, monkeypatch: Any) -> None:
    from datetime import datetime, timezone
    from enum import Enum

    class TestStage(str, Enum):
        PENDING = "pending"
        APPROVED = "approved"

    test_date = datetime(2026, 9, 19, 14, 30, 0, tzinfo=timezone.utc)

    # 1. Configure file-backed SSE replay store
    monkeypatch.setenv("PANTHEON_BFF_SSE_REPLAY_STORE", "file")
    service = EventStreamService(channels=("approval",), data_dir=str(tmp_path))

    data_payload = {
        "stage": TestStage.APPROVED,
        "created_at": test_date,
        "notes": None,
        "idempotency_fingerprint": "sha256:4a5b6c7d8e9f0123456789abcdef",
        "nested": {
            "resolved_at": test_date,
            "decision": None,
            "status": TestStage.PENDING,
        },
    }

    # 2. Publish with date/enum/null/fingerprint types: must persist to disk without TypeError
    event_id = service.publish(
        service.buffers["approval"],
        service.subscribers["approval"],
        "approval.state",
        data_payload,
    )
    assert event_id.startswith("evt-")

    # 3. Verify physical file on disk
    file_path = service._shared_replay_file("approval")
    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8").strip()
    persisted_json = json.loads(content)
    assert persisted_json["id"] == event_id
    assert persisted_json["type"] == "approval.state"
    assert persisted_json["data"]["stage"] == "approved"
    assert persisted_json["data"]["notes"] is None
    assert persisted_json["data"]["idempotency_fingerprint"] == "sha256:4a5b6c7d8e9f0123456789abcdef"
    assert "2026-09-19T14:30:00" in persisted_json["data"]["created_at"]
    assert persisted_json["data"]["nested"]["status"] == "pending"
    assert persisted_json["data"]["nested"]["decision"] is None

    # 4. Reload from fresh EventStreamService instance (simulating server restart)
    new_service = EventStreamService(channels=("approval",), data_dir=str(tmp_path))
    reloaded_events = new_service.replay("approval", deque(), None)
    assert len(reloaded_events) == 1
    reloaded = reloaded_events[0]
    assert reloaded["id"] == event_id
    assert reloaded["data"]["stage"] == "approved"
    assert reloaded["data"]["notes"] is None
    assert reloaded["data"]["idempotency_fingerprint"] == "sha256:4a5b6c7d8e9f0123456789abcdef"
    assert "2026-09-19T14:30:00" in reloaded["data"]["created_at"]

    # 5. Format reloaded event to wire format
    formatted = new_service.format_event(reloaded)
    assert f"id: {event_id}" in formatted
    assert "event: approval.state" in formatted
    assert "data: " in formatted
    data_line = next(line for line in formatted.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["data"]["stage"] == "approved"
    assert payload["data"]["notes"] is None
    assert payload["data"]["idempotency_fingerprint"] == "sha256:4a5b6c7d8e9f0123456789abcdef"

    # 6. Consultation store persistence and reload compatibility
    from services.consultation.store import ConsultationStore
    from services.consultation.models import ConsultRequest, ActorRef, ConsultRequestType
    consult_dir = tmp_path / "consultation"
    consult_store = ConsultationStore(str(consult_dir))
    req = ConsultRequest(
        request_id="req-persist-001",
        request_type=ConsultRequestType.STRATEGY_REVIEW,
        requested_by=ActorRef(actor_type="sponsor", actor_id="sponsor-1"),
        target_type="persona",
        target_id="persona-a",
        trace_id="trace-persist-001",
        metadata={
            "fingerprint": "sha256:fingerprint001",
            "stage": TestStage.APPROVED,
            "created_at": test_date.isoformat(),
            "notes": None,
        },
    )
    consult_store.put_request(req)

    reloaded_consult_store = ConsultationStore(str(consult_dir))
    reloaded_req = reloaded_consult_store.get_request("req-persist-001")
    assert reloaded_req is not None
    assert reloaded_req.request_id == "req-persist-001"
    assert reloaded_req.target_id == "persona-a"
    assert reloaded_req.metadata["fingerprint"] == "sha256:fingerprint001"
    assert reloaded_req.metadata["stage"] == "approved"
    assert reloaded_req.metadata["notes"] is None


def test_sse_streaming_done_token_termination(monkeypatch: Any) -> None:
    import sys
    from fastapi.sse import ServerSentEvent
    from fastapi.testclient import TestClient
    from pathlib import Path
    import importlib.util
    _adapter_dir = str(Path(__file__).resolve().parents[3] / "services" / "openclaw-gateway-adapter")
    if _adapter_dir not in sys.path:
        sys.path.insert(0, _adapter_dir)
    _spec = importlib.util.spec_from_file_location("openclaw_adapter_main", Path(_adapter_dir) / "main.py")
    adapter_main = importlib.util.module_from_spec(_spec)
    sys.modules["openclaw_adapter_main"] = adapter_main
    _spec.loader.exec_module(adapter_main)
    adapter_app = adapter_main.app

    # 1. Format-level [DONE] token check
    formatted_done = sse_service.format_event(ServerSentEvent(raw_data="[DONE]"))
    assert formatted_done == "data: [DONE]\n\n"

    done_str = "data: [DONE]\n\n"
    assert sse_service.format_event(done_str) == "data: [DONE]\n\n"

    # 2. Actual adapter HTTP request verifying [DONE] token and HTTP transport termination
    client = TestClient(adapter_app)

    # Test case A: OPERATOR_REQUIRED error stream terminates with [DONE]
    resp = client.post(
        "/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
        json={"mode": "user", "prompt": "hello"},
    )
    assert resp.status_code == 200
    lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
    assert any("OPERATOR_REQUIRED" in line for line in lines)
    assert lines[-1] == "data: [DONE]"

    # Test case B: Delegated codex stream terminates with [DONE]
    from types import SimpleNamespace
    fake_result = SimpleNamespace(
        provider="codex",
        mode="kernel_debug",
        status="success",
        output={"text": "codex test output"},
        redaction={},
        session_id="sess-123",
        metadata={},
    )
    with monkeypatch.context() as m:
        m.setattr(adapter_main._CODEX_RUNTIME, "invoke", lambda *args, **kwargs: fake_result)
        resp2 = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
            headers={"X-Operator-Id": "op-test"},
            json={"mode": "kernel_debug", "prompt": "debug command"},
        )
        assert resp2.status_code == 200
        lines2 = [line.strip() for line in resp2.text.splitlines() if line.strip()]
        assert any("codex test output" in line for line in lines2)
        assert lines2[-1] == "data: [DONE]"

    # Test case C: EventStreamService stream terminates on [DONE] without hanging
    channel = "ask"

    async def _test_event_stream_done_termination():
        gen = sse_service.stream(channel, _sse_buffers[channel], _sse_subscribers[channel], None)
        task = asyncio.create_task(anext(gen))
        await asyncio.sleep(0.01)

        # Enqueue [DONE] token
        for q in list(_sse_subscribers[channel]):
            q.put_nowait(ServerSentEvent(raw_data="[DONE]"))

        chunk = await task
        assert chunk == "data: [DONE]\n\n" or (isinstance(chunk, ServerSentEvent) and chunk.raw_data == "[DONE]")

        # Generator must have terminated cleanly: next read raises StopAsyncIteration
        with pytest.raises(StopAsyncIteration):
            await anext(gen)

    asyncio.run(_test_event_stream_done_termination())


def test_mounted_app_sse_replay_and_restart_with_bff_data_dir(tmp_path: Path, monkeypatch: Any) -> None:
    """Mounted-app regression: replay and restart with BFF_DATA_DIR without PANTHEON_BFF_DATA_DIR.

    Verifies production assembly binding:
    1. Publisher (main._publish_event) persists events to $BFF_DATA_DIR/sse_replay/{channel}.jsonl.
    2. The mounted reader route (/api/v1/stream/{channel}) reads from $BFF_DATA_DIR when
       PANTHEON_BFF_DATA_DIR is unset.
    3. Old handler and mounted route return 200 and replay events after Last-Event-ID.
    4. Server restart (cleared in-memory buffer) reloads and replays initial events from disk.
    5. Unknown Last-Event-ID fails closed with 409 SSE_REPLAY_HISTORY_MISSING and file store header.
    """
    monkeypatch.setenv("BFF_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PANTHEON_BFF_DATA_DIR", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_SSE_REPLAY_STORE", "file")
    monkeypatch.setenv("RANKING_STORE_BOOTSTRAP", "0")
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")

    import importlib
    main = importlib.import_module("services.control_plane.bff.main")
    from starlette.testclient import TestClient

    # Ensure buffer is empty before test
    main._sse_buffers["approval"].clear()

    # 1. Publish two events via main._publish_event
    first_id = main._publish_event(
        main._sse_buffers["approval"],
        main._sse_subscribers["approval"],
        "approval.stage.changed",
        {"sequence_no": 1, "note": "first-approval"},
    )
    second_id = main._publish_event(
        main._sse_buffers["approval"],
        main._sse_subscribers["approval"],
        "approval.stage.changed",
        {"sequence_no": 2, "note": "second-approval"},
    )

    # 2. Verify shared replay file exists under BFF_DATA_DIR
    replay_file = tmp_path / "sse_replay" / "approval.jsonl"
    assert replay_file.exists(), f"Replay file should be created under {tmp_path}/sse_replay/approval.jsonl"

    # 3. Verify mounted reader endpoint (/api/v1/stream/{channel}) on main._events_router
    stream_endpoint = next(
        r.endpoint for r in main._events_router.routes if r.path == "/api/v1/stream/{channel}"
    )

    # 3a. Replay from mounted endpoint with last_event_id=first_id: must return 200 and second event
    resp = asyncio.run(stream_endpoint("approval", first_id, AUTH))
    assert resp.headers["X-SSE-Replay-Store"] == "file"
    assert resp.headers["X-SSE-Channel"] == "approval"
    it = resp.body_iterator

    async def _read_chunk(iterator):
        return await asyncio.wait_for(anext(iterator), timeout=2.0)

    chunk = asyncio.run(_read_chunk(it))
    asyncio.run(it.aclose())
    assert second_id in chunk
    assert first_id not in chunk
    assert "second-approval" in chunk

    # 3b. Verify old handler also returns 200 and replays the second event
    old_resp = asyncio.run(main.stream_generic_events("approval", first_id, AUTH))
    assert old_resp.headers["X-SSE-Replay-Store"] == "file"
    old_it = old_resp.body_iterator
    old_chunk = asyncio.run(_read_chunk(old_it))
    asyncio.run(old_it.aclose())
    assert second_id in old_chunk
    assert first_id not in old_chunk

    # 4. Restart simulation: clear in-memory buffers; mounted reader must reload and replay from disk
    main._sse_buffers["approval"].clear()
    assert len(main._sse_buffers["approval"]) == 0

    resp_restart = asyncio.run(stream_endpoint("approval", None, AUTH))
    assert resp_restart.headers["X-SSE-Replay-Store"] == "file"
    it_restart = resp_restart.body_iterator

    async def _read_restart_chunks(iterator):
        c1 = await asyncio.wait_for(anext(iterator), timeout=2.0)
        c2 = await asyncio.wait_for(anext(iterator), timeout=2.0)
        return c1, c2

    chunk1, chunk2 = asyncio.run(_read_restart_chunks(it_restart))
    asyncio.run(it_restart.aclose())
    assert first_id in chunk1
    assert "first-approval" in chunk1
    assert second_id in chunk2
    assert "second-approval" in chunk2

    # 5. Unavailable cursor fails closed with 409 SSE_REPLAY_HISTORY_MISSING on mounted app
    client = TestClient(main.app)
    resp_409 = client.get(
        "/api/v1/stream/approval?last_event_id=evt-nonexistent-cursor",
        headers={"Authorization": AUTH},
    )
    assert resp_409.status_code == 409
    assert resp_409.headers["X-SSE-Replay-Store"] == "file"
    error_payload = resp_409.json()["error"]
    assert error_payload["code"] == "RESOURCE_CONFLICT"
    assert error_payload["details"]["reason"] == "SSE_REPLAY_HISTORY_MISSING"
    assert error_payload["details"]["channel"] == "approval"
    assert error_payload["details"]["lastEventId"] == "evt-nonexistent-cursor"
    assert error_payload["details"]["replayStore"] == "file"

