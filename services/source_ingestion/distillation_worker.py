"""Source-to-strategy distillation worker.

Listens for normalized SourceRecord events and enqueues idempotent distillation
jobs that produce or refresh StrategySpecSeed drafts.

Pipeline:
    SourceRecord (normalized) -> DistillationJob (pending)
        -> EvidenceBundle (synthesized) -> StrategySpecSeed (draft)

Invariants:
    - Only DRAFT and NEEDS_MORE_EVIDENCE seeds are ever refreshed.
    - Seeds in accepted, promoted, rejected, or other terminal states are
      skipped; the job is marked SKIPPED without touching the seed.
    - All paths (initial distill, catch-up, manual redispatch) are idempotent:
      re-running with the same source_id produces the same outcome.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.seed_materializer import (
    MaterializationMode,
    SeedMaterializationError,
    SeedMaterializationService,
)
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedStatus
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


# Statuses where re-distillation is allowed (seed is still a mutable draft).
_MUTABLE_SEED_STATUSES: frozenset[str] = frozenset(
    {
        StrategySpecSeedStatus.DRAFT.value,
        StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
    }
)

# Statuses that block re-distillation (seed has advanced past draft).
_IMMUTABLE_SEED_STATUSES: frozenset[str] = frozenset(
    {
        StrategySpecSeedStatus.ACCEPTED.value,
        StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value,
        StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT.value,
        StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
        StrategySpecSeedStatus.MERGED.value,
        StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        StrategySpecSeedStatus.REJECTED.value,
    }
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def source_version_identity(source: SourceRecord) -> dict[str, Any]:
    """Return the content projection that defines one normalized source version.

    Run-local correlation fields are deliberately excluded so that re-ingesting
    the same normalized content in a later run maps to the same version, while
    a revised title, reference, body, content hash, or governed metadata
    creates a distinct event and distillation job.
    """

    metadata = dict(source.metadata)
    for volatile_key in ("ingest_run_id", "source_ingest_run_id", "trace_id"):
        metadata.pop(volatile_key, None)
    return {
        "source_id": source.source_id,
        "connector_id": source.connector_id,
        "source_type": source.source_type.value,
        "title": source.title,
        "content_ref": source.content_ref,
        "metadata": metadata,
    }


def source_version_digest(source: SourceRecord) -> str:
    """Return the stable content identity digest for one source version."""

    identity = _canonical_json(source_version_identity(source))
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _same_version_identity(
    *,
    stored_payload_json: str,
    incoming_payload_json: str,
    versioned: bool,
) -> bool:
    """Return True when two committed snapshots describe the same version.

    Legacy (non-versioned) rows carry no content identity, so they must match
    byte for byte. Versioned rows compare on the digest projection instead, so
    a re-ingest that only changed ``ingest_run_id`` or ``trace_id`` resolves to
    the version already committed rather than failing admission.
    """

    if stored_payload_json == incoming_payload_json:
        return True
    if not versioned:
        return False
    try:
        stored = SourceRecord.from_dict(json.loads(stored_payload_json))
        incoming = SourceRecord.from_dict(json.loads(incoming_payload_json))
    except Exception:
        return False
    return _canonical_json(source_version_identity(stored)) == _canonical_json(
        source_version_identity(incoming)
    )


def _stable_job_id(source_id: str, source_digest: str | None = None) -> str:
    identity = source_id if not source_digest else f"{source_id}:{source_digest}"
    length = 16 if not source_digest else 24
    return "distill-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:length]


def _stable_bundle_id(source_id: str, source_digest: str | None = None) -> str:
    identity = source_id if not source_digest else f"{source_id}:{source_digest}"
    return "evbundle-distill-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _stable_item_id(source_id: str, source_digest: str | None = None) -> str:
    identity = source_id if not source_digest else f"{source_id}:{source_digest}"
    return "evi-distill-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _timestamp(value: str | datetime | float | None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _timestamp_text(value: float | None) -> str | None:
    if value is None:
        return None
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class DistillationJobStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    DONE = "done"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    SKIPPED = "skipped"


class DistillationError(ValueError):
    """Raised when a distillation invariant is violated."""


@dataclass(frozen=True)
class DistillationJob:
    """Persisted record of one source-to-strategy distillation job."""

    job_id: str
    source_id: str
    idempotency_key: str
    status: DistillationJobStatus | str
    enqueued_at: str
    processed_at: str | None = None
    seed_id: str | None = None
    error: str | None = None
    skip_reason: str | None = None
    attempts: int = 0
    source_digest: str = ""
    event_version: str = "source_record.normalized.v1"
    registry_id: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_epoch: int = 0
    lease_expires_at: str | None = None
    next_attempt_at: str | None = None
    max_attempts: int = 3

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value if isinstance(self.status, DistillationJobStatus) else str(self.status)
        return {
            "job_id": self.job_id,
            "source_id": self.source_id,
            "idempotency_key": self.idempotency_key,
            "status": status,
            "enqueued_at": self.enqueued_at,
            "processed_at": self.processed_at,
            "seed_id": self.seed_id,
            "error": self.error,
            "skip_reason": self.skip_reason,
            "attempts": self.attempts,
            "source_digest": self.source_digest,
            "event_version": self.event_version,
            "registry_id": self.registry_id,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_epoch": self.lease_epoch,
            "lease_expires_at": self.lease_expires_at,
            "next_attempt_at": self.next_attempt_at,
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DistillationJob":
        raw_status = str(data.get("status") or DistillationJobStatus.PENDING.value)
        try:
            status = DistillationJobStatus(raw_status)
        except ValueError:
            status = raw_status  # type: ignore[assignment]
        return cls(
            job_id=str(data["job_id"]),
            source_id=str(data["source_id"]),
            idempotency_key=str(data.get("idempotency_key") or ""),
            status=status,
            enqueued_at=str(data.get("enqueued_at") or ""),
            processed_at=data.get("processed_at") or None,
            seed_id=data.get("seed_id") or None,
            error=data.get("error") or None,
            skip_reason=data.get("skip_reason") or None,
            attempts=int(data.get("attempts") or 0),
            source_digest=str(data.get("source_digest") or ""),
            event_version=str(data.get("event_version") or "source_record.normalized.v1"),
            registry_id=data.get("registry_id") or None,
            lease_owner=data.get("lease_owner") or None,
            lease_token=data.get("lease_token") or None,
            lease_epoch=int(data.get("lease_epoch") or 0),
            lease_expires_at=data.get("lease_expires_at") or None,
            next_attempt_at=data.get("next_attempt_at") or None,
            max_attempts=max(1, int(data.get("max_attempts") or 3)),
        )

    @property
    def is_pending(self) -> bool:
        status = self.status.value if isinstance(self.status, DistillationJobStatus) else str(self.status)
        return status == DistillationJobStatus.PENDING.value

    @property
    def is_terminal(self) -> bool:
        status = self.status.value if isinstance(self.status, DistillationJobStatus) else str(self.status)
        return status in (
            DistillationJobStatus.DONE.value,
            DistillationJobStatus.FAILED.value,
            DistillationJobStatus.DEAD_LETTER.value,
            DistillationJobStatus.SKIPPED.value,
        )


@dataclass(frozen=True)
class DistillationRunResult:
    """Summary of a run_pending or catch_up pass."""

    processed: int
    created: int
    refreshed: int
    skipped: int
    failed: int
    enqueued: int = 0
    registry_synced: int = 0
    retried: int = 0
    dead_lettered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "created": self.created,
            "refreshed": self.refreshed,
            "skipped": self.skipped,
            "failed": self.failed,
            "enqueued": self.enqueued,
            "registry_synced": self.registry_synced,
            "retried": self.retried,
            "dead_lettered": self.dead_lettered,
        }


class DistillationJobQueue:
    """Transactional SQLite source-version outbox/inbox and worker ledger.

    A normalized source snapshot and its versioned outbox event are inserted in
    one ``BEGIN IMMEDIATE`` transaction. Claims are fenced by a unique lease
    token. Terminal acknowledgement updates the outbox and inbox together, and
    exhausted failures are copied into the durable dead-letter table.

    The legacy method names remain available because older callers construct a
    queue directly. Their storage semantics are now transactional and
    cross-process safe even when the supplied filename still ends in
    ``.jsonl``.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        now: Callable[[], float] = time.time,
        default_max_attempts: int = 3,
    ) -> None:
        resolved = Path(path) if path else Path(
            os.environ.get(
                "DISTILLATION_JOB_QUEUE_PATH",
                "data/distillation/job_queue.sqlite3",
            )
        )
        # Preserve an existing legacy JSONL file as immutable migration input.
        # New state is written to a SQLite sidecar instead of corrupting it.
        if resolved.exists():
            with resolved.open("rb") as handle:
                if handle.read(16) != b"SQLite format 3\x00":
                    resolved = Path(f"{resolved}.sqlite3")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._path = resolved
        self._now = now
        self._default_max_attempts = max(1, int(default_max_attempts))
        self._bootstrap()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _bootstrap(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS distillation_source_versions (
                    source_id       TEXT NOT NULL,
                    source_digest   TEXT NOT NULL,
                    event_version   TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    committed_at    REAL NOT NULL,
                    PRIMARY KEY (source_id, source_digest)
                );

                CREATE TABLE IF NOT EXISTS distillation_outbox (
                    job_id              TEXT PRIMARY KEY,
                    event_id            TEXT NOT NULL UNIQUE,
                    source_id           TEXT NOT NULL,
                    source_digest       TEXT NOT NULL,
                    event_version       TEXT NOT NULL,
                    idempotency_key     TEXT NOT NULL UNIQUE,
                    status              TEXT NOT NULL,
                    enqueued_at         REAL NOT NULL,
                    processed_at        REAL,
                    available_at        REAL NOT NULL,
                    seed_id             TEXT,
                    registry_id         TEXT,
                    last_error          TEXT,
                    skip_reason         TEXT,
                    attempts            INTEGER NOT NULL DEFAULT 0,
                    max_attempts         INTEGER NOT NULL,
                    lease_owner         TEXT,
                    lease_token         TEXT,
                    lease_epoch         INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at    REAL,
                    updated_at          REAL NOT NULL,
                    CHECK (status IN (
                        'pending', 'leased', 'retry_wait', 'done',
                        'failed', 'dead_letter', 'skipped'
                    )),
                    FOREIGN KEY (source_id, source_digest)
                        REFERENCES distillation_source_versions(source_id, source_digest)
                );

                CREATE INDEX IF NOT EXISTS distillation_outbox_due_idx
                ON distillation_outbox(status, available_at, enqueued_at);

                CREATE TABLE IF NOT EXISTS distillation_inbox (
                    event_id            TEXT PRIMARY KEY,
                    job_id              TEXT NOT NULL UNIQUE,
                    payload_digest      TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    attempts            INTEGER NOT NULL DEFAULT 0,
                    lease_owner         TEXT,
                    lease_token         TEXT,
                    lease_epoch         INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at    REAL,
                    result_ref          TEXT,
                    registry_id         TEXT,
                    last_error          TEXT,
                    applied_at          REAL,
                    updated_at          REAL NOT NULL,
                    CHECK (status IN (
                        'processing', 'retry_wait', 'applied',
                        'failed', 'dead_letter', 'skipped'
                    ))
                );

                CREATE TABLE IF NOT EXISTS distillation_dead_letters (
                    job_id              TEXT PRIMARY KEY,
                    event_id            TEXT NOT NULL,
                    source_id           TEXT NOT NULL,
                    source_digest       TEXT NOT NULL,
                    payload_json        TEXT NOT NULL,
                    attempts            INTEGER NOT NULL,
                    reason              TEXT NOT NULL,
                    created_at          REAL NOT NULL,
                    redrive_count       INTEGER NOT NULL DEFAULT 0,
                    last_redrive_actor  TEXT,
                    last_redrive_note   TEXT,
                    resolved_at         REAL
                );
                """
            )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> DistillationJob:
        return DistillationJob(
            job_id=str(row["job_id"]),
            source_id=str(row["source_id"]),
            source_digest=str(row["source_digest"]),
            event_version=str(row["event_version"]),
            idempotency_key=str(row["idempotency_key"]),
            status=str(row["status"]),
            enqueued_at=_timestamp_text(float(row["enqueued_at"])) or "",
            processed_at=_timestamp_text(row["processed_at"]),
            seed_id=row["seed_id"],
            registry_id=row["registry_id"],
            error=row["last_error"],
            skip_reason=row["skip_reason"],
            attempts=int(row["attempts"]),
            lease_owner=row["lease_owner"],
            lease_token=row["lease_token"],
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at=_timestamp_text(row["lease_expires_at"]),
            next_attempt_at=_timestamp_text(row["available_at"]),
            max_attempts=int(row["max_attempts"]),
        )

    def _select_job(self, connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM distillation_outbox WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    def _insert_source_version_and_job(
        self,
        *,
        source_id: str,
        source_digest: str,
        payload_json: str,
        requested_by: str,
        enqueued_at: float,
        event_version: str,
        versioned: bool,
    ) -> DistillationJob:
        job_id = _stable_job_id(source_id, source_digest if versioned else None)
        event_id = f"source-normalized:{source_id}:{source_digest or job_id}"
        idempotency_key = (
            f"distill:{source_id}:{source_digest}"
            if versioned
            else f"distill:{source_id}:{requested_by}"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO distillation_source_versions (
                        source_id, source_digest, event_version,
                        payload_json, committed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (source_id, source_digest) DO NOTHING
                    """,
                    (
                        source_id,
                        source_digest,
                        event_version,
                        payload_json,
                        enqueued_at,
                    ),
                )
                existing_source = connection.execute(
                    """
                    SELECT payload_json, event_version
                      FROM distillation_source_versions
                     WHERE source_id = ? AND source_digest = ?
                    """,
                    (source_id, source_digest),
                ).fetchone()
                if existing_source is None or str(
                    existing_source["event_version"]
                ) != event_version:
                    raise DistillationError(
                        f"source version identity collision: {source_id}:{source_digest}"
                    )
                # The committed snapshot is the first observation, so it may
                # carry different run-local correlation fields than this one.
                # Only the content identity has to agree; anything else under
                # the same digest means a tampered ledger.
                if not _same_version_identity(
                    stored_payload_json=str(existing_source["payload_json"]),
                    incoming_payload_json=payload_json,
                    versioned=versioned,
                ):
                    raise DistillationError(
                        f"source version identity collision: {source_id}:{source_digest}"
                    )
                connection.execute(
                    """
                    INSERT INTO distillation_outbox (
                        job_id, event_id, source_id, source_digest,
                        event_version, idempotency_key, status,
                        enqueued_at, available_at, max_attempts, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    ON CONFLICT (job_id) DO NOTHING
                    """,
                    (
                        job_id,
                        event_id,
                        source_id,
                        source_digest,
                        event_version,
                        idempotency_key,
                        enqueued_at,
                        enqueued_at,
                        self._default_max_attempts,
                        enqueued_at,
                    ),
                )
                row = self._select_job(connection, job_id)
                if row is None:
                    raise DistillationError(f"distillation outbox insert failed: {job_id}")
                if (
                    str(row["source_id"]) != source_id
                    or str(row["source_digest"]) != source_digest
                    or str(row["event_version"]) != event_version
                ):
                    raise DistillationError(f"distillation job identity collision: {job_id}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self._job_from_row(row)

    def enqueue(
        self,
        source_id: str,
        *,
        requested_by: str = "distillation_worker",
        enqueued_at: str | None = None,
    ) -> DistillationJob:
        """Legacy source-id enqueue backed by the transactional ledger."""
        when = _timestamp(enqueued_at) if enqueued_at else self._now()
        payload_json = _canonical_json({"source_id": source_id})
        return self._insert_source_version_and_job(
            source_id=source_id,
            source_digest="",
            payload_json=payload_json,
            requested_by=requested_by,
            enqueued_at=when,
            event_version="legacy.source_id.v1",
            versioned=False,
        )

    def enqueue_source_record(
        self,
        source: SourceRecord,
        *,
        requested_by: str = "distillation_worker",
        enqueued_at: str | datetime | float | None = None,
    ) -> DistillationJob:
        if source.status != SourceRecordStatus.NORMALIZED:
            raise DistillationError(
                f"Only normalized SourceRecord versions may be committed: {source.source_id}"
            )
        digest = source_version_digest(source)
        return self._insert_source_version_and_job(
            source_id=source.source_id,
            source_digest=digest,
            payload_json=_canonical_json(source.to_dict()),
            requested_by=requested_by,
            enqueued_at=_timestamp(enqueued_at) if enqueued_at is not None else self._now(),
            event_version="source_record.normalized.v1",
            versioned=True,
        )

    def get(self, source_id: str, source_digest: str | None = None) -> DistillationJob | None:
        with self._connect() as connection:
            if source_digest is None:
                row = connection.execute(
                    """
                    SELECT *
                      FROM distillation_outbox
                     WHERE source_id = ?
                     ORDER BY enqueued_at DESC, rowid DESC
                     LIMIT 1
                    """,
                    (source_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT *
                      FROM distillation_outbox
                     WHERE source_id = ? AND source_digest = ?
                    """,
                    (source_id, source_digest),
                ).fetchone()
        return self._job_from_row(row) if row is not None else None

    def list_pending(self) -> list[DistillationJob]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                  FROM distillation_outbox
                 WHERE status = 'pending'
                 ORDER BY available_at, enqueued_at, job_id
                """
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def list_all(self) -> list[DistillationJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM distillation_outbox ORDER BY enqueued_at, job_id"
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def source_for_job(self, job: DistillationJob) -> SourceRecord | None:
        if not job.source_digest:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                  FROM distillation_source_versions
                 WHERE source_id = ? AND source_digest = ?
                """,
                (job.source_id, job.source_digest),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        return SourceRecord.from_dict(payload)

    def version_count(self, source_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                  FROM distillation_source_versions
                 WHERE source_id = ? AND source_digest <> ''
                """,
                (source_id,),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def _recover_expired(self, connection: sqlite3.Connection, *, now: float) -> None:
        expired = connection.execute(
            """
            SELECT event_id
              FROM distillation_outbox
             WHERE status = 'leased' AND lease_expires_at <= ?
            """,
            (now,),
        ).fetchall()
        connection.execute(
            """
            UPDATE distillation_outbox
               SET status = 'pending',
                   available_at = ?,
                   lease_owner = NULL,
                   lease_token = NULL,
                   lease_expires_at = NULL,
                   last_error = 'lease_expired_before_registry_ack',
                   updated_at = ?
             WHERE status = 'leased' AND lease_expires_at <= ?
            """,
            (now, now, now),
        )
        for row in expired:
            connection.execute(
                """
                UPDATE distillation_inbox
                   SET status = 'retry_wait',
                       lease_owner = NULL,
                       lease_token = NULL,
                       lease_expires_at = NULL,
                       last_error = 'lease_expired_before_registry_ack',
                       updated_at = ?
                 WHERE event_id = ? AND status = 'processing'
                """,
                (now, str(row["event_id"])),
            )

    def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
        now: str | datetime | float | None = None,
        limit: int = 100,
    ) -> list[DistillationJob]:
        effective_now = _timestamp(now) if now is not None else self._now()
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        claimed: list[DistillationJob] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._recover_expired(connection, now=effective_now)
                rows = connection.execute(
                    """
                    SELECT *
                      FROM distillation_outbox
                     WHERE status IN ('pending', 'retry_wait')
                       AND available_at <= ?
                     ORDER BY available_at, enqueued_at, job_id
                     LIMIT ?
                    """,
                    (effective_now, max(0, int(limit))),
                ).fetchall()
                for row in rows:
                    token = f"{worker_id}:{uuid.uuid4().hex}"
                    expires_at = effective_now + float(lease_seconds)
                    changed = connection.execute(
                        """
                        UPDATE distillation_outbox
                           SET status = 'leased',
                               attempts = attempts + 1,
                               lease_owner = ?,
                               lease_token = ?,
                               lease_epoch = lease_epoch + 1,
                               lease_expires_at = ?,
                               updated_at = ?
                         WHERE job_id = ?
                           AND status IN ('pending', 'retry_wait')
                           AND available_at <= ?
                        """,
                        (
                            worker_id,
                            token,
                            expires_at,
                            effective_now,
                            str(row["job_id"]),
                            effective_now,
                        ),
                    ).rowcount
                    if changed != 1:
                        continue
                    current = self._select_job(connection, str(row["job_id"]))
                    assert current is not None
                    payload_digest = str(current["source_digest"] or current["job_id"])
                    connection.execute(
                        """
                        INSERT INTO distillation_inbox (
                            event_id, job_id, payload_digest, status,
                            attempts, lease_owner, lease_token, lease_epoch,
                            lease_expires_at, updated_at
                        ) VALUES (?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (event_id) DO UPDATE SET
                            status = 'processing',
                            attempts = excluded.attempts,
                            lease_owner = excluded.lease_owner,
                            lease_token = excluded.lease_token,
                            lease_epoch = excluded.lease_epoch,
                            lease_expires_at = excluded.lease_expires_at,
                            last_error = NULL,
                            updated_at = excluded.updated_at
                        WHERE distillation_inbox.status NOT IN ('applied', 'skipped')
                        """,
                        (
                            str(current["event_id"]),
                            str(current["job_id"]),
                            payload_digest,
                            int(current["attempts"]),
                            worker_id,
                            token,
                            int(current["lease_epoch"]),
                            expires_at,
                            effective_now,
                        ),
                    )
                    claimed.append(self._job_from_row(current))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return claimed

    def _terminal_update(
        self,
        *,
        job_id: str,
        status: DistillationJobStatus,
        processed_at: str | datetime | float | None,
        seed_id: str | None = None,
        registry_id: str | None = None,
        error: str | None = None,
        skip_reason: str | None = None,
        claim_token: str | None = None,
    ) -> None:
        now = _timestamp(processed_at) if processed_at is not None else self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_job(connection, job_id)
                if row is None:
                    raise DistillationError(f"DistillationJob not found: {job_id}")
                if claim_token is not None:
                    if (
                        str(row["status"]) != DistillationJobStatus.LEASED.value
                        or str(row["lease_token"] or "") != claim_token
                    ):
                        raise DistillationError(
                            f"stale distillation lease completion: {job_id}"
                        )
                    lease_expires_at = row["lease_expires_at"]
                    if lease_expires_at is None or float(lease_expires_at) <= now:
                        raise DistillationError(
                            f"expired distillation lease completion: {job_id}"
                        )
                attempts = int(row["attempts"]) + (0 if str(row["status"]) == "leased" else 1)
                connection.execute(
                    """
                    UPDATE distillation_outbox
                       SET status = ?, processed_at = ?, seed_id = ?,
                           registry_id = ?, last_error = ?, skip_reason = ?,
                           attempts = ?, lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, updated_at = ?
                     WHERE job_id = ?
                    """,
                    (
                        status.value,
                        now,
                        seed_id,
                        registry_id,
                        error,
                        skip_reason,
                        attempts,
                        now,
                        job_id,
                    ),
                )
                inbox_status = {
                    DistillationJobStatus.DONE: "applied",
                    DistillationJobStatus.SKIPPED: "skipped",
                    DistillationJobStatus.FAILED: "failed",
                }[status]
                connection.execute(
                    """
                    INSERT INTO distillation_inbox (
                        event_id, job_id, payload_digest, status, attempts,
                        result_ref, registry_id, last_error, applied_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (event_id) DO UPDATE SET
                        status = excluded.status,
                        attempts = excluded.attempts,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        result_ref = excluded.result_ref,
                        registry_id = excluded.registry_id,
                        last_error = excluded.last_error,
                        applied_at = excluded.applied_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(row["event_id"]),
                        job_id,
                        str(row["source_digest"] or job_id),
                        inbox_status,
                        attempts,
                        seed_id,
                        registry_id,
                        error or skip_reason,
                        now,
                        now,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_done(
        self,
        job_id: str,
        *,
        seed_id: str,
        registry_id: str | None = None,
        processed_at: str | datetime | float | None = None,
        claim_token: str | None = None,
    ) -> None:
        self._terminal_update(
            job_id=job_id,
            status=DistillationJobStatus.DONE,
            processed_at=processed_at,
            seed_id=seed_id,
            registry_id=registry_id,
            claim_token=claim_token,
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        error: str,
        processed_at: str | datetime | float | None = None,
        claim_token: str | None = None,
    ) -> None:
        self._terminal_update(
            job_id=job_id,
            status=DistillationJobStatus.FAILED,
            processed_at=processed_at,
            error=error,
            claim_token=claim_token,
        )

    def mark_skipped(
        self,
        job_id: str,
        *,
        reason: str,
        seed_id: str | None = None,
        registry_id: str | None = None,
        processed_at: str | datetime | float | None = None,
        claim_token: str | None = None,
    ) -> None:
        self._terminal_update(
            job_id=job_id,
            status=DistillationJobStatus.SKIPPED,
            processed_at=processed_at,
            seed_id=seed_id,
            registry_id=registry_id,
            skip_reason=reason,
            claim_token=claim_token,
        )

    def mark_retry_or_dead_letter(
        self,
        job: DistillationJob,
        *,
        error: str,
        base_delay_seconds: float,
        now: str | datetime | float | None = None,
        permanent: bool = False,
    ) -> DistillationJobStatus:
        effective_now = _timestamp(now) if now is not None else self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_job(connection, job.job_id)
                if row is None:
                    raise DistillationError(f"DistillationJob not found: {job.job_id}")
                if (
                    str(row["status"]) != DistillationJobStatus.LEASED.value
                    or str(row["lease_token"] or "") != str(job.lease_token or "")
                ):
                    raise DistillationError(f"stale distillation lease failure: {job.job_id}")
                lease_expires_at = row["lease_expires_at"]
                if lease_expires_at is None or float(lease_expires_at) <= effective_now:
                    raise DistillationError(
                        f"expired distillation lease failure: {job.job_id}"
                    )
                attempts = int(row["attempts"])
                dead_letter = permanent or attempts >= int(row["max_attempts"])
                status = (
                    DistillationJobStatus.DEAD_LETTER
                    if dead_letter
                    else DistillationJobStatus.RETRY_WAIT
                )
                delay = max(0.0, float(base_delay_seconds)) * (2 ** max(0, attempts - 1))
                available_at = effective_now if dead_letter else effective_now + delay
                connection.execute(
                    """
                    UPDATE distillation_outbox
                       SET status = ?, available_at = ?, processed_at = ?,
                           last_error = ?, lease_owner = NULL,
                           lease_token = NULL, lease_expires_at = NULL,
                           updated_at = ?
                     WHERE job_id = ?
                    """,
                    (
                        status.value,
                        available_at,
                        effective_now if dead_letter else None,
                        error,
                        effective_now,
                        job.job_id,
                    ),
                )
                inbox_status = "dead_letter" if dead_letter else "retry_wait"
                connection.execute(
                    """
                    UPDATE distillation_inbox
                       SET status = ?, lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, last_error = ?, updated_at = ?
                     WHERE event_id = ?
                    """,
                    (inbox_status, error, effective_now, str(row["event_id"])),
                )
                if dead_letter:
                    payload_row = connection.execute(
                        """
                        SELECT payload_json
                          FROM distillation_source_versions
                         WHERE source_id = ? AND source_digest = ?
                        """,
                        (str(row["source_id"]), str(row["source_digest"])),
                    ).fetchone()
                    payload_json = (
                        str(payload_row["payload_json"])
                        if payload_row is not None
                        else _canonical_json({"source_id": str(row["source_id"])})
                    )
                    connection.execute(
                        """
                        INSERT INTO distillation_dead_letters (
                            job_id, event_id, source_id, source_digest,
                            payload_json, attempts, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (job_id) DO UPDATE SET
                            attempts = excluded.attempts,
                            reason = excluded.reason,
                            created_at = excluded.created_at,
                            resolved_at = NULL
                        """,
                        (
                            job.job_id,
                            str(row["event_id"]),
                            str(row["source_id"]),
                            str(row["source_digest"]),
                            payload_json,
                            attempts,
                            error,
                            effective_now,
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return status

    def reset_to_pending(self, source_id: str, *, requested_by: str = "redispatch") -> DistillationJob:
        """Reset a job to pending for manual re-distillation. Idempotent if already pending."""
        existing = self.get(source_id)
        if existing is None:
            return self.enqueue(source_id, requested_by=requested_by)
        if existing.is_pending:
            return existing
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE distillation_outbox
                       SET status = 'pending', available_at = ?,
                           processed_at = NULL, seed_id = NULL,
                           registry_id = NULL, last_error = NULL,
                           skip_reason = NULL, lease_owner = NULL,
                           lease_token = NULL, lease_expires_at = NULL,
                           updated_at = ?
                     WHERE job_id = ?
                    """,
                    (now, now, existing.job_id),
                )
                connection.execute(
                    """
                    UPDATE distillation_dead_letters
                       SET redrive_count = redrive_count + 1,
                           last_redrive_actor = ?,
                           last_redrive_note = 'manual redispatch',
                           resolved_at = ?
                     WHERE job_id = ? AND resolved_at IS NULL
                    """,
                    (requested_by, now, existing.job_id),
                )
                row = self._select_job(connection, existing.job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        assert row is not None
        return self._job_from_row(row)

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM distillation_outbox"
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def metrics(self) -> dict[str, int]:
        metrics = {
            "pending": 0,
            "leased": 0,
            "retry_wait": 0,
            "done": 0,
            "failed": 0,
            "dead_letter": 0,
            "skipped": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                  FROM distillation_outbox
                 GROUP BY status
                """
            ).fetchall()
        for row in rows:
            metrics[str(row["status"])] = int(row["count"])
        return metrics

    def list_dead_letters(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                  FROM distillation_dead_letters
                 WHERE resolved_at IS NULL
                 ORDER BY created_at, job_id
                """
            ).fetchall()
        return [dict(row) for row in rows]


def _synthesize_evidence_item(
    source: SourceRecord,
    source_digest: str | None = None,
) -> EvidenceItem:
    """Create a minimal EvidenceItem for a normalized SourceRecord.

    The citation_label doubles as the stable dedupe key across distillation runs.
    """
    item_id = _stable_item_id(source.source_id, source_digest)
    body = source.title
    for key in ("body", "excerpt", "abstract", "summary"):
        candidate = source.metadata.get(key)
        if isinstance(candidate, str) and candidate.strip():
            body = candidate.strip()
            break
    return EvidenceItem(
        evidence_item_id=item_id,
        source_id=source.source_id,
        item_type=source.source_type.value,
        content_ref=source.content_ref,
        citation_label=(
            f"{source.source_id}#normalized"
            if not source_digest
            else f"{source.source_id}#normalized@{source_digest.removeprefix('sha256:')[:12]}"
        ),
        body=body,
        confidence=float(source.metadata.get("trust_score") or 0.7),
        access_scope=list(source.metadata.get("access_scope") or ("public",)),
        metadata={"evidence_dedupe_key": item_id, **dict(source.metadata)},
    )


def _synthesize_evidence_bundle(
    source: SourceRecord,
    item: EvidenceItem,
    source_digest: str | None = None,
) -> EvidenceBundle:
    """Create a minimal EvidenceBundle from a SourceRecord + its synthetic EvidenceItem."""
    bundle_id = _stable_bundle_id(source.source_id, source_digest)
    summary = source.title
    license_scope = str(source.metadata.get("license_scope") or "internal").strip() or "internal"
    access_scope = list(source.metadata.get("access_scope") or ("public",))
    confidence = item.confidence
    trace_refs: list[str] = [source.trace_id] if source.trace_id else []
    return EvidenceBundle(
        evidence_bundle_id=bundle_id,
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary=summary,
        citation_refs=[item.citation_label],
        confidence=confidence,
        license_scope=license_scope,
        access_scope=access_scope,
        created_by="distillation_worker",
        trace_refs=trace_refs,
        metadata={
            "distillation_source": "source_record",
            "source_type": source.source_type.value,
            "source_digest": source_digest,
            "source_event_version": "source_record.normalized.v1",
        },
    )


@dataclass(frozen=True)
class RegistrySyncResult:
    """Terminal Registry readback/write result for one source version."""

    registry_id: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class RegistrySyncRequest:
    """Everything a Registry sink needs to publish one distilled source version.

    The evidence item and bundle are the exact objects the seed was
    materialized from. A sink must not re-synthesize them: the version key the
    worker resolved is the only one whose ids are inside the seed's lineage.
    """

    source: SourceRecord
    seed: Any
    job: DistillationJob
    evidence_item: EvidenceItem
    evidence_bundle: EvidenceBundle
    version_key: str | None


class DistillationWorker:
    """Converts normalized SourceRecords into StrategySpecSeed drafts.

    The worker has three entry points:
    - enqueue_from_source_record: trigger from the ingest pipeline on normalization
    - run_pending: process the current pending queue (called by the scheduler)
    - catch_up: idempotent batch-enqueue all normalized sources that lack seeds
    - redispatch: manual reset of a specific source for re-processing
    """

    def __init__(
        self,
        job_queue: DistillationJobQueue | None = None,
        seed_store: StrategySpecSeedStore | None = None,
        *,
        created_by: str = "distillation_worker",
        worker_id: str | None = None,
        lease_seconds: float = 30.0,
        retry_base_seconds: float = 5.0,
        registry_sync: Callable[[RegistrySyncRequest], RegistrySyncResult] | None = None,
    ) -> None:
        self._queue = job_queue or DistillationJobQueue()
        self._seed_store = seed_store or StrategySpecSeedStore()
        self._materializer = SeedMaterializationService(
            store=self._seed_store,
            created_by=created_by,
        )
        self._created_by = created_by
        self._worker_id = worker_id or f"{created_by}-{uuid.uuid4().hex[:8]}"
        self._lease_seconds = max(0.1, float(lease_seconds))
        self._retry_base_seconds = max(0.0, float(retry_base_seconds))
        self._registry_sync = registry_sync

    def enqueue_from_source_record(self, source: SourceRecord) -> DistillationJob:
        """Enqueue a distillation job when a source record becomes normalized.

        Idempotent: calling this multiple times for the same source_id returns
        the same job without creating duplicates.
        """
        if source.status != SourceRecordStatus.NORMALIZED:
            raise DistillationError(
                f"Only normalized source can be enqueued for distillation: {source.source_id}"
            )
        return self._queue.enqueue_source_record(
            source,
            requested_by=self._created_by,
        )

    def run_pending(
        self,
        source_records: Mapping[str, SourceRecord] | None = None,
        *,
        limit: int = 100,
        now: str | None = None,
    ) -> DistillationRunResult:
        """Process pending distillation jobs.

        Args:
            source_records: mapping of source_id -> SourceRecord for lookup.
                Used only by legacy unversioned jobs. Versioned jobs always
                replay their transactionally committed source snapshot.
            limit: maximum number of jobs to process in this pass.
            now: override for the current timestamp (used in tests).

        Returns:
            A DistillationRunResult summarising what happened.
        """
        claimed = self._queue.claim_due(
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            now=now,
            limit=limit,
        )
        processed = created = refreshed = skipped = failed = 0
        registry_synced = retried = dead_lettered = 0
        for job in claimed:
            if job.source_digest:
                source = self._queue.source_for_job(job)
            else:
                source = (
                    source_records.get(job.source_id)
                    if source_records is not None
                    else None
                )
            if source is None:
                self._queue.mark_failed(
                    job.job_id,
                    error=f"SourceRecord not found: {job.source_id}",
                    processed_at=now,
                    claim_token=job.lease_token,
                )
                failed += 1
                processed += 1
                continue
            outcome, delivery = self._distill_one(job, source, now=now)
            processed += 1
            if outcome == "created":
                created += 1
            elif outcome == "refreshed":
                refreshed += 1
            elif outcome == "skipped":
                skipped += 1
            elif outcome == "failed":
                failed += 1
            if delivery == "registry_synced":
                registry_synced += 1
            elif delivery == "retry_wait":
                retried += 1
            elif delivery == "dead_letter":
                dead_lettered += 1
        return DistillationRunResult(
            processed=processed,
            created=created,
            refreshed=refreshed,
            skipped=skipped,
            failed=failed,
            registry_synced=registry_synced,
            retried=retried,
            dead_lettered=dead_lettered,
        )

    def catch_up(
        self,
        source_records: Sequence[SourceRecord],
        *,
        limit: int = 500,
        now: str | None = None,
    ) -> DistillationRunResult:
        """Idempotently enqueue and process all normalized sources without done seeds.

        Sources with existing done/skipped jobs are not re-processed unless
        redispatch() is called explicitly.

        Args:
            source_records: all known normalized source records.
            limit: maximum number of sources to enqueue in this pass.
            now: timestamp override for tests.

        Returns:
            A DistillationRunResult including the enqueued count.
        """
        normalized = [
            s for s in source_records if s.status == SourceRecordStatus.NORMALIZED
        ][:limit]

        enqueued = 0
        for source in normalized:
            digest = source_version_digest(source)
            existing = self._queue.get(source.source_id, digest)
            if existing is None:
                self._queue.enqueue_source_record(
                    source,
                    requested_by=self._created_by,
                    enqueued_at=now,
                )
                enqueued += 1

        # Read the committed source snapshot from the same ledger as the job.
        # This prevents a later revision in the caller's map from being used to
        # process an earlier versioned event.
        run_result = self.run_pending(None, now=now)
        return DistillationRunResult(
            processed=run_result.processed,
            created=run_result.created,
            refreshed=run_result.refreshed,
            skipped=run_result.skipped,
            failed=run_result.failed,
            enqueued=enqueued,
            registry_synced=run_result.registry_synced,
            retried=run_result.retried,
            dead_lettered=run_result.dead_lettered,
        )

    def redispatch(self, source_id: str, source: SourceRecord) -> DistillationJob:
        """Manually reset a source for re-distillation.

        Resets the job to PENDING and returns it. Idempotent if already pending.
        Re-distillation will only proceed if the seed is still a mutable draft;
        advanced seeds are skipped.

        Args:
            source_id: the source to redispatch.
            source: the SourceRecord for the source (must not be rejected).
        """
        if source.status == SourceRecordStatus.REJECTED:
            raise DistillationError(
                f"Rejected source cannot be redispatched: {source_id}"
            )
        return self._queue.reset_to_pending(source_id, requested_by="redispatch")

    def _distill_one(
        self,
        job: DistillationJob,
        source: SourceRecord,
        *,
        now: str | None = None,
    ) -> tuple[str, str | None]:
        """Run distillation for one job. Returns outcome string for accounting.

        The first value is one of ``created``, ``refreshed``, ``skipped``,
        or ``failed``. The second reports Registry delivery state when a
        terminal Registry sink is configured.
        """
        if source.status == SourceRecordStatus.REJECTED:
            self._queue.mark_skipped(
                job.job_id,
                reason=f"source {source.source_id} is rejected",
                processed_at=now,
                claim_token=job.lease_token,
            )
            return "skipped", None

        # Check existing seed status to enforce mutable-draft-only invariant.
        # A versioned job's materialization identity is fixed at admission.
        # It must not change when another revision is admitted before replay.
        version_key = job.source_digest or None
        bundle_id = _stable_bundle_id(source.source_id, version_key)
        existing_seeds = self._seed_store.list_by_bundle(bundle_id)
        if existing_seeds:
            seed = existing_seeds[0]
            seed_status = seed.status.value if isinstance(seed.status, StrategySpecSeedStatus) else str(seed.status)
            if seed_status in _IMMUTABLE_SEED_STATUSES:
                self._queue.mark_skipped(
                    job.job_id,
                    reason=f"seed {seed.seed_id} is in immutable status {seed_status!r}",
                    seed_id=seed.seed_id,
                    processed_at=now,
                    claim_token=job.lease_token,
                )
                return "skipped", "immutable"
            mode = MaterializationMode.REFRESH
            # Registry retries refresh the mutable seed from the same committed
            # version. Preserve its admission-time timestamp because it is part
            # of the StrategySpec payload and therefore its checksum.
            materialization_created_at = seed.created_at
        else:
            mode = MaterializationMode.CREATE_IF_ABSENT
            materialization_created_at = None

        is_refresh = mode == MaterializationMode.REFRESH

        try:
            item = _synthesize_evidence_item(source, version_key)
            bundle = _synthesize_evidence_bundle(source, item, version_key)
            result = self._materializer.materialize(
                bundle,
                source_records=[source],
                evidence_items=[item],
                mode=mode,
                requested_by=self._created_by,
                idempotency_key=job.idempotency_key,
                created_at=materialization_created_at,
            )
        except (SeedMaterializationError, ValueError, Exception) as exc:
            failure_status = self._queue.mark_retry_or_dead_letter(
                job,
                error=str(exc),
                base_delay_seconds=self._retry_base_seconds,
                now=now,
            )
            return "failed", failure_status.value

        if self._registry_sync is not None:
            try:
                registry_result = self._registry_sync(
                    RegistrySyncRequest(
                        source=source,
                        seed=result.seed,
                        job=job,
                        evidence_item=item,
                        evidence_bundle=bundle,
                        version_key=version_key,
                    )
                )
            except Exception as exc:
                failure_status = self._queue.mark_retry_or_dead_letter(
                    job,
                    error=f"registry_sync_failed: {type(exc).__name__}: {exc}",
                    base_delay_seconds=self._retry_base_seconds,
                    now=now,
                )
                return "failed", failure_status.value
            if registry_result.status == "immutable":
                self._queue.mark_skipped(
                    job.job_id,
                    reason=registry_result.reason or "Registry artifact is immutable",
                    seed_id=result.seed.seed_id,
                    registry_id=registry_result.registry_id,
                    processed_at=now,
                    claim_token=job.lease_token,
                )
                return "skipped", "immutable"
            if registry_result.status not in {"synced", "already_terminal"}:
                failure_status = self._queue.mark_retry_or_dead_letter(
                    job,
                    error=(
                        registry_result.reason
                        or f"non-terminal Registry result: {registry_result.status}"
                    ),
                    base_delay_seconds=self._retry_base_seconds,
                    now=now,
                )
                return "failed", failure_status.value
            registry_id = registry_result.registry_id
            delivery = "registry_synced"
        else:
            registry_id = None
            delivery = None

        self._queue.mark_done(
            job.job_id,
            seed_id=result.seed.seed_id,
            registry_id=registry_id,
            processed_at=now,
            claim_token=job.lease_token,
        )
        return ("refreshed" if is_refresh else "created"), delivery


def make_distillation_worker(
    *,
    queue_path: str | Path | None = None,
    seed_store_path: str | Path | None = None,
    created_by: str = "distillation_worker",
    worker_id: str | None = None,
    lease_seconds: float = 30.0,
    retry_base_seconds: float = 5.0,
    max_attempts: int = 3,
    registry_sync: Callable[[RegistrySyncRequest], RegistrySyncResult] | None = None,
) -> DistillationWorker:
    """Factory for a default-configured DistillationWorker."""
    queue = DistillationJobQueue(
        queue_path,
        default_max_attempts=max_attempts,
    )
    seed_store = StrategySpecSeedStore(seed_store_path)
    return DistillationWorker(
        queue,
        seed_store,
        created_by=created_by,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        retry_base_seconds=retry_base_seconds,
        registry_sync=registry_sync,
    )


__all__ = [
    "DistillationError",
    "DistillationJob",
    "DistillationJobQueue",
    "DistillationJobStatus",
    "DistillationRunResult",
    "DistillationWorker",
    "RegistrySyncResult",
    "make_distillation_worker",
    "source_version_digest",
]
