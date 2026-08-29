"""
Tests for Agora write authority matrix and role checking gates.

Verifies fail-closed behavior for unauthorized roles (e.g. viewer) and proper
authorization for operator, admin, trader, and approver roles.
"""
from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from services.agora.service import AgoraWriteService
from services.agora.store import AgoraStore
from services.agora.write_authority import (
    AgoraWriteForbiddenError,
    WRITE_AUTHORITY_MATRIX,
    assert_authorized,
    is_authorized,
    matrix_as_list,
)


def test_write_authority_matrix_coverage() -> None:
    matrix = matrix_as_list()
    assert len(matrix) == len(WRITE_AUTHORITY_MATRIX)
    assert len(matrix) >= 20

    # Ensure viewer is never in any authorized list
    for entry in matrix:
        assert "viewer" not in entry["authorized_roles"], f"Viewer must not have write authority in {entry}"


def test_viewer_role_rejected_across_all_agora_mutations(temp_workspace: Path) -> None:
    service = AgoraWriteService()
    viewer = ["viewer"]
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    def _assert_forbidden(fn, *args, **kwargs):
        with pytest.raises(AgoraWriteForbiddenError) as exc_info:
            fn(*args, **kwargs)
        err = exc_info.value
        assert err.status_code == 403
        assert err.error_code == "FORBIDDEN"
        assert err.reason == "Operator does not hold the required command role"
        assert err.precondition_failed == "role_check"
        payload = err.to_dict()
        assert payload["code"] == "FORBIDDEN"
        assert payload["details"]["reason"] == "Operator does not hold the required command role"
        assert payload["details"]["precondition_failed"] == "role_check"

    # Sessions
    _assert_forbidden(
        service.create_session,
        session_id=session_id,
        title="Unauthorized Session",
        actor_id="viewer-user",
        actor_roles=viewer,
        payload={"mode": "committee"},
    )
    _assert_forbidden(service.open_committee_session, session_id, actor_id="viewer-user", actor_roles=viewer)
    _assert_forbidden(service.close_committee_session, session_id, actor_id="viewer-user", actor_roles=viewer)
    _assert_forbidden(service.close_session, session_id, actor_id="viewer-user", actor_roles=viewer)
    _assert_forbidden(
        service.append_session_message,
        session_id=session_id,
        message_id="msg-001",
        content="Forbidden message",
        actor_id="viewer-user",
        actor_roles=viewer,
        payload={},
    )

    # Committee Memos
    _assert_forbidden(
        service.submit_committee_memo,
        session_id=session_id,
        memo_id="memo-001",
        actor_id="viewer-user",
        actor_roles=viewer,
        payload={"memoType": "summary"},
    )
    _assert_forbidden(
        service.publish_committee_memo,
        session_id=session_id,
        memo_id="memo-001",
        actor_id="viewer-user",
        actor_roles=viewer,
    )

    # Evidence Packs
    _assert_forbidden(service.create_evidence_pack, session_id, payload={}, actor_id="viewer-user", actor_roles=viewer)
    _assert_forbidden(service.append_evidence_files, session_id, files=[], actor_id="viewer-user", actor_roles=viewer)

    # Notes, Insights, Examples
    _assert_forbidden(service.create_note, note_id="note-001", title="T", body="B", actor_id="viewer-user", actor_roles=viewer, payload={})
    _assert_forbidden(service.create_insight, insight_id="ins-001", summary="S", actor_id="viewer-user", actor_roles=viewer, payload={})
    _assert_forbidden(service.create_training_example, example_id="ex-001", payload={}, actor_id="viewer-user", actor_roles=viewer)

    # Signals & Feedback
    _assert_forbidden(service.create_signal, signal_id="sig-001", title="T", body="B", actor_id="viewer-user", actor_roles=viewer, payload={})
    _assert_forbidden(
        service.record_signal_feedback,
        signal_id="sig-001",
        decision="approved",
        confidence=90,
        reason="R",
        actor_id="viewer-user",
        actor_roles=viewer,
    )
    _assert_forbidden(service.create_feedback, signal_id="sig-001", verdict="pass", memo="M", actor_id="viewer-user", actor_roles=viewer)

    # Handoffs & Audits
    _assert_forbidden(
        service.create_handoff,
        handoff_id="h-001",
        handoff_type="type-a",
        source_route="/r1",
        source_entity={},
        destination_route="/r2",
        destination_queue="q1",
        priority="high",
        payload={},
        actor_id="viewer-user",
        actor_roles=viewer,
    )
    _assert_forbidden(service.record_audit_event, {"action": "test", "actorId": "viewer-user"}, actor_roles=viewer)

    # Decision Journal
    _assert_forbidden(
        service.create_decision_journal_entry,
        entry_id="j-001",
        title="T",
        body="B",
        tags=[],
        linked_strategy_ids=[],
        linked_persona_ids=[],
        visibility="private",
        actor_id="viewer-user",
        actor_roles=viewer,
    )
    _assert_forbidden(
        service.patch_decision_journal_entry,
        entry_id="j-001",
        patch={},
        actor_id="viewer-user",
        actor_roles=viewer,
        correlation_id="corr-001",
        idempotency_key="idem-001",
        request_hash="hash-001",
    )

    # Workshops, Proposals & Interactions
    _assert_forbidden(service.create_workshop, initial_message="Hello", created_by="viewer-user", actor_roles=viewer)
    _assert_forbidden(service.append_workshop_message, workshop_id="ws-001", content="Hi", actor_id="viewer-user", actor_roles=viewer)
    _assert_forbidden(service.create_proposal, payload={}, created_by="viewer-user", actor_roles=viewer)
    _assert_forbidden(
        service.modify_proposal,
        proposal_id="prop-001",
        action="modify",
        reason="R",
        proposed_value={},
        actor_id="viewer-user",
        actor_roles=viewer,
    )
    _assert_forbidden(service.create_interaction, payload={}, created_by="viewer-user", actor_roles=viewer)
    _assert_forbidden(service.resolve_interaction_context, payload={}, actor_id="viewer-user", actor_roles=viewer)


