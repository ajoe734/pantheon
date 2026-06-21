"""Agora research router — agora.research.v1.

Migration note: Routes below are currently in main.py (migration pending to here via AG-BE-SW-004).

Routes already implemented in main.py:
  GET /bff/agora/research-tasks
  GET /bff/research/tasks

AG-BE-SW-004: This module exposes publish_research_progress() so that future
POST /bff/agora/workshops/{id}/research-runs implementations can fan research
progress events into the workshop SSE channel without importing the workshop
router directly.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def publish_research_progress(
    workshop_id: str,
    run_id: str,
    progress_pct: float,
    message: str = "",
    *,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    """Publish a workshop.research.progress event to the workshop SSE stream.

    Imports _ws_publish lazily to avoid a circular import with the workshop router.
    Returns the SSE event_id, or an empty string if the workshop has no active stream.
    """
    from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)
    return _ws_publish(
        workshop_id,
        "workshop.research.progress",
        {
            "run_id": run_id,
            "progress_pct": progress_pct,
            "message": message,
        },
        utc_now_fn=utc_now_fn,
    )


def publish_openclaw_degraded(
    workshop_id: str,
    reason: str = "OPENCLAW_UPSTREAM_DEGRADED",
    *,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    """Publish a workshop.openclaw.degraded event when OpenClaw is unreachable.

    Callers should invoke this whenever they detect OpenClaw degradation while
    serving a request for a specific workshop.  The event carries the canonical
    error_code OPENCLAW_UPSTREAM_DEGRADED so the frontend can show a graceful
    degraded state.
    """
    from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)
    return _ws_publish(
        workshop_id,
        "workshop.openclaw.degraded",
        {
            "error_code": "OPENCLAW_UPSTREAM_DEGRADED",
            "reason": reason,
        },
        utc_now_fn=utc_now_fn,
    )


def create_research_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Research router — placeholder until AG-BE-SW-004 migrates routes from main.py."""
    return APIRouter(tags=["agora-research"])
