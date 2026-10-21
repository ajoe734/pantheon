"""Tests for decision-to-signal producer."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services.execution.lean_runtime.pending_signal_store import (
    InMemoryPendingSignalStore,
    RedisPendingSignalStore,
)
from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.execution.lean_runtime.signal_producer import (
    DecisionSignalProducer,
    SignalProducerValidationError,
    build_decision_signals,
)
from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    validate_envelope,
)

_NOW = "2026-06-14T14:30:00Z"


class TestDecisionSignalProducer(unittest.TestCase):
    def test_persona_allocation_proposal_style_decision_enqueues_schema_v1(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = DecisionSignalProducer(store)
        decision = {
            "proposal_id": "pap-alpha-001",
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-paper",
            "scope_ref": "paper",
            "strategy_id": "strategy-alpha",
            "created_at": _NOW,
            "directions": ["long"],
            "target_weights": {"AAPL.US": 0.25, "MSFT.US": 0.1},
            "conviction": 0.82,
            "evidence_refs": ["seed://alpha", "orient://alpha"],
            "metadata": {"strategy_family": "us_equity_momentum"},
        }

        batch = producer.produce(
            decision,
            binding_id="binding-alpha",
            runtime_id="runtime-alpha",
            run_id="run-alpha",
            source_worker="unit-test-producer",
        )

        self.assertEqual(batch.count, 2)
        self.assertEqual(store.queue_depth(), 2)
        drained = store.get_pending()
        self.assertEqual([signal["symbol"] for signal in drained], ["AAPL.US", "MSFT.US"])
        first = drained[0]
        self.assertEqual(first["version"], "1.0")
        self.assertEqual(first["strategy_id"], "strategy-alpha")
        self.assertEqual(first["action"], "BUY")
        self.assertEqual(first["direction"], "LONG")
        self.assertEqual(first["quantity"], 0.25)
        self.assertEqual(first["quantity_type"], "PERCENT_PORTFOLIO")
        self.assertEqual(first["binding_id"], "binding-alpha")
        self.assertEqual(first["runtime_id"], "runtime-alpha")
        self.assertEqual(first["run_id"], "run-alpha")
        self.assertEqual(first["source_worker"], "unit-test-producer")
        self.assertEqual(first["tenant_id"], "default")
        self.assertEqual(first["environment"], "paper")
        self.assertEqual(
            first["journey_id"],
            validate_envelope(first["correlation_envelope"])["journey_id"],
        )
        self.assertEqual(first["correlation_envelope"]["event_id"], f"signal:{first['signal_id']}")
        self.assertNotEqual(first["journey_id"], drained[1]["journey_id"])
        self.assertEqual(first["metadata"]["persona_id"], "persona-alpha")
        self.assertEqual(first["metadata"]["allocation_proposal_id"], "pap-alpha-001")
        self.assertEqual(first["metadata"]["confidence_score"], 0.82)
        self.assertEqual(first["metadata"]["evidence_refs"], ["seed://alpha", "orient://alpha"])

        consumer = SignalConsumer(store_client=MagicMock())
        self.assertIsNotNone(consumer._validate(first))

    def test_strategy_execution_decision_enqueues_explicit_limit_signal(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = DecisionSignalProducer(store)
        decision = {
            "decision_id": "decision-short-001",
            "signal_id": "sig-short-001",
            "strategy_id": "strategy-stat-arb",
            "timestamp": _NOW,
            "symbol": "TSLA.US",
            "action": "sell",
            "direction": "short",
            "target_percent": 0.15,
            "quantity_type": "PERCENT_PORTFOLIO",
            "order_type": "limit",
            "limit_price": 190.25,
            "confidence": 0.73,
            "metadata": {"alpha_source": "strategy_decision_fixture"},
        }

        batch = producer.produce(decision, binding_id="binding-short")

        self.assertEqual(batch.count, 1)
        [signal] = store.get_pending()
        self.assertEqual(signal["signal_id"], "sig-short-001")
        self.assertEqual(signal["action"], "SELL")
        self.assertEqual(signal["direction"], "SHORT")
        self.assertEqual(signal["quantity"], 0.15)
        self.assertEqual(signal["quantity_type"], "PERCENT_PORTFOLIO")
        self.assertEqual(signal["order_type"], "LIMIT")
        self.assertEqual(signal["limit_price"], 190.25)
        self.assertEqual(signal["binding_id"], "binding-short")
        self.assertEqual(signal["metadata"]["confidence_score"], 0.73)
        self.assertEqual(signal["metadata"]["decision_id"], "decision-short-001")
        self.assertEqual(signal["correlation_envelope"]["tenant_id"], "default")
        self.assertEqual(signal["correlation_envelope"]["environment"], "paper")

    def test_existing_decision_envelope_is_propagated_per_signal(self) -> None:
        upstream = mint_trade_envelope(
            {"tenant_id": "tenant-alpha", "environment": "paper"},
            producer="strategy.decision",
            event_id="decision-event-001",
            now=_NOW,
        )
        [signal] = build_decision_signals(
            {
                "decision_id": "decision-envelope-001",
                "strategy_id": "strategy-envelope",
                "timestamp": _NOW,
                "tenant_id": "tenant-alpha",
                "environment": "paper",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 1,
                "quantity_type": "SHARES",
                "correlation_envelope": upstream,
                "run_id": "run-envelope-001",
            }
        )

        outgoing = validate_envelope(signal["correlation_envelope"])
        self.assertEqual(outgoing["journey_id"], upstream["journey_id"])
        self.assertEqual(outgoing["causation_event_id"], upstream["event_id"])
        self.assertEqual(outgoing["event_id"], f"signal:{signal['signal_id']}")
        self.assertEqual(signal["run_id"], "run-envelope-001")

    def test_limit_signal_without_price_fails_before_enqueue(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = DecisionSignalProducer(store)
        decision = {
            "decision_id": "decision-limit-missing-price",
            "strategy_id": "strategy-limit",
            "timestamp": _NOW,
            "symbol": "NVDA.US",
            "action": "buy",
            "direction": "long",
            "quantity": 10,
            "quantity_type": "SHARES",
            "order_type": "LIMIT",
        }

        with self.assertRaisesRegex(
            SignalProducerValidationError,
            "limit_price is required",
        ):
            producer.produce(decision)

        self.assertEqual(store.queue_depth(), 0)

    def test_build_signals_accepts_to_dict_models_without_enqueue(self) -> None:
        class ModelDecision:
            def to_dict(self):
                return {
                    "decision_id": "decision-model-001",
                    "strategy_id": "strategy-model",
                    "timestamp": _NOW,
                    "symbol": "SPY.US",
                    "action": "hold",
                    "direction": "long",
                    "quantity": 0,
                    "quantity_type": "SHARES",
                }

        signals = build_decision_signals(ModelDecision())

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["action"], "HOLD")
        self.assertEqual(signals[0]["direction"], "LONG")

    def test_repeated_identical_decision_keeps_one_journey_across_ticks(self) -> None:
        def decision(tenant: str = "tenant-a", decision_id: str = "dec-1") -> dict:
            return {
                "decision_id": decision_id,
                "signal_id": f"sig-{decision_id}",
                "strategy_id": "strategy-a",
                "symbol": "SPY.US",
                "action": "buy",
                "direction": "long",
                "quantity": 1,
                "quantity_type": "SHARES",
                "tenant_id": tenant,
                "environment": "paper",
            }

        first = build_decision_signals(decision(), now_iso="2026-06-14T14:30:00Z")[0]
        retry = build_decision_signals(decision(), now_iso="2026-06-14T14:31:02Z")[0]
        other_decision = build_decision_signals(decision(decision_id="dec-2"))[0]
        other_tenant = build_decision_signals(decision(tenant="tenant-b"))[0]

        for field in ("journey_id", "correlation_id", "trace_id"):
            self.assertEqual(
                first["correlation_envelope"][field], retry["correlation_envelope"][field]
            )
        self.assertEqual(first["journey_id"], retry["journey_id"])
        self.assertNotEqual(first["journey_id"], other_decision["journey_id"])
        self.assertNotEqual(first["journey_id"], other_tenant["journey_id"])

    def test_redis_factory_uses_redis_pending_signal_store(self) -> None:
        mock_redis_module = MagicMock()
        mock_redis_module.Redis.from_url.return_value = MagicMock()

        with patch.dict(sys.modules, {"redis": mock_redis_module}):
            producer = DecisionSignalProducer.redis(
                "redis://localhost:6379",
                queue_key="pantheon:signals:pending:binding-factory",
            )

        self.assertIsInstance(producer._store, RedisPendingSignalStore)
        self.assertEqual(producer._store._queue_key, "pantheon:signals:pending:binding-factory")

    def test_producer_to_memory_and_redis_serialization_observes_real_enqueue_without_manual_metadata(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = DecisionSignalProducer(store)
        decision = {
            "decision_id": "decision-real-obs-001",
            "strategy_id": "strategy-obs",
            "timestamp": _NOW,
            "symbol": "AAPL.US",
            "action": "buy",
            "direction": "long",
            "quantity": 10,
            "quantity_type": "SHARES",
            "metadata": {"custom_strategy_metric": 42},
        }

        before_enqueue = datetime.now(timezone.utc)
        batch = producer.produce(
            decision,
            binding_id="binding-obs",
            runtime_id="runtime-obs",
            run_id="run-obs",
            source_worker="test-producer-obs",
        )
        after_enqueue = datetime.now(timezone.utc)

        self.assertEqual(batch.count, 1)
        # Caller input and returned batch signals must NOT have manual enqueue metadata
        self.assertNotIn("enqueued_at", decision)
        self.assertNotIn("enqueued_at", decision["metadata"])
        self.assertNotIn("causal_queue_receipt", decision)
        self.assertNotIn("enqueued_at", batch.signals[0])

        # Dequeued item from store DOES observe actual enqueue admission UTC time
        [dequeued] = store.get_pending()
        self.assertIn("enqueued_at", dequeued)
        self.assertEqual(dequeued["metadata"]["custom_strategy_metric"], 42)

        enqueued_dt = datetime.fromisoformat(dequeued["enqueued_at"])
        self.assertGreaterEqual(enqueued_dt, before_enqueue)
        self.assertLessEqual(enqueued_dt, after_enqueue)
        self.assertNotEqual(dequeued["enqueued_at"], _NOW)

        # Redis serialization path
        mock_redis = MagicMock()
        redis_store = RedisPendingSignalStore.__new__(RedisPendingSignalStore)
        redis_store._client = mock_redis
        redis_store._queue_key = "pantheon:signals:pending:test-obs"
        redis_store._worker_id = "test-worker"
        redis_store._default_batch_size = 100
        redis_producer = DecisionSignalProducer(redis_store)

        redis_batch = redis_producer.produce(
            decision,
            binding_id="binding-obs",
            runtime_id="runtime-obs",
            run_id="run-obs",
        )
        self.assertEqual(redis_batch.count, 1)
        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args[0]
        self.assertEqual(call_args[0], "pantheon:signals:pending:test-obs")
        serialized = json.loads(call_args[1])
        self.assertIn("enqueued_at", serialized)
        self.assertEqual(serialized["symbol"], "AAPL.US")
        self.assertEqual(serialized["action"], "BUY")
        self.assertEqual(serialized["binding_id"], "binding-obs")

    def test_producer_and_store_refuse_caller_forged_timestamps_and_observe_actual_enqueue(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = DecisionSignalProducer(store)
        forged_ts_past = "1999-01-01T00:00:00Z"
        forged_decision = {
            "decision_id": "decision-forged-001",
            "strategy_id": "strategy-forged",
            "timestamp": _NOW,
            "symbol": "AAPL.US",
            "action": "buy",
            "direction": "long",
            "quantity": 10,
            "quantity_type": "SHARES",
            "enqueued_at": forged_ts_past,
            "metadata": {
                "enqueued_at": "2099-01-01T00:00:00Z",
                "custom_metric": 99,
            },
        }

        before = datetime.now(timezone.utc)
        batch = producer.produce(
            forged_decision,
            binding_id="b-forged",
            runtime_id="rt-forged",
            run_id="run-forged",
        )
        after = datetime.now(timezone.utc)

        # 1. Caller input dictionary and nested metadata are preserved without mutation
        self.assertEqual(forged_decision["enqueued_at"], forged_ts_past)
        self.assertEqual(forged_decision["metadata"]["enqueued_at"], "2099-01-01T00:00:00Z")
        self.assertEqual(forged_decision["metadata"]["custom_metric"], 99)

        # 2. Dequeued signal observes actual physical enqueue interval, NOT caller forged time
        [dequeued] = store.get_pending()
        self.assertNotEqual(dequeued["enqueued_at"], forged_ts_past)
        self.assertNotEqual(dequeued["enqueued_at"], "2099-01-01T00:00:00Z")
        enqueued_dt = datetime.fromisoformat(dequeued["enqueued_at"])
        self.assertGreaterEqual(enqueued_dt, before)
        self.assertLessEqual(enqueued_dt, after)

        # 3. Correlation fields are preserved intact
        self.assertEqual(dequeued["metadata"]["decision_id"], "decision-forged-001")
        self.assertEqual(dequeued["correlation_envelope"]["journey_id"], batch.signals[0]["journey_id"])
        self.assertEqual(dequeued["strategy_id"], "strategy-forged")
        self.assertEqual(dequeued["binding_id"], "b-forged")
        self.assertEqual(dequeued["runtime_id"], "rt-forged")
        self.assertEqual(dequeued["journey_id"], batch.signals[0]["journey_id"])

        # 4. Direct store enqueue with forged enqueued_at also records actual operation time
        raw_signal = {
            "signal_id": "sig-direct-forged",
            "strategy_id": "strategy-forged",
            "version": "1.0",
            "timestamp": _NOW,
            "symbol": "2330.TW",
            "action": "BUY",
            "quantity": 100,
            "enqueued_at": "2000-01-01T12:00:00Z",
            "metadata": {"enqueued_at": "2000-01-01T12:00:00Z"},
        }
        before_direct = datetime.now(timezone.utc)
        store.enqueue(raw_signal)
        after_direct = datetime.now(timezone.utc)

        self.assertEqual(raw_signal["enqueued_at"], "2000-01-01T12:00:00Z")
        [direct_dequeued] = store.get_pending()
        self.assertEqual(direct_dequeued["signal_id"], "sig-direct-forged")
        self.assertNotEqual(direct_dequeued["enqueued_at"], "2000-01-01T12:00:00Z")
        direct_dt = datetime.fromisoformat(direct_dequeued["enqueued_at"])
        self.assertGreaterEqual(direct_dt, before_direct)
        self.assertLessEqual(direct_dt, after_direct)

    def test_producer_skips_started_execution_fence_and_reuses_original_canonical_journey(self) -> None:
        from services.execution.lean_runtime.signal_producer import remember_canonical_journey

        remember_canonical_journey(
            tenant_id="tenant-a",
            environment="paper",
            journey_id="tj-paper-001",
            signal_id="sig-76fdd54e9d601a08260e875aad01d23c",
            decision_id="decision-paper-001",
            run_id="run-rb-paper-001-2026-06-12",
            trace_id="20000000-0000-0000-0000-000000000001",
        )
        decision = {
            "decision_id": "decision-paper-001",
            "signal_id": "sig-76fdd54e9d601a08260e875aad01d23c",
            "run_id": "run-rb-paper-001-2026-06-12",
            "strategy_id": "strategy-paper-001",
            "timestamp": _NOW,
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 1,
            "quantity_type": "SHARES",
            "tenant_id": "tenant-a",
            "environment": "paper",
        }
        [built] = build_decision_signals(decision)
        self.assertEqual(built["journey_id"], "tj-paper-001")
        self.assertEqual(built["correlation_envelope"]["journey_id"], "tj-paper-001")
        self.assertEqual(
            built["correlation_envelope"]["trace_id"],
            "20000000-0000-0000-0000-000000000001",
        )

        store = InMemoryPendingSignalStore()
        producer = DecisionSignalProducer(store)
        first_batch = producer.produce(decision)
        self.assertEqual(first_batch.count, 1)
        [claimed] = store.get_pending()
        fence = store.begin_execution(claimed)
        self.assertEqual(fence.status, "acquired")
        self.assertFalse(store.is_processed(claimed["signal_id"]))

        retry_batch = producer.produce(decision)
        self.assertEqual(retry_batch.count, 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            store._execution_fences[claimed["signal_id"]],
            f"started:{fence.token}",
        )

    def test_default_tenant_does_not_reuse_foreign_tenant_canonical_journey_and_fresh_process_preserves_fence(
        self,
    ) -> None:
        import subprocess
        from services.execution.lean_runtime.signal_producer import remember_canonical_journey

        remember_canonical_journey(
            tenant_id="tenant-dev",
            environment="paper",
            journey_id="tj_c7d14e8a-3cac-439a-81b7-7401fa2aaac3",
            signal_id="sig-76fdd54e9d601a08260e875aad01d23c",
            decision_id="dec-rb-2eebe2b7a8af4c0c909991a13bba75a6-76fdd54e9d601a08260e875a",
            run_id="run-rb-2eebe2b7a8af4c0c909991a13bba75a6-76fdd54e9d601a08260e875a",
            correlation_id="f2c61c63-b926-4bab-ad1e-1c792bcc18f2",
            trace_id="bcde2641-a43e-4c6d-8a25-de42a0ad97a3",
        )
        base_decision = {
            "decision_id": "dec-rb-2eebe2b7a8af4c0c909991a13bba75a6-76fdd54e9d601a08260e875a",
            "signal_id": "sig-76fdd54e9d601a08260e875aad01d23c",
            "run_id": "run-rb-2eebe2b7a8af4c0c909991a13bba75a6-76fdd54e9d601a08260e875a",
            "strategy_id": "strategy-paper-001",
            "timestamp": _NOW,
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 1,
            "quantity_type": "SHARES",
            "environment": "paper",
        }
        [explicit_tenant_sig] = build_decision_signals(
            {**base_decision, "tenant_id": "tenant-dev"}
        )
        self.assertEqual(explicit_tenant_sig["tenant_id"], "tenant-dev")
        self.assertEqual(
            explicit_tenant_sig["journey_id"],
            "tj_c7d14e8a-3cac-439a-81b7-7401fa2aaac3",
        )
        self.assertEqual(
            explicit_tenant_sig["correlation_envelope"]["correlation_id"],
            "f2c61c63-b926-4bab-ad1e-1c792bcc18f2",
        )
        self.assertEqual(
            explicit_tenant_sig["correlation_envelope"]["trace_id"],
            "bcde2641-a43e-4c6d-8a25-de42a0ad97a3",
        )

        [implicit_default_sig] = build_decision_signals(dict(base_decision))
        self.assertEqual(implicit_default_sig["tenant_id"], "default")
        self.assertEqual(
            implicit_default_sig["correlation_envelope"]["tenant_id"], "default"
        )
        self.assertNotEqual(
            implicit_default_sig["journey_id"],
            "tj_c7d14e8a-3cac-439a-81b7-7401fa2aaac3",
        )
        self.assertNotEqual(
            implicit_default_sig["correlation_envelope"]["correlation_id"],
            "f2c61c63-b926-4bab-ad1e-1c792bcc18f2",
        )
        self.assertNotEqual(
            implicit_default_sig["correlation_envelope"]["trace_id"],
            "bcde2641-a43e-4c6d-8a25-de42a0ad97a3",
        )

        script = """
