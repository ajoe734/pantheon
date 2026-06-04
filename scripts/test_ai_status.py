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


class StatusRootRoutingTests(unittest.TestCase):
    def test_load_local_coordination_payload_tolerates_missing_yaml(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-no-yaml-") as temp_dir:
            root = Path(temp_dir)
            (root / "payload.yaml").write_text("status: done\n", encoding="utf-8")
            (root / "payload.json").write_text('{"status": "done"}\n', encoding="utf-8")

            with (
                mock.patch.object(ai_status, "ROOT", root),
                mock.patch.object(ai_status, "yaml", None),
                mock.patch.object(ai_status, "YAML_ERROR_TYPES", ()),
            ):
                self.assertIsNone(ai_status.load_local_coordination_payload("payload.yaml"))
                self.assertEqual(
                    {"status": "done"},
                    ai_status.load_local_coordination_payload("payload.json"),
                )

    def test_load_config_routes_runtime_paths_to_status_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-routing-") as temp_dir:
            root = Path(temp_dir)
            code_root = root / "code"
            status_root = root / "status"
            config_file = code_root / ".orchestrator" / "config.json"
            config_file.parent.mkdir(parents=True)
            config_file.write_text(
                json.dumps(
                    {
                        "paths": {
                            "status_file": "ai-status.json",
                            "activity_log": "ai-activity-log.jsonl",
                            "state_file": ".orchestrator/state.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(ai_status, "CONFIG_FILE", config_file),
                mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                mock.patch.object(ai_status, "STATUS_FILE", status_root / "ai-status.json"),
                mock.patch.object(ai_status, "LOG_FILE", status_root / "ai-activity-log.jsonl"),
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", status_root / "current-work.md"),
                mock.patch.object(ai_status, "DOCS_SITE_DIR", status_root / "docs-site"),
                mock.patch.object(ai_status, "ORCHESTRATOR_STATE_FILE", status_root / ".orchestrator" / "state.json"),
                mock.patch.object(ai_status, "APPROVAL_QUEUE_FILE", status_root / ".orchestrator" / "approval-queue.json"),
            ):
                config = ai_status.load_config()

        self.assertEqual(config["paths"]["status_file"], str(status_root / "ai-status.json"))
        self.assertEqual(config["paths"]["activity_log"], str(status_root / "ai-activity-log.jsonl"))
        self.assertEqual(config["paths"]["state_file"], str(status_root / ".orchestrator" / "state.json"))
        self.assertEqual(config["paths"]["event_queue"], str(status_root / ".orchestrator" / "event-queue.jsonl"))


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

    def test_collect_done_delivery_metadata_uses_execute_plans_artifact_repo(self) -> None:
        responses = iter(
            [
                "bff-luv-fe-006-dev-deploy",
                "abc123",
                "FE-INT-GATE-DUMMY finalize execute-plans artifact",
                "LLM-Agent: Codex2\nTask-ID: FE-INT-GATE-DUMMY\nReviewer: Claude\n",
                "Codex2",
                "codex2@example.com",
                "",
                "",
            ]
        )
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("cwd") if isinstance(kwargs.get("cwd"), Path) else None))
            return next(responses)

        task = {
            "id": "FE-INT-GATE-DUMMY",
            "owner": "Codex2",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": ["execute-plans/e2e/dummy.spec.ts"],
        }
        with (
            mock.patch.dict(os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        execute_plans_root = Path(delivery["repository_path"])
        self.assertEqual(delivery["repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_path"], str(execute_plans_root))
        self.assertEqual(delivery["repository_slug"], "ajoe734/execute-plans")
        self.assertEqual(delivery["branch"], "bff-luv-fe-006-dev-deploy")
        self.assertTrue(calls)
        self.assertTrue(all(cwd == execute_plans_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_falls_back_to_pantheon_for_missing_mixed_repo(self) -> None:
        responses = iter(
            [
                "task/BFF-PM12-002",
                "abc123",
                "BFF-PM12-002: refresh closeout gate",
                "LLM-Agent: Codex2\nTask-ID: BFF-PM12-002\nReviewer: Claude2\n",
                "Codex2",
                "codex2@example.com",
                "",
                "",
            ]
        )
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("cwd") if isinstance(kwargs.get("cwd"), Path) else None))
            return next(responses)

        task = {
            "id": "BFF-PM12-002",
            "owner": "Codex2",
            "reviewer": "Claude2",
            "status": "review_approved",
            "artifacts": [
                "execute-plans/src/lib/bff-v1/management.ts",
                "services/control-plane/bff/main.py",
            ],
        }
        pantheon_root = Path("/tmp/pantheon-task-worktree")
        missing_execute_plans_root = Path("/tmp/pantheon-worker-worktrees/pantheon/execute-plans")

        def fake_repository_local_path(_config: dict[str, object], repo_id: str | None) -> Path | None:
            if repo_id == "execute_plans":
                return missing_execute_plans_root
            if repo_id == "pantheon":
                return pantheon_root
            return None

        with (
            mock.patch.dict(os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "repository_local_path", side_effect=fake_repository_local_path),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        self.assertEqual(delivery["repository_id"], "pantheon")
        self.assertEqual(delivery["repository_path"], str(pantheon_root))
        self.assertEqual(delivery["branch"], "task/BFF-PM12-002")
        self.assertEqual(delivery["repository_fallback"]["from_repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_fallback"]["missing_repository_path"], str(missing_execute_plans_root))
        self.assertTrue(calls)
        self.assertTrue(all(cwd == pantheon_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_blocks_unmerged_task_pr(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "task/REG-002"
            if args == ["rev-parse", "HEAD"]:
                return "abc123"
            if args == ["show", "-s", "--format=%s", "HEAD"]:
                return "REG-002 finalize"
            if args == ["show", "-s", "--format=%b", "HEAD"]:
                return "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
            if args == ["show", "-s", "--format=%an", "HEAD"]:
                return "Codex"
            if args == ["show", "-s", "--format=%ae", "HEAD"]:
                return "codex@example.com"
            if args == ["status", "--porcelain"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return "origin/task/REG-002"
            if args == ["rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "devsha"
            raise AssertionError(f"unexpected git command: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", return_value=False),
            mock.patch.object(
                ai_status,
                "pull_request_status_for_branch",
                return_value={
                    "number": 152,
                    "state": "OPEN",
                    "mergeStateStatus": "BEHIND",
                    "autoMergeRequest": {"mergeMethod": "MERGE"},
                    "url": "https://github.com/ajoe734/pantheon/pull/152",
                },
            ),
        ):
            with self.assertRaises(SystemExit) as exc_info:
                ai_status.collect_done_delivery_metadata(task, "Codex")

        message = str(exc_info.exception)
        self.assertIn("not merged into `origin/dev`", message)
        self.assertIn("PR #152", message)
        self.assertIn("mergeState=BEHIND", message)
        self.assertIn("review_approved", message)

    def test_collect_done_delivery_metadata_allows_head_merged_to_dev(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "task/REG-002"
            if args == ["rev-parse", "HEAD"]:
                return "abc123"
            if args == ["show", "-s", "--format=%s", "HEAD"]:
                return "REG-002 finalize"
            if args == ["show", "-s", "--format=%b", "HEAD"]:
                return "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
            if args == ["show", "-s", "--format=%an", "HEAD"]:
                return "Codex"
            if args == ["show", "-s", "--format=%ae", "HEAD"]:
                return "codex@example.com"
            if args == ["status", "--porcelain"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return "origin/task/REG-002"
            if args == ["rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "devsha"
            raise AssertionError(f"unexpected git command: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", return_value=True),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertEqual(delivery["merge_target_branch"], "dev")
        self.assertEqual(delivery["merge_target_ref"], "origin/dev")
        self.assertEqual(delivery["merge_target_sha"], "devsha")
        self.assertTrue(delivery["head_merged_to_target"])


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

    def test_prune_archived_active_tasks_removes_duplicate_active_rows(self) -> None:
        def fake_archived_snapshot(task_id: str):
            return {"task_id": task_id} if task_id == "REG-100" else None

        with mock.patch.object(ai_status, "archived_task_snapshot", side_effect=fake_archived_snapshot):
            pruned = ai_status.prune_archived_active_tasks(self.state)

        self.assertEqual(pruned, ["REG-100"])
        self.assertEqual([task["id"] for task in self.state["tasks"]], ["REG-101"])
        self.assertEqual(self.state["handoffs"], [])
        self.assertEqual(self.state["blockers"], [])

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


class HumanOpsAgentTests(unittest.TestCase):
    def test_human_gate_can_belong_to_human_ops_without_blocking_worker(self) -> None:
        state = {
            "agents": [
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "PROD-WRITES-001-V2",
                    "title": "Enable production real writes",
                    "phase": "Phase 8 / EPIC-LIVE-GATE",
                    "owner": "human/ops",
                    "reviewer": "Codex",
                    "status": "blocked",
                    "waiting_for": "ops",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": ["Human risk-owner + operator signoff"],
                    "next": "Awaiting risk-owner and operator signoff",
                    "last_update": "2026-06-01T00:00:00Z",
                    "task_class": "human_gate",
                    "non_dispatchable": True,
                    "allowed_workers": [],
                }
            ],
            "handoffs": [],
            "blockers": [
                {
                    "task_id": "PROD-WRITES-001-V2",
                    "owner": "human/ops",
                    "waiting_for": "ops",
                    "message": "Awaiting risk-owner and operator signoff",
                    "status": "open",
                    "created_at": "2026-06-01T00:00:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

        ai_status.validate_state(state)
        ai_status.recompute_agents(state)
        ai_status.recompute_workload(state)

        task = ai_status.get_task(state, "PROD-WRITES-001-V2")
        self.assertEqual(task["owner"], "Human/Ops")
        self.assertEqual(task["waiting_for"], "Human/Ops")
        self.assertEqual(state["blockers"][0]["owner"], "Human/Ops")
        self.assertEqual(state["blockers"][0]["waiting_for"], "Human/Ops")

        human_ops = ai_status.get_agent(state, "Human/Ops")
        self.assertEqual(human_ops["status"], "blocked")
        self.assertEqual(human_ops["current_task_ids"], ["PROD-WRITES-001-V2"])
        self.assertEqual(ai_status.get_agent(state, "Claude")["status"], "idle")
        self.assertEqual(state["workload"]["Human/Ops"], 0)
        self.assertEqual(state["workload_summary"]["Human/Ops"]["blocked"], 1)


class RuntimeWorkerLivenessTests(unittest.TestCase):
    def test_pid_is_alive_rejects_zombie_processes(self) -> None:
        with mock.patch.object(ai_status, "proc_pid_state", return_value="Z"):
            self.assertFalse(ai_status.pid_is_alive(1234))

    def test_normalize_runtime_workers_marks_zombie_running_worker_stale(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Review stale runtime",
                    "summary_zh": "確認 zombie worker 不會被 dashboard 當成 live。",
                    "owner": "Codex",
                    "reviewer": "Gemini2",
                    "status": "review_approved",
                    "depends_on": [],
                    "next": "Owner finalize",
                    "last_update": "2026-05-17T11:00:00Z",
                }
            ]
        }
        orchestrator_state = {
            "workers": {
                "gemini2-run": {
                    "task_id": "TASK-001",
                    "provider": "gemini2",
                    "logical_agent_id": "gemini2",
                    "status": "running",
                    "pid": 1234,
                    "last_event_at": "2026-05-17T11:03:15Z",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                }
            }
        }

        with mock.patch.object(ai_status, "proc_pid_state", return_value="Z"):
            workers = ai_status.normalize_runtime_workers(state, orchestrator_state)

        self.assertEqual(workers[0]["bucket"], "stale")
        self.assertFalse(workers[0]["is_live_runtime"])
        self.assertFalse(workers[0]["pid_alive"])
        self.assertEqual(workers[0]["pid_state"], "Z")


class PortableStateRenderingTests(unittest.TestCase):
    def test_default_canonical_document_layers_exclude_review_and_session_records(self) -> None:
        layers = ai_status.default_canonical_document_layers()
        flattened = ai_status.flatten_canonical_document_layers(layers)

        self.assertIn("DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md", flattened)
        self.assertIn("WORKBENCH_DELIVERY_BACKLOG.md", flattened)
        self.assertIn("DELIVERY_CLOSURE_AND_LOOP_STATES.md", flattened)
        self.assertIn("EXECUTION_PROOF_AND_MATURITY_LEVELS.md", flattened)
        self.assertNotIn("docs/reviews/2026-04-17-next-wave-implementation-plan.md", flattened)
        self.assertNotIn(
            "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md",
            flattened,
        )

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

    def test_sync_canonical_document_metadata_backfills_new_default_l2_documents(self) -> None:
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
                    "CANONICAL_DOCUMENT_MAP.md",
                    "DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md",
                    "ROADMAP.md",
                    "DEVELOPMENT_WORKBREAKDOWN.md",
                    "OSS_INTEGRATION_CHECKLIST.md",
                ],
            }
        }

        ai_status.sync_canonical_document_metadata(state)

        self.assertIn(
            "WORKBENCH_DELIVERY_BACKLOG.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )
        self.assertIn(
            "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )
        self.assertIn(
            "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
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

        self.assertIn("Read AI_COLLABORATION_GUIDE.md, ai-status.json", prompt)
        self.assertIn("Use current-work.md as a human summary only", prompt)
        self.assertIn("Use ai-activity-log.jsonl only when you need targeted recent history.", prompt)
        self.assertIn("TARGET_ARCHITECTURE.md", prompt)

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
                {
                    "id": "P2-OSS-ACTIVATE-001",
                    "title": "Research OSS production activation after fail-closed gates",
                    "summary_zh": "確認外部資料串接 activation gate。",
                    "phase": "P2 Wave 7",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "status": "todo",
                    "depends_on": ["P0-CI-BOUNDED-001"],
                    "artifacts": ["services/source_ingestion", "services/search"],
                    "next": "Assignment created",
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
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": "2026-04-10T01:00:00Z",
                        "counts": {"total": 1, "completed": 1, "superseded": 0},
                        "recent_terminal_ids": ["DONE-001"],
                    },
                ),
                mock.patch.object(
                    ai_status,
                    "recent_terminal_summaries",
                    return_value=[
                        {
                            "task_id": "DONE-001",
                            "title": "Executed task",
                            "phase": "Archive",
                            "owner": "Codex",
                            "terminal_outcome": "completed",
                            "archived_at": "2026-04-10T01:00:00Z",
                            "snapshot_path": "ai-task-archive/tasks/DONE-001.json",
                        }
                    ],
                ),
            ):
                ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("### Primary Project Work", content)
        self.assertIn("### External / Upstream Integration Work", content)
        self.assertIn("`P2-OSS-ACTIVATE-001`", content)
        self.assertIn("## Recently Executed Tasks", content)
        self.assertIn("`DONE-001`", content)
        self.assertIn("`ai-task-archive/tasks/DONE-001.json`", content)
        self.assertNotIn("### Pantheon Product Work", content)
        self.assertIn("Canonical map", content)
        self.assertIn("Workbench backlog", content)
        self.assertIn("Loop closure", content)
        self.assertIn("Execution proof", content)
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
            },
            {
                "ts": "2026-04-10T02:11:00Z",
                "agent": "Orchestrator",
                "type": "worker_started",
                "task_id": "DEMO-002",
            },
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
        self.assertIn("- 2026-04-10 10:11:00 Orchestrator: `DEMO-002` worker_started", content)

    def test_write_current_work_tolerates_structured_log_entries_without_message(self) -> None:
        state = {
            "updated_at": "2026-05-17T16:24:00Z",
            "objective": "Keep generated status views robust.",
            "sprint": "2026-05-17-status-sync",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
            },
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        logs = [
            {
                "ts": "2026-05-17T16:24:21Z",
                "agent": "Codex2",
                "type": "worker_commit",
                "task_id": "OODA-E2E-002",
                "commit": "abcdef1234567890",
                "scope": ["tests/e2e/test_demo.py"],
            }
        ]

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-structured-log-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": None,
                        "counts": {"total": 0, "completed": 0, "superseded": 0},
                        "recent_terminal_ids": [],
                    },
                ),
                mock.patch.object(ai_status, "recent_terminal_summaries", return_value=[]),
            ):
                ai_status.write_current_work(state, logs)

            content = output_path.read_text(encoding="utf-8")

        self.assertIn(
            "- 2026-05-18 00:24:21 Codex2: `OODA-E2E-002` "
            "worker_commit: commit abcdef123456; scope `tests/e2e/test_demo.py`",
            content,
        )

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

    def test_write_current_work_keeps_active_planning_session_out_of_canonical_files(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Keep planning records separate from blueprint truth.",
            "sprint": "2026-04-11-planning-boundary",
            "canonical_document_layers": ai_status.default_canonical_document_layers(),
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {
            "session_id": "phase6-2026-04-16-oss-ecosystem-closure",
            "status": "accepted",
            "artifacts": {
                "planning_readme": {
                    "path": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md"
                },
                "planning_session": {
                    "path": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json"
                },
            },
            "session_file": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json",
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-planning-boundary-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn(
            "- Planning mode: `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md`",
            content,
        )
        self.assertNotIn(
            "`docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md`,",
            content.split("- Canonical files: ", 1)[1].split("\n", 1)[0],
        )

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
                with (
                    mock.patch.object(ai_status, "load_json_file", return_value=orchestrator_state),
                    mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
                    mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
                ):
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
        self.assertEqual(bundle["runtime_summary"]["dispatch_targets"]["Codex"], 35)
        self.assertEqual(bundle["runtime_summary"]["dispatch_targets"]["Gemini"], 5)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["in_review"], 1)
        self.assertEqual(bundle["planning_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["source_plane"], "planning")
        self.assertEqual(bundle["bridge_summary"]["session_id"], "phase2-2026-04-13-blueprint-gap")
        self.assertEqual(bundle["bridge_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["pending_materialization_count"], 1)
        self.assertEqual(bundle["bridge_summary"]["consensus_packet"], "docs/02-architecture/consensus/phase2/consensus-packet.md")
        self.assertEqual(len(bundle["truth_mismatches"]), 1)
        self.assertEqual({item["type"] for item in bundle["truth_mismatches"]}, {"running_worker_on_todo"})
        mismatch_hints = {item["type"]: item["resolution_hint"] for item in bundle["truth_mismatches"]}
        self.assertIn("先把 task 狀態推成 in_progress", mismatch_hints["running_worker_on_todo"])
        self.assertEqual(bundle["worker_task_links"][0]["task_id"], "APP-002-W1-FRONT-HANDOFF")
        self.assertEqual(bundle["worker_task_links"][0]["task_title"], "Publish front-end handoff packet")
        self.assertEqual(bundle["worker_task_links"][0]["task_summary"], "整理交接封包。")
        self.assertEqual(bundle["worker_task_links"][0]["queue_status"], None)
        self.assertEqual(bundle["worker_task_links"][0]["mismatch_count"], 1)
        self.assertIn("running_worker_on_todo", bundle["worker_task_links"][0]["mismatch_flags"])
        self.assertTrue(bundle["worker_task_links"][0]["resolution_hints"])

    def test_related_live_sidecar_worker_does_not_flag_parent_as_without_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T15:32:45Z",
            "agents": [
                {"name": "Codex", "status": "busy", "current_task_ids": ["BP5-SVC-001"], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
                {"name": "Claude", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
                {"name": "Gemini", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "title": "Lock the deployable service baseline and single-VM topology",
                    "summary_zh": "主線 baseline 定義。",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Supervisor auto-started BP5-SVC-001 after successful dispatch.",
                    "last_update": "2026-04-15T15:29:37Z",
                },
                {
                    "id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "title": "Prepare BP5-SVC-001 acceptance packet and dependency map",
                    "summary_zh": "Sidecar review 中。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": ["support/sidecars/BP5-SVC-001/BP5-SVC-001-SIDECAR-ACCEPTANCE.md"],
                    "acceptance": [],
                    "next": "Acceptance packet handed off to Codex for review.",
                    "last_update": "2026-04-15T15:32:28Z",
                    "task_class": "sidecar",
                    "auto_generated": True,
                    "helper_parent": "BP5-SVC-001",
                    "helper_kind": "acceptance_packet",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T15:32:45Z"},
            "queue": {"events": {"evt-1": {"status": "started", "run_id": "codex-20260415T153233Z-97359030", "processed_at": "2026-04-15T15:32:33Z"}}},
            "workers": {
                "codex-20260415T153233Z-97359030": {
                    "run_id": "codex-20260415T153233Z-97359030",
                    "task_id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "queue_event_id": "evt-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "last_event_at": "2026-04-15T15:32:47Z",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "done"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
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

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-001", mismatch_ids)

    def test_paused_reviewer_does_not_flag_review_task_without_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T16:35:29Z",
            "agents": [
                {"name": "Claude", "status": "working", "current_task_ids": ["BP5-SVC-002"], "branch": "", "next": "", "last_update": "2026-04-15T16:35:29Z"},
                {"name": "Qwen", "status": "blocked", "current_task_ids": ["BP5-LUV-001"], "branch": "", "next": "", "last_update": "2026-04-15T16:35:29Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-002",
                    "title": "Registry review",
                    "summary_zh": "等待 reviewer 檢查。",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Ready for Qwen review.",
                    "last_update": "2026-04-15T16:35:29Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T16:35:46Z"},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2099-04-15T16:38:40Z",
                        "summary": "Capacity / rate limit failure",
                    }
                }
            },
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "review" if task_id == "BP5-SVC-002" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
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

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-002", mismatch_ids)

    def test_review_task_without_live_worker_does_not_flag_truth_mismatch(self) -> None:
        state = {
            "updated_at": "2026-04-16T06:50:44Z",
            "agents": [
                {"name": "Claude", "status": "reviewing", "current_task_ids": ["BP5-LUV-007"], "branch": "", "next": "", "last_update": "2026-04-16T06:50:44Z"},
                {"name": "Codex", "status": "working", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-16T06:50:44Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-LUV-007",
                    "title": "Lovable lineage review",
                    "summary_zh": "等待 reviewer 接手。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Review notes already prepared; waiting in review queue.",
                    "last_update": "2026-04-16T06:50:44Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-16T06:53:55Z"},
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "review" if task_id == "BP5-LUV-007" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
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

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-LUV-007", mismatch_ids)

    def test_coordination_worker_missing_taskboard_entry_does_not_flag_truth_mismatch(self) -> None:
        state = {
            "updated_at": "2026-04-16T06:53:55Z",
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-16T06:53:55Z"},
            "queue": {"events": {}},
            "workers": {
                "codex-1": {
                    "run_id": "codex-1",
                    "task_id": "PKT-002-incident-action-drawer",
                    "queue_event_id": "coord-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "last_event_at": "2026-04-16T06:53:55Z",
                    "request_snapshot": {
                        "reason": "coordination:bff-gap",
                        "metadata": {
                            "coordination": {
                                "feature_id": "PKT-002-incident-action-drawer",
                                "payload_type": "bff-gap",
                            }
                        },
                    },
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {}
        resolver.dependency_status.side_effect = lambda task_id: "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: False
        resolver.get.side_effect = lambda task_id: None
        resolver.source.side_effect = lambda task_id: None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("worker-task-missing:codex-1", mismatch_ids)

    def test_pending_approval_task_does_not_flag_without_live_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T16:41:31Z",
            "agents": [
                {"name": "Claude", "status": "working", "current_task_ids": ["BP5-SVC-003"], "branch": "", "next": "", "last_update": "2026-04-15T16:41:31Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-003",
                    "title": "Governance API fixup",
                    "summary_zh": "等待 approval 後續跑。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Waiting for safe verification approval.",
                    "last_update": "2026-04-15T16:41:31Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T16:41:31Z"},
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "BP5-SVC-003",
                    "worker_run_id": "claude-run-1",
                }
            ],
            "history": [],
        }

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "in_progress" if task_id == "BP5-SVC-003" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
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

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-003", mismatch_ids)

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
        config = {"paths": {"state_file": ".orchestrator/state.json", "event_queue": ".orchestrator/event-queue.jsonl"}}
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {"supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-11T13:08:22Z"}, "queue": {"events": {}}, "workers": {}}
        approval_state = {"pending": [], "history": []}

        with tempfile.TemporaryDirectory(prefix="ai-status-dashboard-bundle-") as temp_dir:
            output_path = Path(temp_dir) / "dashboard-bundle.json"
            with mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", output_path):
                with mock.patch.object(ai_status, "load_config", return_value=config):
                    with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                        with mock.patch.object(ai_status, "load_runtime_state", return_value=orchestrator_state) as load_runtime_state:
                            with mock.patch.object(ai_status, "load_json_file", return_value=approval_state) as load_json_file:
                                ai_status.write_dashboard_bundle(state)

            bundle = json.loads(output_path.read_text(encoding="utf-8"))

        load_runtime_state.assert_called_once_with(config)
        load_json_file.assert_called_once_with(ai_status.APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
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
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["done"], 2)
        self.assertEqual(bundle["execution_summary"]["superseded"], 1)
        self.assertEqual(bundle["archive_summary"]["recent_terminal_ids"], ["REG-100"])
        self.assertEqual(bundle["archive_summary"]["recent_terminal_tasks"][0]["task_id"], "REG-100")

    def test_build_dashboard_bundle_distinguishes_dependency_ready_from_dispatchable_ready(self) -> None:
        state = {
            "updated_at": "2026-04-16T08:50:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "RUN-001",
                    "title": "Running task",
                    "summary_zh": "Claude lane 已占用。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Still running",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CLAUDE",
                    "title": "Ready but owner busy",
                    "summary_zh": "依賴都完成，但 Claude 已忙碌。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CODEX",
                    "title": "Ready but provider paused",
                    "summary_zh": "依賴都完成，但 Codex 正在 pause。",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z", "focus_mode": "execution"},
            "queue": {"events": {}},
            "workers": {
                "claude-run": {
                    "run_id": "claude-run",
                    "provider": "claude",
                    "task_id": "RUN-001",
                    "status": "running",
                    "last_event_at": "2026-04-16T08:50:04Z",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                }
            },
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "paused_at": "2026-04-16T08:45:00Z",
                        "blocked_until": "2026-04-16T09:00:00Z",
                        "reason": "402 You have no quota",
                    }
                }
            },
        }
        approval_state = {"pending": [], "history": []}
        config = {"ready_dispatcher": {"max_tasks_per_agent_by_agent": {"Claude": 1}}}

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 2)

    def test_build_dashboard_bundle_counts_ready_capacity_when_owner_has_free_slots(self) -> None:
        state = {
            "updated_at": "2026-04-16T08:50:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "RUN-CODEX",
                    "title": "Running Codex task",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Running",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CODEX",
                    "title": "Ready Codex task",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z", "focus_mode": "execution"},
            "queue": {"events": {}},
            "workers": {
                "codex-run": {
                    "run_id": "codex-run",
                    "logical_agent_id": "codex",
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "task_id": "RUN-CODEX",
                    "status": "running",
                    "last_event_at": "2026-04-16T08:50:04Z",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                }
            },
            "provider_guardrails": {"dispatch_pauses": {}},
        }
        approval_state = {"pending": [], "history": []}
        config = {"ready_dispatcher": {"max_tasks_per_agent_by_agent": {"Codex": 2}}}

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["dispatch_policy"]["max_tasks_per_agent"], None)
        self.assertEqual(bundle["dispatch_policy"]["max_tasks_per_agent_by_agent"], {"Codex": 2})

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

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
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

    def test_build_dashboard_bundle_does_not_count_stale_bff_gap_after_frontend_feedback(self) -> None:
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
                    "PKT-003-lineage-view": {
                        "feature_id": "PKT-003-lineage-view",
                        "screen": "lineage-view",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/PKT-003-lineage-view-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "bff-gap": {
                                "type": "bff-gap",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-bff-gap.yaml",
                                "payload": {"type": "bff-gap", "status": "blocked", "summary": "Old gap payload"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/PKT-003-lineage-view-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        summary = bundle["coordination_summary"]
        self.assertEqual(summary["counts"]["open_bff_gaps"], 0)
        self.assertEqual(summary["features"][0]["stage"], "frontend_feedback_received")
        self.assertFalse(summary["features"][0]["bff_gap_open"])
        self.assertTrue(summary["features"][0]["has_bff_gap"])

    def test_build_dashboard_bundle_marks_reviewed_frontend_feedback_when_review_packet_exists(self) -> None:
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
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(
                ai_status,
                "coordination_review_snapshot",
                return_value={"path": ".coordination/reviews/F-042-review.md", "disposition": "approved"},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "frontend_feedback_reviewed")
        self.assertEqual(feature["review_disposition"], "approved")
        self.assertEqual(feature["paths"]["review"], ".coordination/reviews/F-042-review.md")

    def test_build_dashboard_bundle_prefers_closeout_response_for_loop_complete(self) -> None:
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
                    "EW-05-mutation-review": {
                        "feature_id": "EW-05-mutation-review",
                        "screen": "mutation-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/EW-05-mutation-review-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/EW-05-mutation-review-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "close",
                                    "can_close": True,
                                    "lovable_ui_task_status": "closed",
                                    "next_action": "none",
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "follow-up-required"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "load_local_coordination_payload", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "loop_complete")

    def test_build_dashboard_bundle_counts_pantheon_frontend_feedback_response_as_feedback_and_runtime_proof(self) -> None:
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
                    "KW-01-institutional-memory": {
                        "feature_id": "KW-01-institutional-memory",
                        "screen": "institutional-memory",
                        "summary": "Pantheon closeout proof ready",
                        "current_payload_type": "lovable-ui-task",
                        "source_repo": "ajoe734/pantheon",
                        "source_repo_id": "pantheon",
                        "target_agent": "Gemini",
                        "worker_kind": "runtime-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/KW-01-institutional-memory-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI ready"},
                                "updated_at": "2026-04-14T02:03:30Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/KW-01-institutional-memory-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "close",
                                    "can_close": True,
                                    "runtime_verified_at": "2026-04-14T02:04:30Z",
                                    "verified_runtime_ref": ".coordination/reviews/KW-01-institutional-memory-review.md",
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "coordination_repo_root", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "loop_complete")
        self.assertTrue(feature["has_frontend_feedback"])
        self.assertEqual(feature["paths"]["frontend_feedback"], ".coordination/responses/KW-01-institutional-memory-frontend-feedback.yaml")
        self.assertTrue(feature["state_flags"]["runtime_verified"])
        self.assertEqual(bundle["coordination_summary"]["counts"]["frontend_feedback_received"], 1)
        self.assertEqual(bundle["coordination_summary"]["counts"]["runtime_verified"], 1)

    def test_build_dashboard_bundle_marks_closed_scope_when_followup_response_has_no_active_next_step(self) -> None:
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
                    "PKT-003-post-incident-review": {
                        "feature_id": "PKT-003-post-incident-review",
                        "screen": "post-incident-review-console",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/PKT-003-post-incident-review-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "follow-up-required",
                                    "can_close": False,
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/PKT-003-post-incident-review-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "closed"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "load_local_coordination_payload", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "closed")

    def test_build_dashboard_bundle_exposes_coordination_state_flags(self) -> None:
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
                        "summary": "Contract ready and mirrored",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                            "needs-runtime": {
                                "type": "needs-runtime",
                                "path": ".coordination/requests/F-042-needs-runtime.yaml",
                                "payload": {
                                    "type": "needs-runtime",
                                    "status": "resolved",
                                    "runtime_verified_at": "2026-04-14T02:02:30Z",
                                },
                                "updated_at": "2026-04-14T02:02:30Z",
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

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "coordination_repo_root", side_effect=lambda repo_id: Path(f"/tmp/{repo_id}")),
            mock.patch.object(
                ai_status,
                "coordination_repo_payload_exists",
                side_effect=lambda _root, rel_path: str(rel_path).endswith("F-042-contract-ready.yaml"),
            ),
            mock.patch.object(
                ai_status,
                "coordination_audit_matches",
                side_effect=lambda _root, _feature_id, marker: marker in {"dispatch-emitted", "received"},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        flags = feature["state_flags"]
        self.assertEqual(flags["backend_route_live"], True)
        self.assertEqual(flags["pantheon_handoff_published"], True)
        self.assertEqual(flags["mirrored_to_front_default_branch"], True)
        self.assertEqual(flags["dispatch_emitted"], True)
        self.assertEqual(flags["front_receiver_applied"], True)
        self.assertEqual(flags["lovable_consumed"], True)
        self.assertEqual(flags["ui_activated"], True)
        self.assertEqual(flags["runtime_verified"], True)

        counts = bundle["coordination_summary"]["counts"]
        self.assertEqual(counts["backend_route_live"], 1)
        self.assertEqual(counts["pantheon_handoff_published"], 1)
        self.assertEqual(counts["mirrored_to_front_default_branch"], 1)
        self.assertEqual(counts["dispatch_emitted"], 1)
        self.assertEqual(counts["front_receiver_applied"], 1)
        self.assertEqual(counts["lovable_consumed"], 1)
        self.assertEqual(counts["ui_activated"], 1)
        self.assertEqual(counts["runtime_verified"], 1)

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


class ActivityLogRotationTests(unittest.TestCase):
    def _make_log(self, *, size_per_line: int = 200, line_count: int = 100) -> Path:
        tmp = tempfile.TemporaryDirectory(prefix="ai-status-rotate-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        log_path = root / "ai-activity-log.jsonl"
        payload = ("x" * (size_per_line - 1)) + "\n"
        log_path.write_text(payload * line_count, encoding="utf-8")
        return log_path

    def test_does_not_rotate_when_under_threshold(self) -> None:
        log_path = self._make_log(line_count=10)
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1_000_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 5),
        ):
            archive = ai_status.maybe_rotate_activity_log()
        self.assertIsNone(archive)
        self.assertEqual(len(log_path.read_bytes().splitlines()), 10)
        archive_dir = log_path.parent / "archive" / "logs"
        self.assertFalse(archive_dir.exists())

    def test_rotates_and_keeps_tail_when_over_threshold(self) -> None:
        log_path = self._make_log(size_per_line=200, line_count=100)  # ~20 KB
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 5_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 8),
        ):
            archive = ai_status.maybe_rotate_activity_log()
        assert archive is not None
        self.assertTrue(archive.exists())
        self.assertTrue(str(archive).endswith(".gz"))
        # The active log now holds just the tail
        active_lines = log_path.read_bytes().splitlines()
        self.assertEqual(len(active_lines), 8)
        # The gzip archive holds the full original
        import gzip as _gz
        with _gz.open(archive, "rb") as fh:
            archived = fh.read().splitlines()
        self.assertEqual(len(archived), 100)

    def test_rotation_preserves_inode_for_concurrent_appenders(self) -> None:
        log_path = self._make_log(line_count=80)
        before_inode = log_path.stat().st_ino
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 3),
        ):
            ai_status.maybe_rotate_activity_log()
        after_inode = log_path.stat().st_ino
        self.assertEqual(before_inode, after_inode)

    def test_append_log_triggers_rotation(self) -> None:
        log_path = self._make_log(size_per_line=200, line_count=50)  # ~10 KB
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 5_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 4),
        ):
            ai_status.append_log({"ts": "2026-05-18T00:00:00Z", "msg": "new entry"})
        active_lines = log_path.read_text(encoding="utf-8").splitlines()
        # 4 kept tail lines + 1 new = 5
        self.assertEqual(len(active_lines), 5)
        self.assertIn("new entry", active_lines[-1])
        archive_dir = log_path.parent / "archive" / "logs"
        archives = list(archive_dir.glob("*.gz"))
        self.assertEqual(len(archives), 1)


if __name__ == "__main__":
    unittest.main()
