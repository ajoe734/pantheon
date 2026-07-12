"""Unit and integration tests for memory and evaluation lesson governance."""

from __future__ import annotations

import uuid
from pathlib import Path
import pytest

from services.memory.persona_memory_store import PersonaMemoryStore
from services.persona.lesson_governance import (
    LessonGovernanceService,
    TradeLessonCandidateError,
    TradeLessonCandidateStore,
    is_sensitive_change,
    check_evaluation_gates,
    utc_now,
)


@pytest.fixture
def tmp_json_path(tmp_path: Path) -> Path:
    return tmp_path / "candidates.json"


@pytest.fixture
def candidate_store(tmp_json_path: Path) -> TradeLessonCandidateStore:
    return TradeLessonCandidateStore(path=tmp_json_path)


@pytest.fixture
def governance_service(candidate_store: TradeLessonCandidateStore) -> LessonGovernanceService:
    return LessonGovernanceService(store=candidate_store)


@pytest.fixture
def memory_store(tmp_path: Path) -> PersonaMemoryStore:
    return PersonaMemoryStore(path=tmp_path / "persona_memory.json")


def make_valid_candidate(overrides: dict | None = None) -> dict:
    base = {
        "lesson_candidate_id": str(uuid.uuid4()),
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_ids": [str(uuid.uuid4())],
        "persona_id": "persona-macro",
        "scope": "strategy",
        "proposed_change": "Reduce holding period target by 2 hours",
        "confidence": 0.75,
        "review_state": "proposed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "expiry": utc_now(),
    }
    if overrides:
        base.update(overrides)
    return base


def test_create_and_get_candidate(candidate_store: TradeLessonCandidateStore) -> None:
    data = make_valid_candidate()
    created = candidate_store.create(data)
    assert created["lesson_candidate_id"] == data["lesson_candidate_id"]
    assert created["review_state"] == "proposed"

    retrieved = candidate_store.get(data["lesson_candidate_id"])
    assert retrieved is not None
    assert retrieved["lesson_candidate_id"] == data["lesson_candidate_id"]


def test_schema_validation_failures(candidate_store: TradeLessonCandidateStore) -> None:
    # Missing required field: proposed_change
    invalid = make_valid_candidate()
    del invalid["proposed_change"]
    with pytest.raises(TradeLessonCandidateError, match="Schema validation failed"):
        candidate_store.create(invalid)

    # Invalid enum value for review_state
    invalid_state = make_valid_candidate({"review_state": "invalid_state"})
    with pytest.raises(TradeLessonCandidateError, match="Schema validation failed"):
        candidate_store.create(invalid_state)


def test_lifecycle_submit_review(governance_service: LessonGovernanceService) -> None:
    candidate = make_valid_candidate()
    governance_service.store.create(candidate)

    updated = governance_service.submit_review(candidate["lesson_candidate_id"])
    assert updated["review_state"] == "pending_review"

    # Cannot submit again
    with pytest.raises(TradeLessonCandidateError, match="Cannot submit review"):
        governance_service.submit_review(candidate["lesson_candidate_id"])


def test_lifecycle_decide_reject(governance_service: LessonGovernanceService) -> None:
    candidate = make_valid_candidate()
    governance_service.store.create(candidate)

    decided = governance_service.decide(
        candidate["lesson_candidate_id"],
        action="reject",
        operator_id="op-alice",
        reason="Does not make sense",
        audit_receipt_id=str(uuid.uuid4()),
    )
    assert decided["review_state"] == "rejected"
    assert decided["receipt"]["action"] == "reject"
    assert decided["receipt"]["operator_id"] == "op-alice"


def test_gate_check_pattern_sample_size() -> None:
    candidate = make_valid_candidate({"scope": "strategy", "trade_episode_ids": [str(uuid.uuid4())]})
    # Pattern lesson requires at least 3 supporting episodes
    errors = check_evaluation_gates(candidate, [])
    assert any("at least 3 supporting episodes" in e for e in errors)


