"""Expression replay tests: determinism, no network, and the design E1-E3 table."""
from __future__ import annotations

import copy
import socket
from unittest import mock

import pytest

from services.execution.lean_runtime import expression_replay as er


def _cash(code, price, max_units):
    return {
        "instrument_id": f"eq:{code}", "kind": "cash_share", "underlying": code,
        "side_capability": ["long"], "unit": 1, "multiplier": 1, "price": price,
        "initial_margin_per_unit": price, "fee_per_unit_per_side": 0.0,
        "tax_rate_per_side": 0.0, "max_units_by_risk": max_units,
    }


def _fut(iid, code, unit, price, margin, max_units):
    return {
        "instrument_id": iid, "kind": "stock_future", "underlying": code,
        "side_capability": ["long", "short"], "unit": unit, "multiplier": unit,
        "price": price, "initial_margin_per_unit": margin,
        "fee_per_unit_per_side": 0.0, "tax_rate_per_side": 0.0,
        "max_units_by_risk": max_units,
    }


def _record(did, code, direction, strength, candidates, gross, marks, book=None):
    return {
        "decision_id": did,
        "view": {"decision_id": did, "underlying": code, "direction": direction, "strength": strength},
        "candidates": candidates,
        "book_before": book or {},
        "budget": {"available_cash": 100000.0, "gross_limit": gross},
        "marks": marks,
    }


# Design section 8 sequences (stand-in stock closes), equity 100,000, costs excluded.
def _e_records(gross, direction):
    sign = 1
    return [
        _record("d-2330", "2330", direction, 1.0,
                [_cash("2330", 2370.0, 42), _fut("ssf:QFF:202611", "2330", 100, 2370.0, 31995.0, 1)],
                gross, {"eq:2330": [2135.0], "ssf:QFF:202611": [2250.0, 2135.0]}),
        _record("d-2317", "2317", direction, 1.0,
                [_cash("2317", 245.5, 407), _fut("ssf:DHF:202611", "2317", 2000, 245.5, 66285.0,
                                                  1 if gross > 500000 else 0)],
                gross, {"eq:2317": [224.5], "ssf:DHF:202611": [224.5]}),
        _record("d-0050", "0050", direction, 1.0,
                [_cash("0050", 104.95, 952), _fut("ssf:SRF:202611", "0050", 1000, 104.95, 8700.0, 2)],
                gross, {"eq:0050": [92.5], "ssf:SRF:202611": [92.5]}),
    ]


def test_e2_one_contract_short_reproduces_design_table():
    result = er.replay(_e_records(600000.0, "SHORT"), None)
    qff, dhf, srf = result["decisions"]
    assert qff["record"]["instrument_id"] == "ssf:QFF:202611" and qff["record"]["units"] == 1
    assert qff["pnl"] == 23500.0 and qff["pnl_path"] == [12000.0, 23500.0]
    assert qff["worst_pnl"] == 12000.0
    assert qff["positions_after"][0]["initial_margin_per_unit"] == 31995.0
    assert qff["margin_use"] == 31995.0
    assert dhf["pnl"] == 42000.0 and dhf["margin_use"] == 66285.0
    assert srf["record"]["units"] == 2  # N* = 600k, clamped to max_units 2
    assert srf["pnl"] == 24900.0


def test_e2_long_side_reverses_the_sign():
    qff, dhf, srf = er.replay(_e_records(600000.0, "LONG"), None)["decisions"]
    assert qff["pnl"] == -23500.0 and qff["worst_pnl"] == -23500.0
    assert dhf["pnl"] == -42000.0
    assert srf["pnl"] == -24900.0


def test_e3_three_times_cap():
    qff, dhf, srf = er.replay(_e_records(300000.0, "SHORT"), None)["decisions"]
    assert qff["record"]["units"] == 1 and qff["pnl"] == 23500.0 and qff["margin_use"] == 31995.0
    # One DHF is 4.91x notional of 100,000 equity: no feasible instrument, no trade recorded.
    assert dhf["outcome"] == "NoTrade" and dhf["pnl"] is None and dhf["margin_use"] == 0.0
    assert srf["record"]["units"] == 2 and srf["margin_use"] == 17400.0 and srf["pnl"] == 24900.0


