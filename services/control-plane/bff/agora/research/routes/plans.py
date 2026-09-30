"""Agora research plans subrouter.

Part of BFF-ROUTER-USECASE-CORRECTIVE-001.
Handlers only perform request parsing, auth invocation, DTO translation, and status mapping.
All store access and business branching are delegated to ctx.service.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Query, Response
from pydantic import ValidationError

from ...servant.research_proposal import ServantDraftError, draft_research_plan

from .common import (
    AgoraResearchRouteContext,
    ResearchPlanCreateRequest,
    ServantResearchProposalRequest,
    _CAPABILITY,
    _plan_detail_envelope,
    _plan_etag,
    _validate_create_body,
)


def build_plans_router(ctx: AgoraResearchRouteContext) -> APIRouter:
    router = APIRouter(tags=["agora-research-plans"])

    # -------------------------------------------------------------------
    # GET /bff/agora/workshops/{workshop_id}/research-plans
    # -------------------------------------------------------------------
    @router.get("/bff/agora/workshops/{workshop_id}/research-plans")
    def list_workshop_research_plans(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        plans = ctx.service.list_workshop_plans(workshop_id, scope=scope)
        return {
            "items": plans,
            "page_info": {
                "next_page_token": None,
                "page_size": len(plans),
                "has_more": False,
                "total": len(plans),
            },
            "meta": {
                "snapshot_at": ctx.utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/workshops/{workshop_id}/research-plans
    # -------------------------------------------------------------------
    @router.post("/bff/agora/workshops/{workshop_id}/research-plans", status_code=201)
    def create_workshop_research_plan(
        workshop_id: str,
        body: ResearchPlanCreateRequest,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope,
            f"POST:/bff/agora/workshops/{workshop_id}/research-plans",
            idempotency_key,  # type: ignore[arg-type]
        )
        _validate_create_body(body, workshop_id, ctx.bff_error, ctx.error_code_enum)
        plan = ctx.service.create_workshop_plan(
            workshop_id,
            body,
            scope=scope,
            trace_id=x_trace_id,
            correlation_id=x_correlation_id,
        )
        envelope = _plan_detail_envelope(plan, ctx.utc_now, scope)
        if response is not None:
            response.headers["ETag"] = envelope["meta"]["etag"]
        return envelope

    # -------------------------------------------------------------------
    # POST /bff/agora/workshops/{workshop_id}/research-plans/servant-proposal
    # -------------------------------------------------------------------
    @router.post("/bff/agora/workshops/{workshop_id}/research-plans/servant-proposal", status_code=201)
    def propose_workshop_research_plan(
        workshop_id: str,
        body: ServantResearchProposalRequest,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    ) -> Dict[str, Any]:
        """Servant drafts a plan (data-only); it is stored as 'draft' and never dispatched here."""
        scope = ctx.write_scope(authorization, x_tenant_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope,
            f"POST:/bff/agora/workshops/{workshop_id}/research-plans/servant-proposal",
            idempotency_key,  # type: ignore[arg-type]
        )
        errors = ctx.error_code_enum()
        try:
            draft = draft_research_plan(body.prompt, operator_id=scope.user_id, trace_id=x_trace_id)
            plan_body = ResearchPlanCreateRequest.model_validate(draft)
        except ServantDraftError as exc:
            raise ctx.bff_error(exc.status_code, errors.UPSTREAM_ERROR, "Servant research draft failed", exc.message)
        except ValidationError as exc:
            raise ctx.bff_error(
                422, errors.VALIDATION_FAILED,
                "Servant research draft violates the research plan contract",
                "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
            )
        _validate_create_body(plan_body, workshop_id, ctx.bff_error, ctx.error_code_enum)
        plan = ctx.service.create_workshop_plan(
            workshop_id,
            plan_body,
            scope=scope,
            trace_id=x_trace_id,
            correlation_id=x_correlation_id,
            proposed_by="servant",
        )
        envelope = _plan_detail_envelope(plan, ctx.utc_now, scope)
        if response is not None:
            response.headers["ETag"] = envelope["meta"]["etag"]
        return envelope

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}")
    def get_agora_research_plan(
        plan_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        plan = ctx.get_plan_or_404(plan_id, scope)
        envelope = _plan_detail_envelope(plan, ctx.utc_now, scope)
        if response is not None:
            response.headers["ETag"] = envelope["meta"]["etag"]
        return envelope

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/approve
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/approve")
    def approve_agora_research_plan(
        plan_id: str,
        response: Response,
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
            f"POST:/bff/agora/research-plans/{plan_id}/approve",
            idempotency_key,  # type: ignore[arg-type]
        )
        res = ctx.service.approve_plan(plan_id, scope=scope, if_match=if_match)
        etag = _plan_etag(plan_id, res["lock_version"])
        if response is not None:
            response.headers["ETag"] = etag
        now = ctx.utc_now()
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "approved"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": etag,
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/cancel
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/cancel")
    def cancel_agora_research_plan(
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
            f"POST:/bff/agora/research-plans/{plan_id}/cancel",
            idempotency_key,  # type: ignore[arg-type]
        )
        ctx.service.cancel_plan(plan_id, scope=scope, if_match=if_match)
        now = ctx.utc_now()
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "cancelled"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    return router
