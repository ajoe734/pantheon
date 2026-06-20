"""Agora identity router — agora.identity.v1 + agora.session.v1.

Migration note: The routes listed below are currently implemented in main.py
(registered before this package router).  This module is the intended future
home for those handlers once downstream AG-BE-ID-* tasks migrate them here.

Routes already implemented in main.py (owned by this capability; migration pending):
  GET  /bff/agora/sessions
  POST /bff/agora/sessions
  GET  /bff/agora/sessions/{sessionId}
  GET  /bff/agora/sessions/{sessionId}/messages
  POST /bff/agora/sessions/{sessionId}/messages
  POST /bff/agora/ask
  GET  /bff/agora/ask/sessions
  POST /bff/agora/ask/sessions
  GET  /bff/agora/ask/sessions/{sessionId}
  POST /bff/agora/ask/sessions/{sessionId}/close
  GET  /bff/agora/inbox
  GET  /bff/agora/incoming
  GET  /bff/agora/handoffs
  POST /bff/agora/messages/{messageId}/actions/{actionId}

This module does NOT re-register these routes; main.py routes take precedence
until migration is complete (FastAPI uses first-registered route for a given path).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_identity_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Identity router — placeholder until AG-BE-ID-* migrate routes from main.py."""
    return APIRouter(tags=["agora-identity"])
