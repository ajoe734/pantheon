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
7. Idempotent deduplication by event_id (service layer + ON CONFLICT at write layer)

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

The Postgres write function uses ON CONFLICT (event_id) DO NOTHING for
idempotent inserts under retry/replay.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Protocol, runtime_checkable

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

try:
    import jsonschema
except ImportError:
    jsonschema = None

log = logging.getLogger(__name__)


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
) -> Callable[[list[dict[str, Any]]], Coroutine[Any, Any, WriteResult]]:
    """
    Build the canonical Postgres batch-write function for production wiring.

    Uses ON CONFLICT (event_id) DO NOTHING for idempotent inserts — a batch
    that is retried or replayed will not create duplicate rows.

    Example
    -------
        write_fn = build_postgres_write_fn(dsn=os.environ["TELEMETRY_DB_DSN"])
        svc = TelemetryIngestService(write_fn=write_fn, binding_store=store)

    The expected table DDL (simplified):

        CREATE TABLE telemetry_events (
            event_id        TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL,
            payload         JSONB NOT NULL
        );
    """
    import json as _json

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
                        _json.dumps(ev),
                    )
                    for ev in batch
                ]
                await conn.executemany(
                    f"INSERT INTO {table} "
                    f"(event_id, event_type, created_at, payload) "
                    f"VALUES ($1, $2, $3::timestamptz, $4::jsonb) "
                    f"ON CONFLICT (event_id) DO NOTHING",
                    rows,
                )
            finally:
                await conn.close()
            return WriteResult.ok(len(batch))
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
        buffer_maxsize: int = 100_000,
        buffer_redis_url: str = "redis://localhost:6379/0",
        batch_size: int = 500,
        batch_interval: float = 1.0,
        max_retries: int = 5,
        dlq_spill_path: Optional[str] = None,
        dlq_incident_threshold: int = 100,
        write_fn: Optional[Callable[[list[dict[str, Any]]], Coroutine[Any, Any, WriteResult]]] = None,
        schema: Optional[dict[str, Any]] = None,
        binding_store: Optional[RuntimeBindingProtocol] = None,
        runtime_summary_store: Optional[RuntimeSummaryProjectionStore] = None,
        dedup_max_size: int = 500_000,
        replay_dlq_on_start: bool = False,
        dlq_replay_tag_filter: Optional[str] = None,
    ):
        """
        Parameters
        ----------
        schema_path : str, optional
            Path to telemetry_event.schema.json for validation.
        storage_dir : str, optional
            Directory for spill files (DLQ, emergency buffer).
        buffer_backend : str
            "memory" (v1) or "redis" (v2).
        buffer_maxsize : int
            Max events in buffer before backpressure.
        buffer_redis_url : str
            Redis URL (only used if buffer_backend="redis").
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
        """
        # Schema
        self._schema: Optional[dict[str, Any]] = schema
        self._schema_path = schema_path
        if self._schema_path and not self._schema:
            self._load_schema()

        # Buffer
        buffer_kwargs: dict[str, Any] = {"maxsize": buffer_maxsize}
        if buffer_backend == "redis":
            buffer_kwargs["redis_url"] = buffer_redis_url
        self._buffer: DurableBuffer = create_buffer(backend=buffer_backend, **buffer_kwargs)

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
        self._seen_event_ids: set[str] = set()
        self._dedup_max_size = dedup_max_size

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
        """Load JSON schema from file."""
        if not self._schema_path:
            return
        try:
            import json
            with open(self._schema_path, "r") as f:
                self._schema = json.load(f)
            log.info(f"Loaded telemetry schema from {self._schema_path}")
        except Exception as e:
            log.warning(f"Failed to load telemetry schema: {e}")
            self._schema = None

    def _validate_event(self, event: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate event against schema.

        Returns (valid, error_message).
        """
        if not self._schema or not jsonschema:
            return True, None

        try:
            jsonschema.validate(instance=event, schema=self._schema)
            return True, None
        except jsonschema.ValidationError as e:
            return False, e.message
        except jsonschema.SchemaError as e:
            return False, f"Schema error: {e.message}"

    def _validate_evidence_contract(self, event: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate TEL-001A evidence contract (E-1 through E-6).

        When a binding_store is configured, binding_id is resolved against the
        authoritative RuntimeBinding record and all identity fields plus the
        temporal window are cross-checked.

        Returns (valid, error_message).
        """
        # E-1: Minimal binding identity (field presence)
        binding_id = event.get("binding_id")
        if not binding_id:
            return False, "Missing binding_id (Evidence E-1)"

        required_identity = ["runtime_id", "capital_pool_id", "artifact_id", "artifact_version"]
        missing = [f for f in required_identity if not event.get(f)]
        if missing:
            return False, f"Missing binding identity fields: {missing} (Evidence E-1)"

        # E-2: Deployment stage proof (field presence + enum)
        deployment_stage = event.get("deployment_stage")
        if not deployment_stage or deployment_stage not in ("paper", "canary", "live", "frozen"):
            return False, f"Invalid deployment_stage: {deployment_stage} (Evidence E-2)"

        # E-3: Governance admissibility
        if not event.get("plan_id") or not event.get("persona_capital_binding_id"):
            return False, "Missing governance admissibility fields (Evidence E-3)"

        # E-5: Rollback lineage consistency
        rollback_parent = event.get("rollback_parent")
        rollback_action_type = event.get("rollback_action_type")
        if (rollback_parent is not None) != (rollback_action_type is not None):
            return False, "rollback_parent and rollback_action_type must both be set or both absent (Evidence E-5)"

        # --- RuntimeBinding authoritative cross-validation (requires binding_store) ---
        if self._binding_store is not None:
            binding = self._binding_store.get_binding(binding_id)
            if binding is None:
                return False, (
                    f"binding_id {binding_id!r} not found in RuntimeBinding store — "
                    f"event cannot be attributed to an authoritative binding (Evidence E-1)"
                )

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
                )

            # E-2 cross-check: deployment_stage must equal binding.deployment_mode
            binding_mode = getattr(binding, "deployment_mode", None)
            if deployment_stage != binding_mode:
                return False, (
                    f"deployment_stage {deployment_stage!r} does not match binding "
                    f"deployment_mode {binding_mode!r} (Evidence E-2)"
                )

            # E-4: Temporal window — event.created_at must fall within
            # [binding.effective_at, binding.retired_at]
            event_ts = event.get("created_at")
            effective_at = getattr(binding, "effective_at", None)
            retired_at = getattr(binding, "retired_at", None)

            if event_ts and effective_at and event_ts < effective_at:
                return False, (
                    f"Event created_at {event_ts!r} precedes binding effective_at "
                    f"{effective_at!r} — temporal violation (Evidence E-4)"
                )
            if event_ts and retired_at and event_ts > retired_at:
                return False, (
                    f"Event created_at {event_ts!r} is after binding retired_at "
                    f"{retired_at!r} — temporal violation (Evidence E-4)"
                )

        return True, None

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
            self._total_duplicates += 1
            log.debug(f"Ingest skipped (duplicate event_id): {event_id}")
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
        valid, error_msg = self._validate_evidence_contract(event)
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
            self._seen_event_ids.add(event_id)
            # Evict oldest half when the dedup set exceeds its size limit
            if len(self._seen_event_ids) > self._dedup_max_size:
                evict = list(self._seen_event_ids)[: self._dedup_max_size // 2]
                for eid in evict:
                    self._seen_event_ids.discard(eid)

        self._total_ingested += 1
        if self._runtime_summary_store is not None:
            try:
                self._runtime_summary_store.project_event(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("Runtime summary projection failed for event %s: %s", event_id, exc)
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

    async def start(self) -> None:
        """Start the ingest service (buffer + batch writer)."""
        if self._started:
            return
        self._load_dlq_from_spill_once()
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
            "startup": {
                "dlq_loaded_from_spill": self._dlq_loaded_from_spill_count,
                "dlq_replay_on_start": self._replay_dlq_on_start,
                "dlq_replay_tag_filter": self._dlq_replay_tag_filter,
                "dlq_replayed_on_start": self._startup_dlq_replay_count,
            },
        }

    def get_runtime_summary(self, runtime_id: str) -> Optional[dict[str, Any]]:
        if self._runtime_summary_store is None:
            return None
        return self._runtime_summary_store.get(runtime_id)

    def list_runtime_summaries(self) -> list[dict[str, Any]]:
        if self._runtime_summary_store is None:
            return []
        return self._runtime_summary_store.list()

    def has_runtime_binding_store(self) -> bool:
        return self._binding_store is not None

    def resolve_runtime_binding(self, binding_id: str) -> Optional[Any]:
        if self._binding_store is None:
            return None
        return self._binding_store.get_binding(binding_id)

    # -- Diagnostics / Replay --

    def get_dlq_entries(self, tag_filter: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        """Get dead-letter queue entries."""
        return self._dlq.get_entries_as_dicts(tag_filter=tag_filter, limit=limit)

    async def replay_dlq(self, tag_filter: Optional[str] = None) -> int:
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
            events = self._dlq.replay_entries(tag_filter=tag_filter)
        else:
            # Collect write-failure entries only (deduplicated by event_id)
            seen: set[str] = set()
            events = []
            for tag in _WRITE_FAILURE_TAGS:
                for ev in self._dlq.replay_entries(tag_filter=tag):
                    eid = ev.get("event_id")
                    if eid:
                        if eid not in seen:
                            seen.add(eid)
                            events.append(ev)
                    else:
                        events.append(ev)

        count = 0
        for event in events:
            # Re-enter the full ingest path — re-validates schema + evidence.
            # Clear the event_id from the dedup set so replay can re-enqueue it.
            eid = event.get("event_id")
            if eid:
                self._seen_event_ids.discard(eid)
            ok = await self.ingest(event, timeout=5.0)
            if ok:
                count += 1
        log.info(f"Replayed {count}/{len(events)} DLQ write-failure events through ingest path")
        return count
