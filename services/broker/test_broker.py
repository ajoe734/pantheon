"""Tests for the broker sidecar service.

Covers:
- health probes
- paper gate disabled by default
- live orders always rejected (paper gate state is irrelevant)
- paper simulation happy path when gate enabled
- paper simulation input validation (side, order_type, qty, limit_price)
- paper order list and get
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
import importlib.util
from unittest.mock import patch

_BROKER_DIR = str(Path(__file__).resolve().parent)
if _BROKER_DIR not in sys.path:
    sys.path.insert(0, _BROKER_DIR)

_BROKER_MAIN_PATH = Path(__file__).resolve().parent / "main.py"
_BROKER_MAIN_SPEC = importlib.util.spec_from_file_location("pantheon_broker_sidecar_main", _BROKER_MAIN_PATH)
if _BROKER_MAIN_SPEC is None or _BROKER_MAIN_SPEC.loader is None:
    raise RuntimeError(f"Could not load broker main module from {_BROKER_MAIN_PATH}")
broker_main = importlib.util.module_from_spec(_BROKER_MAIN_SPEC)
sys.modules[_BROKER_MAIN_SPEC.name] = broker_main
_BROKER_MAIN_SPEC.loader.exec_module(broker_main)
from fastapi.testclient import TestClient

client = TestClient(broker_main.app)

_PAPER_ORDER = {
    "capital_pool_id": "pool-1",
    "strategy_id": "strat-1",
    "symbol": "AAPL",
    "qty": 10.0,
    "side": "buy",
}


class TestBrokerHealthRoutes(unittest.TestCase):
    def test_healthz_ok(self):
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "broker")
        self.assertFalse(body["live_enabled"])

    def test_livez_ok(self):
        resp = client.get("/livez")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_readyz_ok(self):
        resp = client.get("/readyz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class TestBrokerPaperGateDefaultState(unittest.TestCase):
    def test_paper_enabled_false_by_default(self):
        self.assertFalse(broker_main._PAPER_ENABLED)

    def test_live_enabled_always_false(self):
        self.assertFalse(broker_main._LIVE_ENABLED)

    def test_submit_paper_order_rejected_when_gate_closed(self):
        resp = client.post("/api/broker/paper/orders", json=_PAPER_ORDER)
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["error_code"], "PAPER_ADAPTER_DISABLED")

    def test_list_paper_orders_rejected_when_gate_closed(self):
        resp = client.get("/api/broker/paper/orders")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_code"], "PAPER_ADAPTER_DISABLED")

    def test_get_paper_order_rejected_when_gate_closed(self):
        resp = client.get("/api/broker/paper/orders/order-123")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_code"], "PAPER_ADAPTER_DISABLED")


class TestBrokerLiveOrderAlwaysRejected(unittest.TestCase):
    def test_live_order_rejected_when_paper_disabled(self):
        resp = client.post("/api/broker/live/orders")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error_code"], "LIVE_ADAPTER_DISABLED")
        self.assertFalse(body["live_enabled"])

    def test_live_order_rejected_even_when_paper_enabled(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/live/orders")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error_code"], "LIVE_ADAPTER_DISABLED")
        self.assertFalse(body["live_enabled"])

    def test_live_rejection_contains_deployment_stage(self):
        resp = client.post("/api/broker/live/orders")
        self.assertEqual(resp.json()["deployment_stage"], "paper")


class TestBrokerPaperSimulationHappyPath(unittest.TestCase):
    def test_market_order_fills_at_placeholder_price(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json=_PAPER_ORDER)
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        order = body["order"]
        self.assertFalse(order["is_real_order"])
        self.assertFalse(order["is_real_capital"])
        self.assertTrue(order["sim_fill_flag"])
        self.assertEqual(order["deployment_stage"], "paper")
        self.assertIsNotNone(order["order_id"])
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["fill_price"], 100.0)
        self.assertEqual(order["fill_qty"], 10.0)

    def test_limit_order_fills_at_limit_price(self):
        order_req = {**_PAPER_ORDER, "side": "sell", "order_type": "limit", "limit_price": 175.50, "symbol": "TSLA"}
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json=order_req)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["order"]["fill_price"], 175.50)

    def test_sell_order_fills(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json={**_PAPER_ORDER, "side": "sell"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["order"]["side"], "sell")


class TestBrokerPaperSimulationValidation(unittest.TestCase):
    def test_invalid_side_rejected(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json={**_PAPER_ORDER, "side": "short"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "INVALID_SIDE")

    def test_invalid_order_type_rejected(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json={**_PAPER_ORDER, "order_type": "stop"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "INVALID_ORDER_TYPE")

    def test_zero_qty_rejected(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json={**_PAPER_ORDER, "qty": 0.0})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "INVALID_QTY")

    def test_negative_qty_rejected(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post("/api/broker/paper/orders", json={**_PAPER_ORDER, "qty": -5.0})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "INVALID_QTY")

    def test_limit_order_without_price_rejected(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post(
                "/api/broker/paper/orders",
                json={**_PAPER_ORDER, "order_type": "limit", "limit_price": None},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "INVALID_LIMIT_PRICE")

    def test_limit_order_with_zero_price_rejected(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.post(
                "/api/broker/paper/orders",
                json={**_PAPER_ORDER, "order_type": "limit", "limit_price": 0.0},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "INVALID_LIMIT_PRICE")


class TestBrokerPaperOrderListAndGet(unittest.TestCase):
    def test_list_returns_submitted_orders(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            client.post("/api/broker/paper/orders", json={**_PAPER_ORDER, "capital_pool_id": "pool-list-test"})
            resp = client.get("/api/broker/paper/orders", params={"capital_pool_id": "pool-list-test"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["orders"], list)
        orders = [o for o in body["orders"] if o["capital_pool_id"] == "pool-list-test"]
        self.assertGreater(len(orders), 0)

    def test_get_returns_order_by_id(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            create_resp = client.post("/api/broker/paper/orders", json=_PAPER_ORDER)
            order_id = create_resp.json()["order"]["order_id"]
            get_resp = client.get(f"/api/broker/paper/orders/{order_id}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["order"]["order_id"], order_id)

    def test_get_unknown_order_returns_404(self):
        with patch.object(broker_main, "_PAPER_ENABLED", True):
            resp = client.get("/api/broker/paper/orders/nonexistent-order-xyz")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error_code"], "ORDER_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
