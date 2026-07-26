"""Tests for runtime-aware signal isolation (LOOP-AUTO-RT-004).

Acceptance coverage:
  - Multiple runtime consumers cannot consume each other's signals.
  - Mismatched runtime_id or capital_pool_id signals are rejected.
  - Dead-letter or requeue behavior (DLQ) is tested.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.execution.lean_runtime.pending_signal_store import (
    InMemoryPendingSignalStore,
    binding_dlq_key,
    BINDING_DLQ_KEY_PREFIX,
    BINDING_QUEUE_KEY_PREFIX,
)


def _signal(
    signal_id: str,
    *,
    binding_id: str | None = None,
    runtime_id: str | None = None,
    capital_pool_id: str | None = None,
) -> dict:
    sig: dict = {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "test-strategy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "AAPL.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.1,
        "quantity_type": "PERCENT_PORTFOLIO",
    }
    if binding_id is not None:
        sig["binding_id"] = binding_id
    if runtime_id is not None:
        sig["runtime_id"] = runtime_id
    if capital_pool_id is not None:
        sig.setdefault("metadata", {})["capital_pool_id"] = capital_pool_id
    return sig


class _RecordingAlgo:
    def __init__(self) -> None:
        self.Time = datetime.now(timezone.utc).replace(tzinfo=None)
        self.noops: list[dict] = []

    def RecordSignalNoop(self, symbol, *, signal_id, noop_reason, **kwargs):  # noqa: N802
        self.noops.append({"symbol": symbol, "signal_id": signal_id, "noop_reason": noop_reason, **kwargs})


# ---------------------------------------------------------------------------
# Runtime-id isolation
# ---------------------------------------------------------------------------

class TestRuntimeIdIsolation(unittest.TestCase):

    def _consumer(self, runtime_id=None):
        store = InMemoryPendingSignalStore()
        return SignalConsumer(store_client=store, runtime_id=runtime_id), store

    def test_no_consumer_runtime_id_passes_all(self):
        """Consumer without runtime_id never rejects on runtime_id field."""
        c, _ = self._consumer(runtime_id=None)
        sig = _signal("s1", runtime_id="rt-other")
        self.assertFalse(c._is_wrong_runtime(sig))

    def test_matching_runtime_passes(self):
        c, _ = self._consumer(runtime_id="rt-001")
        self.assertFalse(c._is_wrong_runtime(_signal("s1", runtime_id="rt-001")))

    def test_mismatched_runtime_discards(self):
        c, _ = self._consumer(runtime_id="rt-001")
        self.assertTrue(c._is_wrong_runtime(_signal("s1", runtime_id="rt-002")))

    def test_signal_without_runtime_field_fails_closed(self):
        """Unrouted signals (no runtime_id) are rejected in governed paper mode."""
        c, _ = self._consumer(runtime_id="rt-001")
        self.assertTrue(c._is_wrong_runtime(_signal("s1")))  # no runtime_id key

    def test_empty_runtime_field_fails_closed(self):
        c, _ = self._consumer(runtime_id="rt-001")
        sig = _signal("s1")
        sig["runtime_id"] = ""
        self.assertTrue(c._is_wrong_runtime(sig))

    def test_drain_discards_wrong_runtime_signal(self):
        """drain() must not execute a signal destined for another runtime."""
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("wrong-rt", runtime_id="rt-other"))
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        algo = _RecordingAlgo()
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=algo)
        mock_exec.assert_not_called()
        self.assertEqual(len(algo.noops), 1)
        self.assertEqual(algo.noops[0]["noop_reason"], "runtime_mismatch")
        self.assertEqual(algo.noops[0]["metadata"]["expected_runtime_id"], "rt-mine")
        self.assertEqual(algo.noops[0]["metadata"]["signal_runtime_id"], "rt-other")

    def test_drain_executes_matching_runtime_signal(self):
        """Signals matching the consumer's runtime_id are executed normally."""
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("right-rt", runtime_id="rt-mine"))
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=_RecordingAlgo())
        mock_exec.assert_called_once()
        self.assertIn("right-rt", c._processed_signal_ids)

    def test_drain_discards_unrouted_signal_in_governed_mode(self):
        """Signals without runtime_id field are rejected in governed paper mode."""
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("legacy"))  # no runtime_id
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=_RecordingAlgo())
        mock_exec.assert_not_called()

    def test_multiple_consumers_cannot_steal_each_others_signals(self):
        """Two consumers with different runtime_ids each reject the other's signals."""
        store_a = InMemoryPendingSignalStore()
        store_b = InMemoryPendingSignalStore()
        sig_for_a = _signal("sig-a", runtime_id="rt-A")
        sig_for_b = _signal("sig-b", runtime_id="rt-B")
        # Consumer A gets sig_for_b from its queue (simulating a misroute)
        store_a.enqueue(sig_for_b)
        # Consumer B gets sig_for_a from its queue
        store_b.enqueue(sig_for_a)
        c_a = SignalConsumer(store_client=store_a, runtime_id="rt-A")
        c_b = SignalConsumer(store_client=store_b, runtime_id="rt-B")
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c_a.drain(algo=_RecordingAlgo())
        c_b.drain(algo=_RecordingAlgo())
        mock_exec.assert_not_called()
        self.assertEqual(store_a.dlq_depth(), 1)
        self.assertEqual(store_b.dlq_depth(), 1)


