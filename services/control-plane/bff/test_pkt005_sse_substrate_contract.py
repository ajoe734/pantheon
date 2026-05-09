from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from models import (
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
    ObjectType,
    SseEventEnvelope,
)


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


@pytest.fixture(autouse=True)
def clean_sse_buffers():
    for buffer in bff_main._sse_buffers.values():
        buffer.clear()
    for subscribers in bff_main._sse_subscribers.values():
        subscribers.clear()
    bff_main._incident_events.clear()
    bff_main._incident_subscribers.clear()
    yield
    for buffer in bff_main._sse_buffers.values():
        buffer.clear()
    for subscribers in bff_main._sse_subscribers.values():
        subscribers.clear()
    bff_main._incident_events.clear()
    bff_main._incident_subscribers.clear()


def test_final_sse_channel_catalog_contains_approval_and_ask() -> None:
    assert bff_main.SSE_CHANNEL_CATALOG == FINAL_CHANNEL_CATALOG
    assert "approval" in bff_main.SSE_CHANNELS
    assert "ask" in bff_main.SSE_CHANNELS
    assert "incident" not in bff_main.SSE_CHANNELS


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
    first_id = bff_main._publish_event(
        bff_main._sse_buffers[channel],
        bff_main._sse_subscribers[channel],
        "approval.created",
        {"approval_id": "appr-final-sse-001"},
    )
    second_id = bff_main._publish_event(
        bff_main._sse_buffers[channel],
        bff_main._sse_subscribers[channel],
        "approval.decided",
        {"approval_id": "appr-final-sse-001", "outcome": "approved"},
    )

    replayed = bff_main._replay_from(bff_main._sse_buffers[channel], first_id)

    assert [event["id"] for event in replayed] == [second_id]
    assert replayed[0]["type"] == "approval.decided"
    assert replayed[0]["data"]["outcome"] == "approved"
    assert "event: approval.decided" in bff_main._sse_format(replayed[0])


def test_replay_unavailable_uses_final_error_envelope_with_resync_metadata() -> None:
    client = TestClient(bff_main.app)

    response = client.get(
        "/api/v1/stream/approval?last_event_id=evt-final-sse-missing",
        headers={"Authorization": AUTH},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    error = detail["error"]
    assert error["code"] == "SSE_REPLAY_UNAVAILABLE"
    assert error["details"]["reason"] == "SSE_REPLAY_HISTORY_MISSING"
    assert error["details"]["channel"] == "approval"
    assert error["details"]["lastEventId"] == "evt-final-sse-missing"
    assert error["details"]["replaySupported"] is True
    assert error["details"]["replayWindowEvents"] == 500
    assert error["details"]["replayStore"] == "in-memory"
    assert error["details"]["resyncRoutes"] == ["/bff/approvals", "/bff/v5/interventions"]


def test_approval_and_ask_stream_routes_publish_replay_metadata_headers() -> None:
    for route, channel, resync in [
        (bff_main.stream_approval_events, "approval", "/bff/approvals,/bff/v5/interventions"),
        (bff_main.stream_ask_events, "ask", "/bff/agora/ask/sessions/{id}"),
    ]:
        response = asyncio.run(route(last_event_id=None, authorization=AUTH))
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == channel
        assert response.headers["X-SSE-Replay-Supported"] == "true"
        assert response.headers["X-SSE-Replay-Window-Events"] == "500"
        assert response.headers["X-SSE-Replay-Store"] == "in-memory"
        assert response.headers["X-SSE-Resync-Routes"] == resync


def test_execute_plans_sse_compatibility_routes_are_registered() -> None:
    registered_paths = {getattr(route, "path", "") for route in bff_main.app.routes}

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
            lambda: bff_main.bff_events_stream_alias(
                channel="system", last_event_id=None, authorization=AUTH,
            ),
            "system",
        ),
        (lambda: bff_main.bff_sse_notifications_alias(last_event_id=None, authorization=AUTH), "inbox"),
        (lambda: bff_main.bff_sse_cc_kpi_alias(last_event_id=None, authorization=AUTH), "ranking"),
        (lambda: bff_main.bff_sse_cc_events_alias(last_event_id=None, authorization=AUTH), "loop"),
        (
            lambda: bff_main.bff_sse_job_progress_alias(
                jobId="job-final-sse-001", last_event_id=None, authorization=AUTH,
            ),
            "tool",
        ),
        (lambda: bff_main.bff_sse_alerts_alias(last_event_id=None, authorization=AUTH), "sentinel"),
        (
            lambda: bff_main.bff_sse_incident_timeline_alias(
                incidentId="inc-final-sse-001", last_event_id=None, authorization=AUTH,
            ),
            "journal",
        ),
        (lambda: bff_main.bff_sse_deployment_events_alias(last_event_id=None, authorization=AUTH), "artifact"),
        (lambda: bff_main.bff_sse_review_updates_alias(last_event_id=None, authorization=AUTH), "approval"),
        (lambda: bff_main.bff_sse_agora_signals_alias(last_event_id=None, authorization=AUTH), "signal"),
        (
            lambda: bff_main.bff_sse_agora_session_alias(
                sessionId="ask-final-sse-001", last_event_id=None, authorization=AUTH,
            ),
            "ask",
        ),
    ]

    for response_factory, expected_channel in route_factories:
        response = asyncio.run(response_factory())
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == expected_channel
        assert response.headers["X-SSE-Replay-Supported"] == "true"
        assert response.headers["X-SSE-Replay-Window-Events"] == "500"
        assert response.headers["X-SSE-Replay-Store"] == "in-memory"


