"""Expression policy v1 (layer C): choose the instrument and units for a view.

``decide`` is a deterministic pure function of ``(view, candidates, book, budget,
parameters)`` following dev plan section 4.4.  It never reads a price, margin or
contract from anywhere but the candidate records it is given, and it never
invents a quantity: every ``units`` value is clamped to the candidate's
system-computed ``max_units_by_risk``.

``enforce`` is the system-side validation of whatever a decider returned.  A
decider that names an instrument outside the candidate list, exceeds
``max_units_by_risk`` or supplies a price or margin field is rejected with
``instrument_not_in_candidates`` before any order can exist.
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Mapping, Sequence

POLICY_VERSION = "1.0"
POLICY_KIND = "notional_target_v1"

REASON_CODES = frozenset(
    {
        "long_cash",
        "long_lever",
        "short_via_future",
        "close_leg",
        "no_feasible_instrument",
        "flat_view",
        "already_at_target",
        "candidates_unavailable",
        "instrument_not_in_candidates",
    }
)
NO_TRADE_REASON_CODES = frozenset(
    {
        "no_feasible_instrument",
        "flat_view",
        "already_at_target",
        "candidates_unavailable",
        "instrument_not_in_candidates",
    }
)

DEFAULT_PARAMETERS: Mapping[str, Any] = {
    "lever_min_strength": 0.5,
    "notional_fraction_at_full_strength": 1.0,
    "allow_pyramiding": False,
    "close_before_reverse": True,
}

_CHOSEN_KEYS = frozenset(
    {
        "decision_id",
        "instrument_id",
        "side",
        "units",
        "reason_code",
        "reason_text",
        "strength",
        "target_notional",
        "candidates_considered",
        "policy_version",
        "policy_parameters_digest",
    }
)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def policy_parameters_digest(parameters: Mapping[str, Any]) -> str:
    """Stable digest of the exact parameters a decision was made with."""
    canonical = json.dumps(
        _json_safe(dict(parameters)), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    return value


def _resolved_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_PARAMETERS)
    merged.update(dict(parameters or {}))
    return merged


def remaining_gross_notional(budget: Mapping[str, Any]) -> Decimal:
    """Gross notional still spendable; a missing limit leaves nothing."""
    explicit = budget.get("max_gross_notional_remaining")
    if explicit is not None:
        return max(_dec(explicit), Decimal(0))
    limit = budget.get("gross_limit")
    if limit is None:
        return Decimal(0)
    return max(_dec(limit) - _dec(budget.get("gross_used") or 0), Decimal(0))


def _unit_notional(candidate: Mapping[str, Any]) -> Decimal:
    price = candidate.get("price")
    if price is None:
        return Decimal(0)
    return _dec(candidate.get("unit") or 0) * _dec(price)


def _no_trade(
    view: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "decision_id": str(view.get("decision_id") or ""),
        "reason_code": reason_code,
        "detail": detail,
        "candidates_considered": [str(c.get("instrument_id")) for c in candidates],
    }


def _binding_constraint(
    direction: str,
    candidates: Sequence[Mapping[str, Any]],
    budget: Mapping[str, Any],
) -> str:
    side = direction.lower()
    capable = [c for c in candidates if side in (c.get("side_capability") or [])]
    if not capable:
        return "side_not_supported"
    best = min(capable, key=lambda c: (_unit_notional(c), str(c.get("instrument_id"))))
    if best.get("price") is None or _dec(best["price"]) <= 0:
        return "price_unavailable"
    if _unit_notional(best) > remaining_gross_notional(budget):
        return "gross_limit"
    margin = best.get("initial_margin_per_unit")
    available = budget.get("available_cash")
    if margin is not None and (available is None or _dec(margin) > _dec(available)):
        return "insufficient_initial_margin"
    return "max_units_by_risk"


def _open_positions(
    book: Mapping[str, Any] | None, underlying: str
) -> list[Mapping[str, Any]]:
    positions = (book or {}).get("positions") or []
    return [
        p
        for p in positions
        if str(p.get("underlying")) == underlying and int(p.get("units") or 0) > 0
    ]


def _chosen(
    view: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    parameters: Mapping[str, Any],
    *,
    instrument_id: str,
    side: str,
    units: int,
    reason_code: str,
    reason_text: str,
    target_notional: Decimal,
) -> dict[str, Any]:
    return {
        "decision_id": str(view.get("decision_id") or ""),
        "instrument_id": instrument_id,
        "side": side,
        "units": int(units),
        "reason_code": reason_code,
        "reason_text": reason_text,
        "strength": view.get("strength"),
        "target_notional": float(target_notional),
        "candidates_considered": [str(c.get("instrument_id")) for c in candidates],
        "policy_version": POLICY_VERSION,
        "policy_parameters_digest": policy_parameters_digest(parameters),
    }


def decide(
    view: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    book: Mapping[str, Any] | None,
    budget: Mapping[str, Any],
    parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a Chosen record or a NoTrade record (dev plan section 4.4)."""
    params = _resolved_parameters(parameters)
    direction = str(view.get("direction") or "FLAT").upper()
    underlying = str(view.get("underlying") or "")
    strength = view.get("strength")

    # Step 1
    if direction not in ("LONG", "SHORT"):
        return _no_trade(view, candidates, "flat_view", "view direction is FLAT")
    if not candidates:
        return _no_trade(
            view, candidates, "candidates_unavailable", "no candidate record supplied"
        )

    # Step 2: reconcile with the open position on the same underlying.
    by_id = {str(c.get("instrument_id")): c for c in candidates}
    # Opposite holdings are scanned first so a same-direction no-op never
    # hides a leg that must be closed before reversing.
    open_positions = sorted(
        _open_positions(book, underlying),
        key=lambda p: str(p.get("direction") or "").upper() == direction,
    )
    for position in open_positions:
        held_direction = str(position.get("direction") or "").upper()
        held_units = int(position["units"])
        if held_direction != direction:
            if not params["close_before_reverse"]:
                continue
            instrument_id = str(position.get("instrument_id"))
            candidate = by_id.get(instrument_id)
            if candidate is None:
                return _no_trade(
                    view,
                    candidates,
                    "no_feasible_instrument",
                    "close_instrument_not_in_candidates",
                )
            return _chosen(
                view,
                candidates,
                params,
                instrument_id=instrument_id,
                side="sell" if held_direction == "LONG" else "buy",
                units=held_units,
                reason_code="close_leg",
                reason_text=(
                    f"close open {held_direction.lower()} {instrument_id} before "
                    f"reversing to {direction.lower()}"
                ),
                target_notional=_unit_notional(candidate) * held_units,
            )
        if not params["allow_pyramiding"]:
            return _no_trade(
                view,
                candidates,
                "already_at_target",
                f"open {held_direction.lower()} position in {position.get('instrument_id')}",
            )

    # Step 3: feasible set.
    side_key = direction.lower()
    feasible = [
        c
        for c in candidates
        if side_key in (c.get("side_capability") or [])
        and int(c.get("max_units_by_risk") or 0) >= 1
        and c.get("price") is not None
        and _unit_notional(c) > 0
    ]
    if not feasible:
        unavailable = any(
            ref.get("reason") == "candidates_unavailable"
            for c in candidates
            for ref in (c.get("source_refs") or [])
        )
        if unavailable and direction == "SHORT":
            return _no_trade(
                view,
                candidates,
                "candidates_unavailable",
                "futures candidate dataset unavailable",
            )
        return _no_trade(
            view,
            candidates,
            "no_feasible_instrument",
            _binding_constraint(direction, candidates, budget),
        )

    # Step 4: target notional.
    smallest = min(_unit_notional(c) for c in feasible)
    if strength is None:
        target = smallest
    elif direction == "LONG" and _dec(strength) < _dec(params["lever_min_strength"]):
        cash = next((c for c in feasible if str(c["instrument_id"]).startswith("eq:")), None)
        target = _dec(cash["price"]) * _dec(cash["unit"]) if cash is not None else smallest
    else:
        target = (
            _dec(strength)
            * _dec(params["notional_fraction_at_full_strength"])
            * remaining_gross_notional(budget)
        )

    # Step 5: size and score every feasible candidate.
    scored: list[tuple[Decimal, Decimal, str, Mapping[str, Any], int]] = []
    for c in feasible:
        per_unit = _unit_notional(c)
        raw = (target / per_unit).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        units = int(min(max(raw, Decimal(1)), Decimal(int(c["max_units_by_risk"]))))
        cost_per_unit = _dec(c.get("fee_per_unit_per_side") or 0) * 2 + per_unit * _dec(
            c.get("tax_rate_per_side") or 0
        ) * 2
        score = abs(units * per_unit - target) + units * cost_per_unit
        scored.append((score, per_unit, str(c["instrument_id"]), c, units))
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    score, _, instrument_id, winner, units = scored[0]

    # Step 6: reason code.
    if instrument_id.startswith("eq:"):
        reason_code = "long_cash"
    elif direction == "LONG":
        reason_code = "long_lever"
    else:
        reason_code = "short_via_future"
    return _chosen(
        view,
        candidates,
        params,
        instrument_id=instrument_id,
        side="buy" if direction == "LONG" else "sell",
        units=units,
        reason_code=reason_code,
        reason_text=(
            f"{direction.lower()} view strength={strength} target_notional={float(target):.2f} "
            f"lowest score {float(score):.2f} -> {instrument_id} x{units}"
        ),
        target_notional=target,
    )


