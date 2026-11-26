"""Cross-service contract: source-ingestion schemas vs. the adapter validator.

L2-HYPOTHESIS-CARD-ADAPTER-SCHEMA-CONTRACT-20261126. On hosted, 109 of 112 paid
hypothesis-card answers were refused with OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH
because the card schema uses ``anyOf`` and the adapter's structured-argument
validator did not support it. No stub on either side here: the real schema
builders from ``services.source_ingestion`` run through the real adapter
validator, and every answer is cross-checked against ``jsonschema`` (the
validator the source-ingestion client applies to the same answer), so the two
sides cannot drift apart silently again.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import jsonschema
import pytest

ADAPTER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (ADAPTER_DIR, REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from assistant_openclaw_provider import (  # noqa: E402
    OpenClawProviderError,
    _validate_extraction_arguments,
    unsupported_schema_keyword_paths,
)
from services.source_ingestion.hypothesis_card import (  # noqa: E402
    ABSTENTION_REASON_MAX_CHARS,
    build_hypothesis_prompt,
    hypothesis_card_model_schema,
)
from services.source_ingestion.semantic_extraction import (  # noqa: E402
    ExtractionTaskType,
    get_semantic_extraction_json_schema,
)

SPAN = {"source_record_id": "src-1", "start": 0, "end": 5, "exact_text": "Firms"}
CARD = {
    "claim": "Post-earnings drift persists for small caps.",
    "mechanism": "Investors under-react to earnings surprises.",
    "signal_inputs": ["earnings_surprise"],
    "rule_sketch": "long top decile surprise",
    "horizon": "60d",
    "universe": "US small caps",
    "direction": "long",
    "conditions": [],
    "cited_spans": [SPAN],
    "testability": "testable",
}


def _answer(**over):
    answer = {"is_abstained": False, "abstention_reason": None, "hypothesis_card": copy.deepcopy(CARD)}
    answer.update(over)
    return answer


def _adapter_accepts(answer, schema) -> bool:
    try:
        _validate_extraction_arguments(answer, schema)
    except OpenClawProviderError as error:
        assert error.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        assert error.status_code == 422
        return False
    return True


def _assert_both_sides(answer, schema, *, accepted: bool) -> None:
    assert _adapter_accepts(answer, schema) is accepted
    assert jsonschema.Draft7Validator(schema).is_valid(answer) is accepted


def _without(field):
    card = copy.deepcopy(CARD)
    card.pop(field)
    return card


class TestHypothesisCardThroughRealAdapterValidator:
    def test_valid_card_answer_passes(self):
        _assert_both_sides(_answer(), hypothesis_card_model_schema(), accepted=True)

    def test_abstained_answer_with_null_card_passes(self):
        answer = _answer(is_abstained=True, abstention_reason="no testable mechanism", hypothesis_card=None)
        _assert_both_sides(answer, hypothesis_card_model_schema(), accepted=True)

    @pytest.mark.parametrize("field", ["claim", "cited_spans", "testability"])
    def test_card_missing_required_field_is_refused(self, field):
        _assert_both_sides(_answer(hypothesis_card=_without(field)), hypothesis_card_model_schema(), accepted=False)

    @pytest.mark.parametrize("key", ["origin", "tool_call", "instructions"])
    def test_card_with_extra_field_is_refused(self, key):
        card = {**copy.deepcopy(CARD), key: "x"}
        _assert_both_sides(_answer(hypothesis_card=card), hypothesis_card_model_schema(), accepted=False)

    def test_non_object_non_null_card_is_refused(self):
        _assert_both_sides(_answer(hypothesis_card="a card"), hypothesis_card_model_schema(), accepted=False)

    def test_over_long_abstention_reason_stays_a_refusal(self):
        schema = hypothesis_card_model_schema()
        assert schema["properties"]["abstention_reason"]["maxLength"] == ABSTENTION_REASON_MAX_CHARS
        limit = "x" * ABSTENTION_REASON_MAX_CHARS
        _assert_both_sides(_answer(is_abstained=True, abstention_reason=limit, hypothesis_card=None),
                           schema, accepted=True)
        _assert_both_sides(_answer(is_abstained=True, abstention_reason=limit + "x", hypothesis_card=None),
                           schema, accepted=False)

    def test_prompt_states_the_abstention_reason_limit(self):
        prompt = build_hypothesis_prompt("Firms drift.", source_record_id="src-1", origin="paper")
        assert f"at most {ABSTENTION_REASON_MAX_CHARS} characters" in prompt


@pytest.mark.parametrize("task_type", list(ExtractionTaskType), ids=lambda t: t.value)
def test_every_schema_sent_to_structured_passes_the_adapter_keyword_check(task_type):
    """Every schema source-ingestion clients send to ``/structured`` (the
    hypothesis card and each semantic extraction schema, all built by
    ``get_semantic_extraction_json_schema``) uses only keywords the adapter
    validates."""
    schema = get_semantic_extraction_json_schema(task_type)
    assert unsupported_schema_keyword_paths(schema) == []


@pytest.mark.parametrize(
    "task_type",
    [ExtractionTaskType.INTENT, ExtractionTaskType.STRATEGY_SEED,
     ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE],
    ids=lambda t: t.value,
)
def test_semantic_extraction_abstention_conditional_agrees_with_jsonschema(task_type):
    schema = get_semantic_extraction_json_schema(task_type)
    abstained = {"is_abstained": True, "abstention_reason": "insufficient_evidence", "source_spans": []}
    _assert_both_sides(abstained, schema, accepted=True)
    # Abstained without a reason: the `else` branch requires abstention_reason.
    _assert_both_sides({"is_abstained": True, "source_spans": []}, schema, accepted=False)
    # Not abstained but no payload and no spans: the `then` branch refuses it.
    _assert_both_sides({"is_abstained": False, "source_spans": []}, schema, accepted=False)
