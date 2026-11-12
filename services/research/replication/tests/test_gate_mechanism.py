"""Mechanism stage of the replication gate, on recorded official TWSE data.

Prices are the recorded TWSE daily bars (2024-01..2026-10) of 2330, 2317 and 0050 and
the events are the recorded TWSE ex-dividend calendar. Admission-path tests go through
``ReplicationGate.evaluate_candidate`` (the production entry point). The only
test-side choices are named: the split adjustment of 0050 recorded in the fixture and,
for the admission tests, a looser Holm alpha (the recorded slice is too short to reach
0.05 on any prediction; the 0.05 default is pinned separately).
"""
import copy
import json
import math
import re
from pathlib import Path

import pytest

from services.research.replication import mechanism_tests as mt
from services.research.replication import test_gate as gate_tests
from services.research.replication.gate import ReplicationGate, create_promotion_request
from services.research.replication.gate_config import GateConfig

FIXTURE = Path(__file__).parents[1] / "fixtures" / "recorded_mechanism_tw.json"
LOOSE = dict(mechanism_alpha=0.5)


def _iso(roc: str) -> str:
    y, m, d = (int(x) for x in re.findall(r"\d+", roc))
    return f"{y + 1911}-{m:02d}-{d:02d}"


def recorded():
    fx = json.loads(FIXTURE.read_text())["provenance"]
    adjust = {a["symbol"]: a for a in fx["corporate_action_adjustments"]}
    records, events = [], []
    for rec in fx["recordings"]:
        if "STOCK_DAY" in rec["url"]:
            symbol = rec["url"].split("stockNo=")[1]
            for row in rec["rows"]:
                if row[6] in ("--", ""):
                    continue
                date, close = _iso(row[0]), float(row[6].replace(",", ""))
                adj = adjust.get(symbol)
                if adj and date < adj["effective_session"]:
                    close /= adj["ratio"]
                records.append({"instrument": f"{symbol}.TW", "date": date, "open": close,
                                "high": close, "low": close, "close": close, "volume": 1})
        else:
            for row in rec["rows"]:
                day = _iso(row[0]) + "T00:00:00+08:00"
                events.append({"symbol": row[1], "event_time": day, "available_time": day,
                               "cash_dividend_value": float(row[5])})
    records.sort(key=lambda r: (r["instrument"], r["date"]))
    return {"records": records}, {"tw_ex_dividend": events}


def pred(kind, sign, dataset="tw_ex_dividend", field="cash_dividend_value", before=0, after=0):
    return {"statement": f"{kind} {sign}", "observable": {"dataset": dataset, "field": field},
            "test_kind": kind, "expected_sign": sign, "window": {"before": before, "after": after}}


EVENT = lambda sign: pred("event_window", sign)
REGIME = lambda sign: pred("regime_split", sign, dataset=mt.MARKET_PRICE_DATASET, field="close")


def stage(predictions, **config):
    dataset, inputs = recorded()
    return mt.run_mechanism_stage(predictions, dataset, inputs, config=GateConfig(**config),
                                  target_symbols=["2330"])


# ----------------------------------------------------------------------------- statistics
def test_statistics_match_reference_values():
    assert mt.t_two_sided_p(2.228138852, 10) == pytest.approx(0.05, abs=1e-6)
    assert mt.t_two_sided_p(0.0, 7) == pytest.approx(1.0)
    assert mt.binom_upper_tail(5, 5) == pytest.approx(1 / 32)
    assert mt.holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert mt.holm_adjust([0.5]) == [0.5]


# ----------------------------------------------------- recorded real data: one per kind
def test_event_window_on_recorded_ex_dividend_events_is_measured():
    result = stage([EVENT("-")])["predictions"][0]
    assert result["testable"] and result["n"] == 14  # in-sample ex-dividend events of 3 symbols
    assert result["statistic"] == pytest.approx(-0.9679, abs=1e-3)
    assert result["mean_abnormal_window_return"] == pytest.approx(-0.006357, abs=1e-5)
    assert result["p_value"] == pytest.approx(0.3508, abs=1e-3)
    # 14 events are too few to reach 0.05: recorded, not guessed
    assert result["status"] == "inconclusive" and result["holm_p_value"] == result["p_value"]


def test_regime_split_on_recorded_0050_200_day_mean_is_measured():
    result = stage([REGIME("+")])["predictions"][0]
    assert result["testable"]
    assert set(result["regimes"]) == {"above_ma", "below_ma"}
    assert result["regimes"]["above_ma"]["n"] == 210 and result["regimes"]["below_ma"]["n"] == 63
    assert result["regimes"]["above_ma"]["mean_daily_return"] == pytest.approx(0.0015486, abs=1e-6)
    assert result["status"] == "inconclusive"  # sign holds in both, neither significant


def test_default_alpha_is_five_percent_and_holm_is_within_the_card():
    assert GateConfig().mechanism_alpha == 0.05
    one = stage([EVENT("-")], **LOOSE)
    assert one["predictions"][0]["status"] == "supported"  # p=0.351 <= 0.5
    two = stage([EVENT("-"), EVENT("-")], **LOOSE)  # same p twice: Holm doubles the larger
    assert all(p["holm_p_value"] == pytest.approx(0.7015, abs=1e-3) for p in two["predictions"])
    assert all(p["status"] == "inconclusive" for p in two["predictions"])
    assert two["holm_family_size"] == 2


