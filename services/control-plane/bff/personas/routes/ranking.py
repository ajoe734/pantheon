"""Persona league, quarterly ranking, recommendations, and promotion reviews routes.

Bounded cohesive exceptions (SD4.2A / Section D):
This module defines HTTP routes for PM-12 persona league, movers, tiers, heatmap,
quarterly ranking, drilldowns, recommendations, and promotion reviews.
All underlying business workflows (score computation, row filtering, tier assignments,
movement calculation, recommendation enrichment, governance availability, and snapshot
orchestration) are cohesive domain query/command objectives encapsulated in PersonaService.
The route handlers maintain bounded HTTP responsibilities: parameter extraction, authentication
and role checks, caller tenant resolution, input validation, and DTO/status mapping.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response

from services.control_plane.bff.models import CommandType, ErrorCode, ObjectType
from services.control_plane.bff.ports.read_surface_ports import PaperReconcilerUnavailableError
from ..service import (
    _PM12_LEAGUE_FORMULA_VERSION,
    _PM12_QUARTERLY_FORMULA_DOC_REF,
    _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER,
    _PROMOTION_REVIEW_ACTION_IDS,
    _aggregate_group_surface,
    _bff_me_tenant_payload,
    _composed_surface_status,
    _filter_by_common_identifiers,
    _management_number,
    _performance_ranking_source_surface,
    _persona_league_payload,
    _pm12_attach_ranking_evidence,
    _pm12_filter_persona_items,
    _pm12_heatmap_buckets,
    _pm12_normalize_mover_direction,
    _pm12_persona_league_heatmap_rows,
    _pm12_persona_league_mover_items,
    _pm12_persona_league_ranking_item,
    _pm12_persona_league_rankings,
    _pm12_persona_league_rows,
    _pm12_persona_league_source_surfaces,
    _pm12_persona_league_tier_payload,
    _pm12_public_quarter_evidence_refs,
    _pm12_quarter_formula_governance_evidence_refs,
    _pm12_quarter_formula_payload,
    _pm12_quarter_window,
    _pm12_quarterly_drilldown_payload,
    _pm12_quarterly_find_persona_item,
    _pm12_quarterly_find_persona_row,
    _pm12_quarterly_ranking_items,
    _pm12_quarterly_recommendations,
    _promotion_review_clean_id,
    _promotion_review_items,
    _promotion_review_surfaces,
    _resolve_param,
    _sem_command_response,
)
from ...command_adapters.retired import reject_retired_command
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)


def _call_or_503_when_reconciler_down(bff_error: Any, fn: Any, **kwargs: Any) -> Any:
    try:
        return fn(**kwargs)
    except PaperReconcilerUnavailableError as exc:
        raise bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Paper fleet reconciler unavailable", str(exc)) from exc


def build_ranking_router(ctx: PersonaRouteContext) -> APIRouter:
    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

    _service = ctx.service
    _extract_identity = ctx.extract_identity
    _require_read_role = ctx.require_read_role
    _require_operator_role = ctx.require_operator_role
    _bff_error = ctx.bff_error
    utc_now = ctx.utc_now
    _page_slice = ctx.page_slice
    _snapshot_meta = ctx.snapshot_meta
    _dataset_surface_status = ctx.dataset_surface_status
    _read_surface_meta = ctx.read_surface_meta
    _raise_if_read_surface_unavailable = ctx.raise_if_read_surface_unavailable
    _resolve_final_idempotency_key = ctx.resolve_final_idempotency_key

    @router.post("/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit", status_code=202)
    async def bff_management_quarterly_ranking_recommendation_submit(
        recommendation_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """Retired: the evaluator's Governance proposal is the only approval record."""
        identity = _extract_identity(authorization)
        if not {"operator", "approver", "admin"}.intersection(identity.roles):
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Quarterly ranking recommendation submission requires operator-level role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
            )
        reject_retired_command("QuarterlyRankingRecommendationSubmit")

    @router.get("/bff/management/promotion-reviews")
    async def bff_management_promotion_reviews(
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        action_id: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: promotion review queue derived from PM-12 recommendations."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _service.get_promotion_reviews(
            identity=identity,
            quarter=quarter,
            state=state,
            archetype=archetype,
            q=q,
            action_id=action_id,
            status=status,
            page_token=page_token,
            page_size=page_size,
            page_slice_fn=_page_slice,
        )


    @router.get("/bff/management/promotion-reviews/{review_id}")
    async def bff_management_promotion_review_detail(
        review_id: str,
        quarter: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: promotion review detail by review id."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _service.get_promotion_review_detail(
            identity=identity,
            review_id=review_id,
            quarter=quarter,
        )


    @router.post("/bff/management/promotion-reviews/{review_id}/decisions", status_code=202)
    async def bff_management_promotion_review_decision(
        review_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """Retired: the Governance ApprovalDecision is decided through /bff/approvals/{id}/decide."""
        _extract_identity(authorization)
        reject_retired_command("PromotionReviewDecision")


    @router.get("/bff/management/persona-league")
    async def bff_management_persona_league(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: PM-12 persona-league table composed from persona-side read surfaces."""
        state = _resolve_param(state)
        archetype = _resolve_param(archetype)
        q = _resolve_param(q)
        page_token = _resolve_param(page_token)
        page_size = _resolve_param(page_size)
        authorization = _resolve_param(authorization)

        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _service.get_persona_league(
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            page_token=page_token,
            page_size=page_size,
            page_slice_fn=_page_slice,
        )


    @router.get("/bff/management/persona-league/rankings")
    async def bff_management_persona_league_rankings(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        criteria: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 persona-league ranking blocks computed from league rows."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _service.get_persona_league_rankings(
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            criteria=criteria,
            limit=limit,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of,
        )


    @router.get("/bff/management/persona-league/movers")
    async def bff_management_persona_league_movers(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        direction: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: PM-12 persona-league movement list computed from league rows."""
        state = _resolve_param(state)
        archetype = _resolve_param(archetype)
        q = _resolve_param(q)
        direction = _resolve_param(direction)
        limit = _resolve_param(limit)
        authorization = _resolve_param(authorization)

        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _service.get_persona_league_movers(
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            direction=direction,
            limit=limit,
        )


    @router.get("/bff/management/persona-league/tiers")
    async def bff_management_persona_league_tiers(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 persona-league tier definitions and current season assignment."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _service.get_persona_league_tiers(
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of,
        )


    @router.get("/bff/management/persona-league/heatmap")
    async def bff_management_persona_league_heatmap(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        bucket: str = Query(default="day"),
        bucket_count: int = Query(default=7, ge=1, le=90),
        limit: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona x time-bucket league heatmap using the PM-12 composite score."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _service.get_persona_league_heatmap(
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            bucket=bucket,
            bucket_count=bucket_count,
            limit=limit,
        )


    @router.get("/bff/management/quarterly-ranking/formula")
    async def bff_management_quarterly_ranking_formula(
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: PM-12 quarterly ranking formula weights, version, and governance trace."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = utc_now()
        formula = _pm12_quarter_formula_payload()
        evidence_refs = _pm12_quarter_formula_governance_evidence_refs()
        version_history = list(formula.get("version_history") or [])
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=bool(evidence_refs),
            missing_message="Quarterly ranking formula governance evidence is unavailable.",
        )
        weights = formula.get("weights") if isinstance(formula.get("weights"), dict) else {}
        summary = {
            "formula_id": formula["formula_id"],
            "formula_version": formula["formula_version"],
            "component_count": len(formula.get("components") or []),
            "weight_total": round(sum(_management_number(value) or 0.0 for value in weights.values()), 6),
            "evidence_ref_count": len(evidence_refs),
            "basis": formula["basis"],
            "policy": formula["policy"],
        }
        return {
            "data": formula,
            "formula": formula,
            "version_history": version_history,
            "evidence_refs": evidence_refs,
            "summary": summary,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "surfaces": {
                    "quarterly_ranking_formula": formula_surface,
                    "formula": formula_surface,
                    "governance_evidence": evidence_surface,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league/rankings",
                    "GET /api/v1/knowledge/evidence",
                    _PM12_QUARTERLY_FORMULA_DOC_REF,
                ],
                "policy": formula["policy"],
                "version_policy": "formula_version_changes_require_governance_evidence",
            },
        }


    @router.get("/bff/management/quarterly-ranking")
    async def bff_management_quarterly_ranking(
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 quarterly persona ranking composed from league rows and evidence."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _call_or_503_when_reconciler_down(
            _bff_error,
            _service.get_quarterly_ranking,
            quarter=quarter,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            page_token=page_token,
            page_size=page_size,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of,
            page_slice_fn=_page_slice,
        )


    @router.get("/bff/management/quarterly-ranking/drilldown")
    async def bff_management_quarterly_ranking_drilldown(
        response: Response,
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona_id_snake: Optional[str] = Query(default=None, alias="persona_id"),
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        authorization: Optional[str] = Header(default=None),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        # Common filters:
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 single-persona contribution breakdown for quarterly ranking."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        correlation_id = str(x_correlation_id or "").strip() or f"pm12-drilldown-{uuid.uuid4().hex}"
        response.headers["X-Correlation-Id"] = correlation_id

        resolved_persona_id = str(persona_id or persona_id_snake or "").strip()
        if not resolved_persona_id:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "personaId is required",
                "Quarterly ranking drilldown requires personaId or persona_id.",
                precondition_failed="personaId",
                correlation_id=correlation_id,
            )

        return _service.get_quarterly_ranking_drilldown(
            quarter=quarter,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            resolved_persona_id=resolved_persona_id,
            correlation_id=correlation_id,
            state=state,
            archetype=archetype,
            q=q,
            persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of,
        )


    @router.get("/bff/management/quarterly-ranking/recommendations")
    async def bff_management_quarterly_ranking_recommendations(
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 quarterly governance recommendations without live mutations."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        return _service.get_quarterly_ranking_recommendations(
            quarter=quarter,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            state=state,
            archetype=archetype,
            q=q,
            page_token=page_token,
            page_size=page_size,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of,
            page_slice_fn=_page_slice,
        )


    @router.get("/bff/persona-league")
    async def bff_persona_league(
        market_scope: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _persona_league_payload(
            snapshot_at=utc_now(),
            market_scope=market_scope,
            status=status,
            page_token=page_token,
            page_size=page_size,
        )


    @router.get("/bff/persona-league/{persona_id}")
    @router.get("/bff/management/persona-league/{persona_id}")
    async def bff_persona_league_detail(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = utc_now()
        entry = _service.get_persona_league_entry(persona_id)
        if not entry:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona league entry not found",
                f"Persona league entry {persona_id} does not exist",
            )
        return {
            "data": entry,
            "meta": _read_surface_meta(
                "persona_league",
                "persona_league_detail",
                snapshot_at=snapshot_at,
            ),
        }

    return router
