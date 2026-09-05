"""Persona league, quarterly ranking, recommendations, and promotion reviews routes."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from services.control_plane.bff.models import CommandType, ErrorCode, ObjectType
from ..service import (
    _PM12_LEAGUE_FORMULA_VERSION,
    _PM12_QUARTERLY_FORMULA_DOC_REF,
    _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER,
    _PROMOTION_REVIEW_ACTION_IDS,
    _PROMOTION_REVIEW_DECISIONS,
    _aggregate_group_surface,
    _bff_me_tenant_payload,
    _composed_surface_status,
    _filter_by_common_identifiers,
    _management_number,
    _performance_ranking_source_surface,
    _persona_league_payload,
    _pm12_attach_ranking_evidence,
    _pm12_attach_ranking_snapshot,
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
    _promotion_review_decision_payload,
    _promotion_review_decision_response,
    _promotion_review_find,
    _promotion_review_item_from_recommendation,
    _promotion_review_items,
    _promotion_review_quarter_from_id,
    _promotion_review_rationale,
    _promotion_review_revision_recommendation_id,
    _promotion_review_scoped_idempotency_key,
    _promotion_review_submission_projection,
    _promotion_review_submit_response,
    _promotion_review_surfaces,
    _promotion_review_target_id,
    _raise_if_promotion_review_direct_mutation_requested,
    _resolve_param,
    _sem_command_response,
    _validate_quarterly_ranking_recommendation_submit,
)
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)

_HUMAN_INBOX_PROMOTION_PRODUCER = "management_quarterly_ranking_recommendation_submit"


def build_ranking_router(ctx: PersonaRouteContext) -> APIRouter:
    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

    read_store = ctx.read_store
    command_store = ctx.command_store
    ranking_write_owner = ctx.ranking_write_owner
    write_owner = ctx.write_owner
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
    _reject_body_idempotency_key = ctx.reject_body_idempotency_key
    _resolve_final_idempotency_key = ctx.resolve_final_idempotency_key

    @router.post("/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit", status_code=202)
    async def bff_management_quarterly_ranking_recommendation_submit(
        recommendation_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: submit a PM-12 recommendation into Human Gate review without live mutation."""
        route_review_id = _promotion_review_clean_id(recommendation_id)
        recommendation_id = _promotion_review_revision_recommendation_id(
            route_review_id
        )
        identity = _extract_identity(authorization)
        if not {"operator", "approver", "admin"}.intersection(identity.roles):
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Quarterly ranking recommendation submission requires operator-level role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
                suggestion="Escalate to a user with operator, approver, or admin role",
            )
        _reject_body_idempotency_key(payload)
        _raise_if_promotion_review_direct_mutation_requested(payload)
        for key in ("recommendation_id", "recommendationId"):
            asserted_id = str(payload.get(key) or "").strip()
            if asserted_id and asserted_id != recommendation_id:
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "recommendation id assertion mismatch",
                    f"{key} must match the recommendation id in the route.",
                    precondition_failed="recommendation_id",
                )

        snapshot_at = utc_now()
        requested_ranking_snapshot_id = str(
            payload.get("ranking_snapshot_id") or ""
        ).strip()
        command_payload: Optional[Dict[str, Any]] = None
        if requested_ranking_snapshot_id:
            command_payload = {
                **payload,
                "quarter": (
                    payload.get("quarter")
                    or _promotion_review_quarter_from_id(recommendation_id)
                ),
                "recommendation_id": recommendation_id,
                "ranking_snapshot_id": requested_ranking_snapshot_id,
            }
            # Validate caller assertions against the durable snapshot before
            # resolving the dynamic current alias. Forged IDs and snapshots remain
            # validation failures rather than being masked as a missing current row.
            _validate_quarterly_ranking_recommendation_submit(
                command_payload,
                identity,
            )
        current_review: Optional[Dict[str, Any]] = None
        if not requested_ranking_snapshot_id:
            # A snapshotless request deliberately follows the mutable stable alias.
            # A caller that supplied an admitted snapshot has already been resolved
            # from the durable snapshot store above and must not be rebound to this
            # current-only projection after a lifecycle/session rotation.
            current_review, _, _, _ = _promotion_review_find(
                identity,
                recommendation_id,
                snapshot_at=snapshot_at,
                quarter=str(payload.get("quarter") or "").strip() or None,
                include_historical=False,
            )
            if current_review is None:
                if route_review_id == recommendation_id:
                    raise _bff_error(
                        404,
                        ErrorCode.RESOURCE_NOT_FOUND,
                        "Quarterly ranking recommendation not found",
                        f"Recommendation {recommendation_id} does not exist",
                        precondition_failed="recommendation_id",
                    )
                raise _bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "historical promotion review requires its immutable snapshot",
                    "Refresh the historical review and replay it with ranking_snapshot_id.",
                    precondition_failed="ranking_snapshot_id",
                )
            requested_ranking_snapshot_id = str(
                current_review.get("ranking_snapshot_id") or ""
            ).strip()
            command_payload = {
                **payload,
                "quarter": (
                    payload.get("quarter")
                    or _promotion_review_quarter_from_id(recommendation_id)
                ),
                "recommendation_id": recommendation_id,
                "ranking_snapshot_id": requested_ranking_snapshot_id,
            }
            _validate_quarterly_ranking_recommendation_submit(
                command_payload,
                identity,
            )
        assert command_payload is not None
        review_revision_id = str(
            command_payload.get("promotion_review_id")
            or command_payload.get("review_id")
            or ""
        ).strip()
        if not review_revision_id:
            raise _bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "admitted ranking snapshot has no promotion review revision",
                "The server could not bind the recommendation to its immutable snapshot.",
                precondition_failed="promotion_review_id",
            )
        if (
            route_review_id != recommendation_id
            and route_review_id != review_revision_id
        ):
            raise _bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "promotion review revision is stale",
                "The route revision does not identify the admitted ranking snapshot.",
                precondition_failed="promotion_review_id",
                suggestion="Refresh the current recommendation before submitting.",
            )

        existing_submission = _promotion_review_submission_projection(
            review_revision_id,
            include_source_recommendation=True,
        )
        if existing_submission:
            stored_source = existing_submission.get("source_recommendation")
            if not isinstance(stored_source, dict):
                raise _bff_error(
                    409,
                    ErrorCode.PRECONDITION_FAILED,
                    "submitted recommendation has no immutable source snapshot",
                    "The legacy submission is audit-readable but cannot be replayed as a snapshot-bound revision.",
                    precondition_failed="source_recommendation",
                    suggestion="Submit the current governed recommendation revision.",
                )
            stored_source = json.loads(json.dumps(stored_source))
            # Evidence visibility is request-scoped. Never replay stored evidence
            # bodies across identities or roles.
            stored_source["evidence_refs"] = []
            stored_source["evidence_ref_ids"] = []
            already = _promotion_review_item_from_recommendation(stored_source)
            replay_snapshot_id = str(
                existing_submission.get("ranking_snapshot_id")
                or already.get("ranking_snapshot_id")
                or ""
            ).strip()
            return JSONResponse(
                status_code=200,
                content=jsonable_encoder(
                    {
                        "data": {
                            "command_id": existing_submission.get("command_id"),
                            "review_id": already["review_id"],
                            "promotion_review_id": already["promotion_review_id"],
                            "recommendation_id": already["recommendation_id"],
                            "persona_id": already.get("persona_id"),
                            "action_id": already.get("action_id"),
                            "ranking_snapshot_id": replay_snapshot_id,
                            "status": already.get("status"),
                            "submitted": True,
                            "human_inbox_id": already.get("human_inbox_id"),
                            "requires_human_gate_decision": True,
                            "live_capital_mutation": False,
                            "review": already,
                            "links": already.get("links") or {},
                        },
                        "meta": {
                            **_snapshot_meta(snapshot_at),
                            "ranking_snapshot_id": replay_snapshot_id,
                            "idempotency": {
                                "replayed": True,
                                "source": "existing_submission",
                            },
                            "live_capital_mutation": False,
                            "direct_live_capital_mutation": False,
                            "requires_human_gate_decision": True,
                            "governance_policy": "promotion_governance_human_gate_no_direct_live_capital",
                        },
                    }
                ),
            )
        if route_review_id != recommendation_id:
            if current_review is None:
                current_review, _, _, _ = _promotion_review_find(
                    identity,
                    recommendation_id,
                    snapshot_at=snapshot_at,
                    quarter=str(payload.get("quarter") or "").strip() or None,
                    include_historical=False,
                )
            current_revision_id = str(
                (current_review or {}).get("promotion_review_id")
                or (current_review or {}).get("review_id")
                or ""
            ).strip()
            if route_review_id != current_revision_id:
                raise _bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "historical promotion review cannot create a new submission",
                    "Only the current admitted recommendation revision may create a Human Gate submission.",
                    precondition_failed="promotion_review_id",
                    suggestion="Refresh the current recommendation before submitting.",
                )

        source_recommendation = command_payload.get("source_recommendation")
        if not isinstance(source_recommendation, dict):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "admitted ranking snapshot has no recommendation",
                "The durable snapshot could not materialize the requested recommendation.",
                precondition_failed="recommendation_id",
            )
        review = _promotion_review_item_from_recommendation(source_recommendation)
        client_idempotency_key = _resolve_final_idempotency_key(
            idempotency_key,
            x_idempotency_key,
        )
        scoped_idempotency_key = _promotion_review_scoped_idempotency_key(
            client_idempotency_key,
            None,
            review["review_id"],
        )
        command_response = _sem_command_response(
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=review["review_id"],
            payload=command_payload,
            identity=identity,
            idempotency_key=scoped_idempotency_key,
            trusted_evidence_producer=_HUMAN_INBOX_PROMOTION_PRODUCER,
        )
        return _promotion_review_submit_response(
            command_response,
            review=review,
            client_idempotency_key=client_idempotency_key,
        )


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

        snapshot_at = utc_now()
        reviews, quarter_window, redacted_count, evidence_dataset_available = _promotion_review_items(
            identity,
            snapshot_at=snapshot_at,
            quarter=quarter,
            state=state,
            archetype=archetype,
            q=q,
        )
        if action_id:
            clean_action = str(action_id or "").strip()
            if clean_action not in _PROMOTION_REVIEW_ACTION_IDS:
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "action_id is not a promotion review action",
                    f"action_id must be one of {sorted(_PROMOTION_REVIEW_ACTION_IDS)}",
                    precondition_failed="action_id",
                )
            reviews = [item for item in reviews if item.get("action_id") == clean_action]
        if status:
            requested_statuses = {value.strip() for value in str(status).split(",") if value.strip()}
            reviews = [item for item in reviews if str(item.get("status") or "") in requested_statuses]

        total = len(reviews)
        page_items, next_page_token = _page_slice(reviews, page_token, page_size)
        surfaces = _promotion_review_surfaces(
            snapshot_at=snapshot_at,
            evidence_dataset_available=evidence_dataset_available,
        )
        summary = {
            "quarter": quarter_window["quarter"],
            "review_count": total,
            "returned_count": len(page_items),
            "pending_count": len([item for item in reviews if item.get("decision_status") == "pending"]),
            "decision_accepted_count": len([item for item in reviews if item.get("decision_status") == "accepted"]),
            "live_capital_mutation_count": 0,
            "requires_human_gate_decision": True,
            "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
            "policy": "promotion_governance_human_gate_no_direct_live_capital",
        }
        return {
            "data": {
                "id": f"promotion-reviews-{quarter_window['quarter'].lower()}",
                "quarter": quarter_window["quarter"],
                "quarter_window": quarter_window,
                "items": page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                **_snapshot_meta(snapshot_at),
                "surfaces": surfaces,
                "composition_sources": [
                    "GET /bff/management/quarterly-ranking/recommendations",
                    "GET /bff/management/human-inbox",
                    "GET /api/v1/operator/governance/approval-queue",
                ],
                "redacted_evidence_count": redacted_count,
                "requires_human_gate_decision": True,
                "live_capital_mutation": False,
                "direct_live_capital_mutation": False,
                "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
                "policy": "promotion_governance_human_gate_no_direct_live_capital",
            },
        }


    @router.get("/bff/management/promotion-reviews/{review_id}")
    async def bff_management_promotion_review_detail(
        review_id: str,
        quarter: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: promotion review detail by review id."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        review, quarter_window, redacted_count, evidence_dataset_available = _promotion_review_find(
            identity,
            review_id,
            snapshot_at=snapshot_at,
            quarter=quarter,
        )
        if review is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Promotion review not found",
                f"Promotion review {review_id} does not exist",
                precondition_failed="review_id",
            )
        return {
            "data": review,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "quarter": quarter_window["quarter"],
                "surfaces": _promotion_review_surfaces(
                    snapshot_at=snapshot_at,
                    evidence_dataset_available=evidence_dataset_available,
                ),
                "redacted_evidence_count": redacted_count,
                "requires_human_gate_decision": True,
                "live_capital_mutation": False,
                "direct_live_capital_mutation": False,
                "policy": "promotion_governance_human_gate_no_direct_live_capital",
            },
        }


    @router.post("/bff/management/promotion-reviews/{review_id}/decisions", status_code=202)
    async def bff_management_promotion_review_decision(
        review_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: accept a human-gated promotion review decision without live mutation."""
        identity = _extract_identity(authorization)
        if not {"approver", "admin"}.intersection(identity.roles):
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Promotion review decision requires 'approver' or 'admin' role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
                suggestion="Escalate to a user with approver or admin role",
            )
        _reject_body_idempotency_key(payload)
        _raise_if_promotion_review_direct_mutation_requested(payload)

        raw_decision = str(payload.get("decision") or "").strip().lower()
        if raw_decision not in _PROMOTION_REVIEW_DECISIONS:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "decision is invalid",
                f"decision must be one of {sorted(_PROMOTION_REVIEW_DECISIONS)}",
                precondition_failed="decision",
            )
        rationale = _promotion_review_rationale(payload)
        if raw_decision == "reject" and not rationale:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "reject decision requires a non-empty rationale",
                "rationale must be a non-empty string when decision=reject",
                precondition_failed="rationale",
            )

        snapshot_at = utc_now()
        clean_review_id = _promotion_review_clean_id(review_id)
        exact_revision_requested = (
            clean_review_id
            != _promotion_review_revision_recommendation_id(clean_review_id)
        )
        review, _quarter_window, _redacted_count, _evidence_dataset_available = _promotion_review_find(
            identity,
            review_id,
            snapshot_at=snapshot_at,
            quarter=str(payload.get("quarter") or "").strip() or None,
            # Stable aliases remain current-only. An exact immutable revision may
            # still receive its one pending decision after a newer ranking snapshot
            # becomes current; the revision id keeps that authority isolated.
            include_historical=exact_revision_requested,
        )
        if review is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Promotion review not found",
                f"Promotion review {review_id} does not exist",
                precondition_failed="review_id",
            )
        if exact_revision_requested and str(
            review.get("decision_status") or "pending"
        ).strip().lower() != "pending":
            current_review, _, _, _ = _promotion_review_find(
                identity,
                _promotion_review_revision_recommendation_id(clean_review_id),
                snapshot_at=snapshot_at,
                quarter=str(payload.get("quarter") or "").strip() or None,
                include_historical=False,
            )
            current_revision_id = str(
                (current_review or {}).get("promotion_review_id")
                or (current_review or {}).get("review_id")
                or ""
            ).strip()
            if clean_review_id != current_revision_id:
                # Resolved historical revisions remain read-only; in particular an
                # old approval must never be reused as authority for a newer
                # revision. The current exact revision still reaches the durable
                # idempotency layer so its original decision receipt can replay.
                raise _bff_error(
                    404,
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "Promotion review not found",
                    f"Promotion review {review_id} does not exist",
                    precondition_failed="review_id",
                )
        if not bool(review.get("submitted")):
            raise _bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Promotion review has not been submitted",
                "Submit the quarterly ranking recommendation before recording a Human Gate decision.",
                precondition_failed="recommendation_submission",
                suggestion="POST the recommendation submit route and then retry the decision.",
                details_extra={
                    "recommendationId": review.get("recommendation_id"),
                    "submitHref": (review.get("links") or {}).get("submit"),
                },
            )

        command_type = (
            CommandType.HUMAN_GATE_REJECT
            if raw_decision == "reject"
            else CommandType.HUMAN_GATE_APPROVE
        )
        command_payload = _promotion_review_decision_payload(
            payload=payload,
            review=review,
            decision=raw_decision,
            rationale=rationale,
            identity=identity,
        )
        client_idempotency_key = _resolve_final_idempotency_key(
            idempotency_key,
            x_idempotency_key,
        )
        scoped_idempotency_key = _promotion_review_scoped_idempotency_key(
            client_idempotency_key,
            None,
            review["review_id"],
        )
        command_response = _sem_command_response(
            command_type=command_type,
            target_type=ObjectType.HUMAN_GATE_ITEM,
            target_id=_promotion_review_target_id(review["review_id"]),
            payload=command_payload,
            identity=identity,
            idempotency_key=scoped_idempotency_key,
        )
        return _promotion_review_decision_response(
            command_response,
            review=review,
            decision=raw_decision,
            command_payload=command_payload,
            client_idempotency_key=client_idempotency_key,
        )


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
        snapshot_at = utc_now()
        all_rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranking_basis, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            [_pm12_persona_league_ranking_item(row) for row in all_rows],
            surface="rolling",
            period="short_cycle",
        )
        ranking_by_persona = {
            str(item.get("persona_id") or ""): item
            for item in ranking_basis
            if str(item.get("persona_id") or "")
        }
        rows = _pm12_filter_persona_items(
            [
            {
                **row,
                **{
                    field: ranking_by_persona.get(str(row.get("persona_id") or ""), {}).get(field)
                    for field in (
                        "eligible",
                        "exclusion_reason",
                        "exclusion_reasons",
                        "exclusion_codes",
                        "evidence_coverage",
                        "evidence_refs",
                        "source_confidence",
                        "ranking_snapshot_id",
                    )
                },
            }
            for row in all_rows
            ],
            state=state,
            archetype=archetype,
            q=q,
        )
        total = len(rows)
        page_items, next_page_token = _page_slice(rows, page_token, page_size)
        summary = {
            "persona_count": total,
            "returned_count": len(page_items),
            "ranking_snapshot_id": ranking_snapshot_id,
        }
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        surfaces = {
            "persona_league": _composed_surface_status(snapshot_at=snapshot_at),
            "personas": persona_surface,
            "route_policies": _composed_surface_status(snapshot_at=snapshot_at),
            "capability_snapshots": _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at),
            "persona_bindings": _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at),
            "persona_sessions": _dataset_surface_status("sessions", snapshot_at=snapshot_at),
            "teaching_sessions": _dataset_surface_status("teaching_sessions", snapshot_at=snapshot_at),
            "persona_memory": _composed_surface_status(snapshot_at=snapshot_at),
            "persona_health": dict(persona_surface),
        }
        return {
            "data": {
                "id": "management-persona-league",
                "ranking_snapshot_id": ranking_snapshot_id,
                "items": page_items,
                "summary": summary,
            },
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": {
                "snapshot_at": snapshot_at,
                "ranking_snapshot_id": ranking_snapshot_id,
                "total": total,
                "surfaces": surfaces,
                "composition_sources": [
                    "GET /bff/personas",
                    "GET /bff/personas/{id}/route-policy",
                    "GET /bff/personas/{id}/capabilities",
                    "GET /bff/personas/{id}/activity",
                    "GET /bff/personas/{id}/evaluations",
                    "GET /bff/personas/{id}/memory",
                    "GET /bff/v5/execution/persona-health",
                ],
            },
        }


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
        snapshot_at = utc_now()
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)

        # Pre-enrich and filter the base league rows represented as ranking items
        base_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            [_pm12_persona_league_ranking_item(row) for row in rows],
            surface="rolling",
            period="short_cycle",
        )
        enriched_items = _pm12_filter_persona_items(
            base_items,
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_items = _filter_by_common_identifiers(
            enriched_items,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )

        blocks = _pm12_persona_league_rankings(
            rows,
            criteria=criteria,
            limit=limit,
            base_items=filtered_items,
        )
        for block in blocks:
            block["ranking_snapshot_id"] = ranking_snapshot_id
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        rankings_surface = _aggregate_group_surface(
            "persona_league_rankings",
            list(source_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Persona league rankings aggregate unavailable.",
            degraded_message="Persona league rankings are degraded because one or more source surfaces are degraded.",
        )
        top_item = (blocks[0].get("items") or [None])[0] if blocks else None
        summary = {
            "persona_count": len(filtered_items),
            "criteria": [block["criteria"] for block in blocks],
            "top_persona_id": (top_item or {}).get("persona_id") if isinstance(top_item, dict) else None,
            "ranking_snapshot_id": ranking_snapshot_id,
        }
        return {
            "data": {
                "id": "management-persona-league-rankings",
                "ranking_snapshot_id": ranking_snapshot_id,
                "items": blocks,
                "summary": summary,
            },
            "page_info": {"next_page_token": None, "total": len(blocks), "page_size": len(blocks)},
            "meta": {
                "snapshot_at": snapshot_at,
                "ranking_snapshot_id": ranking_snapshot_id,
                "surfaces": {
                    name: _performance_ranking_source_surface(surface, snapshot_at=snapshot_at)
                    for name, surface in {
                        "persona_league_rankings": rankings_surface,
                        **source_surfaces,
                    }.items()
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/tiers",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
            },
        }


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
        snapshot_at = utc_now()
        normalized_direction = _pm12_normalize_mover_direction(direction)
        rows = _pm12_persona_league_rows(state=state, archetype=archetype, q=q, tenant_id=caller_tenant_id)
        movers, summary = _pm12_persona_league_mover_items(
            rows,
            direction=normalized_direction,
            limit=limit,
        )
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        history_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=False,
            missing_message="Historical persona league baseline is unavailable; movers are current-snapshot entries.",
        )
        movers_surface = _aggregate_group_surface(
            "persona_league_movers",
            [*source_surfaces.values(), history_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Persona league movers aggregate unavailable.",
            degraded_message="Persona league movers are degraded because one or more source surfaces are degraded.",
        )
        data = {
            "id": "management-persona-league-movers",
            "items": movers,
            "summary": summary,
            "policy": "read_only_governance_advisory",
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": None,
                "total": summary["mover_count"],
                "page_size": len(movers),
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_league_movers": movers_surface,
                    "persona_league_history": history_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
                "policy": "read_only_governance_advisory",
                "baseline_status": "unavailable",
            },
        }


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
        snapshot_at = utc_now()
        rows = _pm12_persona_league_rows(state=state, archetype=archetype, q=q, tenant_id=caller_tenant_id)

        base_items = [_pm12_persona_league_ranking_item(row) for row in rows]
        filtered_items = _filter_by_common_identifiers(
            base_items,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )

        tiers, assignments, summary = _pm12_persona_league_tier_payload(
            rows,
            ranking_items=filtered_items,
        )
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        tiers_surface = _aggregate_group_surface(
            "persona_league_tiers",
            list(source_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Persona league tiers aggregate unavailable.",
            degraded_message="Persona league tiers are degraded because one or more source surfaces are degraded.",
        )
        return {
            "data": {
                "id": "management-persona-league-tiers",
                "items": tiers,
                "summary": summary,
                "related": {
                    "assignments": assignments,
                },
                "policy": "read_only_governance_advisory",
            },
            "page_info": {"next_page_token": None, "total": len(tiers), "page_size": len(tiers)},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_league_tiers": tiers_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
                "policy": "read_only_governance_advisory",
            },
        }


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
        snapshot_at = utc_now()
        rows = _pm12_persona_league_rows(state=state, archetype=archetype, q=q, tenant_id=caller_tenant_id)[:limit]
        bucket_key, buckets = _pm12_heatmap_buckets(
            snapshot_at,
            bucket=bucket,
            bucket_count=bucket_count,
        )
        heatmap_rows, _, summary = _pm12_persona_league_heatmap_rows(rows, buckets)
        summary = {
            **summary,
            "bucket": bucket_key,
            "returned_persona_count": len(heatmap_rows),
        }
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        heatmap_surface = _aggregate_group_surface(
            "persona_league_heatmap",
            list(source_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Persona league heatmap aggregate unavailable.",
            degraded_message="Persona league heatmap is degraded because one or more source surfaces are degraded.",
        )
        data = {
            "id": "persona-league-heatmap",
            "heatmap_id": "persona-league-heatmap",
            "bucket": bucket_key,
            "items": heatmap_rows,
            "buckets": buckets,
            "summary": summary,
            "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "basis": "persona_x_time_bucket_composite_score",
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": None,
                "total": len(heatmap_rows),
                "page_size": len(heatmap_rows),
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_league_heatmap": heatmap_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
                "policy": "read_only_governance_advisory",
            },
        }


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
        snapshot_at = utc_now()
        quarter_window = _pm12_quarter_window(quarter, snapshot_at)
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
        (
            public_evidence_refs,
            canonical_evidence_refs,
            redacted_count,
            evidence_dataset_available,
        ) = _pm12_public_quarter_evidence_refs(
            identity,
            quarter_window,
        )
        ranked_items = _pm12_attach_ranking_evidence(
            ranked_items,
            public_evidence_refs,
            canonical_evidence_refs=canonical_evidence_refs,
        )
        ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            ranked_items,
            surface="quarterly",
            period=quarter_window["quarter"],
        )

        # Apply common filters after the immutable full-universe snapshot is built.
        enriched_items = _pm12_filter_persona_items(
            ranked_items,
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_items = _filter_by_common_identifiers(
            enriched_items,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )
        total = len(filtered_items)
        page_items, next_page_token = _page_slice(filtered_items, page_token, page_size)

        formula = _pm12_quarter_formula_payload()
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _dataset_surface_status(
            "evidence_refs",
            snapshot_at=snapshot_at,
            has_data=evidence_dataset_available,
            missing_message="Evidence reference read surface is unavailable.",
        )
        quarterly_surface = _aggregate_group_surface(
            "quarterly_ranking",
            [*source_surfaces.values(), formula_surface, evidence_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking aggregate unavailable.",
            degraded_message="Quarterly ranking is degraded because one or more source surfaces are degraded.",
        )
        quarterly_surfaces = {
            name: _performance_ranking_source_surface(surface, snapshot_at=snapshot_at)
            for name, surface in {
                "quarterly_ranking": quarterly_surface,
                "formula": formula_surface,
                "evidence_refs": evidence_surface,
                "knowledge_evidence": evidence_surface,
                **source_surfaces,
            }.items()
        }
        top_item = filtered_items[0] if filtered_items else None
        summary = {
            "quarter": quarter_window["quarter"],
            "formula_version": formula["formula_version"],
            "persona_count": total,
            "ranking_universe_count": len(rows),
            "ranked_count": total,
            "returned_count": len(page_items),
            "top_persona_id": (top_item or {}).get("persona_id") if isinstance(top_item, dict) else None,
            "evidence_ref_count": len(public_evidence_refs),
            "redacted_evidence_count": redacted_count,
            "basis": formula["basis"],
            "ranking_snapshot_id": ranking_snapshot_id,
        }
        data = {
            "id": f"pm12-quarterly-ranking-{quarter_window['quarter'].lower()}",
            "ranking_snapshot_id": ranking_snapshot_id,
            "quarter": quarter_window["quarter"],
            "quarter_window": quarter_window,
            "formula": formula,
            "items": page_items,
            "evidence_refs": public_evidence_refs,
            "summary": summary,
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                **_snapshot_meta(snapshot_at),
                "ranking_snapshot_id": ranking_snapshot_id,
                "surfaces": quarterly_surfaces,
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /api/v1/knowledge/evidence",
                ],
                "policy": "read_only_governance_advisory",
                "redacted_evidence_count": redacted_count,
            },
        }


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

        snapshot_at = utc_now()
        quarter_window = _pm12_quarter_window(quarter, snapshot_at)
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
        (
            public_evidence_refs,
            canonical_evidence_refs,
            redacted_count,
            evidence_dataset_available,
        ) = _pm12_public_quarter_evidence_refs(
            identity,
            quarter_window,
        )
        ranked_items = _pm12_attach_ranking_evidence(
            ranked_items,
            public_evidence_refs,
            canonical_evidence_refs=canonical_evidence_refs,
        )
        ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            ranked_items,
            surface="quarterly",
            period=quarter_window["quarter"],
        )
        ranking_item = _pm12_quarterly_find_persona_item(ranked_items, resolved_persona_id)
        if ranking_item is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Quarterly ranking persona not found",
                f"Persona {resolved_persona_id} is not present in the requested quarterly ranking.",
                precondition_failed="personaId",
                correlation_id=correlation_id,
            )

        legacy_filtered_results = _pm12_filter_persona_items(
            [ranking_item],
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_results = _filter_by_common_identifiers(
            legacy_filtered_results,
            persona_id=resolved_persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )
        if not filtered_results:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Quarterly ranking persona not found matching filter criteria",
                f"Persona {resolved_persona_id} does not match the requested filter criteria.",
                precondition_failed="personaId",
                correlation_id=correlation_id,
            )

        ranking_item = filtered_results[0]

        row = _pm12_quarterly_find_persona_row(rows, resolved_persona_id)
        item_evidence_refs = list(ranking_item.get("evidence_refs") or [])
        drilldown = _pm12_quarterly_drilldown_payload(
            item=ranking_item,
            row=row,
            quarter_window=quarter_window,
            ranked_count=len(ranked_items),
            evidence_refs=item_evidence_refs,
        )

        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _dataset_surface_status(
            "evidence_refs",
            snapshot_at=snapshot_at,
            has_data=evidence_dataset_available,
            missing_message="Evidence reference read surface is unavailable.",
        )
        quarterly_surface = _aggregate_group_surface(
            "quarterly_ranking",
            [*source_surfaces.values(), formula_surface, evidence_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking aggregate unavailable.",
            degraded_message="Quarterly ranking is degraded because one or more source surfaces are degraded.",
        )
        drilldown_surface = _aggregate_group_surface(
            "quarterly_ranking_drilldown",
            [quarterly_surface, formula_surface, evidence_surface, *source_surfaces.values()],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking drilldown aggregate unavailable.",
            degraded_message="Quarterly ranking drilldown is degraded because one or more source surfaces are degraded.",
        )
        summary = dict(drilldown["summary"])
        summary["redacted_evidence_count"] = redacted_count

        return {
            "data": drilldown,
            "item": ranking_item,
            "ranking_item": ranking_item,
            "contributions": drilldown["contributions"],
            "contribution_breakdown": drilldown["contribution_breakdown"],
            "source_breakdown": drilldown["source_breakdown"],
            "formula": drilldown["formula"],
            "quarter_window": quarter_window,
            "evidence_refs": item_evidence_refs,
            "summary": summary,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "ranking_snapshot_id": ranking_snapshot_id,
                "correlation_id": correlation_id,
                "surfaces": {
                    "quarterly_ranking_drilldown": drilldown_surface,
                    "quarterly_ranking": quarterly_surface,
                    "formula": formula_surface,
                    "evidence_refs": evidence_surface,
                    "knowledge_evidence": evidence_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/quarterly-ranking",
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /api/v1/knowledge/evidence",
                ],
                "policy": "read_only_governance_advisory",
                "redacted_evidence_count": redacted_count,
            },
        }


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
        snapshot_at = utc_now()
        quarter_window = _pm12_quarter_window(quarter, snapshot_at)
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
        (
            public_evidence_refs,
            canonical_evidence_refs,
            redacted_count,
            evidence_dataset_available,
        ) = _pm12_public_quarter_evidence_refs(
            identity,
            quarter_window,
        )
        ranked_items = _pm12_attach_ranking_evidence(
            ranked_items,
            public_evidence_refs,
            canonical_evidence_refs=canonical_evidence_refs,
        )
        ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            ranked_items,
            surface="quarterly",
            period=quarter_window["quarter"],
        )
        recommendations = _pm12_quarterly_recommendations(
            ranked_items,
            quarter_window=quarter_window,
            evidence_refs=public_evidence_refs,
        )

        enriched_recs = _pm12_filter_persona_items(
            recommendations,
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_recs = _filter_by_common_identifiers(
            enriched_recs,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )
        total = len(filtered_recs)
        page_items, next_page_token = _page_slice(filtered_recs, page_token, page_size)

        formula = _pm12_quarter_formula_payload()
        action_counts = {
            action_id: len([item for item in filtered_recs if item.get("action_id") == action_id])
            for action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER
        }
        filtered_persona_ids = {
            str(item.get("persona_id") or "")
            for item in filtered_recs
            if str(item.get("persona_id") or "")
        }
        top_item = next(
            (
                item
                for item in ranked_items
                if str(item.get("persona_id") or "") in filtered_persona_ids
            ),
            None,
        )
        summary = {
            "quarter": quarter_window["quarter"],
            "formula_version": formula["formula_version"],
            "persona_count": len(rows),
            "ranked_count": len(ranked_items),
            "recommendation_count": total,
            "returned_count": len(page_items),
            "top_persona_id": (top_item or {}).get("persona_id") if isinstance(top_item, dict) else None,
            "human_gate_decision_count": total,
            "live_capital_mutation_count": 0,
            "evidence_ref_count": len(public_evidence_refs),
            "redacted_evidence_count": redacted_count,
            "by_action": action_counts,
            "allowed_actions": list(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER),
            "basis": formula["basis"],
            "policy": "read_only_governance_advisory",
            "ranking_snapshot_id": ranking_snapshot_id,
        }

        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _dataset_surface_status(
            "evidence_refs",
            snapshot_at=snapshot_at,
            has_data=evidence_dataset_available,
            missing_message="Evidence reference read surface is unavailable.",
        )
        approval_queue_surface = _dataset_surface_status("approval_queue_items", snapshot_at=snapshot_at)
        human_gate_surface = _dataset_surface_status("approval_decisions", snapshot_at=snapshot_at)
        human_inbox_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=(
                approval_queue_surface.get("status") != "unavailable"
                or human_gate_surface.get("status") != "unavailable"
            ),
            missing_message="Human Inbox and HumanGateDecision read surfaces are unavailable.",
        )
        quarterly_surface = _aggregate_group_surface(
            "quarterly_ranking",
            [*source_surfaces.values(), formula_surface, evidence_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking aggregate unavailable.",
            degraded_message="Quarterly ranking is degraded because one or more source surfaces are degraded.",
        )
        recommendations_surface = _aggregate_group_surface(
            "quarterly_ranking_recommendations",
            [
                quarterly_surface,
                formula_surface,
                evidence_surface,
                approval_queue_surface,
                human_gate_surface,
                human_inbox_surface,
            ],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking recommendations aggregate unavailable.",
            degraded_message="Quarterly ranking recommendations are degraded because one or more governance source surfaces are degraded.",
        )
        governance_destinations = ["human_inbox", "governance_queue", "human_gate_decision"]
        data = {
            "id": f"pm12-quarterly-ranking-recommendations-{quarter_window['quarter'].lower()}",
            "ranking_snapshot_id": ranking_snapshot_id,
            "quarter": quarter_window["quarter"],
            "quarter_window": quarter_window,
            "formula": formula,
            "items": page_items,
            "evidence_refs": public_evidence_refs,
            "summary": summary,
            "policy": "read_only_governance_advisory",
            "governance_destinations": governance_destinations,
            "allowed_actions": list(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER),
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                **_snapshot_meta(snapshot_at),
                "ranking_snapshot_id": ranking_snapshot_id,
                "surfaces": {
                    "quarterly_ranking_recommendations": recommendations_surface,
                    "quarterly_ranking": quarterly_surface,
                    "formula": formula_surface,
                    "evidence_refs": evidence_surface,
                    "knowledge_evidence": evidence_surface,
                    "human_inbox": human_inbox_surface,
                    "governance_queue": approval_queue_surface,
                    "human_gate_decision": human_gate_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/quarterly-ranking",
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /api/v1/knowledge/evidence",
                    "GET /bff/management/human-inbox",
                    "GET /api/v1/operator/governance/approval-queue",
                ],
                "policy": "read_only_governance_advisory",
                "governance_destinations": governance_destinations,
                "redacted_evidence_count": redacted_count,
                "live_capital_mutation": False,
            },
        }


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
        entry = read_store.get_persona_league_entry(persona_id)
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
