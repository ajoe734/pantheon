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
    _MEMBER_PAGE_TOKEN_PREFIX,
    _REVIEW_DECISION_TO_LIFECYCLE,
    _candidate_pool_detail_envelope,
    _candidate_detail_envelope,
    _candidate_list_envelope,
    _public_candidate_pool,
    _public_candidate_monitoring,
    _public_candidate_discussion,
    _candidate_public_member,
    _load_default_scoring_recipe,
    _score_without_private_explanations,
    _member_truth_projection,
    _operator_grade_scope,
    _candidate_pool_etag,
    _parse_member_page_token,
    _validate_review_body,
    _discussion_record,
    _validate_monitoring_body,
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
        pool = ctx.store.get_candidate_pool_for_strategy(
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
        pool = ctx.store.get_candidate_pool_for_strategy(
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
        pools = ctx.store.list_candidate_pools(
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
        now = ctx.utc_now()
        pool = ctx.build_candidate_pool(body, scope, now)
        ctx.store.record_audit_action({
            "action_type": "candidate_pool.create",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool",
            "subject_id": pool["pool_id"],
            "payload": {"total": pool.get("total", 0)},
        })
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        scores = ctx.store.list_candidate_scores(pool_id)
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/score",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        recipe_id = body.recipe_id if body is not None else None
        scores = ctx.compute_and_store_candidate_scores(pool, recipe_id=recipe_id)
        now = ctx.utc_now()
        ctx.store.record_audit_action({
            "action_type": "candidate_pool.score",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool",
            "subject_id": pool_id,
            "payload": {"score_count": len(scores)},
        })
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        recipe = _load_default_scoring_recipe()
        ordered = sorted(
            pool.get("candidates", []),
            key=lambda member: (
                str(member.get("created_at") or ""),
                str(member.get("artifact_id") or ""),
            ),
        )
        members = []
        for member in ordered:
            if lifecycle_state and member.get("lifecycle_state") != lifecycle_state:
                continue
            projection = ctx.member_projection(pool, member, scope, recipe, evidence_summary_mode="list_response")
            if band and projection.get("band") != band:
                continue
            members.append(projection)
        offset = _parse_member_page_token(page_token, ctx.bff_error, ctx.error_code_enum)
        page = members[offset:offset + page_size]
        next_token = (
            f"{_MEMBER_PAGE_TOKEN_PREFIX}{offset + page_size}"
            if offset + page_size < len(members)
            else None
        )
        metadata = pool.get("metadata") or {}
        return _candidate_list_envelope(
            items=page,
            utc_now=ctx.utc_now,
            scope=scope,
            page_info={
                "next_page_token": next_token,
                "page_size": len(page),
                "has_more": next_token is not None,
                "total": len(members),
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        member = ctx.get_member_or_404(pool_id, artifact_id)
        score = ctx.store.get_candidate_score(pool_id, artifact_id)
        reviews = ctx.store.list_candidate_reviews(pool_id, artifact_id)
        monitoring = ctx.store.get_candidate_monitoring(pool_id, artifact_id)
        operator_grade = _operator_grade_scope(scope)
        truth = _member_truth_projection(
            pool=pool,
            member=member,
            score=score,
            reviews=reviews,
            monitoring=monitoring,
            recipe=_load_default_scoring_recipe(),
            evidence_summary_mode="detail",
            operator_grade=operator_grade,
        )
        data = {
            "candidate": _candidate_public_member(member),
            "score": (
                score
                if score is None or operator_grade
                else _score_without_private_explanations(score)
            ),
            "reviews": reviews,
            "monitoring": _public_candidate_monitoring(monitoring) if monitoring is not None else None,
            "negative_examples": [
                review for review in reviews
                if review.get("negative_example") is True
            ],
            "lifecycle_state": member.get("lifecycle_state"),
            **truth,
        }
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        member = ctx.get_member_or_404(pool_id, artifact_id)
        if member.get("lifecycle_state") == "rejected":
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Rejected candidate members are immutable retained negative examples",
                artifact_id,
            )
        _validate_review_body(body, ctx.bff_error, ctx.error_code_enum)
        now = ctx.utc_now()
        next_lifecycle = _REVIEW_DECISION_TO_LIFECYCLE[body.decision]
        review = {
            "review_id": str(uuid.uuid4()),
            "artifact_id": artifact_id,
            "decision": body.decision,
            "rationale": body.rationale,
            "score_override": body.score_override,
            "reviewed_by": body.reviewed_by,
            "reviewed_at": body.reviewed_at or now,
            "negative_example_tags": body.negative_example_tags,
            "negative_example": body.decision in {"reject", "park"},
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        }
        ctx.store.add_candidate_review(pool_id, artifact_id, review)
        updated_member = ctx.store.update_candidate_member(
            pool_id,
            artifact_id,
            {
                "lifecycle_state": next_lifecycle,
                "_updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        next_lock_version = int(pool.get("lock_version", 1)) + 1
        metadata = dict(pool.get("metadata") or {})
        metadata["last_reviewed_at"] = now
        ctx.store.update_candidate_pool(
            pool_id,
            {
                "metadata": metadata,
                "lock_version": next_lock_version,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        ctx.store.record_audit_action({
            "action_type": "candidate_member.review",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool_member",
            "subject_id": artifact_id,
            "payload": {"decision": body.decision, "lifecycle_state": next_lifecycle},
        })
        try:
            try:
                from ...trading_room.router import _get_store as _get_tr_store
            except ImportError:
                from services.control_plane.bff.agora.trading_room.router import _get_store as _get_tr_store
            tr_store = _get_tr_store()
            event_decision_state = {
                "approve_for_monitoring": "approved_by_trader",
                "send_to_shadow": "deferred",
                "needs_more_research": "deferred",
                "park": "rejected_by_trader",
                "reject": "rejected_by_trader",
            }.get(body.decision, "pending")
            event_suggested_action = {
                "approve_for_monitoring": "enter",
                "send_to_shadow": "review",
                "needs_more_research": "review",
                "park": "no_action",
                "reject": "no_action",
            }.get(body.decision, "review")
            event_state = "decided" if event_decision_state != "pending" else "pending_review"
            event_kind = "entry" if body.decision == "approve_for_monitoring" else "review"

            member_strategy_id = (
                member.get("strategy_id")
                or (pool.get("metadata") or {}).get("strategy_id")
                or (member.get("strategy_ref") or "").split(":")[-1]
                or "strategy-default"
            )
            member_strategy_registry_id = (
                member.get("strategy_spec_registry_id")
                or member.get("strategy_ref")
                or member_strategy_id
            )
            symbol = str(member.get("symbol") or member.get("title") or artifact_id)
            score_data = ctx.store.get_candidate_score(pool_id, artifact_id) or {}
            effective_score = float(score_data.get("effective_score") or 75.0)
            confidence_val = min(1.0, max(0.0, effective_score / 100.0))

            decision_event = {
                "spec_version": "1.0",
                "decision_event_id": f"trevt-cpm-{artifact_id[:12]}-{uuid.uuid4().hex[:8]}",
                "event_kind": event_kind,
                "origin": "trader_request",
                "strategy_id": member_strategy_id,
                "strategy_spec_registry_id": member_strategy_registry_id,
                "candidate_ref": artifact_id,
                "subject": {
                    "symbol": symbol,
                    "asset_class": member.get("asset_class") or "equity",
                    "venue": member.get("venue") or "default",
                },
                "state": event_state,
                "decision_state": event_decision_state,
                "triggered_at": now,
                "confidence": {
                    "value": confidence_val,
                    "basis": "mixed",
                    "calibration_state": "calibrated",
                    "sample_size": 100,
                },
                "probability": {
                    "target_outcome": "positive_alpha",
                    "horizon": "20d",
                    "value": confidence_val,
                },
                "expected_value": {
                    "horizon": "20d",
                    "unit": "pct_return",
                    "gross": 0.05,
                    "cost": 0.01,
                    "net": 0.04,
                    "downside": 0.02,
                },
                "rationale": [
                    {
                        "claim": body.rationale or f"Candidate {artifact_id} reviewed with decision {body.decision}",
                        "confidence": confidence_val,
                        "evidence_refs": [
                            {"ref_type": "candidate_pool_member", "ref_id": f"{pool_id}:{artifact_id}"}
                        ],
                    }
                ],
                "invalidation": {
                    "conditions": ["price_gap_breach", "regime_change"],
                    "current_state": "valid",
                    "last_checked_at": now,
                },
                "suggested_action": event_suggested_action,
                "suggested_size": {
                    "size_hint": "medium",
                    "portfolio_pct": 0.02,
                    "non_binding": True,
                },
                "no_order_route_proof": "agora_decision_support_only",
            }
            tr_store.upsert_decision_event(decision_event)
        except Exception:
            pass
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        discussions = ctx.store.list_candidate_discussions(
            pool_id,
            kind=kind,
            resolved=resolved,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/discussions",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        record = _discussion_record(
            body=body,
            pool_id=pool_id,
            subject_type="pool",
            subject_id=pool_id,
            scope=scope,
            now=ctx.utc_now(),
        )
        created = ctx.store.add_candidate_discussion(record)
        ctx.store.record_audit_action({
            "action_type": "candidate_discussion.create",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool",
            "subject_id": pool_id,
            "payload": {"discussion_id": created["discussion_id"]},
        })
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.get_member_or_404(pool_id, artifact_id)
        discussions = ctx.store.list_candidate_discussions(
            pool_id,
            subject_type="member",
            subject_id=artifact_id,
            kind=kind,
            resolved=resolved,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.get_member_or_404(pool_id, artifact_id)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        record = _discussion_record(
            body=body,
            pool_id=pool_id,
            subject_type="member",
            subject_id=artifact_id,
            scope=scope,
            now=ctx.utc_now(),
        )
        created = ctx.store.add_candidate_discussion(record)
        ctx.store.record_audit_action({
            "action_type": "candidate_member_discussion.create",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool_member",
            "subject_id": artifact_id,
            "payload": {"discussion_id": created["discussion_id"]},
        })
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        monitoring = ctx.store.list_candidate_monitoring(pool_id, monitoring_state=monitoring_state)
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.get_member_or_404(pool_id, artifact_id)
        monitoring = ctx.store.get_candidate_monitoring(pool_id, artifact_id)
        if monitoring is None:
            ErrorCode = ctx.error_code_enum()
            raise ctx.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Candidate monitoring record not found", artifact_id)
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.get_member_or_404(pool_id, artifact_id)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        _validate_monitoring_body(body, pool_id=pool_id, artifact_id=artifact_id, bff_error_fn=ctx.bff_error, error_code_enum_fn=ctx.error_code_enum)
        now = ctx.utc_now()
        monitoring_doc = {
            "artifact_id": artifact_id,
            "pool_id": pool_id,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "monitoring_state": body.monitoring_state,
            "trigger_conditions": body.trigger_conditions,
            "last_score_result_id": body.last_score_result_id,
            "review_due_at": body.review_due_at,
            "added_by": body.added_by or scope.user_id,
            "added_at": body.added_at or now,
            "notes": body.notes,
        }
        upserted = ctx.store.upsert_candidate_monitoring(pool_id, artifact_id, monitoring_doc)
        next_lock_version = int(pool.get("lock_version", 1)) + 1
        ctx.store.update_candidate_pool(
            pool_id,
            {"lock_version": next_lock_version},
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        pool["lock_version"] = next_lock_version
        ctx.store.record_audit_action({
            "action_type": "candidate_member.monitor_upsert",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_monitoring",
            "subject_id": artifact_id,
            "payload": {"monitoring_state": body.monitoring_state},
        })
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
        pool = ctx.get_candidate_pool_or_404(pool_id)
        ctx.require_pool_access(pool, scope)
        ctx.get_member_or_404(pool_id, artifact_id)
        ctx.require_candidate_pool_if_match(pool, if_match)
        ctx.require_idempotency_key(idempotency_key)
        ctx.check_idempotency(
            scope=scope,
            endpoint=f"DELETE:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
            key=idempotency_key,  # type: ignore[arg-type]
        )
        now = ctx.utc_now()
        existing = ctx.store.get_candidate_monitoring(pool_id, artifact_id) or {
            "artifact_id": artifact_id,
            "pool_id": pool_id,
            "added_by": scope.user_id,
            "added_at": now,
        }
        updated = {**existing, "monitoring_state": "removed", "removed_at": now, "tenant_id": scope.tenant_id, "user_id": scope.user_id}
        ctx.store.upsert_candidate_monitoring(pool_id, artifact_id, updated)
        next_lock_version = int(pool.get("lock_version", 1)) + 1
        ctx.store.update_candidate_pool(
            pool_id,
            {"lock_version": next_lock_version},
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        pool["lock_version"] = next_lock_version
        ctx.store.record_audit_action({
            "action_type": "candidate_member.monitor_remove",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_monitoring",
            "subject_id": artifact_id,
        })
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
