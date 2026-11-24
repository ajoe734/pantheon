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
import re
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


_STALE_TEMP_HEX = re.compile(r"^[0-9a-f]{32}$")


def atomic_temp_path(target: str | Path) -> Path:
    """Deterministic single temp sibling for ``target`` (at most one per target)."""

    target = Path(target)
    return target.with_name(f"{target.name}.tmp")


def sweep_stale_temp_siblings(target: str | Path) -> list[Path]:
    """Remove leftover temp siblings of ``target``; never touch ``target`` itself.

    Matches the deterministic name plus the legacy random names
    (``<name>.<32 hex>.tmp``, ``.<name>.<32 hex>.tmp`` and the reconciler's
    ``.<name>.<pid>.<32 hex>.tmp``) written by earlier releases.  Only siblings of
    this exact target name match, so other targets in the directory are safe.
    """

    target = Path(target)
    removed: list[Path] = []
    try:
        entries = list(os.scandir(target.parent))
    except OSError:
        return removed
    name = target.name
    deterministic = f"{name}.tmp"
    for entry in entries:
        candidate = entry.name
        if candidate == name or not candidate.endswith(".tmp"):
            continue
        stem = candidate[: -len(".tmp")]
        match = candidate == deterministic
        if not match:
            for base in (name, f".{name}"):
                prefix = f"{base}."
                if not stem.startswith(prefix):
                    continue
                middle = stem[len(prefix):]
                parts = middle.split(".")
                if _STALE_TEMP_HEX.match(parts[-1]) and (
                    len(parts) == 1 or (len(parts) == 2 and parts[0].isdigit())
                ):
                    match = True
                    break
        if not match or not entry.is_file(follow_symlinks=False):
            continue
        try:
            os.unlink(entry.path)
            removed.append(Path(entry.path))
        except OSError:
            continue
    return removed


