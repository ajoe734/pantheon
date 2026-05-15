from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_openclaw_broker_tool_denial_smoke as smoke


class OpenClawBrokerToolDenialSmokeTest(unittest.TestCase):
    def test_smoke_denies_broker_tools_before_upstream_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = smoke.run_smoke(Path(tmp))

        self.assertEqual(report["task_id"], smoke.TASK_ID)
        self.assertTrue(report["summary"]["smoke_passed"])
        self.assertFalse(report["summary"]["production_broker_enabled"])
        self.assertFalse(report["summary"]["live_execution_enabled"])
        self.assertFalse(report["summary"]["canary_execution_enabled"])
        self.assertFalse(report["summary"]["capital_binding_enabled"])
        self.assertTrue(report["assertions"]["broker_tools_denied_by_adapter_policy"])
        self.assertTrue(report["assertions"]["denied_tools_not_dispatched_upstream"])
        self.assertTrue(report["assertions"]["denied_workflows_not_dispatched_upstream"])

        rows = {row["row"]: row for row in report["rows"]}
        self.assertEqual(rows["effective-tools-exclude-broker-tools"]["evidence"]["effective_tools"], [smoke.SAFE_TOOL])
        self.assertEqual(
            sorted(rows["effective-tools-exclude-broker-tools"]["evidence"]["policy_blocked_tools"]),
            sorted(smoke.BLOCKED_TOOLS),
        )
        for tool_name in smoke.BLOCKED_TOOLS:
            row = rows[f"tool-denied:{tool_name}"]
            self.assertEqual(row["status"], "passed")
            self.assertEqual(row["evidence"]["error_code"], "BRIDGE_TOOL_DENIED")
            self.assertEqual(row["evidence"]["policy_class"], "always_blocked")
        for workflow_ref in smoke.BLOCKED_WORKFLOWS:
            row = rows[f"workflow-denied:{workflow_ref}"]
            self.assertEqual(row["status"], "passed")
            self.assertEqual(row["evidence"]["error_code"], "BRIDGE_WORKFLOW_DENIED")
            self.assertEqual(row["evidence"]["policy_class"], "always_blocked")

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "safe-003.json"
            exit_code = smoke.main(["--output-dir", str(Path(tmp) / "work"), "--json-out", str(out)])

            self.assertEqual(exit_code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["task_id"], smoke.TASK_ID)
            self.assertTrue(report["summary"]["smoke_passed"])


if __name__ == "__main__":
    unittest.main()
