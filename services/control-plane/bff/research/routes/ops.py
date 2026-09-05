"""Research operator and ops routes."""
from __future__ import annotations

import inspect
from typing import Any, Dict

from fastapi import APIRouter, Request

from .common import (
    ResearchRouteContext,
    _authorization,
    _body_parameter,
    _idempotency_key,
    _path,
    _signature,
    _signature_query,
)

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from ..models import ErrorCode


def build_ops_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    async def endpoint_oss_activation_ready(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=False)
        snapshot_at = ctx.utc_now()
        if ctx.build_research_oss_readiness is not None:
            result = ctx.build_research_oss_readiness(
                activation_ready=True,
                activity_limit=int(ctx.query(request, "activity_limit", "20") or 20),
            )
            return await result if inspect.isawaitable(result) else result
        return {
            "data": {"activation_ready": False, "reason": "research OSS readiness projection is not wired"},
            "meta": ctx.meta(snapshot_at, "research_oss", "research_experiments", False),
        }

    async def endpoint_oss_preactivation(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=False)
        snapshot_at = ctx.utc_now()
        if ctx.build_research_oss_readiness is not None:
            result = ctx.build_research_oss_readiness(
                activation_ready=False,
                activity_limit=int(ctx.query(request, "activity_limit", "20") or 20),
            )
            return await result if inspect.isawaitable(result) else result
        return {
            "data": {"activation_ready": False, "reason": "research OSS readiness projection is not wired"},
            "meta": ctx.meta(snapshot_at, "research_oss", "research_experiments", False),
        }

    async def endpoint_source_ops(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=False)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        data = ctx.call_port(
            port,
            "get_source_ops_snapshot",
            crawl_run_limit=int(ctx.query(request, "crawl_run_limit", "50") or 50),
            dlq_status=ctx.query(request, "dlq_status"),
            frontier_status=ctx.query(request, "frontier_status"),
            audit_limit=int(ctx.query(request, "audit_limit", "20") or 20),
        )
        return {"data": data, "meta": ctx.meta(snapshot_at, "source_ops", "source_ops", bool(data))}

    async def endpoint_search_ops(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=False)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        data = ctx.call_port(port, "get_search_ops_snapshot", pipeline_run_limit=int(ctx.query(request, "pipeline_run_limit", "50") or 50))
        return {"data": data, "meta": ctx.meta(snapshot_at, "search_ops", "search_ops", bool(data))}

    async def _handle_command(name: str, request: Request) -> Dict[str, Any]:
        identity = ctx.identity(request, operator=True)
        if ctx.submit_source_search_command is None:
            raise ctx.bff_error(
                501,
                ErrorCode.NOT_IMPLEMENTED,
                "Source-search command route is not wired",
                "The composition root must inject submit_source_search_command",
            )
        payload = await ctx.body(request)
        result = ctx.submit_source_search_command(
            name.removeprefix("command_"),
            payload,
            identity,
            request.headers.get("x-idempotency-key"),
            request.path_params,
        )
        return await result if inspect.isawaitable(result) else result

    async def endpoint_command_source_dlq_replay(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        return await _handle_command("command_source_dlq_replay", request)

    async def endpoint_command_source_frontier_replay(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        return await _handle_command("command_source_frontier_replay", request)

    async def endpoint_command_search_index_refresh(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        return await _handle_command("command_search_index_refresh", request)

    async def endpoint_command_search_index_materialize(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        return await _handle_command("command_search_index_materialize", request)

    auth = _authorization()
    idempotency = _idempotency_key()

    endpoint_oss_activation_ready.__signature__ = _signature(_signature_query("activity_limit", annotation=int, default=20, ge=1, le=200), auth)
    endpoint_oss_preactivation.__signature__ = _signature(_signature_query("activity_limit", annotation=int, default=20, ge=1, le=200), auth)
    endpoint_source_ops.__signature__ = _signature(
        _signature_query("crawl_run_limit", annotation=int, default=50, ge=1, le=200),
        _signature_query("dlq_status"),
        _signature_query("frontier_status"),
        _signature_query("audit_limit", annotation=int, default=20, ge=1, le=200),
        auth,
    )
    endpoint_search_ops.__signature__ = _signature(_signature_query("pipeline_run_limit", annotation=int, default=50, ge=1, le=200), auth)
    endpoint_command_source_dlq_replay.__signature__ = _signature(_body_parameter(required=False), auth, idempotency)
    endpoint_command_source_frontier_replay.__signature__ = _signature(_path("frontier_id"), _body_parameter(required=False), auth, idempotency)
    endpoint_command_search_index_refresh.__signature__ = _signature(_body_parameter(required=False), auth, idempotency)
    endpoint_command_search_index_materialize.__signature__ = _signature(auth, idempotency)

    router.add_api_route("/api/v1/operator/research/oss-activation-ready", endpoint_oss_activation_ready, methods=["GET"], name="oss_activation_ready")
    router.add_api_route("/api/v1/operator/research/oss-preactivation", endpoint_oss_preactivation, methods=["GET"], name="oss_preactivation")
    router.add_api_route("/api/v1/operator/source/ops", endpoint_source_ops, methods=["GET"], name="source_ops")
    router.add_api_route("/api/v1/operator/search/ops", endpoint_search_ops, methods=["GET"], name="search_ops")
    router.add_api_route("/api/v1/operator/source/dlq/replay", endpoint_command_source_dlq_replay, methods=["POST"], name="command_source_dlq_replay", status_code=202)
    router.add_api_route("/api/v1/operator/source/frontier/{frontier_id}/replay", endpoint_command_source_frontier_replay, methods=["POST"], name="command_source_frontier_replay", status_code=202)
    router.add_api_route("/api/v1/operator/search/index/refresh", endpoint_command_search_index_refresh, methods=["POST"], name="command_search_index_refresh", status_code=202)
    router.add_api_route("/api/v1/operator/search/index/materialize", endpoint_command_search_index_materialize, methods=["POST"], name="command_search_index_materialize", status_code=202)

    return router
