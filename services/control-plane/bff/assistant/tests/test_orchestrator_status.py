"""Tests for orchestrator status readback (ASST-INTEG-007)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ..orchestrator_status import read_orchestrator_status


class TestOrchestratorStatus(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)

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
                }
            },
        }
        (orchestrator_dir / "state.json").write_text(json.dumps(self.state_data), encoding="utf-8")

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
        self._tmpdir.cleanup()

    def test_read_orchestrator_status_success(self):
        status = read_orchestrator_status(str(self.repo_root))

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
