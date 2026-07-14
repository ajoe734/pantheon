#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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

    def test_keeps_pending_approval_when_claude2_worker_can_resume_session(self) -> None:
        self.config["providers"] = {"claude2": {"delivery_mode": "claude_cli"}}
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [
                    {
                        "approval_id": "apr-claude2-resume",
                        "status": "pending",
                        "created_at": "2026-04-06T10:00:00Z",
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

    def test_create_approval_waits_for_runtime_admission_transaction(self) -> None:
        created = threading.Event()
        errors: list[BaseException] = []

        def producer() -> None:
            try:
                approval_queue.create_approval(
                    self.config,
                    {
                        "provider": "codex",
                        "task_id": "TASK-RUNTIME-GUARD",
                        "worker_run_id": "run-runtime-guard",
                        "tool_name": "Bash",
                        "tool_input": {"command": "git status"},
                    },
                )
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                created.set()

        with approval_queue.runtime_state_lock(self.config):
            thread = threading.Thread(target=producer)
            thread.start()
            self.assertFalse(created.wait(timeout=0.1))

        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        saved = json.loads((self.root / "approval-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["pending"][0]["task_id"], "TASK-RUNTIME-GUARD")

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

    def test_resume_override_permission_cleanup_is_durable_and_retriable(self) -> None:
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [],
                "history": [
                    {
                        "approval_id": "apr-cleanup-retry",
                        "status": "resolved",
                        "decision": "allow",
                        "worker_run_id": "run-cleanup-retry",
                        "resume_override_active": True,
                        "resume_override_consumed_at": None,
                        "resume_override_consumed_reason": None,
                        "resume_override_rule": "Bash(git status)",
                        "resume_override_rule_inserted": True,
                        "resume_override_suspended_ask_rules": ["Bash(git *)"],
                    }
                ],
            },
        )

        with (
            mock.patch(
                "permission_broker.remove_temporary_allow_rule",
                side_effect=[OSError("settings unavailable"), True],
            ) as remove_rule,
            mock.patch("permission_broker.restore_rules", return_value=["Bash(git *)"]) as restore_rules,
        ):
            with self.assertRaisesRegex(OSError, "settings unavailable"):
                approval_queue.consume_resume_override(
                    self.config,
                    approval_id="apr-cleanup-retry",
                    reason="worker_terminal",
                )

            after_failure = json.loads(
                (self.root / "approval-queue.json").read_text(encoding="utf-8")
            )["history"][0]
            self.assertFalse(after_failure["resume_override_active"])
            self.assertTrue(after_failure["resume_override_cleanup_pending"])
            self.assertTrue(after_failure["resume_override_consumed_at"])
            self.assertNotIn("resume_override_cleanup_completed_at", after_failure)

            completed = approval_queue.consume_resume_override(
                self.config,
                approval_id="apr-cleanup-retry",
                reason="worker_terminal_retry",
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertFalse(completed["resume_override_cleanup_pending"])
        self.assertTrue(completed["resume_override_cleanup_completed_at"])
        self.assertEqual(completed["resume_override_consumed_reason"], "worker_terminal")
        self.assertEqual(remove_rule.call_count, 2)
        restore_rules.assert_called_once_with(
            self.config,
            bucket="ask",
            rules=["Bash(git *)"],
        )

    def test_cleanup_pending_record_without_consumed_timestamp_is_repaired(self) -> None:
        self._write_json(
            self.root / "approval-queue.json",
            {
                "pending": [],
                "history": [
                    {
                        "approval_id": "apr-malformed-pending",
                        "status": "resolved",
                        "decision": "allow",
                        "worker_run_id": "run-malformed-pending",
                        "resume_override_active": False,
                        "resume_override_consumed_at": None,
                        "resume_override_cleanup_pending": True,
                        "resume_override_rule": "Bash(git status)",
                        "resume_override_rule_inserted": True,
                    }
                ],
            },
        )

        with mock.patch(
            "permission_broker.remove_temporary_allow_rule",
            return_value=True,
        ) as remove_rule:
            completed = approval_queue.consume_resume_override(
                self.config,
                approval_id="apr-malformed-pending",
                reason="repair_malformed_cleanup",
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertTrue(completed["resume_override_consumed_at"])
        self.assertFalse(completed["resume_override_cleanup_pending"])
        self.assertTrue(completed["resume_override_cleanup_completed_at"])
        remove_rule.assert_called_once_with(
            self.config,
            rule="Bash(git status)",
        )


if __name__ == "__main__":
    unittest.main()
