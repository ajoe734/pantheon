#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import tempfile
import unittest
import os
import json
import subprocess
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
                    "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                    "retryDelayMs: 1807388.816191,",
                    "reason: 'QUOTA_EXHAUSTED'",
                    "An unexpected critical error occurred:[object Object]",
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

    def test_detects_claude_auth_failure_from_cli_log(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    '{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":590.5,"error_status":401,"error":"authentication_failed"}',
                    '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
        )

    def test_ignores_transcribed_limit_error_inside_review_notes(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Reviewer note:",
                    'Auto-reassigned ownership from Claude to Copilot after repeated provider failure: {"type":"result","result":"You\'ve hit your limit · resets 12am (Asia/Taipei)","worker_run_id":"claude-123"}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_search_result_json_field_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '718:      "next": "Auto-reassigned ownership from Copilot to Codex after repeated Copilot capacity/429: 402 You have no quota",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_classifies_gemini_capacity_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 429 RESOURCE_EXHAUSTED")

        self.assertEqual(result["kind"], "capacity_retryable")
        self.assertTrue(result["transient"])

    def test_classifies_gemini_terminal_quota_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_copilot_no_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "copilot"}

        result = supervisor.classify_worker_failure(config, worker, "402 You have no quota")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_claude_credit_balance_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude"}

        result = supervisor.classify_worker_failure(config, worker, "Credit balance is too low")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_qwen_free_tier_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "qwen"}

        result = supervisor.classify_worker_failure(config, worker, "[API Error: Qwen OAuth free tier quota exceeded.]")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_qwen_oauth_discontinued_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "qwen"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Qwen OAuth free tier was discontinued on 2026-04-15; switch to providers.qwen.qwen.auth_type=openai with OPENAI-compatible credentials.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_gemini_auth_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 401 unauthorized")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_auth_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("auth"))

    def test_classifies_gemini_unknown_critical_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "An unexpected critical error occurred:[object Object]")

        self.assertEqual(result["kind"], "unknown_critical")
        self.assertFalse(result["transient"])

    def test_formats_runtime_timestamp_in_taipei_time(self) -> None:
        self.assertEqual(
            supervisor.format_runtime_timestamp_local("2026-04-06T14:35:42Z"),
            "2026-04-06 22:35:42",
        )

    @mock.patch("supervisor.os.kill")
    @mock.patch("supervisor.os.waitpid", return_value=(43210, 0))
    def test_pid_is_alive_treats_reaped_child_as_dead(self, _waitpid: mock.Mock, _kill: mock.Mock) -> None:
        self.assertFalse(supervisor.pid_is_alive(43210))

    def test_expire_provider_dispatch_pauses_removes_expired_entry(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "copilot": {
                        "provider": "copilot",
                        "blocked_until": "2026-04-06T12:00:00Z",
                        "pause_kind": "quota_terminal",
                        "task_id": "PKT-001",
                        "worker_run_id": "copilot-run",
                        "raw_ref": ".orchestrator/evidence/copilot.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.expire_provider_dispatch_pauses(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")


class ProcessQueueDispatchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
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

    def test_build_request_uses_provider_model_preference_for_qwen_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "qwen": {
                    "id": "qwen",
                    "display_name": "Qwen",
                    "provider": "qwen",
                    "adapter": "qwen",
                }
            },
            "providers": {
                "qwen": {
                    "delivery_mode": "qwen",
                    "model_preference": {
                        "qwen": "qwen3-coder-plus",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "qwen",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "qwen")
        self.assertEqual(request.provider, "qwen")
        self.assertEqual(request.metadata["model_preference"], "qwen3-coder-plus")

    def test_build_request_skips_default_model_for_primary_copilot_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "copilot": {
                    "id": "copilot",
                    "display_name": "Copilot",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "copilot",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "copilot")
        self.assertEqual(request.provider, "copilot")
        self.assertNotIn("model_preference", request.metadata)

    def test_build_request_keeps_agent_specific_model_for_copilot_alias(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "grok": {
                    "id": "grok",
                    "display_name": "Copilot (legacy alias)",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "grok",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "grok")
        self.assertEqual(request.provider, "copilot")
        self.assertEqual(request.metadata["model_preference"], "grok-code-fast-1")

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
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "run-123")
        build_request.assert_called_once_with(self.config, queue_payload)
        start_worker.assert_called_once()
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)

    def test_failed_auto_lane_dispatch_does_not_create_manual_pending_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-failed-auto",
            "task_id": "BUS-VAL-005",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-005",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "CLI auth unavailable", None)),
            mock.patch.object(supervisor, "classify_worker_failure", return_value={"kind": "auth", "label": "authentication"}),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "CLI auth unavailable", "kind": "auth"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True) as mark_provider_dispatch_paused,
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-failed-auto"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "CLI auth unavailable")
        self.assertEqual(state["workers"], {})
        mark_provider_dispatch_paused.assert_called_once()

    def test_retryable_capacity_start_failure_schedules_queue_retry(self) -> None:
        current_task = {
            "id": "BUS-VAL-006",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-retryable-capacity",
            "task_id": "BUS-VAL-006",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-006",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "status: 429 RESOURCE_EXHAUSTED", None)),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "capacity_retryable", "label": "capacity/429", "transient": True},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "Rate limited", "kind": "capacity_retryable"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as maybe_reassign_task_after_worker_failure,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-retryable-capacity"]
        self.assertEqual(record["status"], "retry_backoff")
        self.assertEqual(record["error"], "Rate limited")
        self.assertEqual(record["retry_count"], 1)
        self.assertIsNotNone(record["next_retry_at"])
        maybe_reassign_task_after_worker_failure.assert_not_called()
        self.assertEqual(state["workers"], {})

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

    def test_dispatcher_queues_owner_finalize_after_review_approved(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        dependency = {
            "id": "REG-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T14:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

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
        self.assertEqual(queued_event["reason"], "owned_finalize_dispatch")

    def test_dispatcher_waits_for_done_not_review_approved_dependencies(self) -> None:
        current_task = {
            "id": "FB-003",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": ["REG-002"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        dependency = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T14:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-003", queued_task_ids)

    def test_dispatcher_accepts_archived_done_dependency(self) -> None:
        current_task = {
            "id": "FB-004",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-100"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-100":
                    return "done"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return task_id == "REG-100"

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertIn("FB-004", queued_task_ids)

    def test_dispatcher_rejects_archived_superseded_dependency(self) -> None:
        current_task = {
            "id": "FB-005",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-200"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-200":
                    return "superseded"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return False

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertFalse(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-005", queued_task_ids)

    def test_discussion_planning_materialization_treats_archived_task_as_already_materialized(self) -> None:
        planning_state = {
            "status": "accepted",
            "human_gate_status": "approved",
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "proposed_execution_tasks": [{"id": "LOOP-001"}],
        }

        class FakeResolver:
            def __init__(self, _task_lookup):
                pass

            def snapshot(self, task_id):
                if task_id == "LOOP-001":
                    return {"task_id": "LOOP-001"}
                return None

        with (
            mock.patch.object(supervisor, "load_json", return_value={"tasks": []}),
            mock.patch.object(supervisor, "config_path", return_value=Path("/tmp/ai-status.json")),
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
        ):
            needs_materialization = supervisor.discussion_planning_needs_materialization(self.config, planning_state)

        self.assertFalse(needs_materialization)

    def test_dispatcher_helper_claims_ready_todo_when_owner_is_busy_with_finalize(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Copilot")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_does_not_helper_claim_when_owner_is_not_busy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Copilot")

    def test_dispatcher_helper_claims_in_progress_when_owner_lane_is_paused(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Copilot", "Codex", "Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "summary": "Capacity / rate limit failure",
                    }
                }
            },
        }
        status = {
            "tasks": [
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-006")
        self.assertEqual(kwargs["new_owner"], "Copilot")
        self.assertEqual(kwargs["new_reviewer"], "Qwen")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Copilot")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_does_not_helper_claim_in_progress_when_owner_lane_is_healthy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Copilot", "Codex", "Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_reassigns_mainline_qwen_owner_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-011")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-011")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_reassigns_mainline_qwen_reviewer_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Qwen", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-012")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-012")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "review_ready_dispatch")

    def test_dispatcher_still_allows_qwen_sidecar_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "WB-013-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Qwen",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "WB-013",
                    "helper_kind": "review_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-013-SIDECAR-REVIEW")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

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
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "gemini-run-1")
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)


class DispatchStatusSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "ai_status.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.status_path = self.root / "ai-status.json"
        self.status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "APP-002-W1-FRONT-HANDOFF",
                            "status": "todo",
                            "owner": "Copilot",
                            "reviewer": "Codex",
                            "depends_on": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.status_path),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
        }

    def test_sync_dispatched_task_status_starts_owned_todo_task(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertTrue(changed)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[2], "start")
        self.assertEqual(command[3], "APP-002-W1-FRONT-HANDOFF")
        self.assertIn("Supervisor auto-started", command[4])
        self.assertEqual(run_mock.call_args.kwargs["env"]["AI_NAME"], "Copilot")

    def test_sync_dispatched_task_status_skips_review_dispatch(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "review_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run") as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertFalse(changed)
        run_mock.assert_not_called()


class RunOnceSupervisorStateTests(unittest.TestCase):
    def test_discussion_planning_needs_materialization_for_accepted_approved_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            class FakeResolver:
                def __init__(self, _task_lookup):
                    pass

                def snapshot(self, _task_id):
                    return None

            with mock.patch.object(supervisor, "TaskResolver", FakeResolver):
                self.assertTrue(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_current_session_tasks_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "LOOP-001",
                                "source_plane": "planning",
                                "source_ref": {"session_id": "phase3-session"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_session_already_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "materialized_at": "2026-04-19T03:40:25Z",
                "session_id": "phase7-session",
                "proposed_execution_tasks": [{"id": "OSS-004A"}],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_heartbeat_lag_seconds_reports_gap(self) -> None:
        lag = supervisor.heartbeat_lag_seconds(
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:00:12Z",
        )

        self.assertEqual(lag, 12.0)

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
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None),
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

    def test_run_once_prioritizes_discussion_planning_dispatch(self) -> None:
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

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "sync_coordination_files", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value={"status": "active", "planning_mode": "discussion_planning", "readouts": {}}),
            mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True) as dispatch_discussion_planning,
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False) as dispatch_ready_tasks,
            mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False) as dispatch_underutilization_sidecars,
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state"),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        dispatch_discussion_planning.assert_called_once()
        dispatch_ready_tasks.assert_not_called()
        dispatch_underutilization_sidecars.assert_not_called()

    def test_run_once_auto_materializes_accepted_session_before_execution_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            script_dir = root / "scripts"
            script_dir.mkdir(parents=True, exist_ok=True)
            (script_dir / "planning_state.py").write_text("# test stub\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(status_file),
                    "activity_log": str(root / "activity-log.jsonl"),
                },
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
            planning_state = {
                "status": "accepted",
                "planning_mode": "discussion_planning",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
                stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
                stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
                stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=planning_state))
                dispatch_discussion_planning = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=False)
                )
                dispatch_ready_tasks = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True)
                )
                stack.enter_context(mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
                stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
                stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
                stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
                stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
                stack.enter_context(
                    mock.patch.object(
                        supervisor,
                        "TaskResolver",
                        type(
                            "FakeResolver",
                            (),
                            {
                                "__init__": lambda self, _task_lookup: None,
                                "snapshot": lambda self, _task_id: None,
                            },
                        ),
                    )
                )
                run_mock = stack.enter_context(
                    mock.patch.object(
                        supervisor.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            args=["python3", str(script_dir / "planning_state.py"), "materialize"],
                            returncode=0,
                            stdout="materialized",
                            stderr="",
                        ),
                    )
                )
                changed = supervisor.run_once(config, watch=False, replay=False)

            self.assertTrue(changed)
            dispatch_discussion_planning.assert_not_called()
            dispatch_ready_tasks.assert_called_once()
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0][-1], "materialize")


