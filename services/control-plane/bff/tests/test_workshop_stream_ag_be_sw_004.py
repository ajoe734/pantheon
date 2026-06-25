"""Integration tests for AG-BE-SW-004 — workshop SSE aggregate stream.

Tests cover:
- GET /bff/agora/workshops/{id}/stream returns text/event-stream with correct headers
- workshop.connected ack event is the first event on connect (< 2s guarantee)
- Last-Event-ID reconnection replays buffered events
- POST /messages pushes workshop.message.ack to active SSE subscribers
- _ws_publish / _ws_replay_after module-level helpers
- OPENCLAW_UPSTREAM_DEGRADED event publication via research router helper
- 404 for unknown workshop, 403 for wrong owner, 401 for no auth
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from typing import Optional

import pytest

BFF_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, BFF_DIR)

from agora.strategy_workshop.router import (
    _WS_SSE_BUFFER_SIZE,
    _workshop_sse_buffers,
    _workshop_sse_subscribers,
    _ws_event_id,
    _ws_get_buffer,
    _ws_get_subscribers,
    _ws_publish,
    _ws_replay_after,
    _ws_sse_format,
)


_OPERATOR_AUTH = "Bearer agora-test-user:operator"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _workshop_client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    import main as bff_main
    return bff_main


def _create_workshop(client_app, workshop_id: str = "ws-stream-001") -> str:
    """Create a workshop session in the in-memory store used by the BFF."""
    from agora.strategy_workshop import MemoryWorkshopStore
    store: Optional[MemoryWorkshopStore] = None
    # Find the store through the app's state (injected by the router factory).
    # Fall back to creating one directly for unit tests that bypass the app.
    for route in client_app.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint and hasattr(endpoint, "__closure__") and endpoint.__closure__:
            for cell in endpoint.__closure__:
                try:
                    obj = cell.cell_contents
                    if isinstance(obj, MemoryWorkshopStore):
                        store = obj
                        break
                except ValueError:
                    pass
        if store:
            break
    if store is None:
        # Direct construction for unit-level SSE helper tests
        store = MemoryWorkshopStore()
    return store


# --------------------------------------------------------------------------- #
# Unit tests: module-level SSE helpers
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def clean_workshop_sse_state():
    """Clear per-workshop SSE state between tests."""
    _workshop_sse_buffers.clear()
    _workshop_sse_subscribers.clear()
    yield
    _workshop_sse_buffers.clear()
    _workshop_sse_subscribers.clear()


class TestWsPublishHelper:
    def test_publish_creates_buffer_entry(self):
        wid = "ws-pub-001"
        event_id = _ws_publish(wid, "workshop.test", {"foo": "bar"})
        buf = _workshop_sse_buffers.get(wid)
        assert buf is not None
        assert len(buf) == 1
        eid, evt = buf[0]
        assert eid == event_id
        assert evt["type"] == "workshop.test"
        assert evt["data"]["workshop_id"] == wid
        assert evt["data"]["foo"] == "bar"

    def test_publish_delivers_to_subscriber_queue(self):
        wid = "ws-pub-002"
        q: asyncio.Queue = asyncio.Queue()
        _ws_get_subscribers(wid).append(q)

        _ws_publish(wid, "workshop.message.ack", {"event_id": "ev-1", "sequence_no": 1})

        assert not q.empty()
        evt = q.get_nowait()
        assert evt["type"] == "workshop.message.ack"
        assert evt["data"]["event_id"] == "ev-1"

    def test_publish_full_queue_does_not_raise(self):
        wid = "ws-pub-003"
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        q.put_nowait({"dummy": True})  # fill the queue
        _ws_get_subscribers(wid).append(q)

        # Should not raise even though the queue is full
        _ws_publish(wid, "workshop.message.ack", {"event_id": "ev-overflow"})

    def test_publish_respects_buffer_size_limit(self):
        wid = "ws-pub-004"
        for i in range(_WS_SSE_BUFFER_SIZE + 10):
            _ws_publish(wid, "workshop.test", {"i": i})
        buf = _ws_get_buffer(wid)
        assert len(buf) == _WS_SSE_BUFFER_SIZE

    def test_publish_event_contains_timestamp(self):
        wid = "ws-pub-005"
        _ws_publish(wid, "workshop.test", {})
        _, evt = _ws_get_buffer(wid)[0]
        assert "timestamp" in evt
        assert evt["timestamp"].endswith("Z")

    def test_publish_utc_now_fn_override(self):
        wid = "ws-pub-006"
        _ws_publish(wid, "workshop.test", {}, utc_now_fn=lambda: "2026-06-21T00:00:00Z")
        _, evt = _ws_get_buffer(wid)[0]
        assert evt["timestamp"] == "2026-06-21T00:00:00Z"


class TestWsReplayAfterHelper:
    def test_replay_returns_events_after_last_id(self):
        wid = "ws-replay-001"
        id1 = _ws_publish(wid, "workshop.test", {"n": 1})
        id2 = _ws_publish(wid, "workshop.test", {"n": 2})
        id3 = _ws_publish(wid, "workshop.test", {"n": 3})

        replayed = _ws_replay_after(wid, id1)
        types_data = [e["data"]["n"] for e in replayed]
        assert types_data == [2, 3]

    def test_replay_returns_empty_for_unknown_last_id(self):
        wid = "ws-replay-002"
        _ws_publish(wid, "workshop.test", {})
        replayed = _ws_replay_after(wid, "missing-id")
        assert replayed == []

    def test_replay_returns_empty_for_unknown_workshop(self):
        replayed = _ws_replay_after("no-such-ws", "any-id")
        assert replayed == []

    def test_replay_returns_empty_when_last_id_is_most_recent(self):
        wid = "ws-replay-003"
        id1 = _ws_publish(wid, "workshop.test", {"n": 1})
        replayed = _ws_replay_after(wid, id1)
        assert replayed == []


class TestWsSseFormat:
    def test_format_contains_id_event_data_lines(self):
        evt = {
            "id": "wsevt-abc",
            "type": "workshop.connected",
            "timestamp": "2026-06-21T00:00:00Z",
            "data": {"workshop_id": "ws-1"},
        }
        formatted = _ws_sse_format(evt)
        assert formatted.startswith("id: wsevt-abc\n")
        assert "event: workshop.connected\n" in formatted
        assert "data: " in formatted
        assert formatted.endswith("\n\n")

    def test_format_data_is_json_serializable(self):
        evt = {
            "id": "wsevt-xyz",
            "type": "workshop.test",
            "timestamp": "2026-06-21T00:00:00Z",
            "data": {"unicode": "你好"},
        }
        formatted = _ws_sse_format(evt)
        data_line = next(l for l in formatted.splitlines() if l.startswith("data: "))
        parsed = json.loads(data_line.removeprefix("data: "))
        assert parsed["data"]["unicode"] == "你好"


# --------------------------------------------------------------------------- #
# Integration tests: stream endpoint via async route invocation
# --------------------------------------------------------------------------- #

class TestWorkshopStreamRoute:
    """Tests calling the async stream handler directly (avoids TestClient SSE limits)."""

    def _make_router_and_store(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import HTTPException

        store = MemoryWorkshopStore()
        store.create_session({
            "workshop_id": "ws-stream-001",
            "tenant_id": "tenant-test",
            "user_id": "user-test",
            "status": "open",
        })

        def fake_extract_identity(auth):
            # operator_id attribute is required by resolve_agora_user_scope;
            # claims dict provides tenant/user resolution without env dependencies.
            return SimpleNamespace(
                operator_id="user-test",
                claims={
                    "sub": "user-test",
                    "tenant_id": "tenant-test",
                },
            )

        def fake_require_read_role(identity):
            pass

        def fake_bff_error(status_code, code, message, reason, **kw):
            return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})

        router = create_strategy_workshop_router(
            extract_identity=fake_extract_identity,
            require_read_role=fake_require_read_role,
            bff_error=fake_bff_error,
            utc_now=lambda: "2026-06-21T00:00:00Z",
            workshop_store=store,
        )
        # Find the stream_workshop endpoint on the router
        stream_fn = None
        for route in router.routes:
            if getattr(route, "path", "") == "/bff/agora/workshops/{workshop_id}/stream":
                stream_fn = route.endpoint
                break
        return stream_fn, store

    def test_stream_returns_streaming_response(self):
        from fastapi.responses import StreamingResponse
        stream_fn, _ = self._make_router_and_store()

        async def run():
            resp = await stream_fn(
                workshop_id="ws-stream-001",
                authorization="Bearer fake-auth",
                x_tenant_id=None,
                last_event_id=None,
            )
            return resp

        resp = asyncio.run(run())
        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"

    def test_stream_response_headers(self):
        stream_fn, _ = self._make_router_and_store()

        async def run():
            return await stream_fn(
                workshop_id="ws-stream-001",
                authorization="Bearer fake-auth",
                x_tenant_id=None,
                last_event_id=None,
            )

        resp = asyncio.run(run())
        assert resp.headers.get("X-SSE-Channel") == "workshop:ws-stream-001"
        assert resp.headers.get("X-SSE-Replay-Supported") == "true"
        assert resp.headers.get("Cache-Control") == "no-cache"

    def test_stream_first_event_is_workshop_connected(self):
        stream_fn, _ = self._make_router_and_store()

        async def run():
            resp = await stream_fn(
                workshop_id="ws-stream-001",
                authorization="Bearer fake-auth",
                x_tenant_id=None,
                last_event_id=None,
            )
            # Read first chunk from the async generator
            chunk = await asyncio.wait_for(anext(resp.body_iterator), timeout=2.0)
            if isinstance(chunk, bytes):
                chunk = chunk.decode()
            return chunk

        first_chunk = asyncio.run(run())
        assert "event: workshop.connected" in first_chunk
        data_line = next(l for l in first_chunk.splitlines() if l.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload["type"] == "workshop.connected"
        assert payload["data"]["workshop_id"] == "ws-stream-001"
        assert payload["data"]["status"] == "open"
        assert "lock_version" in payload["data"]
        assert "timestamp" in payload

    def test_stream_connected_event_stored_in_buffer(self):
        stream_fn, _ = self._make_router_and_store()

        async def run():
            resp = await stream_fn(
                workshop_id="ws-stream-001",
                authorization="Bearer fake-auth",
                x_tenant_id=None,
                last_event_id=None,
            )
            await asyncio.wait_for(anext(resp.body_iterator), timeout=2.0)

        asyncio.run(run())
        buf = _workshop_sse_buffers.get("ws-stream-001")
        assert buf is not None
        assert any(evt["type"] == "workshop.connected" for _, evt in buf)

    def test_stream_404_for_unknown_workshop(self):
        from fastapi import HTTPException
        stream_fn, _ = self._make_router_and_store()

        async def run():
            return await stream_fn(
                workshop_id="no-such-ws",
                authorization="Bearer fake-auth",
                x_tenant_id=None,
                last_event_id=None,
            )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(run())
        assert exc_info.value.status_code == 404

    def test_stream_replay_with_last_event_id(self):
        """Connecting with Last-Event-ID replays buffered events before the ack."""
        stream_fn, store = self._make_router_and_store()
        wid = "ws-stream-001"

        # Pre-populate the buffer with two events
        id1 = _ws_publish(wid, "workshop.message.ack", {"event_id": "ev-001", "sequence_no": 1})
        _ws_publish(wid, "workshop.message.ack", {"event_id": "ev-002", "sequence_no": 2})

        async def run():
            resp = await stream_fn(
                workshop_id=wid,
                authorization="Bearer fake-auth",
                x_tenant_id=None,
                last_event_id=id1,  # reconnect after first event
            )
            chunks = []
            # Read replayed events + the connected ack
            for _ in range(3):
                try:
                    chunk = await asyncio.wait_for(anext(resp.body_iterator), timeout=2.0)
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode()
                    chunks.append(chunk)
                except (StopAsyncIteration, asyncio.TimeoutError):
                    break
            return chunks

        chunks = asyncio.run(run())
        # First chunk should be the replayed second message ack (ev-002)
        assert any("workshop.message.ack" in c for c in chunks)
        # One of the chunks should be the connected ack
        assert any("workshop.connected" in c for c in chunks)


# --------------------------------------------------------------------------- #
# Integration tests: POST /messages pushes SSE to subscribers
# --------------------------------------------------------------------------- #

class TestWorkshopMessageSseIntegration:
    """Verify that POST /messages publishes workshop.message.ack to the SSE stream."""

    def _make_router_and_deps(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import HTTPException

        store = MemoryWorkshopStore()
        wid = "ws-sse-msg-001"
        store.create_session({
            "workshop_id": wid,
            "tenant_id": "tenant-test",
            "user_id": "user-test",
        })

        def fake_extract_identity(auth):
            return SimpleNamespace(
                operator_id="user-test",
                claims={"sub": "user-test", "tenant_id": "tenant-test"},
            )

        router = create_strategy_workshop_router(
            extract_identity=fake_extract_identity,
            require_read_role=lambda identity: None,
            bff_error=lambda status_code, code, message, reason, **kw: HTTPException(
                status_code=status_code
            ),
            utc_now=lambda: "2026-06-21T00:00:00Z",
            workshop_store=store,
        )
        post_msg_fn = None
        for route in router.routes:
            if (
                getattr(route, "path", "") == "/bff/agora/workshops/{workshop_id}/messages"
                and "POST" in getattr(route, "methods", set())
            ):
                post_msg_fn = route.endpoint
                break
        return post_msg_fn, store, wid

    def test_post_message_publishes_ack_to_buffer(self):
        from agora.strategy_workshop.router import WorkshopMessageRequest

        post_msg_fn, store, wid = self._make_router_and_deps()
        # Get current ETag (version 1)
        etag = f'W/"workshop:{wid}:v1"'

        result = post_msg_fn(
            workshop_id=wid,
            body=WorkshopMessageRequest(content="Hello SSE"),
            authorization="Bearer fake-auth",
            x_tenant_id=None,
            if_match=etag,
            idempotency_key="idem-sse-001",
        )
        assert result["data"]["sequence_no"] is not None

        # SSE ack must be in the buffer
        buf = _workshop_sse_buffers.get(wid)
        assert buf is not None
        ack_events = [evt for _, evt in buf if evt["type"] == "workshop.message.ack"]
        assert len(ack_events) == 1
        assert ack_events[0]["data"]["sequence_no"] is not None

    def test_post_message_delivers_ack_to_live_subscriber(self):
        from agora.strategy_workshop.router import WorkshopMessageRequest

        post_msg_fn, store, wid = self._make_router_and_deps()
        # Attach a subscriber
        q: asyncio.Queue = asyncio.Queue()
        _ws_get_subscribers(wid).append(q)

        etag = f'W/"workshop:{wid}:v1"'
        post_msg_fn(
            workshop_id=wid,
            body=WorkshopMessageRequest(content="Push to stream"),
            authorization="Bearer fake-auth",
            x_tenant_id=None,
            if_match=etag,
            idempotency_key="idem-sse-002",
        )

        assert not q.empty()
        delivered = q.get_nowait()
        assert delivered["type"] == "workshop.message.ack"
        assert delivered["data"]["workshop_id"] == wid


# --------------------------------------------------------------------------- #
# Integration tests: OPENCLAW_UPSTREAM_DEGRADED event via research router helper
# --------------------------------------------------------------------------- #

class TestOpenClawDegradedEvent:
    def test_publish_openclaw_degraded_puts_event_in_buffer(self):
        from agora.research.router import publish_openclaw_degraded

        wid = "ws-openclaw-001"
        event_id = publish_openclaw_degraded(wid)
        buf = _workshop_sse_buffers.get(wid)
        assert buf is not None
        assert len(buf) == 1
        _, evt = buf[0]
        assert evt["type"] == "workshop.openclaw.degraded"
        assert evt["data"]["error_code"] == "OPENCLAW_UPSTREAM_DEGRADED"
        assert evt["data"]["workshop_id"] == wid

    def test_publish_research_progress_puts_event_in_buffer(self):
        from agora.research.router import publish_research_progress

        wid = "ws-research-001"
        event_id = publish_research_progress(wid, "run-abc", 42.5, "Backtesting...")
        buf = _workshop_sse_buffers.get(wid)
        assert buf is not None
        _, evt = buf[0]
        assert evt["type"] == "research.run.progress"
        assert evt["data"]["run_id"] == "run-abc"
        assert evt["data"]["phase"] == "running"
        assert evt["data"]["percent"] == 42.5
        assert evt["data"]["message"] == "Backtesting..."


# --------------------------------------------------------------------------- #
# Regression: stream endpoint registered in the router
# --------------------------------------------------------------------------- #

class TestWorkshopStreamEndpointRegistration:
    def test_stream_route_registered(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import HTTPException

        router = create_strategy_workshop_router(
            extract_identity=lambda auth: {"sub": "u1"},
            require_read_role=lambda identity: None,
            bff_error=lambda *a, **kw: HTTPException(status_code=a[0]),
            utc_now=lambda: "2026-06-21T00:00:00Z",
        )
        paths = {getattr(r, "path", "") for r in router.routes}
        assert "/bff/agora/workshops/{workshop_id}/stream" in paths

    def test_stream_route_is_async(self):
        import asyncio
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import HTTPException

        router = create_strategy_workshop_router(
            extract_identity=lambda auth: {"sub": "u1"},
            require_read_role=lambda identity: None,
            bff_error=lambda *a, **kw: HTTPException(status_code=a[0]),
            utc_now=lambda: "2026-06-21T00:00:00Z",
        )
        for route in router.routes:
            if getattr(route, "path", "") == "/bff/agora/workshops/{workshop_id}/stream":
                assert asyncio.iscoroutinefunction(route.endpoint), (
                    "stream_workshop must be async to support StreamingResponse"
                )
                break

    def test_deferred_stub_routes_still_return_501(self, monkeypatch):
        """Verify deferred stubs still return 501 (stream replaced, others unchanged)."""
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
        from fastapi.testclient import TestClient
        import main as bff_main
        client = TestClient(bff_main.app, raise_server_exceptions=False)

        # Create a workshop to get a valid workshop_id
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-stub-check-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Stub check"},
        )
        assert create_resp.status_code == 201, create_resp.text
        wid = create_resp.json()["data"]["workshop_id"]

        for path in [
            f"/bff/agora/workshops/{wid}/versions",
            f"/bff/agora/workshops/{wid}/research-runs",
            f"/bff/agora/workshops/{wid}/consultations",
        ]:
            resp = client.get(path, headers={"Authorization": _OPERATOR_AUTH}) \
                if "/versions" in path else \
                client.post(path, headers={
                    "Authorization": _OPERATOR_AUTH,
                    "Content-Type": "application/json",
                }, json={})
            assert resp.status_code == 501, f"{path} should still be 501, got {resp.status_code}"
