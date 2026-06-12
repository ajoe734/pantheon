"""Test suite for signal_consumer fixes (P3-001)"""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
import logging

logging.basicConfig(level=logging.ERROR)

from services.execution.lean_runtime.signal_consumer import SignalConsumer


class TestSignalProcessingOrder(unittest.TestCase):
    """Test that signals are only marked processed after successful execution"""

    def setUp(self):
        self.store = MagicMock()
        self.consumer = SignalConsumer(store_client=self.store)
        self.mock_algo = MagicMock()

    def test_signal_not_processed_on_execution_error(self):
        """Signal should NOT be marked processed if execution fails"""
        signal = {
            "signal_id": "test-sig-1",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        # Simulate execution error
        with patch('services.execution.lean_runtime.signal_consumer.execute') as mock_execute:
            mock_execute.side_effect = Exception("Broker error")
            self.consumer._execute_one(signal, self.mock_algo)
        
        # Signal should NOT be in processed set after error
        self.assertNotIn("test-sig-1", self.consumer._processed_signal_ids)

    def test_signal_processed_on_success(self):
        """Signal should be marked processed after successful execution"""
        signal = {
            "signal_id": "test-sig-2",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        # Successful execution
        with patch('services.execution.lean_runtime.signal_consumer.execute') as mock_execute:
            self.consumer._execute_one(signal, self.mock_algo)
        
        # Signal should be in processed set after success
        self.assertIn("test-sig-2", self.consumer._processed_signal_ids)

    def test_dry_run_signal_processed(self):
        """Dry-run signals (algo=None) should still be marked processed"""
        signal = {
            "signal_id": "test-dry-1",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        self.consumer._execute_one(signal, algo=None)
        
        # Dry-run signal should still be marked processed
        self.assertIn("test-dry-1", self.consumer._processed_signal_ids)


class TestStalenessCheck(unittest.TestCase):
    """Test that staleness check handles timezone correctly"""

    def setUp(self):
        self.store = MagicMock()
        self.consumer = SignalConsumer(store_client=self.store)

    def test_stale_check_with_naive_algo_time(self):
        """Stale check should work with naive algo.Time (LEAN exchange time)"""
        now_naive = datetime(2026, 4, 6, 12, 0, 0)  # naive
        mock_algo = MagicMock()
        mock_algo.Time = now_naive
        
        # Signal from 12 hours ago (not stale)
        ts_12h_ago = (now_naive - timedelta(hours=12)).isoformat()
        signal = {
            "signal_id": "test-fresh",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": ts_12h_ago,
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        is_stale = self.consumer._is_stale(signal, mock_algo)
        self.assertFalse(is_stale, "Fresh signal should not be stale")

    def test_stale_check_with_old_signal(self):
        """Stale check should reject signals >24h old"""
        now = datetime.now(timezone.utc)
        mock_algo = MagicMock()
        mock_algo.Time = now.replace(tzinfo=None)  # naive
        
        # Signal from 30 hours ago (definitely stale)
        ts_stale = (now - timedelta(hours=30)).isoformat()
        signal = {
            "signal_id": "test-stale",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": ts_stale,
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        is_stale = self.consumer._is_stale(signal, mock_algo)
        self.assertTrue(is_stale, "Signal >24h old should be stale")

    def test_drain_records_stale_signal_noop_feedback(self):
        """Draining a stale signal should emit no-order feedback and mark it processed."""
        now = datetime.now(timezone.utc)
        stale_signal = {
            "signal_id": "test-stale-feedback",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": (now - timedelta(hours=30)).isoformat(),
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
            "metadata": {
                "alpha_source": "unit_stale_filter",
                "market_data": {"close": 150.0},
            },
        }
        self.store.get_pending.return_value = [stale_signal]
        algo = _NoopRecordingAlgo(now.replace(tzinfo=None))

        self.consumer.drain(algo=algo)

        self.assertIn("test-stale-feedback", self.consumer._processed_signal_ids)
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["symbol"], "AAPL.US")
        self.assertEqual(noop["noop_reason"], "stale_signal")
        self.assertEqual(noop["requested_quantity"], 0.5)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["price"], 150.0)
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_filtered")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["metadata"]["filter_reason"], "stale_signal")
        self.assertEqual(noop["metadata"]["alpha_source"], "unit_stale_filter")

    def test_stale_check_with_future_signal_anomaly(self):
        """Stale check should reject signals >1h in future (anomaly)"""
        now = datetime(2026, 4, 6, 12, 0, 0)
        mock_algo = MagicMock()
        mock_algo.Time = now
        
        # Signal from 2 hours in future (anomaly)
        ts_future = (now + timedelta(hours=2)).isoformat()
        signal = {
            "signal_id": "test-future",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": ts_future,
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        is_stale = self.consumer._is_stale(signal, mock_algo)
        self.assertTrue(is_stale, "Signal >1h in future should be rejected as anomaly")

    def test_stale_check_with_unparseable_timestamp_is_not_filtered(self):
        """Unparseable timestamps should not be misclassified as stale."""
        signal = {
            "signal_id": "test-bad-ts",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": "not-a-timestamp",
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }

        is_stale = self.consumer._is_stale(signal, algo=None)

        self.assertFalse(is_stale, "Unparseable timestamp should not be stale-filtered")

    def test_stale_check_without_algo(self):
        """Stale check should work with current UTC time if algo not provided"""
        now = datetime.now(timezone.utc)
        
        # Signal from 12 hours ago (not stale)
        ts_12h_ago = (now - timedelta(hours=12)).isoformat()
        signal = {
            "signal_id": "test-no-algo",
            "version": "1.0",
            "strategy_id": "test",
            "timestamp": ts_12h_ago,
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        
        is_stale = self.consumer._is_stale(signal, algo=None)
        self.assertFalse(is_stale, "Fresh signal should not be stale")


class TestImportPaths(unittest.TestCase):
    """Test that import paths work correctly with lean_runtime directory"""

    def test_imports_work(self):
        """All critical imports should work from lean_runtime package"""
        from services.execution.lean_runtime.signal_consumer import SignalConsumer
        from services.execution.lean_runtime.executor import execute
        from services.execution.lean_runtime.symbol_parser import SymbolParseError

        self.assertIsNotNone(SignalConsumer)
        self.assertIsNotNone(execute)
        self.assertIsNotNone(SymbolParseError)


class _NoopRecordingAlgo:
    def __init__(self, now: datetime) -> None:
        self.Time = now
        self.signal_noops: list[dict] = []

    def RecordSignalNoop(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        noop_reason,
        requested_quantity,
        computed_quantity,
        quantity_type,
        order_type,
        broker_submission_status,
        submitted_to_broker,
        price=None,
        metadata=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "noop_reason": noop_reason,
            "requested_quantity": requested_quantity,
            "computed_quantity": computed_quantity,
            "quantity_type": quantity_type,
            "order_type": order_type,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
            "metadata": dict(metadata or {}),
        }
        if price is not None:
            payload["price"] = price
        self.signal_noops.append(payload)


def _make_signal(signal_id: str, binding_id: str | None = None) -> dict:
    sig = {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "AAPL.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.5,
        "quantity_type": "PERCENT_PORTFOLIO",
    }
    if binding_id is not None:
        sig["binding_id"] = binding_id
    return sig


class TestBindingIsolation(unittest.TestCase):
    """Defense-in-depth: SignalConsumer with binding_id discards misrouted signals."""

    def _consumer(self, binding_id=None):
        store = MagicMock()
        return SignalConsumer(store_client=store, binding_id=binding_id)

    # --- _is_wrong_binding unit tests ---

    def test_no_consumer_binding_passes_all(self):
        """Consumer without binding_id never discards on binding field."""
        c = self._consumer(binding_id=None)
        self.assertFalse(c._is_wrong_binding(_make_signal("s1", binding_id="b-other")))

    def test_matching_binding_passes(self):
        c = self._consumer(binding_id="b-001")
        self.assertFalse(c._is_wrong_binding(_make_signal("s1", binding_id="b-001")))

    def test_mismatched_binding_discards(self):
        c = self._consumer(binding_id="b-001")
        self.assertTrue(c._is_wrong_binding(_make_signal("s1", binding_id="b-002")))

    def test_signal_without_binding_field_passes(self):
        """Signals without a binding_id field are legacy/unrouted — always allowed."""
        c = self._consumer(binding_id="b-001")
        self.assertFalse(c._is_wrong_binding(_make_signal("s1", binding_id=None)))

    def test_empty_string_binding_field_treated_as_absent(self):
        c = self._consumer(binding_id="b-001")
        sig = _make_signal("s1")
        sig["binding_id"] = ""
        self.assertFalse(c._is_wrong_binding(sig))

    def _mock_algo(self):
        algo = MagicMock()
        # Give algo a real datetime so _is_stale() doesn't blow up on type comparison.
        algo.Time = datetime.now(timezone.utc).replace(tzinfo=None)
        return algo

    # --- drain integration: binding filter applied end-to-end ---

    def test_drain_records_symbol_parse_error_noop_feedback(self):
        """Execution errors should not leave signals invisible or unprocessed."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        bad_symbol_signal = _make_signal("s-symbol-parse-error")
        bad_symbol_signal["symbol"] = "2330.TW"
        bad_symbol_signal["metadata"] = {
            "alpha_source": "unit_symbol_parse_error",
            "market_data": {"close": 930.0},
        }
        store = MagicMock()
        store.get_pending.return_value = [bad_symbol_signal]
        c = SignalConsumer(store_client=store)
        algo = _NoopRecordingAlgo(now.replace(tzinfo=None))

        c.drain(algo=algo)

        self.assertEqual(c._processed_signal_ids, {"s-symbol-parse-error"})
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["symbol"], "2330.TW")
        self.assertEqual(noop["noop_reason"], "symbol_parse_error")
        self.assertEqual(noop["requested_quantity"], 0.5)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["price"], 930.0)
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_filtered")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["metadata"]["filter_reason"], "symbol_parse_error")
        self.assertEqual(noop["metadata"]["execution_error_type"], "ExecutionError")
        self.assertEqual(noop["metadata"]["execution_error_stage"], "execute_signal")
        self.assertEqual(noop["metadata"]["execution_error_symbol"], "2330.TW")
        self.assertIn("Unknown market code 'TW'", noop["metadata"]["execution_error_message"])
        self.assertEqual(noop["metadata"]["alpha_source"], "unit_symbol_parse_error")

    def test_drain_records_conflict_loser_noop_feedback(self):
        """Same-symbol conflict losers are terminal no-order outcomes."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        loser = _make_signal("s-conflict-loser")
        loser["timestamp"] = (now - timedelta(minutes=2)).isoformat()
        loser["metadata"] = {"alpha_source": "unit_conflict_loser", "market_data": {"close": 153.0}}
        winner = _make_signal("s-conflict-winner")
        winner["timestamp"] = now.isoformat()
        winner["metadata"] = {"alpha_source": "unit_conflict_winner", "market_data": {"close": 153.0}}
        store = MagicMock()
        store.get_pending.return_value = [loser, winner]
        c = SignalConsumer(store_client=store)
        algo = _NoopRecordingAlgo(now.replace(tzinfo=None))
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=algo)
        mock_exec.assert_called_once_with(winner, algo)
        self.assertEqual(c._processed_signal_ids, {"s-conflict-loser", "s-conflict-winner"})
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["symbol"], "AAPL.US")
        self.assertEqual(noop["noop_reason"], "signal_conflict_loser")
        self.assertEqual(noop["requested_quantity"], 0.5)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["price"], 153.0)
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_filtered")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["metadata"]["filter_reason"], "signal_conflict_loser")
        self.assertEqual(noop["metadata"]["conflict_loser_signal_id"], "s-conflict-loser")
        self.assertEqual(noop["metadata"]["conflict_winner_signal_id"], "s-conflict-winner")
        self.assertEqual(noop["metadata"]["conflict_symbol"], "AAPL.US")
        self.assertEqual(
            noop["metadata"]["conflict_resolution_rule"],
            "last_write_wins_timestamp_then_confidence",
        )

    def test_drain_records_duplicate_signal_noop_feedback(self):
        """Duplicate signal IDs are idempotent and still emit terminal no-order feedback."""
        duplicate_signal = _make_signal("s-duplicate")
        duplicate_signal["metadata"] = {"alpha_source": "unit_duplicate_filter", "market_data": {"close": 152.0}}
        store = MagicMock()
        store.get_pending.return_value = [duplicate_signal]
        c = SignalConsumer(store_client=store)
        c._processed_signal_ids.add("s-duplicate")
        algo = _NoopRecordingAlgo(datetime.now(timezone.utc).replace(tzinfo=None))
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=algo)
        mock_exec.assert_not_called()
        self.assertEqual(c._processed_signal_ids, {"s-duplicate"})
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["symbol"], "AAPL.US")
        self.assertEqual(noop["noop_reason"], "duplicate_signal_id")
        self.assertEqual(noop["requested_quantity"], 0.5)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["price"], 152.0)
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_filtered")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["metadata"]["filter_reason"], "duplicate_signal_id")
        self.assertEqual(noop["metadata"]["duplicate_signal_id"], "s-duplicate")
        self.assertTrue(noop["metadata"]["idempotent_replay"])
        self.assertEqual(noop["metadata"]["alpha_source"], "unit_duplicate_filter")

    def test_drain_records_wrong_binding_noop_feedback(self):
        """Signals for a different binding emit terminal no-order feedback."""
        wrong_binding_signal = _make_signal("s-wrong", binding_id="b-other")
        wrong_binding_signal["metadata"] = {"alpha_source": "unit_binding_filter", "market_data": {"close": 151.0}}
        store = MagicMock()
        store.get_pending.return_value = [wrong_binding_signal]
        c = SignalConsumer(store_client=store, binding_id="b-mine")
        algo = _NoopRecordingAlgo(datetime.now(timezone.utc).replace(tzinfo=None))
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=algo)
        mock_exec.assert_not_called()
        self.assertIn("s-wrong", c._processed_signal_ids)
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["symbol"], "AAPL.US")
        self.assertEqual(noop["noop_reason"], "binding_mismatch")
        self.assertEqual(noop["requested_quantity"], 0.5)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["price"], 151.0)
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_filtered")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["metadata"]["filter_reason"], "binding_mismatch")
        self.assertEqual(noop["metadata"]["expected_binding_id"], "b-mine")
        self.assertEqual(noop["metadata"]["signal_binding_id"], "b-other")
        self.assertEqual(noop["metadata"]["alpha_source"], "unit_binding_filter")

    def test_drain_executes_matching_binding_signal(self):
        """Signals for the consumer's binding are executed normally."""
        right_signal = _make_signal("s-right", binding_id="b-mine")
        store = MagicMock()
        store.get_pending.return_value = [right_signal]
        c = SignalConsumer(store_client=store, binding_id="b-mine")
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=self._mock_algo())
        mock_exec.assert_called_once()
        self.assertIn("s-right", c._processed_signal_ids)

    def test_drain_executes_unrouted_signal_regardless_of_consumer_binding(self):
        """Signals without binding_id pass through even when consumer has binding_id."""
        unrouted = _make_signal("s-legacy")  # no binding_id field
        store = MagicMock()
        store.get_pending.return_value = [unrouted]
        c = SignalConsumer(store_client=store, binding_id="b-mine")
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=self._mock_algo())
        mock_exec.assert_called_once()
        self.assertIn("s-legacy", c._processed_signal_ids)


