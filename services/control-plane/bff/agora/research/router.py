"""Agora research router — agora.research.v1.

Thin composition factory delegating to cohesive subrouters:
  - routes/candidates.py: Candidate pools, scoring, members, reviews, discussions, monitoring
  - routes/plans.py: Workshop research plans lifecycle, create, approve, cancel
  - routes/runs.py: Research run execution, dispatch, cancel, artifacts
"""
from __future__ import annotations

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
) -> APIRouter:
    """Build and return the Agora research APIRouter with strict write role and tenant isolation."""
    store = research_plan_store if research_plan_store is not None else make_research_plan_store()
    dispatcher = ResearchDispatcher(
        store=store,
        publish_progress_fn=publish_research_progress,
        utc_now=utc_now,
    )
    ctx = AgoraResearchRouteContext(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
        store=store,
        dispatcher=dispatcher,
    )
    router = APIRouter(tags=["agora-research"])
    router.routes.extend(build_candidates_router(ctx).routes)
    router.routes.extend(build_plans_router(ctx).routes)
    router.routes.extend(build_runs_router(ctx).routes)
    return router
