#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

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
                "approval_queue": str(self.root / "approval-queue.json"),
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

    def test_migrate_state_quarantines_legacy_unbound_physical_slot_streaks(self) -> None:
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
        self.assertEqual(guardrails["task_failure_streaks"], {})
        self.assertEqual(guardrails["task_failure_streak_aliases"], {})
        self.assertEqual(len(guardrails["task_failure_streak_quarantine"]), 2)
        self.assertEqual(
            {item["record_key"] for item in guardrails["task_failure_streak_quarantine"]},
            {"TASK-1:codex1_1", "TASK-1:codex1_2"},
        )
        self.assertEqual(
            {item["quarantine_disposition"] for item in guardrails["task_failure_streak_quarantine"]},
            {"legacy_unbound"},
        )

        remigrated = runtime_state.migrate_state(migrated, config)
        self.assertEqual(
            remigrated["provider_guardrails"]["task_failure_streaks"],
            guardrails["task_failure_streaks"],
        )
        self.assertEqual(
            remigrated["provider_guardrails"]["task_failure_streak_quarantine"],
            guardrails["task_failure_streak_quarantine"],
        )

    def test_migrate_state_corrupt_failure_count_and_timestamps_fail_closed(self) -> None:
        raw = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "TASK-CORRUPT:codex": {
                        "schema_version": 2,
                        "task_id": "TASK-CORRUPT",
                        "provider": "codex",
                        "logical_agent_id": "codex",
                        "signature": "dispatch:fatal:reason:task",
                        "signature_scope": "exact_dispatch",
                        "dispatch_task_signature": "task-signature",
                        "dispatch_event_key": "event-key",
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
        guardrails = migrated["provider_guardrails"]
        self.assertEqual(guardrails["task_failure_streaks"], {})
        self.assertEqual(len(guardrails["task_failure_streak_quarantine"]), 1)
        self.assertEqual(
            guardrails["task_failure_streak_quarantine"][0]["quarantine_disposition"],
            "invalid_failure_count",
        )

    def test_migrate_state_preserves_only_bound_current_failure_streaks(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        observed = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        future = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        valid = {
            "schema_version": 2,
            "task_id": "TASK-VALID",
            "logical_agent_id": "codex",
            "provider": "codex",
            "signature": "dispatch:fatal:reason:task",
            "signature_scope": "exact_dispatch",
            "dispatch_task_signature": "task-signature",
            "dispatch_event_key": "event-key",
            "count": 2,
            "first_failure_at": observed,
            "last_failure_at": observed,
        }
        raw = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "valid": valid,
                    "future": {**valid, "task_id": "TASK-FUTURE", "last_failure_at": future},
                    "unbound": {
                        **valid,
                        "task_id": "TASK-UNBOUND",
                        "signature_scope": "runtime_unbound",
                        "dispatch_task_signature": None,
                        "dispatch_event_key": None,
                    },
                    "future-schema": {**valid, "task_id": "TASK-SCHEMA", "schema_version": 99},
                }
            }
        }

        migrated = runtime_state.migrate_state(raw, {"agents": {"codex": {"provider": "codex"}}})
        guardrails = migrated["provider_guardrails"]

        self.assertEqual(len(guardrails["task_failure_streaks"]), 1)
        self.assertEqual(next(iter(guardrails["task_failure_streaks"].values()))["task_id"], "TASK-VALID")
        self.assertEqual(
            {item["quarantine_disposition"] for item in guardrails["task_failure_streak_quarantine"]},
            {"future_failure_time", "unbound_dispatch_signature", "future_schema_version"},
        )

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

    def test_runtime_state_lock_is_reentrant_and_serializes_other_threads(self) -> None:
        acquired = threading.Event()

        def contender() -> None:
            with runtime_state.runtime_state_lock(self.config):
                acquired.set()

        with runtime_state.runtime_state_lock(self.config):
            with runtime_state.runtime_state_lock(self.config):
                thread = threading.Thread(target=contender, daemon=True)
                thread.start()
                time.sleep(0.05)
                self.assertFalse(acquired.is_set())

        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())

    def test_canonical_task_lock_uses_stable_sidecar_and_is_reentrant(self) -> None:
        status_file = self.root / "ai-status.json"
        expected = self.root / ".orchestrator" / "task-state.lock"

        with runtime_state.canonical_task_state_lock_file(status_file):
            with runtime_state.canonical_task_state_lock_file(status_file):
                self.assertTrue(expected.exists())

        self.assertEqual(runtime_state.canonical_task_state_lock_path(status_file), expected)

    def test_runtime_admission_fails_closed_for_queued_running_or_admitted_task(self) -> None:
        queued_events = [{"event_id": "evt-1", "task_id": "TASK-1"}]
        state = runtime_state.default_state()
        state["queue"]["events"] = {"evt-1": {"status": "queued"}}
        state["workers"] = {
            "run-active": {
                "run_id": "run-active",
                "task_id": "TASK-1",
                "status": "running",
                "execution_admission": {"event_key": "event-key"},
            }
        }

        decision = runtime_state.inspect_task_runtime_admission(state, queued_events, "TASK-1")

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["queue_event_ids"], ["evt-1"])
        self.assertEqual(decision["worker_run_ids"], ["run-active"])
        self.assertEqual(decision["admitted_run_ids"], ["run-active"])

    def test_runtime_admission_guard_holds_snapshot_and_allows_clear_task(self) -> None:
        self._write_json(self.root / "state.json", runtime_state.default_state())
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        with runtime_state.task_runtime_admission_guard(self.config, "TASK-CLEAR") as decision:
            self.assertTrue(decision["allowed"])
            self.assertEqual(decision["reason"], "runtime_clear")

    def test_runtime_admission_guard_blocks_parallel_enqueue(self) -> None:
        self._write_json(self.root / "state.json", runtime_state.default_state())
        event_path = self.root / "event-queue.jsonl"
        event_path.write_text("", encoding="utf-8")
        enqueued = threading.Event()

        def producer() -> None:
            runtime_state.enqueue_event(
                self.config,
                {"event_id": "evt-parallel", "task_id": "TASK-PARALLEL"},
            )
            enqueued.set()

        with runtime_state.task_runtime_admission_guard(self.config, "TASK-CLEAR") as decision:
            self.assertTrue(decision["allowed"])
            thread = threading.Thread(target=producer, daemon=True)
            thread.start()
            time.sleep(0.05)
            self.assertFalse(enqueued.is_set())
            self.assertEqual(event_path.read_text(encoding="utf-8"), "")

        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())
        self.assertTrue(enqueued.is_set())
        self.assertIn("evt-parallel", event_path.read_text(encoding="utf-8"))

    def test_multi_task_runtime_admission_uses_one_snapshot_for_queue_worker_and_approval(self) -> None:
        state = runtime_state.default_state()
        state["queue"]["events"] = {
            "evt-queue": {"status": "queued"},
        }
        state["workers"] = {
            "run-worker": {
                "run_id": "run-worker",
                "task_id": "TASK-WORKER",
                "status": "running",
            }
        }
        self._write_json(self.root / "state.json", state)
        (self.root / "event-queue.jsonl").write_text(
            json.dumps({"event_id": "evt-queue", "task_id": "TASK-QUEUE"}) + "\n",
            encoding="utf-8",
        )
        approval_state = runtime_state.default_approval_state()
        approval_state["pending"] = [
            {
                "approval_id": "approval-busy",
                "task_id": "TASK-APPROVAL",
                "status": "pending",
            }
        ]
        self._write_json(self.root / "approval-queue.json", approval_state)

        with runtime_state.tasks_runtime_admission_guard(
            self.config,
            ["TASK-CLEAR", "TASK-QUEUE", "TASK-WORKER", "TASK-APPROVAL"],
        ) as aggregate:
            self.assertFalse(aggregate["allowed"])
            self.assertEqual(
                aggregate["busy_task_ids"],
                ["TASK-QUEUE", "TASK-WORKER", "TASK-APPROVAL"],
            )
            decisions = aggregate["decisions"]
            self.assertTrue(decisions["TASK-CLEAR"]["allowed"])
            self.assertEqual(decisions["TASK-QUEUE"]["queue_event_ids"], ["evt-queue"])
            self.assertEqual(decisions["TASK-WORKER"]["worker_run_ids"], ["run-worker"])
            self.assertEqual(
                decisions["TASK-APPROVAL"]["pending_approval_ids"],
                ["approval-busy"],
            )

    def test_multi_task_strict_malformed_snapshot_fails_all_targets_from_one_read(self) -> None:
        self._write_json(self.root / "state.json", runtime_state.default_state())
        (self.root / "event-queue.jsonl").write_text(
            '{"event_id":"evt-other","task_id":"OTHER"}\n',
            encoding="utf-8",
        )
        (self.root / "approval-queue.json").write_text("{not-json", encoding="utf-8")

        with mock.patch.object(
            runtime_state,
            "strict_runtime_admission_snapshot",
            wraps=runtime_state.strict_runtime_admission_snapshot,
        ) as snapshot:
            with runtime_state.tasks_runtime_admission_guard(
                self.config,
                ["TASK-A", "TASK-B"],
                strict=True,
            ) as aggregate:
                self.assertFalse(aggregate["allowed"])
                self.assertEqual(aggregate["busy_task_ids"], ["TASK-A", "TASK-B"])
                for decision in aggregate["decisions"].values():
                    self.assertFalse(decision["allowed"])
                    self.assertIn("approval_queue_file_malformed", decision["reason"])
        snapshot.assert_called_once_with(self.config)

    def test_multi_task_guard_holds_runtime_lock_across_context_and_blocks_producer(self) -> None:
        self._write_json(self.root / "state.json", runtime_state.default_state())
        event_path = self.root / "event-queue.jsonl"
        event_path.write_text("", encoding="utf-8")
        self._write_json(self.root / "approval-queue.json", runtime_state.default_approval_state())
        produced = threading.Event()

        def producer() -> None:
            runtime_state.enqueue_event(
                self.config,
                {"event_id": "evt-after-multi", "task_id": "TASK-A"},
            )
            produced.set()

        with runtime_state.tasks_runtime_admission_guard(
            self.config,
            ["TASK-A", "TASK-B"],
        ) as aggregate:
            self.assertTrue(aggregate["allowed"])
            thread = threading.Thread(target=producer, daemon=True)
            thread.start()
            self.assertFalse(produced.wait(timeout=0.1))
            self.assertEqual(event_path.read_text(encoding="utf-8"), "")

        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(produced.is_set())
        self.assertIn("evt-after-multi", event_path.read_text(encoding="utf-8"))

    def test_multi_task_guard_preserves_runtime_then_canonical_lock_order(self) -> None:
        self._write_json(self.root / "state.json", runtime_state.default_state())
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self._write_json(self.root / "approval-queue.json", runtime_state.default_approval_state())
        observed: list[str] = []

        @contextmanager
        def observed_runtime_lock(_config):
            observed.append("runtime_enter")
            try:
                yield
            finally:
                observed.append("runtime_exit")

        @contextmanager
        def observed_task_lock(_status_file):
            observed.append("task_enter")
            try:
                yield
            finally:
                observed.append("task_exit")

        with (
            mock.patch.object(runtime_state, "runtime_state_lock", side_effect=observed_runtime_lock),
            mock.patch.object(
                runtime_state,
                "canonical_task_state_lock_file",
                side_effect=observed_task_lock,
            ),
        ):
            with runtime_state.tasks_runtime_admission_guard(
                self.config,
                ["TASK-A", "TASK-B"],
            ) as aggregate:
                self.assertTrue(aggregate["allowed"])
                with runtime_state.canonical_task_state_lock_file(self.root / "ai-status.json"):
                    observed.append("mutation")

        self.assertEqual(
            observed,
            ["runtime_enter", "task_enter", "mutation", "task_exit", "runtime_exit"],
        )

    def test_multi_task_guard_rejects_empty_entries_and_duplicates(self) -> None:
        cases = (
            ([], "task_ids_empty"),
            ([""], "task_ids_empty"),
            (["TASK-A", ""], "task_ids_contain_empty"),
            (["TASK-A", "TASK-A"], "task_ids_duplicate"),
        )
        for task_ids, expected_reason in cases:
            with self.subTest(task_ids=task_ids):
                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    task_ids,
                ) as aggregate:
                    self.assertFalse(aggregate["allowed"])
                    self.assertEqual(aggregate["reason"], expected_reason)
                    self.assertTrue(
                        all(not decision["allowed"] for decision in aggregate["decisions"].values())
                    )

        with runtime_state.task_runtime_admission_guard(self.config, "") as decision:
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["reason"], "task_id_missing")

    def test_strict_runtime_admission_rejects_missing_empty_and_malformed_files(self) -> None:
        state_path = self.root / "state.json"
        event_path = self.root / "event-queue.jsonl"
        approval_path = self.root / "approval-queue.json"
        valid_state = json.dumps(runtime_state.default_state())
        valid_event = '{"event_id":"evt-1","task_id":"OTHER"}\n'
        valid_approval = json.dumps(runtime_state.default_approval_state())

        cases = (
            ("missing", None, None, valid_approval, "state_file_missing"),
            ("empty-state", "", valid_event, valid_approval, "state_file_empty"),
            ("malformed-state", "{not-json", valid_event, valid_approval, "state_file_malformed"),
            ("empty-events", valid_state, "", valid_approval, "event_queue_file_empty"),
            ("malformed-events", valid_state, "{not-json\n", valid_approval, "event_queue_file_malformed"),
            ("missing-approvals", valid_state, valid_event, None, "approval_queue_file_missing"),
            ("empty-approvals", valid_state, valid_event, "", "approval_queue_file_empty"),
            ("malformed-approvals", valid_state, valid_event, "{not-json", "approval_queue_file_malformed"),
            (
                "invalid-approval-schema",
                valid_state,
                valid_event,
                json.dumps({"version": 2, "pending": {}, "history": []}),
                "approval_queue_schema_invalid",
            ),
        )
        for label, state_body, event_body, approval_body, expected in cases:
            with self.subTest(label=label):
                state_path.unlink(missing_ok=True)
                event_path.unlink(missing_ok=True)
                approval_path.unlink(missing_ok=True)
                if state_body is not None:
                    state_path.write_text(state_body, encoding="utf-8")
                if event_body is not None:
                    event_path.write_text(event_body, encoding="utf-8")
                if approval_body is not None:
                    approval_path.write_text(approval_body, encoding="utf-8")

                with runtime_state.task_runtime_admission_guard(
                    self.config,
                    "TASK-CLEAR",
                    strict=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertIn(expected, decision["reason"])

    def test_strict_runtime_admission_blocks_pending_approval_task_without_worker(self) -> None:
        self._write_json(self.root / "state.json", runtime_state.default_state())
        (self.root / "event-queue.jsonl").write_text(
            '{"event_id":"evt-other","task_id":"OTHER"}\n',
            encoding="utf-8",
        )
        approval_state = runtime_state.default_approval_state()
        approval_state["pending"] = [
            {
                "approval_id": "approval-target",
                "task_id": "TASK-PENDING-APPROVAL",
                "worker_run_id": "run-already-gone",
                "status": "pending",
            }
        ]
        self._write_json(self.root / "approval-queue.json", approval_state)

        with runtime_state.task_runtime_admission_guard(
            self.config,
            "TASK-PENDING-APPROVAL",
            strict=True,
        ) as decision:
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["pending_approval_ids"], ["approval-target"])
            self.assertEqual(
                decision["reason"],
                "task_queued_running_admitted_or_pending_approval",
            )

    def test_live_config_opt_in_enables_strict_runtime_admission(self) -> None:
        config = {
            **self.config,
            "supervisor": {"strict_task_runtime_admission": True},
        }
        self._write_json(self.root / "state.json", runtime_state.default_state())
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self._write_json(self.root / "approval-queue.json", runtime_state.default_approval_state())

        with runtime_state.task_runtime_admission_guard(config, "TASK-CLEAR") as decision:
            self.assertFalse(decision["allowed"])
            self.assertIn("strict_runtime_invalid:event_queue_file_empty", decision["reason"])
