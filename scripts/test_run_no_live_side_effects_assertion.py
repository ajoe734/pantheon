from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_no_live_side_effects_assertion as smoke  # noqa: E402


class NoLiveSideEffectsAssertionSmokeTest(unittest.TestCase):
    def test_default_smoke_passes_against_current_track_e_evidence(self) -> None:
        report = smoke.run_smoke()

        self.assertEqual(report["task_id"], smoke.TASK_ID)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["summary"]["smoke_passed"])
        self.assertEqual(report["summary"]["side_effect_violation_count"], 0)
        self.assertFalse(report["summary"]["live_capital_side_effects"])
        self.assertFalse(report["summary"]["production_live_order_submitted"])
        self.assertTrue(report["assertions"]["required_evidence_loaded"])
        self.assertTrue(report["assertions"]["side_effect_flags_all_false"])
        self.assertTrue(report["assertions"]["non_live_ooda_packets_reject_live_capital_side_effects"])

    def test_smoke_fails_when_live_side_effect_flag_is_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            evidence_path = Path(tmpdir) / "bad-evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "task_id": "unit-bad",
                        "environment": "paper",
                        "live_capital_side_effects": True,
                        "production_live_order_submitted": False,
                        "real_capital_used": False,
                    }
                ),
                encoding="utf-8",
            )

            report = smoke.run_smoke(required_paths=[evidence_path], optional_paths=[])

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["summary"]["smoke_passed"])
        self.assertEqual(report["summary"]["side_effect_violation_count"], 1)
        evidence_row = report["rows"][0]
        self.assertEqual(evidence_row["row"], "evidence-side-effect-flags-stay-false")
        self.assertEqual(evidence_row["status"], "failed")
        self.assertEqual(evidence_row["evidence"]["violations"][0]["field"], "live_capital_side_effects")

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out = root / "mgmt-safe-005.json"

            exit_code = smoke.main(["--json-out", str(out)])

            self.assertEqual(exit_code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["task_id"], smoke.TASK_ID)
            self.assertTrue(report["summary"]["smoke_passed"])
            self.assertEqual(report["summary"]["side_effect_violation_count"], 0)


if __name__ == "__main__":
    unittest.main()
