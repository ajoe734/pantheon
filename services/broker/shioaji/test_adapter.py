"""Tests for the Shioaji TW broker adapter.

Covers:
- fail-closed default: all operations rejected when BROKER_SHIOAJI_SANDBOX_ENABLED is not set
- live orders always rejected regardless of gate state
- sandbox submit happy path (market + limit) via mock API
- sandbox cancel and get_status via mock API
- input validation (side, order_type, qty, limit_price)
- order-not-found and already-cancelled error paths

Tests inject a mock Shioaji API so no real SDK or network access is required.
"""
from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

_SHIOAJI_DIR = str(os.path.dirname(__file__))
if _SHIOAJI_DIR not in sys.path:
    sys.path.insert(0, _SHIOAJI_DIR)

from adapter import ShioajiBrokerAdapter, ShioajiBrokerError, ShioajiOrder  # noqa: E402


def _make_mock_api(trade_id: str = "mock-trade-001") -> MagicMock:
    """Return a minimal Shioaji API mock sufficient for adapter tests."""
    mock_trade = MagicMock()
    mock_trade.trade_id = trade_id

    api = MagicMock()
    api.Contracts.Stocks.__getitem__.return_value = MagicMock()
    api.Order.return_value = MagicMock()
    api.place_order.return_value = mock_trade
    api.cancel_order.return_value = None
    api.update_status.return_value = None
    return api


_ORDER_KWARGS = dict(
    capital_pool_id="pool-1",
    strategy_id="strat-tw-001",
    symbol="2330",
    qty=1.0,
    side="buy",
)


class TestSandboxGateFailClosed(unittest.TestCase):
    """Default state: sandbox_enabled=False rejects all broker calls."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(sandbox_enabled=False)

    def test_submit_rejected_when_gate_closed(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**_ORDER_KWARGS)
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_SANDBOX_DISABLED")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_cancel_rejected_when_gate_closed(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().cancel("nonexistent-order")
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_SANDBOX_DISABLED")

    def test_get_status_rejected_when_gate_closed(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().get_status("nonexistent-order")
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_SANDBOX_DISABLED")

    def test_env_default_is_fail_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BROKER_SHIOAJI_SANDBOX_ENABLED", None)
            adapter = ShioajiBrokerAdapter()
        self.assertFalse(adapter._sandbox_enabled)


class TestLiveOrderAlwaysRejected(unittest.TestCase):
    """Live reject path must raise unconditionally regardless of sandbox gate."""

    def test_live_rejected_when_gate_closed(self):
        adapter = ShioajiBrokerAdapter(sandbox_enabled=False)
        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.reject_live_order()
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_LIVE_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejected_when_gate_open(self):
        adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=_make_mock_api())
        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.reject_live_order()
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_LIVE_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejection_payload_shape(self):
        adapter = ShioajiBrokerAdapter(sandbox_enabled=False)
        try:
            adapter.reject_live_order()
        except ShioajiBrokerError as exc:
            payload = exc.to_payload()
            self.assertEqual(payload["status"], "broker_error")
            self.assertIn("error_code", payload)
            self.assertIn("message", payload)


class TestSandboxSubmitHappyPath(unittest.TestCase):
    """Sandbox submit with a mock API."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(sandbox_enabled=True, _api=_make_mock_api())

    def test_market_order_returns_shioaji_order(self):
        order = self._adapter().submit(**_ORDER_KWARGS)
        self.assertIsInstance(order, ShioajiOrder)
        self.assertIsNotNone(order.order_id)
        self.assertEqual(order.symbol, "2330")
        self.assertEqual(order.side, "buy")
        self.assertEqual(order.status, "submitted")

    def test_market_order_invariants(self):
        order = self._adapter().submit(**_ORDER_KWARGS)
        self.assertFalse(order.is_real_order)
        self.assertFalse(order.is_real_capital)
        self.assertTrue(order.sim_fill_flag)
        self.assertEqual(order.deployment_stage, "sandbox")

    def test_market_order_trade_id_captured(self):
        order = self._adapter().submit(**_ORDER_KWARGS)
        self.assertEqual(order.shioaji_trade_id, "mock-trade-001")

    def test_limit_order_submit(self):
        order = self._adapter().submit(
            **_ORDER_KWARGS, order_type="limit", limit_price=580.0
        )
        self.assertEqual(order.order_type, "limit")
        self.assertEqual(order.limit_price, 580.0)
        self.assertEqual(order.fill_price, 580.0)

    def test_sell_order_submit(self):
        order = self._adapter().submit(**{**_ORDER_KWARGS, "side": "sell"})
        self.assertEqual(order.side, "sell")

    def test_to_dict_shape_matches_paper_order(self):
        order = self._adapter().submit(**_ORDER_KWARGS)
        d = order.to_dict()
        for field in (
            "order_id", "capital_pool_id", "strategy_id", "symbol",
            "qty", "side", "order_type", "limit_price", "created_at",
            "filled_at", "fill_price", "fill_qty", "status",
            "sim_fill_flag", "is_real_order", "is_real_capital", "deployment_stage",
            "reject_reason",
        ):
            self.assertIn(field, d, f"Missing field: {field}")


