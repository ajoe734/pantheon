"""Agora trading intents and governed handoffs routes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException

from .common import (
    TradingRoomRouteContext,
    GovernedIntentHandoffRequest,
)


def build_intents_router(ctx: TradingRoomRouteContext) -> APIRouter:
    """Trading-room trading intents, governed handoffs, and withdrawal subrouter."""
    router = APIRouter()

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

        intent, state, handoffs = ctx.service.get_intent_detail(intent_id)

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

    return router
