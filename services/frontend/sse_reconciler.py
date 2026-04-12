"""SSE reconciliation helpers for APP-002 frontend.

Provides:
1. A client-side SSE subscription manager with automatic reconnection and
   ``last_event_id`` replay semantics.
2. Deterministic, idempotent state reconciliation that merges incoming SSE
   events into a local UI-state snapshot.

This module is written in Python so it can be exercised by unit tests.  In
production the equivalent logic would live in TypeScript / JavaScript using
the browser ``EventSource`` API.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Reconciliation engine
# ---------------------------------------------------------------------------

def reconcile_ui_state(
    current: Dict[str, Any],
    sse_event: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a single SSE event to *current* UI state and return the updated state.

    Reconciliation is **deterministic** and **idempotent**:
    - Events carry a monotonic ``id`` field; only events newer than
      ``current.get("last_event_id")`` are applied.
    - The ``payload`` is deep-merged into ``current["data"]``.
    - Unknown event types are recorded in ``current["_unhandled"]`` for diagnostics.
    """
    updated: Dict[str, Any] = dict(current)
    event_id = sse_event.get("id", "")
    existing_id = updated.get("last_event_id", "")

    # Idempotency gate — skip events we already applied
    if event_id and event_id == existing_id:
        return updated

    # Update cursor
    if event_id:
        updated["last_event_id"] = event_id
    if sse_event.get("timestamp"):
        updated["last_seen"] = sse_event["timestamp"]

    # Merge payload into data subtree
    payload = sse_event.get("data") or sse_event.get("payload")
    if payload:
        updated.setdefault("data", {}).update(payload)

    # Track event type for audit / debugging
    event_type = sse_event.get("type", "unknown")
    updated.setdefault("_event_log", []).append({
        "id": event_id,
        "type": event_type,
        "ts": sse_event.get("timestamp"),
    })

    return updated