class SupervisorRuntimeFocusTests(unittest.TestCase):
    def test_discussion_planning_focus_overrides_execution_draining(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "event-queue.jsonl").write_text("", encoding="utf-8")
            config = {
                "paths": {
                    "event_queue": str(root / "event-queue.jsonl"),
                    "status_file": str(root / "ai-status.json"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "ready_dispatcher": {},
            }
            state = {
                "queue": {"events": {}},
                "workers": {
                    "exec-worker": {
                        "status": "manual_pending",
                        "reason": "owned_dispatch",
                    },
                    "planning-worker": {
                        "status": "started",
                        "reason": "discussion_planning_baton_dispatch",
                        "request_snapshot": {
                            "reason": "discussion_planning_baton_dispatch",
                            "metadata": {
                                "planning": {
                                    "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
                                    "mode": "discussion_planning",
                                }
                            },
                        },
                    },
                },
                "supervisor": {
                    "pid": 61209,
                    "focus_mode": "execution",
                    "mode_status": "active",
                },
            }
            planning_state = {
                "status": "active",
                "planning_mode": "discussion_planning",
                "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
            }

            supervisor.stamp_supervisor_runtime_state(
                config,
                state,
                planning_state=planning_state,
                heartbeat_at="2026-04-18T14:40:00Z",
                lifecycle="running",
            )

            supervisor_state = state["supervisor"]
            self.assertEqual(supervisor_state["focus_mode"], "planning")
            self.assertEqual(supervisor_state["mode_status"], "active")
            self.assertIsNone(supervisor_state["mode_switch_requested"])
            self.assertEqual(supervisor_state["last_mode_switch_at"], "2026-04-18T14:40:00Z")
            self.assertEqual(supervisor_state["mode_occupancy"]["planning"]["running"], 1)
            self.assertEqual(supervisor_state["mode_occupancy"]["execution"]["pending"], 1)


class DiscussionPlanningDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
            },
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "gemini": {"delivery_mode": "gemini"},
                "codex": {"delivery_mode": "codex"},
                "copilot": {"delivery_mode": "copilot_local"},
                "qwen": {"delivery_mode": "qwen"},
            },
        }

    def test_dispatch_discussion_planning_queues_pending_readouts(self) -> None:
        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Plan the Pantheon backend completion wave.",
            "baton_owner": "Codex",
            "next_reviewer": "Qwen",
            "current_round": 0,
            "consensus_status": "draft",
            "readouts": {
                "Claude": {"status": "pending"},
                "Codex": {"status": "pending"},
                "Gemini": {"status": "pending"},
                "Qwen": {"status": "pending"},
                "Copilot": {"status": "pending"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 5)
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertEqual(codex_event["reason"], "discussion_planning_baton_dispatch")
        self.assertIn("starter-draft.md", "\n".join(codex_event["target_files"]))
        claude_event = next(row for row in rows if row["target_display_name"] == "Claude")
        self.assertIn("consensus-packet.md", "\n".join(claude_event["target_files"]))

    def test_dispatch_discussion_planning_uses_active_session_paths_and_owned_outputs(self) -> None:
        planning_dir = "docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop"
        planning_state = {
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "planning_dir": planning_dir,
            "session_file": f"{planning_dir}/planning-session.json",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Formalize the Pantheon Console closed loop.",
            "objective": "Define the canonical closed-loop coordination protocol and execution backlog for all 8 workbenches.",
            "baton_owner": "Codex",
            "next_reviewer": "Qwen",
            "current_round": 0,
            "consensus_status": "draft",
            "brief_files": [
                "Pantheon_總索引版系統分析文件.md",
                ".coordination/README.md",
            ],
            "artifacts": {
                "planning_readme": {"path": f"{planning_dir}/README.md"},
                "starter_draft": {"path": f"{planning_dir}/starter-draft.md"},
                "consensus_packet": {"path": f"{planning_dir}/consensus-packet.md"},
            },
            "expected_outputs": [
                {
                    "id": "coordination_loop_spec",
                    "path": f"{planning_dir}/coordination-loop-spec.md",
                    "owner": "Codex",
                }
            ],
            "readouts": {
                "Claude": {"status": "pending", "path": f"{planning_dir}/claude-readout.md"},
                "Codex": {"status": "pending", "path": f"{planning_dir}/codex-readout.md"},
                "Gemini": {"status": "pending", "path": f"{planning_dir}/gemini-readout.md"},
                "Qwen": {"status": "pending", "path": f"{planning_dir}/qwen-readout.md"},
                "Copilot": {"status": "pending", "path": f"{planning_dir}/copilot-readout.md"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertIn(f"{planning_dir}/README.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/planning-session.json", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/codex-readout.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/coordination-loop-spec.md", codex_event["target_files"])
        self.assertIn("本輪目標：Define the canonical closed-loop coordination protocol", codex_event["message"])

    def test_planning_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "phase1-2026-04-11-backend-completion",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "discussion_planning_baton_dispatch",
                "metadata": {
                    "planning": {
                        "session_id": "phase1-2026-04-11-backend-completion",
                        "mode": "discussion_planning",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_coordination_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "F-042",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "coordination:ui-done",
                "metadata": {
                    "coordination": {
                        "feature_id": "F-042",
                        "worker_kind": "front-sync-worker",
                        "payload_type": "ui-done",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_detect_worker_failure_ignores_code_snippet_error_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "worker.log"
            log_path.write_text(
                "\n".join(
                    [
                        "class CommandStatusResponse(BaseModel):",
                        "    result: Optional[Dict[str, Any]] = None",
                        "    error: Optional[Dict[str, Any]] = None",
                        "    audit: Optional[Dict[str, Any]] = None",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            worker = {"log_path": str(log_path)}
            self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_dead_coordination_worker_is_completed_without_taskboard_entry(self) -> None:
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
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "F-042",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:ui-done",
                        "metadata": {
                            "coordination": {
                                "feature_id": "F-042",
                                "worker_kind": "front-sync-worker",
                                "payload_type": "ui-done",
                            }
                        },
                    },
                }
            },
        }
        status = {"tasks": []}

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
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")


class OrphanedQueueEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                    "retry_backoff",
                    "stalled",
                ],
                "orphaned_queue_event_grace_seconds": 300,
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }

    def _write_event(self, payload: dict[str, object]) -> None:
        (self.root / "event-queue.jsonl").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_outstanding_delivery_indexes_ignore_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        agents, task_agents, event_keys = supervisor.outstanding_delivery_indexes(self.config, state)

        self.assertEqual(agents, set())
        self.assertEqual(task_agents, set())
        self.assertEqual(event_keys, set())

    def test_process_queue_skips_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "start_worker_for_request") as start_worker:
            changed = supervisor.process_queue(self.config, state, provider_report={})

        self.assertFalse(changed)
        start_worker.assert_not_called()
        self.assertEqual(state["queue"]["events"], {})

    def test_prune_event_queue_drops_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.prune_event_queue(self.config, state)

        self.assertTrue(changed)
        self.assertEqual((self.root / "event-queue.jsonl").read_text(encoding="utf-8"), "")
        self.assertEqual(state["queue"]["events"], {})
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "queue_event_pruned")


class UnderutilizationSidecarDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "sidecar_catalog.json").write_text('{"templates": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "sidecar_catalog": str(self.root / "sidecar_catalog.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
                "dependency_done_statuses": ["done"],
            },
            "underutilization_dispatch": {
                "enabled": True,
                "threshold_ratio": 0.5,
                "continuous_window_seconds": 900,
                "cooldown_seconds": 900,
                "max_new_sidecars_per_wave": 2,
                "max_active_sidecars_per_agent": 1,
                "productive_worker_statuses": ["running", "waiting_approval", "suspended_approval", "retry_backoff"],
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
            },
        }

    def test_waits_full_window_before_creating_sidecars(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "underutilization": {}}

        with (
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("should not create before the window")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertIsNotNone(state["underutilization"]["below_threshold_since"])
        self.assertIsNone(state["underutilization"].get("last_sidecar_wave_at"))
        write_activity_log.assert_not_called()

    def test_creates_visible_sidecar_after_continuous_low_utilization_window(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "TEL-001",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                }
            },
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }
        parent_task = {
            "id": "APP-001",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "title": "Define BFF query surfaces",
            "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
            "artifacts": ["services/control-plane/bff/"],
            "last_update": "2026-04-10T00:05:00Z",
        }
        created_sidecar = {
            "id": "APP-001-SIDECAR-BFF-HANDOFF",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Qwen",
            "reviewer": "Claude",
            "depends_on": [],
            "title": "Prepare APP-001 BFF and frontend handoff packet",
            "summary_zh": "平行支援 APP-001，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。",
            "artifacts": ["support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md"],
            "task_class": "sidecar",
            "auto_generated": True,
            "helper_parent": "APP-001",
            "helper_kind": "bff_handoff_packet",
            "mutates_canonical": False,
            "auto_created_by": "supervisor-underutilization",
            "last_update": "2026-04-10T00:16:05Z",
        }
        status_before = {"tasks": [parent_task]}
        status_after = {"tasks": [parent_task, created_sidecar]}

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[status_before, status_after]),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        create_sidecar_task.assert_called_once()
        kwargs = create_sidecar_task.call_args.kwargs
        self.assertEqual(kwargs["sidecar_id"], "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["owner"], "Qwen")
        self.assertEqual(kwargs["reviewer"], "Claude")
        self.assertEqual(kwargs["helper_parent"], "APP-001")
        self.assertEqual(kwargs["helper_kind"], "bff_handoff_packet")
        self.assertFalse(kwargs["mutates_canonical"])
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["task"]["task_class"], "sidecar")
        self.assertEqual(state["underutilization"]["last_sidecar_wave_at"], "2026-04-10T00:16:05Z")
        self.assertIn("created 1 visible sidecar", state["underutilization"]["last_sidecar_wave_reason"])
        self.assertIn("APP-001-SIDECAR-BFF-HANDOFF", state.get("tasks", {}))
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("sidecar_task_created", activity_types)
        self.assertIn("sidecar_wave_started", activity_types)

    def test_resets_underutilization_timer_when_utilization_recovers(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {"run_id": "run-1", "task_id": "REG-004", "agent_id": "codex", "provider": "codex", "status": "running"},
                "run-2": {"run_id": "run-2", "task_id": "OSS-001", "agent_id": "gemini", "provider": "gemini", "status": "running"},
            },
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }

        changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertIsNone(state["underutilization"]["below_threshold_since"])

    def test_cooldown_prevents_duplicate_sidecar_wave(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": "2026-04-10T00:10:00Z",
                "last_sidecar_wave_reason": "already created a wave recently",
            },
        }

        with (
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("cooldown should prevent new sidecars")),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:20:00Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertFalse(changed)
        self.assertEqual(state["underutilization"]["last_sidecar_wave_reason"], "already created a wave recently")

    def test_skips_duplicate_signature_when_matching_sidecar_already_exists(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }
        parent_task = {
            "id": "APP-001",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "title": "Define BFF query surfaces",
            "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
            "artifacts": ["services/control-plane/bff/"],
            "last_update": "2026-04-10T00:05:00Z",
        }
        existing_sidecar = {
            "id": "APP-001-SIDECAR-BFF-HANDOFF",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "done",
            "owner": "Qwen",
            "reviewer": "Claude",
            "depends_on": [],
            "title": "Prepare APP-001 BFF and frontend handoff packet",
            "summary_zh": "已完成支援包。",
            "artifacts": ["support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md"],
            "task_class": "sidecar",
            "auto_generated": True,
            "helper_parent": "APP-001",
            "helper_kind": "bff_handoff_packet",
            "mutates_canonical": False,
            "auto_created_by": "supervisor-underutilization",
            "last_update": "2026-04-10T00:07:00Z",
        }
        status = {"tasks": [parent_task, existing_sidecar]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("duplicate signature should not create another sidecar")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "underutilized but no sidecar candidates matched the catalog or dynamic fallback",
        )
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertEqual(activity_types, ["sidecar_wave_skipped"])


