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
                    "next": "Next step 1"
                }
            ],
            "handoffs": [],
            "blockers": []
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
                    "status": "running",
                    "started_at": "2026-06-03T12:05:00Z"
                }
            },
            "supervisor": {
                "status": "idle"
            }
        }
        (orchestrator_dir / "state.json").write_text(json.dumps(self.state_data), encoding="utf-8")

        # Create .orchestrator/github-bus-state.json
        self.bus_data = {
            "tasks": {
                "TASK-1": {
                    "review_pr": {
                        "number": 101,
                        "url": "https://github.com/test/pull/101",
                        "status_check_rollup": "SUCCESS"
                    }
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

        # Verify GitHub bus info merge
        self.assertIsNotNone(status.tasks[0].delivery)
        self.assertEqual(status.tasks[0].delivery["github_bus"]["review_pr"]["number"], 101)

        self.assertEqual(len(status.workers), 1)
        self.assertEqual(status.workers[0].run_id, "run_1")
        self.assertEqual(status.workers[0].status, "running")

        self.assertEqual(status.supervisor["status"], "idle")

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


if __name__ == "__main__":
    unittest.main()
