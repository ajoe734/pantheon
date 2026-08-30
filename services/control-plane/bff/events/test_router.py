"""Tests for BFF Events domain router (ACG-01-004, ACG-01-005)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import FastAPI
from fastapi.testclient import TestClient
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from events.router import create_events_router


def _make_mock_read_store(events_list: Optional[List[Dict[str, Any]]] = None, status: str = "ok"):
    class MockReadStore:
        def __init__(self):
            self.events = list(events_list or [])
            self.status = status

        def list_governance_audit_events(
            self,
            actor: Optional[str] = None,
            action_types: Optional[List[str]] = None,
            target_type: Optional[str] = None,
            **kwargs,
        ) -> List[Dict[str, Any]]:
            res = self.events
            if actor:
                res = [e for e in res if e.get("actor") == actor]
            if action_types:
                res = [e for e in res if e.get("action_type") in action_types or e.get("type") in action_types]
            if target_type:
                res = [e for e in res if e.get("target_type") == target_type]
            return res

        def dataset_source(self, ds: str) -> str:
            return self.status

    return MockReadStore()


def test_events_router_routes_uniqueness():
    router = create_events_router()
    routes = [(getattr(r, "methods", set()), getattr(r, "path", "")) for r in router.routes]
    events_get_routes = [r for r in routes if r[1] == "/bff/events" and "GET" in r[0]]
    stream_get_routes = [r for r in routes if r[1] == "/bff/events/stream" and "GET" in r[0]]

    assert len(events_get_routes) == 1
    assert len(stream_get_routes) == 1
    assert len(router.routes) == 14


def test_events_router_list_events_and_filtering():
    sample_events = [
        {"id": "e1", "action_type": "ApproveDeployment", "actor": "op-1", "target_type": "deployment"},
        {"id": "e2", "action_type": "RejectDeployment", "actor": "op-2", "target_type": "deployment"},
        {"id": "e3", "action_type": "CreateStrategy", "actor": "op-1", "target_type": "strategy"},
    ]
    store = _make_mock_read_store(sample_events)
    router = create_events_router(get_read_store=lambda: store)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. List all
    resp = client.get("/bff/events", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 3
    assert "meta" in data
    assert "page_info" in data

    # 2. Filter by event_type
    resp = client.get("/bff/events?event_type=ApproveDeployment", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "e1"

    # 3. Filter by actor
    resp = client.get("/bff/events?actor=op-2", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "e2"

    # 4. Filter by target_type
    resp = client.get("/bff/events?target_type=strategy", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "e3"


def test_events_router_list_degraded_when_unavailable():
    sample_events = [{"id": "e1", "action_type": "ApproveDeployment"}]
    store = _make_mock_read_store(sample_events, status="unavailable")
    router = create_events_router(
        get_read_store=lambda: store,
        dataset_surface_status=lambda ds, **kw: {"status": "unavailable", "source": "missing"},
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/bff/events", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []


def test_events_router_stream_unauthenticated_liveness():
    async def _test_frontend_stream(channels):
        yield "id: evt-1\ndata: {\"channels\": [\"system\"]}\n\n"

    router = create_events_router(frontend_bff_event_stream=_test_frontend_stream)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/bff/events/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-sse-channel"] == "bff"
    assert resp.headers["x-sse-replay-supported"] == "false"


def test_events_router_stream_authenticated_channel():
    from starlette.responses import StreamingResponse

    def _test_handle_sse(channel, buffer, subs, last_id, extra_headers=None):
        async def _gen():
            yield f"id: evt-1\nevent: message\ndata: {{}}\n\n"
        headers = {
            "Content-Type": "text/event-stream",
            "X-SSE-Channel": channel,
            "X-SSE-Replay-Supported": "true",
        }
        if extra_headers:
            headers.update(extra_headers)
        return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)

    router = create_events_router(handle_sse_stream=_test_handle_sse)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get(
        "/bff/events/stream?channel=governance",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-sse-channel"] == "governance"
    assert resp.headers["x-sse-replay-supported"] == "true"


def test_events_router_stream_invalid_channel():
    router = create_events_router()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get(
        "/bff/events/stream?channel=invalid_unknown_channel",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp.status_code == 400
    data = resp.json()
    err = data["error"] if "error" in data else data.get("detail", {}).get("error", {})
    assert err.get("code") == "VALIDATION_FAILED"
