"""Truthful paper execution runtime package for the VM-2 execution plane."""

from __future__ import annotations

import json
import logging
import os
import threading
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_RUNTIME_MANAGER_DIR = Path(__file__).resolve().parents[2] / "runtime-manager"
if str(_RUNTIME_MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_MANAGER_DIR))

from runtime_manager_client import RuntimeManagerClient
from services.execution.lean_runtime.pending_signal_store import (
    BINDING_QUEUE_KEY_PREFIX,
    PendingSignalStore,
    binding_queue_key,
    build_pending_signal_store,
)
from services.execution.lean_runtime.runtime_context import PantheonRuntimeContext
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.lean_runtime.signal_consumer import SignalConsumer

log = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_context_identity_env(
    context: PantheonRuntimeContext,
    base_env: Mapping[str, str],
) -> dict[str, str]:
    env = dict(base_env)
    updates = {
        "PANTHEON_RUNTIME_BINDING_ID": context.runtime_binding_id,
        "PANTHEON_RUNTIME_ID": context.runtime_id,
        "PANTHEON_DEPLOYMENT_PLAN_ID": context.deployment_plan_id,
        "PANTHEON_DEPLOYMENT_STAGE": context.deployment_stage,
        "PANTHEON_RUNTIME_MODE": context.deployment_stage,
        "PANTHEON_RUNTIME_ROLE": context.runtime_role,
        "PANTHEON_ARTIFACT_ID": context.artifact.artifact_id,
        "PANTHEON_ARTIFACT_VERSION": context.artifact.artifact_version,
        "PANTHEON_ARTIFACT_CHECKSUM": context.artifact.artifact_checksum,
        "PANTHEON_STRATEGY_ID": context.artifact.strategy_id,
        "PANTHEON_CAPITAL_POOL_ID": context.capital.capital_pool_id,
        "PANTHEON_PERSONA_CAPITAL_BINDING_ID": context.capital.persona_capital_binding_id,
        "PANTHEON_ENGINE_BRIDGE_REMOTE": context.bridge.repo,
        "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": context.bridge.path,
        "PANTHEON_ENGINE_BRIDGE_COMMIT": context.bridge.commit,
        "PANTHEON_RUNTIME_ADAPTER_VERSION": context.bridge.runtime_adapter_version,
        "PANTHEON_TRACE_ID": context.trace.trace_id,
        "PANTHEON_REQUEST_ID": context.trace.correlation_id,
    }
    env.update({key: value for key, value in updates.items() if value})
    return env


def _binding_from_runtime_context(context: PantheonRuntimeContext) -> dict[str, Any]:
    return {
        "binding_id": context.runtime_binding_id,
        "runtime_binding_id": context.runtime_binding_id,
        "runtime_id": context.runtime_id,
        "status": "context_loaded",
        "deployment_mode": context.deployment_stage,
        "deployment_stage": context.deployment_stage,
        "capital_pool_id": context.capital.capital_pool_id,
        "plan_id": context.deployment_plan_id,
        "deployment_plan_id": context.deployment_plan_id,
        "artifact_id": context.artifact.artifact_id,
        "artifact_version": context.artifact.artifact_version,
        "persona_capital_binding_id": context.capital.persona_capital_binding_id,
        "engine_bridge_repo": context.bridge.repo,
        "engine_bridge_path": context.bridge.path,
        "engine_bridge_commit": context.bridge.commit,
        "runtime_adapter_version": context.bridge.runtime_adapter_version,
        "context_source": context.context_source.value,
    }


def _runtime_context_snapshot(context: PantheonRuntimeContext | None) -> dict[str, Any]:
    if context is None:
        return {
            "loaded": False,
            "context_source": "unavailable",
            "runtime_binding_id": None,
            "deployment_stage": None,
        }
    return {
        "loaded": True,
        "context_source": context.context_source.value,
        "runtime_binding_id": context.runtime_binding_id,
        "runtime_id": context.runtime_id,
        "deployment_plan_id": context.deployment_plan_id,
        "deployment_stage": context.deployment_stage,
        "runtime_role": context.runtime_role,
        "artifact_id": context.artifact.artifact_id,
        "artifact_version": context.artifact.artifact_version,
        "capital_pool_id": context.capital.capital_pool_id,
        "persona_capital_binding_id": context.capital.persona_capital_binding_id,
        "bridge_repo": context.bridge.repo,
        "bridge_path": context.bridge.path,
        "bridge_commit": context.bridge.commit,
        "runtime_adapter_version": context.bridge.runtime_adapter_version,
        "trace_id": context.trace.trace_id,
        "correlation_id": context.trace.correlation_id,
    }


