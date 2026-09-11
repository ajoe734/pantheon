"""Owner-scoped Trade Journey reads for the Agora performance surfaces.

The performance service consumes the configured Postgres projection reader
(``page_journeys`` + ``page_timeline``); there is no performance-owned event
store or materializer.  ``page_journeys`` rows carry snapshot identifiers but
an empty timeline, and ``get_journey`` only keeps the latest event per stage,
so owner visibility and trade/telemetry aggregation read the full durable
stage history through ``page_timeline``.  Reads are bounded and every
truncation or upstream failure is typed instead of being reported as an
empty or complete result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional

from services.trade_journey.materializer import JourneyProjection

from ...trade_journey_projection_store import (
    MAX_PAGE_SIZE,
    ProjectionReadError,
)

_USER_SCOPE_FIELDS = ("owner_user_id", "agora_user_id", "user_id")
MAX_JOURNEY_SCAN = 1000
MAX_TIMELINE_EVENTS = 2000

JourneyScanStatus = Literal["available", "partial", "unavailable"]


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def period_start(period: str, *, now: datetime) -> Optional[datetime]:
    if period in {"all", "latest"}:
        return None
    return now - timedelta(days=7 if period == "7d" else 30)


def timestamp_in_period(value: Any, *, period: str, now: datetime) -> bool:
    start = period_start(period, now=now)
    if start is None:
        return True
    parsed = parse_timestamp(value)
    if parsed is None:
        return False
    return parsed >= start


def _event_user_ids(event: Mapping[str, Any]) -> set[str]:
    return {
        str(event.get(field) or "").strip()
        for field in _USER_SCOPE_FIELDS
        if str(event.get(field) or "").strip()
    }


def projection_visible_to_user(projection: Any, user_id: str) -> bool:
    scoped_values: set[str] = set()
    for event in getattr(projection, "timeline", []) or []:
        scoped_values.update(_event_user_ids(event))
    return scoped_values == {user_id}


def projection_strategy_id(projection: Any) -> str:
    identifiers = (getattr(projection, "snapshot", {}) or {}).get("identifiers") or {}
    values = identifiers.get("strategy_id") or []
    return str(values[0]) if len(values) == 1 else ""


@dataclass
class JourneyScan:
    """Result of one bounded, owner-scoped projection reader walk.

    ``scanned``/``scope_total`` count tenant-wide rows (other owners included)
    and are internal diagnostics only; they must never reach an owner-facing
    response.
    """

    status: JourneyScanStatus
    reason: Optional[str] = None
    projections: List[Any] = field(default_factory=list)
    scanned: int = 0
    scope_total: Optional[int] = None
    controller: Dict[str, Any] = field(default_factory=dict)

    @property
    def reader_available(self) -> bool:
        return self.status != "unavailable"


class _TimelineTruncated(Exception):
    """A journey's durable history exceeded the bounded timeline read."""


def _full_timeline(
    reader: Any,
    *,
    tenant_id: str,
    environment: str,
    journey_id: str,
    max_events: int,
) -> List[Dict[str, Any]]:
    """Read a journey's complete stage history through ``page_timeline``.

    Every durable event is needed: owner scoping must see a conflicting owner
    on an early event, and trade counts must see every fill in a stage.
    """
    events: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        page = reader.page_timeline(
            tenant_id=tenant_id,
            environment=environment,
            journey_id=journey_id,
            page_size=max(1, min(MAX_PAGE_SIZE, max_events - len(events))),
            page_token=page_token,
        )
        events.extend(dict(item) for item in page.items)
        page_token = page.next_page_token
        if not page_token:
            return events
        if len(events) >= max_events:
            raise _TimelineTruncated(journey_id)


