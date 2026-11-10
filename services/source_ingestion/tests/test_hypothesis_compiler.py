"""HYPOTHESIS-RULE-COMPILER-20261110: card -> factor_rule_v1 or explicit refusal."""

from __future__ import annotations

import copy

from services.source_ingestion.hypothesis_compiler import (
    COMPILED,
    NEEDS_DATA,
    NOT_EXPRESSIBLE,
    SUPPORT_ADDED,
    CompilerStore,
    HypothesisCompiler,
)


def _ctl(key, cur, lo, hi, step):
    return {"parameter_key": key, "value_type": "number", "current_value": cur,
            "allowed_range": {"min": lo, "max": hi}, "step": step}


def _proposal(dataset="tw_monthly_revenue", field="yoy", op="ts_zscore"):
    return {
        "outcome": "factor_rule",
        "strategy_logic": {
            "kind": "factor_rule_v1",
            "inputs": [{"name": "x", "dataset": dataset, "field": field, "availability_lag_days": 10}],
            "signal": {"op": op, "args": [{"ref": "x"}, {"const_param": "w"}]},
            "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": "z"}]}, "short_when": None},
            "exit": {"max_holding_bars": {"const_param": "h"}, "flat_when": None},
            "strength": {"op": "clip01", "args": [{"op": "abs", "args": [{"ref": "signal"}]}]},
        },
        "parameters": {"w": 4, "z": 0.8, "h": 3},
        "mutation_surface": {
            "controls": [_ctl("w", 4, 2, 24, 1), _ctl("z", 0.8, 0.2, 3.0, 0.1), _ctl("h", 3, 1, 20, 1)],
            "immutable_parameters": [],
        },
    }


def _card(n=1, testability="testable"):
    s = f"span {n}"
    return {"claim": f"claim {n}", "testability": testability,
            "cited_spans": [{"source_record_id": f"src-{n}", "start": 0, "end": len(s), "exact_text": s}]}


def _compiler(proposal):
    return HypothesisCompiler(lambda card, cat: copy.deepcopy(proposal), CompilerStore())


def test_valid_proposal_compiles():
    out = _compiler(_proposal()).compile(_card())
    assert out.status == COMPILED and out.artifact and out.support_count == 1


def test_unknown_operator_and_free_code_refused():
    out = _compiler(_proposal(op="exec_python")).compile(_card())
    assert out.status == NOT_EXPRESSIBLE and "invalid_factor_rule" in out.reason
    p = _proposal()
    p["code"] = "__import__('os')"
    assert _compiler(p).compile(_card()).status == NOT_EXPRESSIBLE


def test_unknown_dataset_not_accepted_and_counted():
    c = _compiler(_proposal(dataset="tw_tdcc_holders", field="x"))
    out = c.compile(_card())
    assert out.status == NEEDS_DATA and out.artifact is None
    assert c.store.needs_data_report() == [{"dataset": "tw_tdcc_holders", "count": 1}]
    assert c.store.drafts() == []


def test_unknown_field_of_known_dataset_not_expressible():
    assert _compiler(_proposal(field="nope")).compile(_card()).status == NOT_EXPRESSIBLE


def test_same_fingerprint_increases_support():
    c = _compiler(_proposal())
    assert c.compile(_card(1)).status == COMPILED
    out = c.compile(_card(2))
    assert out.status == SUPPORT_ADDED and out.support_count == 2
    c.compile(_card(2))  # replay of same card does not double count
    drafts = c.store.drafts()
    assert len(drafts) == 1 and drafts[0]["support_count"] == 2


def test_needs_data_counted_per_dataset_sorted():
    c = _compiler({"outcome": "not_expressible", "reason": "x"})
    for n in range(3):
        c.compile(_card(n, "needs_data:tw_tdcc"))
    c.compile(_card(9, "needs_data:tw_news"))
    c.compile(_card(9, "needs_data:tw_news"))
    assert c.store.needs_data_report() == [
        {"dataset": "tw_tdcc", "count": 3}, {"dataset": "tw_news", "count": 1}]


def test_model_declared_refusals_and_proposer_failure():
    assert _compiler({"outcome": "not_expressible", "reason": "needs text"}).compile(_card()).reason == "needs text"
    out = _compiler({"outcome": "needs_data", "dataset": "tw_tdcc"}).compile(_card())
    assert out.status == NEEDS_DATA and out.dataset == "tw_tdcc"
    bad = HypothesisCompiler(lambda *_: (_ for _ in ()).throw(RuntimeError("x")), CompilerStore())
    assert bad.compile(_card()).status == "skipped"