# ---------------------------------------------------------------------------
# Capital-pool isolation
# ---------------------------------------------------------------------------

class TestCapitalPoolIsolation(unittest.TestCase):

    def _consumer(self, capital_pool_id=None):
        store = InMemoryPendingSignalStore()
        return SignalConsumer(store_client=store, capital_pool_id=capital_pool_id), store

    def test_no_consumer_pool_passes_all(self):
        c, _ = self._consumer(capital_pool_id=None)
        self.assertFalse(c._is_wrong_capital_pool(_signal("s1", capital_pool_id="pool-other")))

    def test_matching_pool_passes(self):
        c, _ = self._consumer(capital_pool_id="pool-001")
        self.assertFalse(c._is_wrong_capital_pool(_signal("s1", capital_pool_id="pool-001")))

    def test_mismatched_pool_discards(self):
        c, _ = self._consumer(capital_pool_id="pool-001")
        self.assertTrue(c._is_wrong_capital_pool(_signal("s1", capital_pool_id="pool-other")))

    def test_signal_without_pool_field_fails_closed(self):
        """Signals without metadata.capital_pool_id are rejected in governed paper mode."""
        c, _ = self._consumer(capital_pool_id="pool-001")
        self.assertTrue(c._is_wrong_capital_pool(_signal("s1")))

    def test_signal_with_empty_pool_field_fails_closed(self):
        c, _ = self._consumer(capital_pool_id="pool-001")
        sig = _signal("s1")
        sig.setdefault("metadata", {})["capital_pool_id"] = ""
        self.assertTrue(c._is_wrong_capital_pool(sig))

    def test_drain_discards_wrong_pool_signal(self):
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("wrong-pool", capital_pool_id="pool-theirs"))
        c = SignalConsumer(store_client=store, capital_pool_id="pool-mine")
        algo = _RecordingAlgo()
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=algo)
        mock_exec.assert_not_called()
        self.assertIn("wrong-pool", c._processed_signal_ids)
        self.assertEqual(len(algo.noops), 1)
        noop = algo.noops[0]
        self.assertEqual(noop["noop_reason"], "capital_pool_mismatch")
        self.assertEqual(noop["metadata"]["expected_capital_pool_id"], "pool-mine")
        self.assertEqual(noop["metadata"]["signal_capital_pool_id"], "pool-theirs")

    def test_drain_executes_matching_pool_signal(self):
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("right-pool", capital_pool_id="pool-mine"))
        c = SignalConsumer(store_client=store, capital_pool_id="pool-mine")
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=_RecordingAlgo())
        mock_exec.assert_called_once()
        self.assertIn("right-pool", c._processed_signal_ids)

    def test_mismatched_persona_pool_signals_rejected(self):
        """Persona A's pool signals must not be consumed by persona B's runtime."""
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("persona-a-sig", capital_pool_id="pool-persona-a"))
        c = SignalConsumer(store_client=store, capital_pool_id="pool-persona-b")
        algo = _RecordingAlgo()
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=algo)
        mock_exec.assert_not_called()
        self.assertEqual(algo.noops[0]["noop_reason"], "capital_pool_mismatch")


# ---------------------------------------------------------------------------
# DLQ routing
# ---------------------------------------------------------------------------

