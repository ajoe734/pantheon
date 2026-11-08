"""Expression replay (dev plan section 6): offline evaluation of layer C.

Input is a sequence of recorded decision records::

    {
      "decision_id": str,
      "view": {"decision_id", "underlying", "direction", "strength", ...},
      "candidates": [Candidate, ...],        # as given to the policy
      "book_before": {"positions": [...]},    # as given to the policy
      "budget": {...},                        # as given to the policy
      "marks": {instrument_id: [price, ...]}  # optional, recorded marks (D-1
                                              # settlement prices already held)
    }

plus a policy parameter set.  For every record it returns the policy outcome
(Chosen or NoTrade), the positions and margin use after it, the PnL path at the
recorded marks and ``counterfactual_pnl`` for every non chosen candidate.

The module is deterministic, performs no I/O and never recomputes direction:
``view["direction"]`` and ``view["strength"]`` are taken exactly as recorded.
It is not a backtest of the view.

PnL rule (one rule for chosen and counterfactual legs)::

    pnl = side * units * multiplier * (mark - entry) - costs
    costs = units * (fee_per_unit_per_side * 2 + unit * entry * tax_rate_per_side * 2)

``entry`` is the candidate ``price`` (D-1 price held in the candidate record).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from services.execution.lean_runtime import expression_policy as policy

REPLAY_INPUT_KEY = "expression_replay_input"
COUNTERFACTUAL_EVENT_TYPE = "persona_instrument_counterfactual"


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _f(value: Decimal) -> float:
    return float(value)


def _marks_for(record: Mapping[str, Any], instrument_id: str) -> list[Decimal]:
    raw = (record.get("marks") or {}).get(instrument_id)
    if raw is None:
        return []
    if isinstance(raw, (int, float, str, Decimal)) and not isinstance(raw, bool):
        raw = [raw]
    return [_dec(m) for m in raw]


def leg_pnl_path(
    candidate: Mapping[str, Any],
    side: str,
    units: int,
    marks: Sequence[Decimal],
) -> dict[str, Any]:
    """Net PnL at each mark for ``units`` of ``candidate`` opened at its price."""
    sign = Decimal(1) if side == "buy" else Decimal(-1)
    entry = _dec(candidate["price"])
    multiplier = _dec(candidate.get("multiplier") or 1)
    costs = Decimal(units) * policy.round_trip_cost_per_unit(candidate)
    path = [sign * Decimal(units) * multiplier * (m - entry) - costs for m in marks]
    return {
        "entry": _f(entry),
        "mark": _f(marks[-1]) if marks else None,
        "multiplier": _f(multiplier),
        "costs": _f(costs),
        "pnl_path": [_f(p) for p in path],
        "pnl": _f(path[-1]) if path else None,
        "worst_pnl": _f(min(path)) if path else None,
        "margin_at_entry": _f(Decimal(units) * _dec(candidate.get("initial_margin_per_unit") or 0)),
    }


def _position_margin(position: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> Decimal:
    per_unit = position.get("initial_margin_per_unit")
    if per_unit is None:
        per_unit = (by_id.get(str(position.get("instrument_id"))) or {}).get(
            "initial_margin_per_unit"
        )
    return _dec(per_unit or 0) * int(position.get("units") or 0)


def _apply_to_book(
    positions: list[dict[str, Any]],
    chosen: Mapping[str, Any],
    candidate: Mapping[str, Any],
    underlying: str,
) -> tuple[list[dict[str, Any]], Decimal | None]:
    """Return the book after a Chosen record and the realized PnL of a close."""
    instrument_id = str(chosen["instrument_id"])
    units = int(chosen["units"])
    if chosen.get("reason_code") == "close_leg":
        realized: Decimal | None = None
        remaining: list[dict[str, Any]] = []
        for position in positions:
            if str(position.get("instrument_id")) != instrument_id:
                remaining.append(position)
                continue
            held = int(position["units"])
            closed = min(units, held)
            entry = position.get("entry_price")
            if entry is not None:
                sign = Decimal(1) if str(position.get("direction")).upper() == "LONG" else Decimal(-1)
                gain = (
                    sign
                    * closed
                    * _dec(candidate.get("multiplier") or 1)
                    * (_dec(candidate["price"]) - _dec(entry))
                )
                realized = (realized or Decimal(0)) + gain
            if held > closed:
                remaining.append({**position, "units": held - closed})
        return remaining, realized
    opened = {
        "underlying": underlying,
        "instrument_id": instrument_id,
        "direction": "LONG" if chosen["side"] == "buy" else "SHORT",
        "units": units,
        "entry_price": _f(_dec(candidate["price"])),
        "multiplier": _f(_dec(candidate.get("multiplier") or 1)),
        "initial_margin_per_unit": _f(_dec(candidate.get("initial_margin_per_unit") or 0)),
    }
    return [*positions, opened], None


def counterfactuals(
    record: Mapping[str, Any],
    parameters: Mapping[str, Any],
    chosen_instrument_id: str | None,
) -> dict[str, dict[str, Any]]:
    """Per candidate leg the same policy rule would have taken, with PnL.

    ``q_cf`` is the policy sizing rule applied to that candidate: the target
    notional of the recorded view and budget, clamped to the candidate's own
    ``max_units_by_risk``.  A candidate that cannot express the direction (or
    has no room) has ``units == 0`` and ``pnl_cf == 0``.
    """
    view = record.get("view") or {}
    direction = str(view.get("direction") or "FLAT").upper()
    candidates = list(record.get("candidates") or [])
    params = policy.resolved_parameters(parameters)
    out: dict[str, dict[str, Any]] = {}
    feasible: list[Mapping[str, Any]] = []
    if direction in ("LONG", "SHORT"):
        feasible = policy.feasible_candidates(direction, candidates)
    target = (
        policy.target_notional(direction, view.get("strength"), feasible, params, record.get("budget") or {})
        if feasible
        else None
    )
    feasible_ids = {str(c["instrument_id"]) for c in feasible}
    side = "buy" if direction == "LONG" else "sell"
    for candidate in candidates:
        instrument_id = str(candidate.get("instrument_id"))
        if chosen_instrument_id is not None and instrument_id == chosen_instrument_id:
            continue
        if target is None or instrument_id not in feasible_ids:
            out[instrument_id] = {"units": 0, "pnl_cf": 0.0, "costs": 0.0, "entry": None, "mark": None}
            continue
        units = policy.clamped_units(candidate, target)
        leg = leg_pnl_path(candidate, side, units, _marks_for(record, instrument_id))
        out[instrument_id] = {
            "units": units,
            "pnl_cf": leg["pnl"],
            "costs": leg["costs"],
            "entry": leg["entry"],
            "mark": leg["mark"],
            "pnl_path": leg["pnl_path"],
        }
    return out


def replay(
    records: Iterable[Mapping[str, Any]],
    parameters: Mapping[str, Any] | None,
    *,
    carry_book: bool = False,
) -> dict[str, Any]:
    """Replay recorded decisions under ``parameters``.

    With ``carry_book=False`` every decision uses its recorded ``book_before``
    (comparing parameter sets on identical inputs).  With ``carry_book=True``
    the book produced by the replayed decisions is carried forward instead.
    """
    params = policy.resolved_parameters(parameters)
    carried: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    total_pnl = Decimal(0)
    graded = 0
    for index, record in enumerate(records):
        view = dict(record.get("view") or {})
        view.setdefault("decision_id", record.get("decision_id"))
        candidates = list(record.get("candidates") or [])
        budget = dict(record.get("budget") or {})
        if carry_book and index > 0:
            book = {"positions": [dict(p) for p in carried]}
        else:
            book = dict(record.get("book_before") or {})
        outcome = policy.decide(view, candidates, book, budget, params)
        by_id = {str(c.get("instrument_id")): c for c in candidates}
        positions = [dict(p) for p in (book.get("positions") or [])]
        entry: dict[str, Any] = {
            "decision_id": str(record.get("decision_id") or view.get("decision_id") or ""),
            "outcome": "NoTrade" if policy.is_no_trade(outcome) else "Chosen",
            "record": outcome,
            "pnl_path": [],
            "pnl": None,
            "worst_pnl": None,
            "costs": 0.0,
            "counterfactual_pnl": {},
        }
        chosen_id = None
        if entry["outcome"] == "Chosen":
            chosen_id = str(outcome["instrument_id"])
            candidate = by_id[chosen_id]
            positions, realized = _apply_to_book(
                positions, outcome, candidate, str(view.get("underlying") or "")
            )
            if outcome["reason_code"] == "close_leg":
                if realized is not None:
                    entry["pnl"] = _f(realized)
                    entry["pnl_path"] = [_f(realized)]
            else:
                leg = leg_pnl_path(
                    candidate, outcome["side"], int(outcome["units"]), _marks_for(record, chosen_id)
                )
                entry.update(
                    pnl_path=leg["pnl_path"],
                    pnl=leg["pnl"],
                    worst_pnl=leg["worst_pnl"],
                    costs=leg["costs"],
                    entry_price=leg["entry"],
                    mark=leg["mark"],
                )
        if entry["outcome"] == "NoTrade" or outcome.get("reason_code") != "close_leg":
            cf = counterfactuals(record, params, chosen_id)
            entry["counterfactual_pnl"] = {k: v["pnl_cf"] for k, v in cf.items()}
            entry["counterfactual_detail"] = cf
        entry["positions_after"] = positions
        entry["margin_use"] = _f(sum((_position_margin(p, by_id) for p in positions), Decimal(0)))
        carried = positions
        if entry["pnl"] is not None:
            total_pnl += _dec(entry["pnl"])
            graded += 1
        decisions.append(entry)
    return {
        "policy_parameters_digest": policy.policy_parameters_digest(params),
        "decisions": decisions,
        "summary": {
            "decision_count": len(decisions),
            "chosen_count": sum(1 for d in decisions if d["outcome"] == "Chosen"),
            "no_trade_count": sum(1 for d in decisions if d["outcome"] == "NoTrade"),
            "graded_count": graded,
            "total_pnl": _f(total_pnl),
        },
        "final_book": {"positions": carried},
    }


def counterfactual_rows(
    record: Mapping[str, Any],
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """``persona_instrument_counterfactual`` rows for one replayed decision.

    One row per candidate considered, keyed ``(decision_id, instrument_id)``,
    including the chosen instrument (flagged ``chosen``) so regret can be taken
    as ``max(pnl_cf) - pnl_chosen`` from rows alone.
    """
    view = record.get("view") or {}
    decision_id = str(result["decision_id"])
    underlying_leg = next(
        (c for c in record.get("candidates") or [] if str(c.get("instrument_id", "")).startswith("eq:")),
        None,
    )
    u_entry = u_mark = None
    if underlying_leg is not None:
        marks = _marks_for(record, str(underlying_leg["instrument_id"]))
        if marks and underlying_leg.get("price") is not None:
            u_entry, u_mark = _f(_dec(underlying_leg["price"])), _f(marks[-1])
    chosen_id = (
        str(result["record"]["instrument_id"]) if result["outcome"] == "Chosen" else None
    )
    detail = dict(result.get("counterfactual_detail") or {})
    by_id = {str(c["instrument_id"]): c for c in record.get("candidates") or []}
    rows = []
    for instrument_id in sorted(by_id):
        candidate = by_id[instrument_id]
        is_chosen = instrument_id == chosen_id
        if is_chosen:
            units = int(result["record"]["units"])
            pnl_cf, costs = result["pnl"], result["costs"]
            entry, mark = result.get("entry_price"), result.get("mark")
        else:
            d = detail.get(instrument_id) or {}
            units, pnl_cf, costs = d.get("units"), d.get("pnl_cf"), d.get("costs")
            entry, mark = d.get("entry"), d.get("mark")
        rows.append(
            {
                "decision_id": decision_id,
                "instrument_id": instrument_id,
                "kind": candidate.get("kind"),
                "underlying": view.get("underlying"),
                "view_direction": view.get("direction"),
                "chosen": is_chosen,
                "units": units,
                "multiplier": candidate.get("multiplier") or 1,
                "entry": entry,
                "mark": mark,
                "pnl_cf": pnl_cf,
                "costs": costs,
                "margin_use": _f(Decimal(units or 0) * _dec(candidate.get("initial_margin_per_unit") or 0)),
                "underlying_entry": u_entry,
                "underlying_mark": u_mark,
            }
        )
    return rows


def records_from_events(
    events: Iterable[Mapping[str, Any]],
    marks_by_decision: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rebuild replay input from recorded telemetry/signal events.

    Looks for ``metadata.expression_replay_input`` (written by the paper
    producer) on any event, keeps the first occurrence per ``decision_id`` in
    event order and returns ``(records, parameters)``.  ``parameters`` is the
    parameter set the producer recorded (the first decision's).
    """
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    parameters: dict[str, Any] = {}
    for event in events:
        meta = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else event
        payload = meta.get(REPLAY_INPUT_KEY) if isinstance(meta, Mapping) else None
        if not isinstance(payload, Mapping):
            continue
        decision_id = str(payload.get("decision_id") or (payload.get("view") or {}).get("decision_id") or "")
        if not decision_id or decision_id in seen:
            continue
        seen.add(decision_id)
        if not parameters:
            parameters = dict(payload.get("parameters") or {})
        record = {
            "decision_id": decision_id,
            "view": dict(payload.get("view") or {}),
            "candidates": [dict(c) for c in payload.get("candidates") or []],
            "book_before": dict(payload.get("book_before") or {}),
            "budget": dict(payload.get("budget") or {}),
        }
        marks = (marks_by_decision or {}).get(decision_id)
        if marks:
            record["marks"] = dict(marks)
        records.append(record)
    return records, parameters
