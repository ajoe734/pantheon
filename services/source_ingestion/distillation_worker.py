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

import copy
import dataclasses
import hashlib
import json
import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import (
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from services.source_ingestion.seed_materializer import (
    MaterializationMode,
    SeedMaterializationError,
    SeedMaterializationService,
)
from services.source_ingestion.hypothesis_card import (
    DAILY_CAP_REACHED,
    FIXED_DEFECT_REATTEMPT_OUTCOMES,
    HOSTED_DEFECT_CLOSED_SOURCE_BACKLOG,
    HOSTED_DEFECT_REATTEMPT_EXPECTED_CALLS,
    HOSTED_DEFECT_REATTEMPT_EXPECTED_DAYS,
    MAX_RECORD_ATTEMPTS_PER_DAY,
    MAX_RECORD_ATTEMPTS_TOTAL,
    RECORD_DAILY_LIMIT,
    RECORD_TOTAL_LIMIT,
    RESERVED,
    ExtractionLedger,
    FixedDefectOutcome,
    HypothesisExtractor,
    assess_source_relevance,
    card_to_strategy_seed_metadata,
    extraction_origin,
    extractor_from_env,
    fixed_defect_for_skip_reason,
    l2_extraction_tick_budget_seconds,
)
from services.source_ingestion.hypothesis_compiler import (
    COMPILED,
    NEEDS_DATA,
    NOT_EXPRESSIBLE,
    SKIPPED as SKIPPED_COMPILE,
    SUPPORT_ADDED,
    HypothesisCompiler,
)
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedStatus
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.demand_priority import DemandPriority, ResearchDemandReader


# Stable skip reason for market sources that materialize evidence/seed locally
# without reading or writing strategy drafts in the Registry.
MARKET_SOURCE_SKIP_REASON: str = (
    "source_type=market does not produce Registry strategy drafts"
)
DEFER_TTL_SECONDS = 14 * 86400
# Planned (non-failure) deferral reasons: the only ``reason`` values
# ``DistillationJobQueue.defer_budget`` writes, and the exact set
# ``metrics()["deferred"]`` counts, so the controller never reads a planned
# deferral as a Registry delivery retry (one shared definition).
DEFERRAL_BUDGET_EXHAUSTED = "budget_exhausted"
DEFERRAL_ATTEMPTS_DEFERRED = "attempts_deferred"
DEFERRAL_EXTRACTION_TICK_BUDGET = "extraction_tick_budget"
PLANNED_DEFERRAL_REASONS = frozenset({
    DEFERRAL_BUDGET_EXHAUSTED,
    DEFERRAL_ATTEMPTS_DEFERRED,
    DEFERRAL_EXTRACTION_TICK_BUDGET,
})
# A started job's lease covers one extraction call (its deadline) plus this.
EXTRACTION_LEASE_MARGIN_SECONDS = 60.0
EXTRACTION_ATTEMPTS_EXHAUSTED_REASON = "l2_extraction_attempts_exhausted"
DEFAULT_MAX_LEASE_EXPIRIES = 5
LEASE_BACKOFF_BASE_SECONDS = 30.0
LEASE_BACKOFF_CAP_SECONDS = 3600.0
LEASE_RECOVER_BATCH = 200
DEFERRED_CANDIDATE_WINDOW = 500
NEW_CANDIDATE_WINDOW = 500
PRUNED_PAYLOAD_JSON = '{"_pruned":true}'


def _skip_code_sql(column: str = "skip_reason") -> str:
    """Typed closure code of a skip reason: the text before the first colon.

    Free-text reasons that carry an id are mapped to a fixed code, and any
    other prefix with a space or over 64 characters becomes
    ``untyped_reason``, so the set of codes stays small however many records
    are closed. Used by the ``skip_code`` generated column.
    """
    prefix = (
        f"(CASE WHEN instr({column}, ':') > 0"
        f" THEN substr({column}, 1, instr({column}, ':') - 1) ELSE {column} END)"
    )
    return (
        "(CASE WHEN status <> 'skipped' THEN NULL"
        f" WHEN {column} IS NULL OR trim({column}) = '' THEN 'reason_not_recorded'"
        f" WHEN {column} LIKE 'source_type=market%' THEN 'market_source'"
        f" WHEN {column} LIKE 'source % is rejected' THEN 'source_rejected'"
        f" WHEN {column} LIKE 'seed % is in immutable status %' THEN 'seed_immutable'"
        f" WHEN instr({prefix}, ' ') > 0 OR length({prefix}) > 64 THEN 'untyped_reason'"
        f" ELSE {prefix} END)"
    )


# Current-state count of terminal (done/skipped) outbox rows per tenant,
# source type, status and typed closure code. Maintained by triggers on every
# write path, so per-source/per-reason closure accounting and the discovery
# summary never scan the outbox. Seeded once from the outbox when created.
TERMINAL_COUNTS_TABLE = "distillation_terminal_counts"
_TERMINAL_KEY_SQL = (
    "COALESCE({r}.tenant_id, ''), COALESCE({r}.source_type, ''), {r}.status,"
    " CASE WHEN {r}.status = 'done' THEN '' ELSE COALESCE({r}.skip_code, 'reason_not_recorded') END"
)
_TERMINAL_COUNT_UPSERT = (
    f"INSERT INTO {TERMINAL_COUNTS_TABLE} (tenant_id, source_type, status, reason, n)"
    " SELECT {key}, {delta} WHERE {r}.status IN ('done', 'skipped')"
    " ON CONFLICT (tenant_id, source_type, status, reason) DO UPDATE SET n = n + excluded.n;"
)


def _terminal_count_upsert(row: str, delta: int) -> str:
    return _TERMINAL_COUNT_UPSERT.format(key=_TERMINAL_KEY_SQL.format(r=row), delta=delta, r=row)


_PAYLOAD_FIELD_SQL = (
    "(SELECT CASE WHEN json_valid(s.payload_json) THEN json_extract(s.payload_json, '{path}') END"
    " FROM distillation_source_versions AS s"
    " WHERE s.source_id = {r}.source_id AND s.source_digest = {r}.source_digest)"
)
_TERMINAL_COUNT_DDL = (
    f"""CREATE TABLE IF NOT EXISTS {TERMINAL_COUNTS_TABLE} (
        tenant_id   TEXT NOT NULL,
        source_type TEXT NOT NULL,
        status      TEXT NOT NULL,
        reason      TEXT NOT NULL,
        n           INTEGER NOT NULL,
        PRIMARY KEY (tenant_id, source_type, status, reason)
    ) WITHOUT ROWID""",
    # Tenant and source type are stamped from the committed snapshot at insert
    # time; they survive payload pruning.
    "CREATE TRIGGER IF NOT EXISTS distillation_outbox_stamp_scope"
    " AFTER INSERT ON distillation_outbox"
    " WHEN NEW.tenant_id IS NULL OR NEW.source_type IS NULL BEGIN"
    " UPDATE distillation_outbox SET"
    " tenant_id = COALESCE(NEW.tenant_id, "
    + _PAYLOAD_FIELD_SQL.format(path="$.metadata.tenant_id", r="NEW") + ", ''),"
    " source_type = COALESCE(NEW.source_type, "
    + _PAYLOAD_FIELD_SQL.format(path="$.source_type", r="NEW") + ", '')"
    " WHERE job_id = NEW.job_id; END",
    "CREATE TRIGGER IF NOT EXISTS distillation_outbox_terminal_count_insert"
    " AFTER INSERT ON distillation_outbox BEGIN "
    + _terminal_count_upsert("NEW", 1) + " END",
    "CREATE TRIGGER IF NOT EXISTS distillation_outbox_terminal_count_update"
    " AFTER UPDATE OF status, skip_reason, tenant_id, source_type ON distillation_outbox"
    " WHEN OLD.status IN ('done', 'skipped') OR NEW.status IN ('done', 'skipped') BEGIN "
    + _terminal_count_upsert("OLD", -1) + " " + _terminal_count_upsert("NEW", 1) + " END",
    "CREATE TRIGGER IF NOT EXISTS distillation_outbox_terminal_count_delete"
    " AFTER DELETE ON distillation_outbox BEGIN "
    + _terminal_count_upsert("OLD", -1) + " END",
)
DEFAULT_TERMINAL_RETENTION_SECONDS = 7 * 86400
DEFAULT_DEAD_LETTER_RETENTION_SECONDS = 30 * 86400
LEASE_EXPIRED_REASON = "lease_expired_repeatedly"
# A lease-expired dead letter is redriven at most this many times; a job that
# exhausts its leases again afterwards stays dead-lettered with its count.
DEFAULT_MAX_LEASE_REDRIVES = 2
DEFAULT_LEASE_REDRIVE_LIMIT = 10
LINEAGE_CONFLICT_MARKERS = (
    "exists with conflicting distillation lineage/content",
    "readback mismatched distillation lineage/content",
)
LINEAGE_CONFLICT_DISPOSITION = "registry_lineage_conflict_unresolved"
OUT_OF_SCOPE_RETIRED_RUNTIME_REASON = "out_of_scope_retired_runtime"


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


_VOLATILE_RUN_METADATA_KEYS: tuple[str, ...] = (
    "ingest_run_id",
    "source_ingest_run_id",
    "trace_id",
)
_VOLATILE_VERSION_METADATA_KEYS: tuple[str, ...] = (
    *_VOLATILE_RUN_METADATA_KEYS,
    "raw_storage_refs",
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _source_version_identity_with_exclusions(
    source: SourceRecord,
    excluded_keys: Sequence[str],
) -> dict[str, Any]:
    metadata = dict(source.metadata)
    for volatile_key in excluded_keys:
        metadata.pop(volatile_key, None)
    return {
        "source_id": source.source_id,
        "connector_id": source.connector_id,
        "source_type": source.source_type.value,
        "title": source.title,
        "content_ref": source.content_ref,
        "metadata": metadata,
    }


def source_version_identity(source: SourceRecord) -> dict[str, Any]:
    """Return the content projection that defines one normalized source version.

    Run-local correlation fields and batch-scoped raw archive references are
    deliberately excluded so that re-ingesting the same normalized content in a
    later run maps to the same version, while a revised title, reference, body,
    content hash, or governed metadata creates a distinct event and
    distillation job.
    """

    return _source_version_identity_with_exclusions(
        source,
        _VOLATILE_VERSION_METADATA_KEYS,
    )


def source_version_digest(source: SourceRecord) -> str:
    """Return the stable content identity digest for one source version."""

    identity = _canonical_json(source_version_identity(source))
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _legacy_raw_refs_source_version_digest(source: SourceRecord) -> str:
    """Return the pre-repair digest projection that retained ``raw_storage_refs``."""

    identity = _canonical_json(
        _source_version_identity_with_exclusions(
            source,
            _VOLATILE_RUN_METADATA_KEYS,
        )
    )
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def source_version_digest_matches(source: SourceRecord, digest: str) -> bool:
    """Return True when ``digest`` matches the current or pre-repair projection."""

    if not digest:
        return False
    if source_version_digest(source) == digest:
        return True
    if "raw_storage_refs" not in source.metadata:
        return False
    return _legacy_raw_refs_source_version_digest(source) == digest


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


DEFAULT_MAX_TRANSIENT_REDRIVES = 5

_REGISTRY_FAILURE_MARKERS = (
    "registry_sync_failed",
    "failed to query registry",
    "failed to register strategy spec",
)
_TRANSIENT_NETWORK_MARKERS = (
    "timed out",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "connection refused",
    "connection reset",
    "connection aborted",
    "network is unreachable",
)


def is_transient_registry_failure(reason: str) -> bool:
    """True for Registry delivery errors caused by timeouts or DNS/connection loss."""
    text = reason.lower()
    return any(m in text for m in _REGISTRY_FAILURE_MARKERS) and any(
        m in text for m in _TRANSIENT_NETWORK_MARKERS
    )


# Registry authentication/authorization rejection (HTTP 401/403). The worker's
# credential is rejected for the duration of a failed release/rollback and
# accepted again afterwards, so these failures get a long retry schedule and are
# redriven by the controller once a probe is authenticated.
_AUTH_FAILURE_MARKERS = (
    "http error 401",
    "http error 403",
)
# Auth retry schedule: delay = min(cap, max(base, floor) * 2**(attempts-1)).
# With the 10 attempts below the waits are 60,120,240,480,600x5 = 3900s (65 min),
# which outlasts a 60 minute credential outage. Hosted rows carry max_attempts 3
# (5s base: 5s+10s = 15s of coverage), the limit is raised only for auth failures.
AUTH_RETRY_MAX_ATTEMPTS = 10
AUTH_RETRY_MIN_BASE_SECONDS = 60.0
AUTH_RETRY_MAX_DELAY_SECONDS = 600.0
DEFAULT_AUTH_REDRIVE_LIMIT = 100


def is_auth_registry_failure(reason: str) -> bool:
    """True for Registry delivery errors caused by a 401/403 rejection."""
    text = str(reason or "").lower()
    return any(m in text for m in _REGISTRY_FAILURE_MARKERS) and any(
        m in text for m in _AUTH_FAILURE_MARKERS
    )


def is_lease_expired_failure(reason: str) -> bool:
    """True for a dead letter caused by repeatedly expired leases (e.g. an OOM burst)."""
    return str(reason or "").startswith(LEASE_EXPIRED_REASON)


def is_lineage_conflict_failure(reason: str) -> bool:
    """True for a Registry lineage/content conflict that a replay cannot fix."""
    text = str(reason or "")
    return any(m in text for m in LINEAGE_CONFLICT_MARKERS)


def is_extraction_unavailable_failure(reason: str) -> bool:
    """True for a failure or dead letter caused by extraction being unavailable."""
    return str(reason or "").startswith(EXTRACTION_UNAVAILABLE_MARKER)


DEFAULT_SUPERSEDED_RESOLVE_LIMIT = 100
DEFAULT_EXTRACTION_REDRIVE_LIMIT = 20
DEFAULT_DEFECT_REATTEMPT_LIMIT = 100


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


class DistillationLeaseError(DistillationError):
    """A start/completion used a lease token that has been superseded.

    Raised per job; ``run_pending`` records it and keeps processing the rest of
    the batch instead of aborting the whole tick.
    """


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
    strategy_id: str | None = None
    version: str | None = None
    checksum: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_epoch: int = 0
    lease_expires_at: str | None = None
    next_attempt_at: str | None = None
    max_attempts: int = 3
    deferred_since: str | None = None
    priority_reason: str | None = None
    demand_score: int = 0

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
            "strategy_id": self.strategy_id,
            "version": self.version,
            "checksum": self.checksum,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_epoch": self.lease_epoch,
            "lease_expires_at": self.lease_expires_at,
            "next_attempt_at": self.next_attempt_at,
            "max_attempts": self.max_attempts,
            "deferred_since": self.deferred_since,
            "priority_reason": self.priority_reason,
            "demand_score": self.demand_score,
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
            strategy_id=data.get("strategy_id") or None,
            version=data.get("version") or None,
            checksum=data.get("checksum") or None,
            lease_owner=data.get("lease_owner") or None,
            lease_token=data.get("lease_token") or None,
            lease_epoch=int(data.get("lease_epoch") or 0),
            lease_expires_at=data.get("lease_expires_at") or None,
            next_attempt_at=data.get("next_attempt_at") or None,
            max_attempts=max(1, int(data.get("max_attempts") or 3)),
            deferred_since=data.get("deferred_since") or None,
            priority_reason=data.get("priority_reason") or None,
            demand_score=int(data.get("demand_score") or 0),
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
    deferred: int = 0
    # Jobs returned to retry_wait because the extraction model/adapter did
    # not answer (timeout, transport, billing). Not Registry delivery trouble.
    extraction_retried: int = 0
    superseded_dead_letters_resolved: int = 0
    extraction_dead_letters_redriven: int = 0
    defect_reattempted: int = 0
    lease_lost: tuple[str, ...] = ()
    # Today's L2 extraction spend (ledger summary) when an extractor is wired.
    extraction: Mapping[str, Any] | None = None

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
            "deferred": self.deferred,
            "extraction_retried": self.extraction_retried,
            "superseded_dead_letters_resolved": self.superseded_dead_letters_resolved,
            "extraction_dead_letters_redriven": self.extraction_dead_letters_redriven,
            "defect_reattempted": self.defect_reattempted,
            "lease_lost": list(self.lease_lost),
            **({"extraction": dict(self.extraction)} if self.extraction is not None else {}),
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
        max_lease_expiries: int = DEFAULT_MAX_LEASE_EXPIRIES,
        lease_backoff_base_seconds: float = LEASE_BACKOFF_BASE_SECONDS,
    ) -> None:
        resolved = Path(path) if path else Path(
            os.environ.get(
                "DISTILLATION_JOB_QUEUE_PATH",
                "data/distillation/job_queue.sqlite3",
            )
        )
        # Never inspect SQLite with a separate open()/close(): on POSIX,
        # closing ANY descriptor for this inode drops this process's SQLite
        # locks. Non-SQLite legacy input must be migrated offline, not silently
        # redirected to an empty sidecar queue.
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._path = resolved
        self._now = now
        self._default_max_attempts = max(1, int(default_max_attempts))
        self._max_lease_expiries = max(1, int(max_lease_expiries))
        self._lease_backoff_base = max(0.0, float(lease_backoff_base_seconds))
        self._bootstrap()

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Connection.__exit__ commits/rolls back but does NOT close. Closing
        # must also run after PRAGMA setup failures, independently of GC.
        with closing(sqlite3.connect(
            self._path, timeout=30.0, isolation_level=None,
        )) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection

    def _bootstrap(self) -> None:
        with self._connect() as connection:
            # Never rename a live SQLite family or silently discard its outbox,
            # inbox, leases and DLQ. Recovery requires all users to be quiesced.
            checks = connection.execute("PRAGMA quick_check").fetchall()
            if len(checks) != 1 or checks[0][0] != "ok":
                raise sqlite3.DatabaseError(
                    "distillation queue integrity check failed; offline recovery required"
                )
            # First-time rollback->WAL conversion returns SQLITE_BUSY at once
            # (the busy handler is not consulted) when a peer is initializing
            # the same fresh file. Retry only that, within the 30s busy bound.
            deadline = time.monotonic() + 30.0
            while True:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
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
                    strategy_id         TEXT,
                    version             TEXT,
                    checksum            TEXT,
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
                    strategy_id         TEXT,
                    version             TEXT,
                    checksum            TEXT,
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
            # Constructors can race across ingest/controller processes. Hold
            # the writer lock through column inspection and additive migration.
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._migrate_schema(connection)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        for table in ("distillation_outbox", "distillation_inbox"):
            cols = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for col in ("strategy_id", "version", "checksum"):
                if col not in cols:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        cols = {row["name"] for row in connection.execute("PRAGMA table_info(distillation_outbox)")}
        for name, kind in (("deferred_since", "REAL"), ("priority_reason", "TEXT"),
                           ("demand_score", "INTEGER NOT NULL DEFAULT 0")):
            if name not in cols:
                connection.execute(f"ALTER TABLE distillation_outbox ADD COLUMN {name} {kind}")
        cols = {row["name"] for row in connection.execute("PRAGMA table_info(distillation_outbox)")}
        first_bounded_migration = "lease_expiries" not in cols
        for name, kind in (("lease_expiries", "INTEGER NOT NULL DEFAULT 0"),
                           ("lease_started", "INTEGER NOT NULL DEFAULT 0"),
                           ("pruned_at", "REAL")):
            if name not in cols:
                connection.execute(f"ALTER TABLE distillation_outbox ADD COLUMN {name} {kind}")
        if first_bounded_migration:
            # One-time normalisation. Before this migration every claim and
            # every lease-expiry recovery incremented ``attempts``, so rows
            # starved by the 2026-11-13 expiry loop carry attempts in the
            # thousands without ever having executed. Reset them so they are
            # not dead-lettered at once. Other non-terminal rows are clamped to
            # one attempt below their cap. Terminal rows are left untouched.
            connection.execute(
                """UPDATE distillation_outbox SET attempts = 0
                    WHERE status IN ('pending', 'leased', 'retry_wait')
                      AND last_error = 'lease_expired_before_registry_ack'"""
            )
            connection.execute(
                """UPDATE distillation_outbox SET attempts = MAX(0, max_attempts - 1)
                    WHERE status IN ('pending', 'leased', 'retry_wait')
                      AND attempts >= max_attempts"""
            )
            connection.execute(
                """UPDATE distillation_inbox SET attempts =
                       (SELECT o.attempts FROM distillation_outbox o
                         WHERE o.job_id = distillation_inbox.job_id)
                    WHERE status IN ('processing', 'retry_wait')
                      AND job_id IN (SELECT job_id FROM distillation_outbox
                                      WHERE status IN ('pending', 'leased', 'retry_wait'))"""
            )
        if "defect_reattempted" not in cols:
            connection.execute(
                "ALTER TABLE distillation_outbox ADD COLUMN defect_reattempted INTEGER NOT NULL DEFAULT 0"
            )
        self._migrate_terminal_counts(connection)
        # Indexes support the bounded claim, lease recovery, pruning and the
        # per-tick readbacks; none of them may require a sort or a scan that
        # grows with total history.
        for statement in (
            # Tenant-scoped windows of the discovery summary (bounded reads).
            """CREATE INDEX IF NOT EXISTS distillation_outbox_tenant_enqueued_idx
               ON distillation_outbox(tenant_id, enqueued_at, source_type)""",
            """CREATE INDEX IF NOT EXISTS distillation_outbox_tenant_terminal_idx
               ON distillation_outbox(tenant_id, status, processed_at)""",
            """CREATE INDEX IF NOT EXISTS distillation_outbox_deferred_idx
               ON distillation_outbox(status, enqueued_at)
               WHERE deferred_since IS NOT NULL""",
            """CREATE INDEX IF NOT EXISTS distillation_outbox_lease_idx
               ON distillation_outbox(lease_expires_at) WHERE status = 'leased'""",
            """CREATE INDEX IF NOT EXISTS distillation_outbox_prune_idx
               ON distillation_outbox(status, processed_at) WHERE pruned_at IS NULL""",
            """CREATE INDEX IF NOT EXISTS distillation_outbox_defect_reattempt_idx
               ON distillation_outbox(enqueued_at)
               WHERE status = 'skipped' AND defect_reattempted = 0
                 AND skip_reason LIKE 'hypothesis_extraction_failed:%'""",
            # Exact (source_id, source_digest) readbacks (get/claim/due_tenants)
            # would otherwise scan the whole outbox for every lookup.
            """CREATE INDEX IF NOT EXISTS distillation_outbox_source_version_idx
               ON distillation_outbox(source_id, source_digest)""",
            """CREATE INDEX IF NOT EXISTS distillation_dead_letters_resolved_idx
               ON distillation_dead_letters(resolved_at) WHERE resolved_at IS NOT NULL""",
        ):
            connection.execute(statement)

    @staticmethod
    def _migrate_terminal_counts(connection: sqlite3.Connection) -> None:
        """Scope columns, typed closure code and the trigger-kept terminal counter.

        Runs inside the bootstrap writer transaction. The one-time backfill
        reads each existing row's committed snapshot; rows whose payload was
        already pruned keep an empty tenant and source type.
        """
        cols = {row["name"] for row in connection.execute("PRAGMA table_xinfo(distillation_outbox)")}
        if "tenant_id" not in cols:
            connection.execute("ALTER TABLE distillation_outbox ADD COLUMN tenant_id TEXT")
        if "source_type" not in cols:
            connection.execute("ALTER TABLE distillation_outbox ADD COLUMN source_type TEXT")
        if "skip_code" not in cols:
            connection.execute(
                "ALTER TABLE distillation_outbox ADD COLUMN skip_code TEXT"
                f" GENERATED ALWAYS AS {_skip_code_sql()} VIRTUAL"
            )
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TERMINAL_COUNTS_TABLE,),
        ).fetchone()
        if exists is not None:
            for statement in _TERMINAL_COUNT_DDL:
                connection.execute(statement)
            return
        connection.execute(
            "UPDATE distillation_outbox SET"
            " tenant_id = COALESCE(tenant_id, "
            + _PAYLOAD_FIELD_SQL.format(path="$.metadata.tenant_id", r="distillation_outbox") + ", ''),"
            " source_type = COALESCE(source_type, "
            + _PAYLOAD_FIELD_SQL.format(path="$.source_type", r="distillation_outbox") + ", '')"
            " WHERE tenant_id IS NULL OR source_type IS NULL"
        )
        for statement in _TERMINAL_COUNT_DDL:
            connection.execute(statement)
        connection.execute(
            f"INSERT INTO {TERMINAL_COUNTS_TABLE} (tenant_id, source_type, status, reason, n)"
            f" SELECT {_TERMINAL_KEY_SQL.format(r='o')}, COUNT(*) FROM distillation_outbox AS o"
            " WHERE o.status IN ('done', 'skipped') GROUP BY 1, 2, 3, 4"
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> DistillationJob:
        row_keys = row.keys()
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
            strategy_id=row["strategy_id"] if "strategy_id" in row_keys else None,
            version=row["version"] if "version" in row_keys else None,
            checksum=row["checksum"] if "checksum" in row_keys else None,
            error=row["last_error"],
            skip_reason=row["skip_reason"],
            attempts=int(row["attempts"]),
            lease_owner=row["lease_owner"],
            lease_token=row["lease_token"],
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at=_timestamp_text(row["lease_expires_at"]),
            next_attempt_at=_timestamp_text(row["available_at"]),
            max_attempts=int(row["max_attempts"]),
            deferred_since=_timestamp_text(row["deferred_since"]),
            priority_reason=row["priority_reason"],
            demand_score=int(row["demand_score"]),
        )

    def _select_job(self, connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM distillation_outbox WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    def _resolve_committed_source_digest(
        self,
        connection: sqlite3.Connection,
        *,
        source_id: str,
        source_digest: str,
        payload_json: str,
        event_version: str,
        versioned: bool,
    ) -> str:
        if not versioned or not source_digest:
            return source_digest
        exact = connection.execute(
            """
            SELECT source_digest
              FROM distillation_source_versions
             WHERE source_id = ? AND source_digest = ?
            """,
            (source_id, source_digest),
        ).fetchone()
        if exact is not None:
            return source_digest
        rows = connection.execute(
            """
            SELECT source_digest, payload_json, event_version
              FROM distillation_source_versions
             WHERE source_id = ? AND source_digest <> ''
             ORDER BY committed_at ASC, rowid ASC
            """,
            (source_id,),
        ).fetchall()
        for row in rows:
            if str(row["event_version"]) != event_version:
                continue
            if _same_version_identity(
                stored_payload_json=str(row["payload_json"]),
                incoming_payload_json=payload_json,
                versioned=True,
            ):
                return str(row["source_digest"])
        return source_digest

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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                source_digest = self._resolve_committed_source_digest(
                    connection,
                    source_id=source_id,
                    source_digest=source_digest,
                    payload_json=payload_json,
                    event_version=event_version,
                    versioned=versioned,
                )
                job_id = _stable_job_id(source_id, source_digest if versioned else None)
                event_id = f"source-normalized:{source_id}:{source_digest or job_id}"
                idempotency_key = (
                    f"distill:{source_id}:{source_digest}"
                    if versioned
                    else f"distill:{source_id}:{requested_by}"
                )
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
                if str(existing_source["payload_json"]) != PRUNED_PAYLOAD_JSON and not _same_version_identity(
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
                if row is None and source_digest:
                    version_rows = connection.execute(
                        """
                        SELECT source_digest, payload_json
                          FROM distillation_source_versions
                         WHERE source_id = ? AND source_digest <> ''
                         ORDER BY committed_at ASC, rowid ASC
                        """,
                        (source_id,),
                    ).fetchall()
                    for version_row in version_rows:
                        try:
                            stored = SourceRecord.from_dict(
                                json.loads(str(version_row["payload_json"]))
                            )
                        except Exception:
                            continue
                        if source_version_digest_matches(stored, source_digest):
                            row = connection.execute(
                                """
                                SELECT *
                                  FROM distillation_outbox
                                 WHERE source_id = ? AND source_digest = ?
                                """,
                                (source_id, str(version_row["source_digest"])),
                            ).fetchone()
                            if row is not None:
                                break
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
        if row is None or str(row["payload_json"]) == PRUNED_PAYLOAD_JSON:
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
        """Release expired leases: bounded batch, backoff, capped expiries.

        A lease that expired after its job *started* counts toward
        ``lease_expiries``; at the cap the job moves to the dead-letter table
        with its own reason and stops heading the queue. A lease that expired
        before the job started (claimed but never reached) is released without
        penalty. Neither path touches ``attempts``.
        """
        expired = connection.execute(
            """
            SELECT job_id, event_id, source_id, source_digest, attempts,
                   lease_started, lease_expiries
              FROM distillation_outbox
             WHERE status = 'leased' AND lease_expires_at <= ?
             ORDER BY lease_expires_at
             LIMIT ?
            """,
            (now, LEASE_RECOVER_BATCH),
        ).fetchall()
        for row in expired:
            event_id = str(row["event_id"])
            expiries = int(row["lease_expiries"])
            if not int(row["lease_started"]):
                connection.execute(
                    """UPDATE distillation_outbox
                          SET status = 'pending', available_at = ?, lease_owner = NULL,
                              lease_token = NULL, lease_expires_at = NULL,
                              lease_started = 0, updated_at = ?
                        WHERE job_id = ? AND status = 'leased'""",
                    (now, now, str(row["job_id"])),
                )
                connection.execute(
                    """UPDATE distillation_inbox
                          SET status = 'retry_wait', lease_owner = NULL, lease_token = NULL,
                              lease_expires_at = NULL, updated_at = ?
                        WHERE event_id = ? AND status = 'processing'""",
                    (now, event_id),
                )
                continue
            expiries += 1
            if expiries >= self._max_lease_expiries:
                reason = f"{LEASE_EXPIRED_REASON}: {expiries} lease expiries"
                connection.execute(
                    """UPDATE distillation_outbox
                          SET status = 'dead_letter', processed_at = ?, last_error = ?,
                              lease_expiries = ?, lease_started = 0, lease_owner = NULL,
                              lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                        WHERE job_id = ? AND status = 'leased'""",
                    (now, reason, expiries, now, str(row["job_id"])),
                )
                connection.execute(
                    """UPDATE distillation_inbox
                          SET status = 'dead_letter', lease_owner = NULL, lease_token = NULL,
                              lease_expires_at = NULL, last_error = ?, updated_at = ?
                        WHERE event_id = ?""",
                    (reason, now, event_id),
                )
                payload_row = connection.execute(
                    """SELECT payload_json FROM distillation_source_versions
                        WHERE source_id = ? AND source_digest = ?""",
                    (str(row["source_id"]), str(row["source_digest"])),
                ).fetchone()
                payload_json = (
                    str(payload_row["payload_json"]) if payload_row is not None
                    else _canonical_json({"source_id": str(row["source_id"])})
                )
                connection.execute(
                    """INSERT INTO distillation_dead_letters (
                           job_id, event_id, source_id, source_digest,
                           payload_json, attempts, reason, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (job_id) DO UPDATE SET
                           attempts = excluded.attempts, reason = excluded.reason,
                           created_at = excluded.created_at, resolved_at = NULL""",
                    (str(row["job_id"]), event_id, str(row["source_id"]),
                     str(row["source_digest"]), payload_json, int(row["attempts"]),
                     reason, now),
                )
                continue
            # Exponential backoff so one repeatedly expiring job cannot head
            # the queue every tick. Budget-deferred rows keep ``deferred_since``
            # (their 14-day TTL) and get the same backoff.
            delay = min(LEASE_BACKOFF_CAP_SECONDS,
                        self._lease_backoff_base * (2 ** (expiries - 1)))
            connection.execute(
                """UPDATE distillation_outbox
                      SET status = 'pending', available_at = ?, lease_owner = NULL,
                          lease_token = NULL, lease_expires_at = NULL, lease_started = 0,
                          lease_expiries = ?, last_error = 'lease_expired_before_registry_ack',
                          updated_at = ?
                    WHERE job_id = ? AND status = 'leased'""",
                (now + delay, expiries, now, str(row["job_id"])),
            )
            connection.execute(
                """UPDATE distillation_inbox
                      SET status = 'retry_wait', lease_owner = NULL, lease_token = NULL,
                          lease_expires_at = NULL,
                          last_error = 'lease_expired_before_registry_ack', updated_at = ?
                    WHERE event_id = ? AND status = 'processing'""",
                (now, event_id),
            )

    def due_tenants(self, *, now: float, tenant_id: str | None = None) -> set[str]:
        """Discover scopes before network reads, outside the claim transaction.

        Reads only the bounded candidate window the next claim can take, never
        the whole due backlog.
        """
        window = DEFERRED_CANDIDATE_WINDOW * 2
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT json_extract(s.payload_json, '$.metadata.tenant_id') AS tenant
                   FROM (
                       SELECT source_id, source_digest FROM (
                           SELECT source_id, source_digest FROM distillation_outbox
                            WHERE status = 'pending' AND available_at <= ?
                            ORDER BY available_at, enqueued_at LIMIT ?)
                       UNION ALL
                       SELECT source_id, source_digest FROM (
                           SELECT source_id, source_digest FROM distillation_outbox
                            WHERE status = 'retry_wait' AND available_at <= ?
                            ORDER BY available_at, enqueued_at LIMIT ?)
                       UNION ALL
                       SELECT source_id, source_digest FROM (
                           SELECT source_id, source_digest FROM distillation_outbox
                            WHERE status = 'leased' AND lease_expires_at <= ?
                            ORDER BY lease_expires_at LIMIT ?)
                   ) o
                   JOIN distillation_source_versions s USING (source_id, source_digest)""",
                (now, window, now, window, now, LEASE_RECOVER_BATCH),
            ).fetchall()
        return {str(row["tenant"]) for row in rows if row["tenant"]
                and (tenant_id is None or row["tenant"] == tenant_id)}

    def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
        now: str | datetime | float | None = None,
        limit: int = 100,
        tenant_id: str | None = None,
        priority_by_tenant: Mapping[str, DemandPriority] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[DistillationJob]:
        """Claim up to ``limit`` due jobs without reading the whole backlog.

        The claim only reserves jobs (token + a placeholder lease); it does not
        count an attempt. ``begin_job`` starts each job's own lease and counts
        the attempt. Candidate selection reads at most ``limit`` new rows plus a
        bounded window of budget-deferred rows, in index order (no temp sort).
        """
        effective_now = _timestamp(now) if now is not None else self._now()
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        claimed: list[DistillationJob] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._recover_expired(connection, now=effective_now)
                take = max(0, int(limit))
                tenant_clause = ""
                tenant_params: tuple[Any, ...] = ()
                if tenant is not None:
                    tenant_clause = """
                       AND EXISTS (
                           SELECT 1
                             FROM distillation_source_versions AS source_version
                            WHERE source_version.source_id = distillation_outbox.source_id
                              AND source_version.source_digest = distillation_outbox.source_digest
                              AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?
                       )
                    """
                    tenant_params = (tenant,)
                selected: dict[str, sqlite3.Row] = {}
                if job_ids is not None:
                    explicit_ids = [str(jid) for jid in job_ids if str(jid)]
                    if explicit_ids:
                        placeholders = ", ".join("?" for _ in explicit_ids)
                        for row in connection.execute(
                            f"""
                            SELECT job_id, available_at, enqueued_at, deferred_since, defect_reattempted
                              FROM distillation_outbox
                             WHERE job_id IN ({placeholders})
                               AND status IN ('pending', 'retry_wait')
                               AND available_at <= ?
                               {tenant_clause}
                            """,
                            (*explicit_ids, effective_now, *tenant_params),
                        ).fetchall():
                            selected[str(row["job_id"])] = row
                else:
                    # With demand ordering on, read a wider arrival window so that
                    # papers and demand-relevant rows behind older news can be seen.
                    new_window = max(take, NEW_CANDIDATE_WINDOW) if priority_by_tenant else take
                    for status in ("pending", "retry_wait"):
                        # Index (status, available_at, enqueued_at) yields this
                        # order directly; LIMIT bounds rows read and parsed.
                        for row in connection.execute(
                            f"""
                            SELECT job_id, available_at, enqueued_at, deferred_since, defect_reattempted
                              FROM distillation_outbox
                             WHERE status = ? AND available_at <= ?
                               {tenant_clause}
                             ORDER BY available_at, enqueued_at
                             LIMIT ?
                            """,
                            (status, effective_now, *tenant_params, new_window),
                        ).fetchall():
                            selected[str(row["job_id"])] = row
                        if any(int(r["defect_reattempted"] or 0) for r in selected.values()):
                            # Ensure fresh due work is never hidden behind re-opened defect rows.
                            for row in connection.execute(
                                f"""
                                SELECT job_id, available_at, enqueued_at, deferred_since, defect_reattempted
                                  FROM distillation_outbox
                                 WHERE status = ? AND available_at <= ?
                                   AND defect_reattempted = 0
                                   {tenant_clause}
                                 ORDER BY available_at, enqueued_at
                                 LIMIT ?
                                """,
                                (status, effective_now, *tenant_params, new_window),
                            ).fetchall():
                                selected[str(row["job_id"])] = row
                        if priority_by_tenant:
                            # Papers (about 11 a day) are never starved by news volume.
                            for row in connection.execute(
                                f"""
                                SELECT job_id, available_at, enqueued_at, deferred_since, defect_reattempted
                                  FROM distillation_outbox
                                 WHERE status = ? AND available_at <= ?
                                   AND EXISTS (
                                       SELECT 1 FROM distillation_source_versions AS paper_version
                                        WHERE paper_version.source_id = distillation_outbox.source_id
                                          AND paper_version.source_digest = distillation_outbox.source_digest
                                          AND json_extract(paper_version.payload_json, '$.source_type') = 'paper')
                                   {tenant_clause}
                                 ORDER BY available_at, enqueued_at
                                 LIMIT ?
                                """,
                                (status, effective_now, *tenant_params, take),
                            ).fetchall():
                                selected[str(row["job_id"])] = row
                        # Budget-deferred backlog precedes new work: take a bounded
                        # window of it through its partial index.
                        for row in connection.execute(
                            f"""
                            SELECT job_id, available_at, enqueued_at, deferred_since, defect_reattempted
                              FROM distillation_outbox
                             WHERE status = ? AND available_at <= ?
                               AND deferred_since IS NOT NULL
                               {tenant_clause}
                             ORDER BY enqueued_at
                             LIMIT ?
                            """,
                            (status, effective_now, *tenant_params, DEFERRED_CANDIDATE_WINDOW),
                        ).fetchall():
                            selected[str(row["job_id"])] = row
                priorities = priority_by_tenant or {}
                ranked = []
                for candidate in selected.values():
                    payload_row = connection.execute(
                        """SELECT source.payload_json
                             FROM distillation_outbox
                             JOIN distillation_source_versions AS source USING (source_id, source_digest)
                            WHERE distillation_outbox.job_id = ?""",
                        (str(candidate["job_id"]),),
                    ).fetchone()
                    try:
                        payload = json.loads(payload_row["payload_json"]) if payload_row else {}
                    except ValueError:
                        payload = {}
                    scope = (payload.get("metadata") or {}).get("tenant_id") if isinstance(payload, dict) else None
                    priority = priorities.get(scope, DemandPriority(
                        reason="demand_read_missing_tenant" if not scope else "demand_reader_not_configured",
                    ))
                    deferred = candidate["deferred_since"] is not None
                    reattempt = bool(int(candidate["defect_reattempted"] or 0))
                    score = priority.score(payload)
                    is_paper = isinstance(payload, dict) and payload.get("source_type") == "paper"
                    # Fresh work (deferred backlog, then new work) precedes fixed-defect
                    # re-attempts; within each group papers first, then demand score.
                    # Stable arrival ties. A hint only orders: it never admits, drops or
                    # changes tenant scope, and a record that does not fit the cap is
                    # deferred, not dropped.
                    key = (1 if reattempt else 0, 0 if deferred else 1, 0 if is_paper else 1, -score,
                           candidate["enqueued_at"] if deferred else candidate["available_at"],
                           candidate["enqueued_at"], candidate["job_id"])
                    ranked.append((key, candidate, priority.reason, score))
                ranked.sort(key=lambda item: item[0])
                for _, row, priority_reason, score in ranked[:max(0, int(limit))]:
                    token = f"{worker_id}:{uuid.uuid4().hex}"
                    expires_at = effective_now + float(lease_seconds)
                    changed = connection.execute(
                        """
                        UPDATE distillation_outbox
                           SET status = 'leased',
                               lease_started = 0,
                               lease_owner = ?,
                               lease_token = ?,
                               lease_epoch = lease_epoch + 1,
                               lease_expires_at = ?,
                               priority_reason = ?, demand_score = ?,
                               updated_at = ?
                         WHERE job_id = ?
                           AND status IN ('pending', 'retry_wait')
                           AND available_at <= ?
                        """,
                        (
                            worker_id,
                            token,
                            expires_at,
                            priority_reason,
                            score,
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

    def begin_job(
        self,
        job: DistillationJob,
        *,
        lease_seconds: float,
        now: str | datetime | float | None = None,
    ) -> DistillationJob:
        """Start one claimed job: its own lease window begins here, and this is
        the only place an attempt is counted.

        Time spent in the claim and in earlier jobs of the same batch does not
        consume the job's lease. A job whose claim placeholder lease lapsed but
        which nobody recovered is still ours (the token is the fence), so it may
        start; a superseded token raises ``DistillationLeaseError``.
        """
        effective_now = _timestamp(now) if now is not None else self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_job(connection, job.job_id)
                if (row is None or str(row["status"]) != "leased"
                        or str(row["lease_token"] or "") != str(job.lease_token or "")):
                    raise DistillationLeaseError(
                        f"stale distillation lease start: {job.job_id}"
                    )
                expires_at = effective_now + float(lease_seconds)
                connection.execute(
                    """UPDATE distillation_outbox
                          SET attempts = attempts + 1, lease_started = 1,
                              lease_expires_at = ?, updated_at = ?
                        WHERE job_id = ?""",
                    (expires_at, effective_now, job.job_id),
                )
                connection.execute(
                    """UPDATE distillation_inbox
                          SET attempts = attempts + 1, lease_expires_at = ?, updated_at = ?
                        WHERE event_id = ? AND status = 'processing'""",
                    (expires_at, effective_now, str(row["event_id"])),
                )
                current = self._select_job(connection, job.job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        assert current is not None
        return self._job_from_row(current)

    def defer_budget(
        self,
        job: DistillationJob,
        *,
        now: float,
        reason: str = DEFERRAL_BUDGET_EXHAUSTED,
        next_window: bool = True,
    ) -> None:
        """Return a fenced lease without burning retries.

        ``next_window`` waits for the next UTC cap window (14-day deferral TTL);
        otherwise the job is due again at once (end of this tick's budget).
        ``reason`` must be one of ``PLANNED_DEFERRAL_REASONS`` so ``metrics()``
        counts the row as deferred rather than as a delivery retry.
        """
        if reason not in PLANNED_DEFERRAL_REASONS:
            raise ValueError(f"unplanned distillation deferral reason: {reason!r}")
        if not next_window:
            self._release_started(job, now=now, reason=reason)
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_job(connection, job.job_id)
                if (row is None or row["status"] != "leased"
                        or row["lease_token"] != job.lease_token):
                    raise DistillationLeaseError(f"stale or expired distillation deferral: {job.job_id}")
                since = row["deferred_since"] if row["deferred_since"] is not None else now
                available = min((int(now) // 86400 + 1) * 86400, since + DEFER_TTL_SECONDS)
                connection.execute(
                    """UPDATE distillation_outbox SET status = 'retry_wait', available_at = ?,
                       deferred_since = ?, last_error = ?,
                       skip_reason = ?,
                       attempts = MAX(0, attempts - 1), lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, updated_at = ? WHERE job_id = ?""",
                    (available, since, reason, f"{reason}: deferred to next cap window", now, job.job_id),
                )
                connection.execute(
                    """UPDATE distillation_inbox SET status = 'retry_wait',
                       attempts = MAX(0, attempts - 1), lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, last_error = ?, updated_at = ?
                       WHERE job_id = ?""", (reason, now, job.job_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _release_started(self, job: DistillationJob, *, now: float, reason: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_job(connection, job.job_id)
                if (row is None or row["status"] != "leased"
                        or row["lease_token"] != job.lease_token):
                    raise DistillationLeaseError(f"stale or expired distillation release: {job.job_id}")
                connection.execute(
                    """UPDATE distillation_outbox SET status = 'retry_wait', available_at = ?,
                       last_error = ?, attempts = MAX(0, attempts - 1), lease_owner = NULL,
                       lease_token = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?""",
                    (now, reason, now, job.job_id),
                )
                connection.execute(
                    """UPDATE distillation_inbox SET status = 'retry_wait',
                       attempts = MAX(0, attempts - 1), lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, last_error = ?, updated_at = ?
                       WHERE job_id = ?""", (reason, now, job.job_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def clear_budget_deferral(self, job: DistillationJob, *, now: float) -> None:
        """A produced card ends cap waiting; Registry retries have their own lifetime."""
        if job.deferred_since is None:
            return
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE distillation_outbox SET deferred_since = NULL, skip_reason = NULL,
                   last_error = NULL, updated_at = ? WHERE job_id = ? AND status = 'leased'
                   AND lease_token = ?""",
                (now, job.job_id, job.lease_token),
            ).rowcount
            if changed != 1:
                raise DistillationLeaseError(f"stale extraction completion: {job.job_id}")

    def _terminal_update(
        self,
        *,
        job_id: str,
        status: DistillationJobStatus,
        processed_at: str | datetime | float | None,
        seed_id: str | None = None,
        registry_id: str | None = None,
        strategy_id: str | None = None,
        version: str | None = None,
        checksum: str | None = None,
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
                        raise DistillationLeaseError(
                            f"stale distillation lease completion: {job_id}"
                        )
                    # Contract: the lease token is the fence. A completion with
                    # the current token is accepted even when the lease clock
                    # lapsed, because expiry recovery re-tokens the row; only a
                    # superseded token is rejected.
                attempts = int(row["attempts"]) + (0 if str(row["status"]) == "leased" else 1)
                connection.execute(
                    """
                    UPDATE distillation_outbox
                       SET status = ?, processed_at = ?, seed_id = ?,
                           registry_id = ?, strategy_id = ?, version = ?, checksum = ?,
                           last_error = ?, skip_reason = ?,
                           attempts = ?, lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, updated_at = ?
                      WHERE job_id = ?
                    """,
                    (
                        status.value,
                        now,
                        seed_id,
                        registry_id,
                        strategy_id,
                        version,
                        checksum,
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
                        result_ref, registry_id, strategy_id, version, checksum,
                        last_error, applied_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (event_id) DO UPDATE SET
                        status = excluded.status,
                        attempts = excluded.attempts,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        result_ref = excluded.result_ref,
                        registry_id = excluded.registry_id,
                        strategy_id = excluded.strategy_id,
                        version = excluded.version,
                        checksum = excluded.checksum,
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
                        strategy_id,
                        version,
                        checksum,
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
        strategy_id: str | None = None,
        version: str | None = None,
        checksum: str | None = None,
        processed_at: str | datetime | float | None = None,
        claim_token: str | None = None,
    ) -> None:
        self._terminal_update(
            job_id=job_id,
            status=DistillationJobStatus.DONE,
            processed_at=processed_at,
            seed_id=seed_id,
            registry_id=registry_id,
            strategy_id=strategy_id,
            version=version,
            checksum=checksum,
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
        strategy_id: str | None = None,
        version: str | None = None,
        checksum: str | None = None,
        processed_at: str | datetime | float | None = None,
        claim_token: str | None = None,
    ) -> None:
        self._terminal_update(
            job_id=job_id,
            status=DistillationJobStatus.SKIPPED,
            processed_at=processed_at,
            seed_id=seed_id,
            registry_id=registry_id,
            strategy_id=strategy_id,
            version=version,
            checksum=checksum,
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
                    raise DistillationLeaseError(f"stale distillation lease failure: {job.job_id}")
                attempts = int(row["attempts"])
                # Unavailable model/adapter (card or compiler proposer) uses the long schedule of auth outages.
                extraction_failure = error.startswith(
                    (EXTRACTION_UNAVAILABLE_MARKER, COMPILER_FAILED_MARKER)
                )
                auth_failure = is_auth_registry_failure(error) or extraction_failure
                attempt_limit = int(row["max_attempts"])
                if auth_failure:
                    attempt_limit = max(attempt_limit, AUTH_RETRY_MAX_ATTEMPTS)
                was_extraction_dead_letter = False
                if extraction_failure:
                    dl_row = connection.execute(
                        "SELECT redrive_count FROM distillation_dead_letters WHERE job_id = ?",
                        (job.job_id,),
                    ).fetchone()
                    was_extraction_dead_letter = dl_row is not None and int(dl_row["redrive_count"]) > 0
                dead_letter = permanent or was_extraction_dead_letter or attempts >= attempt_limit
                status = (
                    DistillationJobStatus.DEAD_LETTER
                    if dead_letter
                    else DistillationJobStatus.RETRY_WAIT
                )
                if auth_failure:
                    delay = min(
                        AUTH_RETRY_MAX_DELAY_SECONDS,
                        max(float(base_delay_seconds), AUTH_RETRY_MIN_BASE_SECONDS)
                        * (2 ** max(0, attempts - 1)),
                    )
                else:
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
                           registry_id = NULL, strategy_id = NULL,
                           version = NULL, checksum = NULL,
                           last_error = NULL, skip_reason = NULL,
                           deferred_since = NULL, priority_reason = NULL, demand_score = 0,
                           lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, lease_expiries = 0,
                           lease_started = 0, updated_at = ?
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

    def redrive_transient_dead_letters(
        self,
        *,
        requested_by: str,
        note: str = "transient registry failure redrive",
        max_redrives: int = DEFAULT_MAX_TRANSIENT_REDRIVES,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[str]:
        """Re-queue unresolved dead letters whose cause was a transient Registry error.

        Each redrive is one ``BEGIN IMMEDIATE`` transaction that only touches
        rows still ``dead_letter`` in the outbox, so concurrent or repeated
        calls cannot redrive the same dead letter twice. A dead letter is
        redriven at most ``max_redrives`` times; permanent failures and
        exhausted budgets stay dead-lettered. Returns the redriven job ids.
        When ``tenant_id`` is given only jobs whose source version carries that
        tenant are redriven; unowned or foreign jobs are left untouched.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        now = self._now()
        redriven: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                tenant_clause = ""
                params: tuple[Any, ...] = (max(0, int(max_redrives)),)
                if tenant is not None:
                    tenant_clause = """
                       AND EXISTS (
                           SELECT 1
                             FROM distillation_outbox AS outbox
                             JOIN distillation_source_versions AS source_version
                               ON source_version.source_id = outbox.source_id
                              AND source_version.source_digest = outbox.source_digest
                            WHERE outbox.job_id = distillation_dead_letters.job_id
                              AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?
                       )
                    """
                    params = (max(0, int(max_redrives)), tenant)
                rows = connection.execute(
                    f"""
                    SELECT job_id, reason
                      FROM distillation_dead_letters
                     WHERE resolved_at IS NULL AND redrive_count < ?
                       {tenant_clause}
                     ORDER BY created_at, job_id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    if len(redriven) >= max(0, int(limit)):
                        break
                    if not is_transient_registry_failure(str(row["reason"])):
                        continue
                    job_id = str(row["job_id"])
                    changed = connection.execute(
                        """
                        UPDATE distillation_outbox
                           SET status = 'pending', available_at = ?,
                               processed_at = NULL, attempts = 0,
                               lease_owner = NULL, lease_token = NULL,
                               lease_expires_at = NULL, lease_expiries = 0,
                               lease_started = 0, updated_at = ?
                         WHERE job_id = ? AND status = 'dead_letter'
                        """,
                        (now, now, job_id),
                    ).rowcount
                    if changed != 1:
                        continue
                    connection.execute(
                        """
                        UPDATE distillation_inbox
                           SET status = 'retry_wait', updated_at = ?
                         WHERE job_id = ? AND status = 'dead_letter'
                        """,
                        (now, job_id),
                    )
                    connection.execute(
                        """
                        UPDATE distillation_dead_letters
                           SET redrive_count = redrive_count + 1,
                               last_redrive_actor = ?,
                               last_redrive_note = ?,
                               resolved_at = ?
                         WHERE job_id = ? AND resolved_at IS NULL
                        """,
                        (requested_by, note, now, job_id),
                    )
                    redriven.append(job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return redriven

    def redrive_auth_dead_letters(
        self,
        *,
        requested_by: str,
        note: str = "registry accepts worker credential again; auth failure redrive",
        max_redrives: int = DEFAULT_MAX_TRANSIENT_REDRIVES,
        limit: int = DEFAULT_AUTH_REDRIVE_LIMIT,
        tenant_id: str | None = None,
    ) -> dict[str, list[str]]:
        """Recover dead letters caused by Registry 401/403 rejections.

        The caller must only invoke this after a Registry probe made with the
        worker's own credential was answered as authenticated. Per source the
        decision is made on the LATEST outbox row (never on the dead-lettered
        job's own source_id lookup): an older version is resolved as
        superseded (outbox ``skipped``, dead letter resolved with a note) and
        never delivered; the newest version is redriven when it is itself an
        auth dead letter with budget left. At most ``limit`` jobs are
        redriven per call; a dead letter whose ``redrive_count`` reached
        ``max_redrives`` is not redriven. No schema change and no new rows.
        Returns ``{"redriven": [...], "superseded": [...]}`` job ids.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        budget = max(0, int(max_redrives))
        cap = max(0, int(limit))
        now = self._now()
        redriven: list[str] = []
        superseded: list[str] = []
        tenant_clause = ""
        params: tuple[Any, ...] = ()
        if tenant is not None:
            tenant_clause = """
               AND EXISTS (
                   SELECT 1
                     FROM distillation_outbox AS outbox
                     JOIN distillation_source_versions AS source_version
                       ON source_version.source_id = outbox.source_id
                      AND source_version.source_digest = outbox.source_digest
                    WHERE outbox.job_id = distillation_dead_letters.job_id
                      AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?
               )
            """
            params = (tenant,)

        def redrive(connection: sqlite3.Connection, job_id: str) -> bool:
            changed = connection.execute(
                """
                UPDATE distillation_outbox
                   SET status = 'pending', available_at = ?,
                       processed_at = NULL, attempts = 0,
                       lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, lease_expiries = 0,
                       lease_started = 0, updated_at = ?
                 WHERE job_id = ? AND status = 'dead_letter'
                """,
                (now, now, job_id),
            ).rowcount
            if changed != 1:
                return False
            connection.execute(
                """
                UPDATE distillation_inbox
                   SET status = 'retry_wait', updated_at = ?
                 WHERE job_id = ? AND status = 'dead_letter'
                """,
                (now, job_id),
            )
            connection.execute(
                """
                UPDATE distillation_dead_letters
                   SET redrive_count = redrive_count + 1,
                       last_redrive_actor = ?, last_redrive_note = ?,
                       resolved_at = ?
                 WHERE job_id = ? AND resolved_at IS NULL
                """,
                (requested_by, note, now, job_id),
            )
            return True

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT job_id, source_id, reason, redrive_count
                      FROM distillation_dead_letters
                     WHERE resolved_at IS NULL
                       {tenant_clause}
                     ORDER BY created_at, job_id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    if not is_auth_registry_failure(str(row["reason"])):
                        continue
                    job_id = str(row["job_id"])
                    if job_id in redriven:
                        continue
                    latest = connection.execute(
                        """
                        SELECT job_id, status
                          FROM distillation_outbox
                         WHERE source_id = ?
                         ORDER BY enqueued_at DESC, rowid DESC
                         LIMIT 1
                        """,
                        (str(row["source_id"]),),
                    ).fetchone()
                    if latest is None:
                        continue
                    latest_id = str(latest["job_id"])
                    if latest_id == job_id:
                        if (
                            len(redriven) < cap
                            and int(row["redrive_count"]) < budget
                            and redrive(connection, job_id)
                        ):
                            redriven.append(job_id)
                        continue
                    # A newer version of this source exists: the stale one is
                    # never delivered. Resolve it, then handle the source once
                    # through its newest version.
                    note_text = f"superseded by newer version {latest_id} ({latest['status']})"
                    changed = connection.execute(
                        """
                        UPDATE distillation_outbox
                           SET status = 'skipped', processed_at = ?,
                               skip_reason = ?, lease_owner = NULL,
                               lease_token = NULL, lease_expires_at = NULL,
                               updated_at = ?
                         WHERE job_id = ? AND status = 'dead_letter'
                        """,
                        (now, note_text, now, job_id),
                    ).rowcount
                    if changed == 1:
                        connection.execute(
                            """
                            UPDATE distillation_inbox
                               SET status = 'skipped', updated_at = ?
                             WHERE job_id = ? AND status = 'dead_letter'
                            """,
                            (now, job_id),
                        )
                        connection.execute(
                            """
                            UPDATE distillation_dead_letters
                               SET last_redrive_actor = ?, last_redrive_note = ?,
                                   resolved_at = ?
                             WHERE job_id = ? AND resolved_at IS NULL
                            """,
                            (requested_by, note_text, now, job_id),
                        )
                        superseded.append(job_id)
                    if str(latest["status"]) != "dead_letter" or len(redriven) >= cap:
                        continue
                    newest = connection.execute(
                        """
                        SELECT reason, redrive_count
                          FROM distillation_dead_letters
                         WHERE job_id = ? AND resolved_at IS NULL
                        """,
                        (latest_id,),
                    ).fetchone()
                    if (
                        newest is not None
                        and is_auth_registry_failure(str(newest["reason"]))
                        and int(newest["redrive_count"]) < budget
                        and redrive(connection, latest_id)
                    ):
                        redriven.append(latest_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {"redriven": redriven, "superseded": superseded}

    def _redrive_dead_letter_row(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        requested_by: str,
        note: str,
        now: float,
        defect_reattempted: bool = False,
        priority_reason: str | None = None,
    ) -> bool:
        if defect_reattempted:
            changed = connection.execute(
                """
                UPDATE distillation_outbox
                   SET status = 'pending', available_at = ?,
                       processed_at = NULL, attempts = 0,
                       lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, lease_expiries = 0,
                       lease_started = 0, defect_reattempted = 1,
                       priority_reason = COALESCE(?, priority_reason),
                       updated_at = ?
                 WHERE job_id = ? AND status = 'dead_letter'
                """,
                (now, priority_reason, now, job_id),
            ).rowcount
        else:
            changed = connection.execute(
                """
                UPDATE distillation_outbox
                   SET status = 'pending', available_at = ?,
                       processed_at = NULL, attempts = 0,
                       lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, lease_expiries = 0,
                       lease_started = 0, updated_at = ?
                 WHERE job_id = ? AND status = 'dead_letter'
                """,
                (now, now, job_id),
            ).rowcount
        if changed != 1:
            return False
        connection.execute(
            """UPDATE distillation_inbox SET status = 'retry_wait', updated_at = ?
                WHERE job_id = ? AND status = 'dead_letter'""",
            (now, job_id),
        )
        connection.execute(
            """
            UPDATE distillation_dead_letters
               SET redrive_count = redrive_count + 1,
                   last_redrive_actor = ?, last_redrive_note = ?,
                   resolved_at = ?
             WHERE job_id = ? AND resolved_at IS NULL
            """,
            (requested_by, note, now, job_id),
        )
        return True

    def _close_row_skipped(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        from_status: str,
        skip_reason: str,
        now: float,
    ) -> bool:
        changed = connection.execute(
            """
            UPDATE distillation_outbox
               SET status = 'skipped', processed_at = ?, skip_reason = ?,
                   lease_owner = NULL, lease_token = NULL,
                   lease_expires_at = NULL, updated_at = ?
             WHERE job_id = ? AND status = ?
            """,
            (now, skip_reason, now, job_id, from_status),
        ).rowcount
        if changed != 1:
            return False
        connection.execute(
            """UPDATE distillation_inbox SET status = 'skipped', updated_at = ?
                WHERE job_id = ? AND status IN ('dead_letter', 'retry_wait', 'pending')""",
            (now, job_id),
        )
        return True

    def _newer_terminal_outbox_version(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        source_id: str,
    ) -> sqlite3.Row | None:
        newer = connection.execute(
            """
            SELECT newer.job_id, newer.status, newer.defect_reattempted
              FROM distillation_outbox AS current
              JOIN distillation_outbox AS newer
                ON newer.source_id = current.source_id
               AND (newer.enqueued_at > current.enqueued_at
                    OR (newer.enqueued_at = current.enqueued_at
                        AND newer.rowid > current.rowid))
             WHERE current.job_id = ?
               AND current.source_id = ?
               AND (newer.status IN ('done', 'skipped', 'failed', 'dead_letter')
                    OR newer.defect_reattempted > 0)
             ORDER BY newer.enqueued_at DESC, newer.rowid DESC
             LIMIT 1
            """,
            (job_id, source_id),
        ).fetchone()
        if newer is not None:
            return newer
        current_exists = connection.execute(
            "SELECT 1 FROM distillation_outbox WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if current_exists is not None:
            return None
        return connection.execute(
            """
            SELECT job_id, status, defect_reattempted
              FROM distillation_outbox
             WHERE source_id = ? AND job_id <> ?
               AND (status IN ('done', 'skipped', 'failed', 'dead_letter')
                    OR defect_reattempted > 0)
             ORDER BY enqueued_at DESC, rowid DESC
             LIMIT 1
            """,
            (source_id, job_id),
        ).fetchone()

    def _resolve_superseded_dead_letter_row(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        newer_job_id: str,
        newer_status: str,
        requested_by: str,
        now: float,
    ) -> bool:
        status_label = (
            newer_status
            if newer_status in ("done", "skipped", "failed", "dead_letter")
            else "skipped"
        )
        note_text = f"superseded by newer version {newer_job_id} ({status_label})"
        self._close_row_skipped(
            connection,
            job_id,
            from_status="dead_letter",
            skip_reason=note_text,
            now=now,
        )
        changed = connection.execute(
            """
            UPDATE distillation_dead_letters
               SET last_redrive_actor = ?,
                   last_redrive_note = ?,
                   resolved_at = ?
             WHERE job_id = ? AND resolved_at IS NULL
            """,
            (requested_by, note_text, now, job_id),
        ).rowcount
        return changed == 1

    def resolve_superseded_dead_letters(
        self,
        *,
        requested_by: str,
        limit: int = DEFAULT_SUPERSEDED_RESOLVE_LIMIT,
        tenant_id: str | None = None,
        include_auth: bool = True,
        now: str | datetime | float | None = None,
    ) -> list[str]:
        """Resolve dead letters whose source has a newer outbox version in a terminal state.

        Uses the same owner resolution path as auth recovery: the stale outbox
        row is closed ``skipped`` with ``superseded by newer version <id> (<status>)``
        and the dead-letter row records ``last_redrive_actor``, ``last_redrive_note``
        and ``resolved_at`` while remaining readable in ``distillation_dead_letters``.
        Bounded by ``limit`` per call.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        cap = max(0, int(limit))
        effective_now = _timestamp(now) if now is not None else self._now()
        superseded: list[str] = []
        tenant_clause = ""
        params: tuple[Any, ...] = ()
        if tenant is not None:
            tenant_clause = """
               AND EXISTS (
                   SELECT 1
                     FROM distillation_outbox AS outbox
                     JOIN distillation_source_versions AS source_version
                       ON source_version.source_id = outbox.source_id
                      AND source_version.source_digest = outbox.source_digest
                    WHERE outbox.job_id = distillation_dead_letters.job_id
                      AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?
               )
            """
            params = (tenant,)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT job_id, source_id, reason
                      FROM distillation_dead_letters
                     WHERE resolved_at IS NULL
                       {tenant_clause}
                     ORDER BY created_at, job_id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    if len(superseded) >= cap:
                        break
                    if not include_auth and is_auth_registry_failure(str(row["reason"])):
                        continue
                    job_id = str(row["job_id"])
                    source_id = str(row["source_id"])
                    newer = self._newer_terminal_outbox_version(
                        connection, job_id=job_id, source_id=source_id,
                    )
                    if newer is None:
                        continue
                    if self._resolve_superseded_dead_letter_row(
                        connection,
                        job_id=job_id,
                        newer_job_id=str(newer["job_id"]),
                        newer_status=str(newer["status"]),
                        requested_by=requested_by,
                        now=effective_now,
                    ):
                        superseded.append(job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return superseded

    def redrive_extraction_unavailable_dead_letters(
        self,
        *,
        requested_by: str,
        note: str = "l2 extractor configured and answering; extraction-unavailable dead-letter redrive",
        max_redrives: int = MAX_RECORD_ATTEMPTS_TOTAL,
        limit: int = DEFAULT_EXTRACTION_REDRIVE_LIMIT,
        tenant_id: str | None = None,
        ledger: ExtractionLedger | None = None,
        now: str | datetime | float | None = None,
        exclude_job_ids: Sequence[str] | set[str] | frozenset[str] = (),
    ) -> dict[str, list[str]]:
        """Recover dead letters caused by ``hypothesis_extraction_unavailable``.

        A dead letter whose source has a newer outbox version in a terminal
        state is resolved as superseded without making a model call. A dead
        letter that is still the newest version of its source is redriven when
        within ``max_redrives``, the daily call cap, and the per-record attempt
        limits (3/day, 6/total) checked via ``ledger``.
        Returns ``{"redriven": [...], "superseded": [...]}`` job ids.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        budget = max(0, int(max_redrives))
        cap = max(0, int(limit))
        effective_now = _timestamp(now) if now is not None else self._now()
        excluded = {str(jid) for jid in exclude_job_ids}
        redriven: list[str] = []
        superseded: list[str] = []
        tenant_clause = ""
        params: tuple[Any, ...] = ()
        if tenant is not None:
            tenant_clause = """
               AND EXISTS (
                   SELECT 1
                     FROM distillation_outbox AS outbox
                     JOIN distillation_source_versions AS source_version
                       ON source_version.source_id = outbox.source_id
                      AND source_version.source_digest = outbox.source_digest
                    WHERE outbox.job_id = distillation_dead_letters.job_id
                      AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?
               )
            """
            params = (tenant,)
        remaining_fn = getattr(ledger, "remaining_daily_calls", None) if ledger is not None else None
        remaining_daily = remaining_fn() if callable(remaining_fn) else None
        can_attempt_fn = getattr(ledger, "can_attempt", None) if ledger is not None else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT job_id, source_id, source_digest, reason, redrive_count
                      FROM distillation_dead_letters
                     WHERE resolved_at IS NULL
                       {tenant_clause}
                     ORDER BY created_at, job_id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    reason_text = str(row["reason"])
                    is_unavail = is_extraction_unavailable_failure(reason_text)
                    defect = None if is_unavail else fixed_defect_for_skip_reason(reason_text)
                    if not is_unavail and defect is None:
                        continue
                    job_id = str(row["job_id"])
                    source_id = str(row["source_id"])
                    if job_id in excluded or job_id in redriven or job_id in superseded:
                        continue
                    newer = self._newer_terminal_outbox_version(
                        connection, job_id=job_id, source_id=source_id,
                    )
                    if newer is not None:
                        if self._resolve_superseded_dead_letter_row(
                            connection,
                            job_id=job_id,
                            newer_job_id=str(newer["job_id"]),
                            newer_status=str(newer["status"]),
                            requested_by=requested_by,
                            now=effective_now,
                        ):
                            superseded.append(job_id)
                        continue
                    latest = connection.execute(
                        """
                        SELECT job_id, source_digest, defect_reattempted
                          FROM distillation_outbox
                         WHERE source_id = ?
                         ORDER BY enqueued_at DESC, rowid DESC
                         LIMIT 1
                        """,
                        (source_id,),
                    ).fetchone()
                    if latest is None or str(latest["job_id"]) != job_id:
                        continue
                    row_budget = min(budget, 1) if defect is not None else budget
                    if len(redriven) >= cap or int(row["redrive_count"]) >= row_budget:
                        continue
                    if defect is not None and int(latest["defect_reattempted"] or 0) > 0:
                        continue
                    if remaining_daily is not None and len(redriven) >= remaining_daily:
                        continue
                    digest = str(latest["source_digest"] or row["source_digest"] or "")
                    if callable(can_attempt_fn) and can_attempt_fn(source_id, digest) != RESERVED:
                        continue
                    redrive_note = (
                        f"fixed_defect_reattempt:{defect.code}:{requested_by}"
                        if defect is not None
                        else note
                    )
                    if self._redrive_dead_letter_row(
                        connection,
                        job_id,
                        requested_by=requested_by,
                        note=redrive_note,
                        now=effective_now,
                        defect_reattempted=(defect is not None),
                        priority_reason=redrive_note if defect is not None else None,
                    ):
                        redriven.append(job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {"redriven": redriven, "superseded": superseded}

    def reattempt_fixed_defect_records(
        self,
        *,
        requested_by: str,
        limit: int = DEFAULT_DEFECT_REATTEMPT_LIMIT,
        tenant_id: str | None = None,
        now: str | datetime | float | None = None,
        ledger: ExtractionLedger | None = None,
        exclude_job_ids: Sequence[str] | set[str] | frozenset[str] = (),
    ) -> list[str]:
        """Re-open at most once records whose newest version was closed by a fixed defect.

        Only ``skipped`` rows with ``defect_reattempted = 0`` that are still the
        newest outbox version of their source and whose ``skip_reason`` matches
        ``FIXED_DEFECT_REATTEMPT_OUTCOMES`` are eligible. Irrelevant,
        admission-denied (redaction) and model-abstained records never match.
        Stamped with ``defect_reattempted = 1`` so ``claim_due`` orders them
        after fresh records and they are never re-opened a second time.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        cap = max(0, int(limit))
        if cap == 0:
            return []
        effective_now = _timestamp(now) if now is not None else self._now()
        excluded = {str(jid) for jid in exclude_job_ids}
        remaining_fn = getattr(ledger, "remaining_daily_calls", None) if ledger is not None else None
        remaining_daily = remaining_fn() if callable(remaining_fn) else None
        can_attempt_fn = getattr(ledger, "can_attempt", None) if ledger is not None else None
        if remaining_daily is not None and remaining_daily <= 0:
            return []
        tenant_clause = ""
        params: tuple[Any, ...] = ()
        if tenant is not None:
            tenant_clause = """
               AND EXISTS (
                   SELECT 1
                     FROM distillation_source_versions AS source_version
                    WHERE source_version.source_id = distillation_outbox.source_id
                      AND source_version.source_digest = distillation_outbox.source_digest
                      AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?
               )
            """
            params = (tenant,)
        reopened: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT job_id, source_id, source_digest, skip_reason
                      FROM distillation_outbox
                     WHERE status = 'skipped'
                       AND defect_reattempted = 0
                       AND pruned_at IS NULL
                       AND (skip_reason LIKE 'hypothesis_extraction_failed:%'
                            OR skip_reason LIKE 'hypothesis_compiler_failed:%')
                       {tenant_clause}
                       AND NOT EXISTS (
                           SELECT 1
                             FROM distillation_outbox AS newer
                            WHERE newer.source_id = distillation_outbox.source_id
                              AND (newer.enqueued_at > distillation_outbox.enqueued_at
                                   OR (newer.enqueued_at = distillation_outbox.enqueued_at
                                       AND newer.rowid > distillation_outbox.rowid))
                       )
                     ORDER BY enqueued_at, job_id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    if len(reopened) >= cap:
                        break
                    if remaining_daily is not None and len(reopened) >= remaining_daily:
                        break
                    defect = fixed_defect_for_skip_reason(row["skip_reason"])
                    if defect is None:
                        continue
                    job_id = str(row["job_id"])
                    if job_id in excluded:
                        continue
                    source_id = str(row["source_id"])
                    digest = str(row["source_digest"] or "")
                    if digest:
                        payload_row = connection.execute(
                            """
                            SELECT payload_json
                              FROM distillation_source_versions
                             WHERE source_id = ? AND source_digest = ?
                            """,
                            (source_id, digest),
                        ).fetchone()
                        if payload_row is None or str(payload_row["payload_json"]) == PRUNED_PAYLOAD_JSON:
                            continue
                    if callable(can_attempt_fn) and can_attempt_fn(source_id, digest) != RESERVED:
                        continue
                    changed = connection.execute(
                        """
                        UPDATE distillation_outbox
                           SET status = 'pending',
                               available_at = ?,
                               processed_at = NULL,
                               seed_id = NULL,
                               registry_id = NULL,
                               strategy_id = NULL,
                               version = NULL,
                               checksum = NULL,
                               last_error = NULL,
                               skip_reason = NULL,
                               attempts = 0,
                               deferred_since = NULL,
                               priority_reason = ?,
                               demand_score = 0,
                               lease_owner = NULL,
                               lease_token = NULL,
                               lease_expires_at = NULL,
                               lease_expiries = 0,
                               lease_started = 0,
                               defect_reattempted = 1,
                               updated_at = ?
                         WHERE job_id = ? AND status = 'skipped' AND defect_reattempted = 0
                        """,
                        (
                            effective_now,
                            f"fixed_defect_reattempt:{defect.code}:{requested_by}",
                            effective_now,
                            job_id,
                        ),
                    ).rowcount
                    if changed != 1:
                        continue
                    connection.execute(
                        """
                        UPDATE distillation_inbox
                           SET status = 'retry_wait',
                               attempts = 0,
                               lease_owner = NULL,
                               lease_token = NULL,
                               lease_expires_at = NULL,
                               result_ref = NULL,
                               last_error = NULL,
                               applied_at = NULL,
                               updated_at = ?
                         WHERE job_id = ? AND status = 'skipped'
                        """,
                        (effective_now, job_id),
                    )
                    reopened.append(job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return reopened

    def redrive_lease_expired_dead_letters(
        self,
        *,
        requested_by: str,
        note: str = "worker completed normal work after restart; lease-expired redrive",
        max_redrives: int = DEFAULT_MAX_LEASE_REDRIVES,
        limit: int = DEFAULT_LEASE_REDRIVE_LIMIT,
        tenant_id: str | None = None,
    ) -> list[str]:
        """Re-queue ``lease_expired_repeatedly`` dead letters, bounded.

        At most ``limit`` jobs per call and ``max_redrives`` per job. The caller
        only invokes this once the worker completed normal work after a
        restart, so a still-crashing worker does not churn the queue. A job
        that exhausts its leases again is dead-lettered again by
        ``_recover_expired`` with its ``redrive_count`` kept. Only the newest
        outbox version of a source is redriven; an older one whose source has a
        newer terminal version is resolved as superseded.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        budget, cap = max(0, int(max_redrives)), max(0, int(limit))
        now = self._now()
        redriven: list[str] = []
        tenant_clause, params = "", (budget,)
        if tenant is not None:
            tenant_clause = """
               AND EXISTS (
                   SELECT 1 FROM distillation_outbox AS outbox
                     JOIN distillation_source_versions AS source_version
                       ON source_version.source_id = outbox.source_id
                      AND source_version.source_digest = outbox.source_digest
                    WHERE outbox.job_id = distillation_dead_letters.job_id
                      AND json_extract(source_version.payload_json, '$.metadata.tenant_id') = ?)
            """
            params = (budget, tenant)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT job_id, source_id, reason
                      FROM distillation_dead_letters
                     WHERE resolved_at IS NULL AND redrive_count < ?
                       AND reason LIKE '{LEASE_EXPIRED_REASON}%'
                       {tenant_clause}
                     ORDER BY redrive_count, created_at, job_id
                     LIMIT ?
                    """,
                    (*params, cap * 4 + 1),
                ).fetchall()
                for row in rows:
                    job_id = str(row["job_id"])
                    source_id = str(row["source_id"])
                    newer = self._newer_terminal_outbox_version(
                        connection, job_id=job_id, source_id=source_id,
                    )
                    if newer is not None:
                        self._resolve_superseded_dead_letter_row(
                            connection,
                            job_id=job_id,
                            newer_job_id=str(newer["job_id"]),
                            newer_status=str(newer["status"]),
                            requested_by=requested_by,
                            now=now,
                        )
                        continue
                    if len(redriven) >= cap:
                        break
                    latest = connection.execute(
                        """SELECT job_id FROM distillation_outbox WHERE source_id = ?
                            ORDER BY enqueued_at DESC, rowid DESC LIMIT 1""",
                        (source_id,),
                    ).fetchone()
                    if latest is None or str(latest["job_id"]) != job_id:
                        continue
                    if self._redrive_dead_letter_row(
                        connection, job_id,
                        requested_by=requested_by, note=note, now=now,
                    ):
                        redriven.append(job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return redriven

    def settle_lineage_conflict_dead_letters(
        self,
        *,
        requested_by: str,
        limit: int = DEFAULT_LEASE_REDRIVE_LIMIT,
        tenant_id: str | None = None,
    ) -> dict[str, list[str]]:
        """Give Registry lineage-conflict dead letters a terminal, readable end.

        A conflict dead-lettered before the replay-provenance fix is replayed
        once (the fix may now accept it). A conflict that is dead-lettered
        again after that replay is a genuine disagreement: the outbox row is
        closed ``skipped`` with ``registry_lineage_conflict_unresolved`` and
        the Registry entry id, the dead letter is resolved with the same note.
        No Registry entry is read for overwrite, written or checksummed here.
        Returns ``{"replayed": [...], "closed": [...]}``.
        """
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise ValueError("tenant_id must be a non-empty explicit tenant")
        cap = max(0, int(limit))
        now = self._now()
        replayed: list[str] = []
        closed: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """SELECT d.job_id, d.reason, d.redrive_count, d.payload_json
                         FROM distillation_dead_letters AS d
                        WHERE d.resolved_at IS NULL ORDER BY d.created_at, d.job_id"""
                ).fetchall()
                for row in rows:
                    if len(replayed) + len(closed) >= cap:
                        break
                    reason = str(row["reason"])
                    if not is_lineage_conflict_failure(reason):
                        continue
                    job_id = str(row["job_id"])
                    if tenant is not None:
                        try:
                            scope = (json.loads(row["payload_json"]).get("metadata") or {}).get("tenant_id")
                        except (ValueError, AttributeError):
                            scope = None
                        if scope != tenant:
                            continue
                    if int(row["redrive_count"]) < 1:
                        if self._redrive_dead_letter_row(
                            connection, job_id, requested_by=requested_by,
                            note="lineage conflict replayed once under replay-provenance fix",
                            now=now,
                        ):
                            replayed.append(job_id)
                        continue
                    text = f"{LINEAGE_CONFLICT_DISPOSITION}: {reason}"[:1000]
                    if self._close_row_skipped(
                        connection, job_id, from_status="dead_letter",
                        skip_reason=text, now=now,
                    ):
                        connection.execute(
                            """UPDATE distillation_dead_letters
                                  SET last_redrive_actor = ?, last_redrive_note = ?,
                                      resolved_at = ?
                                WHERE job_id = ? AND resolved_at IS NULL""",
                            (requested_by, text, now, job_id),
                        )
                        closed.append(job_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {"replayed": replayed, "closed": closed}

    def close_retired_tenant_pending(
        self,
        *,
        retired_tenant_ids: Sequence[str],
        active_tenant_id: str,
        enqueued_from: float,
        enqueued_to: float,
        requested_by: str,
        limit: int = 2000,
    ) -> dict[str, Any]:
        """Close pending rows of a retired runtime as out of scope.

        Refuses unless the tenant is provably retired in this queue: it is not
        the active tenant, and no row of it was enqueued after ``enqueued_to``
        (a tenant still producing work is not retired). Only ``pending`` rows
        enqueued inside ``[enqueued_from, enqueued_to]`` are closed, as
        ``skipped`` with ``out_of_scope_retired_runtime``; leased, retry and
        dead-letter rows and every other tenant are untouched.
        """
        retired = sorted({str(t).strip() for t in retired_tenant_ids if str(t).strip()})
        active = str(active_tenant_id or "").strip()
        result: dict[str, Any] = {"closed": [], "refused": {}}
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for tenant in retired:
                    if tenant == active:
                        result["refused"][tenant] = "tenant_is_active"
                        continue
                    later = connection.execute(
                        """SELECT COUNT(*) FROM distillation_outbox AS o
                             JOIN distillation_source_versions AS v
                               ON v.source_id = o.source_id AND v.source_digest = o.source_digest
                            WHERE json_extract(v.payload_json, '$.metadata.tenant_id') = ?
                              AND o.enqueued_at > ?""",
                        (tenant, float(enqueued_to)),
                    ).fetchone()[0]
                    if later:
                        result["refused"][tenant] = "tenant_enqueued_after_window"
                        continue
                    rows = connection.execute(
                        """SELECT o.job_id FROM distillation_outbox AS o
                             JOIN distillation_source_versions AS v
                               ON v.source_id = o.source_id AND v.source_digest = o.source_digest
                            WHERE o.status = 'pending'
                              AND json_extract(v.payload_json, '$.metadata.tenant_id') = ?
                              AND o.enqueued_at BETWEEN ? AND ?
                            ORDER BY o.enqueued_at LIMIT ?""",
                        (tenant, float(enqueued_from), float(enqueued_to), max(0, int(limit))),
                    ).fetchall()
                    reason = f"{OUT_OF_SCOPE_RETIRED_RUNTIME_REASON}: tenant={tenant} by {requested_by}"
                    for row in rows:
                        if self._close_row_skipped(
                            connection, str(row["job_id"]), from_status="pending",
                            skip_reason=reason, now=now,
                        ):
                            result["closed"].append(str(row["job_id"]))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return result

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
            "deferred": 0,
            "extraction_retry": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                  FROM distillation_outbox
                 GROUP BY status
                """
            ).fetchall()
            # Every planned deferral, including ``extraction_tick_budget``
            # (released for the next tick without ``deferred_since``).
            reasons = sorted(PLANNED_DEFERRAL_REASONS)
            metrics["deferred"] = int(connection.execute(
                f"""SELECT COUNT(*) FROM distillation_outbox WHERE status = 'retry_wait'
                   AND last_error IN ({", ".join("?" for _ in reasons)})""",
                reasons,
            ).fetchone()[0])
            # Extraction provider failures waiting for their retry: the model
            # or adapter did not answer, the Registry was never called.
            metrics["extraction_retry"] = int(connection.execute(
                """SELECT COUNT(*) FROM distillation_outbox WHERE status = 'retry_wait'
                   AND (substr(last_error, 1, ?) = ? OR substr(last_error, 1, ?) = ?)""",
                (
                    len(EXTRACTION_UNAVAILABLE_MARKER),
                    EXTRACTION_UNAVAILABLE_MARKER,
                    len(COMPILER_FAILED_MARKER),
                    COMPILER_FAILED_MARKER,
                ),
            ).fetchone()[0])
        for row in rows:
            metrics[str(row["status"])] = int(row["count"])
        return metrics

    def external_text_closure_counts(self) -> dict[str, dict[str, int]]:
        """Skipped jobs by source type then typed closure code (all tenants).

        Reads the trigger-kept ``distillation_terminal_counts`` table, the
        single per-source/per-reason accounting of RC2 closures; the
        tenant-scoped discovery summary reads the same table.
        """
        counts: dict[str, dict[str, int]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT source_type, reason, SUM(n) AS count
                  FROM {TERMINAL_COUNTS_TABLE}
                 WHERE status = 'skipped' AND reason <> 'reason_not_recorded'
                 GROUP BY 1, 2 HAVING SUM(n) > 0
                """
            ).fetchall()
        for row in rows:
            by_reason = counts.setdefault(str(row["source_type"] or "unknown"), {})
            by_reason[str(row["reason"])] = int(row["count"])
        return counts

    def list_dead_letters(
        self,
        *,
        include_payload: bool = True,
        limit: int | None = None,
        include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        columns = "*" if include_payload else (
            "job_id, event_id, source_id, source_digest, attempts, reason, "
            "created_at, redrive_count, last_redrive_actor, last_redrive_note, resolved_at"
        )
        where_clause = "" if include_resolved else "WHERE resolved_at IS NULL"
        sql = f"""
                SELECT {columns}
                  FROM distillation_dead_letters
                 {where_clause}
                 ORDER BY created_at, job_id
                """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(0, int(limit)),)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def dead_letter_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM distillation_dead_letters WHERE resolved_at IS NULL"
            ).fetchone()[0])

    def recent_terminal_drafts(
        self, *, limit: int = 200, excluded_skip_reason: str | None = None,
    ) -> list[DistillationJob]:
        """Most recent done/skipped jobs that reached Registry, bounded by ``limit``.

        Reads through the (status, processed_at) index inside the retention
        window, so the cost does not grow with total history.
        """
        jobs: list[DistillationJob] = []
        with self._connect() as connection:
            for status in ("done", "skipped"):
                rows = connection.execute(
                    """SELECT * FROM distillation_outbox
                        WHERE status = ? AND pruned_at IS NULL
                          AND registry_id IS NOT NULL AND registry_id <> ''
                          AND (skip_reason IS NULL OR skip_reason <> ?)
                        ORDER BY processed_at DESC LIMIT ?""",
                    (status, excluded_skip_reason or "", max(0, int(limit))),
                ).fetchall()
                jobs.extend(self._job_from_row(row) for row in rows)
        jobs.sort(key=lambda job: job.processed_at or "", reverse=True)
        return jobs[:max(0, int(limit))]

    def prune(
        self,
        *,
        now: float | None = None,
        terminal_retention_seconds: float = DEFAULT_TERMINAL_RETENTION_SECONDS,
        dead_letter_retention_seconds: float = DEFAULT_DEAD_LETTER_RETENTION_SECONDS,
        batch: int = 500,
    ) -> dict[str, int]:
        """Bound queue growth. Idempotency survives; payload bulk does not.

        * terminal outbox rows (done/skipped/failed) older than the retention
          keep their row (job id, idempotency key, digest, outcome) so
          ``catch_up`` never re-enqueues them, but their source payload is
          replaced by a tiny marker and their inbox row is deleted;
        * resolved dead letters older than their retention are deleted.

        Each call touches at most ``batch`` rows per kind through partial
        indexes, so a tick never scans total history.
        """
        effective_now = float(now) if now is not None else self._now()
        cutoff = effective_now - float(terminal_retention_seconds)
        dl_cutoff = effective_now - float(dead_letter_retention_seconds)
        limit = max(1, int(batch))
        counts = {"payloads": 0, "inbox": 0, "dead_letters": 0, "bytes": 0}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """SELECT job_id, event_id, source_id, source_digest
                         FROM distillation_outbox
                        WHERE status IN ('done', 'skipped', 'failed')
                          AND processed_at < ? AND pruned_at IS NULL
                        LIMIT ?""",
                    (cutoff, limit),
                ).fetchall()
                for row in rows:
                    size = connection.execute(
                        """SELECT length(payload_json) FROM distillation_source_versions
                            WHERE source_id = ? AND source_digest = ? AND payload_json <> ?""",
                        (str(row["source_id"]), str(row["source_digest"]), PRUNED_PAYLOAD_JSON),
                    ).fetchone()
                    counts["bytes"] += int(size[0]) if size else 0
                    changed = connection.execute(
                        """UPDATE distillation_source_versions SET payload_json = ?
                            WHERE source_id = ? AND source_digest = ?
                              AND payload_json <> ?""",
                        (PRUNED_PAYLOAD_JSON, str(row["source_id"]),
                         str(row["source_digest"]), PRUNED_PAYLOAD_JSON),
                    ).rowcount
                    counts["payloads"] += changed
                    counts["inbox"] += connection.execute(
                        """DELETE FROM distillation_inbox
                            WHERE event_id = ? AND status IN ('applied', 'skipped', 'failed')""",
                        (str(row["event_id"]),),
                    ).rowcount
                    connection.execute(
                        "UPDATE distillation_outbox SET pruned_at = ? WHERE job_id = ?",
                        (effective_now, str(row["job_id"])),
                    )
                counts["dead_letters"] = connection.execute(
                    """DELETE FROM distillation_dead_letters
                        WHERE job_id IN (
                            SELECT job_id FROM distillation_dead_letters
                             WHERE resolved_at IS NOT NULL AND resolved_at < ?
                             LIMIT ?)""",
                    (dl_cutoff, limit),
                ).rowcount
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return counts

    def compact(self) -> None:
        """Return pruned space to the filesystem.

        Replacing a payload with a marker leaves its leaf page sparse but
        allocated, and rowid appends never refill it, so without compaction the
        file would not shrink or stop growing. VACUUM rewrites the database;
        callers invoke this only after a large amount of pruned payload.
        """
        with self._connect() as connection:
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _evidence_metadata_without_recipe_authority(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Copy Source metadata for an EvidenceItem without mirroring recipe authority.

    Only ``Source.metadata.strategy_seed.execution_recipe`` may supply a recipe;
    the Source itself is left untouched and the nested mapping is copied.
    """
    copied = copy.deepcopy(dict(metadata))
    nested = copied.get("strategy_seed")
    if isinstance(nested, dict):
        nested.pop("execution_recipe", None)
    return copied


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
        metadata={
            "evidence_dedupe_key": item_id,
            **_evidence_metadata_without_recipe_authority(source.metadata),
        },
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


_EXTERNAL_TEXT_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.PAPER, SourceType.NEWS, SourceType.SOCIAL, SourceType.FILING, SourceType.MACRO}
)

IRRELEVANT_REASON = "l2_irrelevant"
EXTRACTION_DISABLED_REASON = "l2_extraction_disabled"
EXTRACTION_UNAVAILABLE_MARKER = "hypothesis_extraction_unavailable"
COMPILER_FAILED_MARKER = "hypothesis_compiler_failed"
# ``_distill_one`` outcome for a job sent back to retry_wait because the
# extraction model/adapter did not answer (never a Registry call).
EXTRACTION_RETRY_OUTCOME = "extraction_retry"


def _with_hypothesis_card(source: SourceRecord, card: Mapping[str, Any]) -> SourceRecord:
    """Merge a verified card into ``metadata.strategy_seed`` (builder already prefers it).

    Only allowlisted keys derived from the validated card are written; an
    ``execution_recipe`` already declared by the Source is left as it was.
    """
    metadata = copy.deepcopy(dict(source.metadata))
    nested = metadata.get("strategy_seed")
    merged = dict(nested) if isinstance(nested, Mapping) else {}
    merged.update(card_to_strategy_seed_metadata(card))
    metadata["strategy_seed"] = merged
    return dataclasses.replace(source, metadata=metadata)


@dataclass(frozen=True)
class RegistrySyncResult:
    """Terminal Registry readback/write result for one source version."""

    registry_id: str
    status: str
    strategy_id: str | None = None
    version: str | None = None
    checksum: str | None = None
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
        hypothesis_extractor: HypothesisExtractor | None = None,
        hypothesis_compiler: HypothesisCompiler | None = None,
        demand_reader: ResearchDemandReader | None = None,
    ) -> None:
        self._hypothesis_extractor = hypothesis_extractor
        self._hypothesis_compiler = hypothesis_compiler
        if (
            self._hypothesis_compiler is not None
            and self._hypothesis_extractor is not None
            and getattr(self._hypothesis_compiler, "ledger", None) is None
            and hasattr(self._hypothesis_compiler, "bind_ledger")
        ):
            self._hypothesis_compiler.bind_ledger(self._hypothesis_extractor.ledger)
        self._demand_reader = demand_reader
        self._queue = job_queue or DistillationJobQueue()
        self._seed_store = seed_store or StrategySpecSeedStore()
        self._materializer = SeedMaterializationService(
            store=self._seed_store,
            created_by=created_by,
        )
        self._created_by = created_by
        self._worker_id = worker_id or f"{created_by}-{uuid.uuid4().hex[:8]}"
        self._lease_seconds = max(0.1, float(lease_seconds))
        self._tick_extraction_budget = l2_extraction_tick_budget_seconds()
        self._tick_extraction_seconds = 0.0
        self._tick_extractor_answered = False
        self._tick_extractor_unavailable = False
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
        tenant_id: str | None = None,
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
        effective_now = _timestamp(now) if now is not None else self._queue._now()
        superseded_resolved: list[str] = []
        if self._queue.dead_letter_count():
            superseded_resolved.extend(
                self._queue.resolve_superseded_dead_letters(
                    requested_by=self._created_by,
                    limit=max(DEFAULT_SUPERSEDED_RESOLVE_LIMIT, int(limit)),
                    tenant_id=tenant_id,
                    include_auth=False,
                    now=effective_now,
                )
            )
        priorities = {}
        if self._hypothesis_extractor is not None and self._demand_reader is not None:
            for tenant in self._queue.due_tenants(now=effective_now, tenant_id=tenant_id):
                priorities[tenant] = self._demand_reader.read(tenant, now=effective_now)
        claimed = self._queue.claim_due(
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            now=now,
            limit=limit,
            tenant_id=tenant_id,
            priority_by_tenant=priorities,
        )
        self._tick_extraction_seconds = 0.0
        self._tick_extractor_answered = False
        self._tick_extractor_unavailable = False
        processed = created = refreshed = skipped = failed = 0
        registry_synced = retried = dead_lettered = 0
        deferred = extraction_retried = 0
        extraction_dead_letters_redriven = defect_reattempted = 0
        lease_lost: list[str] = []

        def run_batch(batch: Sequence[DistillationJob]) -> None:
            nonlocal processed, created, refreshed, skipped, failed
            nonlocal registry_synced, retried, dead_lettered, deferred, extraction_retried
            for claimed_job in batch:
                try:
                    # The job's own lease starts now; claim time and earlier jobs of
                    # this batch do not consume it, and only here is an attempt counted.
                    job = self._queue.begin_job(
                        claimed_job, lease_seconds=self._job_lease_seconds(), now=now,
                    )
                    outcome, delivery = self._process_claimed(
                        job, source_records, now=now, effective_now=effective_now,
                    )
                except DistillationLeaseError:
                    # A superseded token on one job must not abort the tick: the
                    # job belongs to someone else or was recovered. Record it so
                    # the tick reports degraded with the job id.
                    lease_lost.append(claimed_job.job_id)
                    continue
                processed += 1
                if outcome == "created":
                    created += 1
                elif outcome == "refreshed":
                    refreshed += 1
                elif outcome == "skipped":
                    skipped += 1
                elif outcome == "failed":
                    failed += 1
                elif outcome == "deferred":
                    deferred += 1
                elif outcome == EXTRACTION_RETRY_OUTCOME:
                    extraction_retried += 1
                if delivery == "registry_synced":
                    registry_synced += 1
                elif delivery == "retry_wait":
                    retried += 1
                elif delivery == "dead_letter":
                    dead_lettered += 1

        run_batch(claimed)
        phase1_job_ids = {job.job_id for job in claimed}

        # Phase 2: after all due fresh records have run, redrive newest-version
        # extraction-unavailable dead letters and re-attempt newest-version
        # fixed-defect records once, within the daily call cap, per-record
        # attempt limits (3/day, 6/total) and the tick extraction time budget.
        while (
            self._hypothesis_extractor is not None
            and processed < max(0, int(limit))
            and self._tick_extraction_seconds < self._tick_extraction_budget
        ):
            if self._tick_extractor_unavailable and not self._tick_extractor_answered:
                break
            ledger = getattr(self._hypothesis_extractor, "ledger", None)
            remaining_fn = getattr(ledger, "remaining_daily_calls", None)
            remaining_cap = remaining_fn() if callable(remaining_fn) else int(limit)
            remaining_slots = max(0, int(limit) - processed)
            batch_budget = min(remaining_slots, remaining_cap)
            if batch_budget <= 0:
                break
            step_limit = 1 if not self._tick_extractor_answered else batch_budget
            if self._queue.dead_letter_count():
                dl_out = self._queue.redrive_extraction_unavailable_dead_letters(
                    requested_by=self._created_by,
                    limit=min(step_limit, DEFAULT_EXTRACTION_REDRIVE_LIMIT),
                    tenant_id=tenant_id,
                    ledger=ledger,
                    now=effective_now,
                    exclude_job_ids=phase1_job_ids,
                )
                superseded_resolved.extend(dl_out["superseded"])
                redriven_ids = dl_out["redriven"]
                if redriven_ids:
                    extraction_dead_letters_redriven += len(redriven_ids)
                    redriven_claimed = self._queue.claim_due(
                        worker_id=self._worker_id,
                        lease_seconds=self._lease_seconds,
                        now=now,
                        limit=len(redriven_ids),
                        tenant_id=tenant_id,
                        priority_by_tenant=priorities,
                        job_ids=redriven_ids,
                    )
                    if not redriven_claimed:
                        break
                    run_batch(redriven_claimed)
                    continue
            reopened_ids = self._queue.reattempt_fixed_defect_records(
                requested_by=self._created_by,
                limit=min(step_limit, DEFAULT_DEFECT_REATTEMPT_LIMIT),
                tenant_id=tenant_id,
                now=effective_now,
                ledger=ledger,
                exclude_job_ids=phase1_job_ids,
            )
            if not reopened_ids:
                break
            defect_reattempted += len(reopened_ids)
            reopened_claimed = self._queue.claim_due(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                now=now,
                limit=len(reopened_ids),
                tenant_id=tenant_id,
                priority_by_tenant=priorities,
                job_ids=reopened_ids,
            )
            if not reopened_claimed:
                break
            run_batch(reopened_claimed)

        return DistillationRunResult(
            processed=processed,
            created=created,
            refreshed=refreshed,
            skipped=skipped,
            failed=failed,
            registry_synced=registry_synced,
            retried=retried,
            dead_lettered=dead_lettered,
            deferred=deferred,
            extraction_retried=extraction_retried,
            superseded_dead_letters_resolved=len(superseded_resolved),
            extraction_dead_letters_redriven=extraction_dead_letters_redriven,
            defect_reattempted=defect_reattempted,
            lease_lost=tuple(lease_lost),
            extraction=self._extraction_summary(),
        )

    def _extraction_summary(self) -> dict[str, Any] | None:
        if self._hypothesis_extractor is None:
            return None
        try:
            summary = dict(self._hypothesis_extractor.ledger.summary())
        except Exception as exc:  # noqa: BLE001 - a summary read must not fail the tick
            return {"summary_unavailable": type(exc).__name__}
        summary["call_deadline_seconds"] = self._hypothesis_extractor.call_deadline_seconds
        summary["tick_extraction_seconds"] = round(self._tick_extraction_seconds, 3)
        return summary

    def _job_lease_seconds(self) -> float:
        """A started job's lease outlives one extraction call plus margin.

        The extractor makes exactly one model call per job attempt, bounded by
        its deadline, so a slow call can never let the lease lapse and be
        counted toward ``lease_expired_repeatedly``.
        """
        if self._hypothesis_extractor is None:
            return self._lease_seconds
        return max(
            self._lease_seconds,
            float(self._hypothesis_extractor.call_deadline_seconds) + EXTRACTION_LEASE_MARGIN_SECONDS,
        )

    def _process_claimed(
        self,
        job: DistillationJob,
        source_records: Mapping[str, SourceRecord] | None,
        *,
        now: str | None,
        effective_now: float,
    ) -> tuple[str, str | None]:
        if (job.deferred_since is not None
                and effective_now >= _timestamp(job.deferred_since) + DEFER_TTL_SECONDS):
            self._queue.mark_skipped(
                job.job_id, reason="budget_deferral_expired: 14 days since first cap deferral",
                processed_at=now, claim_token=job.lease_token,
            )
            return "skipped", None
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
            return "failed", None
        return self._distill_one(job, source, now=now)

    def catch_up(
        self,
        source_records: Sequence[SourceRecord],
        *,
        limit: int = 500,
        now: str | None = None,
        tenant_id: str | None = None,
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
        tenant = str(tenant_id or "").strip() or None
        if tenant_id is not None and tenant is None:
            raise DistillationError("tenant_id must be a non-empty explicit tenant")
        if tenant is not None and any(source.tenant_id != tenant for source in source_records):
            raise DistillationError("source record set contains foreign or unowned tenant scope")
        normalized = [
            s for s in source_records if s.status == SourceRecordStatus.NORMALIZED
            and not (s.source_type == SourceType.MARKET and s.metadata.get("collector_market_bundle"))
        ]

        enqueued = 0
        for source in normalized:
            if enqueued >= max(0, limit):
                break
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
        run_result = self.run_pending(None, now=now, tenant_id=tenant)
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
            deferred=run_result.deferred,
            extraction_retried=run_result.extraction_retried,
            superseded_dead_letters_resolved=run_result.superseded_dead_letters_resolved,
            extraction_dead_letters_redriven=run_result.extraction_dead_letters_redriven,
            defect_reattempted=run_result.defect_reattempted,
            lease_lost=run_result.lease_lost,
            extraction=run_result.extraction,
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
        ``failed``, ``deferred`` or ``extraction_retry``. The second reports
        Registry delivery state when a terminal Registry sink is configured.
        """
        if source.status == SourceRecordStatus.REJECTED:
            self._queue.mark_skipped(
                job.job_id,
                reason=f"source {source.source_id} is rejected",
                processed_at=now,
                claim_token=job.lease_token,
            )
            return "skipped", None

        if source.source_type == SourceType.MARKET and source.metadata.get("collector_market_bundle"):
            self._queue.mark_skipped(job.job_id, reason=MARKET_SOURCE_SKIP_REASON,
                                     processed_at=now, claim_token=job.lease_token)
            return "skipped", "market_source"

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
        # Registry sync verifies the committed source version digest, so it must
        # see the source as admitted, not with the extracted card merged in.
        admitted_source = source

        if source.source_type != SourceType.MARKET:
            origin = extraction_origin(source.source_type.value, source.metadata)
            if origin is not None or source.source_type in _EXTERNAL_TEXT_TYPES:
                # External text never becomes a draft without a verified card
                # (RC2): no extractor, no relevance, no card -> typed closure.
                if self._hypothesis_extractor is None:
                    relevance = assess_source_relevance(source)
                    reason = (
                        EXTRACTION_DISABLED_REASON if relevance.relevant
                        else f"{IRRELEVANT_REASON}: {relevance.reason}"
                    )
                    self._queue.mark_skipped(
                        job.job_id, reason=reason, processed_at=now, claim_token=job.lease_token,
                    )
                    return "skipped", reason.split(":")[0]
                if self._tick_extraction_seconds >= self._tick_extraction_budget:
                    # One tick must stay inside the controller's health window:
                    # once this tick spent its extraction time, return the job
                    # for the next tick without counting an attempt.
                    self._queue.defer_budget(
                        job, now=_timestamp(now) if now is not None else self._queue._now(),
                        reason=DEFERRAL_EXTRACTION_TICK_BUDGET, next_window=False,
                    )
                    return "deferred", DEFERRAL_EXTRACTION_TICK_BUDGET
                started = time.monotonic()
                try:
                    outcome = self._hypothesis_extractor.extract(source, digest=version_key or "")
                finally:
                    self._tick_extraction_seconds += time.monotonic() - started
                if outcome.status == "unavailable":
                    self._tick_extractor_unavailable = True
                elif outcome.status in ("card", "no_hypothesis", "rejected", "failed") and outcome.detail != "cached":
                    self._tick_extractor_answered = True
                if outcome.card is None:
                    if outcome.status in (DEFERRAL_BUDGET_EXHAUSTED, DEFERRAL_ATTEMPTS_DEFERRED):
                        # Daily cap, or this record already had 3 calls today:
                        # wait for the next UTC day without burning an attempt.
                        self._queue.defer_budget(
                            job, now=_timestamp(now) if now is not None else self._queue._now(),
                            reason=outcome.status,
                        )
                        return "deferred", outcome.status
                    if outcome.status == "attempts_exhausted":
                        reason = f"{EXTRACTION_ATTEMPTS_EXHAUSTED_REASON}: {outcome.detail}"
                        self._queue.mark_skipped(
                            job.job_id, reason=reason, processed_at=now, claim_token=job.lease_token,
                        )
                        return "skipped", EXTRACTION_ATTEMPTS_EXHAUSTED_REASON
                    if outcome.status == "unavailable":
                        # The model or adapter did not answer: not a delivered
                        # outcome. Retry with backoff; never store no_hypothesis.
                        failure_status = self._queue.mark_retry_or_dead_letter(
                            job,
                            error=f"{EXTRACTION_UNAVAILABLE_MARKER}: {outcome.detail}",
                            base_delay_seconds=self._retry_base_seconds,
                            now=now,
                        )
                        # Counted apart from ``failed``: the controller reads
                        # ``failed`` as Registry delivery trouble.
                        return EXTRACTION_RETRY_OUTCOME, failure_status.value
                    reason = {
                        "rejected": "hypothesis_rejected",
                        "irrelevant": IRRELEVANT_REASON,
                        "admission_denied": "l2_admission_denied",
                        "failed": "hypothesis_extraction_failed",
                    }.get(outcome.status, "no_hypothesis")
                    self._queue.mark_skipped(
                        job.job_id,
                        reason=f"{reason}: {outcome.detail}" if outcome.detail else reason,
                        processed_at=now,
                        claim_token=job.lease_token,
                    )
                    return "skipped", reason
                card = outcome.card
                if self._hypothesis_compiler is not None:
                    compile_started = time.monotonic()
                    try:
                        compiled = self._hypothesis_compiler.compile(
                            outcome.card,
                            source_id=source.source_id,
                            digest=version_key or "",
                        )
                    finally:
                        self._tick_extraction_seconds += time.monotonic() - compile_started
                    if compiled.status in (DEFERRAL_BUDGET_EXHAUSTED, DEFERRAL_ATTEMPTS_DEFERRED):
                        self._queue.defer_budget(
                            job,
                            now=_timestamp(now) if now is not None else self._queue._now(),
                            reason=compiled.status,
                        )
                        return "deferred", compiled.status
                    if compiled.status == "attempts_exhausted":
                        reason = f"{EXTRACTION_ATTEMPTS_EXHAUSTED_REASON}: {compiled.reason}"
                        self._queue.mark_skipped(
                            job.job_id, reason=reason, processed_at=now, claim_token=job.lease_token,
                        )
                        return "skipped", EXTRACTION_ATTEMPTS_EXHAUSTED_REASON
                    if compiled.status == SKIPPED_COMPILE and str(compiled.reason).startswith("proposer_failed"):
                        self._tick_extractor_unavailable = True
                        # Transport/compiler failure is not a delivered outcome:
                        # retry durably instead of materializing an uncompiled seed.
                        failure_status = self._queue.mark_retry_or_dead_letter(
                            job,
                            error=f"{COMPILER_FAILED_MARKER}: {compiled.reason}",
                            base_delay_seconds=self._retry_base_seconds,
                            now=now,
                        )
                        return EXTRACTION_RETRY_OUTCOME, failure_status.value
                    if not compiled.extra.get("cached"):
                        self._tick_extractor_answered = True
                    if compiled.status in {NOT_EXPRESSIBLE, NEEDS_DATA, SUPPORT_ADDED}:
                        self._queue.clear_budget_deferral(
                            job, now=_timestamp(now) if now is not None else self._queue._now(),
                        )
                        # No new draft: refusal is recorded, or an existing
                        # draft's support_count was incremented instead.
                        reason = {
                            NOT_EXPRESSIBLE: "hypothesis_not_expressible",
                            NEEDS_DATA: "hypothesis_needs_data",
                            SUPPORT_ADDED: "hypothesis_rule_support_added",
                        }[compiled.status]
                        detail = compiled.dataset or compiled.reason or compiled.fingerprint
                        self._queue.mark_skipped(
                            job.job_id,
                            reason=f"{reason}: {detail}" if detail else reason,
                            processed_at=now,
                            claim_token=job.lease_token,
                        )
                        return "skipped", reason
                    if compiled.status == COMPILED and compiled.artifact is not None:
                        # The seed builder preserves hypothesis_card wholesale.
                        card = {
                            **card,
                            "factor_rule_v1": {**compiled.artifact, "rule_fingerprint": compiled.fingerprint},
                        }
                self._queue.clear_budget_deferral(
                    job, now=_timestamp(now) if now is not None else self._queue._now(),
                )
                source = _with_hypothesis_card(source, card)

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

        if source.source_type == SourceType.MARKET:
            self._queue.mark_skipped(
                job.job_id,
                reason=MARKET_SOURCE_SKIP_REASON,
                seed_id=result.seed.seed_id,
                processed_at=now,
                claim_token=job.lease_token,
            )
            return "skipped", "market_source"

        if self._registry_sync is not None:
            try:
                registry_result = self._registry_sync(
                    RegistrySyncRequest(
                        source=admitted_source,
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
            if registry_result.status == "skipped":
                self._queue.mark_skipped(
                    job.job_id,
                    reason=registry_result.reason or MARKET_SOURCE_SKIP_REASON,
                    seed_id=result.seed.seed_id,
                    registry_id=registry_result.registry_id or None,
                    strategy_id=registry_result.strategy_id,
                    version=registry_result.version,
                    checksum=registry_result.checksum,
                    processed_at=now,
                    claim_token=job.lease_token,
                )
                return "skipped", "skipped"
            if registry_result.status == "immutable":
                self._queue.mark_skipped(
                    job.job_id,
                    reason=registry_result.reason or "Registry artifact is immutable",
                    seed_id=result.seed.seed_id,
                    registry_id=registry_result.registry_id,
                    strategy_id=registry_result.strategy_id,
                    version=registry_result.version,
                    checksum=registry_result.checksum,
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
            strategy_id = registry_result.strategy_id
            version = registry_result.version
            checksum = registry_result.checksum
            delivery = "registry_synced"
        else:
            registry_id = None
            strategy_id = None
            version = None
            checksum = None
            delivery = None

        self._queue.mark_done(
            job.job_id,
            seed_id=result.seed.seed_id,
            registry_id=registry_id,
            strategy_id=strategy_id,
            version=version,
            checksum=checksum,
            processed_at=now,
            claim_token=job.lease_token,
        )
        return ("refreshed" if is_refresh else "created"), delivery


def make_distillation_worker(
    *,
    queue_path: str | Path | None = None,
    job_queue: DistillationJobQueue | None = None,
    seed_store_path: str | Path | None = None,
    created_by: str = "distillation_worker",
    worker_id: str | None = None,
    lease_seconds: float = 30.0,
    retry_base_seconds: float = 5.0,
    max_attempts: int = 3,
    registry_sync: Callable[[RegistrySyncRequest], RegistrySyncResult] | None = None,
    hypothesis_extractor: HypothesisExtractor | None = None,
    hypothesis_compiler: HypothesisCompiler | None = None,
    demand_reader: ResearchDemandReader | None = None,
) -> DistillationWorker:
    """Factory for a default-configured DistillationWorker."""
    queue = job_queue if job_queue is not None else DistillationJobQueue(
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
        hypothesis_compiler=hypothesis_compiler,
        demand_reader=demand_reader if demand_reader is not None else ResearchDemandReader(),
        hypothesis_extractor=(
            hypothesis_extractor
            if hypothesis_extractor is not None
            else extractor_from_env(
                ledger_path=(Path(str(queue_path)).with_name("l2_extraction_ledger.sqlite") if queue_path else None)
            )
        ),
    )


__all__ = [
    "DistillationError",
    "DistillationJob",
    "DistillationJobQueue",
    "DistillationJobStatus",
    "DistillationLeaseError",
    "DistillationRunResult",
    "DistillationWorker",
    "FIXED_DEFECT_REATTEMPT_OUTCOMES",
    "FixedDefectOutcome",
    "HOSTED_DEFECT_CLOSED_SOURCE_BACKLOG",
    "HOSTED_DEFECT_REATTEMPT_EXPECTED_CALLS",
    "HOSTED_DEFECT_REATTEMPT_EXPECTED_DAYS",
    "MARKET_SOURCE_SKIP_REASON",
    "RegistrySyncResult",
    "fixed_defect_for_skip_reason",
    "is_extraction_unavailable_failure",
    "is_transient_registry_failure",
    "make_distillation_worker",
    "source_version_digest",
    "source_version_digest_matches",
]
