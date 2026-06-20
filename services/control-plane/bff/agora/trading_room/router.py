"""Agora trading-room router — agora.trading.v1 (observation-only).

Safety invariant: NO route in this module may route live orders.
TradingEvent.no_order_route_proof must be present on all persisted events (SD §8).

Migration note: Routes below are currently in main.py (migration pending).

Routes already implemented in main.py:
  GET  /bff/agora/signals
  GET  /bff/agora/signals/{signalId}
  POST /bff/agora/signals
  POST /bff/agora/signals/{signalId}/feedback
  POST /bff/agora/feedback
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


def create_trading_room_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Trading-room router — placeholder; no live order routing is ever permitted."""
    return APIRouter(tags=["agora-trading"])
