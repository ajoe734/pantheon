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


def test_non_sequence_inputs_is_not_expressible():
    for bad in (5, {"a": 1}, "abc"):
        p = _proposal()
        p["strategy_logic"]["inputs"] = bad
        out = _compiler(p).compile(_card())
        assert out.status == NOT_EXPRESSIBLE, bad


def test_card_declared_needs_data_invalid_dataset_refused():
    comp = _compiler(_proposal())
    out = comp.compile(_card(testability="needs_data:bad name; drop"))
    assert out.status == NOT_EXPRESSIBLE
    assert comp.store.needs_data_report() == []


# --- DistillationWorker wiring -------------------------------------------------
import json  # noqa: E402

from services.source_ingestion.distillation_worker import DistillationJobQueue, DistillationWorker  # noqa: E402
from services.source_ingestion.hypothesis_card import ExtractionLedger, HypothesisExtractor  # noqa: E402
from services.source_ingestion.semantic_extraction_client import SemanticExtractionClient  # noqa: E402
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore  # noqa: E402
from services.source_ingestion.tests.test_hypothesis_extraction import (  # noqa: E402
    _Transport, _card as _ext_card, _run, _source, _RecordedTransport, _prediction_recording, _card_with_origin,
)


def _wired(tmp_path, proposal, testability="testable"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = _Transport({"is_abstained": False, "abstention_reason": None,
                    "hypothesis_card": _ext_card(testability=testability)})
    queue = DistillationJobQueue(tmp_path / "q.jsonl")
    store = StrategySpecSeedStore(tmp_path / "s.jsonl")
    ex = HypothesisExtractor(SemanticExtractionClient(transport_fn=t), ExtractionLedger(tmp_path / "l.sqlite"))
    comp = _compiler(proposal)
    return DistillationWorker(queue, store, hypothesis_extractor=ex, hypothesis_compiler=comp), store, comp


def test_worker_compiles_card_into_seed_metadata(tmp_path):
    w, store, comp = _wired(tmp_path, _proposal())
    res = _run(w, _source())
    assert res.created == 1
    assert "factor_rule_v1" in json.dumps(store.list_all()[0].to_dict())
    assert comp.store.drafts()[0]["support_count"] == 1


def test_worker_skips_not_expressible_and_needs_data(tmp_path):
    w, store, _ = _wired(tmp_path / "a", _proposal(op="exec_python"))
    res = _run(w, _source())
    assert res.created == 0 and res.skipped == 1 and store.list_all() == []
    w2, store2, comp2 = _wired(tmp_path / "b", _proposal(), testability="needs_data:monthly_revenue")
    res2 = _run(w2, _source())
    assert res2.created == 0 and res2.skipped == 1 and store2.list_all() == []
    assert comp2.store.needs_data_report() == [{"dataset": "monthly_revenue", "count": 1}]


def test_worker_duplicate_fingerprint_increments_support_without_new_draft(tmp_path):
    w, store, comp = _wired(tmp_path, _proposal())
    _run(w, _source("src-1"))
    comp.compile(_card(7))  # distinct card, same rule structure
    assert comp.store.drafts()[0]["support_count"] == 2
    assert len(store.list_all()) == 1


def test_predictions_do_not_change_proposer_input_artifact_or_fingerprint():
    from services.source_ingestion.hypothesis_compiler import build_compile_prompt, catalogue_summary, card_key

    card = _prediction_recording()["response"]["output"]["structured_data"]["hypothesis_card"]
    legacy = {k: v for k, v in card.items() if k != "predictions"}
    received = []

    def propose(proposal_card, catalogue):
        received.append(copy.deepcopy(proposal_card))
        return _proposal()

    c = HypothesisCompiler(propose, CompilerStore())
    with_predictions = c.compile(card)
    without_predictions = HypothesisCompiler(propose, CompilerStore()).compile(legacy)
    assert received == [legacy, legacy]
    assert with_predictions.artifact == without_predictions.artifact
    assert with_predictions.fingerprint == without_predictions.fingerprint
    assert card_key(card) == card_key(legacy)
    assert build_compile_prompt(card, catalogue_summary()) == build_compile_prompt(legacy, catalogue_summary())
    # Cache identity remains the original card identity; adding predictions is no new support.
    assert c.compile(legacy).support_count == 1
    assert c.compile(card).support_count == 1
    assert c.store.needs_data_report() == [
        {"dataset": "tw_monthly_revenue", "count": 1}, {"dataset": "unlisted_orders", "count": 1},
    ]


def test_prediction_demand_counted_even_after_rule_cached_and_not_testable():
    recording = _prediction_recording()
    card = recording["response"]["output"]["structured_data"]["hypothesis_card"]
    legacy = {k: v for k, v in card.items() if k != "predictions"}
    c = _compiler(_proposal())
    c.compile(legacy)
    assert c.store.needs_data_report() == []
    assert c.compile(card).extra["cached"] is True
    # Multiple predictions for a dataset, repeated cards and rule demand share one count.
    card["predictions"].append(copy.deepcopy(card["predictions"][2]))
    card["testability"] = "needs_data:unlisted_orders"
    assert c.compile(card).status == NEEDS_DATA
    card["testability"] = "not_testable"
    assert c.compile(card).status == "skipped"
    assert c.store.needs_data_report() == [
        {"dataset": "tw_monthly_revenue", "count": 1}, {"dataset": "unlisted_orders", "count": 1},
    ]


def test_worker_prediction_demand_and_support_remain_independent(tmp_path):
    transport = _RecordedTransport()
    queue = DistillationJobQueue(tmp_path / "q.jsonl")
    seeds = StrategySpecSeedStore(tmp_path / "s.jsonl")
    ledger = ExtractionLedger(tmp_path / "l.sqlite")
    extractor = HypothesisExtractor(SemanticExtractionClient(transport_fn=transport), ledger)
    compiler = _compiler(_proposal())
    worker = DistillationWorker(queue, seeds, hypothesis_extractor=extractor, hypothesis_compiler=compiler)
    assert _run(worker, _source()).created == 1
    stored_card = _card_with_origin(seeds.list_all()[0])
    assert stored_card["predictions"][2]["testability"] == "needs_data:unlisted_orders"
    original_draft = compiler.store.drafts()[0]
    assert "predictions" not in original_draft["artifact"]
    compiler.compile(stored_card)  # replay is idempotent
    assert compiler.store.needs_data_report() == [
        {"dataset": "tw_monthly_revenue", "count": 1}, {"dataset": "unlisted_orders", "count": 1},
    ]
    # A distinct source supporting the same rule adds one support, not a new rule.
    second = transport.recording["response"]["output"]["structured_data"]["hypothesis_card"]
    for span in second["cited_spans"]:
        span["source_record_id"] = "src-2"
    for prediction in second["predictions"]:
        for span in prediction["cited_spans"]:
            span["source_record_id"] = "src-2"
    assert _run(worker, _source("src-2")).skipped == 1
    drafts = compiler.store.drafts()
    assert len(drafts) == 1 and drafts[0]["support_count"] == 2
    assert drafts[0]["fingerprint"] == original_draft["fingerprint"]
    assert compiler.store.needs_data_report() == [
        {"dataset": "tw_monthly_revenue", "count": 2}, {"dataset": "unlisted_orders", "count": 2},
    ]
    assert len(seeds.list_all()) == 1


def test_controller_tick_uses_predictions_through_environment_wiring(tmp_path, monkeypatch):
    import sqlite3
    from services.source_ingestion import semantic_extraction_client as sec
    from services.source_ingestion.tests.test_hypothesis_compiler_wiring import _tick

    recording = _prediction_recording()
    calls = []

    def transport(payload):
        calls.append(payload)
        if payload["prompt"].startswith("Propose a factor_rule_v1"):
            return {"output": {"structured_data": _proposal()}}
        return copy.deepcopy(recording["response"])

    real_client = sec.SemanticExtractionClient

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport_fn=transport, **kwargs)

    monkeypatch.setattr(sec, "SemanticExtractionClient", Client)
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_ENABLED", "1")
    result, config = _tick(tmp_path, monkeypatch)
    assert result["status"] == "success" and result["reconcile"]["created"] == 1
    assert len(calls) == 2
    assert all(p["agent_id"] == "structured-extraction" and "tools" not in p for p in calls)
    assert "two to four independently testable predictions" in calls[0]["prompt"]
    assert "predictions" not in calls[1]["prompt"]
    seeds = [json.loads(line) for line in config.seed_store_path.read_text().splitlines()]
    text = json.dumps(seeds)
    assert "needs_data:unlisted_orders" in text and "rule_fingerprint" in text
    with sqlite3.connect(config.seed_store_path.with_name("hypothesis_compiler.sqlite")) as conn:
        assert conn.execute("SELECT dataset,count FROM needs_data_counts ORDER BY dataset").fetchall() == [
            ("tw_monthly_revenue", 1), ("unlisted_orders", 1),
        ]
        assert conn.execute("SELECT support_count FROM rule_drafts").fetchall() == [(1,)]