class TestSandboxCancelAndGetStatus(unittest.TestCase):
    """Cancel and get_status with a mock API."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(sandbox_enabled=True, _api=_make_mock_api())

    def test_cancel_submitted_order(self):
        adapter = self._adapter()
        order = adapter.submit(**_ORDER_KWARGS)
        cancelled = adapter.cancel(order.order_id)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNotNone(cancelled.filled_at)

    def test_get_status_returns_order(self):
        adapter = self._adapter()
        order = adapter.submit(**_ORDER_KWARGS)
        fetched = adapter.get_status(order.order_id)
        self.assertEqual(fetched.order_id, order.order_id)

    def test_cancel_calls_api_cancel_order(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=mock_api)
        order = adapter.submit(**_ORDER_KWARGS)
        adapter.cancel(order.order_id)
        mock_api.cancel_order.assert_called_once()

    def test_get_status_calls_update_status(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=mock_api)
        order = adapter.submit(**_ORDER_KWARGS)
        adapter.get_status(order.order_id)
        mock_api.update_status.assert_called_once()


class TestSandboxInputValidation(unittest.TestCase):
    """Input validation runs before any SDK call."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(sandbox_enabled=True, _api=_make_mock_api())

    def test_invalid_side_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "side": "short"})
        self.assertEqual(ctx.exception.error_code, "INVALID_SIDE")

    def test_invalid_order_type_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "order_type": "stop"})
        self.assertEqual(ctx.exception.error_code, "INVALID_ORDER_TYPE")

    def test_zero_qty_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "qty": 0.0})
        self.assertEqual(ctx.exception.error_code, "INVALID_QTY")

    def test_negative_qty_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "qty": -1.0})
        self.assertEqual(ctx.exception.error_code, "INVALID_QTY")

    def test_limit_order_without_price_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "order_type": "limit", "limit_price": None})
        self.assertEqual(ctx.exception.error_code, "INVALID_LIMIT_PRICE")

    def test_limit_order_with_zero_price_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "order_type": "limit", "limit_price": 0.0})
        self.assertEqual(ctx.exception.error_code, "INVALID_LIMIT_PRICE")


class TestErrorPaths(unittest.TestCase):
    """Error paths for cancel and get_status."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(sandbox_enabled=True, _api=_make_mock_api())

    def test_cancel_unknown_order_raises_not_found(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().cancel("nonexistent-order-xyz")
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_ORDER_NOT_FOUND")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_status_unknown_order_raises_not_found(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().get_status("nonexistent-order-xyz")
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_ORDER_NOT_FOUND")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cancel_already_cancelled_order_raises(self):
        adapter = self._adapter()
        order = adapter.submit(**_ORDER_KWARGS)
        adapter.cancel(order.order_id)
        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.cancel(order.order_id)
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_CANCEL_FAILED")

    def test_error_payload_shape(self):
        try:
            ShioajiBrokerAdapter(sandbox_enabled=False).submit(**_ORDER_KWARGS)
        except ShioajiBrokerError as exc:
            payload = exc.to_payload()
        self.assertEqual(payload["status"], "broker_error")
        self.assertIn("error_code", payload)
        self.assertIn("message", payload)


if __name__ == "__main__":
    unittest.main()
