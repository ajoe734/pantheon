"""Agora dashboard router — agora.dashboard.v1.

Migration note: Routes below are currently in main.py (migration pending).

Routes already implemented in main.py:
  GET   /bff/agora/journal
  POST  /bff/agora/journal
  PATCH /bff/agora/journal/{entry_id}
  GET   /bff/agora/notes
  POST  /bff/agora/notes
  GET   /bff/agora/daily
  GET   /bff/agora/markets
  GET   /bff/agora/market-notes
  GET   /bff/agora/watchlist
  GET   /bff/agora/postmortems
  GET   /bff/agora/alerts/triage
  GET   /bff/agora/decision-journal
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_dashboard_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Dashboard router — placeholder until dashboard routes migrate from main.py."""
    return APIRouter(tags=["agora-dashboard"])
