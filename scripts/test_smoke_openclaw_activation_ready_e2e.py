from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

import smoke_openclaw_activation_ready_e2e as smoke


ROOT = Path(__file__).resolve().parents[1]


class OpenClawActivationReadyE2ETest(unittest.TestCase):
    def test_smoke_runs_default_degraded_and_activation_ready_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = smoke.run_smoke(Path(tmp))

            self.assertTrue(report["summary"]["smoke_passed"])
            self.assertFalse(report["summary"]["production_broker_enabled"])
            self.assertFalse(report["summary"]["live_execution_enabled"])

            rows = {row["row"]: row for row in report["rows"]}
            self.assertEqual(rows["default:readyz-degraded"]["status"], "passed")
            self.assertEqual(rows["default:paper-denied"]["evidence"]["body"]["error_code"], "PAPER_ADAPTER_DISABLED")
            self.assertEqual(rows["default:live-denied"]["evidence"]["body"]["error_code"], "LIVE_EXECUTION_DISABLED")

            self.assertEqual(rows["activation:capabilities-upstream"]["status"], "passed")
            self.assertEqual(rows["activation:session-lifecycle"]["evidence"]["session"]["state"], "active")
            self.assertEqual(rows["activation:tool-resolution"]["evidence"]["body"]["effective_tools"], ["research.search"])
            self.assertTrue(rows["activation:paper-order"]["evidence"]["body"]["order"]["sim_fill_flag"])
            self.assertEqual(
                rows["activation:live-order-denied"]["evidence"]["body"]["error_code"],
                "LIVE_EXECUTION_DISABLED",
            )

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            exit_code = smoke.main(["--output-dir", str(Path(tmp) / "out"), "--json-out", str(out)])

            self.assertEqual(exit_code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["task_id"], smoke.TASK_ID)
            self.assertTrue(report["summary"]["smoke_passed"])

    def test_compose_documents_openclaw_activation_ready_profile(self) -> None:
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        service = compose["services"]["openclaw-activation-ready-e2e"]

        self.assertEqual(service["profiles"], ["openclaw-activation-ready-e2e"])
        self.assertEqual(service["image"], "pantheon-openclaw-activation-ready-e2e-adapter:latest")
        self.assertEqual(service["pull_policy"], "build")
        self.assertEqual(service["build"]["dockerfile"], "services/openclaw-gateway-adapter/Dockerfile")
        self.assertEqual(service["command"], ["python", "scripts/smoke_openclaw_activation_ready_e2e.py"])
        self.assertEqual(service["environment"]["OPENCLAW_PRODUCTION_BROKER_ENABLED"], "false")
        self.assertEqual(service["environment"]["OPENCLAW_LIVE_ADAPTER_ENABLED"], "false")
        self.assertEqual(service["environment"]["PANTHEON_LIVE_BROKER_ENABLED"], "false")
        self.assertNotIn("ports", service)


if __name__ == "__main__":
    unittest.main()
