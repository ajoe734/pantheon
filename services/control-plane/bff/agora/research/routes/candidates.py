"""Agora research candidates subrouter."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Header, Query

from .common import (
    AgoraResearchRouteContext,
    CandidatePoolCreateRequest,
    CandidateScoreRunRequest,
    CandidateMemberReviewRequest,
    CandidateDiscussionRequest,
    CandidateMonitoringRequest,
    _CAPABILITY,
    _CANDIDATE_NO_ORDER_ROUTE_PROOF,
    _MEMBER_ORDER_BY,
    _candidate_pool_detail_envelope,
    _candidate_detail_envelope,
    _candidate_list_envelope,
    _public_candidate_pool,
    _public_candidate_monitoring,
    _public_candidate_discussion,
    _candidate_public_member,
    _score_without_private_explanations,
    _candidate_pool_etag,
)


def build_candidates_router(ctx: AgoraResearchRouteContext) -> APIRouter:
    router = APIRouter(tags=["agora-research-candidates"])

    # GET /bff/agora/candidate-pools/lookup (Strategy-to-pool lookup)
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/lookup")
    def lookup_strategy_candidate_pool(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        strategy_id: Optional[str] = Query(default=None),
        strategy_version: Optional[str] = Query(default=None),
        strategy_ref: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        if not strategy_id and not strategy_ref:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "strategy_id or strategy_ref query parameter is required for candidate pool lookup",
                "missing_strategy_lookup_target",
            )
        target_id = strategy_id or ""
        pool = ctx.service.get_candidate_pool_for_strategy(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            strategy_id=target_id,
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
        )
        if pool is None:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                f"No candidate pool found for strategy '{target_id or strategy_ref}'",
                target_id or str(strategy_ref),
            )
        return _candidate_pool_detail_envelope(pool=pool, utc_now=ctx.utc_now, scope=scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/strategies/{strategy_id}/candidate-pool
    # -------------------------------------------------------------------
    @router.get("/bff/agora/strategies/{strategy_id}/candidate-pool")
    def get_strategy_candidate_pool(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        version: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_for_strategy(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            strategy_id=strategy_id,
            strategy_version=version,
        )
        if pool is None:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                f"No candidate pool found for strategy '{strategy_id}'",
                strategy_id,
            )
        return _candidate_pool_detail_envelope(pool=pool, utc_now=ctx.utc_now, scope=scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools")
    def list_candidate_pools(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        lifecycle_state: Optional[str] = Query(default=None),
        strategy_family: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None),
        strategy_version: Optional[str] = Query(default=None),
        strategy_ref: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pools = ctx.service.list_candidate_pools(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            lifecycle_state=lifecycle_state,
            strategy_family=strategy_family,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
        )
        return _candidate_list_envelope(
            items=[_public_candidate_pool(pool) for pool in pools[:page_size]],
            utc_now=ctx.utc_now,
            scope=scope,
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools", status_code=201)
    def create_candidate_pool(
        body: CandidatePoolCreateRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(scope=scope, endpoint="POST:/bff/agora/candidate-pools", key=idempotency_key)  # type: ignore[arg-type]
        pool = ctx.service.create_candidate_pool(body, scope=scope)
        return _candidate_pool_detail_envelope(pool=pool, utc_now=ctx.utc_now, scope=scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}")
    def get_candidate_pool(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        return _candidate_pool_detail_envelope(pool=pool, utc_now=ctx.utc_now, scope=scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/score
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/score")
    def get_candidate_pool_score(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        scores = ctx.service.list_candidate_scores(pool_id)
        if not scores:
            return {
                "status": "queued",
                "data": {"pool_id": pool_id, "score_results": 0},
                "meta": {
                    "snapshot_at": ctx.utc_now(),
                    "capability": _CAPABILITY,
                    "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                    "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
                },
            }
        scores.sort(
            key=lambda score: (
                score.get("rank") is None,
                int(score.get("rank") or 999999),
                -float(score.get("effective_score") or 0.0),
            )
        )
        return _candidate_list_envelope(
            items=[_score_without_private_explanations(score) for score in scores],
            utc_now=ctx.utc_now,
            scope=scope,
            meta_extra={
                "pool_id": pool_id,
                "recipe_id": (pool.get("metadata") or {}).get("recipe_id"),
                "recipe_version": (pool.get("metadata") or {}).get("recipe_version"),
                "data_cutoff": (pool.get("metadata") or {}).get("data_cutoff"),
                "last_score_run_at": (pool.get("metadata") or {}).get("last_score_run_at"),
            },
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/score
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/score", status_code=202)
    def trigger_candidate_pool_score(
        pool_id: str,
        body: Optional[CandidateScoreRunRequest] = None,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/score",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        recipe_id = body.recipe_id if body is not None else None
        scores, now = ctx.service.score_candidate_pool(pool, recipe_id=recipe_id, scope=scope)
        return {
            "status": "completed",
            "data": {
                "pool_id": pool_id,
                "scored_count": len(scores),
                "scored_at": now,
            },
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": _candidate_pool_etag(pool_id, int(pool.get("lock_version", 1))),
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members")
    def list_candidate_pool_members(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        lifecycle_state: Optional[str] = Query(default=None),
        band: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        page, next_token, total, metadata = ctx.service.list_candidate_members(
            pool,
            scope=scope,
            lifecycle_state=lifecycle_state,
            band=band,
            page_token=page_token,
            page_size=page_size,
        )
        return _candidate_list_envelope(
            items=page,
            utc_now=ctx.utc_now,
            scope=scope,
            page_info={
                "next_page_token": next_token,
                "page_size": len(page),
                "has_more": next_token is not None,
                "total": total,
                "order_by": _MEMBER_ORDER_BY,
            },
            meta_extra={
                "freshness": {
                    "pool_snapshot_at": pool.get("snapshot_at"),
                    "data_cutoff": metadata.get("data_cutoff"),
                    "last_score_run_at": metadata.get("last_score_run_at"),
                },
                "recipe_id": metadata.get("recipe_id"),
                "recipe_version": metadata.get("recipe_version"),
                "etag": _candidate_pool_etag(pool_id, int(pool.get("lock_version", 1))),
            },
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}")
    def get_candidate_pool_member(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        data = ctx.service.get_candidate_member_detail(pool, artifact_id, scope=scope)
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=artifact_id,
            data=data,
            utc_now=ctx.utc_now,
            scope=scope,
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review")
    def review_candidate_pool_member(
        pool_id: str,
        artifact_id: str,
        body: CandidateMemberReviewRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        updated_member, review, now = ctx.service.review_candidate_member(
            pool,
            artifact_id,
            body=body,
            scope=scope,
        )
        return {
            "status": "completed",
            "data": {
                "pool_id": pool_id,
                "artifact_id": artifact_id,
                "decision": body.decision,
                "candidate": (
                    _candidate_public_member(updated_member)
                    if updated_member is not None
                    else None
                ),
                "review": review,
                "negative_example": review["negative_example"],
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/discussions
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/discussions")
    def list_candidate_pool_discussions(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        kind: Optional[str] = Query(default=None),
        resolved: Optional[bool] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        discussions = ctx.service.list_candidate_discussions(
            pool_id,
            kind=kind,
            resolved=resolved,
            scope=scope,
        )
        return _candidate_list_envelope(
            items=[_public_candidate_discussion(d) for d in discussions],
            utc_now=ctx.utc_now,
            scope=scope,
            meta_extra={"pool_id": pool_id},
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/discussions
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/discussions", status_code=201)
    def create_candidate_pool_discussion(
        pool_id: str,
        body: CandidateDiscussionRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/discussions",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        created = ctx.service.create_candidate_discussion(
            pool_id=pool_id,
            body=body,
            scope=scope,
            subject_type="pool",
            subject_id=pool_id,
        )
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=created["discussion_id"],
            data=_public_candidate_discussion(created),
            utc_now=ctx.utc_now,
            scope=scope,
            object_type="candidate_discussion",
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions")
    def list_candidate_member_discussions(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        kind: Optional[str] = Query(default=None),
        resolved: Optional[bool] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.service.get_member_or_404(pool_id, artifact_id)
        discussions = ctx.service.list_candidate_discussions(
            pool_id,
            subject_type="member",
            subject_id=artifact_id,
            kind=kind,
            resolved=resolved,
            scope=scope,
        )
        return _candidate_list_envelope(
            items=[_public_candidate_discussion(d) for d in discussions],
            utc_now=ctx.utc_now,
            scope=scope,
            meta_extra={"pool_id": pool_id, "artifact_id": artifact_id},
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions", status_code=201)
    def create_candidate_member_discussion(
        pool_id: str,
        artifact_id: str,
        body: CandidateDiscussionRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.service.get_member_or_404(pool_id, artifact_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        created = ctx.service.create_candidate_discussion(
            pool_id=pool_id,
            body=body,
            scope=scope,
            subject_type="member",
            subject_id=artifact_id,
        )
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=created["discussion_id"],
            data=_public_candidate_discussion(created),
            utc_now=ctx.utc_now,
            scope=scope,
            object_type="candidate_discussion",
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/monitoring
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/monitoring")
    def list_candidate_pool_monitoring(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        monitoring_state: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        monitoring = ctx.service.list_candidate_monitoring(pool_id, monitoring_state=monitoring_state, scope=scope)
        return _candidate_list_envelope(
            items=[_public_candidate_monitoring(m) for m in monitoring],
            utc_now=ctx.utc_now,
            scope=scope,
            meta_extra={"pool_id": pool_id},
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring")
    def get_candidate_member_monitoring(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.read_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.service.get_member_or_404(pool_id, artifact_id)
        monitoring = ctx.service.get_candidate_monitoring(pool_id, artifact_id, scope=scope)
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=artifact_id,
            data=_public_candidate_monitoring(monitoring),
            utc_now=ctx.utc_now,
            scope=scope,
            object_type="candidate_monitoring",
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor", status_code=201)
    def upsert_candidate_pool_member_monitoring(
        pool_id: str,
        artifact_id: str,
        body: CandidateMonitoringRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.service.get_member_or_404(pool_id, artifact_id)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        upserted = ctx.service.upsert_candidate_monitoring(
            pool=pool,
            artifact_id=artifact_id,
            body=body,
            scope=scope,
        )
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=artifact_id,
            data=_public_candidate_monitoring(upserted),
            utc_now=ctx.utc_now,
            scope=scope,
            object_type="candidate_monitoring",
        )

    # -------------------------------------------------------------------
    # DELETE /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor
    # -------------------------------------------------------------------
    @router.delete("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor")
    def remove_candidate_pool_member_monitoring(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = ctx.write_scope(authorization, x_tenant_id)
        pool = ctx.service.get_candidate_pool_or_404(pool_id)
        ctx.service.require_pool_access(pool, scope)
        ctx.service.get_member_or_404(pool_id, artifact_id)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"DELETE:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        next_lock_version, now = ctx.service.remove_candidate_monitoring(
            pool=pool,
            artifact_id=artifact_id,
            scope=scope,
        )
        return {
            "status": "completed",
            "data": {"pool_id": pool_id, "artifact_id": artifact_id, "monitoring_state": "removed"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": _candidate_pool_etag(pool_id, next_lock_version),
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }

    return router
