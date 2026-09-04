from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


class TestCanonicalGovernanceImports(unittest.TestCase):
    def test_deploy_runs_from_repository_pythonpath_without_path_mutation(self):
        script = r'''
import json
import sys

before = list(sys.path)
from services.control_plane.cron.service import CronOrchestrator
from services.control_plane.governance import deployment_plan, deployment_saga, pool_runtime_compat
after = list(sys.path)

entry = {
    "registry_id": "reg-strat-001-1.0.0",
    "artifact_type": "model_artifact",
    "strategy_id": "strat-001",
    "version": "1.0.0",
    "artifact_state": "approved",
    "checksum": "sha256:abc123def4567890",
    "approval_decision_id": "approval-001",
    "approved_at": "2026-04-09T12:00:00Z",
    "lineage": {"source_run_ids": ["replication-run-001"]},
    "metadata": {
        "rollback": {
            "target_registry_id": "reg-strat-001-0.9.0",
            "target_version": "0.9.0",
        }
    },
    "deployment_summary": {"current_stage": "none"},
}
decision = {
    "decision_id": "approval-001",
    "target_id": "reg-strat-001-1.0.0",
    "target_version": "1.0.0",
    "decision_state": "decided",
    "decision": "approved",
    "capital_pool_id": "pool-001",
    "persona_id": "persona-ops",
}
result = CronOrchestrator().run(
    "pantheon.deploy",
    {
        "target_stage": "paper",
        "capital_pool_id": "pool-001",
        "approval_decision": decision,
        "registry_entry": entry,
    },
)
print(json.dumps({
    "path_unchanged": before == after,
    "module_names": [
        deployment_plan.__name__,
        deployment_saga.__name__,
        pool_runtime_compat.__name__,
    ],
    "target_stage": result.deployment_request["target_stage"],
    "saga_status": result.deployment_request["deployment_saga"]["saga"]["status"],
}))
'''
        env = os.environ.copy()
        env["PYTHONPATH"] = "."
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertTrue(result["path_unchanged"])
        self.assertEqual(
            result["module_names"],
            [
                "services.control_plane.governance.deployment_plan",
                "services.control_plane.governance.deployment_saga",
                "services.control_plane.governance.pool_runtime_compat",
            ],
        )
        self.assertEqual(result["target_stage"], "paper")
        self.assertEqual(result["saga_status"], "awaiting_binding")


if __name__ == "__main__":
    unittest.main()
