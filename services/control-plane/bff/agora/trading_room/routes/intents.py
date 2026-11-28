"""Agora trading intents and governed handoffs routes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Query

from .common import (
    TradingRoomRouteContext,
    GovernedIntentHandoffRequest,
)
from .paper_execution import build_paper_execution_router


def build_intents_router(ctx: TradingRoomRouteContext) -> APIRouter:
    """Trading-room trading intents, governed handoffs, and withdrawal subrouter.

    Also mounts the service-only paper execution boundary
    (AGORA-PAPER-INTENT-EXECUTION-20261127): the paper execution lane drains
    ``paper_execution`` handoffs and writes their state back through it.
    """
    router = APIRouter()
    router.routes.extend(build_paper_execution_router(ctx).routes)

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-intents/{intent_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-intents/{intent_id}")
    def get_trading_intent(
        intent_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        """Return TradingIntent detail (DetailEnvelope).

        Full governed handoff semantics are owned by AG-BE-TR-002.
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)

        intent, state, handoffs = ctx.service.get_intent_detail(intent_id, identity)

        return {
            "object_ref": {"type": "trading_intent", "id": intent_id},
            "status": state,
            "lifecycle_state": state,
            "allowedActions": {
                "submit_handoff": state == "draft",
                "withdraw": state in ("draft", "submitted"),
            },
            "meta": ctx._meta(handoff_count=len(handoffs)),
            "links": {
                "handoffs": f"/bff/agora/trading-intents/{intent_id}/handoffs",
                "withdraw": f"/bff/agora/trading-intents/{intent_id}/withdraw",
            },
            "data": intent,
            "handoffs": handoffs,
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-intents/{intent_id}/handoffs
    # Governed handoff — AG-BE-TR-002 owns the full implementation.
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-intents/{intent_id}/handoffs", status_code=202)
    def submit_trading_intent_handoff(
        intent_id: str,
        body: GovernedIntentHandoffRequest,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        """Submit a governed handoff request for a TradingIntent.

        Safety: no_order_route_proof must be 'agora_request_only_no_order_route'.
        This is a request-only path; it never routes live orders, creates
        RuntimeBinding, or binds capital.  Management/governance paths remain
        authoritative.

        Validation enforces v1.3 stage/type semantics and keeps canary/live
        request-only.
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)
        idem_key = ctx._require_idempotency_key(idempotency_key)
        ctx._require_if_match(if_match)
        request_id = ctx._require_x_request_id(x_request_id)

        data = ctx.service.submit_intent_handoff(
            intent_id=intent_id,
            body=body,
            identity=identity,
            idempotency_key=idem_key,
            x_request_id=request_id,
        )

        return {
            "status": "queued",
            "data": data,
            "meta": ctx._meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-intents/{intent_id}/withdraw
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-intents/{intent_id}/withdraw")
    def withdraw_trading_intent(
        intent_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        """Withdraw a TradingIntent and any pending governed handoff.

        This records withdrawal; it does not cancel any live execution
        (no order routing was ever permitted).
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)
        idem_key = ctx._require_idempotency_key(idempotency_key)
        ctx._require_if_match(if_match)
        request_id = ctx._require_x_request_id(x_request_id)

        data = ctx.service.withdraw_intent(
            intent_id=intent_id,
            identity=identity,
            idempotency_key=idem_key,
        )

        return {
            "status": "completed",
            "data": data,
            "meta": ctx._meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/paper-book
    # Owner-scoped readback of the trader's paper fills, positions, and
    # each intent's progress (AGORA-PAPER-BOOK-READBACK-20261127).
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/paper-book")
    def get_trading_room_paper_book(
        intent_id: Optional[str] = Query(default=None),
        decision_event_id: Optional[str] = Query(default=None),
        handoff_id: Optional[str] = Query(default=None),
        user_id: Optional[str] = Query(default=None),
        trader_id: Optional[str] = Query(default=None),
        tenant_id: Optional[str] = Query(default=None),
        servant_persona_id: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None),
        runtime_binding_id: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        """Return the authenticated trader's own paper fills, positions, and intent progress.

        Strictly owner-scoped to ``(tenant_id, user_id)``: another trader (or any filter
        targeting another trader's book, pool, binding, decision event, or intent)
        receives a non-enumerating 404. Every fill is labelled ``pantheon_paper_proxy``
        and populates the linked decision lock's ``human_actual`` arm as a paper proxy
        (never a real fill).
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)

        requested_user = user_id if user_id is not None else trader_id
        if user_id is not None and trader_id is not None and str(user_id).strip() != str(trader_id).strip():
            raise ctx.bff_error(404, "NOT_FOUND", "Trader paper book not found", "paper_book_not_found")

        data = ctx.service.get_trader_paper_book(
            identity,
            intent_id=intent_id,
            decision_event_id=decision_event_id,
            handoff_id=handoff_id,
            requested_user_id=requested_user,
            requested_tenant_id=tenant_id,
            requested_servant_persona_id=servant_persona_id,
            requested_capital_pool_id=capital_pool_id,
            requested_runtime_binding_id=runtime_binding_id,
            requested_runtime_id=runtime_id,
        )
        pool_id = str((data.get("paper_book") or {}).get("capital_pool_id") or "")
        return {
            "status": "ok",
            "object_ref": {"type": "agora_trader_paper_book", "id": pool_id},
            "meta": ctx._meta(
                fill_count=len(data.get("fills") or []),
                position_count=len(data.get("positions") or []),
                intent_count=len(data.get("intents") or []),
            ),
            "links": {
                "self": "/bff/agora/trading-room/paper-book",
            },
            "data": data,
            **data,
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/paper-book/intents/{intent_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/paper-book/intents/{intent_id}")
    def get_trading_room_paper_book_intent(
        intent_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        """Return paper execution progress and ``pantheon_paper_proxy`` fills for one intent.

        Strictly owner-scoped to ``(tenant_id, user_id)``: another trader's intent
        returns 404.
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)

        data = ctx.service.get_trader_paper_book_intent_progress(intent_id, identity)
        return {
            "status": "ok",
            "object_ref": {"type": "agora_paper_book_intent_progress", "id": intent_id},
            "meta": ctx._meta(fill_count=len(data.get("fills") or [])),
            "links": {
                "self": f"/bff/agora/trading-room/paper-book/intents/{intent_id}",
                "paper_book": "/bff/agora/trading-room/paper-book",
                "trading_intent": f"/bff/agora/trading-intents/{intent_id}",
            },
            "data": data,
            **data,
        }

    return router
