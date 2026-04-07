#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import approval_queue


class ApprovalQueuePruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "approval_queue": str(self.root / "approval-queue.json"),
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "approvals": {
                "stale_pending_seconds": 1800,
            },
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_prunes_pending_approval_when_worker_state_is_missing(self) -> None:
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-missing-worker",
                        "status": "pending",
                        "created_at": "2026-04-06T10:00:00Z",
                        "provider": "claude",
                        "task_id": "OC-002",
                        "worker_run_id": "claude-missing",
                        "tool_name": "Bash",
                    }
                ],
                "history": [],
            },
        )
        self._write_json(self.root / "state.json", {"workers": {}, "queue": {"events": {}}})
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        pruned = approval_queue.prune_stale_approvals(self.config)

        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]["approval_id"], "apr-missing-worker")
        self.assertEqual(pruned[0]["decision"], "deny")
        self.assertIn("worker state disappeared", pruned[0]["note"])

        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["pending"], [])
        self.assertEqual(saved["history"][0]["approval_id"], "apr-missing-worker")

    def test_keeps_pending_approval_when_worker_is_alive(self) -> None:
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-live-worker",
                        "status": "pending",
                        "created_at": "2026-04-06T10:00:00Z",
                        "provider": "claude",
                        "task_id": "OC-002",
                        "worker_run_id": "claude-live",
                        "tool_name": "Bash",
                    }
                ],
                "history": [],
            },
        )
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-live": {
                        "run_id": "claude-live",
                        "task_id": "OC-002",
                        "status": "waiting_approval",
                        "pid": os.getpid(),
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        pruned = approval_queue.prune_stale_approvals(self.config)

        self.assertEqual(pruned, [])
        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved["pending"]), 1)
        self.assertEqual(saved["pending"][0]["approval_id"], "apr-live-worker")

    def test_keeps_pending_approval_when_claude_worker_can_resume_session(self) -> None:
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-claude-resume",
                        "status": "pending",
                        "created_at": "2026-04-06T10:00:00Z",
                        "provider": "claude",
                        "task_id": "LP-004",
                        "worker_run_id": "claude-resume",
                        "tool_name": "ToolSearch",
                    }
                ],
                "history": [],
            },
        )
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-resume": {
                        "run_id": "claude-resume",
                        "task_id": "LP-004",
                        "provider": "claude",
                        "status": "waiting_approval",
                        "pid": 999999,
                        "session_id": "sess-123",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        pruned = approval_queue.prune_stale_approvals(self.config)

        self.assertEqual(pruned, [])
        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved["pending"]), 1)
        self.assertEqual(saved["pending"][0]["approval_id"], "apr-claude-resume")


if __name__ == "__main__":
    unittest.main()
