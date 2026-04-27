from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from .models import (
    ConsultAuditEvent,
    ConsultEvidenceAttachment,
    ConsultGateHandoff,
    ConsultMemo,
    ConsultParticipant,
    ConsultRequest,
    ConsultTranscript,
    MemoStatus,
    TranscriptEvent,
)


def _model_to_data(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


def _model_copy(model: Any) -> Any:
    if hasattr(model, "model_copy"):
        return model.model_copy(deep=True)
    return model.copy(deep=True)


def _model_from_data(model_cls: Type[Any], data: Dict[str, Any]) -> Any:
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(data)
    return model_cls(**data)


def _model_json(model: Any) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return model.json()


class ConsultationStore:
    """JSON-backed store for consultation lifecycle objects."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.requests_path = self.data_dir / "consult_requests.json"
        self.memos_path = self.data_dir / "consult_memos.json"
        self.participants_path = self.data_dir / "consult_participants.json"
        self.transcripts_path = self.data_dir / "consult_transcripts.json"
        self.evidence_path = self.data_dir / "consult_evidence_attachments.json"
        self.handoffs_path = self.data_dir / "consult_gate_handoffs.json"
        self.audit_log_path = self.data_dir / "consult_audit.jsonl"
        self.memo_publications_path = self.data_dir / "consult_memo_publications.jsonl"

        self._requests: Dict[str, ConsultRequest] = self._load(self.requests_path, ConsultRequest)
        self._memos: Dict[str, ConsultMemo] = self._load(self.memos_path, ConsultMemo)
        self._participants: Dict[str, ConsultParticipant] = self._load(
            self.participants_path, ConsultParticipant
        )
        self._transcripts: Dict[str, ConsultTranscript] = self._load(
            self.transcripts_path, ConsultTranscript
        )
        self._evidence: Dict[str, ConsultEvidenceAttachment] = self._load(
            self.evidence_path, ConsultEvidenceAttachment
        )
        self._handoffs: Dict[str, ConsultGateHandoff] = self._load(
            self.handoffs_path, ConsultGateHandoff
        )

    def _load(self, path: Path, model_cls: Type[Any]) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text())
            return {key: _model_from_data(model_cls, value) for key, value in raw.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    def _save(self, path: Path, data: Dict[str, Any]) -> None:
        serialized = {key: _model_to_data(value) for key, value in data.items()}
        path.write_text(json.dumps(serialized, indent=2, sort_keys=True))

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    # --- Requests ---

    def put_request(self, request: ConsultRequest) -> None:
        self._requests[request.request_id] = _model_copy(request)
        self._save(self.requests_path, self._requests)

    def get_request(self, request_id: str) -> Optional[ConsultRequest]:
        request = self._requests.get(request_id)
        return _model_copy(request) if request else None

    def list_requests(self) -> List[ConsultRequest]:
        return [_model_copy(request) for request in self._requests.values()]

    # --- Memos ---

    def put_memo(self, memo: ConsultMemo) -> None:
        existing = self._memos.get(memo.memo_id)
        if existing and existing.status == MemoStatus.PUBLISHED:
            if _model_to_data(existing) != _model_to_data(memo):
                raise ValueError("Published consultation memos are immutable")
            return

        saved_memo = _model_copy(memo)
        self._memos[memo.memo_id] = saved_memo
        self._save(self.memos_path, self._memos)

        if saved_memo.status == MemoStatus.PUBLISHED:
            self._append_jsonl(
                self.memo_publications_path,
                {
                    "memo_id": saved_memo.memo_id,
                    "request_id": saved_memo.request_id,
                    "published_at": saved_memo.published_at,
                    "payload": _model_to_data(saved_memo),
                },
            )

    def get_memo(self, memo_id: str) -> Optional[ConsultMemo]:
        memo = self._memos.get(memo_id)
        return _model_copy(memo) if memo else None

    def list_memos(self) -> List[ConsultMemo]:
        return [_model_copy(memo) for memo in self._memos.values()]

    def list_memos_for_request(self, request_id: str) -> List[ConsultMemo]:
        return [
            _model_copy(memo)
            for memo in self._memos.values()
            if memo.request_id == request_id
        ]

    def list_memos_for_target(self, target_type: str, target_id: str) -> List[ConsultMemo]:
        return [
            _model_copy(memo)
            for memo in self._memos.values()
            if memo.target_type == target_type and memo.target_id == target_id
        ]

    # --- Participants ---

    def put_participant(self, participant: ConsultParticipant) -> None:
        self._participants[participant.participant_id] = _model_copy(participant)
        self._save(self.participants_path, self._participants)

    def list_participants_for_request(self, request_id: str) -> List[ConsultParticipant]:
        return [
            _model_copy(participant)
            for participant in self._participants.values()
            if participant.request_id == request_id
        ]

    # --- Transcripts ---

    def get_transcript(self, request_id: str) -> Optional[ConsultTranscript]:
        transcript = self._transcripts.get(request_id)
        return _model_copy(transcript) if transcript else None

    def put_transcript(self, transcript: ConsultTranscript) -> None:
        self._transcripts[transcript.request_id] = _model_copy(transcript)
        self._save(self.transcripts_path, self._transcripts)

    def add_transcript_event(self, request_id: str, event: TranscriptEvent) -> None:
        transcript = self.get_transcript(request_id)
        if not transcript:
            transcript = ConsultTranscript(
                transcript_id=f"tr-{request_id}",
                session_id=request_id,
                request_id=request_id,
                events=[],
            )
        transcript.events.append(event)
        self.put_transcript(transcript)

    # --- Evidence ---

    def put_evidence_attachment(self, attachment: ConsultEvidenceAttachment) -> None:
        if attachment.attachment_id in self._evidence:
            raise ValueError("Consultation evidence attachments are append-only")
        self._evidence[attachment.attachment_id] = _model_copy(attachment)
        self._save(self.evidence_path, self._evidence)

    def list_evidence_for_request(self, request_id: str) -> List[ConsultEvidenceAttachment]:
        return [
            _model_copy(attachment)
            for attachment in self._evidence.values()
            if attachment.request_id == request_id
        ]

    # --- Handoffs ---

    def put_handoff(self, handoff: ConsultGateHandoff) -> None:
        self._handoffs[handoff.handoff_id] = _model_copy(handoff)
        self._save(self.handoffs_path, self._handoffs)

    def get_handoff(self, handoff_id: str) -> Optional[ConsultGateHandoff]:
        handoff = self._handoffs.get(handoff_id)
        return _model_copy(handoff) if handoff else None

    def list_handoffs_for_request(self, request_id: str) -> List[ConsultGateHandoff]:
        return [
            _model_copy(handoff)
            for handoff in self._handoffs.values()
            if handoff.request_id == request_id
        ]

    # --- Audit ---

    def append_audit(self, event: ConsultAuditEvent) -> None:
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(_model_json(event) + "\n")

    def list_audit_for_request(self, request_id: str) -> List[ConsultAuditEvent]:
        if not self.audit_log_path.exists():
            return []
        events: List[ConsultAuditEvent] = []
        with self.audit_log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                event = _model_from_data(ConsultAuditEvent, json.loads(line))
                if event.request_id == request_id:
                    events.append(event)
        return events