def test_operator_and_admin_authorized_mutations(temp_workspace: Path) -> None:
    service = AgoraWriteService()
    operator = ["operator"]
    admin = ["admin"]
    approver = ["approver"]
    suffix = uuid.uuid4().hex[:8]

    # Create session as operator
    session_id = f"sess-auth-{suffix}"
    session = service.create_session(
        session_id=session_id,
        title="Authorized Committee",
        actor_id="op-1",
        actor_roles=operator,
        payload={"mode": "committee"},
    )
    assert session is not None
    assert session.status == "active"

    # Open committee as operator
    opened = service.open_committee_session(session_id, actor_id="op-1", actor_roles=operator)
    assert opened is not None
    assert opened.status == "open"

    # Submit memo as operator
    memo_id = f"memo-auth-{suffix}"
    memo = service.submit_committee_memo(
        session_id=session_id,
        memo_id=memo_id,
        actor_id="op-1",
        actor_roles=operator,
        payload={"memoType": "summary", "summary": "Approved changes"},
    )
    assert memo is not None
    assert memo.status == "draft"

    # Publish memo as approver
    published = service.publish_committee_memo(
        session_id=session_id,
        memo_id=memo_id,
        actor_id="approver-1",
        actor_roles=approver,
    )
    assert published is not None
    assert published.status == "published"

    # Close committee as admin
    closed = service.close_committee_session(
        session_id=session_id,
        actor_id="admin-1",
        actor_roles=admin,
        outcome="completed",
    )
    assert closed is not None
    assert closed.status == "closed"

    # Workshop as operator
    ws = service.create_workshop(
        initial_message="Workshop init",
        created_by="op-1",
        actor_roles=operator,
    )
    assert ws.workshop_id is not None

    # Proposal as strategy.review
    proposal = service.create_proposal(
        payload={
            "target_kind": "strategy",
            "target_id": "strat-auth",
            "current_value": {"risk": 0.1},
            "proposed_value": {"risk": 0.08},
            "rationale": "lower risk",
        },
        created_by="risk-officer",
        actor_roles=["strategy.review"],
    )
    assert proposal.proposal_id is not None
    assert proposal.revision == 1

    # Modify proposal as admin
    modified = service.modify_proposal(
        proposal_id=proposal.proposal_id,
        action="modify_value",
        reason="risk adjusted",
        proposed_value={"risk": 0.07},
        actor_id="admin-1",
        actor_roles=admin,
    )
    assert modified is not None
    assert modified.revision == 2