async def _first_sse_payload(response) -> dict:
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
    bff_main._publish_event(
        bff_main._sse_buffers["inbox"],
        bff_main._sse_subscribers["inbox"],
        "inbox.notification.created",
        {"notification_id": "note-final-sse-001"},
    )

    async def compare_alias_to_generic() -> tuple[dict, dict]:
        generic_response = await bff_main.stream_generic_events(
            channel="inbox", last_event_id=None, authorization=AUTH,
        )
        alias_response = await bff_main.bff_sse_notifications_alias(
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
            bff_main.bff_sse_notifications_alias(
                last_event_id="evt-final-sse-missing",
                authorization=AUTH,
            )
        )

    assert exc_info.value.status_code == 409
    error = exc_info.value.detail["error"]
    assert error["code"] == "SSE_REPLAY_UNAVAILABLE"
    assert error["details"]["channel"] == "inbox"
    assert error["details"]["lastEventId"] == "evt-final-sse-missing"


def test_bff_events_stream_matches_lovable_shell_schema_without_auth() -> None:
    response = asyncio.run(
        bff_main.stream_bff_events(
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
    assert "Accept-Language" in bff_main._CORS_ALLOW_HEADERS
    assert "X-BFF-Api-Version" in bff_main._CORS_ALLOW_HEADERS
    assert "X-Request-Id" in bff_main._CORS_ALLOW_HEADERS


def test_internal_publish_infers_approval_and_ask_channels() -> None:
    client = TestClient(bff_main.app)

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
    approval_event = bff_main._sse_buffers["approval"][0][1]
    ask_event = bff_main._sse_buffers["ask"][0][1]
    assert approval_event["id"] == approval_response.json()["event_id"]
    assert approval_event["type"] == "approval.created"
    assert approval_event["data"]["approval_id"] == "appr-final-sse-001"
    assert ask_event["id"] == ask_response.json()["event_id"]
    assert ask_event["type"] == "ask.tool.called"
    assert ask_event["data"]["tool_name"] == "search"


def test_invalid_generic_channel_returns_catalog_validation_error() -> None:
    client = TestClient(bff_main.app)

    response = client.get("/api/v1/stream/not-a-channel", headers={"Authorization": AUTH})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "INVALID_REQUEST"
    assert "approval" in detail["error"]["details"]["reason"]
    assert "ask" in detail["error"]["details"]["reason"]


def test_ask_replay_payload_is_json_serializable_sse_data() -> None:
    channel = "ask"
    event_id = bff_main._publish_event(
        bff_main._sse_buffers[channel],
        bff_main._sse_subscribers[channel],
        "ask.message.delta",
        {"session_id": "ask-final-sse-001", "message_id": "msg-1", "delta": "hello"},
    )
    event = bff_main._sse_buffers[channel][0][1]

    assert event["id"] == event_id
    formatted = bff_main._sse_format(event)
    data_line = next(line for line in formatted.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["data"]["delta"] == "hello"
