"""Shared definition of a no-op paper order lifecycle event."""

from __future__ import annotations

from typing import Any, Mapping


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def is_noop_order(event: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    """True for a ``paper_order_simulated`` event with no fill, broker submission or quantity."""
    if event.get("event_type") != "paper_order_simulated":
        return False
    if _first(event.get("fill_id"), metadata.get("fill_id")) is not None:
        return False
    metrics = event.get("metrics") if isinstance(event.get("metrics"), Mapping) else {}
    if metrics.get("submitted_to_broker") is True or metadata.get("submitted_to_broker") is True:
        return False
    quantity = _first(
        metrics.get("fill_quantity"),
        event.get("quantity"),
        metadata.get("fill_quantity"),
    )
    if quantity is not None:
        try:
            if float(quantity) != 0.0:
                return False
        except (TypeError, ValueError):
            return False
    return True
