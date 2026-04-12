"""BFF-to-frontend SSE adapter for APP-002.

Translates BFF SSE payloads into UI-friendly event shapes and provides
subscription / publish helpers for the frontend reconciliation layer.

In production this module's logic would be mirrored by a TypeScript adapter
in the front-end repo that wires ``EventSource`` instances to the
``sse_reconciler`` state machine.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Event transformation
# ---------------------------------------------------------------------------

def transform_bff_event(bff_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize BFF SSE payloads into ``{id, type, timestamp, data}`` shape.

    The BFF emits events in the shape defined by BFF_API_CONTRACT.md §11.3:

    .. code-block:: json

        {
          "id": "evt-20260410T030000Z-abc123",
          "type": "runtime_state_changed",
          "timestamp": "2026-04-10T03:00:00Z",
          "data": { "runtime_id": "r-001", "current_state": "canary", ... }
        }

    This function is a pass-through when the payload already conforms, but
    gracefully handles legacy ``{ts, event, payload}`` shapes.
    """
    # Canonical shape — return as-is
    if "id" in bff_payload and "type" in bff_payload:
        return {
            "id": bff_payload["id"],
            "type": bff_payload["type"],
            "timestamp": bff_payload.get("timestamp") or bff_payload.get("ts"),
            "data": bff_payload.get("data") or bff_payload.get("payload") or {},
        }

    # Legacy scaffold shape: {ts, event, payload}
    return {
        "id": bff_payload.get("id", ""),
        "type": bff_payload.get("type") or bff_payload.get("event") or "unknown",
        "timestamp": bff_payload.get("ts") or bff_payload.get("timestamp"),
        "data": bff_payload.get("data") or bff_payload.get("payload") or {},
    }


# ---------------------------------------------------------------------------
# Stream URL builders
# ---------------------------------------------------------------------------

_BFF_BASE = "/api/v1"


def runtime_sse_url(runtime_id: str, last_event_id: Optional[str] = None) -> str:
    """Return the SSE stream URL for a specific runtime."""
    url = f"{_BFF_BASE}/runtime/{runtime_id}/events/stream"
    if last_event_id:
        url += f"?last_event_id={last_event_id}"
    return url


def incidents_sse_url(last_event_id: Optional[str] = None) -> str:
    """Return the SSE stream URL for incident events."""
    url = f"{_BFF_BASE}/incidents/stream"
    if last_event_id:
        url += f"?last_event_id={last_event_id}"
    return url


def kill_switch_sse_url(last_event_id: Optional[str] = None) -> str:
    """Return the SSE stream URL for kill-switch updates."""
    url = f"{_BFF_BASE}/kill-switch/updates"
    if last_event_id:
        url += f"?last_event_id={last_event_id}"
    return url


# ---------------------------------------------------------------------------
# In-process publish / subscribe (for testing and Python-side consumers)
# ---------------------------------------------------------------------------

_subscribers: List[Callable[[Dict[str, Any]], None]] = []


def subscribe(callback: Callable[[Dict[str, Any]], None]) -> None:
    """Register a callback to receive transformed SSE events."""
    _subscribers.append(callback)


def unsubscribe(callback: Callable[[Dict[str, Any]], None]) -> None:
    """Remove a previously registered callback."""
    if callback in _subscribers:
        _subscribers.remove(callback)


def publish_raw(bff_payload: Dict[str, Any]) -> None:
    """Transform a raw BFF payload and dispatch to all subscribers."""
    event = transform_bff_event(bff_payload)
    for cb in list(_subscribers):
        try:
            cb(event)
        except Exception:
            # Subscriber errors must not break the publisher
            pass


# ---------------------------------------------------------------------------
# Expected event types (from BFF_API_CONTRACT.md §11.3)
# ---------------------------------------------------------------------------

SSE_EVENT_TYPES = {
    "runtime_state_changed",
    "incident_created",
    "incident_updated",
    "kill_switch_activated",
    "kill_switch_deactivated",
}
