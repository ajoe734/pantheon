import sys
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.lean_runtime.bootstrap_contract import (
    materialize_runtime_bootstrap_request,
    BootstrapContractError,
)

def _deployment_plan(**overrides):
    plan = {
        "plan_id": "dp-smoke-001",
        "artifact_id": "art-smoke",
        "artifact_version": "1.0.0",
        "artifact_checksum": "sha256:smoke",
        "strategy_id": "strat-smoke",
        "capital_pool_id": "pool-smoke",
        "target_stage": "live",
        "runtime_role": "live",
        "runtime_config_ref": "/workspace/lean/Launcher/config.json",
    }
    plan.update(overrides)
    return plan

def _runtime_binding(**overrides):
    binding = {
        "binding_id": "rtb-smoke-001",
        "runtime_id": "rt-smoke-001",
        "plan_id": "dp-smoke-001",
        "artifact_id": "art-smoke",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-smoke",
        "deployment_mode": "live",
        "persona_capital_binding_id": "pcb-smoke-001",
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
        },
    }
    binding.update(overrides)
    return binding

class TestLiveBrokerDisabledSmoke(unittest.TestCase):
    def test_live_broker_disabled_enforced(self):
        # Attempt to enable live broker
        with self.assertRaisesRegex(BootstrapContractError, "live_broker_enabled"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(
                    runtime_config={"live_broker_enabled": True},
                ),
                runtime_binding=_runtime_binding(),
            )
        
        # Verify it defaults to disabled when not requested
        request = materialize_runtime_bootstrap_request(
            deployment_plan=_deployment_plan(),
            runtime_binding=_runtime_binding(),
        )
        self.assertFalse(request.runtime_config.live_broker_enabled)

if __name__ == "__main__":
    unittest.main()
