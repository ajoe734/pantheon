"""
Async batch writer for telemetry ingest.

TEL-002: Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §3.1 Layer D,
batch/async writers are responsible for:
- micro-batching
- partition routing
- retry with backoff
- dead-letter routing
- write amplification control

This module provides AsyncBatchWriter which:
1. Pulls events from a DurableBuffer
2. Groups them into micro-batches
3. Routes batches to the appropriate partition (by event_date, environment, pool)
4. Retries transient failures with exponential backoff
5. Routes poison/unrecoverable events to the DeadLetterQueue
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from .backpressure import BackpressureController, CRITICAL_EVENT_TYPES
from .buffer import DurableBuffer
from .dead_letter import (
    DeadLetterQueue,
    TAG_RETRY_EXHAUSTED,
    TAG_WRITER_ERROR,
    TAG_POISON_EVENT,
    TAG_BUFFER_OVERFLOW,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Write result
# ---------------------------------------------------------------------------

class WriteResult:
    """Result of a batch write operation."""
    __slots__ = ("success", "count", "error", "retryable")

    def __init__(self, success: bool, count: int = 0, error: Optional[str] = None, retryable: bool = True):
        self.success = success
        self.count = count
        self.error = error
        self.retryable = retryable

    @classmethod
    def ok(cls, count: int) -> "WriteResult":
        return cls(success=True, count=count)

    @classmethod
    def fail(cls, error: str, retryable: bool = True) -> "WriteResult":
        return cls(success=False, error=error, retryable=retryable)


# ---------------------------------------------------------------------------
# Async Batch Writer
# ---------------------------------------------------------------------------

class AsyncBatchWriter:
    """
    Async batch writer that pulls from a DurableBuffer and writes to
    a canonical store via a user-provided write function.

    The write function signature is:
        async def write_batch(batch: list[dict]) -> WriteResult

    Features:
    - Micro-batching: collects up to `batch_size` events or `batch_interval` seconds
    - Retry with exponential backoff for transient failures
    - Dead-letter routing for poison/unrecoverable events
    - Backpressure-aware: adjusts concurrency based on BackpressureController
    - Partition routing: groups events by partition key before writing
    """

    def __init__(
        self,
        buffer: DurableBuffer,
        write_fn: Callable[[list[dict[str, Any]]], Coroutine[Any, Any, WriteResult]],
        dead_letter_queue: DeadLetterQueue,
        backpressure: Optional[BackpressureController] = None,
        batch_size: int = 500,
        batch_interval: float = 1.0,
        max_retries: int = 5,
        base_retry_delay: float = 0.5,
        max_retry_delay: float = 30.0,
        partition_key_fn: Optional[Callable[[dict[str, Any]], str]] = None,
    ):
        """
        Parameters
        ----------
        buffer : DurableBuffer
            Source of events to consume.
        write_fn : async callable
            Function that takes a list of events and returns WriteResult.
        dead_letter_queue : DeadLetterQueue
            Destination for unrecoverable events.
        backpressure : BackpressureController, optional
            Controller for adaptive concurrency.
        batch_size : int
            Maximum events per batch.
        batch_interval : float
            Maximum seconds to wait before flushing a partial batch.
        max_retries : int
            Maximum retry attempts for transient failures.
        base_retry_delay : float
            Initial retry delay (seconds). Exponential backoff multiplies by 2.
        max_retry_delay : float
            Maximum retry delay cap (seconds).
        partition_key_fn : callable, optional
            Function that extracts a partition key from an event.
            Default: partitions by (event_date, deployment_stage).
        """
        self._buffer = buffer
        self._write_fn = write_fn
        self._dlq = dead_letter_queue
        self._backpressure = backpressure
        self._batch_size = batch_size
        self._batch_interval = batch_interval
        self._max_retries = max_retries
        self._base_retry_delay = base_retry_delay
        self._max_retry_delay = max_retry_delay
        self._partition_key_fn = partition_key_fn or self._default_partition_key

        # State
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._total_written = 0
        self._total_failed = 0
        self._total_retried = 0
        self._total_dlq = 0
        self._batches_flushed = 0
        self._last_write_attempt_ts: Optional[float] = None
        self._last_successful_write_ts: Optional[float] = None
        self._last_failed_write_ts: Optional[float] = None
        self._last_dlq_ts: Optional[float] = None
        self._last_batch_size = 0
        self._last_error: Optional[str] = None

    @staticmethod
    def _iso_from_epoch(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _age_seconds(ts: Optional[float]) -> Optional[float]:
        if ts is None:
            return None
        return round(max(0.0, time.time() - ts), 3)

    def _mark_write_attempt(self, batch_size: int) -> None:
        self._last_write_attempt_ts = time.time()
        self._last_batch_size = batch_size

    def _mark_write_success(self) -> None:
        self._last_successful_write_ts = time.time()
        self._last_error = None

    def _mark_write_failure(self, error: object) -> None:
        self._last_failed_write_ts = time.time()
        self._last_error = str(error)

    def _mark_dlq_write(self) -> None:
        self._last_dlq_ts = time.time()

    @staticmethod
    def _default_partition_key(event: dict[str, Any]) -> str:
        """Default partition key: (date, deployment_stage)."""
        created_at = event.get("created_at", "")
        deployment_stage = event.get("deployment_stage", "unknown")
        # Extract date from ISO timestamp
        date_part = created_at[:10] if created_at else "unknown"
        return f"{date_part}:{deployment_stage}"

    async def start(self) -> None:
        """Start the batch writer loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        log.info("AsyncBatchWriter started")

    async def stop(self, graceful: bool = True) -> None:
        """
        Stop the batch writer.

        Parameters
        ----------
        graceful : bool
            If True, drain remaining buffer events before stopping.
        """
        self._running = False
        if self._task:
            if graceful:
                # Drain remaining
                await self._flush_batch()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info(
            f"AsyncBatchWriter stopped. "
            f"written={self._total_written}, failed={self._total_failed}, "
            f"retried={self._total_retried}, dlq={self._total_dlq}, "
            f"batches={self._batches_flushed}"
        )

    async def _run(self) -> None:
        """Main writer loop."""
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()

        while self._running:
            # Check backpressure
            if self._backpressure:
                self._backpressure.evaluate()

            # Get event from buffer (with timeout)
            get_timeout = 0.5  # Short timeout for responsive shutdown
            event = await self._buffer.get(timeout=get_timeout)

            if event is not None:
                # Check if event should be delayed under backpressure
                event_type = event.get("event_type", "")
                if self._backpressure and self._backpressure.should_delay(event_type):
                    # Put it back — buffer will handle re-queue or spill
                    # For in-memory buffer, we just hold it briefly
                    await asyncio.sleep(0.1)
                    # Re-enqueue by putting back. If the buffer stays full,
                    # preserve the event in DLQ instead of dropping it silently.
                    # Do not wait here: the writer is the active consumer, so
                    # blocking on a full queue can self-deadlock this path.
                    requeued = await self._buffer.put(event)
                    if not requeued:
                        self._dlq.reject(
                            event=event,
                            tags=[TAG_BUFFER_OVERFLOW],
                            reason="Backpressure delay requeue failed: buffer remained full",
                        )
                        self._mark_write_failure("Backpressure delay requeue failed: buffer remained full")
                        self._mark_dlq_write()
                        self._total_dlq += 1
                        self._total_failed += 1
                    continue

                batch.append(event)

            # Flush conditions: batch full or interval elapsed
            now = time.monotonic()
            if len(batch) >= self._batch_size or (batch and (now - last_flush) >= self._batch_interval):
                await self._flush_partitioned_batch(batch)
                batch = []
                last_flush = now

        # Final drain
        if batch:
            await self._flush_partitioned_batch(batch)

    async def _flush_batch(self) -> None:
        """Flush any remaining events from the buffer."""
        remaining = await self._buffer.drain()
        if remaining:
            await self._flush_partitioned_batch(remaining)

    async def _flush_partitioned_batch(self, events: list[dict[str, Any]]) -> None:
        """Group events by partition key and flush each partition."""
        if not events:
            return

        # Group by partition
        partitions: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            key = self._partition_key_fn(event)
            partitions.setdefault(key, []).append(event)

        # Write each partition
        for partition_key, partition_events in partitions.items():
            await self._write_with_retry(partition_events, partition_key)
            self._batches_flushed += 1

    async def _write_with_retry(
        self, batch: list[dict[str, Any]], partition_key: str
    ) -> None:
        """Write a batch with exponential backoff retry."""
        delay = self._base_retry_delay

        for attempt in range(self._max_retries + 1):
            try:
                self._mark_write_attempt(len(batch))
                result = await self._write_fn(batch)

                if result.success:
                    self._total_written += result.count
                    self._mark_write_success()
                    if self._backpressure:
                        self._backpressure.record_write(success=True)
                    return

                # Write failed
                self._mark_write_failure(result.error or "write failed")
                if self._backpressure:
                    self._backpressure.record_write(success=False)

                if not result.retryable:
                    # Poison event — send all to DLQ immediately
                    for event in batch:
                        self._dlq.reject(
                            event=event,
                            tags=[TAG_POISON_EVENT],
                            reason=f"Non-retryable write failure: {result.error}",
                            original_attempt_count=attempt + 1,
                        )
                        self._mark_dlq_write()
                        self._total_dlq += 1
                    self._total_failed += len(batch)
                    return

                # Transient failure — retry
                if attempt < self._max_retries:
                    self._total_retried += 1
                    log.warning(
                        f"Batch write failed (attempt {attempt + 1}/{self._max_retries}), "
                        f"partition={partition_key}, retrying in {delay}s: {result.error}"
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_retry_delay)
                else:
                    # Retries exhausted
                    for event in batch:
                        self._dlq.reject(
                            event=event,
                            tags=[TAG_RETRY_EXHAUSTED],
                            reason=f"Write failed after {self._max_retries} retries: {result.error}",
                            original_attempt_count=self._max_retries + 1,
                        )
                        self._mark_dlq_write()
                        self._total_dlq += 1
                    self._total_failed += len(batch)
                    return

            except Exception as e:
                self._mark_write_failure(e)
                if self._backpressure:
                    self._backpressure.record_write(success=False)

                if attempt < self._max_retries:
                    self._total_retried += 1
                    log.warning(
                        f"Batch write exception (attempt {attempt + 1}/{self._max_retries}), "
                        f"partition={partition_key}, retrying in {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_retry_delay)
                else:
                    for event in batch:
                        self._dlq.reject(
                            event=event,
                            tags=[TAG_WRITER_ERROR],
                            reason=f"Write exception after {self._max_retries} retries: {e}",
                            original_attempt_count=self._max_retries + 1,
                        )
                        self._mark_dlq_write()
                        self._total_dlq += 1
                    self._total_failed += len(batch)
                    log.error(
                        f"Batch write permanently failed after {self._max_retries} retries: {e}"
                    )
                    return

    def stats(self) -> dict[str, Any]:
        """Return writer statistics."""
        return {
            "running": self._running,
            "total_written": self._total_written,
            "total_failed": self._total_failed,
            "total_retried": self._total_retried,
            "total_dlq": self._total_dlq,
            "batches_flushed": self._batches_flushed,
            "batch_size": self._batch_size,
            "batch_interval": self._batch_interval,
            "max_retries": self._max_retries,
            "last_batch_size": self._last_batch_size,
            "last_write_attempt_at": self._iso_from_epoch(self._last_write_attempt_ts),
            "last_successful_write_at": self._iso_from_epoch(self._last_successful_write_ts),
            "last_failed_write_at": self._iso_from_epoch(self._last_failed_write_ts),
            "last_dlq_at": self._iso_from_epoch(self._last_dlq_ts),
            "seconds_since_last_write_attempt": self._age_seconds(self._last_write_attempt_ts),
            "seconds_since_last_successful_write": self._age_seconds(self._last_successful_write_ts),
            "seconds_since_last_failed_write": self._age_seconds(self._last_failed_write_ts),
            "seconds_since_last_dlq": self._age_seconds(self._last_dlq_ts),
            "last_error": self._last_error,
        }
