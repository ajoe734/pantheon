from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_canary_human_gate_smoke as smoke  # noqa: E402


class CanaryHumanGateSmokeTest(unittest.TestCase):
    def test_smoke_requires_human_gate_refs_and_broker_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = smoke.run_smoke(Path(tmp))

        self.assertEqual(report["task_id"], smoke.TASK_ID)
        self.assertTrue(report["summary"]["smoke_passed"])
        self.assertFalse(report["summary"]["production_live_order_submitted"])
        self.assertFalse(report["summary"]["real_capital_used"])
        self.assertTrue(report["summary"]["operator_approval_required"])
        self.assertTrue(report["summary"]["broker_sandbox_smoke_required"])
        self.assertTrue(report["assertions"]["live_target_rejected"])
        self.assertTrue(report["assertions"]["production_live_boundary_must_be_fail_closed"])

        rows = {row["row"]: row for row in report["rows"]}
        self.assertEqual(rows["ready-with-explicit-human-gate-and-broker-smoke"]["status"], "passed")
        self.assertEqual(
            rows["ready-with-explicit-human-gate-and-broker-smoke"]["evidence"]["target_stage"],
            "canary",
        )
        self.assertEqual(
            rows["ready-with-explicit-human-gate-and-broker-smoke"]["evidence"]["live_gate_status"],
            "rejected",
        )
        self.assertIn(
            "operator_approval_ref",
            rows["missing-operator-approval-keeps-packet-incomplete"]["evidence"]["detail"],
        )
        self.assertEqual(rows["missing-broker-smoke-keeps-packet-incomplete"]["status"], "passed")

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "safe-004.json"
            exit_code = smoke.main(["--output-dir", str(Path(tmp) / "work"), "--json-out", str(out)])

            self.assertEqual(exit_code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["task_id"], smoke.TASK_ID)
            self.assertTrue(report["summary"]["smoke_passed"])


if __name__ == "__main__":
    unittest.main()
