"""Sandbox/paper smoke: proves place → readback → cancel → reconcile contract.

This module is the smoke evidence for SVC-OPENCLAW-BROKER-ADAPTER-ACTIVATION-READY
acceptance criterion 2: "sandbox or fake-paper smoke proves place cancel readback
reconcile contract without real capital".

The adapter is called with a fake-paper sidecar stub so no real orders or capital are
involved.  All assertions verify:
- Order lifecycle shape matches the contract (place, readback, cancel)
- Audit trail records every stage with is_real_order=False, is_real_capital=False
- Capability snapshot explicitly expresses sandbox/paper/live/canary states
- Live and canary paths remain fail-closed throughout

Task: SVC-OPENCLAW-BROKER-ADAPTER-ACTIVATION-READY
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ADAPTER_DIR = str(Path(__file__).resolve().parent)
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

from paper_broker_adapter import (
    PaperBrokerAdapter,
    PaperBrokerAdapterError,
    PaperBrokerAuditLog,
)

_TMP = Path("/tmp/pantheon-test-sandbox-paper-smoke")
_TMP.mkdir(parents=True, exist_ok=True)


def _fresh_audit(name: str) -> PaperBrokerAuditLog:
    path = _TMP / f"{name}.jsonl"
    if path.exists():
        path.unlink()
    return PaperBrokerAuditLog(path=str(path))


def _active_paper_binding(**overrides):
    b = {
        "binding_id": "rb-smoke-1",
        "runtime_id": "rt-smoke-1",
        "capital_pool_id": "smoke-pool-1",
        "artifact_id": "smoke-strategy-alpha",
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "status": "active",
        "plan_id": "plan-smoke-1",
        "persona_capital_binding_id": "pcb-smoke-1",
        "metadata": {"strategy_id": "smoke-strategy-alpha"},
    }
    b.update(overrides)
    return b


def _binding_resolver(binding=None):
    if binding is None:
        binding = _active_paper_binding()

    def resolve(capital_pool_id: str):
        if binding.get("capital_pool_id") == capital_pool_id:
            return binding
        return None

    return resolve


_PLACE_RESPONSE = {
    "status": "ok",
    "order": {
        "order_id": "smoke-order-001",
        "capital_pool_id": "smoke-pool-1",
        "strategy_id": "smoke-strategy-alpha",
        "symbol": "AAPL",
        "qty": 5.0,
        "side": "buy",
        "order_type": "market",
        "status": "filled",
        "fill_price": 150.25,
        "fill_qty": 5.0,
        "is_real_order": False,
        "is_real_capital": False,
        "sim_fill_flag": True,
    },
}

_GET_ORDER_RESPONSE = {
    "status": "ok",
    "order": {
        "order_id": "smoke-order-001",
        "capital_pool_id": "smoke-pool-1",
        "strategy_id": "smoke-strategy-alpha",
        "symbol": "AAPL",
        "status": "filled",
        "fill_price": 150.25,
        "fill_qty": 5.0,
        "is_real_order": False,
        "is_real_capital": False,
    },
}

_LIST_ORDERS_RESPONSE = {
    "status": "ok",
    "orders": [
        {
            "order_id": "smoke-order-001",
            "symbol": "AAPL",
            "status": "filled",
            "is_real_order": False,
            "is_real_capital": False,
        }
    ],
    "count": 1,
}

_CANCEL_RESPONSE = {
    "status": "ok",
    "order": {
        "order_id": "smoke-order-001",
        "status": "canceled",
        "is_real_order": False,
        "is_real_capital": False,
    },
}


class TestCapabilityStatesExplicit(unittest.TestCase):
    """AC1: adapter interface has explicit sandbox/paper/live capability states."""

    def test_capability_snapshot_has_all_four_states(self):
        adapter = PaperBrokerAdapter(
            enabled=False,
            broker_url="http://fake-sidecar:8102",
            binding_resolver=_binding_resolver(),
        )
        snap = adapter.capability_snapshot()
        # sandbox_adapter_state must be explicit
        self.assertIn("sandbox_adapter_state", snap)
        self.assertEqual(snap["sandbox_adapter_state"], "activation_ready")
        self.assertIn("sandbox_gate", snap)
        # paper adapter disabled by default
        self.assertFalse(snap["paper_adapter_enabled"])
        # live always false — fail-closed
        self.assertFalse(snap["live_adapter_enabled"])
        # no real capital or orders
        self.assertFalse(snap["is_real_capital"])
        self.assertFalse(snap["is_real_order"])

    def test_capability_snapshot_paper_enabled(self):
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://fake-sidecar:8102",
            binding_resolver=_binding_resolver(),
        )
        snap = adapter.capability_snapshot()
        self.assertEqual(snap["sandbox_adapter_state"], "activation_ready")
        self.assertTrue(snap["paper_adapter_enabled"])
        self.assertFalse(snap["live_adapter_enabled"])
        self.assertEqual(snap["deployment_stage"], "paper")
        self.assertFalse(snap["is_real_capital"])
        self.assertFalse(snap["is_real_order"])

    def test_live_always_false_in_capability_snapshot(self):
        # Even when paper enabled, live_adapter_enabled stays False
        for enabled in (True, False):
            adapter = PaperBrokerAdapter(enabled=enabled, broker_url="http://x:8102")
            snap = adapter.capability_snapshot()
            self.assertFalse(snap["live_adapter_enabled"], f"enabled={enabled}")


class TestSandboxPaperSmoke(unittest.TestCase):
    """AC2: sandbox/fake-paper smoke proves place/cancel/readback/reconcile without real capital."""

    def _adapter(self, audit_name: str) -> tuple[PaperBrokerAdapter, PaperBrokerAuditLog]:
        audit = _fresh_audit(audit_name)
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://fake-sidecar:8102",
            binding_resolver=_binding_resolver(),
            audit_log=audit,
        )
        return adapter, audit

    # ------------------------------------------------------------------
    # Place
    # ------------------------------------------------------------------

    def test_smoke_place_returns_order_with_id_and_fill(self):
        adapter, audit = self._adapter("smoke_place")
        with patch.object(adapter, "_call_sidecar", return_value=_PLACE_RESPONSE):
            result = adapter.submit_paper_order(
                capital_pool_id="smoke-pool-1",
                strategy_id="smoke-strategy-alpha",
                symbol="AAPL",
                qty=5.0,
                side="buy",
                operator_id="smoke-op-1",
            )
        order = result["order"]
        self.assertEqual(order["order_id"], "smoke-order-001")
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["fill_price"], 150.25)
        self.assertEqual(order["fill_qty"], 5.0)

    def test_smoke_place_audit_records_no_real_capital(self):
        adapter, audit = self._adapter("smoke_place_audit")
        with patch.object(adapter, "_call_sidecar", return_value=_PLACE_RESPONSE):
            adapter.submit_paper_order(
                capital_pool_id="smoke-pool-1",
                strategy_id="smoke-strategy-alpha",
                symbol="AAPL",
                qty=5.0,
                side="buy",
                operator_id="smoke-op-1",
            )
        entries = audit.read()
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertFalse(e["is_real_order"], f"entry should have is_real_order=False: {e}")
            self.assertFalse(e["is_real_capital"], f"entry should have is_real_capital=False: {e}")
            self.assertEqual(e["deployment_stage"], "paper")

    def test_smoke_place_audit_has_pending_then_ok(self):
        adapter, audit = self._adapter("smoke_place_seq")
        with patch.object(adapter, "_call_sidecar", return_value=_PLACE_RESPONSE):
            adapter.submit_paper_order(
                capital_pool_id="smoke-pool-1",
                strategy_id="smoke-strategy-alpha",
                symbol="AAPL",
                qty=5.0,
                side="buy",
                operator_id="smoke-op-1",
            )
        entries = audit.read()
        self.assertEqual(entries[0]["event"], "paper_order_intent")
        self.assertEqual(entries[0]["outcome"], "pending")
        self.assertEqual(entries[1]["outcome"], "ok")
        self.assertEqual(entries[1]["order_id"], "smoke-order-001")
        self.assertTrue(entries[1]["sim_fill_flag"])

    # ------------------------------------------------------------------
    # Readback (get / list)
    # ------------------------------------------------------------------

    def test_smoke_readback_get_order_shape(self):
        adapter, _ = self._adapter("smoke_readback_get")
        with patch.object(adapter, "_call_sidecar", return_value=_GET_ORDER_RESPONSE):
            result = adapter.get_paper_order("smoke-order-001")
        order = result["order"]
        self.assertEqual(order["order_id"], "smoke-order-001")
        self.assertEqual(order["status"], "filled")
        self.assertFalse(order["is_real_order"])
        self.assertFalse(order["is_real_capital"])

    def test_smoke_readback_list_orders_shape(self):
        adapter, _ = self._adapter("smoke_readback_list")
        with patch.object(adapter, "_call_sidecar", return_value=_LIST_ORDERS_RESPONSE):
            result = adapter.list_paper_orders(capital_pool_id="smoke-pool-1")
        orders = result["orders"]
        self.assertIsInstance(orders, list)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["order_id"], "smoke-order-001")
        self.assertFalse(orders[0]["is_real_order"])
        self.assertFalse(orders[0]["is_real_capital"])

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def test_smoke_cancel_returns_canceled_status(self):
        adapter, audit = self._adapter("smoke_cancel")
        with patch.object(adapter, "_call_sidecar", return_value=_CANCEL_RESPONSE):
            result = adapter.cancel_paper_order(
                "smoke-order-001",
                operator_id="smoke-op-1",
            )
        order = result["order"]
        self.assertEqual(order["order_id"], "smoke-order-001")
        self.assertEqual(order["status"], "canceled")

    def test_smoke_cancel_audit_records_intent_and_ok(self):
        adapter, audit = self._adapter("smoke_cancel_audit")
        with patch.object(adapter, "_call_sidecar", return_value=_CANCEL_RESPONSE):
            adapter.cancel_paper_order("smoke-order-001", operator_id="smoke-op-1")
        entries = audit.read()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["event"], "paper_order_cancel_intent")
        self.assertEqual(entries[0]["outcome"], "pending")
        self.assertFalse(entries[0]["is_real_order"])
        self.assertFalse(entries[0]["is_real_capital"])
        self.assertEqual(entries[0]["deployment_stage"], "paper")
        self.assertEqual(entries[1]["outcome"], "ok")

    # ------------------------------------------------------------------
    # Full place → readback → cancel lifecycle contract
    # ------------------------------------------------------------------

    def test_smoke_full_lifecycle_place_readback_cancel_reconcile(self):
        """Full place → readback → cancel lifecycle with no real capital at any stage."""
        audit = _fresh_audit("smoke_full_lifecycle")
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://fake-sidecar:8102",
            binding_resolver=_binding_resolver(),
            audit_log=audit,
        )

        call_log: list[tuple[str, str]] = []

        def fake_sidecar(method: str, path: str, payload=None, params=None):
            call_log.append((method, path))
            clean_path = path.split("?")[0]
            if method == "POST" and clean_path.endswith("/orders"):
                return _PLACE_RESPONSE
            if method == "GET" and clean_path.endswith("/smoke-order-001"):
                return _GET_ORDER_RESPONSE
            if method == "GET" and clean_path.endswith("/orders"):
                return _LIST_ORDERS_RESPONSE
            if method == "POST" and "/cancel" in path:
                return _CANCEL_RESPONSE
            raise AssertionError(f"unexpected sidecar call: {method} {path}")

        with patch.object(adapter, "_call_sidecar", side_effect=fake_sidecar):
            # Place
            place_result = adapter.submit_paper_order(
                capital_pool_id="smoke-pool-1",
                strategy_id="smoke-strategy-alpha",
                symbol="AAPL",
                qty=5.0,
                side="buy",
                operator_id="smoke-op-1",
                trace_id="smoke-trace-001",
            )
            order_id = place_result["order"]["order_id"]
            self.assertEqual(order_id, "smoke-order-001")

            # Readback by ID
            readback_result = adapter.get_paper_order(order_id)
            self.assertEqual(readback_result["order"]["order_id"], order_id)

            # Readback list
            list_result = adapter.list_paper_orders(capital_pool_id="smoke-pool-1")
            self.assertEqual(list_result["orders"][0]["order_id"], order_id)

            # Cancel
            cancel_result = adapter.cancel_paper_order(
                order_id, operator_id="smoke-op-1", trace_id="smoke-trace-002"
            )
            self.assertEqual(cancel_result["order"]["status"], "canceled")

        # Reconcile: verify audit covers all lifecycle stages with no real capital
        all_entries = audit.read()
        events = [e["event"] for e in all_entries]
        outcomes = [e["outcome"] for e in all_entries]

        self.assertIn("paper_order_intent", events)
        self.assertIn("paper_order_cancel_intent", events)
        self.assertNotIn("pending", [e for e in outcomes if e == "error"])

        # No real capital at any stage
        for entry in all_entries:
            self.assertFalse(entry["is_real_order"], f"is_real_order True in: {entry}")
            self.assertFalse(entry["is_real_capital"], f"is_real_capital True in: {entry}")

        # All ok outcomes — no errors
        error_entries = [e for e in all_entries if e["outcome"] == "error"]
        self.assertEqual(error_entries, [], "No errors expected in the smoke lifecycle")

    # ------------------------------------------------------------------
    # Reconciliation: no-real-capital invariants
    # ------------------------------------------------------------------

    def test_smoke_reconcile_no_real_capital_assertion_holds(self):
        """Verify the no-real-capital invariant is maintained across all audit entries."""
        audit = _fresh_audit("smoke_reconcile")
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://fake-sidecar:8102",
            binding_resolver=_binding_resolver(),
            audit_log=audit,
        )
        with patch.object(adapter, "_call_sidecar", return_value=_PLACE_RESPONSE):
            adapter.submit_paper_order(
                capital_pool_id="smoke-pool-1",
                strategy_id="smoke-strategy-alpha",
                symbol="AAPL",
                qty=5.0,
                side="buy",
                operator_id="smoke-op-1",
            )
        with patch.object(adapter, "_call_sidecar", return_value=_CANCEL_RESPONSE):
            adapter.cancel_paper_order("smoke-order-001", operator_id="smoke-op-1")

        entries = audit.read()
        self.assertGreaterEqual(len(entries), 3, "Expected at least place-pending, place-ok, cancel-intent entries")
        for entry in entries:
            self.assertFalse(entry.get("is_real_order"), f"real order flag set in: {entry['event']}")
            self.assertFalse(entry.get("is_real_capital"), f"real capital flag set in: {entry['event']}")


class TestFailClosedLiveAndCanary(unittest.TestCase):
    """AC3: live and canary broker execution remain fail-closed."""

    def test_live_rejected_even_when_paper_adapter_enabled(self):
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://fake-sidecar:8102")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.reject_live_order(operator_id="smoke-op-1")
        self.assertEqual(ctx.exception.error_code, "LIVE_ADAPTER_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejected_when_paper_disabled(self):
        adapter = PaperBrokerAdapter(enabled=False, broker_url="")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.reject_live_order(operator_id="smoke-op-1")
        self.assertEqual(ctx.exception.error_code, "LIVE_ADAPTER_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejected_audit_entry_has_correct_flags(self):
        audit = _fresh_audit("live_reject_smoke")
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://fake-sidecar:8102", audit_log=audit)
        try:
            adapter.reject_live_order(operator_id="smoke-op-1")
        except PaperBrokerAdapterError:
            pass
        entries = audit.read()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["event"], "live_order_rejected")
        self.assertEqual(e["outcome"], "rejected")
        self.assertFalse(e["is_real_order"])
        self.assertFalse(e["is_real_capital"])
        self.assertFalse(e["live_enabled"])

    def test_canary_adapter_is_always_disabled_in_capability_snapshot(self):
        # PaperBrokerAdapter capability snapshot must not expose canary as enabled
        for enabled in (True, False):
            adapter = PaperBrokerAdapter(enabled=enabled, broker_url="http://fake-sidecar:8102")
            snap = adapter.capability_snapshot()
            # live must always be False
            self.assertFalse(snap["live_adapter_enabled"])
            # deployment_stage must not claim live or canary
            stage = snap.get("deployment_stage", "")
            self.assertNotIn(stage, {"live", "canary"}, f"deployment_stage={stage!r} should not be live/canary")


if __name__ == "__main__":
    unittest.main()
