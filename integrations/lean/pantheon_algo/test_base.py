from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_LEAN_DIR = Path(__file__).resolve().parent.parent
if str(_LEAN_DIR) not in sys.path:
    sys.path.insert(0, str(_LEAN_DIR))

from pantheon_algo.base import EngineReplayAlgo, PantheonAlgoBase, PersistentLeanObjectStore
from services.execution.lean_runtime.runtime_context import RuntimeContextError


_CONTEXT_ENV = {
    "PANTHEON_RUNTIME_BINDING_ID": "rtb-paper-001",
    "PANTHEON_RUNTIME_ID": "rt-paper-001",
    "PANTHEON_DEPLOYMENT_PLAN_ID": "dp-paper-001",
    "PANTHEON_DEPLOYMENT_STAGE": "paper",
    "PANTHEON_RUNTIME_ROLE": "paper",
    "PANTHEON_ARTIFACT_ID": "art-001",
    "PANTHEON_ARTIFACT_VERSION": "1.0.0",
    "PANTHEON_ARTIFACT_CHECKSUM": "sha256:abc",
    "PANTHEON_STRATEGY_ID": "strat-001",
    "PANTHEON_CAPITAL_POOL_ID": "pool-001",
    "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-001",
    "PANTHEON_ENGINE_BRIDGE_REMOTE": "ajoe734/pantheon-lean.git",
    "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": "pantheon/lean",
    "PANTHEON_ENGINE_BRIDGE_COMMIT": "abc123",
    "PANTHEON_RUNTIME_ADAPTER_VERSION": "0.1.0",
    "PANTHEON_TRACE_ID": "trace-001",
    "PANTHEON_CORRELATION_ID": "corr-001",
}


@contextmanager
def patched_env(values: dict[str, str]) -> Iterator[None]:
    keys = set(values) | {key for key in os.environ if key.startswith("PANTHEON_")}
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key in keys:
            os.environ.pop(key, None)
        for key, value in previous.items():
            if value is not None:
                os.environ[key] = value


class CapturingAlgo(PantheonAlgoBase):
    def __init__(self) -> None:
        self.debug_messages: list[str] = []

    def Debug(self, message: str) -> None:
        self.debug_messages.append(message)