class TestDLQRouting(unittest.TestCase):

    def test_binding_mismatch_routes_to_dlq(self):
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("s-dlq-binding", binding_id="b-other"))
        c = SignalConsumer(store_client=store, binding_id="b-mine")
        c.drain(algo=_RecordingAlgo())
        self.assertEqual(store.dlq_depth(), 1)
        items = store.get_dlq()
        self.assertEqual(items[0]["signal_id"], "s-dlq-binding")
        self.assertEqual(items[0]["_dlq_reason"], "binding_mismatch")

    def test_runtime_mismatch_routes_to_dlq(self):
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("s-dlq-rt", runtime_id="rt-other"))
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        c.drain(algo=_RecordingAlgo())
        self.assertEqual(store.dlq_depth(), 1)
        items = store.get_dlq()
        self.assertEqual(items[0]["signal_id"], "s-dlq-rt")
        self.assertEqual(items[0]["_dlq_reason"], "runtime_mismatch")

    def test_capital_pool_mismatch_routes_to_dlq(self):
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("s-dlq-pool", capital_pool_id="pool-other"))
        c = SignalConsumer(store_client=store, capital_pool_id="pool-mine")
        c.drain(algo=_RecordingAlgo())
        self.assertEqual(store.dlq_depth(), 1)
        items = store.get_dlq()
        self.assertEqual(items[0]["signal_id"], "s-dlq-pool")
        self.assertEqual(items[0]["_dlq_reason"], "capital_pool_mismatch")

    def test_matched_signal_does_not_hit_dlq(self):
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("s-ok", runtime_id="rt-mine", capital_pool_id="pool-mine"))
        c = SignalConsumer(store_client=store, runtime_id="rt-mine", capital_pool_id="pool-mine")
        with patch("services.execution.lean_runtime.signal_consumer.execute"):
            c.drain(algo=_RecordingAlgo())
        self.assertEqual(store.dlq_depth(), 0)

    def test_dlq_enqueue_failure_does_not_break_signal_path(self):
        """If DLQ write raises, the signal-path must still complete (no exception)."""
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("s-dlq-err", runtime_id="rt-other"))

        def _bad_dlq(payload):
            raise OSError("redis down")

        store.enqueue_dlq = _bad_dlq  # type: ignore[method-assign]
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        algo = _RecordingAlgo()
        # Should not raise; DLQ write is best-effort
        c.drain(algo=algo)
        self.assertIn("s-dlq-err", c._processed_signal_ids)
        self.assertEqual(algo.noops[0]["noop_reason"], "runtime_mismatch")

    def test_store_without_enqueue_dlq_works_fine(self):
        """Consumer must work with stores that have no DLQ support."""
        store = MagicMock()
        store.get_pending.return_value = [_signal("s-no-dlq", runtime_id="rt-other")]
        store.is_processed.return_value = False
        # Ensure enqueue_dlq is absent
        del store.enqueue_dlq
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        algo = _RecordingAlgo()
        c.drain(algo=algo)
        self.assertEqual(algo.noops[0]["noop_reason"], "runtime_mismatch")

    def test_dlq_drain_and_replay(self):
        """DLQ items can be retrieved for inspection or replay."""
        store = InMemoryPendingSignalStore()
        store.enqueue(_signal("dlq-1", runtime_id="rt-other"))
        store.enqueue(_signal("dlq-2", runtime_id="rt-other"))
        c = SignalConsumer(store_client=store, runtime_id="rt-mine")
        c.drain(algo=_RecordingAlgo())
        self.assertEqual(store.dlq_depth(), 2)
        # Partial drain
        first = store.get_dlq(limit=1)
        self.assertEqual(len(first), 1)
        self.assertEqual(store.dlq_depth(), 1)
        second = store.get_dlq()
        self.assertEqual(len(second), 1)
        self.assertEqual(store.dlq_depth(), 0)


# ---------------------------------------------------------------------------
# DLQ key helpers
# ---------------------------------------------------------------------------

