#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import watch_events


class WatcherBookkeepingTests(unittest.TestCase):
    def test_wakeup_message_pins_status_command_to_canonical_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "canonical status"
            root.mkdir()
            template = Path(tmpdir) / "wake.txt"
            template.write_text(
                "PANTHEON_STATUS_ROOT={{status_root_shell}} AI_NAME={{target_agent_shell}} "
                "python3 scripts/ai_status.py show {{task_id_shell}}\n",
                encoding="utf-8",
            )
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "codex": {
                        "id": "codex",
                        "display_name": "Codex Review",
                        "provider": "codex",
                        "wake_template": str(template),
                    }
                },
            }

            message = watch_events.render_wakeup_message(
                config,
                {"task_id": "TASK REVIEW", "task": {}},
                "codex",
            )

        self.assertEqual(
            message,
            f"PANTHEON_STATUS_ROOT='{root}' AI_NAME='Codex Review' "
            "python3 scripts/ai_status.py show 'TASK REVIEW'\n",
        )

    def test_queue_delivery_event_persists_dispatch_signature_in_request_metadata(self) -> None:
        config = {
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
        event = {
            "key": "dispatcher:Codex:T-1:review_ready_dispatch:signature",
            "signature": "{\"task_id\":\"T-1\"}",
            "task_id": "T-1",
            "target_agent": "Codex",
            "reason": "review_ready_dispatch",
            "task": {"id": "T-1", "artifacts": ["services/example.py"]},
        }

        with (
            mock.patch.object(watch_events, "execution_context_files", return_value=["AI_COLLABORATION_GUIDE.md"]),
            mock.patch.object(watch_events, "render_wakeup_message", return_value="wake"),
            mock.patch.object(watch_events, "enqueue_event") as enqueue_event,
            mock.patch.object(watch_events, "write_activity_log"),
        ):
            queued = watch_events.queue_delivery_event(config, event)

        self.assertTrue(queued)
        payload = enqueue_event.call_args.args[1]
        self.assertEqual(payload["event_key"], event["key"])
        self.assertEqual(payload["metadata"]["dispatch_event_key"], event["key"])
        self.assertEqual(payload["metadata"]["dispatch_task_signature"], event["signature"])
        self.assertEqual(payload["metadata"]["dispatch_target_display_name"], "Codex")

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


if __name__ == "__main__":
    unittest.main()
