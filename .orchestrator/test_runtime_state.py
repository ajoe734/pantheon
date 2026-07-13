#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import runtime_state


class LoadRuntimeStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            }
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_load_runtime_state_drops_suspended_worker_without_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-stale": {
                        "run_id": "claude-stale",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-missing",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["workers"], {})

    def test_load_runtime_state_keeps_suspended_worker_with_live_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-live": {
                        "run_id": "claude-live",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-live",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text(
            json.dumps({"event_id": "evt-live", "task_id": "EXEC-FRONT-TW03-001"}) + "\n",
            encoding="utf-8",
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertIn("claude-live", state["workers"])

    def test_load_runtime_state_adds_chair_rotation_defaults(self) -> None:
        self._write_json(self.root / "state.json", {"workers": {}, "queue": {"events": {}}})
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["chair_rotation"]["current_index"], 0)
        self.assertIsNone(state["chair_rotation"]["last_chair_agent"])
        self.assertIn("chair_review", state["supervisor"]["mode_occupancy"])

    def test_load_runtime_state_preserves_watchdog_safe_mode(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "watchdog": {
                    "safe_mode_until": "2026-05-18T14:30:00Z",
                    "safe_mode_reason": "stale_heartbeat",
                },
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["watchdog"]["safe_mode_until"], "2026-05-18T14:30:00Z")
        self.assertEqual(state["watchdog"]["safe_mode_reason"], "stale_heartbeat")
        self.assertIn("last_safe_mode_observed_until", state["watchdog"])

    def test_load_runtime_state_preserves_execution_dispatch_guard_ledger(self) -> None:
        guard = {
            "task_id": "TASK-REVIEW",
            "logical_agent_id": "codex",
            "reason": "review_ready_dispatch",
            "event_key": "dispatcher:Codex:TASK-REVIEW:review_ready_dispatch:signature",
            "signature": "signature",
            "lane_attempt_count": 2,
            "retry_not_before": "2026-07-13T17:10:00Z",
            "triage_required": True,
        }
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "execution_dispatch_guardrails": {
                    "schema_version": 1,
                    "records": {"TASK-REVIEW|codex|review|digest": guard},
                },
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(
            state["execution_dispatch_guardrails"]["records"]["TASK-REVIEW|codex|review|digest"],
            guard,
        )

    def test_load_runtime_state_adds_execution_dispatch_guard_defaults(self) -> None:
        self._write_json(self.root / "state.json", {"workers": {}, "queue": {"events": {}}})
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(
            state["execution_dispatch_guardrails"],
            {"schema_version": 1, "records": {}, "history": []},
        )

    def test_migrate_state_coalesces_legacy_physical_slots_into_schema_v2_logical_signature(self) -> None:
        config = {
            "agents": {
                "codex": {"id": "codex", "provider": "codex"},
                "codex1_1": {"id": "codex1_1", "provider": "codex1-1", "dispatch_slot_for": "codex"},
                "codex1_2": {"id": "codex1_2", "provider": "codex1-2", "dispatch_slot_for": "codex"},
            }
        }
        raw = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "TASK-1:codex1_1": {
                        "task_id": "TASK-1",
                        "provider": "codex1_1",
                        "count": 2,
                        "last_failure_kind": "fatal",
                        "last_reason": " Fatal   runner failure ",
                        "first_failure_at": "2026-07-13T10:00:00Z",
                        "last_failure_at": "2026-07-13T10:05:00Z",
                        "raw_ref": "evidence/one.json",
                        "worker_run_id": "run-one",
                    },
                    "TASK-1:codex1_2": {
                        "task_id": "TASK-1",
                        "provider": "codex1_2",
                        "count": 3,
                        "last_failure_kind": "fatal",
                        "last_reason": "fatal runner failure",
                        "first_failure_at": "2026-07-13T10:01:00Z",
                        "last_failure_at": "2026-07-13T10:06:00Z",
                        "raw_ref": "evidence/two.json",
                        "worker_run_id": "run-two",
                    },
                }
            }
        }

        migrated = runtime_state.migrate_state(raw, config)
        guardrails = migrated["provider_guardrails"]
        self.assertEqual(guardrails["task_failure_streak_schema_version"], 2)
        self.assertEqual(len(guardrails["task_failure_streaks"]), 1)
        key, record = next(iter(guardrails["task_failure_streaks"].items()))
        self.assertTrue(key.startswith("TASK-1|codex|"))
        self.assertEqual(record["logical_agent_id"], "codex")
        self.assertEqual(record["signature_scope"], "legacy_unbound")
        self.assertTrue(record["signature"].startswith("legacy:fatal:"))
        self.assertEqual(record["count"], 5)
        self.assertEqual(record["first_failure_at"], "2026-07-13T10:00:00Z")
        self.assertEqual(record["last_failure_at"], "2026-07-13T10:06:00Z")
        self.assertEqual(record["worker_run_id"], "run-two")
        self.assertEqual(record["evidence_refs"], ["evidence/one.json", "evidence/two.json"])
        self.assertEqual(guardrails["task_failure_streak_aliases"]["TASK-1:codex1_1"], key)
        self.assertEqual(guardrails["task_failure_streak_aliases"]["TASK-1:codex1_2"], key)

        remigrated = runtime_state.migrate_state(migrated, config)
        self.assertEqual(
            remigrated["provider_guardrails"]["task_failure_streaks"],
            guardrails["task_failure_streaks"],
        )
        self.assertEqual(
            remigrated["provider_guardrails"]["task_failure_streak_aliases"],
            guardrails["task_failure_streak_aliases"],
        )

    def test_migrate_state_corrupt_failure_count_and_timestamps_fail_closed(self) -> None:
        raw = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "TASK-CORRUPT:codex": {
                        "task_id": "TASK-CORRUPT",
                        "provider": "codex",
                        "count": "not-an-integer",
                        "last_failure_kind": "fatal",
                        "last_reason": "fatal runner failure",
                        "first_failure_at": "not-a-timestamp",
                        "last_failure_at": {"unexpected": "mapping"},
                    }
                }
            }
        }

        migrated = runtime_state.migrate_state(raw, {"agents": {"codex": {"provider": "codex"}}})
        record = next(iter(migrated["provider_guardrails"]["task_failure_streaks"].values()))

        self.assertEqual(record["count"], 0)
        self.assertIsNone(record["first_failure_at"])
        self.assertIsNone(record["last_failure_at"])

    def test_load_runtime_state_preserves_worker_worktree_cleanup_summary(self) -> None:
        last_run = {
            "at": "2026-06-20T06:59:40Z",
            "source": "worker_lifecycle",
            "checked": 25,
            "removed": 25,
            "archived": 4,
            "failed": 0,
        }
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "worker_worktree_cleanup": {"last_run": last_run},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["worker_worktree_cleanup"]["last_run"], last_run)
