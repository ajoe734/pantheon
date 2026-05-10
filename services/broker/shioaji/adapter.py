"""Shioaji TW broker adapter — sandbox-only, fail-closed gating.

Gate: BROKER_SHIOAJI_SANDBOX_ENABLED=true (default: false, fail-closed).
Live orders are permanently rejected regardless of gate state.

Key invariants:
- sandbox_enabled defaults to False; must be explicitly opted-in
- is_real_order is always False
- is_real_capital is always False
- deployment_stage is always "sandbox" when gate is open
- Live order requests always raise ShioajiBrokerError(SHIOAJI_LIVE_DISABLED)
- SDK import is lazy; ImportError falls back to string constants for mock/test
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import os
import threading
import uuid
from typing import Any, Dict, Optional

_ERR_SANDBOX_DISABLED = "SHIOAJI_SANDBOX_DISABLED"
_ERR_LIVE_DISABLED = "SHIOAJI_LIVE_DISABLED"
_ERR_ORDER_NOT_FOUND = "SHIOAJI_ORDER_NOT_FOUND"
_ERR_CANCEL_FAILED = "SHIOAJI_CANCEL_FAILED"
_ERR_SDK_MISSING = "SHIOAJI_SDK_MISSING"
_ERR_CREDENTIALS_MISSING = "SHIOAJI_CREDENTIALS_MISSING"
_ERR_SUBMIT_FAILED = "SHIOAJI_SUBMIT_FAILED"


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ShioajiBrokerError(Exception):
    def __init__(self, error_code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code

    def to_payload(self) -> Dict[str, Any]:
        return {
            "status": "broker_error",
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclasses.dataclass
class ShioajiOrder:
    """Order record — same shape as PaperOrder plus shioaji_trade_id."""

    order_id: str
    capital_pool_id: str
    strategy_id: str
    symbol: str
    qty: float
    side: str
    order_type: str
    limit_price: Optional[float]
    created_at: str
    filled_at: Optional[str]
    fill_price: Optional[float]
    fill_qty: float
    status: str
    sim_fill_flag: bool = True
    is_real_order: bool = False
    is_real_capital: bool = False
    deployment_stage: str = "sandbox"
    reject_reason: Optional[str] = None
    shioaji_trade_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class ShioajiBrokerAdapter:
    """Shioaji TW broker adapter.

    Operates exclusively in sandbox (simulation) mode when enabled.
    Live orders are never accepted; this is enforced unconditionally.

    Args:
        sandbox_enabled: Override the BROKER_SHIOAJI_SANDBOX_ENABLED env gate.
            Pass True in tests to avoid reading the environment.
        _api: Inject a pre-initialized Shioaji API object. Used in tests to
            avoid real SDK import and network calls.
    """

    def __init__(
        self,
        *,
        sandbox_enabled: Optional[bool] = None,
        _api: Any = None,
    ) -> None:
        if sandbox_enabled is None:
            sandbox_enabled = os.getenv("BROKER_SHIOAJI_SANDBOX_ENABLED", "").lower() in {
                "1", "true", "yes"
            }
        self._sandbox_enabled: bool = sandbox_enabled
        self._api: Any = _api
        self._orders: Dict[str, ShioajiOrder] = {}
        self._trades: Dict[str, Any] = {}  # order_id -> Shioaji Trade object
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    def _gate_check(self) -> None:
        if not self._sandbox_enabled:
            raise ShioajiBrokerError(
                _ERR_SANDBOX_DISABLED,
                (
                    "Shioaji sandbox adapter is disabled. "
                    "Set BROKER_SHIOAJI_SANDBOX_ENABLED=true to enable sandbox (simulation) mode."
                ),
                status_code=503,
            )

    def _get_api(self) -> Any:
        """Return the Shioaji API, initializing lazily on first real use."""
        if self._api is not None:
            return self._api

        try:
            import shioaji as sj  # noqa: PLC0415
        except ImportError as exc:
            raise ShioajiBrokerError(
                _ERR_SDK_MISSING,
                "shioaji package is not installed. Add shioaji>=1.1.0,<2.0.0 to requirements.txt and rebuild.",
                status_code=500,
            ) from exc

        api_key = os.getenv("BROKER_SHIOAJI_API_KEY", "")
        secret_key = os.getenv("BROKER_SHIOAJI_SECRET_KEY", "")
        if not api_key or not secret_key:
            raise ShioajiBrokerError(
                _ERR_CREDENTIALS_MISSING,
                "BROKER_SHIOAJI_API_KEY and BROKER_SHIOAJI_SECRET_KEY must be set.",
                status_code=503,
            )

        with self._lock:
            if self._api is None:
                api = sj.Shioaji(simulation=True)
                api.login(api_key=api_key, secret_key=secret_key)
                self._api = api

        return self._api

    def _place_order_via_sdk(
        self,
        api: Any,
        symbol: str,
        qty: float,
        side: str,
        order_type: str,
        limit_price: Optional[float],
    ) -> Any:
        """Thin wrapper around the Shioaji place_order call.

        Imports shioaji.constant lazily and falls back to string values when
        the SDK is not installed (e.g. test environments using a mock API).
        """
        try:
            import shioaji.constant as sc  # noqa: PLC0415
            action = sc.Action.Buy if side == "buy" else sc.Action.Sell
            price_type = sc.StockPriceType.MKT if order_type == "market" else sc.StockPriceType.LMT
            shioaji_order_type = sc.OrderType.ROD
        except ImportError:
            action = "Buy" if side == "buy" else "Sell"
            price_type = "MKT" if order_type == "market" else "LMT"
            shioaji_order_type = "ROD"

        contract = api.Contracts.Stocks[symbol]
        order = api.Order(
            price=limit_price or 0,
            quantity=int(qty),
            action=action,
            price_type=price_type,
            order_type=shioaji_order_type,
        )
        return api.place_order(contract, order)

    # ------------------------------------------------------------------
    # Public adapter interface (aligned with paper_simulation.py shape)
    # ------------------------------------------------------------------

    def submit(
        self,
        *,
        capital_pool_id: str,
        strategy_id: str,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> ShioajiOrder:
        """Submit an order to the Shioaji simulation (sandbox) account."""
        self._gate_check()

        if side not in ("buy", "sell"):
            raise ShioajiBrokerError("INVALID_SIDE", f"side must be 'buy' or 'sell', got {side!r}")
        if order_type not in ("market", "limit"):
            raise ShioajiBrokerError(
                "INVALID_ORDER_TYPE",
                f"order_type must be 'market' or 'limit', got {order_type!r}",
            )
        if qty <= 0:
            raise ShioajiBrokerError("INVALID_QTY", "qty must be positive")
        if qty != int(qty):
            raise ShioajiBrokerError(
                "INVALID_QTY",
                f"qty must be a whole number (integer lots); fractional quantities are not supported, got {qty!r}",
            )
        if order_type == "limit" and (limit_price is None or limit_price <= 0):
            raise ShioajiBrokerError(
                "INVALID_LIMIT_PRICE", "limit_price must be positive for limit orders"
            )

        api = self._get_api()
        now = _utc_now_iso()
        order_id = uuid.uuid4().hex

        try:
            trade = self._place_order_via_sdk(api, symbol, qty, side, order_type, limit_price)
        except ShioajiBrokerError:
            raise
        except Exception as exc:
            raise ShioajiBrokerError(
                _ERR_SUBMIT_FAILED,
                f"Shioaji place_order failed: {exc}",
                status_code=502,
            ) from exc

        shioaji_trade_id = str(getattr(trade, "trade_id", order_id))

        order = ShioajiOrder(
            order_id=order_id,
            capital_pool_id=capital_pool_id,
            strategy_id=strategy_id,
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
            created_at=now,
            filled_at=None,
            fill_price=limit_price if order_type == "limit" else None,
            fill_qty=0.0,
            status="submitted",
            shioaji_trade_id=shioaji_trade_id,
        )

        with self._lock:
            self._orders[order_id] = order
            self._trades[order_id] = trade

        return order

    def cancel(self, order_id: str) -> ShioajiOrder:
        """Cancel a pending order on the simulation account."""
        self._gate_check()

        with self._lock:
            order = self._orders.get(order_id)
            trade = self._trades.get(order_id)

        if order is None:
            raise ShioajiBrokerError(
                _ERR_ORDER_NOT_FOUND,
                f"Order {order_id!r} not found.",
                status_code=404,
            )
        if order.status in ("cancelled", "filled"):
            raise ShioajiBrokerError(
                _ERR_CANCEL_FAILED,
                f"Cannot cancel order already in status {order.status!r}.",
                status_code=400,
            )

        try:
            api = self._get_api()
            if trade is not None:
                api.cancel_order(trade)
        except ShioajiBrokerError:
            raise
        except Exception as exc:
            raise ShioajiBrokerError(
                _ERR_CANCEL_FAILED,
                f"Shioaji cancel_order failed: {exc}",
                status_code=502,
            ) from exc

        with self._lock:
            order.status = "cancelled"
            order.filled_at = _utc_now_iso()

        return order

    def get_status(self, order_id: str) -> ShioajiOrder:
        """Return the current status of an order, refreshing from the broker."""
        self._gate_check()

        with self._lock:
            order = self._orders.get(order_id)

        if order is None:
            raise ShioajiBrokerError(
                _ERR_ORDER_NOT_FOUND,
                f"Order {order_id!r} not found.",
                status_code=404,
            )

        try:
            api = self._get_api()
            api.update_status()
        except ShioajiBrokerError:
            raise
        except Exception:
            pass  # return cached status if the refresh fails

        return order

    def reject_live_order(self) -> None:
        """Always raise — live orders are permanently disabled in this adapter."""
        raise ShioajiBrokerError(
            _ERR_LIVE_DISABLED,
            (
                "Live broker execution is permanently disabled. "
                "The Shioaji adapter operates in sandbox (simulation) mode only."
            ),
            status_code=403,
        )
