"""Agora trading-room decision events and SSE stream routes."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from .common import (
    TradingRoomRouteContext,
    TraderDecisionRequest,
    TradingDecisionEvent,
    _TR_SSE_BUFFER_SIZE,
    _decision_event_etag,
    _stable_hash,
    _tr_buffer,
    _tr_event_id,
    _tr_replay_after,
    _tr_scope_key,
    _tr_sse_format,
    _tr_subscribers,
    _workspace_scope,
)


def build_decisions_router(ctx: TradingRoomRouteContext) -> APIRouter:
    """Trading-room decision events, trader decision recording, and SSE stream subrouter."""
    router = APIRouter()

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/decision-events
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/decision-events")
    def list_trading_decision_events(
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        event_kind: Optional[str] = Query(
            default=None,
            description="Filter by event kind: entry | add | reduce | exit | review",
        ),
        state: Optional[str] = Query(default=None, description="Filter by lifecycle state"),
        page_size: int = Query(default=20, ge=1, le=100),
        next_page_token: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        """List decision-event queue, filterable by event_kind and state."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)

        page = ctx.service.list_decision_events(
            identity=identity,
            event_kind=event_kind,
            state=state,
            page_size=page_size,
            next_page_token=next_page_token,
        )
        response.headers["ETag"] = f'"tr-decision-page:{_stable_hash(page)}"'
        return {
            "items": [{**event, "etag": _decision_event_etag(event)} for event in page["items"]],
            "page_info": page["page_info"],
            "meta": ctx._meta(),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/decision-events/{decision_event_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/decision-events/{decision_event_id}")
    def get_trading_decision_event(
        decision_event_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        """Return a single TradingDecisionEvent by ID."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)

        event = ctx.service.get_decision_event(decision_event_id, identity)
        response.headers["ETag"] = _decision_event_etag(event)
        return event

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/decision-events/{decision_event_id}/decisions", status_code=201)
    def decide_trading_event(
        decision_event_id: str,
        body: TraderDecisionRequest,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        """Record a trader decision against a pending decision event.

        Allowed decisions: approve | reject | defer | modify
        approve/modify creates and persists a TradingIntent.
        reject/defer are retained as Shadow/Learn evidence subject to consent policy.
        This route NEVER routes live orders.
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)
        idem_key = ctx._require_idempotency_key(idempotency_key)
        ctx._require_if_match(if_match)
        request_id = ctx._require_x_request_id(x_request_id)

        data = ctx.service.record_trader_decision(
            decision_event_id=decision_event_id,
            body=body,
            identity=identity,
            idempotency_key=idem_key,
            x_request_id=request_id,
            if_match=if_match,
        )

        return {
            "status": "completed",
            "data": data,
            "meta": ctx._meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/stream
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/stream")
    async def stream_trading_room(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """Typed, replayable SSE stream isolated to the authenticated user scope."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        scope = _workspace_scope(identity)
        scope_key = _tr_scope_key(scope)

        async def _event_stream() -> AsyncGenerator[str, None]:
            queue: asyncio.Queue = asyncio.Queue(maxsize=500)
            subscribers = _tr_subscribers(scope_key)
            subscribers.append(queue)
            try:
                if last_event_id:
                    for event in _tr_replay_after(scope_key, last_event_id):
                        yield _tr_sse_format(event)

                ack_id = _tr_event_id()
                ack = {
                    "id": ack_id,
                    "type": "trading_room.connected",
                    "timestamp": ctx.utc_now(),
                    "data": {
                        "scope": {"tenant_id": scope["tenant_id"], "user_id": scope["user_id"]},
                        "status": "ready",
                        "no_order_route_proof": "agora_decision_support_only",
                    },
                }
                _tr_buffer(scope_key).append((ack_id, ack))
                yield _tr_sse_format(ack)

                while True:
                    try:
                        yield _tr_sse_format(await asyncio.wait_for(queue.get(), timeout=30.0))
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                if queue in subscribers:
                    subscribers.remove(queue)

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-SSE-Channel": f"trading-room:{scope_key}",
                "X-SSE-Replay-Supported": "true",
                "X-SSE-Replay-Window-Events": str(_TR_SSE_BUFFER_SIZE),
                "X-SSE-Resync-Routes": "/bff/agora/trading-room,/bff/agora/trading-room/decision-events",
            },
        )

    return router
