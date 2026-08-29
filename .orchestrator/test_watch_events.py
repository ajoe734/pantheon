#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import watch_events
import runtime_state


class DeliveryQueueBookkeepingTests(unittest.TestCase):
    def test_trim_seen_events_discards_invalid_legacy_values(self) -> None:
        state = {
            "seen_event_keys": {
                "legacy-null": None,
                "legacy-number": 3,
                "task:old": "2026-07-20T00:00:00Z",
                "task:fresh": "2026-07-21T00:00:00Z",
            }
        }
        watch_events.trim_seen_events(state, 1)
        self.assertEqual(
            state["seen_event_keys"],
            {"task:fresh": "2026-07-21T00:00:00Z"},
        )


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

    def test_review_dispatch_forbids_owner_mutations(self) -> None:
        self.event["reason"] = "review_ready_dispatch"
        message = watch_events.render_wakeup_message(
            self.config,
            self.event,
            "Antigravity",
        )
        self.assertIn("角色是 reviewer，不是 task owner", message)
        self.assertIn("不得執行 `assign`、`start`、`progress`、`handoff`、`done`", message)
        self.assertIn("ai-status.sh approve AG-WS-OPS-002", message)
        self.assertIn("ai-status.sh reopen AG-WS-OPS-002", message)
        self.assertIn("唯一權威判定", message)
        self.assertIn("不得只因 current dev 在線性前進後顯示 BEHIND 就 reopen", message)
        self.assertIn("衝突、head/branch/manifest 已變，或 base 非線性倒退／分歧", message)

    def test_finalize_dispatch_identifies_owner(self) -> None:
        self.event["reason"] = "owned_finalize_dispatch"
        message = watch_events.render_wakeup_message(
            self.config,
            self.event,
            "Antigravity",
        )
        self.assertIn("角色是已通過審查後的 task owner", message)
        self.assertIn("不得重新指派 owner/reviewer", message)
        self.assertIn("不得重跑 reviewer 已完成並綁定的測試", message)
        self.assertIn("只核對 approval、merged ancestry 與乾淨工作樹後收尾", message)
        self.assertIn("exact-head approval", message)

    def test_wakeup_exposes_dependency_truth_and_blocker_contract(self) -> None:
        self.event["reason"] = "owned_in_progress_dispatch"
        self.event["task"]["depends_on"] = ["DEP-001"]
        self.event["task"]["dependency_truth"] = [
            {"task_id": "DEP-001", "status": "done", "satisfied": True}
        ]

        message = watch_events.render_wakeup_message(
            self.config,
            self.event,
            "Antigravity",
        )

        self.assertIn("DEP-001: status=done, satisfied=true", message)
        self.assertIn("task_dependency <TASK-ID>", message)
        self.assertIn("最後加 `external`", message)

    def test_anchor_subject_is_bounded_for_long_task_id(self) -> None:
        long_id = (
            "SUP-WORKER-SUBJECT-GUARD-20260811-EXTREMELY-LONG-TASK-ID-"
            "THAT-EXCEEDS-LIMITS"
        )
        event = {
            **self.event,
            "reason": "owned_ready_dispatch",
            "task_id": long_id,
            "task": {**self.event["task"], "id": long_id},
        }
        message = watch_events.render_wakeup_message(
            self.config,
            event,
            "Antigravity",
        )
        match = __import__("re").search(r"Anchor commit subject 建議：`(.*?)`；", message)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertLessEqual(len(match.group(1)), 72)

    def test_noncanonical_reason_is_rejected(self) -> None:
        self.event["reason"] = "github_retry"
        with self.assertRaisesRegex(ValueError, "unsupported delivery reason"):
            watch_events.render_wakeup_message(
                self.config,
                self.event,
                "Antigravity",
            )


class DeliveryQueueTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                }
            },
            "providers": {"codex": {"delivery_mode": "codex"}},
        }
        self.event = {
            "key": "planner:TASK-001:owned_ready_dispatch",
            "task_id": "TASK-001",
            "task_generation": 3,
            "target_agent": "Codex",
            "reason": "owned_ready_dispatch",
            "context_files": ["AI_COLLABORATION_GUIDE.md"],
            "task": {"id": "TASK-001", "generation": 3, "artifacts": []},
        }

    def test_queue_persists_exact_task_generation(self) -> None:
        state = runtime_state.default_state()
        with (
            mock.patch.object(watch_events, "render_wakeup_message", return_value="wake\n"),
            mock.patch.object(watch_events, "write_activity_log"),
        ):
            self.assertTrue(
                watch_events._queue_delivery_event_locked(self.config, state, self.event)
            )
        stored = runtime_state.queue_events(state)
        self.assertEqual(stored[0]["task_generation"], 3)
        self.assertEqual(stored[0]["metadata"]["task"]["generation"], 3)

    def test_nonplanner_reason_is_not_appended(self) -> None:
        event = {**self.event, "reason": "manual_test"}
        state = runtime_state.default_state()
        self.assertFalse(watch_events._queue_delivery_event_locked(self.config, state, event))
        self.assertEqual(runtime_state.queue_events(state), [])

    def test_queue_write_does_not_touch_retired_external_queue_file(self) -> None:
        target = self.root / "outside.jsonl"
        target.write_text('{"operator": true}\n', encoding="utf-8")
        retired_queue_path = self.root / "event-queue.jsonl"
        retired_queue_path.symlink_to(target)
        state = runtime_state.default_state()
        with (
            mock.patch.object(watch_events, "render_wakeup_message", return_value="wake\n"),
            mock.patch.object(watch_events, "write_activity_log"),
        ):
            self.assertTrue(watch_events._queue_delivery_event_locked(self.config, state, self.event))
        self.assertEqual(target.read_text(encoding="utf-8"), '{"operator": true}\n')


if __name__ == "__main__":
    unittest.main()
