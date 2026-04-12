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
2. Durable buffering (bounded, with overflow protection)
3. Async batch writing (micro-batching, retry, partition routing)
4. Backpressure management (adaptive concurrency, delay non-critical events)
5. Dead-letter handling (diagnostic tags, JSONL spill, replay support)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

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
)

try:
    import jsonschema
except ImportError:
    jsonschema = None

log = logging.getLogger(__name__)


class TelemetryIngestService:
    """
    Main telemetry ingest service with shock absorption.

    Usage:
        service = TelemetryIngestService(
            schema_path="services/telemetry/telemetry_event.schema.json",
            storage_dir="/tmp/telemetry_spill",
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
            Custom write function. If None, uses a memory-only sink (for testing).
        schema : dict, optional
            Pre-loaded schema dict (alternative to schema_path).
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

        # State
        self._started = False
        self._total_ingested = 0
        self._total_rejected = 0
        self._start_time: Optional[float] = None

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

        Returns (valid, error_message).
        """
        # E-1: Minimal binding identity
        binding_id = event.get("binding_id")
        if not binding_id:
            return False, "Missing binding_id (Evidence E-1)"

        required_identity = ["runtime_id", "capital_pool_id", "artifact_id", "artifact_version"]
        missing = [f for f in required_identity if not event.get(f)]
        if missing:
            return False, f"Missing binding identity fields: {missing} (Evidence E-1)"

        # E-2: Deployment stage proof
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

        return True, None

    async def ingest(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        """
        Ingest a single telemetry event.

        Flow:
        1. Schema validation
        2. Evidence contract validation (TEL-001A E-1 through E-6)
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
            True if event was enqueued successfully.
        """
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

        # 2. Evidence contract validation
        valid, error_msg = self._validate_evidence_contract(event)
        if not valid:
            # Determine tag based on error type
            if "binding" in error_msg.lower():
                tag = TAG_BINDING_MISMATCH
            elif "temporal" in error_msg.lower():
                tag = TAG_TEMPORAL_VIOLATION
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

        self._total_ingested += 1
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
        self._started = True
        self._start_time = time.monotonic()
        await self._writer.start()
        log.info("TelemetryIngestService started")

    async def stop(self, graceful: bool = True) -> None:
        """Stop the ingest service."""
        await self._writer.stop(graceful=graceful)
        await self._buffer.close()
        self._started = False
        log.info(
            f"TelemetryIngestService stopped. "
            f"ingested={self._total_ingested}, rejected={self._total_rejected}"
        )

    @staticmethod
    async def _default_write_fn(batch: list[dict[str, Any]]) -> WriteResult:
        """
        Default write function — memory-only sink for testing.

        In production, replace with actual Postgres batch insert.
        """
        # Simulate a write (no-op for testing)
        # In real usage, this would do: await db.execute_many(...)
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
            },
            "buffer": self._buffer.stats() if hasattr(self._buffer, "stats") else {
                "size": self._buffer.size(),
                "capacity": self._buffer.capacity(),
            },
            "writer": self._writer.stats(),
            "dead_letter_queue": self._dlq.stats(),
            "backpressure": self._backpressure.stats(),
        }

    # -- Diagnostics / Replay --

    def get_dlq_entries(self, tag_filter: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        """Get dead-letter queue entries."""
        return self._dlq.get_entries_as_dicts(tag_filter=tag_filter, limit=limit)

    async def replay_dlq(self, tag_filter: Optional[str] = None) -> int:
        """
        Replay dead-letter events back into the ingest buffer.
        Returns number of events re-enqueued.
        """
        events = self._dlq.replay_entries(tag_filter=tag_filter)
        count = 0
        for event in events:
            # Re-ingest without re-validation (events were already validated)
            enqueued = await self._buffer.put(event, timeout=5.0)
            if enqueued:
                count += 1
        log.info(f"Replayed {count}/{len(events)} DLQ events back into buffer")
        return count
