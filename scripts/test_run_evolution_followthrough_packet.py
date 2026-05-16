from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_evolution_followthrough_packet as packet


class EvolutionFollowthroughPacketTest(unittest.TestCase):
    def test_packet_traces_freeze_stage_and_runtime_rollback(self) -> None:
        report = packet.build_packet(generated_at="2026-05-15T18:00:00Z")

        self.assertEqual(report["task_id"], packet.TASK_ID)
        self.assertTrue(report["summary"]["packet_passed"])
        self.assertTrue(report["summary"]["freeze_stage_followthrough_traced"])
        self.assertTrue(report["summary"]["runtime_rollback_followthrough_traced"])
        self.assertFalse(report["summary"]["live_execution_performed"])
        self.assertFalse(report["summary"]["broker_session_opened"])
        self.assertFalse(report["summary"]["capital_binding_mutation_performed"])

        scenarios = {row["scenario_id"]: row for row in report["scenarios"]}
        freeze = scenarios["freeze_stage_followthrough"]
        self.assertTrue(freeze["assertions"]["decision_executed"])
        self.assertTrue(freeze["assertions"]["boundary_is_live_active_runtime"])
        self.assertTrue(freeze["assertions"]["deployment_freeze_stage_command_emitted"])
        self.assertTrue(freeze["assertions"]["rollback_command_not_emitted"])
        self.assertEqual(freeze["evidence"]["followthrough_action_types"], ["freeze_stage"])

        rollback = scenarios["runtime_rollback_followthrough"]
        self.assertTrue(rollback["assertions"]["decision_executed"])
        self.assertTrue(rollback["assertions"]["rollback_command_emitted"])
        self.assertTrue(rollback["assertions"]["rollback_action_type_pause_then_replace"])
        self.assertTrue(rollback["assertions"]["rollback_target_binding_id_present"])
        self.assertEqual(rollback["evidence"]["rollback_action_type"], "pause_then_replace")
        self.assertEqual(
            rollback["evidence"]["rollback_target_binding_id"],
            packet.RUNTIME_BINDING_ID,
        )

    def test_runtime_manager_replay_uses_in_memory_store(self) -> None:
        report = packet.build_packet(generated_at="2026-05-15T18:00:00Z")
        replay = report["runtime_manager_replay"]

        self.assertEqual(replay["mode"], "in_memory_runtime_manager_replay")
        self.assertTrue(replay["assertions"]["freeze_binding_paused"])
        self.assertTrue(replay["assertions"]["rollback_old_binding_retired"])
        self.assertTrue(replay["assertions"]["rollback_new_binding_active"])
        self.assertTrue(replay["assertions"]["rollback_parent_linked"])
        self.assertEqual(replay["freeze_result"]["binding"]["status"], "paused")
        self.assertEqual(replay["rollback_result"]["old_binding"]["status"], "retired")
        self.assertEqual(replay["rollback_result"]["new_binding"]["status"], "active")

    def test_cli_writes_json_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "packet.json"
            exit_code = packet.main(
                [
                    "--output-dir",
                    str(Path(tmp) / "work"),
                    "--json-out",
                    str(out),
                    "--generated-at",
                    "2026-05-15T18:00:00Z",
                ]
            )

            self.assertEqual(exit_code, 0)
            loaded = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(loaded["task_id"], packet.TASK_ID)
            self.assertTrue(loaded["summary"]["packet_passed"])


if __name__ == "__main__":
    unittest.main()
