from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from services.persona.trade_reflection_pipeline import (
    FactsUnavailable,
    ReflectionError,
    ReflectionRequest,
    TradeReflectionPipeline,
    due_pattern_requests,
    facts_snapshot,
    provider_extraction_schema,
    reflection_schema_errors,
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


def test_partial_reflection_marks_unknowns_and_candidate_only():
    artifact = TradeReflectionPipeline(Provider()).process(
        request(missing_refs=("attribution://episode-1",))
    )
    assert artifact["evidence_coverage"] == "partial"
    assert artifact["unknowns"] == ["missing canonical ref: attribution://episode-1"]
    assert artifact["counterfactuals"][0]["estimated_impact"] == "unknown"
    assert artifact["lesson_candidates"][0]["expiry"]
    assert artifact["review_state"] == "proposed"


@pytest.mark.parametrize(
    ("trigger", "episode_ids"),
    [
        ("episode_closed", ("episode-1",)),
        ("scheduled_pattern", ("episode-1", "episode-2")),
    ],
)
def test_process_output_conforms_to_reflection_schema(trigger, episode_ids):
    schema_path = Path(__file__).with_name("persona_trade_reflection.schema.json")
    schema = json.loads(schema_path.read_text())
    artifact = TradeReflectionPipeline(Provider()).process(
        request(trigger=trigger, trade_episode_ids=episode_ids)
    )

    jsonschema.validate(instance=artifact, schema=schema)
    assert set(artifact["expected_vs_actual"]) == {
        "thesis", "entry_quality", "exit_quality", "sizing", "timing", "risk_adherence"
    }


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


def _generated(**changes):
    base = Provider().reflect(facts={}, trigger="t")
    base["expected_vs_actual"] = {"thesis": "held", "entry_quality": "late", "exit_quality": None}
    return {**base, **changes}


class Fixed:
    name, model = "fixed", "fixed-v1"

    def __init__(self, output):
        self.output, self.calls = output, 0

    def reflect(self, *, facts, trigger):
        self.calls += 1
        return self.output


@pytest.mark.parametrize(
    "bad",
    [
        {"expected_vs_actual": {"expected": "up", "actual": "down", "comparison": "missed"}},
        {"counterfactuals": [{"scenario": "wait", "expected_effect": "better"}]},
        {"lesson_candidates": [{"lesson": "wait", "confidence": "medium", "evidence_refs": ["e1"]}]},
        {"lesson_candidates": [{"scope": "s", "proposed_change": "c", "confidence": "medium"}]},
        {"lesson_candidates": [{"scope": "s", "proposed_change": "c", "confidence": 1.5}]},
    ],
)
def test_real_provider_shapes_and_bad_confidence_are_rejected_not_nulled(bad):
    pipeline = TradeReflectionPipeline(Fixed(_generated(**bad)), max_attempts=1)
    with pytest.raises(ReflectionError):
        pipeline.process(request())
    assert pipeline.completed == {} and pipeline.dead_letters[0]["error_type"] == "ReflectionError"


def test_meaningful_provider_content_survives_canonical_validation():
    artifact = TradeReflectionPipeline(Fixed(_generated())).process(request())
    assert reflection_schema_errors(artifact) == []
    assert artifact["expected_vs_actual"]["entry_quality"] == "late"
    assert artifact["counterfactuals"][0]["alternative_action"] == "wait"
    assert artifact["lesson_candidates"][0]["confidence"] == 0.6


def test_provider_request_schema_reuses_canonical_properties():
    schema = provider_extraction_schema()
    canonical = json.loads(Path(__file__).with_name("persona_trade_reflection.schema.json").read_text())["properties"]
    assert schema["properties"]["expected_vs_actual"] == canonical["expected_vs_actual"]
    assert schema["properties"]["counterfactuals"] == canonical["counterfactuals"]
    lesson = schema["properties"]["lesson_candidates"]["items"]
    assert lesson["properties"]["confidence"] == canonical["lesson_candidates"]["items"]["properties"]["confidence"]
    assert set(lesson["properties"]) == {"scope", "proposed_change", "confidence"}


def test_unknown_root_fields_are_rejected_not_dropped():
    assert provider_extraction_schema()["additionalProperties"] is False
    pipeline = TradeReflectionPipeline(Fixed(_generated(follow_ups=["review exit"])), max_attempts=1)
    with pytest.raises(ReflectionError):
        pipeline.process(request())
    assert pipeline.completed == {} and pipeline.dead_letters[0]["error_type"] == "ReflectionError"
    artifact = TradeReflectionPipeline(Fixed(_generated(followups=["review exit"]))).process(request())
    assert artifact["followups"] == ["review exit"]