class TestDLQKeyHelpers(unittest.TestCase):

    def test_binding_dlq_key_format(self):
        self.assertEqual(binding_dlq_key("b-001"), "pantheon:signals:dlq:b-001")

    def test_dlq_key_prefix_constant(self):
        self.assertEqual(BINDING_DLQ_KEY_PREFIX, "pantheon:signals:dlq")

    def test_redis_store_derives_dlq_key_from_queue_key(self):
        import sys
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import RedisPendingSignalStore

        mock_redis = MagicMock()
        mock_redis.Redis.from_url.return_value = MagicMock()
        with patch.dict(sys.modules, {"redis": mock_redis}):
            store = RedisPendingSignalStore(
                "redis://localhost:6379",
                queue_key="pantheon:signals:pending:b-test",
            )
        self.assertEqual(store._queue_key, "pantheon:signals:pending:b-test")
        self.assertEqual(store._dlq_key, "pantheon:signals:dlq:b-test")

    def test_redis_store_dlq_key_bare_default(self):
        """Bare default queue key maps to bare DLQ key."""
        import sys
        from unittest.mock import patch
        from services.execution.lean_runtime.pending_signal_store import (
            RedisPendingSignalStore,
            BINDING_QUEUE_KEY_PREFIX,
        )
        mock_redis = MagicMock()
        mock_redis.Redis.from_url.return_value = MagicMock()
        with patch.dict(sys.modules, {"redis": mock_redis}):
            store = RedisPendingSignalStore("redis://localhost:6379")
        self.assertEqual(store._queue_key, BINDING_QUEUE_KEY_PREFIX)
        self.assertEqual(store._dlq_key, BINDING_DLQ_KEY_PREFIX)


# ---------------------------------------------------------------------------
# Combined runtime + binding isolation
# ---------------------------------------------------------------------------

class TestCombinedIsolation(unittest.TestCase):

    def test_all_fields_match_executes(self):
        store = InMemoryPendingSignalStore()
        sig = _signal("all-match",
                       binding_id="b-1", runtime_id="rt-1", capital_pool_id="pool-1")
        store.enqueue(sig)
        c = SignalConsumer(
            store_client=store,
            binding_id="b-1",
            runtime_id="rt-1",
            capital_pool_id="pool-1",
        )
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            c.drain(algo=_RecordingAlgo())
        mock_exec.assert_called_once()
        self.assertEqual(store.dlq_depth(), 0)

    def test_binding_checked_before_runtime(self):
        """When binding doesn't match, DLQ reason is binding_mismatch not runtime_mismatch."""
        store = InMemoryPendingSignalStore()
        sig = _signal("order-test",
                       binding_id="b-wrong", runtime_id="rt-wrong")
        store.enqueue(sig)
        c = SignalConsumer(
            store_client=store,
            binding_id="b-mine",
            runtime_id="rt-mine",
        )
        algo = _RecordingAlgo()
        c.drain(algo=algo)
        self.assertEqual(algo.noops[0]["noop_reason"], "binding_mismatch")
        self.assertEqual(store.get_dlq()[0]["_dlq_reason"], "binding_mismatch")

    def test_runtime_checked_before_capital_pool(self):
        """When runtime doesn't match, DLQ reason is runtime_mismatch."""
        store = InMemoryPendingSignalStore()
        sig = _signal("order-test2",
                       runtime_id="rt-wrong", capital_pool_id="pool-wrong")
        store.enqueue(sig)
        c = SignalConsumer(
            store_client=store,
            runtime_id="rt-mine",
            capital_pool_id="pool-mine",
        )
        algo = _RecordingAlgo()
        c.drain(algo=algo)
        self.assertEqual(algo.noops[0]["noop_reason"], "runtime_mismatch")
        self.assertEqual(store.get_dlq()[0]["_dlq_reason"], "runtime_mismatch")


# ---------------------------------------------------------------------------
# Redis claim & visibility timeout tests (B1)
# ---------------------------------------------------------------------------

