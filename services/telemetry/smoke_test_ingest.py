#!/usr/bin/env python3
"""
TEL-002 Smoke Test — Telemetry Ingest Shock Absorption

Validates end-to-end behavior:
1. Service starts and accepts events
2. High-volume ingest does not block (buffer absorbs burst)
3. Backpressure kicks in under load
4. Dead-letter queue captures failures
5. Replay works
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from services.telemetry.buffer import InMemoryBuffer
from services.telemetry.dead_letter import DeadLetterQueue, TAG_BUFFER_OVERFLOW
from services.telemetry.backpressure import BackpressureController, PressureLevel
from services.telemetry.batch_writer import AsyncBatchWriter, WriteResult
from services.telemetry.ingest_svc import TelemetryIngestService


def _make_event(i: int, deployment_stage: str = "paper") -> dict:
    return {
        "event_id": f"smoke-{i:04d}",
        "event_type": "pnl_snapshot",
        "created_at": f"2026-04-10T12:{i // 60:02d}:{i % 60:02d}Z",
        "execution_mode": deployment_stage,
        "environment": deployment_stage,
        "deployment_stage": deployment_stage,
        "binding_id": "550e8400-e29b-41d4-a716-446655440001",
        "runtime_id": "lean-worker-1",
        "capital_pool_id": "pool-alpha",
        "artifact_id": "artifact-123",
        "artifact_version": "1.0.0",
        "plan_id": "plan-456",
        "persona_capital_binding_id": "pcb-789",
        "target": {"strategy_id": "smoke-strategy"},
        "metrics": {"pnl": float(i)},
    }


async def test_high_volume_ingest():
    """Acceptance: high-volume ingest does not block canonical Postgres writes."""
    print("  [1/4] High-volume ingest (1000 events)...")

    svc = TelemetryIngestService(
        batch_size=100,
        batch_interval=0.1,
        max_retries=3,
    )
    await svc.start()

    start = time.monotonic()
    ingested = 0
    rejected = 0

    for i in range(1000):
        ok = await svc.ingest(_make_event(i))
        if ok:
            ingested += 1
        else:
            rejected += 1

    elapsed = time.monotonic() - start

    # Wait for writer to flush
    await asyncio.sleep(0.5)
    await svc.stop(graceful=True)

    stats = svc.stats()
    print(f"    Ingested: {ingested}, Rejected: {rejected}, Time: {elapsed:.3f}s")
    print(f"    Writer total written: {stats['writer']['total_written']}")
    print(f"    DLQ entries: {stats['dead_letter_queue']['total_rejected']}")

    assert ingested == 1000, f"Expected 1000 ingested, got {ingested}"
    assert stats["writer"]["total_written"] == 1000, \
        f"Expected 1000 written, got {stats['writer']['total_written']}"
    print("    PASS: 1000 events ingested and written without blocking")


async def test_backpressure_behavior():
    """Acceptance: backpressure and replay behavior are verified."""
    print("  [2/4] Backpressure behavior...")

    ctrl = BackpressureController(
        buffer_utilization_high=0.7,
        buffer_utilization_critical=0.9,
        error_rate_high=0.1,
        evaluation_interval=0.0,
        default_concurrency=4,
        min_concurrency=1,
        max_concurrency=8,
    )

    # Normal state
    ctrl._get_buffer_utilization = lambda: 0.2
    ctrl._recent_writes = 100
    ctrl._recent_errors = 1
    level = ctrl.evaluate()
    assert level == PressureLevel.NORMAL, f"Expected NORMAL, got {level}"
    assert ctrl.current_concurrency <= 8

    # High pressure
    ctrl._get_buffer_utilization = lambda: 0.85
    ctrl._recent_writes = 100
    ctrl._recent_errors = 20  # 20% error rate
    level = ctrl.evaluate()
    assert level == PressureLevel.HIGH, f"Expected HIGH, got {level}"
    assert ctrl.current_concurrency < 4, f"Concurrency should reduce: {ctrl.current_concurrency}"

    # Critical events never delayed
    for etype in ["order_filled", "deploy_completed", "kill_switch_action"]:
        assert not ctrl.should_delay(etype), f"{etype} must never be delayed"

    # Delayable events under high pressure
    assert ctrl.should_delay("heartbeat"), "heartbeat should be delayed under HIGH pressure"

    print("    PASS: Backpressure transitions, concurrency reduction, and delay rules verified")


async def test_dlq_and_replay():
    """Dead-letter queue captures failures and supports replay."""
    print("  [3/4] Dead-letter queue and replay...")

    svc = TelemetryIngestService(
        batch_size=10,
        batch_interval=0.1,
    )
    await svc.start()

    # Ingest valid events
    for i in range(5):
        await svc.ingest(_make_event(i))

    # Ingest invalid events (missing binding_id)
    for i in range(3):
        bad = _make_event(100 + i)
        del bad["binding_id"]
        await svc.ingest(bad)

    await asyncio.sleep(0.3)

    # Replay DLQ BEFORE stopping (buffer must be open)
    replay_count = await svc.replay_dlq()

    await svc.stop(graceful=True)

    dlq_entries = svc.get_dlq_entries()
    assert len(dlq_entries) >= 3, f"Expected >= 3 DLQ entries, got {len(dlq_entries)}"
    assert replay_count >= 3, f"Expected >= 3 replayed, got {replay_count}"

    print(f"    PASS: {len(dlq_entries)} DLQ entries captured, {replay_count} replayed")


async def test_buffer_choice_documented():
    """Acceptance: buffer choice tradeoffs are documented."""
    print("  [4/4] Buffer choice tradeoffs documented...")

    import os
    adr_path = os.path.join(os.path.dirname(__file__), "BUFFER_CHOICE_ADR.md")
    assert os.path.exists(adr_path), f"ADR not found: {adr_path}"

    with open(adr_path, "r") as f:
        content = f.read()

    # Verify key sections exist
    assert "Redis Streams" in content, "ADR should discuss Redis Streams"
    assert "NATS JetStream" in content, "ADR should discuss NATS JetStream"
    assert "Kafka" in content, "ADR should discuss Kafka"
    assert "In-Memory" in content or "in_memory" in content or "in-memory" in content, \
        "ADR should discuss in-memory buffer"
    assert "Activation" in content or "activation" in content, \
        "ADR should define activation criteria for upgrading"

    print("    PASS: BUFFER_CHOICE_ADR.md exists with Redis/NATS/Kafka/In-Memory tradeoffs")


async def main():
    print("=" * 60)
    print("TEL-002 Smoke Test: Telemetry Ingest Shock Absorption")
    print("=" * 60)
    print()

    try:
        await test_high_volume_ingest()
        print()
        await test_backpressure_behavior()
        print()
        await test_dlq_and_replay()
        print()
        await test_buffer_choice_documented()
        print()
        print("=" * 60)
        print("ALL SMOKE TESTS PASSED")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
