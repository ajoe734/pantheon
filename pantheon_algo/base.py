"""
PantheonAlgoBase
================
Base QCAlgorithm class that wires the Pantheon signal consumer into LEAN.

Responsibilities:
- Bootstrap SignalStoreClient using environment / Object Store config
- Schedule SignalConsumer.drain() every minute
- Expose flush_rebalance() for FinRL batch completion callbacks

This module intentionally imports from the Pantheon services path.
When running inside LEAN's Docker container, the services/ directory
is expected to be mounted or installed so that the import resolves.

Import path assumption:
    /app/services/execution/lean-runtime/ must be on PYTHONPATH
    (set this in docker-compose lean service environment or via lean.json pythonVenv)
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# Guard import so this file can be parsed without LEAN runtime present
try:
    from AlgorithmImports import (  # type: ignore[import]
        QCAlgorithm,
        TimeSpan,
    )
    _LEAN_AVAILABLE = True
except ImportError:
    # Outside LEAN: define a stub base class so unit tests can import this module
    class QCAlgorithm:  # type: ignore[no-redef]
        def Initialize(self): pass
        def Schedule(self): return _ScheduleStub()
    _LEAN_AVAILABLE = False


class PantheonAlgoBase(QCAlgorithm):
    """
    Subclass this instead of QCAlgorithm to get Pantheon signal consumption.

    The subclass must call super().Initialize() first, then add its own
    securities and indicators.
    """

    def Initialize(self) -> None:
        self._consumer = self._build_consumer()
        if self._consumer and _LEAN_AVAILABLE:
            self.Schedule.On(
                self.DateRules.EveryDay(),
                self.TimeRules.Every(TimeSpan.FromMinutes(1)),
                lambda: self._consumer.drain(algo=self),
            )
            log.info("Pantheon SignalConsumer scheduled (every 1 min)")
        else:
            log.warning("Pantheon SignalConsumer not available — running without signal intake")

    def flush_rebalance(self, run_id: str) -> None:
        """Call when FinRL signals all legs for a run_id are delivered."""
        if self._consumer:
            self._consumer.flush_rebalance(run_id, algo=self)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_consumer(self) -> Any | None:
        try:
            from services.execution.lean_runtime.signal_consumer import SignalConsumer  # type: ignore[import]
            from services.signal_store.client import SignalStoreClient  # type: ignore[import]
        except ImportError as exc:
            log.error("Cannot import Pantheon runtime modules: %s — signal consumer disabled", exc)
            return None

        redis_url = os.getenv("SIGNAL_STORE_URL", "redis://signal-store:6379")
        try:
            store = SignalStoreClient(redis_url=redis_url)
            return SignalConsumer(store_client=store)
        except Exception as exc:
            log.error("Failed to initialise SignalConsumer: %s — running without signal intake", exc)
            return None


# ---------------------------------------------------------------------------
# Stub for non-LEAN environments
# ---------------------------------------------------------------------------

class _ScheduleStub:
    def On(self, *args, **kwargs): pass
