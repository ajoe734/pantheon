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

from services.execution.lean_runtime.taiwan_cash_costs import buy_cash_required

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

# COORDINATOR PROPOSAL (2026-11-13, PERSONA-POOL-TARGET-POSITIONS-20261113),
# pending reviewer confirmation: the rebalance band used to trim a same-direction
# holding under notional_target_v1, the same value target_position_v1 uses.
# Deliberately NOT merged into DEFAULT_PARAMETERS so policy_parameters_digest of
# existing decisions and the multi-member envelope parameters stay byte identical.
# An explicit ``rebalance_band_fraction`` parameter overrides it.
NOTIONAL_REBALANCE_BAND_FRACTION = 0.2

_CHOSEN_KEYS = frozenset(
    {
        "decision_id",
        "policy_kind",
        "rebalance_band",
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


def _effective_gross_limit(budget: Mapping[str, Any]) -> Decimal:
    """Effective gross notional cap from ``gross_limit`` and ``max_leverage * equity``."""
    limit = budget.get("gross_limit")
    equity_raw = budget.get("equity")
    equity = _dec(equity_raw) if equity_raw is not None and _dec(equity_raw) > 0 else None
    if limit is None and budget.get("gross_limit_multiple") is not None and equity is not None:
        limit = _dec(budget["gross_limit_multiple"]) * equity
    if limit is None:
        return Decimal(0)
    limit_dec = _dec(limit)
    leverage = budget.get("max_leverage")
    if leverage is not None and equity is not None and _dec(leverage) > 0:
        limit_dec = min(limit_dec, _dec(leverage) * equity)
    return max(limit_dec, Decimal(0))


def _resolve_sizing_state(
    underlying: str,
    direction: str,
    candidates: Sequence[Mapping[str, Any]],
    book: Mapping[str, Any] | None,
    budget: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute effective account/per-name exposures, reservations and constraint caps."""
    from services.execution.lean_runtime.instrument_candidates import normalize_underlying_code

    try:
        norm_underlying = normalize_underlying_code(underlying) if underlying else ""
    except Exception:
        norm_underlying = str(underlying or "").strip()

    by_id = {str(c.get("instrument_id")): c for c in candidates}
    positions = (book or {}).get("positions")
    pos_list = positions if isinstance(positions, list) else []

    pos_gross = Decimal(0)
    pos_signed_net = Decimal(0)
    underlying_gross_by_code: dict[str, Decimal] = {}
    underlying_same_dir_notional = Decimal(0)

    for p in pos_list:
        if not isinstance(p, Mapping):
            continue
        units = int(p.get("units") or p.get("quantity") or 0)
        if units <= 0:
            continue
        raw_n = p.get("notional")
        if raw_n is not None:
            notional = abs(_dec(raw_n))
        else:
            cand = by_id.get(str(p.get("instrument_id") or ""))
            if cand is not None and _unit_notional(cand) > 0:
                notional = _unit_notional(cand) * units
            else:
                px = _dec(p.get("mark_price") or p.get("price") or 0)
                mult = _dec(p.get("multiplier") or p.get("unit") or 1)
                notional = px * mult * units
        p_dir = str(p.get("direction") or "LONG").strip().upper()
        signed = -notional if p_dir == "SHORT" else notional
        pos_gross += notional
        pos_signed_net += signed
        raw_u = str(p.get("underlying") or p.get("symbol") or p.get("instrument_id") or "").strip()
        try:
            u_code = normalize_underlying_code(raw_u) if raw_u else ""
        except Exception:
            u_code = raw_u
        if u_code:
            underlying_gross_by_code[u_code] = underlying_gross_by_code.get(u_code, Decimal(0)) + notional
            if u_code == norm_underlying and p_dir == direction.upper():
                underlying_same_dir_notional += notional

    raw_res = budget.get("order_reservations")
    if raw_res is None:
        raw_res = budget.get("reservations")
    if raw_res is None and isinstance(book, Mapping):
        raw_res = book.get("order_reservations") or book.get("reservations")
    res_items = list(raw_res.values()) if isinstance(raw_res, Mapping) else (raw_res if isinstance(raw_res, list) else [])

    res_gross = Decimal(0)
    res_signed_net = Decimal(0)
    res_cash = Decimal(0)
    res_margin = Decimal(0)
    res_underlying_gross: dict[str, Decimal] = {}
    active_res_count = 0

    for item in res_items:
        if not isinstance(item, Mapping):
            continue
        if bool(item.get("is_risk_reducing") or item.get("close_leg") or item.get("reason_code") == "close_leg"):
            continue
        active_res_count += 1
        qty = abs(_dec(item.get("units") if item.get("units") is not None else (item.get("quantity") or 0)))
        mult = _dec(item.get("multiplier") if item.get("multiplier") is not None else (item.get("unit") or 1))
        px = _dec(item.get("price") if item.get("price") is not None else (item.get("mark_price") or 0))
        raw_n = item.get("notional") if item.get("notional") is not None else item.get("gross_notional")
        notional = abs(_dec(raw_n)) if raw_n is not None else (qty * mult * px)
        side = str(item.get("side") or item.get("action") or "").strip().lower()
        r_dir = str(item.get("direction") or ("SHORT" if side == "sell" else "LONG")).strip().upper()
        signed = -notional if (r_dir == "SHORT" or side == "sell") else notional
        res_gross += notional
        res_signed_net += signed
        raw_c = item.get("cash_reserved") if item.get("cash_reserved") is not None else item.get("cash_required")
        if raw_c is not None:
            res_cash += max(Decimal(0), _dec(raw_c))
        elif str(item.get("kind") or "cash_share") == "cash_share" and r_dir == "LONG" and qty > 0 and px > 0:
            raw_u_fee = str(item.get("underlying") or item.get("symbol") or "0000")
            res_cash += _dec(buy_cash_required(raw_u_fee, int(qty), float(px)))
        raw_m = item.get("margin_reserved") if item.get("margin_reserved") is not None else item.get("margin_required")
        if raw_m is not None:
            res_margin += max(Decimal(0), _dec(raw_m))
        raw_u = str(item.get("underlying") or item.get("symbol") or item.get("instrument_id") or "").strip()
        try:
            u_code = normalize_underlying_code(raw_u) if raw_u else ""
        except Exception:
            u_code = raw_u
        if u_code:
            res_underlying_gross[u_code] = res_underlying_gross.get(u_code, Decimal(0)) + notional

    budget_already_folded = "raw_cash" in budget and "underlying_exposures" in budget
    if "gross_used" in budget and budget.get("gross_used") is not None:
        base_gross_used = _dec(budget["gross_used"])
        gross_used = base_gross_used if budget_already_folded else (base_gross_used + res_gross)
    elif isinstance(book, Mapping) and book.get("gross_notional") is not None:
        gross_used = _dec(book["gross_notional"]) + res_gross
    else:
        gross_used = pos_gross + res_gross

    if budget.get("signed_net_used") is not None:
        signed_net_used = _dec(budget["signed_net_used"])
        if not budget_already_folded:
            signed_net_used += res_signed_net
    elif budget.get("net_used") is not None and not pos_list:
        net_mag = abs(_dec(budget["net_used"]))
        signed_net_used = (net_mag if direction.upper() == "LONG" else -net_mag) + res_signed_net
    else:
        signed_net_used = pos_signed_net + res_signed_net

    budget_u_map = budget.get("underlying_exposures")
    existing_u_from_budget = Decimal(0)
    if isinstance(budget_u_map, Mapping) and norm_underlying in budget_u_map and budget_u_map[norm_underlying] is not None:
        existing_u_from_budget = _dec(budget_u_map[norm_underlying])
    pos_u = underlying_gross_by_code.get(norm_underlying, Decimal(0))
    res_u = res_underlying_gross.get(norm_underlying, Decimal(0))
    if budget_already_folded:
        existing_underlying_gross = max(existing_u_from_budget, pos_u + res_u)
    else:
        existing_underlying_gross = max(existing_u_from_budget + res_u, pos_u + res_u)

    equity_raw = budget.get("equity")
    equity = _dec(equity_raw) if equity_raw is not None and _dec(equity_raw) > 0 else None

    explicit_rem = budget.get("max_gross_notional_remaining")
    gross_limit_raw = budget.get("gross_limit")
    if gross_limit_raw is None and budget.get("gross_limit_multiple") is not None and equity is not None:
        gross_limit_raw = _dec(budget["gross_limit_multiple"]) * equity
    gross_limit_dec = _dec(gross_limit_raw) if gross_limit_raw is not None else None
    if explicit_rem is not None:
        remaining_by_gross_limit = max(_dec(explicit_rem) - (Decimal(0) if budget_already_folded else res_gross), Decimal(0))
    elif gross_limit_dec is not None:
        remaining_by_gross_limit = max(gross_limit_dec - gross_used, Decimal(0))
    else:
        remaining_by_gross_limit = Decimal(0)

    max_leverage_raw = budget.get("max_leverage")
    if max_leverage_raw is not None and equity is not None and _dec(max_leverage_raw) > 0:
        leverage_limit_dec = _dec(max_leverage_raw) * equity
        remaining_by_leverage: Decimal | None = max(leverage_limit_dec - gross_used, Decimal(0))
    else:
        leverage_limit_dec = None
        remaining_by_leverage = None

    net_limit_raw = budget.get("net_limit")
    if net_limit_raw is None and budget.get("net_limit_multiple") is not None and equity is not None:
        net_limit_raw = _dec(budget["net_limit_multiple"]) * equity
    if net_limit_raw is not None:
        net_limit_dec = _dec(net_limit_raw)
        if direction.upper() == "LONG":
            remaining_by_net: Decimal | None = max(net_limit_dec - signed_net_used, Decimal(0))
        elif direction.upper() == "SHORT":
            remaining_by_net = max(net_limit_dec + signed_net_used, Decimal(0))
        else:
            remaining_by_net = max(net_limit_dec - abs(signed_net_used), Decimal(0))
    else:
        net_limit_dec = None
        remaining_by_net = None

    single_name_raw = budget.get("max_single_name_notional")
    max_single_name_weight = (
        _dec(budget["max_single_name_weight"])
        if budget.get("max_single_name_weight") is not None
        else None
    )
    if single_name_raw is None and max_single_name_weight is not None and equity is not None:
        single_name_raw = max_single_name_weight * equity
    if single_name_raw is not None:
        single_name_limit_dec = _dec(single_name_raw)
        if max_single_name_weight is None and equity is not None and equity > 0:
            max_single_name_weight = single_name_limit_dec / equity
        remaining_by_single_name: Decimal | None = max(single_name_limit_dec - existing_underlying_gross, Decimal(0))
    else:
        single_name_limit_dec = None
        remaining_by_single_name = None

    scalar_cash_res = _dec(budget.get("cash_reserved") or budget.get("reserved_cash") or 0)
    cash_reserved = max(scalar_cash_res, res_cash)
    if cash_reserved > 0 and active_res_count == 0:
        active_res_count = 1

    has_cash_field = any(k in budget and budget.get(k) is not None for k in ("available_cash", "available_buying_power", "raw_cash"))
    if has_cash_field:
        avail_field = budget.get("available_buying_power")
        if avail_field is None:
            avail_field = budget.get("available_cash")
        avail_dec = max(_dec(avail_field if avail_field is not None else budget.get("raw_cash") or 0), Decimal(0))
        raw_cash_dec = _dec(budget["raw_cash"]) if budget.get("raw_cash") is not None else avail_dec
        free_margin_dec = _dec(budget["free_margin"]) if budget.get("free_margin") is not None else raw_cash_dec

        reserve_req = Decimal(0)
        if budget.get("cash_reserve_required") is not None:
            reserve_req = max(reserve_req, _dec(budget["cash_reserve_required"]))
        if budget.get("min_cash_reserve") is not None:
            reserve_req = max(reserve_req, _dec(budget["min_cash_reserve"]))
        if budget.get("min_cash_reserve_pct") is not None:
            base_eq = equity if equity is not None else raw_cash_dec
            reserve_req = max(reserve_req, _dec(budget["min_cash_reserve_pct"]) * base_eq)

        if "raw_cash" in budget:
            unreserved_cash = max(min(raw_cash_dec - cash_reserved, free_margin_dec - res_margin), Decimal(0))
            spendable_cash: Decimal | None = max(min(avail_dec, unreserved_cash - reserve_req), Decimal(0))
        else:
            unreserved_cash = max(avail_dec - cash_reserved - res_margin, Decimal(0))
            spendable_cash = max(unreserved_cash - reserve_req, Decimal(0))
    else:
        raw_cash_dec = None
        unreserved_cash = None
        reserve_req = Decimal(0)
        spendable_cash = None

    effective_remaining_notional = remaining_by_gross_limit
    for opt_rem in (remaining_by_leverage, remaining_by_net, remaining_by_single_name):
        if opt_rem is not None:
            effective_remaining_notional = min(effective_remaining_notional, opt_rem)

    gross_mult = (
        _dec(budget["gross_limit_multiple"])
        if budget.get("gross_limit_multiple") is not None
        else ((gross_limit_dec / equity) if (gross_limit_dec is not None and equity is not None and equity > 0) else None)
    )
    net_mult = (
        _dec(budget["net_limit_multiple"])
        if budget.get("net_limit_multiple") is not None
        else ((net_limit_dec / equity) if (net_limit_dec is not None and equity is not None and equity > 0) else None)
    )
    lev_mult = _dec(max_leverage_raw) if max_leverage_raw is not None else None

    return {
        "underlying": norm_underlying,
        "direction": direction.upper(),
        "equity": equity,
        "raw_cash": raw_cash_dec,
        "unreserved_cash": unreserved_cash,
        "cash_reserved": cash_reserved,
        "margin_reserved": res_margin,
        "cash_reserve_required": reserve_req,
        "spendable_cash": spendable_cash,
        "gross_limit": gross_limit_dec,
        "gross_limit_multiple": gross_mult,
        "gross_used": gross_used,
        "leverage_limit": leverage_limit_dec,
        "max_leverage": lev_mult,
        "net_limit": net_limit_dec,
        "net_limit_multiple": net_mult,
        "signed_net_used": signed_net_used,
        "single_name_limit": single_name_limit_dec,
        "max_single_name_weight": max_single_name_weight,
        "existing_underlying_gross": existing_underlying_gross,
        "underlying_same_dir_notional": underlying_same_dir_notional,
        "remaining_by_gross_limit": remaining_by_gross_limit,
        "remaining_by_leverage": remaining_by_leverage,
        "remaining_by_net": remaining_by_net,
        "remaining_by_single_name": remaining_by_single_name,
        "effective_remaining_notional": max(effective_remaining_notional, Decimal(0)),
        "active_reservations_count": active_res_count,
        "risk_policy_id": budget.get("risk_policy_id"),
        "risk_policy_version": budget.get("risk_policy_version"),
        "risk_policy_digest": budget.get("risk_policy_digest"),
    }


def remaining_gross_notional(budget: Mapping[str, Any]) -> Decimal:
    """Gross notional still spendable; a missing limit leaves nothing."""
    explicit = budget.get("max_gross_notional_remaining")
    if explicit is not None:
        return max(_dec(explicit), Decimal(0))
    limit = _effective_gross_limit(budget)
    if limit <= 0:
        return Decimal(0)
    return max(limit - _dec(budget.get("gross_used") or 0), Decimal(0))


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


def budget_unavailable_no_trade(
    view: Mapping[str, Any], missing: str, detail: str = ""
) -> dict[str, Any]:
    """NoTrade for an expression input that could not be read (fail closed).

    ``detail`` names the missing input so the event never reports a limit that
    did not actually bind.
    """
    text = f"budget_unavailable: {missing}"
    if detail:
        text = f"{text} ({detail})"
    return _no_trade(view, [], "candidates_unavailable", text)


def _candidate_risk_unit_caps(
    candidate: Mapping[str, Any],
    sizing_state: Mapping[str, Any],
) -> dict[str, int | None]:
    """Compute per-constraint unit caps for ``candidate`` under ``sizing_state``."""
    per_unit = _unit_notional(candidate)
    if per_unit <= 0:
        return {
            "candidate_max_units_by_risk": 0,
            "gross_limit": 0,
            "max_leverage": 0,
            "net_limit": 0,
            "max_single_name_weight": 0,
            "available_cash_pre_fee": 0,
            "initial_margin": 0,
            "effective_risk_max_units": 0,
        }
    u_cand = max(int(candidate.get("max_units_by_risk") or 0), 0)
    u_gross = max(int(sizing_state["remaining_by_gross_limit"] // per_unit), 0)
    u_lev = (
        max(int(sizing_state["remaining_by_leverage"] // per_unit), 0)
        if sizing_state.get("remaining_by_leverage") is not None
        else None
    )
    u_net = (
        max(int(sizing_state["remaining_by_net"] // per_unit), 0)
        if sizing_state.get("remaining_by_net") is not None
        else None
    )
    u_single = (
        max(int(sizing_state["remaining_by_single_name"] // per_unit), 0)
        if sizing_state.get("remaining_by_single_name") is not None
        else None
    )
    spendable = sizing_state.get("spendable_cash")
    is_cash_long = candidate.get("kind") == "cash_share" and sizing_state.get("direction") == "LONG"
    if is_cash_long and spendable is not None:
        u_cash_pre_fee: int | None = max(int(spendable // per_unit), 0)
    else:
        u_cash_pre_fee = None

    raw_margin = candidate.get("initial_margin_per_unit")
    if not is_cash_long and raw_margin is not None and _dec(raw_margin) > 0 and spendable is not None:
        u_margin: int | None = max(int(spendable // _dec(raw_margin)), 0)
    else:
        u_margin = None

    active_caps = [
        u
        for u in (u_cand, u_gross, u_lev, u_net, u_single, u_cash_pre_fee, u_margin)
        if u is not None
    ]
    eff_max = min(active_caps) if active_caps else 0
    return {
        "candidate_max_units_by_risk": u_cand,
        "gross_limit": u_gross,
        "max_leverage": u_lev,
        "net_limit": u_net,
        "max_single_name_weight": u_single,
        "available_cash_pre_fee": u_cash_pre_fee,
        "initial_margin": u_margin,
        "effective_risk_max_units": eff_max,
    }


def _satisfies_post_fee_equity_limits(
    candidate: Mapping[str, Any],
    units: int,
    sizing_state: Mapping[str, Any],
) -> bool:
    """Verify post-fee exposure and single-name weights against ``equity_after = equity - fee``."""
    if units <= 0:
        return True
    equity = sizing_state.get("equity")
    if equity is None or equity <= 0:
        return True
    notional = _unit_notional(candidate) * units
    if candidate.get("kind") == "cash_share" and sizing_state.get("direction") == "LONG":
        req_cash = _cash_buy_required(candidate, units)
        fee = max(req_cash - notional, Decimal(0))
    else:
        fee = (
            _dec(candidate.get("fee_per_unit_per_side") or 0) * units
            + notional * _dec(candidate.get("tax_rate_per_side") or 0)
        )
    equity_after = equity - fee
    if equity_after <= 0:
        return False
    eps = Decimal("1e-9")
    max_single_w = sizing_state.get("max_single_name_weight")
    if max_single_w is not None:
        if (sizing_state["existing_underlying_gross"] + notional) > max_single_w * equity_after + eps:
            return False
    gross_mult = sizing_state.get("gross_limit_multiple")
    if gross_mult is not None:
        if (sizing_state["gross_used"] + notional) > gross_mult * equity_after + eps:
            return False
    lev_mult = sizing_state.get("max_leverage")
    if lev_mult is not None:
        if (sizing_state["gross_used"] + notional) > lev_mult * equity_after + eps:
            return False
    net_mult = sizing_state.get("net_limit_multiple")
    if net_mult is not None:
        signed_delta = notional if sizing_state.get("direction") == "LONG" else -notional
        if abs(sizing_state["signed_net_used"] + signed_delta) > net_mult * equity_after + eps:
            return False
    return True


def _binding_constraint(
    direction: str,
    candidates: Sequence[Mapping[str, Any]],
    budget: Mapping[str, Any],
    book: Mapping[str, Any] | None = None,
    underlying: str | None = None,
) -> str:
    side = direction.lower()
    capable = [c for c in candidates if side in (c.get("side_capability") or [])]
    if not capable:
        return "side_not_supported"
    best = min(capable, key=lambda c: (_unit_notional(c), str(c.get("instrument_id"))))
    if best.get("price") is None or _dec(best["price"]) <= 0:
        return "price_unavailable"
    min_unit = _unit_notional(best)
    u_code = underlying or str(best.get("underlying") or str(best.get("instrument_id") or "").split(":", 1)[-1])
    state = _resolve_sizing_state(u_code, direction, candidates, book, budget)

    failing_notional_limits: list[tuple[Decimal, int, str]] = []
    if state["remaining_by_single_name"] is not None and state["remaining_by_single_name"] < min_unit:
        failing_notional_limits.append((state["remaining_by_single_name"], 0, "single_name_limit"))
    if state["remaining_by_gross_limit"] < min_unit:
        failing_notional_limits.append((state["remaining_by_gross_limit"], 1, "gross_limit"))
    if (
        state["remaining_by_leverage"] is not None
        and state["remaining_by_leverage"] < min_unit
        and state["remaining_by_leverage"] < state["remaining_by_gross_limit"]
    ):
        failing_notional_limits.append((state["remaining_by_leverage"], 2, "leverage_limit"))
    if state["remaining_by_net"] is not None and state["remaining_by_net"] < min_unit:
        failing_notional_limits.append((state["remaining_by_net"], 3, "net_limit"))
    if failing_notional_limits:
        failing_notional_limits.sort(key=lambda item: (item[0], item[1]))
        return failing_notional_limits[0][2]

    spendable = state["spendable_cash"]
    unreserved = state["unreserved_cash"]
    raw_cash = state["raw_cash"]
    if best.get("kind") == "cash_share" and direction.upper() == "LONG":
        one_req = _cash_buy_required(best, 1)
        if spendable is not None and one_req > spendable:
            if unreserved is not None and one_req <= unreserved and state["cash_reserve_required"] > 0:
                return "cash_reserve"
            if raw_cash is not None and one_req <= raw_cash and state["cash_reserved"] > 0:
                return "cash_reserved"
            if min_unit <= spendable < one_req:
                return "insufficient_cash_for_commission"
            return "insufficient_cash"
    else:
        margin = best.get("initial_margin_per_unit")
        if margin is not None:
            m_dec = _dec(margin)
            if spendable is None or m_dec > spendable:
                if unreserved is not None and m_dec <= unreserved and state["cash_reserve_required"] > 0:
                    return "cash_reserve"
                if raw_cash is not None and m_dec <= raw_cash and (state["cash_reserved"] > 0 or state["margin_reserved"] > 0):
                    return "cash_reserved"
                return "insufficient_initial_margin"

    if not _satisfies_post_fee_equity_limits(best, 1, state):
        if state["max_single_name_weight"] is not None:
            return "single_name_limit"
        if state["net_limit_multiple"] is not None:
            return "net_limit"
        if state["max_leverage"] is not None:
            return "leverage_limit"
        return "gross_limit"

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


def feasible_candidates(
    direction: str, candidates: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Candidates that can express ``direction`` within ``max_units_by_risk``."""
    side_key = direction.lower()
    return [
        c
        for c in candidates
        if side_key in (c.get("side_capability") or [])
        and int(c.get("max_units_by_risk") or 0) >= 1
        and c.get("price") is not None
        and _unit_notional(c) > 0
    ]


def target_notional(
    direction: str,
    strength: Any,
    feasible: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> Decimal:
    """Dev plan section 4.4 step 4; ``feasible`` must be non-empty."""
    smallest = min(_unit_notional(c) for c in feasible)
    if strength is None:
        return smallest
    if direction == "LONG" and _dec(strength) < _dec(params["lever_min_strength"]):
        cash = next((c for c in feasible if str(c["instrument_id"]).startswith("eq:")), None)
        return _dec(cash["price"]) * _dec(cash["unit"]) if cash is not None else smallest
    return (
        _dec(strength)
        * _dec(params["notional_fraction_at_full_strength"])
        * remaining_gross_notional(budget)
    )


def clamped_units(candidate: Mapping[str, Any], target: Decimal) -> int:
    """``clamp(round(N* / (unit * price)), 1, max_units_by_risk)``."""
    raw = (target / _unit_notional(candidate)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(min(max(raw, Decimal(1)), Decimal(int(candidate["max_units_by_risk"]))))


def _cash_buy_required(candidate: Mapping[str, Any], units: int) -> Decimal:
    """Cash a cash-share buy needs: notional plus commission (single shared source)."""
    return _dec(
        buy_cash_required(
            candidate.get("underlying") or str(candidate["instrument_id"]).split(":", 1)[-1],
            units * int(candidate.get("unit") or 1),
            float(candidate["price"]),
        )
    )


def fit_cash_share_buy(
    candidate: Mapping[str, Any],
    units: int,
    target: Decimal,
    budget: Mapping[str, Any],
    *,
    floor_one: bool = False,
    sizing_state: Mapping[str, Any] | None = None,
) -> int:
    """Shrink a cash-share buy until notional plus commission and post-fee limits fit.

    The cap is ``min(target, spendable_cash)`` using ``taiwan_cash_costs`` (the
    same cost the ledger and paper risk gate charge). ``floor_one`` keeps one unit
    when the target alone is below its cost but cash and risk limits still afford it.
    Returns 0 when even one unit plus commission exceeds spendable cash or risk limits.
    """
    state = (
        sizing_state
        if sizing_state is not None
        else _resolve_sizing_state(
            str(candidate.get("underlying") or str(candidate.get("instrument_id") or "").split(":", 1)[-1]),
            "LONG",
            [candidate],
            None,
            budget,
        )
    )
    cash_cap = state.get("spendable_cash")
    one = _cash_buy_required(candidate, 1)
    if cash_cap is not None and one > cash_cap:
        return 0
    risk_caps = _candidate_risk_unit_caps(candidate, state)
    max_by_risk = int(risk_caps["effective_risk_max_units"] or 0)
    if max_by_risk < 1:
        return 0
    cap = target if cash_cap is None else min(target, cash_cap)
    units = min(int(units), max_by_risk)
    while units > 1 and (
        _cash_buy_required(candidate, units) > cap
        or not _satisfies_post_fee_equity_limits(candidate, units, state)
    ):
        units -= 1
    if units < 1:
        return 0
    if not _satisfies_post_fee_equity_limits(candidate, units, state):
        return 0
    if _cash_buy_required(candidate, units) > cap:
        if not floor_one:
            return 0
        if cash_cap is not None and _cash_buy_required(candidate, units) > cash_cap:
            return 0
        if _unit_notional(candidate) * units > state["effective_remaining_notional"]:
            return 0
    return units


def round_trip_cost_per_unit(candidate: Mapping[str, Any]) -> Decimal:
    return _dec(candidate.get("fee_per_unit_per_side") or 0) * 2 + _unit_notional(
        candidate
    ) * _dec(candidate.get("tax_rate_per_side") or 0) * 2


def resolved_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    return _resolved_parameters(parameters)


def _close_held(view, candidates, params, book, underlying, direction):
    """Close the first held leg of ``underlying`` because the view turned flat."""
    by_id = {str(c.get("instrument_id")): c for c in candidates}
    position = sorted(
        _open_positions(book, underlying), key=lambda p: str(p.get("instrument_id"))
    )[0]
    instrument_id = str(position.get("instrument_id"))
    held_direction = str(position.get("direction") or "").upper()
    held_units = int(position["units"])
    candidate = by_id.get(instrument_id)
    if candidate is None or _unit_notional(candidate) <= 0:
        return _no_trade(
            view, candidates, "no_feasible_instrument", "close_instrument_not_in_candidates"
        )
    return _chosen(
        view,
        candidates,
        params,
        instrument_id=instrument_id,
        side="sell" if held_direction == "LONG" else "buy",
        units=held_units,
        reason_code="close_leg",
        reason_text=f"close open {held_direction.lower()} {instrument_id} on a flat view",
        target_notional=_unit_notional(candidate) * held_units,
    )


def _trim_same_direction(view, candidates, params, budget, book, underlying, direction, position):
    """Trim a same-direction holding when the view's target fell below it.

    Same rule as target_position_v1: reduce only when ``exposure - target``
    exceeds the band ``max(largest held unit notional, target * fraction)``,
    flooring the proportional reduction and never closing more than held.
    The target is anchored on the absolute gross limit so a held position does
    not shrink its own target.  Returns None when no trim applies.
    """
    strength = view.get("strength")
    if strength is None:
        return None
    limit = _effective_gross_limit(budget)
    if limit <= 0:
        explicit = budget.get("max_gross_notional_remaining")
        if explicit is None:
            return None
        limit = _dec(explicit) + _dec(budget.get("gross_used") or 0)
    by_id = {str(c.get("instrument_id")): c for c in candidates}
    exposure = Decimal(0)
    band = Decimal(0)
    held_total = 0
    for p in _open_positions(book, underlying):
        if str(p.get("direction") or "").upper() != direction:
            continue
        candidate = by_id.get(str(p.get("instrument_id")))
        if candidate is None or _unit_notional(candidate) <= 0:
            return None
        exposure += _unit_notional(candidate) * int(p["units"])
        band = max(band, _unit_notional(candidate))
        held_total += int(p["units"])
    fraction = _dec(params.get("rebalance_band_fraction", NOTIONAL_REBALANCE_BAND_FRACTION))
    target = _dec(strength) * _dec(params["notional_fraction_at_full_strength"]) * _dec(limit)
    equity_raw = budget.get("equity")
    equity = _dec(equity_raw) if equity_raw is not None and _dec(equity_raw) > 0 else None
    if budget.get("max_single_name_notional") is not None:
        target = min(target, _dec(budget["max_single_name_notional"]))
    elif budget.get("max_single_name_weight") is not None and equity is not None:
        target = min(target, _dec(budget["max_single_name_weight"]) * equity)
    if budget.get("net_limit") is not None:
        target = min(target, _dec(budget["net_limit"]))
    elif budget.get("net_limit_multiple") is not None and equity is not None:
        target = min(target, _dec(budget["net_limit_multiple"]) * equity)
    if not (target.is_finite() and fraction.is_finite()) or target < 0 or not 0 <= fraction <= 1:
        return None
    band = max(band, target * fraction)
    if exposure <= 0 or exposure - target <= band:
        return None
    instrument_id = str(position.get("instrument_id"))
    held = int(position["units"])
    units = min(held, int(Decimal(held_total) * (exposure - target) / exposure))
    if units < 1:
        return None
    record = _chosen(
        view,
        candidates,
        params,
        instrument_id=instrument_id,
        side="sell" if direction == "LONG" else "buy",
        units=units,
        reason_code="close_leg",
        reason_text=(
            f"trim {direction.lower()} {instrument_id} to weaker view strength={strength} "
            f"target_notional={float(target):.2f}"
        ),
        target_notional=target,
    )
    record["rebalance_band"] = float(band)
    return record


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
        if not _open_positions(book, underlying):
            return _no_trade(view, candidates, "flat_view", "view direction is FLAT")
        # A flat view closes whatever is held on this underlying (one leg per
        # call, the same as the opposite-view close; the next bar continues).
        return _close_held(view, candidates, params, book, underlying, direction)
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
        trim = _trim_same_direction(
            view, candidates, params, budget, book, underlying, direction, position
        )
        if trim is not None:
            return trim
        if not params["allow_pyramiding"]:
            return _no_trade(
                view,
                candidates,
                "already_at_target",
                f"open {held_direction.lower()} position in {position.get('instrument_id')}",
            )

    # Step 3: feasible set.
    state = _resolve_sizing_state(underlying, direction, candidates, book, budget)
    feasible = feasible_candidates(direction, candidates)
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
            _binding_constraint(direction, candidates, budget, book, underlying),
        )

    # Step 4: target notional.
    target = target_notional(direction, strength, feasible, params, budget)
    desired_delta = (
        min(target, state["remaining_by_single_name"])
        if state["remaining_by_single_name"] is not None
        else target
    )

    # Step 5: size and score every feasible candidate.
    scored: list[tuple[Decimal, Decimal, str, Mapping[str, Any], int]] = []
    for c in feasible:
        per_unit = _unit_notional(c)
        risk_caps = _candidate_risk_unit_caps(c, state)
        eff_max_units = int(risk_caps["effective_risk_max_units"] or 0)
        if eff_max_units < 1:
            continue
        units = min(clamped_units(c, desired_delta), eff_max_units)
        if direction == "LONG" and c.get("kind") == "cash_share":
            units = fit_cash_share_buy(
                c,
                units,
                target,
                budget,
                floor_one=True,
                sizing_state=state,
            )
            if units < 1:
                continue
        else:
            while units > 0 and not _satisfies_post_fee_equity_limits(c, units, state):
                units -= 1
            if units < 1:
                continue
        score = abs(units * per_unit - desired_delta) + units * round_trip_cost_per_unit(c)
        scored.append((score, per_unit, str(c["instrument_id"]), c, units))
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    if not scored:
        detail = _binding_constraint(direction, candidates, budget, book, underlying)
        if detail == "max_units_by_risk":
            detail = "insufficient_cash_for_commission"
        return _no_trade(
            view, candidates, "no_feasible_instrument", detail
        )
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


TARGET_POLICY_KIND = "target_position_v1"


def allocate_targets(views, budget, parameters):
    """Allocate one bar from the effective gross/leverage/net/per-name limits, before any order is sized.

    The snapshot is replay input. Decimal summation in canonical key order makes
    it independent of both market enumeration and the order fills later arrive.
    """
    limit = _effective_gross_limit(budget)
    fraction = _dec(parameters.get("notional_fraction_at_full_strength", 1))
    if not limit.is_finite() or limit <= 0 or not fraction.is_finite() or not 0 < fraction <= 1:
        raise ValueError("allocation_budget_unavailable")
    equity_raw = budget.get("equity")
    equity = _dec(equity_raw) if equity_raw is not None and _dec(equity_raw) > 0 else None
    per_name_cap = (
        _dec(budget["max_single_name_notional"])
        if budget.get("max_single_name_notional") is not None
        else (
            _dec(budget["max_single_name_weight"]) * equity
            if budget.get("max_single_name_weight") is not None and equity is not None
            else None
        )
    )
    net_cap = (
        _dec(budget["net_limit"])
        if budget.get("net_limit") is not None
        else (
            _dec(budget["net_limit_multiple"]) * equity
            if budget.get("net_limit_multiple") is not None and equity is not None
            else None
        )
    )
    raw = {}
    for symbol, view in sorted(views.items()):
        strength = _dec(1 if view.get("strength") is None else view["strength"])
        if not strength.is_finite() or not 0 <= strength <= 1:
            raise ValueError("allocation_strength_invalid")
        raw[symbol] = (strength * fraction * limit
                       if view["direction"] in {"LONG", "SHORT"} else Decimal(0))
    total = sum(raw.values(), Decimal(0))
    scale = min(Decimal(1), limit / total) if total else Decimal(1)
    if net_cap is not None and net_cap > 0 and total > 0:
        signed_total = abs(
            sum(
                (raw[s] if views[s]["direction"] == "LONG" else -raw[s])
                for s in sorted(raw)
                if views[s]["direction"] in {"LONG", "SHORT"}
            )
        )
        if signed_total * scale > net_cap:
            scale = min(scale, net_cap / signed_total)
    targets = {}
    for s, n in raw.items():
        t_val = n * scale
        if per_name_cap is not None and t_val > per_name_cap:
            t_val = per_name_cap
        targets[s] = float(t_val)
    return {"policy_kind": TARGET_POLICY_KIND, "gross_budget": float(limit),
            "raw_targets": {s: float(n) for s, n in raw.items()},
            "scale": float(scale), "targets": targets,
            "views": _json_safe(views)}


def decide_target_position(view, candidates, book, budget, parameters):
    """Reconcile a bar-allocated target with trusted holdings; close before reverse.

    A multi-instrument reduction is split proportionally over held legs. The
    producer supplies reconcile_instrument_id for each leg, with the same full
    book and allocation snapshot, so replay uses exactly the production inputs.
    """
    params = {**_resolved_parameters(parameters)}
    params.setdefault("rebalance_band_fraction", 0.2)
    target = _dec(view.get("target_notional", -1))
    fraction = _dec(params["rebalance_band_fraction"])
    underlying = str(view.get("underlying") or "")
    positions = _open_positions(book, underlying)
    by_id = {str(c.get("instrument_id")): c for c in candidates}
    band = Decimal(0)

    def finish(record):
        return {**record, "policy_kind": TARGET_POLICY_KIND,
                "target_notional": float(target) if target.is_finite() and target >= 0 else None,
                "rebalance_band": float(band),
                "policy_version": POLICY_VERSION,
                "policy_parameters_digest": policy_parameters_digest(params)}

    def no(reason, detail):
        return finish(_no_trade(view, candidates, reason, detail))

    if not target.is_finite() or target < 0 or not fraction.is_finite() or not 0 <= fraction <= 1:
        return no("candidates_unavailable", "bar_allocation_unavailable")
    for field in ("gross_limit", "gross_used", "available_cash"):
        value = budget.get(field)
        if value is None or not _dec(value).is_finite() or _dec(value) < 0:
            return no("candidates_unavailable", "budget_unavailable: " + field)
    if _dec(budget["gross_limit"]) <= 0:
        return no("candidates_unavailable", "budget_unavailable: gross_limit")
    direction = str(view.get("direction", "FLAT")).upper()
    state = _resolve_sizing_state(underlying, direction, candidates, book, budget)
    effective_target = (
        min(target, state["single_name_limit"])
        if state["single_name_limit"] is not None
        else target
    )
    exposure = Decimal(0)
    for p in positions:
        candidate = by_id.get(str(p["instrument_id"]))
        if candidate is None or not _unit_notional(candidate).is_finite() or _unit_notional(candidate) <= 0:
            return no("candidates_unavailable", "held_instrument_mark_unavailable")
        exposure += _unit_notional(candidate) * int(p["units"])
        band = max(band, _unit_notional(candidate))
    band = max(band, effective_target * fraction)
    opposite = any(p["direction"] != direction for p in positions)
    liquidate = direction == "FLAT" or effective_target == 0 or opposite
    if positions and (liquidate or exposure - effective_target > band):
        selector = view.get("reconcile_instrument_id")
        selected = [p for p in positions if not selector or p["instrument_id"] == selector]
        if not selected:
            return no("already_at_target", "held leg no longer present")
        p = sorted(selected, key=lambda p: p["instrument_id"])[0]
        candidate = by_id[str(p["instrument_id"])]
        held = int(p["units"])
        # Floor proportional reductions; never cross zero or close more than held.
        units = held if liquidate else int(Decimal(held) * (exposure - effective_target) / exposure)
        if units < 1:
            return no("already_at_target", "reduction below one held unit")
        return finish(_chosen(view, candidates, params,
            instrument_id=str(p["instrument_id"]),
            side="sell" if p["direction"] == "LONG" else "buy",
            units=min(held, units), reason_code="close_leg",
            reason_text="target position liquidation" if liquidate else "target position reduction",
            target_notional=target))
    if direction == "FLAT" or effective_target == 0:
        return no("flat_view", "zero target with no holdings")
    delta = effective_target - exposure
    if positions and delta <= band:
        return no("already_at_target", "target within rebalance band")
    if delta <= 0:
        return no("already_at_target", "no additional exposure required")
    feasible = feasible_candidates(direction, candidates)
    scored = []
    for c in feasible:
        unit = _unit_notional(c)
        risk_caps = _candidate_risk_unit_caps(c, state)
        eff_max_units = int(risk_caps["effective_risk_max_units"] or 0)
        if eff_max_units < 1:
            continue
        units = min(clamped_units(c, delta), int(delta / unit), eff_max_units)
        if units >= 1 and direction == "LONG" and c.get("kind") == "cash_share":
            raw_delta_target = max(target - exposure, Decimal(0))
            units = fit_cash_share_buy(c, units, raw_delta_target, budget, sizing_state=state)
        elif units >= 1:
            while units > 0 and not _satisfies_post_fee_equity_limits(c, units, state):
                units -= 1
        if units < 1:
            continue
        score = abs(units * unit - delta) + units * round_trip_cost_per_unit(c)
        scored.append((score, unit, str(c["instrument_id"]), c, units))
    if not scored:
        return no("no_feasible_instrument", _binding_constraint(direction, candidates, budget, book, underlying))
    _, _, instrument_id, winner, units = min(scored, key=lambda row: row[:3])
    reason = "long_cash" if instrument_id.startswith("eq:") else (
        "long_lever" if direction == "LONG" else "short_via_future")
    return finish(_chosen(view, candidates, params, instrument_id=instrument_id,
        side="buy" if direction == "LONG" else "sell", units=units,
        reason_code=reason, reason_text="add allocated target position delta",
        target_notional=target))


def evaluate_pre_trade_sizing(
    view: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    book: Mapping[str, Any] | None,
    budget: Mapping[str, Any],
    parameters: Mapping[str, Any] | None = None,
    *,
    policy_kind: str = POLICY_KIND,
) -> dict[str, Any]:
    """Evaluate pre-trade sizing and return complete policy/limit/cash/concentration breakdown."""
    params = _resolved_parameters(parameters)
    direction = str(view.get("direction") or "FLAT").upper()
    underlying = str(view.get("underlying") or "")
    state = _resolve_sizing_state(underlying, direction, candidates, book, budget)
    decision = decide_for_policy(policy_kind, view, candidates, book, budget, parameters)

    by_id = {str(c.get("instrument_id")): c for c in candidates}
    chosen_id = decision.get("instrument_id") if not is_no_trade(decision) else None
    winner = by_id.get(str(chosen_id)) if chosen_id else None
    if winner is None:
        side_key = direction.lower()
        capable = [c for c in candidates if side_key in (c.get("side_capability") or [])]
        if capable:
            winner = min(capable, key=lambda c: (_unit_notional(c), str(c.get("instrument_id"))))
        elif candidates:
            winner = candidates[0]

    feasible = feasible_candidates(direction, candidates)
    if decision.get("target_notional") is not None:
        raw_target = _dec(decision["target_notional"])
    elif view.get("target_notional") is not None:
        raw_target = _dec(view["target_notional"])
    elif feasible and direction in ("LONG", "SHORT"):
        raw_target = target_notional(direction, view.get("strength"), feasible, params, budget)
    elif direction in ("LONG", "SHORT") and view.get("strength") is not None:
        raw_target = (
            _dec(view["strength"])
            * _dec(params["notional_fraction_at_full_strength"])
            * remaining_gross_notional(budget)
        )
    else:
        raw_target = Decimal(0)

    effective_target = (
        min(raw_target, state["single_name_limit"])
        if state["single_name_limit"] is not None and raw_target > 0
        else raw_target
    )
    if policy_kind == TARGET_POLICY_KIND or (state["underlying_same_dir_notional"] > 0 and params.get("allow_pyramiding")):
        desired_delta = max(effective_target - state["underlying_same_dir_notional"], Decimal(0))
    else:
        desired_delta = effective_target

    permitted_units = int(decision.get("units") or 0) if not is_no_trade(decision) else 0
    is_close_leg = decision.get("reason_code") == "close_leg"

    price = float(winner["price"]) if (winner is not None and winner.get("price") is not None) else 0.0
    per_unit = _unit_notional(winner) if winner is not None else Decimal(0)

    constraint_unit_caps: dict[str, int | None] = {}
    if winner is not None and per_unit > 0:
        risk_caps = _candidate_risk_unit_caps(winner, state)
        u_target = (
            max(int((desired_delta / per_unit).quantize(Decimal(1), rounding=ROUND_HALF_UP)), 0)
            if policy_kind == POLICY_KIND
            else max(int(desired_delta // per_unit), 0)
        )
        u_raw_cash = (
            max(int(state["raw_cash"] // per_unit), 0)
            if state["raw_cash"] is not None and winner.get("kind") == "cash_share"
            else None
        )
        u_unreserved_cash = (
            max(int(state["unreserved_cash"] // per_unit), 0)
            if state["unreserved_cash"] is not None and winner.get("kind") == "cash_share"
            else None
        )
        u_cash_after_fees: int | None = None
        if winner.get("kind") == "cash_share" and direction == "LONG" and state["spendable_cash"] is not None:
            start_u = int(state["spendable_cash"] // per_unit)
            while start_u > 0 and _cash_buy_required(winner, start_u) > state["spendable_cash"]:
                start_u -= 1
            u_cash_after_fees = max(start_u, 0)
        constraint_unit_caps = {
            "target_notional": u_target,
            "gross_limit": risk_caps["gross_limit"],
            "max_leverage": risk_caps["max_leverage"],
            "net_limit": risk_caps["net_limit"],
            "max_single_name_weight": risk_caps["max_single_name_weight"],
            "raw_cash_pre_fee": u_raw_cash,
            "unreserved_cash_pre_fee": u_unreserved_cash,
            "spendable_cash_pre_fee": risk_caps["available_cash_pre_fee"],
            "available_cash_after_fees": u_cash_after_fees,
            "initial_margin": risk_caps["initial_margin"],
        }

    # Determine binding limit
    if is_close_leg:
        binding_limit = "existing_holding_close_leg"
    elif is_no_trade(decision):
        if decision.get("reason_code") == "already_at_target":
            binding_limit = "already_at_target"
        elif decision.get("reason_code") == "flat_view":
            binding_limit = "flat_view"
        else:
            binding_limit = str(decision.get("detail") or decision.get("reason_code") or "no_feasible_instrument")
    else:
        binding_limit = "target_notional"
        if winner is not None and per_unit > 0:
            u_t = constraint_unit_caps.get("target_notional")
            u_single = constraint_unit_caps.get("max_single_name_weight")
            u_net = constraint_unit_caps.get("net_limit")
            u_lev = constraint_unit_caps.get("max_leverage")
            u_gross = constraint_unit_caps.get("gross_limit")
            u_cash_fee = constraint_unit_caps.get("available_cash_after_fees")
            u_spend_pre = constraint_unit_caps.get("spendable_cash_pre_fee")
            u_unres_pre = constraint_unit_caps.get("unreserved_cash_pre_fee")
            u_raw_pre = constraint_unit_caps.get("raw_cash_pre_fee")
            u_margin = constraint_unit_caps.get("initial_margin")

            # Check if a specific policy limit or cash constraint capped permitted_units below u_target
            candidates_binding: list[tuple[int, int, str]] = []
            if u_single is not None and permitted_units <= u_single and (u_t is None or u_single <= u_t):
                candidates_binding.append((permitted_units, 0, "max_single_name_weight"))
            elif (
                state["max_single_name_weight"] is not None
                and not _satisfies_post_fee_equity_limits(winner, permitted_units + 1, state)
                and u_single is not None
                and permitted_units + 1 >= u_single
            ):
                candidates_binding.append((permitted_units, 0, "max_single_name_weight"))
            if u_cash_fee is not None and permitted_units == u_cash_fee:
                if state["cash_reserve_required"] > 0 and u_unres_pre is not None and u_spend_pre is not None and u_spend_pre < u_unres_pre:
                    candidates_binding.append((u_cash_fee, 1, "cash_reserve"))
                elif state["cash_reserved"] > 0 and u_raw_pre is not None and u_unres_pre is not None and u_unres_pre < u_raw_pre:
                    candidates_binding.append((u_cash_fee, 2, "cash_reserved"))
                else:
                    candidates_binding.append((u_cash_fee, 3, "available_cash_after_fees"))
            if u_margin is not None and permitted_units == u_margin:
                if state["cash_reserve_required"] > 0:
                    candidates_binding.append((u_margin, 1, "cash_reserve"))
                elif state["cash_reserved"] > 0 or state["margin_reserved"] > 0:
                    candidates_binding.append((u_margin, 2, "cash_reserved"))
                else:
                    candidates_binding.append((u_margin, 3, "initial_margin"))
            if u_lev is not None and u_gross is not None and u_lev < u_gross and permitted_units <= u_lev:
                candidates_binding.append((u_lev, 4, "max_leverage"))
            if u_net is not None and (u_gross is None or u_net < u_gross) and permitted_units <= u_net:
                candidates_binding.append((u_net, 5, "net_limit"))
            if u_gross is not None and permitted_units <= u_gross and (u_t is None or u_gross < u_t):
                candidates_binding.append((u_gross, 6, "gross_limit"))
            if u_net is not None and permitted_units <= u_net and (u_t is None or u_net < u_t):
                candidates_binding.append((u_net, 7, "net_limit"))
            if u_lev is not None and permitted_units <= u_lev and (u_t is None or u_lev < u_t):
                candidates_binding.append((u_lev, 8, "max_leverage"))
            if u_t is not None and permitted_units == u_t:
                candidates_binding.append((u_t, 9, "target_notional"))
            if candidates_binding:
                candidates_binding.sort(key=lambda item: (item[0], item[1]))
                binding_limit = candidates_binding[0][2]

    order_notional = per_unit * permitted_units
    if permitted_units > 0 and winner is not None:
        if winner.get("kind") == "cash_share" and decision.get("side") == "buy":
            fee_inclusive_cash = _cash_buy_required(winner, permitted_units)
            fee_amount = max(fee_inclusive_cash - order_notional, Decimal(0))
        elif winner.get("kind") == "cash_share" and decision.get("side") == "sell":
            fee_inclusive_cash = Decimal(0)
            fee_amount = Decimal(0)
        else:
            margin_per = _dec(winner.get("initial_margin_per_unit") or 0)
            fee_amount = (
                _dec(winner.get("fee_per_unit_per_side") or 0) * permitted_units
                + order_notional * _dec(winner.get("tax_rate_per_side") or 0)
            )
            fee_inclusive_cash = (margin_per * permitted_units + fee_amount) if not is_close_leg else fee_amount
    else:
        fee_inclusive_cash = Decimal(0)
        fee_amount = Decimal(0)

    raw_cash_before = state["raw_cash"] if state["raw_cash"] is not None else (state["spendable_cash"] or Decimal(0))
    spendable_before = state["spendable_cash"] if state["spendable_cash"] is not None else raw_cash_before
    equity_before = (
        state["equity"]
        if state["equity"] is not None and state["equity"] > 0
        else (raw_cash_before if raw_cash_before > 0 else _effective_gross_limit(budget))
    )
    remaining_cash = max(raw_cash_before - state["cash_reserved"] - fee_inclusive_cash, Decimal(0))
    remaining_spendable_cash = max(spendable_before - fee_inclusive_cash, Decimal(0))

    if is_close_leg:
        after_trade_underlying_notional = max(state["existing_underlying_gross"] - order_notional, Decimal(0))
        after_trade_gross_notional = max(state["gross_used"] - order_notional, Decimal(0))
        signed_delta = -order_notional if decision.get("side") == "sell" else order_notional
    else:
        after_trade_underlying_notional = state["existing_underlying_gross"] + order_notional
        after_trade_gross_notional = state["gross_used"] + order_notional
        signed_delta = order_notional if direction == "LONG" else -order_notional
    after_trade_signed_net = state["signed_net_used"] + signed_delta

    equity_after = max(equity_before - fee_amount, Decimal("1e-9")) if equity_before > 0 else Decimal(0)
    after_trade_concentration = (
        round(float(after_trade_underlying_notional / equity_after), 6)
        if equity_after > 0
        else 0.0
    )
    after_trade_concentration_of_initial_capital = (
        round(float(after_trade_underlying_notional / equity_before), 6)
        if equity_before > 0
        else 0.0
    )

    return {
        "decision_id": str(view.get("decision_id") or ""),
        "underlying": state["underlying"],
        "direction": direction,
        "strength": float(view["strength"]) if view.get("strength") is not None else None,
        "policy_kind": policy_kind,
        "risk_policy_id": state["risk_policy_id"],
        "risk_policy_version": state["risk_policy_version"],
        "risk_policy_digest": state["risk_policy_digest"],
        "instrument_id": chosen_id,
        "dated_price": price,
        "price_date": winner.get("price_date") if winner is not None else None,
        "capital": round(float(equity_before), 6),
        "available_cash_before": round(float(raw_cash_before), 6),
        "available_buying_power": round(float(spendable_before), 6),
        "cash_reserve_required": round(float(state["cash_reserve_required"]), 6),
        "cash_reserved": round(float(state["cash_reserved"]), 6),
        "target_notional": round(float(raw_target), 6),
        "effective_target_notional": round(float(effective_target), 6),
        "desired_delta_notional": round(float(desired_delta), 6),
        "permitted_units": permitted_units,
        "binding_limit": binding_limit,
        "order_notional": round(float(order_notional), 6),
        "commission_fee": round(float(fee_amount), 6),
        "fee_inclusive_required_cash": round(float(fee_inclusive_cash), 6),
        "remaining_cash": round(float(remaining_cash), 6),
        "remaining_spendable_cash": round(float(remaining_spendable_cash), 6),
        "existing_underlying_exposure": round(float(state["existing_underlying_gross"]), 6),
        "after_trade_underlying_notional": round(float(after_trade_underlying_notional), 6),
        "after_trade_concentration": after_trade_concentration,
        "after_trade_concentration_of_initial_capital": after_trade_concentration_of_initial_capital,
        "after_trade_gross_exposure": (
            round(float(after_trade_gross_notional / equity_after), 6) if equity_after > 0 else 0.0
        ),
        "after_trade_net_exposure": (
            round(float(abs(after_trade_signed_net) / equity_after), 6) if equity_after > 0 else 0.0
        ),
        "active_reservations_count": state["active_reservations_count"],
        "constraint_unit_caps": constraint_unit_caps,
        "decision": decision,
    }


def decide_for_policy(kind, view, candidates, book, budget, parameters):
    if kind == TARGET_POLICY_KIND:
        result = decide_target_position(view, candidates, book, budget, parameters)
        return enforce(result, candidates, view, book)
    if kind != POLICY_KIND:
        return budget_unavailable_no_trade(view, "policy_kind", str(kind))
    return decide_validated(view, candidates, book, budget, parameters)


def target_inputs_unavailable_no_trade(view, missing, detail, parameters):
    """A target decision still records its policy when inputs cannot be read."""
    params = {**_resolved_parameters(parameters)}
    params.setdefault("rebalance_band_fraction", 0.2)
    target = view.get("target_notional")
    return {**budget_unavailable_no_trade(view, missing, detail),
            "policy_kind": TARGET_POLICY_KIND, "policy_version": POLICY_VERSION,
            "policy_parameters_digest": policy_parameters_digest(params),
            "target_notional": target if target is not None and target >= 0 else None,
            "rebalance_band": None}  # no held mark/budget from which to compute it
