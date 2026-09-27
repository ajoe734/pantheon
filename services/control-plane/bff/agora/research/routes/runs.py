"""Agora research runs subrouter.

Part of BFF-ROUTER-USECASE-CORRECTIVE-001.
Handlers only perform request parsing, auth invocation, DTO translation, and status mapping.
All store access, outbox creation, and business branching are delegated to ctx.service.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header

from .common import (
    AgoraResearchRouteContext,
    _CAPABILITY,
)


def build_runs_router(ctx: AgoraResearchRouteContext) -> APIRouter:
    router = APIRouter(tags=["agora-research-runs"])

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}/runs
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}/runs")
    def list_agora_research_plan_runs(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        runs = ctx.service.list_runs_for_plan(plan_id, scope=scope)
        return {
            "items": runs,
            "page_info": {
                "next_page_token": None,
                "page_size": len(runs),
                "has_more": False,
                "total": len(runs),
            },
            "meta": {
                "snapshot_at": ctx.utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/runs  (dispatch)
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/runs", status_code=202)
    def dispatch_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.require_if_match(if_match)
        ctx.check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/runs",
            idempotency_key,  # type: ignore[arg-type]
        )
        data = ctx.service.dispatch_plan(plan_id, scope=scope, if_match=if_match)
        return {
            "status": "queued",
            "data": data,
            "meta": {
                "snapshot_at": ctx.utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-runs/{run_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-runs/{run_id}")
    def get_agora_research_run(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        run = ctx.service.get_run(run_id, scope=scope)
        if run is None:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        return run

    # -------------------------------------------------------------------
    # POST /bff/agora/research-runs/{run_id}/cancel
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-runs/{run_id}/cancel", status_code=202)
    def cancel_agora_research_run(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope,
            f"POST:/bff/agora/research-runs/{run_id}/cancel",
            idempotency_key,  # type: ignore[arg-type]
        )
        data = ctx.service.cancel_run(run_id, scope=scope)
        return {
            "status": "accepted",
            "data": data,
            "meta": {
                "snapshot_at": ctx.utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-runs/{run_id}/artifacts
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-runs/{run_id}/artifacts")
    def list_agora_research_run_artifacts(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        items = ctx.service.get_run_artifacts(run_id, scope=scope)
        return {
            "items": items,
            "page_info": {
                "next_page_token": None,
                "page_size": len(items),
                "has_more": False,
                "total": len(items),
            },
            "meta": {
                "snapshot_at": ctx.utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    return router
