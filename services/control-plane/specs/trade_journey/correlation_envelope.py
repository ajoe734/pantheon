"""Versioned correlation envelope creation and downstream propagation.

The signal/decision boundary is the only boundary allowed to mint a trade
``journey_id``.  Every downstream producer creates a new event identity while
retaining the stable journey, correlation, trace, tenant, and environment
identity of its causal predecessor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping
import uuid


SCHEMA_VERSION = "trade-journey-envelope/1"
ENVIRONMENTS = frozenset({"paper", "broker_sandbox", "canary", "live"})
STABLE_FIELDS = (
    "schema_version",
    "tenant_id",
    "environment",
    "research_journey_id",
    "strategy_lifecycle_id",
    "journey_id",
    "correlation_id",
    "trace_id",
)
REQUIRED_FIELDS = STABLE_FIELDS[:3] + STABLE_FIELDS[5:] + (
    "event_id",
    "causation_event_id",
    "producer",
    "event_time",
    "received_at",
    "producer_revision",
)


class CorrelationEnvelopeError(ValueError):
    """The envelope is incomplete, incompatible, or loses upstream identity."""


def mint_trade_envelope(
    upstream: Mapping[str, Any],
    *,
    producer: str,
    event_id: str | None = None,
    journey_id: str | None = None,
    now: str | None = None,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Mint a trade journey at the signal/decision boundary.

    Existing correlation and trace identifiers are retained.  If they are not
    supplied, deterministic callers may inject ``id_factory``; production uses
    UUID4 identifiers.
    """
    make_id = id_factory or (lambda: str(uuid.uuid4()))
    timestamp = now or _utc_now()
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": _required(upstream, "tenant_id"),
        "environment": _required(upstream, "environment"),
        "journey_id": journey_id or f"tj_{make_id()}",
        "correlation_id": str(upstream.get("correlation_id") or make_id()),
        "trace_id": str(upstream.get("trace_id") or make_id()),
        "event_id": event_id or str(make_id()),
        "causation_event_id": str(
            upstream.get("event_id") or upstream.get("causation_event_id") or make_id()
        ),
        "producer": _nonempty(producer, "producer"),
        "event_time": timestamp,
        "received_at": timestamp,
        "producer_revision": 1,
    }
    for field in ("research_journey_id", "strategy_lifecycle_id"):
        if upstream.get(field) not in (None, ""):
            envelope[field] = str(upstream[field])
    return validate_envelope(envelope)


def propagate_envelope(
    upstream: Mapping[str, Any],
    *,
    producer: str,
    event_id: str,
    event_time: str,
    received_at: str | None = None,
    producer_revision: int = 1,
) -> dict[str, Any]:
    """Create a downstream event envelope without replacing stable identity."""
    validated = validate_envelope(upstream)
    envelope = {field: validated[field] for field in STABLE_FIELDS if field in validated}
    envelope.update(
        event_id=_nonempty(event_id, "event_id"),
        causation_event_id=validated["event_id"],
        producer=_nonempty(producer, "producer"),
        event_time=_nonempty(event_time, "event_time"),
        received_at=received_at or event_time,
        producer_revision=producer_revision,
    )
    return validate_envelope(envelope)


def assert_no_field_loss(upstream: Mapping[str, Any], downstream: Mapping[str, Any]) -> None:
    """Reject a downstream envelope that drops or changes known stable fields."""
    before = validate_envelope(upstream)
    after = validate_envelope(downstream)
    for field in STABLE_FIELDS:
        if field in before and after.get(field) != before[field]:
            raise CorrelationEnvelopeError(f"downstream changed or dropped {field}")
    if after["causation_event_id"] != before["event_id"]:
        raise CorrelationEnvelopeError("causation_event_id must equal the upstream event_id")


def validate_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    envelope = dict(value)
    missing = [field for field in REQUIRED_FIELDS if envelope.get(field) in (None, "")]
    if missing:
        raise CorrelationEnvelopeError(f"missing required fields: {', '.join(missing)}")
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise CorrelationEnvelopeError(f"unsupported schema_version: {envelope['schema_version']}")
    if envelope["environment"] not in ENVIRONMENTS:
        raise CorrelationEnvelopeError(f"unsupported environment: {envelope['environment']}")
    if not isinstance(envelope["producer_revision"], int) or envelope["producer_revision"] < 1:
        raise CorrelationEnvelopeError("producer_revision must be a positive integer")
    return envelope


def _required(value: Mapping[str, Any], field: str) -> str:
    return _nonempty(value.get(field), field)


def _nonempty(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise CorrelationEnvelopeError(f"{field} is required")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
