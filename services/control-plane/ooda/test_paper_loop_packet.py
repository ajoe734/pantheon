from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


OODA_DIR = Path(__file__).resolve().parent
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from paper_loop_packet import (  # noqa: E402
    PaperOodaLoopContext,
    build_evidence_packet,
    validate_evidence_packet,
    write_evidence_packet,
)


class TestPaperOodaLoopPacket(unittest.TestCase):
    def test_complete_paper_loop_packet_validates_and_replays(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:10:00Z")

        self.assertEqual(packet["validation_errors"], [])
        self.assertEqual(packet["environment"], "paper")
        self.assertFalse(packet["live_capital_side_effects"])

        ooda_packet = packet["ooda_packet"]
        self.assertEqual(ooda_packet["packet_id"], "ooda-paper-mgmt-loop-001")
        self.assertEqual(ooda_packet["status"], "closed")
        self.assertEqual(ooda_packet["environment"], "paper")
        self.assertFalse(ooda_packet["act"]["live_capital_side_effects"])

        self.assertEqual(
            ooda_packet["decide"]["approval_decision_id"],
            packet["approval_decision"]["decision_id"],
        )
        self.assertEqual(
            ooda_packet["decide"]["deployment_plan_id"],
            packet["deployment_plan"]["plan_id"],
        )
        self.assertEqual(
            ooda_packet["decide"]["evolution_decision_id"],
            packet["evolution_decision"]["decision_id"],
        )
        self.assertEqual(
            ooda_packet["act"]["runtime_binding_id"],
            packet["runtime_binding"]["binding_id"],
        )

        replay = packet["replay_validation"]
        self.assertTrue(replay["can_replay"])
        self.assertEqual(replay["record_count"], 7)
        self.assertEqual(replay["stage_transition_count"], 6)
        self.assertEqual(replay["latest_status"], "closed")
        for matched_ids in replay["query_matches"].values():
            self.assertIn(ooda_packet["packet_id"], matched_ids)

    def test_closed_packet_missing_stage_evidence_fails_validation(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:10:00Z")
        mutated = json.loads(json.dumps(packet))
        mutated["ooda_packet"]["observe"] = {
            "source_refs": [],
            "telemetry_refs": [],
            "signal_refs": [],
            "market_refs": [],
            "incident_refs": [],
            "human_feedback_refs": [],
        }

        errors = validate_evidence_packet(mutated)

        self.assertTrue(
            any("observe bundle" in error for error in errors),
            f"expected observe evidence error, got: {errors}",
        )

    def test_decide_link_mismatch_fails_validation(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:10:00Z")
        mutated = json.loads(json.dumps(packet))
        mutated["ooda_packet"]["decide"]["deployment_plan_id"] = "deployment-plan-wrong"

        errors = validate_evidence_packet(mutated)

        self.assertIn("ooda_packet decide.deployment_plan_id mismatch", errors)

    def test_write_evidence_packet_writes_task_and_milestone_files(self) -> None:
        packet = build_evidence_packet(
            PaperOodaLoopContext(packet_id="ooda-paper-mgmt-loop-test"),
            generated_at="2026-05-15T15:10:00Z",
        )
        with tempfile.TemporaryDirectory(prefix="mgmt_paper_007_test_") as temp_dir:
            root = Path(temp_dir)
            task_path = root / "task.json"
            milestone_path = root / "milestone.json"

            written = write_evidence_packet(packet, task_path=task_path, milestone_path=milestone_path)

            self.assertEqual(written["task_id"], "MGMT-PAPER-007")
            self.assertTrue(task_path.exists())
            self.assertTrue(milestone_path.exists())
            self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["milestone_id"], "MGMT-OODA-M2")
            self.assertEqual(
                json.loads(milestone_path.read_text(encoding="utf-8"))["ooda_packet"]["packet_id"],
                "ooda-paper-mgmt-loop-test",
            )


if __name__ == "__main__":
    unittest.main()