def atomic_write_text(target: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Durably replace ``target`` through one deterministic temp file.

    The temp file is removed on every exit path, so a failed write leaves no
    orphan; a hard kill leaves at most the single deterministic temp, which
    ``sweep_stale_temp_siblings`` removes on the owner's next start.
    """

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = atomic_temp_path(target)
    directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            with temporary.open("w", encoding=encoding) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.fsync(directory_fd)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        os.close(directory_fd)


class AtomicJsonRecordStore:
    """Concurrent-safe JSON object store with locked atomic replacement."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._thread_lock = threading.RLock()
        try:
            self.sweep_stale_temp()
        except OSError:
            pass

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
        # Compact, key-sorted JSON: still plain JSON, so older readers parse it
        # unchanged, but without the indent=2 whitespace inflation.  The single
        # deterministic temp (removed on every exit path) is swept on start-up.
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        atomic_write_text(self.path, text)

    def sweep_stale_temp(self) -> list[Path]:
        """Remove temp siblings left by a hard-killed writer (lock-guarded)."""

        with self._locked(exclusive=True):
            return sweep_stale_temp_siblings(self.path)

    def compare_and_set_many(
        self,
        updates: list[tuple[str, dict[str, Any] | None, dict[str, Any]]],
    ) -> list[bool]:
        """Apply several snapshot-guarded replacements with one read and one rewrite."""

        for record_id, _expected, _payload in updates:
            if not str(record_id).strip():
                raise ValueError("record_id is required")
        results: list[bool] = []
        with self._locked(exclusive=True):
            records = self._read_unlocked()
            changed = False
            for record_id, expected, payload in updates:
                if records.get(str(record_id)) != expected:
                    results.append(False)
                    continue
                records[str(record_id)] = dict(payload)
                results.append(True)
                changed = True
            if changed:
                self._write_unlocked(records)
        return results

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
        if (
            self.delivery_ready
            or self.status != OutboxRecordStatus.PENDING
            or self.claim_token is not None
        ):
            raise ValueError(f"only an unclaimed prepared outbox record can activate: {self.outbox_id}")
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
        # ``prepare`` deliberately returns the canonical record on exact
        # retries.  It may already be ready, leased, or terminal; those states
        # are monotonic and must never be rebuilt from ``activate()``, which
        # would otherwise erase a live worker claim.
        if (
            record.delivery_ready
            or record.status != OutboxRecordStatus.PENDING
            or record.claim_token is not None
        ):
            canonical = self.get(record.outbox_id)
            if canonical is None:
                raise ValueError(f"outbox record disappeared: {record.outbox_id}")
            _assert_same_prepared_semantics(
                existing=canonical,
                incoming_record=record.record,
                incoming_transition=record.transition or {},
            )
            if canonical.delivery_ready or canonical.status != OutboxRecordStatus.PENDING:
                return canonical
            raise ValueError(f"outbox activation supplied a stale advanced record: {record.outbox_id}")

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
        if canonical.delivery_ready or canonical.status != OutboxRecordStatus.PENDING:
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

    def _list_matching(
        self, statuses: set[str], *, delivery_ready: bool
    ) -> list[ReliableOutboxRecord]:
        # Filter on the raw payload first so published/dead records (the bulk of
        # a long-lived store) never get an envelope and record built for them.
        matched: list[ReliableOutboxRecord] = []
        for payload in self.impl.list_all():
            delivery = payload.get("delivery")
            ready = bool(delivery.get("ready", True)) if isinstance(delivery, Mapping) else True
            status = payload.get("status", OutboxRecordStatus.PENDING.value)
            status = getattr(status, "value", status)
            if ready != delivery_ready or str(status) not in statuses:
                continue
            matched.append(ReliableOutboxRecord.from_dict(payload))
        return matched

    def list_pending_and_failed(self) -> list[ReliableOutboxRecord]:
        return self._list_matching(
            {OutboxRecordStatus.PENDING.value, OutboxRecordStatus.FAILED.value},
            delivery_ready=True,
        )

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
        pending: list[tuple[ReliableOutboxRecord, ReliableOutboxRecord]] = []
        max_claims = None if limit is None else max(0, int(limit))
        for candidate in self.list_due(now=effective_now):
            if max_claims is not None and len(pending) >= max_claims:
                break
            token = f"{str(worker_id).strip() or 'worker'}:{uuid.uuid4()}"
            pending.append(
                (
                    candidate,
                    candidate.claim_for_delivery(
                        token=token,
                        lease_seconds=lease_seconds,
                        now=effective_now,
                    ),
                )
            )
        if not pending:
            return claimed
        batch = getattr(self.impl, "compare_and_set_many", None)
        if batch is not None:
            outcomes = batch(
                [(c.outbox_id, c.to_dict(), leased.to_dict()) for c, leased in pending]
            )
        else:
            outcomes = [
                self.impl.compare_and_set(c.outbox_id, c.to_dict(), leased.to_dict())[0]
                for c, leased in pending
            ]
        for (_candidate, leased), replaced in zip(pending, outcomes):
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
        return self._list_matching(
            {OutboxRecordStatus.PENDING.value}, delivery_ready=False
        )

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


class ReliableInboxConcurrencyError(RuntimeError):
    """A transient inbox reservation race prevented receipt finalization."""


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
        lease_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Atomically claim first-hop admission for an event identity.

        The reservation is written before domain mutation.  Exact concurrent
        callers observe the same reservation instead of both executing the
        consumer, while a divergent event ID or idempotency-key reuse fails
        closed.
        """

        effective_now = now or utc_now()
        payload = {
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "event_checksum": sha256_checksum(event.to_dict()),
            "event": event.to_dict(),
            "state": "reserved",
            "result_ref": str(result_ref),
            "reservation_token": str(uuid.uuid4()),
            "reserved_at": _format_time(effective_now),
            "reservation_expires_at": _format_time(
                effective_now + timedelta(seconds=max(1.0, float(lease_seconds)))
            ),
        }
        inserted, canonical = self.impl.insert_if_absent(
            event.event_id,
            payload,
            unique_fields=("idempotency_key",),
        )
        if canonical.get("event_checksum") != payload["event_checksum"]:
            return "conflict", canonical
        if inserted:
            return "claimed", canonical

        if canonical.get("state") == "applied" or isinstance(canonical.get("receipt"), Mapping):
            return "applied", canonical

        if canonical.get("state") != "reserved":
            return "conflict", canonical

        # A reserved result reference is provisional: a legacy/manual
        # Postmortem can become the incident's canonical result while the
        # claimant is running.  Event identity stays immutable and the first
        # applied receipt finalizes the actual result reference.
        expires_at = _parse_time(canonical.get("reservation_expires_at"))
        if expires_at is None or expires_at <= effective_now:
            canonical_id = str(canonical.get("event_id") or event.event_id)
            replaced, durable = self.impl.compare_and_set(
                canonical_id,
                canonical,
                payload,
            )
            if replaced:
                return "claimed", payload
            if durable is None:
                return "in_progress", canonical
            if durable.get("event_checksum") != payload["event_checksum"]:
                return "conflict", durable
            if durable.get("state") == "applied" or isinstance(durable.get("receipt"), Mapping):
                return "applied", durable
            return "in_progress", durable
        return "in_progress", canonical

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
        reservation: Mapping[str, Any] | None = None,
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

        if reservation is not None:
            claimed = dict(reservation)
            if (
                claimed.get("state") != "reserved"
                or not str(claimed.get("reservation_token") or "").strip()
                or claimed.get("event_checksum") != payload["event_checksum"]
            ):
                raise ReliableInboxConcurrencyError(
                    f"invalid inbox reservation claim for event_id={event.event_id!r}"
                )
            canonical_id = str(claimed.get("event_id") or event.event_id)
            replaced, durable = self.impl.compare_and_set(
                canonical_id,
                claimed,
                payload,
            )
            if replaced:
                return payload
            return self._resolve_applied_cas_loss(
                event=event,
                result_ref=result_ref,
                payload=payload,
                durable=durable,
            )

        inserted, canonical = self.impl.insert_if_absent(
            event.event_id,
            payload,
            unique_fields=("idempotency_key",),
        )
        if inserted:
            return canonical
        if canonical.get("event_checksum") != payload["event_checksum"]:
            raise ValueError(f"divergent replay for event_id={event.event_id!r}")
        if canonical.get("state") == "applied" or isinstance(canonical.get("receipt"), Mapping):
            if str(canonical.get("result_ref") or "") != str(result_ref):
                raise ValueError(f"divergent result for event_id={event.event_id!r}")
            return canonical

        # Reserved receipts must be completed with the exact claim returned by
        # ``reserve``.  An ownerless caller must not adopt a newer claimant's
        # token after lease expiry.
        raise ReliableInboxConcurrencyError(
            f"inbox reservation claim required for event_id={event.event_id!r}"
        )

    @staticmethod
    def _resolve_applied_cas_loss(
        *,
        event: EventEnvelope,
        result_ref: str,
        payload: Mapping[str, Any],
        durable: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if durable is None:
            raise ReliableInboxConcurrencyError(
                f"inbox reservation disappeared concurrently for event_id={event.event_id!r}"
            )
        if durable.get("event_checksum") != payload["event_checksum"]:
            raise ValueError(f"divergent replay for event_id={event.event_id!r}")
        if durable.get("state") == "applied" or isinstance(durable.get("receipt"), Mapping):
            if str(durable.get("result_ref") or "") != str(result_ref):
                raise ValueError(f"divergent result for event_id={event.event_id!r}")
            return durable
        raise ReliableInboxConcurrencyError(
            f"inbox reservation changed concurrently for event_id={event.event_id!r}"
        )


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
