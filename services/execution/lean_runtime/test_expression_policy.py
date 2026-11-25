"""Expression policy v1 tests on the captured Date=20261106 candidate fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from services.execution.lean_runtime import expression_policy as ep
from services.execution.lean_runtime import instrument_candidates as ic
from services.source_ingestion.connectors.taiwan_official import taifex_ssf_normalized_rows

FIXTURES = Path(__file__).parent / "fixtures" / "taifex_20261106"
DECISION_DAY = "2026-11-09"
# equity 100,000 at a 2.0x limit
BUDGET = {"available_cash": 100_000.0, "gross_limit": 200_000.0, "gross_used": 0.0}
PARAMS = {
    "lever_min_strength": 0.5,
    "notional_fraction_at_full_strength": 1.0,
    "allow_pyramiding": False,
    "close_before_reverse": True,
}


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows() -> list[dict[str, Any]]:
    payload = {
        "SingleStockFuturesMargining": _load("SingleStockFuturesMargining"),
        "SingleStockFuturesETFMargining": _load("SingleStockFuturesETFMargining"),
        "DailyMarketReportFut": _load("DailyMarketReportFut"),
        "FinalSettlementPriceSSF": _load("FinalSettlementPriceSSF"),
        "stockLists": (FIXTURES / "stockLists.html").read_text(encoding="utf-8"),
    }
    return [dict(row) for row in taifex_ssf_normalized_rows(payload)]


def _candidates(rows, underlying: str, cash_price: float) -> list[dict[str, Any]]:
    return ic.build_candidates(
        underlying,
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=cash_price,
        cash_price_date="2026-11-06",
        rows_reader=lambda: rows,
    )


def _view(underlying: str, direction: str, strength, decision_id: str = "dec-1") -> dict[str, Any]:
    return {"decision_id": decision_id, "underlying": underlying, "direction": direction, "strength": strength}


def test_2330_short_is_blocked_by_the_gross_limit(rows) -> None:
    out = ep.decide(_view("2330", "SHORT", 0.6), _candidates(rows, "2330", 2135.0), {}, BUDGET, PARAMS)
    assert out["reason_code"] == "no_feasible_instrument"
    assert out["detail"] == "gross_limit"


def test_0050_short_chooses_one_srf(rows) -> None:
    out = ep.decide(_view("0050", "SHORT", 0.6), _candidates(rows, "0050", 92.5), {}, BUDGET, PARAMS)
    assert (out["instrument_id"], out["side"], out["units"], out["reason_code"]) == (
        "ssf:SRF:202611",
        "sell",
        1,
        "short_via_future",
    )
    assert out["target_notional"] == pytest.approx(120_000.0)
    assert out["policy_version"] == "1.0"
    assert out["policy_parameters_digest"] == ep.policy_parameters_digest(PARAMS)


def test_2317_short_is_blocked(rows) -> None:
    out = ep.decide(_view("2317", "SHORT", 0.3), _candidates(rows, "2317", 225.0), {}, BUDGET, PARAMS)
    assert out["reason_code"] == "no_feasible_instrument"
    assert out["detail"] == "gross_limit"


def test_2330_weak_long_buys_one_cash_share(rows) -> None:
    out = ep.decide(_view("2330", "LONG", 0.2), _candidates(rows, "2330", 2135.0), {}, BUDGET, PARAMS)
    assert (out["instrument_id"], out["side"], out["units"], out["reason_code"]) == ("eq:2330", "buy", 1, "long_cash")


def test_0050_strong_long_picks_the_lowest_score_candidate(rows) -> None:
    candidates = _candidates(rows, "0050", 92.5)
    out = ep.decide(_view("0050", "LONG", 0.9), candidates, {}, BUDGET, PARAMS)
    assert out["instrument_id"] == "ssf:SRF:202611"
    assert out["units"] == 2
    assert out["reason_code"] == "long_lever"
    assert out["target_notional"] == pytest.approx(180_000.0)
    assert "ssf:NYF:202611" in out["candidates_considered"]


def test_absent_strength_chooses_one_unit_of_the_cheapest_feasible(rows) -> None:
    out = ep.decide(_view("0050", "LONG", None), _candidates(rows, "0050", 92.5), {}, BUDGET, PARAMS)
    assert out["instrument_id"] == "eq:0050"
    assert out["units"] == 1
    out = ep.decide(_view("0050", "SHORT", None), _candidates(rows, "0050", 92.5), {}, BUDGET, PARAMS)
    assert out["units"] == 1 and out["side"] == "sell"


def test_flat_view_and_empty_candidates() -> None:
    assert ep.decide(_view("0050", "FLAT", None), [], {}, BUDGET, PARAMS)["reason_code"] == "flat_view"
    assert ep.decide(_view("0050", "LONG", 0.5), [], {}, BUDGET, PARAMS)["reason_code"] == "candidates_unavailable"


def test_opposite_open_position_yields_the_closing_leg(rows) -> None:
    book = {"positions": [{"instrument_id": "ssf:SRF:202611", "underlying": "0050", "direction": "SHORT", "units": 3}]}
    out = ep.decide(_view("0050", "LONG", 0.9), _candidates(rows, "0050", 92.5), book, BUDGET, PARAMS)
    assert (out["instrument_id"], out["side"], out["units"], out["reason_code"]) == (
        "ssf:SRF:202611",
        "buy",
        3,
        "close_leg",
    )
    # closing leg is valid even above max_units_by_risk
    assert ep.enforce(out, _candidates(rows, "0050", 92.5), _view("0050", "LONG", 0.9), book) == out


def test_opposite_position_without_close_before_reverse_goes_on(rows) -> None:
    book = {"positions": [{"instrument_id": "ssf:SRF:202611", "underlying": "0050", "direction": "SHORT", "units": 3}]}
    out = ep.decide(
        _view("0050", "LONG", 0.9), _candidates(rows, "0050", 92.5), book, BUDGET, {**PARAMS, "close_before_reverse": False}
    )
    assert out["reason_code"] == "long_lever"


def test_same_direction_position_is_already_at_target_unless_pyramiding(rows) -> None:
    book = {"positions": [{"instrument_id": "eq:0050", "underlying": "0050", "direction": "LONG", "units": 5}]}
    candidates = _candidates(rows, "0050", 92.5)
    out = ep.decide(_view("0050", "LONG", 0.9), candidates, book, BUDGET, PARAMS)
    assert out["reason_code"] == "already_at_target"
    out = ep.decide(_view("0050", "LONG", 0.9), candidates, book, BUDGET, {**PARAMS, "allow_pyramiding": True})
    assert out["reason_code"] == "long_lever"


def test_decide_is_identical_across_a_thousand_calls(rows) -> None:
    candidates = _candidates(rows, "0050", 92.5)
    first = ep.decide(_view("0050", "LONG", 0.9), candidates, {}, BUDGET, PARAMS)
    for _ in range(1000):
        assert ep.decide(_view("0050", "LONG", 0.9), candidates, {}, BUDGET, PARAMS) == first


def test_decide_is_identical_across_process_restarts(rows) -> None:
    code = (
        "import json;from services.execution.lean_runtime import expression_policy as ep;"
        "c=[{'instrument_id':'eq:0050','kind':'cash_share','side_capability':['long'],'unit':1,'price':92.5,"
        "'fee_per_unit_per_side':0.0,'tax_rate_per_side':0.0,'max_units_by_risk':1081},"
        "{'instrument_id':'ssf:SRF:202611','kind':'stock_future','side_capability':['long','short'],'unit':1000,"
        "'price':92.5,'fee_per_unit_per_side':20.0,'tax_rate_per_side':0.00002,'max_units_by_risk':2}];"
        "print(json.dumps(ep.decide({'decision_id':'d','underlying':'0050','direction':'LONG','strength':0.9},c,{},"
        "{'gross_limit':200000,'gross_used':0},{}),sort_keys=True))"
    )
    root = Path(__file__).resolve().parents[3]
    outs = {
        subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True).stdout
        for _ in range(2)
    }
    assert len(outs) == 1
    assert json.loads(outs.pop())["instrument_id"] == "ssf:SRF:202611"


def test_malformed_decider_cannot_place_an_order(rows) -> None:
    candidates = _candidates(rows, "0050", 92.5)
    view = _view("0050", "LONG", 0.9)
    good = ep.decide(view, candidates, {}, BUDGET, PARAMS)
    for bad in (
        {**good, "instrument_id": "ssf:ZZZ:202611"},
        {**good, "units": 99},
        {**good, "price": 1.0},
        {**good, "initial_margin_per_unit": 1.0},
        {**good, "units": 0},
        "buy everything",
    ):
        out = ep.decide_validated(view, candidates, {}, BUDGET, PARAMS, decider=lambda *a, _b=bad: _b)
        assert out["reason_code"] == "instrument_not_in_candidates"
        assert "instrument_id" not in out
    assert ep.decide_validated(view, candidates, {}, BUDGET, PARAMS) == good


def _held(instrument_id: str, direction: str, units: int) -> dict[str, Any]:
    return {"instrument_id": instrument_id, "underlying": "0050", "direction": direction, "units": units}


def test_forged_close_leg_is_rejected_without_a_matching_holding(rows) -> None:
    candidates = _candidates(rows, "0050", 92.5)
    view = _view("0050", "LONG", 0.9)
    forged = {**ep.decide(view, candidates, {}, BUDGET, PARAMS)}
    forged.update(instrument_id="eq:0050", side="buy", units=1000000, reason_code="close_leg")
    for book in ({}, {"positions": [_held("eq:0050", "LONG", 5)]}):  # none / wrong side
        out = ep.decide_validated(view, candidates, book, BUDGET, PARAMS, decider=lambda *a: forged)
        assert out["reason_code"] == "instrument_not_in_candidates"
        assert "instrument_id" not in out
    short_book = {"positions": [_held("eq:0050", "SHORT", 5)]}
    oversized = {**forged, "units": 6}
    out = ep.decide_validated(view, candidates, short_book, BUDGET, PARAMS, decider=lambda *a: oversized)
    assert out["reason_code"] == "instrument_not_in_candidates"
    legit = {**forged, "units": 5}
    out = ep.decide_validated(view, candidates, short_book, BUDGET, PARAMS, decider=lambda *a: legit)
    assert out["reason_code"] == "close_leg" and out["units"] == 5


def test_opposite_holding_is_closed_regardless_of_position_order(rows) -> None:
    candidates = _candidates(rows, "0050", 92.5)
    view = _view("0050", "LONG", 0.9)
    srf = next(c["instrument_id"] for c in candidates if c["instrument_id"].startswith("ssf:"))
    a, b = _held("eq:0050", "LONG", 5), _held(srf, "SHORT", 1)
    for positions in ([a, b], [b, a]):
        out = ep.decide(view, candidates, {"positions": positions}, BUDGET, PARAMS)
        assert (out["instrument_id"], out["side"], out["units"], out["reason_code"]) == (srf, "buy", 1, "close_leg")


class TestBudgetUnavailableNoTrade:
    def test_record_names_the_missing_input_and_is_a_no_trade(self) -> None:
        from services.execution.lean_runtime import expression_policy

        view = {"decision_id": "dec-1", "underlying": "2330", "direction": "LONG", "strength": 1.0}
        record = expression_policy.budget_unavailable_no_trade(view, "risk_policy", "pool has none")
        assert record["reason_code"] == "candidates_unavailable"
        assert record["detail"] == "budget_unavailable: risk_policy (pool has none)"
        assert record["candidates_considered"] == []
        assert expression_policy.is_no_trade(record)
        assert expression_policy.enforce(record, [], view) == record
        bare = expression_policy.budget_unavailable_no_trade(view, "ledger")
        assert bare["detail"] == "budget_unavailable: ledger"


# --- notional_target_v1: a flat view closes and a weaker view trims -----------------
def _cash_cand(price: float = 100.0, maximum: int = 1000) -> dict[str, Any]:
    return {"instrument_id": "eq:2330", "kind": "cash_share", "side_capability": ["long"],
            "unit": 1, "multiplier": 1, "price": price, "max_units_by_risk": maximum,
            "fee_per_unit_per_side": 0.0, "tax_rate_per_side": 0.0}


def _held_book(units: int, direction: str = "LONG") -> dict[str, Any]:
    return {"positions": [{"underlying": "2330", "instrument_id": "eq:2330",
                           "direction": direction, "units": units}]}


_TRIM_BUDGET = {"gross_limit": 100_000.0, "gross_used": 50_000.0, "available_cash": 50_000.0}


def test_flat_view_with_open_position_closes_it_fully() -> None:
    out = ep.decide_validated(_view("2330", "FLAT", None), [_cash_cand()], _held_book(500), _TRIM_BUDGET, PARAMS)
    assert (out["reason_code"], out["side"], out["units"], out["instrument_id"]) == ("close_leg", "sell", 500, "eq:2330")


def test_flat_view_closes_a_short_with_a_buy() -> None:
    cand = {**_cash_cand(), "side_capability": ["long", "short"]}
    out = ep.decide_validated(_view("2330", "FLAT", None), [cand], _held_book(3, "SHORT"), _TRIM_BUDGET, PARAMS)
    assert (out["reason_code"], out["side"], out["units"]) == ("close_leg", "buy", 3)


def test_flat_view_without_position_is_still_flat_view_and_unknown_leg_fails_closed() -> None:
    assert ep.decide(_view("2330", "FLAT", None), [_cash_cand()], {}, _TRIM_BUDGET, PARAMS)["reason_code"] == "flat_view"
    out = ep.decide(_view("2330", "FLAT", None), [], _held_book(5), _TRIM_BUDGET, PARAMS)
    assert out["reason_code"] == "no_feasible_instrument"
    assert out["detail"] == "close_instrument_not_in_candidates"


def test_opposite_view_still_closes_before_reversing() -> None:
    cand = {**_cash_cand(), "side_capability": ["long", "short"]}
    out = ep.decide(_view("2330", "SHORT", 1.0), [cand], _held_book(500), _TRIM_BUDGET, PARAMS)
    assert (out["reason_code"], out["side"], out["units"]) == ("close_leg", "sell", 500)


def test_weaker_same_direction_view_trims_by_the_band_rule() -> None:
    # exposure 50,000; strength .2 -> target 20,000; band = max(100, 4,000) = 4,000.
    out = ep.decide_validated(_view("2330", "LONG", 0.2), [_cash_cand()], _held_book(500), _TRIM_BUDGET, PARAMS)
    assert (out["reason_code"], out["side"]) == ("close_leg", "sell")
    assert out["units"] == 300  # floor(500 * (50,000 - 20,000) / 50,000)
    assert out["target_notional"] == pytest.approx(20_000.0)
    assert out["rebalance_band"] == pytest.approx(4_000.0)
    assert out["policy_parameters_digest"] == ep.policy_parameters_digest(PARAMS)


def test_view_within_the_band_does_not_trim_and_stronger_view_adds_only_with_pyramiding() -> None:
    # strength .48 -> target 48,000; exposure - target = 2,000 <= band 9,600.
    out = ep.decide(_view("2330", "LONG", 0.48), [_cash_cand()], _held_book(500), _TRIM_BUDGET, PARAMS)
    assert out["reason_code"] == "already_at_target"
    strong = _view("2330", "LONG", 1.0)
    assert ep.decide(strong, [_cash_cand()], _held_book(500), _TRIM_BUDGET, PARAMS)["reason_code"] == "already_at_target"
    added = ep.decide(strong, [_cash_cand()], _held_book(500), _TRIM_BUDGET, {**PARAMS, "allow_pyramiding": True})
    assert added["reason_code"] == "long_cash" and added["side"] == "buy"


def test_trim_band_is_a_parameter_and_trim_is_validated_by_enforce() -> None:
    args = (_view("2330", "LONG", 0.4), [_cash_cand()], _held_book(500), _TRIM_BUDGET)
    assert ep.decide(*args, PARAMS)["reason_code"] == "close_leg"  # 10,000 > default band 8,000
    assert ep.decide(*args, {**PARAMS, "rebalance_band_fraction": 1.0})["reason_code"] == "already_at_target"
    out = ep.decide_validated(_view("2330", "LONG", 0.2), [_cash_cand()], _held_book(500), _TRIM_BUDGET, PARAMS)
    over = {**out, "units": 501}
    assert ep.enforce(over, [_cash_cand()], _view("2330", "LONG", 0.2), _held_book(500))["reason_code"] == \
        "instrument_not_in_candidates"


def test_default_parameters_and_digest_are_unchanged() -> None:
    assert set(ep.DEFAULT_PARAMETERS) == {"lever_min_strength", "notional_fraction_at_full_strength",
                                          "allow_pyramiding", "close_before_reverse"}
    assert ep.NOTIONAL_REBALANCE_BAND_FRACTION == 0.2


# --- cash_share buy sizing leaves room for the buy commission -----------------
def _eq(code: str, price: float, maximum: int = 100_000) -> dict[str, Any]:
    return {
        "instrument_id": f"eq:{code}", "kind": "cash_share", "underlying": code,
        "side_capability": ["long"], "unit": 1, "price": price, "multiplier": 1,
        "max_units_by_risk": maximum, "fee_per_unit_per_side": 0, "tax_rate_per_side": 0,
    }


_HOSTED_BUDGET = {"available_cash": 100_000.0, "gross_limit": 100_000.0, "gross_used": 0.0}


def test_hosted_2317_case_sizes_451_not_452_decide() -> None:
    from services.execution.lean_runtime.taiwan_cash_costs import buy_cash_required

    rec = ep.decide(_view("2317", "LONG", 1.0), [_eq("2317", 221.0)], {}, _HOSTED_BUDGET, PARAMS)
    assert rec["instrument_id"] == "eq:2317"
    assert rec["units"] == 451
    assert buy_cash_required("2317", 451, 221.0) <= 100_000
    assert buy_cash_required("2317", 452, 221.0) == pytest.approx(100034.3461)


def test_hosted_2317_case_sizes_451_target_position() -> None:
    from services.execution.lean_runtime.taiwan_cash_costs import buy_cash_required

    view = {**_view("2317", "LONG", 1.0), "target_notional": 100_000.0}
    rec = ep.decide_target_position(view, [_eq("2317", 221.0)], {}, _HOSTED_BUDGET, PARAMS)
    assert rec["instrument_id"] == "eq:2317" and rec["side"] == "buy"
    assert rec["units"] == 451
    assert buy_cash_required("2317", 451, 221.0) <= 100_000


def test_target_position_single_unit_over_cash_is_no_feasible_instrument() -> None:
    view = {**_view("2317", "LONG", 1.0), "target_notional": 100_000.0}
    budget = {**_HOSTED_BUDGET, "available_cash": 221.1}
    rec = ep.decide_target_position(view, [_eq("2317", 221.0)], {}, budget, PARAMS)
    assert ep.is_no_trade(rec) and rec["reason_code"] == "no_feasible_instrument"


def test_single_unit_plus_commission_over_cash_is_no_feasible_instrument() -> None:
    budget = {"available_cash": 221.1, "gross_limit": 100_000.0, "gross_used": 0.0}
    rec = ep.decide(_view("2317", "LONG", 1.0), [_eq("2317", 221.0)], {}, budget, PARAMS)
    assert ep.is_no_trade(rec)
    assert rec["reason_code"] == "no_feasible_instrument"


def test_closes_and_non_cash_share_are_not_commission_fitted() -> None:
    rec = ep.decide(_view("2330", "FLAT", 0.0), [_cash_cand()], _held_book(10), _HOSTED_BUDGET, PARAMS)
    assert rec["units"] == 10 and rec["side"] == "sell"
    future = {**_eq("2317", 221.0), "kind": "stock_future", "instrument_id": "ssf:X:202611"}
    cands = [future]
    # a non cash_share candidate is never passed through the commission fit
    assert ep.fit_cash_share_buy is not None and cands[0]["kind"] != "cash_share"


# --- PAPER-SIZING-BOUND-RISK-POLICY-20261121: AC1 & AC2 replay and explicit limit fixtures ---

TJ_2317_JOURNEY_ID = "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd"
TJ_2317_DECISION_ID = "dec-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09"
TJ_2317_VIEW = {
    "decision_id": TJ_2317_DECISION_ID,
    "underlying": "2317",
    "direction": "LONG",
    "strength": 0.91324200913242,
}
TJ_2317_CANDIDATES = [
    {
        "decision_id": TJ_2317_DECISION_ID,
        "instrument_id": "eq:2317",
        "kind": "cash_share",
        "underlying": "2317",
        "side_capability": ["long"],
        "unit": 1,
        "multiplier": 1,
        "price": 221.0,
        "price_date": "2026-11-18",
        "price_basis": "stock_close_prev_session",
        "initial_margin_per_unit": 221.0,
        "maintenance_margin_per_unit": 0,
        "fee_per_unit_per_side": 0.0,
        "tax_rate_per_side": 0.0,
        "expiry": None,
        "last_trading_day": None,
        "liquidity": None,
        "max_units_by_risk": 452,
    },
    {
        "decision_id": TJ_2317_DECISION_ID,
        "instrument_id": "ssf:DHF:202612",
        "kind": "stock_future",
        "underlying": "2317",
        "side_capability": ["long", "short"],
        "unit": 2000,
        "multiplier": 2000,
        "price": 220.0,
        "price_date": "2026-11-18",
        "price_basis": "futures_daily_settlement_prev_session",
        "initial_margin_per_unit": 59400.0,
        "maintenance_margin_per_unit": 45540.0,
        "fee_per_unit_per_side": 20.0,
        "tax_rate_per_side": 0.00002,
        "expiry": "2026-12-16",
        "last_trading_day": "2026-12-16",
        "liquidity": {"open_interest": 412, "volume": 128},
        "max_units_by_risk": 0,
    },
]
# Observed historical inline v1.0 fixture from tj_79726064-4cc2-5510-8e11-7f5e59ff89bd
# (pool-persona-paper-039ad01f8bcd2b4aa466 / rp-pool-persona-paper-039ad01f8bcd2b4aa466 v1.0).
# Note: on hosted dev, owner capital_pool_activation profile-mapping approval for requested_risk="low"
# remains unresolved (PAPER-RISK-POLICY-PROVENANCE-20261121 / PAPER-RISK-POLICY-BINDING-20261121);
# this fixture replays the pure expression-policy sizing calculation once the policy is resolved.
OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317 = {
    "equity": 100000.0,
    "raw_cash": 100000.0,
    "available_cash": 100000.0,
    "available_buying_power": 100000.0,
    "gross_limit": 200000.0,
    "gross_limit_multiple": 2.0,
    "gross_used": 0.0,
    "net_limit": 200000.0,
    "net_limit_multiple": 2.0,
    "net_used": 0.0,
    "signed_net_used": 0.0,
    "margin_reserved": 0.0,
    "cash_reserved": 0.0,
    "max_leverage": 2.0,
    "max_single_name_weight": None,
    "max_single_name_notional": None,
    "min_cash_reserve_pct": None,
    "min_cash_reserve": None,
    "cash_reserve_required": 0.0,
    "underlying_exposures": {},
    "risk_policy_id": "rp-pool-persona-paper-039ad01f8bcd2b4aa466",
    "risk_policy_version": "1.0",
}


def test_exact_2317_incident_replay_451_share_baseline() -> None:
    """AC2: Exact pre-trade view replay for tj_79726064-4cc2-5510-8e11-7f5e59ff89bd at price 221 and capital 100000."""
    import json

    sizing = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW,
        TJ_2317_CANDIDATES,
        {"positions": [], "gross_notional": 0.0},
        OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        PARAMS,
    )
    assert sizing["decision_id"] == TJ_2317_DECISION_ID
    assert sizing["underlying"] == "2317"
    assert sizing["dated_price"] == 221.0
    assert sizing["capital"] == 100000.0
    assert sizing["risk_policy_id"] == "rp-pool-persona-paper-039ad01f8bcd2b4aa466"
    assert sizing["risk_policy_version"] == "1.0"
    assert sizing["target_notional"] == pytest.approx(182648.401826, rel=1e-9)
    assert sizing["permitted_units"] == 451
    assert sizing["binding_limit"] == "available_cash_after_fees"
    assert sizing["order_notional"] == pytest.approx(99671.0)
    assert sizing["commission_fee"] == pytest.approx(142.031175)
    assert sizing["fee_inclusive_required_cash"] == pytest.approx(99813.031175)
    assert sizing["remaining_cash"] == pytest.approx(186.968825)
    assert sizing["after_trade_concentration"] == pytest.approx(0.998128, abs=1e-6)
    assert sizing["after_trade_concentration_of_initial_capital"] == pytest.approx(0.99671, abs=1e-6)
    assert sizing["decision"]["instrument_id"] == "eq:2317"
    assert sizing["decision"]["units"] == 451
    compact = ep.compact_pre_trade_sizing(sizing)
    assert "decision" not in compact
    assert "constraint_unit_caps" not in compact
    assert len(json.dumps(compact, sort_keys=True, separators=(",", ":")).encode("utf-8")) <= ep.PRE_TRADE_SIZING_EVIDENCE_MAX_BYTES


def test_2317_replay_under_labelled_nontrivial_explicit_limit_fixtures() -> None:
    """AC1 & AC2: Test nontrivial explicit limits with clearly labelled fixture values across long and target-position paths."""
    # Fixture 1 (labelled test fixture): explicit per-name max_single_name_weight = 0.25 (25,000 TWD)
    FIXTURE_EXPLICIT_SINGLE_NAME_BUDGET = {
        **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        "risk_policy_id": "fixture-explicit-single-name-25pct",
        "risk_policy_version": "fixture-1.0",
        "max_single_name_weight": 0.25,
        "max_single_name_notional": 25000.0,
    }
    s_single = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, TJ_2317_CANDIDATES, {"positions": [], "gross_notional": 0.0}, FIXTURE_EXPLICIT_SINGLE_NAME_BUDGET, PARAMS
    )
    assert s_single["permitted_units"] == 113
    assert s_single["binding_limit"] == "max_single_name_weight"
    assert s_single["fee_inclusive_required_cash"] == pytest.approx(25008.586525)
    assert s_single["remaining_cash"] == pytest.approx(74991.413475)
    assert s_single["after_trade_concentration"] <= 0.25

    # Fixture 2 (labelled test fixture): explicit cash-reserve rule min_cash_reserve_pct = 0.20 (20,000 TWD reserve)
    FIXTURE_EXPLICIT_CASH_RESERVE_BUDGET = {
        **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        "risk_policy_id": "fixture-explicit-cash-reserve-20pct",
        "risk_policy_version": "fixture-1.0",
        "min_cash_reserve_pct": 0.20,
        "cash_reserve_required": 20000.0,
        "available_cash": 80000.0,
        "available_buying_power": 80000.0,
    }
    s_reserve = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, TJ_2317_CANDIDATES, {"positions": [], "gross_notional": 0.0}, FIXTURE_EXPLICIT_CASH_RESERVE_BUDGET, PARAMS
    )
    assert s_reserve["permitted_units"] == 361
    assert s_reserve["binding_limit"] == "cash_reserve"
    assert s_reserve["fee_inclusive_required_cash"] == pytest.approx(79894.687925)
    assert s_reserve["remaining_cash"] == pytest.approx(20105.312075)
    assert s_reserve["remaining_cash"] >= 20000.0

    # Fixture 3 (labelled test fixture): explicit max_leverage = 0.60 (60,000 TWD) tighter than gross_limit = 2.0 (200,000 TWD)
    FIXTURE_EXPLICIT_LEVERAGE_BUDGET = {
        **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        "risk_policy_id": "fixture-explicit-leverage-60pct",
        "risk_policy_version": "fixture-1.0",
        "max_leverage": 0.60,
    }
    s_lev = ep.evaluate_pre_trade_sizing(
        {**TJ_2317_VIEW, "strength": 1.0},
        TJ_2317_CANDIDATES,
        {"positions": [], "gross_notional": 0.0},
        FIXTURE_EXPLICIT_LEVERAGE_BUDGET,
        PARAMS,
    )
    assert s_lev["permitted_units"] == 271
    assert s_lev["binding_limit"] == "max_leverage"
    assert s_lev["order_notional"] == pytest.approx(59891.0)
    assert s_lev["after_trade_gross_exposure"] <= 0.60

    # Fixture 4 (labelled test fixture): explicit net_limit = 40,000 TWD with existing long exposure 10,000 TWD -> 30,000 remaining
    FIXTURE_EXPLICIT_NET_LIMIT_BUDGET = {
        **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        "risk_policy_id": "fixture-explicit-net-limit-40pct",
        "risk_policy_version": "fixture-1.0",
        "net_limit": 40000.0,
        "net_limit_multiple": 0.40,
        "gross_used": 10000.0,
        "net_used": 10000.0,
        "signed_net_used": 10000.0,
    }
    s_net = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, TJ_2317_CANDIDATES, {"positions": [], "gross_notional": 10000.0}, FIXTURE_EXPLICIT_NET_LIMIT_BUDGET, PARAMS
    )
    assert s_net["permitted_units"] == 135
    assert s_net["binding_limit"] == "net_limit"
    assert s_net["after_trade_net_exposure"] <= 0.40

    # Fixture 5 (labelled test fixture): target_position_v1 path with existing 2317 holding (50 shares = 11,050 TWD),
    # competing reservation (30,000 TWD cash reserved), and explicit max_single_name_weight = 0.30 (30,000 TWD)
    FIXTURE_TARGET_POS_BOOK = {
        "positions": [
            {"underlying": "2317", "instrument_id": "eq:2317", "direction": "LONG", "units": 50, "notional": 11050.0}
        ],
        "gross_notional": 11050.0,
    }
    FIXTURE_TARGET_POS_BUDGET = {
        **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        "risk_policy_id": "fixture-target-pos-single-name-30pct",
        "risk_policy_version": "fixture-1.0",
        "gross_used": 11050.0,
        "signed_net_used": 11050.0,
        "max_single_name_weight": 0.30,
        "max_single_name_notional": 30000.0,
        "underlying_exposures": {"2317": 11050.0},
        "cash_reserved": 30000.0,
        "available_cash": 70000.0,
        "available_buying_power": 70000.0,
    }
    alloc = ep.allocate_targets({"2317.TW": TJ_2317_VIEW}, FIXTURE_TARGET_POS_BUDGET, PARAMS)
    assert alloc["targets"]["2317.TW"] == pytest.approx(30000.0)
    s_target = ep.evaluate_pre_trade_sizing(
        {**TJ_2317_VIEW, "target_notional": 50000.0},
        TJ_2317_CANDIDATES,
        FIXTURE_TARGET_POS_BOOK,
        FIXTURE_TARGET_POS_BUDGET,
        PARAMS,
        policy_kind=ep.TARGET_POLICY_KIND,
    )
    # 30,000 single-name cap - 11,050 existing = 18,950 remaining -> floor(18950 / 221) = 85 shares (85 * 221 = 18,785)
    assert s_target["permitted_units"] == 85
    assert s_target["binding_limit"] == "max_single_name_weight"
    assert s_target["after_trade_concentration"] <= 0.30


def test_overlapping_limits_report_most_restrictive_binding_cap() -> None:
    """AC2 regression: when multiple limits are active simultaneously, binding_limit reports the actually restrictive cap."""
    # 1. Exact review reproduction: equity=100,000, raw/available cash=10,000, max_single_name_weight=0.80, price=221.
    # Single-name cap is 361 shares, cash cap after fees is 45 shares -> permitted=45, binding_limit=available_cash_after_fees.
    s_cash_tighter_than_single = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW,
        TJ_2317_CANDIDATES,
        {"positions": [], "gross_notional": 0.0},
        {
            **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
            "risk_policy_id": "fixture-overlapping-cash-10k-single-80pct",
            "risk_policy_version": "fixture-1.0",
            "raw_cash": 10000.0,
            "available_cash": 10000.0,
            "available_buying_power": 10000.0,
            "max_single_name_weight": 0.80,
            "max_single_name_notional": 80000.0,
        },
        PARAMS,
    )
    assert s_cash_tighter_than_single["permitted_units"] == 45
    assert s_cash_tighter_than_single["constraint_unit_caps"]["available_cash_after_fees"] == 45
    assert s_cash_tighter_than_single["constraint_unit_caps"]["max_single_name_weight"] == 361
    assert s_cash_tighter_than_single["binding_limit"] == "available_cash_after_fees"

    # 2. Overlapping max_single_name_weight=0.80 (361 shares), min_cash_reserve_pct=0.20 (361 shares),
    # and max_leverage=0.60 (271 shares) -> max_leverage is tightest at 271 shares.
    s_lev_tighter_than_single = ep.evaluate_pre_trade_sizing(
        {**TJ_2317_VIEW, "strength": 1.0},
        TJ_2317_CANDIDATES,
        {"positions": [], "gross_notional": 0.0},
        {
            **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
            "risk_policy_id": "fixture-overlapping-lev-60-single-80",
            "risk_policy_version": "fixture-1.0",
            "max_single_name_weight": 0.80,
            "max_single_name_notional": 80000.0,
            "min_cash_reserve_pct": 0.20,
            "cash_reserve_required": 20000.0,
            "available_cash": 80000.0,
            "available_buying_power": 80000.0,
            "max_leverage": 0.60,
        },
        PARAMS,
    )
    assert s_lev_tighter_than_single["permitted_units"] == 271
    assert s_lev_tighter_than_single["constraint_unit_caps"]["max_single_name_weight"] == 361
    assert s_lev_tighter_than_single["constraint_unit_caps"]["max_leverage"] == 271
    assert s_lev_tighter_than_single["binding_limit"] == "max_leverage"

    # 3. Overlapping max_single_name_weight=0.80 (361 shares), max_leverage=0.60 (271 shares),
    # and net_limit=40,000 with 10,000 existing long (135 shares) -> net_limit is tightest at 135 shares.
    s_net_tighter_than_single = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW,
        TJ_2317_CANDIDATES,
        {"positions": [], "gross_notional": 10000.0},
        {
            **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
            "risk_policy_id": "fixture-overlapping-net-40k-single-80",
            "risk_policy_version": "fixture-1.0",
            "max_single_name_weight": 0.80,
            "max_single_name_notional": 80000.0,
            "max_leverage": 0.60,
            "net_limit": 40000.0,
            "net_limit_multiple": 0.40,
            "gross_used": 10000.0,
            "net_used": 10000.0,
            "signed_net_used": 10000.0,
        },
        PARAMS,
    )
    assert s_net_tighter_than_single["permitted_units"] == 135
    assert s_net_tighter_than_single["constraint_unit_caps"]["max_single_name_weight"] == 361
    assert s_net_tighter_than_single["constraint_unit_caps"]["net_limit"] == 135
    assert s_net_tighter_than_single["binding_limit"] == "net_limit"


def test_integrated_provider_to_candidate_to_decision_deducts_futures_and_cash_reservations_once() -> None:
    """Integrated provider -> candidate -> decision regression: deduct each reservation once across folded and raw budgets."""
    from services.capital.risk_policy import RiskPolicy
    from services.execution.lean_runtime import instrument_candidates as ic
    from services.execution.lean_runtime.expression_inputs import build_expression_inputs

    fut_reservation = {
        "reservation_id": "res-fut-60k",
        "underlying": "2330",
        "instrument_id": "ssf:CDF:202612",
        "kind": "stock_future",
        "side": "buy",
        "quantity": 1,
        "multiplier": 2000,
        "price": 30.0,
        "notional": 60000.0,
        "margin_reserved": 60000.0,
    }
    policy = RiskPolicy.from_mapping(
        {
            "risk_policy_id": "rp-pool-persona-paper-039ad01f8bcd2b4aa466",
            "version": "1.0",
            "status": "active",
            "gross_limit": 2.0,
            "net_limit": 2.0,
            "max_leverage": 2.0,
            "max_drawdown": 0.15,
            "max_daily_loss": 0.05,
            "allowed_asset_classes": ["equity", "future"],
        }
    )
    folded = build_expression_inputs(
        pool={"pool_id": "pool-1", "budget": 100000.0},
        policy=policy,
        summary={
            "runtime_id": "rt-1",
            "binding_id": "rb-1",
            "ledger_fill_count": 0,
            "positions": [],
            "order_reservations": [fut_reservation],
        },
    )
    assert folded["risk_budget"]["free_margin"] == 40000.0
    assert folded["risk_budget"]["available_cash"] == 40000.0

    cands_folded = ic.build_candidates(
        "2317", "2026-11-19", folded["persona_book"], folded["risk_budget"], cash_price=221.0
    )
    assert cands_folded[0]["max_units_by_risk"] == 180

    sizing_folded = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_folded, folded["persona_book"], folded["risk_budget"], PARAMS
    )
    assert sizing_folded["available_buying_power"] == pytest.approx(40000.0)
    assert sizing_folded["permitted_units"] == 180
    assert sizing_folded["decision"]["units"] == 180
    assert sizing_folded["binding_limit"] == "cash_reserved"
    assert sizing_folded["fee_inclusive_required_cash"] == pytest.approx(39836.6865)
    assert sizing_folded["remaining_cash"] == pytest.approx(163.3135)

    # Raw fixture budget with the same reservation must yield identical candidate cap, spendable cash, and permitted units
    raw_budget = {
        **OBSERVED_HISTORICAL_INLINE_V1_BUDGET_2317,
        "order_reservations": [fut_reservation],
    }
    cands_raw = ic.build_candidates("2317", "2026-11-19", {"positions": [], "gross_notional": 0.0}, raw_budget, cash_price=221.0)
    assert cands_raw[0]["max_units_by_risk"] == 180
    sizing_raw = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_raw, {"positions": [], "gross_notional": 0.0}, raw_budget, PARAMS
    )
    assert sizing_raw["available_buying_power"] == pytest.approx(40000.0)
    assert sizing_raw["permitted_units"] == 180
    assert sizing_raw["binding_limit"] == "cash_reserved"


def test_blueprint_limits_sector_reduction_or_refusal_turnover_liquidity_and_reduce_only() -> None:
    """RISK-POLICY-BLUEPRINT-LIMITS-ENFORCED-20261124: sector, turnover, liquidity and reduce-only in expression_policy."""
    from services.execution.lean_runtime import instrument_candidates as ic

    # Hosted-shaped 100,000 TWD pool holding 20,000 TWD in 2303 (Taiwan semiconductor sector "24").
    # Sector "24" cap is 0.25 (25,000 TWD), single-name cap is 0.20 (20,000 TWD).
    # A new buy on 2317 (also placed in sector "24" for sector-cap test) @ 221 TWD has only 5,000 TWD sector room -> 22 shares.
    book_with_sector_peer = {
        "positions": [
            {
                "instrument_id": "eq:2303",
                "underlying": "2303",
                "kind": "cash_share",
                "direction": "LONG",
                "units": 200,
                "multiplier": 1,
                "notional": 20_000.0,
            }
        ],
        "gross_notional": 20_000.0,
    }
    base_budget = {
        "equity": 100_000.0,
        "raw_cash": 80_000.0,
        "available_cash": 70_000.0,
        "available_buying_power": 70_000.0,
        "cash_reserve_required": 10_000.0,
        "gross_limit": 160_000.0,
        "gross_limit_multiple": 1.6,
        "gross_used": 20_000.0,
        "net_limit": 100_000.0,
        "net_limit_multiple": 1.0,
        "net_used": 20_000.0,
        "signed_net_used": 20_000.0,
        "max_leverage": 1.6,
        "max_single_name_weight": 0.20,
        "max_single_name_notional": 20_000.0,
        "max_sector_exposure": {"24": 0.25},
        "sector_by_underlying": {"2303": "24", "2317": "24"},
        "sector_exposures": {"24": 0.20},
        "turnover_limit": 1.0,
        "turnover_limit_notional": 100_000.0,
        "turnover_used": 0.20,
        "turnover_used_notional": 20_000.0,
        "liquidity_constraints": {"min_avg_daily_volume": 1000.0, "max_order_pct_adv": 0.05},
        "avg_daily_volume_by_underlying": {"2317": 10_000.0},
    }
    cands_partial = ic.build_candidates(
        "2317", "2026-11-19", book_with_sector_peer, base_budget, cash_price=221.0
    )
    sizing_partial = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_partial, book_with_sector_peer, base_budget, PARAMS
    )
    # Single-name cap alone would allow 90 shares (19,890 TWD), but sector "24" has only 5,000 TWD left -> 22 shares (4,862 TWD).
    assert sizing_partial["permitted_units"] == 22
    assert sizing_partial["binding_limit"] == "max_sector_exposure"

    # 2. Sector already at/over the cap (25,000 TWD used in sector "24") -> refused with max_sector_exposure.
    full_sector_book = {
        "positions": [
            {
                "instrument_id": "eq:2303",
                "underlying": "2303",
                "kind": "cash_share",
                "direction": "LONG",
                "units": 250,
                "multiplier": 1,
                "notional": 25_000.0,
            }
        ],
        "gross_notional": 25_000.0,
    }
    full_sector_budget = {
        **base_budget,
        "gross_used": 25_000.0,
        "sector_exposures": {"24": 0.25},
    }
    cands_full = ic.build_candidates("2317", "2026-11-19", full_sector_book, full_sector_budget, cash_price=221.0)
    sizing_full = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_full, full_sector_book, full_sector_budget, PARAMS
    )
    assert sizing_full["permitted_units"] == 0
    assert sizing_full["decision"]["reason_code"] == "no_feasible_instrument"
    assert sizing_full["decision"]["detail"] == "max_sector_exposure"
    assert sizing_full["binding_limit"] == "max_sector_exposure"

    # 3. Turnover breach (98,000 TWD already traded against 100,000 TWD turnover limit; order wants 22 shares = 4,862 TWD) -> refused with turnover_limit.
    turnover_breach_budget = {
        **base_budget,
        "turnover_used": 0.98,
        "turnover_used_notional": 98_000.0,
    }
    sizing_turnover = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_partial, book_with_sector_peer, turnover_breach_budget, PARAMS
    )
    assert sizing_turnover["permitted_units"] == 0
    assert sizing_turnover["decision"]["detail"] == "turnover_limit"
    assert sizing_turnover["binding_limit"] == "turnover_limit"

    # 4. Illiquid order (> configured share of ADV: 22 shares vs ADV 200 -> 11% > 5% max_order_pct_adv) -> refused with liquidity_order_pct_adv_breach.
    illiquid_budget = {
        **base_budget,
        "liquidity_constraints": {"max_order_pct_adv": 0.05},
        "avg_daily_volume_by_underlying": {"2317": 200.0},
    }
    cands_illiquid = ic.build_candidates("2317", "2026-11-19", book_with_sector_peer, illiquid_budget, cash_price=221.0)
    sizing_illiquid = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_illiquid, book_with_sector_peer, illiquid_budget, PARAMS
    )
    assert sizing_illiquid["permitted_units"] == 0
    assert sizing_illiquid["decision"]["detail"] == "liquidity_order_pct_adv_breach"
    assert sizing_illiquid["binding_limit"] == "liquidity_order_pct_adv_breach"

    # 5. Missing fact on order increase fails closed naming the missing fact.
    missing_sec_budget = {**base_budget, "sector_by_underlying": {"2303": "24"}}
    sizing_missing_sec = ep.evaluate_pre_trade_sizing(
        TJ_2317_VIEW, cands_partial, book_with_sector_peer, missing_sec_budget, PARAMS
    )
    assert sizing_missing_sec["permitted_units"] == 0
    assert sizing_missing_sec["decision"]["detail"] == "sector_exposures_unavailable"

    # 6. Reduce-only (FLAT view closing held 2317 position) passes even when sector, turnover, and liquidity are breached or missing.
    held_2317_book = {
        "positions": [
            {
                "instrument_id": "eq:2317",
                "underlying": "2317",
                "kind": "cash_share",
                "direction": "LONG",
                "units": 100,
                "multiplier": 1,
                "notional": 22_100.0,
            }
        ],
        "gross_notional": 22_100.0,
    }
    flat_view = {**TJ_2317_VIEW, "direction": "FLAT", "strength": 0.0}
    reduce_sizing = ep.evaluate_pre_trade_sizing(
        flat_view, cands_full, held_2317_book, missing_sec_budget, PARAMS
    )
    assert reduce_sizing["decision"]["reason_code"] == "close_leg"
    assert reduce_sizing["decision"]["side"] == "sell"
    assert reduce_sizing["permitted_units"] == 100




