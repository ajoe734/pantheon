
import os
import sys

# Add Algorithm.Python to sys.path
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
    "PANTHEON_ENGINE_BRIDGE_REMOTE": "ajoe734/pantheon-lean.git",
    "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": "pantheon/lean",
    "PANTHEON_ENGINE_BRIDGE_COMMIT": "smoke-commit",
    "PANTHEON_RUNTIME_ADAPTER_VERSION": "0.1.0",
    "PANTHEON_TRACE_ID": "trace-smoke",
    "PANTHEON_CORRELATION_ID": "corr-smoke",
})

class SmokeAlgo(PantheonAlgoBase):
    def __init__(self):
        self.events = []
    
    def Debug(self, message):
        self.events.append(json.loads(message))

def run_smoke():
    try:
        algo = SmokeAlgo()
        algo.Initialize()
        algo.emit_pantheon_event("SmokeTestEvent", metrics={"smoke": 1})
        
        if len(algo.events) > 0:
            print("Smoke test passed: Event emitted.")
            print(f"Event: {algo.events[-1]}")
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
