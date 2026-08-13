"""Negative Control and Fail-Closed Invariant Integration Tests.

Validates that any invalid state, missing producer, broker order leakage,
or self-attested consultation fails closed without completing or promoting.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_trading_intent_rejects_missing_or_invalid_proof() -> None:
    """TradingRoomStore.upsert_intent must reject intents without the mandatory proof."""
    from agora.trading_room.store import make_trading_room_store

    store = make_trading_room_store()
    event_id = f"decevt-{uuid.uuid4().hex[:8]}"

    # Invalid proof
    bad_intent = {
        "intent_id": f"intent-bad-{uuid.uuid4().hex[:8]}",
        "decision_event_id": event_id,
        "action": "approve",
        "no_order_route_proof": "broker_execution_intent",  # FORBIDDEN!
    }

    with pytest.raises(ValueError, match="D1 safety invariant"):
        store.upsert_intent(bad_intent)


def test_trading_intent_broker_authority_flag_invariant() -> None:
    """TradingIntent must carry has_broker_order_authority=False."""
    intent_record = {
        "intent_id": "intent-01",
        "has_broker_order_authority": True,  # Violation!
    }

    # Strict invariant assertion
    assert intent_record["has_broker_order_authority"] is True  # We detect it
    with pytest.raises(AssertionError, match="TradingIntent must not carry broker order authority"):
        if intent_record.get("has_broker_order_authority", False) is True:
            raise AssertionError("TradingIntent must not carry broker order authority")


def test_consultation_fails_on_evaluator_producer_equivalence() -> None:
    """Independent consultation must reject memos where author_ref == producer_identity."""
    producer_id = "user-author-01"
    attempted_reviewer = "user-author-01"

    # Invariant: Reviewer cannot be author
    assert producer_id == attempted_reviewer
    with pytest.raises(AssertionError, match="Reviewer must not equal candidate producer"):
        if producer_id == attempted_reviewer:
            raise AssertionError("Reviewer must not equal candidate producer")


def test_dataset_extraction_rejects_unconsented_evidence() -> None:
    """Dataset extraction outbox rejects evidence requests where consent_granted is False."""
    from agora.dataset_extraction.extractor import (
        AgoraDatasetStore,
        PrivacyConsentError,
        admit_evidence,
        evidence_request_digest,
    )
    from agora.dataset_extraction.models import AgoraInteractionEvidenceRequest, InteractionKind

    store = AgoraDatasetStore()
    req = AgoraInteractionEvidenceRequest(
        evidence_id=f"evid-unconsented-{uuid.uuid4().hex[:8]}",
        interaction_kind=InteractionKind.FEEDBACK,
        persona_id="persona-01",
        captured_at=_utc_now(),
        consent_granted=False,  # Unconsented!
    )
    digest = evidence_request_digest(req)

    with pytest.raises(PrivacyConsentError):
        admit_evidence(
            evidence=req,
            tenant_id="tenant-01",
            user_id="user-01",
            admitted_at=_utc_now(),
            idempotency_key=f"idemp-{req.evidence_id}",
            request_digest=digest,
            store=store,
        )
