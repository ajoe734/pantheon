"""Durable outbox/inbox storage for cross-service event delivery.

The primitives in this module deliberately keep transport policy out of domain
stores.  Producers persist a prepared event before committing the local state
transition, activate it after the transition, and reconcile prepared records on
worker startup.  Consumers persist the complete envelope with an inbox receipt
so exact retries are harmless and divergent replays fail closed.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from services.foundation.outbox import (
    EventEnvelope,
    InboxReceipt,
    InboxReceiptStatus,
    OutboxRecord,
    OutboxRecordStatus,
)
from services.foundation.postgres_json_store import PostgresJsonOwnerStore
from services.foundation.serialization import sha256_checksum

try:  # pragma: no cover - fcntl is present on the Linux runtime.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AtomicJsonRecordStore:
    """Concurrent-safe JSON object store with locked atomic replacement."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._thread_lock = threading.RLock()

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        with self._thread_lock:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                    fcntl.flock(descriptor, mode)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError(f"JSON record store must contain an object: {self.path}")
        return {
            str(record_id): dict(record)
            for record_id, record in payload.items()
            if isinstance(record, Mapping)
        }

    def _write_unlocked(self, payload: Mapping[str, Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def put(self, record_id: str, payload: dict[str, Any]) -> None:
        if not str(record_id).strip():
            raise ValueError("record_id is required")
        with self._locked(exclusive=True):
            records = self._read_unlocked()
            records[str(record_id)] = dict(payload)
            self._write_unlocked(records)

    def compare_and_set(
        self,
        record_id: str,
        expected_payload: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None]:
        """Atomically replace one record if its complete snapshot still matches."""

        if not str(record_id).strip():
            raise ValueError("record_id is required")
        with self._locked(exclusive=True):
            records = self._read_unlocked()
            current = records.get(str(record_id))
            if current != expected_payload:
                return False, dict(current) if current is not None else None
            records[str(record_id)] = dict(payload)
            self._write_unlocked(records)
            return True, dict(payload)

    def delete_if_matches(self, record_id: str, expected_payload: dict[str, Any]) -> bool:
        """Delete one record if no concurrent writer changed it."""

        if not str(record_id).strip():
            raise ValueError("record_id is required")
        with self._locked(exclusive=True):
            records = self._read_unlocked()
            if records.get(str(record_id)) != expected_payload:
                return False
            del records[str(record_id)]
            self._write_unlocked(records)
            return True

    def insert_if_absent(
        self,
        record_id: str,
        payload: dict[str, Any],
        *,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically insert or return the canonical colliding payload."""

        if not str(record_id).strip():
            raise ValueError("record_id is required")
        fields = tuple(str(field).strip() for field in unique_fields)
        if any(not field for field in fields):
            raise ValueError("unique_fields must contain non-empty field names")
        missing = [field for field in fields if field not in payload]
        if missing:
            raise ValueError(f"payload missing unique fields: {missing}")

        with self._locked(exclusive=True):
            records = self._read_unlocked()
            existing = records.get(str(record_id))
            if existing is not None:
                return False, dict(existing)
            if fields:
                for candidate in records.values():
                    if all(candidate.get(field) == payload.get(field) for field in fields):
                        return False, dict(candidate)
            records[str(record_id)] = dict(payload)
            self._write_unlocked(records)
        return True, dict(payload)

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._locked(exclusive=False):
            record = self._read_unlocked().get(str(record_id))
        return dict(record) if record is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self._locked(exclusive=False):
            records = self._read_unlocked()
        return [dict(records[key]) for key in sorted(records)]


def build_record_store(
    *,
    backend: str,
    dsn: str | None,
    table_name: str,
    json_path: str | Path,
    owner_service: str,
) -> AtomicJsonRecordStore | PostgresJsonOwnerStore:
    normalized = str(backend or "json").strip().lower() or "json"
    if normalized == "json":
        return AtomicJsonRecordStore(json_path)
    if normalized != "postgres":
        raise ValueError("delivery store backend must be json or postgres")
    if not dsn:
        raise ValueError("DSN required for postgres delivery store")
    return PostgresJsonOwnerStore(
        dsn=dsn,
        table=table_name,
        owner_service=owner_service,
        bootstrap=True,
    )


@dataclass(frozen=True)
class ReliableOutboxRecord:
    record: OutboxRecord
    delivery_ready: bool = True
    next_attempt_at: datetime | None = None
    redrive_count: int = 0
    last_redrive_actor: str | None = None
    last_redrive_note: str | None = None
    transition: Mapping[str, Any] | None = None
    claim_token: str | None = None
    claim_expires_at: datetime | None = None

    @property
    def outbox_id(self) -> str:
        return self.record.outbox_id

    @property
    def owner_service(self) -> str:
        return self.record.owner_service

    @property
    def event(self) -> EventEnvelope:
        return self.record.event

    @property
    def status(self) -> OutboxRecordStatus:
        return self.record.status  # type: ignore[return-value]

    @property
    def delivery_attempts(self) -> int:
        return self.record.delivery_attempts

    @property
    def last_error(self) -> str | None:
        return self.record.last_error

    def to_dict(self) -> dict[str, Any]:
        payload = self.record.to_dict()
        payload["delivery"] = {
            "ready": self.delivery_ready,
            "next_attempt_at": _format_time(self.next_attempt_at),
            "redrive_count": self.redrive_count,
            "last_redrive_actor": self.last_redrive_actor,
            "last_redrive_note": self.last_redrive_note,
            "transition": dict(self.transition or {}),
            "claim_token": self.claim_token,
            "claim_expires_at": _format_time(self.claim_expires_at),
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReliableOutboxRecord":
        delivery = payload.get("delivery")
        delivery = dict(delivery) if isinstance(delivery, Mapping) else {}
        return cls(
            record=OutboxRecord.from_dict(payload),
            delivery_ready=bool(delivery.get("ready", True)),
            next_attempt_at=_parse_time(delivery.get("next_attempt_at")),
            redrive_count=int(delivery.get("redrive_count", 0)),
            last_redrive_actor=delivery.get("last_redrive_actor"),
            last_redrive_note=delivery.get("last_redrive_note"),
            transition=dict(delivery.get("transition") or {}),
            claim_token=delivery.get("claim_token"),
            claim_expires_at=_parse_time(delivery.get("claim_expires_at")),
        )

    def activate(self) -> "ReliableOutboxRecord":
        return ReliableOutboxRecord(
            record=_copy_outbox(self.record, updated_at=utc_now()),
            delivery_ready=True,
            redrive_count=self.redrive_count,
            last_redrive_actor=self.last_redrive_actor,
            last_redrive_note=self.last_redrive_note,
            transition=self.transition,
        )

    def claim_for_delivery(
        self,
        *,
        token: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> "ReliableOutboxRecord":
        effective_now = now or utc_now()
        if not str(token).strip():
            raise ValueError("delivery claim token is required")
        if not self.is_due(effective_now):
            raise ValueError(f"outbox record is not due: {self.outbox_id}")
        return ReliableOutboxRecord(
            record=_copy_outbox(self.record, updated_at=effective_now),
            delivery_ready=self.delivery_ready,
            next_attempt_at=self.next_attempt_at,
            redrive_count=self.redrive_count,
            last_redrive_actor=self.last_redrive_actor,
            last_redrive_note=self.last_redrive_note,
            transition=self.transition,
            claim_token=str(token).strip(),
            claim_expires_at=effective_now
            + timedelta(seconds=max(1.0, float(lease_seconds))),
        )

    def mark_published(self) -> "ReliableOutboxRecord":
        return ReliableOutboxRecord(
            record=self.record.mark_published(),
            delivery_ready=False,
            redrive_count=self.redrive_count,
            last_redrive_actor=self.last_redrive_actor,
            last_redrive_note=self.last_redrive_note,
            transition=self.transition,
        )

    def mark_failed(
        self,
        error: str,
        *,
        max_attempts: int,
        base_delay_seconds: float,
        now: datetime | None = None,
        permanent: bool = False,
    ) -> "ReliableOutboxRecord":
        attempt = self.delivery_attempts + 1
        dead_lettered = permanent or attempt >= max(1, int(max_attempts))
        failed = self.record.mark_failed(error, dead_lettered=dead_lettered)
        effective_now = now or utc_now()
        delay = max(0.0, float(base_delay_seconds)) * (2 ** max(0, attempt - 1))
        return ReliableOutboxRecord(
            record=failed,
            delivery_ready=not dead_lettered,
            next_attempt_at=None if dead_lettered else effective_now + timedelta(seconds=delay),
            redrive_count=self.redrive_count,
            last_redrive_actor=self.last_redrive_actor,
            last_redrive_note=self.last_redrive_note,
            transition=self.transition,
        )

    def redrive(self, *, actor: str, note: str, now: datetime | None = None) -> "ReliableOutboxRecord":
        if self.status != OutboxRecordStatus.DEAD_LETTERED:
            raise ValueError("only dead-lettered outbox records may be redriven")
        if not str(actor).strip() or not str(note).strip():
            raise ValueError("redrive actor and note are required")
        effective_now = now or utc_now()
        reset = OutboxRecord(
            outbox_id=self.outbox_id,
            owner_service=self.owner_service,
            event=self.event,
            status=OutboxRecordStatus.PENDING,
            created_at=self.record.created_at,
            updated_at=effective_now,
            delivery_attempts=0,
        )
        return ReliableOutboxRecord(
            record=reset,
            delivery_ready=True,
            next_attempt_at=effective_now,
            redrive_count=self.redrive_count + 1,
            last_redrive_actor=str(actor).strip(),
            last_redrive_note=str(note).strip(),
            transition=self.transition,
        )

    def is_due(self, now: datetime | None = None) -> bool:
        effective_now = now or utc_now()
        if not self.delivery_ready:
            return False
        if self.status not in {OutboxRecordStatus.PENDING, OutboxRecordStatus.FAILED}:
            return False
        if self.claim_token:
            if self.claim_expires_at is None or self.claim_expires_at > effective_now:
                return False
        return self.next_attempt_at is None or self.next_attempt_at <= effective_now


def _copy_outbox(record: OutboxRecord, *, updated_at: datetime) -> OutboxRecord:
    return OutboxRecord(
        outbox_id=record.outbox_id,
        owner_service=record.owner_service,
        event=record.event,
        status=record.status,
        created_at=record.created_at,
        updated_at=updated_at,
        delivery_attempts=record.delivery_attempts,
        published_at=record.published_at,
        last_error=record.last_error,
    )


class ReliableOutboxStore:
    """Persistence facade for prepared, retrying, and DLQ outbox records."""

    def __init__(
        self,
        *,
        backend: str,
        dsn: str | None,
        table_name: str,
        json_path: str | Path,
        owner_service: str,
    ) -> None:
        self.impl = build_record_store(
            backend=backend,
            dsn=dsn,
            table_name=table_name,
            json_path=json_path,
            owner_service=owner_service,
        )

    def put(self, record: ReliableOutboxRecord | OutboxRecord) -> None:
        normalized = (
            record
            if isinstance(record, ReliableOutboxRecord)
            else ReliableOutboxRecord(record=record)
        )
        self.impl.put(normalized.outbox_id, normalized.to_dict())

    def get(self, outbox_id: str) -> ReliableOutboxRecord | None:
        payload = self.impl.get(outbox_id)
        return ReliableOutboxRecord.from_dict(payload) if payload is not None else None

    def prepare(
        self,
        *,
        record: OutboxRecord,
        transition: Mapping[str, Any],
    ) -> ReliableOutboxRecord:
        prepared = ReliableOutboxRecord(
            record=record,
            delivery_ready=False,
            transition=dict(transition),
        )
        inserted, canonical_payload = self.impl.insert_if_absent(
            prepared.outbox_id,
            prepared.to_dict(),
        )
        canonical = ReliableOutboxRecord.from_dict(canonical_payload)
        if not inserted:
            _assert_same_prepared_semantics(
                existing=canonical,
                incoming_record=record,
                incoming_transition=transition,
            )
        return canonical

    def activate(self, record: ReliableOutboxRecord) -> ReliableOutboxRecord:
        activated = record.activate()
        replaced, canonical_payload = self.impl.compare_and_set(
            record.outbox_id,
            record.to_dict(),
            activated.to_dict(),
        )
        if replaced:
            return activated
        if canonical_payload is None:
            raise ValueError(f"prepared outbox record disappeared: {record.outbox_id}")
        canonical = ReliableOutboxRecord.from_dict(canonical_payload)
        _assert_same_prepared_semantics(
            existing=canonical,
            incoming_record=record.record,
            incoming_transition=record.transition or {},
        )
        if canonical.delivery_ready or canonical.status in {
            OutboxRecordStatus.PUBLISHED,
            OutboxRecordStatus.FAILED,
            OutboxRecordStatus.DEAD_LETTERED,
        }:
            return canonical
        raise ValueError(f"prepared outbox activation changed concurrently: {record.outbox_id}")

    def discard_prepared(self, record: ReliableOutboxRecord) -> bool:
        """Remove an inert intent only while its exact prepared snapshot remains."""

        if record.delivery_ready or record.status != OutboxRecordStatus.PENDING:
            raise ValueError("only pending, non-deliverable prepared records may be discarded")
        if record.claim_token:
            raise ValueError("claimed outbox records cannot be discarded")
        return self.impl.delete_if_matches(record.outbox_id, record.to_dict())

    def list_all(self) -> list[ReliableOutboxRecord]:
        return [ReliableOutboxRecord.from_dict(payload) for payload in self.impl.list_all()]

    def list_pending_and_failed(self) -> list[ReliableOutboxRecord]:
        return [
            record
            for record in self.list_all()
            if record.delivery_ready
            and record.status in {OutboxRecordStatus.PENDING, OutboxRecordStatus.FAILED}
        ]

    def list_due(self, *, now: datetime | None = None) -> list[ReliableOutboxRecord]:
        return [record for record in self.list_pending_and_failed() if record.is_due(now)]

    def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[ReliableOutboxRecord]:
        """Atomically lease due records so only one worker completes each attempt."""

        effective_now = now or utc_now()
        claimed: list[ReliableOutboxRecord] = []
        for candidate in self.list_due(now=effective_now):
            if limit is not None and len(claimed) >= max(0, int(limit)):
                break
            token = f"{str(worker_id).strip() or 'worker'}:{uuid.uuid4()}"
            leased = candidate.claim_for_delivery(
                token=token,
                lease_seconds=lease_seconds,
                now=effective_now,
            )
            replaced, _ = self.impl.compare_and_set(
                candidate.outbox_id,
                candidate.to_dict(),
                leased.to_dict(),
            )
            if replaced:
                claimed.append(leased)
        return claimed

    def complete_published(
        self,
        claimed: ReliableOutboxRecord,
    ) -> tuple[bool, ReliableOutboxRecord]:
        return self._complete_claim(claimed, claimed.mark_published())

    def complete_failed(
        self,
        claimed: ReliableOutboxRecord,
        error: str,
        *,
        max_attempts: int,
        base_delay_seconds: float,
        now: datetime | None = None,
        permanent: bool = False,
    ) -> tuple[bool, ReliableOutboxRecord]:
        failed = claimed.mark_failed(
            error,
            max_attempts=max_attempts,
            base_delay_seconds=base_delay_seconds,
            now=now,
            permanent=permanent,
        )
        return self._complete_claim(claimed, failed)

    def _complete_claim(
        self,
        claimed: ReliableOutboxRecord,
        completed: ReliableOutboxRecord,
    ) -> tuple[bool, ReliableOutboxRecord]:
        if not claimed.claim_token:
            raise ValueError("outbox completion requires a delivery claim")
        replaced, canonical_payload = self.impl.compare_and_set(
            claimed.outbox_id,
            claimed.to_dict(),
            completed.to_dict(),
        )
        if replaced:
            return True, completed
        if canonical_payload is None:
            raise ValueError(f"claimed outbox record disappeared: {claimed.outbox_id}")
        return False, ReliableOutboxRecord.from_dict(canonical_payload)

    def list_prepared(self) -> list[ReliableOutboxRecord]:
        return [
            record
            for record in self.list_all()
            if not record.delivery_ready and record.status == OutboxRecordStatus.PENDING
        ]

    def redrive(self, outbox_id: str, *, actor: str, note: str) -> ReliableOutboxRecord:
        existing = self.get(outbox_id)
        if existing is None:
            raise KeyError(outbox_id)
        redriven = existing.redrive(actor=actor, note=note)
        replaced, canonical_payload = self.impl.compare_and_set(
            outbox_id,
            existing.to_dict(),
            redriven.to_dict(),
        )
        if replaced:
            return redriven
        if canonical_payload is None:
            raise KeyError(outbox_id)
        raise ValueError(f"outbox record changed concurrently before redrive: {outbox_id}")


_STABLE_EVENT_FIELDS = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "sequence_no",
    "payload",
    "idempotency_key",
    "causal_parent_id",
    "producer_service",
    "schema_ref",
    "schema_version",
)


def _assert_same_prepared_semantics(
    *,
    existing: ReliableOutboxRecord,
    incoming_record: OutboxRecord,
    incoming_transition: Mapping[str, Any],
) -> None:
    """Reject deterministic-id reuse for a different delivery intent.

    Trace context plus event/outbox timestamps are deliberately excluded: an
    internal retry constructs fresh observability metadata and must reuse the
    already-persisted canonical envelope.  Domain payload, routing/schema
    identity, owner, and reconciliation transition are immutable semantics.
    """

    mismatches = [
        f"event.{field}"
        for field in _STABLE_EVENT_FIELDS
        if getattr(existing.event, field) != getattr(incoming_record.event, field)
    ]
    if existing.owner_service != incoming_record.owner_service:
        mismatches.append("owner_service")
    if existing.record.schema_version != incoming_record.schema_version:
        mismatches.append("outbox.schema_version")
    if dict(existing.transition or {}) != dict(incoming_transition):
        mismatches.append("transition")
    if mismatches:
        raise ValueError(
            "outbox identity collision for "
            f"outbox_id={incoming_record.outbox_id!r}: mismatched {mismatches}"
        )


class ReliableInboxStore:
    """Persistent inbox receipts keyed by complete immutable event envelopes."""

    def __init__(
        self,
        *,
        backend: str,
        dsn: str | None,
        table_name: str,
        json_path: str | Path,
        owner_service: str,
        consumer_name: str,
    ) -> None:
        self.consumer_name = consumer_name
        self.impl = build_record_store(
            backend=backend,
            dsn=dsn,
            table_name=table_name,
            json_path=json_path,
            owner_service=owner_service,
        )

    def classify(self, event: EventEnvelope) -> tuple[str, dict[str, Any] | None]:
        checksum = sha256_checksum(event.to_dict())
        direct = self.impl.get(event.event_id)
        if direct is not None:
            return ("duplicate", direct) if direct.get("event_checksum") == checksum else ("conflict", direct)
        for existing in self.impl.list_all():
            if existing.get("idempotency_key") != event.idempotency_key:
                continue
            return (
                ("duplicate", existing)
                if existing.get("event_checksum") == checksum
                else ("conflict", existing)
            )
        return "new", None

    def reserve(
        self,
        event: EventEnvelope,
        *,
        result_ref: str,
    ) -> tuple[str, dict[str, Any]]:
        """Atomically claim first-hop admission for an event identity.

        The reservation is written before domain mutation.  Exact concurrent
        callers observe the same reservation instead of both executing the
        consumer, while a divergent event ID or idempotency-key reuse fails
        closed.
        """

        payload = {
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "event_checksum": sha256_checksum(event.to_dict()),
            "event": event.to_dict(),
            "state": "reserved",
            "result_ref": str(result_ref),
        }
        inserted, canonical = self.impl.insert_if_absent(
            event.event_id,
            payload,
            unique_fields=("idempotency_key",),
        )
        if canonical.get("event_checksum") != payload["event_checksum"]:
            return "conflict", canonical
        if str(canonical.get("result_ref") or "") != str(result_ref):
            return "conflict", canonical
        return ("claimed" if inserted else "duplicate"), canonical

    def release_reservation(
        self,
        event: EventEnvelope,
        reservation: Mapping[str, Any],
    ) -> bool:
        """Release only the exact still-unapplied reservation after failure."""

        canonical = dict(reservation)
        if canonical.get("state") != "reserved" or canonical.get("receipt") is not None:
            return False
        if canonical.get("event_checksum") != sha256_checksum(event.to_dict()):
            return False
        return self.impl.delete_if_matches(
            str(canonical.get("event_id") or event.event_id),
            canonical,
        )

    def record_applied(
        self,
        event: EventEnvelope,
        *,
        result_ref: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        receipt = InboxReceipt.record(
            consumer_name=self.consumer_name,
            event=event,
            status=InboxReceiptStatus.APPLIED,
            audit_action_ref=result_ref,
            notes=notes,
        )
        payload = {
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "event_checksum": sha256_checksum(event.to_dict()),
            "event": event.to_dict(),
            "receipt": receipt.to_dict(),
            "result_ref": result_ref,
            "state": "applied",
        }
        inserted, canonical = self.impl.insert_if_absent(
            event.event_id,
            payload,
            unique_fields=("idempotency_key",),
        )
        if inserted:
            return canonical
        if canonical.get("event_checksum") != payload["event_checksum"]:
            raise ValueError(f"divergent replay for event_id={event.event_id!r}")
        if str(canonical.get("result_ref") or "") != str(result_ref):
            raise ValueError(f"divergent result for event_id={event.event_id!r}")
        if canonical.get("state") == "applied" or isinstance(canonical.get("receipt"), Mapping):
            return canonical

        canonical_id = str(canonical.get("event_id") or event.event_id)
        replaced, durable = self.impl.compare_and_set(
            canonical_id,
            canonical,
            payload,
        )
        if replaced:
            return payload
        if (
            durable is not None
            and durable.get("event_checksum") == payload["event_checksum"]
            and str(durable.get("result_ref") or "") == str(result_ref)
            and (durable.get("state") == "applied" or isinstance(durable.get("receipt"), Mapping))
        ):
            return durable
        raise ValueError(f"inbox reservation changed concurrently for event_id={event.event_id!r}")


def reconcile_prepared(
    store: ReliableOutboxStore,
    *,
    transition_applied: Callable[[Mapping[str, Any]], bool],
) -> int:
    """Activate durable prepared records whose local transition is visible."""

    activated = 0
    for record in store.list_prepared():
        if transition_applied(record.transition or {}):
            store.activate(record)
            activated += 1
    return activated
