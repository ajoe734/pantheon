"""Agora shadow router skeleton — shadow decisions (agora.trading.v1).

ShadowDecision.no_order_route_proof must be 'shadow_learn_only' on all records
(SD §8 safety_notes).  This surface feeds Learn loops only; it never routes live.

No dedicated §17 routes yet — shadow_decision records are accessed via trading
signals and feedback surfaces.  This module is a placeholder for AG-BE-* shadow tasks.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_shadow_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Shadow sub-router — no routes yet; placeholder for AG-BE-* shadow tasks."""
    return APIRouter(tags=["agora-trading"])