def scan_owner_journeys(
    reader: Any,
    *,
    tenant_id: str,
    environment: str,
    owner_user_id: str,
    period: str,
    now: datetime,
    strategy_id: Optional[str] = None,
    max_journeys: Optional[int] = None,
    max_timeline_events: Optional[int] = None,
) -> JourneyScan:
    """Walk the tenant/environment journey pages and keep the owner's projections.

    Owner visibility and aggregation are decided from the full durable stage
    history, so each candidate row costs a bounded ``page_timeline`` walk;
    candidates are pre-filtered on the page snapshot (strategy, period) first.
    A journey whose history exceeds ``max_timeline_events`` cannot be scoped
    safely and is dropped; that and stopping at ``max_journeys`` are reported
    as ``partial`` rather than silently truncating.
    """
    if reader is None:
        return JourneyScan(status="unavailable", reason="projection_reader_not_configured")
    max_journeys = MAX_JOURNEY_SCAN if max_journeys is None else max_journeys
    max_timeline_events = (
        MAX_TIMELINE_EVENTS if max_timeline_events is None else max_timeline_events
    )

    filters: Dict[str, Any] = {}
    clean_strategy = str(strategy_id or "").strip()
    if clean_strategy:
        filters["strategy_id"] = clean_strategy
    start = period_start(period, now=now)
    if start is not None:
        filters["date_from"] = start.isoformat().replace("+00:00", "Z")

    projections: List[Any] = []
    scanned = 0
    scope_total: Optional[int] = None
    page_token: Optional[str] = None
    truncation_reasons: List[str] = []
    try:
        while True:
            page = reader.page_journeys(
                tenant_id=tenant_id,
                environment=environment,
                filters=filters,
                sort="updated_at_desc",
                page_size=max(1, min(MAX_PAGE_SIZE, max_journeys - scanned)),
                page_token=page_token,
            )
            scope_total = int(page.total)
            for item in page.items:
                scanned += 1
                if clean_strategy and projection_strategy_id(item) != clean_strategy:
                    continue
                if not timestamp_in_period(
                    (item.snapshot or {}).get("updated_at"), period=period, now=now
                ):
                    continue
                try:
                    timeline = _full_timeline(
                        reader,
                        tenant_id=tenant_id,
                        environment=environment,
                        journey_id=item.journey_id,
                        max_events=max_timeline_events,
                    )
                except _TimelineTruncated:
                    if "journey_timeline_truncated" not in truncation_reasons:
                        truncation_reasons.append("journey_timeline_truncated")
                    continue
                projection = JourneyProjection(
                    item.journey_id,
                    item.tenant_id,
                    item.environment,
                    timeline,
                    dict(item.snapshot or {}),
                    list(item.graph_edges or []),
                    list(item.diagnostics or []),
                )
                if not projection_visible_to_user(projection, owner_user_id):
                    continue
                projections.append(projection)
            page_token = page.next_page_token
            if not page_token:
                break
            if scanned >= max_journeys:
                truncation_reasons.append("journey_scan_truncated")
                break
    except (ProjectionReadError, ValueError) as exc:
        return JourneyScan(
            status="unavailable",
            reason=f"projection_reader_unavailable:{exc}",
            scanned=scanned,
        )
    except Exception as exc:  # noqa: BLE001 - reads are fail-closed truth
        return JourneyScan(
            status="unavailable",
            reason=f"projection_reader_error:{type(exc).__name__}",
            scanned=scanned,
        )

    return JourneyScan(
        status="partial" if truncation_reasons else "available",
        reason=",".join(truncation_reasons) or None,
        projections=projections,
        scanned=scanned,
        scope_total=scope_total,
        controller=_controller_freshness(reader, tenant_id=tenant_id, environment=environment),
    )


def _controller_freshness(reader: Any, *, tenant_id: str, environment: str) -> Dict[str, Any]:
    """Projector controller row for freshness metadata; never fails the read."""
    freshness = getattr(reader, "controller_freshness", None)
    if not callable(freshness):
        return {}
    try:
        controller = freshness(tenant_id=tenant_id, environment=environment)
    except Exception:  # noqa: BLE001 - freshness is metadata, journeys are the truth
        return {}
    return dict(controller) if isinstance(controller, Mapping) else {}