class PollWorkersRecoveryTests(unittest.TestCase):
    def test_lower_priority_worker_is_superseded_when_finalize_backlog_exists(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "EX-001", "status": "review_approved", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "superseded")
        self.assertIn("prioritize higher-priority review/finalize work", worker["last_error"])
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(12345)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_parent_worker_is_not_superseded_for_its_sidecar_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "BP5-SVC-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2099-04-15T15:29:37Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                    "depends_on": [],
                },
                {
                    "id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "auto_generated": True,
                    "helper_parent": "BP5-SVC-001",
                    "helper_kind": "acceptance_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertIsInstance(changed, bool)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        terminate_worker_pid.assert_not_called()
        write_activity_log.assert_not_called()

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
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
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
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
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

    def test_dead_claude_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-123",
                    "resume_token": "sess-123",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-004", "status": "in_progress", "owner": "Claude", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "tool_name": "ToolSearch",
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
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-1")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_claude2_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {"claude2": {"delivery_mode": "claude_cli"}},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-456",
                    "resume_token": "sess-456",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-005", "status": "in_progress", "owner": "Claude2", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-2",
                    "worker_run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "tool_name": "ToolSearch",
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
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-2")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_stale_worker_is_reaped_when_task_assignment_moved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "name": "Codex"},
                "claude": {"id": "claude", "name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "manual_pending",
                    "queue_event_id": "evt-1",
                    "pid": None,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "review", "owner": "Grok", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_stalled_worker_returns_to_running_after_new_log_activity(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-002",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:20:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-002", "status": "in_progress", "owner": "Codex", "reviewer": "Copilot"}]}

        def bump_log_activity(_config, worker):
            worker["last_event_at"] = "2026-04-06T14:31:28Z"

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=bump_log_activity),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "running")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_recovered")

    def test_stalled_worker_is_terminated_after_extended_stall(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["todo", "in_progress"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "failed")
        terminate_worker_pid.assert_called_once_with(1234)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_alive_worker_is_superseded_after_reassignment(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "REG-002",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 2222,
                    "last_event_at": "2026-04-06T14:19:47Z",
                }
            },
        }
        status = {"tasks": [{"id": "REG-002", "status": "review", "owner": "Codex", "reviewer": "Gemini"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(2222)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")


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

    def test_reassign_review_skips_paused_reviewer_candidates(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Qwen", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "qwen": {"display_name": "Qwen", "provider": "qwen"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2099-01-01T00:00:00Z",
                    }
                }
            }
        }
        worker = {
            "task_id": "P3-002",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-002",
                    "status": "review",
                    "owner": "Codex",
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
                config,
                state,
                worker,
                "status: 401 unauthorized",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Copilot")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Copilot")

    def test_reassign_review_can_fall_back_to_codex2_when_codex_is_owner(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Codex2", "Qwen", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "qwen": {"display_name": "Qwen", "provider": "qwen"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "codex2": {"display_name": "Codex2", "provider": "codex2"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        worker = {
            "task_id": "P3-003",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-3",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-003",
                    "status": "review",
                    "owner": "Codex",
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
                config,
                worker,
                "Credit balance is too low",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Codex2")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Codex2")

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
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertIn("Task returned to todo until Codex starts a fresh run.", kwargs["message"])


