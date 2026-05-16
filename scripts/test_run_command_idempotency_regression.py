from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_command_idempotency_regression as smoke


class CommandIdempotencyRegressionSmokeTest(unittest.TestCase):
    def test_smoke_proves_replay_conflict_and_durable_record(self) -> None:
        report = smoke.run_smoke()

        self.assertEqual(report["task_id"], smoke.TASK_ID)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["summary"]["smoke_passed"])
        self.assertEqual(report["summary"]["route"], "POST /bff/v1/commands")
        self.assertFalse(report["summary"]["live_capital_side_effects"])

        rows = {row["row"]: row for row in report["rows"]}
        self.assertEqual(rows["first-command-accepted"]["status"], "passed")
        self.assertEqual(rows["same-key-same-payload-replays"]["status"], "passed")
        self.assertEqual(rows["same-key-different-payload-conflicts"]["status"], "passed")
        self.assertEqual(rows["body-idempotency-key-rejected"]["status"], "passed")
        self.assertEqual(rows["single-durable-command-record"]["status"], "passed")

        record = rows["single-durable-command-record"]["evidence"]
        self.assertEqual(record["record_count"], 1)
        self.assertEqual(record["idempotency_record"]["idempotency_key"], smoke.HEADERS["Idempotency-Key"])
        self.assertEqual(record["idempotency_record"]["status"], "succeeded")
        self.assertTrue(record["idempotency_record"]["request_hash_present"])
        self.assertEqual(record["command_envelope"]["trace_id"], smoke.HEADERS["X-Trace-Id"])
        self.assertEqual(record["trace_context"]["correlation_id"], smoke.HEADERS["X-Correlation-Id"])
        self.assertEqual(record["audit_action_type"], "bff.command.accepted")

    def test_cli_writes_json_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "command-idempotency-regression.json"

            exit_code = smoke.main(["--json-out", str(out)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["task_id"], smoke.TASK_ID)
            self.assertTrue(payload["summary"]["smoke_passed"])


if __name__ == "__main__":
    unittest.main()