class _Holding:
    def __init__(self, quantity: float = 0.0) -> None:
        self.Quantity = quantity


class _Security:
    def __init__(self, price: float = 100.0) -> None:
        self.Price = price


@dataclass
class OrderEvent:
    event_type: str
    symbol: str
    quantity: float
    fill_price: float
    action: str
    submitted_to_broker: bool = False
    broker_submission_status: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_type": self.event_type,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "fill_price": self.fill_price,
            "action": self.action,
            "submitted_to_broker": self.submitted_to_broker,
            "created_at": self.created_at,
        }
        if self.broker_submission_status:
            payload["broker_submission_status"] = self.broker_submission_status
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


class PaperExecutionAlgorithm:
    """Small LEAN-like surface used by the executor for paper fills."""

    def __init__(
        self,
        *,
        initial_cash: float = 100_000.0,
        default_price: float = 100.0,
        event_sink: Callable[[OrderEvent], None] | None = None,
        deployment_stage: str = "paper",
        bracket_order_execution_enabled: bool = True,
    ) -> None:
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._default_price = default_price
        self._event_sink = event_sink
        self.DeploymentStage = str(deployment_stage or "paper").strip().lower()
        self.BracketOrderExecutionEnabled = bool(bracket_order_execution_enabled)
        self.Portfolio: dict[str, _Holding] = {}
        self.Securities: dict[str, _Security] = {}
        self._open_bracket_orders: list[dict[str, Any]] = []
        self._current_signal_metadata: dict[str, Any] = {}

    def _holding(self, symbol: str) -> _Holding:
        return self.Portfolio.setdefault(symbol, _Holding())

    def _security(self, symbol: str) -> _Security:
        return self.Securities.setdefault(symbol, _Security(price=self._default_price))

    def EnsureSecurity(self, symbol: str) -> _Security:  # noqa: N802
        """Expose deterministic paper pricing for executor price lookups."""
        return self._security(str(symbol))

    def SetSecurityPrice(self, symbol: str, price: float) -> None:  # noqa: N802
        security = self._security(str(symbol))
        security.Price = float(price)

    def SetCurrentSignalContext(self, metadata: dict[str, Any] | None) -> None:  # noqa: N802
        self._current_signal_metadata = dict(metadata or {})

    def ClearCurrentSignalContext(self) -> None:  # noqa: N802
        self._current_signal_metadata = {}

    def _publish(
        self,
        event_type: str,
        symbol: str,
        quantity: float,
        action: str,
        *,
        broker_submission_status: str | None = None,
        submitted_to_broker: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        security = self._security(symbol)
        event_metadata = dict(self._current_signal_metadata)
        if metadata:
            event_metadata.update(metadata)
        self._event_sink(
            OrderEvent(
                event_type=event_type,
                symbol=str(symbol),
                quantity=float(quantity),
                fill_price=float(security.Price),
                action=action,
                submitted_to_broker=submitted_to_broker,
                broker_submission_status=broker_submission_status,
                metadata=event_metadata,
            )
        )

    def SetHoldings(self, symbol: str, target_percent: float) -> None:  # noqa: N802
        security = self._security(symbol)
        target_quantity = (self._initial_cash * float(target_percent)) / max(float(security.Price), 0.01)
        holding = self._holding(symbol)
        current_quantity = holding.Quantity
        delta = target_quantity - current_quantity
        if abs(delta) <= 1e-12:
            metadata: dict[str, Any] = {
                "noop_reason": "set_holdings_no_delta",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "computed_quantity": 0.0,
                "position_quantity": float(current_quantity),
                "target_quantity": float(target_quantity),
                "target_percent": float(target_percent),
                "price": float(security.Price),
            }
            for field in ("signal_id", "requested_quantity", "quantity_type", "order_type"):
                value = self._current_signal_metadata.get(field)
                if value not in (None, "", [], {}):
                    metadata[field] = value
            self._publish(
                "paper_order_simulated",
                symbol,
                0.0,
                "set_holdings_no_delta_noop",
                broker_submission_status="not_submitted_signal_noop",
                submitted_to_broker=False,
                metadata=metadata,
            )
            return
        holding.Quantity = target_quantity
        self._cash -= delta * float(security.Price)
        self._publish("paper_fill_simulated", symbol, delta, "set_holdings")

    def MarketOrder(self, symbol: str, quantity: float) -> None:  # noqa: N802
        security = self._security(symbol)
        self._holding(symbol).Quantity += float(quantity)
        self._cash -= float(quantity) * float(security.Price)
        self._publish("paper_fill_simulated", symbol, quantity, "market_order")

    def LimitOrder(self, symbol: str, quantity: float, limit_price: float) -> None:  # noqa: N802
        security = self._security(symbol)
        security.Price = float(limit_price)
        self._holding(symbol).Quantity += float(quantity)
        self._cash -= float(quantity) * float(security.Price)
        self._publish("paper_fill_simulated", symbol, quantity, "limit_order")

    def Liquidate(self, symbol: str) -> None:  # noqa: N802
        security = self._security(symbol)
        quantity = self._holding(symbol).Quantity
        if quantity == 0:
            metadata: dict[str, Any] = {
                "noop_reason": "liquidate_without_position",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "computed_quantity": 0.0,
                "position_quantity": 0.0,
                "price": float(security.Price),
            }
            for field in ("signal_id", "requested_quantity", "quantity_type", "order_type"):
                value = self._current_signal_metadata.get(field)
                if value not in (None, "", [], {}):
                    metadata[field] = value
            self._publish(
                "paper_order_simulated",
                symbol,
                0.0,
                "liquidate_without_position_noop",
                broker_submission_status="not_submitted_signal_noop",
                submitted_to_broker=False,
                metadata=metadata,
            )
            return
        self._holding(symbol).Quantity = 0.0
        self._cash += quantity * float(security.Price)
        self._publish("paper_fill_simulated", symbol, -quantity, "liquidate")

    def SubmitBracketOrder(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        legs: list[dict[str, Any]],
        guard_stage: str,
        broker_submission_status: str,
        submitted_to_broker: bool,
    ) -> dict[str, Any]:
        bracket_order_id = uuid.uuid4().hex
        stored_legs: list[dict[str, Any]] = []
        for index, leg in enumerate(legs, start=1):
            stored_legs.append(
                {
                    "bracket_order_id": bracket_order_id,
                    "leg_id": f"{bracket_order_id}-{index}",
                    "symbol": str(symbol),
                    "signal_id": signal_id,
                    "deployment_stage": guard_stage,
                    "submitted_to_broker": bool(submitted_to_broker),
                    "broker_submission_status": broker_submission_status,
                    "status": "open",
                    "created_at": _iso_now(),
                    **dict(leg),
                }
            )
        self._open_bracket_orders.extend(stored_legs)
        return {
            "bracket_order_id": bracket_order_id,
            "leg_count": len(stored_legs),
            "legs": stored_legs,
        }

    def RecordBracketOrderLogged(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        stop_loss_pct: float,
        take_profit_pct: float,
        broker_submission_status: str,
        submitted_to_broker: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "signal_id": signal_id,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
        }
        if metadata:
            event_metadata.update(metadata)
        self._publish(
            "bracket_order_logged",
            str(symbol),
            0.0,
            "bracket_submitted_to_broker" if submitted_to_broker else "bracket_logged_only",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=event_metadata,
        )

    def RecordOrderRejected(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        reject_reason: str,
        requested_quantity: float,
        computed_quantity: float,
        quantity_type: str,
        order_type: str,
        broker_submission_status: str,
        submitted_to_broker: bool,
        price: float | None = None,
    ) -> None:
        event_metadata = {
            "signal_id": signal_id,
            "reject_reason": reject_reason,
            "rejection_status": "rejected",
            "order_status": "rejected",
            "requested_quantity": float(requested_quantity),
            "computed_quantity": float(computed_quantity),
            "quantity_type": quantity_type,
            "order_type": order_type,
        }
        if price is not None:
            event_metadata["price"] = float(price)
        self._publish(
            "order_rejection",
            str(symbol),
            0.0,
            "order_rejected",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=event_metadata,
        )

    def RecordSignalNoop(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        noop_reason: str,
        requested_quantity: float,
        quantity_type: str,
        order_type: str,
        broker_submission_status: str,
        submitted_to_broker: bool,
        computed_quantity: float | None = None,
        price: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "signal_id": signal_id,
            "noop_reason": noop_reason,
            "decision_status": "no_order",
            "order_status": "not_submitted",
            "requested_quantity": float(requested_quantity),
            "quantity_type": quantity_type,
            "order_type": order_type,
        }
        if computed_quantity is not None:
            event_metadata["computed_quantity"] = float(computed_quantity)
        if price is not None:
            event_metadata["price"] = float(price)
        if metadata:
            event_metadata.update(metadata)
        self._publish(
            "paper_order_simulated",
            str(symbol),
            0.0,
            f"{noop_reason}_noop",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=event_metadata,
        )

    def positions(self) -> list[dict[str, Any]]:
        positions: list[dict[str, Any]] = []
        for symbol, holding in sorted(self.Portfolio.items()):
            if holding.Quantity == 0:
                continue
            positions.append(
                {
                    "symbol": symbol,
                    "quantity": holding.Quantity,
                    "price": self._security(symbol).Price,
                }
            )
        return positions

    def open_bracket_orders(self) -> list[dict[str, Any]]:
        return [dict(order) for order in self._open_bracket_orders]

    def pnl(self) -> float:
        portfolio_value = self._cash
        for symbol, holding in self.Portfolio.items():
            portfolio_value += holding.Quantity * self._security(symbol).Price
        return float(portfolio_value - self._initial_cash)


class RuntimeBindingResolver:
    """Resolve the current binding context for this runtime id."""

    def __init__(
        self,
        client: RuntimeManagerClient,
        runtime_id: str | None,
        runtime_context: PantheonRuntimeContext | None = None,
    ) -> None:
        self._client = client
        self._runtime_id = runtime_id
        self._context_binding = (
            _binding_from_runtime_context(runtime_context) if runtime_context is not None else None
        )
        self._cached_binding: dict[str, Any] | None = (
            dict(self._context_binding) if self._context_binding is not None else None
        )
        self._binding_source: str | None = "runtime_context" if self._cached_binding else None
        self._last_sync_at: str | None = None
        self._last_error: str | None = None

    def resolve(self) -> dict[str, Any] | None:
        if not self._runtime_id:
            return None
        try:
            bindings = self._client.list_all()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("runtime-manager binding lookup failed: %s", exc)
            return self._cached_binding

        statuses = {"active": 0, "paused": 1, "pending_pause": 2, "failed": 3, "retired": 4}
        matches = [
            binding
            for binding in bindings
            if binding.get("runtime_id") == self._runtime_id
        ]
        if not matches:
            self._cached_binding = dict(self._context_binding) if self._context_binding else None
            self._binding_source = "runtime_context" if self._context_binding else None
            self._last_sync_at = _iso_now()
            self._last_error = None
            return self._cached_binding

        matches.sort(key=lambda item: statuses.get(str(item.get("status")), 99))
        self._cached_binding = matches[0]
        self._binding_source = "runtime_manager"
        self._last_sync_at = _iso_now()
        self._last_error = None
        return self._cached_binding

    def snapshot(self) -> dict[str, Any]:
        binding = self._cached_binding or {}
        return {
            "resolved": self._cached_binding is not None,
            "binding_id": binding.get("binding_id"),
            "status": binding.get("status"),
            "deployment_mode": binding.get("deployment_mode"),
            "capital_pool_id": binding.get("capital_pool_id"),
            "plan_id": binding.get("plan_id"),
            "artifact_id": binding.get("artifact_id"),
            "artifact_version": binding.get("artifact_version"),
            "persona_capital_binding_id": binding.get("persona_capital_binding_id"),
            "source": self._binding_source,
            "last_sync_at": self._last_sync_at,
            "last_error": self._last_error,
        }


class RuntimeTelemetryEmitter:
    """Emit paper-only canonical telemetry envelopes to the ingest surface."""

    def __init__(
        self,
        identity: RuntimeIdentity,
        binding_resolver: RuntimeBindingResolver,
        runtime_context: PantheonRuntimeContext | None = None,
    ) -> None:
        self._identity = identity
        self._binding_resolver = binding_resolver
        self._runtime_context = runtime_context
        self._url = str(self._identity.telemetry_url or os.getenv("PANTHEON_TELEMETRY_URL", "")).strip().rstrip("/")
        self._timeout = int(os.getenv("PANTHEON_TELEMETRY_TIMEOUT_SECONDS", "5"))
        self._enabled = bool(self._url)
        self._sent = 0
        self._failed = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def build_event(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Build a schema-valid paper TelemetryEvent or return None on invariant failure."""
        binding = self._binding_resolver.resolve()
        if not binding:
            return self._fail_build("binding context unresolved")

        deployment_stage = str(
            binding.get("deployment_stage")
            or binding.get("deployment_mode")
            or self._identity.deployment_stage
            or self._identity.runtime_mode
            or "paper"
        ).strip().lower()
        if deployment_stage != "paper":
            return self._fail_build(
                f"paper runtime telemetry must use deployment_stage='paper', got {deployment_stage!r}"
            )

        binding_id = str(binding.get("binding_id") or binding.get("runtime_binding_id") or "")
        runtime_id = str(binding.get("runtime_id") or self._identity.runtime_id or "")
        capital_pool_id = str(binding.get("capital_pool_id") or "")
        artifact_id = str(binding.get("artifact_id") or "")
        artifact_version = str(binding.get("artifact_version") or "")
        plan_id = str(binding.get("plan_id") or binding.get("deployment_plan_id") or "")
        persona_capital_binding_id = str(binding.get("persona_capital_binding_id") or "")
        required = {
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": capital_pool_id,
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "plan_id": plan_id,
            "persona_capital_binding_id": persona_capital_binding_id,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            return self._fail_build(f"binding context missing required fields: {missing}")

        strategy_id = str(
            os.getenv("PANTHEON_STRATEGY_ID")
            or (self._runtime_context.artifact.strategy_id if self._runtime_context else "")
            or artifact_id
            or "paper-runtime"
        )
        artifact_type = str(os.getenv("PANTHEON_ARTIFACT_TYPE", "execution_bundle"))
        event_metadata = self._base_metadata(binding)
        event_metadata.update(metadata or {})
        payload = {
            "event_id": event_id or str(uuid.uuid4()),
            "event_type": event_type,
            "created_at": created_at or _iso_now(),
            "execution_mode": "paper",
            "environment": deployment_stage,
            "deployment_stage": deployment_stage,
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": capital_pool_id,
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "plan_id": plan_id,
            "persona_capital_binding_id": persona_capital_binding_id,
            "authority_refs": self._identity.authority_refs(),
            "target": {
                "registry_id": artifact_id,
                "strategy_id": strategy_id,
                "artifact_version": artifact_version,
                "artifact_type": artifact_type,
                "promotion_state": "paper",
            },
            "metrics": metrics,
            "metadata": event_metadata,
        }
        lineage_ref = os.getenv("PANTHEON_LINEAGE_REF", "").strip()
        if lineage_ref:
            payload["target"]["lineage_ref"] = lineage_ref
        if self._identity.trace_id:
            payload["trace_id"] = self._identity.trace_id
        return payload

    def emit(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self._enabled:
            return False

        payload = self.build_event(event_type, metrics, metadata)
        if payload is None:
            return False

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._url}/api/telemetry/ingest",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status_code = getattr(response, "status", 200)
                if status_code not in {200, 202}:
                    raise RuntimeError(f"telemetry ingest returned HTTP {status_code}")
        except urllib.error.HTTPError as exc:
            self._failed += 1
            self._last_error = f"HTTPError {exc.code}: {exc.reason}"
            return False
        except Exception as exc:  # noqa: BLE001
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False

        self._sent += 1
        self._last_error = None
        return True

    def emit_deploy_started(self) -> bool:
        return self.emit(
            "deploy_started",
            {"action": "deploy_started"},
            metadata={"runtime_package": "paper_execution_runtime"},
        )

    def emit_deploy_completed(self) -> bool:
        return self.emit(
            "deploy_completed",
            {"action": "deploy_completed"},
            metadata={"runtime_package": "paper_execution_runtime"},
        )

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata=metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics: dict[str, Any] = {"pnl": float(pnl)}
        if extra_metrics:
            metrics.update(extra_metrics)
        return self.emit("pnl_snapshot", metrics, metadata=metadata)

    def _base_metadata(self, binding: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "runtime_role": self._identity.runtime_role,
        }
        if self._runtime_context is not None:
            metadata.update(
                {
                    "engine_bridge_repo": self._runtime_context.bridge.repo,
                    "engine_bridge_path": self._runtime_context.bridge.path,
                    "engine_bridge_commit": self._runtime_context.bridge.commit,
                    "runtime_adapter_version": self._runtime_context.bridge.runtime_adapter_version,
                    "context_source": self._runtime_context.context_source.value,
                }
            )
        else:
            candidates = {
                "engine_bridge_repo": binding.get("engine_bridge_repo")
                or os.getenv("PANTHEON_ENGINE_BRIDGE_REMOTE")
                or os.getenv("PANTHEON_ENGINE_BRIDGE_REPO"),
                "engine_bridge_path": binding.get("engine_bridge_path")
                or os.getenv("PANTHEON_ENGINE_BRIDGE_SOURCE_PATH")
                or os.getenv("PANTHEON_ENGINE_BRIDGE_PATH"),
                "engine_bridge_commit": binding.get("engine_bridge_commit")
                or os.getenv("PANTHEON_ENGINE_BRIDGE_COMMIT"),
                "runtime_adapter_version": binding.get("runtime_adapter_version")
                or os.getenv("PANTHEON_RUNTIME_ADAPTER_VERSION"),
                "context_source": binding.get("context_source")
                or os.getenv("PANTHEON_CONTEXT_SOURCE")
                or "env_vars",
            }
            metadata.update({key: str(value) for key, value in candidates.items() if value})
        return metadata

    def _fail_build(self, message: str) -> None:
        self._failed += 1
        self._last_error = message
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "url": self._url or None,
            "sent": self._sent,
            "failed": self._failed,
            "last_error": self._last_error,
        }


class PaperRuntimeService:
    """Background execution loop plus HTTP-readable runtime state."""

    def __init__(
        self,
        *,
        store: PendingSignalStore | None = None,
        identity: RuntimeIdentity | None = None,
        runtime_manager_client: RuntimeManagerClient | None = None,
        telemetry_emitter: RuntimeTelemetryEmitter | None = None,
        runtime_context: PantheonRuntimeContext | None = None,
        poll_interval_seconds: float | None = None,
        max_batch_size: int | None = None,
    ) -> None:
        self._runtime_context = runtime_context
        identity_env = (
            _runtime_context_identity_env(runtime_context, os.environ)
            if runtime_context is not None
            else None
        )
        self._identity = identity or RuntimeIdentity.from_env(identity_env)
        # Resolve queue key: explicit env > binding-scoped > default.
        # The reconciler sets PANTHEON_SIGNAL_QUEUE_KEY for each worker subprocess;
        # direct invocations fall back to PANTHEON_RUNTIME_BINDING_ID if available.
        _explicit_key = os.getenv("PANTHEON_SIGNAL_QUEUE_KEY", "").strip()
        _binding_for_key = (self._identity.binding_id or "").strip()
        if _explicit_key:
            _resolved_queue_key = _explicit_key
        elif _binding_for_key:
            _resolved_queue_key = binding_queue_key(_binding_for_key)
        else:
            _resolved_queue_key = BINDING_QUEUE_KEY_PREFIX
        self._store = store or build_pending_signal_store(
            os.getenv("SIGNAL_STORE_URL", "redis://signal-store:6379"),
            queue_key=_resolved_queue_key,
            default_batch_size=int(os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")),
        )
        self._runtime_manager_client = runtime_manager_client or RuntimeManagerClient(
            base_url=self._identity.runtime_manager_url,
            bearer_token=self._identity.runtime_manager_auth.token,
        )
        self._binding_resolver = RuntimeBindingResolver(
            self._runtime_manager_client,
            self._identity.runtime_id,
            runtime_context=runtime_context,
        )
        self._telemetry = telemetry_emitter or RuntimeTelemetryEmitter(
            self._identity,
            self._binding_resolver,
            runtime_context=runtime_context,
        )
        self._algo = PaperExecutionAlgorithm(
            event_sink=self._handle_order_event,
            deployment_stage=self._identity.deployment_stage or self._identity.runtime_mode or "paper",
            bracket_order_execution_enabled=_as_bool(
                os.getenv("PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED"),
                default=True,
            ),
        )
        self._consumer = SignalConsumer(
            store_client=self._store,
            binding_id=self._identity.binding_id or None,
        )
        self._poll_interval_seconds = poll_interval_seconds or _as_float(
            os.getenv("PANTHEON_RUNTIME_POLL_INTERVAL_SECONDS"),
            1.0,
        )
        self._max_batch_size = max(int(max_batch_size or os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")), 1)
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = _iso_now()
        self._last_poll_at: str | None = None
        self._last_drain_at: str | None = None
        self._last_error: str | None = None
        self._last_heartbeat_at: str | None = None
        self._poll_count = 0
        self._processed_signal_count = 0
        self._execution_event_count = 0
        self._fill_event_count = 0
        self._recent_order_events: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._emit_deploy_started()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="paper-runtime-loop")
        self._thread.start()
        self._emit_deploy_completed()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def drain_once(self) -> dict[str, Any]:
        with self._lock:
            self._last_poll_at = _iso_now()
            before = len(self._consumer._processed_signal_ids)
            binding = self._binding_resolver.resolve()
            try:
                if not binding:
                    raise RuntimeError(
                        "RuntimeBinding is required before paper execution can drain signals"
                    )
                self._consumer.drain(algo=self._algo)
                self._last_drain_at = _iso_now()
                self._last_error = None
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("paper runtime drain failed")
            after = len(self._consumer._processed_signal_ids)
            self._processed_signal_count += max(after - before, 0)
            self._poll_count += 1
            if self._last_error is None:
                self._maybe_emit_heartbeat()
                self._maybe_emit_pnl_snapshot()
            return self.snapshot()

    def pool_access_violation(self, requested_pool_id: str | None) -> dict[str, Any] | None:
        requested = str(requested_pool_id or "").strip()
        if not requested:
            return None
        current = self._current_capital_pool_id()
        if current and requested == current:
            return None
        return {
            "status": "blocked",
            "error": "capital_pool_scope_mismatch",
            "requested_capital_pool_id": requested,
            "runtime_capital_pool_id": current,
            "message": "Runtime state, credentials, positions, and PnL are scoped to the active RuntimeBinding capital pool.",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._identity.to_health_payload(),
                "status": "ok" if self._last_error is None else "degraded",
                "runtime_package": "paper_execution_runtime",
                "runtime_package_version": "ep4",
                "paper_execution_ready": True,
                "signal_consumer_ready": True,
                "signal_store": {
                    "kind": getattr(self._store, "kind", type(self._store).__name__),
                    "queue_depth": self._safe_queue_depth(),
                    "batch_size": self._max_batch_size,
                },
                "runtime_context": _runtime_context_snapshot(self._runtime_context),
                "binding_lookup": self._binding_resolver.snapshot(),
                "telemetry": self._telemetry.snapshot(),
                "paper_state": {
                    "started_at": self._started_at,
                    "last_poll_at": self._last_poll_at,
                    "last_drain_at": self._last_drain_at,
                    "last_heartbeat_at": self._last_heartbeat_at,
                    "poll_count": self._poll_count,
                    "processed_signal_count": self._processed_signal_count,
                    "execution_event_count": self._execution_event_count,
                    "bracket_order_execution_enabled": bool(
                        getattr(self._algo, "BracketOrderExecutionEnabled", False)
                    ),
                    "bracket_order_execution_stage": getattr(self._algo, "DeploymentStage", "unknown"),
                    "positions": self._algo.positions(),
                    "open_bracket_orders": self._algo.open_bracket_orders(),
                    "recent_order_events": list(self._recent_order_events),
                    "last_error": self._last_error,
                },
                "stub_mode": False,
            }

    def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            self.drain_once()
            self._shutdown.wait(self._poll_interval_seconds)

    def _handle_order_event(self, event: OrderEvent) -> None:
        event_payload = event.to_dict()
        self._execution_event_count += 1
        if event.event_type == "paper_fill_simulated":
            self._fill_event_count += 1
        self._recent_order_events.append(event_payload)
        self._recent_order_events = self._recent_order_events[-20:]
        telemetry_metadata = {
            "runtime_package": "paper_execution_runtime",
            "symbol": event.symbol,
            "sim_fill_flag": event.event_type == "paper_fill_simulated",
            "is_real_order": False,
            "is_real_capital": False,
            "submitted_to_broker": event.submitted_to_broker,
            "capital_scale_pct": 0,
        }
        if event.broker_submission_status:
            telemetry_metadata["broker_submission_status"] = event.broker_submission_status
        telemetry_metadata.update(event.metadata)
        if event.event_type == "bracket_order_logged":
            metrics: dict[str, Any] = {
                "action": (
                    "bracket_submitted_to_broker"
                    if event.submitted_to_broker
                    else "bracket_logged_only"
                ),
                "submitted_to_broker": event.submitted_to_broker,
            }
        elif event.event_type == "order_rejection":
            metrics = {
                "rejected_order_count": 1,
                "fill_quantity": 0.0,
                "fill_rate": 0.0,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            }
            for field in ("requested_quantity", "computed_quantity"):
                if field in event.metadata:
                    metrics[field] = event.metadata[field]
        elif event.event_type == "paper_order_simulated":
            metrics = {
                "noop_count": 1,
                "fill_quantity": 0.0,
                "fill_rate": 0.0,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            }
            for field in ("requested_quantity", "computed_quantity"):
                if field in event.metadata:
                    metrics[field] = event.metadata[field]
        else:
            metrics = {
                "fill_quantity": event.quantity,
                "fill_price": event.fill_price,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            }
        self._telemetry.emit(event.event_type, metrics, metadata=telemetry_metadata)

    def _maybe_emit_heartbeat(self) -> None:
        if not self._telemetry.enabled:
            return
        now = _iso_now()
        if self._last_heartbeat_at == now:
            return
        emitted = self._telemetry.emit_heartbeat(
            metadata={
                "runtime_package": "paper_execution_runtime",
                "queue_depth": self._safe_queue_depth(),
                "is_real_order": False,
                "is_real_capital": False,
                "sim_fill_flag": False,
                "capital_scale_pct": 0,
            },
        )
        if emitted:
            self._last_heartbeat_at = now

    def _maybe_emit_pnl_snapshot(self) -> None:
        if not self._telemetry.enabled:
            return
        self._telemetry.emit_pnl_snapshot(
            self._algo.pnl(),
            metadata={
                "runtime_package": "paper_execution_runtime",
                "queue_depth": self._safe_queue_depth(),
                "is_real_order": False,
                "is_real_capital": False,
                "capital_scale_pct": 0,
            },
            extra_metrics=self._performance_snapshot_metrics(),
        )

    def _performance_snapshot_metrics(self) -> dict[str, Any]:
        processed = int(self._processed_signal_count)
        fill_rate = (float(self._fill_event_count) / processed) if processed > 0 else 0.0
        return {
            "processed_signal_count": processed,
            "execution_event_count": int(self._execution_event_count),
            "fill_event_count": int(self._fill_event_count),
            "fill_rate": round(fill_rate, 6),
            "open_position_count": len(self._algo.positions()),
            "open_bracket_order_count": len(self._algo.open_bracket_orders()),
            "avg_slippage_bps": 0.0,
        }

    def _emit_deploy_started(self) -> None:
        if self._telemetry.enabled:
            self._telemetry.emit_deploy_started()

    def _emit_deploy_completed(self) -> None:
        if self._telemetry.enabled:
            self._telemetry.emit_deploy_completed()

    def _safe_queue_depth(self) -> int | None:
        try:
            return int(self._store.queue_depth())
        except Exception:  # noqa: BLE001
            return None

    def _current_capital_pool_id(self) -> str | None:
        binding_snapshot = self._binding_resolver.snapshot()
        if not binding_snapshot.get("capital_pool_id"):
            try:
                self._binding_resolver.resolve()
                binding_snapshot = self._binding_resolver.snapshot()
            except Exception:  # noqa: BLE001
                pass
        for value in (
            binding_snapshot.get("capital_pool_id"),
            self._identity.capital_pool_id,
            self._runtime_context.capital.capital_pool_id if self._runtime_context else None,
        ):
            text = str(value or "").strip()
            if text:
                return text
        return None


_SERVICE: PaperRuntimeService | None = None


def get_service(runtime_context: PantheonRuntimeContext | None = None) -> PaperRuntimeService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PaperRuntimeService(runtime_context=runtime_context)
    return _SERVICE


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, status_code: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _runtime_health_response(self) -> tuple[int, dict[str, Any]]:
        snapshot = get_service().snapshot()
        ready = snapshot.get("status") == "ok"
        parsed_path = urlparse(self.path).path
        body = {
            **snapshot,
            "live": True,
            "ready": ready,
            "health_contract": {
                "healthz": "/healthz",
                "livez": "/livez",
                "readyz": "/readyz",
                "legacy": ["/health", "/__health__"],
            },
        }
        if parsed_path in {"/healthz", "/livez"}:
            return 200, body
        return (200 if ready else 503), body

    def _requested_pool_id(self) -> str | None:
        query = parse_qs(urlparse(self.path).query)
        for key in ("capital_pool_id", "pool_id"):
            values = query.get(key) or []
            if values and str(values[0]).strip():
                return str(values[0]).strip()
        return None

    def _pool_guard(self, service: PaperRuntimeService) -> dict[str, Any] | None:
        return service.pool_access_violation(self._requested_pool_id())

    def do_GET(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path in {"/", "/__health__", "/health", "/healthz", "/livez", "/readyz"}:
            status_code, body = self._runtime_health_response()
            self._write_json(status_code, body)
            return
        if parsed_path == "/api/runtime/state":
            service = get_service()
            violation = self._pool_guard(service)
            if violation:
                self._write_json(403, violation)
                return
            self._write_json(200, service.snapshot())
            return
        if parsed_path == "/api/runtime/orders":
            service = get_service()
            violation = self._pool_guard(service)
            if violation:
                self._write_json(403, violation)
                return
            snapshot = service.snapshot()
            self._write_json(200, {"orders": snapshot["paper_state"]["recent_order_events"]})
            return
        self._write_json(404, {"status": "not_found", "path": parsed_path})

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path == "/api/runtime/drain":
            service = get_service()
            violation = self._pool_guard(service)
            if violation:
                self._write_json(403, violation)
                return
            self._write_json(200, service.drain_once())
            return
        self._write_json(404, {"status": "not_found", "path": parsed_path})


def main(runtime_context: PantheonRuntimeContext | None = None) -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    service = get_service(runtime_context=runtime_context)
    service.start()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8010"))
    server = ThreadingHTTPServer((host, port), _Handler)
    print(
        json.dumps(
            {
                "message": "paper execution runtime ready",
                "role": service.snapshot().get("runtime_role"),
                "runtime_package": "paper_execution_runtime",
                "port": port,
                "stub_mode": False,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
