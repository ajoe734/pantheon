from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from AlgorithmImports import QCAlgorithm, Resolution  # type: ignore[import]

    _LEAN_AVAILABLE = True
except ImportError:
    _LEAN_AVAILABLE = False

    class _Holding:
        def __init__(self, quantity: float = 0.0) -> None:
            self.Quantity = quantity

    class _Security:
        def __init__(self, symbol: str, price: float = 100.0) -> None:
            self.Symbol = symbol
            self.Price = price

    class _AddedSecurity:
        def __init__(self, symbol: str) -> None:
            self.Symbol = symbol

    class Resolution:
        Daily = "Daily"

    class QCAlgorithm:
        def __init__(self) -> None:
            self.Portfolio: dict[str, _Holding] = {}
            self.Securities: dict[str, _Security] = {}
            self._smoke_fill_events: list[dict[str, Any]] = []
            self._cash = 100000.0

        def SetStartDate(self, *_args: Any) -> None:  # noqa: N802
            return None

        def SetEndDate(self, *_args: Any) -> None:  # noqa: N802
            return None

        def SetCash(self, cash: float) -> None:  # noqa: N802
            self._cash = float(cash)

        def AddEquity(self, ticker: str, _resolution: Any = None) -> _AddedSecurity:  # noqa: N802
            self.Portfolio.setdefault(ticker, _Holding())
            self.Securities.setdefault(ticker, _Security(ticker))
            return _AddedSecurity(ticker)

        def MarketOrder(self, symbol: str, quantity: float) -> None:  # noqa: N802
            self._record_smoke_fill(symbol, quantity, "market_order")

        def LimitOrder(self, symbol: str, quantity: float, limit_price: float) -> None:  # noqa: N802
            self.Securities.setdefault(str(symbol), _Security(str(symbol))).Price = float(limit_price)
            self._record_smoke_fill(symbol, quantity, "limit_order")

        def SetHoldings(self, symbol: str, target_percent: float) -> None:  # noqa: N802
            security = self.Securities.setdefault(str(symbol), _Security(str(symbol)))
            holding = self.Portfolio.setdefault(str(symbol), _Holding())
            target_quantity = (self._cash * float(target_percent)) / max(float(security.Price), 0.01)
            self._record_smoke_fill(symbol, target_quantity - holding.Quantity, "set_holdings")

        def Liquidate(self, symbol: str) -> None:  # noqa: N802
            holding = self.Portfolio.setdefault(str(symbol), _Holding())
            self._record_smoke_fill(symbol, -holding.Quantity, "liquidate")

        def Debug(self, _message: str) -> None:  # noqa: N802
            return None

        def Log(self, _message: str) -> None:  # noqa: N802
            return None

        def Quit(self, _message: str = "") -> None:  # noqa: N802
            self._quit_message = _message

        def _record_smoke_fill(self, symbol: Any, quantity: float, action: str) -> None:
            symbol_key = str(symbol)
            security = self.Securities.setdefault(symbol_key, _Security(symbol_key))
            holding = self.Portfolio.setdefault(symbol_key, _Holding())
            quantity = float(quantity)
            holding.Quantity += quantity
            self._cash -= quantity * float(security.Price)
            active_signal = getattr(self, "_active_smoke_signal", {}) or {}
            self._smoke_fill_events.append(
                {
                    "symbol": symbol_key,
                    "quantity": quantity,
                    "fill_price": float(security.Price),
                    "action": action,
                    "signal_id": active_signal.get("signal_id"),
                    "submitted_to_broker": False,
                    "created_at": _iso_now(),
                }
            )


from services.execution.artifact_loader import ArtifactLoader, ExecutionMode
from services.execution.lean_runtime.executor import execute
from services.execution.lean_runtime.runtime_context import PantheonRuntimeContext


