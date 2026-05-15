#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import smoke_operator_fallback_drills as drills


class OperatorFallbackDrillSmokeTest(unittest.TestCase):
    def test_bff_down_fallback_drill_exercises_non_bff_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            summary = drills.run_smoke(output_dir=output_dir)

            self.assertEqual(summary["status"], "passed")
            self.assertFalse(summary["bff_surface_used"])
            self.assertFalse(summary["bff_modules_loaded"])
            self.assertFalse(summary["production_bff_ha_changed"])
            self.assertEqual(summary["surfaces"]["S-IAPI"]["status_after"], "paused")
            self.assertEqual(summary["surfaces"]["S-CLI"]["actions"], ["pause", "liquidate"])
            self.assertEqual(summary["surfaces"]["S-CLI"]["pause_status_after"], "paused")
            self.assertEqual(summary["surfaces"]["S-CLI"]["pause_binding_status_after"], "paused")
            self.assertEqual(summary["surfaces"]["S-CLI"]["liquidate_binding_status_after"], "retired")
            self.assertEqual(summary["surfaces"]["S-EMRG"]["old_binding_status_after"], "retired")
            self.assertEqual(summary["surfaces"]["S-EMRG"]["replacement_binding_status"], "active")
            self.assertEqual(summary["surfaces"]["S-EMRG"]["telemetry_ack_status"], "acknowledged")
            self.assertGreaterEqual(summary["audit_evidence"]["kill_switch_audit_count"], 2)
            self.assertIn("liquidate", summary["audit_evidence"]["kill_switch_actions"])
            self.assertIn("replace", summary["audit_evidence"]["kill_switch_actions"])

            archived = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(archived["task_id"], drills.TASK_ID)
            self.assertTrue((output_dir / "s_iapi_command_record_response.json").exists())
            self.assertTrue((output_dir / "kill_switch_audit_log_response.json").exists())


if __name__ == "__main__":
    unittest.main()