def test_contradicted_when_effect_is_significant_the_other_way():
    out = stage([EVENT("+")], **LOOSE)
    assert out["predictions"][0]["status"] == "contradicted"
    assert out["mechanism_status"] == "contradicted"
    assert out["mechanism_support"] == 0.0


def test_mechanism_support_is_supported_over_testable():
    # Holm over the 2 testable predictions: event 0.351 -> 0.702 (<= 0.75), regime 0.777 (> 0.75)
    out = stage([EVENT("-"), REGIME("+"), pred("dose_response", "+", dataset="tw_unrecorded")],
                mechanism_alpha=0.75)
    statuses = [p["status"] for p in out["predictions"]]
    assert statuses == ["supported", "inconclusive", "inconclusive"]
    assert out["testable_predictions"] == 2 and out["supported_predictions"] == 1
    assert out["mechanism_support"] == pytest.approx(0.5)
    assert out["mechanism_status"] == "tested"
    unrecorded = out["predictions"][2]
    assert unrecorded["testable"] is False and unrecorded["reason"] == "observable_data_missing"


def test_cross_symbol_sign_fails_closed_below_the_symbol_minimum():
    dataset, _ = recorded()
    p = pred("cross_symbol_sign", "+", dataset="tw_trailing", field="trailing_5d", after=5)
    out = mt.run_mechanism_stage([p], dataset, _price_inputs(dataset), config=GateConfig())
    assert out["predictions"][0]["reason"] == "too_few_symbols_with_enough_observations"
    assert out["mechanism_status"] == "mechanism_untested"


def test_missing_market_or_observable_data_is_untestable_never_support():
    out = mt.run_mechanism_stage([EVENT("-")], None, {}, config=GateConfig())
    assert out["mechanism_status"] == "mechanism_untested" and out["mechanism_support"] is None
    assert out["predictions"][0]["reason"] == "in_sample_market_data_missing"
    dataset, _ = recorded()
    out = mt.run_mechanism_stage([EVENT("-")], dataset, {}, config=GateConfig())
    assert out["predictions"][0]["reason"] == "observable_data_missing"
    out = mt.run_mechanism_stage([{"test_kind": "event_window"}], dataset, {}, config=GateConfig())
    assert out["predictions"][0]["reason"] == "malformed_prediction"
    needs = {**EVENT("-"), "testability": "needs_data:tw_monthly_revenue"}
    out = mt.run_mechanism_stage([needs], dataset, {}, config=GateConfig())
    assert out["predictions"][0]["reason"] == "needs_data:tw_monthly_revenue"


def _price_inputs(dataset):
    """Observable rows derived from the recorded prices: 5-session trailing return."""
    by = {}
    for rec in dataset["records"]:
        by.setdefault(rec["instrument"].split(".")[0], []).append(rec)
    rows = []
    for sym, recs in by.items():
        for i in range(5, len(recs)):
            rows.append({"symbol": sym, "event_time": recs[i]["date"] + "T13:30:00+08:00",
                         "available_time": recs[i]["date"] + "T18:00:00+08:00",
                         "trailing_5d": recs[i]["close"] / recs[i - 5]["close"] - 1.0})
    return {"tw_trailing": rows}


@pytest.mark.parametrize("kind", ["dose_response", "lead_lag", "cross_symbol_sign"])
def test_remaining_kinds_run_on_recorded_prices(kind):
    dataset, _ = recorded()
    p = pred(kind, "+", dataset="tw_trailing", field="trailing_5d", after=5)
    # the recording has 3 symbols; cross_symbol_sign needs 5 by default (fails closed below)
    out = mt.run_mechanism_stage([p], dataset, _price_inputs(dataset), config=GateConfig(mechanism_min_symbols=3),
                                 target_symbols=[])
    result = out["predictions"][0]
    assert result["testable"], result
    assert 0.0 <= result["p_value"] <= 1.0 and math.isfinite(result["statistic"])
    assert result["status"] in ("supported", "contradicted", "inconclusive")


# --------------------------------------------------------------- no out-of-sample bar is read
class _Poison:
    """A close price that raises if anything reads it."""
    def __float__(self):
        raise AssertionError("out-of-sample bar was read")

    def __getattr__(self, name):
        raise AssertionError("out-of-sample bar was read")


