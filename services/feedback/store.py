"""
Append-only in-memory feedback store.

Guarantees (per contract §7):
  - raw events are never mutated after write
  - stable event_id deduplication (idempotent writes)
  - query by strategy_id, registry_id, promotion_state, event_type, created_at prefix
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from .models import ExecutionTelemetryEvent, TraderFeedbackEvent

AnyEvent = Union[TraderFeedbackEvent, ExecutionTelemetryEvent]


class DuplicateEventError(Exception):
    pass


class FeedbackStore:
    """
    Thread-unsafe in-memory append-only store for feedback and telemetry events.
    For production use, replace or wrap with a durable backend that preserves
    the same append-only and idempotency guarantees.
    """

    def __init__(self) -> None:
        self._events: List[AnyEvent] = []
        self._index: Dict[str, AnyEvent] = {}

    def write(self, event: AnyEvent) -> None:
        """Append a deep-copied snapshot. Raises DuplicateEventError if event_id already exists."""
        if event.event_id in self._index:
            raise DuplicateEventError(
                f"Event with id '{event.event_id}' already exists."
            )
        snapshot = copy.deepcopy(event)
        self._events.append(snapshot)
        self._index[event.event_id] = snapshot

    def get(self, event_id: str) -> Optional[AnyEvent]:
        stored = self._index.get(event_id)
        return copy.deepcopy(stored) if stored is not None else None

    def query(
        self,
        *,
        strategy_id: Optional[str] = None,
        registry_id: Optional[str] = None,
        promotion_state: Optional[str] = None,
        event_type: Optional[str] = None,
        created_at_from: Optional[str] = None,
        created_at_to: Optional[str] = None,
        event_family: Optional[str] = None,
    ) -> List[AnyEvent]:
        """
        Filter stored events.  All filters are ANDed.

        event_family: 'feedback' | 'telemetry' | None (both)
        created_at_from / created_at_to: RFC3339 strings used as inclusive bounds.
        """
        results: List[AnyEvent] = []
        from_dt = _parse_dt(created_at_from) if created_at_from else None
        to_dt = _parse_dt(created_at_to) if created_at_to else None

        for ev in self._events:
            if event_family == "feedback" and not isinstance(
                ev, TraderFeedbackEvent
            ):
                continue
            if event_family == "telemetry" and not isinstance(
                ev, ExecutionTelemetryEvent
            ):
                continue
            if strategy_id and ev.target.strategy_id != strategy_id:
                continue
            if registry_id and ev.target.registry_id != registry_id:
                continue
            if promotion_state and (
                ev.target.promotion_state is None
                or ev.target.promotion_state.value != promotion_state
            ):
                continue
            if event_type and ev.event_type.value != event_type:
                continue
            ev_dt = _parse_dt(ev.created_at)
            if from_dt and ev_dt < from_dt:
                continue
            if to_dt and ev_dt > to_dt:
                continue
            results.append(copy.deepcopy(ev))

        return results

    def all(self) -> List[AnyEvent]:
        return copy.deepcopy(self._events)

    def __len__(self) -> int:
        return len(self._events)


def _parse_dt(value: str) -> datetime:
    """Parse RFC3339 string into timezone-aware datetime."""
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
