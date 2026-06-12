from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecordStore,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.trainer_seed_bridge import (
    TrainerSeedBridge,
    TrainerSeedBridgeError,
    TrainerSeedKind,
)


def _stores(tmp_path: Path) -> tuple[InteractionSourceRecordStore, StrategySpecSeedStore]:
    return (
        InteractionSourceRecordStore(path=tmp_path / "interactions.jsonl"),
        StrategySpecSeedStore(path=tmp_path / "seeds.jsonl"),
    )


def _bridge(tmp_path: Path) -> tuple[TrainerSeedBridge, InteractionSourceRecordStore, StrategySpecSeedStore]:
    interaction_store, seed_store = _stores(tmp_path)
    return (
        TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            created_by="Codex",
        ),
        interaction_store,
        seed_store,
    )


def _event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": "trainer_commit",
        "event_id": "tevt-ids004-001",
        "session_id": "trn-ids004-001",
        "persona_id": "persona-alpha",
        "summary": (
            "TWSE momentum factor signal can rank liquid equities for "
            "5-day forward returns using point-in-time OHLCV."
        ),
        "seed_kind": "new_strategy",
        "committed_by": "operator-alpha",
        "committed_at": "2026-06-12T03:00:00Z",
        "raw_ref": "evidence://trainer/trn-ids004-001/tevt-ids004-001",
        "artifact_refs": {
            "candidate_artifact_ref": "artifact-trainer-candidate-001",
            "after_artifact_ref": "artifact-trainer-committed-001",
        },
        "strategy_seed": {
            "hypothesis": (
                "TWSE momentum factor signal can rank liquid equities for "
                "5-day forward returns."
            ),
            "asset_class": ["equity"],
            "market_scope": ["TWSE"],
            "holding_period": "5 trading days",
            "required_data": ["point-in-time OHLCV", "return labels"],
            "backend_hint": "qlib",
            "feature_hints": ["momentum"],
            "label_hints": ["5_day_forward_return"],
        },
    }
    payload.update(overrides)
    return payload


def test_trainer_commit_creates_review_inbox_seed_and_lineage(tmp_path: Path) -> None:
    bridge, interaction_store, seed_store = _bridge(tmp_path)

    result = bridge.ingest_event(_event())

    record = interaction_store.get(result.interaction_record.interaction_id)
    assert record is not None
    assert record.source_surface.value == "trainer"
    assert record.redaction_status == InteractionRedactionStatus.PASSED
    assert record.raw_ref == "evidence://trainer/trn-ids004-001/tevt-ids004-001"

    inbox = seed_store.list_by_status("draft")
    assert [seed.seed_id for seed in inbox] == [result.seed.seed_id]
    stored = seed_store.get(result.seed.seed_id)
    assert stored is not None
    assert stored.metadata["source_kind"] == "trainer"
    assert stored.metadata["seed_kind"] == "new_strategy"
    assert stored.metadata["execution_route"] == "none"
    assert stored.lineage["created_from"] == "trainer_seed_bridge"
    extraction = stored.lineage["trainer_seed_extraction_ref"]
    assert extraction["record_kind"] == "trainer_seed_extraction_ref"
    assert extraction["event_type"] == "trainer_commit"
    assert extraction["event_id"] == "tevt-ids004-001"
    assert extraction["interaction_id"] == record.interaction_id
    assert extraction["seed_id"] == stored.seed_id
    assert stored.lineage["intent_classification"]["primary_intent"] == "strategy_hypothesis"
    assert stored.lineage["registry_write_performed"] is False
    assert stored.lineage["execution_route"] == "none"


def test_explicit_seed_submission_is_accepted(tmp_path: Path) -> None:
    bridge, _interaction_store, seed_store = _bridge(tmp_path)

    result = bridge.ingest_event(
        _event(
            event_type="explicit_seed_submission",
            event_id="tevt-ids004-explicit",
            raw_ref="evidence://trainer/trn-ids004-001/tevt-ids004-explicit",
        )
    )

    assert seed_store.get(result.seed.seed_id) is not None
    assert result.extraction_ref.event_type == "explicit_seed_submission"


