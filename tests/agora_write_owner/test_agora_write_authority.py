"""
Tests for Agora role-based write authority gates and matrix enforcement.
"""
from __future__ import annotations

import pytest

from services.agora.service import AgoraWriteService
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

    # Ensure key operations are present
    ops = {(entry["resource_type"], entry["operation"]) for entry in matrix}
    assert ("AgoraSession", "create") in ops
    assert ("AgoraSession", "open") in ops
    assert ("AgoraSession", "close") in ops
    assert ("AgoraCommitteeMemo", "submit") in ops
    assert ("AgoraCommitteeMemo", "publish") in ops
    assert ("AgoraSignal", "create") in ops
    assert ("DecisionJournalEntry", "patch") in ops


def test_is_authorized_and_assert_authorized() -> None:
    assert is_authorized("AgoraSession", "create", "operator") is True
    assert is_authorized("AgoraSession", "create", ["guest", "admin"]) is True
    assert is_authorized("AgoraSession", "create", "unauthorized_role") is False
    assert is_authorized("UnknownResource", "create", "operator") is False

    # assert_authorized raises when forbidden
    assert_authorized("AgoraSession", "create", "operator")
    with pytest.raises(AgoraWriteForbiddenError) as exc_info:
        assert_authorized("AgoraSession", "create", "viewer")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "FORBIDDEN"
    assert exc_info.value.resource_type == "AgoraSession"
    assert exc_info.value.operation == "create"
    assert exc_info.value.to_dict()["code"] == "FORBIDDEN"


def test_agora_write_service_enforces_authority() -> None:
    svc = AgoraWriteService()

    # 1. Unauthorized caller fails closed for create_session
    with pytest.raises(AgoraWriteForbiddenError):
        svc.create_session(
            session_id="sess-unauth-1",
            title="Unauthorized Session",
            actor_id="hacker",
            actor_roles=["guest"],
        )

    # 2. Authorized caller succeeds
    session = svc.create_session(
        session_id="sess-auth-1",
        title="Authorized Session",
        actor_id="alice",
        actor_roles=["operator"],
        payload={"mode": "general"},
    )
    assert session.sessionId == "sess-auth-1"

    # 3. Open committee with unauthorized role fails
    with pytest.raises(AgoraWriteForbiddenError):
        svc.open_committee_session("sess-auth-1", actor_id="guest-1", actor_roles=["guest"])

    # 4. Open committee with authorized role succeeds
    opened = svc.open_committee_session("sess-auth-1", actor_id="alice", actor_roles=["admin"])
    assert opened is not None
    assert opened.status == "open"

    # 5. Memo submit & publish permission checks
    with pytest.raises(AgoraWriteForbiddenError):
        svc.submit_committee_memo("sess-auth-1", "memo-1", "eve", ["guest"], {})

    memo = svc.submit_committee_memo(
        "sess-auth-1", "memo-1", "alice", ["trader"], {"summary": "Valid summary"}
    )
    assert memo is not None

    with pytest.raises(AgoraWriteForbiddenError):
        svc.publish_committee_memo("sess-auth-1", "memo-1", "analyst-1", ["analyst"])

    published = svc.publish_committee_memo("sess-auth-1", "memo-1", "bob", ["approver"])
    assert published is not None
    assert published.status == "published"

    # 6. Signal creation authorization
    with pytest.raises(AgoraWriteForbiddenError):
        svc.create_signal("sig-1", "AAPL", "BUY", 0.9, "viewer-1", ["viewer"])

    sig = svc.create_signal("sig-1", "AAPL", "BUY", 0.9, "researcher-1", ["researcher"])
    assert sig.signalId == "sig-1"

    # 7. Note creation authorization
    with pytest.raises(AgoraWriteForbiddenError):
        svc.create_note("note-1", "Title", "Content", "viewer-1", ["viewer"])

    note = svc.create_note("note-1", "Title", "Content", "analyst-1", ["analyst"])
    assert note.noteId == "note-1"
