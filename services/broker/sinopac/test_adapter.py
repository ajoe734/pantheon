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

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_SHIOAJI_DIR = str(os.path.dirname(__file__))
if _SHIOAJI_DIR not in sys.path:
    sys.path.insert(0, _SHIOAJI_DIR)

import adapter as adapter_module  # noqa: E402
from adapter import ShioajiBrokerAdapter, ShioajiBrokerError, ShioajiOrder  # noqa: E402


def _make_mock_api(trade_id: str = "mock-trade-001") -> MagicMock:
    """Return a minimal Shioaji API mock sufficient for adapter tests."""
    mock_trade = MagicMock()
    mock_trade.trade_id = trade_id
    mock_trade.status = SimpleNamespace(id=trade_id, status="Submitted", status_code="0", msg="")

    api = MagicMock()
    api.Contracts.Stocks.__getitem__.return_value = MagicMock()
    api.Contracts.Futures.TXF.__getitem__.return_value = MagicMock()
    api.Contracts.Futures.__getitem__.return_value = api.Contracts.Futures.TXF
    api.Order.return_value = MagicMock()
    api.place_order.return_value = mock_trade
    api.cancel_order.return_value = None
    api.update_status.return_value = None
    api.stock_account = SimpleNamespace(
        broker_id="9A95",
        account_id="stock-test-001",
        person_id="person-test",
        signed=True,
    )
    api.futopt_account = SimpleNamespace(
        broker_id="F002000",
        account_id="future-test-001",
        person_id="person-test",
        signed=True,
    )
    return api


