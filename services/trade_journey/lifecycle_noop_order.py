"""Shared definition of a no-op paper order lifecycle event."""

from __future__ import annotations

from typing import Any, Mapping


SELL_WITHOUT_HOLDINGS_REASON = "sell_without_holdings"


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _has_no_fill_or_broker_submission(
    event: Mapping[str, Any], metadata: Mapping[str, Any]
) -> bool:
    if _first(event.get("fill_id"), metadata.get("fill_id")) is not None:
        return False
    metrics = event.get("metrics") if isinstance(event.get("metrics"), Mapping) else {}
    if metrics.get("submitted_to_broker") is True or metadata.get("submitted_to_broker") is True:
        return False
    if event.get("submitted_to_broker") is True:
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


def is_sell_without_holdings_rejection(
    event: Mapping[str, Any], metadata: Mapping[str, Any]
) -> bool:
    """True for an ``order_rejection`` caused by ``sell_without_holdings``."""
    if event.get("event_type") != "order_rejection":
        return False
    reason = _first(
        metadata.get("reason"),
        event.get("reason"),
        metadata.get("reject_reason"),
        event.get("broker_submission_status"),
        metadata.get("broker_submission_status"),
    )
    if str(reason or "").strip() != SELL_WITHOUT_HOLDINGS_REASON:
        return False
    return _has_no_fill_or_broker_submission(event, metadata)


def is_noop_order(event: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    """True for a ``paper_order_simulated`` or ``sell_without_holdings`` event with no fill or submission."""
    if is_sell_without_holdings_rejection(event, metadata):
        return True
    if event.get("event_type") != "paper_order_simulated":
        return False
    return _has_no_fill_or_broker_submission(event, metadata)
