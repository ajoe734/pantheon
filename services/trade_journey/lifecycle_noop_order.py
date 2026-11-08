"""Shared definition of a no-op paper order lifecycle event."""

from __future__ import annotations

import math
from typing import Any, Mapping


SELL_WITHOUT_HOLDINGS_REASON = "sell_without_holdings"
# Reasons the paper order risk gate can reject an order for. Each rejection is
# a terminal non-fill outcome, never an unreconciled order.
PAPER_GATE_REJECTION_REASONS = frozenset(
    {
        SELL_WITHOUT_HOLDINGS_REASON,
        "leverage_limit",
        "gross_limit",
        "net_limit",
        "asset_class_not_allowed",
        "insufficient_initial_margin",
        "price_stale",
        "instrument_not_in_candidates",
    }
)
PAPER_FUTURES_TERMINAL_NONFILL_EVENT_TYPES = frozenset(
    {
        "paper_futures_settled",
        "paper_margin_call",
        "paper_forced_liquidation",
    }
)


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
    for flag in (
        metrics.get("submitted_to_broker"),
        metadata.get("submitted_to_broker"),
        event.get("submitted_to_broker"),
    ):
        if flag is not None and flag is not False:
            return False
    for qty_field in ("fill_quantity", "filled_quantity", "quantity"):
        for container in (metrics, event, metadata):
            raw_qty = container.get(qty_field)
            if raw_qty is None:
                continue
            if isinstance(raw_qty, bool):
                return False
            try:
                parsed_qty = float(raw_qty)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(parsed_qty) or parsed_qty != 0.0:
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


def is_paper_futures_terminal_nonfill(
    event: Mapping[str, Any], metadata: Mapping[str, Any] | None = None
) -> bool:
    """True for a paper futures lifecycle terminal non-fill event."""
    if metadata is None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    if event.get("event_type") not in PAPER_FUTURES_TERMINAL_NONFILL_EVENT_TYPES:
        return False
    metrics = event.get("metrics") if isinstance(event.get("metrics"), Mapping) else {}
    for field in (
        "broker_order_id",
        "fill_id",
        "broker_trade_id",
        "ledger_entry_id",
        "reconciliation_id",
    ):
        if _first(event.get(field), metadata.get(field), metrics.get(field)) is not None:
            return False
    return _has_no_fill_or_broker_submission(event, metadata)


def is_noop_order(
    event: Mapping[str, Any], metadata: Mapping[str, Any] | None = None
) -> bool:
    """True for a ``paper_order_simulated``, ``sell_without_holdings``, or paper futures terminal non-fill event with no fill or submission."""
    if metadata is None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    if is_sell_without_holdings_rejection(event, metadata):
        return True
    if is_paper_futures_terminal_nonfill(event, metadata):
        return True
    if event.get("event_type") != "paper_order_simulated":
        return False
    return _has_no_fill_or_broker_submission(event, metadata)
