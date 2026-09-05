"""Agora research plans subrouter."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Header, Query

from .common import (
    AgoraResearchRouteContext,
    ResearchPlanCreateRequest,
    _CAPABILITY,
    _plan_detail_envelope,
    _validate_create_body,
    _build_plan,
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
        plans = ctx.store.list_plans_for_workshop(
            workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
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
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope,
            f"POST:/bff/agora/workshops/{workshop_id}/research-plans",
            idempotency_key,  # type: ignore[arg-type]
        )
        _validate_create_body(body, workshop_id, ctx.bff_error, ctx.error_code_enum)
        now = ctx.utc_now()
        plan_id = str(uuid.uuid4())
        plan = _build_plan(body, workshop_id, plan_id, now, scope)
        plan = ctx.store.create_plan(plan)
        ctx.store.record_audit_action({
            "action_type": "research_plan.create",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_plan",
            "subject_id": plan_id,
            "workshop_id": workshop_id,
            "payload": {"status": plan["status"]},
        })
        ctx.publish_research_event(
            workshop_id,
            "research.plan.created",
            {"plan_id": plan_id, "status": plan["status"]},
        )
        return _plan_detail_envelope(plan, ctx.utc_now, scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}")
    def get_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        plan = ctx.get_plan_or_404(plan_id, scope)
        return _plan_detail_envelope(plan, ctx.utc_now, scope)

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/approve
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/approve")
    def approve_agora_research_plan(
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
            f"POST:/bff/agora/research-plans/{plan_id}/approve",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = ctx.get_plan_or_404(plan_id, scope)
        ctx.check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        if plan["status"] != "draft":
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Plan cannot be approved from status '{plan['status']}'",
                f"expected status 'draft', got '{plan['status']}'",
            )
        now = ctx.utc_now()
        ctx.store.update_plan(
            plan_id,
            {
                "status": "approved",
                "approved_at": now,
                "approval": {
                    "state": "approved",
                    "decided_by": scope.user_id,
                    "decided_at": now,
                },
                "lock_version": plan.get("lock_version", 1) + 1,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        ctx.store.record_audit_action({
            "action_type": "research_plan.approve",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_plan",
            "subject_id": plan_id,
            "payload": {"status": "approved"},
        })
        ctx.publish_research_event(
            plan.get("workshop_id", ""),
            "research.plan.approved",
            {"plan_id": plan_id, "status": "approved"},
        )
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "approved"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
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
        plan = ctx.get_plan_or_404(plan_id, scope)
        ctx.check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        cancellable = {"draft", "approved", "running"}
        if plan["status"] not in cancellable:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Plan in status '{plan['status']}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable)}",
            )
        now = ctx.utc_now()
        ctx.store.update_plan(
            plan_id,
            {
                "status": "cancelled",
                "lock_version": plan.get("lock_version", 1) + 1,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        ctx.store.record_audit_action({
            "action_type": "research_plan.cancel",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_plan",
            "subject_id": plan_id,
            "payload": {"status": "cancelled"},
        })
        ctx.publish_research_event(
            plan.get("workshop_id", ""),
            "research.plan.cancelled",
            {"plan_id": plan_id, "status": "cancelled"},
        )
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