class TestRedisPendingSignalStoreClaimVisibility(unittest.TestCase):

    def test_reclaim_expired_inflight_only_reclaims_expired_items(self):
        import sys
        from unittest.mock import patch, MagicMock
        from services.execution.lean_runtime.pending_signal_store import RedisPendingSignalStore

        mock_redis_client = MagicMock()
        mock_redis = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_redis_client

        now = 1000.0
        def _hget(key, field):
            skey = str(key)
            if "worker-A" in skey:
                return str(now - 10.0) # 10s old < 300s timeout
            if "worker-B" in skey:
                return str(now - 400.0) # 400s old > 300s timeout
            return None

        mock_redis_client.hget.side_effect = _hget
        mock_redis_client.lrange.side_effect = lambda k, s, e: [json.dumps({"signal_id": "sig-1"})]
        mock_redis_client.lrem.return_value = 1
        mock_redis_client.scan.side_effect = lambda cursor=0, match="", count=100: (0, ["pantheon:signals:pending:b-001:inflight:worker-A", "pantheon:signals:pending:b-001:inflight:worker-B"])

        with patch.dict(sys.modules, {"redis": mock_redis}):
            store = RedisPendingSignalStore(
                "redis://localhost:6379",
                queue_key="pantheon:signals:pending:b-001",
                worker_id="worker-A",
                visibility_timeout_seconds=300,
            )
        store._time_fn = lambda: now

        store.reclaim_expired_inflight()

        # worker-A list should NOT be reclaimed (lrem for worker-A not executed)
        # worker-B list SHOULD be reclaimed
        calls = mock_redis_client.lrem.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("worker-B", calls[0][0][0])


# ---------------------------------------------------------------------------
# Cross-process leader lease tests (B2)
# ---------------------------------------------------------------------------

class TestLeaderLeaseCrossProcess(unittest.TestCase):

    def test_file_backed_leader_lease_prevents_duplicate_leaders(self):
        import tempfile
        import importlib
        from pathlib import Path

        paper_fleet_reconciler = importlib.import_module("services.execution.runtime-manager.paper_fleet_reconciler")
        PaperFleetReconciler = paper_fleet_reconciler.PaperFleetReconciler

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            lease_file = Path(tmp.name)

        try:
            r1 = PaperFleetReconciler(leader_store=str(lease_file))
            r2 = PaperFleetReconciler(leader_store=str(lease_file))

            # First reconciler acquires lease
            self.assertTrue(r1.try_acquire_lease())
            self.assertTrue(r1.is_leader)

            # Second reconciler fails to acquire lease while active
            self.assertFalse(r2.try_acquire_lease())
            self.assertFalse(r2.is_leader)
        finally:
            lease_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Execution Error DLQ Routing & 6-Binding Drill (B3)
# ---------------------------------------------------------------------------

class TestExecutionErrorDLQ(unittest.TestCase):

    def test_execution_error_routes_to_dlq(self):
        store = InMemoryPendingSignalStore()
        sig = _signal("exec-err-1", binding_id="b-1", runtime_id="rt-1", capital_pool_id="pool-1")
        store.enqueue(sig)
        c = SignalConsumer(store_client=store, binding_id="b-1", runtime_id="rt-1", capital_pool_id="pool-1")
        algo = _RecordingAlgo()

        with patch("services.execution.lean_runtime.signal_consumer.execute", side_effect=ValueError("Broker error")):
            c.drain(algo=algo)

        self.assertEqual(store.dlq_depth(), 1)
        dlq_items = store.get_dlq()
        self.assertEqual(dlq_items[0]["signal_id"], "exec-err-1")
        self.assertTrue(any(k in dlq_items[0]["_dlq_reason"] for k in ("execution_error", "unexpected_error")))


class TestSixBindingRestartIsolationDrill(unittest.TestCase):

    def test_six_binding_restart_isolation(self):
        """Simulate 6 bindings being reconciled, ensuring strict key/worker isolation."""
        bindings = [
            _signal(f"sig-{i}", binding_id=f"b-00{i}", runtime_id=f"rt-00{i}", capital_pool_id=f"pool-00{i}")
            for i in range(1, 7)
        ]
        stores = {f"b-00{i}": InMemoryPendingSignalStore() for i in range(1, 7)}
        consumers = {
            f"b-00{i}": SignalConsumer(
                store_client=stores[f"b-00{i}"],
                binding_id=f"b-00{i}",
                runtime_id=f"rt-00{i}",
                capital_pool_id=f"pool-00{i}",
            )
            for i in range(1, 7)
        }

        # Enqueue correctly scoped signals
        for i, sig in enumerate(bindings, 1):
            stores[f"b-00{i}"].enqueue(sig)

        # Drain each worker
        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_exec:
            for i in range(1, 7):
                consumers[f"b-00{i}"].drain(algo=_RecordingAlgo())

        self.assertEqual(mock_exec.call_count, 6)


if __name__ == "__main__":
    unittest.main()

