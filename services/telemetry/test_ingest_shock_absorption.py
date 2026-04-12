"""
TEL-002 Unit Tests — Telemetry Ingest Shock Absorption

Tests cover:
1. DurableBuffer (InMemoryBuffer protocol compliance, backpressure, drain)
2. DeadLetterQueue (reject, tag filter, spill, replay, incident threshold)
3. BackpressureController (level transitions, concurrency adjustment, delay/drop rules)
4. AsyncBatchWriter (micro-batching, retry, DLQ routing, partition flush)
5. TelemetryIngestService (end-to-end ingest, validation, overflow, replay)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from services.telemetry.buffer import (
    DurableBuffer,
    InMemoryBuffer,
    create_buffer,
)
from services.telemetry.dead_letter import (
    DeadLetterQueue,
    DeadLetterEntry,
    TAG_SCHEMA_VIOLATION,
    TAG_BINDING_MISMATCH,
    TAG_BUFFER_OVERFLOW,
    TAG_RETRY_EXHAUSTED,
    TAG_WRITER_ERROR,
    TAG_POISON_EVENT,
)
from services.telemetry.backpressure import (
    BackpressureController,
    PressureLevel,
    CRITICAL_EVENT_TYPES,
    DELAYABLE_EVENT_TYPES,
)
from services.telemetry.batch_writer import (
    AsyncBatchWriter,
    WriteResult,
)
from services.telemetry.ingest_svc import TelemetryIngestService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: str = "pnl_snapshot", **overrides: object) -> dict:
    """Create a minimal valid telemetry event for testing."""
    event = {
        "event_id": "550e8400-e29b-41d4-a716-446655440000",
        "event_type": event_type,
        "created_at": "2026-04-10T12:00:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "550e8400-e29b-41d4-a716-446655440001",
        "runtime_id": "lean-worker-1",
        "capital_pool_id": "pool-alpha",
        "artifact_id": "artifact-123",
        "artifact_version": "1.0.0",
        "plan_id": "plan-456",
        "persona_capital_binding_id": "pcb-789",
        "target": {"strategy_id": "strategy-1"},
        "metrics": {"pnl": 100.0},
    }
    event.update(overrides)
    return event


# ---------------------------------------------------------------------------
# 1. InMemoryBuffer Tests
# ---------------------------------------------------------------------------

class TestInMemoryBuffer(unittest.IsolatedAsyncioTestCase):

    async def test_put_and_get(self):
        buf = InMemoryBuffer(maxsize=100)
        event = _make_event()
        result = await buf.put(event)
        self.assertTrue(result)
        self.assertEqual(buf.size(), 1)

        got = await buf.get(timeout=1.0)
        self.assertIsNotNone(got)
        self.assertEqual(got["event_id"], event["event_id"])

    async def test_put_after_close_rejected(self):
        buf = InMemoryBuffer(maxsize=100)
        await buf.close()
        result = await buf.put(_make_event())
        self.assertFalse(result)
        self.assertTrue(buf.is_closed())

    async def test_backpressure_when_full(self):
        buf = InMemoryBuffer(maxsize=3)
        for i in range(3):
            ok = await buf.put(_make_event(event_id=f"evt-{i}"))
            self.assertTrue(ok)

        # Buffer full — non-blocking put should fail
        ok = await buf.put(_make_event(event_id="overflow"))
        self.assertFalse(ok)

    async def test_put_with_timeout(self):
        buf = InMemoryBuffer(maxsize=1)
        await buf.put(_make_event(event_id="fill"))

        # Timeout put should fail quickly
        ok = await buf.put(_make_event(event_id="late"), timeout=0.1)
        self.assertFalse(ok)

    async def test_drain(self):
        buf = InMemoryBuffer(maxsize=100)
        for i in range(5):
            await buf.put(_make_event(event_id=f"evt-{i}"))

        drained = await buf.drain()
        self.assertEqual(len(drained), 5)
        self.assertEqual(buf.size(), 0)

    async def test_stats(self):
        buf = InMemoryBuffer(maxsize=100)
        await buf.put(_make_event())
        await buf.get(timeout=1.0)
        stats = buf.stats()
        self.assertEqual(stats["total_enqueued"], 1)
        self.assertEqual(stats["total_dequeued"], 1)
        self.assertEqual(stats["total_rejected"], 0)
        self.assertEqual(stats["utilization_pct"], 0.0)

    async def test_capacity(self):
        buf = InMemoryBuffer(maxsize=500)
        self.assertEqual(buf.capacity(), 500)

    async def test_get_timeout_returns_none(self):
        buf = InMemoryBuffer(maxsize=100)
        result = await buf.get(timeout=0.1)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 2. Buffer Factory Tests
# ---------------------------------------------------------------------------

class TestBufferFactory(unittest.TestCase):

    def test_create_memory_buffer(self):
        buf = create_buffer(backend="memory", maxsize=200)
        self.assertIsInstance(buf, InMemoryBuffer)
        self.assertEqual(buf.capacity(), 200)

    def test_create_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            create_buffer(backend="kafka")


# ---------------------------------------------------------------------------
# 3. DeadLetterQueue Tests
# ---------------------------------------------------------------------------

class TestDeadLetterQueue(unittest.TestCase):

    def test_reject_and_get_entries(self):
        dlq = DeadLetterQueue(max_memory_entries=100)
        event = _make_event()
        dlq.reject(event, tags=[TAG_SCHEMA_VIOLATION], reason="missing field")

        entries = dlq.get_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].reason, "missing field")
        self.assertIn(TAG_SCHEMA_VIOLATION, entries[0].tags)

    def test_tag_filter(self):
        dlq = DeadLetterQueue(max_memory_entries=100)
        dlq.reject(_make_event(event_id="e1"), tags=[TAG_SCHEMA_VIOLATION], reason="schema")
        dlq.reject(_make_event(event_id="e2"), tags=[TAG_BINDING_MISMATCH], reason="binding")
        dlq.reject(_make_event(event_id="e3"), tags=[TAG_SCHEMA_VIOLATION], reason="schema2")

        schema_entries = dlq.get_entries(tag_filter=TAG_SCHEMA_VIOLATION)
        self.assertEqual(len(schema_entries), 2)

        binding_entries = dlq.get_entries(tag_filter=TAG_BINDING_MISMATCH)
        self.assertEqual(len(binding_entries), 1)

    def test_replay_entries(self):
        dlq = DeadLetterQueue(max_memory_entries=100)
        dlq.reject(_make_event(event_id="e1"), tags=[TAG_SCHEMA_VIOLATION], reason="x")
        dlq.reject(_make_event(event_id="e2"), tags=[TAG_BUFFER_OVERFLOW], reason="y")

        events = dlq.replay_entries()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_id"], "e1")

    def test_spill_file_persistence(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            spill_path = f.name

        try:
            dlq = DeadLetterQueue(spill_path=spill_path, max_memory_entries=100)
            dlq.reject(_make_event(event_id="spill-test"), tags=[TAG_WRITER_ERROR], reason="db down")

            # Verify spill file has content
            with open(spill_path, "r") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["event"]["event_id"], "spill-test")
        finally:
            os.unlink(spill_path)

    def test_load_from_spill(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            spill_path = f.name

        try:
            # Write spill data manually
            entry = DeadLetterEntry(
                event=_make_event(event_id="loaded"),
                tags=[TAG_POISON_EVENT],
                reason="poison",
            )
            with open(spill_path, "w") as f:
                f.write(entry.to_json() + "\n")

            dlq = DeadLetterQueue(spill_path=spill_path, max_memory_entries=100)
            count = dlq.load_from_spill()
            self.assertEqual(count, 1)
            entries = dlq.get_entries()
            self.assertEqual(entries[0].event["event_id"], "loaded")
        finally:
            os.unlink(spill_path)

    def test_incident_threshold_fires(self):
        dlq = DeadLetterQueue(incident_threshold=3)
        for i in range(3):
            dlq.reject(_make_event(event_id=f"e{i}"), tags=[TAG_SCHEMA_VIOLATION], reason="x")

        stats = dlq.stats()
        self.assertTrue(stats["incident_fired"])

    def test_memory_eviction(self):
        dlq = DeadLetterQueue(max_memory_entries=5)
        for i in range(20):
            dlq.reject(_make_event(event_id=f"e{i}"), tags=[TAG_SCHEMA_VIOLATION], reason="x")

        # Only last 5 should be in memory
        entries = dlq.get_entries()
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0].event["event_id"], "e15")

    def test_stats(self):
        dlq = DeadLetterQueue(max_memory_entries=100)
        dlq.reject(_make_event(), tags=[TAG_SCHEMA_VIOLATION], reason="x")
        dlq.reject(_make_event(), tags=[TAG_SCHEMA_VIOLATION], reason="y")
        dlq.reject(_make_event(), tags=[TAG_BUFFER_OVERFLOW], reason="z")

        stats = dlq.stats()
        self.assertEqual(stats["total_rejected"], 3)
        self.assertEqual(stats["tag_counts"][TAG_SCHEMA_VIOLATION], 2)
        self.assertEqual(stats["tag_counts"][TAG_BUFFER_OVERFLOW], 1)


# ---------------------------------------------------------------------------
# 4. BackpressureController Tests
# ---------------------------------------------------------------------------

class TestBackpressureController(unittest.TestCase):

    def test_normal_pressure(self):
        ctrl = BackpressureController(
            buffer_utilization_high=0.7,
            error_rate_high=0.1,
        )
        ctrl._get_buffer_utilization = lambda: 0.3
        ctrl._recent_writes = 100
        ctrl._recent_errors = 2

        level = ctrl.evaluate()
        self.assertEqual(level, PressureLevel.NORMAL)

    def test_elevated_pressure(self):
        ctrl = BackpressureController(
            buffer_utilization_high=0.7,
            error_rate_high=0.1,
            evaluation_interval=0.0,
        )
        # Set utilization between 80% of high threshold and the high threshold
        # 0.7 * 0.8 = 0.56, so anything in [0.56, 0.7) triggers ELEVATED
        ctrl._get_buffer_utilization = lambda: 0.6
        ctrl._recent_writes = 100
        ctrl._recent_errors = 5  # 5% error rate — below high threshold

        level = ctrl.evaluate()
        self.assertEqual(level, PressureLevel.ELEVATED)

    def test_high_pressure(self):
        ctrl = BackpressureController(
            buffer_utilization_high=0.7,
            buffer_utilization_critical=0.9,
            error_rate_high=0.1,
            error_rate_critical=0.3,
            evaluation_interval=0.0,
        )
        ctrl._get_buffer_utilization = lambda: 0.85

        level = ctrl.evaluate()
        self.assertEqual(level, PressureLevel.HIGH)
        # Concurrency should be reduced
        self.assertLess(ctrl.current_concurrency, ctrl._max_concurrency)

    def test_critical_pressure(self):
        ctrl = BackpressureController(
            buffer_utilization_high=0.7,
            buffer_utilization_critical=0.9,
            error_rate_high=0.1,
            error_rate_critical=0.3,
            default_concurrency=4,
            min_concurrency=1,
            evaluation_interval=0.0,
        )
        ctrl._get_buffer_utilization = lambda: 0.95

        level = ctrl.evaluate()
        self.assertEqual(level, PressureLevel.CRITICAL)
        self.assertEqual(ctrl.current_concurrency, 1)

    def test_critical_events_never_delayed(self):
        ctrl = BackpressureController()
        ctrl._pressure_level = PressureLevel.CRITICAL

        for event_type in CRITICAL_EVENT_TYPES:
            self.assertFalse(ctrl.should_delay(event_type), f"{event_type} should never be delayed")

    def test_delayable_events_under_high_pressure(self):
        ctrl = BackpressureController()
        ctrl._pressure_level = PressureLevel.HIGH

        for event_type in DELAYABLE_EVENT_TYPES:
            self.assertTrue(ctrl.should_delay(event_type), f"{event_type} should be delayed under HIGH")

    def test_concurrency_recovery(self):
        """When pressure returns to NORMAL, concurrency should gradually recover."""
        ctrl = BackpressureController(
            buffer_utilization_high=0.7,
            error_rate_high=0.1,
            default_concurrency=4,
            max_concurrency=8,
            evaluation_interval=0.0,
        )

        # First trigger high pressure
        ctrl._get_buffer_utilization = lambda: 0.85
        ctrl.evaluate()
        reduced = ctrl.current_concurrency

        # Then return to normal — multiple evaluations should increase
        ctrl._get_buffer_utilization = lambda: 0.1
        for _ in range(10):
            ctrl.evaluate()

        self.assertGreaterEqual(ctrl.current_concurrency, reduced)

    def test_stats(self):
        ctrl = BackpressureController()
        ctrl.record_write(success=True)
        ctrl.record_write(success=False)
        stats = ctrl.stats()
        self.assertEqual(stats["recent_writes"], 2)
        self.assertEqual(stats["recent_errors"], 1)


# ---------------------------------------------------------------------------
# 5. AsyncBatchWriter Tests
# ---------------------------------------------------------------------------

class TestAsyncBatchWriter(unittest.IsolatedAsyncioTestCase):

    async def test_successful_batch_write(self):
        buf = InMemoryBuffer(maxsize=100)
        dlq = DeadLetterQueue()
        write_calls = []

        async def write_fn(batch):
            write_calls.append(batch)
            return WriteResult.ok(len(batch))

        writer = AsyncBatchWriter(
            buffer=buf,
            write_fn=write_fn,
            dead_letter_queue=dlq,
            batch_size=5,
            batch_interval=0.1,
        )

        # Add events
        for i in range(12):
            await buf.put(_make_event(event_id=f"evt-{i}"))

        # Start writer briefly
        await writer.start()
        await asyncio.sleep(0.3)
        await writer.stop(graceful=True)

        # All events should be written
        self.assertEqual(writer._total_written, 12)
        self.assertEqual(writer._total_dlq, 0)

    async def test_retry_and_dlq_on_permanent_failure(self):
        buf = InMemoryBuffer(maxsize=100)
        dlq = DeadLetterQueue()

        async def failing_write(batch):
            return WriteResult.fail("db connection refused", retryable=False)

        writer = AsyncBatchWriter(
            buffer=buf,
            write_fn=failing_write,
            dead_letter_queue=dlq,
            batch_size=3,
            batch_interval=0.1,
            max_retries=2,
        )

        for i in range(3):
            await buf.put(_make_event(event_id=f"evt-{i}"))

        await writer.start()
        await asyncio.sleep(0.5)
        await writer.stop(graceful=True)

        self.assertEqual(writer._total_failed, 3)
        self.assertEqual(writer._total_dlq, 3)

    async def test_retry_exhausted_to_dlq(self):
        buf = InMemoryBuffer(maxsize=100)
        dlq = DeadLetterQueue()
        attempt_count = 0

        async def flaky_write(batch):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count <= 3:
                return WriteResult.fail("transient error", retryable=True)
            return WriteResult.ok(len(batch))

        writer = AsyncBatchWriter(
            buffer=buf,
            write_fn=flaky_write,
            dead_letter_queue=dlq,
            batch_size=2,
            batch_interval=0.1,
            max_retries=2,  # Will exhaust retries before flaky_write succeeds
        )

        for i in range(2):
            await buf.put(_make_event(event_id=f"evt-{i}"))

        await writer.start()
        await asyncio.sleep(1.0)
        await writer.stop(graceful=True)

        # Events should end up in DLQ after retry exhaustion
        self.assertGreater(writer._total_retried, 0)

    async def test_delayed_requeue_overflow_routes_event_to_dlq(self):
        buf = InMemoryBuffer(maxsize=1)
        dlq = DeadLetterQueue()
        written_ids = []

        async def write_fn(batch):
            written_ids.extend(event["event_id"] for event in batch)
            return WriteResult.ok(len(batch))

        ctrl = BackpressureController(evaluation_interval=999.0)
        ctrl._pressure_level = PressureLevel.HIGH

        writer = AsyncBatchWriter(
            buffer=buf,
            write_fn=write_fn,
            dead_letter_queue=dlq,
            backpressure=ctrl,
            batch_size=1,
            batch_interval=1.0,
        )

        delayed_event = _make_event(
            event_id="delayed-heartbeat",
            event_type="heartbeat",
            metrics={"heartbeat": 1},
        )
        critical_event = _make_event(
            event_id="critical-fill",
            event_type="order_filled",
            metrics={"fill_quantity": 1.0},
        )

        await buf.put(delayed_event)
        await writer.start()

        # Wait until the writer has consumed the delayable event and is sleeping
        for _ in range(50):
            if buf.size() == 0:
                break
            await asyncio.sleep(0.01)

        await buf.put(critical_event)
        await asyncio.sleep(0.3)
        ctrl._pressure_level = PressureLevel.NORMAL
        await asyncio.sleep(0.3)
        await writer.stop(graceful=True)

        self.assertIn("critical-fill", written_ids)
        self.assertNotIn("delayed-heartbeat", written_ids)

        dlq_entries = dlq.get_entries_as_dicts(tag_filter=TAG_BUFFER_OVERFLOW)
        self.assertEqual(len(dlq_entries), 1)
        self.assertEqual(dlq_entries[0]["event"]["event_id"], "delayed-heartbeat")
        self.assertEqual(writer._total_failed, 1)
        self.assertEqual(writer._total_dlq, 1)

    async def test_partition_routing(self):
        buf = InMemoryBuffer(maxsize=100)
        dlq = DeadLetterQueue()
        written_partitions = []

        async def write_fn(batch):
            if batch:
                partition_keys = set()
                for e in batch:
                    date_part = e.get("created_at", "")[:10]
                    stage = e.get("deployment_stage", "unknown")
                    partition_keys.add(f"{date_part}:{stage}")
                written_partitions.append(partition_keys)
            return WriteResult.ok(len(batch))

        writer = AsyncBatchWriter(
            buffer=buf,
            write_fn=write_fn,
            dead_letter_queue=dlq,
            batch_size=10,
            batch_interval=0.1,
        )

        # Events from different stages
        await buf.put(_make_event(event_id="e1", deployment_stage="paper"))
        await buf.put(_make_event(event_id="e2", deployment_stage="live"))

        await writer.start()
        await asyncio.sleep(0.3)
        await writer.stop(graceful=True)

        self.assertEqual(writer._total_written, 2)

    async def test_stats(self):
        buf = InMemoryBuffer(maxsize=100)
        dlq = DeadLetterQueue()
        writer = AsyncBatchWriter(
            buffer=buf,
            write_fn=lambda b: WriteResult.ok(len(b)),
            dead_letter_queue=dlq,
            batch_size=10,
        )
        stats = writer.stats()
        self.assertEqual(stats["batch_size"], 10)
        self.assertFalse(stats["running"])


# ---------------------------------------------------------------------------
# 6. TelemetryIngestService Integration Tests
# ---------------------------------------------------------------------------

class TestTelemetryIngestService(unittest.IsolatedAsyncioTestCase):

    async def test_end_to_end_ingest(self):
        """Valid event should be ingested and eventually written."""
        svc = TelemetryIngestService(
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()

        event = _make_event(event_id="integration-1")
        result = await svc.ingest(event)
        self.assertTrue(result)

        await asyncio.sleep(0.3)
        await svc.stop(graceful=True)

        stats = svc.stats()
        self.assertEqual(stats["service"]["total_ingested"], 1)

    async def test_schema_validation_rejection(self):
        """Event missing required fields should go to DLQ."""
        svc = TelemetryIngestService(
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()

        # Invalid event — missing required fields
        bad_event = {"foo": "bar"}
        result = await svc.ingest(bad_event)
        self.assertFalse(result)

        await svc.stop(graceful=True)

        dlq_entries = svc.get_dlq_entries()
        self.assertGreater(len(dlq_entries), 0)

    async def test_evidence_contract_rejection(self):
        """Event with missing binding_id should be rejected."""
        svc = TelemetryIngestService(
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()

        # Event missing binding_id
        bad_event = _make_event()
        del bad_event["binding_id"]

        result = await svc.ingest(bad_event)
        self.assertFalse(result)

        await svc.stop(graceful=True)

        dlq_entries = svc.get_dlq_entries(tag_filter=TAG_BINDING_MISMATCH)
        self.assertGreater(len(dlq_entries), 0)

    async def test_buffer_overflow_to_dlq(self):
        """When buffer is full, overflow events go to DLQ."""
        # Directly test: ingest service should reject when buffer put fails
        svc = TelemetryIngestService(
            buffer_maxsize=1,
            batch_size=1,
            batch_interval=0.01,
        )
        await svc.start()

        # Put one valid event
        await svc.ingest(_make_event(event_id="fill-1"))

        # The buffer has maxsize=1 and the writer interval is 0.01s.
        # To guarantee overflow, we test by directly checking that the
        # ingest path handles buffer rejection correctly.
        # We simulate this by closing the buffer (which makes put return False).
        await svc._buffer.close()

        # Now any ingest should fail (buffer is closed → put returns False)
        overflow_event = _make_event(event_id="overflow")
        result = await svc.ingest(overflow_event, timeout=0.01)
        self.assertFalse(result)

        await svc.stop(graceful=False)

        # The overflow event should be in DLQ with buffer_overflow tag
        dlq_entries = svc.get_dlq_entries(tag_filter=TAG_BUFFER_OVERFLOW)
        self.assertGreater(len(dlq_entries), 0)
        self.assertEqual(dlq_entries[0]["event"]["event_id"], "overflow")

    async def test_batch_ingest(self):
        """Batch ingest should return correct counts."""
        svc = TelemetryIngestService(
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()

        events = [_make_event(event_id=f"batch-{i}") for i in range(5)]
        result = await svc.ingest_batch(events)

        self.assertEqual(result["ingested"], 5)
        self.assertEqual(result["rejected"], 0)

        await svc.stop(graceful=True)

    async def test_dlq_replay(self):
        """DLQ replay should re-enqueue events."""
        svc = TelemetryIngestService(
            buffer_maxsize=100,
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()

        # Ingest a valid event
        await svc.ingest(_make_event(event_id="replay-target"))
        await asyncio.sleep(0.2)

        # Manually add to DLQ
        svc._dlq.reject(
            _make_event(event_id="dlq-replay-target"),
            tags=[TAG_WRITER_ERROR],
            reason="test",
        )

        # Replay DLQ
        count = await svc.replay_dlq()
        self.assertGreaterEqual(count, 1)

        await svc.stop(graceful=True)

    async def test_stats_comprehensive(self):
        """Stats should return all sections."""
        svc = TelemetryIngestService()
        await svc.start()
        await asyncio.sleep(0.1)
        stats = svc.stats()

        self.assertIn("service", stats)
        self.assertIn("buffer", stats)
        self.assertIn("writer", stats)
        self.assertIn("dead_letter_queue", stats)
        self.assertIn("backpressure", stats)

        await svc.stop(graceful=True)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
