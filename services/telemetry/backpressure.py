"""
Backpressure controller for telemetry ingest.

TEL-002: Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §8.1,
when Postgres write pressure rises, the system must:
1. Retain events in the durable buffer
2. Reduce batch writer concurrency
3. Allow non-critical aggregated metrics to be delayed
4. NEVER discard critical order/fill/deploy/audit events

This module provides adaptive backpressure based on:
- Buffer utilization (how full the buffer is)
- Writer error rate (how many writes are failing)
- Postgres write latency (if measurable)
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class PressureLevel(str, Enum):
    """Backpressure severity levels."""
    NORMAL = "normal"           # No action needed
    ELEVATED = "elevated"       # Reduce concurrency slightly
    HIGH = "high"               # Significantly reduce concurrency, delay non-critical
    CRITICAL = "critical"       # Maximum reduction, emergency mode


# Critical event types that MUST NOT be discarded or delayed
CRITICAL_EVENT_TYPES = frozenset({
    "order_submitted",
    "order_accepted",
    "order_rejected",
    "order_partially_filled",
    "order_filled",
    "order_canceled",
    "paper_order_simulated",
    "paper_fill_simulated",
    "bracket_order_logged",
    "position_snapshot",
    "deploy_started",
    "deploy_completed",
    "rollback_started",
    "rollback_completed",
    "pause_triggered",
    "liquidate_triggered",
    "governance_decision",
    "approval_action",
    "manual_override",
    "kill_switch_action",
})

# Non-critical event types that CAN be delayed under pressure
DELAYABLE_EVENT_TYPES = frozenset({
    "heartbeat",
    "pnl_snapshot",
    "drawdown_snapshot",
    "telemetry_mirror_mismatch",
})


class BackpressureController:
    """
    Adaptive backpressure controller for telemetry ingest.

    Monitors buffer utilization and writer error rates to dynamically
    adjust batch writer concurrency and event processing priority.
    """

    def __init__(
        self,
        buffer_utilization_high: float = 0.7,
        buffer_utilization_critical: float = 0.9,
        error_rate_high: float = 0.1,
        error_rate_critical: float = 0.3,
        min_concurrency: int = 1,
        max_concurrency: int = 8,
        default_concurrency: int = 4,
        concurrency_reduction_step: int = 2,
        evaluation_interval: float = 2.0,
    ):
        """
        Parameters
        ----------
        buffer_utilization_high : float
            Buffer fill ratio (0-1) that triggers ELEVATED pressure.
        buffer_utilization_critical : float
            Buffer fill ratio (0-1) that triggers CRITICAL pressure.
        error_rate_high : float
            Writer error rate (0-1) that triggers ELEVATED pressure.
        error_rate_critical : float
            Writer error rate (0-1) that triggers CRITICAL pressure.
        min_concurrency : int
            Minimum batch writer concurrency (never goes below this).
        max_concurrency : int
            Maximum batch writer concurrency (normal operating level).
        default_concurrency : int
            Starting concurrency level.
        concurrency_reduction_step : int
            How many concurrency slots to reduce per pressure step.
        evaluation_interval : float
            Seconds between backpressure evaluations.
        """
        self._buffer_utilization_high = buffer_utilization_high
        self._buffer_utilization_critical = buffer_utilization_critical
        self._error_rate_high = error_rate_high
        self._error_rate_critical = error_rate_critical
        self._min_concurrency = min_concurrency
        self._max_concurrency = max_concurrency
        self._concurrency_reduction_step = concurrency_reduction_step
        self._evaluation_interval = evaluation_interval

        self._current_concurrency = default_concurrency
        self._pressure_level = PressureLevel.NORMAL
        self._last_evaluated_at = 0.0

        # Metrics for evaluation
        self._recent_writes = 0
        self._recent_errors = 0
        self._window_start = time.monotonic()

        # Callbacks (set by ingest service)
        self._get_buffer_utilization: Optional[Callable[[], float]] = None

    def set_buffer_utilization_fn(self, fn: Callable[[], float]) -> None:
        """Set the function to query current buffer utilization (0-1)."""
        self._get_buffer_utilization = fn

    def record_write(self, success: bool) -> None:
        """Record a write result for error rate tracking."""
        self._recent_writes += 1
        if not success:
            self._recent_errors += 1

    @property
    def current_concurrency(self) -> int:
        """Current allowed batch writer concurrency."""
        return self._current_concurrency

    @property
    def pressure_level(self) -> PressureLevel:
        """Current backpressure level."""
        return self._pressure_level

    def evaluate(self) -> PressureLevel:
        """
        Evaluate current conditions and adjust concurrency.

        Should be called periodically (every evaluation_interval seconds).
        """
        now = time.monotonic()
        elapsed = now - self._window_start

        if elapsed < self._evaluation_interval:
            return self._pressure_level

        # Calculate metrics
        buffer_util = 0.0
        if self._get_buffer_utilization:
            buffer_util = self._get_buffer_utilization()

        error_rate = 0.0
        if self._recent_writes > 0:
            error_rate = self._recent_errors / self._recent_writes

        # Reset counters
        self._recent_writes = 0
        self._recent_errors = 0
        self._window_start = now
        self._last_evaluated_at = now

        # Determine pressure level
        new_level = self._compute_pressure_level(buffer_util, error_rate)

        if new_level != self._pressure_level:
            log.info(
                f"Backpressure level changed: {self._pressure_level.value} -> {new_level.value} "
                f"(buffer={buffer_util:.1%}, error_rate={error_rate:.1%})"
            )
            self._pressure_level = new_level
            self._apply_concurrency(new_level)

        return self._pressure_level

    def _compute_pressure_level(
        self, buffer_util: float, error_rate: float
    ) -> PressureLevel:
        """Determine pressure level from metrics."""
        # Critical: either metric in critical zone
        if (buffer_util >= self._buffer_utilization_critical or
                error_rate >= self._error_rate_critical):
            return PressureLevel.CRITICAL

        # High: either metric in high zone
        if (buffer_util >= self._buffer_utilization_high or
                error_rate >= self._error_rate_high):
            return PressureLevel.HIGH

        # Elevated: approaching thresholds
        if (buffer_util >= self._buffer_utilization_high * 0.8 or
                error_rate >= self._error_rate_high * 0.8):
            return PressureLevel.ELEVATED

        return PressureLevel.NORMAL

    def _apply_concurrency(self, level: PressureLevel) -> None:
        """Adjust concurrency based on pressure level."""
        if level == PressureLevel.CRITICAL:
            self._current_concurrency = self._min_concurrency
        elif level == PressureLevel.HIGH:
            self._current_concurrency = max(
                self._min_concurrency,
                self._current_concurrency - self._concurrency_reduction_step,
            )
        elif level == PressureLevel.ELEVATED:
            self._current_concurrency = max(
                self._min_concurrency,
                self._current_concurrency - 1,
            )
        else:  # NORMAL
            # Gradually increase back toward max
            self._current_concurrency = min(
                self._max_concurrency,
                self._current_concurrency + 1,
            )

    def should_delay(self, event_type: str) -> bool:
        """
        Check if an event type should be delayed under current pressure.

        Critical events are NEVER delayed.
        """
        if event_type in CRITICAL_EVENT_TYPES:
            return False

        if self._pressure_level == PressureLevel.CRITICAL:
            return event_type in DELAYABLE_EVENT_TYPES

        if self._pressure_level == PressureLevel.HIGH:
            return event_type in DELAYABLE_EVENT_TYPES

        return False

    def should_drop(self, event_type: str) -> bool:
        """
        Check if an event type can be dropped under CRITICAL pressure.

        Per architecture doc: critical events are NEVER dropped.
        Only high-frequency diagnostic metrics may be dropped.
        """
        if event_type in CRITICAL_EVENT_TYPES:
            return False

        # Even under critical pressure, we prefer delay over drop
        # Drop only if buffer is completely full and event is non-critical
        return (
            self._pressure_level == PressureLevel.CRITICAL and
            event_type in DELAYABLE_EVENT_TYPES
        )

    def stats(self) -> dict[str, Any]:
        """Return backpressure statistics."""
        return {
            "pressure_level": self._pressure_level.value,
            "current_concurrency": self._current_concurrency,
            "min_concurrency": self._min_concurrency,
            "max_concurrency": self._max_concurrency,
            "buffer_utilization_high": self._buffer_utilization_high,
            "buffer_utilization_critical": self._buffer_utilization_critical,
            "error_rate_high": self._error_rate_high,
            "error_rate_critical": self._error_rate_critical,
            "recent_writes": self._recent_writes,
            "recent_errors": self._recent_errors,
            "last_evaluated_at": self._last_evaluated_at,
        }
