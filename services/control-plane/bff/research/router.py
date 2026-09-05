"""Composition router for research surfaces."""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter

from .routes import (
    ResearchRouteContext,
    build_analyses_router,
    build_artifacts_router,
    build_experiments_router,
    build_knowledge_router,
    build_ops_router,
    build_tickets_router,
    create_research_experiments_router,
)
from .routes.common import (
    ConflictLogGet,
    ConflictLogList,
    CrossEntitySearch,
    IdentityCapabilities,
    PageSlice,
    SnapshotMeta,
    SubmitAction,
    SurfaceStatus,
    _default_page_slice,
    _default_snapshot_meta,
    _default_surface_status,
)
from .service import ResearchRouterService

RESEARCH_ROUTE_INVENTORY = (
    ("GET", "/api/v1/workbench/knowledge"),
    ("GET", "/api/v1/operator/research/oss-activation-ready"),
    ("GET", "/api/v1/operator/research/oss-preactivation"),
    ("GET", "/api/v1/operator/source/ops"),
    ("GET", "/api/v1/operator/search/ops"),
    ("POST", "/api/v1/operator/source/dlq/replay"),
    ("POST", "/api/v1/operator/source/frontier/{frontier_id}/replay"),
    ("POST", "/api/v1/operator/search/index/refresh"),
    ("POST", "/api/v1/operator/search/index/materialize"),
    ("POST", "/api/v1/research/tickets"),
    ("GET", "/api/v1/research/tickets"),
    ("GET", "/api/v1/research/tickets/{ticket_id}"),
    ("PATCH", "/api/v1/research/tickets/{ticket_id}"),
    ("GET", "/api/v1/research/search"),
    ("GET", "/api/v1/research/source-connectors"),
    ("GET", "/api/v1/research/source-change-proposals"),
    ("GET", "/api/v1/research/analysis"),
    ("GET", "/api/v1/research/analysis/{analysis_id}"),
    ("POST", "/api/v1/experiments/launch"),
    ("GET", "/api/v1/experiments"),
    ("GET", "/api/v1/experiments/{experiment_id}"),
    ("POST", "/api/v1/experiments/{experiment_id}/cancel"),
    ("GET", "/api/v1/artifacts"),
    ("GET", "/api/v1/artifacts/compare"),
    ("GET", "/api/v1/artifacts/{artifact_id}"),
    ("POST", "/api/v1/knowledge/notes"),
    ("GET", "/api/v1/knowledge/notes"),
    ("GET", "/api/v1/knowledge/notes/{note_id}"),
    ("GET", "/api/v1/knowledge/evidence"),
    ("GET", "/api/v1/knowledge/evidence/{ref_id}"),
    ("GET", "/api/v1/knowledge/insights"),
    ("GET", "/api/v1/knowledge/insights/{insight_id}"),
    ("GET", "/api/v1/knowledge/strategy-specs"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}/versions"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}/compare"),
    ("GET", "/api/v1/knowledge/memory"),
    ("GET", "/api/v1/knowledge/memory/{entry_id}"),
    ("GET", "/bff/synthesis/conflict-logs"),
    ("GET", "/bff/synthesis/conflict-logs/{log_id}"),
    ("GET", "/bff/search"),
    ("GET", "/bff/artifacts"),
    ("GET", "/bff/artifacts/{artifact_id}"),
    ("GET", "/bff/research-analyses"),
    ("GET", "/bff/research-analyses/{analysis_id}"),
    ("PATCH", "/bff/artifacts/{artifact_id}"),
    ("POST", "/bff/artifacts"),
)


def create_research_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: PageSlice = _default_page_slice,
    snapshot_meta: SnapshotMeta = _default_snapshot_meta,
    dataset_surface_status: SurfaceStatus = _default_surface_status,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    submit_experiment_action: Optional[SubmitAction] = None,
    include_prepared_subrouters: bool = True,
    build_knowledge_workbench: Optional[Callable[[], Any]] = None,
    build_research_oss_readiness: Optional[Callable[..., Any]] = None,
    submit_source_search_command: Optional[Callable[..., Any]] = None,
    get_capabilities: Optional[IdentityCapabilities] = None,
    cross_entity_search: Optional[CrossEntitySearch] = None,
    list_synthesis_conflict_logs: Optional[ConflictLogList] = None,
    get_synthesis_conflict_log: Optional[ConflictLogGet] = None,
    service: Optional[ResearchRouterService] = None,
) -> APIRouter:
    """Compose and return the prepared Research API router."""
    if read_surface is not None:
        get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
    elif get_read_store is None:
        raise RuntimeError("Neither read_surface nor get_read_store was configured.")

    ctx = ResearchRouteContext(
        get_read_store=get_read_store,
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now=utc_now,
        page_slice=page_slice,
        snapshot_meta=snapshot_meta,
        dataset_surface_status=dataset_surface_status,
        require_operator_role=require_operator_role,
        submit_experiment_action=submit_experiment_action,
        build_knowledge_workbench=build_knowledge_workbench,
        build_research_oss_readiness=build_research_oss_readiness,
        submit_source_search_command=submit_source_search_command,
        get_capabilities=get_capabilities,
        cross_entity_search=cross_entity_search,
        list_synthesis_conflict_logs=list_synthesis_conflict_logs,
        get_synthesis_conflict_log=get_synthesis_conflict_log,
        service=service,
    )

    router = APIRouter()
    subrouters = [
        build_analyses_router(ctx),
        build_artifacts_router(ctx),
        build_experiments_router(ctx),
        build_ops_router(ctx),
        build_tickets_router(ctx),
        build_knowledge_router(ctx),
    ]
    for sub in subrouters:
        router.routes.extend(sub.routes)

    if include_prepared_subrouters:
        from ..console_gap.knowledge import create_knowledge_router

        router.routes.extend(
            create_knowledge_router(
                extract_identity=extract_identity,
                require_read_role=require_read_role,
                read_store_getter=get_read_store,
                utc_now=utc_now,
                dataset_surface_status=dataset_surface_status,
            ).routes
        )
        if require_operator_role is not None:
            router.routes.extend(
                create_research_experiments_router(
                    get_read_store=get_read_store,
                    extract_identity=extract_identity,
                    require_read_role=require_read_role,
                    require_operator_role=require_operator_role,
                    bff_error=bff_error,
                    utc_now=utc_now,
                    page_slice=page_slice,
                    snapshot_meta=snapshot_meta,
                    dataset_surface_status=dataset_surface_status,
                    submit_experiment_action=submit_experiment_action,
                ).routes
            )

    return router


__all__ = [
    "ConflictLogGet",
    "ConflictLogList",
    "CrossEntitySearch",
    "IdentityCapabilities",
    "PageSlice",
    "RESEARCH_ROUTE_INVENTORY",
    "ResearchRouteContext",
    "SnapshotMeta",
    "SubmitAction",
    "SurfaceStatus",
    "create_research_experiments_router",
    "create_research_router",
]
