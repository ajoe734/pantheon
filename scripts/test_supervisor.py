#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys


ROOT = Path(__file__).resolve().parents[1]
ORCH_DIR = ROOT / ".orchestrator"
if str(ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(ORCH_DIR))

import runtime_state
import supervisor


class SupervisorQuotaGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="supervisor-guardrails-")
        self.root = Path(self.temp_dir.name)
        orch = self.root / ".orchestrator"
        orch.mkdir(parents=True, exist_ok=True)
        (orch / "logs").mkdir(parents=True, exist_ok=True)
        self.config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "state_file": str(orch / "state.json"),
                "event_queue": str(orch / "event-queue.jsonl"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
                "approval_queue": str(orch / "approval-queue.json"),
                "provider_capabilities": str(orch / "provider_capabilities.json"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
                "handoffs_path": "handoffs",
            },
            "agents": {
                "qwen": {"id": "qwen", "name": "Qwen", "display_name": "Qwen", "provider": "qwen"},
                "copilot": {"id": "copilot", "name": "Copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "name": "Codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "name": "Claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {
                "qwen": {},
                "copilot": {},
                "codex": {},
                "claude": {},
            },
            "supervisor": {
                "auto_refresh_provider_capabilities": False,
                "stall_after_seconds": 300,
            },
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "eligible_statuses": ["todo", "in_progress", "review"],
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                    "Copilot": ["Codex", "Claude", "Qwen"],
                    "Codex": ["Qwen", "Claude", "Copilot"],
                    "Claude": ["Codex", "Qwen", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                    "Copilot": ["Codex", "Claude", "Qwen"],
                    "Codex": ["Qwen", "Claude", "Copilot"],
                    "Claude": ["Codex", "Qwen", "Copilot"],
                },
            },
            "provider_guardrails": {
                "pause_on_capacity_failure": True,
                "capacity_pause_seconds": 3600,
                "generic_exit_reassign_after": 2,
            },
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _task(self, *, owner: str, reviewer: str, status: str = "in_progress") -> dict:
        return {
            "id": "TASK-001",
            "title": "Guardrail regression task",
            "phase": "Wave X",
            "owner": owner,
            "reviewer": reviewer,
            "status": status,
            "depends_on": [],
            "artifacts": [],
            "next": "Keep moving",
            "last_update": "2026-04-11T00:00:00Z",
        }

    def test_detect_worker_failure_matches_qwen_and_copilot_terminal_quota_variants(self) -> None:
        qwen_log = self.root / ".orchestrator" / "logs" / "qwen.log"
        qwen_log.write_text(
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Qwen OAuth quota exceeded: Your free daily quota has been reached."}]}}\n',
            encoding="utf-8",
        )
        qwen_worker = {"provider": "qwen", "log_path": str(qwen_log)}
        qwen_reason = supervisor.detect_worker_failure(qwen_worker)
        self.assertIsNotNone(qwen_reason)
        self.assertEqual(
            supervisor.classify_worker_failure(self.config, qwen_worker, qwen_reason)["kind"],
            "quota_terminal",
        )

        copilot_log = self.root / ".orchestrator" / "logs" / "copilot.log"
        copilot_log.write_text("402 You have no quota (Request ID: abc)\n", encoding="utf-8")
        copilot_worker = {"provider": "copilot", "log_path": str(copilot_log)}
        copilot_reason = supervisor.detect_worker_failure(copilot_worker)
        self.assertIsNotNone(copilot_reason)
        self.assertEqual(
            supervisor.classify_worker_failure(self.config, copilot_worker, copilot_reason)["kind"],
            "quota_terminal",
        )

    def test_poll_workers_pauses_provider_and_reassigns_capacity_failure(self) -> None:
        log_path = self.root / ".orchestrator" / "logs" / "qwen-capacity.log"
        log_path.write_text(
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Qwen OAuth quota exceeded: Your free daily quota has been reached."}]}}\n',
            encoding="utf-8",
        )
        state = runtime_state.default_state()
        state["queue"]["events"] = {"evt-1": {"status": "started", "attempt_count": 1}}
        state["workers"] = {
            "run-1": {
                "run_id": "run-1",
                "provider": "qwen",
                "agent_id": "qwen",
                "task_id": "TASK-001",
                "status": "running",
                "pid": None,
                "queue_event_id": "evt-1",
                "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                "log_path": str(log_path),
                "last_event_at": "2026-04-11T14:37:23Z",
                "retry_count": 0,
            }
        }
        status = {"tasks": [self._task(owner="Qwen", reviewer="Claude", status="in_progress")], "handoffs": []}

        with mock.patch.object(supervisor, "load_status", return_value=status), \
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}), \
            mock.patch.object(supervisor, "load_provider_report", return_value={}), \
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "reassigned")
        self.assertEqual(state["workers"]["run-1"]["reassigned_to"], "Codex")
        self.assertIn("qwen", state["provider_guardrails"]["dispatch_pauses"])

    def test_poll_workers_reassigns_after_repeated_generic_exit(self) -> None:
        state = runtime_state.default_state()
        state["queue"]["events"] = {"evt-1": {"status": "started", "attempt_count": 2}}
        state["provider_guardrails"]["task_failure_streaks"] = {
            "TASK-001:qwen": {
                "task_id": "TASK-001",
                "provider": "qwen",
                "count": 1,
                "last_reason": supervisor.GENERIC_WORKER_EXIT_REASON,
                "last_failure_at": "2026-04-11T14:30:00Z",
                "last_failure_kind": "generic_exit",
            }
        }
        state["workers"] = {
            "run-2": {
                "run_id": "run-2",
                "provider": "qwen",
                "agent_id": "qwen",
                "task_id": "TASK-001",
                "status": "running",
                "pid": None,
                "queue_event_id": "evt-1",
                "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                "log_path": str(self.root / ".orchestrator" / "logs" / "generic-exit.log"),
                "last_event_at": "2026-04-11T14:37:23Z",
                "retry_count": 0,
            }
        }
        status = {"tasks": [self._task(owner="Qwen", reviewer="Claude", status="in_progress")], "handoffs": []}

        with mock.patch.object(supervisor, "load_status", return_value=status), \
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}), \
            mock.patch.object(supervisor, "load_provider_report", return_value={}), \
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True), \
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-2"]["status"], "reassigned")
        self.assertEqual(state["workers"]["run-2"]["reassigned_to"], "Codex")
        self.assertNotIn("TASK-001:qwen", state["provider_guardrails"]["task_failure_streaks"])

    def test_dispatch_ready_tasks_skips_paused_provider(self) -> None:
        state = runtime_state.default_state()
        state["provider_guardrails"]["dispatch_pauses"] = {
            "qwen": {
                "provider": "qwen",
                "paused_at": "2026-04-11T14:37:23Z",
                "blocked_until": "2099-04-11T15:37:23Z",
                "reason": "Qwen OAuth quota exceeded",
            }
        }
        status = {"tasks": [self._task(owner="Qwen", reviewer="Claude", status="todo")], "handoffs": []}

        with mock.patch.object(supervisor, "load_status", return_value=status), \
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event:
            supervisor.dispatch_ready_tasks(self.config, state)

        queue_delivery_event.assert_not_called()


class SupervisorRuntimeStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="supervisor-runtime-")
        self.root = Path(self.temp_dir.name)
        orch = self.root / ".orchestrator"
        orch.mkdir(parents=True, exist_ok=True)
        self.config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "state_file": str(orch / "state.json"),
                "event_queue": str(orch / "event-queue.jsonl"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
                "approval_queue": str(orch / "approval-queue.json"),
                "provider_capabilities": str(orch / "provider_capabilities.json"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
                "handoffs_path": "handoffs",
            },
            "agents": {
                "claude": {"id": "claude", "name": "Claude", "display_name": "Claude", "provider": "claude"},
                "codex": {"id": "codex", "name": "Codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {
                "claude": {},
                "codex": {},
            },
            "supervisor": {
                "auto_refresh_provider_capabilities": False,
            },
        }
        Path(self.config["paths"]["status_file"]).write_text(json.dumps({"tasks": [], "handoffs": []}), encoding="utf-8")
        Path(self.config["paths"]["approval_queue"]).write_text(json.dumps({"pending": [], "history": []}), encoding="utf-8")
        Path(self.config["paths"]["provider_capabilities"]).write_text("{}", encoding="utf-8")
        Path(self.config["paths"]["event_queue"]).write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bootstrap_supervisor_runtime_state_records_starting_lifecycle(self) -> None:
        state = supervisor.bootstrap_supervisor_runtime_state(self.config, lifecycle="starting")
        supervisor_state = state["supervisor"]
        self.assertEqual(supervisor_state["pid"], supervisor.os.getpid())
        self.assertEqual(supervisor_state["lifecycle"], "starting")
        self.assertEqual(supervisor_state["focus_mode"], "execution")
        self.assertEqual(supervisor_state["mode_status"], "idle")
        self.assertIn("execution", supervisor_state["mode_occupancy"])

    def test_run_once_reconciles_execution_mode_state_from_running_workers(self) -> None:
        state = runtime_state.default_state()
        state["workers"] = {
            "run-1": {
                "run_id": "run-1",
                "provider": "claude",
                "agent_id": "claude",
                "task_id": "BG-001",
                "status": "running",
                "pid": supervisor.os.getpid(),
                "queue_event_id": None,
                "request_snapshot": {"reason": "owned_finalize_dispatch"},
                "last_event_at": "2026-04-13T07:12:46Z",
            }
        }
        runtime_state.save_runtime_state(self.config, state)

        with mock.patch.object(supervisor, "prune_stale_approvals", return_value=False), \
            mock.patch.object(supervisor, "load_provider_report", return_value={}), \
            mock.patch.object(supervisor, "sync_coordination_files", return_value=False), \
            mock.patch.object(supervisor, "poll_workers", return_value=False), \
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False), \
            mock.patch.object(supervisor, "prune_event_queue", return_value=False), \
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False), \
            mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False), \
            mock.patch.object(supervisor, "process_queue", return_value=False), \
            mock.patch.object(supervisor, "sync_github_bus", return_value=False), \
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None):
            supervisor.run_once(self.config, watch=False, quiet=True, once=True)

        saved = runtime_state.load_runtime_state(self.config)
        supervisor_state = saved["supervisor"]
        self.assertEqual(supervisor_state["focus_mode"], "execution")
        self.assertEqual(supervisor_state["mode_status"], "active")
        self.assertEqual(supervisor_state["mode_occupancy"]["execution"]["running"], 1)
        self.assertEqual(supervisor_state["mode_occupancy"]["execution"]["pending"], 0)
        self.assertIsNotNone(supervisor_state["last_successful_loop_at"])
        self.assertIsNotNone(supervisor_state["last_loop_started_at"])
        self.assertIsNotNone(supervisor_state["last_loop_finished_at"])
        self.assertIsNone(supervisor_state["last_loop_error"])
        self.assertIsInstance(supervisor_state["last_loop_duration_ms"], int)
        self.assertGreaterEqual(supervisor_state["last_loop_duration_ms"], 0)
        self.assertGreaterEqual(supervisor_state["last_successful_loop_at"], supervisor_state["started_at"])


if __name__ == "__main__":
    unittest.main()
