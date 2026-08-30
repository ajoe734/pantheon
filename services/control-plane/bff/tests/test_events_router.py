"""Focused contract tests for the extracted Events subscription router."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from events.router import create_events_router
from events.service import EventStreamService


def _endpoint(router, path: str):
    return next(route.endpoint for route in router.routes if route.path == path)


def test_events_router_owns_the_fourteen_event_decorators() -> None:
    router = create_events_router()

    paths = {route.path for route in router.routes}
    assert len(router.routes) == 14
    assert paths == {
        "/bff/events",
        "/bff/events/stream",
        "/api/v1/stream/{channel}",
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
        "/api/v1/internal/sse/publish",
    }


def test_internal_publish_delivers_to_inferred_outbox_channel() -> None:
    service = EventStreamService(channels=("approval", "ask", "journal", "runtime", "system"))
    router = create_events_router(event_stream_service=service)
    publish = _endpoint(router, "/api/v1/internal/sse/publish")

    result = asyncio.run(
        publish(
            event_type="approval.created",
            channel=None,
            runtime_id=None,
            incident_id=None,
            payload={"approval_id": "approval-1"},
            authorization="Bearer op-1:operator",
        )
    )

    event_id = result["event_id"]
    event = service.buffers["approval"][0][1]
    assert result == {"event_id": event_id, "status": "published"}
    assert event["id"] == event_id
    assert event["type"] == "approval.created"
    assert event["data"] == {"approval_id": "approval-1"}


def test_generic_and_compatibility_streams_share_replay_headers() -> None:
    service = EventStreamService(channels=("approval", "inbox", "system"))
    router = create_events_router(event_stream_service=service)
    generic = _endpoint(router, "/api/v1/stream/{channel}")
    inbox = _endpoint(router, "/bff/sse/notifications")

    generic_response = asyncio.run(generic("inbox", None, "Bearer op-1:operator"))
    inbox_response = asyncio.run(inbox(None, "Bearer op-1:operator"))

    assert generic_response.headers["X-SSE-Channel"] == "inbox"
    assert inbox_response.headers["X-SSE-Channel"] == "inbox"
    assert generic_response.headers["X-SSE-Replay-Supported"] == "true"
    assert inbox_response.headers["X-SSE-Replay-Window-Events"] == "500"


def test_generic_stream_rejects_channels_outside_the_injected_catalog() -> None:
    router = create_events_router(event_stream_service=EventStreamService(channels=("system",)))
    generic = _endpoint(router, "/api/v1/stream/{channel}")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(generic("approval", None, "Bearer op-1:operator"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "VALIDATION_FAILED"
