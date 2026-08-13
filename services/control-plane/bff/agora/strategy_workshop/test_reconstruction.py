"""Unit and contract tests for Strategy Reconstruction engine & worker."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

_CONTROL_PLANE_DIR = Path(__file__).resolve().parents[3]
if str(_CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(_CONTROL_PLANE_DIR))

from agora.strategy_workshop.reconstruction import (
    StrategyReconstructionResult,
    reconstruct_strategy_from_events,
)


def test_strategy_reconstruction_from_basic_messages() -> None:
    workshop_id = "ws-test-recon-001"
    messages = [
        "I want to create a momentum strategy for BTC and ETH.",
        "The signal will be a 20-day moving average crossover with 2% stop loss.",
    ]
    events = [
        {"event_id": "evt-1", "sequence_no": 1, "created_at": "2026-08-13T12:00:00Z"},
        {"event_id": "evt-2", "sequence_no": 2, "created_at": "2026-08-13T12:01:00Z"},
    ]

    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=2,
        events=events,
        messages_content=messages,
    )

    assert isinstance(result, StrategyReconstructionResult)
    assert result.workshop_id == workshop_id
    assert result.based_on_sequence_no == 2

    # Check block statuses
    assert result.strategy_map.universe.status != "missing"
    assert result.strategy_map.signal_definition.status != "missing"
    assert result.strategy_map.risk_controls.status != "missing"

    # Check facts and Next Best Question
    assert len(result.explicit_facts) > 0
    assert result.next_best_question is not None
    assert isinstance(result.next_best_question.text, str)
    assert len(result.next_best_question.resolves) > 0

    # Completeness check
    assert result.completeness.grade in {"draftable", "researchable", "trading_room_ready", "insufficient"}


def test_strategy_reconstruction_nbq_uniqueness_and_completeness_derivation() -> None:
    workshop_id = "ws-test-recon-002"
    messages = ["Hello, I want to trade."]
    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=1,
        events=[{"event_id": "evt-1", "sequence_no": 1}],
        messages_content=messages,
    )

    assert result.completeness.grade == "insufficient"
    assert "Missing core strategy hypothesis" in result.completeness.blockers
    assert result.next_best_question is not None
    # NBQ must resolve hypothesis
    assert "hypothesis.summary" in result.next_best_question.resolves
