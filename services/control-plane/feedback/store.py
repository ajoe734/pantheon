from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None
    return parsed


class QueryFilterValidationError(ValueError):
    pass


@dataclass(frozen=True)
class QueryFilters:
    strategy_id: str | None = None
    registry_id: str | None = None
    promotion_state: str | None = None
    event_type: str | None = None
    actor_id: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = 100


def build_query_filters(
    *,
    strategy_id: str | None = None,
    registry_id: str | None = None,
    promotion_state: str | None = None,
    event_type: str | None = None,
    actor_id: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 100,
) -> QueryFilters:
    created_after_dt = _parse_query_timestamp("created_after", created_after)
    created_before_dt = _parse_query_timestamp("created_before", created_before)
    if created_after_dt is not None and created_before_dt is not None and created_after_dt > created_before_dt:
        raise QueryFilterValidationError(
            "created_after must be less than or equal to created_before"
        )

    return QueryFilters(
        strategy_id=strategy_id,
        registry_id=registry_id,
        promotion_state=promotion_state,
        event_type=event_type,
        actor_id=actor_id,
        created_after=created_after_dt,
        created_before=created_before_dt,
        limit=limit,
    )


def _parse_query_timestamp(name: str, value: str | None) -> datetime | None:
    if value is None:
        return None

    parsed = parse_rfc3339(value)
    if parsed is None:
        raise QueryFilterValidationError(
            f"{name} must be a valid RFC3339 timestamp with timezone"
        )
    return parsed


class TraderFeedbackStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            existing = self.get(event.get("event_id"))
            if existing is not None:
                return False, existing

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            return True, event

    def get(self, event_id: str | None) -> dict[str, Any] | None:
        if not event_id:
            return None
        for event in self.iter_events():
            if event.get("event_id") == event_id:
                return event
        return None

    def list(self, filters: QueryFilters) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for event in self.iter_events():
            if not self._matches(event, filters):
                continue
            events.append(event)
            if len(events) >= filters.limit:
                break
        return events

    def iter_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError as exc:
                log.warning("Skipping malformed trader feedback event line %s: %s", line_number, exc)
        return events

    def _matches(self, event: dict[str, Any], filters: QueryFilters) -> bool:
        target = event.get("target", {})
        created_at = parse_rfc3339(event.get("created_at"))

        if filters.strategy_id and target.get("strategy_id") != filters.strategy_id:
            return False
        if filters.registry_id and target.get("registry_id") != filters.registry_id:
            return False
        if filters.promotion_state and target.get("promotion_state") != filters.promotion_state:
            return False
        if filters.event_type and event.get("event_type") != filters.event_type:
            return False
        if filters.actor_id and event.get("actor_id") != filters.actor_id:
            return False
        if filters.created_after and (created_at is None or created_at < filters.created_after):
            return False
        if filters.created_before and (created_at is None or created_at > filters.created_before):
            return False
        return True
