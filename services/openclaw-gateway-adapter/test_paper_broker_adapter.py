"""Unit tests for paper_broker_adapter.py — gated paper order adapter.

Covers:
- gate check (disabled by default)
- capital/strategy/operator binding validation
- live order always rejected regardless of gate state
- sidecar call with audit trail (intent → ok / error)
- broker sidecar not configured → 503
- capability snapshot fields
- audit log read/filter
"""
from __future__ import annotations

import os
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

_TMP = Path("/tmp/pantheon-test-paper-broker-adapter")
_TMP.mkdir(parents=True, exist_ok=True)


def _audit(name: str) -> PaperBrokerAuditLog:
    path = _TMP / f"{name}.jsonl"
    if path.exists():
        path.unlink()
    return PaperBrokerAuditLog(path=str(path))


def _active_binding(**overrides):
    binding = {
        "binding_id": "rb-paper-1",
        "runtime_id": "rt-paper-1",
        "capital_pool_id": "pool-1",
        "artifact_id": "artifact-alpha",
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "status": "active",
        "plan_id": "plan-paper-1",
        "persona_capital_binding_id": "pcb-1",
        "metadata": {"strategy_id": "strat-1"},
    }
    binding.update(overrides)
    return binding


_DEFAULT_BINDING = object()


def _resolver(binding=_DEFAULT_BINDING):
    binding = _active_binding() if binding is _DEFAULT_BINDING else binding

    def resolve(capital_pool_id: str):
        if binding is None or binding.get("capital_pool_id") != capital_pool_id:
            return None
        return binding

    return resolve


class TestPaperBrokerAdapterGate(unittest.TestCase):
    def test_disabled_by_default_via_env(self):
        with patch.dict(os.environ, {"OPENCLAW_PAPER_ADAPTER_ENABLED": ""}, clear=False):
            adapter = PaperBrokerAdapter()
        self.assertFalse(adapter.enabled)

    def test_gate_check_raises_paper_adapter_disabled(self):
        adapter = PaperBrokerAdapter(enabled=False, broker_url="http://broker:8102")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "PAPER_ADAPTER_DISABLED")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_gate_check_raises_for_list_when_disabled(self):
        adapter = PaperBrokerAdapter(enabled=False, broker_url="http://broker:8102")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.list_paper_orders()
        self.assertEqual(ctx.exception.error_code, "PAPER_ADAPTER_DISABLED")

    def test_gate_check_raises_for_get_when_disabled(self):
        adapter = PaperBrokerAdapter(enabled=False, broker_url="http://broker:8102")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.get_paper_order("order-123")
        self.assertEqual(ctx.exception.error_code, "PAPER_ADAPTER_DISABLED")


class TestPaperBrokerAdapterBindingCheck(unittest.TestCase):
    def _enabled_no_sidecar(self) -> PaperBrokerAdapter:
        return PaperBrokerAdapter(enabled=True, broker_url="", binding_resolver=_resolver())

    def test_missing_capital_pool_id_rejected(self):
        adapter = self._enabled_no_sidecar()
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "CAPITAL_POOL_REQUIRED")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_strategy_id_rejected(self):
        adapter = self._enabled_no_sidecar()
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "STRATEGY_ID_REQUIRED")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_operator_id_rejected(self):
        adapter = self._enabled_no_sidecar()
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="",
            )
        self.assertEqual(ctx.exception.error_code, "OPERATOR_REQUIRED")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_binding_check_before_sidecar_call(self):
        """Binding check fires before trying to reach the sidecar."""
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://broker:8102")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "CAPITAL_POOL_REQUIRED")

    def test_runtime_manager_required_when_gate_open(self):
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://broker:8102", runtime_manager_url="")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "RUNTIME_MANAGER_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_missing_active_runtime_binding_rejected(self):
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://broker:8102", binding_resolver=_resolver(None))
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "RUNTIME_BINDING_NOT_FOUND")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_non_paper_runtime_binding_rejected(self):
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://broker:8102",
            binding_resolver=_resolver(_active_binding(deployment_mode="live")),
        )
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "PAPER_RUNTIME_BINDING_REQUIRED")

    def test_strategy_mismatch_rejected(self):
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://broker:8102", binding_resolver=_resolver())
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="other-strategy",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "STRATEGY_BINDING_MISMATCH")


