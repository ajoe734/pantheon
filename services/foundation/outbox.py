"""Shared outbox and event-delivery primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .envelopes import TraceContext
from .exceptions import FoundationValidationError
from .serialization import drop_none, ensure_utc, foundation_id, sha256_checksum
from .types import ActorRef, ActorType, EnvironmentName, EnvironmentScope

EVENT_ENVELOPE_SCHEMA_VERSION = "event_envelope.v1"
OUTBOX_RECORD_SCHEMA_VERSION = "outbox_record.v1"
INBOX_RECEIPT_SCHEMA_VERSION = "inbox_receipt.v1"


class OutboxRecordStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class InboxReceiptStatus(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    REJECTED = "rejected"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    sequence_no: int
    trace: TraceContext
    payload: Mapping[str, Any]
    idempotency_key: str
    event_time: datetime | str = field(default_factory=lambda: ensure_utc(None))
    emitted_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    causal_parent_id: str | None = None
    producer_service: str = "pantheon"
    schema_ref: str | None = None
    schema_version: str = EVENT_ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "idempotency_key",
            "producer_service",
            "schema_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"event.{field_name} is required")
        if int(self.sequence_no) < 1:
            raise FoundationValidationError("event.sequence_no must be >= 1")
        if not isinstance(self.trace, TraceContext):
            raise FoundationValidationError("event.trace must be a TraceContext")
        if not isinstance(self.payload, Mapping):
            raise FoundationValidationError("event.payload must be a mapping")
        object.__setattr__(self, "sequence_no", int(self.sequence_no))
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "event_time", ensure_utc(self.event_time))
        object.__setattr__(self, "emitted_at", ensure_utc(self.emitted_at))

    @classmethod
    def new(
        cls,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        sequence_no: int,
        trace: TraceContext,
        payload: Mapping[str, Any],
        causal_parent_id: str | None = None,
        producer_service: str = "pantheon",
        schema_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> "EventEnvelope":
        effective_key = idempotency_key or "idmp-" + sha256_checksum(
            {
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "sequence_no": sequence_no,
                "payload": payload,
            }
        )[:32]
        return cls(
            event_id=foundation_id("evt"),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence_no=sequence_no,
            trace=trace,
            payload=payload,
            causal_parent_id=causal_parent_id,
            producer_service=producer_service,
            schema_ref=schema_ref,
            idempotency_key=effective_key,
        )

    @property
    def trace_id(self) -> str:
        return self.trace.trace_id

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "event_id": self.event_id,
                "event_type": self.event_type,
                "aggregate_type": self.aggregate_type,
                "aggregate_id": self.aggregate_id,
                "sequence_no": self.sequence_no,
                "causal_parent_id": self.causal_parent_id,
                "event_time": ensure_utc(self.event_time).isoformat().replace("+00:00", "Z"),
                "emitted_at": ensure_utc(self.emitted_at).isoformat().replace("+00:00", "Z"),
                "trace_id": self.trace.trace_id,
                "correlation_id": self.trace.correlation_id,
                "trace": self.trace.to_dict(),
                "idempotency_key": self.idempotency_key,
                "producer_service": self.producer_service,
                "schema_ref": self.schema_ref,
                "payload": dict(self.payload),
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventEnvelope":
        return cls(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            aggregate_type=str(data["aggregate_type"]),
            aggregate_id=str(data["aggregate_id"]),
            sequence_no=int(data["sequence_no"]),
            trace=_trace_from_dict(data["trace"]),
            payload=dict(data.get("payload", {})),
            idempotency_key=str(data["idempotency_key"]),
            event_time=str(data["event_time"]),
            emitted_at=str(data["emitted_at"]),
            causal_parent_id=data.get("causal_parent_id"),
            producer_service=str(data.get("producer_service", "pantheon")),
            schema_ref=data.get("schema_ref"),
            schema_version=str(data.get("schema_version", EVENT_ENVELOPE_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: str
    owner_service: str
    event: EventEnvelope
    status: OutboxRecordStatus | str = OutboxRecordStatus.PENDING
    created_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    updated_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    delivery_attempts: int = 0
    published_at: datetime | str | None = None
    last_error: str | None = None
    schema_version: str = OUTBOX_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("outbox_id", "owner_service", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"outbox.{field_name} is required")
        if not isinstance(self.event, EventEnvelope):
            raise FoundationValidationError("outbox.event must be an EventEnvelope")
        try:
            status = self.status if isinstance(self.status, OutboxRecordStatus) else OutboxRecordStatus(str(self.status))
        except ValueError as exc:
            allowed = ", ".join(item.value for item in OutboxRecordStatus)
            raise FoundationValidationError(f"outbox.status must be one of: {allowed}") from exc
        if int(self.delivery_attempts) < 0:
            raise FoundationValidationError("outbox.delivery_attempts must be >= 0")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "delivery_attempts", int(self.delivery_attempts))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))
        if self.published_at is not None:
            object.__setattr__(self, "published_at", ensure_utc(self.published_at))

    @classmethod
    def new(cls, *, owner_service: str, event: EventEnvelope) -> "OutboxRecord":
        return cls(outbox_id=foundation_id("outbox"), owner_service=owner_service, event=event)

    def mark_published(self) -> "OutboxRecord":
        now = ensure_utc(None)
        return OutboxRecord(
            outbox_id=self.outbox_id,
            owner_service=self.owner_service,
            event=self.event,
            status=OutboxRecordStatus.PUBLISHED,
            created_at=self.created_at,
            updated_at=now,
            delivery_attempts=self.delivery_attempts + 1,
            published_at=now,
        )

    def mark_failed(self, error: str, *, dead_lettered: bool = False) -> "OutboxRecord":
        if not str(error).strip():
            raise FoundationValidationError("outbox failure error is required")
        return OutboxRecord(
            outbox_id=self.outbox_id,
            owner_service=self.owner_service,
            event=self.event,
            status=OutboxRecordStatus.DEAD_LETTERED if dead_lettered else OutboxRecordStatus.FAILED,
            created_at=self.created_at,
            updated_at=ensure_utc(None),
            delivery_attempts=self.delivery_attempts + 1,
            published_at=self.published_at,
            last_error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "outbox_id": self.outbox_id,
                "owner_service": self.owner_service,
                "event": self.event.to_dict(),
                "status": self.status.value,
                "created_at": ensure_utc(self.created_at).isoformat().replace("+00:00", "Z"),
                "updated_at": ensure_utc(self.updated_at).isoformat().replace("+00:00", "Z"),
                "delivery_attempts": self.delivery_attempts,
                "published_at": ensure_utc(self.published_at).isoformat().replace("+00:00", "Z")
                if self.published_at
                else None,
                "last_error": self.last_error,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutboxRecord":
        return cls(
            outbox_id=str(data["outbox_id"]),
            owner_service=str(data["owner_service"]),
            event=EventEnvelope.from_dict(data["event"]),
            status=str(data.get("status", OutboxRecordStatus.PENDING.value)),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            delivery_attempts=int(data.get("delivery_attempts", 0)),
            published_at=data.get("published_at"),
            last_error=data.get("last_error"),
            schema_version=str(data.get("schema_version", OUTBOX_RECORD_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class InboxReceipt:
    consumer_name: str
    event_id: str
    idempotency_key: str
    aggregate_type: str
    aggregate_id: str
    sequence_no: int
    trace_id: str
    status: InboxReceiptStatus | str
    processed_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    audit_action_ref: str | None = None
    notes: str | None = None
    schema_version: str = INBOX_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "consumer_name",
            "event_id",
            "idempotency_key",
            "aggregate_type",
            "aggregate_id",
            "trace_id",
            "schema_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"inbox.{field_name} is required")
        try:
            status = self.status if isinstance(self.status, InboxReceiptStatus) else InboxReceiptStatus(str(self.status))
        except ValueError as exc:
            allowed = ", ".join(item.value for item in InboxReceiptStatus)
            raise FoundationValidationError(f"inbox.status must be one of: {allowed}") from exc
        if int(self.sequence_no) < 1:
            raise FoundationValidationError("inbox.sequence_no must be >= 1")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "sequence_no", int(self.sequence_no))
        object.__setattr__(self, "processed_at", ensure_utc(self.processed_at))

    @classmethod
    def record(
        cls,
        *,
        consumer_name: str,
        event: EventEnvelope,
        status: InboxReceiptStatus | str,
        audit_action_ref: str | None = None,
        notes: str | None = None,
    ) -> "InboxReceipt":
        return cls(
            consumer_name=consumer_name,
            event_id=event.event_id,
            idempotency_key=event.idempotency_key,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            sequence_no=event.sequence_no,
            trace_id=event.trace_id,
            status=status,
            audit_action_ref=audit_action_ref,
            notes=notes,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "consumer_name": self.consumer_name,
                "event_id": self.event_id,
                "idempotency_key": self.idempotency_key,
                "aggregate_type": self.aggregate_type,
                "aggregate_id": self.aggregate_id,
                "sequence_no": self.sequence_no,
                "trace_id": self.trace_id,
                "status": self.status.value,
                "processed_at": ensure_utc(self.processed_at).isoformat().replace("+00:00", "Z"),
                "audit_action_ref": self.audit_action_ref,
                "notes": self.notes,
            }
        )


class JsonlOutboxStore:
    """Append-only JSONL store for shared outbox records."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: OutboxRecord) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")

    def load(self) -> list[OutboxRecord]:
        if not self.path.exists():
            return []
        records: list[OutboxRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(OutboxRecord.from_dict(json.loads(line)))
        return records


def _trace_from_dict(data: Mapping[str, Any]) -> TraceContext:
    actor_ref = data.get("actor_ref")
    return TraceContext(
        trace_id=str(data["trace_id"]),
        correlation_id=str(data["correlation_id"]),
        causation_id=data.get("causation_id"),
        parent_span_id=data.get("parent_span_id"),
        request_id=data.get("request_id"),
        idempotency_key=data.get("idempotency_key"),
        actor_ref=_actor_from_dict(actor_ref) if isinstance(actor_ref, Mapping) else None,
        environment=_environment_from_dict(data["environment"]),
        source_system=str(data.get("source_system", "pantheon")),
        created_at=str(data["created_at"]),
        schema_version=str(data.get("schema_version", "trace_context.v1")),
    )


def _environment_from_dict(data: Mapping[str, Any]) -> EnvironmentScope:
    return EnvironmentScope(
        name=EnvironmentName(str(data["name"])),
        region=data.get("region"),
        market=data.get("market"),
        timezone=str(data.get("timezone", "UTC")),
    )


def _actor_from_dict(data: Mapping[str, Any]) -> ActorRef:
    return ActorRef(
        actor_type=ActorType(str(data["actor_type"])),
        actor_id=str(data["actor_id"]),
        display_name=data.get("display_name"),
        roles=tuple(data.get("roles", ())),
        workspace_id=data.get("workspace_id"),
        persona_id=data.get("persona_id"),
        session_id=data.get("session_id"),
    )
