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


if __name__ == "__main__":
    unittest.main()
