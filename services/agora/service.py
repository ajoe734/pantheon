"""
Thin Agora Write Service.

Coordinates writes for all Agora direct-write groups with strict write-authority
matrix enforcement, delegating persistence directly to AgoraStore (backed by
Pantheon's PostgresJsonOwnerStore).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from .store import AgoraStore, DictRecord
from .write_authority import assert_authorized


class AgoraWriteService:
    """
    Direct write owner service for Agora operations.
    """

    def __init__(
        self,
        store: Optional[AgoraStore] = None,
        *,
        dsn: Optional[str] = None,
        schema: str = "agora",
    ) -> None:
        self.store = store or AgoraStore(dsn=dsn, schema=schema)

    # -------------------------------------------------------------------------
    # Sessions & Committees
    # -------------------------------------------------------------------------
    def create_session(
        self,
        session_id: str,
        title: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraSession", "create", actor_roles)
        return self.store.create_session(
            session_id=session_id,
            title=title,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    def open_committee_session(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        opened_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSession", "open", actor_roles)
        return self.store.open_committee_session(session_id, opened_at=opened_at)

    def close_committee_session(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
        memo_ids: Optional[List[str]] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSession", "close", actor_roles)
        return self.store.close_committee_session(
            session_id, closed_at=closed_at, outcome=outcome, memo_ids=memo_ids
        )

    def close_session(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSession", "close", actor_roles)
        return self.store.close_session(session_id, closed_at=closed_at, outcome=outcome)

    def append_session_message(
        self,
        session_id: str,
        message_id: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSessionMessage", "append", actor_roles)
        return self.store.append_session_message(
            session_id=session_id,
            message_id=message_id,
            content=content,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    # -------------------------------------------------------------------------
    # Committee Memos
    # -------------------------------------------------------------------------
    def submit_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraCommitteeMemo", "submit", actor_roles)
        return self.store.submit_committee_memo(
            session_id=session_id,
            memo_id=memo_id,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    def publish_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        published_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraCommitteeMemo", "publish", actor_roles)
        return self.store.publish_committee_memo(
            session_id=session_id,
            memo_id=memo_id,
            actor_id=actor_id,
            published_at=published_at,
        )

    # -------------------------------------------------------------------------
    # Evidence Packs & Files
    # -------------------------------------------------------------------------
    def create_evidence_pack(
        self,
        session_id: str,
        payload: Optional[Dict[str, Any]],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraCommitteeEvidencePack", "create", actor_roles)
        return self.store.create_evidence_pack(
            session_id=session_id,
            payload=payload or {},
            actor_id=actor_id,
            created_at=created_at,
        )

    def append_evidence_files(
        self,
        session_id: str,
        files: List[Dict[str, Any]],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        uploaded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraCommitteeEvidencePack", "append_files", actor_roles)
        return self.store.append_evidence_files(
            session_id=session_id,
            files=files,
            actor_id=actor_id,
            uploaded_at=uploaded_at,
        )

    # -------------------------------------------------------------------------
    # Notes, Insights & Training Examples
    # -------------------------------------------------------------------------
    def create_note(
        self,
        note_id: str,
        title: str,
        body: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraNote", "create", actor_roles)
        return self.store.create_note(
            note_id=note_id,
            title=title,
            body=body,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    def create_insight(
        self,
        insight_id: str,
        summary: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraInsight", "create", actor_roles)
        return self.store.create_insight(
            insight_id=insight_id,
            summary=summary,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    def create_training_example(
        self,
        example_id: str,
        payload: Dict[str, Any],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraTrainingExample", "create", actor_roles)
        return self.store.create_training_example(
            example_id=example_id,
            payload=payload,
            actor_id=actor_id,
            created_at=created_at,
        )

    # -------------------------------------------------------------------------
    # Signals & Feedback
    # -------------------------------------------------------------------------
    def create_signal(
        self,
        signal_id: str,
        title: str,
        body: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraSignal", "create", actor_roles)
        return self.store.create_signal(
            signal_id=signal_id,
            title=title,
            body=body,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    def record_signal_feedback(
        self,
        signal_id: str,
        decision: str,
        confidence: float,
        reason: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        edit_window_seconds: int = 300,
        recorded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSignalFeedback", "record", actor_roles)
        return self.store.record_signal_feedback(
            signal_id=signal_id,
            decision=decision,
            confidence=confidence,
            reason=reason,
            actor_id=actor_id,
            edit_window_seconds=edit_window_seconds,
            recorded_at=recorded_at,
        )

    def create_feedback(
        self,
        signal_id: str,
        verdict: str,
        memo: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraFeedback", "create", actor_roles)
        return self.store.create_feedback(
            signal_id=signal_id,
            verdict=verdict,
            memo=memo,
            actor_id=actor_id,
            created_at=created_at,
        )

    # -------------------------------------------------------------------------
    # Handoffs & Audit Events
    # -------------------------------------------------------------------------
    def create_handoff(
        self,
        handoff_id: str,
        handoff_type: str,
        source_route: str,
        source_entity: Dict[str, Any],
        destination_route: str,
        destination_queue: str,
        priority: str,
        payload: Dict[str, Any],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraHandoff", "create", actor_roles)
        return self.store.create_handoff(
            handoff_id=handoff_id,
            handoff_type=handoff_type,
            source_route=source_route,
            source_entity=source_entity,
            destination_route=destination_route,
            destination_queue=destination_queue,
            priority=priority,
            payload=payload,
            actor_id=actor_id,
            created_at=created_at,
        )

    def record_audit_event(
        self,
        event: Dict[str, Any],
        actor_roles: Optional[Union[Sequence[str], str]] = None,
    ) -> DictRecord:
        if actor_roles is not None:
            assert_authorized("AgoraAuditEvent", "record", actor_roles)
        return self.store.record_audit_event(event)

    # -------------------------------------------------------------------------
    # Decision Journal
    # -------------------------------------------------------------------------
    def create_decision_journal_entry(
        self,
        entry_id: str,
        title: str,
        body: str,
        tags: List[str],
        linked_strategy_ids: List[str],
        linked_persona_ids: List[str],
        visibility: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("DecisionJournalEntry", "create", actor_roles)
        return self.store.create_journal_entry(
            entry_id=entry_id,
            title=title,
            body=body,
            tags=tags,
            linked_strategy_ids=linked_strategy_ids,
            linked_persona_ids=linked_persona_ids,
            visibility=visibility,
            actor_id=actor_id,
            created_at=created_at,
        )

    def patch_decision_journal_entry(
        self,
        entry_id: str,
        patch: Dict[str, Any],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        correlation_id: str,
        idempotency_key: str,
        request_hash: str,
        patched_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("DecisionJournalEntry", "patch", actor_roles)
        return self.store.patch_journal_entry(
            entry_id=entry_id,
            patch=patch,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            patched_at=patched_at,
        )

    # -------------------------------------------------------------------------
    # Strategy Workshops, Proposals & Interactions
    # -------------------------------------------------------------------------
    def create_workshop(
        self,
        initial_message: str,
        created_by: str,
        actor_roles: Union[Sequence[str], str],
        workshop_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraWorkshop", "create", actor_roles)
        return self.store.create_workshop(
            initial_message=initial_message,
            created_by=created_by,
            workshop_id=workshop_id,
            created_at=created_at,
        )

    def append_workshop_message(
        self,
        workshop_id: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        message_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraWorkshop", "mutate", actor_roles)
        return self.store.append_workshop_message(
            workshop_id=workshop_id,
            content=content,
            actor_id=actor_id,
            message_id=message_id,
            created_at=created_at,
        )

    def create_proposal(
        self,
        payload: Dict[str, Any],
        created_by: str,
        actor_roles: Union[Sequence[str], str],
        proposal_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraProposal", "create", actor_roles)
        return self.store.create_proposal(
            payload=payload,
            created_by=created_by,
            proposal_id=proposal_id,
            created_at=created_at,
        )

    def modify_proposal(
        self,
        proposal_id: str,
        action: str,
        reason: str,
        proposed_value: Optional[Dict[str, Any]],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        updated_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraProposal", "modify", actor_roles)
        return self.store.modify_proposal(
            proposal_id=proposal_id,
            action=action,
            reason=reason,
            proposed_value=proposed_value,
            actor_id=actor_id,
            updated_at=updated_at,
        )

    def create_interaction(
        self,
        payload: Dict[str, Any],
        created_by: str,
        actor_roles: Union[Sequence[str], str],
        interaction_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraInteraction", "create", actor_roles)
        return self.store.create_interaction(
            payload=payload,
            created_by=created_by,
            interaction_id=interaction_id,
            created_at=created_at,
        )

    def resolve_interaction_context(
        self,
        payload: Dict[str, Any],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
    ) -> Dict[str, Any]:
        assert_authorized("AgoraInteraction", "resolve_context", actor_roles)
        return {
            "status": "resolved",
            "actor_id": actor_id,
            "resolved_refs": list(payload.get("context_refs") or []),
            "context_summary": str(payload.get("summary") or ""),
        }


__all__ = ["AgoraWriteService"]
