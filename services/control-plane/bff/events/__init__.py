"""BFF Events domain package.

Provides canonical Events list and authenticated/liveness SSE streaming routes.
"""
from __future__ import annotations

from .router import create_events_router

__all__ = [
    "create_events_router",
]
