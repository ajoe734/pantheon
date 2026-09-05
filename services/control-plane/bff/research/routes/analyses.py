"""Research analyses routes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Query

from .common import ResearchRouteContext
from ..service import ResearchNotFoundError, ResearchValidationError


def build_analyses_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    async def _list_analyses(
        ticket_id: Optional[str],
        experiment_id: Optional[str],
        status: Optional[str],
        date_range: Optional[str],
        page_token: Optional[str],
        page_size: int,
        authorization: Optional[str],
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        ctx.require_read_role(ctx.extract_identity(authorization))
        try:
            return ctx.service.list_analyses(
                ticket_id=ticket_id,
                experiment_id=experiment_id,
                status=status,
                date_range=date_range,
                page_token=page_token,
                page_size=page_size,
                detail_path=detail_path,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    async def _get_analysis(
        analysis_id: str,
        authorization: Optional[str],
        *,
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        ctx.require_read_role(ctx.extract_identity(authorization))
        try:
            return ctx.service.get_analysis(analysis_id, detail_path=detail_path)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/analyses")
    async def list_research_analyses(
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _list_analyses(
            ticket_id, experiment_id, status, date_range, page_token, page_size, authorization
        )

    @router.get("/api/v1/research/analyses/{analysis_id}")
    async def get_research_analysis(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_analysis(analysis_id, authorization)

    @router.get("/api/v1/research/analysis")
    async def list_research_analysis_compat(
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _list_analyses(
            ticket_id,
            experiment_id,
            status,
            date_range,
            page_token,
            page_size,
            authorization,
            detail_path="/api/v1/research/analysis",
        )

    @router.get("/api/v1/research/analysis/{analysis_id}")
    async def get_research_analysis_compat(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_analysis(
            analysis_id,
            authorization,
            detail_path="/api/v1/research/analysis",
        )

    @router.get("/bff/research-analyses")
    async def bff_list_research_analyses(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        result = await _list_analyses(None, None, None, None, page_token, page_size, authorization)
        return {
            "data": result["data"],
            "items": result["data"],
            "page_info": result["page_info"],
            "meta": result["meta"],
        }

    @router.get("/bff/research-analyses/{analysis_id}")
    async def bff_get_research_analysis(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        payload = await _get_analysis(analysis_id, authorization)
        meta = payload.pop("meta")
        return {"data": payload, "meta": meta}

    return router
