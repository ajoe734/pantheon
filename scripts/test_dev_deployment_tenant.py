"""Verify the dev root lane's actual Compose tenant interpolation, without up."""
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DevDeploymentTenantTest(unittest.TestCase):
    def test_root_binds_both_consumers_to_the_bff_tenant(self):
        source = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text()
        root_case = source.split('case "${PANTHEON_DEPLOY_COMPONENT}" in\n  root)')[1].split('\n  bff)')[0]
        assignment = 'export PANTHEON_DEPLOYMENT_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}"'
        self.assertEqual(root_case.count(assignment), 1)
        self.assertLess(root_case.index("prepare_deploy_worktree"), root_case.index(assignment))
        self.assertLess(root_case.index(assignment), root_case.index("docker compose"))
        # Execute the source assignment and real Compose interpolation. A stale
        # caller value must not split the consumer and producer tenants.
        rendered = subprocess.run(
            ["bash", "-euc", assignment + "\nexec docker compose --env-file /dev/null -f docker-compose.yml config --format json"],
            cwd=ROOT,
            env={"PATH": os.environ["PATH"], "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
                 "PANTHEON_DEPLOYMENT_TENANT_ID": "default"},
            text=True, capture_output=True, check=True,
        )
        services = json.loads(rendered.stdout)["services"]
        for name in ("runtime-manager", "deployment-outbox-consumer"):
            self.assertEqual(services[name]["environment"]["PANTHEON_DEPLOYMENT_TENANT_ID"], "tenant-dev")
        self.assertEqual(services["runtime-manager"]["environment"]["PANTHEON_LIVE_BROKER_ENABLED"], "false")
        self.assertEqual(services["runtime-manager"]["environment"]["PANTHEON_CANARY_EXECUTION_ENABLED"], "false")


if __name__ == "__main__":
    unittest.main()
