#!/usr/bin/env python3
from __future__ import annotations

import json
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
        ):
            ai_status.command_done(self.state, ["REG-002", "Owner finalized approved task"])

        self.assertEqual(self.state["tasks"][0]["status"], "done")
        self.assertEqual(self.state["tasks"][0]["terminal_outcome"], "completed")

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

        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_supersede(self.state, ["REG-002", "Superseded by REG-010 after accepted consensus.", "REG-010"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["terminal_outcome"], "superseded")
        self.assertEqual(task["superseded_by"], "REG-010")
        self.assertNotIn("waiting_for", task)
        self.assertEqual(self.state["blockers"][0]["status"], "resolved")


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


if __name__ == "__main__":
    unittest.main()
