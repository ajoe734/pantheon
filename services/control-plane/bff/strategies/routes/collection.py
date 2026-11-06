"""Strategy collection routes (list, create)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from .common import StrategyRouteContext, _build_ranking_strategy_index

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from models import ErrorCode


def build_collection_router(ctx: StrategyRouteContext) -> APIRouter:
    router = APIRouter()

    @router.get("/bff/strategies")
    def bff_list_strategies(
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
        detail_by_id: Dict[str, Optional[Dict[str, Any]]] = {}
        owner_page = (
            ctx.service.list_strategy_summaries_page(
                state=state,
                persona_id=persona_id,
                page_token=page_token,
                page_size=page_size,
            )
            if ctx.service
            else None
        )
        if owner_page is not None:
            if not isinstance(owner_page, dict) or not isinstance(owner_page.get("items"), list):
                raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy list payload", "strategy_store_unavailable")
            page_summaries = list(owner_page["items"])
            for summary in page_summaries:
                strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip() if isinstance(summary, dict) else ""
                if not strategy_id:
                    raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy summary entry", "strategy_store_unavailable")
            total = int(owner_page.get("total") or 0)
            next_page_token = owner_page.get("next_page_token")
        else:
            summaries = ctx.service._invoke_read_owner(ctx.service.list_strategy_summaries) if ctx.service else ctx.list_strategy_summaries_records()
            if not isinstance(summaries, list):
                raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy list payload", "strategy_store_unavailable")
            for summary in summaries:
                strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip() if isinstance(summary, dict) else ""
                if not strategy_id:
                    raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy summary entry", "strategy_store_unavailable")
            if persona_id:
                summaries = [
                    s
                    for s in summaries
                    if persona_id in (s.get("persona_ids") or s.get("personaIds") or [])
                ]
            if state:
                filtered_summaries: List[Dict[str, Any]] = []
                for summary in summaries:
                    strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip()
                    if (
                        "current_spec_version_id" in summary
                        or summary.get("lifecycle_state") is not None
                        or summary.get("state") is not None
                        or summary.get("status") is not None
                    ):
                        lifecycle_raw = (
                            summary.get("lifecycle_state")
                            or summary.get("state")
                            or summary.get("status")
                        )
                    else:
                        detail = (
                            ctx.service.get_strategy_spec_detail(strategy_id, version_selector="current")
                            if ctx.service
                            else None
                        )
                        detail_by_id[strategy_id] = detail
                        lifecycle_raw = (detail or summary).get("lifecycle_state") or summary.get("lifecycle_state")
                    if ctx.normalize_lifecycle_state(lifecycle_raw) == state:
                        filtered_summaries.append(summary)
                summaries = filtered_summaries
            total = len(summaries)
            page_summaries, next_page_token = ctx.page_slice(summaries, page_token, page_size)
        ranking_snapshot, ranking_read_failed = (
            ctx.service.get_latest_ranking_snapshot() if ctx.service else (None, False)
        )
        ranking_index = _build_ranking_strategy_index(
            ranking_snapshot if isinstance(ranking_snapshot, dict) else None
        )
        page_items: List[Dict[str, Any]] = []
        for summary in page_summaries:
            strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip()
            if "current_spec_version_id" in summary:
                detail = summary
            elif strategy_id in detail_by_id:
                detail = detail_by_id[strategy_id]
            else:
                detail = (
                    ctx.service.get_strategy_spec_detail(strategy_id, version_selector="current")
                    if ctx.service
                    else None
                )
            page_items.append(
                ctx.project_strategy_dto(
                    summary,
                    detail=detail,
                    ranking_snapshot=ranking_snapshot,
                    ranking_read_failed=ranking_read_failed,
                    ranking_index=ranking_index,
                )
            )
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
    def bff_create_strategy(
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