class TestPaperBrokerAdapterLiveRejection(unittest.TestCase):
    def test_live_rejected_when_paper_disabled(self):
        adapter = PaperBrokerAdapter(enabled=False, broker_url="")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.reject_live_order(operator_id="op-1")
        self.assertEqual(ctx.exception.error_code, "LIVE_ADAPTER_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejected_even_when_paper_enabled(self):
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://broker:8102")
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.reject_live_order(operator_id="op-1")
        self.assertEqual(ctx.exception.error_code, "LIVE_ADAPTER_DISABLED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_live_rejection_records_audit_entry(self):
        audit = _audit("live_rejection")
        adapter = PaperBrokerAdapter(enabled=True, broker_url="http://broker:8102", audit_log=audit)
        try:
            adapter.reject_live_order(operator_id="op-1")
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

    def test_live_rejection_records_audit_without_operator_id(self):
        audit = _audit("live_rejection_no_op")
        adapter = PaperBrokerAdapter(enabled=False, broker_url="", audit_log=audit)
        try:
            adapter.reject_live_order(operator_id=None)
        except PaperBrokerAdapterError:
            pass
        entries = audit.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event"], "live_order_rejected")


class TestPaperBrokerAdapterSidecarCall(unittest.TestCase):
    def test_submit_calls_sidecar_and_records_intent_then_ok(self):
        audit = _audit("submit_ok")
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://broker:8102",
            binding_resolver=_resolver(),
            audit_log=audit,
        )
        mock_response = {
            "status": "ok",
            "order": {
                "order_id": "abc123",
                "fill_price": 100.0,
                "fill_qty": 10.0,
                "status": "filled",
            },
        }
        with patch.object(adapter, "_call_sidecar", return_value=mock_response):
            result = adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(result["order"]["order_id"], "abc123")
        entries = audit.read()
        self.assertEqual(len(entries), 2)
        pending = entries[0]
        ok = entries[1]
        self.assertEqual(pending["event"], "paper_order_intent")
        self.assertEqual(pending["outcome"], "pending")
        self.assertFalse(pending["is_real_order"])
        self.assertFalse(pending["is_real_capital"])
        self.assertEqual(pending["deployment_stage"], "paper")
        self.assertEqual(pending["runtime_binding_id"], "rb-paper-1")
        self.assertEqual(pending["persona_capital_binding_id"], "pcb-1")
        self.assertEqual(ok["outcome"], "ok")
        self.assertEqual(ok["order_id"], "abc123")
        self.assertTrue(ok["sim_fill_flag"])

    def test_submit_records_error_on_sidecar_failure(self):
        audit = _audit("submit_error")
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://broker:8102",
            binding_resolver=_resolver(),
            audit_log=audit,
        )
        with patch.object(
            adapter,
            "_call_sidecar",
            side_effect=PaperBrokerAdapterError("SIDECAR_ERROR", "unavailable", status_code=503),
        ):
            with self.assertRaises(PaperBrokerAdapterError) as ctx:
                adapter.submit_paper_order(
                    capital_pool_id="pool-1",
                    strategy_id="strat-1",
                    symbol="AAPL",
                    qty=10.0,
                    side="buy",
                    operator_id="op-1",
                )
        self.assertEqual(ctx.exception.error_code, "SIDECAR_ERROR")
        entries = audit.read()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["outcome"], "pending")
        self.assertEqual(entries[1]["outcome"], "error")
        self.assertEqual(entries[1]["error_code"], "SIDECAR_ERROR")

    def test_no_sidecar_url_raises_503(self):
        adapter = PaperBrokerAdapter(enabled=True, broker_url="", binding_resolver=_resolver())
        with self.assertRaises(PaperBrokerAdapterError) as ctx:
            adapter.submit_paper_order(
                capital_pool_id="pool-1",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "BROKER_SIDECAR_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.status_code, 503)


class TestPaperBrokerAdapterCapabilitySnapshot(unittest.TestCase):
    def test_snapshot_when_paper_enabled(self):
        adapter = PaperBrokerAdapter(
            enabled=True,
            broker_url="http://broker:8102",
            binding_resolver=_resolver(),
        )
        snap = adapter.capability_snapshot()
        self.assertTrue(snap["paper_adapter_enabled"])
        self.assertFalse(snap["live_adapter_enabled"])
        self.assertFalse(snap["is_real_order"])
        self.assertFalse(snap["is_real_capital"])
        self.assertEqual(snap["deployment_stage"], "paper")
        self.assertTrue(snap["broker_sidecar_configured"])
        self.assertTrue(snap["runtime_manager_configured"])
        self.assertEqual(snap["runtime_binding_check"], "required_for_submit")

    def test_snapshot_when_paper_disabled(self):
        adapter = PaperBrokerAdapter(enabled=False, broker_url="")
        snap = adapter.capability_snapshot()
        self.assertFalse(snap["paper_adapter_enabled"])
        self.assertFalse(snap["live_adapter_enabled"])
        self.assertEqual(snap["deployment_stage"], "none")
        self.assertFalse(snap["broker_sidecar_configured"])


class TestPaperBrokerAuditLog(unittest.TestCase):
    def test_record_and_read_all(self):
        log = _audit("audit_rw")
        log.record({"event": "e1", "operator_id": "op-1", "capital_pool_id": "pool-A"})
        log.record({"event": "e2", "operator_id": "op-2", "capital_pool_id": "pool-B"})
        entries = log.read()
        self.assertEqual(len(entries), 2)

    def test_filter_by_operator_id(self):
        log = _audit("audit_filter_op")
        log.record({"event": "e1", "operator_id": "op-1", "capital_pool_id": "pool-A"})
        log.record({"event": "e2", "operator_id": "op-2", "capital_pool_id": "pool-A"})
        entries = log.read(operator_id="op-1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event"], "e1")

    def test_filter_by_capital_pool_id(self):
        log = _audit("audit_filter_pool")
        log.record({"event": "e1", "operator_id": "op-1", "capital_pool_id": "pool-A"})
        log.record({"event": "e2", "operator_id": "op-1", "capital_pool_id": "pool-B"})
        entries = log.read(capital_pool_id="pool-B")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event"], "e2")

    def test_read_returns_empty_when_no_file(self):
        path = _TMP / "nonexistent_log_xyz.jsonl"
        if path.exists():
            path.unlink()
        log = PaperBrokerAuditLog(path=str(path))
        entries = log.read()
        self.assertEqual(entries, [])

    def test_entries_have_timestamp(self):
        log = _audit("audit_ts")
        log.record({"event": "test"})
        entries = log.read()
        self.assertIn("at", entries[0])

    def test_limit_applied(self):
        log = _audit("audit_limit")
        for i in range(10):
            log.record({"event": f"e{i}"})
        entries = log.read(limit=3)
        self.assertEqual(len(entries), 3)


if __name__ == "__main__":
    unittest.main()
