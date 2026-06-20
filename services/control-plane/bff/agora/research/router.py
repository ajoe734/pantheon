"""Agora research router — agora.research.v1.

Migration note: Routes below are currently in main.py (migration pending to here via AG-BE-SW-004).

Routes already implemented in main.py:
  GET /bff/agora/research-tasks
  GET /bff/research/tasks
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_research_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Research router — placeholder until AG-BE-SW-004 migrates routes from main.py."""
    return APIRouter(tags=["agora-research"])
