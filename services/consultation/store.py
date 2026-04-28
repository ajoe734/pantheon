from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from services.foundation import EventEnvelope, JsonlOutboxStore, OutboxRecord, TraceContext
from services.foundation.types import ActorRef as FoundationActorRef
from services.foundation.types import ActorType, EnvironmentName, EnvironmentScope

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

CONSULT_LIFECYCLE_EVENT_SCHEMA_VERSION = "consult_lifecycle_event.v1"
CONSULTATION_SERVICE_ACTOR_ID = "consultation-svc"


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
    """Append/replay-backed store for consultation lifecycle objects."""

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
        self.lifecycle_log_path = self.data_dir / "consult_lifecycle_events.jsonl"
        self.outbox_path = self.data_dir / "consult_outbox.jsonl"
        self.outbox = JsonlOutboxStore(self.outbox_path)

        self._requests: Dict[str, ConsultRequest] = {}
        self._memos: Dict[str, ConsultMemo] = {}
        self._participants: Dict[str, ConsultParticipant] = {}
        self._transcripts: Dict[str, ConsultTranscript] = {}
        self._evidence: Dict[str, ConsultEvidenceAttachment] = {}
        self._handoffs: Dict[str, ConsultGateHandoff] = {}
        self._next_sequence_no = 1

        if self.lifecycle_log_path.exists():
            self._replay_lifecycle_log()
        else:
            self._load_legacy_snapshots()
            self._migrate_legacy_snapshots_to_lifecycle_log()

    def _load_snapshot(self, path: Path, model_cls: Type[Any]) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text())
            return {key: _model_from_data(model_cls, value) for key, value in raw.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _load_legacy_snapshots(self) -> None:
        self._requests = self._load_snapshot(self.requests_path, ConsultRequest)
        self._memos = self._load_snapshot(self.memos_path, ConsultMemo)
        self._participants = self._load_snapshot(self.participants_path, ConsultParticipant)
        self._transcripts = self._load_snapshot(self.transcripts_path, ConsultTranscript)
        self._evidence = self._load_snapshot(self.evidence_path, ConsultEvidenceAttachment)
        self._handoffs = self._load_snapshot(self.handoffs_path, ConsultGateHandoff)

    def _migrate_legacy_snapshots_to_lifecycle_log(self) -> None:
        for request in self._requests.values():
            self._append_lifecycle_event(
                record_type="request",
                record_id=request.request_id,
                trace_id=request.trace_id,
                payload=_model_to_data(request),
            )
        for participant in self._participants.values():
            request = self._requests.get(participant.request_id)
            self._append_lifecycle_event(
                record_type="participant",
                record_id=participant.participant_id,
                trace_id=request.trace_id if request else participant.request_id,
                payload=_model_to_data(participant),
            )
        for attachment in self._evidence.values():
            self._append_lifecycle_event(
                record_type="evidence_attachment",
                record_id=attachment.attachment_id,
                trace_id=attachment.trace_id,
                payload=_model_to_data(attachment),
            )
        for transcript in self._transcripts.values():
            request = self._requests.get(transcript.request_id)
            self._append_lifecycle_event(
                record_type="transcript",
                record_id=transcript.request_id,
                trace_id=request.trace_id if request else transcript.request_id,
                payload=_model_to_data(transcript),
            )
        for memo in self._memos.values():
            self._append_lifecycle_event(
                record_type="memo",
                record_id=memo.memo_id,
                trace_id=memo.trace_id,
                payload=_model_to_data(memo),
            )
        for handoff in self._handoffs.values():
            self._append_lifecycle_event(
                record_type="gate_handoff",
                record_id=handoff.handoff_id,
                trace_id=handoff.trace_id,
                payload=_model_to_data(handoff),
            )

    def _replay_lifecycle_log(self) -> None:
        with self.lifecycle_log_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                event = json.loads(line)
                self._next_sequence_no = max(
                    self._next_sequence_no,
                    int(event.get("sequence_no", 0)) + 1,
                )
                self._apply_lifecycle_event(event)

    def _apply_lifecycle_event(self, event: Dict[str, Any]) -> None:
        record_type = event.get("record_type")
        record_id = str(event.get("record_id", ""))
        payload = dict(event.get("payload", {}))
        if event.get("operation") != "upsert" or not record_id:
            return
        if record_type == "request":
            self._requests[record_id] = _model_from_data(ConsultRequest, payload)
        elif record_type == "memo":
            self._memos[record_id] = _model_from_data(ConsultMemo, payload)
        elif record_type == "participant":
            self._participants[record_id] = _model_from_data(ConsultParticipant, payload)
        elif record_type == "transcript":
            self._transcripts[record_id] = _model_from_data(ConsultTranscript, payload)
        elif record_type == "evidence_attachment":
            self._evidence[record_id] = _model_from_data(ConsultEvidenceAttachment, payload)
        elif record_type == "gate_handoff":
            self._handoffs[record_id] = _model_from_data(ConsultGateHandoff, payload)

    def _append_lifecycle_event(
        self,
        *,
        record_type: str,
        record_id: str,
        trace_id: str,
        payload: Dict[str, Any],
    ) -> None:
        event = {
            "schema_version": CONSULT_LIFECYCLE_EVENT_SCHEMA_VERSION,
            "event_id": f"cle-{uuid.uuid4().hex[:12]}",
            "sequence_no": self._next_sequence_no,
            "operation": "upsert",
            "record_type": record_type,
            "record_id": record_id,
            "trace_id": trace_id,
            "producer_service": CONSULTATION_SERVICE_ACTOR_ID,
            "payload": payload,
        }
        self._append_jsonl(self.lifecycle_log_path, event)
        self._append_outbox_event(event)
        self._next_sequence_no += 1

    def _append_outbox_event(self, lifecycle_event: Dict[str, Any]) -> None:
        trace_id = str(lifecycle_event["trace_id"])
        trace = TraceContext(
            trace_id=trace_id,
            correlation_id=trace_id,
            environment=EnvironmentScope(name=EnvironmentName.DEV),
            actor_ref=FoundationActorRef(
                actor_type=ActorType.SERVICE,
                actor_id=CONSULTATION_SERVICE_ACTOR_ID,
            ),
            source_system=CONSULTATION_SERVICE_ACTOR_ID,
        )
        event = EventEnvelope.new(
            event_type=f"consultation.{lifecycle_event['record_type']}.upserted",
            aggregate_type="consultation",
            aggregate_id=str(lifecycle_event["record_id"]),
            sequence_no=int(lifecycle_event["sequence_no"]),
            trace=trace,
            payload=lifecycle_event,
            producer_service=CONSULTATION_SERVICE_ACTOR_ID,
            idempotency_key=f"consult-lifecycle:{lifecycle_event['event_id']}",
        )
        self.outbox.append(OutboxRecord.new(owner_service=CONSULTATION_SERVICE_ACTOR_ID, event=event))

    # --- Requests ---

    def put_request(self, request: ConsultRequest) -> None:
        self._append_lifecycle_event(
            record_type="request",
            record_id=request.request_id,
            trace_id=request.trace_id,
            payload=_model_to_data(request),
        )
        self._requests[request.request_id] = _model_copy(request)

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
        self._append_lifecycle_event(
            record_type="memo",
            record_id=saved_memo.memo_id,
            trace_id=saved_memo.trace_id,
            payload=_model_to_data(saved_memo),
        )
        self._memos[memo.memo_id] = saved_memo

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
        request = self._requests.get(participant.request_id)
        self._append_lifecycle_event(
            record_type="participant",
            record_id=participant.participant_id,
            trace_id=request.trace_id if request else participant.request_id,
            payload=_model_to_data(participant),
        )
        self._participants[participant.participant_id] = _model_copy(participant)

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
        request = self._requests.get(transcript.request_id)
        self._append_lifecycle_event(
            record_type="transcript",
            record_id=transcript.request_id,
            trace_id=request.trace_id if request else transcript.request_id,
            payload=_model_to_data(transcript),
        )
        self._transcripts[transcript.request_id] = _model_copy(transcript)

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
        self._append_lifecycle_event(
            record_type="evidence_attachment",
            record_id=attachment.attachment_id,
            trace_id=attachment.trace_id,
            payload=_model_to_data(attachment),
        )
        self._evidence[attachment.attachment_id] = _model_copy(attachment)

    def list_evidence_for_request(self, request_id: str) -> List[ConsultEvidenceAttachment]:
        return [
            _model_copy(attachment)
            for attachment in self._evidence.values()
            if attachment.request_id == request_id
        ]

    # --- Handoffs ---

    def put_handoff(self, handoff: ConsultGateHandoff) -> None:
        self._append_lifecycle_event(
            record_type="gate_handoff",
            record_id=handoff.handoff_id,
            trace_id=handoff.trace_id,
            payload=_model_to_data(handoff),
        )
        self._handoffs[handoff.handoff_id] = _model_copy(handoff)

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
