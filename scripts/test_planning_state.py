#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import planning_state


class PlanningStateTests(unittest.TestCase):
    def patch_paths(self, root: Path):
        planning_dir = root / "docs" / "02-architecture" / "consensus" / "phase1"
        return mock.patch.multiple(
            planning_state,
            ROOT=root,
            PLANNING_DIR=planning_dir,
            SESSION_FILE=planning_dir / "planning-session.json",
            README_FILE=planning_dir / "README.md",
            READOUT_TEMPLATE_FILE=planning_dir / "LLM_READOUT_TEMPLATE.md",
            CHECKLIST_FILE=planning_dir / "pantheon-backend-completion-checklist.md",
            STARTER_DRAFT_FILE=planning_dir / "starter-draft.md",
            BATON_LOG_FILE=planning_dir / "baton-log.md",
            SUPERVISOR_QUEUE_FILE=planning_dir / "supervisor-queue.md",
            CONSENSUS_PACKET_FILE=planning_dir / "consensus-packet.md",
            DERIVED_STATE_FILE=root / ".orchestrator" / "planning-state.json",
        )

    def test_sync_creates_templates_and_derived_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-sync-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session()
                planning_state.sync_all(session)

                self.assertTrue((root / "docs" / "02-architecture" / "consensus" / "phase1" / "README.md").exists())
                self.assertTrue((root / "docs" / "02-architecture" / "consensus" / "phase1" / "pantheon-backend-completion-checklist.md").exists())
                self.assertTrue((root / ".orchestrator" / "planning-state.json").exists())

                derived = json.loads((root / ".orchestrator" / "planning-state.json").read_text(encoding="utf-8"))
                self.assertEqual(derived["status"], "inactive")
                self.assertEqual(derived["planning_mode"], "discussion_planning")
                self.assertFalse(derived["switch_gate"]["ready_to_materialize"])

    def test_command_flow_advances_switch_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-flow-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session()
                planning_state.command_start(session, ["phase1-2026-04-11", "Kick off planning"])
                for agent in planning_state.AGENT_ORDER:
                    planning_state.command_readout(session, [agent, "submitted", f"{agent} readout ready"])
                planning_state.command_round(session, ["1", "completed", "Round 1 closed"])
                planning_state.command_consensus(session, ["ready_for_human", "Consensus packet drafted"])
                planning_state.command_human_gate(session, ["approved", "Human accepted the packet"])

                derived = planning_state.build_derived_state(session)

        self.assertTrue(derived["switch_gate"]["all_readouts_submitted"])
        self.assertTrue(derived["switch_gate"]["cross_review_round_present"])
        self.assertTrue(derived["switch_gate"]["consensus_packet_drafted"])
        self.assertTrue(derived["switch_gate"]["human_approved"])
        self.assertTrue(derived["switch_gate"]["ready_to_materialize"])

    def test_sync_auto_detects_markdown_activity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-autodetect-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                planning_state.ensure_artifact_files()
                planning_state.STARTER_DRAFT_FILE.write_text(
                    "# Starter Draft\n\n## Shared Draft\n\n- Objective: Real planning has started.\n",
                    encoding="utf-8",
                )
                planning_state.readout_file_for("Codex").write_text(
                    "# Codex Readout\n\n- Architecture summary: drafted.\n",
                    encoding="utf-8",
                )

                session = planning_state.load_session()
                derived = planning_state.build_derived_state(session)

        self.assertEqual(derived["status"], "active")
        self.assertEqual(derived["consensus_status"], "draft")
        self.assertEqual(derived["artifacts"]["starter_draft"]["status"], "active")
        self.assertEqual(derived["readouts"]["Codex"]["status"], "submitted")
        self.assertGreaterEqual(derived["counts"]["readouts_submitted"], 1)

    def test_sync_persists_accepted_consensus_artifact_statuses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-accepted-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session()
                planning_state.command_start(session, ["phase1-2026-04-11", "Kick off planning"])
                planning_state.command_consensus(session, ["ready_for_human", "Consensus packet drafted"])
                planning_state.command_human_gate(session, ["approved", "Human accepted the packet"])
                planning_state.command_round(session, ["1", "completed", "Round 1 closed"])
                planning_state.sync_all(session)

                saved = json.loads((root / "docs" / "02-architecture" / "consensus" / "phase1" / "planning-session.json").read_text(encoding="utf-8"))
                derived = json.loads((root / ".orchestrator" / "planning-state.json").read_text(encoding="utf-8"))

        self.assertEqual(saved["status"], "accepted")
        self.assertEqual(saved["consensus_status"], "accepted")
        self.assertEqual(saved["human_gate_status"], "approved")
        self.assertEqual(saved["artifacts"]["consensus_packet"]["status"], "accepted")
        self.assertEqual(saved["artifacts"]["review_rounds"]["status"], "completed")
        self.assertTrue(derived["switch_gate"]["ready_to_materialize"])

    def test_switch_gate_treats_waived_readouts_and_tracking_items_as_terminal(self) -> None:
        session = planning_state.default_session()
        for agent in planning_state.AGENT_ORDER:
            session["readouts"][agent]["status"] = "waived"
        session["cross_review_rounds"] = [
            {
                "round": 1,
                "status": "completed",
                "reviewers": planning_state.REVIEW_SEQUENCE,
                "updated_at": "2026-04-11T12:00:00Z",
                "summary": "Closed with synthesized packet",
            }
        ]
        session["unresolved_items"] = [
            {
                "id": "DISC-001",
                "severity": "low",
                "status": "tracking",
                "summary": "Provider instability tracked separately.",
            }
        ]
        session["consensus_status"] = "accepted"
        session["human_gate_status"] = "approved"

        derived = planning_state.build_derived_state(session)

        self.assertTrue(derived["switch_gate"]["all_readouts_submitted"])
        self.assertTrue(derived["switch_gate"]["divergence_resolved_or_escalated"])
        self.assertTrue(derived["switch_gate"]["ready_to_materialize"])
        self.assertEqual(derived["counts"]["readouts_resolved"], len(planning_state.AGENT_ORDER))
        self.assertEqual(derived["counts"]["open_items"], 0)


if __name__ == "__main__":
    unittest.main()
