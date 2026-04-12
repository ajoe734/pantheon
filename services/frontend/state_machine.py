"""Frontend state machine scaffold for APP-002-IMPL-FE

States: fresh, degraded, stale, partial, unavailable

This module provides a minimal, testable state machine abstraction for the operator UI.
Implementers should wire this into the actual frontend (JS/TS) or BFF adapters as needed.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class DataState(Enum):
    FRESH = "fresh"
    DEGRADED = "degraded"
    STALE = "stale"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass
class UIState:
    data_state: DataState
    payload: Optional[Dict[str, Any]] = None
    last_known_at: Optional[str] = None


def compute_button_gating(data_state: DataState, deployment_status: str) -> Dict[str, bool]:
    """Return button enabled/disabled map for [approve, reject] as a simple example.

    This is a minimal Python representation of the gating rules defined in
    support/sidecars/APP-002/APP-002-FRONTEND-STATE-MATRIX.md. Keep logic small and
    testable here; the real frontend should mirror these rules.
    """
    if data_state == DataState.FRESH:
        return {"approve": True, "reject": True}
    if data_state == DataState.DEGRADED:
        return {"approve": True, "reject": True}
    if data_state == DataState.STALE:
        return {"approve": False, "reject": True}
    if data_state == DataState.PARTIAL:
        return {"approve": True, "reject": True}
    return {"approve": False, "reject": False}
