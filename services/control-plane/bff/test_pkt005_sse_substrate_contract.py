from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
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