def test_e1_one_cash_share():
    rec = _record("d-e1", "2330", "LONG", 0.2, [_cash("2330", 2370.0, 42),
                  _fut("ssf:QFF:202611", "2330", 100, 2370.0, 31995.0, 1)], 300000.0,
                  {"eq:2330": [2135.0], "ssf:QFF:202611": [2135.0]})
    [d] = er.replay([rec], None)["decisions"]
    assert d["record"]["instrument_id"] == "eq:2330" and d["record"]["units"] == 1
    assert d["pnl"] == -235.0 and d["margin_use"] == 2370.0
    # counterfactual: the future the same rule would have sized (min 1 unit)
    assert d["counterfactual_pnl"] == {"ssf:QFF:202611": -23500.0}


def test_no_trade_has_counterfactuals_for_every_candidate():
    [d] = er.replay([_e_records(200000.0, "SHORT")[1]], None)["decisions"]
    assert d["outcome"] == "NoTrade"
    assert d["counterfactual_pnl"] == {"eq:2317": 0.0, "ssf:DHF:202611": 0.0}


def test_deterministic_no_network_and_inputs_untouched():
    records = _e_records(600000.0, "SHORT")
    snapshot = copy.deepcopy(records)
    with mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")):
        first = er.replay(records, {"lever_min_strength": 0.4})
        second = er.replay(records, {"lever_min_strength": 0.4})
    assert first == second
    assert records == snapshot


def test_direction_is_never_recomputed():
    records = _e_records(600000.0, "SHORT")
    for r in records:  # marks rise: a recomputed direction would flip; the recorded one holds
        r["marks"] = {k: [v[-1] * 1.5] for k, v in r["marks"].items()}
    assert all(d["record"].get("side", "sell") == "sell" for d in er.replay(records, None)["decisions"]
               if d["outcome"] == "Chosen")


def test_parameter_set_changes_the_choice():
    rec = _record("d-p", "0050", "LONG", 0.9,
                  [_cash("0050", 92.5, 1081), _fut("ssf:SRF:202611", "0050", 1000, 92.5, 8700.0, 2)],
                  300000.0, {"eq:0050": [95.0], "ssf:SRF:202611": [95.0]})
    low = er.replay([rec], {"lever_min_strength": 0.5})["decisions"][0]
    high = er.replay([rec], {"lever_min_strength": 0.95})["decisions"][0]
    assert low["record"]["instrument_id"] == "ssf:SRF:202611"
    assert high["record"]["instrument_id"] == "eq:0050" and high["record"]["units"] == 1
    assert low["pnl"] == 5000.0 and high["pnl"] == 2.5


def test_close_leg_realizes_against_the_recorded_entry_and_carries_book():
    held = {"positions": [{"underlying": "0050", "instrument_id": "ssf:SRF:202611",
                           "direction": "SHORT", "units": 1, "entry_price": 104.95}]}
    fut = _fut("ssf:SRF:202611", "0050", 1000, 92.5, 8700.0, 2)
    rec = _record("d-c", "0050", "LONG", 0.6, [_cash("0050", 92.5, 1081), fut], 200000.0, {}, book=held)
    [d] = er.replay([rec], None)["decisions"]
    assert d["record"]["reason_code"] == "close_leg"
    assert d["pnl"] == 12450.0 and d["positions_after"] == [] and d["margin_use"] == 0.0
    assert d["counterfactual_pnl"] == {}


def test_counterfactual_rows_keyed_by_decision_and_instrument():
    records = _e_records(600000.0, "SHORT")
    result = er.replay(records, None)
    rows = er.counterfactual_rows(records[2], result["decisions"][2])
    assert [r["instrument_id"] for r in rows] == ["eq:0050", "ssf:SRF:202611"]
    chosen = [r for r in rows if r["chosen"]]
    assert len(chosen) == 1 and chosen[0]["pnl_cf"] == 24900.0
    assert all(r["decision_id"] == "d-0050" for r in rows)
    assert rows[0]["underlying_entry"] == 104.95 and rows[0]["underlying_mark"] == 92.5


def test_records_from_events_dedupes_and_attaches_marks():
    rec = _e_records(600000.0, "SHORT")[2]
    payload = {k: rec[k] for k in ("view", "candidates", "book_before", "budget")}
    payload.update(decision_id="d-0050", parameters={"lever_min_strength": 0.5})
    events = [{"event_type": "trade_decision", "metadata": {er.REPLAY_INPUT_KEY: payload}},
              {"event_type": "paper_order_simulated", "metadata": {er.REPLAY_INPUT_KEY: payload}},
              {"event_type": "heartbeat", "metadata": {}}]
    records, params = er.records_from_events(events, {"d-0050": rec["marks"]})
    assert len(records) == 1 and params == {"lever_min_strength": 0.5}
    assert er.replay(records, params)["decisions"][0]["pnl"] == 24900.0
