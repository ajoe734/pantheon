"""Recorded TWSE bars: changing member picks and paper/research target parity."""
import copy

import pytest

from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import _binding
from services.execution.lean_runtime.test_portfolio_target_positions import cash_candidates
from services.registry.candidate_pool import MARKET_FEATURE_KEY, build_candidate_pool_snapshot
from services.registry.portfolio_envelope import build_portfolio_envelope
from services.registry.strategy_artifact import strategy_artifact_checksum, validate_strategy_artifact
from services.registry.test_candidate_pool import recorded_market, screen_artifact
from services.registry.test_portfolio_envelope import member_artifact
from services.research.tests.test_candidate_pool_parity import recorded_dataset, paper_binding
from services.research.vectorbt.adapter.vectorbt_adapter import (
    GovernedVectorbtInputAdapter, _bar_features_for_index, _portfolio_target_sequence,
    _artifact_order_sizes, BacktestConfig, VectorbtBackend, run_vectorbt_workflow,
)


def target_envelope():
    # A and B overlap on 2330; 2317 belongs only to A. Screen picks are not pinned.
    a = member_artifact("a", symbols=["2330.TW", "2317.TW"])
    b = member_artifact("b", symbols=["2330.TW"], bearish=True)
    screen = screen_artifact()
    screen["parameters"]["symbols"] = ["2330.TW"]
    screen["parameters"]["data_source"] = a["parameters"]["data_source"]
    return build_portfolio_envelope(artifact_id="target-recorded", strategy_id="target-recorded",
        version="1.0.0", members=[
            {"member_id": "a", "artifact": a, "risk_weight": .4},
            {"member_id": "b", "artifact": b, "risk_weight": .2},
            {"member_id": "screen", "artifact": screen, "risk_weight": .4}])


def test_screen_only_pins_rules_and_weights_not_daily_picks():
    screen = screen_artifact()
    artifact = build_portfolio_envelope(artifact_id="screen-only", strategy_id="screen-only",
        version="1.0.0", members=[{"member_id": "screen", "artifact": screen, "risk_weight": 1}])
    validate_strategy_artifact(artifact)
    assert artifact["parameters"]["symbols"] == []
    assert artifact["expression_policy"]["kind"] == "target_position_v1"
    before = strategy_artifact_checksum(artifact)
    binding = paper_binding(artifact, recorded_market(), "2026-09-02")
    strategy = CurrentArtifactStrategy(expression_layer=True, candidates_builder=cash_candidates,
        expression_inputs=lambda _: {"persona_book": {}, "risk_budget": {
            "gross_limit": 100000, "available_cash": 100000}})
    outputs = strategy(binding, "2026-09-02T12:00:00Z")["portfolio_decisions"]
    assert any(o["symbol"] == "2317.TW" for o in outputs)
    assert before == strategy_artifact_checksum(artifact)
    expected = build_candidate_pool_snapshot(screen, recorded_market(), "2026-09-02")
    assert all(o["metadata"]["member_candidate_pool_digests"] == {"screen": expected["digest"]} for o in outputs)


def test_recorded_paper_backtest_targets_replay_and_member_exits_match(monkeypatch):
    artifact = target_envelope()
    dataset = recorded_dataset()
    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    research = _portfolio_target_sequence(prepared, artifact)
    member_exits = 0
    digests = set()
    for index, bar in enumerate(research["bars"]):
        if bar is None:
            continue
        day = prepared.dates_by_instrument["2330.TW"][index]
        binding = paper_binding(artifact, dataset["candidate_pool_market"], day)
        # Include the exact dated bar features used by production's market input path.
        for inst in prepared.instruments:
            features, _ = _bar_features_for_index(prepared, inst, index)
            binding["market_inputs"][inst]["features"] = features
        strategy = CurrentArtifactStrategy(expression_layer=True, candidates_builder=cash_candidates,
            expression_inputs=lambda _: {"persona_book": bar["book_before"], "risk_budget": bar["budget"]})
        outputs = strategy(binding, f"{day}T12:00:00Z")["portfolio_decisions"]
        for out in outputs:
            assert out["metadata"]["bar_allocation"] == bar["allocation"]
            result = bar["results"][out["symbol"]]
            assert out["metadata"]["portfolio_net_view"] == result["view"]
            digests.update(out["metadata"]["member_candidate_pool_digests"].values())
            member_exits += any(m["view"].get("exit_reason") for m in out["metadata"]["portfolio_member_views"].values())
            actual = out["expression"]["evaluate"]()["decision"]
            expected = bar["decisions"][out["symbol"]]
            assert {k: v for k, v in actual.items() if k != "decision_id"} == {
                k: v for k, v in expected.items() if k != "decision_id"}
    assert member_exits > 0 and len(digests) > 1
    assert _artifact_order_sizes(prepared, artifact) == research["orders"]
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    result = run_vectorbt_workflow(dataset, backend=VectorbtBackend(),
                                   config=BacktestConfig(strategy_artifact=artifact))
    assert result.backtest_result.backend == "vectorbt_portfolio"


def test_missing_complete_market_and_budget_fail_closed():
    artifact = target_envelope()
    binding = paper_binding(artifact, recorded_market(), "2026-09-02")
    del binding["market_inputs"]["2317.TW"]
    store = InMemoryPendingSignalStore()
    producer = PaperSignalProducer(lambda _: store, CurrentArtifactStrategy(expression_layer=True))
    assert producer.produce(binding, "2026-09-02T12:00:00Z") == 0
    assert store.queue_depth() == 0 and producer.degraded_bindings
    binding = paper_binding(artifact, recorded_market(), "2026-09-02")
    strategy = CurrentArtifactStrategy(expression_layer=True, candidates_builder=cash_candidates)
    outputs = strategy(binding, "2026-09-02T12:00:00Z")["portfolio_decisions"]
    assert all(o["expression"]["evaluate"]()["decision"]["reason_code"] == "candidates_unavailable" for o in outputs)
