from __future__ import annotations

import json
import os
import re
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
_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_pg(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


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

    def get_participant(self, participant_id: str) -> Optional[ConsultParticipant]:
        participant = self._participants.get(participant_id)
        return _model_copy(participant) if participant else None

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

    def list_transcripts(self) -> List[ConsultTranscript]:
        return [_model_copy(transcript) for transcript in self._transcripts.values()]

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

    def get_evidence_attachment(
        self, attachment_id: str
    ) -> Optional[ConsultEvidenceAttachment]:
        attachment = self._evidence.get(attachment_id)
        return _model_copy(attachment) if attachment else None

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

    def list_handoffs(self) -> List[ConsultGateHandoff]:
        return [_model_copy(handoff) for handoff in self._handoffs.values()]

    def list_handoffs_for_request(self, request_id: str) -> List[ConsultGateHandoff]:
        return [
            _model_copy(handoff)
            for handoff in self._handoffs.values()
            if handoff.request_id == request_id
        ]

    def list_handoffs_for_target(self, target_gate: str) -> List[ConsultGateHandoff]:
        return [
            _model_copy(handoff)
            for handoff in self._handoffs.values()
            if handoff.target_gate == target_gate
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


class _PostgresOutboxStore:
    def __init__(self, store: "PostgresConsultationStore") -> None:
        self._store = store

    def append(self, record: OutboxRecord) -> None:
        self._store._insert_json_payload(
            self._store.outbox_table,
            (
                "outbox_id",
                "owner_service",
                "payload",
            ),
            (
                record.outbox_id,
                record.owner_service,
                record.to_dict(),
            ),
        )

    def load(self) -> List[OutboxRecord]:
        rows = self._store._select_payloads(self._store.outbox_table, "append_id")
        return [OutboxRecord.from_dict(payload) for payload in rows]


class PostgresConsultationStore(ConsultationStore):
    """Postgres-backed consultation store pilot.

    Write owner: consultation-svc. JSONL remains the default store; this
    implementation is activated only by explicit env through
    build_consultation_store().
    """

    def __init__(
        self,
        dsn: str,
        *,
        lifecycle_table: str = "consult_svc.lifecycle_events",
        audit_table: str = "consult_svc.audit_events",
        memo_publications_table: str = "consult_svc.memo_publications",
        outbox_table: str = "consult_svc.outbox_records",
        bootstrap: bool = True,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresConsultationStore")
        self.dsn = dsn
        self.lifecycle_table = _quote_pg(lifecycle_table)
        self.audit_table = _quote_pg(audit_table)
        self.memo_publications_table = _quote_pg(memo_publications_table)
        self.outbox_table = _quote_pg(outbox_table)
        self._tables = [
            lifecycle_table,
            audit_table,
            memo_publications_table,
            outbox_table,
        ]
        self.data_dir = Path(f"postgres:{lifecycle_table}")
        self.outbox = _PostgresOutboxStore(self)
        self._requests: Dict[str, ConsultRequest] = {}
        self._memos: Dict[str, ConsultMemo] = {}
        self._participants: Dict[str, ConsultParticipant] = {}
        self._transcripts: Dict[str, ConsultTranscript] = {}
        self._evidence: Dict[str, ConsultEvidenceAttachment] = {}
        self._handoffs: Dict[str, ConsultGateHandoff] = {}
        self._next_sequence_no = 1
        if bootstrap:
            self._bootstrap()
        self.reload()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required when CONSULTATION_STORE_BACKEND=postgres"
            ) from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        schemas = {table.split(".", 1)[0] for table in self._tables if "." in table}
        with self._connect() as conn:
            for schema in sorted(schemas):
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg(schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.lifecycle_table} (
                    append_id        BIGSERIAL PRIMARY KEY,
                    event_id         TEXT NOT NULL UNIQUE,
                    sequence_no      BIGINT NOT NULL,
                    record_type      TEXT NOT NULL,
                    record_id        TEXT NOT NULL,
                    trace_id         TEXT NOT NULL,
                    payload          JSONB NOT NULL,
                    event_json       JSONB NOT NULL,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.audit_table} (
                    append_id   BIGSERIAL PRIMARY KEY,
                    audit_id    TEXT NOT NULL UNIQUE,
                    request_id  TEXT NOT NULL,
                    payload     JSONB NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.memo_publications_table} (
                    append_id   BIGSERIAL PRIMARY KEY,
                    memo_id     TEXT NOT NULL,
                    request_id  TEXT NOT NULL,
                    payload     JSONB NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.outbox_table} (
                    append_id       BIGSERIAL PRIMARY KEY,
                    outbox_id       TEXT NOT NULL UNIQUE,
                    owner_service   TEXT NOT NULL,
                    payload         JSONB NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def _insert_json_payload(self, table: str, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
        placeholders = []
        params = []
        for value in values:
            if isinstance(value, dict):
                placeholders.append("%s::jsonb")
                params.append(json.dumps(value, ensure_ascii=True, sort_keys=True))
            else:
                placeholders.append("%s")
                params.append(value)
        column_sql = ", ".join(_quote_pg(column) for column in columns)
        placeholder_sql = ", ".join(placeholders)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholder_sql})",
                tuple(params),
            )

    def _select_payloads(self, table: str, order_column: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT payload FROM {table} ORDER BY {_quote_pg(order_column)} ASC"
            )
            rows = cursor.fetchall()
        payloads: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def reload(self) -> None:
        self._requests.clear()
        self._memos.clear()
        self._participants.clear()
        self._transcripts.clear()
        self._evidence.clear()
        self._handoffs.clear()
        self._next_sequence_no = 1
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT event_json FROM {self.lifecycle_table} ORDER BY sequence_no ASC, append_id ASC"
            )
            rows = cursor.fetchall()
        for row in rows:
            event = row[0] if isinstance(row, tuple) else row.get("event_json")
            if isinstance(event, str):
                event = json.loads(event)
            if isinstance(event, dict):
                self._next_sequence_no = max(
                    self._next_sequence_no,
                    int(event.get("sequence_no", 0)) + 1,
                )
                self._apply_lifecycle_event(event)

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
        self._insert_json_payload(
            self.lifecycle_table,
            (
                "event_id",
                "sequence_no",
                "record_type",
                "record_id",
                "trace_id",
                "payload",
                "event_json",
            ),
            (
                event["event_id"],
                event["sequence_no"],
                event["record_type"],
                event["record_id"],
                event["trace_id"],
                event["payload"],
                event,
            ),
        )
        self._append_outbox_event(event)
        self._next_sequence_no += 1

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
            self._insert_json_payload(
                self.memo_publications_table,
                (
                    "memo_id",
                    "request_id",
                    "payload",
                ),
                (
                    saved_memo.memo_id,
                    saved_memo.request_id,
                    {
                        "memo_id": saved_memo.memo_id,
                        "request_id": saved_memo.request_id,
                        "published_at": saved_memo.published_at,
                        "payload": _model_to_data(saved_memo),
                    },
                ),
            )

    def append_audit(self, event: ConsultAuditEvent) -> None:
        self._insert_json_payload(
            self.audit_table,
            (
                "audit_id",
                "request_id",
                "payload",
            ),
            (
                event.audit_id,
                event.request_id,
                _model_to_data(event),
            ),
        )

    def list_audit_for_request(self, request_id: str) -> List[ConsultAuditEvent]:
        events: List[ConsultAuditEvent] = []
        for payload in self._select_payloads(self.audit_table, "append_id"):
            event = _model_from_data(ConsultAuditEvent, payload)
            if event.request_id == request_id:
                events.append(event)
        return events


