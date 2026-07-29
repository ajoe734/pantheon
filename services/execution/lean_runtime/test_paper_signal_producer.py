"""Tests for paper_signal_producer (closes the missing-signal-source gap)."""
import unittest
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import json
import io

from services.execution.lean_runtime.paper_signal_producer import (
    BindingRef,
    PaperSignalProducer,
    SmokeStrategy,
    BoundedPaperStrategy,
    build_smoke_signal,
    fetch_eligible_paper_bindings,
    healthcheck,
    main,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.trade_journey.correlation_envelope import validate_envelope

_NOW = "2026-06-14T10:30:00Z"
_BINDING = BindingRef(binding_id="rb-test-001", strategy_id="strategy-test-001", symbol="AAPL.US")


class TestSmokeSignalContract(unittest.TestCase):
    def test_smoke_signal_passes_store_and_consumer_validation(self) -> None:
        sig = build_smoke_signal(_BINDING, _NOW)
        # store-side transport validation (raises on failure)
        store = InMemoryPendingSignalStore()
        store.enqueue(sig)
        self.assertEqual(store.queue_depth(), 1)
        # consumer-side canonical validation (research/schema.json)
        consumer = SignalConsumer(store_client=MagicMock())
        validated = consumer._validate(sig)
        self.assertIsNotNone(validated, "smoke signal must pass consumer _validate")
        self.assertEqual(validated["action"], "BUY")
        self.assertEqual(validated["symbol"], "AAPL.US")
        envelope = validate_envelope(validated["correlation_envelope"])
        self.assertEqual(envelope["journey_id"], validated["journey_id"])
        self.assertEqual(envelope["tenant_id"], "default")
        self.assertEqual(envelope["environment"], "paper")
        self.assertTrue(validated["run_id"].startswith("run-rb-test-001-"))


class TestPaperSignalProducer(unittest.TestCase):
    def test_producer_enqueues_per_binding(self) -> None:
        stores = {}

        def store_for(binding: Any) -> InMemoryPendingSignalStore:
            bid = getattr(binding, "binding_id", "")
            if not bid and isinstance(binding, dict):
                bid = binding.get("binding_id", "")
            return stores.setdefault(bid, InMemoryPendingSignalStore())

        bindings = [
            BindingRef("rb-a", "strategy-a"),
            BindingRef("rb-b", "strategy-b"),
        ]
        producer = PaperSignalProducer(store_for=store_for, strategy=SmokeStrategy())
        result = producer.tick(bindings, _NOW)

        self.assertEqual(result, {"rb-a": 1, "rb-b": 1})
        self.assertEqual(stores["rb-a"].queue_depth(), 1)
        self.assertEqual(stores["rb-b"].queue_depth(), 1)
        # signals round-trip and carry the binding's own strategy/binding ids
        drained = stores["rb-a"].get_pending()
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["strategy_id"], "strategy-a")
        self.assertEqual(drained[0]["binding_id"], "rb-a")
        self.assertEqual(
            drained[0]["journey_id"],
            validate_envelope(drained[0]["correlation_envelope"])["journey_id"],
        )

    def test_smoke_strategy_emits_unique_signal_ids_across_ticks(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(store_for=lambda b: store, strategy=SmokeStrategy())
        producer.produce(_BINDING, _NOW)
        producer.produce(_BINDING, _NOW)
        ids = {s["signal_id"] for s in store.get_pending()}
        self.assertEqual(len(ids), 2, "each tick must emit a distinct signal_id")

    def test_parse_bindings_env(self) -> None:
        from services.execution.lean_runtime.paper_signal_producer import parse_bindings_env
        out = parse_bindings_env('[{"binding_id":"rb-x","strategy_id":"s-x"},{"binding_id":"rb-y","strategy_id":"s-y","symbol":"2330.TW"}]')
        self.assertEqual([b.binding_id for b in out], ["rb-x", "rb-y"])
        self.assertEqual(out[1].symbol, "2330.TW")
        self.assertEqual(parse_bindings_env(""), [])

    @patch.dict("os.environ", {"PANTHEON_TENANT_ID": "test-tenant-123"})
    def test_bounded_paper_strategy_and_decision_producer_flow(self) -> None:
        stores = {}

        def store_for(binding: Any) -> InMemoryPendingSignalStore:
            bid = binding if isinstance(binding, str) else binding.get("binding_id")
            if not bid:
                bid = getattr(binding, "binding_id", "")
            return stores.setdefault(bid, InMemoryPendingSignalStore())

        binding = {
            "binding_id": "rb-tw-test",
            "runtime_id": "rt-tw-test",
            "capital_pool_id": "pool-tw-test",
            "artifact_id": "art-tw-test",
            "artifact_version": "2.0.0",
            "persona_capital_binding_id": "pcb-personaX-001",
            "symbol": "2330.TW",
            "metadata": {
                "strategy_id": "tw_momentum_v1"
            }
        }

        producer = PaperSignalProducer(store_for=store_for, strategy=BoundedPaperStrategy())
        result = producer.produce(binding, _NOW)
        self.assertEqual(result, 1)

        store = stores["rb-tw-test"]
        self.assertEqual(store.queue_depth(), 1)
        [sig] = store.get_pending()

        self.assertEqual(sig["version"], "1.0")
        self.assertEqual(sig["binding_id"], "rb-tw-test")
        self.assertEqual(sig["runtime_id"], "rt-tw-test")
        self.assertEqual(sig["strategy_id"], "tw_momentum_v1")
        self.assertEqual(sig["symbol"], "2330.TW")
        self.assertEqual(sig["action"], "BUY")
        self.assertEqual(sig["direction"], "LONG")
        envelope = validate_envelope(sig["correlation_envelope"])
        self.assertEqual(envelope["tenant_id"], "test-tenant-123")
        self.assertEqual(envelope["environment"], "paper")
        self.assertEqual(envelope["journey_id"], sig["journey_id"])
        self.assertTrue(sig["run_id"].startswith("run-rb-tw-test-"))

        # Verify metadata / identities
        self.assertEqual(sig["metadata"]["tenant_id"], "test-tenant-123")
        self.assertEqual(sig["metadata"]["persona_id"], "personaX")
        self.assertEqual(sig["metadata"]["persona_capital_binding_id"], "pcb-personaX-001")
        self.assertEqual(sig["metadata"]["capital_pool_id"], "pool-tw-test")
        self.assertEqual(sig["metadata"]["artifact_id"], "art-tw-test")
        self.assertEqual(sig["metadata"]["artifact_version"], "2.0.0")
        self.assertFalse(sig["metadata"]["is_real_order"])
        self.assertFalse(sig["metadata"]["is_real_capital"])

    @patch("urllib.request.urlopen")
    def test_fetch_eligible_paper_bindings(self, mock_urlopen) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "bindings": [
                {
                    "binding_id": "rb-1",
                    "status": "active",
                    "deployment_mode": "paper",
                },
                {
                    "binding_id": "rb-2",
                    "status": "paused",
                    "deployment_mode": "paper",
                }
            ],
            "excluded": []
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        bindings = fetch_eligible_paper_bindings("http://runtime-manager:8081", "token-xyz")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["binding_id"], "rb-1")
        self.assertEqual(bindings[0]["status"], "active")

    def test_health_requires_recent_paper_only_tick(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            health_file = Path(directory) / "paper-producer-health.json"
            env = {
                "SIGNAL_STORE_URL": "redis://signal-store:6379",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PAPER_PRODUCER_INTERVAL_SECONDS": "1",
                "PAPER_PRODUCER_MAX_TICKS": "1",
                "PAPER_PRODUCER_HEALTH_FILE": str(health_file),
                "PANTHEON_LIVE_BROKER_ENABLED": "false",
                "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
            }
            with patch.dict("os.environ", env, clear=False), patch(
                "services.execution.lean_runtime.paper_signal_producer."
                "fetch_eligible_paper_bindings",
                return_value=[],
            ):
                self.assertEqual(main(), 0)
                self.assertEqual(healthcheck(), 0)

            state = json.loads(health_file.read_text(encoding="utf-8"))
            self.assertEqual(state["worker_name"], "paper-signal-producer")
            self.assertEqual(state["status"], "ok")
            self.assertEqual(state["ticks"], 1)
            self.assertEqual(state["execution_mode"], "paper")
            self.assertFalse(state["live_capital_enabled"])
            self.assertFalse(state["live_order_submission_enabled"])

    def test_paper_producer_refuses_live_execution_flags(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SIGNAL_STORE_URL": "redis://signal-store:6379",
                "PANTHEON_LIVE_BROKER_ENABLED": "true",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "refuses live/canary execution flags",
            ):
                main()


if __name__ == "__main__":
    unittest.main()
