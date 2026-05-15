"""Tests for the Management-facing Shioaji sandbox facade."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.broker.shioaji.adapter import ShioajiBrokerAdapter, ShioajiBrokerError
from services.broker.shioaji.facade import ShioajiSandboxFacade


def _make_mock_api(trade_id: str = "mock-facade-trade-001") -> MagicMock:
    mock_trade = MagicMock()
    mock_trade.trade_id = trade_id
    mock_trade.status = SimpleNamespace(id=trade_id, status="Submitted", status_code="0", msg="")

    api = MagicMock()
    api.Contracts.Stocks.__getitem__.return_value = MagicMock()
    api.Order.return_value = MagicMock()
    api.place_order.return_value = mock_trade
    api.cancel_order.return_value = None
    api.update_status.return_value = None
    api.stock_account = SimpleNamespace(
        account_type="stock",
        broker_id="9A95",
        account_id="stock-test-7890",
        person_id="person-test-1234",
        signed=True,
    )
    api.futopt_account = SimpleNamespace(
        account_type="futures",
        broker_id="F002000",
        account_id="future-test-4321",
        person_id="person-test-1234",
        signed=True,
    )
    return api


_ORDER_KWARGS = {
    "capital_pool_id": "pool-mgmt-broker-001",
    "strategy_id": "strategy-mgmt-broker-001",
    "symbol": "2890",
    "qty": 1.0,
    "side": "buy",
    "order_type": "limit",
    "limit_price": 18.0,
    "account_kind": "stock",
}


class TestShioajiAdapterFacadeHooks(unittest.TestCase):
    def test_connect_returns_sandbox_summary_without_order_side_effects(self) -> None:
        api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=api, submit_spacing_seconds=0.0)

        payload = adapter.connect()

        self.assertEqual(payload["status"], "connected")
        self.assertEqual(payload["broker"], "shioaji")
        self.assertEqual(payload["environment"], "sandbox")
        self.assertFalse(payload["production_live_enabled"])
        self.assertFalse(payload["capital_binding_enabled"])
        api.Order.assert_not_called()
        api.place_order.assert_not_called()
        api.cancel_order.assert_not_called()

    def test_account_status_returns_redacted_ready_account(self) -> None:
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_api(),
            submit_spacing_seconds=0.0,
        )

        payload = adapter.account_status("stock")

        self.assertEqual(payload["account_status"], "ready")
        self.assertTrue(payload["signed"])
        self.assertEqual(payload["account"]["account_id_last4"], "7890")
        self.assertEqual(payload["account"]["person_id_last4"], "1234")
        self.assertNotIn("stock-test-7890", str(payload))
        self.assertFalse(payload["raw_secret_material_persisted"])

    def test_account_status_reports_unsigned_and_missing(self) -> None:
        api = _make_mock_api()
        api.stock_account.signed = False
        api.futopt_account = None
        adapter = ShioajiBrokerAdapter(sandbox_enabled=True, _api=api, submit_spacing_seconds=0.0)

        self.assertEqual(adapter.account_status("stock")["account_status"], "unsigned")
        self.assertEqual(adapter.account_status("futures")["account_status"], "missing")

    def test_connect_gate_closed_does_not_touch_injected_sdk(self) -> None:
        api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(sandbox_enabled=False, _api=api, submit_spacing_seconds=0.0)

        with self.assertRaises(ShioajiBrokerError) as ctx:
            adapter.connect()

        self.assertEqual(ctx.exception.error_code, "SHIOAJI_SANDBOX_DISABLED")
        api.Order.assert_not_called()
        api.place_order.assert_not_called()
        api.cancel_order.assert_not_called()


class TestShioajiSandboxFacade(unittest.TestCase):
    def _facade(self, *, sandbox_enabled: bool = True) -> ShioajiSandboxFacade:
        adapter = ShioajiBrokerAdapter(
            sandbox_enabled=sandbox_enabled,
            _api=_make_mock_api(),
            submit_spacing_seconds=0.0,
        )
        return ShioajiSandboxFacade(adapter)

    def test_run_lifecycle_returns_management_output_shape(self) -> None:
        payload = self._facade().run_lifecycle(**_ORDER_KWARGS)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["broker"], "shioaji")
        self.assertEqual(payload["environment"], "sandbox")
        self.assertEqual(payload["account_status"], "ready")
        self.assertEqual(payload["place_result"]["status"], "submitted")
        self.assertEqual(payload["cancel_result"]["status"], "cancelled")
        self.assertEqual(payload["readback_result"]["status"], "cancelled")
        self.assertEqual(payload["reconcile_result"]["status"], "passed")
        self.assertEqual(payload["live_disabled_result"]["status"], "rejected")
        self.assertFalse(payload["production_live_enabled"])
        self.assertFalse(payload["capital_binding_enabled"])
        self.assertTrue(payload["human_gate_required"])
        self.assertIsNone(payload["error"])

    def test_reconcile_flags_mismatched_readback(self) -> None:
        facade = self._facade()
        result = facade.reconcile(
            place_result={"order_id": "order-1", "status": "submitted", "shioaji_trade_id": "trade-1"},
            cancel_result={"order_id": "order-1", "status": "cancelled"},
            readback_result={
                "order_id": "order-1",
                "status": "submitted",
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "sandbox",
            },
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn(
            {
                "field": "readback.status",
                "expected": "cancelled",
                "observed": "submitted",
                "status": "diff",
            },
            result["comparisons"],
        )

    def test_closed_gate_lifecycle_fails_without_order_side_effects(self) -> None:
        api = _make_mock_api()
        adapter = ShioajiBrokerAdapter(sandbox_enabled=False, _api=api, submit_spacing_seconds=0.0)
        payload = ShioajiSandboxFacade(adapter).run_lifecycle(**_ORDER_KWARGS)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["error_code"], "SHIOAJI_SANDBOX_DISABLED")
        self.assertFalse(payload["production_live_enabled"])
        self.assertFalse(payload["capital_binding_enabled"])
        api.Order.assert_not_called()
        api.place_order.assert_not_called()
        api.cancel_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
