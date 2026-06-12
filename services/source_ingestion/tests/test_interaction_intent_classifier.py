"""Tests for IDS-003 deterministic interaction intent classification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.interaction_intent_classifier import (
    IntentClassification,
    InteractionIntentClassifier,
    InteractionPrimaryIntent,
    classify_interaction_intent,
)
from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceSurface,
    InteractionVisibility,
)

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional in local lean test images.
    jsonschema = None


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "contracts"
    / "interaction_intent_classification.schema.json"
)


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _record(
    summary: str,
    *,
    metadata: dict[str, Any] | None = None,
    redaction_status: str = InteractionRedactionStatus.PENDING.value,
) -> InteractionSourceRecord:
    return InteractionSourceRecord(
        interaction_id="interaction-ids-003",
        source_surface=InteractionSourceSurface.TRAINER.value,
        actor_type="user",
        persona_refs=["persona-alpha"],
        session_id="session-ids-003",
        raw_ref="evidence://trainer/session-ids-003/commit-0001",
        summary=summary,
        evidence_refs=[
            {
                "ref": "evidence://trainer/session-ids-003/commit-0001",
                "kind": "raw_interaction",
                "content_hash": "sha256:ids003",
            }
        ],
        visibility=InteractionVisibility.PERSONA.value,
        redaction_status=redaction_status,
        created_at="2026-06-12T00:00:00Z",
        updated_at="2026-06-12T00:00:00Z",
        metadata={
            "surface_name": "trainer_commit",
            "research_only": True,
            "execution_route": "none",
            **dict(metadata or {}),
        },
    )


def test_schema_declares_intent_classification_contract() -> None:
    schema = _schema()

    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "Pantheon Interaction IntentClassification"
    assert schema["$id"] == "https://pantheon/interaction-intent-classification/v1"
    assert set(schema["properties"]["primary_intent"]["enum"]) == {
        "strategy_hypothesis",
        "risk_overlay",
        "execution_policy",
        "portfolio_allocation",
        "persona_policy",
        "preference_example",
        "negative_memory",
        "operational_note",
        "non_strategy",
    }
    required = set(schema["required"])
    for field in (
        "interaction_id",
        "primary_intent",
        "confidence",
        "requires_human_review",
        "archive_only",
        "matched_signals",
    ):
        assert field in required


@pytest.mark.parametrize(
    ("summary", "expected_intent", "expected_archive_only"),
    [
        (
            "Coaching note: coach persona-alpha's response style to be more skeptical "
            "and concise when discussing trading strategy ideas.",
            InteractionPrimaryIntent.PERSONA_POLICY,
            False,
        ),
        (
            "Investable hypothesis: TWSE momentum and volatility factor signal can "
            "rank equities for 5-day forward return.",
            InteractionPrimaryIntent.STRATEGY_HYPOTHESIS,
            False,
        ),
        (
            "Risk overlay: cap max drawdown, leverage limit, and correlation cap "
            "for existing alpha candidates.",
            InteractionPrimaryIntent.RISK_OVERLAY,
            False,
        ),
        (
            "Execution policy: use limit orders, slippage guard, and paper-only "
            "broker route for this strategy.",
            InteractionPrimaryIntent.EXECUTION_POLICY,
            False,
        ),
        (
            "Portfolio allocation: allocate capital to persona-alpha sleeve and "
            "rebalance target weights monthly.",
            InteractionPrimaryIntent.PORTFOLIO_ALLOCATION,
            False,
        ),
        (
            "Preference example: I prefer the second response and thumbs down "
            "verbose answers.",
            InteractionPrimaryIntent.PREFERENCE_EXAMPLE,
            False,
        ),
        (
            "Negative memory: never repeat the retired mean reversion strategy "
            "after the failed experiment.",
            InteractionPrimaryIntent.NEGATIVE_MEMORY,
            False,
        ),
        (
            "Operational note: update the runbook and monitor supervisor health "
            "after the deployment.",
            InteractionPrimaryIntent.OPERATIONAL_NOTE,
            False,
        ),
        (
            "Schedule lunch with the research team and send a calendar invite.",
            InteractionPrimaryIntent.NON_STRATEGY,
            True,
        ),
    ],
)
def test_classifier_table_matches_ids_003_acceptance_cases(
    summary: str,
    expected_intent: InteractionPrimaryIntent,
    expected_archive_only: bool,
) -> None:
    classification = classify_interaction_intent(_record(summary))

    assert classification.primary_intent == expected_intent
    assert classification.archive_only is expected_archive_only
    assert classification.confidence >= 0.60
    assert classification.requires_human_review is False
    assert classification.metadata["deterministic_first"] is True
    assert classification.metadata["embedding_used"] is False
    assert classification.metadata["seed_created"] is False
    assert classification.metadata["execution_route"] == "none"


def test_style_coaching_beats_strategy_language() -> None:
    classification = InteractionIntentClassifier().classify(
        _record(
            "Coaching note: adjust persona-alpha communication style when it "
            "explains a trading strategy hypothesis to operators."
        )
    )

    assert classification.primary_intent == InteractionPrimaryIntent.PERSONA_POLICY
    assert InteractionPrimaryIntent.STRATEGY_HYPOTHESIS in classification.secondary_intents
    assert classification.archive_only is False


def test_investable_hypothesis_is_not_archived() -> None:
    classification = classify_interaction_intent(
        _record(
            "Investable hypothesis: a cross-sectional alpha signal using daily "
            "OHLCV can predict returns for liquid US equities."
        )
    )

    assert classification.primary_intent == InteractionPrimaryIntent.STRATEGY_HYPOTHESIS
    assert classification.archive_only is False
    assert classification.requires_human_review is False


def test_low_confidence_interaction_requires_review_and_no_seed_route() -> None:
    classification = classify_interaction_intent(_record("Maybe capture the thing we mentioned later."))

    assert classification.primary_intent == InteractionPrimaryIntent.NON_STRATEGY
    assert classification.confidence < 0.60
    assert classification.requires_human_review is True
    assert classification.archive_only is True
    assert classification.metadata["registry_write_performed"] is False


def test_metadata_seed_kind_hint_can_disambiguate_summary() -> None:
    classification = classify_interaction_intent(
        _record(
            "Create the governed inbox card from this committed note.",
            metadata={"seed_kind": "risk_constraint"},
        )
    )

    assert classification.primary_intent == InteractionPrimaryIntent.RISK_OVERLAY
    assert "metadata:seed_kind=risk_constraint" in classification.matched_signals
    assert classification.requires_human_review is False


def test_failed_redaction_status_forces_human_review_without_changing_intent() -> None:
    classification = classify_interaction_intent(
        _record(
            "Investable hypothesis: TWSE momentum factor signal can rank equities.",
            redaction_status=InteractionRedactionStatus.FAILED.value,
        )
    )

    assert classification.primary_intent == InteractionPrimaryIntent.STRATEGY_HYPOTHESIS
    assert classification.requires_human_review is True
    assert "redaction_status=failed" in classification.reason


def test_classification_payload_round_trips_and_validates_schema() -> None:
    classification = classify_interaction_intent(
        _record("Execution policy: use limit order routing and a slippage guard.")
    )
    payload = classification.to_dict()
    round_tripped = IntentClassification.from_dict(payload)

    assert round_tripped == classification

    if jsonschema is None:
        pytest.skip("jsonschema not installed")

    jsonschema.Draft7Validator(_schema()).validate(payload)

    invalid = dict(payload)
    invalid["primary_intent"] = "trade_now"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(_schema()).validate(invalid)
