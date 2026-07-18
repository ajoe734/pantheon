#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
                "evidence_dir": str(self.root / "evidence"),
            },
            "approvals": {
                "stale_pending_seconds": 1800,
            },
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _fresh_created_at(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _stale_created_at(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat().replace("+00:00", "Z")

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
        self.assertTrue(saved["history"][0]["resolution_ref"])
        self.assertTrue(Path(saved["history"][0]["resolution_ref"]).exists())

    def test_keeps_pending_approval_when_worker_is_alive(self) -> None:
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-live-worker",
                        "status": "pending",
                        "created_at": self._fresh_created_at(),
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
                        "created_at": self._fresh_created_at(),
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

    def test_keeps_pending_approval_when_claude2_worker_can_resume_session(self) -> None:
        self.config["providers"] = {"claude2": {"delivery_mode": "claude_cli"}}
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-claude2-resume",
                        "status": "pending",
                        "created_at": self._fresh_created_at(),
                        "provider": "claude2",
                        "task_id": "LP-005",
                        "worker_run_id": "claude2-resume",
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
                    "claude2-resume": {
                        "run_id": "claude2-resume",
                        "task_id": "LP-005",
                        "provider": "claude2",
                        "status": "waiting_approval",
                        "pid": 999999,
                        "session_id": "sess-456",
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
        self.assertEqual(saved["pending"][0]["approval_id"], "apr-claude2-resume")

    def test_prunes_pending_approval_after_stale_window_despite_live_worker(self) -> None:
        # Regression for OPS-APPROVAL-BROKER-RISK-CLASS-001: previously any
        # pending item with a task_id/worker_run_id was unconditionally
        # excluded from the staleness check, so a task-bound approval could
        # hang forever even with a live, non-resumable worker.
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-live-worker-stale",
                        "status": "pending",
                        "created_at": self._stale_created_at(),
                        "provider": "claude",
                        "task_id": "OC-003",
                        "worker_run_id": "claude-live-stale",
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
                    "claude-live-stale": {
                        "run_id": "claude-live-stale",
                        "task_id": "OC-003",
                        "status": "waiting_approval",
                        "pid": os.getpid(),
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        pruned = approval_queue.prune_stale_approvals(self.config)

        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]["approval_id"], "apr-live-worker-stale")
        self.assertEqual(pruned[0]["decision"], "deny")
        self.assertIn("without a broker decision", pruned[0]["note"])
        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["pending"], [])

    def test_prunes_pending_approval_after_stale_window_despite_resumable_claude_session(self) -> None:
        # Direct regression for the 2026-07-17 incident: claude-1/2/3 workers
        # stayed in suspended_approval for 5-8.7h because a resumable Claude
        # session (session_id present) made the orphan check keep the
        # approval alive indefinitely, and the time-based stale check never
        # ran at all since the item carried a task_id/worker_run_id.
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-claude-resume-stale",
                        "status": "pending",
                        "created_at": self._stale_created_at(),
                        "provider": "claude",
                        "task_id": "LP-006",
                        "worker_run_id": "claude-resume-stale",
                        "tool_name": "TaskOutput",
                    }
                ],
                "history": [],
            },
        )
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-resume-stale": {
                        "run_id": "claude-resume-stale",
                        "task_id": "LP-006",
                        "provider": "claude",
                        "status": "suspended_approval",
                        "pid": 999999,
                        "session_id": "sess-789",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        pruned = approval_queue.prune_stale_approvals(self.config)

        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]["approval_id"], "apr-claude-resume-stale")
        self.assertEqual(pruned[0]["decision"], "deny")
        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["pending"], [])

    def test_create_approval_writes_request_evidence_and_sanitizes_queue_state(self) -> None:
        approval = approval_queue.create_approval(
            self.config,
            {
                "provider": "codex",
                "task_id": "BG-006",
                "worker_run_id": "codex-001",
                "agent_id": "Codex",
                "tool_name": "Bash",
                "tool_input": {"command": "python3 -m unittest discover -s .orchestrator -p test_approval_queue.py"},
                "risk_class": "needs_review",
            },
        )

        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        pending = saved["pending"][0]
        self.assertEqual(pending["approval_id"], approval["approval_id"])
        self.assertNotIn("tool_input", pending)
        self.assertTrue(pending["tool_input_signature"])
        self.assertIn("python3 -m unittest", pending["tool_input_preview"])
        self.assertTrue(pending["evidence_ref"])
        evidence_path = Path(pending["evidence_ref"])
        self.assertTrue(evidence_path.exists())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["stage"], "request")
        self.assertEqual(evidence["tool_input"]["command"], "python3 -m unittest discover -s .orchestrator -p test_approval_queue.py")

        # OPS-APPROVAL-BROKER-RISK-CLASS-001: newly created approvals get a
        # default expires_at (created_at + stale_pending_seconds) so pending
        # items are never left with expires_at: null indefinitely.
        created_at = approval_queue._parse_utc(pending["created_at"])
        expires_at = approval_queue._parse_utc(pending["expires_at"])
        self.assertIsNotNone(expires_at)
        self.assertAlmostEqual(
            (expires_at - created_at).total_seconds(),
            self.config["approvals"]["stale_pending_seconds"],
            delta=1,
        )

    def test_can_recover_tool_input_from_request_evidence(self) -> None:
        approval_queue.create_approval(
            self.config,
            {
                "provider": "claude",
                "task_id": "BG-001",
                "worker_run_id": "claude-001",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
                "risk_class": "needs_review",
                "suggested_rule": "Bash(git status)",
            },
        )

        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        pending = saved["pending"][0]
        self.assertNotIn("tool_input", pending)
        recovered = approval_queue._approval_tool_input(pending)
        self.assertEqual(recovered, {"command": "git status"})

    def test_resolve_approval_writes_resolution_evidence(self) -> None:
        approval = approval_queue.create_approval(
            self.config,
            {
                "provider": "codex",
                "task_id": "BG-004",
                "worker_run_id": "codex-002",
                "agent_id": "Codex",
                "tool_name": "WebFetch",
                "tool_input": {"url": "https://example.com/status"},
                "risk_class": "network",
            },
        )

        resolved = approval_queue.resolve_approval(
            self.config,
            approval["approval_id"],
            decision="deny",
            note="Network access denied in test",
            remember=False,
        )

        self.assertEqual(resolved["decision"], "deny")
        self.assertTrue(resolved["resolution_ref"])
        resolution_path = Path(resolved["resolution_ref"])
        self.assertTrue(resolution_path.exists())
        resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
        self.assertEqual(resolution["stage"], "resolution")
        self.assertEqual(resolution["decision"], "deny")
        self.assertEqual(resolution["request_ref"], approval["evidence_ref"])

        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["pending"], [])
        self.assertEqual(saved["history"][0]["approval_id"], approval["approval_id"])
        self.assertNotIn("tool_input", saved["history"][0])


if __name__ == "__main__":
    unittest.main()
