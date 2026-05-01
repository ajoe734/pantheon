"""Tests for the TRL activation evidence harness."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parent


class TestActivationSmoke(unittest.TestCase):
    def test_activation_smoke_requires_explicit_gate(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SERVICE_DIR / "activation_smoke.py"), "--backend", "stub"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("disabled by default", proc.stderr)

    def test_activation_smoke_writes_handoff_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SERVICE_DIR / "activation_smoke.py"),
                    "--enable-activation-ready",
                    "--backend",
                    "stub",
                    "--output-dir",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads((Path(tmp) / "activation_evidence_summary.json").read_text())
            self.assertEqual(summary["fb002_data_evidence"]["source_feedback_event_count"], 240)
            self.assertEqual(summary["fb002_data_evidence"]["preference_pair_count"], 240)
            self.assertTrue(summary["fb002_data_evidence"]["all_required_actions_present"])
            self.assertEqual(summary["real_trl_backend_attempt"]["status"], "not_attempted")
            self.assertEqual(summary["handoff_backend"], "stub_dpo")
            self.assertFalse(summary["governance_boundary"]["order_routing_enabled"])
            manifest = summary["handoff_artifact_manifest"]
            self.assertIn("evaluator_packet", manifest["files"])
            self.assertTrue((Path(tmp) / "evaluator_packet.json").exists())


if __name__ == "__main__":
    unittest.main()
