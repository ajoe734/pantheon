"""Runtime status projection derived from accepted telemetry events."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from services.execution.lean_runtime.performance_telemetry import (
    is_portfolio_mark_fresh,
    quarter_equity_observation,
)
from services.execution.market_snapshot_admission import is_taiwan_symbol


log = logging.getLogger(__name__)

RUNTIME_RETIRED_EVENT_TYPE = "runtime_retired"
_MAX_PENDING_COST_FILLS = 64
PERSONA_INSTRUMENT_COUNTERFACTUAL_EVENT_TYPE = "persona_instrument_counterfactual"
_MAX_INSTRUMENT_COUNTERFACTUAL_ROWS = 512
_COUNTERFACTUAL_ROW_FIELDS = (
    "decision_id",
    "instrument_id",
    "kind",
    "underlying",
    "view_direction",
    "chosen",
    "units",
    "multiplier",
    "entry",
    "mark",
    "pnl_cf",
    "costs",
    "margin_use",
    "underlying_entry",
    "underlying_mark",
)

_LIFECYCLE_IDENTITY_EVENT_TYPES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "paper_order_simulated",
        "order_submitted",
        "order_accepted",
        "order_partially_filled",
        "paper_fill_simulated",
        "fill_received",
        "order_filled",
        "order_rejection",
        "order_rejection_simulated",
        "order_canceled",
        "order_cancelled",
        "position_snapshot",
        "position_snapshot_received",
        "broker_position_snapshot",
        "reconciliation_completed",
        "reconciliation_failed",
    }
)
_DEFAULT_RECENT_LIFECYCLE_EVENT_LIMIT = 512
_LIFECYCLE_IDENTITY_FIELDS = (
    "event_id",
    "event_type",
    "created_at",
    "tenant_id",
    "environment",
    "execution_mode",
    "deployment_stage",
    "binding_id",
    "runtime_id",
    "capital_pool_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "persona_capital_binding_id",
    "trace_id",
    "journey_id",
    "signal_id",
    "run_id",
    "loop_run_id",
    "aggregate_type",
    "aggregate_id",
    "sequence_no",
    "causal_parent_id",
    "source_mode",
    "target",
    "authority_refs",
    "metadata",
    "correlation_envelope",
)
_SUMMARY_LIFECYCLE_IDENTITY_FIELDS = (
    "tenant_id",
    "environment",
    "execution_mode",
    "trace_id",
    "journey_id",
    "signal_id",
    "run_id",
    "loop_run_id",
    "aggregate_type",
    "aggregate_id",
    "sequence_no",
    "causal_parent_id",
    "source_mode",
    "authority_refs",
    "correlation_envelope",
)


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _explicit_metric_as_of(value: Any) -> Optional[str]:
    """Return a timezone-aware RFC3339 timestamp, or None when unusable.

    ``created_at`` remains the backward-compatible metric timestamp fallback.
    Explicit per-metric timestamps are only trusted when they are strings and
    carry timezone information; accepting a naive value would make freshness
    depend on the telemetry host's local timezone.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return text


def _strict_rfc3339(value: Any) -> tuple[Optional[str], Optional[datetime]]:
    """Return an explicitly timezone-aware timestamp and its UTC value.

    Binding rollover is an ownership decision, so unlike the display-oriented
    parser above it must not silently interpret a naive timestamp in the
    telemetry host's timezone.
    """
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if not text:
        return None, None
    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        return None, None
    return text, parsed.astimezone(timezone.utc)


def _binding_effective_boundary(
    metadata: dict[str, Any],
    *,
    event_at: Optional[datetime],
) -> tuple[Optional[str], Optional[datetime], Optional[str]]:
    """Read an optional RuntimeBinding effective boundary from event metadata.

    Producers have historically emitted only ``created_at``.  Newer adapters
    may attach binding authority metadata without changing the telemetry
    envelope schema, so accept the common flat and nested representations.  A
    boundary after the event that cites it is contradictory and is not used.
    """
    candidates: list[tuple[str, Any]] = [
        ("metadata.runtime_binding_effective_at", metadata.get("runtime_binding_effective_at")),
        ("metadata.binding_effective_at", metadata.get("binding_effective_at")),
    ]
    for container_key in ("runtime_binding", "binding"):
        container = metadata.get(container_key)
        if isinstance(container, dict):
            candidates.append(
                (f"metadata.{container_key}.effective_at", container.get("effective_at"))
            )

    for source, value in candidates:
        text, parsed = _strict_rfc3339(value)
        if parsed is None:
            continue
        if event_at is not None and parsed > event_at:
            continue
        return text, parsed, source
    return None, None, None


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _extract_mark_ref_provenance(
    ref: dict[str, Any],
) -> tuple[
    Optional[str],
    Optional[datetime],
    Optional[dict[str, Any]],
    Optional[dict[str, Any]],
    bool,
]:
    has_malformed = False
    obs_text: Optional[str] = None
    obs_dt: Optional[datetime] = None
    if "observed_at" in ref and ref.get("observed_at") is not None:
        obs_text, obs_dt = _strict_rfc3339(ref.get("observed_at"))
        if obs_text is None or obs_dt is None:
            has_malformed = True
    lineage_obj: Optional[dict[str, Any]] = None
    if "lineage" in ref and ref.get("lineage") is not None:
        raw_lin = ref.get("lineage")
        if isinstance(raw_lin, dict) and raw_lin:
            try:
                lineage_obj = json.loads(json.dumps(raw_lin, allow_nan=False))
            except (TypeError, ValueError):
                has_malformed = True
        else:
            has_malformed = True
    calendar_obj: Optional[dict[str, Any]] = None
    if "calendar_evidence" in ref and ref.get("calendar_evidence") is not None:
        raw_cal = ref.get("calendar_evidence")
        if isinstance(raw_cal, dict) and raw_cal:
            try:
                calendar_obj = json.loads(json.dumps(raw_cal, allow_nan=False))
            except (TypeError, ValueError):
                has_malformed = True
        else:
            has_malformed = True
    return obs_text, obs_dt, lineage_obj, calendar_obj, has_malformed