class TestPendingSignalStoreQueueKey(unittest.TestCase):
    """binding_queue_key() and build_pending_signal_store() auto-derive behaviour."""

    def test_binding_queue_key_format(self):
        from services.execution.lean_runtime.pending_signal_store import binding_queue_key
        self.assertEqual(binding_queue_key("b-001"), "pantheon:signals:pending:b-001")

    def test_binding_queue_key_prefix_constant(self):
        from services.execution.lean_runtime.pending_signal_store import BINDING_QUEUE_KEY_PREFIX
        self.assertEqual(BINDING_QUEUE_KEY_PREFIX, "pantheon:signals:pending")

    def test_build_returns_memory_store_for_empty_url(self):
        """Empty URL → InMemoryPendingSignalStore regardless of queue_key."""
        import os
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import (
            build_pending_signal_store, InMemoryPendingSignalStore,
        )
        with patch.dict(os.environ, {}, clear=False):
            store = build_pending_signal_store("")
        self.assertIsInstance(store, InMemoryPendingSignalStore)

    def _make_redis_store(self, url, **kwargs):
        """Build a RedisPendingSignalStore with a mocked redis module."""
        import os
        import sys
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import build_pending_signal_store

        mock_redis_module = MagicMock()
        mock_redis_module.Redis.from_url.return_value = MagicMock()
        with patch.dict(sys.modules, {"redis": mock_redis_module}):
            return build_pending_signal_store(url, **kwargs)

    def test_build_auto_derives_from_binding_env(self):
        """When PANTHEON_RUNTIME_BINDING_ID is set and queue_key is default, derive scoped key."""
        import os
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import RedisPendingSignalStore
        with patch.dict(os.environ, {
            "PANTHEON_RUNTIME_BINDING_ID": "b-env-001",
            "PANTHEON_SIGNAL_QUEUE_KEY": "",
        }):
            store = self._make_redis_store("redis://localhost:6379")
        self.assertIsInstance(store, RedisPendingSignalStore)
        self.assertEqual(store._queue_key, "pantheon:signals:pending:b-env-001")

    def test_build_explicit_queue_key_overrides_env(self):
        """Explicit queue_key parameter takes priority over env-derived key."""
        import os
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import RedisPendingSignalStore
        with patch.dict(os.environ, {"PANTHEON_RUNTIME_BINDING_ID": "b-should-not-use"}):
            store = self._make_redis_store(
                "redis://localhost:6379",
                queue_key="pantheon:signals:pending:b-explicit",
            )
        self.assertIsInstance(store, RedisPendingSignalStore)
        self.assertEqual(store._queue_key, "pantheon:signals:pending:b-explicit")

    def test_build_prefers_signal_queue_key_env_over_binding_env(self):
        """PANTHEON_SIGNAL_QUEUE_KEY takes priority over PANTHEON_RUNTIME_BINDING_ID."""
        import os
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import RedisPendingSignalStore
        with patch.dict(os.environ, {
            "PANTHEON_SIGNAL_QUEUE_KEY": "pantheon:signals:pending:b-from-reconciler",
            "PANTHEON_RUNTIME_BINDING_ID": "b-should-not-use",
        }):
            store = self._make_redis_store("redis://localhost:6379")
        self.assertIsInstance(store, RedisPendingSignalStore)
        self.assertEqual(store._queue_key, "pantheon:signals:pending:b-from-reconciler")


if __name__ == "__main__":
    unittest.main()
