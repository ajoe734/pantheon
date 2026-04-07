"""
Execution Telemetry Capture Module

Captures execution telemetry including pnl, drawdown, slippage, and fills
across paper and live trading modes. Events are validated against
execution_telemetry_event.schema.json and can be persisted to the feedback store
for evolution plane evaluation.
"""

from __future__ import annotations

from .capture import TelemetryCapture
from .feedback_adapter import FeedbackStoreAdapter

__all__ = ["TelemetryCapture", "FeedbackStoreAdapter"]
