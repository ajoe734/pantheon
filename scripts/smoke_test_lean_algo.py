
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Add externalized integrations/lean and fallback lean/Algorithm.Python to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'integrations', 'lean')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'lean', 'Algorithm.Python')))

import json
from pantheon_algo.base import PantheonAlgoBase
os.environ.update({
    "PANTHEON_RUNTIME_BINDING_ID": "smoke-test-binding",
    "PANTHEON_RUNTIME_ID": "smoke-test-runtime",
    "PANTHEON_DEPLOYMENT_PLAN_ID": "smoke-test-plan",
    "PANTHEON_DEPLOYMENT_STAGE": "paper",
    "PANTHEON_RUNTIME_ROLE": "paper",
    "PANTHEON_ARTIFACT_ID": "art-smoke",
    "PANTHEON_ARTIFACT_VERSION": "1.0.0",
    "PANTHEON_ARTIFACT_CHECKSUM": "sha256:smoke",
    "PANTHEON_STRATEGY_ID": "strat-smoke",
    "PANTHEON_CAPITAL_POOL_ID": "pool-smoke",
    "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-smoke",
    "PANTHEON_ENGINE_BRIDGE_REMOTE": "https://github.com/QuantConnect/Lean.git",
    "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": "integrations/lean/pantheon_algo",
    "PANTHEON_ENGINE_BRIDGE_COMMIT": "23b735d99a357807dc0df9f4c51d30f05fe0d277",
    "PANTHEON_RUNTIME_ADAPTER_VERSION": "0.1.0",
    "PANTHEON_TRACE_ID": "trace-smoke",
    "PANTHEON_CORRELATION_ID": "corr-smoke",
})

class SmokeAlgo(PantheonAlgoBase):
    def __init__(self):
        super().__init__()
        self.events = []

    def Debug(self, message):
        self.events.append(json.loads(message))

def run_smoke():
    try:
        algo = SmokeAlgo()
        algo.Initialize()

        # 1. Verify consumer and store wiring (not disabled!)
        if algo._consumer is None:
            print("Smoke test failed: SignalConsumer was not initialized.")
            return False
        if algo._signal_store is None:
            print("Smoke test failed: PendingSignalStore was not initialized.")
            return False

        # 2. Verify scheduling
        scheduled = getattr(algo.Schedule, "scheduled_events", [])
        if not scheduled:
            print("Smoke test failed: SignalConsumer was not scheduled.")
            return False
        print(f"Smoke test: SignalConsumer scheduled ({len(scheduled)} event(s)).")

        # 3. Prove signal intake and order execution
        signal = {
            "signal_id": "smoke-sig-001",
            "version": "1.0",
            "strategy_id": "strat-smoke",
            "binding_id": "smoke-test-binding",
            "runtime_id": "smoke-test-runtime",
            "metadata": {
                "capital_pool_id": "pool-smoke",
            },
            "timestamp": "2026-09-19T12:00:00Z",
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        algo._signal_store.enqueue(signal)
        initial_depth = algo._signal_store.queue_depth()
        if initial_depth != 1:
            print(f"Smoke test failed: Expected queue depth 1, got {initial_depth}")
            return False

        algo.OnData()

        remaining_depth = algo._signal_store.queue_depth()
        if remaining_depth != 0:
            print(f"Smoke test failed: Queue was not drained, remaining depth: {remaining_depth}")
            return False

        if not algo.orders:
            print("Smoke test failed: No order executed from signal intake.")
            return False
        print(f"Smoke test passed: Signal consumed and order executed: {algo.orders[-1]}")

        # 4. Prove telemetry bridge event
        algo.emit_pantheon_event("SmokeTestEvent", metrics={"smoke": 1})
        if len(algo.events) > 0:
            print(f"Smoke test passed: Bridge event emitted: {algo.events[-1]['event_type']}")
            return True
        else:
            print("Smoke test failed: No event emitted.")
            return False
    except Exception as e:
        print(f"Smoke test failed with exception: {e}")
        return False

if __name__ == "__main__":
    if run_smoke():
        sys.exit(0)
    else:
        sys.exit(1)
