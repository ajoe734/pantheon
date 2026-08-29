"""
Independent persistent Agora write-owner storage over Pantheon PostgresJsonOwnerStore.

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
import hashlib
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
        self.schema = schema.strip() or "agora"

        self._sessions = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.sessions", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._memos = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.memos", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._evidence_packs = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.evidence_packs", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._notes = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.notes", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._insights = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.insights", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._training_examples = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.training_examples", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._signals = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.signals", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._signal_feedback = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.signal_feedback", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._feedback = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.feedback", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._handoffs = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.handoffs", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._audit_events = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.audit_events", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._journal = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.journal_entries", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._journal_audit = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.journal_audit", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._journal_idempotency = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.journal_idempotency", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._workshops = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.workshops", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._proposals = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.proposals", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._interactions = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.interactions", owner_service="agora-svc", bootstrap=bootstrap
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
        now = created_at or _utc_now_rfc3339()
        mode = str(payload.get("mode") or "general").strip() or "general"
        participants = list(payload.get("participants") or [])
        if not any(p.get("id") == actor_id for p in participants if isinstance(p, dict)):
            participants.insert(0, {"id": actor_id, "role": "operator", "joinedAt": now})

        target_entity = dict(payload.get("targetEntity") or payload.get("target_entity") or {})
        session_record = DictRecord({
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": mode,
            "status": "active",
            "outcome": None,
            "targetEntity": target_entity,
            "contextRefs": list(payload.get("contextRefs") or payload.get("context_refs") or []),
            "participants": participants,
            "messages": list(payload.get("messages") or []),
            "createdAt": now,
            "updatedAt": now,
            "closedAt": None,
            "canonicalWriteAuthority": "agora_committee_service",
            "persistenceMode": "owner_store",
        })
        self._sessions.put(session_id, session_record)
        return session_record

    def get_session(self, session_id: str) -> Optional[DictRecord]:
        data = self._sessions.get(session_id)
        return DictRecord(data) if data else None

    def list_sessions(self, mode: Optional[str] = None, status: Optional[str] = None) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._sessions.list_all()]
        if mode:
            rows = [r for r in rows if r.get("mode") == mode]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return rows

    def open_committee_session(
        self,
        session_id: str,
        opened_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
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
        if not session:
            return None
        now = closed_at or _utc_now_rfc3339()
        session["status"] = "closed"
        session["closedAt"] = now
        session["updatedAt"] = now
        if outcome is not None:
            session["outcome"] = outcome
        if memo_ids is not None:
            session["memoIds"] = memo_ids
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
        if not session:
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
        now = uploaded_at or _utc_now_rfc3339()
        pack = self.get_evidence_pack(session_id)
        if not pack:
            pack = self.create_evidence_pack(
                session_id=session_id,
                payload={},
                actor_id=actor_id,
                created_at=now,
            )

        uploaded = list(pack.get("uploadedFiles", []))
        for f in files:
            file_id = str(f.get("fileId") or f.get("id") or f"f-{uuid.uuid4().hex[:8]}")
            name = str(f.get("filename") or f.get("name") or "unnamed")
            size = int(f.get("sizeBytes") or f.get("size") or 0)
            mime = str(f.get("mimeType") or f.get("mime") or "application/octet-stream")
            s_ref = str(f.get("storageRef") or f.get("ref") or f"storage://evidence/{session_id}/{file_id}")
            c_hash = str(f.get("contentHash") or f.get("hash") or "")
            uploaded.append({
                "fileId": file_id,
                "filename": name,
                "sizeBytes": size,
                "mimeType": mime,
                "storageRef": s_ref,
                "contentHash": c_hash,
                "uploadedBy": actor_id,
                "uploadedAt": now,
            })
        pack["uploadedFiles"] = uploaded
        pack["updatedAt"] = now
        self._evidence_packs.put(session_id, pack)
        return pack

    # -------------------------------------------------------------------------
    # Notes & Insights
    # -------------------------------------------------------------------------
    def create_note(
        self,
        note_id: str,
        title: str,
        content: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        note = DictRecord({
            "id": note_id,
            "noteId": note_id,
            "title": title,
            "content": content,
            "author": actor_id,
            "tags": list(p.get("tags") or []),
            "category": str(p.get("category") or "general"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_notes_service",
            "persistenceMode": "owner_store",
        })
        self._notes.put(note_id, note)
        return note

    def get_note(self, note_id: str) -> Optional[DictRecord]:
        data = self._notes.get(note_id)
        return DictRecord(data) if data else None

    def list_notes(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._notes.list_all()]
        rows.sort(key=lambda n: n.get("createdAt", ""), reverse=True)
        return rows

    def create_insight(
        self,
        insight_id: str,
        title: str,
        content: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        insight = DictRecord({
            "id": insight_id,
            "insightId": insight_id,
            "title": title,
            "content": content,
            "author": actor_id,
            "category": str(p.get("category") or "market"),
            "confidence": float(p.get("confidence") or 0.8),
            "impact": str(p.get("impact") or "medium"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_insights_service",
            "persistenceMode": "owner_store",
        })
        self._insights.put(insight_id, insight)
        return insight

    def get_insight(self, insight_id: str) -> Optional[DictRecord]:
        data = self._insights.get(insight_id)
        return DictRecord(data) if data else None

    def list_insights(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._insights.list_all()]
        rows.sort(key=lambda i: i.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Training Examples
    # -------------------------------------------------------------------------
    def create_training_example(
        self,
        example_id: str,
        topic: str,
        input_data: Dict[str, Any],
        expected_output: Dict[str, Any],
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        example = DictRecord({
            "id": example_id,
            "exampleId": example_id,
            "topic": topic,
            "inputData": dict(input_data),
            "expectedOutput": dict(expected_output),
            "submittedBy": actor_id,
            "status": str(p.get("status") or "curated"),
            "targetPersonaId": p.get("targetPersonaId") or p.get("target_persona_id"),
            "tags": list(p.get("tags") or []),
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_training_example_service",
            "persistenceMode": "owner_store",
        })
        self._training_examples.put(example_id, example)
        return example

    def get_training_example(self, example_id: str) -> Optional[DictRecord]:
        data = self._training_examples.get(example_id)
        return DictRecord(data) if data else None

    def list_training_examples(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._training_examples.list_all()]
        rows.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Signals & Feedback
    # -------------------------------------------------------------------------
    def create_signal(
        self,
        signal_id: str,
        symbol: str,
        action: str,
        confidence: float,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        signal = DictRecord({
            "id": signal_id,
            "signalId": signal_id,
            "symbol": symbol,
            "action": action,
            "confidence": float(confidence),
            "source": actor_id,
            "reviewStatus": str(p.get("reviewStatus") or p.get("review_status") or "pending"),
            "horizon": str(p.get("horizon") or "short"),
            "rationale": str(p.get("rationale") or ""),
            "metadata": dict(p.get("metadata") or {}),
            "feedback": None,
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_signal_service",
            "persistenceMode": "owner_store",
        })
        self._signals.put(signal_id, signal)
        return signal

    def get_signal(self, signal_id: str) -> Optional[DictRecord]:
        data = self._signals.get(signal_id)
        return DictRecord(data) if data else None

    def list_signals(self, review_status: Optional[str] = None) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._signals.list_all()]
        if review_status:
            rows = [r for r in rows if r.get("reviewStatus") == review_status or r.get("review_status") == review_status]
        rows.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return rows

    def record_signal_feedback(
        self,
        signal_id: str,
        rating: int,
        comments: str,
        actor_id: str,
        recorded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        signal = self.get_signal(signal_id)
        if not signal:
            return None
        now = recorded_at or _utc_now_rfc3339()
        feedback_id = f"fb-sig-{uuid.uuid4().hex[:8]}"
        fb = DictRecord({
            "id": feedback_id,
            "signalId": signal_id,
            "rating": rating,
            "comments": comments,
            "reviewer": actor_id,
            "recordedAt": now,
            "canonicalWriteAuthority": "agora_signal_feedback_service",
            "persistenceMode": "owner_store",
        })
        self._signal_feedback.put(feedback_id, fb)
        signal["feedback"] = dict(fb)
        signal["updatedAt"] = now
        self._signals.put(signal_id, signal)
        return fb

    def create_feedback(
        self,
        feedback_id: str,
        target_id: str,
        content: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        fb = DictRecord({
            "id": feedback_id,
            "feedbackId": feedback_id,
            "targetId": target_id,
            "targetType": str(p.get("targetType") or p.get("target_type") or "item"),
            "content": content,
            "author": actor_id,
            "score": p.get("score"),
            "createdAt": now,
            "canonicalWriteAuthority": "agora_feedback_service",
            "persistenceMode": "owner_store",
        })
        self._feedback.put(feedback_id, fb)
        return fb

    def get_feedback(self, feedback_id: str) -> Optional[DictRecord]:
        data = self._feedback.get(feedback_id)
        return DictRecord(data) if data else None

    def list_feedback(self, target_id: Optional[str] = None) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._feedback.list_all()]
        if target_id:
            rows = [r for r in rows if r.get("targetId") == target_id or r.get("target_id") == target_id]
        rows.sort(key=lambda f: f.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Handoffs
    # -------------------------------------------------------------------------
    def create_handoff(
        self,
        handoff_id: str,
        source_lane: str,
        target_lane: str,
        summary: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        handoff = DictRecord({
            "id": handoff_id,
            "handoffId": handoff_id,
            "sourceLane": source_lane,
            "targetLane": target_lane,
            "summary": summary,
            "status": str(p.get("status") or "pending"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "artifacts": list(p.get("artifacts") or []),
            "createdBy": actor_id,
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

    def list_handoffs(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._handoffs.list_all()]
        rows.sort(key=lambda h: h.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Audit Events
    # -------------------------------------------------------------------------
    def record_audit_event(
        self,
        event_type: str,
        actor_id: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> DictRecord:
        now = recorded_at or _utc_now_rfc3339()
        ev_id = event_id or f"aud-agora-{uuid.uuid4().hex[:12]}"
        p = payload or {}
        ev = DictRecord({
            "id": ev_id,
            "eventId": ev_id,
            "eventType": event_type,
            "actorId": actor_id,
            "targetId": target_id,
            "details": dict(p),
            "recordedAt": now,
            "canonicalWriteAuthority": "agora_audit_service",
            "persistenceMode": "owner_store",
        })
        self._audit_events.put(ev_id, ev)
        return ev

    def get_audit_event(self, event_id: str) -> Optional[DictRecord]:
        data = self._audit_events.get(event_id)
        return DictRecord(data) if data else None

    def list_audit_events(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._audit_events.list_all()]
        rows.sort(key=lambda a: a.get("recordedAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Decision Journal
    # -------------------------------------------------------------------------
    def create_journal_entry(
        self,
        entry_id: str,
        title: str,
        decision: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        entry = DictRecord({
            "id": entry_id,
            "entryId": entry_id,
            "title": title,
            "decision": decision,
            "author": actor_id,
            "category": str(p.get("category") or "strategy"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "tags": list(p.get("tags") or []),
            "visibility": str(p.get("visibility") or "public"),
            "version": 1,
            "createdAt": now,
            "updatedAt": now,
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
        rows.sort(key=lambda j: j.get("createdAt", ""), reverse=True)
        return rows

    def patch_journal_entry(
        self,
        entry_id: str,
        patch: Dict[str, Any],
        actor_id: str,
        idempotency_key: str,
        correlation_id: Optional[str] = None,
        patched_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        request_hash = hashlib.sha256(
            json.dumps({"patch": patch, "actor_id": actor_id}, sort_keys=True).encode("utf-8")
        ).hexdigest()

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


def build_agora_store(
    *,
    dsn: Optional[str] = None,
    schema: str = "agora",
    bootstrap: bool = True,
) -> AgoraStore:
    """Factory creating an AgoraStore bound to Postgres storage."""
    selected_dsn = dsn or os.getenv("AGORA_STORE_DSN") or os.getenv("DATABASE_URL")
    if not selected_dsn:
        raise ValueError(
            "DATABASE_URL or AGORA_STORE_DSN is required for Postgres Agora write owner"
        )
    return AgoraStore(
        dsn=selected_dsn,
        schema=schema,
        bootstrap=bootstrap,
    )


__all__ = ["AgoraStore", "DictRecord", "build_agora_store"]
