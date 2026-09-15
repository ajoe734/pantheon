"""Agora research router — agora.research.v1.

Thin composition factory delegating to cohesive subrouters:
  - routes/candidates.py: Candidate pools, scoring, members, reviews, discussions, monitoring
  - routes/plans.py: Workshop research plans lifecycle, create, approve, cancel
  - routes/runs.py: Research run execution, dispatch, cancel, artifacts
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException

from .dispatcher import ResearchDispatcher
from .routes.candidates import build_candidates_router
from .routes.common import (
    AgoraResearchRouteContext,
    _available_field,
    _candidate_pool_etag,
    _default_registry_candidates,
    _load_default_scoring_recipe,
    _member_truth_projection,
    _plan_etag,
    _rank_scores,
    _score_band,
    _score_candidate,
    _unavailable_field,
    publish_openclaw_degraded,
    publish_research_progress,
)
from .routes.plans import build_plans_router
from .routes.runs import build_runs_router
from .store import make_research_plan_store

__all__ = [
    "create_research_router",
    "publish_research_progress",
    "publish_openclaw_degraded",
]


def create_research_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    require_write_role: Optional[Callable[..., None]] = None,
    research_plan_store: Any = None,
    workshop_store: Any = None,
    dataset_store: Any = None,
    adapter_registry: Optional[Any] = None,
) -> APIRouter:
    """Build and return the Agora research APIRouter with strict write role and tenant isolation."""
    store = research_plan_store if research_plan_store is not None else make_research_plan_store()
    _ACTIVE_RESEARCH_STORE = store
    if adapter_registry is None:
        try:
            from .dispatcher import build_authentic_adapter_registry
        except ImportError:
            from services.control_plane.bff.agora.research.dispatcher import build_authentic_adapter_registry
        adapter_mode = os.getenv("AGORA_RESEARCH_ADAPTER_MODE", "real").strip().lower()
        adapter_registry = build_authentic_adapter_registry(
            mode=adapter_mode,
            allow_missing_endpoints=True,
        )
    dispatcher = ResearchDispatcher(
        store=store,
        adapter_registry=adapter_registry,
        publish_progress_fn=publish_research_progress,
        utc_now=utc_now,
        dataset_store=dataset_store,
    )
    ctx = AgoraResearchRouteContext(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
        store=store,
        dispatcher=dispatcher,
        workshop_store=workshop_store,
        dataset_store=dataset_store,
    )
    router = APIRouter(tags=["agora-research"])
    router.routes.extend(build_candidates_router(ctx).routes)
    router.routes.extend(build_plans_router(ctx).routes)
    router.routes.extend(build_runs_router(ctx).routes)
    router.store = store
    router.dispatcher = dispatcher
    router.adapter_registry = adapter_registry
    return router
