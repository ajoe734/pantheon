"""Tests for runtime-aware signal isolation (LOOP-AUTO-RT-004).

Acceptance coverage:
  - Multiple runtime consumers cannot consume each other's signals.
  - Mismatched runtime_id or capital_pool_id signals are rejected.
  - Dead-letter or requeue behavior (DLQ) is tested.
"""
from __future__ import annotations

import json
import multiprocessing
import shutil
import socket
import subprocess
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.execution.lean_runtime.pending_signal_store import (
    InMemoryPendingSignalStore,
    binding_dlq_key,
    BINDING_DLQ_KEY_PREFIX,
    BINDING_QUEUE_KEY_PREFIX,
)


def _redis_claim_process(
    redis_url: str,
    queue_key: str,
    worker_id: str,
    result_queue,
    *,
    acknowledge: bool,
) -> None:
    """Claim one signal in a fresh process, optionally acknowledge it."""
    from services.execution.lean_runtime.pending_signal_store import (
        RedisPendingSignalStore,
    )

    store = RedisPendingSignalStore(
        redis_url,
        queue_key=queue_key,
        worker_id=worker_id,
        visibility_timeout_seconds=0.25,
    )
    claimed = store.get_pending(limit=1)
    result_queue.put(
        {
            "worker_id": worker_id,
            "signal_ids": [item["signal_id"] for item in claimed],
            "inflight_depth": store.inflight_depth(),
        }
    )
    if acknowledge and claimed:
        store.ack(claimed[0])


