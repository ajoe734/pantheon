"""Tests for decision-to-signal producer."""
from __future__ import annotations

import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
