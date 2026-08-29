"""SSE stream Workshop route: GET .../stream (AG-BE-SW-004).

Split out of the former single-file strategy_workshop/router.py factory
(ACG-06-004). Route body below is unchanged from the original
implementation; only the surrounding closure scaffolding (this
build_stream_router wrapper binding the shared admission context and the
public events module) is new.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from .._common import _raise_cross_user_forbidden
from ..events import (
    _WS_SSE_BUFFER_SIZE,
    _ws_event_id,
    _ws_get_buffer,
    _ws_get_subscribers,
    _ws_replay_after,
    _ws_sse_format,
)


def build_stream_router(
    *,
    store: Any,
    utc_now: Callable[[], str],
    bff_error: Callable[..., HTTPException],
    ctx: Any,
) -> APIRouter:
    router = APIRouter(tags=["agora-workshop"])
    _scope = ctx.scope

    @router.get("/bff/agora/workshops/{workshop_id}/stream")
    async def stream_workshop(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """SSE aggregate of workshop events (AG-BE-SW-004).

        Streams workshop.connected (ack), workshop.message.ack, workshop.completeness.updated,
        research.run.progress, workshop.version.created, and workshop.openclaw.degraded
        events.  Supports reconnection via Last-Event-ID.  First event is always
        workshop.connected, delivered immediately (< 2 s guarantee).
        OpenClaw degradation is surfaced as OPENCLAW_UPSTREAM_DEGRADED in event data.
        """
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )

        async def _event_stream() -> AsyncGenerator[str, None]:
            q: asyncio.Queue = asyncio.Queue(maxsize=500)
            subs = _ws_get_subscribers(workshop_id)
            subs.append(q)
            try:
                # Replay missed events when client reconnects with Last-Event-ID
                if last_event_id:
                    for replayed_evt in _ws_replay_after(workshop_id, last_event_id, store=store):
                        yield _ws_sse_format(replayed_evt)

                # Immediate ack on connect — satisfies the < 2s first-acknowledgement requirement.
                # §8.2 audit: trace_id from the session's openclaw_session_id.
                ack_event_id = _ws_event_id()
                ack_event: Dict[str, Any] = {
                    "id": ack_event_id,
                    "type": "workshop.connected",
                    "timestamp": utc_now(),
                    "data": {
                        "workshop_id": workshop_id,
                        "status": session.get("status", "open"),
                        "lock_version": session.get("lock_version", 1),
                        "trace_id": session.get("openclaw_session_id"),
                    },
                }
                _ws_get_buffer(workshop_id).append((ack_event_id, ack_event))
                yield _ws_sse_format(ack_event)

                # Stream live events until the client disconnects
                while True:
                    try:
                        evt = await asyncio.wait_for(q.get(), timeout=30.0)
                        yield _ws_sse_format(evt)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                if q in subs:
                    subs.remove(q)

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-SSE-Channel": f"workshop:{workshop_id}",
                "X-SSE-Replay-Supported": "true",
                "X-SSE-Replay-Window-Events": str(_WS_SSE_BUFFER_SIZE),
                "X-SSE-Resync-Routes": f"/bff/agora/workshops/{workshop_id}",
            },
        )
    return router
