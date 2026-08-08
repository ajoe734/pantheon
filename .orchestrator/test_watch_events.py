#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

import watch_events


class WatcherBookkeepingTests(unittest.TestCase):
    def test_trim_seen_events_normalizes_legacy_null_values(self) -> None:
        state = {
            "seen_event_keys": {
                "chair:codex:chair_review:operational_review:2026-07-19T22:29:09Z": None,
                "task:already-string": "2026-07-20T00:00:00Z",
            }
        }

        watch_events.trim_seen_events(state, 2000)

        self.assertEqual(
            state["seen_event_keys"]["chair:codex:chair_review:operational_review:2026-07-19T22:29:09Z"],
            "2026-07-19T22:29:09Z",
        )
        self.assertEqual(state["seen_event_keys"]["task:already-string"], "2026-07-20T00:00:00Z")

    def test_trim_seen_events_sorts_legacy_null_values_when_pruning(self) -> None:
        state = {
            "seen_event_keys": {
                "chair:codex:chair_review:operational_review:2026-07-19T22:29:09Z": None,
                "chair:codex:chair_review:operational_review:2026-07-20T22:29:09Z": None,
                "task:fresh": "2026-07-21T00:00:00Z",
            }
        }

        watch_events.trim_seen_events(state, 2)

        self.assertEqual(
            state["seen_event_keys"],
            {
                "chair:codex:chair_review:operational_review:2026-07-20T22:29:09Z": "2026-07-20T22:29:09Z",
                "task:fresh": "2026-07-21T00:00:00Z",
            },
        )

    def test_main_uses_locked_scan_without_reentering_runtime_lock(self) -> None:
        config = {
            "paths": {"provider_capabilities": "/tmp/provider-capabilities.json"},
            "watcher": {"poll_interval_seconds": 2.0},
        }
        state: dict[str, object] = {}
        with (
            mock.patch.object(sys, "argv", ["watch_events.py", "--once"]),
            mock.patch.object(watch_events, "load_config", return_value=config),
            mock.patch.object(watch_events, "config_path", return_value=Path("/tmp/provider-capabilities.json")),
            mock.patch.object(watch_events, "load_json", return_value={}),
            mock.patch.object(watch_events, "runtime_state_lock", return_value=nullcontext()) as runtime_lock,
            mock.patch.object(watch_events, "load_runtime_state", return_value=state),
            mock.patch.object(watch_events, "run_scan", side_effect=AssertionError("main must not re-enter runtime lock")),
            mock.patch.object(watch_events, "_run_scan_locked", return_value=False) as locked_scan,
        ):
            self.assertEqual(watch_events.main(), 0)

        runtime_lock.assert_called_once()
        locked_scan.assert_called_once_with(config, state, replay=False, provider_capabilities={})

    def test_run_scan_updates_snapshot_without_queueing_when_runtime_enqueue_disabled(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
                "handoffs_path": "handoffs",
            },
            "events": {
                "enqueue_runtime_events": False,
                "review_statuses": ["review"],
                "pending_handoff_statuses": ["pending"],
            },
            "watcher": {"max_seen_events": 2000},
        }
        state = {
            "initialized_at": "2026-04-06T09:00:00Z",
            "last_scan_at": "2026-04-06T09:00:00Z",
            "tasks": {
                "P3-001": {
                    "id": "P3-001",
                    "status": "in_progress",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            },
            "pending_handoff_keys": [],
            "seen_event_keys": {},
        }
        status = {
            "tasks": [
                {
                    "id": "P3-001",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            ],
            "handoffs": [],
        }

        with (
            mock.patch.object(watch_events, "load_status", return_value=status),
            mock.patch.object(watch_events, "recent_terminal_summaries", return_value=[{"task_id": "OPS-001"}]),
            mock.patch.object(watch_events, "queue_delivery_event", side_effect=AssertionError("watcher should not queue runtime events")),
            mock.patch.object(watch_events, "save_runtime_state"),
        ):
            changed = watch_events.run_scan(config, state, replay=False, provider_capabilities={})

        self.assertTrue(changed)
        self.assertEqual(state["tasks"]["P3-001"]["status"], "review")
        self.assertEqual(state["recent_terminal_tasks"], [{"task_id": "OPS-001"}])
        self.assertEqual(state["pending_handoff_keys"], [])
        self.assertIsNotNone(state["last_scan_at"])


class WakeupMessageRoleGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "agents": {
                "antigravity": {
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "adapter": "local_cli",
                }
            },
            "branch_workflow": {"dev_branch": "dev", "task_branch_prefix": "task/"},
        }
        self.event = {
            "task_id": "AG-WS-OPS-002",
            "target_agent": "Antigravity",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {
                "id": "AG-WS-OPS-002",
                "status": "review",
                "owner": "Claude",
                "reviewer": "Antigravity",
                "artifacts": ["docs/task.md"],
            },
        }

    def test_review_dispatch_forbids_ownership_and_closeout_mutations(self) -> None:
        self.event["reason"] = "review_ready_dispatch"

        message = watch_events.render_wakeup_message(self.config, self.event, "Antigravity")

        self.assertIn("角色是 reviewer，不是 task owner", message)
        self.assertIn("不得執行 `assign`、`start`、`progress`、`handoff`、`done`", message)
        self.assertIn("ai-status.sh approve AG-WS-OPS-002", message)
        self.assertIn("ai-status.sh reopen AG-WS-OPS-002", message)
        self.assertIn("owner closeout 由 supervisor 另行 dispatch", message)
        self.assertNotIn("{{dispatch_guardrails}}", message)

    def test_finalize_dispatch_identifies_owner_without_reassignment(self) -> None:
        self.event["reason"] = "owned_finalize_dispatch"

        message = watch_events.render_wakeup_message(self.config, self.event, "Antigravity")

        self.assertIn("角色是已通過審查後的 task owner", message)
        self.assertIn("不得重新指派 owner/reviewer", message)
        self.assertIn("才執行 `done`", message)
        self.assertNotIn("角色是 reviewer，不是 task owner", message)


class WatcherQueueTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.queue_path = self.root / "event-queue.jsonl"
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.queue_path),
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
            },
            "agents": {
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "file_inbox",
                }
            },
            "providers": {"codex": {"delivery_mode": "file_inbox"}},
        }
        self.event = {
            "key": "TASK-001:status:in_progress:codex",
            "task_id": "TASK-001",
            "target_agent": "Codex",
            "reason": "status:in_progress",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"id": "TASK-001", "artifacts": []},
        }

    def test_public_queue_append_holds_runtime_sidecar_and_reads_back_exact_event(self) -> None:
        real_append = watch_events._append_runtime_event_locked
        lock_path = self.root / ".orchestrator" / "runtime-admission.lock"

        def assert_locked_append(config: dict, payload: dict) -> None:
            probe = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
            real_append(config, payload)

        with (
            mock.patch.object(watch_events, "render_wakeup_message", return_value="wake\n"),
            mock.patch.object(watch_events, "write_activity_log"),
            mock.patch.object(watch_events, "_append_runtime_event_locked", side_effect=assert_locked_append),
        ):
            self.assertTrue(watch_events.queue_delivery_event(self.config, self.event))

        rows = [json.loads(line) for line in self.queue_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_id"], "TASK-001")
        self.assertEqual(rows[0]["message"], "wake\n")

    def test_queue_append_rejects_symlink_leaf_without_touching_target(self) -> None:
        target = self.root / "outside.jsonl"
        target.write_text('{"operator": true}\n', encoding="utf-8")
        self.queue_path.symlink_to(target)

        with (
            mock.patch.object(watch_events, "render_wakeup_message", return_value="wake\n"),
            mock.patch.object(watch_events, "write_activity_log"),
        ):
            with self.assertRaisesRegex(RuntimeError, "data leaf cannot be a symlink"):
                watch_events.queue_delivery_event(self.config, self.event)

        self.assertTrue(self.queue_path.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), '{"operator": true}\n')

    def test_queue_append_fails_closed_on_readback_mismatch(self) -> None:
        with mock.patch.object(watch_events.os, "pread", return_value=b"corrupt"):
            with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
                watch_events._append_runtime_event_locked(self.config, {"event_id": "evt-1"})


if __name__ == "__main__":
    unittest.main()