class _RealRedisDockerTestCase(unittest.TestCase):
    """Own a disposable real Redis 7 container for crash-boundary tests."""

    redis_url = ""
    _container_name = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker is required for real Redis proof")
        probe = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            raise unittest.SkipTest("docker daemon is unavailable for real Redis proof")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        cls._container_name = f"l12-cap-redis-{uuid.uuid4().hex[:10]}"
        started = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                cls._container_name,
                "-p",
                f"127.0.0.1:{port}:6379",
                "redis:7-alpine",
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            raise unittest.SkipTest(f"could not start Redis container: {started.stderr}")
        cls.redis_url = f"redis://127.0.0.1:{port}/15"
        import redis

        client = redis.Redis.from_url(cls.redis_url, decode_responses=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if client.ping():
                    return
            except Exception:
                time.sleep(0.05)
        cls.tearDownClass()
        raise RuntimeError("real Redis container did not become ready")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._container_name:
            subprocess.run(
                ["docker", "rm", "-f", cls._container_name],
                check=False,
                capture_output=True,
                text=True,
            )
            cls._container_name = ""
        super().tearDownClass()

    def setUp(self) -> None:
        import redis

        self.redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
        self.redis.flushdb()


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
        self.assertNotIn(
            "s-dlq-err",
            c._processed_signal_ids,
            "failed durable DLQ transfer must remain replayable",
        )
        self.assertEqual(store.inflight_depth(), 1)
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

class TestRedisPendingSignalStoreClaimVisibility(_RealRedisDockerTestCase):

    def _store(self, worker_id: str, *, binding_id: str = "b-001"):
        from services.execution.lean_runtime.pending_signal_store import (
            RedisPendingSignalStore,
            binding_queue_key,
        )

        return RedisPendingSignalStore(
            self.redis_url,
            queue_key=binding_queue_key(binding_id),
            worker_id=worker_id,
            visibility_timeout_seconds=0.25,
        )

    def test_live_claim_is_not_stolen_then_crash_claim_is_recovered(self):
        signal = _signal(
            "sig-crash-001",
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )
        worker_a = self._store("worker-A")
        worker_b = self._store("worker-B")
        worker_a.enqueue(signal)

        claimed_a = worker_a.get_pending(limit=1)
        self.assertEqual([item["signal_id"] for item in claimed_a], ["sig-crash-001"])
        self.assertEqual(worker_a.inflight_depth(), 1)
        self.assertEqual(worker_b.get_pending(limit=1), [])

        # Simulate worker-A crashing without ack.  A new worker can reclaim only
        # after Redis server time crosses the visibility deadline.
        time.sleep(0.3)
        claimed_b = worker_b.get_pending(limit=1)
        self.assertEqual([item["signal_id"] for item in claimed_b], ["sig-crash-001"])
        self.assertEqual(worker_a.inflight_depth(), 0)
        worker_b.ack(claimed_b[0])
        self.assertEqual(worker_b.inflight_depth(), 0)
        self.assertEqual(worker_b.queue_depth(), 0)

    def test_slow_rebalance_buffer_cannot_execute_after_claim_reclaim(self):
        signal = _signal(
            "sig-slow-buffer",
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )
        signal["run_id"] = "run-slow-buffer"
        worker_a = self._store("worker-A")
        worker_b = self._store("worker-B")
        worker_a.enqueue(signal)
        consumer_a = SignalConsumer(
            store_client=worker_a,
            rebalance_timeout_bars=3,
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )
        consumer_b = SignalConsumer(
            store_client=worker_b,
            rebalance_timeout_bars=1,
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )

        with patch("services.execution.lean_runtime.signal_consumer.execute") as mock_execute:
            consumer_a.drain(algo=_RecordingAlgo())
            self.assertEqual(mock_execute.call_count, 0)

            time.sleep(0.3)
            consumer_b.drain(algo=_RecordingAlgo())
            self.assertEqual(mock_execute.call_count, 1)

            # worker-A still has the original buffered Python object, but its
            # claim token was reclaimed and acknowledged by worker-B.
            consumer_a.flush_rebalance("run-slow-buffer", algo=_RecordingAlgo())

        self.assertEqual(mock_execute.call_count, 1)
        self.assertEqual(worker_a.inflight_depth(), 0)
        self.assertEqual(worker_b.inflight_depth(), 0)
        self.assertEqual(worker_a.queue_depth(), 0)
        self.assertTrue(worker_a.is_processed("sig-slow-buffer"))

    def test_claim_renews_through_execution_longer_than_visibility_ttl(self):
        from services.execution.lean_runtime.pending_signal_store import (
            RedisPendingSignalStore,
            binding_queue_key,
        )

        binding_id = "b-long-execution"
        visibility_timeout = 0.2
        signal = _signal(
            "sig-long-execution",
            binding_id=binding_id,
            runtime_id="rt-long-execution",
            capital_pool_id="pool-long-execution",
        )
        worker_a = RedisPendingSignalStore(
            self.redis_url,
            queue_key=binding_queue_key(binding_id),
            worker_id="worker-A",
            visibility_timeout_seconds=visibility_timeout,
        )
        worker_b = RedisPendingSignalStore(
            self.redis_url,
            queue_key=binding_queue_key(binding_id),
            worker_id="worker-B",
            visibility_timeout_seconds=visibility_timeout,
        )
        worker_a.enqueue(signal)
        consumer_a = SignalConsumer(
            store_client=worker_a,
            binding_id=binding_id,
            runtime_id="rt-long-execution",
            capital_pool_id="pool-long-execution",
        )
        consumer_b = SignalConsumer(
            store_client=worker_b,
            binding_id=binding_id,
            runtime_id="rt-long-execution",
            capital_pool_id="pool-long-execution",
        )

        execution_started = threading.Event()
        release_execution = threading.Event()
        executions: list[str] = []
        drain_errors: list[BaseException] = []
        renewals: list[bool] = []
        real_renew = worker_a.renew_claim

        def counted_renew(claimed_signal):
            renewed = real_renew(claimed_signal)
            renewals.append(renewed)
            return renewed

        worker_a.renew_claim = counted_renew  # type: ignore[method-assign]

        class _WorkerAlgo(_RecordingAlgo):
            def __init__(self, worker_id: str) -> None:
                super().__init__()
                self.worker_id = worker_id

        def slow_execute(_signal_payload, algo):
            executions.append(algo.worker_id)
            if algo.worker_id == "worker-A":
                execution_started.set()
                if not release_execution.wait(timeout=3):
                    raise TimeoutError("test did not release worker-A execution")

        def drain_worker_a() -> None:
            try:
                consumer_a.drain(algo=_WorkerAlgo("worker-A"))
            except BaseException as exc:  # noqa: BLE001 - surface thread failures in test
                drain_errors.append(exc)

        drain_thread = threading.Thread(target=drain_worker_a)
        with patch(
            "services.execution.lean_runtime.signal_consumer.execute",
            side_effect=slow_execute,
        ):
            drain_thread.start()
            try:
                self.assertTrue(execution_started.wait(timeout=2))
                time.sleep(visibility_timeout * 2.5)
                self.assertGreaterEqual(
                    len(renewals),
                    3,
                    "claim must renew repeatedly while execute() remains blocked",
                )

                # Worker A is still inside execute() after more than two TTLs.
                # Worker B must not reclaim or execute the same side effect.
                consumer_b.drain(algo=_WorkerAlgo("worker-B"))
                self.assertEqual(executions, ["worker-A"])
                self.assertEqual(worker_a.inflight_depth(), 1)
                self.assertEqual(worker_b.inflight_depth(), 0)
                self.assertEqual(worker_a.queue_depth(), 0)
            finally:
                release_execution.set()
                drain_thread.join(timeout=3)

        self.assertFalse(drain_thread.is_alive())
        self.assertEqual(drain_errors, [])
        self.assertEqual(executions, ["worker-A"])
        self.assertEqual(worker_a.inflight_depth(), 0)
        self.assertEqual(worker_b.inflight_depth(), 0)
        self.assertEqual(worker_a.queue_depth(), 0)
        self.assertTrue(worker_a.is_processed("sig-long-execution"))

    def _assert_execution_fence_survives_heartbeat_loss(self, *, renew_failure: str) -> None:
        from services.execution.lean_runtime.pending_signal_store import (
            RedisPendingSignalStore,
            binding_queue_key,
        )

        binding_id = f"b-heartbeat-{renew_failure}"
        visibility_timeout = 0.1
        signal_id = f"sig-heartbeat-{renew_failure}"
        signal = _signal(
            signal_id,
            binding_id=binding_id,
            runtime_id=f"rt-heartbeat-{renew_failure}",
            capital_pool_id=f"pool-heartbeat-{renew_failure}",
        )
        worker_a = RedisPendingSignalStore(
            self.redis_url,
            queue_key=binding_queue_key(binding_id),
            worker_id="worker-A",
            visibility_timeout_seconds=visibility_timeout,
        )
        worker_b = RedisPendingSignalStore(
            self.redis_url,
            queue_key=binding_queue_key(binding_id),
            worker_id="worker-B",
            visibility_timeout_seconds=visibility_timeout,
        )
        worker_a.enqueue(signal)
        consumer_a = SignalConsumer(
            store_client=worker_a,
            binding_id=binding_id,
            runtime_id=f"rt-heartbeat-{renew_failure}",
            capital_pool_id=f"pool-heartbeat-{renew_failure}",
        )
        consumer_b = SignalConsumer(
            store_client=worker_b,
            binding_id=binding_id,
            runtime_id=f"rt-heartbeat-{renew_failure}",
            capital_pool_id=f"pool-heartbeat-{renew_failure}",
        )

        execution_started = threading.Event()
        lease_failure_observed = threading.Event()
        release_execution = threading.Event()
        executions: list[str] = []
        drain_errors: list[BaseException] = []
        renew_calls = 0
        real_renew = worker_a.renew_claim

        def fail_renew_after_execution_starts(claimed_signal):
            nonlocal renew_calls
            renew_calls += 1
            if renew_calls == 1:
                return real_renew(claimed_signal)
            lease_failure_observed.set()
            if renew_failure == "false":
                return False
            raise RuntimeError("simulated renew transport failure")

        worker_a.renew_claim = fail_renew_after_execution_starts  # type: ignore[method-assign]

        class _WorkerAlgo(_RecordingAlgo):
            def __init__(self, worker_id: str) -> None:
                super().__init__()
                self.worker_id = worker_id

        def blocked_execute(_signal_payload, algo):
            executions.append(algo.worker_id)
            if algo.worker_id == "worker-A":
                execution_started.set()
                if not release_execution.wait(timeout=3):
                    raise TimeoutError("test did not release worker-A execution")

        def drain_worker_a() -> None:
            try:
                consumer_a.drain(algo=_WorkerAlgo("worker-A"))
            except BaseException as exc:  # noqa: BLE001 - surface thread failures in test
                drain_errors.append(exc)

        drain_thread = threading.Thread(target=drain_worker_a)
        with patch(
            "services.execution.lean_runtime.signal_consumer.execute",
            side_effect=blocked_execute,
        ):
            drain_thread.start()
            try:
                self.assertTrue(execution_started.wait(timeout=2))
                self.assertTrue(lease_failure_observed.wait(timeout=2))
                time.sleep(visibility_timeout * 2.5)

                # Worker B reclaims while worker A is still inside execute().
                # The durable execution reservation must route B's copy to
                # recovery without allowing a second executor call.
                consumer_b.drain(algo=_WorkerAlgo("worker-B"))
                self.assertEqual(len(executions), 1)
                self.assertEqual(executions, ["worker-A"])
                self.assertEqual(worker_a.inflight_depth(), 0)
                self.assertEqual(worker_b.inflight_depth(), 0)
                self.assertEqual(worker_a.queue_depth(), 0)
                self.assertEqual(worker_b.dlq_depth(), 1)
            finally:
                release_execution.set()
                drain_thread.join(timeout=3)

        self.assertFalse(drain_thread.is_alive())
        self.assertEqual(drain_errors, [])
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions, ["worker-A"])
        self.assertEqual(renew_calls, 2)
        self.assertTrue(worker_a.is_processed(signal_id))
        self.assertEqual(worker_a.queue_depth(), 0)
        self.assertEqual(worker_a.inflight_depth(), 0)
        self.assertEqual(worker_b.inflight_depth(), 0)

    def test_execution_fence_blocks_reclaim_when_heartbeat_renew_returns_false(self):
        self._assert_execution_fence_survives_heartbeat_loss(renew_failure="false")

    def test_execution_fence_blocks_reclaim_when_heartbeat_renew_raises(self):
        self._assert_execution_fence_survives_heartbeat_loss(renew_failure="exception")

    def test_claim_response_loss_remains_recoverable(self):
        signal = _signal(
            "sig-response-loss",
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )
        worker_a = self._store("worker-A")
        worker_a.enqueue(signal)
        real_client = worker_a._client

        class _RaiseAfterClaim:
            def __init__(self):
                self.raised = False

            def __getattr__(self, name):
                return getattr(real_client, name)

            def eval(self, script, *args):
                result = real_client.eval(script, *args)
                if script == worker_a._CLAIM_LUA and not self.raised:
                    self.raised = True
                    raise ConnectionError("simulated response loss after atomic claim")
                return result

        worker_a._client = _RaiseAfterClaim()
        with self.assertRaises(ConnectionError):
            worker_a.get_pending(limit=1)
        self.assertEqual(worker_a.queue_depth(), 0)
        self.assertEqual(worker_a.inflight_depth(), 1)

        time.sleep(0.3)
        worker_b = self._store("worker-B")
        recovered = worker_b.get_pending(limit=1)
        self.assertEqual([item["signal_id"] for item in recovered], ["sig-response-loss"])

    def test_nack_and_dlq_are_atomic_claim_transfers(self):
        worker = self._store("worker-A")
        first = _signal(
            "sig-nack",
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )
        worker.enqueue(first)
        claimed = worker.get_pending(limit=1)
        worker.nack_requeue(claimed[0])
        self.assertEqual(worker.inflight_depth(), 0)
        self.assertEqual(worker.queue_depth(), 1)

        reclaimed = worker.get_pending(limit=1)
        worker.enqueue_dlq({**reclaimed[0], "_dlq_reason": "forced-test"})
        self.assertEqual(worker.inflight_depth(), 0)
        self.assertEqual(worker.queue_depth(), 0)
        self.assertEqual(worker.dlq_depth(), 1)

    def test_ack_nack_and_dlq_response_loss_has_no_signal_loss_window(self):
        worker = self._store("worker-A")
        real_client = worker._client

        class _RaiseAfterScript:
            def __init__(self, target_script):
                self.target_script = target_script
                self.raised = False

            def __getattr__(self, name):
                return getattr(real_client, name)

            def eval(self, script, *args):
                result = real_client.eval(script, *args)
                if script == self.target_script and not self.raised:
                    self.raised = True
                    raise ConnectionError("simulated response loss after atomic transition")
                return result

        ack_signal = _signal(
            "sig-ack-response-loss",
            binding_id="b-001",
            runtime_id="rt-001",
            capital_pool_id="pool-001",
        )
        worker.enqueue(ack_signal)
        ack_claim = worker.get_pending(limit=1)[0]
        worker._client = _RaiseAfterScript(worker._ACK_LUA)
        with self.assertRaises(ConnectionError):
            worker.ack(ack_claim)
        worker._client = real_client
        self.assertEqual(worker.inflight_depth(), 0)
        self.assertEqual(worker.queue_depth(), 0)

        nack_signal = {**ack_signal, "signal_id": "sig-nack-response-loss"}
        worker.enqueue(nack_signal)
        nack_claim = worker.get_pending(limit=1)[0]
        worker._client = _RaiseAfterScript(worker._TRANSFER_LUA)
        with self.assertRaises(ConnectionError):
            worker.nack_requeue(nack_claim)
        worker._client = real_client
        self.assertEqual(worker.inflight_depth(), 0)
        self.assertEqual(worker.queue_depth(), 1)

        dlq_claim = worker.get_pending(limit=1)[0]
        worker._client = _RaiseAfterScript(worker._TRANSFER_LUA)
        with self.assertRaises(ConnectionError):
            worker.enqueue_dlq({**dlq_claim, "_dlq_reason": "response-loss"})
        worker._client = real_client
        self.assertEqual(worker.inflight_depth(), 0)
        self.assertEqual(worker.queue_depth(), 0)
        self.assertEqual(worker.dlq_depth(), 1)


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

