from __future__ import annotations

import pytest

from services.persona.trade_reflection_pipeline import (
    FactsUnavailable,
    ReflectionError,
    ReflectionRequest,
    TradeReflectionPipeline,
    due_pattern_requests,
    facts_snapshot,
)


class Provider:
    name = "test-provider"
    model = "test-model-v1"

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    def reflect(self, *, facts, trigger):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("provider timeout")
        return {
            "expected_vs_actual": {"thesis": "supported by supplied facts"},
            "attribution": "process",
            "counterfactuals": [{"alternative_action": "wait", "estimated_impact": "unknown", "assumptions": "same market"}],
            "lesson_candidates": [{"scope": "strategy", "proposed_change": "review entry timing", "confidence": 0.6}],
        }


def request(**changes):
    values = {
        "request_id": "req-1",
        "persona_id": "persona-a",
        "trade_episode_ids": ("episode-1",),
        "trigger": "episode_closed",
        "facts": {"status": "closed", "realized_pnl": 12.5, "decision_ref": "decision://1"},
    }
    values.update(changes)
    return ReflectionRequest(**values)


def test_no_facts_never_calls_provider():
    provider = Provider()
    pipeline = TradeReflectionPipeline(provider)
    with pytest.raises(FactsUnavailable):
        pipeline.process(request(facts={}))
    assert provider.calls == 0


def test_snapshot_is_stable_and_detached_from_input():
    facts = {"b": [2, 1], "a": {"value": 3}}
    ref1, hash1, snapshot = facts_snapshot(facts)
    facts["a"]["value"] = 99
    ref2, hash2, _ = facts_snapshot({"a": {"value": 3}, "b": [2, 1]})
    assert (ref1, hash1) == (ref2, hash2)
    assert snapshot["a"]["value"] == 3


def test_partial_reflection_marks_unknowns_counterfactuals_and_candidate_only():
    artifact = TradeReflectionPipeline(Provider()).process(
        request(missing_refs=("attribution://episode-1",))
    )
    assert artifact["evidence_coverage"] == "partial"
    assert artifact["unknowns"] == ["missing canonical ref: attribution://episode-1"]
    assert artifact["counterfactuals"][0]["is_counterfactual"] is True
    assert artifact["hindsight_guard"]["outcome_is_not_decision_quality"] is True
    assert artifact["lesson_candidates"][0]["review_state"] == "proposed"
    assert artifact["lesson_candidates"][0]["mutation_authority"] is False


def test_retry_is_idempotent_after_success_and_dead_letters_terminal_failure():
    provider = Provider(failures=1)
    pipeline = TradeReflectionPipeline(provider, max_attempts=2)
    with pytest.raises(ReflectionError):
        pipeline.process(request())
    first = pipeline.process(request())
    assert pipeline.process(request()) == first
    assert provider.calls == 2

    failing = TradeReflectionPipeline(Provider(failures=3), max_attempts=2)
    with pytest.raises(ReflectionError):
        failing.process(request())
    with pytest.raises(ReflectionError):
        failing.process(request())
    assert failing.dead_letters[0]["attempts"] == 2
    assert failing.dead_letters[0]["facts_snapshot_hash"].startswith("sha256:")


def test_pattern_review_requires_multiple_episodes_and_scheduler_threshold():
    pipeline = TradeReflectionPipeline(Provider())
    with pytest.raises(FactsUnavailable):
        pipeline.process(request(trigger="scheduled_pattern"))
    episodes = [
        {"persona_id": "p1", "trade_episode_id": f"e{i}", "status": "closed"}
        for i in range(3)
    ] + [{"persona_id": "p2", "trade_episode_id": "open", "status": "open"}]
    assert due_pattern_requests(episodes) == [("p1", ("e0", "e1", "e2"))]