def test_mechanism_stage_reads_no_out_of_sample_bar():
    dataset, inputs = recorded()
    config = GateConfig()
    market = mt.InSampleMarket(dataset, config.in_sample_ratio)
    cutoff = market.cutoff
    clean = mt.run_mechanism_stage([EVENT("-"), REGIME("+")], dataset, inputs, config=config,
                                   target_symbols=["2330"])
    poisoned = copy.deepcopy(dataset)
    out_of_sample = 0
    for rec in poisoned["records"]:
        if rec["date"] > cutoff:
            for key in ("open", "high", "low", "close"):
                rec[key] = _Poison()
            out_of_sample += 1
    assert out_of_sample > 100
    result = mt.run_mechanism_stage([EVENT("-"), REGIME("+")], poisoned, inputs, config=config,
                                    target_symbols=["2330"])  # raises if any OOS bar is touched
    assert json.dumps(result, sort_keys=True) == json.dumps(clean, sort_keys=True)
    assert clean["in_sample_cutoff"] == cutoff
    # events after the cutoff are ignored too
    late = {"tw_ex_dividend": [e for e in inputs["tw_ex_dividend"] if e["available_time"][:10] > cutoff]}
    assert stage_with(dataset, late)["predictions"][0]["reason"] == "too_few_in_sample_events"


def stage_with(dataset, inputs):
    return mt.run_mechanism_stage([EVENT("-")], dataset, inputs, config=GateConfig(), target_symbols=["2330"])


# ------------------------------------------------------------------------- production path
def gate_request(gate, predictions, *, with_data=True):
    request = gate_tests.TestReplicationGate._build_request(None)
    gate_tests._bind_run(request, gate)
    if predictions is not None:
        request.research_handoff["hypothesis_predictions"] = predictions
    if with_data:
        dataset, inputs = recorded()
        dataset["dataset_id"] = request.backtest_evaluation["dataset_id"]  # same governed dataset id
        request.research_handoff["backtest_dataset"] = dataset
        request.research_handoff["mechanism_inputs"] = inputs
    return request


def evaluate(predictions, *, with_data=True, **config):
    gate = ReplicationGate(config=GateConfig(**config))
    request = gate_request(gate, predictions, with_data=with_data)
    return gate, gate.evaluate_candidate(request)


def result_of(response, criterion):
    return next(r for r in response.results if r.criterion_id == criterion)


def test_baseline_without_predictions_is_admitted_as_mechanism_untested():
    _, response = evaluate(None)
    assert response.passed
    assert response.metadata["mechanism_status"] == "mechanism_untested"
    assert response.metadata["mechanism_support"] is None
    assert result_of(response, "mechanism_stage").passed


def test_contradicted_prediction_refuses_admission_with_recorded_reason():
    _, baseline = evaluate(None)
    _, response = evaluate([EVENT("+")], **LOOSE)
    assert not response.passed and response.admission_status.value == "rejected"
    stage_result = result_of(response, "mechanism_stage")
    assert not stage_result.passed and "contradicted" in stage_result.evidence
    assert "contradicted card prediction" in response.summary
    assert response.metadata["mechanism_status"] == "contradicted"
    # the primary rule checks are otherwise unchanged
    for criterion in ("real_backtest_validation", "schema_validity", "lineage_complete",
                      "governance_context", "no_live_bypass"):
        assert result_of(response, criterion).passed == result_of(baseline, criterion).passed
    base_bt = result_of(baseline, "real_backtest_validation")
    new_bt = result_of(response, "real_backtest_validation")
    assert new_bt.passed and new_bt.details["checks"] == base_bt.details["checks"]


def test_supported_prediction_is_admitted_and_support_is_recorded_on_the_admission():
    gate, response = evaluate([EVENT("-"), REGIME("+")], mechanism_alpha=0.75)
    assert response.passed
    assert response.metadata["mechanism_support"] == pytest.approx(0.5)
    assert response.metadata["mechanism_status"] == "tested"
    promotion = create_promotion_request(response, gate_request(gate, None).proposed_strategy_spec)
    assert promotion.registry_entry["mechanism_support"] == pytest.approx(0.5)
    assert promotion.registry_entry["mechanism_status"] == "tested"
    assert promotion.replication_proof["mechanism"]["testable_predictions"] == 2
    json.dumps(response.to_dict())  # the admission record is serialisable


def test_predictions_without_data_are_admitted_as_mechanism_untested():
    _, response = evaluate([EVENT("-")], with_data=False)
    assert response.passed
    assert response.metadata["mechanism_status"] == "mechanism_untested"
    assert response.metadata["mechanism"]["predictions"][0]["reason"] == "in_sample_market_data_missing"


def test_mechanism_stage_runs_before_the_backtest_and_the_backtest_is_not_used_by_it():
    gate = ReplicationGate(config=GateConfig(**LOOSE))
    order = []
    stage_fn, backtest_fn = gate._run_mechanism_stage, gate._resolve_or_run_backtest
    gate._run_mechanism_stage = lambda r: order.append("mechanism") or stage_fn(r)
    gate._resolve_or_run_backtest = lambda r: order.append("backtest") or backtest_fn(r)
    gate.evaluate_candidate(gate_request(gate, [EVENT("-")]))
    assert order[0] == "mechanism" and "backtest" in order

    gate._resolve_or_run_backtest = lambda r: (_ for _ in ()).throw(AssertionError("backtest ran"))
    gate._run_mechanism_stage(gate_request(gate, [EVENT("-")]))  # must not touch the backtest


def test_production_module_synthesizes_no_data():
    source = (Path(mt.__file__)).read_text()
    assert not re.search(r"^\s*(import|from)\s+(random|numpy)", source, re.M)
    assert "random" not in source.split('"""', 2)[2]