def _finite_positive(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return number if number is not None and number > 0.0 else None


def _fill_instrument_descriptor(
    meta: dict[str, Any], symbol: Any
) -> tuple[Any, dict[str, Any]]:
    """Return ``(position_key, descriptor)`` for one fill's metadata.

    Cash shares stay keyed by their symbol, which keeps persisted summaries and
    the 11/06 hosted holdings unchanged. A stock future is keyed by its
    ``instrument_id`` so a future and its underlying share never net together.
    The descriptor carries only what the fill itself reported.
    """
    descriptor: dict[str, Any] = {}
    instrument_id = meta.get("instrument_id")
    if isinstance(instrument_id, str) and instrument_id.strip():
        descriptor["instrument_id"] = instrument_id.strip()
    kind = meta.get("kind")
    if isinstance(kind, str) and kind.strip():
        descriptor["kind"] = kind.strip()
    multiplier = _finite_positive(meta.get("multiplier"))
    if multiplier is not None:
        descriptor["multiplier"] = multiplier
    contract_month = meta.get("contract_month")
    if isinstance(contract_month, (str, int)) and not isinstance(contract_month, bool):
        if str(contract_month).strip():
            descriptor["contract_month"] = str(contract_month).strip()
    margin = _finite_number(meta.get("initial_margin_reserved"))
    if margin is not None and margin >= 0.0:
        descriptor["margin_reserved"] = margin
    if isinstance(symbol, str) and symbol.strip():
        descriptor["symbol"] = symbol.strip()
    if descriptor.get("kind") == "stock_future" and descriptor.get("instrument_id"):
        return descriptor["instrument_id"], descriptor
    return symbol, descriptor


def _clean_text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _trade_decision_identity(
    metadata: dict[str, Any],
) -> tuple[Optional[str], dict[str, str]]:
    """Return ``(instrument_id, {decision_id, reason_code})`` of a trade_decision.

    The paper signal producer carries the decision identity on
    ``metadata.decision_id`` and the chosen expression on
    ``metadata.expression_decision`` ({instrument_id, reason_code, units, side}).
    ``metadata.reason_code`` is accepted when the expression record has none.
    """
    expression = metadata.get("expression_decision")
    if not isinstance(expression, dict):
        expression = {}
    identity: dict[str, str] = {}
    decision_id = _clean_text(metadata.get("decision_id"))
    if decision_id:
        identity["decision_id"] = decision_id
    reason_code = _clean_text(expression.get("reason_code")) or _clean_text(
        metadata.get("reason_code")
    )
    if reason_code:
        identity["reason_code"] = reason_code
    instrument_id = _clean_text(expression.get("instrument_id")) or _clean_text(
        metadata.get("instrument_id")
    )
    return instrument_id, identity


def _round6(val: float) -> float:
    rounded = round(float(val), 6)
    return 0.0 if rounded == 0.0 else rounded


def _fill_recognized_cost(metadata: Any, *, kind: Any = None) -> Optional[float]:
    """Return the owner-recorded cost (fee + tax) of one fill, or None if unknown.

    A stock-future fill must carry finite non-negative ``fee`` and ``tax``;
    anything else is explicitly unknown, never an invented 0. A cash-share
    fill predating the futures cost schedule carries neither key and is a
    recorded zero-cost fill; a key that is present must still be valid.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    parts: list[float] = []
    for key in ("fee", "tax"):
        if key not in meta:
            if kind == "stock_future":
                return None
            continue
        if meta[key] is None:
            return None
        value = _finite_number(meta[key])
        if value is None or value < 0.0:
            return None
        parts.append(value)
    return float(sum(parts))


def _entry_costs_known(entry: Optional[dict[str, Any]], kind: Any = None) -> bool:
    """Whether a per-instrument cost entry carries trustworthy recognized costs.

    An entry persisted before cost tracking has neither ``costs`` nor
    ``costs_known``. For a cash share that is the documented zero-cost shape;
    for a stock future the recorded cost is simply absent, so it stays unknown
    until startup backfill reconstructs it from retained owner fills.
    """
    if not isinstance(entry, dict):
        return True
    if "costs_known" in entry:
        return entry["costs_known"] is not False
    if "costs" in entry:
        return True
    return kind != "stock_future"


def _has_legacy_future_cost_entry(current: dict[str, Any]) -> bool:
    costs = current.get("_cost_by_symbol")
    if not isinstance(costs, dict):
        return False
    instruments = current.get("_instrument_by_key")
    instruments = instruments if isinstance(instruments, dict) else {}
    for key, entry in costs.items():
        descriptor = instruments.get(key)
        kind = descriptor.get("kind") if isinstance(descriptor, dict) else None
        if isinstance(entry, dict) and not _entry_costs_known(entry, kind):
            if "costs_known" not in entry:
                return True
    return False


def _fold_symbol_cost(
    entry: Optional[dict[str, Any]],
    fill_qty: float,
    fill_price: float,
    multiplier: float = 1.0,
    cost: Optional[float] = 0.0,
    kind: Any = None,
) -> dict[str, Any]:
    """Fold one fill and its recognized cost into the per-instrument entry.

    Cumulative ``costs`` are tracked beside the gross ``realized_pnl`` and
    ``costs_known`` turns False permanently once any fill's cost is unknown.
    """
    folded = _fold_symbol_cost_gross(entry, fill_qty, fill_price, multiplier)
    prior_known = _entry_costs_known(entry, kind)
    prior_costs = float((entry or {}).get("costs") or 0.0)
    if prior_known and cost is not None and math.isfinite(float(cost)):
        folded["costs"] = prior_costs + float(cost)
        folded["costs_known"] = True
    else:
        folded["costs"] = prior_costs
        folded["costs_known"] = False
    return folded


def _fold_symbol_cost_gross(
    entry: Optional[dict[str, Any]],
    fill_qty: float,
    fill_price: float,
    multiplier: float = 1.0,
) -> dict[str, Any]:
    """Fold one instrument fill into unrounded moving-average cost and realized PnL.

    ``average_price`` is the cost per unit (a share or a contract). Realized PnL
    is expressed in currency, so it is scaled by ``multiplier`` (1.0 for a cash
    share, the contract multiplier for a stock future).
    """
    old_qty = float((entry or {}).get("quantity") or 0.0)
    raw_old_avg = (entry or {}).get("average_price")
    old_avg = (
        float(raw_old_avg)
        if isinstance(raw_old_avg, (int, float)) and not isinstance(raw_old_avg, bool)
        else None
    )
    realized = float((entry or {}).get("realized_pnl") or 0.0)
    qty = float(fill_qty)
    price = float(fill_price)
    mult = float(multiplier)

    if abs(old_qty) <= 1e-12 or old_avg is None:
        new_qty = qty
        new_avg: Optional[float] = price if abs(new_qty) > 1e-12 else None
        return {
            "quantity": new_qty,
            "average_price": new_avg,
            "realized_pnl": realized,
        }

    if (old_qty > 0.0 and qty > 0.0) or (old_qty < 0.0 and qty < 0.0):
        new_qty = old_qty + qty
        new_avg = (abs(old_qty) * old_avg + abs(qty) * price) / abs(new_qty)
        return {
            "quantity": new_qty,
            "average_price": new_avg,
            "realized_pnl": realized,
        }

    position_sign = 1.0 if old_qty > 0.0 else -1.0
    closed_quantity = min(abs(old_qty), abs(qty))
    new_realized = realized + closed_quantity * mult * (price - old_avg) * position_sign
    raw_new_qty = old_qty + qty
    if abs(raw_new_qty) <= 1e-12 or abs(qty) == abs(old_qty):
        return {
            "quantity": 0.0,
            "average_price": None,
            "realized_pnl": new_realized,
        }
    if abs(qty) < abs(old_qty):
        return {
            "quantity": raw_new_qty,
            "average_price": old_avg,
            "realized_pnl": new_realized,
        }
    return {
        "quantity": raw_new_qty,
        "average_price": price,
        "realized_pnl": new_realized,
    }


def _withhold_cost(current: dict[str, Any]) -> None:
    current["_cost_withheld"] = True
    current.pop("_cost_by_symbol", None)
    current.pop("_cost_applied_fill_count", None)
    current.pop("_cost_pending_fills", None)


def _cost_is_available(current: dict[str, Any]) -> bool:
    if current.get("_cost_withheld"):
        return False
    if current.get("_cost_pending_fills"):
        return False
    if not isinstance(current.get("_cost_by_symbol"), dict):
        return False
    applied_fills = int(current.get("_cost_applied_fill_count") or 0)
    executed_fills = int(current.get("executed_trade_count") or 0)
    if applied_fills <= 0 or applied_fills != executed_fills:
        return False
    ledger_fill_count = current.get("ledger_fill_count")
    if (
        isinstance(ledger_fill_count, int)
        and not isinstance(ledger_fill_count, bool)
        and ledger_fill_count > applied_fills
    ):
        return False
    return True


def _rebuild_positions(
    current: dict[str, Any],
    *,
    reference_at: Optional[datetime] = None,
) -> None:
    """Project fill-derived quantities, joined with accepted valuation marks.

    Marks are only joined when they belong to the current binding generation
    and the exact symbol; a position without one stays explicitly unmarked.
    """
    ledger_fill_count = current.get("ledger_fill_count")
    executed_fills = int(current.get("executed_trade_count") or 0)
    if (
        isinstance(ledger_fill_count, int)
        and not isinstance(ledger_fill_count, bool)
        and ledger_fill_count > executed_fills
    ):
        # The runtime ledger (authoritative, carried by pnl/drawdown snapshots)
        # has committed more fills than this projection received as fill
        # events. Quantities summed from the received fills would present a
        # stale open position as current, so withhold them instead.
        current["positions"] = []
        current["position_count"] = None
        current.pop("positions_by_underlying", None)
        current["positions_status"] = "unavailable"
        current["positions_unavailable_reason"] = (
            f"fill_event_gap: runtime ledger fill_count={ledger_fill_count} "
            f"but {executed_fills} fill event(s) projected"
        )
        return
    current.pop("positions_status", None)
    current.pop("positions_unavailable_reason", None)
    positions = current.get("_positions_by_symbol")
    marks = current.get("_marks_by_symbol")
    if not isinstance(positions, dict):
        positions = {}
    if not isinstance(marks, dict):
        marks = {}
    cost_available = _cost_is_available(current)
    cost_by_symbol = (
        current.get("_cost_by_symbol")
        if cost_available and isinstance(current.get("_cost_by_symbol"), dict)
        else {}
    )
    currency_by_symbol = (
        current.get("_currency_by_symbol")
        if isinstance(current.get("_currency_by_symbol"), dict)
        else {}
    )
    broker_by_symbol = (
        current.get("_broker_by_symbol")
        if isinstance(current.get("_broker_by_symbol"), dict)
        else {}
    )
    instrument_by_key = (
        current.get("_instrument_by_key")
        if isinstance(current.get("_instrument_by_key"), dict)
        else {}
    )
    decision_by_key = (
        current.get("_decision_by_key")
        if isinstance(current.get("_decision_by_key"), dict)
        else {}
    )
    binding_id = str(current.get("binding_id") or "")
    failure = current.get("_valuation_failure")
    latest_event_at = reference_at or _parse_rfc3339(
        current.get("_binding_latest_event_at") or current.get("last_event_at")
    )
    rows = []
    for position_key, quantity in sorted(positions.items()):
        if not quantity:
            continue
        # ``position_key`` is the symbol for a cash share and the instrument_id
        # for a stock future; ``symbol`` is always the underlying/native symbol.
        descriptor = instrument_by_key.get(position_key)
        if not isinstance(descriptor, dict):
            descriptor = {}
        symbol = descriptor.get("symbol") or position_key
        multiplier = _finite_positive(descriptor.get("multiplier")) or 1.0
        row: dict[str, Any] = {"symbol": symbol, "quantity": quantity}
        if descriptor.get("instrument_id"):
            row["instrument_id"] = descriptor["instrument_id"]
            if descriptor.get("kind"):
                row["kind"] = descriptor["kind"]
            row["multiplier"] = multiplier
            if descriptor.get("contract_month"):
                row["contract_month"] = descriptor["contract_month"]
            if descriptor.get("kind") == "stock_future":
                row["margin_reserved"] = _round6(
                    float(descriptor.get("margin_reserved") or 0.0)
                )
        decision_identity = decision_by_key.get(position_key)
        if isinstance(decision_identity, dict):
            for identity_key in ("decision_id", "reason_code"):
                if decision_identity.get(identity_key):
                    row[identity_key] = decision_identity[identity_key]
        fill_currency = currency_by_symbol.get(position_key)
        if isinstance(fill_currency, str) and fill_currency.strip():
            row["currency"] = fill_currency.strip()
        fill_broker = broker_by_symbol.get(position_key)
        if isinstance(fill_broker, str) and fill_broker.strip():
            row["broker_id"] = fill_broker.strip()
        sym_cost = cost_by_symbol.get(position_key) if isinstance(cost_by_symbol, dict) else None
        raw_avg = sym_cost.get("average_price") if isinstance(sym_cost, dict) else None
        unrounded_avg: Optional[float] = (
            float(raw_avg)
            if isinstance(raw_avg, (int, float))
            and not isinstance(raw_avg, bool)
            and math.isfinite(float(raw_avg))
            and float(raw_avg) > 0.0
            else None
        )
        if unrounded_avg is not None and isinstance(sym_cost, dict):
            unrounded_realized = float(sym_cost.get("realized_pnl") or 0.0)
            row["average_price"] = _round6(unrounded_avg)
            row["gross_realized_pnl"] = _round6(unrounded_realized)
            costs_known = _entry_costs_known(sym_cost, descriptor.get("kind"))
            if costs_known:
                unrounded_costs = float(sym_cost.get("costs") or 0.0)
                row["costs"] = _round6(unrounded_costs)
                # Recognized costs are cash expenses already paid: they are
                # part of net realized PnL, never of unrealized or margin.
                row["realized_pnl"] = _round6(unrounded_realized - unrounded_costs)
            else:
                row["costs_status"] = "unknown"
        mark = marks.get(position_key)
        if isinstance(mark, dict) and str(mark.get("binding_id") or "") == binding_id:
            failed_symbols = failure.get("symbols") if isinstance(failure, dict) else None
            if isinstance(failure, dict) and (
                not failed_symbols
                or symbol in failed_symbols
                or position_key in failed_symbols
            ):
                # The producer's latest valuation attempt failed: keep the last
                # known quote only as explicitly unavailable provenance.
                row["mark_unavailable_reason"] = failure.get("code") or failure.get("status")
                row["last_known_mark_price"] = mark["price"]
                row["last_known_mark_as_of"] = mark["as_of"]
                row["last_known_mark_source_ref"] = mark["source_ref"]
                if isinstance(mark.get("observed_at"), str) and mark["observed_at"]:
                    row["last_known_mark_observed_at"] = mark["observed_at"]
                if isinstance(mark.get("lineage"), dict) and mark["lineage"]:
                    row["last_known_mark_lineage"] = json.loads(
                        json.dumps(mark["lineage"])
                    )
                if (
                    isinstance(mark.get("calendar_evidence"), dict)
                    and mark["calendar_evidence"]
                ):
                    row["last_known_mark_calendar_evidence"] = json.loads(
                        json.dumps(mark["calendar_evidence"])
                    )
                if mark.get("quote_currency"):
                    row["currency"] = mark["quote_currency"]
                rows.append(row)
                continue
            mark_as_of = _parse_rfc3339(mark.get("as_of"))
            symbol_stale = mark_as_of is None or bool(mark.get("provenance_invalid"))
            if (
                not symbol_stale
                and latest_event_at is not None
                and mark_as_of is not None
            ):
                symbol_stale = not is_portfolio_mark_fresh(
                    # A stock future's mark is keyed and aged by its contract
                    # instrument id, not by the underlying cash symbol.
                    symbol=position_key if position_key.startswith("ssf:") else symbol,
                    mark_as_of=mark_as_of,
                    now_dt=latest_event_at,
                    max_mark_age_seconds=172800.0,
                    observed_at=mark.get("observed_at"),
                    lineage=mark.get("lineage"),
                    calendar_evidence=mark.get("calendar_evidence"),
                )
            if symbol_stale:
                row["mark_stale"] = True
                row["last_known_mark_price"] = mark["price"]
                row["last_known_mark_as_of"] = mark["as_of"]
                row["last_known_mark_source_ref"] = mark["source_ref"]
                if isinstance(mark.get("observed_at"), str) and mark["observed_at"]:
                    row["last_known_mark_observed_at"] = mark["observed_at"]
                if isinstance(mark.get("lineage"), dict) and mark["lineage"]:
                    row["last_known_mark_lineage"] = json.loads(
                        json.dumps(mark["lineage"])
                    )
                if (
                    isinstance(mark.get("calendar_evidence"), dict)
                    and mark["calendar_evidence"]
                ):
                    row["last_known_mark_calendar_evidence"] = json.loads(
                        json.dumps(mark["calendar_evidence"])
                    )
                if mark.get("quote_currency"):
                    row["currency"] = mark["quote_currency"]
                rows.append(row)
                continue
            row["mark_price"] = mark["price"]
            row["mark_as_of"] = mark["as_of"]
            row["mark_source_ref"] = mark["source_ref"]
            row["market_value"] = round(
                float(quantity) * multiplier * float(mark["price"]), 6
            )
            if unrounded_avg is not None and row.get("costs_status") != "unknown":
                row["unrealized_pnl"] = _round6(
                    float(quantity) * multiplier * (float(mark["price"]) - unrounded_avg)
                )
            if isinstance(mark.get("observed_at"), str) and mark["observed_at"]:
                row["mark_observed_at"] = mark["observed_at"]
            if isinstance(mark.get("lineage"), dict) and mark["lineage"]:
                row["mark_lineage"] = json.loads(json.dumps(mark["lineage"]))
            if (
                isinstance(mark.get("calendar_evidence"), dict)
                and mark["calendar_evidence"]
            ):
                row["mark_calendar_evidence"] = json.loads(
                    json.dumps(mark["calendar_evidence"])
                )
            if mark.get("quote_currency"):
                row["currency"] = mark["quote_currency"]
        rows.append(row)
    current["positions"] = rows
    current["position_count"] = len(rows)
    by_underlying: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = by_underlying.setdefault(
            str(row["symbol"]), {"net_delta_units": 0.0, "instruments": []}
        )
        group["net_delta_units"] = _round6(
            group["net_delta_units"]
            + float(row["quantity"]) * float(row.get("multiplier") or 1.0)
        )
        group["instruments"].append(
            {
                key: row[key]
                for key in (
                    "instrument_id",
                    "kind",
                    "quantity",
                    "multiplier",
                    "contract_month",
                    "margin_reserved",
                    "currency",
                )
                if key in row
            }
        )
    if by_underlying:
        current["positions_by_underlying"] = by_underlying
    else:
        current.pop("positions_by_underlying", None)


def _summary_identity(payload: dict[str, Any]) -> Optional[tuple[str, str]]:
    runtime_id = str(payload.get("runtime_id") or "").strip()
    if not runtime_id:
        return None
    tenant_id = str(payload.get("tenant_id") or "").strip()
    envelope = payload.get("correlation_envelope")
    envelope_tenant_id = (
        str(envelope.get("tenant_id") or "").strip()
        if isinstance(envelope, dict)
        else ""
    )
    if tenant_id and envelope_tenant_id and tenant_id != envelope_tenant_id:
        return None
    return tenant_id or envelope_tenant_id, runtime_id


def _summary_key(payload: dict[str, Any]) -> Optional[str]:
    identity = _summary_identity(payload)
    if identity is None:
        return None
    # Persist a collision-safe composite key while keeping the summary payload
    # and HTTP contract unchanged. Legacy unscoped records remain isolated
    # under an empty tenant and are never returned to a scoped caller.
    return json.dumps(identity, separators=(",", ":"), ensure_ascii=False)


def _json_safe_object(value: Any) -> Optional[dict[str, Any]]:
    """Return a detached JSON object, or None for unsafe/non-object input."""
    if not isinstance(value, dict):
        return None
    try:
        cloned = json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None
    return cloned if isinstance(cloned, dict) else None


def _append_recent_lifecycle_event_id(
    values: Any,
    event_id: Any,
    *,
    limit: int,
) -> list[str]:
    """Return an ordered, deduplicated, bounded lifecycle receipt list."""
    recent: list[str] = []
    seen: set[str] = set()
    if isinstance(values, list):
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in seen:
                recent.append(normalized)
                seen.add(normalized)
    normalized_event_id = str(event_id or "").strip()
    if normalized_event_id:
        # A replay through this projection becomes the most recent observation
        # without creating a second receipt, keeping the history tail aligned
        # with last_lifecycle_identity.
        recent = [value for value in recent if value != normalized_event_id]
        recent.append(normalized_event_id)
    return recent[-limit:]


class RuntimeSummaryProjectionStore:
    """Small telemetry-owned read model for BFF runtime status surfaces."""

    def __init__(
        self,
        path: Optional[str | Path] = None,
        *,
        heartbeat_stale_after_seconds: int = 90,
        recent_lifecycle_event_limit: int = _DEFAULT_RECENT_LIFECYCLE_EVENT_LIMIT,
    ) -> None:
        self._path = Path(path) if path else None
        self._heartbeat_stale_after_seconds = max(int(heartbeat_stale_after_seconds), 1)
        self._recent_lifecycle_event_limit = max(int(recent_lifecycle_event_limit), 1)
        self._lock = threading.RLock()
        self._summaries: dict[str, dict[str, Any]] = {}
        self._load()

    @property
    def path(self) -> Optional[str]:
        return str(self._path) if self._path else None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "summary_count": len(self._summaries),
                "path": self.path,
                "heartbeat_stale_after_seconds": self._heartbeat_stale_after_seconds,
                "recent_lifecycle_event_limit": self._recent_lifecycle_event_limit,
            }

    _VALID_STAGES = frozenset({"paper", "canary", "live", "frozen"})

    @staticmethod
    def _fill_decision_identity(
        current: dict[str, Any],
        metadata: dict[str, Any],
        fill_descriptor: dict[str, Any],
        symbol: Any,
    ) -> dict[str, str]:
        """Decision identity of the decision that produced this fill.

        The trade_decision event for the fill's instrument wins; a fill that
        reports its own ``decision_id``/``reason_code`` is the fallback.
        """
        pending = current.get("_pending_decision_by_instrument")
        if isinstance(pending, dict):
            candidates = [
                fill_descriptor.get("instrument_id"),
                f"eq:{symbol}" if isinstance(symbol, str) else None,
                f"eq:{symbol.split('.')[0]}" if isinstance(symbol, str) else None,
            ]
            for candidate in candidates:
                if candidate and isinstance(pending.get(candidate), dict):
                    return dict(pending[candidate])
        identity: dict[str, str] = {}
        decision_id = _clean_text(metadata.get("decision_id"))
        reason_code = _clean_text(metadata.get("reason_code"))
        if decision_id:
            identity["decision_id"] = decision_id
        if reason_code:
            identity["reason_code"] = reason_code
        return identity

    def project_event(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update the runtime summary from one validated, accepted telemetry event."""
        stage = str(event.get("deployment_stage") or "").lower()
        if stage not in self._VALID_STAGES:
            return None

        summary_identity = _summary_identity(event)
        summary_key = _summary_key(event)
        if summary_identity is None or summary_key is None:
            return None
        incoming_tenant_id, runtime_id = summary_identity

        event_type = str(event.get("event_type") or "").strip()
        event_time = str(event.get("created_at") or utc_now_rfc3339())
        event_boundary_text, event_boundary_at = _strict_rfc3339(event.get("created_at"))
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        binding_id = str(event.get("binding_id") or event.get("runtime_binding_id") or "").strip()
        raw_trace_id = event.get("trace_id")
        trace_id = raw_trace_id.strip() if isinstance(raw_trace_id, str) else ""
        correlation_envelope = _json_safe_object(event.get("correlation_envelope"))
        if correlation_envelope is not None:
            raw_envelope_trace_id = correlation_envelope.get("trace_id")
            envelope_trace_id = (
                raw_envelope_trace_id.strip()
                if isinstance(raw_envelope_trace_id, str)
                else ""
            )
            if trace_id and envelope_trace_id and envelope_trace_id != trace_id:
                correlation_envelope = None
            elif not trace_id:
                trace_id = envelope_trace_id
        (
            binding_effective_text,
            binding_effective_at,
            binding_effective_source,
        ) = _binding_effective_boundary(metadata, event_at=event_boundary_at)
        bridge_repo = metadata.get("engine_bridge_repo")
        bridge_commit = metadata.get("engine_bridge_commit")
        bridge_path = metadata.get("engine_bridge_path")
        runtime_adapter_version = metadata.get("runtime_adapter_version")
        # A threshold-breach producer admits a derived event that just echoes
        # an existing metric value/back through ingest to prove it is
        # schema/evidence-valid before citing it as incident evidence. That
        # echo must not refresh the metric's own as-of time: doing so would
        # let a stale metric value keep looking fresh forever, because the
        # very breach it produced would re-stamp the freshness check that
        # flagged it as stale in the first place.
        is_derived_metric_echo = metadata.get("derived_from_threshold_evaluation") is True

        with self._lock:
            current = dict(self._summaries.get(summary_key, {}))
            existing_binding = str(
                current.get("binding_id") or current.get("runtime_binding_id") or ""
            ).strip()
            if existing_binding and not binding_id:
                return self._reject_binding_reclaim(
                    summary_key,
                    current,
                    event,
                    candidate_binding_id=binding_id,
                    reason="missing_binding_id",
                    candidate_boundary_at=binding_effective_text or event_boundary_text,
                )

            if existing_binding and binding_id and existing_binding != binding_id:
                accepted, reason = self._binding_rollover_is_newer(
                    current,
                    event,
                    candidate_binding_id=binding_id,
                    candidate_event_at=event_boundary_at,
                    candidate_effective_at=binding_effective_at,
                )
                if not accepted:
                    return self._reject_binding_reclaim(
                        summary_key,
                        current,
                        event,
                        candidate_binding_id=binding_id,
                        reason=reason,
                        candidate_boundary_at=binding_effective_text or event_boundary_text,
                    )

                retired_binding_ids = {
                    str(value)
                    for value in current.get("_retired_binding_ids", [])
                    if str(value).strip()
                }
                retired_binding_ids.add(existing_binding)
                current["_retired_binding_ids"] = sorted(retired_binding_ids)
                # Reset old metrics, positions, and history to prevent carrying them across rollover
                keys_to_clear = [
                    "pnl", "pnl_at", "pnl_binding_id", "pnl_event_id",
                    "initial_cash", "pnl_period_start_at", "pnl_currency", "quarter_equity_observations",
                    "drawdown", "drawdown_at", "drawdown_binding_id", "drawdown_event_id",
                    "sharpe_ratio", "sharpe_ratio_at", "sharpe_ratio_binding_id", "sharpe_ratio_event_id",
                    "fill_rate", "fill_rate_at", "fill_rate_binding_id", "fill_rate_event_id",
                    "avg_slippage_bps", "avg_slippage_bps_at", "avg_slippage_bps_binding_id", "avg_slippage_bps_event_id",
                    "total_trades", "total_trades_at", "total_trades_binding_id", "total_trades_event_id",
                    "executed_trade_count", "member_virtual_books",
                    "last_fill",
                    "_positions_by_symbol", "_marks_by_symbol", "_valuation_failure", "_valuation_attempt_at", "positions", "position_count",
                    "ledger_fill_count", "positions_status", "positions_unavailable_reason",
                    "ledger_cash", "ledger_cash_fill_count", "ledger_cash_as_of",
                    "_cost_by_symbol", "_cost_applied_fill_count", "_cost_pending_fills", "_cost_withheld",
                    "_currency_by_symbol", "_broker_by_symbol", "_instrument_by_key",
                    "_decision_by_key", "_pending_decision_by_instrument",
                    "last_heartbeat_at", "last_heartbeat_event_id",
                    "state", "connectivity_status", "broker_status",
                    "queue_lag_ms", "event_delivery_lag_ms", "reported_health_summary",
                    "trace_id", "correlation_envelope", "last_lifecycle_identity",
                    "recent_lifecycle_event_ids",
                    "runtime_package",
                    "retired", "retired_at", "retired_event_id",
                    "retired_binding_id",
                ]
                for key in keys_to_clear:
                    current.pop(key, None)

                self._set_binding_boundary(
                    current,
                    event_at_text=event_boundary_text,
                    event_at=event_boundary_at,
                    effective_at_text=binding_effective_text,
                    effective_at=binding_effective_at,
                    effective_source=binding_effective_source,
                    reset=True,
                )
            elif not existing_binding:
                self._set_binding_boundary(
                    current,
                    event_at_text=event_boundary_text,
                    event_at=event_boundary_at,
                    effective_at_text=binding_effective_text,
                    effective_at=binding_effective_at,
                    effective_source=binding_effective_source,
                    reset=True,
                )
            else:
                self._set_binding_boundary(
                    current,
                    event_at_text=event_boundary_text,
                    event_at=event_boundary_at,
                    effective_at_text=binding_effective_text,
                    effective_at=binding_effective_at,
                    effective_source=binding_effective_source,
                    reset=False,
                )

            current.update(
                {
                    "id": runtime_id,
                    "runtime_id": runtime_id,
                    "runtime_binding_id": binding_id,
                    "binding_id": binding_id,
                    "deployment_stage": event.get("deployment_stage"),
                    "capital_pool_id": event.get("capital_pool_id"),
                    "artifact_id": event.get("artifact_id"),
                    "artifact_version": event.get("artifact_version"),
                    "plan_id": event.get("plan_id"),
                    "deployment_plan_id": event.get("plan_id"),
                    "persona_capital_binding_id": event.get("persona_capital_binding_id"),
                    "last_event_id": event.get("event_id"),
                    "last_event_type": event_type,
                    "last_event_at": event_time,
                    "collected_at": event_time,
                    "window": "latest",
                    "projection_source": "telemetry_ingest",
                    "projection_updated_at": utc_now_rfc3339(),
                }
            )
            if incoming_tenant_id:
                current["tenant_id"] = incoming_tenant_id
            incoming_environment = str(event.get("environment") or "").strip()
            if incoming_environment:
                current["environment"] = incoming_environment

            # A lifecycle event owns the reconciliation trace identity. Runtime
            # freshness events (especially heartbeats) may advance last_event
            # and heartbeat fields, but they cannot replace or remove that
            # identity at the consumer-compatible top level.
            is_lifecycle_identity = (
                event_type in _LIFECYCLE_IDENTITY_EVENT_TYPES
                and bool(trace_id)
            )
            if is_lifecycle_identity:
                lifecycle_identity = {
                    field: json.loads(json.dumps(event[field]))
                    for field in _LIFECYCLE_IDENTITY_FIELDS
                    if event.get(field) not in (None, "", [], {})
                }
                lifecycle_identity["trace_id"] = trace_id
                if correlation_envelope is not None:
                    lifecycle_identity["correlation_envelope"] = correlation_envelope
                current["last_lifecycle_identity"] = lifecycle_identity
                current["recent_lifecycle_event_ids"] = (
                    _append_recent_lifecycle_event_id(
                        current.get("recent_lifecycle_event_ids"),
                        event.get("event_id"),
                        limit=self._recent_lifecycle_event_limit,
                    )
                )

            preserved_identity = current.get("last_lifecycle_identity")
            if isinstance(preserved_identity, dict):
                for field in _SUMMARY_LIFECYCLE_IDENTITY_FIELDS:
                    value = preserved_identity.get(field)
                    if value not in (None, "", [], {}):
                        current[field] = json.loads(json.dumps(value))
            else:
                if trace_id:
                    current["trace_id"] = trace_id
                if correlation_envelope is not None:
                    current["correlation_envelope"] = correlation_envelope

            if event_type == "heartbeat":
                current["last_heartbeat_at"] = event_time
                current["last_heartbeat_event_id"] = event.get("event_id")
                current["state"] = "active"
                runtime_heartbeat = (
                    metadata.get("runtime_heartbeat")
                    if isinstance(metadata.get("runtime_heartbeat"), dict)
                    else {}
                )
                connectivity_status = (
                    runtime_heartbeat.get("connectivity_status")
                    or metadata.get("connectivity_status")
                )
                broker_status = runtime_heartbeat.get("broker_status") or metadata.get("broker_status")
                queue_lag_ms = runtime_heartbeat.get("queue_lag_ms", metadata.get("queue_lag_ms"))
                event_delivery_lag_ms = runtime_heartbeat.get(
                    "event_delivery_lag_ms",
                    metadata.get("event_delivery_lag_ms"),
                )
                reported_health_summary = (
                    runtime_heartbeat.get("health_summary")
                    if isinstance(runtime_heartbeat.get("health_summary"), dict)
                    else metadata.get("reported_health_summary")
                )
                if connectivity_status is not None:
                    current["connectivity_status"] = connectivity_status
                    if connectivity_status != "connected":
                        current["state"] = "degraded"
                if broker_status is not None:
                    current["broker_status"] = broker_status
                if queue_lag_ms is not None:
                    current["queue_lag_ms"] = queue_lag_ms
                if event_delivery_lag_ms is not None:
                    current["event_delivery_lag_ms"] = event_delivery_lag_ms
                if isinstance(reported_health_summary, dict):
                    current["reported_health_summary"] = reported_health_summary
                runtime_package = metadata.get("runtime_package")
                if isinstance(runtime_package, str) and runtime_package.strip():
                    current["runtime_package"] = runtime_package.strip()
            elif event_type == "deploy_started":
                current.setdefault("state", "active")
            elif event_type == "deploy_completed":
                current["state"] = "active"
            elif event_type == RUNTIME_RETIRED_EVENT_TYPE:
                # Owner-attributed retirement: sticky for this binding
                # generation regardless of arrival order against heartbeats.
                current["retired"] = True
                current["retired_at"] = (
                    str(metadata.get("retired_at") or "").strip() or event_time
                )
                current["retired_event_id"] = event.get("event_id")
                current["retired_binding_id"] = binding_id

            if (
                current.get("retired") is True
                and str(current.get("retired_binding_id") or "") == binding_id
            ):
                current["state"] = "retired"

            metric_map = {
                "pnl": ("pnl",),
                "drawdown": ("drawdown", "drawdown_pct"),
                "sharpe_ratio": ("sharpe_ratio",),
                "fill_rate": ("fill_rate",),
                "avg_slippage_bps": ("avg_slippage_bps", "slippage_bps"),
                "total_trades": ("total_trades",),
            }
            for target_key, source_keys in metric_map.items():
                if is_derived_metric_echo:
                    break
                for source_key in source_keys:
                    if source_key in metrics:
                        # Per-metric as-of timestamp: a metric only carried
                        # forward by `dict.update` above (e.g. an old drawdown
                        # value untouched by a newer heartbeat-only event)
                        # must not read as fresh just because the summary's
                        # overall `last_event_at` advanced.
                        explicit_as_of = _explicit_metric_as_of(
                            event.get(f"{target_key}_as_of")
                        )
                        metric_as_of = explicit_as_of or event_time
                        previous_as_of = _parse_rfc3339(
                            current.get(f"{target_key}_at")
                        )
                        candidate_as_of = _parse_rfc3339(metric_as_of)
                        if previous_as_of is not None and (
                            candidate_as_of is None or candidate_as_of < previous_as_of
                        ):
                            # Events can arrive out of order. Compare each
                            # metric against its own as-of field so a stale
                            # pnl observation cannot overwrite pnl while an
                            # independently newer drawdown still advances.
                            break
                        current[target_key] = metrics[source_key]
                        if target_key == "pnl":
                            # Capital/window belong to this PnL observation. A
                            # later legacy observation must clear old provenance,
                            # and out-of-order observations must not replace it.
                            for field in ("initial_cash", "pnl_period_start_at", "pnl_currency"):
                                current.pop(field, None)
                                if field in metrics:
                                    current[field] = metrics[field]
                        current[f"{target_key}_at"] = metric_as_of
                        current[f"{target_key}_binding_id"] = binding_id
                        metric_event_id = str(event.get("event_id") or "").strip()
                        if metric_event_id:
                            current[f"{target_key}_event_id"] = metric_event_id
                        else:
                            current.pop(f"{target_key}_event_id", None)
                        break

            # Keep owner valuations independently of the latest-metric view.
            # A heartbeat or rolling-window eviction must not erase an earlier
            # quarterly decline. Conflicts at the same boundary are retained so
            # the consumer can fail closed rather than pick arrival order.
            observation = None
            if event_type == "pnl_snapshot" and not is_derived_metric_echo:
                observation = quarter_equity_observation(
                    metrics, metadata,
                    as_of=event.get("pnl_as_of") or metrics.get("pnl_as_of")
                    or metadata.get("valuation_as_of"),
                )
                if observation["status"] == "unknown" and not observation.get("as_of"):
                    # Date an admission failure without inventing a valuation
                    # time or letting undated legacy data poison every quarter.
                    observation["as_of"] = event_time
            perf_observation = metadata.get("performance_telemetry")
            if event_type == "heartbeat" and isinstance(perf_observation, dict):
                if perf_observation.get("status") in ("marks_unavailable", "invalid_ledger", "invalid_drawdown_series"):
                    _, attempted_at = _strict_rfc3339(perf_observation.get("attempted_at"))
                    attempted_at = attempted_at or event_boundary_at
                    if attempted_at is not None and event_boundary_at is not None:
                        attempted_at = min(attempted_at, event_boundary_at)
                    observation = {
                        "schema_version": "quarter_equity.v1", "status": "unknown",
                        "reason": perf_observation["status"],
                        "as_of": attempted_at.isoformat().replace("+00:00", "Z") if attempted_at else event_time,
                    }
            if observation is not None:
                observation.update({"runtime_id": runtime_id, "binding_id": binding_id,
                                    "event_id": event.get("event_id")})
                history = list(current.get("quarter_equity_observations") or [])
                semantic = {k: v for k, v in observation.items() if k != "event_id"}
                if not any({k: v for k, v in prior.items() if k != "event_id"} == semantic for prior in history):
                    history.append(observation)
                history.sort(key=lambda item: str(item.get("as_of") or ""))
                current["quarter_equity_observations"] = history

            if event_type in ("pnl_snapshot", "drawdown_snapshot") and not is_derived_metric_echo:
                reported_fill_count = metrics.get("fill_count")
                if (
                    isinstance(reported_fill_count, int)
                    and not isinstance(reported_fill_count, bool)
                    and reported_fill_count >= 0
                ):
                    # Ledger fill counts only grow within a binding generation.
                    current["ledger_fill_count"] = max(
                        int(current.get("ledger_fill_count") or 0),
                        reported_fill_count,
                    )
                    _rebuild_positions(current)

            # Surface executed paper fills so trade activity is visible end-to-end.
            # ``bracket_order_logged`` only records order intent and must never
            # inflate fill counts or positions.  Only the simulated-fill event
            # proves that the paper ledger actually changed.
            if event_type == "trade_decision":
                decided_instrument, decided_identity = _trade_decision_identity(
                    metadata
                )
                if decided_instrument and decided_identity:
                    pending_decisions = current.get(
                        "_pending_decision_by_instrument"
                    )
                    pending_decisions = (
                        dict(pending_decisions)
                        if isinstance(pending_decisions, dict)
                        else {}
                    )
                    pending_decisions[decided_instrument] = decided_identity
                    current["_pending_decision_by_instrument"] = pending_decisions

            if event_type == "paper_fill_simulated":
                symbol = metadata.get("symbol") or metrics.get("symbol")
                fill_qty = metrics.get("fill_quantity")
                fill_price = metrics.get("fill_price")
                pos_key, fill_descriptor = _fill_instrument_descriptor(
                    metadata, symbol
                )
                fill_multiplier = float(fill_descriptor.get("multiplier") or 1.0)
                fill_cost = _fill_recognized_cost(
                    metadata, kind=fill_descriptor.get("kind")
                )
                raw_lfc = metadata.get("ledger_fill_count")
                usable_lfc = (
                    isinstance(raw_lfc, int)
                    and not isinstance(raw_lfc, bool)
                    and raw_lfc >= 1
                )
                applied_lfc = int(current.get("_cost_applied_fill_count") or 0)
                raw_pending = current.get("_cost_pending_fills")
                pending_fills: dict[str, dict[str, Any]] = (
                    dict(raw_pending) if isinstance(raw_pending, dict) else {}
                )
                is_duplicate_lfc = (
                    usable_lfc
                    and not current.get("_cost_withheld")
                    and (raw_lfc <= applied_lfc or str(raw_lfc) in pending_fills)
                )
                if not is_duplicate_lfc:
                    # The paper ledger's cash after this fill, kept so readers
                    # (the expression-layer inputs) can size against published
                    # truth. The highest ledger fill count wins, so a late or
                    # replayed fill cannot move cash backwards.
                    raw_cash = metadata.get("ledger_cash")
                    if (
                        usable_lfc
                        and isinstance(raw_cash, (int, float))
                        and not isinstance(raw_cash, bool)
                        and math.isfinite(float(raw_cash))
                        and raw_lfc >= int(current.get("ledger_cash_fill_count") or 0)
                    ):
                        current["ledger_cash"] = float(raw_cash)
                        current["ledger_cash_fill_count"] = raw_lfc
                        current["ledger_cash_as_of"] = event_time
                    trade_count = int(current.get("executed_trade_count") or 0) + 1
                    current["executed_trade_count"] = trade_count
                    # total_trades may also arrive via metrics; the executed counter is
                    # authoritative for paper fills and must not regress below it.
                    current["total_trades"] = max(
                        int(current.get("total_trades") or 0), trade_count
                    )
                    usable_symbol = isinstance(symbol, str) and bool(symbol.strip())
                    usable_qty = (
                        isinstance(fill_qty, (int, float))
                        and not isinstance(fill_qty, bool)
                        and math.isfinite(float(fill_qty))
                        and float(fill_qty) != 0.0
                    )
                    usable_price = (
                        isinstance(fill_price, (int, float))
                        and not isinstance(fill_price, bool)
                        and math.isfinite(float(fill_price))
                        and float(fill_price) > 0.0
                    )
                    if symbol is not None:
                        max_pending_lfc = max(
                            (int(k) for k in pending_fills if str(k).isdigit()),
                            default=0,
                        )
                        if not usable_lfc or raw_lfc >= max(applied_lfc, max_pending_lfc):
                            current["last_fill"] = {
                                "symbol": symbol,
                                **(
                                    {"instrument_id": fill_descriptor["instrument_id"]}
                                    if fill_descriptor.get("instrument_id")
                                    else {}
                                ),
                                "quantity": fill_qty,
                                "fill_price": fill_price,
                                "action": metrics.get("action"),
                                "submitted_to_broker": bool(
                                    metrics.get("submitted_to_broker")
                                ),
                                "at": event_time,
                            }
                        if usable_symbol:
                            raw_currency = metadata.get("currency")
                            if isinstance(raw_currency, str) and raw_currency.strip():
                                currency_by_symbol = current.get("_currency_by_symbol")
                                if not isinstance(currency_by_symbol, dict):
                                    currency_by_symbol = {}
                                currency_by_symbol[pos_key] = raw_currency.strip()
                                current["_currency_by_symbol"] = currency_by_symbol
                            raw_broker = metadata.get("broker")
                            if isinstance(raw_broker, str) and raw_broker.strip():
                                broker_by_symbol = current.get("_broker_by_symbol")
                                if not isinstance(broker_by_symbol, dict):
                                    broker_by_symbol = {}
                                broker_by_symbol[pos_key] = raw_broker.strip()
                                current["_broker_by_symbol"] = broker_by_symbol
                            fill_identity = self._fill_decision_identity(
                                current, metadata, fill_descriptor, symbol
                            )
                            if fill_identity:
                                decision_by_key = current.get("_decision_by_key")
                                decision_by_key = (
                                    dict(decision_by_key)
                                    if isinstance(decision_by_key, dict)
                                    else {}
                                )
                                decision_by_key[pos_key] = fill_identity
                                current["_decision_by_key"] = decision_by_key
                            if fill_descriptor.get("instrument_id"):
                                instrument_meta = current.get("_instrument_by_key")
                                if not isinstance(instrument_meta, dict):
                                    instrument_meta = {}
                                instrument_meta[pos_key] = dict(fill_descriptor)
                                current["_instrument_by_key"] = instrument_meta
                        positions = current.get("_positions_by_symbol")
                        if not isinstance(positions, dict):
                            positions = {}
                        if isinstance(fill_qty, (int, float)) and not isinstance(
                            fill_qty, bool
                        ):
                            positions[pos_key] = float(
                                positions.get(pos_key, 0.0)
                            ) + float(fill_qty)
                        current["_positions_by_symbol"] = positions

                    if (
                        not current.get("_cost_withheld")
                        and usable_lfc
                        and usable_symbol
                        and usable_qty
                        and usable_price
                    ):
                        cost_by_symbol = current.get("_cost_by_symbol")
                        if not isinstance(cost_by_symbol, dict):
                            cost_by_symbol = {}
                        else:
                            cost_by_symbol = dict(cost_by_symbol)
                        if raw_lfc == applied_lfc + 1:
                            cost_by_symbol[pos_key] = _fold_symbol_cost(
                                cost_by_symbol.get(pos_key),
                                float(fill_qty),
                                float(fill_price),
                                fill_multiplier,
                                fill_cost,
                                fill_descriptor.get("kind"),
                            )
                            applied_lfc += 1
                            while str(applied_lfc + 1) in pending_fills:
                                next_fill = pending_fills.pop(str(applied_lfc + 1))
                                next_sym = str(next_fill["symbol"])
                                cost_by_symbol[next_sym] = _fold_symbol_cost(
                                    cost_by_symbol.get(next_sym),
                                    float(next_fill["quantity"]),
                                    float(next_fill["price"]),
                                    float(next_fill.get("multiplier") or 1.0),
                                    next_fill.get("cost"),
                                    next_fill.get("kind"),
                                )
                                applied_lfc += 1
                            current["_cost_by_symbol"] = cost_by_symbol
                            current["_cost_applied_fill_count"] = applied_lfc
                            if pending_fills:
                                current["_cost_pending_fills"] = pending_fills
                            else:
                                current.pop("_cost_pending_fills", None)
                        else:
                            if len(pending_fills) >= _MAX_PENDING_COST_FILLS:
                                _withhold_cost(current)
                            else:
                                pending_fills[str(raw_lfc)] = {
                                    "symbol": pos_key,
                                    "quantity": float(fill_qty),
                                    "price": float(fill_price),
                                    "multiplier": fill_multiplier,
                                    "cost": fill_cost,
                                    "kind": fill_descriptor.get("kind"),
                                }
                                current["_cost_pending_fills"] = pending_fills
                                current["_cost_by_symbol"] = cost_by_symbol
                                current["_cost_applied_fill_count"] = applied_lfc
                    else:
                        _withhold_cost(current)

                    if symbol is not None:
                        _rebuild_positions(current)

            # Counterfactual grading rows never touch fills, positions or cost
            # basis: they are an append-only set keyed (decision_id,
            # instrument_id), so a redelivery overwrites itself.
            if event_type == PERSONA_INSTRUMENT_COUNTERFACTUAL_EVENT_TYPE:
                decision_key = metadata.get("decision_id")
                instrument_key = metadata.get("instrument_id")
                if (
                    isinstance(decision_key, str)
                    and decision_key.strip()
                    and isinstance(instrument_key, str)
                    and instrument_key.strip()
                ):
                    rows = {
                        f"{r['decision_id']}|{r['instrument_id']}": r
                        for r in current.get("instrument_counterfactuals") or []
                    }
                    row = {k: metadata.get(k) for k in _COUNTERFACTUAL_ROW_FIELDS}
                    row["decision_id"] = decision_key.strip()
                    row["instrument_id"] = instrument_key.strip()
                    row["event_id"] = event.get("event_id")
                    row["observed_at"] = event.get("created_at")
                    rows[f"{row['decision_id']}|{row['instrument_id']}"] = row
                    ordered = sorted(rows)[-_MAX_INSTRUMENT_COUNTERFACTUAL_ROWS:]
                    current["instrument_counterfactuals"] = [rows[k] for k in ordered]

            # Expiry settlement closes a stock-future position at the final
            # settlement price. It is not a fill (the ledger fill count does not
            # move), so it only zeroes that contract's quantity and books the
            # realized PnL the ledger computed with the contract multiplier.
            if event_type == "paper_futures_settled":
                settled_id = metadata.get("instrument_id")
                if isinstance(settled_id, str) and settled_id.strip():
                    settled_key = settled_id.strip()
                    positions = current.get("_positions_by_symbol")
                    cost_by_symbol = current.get("_cost_by_symbol")
                    event_id = str(event.get("event_id") or "")
                    if isinstance(positions, dict) and settled_key in positions:
                        already = False
                        if isinstance(cost_by_symbol, dict):
                            prior = cost_by_symbol.get(settled_key)
                            already = bool(
                                event_id
                                and isinstance(prior, dict)
                                and event_id in (prior.get("settled_event_ids") or [])
                            )
                        if not already:
                            positions = dict(positions)
                            positions[settled_key] = 0.0
                            current["_positions_by_symbol"] = positions
                            realized_delta = _finite_number(metadata.get("realized_pnl"))
                            if (
                                isinstance(cost_by_symbol, dict)
                                and not current.get("_cost_withheld")
                                and realized_delta is not None
                            ):
                                cost_by_symbol = dict(cost_by_symbol)
                                prior = cost_by_symbol.get(settled_key) or {}
                                settle_cost = _fill_recognized_cost(
                                    metadata, kind="stock_future"
                                )
                                settle_known = (
                                    _entry_costs_known(prior, "stock_future")
                                    and settle_cost is not None
                                )
                                cost_by_symbol[settled_key] = {
                                    "quantity": 0.0,
                                    "average_price": None,
                                    "realized_pnl": float(prior.get("realized_pnl") or 0.0)
                                    + realized_delta,
                                    "costs": float(prior.get("costs") or 0.0)
                                    + (settle_cost if settle_known else 0.0),
                                    "costs_known": settle_known,
                                    "settled_event_ids": [
                                        *(prior.get("settled_event_ids") or []),
                                        *([event_id] if event_id else []),
                                    ],
                                }
                                current["_cost_by_symbol"] = cost_by_symbol
                            else:
                                _withhold_cost(current)
                            _rebuild_positions(current)

            # The producer reports a failed/expired valuation attempt through its
            # heartbeat performance_telemetry. Only its own attempt time orders
            # it; a heartbeat never refreshes a mark, it can only withdraw one.
            member_books = metadata.get("member_virtual_books")
            if isinstance(member_books, list) and event_type in {"heartbeat", "signal_generation"}:
                safe_books = _json_safe_object({"books": member_books})
                if safe_books is not None:
                    current["member_virtual_books"] = safe_books["books"]
            perf = metadata.get("performance_telemetry")
            if event_type == "heartbeat" and isinstance(perf, dict):
                # The heartbeat reports the same authoritative ledger fill_count
                # even when the performance sample is unchanged and no new
                # pnl/drawdown snapshot is emitted (e.g. a flat book that was
                # closed before this projection learned fill counts).
                hb_fill_count = perf.get("fill_count")
                if (
                    isinstance(hb_fill_count, int)
                    and not isinstance(hb_fill_count, bool)
                    and hb_fill_count >= 0
                ):
                    current["ledger_fill_count"] = max(
                        int(current.get("ledger_fill_count") or 0),
                        hb_fill_count,
                    )
                    _rebuild_positions(current)
                perf_diag = (
                    perf.get("diagnostic")
                    if isinstance(perf.get("diagnostic"), dict)
                    else {}
                )
                perf_status = str(perf.get("status") or perf_diag.get("status") or "")
                _, attempted_at = _strict_rfc3339(
                    perf.get("attempted_at") or perf_diag.get("attempted_at")
                )
                if (
                    attempted_at is not None
                    and event_boundary_at is not None
                    and attempted_at > event_boundary_at
                ):
                    attempted_at = event_boundary_at
                attempted_at = attempted_at or event_boundary_at
                prior = current.get("_valuation_failure")
                prior_at = _parse_rfc3339(current.get("_valuation_attempt_at"))
                if attempted_at is not None and (prior_at is None or attempted_at >= prior_at):
                    current["_valuation_attempt_at"] = attempted_at.isoformat().replace("+00:00", "Z")
                    if perf_status in (
                        "marks_unavailable",
                        "invalid_ledger",
                        "invalid_drawdown_series",
                    ):
                        missing = (
                            perf.get("missing_symbols")
                            if "missing_symbols" in perf
                            else perf_diag.get("missing_symbols")
                        )
                        current["_valuation_failure"] = {
                            "status": perf_status,
                            "code": str(
                                perf.get("code")
                                or perf_diag.get("code")
                                or perf_status
                            ),
                            "at": attempted_at.isoformat().replace("+00:00", "Z"),
                            "symbols": sorted(str(x) for x in missing)
                            if isinstance(missing, list)
                            else [],
                        }
                        _rebuild_positions(current)
                    elif perf_status in ("valued", "unchanged", "emitted") and prior is not None:
                        current.pop("_valuation_failure", None)
                        _rebuild_positions(current)

            # Producer valuation snapshots carry the actual quote marks. Each
            # mark keeps its own quote as-of (never the event/heartbeat time)
            # and only advances per symbol, so replays and out-of-order
            # events cannot regress or refresh an accepted mark.
            mark_refs = metadata.get("mark_refs")
            if (
                event_type in ("pnl_snapshot", "drawdown_snapshot")
                and not is_derived_metric_echo
                and isinstance(mark_refs, list)
            ):
                marks = current.get("_marks_by_symbol")
                if not isinstance(marks, dict):
                    marks = {}
                changed = False
                valid_ref_seen = False
                for ref in mark_refs:
                    if not isinstance(ref, dict):
                        continue
                    ref_symbol = ref.get("symbol")
                    price = _finite_number(ref.get("price"))
                    source_ref = ref.get("source_ref")
                    ref_as_of_text, ref_as_of = _strict_rfc3339(ref.get("as_of"))
                    if (
                        not isinstance(ref_symbol, str)
                        or not ref_symbol.strip()
                        or price is None
                        or price <= 0
                        or not isinstance(source_ref, str)
                        or not source_ref.strip()
                        or ref_as_of is None
                    ):
                        continue
                    (
                        obs_text,
                        obs_dt,
                        lineage_obj,
                        calendar_obj,
                        has_malformed_prov,
                    ) = _extract_mark_ref_provenance(ref)
                    quote_currency = ref.get("quote_currency")
                    cand_currency = (
                        quote_currency.strip()
                        if isinstance(quote_currency, str) and quote_currency.strip()
                        else ""
                    )
                    previous = marks.get(ref_symbol)
                    if isinstance(previous, dict) and str(previous.get("binding_id") or "") == binding_id:
                        previous_as_of = _parse_rfc3339(previous.get("as_of"))
                        if previous_as_of is not None:
                            if ref_as_of < previous_as_of:
                                continue
                            if ref_as_of == previous_as_of:
                                prev_price = _finite_number(previous.get("price"))
                                prev_source_ref = str(previous.get("source_ref") or "").strip()
                                prev_currency = str(previous.get("quote_currency") or "").strip()
                                if (
                                    prev_price is None
                                    or round(price, 8) != round(prev_price, 8)
                                    or source_ref.strip() != prev_source_ref
                                    or (
                                        prev_currency
                                        and cand_currency
                                        and cand_currency != prev_currency
                                    )
                                    or has_malformed_prov
                                ):
                                    continue
                                _, prev_obs_dt = _strict_rfc3339(previous.get("observed_at"))
                                prev_lineage = (
                                    previous.get("lineage")
                                    if isinstance(previous.get("lineage"), dict)
                                    and previous.get("lineage")
                                    else None
                                )
                                prev_calendar = (
                                    previous.get("calendar_evidence")
                                    if isinstance(previous.get("calendar_evidence"), dict)
                                    and previous.get("calendar_evidence")
                                    else None
                                )
                                if prev_obs_dt is not None and (
                                    obs_dt is None or obs_dt < prev_obs_dt
                                ):
                                    continue
                                if prev_lineage is not None and lineage_obj is None:
                                    continue
                                if prev_calendar is not None and calendar_obj is None:
                                    continue
                                check_dt = obs_dt or event_boundary_at or ref_as_of
                                if (
                                    is_taiwan_symbol(ref_symbol)
                                    and (
                                        obs_text is not None
                                        or lineage_obj is not None
                                        or calendar_obj is not None
                                    )
                                    and not is_portfolio_mark_fresh(
                                        symbol=ref_symbol,
                                        mark_as_of=ref_as_of,
                                        now_dt=check_dt,
                                        max_mark_age_seconds=172800.0,
                                        observed_at=obs_text,
                                        lineage=lineage_obj,
                                        calendar_evidence=calendar_obj,
                                    )
                                ):
                                    continue
                                enriched = dict(previous)
                                enriched.pop("provenance_invalid", None)
                                if cand_currency and not prev_currency:
                                    enriched["quote_currency"] = cand_currency
                                if obs_text is not None:
                                    enriched["observed_at"] = obs_text
                                if lineage_obj is not None:
                                    enriched["lineage"] = lineage_obj
                                if calendar_obj is not None:
                                    enriched["calendar_evidence"] = calendar_obj
                                if enriched != previous:
                                    marks[ref_symbol] = enriched
                                    changed = True
                                valid_ref_seen = True
                                continue
                    entry: dict[str, Any] = {
                        "price": price,
                        "as_of": ref_as_of_text,
                        "source_ref": source_ref,
                        "binding_id": binding_id,
                    }
                    if cand_currency:
                        entry["quote_currency"] = cand_currency
                    if obs_text is not None:
                        entry["observed_at"] = obs_text
                    if lineage_obj is not None:
                        entry["lineage"] = lineage_obj
                    if calendar_obj is not None:
                        entry["calendar_evidence"] = calendar_obj
                    if has_malformed_prov:
                        entry["provenance_invalid"] = True
                    marks[ref_symbol] = entry
                    changed = True
                    valid_ref_seen = True
                if changed or valid_ref_seen:
                    current["_marks_by_symbol"] = marks
                    failure = current.get("_valuation_failure")
                    failure_at = _parse_rfc3339(failure.get("at")) if isinstance(failure, dict) else None
                    if failure_at is not None and event_boundary_at is not None and event_boundary_at > failure_at:
                        current.pop("_valuation_failure", None)
                    if event_boundary_at is not None:
                        prior_attempt_at = _parse_rfc3339(current.get("_valuation_attempt_at"))
                        if prior_attempt_at is None or event_boundary_at > prior_attempt_at:
                            current["_valuation_attempt_at"] = event_boundary_at.isoformat().replace("+00:00", "Z")
                    _rebuild_positions(current)

            if isinstance(current.get("_positions_by_symbol"), dict) and current.get("_positions_by_symbol"):
                _rebuild_positions(current)

            if bridge_repo is not None:
                current["engine_bridge_repo"] = bridge_repo
            if bridge_commit is not None:
                current["engine_bridge_commit"] = bridge_commit
            if bridge_path is not None:
                current["engine_bridge_path"] = bridge_path
            if runtime_adapter_version is not None:
                current["runtime_adapter_version"] = runtime_adapter_version

            # total_trades must never regress below the authoritative executed fill
            # counter, regardless of event ordering or stale metric payloads.
            executed = int(current.get("executed_trade_count") or 0)
            if executed:
                current["total_trades"] = max(int(current.get("total_trades") or 0), executed)

            current["health_summary"] = self._health_summary(current)
            self._summaries[summary_key] = current
            self._persist()
            return json.loads(json.dumps(current))

    def _binding_rollover_is_newer(
        self,
        current: dict[str, Any],
        event: dict[str, Any],
        *,
        candidate_binding_id: str,
        candidate_event_at: Optional[datetime],
        candidate_effective_at: Optional[datetime],
    ) -> tuple[bool, str]:
        """Decide whether a different binding may take ownership of a runtime.

        Binding IDs are immutable generations.  Once one has been replaced it
        is a tombstone for this runtime and can never reclaim the projection.
        For a previously unseen binding, require binding-generation evidence:
        comparable effective times, an effective time beyond a legacy
        projection's first-event boundary, or explicit replacement lineage.
        Event time alone proves when an event was created, not whether its
        binding generation is newer.
        """
        retired_binding_ids = {
            str(value)
            for value in current.get("_retired_binding_ids", [])
            if str(value).strip()
        }
        if candidate_binding_id in retired_binding_ids:
            return False, "retired_binding_reclaim"

        existing_binding = str(
            current.get("binding_id") or current.get("runtime_binding_id") or ""
        ).strip()
        rollback_parent = str(event.get("rollback_parent") or "").strip()
        if rollback_parent and rollback_parent == existing_binding and (
            candidate_event_at is not None or candidate_effective_at is not None
        ):
            return True, "explicit_replacement_lineage"

        _, current_effective_at = _strict_rfc3339(current.get("_binding_effective_at"))
        if current_effective_at is not None and candidate_effective_at is not None:
            if candidate_effective_at > current_effective_at:
                return True, "newer_binding_effective_at"
            return False, "binding_effective_at_not_newer"

        _, current_first_event_at = _strict_rfc3339(
            current.get("_binding_first_event_at")
            or current.get("last_event_at")
            or current.get("collected_at")
        )
        if candidate_effective_at is not None and current_effective_at is None:
            if current_first_event_at is None:
                return False, "binding_boundary_unavailable"
            if candidate_effective_at > current_first_event_at:
                return True, "binding_effective_at_after_legacy_boundary"
            return False, "binding_effective_at_not_after_legacy_boundary"

        if current_effective_at is not None:
            return False, "candidate_binding_effective_at_missing"
        if current_first_event_at is None or candidate_event_at is None:
            return False, "binding_boundary_unavailable"
        return False, "binding_effective_boundary_unavailable"

    @staticmethod
    def _set_binding_boundary(
        current: dict[str, Any],
        *,
        event_at_text: Optional[str],
        event_at: Optional[datetime],
        effective_at_text: Optional[str],
        effective_at: Optional[datetime],
        effective_source: Optional[str],
        reset: bool,
    ) -> None:
        if reset:
            current.pop("_binding_effective_at", None)
            current.pop("_binding_effective_source", None)
            current.pop("_binding_first_event_at", None)
            current.pop("_binding_latest_event_at", None)
            current.pop("_binding_boundary_at", None)
            current.pop("_binding_boundary_source", None)

        first_text, first_at = _strict_rfc3339(current.get("_binding_first_event_at"))
        latest_text, latest_at = _strict_rfc3339(current.get("_binding_latest_event_at"))
        if event_at is not None:
            if first_at is None or event_at < first_at:
                first_text, first_at = event_at_text, event_at
            if latest_at is None or event_at > latest_at:
                latest_text, latest_at = event_at_text, event_at
        if first_text is not None:
            current["_binding_first_event_at"] = first_text
        if latest_text is not None:
            current["_binding_latest_event_at"] = latest_text

        _, stored_effective_at = _strict_rfc3339(current.get("_binding_effective_at"))
        # A late same-binding event may upgrade a created_at-only legacy
        # projection with an effective boundary, but only if that boundary is
        # no later than the first event already attributed to the binding.
        if (
            stored_effective_at is None
            and effective_at is not None
            and (first_at is None or effective_at <= first_at)
        ):
            current["_binding_effective_at"] = effective_at_text
            current["_binding_effective_source"] = effective_source

        boundary_at = current.get("_binding_effective_at") or current.get(
            "_binding_first_event_at"
        )
        if boundary_at is not None:
            current["_binding_boundary_at"] = boundary_at
            current["_binding_boundary_source"] = (
                current.get("_binding_effective_source") or "event.created_at"
            )

    def _reject_binding_reclaim(
        self,
        summary_key: str,
        current: dict[str, Any],
        event: dict[str, Any],
        *,
        candidate_binding_id: str,
        reason: str,
        candidate_boundary_at: Optional[str],
    ) -> dict[str, Any]:
        diagnostics = (
            dict(current.get("projection_diagnostics"))
            if isinstance(current.get("projection_diagnostics"), dict)
            else {}
        )
        diagnostics["binding_rollover_rejection_count"] = int(
            diagnostics.get("binding_rollover_rejection_count") or 0
        ) + 1
        diagnostics["last_binding_rollover_rejection"] = {
            "reason": reason,
            "event_id": event.get("event_id"),
            "candidate_binding_id": candidate_binding_id or None,
            "current_binding_id": current.get("binding_id")
            or current.get("runtime_binding_id"),
            "candidate_boundary_at": candidate_boundary_at,
            "current_boundary_at": current.get("_binding_boundary_at")
            or current.get("_binding_latest_event_at")
            or current.get("last_event_at"),
            "recorded_at": utc_now_rfc3339(),
        }
        current["projection_diagnostics"] = diagnostics
        self._summaries[summary_key] = current
        self._persist()
        return json.loads(json.dumps(current))

    def backfill_missing_trade_episodes(
        self, trade_episode_store: Any, binding_events_reader: Any
    ) -> int:
        """Project canonical lifecycle events that never reached the episode store.

        Candidates are non-retired summaries whose ``ledger_fill_count`` exceeds
        the number of retained ``paper_fill_simulated`` events. Events come from
        ``binding_events_reader(binding_id, tenant_id=..., limit=100)``; those of
        the matching runtime with a journey id are replayed chronologically and,
        when ``trade_episode_id`` is absent, given a deterministic UUID5 per
        binding, position key and opening journey. Returns projected event count.
        """
        if (
            trade_episode_store is None
            or binding_events_reader is None
            or not hasattr(trade_episode_store, "retained_paper_fill_events")
            or not hasattr(trade_episode_store, "project_event")
        ):
            return 0
        with self._lock:
            candidates = [
                dict(current)
                for current in self._summaries.values()
                if isinstance(current, dict)
                and current.get("retired") is not True
                and str(current.get("state") or "") != "retired"
            ]
        projected = 0
        for current in candidates:
            identity = _summary_identity(current)
            if identity is None:
                continue
            tenant_id, runtime_id = identity
            binding_id = str(
                current.get("binding_id") or current.get("runtime_binding_id") or ""
            ).strip()
            lfc = current.get("ledger_fill_count")
            if (
                not binding_id
                or not isinstance(lfc, int)
                or isinstance(lfc, bool)
                or lfc <= 0
            ):
                continue
            retained = trade_episode_store.retained_paper_fill_events(
                tenant_id=tenant_id, runtime_id=runtime_id, binding_id=binding_id
            )
            if lfc <= len(retained):
                continue
            try:
                raw_events = binding_events_reader(
                    binding_id, tenant_id=tenant_id, limit=100
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("trade episode backfill read failed for %s: %s", binding_id, exc)
                continue
            projected += self._project_missing_episodes(
                trade_episode_store, raw_events or [], binding_id, runtime_id
            )
        return projected

    @staticmethod
    def _project_missing_episodes(
        trade_episode_store: Any,
        raw_events: list[Any],
        binding_id: str,
        runtime_id: str,
    ) -> int:
        def _journey(ev: dict[str, Any]) -> str:
            env = ev.get("correlation_envelope")
            return (
                _clean_text(ev.get("journey_id"))
                or (_clean_text(env.get("journey_id")) if isinstance(env, dict) else None)
                or ""
            )

        def _int(v: Any) -> int:
            return v if isinstance(v, int) and not isinstance(v, bool) else 0

        def _order(ev: dict[str, Any]) -> tuple[str, int, int]:
            return (
                str(ev.get("created_at") or ""),
                _int(ev.get("sequence_no")),
                _int(ev.get("ingested_seq")),
            )

        events = [
            ev
            for ev in raw_events
            if isinstance(ev, dict)
            and str(ev.get("runtime_id") or runtime_id) == runtime_id
            and ev.get("event_type") in _LIFECYCLE_IDENTITY_EVENT_TYPES
            and ev.get("event_id")
            and _journey(ev)
        ]
        events.sort(key=_order)
        journeys: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            journeys.setdefault(_journey(ev), []).append(ev)

        open_episode: dict[str, tuple[str, float]] = {}
        projected = 0
        for journey_id, j_events in journeys.items():
            fill = next(
                (e for e in j_events if e.get("event_type") == "paper_fill_simulated"),
                None,
            )
            if fill is None:
                continue
            meta = fill.get("metadata") if isinstance(fill.get("metadata"), dict) else {}
            metrics = fill.get("metrics") if isinstance(fill.get("metrics"), dict) else {}
            raw_sym = meta.get("symbol") or metrics.get("symbol")
            pos_key, _desc = _fill_instrument_descriptor(meta, raw_sym)
            if not isinstance(pos_key, str) or not pos_key.strip():
                continue
            post_qty = _finite_number(
                meta.get("position_quantity", metrics.get("position_quantity"))
            )
            existing_id = _clean_text(meta.get("trade_episode_id")) or _clean_text(
                fill.get("trade_episode_id")
            )
            prev = open_episode.get(pos_key)
            prev_id, prev_qty = prev if prev else (None, 0.0)
            new_id = existing_id or str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"pantheon:trade-episode:{binding_id}:{pos_key}:{journey_id}",
                )
            )
            reversal: Optional[dict[str, Any]] = None
            if post_qty is None:
                ep_id = existing_id or prev_id or new_id
            elif abs(post_qty) <= 1e-12:
                ep_id = existing_id or prev_id or new_id
                open_episode.pop(pos_key, None)
            elif prev_id is None or abs(prev_qty) <= 1e-12:
                ep_id = new_id
                open_episode[pos_key] = (ep_id, post_qty)
            elif (prev_qty > 0) == (post_qty > 0):
                ep_id = prev_id
                open_episode[pos_key] = (ep_id, post_qty)
            else:
                ep_id = new_id
                reversal = {
                    "closed_episode_id": prev_id,
                    "close_quantity": -prev_qty,
                    "opened_episode_id": new_id,
                    "open_quantity": post_qty,
                }
                open_episode[pos_key] = (ep_id, post_qty)
            for ev in j_events:
                ev_copy = json.loads(json.dumps(ev))
                ev_meta = ev_copy.get("metadata")
                if not isinstance(ev_meta, dict):
                    ev_meta = {}
                    ev_copy["metadata"] = ev_meta
                if not (
                    _clean_text(ev_copy.get("trade_episode_id"))
                    or _clean_text(ev_meta.get("trade_episode_id"))
                ):
                    ev_meta["trade_episode_id"] = ep_id
                if reversal and ev_copy.get("event_type") == "paper_fill_simulated":
                    ev_meta.setdefault("reversal", reversal)
                try:
                    if trade_episode_store.project_event(ev_copy) is not None:
                        projected += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "trade episode backfill projection failed for %s: %s",
                        ev_copy.get("event_id"),
                        exc,
                    )
        return projected

    def backfill_open_position_costs(self, trade_episode_store: Any) -> int:
        """Rebuild missing cost, currency, and broker state once at startup.

        Candidate summaries are non-retired summaries with open positions in
        ``_positions_by_symbol`` and no prior cost state. Retained
        ``paper_fill_simulated`` events are read in-memory from
        ``trade_episode_store.retained_paper_fill_events`` for the exact
        ``(tenant_id, runtime_id, binding_id)`` identity.
        """
        if trade_episode_store is None or not hasattr(
            trade_episode_store, "retained_paper_fill_events"
        ):
            return 0

        updated_count = 0
        with self._lock:
            for summary_key, current in list(self._summaries.items()):
                if not isinstance(current, dict):
                    continue
                if current.get("retired") is True or str(current.get("state") or "") == "retired":
                    continue
                positions = current.get("_positions_by_symbol")
                if not isinstance(positions, dict) or not any(
                    isinstance(qty, (int, float))
                    and not isinstance(qty, bool)
                    and float(qty) != 0.0
                    for qty in positions.values()
                ):
                    continue
                legacy_future_costs = (
                    "_cost_by_symbol" in current
                    and "_cost_withheld" not in current
                    and "_cost_pending_fills" not in current
                    and _has_legacy_future_cost_entry(current)
                )
                if not legacy_future_costs and (
                    "_cost_by_symbol" in current
                    or "_cost_withheld" in current
                    or "_cost_applied_fill_count" in current
                    or "_cost_pending_fills" in current
                ):
                    continue

                identity = _summary_identity(current)
                if identity is None:
                    continue
                tenant_id, runtime_id = identity
                binding_id = str(
                    current.get("binding_id") or current.get("runtime_binding_id") or ""
                ).strip()
                if not binding_id:
                    continue

                retained_events = trade_episode_store.retained_paper_fill_events(
                    tenant_id=tenant_id,
                    runtime_id=runtime_id,
                    binding_id=binding_id,
                )

                currency_by_symbol: dict[str, str] = (
                    dict(current.get("_currency_by_symbol"))
                    if isinstance(current.get("_currency_by_symbol"), dict)
                    else {}
                )
                broker_by_symbol: dict[str, str] = (
                    dict(current.get("_broker_by_symbol"))
                    if isinstance(current.get("_broker_by_symbol"), dict)
                    else {}
                )

                instrument_by_key: dict[str, dict[str, Any]] = (
                    dict(current.get("_instrument_by_key"))
                    if isinstance(current.get("_instrument_by_key"), dict)
                    else {}
                )
                fills_by_lfc: dict[int, dict[str, Any]] = {}
                has_invalid_fill = False

                def _sort_key(ev: dict[str, Any]) -> tuple[int, str, str]:
                    meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
                    lfc = meta.get("ledger_fill_count")
                    lfc_key = (
                        lfc
                        if isinstance(lfc, int) and not isinstance(lfc, bool) and lfc >= 1
                        else 10**12
                    )
                    return (
                        lfc_key,
                        str(ev.get("created_at") or ""),
                        str(ev.get("event_id") or ""),
                    )

                for ev in sorted(retained_events, key=_sort_key):
                    meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
                    metrics = ev.get("metrics") if isinstance(ev.get("metrics"), dict) else {}
                    raw_sym = meta.get("symbol") or metrics.get("symbol")
                    sym, ev_descriptor = _fill_instrument_descriptor(meta, raw_sym)
                    usable_sym = isinstance(raw_sym, str) and bool(raw_sym.strip())
                    if usable_sym:
                        if ev_descriptor.get("instrument_id"):
                            instrument_by_key[sym] = dict(ev_descriptor)
                        raw_curr = meta.get("currency")
                        if isinstance(raw_curr, str) and raw_curr.strip():
                            currency_by_symbol[sym] = raw_curr.strip()
                        raw_broker = meta.get("broker")
                        if isinstance(raw_broker, str) and raw_broker.strip():
                            broker_by_symbol[sym] = raw_broker.strip()

                    raw_lfc = meta.get("ledger_fill_count")
                    if (
                        isinstance(raw_lfc, int)
                        and not isinstance(raw_lfc, bool)
                        and raw_lfc >= 1
                    ):
                        if raw_lfc not in fills_by_lfc:
                            fills_by_lfc[raw_lfc] = ev
                    else:
                        has_invalid_fill = True

                raw_expected = current.get("ledger_fill_count")
                expected_valid = (
                    isinstance(raw_expected, int)
                    and not isinstance(raw_expected, bool)
                    and raw_expected >= 1
                )
                found_fill_counts = sorted(fills_by_lfc.keys())
                cost_by_symbol: dict[str, dict[str, Any]] = {}
                replayed_positions: dict[str, float] = {}
                fold_ok = (
                    expected_valid
                    and not has_invalid_fill
                    and found_fill_counts == list(range(1, int(raw_expected) + 1))
                )
                if fold_ok:
                    for lfc in range(1, int(raw_expected) + 1):
                        ev = fills_by_lfc[lfc]
                        meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
                        metrics = ev.get("metrics") if isinstance(ev.get("metrics"), dict) else {}
                        raw_sym = meta.get("symbol") or metrics.get("symbol")
                        sym, ev_descriptor = _fill_instrument_descriptor(meta, raw_sym)
                        fill_mult = float(ev_descriptor.get("multiplier") or 1.0)
                        qty = metrics.get("fill_quantity")
                        price = metrics.get("fill_price")
                        if (
                            not isinstance(raw_sym, str)
                            or not raw_sym.strip()
                            or not isinstance(qty, (int, float))
                            or isinstance(qty, bool)
                            or not math.isfinite(float(qty))
                            or float(qty) == 0.0
                            or not isinstance(price, (int, float))
                            or isinstance(price, bool)
                            or not math.isfinite(float(price))
                            or float(price) <= 0.0
                        ):
                            fold_ok = False
                            break
                        replayed_positions[sym] = float(
                            replayed_positions.get(sym, 0.0)
                        ) + float(qty)
                        cost_by_symbol[sym] = _fold_symbol_cost(
                            cost_by_symbol.get(sym),
                            float(qty),
                            float(price),
                            fill_mult,
                            _fill_recognized_cost(
                                meta, kind=ev_descriptor.get("kind")
                            ),
                            ev_descriptor.get("kind"),
                        )

                if fold_ok:
                    all_symbols = set(positions.keys()) | set(replayed_positions.keys())
                    for sym in all_symbols:
                        raw_pos_qty = positions.get(sym, 0.0)
                        pos_qty = (
                            float(raw_pos_qty)
                            if isinstance(raw_pos_qty, (int, float))
                            and not isinstance(raw_pos_qty, bool)
                            else 0.0
                        )
                        rep_qty = float(replayed_positions.get(sym, 0.0))
                        if abs(pos_qty - rep_qty) > 1e-9:
                            fold_ok = False
                            break

                if currency_by_symbol:
                    current["_currency_by_symbol"] = currency_by_symbol
                if broker_by_symbol:
                    current["_broker_by_symbol"] = broker_by_symbol
                if instrument_by_key:
                    current["_instrument_by_key"] = instrument_by_key

                if fold_ok:
                    expected_int = int(raw_expected)
                    current["_cost_by_symbol"] = cost_by_symbol
                    current["_cost_applied_fill_count"] = expected_int
                    current["executed_trade_count"] = max(
                        int(current.get("executed_trade_count") or 0), expected_int
                    )
                elif legacy_future_costs:
                    # Authoritative history is unavailable: keep the persisted
                    # gross entries and leave their costs explicitly unknown.
                    log.warning(
                        "Legacy futures cost entries stay unknown for runtime_id=%s "
                        "binding_id=%s: retained fill history incomplete",
                        runtime_id,
                        binding_id,
                    )
                else:
                    _withhold_cost(current)
                    log.warning(
                        "Withholding runtime summary position cost during startup backfill "
                        "for runtime_id=%s binding_id=%s expected_fill_count=%s found_fill_counts=%s",
                        runtime_id,
                        binding_id,
                        raw_expected,
                        found_fill_counts,
                    )

                _rebuild_positions(current)
                self._summaries[summary_key] = current
                updated_count += 1

            if updated_count > 0:
                self._persist()
        return updated_count

    def get(
        self,
        runtime_id: str,
        *,
        tenant_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            normalized_runtime_id = str(runtime_id).strip()
            if tenant_id is not None:
                key = json.dumps(
                    (str(tenant_id).strip(), normalized_runtime_id),
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                summary = self._summaries.get(key)
                return self._apply_staleness(summary, now=now) if summary else None

            matches = [
                summary
                for summary in self._summaries.values()
                if str(summary.get("runtime_id") or "").strip() == normalized_runtime_id
            ]
            # Unscoped internal callers are retained for compatibility only
            # when the runtime identity is unambiguous.
            if len(matches) != 1:
                return None
            return self._apply_staleness(matches[0], now=now)

    def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        now: Optional[datetime] = None,
        include_retired: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._apply_staleness(summary, now=now)
                for summary in sorted(
                    self._summaries.values(),
                    key=lambda item: (
                        str(item.get("tenant_id") or ""),
                        str(item.get("runtime_id") or ""),
                    ),
                )
                if (include_retired or summary.get("retired") is not True)
                and (
                    tenant_id is None
                    or str(summary.get("tenant_id") or "").strip()
                    == str(tenant_id).strip()
                )
            ]

    def _health_summary(self, summary: dict[str, Any]) -> dict[str, str]:
        has_bridge_identity = bool(summary.get("engine_bridge_repo") and summary.get("engine_bridge_commit"))
        connectivity_status = summary.get("connectivity_status")
        telemetry_state = "ok" if summary.get("last_heartbeat_at") else "degraded"
        if connectivity_status in {"degraded", "disconnected"}:
            telemetry_state = "degraded"
        broker_status = summary.get("broker_status")
        stage = str(summary.get("deployment_stage") or "paper").lower()
        stage_key = f"{stage}_runtime"
        if has_bridge_identity:
            bridge_status = "ok"
        elif (
            stage == "paper"
            and str(summary.get("environment") or "").lower() == "paper"
            and summary.get("runtime_package") == "paper_execution_runtime"
        ):
            bridge_status = "not_applicable"
        else:
            bridge_status = "degraded"
        return {
            stage_key: (
                "ok"
                if summary.get("state") == "active" and connectivity_status != "disconnected"
                else "degraded"
            ),
            "bridge": bridge_status,
            "telemetry": telemetry_state,
            "broker": broker_status if broker_status in {"ok", "degraded", "unavailable"} else "not_applicable",
        }

    def _apply_staleness(
        self,
        summary: dict[str, Any],
        *,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        projected = json.loads(json.dumps(summary))
        if (
            now is not None
            and isinstance(projected.get("_positions_by_symbol"), dict)
            and projected.get("_positions_by_symbol")
        ):
            _rebuild_positions(projected, reference_at=now.astimezone(timezone.utc))
        heartbeat_at = _parse_rfc3339(projected.get("last_heartbeat_at"))
        if heartbeat_at is None or projected.get("retired") is True:
            return projected

        reference = now or datetime.now(timezone.utc)
        age_seconds = (reference - heartbeat_at).total_seconds()
        if age_seconds > self._heartbeat_stale_after_seconds:
            projected["state"] = "degraded"
            if projected.get("connectivity_status") != "disconnected":
                projected["connectivity_status"] = "degraded"
            health = dict(projected.get("health_summary") or {})
            health["telemetry"] = "degraded"
            stage = str(projected.get("deployment_stage") or "paper").lower()
            stage_key = f"{stage}_runtime"
            health[stage_key] = "degraded"
            projected["health_summary"] = health
            projected["staleness"] = {
                "last_known_at": projected.get("last_heartbeat_at"),
                "age_seconds": int(age_seconds),
                "threshold_seconds": self._heartbeat_stale_after_seconds,
            }
        return projected

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8").strip() or "{}")
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("summaries"), dict):
            payload = payload["summaries"]
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(payload, dict):
            records = payload.values()
        elif isinstance(payload, list):
            records = payload
        else:
            records = []
        for item in records:
            if not isinstance(item, dict):
                continue
            key = _summary_key(item)
            if key:
                recent_lifecycle_event_ids = _append_recent_lifecycle_event_id(
                    item.get("recent_lifecycle_event_ids"),
                    None,
                    limit=self._recent_lifecycle_event_limit,
                )
                if recent_lifecycle_event_ids:
                    item["recent_lifecycle_event_ids"] = recent_lifecycle_event_ids
                else:
                    item.pop("recent_lifecycle_event_ids", None)
                normalized[key] = item
        self._summaries = normalized

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(
            json.dumps(self._summaries, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, self._path)