class PantheonSmokeLoaderAlgorithm(QCAlgorithm):
    """Minimal LEAN Python algorithm that loads and executes one approved artifact."""

    def Initialize(self) -> None:
        self._ensure_smoke_state()
        if _truthy(os.getenv("BROKER_PRODUCTION_LIVE_ENABLED")):
            raise RuntimeError("LEAN smoke must not enable production broker access")

        self.SetStartDate(2026, 1, 5)
        self.SetEndDate(2026, 1, 9)
        self.SetCash(100000)

        strategy_id = _env_required("PANTHEON_STRATEGY_ID")
        artifact_version = _env_required("PANTHEON_ARTIFACT_VERSION")
        deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "paper").strip().lower()
        if deployment_stage != "paper":
            raise RuntimeError(f"LEAN smoke requires deployment_stage='paper', got {deployment_stage!r}")

        self._runtime_context = PantheonRuntimeContext.from_env(
            os.environ,
            expected_stage="paper",
            managed_runtime=True,
        )
        self._loaded_artifact = ArtifactLoader.from_runtime(self).load(
            strategy_id=strategy_id,
            version=artifact_version,
            execution_mode=ExecutionMode.PAPER,
        )
        payload = json.loads(self._loaded_artifact.payload.decode("utf-8"))
        signal = payload.get("signal")
        if not isinstance(signal, Mapping):
            raise RuntimeError("LEAN smoke artifact payload must contain a signal object")
        self._loaded_signal = dict(signal)

        ticker = str(self._loaded_signal["symbol"]).split(".", 1)[0]
        self._smoke_ticker = ticker
        added_security = self.AddEquity(ticker, Resolution.Daily)
        self._smoke_symbol = getattr(added_security, "Symbol", ticker)

    def OnData(self, data: Any) -> None:
        self._ensure_smoke_state()
        self._raw_on_data_callbacks += 1
        if self._signal_executed:
            return
        if not _contains_symbol(data, self._smoke_symbol, self._smoke_ticker):
            return

        self._update_smoke_price(data)
        self._executed_on_data_callbacks += 1
        self._active_smoke_signal = dict(self._loaded_signal)
        execute(dict(self._loaded_signal), self)
        self._signal_executed = True

    def OnOrderEvent(self, order_event: Any) -> None:
        self._ensure_smoke_state()
        signal = getattr(self, "_active_smoke_signal", {}) or {}
        self._smoke_fill_events.append(
            {
                "symbol": str(getattr(order_event, "Symbol", self._smoke_symbol)),
                "quantity": float(getattr(order_event, "FillQuantity", 0) or 0),
                "fill_price": float(getattr(order_event, "FillPrice", 0) or 0),
                "action": "order_event",
                "signal_id": signal.get("signal_id"),
                "submitted_to_broker": False,
                "created_at": _iso_now(),
            }
        )

    def get_smoke_observations(self) -> dict[str, Any]:
        self._ensure_smoke_state()
        context = self._runtime_context.to_dict()
        return {
            "raw_on_data_callbacks": self._raw_on_data_callbacks,
            "executed_on_data_callbacks": self._executed_on_data_callbacks,
            "fill_events": list(getattr(self, "_smoke_fill_events", [])),
            "loaded_metadata": dict(self._loaded_artifact.metadata),
            "loaded_signal": dict(self._loaded_signal),
            "runtime_context": {
                "runtime_binding_id": context["runtime_binding_id"],
                "runtime_id": context["runtime_id"],
                "deployment_plan_id": context["deployment_plan_id"],
                "deployment_stage": context["deployment_stage"],
                "artifact_id": context["artifact"]["artifact_id"],
                "artifact_version": context["artifact"]["artifact_version"],
                "strategy_id": context["artifact"]["strategy_id"],
                "capital_pool_id": context["capital"]["capital_pool_id"],
            },
        }

    def _ensure_smoke_state(self) -> None:
        if not hasattr(self, "_raw_on_data_callbacks"):
            self._raw_on_data_callbacks = 0
        if not hasattr(self, "_executed_on_data_callbacks"):
            self._executed_on_data_callbacks = 0
        if not hasattr(self, "_signal_executed"):
            self._signal_executed = False
        if not hasattr(self, "_smoke_fill_events"):
            self._smoke_fill_events = []

    def _update_smoke_price(self, data: Any) -> None:
        close = _extract_close(data, self._smoke_symbol, self._smoke_ticker)
        if close is None or _LEAN_AVAILABLE:
            return
        security = self.Securities.setdefault(str(self._smoke_symbol), _Security(str(self._smoke_symbol)))
        security.Price = close


def _contains_symbol(data: Any, symbol: Any, ticker: str) -> bool:
    candidates = (symbol, str(symbol), ticker)
    contains = getattr(data, "ContainsKey", None)
    if callable(contains):
        for candidate in candidates:
            try:
                if contains(candidate):
                    return True
            except Exception:
                continue
    if isinstance(data, Mapping):
        return any(candidate in data for candidate in candidates)
    bars = getattr(data, "Bars", None)
    if isinstance(bars, Mapping):
        return any(candidate in bars for candidate in candidates)
    return False


def _extract_close(data: Any, symbol: Any, ticker: str) -> float | None:
    bar = None
    if isinstance(data, Mapping):
        for candidate in (symbol, str(symbol), ticker):
            if candidate in data:
                bar = data[candidate]
                break
    if bar is None:
        bars = getattr(data, "Bars", None)
        if isinstance(bars, Mapping):
            for candidate in (symbol, str(symbol), ticker):
                if candidate in bars:
                    bar = bars[candidate]
                    break
    if bar is None:
        return None
    value = getattr(bar, "Close", None)
    if value is None and isinstance(bar, Mapping):
        value = bar.get("close")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_required(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(f"{key} is required for LEAN smoke algorithm")
    return value


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
