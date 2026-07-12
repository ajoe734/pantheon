"""In-process API latency recorder for the Trade Journey BFF.

TJ-E2E-011 review found that the `detail_api_p95_seconds` and
`resolve_p95_seconds` SLO targets (gap-spec section 13) were loaded from
`trade_journey_slo_targets.json` but never measured against real request
latency. This is the measurement side: a small bounded ring buffer the BFF
router records real request latency into
(`services/control-plane/bff/trade_journeys.py`), which
`slo_data_quality.compute_data_quality_metrics()` then reads to compute
`detail_api_p95_ms` / `resolve_api_p95_ms`.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Tuple

DEFAULT_WINDOW_SIZE = 500


class ApiLatencyRecorder:
    """Process-local, bounded-memory p95 latency sample store per endpoint."""

    def __init__(self, *, window_size: int = DEFAULT_WINDOW_SIZE) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self._window_size = window_size
        self._samples: Dict[str, Deque[float]] = {}

    def record(self, endpoint: str, latency_ms: float) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        bucket = self._samples.setdefault(endpoint, deque(maxlen=self._window_size))
        bucket.append(float(latency_ms))

    def samples(self, endpoint: str) -> Tuple[float, ...]:
        return tuple(self._samples.get(endpoint, ()))

    def reset(self) -> None:
        self._samples.clear()
