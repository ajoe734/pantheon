"""Research knowledge, workbench, institutional memory, and synthesis routes."""
from __future__ import annotations

import inspect
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request

from .common import (
    ResearchRouteContext,
    _authorization,
    _body_parameter,
    _path,
    _signature,
    _signature_query,
)

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from ..models import ErrorCode


def _build_knowledge_workbench_overview(ctx: ResearchRouteContext, snapshot_at: str) -> Dict[str, Any]:
    return ctx.service.get_knowledge_workbench_overview(snapshot_at=snapshot_at)



def build_knowledge_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    # Endpoints
    async def endpoint_knowledge_workbench(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        if ctx.build_knowledge_workbench is not None:
            result = ctx.build_knowledge_workbench()
            return await result if inspect.isawaitable(result) else result
        return _build_knowledge_workbench_overview(ctx, ctx.utc_now())

    async def endpoint_create_note(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        payload = await ctx.body(request)
        try:
            return ctx.service.create_research_note(
                payload,
                operator_id=str(getattr(identity, "operator_id", "") or ""),
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_list_notes(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        try:
            return ctx.service.list_research_notes(
                attachment_type=ctx.query(request, "attachment_type"),
                attachment_ref=ctx.query(request, "attachment_ref"),
                owner_ref=ctx.query(request, "owner_ref"),
                tags=ctx.query(request, "tags"),
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_get_note(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        identifier = str(request.path_params.get("note_id") or "")
        try:
            return ctx.service.get_research_note(identifier)
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_list_evidence(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        try:
            return ctx.service.list_evidence_refs(
                identity=identity,
                linked_entity_type=ctx.query(request, "linked_entity_type"),
                linked_entity_ref=ctx.query(request, "linked_entity_ref"),
                link_type=ctx.query(request, "link_type"),
                credibility_tier=ctx.query(request, "credibility_tier"),
                verified_raw=ctx.query(request, "verified"),
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_get_evidence(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        identifier = str(request.path_params.get("ref_id") or "")
        try:
            return ctx.service.get_evidence_ref(identifier, identity=identity)
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_list_insights(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        confidence_raw = ctx.query(request, "confidence_min")
        confidence_min = float(confidence_raw) if confidence_raw is not None else None
        try:
            return ctx.service.list_insight_cards(
                status=ctx.query(request, "status", "active"),
                tag=ctx.query(request, "tag"),
                linked_entity_type=ctx.query(request, "linked_entity_type"),
                linked_entity_ref=ctx.query(request, "linked_entity_ref"),
                recency=ctx.query(request, "recency", "all"),
                confidence_min=confidence_min,
                include_inactive=str(ctx.query(request, "include_inactive", "false") or "false").lower() == "true",
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_get_insight(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        identifier = str(request.path_params.get("insight_id") or "")
        try:
            return ctx.service.get_insight_card(identifier)
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_list_strategy_specs(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        try:
            return ctx.service.list_strategy_specs(
                lifecycle_state=ctx.query(request, "lifecycle_state", "all"),
                archetype=ctx.query(request, "source_kind"),
                persona_id=ctx.query(request, "persona_id"),
                include_retired=ctx.query(request, "include_retired", "false") == "true",
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_strategy_versions(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        strategy_id = str(request.path_params.get("strategy_id") or "")
        try:
            return ctx.service.get_strategy_spec_versions(strategy_id)
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_strategy_compare(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        strategy_id = str(request.path_params.get("strategy_id") or "")
        try:
            return ctx.service.compare_strategy_spec_versions(
                strategy_id,
                left_version=ctx.query(request, "left_version"),
                right_version=ctx.query(request, "right_version"),
                base_version=ctx.query(request, "base_version"),
                target_version=ctx.query(request, "target_version"),
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_get_strategy_spec(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        strategy_id = str(request.path_params.get("strategy_id") or "")
        try:
            return ctx.service.get_strategy_spec(
                strategy_id,
                version_selector=ctx.query(request, "version", "current"),
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_list_memory(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            page_number = int(ctx.query(request, "page", "1") or 1)
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_number = 1
            page_size = 20
        try:
            return ctx.service.list_institutional_memory_entries(
                knowledge_type=ctx.query(request, "knowledge_type"),
                scope=ctx.query(request, "scope"),
                scope_filter=ctx.query(request, "scope_filter"),
                tags=ctx.query(request, "tags"),
                page=page_number,
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_get_memory(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        identifier = str(request.path_params.get("entry_id") or "")
        try:
            return ctx.service.get_institutional_memory_entry(identifier)
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_synthesis_conflict_logs(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
        if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict log view disabled",
                "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                precondition_failed="synthesis_conflict_log_feature_flag",
            )
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        try:
            return ctx.service.list_synthesis_conflict_logs(
                capital_pool_id=ctx.query(request, "capital_pool_id"),
                scope_ref=ctx.query(request, "scope_ref"),
                proposal_id=ctx.query(request, "proposal_id"),
                sponsor_persona_id=ctx.query(request, "sponsor_persona_id"),
                synthesis_method=ctx.query(request, "synthesis_method"),
                committee_ref=ctx.query(request, "committee_ref"),
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_synthesis_conflict_log(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
        if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict log view disabled",
                "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                precondition_failed="synthesis_conflict_log_feature_flag",
            )
        log_id = str(request.path_params.get("log_id") or "")
        try:
            return ctx.service.get_synthesis_conflict_log(log_id)
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_bff_search(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        limit_val = ctx.query(request, "limit")
        limit = int(limit_val) if limit_val is not None and limit_val.isdigit() else None
        try:
            return await ctx.service.search_knowledge(
                query=str(ctx.query(request, "q", "") or "").strip(),
                types_raw=ctx.query(request, "types"),
                page_size=page_size,
                limit=limit,
                page_token=ctx.query(request, "page_token"),
                identity=identity,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    auth = _authorization()

    endpoint_knowledge_workbench.__signature__ = _signature(auth)
    endpoint_create_note.__signature__ = _signature(_body_parameter(), auth)
    endpoint_list_notes.__signature__ = _signature(
        _signature_query("owner_ref"), _signature_query("attachment_type"), _signature_query("attachment_ref"),
        _signature_query("tags"), _signature_query("page_token"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_get_note.__signature__ = _signature(_path("note_id"), auth)
    endpoint_list_evidence.__signature__ = _signature(
        _signature_query("linked_entity_type"), _signature_query("linked_entity_ref"),
        _signature_query("link_type"), _signature_query("credibility_tier"),
        _signature_query("verified", annotation=Optional[bool]), _signature_query("page_token"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_get_evidence.__signature__ = _signature(_path("ref_id"), auth)
    endpoint_list_insights.__signature__ = _signature(
        _signature_query("status", annotation=str, default="active"), _signature_query("tag"),
        _signature_query("linked_entity_type"), _signature_query("linked_entity_ref"),
        _signature_query("recency", annotation=str, default="all"), _signature_query("confidence_min", annotation=Optional[float]),
        _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100),
        _signature_query("include_inactive", annotation=bool, default=False), auth,
    )
    endpoint_get_insight.__signature__ = _signature(_path("insight_id"), auth)
    endpoint_list_strategy_specs.__signature__ = _signature(
        _signature_query("lifecycle_state", annotation=str, default="all"), _signature_query("source_kind"),
        _signature_query("persona_id"), _signature_query("include_retired", annotation=bool, default=False),
        _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_strategy_versions.__signature__ = _signature(_path("strategy_id"), auth)
    endpoint_strategy_compare.__signature__ = _signature(
        _path("strategy_id"), _signature_query("left_version"), _signature_query("right_version"),
        _signature_query("base_version"), _signature_query("target_version"), auth,
    )
    endpoint_get_strategy_spec.__signature__ = _signature(_path("strategy_id"), _signature_query("version", annotation=str, default="current"), auth)
    endpoint_list_memory.__signature__ = _signature(
        _signature_query("knowledge_type"), _signature_query("scope"), _signature_query("scope_filter"),
        _signature_query("tags"), _signature_query("page", annotation=int, default=1, ge=1),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
    )
    endpoint_get_memory.__signature__ = _signature(_path("entry_id"), auth)
    endpoint_synthesis_conflict_logs.__signature__ = _signature(
        _signature_query("capital_pool_id"), _signature_query("scope_ref"), _signature_query("proposal_id"),
        _signature_query("sponsor_persona_id"), _signature_query("synthesis_method"), _signature_query("committee_ref"),
        _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
    )
    endpoint_synthesis_conflict_log.__signature__ = _signature(_path("log_id"), auth)
    endpoint_bff_search.__signature__ = _signature(
        _signature_query("q", annotation=str, default=""), _signature_query("types"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100),
        _signature_query("limit", annotation=Optional[int], default=None, ge=1, le=100),
        _signature_query("page_token"), auth,
    )

    router.add_api_route("/api/v1/workbench/knowledge", endpoint_knowledge_workbench, methods=["GET"], name="knowledge_workbench")
    router.add_api_route("/api/v1/knowledge/notes", endpoint_create_note, methods=["POST"], name="create_note", status_code=201)
    router.add_api_route("/api/v1/knowledge/notes", endpoint_list_notes, methods=["GET"], name="list_notes")
    router.add_api_route("/api/v1/knowledge/notes/{note_id}", endpoint_get_note, methods=["GET"], name="get_note")
    router.add_api_route("/api/v1/knowledge/evidence", endpoint_list_evidence, methods=["GET"], name="list_evidence")
    router.add_api_route("/api/v1/knowledge/evidence/{ref_id}", endpoint_get_evidence, methods=["GET"], name="get_evidence")
    router.add_api_route("/api/v1/knowledge/insights", endpoint_list_insights, methods=["GET"], name="list_insights")
    router.add_api_route("/api/v1/knowledge/insights/{insight_id}", endpoint_get_insight, methods=["GET"], name="get_insight")
    router.add_api_route("/api/v1/knowledge/strategy-specs", endpoint_list_strategy_specs, methods=["GET"], name="list_strategy_specs")
    router.add_api_route("/api/v1/knowledge/strategy-specs/{strategy_id}/versions", endpoint_strategy_versions, methods=["GET"], name="strategy_versions")
    router.add_api_route("/api/v1/knowledge/strategy-specs/{strategy_id}/compare", endpoint_strategy_compare, methods=["GET"], name="strategy_compare")
    router.add_api_route("/api/v1/knowledge/strategy-specs/{strategy_id}", endpoint_get_strategy_spec, methods=["GET"], name="get_strategy_spec")
    router.add_api_route("/api/v1/knowledge/memory", endpoint_list_memory, methods=["GET"], name="list_memory")
    router.add_api_route("/api/v1/knowledge/memory/{entry_id}", endpoint_get_memory, methods=["GET"], name="get_memory")
    router.add_api_route("/bff/synthesis/conflict-logs", endpoint_synthesis_conflict_logs, methods=["GET"], name="synthesis_conflict_logs")
    router.add_api_route("/bff/synthesis/conflict-logs/{log_id}", endpoint_synthesis_conflict_log, methods=["GET"], name="synthesis_conflict_log")
    router.add_api_route("/bff/search", endpoint_bff_search, methods=["GET"], name="bff_search")

    return router
