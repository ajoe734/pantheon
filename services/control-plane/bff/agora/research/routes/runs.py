"""Agora research runs subrouter."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Header

from .common import (
    AgoraResearchRouteContext,
    _CAPABILITY,
    _run_projection_with_defaults,
    _build_run_projection,
    publish_research_progress,
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
        ctx.get_plan_or_404(plan_id, scope)
        runs = ctx.store.list_runs_for_plan(
            plan_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        return {
            "items": [_run_projection_with_defaults(r) for r in runs],
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
        plan = ctx.get_plan_or_404(plan_id, scope)
        ctx.check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        if plan["status"] != "approved":
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Only approved plans may be dispatched; current status: '{plan['status']}'",
                f"expected 'approved', got '{plan['status']}'",
            )
        dispatch_stage: Optional[Dict[str, Any]] = None
        for stage in plan.get("stages", []):
            if stage.get("status") in ("pending", "ready"):
                dispatch_stage = stage
                break
        if dispatch_stage is None:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "No pending or ready stages to dispatch",
                "all_stages_dispatched_or_blocked",
            )
        now = ctx.utc_now()
        run_id = str(uuid.uuid4())
        run = _build_run_projection(
            plan=plan,
            stage=dispatch_stage,
            run_id=run_id,
            now=now,
            scope=scope,
        )
        ctx.store.create_run(run)

        # Create durable outbox record
        ctx.dispatcher.create_outbox_record(
            plan=plan,
            stage=dispatch_stage,
            run_id=run_id,
            scope=scope,
            now=now,
        )

        updated_stages = [
            {**s, "status": "queued"} if s["stage_id"] == dispatch_stage["stage_id"] else s
            for s in plan.get("stages", [])
        ]
        plan_run_ids = list(plan.get("run_ids") or [])
        plan_run_ids.append(run_id)
        ctx.store.update_plan(
            plan_id,
            {
                "stages": updated_stages,
                "run_ids": plan_run_ids,
                "status": "running",
                "lock_version": plan.get("lock_version", 1) + 1,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        ctx.publish_research_event(
            plan.get("workshop_id", ""),
            "research.run.queued",
            {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
                "percent": 0,
            },
        )

        ctx.store.record_audit_action({
            "action_type": "research_plan.dispatch",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_run",
            "subject_id": run_id,
            "plan_id": plan_id,
            "stage_id": dispatch_stage["stage_id"],
        })

        # Drain queued outbox records synchronously via ResearchDispatcher leased consumer
        ctx.dispatcher.drain_outbox(
            worker_id=f"dispatcher-router-{scope.user_id}",
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )

        return {
            "status": "queued",
            "data": {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
            },
            "meta": {
                "snapshot_at": now,
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
        run = ctx.get_run_or_404(run_id, scope)
        return _run_projection_with_defaults(run)

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
        run = ctx.get_run_or_404(run_id, scope)
        cancellable_statuses = {"queued", "dispatching", "running"}
        current = run.get("execution_status")
        if current not in cancellable_statuses:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Run in status '{current}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable_statuses)}",
            )
        now = ctx.utc_now()
        ctx.store.update_run(
            run_id,
            {
                "execution_status": "cancelled",
                "progress": {
                    **run.get("progress", {}),
                    "phase": "cancelled",
                    "message": "Run cancellation accepted",
                    "updated_at": now,
                },
                "completed_at": now,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        publish_research_progress(
            run.get("workshop_id", ""),
            run_id,
            float(run.get("progress", {}).get("percent", 0)),
            "Run cancellation accepted",
            phase="cancelled",
            utc_now_fn=ctx.utc_now,
        )
        ctx.store.record_audit_action({
            "action_type": "research_run.cancel",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_run",
            "subject_id": run_id,
        })
        return {
            "status": "accepted",
            "data": {"run_id": run_id, "execution_status": "cancelled"},
            "meta": {
                "snapshot_at": now,
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
        run = ctx.get_run_or_404(run_id, scope)
        artifact_refs = run.get("artifact_refs") or []
        evidence_refs = run.get("evidence_refs") or []
        items: List[Dict[str, Any]] = (
            [{"ref_type": "experiment_artifact", "ref_id": a} for a in artifact_refs]
            + list(evidence_refs)
        )
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
