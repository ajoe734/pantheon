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
import time
import uuid
from typing import Any, Dict, Optional

_ERR_SANDBOX_DISABLED = "SHIOAJI_SANDBOX_DISABLED"
_ERR_LIVE_DISABLED = "SHIOAJI_LIVE_DISABLED"
_ERR_ORDER_NOT_FOUND = "SHIOAJI_ORDER_NOT_FOUND"
_ERR_CANCEL_FAILED = "SHIOAJI_CANCEL_FAILED"
_ERR_SDK_MISSING = "SHIOAJI_SDK_MISSING"
_ERR_CREDENTIALS_MISSING = "SHIOAJI_CREDENTIALS_MISSING"
_ERR_SUBMIT_FAILED = "SHIOAJI_SUBMIT_FAILED"
_ERR_INVALID_ACCOUNT_KIND = "SHIOAJI_INVALID_ACCOUNT_KIND"
_ERR_CONTRACT_NOT_FOUND = "SHIOAJI_CONTRACT_NOT_FOUND"
_ERR_ACCOUNT_MISSING = "SHIOAJI_ACCOUNT_MISSING"

_ACCOUNT_STOCK = "stock"
_ACCOUNT_FUTURES = "futures"
_VALID_ACCOUNT_KINDS = {_ACCOUNT_STOCK, _ACCOUNT_FUTURES}
_SUBMIT_SPACING_SECONDS = 1.0


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
    account_kind: str = _ACCOUNT_STOCK
    submit_spacing_seconds: float = _SUBMIT_SPACING_SECONDS
    submit_spacing_wait_seconds: float = 0.0
    submit_spacing_previous_elapsed_seconds: Optional[float] = None
    shioaji_order_status_id: Optional[str] = None
    shioaji_order_status: Optional[str] = None
    shioaji_order_status_code: Optional[str] = None
    shioaji_order_status_message: Optional[str] = None

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

    _thread_submit_state = threading.local()

    def __init__(
        self,
        *,
        sandbox_enabled: Optional[bool] = None,
        _api: Any = None,
        submit_spacing_seconds: float = _SUBMIT_SPACING_SECONDS,
    ) -> None:
        if sandbox_enabled is None:
            sandbox_enabled = os.getenv("BROKER_SHIOAJI_SANDBOX_ENABLED", "").lower() in {
                "1", "true", "yes"
            }
        self._sandbox_enabled: bool = sandbox_enabled
        self._api: Any = _api
        self._orders: Dict[str, ShioajiOrder] = {}
        self._trades: Dict[str, Any] = {}  # order_id -> Shioaji Trade object
        self._accounts: Dict[str, Any] = {}  # order_id -> Shioaji Account object
        self._lock = threading.Lock()
        self._submit_spacing_seconds = max(0.0, float(submit_spacing_seconds))

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
                "shioaji package is not installed. Add shioaji>=1.2.0,<2.0.0 to requirements.txt and rebuild.",
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

    def _resolve_account(self, api: Any, account_kind: str) -> Any:
        if account_kind == _ACCOUNT_STOCK:
            account = getattr(api, "stock_account", None)
        elif account_kind == _ACCOUNT_FUTURES:
            account = getattr(api, "futopt_account", None)
        else:
            raise ShioajiBrokerError(
                _ERR_INVALID_ACCOUNT_KIND,
                f"account_kind must be one of {sorted(_VALID_ACCOUNT_KINDS)!r}, got {account_kind!r}",
            )

        if account is None:
            raise ShioajiBrokerError(
                _ERR_ACCOUNT_MISSING,
                f"Shioaji {account_kind} account is not available after login.",
                status_code=503,
            )
        # In Shioaji simulation mode, place_order itself is the audit-generating
        # test step. signed=True is verified later in production mode.
        return account

    @staticmethod
    def _safe_account_key(account_kind: str, account: Any) -> str:
        parts = [account_kind]
        for attr in ("broker_id", "account_id"):
            value = getattr(account, attr, "")
            parts.append(value if isinstance(value, str) and value else "unknown")
        return ":".join(parts)

    @classmethod
    def _last_submit_by_account(cls) -> Dict[str, float]:
        last_submit = getattr(cls._thread_submit_state, "last_submit_by_account", None)
        if last_submit is None:
            last_submit = {}
            cls._thread_submit_state.last_submit_by_account = last_submit
        return last_submit

    def _enforce_submit_spacing(self, account_key: str) -> tuple[float, Optional[float]]:
        if self._submit_spacing_seconds <= 0:
            return 0.0, None

        now = time.monotonic()
        last_submit_by_account = self._last_submit_by_account()
        previous_submit = last_submit_by_account.get(account_key)
        previous_elapsed = None if previous_submit is None else now - previous_submit
        wait_seconds = 0.0
        if previous_elapsed is not None and previous_elapsed < self._submit_spacing_seconds:
            wait_seconds = self._submit_spacing_seconds - previous_elapsed
            time.sleep(wait_seconds)
            now = time.monotonic()

        last_submit_by_account[account_key] = now
        return wait_seconds, previous_elapsed

    @staticmethod
    def _container_get(container: Any, key: str) -> Any:
        try:
            return container[key]
        except (KeyError, TypeError, AttributeError):
            pass
        return getattr(container, key)

    def _resolve_contract(
        self,
        api: Any,
        *,
        account_kind: str,
        symbol: str,
        futures_category: Optional[str],
    ) -> Any:
        try:
            if account_kind == _ACCOUNT_STOCK:
                return self._container_get(api.Contracts.Stocks, symbol)

            futures = api.Contracts.Futures
            if futures_category:
                return self._container_get(self._container_get(futures, futures_category), symbol)

            try:
                return self._container_get(futures, symbol)
            except (KeyError, TypeError, AttributeError):
                guessed_category = symbol[:3] if len(symbol) >= 3 else ""
                if guessed_category:
                    return self._container_get(self._container_get(futures, guessed_category), symbol)
                raise
        except (KeyError, TypeError, AttributeError) as exc:
            raise ShioajiBrokerError(
                _ERR_CONTRACT_NOT_FOUND,
                f"Could not resolve {account_kind} contract for symbol {symbol!r}.",
                status_code=404,
            ) from exc

    @staticmethod
    def _trade_id(trade: Any, fallback: str) -> str:
        for path in (
            ("trade_id",),
            ("status", "id"),
            ("order", "id"),
            ("order", "seqno"),
        ):
            value = trade
            for attr in path:
                value = getattr(value, attr, None)
                if value is None:
                    break
            if value:
                return str(value)
        return fallback

    @staticmethod
    def _public_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        enum_value = getattr(value, "value", None)
        if enum_value is not None:
            return str(enum_value)
        return str(value)

    @classmethod
    def _trade_status_snapshot(cls, trade: Any) -> Dict[str, Optional[str]]:
        status_obj = getattr(trade, "status", None)
        if status_obj is None:
            return {}
        if isinstance(status_obj, str):
            return {
                "id": None,
                "status": status_obj,
                "status_code": None,
                "message": None,
            }
        return {
            "id": cls._public_str(getattr(status_obj, "id", None)),
            "status": cls._public_str(getattr(status_obj, "status", None)),
            "status_code": cls._public_str(getattr(status_obj, "status_code", None)),
            "message": cls._public_str(getattr(status_obj, "msg", None)),
        }

    @classmethod
    def _apply_trade_status_snapshot(cls, order: ShioajiOrder, trade: Any) -> None:
        snapshot = cls._trade_status_snapshot(trade)
        if not snapshot:
            return
        order.shioaji_order_status_id = snapshot.get("id")
        order.shioaji_order_status = snapshot.get("status")
        order.shioaji_order_status_code = snapshot.get("status_code")
        order.shioaji_order_status_message = snapshot.get("message")

    def _place_order_via_sdk(
        self,
        api: Any,
        account: Any,
        symbol: str,
        qty: float,
        side: str,
        order_type: str,
        limit_price: Optional[float],
        account_kind: str,
        futures_category: Optional[str],
    ) -> Any:
        """Thin wrapper around the Shioaji place_order call.

        Imports shioaji.constant lazily and falls back to string values when
        the SDK is not installed (e.g. test environments using a mock API).
        """
        try:
            import shioaji.constant as sc  # noqa: PLC0415
            action = sc.Action.Buy if side == "buy" else sc.Action.Sell
            price_type = sc.StockPriceType.MKT if order_type == "market" else sc.StockPriceType.LMT
            if account_kind == _ACCOUNT_FUTURES:
                shioaji_order_type = getattr(sc, "FuturesOrderType", sc.OrderType).ROD
                futures_octype = sc.FuturesOCType.Auto
            else:
                shioaji_order_type = sc.OrderType.ROD
                futures_octype = None
        except ImportError:
            action = "Buy" if side == "buy" else "Sell"
            price_type = "MKT" if order_type == "market" else "LMT"
            shioaji_order_type = "ROD"
            futures_octype = "Auto" if account_kind == _ACCOUNT_FUTURES else None

        contract = self._resolve_contract(
            api,
            account_kind=account_kind,
            symbol=symbol,
            futures_category=futures_category,
        )
        order_kwargs = dict(
            price=limit_price or 0,
            quantity=int(qty),
            action=action,
            price_type=price_type,
            order_type=shioaji_order_type,
            account=account,
        )
        if futures_octype is not None:
            order_kwargs["octype"] = futures_octype
        order = api.Order(**order_kwargs)
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
        account_kind: str = _ACCOUNT_STOCK,
        futures_category: Optional[str] = None,
    ) -> ShioajiOrder:
        """Submit an order to the Shioaji simulation (sandbox) account."""
        self._gate_check()

        if account_kind not in _VALID_ACCOUNT_KINDS:
            raise ShioajiBrokerError(
                _ERR_INVALID_ACCOUNT_KIND,
                f"account_kind must be one of {sorted(_VALID_ACCOUNT_KINDS)!r}, got {account_kind!r}",
            )
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
        account = self._resolve_account(api, account_kind)
        account_key = self._safe_account_key(account_kind, account)
        spacing_wait, spacing_previous_elapsed = self._enforce_submit_spacing(account_key)
        now = _utc_now_iso()
        order_id = uuid.uuid4().hex

        try:
            trade = self._place_order_via_sdk(
                api,
                account,
                symbol,
                qty,
                side,
                order_type,
                limit_price,
                account_kind,
                futures_category,
            )
        except ShioajiBrokerError:
            raise
        except Exception as exc:
            raise ShioajiBrokerError(
                _ERR_SUBMIT_FAILED,
                f"Shioaji place_order failed: {exc}",
                status_code=502,
            ) from exc

        shioaji_trade_id = self._trade_id(trade, order_id)
        trade_status = self._trade_status_snapshot(trade)

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
            account_kind=account_kind,
            submit_spacing_seconds=self._submit_spacing_seconds,
            submit_spacing_wait_seconds=spacing_wait,
            submit_spacing_previous_elapsed_seconds=spacing_previous_elapsed,
            shioaji_order_status_id=trade_status.get("id"),
            shioaji_order_status=trade_status.get("status"),
            shioaji_order_status_code=trade_status.get("status_code"),
            shioaji_order_status_message=trade_status.get("message"),
        )

        with self._lock:
            self._orders[order_id] = order
            self._trades[order_id] = trade
            self._accounts[order_id] = account

        return order

    def cancel(self, order_id: str) -> ShioajiOrder:
        """Cancel a pending order on the simulation account."""
        self._gate_check()

        with self._lock:
            order = self._orders.get(order_id)
            trade = self._trades.get(order_id)
            account = self._accounts.get(order_id)

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
            if account is not None:
                try:
                    api.update_status(account)
                except TypeError:
                    api.update_status()
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
            self._apply_trade_status_snapshot(order, trade)

        return order

    def get_status(self, order_id: str) -> ShioajiOrder:
        """Return the current status of an order, refreshing from the broker."""
        self._gate_check()

        with self._lock:
            order = self._orders.get(order_id)
            account = self._accounts.get(order_id)
            trade = self._trades.get(order_id)

        if order is None:
            raise ShioajiBrokerError(
                _ERR_ORDER_NOT_FOUND,
                f"Order {order_id!r} not found.",
                status_code=404,
            )

        try:
            api = self._get_api()
            if account is not None:
                try:
                    api.update_status(account)
                except TypeError:
                    api.update_status()
            else:
                api.update_status()
        except ShioajiBrokerError:
            raise
        except Exception:
            pass  # return cached status if the refresh fails

        with self._lock:
            self._apply_trade_status_snapshot(order, trade)

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
