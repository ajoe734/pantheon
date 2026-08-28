"""Public Workshop SSE events/stream module (AG-BE-SW-004).

Per-workshop pub-sub used by the strategy-workshop stream route and by
every other Agora router that needs to publish a workshop-scoped SSE
event (Interaction command outbox drain, Research run progress). Moved
out of router.py so those callers import a public module instead of a
router-private helper (ACG-06-002).

_ws_publish() is called from sync route handlers; asyncio.Queue.put_nowait()
is thread-safe and requires no running event loop at the call site.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_WS_SSE_BUFFER_SIZE = 500  # max events per workshop kept for reconnect replay

# workshop_id -> deque[(event_id, event_dict)]
_workshop_sse_buffers: Dict[str, deque] = {}
# workshop_id -> list[asyncio.Queue]
_workshop_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}


def _ws_utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ws_event_id() -> str:
    return f"wsevt-{uuid.uuid4().hex[:16]}"


def _ws_sse_format(event: dict) -> str:
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _ws_get_buffer(workshop_id: str) -> deque:
    if workshop_id not in _workshop_sse_buffers:
        _workshop_sse_buffers[workshop_id] = deque(maxlen=_WS_SSE_BUFFER_SIZE)
    return _workshop_sse_buffers[workshop_id]


def _ws_get_subscribers(workshop_id: str) -> List[asyncio.Queue]:
    if workshop_id not in _workshop_sse_subscribers:
        _workshop_sse_subscribers[workshop_id] = []
    return _workshop_sse_subscribers[workshop_id]


def _ws_publish(
    workshop_id: str,
    event_type: str,
    data: dict,
    *,
    utc_now_fn: Optional[Callable[[], str]] = None,
    event_id: Optional[str] = None,
) -> str:
    """Publish an SSE event to the workshop buffer and all live subscribers.

    Safe to call from sync route handlers. Returns the new event_id.
    """
    event_id = event_id or _ws_event_id()
    if any(existing_id == event_id for existing_id, _ in _ws_get_buffer(workshop_id)):
        return event_id
    timestamp = utc_now_fn() if utc_now_fn else _ws_utc_now()
    event: Dict[str, Any] = {
        "id": event_id,
        "type": event_type,
        "timestamp": timestamp,
        "data": {"workshop_id": workshop_id, **data},
    }
    _ws_get_buffer(workshop_id).append((event_id, event))
    for q in list(_ws_get_subscribers(workshop_id)):
        try:
            q.put_nowait(event)
        except (asyncio.QueueFull, Exception):
            pass
    return event_id


def _ws_replay_after(
    workshop_id: str,
    last_event_id: str,
    store: Optional[Any] = None,
) -> List[dict]:
    """Return buffered events that came after *last_event_id*.

    Returns an empty list when last_event_id is not found (caller may treat
    this as a missed-event condition and reconnect from the top).
    """
    buf = _workshop_sse_buffers.get(workshop_id)
    found = False
    replayed: List[dict] = []
    if buf:
        for eid, evt in buf:
            if found:
                replayed.append(evt)
            elif eid == last_event_id:
                found = True
        if found:
            return replayed

    # Database fallback to ensure reconnect/readback does not lose durable cards/events
    if store and hasattr(store, "list_events"):
        try:
            db_events = store.list_events(workshop_id)
            idx = -1
            for i, ev in enumerate(db_events):
                if ev.get("event_id") == last_event_id:
                    idx = i
                    break
            if idx != -1:
                replayed = []
                for ev in db_events[idx + 1:]:
                    payload_refs = ev.get("payload_refs_json")
                    if isinstance(payload_refs, str) and payload_refs:
                        try:
                            payload_refs = json.loads(payload_refs)
                        except Exception:
                            pass
                    if isinstance(payload_refs, dict) and "event_type" in payload_refs:
                        # Reconstructed SSE event format from full DB event
                        evt = {
                            "id": ev["event_id"],
                            "type": ev["event_type"],
                            "timestamp": ev["created_at"],
                            "data": {
                                "workshop_id": workshop_id,
                                **payload_refs
                            }
                        }
                    else:
                        evt = {
                            "id": ev["event_id"],
                            "type": ev["event_type"],
                            "timestamp": ev["created_at"],
                            "data": {
                                "workshop_id": workshop_id,
                                "event_id": ev["event_id"],
                                "sequence_no": ev["sequence_no"],
                                "actor_type": ev["actor_type"],
                                "event_type": ev["event_type"],
                                "private_content_ref": ev.get("private_content_ref"),
                                "redacted_summary": ev.get("redacted_summary"),
                                "payload_refs_json": payload_refs,
                                "trace_id": ev.get("trace_id"),
                            }
                        }
                    replayed.append(evt)
                return replayed
        except Exception:
            pass
    return []