def test_gate_check_pattern_cross_regime() -> None:
    episodes = [
        {"trade_episode_id": "ep1", "regime": "bull_market"},
        {"trade_episode_id": "ep2", "regime": "bull_market"},
        {"trade_episode_id": "ep3", "regime": "bull_market"},
    ]
    candidate = make_valid_candidate({
        "scope": "strategy",
        "trade_episode_ids": [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())],
    })
    errors = check_evaluation_gates(candidate, episodes)
    assert any("at least 2 distinct market regimes" in e for e in errors)

    # Adding distinct regime should pass
    episodes[2]["regime"] = "bear_market"
    errors = check_evaluation_gates(candidate, episodes)
    assert not any("at least 2 distinct market regimes" in e for e in errors)


def test_gate_check_sensitive_changes() -> None:
    candidate = make_valid_candidate({
        "scope": "risk",
        "proposed_change": "Change leverage limit from 2x to 3x",
    })
    assert is_sensitive_change(candidate)

    # 1. No receipt -> fails
    errors = check_evaluation_gates(candidate, [])
    assert any("Sensitive changes (policy/risk/capital/live) require an endorsement receipt" in e for e in errors)

    # 2. Receipt lacks approval/deployment refs -> fails
    candidate["receipt"] = {
        "operator_id": "op-alice",
        "decided_at": utc_now(),
        "action": "endorse",
        "reason": "We should increase limit",
        "audit_receipt_id": str(uuid.uuid4()),
    }
    errors = check_evaluation_gates(candidate, [])
    assert any("Sensitive changes require both approval decision and deployment plan references" in e for e in errors)

    # 3. Valid references in reason but missing audit_receipt_id -> fails
    candidate["receipt"]["reason"] = "Approved via decision app-123 and deployment plan plan-456."
    del candidate["receipt"]["audit_receipt_id"]
    errors = check_evaluation_gates(candidate, [])
    assert any("audit_receipt_id" in e for e in errors)

    # 4. Valid references and audit_receipt_id -> passes
    candidate["receipt"]["audit_receipt_id"] = str(uuid.uuid4())
    errors = check_evaluation_gates(candidate, [])
    assert not errors


def test_endorse_gated_fail_closed(governance_service: LessonGovernanceService) -> None:
    # Pattern candidate failing sample size and regime checks
    candidate = make_valid_candidate({
        "scope": "strategy",
        "trade_episode_ids": [str(uuid.uuid4())],
    })
    governance_service.store.create(candidate)

    with pytest.raises(TradeLessonCandidateError, match="Endorsement blocked by evaluation gates"):
        governance_service.decide(
            candidate["lesson_candidate_id"],
            action="endorse",
            operator_id="op-alice",
            reason="Approved",
            audit_receipt_id=str(uuid.uuid4()),
            episodes=[],
        )


def test_successful_endorse_and_merge(
    governance_service: LessonGovernanceService,
    memory_store: PersonaMemoryStore,
) -> None:
    episodes = [
        {"trade_episode_id": "ep1", "regime": "bull_market"},
        {"trade_episode_id": "ep2", "regime": "bear_market"},
        {"trade_episode_id": "ep3", "regime": "bull_market"},
    ]
    candidate = make_valid_candidate({
        "scope": "strategy",
        "trade_episode_ids": ["ep1", "ep2", "ep3"],
    })
    governance_service.store.create(candidate)

    # Endorse passes gates
    endorsed = governance_service.decide(
        candidate["lesson_candidate_id"],
        action="endorse",
        operator_id="op-alice",
        reason="Passes all criteria",
        audit_receipt_id=str(uuid.uuid4()),
        episodes=episodes,
    )
    assert endorsed["review_state"] == "endorsed"

    # Merge to memory store
    merged = governance_service.merge_to_memory(candidate["lesson_candidate_id"], memory_store)
    assert merged["review_state"] == "merged"

    # Verify memory entry exists
    entry_id = f"pmem-lesson-{candidate['lesson_candidate_id']}"
    entry = memory_store.get(entry_id)
    assert entry is not None
    assert entry.persona_id == candidate["persona_id"]
    assert entry.memory_type == "strategy_lesson"
    assert entry.content["structured_payload"]["lesson_candidate_id"] == candidate["lesson_candidate_id"]