def is_no_trade(record: Mapping[str, Any]) -> bool:
    return str(record.get("reason_code")) in NO_TRADE_REASON_CODES


def enforce(
    record: Any,
    candidates: Sequence[Mapping[str, Any]],
    view: Mapping[str, Any],
    book: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """System validation of a decider output; return it or an ``instrument_not_in_candidates`` NoTrade.

    A rejected record is a NoTrade so the bar is consumed and no order exists.
    """

    def reject(detail: str) -> dict[str, Any]:
        return _no_trade(view, candidates, "instrument_not_in_candidates", detail)

    if not isinstance(record, Mapping):
        return reject("decider output is not a mapping")
    for key in record:
        lowered = str(key).lower()
        if "price" in lowered or "margin" in lowered:
            return reject(f"decider supplied forbidden field {key!r}")
    if is_no_trade(record):
        return dict(record)
    for key in record:
        if key not in _CHOSEN_KEYS:
            return reject(f"decider supplied unknown field {key!r}")
    by_id = {str(c.get("instrument_id")): c for c in candidates}
    instrument_id = str(record.get("instrument_id") or "")
    candidate = by_id.get(instrument_id)
    if candidate is None:
        return reject(f"instrument_id {instrument_id!r} is not a candidate")
    side = str(record.get("side") or "")
    if side not in ("buy", "sell"):
        return reject(f"side {side!r} is invalid")
    units = record.get("units")
    if isinstance(units, bool) or not isinstance(units, int) or units < 1:
        return reject(f"units {units!r} must be an integer >= 1")
    if record.get("reason_code") not in REASON_CODES:
        return reject(f"reason_code {record.get('reason_code')!r} is outside the vocabulary")
    if record.get("reason_code") == "close_leg":
        # A close leg bypasses the open-risk cap only when it exactly reduces a
        # trusted holding: same instrument, opposite side, units <= held.
        held = [
            p
            for p in _open_positions(book, str(view.get("underlying") or ""))
            if str(p.get("instrument_id")) == instrument_id
        ]
        reducing = {"LONG": "sell", "SHORT": "buy"}
        if not any(
            reducing.get(str(p.get("direction") or "").upper()) == side
            and units <= int(p["units"])
            for p in held
        ):
            return reject("close_leg does not reduce a held position")
    elif units > int(candidate.get("max_units_by_risk") or 0):
        return reject(f"units {units} exceed max_units_by_risk")
    return dict(record)


def decide_validated(
    view: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    book: Mapping[str, Any] | None,
    budget: Mapping[str, Any],
    parameters: Mapping[str, Any] | None,
    *,
    decider: Callable[..., Any] = decide,
) -> dict[str, Any]:
    """Run a decider and pass its output through ``enforce``."""
    return enforce(
        decider(view, candidates, book, budget, parameters), candidates, view, book
    )
