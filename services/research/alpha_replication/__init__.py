"""Alpha replication queue and scheduled revalidation worker.

Accepts approved StrategySpec entries and schedules revalidation runs via
the research-worker-gateway stub path. Production adapters remain fail-closed.
"""

from .queue import AlphaReplicationQueue
from .revalidation_worker import AlphaRevalidationWorker, RevalidationWorkerMetrics

__all__ = [
    "AlphaReplicationQueue",
    "AlphaRevalidationWorker",
    "RevalidationWorkerMetrics",
]
