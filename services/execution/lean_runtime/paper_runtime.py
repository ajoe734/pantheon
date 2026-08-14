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
from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    propagate_envelope,
    validate_envelope,
)

log = logging.getLogger(__name__)


_HALT_BINDING_STATUSES = frozenset({"paused", "pending_pause", "failed", "retired"})
_CANONICAL_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "order_submitted",
        "order_accepted",
        "paper_order_simulated",
        "paper_fill_simulated",
        "order_rejection",
        "position_snapshot",
    }
)
_LIFECYCLE_UUID_NAMESPACE = uuid.UUID("1760784c-c9e0-47eb-b0aa-d37f58d892df")
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


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _finite_float(value: Any, *, field: str, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _parse_ledger_timestamp(value: Any, *, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"paper performance ledger {field} is missing")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"paper performance ledger {field} is not a valid timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"paper performance ledger {field} must include a timezone"
        )
    return normalized, parsed.astimezone(timezone.utc)


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
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
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
        self._initial_cash = _finite_float(
            initial_cash,
            field="initial_cash",
            positive=True,
        )
        self._cash = self._initial_cash
        self._default_price = _finite_float(
            default_price,
            field="default_price",
            positive=True,
        )
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
        self._pending_performance_pair: dict[str, Any] | None = None
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
        validated_price = _finite_float(
            price,
            field="security price",
            positive=True,
        )
        security = self._security(str(symbol))
        security.Price = validated_price
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

    def _reject_invalid_paper_fill(
        self,
        symbol: str,
        action: str,
        *,
        reason: str,
        broker_submission_status: str = "invalid_paper_fill",
        submitted_to_broker: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        diagnostic = {
            "reject_reason": reason,
            "rejection_status": "rejected",
            "order_status": "rejected",
            "rejected_order_count": 1,
        }
        if metadata:
            diagnostic.update(dict(metadata))
        self._publish(
            "order_rejection",
            str(symbol),
            0.0,
            action,
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=diagnostic,
        )

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

        try:
            qty = _finite_float(
                quantity,
                field="Taiwan order quantity",
                positive=True,
            )
            if limit_price is not None:
                limit_price = _finite_float(
                    limit_price,
                    field="Taiwan limit price",
                    positive=True,
                )
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                action,
                reason="invalid_taiwan_order_input",
                broker_submission_status="tw_invalid_order_input",
                metadata={**base_metadata, "diagnostic": str(exc)},
            )
            log.warning("[%s] TW order rejected before broker: %s", signal_id, exc)
            return
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

        try:
            fill_price = _finite_float(
                order.get("fill_price"),
                field="Taiwan broker fill_price",
                positive=True,
            )
            fill_qty = _finite_float(
                order.get("fill_qty"),
                field="Taiwan broker fill_qty",
                positive=True,
            )
            signed_fill_qty = fill_qty if side == "buy" else -fill_qty
            current_quantity = _finite_float(
                self._holding(str(symbol)).Quantity,
                field="Taiwan current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="Taiwan current cash")
            next_quantity = current_quantity + signed_fill_qty
            next_cash = current_cash - (signed_fill_qty * fill_price)
            if not math.isfinite(next_quantity) or not math.isfinite(next_cash):
                raise ValueError("Taiwan broker fill produces non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                action,
                reason="invalid_taiwan_broker_fill",
                broker_submission_status="tw_invalid_broker_fill",
                submitted_to_broker=True,
                metadata={
                    **base_metadata,
                    "diagnostic": str(exc),
                    "broker_fill_qty": repr(order.get("fill_qty")),
                    "broker_fill_price": repr(order.get("fill_price")),
                },
            )
            log.error("[%s] TW broker returned invalid fill for %s: %s", signal_id, symbol, exc)
            return
        order_id = str(order.get("order_id") or "")
        self._commit_fill(
            str(symbol),
            next_quantity=next_quantity,
            next_cash=next_cash,
            execution_price=fill_price,
            price_as_of=str(order.get("filled_at") or order.get("updated_at") or _iso_now()),
            price_source=str(order.get("quote_source") or "shioaji_paper_fill"),
        )
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
        try:
            event_quantity = _finite_float(quantity, field="event quantity")
        except ValueError:
            event_quantity = 0.0
        try:
            event_price = _finite_float(security.Price, field="event fill price")
        except ValueError:
            event_price = 0.0
        event_metadata = dict(self._current_signal_metadata)
        if metadata:
            event_metadata.update(metadata)
        if event_type == "paper_fill_simulated":
            # All fill publishers call `_commit_fill` first.  This marker makes
            # the ordering explicit so downstream code never emits a position
            # snapshot for an uncommitted or rejected paper fill.
            event_metadata["ledger_committed"] = True
            event_metadata["ledger_fill_count"] = int(self._fill_count)
            event_metadata["position_quantity"] = float(
                self._holding(str(symbol)).Quantity
            )
        self._event_sink(
            OrderEvent(
                event_type=event_type,
                symbol=str(symbol),
                quantity=event_quantity,
                fill_price=event_price,
                action=action,
                submitted_to_broker=submitted_to_broker,
                broker_submission_status=broker_submission_status,
                metadata=event_metadata,
            )
        )

    def SetHoldings(self, symbol: str, target_percent: float) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            target_percent_value = _finite_float(
                target_percent,
                field="target percent",
            )
            price = _finite_float(
                security.Price,
                field="paper fill price",
                positive=True,
            )
            current_quantity = _finite_float(
                holding.Quantity,
                field="current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="current cash")
            target_quantity = (self._initial_cash * target_percent_value) / price
            delta = target_quantity - current_quantity
            next_cash = current_cash - (delta * price)
            if not all(math.isfinite(value) for value in (target_quantity, delta, next_cash)):
                raise ValueError("SetHoldings produced non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "set_holdings",
                reason="invalid_set_holdings_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        if abs(delta) <= 1e-12:
            metadata: dict[str, Any] = {
                "noop_reason": "set_holdings_no_delta",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "computed_quantity": 0.0,
                "position_quantity": float(current_quantity),
                "target_quantity": float(target_quantity),
                "target_percent": target_percent_value,
                "price": price,
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
        self._commit_fill(
            str(symbol),
            next_quantity=target_quantity,
            next_cash=next_cash,
        )
        self._publish("paper_fill_simulated", symbol, delta, "set_holdings")

    def MarketOrder(self, symbol: str, quantity: float) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            fill_quantity = _finite_float(quantity, field="market fill quantity")
            if fill_quantity == 0:
                raise ValueError("market fill quantity must be non-zero")
            price = _finite_float(
                security.Price,
                field="market fill price",
                positive=True,
            )
            current_quantity = _finite_float(
                holding.Quantity,
                field="current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="current cash")
            next_quantity = current_quantity + fill_quantity
            next_cash = current_cash - (fill_quantity * price)
            if not math.isfinite(next_quantity) or not math.isfinite(next_cash):
                raise ValueError("market fill produces non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "market_order",
                reason="invalid_market_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=next_quantity,
            next_cash=next_cash,
        )
        self._publish("paper_fill_simulated", symbol, fill_quantity, "market_order")

    def LimitOrder(self, symbol: str, quantity: float, limit_price: float) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            fill_quantity = _finite_float(quantity, field="limit fill quantity")
            if fill_quantity == 0:
                raise ValueError("limit fill quantity must be non-zero")
            fill_price = _finite_float(
                limit_price,
                field="limit fill price",
                positive=True,
            )
            current_quantity = _finite_float(
                holding.Quantity,
                field="current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="current cash")
            next_quantity = current_quantity + fill_quantity
            next_cash = current_cash - (fill_quantity * fill_price)
            if not math.isfinite(next_quantity) or not math.isfinite(next_cash):
                raise ValueError("limit fill produces non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "limit_order",
                reason="invalid_limit_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=next_quantity,
            next_cash=next_cash,
            execution_price=fill_price,
            price_as_of=_iso_now(),
            price_source="paper_limit_fill",
        )
        self._publish("paper_fill_simulated", symbol, fill_quantity, "limit_order")

    def Liquidate(self, symbol: str) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            quantity = _finite_float(
                holding.Quantity,
                field="liquidation quantity",
            )
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "liquidate",
                reason="invalid_liquidation_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
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
        try:
            price = _finite_float(
                security.Price,
                field="liquidation fill price",
                positive=True,
            )
            current_cash = _finite_float(self._cash, field="current cash")
            next_cash = current_cash + (quantity * price)
            if not math.isfinite(next_cash):
                raise ValueError("liquidation produces non-finite cash")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "liquidate",
                reason="invalid_liquidation_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=0.0,
            next_cash=next_cash,
        )
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
        pending = self._pending_performance_pair or {}
        return {
            "binding_id": self._performance_binding_id,
            "initial_cash": float(self._initial_cash),
            "cash": float(self._cash),
            "positions": self.positions(),
            "fill_count": int(self._fill_count),
            "ledger_started_at": self._ledger_started_at,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "pending_performance_pair_id": pending.get("pair_id"),
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

    def pending_performance_pair(self) -> dict[str, Any] | None:
        if self._pending_performance_pair is None:
            return None
        return json.loads(json.dumps(self._pending_performance_pair))

    @staticmethod
    def _validated_performance_pair(
        payload: Mapping[str, Any],
        *,
        binding_id: str,
    ) -> dict[str, Any]:
        try:
            candidate = json.loads(json.dumps(dict(payload), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pending performance pair is not strict JSON: {exc}") from exc
        if candidate.get("schema_version") != "paper_performance_pair.v1":
            raise ValueError("unsupported pending performance pair schema")
        pair_id = str(candidate.get("pair_id") or "").strip()
        try:
            uuid.UUID(pair_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("pending performance pair_id must be a UUID") from exc
        if str(candidate.get("binding_id") or "").strip() != binding_id:
            raise ValueError("pending performance pair binding mismatch")
        valuation_as_of, _ = _parse_ledger_timestamp(
            candidate.get("valuation_as_of"),
            field="pending_performance_pair.valuation_as_of",
        )
        staged_at, _ = _parse_ledger_timestamp(
            candidate.get("staged_at"),
            field="pending_performance_pair.staged_at",
        )
        next_window = candidate.get("next_window")
        if not isinstance(next_window, dict):
            raise ValueError("pending performance pair next_window must be an object")
        # Validate the staged window before it can ever replace the committed
        # drawdown state. This also rejects malformed/non-finite observations.
        staged_tracker = RollingDrawdownTracker()
        staged_tracker.restore(next_window)
        if str(next_window.get("latest_as_of") or "") != valuation_as_of:
            raise ValueError("pending performance pair next_window as-of mismatch")
        events = candidate.get("events")
        expected_types = ("pnl_snapshot", "drawdown_snapshot")
        if not isinstance(events, dict) or set(events) != set(expected_types):
            raise ValueError("pending performance pair must contain exactly two event legs")
        event_ids: set[str] = set()
        validated_events: dict[str, Any] = {}
        for event_type in expected_types:
            leg = events.get(event_type)
            if not isinstance(leg, dict) or not isinstance(leg.get("acked"), bool):
                raise ValueError(f"pending {event_type} leg must contain boolean acked")
            event_payload = leg.get("payload")
            if not isinstance(event_payload, dict):
                raise ValueError(f"pending {event_type} payload must be an object")
            if event_payload.get("event_type") != event_type:
                raise ValueError(f"pending {event_type} payload type mismatch")
            event_created_at, _ = _parse_ledger_timestamp(
                event_payload.get("created_at"),
                field=f"pending_performance_pair.{event_type}.created_at",
            )
            if event_created_at != staged_at:
                raise ValueError(f"pending {event_type} created_at mismatch")
            event_id = str(event_payload.get("event_id") or "").strip()
            try:
                uuid.UUID(event_id)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"pending {event_type} event_id must be a UUID") from exc
            if event_id in event_ids:
                raise ValueError("pending performance pair event IDs must be distinct")
            event_ids.add(event_id)
            if str(event_payload.get("binding_id") or "").strip() != binding_id:
                raise ValueError(f"pending {event_type} payload binding mismatch")
            if str(event_payload.get(f"{'pnl' if event_type == 'pnl_snapshot' else 'drawdown'}_as_of") or "") != valuation_as_of:
                raise ValueError(f"pending {event_type} as-of mismatch")
            metadata = event_payload.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("performance_pair_id") != pair_id:
                raise ValueError(f"pending {event_type} pair metadata mismatch")
            if metadata.get("performance_pair_leg") != event_type:
                raise ValueError(f"pending {event_type} leg metadata mismatch")
            metrics = event_payload.get("metrics")
            if not isinstance(metrics, dict):
                raise ValueError(f"pending {event_type} metrics must be an object")
            if event_type == "pnl_snapshot":
                if "pnl" not in metrics or "drawdown_pct" in metrics or "drawdown" in metrics:
                    raise ValueError("pending pnl leg has invalid primary metrics")
                _finite_float(metrics["pnl"], field="pending pnl metric")
            else:
                if "drawdown_pct" not in metrics or "pnl" in metrics:
                    raise ValueError("pending drawdown leg has invalid primary metrics")
                drawdown = _finite_float(
                    metrics["drawdown_pct"],
                    field="pending drawdown metric",
                )
                if not 0 <= drawdown <= 1:
                    raise ValueError("pending drawdown metric must be in [0, 1]")
            validated_events[event_type] = leg
        candidate.update(
            {
                "pair_id": pair_id,
                "binding_id": binding_id,
                "valuation_as_of": valuation_as_of,
                "staged_at": staged_at,
                "events": validated_events,
            }
        )
        return candidate

    def stage_performance_pair(self, payload: Mapping[str, Any]) -> bool:
        if self._pending_performance_pair is not None:
            self._state_error = "pending performance pair already exists"
            return False
        binding_id = str(self._performance_binding_id or "").strip()
        if not binding_id:
            self._state_error = "pending performance pair requires a bound ledger"
            return False
        try:
            candidate = self._validated_performance_pair(payload, binding_id=binding_id)
        except (TypeError, ValueError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            return False
        self._pending_performance_pair = candidate
        if not self._persist_state():
            self._pending_performance_pair = None
            return False
        return True

    def ack_performance_pair_leg(self, pair_id: str, event_type: str) -> bool:
        pending = self._pending_performance_pair
        if pending is None or pending.get("pair_id") != pair_id:
            self._state_error = "pending performance pair identity mismatch"
            return False
        leg = pending.get("events", {}).get(event_type)
        if not isinstance(leg, dict):
            self._state_error = "pending performance pair leg is missing"
            return False
        if leg.get("acked") is True:
            return True
        previous = json.loads(json.dumps(pending))
        leg["acked"] = True
        if not self._persist_state():
            self._pending_performance_pair = previous
            return False
        return True

    def finalize_performance_pair(self, pair_id: str) -> bool:
        pending = self._pending_performance_pair
        if pending is None or pending.get("pair_id") != pair_id:
            self._state_error = "pending performance pair identity mismatch"
            return False
        events = pending.get("events", {})
        if not all(
            isinstance(events.get(event_type), dict)
            and events[event_type].get("acked") is True
            for event_type in ("pnl_snapshot", "drawdown_snapshot")
        ):
            self._state_error = "pending performance pair is not fully acknowledged"
            return False
        previous_window = self._performance_window_state
        previous_pending = pending
        self._performance_window_state = json.loads(json.dumps(pending["next_window"]))
        self._pending_performance_pair = None
        if not self._persist_state():
            self._performance_window_state = previous_window
            self._pending_performance_pair = previous_pending
            return False
        return True

    def save_performance_window(self, payload: Mapping[str, Any]) -> bool:
        try:
            serialized = json.dumps(dict(payload), allow_nan=False)
            candidate = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            return False
        previous = self._performance_window_state
        self._performance_window_state = candidate
        if not self._persist_state():
            self._performance_window_state = previous
            return False
        return self._state_error is None

    def _record_fill(self) -> None:
        filled_at = _iso_now()
        if self._fill_count == 0:
            self._first_fill_at = filled_at
        self._fill_count += 1
        self._last_fill_at = filled_at
        if not self._persist_state():
            raise RuntimeError(
                "paper fill ledger persistence failed: "
                f"{self._state_error or 'unknown persistence error'}"
            )

    def _commit_fill(
        self,
        symbol: str,
        *,
        next_quantity: float,
        next_cash: float,
        execution_price: float | None = None,
        price_as_of: str | None = None,
        price_source: str = "paper_fill",
    ) -> None:
        """Atomically mutate and persist a fill before any fill event is published."""
        holding = self._holding(symbol)
        security = self._security(symbol)
        checkpoint = {
            "quantity": holding.Quantity,
            "cash": self._cash,
            "fill_count": self._fill_count,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "price": security.Price,
            "mark_as_of": security.MarkAsOf,
            "mark_source": security.MarkSource,
            "mark_authoritative": security.MarkAuthoritative,
        }
        try:
            if execution_price is not None:
                self.SetSecurityPrice(
                    symbol,
                    execution_price,
                    as_of=price_as_of,
                    source=price_source,
                    authoritative=False,
                )
            holding.Quantity = next_quantity
            self._cash = next_cash
            self._record_fill()
        except Exception:
            holding.Quantity = checkpoint["quantity"]
            self._cash = checkpoint["cash"]
            self._fill_count = checkpoint["fill_count"]
            self._first_fill_at = checkpoint["first_fill_at"]
            self._last_fill_at = checkpoint["last_fill_at"]
            security.Price = checkpoint["price"]
            security.MarkAsOf = checkpoint["mark_as_of"]
            security.MarkSource = checkpoint["mark_source"]
            security.MarkAuthoritative = checkpoint["mark_authoritative"]
            raise

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("paper performance ledger must be an object")
            if payload.get("schema_version") != "paper_performance_ledger.v1":
                raise ValueError("unsupported paper performance ledger schema")
            initial_cash = _finite_float(
                payload["initial_cash"],
                field="paper performance ledger initial_cash",
                positive=True,
            )
            cash = _finite_float(
                payload["cash"],
                field="paper performance ledger cash",
            )
            raw_fill_count = payload["fill_count"]
            if isinstance(raw_fill_count, bool) or not isinstance(raw_fill_count, int):
                raise ValueError("paper performance ledger fill_count must be an integer")
            fill_count = raw_fill_count
            if fill_count < 0:
                raise ValueError("paper performance ledger fill_count must be non-negative")
            holdings = payload.get("holdings")
            if not isinstance(holdings, dict):
                raise ValueError("paper performance ledger holdings must be an object")
            restored: dict[str, _Holding] = {}
            for symbol, raw_quantity in holdings.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError("paper performance ledger contains an empty symbol")
                normalized_symbol = symbol.strip()
                quantity = _finite_float(
                    raw_quantity,
                    field=f"paper performance ledger quantity for {normalized_symbol}",
                )
                if quantity == 0:
                    raise ValueError(
                        f"paper performance ledger stores zero quantity for {normalized_symbol}"
                    )
                if normalized_symbol in restored:
                    raise ValueError(
                        f"paper performance ledger contains duplicate symbol {normalized_symbol}"
                    )
                restored[normalized_symbol] = _Holding(quantity)
            execution_prices = payload.get("execution_prices", {})
            if not isinstance(execution_prices, dict):
                raise ValueError("paper performance ledger execution_prices must be an object")
            restored_prices: dict[str, float] = {}
            for symbol, raw_price in execution_prices.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError(
                        "paper performance ledger execution_prices contains an empty symbol"
                    )
                normalized_symbol = symbol.strip()
                price = _finite_float(
                    raw_price,
                    field=f"paper performance ledger execution price for {normalized_symbol}",
                    positive=True,
                )
                if normalized_symbol in restored_prices:
                    raise ValueError(
                        "paper performance ledger execution_prices contains duplicate symbol "
                        f"{normalized_symbol}"
                    )
                restored_prices[normalized_symbol] = price

            ledger_started_at, ledger_started_dt = _parse_ledger_timestamp(
                payload.get("ledger_started_at"),
                field="ledger_started_at",
            )
            first_fill_at_raw = payload.get("first_fill_at")
            last_fill_at_raw = payload.get("last_fill_at")
            if fill_count == 0:
                if first_fill_at_raw is not None or last_fill_at_raw is not None:
                    raise ValueError(
                        "paper performance ledger without fills must not have fill timestamps"
                    )
                if restored:
                    raise ValueError(
                        "paper performance ledger without fills cannot contain holdings"
                    )
                if not math.isclose(cash, initial_cash, rel_tol=0.0, abs_tol=1e-9):
                    raise ValueError(
                        "paper performance ledger without fills must retain initial cash"
                    )
                first_fill_at = None
                last_fill_at = None
            else:
                first_fill_at, first_fill_dt = _parse_ledger_timestamp(
                    first_fill_at_raw,
                    field="first_fill_at",
                )
                last_fill_at, last_fill_dt = _parse_ledger_timestamp(
                    last_fill_at_raw,
                    field="last_fill_at",
                )
                if ledger_started_dt > first_fill_dt:
                    raise ValueError(
                        "paper performance ledger ledger_started_at is after first_fill_at"
                    )
                if first_fill_dt > last_fill_dt:
                    raise ValueError(
                        "paper performance ledger first_fill_at is after last_fill_at"
                    )
                missing_execution_prices = sorted(set(restored) - set(restored_prices))
                if missing_execution_prices:
                    raise ValueError(
                        "paper performance ledger holdings lack execution prices: "
                        f"{missing_execution_prices}"
                    )
                if len(restored) > fill_count:
                    raise ValueError(
                        "paper performance ledger has more open symbols than recorded fills"
                    )

            performance_window = payload.get("performance_window", {})
            if not isinstance(performance_window, dict):
                raise ValueError("paper performance window must be an object")
            # Reject Python's permissive NaN/Infinity JSON extensions anywhere
            # in the persisted auxiliary state before mutating the live ledger.
            json.dumps(performance_window, allow_nan=False)

            binding_value = payload.get("binding_id")
            if binding_value is not None and not isinstance(binding_value, str):
                raise ValueError("paper performance ledger binding_id must be a string")
            normalized_binding_id = str(binding_value or "").strip()
            pending_performance_pair = payload.get("pending_performance_pair")
            if pending_performance_pair is not None:
                if not normalized_binding_id:
                    raise ValueError("pending performance pair requires ledger binding identity")
                if not isinstance(pending_performance_pair, dict):
                    raise ValueError("pending performance pair must be an object")
                pending_performance_pair = self._validated_performance_pair(
                    pending_performance_pair,
                    binding_id=normalized_binding_id,
                )

            self._initial_cash = initial_cash
            self._cash = cash
            self.Portfolio = restored
            self.Securities = {}
            for symbol, price in restored_prices.items():
                # Restored fill/execution prices are deliberately not
                # authoritative marks; source-ingest must refresh them.
                self._security(symbol).Price = price
            self._fill_count = fill_count
            self._last_fill_at = last_fill_at
            self._first_fill_at = first_fill_at
            self._ledger_started_at = ledger_started_at
            self._performance_window_state = dict(performance_window)
            self._pending_performance_pair = pending_performance_pair
            self._performance_binding_id = normalized_binding_id or None
            if self._performance_binding_id is None:
                self._loaded_state_missing_binding_id = True
                self._state_binding_error = (
                    "loaded performance ledger is missing binding identity"
                )
            self._state_error = None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            self._state_load_error = self._state_error

    def _persist_state(self) -> bool:
        if self._state_path is None:
            return True
        if self._state_load_error:
            # Never replace a ledger that failed validation during load.  The
            # operator needs the original bytes for diagnosis/recovery.
            self._state_error = self._state_load_error
            return False
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
            "pending_performance_pair": self._pending_performance_pair,
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
        temporary: Path | None = None
        directory_fd: int | None = None
        try:
            serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            directory_fd = os.open(
                self._state_path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            temporary = self._state_path.with_name(f"{self._state_path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(serialized, encoding="utf-8")
            with temporary.open("rb") as state_file:
                os.fsync(state_file.fileno())
            os.replace(temporary, self._state_path)
            os.fsync(directory_fd)
            self._state_error = None
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        finally:
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

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
        runtime_binding_id: str | None = None,
    ) -> None:
        self._client = client
        self._runtime_id = runtime_id
        self._runtime_binding_id = str(
            runtime_binding_id
            or (
                runtime_context.runtime_binding_id
                if runtime_context is not None
                else ""
            )
            or ""
        ).strip()
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
        if self._runtime_binding_id:
            exact_matches = [
                binding
                for binding in matches
                if str(
                    binding.get("binding_id")
                    or binding.get("runtime_binding_id")
                    or ""
                ).strip()
                == self._runtime_binding_id
            ]
            if exact_matches:
                matches = exact_matches
            elif self._context_binding is not None:
                self._cached_binding = dict(self._context_binding)
                self._binding_source = "runtime_context"
                self._last_sync_at = _iso_now()
                self._last_error = None
                return self._cached_binding
            else:
                self._cached_binding = None
                self._binding_source = None
                self._last_sync_at = _iso_now()
                self._last_error = (
                    "runtime binding lookup found no exact binding_id "
                    f"{self._runtime_binding_id!r} for runtime_id "
                    f"{self._runtime_id!r}"
                )
                return None
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
        self._service_token = os.getenv(
            "PANTHEON_TELEMETRY_SERVICE_TOKEN", ""
        ).strip()
        self._tenant_id = (
            os.getenv("PANTHEON_TENANT_ID", "default").strip() or "default"
        )
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

        supplied_metadata = dict(metadata or {})
        strategy_id = str(
            supplied_metadata.get("strategy_id")
            or os.getenv("PANTHEON_STRATEGY_ID")
            or (self._runtime_context.artifact.strategy_id if self._runtime_context else "")
            or artifact_id
            or "paper-runtime"
        )
        artifact_type = str(os.getenv("PANTHEON_ARTIFACT_TYPE", "execution_bundle"))
        event_metadata = self._base_metadata(binding)
        event_metadata.update(supplied_metadata)
        authority_refs = self._identity.authority_refs()
        persona_id = self._resolve_persona_id(binding, event_metadata)
        if persona_id:
            authority_refs["persona_id"] = persona_id
            event_metadata["persona_id"] = persona_id
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
            "authority_refs": authority_refs,
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
            try:
                outgoing_envelope = propagate_envelope(
                    incoming_envelope,
                    producer="execution.paper_runtime",
                    event_id=str(payload["event_id"]),
                    event_time=str(payload["created_at"]),
                )
            except CorrelationEnvelopeError as exc:
                return self._fail_build(f"invalid correlation envelope: {exc}")
            payload["correlation_envelope"] = outgoing_envelope
            payload["tenant_id"] = outgoing_envelope["tenant_id"]
            payload["journey_id"] = outgoing_envelope["journey_id"]
            payload["trace_id"] = outgoing_envelope["trace_id"]

        if event_type in _CANONICAL_LIFECYCLE_EVENT_TYPES and isinstance(
            payload.get("correlation_envelope"), Mapping
        ):
            run_id = str(event_metadata.get("run_id") or "").strip()
            signal_id = str(event_metadata.get("signal_id") or "").strip()
            raw_sequence = event_metadata.get("sequence_no")
            causal_parent_id = str(
                event_metadata.get("causal_parent_id") or ""
            ).strip()
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                return self._fail_build(
                    "canonical lifecycle sequence_no must be a positive integer"
                )
            if raw_sequence < 1:
                return self._fail_build(
                    "canonical lifecycle sequence_no must be a positive integer"
                )
            if not run_id or not signal_id or not causal_parent_id:
                return self._fail_build(
                    "canonical lifecycle telemetry requires run_id, signal_id, and causal_parent_id"
                )
            outgoing_envelope = payload["correlation_envelope"]
            if outgoing_envelope["causation_event_id"] != causal_parent_id:
                return self._fail_build(
                    "causal_parent_id must match correlation envelope causation_event_id"
                )
            journey_id = str(outgoing_envelope["journey_id"])
            loop_run_id = str(
                event_metadata.get("loop_run_id") or f"lr-{run_id}"
            )
            payload.update(
                {
                    "aggregate_type": "trade_journey",
                    "aggregate_id": journey_id,
                    "sequence_no": raw_sequence,
                    "causal_parent_id": causal_parent_id,
                    "source_mode": "live",
                    "run_id": run_id,
                    "loop_run_id": loop_run_id,
                    "signal_id": signal_id,
                }
            )
            event_metadata.setdefault("journey_id", journey_id)
            event_metadata.setdefault("loop_run_id", loop_run_id)
            event_metadata.setdefault("source_mode", "live")
            for field in (
                "decision_id",
                "risk_decision_id",
                "client_order_id",
                "order_id",
                "fill_id",
                "position_id",
                "symbol",
            ):
                value = event_metadata.get(field)
                if value not in (None, ""):
                    payload[field] = value
            if event_type == "position_snapshot":
                position_qty = event_metrics.get("position_qty")
                if position_qty is not None:
                    payload["position_qty"] = position_qty
                price = event_metrics.get("price")
                if price is not None:
                    payload["price"] = price
        return payload

    def _resolve_persona_id(
        self,
        binding: Mapping[str, Any],
        event_metadata: Mapping[str, Any],
    ) -> str:
        binding_metadata = (
            binding.get("metadata") if isinstance(binding.get("metadata"), Mapping) else {}
        )
        candidates = (
            self._identity.persona_id,
            binding.get("persona_id"),
            binding.get("sponsor_persona_id"),
            binding_metadata.get("persona_id"),
            binding_metadata.get("sponsor_persona_id"),
            event_metadata.get("persona_id"),
            event_metadata.get("sponsor_persona_id"),
            os.getenv("PANTHEON_PERSONA_ID"),
        )
        for candidate in candidates:
            cleaned = _clean_text(candidate)
            if cleaned:
                return cleaned
        return ""

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

        return self.emit_payload(payload)

    def emit_payload(self, payload: Mapping[str, Any]) -> bool:
        """Deliver an already-built immutable event for exact retry.

        Important producer events are staged durably before this method is
        called.  Retrying the stored payload preserves its event ID, metrics,
        metadata, and correlation envelope so telemetry idempotency can safely
        collapse an accepted response that was lost in transit.
        """
        if not self._enabled:
            return False

        try:
            body = json.dumps(dict(payload), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Tenant-Id": self._tenant_id,
        }
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        request = urllib.request.Request(
            f"{self._url}/api/telemetry/ingest",
            data=body,
            headers=headers,
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
        binding_effective_at = binding.get("effective_at") or binding.get(
            "binding_effective_at"
        )
        if binding_effective_at not in (None, ""):
            metadata["runtime_binding_effective_at"] = str(binding_effective_at)
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


class LifecycleTelemetryOutboxError(RuntimeError):
    """Raised when canonical lifecycle durability cannot be guaranteed."""


class LifecycleTelemetryOutbox:
    """Crash-safe exact-payload outbox for canonical lifecycle telemetry."""

    _SCHEMA_VERSION = "paper_lifecycle_telemetry_outbox.v1"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._state = self._empty_state()
        self._load_error: str | None = None
        self._last_error: str | None = None
        self._load()

    @classmethod
    def _empty_state(cls) -> dict[str, Any]:
        return {
            "schema_version": cls._SCHEMA_VERSION,
            "next_admission_no": 1,
            "chains": {},
            "pending": [],
        }

    @staticmethod
    def _strict_copy(value: Any) -> Any:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            candidate = self._strict_copy(raw)
            self._validate_state(candidate)
            self._state = candidate
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            self._last_error = self._load_error

    @classmethod
    def _validate_state(cls, state: Mapping[str, Any]) -> None:
        if state.get("schema_version") != cls._SCHEMA_VERSION:
            raise ValueError("unsupported lifecycle telemetry outbox schema")
        next_admission_no = state.get("next_admission_no")
        if (
            isinstance(next_admission_no, bool)
            or not isinstance(next_admission_no, int)
            or next_admission_no < 1
        ):
            raise ValueError("lifecycle telemetry next_admission_no must be positive")
        chains = state.get("chains")
        pending = state.get("pending")
        if not isinstance(chains, dict) or not isinstance(pending, list):
            raise ValueError("lifecycle telemetry outbox chains/pending are malformed")
        checkpoint_envelopes: dict[str, dict[str, Any]] = {}
        for journey_id, checkpoint in chains.items():
            if not isinstance(journey_id, str) or not journey_id:
                raise ValueError("lifecycle telemetry chain journey_id is missing")
            if not isinstance(checkpoint, dict):
                raise ValueError("lifecycle telemetry chain checkpoint is malformed")
            sequence_no = checkpoint.get("sequence_no")
            if (
                isinstance(sequence_no, bool)
                or not isinstance(sequence_no, int)
                or sequence_no < 1
            ):
                raise ValueError("lifecycle telemetry chain sequence_no must be positive")
            event_id = str(checkpoint.get("event_id") or "")
            uuid.UUID(event_id)
            envelope = validate_envelope(checkpoint.get("correlation_envelope"))
            if envelope["journey_id"] != journey_id or envelope["event_id"] != event_id:
                raise ValueError("lifecycle telemetry chain envelope does not match checkpoint")
            checkpoint_envelopes[journey_id] = envelope

        seen_admissions: set[int] = set()
        seen_events: set[str] = set()
        last_pending_by_journey: dict[str, dict[str, Any]] = {}
        previous_admission = 0
        for record in pending:
            if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                raise ValueError("lifecycle telemetry pending record is malformed")
            admission_no = record.get("admission_no")
            if (
                isinstance(admission_no, bool)
                or not isinstance(admission_no, int)
                or admission_no < 1
                or admission_no in seen_admissions
                or admission_no <= previous_admission
            ):
                raise ValueError("lifecycle telemetry admission order is invalid")
            previous_admission = admission_no
            seen_admissions.add(admission_no)
            journey_id = str(record.get("journey_id") or "")
            event_id = str(record.get("event_id") or "")
            sequence_no = record.get("sequence_no")
            payload = record["payload"]
            if journey_id not in chains or event_id in seen_events:
                raise ValueError("lifecycle telemetry pending identity is invalid")
            uuid.UUID(event_id)
            if payload.get("event_id") != event_id:
                raise ValueError("lifecycle telemetry pending payload event_id mismatch")
            if (
                isinstance(sequence_no, bool)
                or not isinstance(sequence_no, int)
                or sequence_no < 1
            ):
                raise ValueError("lifecycle telemetry pending sequence_no must be positive")
            if payload.get("aggregate_type") != "trade_journey":
                raise ValueError("lifecycle telemetry pending aggregate_type mismatch")
            if payload.get("aggregate_id") != journey_id:
                raise ValueError("lifecycle telemetry pending aggregate_id mismatch")
            if payload.get("sequence_no") != sequence_no:
                raise ValueError("lifecycle telemetry pending payload sequence_no mismatch")
            causal_parent_id = str(payload.get("causal_parent_id") or "")
            if not causal_parent_id:
                raise ValueError("lifecycle telemetry pending causal_parent_id is missing")
            payload_envelope = validate_envelope(payload.get("correlation_envelope"))
            if (
                payload_envelope["event_id"] != event_id
                or payload_envelope["journey_id"] != journey_id
                or payload_envelope["causation_event_id"] != causal_parent_id
            ):
                raise ValueError(
                    "lifecycle telemetry pending envelope does not match payload identity"
                )
            prior = last_pending_by_journey.get(journey_id)
            if prior is not None and (
                sequence_no != int(prior["sequence_no"]) + 1
                or causal_parent_id != prior["event_id"]
            ):
                raise ValueError("lifecycle telemetry pending journey chain is not contiguous")
            last_pending_by_journey[journey_id] = {
                "sequence_no": sequence_no,
                "event_id": event_id,
                "correlation_envelope": payload_envelope,
            }
            seen_events.add(event_id)
        if seen_admissions and next_admission_no <= max(seen_admissions):
            raise ValueError("lifecycle telemetry next_admission_no is stale")
        for journey_id, latest in last_pending_by_journey.items():
            checkpoint = chains[journey_id]
            if (
                checkpoint["sequence_no"] != latest["sequence_no"]
                or checkpoint["event_id"] != latest["event_id"]
                or checkpoint_envelopes[journey_id]
                != latest["correlation_envelope"]
            ):
                raise ValueError(
                    "lifecycle telemetry pending payload does not match chain checkpoint"
                )

    def _require_healthy(self) -> None:
        if self._load_error:
            raise LifecycleTelemetryOutboxError(
                f"lifecycle telemetry outbox load failed: {self._load_error}"
            )

    def _write_state(self, state: Mapping[str, Any]) -> None:
        self._require_healthy()
        temporary: Path | None = None
        directory_fd: int | None = None
        try:
            serialized = json.dumps(state, sort_keys=True, allow_nan=False)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            directory_fd = os.open(
                self._path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            temporary = self._path.with_name(
                f"{self._path.name}.{uuid.uuid4().hex}.tmp"
            )
            with temporary.open("w", encoding="utf-8") as state_file:
                state_file.write(serialized)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self._path)
            os.fsync(directory_fd)
            self._last_error = None
        except (OSError, TypeError, ValueError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise LifecycleTelemetryOutboxError(
                f"lifecycle telemetry outbox persistence failed: {self._last_error}"
            ) from exc
        finally:
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

    def verify_writable(self) -> None:
        """Fail before queue consumption when the durable store is unavailable."""
        with self._lock:
            self._write_state(self._state)

    def chains(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._require_healthy()
            return self._strict_copy(self._state["chains"])

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            self._require_healthy()
            return self._strict_copy(self._state["pending"])

    def admit(
        self,
        *,
        payload: Mapping[str, Any],
        journey_id: str,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist an exact payload and its advanced chain checkpoint."""
        with self._lock:
            self._require_healthy()
            exact_payload = self._strict_copy(dict(payload))
            exact_checkpoint = self._strict_copy(dict(checkpoint))
            event_id = str(exact_payload.get("event_id") or "")
            sequence_no = exact_checkpoint.get("sequence_no")
            if not journey_id or exact_checkpoint.get("event_id") != event_id:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry admission identity is invalid"
                )
            uuid.UUID(event_id)
            envelope = validate_envelope(exact_checkpoint.get("correlation_envelope"))
            if envelope["journey_id"] != journey_id or envelope["event_id"] != event_id:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry admission envelope is invalid"
                )
            for record in self._state["pending"]:
                if record["event_id"] == event_id:
                    if (
                        record["journey_id"] != journey_id
                        or record["sequence_no"] != sequence_no
                        or record["payload"] != exact_payload
                    ):
                        raise LifecycleTelemetryOutboxError(
                            "lifecycle telemetry event_id payload conflict"
                        )
                    return self._strict_copy(record["payload"])
            current = self._state["chains"].get(journey_id)
            expected_sequence = 1 if current is None else int(current["sequence_no"]) + 1
            if sequence_no != expected_sequence:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry admission sequence is not contiguous"
                )

            candidate = self._strict_copy(self._state)
            admission_no = int(candidate["next_admission_no"])
            candidate["next_admission_no"] = admission_no + 1
            candidate["chains"][journey_id] = exact_checkpoint
            candidate["pending"].append(
                {
                    "admission_no": admission_no,
                    "journey_id": journey_id,
                    "event_id": event_id,
                    "sequence_no": sequence_no,
                    "payload": exact_payload,
                }
            )
            self._validate_state(candidate)
            self._write_state(candidate)
            self._state = candidate
            return self._strict_copy(exact_payload)

    def acknowledge(self, event_id: str) -> None:
        """Remove a pending record only after a positive remote acknowledgement."""
        with self._lock:
            self._require_healthy()
            candidate = self._strict_copy(self._state)
            remaining = [
                record
                for record in candidate["pending"]
                if record["event_id"] != event_id
            ]
            if len(remaining) == len(candidate["pending"]):
                return
            candidate["pending"] = remaining
            self._write_state(candidate)
            self._state = candidate

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = self._state["pending"]
            return {
                "schema_version": self._SCHEMA_VERSION,
                "path": str(self._path),
                "status": "degraded" if self._last_error else "ok",
                "pending_count": len(pending),
                "chain_count": len(self._state["chains"]),
                "oldest_admission_no": (
                    pending[0]["admission_no"] if pending else None
                ),
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
        lifecycle_outbox_path: str | os.PathLike[str] | None = None,
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
            runtime_binding_id=self._identity.binding_id,
        )
        self._telemetry = telemetry_emitter or RuntimeTelemetryEmitter(
            self._identity,
            self._binding_resolver,
            runtime_context=runtime_context,
        )
        self._legacy_journey_publish_enabled = _as_bool(
            os.getenv("PANTHEON_LEGACY_JOURNEY_BFF_PUBLISH_ENABLED"),
            default=False,
        )
        performance_state_path = os.getenv("PANTHEON_PERFORMANCE_STATE_PATH") or None
        lifecycle_scope = "".join(
            character
            if character.isalnum() or character in {"-", "_", "."}
            else "_"
            for character in (
                self._identity.runtime_id
                or self._identity.binding_id
                or "paper-runtime"
            )
        )
        configured_lifecycle_path = (
            str(lifecycle_outbox_path)
            if lifecycle_outbox_path is not None
            else os.getenv("PANTHEON_LIFECYCLE_OUTBOX_PATH")
        )
        if configured_lifecycle_path:
            resolved_lifecycle_path = configured_lifecycle_path
        elif performance_state_path:
            ledger_path = Path(performance_state_path)
            resolved_lifecycle_path = str(
                ledger_path.with_name(f"{ledger_path.name}.lifecycle-outbox.json")
            )
        else:
            resolved_lifecycle_path = str(
                Path("/data/runtime/lifecycle-outbox")
                / f"{lifecycle_scope}.json"
            )
        self._lifecycle_outbox = LifecycleTelemetryOutbox(resolved_lifecycle_path)
        try:
            self._lifecycle_chains = self._lifecycle_outbox.chains()
            lifecycle_load_error = None
        except LifecycleTelemetryOutboxError as exc:
            self._lifecycle_chains = {}
            lifecycle_load_error = str(exc)
        self._blocked_lifecycle_chains: set[str] = set()
        self._lifecycle_outbox_verified = False
        self._lifecycle_outbox_persistence_error = lifecycle_load_error
        self._lifecycle_delivery_error: str | None = None
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
            state_path=performance_state_path,
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
        if self._legacy_journey_publish_enabled and outbox_dir:
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
        # Lifecycle replay precedes all new runtime activity. Remote delivery
        # failure keeps exact payloads pending but does not halt paper execution.
        self._flush_lifecycle_outbox()
        self._emit_deploy_started()

        if self._legacy_journey_publish_enabled:
            # Temporary compatibility publisher. Canonical live journey writes
            # flow through telemetry and the lifecycle projector by default.
            outbox_dir = os.path.dirname(self._outbox_path)
            if outbox_dir:
                try:
                    os.makedirs(outbox_dir, exist_ok=True)
                except Exception as exc:
                    log.warning("Failed to create outbox directory %s: %s", outbox_dir, exc)

            self._outbox_thread = threading.Thread(
                target=self._outbox_loop,
                daemon=True,
                name="paper-runtime-legacy-journey-outbox",
            )
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
            ledger_binding_failed = False
            try:
                self._ensure_lifecycle_outbox_ready()
                self._flush_lifecycle_outbox()
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
                    ledger_binding_failed = True
                    self._performance_telemetry = {
                        "status": "invalid_ledger",
                        "code": "performance_ledger_load_failed",
                        "attempted_at": _iso_now(),
                        "state_path": ledger.get("state_path"),
                        "detail": (
                            ledger.get("state_load_error")
                            or ledger.get("state_binding_error")
                            or ledger.get("state_error")
                            or "paper performance ledger binding failed"
                        ),
                    }
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
            elif ledger_binding_failed:
                # Execution remains blocked, but surface the durable-ledger
                # failure through the normal heartbeat diagnostics path.
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
            lifecycle_outbox = self._lifecycle_outbox.snapshot()
            lifecycle_outbox.update(
                {
                    "persistence_error": self._lifecycle_outbox_persistence_error,
                    "delivery_error": self._lifecycle_delivery_error,
                }
            )
            return {
                **self._identity.to_health_payload(),
                "status": (
                    "ok"
                    if self._last_error is None
                    and self._lifecycle_outbox_persistence_error is None
                    else "degraded"
                ),
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
                "lifecycle_outbox": lifecycle_outbox,
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

    @staticmethod
    def _signal_lifecycle_metadata(signal: Mapping[str, Any]) -> dict[str, Any]:
        nested = signal.get("metadata")
        metadata = dict(nested) if isinstance(nested, Mapping) else {}
        for field in (
            "signal_id",
            "strategy_id",
            "run_id",
            "binding_id",
            "runtime_id",
            "source_worker",
            "tenant_id",
            "environment",
            "journey_id",
            "decision_id",
            "correlation_envelope",
        ):
            value = signal.get(field)
            if value not in (None, "", [], {}):
                metadata[field] = value
        return metadata

    @staticmethod
    def _canonical_lifecycle_identity(
        metadata: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        raw_envelope = metadata.get("correlation_envelope")
        run_id = str(metadata.get("run_id") or "").strip()
        signal_id = str(metadata.get("signal_id") or "").strip()
        if not isinstance(raw_envelope, Mapping) or not run_id or not signal_id:
            return None
        try:
            envelope = validate_envelope(raw_envelope)
        except CorrelationEnvelopeError as exc:
            log.warning("Skipping non-canonical lifecycle telemetry: %s", exc)
            return None
        normalized = dict(metadata)
        normalized["correlation_envelope"] = envelope
        normalized["run_id"] = run_id
        normalized["signal_id"] = signal_id
        normalized["journey_id"] = envelope["journey_id"]
        normalized.setdefault("tenant_id", envelope["tenant_id"])
        normalized.setdefault("environment", envelope["environment"])
        normalized.setdefault("loop_run_id", f"lr-{run_id}")
        return normalized, envelope

    def _record_lifecycle_persistence_failure(
        self,
        exc: BaseException,
        *,
        journey_id: str | None = None,
    ) -> None:
        self._lifecycle_outbox_persistence_error = f"{type(exc).__name__}: {exc}"
        self._lifecycle_outbox_verified = False
        if journey_id:
            self._blocked_lifecycle_chains.add(journey_id)

    def _ensure_lifecycle_outbox_ready(self) -> None:
        if self._lifecycle_outbox_verified:
            return
        try:
            self._lifecycle_outbox.verify_writable()
        except LifecycleTelemetryOutboxError as exc:
            self._record_lifecycle_persistence_failure(exc)
            raise
        self._lifecycle_outbox_verified = True
        self._lifecycle_outbox_persistence_error = None
        self._blocked_lifecycle_chains.clear()

    def _flush_lifecycle_outbox(self) -> bool:
        """Replay exact pending payloads in durable admission order."""
        try:
            pending = self._lifecycle_outbox.pending()
        except LifecycleTelemetryOutboxError as exc:
            self._record_lifecycle_persistence_failure(exc)
            return False
        if not pending:
            self._lifecycle_delivery_error = None
            return True
        sender = getattr(self._telemetry, "emit_payload", None)
        if not callable(sender):
            self._lifecycle_delivery_error = (
                "canonical lifecycle telemetry emitter lacks exact-payload delivery"
            )
            return False
        for record in pending:
            payload = record["payload"]
            try:
                acknowledged = bool(sender(payload))
            except Exception as exc:  # noqa: BLE001
                self._lifecycle_delivery_error = f"{type(exc).__name__}: {exc}"
                return False
            if not acknowledged:
                self._lifecycle_delivery_error = (
                    f"remote acknowledgement missing for lifecycle event "
                    f"{record['event_id']}"
                )
                return False
            try:
                self._lifecycle_outbox.acknowledge(record["event_id"])
            except LifecycleTelemetryOutboxError as exc:
                # The exact payload remains pending on disk. A subsequent replay
                # may duplicate a remotely accepted event; ingest idempotency
                # collapses it by event_id.
                self._record_lifecycle_persistence_failure(
                    exc,
                    journey_id=str(record["journey_id"]),
                )
                return False
        self._lifecycle_delivery_error = None
        return True

    def _emit_lifecycle_telemetry(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: Mapping[str, Any],
        *,
        event_id: str,
        created_at: str,
    ) -> dict[str, Any] | None:
        canonical = self._canonical_lifecycle_identity(metadata)
        if canonical is None:
            return None
        lifecycle_metadata, incoming_envelope = canonical
        journey_id = str(incoming_envelope["journey_id"])
        chain = self._lifecycle_chains.get(journey_id)
        if chain is None:
            sequence_no = 1
            causal_parent_id = str(incoming_envelope["event_id"])
        else:
            sequence_no = int(chain["sequence_no"]) + 1
            causal_parent_id = str(chain["event_id"])
            previous_envelope = chain.get("correlation_envelope")
            if isinstance(previous_envelope, Mapping):
                lifecycle_metadata["correlation_envelope"] = dict(previous_envelope)

        lifecycle_metadata["sequence_no"] = sequence_no
        lifecycle_metadata["causal_parent_id"] = causal_parent_id
        lifecycle_metadata["source_mode"] = "live"

        builder = getattr(self._telemetry, "build_event", None)
        sender = getattr(self._telemetry, "emit_payload", None)
        if not callable(builder) or not callable(sender):
            exc = LifecycleTelemetryOutboxError(
                "canonical lifecycle telemetry requires exact-payload build/delivery"
            )
            self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            raise exc
        payload = builder(
            event_type,
            metrics,
            metadata=lifecycle_metadata,
            event_id=event_id,
            created_at=created_at,
        )
        if payload is None:
            exc = LifecycleTelemetryOutboxError(
                f"canonical lifecycle payload build failed for {event_type}"
            )
            self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            raise exc

        outgoing_envelope = payload.get("correlation_envelope")
        if not isinstance(outgoing_envelope, Mapping):
            outgoing_envelope = propagate_envelope(
                lifecycle_metadata["correlation_envelope"],
                producer="execution.paper_runtime",
                event_id=event_id,
                event_time=created_at,
            )
        checkpoint = {
            "sequence_no": sequence_no,
            "event_id": event_id,
            "correlation_envelope": dict(outgoing_envelope),
        }
        try:
            had_pending = bool(self._lifecycle_outbox.pending())
            exact_payload = self._lifecycle_outbox.admit(
                payload=payload,
                journey_id=journey_id,
                checkpoint=checkpoint,
            )
        except (LifecycleTelemetryOutboxError, CorrelationEnvelopeError) as exc:
            self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            if isinstance(exc, LifecycleTelemetryOutboxError):
                raise
            raise LifecycleTelemetryOutboxError(str(exc)) from exc

        # A canonical payload is causally usable after local admission, not
        # after remote acknowledgement. This lets later fill/position payloads
        # join the same durable chain during an ingest outage.
        self._lifecycle_chains[journey_id] = checkpoint
        self._lifecycle_outbox_verified = True
        self._lifecycle_outbox_persistence_error = None
        self._blocked_lifecycle_chains.discard(journey_id)
        if not had_pending:
            self._flush_lifecycle_outbox()
        return exact_payload

    def _handle_order_event(self, event: OrderEvent) -> None:
        if event.event_type == "signal_generation":
            signal_metadata = self._signal_lifecycle_metadata(event.metadata)
            signal_metadata.setdefault("symbol", event.symbol)
            signal_metadata.setdefault("order_type", event.metadata.get("order_type", "MARKET"))
            occurred_at = str(event.metadata.get("timestamp") or event.created_at)
            try:
                signal_payload = self._emit_lifecycle_telemetry(
                    "signal_generation",
                    {
                        "action": "signal_generated",
                        "signal_quantity": event.quantity,
                    },
                    signal_metadata,
                    event_id=event.event_id,
                    created_at=occurred_at,
                )
            except LifecycleTelemetryOutboxError:
                # Pending stores remove on claim. Put the exact signal back when
                # local admission fails so execution cannot continue without a
                # durable lifecycle record.
                enqueue = getattr(self._store, "enqueue", None)
                if callable(enqueue):
                    try:
                        enqueue(dict(event.metadata))
                    except Exception as enqueue_exc:  # noqa: BLE001
                        log.exception(
                            "failed to requeue signal after lifecycle admission failure: %s",
                            enqueue_exc,
                        )
                raise
            if signal_payload is not None:
                decision_event_id = str(
                    uuid.uuid5(
                        _LIFECYCLE_UUID_NAMESPACE,
                        f"{event.event_id}:trade_decision",
                    )
                )
                self._emit_lifecycle_telemetry(
                    "trade_decision",
                    {"action": "trade_decision_recorded"},
                    signal_metadata,
                    event_id=decision_event_id,
                    created_at=occurred_at,
                )
            self._publish_legacy_journey_events(event)
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
        lifecycle_payload = None
        canonical_lifecycle = (
            event.event_type in _CANONICAL_LIFECYCLE_EVENT_TYPES
            and self._canonical_lifecycle_identity(telemetry_metadata) is not None
        )
        committed_fill = (
            event.event_type == "paper_fill_simulated"
            and event.metadata.get("ledger_committed") is True
        )
        if committed_fill and canonical_lifecycle:
            risk_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:risk_evaluation",
                )
            )
            risk_metadata = dict(telemetry_metadata)
            risk_scope = (
                _clean_text(telemetry_metadata.get("signal_id")) or event.event_id
            )
            risk_metadata.setdefault("risk_decision_id", f"paper-risk-{risk_scope}")
            risk_metadata.setdefault("risk_status", "succeeded")
            self._emit_lifecycle_telemetry(
                "risk_evaluation",
                {
                    "action": "paper_risk_accepted",
                    "risk_quantity": abs(event.quantity),
                    "risk_price": event.fill_price,
                    "submitted_to_broker": False,
                },
                risk_metadata,
                event_id=risk_event_id,
                created_at=event.created_at,
            )
            order_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:order_submitted",
                )
            )
            telemetry_metadata.setdefault("order_id", f"paper-order-{order_event_id}")
            telemetry_metadata.setdefault("fill_id", f"paper-fill-{event.event_id}")
            self._emit_lifecycle_telemetry(
                "order_submitted",
                {
                    "action": "paper_order_submitted",
                    "order_quantity": abs(event.quantity),
                    "order_price": event.fill_price,
                },
                telemetry_metadata,
                event_id=order_event_id,
                created_at=event.created_at,
            )
            accepted_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:order_accepted",
                )
            )
            accepted_metadata = dict(telemetry_metadata)
            accepted_metadata.setdefault(
                "broker_order_id", f"paper-accepted-{order_event_id}"
            )
            accepted_metadata["broker_submission_status"] = "simulated_accepted"
            self._emit_lifecycle_telemetry(
                "order_accepted",
                {
                    "action": "paper_order_accepted",
                    "order_quantity": abs(event.quantity),
                    "order_price": event.fill_price,
                    "submitted_to_broker": False,
                },
                accepted_metadata,
                event_id=accepted_event_id,
                created_at=event.created_at,
            )
        if canonical_lifecycle and (
            event.event_type != "paper_fill_simulated" or committed_fill
        ):
            lifecycle_payload = self._emit_lifecycle_telemetry(
                event.event_type,
                metrics,
                telemetry_metadata,
                event_id=event.event_id,
                created_at=event.created_at,
            )
        if lifecycle_payload is None and not canonical_lifecycle:
            self._telemetry.emit(event.event_type, metrics, metadata=telemetry_metadata)

        if (
            committed_fill
            and lifecycle_payload is not None
        ):
            fill_count = int(event.metadata.get("ledger_fill_count") or 0)
            position_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:position_snapshot",
                )
            )
            position_metadata = dict(telemetry_metadata)
            position_metadata.update(
                {
                    "ledger_committed": True,
                    "ledger_fill_count": fill_count,
                    "source_fill_event_id": event.event_id,
                    "position_id": (
                        f"{self._identity.runtime_id or 'paper-runtime'}:"
                        f"{event.symbol}:{fill_count}"
                    ),
                }
            )
            position_quantity = float(self._algo._holding(event.symbol).Quantity)
            position_price = float(self._algo._security(event.symbol).Price)
            self._emit_lifecycle_telemetry(
                "position_snapshot",
                {
                    "action": "position_snapshot_committed",
                    "position_qty": position_quantity,
                    "price": position_price,
                    "cash": float(self._algo._cash),
                },
                position_metadata,
                event_id=position_event_id,
                created_at=_iso_now(),
            )

        self._publish_legacy_journey_events(event)

    def _publish_legacy_journey_events(self, event: OrderEvent) -> None:
        """Opt-in compatibility writer; canonical telemetry remains sole default."""
        if not self._legacy_journey_publish_enabled:
            return
        metadata = self._signal_lifecycle_metadata(event.metadata)
        envelope = metadata.get("correlation_envelope")
        envelope = dict(envelope) if isinstance(envelope, Mapping) else {}
        signal_id = metadata.get("signal_id")
        binding = self._binding_resolver.resolve() or {}
        tenant_id = metadata.get("tenant_id") or envelope.get("tenant_id") or binding.get("tenant_id") or "default"
        environment = metadata.get("environment") or envelope.get("environment") or binding.get("deployment_stage") or "paper"
        journey_id = metadata.get("journey_id") or envelope.get("journey_id")
        if not journey_id:
            journey_id = f"tj-{signal_id}" if signal_id else f"tj-evt-{event.event_id}"
        common = {
            "journey_id": journey_id,
            "tenant_id": tenant_id,
            "environment": environment,
            "occurred_at": event.created_at,
            "recorded_at": _iso_now(),
            "source": "runtime_legacy_direct",
            "signal_id": signal_id,
            "symbol": event.symbol,
            "run_id": metadata.get("run_id"),
            "correlation_envelope": envelope,
        }
        specs: list[tuple[str, str, int]] = []
        if event.event_type == "signal_generation":
            specs = [("signal_generation", "succeeded", 1)]
            common["occurred_at"] = event.metadata.get("timestamp") or event.created_at
        elif event.event_type in {"paper_fill_simulated", "paper_order_simulated", "order_rejection"}:
            specs.append(("trade_decision", metadata.get("decision_status") or "succeeded", 2))
            order_status = "succeeded"
            if event.event_type == "paper_order_simulated":
                order_status = "noop"
            elif event.event_type == "order_rejection":
                order_status = "rejected"
            specs.append(("order_submission", order_status, 3))
            if event.event_type in {"paper_fill_simulated", "order_rejection"}:
                specs.append(
                    (
                        "fill_management",
                        "succeeded" if event.event_type == "paper_fill_simulated" else "failed",
                        4,
                    )
                )
        journey_events = []
        for stage, status, sequence in specs:
            item = {
                **common,
                "event_id": f"{event.event_id}:{stage}",
                "stage": stage,
                "stage_status": status,
                "sequence": sequence,
            }
            if stage == "fill_management" and event.event_type == "paper_fill_simulated":
                item.update(
                    quantity=abs(event.quantity),
                    price=event.fill_price,
                    side="sell" if event.quantity < 0 else "buy",
                )
            journey_events.append(item)
        self._publish_journey_events(journey_events)

    def _publish_journey_events(self, events: list[dict[str, Any]]) -> None:
        if not self._legacy_journey_publish_enabled or not events:
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
                        f.write(json.dumps(event, allow_nan=False) + "\n")
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
                                        f.write(json.dumps(e, allow_nan=False) + "\n")
                                os.replace(tmp_path, self._outbox_path)
                            else:
                                if os.path.exists(self._outbox_path):
                                    os.remove(self._outbox_path)
                        except Exception as exc:
                            log.error("Failed to update outbox file: %s", exc)
            else:
                self._shutdown.wait(timeout=2.0)

    def _send_to_bff(self, events: list[dict[str, Any]]) -> bool:
        if not self._legacy_journey_publish_enabled:
            return False
        bff_url = os.getenv("PANTHEON_BFF_URL", "http://operator-bff:8080").strip().rstrip("/")
        url = f"{bff_url}/bff/management/trade-journeys/events"
        body = json.dumps(events, allow_nan=False).encode("utf-8")

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

        if self._algo.pending_performance_pair() is not None:
            self._flush_pending_performance_pair()
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
            "runtime_binding_id": ledger.get("binding_id"),
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
        pair_id = str(uuid.uuid4())
        staged_at = _iso_now()
        pnl_metadata = {
            **metadata,
            "performance_pair_id": pair_id,
            "performance_pair_leg": "pnl_snapshot",
        }
        drawdown_metadata = {
            **metadata,
            "performance_pair_id": pair_id,
            "performance_pair_leg": "drawdown_snapshot",
        }
        pnl_payload = self._build_performance_event_payload(
            "pnl_snapshot",
            {
                "pnl": sample.pnl,
                **common_metrics,
                "pnl_as_of": sample.as_of,
            },
            pnl_metadata,
            event_id=str(uuid.uuid4()),
            created_at=staged_at,
        )
        drawdown_payload = self._build_performance_event_payload(
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
            drawdown_metadata,
            event_id=str(uuid.uuid4()),
            created_at=staged_at,
        )
        if pnl_payload is None or drawdown_payload is None:
            self._drawdown_tracker.restore(tracker_checkpoint)
            self._performance_telemetry.update(
                {
                    "status": "emit_failed",
                    "code": "performance_snapshot_build_failed",
                    "detail": getattr(self._telemetry, "snapshot", lambda: {})().get(
                        "last_error"
                    ),
                }
            )
            return
        pair = {
            "schema_version": "paper_performance_pair.v1",
            "pair_id": pair_id,
            "binding_id": str(ledger.get("binding_id") or ""),
            "valuation_as_of": sample.as_of,
            "staged_at": staged_at,
            "next_window": self._drawdown_tracker.export_state(),
            "events": {
                "pnl_snapshot": {"payload": pnl_payload, "acked": False},
                "drawdown_snapshot": {"payload": drawdown_payload, "acked": False},
            },
        }
        if not self._algo.stage_performance_pair(pair):
            self._drawdown_tracker.restore(tracker_checkpoint)
            self._performance_telemetry.update(
                {
                    "status": "state_persist_failed",
                    "code": "performance_pair_stage_failed",
                    "detail": self._algo.performance_ledger().get("state_error"),
                }
            )
            return
        # The staged next window is not committed until both immutable event
        # legs have been acknowledged. A crash resumes from the pending pair.
        self._drawdown_tracker.restore(tracker_checkpoint)
        self._flush_pending_performance_pair()

    def _build_performance_event_payload(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any],
        *,
        event_id: str,
        created_at: str,
    ) -> dict[str, Any] | None:
        builder = getattr(self._telemetry, "build_event", None)
        if callable(builder):
            return builder(
                event_type,
                metrics,
                metadata,
                event_id=event_id,
                created_at=created_at,
            )
        return None

    def _emit_staged_performance_payload(self, payload: Mapping[str, Any]) -> bool:
        sender = getattr(self._telemetry, "emit_payload", None)
        if callable(sender):
            return bool(sender(payload))
        return False

    def _flush_pending_performance_pair(self) -> bool:
        pending = self._algo.pending_performance_pair()
        if pending is None:
            return True
        pair_id = str(pending["pair_id"])
        events = pending["events"]
        for event_type in ("pnl_snapshot", "drawdown_snapshot"):
            leg = events[event_type]
            if leg["acked"]:
                continue
            if not self._emit_staged_performance_payload(leg["payload"]):
                self._performance_telemetry = {
                    "status": "emit_failed",
                    "code": "performance_snapshot_emit_failed",
                    "attempted_at": _iso_now(),
                    "pair_id": pair_id,
                    "failed_leg": event_type,
                    "as_of": pending["valuation_as_of"],
                    "pnl_snapshot_sent": bool(events["pnl_snapshot"]["acked"]),
                    "drawdown_snapshot_sent": bool(
                        events["drawdown_snapshot"]["acked"]
                    ),
                }
                return False
            if not self._algo.ack_performance_pair_leg(pair_id, event_type):
                self._performance_telemetry = {
                    "status": "state_persist_failed",
                    "code": "performance_pair_ack_persist_failed",
                    "attempted_at": _iso_now(),
                    "pair_id": pair_id,
                    "failed_leg": event_type,
                    "detail": self._algo.performance_ledger().get("state_error"),
                }
                return False
            # Refresh the durable ack state before deciding whether to send the
            # next leg. This makes a process crash resume at the exact boundary.
            pending = self._algo.pending_performance_pair()
            assert pending is not None
            events = pending["events"]

        next_window = pending["next_window"]
        pnl = float(events["pnl_snapshot"]["payload"]["metrics"]["pnl"])
        drawdown = float(
            events["drawdown_snapshot"]["payload"]["metrics"]["drawdown_pct"]
        )
        if not self._algo.finalize_performance_pair(pair_id):
            self._performance_telemetry = {
                "status": "state_persist_failed",
                "code": "performance_pair_finalize_failed",
                "attempted_at": _iso_now(),
                "pair_id": pair_id,
                "detail": self._algo.performance_ledger().get("state_error"),
            }
            return False
        try:
            self._drawdown_tracker.restore(next_window)
        except ValueError as exc:
            self._performance_state_restore_error = f"{type(exc).__name__}: {exc}"
            self._performance_telemetry = {
                "status": "invalid_drawdown_series",
                "code": "performance_window_restore_failed",
                "attempted_at": _iso_now(),
                "pair_id": pair_id,
                "detail": self._performance_state_restore_error,
            }
            return False
        self._performance_telemetry = {
            "status": "emitted",
            "code": "performance_snapshots_emitted",
            "attempted_at": _iso_now(),
            "pair_id": pair_id,
            "as_of": pending["valuation_as_of"],
            "pnl": pnl,
            "drawdown_pct": drawdown,
            "pnl_snapshot_sent": True,
            "drawdown_snapshot_sent": True,
        }
        return True

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
