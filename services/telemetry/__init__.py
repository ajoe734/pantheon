"""
Execution Telemetry Capture and Ingest Module

TEL-001: Captures execution telemetry including pnl, drawdown, slippage, and fills
across paper and live trading modes. Events carry deployment-stage and runtime-binding
evidence fields (E-1 through E-6) and are validated against telemetry_event.schema.json.

TEL-002: Shock absorption layer — durable buffer + async batch writer + backpressure
controller + dead-letter queue. Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §2.2,
LEAN runtime must NOT directly write to canonical Postgres telemetry tables; all events
flow through TelemetryIngestService.
"""

from __future__ import annotations

from .capture import TelemetryCapture
from .feedback_adapter import FeedbackStoreAdapter
from .buffer import DurableBuffer, InMemoryBuffer, RedisStreamBuffer, create_buffer
from .batch_writer import AsyncBatchWriter, WriteResult
from .backpressure import BackpressureController, PressureLevel, CRITICAL_EVENT_TYPES, DELAYABLE_EVENT_TYPES
from .dead_letter import DeadLetterQueue, DeadLetterEntry
from .ingest_svc import TelemetryIngestService

__all__ = [
    # TEL-001: Capture
    "TelemetryCapture",
    "FeedbackStoreAdapter",
    # TEL-002: Shock absorption
    "DurableBuffer",
    "InMemoryBuffer",
    "RedisStreamBuffer",
    "create_buffer",
    "AsyncBatchWriter",
    "WriteResult",
    "BackpressureController",
    "PressureLevel",
    "CRITICAL_EVENT_TYPES",
    "DELAYABLE_EVENT_TYPES",
    "DeadLetterQueue",
    "DeadLetterEntry",
    "TelemetryIngestService",
]
