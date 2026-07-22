"""Contract and schema integration tests for Persona Trade Reflections and Lesson Candidates."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
import pytest

try:
    import jsonschema
except ImportError:
    jsonschema = None

REPO_ROOT = Path(__file__).resolve().parents[2]
REFLECTION_SCHEMA_PATH = REPO_ROOT / "services" / "persona" / "persona_trade_reflection.schema.json"
LESSON_SCHEMA_PATH = REPO_ROOT / "services" / "persona" / "trade_lesson_candidate.schema.json"


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_persona_trade_reflection_schema_checks() -> None:
    schema = load_schema(REFLECTION_SCHEMA_PATH)
    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "PersonaTradeReflection"
    assert "reflection_id" in schema["required"]
    assert "expected_vs_actual" in schema["required"]
    assert "lesson_candidates" in schema["required"]


def test_validate_valid_persona_trade_reflection() -> None:
    schema = load_schema(REFLECTION_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # A complete valid reflection fixture
    valid_reflection = {
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_id": str(uuid.uuid4()),
        "persona_id": "persona-macro",
        "reflection_version": 1,
        "trigger": "episode_closed",
        "facts_snapshot_ref": "fact-snap-123",
        "facts_snapshot_hash": "sha256-abc123xyz789",
        "expected_vs_actual": {
            "thesis": "matched",
            "entry_quality": "good",
            "exit_quality": "fair",
            "sizing": "appropriate",
            "timing": "slightly late exit",
            "risk_adherence": "strict"
        },
        "counterfactuals": [
            {
                "alternative_action": "Exit 1 hour earlier at target",
                "estimated_impact": "+50 USD",
                "assumptions": "Liquidity was sufficient at that price"
            }
        ],
        "attribution": "process",
        "mistakes": ["delayed exit execution"],
        "what_worked": ["disciplined entry at key support level"],
        "unknowns": ["sudden late-day volume spike cause"],
        "followups": ["evaluate shorter catalyst observation window"],
        "lesson_candidates": [
            {
                "lesson_candidate_id": str(uuid.uuid4()),
                "scope": "strategy",
                "proposed_change": "Reduce holding period target by 2 hours",
                "supporting_episode_ids": [str(uuid.uuid4())],
                "confidence": 0.75,
                "expiry": "2026-08-11T00:00:00Z"
            }
        ],
        "model": "claude-3-5-sonnet",
        "provider": "anthropic",
        "prompt_template": "episode-close-reflection-v1",
        "version": "1.0.0",
        "generated_at": "2026-07-11T13:00:00Z",
        "evidence_coverage": "complete",
        "review_state": "proposed"
      }

    jsonschema.validate(instance=valid_reflection, schema=schema)


def test_trade_lesson_candidate_schema_checks() -> None:
    schema = load_schema(LESSON_SCHEMA_PATH)
    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "TradeLessonCandidate"
    assert "lesson_candidate_id" in schema["required"]
    assert "review_state" in schema["required"]


def test_validate_valid_lesson_candidate_with_governance_receipt() -> None:
    schema = load_schema(LESSON_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # A valid lesson candidate with an endorsed governance receipt
    valid_lesson = {
        "lesson_candidate_id": str(uuid.uuid4()),
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        "persona_id": "persona-macro",
        "scope": "portfolio",
        "proposed_change": "Limit correlation between strategy A and B to 0.7",
        "confidence": 0.85,
        "review_state": "endorsed",
        "created_at": "2026-07-11T13:00:00Z",
        "updated_at": "2026-07-11T14:30:00Z",
        "expiry": "2026-09-11T00:00:00Z",
        "reflection_version": "v1",
        "receipt": {
            "operator_id": "operator-alice",
            "decided_at": "2026-07-11T14:30:00Z",
            "action": "endorse",
            "reason": "Correlation limit is required to prevent portfolio drawdown",
            "audit_receipt_id": str(uuid.uuid4())
        }
    }

    jsonschema.validate(instance=valid_lesson, schema=schema)


def test_reflection_schema_negative_cases() -> None:
    schema = load_schema(REFLECTION_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # Case 1: Invalid review_state
    invalid_reflection = {
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_id": str(uuid.uuid4()),
        "persona_id": "persona-macro",
        "reflection_version": 1,
        "trigger": "episode_closed",
        "facts_snapshot_ref": "fact-snap-123",
        "facts_snapshot_hash": "sha256-abc123xyz789",
        "expected_vs_actual": {
            "thesis": "matched",
            "entry_quality": "good",
            "exit_quality": "fair",
            "sizing": "appropriate",
            "timing": "slightly late exit",
            "risk_adherence": "strict"
        },
        "counterfactuals": [],
        "attribution": "process",
        "mistakes": [],
        "what_worked": [],
        "unknowns": [],
        "followups": [],
        "lesson_candidates": [],
        "model": "claude-3-5-sonnet",
        "provider": "anthropic",
        "prompt_template": "episode-close-reflection-v1",
        "version": "1.0.0",
        "generated_at": "2026-07-11T13:00:00Z",
        "evidence_coverage": "complete",
        "review_state": "invalid_review_state"
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_reflection, schema=schema)


def test_lesson_candidate_schema_negative_cases() -> None:
    schema = load_schema(LESSON_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # Case 1: Missing proposed_change
    invalid_lesson = {
        "lesson_candidate_id": str(uuid.uuid4()),
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_ids": [str(uuid.uuid4())],
        "persona_id": "persona-macro",
        "scope": "portfolio",
        # proposed_change is missing
        "confidence": 0.85,
        "review_state": "endorsed",
        "created_at": "2026-07-11T13:00:00Z",
        "updated_at": "2026-07-11T14:30:00Z",
        "expiry": "2026-09-11T00:00:00Z"
    }
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(instance=invalid_lesson, schema=schema)
    assert "proposed_change" in str(exc_info.value)
