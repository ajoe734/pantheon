import asyncio
import json
import os
import sys


from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from bff.agora.trading_room.router import (
    _tr_publish,
    _trading_room_sse_buffers,
    _trading_room_sse_subscribers,
    create_trading_room_router,
)


def _route(identity):
    router = create_trading_room_router(
        extract_identity=lambda *args, **kwargs: identity,
        require_read_role=lambda value: None,
        bff_error=lambda status, code, message, reason, **kwargs: HTTPException(status, message),
        utc_now=lambda: "2026-07-12T02:00:00Z",
    )
    return next(route.endpoint for route in router.routes if route.path == "/bff/agora/trading-room/stream")


def _decode(chunk):
    data = next(line[6:] for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(data)


def setup_function():
    _trading_room_sse_buffers.clear()
    _trading_room_sse_subscribers.clear()


def test_stream_immediately_acknowledges_with_typed_scoped_event():
    async def run():
        response = await _route({"tenant_id": "tenant-a", "user_id": "user-a"})(authorization="Bearer x")
        assert isinstance(response, StreamingResponse)
        assert response.headers["x-sse-channel"] == "trading-room:tenant-a:user-a"
        iterator = response.body_iterator
        event = _decode(await asyncio.wait_for(iterator.__anext__(), timeout=0.25))
        assert event["type"] == "trading_room.connected"
        assert event["data"]["scope"] == {"tenant_id": "tenant-a", "user_id": "user-a"}
        assert event["data"]["no_order_route_proof"] == "agora_decision_support_only"
        await iterator.aclose()

    asyncio.run(run())


def test_stream_replay_and_live_delivery_are_scope_isolated():
    async def run():
        scope_a = {"tenant_id": "tenant-a", "user_id": "user-a"}
        scope_b = {"tenant_id": "tenant-a", "user_id": "user-b"}
        first_id = _tr_publish(scope_a, "trading_room.decision.recorded", {"decision_event_id": "evt-1"}, "t1")
        _tr_publish(scope_a, "trading_room.decision.recorded", {"decision_event_id": "evt-2"}, "t2")
        _tr_publish(scope_b, "trading_room.decision.recorded", {"decision_event_id": "private-b"}, "t3")

        response = await _route(scope_a)(authorization="Bearer x", last_event_id=first_id)
        iterator = response.body_iterator
        replay = _decode(await iterator.__anext__())
        ack = _decode(await iterator.__anext__())
        assert replay["data"]["decision_event_id"] == "evt-2"
        assert ack["type"] == "trading_room.connected"
        assert "private-b" not in json.dumps([replay, ack])

        _tr_publish(scope_b, "trading_room.decision.recorded", {"decision_event_id": "private-live-b"}, "t4")
        _tr_publish(scope_a, "trading_room.decision.recorded", {"decision_event_id": "evt-live-a"}, "t5")
        live = _decode(await asyncio.wait_for(iterator.__anext__(), timeout=0.25))
        assert live["data"]["decision_event_id"] == "evt-live-a"
        await iterator.aclose()

    asyncio.run(run())