class WorkerPreemptionSyncTests(unittest.TestCase):
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

    def test_sync_preempted_owned_task_returns_in_progress_task_to_todo(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-CICD-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-CICD-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("returned to todo until a fresh run restarts it", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_sync_preempted_finalize_task_keeps_review_approved(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-SVC-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_finalize_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Qwen",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("task remains review_approved", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_reassigns_finalize_task_to_new_owner_after_repeated_failure(self) -> None:
        config = {
            **self.config,
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                **self.config["worker_reassignment"],
                "owner_fallbacks": {
                    **self.config["worker_reassignment"]["owner_fallbacks"],
                    "Claude": ["Qwen", "Grok", "Gemini"],
                },
                "reviewer_fallbacks": {
                    **self.config["worker_reassignment"]["reviewer_fallbacks"],
                    "Claude": ["Qwen", "Grok", "Gemini"],
                },
            },
            "agents": {
                **self.config["agents"],
                "qwen": {"display_name": "Qwen"},
            },
        }
        worker = {
            "task_id": "RUN-001",
            "agent_id": "claude",
            "retry_count": 5,
            "run_id": "claude-run-9",
        }
        status = {
            "tasks": [
                {
                    "id": "RUN-001",
                    "status": "review_approved",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "You've hit your limit · resets 1pm (Asia/Taipei)",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Grok")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "RUN-001")
        self.assertEqual(kwargs["new_owner"], "Grok")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertIsNone(kwargs["new_status"])


if __name__ == "__main__":
    unittest.main()
