"""
Thin, concrete Agora write-owner storage over Pantheon PostgresJsonOwnerStore.

Provides durable persistence for Agora write groups:
- Core Sessions, Committees & Evidence Packs
- Notes, Insights & Training Examples
- Signals & Signal Feedback
- Handoffs & Audit Events
- Decision Journal (with versioning, diff calculation, and idempotency protection)
- Workshops, Proposals & Interactions

Does not use SQLite, JSON file fallbacks, or in-memory caches.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional, Sequence
import uuid

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DictRecord(dict):
    """Thin dictionary supporting attribute-style property access for convenience."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"DictRecord has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


class AgoraStore:
    """
    Concrete persistent store for Agora write operations.

    Built directly on Pantheon's durable foundation: PostgresJsonOwnerStore.
    Every mutation persists durably to PostgreSQL; every read fetches fresh data
    directly from PostgreSQL without caching.
    """

    def __init__(
        self,
        *,
        dsn: Optional[str] = None,
        schema: str = "agora",
        bootstrap: bool = True,
    ) -> None:
        self.dsn = (
            dsn
            or os.getenv("AGORA_STORE_DSN")
            or os.getenv("DATABASE_URL")
            or "postgresql://postgres@localhost:5432/pantheon"
        ).strip()
        self.schema = schema

        self._sessions = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.sessions", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._memos = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.memos", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._evidence_packs = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.evidence_packs", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._notes = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.notes", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._insights = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.insights", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._training_examples = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.training_examples", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._signals = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.signals", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._signal_feedback = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.signal_feedback", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._feedback = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.feedback", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._handoffs = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.handoffs", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._audit_events = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.audit_events", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._journal = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.decision_journal", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._journal_audit = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.decision_journal_audit", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._journal_idempotency = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.decision_journal_idempotency", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._workshops = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.workshops", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._proposals = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.proposals", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._interactions = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{schema}.interactions", owner_service="agora-svc", bootstrap=bootstrap
        )

    # -------------------------------------------------------------------------
    # Sessions & Committees
    # -------------------------------------------------------------------------
    def create_session(
        self,
        session_id: str,
        title: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        timestamp = created_at or _utc_now_rfc3339()
        record = DictRecord({
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": str(payload.get("mode") or payload.get("sessionType") or "quick_ask"),
            "status": str(payload.get("status") or "active"),
            "participants": list(payload.get("participants") or []),
            "contextRefs": list(payload.get("contextRefs") or payload.get("context_refs") or []),
            "messages": list(payload.get("messages") or []),
            "createdBy": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        })
        if payload.get("quorumState") is not None:
            record["quorumState"] = dict(payload["quorumState"])
        if payload.get("consensusState") is not None:
            record["consensusState"] = dict(payload["consensusState"])
        if payload.get("participantRoster") is not None:
            record["participantRoster"] = list(payload["participantRoster"])
        if payload.get("linkedRequestId") or payload.get("linked_request_id"):
            record["linkedRequestId"] = payload.get("linkedRequestId") or payload.get("linked_request_id")
        if payload.get("targetEntity") is not None:
            record["targetEntity"] = dict(payload["targetEntity"])
        self._sessions.put(session_id, record)
        return record

    def get_session(self, session_id: str) -> Optional[DictRecord]:
        data = self._sessions.get(session_id)
        return DictRecord(data) if data else None

    def list_sessions(
        self,
        status: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._sessions.list_all()]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        if mode:
            rows = [r for r in rows if r.get("mode") == mode]
        rows.sort(key=lambda r: r.get("updatedAt", ""), reverse=True)
        return rows

    def open_committee_session(self, session_id: str, opened_at: Optional[str] = None) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session or str(session.get("mode", "")).strip() != "committee":
            return None
        now = opened_at or _utc_now_rfc3339()
        session["status"] = "open"
        session["openedAt"] = now
        session["updatedAt"] = now
        self._sessions.put(session_id, session)
        return session

    def close_committee_session(
        self,
        session_id: str,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
        memo_ids: Optional[List[str]] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session or str(session.get("mode", "")).strip() != "committee":
            return None
        now = closed_at or _utc_now_rfc3339()
        session["status"] = "closed"
        session["closedAt"] = now
        session["updatedAt"] = now
        if outcome is not None:
            session["outcome"] = outcome
        if memo_ids is not None:
            session["memoIds"] = list(memo_ids)
        self._sessions.put(session_id, session)
        return session

    def close_session(
        self,
        session_id: str,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = closed_at or _utc_now_rfc3339()
        session["status"] = "closed"
        session["closedAt"] = now
        session["updatedAt"] = now
        if outcome is not None:
            session["outcome"] = outcome
        self._sessions.put(session_id, session)
        return session

    def append_session_message(
        self,
        session_id: str,
        message_id: str,
        content: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = created_at or _utc_now_rfc3339()
        msg = DictRecord({
            "id": message_id,
            "sessionId": session_id,
            "sender": dict(payload.get("sender") or {"type": "operator", "id": actor_id}),
            "role": str(payload.get("role") or "user"),
            "content": content,
            "language": str(payload.get("language") or "zh-TW"),
            "attachments": list(payload.get("attachments") or []),
            "citations": list(payload.get("citations") or []),
            "annotations": list(payload.get("annotations") or []),
            "createdAt": now,
        })
        messages = list(session.get("messages") or [])
        messages.append(msg)
        session["messages"] = messages
        session["updatedAt"] = now
        self._sessions.put(session_id, session)
        return msg

    # -------------------------------------------------------------------------
    # Committee Memos
    # -------------------------------------------------------------------------
    def submit_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session or str(session.get("mode", "")).strip() != "committee":
            return None
        now = created_at or _utc_now_rfc3339()
        memo_type = str(payload.get("memoType") or payload.get("memo_type") or "committee_summary").strip() or "committee_summary"
        author_ref = dict(payload.get("authorRef") or payload.get("author_ref") or {"type": "operator", "id": actor_id})
        evidence_refs = list(payload.get("evidenceRefs") or payload.get("evidence_refs") or [])
        evidence_ref_ids = [str(r.get("id") or r.get("evidence_id") or r.get("ref") or "") for r in evidence_refs if isinstance(r, dict)]

        target = session.get("targetEntity") or {"type": "artifact", "id": session_id}
        memo = DictRecord({
            "id": memo_id,
            "memo_id": memo_id,
            "memo_type": memo_type,
            "status": "draft",
            "lifecycle_state": "draft",
            "linked_session_id": session_id,
            "linked_request_id": payload.get("linkedRequestId") or payload.get("linked_request_id") or session.get("linkedRequestId"),
            "author_ref": author_ref,
            "session_to_memo_mapping": {
                "session_id": session_id,
                "session_type": "committee",
                "memo_type": memo_type,
                "mapping_status": "draft",
                "evidence_ref_ids": evidence_ref_ids,
            },
            "summary": str(payload.get("summary") or "").strip() or None,
            "recommendations": list(payload.get("recommendations") or []),
            "evidence_refs": evidence_refs,
            "created_at": now,
            "published_at": None,
            "published_by": None,
            "governance_target": {
                "type": target.get("type", "artifact"),
                "id": target.get("id", session_id),
                "target_type": target.get("type", "artifact"),
                "target_id": target.get("id", session_id),
            },
        })
        self._memos.put(memo_id, memo)
        return memo

    def get_committee_memo(self, session_id: str, memo_id: str) -> Optional[DictRecord]:
        data = self._memos.get(memo_id)
        if data and data.get("linked_session_id") == session_id:
            return DictRecord(data)
        return None

    def list_committee_memos(self, session_id: str) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._memos.list_all() if r.get("linked_session_id") == session_id]
        rows.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return rows

    def publish_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        published_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        memo = self.get_committee_memo(session_id, memo_id)
        if not memo:
            return None
        now = published_at or _utc_now_rfc3339()
        memo["status"] = "published"
        memo["lifecycle_state"] = "published"
        memo["published_at"] = now
        memo["published_by"] = actor_id
        mapping = memo.get("session_to_memo_mapping")
        if isinstance(mapping, dict):
            mapping["mapping_status"] = "active"
        self._memos.put(memo_id, memo)
        return memo

    # -------------------------------------------------------------------------
    # Evidence Packs & Files
    # -------------------------------------------------------------------------
    def create_evidence_pack(
        self,
        session_id: str,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        existing = self.get_evidence_pack(session_id)
        pack_id = str(
            payload.get("id")
            or payload.get("packId")
            or (existing.get("packId") if existing else f"evpack-{uuid.uuid4().hex[:12]}")
        )
        session = self.get_session(session_id)
        session_target = session.get("targetEntity", {}) if session else {}
        target_type = str(payload.get("targetEntityType") or payload.get("target_entity_type") or session_target.get("type") or "artifact")
        target_id = str(payload.get("targetEntityId") or payload.get("target_entity_id") or session_target.get("id") or session_id)
        now = created_at or _utc_now_rfc3339()

        pack = DictRecord({
            "id": pack_id,
            "packId": pack_id,
            "sessionId": session_id,
            "targetEntityType": target_type,
            "targetEntityId": target_id,
            "uploadedFiles": list(existing.get("uploadedFiles", [])) if existing else [],
            "linkedEntities": list(payload.get("linkedEntities") or payload.get("linked_entities") or []),
            "notes": str(payload.get("notes") or ""),
            "createdBy": existing.get("createdBy") if existing else actor_id,
            "createdAt": existing.get("createdAt") if existing else now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_committee_evidence_service",
            "persistenceMode": "owner_store",
        })
        self._evidence_packs.put(session_id, pack)
        return pack

    def get_evidence_pack(self, session_id: str) -> Optional[DictRecord]:
        data = self._evidence_packs.get(session_id)
        return DictRecord(data) if data else None

    def append_evidence_files(
        self,
        session_id: str,
        files: List[Dict[str, Any]],
        actor_id: str,
        uploaded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = uploaded_at or _utc_now_rfc3339()
        pack = self.get_evidence_pack(session_id)
        if not pack:
            pack = self.create_evidence_pack(
                session_id,
                payload={"targetEntityType": "artifact", "targetEntityId": session_id, "linkedEntities": [], "notes": ""},
                actor_id=actor_id,
                created_at=now,
            )

        new_files: List[Dict[str, Any]] = []
        for item in files:
            file_id = str(item.get("id") or item.get("fileId") or f"evfile-{uuid.uuid4().hex[:12]}")
            f_rec = {
                "id": file_id,
                "fileName": str(item.get("fileName") or item.get("filename") or item.get("name") or ""),
                "mimeType": str(item.get("mimeType") or item.get("mime_type") or ""),
                "sizeBytes": int(item.get("sizeBytes") or item.get("size_bytes") or 0),
                "storageUrl": str(item.get("storageUrl") or item.get("storage_url") or f"agora://committee/{session_id}/evidence/{file_id}"),
                "extractedTextStatus": str(item.get("extractedTextStatus") or item.get("extracted_text_status") or "not_started"),
                "metadata": dict(item.get("metadata") or {}),
                "uploadedBy": actor_id,
                "createdAt": now,
            }
            pack["uploadedFiles"].append(f_rec)
            new_files.append(f_rec)

        pack["updatedAt"] = now
        self._evidence_packs.put(session_id, pack)
        result = DictRecord(dict(pack))
        result["newFiles"] = new_files
        return result

    # -------------------------------------------------------------------------
    # Notes, Insights & Training Examples
    # -------------------------------------------------------------------------
    def create_note(
        self,
        note_id: str,
        title: str,
        body: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        note = DictRecord({
            "id": note_id,
            "note_id": note_id,
            "title": title,
            "body": body,
            "attachment_type": str(payload.get("attachment_type") or "free_standing"),
            "attachment_ref": dict(payload["attachment_ref"]) if payload.get("attachment_ref") is not None else None,
            "owner_ref": dict(payload.get("owner_ref") or {"owner_type": "operator", "owner_id": actor_id}),
            "tags": list(payload.get("tags") or []),
            "linked_evidence_refs": list(payload.get("linked_evidence_refs") or payload.get("linkedEvidenceRefs") or []),
            "linked_memory_anchors": list(payload.get("linked_memory_anchors") or payload.get("linkedMemoryAnchors") or []),
            "created_at": now,
            "updated_at": now,
            "created_by": actor_id,
        })
        self._notes.put(note_id, note)
        return note

    def get_note(self, note_id: str) -> Optional[DictRecord]:
        data = self._notes.get(note_id)
        return DictRecord(data) if data else None

    def list_notes(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._notes.list_all()]
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows

    def create_insight(
        self,
        insight_id: str,
        summary: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        insight = DictRecord({
            "id": insight_id,
            "insight_id": insight_id,
            "summary": summary,
            "scope": str(payload.get("scope") or "global"),
            "scope_ref": payload.get("scope_ref") or payload.get("scopeRef"),
            "status": str(payload.get("status") or "classified"),
            "confidence": dict(payload.get("confidence") or {}),
            "tags": list(payload.get("tags") or []),
            "source_ref": str(payload.get("source_ref") or payload.get("sourceRef") or f"agora:{insight_id}"),
            "supporting_evidence_refs": list(payload.get("supporting_evidence_refs") or payload.get("supportingEvidenceRefs") or []),
            "linked_sources": list(payload.get("linked_sources") or payload.get("linkedSources") or []),
            "aggregation_provenance": {
                "created_by": actor_id,
                "aggregated_at": now,
                **(dict(payload.get("aggregation_provenance") or {})),
            },
            "created_at": now,
            "updated_at": now,
        })
        self._insights.put(insight_id, insight)
        return insight

    def get_insight(self, insight_id: str) -> Optional[DictRecord]:
        data = self._insights.get(insight_id)
        return DictRecord(data) if data else None

    def list_insights(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._insights.list_all()]
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows

    def create_training_example(
        self,
        example_id: str,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        example = DictRecord({
            "id": example_id,
            "trainingExampleId": example_id,
            "source": str(payload.get("source") or "agora"),
            "personaId": payload.get("personaId") or payload.get("persona_id"),
            "input": dict(payload.get("input") or {}),
            "expected": dict(payload.get("expected") or {}),
            "labels": list(payload.get("labels") or []),
            "status": str(payload.get("status") or "draft"),
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        })
        self._training_examples.put(example_id, example)
        return example

    def get_training_example(self, example_id: str) -> Optional[DictRecord]:
        data = self._training_examples.get(example_id)
        return DictRecord(data) if data else None

    def list_training_examples(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._training_examples.list_all()]
        rows.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Signals & Feedback
    # -------------------------------------------------------------------------
    def create_signal(
        self,
        signal_id: str,
        title: str,
        body: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        signal = DictRecord({
            "id": signal_id,
            "signal_id": signal_id,
            "title": title,
            "body": body,
            "market": payload.get("market"),
            "tags": list(payload.get("tags") or []),
            "linkedPersonaIds": list(payload.get("linkedPersonaIds") or payload.get("linked_persona_ids") or []),
            "linkedStrategyIds": list(payload.get("linkedStrategyIds") or payload.get("linked_strategy_ids") or []),
            "severity": str(payload.get("severity") or "info"),
            "status": str(payload.get("status") or "open"),
            "reviewStatus": str(payload.get("reviewStatus") or payload.get("review_status") or "pending_trader_review"),
            "latestFeedbackId": payload.get("latestFeedbackId") or payload.get("latest_feedback_id"),
            "createdAt": now,
            "updatedAt": now,
            "createdBy": actor_id,
            "authorId": actor_id,
        })
        self._signals.put(signal_id, signal)
        return signal

    def get_signal(self, signal_id: str) -> Optional[DictRecord]:
        data = self._signals.get(signal_id)
        return DictRecord(data) if data else None

    def list_signals(
        self,
        review_status: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._signals.list_all()]
        if review_status:
            rows = [r for r in rows if r.get("reviewStatus") == review_status]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return rows

    def record_signal_feedback(
        self,
        signal_id: str,
        decision: str,
        confidence: float,
        reason: str,
        actor_id: str,
        edit_window_seconds: int = 300,
        recorded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        signal = self.get_signal(signal_id)
        if not signal:
            return None
        now = recorded_at or _utc_now_rfc3339()
        feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
        fb = DictRecord({
            "id": feedback_id,
            "feedbackId": feedback_id,
            "signalId": signal_id,
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "actorId": actor_id,
            "createdAt": now,
            "updatedAt": now,
            "editWindowSeconds": edit_window_seconds,
        })
        self._signal_feedback.put(feedback_id, fb)

        signal["reviewStatus"] = decision
        signal["latestFeedbackId"] = feedback_id
        signal["updatedAt"] = now
        self._signals.put(signal_id, signal)
        return fb

    def create_feedback(
        self,
        signal_id: str,
        verdict: str,
        memo: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        signal = self.get_signal(signal_id)
        if not signal:
            return None
        now = created_at or _utc_now_rfc3339()
        feedback_id = f"fb-gen-{uuid.uuid4().hex[:12]}"
        fb = DictRecord({
            "id": feedback_id,
            "feedbackId": feedback_id,
            "signal_id": signal_id,
            "signalId": signal_id,
            "verdict": verdict,
            "memo": memo,
            "author_id": actor_id,
            "authorId": actor_id,
            "created_at": now,
            "createdAt": now,
        })
        self._feedback.put(feedback_id, fb)
        return fb

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
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        handoff = DictRecord({
            "id": handoff_id,
            "handoffId": handoff_id,
            "handoffType": handoff_type,
            "status": "submitted",
            "source": {"route": source_route, "entity": dict(source_entity)},
            "destination": {"route": destination_route, "queue": destination_queue},
            "priority": priority,
            "slaDueAt": str(payload.get("slaDueAt") or payload.get("sla_due_at") or ""),
            "rerouteCount": 0,
            "payload": dict(payload),
            "createdBy": {"type": "operator", "id": actor_id},
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_handoff_service",
            "persistenceMode": "owner_store",
        })
        self._handoffs.put(handoff_id, handoff)
        return handoff

    def get_handoff(self, handoff_id: str) -> Optional[DictRecord]:
        data = self._handoffs.get(handoff_id)
        return DictRecord(data) if data else None

    def list_handoffs(
        self,
        status: Optional[str] = None,
        handoff_type: Optional[str] = None,
    ) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._handoffs.list_all()]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        if handoff_type:
            rows = [r for r in rows if r.get("handoffType") == handoff_type]
        rows.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return rows

    def record_audit_event(self, event: Dict[str, Any]) -> DictRecord:
        audit_id = str(event.get("auditId") or event.get("audit_id") or f"aud-{uuid.uuid4().hex[:12]}")
        now = str(event.get("recordedAt") or event.get("timestamp") or _utc_now_rfc3339())
        details = dict(event)
        audit = DictRecord({
            "id": audit_id,
            "auditId": audit_id,
            "recordedAt": now,
            "details": details,
            **details,
        })
        self._audit_events.put(audit_id, audit)
        return audit

    def list_audit_events(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._audit_events.list_all()]
        rows.sort(key=lambda r: r.get("recordedAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Decision Journal
    # -------------------------------------------------------------------------
    def create_journal_entry(
        self,
        entry_id: str,
        title: str,
        body: str,
        tags: List[str],
        linked_strategy_ids: List[str],
        linked_persona_ids: List[str],
        visibility: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        entry = DictRecord({
            "id": entry_id,
            "title": title,
            "body": body,
            "tags": list(tags),
            "linkedStrategyIds": list(linked_strategy_ids),
            "linkedPersonaIds": list(linked_persona_ids),
            "visibility": visibility,
            "createdAt": now,
            "updatedAt": now,
            "version": 1,
            "createdBy": actor_id,
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": "owner_store",
        })
        self._journal.put(entry_id, entry)
        return entry

    def get_journal_entry(self, entry_id: str) -> Optional[DictRecord]:
        data = self._journal.get(entry_id)
        return DictRecord(data) if data else None

    def list_journal_entries(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._journal.list_all()]
        rows.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return rows

    def patch_journal_entry(
        self,
        entry_id: str,
        patch: Dict[str, Any],
        actor_id: str,
        correlation_id: str,
        idempotency_key: str,
        request_hash: str,
        patched_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        # Idempotency check
        idem = self._journal_idempotency.get(idempotency_key)
        if idem:
            if idem.get("request_hash") == request_hash:
                current_entry = self.get_journal_entry(entry_id)
                return DictRecord({
                    "status": "replayed",
                    "entry": current_entry,
                    "audit": idem.get("audit"),
                    "existing_patch_id": idem.get("patch_id"),
                })
            else:
                return DictRecord({"status": "conflict"})

        current = self.get_journal_entry(entry_id)
        if not current:
            return None

        now = patched_at or _utc_now_rfc3339()
        changed_fields = [k for k, v in patch.items() if current.get(k) != v]
        old_values = {k: current.get(k) for k in changed_fields}
        new_values = {k: patch.get(k) for k in changed_fields}

        current.update(patch)
        current["version"] = int(current.get("version", 1)) + 1
        current["updatedAt"] = now
        self._journal.put(entry_id, current)

        audit_id = f"aud-j-{uuid.uuid4().hex[:12]}"
        audit = DictRecord({
            "id": audit_id,
            "auditId": audit_id,
            "action": "agora.journal.merge_patch",
            "target": {"type": "decision_journal_entry", "id": entry_id},
            "actorId": actor_id,
            "correlationId": correlation_id,
            "idempotencyKey": idempotency_key,
            "recordedAt": now,
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": "owner_store",
            "diff": {
                "changedFields": changed_fields,
                "oldValues": old_values,
                "newValues": new_values,
            },
        })
        self._journal_audit.put(audit_id, audit)

        self._journal_idempotency.put(
            idempotency_key,
            {
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "patch_id": audit_id,
                "status": "updated",
                "audit": dict(audit),
            },
        )

        return DictRecord({
            "status": "updated",
            "entry": current,
            "audit": audit,
        })

    # -------------------------------------------------------------------------
    # Strategy Workshops, Proposals & Interactions
    # -------------------------------------------------------------------------
    def create_workshop(
        self,
        initial_message: str,
        created_by: str,
        workshop_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        ws_id = workshop_id or f"ws-{uuid.uuid4().hex[:12]}"
        now = created_at or _utc_now_rfc3339()
        ws = DictRecord({
            "id": ws_id,
            "workshop_id": ws_id,
            "status": "active",
            "version": 1,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "messages": [
                {
                    "id": f"msg-{uuid.uuid4().hex[:8]}",
                    "content": initial_message,
                    "sender": created_by,
                    "created_at": now,
                }
            ],
        })
        self._workshops.put(ws_id, ws)
        return ws

    def get_workshop(self, workshop_id: str) -> Optional[DictRecord]:
        data = self._workshops.get(workshop_id)
        return DictRecord(data) if data else None

    def append_workshop_message(
        self,
        workshop_id: str,
        content: str,
        actor_id: str,
        message_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        ws = self.get_workshop(workshop_id)
        if not ws:
            return None
        now = created_at or _utc_now_rfc3339()
        m_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
        msg = {
            "id": m_id,
            "content": content,
            "sender": actor_id,
            "created_at": now,
        }
        ws["messages"].append(msg)
        ws["version"] = int(ws.get("version", 1)) + 1
        ws["updated_at"] = now
        self._workshops.put(workshop_id, ws)
        return ws

    def create_proposal(
        self,
        payload: Dict[str, Any],
        created_by: str,
        proposal_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        p_id = proposal_id or f"prop-{uuid.uuid4().hex[:12]}"
        now = created_at or _utc_now_rfc3339()
        prop = DictRecord({
            "id": p_id,
            "proposal_id": p_id,
            "proposal_type": str(payload.get("proposal_type") or "strategy_patch"),
            "target_kind": str(payload.get("target_kind") or "strategy"),
            "target_id": str(payload.get("target_id") or ""),
            "target_version": str(payload.get("target_version") or "v1"),
            "status": str(payload.get("status") or "draft"),
            "revision": int(payload.get("revision") or 1),
            "current_value": dict(payload.get("current_value") or {}),
            "proposed_value": dict(payload.get("proposed_value") or {}),
            "rationale": str(payload.get("rationale") or ""),
            "confidence": payload.get("confidence"),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "change_history": [],
        })
        self._proposals.put(p_id, prop)
        return prop

    def get_proposal(self, proposal_id: str) -> Optional[DictRecord]:
        data = self._proposals.get(proposal_id)
        return DictRecord(data) if data else None

    def modify_proposal(
        self,
        proposal_id: str,
        action: str,
        reason: str,
        proposed_value: Optional[Dict[str, Any]],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        prop = self.get_proposal(proposal_id)
        if not prop:
            return None
        now = updated_at or _utc_now_rfc3339()
        history_entry = {
            "action": action,
            "reason": reason,
            "actor_id": actor_id,
            "at": now,
            "previous_revision": prop.get("revision", 1),
        }
        prop["change_history"].append(history_entry)
        prop["revision"] = int(prop.get("revision", 1)) + 1
        prop["updated_at"] = now
        if action == "approve":
            prop["status"] = "approved"
        elif action == "reject":
            prop["status"] = "rejected"
        elif action == "modify_value" and proposed_value is not None:
            prop["proposed_value"] = dict(proposed_value)
        self._proposals.put(proposal_id, prop)
        return prop

    def create_interaction(
        self,
        payload: Dict[str, Any],
        created_by: str,
        interaction_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        act_id = interaction_id or f"act-{uuid.uuid4().hex[:12]}"
        now = created_at or _utc_now_rfc3339()
        act = DictRecord({
            "id": act_id,
            "interaction_id": act_id,
            "workshop_id": str(payload.get("workshop_id") or ""),
            "mode": str(payload.get("mode") or "propose_action"),
            "environment": str(payload.get("environment") or "paper"),
            "status": str(payload.get("status") or "active"),
            "topic": str(payload.get("topic") or ""),
            "participant_persona_ids": list(payload.get("participant_persona_ids") or []),
            "context_refs": list(payload.get("context_refs") or []),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        })
        self._interactions.put(act_id, act)
        return act

    def get_interaction(self, interaction_id: str) -> Optional[DictRecord]:
        data = self._interactions.get(interaction_id)
        return DictRecord(data) if data else None


__all__ = ["AgoraStore", "DictRecord"]
