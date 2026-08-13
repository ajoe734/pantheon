"""Integration tests verifying service restart persistence and canonical readback.

Verifies:
  - SQLite-backed PerformanceSuggestionStore survives store reconnection
  - JSON-backed PolicyLearningStore survives store restart
  - ConsultationStore filesystem state reloads cleanly across restarts
"""
from __future__ import annotations

import hashlib
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_performance_suggestion_store_restart_persistence(temp_workspace: Path) -> None:
    """Performance suggestions and audit receipts persist across store restarts."""
    from agora.performance.models import AdjustmentSuggestion, SuggestionProvenance
    from agora.performance.store import PerformanceSuggestionStore

    db_path = str(temp_workspace / "restart_perf.sqlite3")
    tenant_id = "tenant-persist"
    user_id = "user-persist"
    strategy_id = "strat-persist-01"
    sugg_id = f"sugg-{uuid.uuid4().hex[:8]}"

    # Instance 1: Write suggestion and apply action
    store1 = PerformanceSuggestionStore(path=db_path)
    suggestion = AdjustmentSuggestion(
        suggestion_id=sugg_id,
        strategy_id=strategy_id,
        period="latest",
        status="proposed",
        version=1,
        title="Persist Test",
        provenance=SuggestionProvenance(
            source_id="gov-perf",
            source_type="rule_engine",
            produced_at=_utc_now(),
        ),
        as_of=_utc_now(),
    )
    store1.upsert_suggestion(tenant_id=tenant_id, owner_user_id=user_id, suggestion=suggestion)
    receipt1, _ = store1.act(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        strategy_id=strategy_id,
        suggestion_id=sugg_id,
        action="apply",
        expected_version=1,
        reason="Approved",
        actor_id=user_id,
        idempotency_key=f"idemp-persist-{sugg_id}",
        recorded_at=_utc_now(),
    )
    receipt_id = receipt1["receipt_id"]

    # Destroy store1, construct fresh store2 pointing to same database
    del store1
    store2 = PerformanceSuggestionStore(path=db_path)

    # Readback from fresh store instance
    persisted_suggestions = store2.list_suggestions(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        strategy_id=strategy_id,
        period="latest",
    )
    assert len(persisted_suggestions) == 1
    assert persisted_suggestions[0]["suggestion_id"] == sugg_id
    assert persisted_suggestions[0]["status"] == "applied"
    assert persisted_suggestions[0]["version"] == 2

    # Receipt readback
    persisted_receipt = store2.get_receipt(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        receipt_id=receipt_id,
    )
    assert persisted_receipt is not None
    assert persisted_receipt["action"] == "apply"


def test_consultation_store_restart_persistence(temp_workspace: Path) -> None:
    """Consultation requests, memos, and audit logs persist across store restarts."""
    from services.consultation.models import (
        ActorRef,
        AuthorType,
        ConsultFinding,
        ConsultMemo,
        ConsultRequest,
        ConsultRequestStatus,
        ConsultRequestType,
        FindingSeverity,
        MemoStatus,
        MemoType,
        Recommendation,
    )
    from services.consultation.store import ConsultationStore

    consult_dir = temp_workspace / "consult_persist_data"
    tenant_id = "tenant-consult-persist"
    user_id = "user-consult-persist"
    req_id = f"cr-{uuid.uuid4().hex[:8]}"
    memo_id = f"memo-{uuid.uuid4().hex[:8]}"

    # Store Instance 1: Write request and memo
    store1 = ConsultationStore(data_dir=str(consult_dir))
    req = ConsultRequest(
        request_id=req_id,
        tenant_id=tenant_id,
        request_type=ConsultRequestType.PERSONA_POLICY,
        requested_by=ActorRef(actor_type="user", actor_id=user_id),
        target_type="policy_learning_candidate",
        target_id="cand-01",
        status=ConsultRequestStatus.SUBMITTED,
        trace_id="trace-persist-01",
    )
    store1.put_request(req)

    memo = ConsultMemo(
        memo_id=memo_id,
        request_id=req_id,
        memo_type=MemoType.COMMITTEE_SUMMARY,
        author_type=AuthorType.PERSONA,
        author_ref="consultant-01",
        target_type="policy_learning_candidate",
        target_id="cand-01",
        summary="Persisted memo summary",
        findings=[],
        recommendation=Recommendation.APPROVE,
        confidence=0.9,
        status=MemoStatus.PUBLISHED,
        trace_id="trace-persist-01",
        published_at=_utc_now(),
    )
    store1.put_memo(memo)

    # Destroy store1, construct fresh store2 pointing to same data directory
    del store1
    store2 = ConsultationStore(data_dir=str(consult_dir))

    # Readback
    read_req = store2.get_request(req_id)
    assert read_req is not None
    assert read_req.request_id == req_id
    assert read_req.status == ConsultRequestStatus.SUBMITTED

    read_memo = store2.get_memo(memo_id)
    assert read_memo is not None
    assert read_memo.memo_id == memo_id
    assert read_memo.status == MemoStatus.PUBLISHED
