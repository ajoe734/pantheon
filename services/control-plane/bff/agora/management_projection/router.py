"""Agora management-projection router skeleton.

Provides operator management-plane projections of Agora state (operator-only read
surfaces that combine Agora evidence with management-plane context).

No dedicated §17 routes yet — this module is a placeholder for future
AG-BE-ID-004 ContextBundle redaction and central persona boundary work.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_management_projection_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Management-projection sub-router — placeholder for AG-BE-ID-004."""
    return APIRouter(tags=["agora-management"])
