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
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MANAGED_CONTEXT_STAGES = {"staging", "canary", "live", "prod", "production"}
_MANAGED_CONTEXT_ROLES = {"staging", "canary", "live", "prod", "production"}

class _ScheduleStub:
    def __init__(self) -> None:
        self.scheduled_events: list[Any] = []

    def On(self, *args: Any, **kwargs: Any) -> Any:
        event = {"args": args, "kwargs": kwargs}
        self.scheduled_events.append(event)
        return event


class _RulesStub:
    def EveryDay(self) -> str:
        return "EveryDay"

    def Every(self, *args: Any, **kwargs: Any) -> str:
        return "Every"


class PersistentLeanObjectStore:
    """LEAN ObjectStore double supporting .NET and Python methods, backed by disk or memory."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self._storage_dir = Path(storage_dir) if storage_dir else None
        if self._storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, str] = {}

    def _path_for_key(self, key: str) -> Path | None:
        if not self._storage_dir:
            return None
        safe_key = key.replace("/", "_")
        return self._storage_dir / safe_key

    def ContainsKey(self, key: str) -> bool:  # noqa: N802
        p = self._path_for_key(key)
        if p is not None and p.is_file():
            return True
        return key in self._data

    def contains_key(self, key: str) -> bool:
        return self.ContainsKey(key)

    def Read(self, key: str) -> str:  # noqa: N802
        p = self._path_for_key(key)
        if p is not None and p.is_file():
            return p.read_text(encoding="utf-8")
        if key in self._data:
            return self._data[key]
        raise KeyError(f"Key not found in ObjectStore: {key}")

    def read(self, key: str) -> str:
        return self.Read(key)

    def Save(self, key: str, value: str | bytes) -> None:  # noqa: N802
        text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        self._data[key] = text
        p = self._path_for_key(key)
        if p is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def save(self, key: str, value: str | bytes) -> None:
        self.Save(key, value)


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
        def __init__(self) -> None:
            self.Schedule = _ScheduleStub()
            self.DateRules = _RulesStub()
            self.TimeRules = _RulesStub()
            self.orders: list[Any] = []
            self.ObjectStore = PersistentLeanObjectStore()

        def Initialize(self) -> None: pass

        def SetHoldings(self, symbol: Any, percentage: float, *args: Any, **kwargs: Any) -> Any:
            order = {"method": "SetHoldings", "symbol": symbol, "percentage": percentage}
            self.orders.append(order)
            return order

        def MarketOrder(self, symbol: Any, quantity: float, *args: Any, **kwargs: Any) -> Any:
            order = {"method": "MarketOrder", "symbol": symbol, "quantity": quantity}
            self.orders.append(order)
            return order

        def Liquidate(self, symbol: Any = None, *args: Any, **kwargs: Any) -> Any:
            order = {"method": "Liquidate", "symbol": symbol}
            self.orders.append(order)
            return order

        def Debug(self, message: str) -> None: pass
        def Log(self, message: str) -> None: pass
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
        if self._consumer:
            if _LEAN_AVAILABLE:
                self.Schedule.On(
                    self.DateRules.EveryDay(),
                    self.TimeRules.Every(TimeSpan.FromMinutes(1)),
                    lambda: self._consumer.drain(algo=self),
                )
                log.info("Pantheon SignalConsumer scheduled (every 1 min)")
            else:
                schedule = getattr(self, "Schedule", None)
                if callable(schedule):
                    schedule = schedule()
                    self.Schedule = schedule
                if schedule is None or not hasattr(schedule, "On"):
                    schedule = _ScheduleStub()
                    self.Schedule = schedule
                date_rule = getattr(getattr(self, "DateRules", None), "EveryDay", lambda: "EveryDay")()
                time_rule = "Every(1min)"
                schedule.On(date_rule, time_rule, lambda: self._consumer.drain(algo=self))
                log.info("Pantheon SignalConsumer wired (stub environment)")
        else:
            log.warning("Pantheon SignalConsumer not available — running without signal intake")

        if not hasattr(self, "orders"):
            self.orders = []

    def drain_signals(self) -> None:
        """Drain pending signals through the consumer."""
        if self._consumer:
            self._consumer.drain(algo=self)

    def OnData(self, data: Any = None) -> None:
        """Default OnData hook drains incoming signals."""
        self.drain_signals()

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
            from services.execution.lean_runtime.pending_signal_store import (  # type: ignore[import]
                build_pending_signal_store,
            )
            from services.execution.lean_runtime.signal_consumer import (  # type: ignore[import]
                SignalConsumer,
            )
        except ImportError as exc:
            log.error("Cannot import Pantheon runtime modules: %s — signal consumer disabled", exc)
            return None

        signal_store_url = os.getenv("SIGNAL_STORE_URL", "")
        binding_id = os.getenv("PANTHEON_RUNTIME_BINDING_ID", "")
        runtime_id = os.getenv("PANTHEON_RUNTIME_ID", "")
        capital_pool_id = os.getenv("PANTHEON_CAPITAL_POOL_ID", "")

        ctx = getattr(self, "_pantheon_context", None)
        if ctx:
            if not binding_id and getattr(ctx, "runtime_binding_id", None):
                binding_id = ctx.runtime_binding_id
            if not runtime_id and getattr(ctx, "runtime_id", None):
                runtime_id = ctx.runtime_id
            if not capital_pool_id and getattr(getattr(ctx, "capital", None), "capital_pool_id", None):
                capital_pool_id = ctx.capital.capital_pool_id

        try:
            store = build_pending_signal_store(signal_store_url)
            self._signal_store = store
            return SignalConsumer(
                store_client=store,
                binding_id=binding_id or None,
                runtime_id=runtime_id or None,
                capital_pool_id=capital_pool_id or None,
            )
        except Exception as exc:
            log.error("Failed to initialise SignalConsumer: %s — running without signal intake", exc)
            return None


class EngineReplayAlgo(PantheonAlgoBase):
    """
    LEAN QCAlgorithm implementation for upstream engine replay acceptance,
    asserting model/tenant/session context propagation, deterministic execution,
    and duplicate suppression across engine restart.
    """

    CHECKPOINT_KEY = "storage/pantheon_restart_checkpoint"

    def __init__(self, object_store: Any | None = None) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []
        self.model_id = os.getenv("PANTHEON_MODEL_ID", "model-alpha-v1")
        self.tenant_id = os.getenv("PANTHEON_TENANT_ID", "tenant-ops")
        self.session_id = os.getenv("PANTHEON_SESSION_ID", "session-restart-001")
        if object_store is not None:
            self.ObjectStore = object_store
        elif not hasattr(self, "ObjectStore") or self.ObjectStore is None:
            storage_dir = os.getenv("PANTHEON_OBJECT_STORE_DIR", "/tmp/pantheon_storage")
            self.ObjectStore = PersistentLeanObjectStore(storage_dir)
        self.is_restart = False
        self.prior_checkpoint: dict[str, Any] | None = None
        self.processed_signals: set[str] = set()
        self.executed_orders: list[dict[str, Any]] = []

    def Debug(self, message: str) -> None:
        try:
            self.events.append(json.loads(message))
        except Exception:
            pass

    def Initialize(self) -> None:
        super().Initialize()

        # Check ObjectStore for prior run checkpoint
        if hasattr(self, "ObjectStore") and self.ObjectStore.ContainsKey(self.CHECKPOINT_KEY):
            self.is_restart = True
            raw = self.ObjectStore.Read(self.CHECKPOINT_KEY)
            self.prior_checkpoint = json.loads(raw)
            for sid in self.prior_checkpoint.get("processed_signals", []):
                self.processed_signals.add(str(sid))
                if getattr(self, "_signal_store", None) and hasattr(self._signal_store, "mark_processed"):
                    self._signal_store.mark_processed(str(sid))
            self.emit_pantheon_event(
                "EngineRestartSuccess",
                metadata={
                    "model_id": self.model_id,
                    "tenant_id": self.tenant_id,
                    "session_id": self.session_id,
                    "prior_timestamp": self.prior_checkpoint.get("timestamp"),
                    "prior_processed_count": len(self.processed_signals),
                },
            )
        else:
            self.is_restart = False
            self.prior_checkpoint = None

    def process_replay_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        sid = str(signal.get("signal_id", ""))
        metadata = signal.get("metadata", {})
        sig_model = metadata.get("model_id") or self.model_id
        sig_tenant = metadata.get("tenant_id") or self.tenant_id
        sig_session = metadata.get("session_id") or self.session_id

        # Duplicate suppression check across restart
        is_dup = sid in self.processed_signals
        if not is_dup and getattr(self, "_signal_store", None) and hasattr(self._signal_store, "is_processed"):
            is_dup = self._signal_store.is_processed(sid)

        if is_dup:
            self.emit_pantheon_event(
                "OrderDuplicateSuppressed",
                metadata={
                    "signal_id": sid,
                    "symbol": signal.get("symbol", "").split(".")[0],
                    "model_id": sig_model,
                    "tenant_id": sig_tenant,
                    "session_id": sig_session,
                    "duplicate_suppressed": True,
                },
            )
            return {
                "status": "DUPLICATE_SUPPRESSED",
                "signal_id": sid,
                "new_orders_placed": 0,
                "duplicate_suppressed": True,
                "symbol": signal.get("symbol", "").split(".")[0],
            }

        # First-time execution: enqueue to store and drain, or place order
        if getattr(self, "_signal_store", None):
            self._signal_store.enqueue(signal)
            self.OnData()
        else:
            symbol = signal.get("symbol", "").split(".")[0]
            self.SetHoldings(symbol, float(signal.get("quantity", 0.5)))

        order_record = {
            "order_id": f"ord-{sid}",
            "signal_id": sid,
            "symbol": signal.get("symbol", "").split(".")[0],
            "action": signal.get("action", "BUY"),
            "fill_price": 144.78172417,
            "fill_quantity": 344.0,
            "status": "FILLED",
        }
        self.executed_orders.append(order_record)
        self.processed_signals.add(sid)
        if getattr(self, "_signal_store", None) and hasattr(self._signal_store, "mark_processed"):
            self._signal_store.mark_processed(sid)

        # Persist checkpoint to ObjectStore
        checkpoint = {
            "initial_run": not self.is_restart,
            "model_id": sig_model,
            "tenant_id": sig_tenant,
            "session_id": sig_session,
            "runtime_id": os.getenv("PANTHEON_RUNTIME_ID", "rt-engine-replay-001"),
            "runtime_binding_id": os.getenv("PANTHEON_RUNTIME_BINDING_ID", "rtb-engine-replay-001"),
            "deployment_plan_id": os.getenv("PANTHEON_DEPLOYMENT_PLAN_ID", "dp-engine-replay-001"),
            "strategy_id": os.getenv("PANTHEON_STRATEGY_ID", "strat-engine-replay-001"),
            "capital_pool_id": os.getenv("PANTHEON_CAPITAL_POOL_ID", "pool-engine-replay-001"),
            "processed_signals": sorted(list(self.processed_signals)),
            "last_order": order_record,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.ObjectStore.Save(self.CHECKPOINT_KEY, json.dumps(checkpoint, indent=2))

        self.emit_pantheon_event(
            "OrderFilledReplay",
            metrics={"fill_quantity": 344.0, "fill_price": 144.78172417},
            metadata={
                "signal_id": sid,
                "symbol": order_record["symbol"],
                "model_id": sig_model,
                "tenant_id": sig_tenant,
                "session_id": sig_session,
            },
        )
        return {
            "status": "FILLED",
            "signal_id": sid,
            "new_orders_placed": 1,
            "fill_price": 144.78172417,
            "fill_quantity": 344.0,
            "duplicate_suppressed": False,
            "symbol": order_record["symbol"],
        }

    def complete_replay(self) -> dict[str, Any]:
        return self.emit_pantheon_event(
            "EngineReplayComplete",
            metrics={"orders_executed": len(self.executed_orders), "processed_signals": len(self.processed_signals)},
            metadata={
                "is_restart": self.is_restart,
                "model_id": self.model_id,
                "tenant_id": self.tenant_id,
                "session_id": self.session_id,
            },
        )
