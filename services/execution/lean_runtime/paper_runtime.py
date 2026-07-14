"""Truthful paper execution runtime package for the VM-2 execution plane."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

try:  # Taiwan symbol normalization lives at the shioaji adapter boundary
    from ..shioaji_adapter import normalize_taiwan_symbol
except Exception:  # pragma: no cover - fallback if package path differs at runtime
    def normalize_taiwan_symbol(symbol, venue=None):  # type: ignore[misc]
        s = str(symbol).strip().upper()
        ticker = s.rsplit(".", 1)[0] if "." in s else s
        suffix = s.rsplit(".", 1)[1] if "." in s else "TW"
        return ticker, {"TPEX": "OTC", "TWO": "OTC"}.get(suffix, "TSE")
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
from services.execution.lean_runtime.performance_telemetry import (
    MarketMark,
    RollingDrawdownTracker,
    SourceIngestMarkProvider,
    value_portfolio,
)
from services.execution.lean_runtime.runtime_context import PantheonRuntimeContext
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.trade_journey.correlation_envelope import propagate_envelope

log = logging.getLogger(__name__)


_HALT_BINDING_STATUSES = frozenset({"paused", "pending_pause", "failed", "retired"})
"""Binding statuses at which the paper runtime must NOT execute signals.

A binding moved to paused / pending_pause / failed / retired (e.g. by an operator
pause or the kill-switch / safe-mode path) must halt order execution as
defense-in-depth — otherwise the execution loop keeps filling orders for a
halted binding. Signals are left on the queue so they can replay on resume.
"""


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
        self.MarkAsOf: str | None = None
        self.MarkSource: str | None = None
        self.MarkAuthoritative = False


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
        state_path: str | None = None,
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
        self._fill_count = 0
        self._ledger_started_at = _iso_now()
        self._first_fill_at: str | None = None
        self._last_fill_at: str | None = None
        self._performance_window_state: dict[str, Any] = {}
        self._performance_binding_id: str | None = None
        self._state_binding_error: str | None = None
        self._loaded_state_missing_binding_id = False
        self._state_path = Path(state_path) if state_path else None
        self._state_error: str | None = None
        self._state_load_error: str | None = None
        self._load_state()

    def _holding(self, symbol: str) -> _Holding:
        return self.Portfolio.setdefault(symbol, _Holding())

    def _security(self, symbol: str) -> _Security:
        return self.Securities.setdefault(symbol, _Security(price=self._default_price))

    def EnsureSecurity(self, symbol: str) -> _Security:  # noqa: N802
        """Expose deterministic paper pricing for executor price lookups."""
        return self._security(str(symbol))

    def SetSecurityPrice(  # noqa: N802
        self,
        symbol: str,
        price: float,
        *,
        as_of: str | None = None,
        source: str = "runtime_price",
        authoritative: bool = False,
    ) -> None:
        security = self._security(str(symbol))
        security.Price = float(price)
        security.MarkAsOf = str(as_of) if as_of else None
        security.MarkSource = str(source) if source else None
        security.MarkAuthoritative = bool(authoritative and as_of and source)

    def SetSecurityMark(  # noqa: N802
        self,
        symbol: str,
        price: float,
        *,
        as_of: str | None,
        source: str,
    ) -> None:
        self.SetSecurityPrice(
            symbol,
            price,
            as_of=as_of,
            source=source,
            authoritative=True,
        )

    def SetCurrentSignalContext(self, metadata: dict[str, Any] | None) -> None:  # noqa: N802
        self._current_signal_metadata = dict(metadata or {})

    def ClearCurrentSignalContext(self) -> None:  # noqa: N802
        self._current_signal_metadata = {}

    def SubmitTaiwanBrokerOrder(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        side: str,
        quantity: float,
        quantity_type: str,
        action: str,
        order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> None:
        """Place a Taiwan paper order via the broker sidecar and record the fill.

        Taiwan venues are excluded from LEAN Symbol.Create(); execution is
        delegated here to the paper broker (Shioaji sandbox boundary). The fill
        is published on the same telemetry path as LEAN fills, tagged with the
        broker order id under shioaji_trade_id.
        """
        native, exchange = normalize_taiwan_symbol(symbol)
        base_metadata = {
            "signal_id": signal_id,
            "adapter": "shioaji",
            "broker": "shioaji_paper",
            "venue": "shioaji_paper",
            "exchange": exchange,
            "contract_symbol": native,
            "sec_type": "equity",
            "currency": "TWD",
            "side": side,
        }
        if quantity_type != "SHARES":
            self._publish(
                "order_rejection", str(symbol), 0.0, action,
                broker_submission_status="tw_unsupported_quantity_type",
                submitted_to_broker=False,
                metadata={**base_metadata, "rejected_order_count": 1,
                          "quantity_type": quantity_type},
            )
            log.warning("[%s] TW order rejected: unsupported quantity_type=%s", signal_id, quantity_type)
            return

        qty = abs(float(quantity))
        broker_url = os.getenv("PANTHEON_BROKER_PAPER_URL", "http://broker:8102").rstrip("/")
        payload = {
            "capital_pool_id": os.getenv("PANTHEON_CAPITAL_POOL_ID", "") or self._taiwan_capital_pool_id(),
            "strategy_id": os.getenv("PANTHEON_STRATEGY_ID", "") or "strategy-tw-session-momentum",
            "client_order_id": str(signal_id),
            "symbol": native,
            "qty": qty,
            "side": side,
            "order_type": str(order_type or "MARKET").lower(),
        }
        correlation_envelope = self._current_signal_metadata.get("correlation_envelope")
        if correlation_envelope is not None:
            payload["correlation_envelope"] = correlation_envelope
        if limit_price is not None:
            payload["limit_price"] = float(limit_price)
        try:
            order = self._post_broker_paper_order(broker_url, payload)
        except Exception as exc:
            self._publish(
                "order_rejection", str(symbol), 0.0, action,
                broker_submission_status="taiwan_broker_error",
                submitted_to_broker=True,
                metadata={**base_metadata, "rejected_order_count": 1,
                          "execution_error_message": str(exc)},
            )
            log.error("[%s] TW broker order failed for %s: %s", signal_id, symbol, exc)
            return

        fill_price = float(order.get("fill_price") or 0.0)
        fill_qty = float(order.get("fill_qty") or qty)
        order_id = str(order.get("order_id") or "")
        if fill_price > 0:
            self.SetSecurityPrice(
                str(symbol),
                fill_price,
                as_of=str(order.get("filled_at") or order.get("updated_at") or _iso_now()),
                source=str(order.get("quote_source") or "shioaji_paper_fill"),
                authoritative=False,
            )
        holding = self._holding(str(symbol))
        signed_fill_qty = fill_qty if side == "buy" else -fill_qty
        holding.Quantity += signed_fill_qty
        self._cash -= signed_fill_qty * fill_price
        self._record_fill()
        self._publish(
            "paper_fill_simulated", str(symbol), signed_fill_qty, action,
            broker_submission_status="filled",
            submitted_to_broker=True,
            metadata={**base_metadata, "broker_order_id": order_id,
                      "shioaji_trade_id": order_id},
        )
        log.info("[%s] TW paper fill %s %s %.0f @ %.4f (order=%s)",
                 signal_id, side, native, fill_qty, fill_price, order_id)

    def _taiwan_capital_pool_id(self) -> str:
        binding = getattr(self, "_cached_binding", None) or {}
        return str(binding.get("capital_pool_id") or "pool-tw-equity-paper")

    @staticmethod
    def _post_broker_paper_order(broker_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            broker_url + "/api/broker/paper/orders",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            timeout = int(os.getenv("PANTHEON_BROKER_TIMEOUT_SECONDS", "5"))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise RuntimeError(f"broker HTTP {exc.code}: {detail}") from exc
        order = body.get("order")
        if not isinstance(order, dict):
            raise RuntimeError(f"broker response missing order: {str(body)[:200]}")
        return order

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
        self._record_fill()
        self._publish("paper_fill_simulated", symbol, delta, "set_holdings")

    def MarketOrder(self, symbol: str, quantity: float) -> None:  # noqa: N802
        security = self._security(symbol)
        self._holding(symbol).Quantity += float(quantity)
        self._cash -= float(quantity) * float(security.Price)
        self._record_fill()
        self._publish("paper_fill_simulated", symbol, quantity, "market_order")

    def LimitOrder(self, symbol: str, quantity: float, limit_price: float) -> None:  # noqa: N802
        security = self._security(symbol)
        self.SetSecurityPrice(
            symbol,
            float(limit_price),
            as_of=_iso_now(),
            source="paper_limit_fill",
            authoritative=False,
        )
        self._holding(symbol).Quantity += float(quantity)
        self._cash -= float(quantity) * float(security.Price)
        self._record_fill()
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
        self._record_fill()
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

    def RecordSignalProcessed(self, signal: dict[str, Any]) -> None:  # noqa: N802
        if self._event_sink is None:
            return
        self._event_sink(
            OrderEvent(
                event_type="signal_generation",
                symbol=signal["symbol"],
                quantity=float(signal.get("quantity") or 0.0),
                fill_price=0.0,
                action=signal["action"],
                submitted_to_broker=False,
                metadata=signal,
            )
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

    def mark_symbols(self) -> list[str]:
        return [
            symbol
            for symbol, holding in sorted(self.Portfolio.items())
            if abs(float(holding.Quantity)) > 1e-12
        ]

    def apply_market_marks(self, marks: Mapping[str, MarketMark]) -> None:
        for symbol, mark in marks.items():
            self.SetSecurityMark(
                symbol,
                mark.price,
                as_of=mark.as_of,
                source=mark.source_ref,
            )

    def authoritative_marks(self) -> dict[str, MarketMark]:
        marks: dict[str, MarketMark] = {}
        for symbol in self.mark_symbols():
            security = self._security(symbol)
            if not security.MarkAuthoritative or not security.MarkAsOf or not security.MarkSource:
                continue
            marks[symbol] = MarketMark(
                symbol=symbol,
                price=float(security.Price),
                as_of=security.MarkAsOf,
                source_ref=security.MarkSource,
            )
        return marks

    def performance_ledger(self) -> dict[str, Any]:
        return {
            "binding_id": self._performance_binding_id,
            "initial_cash": float(self._initial_cash),
            "cash": float(self._cash),
            "positions": self.positions(),
            "fill_count": int(self._fill_count),
            "ledger_started_at": self._ledger_started_at,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "state_path": str(self._state_path) if self._state_path else None,
            "state_error": self._state_error,
            "state_load_error": self._state_load_error,
            "state_binding_error": self._state_binding_error,
        }

    def BindPerformanceBinding(self, binding_id: str | None) -> bool:  # noqa: N802
        if self._state_load_error:
            return False
        if self._loaded_state_missing_binding_id:
            return False
        candidate = str(binding_id or "").strip()
        if not candidate:
            self._state_binding_error = "performance binding identity is missing"
            return False
        if self._performance_binding_id:
            if self._performance_binding_id != candidate:
                self._state_binding_error = (
                    "performance ledger binding mismatch: "
                    f"state={self._performance_binding_id} runtime={candidate}"
                )
                return False
            self._state_binding_error = None
            if self._state_error:
                self._persist_state()
            return self._state_error is None
        self._performance_binding_id = candidate
        self._state_binding_error = None
        self._persist_state()
        return self._state_error is None

    def performance_window_state(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._performance_window_state))

    def save_performance_window(self, payload: Mapping[str, Any]) -> bool:
        self._performance_window_state = json.loads(json.dumps(dict(payload)))
        self._persist_state()
        return self._state_error is None

    def _record_fill(self) -> None:
        filled_at = _iso_now()
        if self._fill_count == 0:
            self._first_fill_at = filled_at
        self._fill_count += 1
        self._last_fill_at = filled_at
        self._persist_state()

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "paper_performance_ledger.v1":
                raise ValueError("unsupported paper performance ledger schema")
            initial_cash = float(payload["initial_cash"])
            cash = float(payload["cash"])
            fill_count = int(payload["fill_count"])
            if not math.isfinite(initial_cash) or initial_cash <= 0 or not math.isfinite(cash):
                raise ValueError("paper performance ledger contains invalid cash values")
            holdings = payload.get("holdings")
            if not isinstance(holdings, dict):
                raise ValueError("paper performance ledger holdings must be an object")
            restored: dict[str, _Holding] = {}
            for symbol, raw_quantity in holdings.items():
                quantity = float(raw_quantity)
                if not math.isfinite(quantity):
                    raise ValueError(f"paper performance ledger has invalid quantity for {symbol}")
                restored[str(symbol)] = _Holding(quantity)
            execution_prices = payload.get("execution_prices")
            if isinstance(execution_prices, dict):
                for symbol, raw_price in execution_prices.items():
                    price = float(raw_price)
                    if math.isfinite(price) and price > 0:
                        # Restored fill/execution prices are deliberately not
                        # authoritative marks; source-ingest must refresh them.
                        self._security(str(symbol)).Price = price
            self._initial_cash = initial_cash
            self._cash = cash
            self.Portfolio = restored
            self._fill_count = max(fill_count, 0)
            self._last_fill_at = str(payload.get("last_fill_at") or "") or None
            self._first_fill_at = (
                str(payload.get("first_fill_at") or "")
                or (self._last_fill_at if self._fill_count else None)
            )
            self._ledger_started_at = (
                str(payload.get("ledger_started_at") or "")
                or self._first_fill_at
                or _iso_now()
            )
            performance_window = payload.get("performance_window") or {}
            if not isinstance(performance_window, dict):
                raise ValueError("paper performance window must be an object")
            self._performance_window_state = dict(performance_window)
            self._performance_binding_id = (
                str(payload.get("binding_id") or "").strip() or None
            )
            if self._performance_binding_id is None:
                self._loaded_state_missing_binding_id = True
                self._state_binding_error = (
                    "loaded performance ledger is missing binding identity"
                )
            self._state_error = None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            self._state_load_error = self._state_error

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "schema_version": "paper_performance_ledger.v1",
            "binding_id": self._performance_binding_id,
            "initial_cash": float(self._initial_cash),
            "cash": float(self._cash),
            "fill_count": int(self._fill_count),
            "ledger_started_at": self._ledger_started_at,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "performance_window": self._performance_window_state,
            "holdings": {
                symbol: float(holding.Quantity)
                for symbol, holding in sorted(self.Portfolio.items())
                if abs(float(holding.Quantity)) > 1e-12
            },
            "execution_prices": {
                symbol: float(security.Price)
                for symbol, security in sorted(self.Securities.items())
                if math.isfinite(float(security.Price)) and float(security.Price) > 0
            },
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_name(f"{self._state_path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self._state_path)
            self._state_error = None
        except OSError as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"

    def open_bracket_orders(self) -> list[dict[str, Any]]:
        return [dict(order) for order in self._open_bracket_orders]

    def pnl(self) -> float:
        portfolio_value = self._cash
        for symbol, holding in self.Portfolio.items():
            portfolio_value += holding.Quantity * self._security(symbol).Price
        return float(portfolio_value - self._initial_cash)



class SyntheticMarketData:
    """Opt-in deterministic synthetic price source for PAPER runtimes only.

    Paper fills are marked at the fill price, so without any market-data feed a
    position's PnL stays flat at 0 and the telemetry -> reconcile -> evolution
    feedback half never has a signal to react to. When
    ``PANTHEON_PAPER_SYNTHETIC_MARKET_DATA`` is enabled this nudges held-symbol
    prices along a bounded, deterministic path (anchored at each symbol's first
    observed price) so paper PnL moves and the right half lights up.

    Paper-only by construction: it only calls ``algo.SetSecurityPrice`` on the
    in-process simulated book. It never connects a broker, never touches a real
    market-data feed, and is never wired for canary/live.
    """

    def __init__(self, *, amplitude: float = 0.05, freq: float = 0.5) -> None:
        self._amplitude = float(amplitude)
        self._freq = float(freq)
        self._step = 0
        self._anchor: dict[str, float] = {}

    @staticmethod
    def _phase_offset(symbol: str) -> float:
        return (sum(ord(c) for c in symbol) % 360) * math.pi / 180.0

    def advance(self, algo: Any) -> dict[str, float]:
        """Move every held symbol's price one bounded step; return new prices."""
        self._step += 1
        updated: dict[str, float] = {}
        portfolio = getattr(algo, "Portfolio", {}) or {}
        for symbol in list(portfolio.keys()):
            try:
                current = float(algo._security(symbol).Price)
            except Exception:  # noqa: BLE001
                current = 100.0
            anchor = self._anchor.setdefault(symbol, current if current else 100.0)
            price = round(
                anchor * (1.0 + self._amplitude * math.sin(self._step * self._freq + self._phase_offset(symbol))),
                4,
            )
            try:
                algo.SetSecurityPrice(
                    symbol,
                    price,
                    as_of=_iso_now(),
                    source="synthetic_market_data",
                    authoritative=False,
                )
            except TypeError:
                # Compatibility for the deliberately tiny fake used by the
                # isolated synthetic-source unit tests.  Canonical runtime
                # valuation never treats this source as authoritative.
                algo.SetSecurityPrice(symbol, price)
            updated[symbol] = price
        return updated


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
        incoming_envelope = event_metadata.get("correlation_envelope")
        event_metrics = dict(metrics)
        metric_as_of: str | None = None
        if event_type == "pnl_snapshot":
            raw_as_of = event_metrics.pop("pnl_as_of", None)
            metric_as_of = str(raw_as_of) if raw_as_of not in (None, "") else None
        elif event_type == "drawdown_snapshot":
            raw_as_of = event_metrics.pop("drawdown_as_of", None)
            metric_as_of = str(raw_as_of) if raw_as_of not in (None, "") else None
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
            "metrics": event_metrics,
            "metadata": event_metadata,
        }
        if metric_as_of is not None:
            payload[f"{'pnl' if event_type == 'pnl_snapshot' else 'drawdown'}_as_of"] = metric_as_of
        lineage_ref = os.getenv("PANTHEON_LINEAGE_REF", "").strip()
        if lineage_ref:
            payload["target"]["lineage_ref"] = lineage_ref
        if self._identity.trace_id:
            payload["trace_id"] = self._identity.trace_id
        if isinstance(incoming_envelope, Mapping):
            payload["correlation_envelope"] = propagate_envelope(
                incoming_envelope,
                producer="execution.paper_runtime",
                event_id=str(payload["event_id"]),
                event_time=str(payload["created_at"]),
            )
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

    def emit_drawdown_snapshot(
        self,
        drawdown_pct: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics: dict[str, Any] = {"drawdown_pct": float(drawdown_pct)}
        if extra_metrics:
            metrics.update(extra_metrics)
        return self.emit("drawdown_snapshot", metrics, metadata=metadata)

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
        mark_provider: SourceIngestMarkProvider | None = None,
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
        self._mark_provider = mark_provider or SourceIngestMarkProvider()
        self._drawdown_tracker = RollingDrawdownTracker(
            window_days=int(os.getenv("PANTHEON_PERFORMANCE_WINDOW_DAYS", "20"))
        )
        self._algo = PaperExecutionAlgorithm(
            event_sink=self._handle_order_event,
            deployment_stage=self._identity.deployment_stage or self._identity.runtime_mode or "paper",
            bracket_order_execution_enabled=_as_bool(
                os.getenv("PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED"),
                default=True,
            ),
            state_path=os.getenv("PANTHEON_PERFORMANCE_STATE_PATH") or None,
        )
        self._performance_state_restore_error: str | None = None
        try:
            self._drawdown_tracker.restore(self._algo.performance_window_state())
        except (TypeError, ValueError) as exc:
            self._performance_state_restore_error = f"{type(exc).__name__}: {exc}"
        self._consumer = SignalConsumer(
            store_client=self._store,
            binding_id=self._identity.binding_id or None,
            runtime_id=self._identity.runtime_id or None,
            capital_pool_id=self._identity.capital_pool_id or None,
        )
        self._poll_interval_seconds = poll_interval_seconds or _as_float(
            os.getenv("PANTHEON_RUNTIME_POLL_INTERVAL_SECONDS"),
            1.0,
        )
        self._max_batch_size = max(int(max_batch_size or os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")), 1)
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._outbox_path = os.getenv("PANTHEON_OUTBOX_PATH") or "/data/runtime/outbox.jsonl"
        outbox_dir = os.path.dirname(self._outbox_path)
        if outbox_dir:
            try:
                os.makedirs(outbox_dir, exist_ok=True)
            except Exception:
                self._outbox_path = os.path.join(tempfile.gettempdir(), "outbox.jsonl")
        self._outbox_lock = threading.Lock()
        self._outbox_event = threading.Event()
        self._outbox_thread: threading.Thread | None = None
        self._started_at = _iso_now()
        self._synthetic_market = (
            SyntheticMarketData()
            if _as_bool(os.getenv("PANTHEON_PAPER_SYNTHETIC_MARKET_DATA"))
            else None
        )
        self._last_poll_at: str | None = None
        self._last_drain_at: str | None = None
        self._last_skipped_status: str | None = None
        self._last_error: str | None = None
        self._last_heartbeat_at: str | None = None
        self._poll_count = 0
        self._processed_signal_count = 0
        self._execution_event_count = 0
        self._fill_event_count = 0
        self._recent_order_events: list[dict[str, Any]] = []
        self._performance_telemetry: dict[str, Any] = {
            "status": "not_evaluated",
            "code": "performance_not_evaluated",
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._emit_deploy_started()

        # Ensure outbox directory exists
        outbox_dir = os.path.dirname(self._outbox_path)
        if outbox_dir:
            try:
                os.makedirs(outbox_dir, exist_ok=True)
            except Exception as exc:
                log.warning("Failed to create outbox directory %s: %s", outbox_dir, exc)

        self._outbox_thread = threading.Thread(target=self._outbox_loop, daemon=True, name="paper-runtime-outbox")
        self._outbox_thread.start()

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="paper-runtime-loop")
        self._thread.start()
        self._emit_deploy_completed()

    def stop(self) -> None:
        self._shutdown.set()
        self._outbox_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._outbox_thread is not None:
            self._outbox_thread.join(timeout=5)

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
                binding_id = str(
                    binding.get("binding_id")
                    or binding.get("runtime_binding_id")
                    or ""
                )
                if not self._algo.BindPerformanceBinding(binding_id):
                    ledger = self._algo.performance_ledger()
                    raise RuntimeError(
                        ledger.get("state_binding_error")
                        or ledger.get("state_error")
                        or "paper performance ledger binding failed"
                    )
                binding_status = str(binding.get("status") or "").lower()
                if binding_status in _HALT_BINDING_STATUSES:
                    # Safety gate (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY):
                    # do not execute while the binding is halted; hold signals on
                    # the queue so they replay when the binding returns to active.
                    self._last_skipped_status = binding_status
                    self._last_drain_at = _iso_now()
                    self._last_error = None
                else:
                    self._last_skipped_status = None
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
                if self._synthetic_market is not None:
                    self._synthetic_market.advance(self._algo)
                self._maybe_emit_performance_snapshots()
                self._maybe_emit_heartbeat()
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
                    "last_skipped_status": self._last_skipped_status,
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
                    "performance_telemetry": dict(self._performance_telemetry),
                    "last_error": self._last_error,
                },
                "stub_mode": False,
            }

    def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            self.drain_once()
            self._shutdown.wait(self._poll_interval_seconds)

    def _handle_order_event(self, event: OrderEvent) -> None:
        if event.event_type == "signal_generation":
            binding = self._binding_resolver.resolve() or {}
            tenant_id = binding.get("tenant_id") or "default"
            environment = binding.get("deployment_stage") or "paper"
            signal_id = event.metadata.get("signal_id")
            journey_id = event.metadata.get("journey_id") or (f"tj-{signal_id}" if signal_id else f"tj-evt-{event.event_id}")

            journey_event = {
                "event_id": f"sig-{signal_id}-generation",
                "journey_id": journey_id,
                "tenant_id": tenant_id,
                "environment": environment,
                "occurred_at": event.metadata.get("timestamp") or event.created_at,
                "recorded_at": _iso_now(),
                "source": "runtime",
                "stage": "signal_generation",
                "stage_status": "succeeded",
                "signal_id": signal_id,
                "symbol": event.symbol,
                "order_type": event.metadata.get("order_type", "MARKET"),
                "quantity": event.quantity,
                "strategy_id": event.metadata.get("strategy_id"),
                "sequence": 1,
            }
            self._publish_journey_events([journey_event])
            return

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

        # Build first-class journey events directly with deterministic ordering and matching timestamps
        try:
            metadata = event.metadata or {}
            envelope = metadata.get("correlation_envelope") or {}
            signal_id = metadata.get("signal_id") or envelope.get("signal_id")

            binding = self._binding_resolver.resolve() or {}
            tenant_id = metadata.get("tenant_id") or envelope.get("tenant_id") or binding.get("tenant_id") or "default"
            environment = metadata.get("environment") or envelope.get("environment") or binding.get("deployment_stage") or "paper"

            journey_id = metadata.get("journey_id") or envelope.get("journey_id")
            if not journey_id:
                journey_id = f"tj-{signal_id}" if signal_id else f"tj-evt-{event.event_id}"

            journey_events = []
            occurred_at = event.created_at or _iso_now()
            recorded_at = _iso_now()

            # Sequence 2: trade_decision
            if event.event_type in ("paper_fill_simulated", "paper_order_simulated", "order_rejection"):
                journey_events.append({
                    "event_id": f"sig-{signal_id}-decision" if signal_id else f"evt-{event.event_id}-decision",
                    "journey_id": journey_id,
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "occurred_at": occurred_at,
                    "recorded_at": recorded_at,
                    "source": "runtime",
                    "stage": "trade_decision",
                    "stage_status": metadata.get("decision_status") or "succeeded",
                    "signal_id": signal_id,
                    "symbol": event.symbol,
                    "sequence": 2,
                    "correlation_envelope": envelope,
                })

            # Sequence 3: order_submission
            if event.event_type in ("paper_fill_simulated", "paper_order_simulated", "order_rejection"):
                stage_status = "submitted"
                if event.event_type == "paper_order_simulated":
                    stage_status = "noop"
                elif event.event_type == "order_rejection":
                    stage_status = "rejected"

                journey_events.append({
                    "event_id": f"sig-{signal_id}-order" if signal_id else f"evt-{event.event_id}-order",
                    "journey_id": journey_id,
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "occurred_at": occurred_at,
                    "recorded_at": recorded_at,
                    "source": "runtime",
                    "stage": "order_submission",
                    "stage_status": stage_status,
                    "signal_id": signal_id,
                    "symbol": event.symbol,
                    "sequence": 3,
                    "correlation_envelope": envelope,
                })

            # Sequence 4: fill_management
            if event.event_type in ("paper_fill_simulated", "order_rejection"):
                stage_status = "filled" if event.event_type == "paper_fill_simulated" else "failed"
                fill_event = {
                    "event_id": f"sig-{signal_id}-fill" if signal_id else f"evt-{event.event_id}-fill",
                    "journey_id": journey_id,
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "occurred_at": occurred_at,
                    "recorded_at": recorded_at,
                    "source": "runtime",
                    "stage": "fill_management",
                    "stage_status": stage_status,
                    "signal_id": signal_id,
                    "symbol": event.symbol,
                    "sequence": 4,
                    "correlation_envelope": envelope,
                }
                if event.event_type == "paper_fill_simulated":
                    fill_event["quantity"] = abs(event.quantity)
                    fill_event["price"] = event.fill_price
                    fill_event["side"] = "sell" if event.quantity < 0 else "buy"
                journey_events.append(fill_event)

            if journey_events:
                self._publish_journey_events(journey_events)
        except Exception as exc:
            log.warning("Failed to map or publish journey events directly: %s", exc)

    def _publish_journey_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        if not self._outbox_thread or not self._outbox_thread.is_alive():
            # Fallback to synchronous publish in tests or if the thread isn't running
            self._send_to_bff(events)
            return

        with self._outbox_lock:
            try:
                outbox_dir = os.path.dirname(self._outbox_path)
                if outbox_dir:
                    os.makedirs(outbox_dir, exist_ok=True)
                with open(self._outbox_path, "a", encoding="utf-8") as f:
                    for event in events:
                        f.write(json.dumps(event) + "\n")
            except Exception as exc:
                log.error("Failed to append events to outbox: %s", exc)
        self._outbox_event.set()

    def _outbox_loop(self) -> None:
        while not self._shutdown.is_set():
            self._outbox_event.wait(timeout=1.0)
            self._outbox_event.clear()

            events_to_send = []
            with self._outbox_lock:
                if os.path.exists(self._outbox_path):
                    try:
                        with open(self._outbox_path, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.strip():
                                    events_to_send.append(json.loads(line))
                    except Exception as exc:
                        log.error("Failed to read outbox file: %s", exc)

            if not events_to_send:
                continue

            success = self._send_to_bff(events_to_send)
            if success:
                with self._outbox_lock:
                    if os.path.exists(self._outbox_path):
                        try:
                            current_events = []
                            with open(self._outbox_path, "r", encoding="utf-8") as f:
                                for line in f:
                                    if line.strip():
                                        current_events.append(json.loads(line))
                            sent_ids = {e["event_id"] for e in events_to_send}
                            remaining = [e for e in current_events if e["event_id"] not in sent_ids]
                            if remaining:
                                tmp_path = self._outbox_path + ".tmp"
                                with open(tmp_path, "w", encoding="utf-8") as f:
                                    for e in remaining:
                                        f.write(json.dumps(e) + "\n")
                                os.replace(tmp_path, self._outbox_path)
                            else:
                                if os.path.exists(self._outbox_path):
                                    os.remove(self._outbox_path)
                        except Exception as exc:
                            log.error("Failed to update outbox file: %s", exc)
            else:
                self._shutdown.wait(timeout=2.0)

    def _send_to_bff(self, events: list[dict[str, Any]]) -> bool:
        bff_url = os.getenv("PANTHEON_BFF_URL", "http://operator-bff:8080").strip().rstrip("/")
        url = f"{bff_url}/bff/management/trade-journeys/events"
        body = json.dumps(events).encode("utf-8")

        token = os.getenv("PANTHEON_BFF_TOKEN") or os.getenv("BFF_TOKEN") or "op-dev:admin:mfa"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            timeout = int(os.getenv("PANTHEON_BFF_TIMEOUT_SECONDS", "5"))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
            return True
        except Exception as exc:
            log.warning("Failed to publish outbox events to BFF (will retry): %s", exc)
            return False

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
                "performance_telemetry": dict(self._performance_telemetry),
            },
        )
        if emitted:
            self._last_heartbeat_at = now

    def _maybe_emit_performance_snapshots(self) -> None:
        if not self._telemetry.enabled:
            return

        symbols = self._algo.mark_symbols()
        provider_diagnostic: dict[str, Any]
        provider_marks: dict[str, MarketMark] = {}
        if symbols:
            provider_marks, provider_diagnostic = self._mark_provider.resolve(symbols)
            self._algo.apply_market_marks(provider_marks)
        else:
            provider_diagnostic = self._mark_provider.snapshot(requested_symbols=[])

        ledger = self._algo.performance_ledger()
        if (
            ledger.get("state_load_error")
            or ledger.get("state_binding_error")
            or self._performance_state_restore_error
        ):
            self._performance_telemetry = {
                "status": "invalid_ledger",
                "code": "performance_ledger_load_failed",
                "attempted_at": _iso_now(),
                "state_path": ledger.get("state_path"),
                "detail": (
                    ledger.get("state_load_error")
                    or ledger.get("state_binding_error")
                    or self._performance_state_restore_error
                ),
            }
            return
        if ledger.get("state_error"):
            self._performance_telemetry = {
                "status": "invalid_ledger",
                "code": "performance_ledger_persist_failed",
                "attempted_at": _iso_now(),
                "state_path": ledger.get("state_path"),
                "detail": ledger.get("state_error"),
            }
            return
        valuation = value_portfolio(
            initial_cash=ledger["initial_cash"],
            cash=ledger["cash"],
            positions=ledger["positions"],
            # Use only this resolve cycle.  A prior algorithm/security mark
            # must not survive a source timeout or a newly missing symbol.
            marks=provider_marks,
            fill_count=ledger["fill_count"],
            last_fill_at=ledger["last_fill_at"],
            mark_diagnostic=provider_diagnostic,
            max_mark_age_seconds=_as_float(
                os.getenv("PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"),
                172800.0,
            ),
        )
        self._performance_telemetry = {
            "status": valuation.status,
            **valuation.diagnostic,
        }
        if valuation.sample is None:
            return

        sample = valuation.sample
        tracker_checkpoint = self._drawdown_tracker.export_state()
        try:
            drawdown_metrics = self._drawdown_tracker.observe(
                sample,
                initial_equity_as_of=(
                    ledger.get("first_fill_at") or ledger.get("ledger_started_at")
                ),
            )
        except ValueError as exc:
            self._drawdown_tracker.restore(tracker_checkpoint)
            self._performance_telemetry.update(
                {
                    "status": "invalid_drawdown_series",
                    "code": "invalid_drawdown_series",
                    "detail": str(exc),
                }
            )
            return
        if drawdown_metrics is None:
            self._performance_telemetry.update(
                {
                    "status": "unchanged",
                    "code": "performance_sample_unchanged",
                    "as_of": sample.as_of,
                }
            )
            return

        mark_refs = [mark.to_dict() for mark in sample.marks]
        metadata = {
            "runtime_package": "paper_execution_runtime",
            "queue_depth": self._safe_queue_depth(),
            "is_real_order": False,
            "is_real_capital": False,
            "capital_scale_pct": 0,
            "valuation_method": "fill_cash_ledger_mark_to_market",
            "valuation_as_of": sample.as_of,
            "mark_refs": mark_refs,
        }
        common_metrics = {
            "portfolio_value": sample.portfolio_value,
            "initial_cash": sample.initial_cash,
            "cash": sample.cash,
            "fill_count": sample.fill_count,
            "valuation_mark_count": len(sample.marks),
            **self._performance_snapshot_metrics(),
        }
        pnl_sent = self._telemetry.emit_pnl_snapshot(
            sample.pnl,
            metadata=metadata,
            extra_metrics={
                **common_metrics,
                "pnl_as_of": sample.as_of,
            },
        )
        emit_drawdown = getattr(self._telemetry, "emit_drawdown_snapshot", None)
        if callable(emit_drawdown):
            drawdown_sent = emit_drawdown(
                drawdown_metrics["drawdown_pct"],
                metadata=metadata,
                extra_metrics={
                    **common_metrics,
                    **{
                        key: value
                        for key, value in drawdown_metrics.items()
                        if key != "drawdown_pct"
                    },
                },
            )
        else:
            drawdown_sent = self._telemetry.emit(
                "drawdown_snapshot",
                {
                    "drawdown_pct": drawdown_metrics["drawdown_pct"],
                    **common_metrics,
                    **{
                        key: value
                        for key, value in drawdown_metrics.items()
                        if key != "drawdown_pct"
                    },
                },
                metadata=metadata,
            )
        self._performance_telemetry.update(
            {
                "status": "emitted" if pnl_sent and drawdown_sent else "emit_failed",
                "code": (
                    "performance_snapshots_emitted"
                    if pnl_sent and drawdown_sent
                    else "performance_snapshot_emit_failed"
                ),
                "as_of": sample.as_of,
                "pnl": sample.pnl,
                "drawdown_pct": drawdown_metrics["drawdown_pct"],
                "pnl_snapshot_sent": bool(pnl_sent),
                "drawdown_snapshot_sent": bool(drawdown_sent),
            }
        )
        if pnl_sent and drawdown_sent:
            if not self._algo.save_performance_window(self._drawdown_tracker.export_state()):
                self._performance_telemetry.update(
                    {
                        "status": "state_persist_failed",
                        "code": "performance_window_persist_failed",
                        "detail": self._algo.performance_ledger().get("state_error"),
                    }
                )
        else:
            # Retry the same observation after a transient telemetry failure;
            # do not advance the durable high-water series without both
            # canonical events.
            self._drawdown_tracker.restore(tracker_checkpoint)

    # Backward-compatible private hook retained for narrow callers/tests.  It
    # now enforces the paired, fail-closed performance contract.
    def _maybe_emit_pnl_snapshot(self) -> None:
        self._maybe_emit_performance_snapshots()

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
