"""Tests for orchestrator status readback (ASST-INTEG-007)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ..orchestrator_status import read_orchestrator_status


class TestOrchestratorStatus(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self._old_inbox_env = os.environ.pop("PANTHEON_ASSISTANT_DEV_PACKET_INBOX", None)

        # Create ai-status.json
        self.ai_status_data = {
            "project": "test-project",
            "sprint": "test-sprint",
            "objective": "test-objective",
            "updated_at": "2026-06-03T12:00:00Z",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Task 1",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "next": "Next step 1",
                    "waiting_for": "CI",
                }
            ],
            "handoffs": [],
            "blockers": [
                {
                    "task_id": "TASK-1",
                    "status": "blocked",
                    "waiting_for": "CI",
                    "message": "Waiting for required checks",
                }
            ],
        }
        (self.repo_root / "ai-status.json").write_text(json.dumps(self.ai_status_data), encoding="utf-8")

        # Create .orchestrator/state.json
        orchestrator_dir = self.repo_root / ".orchestrator"
        orchestrator_dir.mkdir()
        self.state_data = {
            "workers": {
                "run_1": {
                    "task_id": "TASK-1",
                    "agent_id": "Codex",
                    "provider": "codex",
                    "status": "running",
                    "started_at": "2026-06-03T12:05:00Z",
                    "last_event_at": "2026-06-03T12:08:00Z",
                    "last_error": "Bearer abcdef123456 failed at /home/me/.codex/session.json",
                    "queue_event_id": "event-1",
                    "delivery_mode": "worker",
                    "resume_token": "do-not-expose",
                }
            },
            "queue": {
                "events": {
                    "event-1": {
                        "status": "started",
                        "attempt_count": 1,
                        "error": "OPENAI_API_KEY=sk-test123",
                    }
                }
            },
            "supervisor": {
                "lifecycle": "idle",
                "last_loop_error": "token=super-secret",
            },
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "reason": "capacity",
                        "detail": "ANTHROPIC_API_KEY=secret",
                        "blocked_until": "2026-06-03T12:30:00Z",
                    }
                },
                "task_failure_streaks": {
                    "TASK-1:claude": {
                        "task_id": "TASK-1",
                        "provider": "claude",
                        "count": 2,
                        "last_reason": "You've hit your weekly limit token=hidden",
                        "last_failure_at": "2026-06-03T12:10:00Z",
                        "last_failure_kind": "terminal",
                    }
                },
            },
            "assistant_dev_bridge": {
                "last_drain_at": "2026-06-03T12:12:00Z",
                "last_result": {
                    "status": "drained",
                    "processedCount": 1,
                    "errorCount": 0,
                    "inbox": "token=do-not-expose",
                },
            },
        }
        (orchestrator_dir / "state.json").write_text(json.dumps(self.state_data), encoding="utf-8")

        # Create assistant DevTaskPacket inbox readback fixtures.
        inbox = orchestrator_dir / "assistant-dev-packets"
        for name in ("pending", "processed", "failed", "receipts"):
            (inbox / name).mkdir(parents=True)
        (inbox / "pending" / "pkt-pending.json").write_text(
            json.dumps({"taskPacket": {"packetId": "pkt-pending"}}),
            encoding="utf-8",
        )
        (inbox / "processed" / "pkt-processed.json").write_text(
            json.dumps({"taskPacket": {"packetId": "pkt-processed"}}),
            encoding="utf-8",
        )
        (inbox / "failed" / "pkt-failed.json").write_text(
            json.dumps({"taskPacket": {"packetId": "pkt-failed"}}),
            encoding="utf-8",
        )
        (inbox / "receipts" / "pkt-processed.json").write_text(
            json.dumps(
                {
                    "packetId": "pkt-processed",
                    "status": "processed",
                    "drainedAt": "2026-06-03T12:13:00Z",
                    "result": {"processedTaskCount": 1, "errors": []},
                }
            ),
            encoding="utf-8",
        )

        # Create .orchestrator/github-bus-state.json
        self.bus_data = {
            "tasks": {
                "TASK-1": {
                    "review_pr": {
                        "number": 101,
                        "url": "https://github.com/test/pull/101",
                        "state": "OPEN",
                        "merge_state_status": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "status_check_rollup": [
                            {
                                "name": "Commit trailers",
                                "status": "COMPLETED",
                                "conclusion": "SUCCESS",
                            },
                            {
                                "name": "Smoke acceptance",
                                "status": "IN_PROGRESS",
                            },
                        ],
                    },
                    "deployment": {
                        "status": "deployed",
                        "environment": "dev",
                    },
                }
            }
        }
        (orchestrator_dir / "github-bus-state.json").write_text(json.dumps(self.bus_data), encoding="utf-8")

    def tearDown(self):
        if self._old_inbox_env is None:
            os.environ.pop("PANTHEON_ASSISTANT_DEV_PACKET_INBOX", None)
        else:
            os.environ["PANTHEON_ASSISTANT_DEV_PACKET_INBOX"] = self._old_inbox_env
        self._tmpdir.cleanup()

    def test_read_orchestrator_status_success(self):
        status = read_orchestrator_status(
            str(self.repo_root),
            provider_readiness=lambda: {
                "provider": "codex_cli",
                "provider_name": "codex",
                "runtime": "openclaw_gateway_cli_mount",
                "ready": True,
                "status": "ready",
                "auth": "account_session",
                "auth_status": "ready",
                "binary_path": "/usr/local/bin/codex",
                "checked_at": "2026-06-03T12:14:00Z",
                "repair_workspace": {
                    "ready": True,
                    "status": "ready",
                    "root": "/srv/pantheon-assistant/worktrees",
                    "writable": True,
                    "worktreeCount": 2,
                },
                "capabilities": {"read": True, "repairWrite": True},
            },
            openclaw_tool_policy=lambda: {
                "allowed_tools": ["assistant.command"],
                "allowed_workflows": [],
                "assistant_command_tool": "assistant.command",
                "default_posture": "deny_all",
                "always_blocked_tools": ["broker_order", "live_order"],
                "always_blocked_tool_prefixes": ["broker.", "live.", "paper.", "capital."],
                "always_blocked_workflow_prefixes": ["broker.", "live.", "paper.", "capital."],
            },
            openclaw_effective_tools=lambda: {
                "status": "degraded",
                "upstream_status": "degraded",
                "agent_id": "management-ai",
                "policy_allowed_tools": ["assistant.command", "assistant.sa_sd.generate"],
                "effective_tools": ["assistant.command", "assistant.sa_sd.generate"],
                "effective_skills": [
                    {
                        "id": "assistant.command",
                        "title": "Assistant Command Authorization",
                        "surface": "assistant_command",
                        "mode_gate": {"type": "allowlist", "default": "deny", "allowed_modes": ["kernel_debug"]},
                        "role": "operator",
                        "confirm_policy": {"required": False},
                        "input_schema": {"type": "object"},
                        "handler_ref": "openclaw.tool:assistant.command",
                        "result_surface": "assistant_command_authorization",
                    },
                    {
                        "id": "assistant.sa_sd.generate",
                        "title": "Generate SA/SD",
                        "surface": "assistant_command",
                        "mode_gate": {"type": "allowlist", "default": "deny", "allowed_modes": ["kernel_debug"]},
                        "role": "operator",
                        "confirm_policy": {"required": False},
                        "input_schema": {"type": "object"},
                        "handler_ref": "bff.route:POST /bff/assistant/dev-docs/generate",
                        "result_surface": "assistant_dev_docs_packet",
                    },
                ],
                "note": "upstream unavailable; policy allowlist visible",
            },
        )

        self.assertEqual(status.project, "test-project")
        self.assertEqual(status.sprint, "test-sprint")
        self.assertTrue(status.snapshot_at)
        source_refs = {source["path"]: source for source in status.source_refs}
        self.assertTrue(source_refs["ai-status.json"]["available"])
        self.assertTrue(source_refs[".orchestrator/state.json"]["available"])
        self.assertTrue(source_refs[".orchestrator/github-bus-state.json"]["available"])
        self.assertEqual(len(status.tasks), 1)
        self.assertEqual(status.tasks[0].id, "TASK-1")
        self.assertEqual(status.tasks[0].status, "in_progress")
        self.assertEqual(status.tasks[0].waiting_for, "CI")
        self.assertEqual(status.tasks[0].blockers[0]["waitingFor"], "CI")
        self.assertEqual(status.tasks[0].blockers[1]["type"], "worker_failure_streak")
        self.assertEqual(status.tasks[0].blockers[1]["provider"], "claude")
        self.assertEqual(status.tasks[0].blockers[1]["count"], 2)
        self.assertNotIn("hidden", json.dumps(status.tasks[0].blockers[1]))

        # Verify GitHub bus info is preserved and normalized for assistant readback.
        self.assertIsNotNone(status.tasks[0].delivery)
        self.assertEqual(status.tasks[0].delivery["github_bus"]["review_pr"]["number"], 101)
        self.assertTrue(status.tasks[0].github["available"])
        self.assertEqual(status.tasks[0].github["reviewPr"]["number"], 101)
        self.assertEqual(status.tasks[0].github["reviewPr"]["ci"]["summary"], "pending")
        self.assertEqual(status.tasks[0].github["reviewPr"]["merge"]["mergeStateStatus"], "CLEAN")
        self.assertEqual(status.tasks[0].deployment["status"], "deployed")

        self.assertEqual(len(status.workers), 1)
        self.assertEqual(status.workers[0].run_id, "run_1")
        self.assertEqual(status.workers[0].status, "running")
        self.assertEqual(status.workers[0].queue_event_id, "event-1")
        self.assertEqual(status.workers[0].delivery_mode, "worker")
        self.assertNotIn("abcdef123456", status.workers[0].last_error or "")
        self.assertNotIn(".codex", status.workers[0].last_error or "")

        self.assertEqual(status.queue[0]["eventId"], "event-1")
        self.assertNotIn("sk-test123", status.queue[0]["lastError"])
        self.assertEqual(status.supervisor["lifecycle"], "idle")
        self.assertNotIn("super-secret", status.supervisor["lastLoopError"])
        self.assertNotIn("secret", status.provider_guardrails["dispatchPauses"][0]["detail"])
        self.assertEqual(status.provider_guardrails["taskFailureStreakCount"], 1)
        self.assertEqual(status.provider_guardrails["taskFailureStreaks"][0]["taskId"], "TASK-1")
        self.assertEqual(status.provider_guardrails["taskFailureStreaks"][0]["provider"], "claude")
        self.assertNotIn("hidden", json.dumps(status.provider_guardrails["taskFailureStreaks"][0]))
        self.assertEqual(status.provider_readiness["status"], "ready")
        self.assertTrue(status.provider_readiness["ready"])
        self.assertEqual(status.provider_readiness["authStatus"], "ready")
        self.assertTrue(status.provider_readiness["repairWorkspace"]["ready"])
        self.assertEqual(
            status.provider_readiness["repairWorkspace"]["root"],
            "/srv/pantheon-assistant/worktrees",
        )
        self.assertTrue(status.provider_readiness["capabilities"]["repairWrite"])
        self.assertEqual(status.openclaw_tool_policy["status"], "ready")
        self.assertTrue(status.openclaw_tool_policy["assistantCommandAllowed"])
        self.assertTrue(status.openclaw_tool_policy["assistantCommandEffective"])
        self.assertTrue(status.openclaw_tool_policy["assistantCommandUsable"])
        self.assertEqual(status.openclaw_tool_policy["assistantCommandStatus"], "usable")
        self.assertEqual(status.openclaw_tool_policy["allowedTools"], ["assistant.command"])
        self.assertEqual(status.openclaw_tool_policy["effectiveStatus"], "degraded")
        self.assertEqual(status.openclaw_tool_policy["effectiveTools"], ["assistant.command", "assistant.sa_sd.generate"])
        self.assertEqual(status.openclaw_tool_policy["policyAllowedTools"], ["assistant.command", "assistant.sa_sd.generate"])
        skill_ids = [skill["id"] for skill in status.openclaw_tool_policy["effectiveSkills"]]
        self.assertEqual(skill_ids, ["assistant.command", "assistant.sa_sd.generate"])
        self.assertEqual(
            status.openclaw_tool_policy["effectiveSkills"][1]["handler_ref"],
            "bff.route:POST /bff/assistant/dev-docs/generate",
        )
        self.assertIn("broker_order", status.openclaw_tool_policy["alwaysBlockedTools"])
        self.assertEqual(status.assistant_dev_bridge["status"], "attention")
        self.assertEqual(status.assistant_dev_bridge["inbox"]["pendingCount"], 1)
        self.assertEqual(status.assistant_dev_bridge["inbox"]["processedCount"], 1)
        self.assertEqual(status.assistant_dev_bridge["inbox"]["failedCount"], 1)
        self.assertEqual(status.assistant_dev_bridge["inbox"]["receiptCount"], 1)
        self.assertEqual(status.assistant_dev_bridge["recentReceipts"][0]["packetId"], "pkt-processed")
        self.assertNotIn("do-not-expose", json.dumps(status.assistant_dev_bridge))

    def test_read_orchestrator_status_provider_readiness_failure_degrades(self):
        def failing_provider_readiness():
            raise RuntimeError("gateway unavailable token=hidden")

        status = read_orchestrator_status(
            str(self.repo_root),
            provider_readiness=failing_provider_readiness,
        )

        self.assertFalse(status.provider_readiness["available"])
        self.assertEqual(status.provider_readiness["status"], "unavailable")
        self.assertEqual(status.provider_readiness["reason"], "RuntimeError")
        self.assertNotIn("hidden", status.provider_readiness["message"])

    def test_read_orchestrator_status_tool_policy_failure_degrades(self):
        def failing_tool_policy():
            raise RuntimeError("tool policy unavailable token=hidden")

        status = read_orchestrator_status(
            str(self.repo_root),
            openclaw_tool_policy=failing_tool_policy,
        )

        self.assertFalse(status.openclaw_tool_policy["available"])
        self.assertEqual(status.openclaw_tool_policy["status"], "unavailable")
        self.assertEqual(status.openclaw_tool_policy["reason"], "RuntimeError")
        self.assertFalse(status.openclaw_tool_policy["assistantCommandAllowed"])
        self.assertNotIn("hidden", status.openclaw_tool_policy["message"])

    def test_read_orchestrator_status_tool_policy_allowed_but_not_effective(self):
        status = read_orchestrator_status(
            str(self.repo_root),
            openclaw_tool_policy=lambda: {
                "allowed_tools": ["assistant.command"],
                "allowed_workflows": [],
                "assistant_command_tool": "assistant.command",
                "default_posture": "deny_all",
            },
            openclaw_effective_tools=lambda: {
                "status": "degraded",
                "upstream_status": "degraded",
                "agent_id": "management-ai",
                "policy_allowed_tools": ["assistant.command"],
                "effective_tools": [],
            },
        )

        self.assertTrue(status.openclaw_tool_policy["assistantCommandAllowed"])
        self.assertFalse(status.openclaw_tool_policy["assistantCommandEffective"])
        self.assertFalse(status.openclaw_tool_policy["assistantCommandUsable"])
        self.assertEqual(
            status.openclaw_tool_policy["assistantCommandStatus"],
            "policy_allowed_not_effective",
        )

    def test_read_orchestrator_status_missing_files(self):
        # Test with empty dir
        empty_dir = tempfile.TemporaryDirectory()
        try:
            status = read_orchestrator_status(empty_dir.name)
            self.assertEqual(status.project, "unknown")
            self.assertEqual(len(status.tasks), 0)
            self.assertEqual(len(status.workers), 0)
            self.assertFalse(status.source_refs[0]["available"])
        finally:
            empty_dir.cleanup()

    def test_read_orchestrator_status_missing_github_bus_degrades(self):
        (self.repo_root / ".orchestrator" / "github-bus-state.json").unlink()

        status = read_orchestrator_status(str(self.repo_root))

        source_refs = {source["path"]: source for source in status.source_refs}
        self.assertFalse(source_refs[".orchestrator/github-bus-state.json"]["available"])
        self.assertEqual(source_refs[".orchestrator/github-bus-state.json"]["status"], "unavailable")
        self.assertFalse(status.tasks[0].github["available"])
        self.assertEqual(status.tasks[0].github["status"], "unavailable")
        self.assertFalse(status.tasks[0].deployment["available"])


if __name__ == "__main__":
    unittest.main()
