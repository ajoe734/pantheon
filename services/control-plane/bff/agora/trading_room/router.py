"""Agora trading-room router — agora.trading.v1 (observation and decision-support only).

Thin composition factory delegating to cohesive subrouters:
  - routes/workspaces.py: Workspace aggregations, proposals, layout mutation, views, widgets, rollbacks
  - routes/decisions.py: Trading decision events, recording, and SSE stream
  - routes/intents.py: Governed trading intents and handoffs
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException

from .routes import (
    TradingRoomRouteContext,
    build_workspaces_router,
    build_decisions_router,
    build_intents_router,
    TradingDecisionEvent,
    TradingRoomAggregate,
    QueueSummary,
    RiskSummary,
    TradingRoomStrategy,
    PendingEventCounts,
    ConfidenceAssessment,
    ProbabilityForecast,
    ExpectedValue,
    RationaleItem,
    RiskNote,
    InvalidationState,
    EvidenceRef,
    TraderDecisionRequest,
    GovernedIntentHandoffRequest,
    _tr_publish,
    _trading_room_sse_buffers,
    _trading_room_sse_subscribers,
    _get_store,
)
from .store import TradingRoomStore

__all__ = [
    "create_trading_room_router",
    "TradingDecisionEvent",
    "TradingRoomAggregate",
    "QueueSummary",
    "RiskSummary",
    "TradingRoomStrategy",
    "PendingEventCounts",
    "ConfidenceAssessment",
    "ProbabilityForecast",
    "ExpectedValue",
    "RationaleItem",
    "RiskNote",
    "InvalidationState",
    "EvidenceRef",
    "TraderDecisionRequest",
    "GovernedIntentHandoffRequest",
    "_tr_publish",
    "_trading_room_sse_buffers",
    "_trading_room_sse_subscribers",
    "_get_store",
]


def create_trading_room_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Optional[Callable[..., None]] = None,
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    trading_room_store: Optional[TradingRoomStore] = None,
    workshop_store: Optional[Any] = None,
) -> APIRouter:
    """Trading-room router — agora.trading.v1.

    No live order routing is ever permitted (D1 boundary).
    All routes are operator-scoped (user-private read predicate enforced).
    """
    store = trading_room_store if trading_room_store is not None else _get_store()
    ctx = TradingRoomRouteContext(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
        store=store,
        workshop_store=workshop_store,
    )
    router = APIRouter(tags=["agora-trading"])
    router.routes.extend(build_workspaces_router(ctx).routes)
    router.routes.extend(build_decisions_router(ctx).routes)
    router.routes.extend(build_intents_router(ctx).routes)
    return router
