"""Approved-only tenant-scoped Alpha replication.

The worker evaluates immutable StrategySpec reviews and acknowledges queue work
only after authoritative ExperimentTask/ExperimentRun service readback.
Production activation remains fail-closed.
"""

from .queue import AlphaReplicationQueue
from .revalidation_worker import AlphaRevalidationWorker, RevalidationWorkerMetrics

__all__ = [
    "AlphaReplicationQueue",
    "AlphaRevalidationWorker",
    "RevalidationWorkerMetrics",
]
