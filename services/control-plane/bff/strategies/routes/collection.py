"""Strategy collection routes (list, create)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from .common import StrategyRouteContext

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from models import ErrorCode


def build_collection_router(ctx: StrategyRouteContext) -> APIRouter:
    router = APIRouter()

    @router.get("/bff/strategies")
    async def bff_list_strategies(
        state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: strategy list (execute-plans Strategy DTO compatibility)."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        snapshot_at = ctx.utc_now()
        summaries = ctx.service.list_strategy_summaries() if ctx.service else ctx.list_strategy_summaries_records()
        if persona_id:
            summaries = [
                s for s in summaries
                if persona_id in (s.get("persona_ids") or [])
            ]
        items = []
        for summary in summaries:
            strategy_id = str(summary.get("strategy_id") or "")
            detail = ctx.service.get_strategy_spec_detail(strategy_id, version_selector="current") if ctx.service else None
            items.append(ctx.project_strategy_dto(summary, detail=detail))
        if state:
            items = [s for s in items if s.get("state") == state]
        total = len(items)
        page_items, next_page_token = ctx.page_slice(items, page_token, page_size)
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": ctx.read_surface_meta(
                "strategy_specs", "strategy_list",
                snapshot_at=snapshot_at, total=total,
            ),
        }

    @router.post("/bff/strategies", status_code=201)
    async def bff_create_strategy(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: create strategy stub (execute-plans compatibility)."""
        identity = ctx.extract_identity(authorization)
        ctx.require_operator_role(identity)
        principal = ctx.write_principal(identity)
        ctx.reject_body_idempotency_key(payload)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ctx.bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Strategy name must be a non-empty string",
                precondition_failed="name",
            )
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        dry_run = ctx.request_dry_run_requested()
        return ctx.service.create_strategy(
            payload=payload,
            identity=identity,
            principal=principal,
            resolved_key=resolved_key,
            dry_run=dry_run,
        )


    return router
