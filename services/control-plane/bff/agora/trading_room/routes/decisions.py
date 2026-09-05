"""Agora trading-room decision events and SSE stream routes."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional
import uuid

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from .common import (
    TradingRoomRouteContext,
    TraderDecisionRequest,
    TradingDecisionEvent,
    _TR_SSE_BUFFER_SIZE,
    _tr_buffer,
    _tr_event_id,
    _tr_publish,
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

        valid_kinds = {"entry", "add", "reduce", "exit", "review"}
        if event_kind and event_kind not in valid_kinds:
            raise ctx.bff_error(422, "VALIDATION_ERROR", f"event_kind must be one of {sorted(valid_kinds)}", "invalid_event_kind")

        page = ctx.store.list_decision_events(
            event_kind=event_kind,
            state=state,
            page_size=page_size,
            next_page_token=next_page_token,
        )
        return {
            "items": page["items"],
            "page_info": page["page_info"],
            "meta": ctx._meta(),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/decision-events/{decision_event_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/decision-events/{decision_event_id}")
    def get_trading_decision_event(
        decision_event_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        """Return a single TradingDecisionEvent by ID."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)

        event = ctx.store.get_decision_event(decision_event_id)
        if event is None:
            raise ctx.bff_error(404, "NOT_FOUND", f"Decision event {decision_event_id!r} not found", "decision_event_not_found")
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

        event = ctx.store.get_decision_event(decision_event_id)
        if event is None:
            raise ctx.bff_error(404, "NOT_FOUND", f"Decision event {decision_event_id!r} not found", "decision_event_not_found")

        ctx._check_idempotency(
            identity,
            f"POST:/bff/agora/trading-room/decision-events/{decision_event_id}/decisions",
            idem_key,
        )

        if event.get("state") in ("decided", "expired", "invalidated", "superseded"):
            raise ctx.bff_error(
                409,
                "TRADING_INTENT_ALREADY_RECORDED",
                f"Decision event {decision_event_id!r} is already in terminal state '{event['state']}'",
                "decision_event_not_actionable",
            )

        decision_record = {
            "decision_record_id": str(uuid.uuid4()),
            "decision_event_id": decision_event_id,
            "decision": body.decision,
            "rationale": body.rationale,
            "modifications": body.modifications,
            "decided_by": _workspace_scope(identity)["user_id"] or "unknown",
            "decided_at": ctx.utc_now(),
        }
        ctx.store.record_trader_decision(decision_event_id, decision_record)

        intent_ref: Optional[str] = None
        if body.decision in ("approve", "modify"):
            intent_ref = str(uuid.uuid4())
            intent = ctx._intent_from_decision(
                event=event,
                decision_record=decision_record,
                body=body,
                identity=identity,
                intent_id=intent_ref,
                x_request_id=request_id,
            )
            ctx.store.upsert_intent(intent, state="draft")

        data = {
            "decision_record_id": decision_record["decision_record_id"],
            "decision_event_id": decision_event_id,
            "decision": body.decision,
            "intent_ref": intent_ref,
        }
        if intent_ref:
            data["no_order_route_proof"] = "agora_intent_record_only"

        _tr_publish(
            _workspace_scope(identity),
            "trading_room.decision.recorded",
            data,
            ctx.utc_now(),
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
