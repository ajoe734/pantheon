#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_status


class ReviewApprovedWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Gemini", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Copilot", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "REG-002",
                    "title": "Promotion gate",
                    "phase": "Epic C",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Awaiting review",
                    "last_update": "2026-04-06T15:00:00Z",
                }
            ],
            "handoffs": [
                {
                    "task_id": "REG-002",
                    "from": "Codex",
                    "to": "Claude",
                    "message": "Please review the promotion gate.",
                    "status": "pending",
                    "created_at": "2026-04-06T15:00:00Z",
                }
            ],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    def test_approve_creates_owner_finalize_handoff(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude", "REVIEW_NOTES_ZH": "審查通過||交回 owner 收尾"}, clear=False):
            ai_status.command_approve(self.state, ["REG-002", "Review passed. Owner should finalize."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["review_notes_zh"], ["審查通過", "交回 owner 收尾"])

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertIn("finalize", pending[0]["message"].lower())

    def test_done_requires_owner_and_review_approved(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_done(self.state, ["REG-002", "Attempted direct completion"])

        self.state["tasks"][0]["status"] = "review_approved"

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_done(self.state, ["REG-002", "Reviewer cannot finalize"])

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value={}),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": "REG-002"}) as archive_task_snapshot,
        ):
            ai_status.command_done(self.state, ["REG-002", "Owner finalized approved task"])

        self.assertIsNone(ai_status.get_task(self.state, "REG-002"))
        self.assertEqual(self.state["handoffs"], [])
        archive_task = archive_task_snapshot.call_args.args[0]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["terminal_outcome"], "completed")

    def test_handoff_must_go_from_owner_to_reviewer(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Claude", "Wrong actor"])

        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Gemini", "Wrong reviewer"])

        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_handoff(self.state, ["REG-002", "Claude", "Ready for review"])

        self.assertEqual(self.state["tasks"][0]["status"], "review")

    def test_reviewer_reopen_creates_handoff_back_to_owner(self) -> None:
        self.state["tasks"][0]["status"] = "review"
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            ai_status.command_reopen(self.state, ["REG-002", "Please address the requested changes"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")

    def test_normalize_handoffs_adds_finalize_handoff_for_approved_task(self) -> None:
        self.state["tasks"][0]["status"] = "review_approved"
        self.state["handoffs"] = []

        ai_status.normalize_handoffs(self.state)

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertEqual(pending[0]["from"], "Claude")

    def test_supersede_closes_legacy_blocker_and_resolves_blocker_entries(self) -> None:
        self.state["tasks"][0]["status"] = "blocked"
        self.state["tasks"][0]["waiting_for"] = "Gemini"
        self.state["blockers"] = [
            {
                "task_id": "REG-002",
                "owner": "Codex",
                "waiting_for": "Gemini",
                "message": "Legacy lane replaced by newer execution slice",
                "status": "open",
                "created_at": "2026-04-06T15:05:00Z",
            }
        ]

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": "REG-002"}) as archive_task_snapshot,
        ):
            ai_status.command_supersede(self.state, ["REG-002", "Superseded by REG-010 after accepted consensus.", "REG-010"])

        self.assertIsNone(ai_status.get_task(self.state, "REG-002"))
        self.assertEqual(self.state["blockers"], [])
        archive_task = archive_task_snapshot.call_args.args[0]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["terminal_outcome"], "superseded")
        self.assertEqual(archive_task["superseded_by"], "REG-010")
        self.assertNotIn("waiting_for", archive_task)


class DeliveryMetadataValidationTests(unittest.TestCase):
    def test_collect_done_delivery_metadata_reports_all_missing_trailers_at_once(self) -> None:
        responses = iter(
            [
                "feat/bg-006",
                "abc123",
                "BG-006 finalize operator acceptance matrix",
                "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
                "Claude",
                "noreply@anthropic.com",
            ]
        )
        task = {
            "id": "BG-006",
            "owner": "Claude",
            "reviewer": "Codex",
            "status": "review_approved",
        }

        with mock.patch.object(ai_status, "run_git_command", side_effect=lambda *args, **kwargs: next(responses)):
            with self.assertRaises(SystemExit) as exc_info:
                ai_status.collect_done_delivery_metadata(task, "Claude")

        message = str(exc_info.exception)
        self.assertIn("`LLM-Agent: ...`", message)
        self.assertIn("`Task-ID: ...`", message)
        self.assertIn("`Reviewer: ...`", message)


class ArchiveWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "REG-100",
                    "title": "Archived completion candidate",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "done",
                    "terminal_outcome": "completed",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Completed",
                    "last_update": "2026-04-14T02:00:00Z",
                },
                {
                    "id": "REG-101",
                    "title": "Still active",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": ["REG-100"],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Waiting on archived dependency",
                    "last_update": "2026-04-14T02:00:00Z",
                },
            ],
            "handoffs": [
                {
                    "task_id": "REG-100",
                    "from": "Claude",
                    "to": "Codex",
                    "message": "Finalize complete",
                    "status": "done",
                    "created_at": "2026-04-14T01:50:00Z",
                }
            ],
            "blockers": [
                {
                    "task_id": "REG-100",
                    "owner": "Codex",
                    "waiting_for": "Claude",
                    "message": "Resolved blocker snapshot",
                    "status": "resolved",
                    "created_at": "2026-04-14T01:45:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

    def test_archive_migrate_moves_terminal_tasks_out_of_active_state(self) -> None:
        with (
            mock.patch.object(ai_status, "archive_task_snapshot", return_value={"task_id": "REG-100"}) as archive_task_snapshot,
            mock.patch.object(ai_status, "rebuild_archive_index") as rebuild_archive_index,
        ):
            ai_status.command_archive_migrate(self.state, [])

        self.assertEqual([task["id"] for task in self.state["tasks"]], ["REG-101"])
        self.assertEqual(self.state["handoffs"], [])
        self.assertEqual(self.state["blockers"], [])
        archive_task = archive_task_snapshot.call_args.args[0]
        self.assertEqual(archive_task["id"], "REG-100")
        rebuild_archive_index.assert_called_once()

    def test_reopen_rejects_archived_task(self) -> None:
        self.state["tasks"] = []
        with mock.patch.object(ai_status, "archived_task_snapshot", return_value={"task_id": "REG-100"}):
            with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
                with self.assertRaises(SystemExit) as exc_info:
                    ai_status.command_reopen(self.state, ["REG-100", "Resume work"])

        self.assertIn("archived", str(exc_info.exception))
        self.assertIn("follow-up", str(exc_info.exception))

    def test_show_reads_archive_snapshot(self) -> None:
        self.state["tasks"] = []
        snapshot = {
            "task_id": "REG-100",
            "archived_at": "2026-04-14T02:00:00Z",
            "terminal_outcome": "completed",
            "task": {
                "id": "REG-100",
                "status": "done",
                "title": "Archived completion candidate",
            },
        }
        with (
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=snapshot),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ai_status.command_show(self.state, ["REG-100"])

        rendered = stdout.getvalue()
        self.assertIn('"source": "archive"', rendered)
        self.assertIn('"task_id": "REG-100"', rendered)
        self.assertIn("ai-task-archive/tasks", rendered)


class SidecarTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Gemini", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Copilot", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Qwen", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    def test_assign_supports_sidecar_metadata_from_env(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_PHASE": "Phase 5: Persona and Application Surfaces",
            "TASK_TITLE": "Prepare APP-001 BFF handoff packet",
            "TASK_SUMMARY_ZH": "平行支援 APP-001，整理 BFF handoff materials。",
            "TASK_DEPENDS_ON": "PER-001",
            "TASK_ARTIFACTS": "support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md",
            "TASK_CLASS": "sidecar",
            "TASK_HELPER_PARENT": "APP-001",
            "TASK_HELPER_KIND": "bff_handoff_packet",
            "TASK_AUTO_GENERATED": "true",
            "TASK_MUTATES_CANONICAL": "false",
            "TASK_AUTO_CREATED_BY": "supervisor-underutilization",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_assign(self.state, ["APP-001-SIDECAR-BFF-HANDOFF", "Gemini", "Copilot"])

        task = ai_status.get_task(self.state, "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertIsNotNone(task)
        self.assertEqual(task["task_class"], "sidecar")
        self.assertTrue(task["auto_generated"])
        self.assertEqual(task["helper_parent"], "APP-001")
        self.assertEqual(task["helper_kind"], "bff_handoff_packet")
        self.assertFalse(task["mutates_canonical"])
        self.assertEqual(task["auto_created_by"], "supervisor-underutilization")
        self.assertEqual(task["depends_on"], ["PER-001"])

    def test_display_task_title_marks_sidecar_parent(self) -> None:
        title = ai_status.display_task_title(
            {
                "title": "Prepare APP-001 BFF handoff packet",
                "task_class": "sidecar",
                "auto_generated": True,
                "helper_parent": "APP-001",
            }
        )

        self.assertEqual(title, "[Sidecar] [Auto] [Parent APP-001] Prepare APP-001 BFF handoff packet")


class PortableStateRenderingTests(unittest.TestCase):
    def test_sync_canonical_document_metadata_migrates_current_work_to_derived_layer(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            }
        }

        ai_status.sync_canonical_document_metadata(state)

        self.assertEqual(
            state["canonical_document_layers"]["L0 Collaboration & State"],
            [
                "AI_COLLABORATION_GUIDE.md",
                "ai-status.json",
                "ai-activity-log.jsonl",
            ],
        )
        self.assertEqual(
            state["canonical_document_layers"]["L0.5 Derived Narrative"],
            ["current-work.md"],
        )

    def test_build_onboarding_prompt_follows_state_canonical_files(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            }
        }

        prompt = ai_status.build_onboarding_prompt(state)

        self.assertIn("Read AI_COLLABORATION_GUIDE.md and ai-status.json first.", prompt)
        self.assertIn("Use current-work.md as a human summary only", prompt)
        self.assertIn("Use ai-activity-log.jsonl only when you need targeted recent history.", prompt)
        self.assertNotIn("TARGET_ARCHITECTURE.md", prompt)

    def test_write_current_work_uses_generic_delivery_sections(self) -> None:
        state = {
            "updated_at": "2026-04-10T00:00:00Z",
            "objective": "Stand up a portable delivery system.",
            "sprint": "2026-04-10-bootstrap",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": ["review"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "DEMO-001",
                    "title": "First migrated task",
                    "summary_zh": "建立第一個遷移任務。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Implement foundation task",
                    "last_update": "2026-04-10T00:00:00Z",
                },
                {
                    "id": "OSS-001",
                    "title": "Verify external integration",
                    "summary_zh": "驗證外部整合。",
                    "phase": "Integration",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Queue external validation",
                    "last_update": "2026-04-10T00:00:00Z",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("### Primary Project Work", content)
        self.assertIn("### External / Upstream Integration Work", content)
        self.assertNotIn("### Pantheon Product Work", content)
        self.assertNotIn("Canonical map", content)
        self.assertIn("- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Runtime & Dashboard`", content)

    def test_write_current_work_formats_absolute_times_in_taiwan_time(self) -> None:
        state = {
            "updated_at": "2026-04-10T00:00:00Z",
            "objective": "Track the queue and resume work before 2026-04-10T01:30:00Z.",
            "sprint": "2026-04-10-bootstrap",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "Resume at 2026-04-10T01:45:00Z.", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "DEMO-002",
                    "title": "Timezone rendering",
                    "summary_zh": "確認人類可讀時間會轉成台灣時間。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "review",
                    "depends_on": [],
                    "next": "Waiting until 2026-04-10T02:15:00Z.",
                    "last_update": "2026-04-10T02:00:00Z",
                    "review_notes_zh": ["Reviewer checked the handoff at 2026-04-10T02:30:00Z."],
                    "review_file": "reviews/demo-002.md",
                },
            ],
            "handoffs": [
                {
                    "task_id": "DEMO-002",
                    "from": "Codex",
                    "to": "Claude",
                    "message": "Please review before 2026-04-10T02:20:00Z.",
                    "status": "pending",
                    "created_at": "2026-04-10T02:05:00Z",
                }
            ],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        logs = [
            {
                "ts": "2026-04-10T02:10:00Z",
                "agent": "Codex",
                "task_id": "DEMO-002",
                "message": "Paused until 2026-04-10T02:40:00Z.",
            }
        ]

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-taipei-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                ai_status.write_current_work(state, logs)

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("Absolute times below use 台灣時間 (UTC+8).", content)
        self.assertIn("Last updated: 2026-04-10 08:00:00", content)
        self.assertIn("Track the queue and resume work before 2026-04-10 09:30:00.", content)
        self.assertIn("Resume at 2026-04-10 09:45:00.", content)
        self.assertIn("| `DEMO-002` | Foundation | Timezone rendering |", content)
        self.assertIn("| review | - | 2026-04-10 10:00:00 | Waiting until 2026-04-10 10:15:00. |", content)
        self.assertIn("| `DEMO-002` | Codex | Claude | Please review before 2026-04-10 10:20:00. | pending | 2026-04-10 10:05:00 |", content)
        self.assertIn("Reviewer checked the handoff at 2026-04-10 10:30:00.", content)
        self.assertIn("- 2026-04-10 10:10:00 Codex: `DEMO-002` Paused until 2026-04-10 10:40:00.", content)

    def test_build_onboarding_prompt_mentions_active_planning(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "docs/02-architecture/consensus/phase1/README.md",
                    "docs/02-architecture/consensus/phase1/planning-session.json",
                ],
            }
        }

        with mock.patch.object(ai_status, "load_planning_state", return_value={"status": "active"}):
            prompt = ai_status.build_onboarding_prompt(state)

        self.assertIn("Discussion planning is active", prompt)
        self.assertIn("docs/02-architecture/consensus/phase1/README.md", prompt)
        self.assertIn("docs/02-architecture/consensus/phase1/planning-session.json", prompt)

    def test_write_current_work_includes_planning_snapshot(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Stand up a planning-aware control plane.",
            "sprint": "2026-04-11-planning-mode",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "docs/02-architecture/consensus/phase1/README.md",
                    "docs/02-architecture/consensus/phase1/planning-session.json",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "baton_owner": "Codex",
            "current_round": 1,
            "consensus_status": "draft",
            "human_gate_status": "pending",
            "switch_gate": {
                "ready_for_human": False,
                "ready_to_materialize": False,
            },
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-planning-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("## Discussion Planning", content)
        self.assertIn("phase1-2026-04-11", content)
        self.assertIn("`active`", content)

    def test_write_current_work_includes_lovable_coordination_snapshot(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Track cross-repo Lovable delivery.",
            "sprint": "2026-04-11-lovable-loop",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        orchestrator_state = {
            "coordination": {
                "last_scan_at": "2026-04-11T02:30:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/F-042-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback bundle ready"},
                                "updated_at": "2026-04-11T02:30:00Z",
                            }
                        },
                        "responses_by_type": {
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-11T02:00:00Z",
                            }
                        },
                    }
                },
            }
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-lovable-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_json_file", return_value=orchestrator_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("## Lovable Coordination", content)
        self.assertIn("Lovable-ready packets: `1`", content)
        self.assertIn("Frontend feedback returned: `1`", content)
        self.assertIn("| `F-042` | promotion-review | `frontend_feedback_received` | yes | yes | no | yes |", content)

    def test_build_dashboard_bundle_summarizes_truth_layers(self) -> None:
        state = {
            "updated_at": "2026-04-11T13:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "title": "Publish front-end handoff packet",
                    "summary_zh": "整理交接封包。",
                    "phase": "Planning Materialized",
                    "owner": "Copilot",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Assignment created",
                    "last_update": "2026-04-11T13:00:00Z",
                },
                {
                    "id": "APP-002-W2-READ-INCIDENT",
                    "title": "Incident response read surfaces",
                    "summary_zh": "補 incident read view。",
                    "phase": "Planning Materialized",
                    "owner": "Qwen",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "next": "Reviewer validating read model",
                    "last_update": "2026-04-11T13:00:00Z",
                },
            ],
        }
        planning_state = {
            "status": "accepted",
            "session_id": "phase2-2026-04-13-blueprint-gap",
            "planning_dir": "docs/02-architecture/consensus/phase2",
            "session_file": "docs/02-architecture/consensus/phase2/planning-session.json",
            "runtime_mode": "supervisor_managed_execution",
            "consensus_status": "accepted",
            "human_gate_status": "approved",
            "counts": {"readouts_resolved": 5, "open_items": 0},
            "artifacts": {
                "consensus_packet": {"path": "docs/02-architecture/consensus/phase2/consensus-packet.md"},
                "execution_materialization": {"path": "docs/02-architecture/consensus/phase2/execution-materialization.md"},
            },
            "materialization_contract": {
                "source_plane": "planning",
                "session_id": "phase2-2026-04-13-blueprint-gap",
                "phase": "phase2",
                "planning_dir": "docs/02-architecture/consensus/phase2",
                "session_file": "docs/02-architecture/consensus/phase2/planning-session.json",
                "consensus_packet": "docs/02-architecture/consensus/phase2/consensus-packet.md",
                "execution_materialization": "docs/02-architecture/consensus/phase2/execution-materialization.md",
            },
            "proposed_execution_tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "source_plane": "planning",
                    "source_ref": {"session_id": "phase2-2026-04-13-blueprint-gap"},
                },
                {
                    "id": "APP-002-W2-READ-INCIDENT",
                    "source_plane": "planning",
                    "source_ref": {"session_id": "phase2-2026-04-13-blueprint-gap"},
                },
                {"id": "APP-002-W5-SSE-LIVE"},
            ],
        }
        orchestrator_state = {
            "supervisor": {"pid": 294672, "last_heartbeat_at": "2026-04-11T13:08:22Z"},
            "queue": {"events": {}},
            "workers": {
                "copilot-run-1": {
                    "task_id": "APP-002-W1-FRONT-HANDOFF",
                    "queue_event_id": "evt-1",
                    "agent_id": "copilot",
                    "provider": "copilot",
                    "status": "running",
                    "last_event_at": "2026-04-11T13:08:21Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {
            "APP-002-W1-FRONT-HANDOFF": state["tasks"][0],
            "APP-002-W2-READ-INCIDENT": state["tasks"][1],
        }
        resolver.dependency_status.side_effect = lambda task_id: "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: False
        resolver.get.side_effect = lambda task_id: {
            "APP-002-W1-FRONT-HANDOFF": state["tasks"][0],
            "APP-002-W2-READ-INCIDENT": state["tasks"][1],
        }.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["focus_mode"], "execution")
        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["in_review"], 1)
        self.assertEqual(bundle["planning_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["source_plane"], "planning")
        self.assertEqual(bundle["bridge_summary"]["session_id"], "phase2-2026-04-13-blueprint-gap")
        self.assertEqual(bundle["bridge_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["pending_materialization_count"], 1)
        self.assertEqual(bundle["bridge_summary"]["consensus_packet"], "docs/02-architecture/consensus/phase2/consensus-packet.md")
        self.assertEqual(len(bundle["truth_mismatches"]), 2)
        self.assertEqual({item["type"] for item in bundle["truth_mismatches"]}, {"running_worker_on_todo", "active_task_without_worker"})
        mismatch_hints = {item["type"]: item["resolution_hint"] for item in bundle["truth_mismatches"]}
        self.assertIn("先把 task 狀態推成 in_progress", mismatch_hints["running_worker_on_todo"])
        self.assertIn("重新 dispatch expected actor", mismatch_hints["active_task_without_worker"])
        self.assertEqual(bundle["worker_task_links"][0]["task_id"], "APP-002-W1-FRONT-HANDOFF")
        self.assertEqual(bundle["worker_task_links"][0]["task_title"], "Publish front-end handoff packet")
        self.assertEqual(bundle["worker_task_links"][0]["task_summary"], "整理交接封包。")
        self.assertEqual(bundle["worker_task_links"][0]["queue_status"], None)
        self.assertEqual(bundle["worker_task_links"][0]["mismatch_count"], 1)
        self.assertIn("running_worker_on_todo", bundle["worker_task_links"][0]["mismatch_flags"])
        self.assertTrue(bundle["worker_task_links"][0]["resolution_hints"])

    def test_write_dashboard_bundle_persists_json_artifact(self) -> None:
        state = {
            "updated_at": "2026-04-11T13:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "title": "Publish front-end handoff packet",
                    "summary_zh": "整理交接封包。",
                    "phase": "Planning Materialized",
                    "owner": "Copilot",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Working",
                    "last_update": "2026-04-11T13:00:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {"supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-11T13:08:22Z"}, "queue": {"events": {}}, "workers": {}}
        approval_state = {"pending": [], "history": []}

        with tempfile.TemporaryDirectory(prefix="ai-status-dashboard-bundle-") as temp_dir:
            output_path = Path(temp_dir) / "dashboard-bundle.json"
            with mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    with mock.patch.object(ai_status, "load_json_file", side_effect=[orchestrator_state, approval_state]):
                        ai_status.write_dashboard_bundle(state)

            bundle = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(bundle["runtime_summary"]["supervisor_pid"], 1)
        self.assertEqual(bundle["execution_summary"]["in_progress"], 1)
        self.assertEqual(bundle["focus_mode"], "execution")
        self.assertIn("worker_task_links", bundle)
        self.assertIn("truth_mismatches", bundle)

    def test_build_dashboard_bundle_reads_terminal_counts_from_archive_summary(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "REG-101",
                    "title": "Still active",
                    "summary_zh": "等待已封存依賴。",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": ["REG-100"],
                    "next": "Ready to start",
                    "last_update": "2026-04-14T02:00:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "recent_terminal_tasks": [
                {
                    "task_id": "REG-100",
                    "terminal_outcome": "completed",
                    "archived_at": "2026-04-14T01:59:00Z",
                }
            ],
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {"REG-101": state["tasks"][0]}
        resolver.dependency_status.side_effect = lambda task_id: "done" if task_id == "REG-100" else "todo"
        resolver.dependency_satisfied.side_effect = lambda task_id: task_id == "REG-100"
        resolver.get.side_effect = lambda task_id: state["tasks"][0] if task_id == "REG-101" else {"id": "REG-100", "status": "done"}
        resolver.source.side_effect = lambda task_id: "active" if task_id == "REG-101" else "archive"

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={
                    "updated_at": "2026-04-14T02:00:00Z",
                    "counts": {"total": 3, "completed": 2, "superseded": 1},
                    "recent_terminal_ids": ["REG-100"],
                },
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["done"], 2)
        self.assertEqual(bundle["execution_summary"]["superseded"], 1)
        self.assertEqual(bundle["archive_summary"]["recent_terminal_ids"], ["REG-100"])
        self.assertEqual(bundle["archive_summary"]["recent_terminal_tasks"][0]["task_id"], "REG-100")

    def test_build_dashboard_bundle_includes_coordination_summary(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/F-042-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/F-042-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with mock.patch.object(
            ai_status,
            "load_archive_index",
            return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        summary = bundle["coordination_summary"]
        self.assertEqual(summary["last_scan_at"], "2026-04-14T02:04:00Z")
        self.assertEqual(summary["counts"]["tracked_features"], 1)
        self.assertEqual(summary["counts"]["lovable_ready"], 1)
        self.assertEqual(summary["counts"]["ui_done_received"], 1)
        self.assertEqual(summary["counts"]["frontend_feedback_received"], 1)
        self.assertEqual(summary["counts"]["waiting_for_lovable"], 0)
        self.assertEqual(summary["features"][0]["stage"], "frontend_feedback_received")
        self.assertTrue(summary["features"][0]["mirrored_to_target_repo"])
        self.assertEqual(summary["features"][0]["paths"]["frontend_feedback"], "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml")

    def test_build_dashboard_bundle_treats_dead_suspended_approval_as_approval_wait_not_live_worker(self) -> None:
        state = {
            "updated_at": "2026-04-14T01:42:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "BG-005",
                    "title": "Define golden replay scenario and acceptance runbook",
                    "summary_zh": "定義 golden replay scenario 與 acceptance runbook。",
                    "phase": "Blueprint Gap P0",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "status": "review_approved",
                    "depends_on": ["BG-000"],
                    "next": "Supervisor resumed BG-005 for finalize after successful dispatch.",
                    "last_update": "2026-04-14T00:42:04Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {
                "pid": 490443,
                "last_heartbeat_at": "2026-04-14T01:42:22Z",
                "mode_status": "active",
                "mode_occupancy": {
                    "planning": {"running": 0, "pending": 0, "queued": 0},
                    "execution": {"running": 0, "pending": 1, "queued": 0},
                    "coordination": {"running": 0, "pending": 0, "queued": 0},
                },
            },
            "queue": {
                "events": {
                    "evt-1": {
                        "status": "manual_pending",
                        "run_id": "claude-run-1",
                        "processed_at": "2026-04-14T00:42:04Z",
                    }
                }
            },
            "workers": {
                "claude-run-1": {
                    "task_id": "BG-005",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "suspended_approval",
                    "pid": 477808,
                    "last_event_at": "2026-04-14T00:42:46Z",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "BG-005",
                    "worker_run_id": "claude-run-1",
                    "provider": "claude",
                    "created_at": "2026-04-14T00:42:46Z",
                }
            ],
            "history": [],
        }

        with mock.patch.object(ai_status, "pid_is_alive", return_value=False):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 0)
        self.assertEqual(bundle["runtime_summary"]["pending_workers"], 0)
        self.assertEqual(bundle["worker_task_links"], [])
        self.assertFalse(any(item["type"] == "queue_started_without_worker" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_skips_planning_approval_without_task_board_row(self) -> None:
        state = {
            "updated_at": "2026-04-14T05:35:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "active", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T05:35:00Z"},
            "queue": {"events": {}},
            "workers": {
                "claude-run-1": {
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "suspended_approval",
                    "last_event_at": "2026-04-14T05:35:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_readout",
                        "metadata": {"planning": {"mode": "discussion_planning"}},
                    },
                }
            },
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "worker_run_id": "claude-run-1",
                    "provider": "claude",
                    "created_at": "2026-04-14T05:35:00Z",
                }
            ],
            "history": [],
        }

        bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertFalse(any(item["type"] == "approval_missing_task" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_skips_planning_worker_without_task_board_row(self) -> None:
        state = {
            "updated_at": "2026-04-14T05:37:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "active", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T05:37:00Z"},
            "queue": {"events": {}},
            "workers": {
                "claude-run-1": {
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "running",
                    "pid": None,
                    "last_event_at": "2026-04-14T05:37:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_round",
                        "metadata": {"planning": {"mode": "discussion_planning"}},
                    },
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertFalse(any(item["type"] == "worker_task_missing" for item in bundle["truth_mismatches"]))


if __name__ == "__main__":
    unittest.main()