class PantheonAlgoBaseContextTests(unittest.TestCase):
    def test_initialize_loads_runtime_context_from_env(self) -> None:
        with patched_env(_CONTEXT_ENV):
            algo = CapturingAlgo()
            algo.Initialize()

        context = algo.get_pantheon_context()
        self.assertIsNotNone(context)
        self.assertEqual(context.runtime_binding_id, "rtb-paper-001")
        self.assertEqual(context.deployment_plan_id, "dp-paper-001")
        self.assertEqual(context.bridge.path, "pantheon/lean")

    def test_emit_pantheon_event_attaches_context_metadata(self) -> None:
        with patched_env(_CONTEXT_ENV):
            algo = CapturingAlgo()
            algo.Initialize()
            event = algo.emit_pantheon_event(
                "PaperHeartbeat",
                metrics={"cash": 100000},
                metadata={"source": "unit-test"},
            )

        self.assertEqual(event["runtime_binding_id"], "rtb-paper-001")
        self.assertEqual(event["runtime_id"], "rt-paper-001")
        self.assertEqual(event["deployment_plan_id"], "dp-paper-001")
        self.assertEqual(event["deployment_stage"], "paper")
        self.assertEqual(event["artifact_id"], "art-001")
        self.assertEqual(event["capital_pool_id"], "pool-001")
        self.assertEqual(event["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
        self.assertEqual(event["engine_bridge_path"], "pantheon/lean")
        self.assertEqual(event["engine_bridge_commit"], "abc123")
        self.assertEqual(event["metrics"]["cash"], 100000)
        self.assertEqual(event["metadata"]["source"], "unit-test")

    def test_missing_managed_context_fails_closed(self) -> None:
        with patched_env(
            {
                "PANTHEON_DEPLOYMENT_STAGE": "staging",
                "PANTHEON_RUNTIME_ROLE": "paper",
            }
        ):
            algo = CapturingAlgo()
            with self.assertRaisesRegex(RuntimeContextError, "runtime context is required"):
                algo.Initialize()

    def test_consumer_wiring_and_signal_intake(self) -> None:
        with patched_env(_CONTEXT_ENV):
            algo = CapturingAlgo()
            algo.Initialize()

            self.assertIsNotNone(algo._consumer)
            self.assertIsNotNone(algo._signal_store)
            self.assertGreater(len(algo.Schedule.scheduled_events), 0)

            signal = {
                "signal_id": "sig-test-001",
                "version": "1.0",
                "strategy_id": "strat-001",
                "binding_id": "rtb-paper-001",
                "runtime_id": "rt-paper-001",
                "metadata": {
                    "capital_pool_id": "pool-001",
                },
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 0.5,
                "quantity_type": "PERCENT_PORTFOLIO",
            }
            algo._signal_store.enqueue(signal)
            self.assertEqual(algo._signal_store.queue_depth(), 1)

            algo.OnData()

            self.assertEqual(algo._signal_store.queue_depth(), 0)
            self.assertGreater(len(algo.orders), 0)
            order = algo.orders[0]
            self.assertEqual(order["method"], "SetHoldings")
            self.assertEqual(order["percentage"], 0.5)

    def test_engine_replay_and_duplicate_suppression_across_restart(self) -> None:
        object_store = PersistentLeanObjectStore()
        signal = {
            "signal_id": "engine-replay-sig-001",
            "version": "1.0",
            "strategy_id": "strat-engine-replay-001",
            "binding_id": "rtb-paper-001",
            "runtime_id": "rt-paper-001",
            "metadata": {
                "capital_pool_id": "pool-001",
                "model_id": "model-alpha-v1",
                "tenant_id": "tenant-ops",
                "session_id": "session-restart-001",
            },
            "timestamp": "2026-09-19T12:00:00Z",
            "symbol": "SPY.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }

        # Run 1: Initial run
        with patched_env({**_CONTEXT_ENV, "PANTHEON_MODEL_ID": "model-alpha-v1", "PANTHEON_TENANT_ID": "tenant-ops", "PANTHEON_SESSION_ID": "session-restart-001"}):
            algo_run1 = EngineReplayAlgo(object_store=object_store)
            algo_run1.Initialize()
            self.assertFalse(algo_run1.is_restart)
            self.assertIsNone(algo_run1.prior_checkpoint)

            # Process initial signal
            result1 = algo_run1.process_replay_signal(signal)
            self.assertEqual(result1["status"], "FILLED")
            self.assertEqual(result1["new_orders_placed"], 1)
            self.assertFalse(result1["duplicate_suppressed"])
            self.assertEqual(len(algo_run1.executed_orders), 1)
            algo_run1.complete_replay()

            event_types1 = [e["event_type"] for e in algo_run1.events]
            self.assertIn("RuntimeContextLoaded", event_types1)
            self.assertIn("OrderFilledReplay", event_types1)
            self.assertIn("EngineReplayComplete", event_types1)

        # Run 2: Restart run with fresh instance sharing same persistent ObjectStore
        with patched_env({**_CONTEXT_ENV, "PANTHEON_MODEL_ID": "model-alpha-v1", "PANTHEON_TENANT_ID": "tenant-ops", "PANTHEON_SESSION_ID": "session-restart-001"}):
            algo_run2 = EngineReplayAlgo(object_store=object_store)
            algo_run2.Initialize()
            self.assertTrue(algo_run2.is_restart)
            self.assertIsNotNone(algo_run2.prior_checkpoint)
            self.assertEqual(algo_run2.prior_checkpoint["model_id"], "model-alpha-v1")
            self.assertEqual(algo_run2.prior_checkpoint["tenant_id"], "tenant-ops")
            self.assertEqual(algo_run2.prior_checkpoint["session_id"], "session-restart-001")

            # Replay the same signal - assert duplicate suppression
            result2 = algo_run2.process_replay_signal(signal)
            self.assertEqual(result2["status"], "DUPLICATE_SUPPRESSED")
            self.assertEqual(result2["new_orders_placed"], 0)
            self.assertTrue(result2["duplicate_suppressed"])
            self.assertEqual(len(algo_run2.executed_orders), 0)
            algo_run2.complete_replay()

            event_types2 = [e["event_type"] for e in algo_run2.events]
            self.assertIn("EngineRestartSuccess", event_types2)
            self.assertIn("OrderDuplicateSuppressed", event_types2)
            self.assertIn("EngineReplayComplete", event_types2)

    def test_engine_replay_wrong_binding_rejected(self) -> None:
        object_store = PersistentLeanObjectStore()
        wrong_signal = {
            "signal_id": "sig-rejected-001",
            "version": "1.0",
            "strategy_id": "strat-engine-replay-001",
            "binding_id": "rtb-mismatched-wrong-binding",
            "runtime_id": "rt-paper-001",
            "metadata": {
                "capital_pool_id": "pool-001",
                "model_id": "model-alpha-v1",
                "tenant_id": "tenant-ops",
                "session_id": "session-restart-001",
            },
            "timestamp": "2026-09-19T12:00:00Z",
            "symbol": "SPY.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        with patched_env({**_CONTEXT_ENV, "PANTHEON_RUNTIME_BINDING_ID": "rtb-paper-001"}):
            algo = EngineReplayAlgo(object_store=object_store)
            algo.Initialize()

            result = algo.process_replay_signal(wrong_signal)
            self.assertEqual(result["status"], "BINDING_MISMATCH")
            self.assertEqual(result["new_orders_placed"], 0)
            self.assertTrue(result.get("rejected", False))
            self.assertEqual(len(algo.executed_orders), 0)
            self.assertNotIn("sig-rejected-001", algo.processed_signals)
            self.assertFalse(object_store.ContainsKey(algo.CHECKPOINT_KEY))


if __name__ == "__main__":
    unittest.main()
