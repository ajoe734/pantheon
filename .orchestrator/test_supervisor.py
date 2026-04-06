#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

import supervisor


class DetectWorkerFailureTests(unittest.TestCase):
    def _worker_for_log(self, content: str) -> dict[str, str]:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write(content)
        handle.flush()
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return {"log_path": handle.name}

    def test_ignores_error_markers_inside_captured_log_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "codex",
                    "I am reading ai-activity-log.jsonl for context.",
                    '262-{"ts": "2026-04-05T13:36:01Z", "message": "Error: Model \\"grok-code-fast-1\\" from --model flag is not available."}',
                    'worker_retry_scheduled: {"message": "Transient worker failure detected; retry 1 scheduled at 2026-04-05T13:48:48Z: reason: \\"QUOTA_EXHAUSTED\\""}',
                    "No local failure happened in this session.",
                ]
            )
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_real_model_availability_failure(self) -> None:
        worker = self._worker_for_log('Error: Model "grok-code-fast-1" from --model flag is not available.\n')

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            'Error: Model "grok-code-fast-1" from --model flag is not available.',
        )

    def test_detects_real_gemini_quota_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "retryDelayMs: 1807388.816191,",
                    "reason: 'QUOTA_EXHAUSTED'",
                    "An unexpected critical error occurred:[object Object]",
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "An unexpected critical error occurred:[object Object]",
        )


class ProcessQueueDispatchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex": {
                    "id": "codex",
                    "name": "Codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                }
            },
            "providers": {
                "codex": {
                    "delivery_mode": "codex",
                }
            },
        }
        self.provider_report: dict[str, object] = {}

    def test_skips_stale_owned_dispatch_event_after_task_completion(self) -> None:
        queued_task = {
            "id": "BUS-VAL-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T11:45:16Z",
        }
        queued_event = supervisor.build_dispatch_event(
            queued_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-001": queued_task},
        )
        queue_payload = {
            "event_id": "evt-stale",
            "event_key": queued_event["key"],
            "task_id": "BUS-VAL-001",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        current_status = {
            "tasks": [
                {
                    **queued_task,
                    "status": "done",
                    "last_update": "2026-04-05T12:00:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value=current_status),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("stale event should not start a worker")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-stale"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "stale_dispatch_event")
        self.assertIn("processed_at", record)
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_starts_current_owned_dispatch_event(self) -> None:
        current_task = {
            "id": "BUS-VAL-004",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T14:54:01Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-004": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-004",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = object()
        delivery = {"manual_confirmation_required": False, "auto_delivered": True}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request) as build_request,
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", delivery)) as start_worker,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "run-123")
        build_request.assert_called_once_with(self.config, queue_payload)
        start_worker.assert_called_once()

    def test_dispatcher_can_requeue_same_task_after_previous_failure(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "last_update": "2026-04-06T09:00:00Z",
            "artifacts": ["services/registry/promotion/"],
            "next": "continue",
        }
        state = {
            "queue": {
                "events": {
                    "evt-old": {
                        "status": "failed",
                        "run_id": "old-run",
                    }
                }
            },
            "workers": {
                "old-run": {
                    "run_id": "old-run",
                    "queue_event_id": "evt-old",
                    "task_id": "REG-002",
                    "agent_id": "codex",
                    "status": "failed",
                }
            },
            "seen_event_keys": {"dispatcher:Codex:REG-002:owned_in_progress_dispatch:stale-signature": "2026-04-06T08:59:00Z"},
        }
        status = {"tasks": [current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_skips_duplicate_start_when_active_worker_already_exists(self) -> None:
        current_task = {
            "id": "P3-001",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T05:30:43Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Gemini",
            "review_ready_dispatch",
            {"P3-001": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "P3-001",
            "target_agent": "gemini",
            "target_display_name": "Gemini",
            "reason": "review_ready_dispatch",
            "message": "wake",
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "gemini-run-1": {
                    "run_id": "gemini-run-1",
                    "queue_event_id": "evt-current",
                    "status": "running",
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("duplicate queue event should not start another worker")),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "gemini-run-1")


class RunOnceSupervisorStateTests(unittest.TestCase):
    def test_run_once_re_stamps_current_pid_after_watch_reload(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }
        saved_state: dict[str, object] = {}

        def capture_save(_config: dict[str, object], state: dict[str, object]) -> None:
            saved_state.clear()
            saved_state.update(state)

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False),
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state", side_effect=capture_save),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        self.assertEqual(saved_state["supervisor"]["pid"], os.getpid())
        self.assertIsNotNone(saved_state["supervisor"]["last_heartbeat_at"])
        self.assertEqual(saved_state["supervisor"]["started_at"], saved_state["supervisor"]["last_heartbeat_at"])


class PollWorkersRecoveryTests(unittest.TestCase):
    def test_dead_worker_for_open_task_is_marked_failed_not_completed(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "in_progress", "owner": "Codex", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "Worker exited before the task reached a terminal status.")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_waiting_approval_worker_is_failed_and_approval_is_resolved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "OC-002", "status": "review", "owner": "Codex", "reviewer": "Claude"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "tool_name": "Bash",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "Worker exited while waiting for approval.")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        resolve_approval.assert_called_once_with(
            config,
            "apr-1",
            decision="deny",
            note="Auto-denied because the worker exited before approval could be applied.",
            remember=False,
        )
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")


class SingleSupervisorGuardTests(unittest.TestCase):
    def test_terminate_older_supervisors_kills_only_older_matching_processes(self) -> None:
        config = {"activity_log": "/tmp/fake-log.jsonl"}
        killed: list[tuple[int, int]] = []
        alive = {101: True, 202: True, 404: True}

        def fake_kill(pid: int, sig: int) -> None:
            killed.append((pid, sig))
            if sig in {supervisor.signal.SIGTERM, supervisor.signal.SIGKILL}:
                alive[pid] = False

        with (
            mock.patch.object(supervisor, "iter_matching_supervisor_pids", return_value=[101, 202, 404]),
            mock.patch.object(supervisor, "pid_is_alive", side_effect=lambda pid: alive.get(pid, False)),
            mock.patch.object(supervisor.os, "getpid", return_value=202),
            mock.patch.object(supervisor.os, "kill", side_effect=fake_kill),
            mock.patch.object(supervisor.time, "sleep"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            supervisor.terminate_older_supervisors(config)

        self.assertEqual(killed, [(101, supervisor.signal.SIGTERM)])
        write_activity_log.assert_called_once()
        payload = write_activity_log.call_args.args[1]
        self.assertEqual(payload["type"], "supervisor_replaced")
        self.assertEqual(payload["old_pid"], 101)
        self.assertEqual(payload["new_pid"], 202)


class WorkerReassignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_reassigns_review_task_to_new_reviewer_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "P3-001",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-001",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "P3-001")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_reassigned")

    def test_reassigns_owned_task_to_new_owner_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "LP-003",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "LP-003",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "LP-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")


if __name__ == "__main__":
    unittest.main()