def reconcile_event_sequence(
    initial_state: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Replay an ordered list of SSE events starting from *initial_state*.

    Returns the final reconciled UI state.  Used on initial connection to
    replay the full buffer received from the ``?last_event_id=`` replay path.
    """
    state = dict(initial_state)
    for evt in events:
        state = reconcile_ui_state(state, evt)
    return state


# ---------------------------------------------------------------------------
# Reconnection manager (client-side semantics)
# ---------------------------------------------------------------------------

class SSEReconnectManager:
    """Manages the reconnection lifecycle for a single SSE stream.

    Implements exponential back-off with jitter, tracks the last received
    event id for replay, and exposes connection-health signals.
    """

    # Default back-off parameters
    INITIAL_DELAY = 1.0      # seconds
    MAX_DELAY = 30.0         # seconds
    BACKOFF_FACTOR = 2.0     # multiplier per retry
    JITTER = 0.5             # ±50% random jitter

    def __init__(self, stream_url: str) -> None:
        self.stream_url = stream_url
        self.last_event_id: Optional[str] = None
        self._attempt = 0
        self._connected = False
        self._last_connect_ts: Optional[float] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def on_connect(self) -> None:
        """Call when the SSE connection is successfully established."""
        self._connected = True
        self._attempt = 0
        self._last_connect_ts = time.monotonic()

    def on_disconnect(self) -> float:
        """Call when the SSE connection drops.  Returns the delay before next attempt."""
        self._connected = False
        delay = min(
            self.INITIAL_DELAY * (self.BACKOFF_FACTOR ** self._attempt),
            self.MAX_DELAY,
        )
        # Add jitter: delay * (1 ± JITTER)
        import random
        jittered = delay * (1.0 + random.uniform(-self.JITTER, self.JITTER))
        self._attempt += 1
        return max(jittered, 0.1)

    def build_url(self) -> str:
        """Return the URL to use for the next connection attempt.

        If ``last_event_id`` is set, appends ``?last_event_id=`` for replay.
        """
        if self.last_event_id:
            sep = "&" if "?" in self.stream_url else "?"
            return f"{self.stream_url}{sep}last_event_id={self.last_event_id}"
        return self.stream_url

    def record_event_id(self, event_id: str) -> None:
        """Update the replay cursor."""
        self.last_event_id = event_id

    @property
    def uptime_seconds(self) -> Optional[float]:
        """Seconds since the last successful connect, or None if disconnected."""
        if not self._connected or self._last_connect_ts is None:
            return None
        return time.monotonic() - self._last_connect_ts


# ---------------------------------------------------------------------------
# Stream-specific reconcilers
# ---------------------------------------------------------------------------

def reconcile_runtime_event(
    state: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Reconcile a ``runtime_state_changed`` event into UI state."""
    updated = reconcile_ui_state(state, event)
    data = event.get("data", {})
    # Update the runtime binding status in a structured way
    runtime_id = data.get("runtime_id", "")
    if runtime_id:
        updated.setdefault("runtimes", {}).setdefault(runtime_id, {})
        updated["runtimes"][runtime_id]["state"] = data.get("current_state")
        updated["runtimes"][runtime_id]["previous_state"] = data.get("previous_state")
    return updated


def reconcile_incident_event(
    state: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Reconcile an ``incident_created`` / ``incident_updated`` event into UI state."""
    updated = reconcile_ui_state(state, event)
    data = event.get("data", {})
    incident_id = data.get("incident_id", "")
    if incident_id:
        updated.setdefault("incidents", {}).setdefault(incident_id, {})
        updated["incidents"][incident_id].update(data)
    return updated


def reconcile_kill_switch_event(
    state: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Reconcile a ``kill_switch_activated`` / ``kill_switch_deactivated`` event."""
    updated = reconcile_ui_state(state, event)
    data = event.get("data", {})
    updated.setdefault("kill_switch", {})
    updated["kill_switch"]["active"] = event.get("type") == "kill_switch_activated"
    updated["kill_switch"].update(data)
    return updated


# ---------------------------------------------------------------------------
# Event stream + reconciler (test harness + adapter bridge)
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_event_id(prefix: str = "evt") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class SseEvent:
    id: str
    type: str
    timestamp: str
    data: Dict[str, Any]

    def format_sse(self) -> str:
        return (
            f"id: {self.id}\n"
            f"event: {self.type}\n"
            f"data: {json.dumps(self.data, ensure_ascii=False)}\n\n"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": self.data,
        }


class EventStream:
    """Bounded event stream with replay semantics aligned to BFF SSE."""

    def __init__(self, max_events: int = 500):
        self._events: deque[SseEvent] = deque(maxlen=max_events)

    def append(self, event_type: str, data: Dict[str, Any]) -> SseEvent:
        event = SseEvent(
            id=_make_event_id(),
            type=event_type,
            timestamp=_utc_now(),
            data=data,
        )
        self._events.append(event)
        return event

    def replay_from(self, last_event_id: Optional[str]) -> List[SseEvent]:
        if not last_event_id:
            return list(self._events)
        found = False
        result: List[SseEvent] = []
        for evt in self._events:
            if found:
                result.append(evt)
            elif evt.id == last_event_id:
                found = True
        if not found:
            return list(self._events)
        return result

    def recent(self, n: int) -> List[SseEvent]:
        if n <= 0:
            return []
        return list(self._events)[-n:]


class SseReconciler:
    """Reconciles SSE events into a consistent UI state snapshot."""

    def __init__(self, max_log: int = 200):
        self.last_seen_event_id: Optional[str] = None
        self.state: Dict[str, Any] = {}
        self._event_log: List[Dict[str, Any]] = []
        self._max_log = max_log
        self._handlers: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], None]] = {
            "runtime_state_changed": _handle_runtime_state_changed,
            "incident_created": _handle_incident_created,
            "incident_updated": _handle_incident_updated,
            "kill_switch_activated": _handle_kill_switch_activated,
            "kill_switch_deactivated": _handle_kill_switch_deactivated,
        }
        self._streams: Dict[str, EventStream] = {
            "runtime": EventStream(),
            "incident": EventStream(),
            "kill_switch": EventStream(),
        }

    def get_stream(self, name: str) -> Optional[EventStream]:
        return self._streams.get(name)

    def emit_runtime_event(self, event_type: str, data: Dict[str, Any]) -> SseEvent:
        return self._emit("runtime", event_type, data)

    def emit_incident_event(self, event_type: str, data: Dict[str, Any]) -> SseEvent:
        return self._emit("incident", event_type, data)

    def emit_kill_switch_event(self, event_type: str, data: Dict[str, Any]) -> SseEvent:
        return self._emit("kill_switch", event_type, data)

    def _emit(self, stream_name: str, event_type: str, data: Dict[str, Any]) -> SseEvent:
        stream = self._streams[stream_name]
        event = stream.append(event_type, data)
        self.apply_event(event.to_dict())
        return event

    def apply_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event_id = event.get("id")
        if event_id and event_id == self.last_seen_event_id:
            return self.state

        event_type = event.get("type", "unknown")
        data = event.get("data", {})

        handler = self._handlers.get(event_type, _default_handler)
        handler(self.state, data)

        if event_id:
            self.last_seen_event_id = event_id

        self._event_log.append({
            "event_id": event_id,
            "type": event_type,
            "applied_at": time.time(),
        })
        if len(self._event_log) > self._max_log:
            self._event_log = self._event_log[-self._max_log:]

        return self.state

    def apply_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        for event in events:
            self.apply_event(event)
        return self.state

    @property
    def reconnect_params(self) -> Dict[str, str]:
        if self.last_seen_event_id:
            return {"last_event_id": self.last_seen_event_id}
        return {}


def _default_handler(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    _deep_merge(state, data)


def _handle_runtime_state_changed(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    runtime_id = data.get("runtime_id")
    if runtime_id:
        state.setdefault("runtimes", {})[runtime_id] = {
            **state.get("runtimes", {}).get(runtime_id, {}),
            **data,
        }


def _handle_incident_created(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    incident_id = data.get("incident_id")
    if incident_id:
        state.setdefault("incidents", {})[incident_id] = {
            **data,
            "status": data.get("status", "open"),
        }


def _handle_incident_updated(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    incident_id = data.get("incident_id")
    if incident_id and incident_id in state.get("incidents", {}):
        state["incidents"][incident_id].update(data)


def _handle_kill_switch_activated(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    state["kill_switch"] = {
        "active": True,
        **data,
    }


def _handle_kill_switch_deactivated(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    state["kill_switch"] = {
        "active": False,
        **data,
    }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# Compatibility alias for earlier test harnesses
SSEReconciler = SseReconciler