import hashlib, json, sys, types
from services.execution.lean_runtime.signal_producer import DecisionSignalProducer, _CANONICAL_JOURNEYS

assert len(_CANONICAL_JOURNEYS) == 0
sid = "sig-76fdd54e9d601a08260e875aad01d23c"
qkey = "pantheon:signals:pending:rb-2eebe2b7a8af4c0c909991a13bba75a6"
ekey = f"{qkey}:execution:{hashlib.sha256(sid.encode('utf-8')).hexdigest()}"

for fence_value in ("started:worker-1:1", "completed:worker-1:1"):
    kv = {ekey: fence_value}
    enqueued = []
    mutations = []
    class _FakeRedisClient:
        def exists(self, key):
            return 1 if key in kv else 0
        def get(self, key):
            return kv.get(key)
        def rpush(self, key, val):
            enqueued.append((key, val))
        def set(self, *a, **kw):
            mutations.append(("set", a, kw))
        def setex(self, *a, **kw):
            mutations.append(("setex", a, kw))
        def delete(self, *a, **kw):
            mutations.append(("delete", a, kw))
        def eval(self, *a, **kw):
            mutations.append(("eval", a, kw))

    fake_redis_mod = types.ModuleType("redis")
    fake_redis_mod.Redis = types.SimpleNamespace(from_url=lambda *a, **kw: _FakeRedisClient())
    sys.modules["redis"] = fake_redis_mod
    producer = DecisionSignalProducer.redis("redis://unused:6379/0", queue_key=qkey)
    batch = producer.produce({
        "decision_id": "dec-rb-2eebe2b7a8af4c0c909991a13bba75a6-76fdd54e9d601a08260e875a",
        "signal_id": sid,
        "run_id": "run-rb-2eebe2b7a8af4c0c909991a13bba75a6-76fdd54e9d601a08260e875a",
        "strategy_id": "strategy-paper-001",
        "timestamp": "2026-06-14T14:30:00Z",
        "symbol": "AAPL.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 1,
        "quantity_type": "SHARES",
        "tenant_id": "tenant-dev",
        "environment": "paper",
    })
    assert batch.count == 0
    assert enqueued == []
    assert mutations == []
    assert kv == {ekey: fence_value}
print("ok")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
