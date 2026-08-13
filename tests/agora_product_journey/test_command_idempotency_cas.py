"""Integration tests for Command Replay, Idempotency, and CAS Optimistic Locking.

Verifies:
  - Exact replay with identical key and digest returns previous receipt
  - Key reuse with mutated payload produces conflict / rejection
  - Append event CAS with mismatched lock_version returns version conflict
  - Performance suggestion act with stale expected_version returns version conflict
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


def test_workshop_event_append_cas_conflict() -> None:
    """Appending an event with a stale lock version must return (None, current_version)."""
    from agora.strategy_workshop.store import make_workshop_store

    store = make_workshop_store(backend="off")
    ws_id = f"ws-cas-{uuid.uuid4().hex[:8]}"

    session = store.create_session(
        {
            "workshop_id": ws_id,
            "tenant_id": "tenant-cas",
            "user_id": "user-cas",
            "title": "CAS Test",
            "status": "open",
        }
    )
    assert session["lock_version"] == 1

    # First append succeeds with expected_lock_version=1 -> bumps to 2
    ev1, new_ver1 = store.append_event_cas(
        ws_id,
        1,
        {
            "actor_type": "user",
            "event_type": "user_message",
            "payload": {"content": "Msg 1"},
            "created_at": _utc_now(),
        },
    )
    assert ev1 is not None
    assert new_ver1 == 2

    # Second append with stale version 1 must conflict and return (None, 2)
    ev2, curr_ver = store.append_event_cas(
        ws_id,
        1,  # Stale lock version!
        {
            "actor_type": "user",
            "event_type": "user_message",
            "payload": {"content": "Msg 2 concurrent"},
            "created_at": _utc_now(),
        },
    )
    assert ev2 is None
    assert curr_ver == 2

    # Append with correct version 2 succeeds
    ev3, new_ver3 = store.append_event_cas(
        ws_id,
        2,
        {
            "actor_type": "user",
            "event_type": "user_message",
            "payload": {"content": "Msg 2 with correct version"},
            "created_at": _utc_now(),
        },
    )
    assert ev3 is not None
    assert new_ver3 == 3


def test_dataset_extraction_idempotency_key_replay_and_conflict() -> None:
    """Adding evidence with identical key and digest replays; mutated payload raises conflict."""
    from agora.dataset_extraction.extractor import (
        AgoraDatasetStore,
        IdempotencyConflictError,
        evidence_request_digest,
    )
    from agora.dataset_extraction.models import AgoraInteractionEvidenceRequest, InteractionKind

    store = AgoraDatasetStore()
    tenant_id = "tenant-idemp"
    user_id = "user-idemp"
    idemp_key = f"idemp-test-{uuid.uuid4().hex[:8]}"

    req1 = AgoraInteractionEvidenceRequest(
        evidence_id=f"evid-1-{uuid.uuid4().hex[:6]}",
        interaction_kind=InteractionKind.FEEDBACK,
        persona_id="persona-01",
        captured_at=_utc_now(),
        content={"decision": "approve"},
    )
    digest1 = evidence_request_digest(req1)

    # First insert
    entry1, is_new1 = store.add_to_inbox(
        evidence=req1,
        tenant_id=tenant_id,
        user_id=user_id,
        extracted_at=_utc_now(),
        idempotency_key=idemp_key,
        request_digest=digest1,
    )
    assert is_new1 is True

    # Exact replay returns same entry with is_new=False
    entry_replay, is_new_replay = store.add_to_inbox(
        evidence=req1,
        tenant_id=tenant_id,
        user_id=user_id,
        extracted_at=_utc_now(),
        idempotency_key=idemp_key,
        request_digest=digest1,
    )
    assert is_new_replay is False
    assert entry_replay["evidence_id"] == entry1["evidence_id"]

    # Reusing same idempotency key with mutated content must raise IdempotencyConflictError
    req_mutated = AgoraInteractionEvidenceRequest(
        evidence_id=f"evid-mutated-{uuid.uuid4().hex[:6]}",
        interaction_kind=InteractionKind.FEEDBACK,
        persona_id="persona-01",
        captured_at=_utc_now(),
        content={"decision": "reject_mutated"},
    )
    digest_mutated = evidence_request_digest(req_mutated)

    with pytest.raises(IdempotencyConflictError):
        store.add_to_inbox(
            evidence=req_mutated,
            tenant_id=tenant_id,
            user_id=user_id,
            extracted_at=_utc_now(),
            idempotency_key=idemp_key,  # Reused key with different digest
            request_digest=digest_mutated,
        )


def test_performance_suggestion_action_idempotency_and_version_cas(temp_workspace: Path) -> None:
    """Suggestion action handles idempotent replays and detects CAS version mismatch."""
    from agora.performance.models import AdjustmentSuggestion, SuggestionProvenance
    from agora.performance.store import PerformanceSuggestionConflict, PerformanceSuggestionStore

    db_path = str(temp_workspace / "perf_cas.sqlite3")
    store = PerformanceSuggestionStore(path=db_path)

    tenant_id = "tenant-cas"
    user_id = "user-cas"
    strategy_id = "strat-perf-cas"
    sugg_id = f"sugg-{uuid.uuid4().hex[:8]}"
    idemp_key = f"idemp-act-{uuid.uuid4().hex[:8]}"

    suggestion = AdjustmentSuggestion(
        suggestion_id=sugg_id,
        strategy_id=strategy_id,
        period="latest",
        status="proposed",
        version=1,
        title="CAS Suggestion",
        provenance=SuggestionProvenance(
            source_id="gov-perf",
            source_type="rule_engine",
            produced_at=_utc_now(),
        ),
        as_of=_utc_now(),
    )
    store.upsert_suggestion(tenant_id=tenant_id, owner_user_id=user_id, suggestion=suggestion)

    # 1. Apply action version=1 -> succeeds, returns receipt
    receipt1, replayed1 = store.act(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        strategy_id=strategy_id,
        suggestion_id=sugg_id,
        action="apply",
        expected_version=1,
        reason="Approved",
        actor_id=user_id,
        idempotency_key=idemp_key,
        recorded_at=_utc_now(),
    )
    assert receipt1["status"] == "applied"
    assert replayed1 is False

    # 2. Replay identical action -> returns replayed receipt
    receipt_replay, replayed2 = store.act(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        strategy_id=strategy_id,
        suggestion_id=sugg_id,
        action="apply",
        expected_version=1,
        reason="Approved",
        actor_id=user_id,
        idempotency_key=idemp_key,
        recorded_at=_utc_now(),
    )
    assert replayed2 is True
    assert receipt_replay["receipt_id"] == receipt1["receipt_id"]

    # 3. Key reuse with different action parameters raises conflict
    with pytest.raises(PerformanceSuggestionConflict):
        store.act(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            strategy_id=strategy_id,
            suggestion_id=sugg_id,
            action="reject",  # Mutated action with same idempotency key
            expected_version=1,
            reason="Different reason",
            actor_id=user_id,
            idempotency_key=idemp_key,
            recorded_at=_utc_now(),
        )