_ORDER_KWARGS = dict(
    capital_pool_id="pool-1",
    strategy_id="strat-tw-001",
    symbol="2330",
    qty=1.0,
    side="buy",
)
_TEST_SPACING_SECONDS = 0.0


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

    def test_gate_closed_does_not_touch_injected_sdk(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=False,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

        for call in (
            lambda: adapter.submit(**_ORDER_KWARGS),
            lambda: adapter.cancel("order-never-submitted"),
            lambda: adapter.get_status("order-never-submitted"),
        ):
            with self.assertRaises(ShioajiBrokerError) as ctx:
                call()
            self.assertEqual(ctx.exception.error_code, "SHIOAJI_SANDBOX_DISABLED")

        mock_api.Order.assert_not_called()
        mock_api.place_order.assert_not_called()
        mock_api.cancel_order.assert_not_called()
        mock_api.update_status.assert_not_called()


class TestLiveOrderAlwaysRejected(unittest.TestCase):
    """Live reject path must raise unconditionally regardless of sandbox gate."""

    def test_live_rejected_when_gate_closed(self):
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=False,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.reject_live_order()
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_LIVE_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejected_when_gate_open(self):
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.reject_live_order()
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_LIVE_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejection_payload_shape(self):
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=False,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.reject_live_order()

        payload = ctx.exception.to_payload()
        self.assertEqual(payload["status"], "broker_error")
        self.assertIn("error_code", payload)
        self.assertIn("message", payload)

    def test_live_reject_does_not_touch_sdk_or_order_book_when_gate_open(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.reject_live_order()

        self.assertEqual(ctx.exception.error_code, "SHIOAJI_LIVE_DISABLED")
        mock_api.Order.assert_not_called()
        mock_api.place_order.assert_not_called()
        mock_api.cancel_order.assert_not_called()
        mock_api.update_status.assert_not_called()
        self.assertEqual(adapter._orders, {})
        self.assertEqual(adapter._trades, {})


class TestSandboxSubmitHappyPath(unittest.TestCase):
    """Sandbox submit with a mock API."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

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
        self.assertEqual(order.shioaji_order_status, "Submitted")
        self.assertEqual(order.shioaji_order_status_code, "0")

    def test_stock_order_uses_stock_account(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        adapter.submit(**_ORDER_KWARGS)
        self.assertIs(mock_api.Order.call_args.kwargs["account"], mock_api.stock_account)

    def test_futures_order_uses_futopt_account(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        order = adapter.submit(
            **{
                **_ORDER_KWARGS,
                "symbol": "TXFR1",
                "account_kind": "futures",
                "futures_category": "TXF",
            }
        )
        self.assertEqual(order.account_kind, "futures")
        self.assertIs(mock_api.Order.call_args.kwargs["account"], mock_api.futopt_account)
        mock_api.Contracts.Futures.TXF.__getitem__.assert_called_with("TXFR1")

    def test_unsigned_stock_account_still_places_in_simulation(self):
        mock_api = _make_mock_api()
        mock_api.stock_account.signed = False
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        order = adapter.submit(**_ORDER_KWARGS)

        self.assertEqual(order.status, "submitted")
        self.assertIs(mock_api.Order.call_args.kwargs["account"], mock_api.stock_account)
        mock_api.place_order.assert_called_once()

    def test_missing_futures_account_rejected_before_place(self):
        mock_api = _make_mock_api()
        mock_api.futopt_account = None
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.submit(
                **{
                    **_ORDER_KWARGS,
                    "symbol": "TXFR1",
                    "account_kind": "futures",
                    "futures_category": "TXF",
                }
            )
        self.assertEqual(ctx.exception.error_code, "SHIOAJI_ACCOUNT_MISSING")
        mock_api.place_order.assert_not_called()

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
        return ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

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

    def test_get_status_projects_filled_trade_status(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        order = adapter.submit(
            **{
                **_ORDER_KWARGS,
                "order_type": "limit",
                "limit_price": 580.0,
                "qty": 3.0,
            }
        )
        mock_api.place_order.return_value.status = SimpleNamespace(
            id="mock-trade-001",
            status="Filled",
            status_code="0",
            msg="filled by sandbox",
        )

        fetched = adapter.get_status(order.order_id)

        self.assertEqual(fetched.status, "filled")
        self.assertEqual(fetched.fill_qty, 3.0)
        self.assertEqual(fetched.fill_price, 580.0)
        self.assertIsNotNone(fetched.filled_at)
        self.assertEqual(fetched.shioaji_order_status, "Filled")
        self.assertEqual(fetched.shioaji_order_status_message, "filled by sandbox")

    def test_cancel_calls_api_cancel_order(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=mock_api)
        order = adapter.submit(**_ORDER_KWARGS)
        adapter.cancel(order.order_id)
        mock_api.cancel_order.assert_called_once()

    def test_get_status_calls_update_status(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        order = adapter.submit(**_ORDER_KWARGS)
        adapter.get_status(order.order_id)
        mock_api.update_status.assert_called_once_with(mock_api.stock_account)


class TestSandboxInputValidation(unittest.TestCase):
    """Input validation runs before any SDK call."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

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


class TestFractionalQtyRejection(unittest.TestCase):
    """Fractional qty must be rejected before any SDK call."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

    def test_fractional_qty_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "qty": 1.9})
        self.assertEqual(ctx.exception.error_code, "INVALID_QTY")
        self.assertIn("whole number", ctx.exception.message)

    def test_half_lot_rejected(self):
        with self.assertRaises(ShioajiBrokerError) as ctx:
            self._adapter().submit(**{**_ORDER_KWARGS, "qty": 0.5})
        self.assertEqual(ctx.exception.error_code, "INVALID_QTY")

    def test_sdk_not_called_for_fractional_qty(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )
        with self.assertRaises(ShioajiBrokerError):
            adapter.submit(**{**_ORDER_KWARGS, "qty": 1.5})
        mock_api.place_order.assert_not_called()

    def test_integer_float_qty_accepted(self):
        order = self._adapter().submit(**{**_ORDER_KWARGS, "qty": 2.0})
        self.assertEqual(order.qty, 2.0)

    def test_integer_qty_accepted(self):
        order = self._adapter().submit(**{**_ORDER_KWARGS, "qty": 5.0})
        self.assertEqual(order.qty, 5.0)


class TestErrorPaths(unittest.TestCase):
    """Error paths for cancel and get_status."""

    def _adapter(self) -> ShioajiBrokerAdapter:
        return ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=_TEST_SPACING_SECONDS,
        )

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
            ShioajiBrokerAdapter(
                sandbox_enabled=False,
                submit_spacing_seconds=_TEST_SPACING_SECONDS,
            ).submit(**_ORDER_KWARGS)
        except ShioajiBrokerError as exc:
            payload = exc.to_payload()
        self.assertEqual(payload["status"], "broker_error")
        self.assertIn("error_code", payload)
        self.assertIn("message", payload)


class TestSubmitSpacingGate(unittest.TestCase):
    """Adapter enforces a per-account thread-local submit interval."""

    def setUp(self):
        adapter_module.ShioajiBrokerAdapter._thread_submit_state.last_submit_by_account = {}

    def test_second_submit_waits_for_same_account(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=1.0,
        )
        with patch.object(adapter_module.time, "monotonic", side_effect=[100.0, 100.25, 101.0]):
            with patch.object(adapter_module.time, "sleep") as sleep:
                adapter.submit(**_ORDER_KWARGS)
                order = adapter.submit(**_ORDER_KWARGS)

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.75)
        self.assertAlmostEqual(order.submit_spacing_wait_seconds, 0.75)
        self.assertAlmostEqual(order.submit_spacing_previous_elapsed_seconds, 0.25)

    def test_submit_spacing_is_per_account(self):
        mock_api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=mock_api,
            submit_spacing_seconds=1.0,
        )
        with patch.object(adapter_module.time, "monotonic", side_effect=[200.0, 200.1]):
            with patch.object(adapter_module.time, "sleep") as sleep:
                adapter.submit(**_ORDER_KWARGS)
                order = adapter.submit(
                    **{
                        **_ORDER_KWARGS,
                        "symbol": "TXFR1",
                        "account_kind": "futures",
                        "futures_category": "TXF",
                    }
                )

        sleep.assert_not_called()
        self.assertEqual(order.submit_spacing_wait_seconds, 0.0)
        self.assertIsNone(order.submit_spacing_previous_elapsed_seconds)


if __name__ == "__main__":
    unittest.main()
