"""
Telemetry Ingest Service — shock absorption layer.

TEL-002: This is the main service that ties together:
- DurableBuffer (Layer C)
- AsyncBatchWriter (Layer D)
- BackpressureController
- DeadLetterQueue

Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §2.2, this service is the
formal path between event producers and canonical Postgres — LEAN runtime
must NEVER directly write to Postgres telemetry tables.

The ingest service provides:
1. Event intake with schema validation (TEL-001 evidence contract E-1 through E-6)
2. RuntimeBinding truth validation (binding_id resolved against authoritative store)
3. Durable buffering (bounded, with overflow protection)
4. Async batch writing (micro-batching, retry, partition routing)
5. Backpressure management (adaptive concurrency, delay non-critical events)
6. Dead-letter handling (diagnostic tags, JSONL spill, startup loading,
   and replay support)
7. Idempotent deduplication by event_id (service layer + transactional conflict
   detection at the canonical write layer)

Replay policy
-------------
replay_dlq() only re-enqueues events whose DLQ tag indicates a *write failure*
(TAG_WRITER_ERROR, TAG_RETRY_EXHAUSTED).  Events rejected for schema or
evidence violations are never re-enqueued without operator intervention,
because replaying a binding-invalid event into the canonical write path would
break AC-1 (canonical stage/binding-reference guarantee).

All replay goes through the full ingest() path (re-validates schema + evidence).

Production wiring
-----------------
Replace the default memory-only sink with build_postgres_write_fn():

    write_fn = build_postgres_write_fn(dsn=os.environ["TELEMETRY_DB_DSN"])
    svc = TelemetryIngestService(write_fn=write_fn, ...)

The Postgres write function distinguishes an exact retry from a conflicting
reuse of an event_id in the same transaction.  Newly committed rows emit one
transaction-scoped pg_notify wake-up; PostgreSQL delivers that notification
only after the rows are commit-visible.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Mapping, Optional, Protocol, runtime_checkable

try:  # POSIX advisory locking makes the admission ledger replica-safe.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from .buffer import DurableBuffer, create_buffer
from .batch_writer import AsyncBatchWriter, WriteResult
from .backpressure import BackpressureController, CRITICAL_EVENT_TYPES
from .dead_letter import (
    DeadLetterQueue,
    TAG_SCHEMA_VIOLATION,
    TAG_BINDING_MISMATCH,
    TAG_TEMPORAL_VIOLATION,
    TAG_BUFFER_OVERFLOW,
    TAG_WRITER_ERROR,
    TAG_RETRY_EXHAUSTED,
)
from .runtime_summary import RuntimeSummaryProjectionStore
from .trade_episode_projection import TradeEpisodeProjectionStore

TRADE_JOURNAL_EVENT_TYPES = frozenset({
    "trade_episode.opened",
    "trade_episode.updated",
    "trade_episode.closed",
    "trade_episode.unresolved",
    "trade_reflection.requested",
    "trade_reflection.completed",
    "trade_reflection.failed",
    "trade_lesson.proposed",
    "trade_lesson.reviewed",
    "trade_lesson.merged",
    "trade_lesson.quarantined",
})

try:
    import jsonschema
except ImportError:
    jsonschema = None

log = logging.getLogger(__name__)

TELEMETRY_COMMIT_NOTIFY_CHANNEL = "pantheon_lifecycle_events"

# --- Non-trading infrastructure health contract -----------------------------
# OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001. Infrastructure health is admitted
# through its own authoritative schema and its own strict-auth route. It never
# carries, requires, or invents RuntimeBinding evidence, and it never enters the
# trading ingest path, so trading binding and lineage validation are unaffected.
INFRASTRUCTURE_HEALTH_EVENT_TYPE = "infrastructure_health"
INFRASTRUCTURE_HEALTH_SCHEMA_VERSION = "pantheon.infrastructure-health/1"
INFRASTRUCTURE_HEALTH_SCHEMA_DEFINITION = "InfrastructureHealthEvent"
INFRASTRUCTURE_HEALTH_LEDGER_FILENAME = "infrastructure_health_admissions.jsonl"

# RuntimeBinding evidence fields. An infrastructure producer presenting any of
# these — at any depth, including inside metadata — is rejected outright rather
# than being allowed to look like an attributable trading event.
RUNTIME_BINDING_EVIDENCE_FIELDS = frozenset({
    "binding_id",
    "runtime_id",
    "capital_pool_id",
    "artifact_id",
    "artifact_version",
    "deployment_stage",
    "execution_mode",
    "plan_id",
    "persona_capital_binding_id",
    "rollback_parent",
    "rollback_action_type",
})

TRADE_JOURNEY_FIXTURE_EVENT_TYPE = "trade_journey_fixture"
TRADE_JOURNEY_FIXTURE_SCHEMA_VERSION = "pantheon.trade-journey-fixture.v1"
TRADE_JOURNEY_FIXTURE_SOURCE = "tj_e2e_012_hosted_seed_v3"
TRADE_JOURNEY_FIXTURE_TENANT = "tenant-dev"


class ConflictingTelemetryEventError(ValueError):
    """An event_id was reused for content other than the immutable original."""


# ---------------------------------------------------------------------------
# Canonical Postgres value normalization
# ---------------------------------------------------------------------------

def _coerce_postgres_created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("telemetry event missing created_at")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid telemetry created_at: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

# ---------------------------------------------------------------------------
# Replay policy constants
# ---------------------------------------------------------------------------

# Tags that indicate a transient *write failure* — safe to replay after recovery
_WRITE_FAILURE_TAGS = (TAG_WRITER_ERROR, TAG_RETRY_EXHAUSTED)

# Tags that indicate a *validation failure* — must NOT be replayed without
# operator intervention and re-validation
_VALIDATION_FAILURE_TAGS = frozenset({
    TAG_SCHEMA_VIOLATION,
    TAG_BINDING_MISMATCH,
    TAG_TEMPORAL_VIOLATION,
})

# ---------------------------------------------------------------------------
# Durable infrastructure health admission ledger
# ---------------------------------------------------------------------------


def infrastructure_health_fingerprint(event: Mapping[str, Any]) -> str:
    """Return the content fingerprint used to detect event_id reuse."""

    payload = json.dumps(
        dict(event),
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class InfrastructureHealthReservation:
    """Result of one attempt to reserve a stable infrastructure event ID."""

    outcome: str
    token: Optional[str] = None
    detail: Optional[str] = None


class InfrastructureHealthAdmissionLedger:
    """Durable, replica-safe two-phase admission log keyed by stable event ID.

    The in-process dedup set of :class:`TelemetryIngestService` is lost on
    restart and is not shared between replicas, so it cannot by itself make
    admission idempotent for a producer that retries across a redeploy or that
    runs two probe replicas. This ledger is an append-only JSONL file in the
    shared telemetry storage directory, guarded by a POSIX advisory lock so
    concurrent replicas serialise on the same event_id.

    Admission is deliberately **two-phase**, because a single-phase
    "record then enqueue" log has a loss window: a second caller arriving
    between the record and the durable enqueue would be answered as a
    successful duplicate even though nothing had been persisted yet, and a crash
    in that window would leave an event_id that was permanently marked admitted
    and permanently never enqueued.

    Record states:

    * ``reserved`` — one caller holds a leased, fenced claim on the event_id.
      It carries the owner ``token`` and an ``expires_at`` lease. No caller may
      be told the event was accepted while the claim is only reserved.
    * ``committed`` — a durable enqueue receipt exists. Only now is the event_id
      an idempotent success for every later retry.
    * ``released`` — the owner's enqueue did not succeed, so the event_id is
      free to be admitted again.

    ``commit`` and ``release`` are token-scoped, so a slow owner whose lease
    expired and was taken over by another caller is fenced out and cannot
    resurrect or cancel someone else's claim. An expired reservation left by a
    crashed process is recovered by the next caller, which steals the claim with
    a fresh token.
    """

    STATE_RESERVED = "reserved"
    STATE_COMMITTED = "committed"
    STATE_RELEASED = "released"

    OUTCOME_RESERVED = "reserved"
    OUTCOME_COMMITTED = "committed"
    OUTCOME_IN_FLIGHT = "in_flight"
    OUTCOME_CONFLICT = "conflict"
    OUTCOME_FENCED = "fenced"

    DEFAULT_LEASE_SECONDS = 30.0

    def __init__(self, path: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        self._lease_seconds = max(0.1, float(lease_seconds))
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._offset = 0
        with self._lock:
            self._refresh_locked()

    @property
    def path(self) -> str:
        return str(self._path)

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds

    # -- internal helpers --

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _stamp(moment: datetime) -> str:
        return moment.isoformat().replace("+00:00", "Z")

    @classmethod
    def _parse_stamp(cls, value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _reservation_expired(cls, record: Mapping[str, Any], now: datetime) -> bool:
        expires_at = cls._parse_stamp(record.get("expires_at"))
        if expires_at is None:
            # A reservation without a readable lease cannot be trusted to be
            # live; treat it as expired so a crashed owner never blocks forever.
            return True
        return now >= expires_at

    def _refresh_locked(self) -> None:
        """Replay ledger records written since the last read, in order."""
        with self._path.open("rb") as handle:
            handle.seek(self._offset)
            pending = handle.read()
        consumed = 0
        for raw_line in pending.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                # Partial trailing write from a crashed or concurrent writer.
                # Leave it unconsumed and re-read it once it is complete.
                break
            consumed += len(raw_line)
            try:
                record = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                log.warning("Skipping unreadable infrastructure health ledger record")
                continue
            if not isinstance(record, dict):
                continue
            event_id = str(record.get("event_id") or "").strip()
            if not event_id:
                continue
            if record.get("state") == self.STATE_RELEASED:
                self._records.pop(event_id, None)
            else:
                self._records[event_id] = record
        self._offset += consumed

    def _append_locked(self, handle, record: dict[str, Any]) -> None:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        handle.seek(0, os.SEEK_END)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
        self._offset = handle.tell()

    def _locked_handle(self):
        handle = self._path.open("r+b")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    @staticmethod
    def _unlock(handle) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    # -- public API --

    def begin(
        self,
        event_id: str,
        fingerprint: str,
        *,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> InfrastructureHealthReservation:
        """Phase one: try to take a leased, fenced claim on *event_id*.

        Outcomes:

        * ``reserved`` — the caller owns the claim and must follow with
          :meth:`commit` after a durable enqueue, or :meth:`release`;
        * ``committed`` — a durable receipt already exists for identical
          content, so this is a true idempotent duplicate;
        * ``in_flight`` — another caller holds a live claim and no durable
          receipt exists yet, so this caller must not be told it succeeded;
        * ``conflict`` — the event_id is already bound to different content.
        """
        clean_id = str(event_id or "").strip()
        if not clean_id:
            raise ValueError("infrastructure health admission requires an event_id")
        now = self._now()
        with self._lock:
            handle = self._locked_handle()
            try:
                self._refresh_locked()
                current = self._records.get(clean_id)
                if current is not None:
                    if str(current.get("fingerprint") or "") != fingerprint:
                        return InfrastructureHealthReservation(
                            self.OUTCOME_CONFLICT,
                            detail=f"event_id is bound to state {current.get('state')!r} with different content",
                        )
                    if current.get("state") == self.STATE_COMMITTED:
                        return InfrastructureHealthReservation(self.OUTCOME_COMMITTED)
                    if not self._reservation_expired(current, now):
                        return InfrastructureHealthReservation(
                            self.OUTCOME_IN_FLIGHT,
                            detail="another admission attempt holds a live reservation",
                        )
                token = uuid.uuid4().hex
                record: dict[str, Any] = {
                    "event_id": clean_id,
                    "fingerprint": fingerprint,
                    "state": self.STATE_RESERVED,
                    "token": token,
                    "reserved_at": self._stamp(now),
                    "expires_at": self._stamp(now + timedelta(seconds=self._lease_seconds)),
                }
                if current is not None:
                    # Recovering a crashed or stalled owner's expired claim.
                    record["recovered_token"] = current.get("token")
                for key, value in (attributes or {}).items():
                    record.setdefault(str(key), value)
                self._append_locked(handle, record)
                self._records[clean_id] = record
                return InfrastructureHealthReservation(self.OUTCOME_RESERVED, token=token)
            finally:
                self._unlock(handle)

    def commit(self, event_id: str, fingerprint: str, token: str) -> str:
        """Phase two: record the durable receipt for a claim this caller owns.

        Returns ``committed`` when the receipt is now durable — including the
        case where the current owner already committed identical content —
        ``conflict`` when the event_id is bound to different content, and
        ``fenced`` when this caller no longer owns the claim.
        """
        clean_id = str(event_id or "").strip()
        if not clean_id:
            return self.OUTCOME_FENCED
        now = self._now()
        with self._lock:
            handle = self._locked_handle()
            try:
                self._refresh_locked()
                current = self._records.get(clean_id)
                if current is None:
                    return self.OUTCOME_FENCED
                if str(current.get("fingerprint") or "") != fingerprint:
                    return self.OUTCOME_CONFLICT
                if current.get("state") == self.STATE_COMMITTED:
                    # Someone durably committed identical content. The receipt
                    # this caller needs already exists, so this is not a loss.
                    return self.OUTCOME_COMMITTED
                if str(current.get("token") or "") != str(token or ""):
                    return self.OUTCOME_FENCED
                record = {
                    key: value
                    for key, value in current.items()
                    if key not in ("state", "expires_at")
                }
                record["state"] = self.STATE_COMMITTED
                record["committed_at"] = self._stamp(now)
                self._append_locked(handle, record)
                self._records[clean_id] = record
                return self.OUTCOME_COMMITTED
            finally:
                self._unlock(handle)

    def release(self, event_id: str, token: str) -> bool:
        """Release a claim this caller owns whose durable enqueue failed."""
        clean_id = str(event_id or "").strip()
        if not clean_id:
            return False
        with self._lock:
            handle = self._locked_handle()
            try:
                self._refresh_locked()
                current = self._records.get(clean_id)
                if current is None:
                    return False
                if current.get("state") != self.STATE_RESERVED:
                    return False
                if str(current.get("token") or "") != str(token or ""):
                    # Fenced: the lease was taken over by another caller.
                    return False
                self._append_locked(
                    handle,
                    {
                        "event_id": clean_id,
                        "state": self.STATE_RELEASED,
                        "token": current.get("token"),
                        "released_at": self._stamp(self._now()),
                    },
                )
                self._records.pop(clean_id, None)
                return True
            finally:
                self._unlock(handle)

    def is_committed(self, event_id: str) -> bool:
        """Return whether a durable receipt exists for *event_id*."""
        clean_id = str(event_id or "").strip()
        if not clean_id:
            return False
        with self._lock:
            self._refresh_locked()
            current = self._records.get(clean_id)
            return bool(current) and current.get("state") == self.STATE_COMMITTED

    def state_of(self, event_id: str) -> Optional[str]:
        clean_id = str(event_id or "").strip()
        if not clean_id:
            return None
        with self._lock:
            self._refresh_locked()
            current = self._records.get(clean_id)
            return None if current is None else str(current.get("state") or "")

    def stats(self) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            self._refresh_locked()
            committed = 0
            open_reservations = 0
            expired_reservations = 0
            for record in self._records.values():
                if record.get("state") == self.STATE_COMMITTED:
                    committed += 1
                elif record.get("state") == self.STATE_RESERVED:
                    if self._reservation_expired(record, now):
                        expired_reservations += 1
                    else:
                        open_reservations += 1
            return {
                "path": str(self._path),
                "committed_event_ids": committed,
                "open_reservations": open_reservations,
                "recoverable_expired_reservations": expired_reservations,
                "lease_seconds": self._lease_seconds,
            }


# ---------------------------------------------------------------------------
# RuntimeBinding protocol (injected dependency)
# ---------------------------------------------------------------------------


@runtime_checkable
class RuntimeBindingProtocol(Protocol):
    """
    Protocol for authoritative RuntimeBinding lookups.

    The ingest service uses this to resolve binding_id references against the
    canonical RuntimeBinding store (owned by the Runtime Manager service).

    In production, inject the RuntimeManagerService or a thin read-only adapter.
    In tests, inject a mock or stub that returns pre-configured bindings.
    """

    def get_binding(self, binding_id: str) -> Optional[Any]:
        """
        Look up a RuntimeBinding by ID.

        Returns the binding object (must expose runtime_id, capital_pool_id,
        artifact_id, artifact_version, deployment_mode, effective_at,
        retired_at, plan_id, persona_capital_binding_id attributes) or None
        if the binding_id is not found.
        """
        ...


# ---------------------------------------------------------------------------
# Canonical Postgres write function factory
# ---------------------------------------------------------------------------


def build_postgres_write_fn(
    dsn: str,
    table: str = "telemetry_events",
    notify_channel: str = TELEMETRY_COMMIT_NOTIFY_CHANNEL,
) -> Callable[[list[dict[str, Any]]], Coroutine[Any, Any, WriteResult]]:
    """
    Build the canonical Postgres batch-write function for production wiring.

    A batch is committed atomically.  ON CONFLICT (event_id) DO NOTHING is
    followed by an equality check against the committed immutable row:

    * exact duplicates are successful no-ops;
    * conflicting duplicates fail the whole batch as non-retryable;
    * new rows receive database-owned ingested_seq / ingested_at values.

    When at least one new row is inserted, pg_notify is invoked inside the
    write transaction.  PostgreSQL releases the notification only when that
    same transaction commits, so consumers can immediately read every
    advertised ingested_seq from the canonical table.

    Example
    -------
        write_fn = build_postgres_write_fn(dsn=os.environ["TELEMETRY_DB_DSN"])
        svc = TelemetryIngestService(write_fn=write_fn, binding_store=store)

    The expected table DDL (simplified):

        CREATE TABLE telemetry_events (
            event_id        TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL,
            payload         JSONB NOT NULL,
            ingested_seq    BIGINT NOT NULL DEFAULT nextval('telemetry_events_ingested_seq_seq'),
            ingested_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
    """
    import json as _json

    table_parts = table.split(".")
    if not table_parts or any(
        not part
        or not (part[0].isalpha() or part[0] == "_")
        or any(not (char.isalnum() or char == "_") for char in part)
        for part in table_parts
    ):
        raise ValueError(f"invalid Postgres table identifier: {table!r}")
    if not notify_channel or len(notify_channel.encode("utf-8")) > 63 or "\x00" in notify_channel:
        raise ValueError("notify_channel must be a non-empty Postgres identifier of at most 63 bytes")

    insert_sql = (
        f"INSERT INTO {table} "
        f"(event_id, event_type, created_at, payload) "
        f"VALUES ($1, $2, $3::timestamptz, $4::jsonb) "
        f"ON CONFLICT (event_id) DO NOTHING "
        f"RETURNING ingested_seq"
    )
    exact_duplicate_sql = (
        f"SELECT event_type = $2 "
        f"AND created_at = $3::timestamptz "
        f"AND payload = $4::jsonb "
        f"FROM {table} WHERE event_id = $1"
    )

    async def _postgres_write(batch: list[dict[str, Any]]) -> WriteResult:
        try:
            import asyncpg  # type: ignore[import]
        except ImportError:
            return WriteResult.fail("asyncpg not installed", retryable=False)
        try:
            conn = await asyncpg.connect(dsn)
            try:
                rows = [
                    (
                        ev.get("event_id"),
                        ev.get("event_type"),
                        _coerce_postgres_created_at(ev.get("created_at")),
                        _json.dumps(ev, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    )
                    for ev in batch
                ]
                inserted_sequences: list[int] = []
                async with conn.transaction():
                    # Sequence values are allocated before commit. Serializing
                    # canonical writer transactions prevents a later sequence
                    # from committing before an earlier one and being used to
                    # advance a projector checkpoint past an invisible gap.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        table,
                    )
                    for row in rows:
                        inserted = await conn.fetchrow(insert_sql, *row)
                        if inserted is not None:
                            inserted_sequences.append(int(inserted["ingested_seq"]))
                            continue

                        exact_duplicate = await conn.fetchval(exact_duplicate_sql, *row)
                        if exact_duplicate is not True:
                            raise ConflictingTelemetryEventError(
                                f"conflicting duplicate event_id={row[0]}"
                            )

                    if inserted_sequences:
                        notification = _json.dumps(
                            {
                                "schema_version": "telemetry-commit-notification/1",
                                "table": table,
                                "inserted_count": len(inserted_sequences),
                                "first_ingested_seq": min(inserted_sequences),
                                "last_ingested_seq": max(inserted_sequences),
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        await conn.execute(
                            "SELECT pg_notify($1, $2)",
                            notify_channel,
                            notification,
                        )
            finally:
                await conn.close()
            return WriteResult.ok(len(inserted_sequences))
        except ConflictingTelemetryEventError as exc:
            return WriteResult.fail(str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001
            return WriteResult.fail(str(exc), retryable=True)

    return _postgres_write


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class TelemetryIngestService:
    """
    Main telemetry ingest service with shock absorption.

    Usage:
        service = TelemetryIngestService(
            schema_path="services/telemetry/telemetry_event.schema.json",
            storage_dir="/tmp/telemetry_spill",
            binding_store=runtime_manager_service,  # for production
            write_fn=build_postgres_write_fn(dsn=os.environ["TELEMETRY_DB_DSN"]),
        )
        await service.start()

        # Ingest events
        await service.ingest(event_dict)

        # Shutdown
        await service.stop()
    """

    def __init__(
        self,
        schema_path: Optional[str] = None,
        storage_dir: Optional[str] = None,
        buffer_backend: str = "memory",
        buffer: Optional[DurableBuffer] = None,
        buffer_maxsize: int = 100_000,
        buffer_redis_url: str = "redis://localhost:6379/0",
        buffer_nats_url: str = "nats://localhost:4222",
        buffer_stream_name: str = "PANTHEON_TELEMETRY_INGEST",
        buffer_subject: str = "pantheon.telemetry.ingest",
        buffer_durable_name: str = "telemetry-postgres-writer",
        batch_size: int = 500,
        batch_interval: float = 1.0,
        max_retries: int = 5,
        dlq_spill_path: Optional[str] = None,
        dlq_incident_threshold: int = 100,
        write_fn: Optional[Callable[[list[dict[str, Any]]], Coroutine[Any, Any, WriteResult]]] = None,
        schema: Optional[dict[str, Any]] = None,
        binding_store: Optional[RuntimeBindingProtocol] = None,
        runtime_summary_store: Optional[RuntimeSummaryProjectionStore] = None,
        trade_episode_projection_store: Optional[TradeEpisodeProjectionStore] = None,
        lineage_write_store: Optional[Any] = None,
        dedup_max_size: int = 500_000,
        replay_dlq_on_start: bool = False,
        dlq_replay_tag_filter: Optional[str] = None,
        infrastructure_health_ledger_path: Optional[str] = None,
        infrastructure_health_lease_seconds: Optional[float] = None,
    ):
        """
        Parameters
        ----------
        schema_path : str, optional
            Path to telemetry_event.schema.json for validation.
        storage_dir : str, optional
            Directory for spill files (DLQ, emergency buffer).
        buffer_backend : str
            "jetstream" (deployed default), "redis", or explicit test-only
            "memory".
        buffer : DurableBuffer, optional
            Pre-built buffer instance used instead of the ``buffer_backend``
            factory. This is an in-process wiring seam for tests and embedders
            that supply their own broker adapter; it is deliberately not
            reachable from configuration, and it grants no durability of its
            own. Infrastructure health admission trusts ``is_durable()`` alone,
            so injecting a volatile buffer here still fails closed.
        buffer_maxsize : int
            Max events in buffer before backpressure.
        buffer_redis_url : str
            Redis URL (only used if buffer_backend="redis").
        buffer_nats_url : str
            NATS URL (only used if buffer_backend="jetstream").
        batch_size : int
            Max events per write batch.
        batch_interval : float
            Max seconds before flushing a partial batch.
        max_retries : int
            Max retries for transient write failures.
        dlq_spill_path : str, optional
            Path to DLQ JSONL spill file.
        dlq_incident_threshold : int
            DLQ entries before incident alert fires.
        write_fn : async callable, optional
            Custom write function.  If None, uses a memory-only test sink.
            For production, use build_postgres_write_fn().
        schema : dict, optional
            Pre-loaded schema dict (alternative to schema_path).
        binding_store : RuntimeBindingProtocol, optional
            Authoritative RuntimeBinding store for evidence cross-validation.
            When provided, binding_id is resolved and all identity fields plus
            temporal window are verified against the canonical binding record.
            When absent, only field-presence and enum checks are applied.
        runtime_summary_store : RuntimeSummaryProjectionStore, optional
            Telemetry-owned read model updated after validated paper telemetry
            is accepted, used by the BFF runtime-state surfaces.
        lineage_write_store : LineageReadService, optional
            LIN-003 live lineage write path. When provided, every accepted
            event (and its resolved RuntimeBinding, if not already a graph
            node) is admitted into this lineage graph immediately, so the
            deployed lineage read surface resolves newly-ingested events
            without waiting for a static corpus reload. Failures here are
            logged and never fail the ingest call.
        dedup_max_size : int
            Maximum number of event_ids tracked for idempotent deduplication.
            When exceeded, the oldest half of tracked IDs are evicted.
        replay_dlq_on_start : bool
            If True, load persisted DLQ spill entries and replay safe write
            failures after the writer starts. Validation-failure entries remain
            blocked by replay_dlq() policy.
        dlq_replay_tag_filter : str, optional
            Optional explicit tag filter for startup replay. When None, startup
            replay uses the safe default write-failure tag set.
        infrastructure_health_ledger_path : str, optional
            Path to the durable infrastructure health admission ledger. Defaults
            to ``<storage_dir>/infrastructure_health_admissions.jsonl``. When no
            path can be resolved, infrastructure health ingestion fails closed
            rather than admitting events it cannot deduplicate durably.
        infrastructure_health_lease_seconds : float, optional
            Lease held by one infrastructure health admission reservation before
            another caller may recover it. It must exceed the worst-case durable
            enqueue latency; a crashed owner's claim becomes recoverable only
            after it expires.
        """
        # Schema
        self._schema: Optional[dict[str, Any]] = schema
        self._schema_path = schema_path
        self._infrastructure_health_schema: Optional[dict[str, Any]] = None
        self._trade_journal_schema: Optional[dict[str, Any]] = None
        self._trade_journal_schema_path = str(Path(schema_path).parent / "trade_journal_event.schema.json") if schema_path else None
        if self._schema_path and not self._schema:
            self._load_schema()
        self._infrastructure_health_schema = self._extract_infrastructure_health_schema()

        # Buffer
        if buffer is not None:
            self._buffer: DurableBuffer = buffer
        else:
            buffer_kwargs: dict[str, Any] = {"maxsize": buffer_maxsize}
            if buffer_backend == "redis":
                buffer_kwargs["redis_url"] = buffer_redis_url
            elif buffer_backend in {"jetstream", "nats", "nats_jetstream"}:
                buffer_kwargs.update(
                    {
                        "nats_url": buffer_nats_url,
                        "stream_name": buffer_stream_name,
                        "subject": buffer_subject,
                        "durable_name": buffer_durable_name,
                    }
                )
            self._buffer = create_buffer(backend=buffer_backend, **buffer_kwargs)

        # Dead-letter queue
        dlq_spill = dlq_spill_path
        if not dlq_spill and storage_dir:
            dlq_spill = str(Path(storage_dir) / "dead_letter.jsonl")
        self._dlq = DeadLetterQueue(
            spill_path=dlq_spill,
            incident_threshold=dlq_incident_threshold,
        )

        # Backpressure controller
        self._backpressure = BackpressureController(
            max_concurrency=8,
            default_concurrency=4,
            min_concurrency=1,
        )
        self._backpressure.set_buffer_utilization_fn(
            lambda: self._buffer.size() / buffer_maxsize if buffer_maxsize else 0.0
        )

        # RuntimeBinding store for authoritative evidence cross-validation
        self._binding_store = binding_store
        self._runtime_summary_store = runtime_summary_store
        self._trade_episode_projection_store = trade_episode_projection_store
        self._lineage_write_store = lineage_write_store

        # Write function
        self._write_fn = write_fn or self._default_write_fn

        # Batch writer
        self._writer = AsyncBatchWriter(
            buffer=self._buffer,
            write_fn=self._write_fn,
            dead_letter_queue=self._dlq,
            backpressure=self._backpressure,
            batch_size=batch_size,
            batch_interval=batch_interval,
            max_retries=max_retries,
        )

        # Idempotent deduplication by event_id
        self._seen_event_ids: dict[str, dict[str, Any]] = {}
        self._dedup_max_size = dedup_max_size

        # Durable infrastructure health admission ledger. Unlike the in-process
        # dedup set above, this survives restart and is shared between replicas
        # that mount the same telemetry storage directory.
        ledger_path = infrastructure_health_ledger_path
        if not ledger_path and storage_dir:
            ledger_path = str(Path(storage_dir) / INFRASTRUCTURE_HEALTH_LEDGER_FILENAME)
        self._infrastructure_health_ledger: Optional[InfrastructureHealthAdmissionLedger] = None
        if ledger_path:
            try:
                self._infrastructure_health_ledger = InfrastructureHealthAdmissionLedger(
                    ledger_path,
                    lease_seconds=(
                        infrastructure_health_lease_seconds
                        if infrastructure_health_lease_seconds is not None
                        else InfrastructureHealthAdmissionLedger.DEFAULT_LEASE_SECONDS
                    ),
                )
            except OSError as exc:
                log.error(
                    "Infrastructure health admission ledger unavailable at %s: %s",
                    ledger_path,
                    exc,
                )
        else:
            log.warning(
                "No telemetry storage directory configured — infrastructure health "
                "ingestion will fail closed without a durable admission ledger"
            )
        self._infrastructure_health_admitted = 0
        self._infrastructure_health_duplicates = 0
        self._infrastructure_health_conflicts = 0
        self._infrastructure_health_in_flight = 0
        self._infrastructure_health_fenced = 0
        self._infrastructure_health_non_durable = 0
        self._infrastructure_health_rejected = 0

        # State
        self._started = False
        self._total_ingested = 0
        self._total_rejected = 0
        self._total_duplicates = 0
        self._start_time: Optional[float] = None
        self._dlq_loaded_from_spill = False
        self._dlq_loaded_from_spill_count = 0
        self._replay_dlq_on_start = replay_dlq_on_start
        self._dlq_replay_tag_filter = dlq_replay_tag_filter
        self._startup_dlq_replay_count = 0

    def _load_schema(self) -> None:
        """Load JSON schemas from file."""
        if self._schema_path:
            try:
                import json
                with open(self._schema_path, "r") as f:
                    self._schema = json.load(f)
                log.info(f"Loaded telemetry schema from {self._schema_path}")
            except Exception as e:
                log.warning(f"Failed to load telemetry schema: {e}")
                self._schema = None

        if self._trade_journal_schema_path and Path(self._trade_journal_schema_path).exists():
            try:
                import json
                with open(self._trade_journal_schema_path, "r") as f:
                    self._trade_journal_schema = json.load(f)
                log.info(f"Loaded trade journal schema from {self._trade_journal_schema_path}")
            except Exception as e:
                log.warning(f"Failed to load trade journal schema: {e}")
                self._trade_journal_schema = None

    def _extract_infrastructure_health_schema(self) -> Optional[dict[str, Any]]:
        """Return the standalone non-trading infrastructure health schema."""
        if not isinstance(self._schema, dict):
            return None
        definitions = self._schema.get("definitions")
        if not isinstance(definitions, dict):
            return None
        infrastructure = definitions.get(INFRASTRUCTURE_HEALTH_SCHEMA_DEFINITION)
        if not isinstance(infrastructure, dict):
            log.warning(
                "Telemetry schema has no %s definition — infrastructure health "
                "ingestion will fail closed",
                INFRASTRUCTURE_HEALTH_SCHEMA_DEFINITION,
            )
            return None
        return infrastructure

    def _validate_event(self, event: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate event against schema.

        Returns (valid, error_message).
        """
        event_type = event.get("event_type")
        if event_type == INFRASTRUCTURE_HEALTH_EVENT_TYPE:
            # Infrastructure health has its own authority, its own schema, and
            # its own route. It must never reach the trading ingest path, where
            # there is no RuntimeBinding to validate it against.
            return False, (
                "infrastructure_health events must be admitted through the "
                "infrastructure health authority, not the trading telemetry path"
            )
        if event_type in TRADE_JOURNAL_EVENT_TYPES:
            if not self._trade_journal_schema or not jsonschema:
                return True, None
            try:
                jsonschema.validate(instance=event, schema=self._trade_journal_schema)
                return True, None
            except jsonschema.ValidationError as e:
                return False, e.message
            except jsonschema.SchemaError as e:
                return False, f"Schema error: {e.message}"

        if not self._schema or not jsonschema:
            return True, None

        try:
            jsonschema.validate(instance=event, schema=self._schema)
            return True, None
        except jsonschema.ValidationError as e:
            return False, e.message
        except jsonschema.SchemaError as e:
            return False, f"Schema error: {e.message}"

    def _validate_evidence_contract(
        self, event: dict[str, Any]
    ) -> tuple[bool, Optional[str], Optional[Any]]:
        """
        Validate TEL-001A evidence contract (E-1 through E-6).

        When a binding_store is configured, binding_id is resolved against the
        authoritative RuntimeBinding record and all identity fields plus the
        temporal window are cross-checked.

        Returns (valid, error_message, binding). ``binding`` is the resolved
        RuntimeBinding record (or None) so callers with a lineage_write_store
        (LIN-003) can admit it without a second authoritative lookup.
        """
        event_type = event.get("event_type")
        if event_type in TRADE_JOURNAL_EVENT_TYPES:
            return True, None, None
        if event_type == TRADE_JOURNEY_FIXTURE_EVENT_TYPE:
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            envelope = (
                event.get("correlation_envelope")
                if isinstance(event.get("correlation_envelope"), dict)
                else {}
            )
            fixture_enabled = (
                os.getenv("PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED", "").lower()
                == "true"
            )
            if not fixture_enabled:
                return False, "Trade Journey dev fixture ingest is disabled", None
            if (
                metadata.get("fixture_schema_version")
                != TRADE_JOURNEY_FIXTURE_SCHEMA_VERSION
                or metadata.get("fixture_source") != TRADE_JOURNEY_FIXTURE_SOURCE
                or metadata.get("fixture_scope") != "dev-only"
                or envelope.get("tenant_id") != TRADE_JOURNEY_FIXTURE_TENANT
            ):
                return False, "Trade Journey dev fixture scope is invalid", None

        # E-1: Minimal binding identity (field presence)
        binding_id = event.get("binding_id")
        if not binding_id:
            return False, "Missing binding_id (Evidence E-1)", None

        required_identity = ["runtime_id", "capital_pool_id", "artifact_id", "artifact_version"]
        missing = [f for f in required_identity if not event.get(f)]
        if missing:
            return False, f"Missing binding identity fields: {missing} (Evidence E-1)", None

        # E-2: Deployment stage and execution mode proof (field presence + enum)
        deployment_stage = event.get("deployment_stage")
        if not deployment_stage or deployment_stage not in ("paper", "canary", "live", "frozen"):
            return False, f"Invalid deployment_stage: {deployment_stage} (Evidence E-2)", None
        execution_mode = event.get("execution_mode")
        if not execution_mode or execution_mode not in ("paper", "canary", "live", "frozen"):
            return False, f"Invalid execution_mode: {execution_mode} (Evidence E-2)", None
        if execution_mode != deployment_stage:
            return False, (
                f"execution_mode/deployment_stage mismatch: execution_mode {execution_mode!r} must match deployment_stage "
                f"{deployment_stage!r} (Evidence E-2)"
            ), None

        # E-3: Governance admissibility
        if not event.get("plan_id") or not event.get("persona_capital_binding_id"):
            return False, "Missing governance admissibility fields (Evidence E-3)", None

        # E-5: Rollback lineage consistency
        rollback_parent = event.get("rollback_parent")
        rollback_action_type = event.get("rollback_action_type")
        if (rollback_parent is not None) != (rollback_action_type is not None):
            return False, "rollback_parent and rollback_action_type must both be set or both absent (Evidence E-5)", None

        # --- RuntimeBinding authoritative cross-validation (requires binding_store) ---
        if self._binding_store is not None:
            binding = self._binding_store.get_binding(binding_id)
            if binding is None:
                return False, (
                    f"binding_id {binding_id!r} not found in RuntimeBinding store — "
                    f"event cannot be attributed to an authoritative binding (Evidence E-1)"
                ), None

            # E-1 cross-check: all identity fields must match the canonical binding
            identity_fields = (
                "runtime_id",
                "capital_pool_id",
                "artifact_id",
                "artifact_version",
                "plan_id",
                "persona_capital_binding_id",
            )
            mismatches = []
            for field_name in identity_fields:
                event_val = event.get(field_name)
                binding_val = getattr(binding, field_name, None)
                if event_val != binding_val:
                    mismatches.append(
                        f"{field_name}: event={event_val!r} binding={binding_val!r}"
                    )
            if mismatches:
                return False, (
                    f"RuntimeBinding identity mismatch for {binding_id!r}: "
                    f"{'; '.join(mismatches)} (Evidence E-1)"
                ), None

            # E-2 cross-check: deployment_stage must equal binding.deployment_mode
            binding_mode = getattr(binding, "deployment_mode", None)
            if deployment_stage != binding_mode:
                return False, (
                    f"deployment_stage {deployment_stage!r} does not match binding "
                    f"deployment_mode {binding_mode!r} (Evidence E-2)"
                ), None
            binding_execution_mode = getattr(binding, "execution_mode", None) or binding_mode
            if execution_mode != binding_execution_mode:
                return False, (
                    f"execution_mode {execution_mode!r} does not match binding "
                    f"execution_mode {binding_execution_mode!r} (Evidence E-2)"
                ), None

            # E-4: Temporal window — event.created_at must fall within
            # [binding.effective_at, binding.retired_at]
            event_ts = event.get("created_at")
            effective_at = getattr(binding, "effective_at", None)
            retired_at = getattr(binding, "retired_at", None)

            if event_ts and effective_at and event_ts < effective_at:
                return False, (
                    f"Event created_at {event_ts!r} precedes binding effective_at "
                    f"{effective_at!r} — temporal violation (Evidence E-4)"
                ), None
            if event_ts and retired_at and event_ts > retired_at:
                return False, (
                    f"Event created_at {event_ts!r} is after binding retired_at "
                    f"{retired_at!r} — temporal violation (Evidence E-4)"
                ), None

            return True, None, binding

        return True, None, None

    async def ingest(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        """
        Ingest a single telemetry event.

        Flow:
        0. Idempotent deduplication by event_id
        1. Schema validation
        2. Evidence contract validation (TEL-001A E-1 through E-6)
           — includes RuntimeBinding authoritative lookup when binding_store is set
        3. Push to durable buffer
        4. Backpressure: if buffer full, overflow to DLQ

        Parameters
        ----------
        event : dict
            Telemetry event envelope.
        timeout : float, optional
            Timeout for buffer put operation.

        Returns
        -------
        bool
            True if event was enqueued successfully (or was a known duplicate).
        """
        # 0. Idempotent deduplication — prevent the same event_id from being
        #    counted or written more than once within this service instance.
        event_id = event.get("event_id")
        if event_id and event_id in self._seen_event_ids:
            # 0.a. Validate schema on duplicate retry
            valid_schema, schema_err = self._validate_event(event)
            if not valid_schema:
                self._total_rejected += 1
                self._dlq.reject(
                    event=event,
                    tags=[TAG_SCHEMA_VIOLATION],
                    reason=f"Schema validation failed on duplicate retry: {schema_err}",
                )
                log.warning(f"Ingest duplicate retry rejected (schema): {schema_err}")
                return False

            # 0.b. Validate evidence contract on duplicate retry
            valid_ev, ev_err, resolved_binding = self._validate_evidence_contract(event)
            if not valid_ev:
                err_lower = ev_err.lower()
                if "temporal" in err_lower or "effective_at" in err_lower or "retired_at" in err_lower:
                    tag = TAG_TEMPORAL_VIOLATION
                elif "binding" in err_lower or "mismatch" in err_lower or "not found" in err_lower:
                    tag = TAG_BINDING_MISMATCH
                else:
                    tag = TAG_SCHEMA_VIOLATION

                self._total_rejected += 1
                self._dlq.reject(
                    event=event,
                    tags=[tag],
                    reason=f"Evidence contract violation on duplicate retry: {ev_err}",
                )
                log.warning(f"Ingest duplicate retry rejected (evidence): {ev_err}")
                return False

            # 0.c. Reject same-ID content mismatch
            original_event = self._seen_event_ids[event_id]
            mismatch = False
            for k in (set(event.keys()) | set(original_event.keys())):
                if event.get(k) != original_event.get(k):
                    mismatch = True
                    break
            if mismatch:
                self._total_rejected += 1
                self._dlq.reject(
                    event=event,
                    tags=[TAG_SCHEMA_VIOLATION],
                    reason=f"Content mismatch for duplicate event_id={event_id}",
                )
                log.warning(f"Ingest duplicate retry rejected (content mismatch for event_id={event_id})")
                return False

            self._total_duplicates += 1
            log.debug(f"Ingest skipped (duplicate event_id): {event_id}")
            if self._lineage_write_store is not None:
                try:
                    # Lineage repair must use the immutable originally accepted payload
                    self._lineage_write_store.admit_telemetry_event(original_event, resolved_binding)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Lineage live-write admission failed for duplicate event %s: %s", event_id, exc)
            return True  # idempotent: already delivered, treat as success

        # 1. Schema validation
        valid, error_msg = self._validate_event(event)
        if not valid:
            self._total_rejected += 1
            self._dlq.reject(
                event=event,
                tags=[TAG_SCHEMA_VIOLATION],
                reason=f"Schema validation failed: {error_msg}",
            )
            log.warning(f"Ingest rejected (schema): {error_msg}")
            return False

        # 2. Evidence contract validation (field presence + RuntimeBinding cross-check)
        valid, error_msg, resolved_binding = self._validate_evidence_contract(event)
        if not valid:
            # Classify the tag based on the error type
            err_lower = error_msg.lower()
            if "temporal" in err_lower or "effective_at" in err_lower or "retired_at" in err_lower:
                tag = TAG_TEMPORAL_VIOLATION
            elif "binding" in err_lower or "mismatch" in err_lower or "not found" in err_lower:
                tag = TAG_BINDING_MISMATCH
            else:
                tag = TAG_SCHEMA_VIOLATION

            self._total_rejected += 1
            self._dlq.reject(
                event=event,
                tags=[tag],
                reason=f"Evidence contract violation: {error_msg}",
            )
            log.warning(f"Ingest rejected (evidence): {error_msg}")
            return False

        # 3. Push to durable buffer
        enqueued = await self._buffer.put(event, timeout=timeout)
        if not enqueued:
            self._total_rejected += 1
            self._dlq.reject(
                event=event,
                tags=[TAG_BUFFER_OVERFLOW],
                reason="Buffer full — backpressure overflow to DLQ",
            )
            log.warning("Ingest rejected (buffer overflow): buffer at capacity")
            return False

        # Track event_id for idempotent dedup after successful enqueue
        if event_id:
            # Keep an immutable snapshot so producer-side mutation cannot turn
            # a conflicting retry into an apparent exact duplicate.
            self._seen_event_ids[event_id] = copy.deepcopy(event)
            # Evict oldest half when the dedup set exceeds its size limit
            if len(self._seen_event_ids) > self._dedup_max_size:
                evict = list(self._seen_event_ids.keys())[: self._dedup_max_size // 2]
                for eid in evict:
                    self._seen_event_ids.pop(eid, None)

        self._total_ingested += 1
        if self._runtime_summary_store is not None:
            try:
                self._runtime_summary_store.project_event(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("Runtime summary projection failed for event %s: %s", event_id, exc)

        if self._lineage_write_store is not None:
            try:
                self._lineage_write_store.admit_telemetry_event(event, resolved_binding)
            except Exception as exc:  # noqa: BLE001
                log.warning("Lineage live-write admission failed for event %s: %s", event_id, exc)

        if self._trade_episode_projection_store is not None:
            has_episode = (
                event.get("trade_episode_id")
                or event.get("payload", {}).get("trade_episode_id")
                or event.get("metadata", {}).get("trade_episode_id")
            )
            if has_episode:
                try:
                    self._trade_episode_projection_store.project_event(event)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Trade episode projection failed for event %s: %s", event_id, exc)
        return True

    async def ingest_batch(self, events: list[dict[str, Any]]) -> dict[str, int]:
        """
        Ingest a batch of events.

        Returns dict with keys: ingested, rejected
        """
        ingested = 0
        rejected = 0
        for event in events:
            if await self.ingest(event):
                ingested += 1
            else:
                rejected += 1
        return {"ingested": ingested, "rejected": rejected}

    # -- Non-trading infrastructure health admission --

    @staticmethod
    def _forbidden_binding_fields(value: Any) -> list[str]:
        """Return every RuntimeBinding evidence key present at any depth.

        "At any depth" is literal. The traversal is iterative over an explicit
        stack, so it has no depth ceiling of its own and cannot exhaust the
        interpreter stack on a deeply nested payload. An earlier revision
        recursed and gave up past depth 8 by returning no findings, which made
        the contract false in the one direction that matters: a ``binding_id``
        nested deeper than the cap was silently admitted rather than rejected.
        A scan that cannot see the whole payload must never answer "clean".

        Containers are tracked by identity so a payload that reuses or
        self-references an object terminates instead of looping. Re-visiting is
        skipped only after that object's keys have already been collected, so
        the returned key set is unaffected.
        """
        found: list[str] = []
        visited: set[int] = set()
        stack: list[Any] = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, Mapping):
                if id(current) in visited:
                    continue
                visited.add(id(current))
                for key, item in current.items():
                    if str(key) in RUNTIME_BINDING_EVIDENCE_FIELDS:
                        found.append(str(key))
                    stack.append(item)
            elif isinstance(current, (list, tuple, set, frozenset)):
                if id(current) in visited:
                    continue
                visited.add(id(current))
                stack.extend(current)
        return found

    def _buffer_durability_defect(self) -> Optional[str]:
        """Return why the configured buffer cannot back an admission, or None.

        Infrastructure health admission is authoritative: a 202 plus a committed
        ledger receipt is a promise that the event survives this process. A
        volatile buffer cannot keep that promise — a crash erases the only copy
        of the event while the committed receipt makes every later producer
        retry an idempotent ``duplicate``, so the observation is permanently
        lost with no error anywhere. The buffer's own ``is_durable()`` is the
        single authority here; there is no configuration, environment variable,
        or event field that can assert durability on a backend's behalf.
        """

        buffer = self._buffer
        is_durable = getattr(buffer, "is_durable", None)
        if not callable(is_durable):
            return (
                f"Configured telemetry buffer {type(buffer).__name__!r} does not "
                "implement the durable broker contract"
            )
        try:
            durable = bool(is_durable())
        except Exception as exc:  # noqa: BLE001 - an unprovable buffer is not durable
            return (
                f"Configured telemetry buffer {type(buffer).__name__!r} could not "
                f"prove durability: {exc}"
            )
        if not durable:
            return (
                f"Configured telemetry buffer {type(buffer).__name__!r} is volatile; "
                "infrastructure health admission requires a durable broker so an "
                "admitted event survives process crash, restart, and replica failover"
            )
        return None

    def _reject_infrastructure_health(
        self,
        event: dict[str, Any],
        code: str,
        message: str,
        *,
        tag: str = TAG_SCHEMA_VIOLATION,
        dead_letter: bool = True,
    ) -> dict[str, Any]:
        self._infrastructure_health_rejected += 1
        if dead_letter:
            self._dlq.reject(
                event=event,
                tags=[tag],
                reason=f"{code}: {message}",
            )
        log.warning("Infrastructure health ingest rejected (%s): %s", code, message)
        return {
            "status": "rejected",
            "code": code,
            "message": message,
            "event_id": str(event.get("event_id") or ""),
        }

    async def ingest_infrastructure_health(
        self,
        event: dict[str, Any],
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Admit one non-trading infrastructure health event.

        The caller must already have been authenticated and tenant/producer
        bound by the infrastructure health authority in ``services.telemetry.auth``.

        Flow
        ----
        1. Reject anything that is not an infrastructure_health event.
        2. Reject any RuntimeBinding evidence field at any depth — an
           infrastructure producer must never be able to present, invent, or
           spoof trading binding identity.
        3. Validate against the authoritative non-trading schema. Fail closed
           when that schema or its validator is unavailable.
        4. Fail closed *before* any reservation when the configured buffer is
           not a durable broker, so no volatile enqueue can ever be recorded as
           an admitted event.
        5. Take a leased, fenced reservation on the stable event_id in the
           durable admission ledger. A live reservation held by another attempt
           is answered as retryable, never as success.
        6. Enqueue for durable persistence, re-check durability, then commit the
           reservation. A failed enqueue releases it so the producer's retry
           still works.

        Only a committed reservation — one backed by a durable enqueue receipt —
        makes a later retry an idempotent ``duplicate``.

        Returns a result dict with ``status`` of ``accepted``, ``duplicate``, or
        ``rejected``; rejections carry a stable ``code``.
        """
        if event.get("event_type") != INFRASTRUCTURE_HEALTH_EVENT_TYPE:
            return self._reject_infrastructure_health(
                event,
                "INFRA_EVENT_TYPE_INVALID",
                f"event_type must be {INFRASTRUCTURE_HEALTH_EVENT_TYPE!r}",
            )

        forbidden = list(dict.fromkeys(self._forbidden_binding_fields(event)))
        if forbidden:
            return self._reject_infrastructure_health(
                event,
                "INFRA_BINDING_FIELD_FORBIDDEN",
                (
                    "infrastructure health telemetry must not carry RuntimeBinding "
                    f"evidence fields: {forbidden}"
                ),
                tag=TAG_BINDING_MISMATCH,
            )

        if self._infrastructure_health_schema is None or jsonschema is None:
            return self._reject_infrastructure_health(
                event,
                "INFRA_SCHEMA_UNAVAILABLE",
                "Authoritative infrastructure health schema is not loaded",
                dead_letter=False,
            )
        try:
            jsonschema.validate(
                instance=event,
                schema=self._infrastructure_health_schema,
            )
        except jsonschema.ValidationError as exc:
            return self._reject_infrastructure_health(
                event,
                "INFRA_SCHEMA_VIOLATION",
                exc.message,
            )
        except jsonschema.SchemaError as exc:
            return self._reject_infrastructure_health(
                event,
                "INFRA_SCHEMA_UNAVAILABLE",
                f"Schema error: {exc.message}",
                dead_letter=False,
            )

        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            return self._reject_infrastructure_health(
                event,
                "INFRA_EVENT_ID_REQUIRED",
                "infrastructure health telemetry requires a stable event_id",
            )

        # Durability is checked before the reservation, not after the enqueue.
        # A reservation taken against a volatile buffer could still be committed
        # by this attempt, and a committed receipt is irreversible: it turns
        # every later retry of a lost event into a successful duplicate.
        durability_defect = self._buffer_durability_defect()
        if durability_defect is not None:
            self._infrastructure_health_non_durable += 1
            return self._reject_infrastructure_health(
                event,
                "INFRA_BUFFER_NOT_DURABLE",
                durability_defect,
                dead_letter=False,
            )

        ledger = self._infrastructure_health_ledger
        if ledger is None:
            return self._reject_infrastructure_health(
                event,
                "INFRA_LEDGER_UNCONFIGURED",
                (
                    "Durable infrastructure health admission ledger is unavailable; "
                    "refusing to admit an event that cannot be deduplicated durably"
                ),
                dead_letter=False,
            )

        fingerprint = infrastructure_health_fingerprint(event)
        component = event.get("component") if isinstance(event.get("component"), dict) else {}
        reservation = ledger.begin(
            event_id,
            fingerprint,
            attributes={
                "tenant_id": event.get("tenant_id"),
                "producer": event.get("producer"),
                "service_name": component.get("service_name"),
                "created_at": event.get("created_at"),
            },
        )
        if reservation.outcome == ledger.OUTCOME_CONFLICT:
            self._infrastructure_health_conflicts += 1
            return self._reject_infrastructure_health(
                event,
                "INFRA_EVENT_ID_CONFLICT",
                f"event_id {event_id!r} is already bound to different content",
            )
        if reservation.outcome == ledger.OUTCOME_COMMITTED:
            # A durable receipt already exists for identical content, so this is
            # a true idempotent duplicate rather than an optimistic success.
            self._infrastructure_health_duplicates += 1
            log.debug("Infrastructure health admission skipped (duplicate): %s", event_id)
            return {
                "status": "duplicate",
                "event_id": event_id,
                "fingerprint": fingerprint,
            }
        if reservation.outcome == ledger.OUTCOME_IN_FLIGHT:
            # Another attempt holds a live reservation and nothing is durable
            # yet. Answering "accepted" here would be a false success that stops
            # the producer from retrying an event that may never be persisted.
            self._infrastructure_health_in_flight += 1
            return self._reject_infrastructure_health(
                event,
                "INFRA_ADMISSION_IN_FLIGHT",
                (
                    f"event_id {event_id!r} is being admitted by another attempt and has no "
                    "durable receipt yet; retry"
                ),
                dead_letter=False,
            )

        token = reservation.token or ""
        try:
            enqueued = await self._buffer.put(event, timeout=timeout)
        except BaseException:
            # Never strand the reservation on cancellation or a buffer error.
            ledger.release(event_id, token)
            raise
        if not enqueued:
            # The reservation must not outlive a failed enqueue, otherwise the
            # producer's retry would be answered as an already-admitted event
            # that was never persisted.
            ledger.release(event_id, token)
            return self._reject_infrastructure_health(
                event,
                "INFRA_BUFFER_OVERFLOW",
                "Buffer full — infrastructure health event was not durably enqueued",
                tag=TAG_BUFFER_OVERFLOW,
            )

        # Re-prove durability before the commit makes the receipt permanent. A
        # backend that degraded to a volatile path during this enqueue must not
        # be able to convert an unsafe write into an idempotent success; release
        # the claim so the producer's retry is still admissible.
        durability_defect = self._buffer_durability_defect()
        if durability_defect is not None:
            ledger.release(event_id, token)
            self._infrastructure_health_non_durable += 1
            return self._reject_infrastructure_health(
                event,
                "INFRA_BUFFER_NOT_DURABLE",
                durability_defect,
                dead_letter=False,
            )

        commit_outcome = ledger.commit(event_id, fingerprint, token)
        if commit_outcome == ledger.OUTCOME_CONFLICT:
            self._infrastructure_health_conflicts += 1
            return self._reject_infrastructure_health(
                event,
                "INFRA_EVENT_ID_CONFLICT",
                f"event_id {event_id!r} is already bound to different content",
            )
        if commit_outcome != ledger.OUTCOME_COMMITTED:
            # This attempt's lease expired and another caller took the claim over.
            # The event is durably enqueued, but this attempt cannot own the
            # admission record, so report a retryable failure rather than a
            # success it cannot prove.
            self._infrastructure_health_fenced += 1
            return self._reject_infrastructure_health(
                event,
                "INFRA_ADMISSION_FENCED",
                (
                    f"reservation for event_id {event_id!r} expired and was taken over by "
                    "another admission attempt; retry"
                ),
                dead_letter=False,
            )

        self._seen_event_ids[event_id] = copy.deepcopy(event)
        if len(self._seen_event_ids) > self._dedup_max_size:
            evict = list(self._seen_event_ids.keys())[: self._dedup_max_size // 2]
            for eid in evict:
                self._seen_event_ids.pop(eid, None)

        self._infrastructure_health_admitted += 1
        return {
            "status": "accepted",
            "event_id": event_id,
            "fingerprint": fingerprint,
        }

    def infrastructure_health_stats(self) -> dict[str, Any]:
        ledger = self._infrastructure_health_ledger
        durability_defect = self._buffer_durability_defect()
        return {
            "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
            "schema_loaded": self._infrastructure_health_schema is not None,
            "buffer_type": type(self._buffer).__name__,
            "buffer_durable": durability_defect is None,
            "buffer_durability_defect": durability_defect,
            "admitted": self._infrastructure_health_admitted,
            "duplicates": self._infrastructure_health_duplicates,
            "conflicts": self._infrastructure_health_conflicts,
            "in_flight_rejections": self._infrastructure_health_in_flight,
            "fenced_rejections": self._infrastructure_health_fenced,
            "non_durable_rejections": self._infrastructure_health_non_durable,
            "rejected": self._infrastructure_health_rejected,
            "ledger": ledger.stats() if ledger is not None else {
                "path": None,
                "committed_event_ids": 0,
                "open_reservations": 0,
                "recoverable_expired_reservations": 0,
                "lease_seconds": None,
            },
        }

    async def start(self) -> None:
        """Start the ingest service (buffer + batch writer)."""
        if self._started:
            return
        self._load_dlq_from_spill_once()
        # Fail startup closed when a configured durable backend cannot prove
        # its stream/consumer safety. HTTP must never acknowledge into a
        # process-local fallback.
        await self._buffer.start()
        self._started = True
        self._start_time = time.monotonic()
        await self._writer.start()
        if self._replay_dlq_on_start:
            self._startup_dlq_replay_count = await self.replay_dlq(
                tag_filter=self._dlq_replay_tag_filter
            )
            log.info(
                "TelemetryIngestService startup DLQ replay complete: replayed=%s",
                self._startup_dlq_replay_count,
            )
        log.info("TelemetryIngestService started")

    def _load_dlq_from_spill_once(self) -> int:
        """Load persisted DLQ spill entries once per service instance."""
        if self._dlq_loaded_from_spill:
            return 0
        self._dlq_loaded_from_spill = True
        self._dlq_loaded_from_spill_count = self._dlq.load_from_spill()
        if self._dlq_loaded_from_spill_count:
            log.info(
                "TelemetryIngestService loaded %s DLQ entries from spill",
                self._dlq_loaded_from_spill_count,
            )
        return self._dlq_loaded_from_spill_count

    async def stop(self, graceful: bool = True) -> None:
        """Stop the ingest service."""
        await self._writer.stop(graceful=graceful)
        await self._buffer.close()
        self._started = False
        log.info(
            f"TelemetryIngestService stopped. "
            f"ingested={self._total_ingested}, rejected={self._total_rejected}, "
            f"duplicates={self._total_duplicates}"
        )

    @staticmethod
    async def _default_write_fn(batch: list[dict[str, Any]]) -> WriteResult:
        """
        Default write function — memory-only sink for development and testing.

        WARNING: This is a no-op sink. In production, replace with
        build_postgres_write_fn(dsn=...) to persist events to the canonical
        Postgres telemetry store with ON CONFLICT (event_id) DO NOTHING for
        idempotent inserts.
        """
        return WriteResult.ok(len(batch))

    def stats(self) -> dict[str, Any]:
        """Return comprehensive service statistics."""
        uptime = time.monotonic() - self._start_time if self._start_time else 0.0
        return {
            "service": {
                "started": self._started,
                "uptime_seconds": round(uptime, 2),
                "total_ingested": self._total_ingested,
                "total_rejected": self._total_rejected,
                "total_duplicates": self._total_duplicates,
                "dedup_tracked_ids": len(self._seen_event_ids),
            },
            "buffer": self._buffer.stats() if hasattr(self._buffer, "stats") else {
                "size": self._buffer.size(),
                "capacity": self._buffer.capacity(),
            },
            "writer": self._writer.stats(),
            "dead_letter_queue": self._dlq.stats(),
            "backpressure": self._backpressure.stats(),
            "runtime_summary_projection": (
                self._runtime_summary_store.stats()
                if self._runtime_summary_store is not None
                else {"summary_count": 0, "path": None}
            ),
            "trade_episode_projection": (
                self._trade_episode_projection_store.stats()
                if self._trade_episode_projection_store is not None
                else {"projection_count": 0, "projections_path": None}
            ),
            "infrastructure_health": self.infrastructure_health_stats(),
            "startup": {
                "dlq_loaded_from_spill": self._dlq_loaded_from_spill_count,
                "dlq_replay_on_start": self._replay_dlq_on_start,
                "dlq_replay_tag_filter": self._dlq_replay_tag_filter,
                "dlq_replayed_on_start": self._startup_dlq_replay_count,
            },
        }

    @staticmethod
    def _event_tenant_id(event: dict[str, Any]) -> str:
        top_level = str(event.get("tenant_id") or "").strip()
        envelope = event.get("correlation_envelope")
        envelope_tenant = (
            str(envelope.get("tenant_id") or "").strip()
            if isinstance(envelope, dict)
            else ""
        )
        if top_level and envelope_tenant and top_level != envelope_tenant:
            return ""
        return top_level or envelope_tenant

    def get_runtime_summary(
        self,
        runtime_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._runtime_summary_store is None:
            return None
        return self._runtime_summary_store.get(runtime_id, tenant_id=tenant_id)

    def list_runtime_summaries(
        self,
        *,
        tenant_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        if self._runtime_summary_store is None:
            return []
        return self._runtime_summary_store.list(tenant_id=tenant_id)

    def get_accepted_event(
        self,
        event_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Return the immutable event accepted by this owner process.

        The exact event lookup is deliberately separate from runtime summaries:
        summaries are continuously updated by concurrent heartbeats and are not
        a stable acknowledgement surface for one producer event.
        """

        clean_event_id = str(event_id or "").strip()
        if not clean_event_id:
            return None
        event = self._seen_event_ids.get(clean_event_id)
        if (
            event is not None
            and tenant_id is not None
            and self._event_tenant_id(event) != tenant_id
        ):
            return None
        return copy.deepcopy(event) if event is not None else None

    def get_trade_episode_projection(
        self,
        trade_episode_id: str,
        *,
        as_of: Optional[str] = None,
        as_of_sequence: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._trade_episode_projection_store is None:
            return None
        return self._trade_episode_projection_store.get(
            trade_episode_id,
            as_of=as_of,
            as_of_sequence=as_of_sequence,
            tenant_id=tenant_id,
        )

    def list_trade_episode_projections(
        self,
        *,
        persona_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 20,
        environment: Optional[str] = None,
        strategy_id: Optional[str] = None,
        instrument_id: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
        outcome: Optional[str] = None,
        reflection_state: Optional[str] = None,
        coverage_state: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if self._trade_episode_projection_store is None:
            return {"projections": [], "next_cursor": None, "count": 0}
        return self._trade_episode_projection_store.list(
            persona_id=persona_id,
            cursor=cursor,
            limit=limit,
            environment=environment,
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            side=side,
            status=status,
            outcome=outcome,
            reflection_state=reflection_state,
            coverage_state=coverage_state,
            start_time=start_time,
            end_time=end_time,
            tenant_id=tenant_id,
        )

    def has_runtime_binding_store(self) -> bool:
        return self._binding_store is not None

    def resolve_runtime_binding(self, binding_id: str) -> Optional[Any]:
        if self._binding_store is None:
            return None
        return self._binding_store.get_binding(binding_id)

    # -- Diagnostics / Replay --

    def get_dlq_entries(
        self,
        tag_filter: Optional[str] = None,
        limit: int = 100,
        *,
        tenant_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get dead-letter queue entries."""
        entries = self._dlq.get_entries_as_dicts(
            tag_filter=tag_filter,
            limit=max(limit, 10_000) if tenant_id is not None else limit,
        )
        if tenant_id is not None:
            entries = [
                entry
                for entry in entries
                if isinstance(entry.get("event"), dict)
                and self._event_tenant_id(entry["event"]) == tenant_id
            ]
        return entries[-limit:]

    @staticmethod
    def _deduplicate_replay_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return replay candidates once per event_id while preserving order."""
        seen: set[str] = set()
        deduplicated: list[dict[str, Any]] = []
        for event in events:
            eid = event.get("event_id")
            if eid:
                if eid in seen:
                    continue
                seen.add(eid)
            deduplicated.append(event)
        return deduplicated

    async def replay_dlq(
        self,
        tag_filter: Optional[str] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> int:
        """
        Replay dead-letter events through the full ingest validation path.

        Replay policy
        -------------
        By default (tag_filter=None), only write-failure entries are replayed
        (TAG_WRITER_ERROR, TAG_RETRY_EXHAUSTED).  These are events that passed
        validation but could not be persisted due to a transient storage error.

        Validation failures (TAG_SCHEMA_VIOLATION, TAG_BINDING_MISMATCH,
        TAG_TEMPORAL_VIOLATION) are NEVER replayed automatically, because
        forwarding a binding-invalid event into the canonical write path would
        violate AC-1 (canonical stage and binding-reference guarantee).

        When tag_filter is provided explicitly, only entries with that tag are
        replayed, and they still pass through the full ingest() validation path.
        Operators who wish to force-replay a rejected event must correct the
        event data externally before manually re-ingesting it.

        All replay re-enters ingest() so schema and evidence are re-validated.
        Events that fail re-validation are re-routed to the DLQ under the
        appropriate tag rather than silently dropped.

        Parameters
        ----------
        tag_filter : str, optional
            If provided, replay only entries with this specific tag.
            If None, replay all write-failure-tagged entries (safe default).

        Returns
        -------
        int
            Number of events successfully re-enqueued.
        """
        if tag_filter is not None:
            events = self._deduplicate_replay_events(
                self._dlq.replay_entries(tag_filter=tag_filter)
            )
        else:
            # Collect write-failure entries only.
            replay_candidates: list[dict[str, Any]] = []
            for tag in _WRITE_FAILURE_TAGS:
                replay_candidates.extend(self._dlq.replay_entries(tag_filter=tag))
            events = self._deduplicate_replay_events(replay_candidates)
        if tenant_id is not None:
            events = [
                event
                for event in events
                if self._event_tenant_id(event) == tenant_id
            ]

        count = 0
        for event in events:
            # Re-enter the full ingest path — re-validates schema + evidence.
            # Clear the event_id from the dedup set so replay can re-enqueue it.
            eid = event.get("event_id")
            if eid:
                self._seen_event_ids.pop(eid, None)
            ok = await self.ingest(event, timeout=5.0)
            if ok:
                count += 1
        log.info(f"Replayed {count}/{len(events)} DLQ write-failure events through ingest path")
        return count
