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

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_MANAGED_CONTEXT_STAGES = {"staging", "canary", "live", "prod", "production"}
_MANAGED_CONTEXT_ROLES = {"staging", "canary", "live", "prod", "production"}

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
        self._pantheon_context = self._load_pantheon_context()
        if self._pantheon_context:
            self.emit_pantheon_event("RuntimeContextLoaded")
        else:
            self.emit_pantheon_event(
                "RuntimeContextMissing",
                metadata={"context_source": "unavailable"},
            )

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

    def get_pantheon_context(self) -> Any | None:
        """Return the loaded PantheonRuntimeContext, or None when unavailable."""
        return getattr(self, "_pantheon_context", None)

    def emit_pantheon_event(
        self,
        event_type: str,
        metrics: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build and emit a Pantheon event payload with runtime context attached."""
        payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "event_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "metrics": dict(metrics or {}),
            "metadata": dict(metadata or {}),
        }
        context = self.get_pantheon_context()
        if context is not None:
            payload.update(self._pantheon_context_fields(context))

        self._last_pantheon_event = payload
        self._emit_pantheon_event_payload(payload)
        return payload

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_pantheon_context(self) -> Any | None:
        try:
            from services.execution.lean_runtime.runtime_context import (  # type: ignore[import]
                PantheonRuntimeContext,
                RuntimeContextError,
            )
        except ImportError as exc:
            if self._runtime_context_required():
                raise RuntimeError(
                    "Pantheon runtime context is required but runtime_context.py is unavailable"
                ) from exc
            log.warning("Cannot import PantheonRuntimeContext: %s", exc)
            return None

        expected_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE") or os.getenv("PANTHEON_RUNTIME_MODE")
        manifest = os.getenv("PANTHEON_LAUNCH_MANIFEST")
        try:
            if manifest:
                return PantheonRuntimeContext.from_manifest(
                    manifest,
                    expected_stage=expected_stage,
                    managed_runtime=True,
                )
            if self._env_has_runtime_context():
                return PantheonRuntimeContext.from_env(
                    os.environ,
                    expected_stage=expected_stage,
                    managed_runtime=True,
                )
        except RuntimeContextError:
            raise

        if self._runtime_context_required():
            raise RuntimeContextError(
                "Pantheon runtime context is required for deployment-managed runtime"
            )
        return None

    def _runtime_context_required(self) -> bool:
        stage = (os.getenv("PANTHEON_DEPLOYMENT_STAGE") or os.getenv("PANTHEON_RUNTIME_MODE") or "").lower()
        role = (os.getenv("PANTHEON_RUNTIME_ROLE") or "").lower()
        return stage in _MANAGED_CONTEXT_STAGES or role in _MANAGED_CONTEXT_ROLES

    def _env_has_runtime_context(self) -> bool:
        keys = (
            "PANTHEON_RUNTIME_BINDING_ID",
            "PANTHEON_RUNTIME_ID",
            "PANTHEON_PAPER_RUNTIME_ID",
            "PANTHEON_DEPLOYMENT_PLAN_ID",
            "PANTHEON_ARTIFACT_ID",
            "PANTHEON_CAPITAL_POOL_ID",
        )
        return any(os.getenv(key) for key in keys)

    def _pantheon_context_fields(self, context: Any) -> dict[str, Any]:
        return {
            "runtime_binding_id": context.runtime_binding_id,
            "runtime_id": context.runtime_id,
            "deployment_plan_id": context.deployment_plan_id,
            "deployment_stage": context.deployment_stage,
            "runtime_role": context.runtime_role,
            "artifact_id": context.artifact.artifact_id,
            "artifact_version": context.artifact.artifact_version,
            "artifact_checksum": context.artifact.artifact_checksum,
            "strategy_id": context.artifact.strategy_id,
            "capital_pool_id": context.capital.capital_pool_id,
            "persona_capital_binding_id": context.capital.persona_capital_binding_id,
            "engine_bridge_repo": context.bridge.repo,
            "engine_bridge_path": context.bridge.path,
            "engine_bridge_commit": context.bridge.commit,
            "runtime_adapter_version": context.bridge.runtime_adapter_version,
            "trace_id": context.trace.trace_id,
            "correlation_id": context.trace.correlation_id,
            "context_source": context.context_source.value,
        }

    def _emit_pantheon_event_payload(self, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        debug = getattr(self, "Debug", None)
        if callable(debug):
            debug(message)
            return
        lean_log = getattr(self, "Log", None)
        if callable(lean_log):
            lean_log(message)
            return
        log.info("Pantheon event: %s", message)

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
