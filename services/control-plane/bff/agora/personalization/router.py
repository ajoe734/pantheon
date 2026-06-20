"""Agora personalization router — agora.personalization.v1.

Migration note: Routes below are currently in main.py (migration pending).

Routes already implemented in main.py:
  GET  /bff/agora/memory
  POST /bff/agora/memory/{memoryId}/actions/{actionId}
  POST /bff/memory/{memoryId}/actions/quarantine
  GET  /bff/agora/insights
  POST /bff/agora/insights
  POST /bff/agora/insights/{insightId}/actions/{actionId}
  POST /bff/insights/{insightId}/actions/attach-strategy
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_personalization_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Personalization router — placeholder until personalization routes migrate from main.py."""
    return APIRouter(tags=["agora-personalization"])
