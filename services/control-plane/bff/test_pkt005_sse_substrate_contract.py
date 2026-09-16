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