def build_consultation_store(data_dir: str) -> ConsultationStore:
    """Factory: returns JSONL ConsultationStore unless CONSULTATION_STORE_BACKEND=postgres."""
    backend = os.getenv("CONSULTATION_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return ConsultationStore(data_dir)
    if backend != "postgres":
        raise ValueError("CONSULTATION_STORE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("CONSULTATION_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "CONSULTATION_STORE_DSN or DATABASE_URL is required for Postgres consultation store"
        )
    bootstrap = (
        os.getenv("CONSULTATION_STORE_BOOTSTRAP", "1").strip().lower()
        not in ("0", "false", "no")
    )
    return PostgresConsultationStore(
        dsn=dsn,
        lifecycle_table=os.getenv(
            "CONSULTATION_LIFECYCLE_TABLE",
            "consult_svc.lifecycle_events",
        ),
        audit_table=os.getenv("CONSULTATION_AUDIT_TABLE", "consult_svc.audit_events"),
        memo_publications_table=os.getenv(
            "CONSULTATION_MEMO_PUBLICATIONS_TABLE",
            "consult_svc.memo_publications",
        ),
        outbox_table=os.getenv("CONSULTATION_OUTBOX_TABLE", "consult_svc.outbox_records"),
        bootstrap=bootstrap,
    )
