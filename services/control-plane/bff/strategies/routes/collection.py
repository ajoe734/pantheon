"""Strategy collection routes (list, create)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, Query

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
        read_store = ctx.get_read_store_port()
        snapshot_at = ctx.utc_now()
        summaries = ctx.list_strategy_summaries_records()
        if persona_id:
            summaries = [
                s for s in summaries
                if persona_id in (s.get("persona_ids") or [])
            ]
        items = []
        for summary in summaries:
            strategy_id = str(summary.get("strategy_id") or "")
            detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
            items.append(ctx.project_strategy_dto(summary, detail=detail, overlay=None))
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
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = ctx.stable_json_hash({"route": "POST /bff/strategies", "payload": payload})
        dry_run = ctx.request_dry_run_requested()
        if not dry_run:
            cached = ctx.strategy_persona_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ctx.bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Strategy name must be a non-empty string",
                precondition_failed="name",
            )
        snapshot_at = ctx.utc_now()
        strategy_id = f"strategy-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        record = {
            "id": strategy_id,
            "strategy_id": strategy_id,
            "name": name,
            "owner": str(payload.get("owner") or identity.operator_id),
            "updatedAt": snapshot_at,
            "state": ctx.normalize_lifecycle_state(payload.get("state") or "draft"),
            "risk": ctx.normalize_risk_level(payload.get("risk")),
            "alpha": str(payload.get("alpha") or ""),
            "capitalPoolId": str(payload.get("capitalPoolId") or payload.get("capital_pool_id") or ""),
            "personaIds": list(payload.get("personaIds") or payload.get("persona_ids") or []),
            "pnl30d": float(payload.get("pnl30d") or 0.0),
            "sharpe": float(payload.get("sharpe") or 0.0),
            "drawdown": float(payload.get("drawdown") or 0.0),
            "availableActions": ["edit", "submit", "retire"],
            "labelKey": f"strategy.{strategy_id}",
        }
        if dry_run:
            return ctx.dry_run_success_response(
                record,
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="strategy.create",
            )
        written = False
        try:
            rs = ctx.get_read_store_port()
            if hasattr(rs, "upsert_strategy"):
                rs.upsert_strategy(record)
                written = True
            elif hasattr(rs, "create_strategy_spec"):
                rs.create_strategy_spec(record)
                written = True
            elif hasattr(rs, "_data") and isinstance(rs._data, dict):
                strats = rs._data.setdefault("strategies", {})
                if isinstance(strats, dict):
                    strats[strategy_id] = record
                    written = True
                elif isinstance(strats, list):
                    strats.append(record)
                    written = True
        except Exception as exc:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Canonical strategy persistence failed",
                str(exc),
            ) from exc

        if not written:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Canonical strategy writer unavailable",
                "Cannot persist strategy without an authoritative domain store",
            )
        result = {
            "data": record,
            "meta": {"snapshot_at": snapshot_at},
        }
        ctx.strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    return router