class TestExecutionErrorDLQ(_RealRedisDockerTestCase):

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

    def test_execution_error_atomically_moves_real_redis_claim_to_dlq(self):
        from services.execution.lean_runtime.pending_signal_store import (
            RedisPendingSignalStore,
            binding_queue_key,
        )

        store = RedisPendingSignalStore(
            self.redis_url,
            queue_key=binding_queue_key("b-real-dlq"),
            worker_id="worker-real-dlq",
            visibility_timeout_seconds=5,
        )
        store.enqueue(
            _signal(
                "exec-err-real",
                binding_id="b-real-dlq",
                runtime_id="rt-real-dlq",
                capital_pool_id="pool-real-dlq",
            )
        )
        consumer = SignalConsumer(
            store_client=store,
            binding_id="b-real-dlq",
            runtime_id="rt-real-dlq",
            capital_pool_id="pool-real-dlq",
        )
        with patch(
            "services.execution.lean_runtime.signal_consumer.execute",
            side_effect=ValueError("simulated broker exception"),
        ):
            consumer.drain(algo=_RecordingAlgo())

        self.assertEqual(store.inflight_depth(), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(store.dlq_depth(), 1)
        dlq_payload = json.loads(self.redis.lindex(store._dlq_key, 0))
        self.assertEqual(dlq_payload["signal_id"], "exec-err-real")
        self.assertIn("unexpected_error", dlq_payload["_dlq_reason"])


class TestSixBindingRestartIsolationDrill(_RealRedisDockerTestCase):

    def test_six_binding_restart_isolation(self):
        """Six crashed processes recover only their own Redis binding queue."""
        from services.execution.lean_runtime.pending_signal_store import (
            RedisPendingSignalStore,
            binding_queue_key,
        )

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        crash_processes = []
        for index in range(1, 7):
            binding_id = f"b-00{index}"
            store = RedisPendingSignalStore(
                self.redis_url,
                queue_key=binding_queue_key(binding_id),
                worker_id="producer",
                visibility_timeout_seconds=0.25,
            )
            store.enqueue(
                _signal(
                    f"sig-{index}",
                    binding_id=binding_id,
                    runtime_id=f"rt-00{index}",
                    capital_pool_id=f"pool-00{index}",
                )
            )
            process = context.Process(
                target=_redis_claim_process,
                args=(
                    self.redis_url,
                    binding_queue_key(binding_id),
                    f"crashed-worker-{index}",
                    result_queue,
                ),
                kwargs={"acknowledge": False},
            )
            process.start()
            crash_processes.append(process)

        for process in crash_processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        crashed = [result_queue.get(timeout=2) for _ in range(6)]
        self.assertEqual(
            {item["signal_ids"][0] for item in crashed},
            {f"sig-{index}" for index in range(1, 7)},
        )

        time.sleep(0.3)
        recovered_by_binding = {}
        for index in range(1, 7):
            binding_id = f"b-00{index}"
            restarted = RedisPendingSignalStore(
                self.redis_url,
                queue_key=binding_queue_key(binding_id),
                worker_id=f"restarted-worker-{index}",
                visibility_timeout_seconds=0.25,
            )
            recovered = restarted.get_pending(limit=1)
            recovered_by_binding[binding_id] = [item["signal_id"] for item in recovered]
            self.assertEqual(
                recovered_by_binding[binding_id],
                [f"sig-{index}"],
            )
            restarted.ack(recovered[0])

        self.assertEqual(len(recovered_by_binding), 6)


if __name__ == "__main__":
    unittest.main()
