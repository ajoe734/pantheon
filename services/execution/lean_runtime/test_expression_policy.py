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
    assert ep.enforce(out, _candidates(rows, "0050", 92.5), _view("0050", "LONG", 0.9)) == out


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