def test_raw_or_uncommitted_trainer_log_is_refused_before_store_write(tmp_path: Path) -> None:
    bridge, interaction_store, seed_store = _bridge(tmp_path)

    with pytest.raises(TrainerSeedBridgeError) as exc_info:
        bridge.ingest_event(
            _event(
                event_type="message",
                event_id="tevt-raw-message",
                message_body="Raw operator teaching text must remain evidence only.",
            )
        )

    assert exc_info.value.code == "raw_teaching_log_refused"
    assert interaction_store.count() == 0
    assert seed_store.count() == 0


def test_committed_event_with_raw_events_array_is_refused(tmp_path: Path) -> None:
    bridge, interaction_store, seed_store = _bridge(tmp_path)

    with pytest.raises(TrainerSeedBridgeError) as exc_info:
        bridge.ingest_event(
            _event(
                event_id="tevt-raw-events",
                events=[{"event_type": "message", "message_body": "raw log"}],
            )
        )

    assert exc_info.value.code == "raw_teaching_log_refused"
    assert interaction_store.count() == 0
    assert seed_store.count() == 0


@pytest.mark.parametrize(
    ("seed_kind", "summary"),
    [
        (
            TrainerSeedKind.NEW_STRATEGY,
            "Investable hypothesis: TWSE value factor can rank equities.",
        ),
        (
            TrainerSeedKind.MUTATION,
            "Strategy mutation: adjust the momentum signal confirmation window.",
        ),
        (
            TrainerSeedKind.RISK_CONSTRAINT,
            "Risk constraint: cap drawdown and exposure for this strategy.",
        ),
        (
            TrainerSeedKind.EXECUTION_CONSTRAINT,
            "Execution policy: use limit orders with a slippage guard.",
        ),
        (
            TrainerSeedKind.NEGATIVE,
            "Negative memory: never repeat the failed mean reversion setup.",
        ),
        (
            TrainerSeedKind.PERSONA_POLICY,
            "Persona policy: coaching note for a concise skeptical response style.",
        ),
        (
            TrainerSeedKind.DATA_REQUIREMENT,
            "Data requirement: point-in-time OHLCV and return labels are required.",
        ),
    ],
)
def test_supported_seed_kinds_create_reviewable_candidates(
    tmp_path: Path,
    seed_kind: TrainerSeedKind,
    summary: str,
) -> None:
    bridge, _interaction_store, seed_store = _bridge(tmp_path)

    result = bridge.ingest_event(
        _event(
            event_id=f"tevt-{seed_kind.value}",
            raw_ref=f"evidence://trainer/trn-ids004-001/tevt-{seed_kind.value}",
            seed_kind=seed_kind.value,
            summary=summary,
            strategy_seed={
                "hypothesis": summary,
                "asset_class": ["equity"],
                "market_scope": ["TWSE"],
                "required_data": ["governed trainer commit evidence"],
            },
        )
    )

    stored = seed_store.get(result.seed.seed_id)
    assert stored is not None
    assert stored.metadata["seed_kind"] == seed_kind.value
    assert stored.lineage["trainer_seed_extraction_ref"]["seed_kind"] == seed_kind.value


def test_redaction_is_applied_before_seed_candidate_is_saved(tmp_path: Path) -> None:
    bridge, interaction_store, seed_store = _bridge(tmp_path)

    result = bridge.ingest_event(
        _event(
            event_id="tevt-redacted",
            raw_ref="evidence://trainer/trn-ids004-001/tevt-redacted",
            summary=(
                "Investable hypothesis: email trader@example.com about "
                "$500k TWSE momentum capacity."
            ),
        )
    )

    record = interaction_store.get(result.interaction_record.interaction_id)
    assert record is not None
    assert "trader@example.com" not in record.summary
    assert "$500k" not in record.summary
    stored = seed_store.get(result.seed.seed_id)
    assert stored is not None
    assert "trader@example.com" not in stored.hypothesis
    assert "$500k" not in stored.hypothesis
    assert result.redaction_findings
