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

        intent = ctx.store.get_intent(intent_id)
        if intent is None:
            raise ctx.bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")
        state = ctx.store.get_intent_state(intent_id) or "draft"
        handoffs = ctx.store.list_handoffs_for_intent(intent_id)

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

        if body.no_order_route_proof != "agora_request_only_no_order_route":
            raise ctx.bff_error(
                422,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                "no_order_route_proof must be 'agora_request_only_no_order_route'",
                "invalid_no_order_route_proof",
            )

        if body.intent_id != intent_id:
            raise ctx.bff_error(
                422,
                "VALIDATION_ERROR",
                "intent_id in body must match path parameter",
                "intent_id_mismatch",
            )

        intent = ctx.store.get_intent(intent_id)
        if intent is None:
            raise ctx.bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")

        ctx._check_idempotency(
            identity,
            f"POST:/bff/agora/trading-intents/{intent_id}/handoffs",
            idem_key,
        )

        intent_state = ctx.store.get_intent_state(intent_id) or "draft"
        if intent_state != "draft":
            raise ctx.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                f"TradingIntent {intent_id!r} is not draft; current state is '{intent_state}'",
                "intent_not_handoffable",
            )

        if body.state not in {"draft", "submitted"}:
            raise ctx.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                "Agora can only create draft/submitted request-only handoffs",
                "handoff_state_not_request_only",
            )

        stage_rule = ctx._handoff_stage_rule(body.requested_stage)
        if body.handoff_type != stage_rule["handoff_type"]:
            raise ctx.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                (
                    f"requested_stage '{body.requested_stage}' requires "
                    f"handoff_type '{stage_rule['handoff_type']}'"
                ),
                "stage_handoff_type_mismatch",
            )

        if body.target_queue is not None and body.target_queue != stage_rule["target_queue"]:
            raise ctx.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                (
                    f"requested_stage '{body.requested_stage}' requires "
                    f"target_queue '{stage_rule['target_queue']}'"
                ),
                "stage_target_queue_mismatch",
            )

        if ctx.store.get_handoff(body.handoff_id) is not None:
            raise ctx.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                f"handoff_id {body.handoff_id!r} already exists",
                "duplicate_handoff_id",
            )

        handoff = body.model_dump(exclude_none=True)
        handoff["state"] = "submitted"
        handoff["target_queue"] = stage_rule["target_queue"]
        handoff["updated_at"] = handoff.get("updated_at") or ctx.utc_now()
        ctx.store.upsert_handoff(handoff)

        return {
            "status": "queued",
            "data": {
                "handoff_id": body.handoff_id,
                "intent_id": intent_id,
                "requested_stage": body.requested_stage,
                "handoff_type": body.handoff_type,
                "target_queue": stage_rule["target_queue"],
                "state": "submitted",
                "no_order_route_proof": "agora_request_only_no_order_route",
            },
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

        if ctx.store.get_intent(intent_id) is None:
            raise ctx.bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")

        ctx._check_idempotency(
            identity,
            f"POST:/bff/agora/trading-intents/{intent_id}/withdraw",
            idem_key,
        )

        withdrawn_at = ctx.utc_now()
        withdrawn = ctx.store.withdraw_intent(intent_id, withdrawn_at=withdrawn_at)
        withdrawn_handoff_ids = withdrawn.get("withdrawn_handoff_ids", []) if withdrawn else []

        return {
            "status": "completed",
            "data": {
                "intent_id": intent_id,
                "state": "withdrawn",
                "withdrawn_at": withdrawn_at,
                "withdrawn_handoff_ids": withdrawn_handoff_ids,
            },
            "meta": ctx._meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    return router
