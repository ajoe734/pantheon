"""Agora strategy-workshop router — agora.workshop.v1.

Migration note: The routes listed below are currently implemented in main.py.
This module is the intended future home once AG-BE-SW-* tasks migrate them here.

Routes already implemented in main.py (migration pending):
  GET  /bff/agora/training-examples
  POST /bff/agora/training-examples
  GET  /bff/agora/evaluation-runs
  GET  /bff/agora/evaluation-suites
  GET  /bff/agora/skill-coaching/sessions
  GET  /bff/agora/committee-sessions           (core alias)
  GET  /bff/agora/committee/sessions
  POST /bff/agora/committee/sessions
  GET  /bff/agora/committee/sessions/{sessionId}
  POST /bff/agora/committee/sessions/{sessionId}/open
  POST /bff/agora/committee/sessions/{sessionId}/close
  GET  /bff/agora/committee/sessions/{sessionId}/memos
  GET  /bff/agora/committee/sessions/{sessionId}/memos/{memoId}
  POST /bff/agora/committee/sessions/{sessionId}/memos
  POST /bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish
  POST /bff/agora/committee/{sessionId}/evidence-pack
  POST /bff/agora/committee/{sessionId}/evidence-pack/files
  GET  /bff/agora/persona-lab/runs
  POST /bff/agora/persona-lab/{draftId}/actions/submit-commit
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_strategy_workshop_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Workshop router — placeholder until AG-BE-SW-* migrate routes from main.py."""
    return APIRouter(tags=["agora-workshop"])
