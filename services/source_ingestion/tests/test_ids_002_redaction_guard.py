"""Tests for IDS-002 — Redaction / visibility / scope guard.

Acceptance criteria verified here:
  1. redaction_status=failed blocks any downstream SeedCandidate (typed refusal + test).
  2. private/persona visibility cannot leak into shared results.
  3. Raw prompt (transcript) never persisted — detected and set to redaction_status=failed.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionVisibility,
)
from services.source_ingestion.redaction_guard import (
    RedactionContext,
    RedactionResult,
    SeedCandidateBlockedError,
    VisibilityScopeError,
    apply_redaction,
    guard_seed_candidate,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_RAW_REF = "evidence://trainer/session-ids-002/commit-0001"


def _record(**overrides: Any) -> InteractionSourceRecord:
    base: dict[str, Any] = {
        "interaction_id": "interaction-ids-002-test",
        "source_surface": "trainer",
        "actor_type": "user",
        "persona_refs": ["persona-alpha"],
        "session_id": "session-ids-002",
        "raw_ref": _RAW_REF,
        "summary": "User proposed a momentum strategy on US equities.",
        "evidence_refs": [{"ref": _RAW_REF, "kind": "raw_interaction"}],
        "visibility": InteractionVisibility.PERSONA.value,
        "redaction_status": InteractionRedactionStatus.PENDING.value,
        "created_at": "2026-06-12T00:00:00Z",
        "updated_at": "2026-06-12T00:00:00Z",
    }
    base.update(overrides)
    return InteractionSourceRecord(**base)


def _ctx(**overrides: Any) -> RedactionContext:
    base: dict[str, Any] = {
        "tenant_id": "tenant-001",
        "requesting_visibility": InteractionVisibility.PERSONA,
        "user_id": "user-001",
        "persona_id": "persona-alpha",
    }
    base.update(overrides)
    return RedactionContext(**base)


# ---------------------------------------------------------------------------
# Basic pass / fail
# ---------------------------------------------------------------------------


def test_clean_record_passes_redaction() -> None:
    result = apply_redaction(_record(), _ctx())
    assert result.passed
    assert not result.failed
    assert result.record.redaction_status == InteractionRedactionStatus.PASSED


def test_clean_record_not_marked_as_redacted() -> None:
    result = apply_redaction(_record(), _ctx())
    assert not result.was_redacted
    assert result.findings == ()


# ---------------------------------------------------------------------------
# Acceptance 1: redaction_status=failed blocks SeedCandidate
# ---------------------------------------------------------------------------


def test_seed_candidate_blocked_on_failed_status() -> None:
    failed_record = _record(redaction_status="failed")
    with pytest.raises(SeedCandidateBlockedError) as exc_info:
        guard_seed_candidate(failed_record)
    err = exc_info.value
    assert err.interaction_id == "interaction-ids-002-test"
    assert "redaction_status=failed" in err.failure_reason
    assert "interaction-ids-002-test" in str(err)


def test_seed_candidate_blocked_on_pending_status() -> None:
    pending_record = _record(redaction_status="pending")
    with pytest.raises(SeedCandidateBlockedError) as exc_info:
        guard_seed_candidate(pending_record)
    err = exc_info.value
    assert "redaction_status=pending" in err.failure_reason


def test_seed_candidate_allowed_after_redaction_passes() -> None:
    passed_record = apply_redaction(_record(), _ctx()).record
    guard_seed_candidate(passed_record)  # must not raise


def test_failed_redaction_produces_blocked_error_when_guarded() -> None:
    # End-to-end: raw transcript → failed → guard raises typed refusal.
    transcript_record = _record(
        summary="User: Buy me 100 shares of AAPL.\nAssistant: Done."
    )
    result = apply_redaction(transcript_record, _ctx())
    assert result.failed

    with pytest.raises(SeedCandidateBlockedError) as exc_info:
        guard_seed_candidate(result.record)
    assert exc_info.value.interaction_id == "interaction-ids-002-test"


# ---------------------------------------------------------------------------
# Acceptance 2: private/persona visibility cannot leak into shared results
# ---------------------------------------------------------------------------


def test_private_record_blocked_from_shared_consumer() -> None:
    private_record = _record(visibility="private")
    shared_ctx = _ctx(
        requesting_visibility=InteractionVisibility.SHARED,
        user_id="user-001",
        persona_id=None,
    )
    with pytest.raises(VisibilityScopeError) as exc_info:
        apply_redaction(private_record, shared_ctx)
    err = exc_info.value
    assert err.record_visibility == "private"
    assert err.requesting_visibility == "shared"
    assert "interaction-ids-002-test" in str(err)


def test_persona_record_blocked_from_shared_consumer() -> None:
    persona_record = _record(visibility="persona")
    shared_ctx = _ctx(
        requesting_visibility=InteractionVisibility.SHARED,
        persona_id=None,
    )
    with pytest.raises(VisibilityScopeError):
        apply_redaction(persona_record, shared_ctx)


def test_persona_record_blocked_from_desk_consumer() -> None:
    persona_record = _record(visibility="persona")
    desk_ctx = _ctx(
        requesting_visibility=InteractionVisibility.DESK,
        persona_id=None,
    )
    with pytest.raises(VisibilityScopeError):
        apply_redaction(persona_record, desk_ctx)


def test_private_record_blocked_when_no_user_id() -> None:
    private_record = _record(visibility="private")
    anonymous_ctx = _ctx(
        requesting_visibility=InteractionVisibility.PRIVATE,
        user_id=None,
    )
    with pytest.raises(VisibilityScopeError) as exc_info:
        apply_redaction(private_record, anonymous_ctx)
    assert exc_info.value.requesting_visibility == "anonymous"


def test_persona_record_blocked_when_no_persona_id() -> None:
    persona_record = _record(visibility="persona")
    no_persona_ctx = _ctx(
        requesting_visibility=InteractionVisibility.PERSONA,
        persona_id=None,
    )
    with pytest.raises(VisibilityScopeError) as exc_info:
        apply_redaction(persona_record, no_persona_ctx)
    assert exc_info.value.requesting_visibility == "no_persona_context"


def test_shared_record_accessible_to_shared_consumer() -> None:
    shared_record = _record(visibility="shared")
    shared_ctx = _ctx(
        requesting_visibility=InteractionVisibility.SHARED,
        persona_id=None,
    )
    result = apply_redaction(shared_record, shared_ctx)
    assert result.passed


def test_desk_record_accessible_to_shared_consumer_is_blocked() -> None:
    desk_record = _record(visibility="desk")
    shared_ctx = _ctx(
        requesting_visibility=InteractionVisibility.SHARED,
        persona_id=None,
    )
    with pytest.raises(VisibilityScopeError):
        apply_redaction(desk_record, shared_ctx)


# ---------------------------------------------------------------------------
# Acceptance 3: raw prompt never persisted — transcript detection
# ---------------------------------------------------------------------------


def test_raw_transcript_dialogue_fails_redaction() -> None:
    record = _record(summary="User: What is the momentum?\nAssistant: High momentum detected.")
    result = apply_redaction(record, _ctx())
    assert result.failed
    assert "raw_transcript_detected" in result.findings


def test_raw_json_role_content_fails_redaction() -> None:
    record = _record(summary='{"role": "user", "content": "Buy AAPL"}')
    result = apply_redaction(record, _ctx())
    assert result.failed
    assert "raw_transcript_detected" in result.findings


def test_private_note_marker_fails_redaction() -> None:
    record = _record(summary="PRIVATE: My personal trading notes for Q3.")
    result = apply_redaction(record, _ctx())
    assert result.failed
    assert "private_note_marker_detected" in result.findings


def test_confidential_marker_fails_redaction() -> None:
    record = _record(summary="CONFIDENTIAL: Portfolio review for sponsor.")
    result = apply_redaction(record, _ctx())
    assert result.failed


# ---------------------------------------------------------------------------
# PII stripping
# ---------------------------------------------------------------------------


def test_email_is_stripped_from_summary() -> None:
    record = _record(summary="Contact trader@example.com for details on the strategy.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "trader@example.com" not in result.record.summary
    assert "[REDACTED:EMAIL]" in result.record.summary
    assert result.was_redacted
    assert any("pii:email" in f for f in result.findings)


def test_phone_number_is_stripped() -> None:
    record = _record(summary="Call 415-555-1234 to confirm the trade.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "415-555-1234" not in result.record.summary
    assert result.was_redacted


def test_ssn_is_stripped() -> None:
    record = _record(summary="Reference SSN 123-45-6789 for verification.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "123-45-6789" not in result.record.summary


def test_credit_card_is_stripped() -> None:
    record = _record(summary="Card 4111 1111 1111 1111 was used for the subscription.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "4111 1111 1111 1111" not in result.record.summary


# ---------------------------------------------------------------------------
# Credential stripping
# ---------------------------------------------------------------------------


def test_api_key_is_stripped() -> None:
    record = _record(summary="API key is sk-live-ABCDEF1234567890 for the broker.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "sk-live-ABCDEF1234567890" not in result.record.summary
    assert "[REDACTED:CREDENTIAL]" in result.record.summary
    assert any("credential:" in f for f in result.findings)


def test_password_is_stripped() -> None:
    record = _record(summary="Login password=mySecretPassword123 was used.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "mySecretPassword123" not in result.record.summary


def test_bearer_token_is_stripped() -> None:
    record = _record(summary="Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "Bearer eyJ" not in result.record.summary


# ---------------------------------------------------------------------------
# Capital amount stripping
# ---------------------------------------------------------------------------


def test_dollar_amount_is_stripped() -> None:
    record = _record(summary="The strategy targets $250,000 per quarter.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "$250,000" not in result.record.summary
    assert "[REDACTED:AMOUNT]" in result.record.summary
    assert any("capital_amount:" in f for f in result.findings)


def test_amount_word_is_stripped() -> None:
    record = _record(summary="Initial allocation is 5 million in tech equities.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "5 million" not in result.record.summary


# ---------------------------------------------------------------------------
# Broker ref stripping
# ---------------------------------------------------------------------------


def test_account_id_is_stripped() -> None:
    record = _record(summary="Account #123456789 placed the order.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "123456789" not in result.record.summary
    assert "[REDACTED:BROKER_REF]" in result.record.summary
    assert any("broker_ref:" in f for f in result.findings)


def test_ibkr_account_id_is_stripped() -> None:
    record = _record(summary="IBKR account U1234567 held the position.")
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "U1234567" not in result.record.summary


# ---------------------------------------------------------------------------
# Metadata stripping
# ---------------------------------------------------------------------------


def test_pii_in_metadata_is_stripped() -> None:
    record = _record(metadata={"note": "email trader@example.com for access"})
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "trader@example.com" not in result.record.metadata["note"]
    assert "[REDACTED:EMAIL]" in result.record.metadata["note"]


def test_nested_metadata_pii_is_stripped() -> None:
    record = _record(metadata={"extra": {"contact": "reach@sample.org for review"}})
    result = apply_redaction(record, _ctx())
    assert result.passed
    assert "reach@sample.org" not in result.record.metadata["extra"]["contact"]


def test_list_metadata_pii_is_stripped() -> None:
    record = _record(metadata={"contacts": ["a@b.com", "c@d.com"]})
    result = apply_redaction(record, _ctx())
    assert result.passed
    for item in result.record.metadata["contacts"]:
        assert "@" not in item


# ---------------------------------------------------------------------------
# Idempotency and invariant preservation
# ---------------------------------------------------------------------------


def test_redaction_preserves_interaction_id_and_raw_ref() -> None:
    record = _record(summary="PII here: test@example.com")
    result = apply_redaction(record, _ctx())
    assert result.record.interaction_id == record.interaction_id
    assert result.record.raw_ref == record.raw_ref
    assert result.record.evidence_refs == record.evidence_refs


def test_apply_redaction_on_already_passed_record() -> None:
    first = apply_redaction(_record(), _ctx()).record
    second = apply_redaction(first, _ctx())
    assert second.passed
    assert not second.was_redacted
